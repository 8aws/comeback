from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import scheduler

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class ScheduleCreate(BaseModel):
    name: str
    container_ids: list[str]
    frequency: str = "daily"        # daily | weekly
    time: str = "03:00"             # HH:MM, container TZ
    weekday: int = 0                # 0=lunes … 6=domingo (weekly only)
    retention: int = 7              # keep newest N archives
    include_images: bool = False


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    container_ids: Optional[list[str]] = None
    frequency: Optional[str] = None
    time: Optional[str] = None
    weekday: Optional[int] = None
    retention: Optional[int] = None
    include_images: Optional[bool] = None
    enabled: Optional[bool] = None


@router.get("")
def list_schedules() -> list[dict]:
    return [scheduler.describe(s) for s in scheduler.load_schedules()]


@router.post("")
def create_schedule(body: ScheduleCreate) -> dict:
    if not body.container_ids:
        raise HTTPException(status_code=400, detail="container_ids vacío")
    if body.frequency not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="frequency debe ser daily o weekly")
    try:
        h, m = (int(x) for x in body.time.split(":"))
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        raise HTTPException(status_code=400, detail="time debe ser HH:MM")
    return scheduler.describe(scheduler.create_schedule(body.model_dump()))


@router.put("/{sched_id}")
def update_schedule(sched_id: str, body: ScheduleUpdate) -> dict:
    sched = scheduler.update_schedule(sched_id, body.model_dump(exclude_none=True))
    if not sched:
        raise HTTPException(status_code=404, detail="Programación no encontrada")
    return scheduler.describe(sched)


@router.delete("/{sched_id}")
def delete_schedule(sched_id: str) -> dict:
    if not scheduler.delete_schedule(sched_id):
        raise HTTPException(status_code=404, detail="Programación no encontrada")
    return {"deleted": sched_id}


@router.post("/{sched_id}/run")
async def run_now(sched_id: str) -> dict:
    sched = next((s for s in scheduler.load_schedules() if s["id"] == sched_id), None)
    if not sched:
        raise HTTPException(status_code=404, detail="Programación no encontrada")
    job_id = await scheduler.run_schedule_now(sched)
    return {"job_id": job_id}
