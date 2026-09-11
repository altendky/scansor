"""Mesh stage failures are distinct from successful geometric dispositions."""

from __future__ import annotations

import errno

from scansor.errors import ScansorError


class MeshImportError(ScansorError):
    def __init__(
        self, category: str, stage: str, message: str, *, row: int | None = None
    ) -> None:
        self.category: str = category
        self.stage: str = stage
        self.row: int | None = row
        position = "" if row is None else f" at source row {row}"
        super().__init__(f"{stage}: {category}{position}: {message}")


def io_failure(
    error: OSError, stage: str, *, row: int | None = None
) -> MeshImportError:
    category = (
        "resource"
        if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.ENOMEM)
        else "execution"
    )
    return MeshImportError(category, stage, str(error), row=row)
