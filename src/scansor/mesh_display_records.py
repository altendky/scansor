"""Canonical display records and streamed hashes bound before publication."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import BinaryIO, final

import numpy as np

from scansor._plyio import Layout, Writer
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control
from scansor.mesh_display_numeric import (
    DisplayTransform,
    rejected_colors,
    split_ids,
    validity_colors,
    weight_colors,
)
from scansor.mesh_display_ply import (
    FACE_DIGITS,
    VERTEX_DIGITS,
    display_header,
    display_layout,
    validate_export_vertices,
)
from scansor.mesh_errors import MeshImportError
from scansor.mesh_semantics import ColumnSpec


def face_records(dtype: np.dtype, indices: np.ndarray, vertices: int) -> np.ndarray:
    """Preserve source corner order and multiplicity in remapped triangles."""
    if (
        indices.dtype != np.dtype("<i4")
        or indices.ndim != 2
        or indices.shape[1] != 3
        or len(indices) > 65_536
        or np.any(indices < 0)
        or np.any(indices.astype(np.int64) >= vertices)
    ):
        raise MeshImportError(
            "integrity", "display-records", "invalid remapped usable face"
        )
    rows = np.empty(len(indices), dtype=dtype)
    rows["vertex_indices"]["count"] = 3
    rows["vertex_indices"]["values"] = indices
    return rows


def vertex_records(
    dtype: np.dtype,
    kind: str,
    columns: tuple[np.ndarray, ...],
    transform: DisplayTransform,
    *,
    maximum: float,
    face_ids: np.ndarray | None = None,
    corner_ids: np.ndarray | None = None,
    face_status: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    source, _view, x, y, z, normal, status, area_words, weight_words = columns
    xyz = np.column_stack((x, y, z)).view("<f4")
    transformed = transform.apply(xyz)
    records = np.empty(len(source), dtype=dtype)
    for axis, name in enumerate(("x", "y", "z")):
        records[name] = transformed.coordinates[:, axis]
    records["scalar_vertex_status"] = 0
    records["scalar_normal_status"] = normal
    records["scalar_contribution_status"] = status
    areas = area_words.view("<f8")
    records["scalar_raw_area"] = areas
    records["scalar_weight"] = weight_words.view("<f8")
    digits = split_ids(source)
    for digit, name in enumerate(VERTEX_DIGITS):
        records[name] = digits[:, digit]
    if kind == "rejected-face-corners":
        if face_ids is None or corner_ids is None or face_status is None:
            raise MeshImportError(
                "integrity",
                "display-records",
                "rejected corners require complete source keys",
            )
        digits = split_ids(face_ids)
        for digit, name in enumerate(FACE_DIGITS):
            records[name] = digits[:, digit]
        records["scalar_source_corner"] = corner_ids
        records["scalar_face_status"] = face_status
        colors = rejected_colors(face_status)
    else:
        colors = (
            weight_colors(status, areas, maximum=maximum)
            if kind == "weights"
            else validity_colors(status)
        )
    for channel, name in enumerate(("red", "green", "blue")):
        records[name] = colors[:, channel]
    validate_export_vertices(records, kind)
    return records, transformed.roundtrip_mismatch_rows


@final
class PlyOutput:
    def __init__(
        self, path: Path, kind: str, vertices: int, faces: int, chunk_rows: int
    ) -> None:
        self.path: Path = path
        self.layout: Layout = display_layout(
            display_header(kind, vertices, faces), kind
        )
        self.stream: BinaryIO = path.open("xb")
        try:
            self.writer: Writer = Writer(
                self.stream,
                self.layout,
                max_range_bytes=chunk_rows
                * max(e.dtype.itemsize for e in self.layout.elements),
            )
        except BaseException:
            self.stream.close()
            raise
        self.digest = hashlib.sha256(self.layout.header.raw)

    def write(self, element: str, start: int, rows: np.ndarray) -> None:
        self.writer.write_range(element, start, rows)
        # Hash the intended bytes while the caller's bounded arrays are still
        # alive, rather than adopting possibly corrupted staged file contents.
        self.digest.update(memoryview(rows).cast("B"))

    def finish(self) -> dict[str, Control]:
        self.writer.finish()
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        return {
            "name": self.path.name,
            "byte_count": self.layout.byte_count,
            "sha256": self.digest.hexdigest(),
        }

    def close(self) -> None:
        self.stream.close()


@final
class MapOutput:
    def __init__(self, path: Path, rows: int, chunk_rows: int) -> None:
        self.column: Column = Column(
            ColumnSpec(path.name, rows, 1, "<u8"),
            path=path,
            max_range_bytes=chunk_rows * 8,
        )
        self.digest = hashlib.sha256()

    def write(self, start: int, source_ids: np.ndarray) -> None:
        self.column.write_range(start, source_ids)
        self.digest.update(memoryview(source_ids).cast("B"))

    def finish(self) -> dict[str, Control]:
        self.column.finish()
        record = self.column.inventory()
        if record["sha256"] != self.digest.hexdigest():
            raise MeshImportError(
                "integrity",
                "display-map",
                "map differs from intended source row sequence",
            )
        self.column.close()
        return record

    def close(self) -> None:
        self.column.close()
