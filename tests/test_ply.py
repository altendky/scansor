from __future__ import annotations

import math
import struct
from typing import Literal, cast

import numpy as np
import pytest

from scansor.errors import ScansorError
from scansor.ply import canonical_npy, load_canonical_npy, parse_ply
from tests.conftest import ply_bytes


@pytest.mark.parametrize("scalar,source", [("float", "float32"), ("double", "float64")])
@pytest.mark.parametrize(
    "normals,rgb", [(False, False), (True, False), (False, True), (True, True)]
)
def test_accepted_layouts_convert_units_and_preserve_attributes(
    scalar: str, source: str, normals: bool, rgb: bool
) -> None:
    row: tuple[float | int, ...] = (1000.0, -2000.0, 3000.0)
    if normals:
        row += (0.0, 3.0, 4.0)
    if rgb:
        row += (7, 8, 9)
    parsed = parse_ply(
        ply_bytes([row], scalar=scalar, normals=normals, rgb=rgb), "mm", 65_536, 10
    )
    assert parsed.coordinate_source_dtype == source
    assert parsed.position_bounds_m == {
        "x": (1.0, 1.0),
        "y": (-2.0, -2.0),
        "z": (3.0, 3.0),
    }
    assert parsed.normal_magnitude_bounds == ((5.0, 5.0) if normals else None)
    assert parsed.rgb_preserved is rgb
    if rgb:
        assert tuple(
            int(parsed.canonical[name][0]) for name in ("red", "green", "blue")
        ) == (7, 8, 9)
    encoded = canonical_npy(parsed.canonical)
    np.testing.assert_array_equal(load_canonical_npy(encoded), parsed.canonical)


@pytest.mark.parametrize(
    "replacement,message",
    [
        (b"format ascii 1.0", "unsupported PLY"),
        (b"format binary_big_endian 1.0", "unsupported PLY"),
        (b"element face 1", "exactly one vertex"),
        (b"property list uchar int x", "unsupported directive"),
        (b"property float y\nproperty float x", "ordered homogeneous XYZ"),
        (b"property double y", "ordered homogeneous XYZ"),
    ],
)
def test_rejects_unsupported_headers(replacement: bytes, message: str) -> None:
    data = ply_bytes([(1.0, 2.0, 3.0)])
    if replacement.startswith(b"format"):
        data = data.replace(b"format binary_little_endian 1.0", replacement)
    elif replacement.startswith(b"element"):
        data = data.replace(b"element vertex 1", replacement)
    elif replacement.startswith(b"property list"):
        data = data.replace(b"property float x", replacement)
    elif replacement == b"property double y":
        data = data.replace(b"property float y", replacement)
    else:
        data = data.replace(b"property float x\nproperty float y", replacement)
    with pytest.raises(ScansorError, match=message):
        _ = parse_ply(data, "m", 65_536, 10)


@pytest.mark.parametrize("directive", [b"comment nope\n", b"obj_info nope\n", b"\n"])
def test_rejects_comments_obj_info_and_blank_lines(directive: bytes) -> None:
    data = ply_bytes([(1.0, 2.0, 3.0)]).replace(
        b"element vertex 1\n", b"element vertex 1\n" + directive
    )
    with pytest.raises(ScansorError):
        _ = parse_ply(data, "m", 65_536, 10)


def test_rejects_trailing_truncated_empty_and_excess_vertices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = ply_bytes([(1.0, 2.0, 3.0)])
    for data, message in ((valid + b"x", "trailing"), (valid[:-1], "truncated")):
        with pytest.raises(ScansorError, match=message):
            _ = parse_ply(data, "m", 65_536, 10)
    empty = valid.replace(b"element vertex 1", b"element vertex 0")
    with pytest.raises(ScansorError, match="positive decimal"):
        _ = parse_ply(empty, "m", 65_536, 10)
    with pytest.raises(ScansorError, match="vertex count exceeds"):
        _ = parse_ply(valid, "m", 65_536, 0)
    monkeypatch.setattr("scansor.ply.MAX_CANONICAL_BYTES", 1)
    with pytest.raises(ScansorError, match="canonical array would exceed"):
        _ = parse_ply(valid, "m", 65_536, 10)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_nonfinite_coordinates_and_normals(value: float) -> None:
    with pytest.raises(ScansorError, match="coordinates must be finite"):
        _ = parse_ply(ply_bytes([(value, 0.0, 0.0)]), "m", 65_536, 10)
    with pytest.raises(ScansorError, match="normals must be finite"):
        _ = parse_ply(
            ply_bytes([(0.0, 0.0, 0.0, value, 1.0, 0.0)], normals=True),
            "m",
            65_536,
            10,
        )


def test_rejects_zero_or_infinite_magnitude_normals() -> None:
    with pytest.raises(ScansorError, match="normals must be nonzero"):
        _ = parse_ply(
            ply_bytes([(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)], normals=True),
            "m",
            65_536,
            10,
        )
    header = ply_bytes([(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)], scalar="double", normals=True)
    payload = struct.pack("<dddddd", 0.0, 0.0, 0.0, 1.7e308, 1.7e308, 1.7e308)
    data = header[: -6 * 8] + payload
    with pytest.raises(ScansorError, match="magnitudes must be finite"):
        _ = parse_ply(data, "m", 65_536, 10)


def test_direct_api_rejects_unknown_unit() -> None:
    with pytest.raises(ScansorError, match="exactly 'm' or 'mm'"):
        _ = parse_ply(
            ply_bytes([(1.0, 2.0, 3.0)]),
            cast(Literal["m", "mm"], cast(object, "cm")),
            65_536,
            10,
        )
