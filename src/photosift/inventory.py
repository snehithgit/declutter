"""Read-only, restartable inventory. Never opens a source for writing."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from PIL import Image, ImageOps, UnidentifiedImageError
from .config import Settings, path_key
from .db import connect

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # optional only for local development without HEIF
    pass

logger = logging.getLogger("photosift.inventory")
SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
DATE_IN_NAME = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_]?([01]\d)[-_]?([0-3]\d)(?!\d)")


class ScanPaused(Exception):
    """A scan cannot finish safely: root unavailable, worker stopped, or user pause."""


class ScanCancelled(Exception):
    pass


def root_available(root: Path) -> bool:
    if root.is_symlink() or not root.is_dir():
        return False
    try:
        with os.scandir(root) as entries:
            next(entries, None)  # also test directory-read permission
        return True
    except OSError:
        return False


def iter_files(root: Path) -> Iterator[Path]:
    """Walk without following symlinks; an unreadable subtree must NOT mark files missing."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = list(entries)
        except OSError as exc:
            raise ScanPaused(f"Cannot list {directory}: {exc}") from exc
        for entry in children:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError as exc:
                raise ScanPaused(f"Cannot inspect {entry.path}: {exc}") from exc


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def capture_date(name: str, mtime: float, exif: dict) -> tuple[str, str]:
    for tag in (36867, 36868, 306):
        value = exif.get(tag)
        if value:
            try:
                return datetime.strptime(str(value)[:19], "%Y:%m:%d %H:%M:%S").isoformat(), "exif"
            except ValueError:
                pass
    match = DATE_IN_NAME.search(name)
    if match:
        try:
            return datetime(*map(int, match.groups())).date().isoformat(), "filename"
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(), "mtime"


def image_metadata(path: Path) -> dict:
    """Decode only supported images; images are accessed using a read-only descriptor."""
    info: dict = {"format": path.suffix[1:].upper(), "width": None, "height": None,
                  "camera_make": None, "camera_model": None, "orientation": None,
                  "gps_present": 0, "exif": {}}
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle, Image.open(handle) as im:
        info["format"] = im.format or info["format"]
        info["width"], info["height"] = im.size
        exif = im.getexif()
        info["exif"] = exif
        info["camera_make"] = str(exif.get(271, ""))[:200] or None
        info["camera_model"] = str(exif.get(272, ""))[:200] or None
        info["orientation"] = exif.get(274)
        info["gps_present"] = int(bool(exif.get(34853)))
    return info


def make_previews(path: Path, data_dir: Path, digest: str, settings: Settings) -> tuple[str, str]:
    folder = data_dir / "thumbnails" / digest[:2]
    preview_folder = data_dir / "previews" / digest[:2]
    folder.mkdir(parents=True, exist_ok=True)
    preview_folder.mkdir(parents=True, exist_ok=True)
    thumb = folder / (digest + ".webp")
    preview = preview_folder / (digest + ".webp")
    if thumb.is_file() and preview.is_file():
        return str(thumb.relative_to(data_dir)), str(preview.relative_to(data_dir))
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle, Image.open(handle) as original:
        # Respect embedded rotation and use first frame for animated images.
        original.seek(0)
        oriented = ImageOps.exif_transpose(original)
        for output, edge in ((thumb, settings.images.thumbnail_size), (preview, settings.images.preview_size)):
            if output.is_file():
                continue
            image = oriented.copy()
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            if image.mode not in ("RGB", "RGBA"):
                image = image.convert("RGB")
            temp = output.with_name(f".{uuid.uuid4().hex}.tmp")
            try:
                image.save(temp, format="WEBP", quality=80, method=4)
                os.replace(temp, output)
            finally:
                temp.unlink(missing_ok=True)
    return str(thumb.relative_to(data_dir)), str(preview.relative_to(data_dir))


def _job_status(db: sqlite3.Connection, job_id: str) -> str:
    row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row["state"] if row else "cancelled"


