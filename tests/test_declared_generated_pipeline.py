from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

import scansor.declared_generation as generation_module
import scansor.mapping_runs as mapping_runs_module
from scansor.declared_execution_models import ExecutionResult
from scansor.declared_execution_run_models import ExecutionRunRecords
from scansor.declared_execution_runs import create_execution_run, verify_execution_run
from scansor.declared_factor_models import ParameterVector
from scansor.declared_factors import (
    evaluate_factors,
    instantiate_factors,
    select_active_factors,
)
from scansor.declared_generation import (
    create_generation_request,
    generated_fixture_provenance,
)
from scansor.declared_generation_models import PreparedGeneration
from scansor.declared_generation_runs import (
    create_generated_mapping_run,
    create_generation_run,
    verify_generation_run,
)
from scansor.declared_numpy_backend import DeclaredNumpyBackend
from scansor.declared_truth_comparison import compare_truth
from scansor.errors import ScansorError
from scansor.mapping_models import InputRevision, MappingRequest, MappingResult
from scansor.mapping_runs import verify_mapping_run
from scansor.models import InspectJobConfig
from scansor.runs import inspect_source, publish_run
from scansor.serialization import canonical_json, sha256
from tests.test_runs import settings

FIXTURES = ("asymmetric-stepped-v1", "coaxial-tube-v1")


def published_mapping(
    root: Path, fixture_id: str
) -> tuple[PreparedGeneration, MappingResult]:
    root.mkdir(parents=True, exist_ok=True)
    generation = root / "generation"
    generated = create_generation_run(
        generation, create_generation_request(fixture_id, seed=7, noise_sigma_m=20e-6)
    )
    assert verify_generation_run(generation) == generated
    inspection = root / "inspection"
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=generation / "observations.ply",
            output_path=inspection,
            unit="m",
            frame=generated.provenance.source.frame,
        ),
        settings(),
    )
    publish_run(inspection, report, canonical)
    request = MappingRequest(
        declaration=generated.provenance.request.declaration,
        held_out_row_indices=generated.provenance.held_out_row_indices,
        input_revision=InputRevision(
            canonical_row_count=report.inspection.point_count,
            canonical_sha256=report.canonical.sha256,
            inspection_report_sha256=sha256(canonical_json(report)),
            inspection_run_id=report.run_id,
            observation_frame=report.source.frame,
            synthetic_fixture=generated_fixture_provenance(
                generated, report.canonical.sha256
            ),
        ),
        transform=generated.provenance.request.transform,
    )
    mapping = create_generated_mapping_run(
        root / "mapping", generation, inspection, request
    )
    assert mapping.disposition == "accepted"
    return generated, mapping


def published_execution(root: Path, mapping: MappingResult) -> ExecutionRunRecords:
    factors = instantiate_factors(mapping)
    parameters = ParameterVector(
        model_id=mapping.model_id,
        values=tuple(
            parameter.nominal
            + parameter.diagnostic_scale * (0.1 if index % 2 == 0 else -0.1)
            for index, parameter in enumerate(factors.declaration.parameters)
        ),
    )
    return create_execution_run(
        root / "execution",
        root / "inspection",
        root / "mapping",
        tuple(factor.factor_id for factor in factors.factors),
        parameters,
    )


