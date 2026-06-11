import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from .config import settings
from .models import JobStatus, JobType, LogEntry, LogLevel

logger = logging.getLogger("comeback.jobs")

MAX_PERSISTED_JOBS = 200   # loaded into memory at startup


class Job:
    def __init__(self, job_type: JobType, label: str):
        self.id = str(uuid4())
        self.type = job_type
        self.label = label
        self.status = JobStatus.pending
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.logs: list[LogEntry] = []
        self.progress: int = 0
        self._subscribers: list[asyncio.Queue] = []
        self._task: Optional[asyncio.Task] = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subscribers.discard(q) if hasattr(self._subscribers, "discard") else None
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def _broadcast(self, event: dict):
        for q in list(self._subscribers):
            await q.put(event)

    async def log(self, level: LogLevel, message: str, detail: str = None):
        entry = LogEntry(level=level, message=message, detail=detail)
        self.logs.append(entry)
        await self._broadcast({"type": "log", "data": entry.model_dump(mode="json")})

    async def set_progress(self, pct: int, message: str = None):
        self.progress = pct
        await self._broadcast({"type": "progress", "pct": pct, "message": message})

    async def finish(self, status: JobStatus, summary: dict = None):
        self.status = status
        self.finished_at = datetime.utcnow()
        await self._broadcast({"type": "finished", "status": status, "summary": summary})
        job_manager._persist(self)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "progress": self.progress,
            "log_count": len(self.logs),
        }


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._loaded = False

    @property
    def _store(self) -> Path:
        return settings.backup_dir / ".jobs" / "jobs.jsonl"

    def _persist(self, job: Job):
        """Append a finished job (with logs) to the JSONL store."""
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            record = {
                **job.to_dict(),
                "logs": [e.model_dump(mode="json") for e in job.logs],
            }
            with open(self._store, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning("Could not persist job %s: %s", job.id[:8], e)

    def _load_history(self):
        """Restore the most recent finished jobs from disk (read-only replay)."""
        self._loaded = True
        try:
            if not self._store.exists():
                return
            lines = self._store.read_text().splitlines()[-MAX_PERSISTED_JOBS:]
            for line in lines:
                try:
                    rec = json.loads(line)
                    job = Job(JobType(rec["type"]), rec["label"])
                    job.id = rec["id"]
                    job.status = JobStatus(rec["status"])
                    job.created_at = datetime.fromisoformat(rec["created_at"])
                    if rec.get("started_at"):
                        job.started_at = datetime.fromisoformat(rec["started_at"])
                    if rec.get("finished_at"):
                        job.finished_at = datetime.fromisoformat(rec["finished_at"])
                    job.progress = rec.get("progress", 100)
                    job.logs = [LogEntry(**e) for e in rec.get("logs", [])]
                    self._jobs[job.id] = job
                except Exception:
                    continue   # skip corrupt lines
        except Exception as e:
            logger.warning("Could not load job history: %s", e)

    def create(self, job_type: JobType, label: str) -> Job:
        if not self._loaded:
            self._load_history()
        job = Job(job_type, label)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        if not self._loaded:
            self._load_history()
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        if not self._loaded:
            self._load_history()
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]


job_manager = JobManager()
