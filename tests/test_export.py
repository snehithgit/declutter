"""M6 acceptance: verified copy-only plans, approval, conflicts and rollback safety."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from photosift.api import create_app
from photosift.config import load_settings
from photosift.db import connect, initialize
from photosift.export import (ExportError, create_plan, export_detail, export_root,
                             run_export, rollback_export, set_export_state, next_export_job)
from photosift.inventory import run_scan


@pytest.fixture
def library(tmp_path: Path):
    photos = tmp_path / "photos"
    exports = tmp_path / "exports"
    photos.mkdir()
    exports.mkdir()
    conf = tmp_path / "config.toml"
    conf.write_text('data_dir="./data"\nexport_dir="./exports"\nlibrary_roots=["./photos"]\n',encoding="utf-8")
    settings = load_settings(conf)
    initialize(settings)
    Image.new("RGB",(640,480),(150,25,100)).save(photos / "IMG_20260102.jpg")
    shutil.copy2(photos / "IMG_20260102.jpg",photos / "duplicate.jpg")
    with connect(settings.data_dir) as db:
        root_id = db.execute("SELECT id FROM library_roots").fetchone()[0]
        job = uuid.uuid4().hex
        db.execute("INSERT INTO jobs(id,kind,root_id) VALUES (?,'inventory',?)",(job,root_id))
    run_scan(settings,job,root_id,photos,Event())
    digest = hashlib.sha256((photos / "IMG_20260102.jpg").read_bytes()).hexdigest()
    with connect(settings.data_dir) as db:
        db.execute("INSERT INTO reviews(content_hash,decision,category_override) VALUES (?,'keep','personal_photo')",(digest,))
    return settings,photos,exports,digest


def approve(settings,run_id):
    return set_export_state(settings,run_id,"approve","COPY APPROVED")


def test_plan_requires_approval_and_copies_one_unique_content(library):
    settings,photos,exports,digest=library
    snapshot={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in photos.iterdir()}
    plan=create_plan(settings)
    assert plan["files"]==1
    detail=export_detail(settings,plan["id"])
    assert detail["state"]=="planned"
    assert len(detail["operations"])==1
    path=Path(detail["operations"][0]["destination"])
    assert "Organized/Photos/2026/2026-01" in path.as_posix()
    assert not path.exists()
    assert next_export_job(settings) is None
    with pytest.raises(ExportError):
        set_export_state(settings,plan["id"],"approve","no")
    approve(settings,plan["id"])
    assert next_export_job(settings)[0]==plan["id"]
    run_export(settings,plan["id"],Event())
    result=export_detail(settings,plan["id"])
    assert result["state"]=="done"
    assert result["copied"]==1
    assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==digest
    assert path.stat().st_mtime_ns==(photos / "IMG_20260102.jpg").stat().st_mtime_ns
    manifest=json.loads((Path(plan["destination"])/"manifest.json").read_text())
    assert manifest[0]["source"]==str(photos / "IMG_20260102.jpg")
    assert manifest[0]["verified_hash"]==digest
    assert (Path(plan["destination"])/"manifest.csv").is_file()
    assert {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in photos.iterdir()}==snapshot


def test_source_change_is_refused_and_no_output_created(library):
    settings,photos,exports,digest=library
    plan=create_plan(settings)
    target=Path(export_detail(settings,plan["id"])["operations"][0]["destination"])
    (photos / "IMG_20260102.jpg").write_bytes(b"changed")
    # The plan may select a different instance; change every duplicate.
    (photos / "duplicate.jpg").write_bytes(b"changed")
    approve(settings,plan["id"])
    run_export(settings,plan["id"],Event())
    detail=export_detail(settings,plan["id"])
    assert detail["state"]=="done_with_errors"
    assert detail["failed"]==1
    assert not target.exists()


def test_existing_output_never_overwritten_and_retry_is_explicit(library):
    settings,photos,exports,digest=library
    plan=create_plan(settings)
    target=Path(export_detail(settings,plan["id"])["operations"][0]["destination"])
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(b"preexisting-user-file")
    approve(settings,plan["id"])
    run_export(settings,plan["id"],Event())
    assert target.read_bytes()==b"preexisting-user-file"
    assert export_detail(settings,plan["id"])["state"]=="done_with_errors"
    target.unlink()
    set_export_state(settings,plan["id"],"retry","RETRY FAILED")
    run_export(settings,plan["id"],Event())
    assert export_detail(settings,plan["id"])["state"]=="done"
    assert target.read_bytes()==(photos / "IMG_20260102.jpg").read_bytes()


def test_rollback_removes_only_owned_verified_copies(library):
    settings,photos,exports,digest=library
    plan=create_plan(settings)
    approve(settings,plan["id"])
    run_export(settings,plan["id"],Event())
    target=Path(export_detail(settings,plan["id"])["operations"][0]["destination"])
    set_export_state(settings,plan["id"],"rollback","ROLLBACK COPIES")
    rollback_export(settings,plan["id"])
    assert not target.exists()
    assert export_detail(settings,plan["id"])["state"]=="rolled_back"
    assert all(p.is_file() for p in photos.iterdir())


def test_rollback_refuses_modified_output(library):
    settings,photos,exports,digest=library
    plan=create_plan(settings)
    approve(settings,plan["id"])
    run_export(settings,plan["id"],Event())
    target=Path(export_detail(settings,plan["id"])["operations"][0]["destination"])
    target.write_bytes(b"someone-modified-this")
    set_export_state(settings,plan["id"],"rollback","ROLLBACK COPIES")
    rollback_export(settings,plan["id"])
    assert target.read_bytes()==b"someone-modified-this"
    assert export_detail(settings,plan["id"])["state"]=="rollback_partial"


def test_http_endpoints_and_disabled_default(tmp_path: Path,library):
    settings,photos,exports,digest=library
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").json()["features"]["export"] is True
        assert client.get("/api/exports/status").json()["configured"] is True
        res=client.post("/api/exports/plans",json={})
        assert res.status_code==201
        run_id=res.json()["id"]
        assert client.get("/api/exports/"+run_id).status_code==200
        assert client.get("/api/exports/no-such-id").status_code==404
        assert client.post("/api/exports/"+run_id+"/action",json={"action":"approve","confirmation":"NO"}).status_code==409
        assert client.post("/api/exports/"+run_id+"/action",json={"action":"cancel","confirmation":"CANCEL EXPORT"}).status_code==200
    conf=tmp_path / "disabled.toml"
    conf.write_text('data_dir="./disabled-data"\nlibrary_roots=[]\n')
    disabled=load_settings(conf)
    initialize(disabled)
    with TestClient(create_app(disabled)) as client:
        assert client.get("/api/exports/status").json()["configured"] is False
        assert client.post("/api/exports/plans",json={}).status_code==409


def test_export_root_overlap_rejected(tmp_path: Path):
    p=tmp_path/"photos"
    p.mkdir()
    conf=tmp_path/"config.toml"
    conf.write_text('data_dir="./data"\nexport_dir="./photos/out"\nlibrary_roots=["./photos"]\n')
    with pytest.raises(ValueError,match="overlap"):
        load_settings(conf)



def test_partial_rollback_can_be_retried_after_modified_output_is_restored(library):
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    target = Path(export_detail(settings, plan["id"])["operations"][0]["destination"])
    target.write_bytes(b"someone-modified-this")
    set_export_state(settings, plan["id"], "rollback", "ROLLBACK COPIES")
    rollback_export(settings, plan["id"])
    assert export_detail(settings, plan["id"])["state"] == "rollback_partial"
    assert target.exists()
    with pytest.raises(ExportError):
        set_export_state(settings, plan["id"], "retry_rollback", "no")
    # Restore the exact verified export bytes before retrying.
    target.write_bytes((photos / "IMG_20260102.jpg").read_bytes())
    set_export_state(settings, plan["id"], "retry_rollback", "ROLLBACK COPIES")
    rollback_export(settings, plan["id"])
    assert export_detail(settings, plan["id"])["state"] == "rolled_back"
    assert not target.exists()
    assert (photos / "IMG_20260102.jpg").is_file()


def test_rollback_recovers_unlinked_but_unjournaled_copy(library):
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    target = Path(export_detail(settings, plan["id"])["operations"][0]["destination"])
    set_export_state(settings, plan["id"], "rollback", "ROLLBACK COPIES")
    target.unlink()  # Simulate crash after unlink but before SQLite update
    rollback_export(settings, plan["id"])
    assert export_detail(settings, plan["id"])["state"] == "rolled_back"
    with connect(settings.data_dir) as db:
        assert db.execute("SELECT state FROM export_operations WHERE export_id=?",
                          (plan["id"],)).fetchone()[0] == "rolled_back"


def test_manifest_csv_escapes_untrusted_spreadsheet_formulas(library):
    import csv
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    with connect(settings.data_dir) as db:
        db.execute("UPDATE export_operations SET model_reason=? WHERE export_id=?",
                   ("=HYPERLINK(\"https://example.invalid\",\"click\")", plan["id"]))
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    folder = Path(plan["destination"])
    original = json.loads((folder / "manifest.json").read_text())[0]["model_reason"]
    with (folder / "manifest.csv").open(newline="", encoding="utf-8") as stream:
        csv_value = list(csv.DictReader(stream))[0]["model_reason"]
    assert original.startswith("=")
    assert csv_value.startswith("'=")
    assert csv_value[1:] == original


def test_manifest_failure_during_rollback_stays_recoverable(library, monkeypatch):
    import photosift.export as exporter
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    target = Path(export_detail(settings, plan["id"])["operations"][0]["destination"])
    set_export_state(settings, plan["id"], "rollback", "ROLLBACK COPIES")
    original = exporter._write_manifest
    monkeypatch.setattr(exporter, "_write_manifest", lambda *_: (_ for _ in ()).throw(OSError("disk unplugged")))
    rollback_export(settings, plan["id"])
    assert export_detail(settings, plan["id"])["state"] == "rollback_partial"
    assert not target.exists()
    monkeypatch.setattr(exporter, "_write_manifest", original)
    set_export_state(settings, plan["id"], "retry_rollback", "ROLLBACK COPIES")
    rollback_export(settings, plan["id"])
    assert export_detail(settings, plan["id"])["state"] == "rolled_back"


def test_rollback_refuses_identical_byte_replacement(library):
    """A same-hash replacement is not a file created by this export."""
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    target = Path(export_detail(settings, plan["id"])["operations"][0]["destination"])
    with connect(settings.data_dir) as db:
        owned = db.execute(
            "SELECT output_device,output_inode FROM export_operations WHERE export_id=?",
            (plan["id"],)).fetchone()
        assert (owned["output_device"],owned["output_inode"]) == (
            target.stat().st_dev,target.stat().st_ino)
    replacement = target.with_name("different-inode.tmp")
    replacement.write_bytes(target.read_bytes())
    assert replacement.stat().st_ino != target.stat().st_ino
    os.replace(replacement,target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
    set_export_state(settings, plan["id"], "rollback", "ROLLBACK COPIES")
    rollback_export(settings, plan["id"])
    assert target.exists()
    detail = export_detail(settings, plan["id"])
    assert detail["state"] == "rollback_partial"
    assert "replaced" in detail["operations"][0]["error"].lower()
    assert all(p.is_file() for p in photos.iterdir())


def test_rollback_refuses_legacy_output_without_ownership_metadata(library):
    """Migrated prior M6 exports cannot be deleted based on hashes alone."""
    settings, photos, exports, digest = library
    plan = create_plan(settings)
    approve(settings, plan["id"])
    run_export(settings, plan["id"], Event())
    target = Path(export_detail(settings, plan["id"])["operations"][0]["destination"])
    with connect(settings.data_dir) as db:
        db.execute(
            "UPDATE export_operations SET output_device=NULL,output_inode=NULL WHERE export_id=?",
            (plan["id"],))
    set_export_state(settings, plan["id"], "rollback", "ROLLBACK COPIES")
    rollback_export(settings, plan["id"])
    assert target.is_file()
    detail = export_detail(settings, plan["id"])
    assert detail["state"] == "rollback_partial"
    assert "ownership" in detail["operations"][0]["error"].lower()
