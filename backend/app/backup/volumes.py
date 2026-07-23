"""Backup Docker named volumes and bind mounts."""
import asyncio
from pathlib import Path

from ..models import LogLevel


async def backup_docker_volume(volume_name: str, dest_dir: Path, job) -> dict:
    """Tar the volume via an alpine helper, streaming stdout to disk in chunks.

    The stream never lives fully in memory, so multi-GB volumes are safe.
    No docker CLI needed — uses the low-level APIClient attach stream.
    """
    archive = dest_dir / f"{volume_name}.tar.gz"
    await job.log(LogLevel.info, f"Backing up volume: {volume_name}")

    loop = asyncio.get_event_loop()

    def _run() -> int:
        import docker
        api = docker.APIClient(base_url="unix://var/run/docker.sock")

        host_config = api.create_host_config(
            binds={volume_name: {"bind": "/data", "mode": "ro"}}
        )
        container = api.create_container(
            image="alpine",
            command=["tar", "czf", "-", "-C", "/data", "."],
            host_config=host_config,
        )
        cid = container["Id"]
        try:
            # Attach before start so no early output is missed
            stream = api.attach(cid, stdout=True, stderr=False, stream=True, logs=True)
            api.start(cid)
            written = 0
            with open(archive, "wb") as f:
                for chunk in stream:
                    f.write(chunk)
                    written += len(chunk)
            result = api.wait(cid)
            if result.get("StatusCode", 1) != 0:
                raise RuntimeError(f"tar exited with code {result.get('StatusCode')}")
            return written
        finally:
            api.remove_container(cid, force=True)

    size = await loop.run_in_executor(None, _run)
    await job.log(LogLevel.success, f"Volume {volume_name} backed up ({size // 1024} KB)")
    return {"name": volume_name, "type": "docker", "archive": archive.name, "size": size}


async def backup_bind_mount(source_path: str, dest_dir: Path, job, host_root: str = "/host") -> dict | None:
    """Tar a host bind mount path (accessed via /host prefix inside container)."""
    from pathlib import Path as P
    host_path = P(host_root) / source_path.lstrip("/")
    if not host_path.exists():
        await job.log(LogLevel.warning, f"Bind mount not accessible: {source_path}")
        return None

    safe_name = source_path.replace("/", "_").strip("_") + ".tar.gz"
    archive = dest_dir / safe_name
    await job.log(LogLevel.info, f"Backing up bind mount: {source_path}")

    proc = await asyncio.create_subprocess_exec(
        "tar", "czf", str(archive), "-C", str(host_path.parent), host_path.name,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        await job.log(LogLevel.warning, f"Bind mount backup incomplete: {stderr.decode()[:200]}")
        return None

    size = archive.stat().st_size if archive.exists() else 0
    await job.log(LogLevel.success, f"Bind mount {source_path} backed up ({size // 1024} KB)")
    return {"source": source_path, "type": "bind", "archive": safe_name, "size": size}


async def backup_all_volumes(
    container_spec: dict,
    dest_dir: Path,
    job,
    host_root: str,
    excluded_bind_mounts: list[str] | None = None,
) -> list[dict]:
    results = []
    seen: set[str] = set()
    excluded = set(excluded_bind_mounts or [])

    for mount in container_spec.get("mounts", []):
        mtype = mount.get("type")
        if mtype == "volume":
            name = mount.get("name")
            if name and name not in seen:
                seen.add(name)
                try:
                    r = await backup_docker_volume(name, dest_dir, job)
                    r["destination"] = mount.get("destination")
                    results.append(r)
                except Exception as e:
                    await job.log(LogLevel.error, f"Failed volume {name}: {e}")

        elif mtype == "bind":
            source = mount.get("source", "")
            if source and source not in seen:
                seen.add(source)
                if source in excluded:
                    await job.log(LogLevel.warning,
                        f"Bind mount excluido (configuración de backup): {source}")
                    results.append({
                        "type": "bind", "source": source,
                        "destination": mount.get("destination"),
                        "excluded": True,
                    })
                    continue
                try:
                    r = await backup_bind_mount(source, dest_dir, job, host_root)
                    if r:
                        r["destination"] = mount.get("destination")
                        results.append(r)
                except Exception as e:
                    await job.log(LogLevel.error, f"Failed bind mount {source}: {e}")

    return results
