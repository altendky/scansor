"""Vertex-based STL observations have explicit topology, normals and identity."""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, BinaryIO, override

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.feature_graph import Recipe, Source
from experiments.scan_actions import preflight_actions
from experiments.scan_project import ScanProject
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import (
    INTERPRETATION_SHA256,
    STL_INTERPRETATION_SHA256,
    STL_PROFILE,
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    SourceDescriptor,
    prepare_scan,
    prepare_scan_input,
    supported_source_interpretation,
)
from experiments.selection_growth import connected_growth, fit_seed
from tests.native_scan_fixture import native_grid_fixture
from tests.test_scan_sources import SequentialInput

Point = tuple[float, float, float]
Face = tuple[int, int, int]
LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000, chunk_bytes=240)


def stl_bytes(
    vertices: list[Point],
    faces: list[Face],
    *,
    encoding: str = "binary",
    normal: Point = (0, 0, -7),
    header: bytes = b"independent fixture",
) -> bytes:
    if encoding == "binary":
        return (
            header.ljust(80, b"\0")
            + struct.pack("<I", len(faces))
            + b"".join(
                struct.pack(
                    "<12fH",
                    *normal,
                    *(value for index in face for value in vertices[index]),
                    0,
                )
                for face in faces
            )
        )
    lines = ["solid fixture"]
    for face in faces:
        lines.extend(
            ("facet normal " + " ".join(str(value) for value in normal), "outer loop")
        )
        lines.extend(
            "vertex " + " ".join(str(value) for value in vertices[index])
            for index in face
        )
        lines.extend(("endloop", "endfacet"))
    lines.append("endsolid fixture")
    return ("\n".join(lines) + "\n").encode("ascii")


def source_recipe(workspace: ScanWorkspace) -> Recipe:
    assert workspace.descriptor is not None
    return Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Scan",
                operation="source",
                source_sha256=workspace.descriptor.source_sha256,
                reference_sha256=workspace.reference_sha256,
                descriptor=workspace.descriptor,
            )
        ],
        output="scan",
    )


