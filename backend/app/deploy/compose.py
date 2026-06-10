"""
Compose YAML deployer — parses a subset of docker-compose format
and deploys via the Docker Python SDK (no docker CLI required).

Supported service keys:
  image, build.dockerfile_inline, command, entrypoint, environment,
  ports, volumes, networks, restart, labels, hostname, privileged,
  cap_add, depends_on (ordering only).

Supported top-level keys: services, networks, volumes.
"""
from __future__ import annotations

import asyncio
import io
import tarfile
from typing import Any

import yaml

from ..docker_client import get_docker
from ..job_manager import Job
from ..models import LogLevel


# ─── helpers ──────────────────────────────────────────────────────────────────

def _parse_env(env: Any) -> list[str]:
    """Dict or list → list of KEY=VALUE strings."""
    if isinstance(env, dict):
        return [f"{k}={v}" for k, v in env.items()]
    return [str(e) for e in (env or [])]


def _parse_labels(labels: Any) -> dict[str, str]:
    if isinstance(labels, dict):
        return {str(k): str(v) for k, v in labels.items()}
    result: dict[str, str] = {}
    for item in (labels or []):
        s = str(item)
        if "=" in s:
            k, v = s.split("=", 1)
            result[k] = v
        else:
            result[s] = ""
    return result


def _parse_ports(ports: list | None) -> dict:
    """
    Accepts short syntax strings: "host:container[/proto]" or "container[/proto]"
    Returns docker-py port-binding dict: {"container_port/proto": host_port_or_None}
    """
    result: dict = {}
    for p in (ports or []):
        p = str(p)
        parts = p.split(":")
        if len(parts) >= 2:
            host = parts[-2]
            container = parts[-1]
        else:
            host = None
            container = parts[0]
        # ensure proto
        if "/" not in container:
            container += "/tcp"
        result[container] = int(host) if host else None
    return result


def _topological_sort(services: dict) -> list[str]:
    """Simple toposort by depends_on — guarantees start order."""
    visited: set[str] = set()
    order: list[str] = []

    def visit(name: str):
        if name in visited:
            return
        visited.add(name)
        svc = services.get(name, {})
        deps = svc.get("depends_on", [])
        if isinstance(deps, dict):
            deps = list(deps.keys())
        for dep in (deps or []):
            if dep in services:
                visit(dep)
        order.append(name)

    for name in services:
        visit(name)
    return order


def _make_dockerfile_tar(content: str) -> bytes:
    """Pack a Dockerfile string into an in-memory tar for client.images.build()."""
    buf = io.BytesIO()
    data = content.encode()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="Dockerfile")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _pull_if_needed(client, image: str):
    try:
        client.images.get(image)
    except Exception:
        client.images.pull(image)


async def _pull_with_progress(client, image: str, job: Job, force: bool = False):
    """
    Pull image streaming real-time progress per layer:
      "php:7-apache — 45.2 MB / 120.0 MB (37%) @ 3.1 MB/s"
    Uses client.api (low-level APIClient) for streaming events.
    Runs the blocking iterator in a thread executor so the event loop
    stays free for WebSocket messages.

    force=True skips the local cache check — required by the updater,
    where the point is fetching a newer image for a tag we already have.
    """
    import time as _time
    await job.log(LogLevel.info, f"Pulling {image}…")

    if not force:
        try:
            client.images.get(image)
            await job.log(LogLevel.success, f"Imagen en caché: {image}")
            return
        except Exception:
            pass

    loop = asyncio.get_event_loop()

    def _stream():
        layers: dict[str, tuple[int, int]] = {}   # id → (current_bytes, total_bytes)
        last_log = _time.monotonic()
        start    = _time.monotonic()

        for event in client.api.pull(image, stream=True, decode=True):
            status = event.get("status", "")
            lid    = event.get("id", "")
            detail = event.get("progressDetail") or {}

            if status == "Pulling fs layer" and lid:
                layers.setdefault(lid, (0, 0))
            elif status == "Downloading" and lid and detail.get("total"):
                layers[lid] = (detail.get("current", 0), detail["total"])
            elif status in ("Pull complete", "Already exists", "Layer already exists") and lid:
                _, tot = layers.get(lid, (0, 0))
                layers[lid] = (tot, tot)

            now = _time.monotonic()
            if now - last_log >= 5 and layers:
                curr_b  = sum(c for c, _ in layers.values())
                tot_b   = sum(t for _, t in layers.values())
                elapsed = max(now - start, 0.1)
                speed   = curr_b / elapsed

                def mb(b: int) -> str: return f"{b / 1_048_576:.1f} MB"

                if tot_b > 0:
                    pct = int(100 * curr_b / tot_b)
                    msg = f"  {image}: {mb(curr_b)}/{mb(tot_b)} ({pct}%) @ {mb(speed)}/s"
                else:
                    msg = f"  {image}: {mb(curr_b)} recibidos @ {mb(speed)}/s"

                asyncio.run_coroutine_threadsafe(job.log(LogLevel.info, msg), loop)
                last_log = now

    await loop.run_in_executor(None, _stream)
    await job.log(LogLevel.success, f"Imagen lista: {image}")


