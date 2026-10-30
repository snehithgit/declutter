"""Durable per-content Ollama classification; source files are never written."""
from __future__ import annotations
import base64
import io
import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Literal
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .config import Settings
from .db import connect

logger = logging.getLogger("photosift.classification")
PROMPT_VERSION = "m4-v1"
PROMPT = ("Classify the image, not its filename. Return only JSON matching the schema. "
          "Image type and importance are independent: receipts and screenshots may be important. "
          "If uncertain, choose other with low confidence. Do not identify people.")
MAX_ATTEMPTS = 3

class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: Literal["personal_photo", "screenshot", "meme_graphic", "document", "other"]
    subcategory: str = Field(max_length=100)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(max_length=500)
    contains_people: bool
    contains_text: bool
    possibly_important: bool
    importance_reason: str = Field(max_length=300)

class ModelUnavailable(Exception):
    pass

class InvalidPrediction(Exception):
    pass

def call_ollama(settings: Settings, preview: Path, model: str | None = None) -> tuple[str, float]:
    """Use only cached previews over LAN. Originals are not opened or transferred."""
    if preview.is_symlink() or not preview.is_file():
        raise FileNotFoundError("Preview missing; rescan the library")
    with Image.open(preview) as original:
        image = original.convert("RGB")
        image.thumbnail((settings.images.preview_size, settings.images.preview_size))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=82)
    payload = {
        "model": model or settings.ollama.model, "stream": False,
        "format": Prediction.model_json_schema(),
        "messages": [{"role": "user", "content": PROMPT,
                      "images": [base64.b64encode(buffer.getvalue()).decode("ascii")]}],
        "options": {"temperature": 0}
    }
    request = urllib.request.Request(settings.ollama.url + "/api/chat",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw_body = response.read(1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ModelUnavailable(f"Ollama unavailable: {exc}") from exc
    if len(raw_body) > 1024 * 1024:
        raise InvalidPrediction("Ollama response exceeded 1 MiB")
    try:
        data = json.loads(raw_body)
        if "error" in data:
            raise ModelUnavailable(str(data["error"])[:250])
        text = data["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("Ollama message was not text")
        return text, (time.monotonic() - start) * 1000
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidPrediction(f"Invalid Ollama response: {exc}") from exc

def parse_prediction(raw: str) -> Prediction:
    try:
        return Prediction.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise InvalidPrediction(str(exc)[:500]) from exc

def queue_classification(settings: Settings, force: bool = False, limit: int | None = None) -> dict:
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        running = db.execute("SELECT id FROM jobs WHERE kind='classification' AND state IN ('queued','running','paused') LIMIT 1").fetchone()
        if running:
            db.rollback()
            raise ValueError(f"Finish or cancel classification job {running['id']} first")
        job_id = uuid.uuid4().hex
        params = json.dumps({"model": settings.ollama.model, "prompt_version": PROMPT_VERSION, "force": force})
        db.execute("INSERT INTO jobs(id,kind,parameters_json) VALUES (?,'classification',?)", (job_id, params))
        clause = "" if force else """ AND NOT EXISTS (
            SELECT 1 FROM ai_results a WHERE a.content_hash=c.sha256 AND a.model_tag=? AND a.prompt_version=?)"""
        args = [] if force else [settings.ollama.model, PROMPT_VERSION]
        sql = """SELECT c.sha256 FROM contents c WHERE c.preview_path IS NOT NULL
             AND EXISTS (SELECT 1 FROM files f JOIN library_roots r ON r.id=f.root_id
             WHERE f.content_hash=c.sha256 AND f.missing=0 AND r.enabled=1 AND f.analysis_supported=1)""" + clause
        sql += " ORDER BY c.sha256" + (" LIMIT ?" if limit is not None else "")
        hashes = db.execute(sql, (*args, limit) if limit is not None else args).fetchall()
        for row in hashes:
            db.execute("INSERT INTO queue_items(job_id,content_hash) VALUES (?,?)", (job_id, row["sha256"]))
        state = "queued" if hashes else "done"
        db.execute("UPDATE jobs SET state=?,total=?,finished_at=CASE WHEN ?='done' THEN CURRENT_TIMESTAMP ELSE NULL END WHERE id=?",
                   (state, len(hashes), state, job_id))
        db.commit()
        return {"job_id": job_id, "queued": len(hashes)}

def next_classification_job(settings: Settings) -> str | None:
    with connect(settings.data_dir) as db:
        row = db.execute("""SELECT id FROM jobs WHERE kind='classification'
             AND state IN ('queued','running') ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,
             created_at,id LIMIT 1""").fetchone()
        return row["id"] if row else None

def _preview_path(settings: Settings, digest: str) -> Path | None:
    with connect(settings.data_dir) as db:
        row = db.execute("SELECT preview_path FROM contents WHERE sha256=?", (digest,)).fetchone()
    expected = (settings.data_dir / "previews" / digest[:2] / (digest + ".webp")).resolve()
    if row and row["preview_path"] and (settings.data_dir / row["preview_path"]).resolve() == expected:
        return expected
    return None

def _signals(preview: Path) -> tuple[float, int, int]:
    with Image.open(preview) as image:
        w, h = image.size
        low_resolution = int(min(w, h) < 480)
        ratio = max(w, h) / max(1, min(w, h))
        screen_aspect = int(1.7 < ratio < 2.35)
        gray = image.convert("L").resize((96,96))
        pixels = list(gray.get_flattened_data()) if hasattr(gray,"get_flattened_data") else list(gray.getdata())
    values = []
    for y in range(1,95):
        for x in range(1,95):
            i = y*96+x
            values.append(4*pixels[i]-pixels[i-1]-pixels[i+1]-pixels[i-96]-pixels[i+96])
    mean = sum(values)/len(values)
    variance = sum((v-mean)**2 for v in values)/len(values)
    return variance, low_resolution, screen_aspect

def _record_failure(settings: Settings, job_id: str, item: dict, message: str, invalid: bool = False) -> None:
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if job and job["state"] == "running":
            invalid_count = item["invalid_response_attempts"] + int(invalid)
            terminal = invalid_count >= 2 if invalid else item["attempts"] >= MAX_ATTEMPTS
            state = "failed_permanent" if terminal else "failed_retryable"
            db.execute("""UPDATE queue_items SET state=?,invalid_response_attempts=?,error=?,
                available_at=datetime('now',?),finished_at=CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END
                WHERE id=? AND state='running'""",
                (state, invalid_count, message[:500], f"+{min(2**item['attempts'],60)} seconds", int(terminal), item["id"]))
            if terminal:
                db.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
        db.commit()

def run_classification(settings: Settings, job_id: str, stop: object) -> None:
    """A single leased worker owns the queue. Each result commits independently."""
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT state,parameters_json FROM jobs WHERE id=? AND kind='classification'", (job_id,)).fetchone()
        if not job or job["state"] not in ("queued","running"):
            db.rollback()
            return
        snapshot = json.loads(job["parameters_json"])
        db.execute("UPDATE queue_items SET state='queued' WHERE job_id=? AND state='running'", (job_id,))
        db.execute("""UPDATE jobs SET state='running',
             started_at=COALESCE(started_at,CURRENT_TIMESTAMP),pause_reason=NULL WHERE id=?""", (job_id,))
        db.commit()
    while not stop.is_set():
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["state"] != "running":
                db.rollback()
                return
            item = db.execute("""SELECT * FROM queue_items WHERE job_id=?
               AND state IN ('queued','failed_retryable') AND available_at<=CURRENT_TIMESTAMP
               ORDER BY available_at,id LIMIT 1""", (job_id,)).fetchone()
            if item is None:
                pending = db.execute("""SELECT COUNT(*) FROM queue_items WHERE job_id=?
                    AND state IN ('queued','running','failed_retryable')""", (job_id,)).fetchone()[0]
                if not pending:
                    db.execute("UPDATE jobs SET state='done',finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
                db.commit()
                if pending:
                    time.sleep(1)
                    continue
                return
            item = dict(item)
            db.execute("UPDATE queue_items SET state='running',attempts=attempts+1,started_at=CURRENT_TIMESTAMP WHERE id=?", (item["id"],))
            db.commit()
        item["attempts"] += 1
        digest = item["content_hash"]
        preview = _preview_path(settings, digest)
        if preview is None or not preview.is_file():
            _record_failure(settings, job_id, item, "Preview unavailable; rescan your library")
            continue
        try:
            raw, duration = call_ollama(settings, preview, model=snapshot["model"])
        except ModelUnavailable as exc:
            with connect(settings.data_dir) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE queue_items SET state='queued',attempts=MAX(attempts-1,0) WHERE id=? AND state='running'", (item["id"],))
                db.execute("UPDATE jobs SET state='paused',pause_reason=? WHERE id=? AND state='running'", (str(exc)[:500], job_id))
                db.commit()
            return
        except InvalidPrediction as exc:
            _record_failure(settings, job_id, item, str(exc), invalid=True)
            continue
        except Exception as exc:
            _record_failure(settings, job_id, item, f"Inference error: {exc}")
            continue
        try:
            prediction = parse_prediction(raw)
            signals = _signals(preview)
        except InvalidPrediction as exc:
            _record_failure(settings, job_id, item, str(exc), invalid=True)
            continue
        except Exception as exc:
            _record_failure(settings, job_id, item, f"Preview error: {exc}")
            continue
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job or job["state"] != "running":
                db.rollback()
                return
            db.execute("""INSERT INTO ai_results(content_hash,model_tag,prompt_version,raw_response,
                category,subcategory,confidence,reason,contains_people,contains_text,possibly_important,
                importance_reason,uncertain,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (digest, snapshot["model"], snapshot["prompt_version"], raw, prediction.category,
                 prediction.subcategory, prediction.confidence, prediction.reason,
                 int(prediction.contains_people), int(prediction.contains_text),
                 int(prediction.possibly_important), prediction.importance_reason,
                 int(prediction.confidence < settings.classification.uncertainty_threshold or prediction.category=="other"),duration))
            db.execute("UPDATE contents SET blur_score=?,low_resolution=?,screen_aspect_ratio=? WHERE sha256=?", (*signals,digest))
            db.execute("UPDATE queue_items SET state='done',finished_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?", (item["id"],))
            db.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
            db.commit()
    # Claimed items are reset to queued when the restarted worker resumes this job.

def classification_jobs(settings: Settings) -> list[dict]:
    with connect(settings.data_dir) as db:
        rows = [dict(row) for row in db.execute("""SELECT id,state,total,completed,created_at,
           started_at,finished_at,pause_reason,last_error,parameters_json FROM jobs WHERE kind='classification'
           ORDER BY created_at DESC,id DESC LIMIT 20""")]
        for job in rows:
            job["parameters"] = json.loads(job.pop("parameters_json"))
            job["counts"] = {r["state"]:r["n"] for r in db.execute(
                "SELECT state,COUNT(*) n FROM queue_items WHERE job_id=? GROUP BY state", (job["id"],))}
            started = db.execute("SELECT unixepoch(?)", (job["started_at"],)).fetchone()[0] if job["started_at"] else None
            elapsed = max(1,time.time()-started) if started is not None else None
            job["rate_per_minute"] = round(60*job["completed"]/elapsed,2) if elapsed and job["completed"] else 0
            job["eta_seconds"] = round((job["total"]-job["completed"])*elapsed/job["completed"]) if (
                elapsed and job["completed"] and job["state"]=="running") else None
        return rows

def recent_predictions(settings: Settings, limit: int = 30) -> list[dict]:
    with connect(settings.data_dir) as db:
        return [dict(row) for row in db.execute("""SELECT a.id,a.content_hash,a.category,
            a.subcategory,a.confidence,a.reason,a.possibly_important,a.uncertain,a.duration_ms,
            a.created_at,a.model_tag,CASE WHEN c.thumbnail_path IS NOT NULL
            THEN '/api/thumbnails/'||a.content_hash ELSE NULL END thumbnail_url
            FROM ai_results a JOIN contents c ON c.sha256=a.content_hash ORDER BY a.id DESC LIMIT ?""", (limit,))]
