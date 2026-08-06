from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

import scansor.stepped_rotational_numpy_backend as backend_module
from scansor.errors import ScansorError
from scansor.factor_models import NOMINAL_SHAPE
from scansor.serialization import canonical_json
from scansor.stepped_rotational_execution import create_execution_request, execute
from scansor.stepped_rotational_numpy_backend import (
    NUMPY_GAUSS_NEWTON_DESCRIPTOR,
    SteppedRotationalNumpyBackend,
)
from tests.test_execution import execution_case


def numpy_case(
    variant: str,
    problem: str,
    initial: tuple[float, ...],
    *,
    callback_limit: int = 256,
):
    _mapping, factor_set, selection, old_request = execution_case(
        variant, problem, initial
    )
    request = create_execution_request(
        factor_set,
        selection,
        old_request.initial_parameters,
        NUMPY_GAUSS_NEWTON_DESCRIPTOR,
        callback_limit=callback_limit,
    )
    return factor_set, selection, request


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_exact_shape_recovery_is_deterministic(variant: str) -> None:
    size = 7 if variant == "asymmetric-datum-flat" else 6
    truth = NOMINAL_SHAPE[:size]
    offsets = (-0.0004, 0.0005, -0.0003, -0.0002, 0.0003, -0.0004, 0.0002)
    initial = tuple(value + delta for value, delta in zip(truth, offsets, strict=False))
    factor_set, selection, request = numpy_case(variant, "fixed-pose-shape", initial)
    first = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    second = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert canonical_json(first) == canonical_json(second)
    assert first.disposition == "completed-not-assessed"
    assert first.normalized_termination.raw_code == "residual-tolerance"
    assert first.final_parameters is not None
    np.testing.assert_allclose(first.final_parameters.values, truth, atol=1e-15, rtol=0)


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_bounded_pose_recovery_and_roll_gauge(variant: str) -> None:
    initial = (0.0003, -0.0002, 0.0001, 0.012, -0.009, 0.025)
    factor_set, selection, request = numpy_case(
        variant, "fixed-geometry-pose-correction", initial
    )
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.final_parameters is not None
    values = result.final_parameters.values
    np.testing.assert_allclose(values[:5], (0.0,) * 5, atol=2e-11, rtol=0)
    if variant == "axisymmetric":
        assert result.final_evaluation is not None
        assert (
            max(abs(value) for value in result.final_evaluation.raw_residuals_m)
            <= 1e-12
        )
        initial_entry, first_trial = result.callback_trace[:2]
        assert initial_entry.evaluation is not None
        jacobian = np.asarray(initial_entry.evaluation.jacobian)
        scales = np.asarray(request.parameter_scales)
        _left, _singular, right = np.linalg.svd(
            jacobian * scales[np.newaxis, :] / 0.001, full_matrices=False
        )
        scaled_increment = (
            np.asarray(first_trial.values) - np.asarray(initial_entry.values)
        ) / scales
        assert float(scaled_increment @ right[-1]) == pytest.approx(0.0, abs=2e-13)
    else:
        assert values[5] == pytest.approx(0.0, abs=2e-11)


def test_callback_cap_has_no_extra_attempt() -> None:
    initial = tuple(value + 0.0002 for value in NOMINAL_SHAPE)
    factor_set, selection, request = numpy_case(
        "asymmetric-datum-flat",
        "fixed-pose-shape",
        initial,
        callback_limit=1,
    )
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert result.callback_count == 1
    assert result.normalized_termination.raw_code == "callback-limit"
    assert result.normalized_termination.category == "backend-limit-reached"


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_active_bound_re_solves_in_feasible_tangent_space(variant: str) -> None:
    initial = (0.0003, -0.0002, 0.0001, 0.012, -0.009, -0.08)
    factor_set, selection, request = numpy_case(
        variant, "fixed-geometry-pose-correction", initial
    )
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.callback_count > 1
    assert result.normalized_termination.raw_code != "line-search-stagnation"
    assert result.final_objective is not None
    assert result.initial_evaluation is not None
    initial_objective = 0.5 * sum(
        value * value for value in result.initial_evaluation.raw_residuals_m
    )
    assert result.final_objective < initial_objective


def test_convergence_precedes_step_and_objective_stagnation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = tuple(value + 0.0002 for value in NOMINAL_SHAPE)
    factor_set, selection, request = numpy_case(
        "asymmetric-datum-flat", "fixed-pose-shape", initial
    )
    monkeypatch.setattr(backend_module, "RELATIVE_SCALED_STEP_THRESHOLD", 1.0)
    monkeypatch.setattr(backend_module, "RELATIVE_OBJECTIVE_STAGNATION", 1.0)
    monkeypatch.setattr(backend_module, "OBJECTIVE_STAGNATION_STEPS", 1)
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert result.normalized_termination.category == "backend-converged"
    assert result.normalized_termination.raw_code == "residual-tolerance"


def test_invocation_mismatch_is_rejected_before_callback() -> None:
    factor_set, selection, request = numpy_case(
        "axisymmetric", "fixed-pose-shape", NOMINAL_SHAPE[:6]
    )
    stale = request.model_copy(
        update={"parameter_order": tuple(reversed(request.parameter_order))}
    )
    with pytest.raises(ScansorError, match=r"invalid execution request|does not match"):
        execute(stale, factor_set, selection, SteppedRotationalNumpyBackend())


def test_svd_failure_is_a_valid_completed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = tuple(value + 0.0002 for value in NOMINAL_SHAPE)
    factor_set, selection, request = numpy_case(
        "asymmetric-datum-flat", "fixed-pose-shape", initial
    )

    def fail_svd(*args: object, **kwargs: object) -> object:
        raise np.linalg.LinAlgError

    monkeypatch.setattr(backend_module, "_svd", fail_svd)
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.normalized_termination.category == "backend-reported-failure"
    assert result.normalized_termination.raw_code == "svd-failed"


def test_backend_import_boundary() -> None:
    source_path = Path(backend_module.__file__ or "")
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    prohibited = {
        "scipy",
        "scansor.cli",
        "scansor.files",
        "scansor.mapping_models",
        "scansor.mapping_runs",
        "scansor.runs",
        "scansor.stepped_rotational",
        "scansor.stepped_rotational_execution",
        "scansor.stepped_rotational_factors",
    }
    assert not any(
        imported == name or imported.startswith(f"{name}.")
        for imported in imports
        for name in prohibited
    )
