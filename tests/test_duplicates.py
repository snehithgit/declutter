"""M3 acceptance: exact matches across roots, pagination and non-destructive behavior."""
from __future__ import annotations

import hashlib
import shutil
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


@pytest.fixture
def setup(tmp_path: Path):
    camera, backups = tmp_path / "camera", tmp_path / "backups"
    camera.mkdir()
    backups.mkdir()
    config = tmp_path / "config.toml"
    config.write_text('data_dir="./data"\nlibrary_roots=["./camera","./backups"]\n')
    settings = load_settings(config)
    initialize(settings)
    with connect(settings.data_dir) as db:
        roots = {Path(row["path"]).name: row["id"] for row in db.execute("SELECT id,path FROM library_roots")}
    return settings, camera, backups, roots


def scan(settings, root: Path, root_id: int):
    jid = uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (jid, root_id))
    run_scan(settings, jid, root_id, root, Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0] == "done"


def image(path: Path, rgb: tuple[int, int, int]):
    Image.new("RGB", (83, 42), rgb).save(path)


def test_groups_cross_roots_pagination_and_no_original_changes(setup):
    settings, camera, backups, roots = setup
    image(camera / "event.jpg", (123, 45, 67))
    shutil.copy2(camera / "event.jpg", camera / "copy.jpg")
    shutil.copy2(camera / "event.jpg", backups / "backup.jpg")
    image(camera / "different.jpg", (200, 100, 20))
    (camera / "same.mp4").write_bytes(b"video-data")
    shutil.copy2(camera / "same.mp4", backups / "same.mp4")
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for root in (camera, backups) for p in root.iterdir()}
    scan(settings, camera, roots["camera"])
    scan(settings, backups, roots["backups"])
    digest = hashlib.sha256((camera / "event.jpg").read_bytes()).hexdigest()

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["features"]["duplicates"] is True
        summary = client.get("/api/duplicates/summary").json()
        assert summary == {"groups": 2, "copies": 5, "extra_copies": 3,
                           "potential_reclaim_bytes": (camera / "event.jpg").stat().st_size * 2 + len(b"video-data")}
        first = client.get("/api/duplicates", params={"sort": "copies", "limit": 1}).json()
        assert first["total"] == 2 and len(first["items"]) == 1
        assert first["items"][0]["content_hash"] == digest
        assert first["items"][0]["copy_count"] == 3
        assert client.get(first["items"][0]["thumbnail_url"]).status_code == 200
        assert client.get("/api/duplicates?sort=copies&limit=1&offset=1").json()["items"][0]["copy_count"] == 2
        details = client.get(f"/api/duplicates/{digest}/files?limit=2").json()
        assert details["copy_count"] == 3 and details["missing_count"] == 0 and details["total"] == 3
        more = client.get(f"/api/duplicates/{digest}/files?offset=2&limit=2").json()
        copies = details["items"] + more["items"]
        assert {copy["relative_path"] for copy in copies} == {"event.jpg", "copy.jpg", "backup.jpg"}
        assert {copy["root_path"] for copy in copies} == {str(camera), str(backups)}
        assert client.get("/api/duplicates/not-a-sha/files").status_code == 404
        assert client.get("/api/duplicates/" + "f" * 64 + "/files").status_code == 404
        assert client.get("/api/duplicates?sort=invalid").status_code == 422
        assert client.get("/api/duplicates?limit=999").status_code == 422
        assert client.post("/api/duplicates").status_code == 405
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


def test_missing_history_and_group_changes_after_rescan(setup):
    settings, camera, backups, roots = setup
    image(camera / "keep.jpg", (10, 20, 30))
    shutil.copy2(camera / "keep.jpg", backups / "old.jpg")
    shutil.copy2(camera / "keep.jpg", backups / "other.jpg")
    scan(settings, camera, roots["camera"])
    scan(settings, backups, roots["backups"])
    digest = hashlib.sha256((camera / "keep.jpg").read_bytes()).hexdigest()
    (backups / "old.jpg").unlink()
    scan(settings, backups, roots["backups"])
    with TestClient(create_app(settings)) as client:
        details = client.get(f"/api/duplicates/{digest}/files").json()
        assert (details["copy_count"], details["missing_count"], details["total"]) == (2, 1, 3)
        assert details["items"][-1]["relative_path"] == "old.jpg" and details["items"][-1]["missing"] == 1
        assert client.get("/api/duplicates/summary").json()["extra_copies"] == 1
    (backups / "other.jpg").unlink()
    scan(settings, backups, roots["backups"])
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/duplicates/summary").json()["groups"] == 0
        assert client.get("/api/duplicates").json()["items"] == []
        assert client.get(f"/api/duplicates/{digest}/files").status_code == 404
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT count(*) FROM files WHERE content_hash=?", (digest,)).fetchone()[0] == 3


def test_shared_identity_across_extensions_no_rescan_drift(setup):
    settings, camera, backups, roots = setup
    image(camera / "original.png", (50, 60, 70))
    shutil.copy2(camera / "original.png", camera / "renamed.jpg")
    scan(settings, camera, roots["camera"])
    scan(settings, camera, roots["camera"])
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/duplicates/summary").json()["groups"] == 1
        group = client.get("/api/duplicates").json()["items"][0]
        assert group["copy_count"] == 2 and group["extra_copies"] == 1
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT count(*) FROM contents").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM files").fetchone()[0] == 2


def test_empty_inventory_and_single_instance_have_no_groups(setup):
    settings, camera, backups, roots = setup
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/duplicates/summary").json()["groups"] == 0
        assert client.get("/api/duplicates").json()["total"] == 0
    image(camera / "only.jpg", (1, 2, 3))
    scan(settings, camera, roots["camera"])
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/duplicates/summary").json()["groups"] == 0
        assert client.get("/api/duplicates").json()["total"] == 0
