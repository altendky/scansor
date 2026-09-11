"""Anchored, independently copied mesh sources; no work uses the original bytes."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from scansor.mesh_controls import Control, control_id
from scansor.mesh_errors import MeshImportError

type Progress = Callable[[str, int, int], None]


def _nothing(_phase: str, _completed: int, _total: int) -> None:
    pass


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _hash(
    stream: BinaryIO, size: int, chunk_bytes: int, progress: Progress, phase: str
) -> str:
    _ = stream.seek(0)
    before = os.fstat(stream.fileno())
    digest = hashlib.sha256()
    completed = 0
    while part := stream.read(chunk_bytes):
        completed += len(part)
        if completed > size:
            raise MeshImportError("integrity", phase, "source grew while hashing")
        digest.update(part)
        progress(phase, completed, size)
    if completed != size:
        raise MeshImportError("integrity", phase, "source changed length while hashing")
    progress(phase, completed, size)
    if _identity(before) != _identity(os.fstat(stream.fileno())):
        raise MeshImportError(
            "integrity", phase, "file changed during hash verification"
        )
    return digest.hexdigest()


@dataclass
class Snapshot:
    role: str
    path: Path
    byte_count: int
    sha256: str
    stream: BinaryIO
    file_identity: tuple[int, int]

    def record(self) -> dict[str, Control]:
        return {"role": self.role, "byte_count": self.byte_count, "sha256": self.sha256}

    def close(self) -> None:
        self.stream.close()


@dataclass
class SourceBundle:
    ply: Snapshot
    sidecar: Snapshot | None

    def inventory(self) -> dict[str, Control]:
        sources: list[Control] = [self.ply.record()]
        if self.sidecar is not None:
            sources.append(self.sidecar.record())
        return {
            "revision": "mesh-source-inventory-v1",
            "sources": sources,
            "sidecar": "absent" if self.sidecar is None else "present",
        }

    @property
    def identity(self) -> str:
        return control_id(self.inventory())

    def close(self) -> None:
        try:
            self.ply.close()
        finally:
            if self.sidecar is not None:
                self.sidecar.close()


def _unlink_owned(path: Path, identity: tuple[int, int], error: BaseException) -> None:
    try:
        current = path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            error.add_note(f"Replaced snapshot path left untouched: {path}")
            return
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as cleanup_error:
        error.add_note(f"Owned snapshot cleanup failed: {cleanup_error}")


def _snapshot(
    source: Path, target: Path, role: str, chunk_bytes: int, progress: Progress
) -> Snapshot:
    phase = f"snapshot-{role}"
    created: tuple[int, int] | None = None
    io_category = "input"
    private: BinaryIO | None = None
    try:
        parent_before = source.parent.stat(follow_symlinks=False)
        entry_before = source.stat(follow_symlinks=False)
        if not stat.S_ISDIR(parent_before.st_mode) or not stat.S_ISREG(
            entry_before.st_mode
        ):
            raise MeshImportError(
                "input", phase, "source and parent must be regular non-symlink entries"
            )
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as original:
            before = os.fstat(original.fileno())
            if not stat.S_ISREG(before.st_mode) or _identity(before) != _identity(
                entry_before
            ):
                raise MeshImportError(
                    "integrity", phase, "source changed while opening"
                )
            if before.st_size < 0:
                raise MeshImportError("input", phase, "negative source length")
            # Exclusive creation; a failed open never gives ownership of a path.
            io_category = "execution"
            with target.open("xb") as destination:
                target_info = os.fstat(destination.fileno())
                created = (target_info.st_dev, target_info.st_ino)
                digest = hashlib.sha256()
                completed = 0
                progress(phase + "-copy", completed, before.st_size)
                while part := original.read(chunk_bytes):
                    if completed + len(part) > before.st_size:
                        raise MeshImportError(
                            "integrity", phase, "source grew while copying"
                        )
                    view = memoryview(part)
                    done = 0
                    while done < len(view):
                        count = destination.write(view[done:])
                        if type(count) is not int or not 0 < count <= len(view) - done:
                            raise MeshImportError(
                                "execution",
                                phase,
                                "snapshot short write made no progress",
                            )
                        done += count
                    digest.update(part)
                    completed += len(part)
                    progress(phase + "-copy", completed, before.st_size)
                destination.flush()
                os.fsync(destination.fileno())
            if completed != before.st_size or _identity(before) != _identity(
                os.fstat(original.fileno())
            ):
                raise MeshImportError(
                    "integrity", phase, "source changed while copying"
                )
            expected = digest.hexdigest()
            second = _hash(
                original,
                before.st_size,
                chunk_bytes,
                progress,
                phase + "-source-rehash",
            )
            if second != expected or _identity(before) != _identity(
                os.fstat(original.fileno())
            ):
                raise MeshImportError(
                    "integrity", phase, "source changed during verification"
                )
            entry = target.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(entry.st_mode)
                or (entry.st_dev, entry.st_ino) != created
            ):
                raise MeshImportError(
                    "integrity", phase, "private snapshot path was replaced"
                )
            private = os.fdopen(os.open(target, flags), "rb")
            opened = os.fstat(private.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != created
            ):
                raise MeshImportError(
                    "integrity", phase, "private snapshot changed while opening"
                )
            if (
                _hash(
                    private,
                    before.st_size,
                    chunk_bytes,
                    progress,
                    phase + "-private-rehash",
                )
                != expected
            ):
                raise MeshImportError(
                    "integrity", phase, "private snapshot differs from source"
                )
            try:
                after = source.stat(follow_symlinks=False)
                parent_after = source.parent.stat(follow_symlinks=False)
            except OSError as error:
                raise MeshImportError(
                    "integrity", phase, "source or parent path disappeared"
                ) from error
            if (
                _identity(after) != _identity(before)
                or not stat.S_ISREG(after.st_mode)
                or not stat.S_ISDIR(parent_after.st_mode)
                or (parent_after.st_dev, parent_after.st_ino)
                != (parent_before.st_dev, parent_before.st_ino)
            ):
                raise MeshImportError(
                    "integrity", phase, "source or parent path changed"
                )
            current = target.stat(follow_symlinks=False)
            if (current.st_dev, current.st_ino) != created:
                raise MeshImportError(
                    "integrity",
                    phase,
                    "private snapshot path changed during verification",
                )
            _ = private.seek(0)
            return Snapshot(role, target, before.st_size, expected, private, created)
    except BaseException as error:
        if private is not None:
            private.close()
        if created is not None:
            _unlink_owned(target, created, error)
        if isinstance(error, OSError):
            category = (
                "resource"
                if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.ENOMEM)
                else io_category
            )
            raise MeshImportError(
                category, phase, f"source snapshot I/O failed: {error}"
            ) from error
        raise


def snapshot_bundle(
    directory: Path,
    ply: Path,
    sidecar: Path | None = None,
    *,
    chunk_bytes: int = 65_536,
    progress: Progress = _nothing,
) -> SourceBundle:
    """Caller supplies an owned private directory; returned streams are anchored.

    Close the bundle before cleanup/publication. Checks detect ordinary mutation,
    not a hostile writer able to restore bytes and metadata between observations.
    """
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 8 * 1024 * 1024:
        raise MeshImportError(
            "resource-budget-too-small", "snapshot", "invalid snapshot I/O chunk size"
        )
    mesh = _snapshot(
        ply, directory / "observations.ply", "mesh-ply", chunk_bytes, progress
    )
    try:
        info = (
            None
            if sidecar is None
            else _snapshot(
                sidecar,
                directory / "observations.rsInfo",
                "realityscan-rsinfo",
                chunk_bytes,
                progress,
            )
        )
        return SourceBundle(mesh, info)
    except BaseException as error:
        mesh.close()
        _unlink_owned(mesh.path, mesh.file_identity, error)
        raise
