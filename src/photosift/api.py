"""Read-only photo browsing plus explicit inventory job controls."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Settings
from .db import connect, initialize
from .logging_setup import configure_logging
from .worker import LEASE_SECONDS


class ScanRequest(BaseModel):
    root_id: int | None = None


class JobAction(BaseModel):
    action: Literal["pause", "resume", "cancel"]


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


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
        return {"status": "ok" if alive else "degraded", "version": __version__, "milestone": "M2",
                "database": {"schema_version": version, "journal_mode": journal},
                "worker": {"state": "ready" if alive else "offline"},
                "features": {"inventory": True, "classification": False, "review": False, "export": False}}

    @app.get("/api/settings")
    def public_settings() -> dict:
        return {"data_dir": str(settings.data_dir), "library_roots": settings.library_roots,
                "ollama": settings.ollama.model_dump(), "images": settings.images.model_dump(),
                "classification": settings.classification.model_dump(), "ollama_checked": False}

    @app.get("/api/inventory")
    def inventory() -> dict:
        with connect(settings.data_dir) as db:
            totals = dict(db.execute("""SELECT COUNT(*) AS files,COALESCE(SUM(size),0) AS bytes,
                   COALESCE(SUM(CASE WHEN missing=1 THEN 1 ELSE 0 END),0) AS missing,
                   COALESCE(SUM(CASE WHEN readable=0 THEN 1 ELSE 0 END),0) AS unreadable,
                   COALESCE(SUM(CASE WHEN analysis_supported=0 THEN 1 ELSE 0 END),0) AS unsupported,
                   COUNT(DISTINCT content_hash) AS unique_contents FROM files""").fetchone())
            formats = _rows(db.execute("SELECT COALESCE(NULLIF(format,''),'UNKNOWN') AS format,COUNT(*) AS count "
                                       "FROM files GROUP BY format ORDER BY count DESC LIMIT 30"))
            roots = _rows(db.execute("""SELECT r.id,r.path,r.enabled,r.last_scan_at,r.reachability,r.last_error,
                                  COUNT(f.id) AS files FROM library_roots r LEFT JOIN files f ON f.root_id=r.id
                                  GROUP BY r.id ORDER BY r.id"""))
        return {"totals": totals, "formats": formats, "roots": roots}

    @app.get("/api/files")
    def list_files(limit: int = Query(default=60, ge=1, le=120), offset: int = Query(default=0, ge=0),
                   root_id: int | None = None, search: str = Query(default="", max_length=160),
                   format: str = Query(default="", max_length=20), missing: bool | None = None) -> dict:
        conditions = []
        params: list[object] = []
        if root_id is not None:
            conditions.append("f.root_id=?")
            params.append(root_id)
        if search:
            conditions.append("f.relative_path LIKE ? ESCAPE '\\'")
            params.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if format:
            conditions.append("UPPER(f.format)=?")
            params.append(format.upper())
        if missing is not None:
            conditions.append("f.missing=?")
            params.append(int(missing))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with connect(settings.data_dir) as db:
            total = db.execute("SELECT COUNT(*) FROM files f" + where, params).fetchone()[0]
            items = _rows(db.execute("""SELECT f.id,f.root_id,f.relative_path,f.size,f.format,f.width,f.height,
                                 f.capture_time,f.date_source,f.camera_make,f.camera_model,f.gps_present,
                                 f.readable,f.analysis_supported,f.missing,f.error,f.content_hash,
                                 c.thumbnail_path,c.preview_path FROM files f LEFT JOIN contents c
                                 ON c.sha256=f.content_hash""" + where +
                                 " ORDER BY f.missing ASC,f.id DESC LIMIT ? OFFSET ?", (*params, limit, offset)))
        for item in items:
            digest = item["content_hash"]
            item["thumbnail_url"] = f"/api/thumbnails/{digest}" if digest and item["thumbnail_path"] else None
            item["preview_url"] = f"/api/previews/{digest}" if digest and item["preview_path"] else None
            item.pop("thumbnail_path")
            item.pop("preview_path")
        return {"total": total, "items": items, "limit": limit, "offset": offset}

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        with connect(settings.data_dir) as db:
            return _rows(db.execute("""SELECT j.id,j.kind,j.root_id,r.path AS root_path,j.state,j.total,
                          j.completed,j.created_at,j.started_at,j.finished_at,j.pause_reason,j.last_error
                          FROM jobs j LEFT JOIN library_roots r ON r.id=j.root_id
                          WHERE j.kind='inventory' ORDER BY j.created_at DESC,j.rowid DESC LIMIT 30"""))

    @app.post("/api/scans", status_code=201)
    def start_scan(request: ScanRequest) -> dict:
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            selected = _rows(db.execute("SELECT id FROM library_roots WHERE enabled=1" +
                                        (" AND id=?" if request.root_id is not None else ""),
                                        (request.root_id,) if request.root_id is not None else ()))
            if not selected:
                db.rollback()
                raise HTTPException(404, "No enabled photo library roots found")
            ids = [row["id"] for row in selected]
            placeholders = ",".join("?" for _ in ids)
            active = db.execute("SELECT id,root_id FROM jobs WHERE kind='inventory' "
                                "AND state IN ('queued','running','paused') AND root_id IN (" + placeholders + ") LIMIT 1", ids).fetchone()
            if active:
                db.rollback()
                raise HTTPException(409, f"Scan already exists for root {active['root_id']}; resume or cancel that job")
            job_ids = []
            for root_id in ids:
                job_id = uuid.uuid4().hex
                db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (job_id, root_id))
                job_ids.append(job_id)
            db.commit()
        return {"job_ids": job_ids}

    @app.post("/api/jobs/{job_id}/action")
    def change_job(job_id: str, request: JobAction) -> dict:
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE id=? AND kind='inventory'", (job_id,)).fetchone()
            if row is None:
                db.rollback()
                raise HTTPException(404, "Scan not found")
            old = row["state"]
            next_states = {"pause": ({"running", "queued"}, "paused"),
                           "resume": ({"paused", "failed"}, "queued"),
                           "cancel": ({"running", "queued", "paused", "failed"}, "cancelled")}
            allowed, new = next_states[request.action]
            if old not in allowed:
                db.rollback()
                raise HTTPException(409, f"Cannot {request.action} a {old} scan")
            db.execute("UPDATE jobs SET state=?,pause_reason=NULL,last_error=NULL,"
                       "finished_at=CASE WHEN ?='cancelled' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
                       (new, new, job_id))
            db.commit()
        return {"id": job_id, "state": new}

    def media(digest: str, kind: Literal["thumbnails", "previews"]) -> FileResponse:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise HTTPException(404, "Image not found")
        column = "thumbnail_path" if kind == "thumbnails" else "preview_path"
        with connect(settings.data_dir) as db:
            row = db.execute(f"SELECT {column} AS filename FROM contents WHERE sha256=?", (digest,)).fetchone()
        if not row or not row["filename"]:
            raise HTTPException(404, "Image not found")
        expected = (settings.data_dir / kind / digest[:2] / (digest + ".webp")).resolve()
        actual = (settings.data_dir / row["filename"]).resolve()
        if actual != expected or not actual.is_file():
            raise HTTPException(404, "Image not found")
        return FileResponse(actual, media_type="image/webp", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/thumbnails/{digest}")
    def thumbnail(digest: str) -> FileResponse:
        return media(digest, "thumbnails")

    @app.get("/api/previews/{digest}")
    def preview(digest: str) -> FileResponse:
        return media(digest, "previews")

    if frontend_dir and (frontend_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
    else:
        @app.get("/")
        def frontend_unbuilt() -> dict:
            return {"message": "Build the React frontend or start its Vite dev server.", "health": "/api/health"}
    return app
