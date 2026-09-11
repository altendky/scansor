"""Quarantine an owned workspace before recursive cleanup.

Uses the repository's Linux atomic no-replace primitive and anchored directories.
Unsupported hosts fail without recursively deleting a public destination path.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from scansor.errors import ScansorError
from scansor.files import rename_no_replace
from scansor.mesh_errors import MeshImportError


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
