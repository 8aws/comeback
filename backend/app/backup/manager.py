"""Orchestrates a full backup job."""
import hashlib
import json
import shutil
import socket
import tarfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..config import settings
from ..docker_client import get_docker
from ..job_manager import Job
from ..models import LogLevel, JobStatus
from .containers import export_container_spec, export_networks, detect_db_type
from .volumes import backup_all_volumes
from .databases import dump_database


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def run_backup(job: Job, container_ids: list[str], include_images: bool, label: str | None):
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running

    backup_id = str(uuid4())[:8]
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{ts}_{backup_id}"
    work_dir = settings.backup_dir / backup_name
    work_dir.mkdir(parents=True, exist_ok=True)

    containers_dir = work_dir / "containers"
    volumes_dir = work_dir / "volumes"
    databases_dir = work_dir / "databases"
    images_dir = work_dir / "images"
    networks_dir = work_dir / "networks"

    for d in [containers_dir, volumes_dir, databases_dir, images_dir, networks_dir]:
        d.mkdir(exist_ok=True)

    client = get_docker()
    total = len(container_ids)
    manifest = {
        "id": backup_id,
        "label": label,
        "created_at": datetime.utcnow().isoformat(),
        "comeback_version": "1.0.0",
        "source_hostname": socket.gethostname(),
        "containers": [],
        "volumes": [],
        "databases": [],
        "images": [],
        "networks": [],
    }

    await job.log(LogLevel.info, f"Starting backup of {total} container(s)")
    await job.set_progress(5, "Initializing...")

    # Networks
    await job.log(LogLevel.info, "Saving network configurations")
    try:
        nets = export_networks(networks_dir)
        manifest["networks"] = nets
    except Exception as e:
        await job.log(LogLevel.warning, f"Networks export error: {e}")

    all_volumes: list[dict] = []

    for idx, cid in enumerate(container_ids):
        pct = 10 + int((idx / total) * 70)
        await job.set_progress(pct, f"Processing {cid}...")

        try:
            c = client.containers.get(cid)
        except Exception:
            await job.log(LogLevel.error, f"Container not found: {cid}")
            continue

        container_name = c.name
        await job.log(LogLevel.info, f"[{idx+1}/{total}] Container: {container_name}")

        # Container spec
        try:
            spec = export_container_spec(cid, containers_dir)
            manifest["containers"].append({
                "name": container_name,
                "image": spec["image"],
                "spec_file": f"containers/{container_name.lstrip('/')}.json",
            })
        except Exception as e:
            await job.log(LogLevel.error, f"Spec export failed: {e}")
            continue

        # Volumes
        mounts = spec.get("mounts", [])
        if mounts:
            await job.log(LogLevel.info, f"Found {len(mounts)} mount(s): {[m.get('name') or m.get('source') for m in mounts]}")
        else:
            await job.log(LogLevel.info, "No volumes or bind mounts found for this container")
        vol_results = await backup_all_volumes(spec, volumes_dir, job, settings.host_root)
        all_volumes.extend(vol_results)

        # Databases
        db_type = detect_db_type(spec["image"])
        if db_type and c.status == "running":
            db_result = await dump_database(
                container_name, db_type,
                spec["config"].get("Env") or [],
                databases_dir, job
            )
            if db_result:
                manifest["databases"].append(db_result)

        # Images (optional)
        if include_images:
            image_file = images_dir / f"{container_name}.tar"
            await job.log(LogLevel.info, f"Saving image for {container_name}...")
            try:
                img = client.images.get(spec["image"])
                with open(image_file, "wb") as f:
                    for chunk in img.save():
                        f.write(chunk)
                manifest["images"].append({
                    "container": container_name,
                    "image": spec["image"],
                    "file": f"images/{container_name}.tar",
                })
            except Exception as e:
                await job.log(LogLevel.warning, f"Image save failed: {e}")

    manifest["volumes"] = all_volumes

    # Write manifest
    await job.set_progress(85, "Writing manifest...")
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    # Create final archive
    await job.set_progress(90, "Compressing backup bundle...")
    archive_path = settings.backup_dir / f"{backup_name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(work_dir, arcname=backup_name)

    # Checksum
    checksum = _sha256_file(archive_path)
    checksum_path = settings.backup_dir / f"{backup_name}.sha256"
    checksum_path.write_text(f"{checksum}  {archive_path.name}\n")

    # Update manifest inside archive with checksum (append to json)
    manifest["checksum"] = checksum
    manifest["size_bytes"] = archive_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    # Cleanup working dir
    shutil.rmtree(work_dir)

    size_mb = archive_path.stat().st_size / 1024 / 1024
    await job.set_progress(100, "Done")
    await job.log(LogLevel.success, f"Backup complete: {archive_path.name} ({size_mb:.1f} MB)")

    await job.finish(JobStatus.success, {
        "backup_id": backup_id,
        "backup_name": backup_name,
        "archive": archive_path.name,
        "checksum": checksum,
        "size_bytes": archive_path.stat().st_size,
        "containers": len(manifest["containers"]),
    })
