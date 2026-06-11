"""Auth: login, sessions, brute-force lockout, bcrypt and middleware."""
import bcrypt
import pytest

from app import auth as auth_module
from app.config import settings


def _login(client, password="secret123", username="admin"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def test_status_reports_instance_and_auth_disabled(client):
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    data = r.json()
    assert data["auth_enabled"] is False
    assert data["authenticated"] is True
    assert data["instance_name"] == "test-instance"


def test_api_open_when_auth_disabled(client):
    # /api/jobs requires no Docker, good probe for the middleware
    assert client.get("/api/jobs").status_code == 200


def test_api_blocked_without_session(client):
    settings.auth_password = "secret123"
    assert client.get("/api/jobs").status_code == 401


def test_login_and_access(client):
    settings.auth_password = "secret123"
    assert _login(client).status_code == 200
    assert client.get("/api/jobs").status_code == 200


def test_wrong_password_includes_remaining_attempts(client):
    settings.auth_password = "secret123"
    r = _login(client, password="nope")
    assert r.status_code == 401
    assert "4" in r.json()["detail"]


def test_lockout_after_five_failures(client):
    settings.auth_password = "secret123"
    for _ in range(4):
        assert _login(client, password="nope").status_code == 401
    assert _login(client, password="nope").status_code == 429
    # correct credentials are also rejected while locked
    assert _login(client).status_code == 429


def test_success_resets_failure_counter(client):
    settings.auth_password = "secret123"
    for _ in range(3):
        _login(client, password="nope")
    assert _login(client).status_code == 200
    auth_module._sessions.clear()
    r = _login(client, password="nope")
    assert "4" in r.json()["detail"]


def test_logout_invalidates_session(auth_client):
    assert auth_client.get("/api/jobs").status_code == 200
    auth_client.post("/api/auth/logout")
    assert auth_client.get("/api/jobs").status_code == 401


def test_bcrypt_hash_login(client):
    settings.auth_password_hash = bcrypt.hashpw(
        b"hashed-pass", bcrypt.gensalt(rounds=4)).decode()
    assert _login(client, password="wrong").status_code == 401
    assert _login(client, password="hashed-pass").status_code == 200


def test_auth_routes_exempt_from_middleware(client):
    settings.auth_password = "secret123"
    assert client.get("/api/auth/status").status_code == 200


@pytest.mark.parametrize("delay", [0.0])
def test_failed_login_has_delay_configured(delay, monkeypatch):
    # The 1s anti-brute-force delay must exist (we don't wait for it in tests)
    assert auth_module.FAIL_DELAY_SECONDS >= 1.0


# ─── change password ─────────────────────────────────────────────────────────

def test_change_password_full_flow(auth_client):
    r = auth_client.post("/api/auth/change-password", json={
        "current_password": "secret123", "new_password": "nuevaclave9"})
    assert r.status_code == 200
    # stored hash file created and takes precedence over env password
    assert (auth_module._password_file()).exists()
    auth_module._sessions.clear()
    assert _login(auth_client, password="secret123").status_code == 401
    auth_module._failures.clear()
    assert _login(auth_client, password="nuevaclave9").status_code == 200


def test_change_password_requires_auth(client):
    from app.config import settings
    settings.auth_password = "secret123"
    r = client.post("/api/auth/change-password", json={
        "current_password": "secret123", "new_password": "nuevaclave9"})
    assert r.status_code == 401


def test_change_password_validates(auth_client):
    r = auth_client.post("/api/auth/change-password", json={
        "current_password": "WRONG", "new_password": "nuevaclave9"})
    assert r.status_code == 401
    r = auth_client.post("/api/auth/change-password", json={
        "current_password": "secret123", "new_password": "corta"})
    assert r.status_code == 400


def test_system_endpoint_reports_version(auth_client):
    from app.config import APP_VERSION
    r = auth_client.get("/api/system")
    assert r.status_code == 200
    assert r.json()["version"] == APP_VERSION
    assert r.json()["instance_name"] == "test-instance"


def test_system_endpoint_requires_auth(client):
    from app.config import settings
    settings.auth_password = "secret123"
    assert client.get("/api/system").status_code == 401
