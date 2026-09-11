"""Authoritative mesh shapes, encodings and identity records, without execution settings."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scansor.mesh_controls import Control, control_id, implementation_inventory
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import REVISION as NUMERIC_REVISION
from scansor.mesh_numeric import canonical_f32
from scansor.mesh_ply import PROFILE

ASSOCIATION_REVISION = "source-face-corner-left-association-v1"
PENDING_IMPORT_COLUMNS = frozenset(
    ("reference-count.bin", "face-status.bin", "face-area.bin")
)

_DTYPES = frozenset(("<f4", "<f8", "<i4", "<u8", "u1"))


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    rows: int
    width: int
    encoding: str

    def __post_init__(self) -> None:
        if not self.name.endswith(".bin") or any(
            c not in "abcdefghijklmnopqrstuvwxyz-." for c in self.name
        ):
            raise MeshImportError(
                "structure", "columns", "invalid fixed column filename"
            )
        if (
            type(self.rows) is not int
            or self.rows < 0
            or type(self.width) is not int
            or self.width not in (1, 3)
        ):
            raise MeshImportError("structure", "columns", "invalid column shape")
        if self.encoding not in _DTYPES or self.byte_count > 2**63 - 1:
            raise MeshImportError(
                "structure", "columns", "unsupported dtype or overflowing byte count"
            )

    @property
    def dtype(self) -> np.dtype:
        return np.dtype(self.encoding)

    @property
    def shape(self) -> tuple[int, ...]:
        return (self.rows,) if self.width == 1 else (self.rows, self.width)

    @property
    def stride(self) -> int:
        return self.dtype.itemsize * self.width

    @property
    def byte_count(self) -> int:
        return self.rows * self.stride


def import_specs(vertices: int, faces: int, *, normals: bool) -> tuple[ColumnSpec, ...]:
    if (
        type(vertices) is not int
        or not 1 <= vertices <= 2**31
        or type(faces) is not int
        or faces < 0
    ):
        raise MeshImportError("structure", "columns", "invalid source row counts")
    return (
        ColumnSpec("xyz.bin", vertices, 3, "<f4"),
        *((ColumnSpec("normals.bin", vertices, 3, "<f4"),) if normals else ()),
        ColumnSpec("vertex-status.bin", vertices, 1, "u1"),
        ColumnSpec("normal-status.bin", vertices, 1, "u1"),
        ColumnSpec("reference-count.bin", vertices, 1, "<u8"),
        ColumnSpec("triangles.bin", faces, 3, "<i4"),
        ColumnSpec("face-status.bin", faces, 1, "u1"),
        ColumnSpec("face-area.bin", faces, 1, "<f8"),
    )


def contribution_specs(vertices: int) -> tuple[ColumnSpec, ...]:
    return (
        ColumnSpec("contribution-status.bin", vertices, 1, "u1"),
        ColumnSpec("vertex-area.bin", vertices, 1, "<f8"),
        ColumnSpec("weight.bin", vertices, 1, "<f8"),
    )


def decode_coordinates(rows: np.ndarray, names: tuple[str, str, str]) -> np.ndarray:
    words = np.empty((len(rows), 3), dtype="<u4")
    for axis, name in enumerate(names):
        words[:, axis] = rows[name].view("<u4")
    return canonical_f32(words.view("<f4"))


def foundation_inventory(
    *,
    source: dict[str, Control],
    ply_sha256: str,
    vertices: int,
    faces: int,
    sidecar: dict[str, Control],
    artifacts: list[Control],
) -> dict[str, Control]:
    # Query changes to row/cardinality/order/lookup rules MUST revise this
    # semantic contract even when their SQL lives in execution-only code.
    return {
        "revision": "mesh-import-foundation-v1",
        "status": "foundation-ready",
        "source": source,
        "source_id": control_id(source),
        "profile": PROFILE,
        "numeric_revision": NUMERIC_REVISION,
        "importer_implementation": control_id(implementation_inventory("importer")),
        "association_revision": ASSOCIATION_REVISION,
        "physical_unit": "unknown",
        "source_frame_id": control_id(
            {"revision": "mesh-source-frame-v1", "ply_sha256": ply_sha256}
        ),
        "normal_convention": "unverified-exporter-components",
        "vertices": vertices,
        "faces": faces,
        "sidecar_interpretation": sidecar,
        "columns": artifacts,
        "pending_columns": [str(name) for name in sorted(PENDING_IMPORT_COLUMNS)],
    }
