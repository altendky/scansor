from __future__ import annotations

import io
import json
import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from pydantic import ValidationError

import scansor.files as files_module
import scansor.mapping_runs as mapping_runs_module
import scansor.stepped_rotational as mapping_module
from scansor.errors import ScansorError
from scansor.files import hash_run_file, rename_no_replace
from scansor.mapping_models import (
    InputRevision,
    MappingRequest,
    MappingResult,
    MappingThresholds,
    ObservationRecord,
    RigidTransform,
    SyntheticFixtureProvenance,
)
from scansor.mapping_runs import create_mapping_run, verify_mapping_run
from scansor.models import InspectJobConfig
from scansor.ply import canonical_npy
from scansor.runs import inspect_source, publish_run
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from scansor.stepped_rotational import build_mapping
from scansor.synthetic_fixture import FIXTURE_FRAME, Variant, prepare_synthetic_fixture
from tests.conftest import write_ply
from tests.test_runs import settings


def fixture_points(*, asymmetric: bool = True) -> list[tuple[float, float, float]]:
    points: list[tuple[float, float, float]] = []
    for radius, lower, upper in (
        (0.012, 0.0, 0.020),
        (0.018, 0.020, 0.050),
        (0.014, 0.050, 0.080),
    ):
        z = (lower + upper) / 2.0
        points.extend(
            (radius * math.cos(angle), radius * math.sin(angle), z)
            for angle in (math.pi / 2, math.pi, 3 * math.pi / 2)
        )
    for station, radii in (
        (0.0, (0.004, 0.006, 0.008)),
        (0.020, (0.014, 0.015, 0.016)),
        (0.050, (0.015, 0.016, 0.017)),
        (0.080, (0.004, 0.007, 0.010)),
    ):
        points.extend((-radius, 0.0, station) for radius in radii)
    if asymmetric:
        points.extend(
            (0.016, y, z) for y, z in ((-0.004, 0.03), (0.0, 0.035), (0.004, 0.04))
        )
    return points


def canonical_bytes(
    points: list[tuple[float, float, float]],
    *,
    normals: bool = False,
    normal_value: float = 2.0,
) -> bytes:
    fields = [("x_m", "<f8"), ("y_m", "<f8"), ("z_m", "<f8")]
    if normals:
        fields.extend((name, "<f8") for name in ("nx", "ny", "nz"))
    array = np.zeros(len(points), dtype=np.dtype(fields))
    for index, name in enumerate(("x_m", "y_m", "z_m")):
        array[name] = [point[index] for point in points]
    if normals:
        array["nx"] = 0.0
        array["ny"] = 0.0
        array["nz"] = normal_value
    return canonical_npy(array)


def request_for(
    canonical: bytes,
    *,
    asymmetric: bool = True,
    held_out: tuple[int, ...] = (),
    thresholds: MappingThresholds | None = None,
    transform: RigidTransform | None = None,
) -> MappingRequest:
    array = np.load(io.BytesIO(canonical), allow_pickle=False)
    variant = "asymmetric-datum-flat" if asymmetric else "axisymmetric"
    return MappingRequest(
        held_out_row_indices=held_out,
        input_revision=InputRevision(
            canonical_row_count=len(array),
            canonical_sha256=sha256(canonical),
            inspection_report_sha256="1" * 64,
            inspection_run_id="2" * 64,
            observation_frame="synthetic-observation",
            synthetic_fixture=SyntheticFixtureProvenance(
                canonical_sha256=sha256(canonical),
                content_sha256="3" * 64,
                source_sha256="4" * 64,
                variant=variant,
            ),
        ),
        thresholds=thresholds or MappingThresholds(),
        transform=transform
        or RigidTransform(
            rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_m=(0.0, 0.0, 0.0),
        ),
        variant=variant,
    )


def accepted_fixture(*, normals: bool = False) -> tuple[MappingRequest, bytes]:
    points = fixture_points()
    points.append(points[0])
    canonical = canonical_bytes(points, normals=normals)
    return request_for(canonical, held_out=(len(points) - 1,)), canonical


