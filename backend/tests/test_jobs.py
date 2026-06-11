"""Job persistence: finished jobs survive a JobManager reload."""
import asyncio

from app.job_manager import JobManager, job_manager
from app.models import JobStatus, JobType, LogLevel


def test_finished_job_persists_and_reloads():
    async def _run():
        job = job_manager.create(JobType.backup, "persist-me")
        await job.log(LogLevel.info, "hello")
        await job.finish(JobStatus.success, {"ok": True})
        return job.id

    job_id = asyncio.run(_run())
    assert job_manager._store.exists()

    # Fresh manager simulates a server restart
    fresh = JobManager()
    reloaded = fresh.get(job_id)
    assert reloaded is not None
    assert reloaded.status == JobStatus.success
    assert reloaded.label == "persist-me"
    assert reloaded.logs[0].message == "hello"


def test_corrupt_lines_are_skipped():
    async def _run():
        job = job_manager.create(JobType.verify, "good-one")
        await job.finish(JobStatus.success)
        return job.id

    job_id = asyncio.run(_run())
    with open(job_manager._store, "a") as f:
        f.write("{not valid json}\n")

    fresh = JobManager()
    assert fresh.get(job_id) is not None
    assert len(fresh.list()) == 1
