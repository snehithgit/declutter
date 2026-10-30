"""Rotating process-specific logs on the configured local disk."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(data_dir: Path, process: str) -> None:
    root = logging.getLogger("photosift")
    root.setLevel(logging.INFO)
    root.handlers.clear()
    handler = RotatingFileHandler(data_dir / "logs" / f"{process}.log", maxBytes=5_000_000,
                                 backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
