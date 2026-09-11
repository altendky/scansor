"""Quarantine an owned workspace before recursive cleanup.

Uses the repository's Linux atomic no-replace primitive and anchored directories.
Unsupported hosts fail without recursively deleting a public destination path.
"""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from scansor.errors import ScansorError
from scansor.files import rename_no_replace
from scansor.mesh_errors import MeshImportError, io_failure


@dataclass
class Workspace:
    directory: Path
    descriptor: int
    identity: tuple[int, int]

    @property
    def access(self) -> Path:
        """Operations use the held inode even if its public path is replaced."""
        return Path(f"/proc/self/fd/{self.descriptor}")

    def close(self) -> None:
        os.close(self.descriptor)


def create_workspace(destination: Path) -> Workspace:
    """Create privately, capture its handle, then expose its public name.

    A mkdtemp pathname is never proof of ownership for recursive deletion. The
    bootstrap container is checked empty/private and is only ever removed with
    rmdir, so substituted or concurrently added contents are not adopted/deleted.
    The actual workspace is constructed beneath the private held container and
    its inode is captured before publication into the caller-writable parent.
    """
    if sys.platform != "linux" or not Path("/proc/self/fd").is_dir():
        raise MeshImportError(
            "unsupported",
            "workspace",
            "anchored Linux workspace operations are unavailable",
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(destination, flags)
    parent_identity = os.fstat(parent)
    bootstrap: Path | None = None
    held: int | None = None
    workspace_fd: int | None = None
    created_work = False
    try:
        bootstrap = Path(tempfile.mkdtemp(prefix=".scansor-create-", dir=destination))
        held = os.open(bootstrap, flags)
        opened = os.fstat(held)
        if (
            opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o077
            or os.listdir(held)
        ):
            raise MeshImportError(
                "integrity",
                "workspace",
                "bootstrap directory is not private and empty; left its contents untouched",
            )
        os.mkdir("work", mode=0o700, dir_fd=held)
        created_work = True
        workspace_fd = os.open("work", flags, dir_fd=held)
        actual = os.fstat(workspace_fd)
        if (
            actual.st_uid != os.geteuid()
            or stat.S_IMODE(actual.st_mode) & 0o077
            or os.listdir(workspace_fd)
        ):
            raise MeshImportError(
                "integrity",
                "workspace",
                "new working directory was substituted; no contents adopted",
            )
        identity = (actual.st_dev, actual.st_ino)
        name = ".scansor-mesh-" + secrets.token_hex(16)
        rename_no_replace(held, "work", parent, name)
        created_work = False
        published = os.stat(name, dir_fd=parent, follow_symlinks=False)
        final_parent = destination.stat(follow_symlinks=False)
        if (published.st_dev, published.st_ino) != identity or (
            final_parent.st_dev,
            final_parent.st_ino,
        ) != (parent_identity.st_dev, parent_identity.st_ino):
            raise MeshImportError(
                "integrity",
                "workspace",
                "workspace or destination path changed during publication; no recursive cleanup attempted",
            )
        result = Workspace(destination / name, workspace_fd, identity)
        workspace_fd = None
        return result
    except OSError as error:
        raise io_failure(error, "workspace") from error
    except ScansorError as error:
        if isinstance(error, MeshImportError):
            raise
        raise MeshImportError("execution", "workspace", str(error)) from error
    finally:
        if workspace_fd is not None:
            os.close(workspace_fd)
        if held is not None:
            # Only empty directories can be removed here; never recurse through
            # a bootstrap pathname or assume preexisting content belongs to us.
            if created_work:
                with suppress(OSError):
                    os.rmdir("work", dir_fd=held)
            os.close(held)
        if bootstrap is not None:
            with suppress(OSError):
                bootstrap.rmdir()
        os.close(parent)


def remove_owned_workspace(directory: Path, identity: tuple[int, int]) -> None:
    if not hasattr(os, "O_DIRECTORY") or not shutil.rmtree.avoids_symlink_attacks:
        raise MeshImportError(
            "unsupported", "cleanup", "anchored cleanup is unavailable"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(directory.parent, flags)
    quarantine: Path | None = None
    held: int | None = None
    moved = False
    try:
        quarantine = Path(
            tempfile.mkdtemp(prefix=".scansor-cleanup-", dir=directory.parent)
        )
        held = os.open(quarantine, flags)
        # Do not recursively delete a path in the caller-writable parent. Move it
        # atomically into our private directory, then check the moved entry.
        rename_no_replace(parent, directory.name, held, "candidate")
        moved = True
        actual = os.stat("candidate", dir_fd=held, follow_symlinks=False)
        if (actual.st_dev, actual.st_ino) != identity:
            error = MeshImportError(
                "integrity",
                "cleanup",
                "workspace entry was replaced; no contents deleted",
            )
            try:
                rename_no_replace(held, "candidate", parent, directory.name)
                moved = False
            except ScansorError as restore_error:
                error.add_note(
                    f"Replacement retained at {quarantine / 'candidate'}: {restore_error}"
                )
            raise error
        # The held directory is private and owned. rmtree uses descriptors beneath
        # it, so swapping the original public path cannot redirect recursion.
        shutil.rmtree("candidate", dir_fd=held)
        moved = False
    except BaseException as error:
        if moved and quarantine is not None:
            error.add_note(
                f"Cleanup quarantine retained without further deletion: {quarantine}"
            )
        if isinstance(error, ScansorError) and not isinstance(error, MeshImportError):
            raise MeshImportError("execution", "cleanup", str(error)) from error
        raise
    finally:
        os.close(parent)
        if held is not None:
            os.close(held)
        # Never recurse into a quarantine containing an unverified replacement.
        if quarantine is not None and not moved:
            quarantine.rmdir()
