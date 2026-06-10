"""Orchestrates full restore from a backup bundle."""
import asyncio
import json
import shutil
import tarfile
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..docker_client import get_docker
from ..job_manager import Job
from ..models import LogLevel, JobStatus
from .verify import verify_backup
from .volumes import restore_docker_volume, restore_bind_mount
from .databases import restore_database


async def _recreate_networks(networks: list[dict], job):
    client = get_docker()
    existing = {n.name for n in client.networks.list()}
    for net in networks:
        name = net.get("name")
        if name in existing:
            await job.log(LogLevel.info, f"Network already exists: {name}")
            continue
        try:
            ipam_config = net.get("ipam", {})
            client.networks.create(
                name=name,
                driver=net.get("driver", "bridge"),
                options=net.get("options") or {},
                labels=net.get("labels") or {},
                internal=net.get("internal", False),
                attachable=net.get("attachable", False),
            )
            await job.log(LogLevel.success, f"Network created: {name}")
        except Exception as e:
            await job.log(LogLevel.warning, f"Network {name} creation failed: {e}")


async def _pull_or_load_image(image_name: str, images_dir: Path, job) -> bool:
    client = get_docker()

    # Try local tarball first
    container_name = image_name.replace("/", "_").replace(":", "_")
    tar_file = images_dir / f"{container_name}.tar"
    if tar_file.exists():
        await job.log(LogLevel.info, f"Loading image from archive: {tar_file.name}")
        with open(tar_file, "rb") as f:
            client.images.load(f.read())
        return True

    # Pull from registry
    await job.log(LogLevel.info, f"Pulling image: {image_name}")
    try:
        client.images.pull(image_name)
        return True
    except Exception as e:
        await job.log(LogLevel.error, f"Failed to pull {image_name}: {e}")
        return False


def _build_run_kwargs(spec: dict, name_prefix: str = "") -> tuple[dict, list[str]]:
    """Convert docker inspect spec back into docker-py run() kwargs."""
    config = spec.get("config", {})
    host_config = spec.get("host_config", {})
    network_settings = spec.get("network_settings", {})

    original_name = spec.get("name", "").lstrip("/")
    container_name = f"{name_prefix}{original_name}" if name_prefix else original_name

    kwargs = {
        "image": spec.get("image"),
        "name": container_name,
        "detach": True,
        "environment": config.get("Env") or [],
        "labels": {
            **(config.get("Labels") or {}),
            "com.uverse.comeback.prefix": name_prefix,
            "com.uverse.comeback.original": original_name,
        },
        "command": config.get("Cmd"),
        "entrypoint": config.get("Entrypoint"),
        "user": config.get("User") or None,
        "working_dir": config.get("WorkingDir") or None,
        "hostname": config.get("Hostname") or None,
        # Don't auto-restart prefixed (test) containers
        "restart_policy": {"Name": "no"} if name_prefix else
                          {"Name": (host_config.get("RestartPolicy") or {}).get("Name", "no")},
    }

    # Port bindings — skip for prefixed restores to avoid port conflicts
    if not name_prefix:
        port_bindings = host_config.get("PortBindings") or {}
        if port_bindings:
            ports = {}
            for container_port, host_bindings in port_bindings.items():
                if host_bindings:
                    ports[container_port] = [(b.get("HostIp", ""), b.get("HostPort", "")) for b in host_bindings]
                else:
                    ports[container_port] = None
            kwargs["ports"] = ports

    # Volumes / mounts — prefix docker volume names if prefix set
    mounts = spec.get("mounts", [])
    volume_list = []
    for m in mounts:
        if m.get("type") == "volume":
            vol_name = f"{name_prefix}{m['name']}" if name_prefix else m["name"]
            volume_list.append(f"{vol_name}:{m['destination']}:{m.get('mode', 'rw')}")
        elif m.get("type") == "bind":
            # In prefixed mode skip bind mounts to avoid touching live host data
            if not name_prefix:
                volume_list.append(f"{m['source']}:{m['destination']}:{m.get('mode', 'rw')}")
    if volume_list:
        kwargs["volumes"] = volume_list

    # Networks
    networks = list((network_settings.get("Networks") or {}).keys())
    primary_net = networks[0] if networks else None
    if primary_net and primary_net not in ("bridge", "host", "none"):
        kwargs["network"] = primary_net

    if host_config.get("Privileged"):
        kwargs["privileged"] = True
    if host_config.get("CapAdd"):
        kwargs["cap_add"] = host_config["CapAdd"]
    if host_config.get("Devices"):
        kwargs["devices"] = [d.get("PathInContainer") for d in host_config["Devices"]]

    return kwargs, networks


