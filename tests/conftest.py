from __future__ import annotations

import struct
from collections.abc import Sequence
from pathlib import Path


def ply_bytes(
    rows: Sequence[Sequence[float | int]],
    *,
    scalar: str = "float",
    normals: bool = False,
    rgb: bool = False,
) -> bytes:
    names = ["x", "y", "z"]
    if normals:
        names.extend(("nx", "ny", "nz"))
    properties = [f"property {scalar} {name}" for name in names]
    if rgb:
        properties.extend(
            ("property uchar red", "property uchar green", "property uchar blue")
        )
    header = (
        "ply\n"
        + "format binary_little_endian 1.0\n"
        + f"element vertex {len(rows)}\n"
        + "\n".join(properties)
        + "\nend_header\n"
    ).encode("ascii")
    code = "f" if scalar == "float" else "d"
    format_string = "<" + code * len(names) + ("BBB" if rgb else "")
    return header + b"".join(struct.pack(format_string, *row) for row in rows)


def write_ply(
    path: Path,
    rows: Sequence[Sequence[float | int]] | None = None,
    *,
    scalar: str = "float",
    normals: bool = False,
    rgb: bool = False,
) -> Path:
    if rows is None:
        rows = [(1.0, 2.0, 3.0)]
    _ = path.write_bytes(ply_bytes(rows, scalar=scalar, normals=normals, rgb=rgb))
    return path
