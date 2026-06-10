from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from .api import containers, backup, restore, jobs, cleanup, deploy

app = FastAPI(title="uverse comeback", version="1.0.0", docs_url="/api/docs")

app.include_router(containers.router)
app.include_router(backup.router)
app.include_router(restore.router)
app.include_router(jobs.router)
app.include_router(cleanup.router)
app.include_router(deploy.router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def spa(path: str = ""):
    index = static_dir / "index.html"
    return FileResponse(str(index))