def _check_controls(settings: Settings, job_id: str, root: Path, stop: object) -> None:
    if stop.is_set():
        raise ScanPaused("Worker stopping; scan will resume on restart")
    with connect(settings.data_dir) as db:
        state = _job_status(db, job_id)
    if state == "cancelled":
        raise ScanCancelled()
    if state != "running":
        raise ScanPaused("Paused by user")
    if not root_available(root):
        raise ScanPaused(f"Photo library is disconnected or unreadable: {root}")


def _record_file(settings: Settings, root_id: int, root: Path, path: Path, scan_id: str) -> None:
    relative = path.relative_to(root).as_posix()
    stat = path.stat(follow_symlinks=False)
    if not path.is_file() or path.is_symlink():
        return
    extension = path.suffix.lower()
    file_key = path_key(str(root / relative))
    with connect(settings.data_dir) as db:
        old = db.execute("SELECT size,mtime_ns,content_hash,readable FROM files WHERE root_id=? AND path_key=?",
                         (root_id, file_key)).fetchone()
    unchanged = bool(old and old["size"] == stat.st_size and old["mtime_ns"] == stat.st_mtime_ns
                     and old["content_hash"] and old["readable"] == 1)
    digest: str | None = old["content_hash"] if unchanged else None
    metadata: dict = {}
    error: str | None = None
    readable = 1
    supported = extension in SUPPORTED
    if not unchanged:
        try:
            digest = hash_file(path)
            if supported:
                metadata = image_metadata(path)
                thumb, preview = make_previews(path, settings.data_dir, digest, settings)
            else:
                thumb = preview = None
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:1000]
            readable = 0
            thumb = preview = None
            if not root_available(root):
                raise ScanPaused(f"Library disconnected while reading {path}") from exc
    else:
        thumb = preview = None
    if path.stat(follow_symlinks=False).st_size != stat.st_size or path.stat(follow_symlinks=False).st_mtime_ns != stat.st_mtime_ns:
        raise OSError(f"File changed during scan: {path}")
    stamp, source = capture_date(path.name, stat.st_mtime, metadata.get("exif", {}))
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        if digest:
            db.execute("INSERT OR IGNORE INTO contents(sha256,size) VALUES (?,?)", (digest, stat.st_size))
            if thumb or preview:
                db.execute("UPDATE contents SET thumbnail_path=COALESCE(thumbnail_path,?), "
                           "preview_path=COALESCE(preview_path,?) WHERE sha256=?", (thumb, preview, digest))
        if unchanged:
            db.execute("UPDATE files SET seen_scan_id=?,missing=0,error=NULL WHERE root_id=? AND path_key=?",
                       (scan_id, root_id, file_key))
        else:
            db.execute("""INSERT INTO files(root_id,relative_path,path_key,size,mtime_ns,ctime_ns,extension,
                         format,width,height,capture_time,date_source,camera_make,camera_model,orientation,
                         gps_present,readable,analysis_supported,missing,error,seen_scan_id,content_hash)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?)
                         ON CONFLICT(root_id,path_key) DO UPDATE SET
                         relative_path=excluded.relative_path,size=excluded.size,mtime_ns=excluded.mtime_ns,
                         ctime_ns=excluded.ctime_ns,extension=excluded.extension,format=excluded.format,
                         width=excluded.width,height=excluded.height,capture_time=excluded.capture_time,
                         date_source=excluded.date_source,camera_make=excluded.camera_make,
                         camera_model=excluded.camera_model,orientation=excluded.orientation,
                         gps_present=excluded.gps_present,readable=excluded.readable,
                         analysis_supported=excluded.analysis_supported,missing=0,error=excluded.error,
                         seen_scan_id=excluded.seen_scan_id,content_hash=excluded.content_hash""",
                       (root_id, relative, file_key, stat.st_size, stat.st_mtime_ns,
                        stat.st_ctime_ns, extension, metadata.get("format", extension[1:].upper()),
                        metadata.get("width"), metadata.get("height"), stamp, source,
                        metadata.get("camera_make"), metadata.get("camera_model"), metadata.get("orientation"),
                        metadata.get("gps_present", 0), readable, int(supported and readable),
                        error, scan_id, digest))
        db.commit()


