"""M5 content-level review, filters, corrections and audit. No source-file writes."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Category = Literal["personal_photo", "screenshot", "meme_graphic", "document", "other"]
Decision = Literal["undecided", "keep", "clutter", "review_later"]
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_BULK = 100_000


class ReviewFilters(BaseModel):
    category: Category | Literal["unclassified"] | None = None
    review_status: Decision | None = None
    uncertain: bool | None = None
    important: bool | None = None
    root_id: int | None = Field(default=None, ge=1)
    folder: str = Field(default="", max_length=180)
    search: str = Field(default="", max_length=180)
    date_from: date | None = None
    date_to: date | None = None
    duplicates_only: bool = False
    blurry: bool | None = None
    low_res: bool | None = None

    @model_validator(mode="after")
    def validate_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("Start date must not be later than end date")
        return self


class ReviewChange(BaseModel):
    hashes: list[str] | None = Field(default=None, max_length=MAX_BULK)
    filters: ReviewFilters | None = None
    decision: Decision | None = None
    category_override: Category | None = None
    clear_category: bool = False

    @model_validator(mode="after")
    def validate_change(self):
        if (self.hashes is None) == (self.filters is None):
            raise ValueError("Provide either hashes or filters")
        if self.hashes is not None:
            if not self.hashes or len(set(self.hashes)) != len(self.hashes):
                raise ValueError("Hashes must be a nonempty unique list")
            if any(not DIGEST.fullmatch(d) for d in self.hashes):
                raise ValueError("Invalid content hash")
        if self.decision is None and self.category_override is None and not self.clear_category:
            raise ValueError("No changes requested")
        if self.clear_category and self.category_override is not None:
            raise ValueError("Cannot both clear and set category")
        return self


# One review record per SHA-256 content, regardless of number of source file paths.
# The latest classifier output is advisory; a manual category always wins.
BASE = """
WITH instances AS (
 SELECT f.content_hash, COUNT(*) AS copy_count,
        MIN(CASE WHEN f.analysis_supported=1 THEN f.id ELSE NULL END) sample_id
 FROM files f JOIN library_roots lr ON lr.id=f.root_id
 WHERE f.content_hash IS NOT NULL AND f.missing=0 AND lr.enabled=1
 GROUP BY f.content_hash HAVING sample_id IS NOT NULL
), latest AS (
 SELECT a.* FROM ai_results a
 JOIN (SELECT content_hash,MAX(id) max_id FROM ai_results GROUP BY content_hash) x
 ON x.max_id=a.id
)
SELECT i.content_hash,i.copy_count,f.id AS sample_file_id,f.relative_path AS sample_path,
       f.root_id AS sample_root_id,f.capture_time AS sample_capture_time,
       f.date_source AS sample_date_source,f.camera_make,f.camera_model,f.width,f.height,
       f.format,c.size AS size_bytes,c.blur_score,c.low_resolution,c.screen_aspect_ratio,
       c.thumbnail_path,c.preview_path,
       COALESCE(r.decision,'undecided') AS decision,r.category_override,r.updated_at AS reviewed_at,
       a.id AS ai_id,a.category AS ai_category,a.subcategory,a.confidence,a.reason,
       a.possibly_important,a.importance_reason,a.contains_people,a.contains_text,
       a.uncertain,a.model_tag,a.prompt_version,a.created_at AS ai_date,
       COALESCE(r.category_override,a.category) AS effective_category
FROM instances i JOIN files f ON f.id=i.sample_id
 JOIN contents c ON c.sha256=i.content_hash
 LEFT JOIN reviews r ON r.content_hash=i.content_hash
 LEFT JOIN latest a ON a.content_hash=i.content_hash
