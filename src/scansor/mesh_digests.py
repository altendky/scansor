"""Canonical ordered row hashes; chunk boundaries are never identity inputs."""

from __future__ import annotations

import hashlib
import struct
from typing import final

import numpy as np

from scansor.mesh_errors import MeshImportError
from scansor.mesh_semantics import ColumnSpec


@final
class RowDigest:
    """Hash a checked contiguous source prefix with bounded packed byte copies."""

    def __init__(
        self, tag: str, specs: tuple[ColumnSpec, ...], *, max_rows: int
    ) -> None:
        if (
            not tag.isascii()
            or not tag
            or "\0" in tag
            or not specs
            or len({spec.rows for spec in specs}) != 1
            or len({spec.name for spec in specs}) != len(specs)
            or type(max_rows) is not int
            or max_rows < 1
        ):
            raise MeshImportError("structure", "row-digest", "invalid digest schema")
        self.specs: tuple[ColumnSpec, ...] = specs
        self.rows: int = specs[0].rows
        self.max_rows: int = max_rows
        self.written: int = 0
        self._digest = hashlib.sha256(
            tag.encode("ascii") + b"\0" + struct.pack("<Q", self.rows)
        )
        # Void fields copy bytes, including signaling/quiet NaN encodings, without
        # asking floating assignment to interpret the payload. No alignment gaps.
        self._dtype: np.dtype = np.dtype(
            [("ordinal", "<u8"), *((spec.name, f"V{spec.stride}") for spec in specs)]
        )

    def update(self, start: int, columns: tuple[np.ndarray, ...]) -> None:
        if (
            type(start) is not int
            or start != self.written
            or len(columns) != len(self.specs)
        ):
            raise MeshImportError(
                "integrity", "row-digest", "missing or reordered source prefix"
            )
        count = len(columns[0]) if columns[0].ndim else -1
        if not 0 <= count <= self.max_rows or start + count > self.rows:
            raise MeshImportError(
                "integrity", "row-digest", "row range exceeds digest bounds"
            )
        for spec, column in zip(self.specs, columns, strict=True):
            shape = (count,) if spec.width == 1 else (count, spec.width)
            if (
                column.shape != shape
                or column.dtype != spec.dtype
                or not column.flags.c_contiguous
            ):
                raise MeshImportError(
                    "structure", "row-digest", "column dtype/shape differs from schema"
                )
        if count:
            records = np.empty(count, dtype=self._dtype)
            records["ordinal"] = np.arange(start, start + count, dtype="<u8")
            for spec, column in zip(self.specs, columns, strict=True):
                records[spec.name] = (
                    column.reshape(count, -1).view(f"V{spec.stride}").reshape(count)
                )
            self._digest.update(memoryview(records).cast("B"))
        self.written += count

    def finish(self) -> str:
        if self.written != self.rows:
            raise MeshImportError(
                "integrity", "row-digest", "incomplete source row coverage"
            )
        return self._digest.hexdigest()
