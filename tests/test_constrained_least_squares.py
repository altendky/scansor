from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresFailure,
    ConstrainedLeastSquaresResult,
    solve_constrained_least_squares,
)


def circle(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.asarray([values @ values - 1.0]), 2.0 * values[None, :]


@pytest.mark.parametrize("initial", [[0.3, 0.7], [8.0, 1.0], [0.001, 0.002]])
def test_nonlinear_equalities_are_feasible_and_tangent_stationary(
    initial: list[float],
) -> None:
    target = np.asarray([2.0, 1.0])
    result = solve_constrained_least_squares(
        initial,
        lambda values: (values - target, np.eye(2)),
        circle,
        parameter_scales=[1.0, 1.0],
    )
    parameters = np.asarray(result.parameters)
    expected = target / np.linalg.norm(target)
    assert parameters == pytest.approx(expected, abs=1e-9)
    assert abs(parameters @ parameters - 1.0) <= 1e-11
    # The objective gradient is nonzero and normal to the manifold. Testing the
    # ordinary gradient instead of its tangent projection would reject this.
    assert np.linalg.norm(parameters - target) > 1.0
    assert result.constraint_rank == result.rank == result.tangent_dimension == 1
    assert result.projected_gradient_norm <= 1e-9
    assert result.constraint_violation <= 1e-11
    assert result.termination == "constrained-stationarity"
    assert all(
        a >= b - 2e-14
        for a, b in zip(
            result.objective_history, result.objective_history[1:], strict=False
        )
    )
    with pytest.raises(FrozenInstanceError):
        result.rank = 0  # pyright: ignore[reportAttributeAccessIssue]


def test_redundant_equalities_do_not_reduce_the_tangent_twice() -> None:
    def redundant(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        residual, jacobian = circle(values)
        return np.concatenate([residual, 2 * residual]), np.vstack(
            [jacobian, 2 * jacobian]
        )

    result = solve_constrained_least_squares(
        [0.6, 0.8],
        lambda values: (values - np.asarray([2.0, 1.0]), np.eye(2)),
        redundant,
        parameter_scales=[1.0, 1.0],
    )
    assert result.parameters == pytest.approx(
        np.asarray([2.0, 1.0]) / np.sqrt(5), abs=1e-9
    )
    assert result.constraint_rank == 1
    assert result.constraint_violation <= 1e-11


def test_inconsistent_equalities_are_not_success() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [0.0],
            lambda values: (values, np.eye(1)),
            lambda values: (np.asarray([values[0], values[0] - 1.0]), np.ones((2, 1))),
            parameter_scales=[1.0],
        )
    assert error.value.code == "infeasible-constraints"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.termination == "infeasible-constraints"
    assert error.value.diagnostics.constraint_violation > 1e-11


def test_singular_infeasible_start_fails_explicitly() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [0.0, 0.0],
            lambda values: (values, np.eye(2)),
            circle,
            parameter_scales=[1.0, 1.0],
        )
    assert error.value.code == "infeasible-constraints"


def test_unobservable_tangent_is_not_hidden_by_zero_objective() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [1.0, 0.0],
            lambda values: (np.asarray([values[0] - 1.0]), np.asarray([[1.0, 0.0]])),
            circle,
            parameter_scales=[1.0, 1.0],
        )
    assert error.value.code == "rank-deficient"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.tangent_dimension == 1
    assert error.value.diagnostics.rank == 0


def test_fully_constrained_problem_needs_no_observation_tangent_rank() -> None:
    result = solve_constrained_least_squares(
        [9.0, -3.0],
        lambda values: (values, np.eye(2)),
        lambda values: (values - np.asarray([1.0, 2.0]), np.eye(2)),
        parameter_scales=[1.0, 1.0],
    )
    assert result.parameters == (1.0, 2.0)
    assert result.tangent_dimension == result.projected_gradient_norm == 0
    assert result.constraint_rank == 2


def test_empty_equalities_reduce_to_unconstrained_least_squares() -> None:
    result = solve_constrained_least_squares(
        [0.0, 0.0],
        lambda values: (values - np.asarray([1.0, 2.0]), np.eye(2)),
        lambda _values: (np.empty(0), np.empty((0, 2))),
        parameter_scales=[1.0, 1.0],
    )
    assert result.parameters == (1.0, 2.0)
    assert result.constraint_rank == 0
    assert result.tangent_dimension == 2


def test_invalid_initial_geometry_is_not_repaired_silently() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [-1.0, 0.0],
            lambda values: (values, np.eye(2)),
            circle,
            parameter_scales=[1.0, 1.0],
            geometry_is_valid=lambda values: bool(values[0] > 0.0),
        )
    assert error.value.code == "invalid-initial-geometry"


