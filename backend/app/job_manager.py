import asyncio
from datetime import datetime
from typing import Callable, Optional
from uuid import uuid4

from .models import JobStatus, JobType, LogEntry, LogLevel


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

    def create(self, job_type: JobType, label: str) -> Job:
        job = Job(job_type, label)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.to_dict() for j in sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)]


job_manager = JobManager()
