"""Single-user session auth with brute-force protection.

Auth is enabled by setting AUTH_PASSWORD (and optionally AUTH_USERNAME).
If AUTH_PASSWORD is empty, the API is open — a warning is logged at startup.

Brute-force protection (per client IP, in-memory — fine with --workers 1):
  - every failed attempt adds a ~1s delay before responding
  - after MAX_FAILURES failed attempts the IP is locked for LOCKOUT_MINUTES
  - a successful login clears the counter
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger("comeback.auth")

COOKIE_NAME = "comeback_session"
CSRF_COOKIE = "comeback_csrf"
SESSION_HOURS = 24
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15
FAIL_DELAY_SECONDS = 1.0

_sessions: dict[str, datetime] = {}            # token → expiry (UTC)
_failures: dict[str, dict] = {}                # ip → {count, locked_until}


def _password_file():
    return settings.backup_dir / ".auth.json"


def _stored_hash() -> str:
    """Password changed from the UI — bcrypt hash stored on the backups volume.
    Takes precedence over both env vars so the change survives recreations."""
    try:
        import json
        return json.loads(_password_file().read_text()).get("password_hash", "")
    except Exception:
        return ""


def set_password(new_plain: str):
    import bcrypt
    import json
    _password_file().parent.mkdir(parents=True, exist_ok=True)
    _password_file().write_text(json.dumps({
        "password_hash": bcrypt.hashpw(new_plain.encode(), bcrypt.gensalt()).decode(),
    }))


def auth_enabled() -> bool:
    return bool(settings.auth_password or settings.auth_password_hash or _stored_hash())


def _verify_password(plain: str) -> bool:
    """Precedence: UI-stored hash → AUTH_PASSWORD_HASH → plain AUTH_PASSWORD."""
    stored = _stored_hash() or settings.auth_password_hash
    if stored:
        try:
            import bcrypt
            return bcrypt.checkpw(plain.encode(), stored.encode())
        except Exception:
            return False
    return secrets.compare_digest(plain, settings.auth_password)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    _sessions[token] = {"expiry": _now() + timedelta(hours=SESSION_HOURS), "csrf": csrf}
    return token, csrf


def validate_session(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token:
        return False
    sess = _sessions.get(token)
    if not sess:
        return False
    if _now() > sess["expiry"]:
        _sessions.pop(token, None)
        return False
    sess["expiry"] = _now() + timedelta(hours=SESSION_HOURS)
    return True


def validate_csrf(request: Request) -> bool:
    """Double-submit cookie: X-CSRF-Token header must match the csrf cookie value."""
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE_NAME)
    sess = _sessions.get(token) if token else None
    if not sess:
        return False
    header_csrf = request.headers.get("X-CSRF-Token", "")
    return secrets.compare_digest(header_csrf, sess["csrf"])


def destroy_session(token: str | None):
    if token:
        _sessions.pop(token, None)


def is_authenticated(request: Request) -> bool:
    return validate_session(request.cookies.get(COOKIE_NAME))


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _lock_state(ip: str) -> tuple[bool, int]:
    """Returns (locked, seconds_remaining)."""
    info = _failures.get(ip)
    if not info or not info.get("locked_until"):
        return False, 0
    remaining = (info["locked_until"] - _now()).total_seconds()
    if remaining <= 0:
        _failures.pop(ip, None)
        return False, 0
    return True, int(remaining)


def _register_failure(ip: str) -> tuple[int, int]:
    """Returns (failures, remaining_attempts). Locks the IP at MAX_FAILURES."""
    info = _failures.setdefault(ip, {"count": 0, "locked_until": None})
    info["count"] += 1
    if info["count"] >= MAX_FAILURES:
        info["locked_until"] = _now() + timedelta(minutes=LOCKOUT_MINUTES)
        logger.warning("IP %s locked for %d min after %d failed logins",
                       ip, LOCKOUT_MINUTES, info["count"])
    return info["count"], max(MAX_FAILURES - info["count"], 0)


# ─── API routes ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/status")
def auth_status(request: Request):
    return {
        "auth_enabled": auth_enabled(),
        "authenticated": is_authenticated(request),
        # shown on the login screen and in the header so you always know
        # which installation you are talking to
        "instance_name": settings.effective_instance_name,
    }


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response):
    if not auth_enabled():
        return {"ok": True, "detail": "Auth disabled"}

    ip = _client_ip(request)
    locked, remaining = _lock_state(ip)
    if locked:
        return JSONResponse(status_code=429, content={
            "detail": f"Demasiados intentos. Bloqueado durante {remaining // 60 + 1} min.",
            "locked_seconds": remaining,
        })

    user_ok = secrets.compare_digest(body.username, settings.auth_username)
    pass_ok = _verify_password(body.password)
    if not (user_ok and pass_ok):
        await asyncio.sleep(FAIL_DELAY_SECONDS)
        count, attempts_left = _register_failure(ip)
        logger.warning("Failed login for %r from %s (%d/%d)",
                       body.username, ip, count, MAX_FAILURES)
        if attempts_left == 0:
            return JSONResponse(status_code=429, content={
                "detail": f"Demasiados intentos. Bloqueado durante {LOCKOUT_MINUTES} min.",
            })
        return JSONResponse(status_code=401, content={
            "detail": f"Credenciales incorrectas ({attempts_left} intentos restantes).",
        })

    _failures.pop(ip, None)
    token, csrf = create_session()
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax", path="/",
        max_age=SESSION_HOURS * 3600,
    )
    response.set_cookie(
        CSRF_COOKIE, csrf,
        httponly=False, samesite="lax", path="/",
        max_age=SESSION_HOURS * 3600,
    )
    logger.info("Login OK from %s", ip)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    destroy_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request, response: Response):
    # /api/auth/* is exempt from the middleware — enforce the session here
    if not is_authenticated(request):
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    if not auth_enabled():
        return JSONResponse(status_code=400, content={
            "detail": "Sin autenticación activa — define AUTH_PASSWORD primero"})
    if not _verify_password(body.current_password):
        await asyncio.sleep(FAIL_DELAY_SECONDS)
        return JSONResponse(status_code=401, content={"detail": "Contraseña actual incorrecta"})
    if len(body.new_password) < 8:
        return JSONResponse(status_code=400, content={
            "detail": "La nueva contraseña debe tener al menos 8 caracteres"})

    set_password(body.new_password)
    _sessions.clear()
    token, csrf = create_session()
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                        path="/", max_age=SESSION_HOURS * 3600)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="lax",
                        path="/", max_age=SESSION_HOURS * 3600)
    logger.info("Password changed from %s — all other sessions invalidated",
                _client_ip(request))
    return {"ok": True}
