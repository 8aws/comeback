import asyncio
import hashlib
import json
import tarfile
from pathlib import Path

import aiofiles
import humanize
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import settings
from ..job_manager import job_manager
from ..models import BackupRequest, BackupSummary, JobType
from ..backup.manager import run_backup

router = APIRouter(prefix="/api/backups", tags=["backups"])


def _read_manifest_from_archive(archive_path: Path) -> dict:
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            m = next((x for x in tar.getmembers() if x.name.endswith("manifest.json")), None)
            if m:
                return json.loads(tar.extractfile(m).read().decode())
    except Exception:
        pass
    return {}


@router.get("")
def list_backups() -> list[dict]:
    backup_dir = settings.backup_dir
    if not backup_dir.exists():
        return []

    result = []
    for archive in sorted(backup_dir.glob("backup_*.tar.gz"), reverse=True):
        manifest = _read_manifest_from_archive(archive)
        size = archive.stat().st_size
        result.append({
            "id": manifest.get("id", archive.name.replace(".tar.gz", "")),
            "name": archive.name.replace(".tar.gz", ""),
            "label": manifest.get("label"),
            "created_at": manifest.get("created_at"),
            "source_hostname": manifest.get("source_hostname", "unknown"),
            "size_bytes": size,
            "size_human": humanize.naturalsize(size),
            "container_count": len(manifest.get("containers", [])),
            "volume_count": len(manifest.get("volumes", [])),
            "db_count": len(manifest.get("databases", [])),
            "containers": manifest.get("containers", []),
            "path": archive.name,
        })
    return result


@router.get("/{backup_name}/manifest")
def get_manifest(backup_name: str) -> dict:
    archive = settings.backup_dir / f"{backup_name}.tar.gz"
    if not archive.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    manifest = _read_manifest_from_archive(archive)
    if not manifest:
        raise HTTPException(status_code=500, detail="Could not read manifest")
    return manifest


@router.delete("/{backup_name}")
def delete_backup(backup_name: str):
    archive = settings.backup_dir / f"{backup_name}.tar.gz"
    checksum = settings.backup_dir / f"{backup_name}.sha256"
    if not archive.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    archive.unlink()
    if checksum.exists():
        checksum.unlink()
    return {"deleted": backup_name}


@router.get("/{backup_name}/download")
def download_backup(backup_name: str):
    archive = settings.backup_dir / f"{backup_name}.tar.gz"
    if not archive.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(archive, media_type="application/gzip", filename=archive.name)


@router.post("/upload")
async def upload_backup(file: UploadFile) -> dict:
    """Receive a backup archive from another comeback instance (migrations)."""
    name = Path(file.filename or "").name   # strip any path components
    if not name.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .tar.gz")

    max_bytes = settings.max_upload_bytes
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.backup_dir / name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"Ya existe un backup llamado {name}")

    # Stream to a temp file computing the checksum on the fly
    tmp = dest.with_suffix(".uploading")
    sha = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(tmp, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                sha.update(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=413,
                        detail=f"Archivo demasiado grande (máx {max_bytes // (1024**3)} GB)")
                await out.write(chunk)

        manifest = _read_manifest_from_archive(tmp)
        if not manifest or "containers" not in manifest:
            raise HTTPException(status_code=400,
                                detail="El archivo no es un backup válido de comeback (sin manifest.json)")

        tmp.rename(dest)
        sidecar = settings.backup_dir / f"{name.replace('.tar.gz', '')}.sha256"
        sidecar.write_text(f"{sha.hexdigest()}  {name}\n")
    except HTTPException:
        tmp.unlink(missing_ok=True)
        raise
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Error guardando el archivo: {e}")

    return {
        "name": name.replace(".tar.gz", ""),
        "size_bytes": size,
        "size_human": humanize.naturalsize(size),
        "container_count": len(manifest.get("containers", [])),
        "checksum": sha.hexdigest(),
    }


@router.post("/start")
async def start_backup(req: BackupRequest) -> dict:
    job = job_manager.create(JobType.backup, f"Backup of {len(req.container_ids)} container(s)")
    job._task = asyncio.create_task(run_backup(
        job,
        req.container_ids,
        req.include_images,
        req.label,
    ))
    return {"job_id": job.id}
