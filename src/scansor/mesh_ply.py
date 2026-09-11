"""Scansor's binary-triangle-ply-v1 profile over the independent PLY I/O layer."""

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
from scansor.errors import ScansorError

PROFILE = "binary-triangle-ply-v1"
_XYZ = tuple(ScalarProperty(name, "float") for name in ("x", "y", "z"))
_NORMALS = tuple(ScalarProperty(name, "float") for name in ("nx", "ny", "nz"))
_FACE = (ListProperty("vertex_indices", "uchar", "int"),)


class MeshPlyError(ScansorError):
    """Application error retaining the independent format error's context."""

    def __init__(self, error: PlyError) -> None:
        self.detail: PlyError = error
        super().__init__(f"{PROFILE}: {error}")


def mesh_layout(header: Header) -> Layout:
    try:
        if header.object_info:
            raise PlyError(
                "source profile does not admit obj_info metadata",
                category="unsupported",
            )
        if tuple(e.name for e in header.elements) != ("vertex", "face"):
            raise PlyError("requires vertex then face elements", category="unsupported")
        vertex, face = header.elements
        if not 1 <= vertex.count <= 2**31:
            raise PlyError("vertex count must be in 1..2^31", element="vertex")
        if vertex.properties not in (_XYZ, _XYZ + _NORMALS):
            raise PlyError(
                "unsupported ordered XYZ/normal properties",
                category="unsupported",
                element="vertex",
            )
        if face.properties != _FACE:
            raise PlyError(
                "unsupported triangle properties",
                category="unsupported",
                element="face",
            )
        return build_layout(header, fixed_lists={("face", "vertex_indices"): 3})
    except PlyError as error:
        raise MeshPlyError(error) from error


def mesh_header(
    vertex_count: int,
    face_count: int,
    *,
    normals: bool = False,
    comments: tuple[str, ...] = (),
    newline: bytes = b"\n",
) -> Header:
    try:
        header = make_header(
            (
                Element("vertex", vertex_count, _XYZ + (_NORMALS if normals else ())),
                Element("face", face_count, _FACE),
            ),
            comments=comments,
            newline=newline,
        )
        _ = mesh_layout(header)
        return header
    except PlyError as error:
        raise MeshPlyError(error) from error


class MeshPlyReader:
    """Bounded profile adapter; stream ownership and full validation are explicit."""

    def __init__(
        self, stream: BinaryIO, *, max_range_bytes: int, io_block_bytes: int = 65_536
    ) -> None:
        try:
            self.layout: Layout = mesh_layout(read_header(stream))
            self.reader: Reader = Reader(
                stream,
                self.layout,
                max_range_bytes=max_range_bytes,
                io_block_bytes=io_block_bytes,
            )
        except PlyError as error:
            raise MeshPlyError(error) from error

    def read_range(self, element: str, start: int, stop: int) -> np.ndarray:
        try:
            return self.reader.read_range(element, start, stop)
        except PlyError as error:
            raise MeshPlyError(error) from error

    def validate_all(self, *, chunk_rows: int) -> None:
        try:
            self.reader.validate_all(chunk_rows=chunk_rows)
        except PlyError as error:
            raise MeshPlyError(error) from error
