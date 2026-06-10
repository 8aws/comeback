import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import settings
from ..job_manager import job_manager
from ..models import RestoreRequest, JobType
from ..restore.manager import run_restore

router = APIRouter(prefix="/api/restore", tags=["restore"])


@router.post("/start")
async def start_restore(req: RestoreRequest) -> dict:
    archive = settings.backup_dir / f"{req.backup_id}.tar.gz"
    if not archive.exists():
        # Try as full name
        archive = settings.backup_dir / req.backup_id
        if not archive.exists():
            raise HTTPException(status_code=404, detail=f"Backup not found: {req.backup_id}")

    prefix = req.name_prefix or ""
    label = f"[{prefix}] " if prefix else ""
    job = job_manager.create(JobType.restore, f"{label}Restore from {req.backup_id}")
    asyncio.create_task(run_restore(
        job,
        archive,
        req.container_names,
        req.overwrite_existing,
        req.start_after_restore,
        name_prefix=prefix,
        path_map=req.path_map,
    ))
    return {"job_id": job.id}


@router.post("/verify")
async def verify_backup_endpoint(req: RestoreRequest) -> dict:
    archive = settings.backup_dir / f"{req.backup_id}.tar.gz"
    if not archive.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    from ..restore.verify import verify_backup
    job = job_manager.create(JobType.verify, f"Verify {req.backup_id}")

    async def _run():
        job.status = "running"
        try:
            manifest = await verify_backup(archive, job)
            from ..models import JobStatus
            await job.finish(JobStatus.success, {"manifest": manifest})
        except Exception as e:
            from ..models import JobStatus, LogLevel
            await job.log(LogLevel.error, str(e))
            await job.finish(JobStatus.failed, {"error": str(e)})

    asyncio.create_task(_run())
    return {"job_id": job.id}
