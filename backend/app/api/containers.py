import asyncio

import humanize
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..backup.containers import get_container_info
from ..docker_client import get_docker
from ..job_manager import job_manager
from ..models import JobType

router = APIRouter(prefix="/api/containers", tags=["containers"])

QUICK_ACTIONS = {"start", "stop", "restart", "pause", "unpause", "kill"}


class ActionRequest(BaseModel):
    action: str   # start|stop|restart|pause|unpause|kill|recreate|pull


def _container_sizes(client) -> dict[str, int]:
    """SizeRootFs per short id — one listing call, the daemon computes sizes."""
    try:
        return {
            entry["Id"][:12]: entry.get("SizeRootFs") or entry.get("SizeRw") or 0
            for entry in client.api.containers(all=True, size=True)
        }
    except Exception:
        return {}


@router.get("")
def list_containers():
    """Container list WITHOUT sizes — size computation can take many seconds
    on the daemon, so the UI fetches /api/containers/sizes separately."""
    client = get_docker()
    containers = client.containers.list(all=True)
    result = []
    for c in containers:
        try:
            result.append(get_container_info(c).model_dump())
        except Exception as e:
            result.append({"id": c.id[:12], "name": c.name, "error": str(e)})
    return result


@router.get("/sizes")
def container_sizes():
    """Disk usage per container id (slow daemon call, fetched in background)."""
    client = get_docker()
    return {
        cid: {"size_bytes": size, "size_human": humanize.naturalsize(size)}
        for cid, size in _container_sizes(client).items()
    }


@router.post("/{container_id}/action")
async def container_action(container_id: str, req: ActionRequest) -> dict:
    client = get_docker()
    loop = asyncio.get_event_loop()
    try:
        c = await loop.run_in_executor(None, lambda: client.containers.get(container_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Container not found")

    action = req.action
    if action in QUICK_ACTIONS:
        def _do():
            if action == "stop":
                c.stop(timeout=10)
            else:
                getattr(c, action)()
        try:
            await loop.run_in_executor(None, _do)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "action": action, "container": c.name}

    if action == "recreate":
        from ..updates import run_recreate
        job = job_manager.create(JobType.update, f"Recreate: {c.name}")
        asyncio.create_task(run_recreate(job, container_id))
        return {"job_id": job.id}

    if action == "pull":
        from ..updates import run_pull
        job = job_manager.create(JobType.update, f"Pull: {c.name}")
        asyncio.create_task(run_pull(job, container_id))
        return {"job_id": job.id}

    raise HTTPException(status_code=400, detail=f"Acción desconocida: {action}")


@router.get("/{container_id}")
def get_container(container_id: str):
    client = get_docker()
    try:
        c = client.containers.get(container_id)
        return get_container_info(c).model_dump()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
