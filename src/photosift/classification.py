"""Durable per-content Ollama classification; source files are never written."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
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
PROMPT_VERSION = "m4-v2"
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

class RetryableInferenceError(Exception):
    pass

class InvalidPrediction(Exception):
    pass


class ChoiceAnswer(BaseModel):
    model_config = ConfigDict(strict=True)
    choice: str
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class BooleanAnswer(BaseModel):
    model_config = ConfigDict(strict=True)
    noul: float = Field(ge=0, le=1, allow_inf_nan=False)


class DecisionAnswers(BaseModel):
    category: ChoiceAnswer
    subcategory: ChoiceAnswer
    contains_people: BooleanAnswer
    contains_text: BooleanAnswer
    possibly_important: BooleanAnswer


CATEGORY_CRITERIA = {
    "personal_photo": "A camera photograph of a personal memory: people, pets, places, nature or activities",
    "screenshot": "A captured phone or computer screen showing an app, website, game or conversation",
    "meme_graphic": "A meme, joke, greeting, inspirational quote, illustration or forwarded graphic",
    "document": "A receipt, ticket, certificate, form, identity document or other primarily informational record",
    "other": "An image which cannot be reliably assigned to the other categories",
}
SUBCATEGORY_CRITERIA = {
    "family": "A family or group photograph", "portrait": "A person's portrait",
    "pet": "A pet or animal", "landscape": "Nature or scenery", "travel": "Travel or sightseeing",
    "food": "Food or a meal", "event": "A celebration or event", "receipt": "A receipt or invoice",
    "ticket": "A ticket or booking", "identity_document": "An identity or official document",
    "chat": "A conversation screenshot", "app_screen": "An application or website screenshot",
    "meme": "A joke or reaction graphic", "greeting": "A greeting or motivational graphic",
    "other": "None of the above",
}


def decision_questions() -> dict:
    return {
        "category": {"type": "choice", "instructions": "Which content type best describes this image?", "criteria": CATEGORY_CRITERIA},
        "subcategory": {"type": "choice", "instructions": "Which description best fits this image?", "criteria": SUBCATEGORY_CRITERIA},
        "contains_people": {"type": "noul", "instructions": "Are people visible in the image?"},
        "contains_text": {"type": "noul", "instructions": "Is readable text visible in the image?"},
        "possibly_important": {"type": "noul", "instructions": "Could this image preserve a personal memory or useful information worth keeping, regardless of its content type?"},
    }


def call_ollama(settings: Settings, preview: Path, model: str | None = None,
                protocol: str | None = None) -> tuple[str, float]:
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
    active_protocol = protocol or settings.ollama.protocol
    endpoint = "/api/chat"
    if active_protocol == "decision":
        endpoint = "/v1/systemone"
        payload = {"model": model or settings.ollama.model,
                   "state": "Sort a private photo library. Classify visible content, not file names. Content type and importance are independent. Do not identify people.",
                   "images": [base64.b64encode(buffer.getvalue()).decode("ascii")],
                   "questions": decision_questions()}
    request = urllib.request.Request(settings.ollama.url + endpoint,
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw_body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {429, 500, 502, 503, 504}:
            raise RetryableInferenceError(f"Ollama HTTP {exc.code}") from exc
        raise ModelUnavailable(f"Ollama HTTP {exc.code}; check the configured model") from exc
    except (TimeoutError, ConnectionError, OSError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            raise RetryableInferenceError("Ollama request timed out") from exc
        raise ModelUnavailable(f"Ollama unavailable: {exc}") from exc
    if len(raw_body) > 1024 * 1024:
        raise InvalidPrediction("Ollama response exceeded 1 MiB")
    try:
        data = json.loads(raw_body)
        if "error" in data:
            raise ModelUnavailable(str(data["error"])[:250])
        if active_protocol == "decision":
            # Preserve the actual decision response, including scores, in the journal.
            return raw_body.decode("utf-8"), (time.monotonic() - start) * 1000
        text = data["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("Ollama message was not text")
        return text, (time.monotonic() - start) * 1000
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidPrediction(f"Invalid Ollama response: {exc}") from exc

def parse_prediction(raw: str) -> Prediction:
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "answers" in data:
            answers = DecisionAnswers.model_validate(data["answers"])
            important = answers.possibly_important.noul >= 0.5
            return Prediction(category=answers.category.choice, subcategory=answers.subcategory.choice,
                confidence=answers.category.confidence,
                reason=f"D3 decision: {answers.category.choice}; model confidence {answers.category.confidence:.3f}.",
                contains_people=answers.contains_people.noul >= 0.5,
                contains_text=answers.contains_text.noul >= 0.5,
                possibly_important=important,
                importance_reason=f"D3 importance score: {answers.possibly_important.noul:.3f}." if important else "")
        return Prediction.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise InvalidPrediction(str(exc)[:500]) from exc

def _folder_scope(db, root_id: int | None, folder: str) -> tuple[str, list, str | None]:
    if folder and root_id is None:
        raise ValueError("Select a library for a folder")
    if "\\" in folder or ":" in folder or "\x00" in folder or (folder and any(
            part in ("", ".", "..") for part in folder.split("/"))):
        raise ValueError("Folder must be a relative indexed folder path")
    root = None
    if root_id is not None:
        root = db.execute("SELECT path FROM library_roots WHERE id=? AND enabled=1", (root_id,)).fetchone()
        if root is None:
            raise ValueError("Library is unavailable or disabled")
    clause, args = "", []
    if root_id is not None:
        clause += " AND f.root_id=?"
        args.append(root_id)
    if folder:
        prefix = folder + "/"
        clause += " AND substr(f.relative_path,1,?)=?"
        args.extend([len(prefix), prefix])
    return clause, args, root["path"] if root else None


def classification_folders(settings: Settings, root_id: int, folder: str = "",
                           offset: int = 0, limit: int = 100) -> dict:
    with connect(settings.data_dir) as db:
        clause, args, root_path = _folder_scope(db, root_id, folder)
        base = """ FROM files f JOIN contents c ON c.sha256=f.content_hash
            WHERE f.missing=0 AND f.analysis_supported=1 AND c.preview_path IS NOT NULL""" + clause
        counts = db.execute("""SELECT COUNT(DISTINCT c.sha256) AS images,
            COUNT(DISTINCT CASE WHEN NOT EXISTS (SELECT 1 FROM ai_results a
            WHERE a.content_hash=c.sha256 AND a.model_tag=? AND a.prompt_version=?)
            THEN c.sha256 END) AS pending""" + base,
            [settings.ollama.model, PROMPT_VERSION, *args]).fetchone()
        start = len(folder) + 2 if folder else 1
        children_sql = """SELECT substr(rest,1,instr(rest,'/')-1) AS name,
            COUNT(DISTINCT content_hash) AS images FROM (
            SELECT substr(f.relative_path,?) AS rest,f.content_hash""" + base + """
            ) WHERE instr(rest,'/')>0 GROUP BY name"""
        total = db.execute("SELECT COUNT(*) FROM (" + children_sql + ")", [start, *args]).fetchone()[0]
        children = db.execute(children_sql + " ORDER BY name LIMIT ? OFFSET ?",
                              [start, *args, limit, offset]).fetchall()
        return {"root_path": root_path, "folder": folder, "images": counts["images"],
                "pending": counts["pending"], "total": total,
                "children": [{"name": r["name"], "folder": (folder + "/" if folder else "") + r["name"],
                              "images": r["images"]} for r in children]}


def queue_classification(settings: Settings, force: bool = False, limit: int | None = None,
                         root_id: int | None = None, folder: str = "") -> dict:
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        scope, scope_args, root_path = _folder_scope(db, root_id, folder)
        running = db.execute("SELECT id FROM jobs WHERE kind='classification' AND state IN ('queued','running','paused') LIMIT 1").fetchone()
        if running:
            db.rollback()
            raise ValueError(f"Finish or cancel classification job {running['id']} first")
        job_id = uuid.uuid4().hex
        params = json.dumps({"model": settings.ollama.model, "prompt_version": PROMPT_VERSION,
                             "protocol": settings.ollama.protocol, "force": force,
                             "root_id": root_id, "root_path": root_path, "folder": folder})
        db.execute("INSERT INTO jobs(id,kind,parameters_json) VALUES (?,'classification',?)", (job_id, params))
        clause = "" if force else """ AND NOT EXISTS (
            SELECT 1 FROM ai_results a WHERE a.content_hash=c.sha256 AND a.model_tag=? AND a.prompt_version=?)"""
        args = scope_args + ([] if force else [settings.ollama.model, PROMPT_VERSION])
        sql = """SELECT c.sha256 FROM contents c WHERE c.preview_path IS NOT NULL
             AND EXISTS (SELECT 1 FROM files f JOIN library_roots r ON r.id=f.root_id
             WHERE f.content_hash=c.sha256 AND f.missing=0 AND r.enabled=1 AND f.analysis_supported=1""" + scope + ")" + clause
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

def _signals(preview: Path, original_size: tuple[int, int]) -> tuple[float, int, int]:
    with Image.open(preview) as image:
        w, h = original_size
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
        if job and job["state"] == "running" and _owns_item(db, item):
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

def _owns_item(db, item: dict) -> bool:
    row = db.execute("SELECT state,worker_id FROM queue_items WHERE id=?", (item["id"],)).fetchone()
    return bool(row and row["state"] == "running" and row["worker_id"] == item["worker_id"])


def _classify_item(settings: Settings, job_id: str, snapshot: dict, item: dict, stop: object) -> None:
    digest = item["content_hash"]
    preview = _preview_path(settings, digest)
    if preview is None or not preview.is_file():
        _record_failure(settings, job_id, item, "Preview unavailable; rescan your library")
        return
    try:
        raw, duration = call_ollama(settings, preview, model=snapshot["model"],
                                   protocol=snapshot.get("protocol", "chat"))
        prediction = parse_prediction(raw)
        with connect(settings.data_dir) as db:
            dimensions = db.execute("""SELECT width,height FROM files WHERE content_hash=?
                AND width IS NOT NULL AND height IS NOT NULL ORDER BY id LIMIT 1""", (digest,)).fetchone()
        if dimensions is None:
            raise ValueError("Original image dimensions unavailable; rescan the library")
        signals = _signals(preview, (dimensions["width"], dimensions["height"]))
    except ModelUnavailable as exc:
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            if _owns_item(db, item):
                db.execute("UPDATE jobs SET state='paused',pause_reason=? WHERE id=? AND state='running'",
                           (str(exc)[:500], job_id))
            db.commit()
        return
    except InvalidPrediction as exc:
        _record_failure(settings, job_id, item, str(exc), invalid=True)
        return
    except Exception as exc:
        _record_failure(settings, job_id, item, f"Inference error: {exc}")
        return
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if stop.is_set() or not job or job["state"] != "running" or not _owns_item(db, item):
            db.rollback()
            return
        db.execute("""INSERT INTO ai_results(content_hash,model_tag,prompt_version,raw_response,
            category,subcategory,confidence,reason,contains_people,contains_text,possibly_important,
            importance_reason,uncertain,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (digest, snapshot["model"], snapshot["prompt_version"], raw, prediction.category,
             prediction.subcategory, prediction.confidence, prediction.reason,
             int(prediction.contains_people), int(prediction.contains_text),
             int(prediction.possibly_important), prediction.importance_reason,
             int(prediction.confidence < settings.classification.uncertainty_threshold or prediction.category == "other"), duration))
        db.execute("UPDATE contents SET blur_score=?,low_resolution=?,screen_aspect_ratio=? WHERE sha256=?", (*signals,digest))
        db.execute("UPDATE queue_items SET state='done',finished_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?", (item["id"],))
        db.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
        db.commit()


def run_classification(settings: Settings, job_id: str, stop: object) -> None:
    """Bounded parallel inference; each owned result is committed immediately."""
    owner = uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        job = db.execute("SELECT state,parameters_json FROM jobs WHERE id=? AND kind='classification'", (job_id,)).fetchone()
        if not job or job["state"] not in ("queued", "running"):
            db.rollback()
            return
        snapshot = json.loads(job["parameters_json"])
        db.execute("UPDATE queue_items SET state='queued',worker_id=NULL WHERE job_id=? AND state='running'", (job_id,))
        db.execute("""UPDATE jobs SET state='running', started_at=COALESCE(started_at,CURRENT_TIMESTAMP),
            pause_reason=NULL WHERE id=?""", (job_id,))
        db.commit()
    try:
        with ThreadPoolExecutor(max_workers=settings.ollama.concurrency) as pool:
            while not stop.is_set():
                with connect(settings.data_dir) as db:
                    db.execute("BEGIN IMMEDIATE")
                    job = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
                    if not job or job["state"] != "running":
                        db.rollback()
                        return
                    rows = db.execute("""SELECT * FROM queue_items WHERE job_id=?
                        AND state IN ('queued','failed_retryable') AND available_at<=CURRENT_TIMESTAMP
                        ORDER BY available_at,id LIMIT ?""", (job_id, settings.ollama.concurrency)).fetchall()
                    items = []
                    for row in rows:
                        item = dict(row)
                        item["attempts"] += 1
                        item["worker_id"] = owner
                        db.execute("""UPDATE queue_items SET state='running',attempts=attempts+1,
                            worker_id=?,started_at=CURRENT_TIMESTAMP WHERE id=?""", (owner, item["id"]))
                        items.append(item)
                    if not items:
                        pending = db.execute("""SELECT COUNT(*) FROM queue_items WHERE job_id=?
                            AND state IN ('queued','running','failed_retryable')""", (job_id,)).fetchone()[0]
                        if not pending:
                            db.execute("UPDATE jobs SET state='done',finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
                        db.commit()
                        if not pending:
                            return
                    else:
                        db.commit()
                if not items:
                    time.sleep(0.2)
                    continue
                futures = [pool.submit(_classify_item, settings, job_id, snapshot, item, stop) for item in items]
                for future in futures:
                    future.result()
    finally:
        # A paused/cancelled/stopped request cannot publish a stale result. Only
        # claims owned by this run are released; a successor's claims stay intact.
        with connect(settings.data_dir) as db:
            db.execute("""UPDATE queue_items SET state='queued',worker_id=NULL,attempts=MAX(attempts-1,0)
                WHERE job_id=? AND state='running' AND worker_id=?""", (job_id, owner))


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
