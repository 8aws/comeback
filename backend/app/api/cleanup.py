from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..docker_client import get_docker

router = APIRouter(prefix="/api/cleanup", tags=["cleanup"])


class CleanupRequest(BaseModel):
    prefix: str


@router.get("/test")
def list_test_resources() -> dict:
    """List all containers and volumes created by comeback in test/prefixed mode."""
    client = get_docker()
    containers = []
    prefixes: set[str] = set()

    for c in client.containers.list(all=True):
        prefix = c.labels.get("com.uverse.comeback.prefix", "")
        if prefix:
            containers.append({
                "id": c.id[:12],
                "name": c.name,
                "original": c.labels.get("com.uverse.comeback.original", ""),
                "prefix": prefix,
                "status": c.status,
                "image": c.image.tags[0] if c.image.tags else c.image.short_id,
            })
            prefixes.add(prefix)

    # Find volumes that match any known prefix
    volumes = []
    for vol in client.volumes.list():
        for prefix in prefixes:
            if vol.name.startswith(prefix):
                volumes.append({
                    "name": vol.name,
                    "prefix": prefix,
                    "original": vol.name[len(prefix):],
                })
                break

    return {"containers": containers, "volumes": volumes, "prefixes": sorted(prefixes)}


@router.delete("/test/{prefix}")
def cleanup_prefix(prefix: str) -> dict:
    """Remove all containers and volumes created with a given prefix."""
    if not prefix:
        raise HTTPException(status_code=400, detail="Prefix required")

    client = get_docker()
    removed_containers = []
    removed_volumes = []
    errors = []

    # Remove containers with this prefix label
    for c in client.containers.list(all=True):
        if c.labels.get("com.uverse.comeback.prefix") == prefix:
            try:
                c.remove(force=True)
                removed_containers.append(c.name)
            except Exception as e:
                errors.append(f"Container {c.name}: {e}")

    # Remove volumes that start with this prefix
    for vol in client.volumes.list():
        if vol.name.startswith(prefix):
            try:
                vol.remove(force=True)
                removed_volumes.append(vol.name)
            except Exception as e:
                errors.append(f"Volume {vol.name}: {e}")

    return {
        "removed_containers": removed_containers,
        "removed_volumes": removed_volumes,
        "errors": errors,
    }