def independent_oracle(workspace: ScanWorkspace) -> tuple[list[float], list[Point]]:
    weights = [0.0] * len(workspace.local)
    sums = [[0.0, 0.0, 0.0] for _ in workspace.local]
    for face in workspace.data.triangles:
        a, b, c = (
            tuple(float(value) for value in workspace.local[index]) for index in face
        )
        u = [b[axis] - a[axis] for axis in range(3)]
        v = [c[axis] - a[axis] for axis in range(3)]
        vector = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        third = math.sqrt(sum(value * value for value in vector)) / 6
        for index in face:
            weights[index] += third
            for axis in range(3):
                sums[index][axis] += vector[axis]
    normals: list[Point] = []
    for vector in sums:
        length = math.sqrt(sum(value * value for value in vector))
        normals.append((vector[0] / length, vector[1] / length, vector[2] / length))
    return weights, normals


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
def test_exact_topology_first_occurrence_ids_weights_derived_normals_and_replay(
    encoding: str,
) -> None:
    vertices: list[Point] = [(0, -0.0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    raw = stl_bytes(vertices, [(2, 3, 0), (2, 0, 1)], encoding=encoding)
    authority = SequentialInput(raw, short=7)
    workspace = prepare_scan(authority, "not-an-stl.ply", LIMITS)
    assert workspace.descriptor is not None and workspace.source is not None
    descriptor = workspace.descriptor
    assert descriptor.profile == STL_PROFILE
    assert descriptor.interpretation_sha256 == STL_INTERPRETATION_SHA256
    assert descriptor.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert descriptor.vertices == 4 and descriptor.triangles == 2
    assert workspace.source.input is authority and not hasattr(
        workspace, "original_bytes"
    )
    assert authority.opens == authority.closes == 2  # sniff/count share a replay
    assert authority.read_bytes == 2 * len(raw)
    assert max(authority.requests) <= LIMITS.chunk_bytes
    np.testing.assert_array_equal(
        workspace.local, [vertices[2], vertices[3], vertices[0], vertices[1]]
    )
    np.testing.assert_array_equal(workspace.data.triangles, [[0, 1, 2], [0, 2, 3]])
    expected_weights, expected_normals = independent_oracle(workspace)
    np.testing.assert_allclose(workspace.data.weights, expected_weights, rtol=1e-15)
    np.testing.assert_array_equal(
        workspace.data.original_normals, np.asarray(expected_normals, dtype="<f4")
    )
    np.testing.assert_array_equal(workspace.data.normals, [[0, 0, 1]] * 4)
    assert not np.signbit(workspace.local[workspace.local == 0]).any()
    assert (
        not workspace.local.flags.writeable
        and not workspace.data.normals.flags.writeable
    )
    replayed = prepare_scan_input(workspace.source, replace(LIMITS, chunk_bytes=48))
    assert replayed.reference_sha256 == workspace.reference_sha256
    np.testing.assert_array_equal(replayed.data.triangles, workspace.data.triangles)
    np.testing.assert_array_equal(replayed.data.normals, workspace.data.normals)
    metadata = workspace.metadata()
    assert metadata["normal_source"] == "geometry-area-weighted-smoothed"
    assert metadata["capabilities"] == {
        "max_sources": 1,
        "profile": STL_PROFILE,
        "normals_required": False,
    }
    assert preflight_actions(source_recipe(workspace).model_dump()).kind == "generic"


def test_near_coordinates_do_not_weld_and_signed_zeros_share_exactly() -> None:
    vertices: list[Point] = [
        (0, 0, 0),
        (1, 0, 0),
        (0, 1, 0),
        (-0.0, 0, -0.0),
        (0, 1 + 2.0**-23, 0),
    ]
    raw = stl_bytes(vertices, [(0, 1, 2), (3, 1, 4)])
    workspace = prepare_scan_input(BytesInput(raw, "exact.stl"), LIMITS)
    assert len(workspace.local) == 4
    np.testing.assert_array_equal(workspace.data.triangles, [[0, 1, 2], [0, 1, 3]])
    assert workspace.local[2, 1] != workspace.local[3, 1]


def test_ascii_declared_rounding_matches_binary_geometry_without_sharing_byte_identity() -> (
    None
):
    vertices: list[Point] = [(1, 0, 0), (2, 0, 0), (1, 1, 0)]
    ascii_raw = stl_bytes(vertices, [(0, 1, 2)], encoding="ascii").replace(
        b"vertex 1 0 0", b"vertex 1.000000059604644775390625 -1e-100 0"
    )
    binary_raw = stl_bytes(vertices, [(0, 1, 2)])
    first = prepare_scan_input(BytesInput(ascii_raw, "ascii.stl"), LIMITS)
    second = prepare_scan_input(BytesInput(binary_raw, "binary.stl"), LIMITS)
    np.testing.assert_array_equal(first.local, second.local)
    np.testing.assert_array_equal(first.data.weights, second.data.weights)
    assert first.descriptor is not None and second.descriptor is not None
    assert (
        first.descriptor.interpretation_sha256
        == second.descriptor.interpretation_sha256
    )
    assert first.source_sha256 != second.source_sha256
    assert first.reference_sha256 != second.reference_sha256


def test_face_order_duplicates_and_degenerate_accounting_are_retained() -> None:
    vertices: list[Point] = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (0, 1, 0), (2, 1, 0)]
    faces: list[Face] = [
        (0, 1, 3),
        (1, 4, 3),
        (1, 2, 4),
        (0, 1, 3),
        (0, 0, 1),
        (0, 1, 2),
    ]
    workspace = prepare_scan_input(
        BytesInput(stl_bytes(vertices, faces), "mixed.stl"), LIMITS
    )
    np.testing.assert_array_equal(workspace.data.face_status, [0, 0, 0, 0, 3, 4])
    np.testing.assert_array_equal(
        workspace.local[workspace.data.triangles], np.asarray(vertices)[faces]
    )
    expected, _ = independent_oracle(workspace)
    np.testing.assert_allclose(workspace.data.weights, expected, rtol=1e-15)
    assert workspace.data.weights.sum() == pytest.approx(2.5)
    assert (
        workspace.diagnostics.repeated_index_faces
        == workspace.diagnostics.zero_area_faces
        == 1
    )


@pytest.mark.parametrize("problem", ["zero-area", "cancelled", "zero-only-extra"])
def test_unusable_generated_normals_reject_without_hidden_repair(problem: str) -> None:
    vertices: list[Point] = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    faces: list[Face] = [(0, 1, 2)]
    if problem == "zero-area":
        vertices[2] = (2, 0, 0)
    if problem == "cancelled":
        faces.append((0, 2, 1))
    if problem == "zero-only-extra":
        vertices.append((9, 9, 9))
        faces.append((3, 3, 3))
    with pytest.raises(ScanImportError) as error:
        _ = prepare_scan_input(
            BytesInput(stl_bytes(vertices, faces), "invalid.stl"), LIMITS
        )
    assert error.value.code == "invalid_stl_normals"
    assert "first IDs" in str(error.value)
    assert "positive-area directions" in str(error.value)


@pytest.mark.parametrize("header", [b"ply\n", b"ply\r\n", b"solid", b"plain binary"])
def test_binary_layout_precedes_text_magic_and_filename_hints(header: bytes) -> None:
    raw = stl_bytes([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)], header=header)
    workspace = prepare_scan_input(BytesInput(raw, "looks-like-ply.ply"), LIMITS)
    assert (
        workspace.descriptor is not None and workspace.descriptor.profile == STL_PROFILE
    )


