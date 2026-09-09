from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

import scansor.declared_numpy_backend as backend_module
from scansor.declared_execution import (
    create_execution_request,
    execute,
    replay_execution,
)
from scansor.declared_execution_models import BackendResponse, execution_content_id
from scansor.declared_factor_models import FactorEvaluation, content_id
from scansor.declared_factors import evaluate_factors, preflight_factors
from scansor.declared_numpy_backend import (
    NUMPY_GAUSS_NEWTON_DESCRIPTOR,
    DeclaredNumpyBackend,
)
from scansor.model_declarations import (
    ModelDeclaration,
    ModelSemanticDeclaration,
    identify_model,
)
from scansor.serialization import canonical_json
from scansor.stepped_model_declarations import Variant, stepped_model_declaration

# Reuse the declared-factor tests' internal construction fixtures.
from tests.test_declared_factors import (
    _factor_case,  # pyright: ignore[reportPrivateUsage]
    _parameters,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_mapping import constructed_shell_declaration, fixture_points
from tests.test_numpy_backend import numpy_case


def double_shell_declaration(
    *, rank_ids: tuple[str, ...] = ("bore-size",), required_rank: int = 1
) -> ModelDeclaration:
    record: dict[str, Any] = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"] += (
        {
            "parameter_id": "bore-size",
            "nominal": 0.005,
            "lower": 0.002,
            "upper": 0.0075,
            "diagnostic_scale": 0.0004,
            "unit": "m",
        },
    )
    record["problem"]["varied_parameter_ids"] += ("bore-size",)
    record["relationships"] = (
        {
            "kind": "oriented-offset",
            "relationship_id": "bore-radius",
            "parameter_id": "bore-size",
            "constant": 0.0,
            "coefficient": 2.0,
        },
    )
    record["elements"] += (
        {
            "element_id": "inward-wall",
            "primitive": {
                "kind": "coaxial-cylinder",
                "radial_orientation": -1,
                "radius": {"kind": "relationship", "relationship_id": "bore-radius"},
            },
            "domain": {
                "domain_id": "inward-wall.domain",
                "predicates": (
                    {
                        "kind": "axial-interval",
                        "predicate_id": "inward-wall.axial",
                        "lower": {"kind": "literal", "value": -1.0},
                        "upper": {"kind": "literal", "value": 1.0},
                    },
                ),
            },
        },
    )
    for name in ("mapping_admission", "optimization_preflight"):
        record[name]["required_support"] += (
            {"element_id": "inward-wall", "minimum_count": 1},
        )
    record["optimization_preflight"]["relative_rank"] = {
        "parameter_ids": rank_ids,
        "parameter_scales": tuple(
            0.003 * (index + 1) for index in range(len(rank_ids))
        ),
        "relative_threshold": 1e-8,
        "required_rank": required_rank,
        "residual_scale": 0.007,
    }
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def shell_points(*, double: bool = False) -> list[tuple[float, float, float]]:
    return [
        (radius, 0.0, axial)
        for radius in ((0.02, 0.01) if double else (0.02,))
        for axial in (-0.25, 0.25, 0.75)
    ]


