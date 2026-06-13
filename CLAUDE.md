# CLAUDE.md — uverse comeback

Reference document for AI assistants working on this codebase. Contains everything needed to understand, extend, or debug the project without re-reading source files.

---

## 1. Project Overview

**comeback** is a self-hosted Docker backup/restore/deploy tool designed to run as a container on NAS systems (QNAP, ZimaOS) optionally behind a Cosmos Cloud reverse proxy. Current version: see `APP_VERSION` in `app/config.py`. It provides:

- **Backup**: full container configuration, named volumes, bind mounts, and database dumps into a single compressed, checksummed archive. Manual or scheduled (daily/weekly with retention).
- **Restore**: recreate networks, volumes, and containers with original configuration. Test mode via name prefix. Cross-host migrations: upload archives from another instance and remap bind-mount paths.
- **Deploy**: stack templates (Ente Photos, Plex, Portainer, Grafana, Nextcloud), arbitrary Compose YAML or inline Dockerfile, with streaming pull progress, rollback on failure and post-deploy auto-backup. Environment report (used ports, generic bind roots, shared networks) to avoid conflicts.
- **Updates**: Watchtower-style image update detection (digest comparison), one-click or bulk update with optional pre-update backup, recreation and automatic rollback if the new image crashes.
- **Container management**: per-container actions (start/stop/restart/pause/unpause/kill inline; pull/recreate as jobs), Cosmos-style cards with status badge, ports, networks and CPU/RAM sparkline.
- **Monitoring**: per-container stats tab, live metrics in cards, host-wide monitor in the header (CPU/RAM/disk/load/temp/net/IO from host /proc via /host).
- **Auth**: single-user login with brute-force lockout, bcrypt hash support, UI password change persisted on the backups volume.
- **i18n**: Spanish/English with browser auto-detection (client-side; backend job logs remain Spanish).

**Deployment context:**
- Runs as a single Docker container (`uverse-comeback`) exposed on port `7731`.
- Published image: `espiralvex/comeback` on Docker Hub (amd64+arm64); GitHub repo `8aws/comeback`. Tag `v*` triggers the publish workflow; every push runs the pytest CI.
- Optionally accessed via Cosmos Cloud reverse proxy (HTTPS termination, wss:// WebSocket proxying).
- Host filesystem is bind-mounted at `/host` for bind-mount backup/restore and host monitoring.
- Backup storage is a named Docker volume (`uverse-comeback-backups`) mounted at `/backups`. Also holds job history (`.jobs/jobs.jsonl`), schedules (`.schedules.json`) and the UI-set password hash (`.auth.json`).

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

**MelodY integration**: `static/melody.html` is a self-contained visual Docker Compose generator (vendored copy from the MelodY project, ~83 KB, no external deps). The Deploy tab opens it in an iframe modal (`openMelody()`). When run embedded, MelodY shows a "🚀 Desplegar en comeback" button that `postMessage`s `{type:'melody-deploy', name, yaml, env}` to the parent. The SPA listener (origin-checked) inlines any `${VAR}` using MelodY's `.env` text (comeback's compose deploy has no separate `.env`), fills the compose panel and calls `startComposeDeploy()`. To update MelodY, re-copy the file and re-add the bridge block (button + `EMBEDDED` postMessage at the end of its `<script>`).

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

### Auth (exempt from the auth middleware — checks session internally where needed)

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/auth/status` | — | `{auth_enabled, authenticated, instance_name}` — public, feeds login screen |
| POST | `/api/auth/login` | `{username, password}` | Sets session cookie; 1s delay per failure, 15 min IP lockout after 5 |
| POST | `/api/auth/logout` | — | Destroys session |
| POST | `/api/auth/change-password` | `{current_password, new_password}` | Requires session; stores bcrypt hash in `/backups/.auth.json` (precedence over env), clears all sessions |

### Containers

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/containers` | — | `ContainerInfo[]` | Fast list WITHOUT sizes (running + stopped) with mounts, networks, db detection, created, health, exit_code |
| GET | `/api/containers/sizes` | — | `{id: {size_bytes, size_human}}` | Disk usage per container (slow daemon call, fetched separately by the UI) |
| GET | `/api/containers/{id}` | — | `ContainerInfo` | Single container detail |
| POST | `/api/containers/{id}/action` | `{action}` | `{ok}` or `{job_id}` | start/stop/restart/pause/unpause/kill inline; recreate/pull return a job |