def test_accepted_mapping_has_distinct_deterministic_records_and_no_factors() -> None:
    request, canonical = accepted_fixture()
    first = build_mapping(request, canonical)
    second = build_mapping(request, canonical)
    assert "fixture_observation_id" not in ObservationRecord.model_fields
    assert b"fixture_observation_id" not in canonical_json(first)
    assert first == second
    assert canonical_json(first) == canonical_json(second)
    assert first.disposition == "accepted"
    assert first.instantiated_factors is None
    assert first.active_factor_ids == ()
    assert first.fit_result is None
    assert first.cad_evidence is None
    assert first.future_physical_reference is None
    assert len(first.held_out_observations) == 1
    assert first.diagnostics.rank_value == first.diagnostics.rank_required == 7
    assert not first.diagnostics.missing_required_regions
    assert len(first.candidates) == len(first.memberships) == len(first.mappings)
    assert len({item.observation_id for item in first.observations}) == len(
        first.observations
    )
    assert all(item.role == "primary-geometric" for item in first.mappings)


def test_revision_one_observation_serialization_uses_pydantic_2_11_contract() -> None:
    canonical = canonical_bytes(fixture_points())
    result = build_mapping(request_for(canonical), canonical)
    observation = result.observations[0]
    assert set(observation.model_dump(mode="json")) == {
        "evaluation_state",
        "normal",
        "observation_id",
        "point_model_m",
        "role",
        "row_index",
    }
    assert b"fixture_observation_id" not in canonical_json(result)


def test_axisymmetric_variant_has_only_its_regions_and_rank() -> None:
    canonical = canonical_bytes(fixture_points(asymmetric=False))
    result = build_mapping(request_for(canonical, asymmetric=False), canonical)
    assert result.disposition == "accepted"
    assert result.diagnostics.rank_value == result.diagnostics.rank_required == 6
    assert (
        "plane.datum-flat" not in result.diagnostics.per_element_training_mapping_counts
    )


def test_normals_are_optional_untrusted_and_never_change_classification() -> None:
    request_without, without = accepted_fixture(normals=False)
    request_with, with_normals = accepted_fixture(normals=True)
    result_without = build_mapping(request_without, without)
    result_with = build_mapping(request_with, with_normals)
    assert [item.element_id for item in result_without.mappings] == [
        item.element_id for item in result_with.mappings
    ]
    assert result_without.diagnostics.normal_magnitude_bounds is None
    assert result_with.diagnostics.normal_magnitude_bounds == (2.0, 2.0)
    assert all(
        item.normal.trust == "untrusted-diagnostic-only"
        for item in result_with.observations
    )


@pytest.mark.parametrize("normal_value", [1e-300, 1e308])
def test_normal_diagnostics_use_overflow_safe_magnitudes(normal_value: float) -> None:
    points = fixture_points()
    canonical = canonical_bytes(points, normals=True, normal_value=normal_value)
    result = build_mapping(request_for(canonical), canonical)
    assert result.disposition == "accepted"
    assert result.diagnostics.normal_magnitude_bounds == (normal_value, normal_value)


def test_held_out_rows_have_no_training_derived_path() -> None:
    request, canonical = accepted_fixture()
    result = build_mapping(request, canonical)
    held = result.held_out_observations[0]
    assert held.evaluation_state == "post-fit-evaluation/not-evaluated"
    assert held.observation_id not in {
        item.observation_id for item in result.candidates
    }
    assert held.observation_id not in {
        item.observation_id for item in result.memberships
    }
    assert held.observation_id not in {item.observation_id for item in result.mappings}
    audit = result.diagnostics.held_out_leakage
    assert all(value == () for value in audit.model_dump().values())
    assert result.diagnostics.counts["training"] == len(fixture_points())


@pytest.mark.parametrize(
    ("point", "reason"),
    [
        ((0.012, 0.0, 0.0001), "transition"),
        ((0.013, 0.0, 0.010), "outlier"),
        ((1.0, 1.0, 1.0), "gap"),
    ],
)
def test_complete_exclusions_publish_as_rejected(
    point: tuple[float, float, float], reason: str
) -> None:
    points = [*fixture_points(), point]
    canonical = canonical_bytes(points)
    result = build_mapping(request_for(canonical), canonical)
    assert result.disposition == "rejected"
    assert result.exclusions[-1].reason == reason
    assert reason in result.diagnostics.rejection_reasons