def run_scan(settings: Settings, job_id: str, root_id: int, root: Path, stop: object) -> None:
    scan_id = uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("UPDATE jobs SET state='running',total=0,completed=0,started_at=CURRENT_TIMESTAMP,"
                   "pause_reason=NULL,last_error=NULL WHERE id=? AND state IN ('queued','running')", (job_id,))
        db.execute("UPDATE library_roots SET reachability='checking',last_error=NULL WHERE id=?", (root_id,))
    try:
        _check_controls(settings, job_id, root, stop)
        for path in iter_files(root):
            _check_controls(settings, job_id, root, stop)
            with connect(settings.data_dir) as db:
                db.execute("UPDATE jobs SET total=total+1 WHERE id=?", (job_id,))
            try:
                _record_file(settings, root_id, root, path, scan_id)
            except (OSError, ValueError) as exc:
                if not root_available(root):
                    raise ScanPaused(f"Library disconnected: {root}") from exc
                logger.exception("Unable to inventory %s", path)
                # Record a failure as a scan error; do not silently declare a complete inventory.
                raise ScanPaused(f"Error scanning {path}: {exc}") from exc
            with connect(settings.data_dir) as db:
                db.execute("UPDATE jobs SET completed=completed+1 WHERE id=?", (job_id,))
        _check_controls(settings, job_id, root, stop)
        with connect(settings.data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            # Never mark anything missing until a complete, reachable-root pass.
            if _job_status(db, job_id) != "running":
                db.rollback()
                raise ScanPaused("Paused before committing scan results")
            db.execute("UPDATE files SET missing=1 WHERE root_id=? AND (seen_scan_id IS NULL OR seen_scan_id<>?)",
                       (root_id, scan_id))
            db.execute("UPDATE jobs SET state='done',finished_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
            db.execute("UPDATE library_roots SET last_scan_at=CURRENT_TIMESTAMP,reachability='online' WHERE id=?", (root_id,))
            db.commit()
    except ScanCancelled:
        with connect(settings.data_dir) as db:
            db.execute("UPDATE jobs SET finished_at=CURRENT_TIMESTAMP WHERE id=? AND state='cancelled'", (job_id,))
    except ScanPaused as exc:
        with connect(settings.data_dir) as db:
            db.execute("UPDATE jobs SET state='paused',pause_reason=? WHERE id=? AND state='running'", (str(exc), job_id))
            if not root_available(root):
                db.execute("UPDATE library_roots SET reachability='offline',last_error=? WHERE id=?", (str(exc), root_id))
        logger.warning("Scan %s paused: %s", job_id, exc)
    except Exception as exc:
        logger.exception("Inventory job failed: %s", job_id)
        with connect(settings.data_dir) as db:
            db.execute("UPDATE jobs SET state='failed',last_error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                       (f"{type(exc).__name__}: {exc}"[:1000], job_id))


def next_scan_job(settings: Settings) -> tuple[str, int, Path] | None:
    """Only one worker holds the lease, so one claimed job is processed at a time."""
    with connect(settings.data_dir) as db:
        row = db.execute("""SELECT j.id,j.root_id,r.path FROM jobs j JOIN library_roots r ON r.id=j.root_id
                            WHERE j.kind='inventory' AND j.state IN ('running','queued')
                            ORDER BY CASE j.state WHEN 'running' THEN 0 ELSE 1 END,j.created_at LIMIT 1""").fetchone()
        return (row["id"], row["root_id"], Path(row["path"])) if row else None
