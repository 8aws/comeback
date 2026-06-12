"""Container image update detection and one-click updates (built-in Watchtower).

Update check: compares the local image digest of each container against the
registry digest obtained via the Docker daemon (`get_registry_data`), which
works with Docker Hub, ghcr.io and any registry the daemon can reach.

Update flow per container:
  1. (optional) full backup via the normal backup pipeline (child job)
  2. pull new image with streaming progress
  3. stop + remove the old container (volumes untouched)
  4. recreate with identical configuration (same spec→kwargs path as restore)
  5. quick health check; on crash, automatic rollback to the previous image

Comeback itself is detected (is_self) and only gets a pull — a container
cannot stop and recreate itself; the recreation is done from compose/ZimaOS.
"""
from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path

from .backup.containers import export_container_spec
from .backup.manager import run_backup
from .deploy.compose import _pull_with_progress
from .docker_client import get_docker
from .job_manager import Job, job_manager
from .models import JobStatus, JobType, LogLevel
from .restore.manager import _build_run_kwargs


def _self_container_id() -> str:
    """Inside a container the default hostname is the short container id."""
    try:
        return Path("/etc/hostname").read_text().strip()
    except Exception:
        return ""


def _local_digests(container) -> set[str]:
    """sha256 digests this container's image is known by in registries."""
    try:
        repo_digests = container.image.attrs.get("RepoDigests") or []
        return {d.split("@", 1)[1] for d in repo_digests if "@" in d}
    except Exception:
        return set()


async def _check_one(c, self_id: str) -> dict:
    client = get_docker()
    loop = asyncio.get_event_loop()
    image_ref = c.attrs["Config"]["Image"]
    entry = {
        "id": c.id[:12],
        "name": c.name,
        "image": image_ref,
        "running": c.status == "running",
        "is_self": bool(self_id) and c.id.startswith(self_id),
        "status": "unknown",
        "detail": None,
    }
    if "@sha256:" in image_ref:
        entry["status"] = "pinned"
        entry["detail"] = "Imagen fijada por digest — sin seguimiento de tag"
        return entry
    local = _local_digests(c)
    if not local:
        entry["status"] = "local"
        entry["detail"] = "Imagen construida localmente — no hay registry que consultar"
        return entry
    try:
        reg_data = await loop.run_in_executor(
            None, lambda: client.images.get_registry_data(image_ref))
        remote_digest = reg_data.id
        entry["status"] = "current" if remote_digest in local else "update"
        if entry["status"] == "update":
            entry["detail"] = f"Nuevo digest: {remote_digest[:19]}…"
    except Exception as e:
        msg = str(e)
        if "denied" in msg or "unauthorized" in msg or "not found" in msg.lower():
            # Docker Desktop's containerd store gives RepoDigests to local
            # builds too — a denied registry lookup means a local-only image
            entry["status"] = "local"
            entry["detail"] = "Imagen no publicada en un registry accesible"
        else:
            entry["detail"] = f"No se pudo consultar el registry: {msg}"
    return entry


async def check_container(container_id: str) -> dict:
    """Update status for a single container — used for progressive UI checks."""
    client = get_docker()
    loop = asyncio.get_event_loop()
    c = await loop.run_in_executor(None, lambda: client.containers.get(container_id))
    return await _check_one(c, _self_container_id())


async def check_updates() -> list[dict]:
    client = get_docker()
    loop = asyncio.get_event_loop()
    containers = await loop.run_in_executor(None, lambda: client.containers.list(all=True))
    self_id = _self_container_id()

    results = await asyncio.gather(*(_check_one(c, self_id) for c in containers))
    # updates first, then current, self always near the end of its group
    order = {"update": 0, "unknown": 1, "local": 2, "pinned": 3, "current": 4}
    return sorted(results, key=lambda r: (order.get(r["status"], 9), r["is_self"], r["name"]))


async def _quick_health_check(client, name: str, job: Job, wait_s: int = 8) -> bool:
    await asyncio.sleep(wait_s)
    loop = asyncio.get_event_loop()
    c = await loop.run_in_executor(None, lambda: client.containers.get(name))
    if c.status in ("running", "created"):
        return True
    logs = await loop.run_in_executor(
        None, lambda: c.logs(tail=15).decode(errors="replace"))
    await job.log(LogLevel.error, f"{name} no arranca (estado: {c.status})", detail=logs)
    return False


