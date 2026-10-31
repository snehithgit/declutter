"""M8 Linux export path security: no symlink traversal or unsafe rollback."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from photosift import fs_guard as guard

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux/POSIX security tests")


@pytest.fixture
def photo(tmp_path):
    source, export = tmp_path / "source", tmp_path / "export"
    source.mkdir()
    export.mkdir()
    (source / "inner").mkdir()
    raw = b"fixture-photo-payload" * 2048
    original = source / "inner" / "photo.jpg"
    original.write_bytes(raw)
    return source, export, original, hashlib.sha256(raw).hexdigest(), raw


def test_descriptor_copy_and_rollback_preserve_original(photo):
    source, export, original, digest, raw = photo
    before = original.stat().st_mtime_ns
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    result = guard.copy_verified(source, Path("inner/photo.jpg"), export, destination, digest, len(raw))
    target = export / destination
    assert result == (digest, target.stat().st_dev, target.stat().st_ino)
    assert target.read_bytes() == original.read_bytes() == raw
    assert target.stat().st_mtime_ns == before
    assert guard.remove_verified(export, destination, result[1], result[2], digest)
    assert not target.exists() and original.read_bytes() == raw


def test_collision_cannot_overwrite_preexisting_file(photo):
    source, export, original, digest, raw = photo
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    target = export / destination
    target.parent.mkdir(parents=True)
    target.write_bytes(b"user-owned-output")
    with pytest.raises(FileExistsError):
        guard.copy_verified(source, Path("inner/photo.jpg"), export, destination, digest, len(raw))
    assert target.read_bytes() == b"user-owned-output"
    assert not list(target.parent.glob("*.partial"))


def test_source_parent_symlink_rejected(photo):
    source, export, original, digest, raw = photo
    outside = source.parent / "outside"
    outside.mkdir()
    (source / "inner").rename(source / "former")
    (source / "inner").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        guard.copy_verified(source, Path("inner/photo.jpg"), export,
                            Path("PhotoSift-run/Organized/photo.jpg"), digest, len(raw))
    assert not (export / "PhotoSift-run/Organized/photo.jpg").exists()


def test_export_parent_symlink_rejected(photo):
    source, export, original, digest, raw = photo
    outside = export.parent / "outside"
    outside.mkdir()
    (export / "PhotoSift-run").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        guard.copy_verified(source, Path("inner/photo.jpg"), export,
                            Path("PhotoSift-run/Organized/photo.jpg"), digest, len(raw))
    assert not list(outside.iterdir())


def test_identical_bytes_replacement_not_removed(photo):
    source, export, original, digest, raw = photo
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    _, device, inode = guard.copy_verified(source, Path("inner/photo.jpg"),
                                           export, destination, digest, len(raw))
    target = export / destination
    alternative = target.parent / "replacement.tmp"
    alternative.write_bytes(raw)
    os.replace(alternative, target)
    with pytest.raises(guard.UnsafeFilesystem):
        guard.remove_verified(export, destination, device, inode, digest)
    assert target.read_bytes() == raw


def test_source_swap_after_open_uses_original_verified_inode(photo, monkeypatch):
    source, export, original, digest, raw = photo
    real = guard.digest_fd
    triggered = False

    def swap(fd):
        nonlocal triggered
        result = real(fd)
        if not triggered:
            triggered = True
            original.rename(original.with_name("moved-photo.jpg"))
            original.symlink_to("/etc/passwd")
        return result

    monkeypatch.setattr(guard, "digest_fd", swap)
    result = guard.copy_verified(source, Path("inner/photo.jpg"), export,
                                 Path("PhotoSift-run/Organized/photo.jpg"), digest, len(raw))
    assert result[0] == digest
    assert (export / "PhotoSift-run/Organized/photo.jpg").read_bytes() == raw
    assert original.is_symlink()


def test_output_symlink_replacement_refuses_rollback(photo):
    source, export, original, digest, raw = photo
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    _, device, inode = guard.copy_verified(source, Path("inner/photo.jpg"),
                                           export, destination, digest, len(raw))
    target = export / destination
    target.unlink()
    target.symlink_to(original)
    with pytest.raises(guard.UnsafeFilesystem):
        guard.remove_verified(export, destination, device, inode, digest)
    assert target.is_symlink() and original.read_bytes() == raw


def test_missing_output_reconciles_without_deleting_source(photo):
    source, export, original, digest, raw = photo
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    _, device, inode = guard.copy_verified(source, Path("inner/photo.jpg"),
                                           export, destination, digest, len(raw))
    (export / destination).unlink()
    assert guard.remove_verified(export, destination, device, inode, digest) is False
    assert original.read_bytes() == raw


def test_worker_stop_prevents_output_publication(photo):
    source, export, original, digest, raw = photo
    destination = Path("PhotoSift-run/Organized/photo.jpg")
    with pytest.raises(guard.UnsafeFilesystem, match="stopped"):
        guard.copy_verified(source, Path("inner/photo.jpg"), export,
                            destination, digest, len(raw), stop=lambda: True)
    assert not (export / destination).exists()
    assert original.read_bytes() == raw
    partial_dir = export / destination.parent
    assert not list(partial_dir.glob("*.partial"))