async def run_restore(
    job: Job,
    backup_archive: Path,
    container_filter: list[str] | None,
    overwrite: bool,
    start_after: bool,
    name_prefix: str = "",
):
    job.started_at = datetime.utcnow()
    job.status = JobStatus.running

    # Step 1: Verify
    await job.set_progress(5, "Verifying backup...")
    manifest = await verify_backup(backup_archive, job)

    # Step 2: Extract to temp dir
    await job.set_progress(10, "Extracting backup bundle...")
    work_dir = settings.backup_dir / f"_restore_{job.id[:8]}"
    work_dir.mkdir(exist_ok=True)

    try:
        with tarfile.open(backup_archive, "r:gz") as tar:
            tar.extractall(work_dir)

        # Find extracted backup dir
        extracted = next((d for d in work_dir.iterdir() if d.is_dir()), None)
        if not extracted:
            raise ValueError("Could not find extracted backup directory")

        containers_dir = extracted / "containers"
        volumes_dir = extracted / "volumes"
        databases_dir = extracted / "databases"
        images_dir = extracted / "images"
        networks_dir = extracted / "networks"

        client = get_docker()

        # Step 3: Networks
        await job.set_progress(15, "Recreating networks...")
        await _recreate_networks(manifest.get("networks", []), job)

        # Filter containers
        containers_to_restore = manifest.get("containers", [])
        if container_filter:
            containers_to_restore = [c for c in containers_to_restore if c["name"] in container_filter]

        total = len(containers_to_restore)
        mode_label = f"[PREFIX: {name_prefix}] " if name_prefix else ""
        await job.log(LogLevel.info, f"{mode_label}Restoring {total} container(s)")
        if name_prefix:
            await job.log(LogLevel.info,
                f"Test mode: containers → {name_prefix}<name> | "
                "Docker volumes prefixed | bind mounts skipped | ports not mapped | restart=no")

        for idx, cmeta in enumerate(containers_to_restore):
            pct = 20 + int((idx / max(total, 1)) * 65)
            cname = cmeta["name"].lstrip("/")
            effective_name = f"{name_prefix}{cname}" if name_prefix else cname
            await job.set_progress(pct, f"Restoring {effective_name}...")
            await job.log(LogLevel.info, f"[{idx+1}/{total}] Restoring: {cname} → {effective_name}")

            # Load spec
            spec_file = extracted / cmeta.get("spec_file", f"containers/{cname}.json")
            if not spec_file.exists():
                await job.log(LogLevel.error, f"Spec file missing for {cname}")
                continue

            spec = json.loads(spec_file.read_text())

            # Handle existing container (check effective name)
            try:
                existing = client.containers.get(effective_name)
                if overwrite:
                    await job.log(LogLevel.warning, f"Removing existing container: {effective_name}")
                    existing.stop(timeout=10)
                    existing.remove()
                else:
                    await job.log(LogLevel.warning, f"Container exists, skipping: {effective_name} (enable overwrite)")
                    continue
            except Exception:
                pass  # doesn't exist, fine

            # Step 4: Restore volumes for this container
            container_volumes = [v for v in manifest.get("volumes", [])
                                  if any(m.get("name") == v.get("name") or m.get("source") == v.get("source")
                                         for m in spec.get("mounts", []))]

            for vol in container_volumes:
                archive_name = vol.get("archive")
                if not archive_name:
                    continue
                archive_path = volumes_dir / archive_name
                if not archive_path.exists():
                    await job.log(LogLevel.warning, f"Volume archive missing: {archive_name}")
                    continue

                if vol.get("type") == "docker":
                    target_vol = f"{name_prefix}{vol['name']}" if name_prefix else vol["name"]
                    await restore_docker_volume(target_vol, archive_path, job)
                elif vol.get("type") == "bind" and not name_prefix:
                    await restore_bind_mount(vol["source"], archive_path, job, settings.host_root)
                elif vol.get("type") == "bind" and name_prefix:
                    await job.log(LogLevel.info, f"Skipping bind mount restore (test mode): {vol.get('source')}")

            # Step 5: Pull/load image
            image_name = spec.get("image")
            await _pull_or_load_image(image_name, images_dir, job)

            # Step 6: Create container
            try:
                run_kwargs, all_networks = _build_run_kwargs(spec, name_prefix)
                await job.log(LogLevel.info, f"Creating container {effective_name}...")
                container = client.containers.create(**run_kwargs)

                # Connect to additional networks
                for net_name in all_networks[1:]:
                    try:
                        net = client.networks.get(net_name)
                        net.connect(container)
                    except Exception as e:
                        await job.log(LogLevel.warning, f"Could not connect to network {net_name}: {e}")

                if start_after:
                    container.start()
                    await job.log(LogLevel.success, f"Container {cname} started")
                else:
                    await job.log(LogLevel.success, f"Container {cname} created (not started)")

            except Exception as e:
                await job.log(LogLevel.error, f"Container creation failed for {cname}: {e}")
                continue

            # Step 7: Restore databases (after container starts)
            if start_after:
                db_entries = [d for d in manifest.get("databases", []) if d.get("container") == cname]
                for db_meta in db_entries:
                    await restore_database(
                        db_meta, databases_dir, cname,
                        spec.get("config", {}).get("Env") or [],
                        job
                    )

        await job.set_progress(100, "Restore complete")
        await job.log(LogLevel.success, f"All containers restored from backup {manifest.get('id')}")
        await job.finish(JobStatus.success, {"restored": total, "backup_id": manifest.get("id")})

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