async def _update_one(job: Job, container_id: str, backup_first: bool) -> dict:
    """Update a single container. Raises on failure (after rollback)."""
    client = get_docker()
    loop = asyncio.get_event_loop()

    container = await loop.run_in_executor(
        None, lambda: client.containers.get(container_id))
    name = container.name
    image_ref = container.attrs["Config"]["Image"]
    old_image_id = container.attrs["Image"]   # sha256 id for rollback

    # ── Self-update: pull only ───────────────────────────────────────────────
    self_id = _self_container_id()
    if self_id and container.id.startswith(self_id):
        await job.log(LogLevel.info, f"Descargando nueva imagen de {image_ref}…")
        await _pull_with_progress(client, image_ref, job, force=True)
        await job.log(LogLevel.warning,
            "Comeback no puede recrearse a sí mismo. Imagen descargada — "
            "recrea el contenedor desde compose/ZimaOS para aplicarla.")
        return {"container": name, "pulled": image_ref, "self_update": True}

    # ── Optional pre-update backup (child job) ───────────────────────────────
    if backup_first:
        backup_job = job_manager.create(JobType.backup, f"Pre-update: {name}")
        await job.log(LogLevel.info, f"Backup previo iniciado (job {backup_job.id[:8]})")
        await run_backup(backup_job, [container_id], False, f"pre-update {name}")
        if backup_job.status != JobStatus.success:
            raise RuntimeError("El backup previo falló — actualización cancelada")
        await job.log(LogLevel.success, "Backup previo completado")

    # ── Pull new image ───────────────────────────────────────────────────────
    await _pull_with_progress(client, image_ref, job, force=True)

    new_image = await loop.run_in_executor(
        None, lambda: client.images.get(image_ref))
    if new_image.id == old_image_id:
        await job.log(LogLevel.success, f"{name} ya estaba en la última versión")
        return {"container": name, "already_current": True}

    # ── Capture spec, replace container ──────────────────────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        spec = export_container_spec(container.id, Path(tmp))

    was_running = container.status == "running"
    await job.log(LogLevel.info, f"Parando y eliminando {name} (volúmenes intactos)")
    await loop.run_in_executor(None, lambda: container.stop(timeout=10))
    await loop.run_in_executor(None, container.remove)

    async def _create_and_start(image_override: str | None = None):
        kwargs, networks = _build_run_kwargs(spec)
        if image_override:
            kwargs["image"] = image_override
        new_c = await loop.run_in_executor(
            None, lambda: client.containers.create(**kwargs))
        for net_name in networks[1:]:
            try:
                net = client.networks.get(net_name)
                await loop.run_in_executor(None, lambda: net.connect(new_c))
            except Exception as e:
                await job.log(LogLevel.warning, f"Red {net_name}: {e}")
        if was_running:
            await loop.run_in_executor(None, new_c.start)
        return new_c

    # Any failure past this point leaves no old container — must roll back
    crashed = False
    try:
        await _create_and_start()
        await job.log(LogLevel.success, f"{name} recreado con la nueva imagen")
        if was_running:
            await job.log(LogLevel.info, f"Comprobando salud de {name}…")
            crashed = not await _quick_health_check(client, name, job)
    except Exception as e:
        await job.log(LogLevel.error, f"La nueva imagen falla al arrancar: {e}")
        crashed = True

    if crashed:
        await job.log(LogLevel.warning,
                      f"Rollback: restaurando imagen anterior {old_image_id[:19]}…")
        try:
            broken = await loop.run_in_executor(
                None, lambda: client.containers.get(name))
            await loop.run_in_executor(None, lambda: broken.remove(force=True))
        except Exception:
            pass  # may not exist if create itself failed
        await _create_and_start(image_override=old_image_id)
        if not was_running or await _quick_health_check(client, name, job):
            await job.log(LogLevel.success, f"Rollback OK — {name} con la imagen anterior")
        raise RuntimeError("La nueva imagen no arranca — rollback aplicado")

    return {"container": name, "image": image_ref, "updated": True}


