"""Official vendor image templates — single-container deploys.

All images here are published by the software vendor itself (Plex Inc,
Portainer, Grafana Labs, Nextcloud GmbH), not community re-packs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from .base import BaseTemplate, TemplateField
from ..docker_client import get_docker
from ..job_manager import Job
from ..models import JobStatus, LogLevel


class SingleContainerTemplate(BaseTemplate):
    """Shared deploy flow: pull with progress → dirs → run → health → rollback."""

    image: str = ""
    container_name: str = ""

    def build_run_kwargs(self, cfg: dict) -> dict:
        raise NotImplementedError

    def host_dirs(self, cfg: dict) -> list[str]:
        """Host paths that must exist before the container starts."""
        return []

    def success_notes(self, cfg: dict) -> list[str]:
        return []

    async def deploy(self, job: Job, config: dict[str, str]) -> None:
        from ..deploy.compose import _pull_with_progress

        job.started_at = datetime.utcnow()
        job.status = JobStatus.running
        client = get_docker()
        loop = asyncio.get_event_loop()
        created = False

        try:
            await job.set_progress(5, "Preparando directorios…")
            for d in self.host_dirs(config):
                p = Path(f"/host{d}")
                await loop.run_in_executor(None, lambda p=p: p.mkdir(parents=True, exist_ok=True))
                await job.log(LogLevel.info, f"Directorio: {d}")

            await job.set_progress(10, f"Descargando {self.image}…")
            await _pull_with_progress(client, self.image, job)

            # Remove previous instance if present
            try:
                old = await loop.run_in_executor(
                    None, lambda: client.containers.get(self.container_name))
                await job.log(LogLevel.warning, f"Eliminando contenedor existente: {self.container_name}")
                await loop.run_in_executor(None, lambda: old.remove(force=True))
            except Exception:
                pass

            await job.set_progress(60, f"Arrancando {self.container_name}…")
            kwargs = self.build_run_kwargs(config)
            kwargs.setdefault("name", self.container_name)
            kwargs.setdefault("detach", True)
            kwargs.setdefault("restart_policy", {"Name": "unless-stopped"})
            kwargs.setdefault("labels", {})
            kwargs["labels"]["com.uverse.template"] = self.id
            await loop.run_in_executor(None, lambda: client.containers.run(self.image, **kwargs))
            created = True
            await job.log(LogLevel.success, f"{self.container_name} creado")

            # Quick health check
            await job.set_progress(85, "Comprobando arranque…")
            await asyncio.sleep(6)
            c = await loop.run_in_executor(
                None, lambda: client.containers.get(self.container_name))
            if c.status not in ("running", "created"):
                logs = await loop.run_in_executor(
                    None, lambda: c.logs(tail=15).decode(errors="replace"))
                await job.log(LogLevel.error, f"{self.container_name} no arranca ({c.status})", detail=logs)
                raise RuntimeError(f"El contenedor no arranca (estado: {c.status})")

            await job.set_progress(100, "Deploy completado")
            await job.log(LogLevel.success, f"─── {self.name} desplegado correctamente ───")
            for note in self.success_notes(config):
                await job.log(LogLevel.info, note)
            await job.finish(JobStatus.success, {
                "template": self.id, "containers": [self.container_name]})

        except Exception as e:
            if created:
                try:
                    broken = client.containers.get(self.container_name)
                    broken.remove(force=True)
                    await job.log(LogLevel.warning, f"Rollback: {self.container_name} eliminado")
                except Exception:
                    pass
            await job.log(LogLevel.error, f"Deploy fallido: {e}")
            await job.finish(JobStatus.failed, {"error": str(e)})


def _common_fields(default_data: str) -> list[TemplateField]:
    return [
        TemplateField(key="data_path", label="Ruta de datos en el host", type="path",
                      default=default_data, required=False,
                      hint="Configuración persistente del servicio"),
        TemplateField(key="tz", label="Zona horaria", type="text",
                      default="Europe/Madrid", required=False),
    ]


class PlexTemplate(SingleContainerTemplate):
    id = "plex"
    name = "Plex Media Server"
    description = "Servidor multimedia oficial de Plex Inc (plexinc/pms-docker). Red del host para descubrimiento DLNA/chromecast."
    version = "1.0"
    icon = "🎬"
    services = ["plex"]
    image = "plexinc/pms-docker:latest"
    container_name = "plex"
    fields = [
        TemplateField(key="claim_token", label="Claim token", type="text", required=False,
                      hint="Token de https://plex.tv/claim (caduca en 4 min, opcional)",
                      placeholder="claim-xxxxxxxxxxxx"),
        TemplateField(key="media_path", label="Ruta de medios", type="path",
                      default="/share/Multimedia",
                      hint="Biblioteca de películas/series/música (solo lectura)"),
        *_common_fields("/share/Container/plex"),
    ]

    def host_dirs(self, cfg):
        data = (cfg.get("data_path") or "/share/Container/plex").rstrip("/")
        return [f"{data}/config", f"{data}/transcode"]

    def build_run_kwargs(self, cfg):
        data = (cfg.get("data_path") or "/share/Container/plex").rstrip("/")
        env = {
            "TZ": cfg.get("tz") or "Europe/Madrid",
            "PLEX_UID": "0", "PLEX_GID": "0",
        }
        if cfg.get("claim_token"):
            env["PLEX_CLAIM"] = cfg["claim_token"]
        return {
            "environment": env,
            # host network: required for DLNA/GDM discovery and Chromecast
            "network_mode": "host",
            "volumes": [
                f"{data}/config:/config",
                f"{data}/transcode:/transcode",
                f"{cfg['media_path'].rstrip('/')}:/data:ro",
            ],
        }

    def success_notes(self, cfg):
        return [
            "🎬 Plex usa la red del host — accede en http://IP-del-servidor:32400/web",
            "ℹ️  Sin claim token el servidor aparece como no reclamado; reclámalo desde la misma red local",
        ]


class PortainerTemplate(SingleContainerTemplate):
    id = "portainer"
    name = "Portainer CE"
    description = "Gestión visual de Docker, imagen oficial de Portainer (portainer/portainer-ce)."
    version = "1.0"
    icon = "🐋"
    services = ["portainer"]
    image = "portainer/portainer-ce:lts"
    container_name = "portainer"
    fields = [
        TemplateField(key="host_port", label="Puerto HTTPS en el host", type="text",
                      default="9443", required=False,
                      hint="Vacío = sin puerto (acceso solo por reverse proxy a portainer:9443)"),
        *_common_fields("/share/Container/portainer"),
    ]

    def host_dirs(self, cfg):
        return [(cfg.get("data_path") or "/share/Container/portainer").rstrip("/")]

    def build_run_kwargs(self, cfg):
        data = (cfg.get("data_path") or "/share/Container/portainer").rstrip("/")
        kwargs = {
            "environment": {"TZ": cfg.get("tz") or "Europe/Madrid"},
            "volumes": [
                "/var/run/docker.sock:/var/run/docker.sock",
                f"{data}:/data",
            ],
        }
        if (cfg.get("host_port") or "").strip():
            kwargs["ports"] = {"9443/tcp": int(cfg["host_port"])}
        return kwargs

    def success_notes(self, cfg):
        port = (cfg.get("host_port") or "").strip()
        return [f"🐋 Portainer accesible en https://IP-del-servidor:{port}" if port
                else "🐋 Apunta tu reverse proxy a portainer:9443 (HTTPS interno)"]


class GrafanaTemplate(SingleContainerTemplate):
    id = "grafana"
    name = "Grafana"
    description = "Dashboards y visualización, imagen oficial de Grafana Labs (grafana/grafana)."
    version = "1.0"
    icon = "📈"
    services = ["grafana"]
    image = "grafana/grafana:latest"
    container_name = "grafana"
    fields = [
        TemplateField(key="admin_password", label="Contraseña admin", type="password",
                      hint="Usuario inicial: admin"),
        TemplateField(key="host_port", label="Puerto en el host", type="text",
                      default="3000", required=False,
                      hint="Vacío = sin puerto (reverse proxy a grafana:3000)"),
        *_common_fields("/share/Container/grafana"),
    ]

    def host_dirs(self, cfg):
        return [(cfg.get("data_path") or "/share/Container/grafana").rstrip("/")]

    def build_run_kwargs(self, cfg):
        data = (cfg.get("data_path") or "/share/Container/grafana").rstrip("/")
        kwargs = {
            "environment": {
                "TZ": cfg.get("tz") or "Europe/Madrid",
                "GF_SECURITY_ADMIN_PASSWORD": cfg["admin_password"],
            },
            "user": "0",   # NAS bind mounts are usually root-owned
            "volumes": [f"{data}:/var/lib/grafana"],
        }
        if (cfg.get("host_port") or "").strip():
            kwargs["ports"] = {"3000/tcp": int(cfg["host_port"])}
        return kwargs

    def success_notes(self, cfg):
        port = (cfg.get("host_port") or "").strip()
        return [f"📈 Grafana en http://IP-del-servidor:{port} (admin / la contraseña indicada)" if port
                else "📈 Apunta tu reverse proxy a grafana:3000"]


class NextcloudTemplate(SingleContainerTemplate):
    id = "nextcloud"
    name = "Nextcloud"
    description = "Nube de archivos personal, imagen oficial (nextcloud). SQLite — para uso doméstico."
    version = "1.0"
    icon = "☁️"
    services = ["nextcloud"]
    image = "nextcloud:latest"
    container_name = "nextcloud"
    fields = [
        TemplateField(key="admin_user", label="Usuario admin", type="text", default="admin"),
        TemplateField(key="admin_password", label="Contraseña admin", type="password"),
        TemplateField(key="domain", label="Dominio de acceso", type="domain", required=False,
                      hint="Se añade a trusted_domains (ej. nube.tudominio.com)",
                      placeholder="nube.tudominio.com"),
        TemplateField(key="host_port", label="Puerto en el host", type="text",
                      default="8081", required=False,
                      hint="Vacío = sin puerto (reverse proxy a nextcloud:80)"),
        *_common_fields("/share/Container/nextcloud"),
    ]

    def host_dirs(self, cfg):
        return [(cfg.get("data_path") or "/share/Container/nextcloud").rstrip("/")]

    def build_run_kwargs(self, cfg):
        data = (cfg.get("data_path") or "/share/Container/nextcloud").rstrip("/")
        env = {
            "TZ": cfg.get("tz") or "Europe/Madrid",
            "NEXTCLOUD_ADMIN_USER": cfg.get("admin_user") or "admin",
            "NEXTCLOUD_ADMIN_PASSWORD": cfg["admin_password"],
            "SQLITE_DATABASE": "nextcloud",
        }
        domains = ["localhost"]
        if (cfg.get("domain") or "").strip():
            domains.append(cfg["domain"].strip())
        env["NEXTCLOUD_TRUSTED_DOMAINS"] = " ".join(domains)
        kwargs = {
            "environment": env,
            "volumes": [f"{data}:/var/www/html"],
        }
        if (cfg.get("host_port") or "").strip():
            kwargs["ports"] = {"80/tcp": int(cfg["host_port"])}
        return kwargs

    def success_notes(self, cfg):
        port = (cfg.get("host_port") or "").strip()
        return [f"☁️ Nextcloud en http://IP-del-servidor:{port}" if port
                else "☁️ Apunta tu reverse proxy a nextcloud:80"]
