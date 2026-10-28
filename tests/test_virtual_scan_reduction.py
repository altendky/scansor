"""Independent generated geometry checks for bounded preliminary mesh sources."""

from __future__ import annotations

import hashlib
import io
import math
import struct
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.scan_sources import BytesInput
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    VerifiedSource,
    prepare_scan,
)
from experiments.virtual_scan_reduction import (
    ReductionSettings,
    VirtualScanDerivation,
    prepare_virtual_scan,
)
from tests.scan_source_fixture import read_source_bytes

Point = tuple[float, float, float]
Face = tuple[int, int, int]


def _bytes(
    positions: list[Point], faces: list[Face], normals: Sequence[Point] | None = None
) -> bytes:
    if normals is None:
        normals = [(0, 0, 2)] * len(positions)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(positions)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\n"
        "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    return (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(positions, normals, strict=True)
        )
        + b"".join(struct.pack("<Biii", 3, *face) for face in faces)
    )


def _grid(size: int = 17, *, z: float = 0) -> tuple[list[Point], list[Face]]:
    positions = [(x / 16, y / 16, z) for y in range(size) for x in range(size)]
    faces: list[Face] = []
    for y in range(size - 1):
        for x in range(size - 1):
            a = y * size + x
            faces.extend(((a, a + 1, a + size), (a + 1, a + size + 1, a + size)))
    return positions, faces


@pytest.fixture
def limits() -> ScanLimits:
    return ScanLimits(10_000_000, 100_000, 200_000, 200_000_000)


def _source(
    limits: ScanLimits,
    positions: list[Point] | None = None,
    faces: list[Face] | None = None,
    normals: Sequence[Point] | None = None,
) -> ScanWorkspace:
    if positions is None:
        positions, faces = _grid()
    assert faces is not None
    return prepare_scan(
        io.BytesIO(_bytes(positions, faces, normals)), "original.ply", limits
    )


def _independent_weights(workspace: ScanWorkspace) -> list[float]:
    result = [0.0] * len(workspace.data.xyz)
    for raw in workspace.data.triangles:
        a, b, c = [tuple(float(x) for x in workspace.data.xyz[index]) for index in raw]
        u = tuple(b[i] - a[i] for i in range(3))
        v = tuple(c[i] - a[i] for i in range(3))
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        third = math.sqrt(sum(value * value for value in cross)) / 6
        for index in raw:
            result[index] += third
    return result


def test_planar_reduction_preserves_frame_observations_and_derived_area(
    limits: ScanLimits,
) -> None:
    original = _source(limits)
    result = prepare_virtual_scan(original, ReductionSettings(divisions=4), limits)
    derived = result.workspace
    assert len(derived.data.xyz) == 25
    assert len(derived.data.triangles) == 32
    assert result.diagnostics.merged_vertices == 264
    assert result.diagnostics.refinement_passes == 0
    assert result.diagnostics.estimated_bytes <= limits.max_estimated_bytes
    np.testing.assert_array_equal(derived.origin, original.origin)
    np.testing.assert_array_equal(derived.frame, original.frame)
    np.testing.assert_array_equal(derived.data.xyz.min(axis=0), [0, 0, 0])
    np.testing.assert_array_equal(derived.data.xyz.max(axis=0), [1, 1, 0])
    assert all(
        tuple(row) in {tuple(point) for point in original.data.xyz}
        for row in derived.data.xyz
    )
    np.testing.assert_array_equal(derived.data.original_normals, [[0, 0, 2]] * 25)
    np.testing.assert_array_equal(derived.data.normals, [[0, 0, 1]] * 25)
    np.testing.assert_allclose(
        derived.data.weights, _independent_weights(derived), rtol=0, atol=1e-16
    )
    assert sum(derived.data.weights) == pytest.approx(1)
    assert np.all(derived.data.face_status == 0)
    assert np.all(derived.data.weights > 0)
    assert not derived.data.xyz.flags.writeable
    assert original.descriptor is not None and derived.descriptor is not None
    assert original.descriptor.binding_sha256 != derived.descriptor.binding_sha256
    assert (
        hashlib.sha256(read_source_bytes(derived)).hexdigest()
        == derived.descriptor.source_sha256
    )
    with pytest.raises(FrozenInstanceError):
        cast(Any, result).workspace = original


