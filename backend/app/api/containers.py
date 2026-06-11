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


@router.get("/{container_id}")
def get_container(container_id: str):
    client = get_docker()
    try:
        c = client.containers.get(container_id)
        return get_container_info(c).model_dump()
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
