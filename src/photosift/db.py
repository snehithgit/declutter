"""Short-lived SQLite connections and atomic, versioned schema migrations."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator

from .config import Settings, path_key


@contextmanager
def connect(data_dir: Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(data_dir / "photosift.sqlite3", timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
    finally:
        db.close()


def initialize(settings: Settings) -> None:
    for subdir in ("", "thumbnails", "previews", "logs"):
        (settings.data_dir / subdir).mkdir(parents=True, exist_ok=True)
    with connect(settings.data_dir) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                       "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            migrations = sorted(files("photosift").joinpath("migrations").iterdir(), key=lambda p: p.name)
            known_versions = {int(p.name.split('_')[0]) for p in migrations if p.name.endswith('.sql')}
            applied = {row['version']: row['checksum'] for row in db.execute('SELECT * FROM schema_migrations')}
            if set(applied) - known_versions:
                raise RuntimeError("Database was created by a newer PhotoSift version")
            for resource in migrations:
                if not resource.name.endswith(".sql"):
                    continue
                version = int(resource.name.split("_")[0])
                sql = resource.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise RuntimeError(f"Applied migration {version} was modified")
                    continue
                # Migrations contain plain statements, not triggers or string-literal semicolons.
                for statement in sql.split(";"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT INTO schema_migrations(version,checksum) VALUES (?,?)", (version, checksum))
            db.execute("UPDATE library_roots SET enabled=0")
            for root in settings.library_roots:
                db.execute("INSERT INTO library_roots(path,path_key) VALUES (?,?) "
                           "ON CONFLICT(path_key) DO UPDATE SET enabled=1,path=excluded.path", (root, path_key(root)))
            db.commit()
        except BaseException:
            db.rollback()
            raise
