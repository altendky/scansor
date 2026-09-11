"""Bounded read-only artifact I/O through anchored Linux directory handles."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

from scansor.mesh_controls import MAX_CONTROL_BYTES, Control, decode_control
from scansor.mesh_errors import MeshImportError

type Progress = Callable[[str, int, int], None]


def no_progress(_phase: str, _completed: int, _total: int) -> None:
    pass


def metadata(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class ReadDirectory:
    """A fixed directory entry set; never repairs or removes artifact contents."""

    def __init__(self, path: Path) -> None:
        if sys.platform != "linux" or not Path("/proc/self/fd").is_dir():
            raise MeshImportError(
                "unsupported",
                "artifact-open",
                "anchored Linux artifact operations are unavailable",
            )
        self.path: Path = path
        before = path.stat(follow_symlinks=False)
        self.descriptor: int = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        self.initial: tuple[int, int, int, int, int] = metadata(
            os.fstat(self.descriptor)
        )
        try:
            if metadata(before) != self.initial:
                raise MeshImportError(
                    "integrity", "artifact-open", "directory changed while opening"
                )
        except BaseException:
            os.close(self.descriptor)
            raise
        self.closed: bool = False

    @property
    def access(self) -> Path:
        if self.closed:
            raise MeshImportError("execution", "artifact-read", "directory is closed")
        return Path(f"/proc/self/fd/{self.descriptor}")

    def check(self) -> None:
        if self.closed:
            raise MeshImportError("execution", "artifact-read", "directory is closed")
        try:
            changed = (
                metadata(os.fstat(self.descriptor)) != self.initial
                or metadata(self.path.stat(follow_symlinks=False)) != self.initial
            )
        except OSError as error:
            raise MeshImportError(
                "integrity", "artifact-read", "artifact directory is no longer readable"
            ) from error
        if changed:
            raise MeshImportError(
                "integrity", "artifact-read", "artifact directory changed"
            )

    def require_names(self, expected: set[str]) -> None:
        seen: set[str] = set()
        with os.scandir(self.descriptor) as entries:
            for entry in entries:
                if entry.name not in expected:
                    raise MeshImportError(
                        "integrity",
                        "artifact-open",
                        f"unexpected artifact child: {entry.name}",
                    )
                seen.add(entry.name)
        if seen != expected:
            raise MeshImportError(
                "integrity", "artifact-open", "missing artifact children"
            )
        self.check()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.descriptor)


class ReadFile:
    """Held regular file with mutation checks and bounded hash/control reads."""

    def __init__(self, directory: ReadDirectory, name: str) -> None:
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise MeshImportError(
                "structure", "artifact-open", "invalid fixed child name"
            )
        self.directory: ReadDirectory = directory
        self.path: Path = directory.access / name
        before = self.path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise MeshImportError(
                "integrity",
                "artifact-open",
                "artifact child must be a regular non-symlink file",
            )
        descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        self.stream: BinaryIO = cast(BinaryIO, os.fdopen(descriptor, "rb"))
        self.initial: tuple[int, int, int, int, int] = metadata(os.fstat(descriptor))
        try:
            if self.initial != metadata(before):
                raise MeshImportError(
                    "integrity", "artifact-open", "artifact child changed while opening"
                )
        except BaseException:
            self.stream.close()
            raise
        self.size: int = before.st_size

    def check(self) -> None:
        self.directory.check()
        if self.stream.closed:
            raise MeshImportError(
                "execution", "artifact-read", "artifact file is closed"
            )
        try:
            changed = (
                metadata(os.fstat(self.stream.fileno())) != self.initial
                or metadata(self.path.stat(follow_symlinks=False)) != self.initial
            )
        except OSError as error:
            raise MeshImportError(
                "integrity", "artifact-read", "artifact child is no longer readable"
            ) from error
        if changed:
            raise MeshImportError(
                "integrity", "artifact-read", "artifact child changed"
            )

    def control(self) -> dict[str, Control]:
        self.check()
        if self.size > MAX_CONTROL_BYTES:
            raise MeshImportError(
                "structure", "artifact-control", "control exceeds 8 MiB"
            )
        _ = self.stream.seek(0)
        value = decode_control(self.stream.read(MAX_CONTROL_BYTES + 1))
        self.check()
        if not isinstance(value, dict):
            raise MeshImportError(
                "structure", "artifact-control", "control must be an object"
            )
        return value

    def sha256(self, *, phase: str, progress: Progress = no_progress) -> str:
        self.check()
        _ = self.stream.seek(0)
        seen, digest = 0, hashlib.sha256()
        progress(phase, 0, self.size)
        while part := self.stream.read(65_536):
            seen += len(part)
            if seen > self.size:
                raise MeshImportError(
                    "integrity", "artifact-read", "artifact child grew while hashing"
                )
            digest.update(part)
            progress(phase, seen, self.size)
        self.check()
        if seen != self.size:
            raise MeshImportError(
                "integrity", "artifact-read", "artifact child was truncated"
            )
        progress(phase, seen, self.size)
        return digest.hexdigest()

    def close(self) -> None:
        self.stream.close()
