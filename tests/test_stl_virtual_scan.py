"""STL originals retain their exact authority while virtual derivatives are PLY."""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.feature_graph import Recipe
from experiments.scan_actions import preflight_actions
from experiments.scan_project import ScanProject, reconstruct_virtual_scan
from experiments.scan_sources import BytesInput, PathInput
from experiments.scan_workspace import (
    INTERPRETATION_SHA256,
    STL_PROFILE,
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    prepare_scan_input,
)
from experiments.virtual_scan_reduction import (
    ReductionSettings,
    VirtualScanDerivation,
    prepare_virtual_scan,
)
from tests.test_stl_scan_workspace import Face, Point, stl_bytes

LIMITS = ScanLimits(4_000_000, 20_000, 40_000, 100_000_000, chunk_bytes=240)
SETTINGS = ReductionSettings(divisions=4)


def grid_bytes(encoding: str = "binary", *, cancelled_normals: bool = False) -> bytes:
    vertices: list[Point] = [(x / 16, y / 16, 0) for y in range(17) for x in range(17)]
    faces: list[Face] = []
    for y in range(16):
        for x in range(16):
            a = 17 * y + x
            faces.extend(((a, a + 1, a + 17), (a + 1, a + 18, a + 17)))
    if cancelled_normals:
        vertices.extend(((2, 2, 0), (2.125, 2, 0), (2, 2.125, 0)))
        faces.extend(((289, 290, 291), (289, 291, 290)))
    return stl_bytes(vertices, faces, encoding=encoding, normal=(0, 0, 0))


def cursor(project: ScanProject) -> dict[str, Any]:
    return {
        "generation": project.accepted.generation,
        "token": project.accepted.graph.snapshot()["token"],
    }


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
def test_stl_to_ply_derivative_is_exactly_reconstructible_from_replayable_original(
    encoding: str,
) -> None:
    raw = grid_bytes(encoding)
    original = prepare_scan_input(BytesInput(raw, "original.stl"), LIMITS)
    reduced = prepare_virtual_scan(original, SETTINGS, LIMITS)
    assert original.descriptor is not None and reduced.workspace.descriptor is not None
    assert reduced.derivation.original.profile == STL_PROFILE
    assert reduced.derivation.derived.profile == "binary-triangle-ply-v1"
    assert reduced.derivation.derived.interpretation_sha256 == INTERPRETATION_SHA256
    assert len(reduced.workspace.local) == 25
    assert len(reduced.workspace.data.triangles) == 32
    assert reduced.workspace.source is not None
    assert not hasattr(original, "original_bytes")
    assert not isinstance(reduced.workspace.source.input, BytesInput)
    assert reduced.derivation == VirtualScanDerivation.model_validate_json(
        reduced.derivation.model_dump_json()
    )
    first, replayed = reconstruct_virtual_scan(original, reduced.derivation, LIMITS)
    assert first is original
    assert replayed.reference_sha256 == reduced.workspace.reference_sha256
    assert replayed.source is not None
    replayed.source.verify()
    np.testing.assert_array_equal(replayed.local, reduced.workspace.local)
    np.testing.assert_array_equal(replayed.data.weights, reduced.workspace.data.weights)


@pytest.mark.parametrize("active", ["original", "virtual"])
def test_saved_actions_reopen_either_endpoint_from_original_stl_with_renamed_file(
    active: str,
) -> None:
    raw = grid_bytes()
    project = ScanProject()
    original = project.commit(
        project.prepare_add(
            BytesInput(raw, "original.stl"), "original.stl", LIMITS, **cursor(project)
        )
    )
    accepted = project.commit_replace(
        project.prepare_virtual_scan(SETTINGS, LIMITS, **cursor(project))
    )
    if active == "original":
        accepted = project.commit_replace(
            project.prepare_activate_scan(
                original.workspace.reference_sha256, LIMITS, **cursor(project)
            )
        )
    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    assert preflight_actions(recipe).kind == "generic"
    reopened = ScanProject()
    candidate = reopened.prepare_open(
        recipe,
        confirmed_discard=True,
        stream=BytesInput(raw, "renamed.dat"),
        filename="even-named.ply",
        limits=LIMITS,
        **cursor(reopened),
    )
    result = reopened.commit_open(candidate)
    assert result.workspace.reference_sha256 == accepted.workspace.reference_sha256
    assert result.graph.snapshot()["recipe"] == accepted.graph.snapshot()["recipe"]
    assert len(result.retained_sources) == 2
    assert result.retained_sources[0].descriptor is not None
    assert result.retained_sources[0].descriptor.profile == STL_PROFILE
    np.testing.assert_array_equal(result.workspace.local, accepted.workspace.local)


def test_registry_rejects_cross_profile_interpretation_and_noncanonical_derivative() -> (
    None
):
    original = prepare_scan_input(BytesInput(grid_bytes(), "original.stl"), LIMITS)
    reduced = prepare_virtual_scan(original, SETTINGS, LIMITS)
    record = reduced.derivation.model_dump()
    record["original"]["interpretation_sha256"] = INTERPRETATION_SHA256
    with pytest.raises(ValidationError, match="supported scan interpretation"):
        _ = VirtualScanDerivation.model_validate(record)
    record = reduced.derivation.model_dump()
    record["derived"] = {**record["original"], "source_sha256": "0" * 64}
    with pytest.raises(ValidationError, match="canonical PLY"):
        _ = VirtualScanDerivation.model_validate(record)


def test_changed_source_and_cached_normal_tampering_fail_before_derivative_publication(
    tmp_path: Path,
) -> None:
    raw = grid_bytes()
    path = tmp_path / "original.stl"
    _ = path.write_bytes(raw)
    original = prepare_scan_input(PathInput(path), LIMITS)
    tampered = replace(
        original, data=replace(original.data, normals=-original.data.normals)
    )
    with pytest.raises(ScanImportError, match="normals disagrees"):
        _ = prepare_virtual_scan(tampered, SETTINGS, LIMITS)
    changed = bytearray(raw)
    struct.pack_into("<f", changed, 84, 1.0)
    _ = path.write_bytes(changed)
    assert path.read_bytes() != raw
    with pytest.raises(ScanImportError):
        _ = prepare_virtual_scan(original, SETTINGS, LIMITS)
    np.testing.assert_array_equal(
        original.data.normals, [[0, 0, 1]] * len(original.local)
    )


def test_cancelled_stl_reconstruction_and_wrong_saved_settings_leave_original_available() -> (
    None
):
    original = prepare_scan_input(BytesInput(grid_bytes(), "original.stl"), LIMITS)
    reduced = prepare_virtual_scan(original, SETTINGS, LIMITS)
    cancelled = False

    def progress(update: ScanProgress) -> None:
        nonlocal cancelled
        cancelled = update.phase == "verify-original"

    with pytest.raises(ScanCancelled):
        _ = prepare_virtual_scan(
            original, SETTINGS, LIMITS, progress=progress, cancelled=lambda: cancelled
        )
    wrong = reduced.derivation.model_copy(
        update={"settings": ReductionSettings(divisions=8)}
    )
    with pytest.raises(ScanImportError) as mismatch:
        _ = reconstruct_virtual_scan(original, wrong, LIMITS)
    assert mismatch.value.code == "source_mismatch"
    assert original.source is not None
    original.source.verify()
