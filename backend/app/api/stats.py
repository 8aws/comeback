"""Per-container resource stats (CPU/RAM/network/disk IO) for the Monitor tab.

Uses the Docker stats API one-shot (stream=False). Each call takes ~1s on the
daemon side, so all containers are sampled in parallel threads.
"""
import asyncio

from fastapi import APIRouter

from ..docker_client import get_docker
from ..host_stats import host_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/host")
async def host() -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, host_stats)


def _cpu_percent(s: dict) -> float:
    try:
        cpu = s["cpu_stats"]["cpu_usage"]["total_usage"] - \
              s["precpu_stats"]["cpu_usage"]["total_usage"]
        system = s["cpu_stats"]["system_cpu_usage"] - \
                 s["precpu_stats"].get("system_cpu_usage", 0)
        online = s["cpu_stats"].get("online_cpus") or \
                 len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
        if system > 0 and cpu >= 0:
            return round((cpu / system) * online * 100, 1)
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return 0.0


def _mem(s: dict) -> tuple[int, int]:
    try:
        stats = s["memory_stats"]
        usage = stats.get("usage", 0)
        # cgroup v2 reports cache as inactive_file — subtract for "real" usage
        usage -= stats.get("stats", {}).get("inactive_file", 0)
        return max(usage, 0), stats.get("limit", 0)
    except (KeyError, TypeError):
        return 0, 0


def _net(s: dict) -> tuple[int, int]:
    rx = tx = 0
    for iface in (s.get("networks") or {}).values():
        rx += iface.get("rx_bytes", 0)
        tx += iface.get("tx_bytes", 0)
    return rx, tx


def _block_io(s: dict) -> tuple[int, int]:
    read = write = 0
    for entry in (s.get("blkio_stats", {}).get("io_service_bytes_recursive") or []):
        op = (entry.get("op") or "").lower()
        if op == "read":
            read += entry.get("value", 0)
        elif op == "write":
            write += entry.get("value", 0)
    return read, write


@router.get("")
async def container_stats() -> list[dict]:
    client = get_docker()
    loop = asyncio.get_event_loop()
    containers = await loop.run_in_executor(
        None, lambda: client.containers.list())   # running only

    def _sample(c) -> dict:
        s = c.stats(stream=False)
        mem_usage, mem_limit = _mem(s)
        rx, tx = _net(s)
        io_r, io_w = _block_io(s)
        return {
            "id": c.id[:12],
            "name": c.name,
            "created": c.attrs.get("Created"),
            "cpu_pct": _cpu_percent(s),
            "mem_usage": mem_usage,
            "mem_limit": mem_limit,
            "mem_pct": round(mem_usage / mem_limit * 100, 1) if mem_limit else 0,
            "net_rx": rx,
            "net_tx": tx,
            "block_read": io_r,
            "block_write": io_w,
            "pids": s.get("pids_stats", {}).get("current", 0),
        }

    async def _one(c):
        try:
            return await loop.run_in_executor(None, _sample, c)
        except Exception as e:
            return {"id": c.id[:12], "name": c.name, "error": str(e)}

    results = await asyncio.gather(*(_one(c) for c in containers))
    return sorted(results, key=lambda r: -(r.get("cpu_pct") or 0))
