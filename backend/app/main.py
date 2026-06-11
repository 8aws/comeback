import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from . import auth
import asyncio

from .api import containers, backup, restore, jobs, cleanup, deploy, schedules, updates
from .scheduler import scheduler_loop

logger = logging.getLogger("comeback")

app = FastAPI(title="uverse comeback", version="1.0.0", docs_url="/api/docs")


@app.middleware("http")
async def require_auth(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        if not auth.is_authenticated(request):
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    return await call_next(request)


@app.on_event("startup")
async def on_startup():
    if not auth.auth_enabled():
        logger.warning(
            "AUTH_PASSWORD is not set — the API is OPEN without authentication. "
            "Set AUTH_PASSWORD (and optionally AUTH_USERNAME) to enable login."
        )
    asyncio.create_task(scheduler_loop())


app.include_router(auth.router)
app.include_router(containers.router)
app.include_router(backup.router)
app.include_router(restore.router)
app.include_router(jobs.router)
app.include_router(cleanup.router)
app.include_router(deploy.router)
app.include_router(updates.router)
app.include_router(schedules.router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def spa(path: str = ""):
    index = static_dir / "index.html"
    return FileResponse(str(index))
