from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from ..job_manager import job_manager
from ..models import JobStatus

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


@router.websocket("/{job_id}/ws")
async def job_websocket(websocket: WebSocket, job_id: str):
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
