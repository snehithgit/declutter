"""M6 copy-only exports: immutable plans, explicit approval and verified rollback."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Protocol

from .config import Settings
from .db import connect

SAFE_NAME = re.compile(r"[^a-zA-Z0-9_\-. ()\[\]@]+")
CATEGORIES = {
    "personal_photo": "Photos", "document": "Documents",
    "screenshot": "Screenshots", "meme_graphic": "Memes-and-Graphics", "other": "Other",
}
COPY_CHUNK = 1024 * 1024


class ExportError(ValueError):
    pass


def export_root(settings: Settings) -> Path | None:
    """An explicit, separate, already-mounted writable destination is required."""
    root = settings.export_dir
    if root is None:
        return None
    if root.is_symlink() or not root.is_dir():
        raise ExportError("Export destination must be an existing, non-symlink directory")
    root = root.resolve()
    for source in [settings.data_dir, *map(Path, settings.library_roots)]:
        source = source.resolve()
        if root == source or root in source.parents or source in root.parents:
            raise ExportError("Export destination cannot overlap photo libraries or app data")
    if not os.access(root, os.W_OK):
        raise ExportError("Export destination is not writable")
    return root


def _source(root: str, relative: str) -> Path:
    library = Path(root)
    if library.is_symlink():
        raise ExportError("Source library is a symlink")
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ExportError("Invalid source path in inventory")
    path = library / name
    if any(segment.is_symlink() for segment in (library, *[library.joinpath(*name.parts[:i])
                                                       for i in range(1, len(name.parts) + 1)])):
        raise ExportError("Source path contains a symlink")
    if not path.resolve().is_relative_to(library.resolve()):
        raise ExportError("Source escapes library")
    return path


def _relative_destination(category: str, decision: str, stamp: str, original: str) -> Path:
    name = SAFE_NAME.sub("_", Path(original).name).strip(" .")
    if not name or name.startswith("."):
        name = "photo" + (Path(original).suffix.lower() or ".bin")
    if len(name) > 170:
        suffix = Path(name).suffix[:15]
        name = name[:150] + suffix
    if decision == "clutter":
        folder = Path("Clutter")
    elif decision == "review_later":
        folder = Path("Organized/Review-Later")
    elif category == "personal_photo":
        year = stamp[:4] if len(stamp) >= 10 else "Unknown"
        month = stamp[:7] if len(stamp) >= 10 else "Unknown"
        folder = Path("Organized/Photos") / year / month
    else:
        folder = Path("Organized") / CATEGORIES.get(category, "Other")
    return folder / name


def _plan_rows(db: sqlite3.Connection, include_clutter: bool) -> list[sqlite3.Row]:
    return db.execute("""SELECT f.id AS file_id, r.path AS root_path, f.relative_path,
        f.content_hash AS digest, f.size, f.mtime_ns, f.capture_time, f.date_source,
        v.decision, COALESCE(v.category_override, a.category,'other') AS category,
        COALESCE(a.reason,'') AS reason
      FROM reviews v
      JOIN files f ON f.id=(
        SELECT MIN(f2.id) FROM files f2 JOIN library_roots r2 ON r2.id=f2.root_id
        WHERE f2.content_hash=v.content_hash AND f2.missing=0 AND r2.enabled=1
          AND f2.readable=1)
      JOIN library_roots r ON r.id=f.root_id
      LEFT JOIN ai_results a ON a.id=(SELECT MAX(id) FROM ai_results
                                      WHERE content_hash=v.content_hash)
      WHERE v.decision IN ('keep','review_later'""" +
      (",'clutter'" if include_clutter else "") +
      """) ORDER BY v.content_hash""").fetchall()


def create_plan(settings: Settings, include_clutter: bool = False) -> dict:
    root = export_root(settings)
    if root is None:
        raise ExportError("Configure a separate export directory before creating a plan")
    run_id = uuid.uuid4().hex
    destination_root = root / ("PhotoSift-" + run_id)
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            rows = _plan_rows(db, include_clutter)
            if not rows:
                raise ExportError("No reviewed images available. Mark photos Keep or Review Later first")
            settings_json = json.dumps({"include_clutter": include_clutter, "version": 1})
            db.execute("INSERT INTO export_runs(id,destination,state,settings_json) "
                       "VALUES (?,?,'planned',?)", (run_id, str(destination_root), settings_json))
            reserved: set[str] = set()
            total = 0
            for row in rows:
                # Root and file names originate from inventory. Validate before journaling.
                source = _source(row["root_path"], row["relative_path"])
                relative = _relative_destination(row["category"], row["decision"],
                                                 row["capture_time"] or "", row["relative_path"])
                path = destination_root / relative
                base = path
                index = 1
                while str(path).casefold() in reserved or path.exists():
                    path = base.with_name(f"{base.stem}-{row['digest'][:10]}-{index}{base.suffix}")
                    index += 1
                reserved.add(str(path).casefold())
                total += row["size"]
                db.execute("""INSERT INTO export_operations(export_id,file_id,source,destination,
                    expected_hash,expected_size,category,decision,model_reason,capture_time,date_source)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id,row["file_id"],str(source),str(path),row["digest"],row["size"],
                     row["category"],row["decision"],row["reason"],row["capture_time"],
                     row["date_source"] or "mtime"))
            db.commit()
            return {"id": run_id, "state": "planned", "destination": str(destination_root),
                    "files": len(rows), "total_bytes": total}
        except Exception:
            db.rollback()
            raise


