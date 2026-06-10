import asyncio

from fastapi import APIRouter, HTTPException

from ..docker_client import get_docker
from ..job_manager import job_manager
from ..models import (
    DeployRequest, ComposeDeployRequest, DockerfileDeployRequest,
    JobType, JobStatus, BackupRequest,
)
from .. import templates as template_registry
from ..deploy.compose import run_compose_deploy, run_dockerfile_deploy

router = APIRouter(prefix="/api/deploy", tags=["deploy"])


# ─── auto-backup helper ───────────────────────────────────────────────────────

async def _trigger_auto_backup(container_names: list[str], label: str):
    """
    Wait for containers to settle, then create a backup job for them.
    Runs as a background task — failures are swallowed silently.
    """
    await asyncio.sleep(15)          # give services time to initialise
    client = get_docker()
    ids: list[str] = []
    for name in container_names:
        try:
            c = client.containers.get(name)
            ids.append(c.id[:12])
        except Exception:
            pass
    if not ids:
        return
    from ..backup.manager import run_backup
    backup_job = job_manager.create(JobType.backup, f"Auto-backup — {label}")
    try:
        await run_backup(backup_job, ids, include_images=False, label=f"Auto-backup: {label}")
    except Exception:
        pass


# ─── template deploy ──────────────────────────────────────────────────────────

@router.get("/templates")
def list_templates() -> list[dict]:
    return template_registry.list_all()


@router.get("/templates/{template_id}")
def get_template(template_id: str) -> dict:
    t = template_registry.get(template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return t.meta()


@router.post("/start")
async def start_deploy(req: DeployRequest) -> dict:
    t = template_registry.get(req.template_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"Template not found: {req.template_id}")

    missing = [
        f.key for f in t.fields
        if f.required and not req.config.get(f.key, "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required fields: {', '.join(missing)}",
        )

    job = job_manager.create(JobType.deploy, f"Deploy {t.name}")

    async def _run():
        try:
            await t.deploy(job, req.config)
            if job.status == JobStatus.success:
                asyncio.create_task(
                    _trigger_auto_backup(t.services, t.name)
                )
        except Exception:
            pass

    asyncio.create_task(_run())
    return {"job_id": job.id}


# ─── compose deploy ───────────────────────────────────────────────────────────

@router.post("/compose")
async def start_compose_deploy(req: ComposeDeployRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    if not req.yaml_content.strip():
        raise HTTPException(status_code=422, detail="yaml_content is required")

    job = job_manager.create(JobType.deploy, f"Compose deploy — {req.name}")

    async def _run():
        from datetime import datetime
        from ..models import JobStatus, LogLevel
        job.started_at = datetime.utcnow()
        job.status = JobStatus.running
        try:
            deployed = await run_compose_deploy(job, req.yaml_content, req.name)
            await job.set_progress(100, "Deploy complete")
            await job.log(LogLevel.success, f"─── Compose deploy '{req.name}' complete ───")
            await job.log(LogLevel.info, f"Containers: {', '.join(deployed)}")
            await job.finish(JobStatus.success, {
                "deploy_name": req.name,
                "containers": deployed,
            })
            asyncio.create_task(_trigger_auto_backup(deployed, req.name))
        except Exception as exc:
            from ..models import LogLevel
            await job.log(LogLevel.error, f"Compose deploy failed: {exc}")
            await job.finish(JobStatus.failed, {"error": str(exc)})

    asyncio.create_task(_run())
    return {"job_id": job.id}


# ─── dockerfile deploy ────────────────────────────────────────────────────────

@router.post("/dockerfile")
async def start_dockerfile_deploy(req: DockerfileDeployRequest) -> dict:
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="name is required")
    if not req.dockerfile_content.strip():
        raise HTTPException(status_code=422, detail="dockerfile_content is required")
    if not req.image_tag.strip():
        raise HTTPException(status_code=422, detail="image_tag is required")

    job = job_manager.create(JobType.deploy, f"Dockerfile deploy — {req.name}")

    async def _run():
        from datetime import datetime
        from ..models import JobStatus, LogLevel
        job.started_at = datetime.utcnow()
        job.status = JobStatus.running
        try:
            deployed = await run_dockerfile_deploy(
                job,
                dockerfile_content=req.dockerfile_content,
                container_name=req.name,
                image_tag=req.image_tag,
                ports=req.ports,
                environment=req.environment,
                restart=req.restart,
                deploy_name=req.name,
            )
            await job.set_progress(100, "Deploy complete")
            await job.log(LogLevel.success, f"─── Dockerfile deploy '{req.name}' complete ───")
            await job.finish(JobStatus.success, {
                "deploy_name": req.name,
                "containers": deployed,
            })
            asyncio.create_task(_trigger_auto_backup(deployed, req.name))
        except Exception as exc:
            from ..models import LogLevel
            await job.log(LogLevel.error, f"Dockerfile deploy failed: {exc}")
            await job.finish(JobStatus.failed, {"error": str(exc)})

    asyncio.create_task(_run())
    return {"job_id": job.id}
