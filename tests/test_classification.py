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


def test_resolution_flags_use_original_not_small_preview(tmp_path):
    from photosift.classification import _signals
    preview = tmp_path / "preview.webp"
    image = Image.new("RGB", (4000, 1800), "green")
    image.thumbnail((1024, 1024))
    image.save(preview)
    assert image.size == (1024, 461)
    assert _signals(preview, (4000, 1800))[1] == 0
    assert _signals(preview, (320, 240))[1] == 1


def test_configured_concurrency_processes_distinct_hashes_in_parallel(env, monkeypatch):
    from threading import Barrier, Lock
    import photosift.classification as cls
    settings, library = env
    settings.ollama.concurrency = 2
    Image.new("RGB", (800, 600), "red").save(library / "third.jpg")
    with connect(settings.data_dir) as db:
        root = db.execute("SELECT id FROM library_roots").fetchone()[0]
        scan = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (scan, root))
    run_scan(settings, scan, root, library, Event())
    barrier, lock, calls = Barrier(2, timeout=3), Lock(), []
    def predict(*args, **kwargs):
        with lock:
            calls.append(1)
        barrier.wait()
        return GOOD, 1.0
    monkeypatch.setattr(cls, "call_ollama", predict)
    job = queue_classification(settings)
    assert job["queued"] == 2
    run_classification(settings, job["job_id"], Event())
    assert len(calls) == 2
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM ai_results").fetchone()[0] == 2
        assert db.execute("SELECT completed FROM jobs WHERE id=?", (job["job_id"],)).fetchone()[0] == 2


def test_timeout_is_retryable_and_successful_retry_finishes(env, monkeypatch):
    import photosift.classification as cls
    settings, _ = env
    stop = Event()
    job = queue_classification(settings)
    def timed_out(*args, **kwargs):
        stop.set()
        raise cls.RetryableInferenceError("Request timed out")
    monkeypatch.setattr(cls, "call_ollama", timed_out)
    run_classification(settings, job["job_id"], stop)
    with connect(settings.data_dir) as db:
        item = db.execute("SELECT state,attempts FROM queue_items").fetchone()
        assert tuple(item) == ("failed_retryable", 1)
        db.execute("UPDATE queue_items SET available_at=datetime('now','-1 second')")
    monkeypatch.setattr(cls, "call_ollama", lambda *a, **k: (GOOD, 1))
    run_classification(settings, job["job_id"], Event())
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM queue_items").fetchone()[0] == "done"


def test_lease_loss_discards_inflight_success_and_releases_claim(env, monkeypatch):
    import photosift.classification as cls
    settings, _ = env
    stop = Event()
    def finished_after_stop(*args, **kwargs):
        stop.set()
        return GOOD, 1
    monkeypatch.setattr(cls, "call_ollama", finished_after_stop)
    job = queue_classification(settings)
    run_classification(settings, job["job_id"], stop)
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM ai_results").fetchone()[0] == 0
        assert tuple(db.execute("SELECT state,attempts FROM queue_items").fetchone()) == ("queued", 0)


def test_network_timeout_and_offline_are_different_errors(env, monkeypatch):
    import urllib.error
    import photosift.classification as cls
    settings, _ = env
    with connect(settings.data_dir) as db:
        preview = settings.data_dir / db.execute("SELECT preview_path FROM contents").fetchone()[0]
    def timed_out(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr(cls.urllib.request, "urlopen", timed_out)
    with pytest.raises(cls.RetryableInferenceError):
        cls.call_ollama(settings, preview)
    def offline(*args, **kwargs):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(cls.urllib.request, "urlopen", offline)
    with pytest.raises(ModelUnavailable):
        cls.call_ollama(settings, preview)


def test_d3_uses_decision_api_and_preserves_raw_scores(env, monkeypatch):
    import io, json
    import photosift.classification as cls
    settings, _ = env
    payload = {"model": settings.ollama.model, "answers": {
        "category": {"choice": "document", "confidence": 0.9},
        "subcategory": {"choice": "receipt", "confidence": 0.8},
        "contains_people": {"noul": 0.01}, "contains_text": {"noul": 0.99},
        "possibly_important": {"noul": 0.75}}, "usage": {"input_tokens": 100}}
    def respond(request, timeout):
        assert request.full_url.endswith('/v1/systemone')
        body = json.loads(request.data)
        assert body["model"] == settings.ollama.model
        assert body["questions"]["category"]["type"] == "choice"
        assert body["images"] and "messages" not in body
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(cls.urllib.request, "urlopen", respond)
    with connect(settings.data_dir) as db:
        preview = settings.data_dir / db.execute("SELECT preview_path FROM contents").fetchone()[0]
    raw, _ = cls.call_ollama(settings, preview)
    assert json.loads(raw) == payload
    result = parse_prediction(raw)
    assert result.category == "document" and result.subcategory == "receipt"
    assert result.possibly_important and not result.contains_people
    payload["answers"]["contains_people"]["noul"] = 1.5
    with pytest.raises(InvalidPrediction):
        parse_prediction(json.dumps(payload))

def test_folder_scope_browser_and_queue(env, monkeypatch):
    import json
    import photosift.classification as cls
    settings, library = env
    for name, color in [('Family/nested/a.jpg','red'), ('Family2/b.jpg','green'), ('literal%_/c.jpg','blue')]:
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (640,480), color).save(path)
    shutil.copy2(library/'first.jpg', library/'Family'/'copy.jpg')
    with connect(settings.data_dir) as db:
        root = db.execute('SELECT id FROM library_roots').fetchone()[0]
        jid = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)", (jid,root))
    run_scan(settings,jid,root,library,Event())
    client = TestClient(create_app(settings))
    assert client.get('/api/classification/options').json()['roots'][0]['id'] == root
    page = client.get('/api/classification/folders',params={'root_id':root,'folder':'Family'}).json()
    assert page['images'] == page['pending'] == 2
    assert page['children'] == [{'name':'nested','folder':'Family/nested','images':1}]
    assert cls.classification_folders(settings,root,'literal%_')['images'] == 1
    assert cls.classification_folders(settings,root,limit=1)['total'] == 3
    assert len(cls.classification_folders(settings,root,offset=1,limit=1)['children']) == 1
    for folder in ['/Family','../Family','Family/../Family2','Family\\nested','C:/Family']:
        assert client.post('/api/classification/jobs',json={'root_id':root,'folder':folder}).status_code == 409
    assert client.get('/api/classification/folders',params={'root_id':999}).status_code == 422
    result = client.post('/api/classification/jobs',json={'root_id':root,'folder':'Family'}).json()
    assert result['queued'] == 2
    with connect(settings.data_dir) as db:
        params = json.loads(db.execute('SELECT parameters_json FROM jobs WHERE id=?',(result['job_id'],)).fetchone()[0])
        assert params['folder'] == 'Family' and params['root_id'] == root
    monkeypatch.setattr(cls,'call_ollama',lambda *a,**kw:(GOOD,1))
    run_classification(settings,result['job_id'],Event())
    assert cls.classification_folders(settings,root,'Family')['pending'] == 0
    assert cls.classification_folders(settings,root,'Family2')['pending'] == 1
    assert queue_classification(settings,root_id=root,folder='Family')['queued'] == 0