def test_ambiguous_overlap_fails_closed_with_geometric_clearance() -> None:
    points = [*fixture_points(), (0.0121, 0.0, 0.0199)]
    canonical = canonical_bytes(points)
    thresholds = MappingThresholds(transition_guard_m=1e-6)
    result = build_mapping(request_for(canonical, thresholds=thresholds), canonical)
    ambiguous = result.exclusions[-1]
    assert ambiguous.reason == "ambiguous"
    assert len(ambiguous.candidate_ids) == 2
    candidates = [
        item for item in result.candidates if item.row_index == ambiguous.row_index
    ]
    assert candidates[0].geometric_clearance_m is not None
    assert (
        candidates[0].geometric_clearance_m < thresholds.minimum_geometric_clearance_m
    )


def test_any_transition_hit_overrides_other_surviving_support() -> None:
    points = [*fixture_points(), (0.0118, 0.0, 0.00005)]
    canonical = canonical_bytes(points)
    thresholds = MappingThresholds(transition_guard_m=0.0001)
    result = build_mapping(request_for(canonical, thresholds=thresholds), canonical)
    exclusion = result.exclusions[-1]
    assert exclusion.reason == "transition"
    surviving = [
        candidate
        for candidate in result.candidates
        if candidate.row_index == len(points) - 1
    ]
    assert [candidate.element_id for candidate in surviving] == ["plane.station-0"]
    assert exclusion.candidate_ids == (surviving[0].candidate_id,)
    assert surviving[0].observation_id not in {
        mapping.observation_id for mapping in result.mappings
    }


def test_missing_regions_and_degeneracy_are_distinct_failures() -> None:
    canonical = canonical_bytes([(0.012, 0.0, z) for z in (0.005, 0.010, 0.015)])
    result = build_mapping(request_for(canonical), canonical)
    assert result.disposition == "rejected"
    assert "missing-required-regions" in result.diagnostics.rejection_reasons
    assert "rank-deficient" in result.diagnostics.rejection_reasons
    assert result.diagnostics.rank_value == 1