def snapshot(root: Path) -> dict[str, tuple[int, bytes]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


def assert_completed(mapping: MappingResult, records: ExecutionRunRecords) -> None:
    assert records.result.disposition == "completed-not-assessed"
    assert records.result.final_parameters is not None
    assert records.result.final_evaluation is not None
    assert records.held_out is not None
    assert records.manifest is not None
    assert (
        records.result.model_id
        == records.held_out.model_id
        == records.manifest.model_id
        == mapping.model_id
    )
    factors = instantiate_factors(mapping)
    selection = select_active_factors(
        factors, tuple(factor.factor_id for factor in factors.factors)
    )
    nominal = tuple(parameter.nominal for parameter in factors.declaration.parameters)
    evaluated = evaluate_factors(
        factors, selection, ParameterVector(model_id=mapping.model_id, values=nominal)
    )
    # These fixture supports are affine in their varied shape parameters. Check
    # the optimizer against an independent unscaled linear least-squares solve.
    increment = np.linalg.lstsq(
        np.asarray(evaluated.jacobian),
        -np.asarray(evaluated.raw_residuals_m),
        rcond=1e-10,
    )[0]
    assert records.result.final_parameters.values == pytest.approx(
        np.asarray(nominal) + increment, abs=1e-12
    )
    assert records.held_out.summary.count == len(mapping.held_out_observations)
    held_out = set(mapping.request.held_out_row_indices)
    assert held_out.isdisjoint(item.row_index for item in factors.declarations)
    assert held_out.isdisjoint(item.row_index for item in mapping.candidates)
    assert records.result.request.parameter_order == tuple(
        parameter.parameter_id for parameter in factors.declaration.parameters
    )


@pytest.mark.parametrize("fixture_id", FIXTURES)
def test_complete_generated_declared_workflow_read_only_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_id: str
) -> None:
    generated, mapping = published_mapping(tmp_path, fixture_id)
    records = published_execution(tmp_path, mapping)
    assert_completed(mapping, records)
    expected_ids = {
        row.row_index: row.expected_element_id for row in generated.provenance.rows
    }
    rows = {item.observation_id: item.row_index for item in mapping.observations}
    assert all(
        item.element_id == expected_ids[rows[item.observation_id]]
        for item in mapping.mappings
    )
    assert records.held_out is not None
    assert all(
        row.assigned_element_id == expected_ids[row.row_index]
        for row in records.held_out.rows
    )
    assert (
        mapping.diagnostics.rank_value
        == mapping.diagnostics.rank_required
        == len(generated.provenance.request.parameter_order)
    )
    before = snapshot(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("read-only verification invoked the optimizer")

    monkeypatch.setattr(DeclaredNumpyBackend, "execute", forbidden)
    assert verify_mapping_run(tmp_path / "mapping", tmp_path / "inspection") == mapping
    assert (
        verify_execution_run(
            tmp_path / "execution", tmp_path / "inspection", tmp_path / "mapping"
        )
        == records
    )
    lines = compare_truth(
        *(
            tmp_path / name
            for name in ("generation", "inspection", "mapping", "execution")
        )
    )
    assert "comparison: available" in lines
    assert "quality assessment: not configured" in lines
    for parameter_id in generated.provenance.request.parameter_order:
        assert any(line.startswith(f"parameter {parameter_id}:") for line in lines)
    assert snapshot(tmp_path) == before
    relocated = tmp_path / "relocated-generation"
    _ = (tmp_path / "generation").rename(relocated)
    assert verify_generation_run(relocated) == generated
    assert (
        compare_truth(
            relocated,
            tmp_path / "inspection",
            tmp_path / "mapping",
            tmp_path / "execution",
        )
        == lines
    )


@pytest.mark.parametrize("fixture_id", FIXTURES)
def test_held_out_only_noise_changes_leave_training_and_fit_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_id: str
) -> None:
    generated, first_mapping = published_mapping(tmp_path / "first", fixture_id)
    first = published_execution(tmp_path / "first", first_mapping)
    sealed = canonical_json(first.result)
    original = cast(
        Callable[[int, float, str, str], float], vars(generation_module)["_noise"]
    )

    def held_out_noise(seed: int, sigma: float, role: str, fixture_id: str) -> float:
        value = original(seed, sigma, role, fixture_id)
        return -value if role == "held-out" else value

    monkeypatch.setattr(generation_module, "_noise", held_out_noise)
    changed, second_mapping = published_mapping(tmp_path / "second", fixture_id)
    second = published_execution(tmp_path / "second", second_mapping)
    assert_completed(second_mapping, second)
    assert (
        generated.provenance.generation_run_id != changed.provenance.generation_run_id
    )
    assert first_mapping.request.declaration == second_mapping.request.declaration
    assert first_mapping.diagnostics == second_mapping.diagnostics
    assert [row.point_model_m for row in first_mapping.observations] == [
        row.point_model_m for row in second_mapping.observations
    ]
    assert (
        first.result.final_parameters is not None
        and second.result.final_parameters is not None
    )
    assert first.result.final_parameters.values == second.result.final_parameters.values
    assert (
        first.result.final_evaluation is not None
        and second.result.final_evaluation is not None
    )
    assert (
        first.result.final_evaluation.raw_residuals_m
        == second.result.final_evaluation.raw_residuals_m
    )
    assert (
        first.result.final_evaluation.jacobian
        == second.result.final_evaluation.jacobian
    )
    assert first.result.normalized_termination == second.result.normalized_termination
    assert first.held_out is not None and second.held_out is not None
    assert first.held_out.summary != second.held_out.summary
    assert canonical_json(first.result) == sealed


