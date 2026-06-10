"""Restore databases into running containers via Docker SDK (no CLI required)."""
import asyncio
import gzip
import io
import tarfile
from pathlib import Path

from ..docker_client import get_docker
from ..models import LogLevel


def _env_dict(env_vars: list[str]) -> dict[str, str]:
    return {k: v for e in env_vars if "=" in e for k, v in [e.split("=", 1)]}


async def _wait_ready(container_name: str, check_cmd: list[str], max_wait: int = 60) -> bool:
    """Poll until a command exits 0 inside the container."""
    loop = asyncio.get_event_loop()
    for _ in range(max_wait):
        def _check():
            client = get_docker()
            c = client.containers.get(container_name)
            r = c.exec_run(check_cmd, stdout=False, stderr=False)
            return r.exit_code == 0
        try:
            ready = await loop.run_in_executor(None, _check)
            if ready:
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def _sdk_exec(container_name: str, cmd: list[str]) -> tuple[int, bytes]:
    loop = asyncio.get_event_loop()
    def _run():
        client = get_docker()
        c = client.containers.get(container_name)
        r = c.exec_run(cmd, stdout=True, stderr=True, demux=False)
        return r.exit_code, r.output or b""
    return await loop.run_in_executor(None, _run)


async def _put_file(container_name: str, dest_path: str, data: bytes) -> None:
    """Upload bytes into a container at dest_path using SDK put_archive."""
    loop = asyncio.get_event_loop()
    filename = dest_path.split("/")[-1]
    dest_dir = "/".join(dest_path.split("/")[:-1]) or "/"

    def _upload():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        buf.seek(0)
        client = get_docker()
        c = client.containers.get(container_name)
        c.put_archive(dest_dir, buf.read())

    await loop.run_in_executor(None, _upload)


async def restore_mysql(container_name: str, db_file: Path, env_vars: list[str], job) -> bool:
    env = _env_dict(env_vars)
    password = env.get("MYSQL_ROOT_PASSWORD") or env.get("MARIADB_ROOT_PASSWORD", "")
    await job.log(LogLevel.info, f"Waiting for MySQL in {container_name}...")
    await _wait_ready(container_name, ["mysqladmin", "-uroot", f"-p{password}", "ping", "--silent"])

    await job.log(LogLevel.info, f"Restoring MySQL into {container_name}")
    sql_data = gzip.decompress(db_file.read_bytes())
    exit_code, output = await _sdk_exec(container_name,
        ["sh", "-c", f"mysql -uroot -p{password}"])

    # exec_run doesn't support stdin piping directly — upload SQL and source it
    await _put_file(container_name, "/tmp/comeback_restore.sql", sql_data)
    exit_code, output = await _sdk_exec(container_name,
        ["sh", "-c", f"mysql -uroot -p{password} < /tmp/comeback_restore.sql"])

    if exit_code != 0:
        await job.log(LogLevel.error, f"MySQL restore failed: {output.decode()[:300]}")
        return False
    await job.log(LogLevel.success, "MySQL restored")
    return True


async def restore_postgres(container_name: str, db_file: Path, env_vars: list[str], job) -> bool:
    env = _env_dict(env_vars)
    user = env.get("POSTGRES_USER", "postgres")
    password = env.get("POSTGRES_PASSWORD", "")
    await job.log(LogLevel.info, f"Waiting for PostgreSQL in {container_name}...")
    await _wait_ready(container_name, ["pg_isready", "-U", user])

    await job.log(LogLevel.info, f"Restoring PostgreSQL into {container_name}")
    sql_data = gzip.decompress(db_file.read_bytes())
    await _put_file(container_name, "/tmp/comeback_restore.sql", sql_data)

    exit_code, output = await _sdk_exec(container_name,
        ["sh", "-c", f"PGPASSWORD={password} psql -U {user} < /tmp/comeback_restore.sql"])

    if exit_code != 0:
        await job.log(LogLevel.warning, f"PostgreSQL restore warnings: {output.decode()[:300]}")
    await job.log(LogLevel.success, "PostgreSQL restored")
    return True


async def restore_mongodb(container_name: str, db_file: Path, env_vars: list[str], job) -> bool:
    env = _env_dict(env_vars)
    user = env.get("MONGO_INITDB_ROOT_USERNAME", "")
    password = env.get("MONGO_INITDB_ROOT_PASSWORD", "")
    await job.log(LogLevel.info, f"Restoring MongoDB into {container_name}")

    archive_data = db_file.read_bytes()
    await _put_file(container_name, f"/tmp/{db_file.name}", archive_data)

    folder = db_file.name.replace(".tar.gz", "")
    await _sdk_exec(container_name, ["tar", "xzf", f"/tmp/{db_file.name}", "-C", "/tmp"])

    auth = ["--username", user, "--password", password, "--authenticationDatabase", "admin"] if user else []
    exit_code, output = await _sdk_exec(container_name, ["mongorestore", f"/tmp/{folder}"] + auth)

    if exit_code != 0:
        await job.log(LogLevel.warning, f"MongoDB restore warnings: {output.decode()[:200]}")
    await job.log(LogLevel.success, "MongoDB restored")
    return True


async def restore_redis(container_name: str, db_file: Path, env_vars: list[str], job) -> bool:
    await job.log(LogLevel.info, f"Restoring Redis RDB into {container_name}")
    rdb_data = db_file.read_bytes()
    await _put_file(container_name, "/data/dump.rdb", rdb_data)
    await job.log(LogLevel.success, "Redis RDB restored")
    return True


async def restore_database(db_meta: dict, databases_dir: Path,
                           container_name: str, env_vars: list[str], job) -> bool:
    db_type = db_meta.get("type")
    db_file = databases_dir / db_meta.get("file", "")
    if not db_file.exists():
        await job.log(LogLevel.warning, f"DB dump file not found: {db_file}")
        return False

    handlers = {
        "mysql": restore_mysql,
        "mariadb": restore_mysql,
        "postgres": restore_postgres,
        "mongodb": restore_mongodb,
        "redis": restore_redis,
    }
    handler = handlers.get(db_type)
    if handler:
        return await handler(container_name, db_file, env_vars, job)
    await job.log(LogLevel.warning, f"No restore handler for db type: {db_type}")
    return False