@pytest.mark.parametrize(
    "rotation",
    [
        ((2.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        ((-1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ],
)
def test_invalid_rigid_transforms_fail_before_mapping(
    rotation: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> None:
    request, canonical = accepted_fixture()
    invalid = request.model_copy(
        update={
            "transform": RigidTransform(
                rotation=rotation, translation_m=(0.0, 0.0, 0.0)
            )
        }
    )
    with pytest.raises(ScansorError, match="rotation"):
        _ = build_mapping(invalid, canonical)
    with pytest.raises(ValidationError, match="scale"):
        _ = RigidTransform(
            rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            scale=0.001,
            translation_m=(0.0, 0.0, 0.0),
        )


def test_nonfinite_and_revision_mismatch_publish_nothing(tmp_path: Path) -> None:
    points = fixture_points()
    canonical = canonical_bytes(points)
    request = request_for(canonical)
    array = np.load(io.BytesIO(canonical), allow_pickle=False).copy()
    array["x_m"][0] = np.nan
    nonfinite = canonical_npy(array)
    invalid_request = request_for(nonfinite)
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="finite"):
        _ = build_mapping(invalid_request, nonfinite)
    assert not output.exists()
    with pytest.raises(ScansorError, match="SHA-256"):
        _ = build_mapping(request, canonical + b"x")


def test_finite_transform_overflow_fails_closed() -> None:
    canonical = canonical_bytes([(1e308, 0.0, 0.0)])
    transform = RigidTransform(
        rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        translation_m=(1e308, 0.0, 0.0),
    )
    with (
        np.errstate(over="ignore"),
        pytest.raises(ScansorError, match="produced nonfinite"),
    ):
        _ = build_mapping(request_for(canonical, transform=transform), canonical)


def test_contract_and_canonical_serialization_round_trip() -> None:
    request, canonical = accepted_fixture()
    result = build_mapping(request, canonical)
    encoded = canonical_json(result)
    decoded = parse_canonical_json(encoded, "mapping", len(encoded))
    restored = MappingResult.model_validate(decoded)
    assert restored == result
    assert canonical_json(restored) == encoded
    with pytest.raises(ScansorError, match="canonical"):
        parse_canonical_json(
            json.dumps(decoded).encode("ascii"), "mapping", len(encoded) * 2
        )
    bad = request.model_dump()
    bad["held_out_row_indices"] = [2, 1]
    with pytest.raises(ValidationError, match="sorted"):
        _ = MappingRequest.model_validate(bad)


def inspection_mapping_fixture(
    tmp_path: Path,
    variant: Variant = "asymmetric-datum-flat",
) -> tuple[Path, MappingResult]:
    fixture = prepare_synthetic_fixture(variant)
    source = tmp_path / "synthetic.ply"
    _ = source.write_bytes(fixture.source)
    inspection_run = tmp_path / "inspection"
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=source,
            output_path=inspection_run,
            unit="m",
            frame=FIXTURE_FRAME,
        ),
        settings(),
    )
    publish_run(inspection_run, report, canonical)
    request = MappingRequest(
        held_out_row_indices=fixture.held_out_row_indices,
        input_revision=InputRevision(
            canonical_row_count=report.inspection.point_count,
            canonical_sha256=report.canonical.sha256,
            inspection_report_sha256=sha256(canonical_json(report)),
            inspection_run_id=report.run_id,
            observation_frame=report.source.frame,
            synthetic_fixture=fixture.provenance,
        ),
        transform=RigidTransform(
            rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_m=(0.0, 0.0, 0.0),
        ),
        variant=variant,
    )
    return inspection_run, build_mapping(request, canonical)


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_mapping_publication_replay_and_external_reference(
    tmp_path: Path, variant: Variant
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path, variant)
    mapping_run = tmp_path / "mapping"
    assert create_mapping_run(mapping_run, inspection_run, result.request) == result
    assert {item.name for item in mapping_run.iterdir()} == {
        "mapping.json",
        "manifest.json",
        "manifest.sha256",
    }
    assert {item.name for item in inspection_run.iterdir()} == {
        "canonical.npy",
        "report.json",
        "manifest.json",
        "manifest.sha256",
    }
    mapping_bytes = (mapping_run / "mapping.json").read_bytes()
    assert b"canonical.npy" in mapping_bytes
    assert (inspection_run / "canonical.npy").read_bytes() not in mapping_bytes
    before = {item.name: item.read_bytes() for item in inspection_run.iterdir()}
    assert verify_mapping_run(mapping_run, inspection_run) == result
    assert {item.name: item.read_bytes() for item in inspection_run.iterdir()} == before
    with pytest.raises(ScansorError, match="already exists"):
        _ = create_mapping_run(mapping_run, inspection_run, result.request)


def test_mapping_replay_rejects_corruption_and_wrong_inspection(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    mapping_run = tmp_path / "mapping"
    _ = create_mapping_run(mapping_run, inspection_run, result.request)
    _ = (mapping_run / "manifest.sha256").write_bytes(b"0" * 64)
    with pytest.raises(ScansorError, match="sidecar"):
        _ = verify_mapping_run(mapping_run, inspection_run)


def test_mapping_publication_rejects_revision_before_output(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    wrong_revision = result.request.input_revision.model_copy(
        update={"inspection_report_sha256": "0" * 64}
    )
    request = result.request.model_copy(update={"input_revision": wrong_revision})
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="input revision"):
        _ = create_mapping_run(output, inspection_run, request)
    assert not output.exists()


def test_mapping_publication_recomputes_synthetic_provenance(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    wrong_provenance = result.request.input_revision.synthetic_fixture.model_copy(
        update={"content_sha256": "0" * 64}
    )
    wrong_revision = result.request.input_revision.model_copy(
        update={"synthetic_fixture": wrong_provenance}
    )
    request = result.request.model_copy(update={"input_revision": wrong_revision})
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="designated synthetic fixture"):
        _ = create_mapping_run(output, inspection_run, request)
    assert not output.exists()


def test_geometrically_compatible_arbitrary_inspection_cannot_publish(
    tmp_path: Path,
) -> None:
    points = fixture_points()
    points.append(points[0])
    source = write_ply(tmp_path / "arbitrary.ply", rows=points, scalar="double")
    inspection_run = tmp_path / "arbitrary-inspection"
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=source,
            output_path=inspection_run,
            unit="m",
            frame=FIXTURE_FRAME,
        ),
        settings(),
    )
    publish_run(inspection_run, report, canonical)
    request = request_for(canonical, held_out=(len(points) - 1,)).model_copy(
        update={
            "input_revision": InputRevision(
                canonical_row_count=report.inspection.point_count,
                canonical_sha256=report.canonical.sha256,
                inspection_report_sha256=sha256(canonical_json(report)),
                inspection_run_id=report.run_id,
                observation_frame=report.source.frame,
                synthetic_fixture=SyntheticFixtureProvenance(
                    canonical_sha256=report.canonical.sha256,
                    content_sha256="5" * 64,
                    source_sha256=report.source.sha256,
                    variant="asymmetric-datum-flat",
                ),
            )
        }
    )
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="designated synthetic fixture"):
        _ = create_mapping_run(output, inspection_run, request)
    assert not output.exists()


