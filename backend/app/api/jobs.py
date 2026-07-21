from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from ..auth import COOKIE_NAME, validate_session
from ..job_manager import job_manager
from ..models import JobStatus, LogLevel

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs():
    return job_manager.list()


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        **job.to_dict(),
        "logs": [e.model_dump(mode="json") for e in job.logs],
    }


@router.delete("/{job_id}")
async def cancel_job(job_id: str):
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.pending, JobStatus.running):
        raise HTTPException(status_code=409, detail=f"Job already {job.status}")
    await job.log(LogLevel.warning, "Cancelación solicitada por el usuario")
    job.cancel()
    await job.finish(JobStatus.cancelled, {"cancelled_by": "user"})
    return {"cancelled": job_id}


@router.websocket("/{job_id}/ws")
async def job_websocket(websocket: WebSocket, job_id: str):
    if not validate_session(websocket.cookies.get(COOKIE_NAME)):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    job = job_manager.get(job_id)
    if not job:
        await websocket.send_json({"type": "error", "message": "Job not found"})
        await websocket.close()
        return

    # Send existing state immediately
    await websocket.send_json({
        "type": "state",
        "job": job.to_dict(),
        "logs": [e.model_dump(mode="json") for e in job.logs],
    })

    # Job already finished — send final event and close cleanly
    if job.status in (JobStatus.success, JobStatus.failed, JobStatus.cancelled):
        await websocket.send_json({"type": "finished", "status": job.status})
        return

    queue = job.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "finished":
                break
    except WebSocketDisconnect:
        pass
    finally:
        job.unsubscribe(queue)