def shell_case(
    declaration: ModelDeclaration,
    *,
    double: bool = False,
    initial: tuple[float, ...] | None = None,
    callback_limit: int = 256,
):
    _mapping, factor_set, selection = _factor_case(
        declaration, shell_points(double=double)
    )
    request = create_execution_request(
        factor_set,
        selection,
        _parameters(
            declaration,
            initial or tuple(p.nominal + 0.0002 for p in declaration.parameters),
        ),
        NUMPY_GAUSS_NEWTON_DESCRIPTOR,
        callback_limit=callback_limit,
    )
    return factor_set, selection, request


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_stepped_shape_execution_matches_legacy_numerics(variant: Variant) -> None:
    from scansor.stepped_rotational_execution import execute as execute_legacy
    from scansor.stepped_rotational_numpy_backend import SteppedRotationalNumpyBackend

    declaration = stepped_model_declaration(variant)
    initial = tuple(item.nominal + 0.0002 for item in declaration.parameters)
    _mapping, factor_set, selection = _factor_case(
        declaration,
        fixture_points(asymmetric=variant == "asymmetric-datum-flat"),
        asymmetric=variant == "asymmetric-datum-flat",
    )
    request = create_execution_request(
        factor_set,
        selection,
        _parameters(declaration, initial),
        NUMPY_GAUSS_NEWTON_DESCRIPTOR,
    )
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    repeated = execute(request, factor_set, selection, DeclaredNumpyBackend())
    old_set, old_selection, old_request = numpy_case(
        variant, "fixed-pose-shape", initial
    )
    legacy = execute_legacy(
        old_request, old_set, old_selection, SteppedRotationalNumpyBackend()
    )

    assert result.disposition == legacy.disposition == "completed-not-assessed"
    assert result.normalized_termination.model_dump(
        mode="python"
    ) == legacy.normalized_termination.model_dump(mode="python")
    assert canonical_json(result) == canonical_json(repeated)
    assert result.final_parameters is not None and legacy.final_parameters is not None
    assert result.final_parameters.values == legacy.final_parameters.values
    assert result.final_evaluation is not None and legacy.final_evaluation is not None
    assert (
        result.final_evaluation.raw_residuals_m
        == legacy.final_evaluation.raw_residuals_m
    )
    assert result.final_evaluation.jacobian == legacy.final_evaluation.jacobian
    assert result.final_objective == legacy.final_objective
    assert [entry.values for entry in result.callback_trace] == [
        entry.values for entry in legacy.callback_trace
    ]
    _ = replay_execution(result, request, factor_set, selection)


@pytest.mark.parametrize("double", [False, True])
def test_constructed_topologies_recover_all_parameters_deterministically(
    double: bool,
) -> None:
    declaration = (
        double_shell_declaration() if double else constructed_shell_declaration()
    )
    factor_set, selection, request = shell_case(declaration, double=double)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.normalized_termination.raw_code == "residual-tolerance"
    assert result.final_parameters is not None
    np.testing.assert_allclose(
        result.final_parameters.values,
        [p.nominal for p in declaration.parameters],
        atol=1e-15,
        rtol=0,
    )
    assert canonical_json(result) == canonical_json(
        execute(request, factor_set, selection, DeclaredNumpyBackend())
    )
    _ = replay_execution(result, request, factor_set, selection)


def test_above_minimum_rank_is_eligible_and_executes() -> None:
    declaration = double_shell_declaration(rank_ids=("bore-size", "shell-radius"))
    factor_set, selection, request = shell_case(declaration, double=True)
    preflight = preflight_factors(factor_set, selection, request.initial_parameters)
    assert preflight.expected_rank == 1
    assert preflight.observed_rank == 2
    assert preflight.eligible_for_optimization
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.normalized_termination.raw_code == "residual-tolerance"


def test_rank_subsequence_does_not_limit_execution_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declaration = double_shell_declaration()
    factor_set, selection, request = shell_case(declaration, double=True)
    matrices: list[np.ndarray] = []

    def record_svd(matrix: np.ndarray):
        matrices.append(matrix.copy())
        return np.linalg.svd(matrix, full_matrices=False)

    monkeypatch.setattr(backend_module, "_svd", record_svd)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.initial_evaluation is not None
    expected = (
        np.asarray(result.initial_evaluation.jacobian)
        * np.asarray([0.002, 0.0004])
        / 0.007
    )
    np.testing.assert_array_equal(matrices[0], expected)
    assert result.final_parameters is not None
    np.testing.assert_allclose(
        result.final_parameters.values, (0.02, 0.005), atol=1e-15, rtol=0
    )


