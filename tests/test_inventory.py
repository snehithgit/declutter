from __future__ import annotations

import multiprocessing
import shutil
import time
import uuid
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from photosift.api import create_app
from photosift.config import load_settings
from photosift.db import connect, initialize
from photosift.inventory import run_scan
from photosift.worker import run_worker


@pytest.fixture
def env(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    conf = tmp_path / "config.toml"
    conf.write_text('data_dir="./data"\nlibrary_roots=["./library"]\n')
    settings = load_settings(conf)
    initialize(settings)
    with connect(settings.data_dir) as db:
        root_id = db.execute("SELECT id FROM library_roots").fetchone()[0]
    return settings, library, root_id


def job(settings, root_id: int) -> str:
    id = uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (id, root_id))
    return id


def make_image(path: Path, color=(255, 20, 20)):
    Image.new("RGB", (120, 75), color).save(path)


def test_scan_creates_inventory_images_thumbnails_and_hashes(env):
    settings, library, root_id = env
    (library / "sub").mkdir()
    make_image(library / "sub" / "photo_20200102.jpg")
    shutil.copy2(library / "sub" / "photo_20200102.jpg", library / "same.jpg")
    (library / "video.mp4").write_bytes(b"movie")
    (library / "corrupt.png").write_bytes(b"not a png")
    id = job(settings, root_id)
    run_scan(settings, id, root_id, library, Event())
    with connect(settings.data_dir) as db:
        state = db.execute("SELECT state,total,completed FROM jobs WHERE id=?", (id,)).fetchone()
        rows = db.execute("SELECT * FROM files ORDER BY relative_path").fetchall()
        contents = db.execute("SELECT * FROM contents").fetchall()
    assert tuple(state) == ("done", 4, 4)
    assert len(rows) == 4
    assert len(contents) == 3
    photo = next(r for r in rows if r["relative_path"].endswith("photo_20200102.jpg"))
    same = next(r for r in rows if r["relative_path"] == "same.jpg")
    assert photo["content_hash"] == same["content_hash"]
    assert photo["width"] == 120 and photo["height"] == 75
    assert photo["capture_time"] == "2020-01-02" and photo["date_source"] == "filename"
    video = next(r for r in rows if r["relative_path"] == "video.mp4")
    assert video["analysis_supported"] == 0 and video["readable"] == 1
    corrupt = next(r for r in rows if r["relative_path"] == "corrupt.png")
    assert corrupt["readable"] == 0 and corrupt["error"]
    assert (settings.data_dir / "thumbnails" / photo["content_hash"][:2] / (photo["content_hash"] + ".webp")).is_file()
    with TestClient(create_app(settings)) as client:
        summary = client.get("/api/inventory").json()
        assert summary["totals"]["files"] == 4
        page = client.get("/api/files", params={"search": "photo_20200102"}).json()
        assert page["total"] == 1
        assert client.get(page["items"][0]["thumbnail_url"]).headers["content-type"].startswith("image/webp")
        assert client.get("/api/thumbnails/" + "a"*64).status_code == 404
        assert client.get("/api/files", params={"limit": 200}).status_code == 422


def test_incremental_skips_hash_and_records_missing_only_after_complete(env, monkeypatch):
    settings, library, root_id = env
    make_image(library / "one.png")
    id = job(settings, root_id)
    run_scan(settings, id, root_id, library, Event())
    import photosift.inventory as inv
    original = inv.hash_file
    calls = []

    def spy(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(inv, "hash_file", spy)
    run_scan(settings, job(settings, root_id), root_id, library, Event())
    assert not calls
    (library / "one.png").unlink()
    run_scan(settings, job(settings, root_id), root_id, library, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT missing FROM files").fetchone()[0] == 1


def test_disconnected_root_pauses_without_marking_files_missing(env):
    settings, library, root_id = env
    make_image(library / "one.png")
    run_scan(settings, job(settings, root_id), root_id, library, Event())
    shutil.rmtree(library)
    id = job(settings, root_id)
    run_scan(settings, id, root_id, library, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (id,)).fetchone()[0] == "paused"
        assert db.execute("SELECT missing FROM files").fetchone()[0] == 0


def test_symlinks_are_skipped_and_no_access_outside_root(env):
    settings, library, root_id = env
    outside = settings.data_dir / "outside.png"
    make_image(outside)
    try:
        (library / "link.png").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privileges unavailable")
    run_scan(settings, job(settings, root_id), root_id, library, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0


def test_api_creates_controls_and_resumes_job(env):
    settings, library, root_id = env
    with TestClient(create_app(settings)) as client:
        health = client.get("/api/health").json()
        assert health["features"]["inventory"] is True
        result = client.post("/api/scans", json={"root_id": root_id})
        assert result.status_code == 201
        id = result.json()["job_ids"][0]
        assert client.post("/api/scans", json={"root_id": root_id}).status_code == 409
        assert client.post("/api/jobs/" + id + "/action", json={"action": "pause"}).json()["state"] == "paused"
        assert client.post("/api/jobs/" + id + "/action", json={"action": "resume"}).json()["state"] == "queued"
        assert client.post("/api/jobs/" + id + "/action", json={"action": "cancel"}).json()["state"] == "cancelled"
        assert client.post("/api/jobs/" + id + "/action", json={"action": "resume"}).status_code == 409
        assert client.post("/api/scans", json={"root_id": 9999}).status_code == 404


def test_partial_scan_never_marks_unvisited_files_missing(env, monkeypatch):
    settings, library, root_id = env
    make_image(library / "keep.png")
    run_scan(settings, job(settings, root_id), root_id, library, Event())
    import photosift.inventory as inv

    def interrupted(_root):
        raise inv.ScanPaused("Simulated share disconnect")
        yield  # pragma: no cover

    monkeypatch.setattr(inv, "iter_files", interrupted)
    id = job(settings, root_id)
    run_scan(settings, id, root_id, library, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (id,)).fetchone()[0] == "paused"
        assert db.execute("SELECT missing FROM files").fetchone()[0] == 0


def test_worker_executes_persisted_job(env):
    settings, library, root_id = env
    make_image(library / "work.png")
    id = job(settings, root_id)
    ctx = multiprocessing.get_context("spawn")
    stop = ctx.Event()
    worker = ctx.Process(target=run_worker, args=(str(library.parent / "config.toml"), stop))
    worker.start()
    try:
        deadline = time.monotonic() + 12
        state = None
        while time.monotonic() < deadline:
            with connect(settings.data_dir) as db:
                state = db.execute("SELECT state FROM jobs WHERE id=?", (id,)).fetchone()[0]
            if state == "done":
                break
            time.sleep(.15)
        assert state == "done"
    finally:
        stop.set()
        worker.join(6)
        if worker.is_alive():
            worker.terminate()
            worker.join(3)
    assert worker.exitcode == 0


def test_orderly_worker_stop_recovers_scan_on_restart(env):
    settings, library, root_id = env
    make_image(library / "restart.png")
    id = job(settings, root_id)
    stopped = Event()
    stopped.set()
    run_scan(settings, id, root_id, library, stopped)
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (id,)).fetchone()[0] == "running"
    run_scan(settings, id, root_id, library, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (id,)).fetchone()[0] == "done"
