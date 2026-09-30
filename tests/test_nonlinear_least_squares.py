from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from scansor.nonlinear_least_squares import (
    LeastSquaresFailure,
    LeastSquaresResult,
    solve_least_squares,
)


@pytest.mark.parametrize("noise", [0.0, 0.15])
def test_linear_exact_and_noisy_problem(noise: float) -> None:
    matrix = np.asarray([[1.0, 0.0], [1.0, 1.0], [1.0, 2.0], [1.0, 3.0]])
    observations = matrix @ np.asarray([2.0, -0.5])
    observations += noise * np.asarray([1.0, -1.0, -1.0, 1.0])
    result = solve_least_squares(
        [0.0, 0.0],
        lambda values: (matrix @ values - observations, matrix),
        parameter_scales=[1.0, 1.0],
        residual_scale=1.0,
    )
    expected = np.linalg.lstsq(matrix, observations, rcond=None)[0]
    assert result.parameters == pytest.approx(expected, abs=1e-12)
    assert result.projected_gradient_norm <= 1e-10
    assert result.rank == 2
    assert result.termination == "projected-gradient"
    assert all(
        a > b
        for a, b in zip(
            result.objective_history, result.objective_history[1:], strict=False
        )
    )
    with pytest.raises(FrozenInstanceError):
        result.rank = 0  # pyright: ignore[reportAttributeAccessIssue]


def test_physical_parameter_and_residual_scaling_are_invariant() -> None:
    matrix = np.asarray([[1.0, 2.0], [2.0, -1.0], [-1.0, 3.0]])
    target = np.asarray([3.0, -2.0])
    results: list[LeastSquaresResult] = []
    for scales, residual_scale in [
        (np.asarray([1.0, 1.0]), 1.0),
        (np.asarray([1e-7, 1e6]), 1e-4),
    ]:
        result = solve_least_squares(
            np.asarray([0.0, 0.0]) * scales,
            lambda values, scales=scales, residual_scale=residual_scale: (
                residual_scale * (matrix @ (values / scales) - matrix @ target),
                residual_scale * matrix / scales,
            ),
            parameter_scales=scales,
            residual_scale=residual_scale,
        )
        assert np.asarray(result.parameters) / scales == pytest.approx(target)
        results.append(result)
    assert results[0].condition == pytest.approx(results[1].condition)
    assert results[0].objective_history == pytest.approx(results[1].objective_history)


def test_box_bound_optimum_reports_projected_stationarity() -> None:
    result = solve_least_squares(
        [0.0, 0.0],
        lambda values: (values - np.asarray([3.0, -2.0]), np.eye(2)),
        parameter_scales=[1.0, 1.0],
        residual_scale=1.0,
        lower_bounds=[0.0, -1.0],
        upper_bounds=[1.0, 1.0],
    )
    assert result.parameters == (1.0, -1.0)
    assert result.projected_gradient_norm == 0.0
    assert result.objective_history[-1] == pytest.approx(2.5)


def test_blocked_gauss_newton_direction_recomputes_free_variables() -> None:
    # At x=0, GN proposes x<0 even though the gradient does not make x active.
    # Eliminating the outward component and recomputing y is necessary.
    matrix = np.asarray([[1.0, 2.0], [0.0, 1.0]])
    observations = matrix @ np.asarray([-1.0, 1.0])
    result = solve_least_squares(
        [0.0, 0.0],
        lambda values: (matrix @ values - observations, matrix),
        parameter_scales=[1.0, 1.0],
        residual_scale=1.0,
        lower_bounds=[0.0, -np.inf],
    )
    assert result.parameters == pytest.approx([0.0, 0.6])
    assert result.projected_gradient_norm <= 1e-10


def test_nonlinear_globalization_and_damping() -> None:
    result = solve_least_squares(
        [0.05],
        lambda values: (
            np.asarray([values[0] ** 2 - 4.0]),
            np.asarray([[2.0 * values[0]]]),
        ),
        parameter_scales=[1.0],
        residual_scale=1.0,
    )
    assert result.parameters == pytest.approx([2.0], abs=1e-10)
    assert result.evaluations > result.iterations + 1


def test_curved_valley_converges_to_stationary_solution() -> None:
    result = solve_least_squares(
        [-1.2, 1.0],
        lambda values: (
            np.asarray([10.0 * (values[1] - values[0] ** 2), 1.0 - values[0]]),
            np.asarray([[-20.0 * values[0], 10.0], [-1.0, 0.0]]),
        ),
        parameter_scales=[1.0, 1.0],
        residual_scale=1.0,
    )
    assert result.parameters == pytest.approx([1.0, 1.0], abs=1e-9)
    assert result.projected_gradient_norm <= 1e-10


def test_weighted_noisy_nonlinear_observations_remain_fixed() -> None:
    times = np.linspace(0.0, 2.0, 30)
    observations = 3.0 * np.exp(-0.7 * times) + 0.01 * np.sin(17.0 * times)
    square_root_weights = np.sqrt(np.linspace(0.5, 2.0, times.size))

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        exponential = np.exp(values[1] * times)
        return (
            square_root_weights * (values[0] * exponential - observations),
            square_root_weights[:, None]
            * np.column_stack([exponential, values[0] * times * exponential]),
        )

    result = solve_least_squares(
        [1.0, -0.2],
        evaluate,
        parameter_scales=[3.0, 1.0],
        residual_scale=3.0,
    )
    assert result.parameters == pytest.approx([3.0, -0.7], abs=0.01)
    residual, jacobian = evaluate(np.asarray(result.parameters))
    independently_scaled_gradient = (jacobian * [3.0, 1.0]).T @ residual / 9.0
    assert np.max(np.abs(independently_scaled_gradient)) <= 1e-10
    assert result.objective_history[-1] > 0.0