async def run_recreate(job: Job, container_id: str):
    """Recreate a container with its current image and exact configuration."""
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running
    client = get_docker()
    loop = asyncio.get_event_loop()
    try:
        container = await loop.run_in_executor(
            None, lambda: client.containers.get(container_id))
        name = container.name

        self_id = _self_container_id()
        if self_id and container.id.startswith(self_id):
            raise RuntimeError("Comeback no puede recrearse a sí mismo")

        await job.set_progress(20, f"Capturando configuración de {name}…")
        with tempfile.TemporaryDirectory() as tmp:
            spec = export_container_spec(container.id, Path(tmp))

        was_running = container.status == "running"
        await job.set_progress(50, f"Recreando {name}…")
        await job.log(LogLevel.info, f"Parando y eliminando {name} (volúmenes intactos)")
        await loop.run_in_executor(None, lambda: container.stop(timeout=10))
        await loop.run_in_executor(None, container.remove)

        kwargs, networks = _build_run_kwargs(spec)
        new_c = await loop.run_in_executor(
            None, lambda: client.containers.create(**kwargs))
        for net_name in networks[1:]:
            try:
                net = client.networks.get(net_name)
                await loop.run_in_executor(None, lambda: net.connect(new_c))
            except Exception as e:
                await job.log(LogLevel.warning, f"Red {net_name}: {e}")
        if was_running:
            await loop.run_in_executor(None, new_c.start)
            if not await _quick_health_check(client, name, job):
                raise RuntimeError("El contenedor recreado no arranca")

        await job.set_progress(100, "Recreación completada")
        await job.log(LogLevel.success, f"{name} recreado")
        await job.finish(JobStatus.success, {"container": name, "recreated": True})
    except Exception as e:
        await job.log(LogLevel.error, f"Recreación fallida: {e}")
        await job.finish(JobStatus.failed, {"error": str(e)})


async def run_pull(job: Job, container_id: str):
    """Force-pull the image of a container (no recreation)."""
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running
    client = get_docker()
    loop = asyncio.get_event_loop()
    try:
        container = await loop.run_in_executor(
            None, lambda: client.containers.get(container_id))
        image_ref = container.attrs["Config"]["Image"]
        if "@sha256:" in image_ref:
            raise RuntimeError("Imagen fijada por digest — no hay tag que actualizar")
        await job.set_progress(20, f"Descargando {image_ref}…")
        await _pull_with_progress(client, image_ref, job, force=True)
        await job.set_progress(100, "Pull completado")
        await job.log(LogLevel.success,
                      f"Imagen {image_ref} descargada — usa Recrear para aplicarla")
        await job.finish(JobStatus.success, {"pulled": image_ref})
    except Exception as e:
        await job.log(LogLevel.error, f"Pull fallido: {e}")
        await job.finish(JobStatus.failed, {"error": str(e)})


async def run_update(job: Job, container_id: str, backup_first: bool):
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running
    try:
        await job.set_progress(10, "Actualizando…")
        summary = await _update_one(job, container_id, backup_first)
        await job.set_progress(100, "Actualización completada")
        await job.finish(JobStatus.success, summary)
    except Exception as e:
        await job.log(LogLevel.error, f"Actualización fallida: {e}")
        await job.finish(JobStatus.failed, {"error": str(e)})


async def run_update_all(job: Job, container_ids: list[str], backup_first: bool):
    """Update several containers serially; one failure does not stop the rest."""
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running
    total = len(container_ids)
    updated, failed = [], []

    for idx, cid in enumerate(container_ids):
        pct = int((idx / max(total, 1)) * 95)
        await job.set_progress(pct, f"Actualizando {idx + 1}/{total}…")
        await job.log(LogLevel.info, f"━━ [{idx + 1}/{total}] ━━")
        try:
            summary = await _update_one(job, cid, backup_first)
            updated.append(summary.get("container", cid))
        except Exception as e:
            failed.append(cid)
            await job.log(LogLevel.error, f"Fallo en {cid}: {e} — continuando con el resto")

    await job.set_progress(100, "Actualización masiva completada")
    result = {"updated": updated, "failed": failed, "total": total}
    if failed:
        await job.log(LogLevel.warning,
                      f"Completado con errores: {len(updated)} OK, {len(failed)} fallidos")
        await job.finish(JobStatus.failed if not updated else JobStatus.success, result)
    else:
        await job.log(LogLevel.success, f"{len(updated)} contenedor(es) actualizados")
        await job.finish(JobStatus.success, result)