def test_geometry_guard_does_not_claim_inequality_stationarity() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [1.0, 0.0],
            lambda values: (values - np.asarray([1.0, 1.0]), np.eye(2)),
            circle,
            parameter_scales=[1.0, 1.0],
            geometry_is_valid=lambda values: bool(values[1] <= 0.0),
        )
    assert error.value.code == "geometry-trial-exhaustion"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.projected_gradient_norm > 1e-9


def test_geometry_guard_can_reject_large_trials_without_blocking_solution() -> None:
    target = np.asarray([2.0, 1.0])
    result = solve_constrained_least_squares(
        [1.0, 0.0],
        lambda values: (values - target, np.eye(2)),
        circle,
        parameter_scales=[1.0, 1.0],
        geometry_is_valid=lambda values: bool(values[0] > 0.0 and values[1] < 0.8),
    )
    assert result.parameters == pytest.approx(target / np.linalg.norm(target), abs=1e-9)


def test_physical_parameter_residual_and_equality_scaling_are_invariant() -> None:
    outcomes: list[ConstrainedLeastSquaresResult] = []
    target = np.asarray([2.0, 1.0])
    for scales, residual_scale, equality_scale in [
        (np.asarray([1.0, 1.0]), 1.0, 1.0),
        (np.asarray([1e-7, 1e6]), 1e-4, 1e5),
    ]:

        def observations(
            values: np.ndarray,
            scales: np.ndarray = scales,
            residual_scale: float = residual_scale,
        ) -> tuple[np.ndarray, np.ndarray]:
            return residual_scale * (
                values / scales - target
            ), residual_scale * np.diag(1 / scales)

        def equalities(
            values: np.ndarray,
            scales: np.ndarray = scales,
            equality_scale: float = equality_scale,
        ) -> tuple[np.ndarray, np.ndarray]:
            residual, jacobian = circle(values / scales)
            return equality_scale * residual, equality_scale * jacobian / scales

        result = solve_constrained_least_squares(
            np.asarray([0.6, 0.8]) * scales,
            observations,
            equalities,
            parameter_scales=scales,
            residual_scale=residual_scale,
            equality_scale=[equality_scale],
        )
        assert np.asarray(result.parameters) / scales == pytest.approx(
            target / np.sqrt(5), abs=1e-9
        )
        outcomes.append(result)
    assert outcomes[0].condition == pytest.approx(outcomes[1].condition)
    assert outcomes[0].objective_history == pytest.approx(outcomes[1].objective_history)


def test_extra_roundoff_retraction_goal_does_not_override_feasibility_tolerance() -> (
    None
):
    result = solve_constrained_least_squares(
        [0.0, 0.0],
        lambda values: (values - [0.0, 2.0], np.eye(2)),
        lambda values: (
            np.asarray([values[0], 3e-15]),
            np.asarray([[1.0, 0.0], [0.0, 0.0]]),
        ),
        parameter_scales=[1.0, 1.0],
    )
    assert result.parameters == (0.0, 2.0)
    assert result.constraint_violation == 3e-15
    assert result.constraint_violation <= 1e-11


def test_iteration_limit_is_not_success() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [0.6, 0.8],
            lambda values: (values - np.asarray([2.0, 1.0]), np.eye(2)),
            circle,
            parameter_scales=[1.0, 1.0],
            max_iterations=0,
        )
    assert error.value.code == "iteration-limit"


@pytest.mark.parametrize("bad", ["nonfinite", "jacobian", "changing"])
@pytest.mark.parametrize("equality", [True, False])
def test_callback_rows_and_derivatives_are_validated(bad: str, equality: bool) -> None:
    calls = 0

    def malformed(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        nonlocal calls
        calls += 1
        if bad == "nonfinite":
            return np.asarray([np.nan]), np.ones((1, 2))
        if bad == "jacobian":
            return np.zeros(1), np.zeros((2, 1))
        if bad == "changing" and calls > 1:
            return np.zeros(3), np.zeros((3, 2))
        return circle(values) if equality else (values - [2.0, 1.0], np.eye(2))

    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [0.6, 0.8],
            malformed
            if not equality
            else lambda values: (values - [2.0, 1.0], np.eye(2)),
            malformed if equality else circle,
            parameter_scales=[1.0, 1.0],
        )
    assert error.value.code == "numeric-failure"


def test_wrong_equality_scale_count_is_rejected() -> None:
    with pytest.raises(ConstrainedLeastSquaresFailure) as error:
        _ = solve_constrained_least_squares(
            [0.6, 0.8],
            lambda values: (values, np.eye(2)),
            circle,
            parameter_scales=[1.0, 1.0],
            equality_scale=[1.0, 1.0],
        )
    assert error.value.code == "invalid-input"
