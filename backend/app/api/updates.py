import asyncio

from fastapi import APIRouter, HTTPException

from ..job_manager import job_manager
from ..models import JobType, UpdateRequest
from ..updates import check_updates, run_update

router = APIRouter(prefix="/api/updates", tags=["updates"])


@router.get("")
async def list_updates() -> list[dict]:
    return await check_updates()


@router.post("/start")
async def start_update(req: UpdateRequest) -> dict:
    job = job_manager.create(JobType.update, f"Update: {req.container_id}")
    asyncio.create_task(run_update(job, req.container_id, req.backup_first))
    return {"job_id": job.id}
