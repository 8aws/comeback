"""Host-wide stats for the header monitor: CPU, RAM, disk, load, temp, net, IO.

Everything is read from the host's /proc and /sys through the HOST_ROOT mount,
so it reflects the real machine, not the container. Rate values (CPU %, net,
disk IO) need two samples — previous counters are cached between calls, so the
first request after startup reports 0 and values stabilise from the second on.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from .config import settings

_prev: dict = {}   # cached counters between polls


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except Exception:
        return ""


def _cpu_counters(base: Path) -> tuple[int, int] | None:
    line = _read(base / "proc/stat").splitlines()[:1]
    if not line or not line[0].startswith("cpu "):
        return None
    parts = [int(x) for x in line[0].split()[1:]]
    total = sum(parts)
    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)   # idle + iowait
    return total, idle


def _mem(base: Path) -> dict | None:
    info = {}
    for line in _read(base / "proc/meminfo").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = int(v.strip().split()[0]) * 1024   # kB → bytes
    if "MemTotal" not in info:
        return None
    total = info["MemTotal"]
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = total - avail
    return {"total": total, "used": used,
            "pct": round(used / total * 100, 1) if total else 0}


def _load(base: Path) -> list[float] | None:
    parts = _read(base / "proc/loadavg").split()
    try:
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except (IndexError, ValueError):
        return None


def _temperature(base: Path) -> float | None:
    """Highest sensor reading in °C from thermal zones or hwmon."""
    temps = []
    for zone in sorted((base / "sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            temps.append(int(zone.read_text().strip()) / 1000)
        except Exception:
            continue
    if not temps:
        for sensor in sorted((base / "sys/class/hwmon").glob("hwmon*/temp*_input")):
            try:
                temps.append(int(sensor.read_text().strip()) / 1000)
            except Exception:
                continue
    valid = [t for t in temps if 0 < t < 150]
    return round(max(valid), 1) if valid else None


def _net_counters(base: Path) -> tuple[int, int] | None:
    rx = tx = 0
    found = False
    for line in _read(base / "proc/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        iface, data = line.split(":", 1)
        if iface.strip() == "lo":
            continue
        fields = data.split()
        if len(fields) >= 9:
            rx += int(fields[0])
            tx += int(fields[8])
            found = True
    return (rx, tx) if found else None


def _disk_io_counters(base: Path) -> tuple[int, int] | None:
    """Bytes read/written across physical disks (sectors are 512B)."""
    read = write = 0
    found = False
    for line in _read(base / "proc/diskstats").splitlines():
        fields = line.split()
        if len(fields) < 10:
            continue
        name = fields[2]
        # whole devices only — skip partitions (sda1, nvme0n1p2, mmcblk0p1…)
        is_whole = (
            (name.startswith(("sd", "hd", "vd")) and not name[-1].isdigit())
            or (name.startswith("nvme") and "p" not in name[4:])
            or (name.startswith("mmcblk") and "p" not in name[6:])
        )
        if not is_whole:
            continue
        read += int(fields[5]) * 512
        write += int(fields[9]) * 512
        found = True
    return (read, write) if found else None


def _rate(key: str, current: tuple[int, int] | None, now: float) -> dict | None:
    """Per-second rates from cached previous counters."""
    if current is None:
        return None
    prev = _prev.get(key)
    _prev[key] = (now, current)
    if not prev:
        return {"a": 0, "b": 0}
    dt = now - prev[0]
    if dt <= 0:
        return {"a": 0, "b": 0}
    return {
        "a": max(int((current[0] - prev[1][0]) / dt), 0),
        "b": max(int((current[1] - prev[1][1]) / dt), 0),
    }


def host_stats() -> dict:
    base = Path(settings.host_root)
    now = time.monotonic()

    # CPU % from counter deltas
    cpu_pct = None
    counters = _cpu_counters(base)
    if counters:
        prev = _prev.get("cpu")
        _prev["cpu"] = (now, counters)
        if prev:
            d_total = counters[0] - prev[1][0]
            d_idle = counters[1] - prev[1][1]
            if d_total > 0:
                cpu_pct = round((1 - d_idle / d_total) * 100, 1)
        else:
            cpu_pct = 0.0

    disk = None
    try:
        usage = shutil.disk_usage(base)
        disk = {"total": usage.total, "used": usage.used,
                "pct": round(usage.used / usage.total * 100, 1)}
    except Exception:
        pass

    net = _rate("net", _net_counters(base), now)
    io = _rate("io", _disk_io_counters(base), now)

    return {
        "cpu_pct": cpu_pct,
        "load": _load(base),
        "mem": _mem(base),
        "disk": disk,
        "temp_c": _temperature(base),
        "net": {"rx_s": net["a"], "tx_s": net["b"]} if net else None,
        "io": {"read_s": io["a"], "write_s": io["b"]} if io else None,
    }
