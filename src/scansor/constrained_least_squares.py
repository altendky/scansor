"""NumPy-only least squares on a caller-declared equality manifold.

Observation rows and equality rows remain fixed. Equalities are not weighted
observations: every accepted iterate is retracted to the equality manifold.
Success requires both feasibility and tangent first-order stationarity. This
local solver does not certify global optimality or handle inequalities; a
geometry predicate only rejects invalid trials and cannot establish constrained
stationarity at an inequality boundary.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
Evaluation = Callable[[Array], tuple[Array, Array]]
FailureCode = Literal[
    "invalid-input",
    "invalid-initial-geometry",
    "numeric-failure",
    "infeasible-constraints",
    "rank-deficient",
    "geometry-trial-exhaustion",
    "constraint-projection-exhaustion",
    "step-stagnation",
    "line-search-stagnation",
    "iteration-limit",
]
Termination = Literal["constrained-stationarity"] | FailureCode


@dataclass(frozen=True)
class ConstrainedLeastSquaresResult:
    """Scaled diagnostics and the last accepted physical parameters."""

    parameters: tuple[float, ...]
    objective_history: tuple[float, ...]
    iterations: int
    evaluations: int
    constraint_evaluations: int
    rank: int
    constraint_rank: int
    tangent_dimension: int
    condition: float
    projected_gradient_norm: float
    constraint_violation: float
    termination: Termination


class ConstrainedLeastSquaresFailure(ValueError):
    """An unsuccessful iterate is never returned as converged geometry."""

    code: FailureCode
    diagnostics: ConstrainedLeastSquaresResult | None

    def __init__(
        self,
        code: FailureCode,
        message: str,
        diagnostics: ConstrainedLeastSquaresResult | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.diagnostics = diagnostics


def solve_constrained_least_squares(
    initial: Sequence[float] | Array,
    residual_and_jacobian: Evaluation,
    equality_and_jacobian: Evaluation,
    *,
    parameter_scales: Sequence[float] | Array,
    residual_scale: float = 1.0,
    equality_scale: float | Sequence[float] | Array = 1.0,
    geometry_is_valid: Callable[[Array], bool] | None = None,
    gradient_tolerance: float = 1e-9,
    feasibility_tolerance: float = 1e-11,
    rank_tolerance: float = 1e-12,
    max_iterations: int = 100,
    max_trials: int = 32,
    max_projection_iterations: int = 32,
) -> ConstrainedLeastSquaresResult:
    """Minimize ``0.5 * ||residual / residual_scale||**2`` with exact equalities.

    Both callbacks return physical-coordinate residuals and analytic Jacobians.
    Steps are expressed in units of ``parameter_scales``. ``equality_scale`` is
    a positive scalar or one positive physical scale per fixed equality row;
    feasibility is measured after division by these scales. Redundant equality
    rows are supported by SVD, but observations must identify every free tangent
    direction. An infeasible start is projected before fitting observations.
    """
    try:
        current = np.asarray(initial, dtype=np.float64).copy()
        scales = np.asarray(parameter_scales, dtype=np.float64)
        equality_scales = np.asarray(equality_scale, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as error:
        raise ConstrainedLeastSquaresFailure(
            "invalid-input", "parameters and scales must be numeric"
        ) from error
    if (
        current.ndim != 1
        or current.size == 0
        or scales.shape != current.shape
        or not bool(np.isfinite(current).all())
        or not bool(np.isfinite(scales).all())
        or bool(np.any(scales <= 0.0))
        or equality_scales.ndim > 1
        or not bool(np.isfinite(equality_scales).all())
        or bool(np.any(equality_scales <= 0.0))
        or not math.isfinite(residual_scale)
        or residual_scale <= 0.0
        or not math.isfinite(gradient_tolerance)
        or gradient_tolerance <= 0.0
        or not math.isfinite(feasibility_tolerance)
        or feasibility_tolerance <= 0.0
        or not 0.0 < rank_tolerance < 1.0
        or max_iterations < 0
        or max_trials < 1
        or max_projection_iterations < 1
    ):
        raise ConstrainedLeastSquaresFailure(
            "invalid-input", "invalid parameters, scales, tolerances, or limits"
        )
    if geometry_is_valid is not None and not geometry_is_valid(current.copy()):
        raise ConstrainedLeastSquaresFailure(
            "invalid-initial-geometry", "initial parameters violate geometry validity"
        )

    history: list[float] = []
    evaluations = 0
    constraint_evaluations = 0
    iterations = 0
    observation_count: int | None = None
    equality_count: int | None = None
    rank = constraint_rank = 0
    tangent_dimension = current.size
    condition = projected_norm = violation = math.inf

    def result(code: Termination) -> ConstrainedLeastSquaresResult:
        return ConstrainedLeastSquaresResult(
            tuple(float(value) for value in current),
            tuple(history),
            iterations,
            evaluations,
            constraint_evaluations,
            rank,
            constraint_rank,
            tangent_dimension,
            condition,
            projected_norm,
            violation,
            code,
        )

    def failure(code: FailureCode, message: str) -> ConstrainedLeastSquaresFailure:
        return ConstrainedLeastSquaresFailure(code, message, result(code))

    def callback(
        values: Array, evaluate: Evaluation, *, equality: bool
    ) -> tuple[Array, Array]:
        nonlocal evaluations, constraint_evaluations
        nonlocal observation_count, equality_count
        if equality:
            constraint_evaluations += 1
        else:
            evaluations += 1
        try:
            raw_residual, raw_jacobian = evaluate(values.copy())
            residual = np.asarray(raw_residual, dtype=np.float64)
            jacobian = np.asarray(raw_jacobian, dtype=np.float64)
        except (TypeError, ValueError, FloatingPointError, OverflowError) as error:
            raise failure(
                "numeric-failure", "residual/Jacobian callback failed"
            ) from error
        expected_count = equality_count if equality else observation_count
        if (
            residual.ndim != 1
            or (not equality and residual.size == 0)
            or jacobian.shape != (residual.size, current.size)
            or not bool(np.isfinite(residual).all())
            or not bool(np.isfinite(jacobian).all())
            or (expected_count is not None and residual.size != expected_count)
        ):
            raise failure(
                "numeric-failure",
                "nonfinite or inconsistent fixed residual/Jacobian rows",
            )
        if equality:
            if equality_scales.ndim == 1 and equality_scales.shape != residual.shape:
                raise failure("invalid-input", "one equality scale is required per row")
            equality_count = residual.size
            divisor = equality_scales
        else:
            observation_count = residual.size
            divisor = np.asarray(residual_scale)
        with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
            residual = residual / divisor
            jacobian = (
                jacobian
                * scales[None, :]
                / (divisor[:, None] if divisor.ndim == 1 else divisor)
            )
        if not bool(np.isfinite(residual).all() and np.isfinite(jacobian).all()):
            raise failure("numeric-failure", "scaled residual/Jacobian overflowed")
        return residual, jacobian

    def equality_svd(jacobian: Array) -> tuple[Array, Array, Array, int, Array]:
        try:
            # A complete right basis is required for the nullspace. Avoid a
            # quadratic full left basis when there are more rows than columns.
            left, singular, right = np.linalg.svd(
                jacobian, full_matrices=jacobian.shape[0] < current.size
            )
        except np.linalg.LinAlgError as error:
            raise failure("numeric-failure", "equality SVD failed") from error
        largest = float(singular[0]) if singular.size else 0.0
        count = int(np.count_nonzero(singular > largest * rank_tolerance))
        return left, singular, right, count, right[count:].T

    def feasibility(residual: Array) -> float:
        return float(np.max(np.abs(residual))) if residual.size else 0.0

    # A normal objective gradient can turn feasible-but-inexact retraction
    # error into apparent objective improvement and prevent tangent convergence.
    # Retract close to roundoff, while the public feasibility tolerance remains
    # the acceptance certificate rather than a penalty/objective tradeoff.
    projection_tolerance = min(feasibility_tolerance, 1e-15)

    def project(values: Array) -> tuple[Array, Array, Array] | None:
        """Newton least-norm retraction with a constraint-only line search."""
        nonlocal projection_guard_rejections
        if geometry_is_valid is not None and not geometry_is_valid(values.copy()):
            projection_guard_rejections += 1
            return None
        equality, derivative = callback(values, equality_and_jacobian, equality=True)
        for _ in range(max_projection_iterations):
            if feasibility(equality) <= projection_tolerance:
                return values, equality, derivative
            left, singular, right, count, _ = equality_svd(derivative)
            if count == 0:
                return (
                    (values, equality, derivative)
                    if feasibility(equality) <= feasibility_tolerance
                    else None
                )
            correction = -(
                right[:count].T @ ((left[:, :count].T @ equality) / singular[:count])
            )
            old_norm = float(np.linalg.norm(equality))
            accepted = False
            for trial_index in range(max_trials):
                fraction = 0.5**trial_index
                trial = values + fraction * scales * correction
                if not bool(np.isfinite(trial).all()):
                    continue
                if np.array_equal(trial, values):
                    break
                if geometry_is_valid is not None and not geometry_is_valid(
                    trial.copy()
                ):
                    projection_guard_rejections += 1
                    continue
                trial_equality, trial_derivative = callback(
                    trial, equality_and_jacobian, equality=True
                )
                if feasibility(trial_equality) <= projection_tolerance or (
                    float(np.linalg.norm(trial_equality))
                    < (1 - 1e-4 * fraction) * old_norm
                ):
                    values, equality, derivative = (
                        trial,
                        trial_equality,
                        trial_derivative,
                    )
                    accepted = True
                    break
            if not accepted:
                # The requested certificate may be attainable when an extra
                # roundoff-level refinement is not representable. Never reject
                # such an iterate merely for missing our tighter internal goal.
                return (
                    (values, equality, derivative)
                    if feasibility(equality) <= feasibility_tolerance
                    else None
                )
        if feasibility(equality) <= feasibility_tolerance:
            return values, equality, derivative
        return None

    projection_guard_rejections = 0
    initial_equality, _ = callback(current, equality_and_jacobian, equality=True)
    violation = feasibility(initial_equality)
    projected = project(current.copy())
    if projected is None:
        raise failure(
            "infeasible-constraints",
            "could not project initial parameters to the declared equalities",
        )
    current, equality, equality_jacobian = projected
    residual, jacobian = callback(current, residual_and_jacobian, equality=False)
    objective = 0.5 * float(residual @ residual)
    if not math.isfinite(objective):
        raise failure("numeric-failure", "nonfinite objective")
    history.append(objective)
    damping = 0.0
    while True:
        _, _, _, constraint_rank, tangent = equality_svd(equality_jacobian)
        tangent_dimension = tangent.shape[1]
        tangent_jacobian = jacobian @ tangent
        gradient = jacobian.T @ residual
        projected_gradient = tangent @ (tangent.T @ gradient)
        projected_norm = float(np.linalg.norm(projected_gradient, ord=np.inf))
        violation = feasibility(equality)
        if not bool(np.isfinite(gradient).all()) or not math.isfinite(projected_norm):
            raise failure("numeric-failure", "nonfinite gradient")
        try:
            left, singular, right = np.linalg.svd(tangent_jacobian, full_matrices=False)
        except np.linalg.LinAlgError as error:
            raise failure("numeric-failure", "tangent SVD failed") from error
        largest = float(singular[0]) if singular.size else 0.0
        rank = int(np.count_nonzero(singular > largest * rank_tolerance))
        condition = (
            largest / float(singular[-1])
            if rank == tangent_dimension and singular.size
            else (1.0 if tangent_dimension == 0 else math.inf)
        )
        if rank < tangent_dimension:
            raise failure(
                "rank-deficient",
                f"observation tangent rank {rank} is below {tangent_dimension}",
            )
        if violation <= feasibility_tolerance and projected_norm <= gradient_tolerance:
            return result("constrained-stationarity")
        if iterations >= max_iterations:
            raise failure(
                "iteration-limit", "feasible tangent stationarity not reached"
            )

        accepted: tuple[Array, Array, Array, Array, Array, float] | None = None
        valid_trials = projection_failures = 0
        projection_guard_rejections = 0
        for trial_index in range(max_trials):
            coefficient = singular / (singular**2 + damping)
            direction = -(tangent @ (right.T @ (coefficient * (left.T @ residual))))
            if trial_index >= max_trials // 2 or float(gradient @ direction) >= 0.0:
                direction = -projected_gradient / max(largest**2 + damping, 1.0)
            if not bool(np.isfinite(direction).all()):
                raise failure("numeric-failure", "nonfinite tangent step")
            trial = current + scales * direction
            derivative = float(gradient @ direction)
            if derivative >= 0.0 or np.array_equal(trial, current):
                damping = max(damping * 10.0, largest**2 * 1e-6, 1e-12)
                continue
            retracted = project(trial)
            if retracted is None:
                projection_failures += 1
                damping = max(damping * 10.0, largest**2 * 1e-6, 1e-12)
                continue
            trial, trial_equality, trial_equality_jacobian = retracted
            trial_residual, trial_jacobian = callback(
                trial, residual_and_jacobian, equality=False
            )
            trial_objective = 0.5 * float(trial_residual @ trial_residual)
            if not math.isfinite(trial_objective):
                raise failure("numeric-failure", "nonfinite trial objective")
            valid_trials += 1
            _, _, _, _, trial_tangent = equality_svd(trial_equality_jacobian)
            trial_gradient = trial_jacobian.T @ trial_residual
            trial_projected = trial_tangent @ (trial_tangent.T @ trial_gradient)
            trial_projected_norm = float(np.linalg.norm(trial_projected, ord=np.inf))
            stationary = trial_projected_norm <= gradient_tolerance
            decrease = objective - trial_objective
            roundoff = 8 * np.finfo(float).eps * max(objective, trial_objective)
            if (decrease > 0.0 and decrease >= -1e-4 * derivative) or (
                decrease >= -roundoff
                and (
                    stationary
                    or (
                        -derivative <= roundoff
                        and trial_projected_norm < 0.75 * projected_norm
                    )
                )
            ):
                accepted = (
                    trial,
                    trial_equality,
                    trial_equality_jacobian,
                    trial_residual,
                    trial_jacobian,
                    trial_objective,
                )
                predicted = -derivative - 0.5 * float(
                    (jacobian @ direction) @ (jacobian @ direction)
                )
                ratio = decrease / predicted if predicted > 0.0 else 0.0
                if decrease <= roundoff:
                    # Objective ratios at roundoff are not informative. Keep
                    # the damping that just improved tangent stationarity.
                    pass
                elif ratio < 0.25:
                    damping = max(damping * 10.0, largest**2 * 0.1, 1e-12)
                elif ratio > 0.75:
                    damping *= 0.1
                break
            damping = max(damping * 10.0, largest**2 * 1e-6, 1e-12)
        if accepted is None:
            if valid_trials == 0 and projection_guard_rejections:
                raise failure(
                    "geometry-trial-exhaustion",
                    "moving trials violate geometry validity; inequality stationarity is not established",
                )
            if valid_trials == 0 and projection_failures:
                raise failure(
                    "constraint-projection-exhaustion",
                    "moving trials could not be retracted to the equality manifold",
                )
            if valid_trials == 0:
                raise failure(
                    "step-stagnation", "no representable descending tangent step"
                )
            raise failure(
                "line-search-stagnation",
                "feasible trials did not decrease objective at a nonstationary point",
            )
        current, equality, equality_jacobian, residual, jacobian, objective = accepted
        iterations += 1
        history.append(objective)
