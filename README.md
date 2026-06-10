# 🐳 Comeback

Herramienta self-hosted de **backup, restauración y despliegue de contenedores Docker** con interfaz web. Pensada para NAS (QNAP, Synology…) y servidores domésticos, opcionalmente detrás de un reverse proxy como Cosmos Cloud.

## Características

- **Backup completo de contenedores**: configuración (`docker inspect`), volúmenes con nombre, bind mounts y dumps de bases de datos (MySQL/MariaDB, PostgreSQL, MongoDB, Redis) en un único archivo comprimido y verificado con SHA-256.
- **Restauración con un clic**: recrea redes, volúmenes y contenedores con su configuración original. Modo test con prefijo de nombre para restaurar en paralelo sin tocar el original.
- **Migraciones entre servidores**: descarga un backup, súbelo en otra instancia de comeback y restaura reasignando las rutas de bind mounts que difieran entre máquinas (p. ej. `/share/Container` de QNAP → `/DATA/AppData` de ZimaOS).
- **Despliegue de stacks**: plantillas integradas (Ente Photos…), YAML de Docker Compose o Dockerfile inline, con progreso en tiempo real, rollback automático en caso de error y backup automático tras cada deploy exitoso.
- **Actualizaciones de contenedores**: detecta imágenes nuevas en el registry (estilo Watchtower) y actualiza con un clic — backup previo opcional, recreación con la configuración original y rollback automático si la nueva versión no arranca.
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
      - AUTH_USERNAME=admin
      - AUTH_PASSWORD=cambia-esta-contraseña

volumes:
  comeback_backups:
    name: uverse-comeback-backups
```

```bash
docker compose up -d
```

### Opción B — ZimaOS / CasaOS

En ZimaOS: **App Store → Install a customized app** y pega el contenido de [`zimaos/docker-compose.yml`](zimaos/docker-compose.yml) (incluye los metadatos `x-casaos` para icono, descripción y puerto en la interfaz). Cambia `AUTH_PASSWORD` antes de instalar.

### Opción C — Desde el código fuente (GitHub)

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
| `AUTH_USERNAME` | `admin` | Usuario del login web |
| `AUTH_PASSWORD` | *(vacía)* | Contraseña del login. **Si está vacía, la API queda abierta sin autenticación** |

## 🔐 Autenticación

Define `AUTH_PASSWORD` para activar el login (sesión de 24 h por cookie). Incluye protección anti fuerza bruta: cada intento fallido añade un retardo de 1 s y tras 5 fallos la IP queda bloqueada 15 minutos.

```yaml
    environment:
      - AUTH_USERNAME=admin
      - AUTH_PASSWORD=una-contraseña-fuerte
```

## ⚠️ Seguridad

Comeback tiene acceso completo al socket de Docker y al filesystem del host — equivale a acceso root en el servidor. Incluso con el login activado, no lo expongas directamente a internet: úsalo en red local o detrás de un reverse proxy con HTTPS y, a ser posible, autenticación adicional (Cosmos Cloud, Authelia, etc.).

## Licencia

[MIT](LICENSE)
