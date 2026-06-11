import humanize
from fastapi import APIRouter, HTTPException
from ..docker_client import get_docker
from ..backup.containers import get_container_info

router = APIRouter(prefix="/api/containers", tags=["containers"])


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
    client = get_docker()
    containers = client.containers.list(all=True)
    sizes = _container_sizes(client)
    result = []
    for c in containers:
        try:
            info = get_container_info(c)
            size = sizes.get(info.id)
            if size is not None:
                info.size_bytes = size
                info.size_human = humanize.naturalsize(size)
            result.append(info.model_dump())
        except Exception as e:
            result.append({"id": c.id[:12], "name": c.name, "error": str(e)})
    return result


@router.get("/{container_id}")
def get_container(container_id: str):
    client = get_docker()
    try:
        c = client.containers.get(container_id)
        return get_container_info(c).model_dump()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