def _run(db: sqlite3.Connection, run_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM export_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise LookupError("Export run not found")
    return row


def export_list(settings: Settings) -> list[dict]:
    with connect(settings.data_dir) as db:
        return [dict(row) for row in db.execute("""SELECT e.id,e.destination,e.state,
            e.created_at,e.approved_at,COUNT(o.id) AS files,
            COALESCE(SUM(o.expected_size),0) AS total_bytes,
            SUM(CASE WHEN o.state='done' THEN 1 ELSE 0 END) AS copied,
            SUM(CASE WHEN o.state='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN o.state='rolled_back' THEN 1 ELSE 0 END) AS rolled_back
            FROM export_runs e LEFT JOIN export_operations o ON o.export_id=e.id
            GROUP BY e.id ORDER BY e.created_at DESC,e.id DESC LIMIT 40""")]


def export_detail(settings: Settings, run_id: str, limit: int = 100, offset: int = 0) -> dict:
    with connect(settings.data_dir) as db:
        run = dict(_run(db, run_id))
        counts = db.execute("""SELECT COUNT(*) files,COALESCE(SUM(expected_size),0) total_bytes,
            SUM(CASE WHEN state='done' THEN 1 ELSE 0 END) copied,
            SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END) failed
            FROM export_operations WHERE export_id=?""", (run_id,)).fetchone()
        run.update(dict(counts))
        run["operations"] = [dict(row) for row in db.execute("""SELECT id,source,destination,
            expected_hash,expected_size,category,decision,model_reason,
            capture_time,date_source,state,error,verified_hash,created_by_export
            FROM export_operations WHERE export_id=? ORDER BY id LIMIT ? OFFSET ?""",
            (run_id,limit,offset))]
        run["limit"],run["offset"] = limit,offset
        run.pop("settings_json",None)
        return run


def set_export_state(settings: Settings, run_id: str, action: str, confirmation: str) -> dict:
    root = export_root(settings)
    if root is None:
        raise ExportError("Export destination not configured")
    expected = {"approve": ("planned", "approved", "COPY APPROVED"),
                "retry": ("done_with_errors", "approved", "RETRY FAILED"),
                "rollback": ("done", "rollback_requested", "ROLLBACK COPIES"),
                "rollback_errors": ("done_with_errors", "rollback_requested", "ROLLBACK COPIES"),
                "cancel": ("planned", "cancelled", "CANCEL EXPORT")}
    if action not in expected:
        raise ExportError("Unknown export action")
    old, new, token = expected[action]
    if confirmation != token:
        raise ExportError(f"Confirm with the exact phrase: {token}")
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = _run(db,run_id)
            if row["state"] != old:
                raise ExportError(f"Cannot {action} export in state {row['state']}")
            if Path(row["destination"]).parent.resolve() != root:
                raise ExportError("Export destination configuration changed; refusing action")
            db.execute("UPDATE export_runs SET state=?,approved_at=CASE WHEN ?='approved' "
                       "THEN CURRENT_TIMESTAMP ELSE approved_at END WHERE id=?", (new,new,run_id))
            if action == "retry":
                db.execute("UPDATE export_operations SET state='planned',error=NULL WHERE export_id=? AND state='failed'",(run_id,))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"id":run_id,"state":new}


