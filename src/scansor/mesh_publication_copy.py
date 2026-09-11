"""Bounded closed-file copying onto the publication filesystem when necessary."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from scansor.mesh_errors import MeshImportError
from scansor.mesh_workspace import Workspace, create_workspace, remove_owned_workspace

type FileRecord = tuple[int, str]
type Check = Callable[[], None]


def directory_mount(descriptor: int) -> int:
    """Linux mount ID; st_dev alone misses distinct bind mounts of one device."""
    with Path(f"/proc/self/fdinfo/{descriptor}").open("r", encoding="ascii") as stream:
        raw = stream.read(4097)
    if len(raw) <= 4096:
        for line in raw.splitlines():
            key, _, value = line.partition(":")
            if key == "mnt_id" and value.strip().isdigit():
                return int(value)
    raise MeshImportError(
        "unsupported", "publication", "directory mount ID unavailable"
    )


def copy_stage_files(
    source: int, target: int, expected: dict[str, FileRecord], check: Check
) -> None:
    """Copy fixed expected children; final publication rehashes the entire copy."""
    for name in sorted({key.split("/", 1)[0] for key in expected}):
        children = {
            key.split("/", 1)[1]: record
            for key, record in expected.items()
            if key.startswith(name + "/")
        }
        if children:
            os.mkdir(name, mode=0o700, dir_fd=target)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            first = os.open(name, flags, dir_fd=source)
            try:
                second = os.open(name, flags, dir_fd=target)
                try:
                    copy_stage_files(first, second, children, check)
                finally:
                    os.close(second)
            finally:
                os.close(first)
            continue
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source
        )
        with os.fdopen(descriptor, "rb") as incoming:
            before = os.fstat(incoming.fileno())
            size = expected[name][0]
            if not stat.S_ISREG(before.st_mode) or before.st_size != size:
                raise MeshImportError(
                    "integrity", "publish-copy", "source child changed before copy"
                )
            output = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=target,
            )
            with os.fdopen(output, "wb") as outgoing:
                seen = 0
                while part := incoming.read(min(65_536, size - seen + 1)):
                    check()
                    seen += len(part)
                    if seen > size:
                        raise MeshImportError(
                            "integrity", "publish-copy", "source child grew during copy"
                        )
                    view, done = memoryview(part), 0
                    while done < len(view):
                        count = outgoing.write(view[done:])
                        if type(count) is not int or not 0 < count <= len(view) - done:
                            raise MeshImportError(
                                "execution",
                                "publish-copy",
                                "copy write made no progress",
                            )
                        done += count
                if seen != size:
                    raise MeshImportError(
                        "integrity", "publish-copy", "source child was truncated"
                    )
                outgoing.flush()
                os.fsync(outgoing.fileno())
            after = os.fstat(incoming.fileno())
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns
            ):
                raise MeshImportError(
                    "integrity", "publish-copy", "source child changed during copy"
                )
    os.fsync(target)


@contextmanager
def publication_stage(
    source_parent: int,
    source_name: str,
    source: int,
    expected: dict[str, FileRecord],
    destination: Path,
    target_mount: int,
    kind: str,
    check: Check,
    staging_directory: Path | None,
) -> Generator[tuple[int, str, int]]:
    """Use the original stage on one filesystem, otherwise a verified copy.

    A supervisor provides an already owned destination workspace so it can clean
    interrupted copies after SIGKILL. Direct scoped callers get their own one.
    The caller must verify all copied children before the final atomic rename.
    """
    if directory_mount(source) == target_mount:
        yield source_parent, source_name, source
        return
    workspace: Workspace | None = None
    parent: int | None = None
    held: int | None = None
    failure: BaseException | None = None
    try:
        if staging_directory is None:
            workspace = create_workspace(destination)
            staging_directory = workspace.access
        parent = os.open(staging_directory, os.O_RDONLY | os.O_DIRECTORY)
        if directory_mount(parent) != target_mount:
            raise MeshImportError(
                "integrity",
                "publish-copy",
                "staging is not on the destination filesystem",
            )
        os.mkdir(kind, mode=0o700, dir_fd=parent)
        held = os.open(
            kind, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        copy_stage_files(source, held, expected, check)
        yield parent, kind, held
    except BaseException as error:
        failure = error
        raise
    finally:
        if held is not None:
            os.close(held)
        if parent is not None:
            os.close(parent)
        if workspace is not None:
            try:
                try:
                    remove_owned_workspace(workspace.directory, workspace.identity)
                except BaseException as error:
                    if failure is None:
                        raise
                    failure.add_note(f"Publication workspace cleanup failed: {error}")
            finally:
                workspace.close()