def test_mapping_publication_requires_full_inspection_replay(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    (tmp_path / "synthetic.ply").unlink()
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="replay PLY input"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()


def test_mapping_output_cannot_modify_inspection_inventory(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    before = {item.name: item.read_bytes() for item in inspection_run.iterdir()}
    output = inspection_run / "mapping"
    with pytest.raises(ScansorError, match="within its inspection tree"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()
    assert {item.name: item.read_bytes() for item in inspection_run.iterdir()} == before


def test_mapping_output_rejects_existing_inspection_descendant(tmp_path: Path) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    nested = inspection_run / "nested"
    nested.mkdir()
    output = nested / "mapping"
    with pytest.raises(ScansorError, match="within its inspection tree"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()


def test_mapping_row_bound_fails_before_canonical_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, canonical = accepted_fixture()
    oversized_revision = request.input_revision.model_copy(
        update={"canonical_row_count": 20_001}
    )
    oversized = request.model_copy(update={"input_revision": oversized_revision})
    loaded = False

    def forbidden_load(_data: bytes) -> None:
        nonlocal loaded
        loaded = True
        raise AssertionError("canonical loading must not occur")

    monkeypatch.setattr(mapping_module, "load_canonical_npy", forbidden_load)
    with pytest.raises(ScansorError, match="20,000-row"):
        _ = build_mapping(oversized, canonical)
    assert not loaded


def test_mapping_publication_enforces_replay_byte_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    monkeypatch.setattr(mapping_runs_module, "MAX_MAPPING_BYTES", 1)
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match="byte limit"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()


def test_mapping_publication_detects_output_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    output = tmp_path / "mapping"
    moved = tmp_path / "moved-mapping"
    original = rename_no_replace

    def replace_output(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        original(source_directory_fd, source_name, target_directory_fd, target_name)
        _ = output.rename(moved)
        output.mkdir()
        _ = (output / "marker").write_text("preserve", encoding="ascii")

    monkeypatch.setattr(mapping_runs_module, "rename_no_replace", replace_output)
    with pytest.raises(ScansorError, match="output path changed"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert (output / "marker").read_text(encoding="ascii") == "preserve"


def test_mapping_publication_rechecks_entries_after_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    output = tmp_path / "mapping"
    original = hash_run_file

    def replace_after_hash(directory_fd: int, name: str, max_bytes: int):
        hashed = original(directory_fd, name, max_bytes)
        if name == "manifest.sha256":
            os.unlink("mapping.json", dir_fd=directory_fd)
            descriptor = os.open(
                "mapping.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=directory_fd,
            )
            os.close(descriptor)
        return hashed

    monkeypatch.setattr(mapping_runs_module, "hash_run_file", replace_after_hash)
    with pytest.raises(ScansorError, match="artifact entry changed"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()
    stages = list(tmp_path.glob(".mapping.scansor-mapping-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "mapping.json").is_file()


def test_unpublished_unchanged_stage_is_cleaned_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    before = {item.name for item in tmp_path.iterdir()}

    def fail_before_publish(
        _source_directory_fd: int,
        _source_name: str,
        _target_directory_fd: int,
        _target_name: str,
    ) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(mapping_runs_module, "rename_no_replace", fail_before_publish)
    with pytest.raises(ScansorError, match="injected rename failure"):
        _ = create_mapping_run(tmp_path / "mapping", inspection_run, result.request)
    assert {item.name for item in tmp_path.iterdir()} == before


def test_unpublished_cleanup_quarantines_before_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    quarantines: list[str] = []
    original_cleanup_rename = rename_no_replace

    def fail_before_publish(
        _source_directory_fd: int,
        _source_name: str,
        _target_directory_fd: int,
        _target_name: str,
    ) -> None:
        raise OSError("injected rename failure")

    def observe_quarantine(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        quarantines.append(target_name)
        original_cleanup_rename(
            source_directory_fd,
            source_name,
            target_directory_fd,
            target_name,
        )

    monkeypatch.setattr(mapping_runs_module, "rename_no_replace", fail_before_publish)
    monkeypatch.setattr(files_module, "rename_no_replace", observe_quarantine)
    with pytest.raises(ScansorError, match="injected rename failure"):
        _ = create_mapping_run(tmp_path / "mapping", inspection_run, result.request)
    assert len(quarantines) == 1
    assert ".quarantine-" in quarantines[0]
    assert not (tmp_path / quarantines[0]).exists()


def test_populated_stage_is_restored_and_preserved_after_quarantine_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    original_cleanup_rename = rename_no_replace
    populated = False

    def fail_before_publish(
        _source_directory_fd: int,
        _source_name: str,
        _target_directory_fd: int,
        _target_name: str,
    ) -> None:
        raise OSError("injected rename failure")

    def populate_before_quarantine(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        nonlocal populated
        if ".quarantine-" in target_name and not populated:
            stage_fd = os.open(
                source_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=source_directory_fd,
            )
            try:
                descriptor = os.open(
                    "preserve",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=stage_fd,
                )
                os.close(descriptor)
            finally:
                os.close(stage_fd)
            populated = True
        original_cleanup_rename(
            source_directory_fd,
            source_name,
            target_directory_fd,
            target_name,
        )

    monkeypatch.setattr(mapping_runs_module, "rename_no_replace", fail_before_publish)
    monkeypatch.setattr(
        files_module,
        "rename_no_replace",
        populate_before_quarantine,
    )
    with pytest.raises(ScansorError, match="injected rename failure"):
        _ = create_mapping_run(tmp_path / "mapping", inspection_run, result.request)
    stages = list(tmp_path.glob(".mapping.scansor-mapping-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "preserve").is_file()
    assert ".quarantine-" not in stages[0].name


def test_empty_stage_is_cleaned_when_stage_open_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    before = {item.name for item in tmp_path.iterdir()}
    original = os.open

    def fail_stage_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if (
            isinstance(path, str)
            and path.startswith(".mapping.scansor-mapping-stage-")
            and dir_fd is not None
        ):
            raise PermissionError("injected stage open failure")
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_stage_open)
    with pytest.raises(ScansorError, match="injected stage open failure"):
        _ = create_mapping_run(tmp_path / "mapping", inspection_run, result.request)
    assert {item.name for item in tmp_path.iterdir()} == before


def test_replaced_unopened_stage_and_original_are_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    original = os.open
    moved_stage = tmp_path / "moved-stage"
    replacements: list[Path] = []

    def replace_stage_before_open(
        path: str | Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if (
            isinstance(path, str)
            and path.startswith(".mapping.scansor-mapping-stage-")
            and dir_fd is not None
        ):
            stage = tmp_path / path
            _ = stage.rename(moved_stage)
            stage.mkdir()
            _ = (stage / "preserve").write_text("replacement", encoding="ascii")
            replacements.append(stage)
            raise PermissionError("injected replaced stage open failure")
        return original(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_stage_before_open)
    with pytest.raises(ScansorError, match="replaced stage open failure"):
        _ = create_mapping_run(tmp_path / "mapping", inspection_run, result.request)
    assert len(replacements) == 1
    assert (replacements[0] / "preserve").read_text(encoding="ascii") == "replacement"
    assert moved_stage.is_dir()


def test_output_parent_move_into_inspection_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    output = output_parent / "mapping"
    original = cast(
        Callable[
            [
                int,
                dict[str, bytes],
                dict[str, tuple[int, int, int, int, int, int]],
            ],
            None,
        ],
        vars(mapping_runs_module)["_verify_mapping_artifacts"],
    )
    moved = False

    def move_parent_after_staging(
        directory_fd: int,
        artifacts: dict[str, bytes],
        identities: dict[str, tuple[int, int, int, int, int, int]],
    ) -> None:
        nonlocal moved
        original(directory_fd, artifacts, identities)
        if not moved:
            _ = output_parent.rename(inspection_run / "moved-output-parent")
            moved = True

    monkeypatch.setattr(
        mapping_runs_module, "_verify_mapping_artifacts", move_parent_after_staging
    )
    with pytest.raises(ScansorError, match="output parent changed"):
        _ = create_mapping_run(output, inspection_run, result.request)
    assert not output.exists()


def test_output_parent_move_after_rename_rolls_back_exact_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    moved_parent = inspection_run / "moved-output-parent"
    original = rename_no_replace

    def move_parent_after_rename(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        original(source_directory_fd, source_name, target_directory_fd, target_name)
        _ = output_parent.rename(moved_parent)

    monkeypatch.setattr(
        mapping_runs_module, "rename_no_replace", move_parent_after_rename
    )
    with pytest.raises(ScansorError, match="output parent changed"):
        _ = create_mapping_run(
            output_parent / "mapping", inspection_run, result.request
        )
    assert moved_parent.is_dir()
    assert list(moved_parent.iterdir()) == []


def test_rollback_ignores_occupied_former_stage_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    moved_parent = inspection_run / "moved-output-parent"
    original = rename_no_replace
    occupied_stage = ""

    def occupy_stage_and_move_parent(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        nonlocal occupied_stage
        original(
            source_directory_fd,
            source_name,
            target_directory_fd,
            target_name,
        )
        occupied_stage = source_name
        os.mkdir(source_name, dir_fd=source_directory_fd)
        _ = output_parent.rename(moved_parent)

    monkeypatch.setattr(
        mapping_runs_module, "rename_no_replace", occupy_stage_and_move_parent
    )
    with pytest.raises(ScansorError, match="output parent changed"):
        _ = create_mapping_run(
            output_parent / "mapping", inspection_run, result.request
        )
    assert occupied_stage
    assert (moved_parent / occupied_stage).is_dir()
    assert {item.name for item in moved_parent.iterdir()} == {occupied_stage}


def test_mapping_verification_detects_root_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, result = inspection_mapping_fixture(tmp_path)
    mapping_run = tmp_path / "mapping"
    _ = create_mapping_run(mapping_run, inspection_run, result.request)
    moved = tmp_path / "moved-mapping"
    original = cast(
        Callable[[MappingRequest, Path, int], tuple[bytes, bytes]],
        vars(mapping_runs_module)["_validate_inspection_revision"],
    )

    def replace_root(
        request: MappingRequest, inspection_path: Path, inspection_fd: int
    ) -> tuple[bytes, bytes]:
        loaded = original(request, inspection_path, inspection_fd)
        _ = mapping_run.rename(moved)
        mapping_run.mkdir()
        return loaded

    monkeypatch.setattr(
        mapping_runs_module, "_validate_inspection_revision", replace_root
    )
    with pytest.raises(ScansorError, match="run path changed"):
        _ = verify_mapping_run(mapping_run, inspection_run)


def test_persisted_contract_rejects_held_out_leakage() -> None:
    request, canonical = accepted_fixture()
    result = build_mapping(request, canonical)
    record = result.model_dump(mode="json")
    record["mappings"][0]["observation_id"] = result.held_out_observations[
        0
    ].observation_id
    with pytest.raises(ValidationError, match="held-out observation leaked"):
        _ = MappingResult.model_validate(record)

    row_leak = result.model_dump(mode="json")
    row_leak["candidates"][0]["row_index"] = result.request.held_out_row_indices[0]
    with pytest.raises(ValidationError, match="row index"):
        _ = MappingResult.model_validate(row_leak)

    forged_rank = result.model_dump(mode="json")
    forged_rank["diagnostics"]["rank_value"] = 0
    with pytest.raises(ValidationError, match="rank diagnostics"):
        _ = MappingResult.model_validate(forged_rank)

    wrong_role = result.model_dump(mode="json")
    wrong_role["held_out_observations"][0]["evaluation_state"] = "training-mapped"
    with pytest.raises(ValidationError, match="role and evaluation state"):
        _ = MappingResult.model_validate(wrong_role)


def test_pure_mapping_module_has_no_experiment_cad_or_filesystem_imports() -> None:
    source = Path(mapping_module.__file__).read_text(encoding="ascii")
    for prohibited in ("experiments", "onshape", "scansor.mapping_runs", "pathlib"):
        assert prohibited not in source.lower()