@pytest.mark.parametrize(
    "rank_scales,threshold,required,expected",
    [
        ((0.003, 0.006), 1e-8, 2, "residual-tolerance"),
        ((0.003, 1e-15), 1e-8, 2, "rank-deficient"),
        ((0.003, 1e-15), 1e-14, 2, "residual-tolerance"),
    ],
)
def test_backend_applies_exact_rank_policy(
    rank_scales: tuple[float, float], threshold: float, required: int, expected: str
) -> None:
    declaration = double_shell_declaration(
        rank_ids=("bore-size", "shell-radius"), required_rank=required
    )
    factor_set, selection, request = shell_case(declaration, double=True)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None
    record = declaration.model_dump(mode="python", exclude={"model_id"})
    record["optimization_preflight"]["relative_rank"]["parameter_scales"] = rank_scales
    record["optimization_preflight"]["relative_rank"]["relative_threshold"] = threshold
    changed = identify_model(ModelSemanticDeclaration.model_validate(record))
    changed_set, changed_selection, changed_request = shell_case(changed, double=True)
    # Obtain the strictly bound invocation without bypassing ineligible preflight.
    provisional = result.invocation.model_copy(
        update={
            "declaration": changed,
            "model_id": changed.model_id,
            "request_id": changed_request.request_id,
            "factor_set_id": changed_set.factor_set_id,
            "mapping_run_id": changed_set.mapping_run_id,
            "selection_id": changed_selection.selection_id,
        }
    )
    invocation = type(result.invocation).model_validate(
        provisional.model_dump(mode="python")
        | {
            "invocation_id": execution_content_id(
                "invocation", provisional, "invocation_id"
            )
        }
    )

    def callback(values: tuple[float, ...]) -> FactorEvaluation:
        return evaluate_factors(
            changed_set, changed_selection, _parameters(changed, values)
        )

    response = DeclaredNumpyBackend().execute(invocation, callback)
    assert isinstance(response, BackendResponse)
    assert response.raw_code == expected


def test_rank_deficiency_from_untrusted_callback_is_detected() -> None:
    declaration = constructed_shell_declaration()
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None

    def callback(values: tuple[float, ...]) -> FactorEvaluation:
        evaluation = evaluate_factors(
            factor_set, selection, _parameters(declaration, values)
        )
        provisional = evaluation.model_copy(update={"jacobian": ((0.0,),) * 3})
        return FactorEvaluation.model_validate(
            provisional.model_dump(mode="python")
            | {"evaluation_id": content_id("evaluation", provisional, "evaluation_id")}
        )

    response = DeclaredNumpyBackend().execute(result.invocation, callback)
    assert isinstance(response, BackendResponse)
    assert response.raw_code == "rank-deficient"


def test_rank_policy_can_allow_a_full_vector_null_direction() -> None:
    record = double_shell_declaration().model_dump(mode="python", exclude={"model_id"})
    record["parameters"] += (
        {
            "parameter_id": "extent",
            "nominal": 1.0,
            "lower": 0.8,
            "upper": 1.2,
            "diagnostic_scale": 0.1,
            "unit": "m",
        },
    )
    record["problem"]["varied_parameter_ids"] += ("extent",)
    record["elements"][0]["domain"]["predicates"][0]["upper"] = {
        "kind": "parameter",
        "parameter_id": "extent",
    }
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(
        declaration, double=True, initial=(0.0202, 0.0052, 0.9)
    )
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.normalized_termination.raw_code == "residual-tolerance"
    assert result.final_parameters is not None
    np.testing.assert_allclose(
        result.final_parameters.values, (0.02, 0.005, 0.9), atol=1e-15, rtol=0
    )
    assert result.final_evaluation is not None
    assert all(row[-1] == 0.0 for row in result.final_evaluation.jacobian)


def test_declared_bound_produces_projected_gradient_termination() -> None:
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0]["nominal"] = 0.0199
    record["parameters"][0]["upper"] = 0.01995
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(declaration, initial=(0.0198,))
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.normalized_termination.raw_code == "projected-gradient-tolerance"
    assert result.final_parameters is not None
    assert result.final_parameters.values == (0.01995,)
    assert result.bound_activity is not None
    assert result.bound_activity.activity == ("upper",)
    assert result.final_objective is not None and result.final_objective > 0.0
    _ = replay_execution(result, request, factor_set, selection)


