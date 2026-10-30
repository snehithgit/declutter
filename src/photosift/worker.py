"""Single durable worker and heartbeat, with restart-safe inventory processing."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Protocol

from .config import load_settings
from .db import connect, initialize
from .inventory import next_scan_job, run_scan
from .classification import next_classification_job, run_classification
from .export import next_export_job, run_export, rollback_export
from .logging_setup import configure_logging

LEASE_SECONDS = 15


class StopEvent(Protocol):
    def is_set(self) -> bool: ...
    def wait(self, timeout: float) -> bool: ...


def acquire_lease(data_dir: Path, owner: str, now: float) -> bool:
    with connect(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM worker_leases WHERE name='main'").fetchone()
        if row and now - row["heartbeat"] < LEASE_SECONDS:
            db.rollback()
            return False
        db.execute("INSERT INTO worker_leases VALUES ('main',?,?) "
                   "ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,heartbeat=excluded.heartbeat", (owner, now))
        db.commit()
        return True


class CombinedStop:
    def __init__(self, user_stop: StopEvent, lost: threading.Event):
        self.user_stop, self.lost = user_stop, lost

    def is_set(self) -> bool:
        return self.user_stop.is_set() or self.lost.is_set()


def run_worker(config_path: str, stop: StopEvent) -> None:
    settings = load_settings(Path(config_path))
    initialize(settings)
    configure_logging(settings.data_dir, "worker")
    logger = logging.getLogger("photosift.worker")
    owner = uuid.uuid4().hex
    deadline = time.monotonic() + LEASE_SECONDS + 2
    while not acquire_lease(settings.data_dir, owner, time.time()):
        if stop.is_set():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Another PhotoSift worker is active")
        logger.info("Waiting for the previous worker lease to expire")
        stop.wait(1)
    lost = threading.Event()
    done = threading.Event()

    def heartbeat() -> None:
        while not done.wait(2):
            try:
                with connect(settings.data_dir) as db:
                    updated = db.execute("UPDATE worker_leases SET heartbeat=? WHERE name='main' AND owner=?",
                                         (time.time(), owner)).rowcount
                if updated != 1:
                    raise RuntimeError("Worker lease lost")
            except Exception:
                logger.exception("Worker heartbeat stopped")
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True, name="worker-heartbeat")
    thread.start()
    combined = CombinedStop(stop, lost)
    logger.info("Worker ready for persistent inventory jobs")
    try:
        while not combined.is_set():
            job = next_scan_job(settings)
            if job:
                run_scan(settings, *job, combined)
            else:
                classification = next_classification_job(settings)
                if classification:
                    run_classification(settings, classification, combined)
                else:
                    exporting = next_export_job(settings)
                    if exporting:
                        export_id, state = exporting
                        if state == "rollback_requested":
                            rollback_export(settings, export_id)
                        else:
                            run_export(settings, export_id, combined)
                    else:
                        stop.wait(1)
        if lost.is_set():
            raise RuntimeError("Worker lost its database lease")
    finally:
        done.set()
        thread.join(timeout=5)
        with connect(settings.data_dir) as db:
            db.execute("DELETE FROM worker_leases WHERE name='main' AND owner=?", (owner,))
        logger.info("Worker stopped")
