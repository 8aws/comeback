"""Backup archive upload (migrations)."""
import io
import json
import tarfile

from app.config import settings


def _make_backup_bytes(name="backup_20260101_000000_abcd1234", label=None) -> bytes:
    """Minimal valid comeback archive: dir with manifest.json inside."""
    buf = io.BytesIO()
    manifest = json.dumps({
        "id": "abcd1234", "label": label, "containers": [{"name": "x"}],
        "volumes": [], "databases": [],
    }).encode()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"{name}/manifest.json")
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    return buf.getvalue()


def test_upload_valid_backup(auth_client):
    data = _make_backup_bytes()
    r = auth_client.post("/api/backups/upload",
                         files={"file": ("backup_test.tar.gz", data, "application/gzip")})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "backup_test"
    assert body["container_count"] == 1
    assert (settings.backup_dir / "backup_test.tar.gz").exists()
    sidecar = (settings.backup_dir / "backup_test.sha256").read_text()
    assert body["checksum"] in sidecar


def test_upload_rejects_non_targz(auth_client):
    r = auth_client.post("/api/backups/upload",
                         files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_garbage_archive(auth_client):
    r = auth_client.post("/api/backups/upload",
                         files={"file": ("fake.tar.gz", b"not a tarball", "application/gzip")})
    assert r.status_code == 400
    # the temp file must not linger
    assert not list(settings.backup_dir.glob("*.uploading"))


def test_upload_duplicate_conflict(auth_client):
    data = _make_backup_bytes()
    files = {"file": ("dup.tar.gz", data, "application/gzip")}
    assert auth_client.post("/api/backups/upload", files=files).status_code == 200
    assert auth_client.post("/api/backups/upload", files=files).status_code == 409


def test_upload_strips_path_components(auth_client):
    data = _make_backup_bytes()
    r = auth_client.post("/api/backups/upload",
                         files={"file": ("../../evil.tar.gz", data, "application/gzip")})
    assert r.status_code == 200
    assert (settings.backup_dir / "evil.tar.gz").exists()
    assert not (settings.backup_dir.parent / "evil.tar.gz").exists()


def test_upload_requires_auth(client):
    settings.auth_password = "secret123"
    r = client.post("/api/backups/upload",
                    files={"file": ("x.tar.gz", b"x", "application/gzip")})
    assert r.status_code == 401
