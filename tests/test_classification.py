"""M4 acceptance tests: results are per hash, retryable and never touch originals."""
from __future__ import annotations
import shutil
import uuid
from pathlib import Path
from threading import Event
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from photosift.api import create_app
from photosift.classification import (InvalidPrediction, ModelUnavailable, PROMPT_VERSION,
    parse_prediction, queue_classification, run_classification)
from photosift.config import load_settings
from photosift.db import connect, initialize
from photosift.inventory import run_scan

GOOD = ('{"category":"personal_photo","subcategory":"family","confidence":0.9,'
        '"reason":"People outdoors","contains_people":true,"contains_text":false,'
        '"possibly_important":true,"importance_reason":"Personal memory"}')

@pytest.fixture
def env(tmp_path: Path):
    library = tmp_path / "library"
    library.mkdir()
    config = tmp_path / "config.toml"
    config.write_text('data_dir="./data"\nlibrary_roots=["./library"]\n')
    settings = load_settings(config)
    initialize(settings)
    with connect(settings.data_dir) as db:
        root = db.execute("SELECT id FROM library_roots").fetchone()[0]
    Image.new("RGB", (640,480),(40,120,200)).save(library / "first.jpg")
    shutil.copy2(library / "first.jpg", library / "second.jpg")
    jid = uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (jid, root))
    run_scan(settings, jid, root, library, Event())
    return settings, library

def test_prediction_schema_strict():
    assert parse_prediction(GOOD).category == "personal_photo"
    with pytest.raises(InvalidPrediction):
        parse_prediction(GOOD.replace('"confidence":0.9','"confidence":"0.9"'))
    with pytest.raises(InvalidPrediction):
        parse_prediction(GOOD[:-1] + ',"person_name":"unknown"}')

def test_unique_hash_result_preserves_source(env, monkeypatch):
    import photosift.classification as cls
    settings, library = env
    original = {p.name:p.read_bytes() for p in library.iterdir()}
    job = queue_classification(settings)
    assert job["queued"] == 1
    with pytest.raises(ValueError):
        queue_classification(settings)
    calls = []
    def fake(*args, **kwargs):
        calls.append(1)
        return GOOD, 3.5
    monkeypatch.setattr(cls, "call_ollama", fake)
    run_classification(settings, job["job_id"], Event())
    assert len(calls) == 1
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM ai_results").fetchone()[0] == 1
        assert db.execute("SELECT state FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == "done"
        assert db.execute("SELECT prompt_version FROM ai_results").fetchone()[0] == PROMPT_VERSION
    assert queue_classification(settings)["queued"] == 0
    assert queue_classification(settings, force=True)["queued"] == 1
    assert {p.name:p.read_bytes() for p in library.iterdir()} == original

def test_offline_pause_and_resume_no_attempt_loss(env, monkeypatch):
    import photosift.classification as cls
    settings, _ = env
    job = queue_classification(settings)
    def offline(*args, **kwargs):
        raise ModelUnavailable("Laptop unavailable")
    monkeypatch.setattr(cls, "call_ollama", offline)
    run_classification(settings, job["job_id"], Event())
    with connect(settings.data_dir) as db:
        row = db.execute("SELECT state,attempts FROM queue_items WHERE job_id=?", (job["job_id"],)).fetchone()
        assert tuple(row) == ("queued",0)
        assert db.execute("SELECT state FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == "paused"
    monkeypatch.setattr(cls, "call_ollama", lambda *args,**kwargs:(GOOD,5.0))
    with TestClient(create_app(settings)) as client:
        assert client.post(f"/api/classification/jobs/{job['job_id']}/action",json={"action":"resume"}).status_code==200
    run_classification(settings, job["job_id"], Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == "done"

def test_invalid_json_retries_once_then_permanent(env, monkeypatch):
    import photosift.classification as cls
    settings, _ = env
    job = queue_classification(settings)
    stop = Event()
    def bad(*args, **kwargs):
        stop.set()
        return "{}",2.0
    monkeypatch.setattr(cls, "call_ollama", bad)
    run_classification(settings, job["job_id"], stop)
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT invalid_response_attempts FROM queue_items").fetchone()[0] == 1
        db.execute("UPDATE queue_items SET available_at=datetime('now','-1 second')")
    run_classification(settings, job["job_id"], Event())
    with connect(settings.data_dir) as db:
        row = db.execute("SELECT state,invalid_response_attempts FROM queue_items").fetchone()
        assert tuple(row) == ("failed_permanent",2)
        assert db.execute("SELECT state FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == "done"

def test_restart_claimed_item_and_api_controls(env,monkeypatch):
    import photosift.classification as cls
    settings, _ = env
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/classification/jobs", json={})
        assert resp.status_code == 201
        job_id = resp.json()["job_id"]
        assert client.get("/api/classification/jobs").json()[0]["total"] == 1
        assert client.post(f"/api/classification/jobs/{job_id}/action",json={"action":"pause"}).json()["state"] == "paused"
        assert client.post(f"/api/classification/jobs/{job_id}/action",json={"action":"resume"}).json()["state"] == "queued"
        assert client.get("/api/classification/results").json() == []
    with connect(settings.data_dir) as db:
        db.execute("UPDATE queue_items SET state='running' WHERE job_id=?", (job_id,))
        db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
    monkeypatch.setattr(cls, "call_ollama", lambda *args, **kwargs:(GOOD,2.0))
    run_classification(settings,job_id,Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM queue_items WHERE job_id=?", (job_id,)).fetchone()[0]=="done"
    with TestClient(create_app(settings)) as client:
        assert len(client.get("/api/classification/results").json())==1