def test_geometry_guard_can_reject_trial_without_losing_valid_solution() -> None:
    result = solve_least_squares(
        [0.05],
        lambda values: (
            np.asarray([values[0] ** 2 - 4.0]),
            np.asarray([[2.0 * values[0]]]),
        ),
        parameter_scales=[1.0],
        residual_scale=1.0,
        geometry_is_valid=lambda values: bool(0.0 < values[0] <= 3.0),
    )
    assert result.parameters == pytest.approx([2.0], abs=1e-9)


def test_coordinate_offset_does_not_enlarge_stationarity_tolerance() -> None:
    offset = 1e8
    result = solve_least_squares(
        [offset],
        lambda values: (values - (offset + 2.0), np.eye(1)),
        parameter_scales=[1.0],
        residual_scale=1.0,
    )
    assert result.parameters == (offset + 2.0,)
    assert result.projected_gradient_norm == 0.0


def test_invalid_initial_geometry() -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [-1.0],
            lambda values: (values, np.eye(1)),
            parameter_scales=[1.0],
            residual_scale=1.0,
            geometry_is_valid=lambda values: bool(values[0] >= 0.0),
        )
    assert error.value.code == "invalid-initial-geometry"


def test_rank_diagnosis_is_not_hidden_by_damping_or_zero_residual() -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0, 0.0],
            lambda values: (np.asarray([values.sum()]), np.asarray([[1.0, 1.0]])),
            parameter_scales=[1.0, 1.0],
            residual_scale=1.0,
        )
    assert error.value.code == "rank-deficient"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.rank == 1


def test_iteration_limit_is_not_success() -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0],
            lambda values: (values - 1.0, np.eye(1)),
            parameter_scales=[1.0],
            residual_scale=1.0,
            max_iterations=0,
        )
    assert error.value.code == "iteration-limit"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.projected_gradient_norm == 1.0


def test_nonstationary_stagnation_is_not_success() -> None:
    # Deliberately inconsistent callback: no descending trial can improve it.
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0],
            lambda _values: (np.asarray([1.0]), np.asarray([[1.0]])),
            parameter_scales=[1.0],
            residual_scale=1.0,
        )
    assert error.value.code == "line-search-stagnation"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.projected_gradient_norm == 1.0


def test_geometry_guard_exhaustion_is_not_nonlinear_constrained_optimality() -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0],
            lambda values: (values - 1.0, np.eye(1)),
            parameter_scales=[1.0],
            residual_scale=1.0,
            geometry_is_valid=lambda values: bool(values[0] <= 0.0),
        )
    assert error.value.code == "geometry-trial-exhaustion"
    assert error.value.diagnostics is not None
    assert error.value.diagnostics.projected_gradient_norm == 1.0


def test_fixed_box_parameter_does_not_require_unobservable_column() -> None:
    result = solve_least_squares(
        [2.0, 0.0],
        lambda values: (np.asarray([values[1] - 3.0]), np.asarray([[0.0, 1.0]])),
        parameter_scales=[1.0, 1.0],
        residual_scale=1.0,
        lower_bounds=[2.0, -np.inf],
        upper_bounds=[2.0, np.inf],
    )
    assert result.parameters == (2.0, 3.0)
    assert result.rank == 1


def test_changed_residual_rows_are_rejected() -> None:
    def evaluation(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        rows = 1 if values[0] == 0.0 else 2
        return np.full(rows, values[0] - 1.0), np.ones((rows, 1))

    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0], evaluation, parameter_scales=[1.0], residual_scale=1.0
        )
    assert error.value.code == "numeric-failure"


def test_nonfinite_residual_is_explicit_numeric_failure() -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0],
            lambda _values: (np.asarray([np.nan]), np.eye(1)),
            parameter_scales=[1.0],
            residual_scale=1.0,
        )
    assert error.value.code == "numeric-failure"


def test_physical_trial_overflow_is_rejected_before_geometry_callback() -> None:
    guarded_values: list[float] = []

    def valid(values: np.ndarray) -> bool:
        guarded_values.append(float(values[0]))
        assert np.isfinite(values).all()
        return True

    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [1e308],
            lambda values: (values * 1e-308 - 2.0, np.asarray([[1e-308]])),
            parameter_scales=[1e308],
            residual_scale=1.0,
            geometry_is_valid=valid,
        )
    assert error.value.code == "numeric-failure"
    assert guarded_values == [1e308]


@pytest.mark.parametrize("scales", [[0.0], [float("nan")], [1.0, 1.0]])
def test_invalid_scales_are_rejected(scales: list[float]) -> None:
    with pytest.raises(LeastSquaresFailure) as error:
        _ = solve_least_squares(
            [0.0],
            lambda values: (values, np.eye(1)),
            parameter_scales=scales,
            residual_scale=1.0,
        )
    assert error.value.code == "invalid-input"
