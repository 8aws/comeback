# 🐳 Comeback

Herramienta self-hosted de **backup, restauración y despliegue de contenedores Docker** con interfaz web. Pensada para NAS (QNAP, Synology…) y servidores domésticos, opcionalmente detrás de un reverse proxy como Cosmos Cloud.

## Características

- **Backup completo de contenedores**: configuración (`docker inspect`), volúmenes con nombre, bind mounts y dumps de bases de datos (MySQL/MariaDB, PostgreSQL, MongoDB, Redis) en un único archivo comprimido y verificado con SHA-256.
- **Restauración con un clic**: recrea redes, volúmenes y contenedores con su configuración original. Modo test con prefijo de nombre para restaurar en paralelo sin tocar el original.
- **Despliegue de stacks**: plantillas integradas (Ente Photos…), YAML de Docker Compose o Dockerfile inline, con progreso en tiempo real, rollback automático en caso de error y backup automático tras cada deploy exitoso.
- **Verificación de archivos**: comprueba integridad (checksum + manifest) sin restaurar.
- **Sin docker CLI**: todas las operaciones usan el SDK de Python sobre el socket de Docker.
- **Logs en tiempo real**: WebSocket con fallback a polling, compatible con proxies HTTPS.

## Instalación

### Opción A — Docker Compose (recomendada)

```yaml
services:
  comeback:
    image: espiralvex/comeback:latest
    container_name: uverse-comeback
    restart: unless-stopped
    ports:
      - "7731:7731"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /:/host
      - comeback_backups:/backups
    environment:
      - BACKUP_PATH=/backups
      - HOST_ROOT=/host
      - TZ=Europe/Madrid

volumes:
  comeback_backups:
    name: uverse-comeback-backups
```

```bash
docker compose up -d
```

### Opción B — Desde el código fuente (GitHub)

```bash
git clone https://github.com/8aws/comeback.git
cd comeback
docker compose up -d --build
```

El `docker-compose.yml` del repo monta `./backend/app` dentro del contenedor, así que los cambios de código se aplican con un simple `docker restart uverse-comeback` (sin rebuild).

### Acceso

Abre `http://tu-servidor:7731`. La documentación de la API está en `/api/docs`.

## Volúmenes requeridos

| Montaje | Propósito |
|---|---|
| `/var/run/docker.sock` | Control de Docker (obligatorio) |
| `/:/host` | Acceso a bind mounts del host para backup/restore (obligatorio para bind mounts) |
| `/backups` | Almacenamiento de archivos de backup |

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `BACKUP_PATH` | `/backups` | Ruta de almacenamiento de archivos |
| `HOST_ROOT` | `/host` | Punto de montaje del filesystem del host |
| `TZ` | `Europe/Madrid` | Zona horaria de los logs |

## ⚠️ Seguridad

Comeback **no incluye autenticación** y tiene acceso completo al socket de Docker y al filesystem del host. No lo expongas nunca directamente a internet: úsalo solo en red local o detrás de un reverse proxy con autenticación (Cosmos Cloud, Authelia, etc.).

## Licencia

[MIT](LICENSE)
