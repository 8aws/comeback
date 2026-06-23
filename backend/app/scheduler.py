"""Scheduled backups with retention.

Schedules are stored as JSON in {BACKUP_PATH}/.schedules.json. A background
asyncio loop checks every minute whether a schedule is due (interpreting the
configured time in the container's TZ) and launches a normal backup job.

After a successful run, retention is applied: archives whose manifest label
matches the schedule marker (⏰ {name}) beyond the newest N are deleted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .config import settings
from .job_manager import job_manager
from .models import JobStatus, JobType

logger = logging.getLogger("comeback.scheduler")

CHECK_INTERVAL_S = 60
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _store() -> Path:
    return settings.backup_dir / ".schedules.json"


def load_schedules() -> list[dict]:
    try:
        if _store().exists():
            return json.loads(_store().read_text())
    except Exception as e:
        logger.warning("Could not load schedules: %s", e)
    return []


def save_schedules(schedules: list[dict]):
    _store().parent.mkdir(parents=True, exist_ok=True)
    _store().write_text(json.dumps(schedules, indent=2, default=str))


def _label_for(sched: dict) -> str:
    return f"⏰ {sched['name']}"


def _next_run(sched: dict, after: datetime) -> datetime:
    """Next occurrence of the schedule strictly after `after` (local time)."""
    hour, minute = (int(x) for x in sched.get("time", "03:00").split(":"))
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if sched.get("frequency") == "weekly":
        target_wd = int(sched.get("weekday", 0))
        while candidate.weekday() != target_wd or candidate <= after:
            candidate += timedelta(days=1)
            candidate = candidate.replace(hour=hour, minute=minute)
    else:   # daily
        if candidate <= after:
            candidate += timedelta(days=1)
    return candidate


def describe(sched: dict) -> dict:
    """Schedule dict enriched with next_run for the API/UI."""
    last = sched.get("last_run")
    base = datetime.fromisoformat(last) if last else datetime.now() - timedelta(days=8)
    return {**sched, "next_run": _next_run(sched, max(base, datetime.now() - timedelta(minutes=1))).isoformat(),
            "last_status": sched.get("last_status")}


def _is_due(sched: dict, now: datetime) -> bool:
    if not sched.get("enabled", True):
        return False
    hour, minute = (int(x) for x in sched.get("time", "03:00").split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        return False
    if sched.get("frequency") == "weekly" and now.weekday() != int(sched.get("weekday", 0)):
        return False
    # A schedule created after today's slot must wait for the next one —
    # otherwise a 03:00 schedule created at 13:00 fires immediately
    created = sched.get("created_at")
    if created and datetime.fromisoformat(created) > slot:
        return False
    last = sched.get("last_run")
    return not last or datetime.fromisoformat(last) < slot


def _read_label(archive: Path) -> str | None:
    try:
        with tarfile.open(archive, "r:gz") as tar:
            m = next((x for x in tar.getmembers() if x.name.endswith("manifest.json")), None)
            if m:
                return json.loads(tar.extractfile(m).read().decode()).get("label")
    except Exception:
        pass
    return None


async def _apply_retention(sched: dict, job):
    """Keep the newest N archives created by this schedule, delete the rest."""
    from .models import LogLevel
    keep = int(sched.get("retention", 7))
    if keep <= 0:
        return
    label = _label_for(sched)
    loop = asyncio.get_event_loop()

    def _matching() -> list[Path]:
        result = []
        for archive in sorted(settings.backup_dir.glob("backup_*.tar.gz"), reverse=True):
            if _read_label(archive) == label:
                result.append(archive)
        return result

    archives = await loop.run_in_executor(None, _matching)
    for old in archives[keep:]:
        try:
            old.unlink()
            sidecar = old.with_name(old.name.replace(".tar.gz", ".sha256"))
            sidecar.unlink(missing_ok=True)
            await job.log(LogLevel.info, f"Retención: eliminado {old.name}")
        except Exception as e:
            await job.log(LogLevel.warning, f"Retención: no se pudo eliminar {old.name}: {e}")


async def run_schedule_now(sched: dict) -> str:
    """Launch the backup job for a schedule. Returns the job id."""
    from .backup.manager import run_backup
    from .models import LogLevel

    job = job_manager.create(JobType.backup, f"⏰ Programado: {sched['name']}")

    async def _run():
        await run_backup(job, sched["container_ids"], sched.get("include_images", False),
                         _label_for(sched))
        if job.status == JobStatus.success:
            await _apply_retention(sched, job)
        _update_last_status(sched["id"], job.status)

    asyncio.create_task(_run())
    return job.id


def _update_last_status(sched_id: str, status: JobStatus):
    schedules = load_schedules()
    for s in schedules:
        if s["id"] == sched_id:
            s["last_status"] = str(status)
            break
    save_schedules(schedules)


async def scheduler_loop():
    logger.info("Backup scheduler started (checking every %ds)", CHECK_INTERVAL_S)
    while True:
        try:
            now = datetime.now()
            schedules = load_schedules()
            changed = False
            for sched in schedules:
                if _is_due(sched, now):
                    logger.info("Schedule due: %s", sched["name"])
                    sched["last_run"] = now.isoformat()
                    changed = True
                    await run_schedule_now(sched)
            if changed:
                save_schedules(schedules)
        except Exception as e:
            logger.error("Scheduler tick failed: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_S)


def create_schedule(data: dict) -> dict:
    schedules = load_schedules()
    sched = {
        "id": str(uuid4())[:8],
        "name": data["name"],
        "container_ids": data["container_ids"],
        "frequency": data.get("frequency", "daily"),
        "time": data.get("time", "03:00"),
        "weekday": int(data.get("weekday", 0)),
        "retention": int(data.get("retention", 7)),
        "include_images": bool(data.get("include_images", False)),
        "enabled": True,
        "last_run": None,
        "created_at": datetime.now().isoformat(),
    }
    schedules.append(sched)
    save_schedules(schedules)
    return sched


def update_schedule(sched_id: str, data: dict) -> dict | None:
    schedules = load_schedules()
    for sched in schedules:
        if sched["id"] == sched_id:
            for key in ("name", "container_ids", "frequency", "time",
                        "weekday", "retention", "include_images", "enabled"):
                if key in data and data[key] is not None:
                    sched[key] = data[key]
            save_schedules(schedules)
            return sched
    return None


def delete_schedule(sched_id: str) -> bool:
    schedules = load_schedules()
    remaining = [s for s in schedules if s["id"] != sched_id]
    if len(remaining) == len(schedules):
        return False
    save_schedules(remaining)
    return True
