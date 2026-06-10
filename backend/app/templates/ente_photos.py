"""Ente Photos self-hosted stack deploy template.

Services deployed:
  ente-postgres  — postgres:16-alpine
  ente-minio     — minio/minio:latest  (port 3200 S3, 3201 console)
  ente-museum    — ghcr.io/ente-io/server:latest  (port 8080)
  ente-web       — ghcr.io/ente-io/web:latest  (port 3000)

All containers share the Docker network  ente-network.
MinIO bucket b2-eu-cen is created via a temporary minio/mc container.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import base64
from datetime import datetime
from pathlib import Path

from .base import BaseTemplate, TemplateField
from ..docker_client import get_docker
from ..job_manager import Job
from ..models import JobStatus, LogLevel


def _b64(n_bytes: int) -> str:
    return base64.b64encode(secrets.token_bytes(n_bytes)).decode()


def _urlsafe_b64(n_bytes: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(n_bytes)).decode()


def _hex(n_bytes: int) -> str:
    """Pure hex string (0-9, a-f) — valid in any base64 variant, no special chars."""
    return secrets.token_hex(n_bytes)


def _url_safe(n_bytes: int) -> str:
    return secrets.token_urlsafe(n_bytes)


NETWORK = "ente-network"
POSTGRES_IMAGE = "postgres:16-alpine"
MINIO_IMAGE = "minio/minio:latest"
MUSEUM_IMAGE = "ghcr.io/ente-io/server:latest"
WEB_IMAGE = "ghcr.io/ente-io/web:latest"
MC_IMAGE = "minio/mc:latest"


class EntePhotosTemplate(BaseTemplate):
    id = "ente-photos"
    name = "Ente Photos"
    description = "Stack de fotos E2E cifrado. Incluye servidor museo, PostgreSQL, MinIO y app web."
    version = "1.0"
    icon = "📷"
    services = ["ente-postgres", "ente-minio", "ente-museum", "ente-web"]

    fields = [
        TemplateField(
            key="pg_password",
            label="PostgreSQL password",
            type="password",
            hint="Contraseña de la base de datos Ente",
            placeholder="mi_password_seguro",
        ),
        TemplateField(
            key="minio_password",
            label="MinIO root password",
            type="password",
            hint="Mínimo 8 caracteres",
            placeholder="minio_password_seguro",
        ),
        TemplateField(
            key="api_domain",
            label="Dominio API (museum)",
            type="domain",
            hint="Subdominio que Cosmos usará para el servidor museo",
            placeholder="ente-api.tudominio.com",
        ),
        TemplateField(
            key="web_domain",
            label="Dominio Web",
            type="domain",
            hint="Subdominio para la interfaz web",
            placeholder="photos.tudominio.com",
        ),
        TemplateField(
            key="data_path",
            label="Ruta de datos en el host",
            type="path",
            default="/share/Container/ente/data",
            hint="Directorio raíz donde se guardarán postgres y minio",
            required=False,
        ),
    ]

    # ─── helpers ──────────────────────────────────────────────────────────

    async def _pull_async(self, image: str, job: Job):
        """Pull with real-time per-layer progress streamed to the job log."""
        import time as _time
        await job.log(LogLevel.info, f"Pulling {image}…")
        client = get_docker()

        # Already cached?
        try:
            client.images.get(image)
            await job.log(LogLevel.success, f"Imagen en caché: {image}")
            return
        except Exception:
            pass

        loop = asyncio.get_event_loop()

        def _stream():
            layers: dict[str, tuple[int, int]] = {}
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
                elif status in ("Pull complete", "Already exists") and lid:
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

    def _ensure_network(self) -> bool:
        """Returns True if the network was newly created."""
        client = get_docker()
        existing = {n.name for n in client.networks.list()}
        if NETWORK not in existing:
            client.networks.create(NETWORK, driver="bridge", attachable=True)
            return True
        return False

    def _ensure_dirs(self, base: str):
        for sub in ("postgres", "minio", "museum"):
            p = Path(f"/host{base}/{sub}")
            if p.exists() and not p.is_dir():
                p.unlink()          # elimina si es fichero en vez de directorio
            p.mkdir(parents=True, exist_ok=True)

    def _remove_if_exists(self, name: str):
        client = get_docker()
        try:
            c = client.containers.get(name)
            c.remove(force=True)
        except Exception:
            pass

    async def _wait_healthy_async(self, container_name: str, check_cmd: list[str],
                                   label: str, job: Job, max_wait: int = 90) -> bool:
        """
        Async health-check loop.
        - exec_run corre en executor → nunca bloquea el event loop.
        - Comprueba si el contenedor crasheó → error inmediato con logs.
        - Muestra estado real cada 5s para que el usuario sepa que avanza.
        """
        await job.log(LogLevel.info, f"Esperando que {label} esté listo…")
        loop = asyncio.get_event_loop()
        client = get_docker()

        def _check():
            """Devuelve (ok: bool, status: str, crashed: bool, tail_logs: str)"""
            try:
                c = client.containers.get(container_name)
                c.reload()
                status = c.status
                if status in ("exited", "dead", "removing"):
                    tail = c.logs(tail=30).decode(errors="replace").strip()
                    return False, status, True, tail
                r = c.exec_run(check_cmd, stdout=False, stderr=False)
                return r.exit_code == 0, status, False, ""
            except Exception as exc:
                return False, "unknown", False, str(exc)

        for i in range(max_wait):
            await asyncio.sleep(1)
            ok, status, crashed, tail_logs = await loop.run_in_executor(None, _check)

            if crashed:
                await job.log(LogLevel.error,
                    f"{label} terminó inesperadamente (estado: {status})")
                if tail_logs:
                    for line in tail_logs.splitlines()[-15:]:
                        await job.log(LogLevel.error, f"  | {line}")
                raise RuntimeError(f"{label} crasheó (estado: {status}) — ver logs arriba")

            if ok:
                await job.log(LogLevel.success, f"{label} listo ✓  ({i+1}s)")
                return True

            if (i + 1) % 5 == 0:
                await job.log(LogLevel.info,
                    f"  … {label} iniciando ({i+1}s) — estado Docker: {status}")

        # Timeout — captura los últimos logs antes de continuar
        def _tail():
            try:
                return client.containers.get(container_name).logs(tail=10).decode(errors="replace").strip()
            except Exception:
                return ""
        tail = await loop.run_in_executor(None, _tail)
        await job.log(LogLevel.warning,
            f"{label} no respondió en {max_wait}s — continuando (últimos logs):")
        for line in tail.splitlines():
            await job.log(LogLevel.warning, f"  | {line}")
        return False

    # ─── deploy steps ─────────────────────────────────────────────────────

    def _start_postgres(self, pg_pass: str, data_path: str):
        client = get_docker()
        self._remove_if_exists("ente-postgres")
        client.containers.run(
            POSTGRES_IMAGE,
            name="ente-postgres",
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=NETWORK,
            environment={
                "POSTGRES_USER": "ente",
                "POSTGRES_PASSWORD": pg_pass,
                "POSTGRES_DB": "ente",
                "TZ": "Europe/Madrid",
            },
            volumes=[f"{data_path}/postgres:/var/lib/postgresql/data"],
            labels={
                "com.uverse.template": "ente-photos",
                "com.uverse.service": "ente-postgres",
            },
        )

    def _start_minio(self, minio_pass: str, data_path: str):
        client = get_docker()
        self._remove_if_exists("ente-minio")
        client.containers.run(
            MINIO_IMAGE,
            command="server /data --address :3200 --console-address :3201",
            name="ente-minio",
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=NETWORK,
            environment={
                "MINIO_ROOT_USER": "ente",
                "MINIO_ROOT_PASSWORD": minio_pass,
                "TZ": "Europe/Madrid",
            },
            volumes=[f"{data_path}/minio:/data"],
            # No host port bindings — Cosmos Cloud accede por red interna
            labels={
                "com.uverse.template": "ente-photos",
                "com.uverse.service": "ente-minio",
            },
        )

    def _setup_minio_bucket(self, minio_pass: str):
        """Create b2-eu-cen bucket using a temporary mc container.

        minio/mc uses mc as entrypoint — override to sh so we can chain commands.
        The mc binary is installed inside the image at /usr/bin/mc.
        """
        client = get_docker()
        try:
            client.containers.run(
                MC_IMAGE,
                entrypoint=["sh"],
                command=[
                    "-c",
                    "mc alias set myminio http://ente-minio:3200 ente \"$MINIO_PASS\" "
                    "&& mc mb --ignore-existing myminio/b2-eu-cen "
                    "&& mc anonymous set download myminio/b2-eu-cen",
                ],
                name="ente-mc-setup",
                remove=True,
                network=NETWORK,
                environment={"MINIO_PASS": minio_pass},
            )
        except Exception as e:
            raise RuntimeError(f"MinIO bucket setup failed: {e}")

    def _generate_museum_yaml(self, cfg: dict) -> str:
        enc_key = _b64(32)
        hash_key = _b64(64)
        # jwt-secret: museum rechaza +//-// (std b64) y -/_ (url-safe b64).
        # Hex puro (0-9,a-f) es válido en cualquier variante de base64 y no contiene chars especiales.
        jwt_secret = _hex(22)   # 44 hex chars — longitud múltiplo de 4, 176 bits de entropía

        return f"""# Auto-generated by uverse comeback — Ente Photos template
