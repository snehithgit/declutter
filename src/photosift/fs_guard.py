"""Linux directory-descriptor-anchored access for M8 copy and rollback.

Never follow symlinks in a source/export tree. No path-based reopen between
validation and output publication. Final unlink checks the same open file's
identity and hash again immediately before unlinkat. POSIX does not provide
conditional inode-based unlink, so deployment must keep export directories
private to the PhotoSift user (0700 for newly created directories).
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Callable

CHUNK = 1024 * 1024
DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)


class UnsafeFilesystem(ValueError):
    """Reject a modified/symlinked tree or an unowned output."""


def _components(relative: Path) -> tuple[str, ...]:
    if relative.is_absolute() or not relative.parts or any(p in ("", ".", "..") for p in relative.parts):
        raise UnsafeFilesystem("Invalid path within trusted directory")
    return relative.parts


@contextmanager
def within(root: Path, folders: tuple[str, ...] = (), create: bool = False) -> Iterator[int]:
    """Open a rooted directory chain with O_NOFOLLOW and retain its descriptors."""
    if os.name != "posix":
        raise UnsafeFilesystem("Descriptor-anchored file operations require POSIX")
    fd = os.open(root, DIR_FLAGS)
    try:
        for part in folders:
            if part in ("", ".", "..") or "/" in part:
                raise UnsafeFilesystem("Invalid directory component")
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def digest_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    result = hashlib.sha256()
    while block := os.read(fd, CHUNK):
        result.update(block)
    return result.hexdigest()


def copy_verified(source_root: Path, source_relative: Path, output_root: Path,
                  output_relative: Path, expected_hash: str, expected_size: int,
                  stop: Callable[[], bool] | None = None) -> tuple[str, int, int]:
    """Read exactly one anchored source inode; publish copy with exclusive linkat."""
    src = _components(source_relative)
    dst = _components(output_relative)
    with within(source_root, src[:-1]) as source_dir:
        source_fd = os.open(src[-1], READ_FLAGS, dir_fd=source_dir)
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size != expected_size:
                raise UnsafeFilesystem("Source missing, changed size or is not a regular file")
            if digest_fd(source_fd) != expected_hash:
                raise UnsafeFilesystem("Source hash changed since planning")
            os.lseek(source_fd, 0, os.SEEK_SET)
            with within(output_root, dst[:-1], create=True) as dest_dir:
                temp_name = "." + dst[-1] + "." + uuid.uuid4().hex + ".partial"
                temp_fd = os.open(temp_name, WRITE_FLAGS, 0o600, dir_fd=dest_dir)
                try:
                    with os.fdopen(temp_fd, "wb") as writer:
                        temp_fd = -1  # fdopen owns and closes the descriptor.
                        while block := os.read(source_fd, CHUNK):
                            writer.write(block)
                        writer.flush()
                        os.fchmod(writer.fileno(), stat.S_IMODE(source_stat.st_mode))
                        os.fsync(writer.fileno())
                    check_fd = os.open(temp_name, READ_FLAGS, dir_fd=dest_dir)
                    try:
                        checked = os.fstat(check_fd)
                        if checked.st_size != expected_size or digest_fd(check_fd) != expected_hash:
                            raise UnsafeFilesystem("Copied file failed size/hash verification")
                    finally:
                        os.close(check_fd)
                    os.utime(temp_name, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                             dir_fd=dest_dir, follow_symlinks=False)
                    owned = os.stat(temp_name, dir_fd=dest_dir, follow_symlinks=False)
                    if stop is not None and stop():
                        raise UnsafeFilesystem("Export worker stopped before publication")
                    # linkat is an atomic exclusive publish: existing targets never overwritten.
                    os.link(temp_name, dst[-1], src_dir_fd=dest_dir, dst_dir_fd=dest_dir,
                            follow_symlinks=False)
                    os.fsync(dest_dir)
                    return expected_hash, owned.st_dev, owned.st_ino
                finally:
                    if temp_fd >= 0:
                        os.close(temp_fd)
                    try:
                        os.unlink(temp_name, dir_fd=dest_dir)
                    except FileNotFoundError:
                        pass
        finally:
            os.close(source_fd)


def remove_verified(output_root: Path, output_relative: Path,
                    expected_device: int, expected_inode: int, expected_hash: str) -> bool:
    """Remove a verified file created by this export; false means already absent."""
    dst = _components(output_relative)
    try:
        with within(output_root, dst[:-1]) as parent:
            try:
                info = os.stat(dst[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if not stat.S_ISREG(info.st_mode) or (
                info.st_dev != expected_device or info.st_ino != expected_inode
            ):
                raise UnsafeFilesystem("Output replaced or symlinked; refusing rollback")
            handle = os.open(dst[-1], READ_FLAGS, dir_fd=parent)
            try:
                opened = os.fstat(handle)
                if (opened.st_dev, opened.st_ino) != (expected_device, expected_inode):
                    raise UnsafeFilesystem("Output changed while opening; refusing rollback")
                if digest_fd(handle) != expected_hash:
                    raise UnsafeFilesystem("Output modified; refusing rollback")
                latest = os.stat(dst[-1], dir_fd=parent, follow_symlinks=False)
                if (latest.st_dev, latest.st_ino) != (expected_device, expected_inode):
                    raise UnsafeFilesystem("Output changed before unlink; refusing rollback")
                os.unlink(dst[-1], dir_fd=parent)
                os.fsync(parent)
                return True
            finally:
                os.close(handle)
    except FileNotFoundError:
        return False
