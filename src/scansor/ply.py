from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Literal, cast

import numpy as np

from scansor.errors import ScansorError

_COUNT = re.compile(r"[1-9][0-9]*\Z")
_COORD_PROPERTIES = {
    "float": ("float32", "<f4"),
    "double": ("float64", "<f8"),
}
MAX_CANONICAL_BYTES = 268_435_456
NPY_HEADER_BOUND = 4_096
NPY_MAGIC = b"\x93NUMPY"


@dataclass(frozen=True)
class ParsedPly:
    canonical: np.ndarray
    coordinate_source_dtype: Literal["float32", "float64"]
    fields: tuple[str, ...]
    normal_magnitude_bounds: tuple[float, float] | None
    point_count: int
    position_bounds_m: dict[str, tuple[float, float]]
    rgb_preserved: bool


def _parse_header(
    data: bytes, max_header_bytes: int, max_vertices: int
) -> tuple[int, np.dtype, int, Literal["float32", "float64"], tuple[str, ...]]:
    marker = b"end_header\n"
    marker_index = data.find(marker)
    if marker_index < 0:
        raise ScansorError("PLY header lacks an exact end_header line")
    header_size = marker_index + len(marker)
    if header_size > max_header_bytes:
        raise ScansorError("PLY header exceeds configured limit")
    try:
        lines = data[:header_size].decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ScansorError("PLY header must be ASCII") from error
    if len(lines) < 6 or lines[:2] != ["ply", "format binary_little_endian 1.0"]:
        raise ScansorError("unsupported PLY magic or format")
    if lines[-1] != "end_header" or any(not line for line in lines):
        raise ScansorError("PLY header contains an invalid or blank line")
    element = lines[2].split(" ")
    if len(element) != 3 or element[:2] != ["element", "vertex"]:
        raise ScansorError("PLY must contain exactly one vertex element")
    if not _COUNT.fullmatch(element[2]):
        raise ScansorError("PLY vertex count must be a positive decimal integer")
    try:
        count = int(element[2])
    except ValueError as error:
        raise ScansorError("PLY vertex count is too large to parse") from error
    if count > max_vertices:
        raise ScansorError("PLY vertex count exceeds configured limit")
    property_lines = lines[3:-1]
    properties: list[tuple[str, str]] = []
    for line in property_lines:
        parts = line.split(" ")
        if len(parts) != 3 or parts[0] != "property":
            raise ScansorError("PLY header contains an unsupported directive")
        properties.append((parts[1], parts[2]))
    if len(properties) not in {3, 6, 9}:
        raise ScansorError("PLY property set is incomplete or unsupported")
    coordinate_type = properties[0][0]
    if coordinate_type not in _COORD_PROPERTIES:
        raise ScansorError("PLY XYZ coordinates must use float or double")
    if properties[:3] != [
        (coordinate_type, "x"),
        (coordinate_type, "y"),
        (coordinate_type, "z"),
    ]:
        raise ScansorError("PLY requires ordered homogeneous XYZ properties")
    remaining = properties[3:]
    normals = [
        (coordinate_type, "nx"),
        (coordinate_type, "ny"),
        (coordinate_type, "nz"),
    ]
    rgb = [("uchar", "red"), ("uchar", "green"), ("uchar", "blue")]
    if remaining not in ([], normals, rgb, normals + rgb):
        raise ScansorError("PLY optional normals/RGB must be complete and ordered")
    source_name, source_code = _COORD_PROPERTIES[coordinate_type]
    fields = tuple(name for _, name in properties)
    dtype = np.dtype(
        [
            (name, source_code if kind == coordinate_type else "u1")
            for kind, name in properties
        ]
    )
    return (
        header_size,
        dtype,
        count,
        cast(Literal["float32", "float64"], source_name),
        fields,
    )


def _row_magnitudes(values: np.ndarray) -> np.ndarray:
    scales = np.max(np.abs(values), axis=1)
    if np.any(scales == 0.0):
        raise ScansorError("PLY normals must be nonzero")
    with np.errstate(over="ignore", invalid="ignore"):
        magnitudes = scales * np.sqrt(np.sum((values / scales[:, None]) ** 2, axis=1))
    if not np.isfinite(magnitudes).all():
        raise ScansorError("PLY normal magnitudes must be finite")
    return magnitudes


