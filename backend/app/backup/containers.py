"""Extract full container configuration and metadata."""
import json
import socket
from pathlib import Path

import docker

from ..docker_client import get_docker
from ..models import ContainerInfo

DB_IMAGES = {
    "mysql": "mysql",
    "mariadb": "mariadb",
    "postgres": "postgres",
    "mongo": "mongodb",
    "redis": "redis",
    "elasticsearch": "elasticsearch",
    "influxdb": "influxdb",
}


def detect_db_type(image_name: str) -> str | None:
    name = image_name.lower().split(":")[0].split("/")[-1]
    for key, db in DB_IMAGES.items():
        if key in name:
            return db
    return None


def get_container_info(container) -> ContainerInfo:
    client = get_docker()
    c = client.containers.get(container.id)
    attrs = c.attrs

    config = attrs.get("Config", {})
    host_config = attrs.get("HostConfig", {})
    network_settings = attrs.get("NetworkSettings", {})

    image_name = config.get("Image", "")
    db_type = detect_db_type(image_name)

    networks = list(network_settings.get("Networks", {}).keys())

    volumes = []
    for mount in attrs.get("Mounts", []):
        volumes.append({
            "type": mount.get("Type"),
            "source": mount.get("Source"),
            "destination": mount.get("Destination"),
            "mode": mount.get("Mode", "rw"),
            "name": mount.get("Name"),
            "driver": mount.get("Driver"),
        })

    ports = {}
    for port, bindings in (network_settings.get("Ports") or {}).items():
        if bindings:
            ports[port] = bindings

    state = attrs.get("State", {})
    health = (state.get("Health") or {}).get("Status")
    exit_code = state.get("ExitCode") if c.status == "exited" else None

    return ContainerInfo(
        id=c.id[:12],
        name=c.name,
        image=image_name,
        status=c.status,
        running=c.status == "running",
        labels=config.get("Labels") or {},
        networks=networks,
        volumes=volumes,
        env_vars=config.get("Env") or [],
        ports=ports,
        db_type=db_type,
        created=attrs.get("Created"),
        health=health,
        exit_code=exit_code,
    )


def export_container_spec(container_id: str, dest_dir: Path) -> dict:
    """Dump full docker inspect JSON for a container."""
    client = get_docker()
    c = client.containers.get(container_id)
    attrs = c.attrs

    # Normalize mount keys to lowercase so backup/restore code is consistent
    mounts = [
        {
            "type": m.get("Type"),
            "name": m.get("Name"),
            "source": m.get("Source"),
            "destination": m.get("Destination"),
            "mode": m.get("Mode") or "rw",
            "driver": m.get("Driver"),
        }
        for m in attrs.get("Mounts", [])
    ]

    spec = {
        "id": c.id[:12],
        "name": c.name,
        "image": attrs["Config"]["Image"],
        "image_id": attrs["Image"],
        "config": attrs["Config"],
        "host_config": attrs["HostConfig"],
        "network_settings": attrs["NetworkSettings"],
        "mounts": mounts,
        "state": attrs["State"],
        "hostname": socket.gethostname(),
    }

    out = dest_dir / f"{c.name.lstrip('/')}.json"
    out.write_text(json.dumps(spec, indent=2, default=str))
    return spec


def export_networks(dest_dir: Path) -> list[dict]:
    """Dump non-default network configs."""
    client = get_docker()
    default_nets = {"bridge", "host", "none"}
    networks = []
    for net in client.networks.list():
        if net.name in default_nets:
            continue
        attrs = net.attrs
        networks.append({
            "name": net.name,
            "driver": attrs.get("Driver"),
            "options": attrs.get("Options", {}),
            "ipam": attrs.get("IPAM", {}),
            "labels": attrs.get("Labels", {}),
            "internal": attrs.get("Internal", False),
            "attachable": attrs.get("Attachable", False),
        })

    out = dest_dir / "networks.json"
    out.write_text(json.dumps(networks, indent=2, default=str))
    return networks
