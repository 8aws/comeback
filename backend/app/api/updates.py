import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..job_manager import job_manager
from ..models import JobType, UpdateRequest
from ..updates import check_container, check_updates, run_update, run_update_all

router = APIRouter(prefix="/api/updates", tags=["updates"])


class BulkUpdateRequest(BaseModel):
    container_ids: list[str]
    backup_first: bool = True


@router.get("")
async def list_updates() -> list[dict]:
    return await check_updates()


@router.get("/check/{container_id}")
async def check_single(container_id: str) -> dict:
    try:
        return await check_container(container_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/start")
async def start_update(req: UpdateRequest) -> dict:
    job = job_manager.create(JobType.update, f"Update: {req.container_id}")
    asyncio.create_task(run_update(job, req.container_id, req.backup_first))
    return {"job_id": job.id}


@router.post("/start-all")
async def start_update_all(req: BulkUpdateRequest) -> dict:
    if not req.container_ids:
        raise HTTPException(status_code=400, detail="container_ids vacío")
    job = job_manager.create(
        JobType.update, f"Update masivo: {len(req.container_ids)} contenedor(es)")
    asyncio.create_task(run_update_all(job, req.container_ids, req.backup_first))
    return {"job_id": job.id}
