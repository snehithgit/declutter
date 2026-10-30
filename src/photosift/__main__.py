"""One command owns the API and separate worker lifecycle."""
from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import threading
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_settings
from .db import initialize
from .worker import run_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="PhotoSift M1")
    parser.add_argument("command", choices=["run", "init"], nargs="?", default="run")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args()
    settings = load_settings(args.config)
    if settings.server.host == "0.0.0.0" and os.environ.get("PHOTOSIFT_ALLOW_LAN") != "1":
        parser.error("LAN binding requires PHOTOSIFT_ALLOW_LAN=1 (set in the Zima Compose file)")
    initialize(settings)
    if args.command == "init":
        print(f"Initialized PhotoSift database at {settings.data_dir}")
        return
    # Fail before spawning a worker if this port is already occupied.
    with socket.socket() as probe:
        probe.bind((settings.server.host, settings.server.port))
    ctx = multiprocessing.get_context("spawn")
    stop = ctx.Event()
    process = ctx.Process(target=run_worker, args=(str(args.config.resolve()), stop), name="photosift-worker")
    process.start()
    default_frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    frontend = Path(os.environ.get("PHOTOSIFT_FRONTEND_DIR", str(default_frontend)))
    server = uvicorn.Server(uvicorn.Config(
        create_app(settings, frontend), host=settings.server.host,
        port=settings.server.port, access_log=False,
    ))

    def watch_worker() -> None:
        while not stop.wait(1):
            if not process.is_alive():
                server.should_exit = True
                return

    watcher = threading.Thread(target=watch_worker, daemon=True, name="worker-watchdog")
    watcher.start()
    try:
        server.run()
    finally:
        stop.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    if process.exitcode not in (0, None):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
