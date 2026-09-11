from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import cast

import pytest

from scansor.mesh_artifacts import open_contributions, open_import
from scansor.mesh_controls import (
    Control,
    control_artifact,
    control_id,
    decode_control,
    encode_control,
)
from scansor.mesh_errors import MeshImportError
from scansor.mesh_replay import verify_mesh
from tests import mesh_rounding_oracle as oracle
from tests.test_mesh_artifacts import artifact_state, published


def _control(path: Path) -> dict[str, Control]:
    return cast(dict[str, Control], decode_control(path.read_bytes()))


def _save(path: Path, record: dict[str, Control]) -> None:
    _ = path.write_bytes(encode_control(record))


def _row_hash(
    root: Path, tag: str, count: int, descriptors: list[dict[str, Control]]
) -> str:
    # Independent tiny-fixture serialization, without the production RowDigest.
    digest = hashlib.sha256(tag.encode("ascii") + b"\0" + struct.pack("<Q", count))
    columns = [(root / str(item["name"])).read_bytes() for item in descriptors]
    widths = [len(column) // count for column in columns] if count else []
    for index in range(count):
        digest.update(struct.pack("<Q", index))
        for column, width in zip(columns, widths, strict=True):
            digest.update(column[index * width : (index + 1) * width])
    return digest.hexdigest()


def _reseal(first: Path, second: Path) -> tuple[str, str]:
    """Rewrite child hashes, ordered digests, summaries and the cross-stage ID.

    These tests deliberately remove simple hash inconsistency as a rejection
    reason, then require source replay to find the wrong derivation.
    """
    imported, contributions = (
        _control(first / "inventory.json"),
        _control(second / "inventory.json"),
    )
    n, m = int(str(imported["vertices"])), int(str(imported["faces"]))
    for root, inventory in ((first, imported), (second, contributions)):
        descriptors = cast(list[dict[str, Control]], inventory["columns"])
        for descriptor in descriptors:
            raw = (root / str(descriptor["name"])).read_bytes()
            descriptor["byte_count"] = len(raw)
            descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
        if root == first:
            inventory["row_digests"] = {
                "vertices": _row_hash(
                    root, "mesh-import-vertices-v1", n, descriptors[:-3]
                ),
                "faces": _row_hash(root, "mesh-import-faces-v1", m, descriptors[-3:]),
            }
        else:
            inventory["row_digests"] = {
                "vertices": _row_hash(
                    root, "mesh-contribution-vertices-v1", n, descriptors
                )
            }
        summary = _control(root / "summary.json")
        measures = (
            (("usable_face_area_sum", "face-area.bin"),)
            if root == first
            else (
                ("eligible_area_sum", "vertex-area.bin"),
                ("weight_sum", "weight.bin"),
            )
        )
        for name, filename in measures:
            raw = (root / filename).read_bytes()
            words = list(struct.unpack(f"<{len(raw) // 8}Q", raw))
            cast(dict[str, Control], summary[name])["value_bits"] = (
                f"{oracle.fold(words):016x}"
            )
        _save(root / "summary.json", summary)
        inventory["summary"] = control_artifact("summary.json", summary)
    source = cast(dict[str, Control], imported["source"])
    source_ply = cast(list[dict[str, Control]], source["sources"])[0]
    source_ply["sha256"] = hashlib.sha256(
        (first / "source/observations.ply").read_bytes()
    ).hexdigest()
    imported["source_id"] = control_id(source)
    imported["source_frame_id"] = control_id(
        {"revision": "mesh-source-frame-v1", "ply_sha256": source_ply["sha256"]}
    )
    _save(first / "inventory.json", imported)
    new_import_id = control_id(imported)
    request = _control(second / "request.json")
    request["import_id"] = new_import_id
    _save(second / "request.json", request)
    contributions["import_id"] = new_import_id
    contributions["request"] = control_artifact("request.json", request)
    _save(second / "inventory.json", contributions)
    return new_import_id, control_id(contributions)


def _swap(
    path: Path, left: int, right: int, stride: int, *, duplicate: bool = False
) -> None:
    raw = bytearray(path.read_bytes())
    first, second = (
        bytes(raw[left * stride : (left + 1) * stride]),
        bytes(raw[right * stride : (right + 1) * stride]),
    )
    raw[left * stride : (left + 1) * stride] = second
    if not duplicate:
        raw[right * stride : (right + 1) * stride] = first
    _ = path.write_bytes(raw)


@pytest.mark.parametrize(
    "mutation",
    (
        "reordered-vertices",
        "duplicate-vertex",
        "reordered-faces",
        "duplicate-face",
        "wrong-weights",
        "wrong-references",
        "changed-source",
    ),
)
def test_source_replay_rejects_self_consistently_rehashed_artifacts(
    tmp_path: Path, mutation: str
) -> None:
    first, second = published(tmp_path, "unequal-adjacent-v1")
    if mutation == "reordered-vertices":
        _swap(first.path / "xyz.bin", 2, 3, 12)
    elif mutation == "duplicate-vertex":
        _swap(first.path / "xyz.bin", 2, 3, 12, duplicate=True)
    elif mutation == "reordered-faces":
        _swap(first.path / "triangles.bin", 0, 1, 12)
        _swap(first.path / "face-area.bin", 0, 1, 8)
    elif mutation == "duplicate-face":
        _swap(first.path / "triangles.bin", 0, 1, 12, duplicate=True)
    elif mutation == "wrong-weights":
        _swap(second.path / "vertex-area.bin", 2, 3, 8)
        _swap(second.path / "weight.bin", 2, 3, 8)
    elif mutation == "wrong-references":
        _swap(first.path / "reference-count.bin", 0, 2, 8)
    else:
        path = first.path / "source/observations.ply"
        raw = bytearray(path.read_bytes())
        start = raw.index(b"end_header\n") + len(b"end_header\n")
        raw[start + 12 : start + 16] = struct.pack("<f", 6.0)
        _ = path.write_bytes(raw)
    import_id, contribution_id = _reseal(first.path, second.path)
    assert (import_id, contribution_id) != (first.identity, second.identity)
    before = artifact_state(first.path), artifact_state(second.path)
    # Establish that all child hashes, row digests, category totals, request,
    # cross-stage IDs and normalization are consistent with the altered columns.
    with (
        open_import(first.path, chunk_rows=2, expected_id=import_id) as imported,
        open_contributions(second.path, imported, expected_id=contribution_id),
    ):
        pass
    with pytest.raises(MeshImportError) as caught:
        _ = verify_mesh(
            first.path,
            tmp_path,
            contribution_path=second.path,
            expected_import_id=import_id,
            expected_contribution_id=contribution_id,
            chunk_rows=2,
        )
    assert caught.value.stage == "source-replay"
    assert caught.value.category == "integrity"
    assert "differs from source recomputation" in str(caught.value)
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_trusted_root_distinguishes_a_fully_regenerated_different_source(
    tmp_path: Path,
) -> None:
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    old_import, _ = published(first_dir, "right-triangle-orphan-v1")
    new_import, new_contributions = published(second_dir, "unequal-adjacent-v1")
    # A different, valid unsigned artifact has no intrinsic knowledge of history.
    result = verify_mesh(
        new_import.path,
        tmp_path,
        contribution_path=new_contributions.path,
        chunk_rows=2,
    )
    assert result["status"] == "verified"
    with pytest.raises(MeshImportError, match="expected root"):
        _ = verify_mesh(
            new_import.path,
            tmp_path,
            expected_import_id=old_import.identity,
            chunk_rows=2,
        )
