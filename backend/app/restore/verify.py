"""Verify backup bundle integrity before restore."""
import hashlib
import json
import tarfile
from pathlib import Path

from ..models import LogLevel


async def verify_backup(archive_path: Path, job) -> dict:
    """Check checksum and manifest integrity. Returns manifest dict."""
    await job.log(LogLevel.info, f"Verifying {archive_path.name}")
    await job.set_progress(10, "Starting...")

    if not archive_path.exists():
        raise FileNotFoundError(f"Backup not found: {archive_path}")

    # Check sha256 sidecar
    checksum_file = archive_path.with_suffix("").with_suffix(".sha256")
    if checksum_file.exists():
        await job.set_progress(30, "Verifying checksum...")
        await job.log(LogLevel.info, "Verifying SHA-256 checksum...")
        expected_line = checksum_file.read_text().split()[0]
        h = hashlib.sha256()
        with open(archive_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected_line:
            raise ValueError(f"Checksum mismatch! Expected {expected_line}, got {actual}")
        await job.log(LogLevel.success, "Checksum OK")
    else:
        await job.log(LogLevel.warning, "No .sha256 sidecar found — skipping checksum")

    # Extract and validate manifest
    await job.set_progress(70, "Reading manifest...")
    await job.log(LogLevel.info, "Reading manifest...")
    with tarfile.open(archive_path, "r:gz") as tar:
        manifest_member = next(
            (m for m in tar.getmembers() if m.name.endswith("manifest.json")), None
        )
        if not manifest_member:
            raise ValueError("manifest.json not found in backup archive")

        f = tar.extractfile(manifest_member)
        manifest = json.loads(f.read().decode())

    containers = len(manifest.get("containers", []))
    volumes = len(manifest.get("volumes", []))
    databases = len(manifest.get("databases", []))

    await job.log(LogLevel.success,
        f"Manifest OK — {containers} containers, {volumes} volumes, {databases} databases")
    await job.log(LogLevel.info,
        f"Source host: {manifest.get('source_hostname', 'unknown')} | "
        f"Created: {manifest.get('created_at', 'unknown')}")

    return manifest
