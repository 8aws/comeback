"""Server environment report for the Deploy tab.

Goal: surface conflicts BEFORE a deploy fails — which host ports are taken
(by Docker bindings and by any host process), which generic bind-mount roots
other containers use (never specific paths, for privacy), and which networks
are shared by several containers.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .config import settings
from .docker_client import get_docker

# infra paths that say nothing about where deploy data should live
_EXCLUDED_PREFIXES = ("/var/run", "/run", "/etc", "/dev", "/sys", "/proc", "/tmp")


def docker_used_ports() -> list[int]:
    """Host ports bound (or reserved) by any container, running or not."""
    client = get_docker()
    ports: set[int] = set()
    for c in client.containers.list(all=True):
        bindings = (c.attrs.get("HostConfig") or {}).get("PortBindings") or {}
        for entries in bindings.values():
            for entry in entries or []:
                try:
                    ports.add(int(entry.get("HostPort")))
                except (TypeError, ValueError):
                    continue
    return sorted(ports)


def host_listening_ports(base: Path | None = None) -> list[int]:
    """Ports with a listening/bound socket on the host (TCP LISTEN, UDP bound)."""
    base = base or Path(settings.host_root)
    ports: set[int] = set()
    for name, listen_state in (("tcp", "0A"), ("tcp6", "0A"), ("udp", "07"), ("udp6", "07")):
        try:
            lines = (base / f"proc/net/{name}").read_text().splitlines()[1:]
        except Exception:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != listen_state:
                continue
            try:
                port = int(fields[1].rsplit(":", 1)[1], 16)
            except (IndexError, ValueError):
                continue
            if 0 < port < 65536:
                ports.add(port)
    return sorted(ports)


def reduce_to_roots(sources: list[str], depth: int = 2) -> list[dict]:
    """Generic parent dirs (first `depth` components) of bind sources with counts.

    /share/Container/ente/data → /share/Container — specific paths never leak.
    """
    counter: Counter = Counter()
    for src in sources:
        if not src or not src.startswith("/"):
            continue
        if src.startswith(_EXCLUDED_PREFIXES):
            continue
        parts = [p for p in src.split("/") if p]
        root = "/" + "/".join(parts[:depth])
        counter[root] += 1
    return [{"path": path, "count": count}
            for path, count in counter.most_common(8)]


def bind_mount_roots() -> list[dict]:
    client = get_docker()
    sources = []
    for c in client.containers.list(all=True):
        for m in c.attrs.get("Mounts", []):
            if m.get("Type") == "bind":
                sources.append(m.get("Source") or "")
    return reduce_to_roots(sources)


def common_networks() -> list[dict]:
    """User-defined networks shared by 2+ containers — names only, no details."""
    client = get_docker()
    result = []
    for net in client.networks.list():
        if net.name in ("bridge", "host", "none"):
            continue
        try:
            count = len(net.attrs.get("Containers") or {})
        except Exception:
            count = 0
        if count >= 2:
            result.append({"name": net.name, "count": count})
    return sorted(result, key=lambda n: -n["count"])[:8]


def environment_report() -> dict:
    docker_ports = docker_used_ports()
    host_ports = host_listening_ports()
    return {
        "docker_ports": docker_ports,
        "host_ports": host_ports,
        "used_ports": sorted(set(docker_ports) | set(host_ports)),
        "bind_roots": bind_mount_roots(),
        "common_networks": common_networks(),
    }
