import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app import auth as auth_module
from app.main import app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path):
    """Point storage at a temp dir and reset auth state for every test."""
    settings.backup_path = str(tmp_path / "backups")
    settings.auth_username = "admin"
    settings.auth_password = ""
    settings.auth_password_hash = ""
    settings.instance_name = "test-instance"
    auth_module._sessions.clear()
    auth_module._failures.clear()
    # job history must not leak between tests
    from app.job_manager import job_manager
    job_manager._jobs.clear()
    job_manager._loaded = False
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    """Client with auth enabled and already logged in."""
    settings.auth_password = "secret123"
    r = client.post("/api/auth/login",
                    json={"username": "admin", "password": "secret123"})
    assert r.status_code == 200
    return client
