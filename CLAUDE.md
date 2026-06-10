# CLAUDE.md — uverse comeback

Reference document for AI assistants working on this codebase. Contains everything needed to understand, extend, or debug the project without re-reading source files.

---

## 1. Project Overview

**comeback** is a self-hosted Docker backup/restore tool designed to run as a container on a QNAP NAS behind a Cosmos Cloud reverse proxy. It lets you:

- Select running or stopped Docker containers via a web UI.
- Capture full container configuration, named volumes, bind mounts, and database dumps into a single compressed, checksummed archive.
- Restore from any archive: recreate networks, volumes, and containers with original configuration, optionally injecting a name prefix for safe parallel testing.
- Verify archive integrity (SHA-256 + manifest check) without restoring.

**Deployment context:**
- Runs as a single Docker container (`uverse-comeback`) exposed on port `7731`.
- Accessed via Cosmos Cloud reverse proxy (HTTPS termination, wss:// WebSocket proxying).
- Host filesystem is bind-mounted at `/host` for bind-mount backup/restore.
- Backup storage is a named Docker volume (`uverse-comeback-backups`) mounted at `/backups`.

---

## 2. Architecture

### Container layout

```
uverse-comeback  (python:3.12-slim)
  ├── /var/run/docker.sock  → host Docker socket (rw)
  ├── /host                 → / on host (rw, for bind-mount access)
  └── /backups              → named volume uverse-comeback-backups
```

### Process model

Single uvicorn worker (`--workers 1`), started with `--proxy-headers --forwarded-allow-ips=*` to honour `X-Forwarded-Proto` from Cosmos Cloud. All backup/restore work runs as `asyncio.Task` objects — there is no Celery, no Redis, no process pool. Long blocking operations (Docker SDK volume tar via `containers.run`) are dispatched to the default thread executor via `loop.run_in_executor`.

### Frontend

Vanilla JS SPA served as static files from `app/static/`. FastAPI serves `index.html` for every non-API, non-static route (including HEAD) via a catch-all `/{path:path}` handler. There is no build step; JS/CSS are plain files.

### Python dependencies

| Package | Version | Purpose |
|---|---|---|
| fastapi | 0.115.5 | Web framework |
| uvicorn[standard] | 0.32.1 | ASGI server |
| docker | 7.1.0 | Docker SDK (both high-level and APIClient) |
| aiofiles | 24.1.0 | Async file I/O |
| python-multipart | 0.0.12 | Form/file upload support |
| pydantic | 2.10.3 | Models and validation |
| pydantic-settings | 2.6.1 | Settings from env |
| humanize | 4.11.0 | Human-readable file sizes in listing |

System tools installed in the image: `pigz`, `gzip`, `tar`, `curl`. There is **no docker CLI** in the image; all Docker operations use the Python SDK.

---

## 3. API Endpoints

All API routes are prefixed under `/api`. The SPA catch-all handles everything else.

### Containers

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/containers` | — | `ContainerInfo[]` | List all containers (running + stopped) with mounts, networks, db detection |
| GET | `/api/containers/{id}` | — | `ContainerInfo` | Single container detail |

### Backups

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/backups` | — | `BackupSummary[]` | List all backup archives (reads manifest from each .tar.gz) |
| GET | `/api/backups/{backup_name}/manifest` | — | manifest dict | Read full manifest from archive |
| DELETE | `/api/backups/{backup_name}` | — | `{"deleted": name}` | Delete archive + .sha256 sidecar |
| GET | `/api/backups/{backup_name}/download` | — | file stream | Download raw .tar.gz |
| POST | `/api/backups/start` | `BackupRequest` | `{"job_id": str}` | Start async backup job |

`backup_name` is the archive stem without `.tar.gz` (e.g. `backup_20240101_120000_a1b2c3d4`).

### Restore

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| POST | `/api/restore/start` | `RestoreRequest` | `{"job_id": str}` | Start async restore job |
| POST | `/api/restore/verify` | `RestoreRequest` (only `backup_id` used) | `{"job_id": str}` | Start async verify job (checksum + manifest only) |

### Jobs

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/jobs` | — | `Job[]` (summary) | List all jobs in memory (newest first) |
| GET | `/api/jobs/{job_id}` | — | job dict + `logs[]` | Full job state with all log entries |
| WS | `/api/jobs/{job_id}/ws` | — | event stream | Real-time job events (see Job System section) |

### Cleanup

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/cleanup/test` | — | `{containers, volumes, prefixes}` | List containers/volumes created in test mode |
| DELETE | `/api/cleanup/test/{prefix}` | — | `{removed_containers, removed_volumes, errors}` | Remove all containers and volumes with a given prefix |

### SPA / Root

| Method | Path | Purpose |
|---|---|---|
| GET, HEAD | `/` | Serve `index.html` |
| GET, HEAD | `/{path:path}` | Serve `index.html` (SPA fallback) |

HEAD is explicitly supported on all SPA routes — required by Cosmos Cloud for health probing. FastAPI docs are at `/api/docs`.

---

## 4. Data Models

### `JobStatus` (enum)
`pending` | `running` | `success` | `failed` | `cancelled`

### `JobType` (enum)
`backup` | `restore` | `verify`

### `LogLevel` (enum)
`info` | `warning` | `error` | `success` | `progress`

### `LogEntry`
| Field | Type | Description |
|---|---|---|
| `ts` | datetime | UTC timestamp, auto-set on creation |
| `level` | LogLevel | Severity |
| `message` | str | Short human-readable message |
| `detail` | str \| None | Optional extra context |

### `WSMessage`
| Field | Type | Description |
|---|---|---|
| `type` | str | Event type: `state`, `log`, `progress`, `finished`, `error` |
| `job_id` | str | UUID of the job |
| `payload` | Any | Type-dependent data |

### `ContainerInfo`
| Field | Type | Description |
|---|---|---|
| `id` | str | First 12 chars of container ID |
| `name` | str | Container name |
| `image` | str | Full image reference |
| `status` | str | Docker status string |
| `running` | bool | True if status == "running" |
| `labels` | dict[str,str] | Container labels |
| `networks` | list[str] | Network names |
| `volumes` | list[dict] | Mount info (type, source, destination, mode, name, driver) |
| `env_vars` | list[str] | Raw env list (`KEY=VALUE`) |
| `ports` | dict | Port bindings from NetworkSettings |
| `db_type` | str \| None | Auto-detected: `mysql`, `mariadb`, `postgres`, `mongodb`, `redis`, `elasticsearch`, `influxdb` |

### `BackupRequest`
| Field | Type | Default | Description |
|---|---|---|---|
| `container_ids` | list[str] | required | Container IDs or names to back up |
| `include_images` | bool | False | Export image tarballs |
| `compress` | bool | True | Reserved; always compressed |
| `label` | str \| None | None | Human label stored in manifest |

### `RestoreRequest`
| Field | Type | Default | Description |
|---|---|---|---|
| `backup_id` | str | required | Archive stem name or full filename |
| `container_names` | list[str] \| None | None | Filter to specific containers; None = all |
| `overwrite_existing` | bool | False | Stop+remove existing containers before restore |
| `start_after_restore` | bool | True | Start containers after recreation |
| `name_prefix` | str \| None | None | Prefix all names for test-mode restore |

### `BackupManifest`
| Field | Type | Description |
|---|---|---|
| `id` | str | 8-char UUID fragment |
| `label` | str \| None | User-supplied label |
| `created_at` | datetime | UTC creation time |
| `comeback_version` | str | Always `"1.0.0"` |
| `source_hostname` | str | `socket.gethostname()` at backup time |
| `containers` | list[dict] | `{name, image, spec_file}` per container |
| `volumes` | list[dict] | Volume/bind mount backup entries |
| `databases` | list[dict] | DB dump entries |
| `images` | list[dict] | Image tarball entries (if include_images) |
| `networks` | list[dict] | Non-default network configs |
| `checksum` | str \| None | SHA-256 hex of the .tar.gz archive |
| `size_bytes` | int | Compressed archive size |

### `BackupSummary`
| Field | Type | Description |
|---|---|---|
| `id` | str | Manifest id |
| `label` | str \| None | User label |
| `created_at` | datetime | |
| `size_bytes` | int | |
| `size_human` | str | e.g. `"42.1 MB"` |
| `container_count` | int | |
| `status` | str | Always `"ok"` in listing |
| `path` | str | Filename (no directory) |

---

## 5. Backup System

### Orchestration (`backup/manager.py: run_backup`)

1. **Generate ID and directories.** 8-char UUID fragment `backup_id`, timestamp `ts = %Y%m%d_%H%M%S`, working dir `{BACKUP_PATH}/backup_{ts}_{backup_id}/` with subdirs `containers/`, `volumes/`, `databases/`, `images/`, `networks/`.

2. **Export networks** (`backup/containers.py: export_networks`). All Docker networks except `bridge`, `host`, `none` are serialised to `networks/networks.json` with driver, options, IPAM, labels, internal, attachable flags.

3. **Per-container loop** (progress 10%→80%):
   - **Container spec** (`export_container_spec`): full `docker inspect` output normalised to a flat dict (`id`, `name`, `image`, `image_id`, `config`, `host_config`, `network_settings`, `mounts`, `state`, `hostname`). Written to `containers/{name}.json`. Mount keys are lower-cased for consistency.
   - **Volume backup** (`backup_all_volumes`): iterates `spec["mounts"]`, deduplicates by name/source.
   - **Database dump**: if image matches a known DB pattern and container is running, dump is taken.
   - **Image export** (optional): `client.images.get(image).save()` streamed to `images/{name}.tar`.

4. **Write manifest** to `work_dir/manifest.json`.

5. **Create archive**: `tarfile.open(archive_path, "w:gz")` adds the entire `work_dir` as `backup_{ts}_{backup_id}/`.

6. **Compute SHA-256**: read archive file in 64 KB chunks. Write `{backup_name}.sha256` sidecar with `{hex}  {filename}` format.

7. **Cleanup**: `shutil.rmtree(work_dir)`. Only the `.tar.gz` and `.sha256` remain.

### Named volume backup (`backup/volumes.py: backup_docker_volume`)

Uses **Docker Python SDK only** — no docker CLI in the container.

```
client.containers.run(
    "alpine",
    ["tar", "czf", "-", "-C", "/data", "."],
    volumes={volume_name: {"bind": "/data", "mode": "ro"}},
    remove=True,
)
```

The return value of `containers.run()` with no stream option is the full stdout bytes. Written directly to `volumes/{volume_name}.tar.gz`. Blocking call dispatched to thread executor via `loop.run_in_executor`.

### Bind mount backup (`backup/volumes.py: backup_bind_mount`)

Host path accessed as `{HOST_ROOT}/{source_path}` (e.g. `/host/mnt/data/myapp`). Uses subprocess `tar czf {archive} -C {parent} {name}`. Archive name: `source_path.replace("/","_").strip("_") + ".tar.gz"` (e.g. `mnt_data_myapp.tar.gz`).

If the path does not exist inside the container (i.e. not reachable via `/host`), the mount is skipped with a warning — no error.

### Database detection (`backup/containers.py: detect_db_type`)

Image name lowercased, tag stripped, basename extracted. Substring matched against:

| Substring | db_type returned |
|---|---|
| `mysql` | `mysql` |
| `mariadb` | `mariadb` |
| `postgres` | `postgres` |
| `mongo` | `mongodb` |
| `redis` | `redis` |
| `elasticsearch` | `elasticsearch` |
| `influxdb` | `influxdb` |

### Database dumps (`backup/databases.py`)

All dumps use `docker exec` via subprocess (not the SDK). The `docker` CLI binary is available on the host socket path; since `/var/run/docker.sock` is mounted, the host's `docker` CLI is invoked via subprocess inside the container. Wait — actually the container has **no docker CLI**. Database dumps use `asyncio.create_subprocess_exec("docker", ...)` which resolves to the `docker` binary that must exist in `PATH`. Check: the Dockerfile does not install docker CLI. This means `dump_database` calls will fail unless `docker` is on PATH. **This is a known gap** — the database dump and restore functions call `docker exec`, `docker cp` via subprocess, which requires the docker CLI binary to be present. In practice this works only if the host's docker CLI is accessible through the socket somehow, or if a future image layer adds it. If database dumps are failing, install the docker CLI in the Dockerfile.

**MySQL/MariaDB**: `mysqldump -uroot -p{pass} --all-databases --single-transaction --routines --triggers --events` piped through `gzip -c` to `databases/mysql_{container}.sql.gz`. Root password read from `MYSQL_ROOT_PASSWORD` or `MARIADB_ROOT_PASSWORD`.

**PostgreSQL**: `pg_dumpall -U {user}` with `PGPASSWORD` env var, piped through `gzip -c` to `databases/postgres_{container}.sql.gz`. User from `POSTGRES_USER` (default: `postgres`).

**MongoDB**: `mongodump --out /tmp/comeback_mongodump_{name}` inside container, then `tar czf /tmp/mongo_{name}.tar.gz` inside container, then `docker cp container:/tmp/mongo_{name}.tar.gz databases/`. Credentials from `MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD`.

**Redis**: `redis-cli BGSAVE` inside container, 2-second sleep, then `docker cp container:/data/dump.rdb databases/redis_{name}.rdb`.

### Bundle structure on disk

```
{BACKUP_PATH}/
  backup_20240101_120000_a1b2c3d4.tar.gz
  backup_20240101_120000_a1b2c3d4.sha256
  ...

Inside .tar.gz (extracted root = backup_20240101_120000_a1b2c3d4/):
  manifest.json
  containers/
    myapp.json           # full docker inspect dict
    mydb.json
  volumes/
    myapp_data.tar.gz    # named volume archive
    mnt_data_myapp.tar.gz  # bind mount archive
  databases/
    mysql_mydb.sql.gz
    postgres_mydb.sql.gz
    mongo_mydb.tar.gz
    redis_mydb.rdb
  images/
    myapp.tar            # optional, only if include_images=true
  networks/
    networks.json
```

---

## 6. Restore System

### Orchestration (`restore/manager.py: run_restore`)

1. **Verify** (`restore/verify.py: verify_backup`): check SHA-256 sidecar if present; open tarball, find `manifest.json`, parse and validate. Returns manifest dict.

2. **Extract** to temp dir `{BACKUP_PATH}/_restore_{job_id[:8]}/`. The tar root becomes the `extracted/` dir.

3. **Recreate networks** (`_recreate_networks`): for each network in manifest, skip if already exists, otherwise `client.networks.create()` with original driver/options/labels/IPAM. Networks missing from manifest are never deleted.

4. **Container filter**: if `container_names` was specified, only those entries are processed.

5. **Per-container loop** (progress 20%→85%):

   a. **Resolve spec file**: `extracted/containers/{name}.json`.

   b. **Handle existing container**: if `overwrite=True`, stop (timeout=10s) and remove; otherwise skip with warning.

   c. **Restore volumes**: for each volume entry in manifest that matches this container's mounts:
      - `type=docker`: call `restore_docker_volume(target_vol, archive, job)`. If `name_prefix` is set, target volume name is `{prefix}{original_name}`.
      - `type=bind` and no prefix: call `restore_bind_mount(source, archive, job)`.
      - `type=bind` with prefix: skip (test mode — bind mounts not touched).

   d. **Pull/load image**: check `images/{container_name}.tar` first; if present, `client.images.load(bytes)`. Otherwise `client.images.pull(image_name)`.

   e. **Create container** via `_build_run_kwargs` (see below). First call `client.containers.create(**kwargs)`, then connect to additional networks (all beyond the first, since docker-py `run/create` only accepts one network), then `container.start()` if `start_after=True`.

   f. **Restore databases** (only if `start_after=True`): match manifest `databases[]` entries by `container` field, call `restore_database`.

6. **Cleanup**: `shutil.rmtree(work_dir)` in `finally` block.

### Named volume restore (`restore/volumes.py: restore_docker_volume`)

Uses Docker Python SDK exclusively via the low-level `APIClient`:

```python
# Create volume
client.volumes.create(volume_name)

# Create alpine container with stdin open, volume mounted rw
container = api.create_container("alpine",
    command=["sh", "-c", "cd /data && tar xzf -"],
    host_config=api.create_host_config(binds={volume_name: {"bind": "/data", "mode": "rw"}}),
    stdin_open=True)

api.start(cid)

# Pipe archive bytes into stdin via raw socket
sock = api.attach_socket(cid, params={"stdin": 1, "stream": 1})
raw = sock._sock
# send in 64KB chunks, then shutdown write side
raw.sendall(chunk); raw.shutdown(SHUT_WR); raw.close()

api.wait(cid)
api.remove_container(cid, force=True)
```

This is entirely SDK-based because the container has no docker CLI. The archive is read fully into memory before being piped (limitation for very large volumes).

### Bind mount restore (`restore/volumes.py: restore_bind_mount`)

Host destination: `{HOST_ROOT}/{source_path}` (e.g. `/host/mnt/data/myapp`). `os.makedirs` to ensure path exists, then subprocess `tar xzf {archive} -C {dest}`. Requires `/host` to be mounted `rw` in the comeback container.

### Container recreation (`restore/manager.py: _build_run_kwargs`)

Converts `docker inspect` spec back to `docker.containers.create()` kwargs:

| Spec field | Action |
|---|---|
| `config.Image` | `image` |
| `config.Env` | `environment` |
| `config.Labels` | `labels` (merged with comeback prefix labels) |
| `config.Cmd` | `command` |
| `config.Entrypoint` | `entrypoint` |
| `config.User` | `user` |
| `config.WorkingDir` | `working_dir` |
| `config.Hostname` | `hostname` |
| `host_config.RestartPolicy.Name` | `restart_policy` (ignored if prefix set → `no`) |
| `host_config.PortBindings` | `ports` (skipped entirely if prefix set) |
| `host_config.Privileged` | `privileged` |
| `host_config.CapAdd` | `cap_add` |
| `host_config.Devices` | `devices` (PathInContainer values) |
| `mounts[type=volume]` | `volumes` list: `{prefix}{name}:{dest}:{mode}` |
| `mounts[type=bind]` | `volumes` list: `{source}:{dest}:{mode}` (skipped if prefix set) |
| `network_settings.Networks` first key | `network` (primary network) |
| remaining networks | connected via `net.connect(container)` after create |

Labels added by comeback on every restored container:
- `com.uverse.comeback.prefix`: the prefix string (empty string for normal restores)
- `com.uverse.comeback.original`: the original container name without prefix

### Database restore (`restore/databases.py`)

All DB restores use `docker exec -i` / `docker cp` subprocesses. Same CLI dependency caveat as backup.

**MySQL/MariaDB**: `gunzip -c {dump.sql.gz}` piped into `docker exec -i {container} mysql -uroot -p{pass}`. 5-second sleep before restore to let MySQL start.

**PostgreSQL**: `gunzip -c {dump.sql.gz}` piped into `docker exec -i -e PGPASSWORD={pass} {container} psql -U {user}`. 5-second sleep before restore. Non-zero exit is logged as warning (not error) because `psql` exits non-zero on certain restore scenarios.

**MongoDB**: `docker cp {dump.tar.gz} container:/tmp/`, `docker exec container tar xzf /tmp/{file} -C /tmp`, `docker exec container mongorestore /tmp/{folder}`.

**Redis**: `docker cp {dump.rdb} container:/data/dump.rdb`.

---

## 7. Prefix / Test Mode

When `name_prefix` is set on a `RestoreRequest` (the UI defaults it to `"test-"` when the Test button is clicked), the restore runs in test/parallel mode. The following changes apply:

| Aspect | Normal restore | Prefixed restore |
|---|---|---|
| Container name | original name | `{prefix}{name}` |
| Docker volume names | original | `{prefix}{volume_name}` |
| Bind mounts | restored to `/host/...` | **skipped** |
| Port bindings | restored from spec | **not mapped** (avoids conflicts) |
| Restart policy | from spec | forced to `no` |
| Labels added | `comeback.prefix=""`, `comeback.original=name` | `comeback.prefix={prefix}`, `comeback.original=name` |

The label `com.uverse.comeback.prefix` is the discovery mechanism for cleanup.

### Cleanup API

`GET /api/cleanup/test`: iterates all containers, collects those with a non-empty `com.uverse.comeback.prefix` label. Then finds volumes whose names start with any discovered prefix. Returns `{containers, volumes, prefixes}`.

`DELETE /api/cleanup/test/{prefix}`: force-removes all containers with `com.uverse.comeback.prefix == prefix`, then all volumes whose name starts with `prefix`.

---

## 8. Job System

### In-memory job store

`JobManager` holds a `dict[str, Job]` in process memory. Jobs are never persisted. A server restart clears all job history. There is no job limit or eviction.

### Job lifecycle

```
created (pending) → started_at set, status=running → logs/progress events → finished (success/failed)
```

### Real-time streaming

Each `Job` maintains a `list[asyncio.Queue]` of subscribers. Log entries, progress updates, and the final finished event are broadcast to all subscriber queues via `job._broadcast()`.

WebSocket endpoint (`/api/jobs/{id}/ws`):
1. Accepts connection.
2. Sends current state immediately: `{type: "state", job: {...}, logs: [...]}`.
3. If job already finished, sends `{type: "finished", status: ...}` and returns (no loop).
4. Otherwise subscribes to job queue, relays events until `type == "finished"` or client disconnects.

Event types emitted:
- `{type: "log", data: LogEntry}` — a new log line
- `{type: "progress", pct: int, message: str}` — progress update
- `{type: "finished", status: JobStatus, summary: dict}` — terminal event

### Polling fallback (`static/js/app.js`)

The frontend tries WebSocket first. If the connection errors before ever opening (`wsConnected = false`), it falls back to HTTP polling of `GET /api/jobs/{id}` every 1500ms. The fallback tracks `lastLogCount` to append only new entries. Cosmos Cloud supports WebSocket proxying (wss://), so polling is only a safety net for broken proxy configurations.

### WebSocket URL construction

```js
const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
return new WebSocket(`${proto}//${location.host}/api/jobs/${id}/ws`);
```

Protocol is derived from `location.protocol` — no hardcoding. Works correctly behind Cosmos Cloud's HTTPS termination.

---

## 9. Known Constraints and Design Decisions

### No docker CLI in container

The Dockerfile installs `pigz gzip tar curl` but **not** the docker CLI. All operations use the Python `docker` SDK exclusively:
- Volume backup: `client.containers.run()` stdout pipe via alpine
- Volume restore: `APIClient.exec_run()` + `APIClient.attach_socket()` stdin pipe
- Database dump: `container.exec_run(cmd)` → stdout bytes → gzip in Python
- Database restore: `container.put_archive()` to upload SQL/dump file, then `container.exec_run()` to apply it
- MongoDB/Redis file extraction: `container.get_archive()` → in-memory tarfile extraction

### `/host` mount required for bind mounts

Bind mount backup reads from `{HOST_ROOT}/{path}` (read access, using subprocess `tar`). Bind mount restore writes to `{HOST_ROOT}/{path}` (write access, using subprocess `tar xzf`). The docker-compose volume `- /:/host` provides this. Without it, all bind mounts are silently skipped on backup and will fail on restore.

### Volume backup reads all bytes into memory

`backup_docker_volume` calls `client.containers.run(...)` which returns the full stdout bytes before writing to disk. For very large volumes this can exhaust container memory. The restore path similarly reads the full archive into memory before piping to stdin. For large volumes, consider streaming alternatives using the low-level API.

### Volume name normalisation

`export_container_spec` normalises all mount dict keys to lowercase (`type`, `name`, `source`, `destination`, `mode`, `driver`). Restore code reads these lowercase keys. Do not mix casing when adding new mount-related logic.

### `.tar.gz` name handling — avoid `.stem` double-extension bug

MongoDB dump archive is named `mongo_{container}.tar.gz`. When computing the extracted folder name on restore, the code uses:
```python
folder = db_file.name.replace(".tar.gz", "")
```
Do **not** use `db_file.stem` here — `.stem` strips only one extension, so `mongo_foo.tar.gz` → `.stem` = `mongo_foo.tar` → wrong folder path. The `replace(".tar.gz", "")` approach correctly produces `mongo_foo`.

### Cosmos Cloud proxy

- HEAD method must be handled on all routes — done via `methods=["GET", "HEAD"]` on the SPA catch-all and FastAPI's router supports HEAD on GET routes.
- `--proxy-headers --forwarded-allow-ips=*` on uvicorn ensures `X-Forwarded-Proto: https` is respected, so redirect/URL generation is correct and WebSocket upgrade headers work.

### Single worker process

`--workers 1` is intentional. The in-memory `JobManager` cannot be shared across multiple uvicorn workers. If multiple workers are needed, the job store must be moved to Redis or a database.

### No authentication

There is no authentication layer. Access control is assumed to be handled by Cosmos Cloud (e.g. forward-auth, IP restriction) or network isolation.

---

## 10. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BACKUP_PATH` | `/backups` | Absolute path inside container where archives are stored |
| `HOST_ROOT` | `/host` | Mount point of the host's root filesystem |
| `TZ` | `Europe/Madrid` | Timezone for log timestamps |

Loaded via `pydantic-settings` (`Settings` class in `app/config.py`). Can also be set via a `.env` file for local development.

---

## 11. File/Directory Layout

```
comeback/
  docker-compose.yml
  backend/
    Dockerfile
    requirements.txt
    app/
      main.py              # FastAPI app, router registration, SPA catch-all
      config.py            # Settings (BACKUP_PATH, HOST_ROOT, TZ)
      models.py            # All Pydantic models and enums
      job_manager.py       # In-memory Job and JobManager classes
      docker_client.py     # Cached DockerClient and APIClient factories
      api/
        containers.py      # GET /api/containers
        backup.py          # GET/POST/DELETE /api/backups
        restore.py         # POST /api/restore/start|verify
        jobs.py            # GET /api/jobs, WS /api/jobs/{id}/ws
        cleanup.py         # GET/DELETE /api/cleanup/test
      backup/
        manager.py         # run_backup() orchestrator
        containers.py      # get_container_info, export_container_spec, export_networks
        volumes.py         # backup_docker_volume, backup_bind_mount, backup_all_volumes
        databases.py       # dump_mysql, dump_postgres, dump_mongodb, dump_redis
      restore/
        manager.py         # run_restore() orchestrator, _build_run_kwargs
        verify.py          # verify_backup() checksum + manifest check
        volumes.py         # restore_docker_volume, restore_bind_mount
        databases.py       # restore_mysql, restore_postgres, restore_mongodb, restore_redis
      static/
        index.html         # SPA entry point
        js/
          api.js           # Fetch/WebSocket API client wrapper
          app.js           # UI state, rendering, job modal, polling fallback
        css/               # (assumed) styles
```

---

## 12. How to Build and Run

### Production (docker-compose)

```bash
cd /path/to/comeback
docker compose up -d --build
```

The compose file builds from `./backend`, names the container `uverse-comeback`, exposes port `7731`, and creates the named volume `uverse-comeback-backups`.

Access at `http://host:7731` or via Cosmos Cloud reverse proxy at `https://comeback.yourdomain.com`.

### Local development

```bash
cd backend
pip install -r requirements.txt
BACKUP_PATH=/tmp/backups HOST_ROOT=/host uvicorn app.main:app --reload --port 7731
```

For bind-mount operations to work locally, `/host` must point to an accessible directory (can be faked with a symlink for testing). Docker socket access requires the dev machine to have Docker running and the socket at the standard path.

### Healthcheck

The Dockerfile configures a health check:
```
curl -f http://localhost:7731/api/docs || exit 1
```
Runs every 30s with a 5s timeout.

### Image tags

No registry is configured; the image is built locally from `./backend`. To push to a registry, add an `image:` key to the compose service.
