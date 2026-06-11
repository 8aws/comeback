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
SESSION_HOURS = 24
MAX_FAILURES = 5
LOCKOUT_MINUTES = 15
FAIL_DELAY_SECONDS = 1.0

_sessions: dict[str, datetime] = {}            # token → expiry (UTC)
_failures: dict[str, dict] = {}                # ip → {count, locked_until}


def auth_enabled() -> bool:
    return bool(settings.auth_password or settings.auth_password_hash)


def _verify_password(plain: str) -> bool:
    """AUTH_PASSWORD_HASH (bcrypt) takes precedence over plain AUTH_PASSWORD."""
    if settings.auth_password_hash:
        try:
            import bcrypt
            return bcrypt.checkpw(plain.encode(), settings.auth_password_hash.encode())
        except Exception:
            return False
    return secrets.compare_digest(plain, settings.auth_password)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = _now() + timedelta(hours=SESSION_HOURS)
    return token


def validate_session(token: str | None) -> bool:
    if not auth_enabled():
        return True
    if not token:
        return False
    expiry = _sessions.get(token)
    if not expiry:
        return False
    if _now() > expiry:
        _sessions.pop(token, None)
        return False
    # sliding expiry — active sessions stay alive
    _sessions[token] = _now() + timedelta(hours=SESSION_HOURS)
    return True


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
    token = create_session()
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax", path="/",
        max_age=SESSION_HOURS * 3600,
    )
    logger.info("Login OK from %s", ip)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    destroy_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}