@pytest.mark.parametrize("fixture_id", FIXTURES)
def test_missing_required_support_prevents_execution_and_held_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture_id: str
) -> None:
    _generated, mapping = published_mapping(tmp_path, fixture_id)
    factors = instantiate_factors(mapping)
    missing = factors.declaration.elements[-1].element_id
    active_ids = tuple(
        factor.factor_id
        for declaration, factor in zip(
            factors.declarations, factors.factors, strict=True
        )
        if declaration.element_id != missing
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ineligible execution invoked the optimizer")

    monkeypatch.setattr(DeclaredNumpyBackend, "execute", forbidden)
    records = create_execution_run(
        tmp_path / "execution",
        tmp_path / "inspection",
        tmp_path / "mapping",
        active_ids,
        ParameterVector(
            model_id=mapping.model_id,
            values=tuple(
                parameter.nominal for parameter in factors.declaration.parameters
            ),
        ),
    )
    assert records.result.disposition == "ineligible"
    assert records.held_out is None
    assert not (tmp_path / "execution" / "held-out.json").exists()
    assert (
        verify_execution_run(
            tmp_path / "execution", tmp_path / "inspection", tmp_path / "mapping"
        )
        == records
    )
    assert "comparison: unavailable" in compare_truth(
        *(
            tmp_path / name
            for name in ("generation", "inspection", "mapping", "execution")
        )
    )


def test_cross_model_substitution_fails_at_mapping_and_execution(
    tmp_path: Path,
) -> None:
    _first_generated, first_mapping = published_mapping(tmp_path / "first", FIXTURES[0])
    _second_generated, second_mapping = published_mapping(
        tmp_path / "second", FIXTURES[1]
    )
    first = published_execution(tmp_path / "first", first_mapping)
    second = published_execution(tmp_path / "second", second_mapping)
    request = first_mapping.request.model_dump(mode="python")
    request["declaration"] = second_mapping.request.declaration
    with pytest.raises(ValidationError, match="generation and mapping"):
        _ = MappingRequest.model_validate(request)
    raw = copy.deepcopy(first.result.model_dump(mode="python"))
    raw["final_parameters"] = second.result.final_parameters
    with pytest.raises(ValidationError, match=r"model|provenance|ID"):
        _ = ExecutionResult.model_validate(raw)
    with pytest.raises(
        ScansorError, match=r"provenance|reference|model|mapping|replay"
    ):
        _ = verify_execution_run(
            tmp_path / "first" / "execution",
            tmp_path / "second" / "inspection",
            tmp_path / "second" / "mapping",
        )
    with pytest.raises(
        ScansorError, match=r"provenance|source|hash|content|replay|inspection"
    ):
        _ = compare_truth(
            tmp_path / "second" / "generation",
            tmp_path / "first" / "inspection",
            tmp_path / "first" / "mapping",
            tmp_path / "first" / "execution",
        )


@pytest.mark.parametrize(
    "field", ["held_out_row_indices", "transform", "source_frame", "signed-zero"]
)
def test_generation_pose_and_partition_are_exact_mapping_inputs(
    tmp_path: Path, field: str
) -> None:
    _generated, mapping = published_mapping(tmp_path, FIXTURES[1])
    raw = mapping.request.model_dump(mode="python")
    if field == "held_out_row_indices":
        raw[field] = ()
    elif field == "transform":
        raw[field]["translation_m"] = (0.0, 0.0, 0.0)
    elif field == "signed-zero":
        rotation = [list(row) for row in raw["transform"]["rotation"]]
        rotation[0][1] = -0.0
        raw["transform"]["rotation"] = rotation
    else:
        raw["input_revision"]["observation_frame"] = "substituted-frame"
    with pytest.raises(ValidationError, match="generation and mapping"):
        _ = MappingRequest.model_validate(raw)


def test_generated_mapping_rechecks_generation_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _generated, mapping = published_mapping(tmp_path, FIXTURES[1])
    original = cast(
        Callable[..., None], vars(mapping_runs_module)["_verify_mapping_artifacts"]
    )
    mutated = False

    def mutate(
        directory_fd: int, artifacts: dict[str, bytes], identities: dict[str, Any]
    ) -> None:
        nonlocal mutated
        original(directory_fd, artifacts, identities)
        if not mutated:
            mutated = True
            path = tmp_path / "generation" / "observations.ply"
            _ = path.write_bytes(path.read_bytes() + b"x")

    monkeypatch.setattr(mapping_runs_module, "_verify_mapping_artifacts", mutate)
    output = tmp_path / "new-mapping"
    with pytest.raises(ScansorError, match=r"generation|manifest|source|artifact"):
        _ = create_generated_mapping_run(
            output, tmp_path / "generation", tmp_path / "inspection", mapping.request
        )
    assert mutated
    assert not output.exists()


def test_generated_mapping_output_protects_generation_tree(tmp_path: Path) -> None:
    _generated, mapping = published_mapping(tmp_path, FIXTURES[1])
    with pytest.raises(ScansorError, match="generation tree"):
        _ = create_generated_mapping_run(
            tmp_path / "generation" / "bad",
            tmp_path / "generation",
            tmp_path / "inspection",
            mapping.request,
        )
