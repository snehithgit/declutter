"""Synthetic metadata-only scale check; no personal library files are read."""
from pathlib import Path
import time

from photosift.config import load_settings
from photosift.db import initialize, connect
from photosift.review import ReviewFilters, review_page, review_selection


def test_review_pagination_and_frozen_scope_at_100k(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text('data_dir="./data"\nlibrary_roots=["./synthetic-library"]\n')
    settings = load_settings(config)
    initialize(settings)
    count = 100_000
    with connect(settings.data_dir) as db:
        root = db.execute("SELECT id FROM library_roots").fetchone()[0]
        db.execute("BEGIN IMMEDIATE")
        db.executemany("INSERT INTO contents(sha256,size) VALUES (?,100)",
                       ((f"{i:064x}",) for i in range(count)))
        db.executemany("""INSERT INTO files(root_id,relative_path,path_key,size,mtime_ns,ctime_ns,
            extension,width,height,analysis_supported,content_hash)
            VALUES (?,?,?,100,1,1,'.jpg',4000,3000,1,?)""",
            ((root, f"{i}.jpg", f"{i}.jpg", f"{i:064x}") for i in range(count)))
        db.commit()
        start = time.monotonic()
        page = review_page(db, ReviewFilters(), 120, 99_840)
        snapshot = review_selection(db, ReviewFilters())
        elapsed = time.monotonic() - start
    assert page["total"] == snapshot["total"] == count
    assert len(page["items"]) == 120
    assert len(snapshot["digest"]) == 64
    assert not Path(settings.library_roots[0]).exists()
    print(f"100k metadata page + scope: {elapsed:.3f}s")
