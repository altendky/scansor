"""Separate narrow PLY profiles for audit exports and observed viewer resaves."""

from __future__ import annotations

from typing import BinaryIO

import numpy as np

from scansor._plyio import (
    Element,
    Header,
    Layout,
    ListProperty,
    PlyError,
    Reader,
    ScalarProperty,
    build_layout,
    make_header,
    read_header,
)
from scansor.mesh_display_numeric import MAX_ROWS, join_ids
from scansor.mesh_errors import MeshImportError

PROFILE = "cloudcompare-audit-v1"
KINDS = ("validity", "weights", "rejected-face-corners")
MAX_VERTICES = 2**31
VERTEX_DIGITS = tuple(f"scalar_source_vertex_{digit}" for digit in range(4))
FACE_DIGITS = tuple(f"scalar_source_face_{digit}" for digit in range(4))
STATUS_FIELDS = (
    "scalar_vertex_status",
    "scalar_normal_status",
    "scalar_contribution_status",
)
SCALAR_FIELDS = (*STATUS_FIELDS, "scalar_raw_area", "scalar_weight", *VERTEX_DIGITS)
CORNER_FIELDS = (*FACE_DIGITS, "scalar_source_corner", "scalar_face_status")
FACE_PROPERTIES = (ListProperty("vertex_indices", "uchar", "int"),)


def vertex_properties(kind: str) -> tuple[ScalarProperty, ...]:
    if kind not in KINDS:
        raise MeshImportError("structure", "display-ply", "unknown view kind")
    fields = SCALAR_FIELDS + (CORNER_FIELDS if kind == "rejected-face-corners" else ())
    return (
        tuple(ScalarProperty(name, "double") for name in ("x", "y", "z"))
        + tuple(ScalarProperty(name, "uchar") for name in ("red", "green", "blue"))
        + tuple(
            ScalarProperty(
                name,
                "double" if name in ("scalar_raw_area", "scalar_weight") else "float",
            )
            for name in fields
        )
    )


def display_layout(header: Header, kind: str, *, resaved: bool = False) -> Layout:
    expected = vertex_properties(kind)
    if header.object_info and not resaved:
        raise MeshImportError(
            "unsupported",
            "display-ply",
            "export profile does not admit obj_info metadata",
        )
    names = tuple(element.name for element in header.elements)
    if names != ("vertex", "face") and not (resaved and names == ("vertex",)):
        raise MeshImportError(
            "unsupported",
            "display-ply",
            "expected vertex and optional resaved face elements",
        )
    vertex = header.elements[0]
    if len(vertex.properties) > 32:
        raise MeshImportError(
            "unsupported",
            "display-ply",
            "display profile permits at most 32 vertex scalar fields",
        )
    if not 0 <= vertex.count <= MAX_VERTICES:
        raise MeshImportError(
            "unsupported", "display-ply", "display vertex count exceeds 0..2^31"
        )
    if not resaved and vertex.properties != expected:
        raise MeshImportError(
            "structure",
            "display-ply",
            "export properties differ from the ordered profile",
        )
    if resaved:
        if any(not isinstance(prop, ScalarProperty) for prop in vertex.properties):
            raise MeshImportError(
                "unsupported", "display-ply", "resaved vertex lists are unsupported"
            )
        actual = {
            prop.name: prop.scalar_type
            for prop in vertex.properties
            if isinstance(prop, ScalarProperty)
        }
        for name in ("x", "y", "z"):
            if actual.get(name) not in ("float", "double"):
                raise MeshImportError(
                    "unsupported",
                    "display-ply",
                    "resaved coordinates require float or double",
                )
        for prop in expected[3:]:
            allowed = (
                ("uchar",)
                if prop.name in ("red", "green", "blue")
                else ("float", "double")
            )
            if prop.name in actual and actual[prop.name] not in allowed:
                raise MeshImportError(
                    "unsupported", "display-ply", "unsupported resaved field encoding"
                )
    fixed: dict[tuple[str, str], int] = {}
    if len(header.elements) == 2:
        face = header.elements[1]
        if face.properties != FACE_PROPERTIES:
            raise MeshImportError(
                "unsupported", "display-ply", "unsupported resaved triangle properties"
            )
        if (vertex.count == 0 or kind == "rejected-face-corners") and face.count != 0:
            raise MeshImportError(
                "structure",
                "display-ply",
                "point-only or empty view cannot contain faces",
            )
        fixed[("face", "vertex_indices")] = 3
    return build_layout(header, fixed_lists=fixed)