def _build_image(client, tar_bytes: bytes, tag: str):
    image, logs = client.images.build(
        fileobj=io.BytesIO(tar_bytes),
        tag=tag,
        rm=True,
        pull=True,
    )
    for _ in logs:
        pass  # consume generator
    return image


# ─── main ─────────────────────────────────────────────────────────────────────

async def run_compose_deploy(job: Job, yaml_content: str, deploy_name: str) -> list[str]:
    """
    Parse docker-compose YAML and deploy all services.
    Returns list of deployed container names.
    Rolls back (removes created containers/networks) on any error.
    """
    client = get_docker()
    loop = asyncio.get_event_loop()
    deployed: list[str] = []
    newly_created_networks: list[str] = []
    newly_created_volumes: list[str] = []

    async def _rollback():
        await job.log(LogLevel.warning, "⚠️  Deploy fallido — iniciando rollback…")
        for cname in reversed(deployed):
            try:
                client.containers.get(cname).remove(force=True)
                await job.log(LogLevel.info, f"  Eliminado: {cname}")
            except Exception:
                pass
        for net in newly_created_networks:
            try:
                client.networks.get(net).remove()
                await job.log(LogLevel.info, f"  Eliminada red: {net}")
            except Exception:
                pass
        for vol in newly_created_volumes:
            try:
                client.volumes.get(vol).remove()
                await job.log(LogLevel.info, f"  Eliminado volumen: {vol}")
            except Exception:
                pass
        await job.log(LogLevel.info, "Rollback completado")

    try:
        spec = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML inválido: {exc}") from exc

    if not isinstance(spec, dict):
        raise ValueError("El YAML debe ser un mapping a nivel raíz")

    services: dict = spec.get("services") or {}
    networks_spec: dict = spec.get("networks") or {}
    volumes_spec: dict = spec.get("volumes") or {}

    if not services:
        raise ValueError("No hay servicios definidos en el compose YAML")

    svc_count = len(services)

    try:
        # 1 — networks
        await job.set_progress(5, "Creando redes…")
        existing_nets = {n.name for n in client.networks.list()}
        for net_name, net_cfg in networks_spec.items():
            net_cfg = net_cfg or {}
            if net_cfg.get("external"):
                await job.log(LogLevel.info, f"Red externa (skipped): {net_name}")
                continue
            if net_name not in existing_nets:
                await job.log(LogLevel.info, f"Creando red: {net_name}")
                cfg = net_cfg
                await loop.run_in_executor(None, lambda n=net_name, c=cfg: client.networks.create(
                    n,
                    driver=c.get("driver", "bridge"),
                    options=c.get("driver_opts") or {},
                    labels=_parse_labels(c.get("labels")),
                    internal=bool(c.get("internal", False)),
                    attachable=bool(c.get("attachable", True)),
                ))
                newly_created_networks.append(net_name)
            else:
                await job.log(LogLevel.info, f"Red ya existe: {net_name}")

        # 2 — volumes
        await job.set_progress(10, "Creando volúmenes…")
        existing_vols = {v.name for v in client.volumes.list()}
        for vol_name, vol_cfg in volumes_spec.items():
            vol_cfg = vol_cfg or {}
            if vol_cfg.get("external"):
                await job.log(LogLevel.info, f"Volumen externo (skipped): {vol_name}")
                continue
            if vol_name not in existing_vols:
                await job.log(LogLevel.info, f"Creando volumen: {vol_name}")
                cfg = vol_cfg
                await loop.run_in_executor(None, lambda n=vol_name, c=cfg: client.volumes.create(
                    name=n,
                    driver=c.get("driver", "local"),
                    driver_opts=c.get("driver_opts") or {},
                    labels=_parse_labels(c.get("labels")),
                ))
                newly_created_volumes.append(vol_name)

        # 3 — services in dependency order
        order = _topological_sort(services)
        base_pct, end_pct = 15, 95
        step_pct = max(1, (end_pct - base_pct) // svc_count)

        for idx, svc_name in enumerate(order):
            svc = services[svc_name]
            pct = base_pct + idx * step_pct
            await job.set_progress(pct, f"Desplegando: {svc_name}…")

            container_name = svc_name

            # remove existing
            try:
                old = client.containers.get(container_name)
                await job.log(LogLevel.info, f"Eliminando contenedor existente: {container_name}")
                await loop.run_in_executor(None, lambda c=old: c.remove(force=True))
            except Exception:
                pass

            # image: build inline or pull
            if "build" in svc:
                build_cfg = svc["build"] if isinstance(svc["build"], dict) else {}
                dockerfile = (
                    build_cfg.get("dockerfile_inline")
                    or build_cfg.get("dockerfile", "")
                )
                if not dockerfile:
                    raise ValueError(
                        f"Servicio '{svc_name}': build requiere 'dockerfile_inline' "
                        "(build desde ruta de host no está soportado)"
                    )
                image_tag = f"comeback-{deploy_name}-{svc_name}:latest"
                await job.log(LogLevel.info, f"Construyendo imagen para {svc_name} → {image_tag}…")
                tar_bytes = _make_dockerfile_tar(dockerfile)

                done = asyncio.Event()
                async def _build_hb(tag=image_tag):
                    elapsed = 0
                    while not done.is_set():
                        await asyncio.sleep(8)
                        elapsed += 8
                        if not done.is_set():
                            await job.log(LogLevel.info, f"  … construyendo {tag} ({elapsed}s)")
                hb = asyncio.create_task(_build_hb())
                try:
                    await loop.run_in_executor(None, lambda tb=tar_bytes, t=image_tag: _build_image(client, tb, t))
                finally:
                    done.set(); hb.cancel()

                image = image_tag
                await job.log(LogLevel.success, f"Imagen construida: {image_tag}")
            else:
                image = svc.get("image")
                if not image:
                    raise ValueError(f"Servicio '{svc_name}': no se especificó image ni build")
                await _pull_with_progress(client, image, job)

            # networks for this service
            svc_nets = svc.get("networks") or []
            if isinstance(svc_nets, dict):
                svc_nets = list(svc_nets.keys())
            primary_net = svc_nets[0] if svc_nets else None

            # labels
            labels = _parse_labels(svc.get("labels"))
            labels["com.uverse.compose.deploy"] = deploy_name
            labels["com.uverse.compose.service"] = svc_name

            restart_val = svc.get("restart", "unless-stopped")

            kwargs: dict[str, Any] = dict(
                image=image,
                name=container_name,
                detach=True,
                environment=_parse_env(svc.get("environment")),
                ports=_parse_ports(svc.get("ports")),
                volumes=[str(v) for v in (svc.get("volumes") or [])],
                restart_policy={"Name": restart_val},
                labels=labels,
            )
            if primary_net:
                kwargs["network"] = primary_net
            if svc.get("command") is not None:
                kwargs["command"] = svc["command"]
            if svc.get("entrypoint") is not None:
                kwargs["entrypoint"] = svc["entrypoint"]
            if svc.get("hostname"):
                kwargs["hostname"] = svc["hostname"]
            if svc.get("privileged"):
                kwargs["privileged"] = True
            if svc.get("cap_add"):
                kwargs["cap_add"] = svc["cap_add"]

            await job.log(LogLevel.info, f"Iniciando contenedor: {container_name}")
            container = await loop.run_in_executor(
                None, lambda kw=kwargs: client.containers.run(**kw)
            )
            deployed.append(container_name)

            # connect additional networks
            for net in svc_nets[1:]:
                try:
                    n = client.networks.get(net)
                    await loop.run_in_executor(None, lambda nw=n, c=container: nw.connect(c))
                except Exception as exc:
                    await job.log(LogLevel.warning, f"No se pudo conectar {container_name} a {net}: {exc}")

            # quick health check: is the container still running after 3s?
            await asyncio.sleep(3)
            try:
                c = client.containers.get(container_name)
                c.reload()
                if c.status not in ("running", "created"):
                    logs_tail = c.logs(tail=20).decode(errors="replace")
                    raise RuntimeError(
                        f"El contenedor '{container_name}' terminó con estado '{c.status}'.\n"
                        f"Últimas líneas de log:\n{logs_tail}"
                    )
            except RuntimeError:
                raise
            except Exception:
                pass  # container not found or other transient error — keep going

            await job.log(LogLevel.success, f"✓ {svc_name} en marcha")

        return deployed

    except Exception as exc:
        await _rollback()
        raise


async def run_dockerfile_deploy(
    job: Job,
    dockerfile_content: str,
    container_name: str,
    image_tag: str,
    ports: dict,
    environment: list[str],
    restart: str,
    deploy_name: str,
) -> list[str]:
    """Build and run a single container from a raw Dockerfile. Rolls back on error."""
    client = get_docker()
    loop = asyncio.get_event_loop()
    _container_created = False

    try:
        await job.set_progress(10, "Construyendo imagen…")
        tar_bytes = _make_dockerfile_tar(dockerfile_content)

        done = asyncio.Event()
        async def _hb():
            elapsed = 0
            while not done.is_set():
                await asyncio.sleep(8); elapsed += 8
                if not done.is_set():
                    await job.log(LogLevel.info, f"  … construyendo {image_tag} ({elapsed}s)")
        hb = asyncio.create_task(_hb())
        try:
            await loop.run_in_executor(None, lambda: _build_image(client, tar_bytes, image_tag))
        finally:
            done.set(); hb.cancel()
        await job.log(LogLevel.success, f"Imagen construida: {image_tag}")

        # remove existing container
        try:
            old = client.containers.get(container_name)
            await job.log(LogLevel.info, f"Eliminando contenedor existente: {container_name}")
            await loop.run_in_executor(None, lambda c=old: c.remove(force=True))
        except Exception:
            pass

        await job.set_progress(80, f"Iniciando {container_name}…")
        labels = {
            "com.uverse.compose.deploy": deploy_name,
            "com.uverse.compose.service": container_name,
        }
        await loop.run_in_executor(None, lambda: client.containers.run(
            image=image_tag,
            name=container_name,
            detach=True,
            environment=environment,
            ports=ports,
            restart_policy={"Name": restart},
            labels=labels,
        ))
        _container_created = True

        # quick health check
        await asyncio.sleep(3)
        try:
            c = client.containers.get(container_name)
            c.reload()
            if c.status not in ("running", "created"):
                logs_tail = c.logs(tail=20).decode(errors="replace")
                raise RuntimeError(
                    f"El contenedor '{container_name}' terminó con estado '{c.status}'.\n"
                    f"Últimas líneas de log:\n{logs_tail}"
                )
        except RuntimeError:
            raise
        except Exception:
            pass

        await job.log(LogLevel.success, f"✓ {container_name} en marcha")
        return [container_name]

    except Exception as exc:
        await job.log(LogLevel.warning, "⚠️  Deploy fallido — rollback…")
        if _container_created:
            try:
                client.containers.get(container_name).remove(force=True)
                await job.log(LogLevel.info, f"  Eliminado: {container_name}")
            except Exception:
                pass
        # Note: we keep the built image — it might be useful for debugging
        raise
