from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

import scansor.declared_execution_runs as execution_runs_module
from scansor.declared_execution_models import execution_content_id
from scansor.declared_execution_run_models import (
    ExecutionRunArtifact,
    ExecutionRunRecords,
    ExecutionRunSelection,
    HeldOutRunReference,
    run_content_id,
)
from scansor.declared_execution_runs import (
    create_execution_run,
    run_numpy_execution,
    verify_execution_run,
)
from scansor.declared_factor_models import ParameterVector
from scansor.declared_factors import instantiate_factors
from scansor.declared_numpy_backend import DeclaredNumpyBackend
from scansor.errors import ScansorError
from scansor.mapping_models import MappingResult
from scansor.mapping_runs import create_mapping_run
from scansor.model_declarations import ModelDeclaration
from scansor.serialization import canonical_json, sha256
from scansor.stepped_model_declarations import Variant
from tests.test_declared_execution import reidentified_result
from tests.test_mapping import inspection_mapping_fixture


def _parameters(mapping: MappingResult) -> ParameterVector:
    declaration = mapping.request.declaration
    return ParameterVector(
        model_id=declaration.model_id,
        values=tuple(parameter.nominal for parameter in declaration.parameters),
    )


def published_inputs(
    tmp_path: Path, variant: Variant = "asymmetric-datum-flat"
) -> tuple[Path, Path, MappingResult]:
    tmp_path.mkdir(exist_ok=True)
    inspection_run, mapping = inspection_mapping_fixture(tmp_path, variant)
    mapping_run = tmp_path / "mapping"
    _ = create_mapping_run(mapping_run, inspection_run, mapping.request)
    return inspection_run, mapping_run, mapping


def _active_ids(mapping: MappingResult) -> tuple[str, ...]:
    return tuple(item.factor_id for item in instantiate_factors(mapping).factors)


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_declared_publication_replays_read_only_without_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: Variant
) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path, variant)
    output = tmp_path / "execution"
    created = create_execution_run(
        output,
        inspection_run,
        mapping_run,
        _active_ids(mapping),
        _parameters(mapping),
    )
    assert created.result.disposition == "completed-not-assessed"
    assert created.held_out is not None
    assert created.manifest is not None
    assert {item.name for item in output.iterdir()} == {
        "selection.json",
        "result.json",
        "held-out.json",
        "manifest.json",
        "manifest.sha256",
    }

    declaration = mapping.request.declaration
    request = created.result.request
    assert (
        created.selection.format
        == "scansor-declared-analytic-model-execution-selection-v1"
    )
    assert (
        created.result.format == "scansor-declared-analytic-model-execution-result-v1"
    )
    assert (
        created.manifest.format
        == "scansor-declared-analytic-model-execution-run-manifest-v1"
    )
    assert (
        created.held_out.format
        == "scansor-declared-analytic-model-held-out-assessment-v1"
    )
    assert created.selection.declaration == declaration
    assert created.selection.model_id == mapping.model_id == declaration.model_id
    assert request.declaration == declaration
    assert request.model_id == created.result.model_id == declaration.model_id
    assert request.parameter_order == tuple(
        parameter.parameter_id for parameter in declaration.parameters
    )
    assert request.lower_bounds == tuple(
        parameter.lower for parameter in declaration.parameters
    )
    assert request.upper_bounds == tuple(
        parameter.upper for parameter in declaration.parameters
    )
    assert request.parameter_scales == tuple(
        parameter.diagnostic_scale for parameter in declaration.parameters
    )
    assert len(request.initial_parameters.values) == len(declaration.parameters)
    assert request.active_factor_count == len(
        created.selection.active_selection.active_factor_ids
    )
    assert request.problem == declaration.problem.kind == "fixed-pose-shape"
    assert created.result.invocation is not None
    assert created.result.invocation.declaration == declaration
    assert created.result.invocation.model_id == declaration.model_id
    assert created.result.invocation.parameter_order == request.parameter_order
    assert created.result.invocation.parameter_scales == request.parameter_scales
    assert created.result.final_evaluation is not None
    assert all(
        len(row) == len(declaration.parameters)
        for row in created.result.final_evaluation.jacobian
    )
    assert created.manifest.declaration == declaration
    assert created.manifest.model_id == declaration.model_id
    assert created.manifest.mapping.model_id == declaration.model_id
    assert created.held_out.model_id == declaration.model_id
    assert all(row.model_id == declaration.model_id for row in created.held_out.rows)

    before = {path.name: path.read_bytes() for path in output.iterdir()}

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verification invoked the backend")

    backend_type = cast(
        type[DeclaredNumpyBackend],
        vars(execution_runs_module)["DeclaredNumpyBackend"],
    )
    monkeypatch.setattr(backend_type, "execute", forbidden)
    assert verify_execution_run(output, inspection_run, mapping_run) == created
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_ineligible_publication_is_model_bound_and_omits_held_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ineligible execution invoked the backend")

    monkeypatch.setattr(DeclaredNumpyBackend, "execute", forbidden)
    output = tmp_path / "execution"
    created = create_execution_run(
        output,
        inspection_run,
        mapping_run,
        (),
        _parameters(mapping),
    )
    assert created.result.disposition == "ineligible"
    assert created.result.model_id == mapping.model_id
    assert created.held_out is None
    assert created.manifest is not None
    assert created.manifest.held_out.state == "not-applicable-noncompleted"
    assert {item.name for item in output.iterdir()} == {
        "selection.json",
        "result.json",
        "manifest.json",
        "manifest.sha256",
    }
    assert verify_execution_run(output, inspection_run, mapping_run) == created