def display_header(kind: str, vertices: int, faces: int) -> Header:
    try:
        header = make_header(
            (
                Element("vertex", vertices, vertex_properties(kind)),
                Element("face", faces, FACE_PROPERTIES),
            ),
            comments=(
                f"Scansor {PROFILE} {kind}; display-only",
                "Source bindings, transforms and field meanings are in inventory.json and legend.json",
            ),
        )
        _ = display_layout(header, kind)
        return header
    except PlyError as error:
        raise MeshImportError(error.category, "display-ply", str(error)) from error


def id_values(rows: np.ndarray, names: tuple[str, ...]) -> np.ndarray:
    if any(name not in (rows.dtype.names or ()) for name in names):
        raise MeshImportError(
            "integrity",
            "display-ids",
            "required source ID fields were dropped or renamed",
        )
    return join_ids(np.column_stack([rows[name] for name in names]))


def validate_export_vertices(rows: np.ndarray, kind: str) -> None:
    xyz = np.column_stack([rows[name] for name in ("x", "y", "z")])
    if not np.all(np.isfinite(xyz)):
        raise MeshImportError(
            "integrity", "display-ply", "export contains nonfinite display coordinates"
        )
    allowed = ((0,), (0, 1, 2, 3), (0, 2, 3))
    for name, codes in zip(STATUS_FIELDS, allowed, strict=True):
        if not np.all(np.isin(rows[name], codes)):
            raise MeshImportError(
                "integrity", "display-ply", "invalid export disposition"
            )
    eligible = rows["scalar_contribution_status"] == 0
    for name in ("scalar_raw_area", "scalar_weight"):
        values = rows[name]
        if (
            not np.all(np.isfinite(values))
            or np.any(np.signbit(values))
            or np.any((values > 0) != eligible)
        ):
            raise MeshImportError(
                "integrity", "display-ply", "invalid exported area or weight"
            )
    _ = id_values(rows, VERTEX_DIGITS)
    if kind == "rejected-face-corners":
        _ = id_values(rows, FACE_DIGITS)
        if not np.all(np.isin(rows["scalar_source_corner"], (0, 1, 2))) or not np.all(
            np.isin(rows["scalar_face_status"], (1, 2, 3, 4))
        ):
            raise MeshImportError(
                "integrity",
                "display-ply",
                "invalid rejected-corner disposition or ordinal",
            )


class DisplayPlyReader:
    """Owned bounded rows; resaved floats may be degraded and remain observable."""

    def __init__(
        self,
        stream: BinaryIO,
        kind: str,
        *,
        chunk_rows: int = MAX_ROWS,
        resaved: bool = False,
    ) -> None:
        if type(chunk_rows) is not int or not 1 <= chunk_rows <= MAX_ROWS:
            raise MeshImportError(
                "structure", "display-ply", "invalid display batch bound"
            )
        try:
            self.layout: Layout = display_layout(
                read_header(stream),
                kind,
                resaved=resaved,
            )
            self.kind: str = kind
            self.resaved: bool = resaved
            self.chunk_rows: int = chunk_rows
            # Bound by the largest actual record, including admitted extra scalar fields.
            self.reader: Reader = Reader(
                stream,
                self.layout,
                max_range_bytes=chunk_rows
                * max(e.dtype.itemsize for e in self.layout.elements),
            )
        except PlyError as error:
            raise MeshImportError(error.category, "display-ply", str(error)) from error

    @property
    def vertices(self) -> int:
        return self.layout.elements[0].element.count

    @property
    def faces(self) -> int:
        return (
            0
            if len(self.layout.elements) == 1
            else self.layout.elements[1].element.count
        )

    def fields(self) -> dict[str, str]:
        return {
            prop.name: prop.scalar_type
            for prop in self.layout.elements[0].element.properties
            if isinstance(prop, ScalarProperty)
        }

    def read_range(self, element: str, start: int, stop: int) -> np.ndarray:
        if (
            type(start) is not int
            or type(stop) is not int
            or stop - start > self.chunk_rows
        ):
            raise MeshImportError("structure", "display-ply", "range exceeds row bound")
        try:
            rows = self.reader.read_range(element, start, stop)
            if element == "vertex" and not self.resaved:
                validate_export_vertices(rows, self.kind)
            elif element == "face":
                indices = rows["vertex_indices"]["values"]
                if np.any(indices < 0) or np.any(
                    indices.astype(np.int64) >= self.vertices
                ):
                    raise MeshImportError(
                        "integrity", "display-ply", "view face index is out of range"
                    )
            return rows
        except PlyError as error:
            raise MeshImportError(error.category, "display-ply", str(error)) from error

    def validate_all(self) -> None:
        for element in self.layout.elements:
            for start in range(0, element.element.count, self.chunk_rows):
                _ = self.read_range(
                    element.element.name,
                    start,
                    min(start + self.chunk_rows, element.element.count),
                )
