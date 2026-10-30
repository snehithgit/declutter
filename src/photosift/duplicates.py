"""Exact duplicates derived from the existing SHA-256 content identity.

Only known-present files count toward duplicate groups. Historical missing
instances are retained in a group's details, never treated as deletions.
This module reads the inventory database only; no source file is opened.
"""
from __future__ import annotations

import sqlite3

# Existing files(content_hash) index supports grouping and per-content lookups.
# Do not persist a second, potentially stale grouping table.
_GROUPS = """
    SELECT content_hash, COUNT(*) AS copy_count, MIN(id) AS example_file_id
    FROM files
    WHERE missing=0 AND content_hash IS NOT NULL
    GROUP BY content_hash
    HAVING COUNT(*) >= 2
"""

SORTS = {
    "space": "potential_reclaim_bytes DESC, copy_count DESC, g.content_hash ASC",
    "copies": "copy_count DESC, potential_reclaim_bytes DESC, g.content_hash ASC",
    "size": "size_bytes DESC, copy_count DESC, g.content_hash ASC",
}


def _as_dicts(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _urls(item: dict) -> dict:
    digest = item["content_hash"]
    item["thumbnail_url"] = f"/api/thumbnails/{digest}" if item.pop("thumbnail_path", None) else None
    item["preview_url"] = f"/api/previews/{digest}" if item.pop("preview_path", None) else None
    return item


def duplicate_summary(db: sqlite3.Connection) -> dict:
    row = db.execute(f"""
        SELECT COUNT(*) AS groups, COALESCE(SUM(copy_count), 0) AS copies,
               COALESCE(SUM(copy_count - 1), 0) AS extra_copies,
               COALESCE(SUM((copy_count - 1) * c.size), 0) AS potential_reclaim_bytes
        FROM ({_GROUPS}) AS g JOIN contents AS c ON c.sha256=g.content_hash
    """).fetchone()
    return dict(row)


def list_duplicate_groups(db: sqlite3.Connection, limit: int, offset: int, sort: str = "space") -> dict:
    # Fixed allowlist: never interpolate arbitrary client-provided sort SQL.
    order = SORTS[sort]
    summary = duplicate_summary(db)
    rows = _as_dicts(db.execute(f"""
        SELECT g.content_hash, g.copy_count, (g.copy_count - 1) AS extra_copies,
               c.size AS size_bytes, ((g.copy_count - 1) * c.size) AS potential_reclaim_bytes,
               c.thumbnail_path, c.preview_path,
               f.relative_path AS sample_path, r.path AS sample_root, f.format AS sample_format
        FROM ({_GROUPS}) AS g
        JOIN contents AS c ON c.sha256=g.content_hash
        JOIN files AS f ON f.id=g.example_file_id
        JOIN library_roots AS r ON r.id=f.root_id
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, (limit, offset)))
    return {"total": summary["groups"], "items": [_urls(row) for row in rows],
            "limit": limit, "offset": offset, "sort": sort}


def duplicate_group_files(db: sqlite3.Connection, digest: str, limit: int, offset: int) -> dict | None:
    content = db.execute("""
        SELECT c.sha256 AS content_hash, c.size AS size_bytes, c.thumbnail_path, c.preview_path,
               (SELECT COUNT(*) FROM files f WHERE f.content_hash=c.sha256 AND f.missing=0) AS copy_count,
               (SELECT COUNT(*) FROM files f WHERE f.content_hash=c.sha256 AND f.missing=1) AS missing_count
        FROM contents c WHERE c.sha256=?
    """, (digest,)).fetchone()
    if content is None or content["copy_count"] < 2:
        return None
    rows = _as_dicts(db.execute("""
        SELECT f.id, f.root_id, r.path AS root_path, f.relative_path, f.size,
               f.format, f.width, f.height, f.capture_time, f.date_source,
               f.missing, f.readable, f.error
        FROM files f JOIN library_roots r ON r.id=f.root_id
        WHERE f.content_hash=?
        ORDER BY f.missing ASC, r.path COLLATE NOCASE ASC,
                 f.relative_path COLLATE NOCASE ASC, f.id ASC
        LIMIT ? OFFSET ?
    """, (digest, limit, offset)))
    payload = _urls(dict(content))
    payload["extra_copies"] = payload["copy_count"] - 1
    payload["potential_reclaim_bytes"] = payload["extra_copies"] * payload["size_bytes"]
    payload["total"] = payload["copy_count"] + payload["missing_count"]
    payload["items"] = rows
    payload["limit"] = limit
    payload["offset"] = offset
    return payload