def next_export_job(settings: Settings) -> tuple[str,str] | None:
    with connect(settings.data_dir) as db:
        row = db.execute("""SELECT id,state FROM export_runs
            WHERE state IN ('approved','running','rollback_requested')
            ORDER BY created_at,id LIMIT 1""").fetchone()
        return (row["id"],row["state"]) if row else None


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os,"O_BINARY",0) | getattr(os,"O_NOFOLLOW",0)
    with os.fdopen(os.open(path,flags),"rb") as handle:
        while data := handle.read(COPY_CHUNK):
            h.update(data)
    return h.hexdigest()


def _safe_destination(root: Path, destination: Path, create: bool = False) -> None:
    if not destination.is_relative_to(root) or destination == root:
        raise ExportError("Export operation escapes its run directory")
    if root.is_symlink():
        raise ExportError("Export run folder is a symlink")
    # Validate existing parents BEFORE mkdir so an existing symlink can never
    # redirect directory creation outside the configured export folder.
    for ancestor in (destination.parent,*destination.parent.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ExportError("Export destination contains a symlink")
    if create:
        destination.parent.mkdir(parents=True,exist_ok=True)
    for ancestor in (destination.parent,*destination.parent.parents):
        if ancestor == root.parent:
            break
        if ancestor.is_symlink():
            raise ExportError("Export destination contains a symlink")
    if destination.is_symlink():
        raise ExportError("Destination is a symlink")


def _copy_one(run_root: Path, row: sqlite3.Row) -> str:
    src = Path(row["source"])
    dst = Path(row["destination"])
    _safe_destination(run_root,dst,create=True)
    if dst.exists():
        raise ExportError("Destination already exists; never overwrite it")
    if not src.is_file() or src.is_symlink():
        raise ExportError("Source missing or symlinked since planning")
    stat = src.stat(follow_symlinks=False)
    if stat.st_size != row["expected_size"]:
        raise ExportError("Source size changed since planning")
    if _hash(src) != row["expected_hash"]:
        raise ExportError("Source hash changed since planning")
    tmp = dst.with_name("." + dst.name + "." + uuid.uuid4().hex + ".partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os,"O_NOFOLLOW",0)
    try:
        with os.fdopen(os.open(tmp,flags,0o600),"wb") as out:
            read_flags = os.O_RDONLY | getattr(os,"O_BINARY",0) | getattr(os,"O_NOFOLLOW",0)
            with os.fdopen(os.open(src,read_flags),"rb") as input_file:
                while data := input_file.read(COPY_CHUNK):
                    out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if tmp.stat().st_size != row["expected_size"] or _hash(tmp) != row["expected_hash"]:
            raise ExportError("Copied file failed size/hash verification")
        import shutil
        shutil.copystat(src,tmp,follow_symlinks=False)
        # Atomic create-without-overwrite: hard-link temp into final name on same volume.
        # If the process crashes between link and journal update, it will NOT delete
        # the unjournaled output; it reports a conflict for manual inspection.
        os.link(tmp,dst,follow_symlinks=False)
        return row["expected_hash"]
    finally:
        tmp.unlink(missing_ok=True)


def _write_manifest(run_root: Path, operations: list[dict]) -> None:
    _safe_destination(run_root.parent,run_root / "manifest.json",create=True)
    fields = ["source","destination","expected_hash","expected_size","category","decision",
              "model_reason","capture_time","date_source","state","verified_hash","error"]
    records = [{k:item.get(k) for k in fields} for item in operations]
    for extension in ("json","csv"):
        target = run_root / ("manifest." + extension)
        temp = run_root / ("." + target.name + "." + uuid.uuid4().hex + ".partial")
        with temp.open("x",newline="",encoding="utf-8") as handle:
            if extension == "json":
                json.dump(records,handle,indent=2,ensure_ascii=False)
            else:
                writer = csv.DictWriter(handle,fieldnames=fields)
                writer.writeheader()
                writer.writerows(records)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp,target)


def run_export(settings: Settings, run_id: str, stop: Protocol) -> None:
    root = export_root(settings)
    if root is None:
        return
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        run = _run(db,run_id)
        run_root = Path(run["destination"])
        if run_root.parent.resolve() != root or run["state"] not in ("approved","running"):
            db.rollback()
            return
        db.execute("UPDATE export_runs SET state='running' WHERE id=?", (run_id,))
        db.commit()
    while not stop.is_set():
        with connect(settings.data_dir) as db:
            run = _run(db,run_id)
            if run["state"] != "running":
                return
            row = db.execute("""SELECT * FROM export_operations WHERE export_id=?
                AND state IN ('planned','in_progress','failed') ORDER BY id LIMIT 1""",
                (run_id,)).fetchone()
            if row is None:
                operations = [dict(r) for r in db.execute(
                    "SELECT * FROM export_operations WHERE export_id=? ORDER BY id",(run_id,))]
                try:
                    _safe_destination(root,run_root / "manifest.json",create=True)
                    _write_manifest(run_root,operations)
                except Exception:
                    db.execute("UPDATE export_runs SET state='done_with_errors' WHERE id=?", (run_id,))
                    return
                failures = sum(r["state"]=="failed" for r in operations)
                db.execute("UPDATE export_runs SET state=? WHERE id=?",
                           ("done_with_errors" if failures else "done",run_id))
                return
            # Do not retry a failure repeatedly in one execution: failed entries
            # are retried only after an explicit Retry action.
            if row["state"]=="failed":
                remaining = db.execute("""SELECT COUNT(*) FROM export_operations WHERE export_id=?
                    AND state IN ('planned','in_progress')""",(run_id,)).fetchone()[0]
                if remaining:
                    row = db.execute("""SELECT * FROM export_operations WHERE export_id=?
                        AND state IN ('planned','in_progress') ORDER BY id LIMIT 1""",(run_id,)).fetchone()
                else:
                    operations = [dict(r) for r in db.execute(
                        "SELECT * FROM export_operations WHERE export_id=? ORDER BY id",(run_id,))]
                    _write_manifest(run_root,operations)
                    db.execute("UPDATE export_runs SET state='done_with_errors' WHERE id=?",(run_id,))
                    return
            db.execute("UPDATE export_operations SET state='in_progress',error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                       (row["id"],))
        try:
            # Recheck the source location and every ancestor against the stored library root.
            with connect(settings.data_dir) as db:
                src_row = db.execute("""SELECT lr.path AS root_path,f.relative_path
                    FROM files f JOIN library_roots lr ON lr.id=f.root_id WHERE f.id=?""",
                    (row["file_id"],)).fetchone()
            if src_row is None or _source(src_row["root_path"],src_row["relative_path"]) != Path(row["source"]):
                raise ExportError("Source path changed or is no longer safe")
            _safe_destination(root,run_root / "manifest.json")
            digest = _copy_one(run_root,row)
            with connect(settings.data_dir) as db:
                db.execute("""UPDATE export_operations SET state='done',created_by_export=1,verified_hash=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?""",(digest,row["id"]))
        except Exception as exc:
            with connect(settings.data_dir) as db:
                db.execute("UPDATE export_operations SET state='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (str(exc)[:500],row["id"]))


def rollback_export(settings: Settings, run_id: str) -> None:
    root = export_root(settings)
    if root is None:
        return
    with connect(settings.data_dir) as db:
        run = _run(db,run_id)
        run_root = Path(run["destination"])
        if run["state"] != "rollback_requested" or run_root.parent.resolve() != root:
            return
        rows = db.execute("""SELECT * FROM export_operations WHERE export_id=? AND state='done'
            AND created_by_export=1 ORDER BY id DESC""",(run_id,)).fetchall()
    failures = 0
    for row in rows:
        try:
            dst = Path(row["destination"])
            _safe_destination(run_root,dst)
            if not dst.is_file() or dst.is_symlink() or _hash(dst) != row["verified_hash"] or row["verified_hash"] != row["expected_hash"]:
                raise ExportError("Output missing or modified: refusing to remove")
            dst.unlink()
            with connect(settings.data_dir) as db:
                db.execute("UPDATE export_operations SET state='rolled_back',updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (row["id"],))
        except Exception as exc:
            failures += 1
            with connect(settings.data_dir) as db:
                db.execute("UPDATE export_operations SET error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           ("Rollback skipped: " + str(exc))[:500],row["id"])
    with connect(settings.data_dir) as db:
        operations = [dict(r) for r in db.execute(
            "SELECT * FROM export_operations WHERE export_id=? ORDER BY id",(run_id,))]
        _write_manifest(run_root,operations)
        db.execute("UPDATE export_runs SET state=? WHERE id=?",
                   ("rollback_partial" if failures else "rolled_back",run_id))
