from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import scansor.execution_runs as execution_runs_module
from scansor.errors import ScansorError
from scansor.execution_runs import (
    create_execution_run,
    run_numpy_execution,
    verify_execution_run,
)
from scansor.factor_models import NOMINAL_SHAPE, ParameterVector
from scansor.mapping_runs import create_mapping_run
from scansor.stepped_rotational_factors import instantiate_factors
from tests.test_factors import parameters
from tests.test_mapping import inspection_mapping_fixture


def published_inputs(tmp_path: Path, variant: str = "asymmetric-datum-flat"):
    inspection_run, mapping = inspection_mapping_fixture(tmp_path, variant)
    mapping_run = tmp_path / "mapping"
    create_mapping_run(mapping_run, inspection_run, mapping.request)
    return inspection_run, mapping_run, mapping


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
@pytest.mark.parametrize(
    "problem", ["fixed-pose-shape", "fixed-geometry-pose-correction"]
)
def test_in_memory_completed_shape_and_pose(variant: str, problem: str) -> None:
    from tests.test_factors import factor_case

    mapping, factor_set, _selection = factor_case(variant)
    active_ids = tuple(item.factor_id for item in factor_set.factors)
    if problem == "fixed-pose-shape":
        size = 7 if variant == "asymmetric-datum-flat" else 6
        initial_values = tuple(value + 0.0001 for value in NOMINAL_SHAPE[:size])
    else:
        initial_values = (0.0002, -0.0001, 0.0001, 0.01, -0.008, 0.02)
    records = run_numpy_execution(
        mapping, active_ids, parameters(variant, problem, initial_values)
    )
    assert records.result.disposition == "completed-not-assessed"
    assert records.held_out is not None
    assert records.manifest is None


def test_completed_publication_and_adapter_free_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)
    factor_set = instantiate_factors(mapping)
    active_ids = tuple(item.factor_id for item in factor_set.factors)
    output = tmp_path / "execution"
    created = create_execution_run(
        output,
        inspection_run,
        mapping_run,
        active_ids,
        parameters("asymmetric-datum-flat"),
    )
    assert created.manifest is not None
    assert {item.name for item in output.iterdir()} == {
        "selection.json",
        "result.json",
        "held-out.json",
        "manifest.json",
        "manifest.sha256",
    }

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("verification invoked the backend")

    monkeypatch.setattr(
        execution_runs_module.SteppedRotationalNumpyBackend, "execute", forbidden
    )
    verified = verify_execution_run(output, inspection_run, mapping_run)
    assert verified == created


def test_ineligible_publication_omits_held_out(tmp_path: Path) -> None:
    inspection_run, mapping_run, _mapping = published_inputs(tmp_path)
    output = tmp_path / "execution"
    created = create_execution_run(
        output,
        inspection_run,
        mapping_run,
        (),
        parameters("asymmetric-datum-flat"),
    )
    assert created.result.disposition == "ineligible"
    assert created.held_out is None
    assert {item.name for item in output.iterdir()} == {
        "selection.json",
        "result.json",
        "manifest.json",
        "manifest.sha256",
    }
    assert verify_execution_run(output, inspection_run, mapping_run) == created


def test_tampering_and_no_overwrite_fail_closed(tmp_path: Path) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)
    factor_set = instantiate_factors(mapping)
    active_ids = tuple(item.factor_id for item in factor_set.factors)
    output = tmp_path / "execution"
    create_execution_run(
        output,
        inspection_run,
        mapping_run,
        active_ids,
        parameters("asymmetric-datum-flat"),
    )
    with pytest.raises(ScansorError, match="already exists"):
        create_execution_run(
            output,
            inspection_run,
            mapping_run,
            active_ids,
            parameters("asymmetric-datum-flat"),
        )
    (output / "manifest.sha256").write_bytes(b"0" * 64)
    with pytest.raises(ScansorError, match="sidecar"):
        verify_execution_run(output, inspection_run, mapping_run)


def test_output_cannot_be_inside_an_input_tree(tmp_path: Path) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)
    factor_set = instantiate_factors(mapping)
    active_ids = tuple(item.factor_id for item in factor_set.factors)
    with pytest.raises(ScansorError, match="input tree"):
        create_execution_run(
            mapping_run / "execution",
            inspection_run,
            mapping_run,
            active_ids,
            parameters("asymmetric-datum-flat"),
        )


def test_output_replaced_during_final_input_replay_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_run, mapping_run, mapping = published_inputs(tmp_path)
    factor_set = instantiate_factors(mapping)
    active_ids = tuple(item.factor_id for item in factor_set.factors)
    output = tmp_path / "execution"
    moved = tmp_path / "moved-execution"
    original_verify = execution_runs_module.verify_run_artifacts_fd
    replaced = False

    def replace_output_during_replay(*args: object, **kwargs: object):
        nonlocal replaced
        verified = original_verify(*args, **kwargs)
        if output.exists() and not replaced:
            output.rename(moved)
            output.mkdir()
            (output / "preserve").write_text("foreign", encoding="ascii")
            replaced = True
        return verified

    monkeypatch.setattr(
        execution_runs_module,
        "verify_run_artifacts_fd",
        replace_output_during_replay,
    )
    with pytest.raises(ScansorError, match="execution output path changed"):
        create_execution_run(
            output,
            inspection_run,
            mapping_run,
            active_ids,
            parameters("asymmetric-datum-flat"),
        )
    assert replaced
    assert (output / "preserve").read_text(encoding="ascii") == "foreign"
    assert moved.is_dir()
    assert (moved / "manifest.json").is_file()


def test_initial_parameters_are_explicit_and_strict() -> None:
    with pytest.raises(ValidationError):
        ParameterVector(
            problem="fixed-pose-shape",
            units="metre",
            values=NOMINAL_SHAPE[:5],
            variant="axisymmetric",
        )
