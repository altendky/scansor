from __future__ import annotations

import errno
import hashlib
import os
import stat
from pathlib import Path

from scansor.errors import ScansorError

READ_CHUNK = 64 * 1024


def _open_directory(path: Path, label: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except (OSError, UnicodeError, ValueError) as error:
        raise ScansorError(
            f"{label} parent must be an existing non-symlink directory: {path}"
        ) from error
    try:
        opened = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise ScansorError(f"cannot inspect {label} parent: {path}") from error
    if not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise ScansorError(f"{label} parent is not a directory: {path}")
    return descriptor


def read_regular(path: Path, label: str, max_bytes: int) -> bytes:
    if not path.name or path.name in {".", ".."}:
        raise ScansorError(f"{label} path must name a file")
    parent_fd = _open_directory(path.parent, label)
    try:
        opened_parent = os.fstat(parent_fd)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        try:
            descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        except (OSError, UnicodeError, ValueError) as error:
            raise ScansorError(
                f"cannot open {label} as a non-symlink file: {path}"
            ) from error
        output = bytearray()
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ScansorError(
                    f"{label} must be a regular non-symlink file: {path}"
                )
            if opened.st_size < 0 or opened.st_size > max_bytes:
                raise ScansorError(f"{label} exceeds the {max_bytes}-byte limit")
            while chunk := stream.read(READ_CHUNK):
                output.extend(chunk)
                if len(output) > max_bytes:
                    raise ScansorError(f"{label} exceeded its size limit while reading")
            after = os.fstat(stream.fileno())
        if len(output) != opened.st_size:
            raise ScansorError(f"{label} changed size while reading")
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        ):
            raise ScansorError(f"{label} changed while reading")
        try:
            final_entry = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            final_parent = os.stat(path.parent, follow_symlinks=False)
        except (OSError, UnicodeError, ValueError) as error:
            raise ScansorError(f"{label} path changed while reading: {path}") from error
        if (final_entry.st_dev, final_entry.st_ino) != (
            opened.st_dev,
            opened.st_ino,
        ) or (final_parent.st_dev, final_parent.st_ino) != (
            opened_parent.st_dev,
            opened_parent.st_ino,
        ):
            raise ScansorError(f"{label} path changed while reading: {path}")
        return bytes(output)
    finally:
        os.close(parent_fd)


def open_run_directory(path: Path) -> int:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except (OSError, UnicodeError, ValueError) as error:
        raise ScansorError(f"run must be a non-symlink directory: {path}") from error
    try:
        opened = os.fstat(descriptor)
    except OSError as error:
        os.close(descriptor)
        raise ScansorError(f"cannot inspect run directory: {path}") from error
    if not stat.S_ISDIR(opened.st_mode):
        os.close(descriptor)
        raise ScansorError(f"run is not a directory: {path}")
    return descriptor


def read_run_file(directory_fd: int, name: str, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise ScansorError(f"cannot open run artifact {name}") from error
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
        os.close(descriptor)
        raise ScansorError(f"run artifact is not a bounded regular file: {name}")
    output = bytearray()
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(READ_CHUNK):
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ScansorError(f"run artifact exceeds size limit: {name}")
        after = os.fstat(stream.fileno())
    if len(output) != opened.st_size:
        raise ScansorError(f"run artifact changed size while reading: {name}")
    if (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ):
        raise ScansorError(f"run artifact changed while reading: {name}")
    return bytes(output)


def hash_run_file(directory_fd: int, name: str, max_bytes: int) -> tuple[int, str]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as error:
        raise ScansorError(f"cannot reopen run artifact {name}") from error
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode) or opened.st_size > max_bytes:
        os.close(descriptor)
        raise ScansorError(f"run artifact is not a bounded regular file: {name}")
    digest = hashlib.sha256()
    byte_count = 0
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(READ_CHUNK):
            digest.update(chunk)
            byte_count += len(chunk)
            if byte_count > max_bytes:
                raise ScansorError(f"run artifact exceeds size limit: {name}")
        after = os.fstat(stream.fileno())
    if byte_count != opened.st_size or (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    ):
        raise ScansorError(f"run artifact changed while hashing: {name}")
    return byte_count, digest.hexdigest()


def write_new_file(directory_fd: int, name: str, data: bytes) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    with os.fdopen(descriptor, "wb") as stream:
        _ = stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
        return os.fstat(stream.fileno())


def rename_no_replace(
    source_directory_fd: int,
    source_name: str,
    target_directory_fd: int,
    target_name: str,
) -> None:
    # Linux renameat2 gives the atomic no-overwrite publication required here.
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ScansorError("atomic no-replace directory publication is unavailable")
    result = renameat2(
        source_directory_fd,
        os.fsencode(source_name),
        target_directory_fd,
        os.fsencode(target_name),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise ScansorError(
            f"output already exists; refusing to overwrite: {target_name}"
        )
    raise ScansorError(f"atomic run publication failed: {os.strerror(error_number)}")
