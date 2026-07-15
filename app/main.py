"""FastAPI application entrypoint.

Run:  .venv/bin/uvicorn app.main:app --port 8787 --reload   (or ./run.sh)
"""
import asyncio

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .db import init_db
from .history import init_history_db
from .scheduler import snapshot_scheduler

app = FastAPI(title="Pipeline Console", version="0.1.0")
app.include_router(router, prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    await init_history_db()
    asyncio.create_task(snapshot_scheduler())


class RevalidatedStatics(StaticFiles):
    """StaticFiles that forbids stale caching. Without Cache-Control, browsers
    heuristically cache the SPA's JS modules for hours after a deploy (new
    index.html + old settings.js = missing panels until a hard refresh).
    `no-cache` = cache but ALWAYS revalidate: unchanged files still 304, but a
    fresh deploy shows up on a normal reload."""
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# Serve the SPA at "/". Registered last so /api/* routes win.
settings.WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", RevalidatedStatics(directory=str(settings.WEB_DIR), html=True), name="web")
