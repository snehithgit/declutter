from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from photosift.api import create_app
from photosift.config import load_settings, path_key
from photosift.db import connect, initialize
from photosift.worker import acquire_lease, run_worker


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text('data_dir="./data"\nlibrary_roots=["./unreachable-library"]\n', encoding="utf-8")
    return path


def test_config_resolves_without_touching_missing_root(config_file: Path):
    settings = load_settings(config_file)
    assert settings.data_dir == config_file.parent / "data"
    assert settings.ollama.concurrency == 1
    assert not (config_file.parent / "unreachable-library").exists()
    assert path_key(r"\\ZIMA\Photos\Family") == path_key(r"\\zima\photos\FAMILY")


@pytest.mark.parametrize("text", [
    'data_dir="./data"\nlibrary_roots=["."]',
    'data_dir="./data"\n[ollama]\nconcurrency=0',
    'data_dir="./data"\n[ollama]\nurl="http://user:password@localhost:11434"',
    'data_dir="./data"\n[server]\nhost="192.168.1.1"',
    'data_dir="./data"\nunknown=true',
    'data_dir="./data"\nlibrary_roots=["./photos","./photos"]',
    "data_dir='\\\\ZIMA\\share\\data'",
])
def test_bad_config_rejected(tmp_path: Path, text: str):
    path = tmp_path / "bad.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(path)


def test_environment_ollama_override(config_file: Path, monkeypatch):
    monkeypatch.setenv("PHOTOSIFT_OLLAMA_URL", "http://192.168.68.10:11434")
    assert load_settings(config_file).ollama.url == "http://192.168.68.10:11434"


def test_migrations_idempotent_and_preserve_records(config_file: Path):
    settings = load_settings(config_file)
    initialize(settings)
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO jobs(id,kind) VALUES ('test','future')")
    initialize(settings)
    with connect(settings.data_dir) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 4
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert db.execute("SELECT decision FROM reviews").fetchone() is None
        with pytest.raises(Exception):
            db.execute("INSERT INTO queue_items(job_id) VALUES ('missing-job')")


def test_newer_schema_refused(config_file: Path):
    settings = load_settings(config_file)
    initialize(settings)
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO schema_migrations(version,checksum) VALUES (999,'future')")
    with pytest.raises(RuntimeError, match="newer"):
        initialize(settings)


def test_changed_applied_migration_refused(config_file: Path):
    settings = load_settings(config_file)
    initialize(settings)
    with connect(settings.data_dir) as db:
        db.execute("UPDATE schema_migrations SET checksum='changed'")
    with pytest.raises(RuntimeError, match="modified"):
        initialize(settings)


def test_worker_lease_prevents_two_workers_and_expires(config_file: Path):
    settings = load_settings(config_file)
    initialize(settings)
    assert acquire_lease(settings.data_dir, "first", 100)
    assert not acquire_lease(settings.data_dir, "second", 101)
    assert acquire_lease(settings.data_dir, "second", 116)


def test_api_never_scans_and_reports_worker(config_file: Path):
    settings = load_settings(config_file)
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
        assert health["worker"]["state"] == "offline"
        assert health["features"]["inventory"] is True
        assert health["features"]["classification"] is True
        assert health["features"]["review"] is True
        assert health["features"]["export"] is True
        assert client.get("/api/settings").json()["ollama_checked"] is False
        assert client.post("/api/scan").status_code == 404
        assert client.get("/api/health", headers={"host": "attacker.invalid"}).status_code == 400
        assert acquire_lease(settings.data_dir, "test", time.time())
        assert client.get("/api/health").json()["status"] == "ok"
    assert not Path(settings.library_roots[0]).exists()


@pytest.mark.parametrize("leftover_lease", [False, True])
def test_real_separate_worker_start_and_stop(config_file: Path, leftover_lease: bool):
    settings = load_settings(config_file)
    initialize(settings)
    if leftover_lease:
        assert acquire_lease(settings.data_dir, "old-terminated-worker", time.time() - 14)
    ctx = multiprocessing.get_context("spawn")
    stop = ctx.Event()
    worker = ctx.Process(target=run_worker, args=(str(config_file), stop))
    worker.start()
    try:
        deadline = time.monotonic() + 15
        observed = False
        while time.monotonic() < deadline:
            with connect(settings.data_dir) as db:
                row = db.execute("SELECT owner FROM worker_leases").fetchone()
                observed = bool(row and row["owner"] != "old-terminated-worker")
            if observed:
                break
            time.sleep(0.1)
        assert observed, "Worker never acquired a lease"
    finally:
        stop.set()
        worker.join(10)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
    assert worker.exitcode == 0
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM worker_leases").fetchone()[0] == 0