def test_repeatability_chunk_size_independence_and_lineage_replay(
    limits: ScanLimits,
) -> None:
    original = _source(limits)
    settings = ReductionSettings(divisions=4)
    first = prepare_virtual_scan(original, settings, limits)
    second = prepare_virtual_scan(original, settings, replace(limits, chunk_bytes=240))
    assert read_source_bytes(first.workspace) == read_source_bytes(second.workspace)
    assert first.derivation == VirtualScanDerivation.model_validate_json(
        first.derivation.model_dump_json()
    )
    assert first.derivation.derived == second.derivation.derived
    reopened = prepare_scan(
        io.BytesIO(read_source_bytes(first.workspace)), "renamed.ply", limits
    )
    assert reopened.descriptor is not None
    assert reopened.descriptor.binding_sha256 == first.derivation.derived.binding_sha256
    np.testing.assert_array_equal(reopened.data.weights, first.workspace.data.weights)
    changed = prepare_virtual_scan(original, ReductionSettings(divisions=8), limits)
    assert (
        changed.derivation.derived.binding_sha256
        != first.derivation.derived.binding_sha256
    )


@pytest.mark.parametrize("opposing", [False, True])
def test_close_disconnected_sheets_never_weld(
    limits: ScanLimits, opposing: bool
) -> None:
    positions, faces = _grid()
    top, top_faces = _grid(z=1 / 64)
    offset = len(positions)
    normals: list[Point] = [(0.0, 0.0, 2.0)] * offset + [
        (0.0, 0.0, -2.0 if opposing else 2.0)
    ] * offset
    original = _source(
        limits,
        positions + top,
        faces + [(a + offset, b + offset, c + offset) for a, b, c in top_faces],
        normals,
    )
    result = prepare_virtual_scan(original, ReductionSettings(divisions=4), limits)
    derived = result.workspace
    assert len(derived.data.xyz) == 50
    for face in derived.data.triangles:
        assert len(set(derived.data.xyz[face, 2])) == 1
    assert set(derived.data.xyz[:, 2]) == {0, 1 / 64}
    assert sum(derived.data.weights) == pytest.approx(2)


def test_cleanup_drops_duplicates_collapsed_faces_and_original_orphans(
    limits: ScanLimits,
) -> None:
    positions, faces = _grid()
    positions.append((0.5, 0.5, 0))
    faces += [*faces.copy(), (0, 0, 1), (0, 1, 2)]
    original = _source(limits, positions, faces)
    result = prepare_virtual_scan(original, ReductionSettings(divisions=4), limits)
    assert result.diagnostics.usable_original_triangles == 1024
    assert result.diagnostics.collapsed_triangles > 0
    assert result.diagnostics.duplicate_triangles == 32
    assert result.diagnostics.unreferenced_representatives == 1
    assert len(result.workspace.data.xyz) == 25
    assert len(result.workspace.data.triangles) == 32


@pytest.mark.parametrize("value", [True, 4.0, "4", 1, 4097])
def test_settings_are_strict_bounded_and_frozen(value: object) -> None:
    with pytest.raises(ValidationError):
        _ = ReductionSettings.model_validate({"divisions": value})


