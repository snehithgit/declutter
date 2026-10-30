"""Separate persistent worker process. Job handlers arrive in M2/M4/M6."""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Protocol

from .config import load_settings
from .db import connect, initialize
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


def run_worker(config_path: str, stop: StopEvent) -> None:
    settings = load_settings(Path(config_path))
    initialize(settings)
    configure_logging(settings.data_dir, "worker")
    logger = logging.getLogger("photosift.worker")
    owner = uuid.uuid4().hex
    # Docker may terminate the previous process before its finally block releases
    # the lease. Wait out that short lease instead of failing a rapid restart.
    deadline = time.monotonic() + LEASE_SECONDS + 2
    while not acquire_lease(settings.data_dir, owner, time.time()):
        if stop.is_set():
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("Another PhotoSift worker is active")
        logger.info("Waiting for the previous worker lease to expire")
        stop.wait(1)
    logger.info("Worker ready; M1 has no scan, classification, or export handlers")
    try:
        while not stop.is_set():
            with connect(settings.data_dir) as db:
                updated = db.execute("UPDATE worker_leases SET heartbeat=? WHERE name='main' AND owner=?",
                                     (time.time(), owner)).rowcount
                if updated != 1:
                    raise RuntimeError("Worker lease lost")
            stop.wait(2)
    finally:
        with connect(settings.data_dir) as db:
            db.execute("DELETE FROM worker_leases WHERE name='main' AND owner=?", (owner,))
        logger.info("Worker stopped")