db:
  host: ente-postgres
  port: 5432
  name: ente
  user: ente
  password: "{cfg['pg_password']}"

key:
  encryption: "{enc_key}"
  hash: "{hash_key}"

jwt:
  secret: "{jwt_secret}"

s3:
  are_local_buckets: true
  b2-eu-cen:
    key: "ente"
    secret: "{cfg['minio_password']}"
    endpoint: "http://ente-minio:3200"
    region: "eu-central-2"
    bucket: "b2-eu-cen"
"""

    def _write_museum_config(self, yaml_content: str, data_path: str) -> str:
        config_dir = Path(f"/host{data_path}/museum")
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "credentials.yaml"
        config_path.write_text(yaml_content)
        return f"{data_path}/museum/credentials.yaml"

    def _start_museum(self, cfg: dict, data_path: str):
        client = get_docker()
        self._remove_if_exists("ente-museum")
        client.containers.run(
            MUSEUM_IMAGE,
            name="ente-museum",
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=NETWORK,
            volumes=[
                # /museum es el binario dentro de la imagen — montamos el fichero directamente en /
                f"{data_path}/museum/credentials.yaml:/credentials.yaml:ro",
            ],
            # No host port — Cosmos apunta a ente-museum:8080 por red interna
            labels={
                "com.uverse.template": "ente-photos",
                "com.uverse.service": "ente-museum",
            },
        )

    def _start_web(self, api_domain: str, web_domain: str):
        client = get_docker()
        self._remove_if_exists("ente-web")
        client.containers.run(
            WEB_IMAGE,
            name="ente-web",
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=NETWORK,
            environment={
                "NEXT_PUBLIC_ENTE_ENDPOINT": f"https://{api_domain}",
                "TZ": "Europe/Madrid",
            },
            # No host port — Cosmos apunta a ente-web:3000 por red interna
            labels={
                "com.uverse.template": "ente-photos",
                "com.uverse.service": "ente-web",
            },
        )

    # ─── rollback ─────────────────────────────────────────────────────────

    async def _rollback(self, job: Job, containers: list[str], remove_network: bool):
        await job.log(LogLevel.warning, "⚠️  Deploy fallido — iniciando rollback…")
        client = get_docker()
        for cname in reversed(containers):
            try:
                self._remove_if_exists(cname)
                await job.log(LogLevel.info, f"  Eliminado contenedor: {cname}")
            except Exception as exc:
                await job.log(LogLevel.warning, f"  No se pudo eliminar {cname}: {exc}")
        if remove_network:
            try:
                client.networks.get(NETWORK).remove()
                await job.log(LogLevel.info, f"  Eliminada red: {NETWORK}")
            except Exception:
                pass
        await job.log(LogLevel.info, "Rollback completado")

    # ─── main entry point ─────────────────────────────────────────────────

    async def deploy(self, job: Job, config: dict[str, str]) -> None:
        job.started_at = datetime.utcnow()
        job.status = JobStatus.running

        pg_pass    = config["pg_password"]
        minio_pass = config["minio_password"]
        api_domain = config["api_domain"]
        web_domain = config.get("web_domain", "")
        data_path  = config.get("data_path", "/share/Container/ente/data").rstrip("/")

        loop = asyncio.get_event_loop()

        # Track resources for rollback
        _network_created   = False
        _containers_started: list[str] = []

        try:
            # 1 — Network
            await job.set_progress(5, "Creando red Docker…")
            await job.log(LogLevel.info, f"Red Docker: {NETWORK}")
            _network_created = await loop.run_in_executor(None, self._ensure_network)
            status = "creada" if _network_created else "ya existía"
            await job.log(LogLevel.success, f"Red {NETWORK} {status}")

            # 2 — Directories
            await job.set_progress(8, "Creando directorios…")
            await job.log(LogLevel.info, f"Directorios en {data_path}")
            await loop.run_in_executor(None, self._ensure_dirs, data_path)
            await job.log(LogLevel.success, "Directorios listos")

            # 3 — Pull images (parallelised) with heartbeat
            await job.set_progress(10, "Descargando imágenes (puede tardar varios minutos)…")
            await asyncio.gather(
                self._pull_async(POSTGRES_IMAGE, job),
                self._pull_async(MINIO_IMAGE, job),
                self._pull_async(MUSEUM_IMAGE, job),
                self._pull_async(WEB_IMAGE, job),
                self._pull_async(MC_IMAGE, job),
            )

            # 4 — PostgreSQL
            await job.set_progress(40, "Iniciando PostgreSQL…")
            await job.log(LogLevel.info, "Arrancando ente-postgres")
            await loop.run_in_executor(None, self._start_postgres, pg_pass, data_path)
            _containers_started.append("ente-postgres")
            await job.log(LogLevel.info, f"  ente-postgres creado — iniciando health check")
            await self._wait_healthy_async(
                "ente-postgres",
                ["pg_isready", "-U", "ente", "-d", "ente"],
                "PostgreSQL", job,
            )

            # 5 — MinIO
            await job.set_progress(52, "Iniciando MinIO…")
            await job.log(LogLevel.info, "Arrancando ente-minio")
            await loop.run_in_executor(None, self._start_minio, minio_pass, data_path)
            _containers_started.append("ente-minio")
            await job.log(LogLevel.info, f"  ente-minio creado — iniciando health check")
            await self._wait_healthy_async(
                "ente-minio",
                ["wget", "-q", "-O", "/dev/null", "http://localhost:3200/minio/health/live"],
                "MinIO", job,
            )

            # 6 — Bucket setup
            await job.set_progress(62, "Creando bucket MinIO b2-eu-cen…")
            await job.log(LogLevel.info, "Configurando bucket via mc")
            await loop.run_in_executor(None, self._setup_minio_bucket, minio_pass)
            await job.log(LogLevel.success, "Bucket b2-eu-cen creado")

            # 7 — Museum config
            await job.set_progress(68, "Generando configuración de museum…")
            yaml_content = self._generate_museum_yaml(config)
            await loop.run_in_executor(None, self._write_museum_config, yaml_content, data_path)
            await job.log(LogLevel.success, f"credentials.yaml escrito en {data_path}/museum/")

            # 8 — Museum
            await job.set_progress(72, "Iniciando museum (API server)…")
            await job.log(LogLevel.info, "Arrancando ente-museum")
            await loop.run_in_executor(None, self._start_museum, config, data_path)
            _containers_started.append("ente-museum")
            await job.log(LogLevel.info, f"  ente-museum creado — iniciando health check")
            await self._wait_healthy_async(
                "ente-museum",
                ["wget", "-q", "-O", "/dev/null", "http://localhost:8080/ping"],
                "Museum API", job,
            )

            # 9 — Web
            await job.set_progress(88, "Iniciando frontend web…")
            await job.log(LogLevel.info, "Arrancando ente-web")
            await loop.run_in_executor(None, self._start_web, api_domain, web_domain)
            _containers_started.append("ente-web")

            # Done
            await job.set_progress(100, "Deploy completado")
            await job.log(LogLevel.success, "─── Ente Photos desplegado correctamente ───")
            await job.log(LogLevel.info, f"🔌 En Cosmos apunta  {api_domain}  →  ente-museum:8080")
            if web_domain:
                await job.log(LogLevel.info, f"🌐 En Cosmos apunta  {web_domain}  →  ente-web:3000")
            await job.log(LogLevel.info, "🗄  MinIO console accesible en  ente-minio:3201  (desde la red Docker)")
            await job.log(LogLevel.info,
                "ℹ️  Sin puertos al host — Cosmos accede por red interna ente-network")
            await job.log(LogLevel.info,
                "💾 Se lanzará un backup automático de los contenedores en 15s…")

            await job.finish(JobStatus.success, {
                "template": self.id,
                "containers": self.services,
                "api_url": f"https://{api_domain}",
                "web_url": f"https://{web_domain}" if web_domain else None,
            })

        except Exception as e:
            await self._rollback(job, _containers_started, _network_created)
            await job.log(LogLevel.error, f"Deploy fallido: {e}")
            await job.finish(JobStatus.failed, {"error": str(e)})
            raise
