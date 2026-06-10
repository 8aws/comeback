from fastapi import APIRouter, HTTPException
from ..docker_client import get_docker
from ..backup.containers import get_container_info

router = APIRouter(prefix="/api/containers", tags=["containers"])


@router.get("")
def list_containers():
    client = get_docker()
    containers = client.containers.list(all=True)
    result = []
    for c in containers:
        try:
            info = get_container_info(c)
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
