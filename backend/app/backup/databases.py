"""Auto-detect and dump databases from running containers via Docker SDK (no CLI required)."""
import asyncio
import gzip
from pathlib import Path

from ..docker_client import get_docker
from ..models import LogLevel


def _env_dict(env_vars: list[str]) -> dict[str, str]:
    result = {}
    for e in env_vars:
        if "=" in e:
            k, v = e.split("=", 1)
            result[k] = v
    return result


def _sdk_exec(container_name: str, cmd: list[str]) -> tuple[int, bytes, bytes]:
    """Run a command inside a container via SDK exec_run."""
    client = get_docker()
    c = client.containers.get(container_name)
    result = c.exec_run(cmd, stdout=True, stderr=True, demux=True)
    exit_code = result.exit_code
    stdout = result.output[0] or b"" if result.output else b""
    stderr = result.output[1] or b"" if result.output else b""
    return exit_code, stdout, stderr


async def _run_exec(container_name: str, cmd: list[str]) -> tuple[int, bytes, bytes]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sdk_exec, container_name, cmd)


async def dump_mysql(container_name: str, env: dict, dest_dir: Path, job) -> dict | None:
    password = env.get("MYSQL_ROOT_PASSWORD") or env.get("MARIADB_ROOT_PASSWORD", "")
    dump_file = dest_dir / f"mysql_{container_name}.sql.gz"
    await job.log(LogLevel.info, f"Dumping MySQL/MariaDB from {container_name}")

    cmd = ["mysqldump", "-uroot", f"-p{password}",
           "--all-databases", "--single-transaction", "--routines", "--triggers", "--events"]

    exit_code, stdout, stderr = await _run_exec(container_name, cmd)
    if exit_code != 0:
        await job.log(LogLevel.warning, f"MySQL dump may be incomplete: {stderr.decode()[:200]}")
        if not stdout:
            return None

    dump_file.write_bytes(gzip.compress(stdout))
    size = dump_file.stat().st_size
    await job.log(LogLevel.success, f"MySQL dump done ({size // 1024} KB)")
    return {"type": "mysql", "container": container_name, "file": dump_file.name, "size": size}


async def dump_postgres(container_name: str, env: dict, dest_dir: Path, job) -> dict | None:
    user = env.get("POSTGRES_USER", "postgres")
    password = env.get("POSTGRES_PASSWORD", "")
    dump_file = dest_dir / f"postgres_{container_name}.sql.gz"
    await job.log(LogLevel.info, f"Dumping PostgreSQL from {container_name}")

    cmd = ["pg_dumpall", "-U", user]
    exit_code, stdout, stderr = await _run_exec(container_name, ["sh", "-c",
        f"PGPASSWORD={password} pg_dumpall -U {user}"])

    if exit_code != 0:
        await job.log(LogLevel.warning, f"PostgreSQL dump may be incomplete: {stderr.decode()[:200]}")
        if not stdout:
            return None

    dump_file.write_bytes(gzip.compress(stdout))
    size = dump_file.stat().st_size
    await job.log(LogLevel.success, f"PostgreSQL dump done ({size // 1024} KB)")
    return {"type": "postgres", "container": container_name, "file": dump_file.name, "size": size}


async def dump_mongodb(container_name: str, env: dict, dest_dir: Path, job) -> dict | None:
    user = env.get("MONGO_INITDB_ROOT_USERNAME", "")
    password = env.get("MONGO_INITDB_ROOT_PASSWORD", "")
    archive_name = f"mongo_{container_name}.tar.gz"
    dump_file = dest_dir / archive_name
    tmp_dump = f"/tmp/comeback_mongodump_{container_name}"
    tmp_archive = f"/tmp/{archive_name}"
    await job.log(LogLevel.info, f"Dumping MongoDB from {container_name}")

    auth = ["--username", user, "--password", password, "--authenticationDatabase", "admin"] if user else []
    exit_code, _, stderr = await _run_exec(container_name, ["mongodump", "--out", tmp_dump] + auth)
    if exit_code != 0:
        await job.log(LogLevel.warning, f"mongodump failed: {stderr.decode()[:200]}")
        return None

    await _run_exec(container_name, ["tar", "czf", tmp_archive, "-C", "/tmp",
                                      f"comeback_mongodump_{container_name}"])

    # Copy archive out of container using SDK get_archive
    loop = asyncio.get_event_loop()
    def _copy_out():
        client = get_docker()
        c = client.containers.get(container_name)
        bits, _ = c.get_archive(tmp_archive)
        # get_archive returns a tar stream containing the file; extract it
        import io, tarfile
        buf = b"".join(bits)
        with tarfile.open(fileobj=io.BytesIO(buf)) as tf:
            member = tf.getmembers()[0]
            member.name = archive_name
            extracted = tf.extractfile(member)
            dump_file.write_bytes(extracted.read())

    await loop.run_in_executor(None, _copy_out)
    size = dump_file.stat().st_size if dump_file.exists() else 0
    await job.log(LogLevel.success, f"MongoDB dump done ({size // 1024} KB)")
    return {"type": "mongodb", "container": container_name, "file": archive_name, "size": size}


async def dump_redis(container_name: str, env: dict, dest_dir: Path, job) -> dict | None:
    dump_file = dest_dir / f"redis_{container_name}.rdb"
    await job.log(LogLevel.info, f"Saving Redis RDB from {container_name}")

    exit_code, _, _ = await _run_exec(container_name, ["redis-cli", "BGSAVE"])
    if exit_code != 0:
        await job.log(LogLevel.warning, f"Redis BGSAVE failed for {container_name}")
        return None

    await asyncio.sleep(2)

    loop = asyncio.get_event_loop()
    def _copy_rdb():
        import io, tarfile
        client = get_docker()
        c = client.containers.get(container_name)
        bits, _ = c.get_archive("/data/dump.rdb")
        buf = b"".join(bits)
        with tarfile.open(fileobj=io.BytesIO(buf)) as tf:
            extracted = tf.extractfile(tf.getmembers()[0])
            dump_file.write_bytes(extracted.read())

    await loop.run_in_executor(None, _copy_rdb)
    size = dump_file.stat().st_size if dump_file.exists() else 0
    await job.log(LogLevel.success, f"Redis RDB saved ({size // 1024} KB)")
    return {"type": "redis", "container": container_name, "file": dump_file.name, "size": size}


async def dump_database(container_name: str, db_type: str, env_vars: list[str],
                        dest_dir: Path, job) -> dict | None:
    env = _env_dict(env_vars)
    handlers = {
        "mysql": dump_mysql,
        "mariadb": dump_mysql,
        "postgres": dump_postgres,
        "mongodb": dump_mongodb,
        "redis": dump_redis,
    }
    handler = handlers.get(db_type)
    if handler:
        try:
            return await handler(container_name, env, dest_dir, job)
        except Exception as e:
            await job.log(LogLevel.error, f"DB dump error ({db_type}): {e}")
    return None