def test_no_overwrite_and_input_tree_protections(tmp_path: Path) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)
    active_ids = _active_ids(mapping)
    parameters = _parameters(mapping)
    output = tmp_path / "execution"
    _ = create_execution_run(
        output, inspection_run, mapping_run, active_ids, parameters
    )
    with pytest.raises(ScansorError, match="already exists"):
        _ = create_execution_run(
            output, inspection_run, mapping_run, active_ids, parameters
        )
    with pytest.raises(ScansorError, match="input tree"):
        _ = create_execution_run(
            mapping_run / "execution",
            inspection_run,
            mapping_run,
            active_ids,
            parameters,
        )


def test_tampering_and_valid_cross_model_substitution_fail_closed(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "axisymmetric"
    second_root = tmp_path / "asymmetric"
    first_inspection, first_mapping_run, first_mapping = published_inputs(
        first_root, "axisymmetric"
    )
    second_inspection, second_mapping_run, second_mapping = published_inputs(
        second_root, "asymmetric-datum-flat"
    )
    first_output = first_root / "execution"
    second_output = second_root / "execution"
    first = create_execution_run(
        first_output,
        first_inspection,
        first_mapping_run,
        _active_ids(first_mapping),
        _parameters(first_mapping),
    )
    second = create_execution_run(
        second_output,
        second_inspection,
        second_mapping_run,
        _active_ids(second_mapping),
        _parameters(second_mapping),
    )
    assert first_mapping.model_id != second_mapping.model_id

    with pytest.raises(ScansorError, match="model bindings disagree"):
        _ = verify_execution_run(first_output, second_inspection, second_mapping_run)

    with pytest.raises(ValidationError, match="selection disagrees"):
        _ = ExecutionRunRecords(
            held_out=first.held_out,
            result=first.result,
            selection=second.selection,
        )

    stale_declaration = first.selection.declaration.model_copy(
        update={"model_id": "model." + "0" * 64}
    )
    stale_selection = first.selection.model_copy(
        update={"declaration": stale_declaration}
    )
    stale_selection = stale_selection.model_copy(
        update={
            "execution_selection_id": run_content_id(
                "execution-selection", stale_selection, "execution_selection_id"
            )
        }
    )
    with pytest.raises(ValidationError, match="model ID"):
        _ = ExecutionRunSelection.model_validate(
            stale_selection.model_dump(mode="python")
        )

    _ = (second_output / "manifest.sha256").write_bytes(b"0" * 64)
    with pytest.raises(ScansorError, match="sidecar"):
        _ = verify_execution_run(second_output, second_inspection, second_mapping_run)


def test_dimension_mismatch_is_rejected_before_execution(tmp_path: Path) -> None:
    _inspection_run, _mapping_run, mapping = published_inputs(tmp_path)
    declaration: ModelDeclaration = mapping.request.declaration
    parameters = ParameterVector(
        model_id=declaration.model_id,
        values=tuple(parameter.nominal for parameter in declaration.parameters[:-1]),
    )
    with pytest.raises((ScansorError, ValidationError), match="dimension"):
        _ = run_numpy_execution(mapping, _active_ids(mapping), parameters)


def test_semantic_result_tampering_fails_after_all_artifact_hashes_are_recomputed(
    tmp_path: Path,
) -> None:
    inspection, mapping_run, mapping = published_inputs(tmp_path)
    output = tmp_path / "execution"
    created = create_execution_run(
        output, inspection, mapping_run, _active_ids(mapping), _parameters(mapping)
    )
    assert created.held_out is not None and created.manifest is not None
    result = reidentified_result(created.result, final_objective=0.001)
    held_out = created.held_out.model_copy(
        update={"execution_result_id": result.result_id}
    )
    held_out = type(held_out).model_validate(
        held_out.model_dump(mode="python")
        | {
            "assessment_id": execution_content_id(
                "held-out-assessment", held_out, "assessment_id"
            )
        }
    )
    artifacts = {
        "selection.json": canonical_json(created.selection),
        "result.json": canonical_json(result),
        "held-out.json": canonical_json(held_out),
    }
    manifest = created.manifest.model_copy(
        update={
            "result_id": result.result_id,
            "held_out": HeldOutRunReference(
                state="assessed",
                assessment_id=held_out.assessment_id,
                sha256=sha256(artifacts["held-out.json"]),
            ),
            "artifacts": {
                name: ExecutionRunArtifact(byte_count=len(data), sha256=sha256(data))
                for name, data in artifacts.items()
            },
        }
    )
    manifest = type(manifest).model_validate(
        manifest.model_dump(mode="python")
        | {
            "execution_run_id": run_content_id(
                "execution-run", manifest, "execution_run_id"
            )
        }
    )
    _ = ExecutionRunRecords(
        held_out=held_out, manifest=manifest, result=result, selection=created.selection
    )
    artifacts["manifest.json"] = canonical_json(manifest)
    artifacts["manifest.sha256"] = (
        f"{sha256(artifacts['manifest.json'])}  manifest.json\n".encode("ascii")
    )
    for name, data in artifacts.items():
        _ = (output / name).write_bytes(data)
    with pytest.raises(ScansorError, match="replay disposition facts"):
        _ = verify_execution_run(output, inspection, mapping_run)