def test_ply_hash_and_registry_are_unchanged_and_profile_cross_hashes_reject() -> None:
    assert (
        INTERPRETATION_SHA256
        == "60899f6e2a9409a787106e123a5e893ed4a9110191f27831e63d2824135f9622"
    )
    raw = native_grid_fixture(5, 5).raw
    workspace = prepare_scan_input(BytesInput(raw, "actually-ply.stl"), LIMITS)
    assert workspace.descriptor is not None
    assert workspace.descriptor.profile == "binary-triangle-ply-v1"
    assert workspace.descriptor.interpretation_sha256 == INTERPRETATION_SHA256
    assert "normal_source" not in workspace.metadata()
    assert supported_source_interpretation(workspace.descriptor)
    changed = workspace.descriptor.model_copy(update={"profile": STL_PROFILE})
    assert not supported_source_interpretation(changed)
    payload = source_recipe(workspace).model_dump()
    payload["nodes"][0]["descriptor"] = changed.model_dump()
    payload["nodes"][0]["reference_sha256"] = changed.binding_sha256
    with pytest.raises(ScanImportError) as error:
        _ = preflight_actions(payload)
    assert error.value.code == "unsupported_interpretation"
    with pytest.raises(ValidationError):
        _ = SourceDescriptor.model_validate(
            {**workspace.descriptor.model_dump(), "profile": "unknown"}
        )


def test_work_memory_and_vertex_limits_are_enforced_without_early_full_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = stl_bytes([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)] * 20)
    allocated: list[Any] = []
    original = np.empty

    def observe(shape: Any, *args: Any, **kwargs: Any) -> Any:
        allocated.append(shape)
        return original(shape, *args, **kwargs)

    monkeypatch.setattr(np, "empty", observe)
    with pytest.raises(ScanImportError) as memory:
        _ = prepare_scan_input(
            BytesInput(raw, "limited.stl"), replace(LIMITS, max_estimated_bytes=1)
        )
    assert memory.value.code == "source_limit"
    assert (60, 3) not in allocated
    for limits in (
        replace(LIMITS, max_vertices=2),
        replace(LIMITS, max_faces=19),
        replace(LIMITS, max_source_bytes=len(raw) - 1),
    ):
        with pytest.raises(ScanImportError) as cap:
            _ = prepare_scan_input(BytesInput(raw, "limited.stl"), limits)
        assert cap.value.code == "source_limit"
    admitted = prepare_scan_input(
        BytesInput(raw, "shared.stl"), replace(LIMITS, max_vertices=3)
    )
    assert len(admitted.local) == 3 and len(admitted.data.triangles) == 20


@pytest.mark.parametrize(
    "phase",
    [
        "stl-inspect",
        "stl-corners",
        "stl-connectivity",
        "faces",
        "stl-normals",
        "prepared",
    ],
)
def test_cancellation_never_returns_partial_geometry(phase: str) -> None:
    raw = stl_bytes([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)] * 20)
    cancelled = False

    def progress(update: ScanProgress) -> None:
        nonlocal cancelled
        cancelled = update.phase == phase

    with pytest.raises(ScanCancelled):
        _ = prepare_scan_input(
            BytesInput(raw, "cancel.stl"),
            LIMITS,
            progress=progress,
            cancelled=lambda: cancelled,
        )


