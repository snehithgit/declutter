"""Read-only milestone-one API and built frontend hosting."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .db import connect, initialize
from .logging_setup import configure_logging
from .worker import LEASE_SECONDS


def create_app(settings: Settings, frontend_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        initialize(settings)
        configure_logging(settings.data_dir, "api")
        yield

    app = FastAPI(title="PhotoSift", version=__version__, lifespan=lifespan)
    if settings.server.host == "127.0.0.1":
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.get("/api/health")
    def health() -> dict:
        with connect(settings.data_dir) as db:
            version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            worker = db.execute("SELECT heartbeat FROM worker_leases WHERE name='main'").fetchone()
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
        alive = bool(worker and time.time() - worker["heartbeat"] < LEASE_SECONDS)
        return {"status": "ok" if alive else "degraded", "version": __version__, "milestone": "M1",
                "database": {"schema_version": version, "journal_mode": journal},
                "worker": {"state": "ready" if alive else "offline"},
                "features": {"inventory": False, "classification": False, "review": False, "export": False}}

    @app.get("/api/settings")
    def public_settings() -> dict:
        return {"data_dir": str(settings.data_dir), "library_roots": settings.library_roots,
                "ollama": settings.ollama.model_dump(), "images": settings.images.model_dump(),
                "classification": settings.classification.model_dump(), "ollama_checked": False}

    if frontend_dir and (frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:
        @app.get("/")
        def frontend_unbuilt() -> dict:
            return {"message": "Build the React frontend or start its Vite dev server.", "health": "/api/health"}
    return app
