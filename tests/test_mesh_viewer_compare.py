from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import cast

import pytest

from scansor._plyio import Element, ScalarProperty, make_header
from scansor.mesh_controls import Control
from scansor.mesh_display_ply import (
    FACE_PROPERTIES,
    VERTEX_DIGITS,
    DisplayPlyReader,
    display_header,
)
from scansor.mesh_viewer_compare import compare_viewer_resave
from tests.test_mesh_display_ply import raw_export


def pair(tmp_path: Path, *, duplicate_faces: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "inputs"
    root.mkdir()
    original, resaved = root / "original.ply", root / "resaved.ply"
    raw = raw_export()
    if duplicate_faces:
        payload = raw.split(b"end_header\n", 1)[1]
        raw = display_header("validity", 3, 2).raw + payload + payload[-13:]
    _ = original.write_bytes(raw)
    _ = resaved.write_bytes(raw)
    return original, resaved


def object_field(report: dict[str, Control], name: str) -> dict[str, Control]:
    value = report[name]
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize("chunk", (1, 7))
def test_viewer_comparison_preserves_reordered_vertices_and_cyclic_faces(
    tmp_path: Path, chunk: int
) -> None:
    original, resaved = pair(tmp_path, duplicate_faces=True)
    with original.open("rb") as stream:
        reader = DisplayPlyReader(stream, "validity")
        rows = reader.read_range("vertex", 0, 3)
    # Source order 2,0,1; both (0,1,2) faces remap to (1,2,0),
    # cyclically equivalent to the (2,0,1) records saved below.
    _ = resaved.write_bytes(
        display_header("validity", 3, 2).raw
        + rows[[2, 0, 1]].tobytes()
        + 2 * struct.pack("<Biii", 3, 2, 0, 1)
    )
    report = compare_viewer_resave(
        original, resaved, "validity", tmp_path, chunk_rows=chunk
    )
    mapping = object_field(report, "mapping")
    assert mapping["status"] == "exact" and mapping["reordered_matched_rows"] == 3
    topology = object_field(report, "topology")
    assert topology["status"] == "exact"
    assert topology["original_indistinguishable_duplicate_groups"] == 1
    assert "not recoverable" in str(topology["individual_duplicate_face_identity"])
    assert all(
        cast(dict[str, Control], field)["different"] == 0
        for field in object_field(report, "fields").values()
    )
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize(
    "mutation",
    (
        "reverse-winding",
        "lose-duplicate",
        "duplicate-id",
        "fractional-id",
        "changed-id",
        "renamed-id",
        "renamed-status",
        "precision",
    ),
)
def test_viewer_comparison_reports_degradation_and_mapping_failures(
    tmp_path: Path, mutation: str
) -> None:
    original, resaved = pair(tmp_path, duplicate_faces=True)
    with original.open("rb") as stream:
        reader = DisplayPlyReader(stream, "validity")
        rows = reader.read_range("vertex", 0, 3).copy()
        faces = reader.read_range("face", 0, 2).copy()
        header = reader.layout.header
    if mutation == "reverse-winding":
        faces["vertex_indices"]["values"][0] = (0, 2, 1)
    elif mutation == "lose-duplicate":
        faces = faces[:1]
        header = display_header("validity", 3, 1)
    elif mutation == "duplicate-id":
        rows[VERTEX_DIGITS[0]][1] = 0
    elif mutation == "fractional-id":
        rows[VERTEX_DIGITS[0]][1] = 0.5
    elif mutation == "changed-id":
        rows[VERTEX_DIGITS[0]][1] = 99
    elif mutation in ("renamed-id", "renamed-status"):
        old = (
            VERTEX_DIGITS[0]
            if mutation == "renamed-id"
            else "scalar_contribution_status"
        )
        props = tuple(
            ScalarProperty("renamed_field" if p.name == old else p.name, p.scalar_type)
            for p in header.elements[0].properties
            if isinstance(p, ScalarProperty)
        )
        header = make_header(
            (Element("vertex", 3, props), Element("face", 2, FACE_PROPERTIES))
        )
    elif mutation == "precision":
        rows["x"][0] = 0.25
        rows["scalar_raw_area"][0] = float("-inf")
        rows["scalar_weight"][0] = 0
    _ = resaved.write_bytes(header.raw + rows.tobytes() + faces.tobytes())
    report = compare_viewer_resave(
        original, resaved, "validity", tmp_path, chunk_rows=1
    )
    mapping = object_field(report, "mapping")
    if mutation == "duplicate-id":
        assert mapping["status"] == "ambiguous"
    elif mutation in ("fractional-id", "renamed-id"):
        assert mapping["status"] == "unavailable"
    elif mutation == "changed-id":
        assert mapping["missing_keys"] == mapping["extra_keys"] == 1
    elif mutation == "renamed-status":
        assert report["dropped_or_renamed_fields"] == ["scalar_contribution_status"]
        assert report["added_or_renamed_fields"] == ["renamed_field"]
    elif mutation == "precision":
        fields = object_field(report, "fields")
        assert object_field(fields, "scalar_raw_area")["to_nonfinite"] == 1
        assert object_field(fields, "scalar_weight")["to_zero"] == 1
        assert object_field(fields, "x")["finite_rounding"] == 1
    else:
        assert object_field(report, "topology")["status"] == "changed"


def test_viewer_source_codec_diagnostic_exceeds_single_scalar_precision(
    tmp_path: Path,
) -> None:
    original, resaved = pair(tmp_path)
    reader = DisplayPlyReader(io.BytesIO(raw_export()), "validity")
    rows = reader.read_range("vertex", 0, 3).copy()
    # Standalone codec diagnostic, not a claim that this tiny mesh has 2^64 rows.
    for row, source in enumerate((2**24 + 1, 2**53 + 1, 2**64 - 1)):
        for digit, name in enumerate(VERTEX_DIGITS):
            rows[name][row] = (source >> (16 * digit)) & 65535
    payload = (
        reader.layout.header.raw + rows.tobytes() + struct.pack("<Biii", 3, 0, 1, 2)
    )
    _ = original.write_bytes(payload)
    _ = resaved.write_bytes(payload)
    report = compare_viewer_resave(
        original, resaved, "validity", tmp_path, chunk_rows=1
    )
    assert object_field(report, "mapping")["status"] == "exact"
    assert object_field(report, "topology")["status"] == "exact"
