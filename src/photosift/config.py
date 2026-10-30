"""Validated configuration. Loading it never contacts a library or Ollama."""
from __future__ import annotations

import ctypes
import ntpath
import os
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Server(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1024, le=65535)

    @field_validator("host")
    @classmethod
    def allowed_host(cls, value: str) -> str:
        if value not in {"127.0.0.1", "0.0.0.0"}:
            raise ValueError("host must be 127.0.0.1 or 0.0.0.0")
        return value


class Ollama(StrictModel):
    url: str = "http://127.0.0.1:11434"
    model: str = "hf.co/ggml-org/vllm-d3-nano-GGUF:Q8_0"
    concurrency: int = Field(default=1, ge=1)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment
                or parts.path not in {"", "/"}):
            raise ValueError("Ollama URL must be an HTTP(S) origin without credentials")
        try:
            _ = parts.port
        except ValueError as exc:
            raise ValueError("invalid Ollama port") from exc
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def nonempty_model(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("model cannot be empty")
        return value


class Images(StrictModel):
    thumbnail_size: int = Field(default=256, ge=32)
    preview_size: int = Field(default=1024, ge=128)


class Classification(StrictModel):
    uncertainty_threshold: float = Field(default=0.6, ge=0, le=1)


class Settings(StrictModel):
    data_dir: Path
    library_roots: list[str] = Field(default_factory=list)
    server: Server = Field(default_factory=Server)
    ollama: Ollama = Field(default_factory=Ollama)
    images: Images = Field(default_factory=Images)
    classification: Classification = Field(default_factory=Classification)


def path_key(value: str) -> str:
    """Canonical Windows keys preserve UNC identity and ignore case."""
    if os.name == "nt" or value.startswith("\\\\") or ntpath.splitdrive(value)[0]:
        return ntpath.normcase(ntpath.normpath(value))
    return str(Path(value).resolve())


def require_local_storage(path: Path) -> None:
    text = str(path)
    if text.startswith(("\\\\", "//")):
        raise ValueError("data_dir must use local storage, not a UNC share")
    if os.name == "nt":
        anchor = Path(path).anchor
        if ctypes.windll.kernel32.GetDriveTypeW(anchor) == 4:
            raise ValueError("data_dir cannot use a mapped network drive")
    elif Path("/proc/mounts").exists():
        # Find the longest matching mount, including escaped mount-point spaces.
        matches: list[tuple[int, str]] = []
        for line in Path("/proc/mounts").read_text().splitlines():
            columns = line.split()
            mount = columns[1].replace("\\040", " ").replace("\\134", "\\")
            if path == Path(mount) or Path(mount) in path.parents:
                matches.append((len(mount), columns[2]))
        if matches and max(matches)[1] in {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs"}:
            raise ValueError("data_dir cannot be on a network filesystem")


def load_settings(config_path: Path) -> Settings:
    config_path = config_path.resolve()
    with config_path.open("rb") as stream:
        raw = tomllib.load(stream)
    if "PHOTOSIFT_OLLAMA_URL" in os.environ:
        raw.setdefault("ollama", {})["url"] = os.environ["PHOTOSIFT_OLLAMA_URL"]
    settings = Settings.model_validate(raw)
    data_text = str(settings.data_dir)
    if data_text.startswith(("\\\\", "//")):
        raise ValueError("data_dir must use local storage")
    settings.data_dir = (config_path.parent / settings.data_dir.expanduser()).resolve()
    require_local_storage(settings.data_dir)
    roots: list[str] = []
    for root in settings.library_roots:
        if not root.strip():
            raise ValueError("library root cannot be empty")
        resolved = str((config_path.parent / Path(root).expanduser()).resolve())
        roots.append(resolved)
        root_path = Path(resolved)
        if root_path == settings.data_dir or root_path in settings.data_dir.parents or settings.data_dir in root_path.parents:
            raise ValueError("data_dir and library roots must not overlap")
    if len({path_key(root) for root in roots}) != len(roots):
        raise ValueError("duplicate library roots")
    settings.library_roots = roots
    return settings
