"""Restore Docker volumes and bind mounts from backup."""
import asyncio
from pathlib import Path

from ..models import LogLevel


async def restore_docker_volume(volume_name: str, archive: Path, job) -> bool:
    """Create volume and extract archive into it via SDK (no docker CLI needed)."""
    await job.log(LogLevel.info, f"Restoring volume: {volume_name}")

    loop = asyncio.get_event_loop()

    def _run():
        import docker
        import socket as _socket

        client = docker.from_env()
        api = docker.APIClient(base_url="unix://var/run/docker.sock")

        # Create volume if missing
        try:
            client.volumes.create(volume_name)
        except Exception:
            pass

        # Create container with stdin open
        host_config = api.create_host_config(
            binds={volume_name: {"bind": "/data", "mode": "rw"}}
        )
        container = api.create_container(
            image="alpine",
            command=["sh", "-c", "cd /data && tar xzf -"],
            host_config=host_config,
            stdin_open=True,
        )
        cid = container["Id"]
        api.start(cid)

        # Stream the archive from disk to container stdin in chunks —
        # never load the whole file into memory (multi-GB volumes)
        sock = api.attach_socket(cid, params={"stdin": 1, "stream": 1})
        raw = sock._sock
        with open(archive, "rb") as f:
            while chunk := f.read(65536):
                raw.sendall(chunk)
        raw.shutdown(_socket.SHUT_WR)
        raw.close()

        result = api.wait(cid)
        api.remove_container(cid, force=True)
        if result.get("StatusCode", 1) != 0:
            raise RuntimeError(f"tar exited with code {result.get('StatusCode')}")

    try:
        await loop.run_in_executor(None, _run)
    except Exception as e:
        await job.log(LogLevel.error, f"Volume restore failed for {volume_name}: {e}")
        return False

    await job.log(LogLevel.success, f"Volume {volume_name} restored")
    return True


async def restore_bind_mount(source_path: str, archive: Path, job, host_root: str = "/host") -> bool:
    """Extract bind mount archive to the host path (via /host rw mount)."""
    import os
    # Write to host via /host prefix — requires the volume to be mounted rw
    host_dest = Path(host_root) / source_path.lstrip("/")
    os.makedirs(host_dest, exist_ok=True)
    await job.log(LogLevel.info, f"Restoring bind mount: {source_path}")

    # The archive contains the original top-level dir name (tar -C parent name);
    # strip it so contents land directly in host_dest — otherwise restores nest
    # as /path/name/name, and path-remapped destinations keep the old dir name.
    proc = await asyncio.create_subprocess_exec(
        "tar", "xzf", str(archive), "--strip-components=1", "-C", str(host_dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        await job.log(LogLevel.warning, f"Bind mount restore incomplete: {stderr.decode()[:200]}")
        return False

    await job.log(LogLevel.success, f"Bind mount {source_path} restored")
    return True
