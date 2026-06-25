"""FastAPI application entrypoint.

Run:  .venv/bin/uvicorn app.main:app --port 8787 --reload   (or ./run.sh)
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import settings
from .db import init_db

app = FastAPI(title="Pipeline Console", version="0.1.0")
app.include_router(router, prefix="/api")


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


# Serve the SPA at "/". Registered last so /api/* routes win.
settings.WEB_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(settings.WEB_DIR), html=True), name="web")