### Updates

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/updates` | — | Update status for all containers (digest comparison) |
| GET | `/api/updates/check/{id}` | — | Single-container check (progressive UI) |
| POST | `/api/updates/start` | `{container_id, backup_first}` | Update one container (job) |
| POST | `/api/updates/start-all` | `{container_ids, backup_first}` | Serial bulk update in one job, continues past failures |

### Schedules

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/api/schedules` | List (with computed next_run) / create |
| PUT/DELETE | `/api/schedules/{id}` | Update (incl. enabled toggle) / delete |
| POST | `/api/schedules/{id}/run` | Run now (job) |

### Stats & system

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stats` | Per-running-container CPU/RAM/net/blockIO/pids (parallel one-shot docker stats, ~2s) |
| GET | `/api/stats/host` | Host-wide CPU/RAM/disk/load/temp/net/IO from host /proc//sys; rates need two samples (counters cached) |
| GET | `/api/system` | `{version, instance_name, tz}` |
| GET | `/api/deploy/environment` | Used ports (docker + host listeners), generic bind roots, shared networks |

### Backups

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| GET | `/api/backups` | — | `BackupSummary[]` | List all backup archives (reads manifest from each .tar.gz) |
| GET | `/api/backups/{backup_name}/manifest` | — | manifest dict | Read full manifest from archive |
| DELETE | `/api/backups/{backup_name}` | — | `{"deleted": name}` | Delete archive + .sha256 sidecar |
| GET | `/api/backups/{backup_name}/download` | — | file stream | Download raw .tar.gz |
| POST | `/api/backups/upload` | multipart `file` | summary dict | Import archive from another instance (streamed, SHA-256 on the fly, manifest validated, 409 on duplicate) |
| POST | `/api/backups/start` | `BackupRequest` | `{"job_id": str}` | Start async backup job |

`backup_name` is the archive stem without `.tar.gz` (e.g. `backup_20240101_120000_a1b2c3d4`).

### Restore

| Method | Path | Body | Response | Purpose |
|---|---|---|---|---|
| POST | `/api/restore/start` | `RestoreRequest` | `{"job_id": str}` | Start async restore job. `path_map` ({old_prefix: new_prefix}, longest prefix wins) remaps bind-mount paths for cross-host migrations |
| POST | `/api/restore/verify` | `RestoreRequest` (only `backup_id` used) | `{"job_id": str}` | Start async verify job (checksum + manifest only) |

### Deploy

| Method | Path | Body | Purpose |
|---|---|---|---|
| GET | `/api/deploy/templates` | — | Template metadata (fields rendered as a form by the UI) |
| POST | `/api/deploy/start` | `{template_id, config}` | Template deploy (job) + auto-backup on success |
| POST | `/api/deploy/compose` | `{name, yaml_content}` | Compose YAML deploy (job) + auto-backup |
| POST | `/api/deploy/dockerfile` | `DockerfileDeployRequest` | Build inline Dockerfile and run (job) + auto-backup |

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

All dumps and restores use the Docker Python SDK exclusively (`container.exec_run`, `put_archive`, `get_archive`) — there is **no docker CLI** in the image. See section 9 for the per-operation mapping.

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

All DB restores use the SDK (`put_archive` to upload the dump, `exec_run` to apply it). The `docker exec`/`docker cp` notation below describes the logical operations, executed via SDK equivalents.

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

### Volume backup/restore streams in chunks

`backup_docker_volume` attaches to an alpine helper via the low-level APIClient and streams tar stdout to disk in chunks; the restore path streams the archive from disk into the helper's stdin in 64 KB chunks. Neither direction holds the full archive in memory — multi-GB volumes are safe. Both check the helper's exit code and raise on tar failure.

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

### Authentication

Single-user session auth (`app/auth.py`). Enabled when `AUTH_PASSWORD`, `AUTH_PASSWORD_HASH` or a UI-set password exist; otherwise the API is open (warning logged at startup). Password precedence: `/backups/.auth.json` (UI change, bcrypt) → `AUTH_PASSWORD_HASH` env (bcrypt) → `AUTH_PASSWORD` env (plain, constant-time compare). Sessions: in-memory token → expiry (24h sliding), HttpOnly SameSite=Lax cookie `comeback_session`; works for the WebSocket too (cookie validated before accept, close 4401). Brute force: 1s delay per failed login, IP locked 15 min after 5 failures (cleared on success). The HTTP middleware guards everything under `/api/` except `/api/auth/*`; the SPA and static files stay public. A server restart clears all sessions (in-memory).

### Background scheduler

`scheduler_loop()` (started on FastAPI startup) checks every 60s whether a schedule is due, interpreting `time` in the container TZ (tzdata installed in the image). Schedules persist in `/backups/.schedules.json`. A schedule created after today's slot waits for the next occurrence (`created_at` guard). Retention: after a successful run, archives whose manifest label equals `⏰ {name}` beyond the newest N are deleted. The in-memory JobManager persists finished jobs (with logs) to `/backups/.jobs/jobs.jsonl`, reloading the last 200 lazily.

### Self-update limitation

Comeback detects its own container (short id from `/etc/hostname` vs container id) and refuses to recreate itself — update/recreate actions on it only pull the new image; the user recreates from compose/ZimaOS.

---

## 10. Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BACKUP_PATH` | `/backups` | Absolute path inside container where archives are stored |
| `HOST_ROOT` | `/host` | Mount point of the host's root filesystem |
| `TZ` | `Europe/Madrid` | Timezone for log timestamps and the backup scheduler |
| `AUTH_USERNAME` | `admin` | Login username |
| `AUTH_PASSWORD` | *(empty)* | Plain login password; empty (and no hash) = API open |
| `AUTH_PASSWORD_HASH` | *(empty)* | bcrypt hash, takes precedence over `AUTH_PASSWORD` |
| `INSTANCE_NAME` | *(host hostname)* | Label shown on login/header/manifests; falls back to `/host/etc/hostname` |

Loaded via `pydantic-settings` (`Settings` class in `app/config.py`). Can also be set via a `.env` file for local development.

---

## 11. File/Directory Layout

```
comeback/
  docker-compose.yml       # dev compose: bind-mounts ./backend/app → /app/app (no rebuild for code changes)
  zimaos/docker-compose.yml # ZimaOS/CasaOS install file with x-casaos metadata
  .github/workflows/
    ci.yml                 # pytest on every push/PR
    docker-publish.yml     # multi-arch image to Docker Hub on tag v*
  backend/
    Dockerfile             # python:3.12-slim + pigz gzip tar curl tzdata
    requirements.txt
    requirements-dev.txt   # + pytest, httpx
    tests/                 # pytest suite (run via docker python:3.12-slim; Mac only has 3.9)
    app/
      main.py              # FastAPI app, auth middleware, routers, /api/system, scheduler startup, SPA catch-all
      config.py            # Settings + APP_VERSION (bump on each release)
      models.py            # All Pydantic models and enums
      auth.py              # Sessions, brute-force lockout, password change, /api/auth router
      job_manager.py       # Job/JobManager + JSONL persistence of finished jobs
      scheduler.py         # Scheduled backups: store, due logic, retention, loop
      updates.py           # Update check (digests), run_update(_all), run_recreate, run_pull
      host_stats.py        # Host CPU/RAM/disk/load/temp/net/IO from /host proc//sys
      environment.py       # Deploy environment report (ports, bind roots, shared networks)
      docker_client.py     # Cached DockerClient and APIClient factories
      api/
        containers.py      # GET list/sizes/{id}, POST {id}/action
        backup.py          # GET/POST/DELETE /api/backups, POST upload
        restore.py         # POST /api/restore/start|verify
        jobs.py            # GET /api/jobs, WS /api/jobs/{id}/ws (cookie-gated)
        cleanup.py         # GET/DELETE /api/cleanup/test
        deploy.py          # templates/compose/dockerfile deploys, environment, auto-backup
        schedules.py       # CRUD + run-now
        stats.py           # /api/stats (containers) + /api/stats/host
        updates.py         # /api/updates check/start/start-all
      backup/              # run_backup orchestrator, spec export, volumes (streaming), db dumps
      restore/             # run_restore, _build_run_kwargs, _remap_path, verify, volumes (streaming), db restores
      deploy/
        compose.py         # Compose/Dockerfile deploys, _pull_with_progress(force=)
      templates/
        base.py            # BaseTemplate + TemplateField
        ente_photos.py     # Ente stack (SSD/HDD paths, TZ, hex jwt-secret)
        official.py        # SingleContainerTemplate + Plex/Portainer/Grafana/Nextcloud
      static/
        index.html         # SPA entry point (tabs, modals, login overlay, settings, host monitor)
        melody.html        # MelodY visual Compose generator (self-contained; iframe in Deploy)
        js/
          i18n.js          # ES/EN dictionary, t(), DOM translation + MutationObserver
          api.js           # Fetch/WebSocket API client wrapper (401 → login overlay)
          app.js           # UI state, rendering, cards, filters, job modal, polling fallback
        css/style.css      # dark theme vars + [data-theme=light] overrides
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
