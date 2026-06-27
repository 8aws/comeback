"""Production-ready Traefik reverse proxy template.

Deploys Traefik with:
  - HTTPS entrypoint with automatic Let's Encrypt certificates
  - HTTP → HTTPS redirect
  - Docker provider (autodiscovery via labels)
  - File provider for static routing rules
  - Dashboard (optional, secured by basicauth)
  - Persistent certificate storage (acme.json)
  - External network for service discovery
  - Access logs
"""
from __future__ import annotations

from .base import TemplateField
from .official import SingleContainerTemplate
from ..models import LogLevel


class TraefikTemplate(SingleContainerTemplate):
    id = "traefik"
    name = "Traefik (producción)"
    description = (
        "Reverse proxy con HTTPS automático (Let's Encrypt), "
        "Docker autodiscovery y provider file para routing estático. "
        "Listo para producción y compatible con Keepalived HA."
    )
    version = "1.0"
    icon = "🔀"
    services = ["traefik"]
    image = "traefik:v3.4"
    container_name = "traefik"
    fields = [
        TemplateField(key="acme_email", label="Email para Let's Encrypt", type="text",
                      hint="Recibirás avisos de expiración de certificados",
                      placeholder="admin@tudominio.com"),
        TemplateField(key="dashboard_domain", label="Dominio del dashboard", type="domain",
                      required=False,
                      hint="Vacío = dashboard desactivado. Ej: traefik.tudominio.com",
                      placeholder="traefik.tudominio.com"),
        TemplateField(key="dashboard_user", label="Usuario dashboard", type="text",
                      default="admin", required=False,
                      hint="Solo si activas el dashboard"),
        TemplateField(key="dashboard_password", label="Contraseña dashboard", type="password",
                      required=False,
                      hint="Solo si activas el dashboard"),
        TemplateField(key="data_path", label="Ruta de datos en el host", type="path",
                      default="/share/Container/traefik",
                      hint="Certificados, configuración dinámica y logs"),
        TemplateField(key="tz", label="Zona horaria", type="text",
                      default="Europe/Madrid", required=False),
    ]

    def _data(self, cfg: dict) -> str:
        return (cfg.get("data_path") or "/share/Container/traefik").rstrip("/")

    def host_dirs(self, cfg):
        d = self._data(cfg)
        return [d, f"{d}/dynamic", f"{d}/logs"]

    def host_files(self, cfg):
        d = self._data(cfg)
        files = {}

        dashboard_enabled = bool((cfg.get("dashboard_domain") or "").strip())
        files[f"{d}/traefik.yml"] = _static_config(
            acme_email=cfg["acme_email"],
            dashboard=dashboard_enabled,
        )

        files[f"{d}/dynamic/default.yml"] = _dynamic_config(cfg)

        return files

    async def post_setup(self, job, cfg):
        import os
        from pathlib import Path as P
        from ..docker_client import get_docker

        d = self._data(cfg)
        acme = P(f"/host{d}/acme.json")
        if not acme.exists():
            acme.write_text("")
        os.chmod(str(acme), 0o600)
        await job.log(LogLevel.info, f"Archivo: {d}/acme.json (permisos 600)")

        client = get_docker()
        try:
            client.networks.get("traefik-public")
            await job.log(LogLevel.info, "Red traefik-public ya existe")
        except Exception:
            client.networks.create("traefik-public", driver="bridge",
                                   attachable=True,
                                   labels={"com.uverse.template": "traefik"})
            await job.log(LogLevel.success, "Red traefik-public creada")

    def build_run_kwargs(self, cfg):
        d = self._data(cfg)
        kwargs = {
            "environment": {"TZ": cfg.get("tz") or "Europe/Madrid"},
            "ports": {
                "80/tcp": 80,
                "443/tcp": 443,
            },
            "volumes": [
                "/var/run/docker.sock:/var/run/docker.sock:ro",
                f"{d}/traefik.yml:/etc/traefik/traefik.yml:ro",
                f"{d}/acme.json:/etc/traefik/acme.json",
                f"{d}/dynamic:/etc/traefik/dynamic:ro",
                f"{d}/logs:/var/log/traefik",
            ],
            "labels": _traefik_labels(cfg),
            "network": "traefik-public",
        }
        return kwargs

    def success_notes(self, cfg):
        notes = [
            "🔀 Traefik escuchando en :80 (HTTP→HTTPS) y :443 (HTTPS)",
            "🔒 Certificados Let's Encrypt automáticos (almacenados en acme.json)",
            f"📁 Añade reglas de routing en {self._data(cfg)}/dynamic/",
        ]
        domain = (cfg.get("dashboard_domain") or "").strip()
        if domain:
            notes.append(f"📊 Dashboard en https://{domain}")
        else:
            notes.append("📊 Dashboard desactivado — configura un dominio para activarlo")
        notes.append(
            "🔗 Para exponer un servicio, añade labels al contenedor:\n"
            "     traefik.enable=true\n"
            "     traefik.http.routers.NOMBRE.rule=Host(`dominio.com`)\n"
            "     traefik.http.routers.NOMBRE.tls.certresolver=letsencrypt"
        )
        return notes


def _static_config(acme_email: str, dashboard: bool) -> str:
    return f"""\
# Traefik static configuration — generated by comeback
api:
  dashboard: {str(dashboard).lower()}

entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: ":443"
    http:
      tls:
        certResolver: letsencrypt

certificatesResolvers:
  letsencrypt:
    acme:
      email: "{acme_email}"
      storage: /etc/traefik/acme.json
      httpChallenge:
        entryPoint: web

providers:
  docker:
    endpoint: "unix:///var/run/docker.sock"
    exposedByDefault: false
    network: traefik-public
  file:
    directory: /etc/traefik/dynamic
    watch: true

log:
  level: INFO
  filePath: /var/log/traefik/traefik.log

accessLog:
  filePath: /var/log/traefik/access.log
  bufferingSize: 100
"""


def _dynamic_config(cfg: dict) -> str:
    return """\
# Dynamic configuration — add your routers/middlewares/services here.
# Traefik watches this directory and reloads automatically.
#
# Example — expose a service:
#   http:
#     routers:
#       my-app:
#         rule: "Host(`app.example.com`)"
#         service: my-app
#         tls:
#           certResolver: letsencrypt
#     services:
#       my-app:
#         loadBalancer:
#           servers:
#             - url: "http://my-app:8080"
"""


def _traefik_labels(cfg: dict) -> dict[str, str]:
    labels = {"traefik.enable": "true"}
    domain = (cfg.get("dashboard_domain") or "").strip()
    if not domain:
        labels["traefik.enable"] = "false"
        return labels

    labels.update({
        "traefik.http.routers.traefik-dashboard.rule": f"Host(`{domain}`)",
        "traefik.http.routers.traefik-dashboard.tls.certresolver": "letsencrypt",
        "traefik.http.routers.traefik-dashboard.service": "api@internal",
    })

    user = (cfg.get("dashboard_user") or "").strip()
    password = (cfg.get("dashboard_password") or "").strip()
    if user and password:
        import hashlib
        import base64
        # Apache MD5 (apr1) is not trivially available, use bcrypt via htpasswd-style
        # For simplicity we use SHA1 which Traefik basicauth accepts ({SHA}base64)
        sha = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
        htpasswd = f"{user}:{{SHA}}{sha}"
        labels.update({
            "traefik.http.routers.traefik-dashboard.middlewares": "dashboard-auth",
            "traefik.http.middlewares.dashboard-auth.basicauth.users": htpasswd,
        })

    return labels