def test_smoothed_corner_normals_preserve_sharp_positions_but_can_reject_narrow_growth() -> (
    None
):
    vertices: list[Point] = [
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    ]
    faces: list[Face] = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    workspace = prepare_scan_input(
        BytesInput(stl_bytes(vertices, faces, normal=(0, 0, 0)), "sharp.stl"), LIMITS
    )
    assert len(workspace.local) == 8
    expected_weights, expected_normals = independent_oracle(workspace)
    np.testing.assert_allclose(workspace.data.weights, expected_weights, rtol=1e-15)
    expected_raw = np.asarray(expected_normals, dtype="<f4")
    np.testing.assert_array_equal(workspace.data.original_normals, expected_raw)
    expected_unit = expected_raw.astype(np.float64)
    expected_unit /= np.linalg.norm(expected_unit, axis=1)[:, None]
    np.testing.assert_array_equal(workspace.data.normals, expected_unit)
    ids = np.flatnonzero(workspace.local[:, 2] == 1).tolist()
    result = fit_seed(
        workspace.local[ids],
        workspace.data.weights[ids],
        workspace.data.normals[ids],
        "plane",
        None,
        None,
    )
    assert result["weighted_rms"] == 0
    with pytest.raises(ValueError, match="no seed vertices"):
        _ = connected_growth(
            workspace.local,
            workspace.data.normals,
            workspace.data.triangles,
            workspace.data.weights,
            ids,
            [],
            result,
            0.01,
            45,
        )
    grown = connected_growth(
        workspace.local,
        workspace.data.normals,
        workspace.data.triangles,
        workspace.data.weights,
        ids,
        [],
        result,
        0.01,
        90,
    )
    assert grown["ids"] == ids


def test_changed_same_length_source_between_count_and_decode_is_not_accepted() -> None:
    raw = stl_bytes([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])

    class ChangingInput(SequentialInput):
        raw: bytes

        @contextmanager
        @override
        def open(self) -> Generator[BinaryIO]:
            if self.opens == 1:
                changed = bytearray(self.raw)
                struct.pack_into("<f", changed, 84, 1.0)
                self.raw = bytes(changed)
            with super().open() as stream:
                yield stream

    authority = ChangingInput(raw)
    with pytest.raises(ScanImportError, match="hash changed") as error:
        _ = prepare_scan_input(authority, LIMITS)
    assert error.value.code == "invalid_source"
    assert authority.opens == authority.closes == 2


def test_failed_stl_replacement_preserves_previous_project_and_source() -> None:
    project = ScanProject()
    original = project.commit(
        project.prepare_add(
            BytesInput(native_grid_fixture(5, 5).raw, "original.ply"),
            "original.ply",
            LIMITS,
            generation=0,
            token=str(project.accepted.graph.snapshot()["token"]),
        )
    )
    before = original.graph.snapshot()
    invalid = stl_bytes([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2), (0, 2, 1)])
    with pytest.raises(ScanImportError) as error:
        _ = project.prepare_replace(
            BytesInput(invalid, "cancelled.stl"),
            "cancelled.stl",
            LIMITS,
            generation=original.generation,
            token=str(before["token"]),
        )
    assert error.value.code == "invalid_stl_normals"
    assert project.accepted is original and project.accepted.graph.snapshot() == before


@pytest.mark.parametrize("scale", [2.0**-80, 2.0**80])
def test_finite_binary32_scale_extremes_keep_positive_area_and_unit_normals(
    scale: float,
) -> None:
    raw = stl_bytes([(0, 0, 0), (scale, 0, 0), (0, scale, 0)], [(0, 1, 2)])
    workspace = prepare_scan_input(BytesInput(raw, "scaled.stl"), LIMITS)
    assert np.isfinite(workspace.data.weights).all() and np.all(
        workspace.data.weights > 0
    )
    np.testing.assert_array_equal(workspace.data.normals, [[0, 0, 1]] * 3)
    assert workspace.data.weights.sum() == pytest.approx(
        scale * scale / 2, rel=1e-15, abs=0
    )