def test_structural_trial_rejection_precedes_callback() -> None:
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0]["nominal"] = 0.0199
    record["structural_predicates"] += (
        {
            "kind": "ordered-minimum-separation",
            "predicate_id": "radius-below-lip",
            "left": {"kind": "parameter", "parameter_id": "shell-radius"},
            "right": {"kind": "literal", "value": 0.01995},
            "minimum": 0.0,
        },
    )
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(declaration, initial=(0.0198,))
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.disposition == "completed-not-assessed"
    assert result.callback_count > 1
    assert all(
        entry.status == "successful" and entry.values[0] < 0.01995
        for entry in result.callback_trace
    )
    assert result.final_parameters is not None
    assert 0.0198 < result.final_parameters.values[0] < 0.01995


def test_callback_budget_and_svd_failure_are_separate_from_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factor_set, selection, request = shell_case(
        constructed_shell_declaration(), callback_limit=1
    )
    limited = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert limited.callback_count == 1
    assert limited.normalized_termination.raw_code == "callback-limit"
    assert limited.disposition == "completed-not-assessed"

    def fail_svd(_matrix: np.ndarray):
        raise np.linalg.LinAlgError

    monkeypatch.setattr(backend_module, "_svd", fail_svd)
    failed = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert failed.normalized_termination.raw_code == "svd-failed"
    assert failed.disposition == "completed-not-assessed"


def test_rehashed_invocation_policy_mismatch_fails_before_callback() -> None:
    factor_set, selection, request = shell_case(constructed_shell_declaration())
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None
    stale = result.invocation.model_copy(update={"parameter_scales": (0.01,)})
    stale = stale.model_copy(
        update={
            "invocation_id": execution_content_id("invocation", stale, "invocation_id")
        }
    )

    def forbidden(_values: object) -> object:
        raise AssertionError("invalid invocation reached callback")

    with pytest.raises((ValidationError, ValueError)):
        _ = DeclaredNumpyBackend().execute(stale, forbidden)


def test_backend_has_no_topology_imports_or_variant_dispatch() -> None:
    tree = ast.parse(Path(backend_module.__file__ or "").read_text(encoding="utf-8"))
    imports = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        "stepped" in imported
        or imported in {"scansor.factor_models", "scansor.execution_models", "scipy"}
        for imported in imports
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "variant"
        for node in ast.walk(tree)
    )


def changed_evaluation(
    evaluation: FactorEvaluation, **updates: object
) -> FactorEvaluation:
    provisional = evaluation.model_copy(update=updates)
    return FactorEvaluation.model_validate(
        provisional.model_dump(mode="python")
        | {"evaluation_id": content_id("evaluation", provisional, "evaluation_id")}
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "type",
        "factor-set",
        "selection",
        "model",
        "values",
        "order",
        "dimension",
        "nonfinite",
    ],
)
def test_untrusted_callback_payloads_fail_closed(corruption: str) -> None:
    declaration = constructed_shell_declaration()
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None

    def callback(values: tuple[float, ...]) -> object:
        evaluation = evaluate_factors(
            factor_set, selection, _parameters(declaration, values)
        )
        if corruption == "type":
            return None
        if corruption == "factor-set":
            return changed_evaluation(
                evaluation, factor_set_id="factor-set." + "0" * 64
            )
        if corruption == "selection":
            return changed_evaluation(evaluation, selection_id="selection." + "0" * 64)
        if corruption == "model":
            return changed_evaluation(
                evaluation,
                model_id="model." + "0" * 64,
                parameters=evaluation.parameters.model_copy(
                    update={"model_id": "model." + "0" * 64}
                ),
            )
        if corruption == "values":
            return changed_evaluation(
                evaluation, parameters=_parameters(declaration, (0.021,))
            )
        if corruption == "order":
            return changed_evaluation(evaluation, parameter_order=("foreign-radius",))
        if corruption == "dimension":
            return evaluation.model_copy(update={"jacobian": ((-1.0, -1.0),) * 3})
        return evaluation.model_copy(update={"raw_residuals_m": (float("nan"),) * 3})

    response = DeclaredNumpyBackend().execute(result.invocation, callback)
    assert isinstance(response, BackendResponse)
    assert response.raw_code == "numeric-failure"


