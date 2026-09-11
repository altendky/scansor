"""Bounded, float-free mesh controls and narrowly owned semantic inventories."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from importlib.metadata import version

import numpy as np

from scansor.errors import ScansorError
from scansor.serialization import parse_canonical_json, sha256

MAX_CONTROL_BYTES = 8 * 1024 * 1024
type Control = bool | int | str | list[Control] | dict[str, Control] | None


def _validate(value: object, *, depth: int = 0) -> None:
    if depth > 64:
        raise ScansorError("mesh control nesting exceeds 64 levels")
    if value is None or type(value) in (bool, int, str):
        return
    if isinstance(value, list):
        for item in value:
            _validate(item, depth=depth + 1)
        return
    if isinstance(value, dict) and all(type(key) is str for key in value):
        for item in value.values():
            _validate(item, depth=depth + 1)
        return
    raise ScansorError(
        "mesh controls permit only objects, arrays, strings, bool, null, integers"
    )


def encode_control(value: Control) -> bytes:
    _validate(value)
    encoder = json.JSONEncoder(
        allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True
    )
    result = bytearray()
    try:
        for piece in encoder.iterencode(value):
            # Check before creating the encoded copy of an oversized string.
            if len(result) + len(piece) + 1 > MAX_CONTROL_BYTES:
                raise ScansorError("mesh control exceeds 8 MiB")
            result.extend(piece.encode("ascii"))
    except (ValueError, OverflowError, RecursionError) as error:
        raise ScansorError(f"invalid mesh control: {error}") from error
    result.extend(b"\n")
    return bytes(result)


def decode_control(data: bytes) -> Control:
    value = parse_canonical_json(data, "mesh control", MAX_CONTROL_BYTES)
    _validate(value)
    return value


def control_id(value: Control) -> str:
    return sha256(encode_control(value))


def control_artifact(name: str, record: dict[str, Control]) -> dict[str, Control]:
    return {
        "name": name,
        "byte_count": len(encode_control(record)),
        "sha256": control_id(record),
    }


_OWNED_FILES: dict[str, tuple[str, ...]] = {
    "generator": (
        "_plyio/format.py",
        "_plyio/stream.py",
        "mesh_controls.py",
        "mesh_numeric.py",
        "mesh_ply.py",
        "mesh_recipes.py",
        "serialization.py",
    ),
    "importer": (
        "_plyio/format.py",
        "_plyio/stream.py",
        "mesh_accumulation.py",
        "mesh_controls.py",
        "mesh_digests.py",
        "mesh_dispositions.py",
        "mesh_numeric.py",
        "mesh_ply.py",
        "mesh_semantics.py",
        "mesh_sidecar.py",
        "mesh_statistics.py",
        "serialization.py",
    ),
    "policy": (
        "mesh_accumulation.py",
        "mesh_controls.py",
        "mesh_digests.py",
        "mesh_numeric.py",
        "mesh_policy.py",
        "mesh_semantics.py",
        "mesh_statistics.py",
        "serialization.py",
    ),
}


def implementation_inventory(owner: str) -> dict[str, Control]:
    """Explicit inventories; no directory walk, repository hash or runtime path.

    S3/S4 must extend their inventory when adding semantic source/dispositions.
    Query semantics also require a bound revision; DuckDB/PyArrow build versions
    and execution-only orchestration do not belong here.
    """
    if owner not in _OWNED_FILES:
        raise ScansorError(f"unknown mesh implementation owner: {owner}")
    root = resources.files("scansor")
    files: list[Control] = []
    for name in _OWNED_FILES[owner]:
        digest = hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
        files.append({"name": name, "sha256": digest})
    return {
        "revision": "mesh-implementation-inventory-v1",
        "owner": owner,
        "files": files,
        "dependencies": {
            "numpy": np.__version__,
            **({"defusedxml": version("defusedxml")} if owner == "importer" else {}),
        },
    }