def test_settings_and_lineage_reject_extras_wrong_versions_and_same_source(
    limits: ScanLimits,
) -> None:
    settings = ReductionSettings(divisions=4)
    with pytest.raises(ValidationError):
        cast(Any, settings).divisions = 8
    with pytest.raises(ValidationError):
        _ = ReductionSettings.model_validate({"divisions": 4, "extra": True})
    with pytest.raises(ValidationError):
        _ = ReductionSettings.model_validate({"divisions": 4, "version": "other"})
    original = _source(limits)
    assert original.descriptor is not None
    with pytest.raises(ValidationError, match="different source binding"):
        _ = VirtualScanDerivation(
            original=original.descriptor, derived=original.descriptor, settings=settings
        )
    result = prepare_virtual_scan(original, settings, limits)
    payload = result.derivation.model_dump()
    payload["original"]["interpretation_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="supported scan interpretation"):
        _ = VirtualScanDerivation.model_validate(payload)


def test_missing_source_no_usable_mesh_noop_and_aggressive_reduction(
    limits: ScanLimits,
) -> None:
    settings = ReductionSettings(divisions=4)
    with pytest.raises(ScanImportError, match="imported original"):
        _ = prepare_virtual_scan(ScanWorkspace.empty(), settings, limits)
    with pytest.raises(ScanImportError, match="usable source triangles"):
        _ = prepare_virtual_scan(
            _source(limits, [(0, 0, 0), (1, 0, 0)], []), settings, limits
        )
    with pytest.raises(ScanImportError) as noop:
        _ = prepare_virtual_scan(
            _source(limits), ReductionSettings(divisions=4096), limits
        )
    assert noop.value.code == "no_reduction"
    tiny = _source(
        limits, [(0, 0, 0), (0.125, 0, 0), (0, 0.125, 0), (1, 1, 1)], [(0, 1, 2)]
    )
    with pytest.raises(ScanImportError) as empty:
        _ = prepare_virtual_scan(tiny, ReductionSettings(divisions=2), limits)
    assert empty.value.code == "empty_reduction"


def test_original_bytes_arrays_descriptor_and_frame_must_match(
    limits: ScanLimits,
) -> None:
    original = _source(limits)
    settings = ReductionSettings(divisions=4)
    assert original.descriptor is not None
    invalid = [
        replace(
            original,
            source=VerifiedSource(
                BytesInput(read_source_bytes(original) + b"x", "changed.ply"),
                original.descriptor,
            ),
        ),
        replace(
            original, descriptor=original.descriptor.model_copy(update={"vertices": 12})
        ),
        replace(original, data=replace(original.data, xyz=original.data.xyz + 1)),
        replace(original, frame=np.zeros((3, 3))),
    ]
    for value in invalid:
        with pytest.raises(ScanImportError):
            _ = prepare_virtual_scan(value, settings, limits)
    bypassed = settings.model_copy(update={"divisions": 0})
    with pytest.raises(ValidationError):
        _ = prepare_virtual_scan(original, bypassed, limits)


def test_resources_and_cancellation_leave_original_unchanged(
    limits: ScanLimits,
) -> None:
    original = _source(limits)
    before = read_source_bytes(original)
    settings = ReductionSettings(divisions=4)
    with pytest.raises(ScanImportError) as limited:
        _ = prepare_virtual_scan(
            original, settings, replace(limits, max_estimated_bytes=1)
        )
    assert limited.value.code == "source_limit"
    with pytest.raises(ScanCancelled):
        _ = prepare_virtual_scan(original, settings, limits, cancelled=lambda: True)
    phases: list[str] = []
    stopped = False

    def progress(record: ScanProgress) -> None:
        nonlocal stopped
        phases.append(record.phase)
        if record.phase == "remap-faces":
            stopped = True

    with pytest.raises(ScanCancelled):
        _ = prepare_virtual_scan(
            original, settings, limits, progress=progress, cancelled=lambda: stopped
        )
    assert "remap-faces" in phases
    assert read_source_bytes(original) == before
    np.testing.assert_array_equal(original.data.xyz, _source(limits).data.xyz)


def test_inverted_candidate_is_refined_using_original_vertices(
    limits: ScanLimits,
) -> None:
    positions, faces = _grid()
    offset = len(positions)
    positions += [
        (0.4, 0.4, 0),
        (0.6, 0.1, 0),
        (0.1, 0.6, 0),
        (0.4, 0.4, 0),
        (0.6, 0.45, 0),
        (0.45, 0.6, 0),
    ]
    faces += [
        (a + offset, b + offset, c + offset)
        for a, b, c in [(0, 3, 1), (1, 4, 2), (2, 5, 0), (3, 4, 5)]
    ]
    original = _source(limits, positions, faces)
    result = prepare_virtual_scan(original, ReductionSettings(divisions=2), limits)
    assert result.diagnostics.refinement_passes > 0
    assert result.diagnostics.split_vertices > 0
    assert result.diagnostics.merged_vertices > 0
    # The troublesome original triangle is preserved with its original winding.
    expected = original.data.xyz[np.array([offset + 3, offset + 4, offset + 5])]
    assert any(
        np.array_equal(result.workspace.data.xyz[face], expected)
        for face in result.workspace.data.triangles
    )


@pytest.mark.parametrize("divisions", [16, 32, 64])
def test_retained_nozzle_has_useful_reduction_after_orientation_refinement(
    limits: ScanLimits, divisions: int
) -> None:
    path = Path("examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply")
    original = prepare_scan(io.BytesIO(path.read_bytes()), path.name, limits)
    result = prepare_virtual_scan(
        original, ReductionSettings(divisions=divisions), limits
    )
    assert result.diagnostics.refinement_passes > 0
    assert len(result.workspace.data.xyz) < 0.9 * len(original.data.xyz)
    assert len(result.workspace.data.triangles) < len(original.data.triangles)
    assert np.all(result.workspace.data.face_status == 0)
    assert np.all(result.workspace.data.weights > 0)


def _covers_xy(workspace: ScanWorkspace, point: tuple[float, float]) -> bool:
    for face in workspace.data.triangles:
        corners = workspace.data.xyz[face, :2]
        signs: list[float] = []
        for index in range(3):
            a, b = corners[index], corners[(index + 1) % 3]
            signs.append(
                float(
                    (b[0] - a[0]) * (point[1] - a[1])
                    - (b[1] - a[1]) * (point[0] - a[0])
                )
            )
        if all(value >= 0 for value in signs) or all(value <= 0 for value in signs):
            return True
    return False


def test_small_hole_can_disappear_at_coarse_detail_and_survive_finer_detail(
    limits: ScanLimits,
) -> None:
    positions, faces = _grid(33)
    faces = [
        face
        for face in faces
        if not all(
            0.875 <= positions[i][0] <= 1.125 and 0.875 <= positions[i][1] <= 1.125
            for i in face
        )
    ]
    original = _source(limits, positions, faces)
    coarse = prepare_virtual_scan(
        original, ReductionSettings(divisions=4), limits
    ).workspace
    finer = prepare_virtual_scan(
        original, ReductionSettings(divisions=16), limits
    ).workspace
    assert not _covers_xy(original, (1, 1))
    assert _covers_xy(coarse, (1, 1))
    assert not _covers_xy(finer, (1, 1))


def test_thin_disconnected_patch_can_disappear_without_welding_to_other_surface(
    limits: ScanLimits,
) -> None:
    positions, faces = _grid()
    offset = len(positions)
    positions += [
        (0.45 + x / 400, 0.45 + y / 160, 0.001) for y in range(17) for x in range(5)
    ]
    for y in range(16):
        for x in range(4):
            a = offset + 5 * y + x
            faces.extend(((a, a + 1, a + 5), (a + 1, a + 6, a + 5)))
    original = _source(limits, positions, faces)
    coarse = prepare_virtual_scan(
        original, ReductionSettings(divisions=4), limits
    ).workspace
    finer = prepare_virtual_scan(
        original, ReductionSettings(divisions=128), limits
    ).workspace
    assert np.all(coarse.data.xyz[:, 2] == 0)
    assert np.any(finer.data.xyz[:, 2] > 0)
    for face in finer.data.triangles:
        assert len(set(finer.data.xyz[face, 2])) == 1


@pytest.mark.parametrize("scale", [2.0**-80, 1.0, 2.0**80])
def test_dyadic_scale_and_signed_zero_boundary_behavior(
    limits: ScanLimits, scale: float
) -> None:
    positions, faces = _grid()
    positions = [
        (x * scale if x else -0.0, y * scale if y else -0.0, -0.0)
        for x, y, _ in positions
    ]
    original = _source(limits, positions, faces, [(-0.0, 0, 2)] * len(positions))
    result = prepare_virtual_scan(original, ReductionSettings(divisions=4), limits)
    baseline = prepare_virtual_scan(
        _source(limits), ReductionSettings(divisions=4), limits
    )
    np.testing.assert_array_equal(
        result.workspace.data.triangles, baseline.workspace.data.triangles
    )
    np.testing.assert_array_equal(
        result.workspace.data.xyz / scale, baseline.workspace.data.xyz
    )
    np.testing.assert_array_equal(
        result.workspace.data.weights / (scale * scale), baseline.workspace.data.weights
    )
    assert np.signbit(result.workspace.data.original_normals[:, 0]).all()


def test_orientation_refinement_budget_fails_before_returning_candidate(
    limits: ScanLimits, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = Path("examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply")
    original = prepare_scan(io.BytesIO(path.read_bytes()), path.name, limits)
    monkeypatch.setattr("experiments.virtual_scan_reduction._MAX_REFINEMENT_PASSES", 0)
    with pytest.raises(ScanImportError) as exhausted:
        _ = prepare_virtual_scan(original, ReductionSettings(divisions=32), limits)
    assert exhausted.value.code == "inverted_reduction"
    assert read_source_bytes(original) == path.read_bytes()