def parse_ply(
    data: bytes,
    unit: Literal["m", "mm"],
    max_header_bytes: int,
    max_vertices: int,
) -> ParsedPly:
    if unit not in {"m", "mm"}:
        raise ScansorError("PLY input unit must be exactly 'm' or 'mm'")
    header_size, source_dtype, count, source_name, fields = _parse_header(
        data, max_header_bytes, max_vertices
    )
    has_normals = "nx" in fields
    has_rgb = "red" in fields
    canonical_row_bytes = 24 + (24 if has_normals else 0) + (3 if has_rgb else 0)
    if count * canonical_row_bytes + NPY_HEADER_BOUND > MAX_CANONICAL_BYTES:
        raise ScansorError("canonical array would exceed its internal byte limit")
    payload_size = count * source_dtype.itemsize
    expected_size = header_size + payload_size
    if expected_size != len(data):
        detail = "trailing bytes" if len(data) > expected_size else "truncated payload"
        raise ScansorError(f"PLY byte count mismatch: {detail}")
    source = np.frombuffer(data, dtype=source_dtype, count=count, offset=header_size)
    coordinates = np.column_stack([source[name] for name in ("x", "y", "z")]).astype(
        "<f8", copy=False
    )
    if not np.isfinite(coordinates).all():
        raise ScansorError("PLY coordinates must be finite")
    coordinates *= 1.0 if unit == "m" else 0.001
    if not np.isfinite(coordinates).all():
        raise ScansorError("canonical metre coordinates must be finite")
    canonical_fields: list[tuple[str, str]] = [
        ("x_m", "<f8"),
        ("y_m", "<f8"),
        ("z_m", "<f8"),
    ]
    normals = None
    magnitudes = None
    if has_normals:
        normals = np.column_stack([source[name] for name in ("nx", "ny", "nz")]).astype(
            "<f8", copy=False
        )
        if not np.isfinite(normals).all():
            raise ScansorError("PLY normals must be finite")
        magnitudes = _row_magnitudes(normals)
        canonical_fields.extend((name, "<f8") for name in ("nx", "ny", "nz"))
    if has_rgb:
        canonical_fields.extend((name, "u1") for name in ("red", "green", "blue"))
    canonical_dtype = np.dtype(canonical_fields)
    canonical = np.empty(count, dtype=canonical_dtype)
    for index, name in enumerate(("x_m", "y_m", "z_m")):
        canonical[name] = coordinates[:, index]
    if normals is not None:
        for index, name in enumerate(("nx", "ny", "nz")):
            canonical[name] = normals[:, index]
    if has_rgb:
        for name in ("red", "green", "blue"):
            canonical[name] = source[name]
    bounds = {
        axis: (
            float(np.min(coordinates[:, index])),
            float(np.max(coordinates[:, index])),
        )
        for index, axis in enumerate(("x", "y", "z"))
    }
    normal_bounds = (
        (float(np.min(magnitudes)), float(np.max(magnitudes)))
        if magnitudes is not None
        else None
    )
    return ParsedPly(
        canonical=canonical,
        coordinate_source_dtype=source_name,
        fields=fields,
        normal_magnitude_bounds=normal_bounds,
        point_count=count,
        position_bounds_m=bounds,
        rgb_preserved=has_rgb,
    )


def canonical_npy(array: np.ndarray) -> bytes:
    stream = io.BytesIO()
    np.lib.format.write_array(stream, array, version=(2, 0), allow_pickle=False)
    return stream.getvalue()


def load_canonical_npy(data: bytes) -> np.ndarray:
    if not data.startswith(NPY_MAGIC):
        raise ScansorError("canonical.npy lacks the required NPY magic")
    if len(data) < 12 or data[6:8] != b"\x02\x00":
        raise ScansorError("canonical.npy must use the internal NPY v2 encoding")
    header_bytes = int.from_bytes(data[8:12], "little")
    if header_bytes > NPY_HEADER_BOUND or 12 + header_bytes > len(data):
        raise ScansorError("canonical.npy header exceeds its internal bound")
    stream = io.BytesIO(data)
    try:
        version = np.lib.format.read_magic(stream)
        if version != (2, 0):
            raise ScansorError("canonical.npy must use the internal NPY v2 encoding")
        shape, fortran_order, dtype = np.lib.format.read_array_header_2_0(stream)
    except ScansorError:
        raise
    except Exception as error:
        raise ScansorError(f"canonical.npy is invalid: {error}") from error
    if len(shape) != 1 or shape[0] < 0 or dtype.hasobject or fortran_order:
        raise ScansorError("canonical.npy has an unsupported array shape or dtype")
    offset = stream.tell()
    payload_bytes = shape[0] * dtype.itemsize
    if offset + payload_bytes != len(data):
        raise ScansorError("canonical.npy header and payload size do not match")
    try:
        array = np.frombuffer(data, dtype=dtype, count=shape[0], offset=offset)
    except Exception as error:
        raise ScansorError(f"canonical.npy payload is invalid: {error}") from error
    try:
        encoded = canonical_npy(array)
    except Exception as error:
        raise ScansorError(f"canonical.npy dtype is not canonical: {error}") from error
    if encoded != data:
        raise ScansorError("canonical.npy is not in the internal canonical encoding")
    return array
