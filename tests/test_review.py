"""M5 regression: content-level decisions, filters, audit, safety and bulk actions."""
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
from photosift.db import connect,initialize
from photosift.inventory import run_scan


@pytest.fixture
def collection(tmp_path:Path):
    a,b=tmp_path/"camera",tmp_path/"backup"
    a.mkdir();b.mkdir()
    config=tmp_path/"config.toml"
    config.write_text('data_dir="./data"\nlibrary_roots=["./camera","./backup"]\n')
    settings=load_settings(config)
    initialize(settings)
    with connect(settings.data_dir) as db:
        roots={Path(r["path"]).name:r["id"] for r in db.execute("SELECT id,path FROM library_roots")}
    Image.new("RGB",(640,480),(240,5,5)).save(a/"first.jpg")
    shutil.copy2(a/"first.jpg",b/"duplicate.jpg")
    Image.new("RGB",(640,480),(5,5,240)).save(a/"second.jpg")
    for root in (a,b):
        jid=uuid.uuid4().hex
        with connect(settings.data_dir) as db:
            db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)",(jid,roots[root.name]))
        run_scan(settings,jid,roots[root.name],root,Event())
        with connect(settings.data_dir) as db:
            assert db.execute("SELECT state FROM jobs WHERE id=?",(jid,)).fetchone()[0]=="done"
    first=hashlib.sha256((a/"first.jpg").read_bytes()).hexdigest()
    second=hashlib.sha256((a/"second.jpg").read_bytes()).hexdigest()
    return settings,a,b,roots,first,second


def test_summary_and_shared_duplicate_identity(collection):
    settings,a,b,roots,first,second=collection
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["features"]["review"] is True
        summary=client.get("/api/reviews/summary").json()
        assert summary["total"]==2 and summary["undecided"]==2
        page=client.get("/api/reviews?duplicates_only=true").json()
        assert page["total"]==1 and page["items"][0]["content_hash"]==first
        assert page["items"][0]["copy_count"]==2
        detail=client.get("/api/reviews/"+first).json()
        assert {x["relative_path"] for x in detail["copies"]}=={"first.jpg","duplicate.jpg"}
        assert client.get("/api/reviews/"+("z"*64)).status_code==404
        assert client.get("/api/reviews?limit=121").status_code==422


def test_changes_audited_idempotent_and_originals_unmodified(collection):
    settings,a,b,roots,first,second=collection
    before={p:(p.read_bytes(),p.stat().st_mtime_ns) for root in (a,b) for p in root.iterdir()}
    data={"hashes":[first],"decision":"keep","category_override":"personal_photo"}
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/reviews/bulk",json=data).json()=={"matched":1,"changed":1}
        assert client.post("/api/reviews/bulk",json=data).json()=={"matched":1,"changed":0}
        detail=client.get("/api/reviews/"+first).json()
        assert detail["decision"]=="keep"
        assert detail["category_override"]=="personal_photo"
        assert client.get("/api/reviews?review_status=keep").json()["total"]==1
        audit=client.get("/api/reviews/audit?content_hash="+first).json()
        assert audit["total"]==1
        assert audit["items"][0]["before"]["decision"]=="undecided"
        assert audit["items"][0]["after"]["decision"]=="keep"
        assert client.post("/api/reviews/bulk",json={"hashes":[first],"clear_category":True}).json()["changed"]==1
        assert client.get("/api/reviews/"+first).json()["category_override"] is None
    assert {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before}==before


def test_select_all_matching_and_filters(collection):
    settings,a,b,roots,first,second=collection
    with TestClient(create_app(settings)) as client:
        response=client.post("/api/reviews/bulk",json={
            "filters":{"root_id":roots["backup"],"duplicates_only":True},
            "decision":"review_later"})
        assert response.json()=={"matched":1,"changed":1}
        assert client.get("/api/reviews",params={"root_id":roots["backup"],"review_status":"review_later"}).json()["total"]==1
        assert client.get("/api/reviews",params={"root_id":roots["backup"],"search":"no-match"}).json()["total"]==0
        assert client.get("/api/reviews",params={"folder":"first.jpg"}).json()["total"]==1
        assert client.get("/api/reviews",params={"category":"unclassified"}).json()["total"]==2
        assert client.post("/api/reviews/bulk",json={"filters":{},"decision":"clutter"}).json()["changed"]==2
        assert client.get("/api/reviews/summary").json()["clutter"]==2


def test_validation_atomicity_and_disabled_sources(collection):
    settings,a,b,roots,first,second=collection
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/reviews/bulk",json={"hashes":[first,"f"*64],"decision":"keep"}).status_code==409
        assert client.post("/api/reviews/bulk",json={"hashes":[first,first],"decision":"keep"}).status_code==422
        assert client.post("/api/reviews/bulk",json={"hashes":[first],"decision":"keep","category_override":"bad"}).status_code==422
        assert client.post("/api/reviews/bulk",json={"hashes":[first],"clear_category":True,"category_override":"other"}).status_code==422
        assert client.get("/api/reviews/summary").json()["keep"]==0
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]==0
        db.execute("UPDATE library_roots SET enabled=0 WHERE id=?",(roots["backup"],))
    (a/"first.jpg").unlink()
    job=uuid.uuid4().hex
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)",(job,roots["camera"]))
    run_scan(settings,job,roots["camera"],a,Event())
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/reviews/bulk",json={"hashes":[first],"decision":"keep"}).status_code==409
        assert client.get("/api/reviews").json()["total"]==1


def test_latest_ai_flags_and_category_corrections(collection):
    settings,a,b,roots,first,second=collection
    with connect(settings.data_dir) as db:
        for digest,category,conf,important,uncertain in ((first,"screenshot",0.2,1,1),(second,"document",0.9,0,0)):
            db.execute("""INSERT INTO ai_results(content_hash,model_tag,prompt_version,raw_response,
                category,subcategory,confidence,reason,contains_people,contains_text,
                possibly_important,importance_reason,uncertain,duration_ms)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (digest,"model","v1","{}",category,"test",conf,"reason",0,1,important,"reason",uncertain,4.0))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/reviews",params={"uncertain":True}).json()["total"]==1
        assert client.get("/api/reviews",params={"important":True}).json()["items"][0]["content_hash"]==first
        assert client.get("/api/reviews",params={"category":"document"}).json()["total"]==1
        assert client.get("/api/reviews",params={"date_from":"2027-01-01"}).json()["total"]==0
        assert client.get("/api/reviews",params={"date_from":"2027-01-01","date_to":"2026-01-01"}).status_code==422
        assert client.post("/api/reviews/bulk",json={"hashes":[first],"category_override":"other"}).json()["changed"]==1
        assert client.get("/api/reviews",params={"category":"other"}).json()["total"]==1
        assert client.get("/api/reviews",params={"category":"screenshot"}).json()["total"]==0
