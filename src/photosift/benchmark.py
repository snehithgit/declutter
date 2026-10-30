"""Optional read-only Ollama throughput benchmark: python -m photosift.benchmark."""
from __future__ import annotations
import argparse
import json
import statistics
import time
from pathlib import Path
from .classification import call_ollama, parse_prediction
from .config import load_settings
from .db import connect, initialize

def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark M4 image inference without modifying the database")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.count <= 100:
        parser.error("--count must be 1..100")
    settings = load_settings(Path(args.config))
    initialize(settings)
    with connect(settings.data_dir) as db:
        paths = [row["preview_path"] for row in db.execute("""
            SELECT c.preview_path FROM contents c WHERE c.preview_path IS NOT NULL
            AND EXISTS (SELECT 1 FROM files f JOIN library_roots r ON r.id=f.root_id
              WHERE f.content_hash=c.sha256 AND f.missing=0 AND r.enabled=1 AND f.analysis_supported=1)
            ORDER BY c.sha256 LIMIT ?""", (args.count,))]
    durations = []
    errors = []
    start = time.monotonic()
    for name in paths:
        try:
            preview = (settings.data_dir / name).resolve()
            if not preview.is_relative_to((settings.data_dir / "previews").resolve()):
                raise ValueError("Preview cache path escaped the data directory")
            raw, ms = call_ollama(settings, preview)
            parse_prediction(raw)
            durations.append(ms)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}"[:240])
    elapsed = max(0.001, time.monotonic() - start)
    print(json.dumps({"model":settings.ollama.model,"requested":args.count,
        "attempted":len(paths),"success":len(durations),"errors":errors,
        "average_latency_ms":round(statistics.mean(durations),1) if durations else None,
        "images_per_minute":round(60*len(durations)/elapsed,2)}, indent=2))

if __name__ == "__main__":
    main()