"""


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query(filters: ReviewFilters) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if filters.category == "unclassified":
        conditions.append("q.effective_category IS NULL")
    elif filters.category:
        conditions.append("q.effective_category=?")
        params.append(filters.category)
    if filters.review_status is not None:
        conditions.append("q.decision=?")
        params.append(filters.review_status)
    if filters.uncertain is not None:
        conditions.append("q.uncertain=?")
        params.append(int(filters.uncertain))
    if filters.important is not None:
        conditions.append("q.possibly_important=?")
        params.append(int(filters.important))
    if filters.duplicates_only:
        conditions.append("q.copy_count>=2")
    if filters.blurry is not None:
        conditions.append("q.blur_score IS NOT NULL AND q.blur_score " +
                          ("<100" if filters.blurry else ">=100"))
    if filters.low_res is not None:
        conditions.append("q.low_resolution=?")
        params.append(int(filters.low_res))
    # All path/date filters must match the SAME active copy, not separate copies.
    path_conditions = ["fx.content_hash=q.content_hash", "fx.missing=0", "rx.enabled=1"]
    if filters.root_id is not None:
        path_conditions.append("fx.root_id=?")
        params.append(filters.root_id)
    if filters.folder:
        path_conditions.append("fx.relative_path LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(filters.folder) + "%")
    if filters.search:
        path_conditions.append("fx.relative_path LIKE ? ESCAPE '\\'")
        params.append("%" + _escape_like(filters.search) + "%")
    if filters.date_from:
        path_conditions.append("substr(fx.capture_time,1,10)>=?")
        params.append(filters.date_from.isoformat())
    if filters.date_to:
        path_conditions.append("substr(fx.capture_time,1,10)<=?")
        params.append(filters.date_to.isoformat())
    if len(path_conditions) > 3:
        conditions.append("EXISTS (SELECT 1 FROM files fx JOIN library_roots rx ON rx.id=fx.root_id WHERE " +
                          " AND ".join(path_conditions) + ")")
    clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    return "SELECT * FROM (" + BASE + ") AS q" + clause, params


def _decorate(record: dict) -> dict:
    digest = record["content_hash"]
    record["thumbnail_url"] = f"/api/thumbnails/{digest}" if record.pop("thumbnail_path", None) else None
    record["preview_url"] = f"/api/previews/{digest}" if record.pop("preview_path", None) else None
    record["blurry"] = record["blur_score"] is not None and record["blur_score"] < 100
    return record


def review_page(db: sqlite3.Connection, filters: ReviewFilters, limit: int, offset: int) -> dict:
    sql, params = _query(filters)
    db.execute("BEGIN")
    try:
        total = db.execute("SELECT COUNT(*) FROM (" + sql + ")", params).fetchone()[0]
        rows = db.execute(sql + " ORDER BY q.sample_capture_time DESC,q.content_hash ASC LIMIT ? OFFSET ?",
                          (*params, limit, offset)).fetchall()
        result = {"total": total, "limit": limit, "offset": offset,
                  "items": [_decorate(dict(row)) for row in rows]}
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def review_summary(db: sqlite3.Connection) -> dict:
    sql, _ = _query(ReviewFilters())
    row = db.execute("SELECT COUNT(*) AS total, " +
                     ",".join(f"SUM(CASE WHEN q.decision='{d}' THEN 1 ELSE 0 END) AS {d}"
                              for d in ("keep","clutter","review_later","undecided")) +
                     ",SUM(CASE WHEN q.ai_id IS NOT NULL THEN 1 ELSE 0 END) AS classified " +
                     "FROM (" + sql + ") q").fetchone()
    return {field: int(row[field] or 0) for field in row.keys()}


def review_history(db: sqlite3.Connection, digest: str | None, limit: int, offset: int) -> dict:
    if digest is not None and not DIGEST.fullmatch(digest):
        return {"total": 0, "items": []}
    where = " WHERE content_hash=?" if digest else ""
    params = [digest] if digest else []
    total = db.execute("SELECT COUNT(*) FROM audit_log" + where, params).fetchone()[0]
    rows = [dict(row) for row in db.execute("""SELECT id,content_hash,action,before_json,after_json,created_at
        FROM audit_log""" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", (*params,limit,offset))]
    for row in rows:
        row["before"] = json.loads(row.pop("before_json") or "{}")
        row["after"] = json.loads(row.pop("after_json") or "{}")
    return {"total": total, "items": rows}


def review_detail(db: sqlite3.Connection, digest: str) -> dict | None:
    if not DIGEST.fullmatch(digest):
        return None
    sql, params = _query(ReviewFilters())
    item = db.execute(sql + " WHERE q.content_hash=?", (*params, digest)).fetchone()
    if item is None:
        return None
    result = _decorate(dict(item))
    result["copies"] = [dict(row) for row in db.execute("""SELECT f.id,r.path AS root_path,
        f.relative_path,r.enabled AS root_enabled,f.missing,f.capture_time,
        f.camera_make,f.camera_model,f.width,f.height,f.format,f.size
        FROM files f JOIN library_roots r ON r.id=f.root_id
        WHERE f.content_hash=? ORDER BY f.missing ASC,r.enabled DESC,r.path,f.relative_path
        LIMIT 300""", (digest,))]
    result["history"] = review_history(db, digest, 40, 0)["items"]
    return result


def apply_review(db: sqlite3.Connection, change: ReviewChange) -> dict:
    """Atomically validate all targets and audit each actual change."""
    db.execute("BEGIN IMMEDIATE")
    try:
        sql, params = _query(change.filters or ReviewFilters())
        if change.hashes is None:
            rows = db.execute("SELECT q.content_hash FROM (" + sql +
                              ") q ORDER BY q.content_hash LIMIT ?", (*params, MAX_BULK + 1))
            targets = [row[0] for row in rows]
            if len(targets) > MAX_BULK:
                raise ValueError(f"More than {MAX_BULK} images matched; narrow the filter")
        else:
            valid: set[str] = set()
            for pos in range(0, len(change.hashes), 400):
                batch = change.hashes[pos:pos + 400]
                placeholders = ",".join("?" for _ in batch)
                matches = db.execute("SELECT q.content_hash FROM (" + sql +
                                     f") q WHERE q.content_hash IN ({placeholders})",
                                     (*params, *batch))
                valid.update(row[0] for row in matches)
            if len(valid) != len(change.hashes):
                raise LookupError("One or more selected images are no longer eligible")
            targets = change.hashes
        changed = 0
        for digest in targets:
            record = db.execute("SELECT decision,category_override FROM reviews WHERE content_hash=?",
                                (digest,)).fetchone()
            before = {"decision": record["decision"] if record else "undecided",
                      "category_override": record["category_override"] if record else None}
            after = dict(before)
            if change.decision is not None:
                after["decision"] = change.decision
            if change.clear_category:
                after["category_override"] = None
            elif change.category_override is not None:
                after["category_override"] = change.category_override
            if after == before:
                continue
            db.execute("""INSERT INTO reviews(content_hash,decision,category_override,updated_at)
                 VALUES (?,?,?,CURRENT_TIMESTAMP)
                 ON CONFLICT(content_hash) DO UPDATE SET decision=excluded.decision,
                    category_override=excluded.category_override,updated_at=CURRENT_TIMESTAMP""",
                       (digest,after["decision"],after["category_override"]))
            db.execute("INSERT INTO audit_log(content_hash,action,before_json,after_json) VALUES (?,?,?,?)",
                       (digest,"review_update",json.dumps(before,sort_keys=True),json.dumps(after,sort_keys=True)))
            changed += 1
        db.commit()
        return {"matched":len(targets),"changed":changed}
    except Exception:
        db.rollback()
        raise