def test_accepted_trial_rank_loss_precedes_residual_convergence() -> None:
    declaration = constructed_shell_declaration()
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None
    calls = 0

    def callback(values: tuple[float, ...]) -> FactorEvaluation:
        nonlocal calls
        calls += 1
        evaluation = evaluate_factors(
            factor_set, selection, _parameters(declaration, values)
        )
        return (
            evaluation
            if calls == 1
            else changed_evaluation(evaluation, jacobian=((0.0,),) * 3)
        )

    response = DeclaredNumpyBackend().execute(result.invocation, callback)
    assert isinstance(response, BackendResponse)
    assert calls == 2
    assert response.raw_code == "rank-deficient"


def test_empty_rank_subsequence_with_zero_minimum_executes_full_vector() -> None:
    declaration = double_shell_declaration(rank_ids=(), required_rank=0)
    factor_set, selection, request = shell_case(declaration, double=True)
    preflight = preflight_factors(factor_set, selection, request.initial_parameters)
    assert preflight.observed_rank == preflight.expected_rank == 0
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.normalized_termination.raw_code == "residual-tolerance"
    assert result.final_parameters is not None
    np.testing.assert_allclose(
        result.final_parameters.values, (0.02, 0.005), atol=1e-15, rtol=0
    )


def test_converged_accepted_trial_avoids_unused_overflowing_scaling() -> None:
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0]["diagnostic_scale"] = 1e300
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None
    calls = 0

    def callback(values: tuple[float, ...]) -> FactorEvaluation:
        nonlocal calls
        calls += 1
        evaluation = evaluate_factors(
            factor_set, selection, _parameters(declaration, values)
        )
        return changed_evaluation(
            evaluation,
            raw_residuals_m=(0.001 if calls == 1 else 0.0,) * 3,
            jacobian=((-1e-300 if calls == 1 else -1e300,),) * 3,
        )

    with np.errstate(over="raise", invalid="raise"):
        response = DeclaredNumpyBackend().execute(result.invocation, callback)
    assert isinstance(response, BackendResponse)
    assert calls == 2
    assert response.raw_code == "residual-tolerance"


def test_overflowing_relative_coordinates_are_numeric_failure_not_stagnation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0]["diagnostic_scale"] = 1e-310
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.invocation is not None
    calls = 0

    def callback(values: tuple[float, ...]) -> FactorEvaluation:
        nonlocal calls
        calls += 1
        evaluation = evaluate_factors(
            factor_set, selection, _parameters(declaration, values)
        )
        return (
            evaluation
            if calls == 1
            else changed_evaluation(evaluation, raw_residuals_m=(-0.0001,) * 3)
        )

    monkeypatch.setattr(backend_module, "PROJECTED_GRADIENT_INFINITY_TOLERANCE", 0.0)
    with np.errstate(over="raise", invalid="raise"):
        response = DeclaredNumpyBackend().execute(result.invocation, callback)
    assert isinstance(response, BackendResponse)
    assert calls == 2
    assert response.raw_code == "numeric-failure"


def test_nonlinear_declared_radius_relationship_converges() -> None:
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0]["nominal"] = float(np.sqrt(0.02**2 + 0.01**2))
    record["parameters"][0]["lower"] = 0.011
    record["relationships"] = (
        {
            "kind": "right-triangle-leg",
            "relationship_id": "derived-radius",
            "hypotenuse": {"kind": "parameter", "parameter_id": "shell-radius"},
            "other_leg": {"kind": "literal", "value": 0.01},
        },
    )
    record["elements"][0]["primitive"]["radius"] = {
        "kind": "relationship",
        "relationship_id": "derived-radius",
    }
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    factor_set, selection, request = shell_case(declaration)
    result = execute(request, factor_set, selection, DeclaredNumpyBackend())
    assert result.normalized_termination.raw_code == "residual-tolerance"
    assert result.callback_count > 2
    assert result.final_parameters is not None
    np.testing.assert_allclose(
        result.final_parameters.values,
        (declaration.parameters[0].nominal,),
        atol=1e-12,
        rtol=0,
    )
    _ = replay_execution(result, request, factor_set, selection)
