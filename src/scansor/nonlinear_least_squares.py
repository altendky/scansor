"""Provisional, NumPy-only nonlinear least squares with explicit diagnostics.

The caller owns observation membership, weights, geometric parameterization,
and physical scales. Residual rows must remain fixed; weights must already be
applied to both residuals and their analytic Jacobian. No robust loss, trimming,
or change of observations is performed here. Box constraints participate in
the step computation. A geometry predicate is only a trial-rejection guard,
not a nonlinear constraint optimizer or a constrained stationarity certificate.
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
    "rank-deficient",
    "geometry-trial-exhaustion",
    "step-stagnation",
    "line-search-stagnation",
    "iteration-limit",
]


@dataclass(frozen=True)
class LeastSquaresResult:
    """Immutable last iterate, including diagnostics on unsuccessful solves."""

    parameters: tuple[float, ...]
    objective_history: tuple[float, ...]
    iterations: int
    evaluations: int
    rank: int
    condition: float
    projected_gradient_norm: float
    termination: Literal["projected-gradient"] | FailureCode


class LeastSquaresFailure(ValueError):
    """Failure is never returned as successful convergence."""

    code: FailureCode
    diagnostics: LeastSquaresResult | None

    def __init__(
        self,
        code: FailureCode,
        message: str,
        diagnostics: LeastSquaresResult | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.diagnostics = diagnostics


def _projected_gradient(
    gradient: Array, values: Array, lower: Array, upper: Array
) -> Array:
    projected = gradient.copy()
    projected[(values == lower) & (gradient > 0.0)] = 0.0
    projected[(values == upper) & (gradient < 0.0)] = 0.0
    projected[lower == upper] = 0.0
    return projected


def _svd_direction(
    jacobian: Array,
    residual: Array,
    gradient: Array,
    values: Array,
    lower: Array,
    upper: Array,
    damping: float,
) -> Array:
    """Recompute the free-variable problem when a box face blocks a direction."""
    free = (lower != upper) & ~(
        ((values == lower) & (gradient > 0.0)) | ((values == upper) & (gradient < 0.0))
    )
    while True:
        direction = np.zeros_like(values)
        if not bool(np.any(free)):
            return direction
        left, singular, right = np.linalg.svd(jacobian[:, free], full_matrices=False)
        # A rank diagnosis is performed separately on the undamped Jacobian.
        # Damping must not disguise an unobservable problem.
        denominator = singular * singular + damping
        coefficient = np.divide(
            singular,
            denominator,
            out=np.zeros_like(singular),
            where=denominator > 0.0,
        )
        direction[free] = -(right.T @ (coefficient * (left.T @ residual)))
        blocked = free & (
            ((values == lower) & (direction < 0.0))
            | ((values == upper) & (direction > 0.0))
        )
        if not bool(np.any(blocked)):
            return direction
        free[blocked] = False


def solve_least_squares(
    initial: Sequence[float] | Array,
    residual_and_jacobian: Evaluation,
    *,
    parameter_scales: Sequence[float] | Array,
    residual_scale: float,
    lower_bounds: Sequence[float] | Array | None = None,
    upper_bounds: Sequence[float] | Array | None = None,
    geometry_is_valid: Callable[[Array], bool] | None = None,
    gradient_tolerance: float = 1e-10,
    rank_tolerance: float = 1e-12,
    max_iterations: int = 100,
    max_trials: int = 32,
) -> LeastSquaresResult:
    """Minimize ``0.5 * ||weighted_residual / residual_scale||**2``.

    Parameter increments are scaled by caller-supplied physical scales, not by
    absolute parameter values. Thus a coordinate translation does not itself
    enlarge a step or a convergence tolerance. Success requires scaled,
    box-projected first-order stationarity. The rank threshold is relative to
    the largest scaled singular value, independently of roundoff safeguards.

    Geometry predicates must be pure and return a boolean; a false initial
    predicate is an error. A geometrically valid starting point at a nonlinear
    boundary is not promised a constrained solution: only box constraints have
    feasibility-aware steps here.
    """
    try:
        current = np.asarray(initial, dtype=np.float64).copy()
        scales = np.asarray(parameter_scales, dtype=np.float64)
        lower = (
            np.full(current.shape, -np.inf)
            if lower_bounds is None
            else np.asarray(lower_bounds, dtype=np.float64)
        )
        upper = (
            np.full(current.shape, np.inf)
            if upper_bounds is None
            else np.asarray(upper_bounds, dtype=np.float64)
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise LeastSquaresFailure(
            "invalid-input", "values must be numeric vectors"
        ) from error
    if (
        current.ndim != 1
        or current.size == 0
        or scales.shape != current.shape
        or lower.shape != current.shape
        or upper.shape != current.shape
        or not bool(np.isfinite(current).all())
        or not bool(np.isfinite(scales).all())
        or bool(np.any(scales <= 0.0))
        or bool(np.isnan(lower).any())
        or bool(np.isnan(upper).any())
        or bool(np.any(lower > upper))
        or bool(np.any(current < lower) or np.any(current > upper))
        or not math.isfinite(residual_scale)
        or residual_scale <= 0.0
        or not math.isfinite(gradient_tolerance)
        or gradient_tolerance <= 0.0
        or not 0.0 < rank_tolerance < 1.0
        or max_iterations < 0
        or max_trials < 1
    ):
        raise LeastSquaresFailure("invalid-input", "invalid values, bounds, or scales")
    if geometry_is_valid is not None and not geometry_is_valid(current.copy()):
        raise LeastSquaresFailure(
            "invalid-initial-geometry", "initial parameters violate geometry validity"
        )

    history: list[float] = []
    evaluations = 0
    accepted_iterations = 0
    row_count: int | None = None
    rank = 0
    condition = math.inf
    projected_norm = math.inf

    def result(code: Literal["projected-gradient"] | FailureCode) -> LeastSquaresResult:
        return LeastSquaresResult(
            tuple(float(value) for value in current),
            tuple(history),
            accepted_iterations,
            evaluations,
            rank,
            condition,
            projected_norm,
            code,
        )

    def failure(code: FailureCode, message: str) -> LeastSquaresFailure:
        return LeastSquaresFailure(code, message, result(code))

    def evaluate(values: Array) -> tuple[Array, Array, float]:
        nonlocal evaluations, row_count
        evaluations += 1
        try:
            raw_residual, raw_jacobian = residual_and_jacobian(values.copy())
            residual = np.asarray(raw_residual, dtype=np.float64) / residual_scale
            jacobian = (
                np.asarray(raw_jacobian, dtype=np.float64)
                * scales[np.newaxis, :]
                / residual_scale
            )
        except (TypeError, ValueError, FloatingPointError, OverflowError) as error:
            raise failure(
                "numeric-failure", "residual/Jacobian callback failed"
            ) from error
        if (
            residual.ndim != 1
            or residual.size == 0
            or jacobian.shape != (residual.size, current.size)
            or not bool(np.isfinite(residual).all())
            or not bool(np.isfinite(jacobian).all())
            or (row_count is not None and residual.size != row_count)
        ):
            raise failure(
                "numeric-failure",
                "nonfinite or inconsistent fixed residual/Jacobian rows",
            )
        row_count = residual.size
        objective = 0.5 * float(residual @ residual)
        if not math.isfinite(objective):
            raise failure("numeric-failure", "nonfinite objective")
        return residual, jacobian, objective

    residual, jacobian, objective = evaluate(current)
    history.append(objective)
    damping = 0.0
    while True:
        gradient = jacobian.T @ residual
        projected = _projected_gradient(gradient, current, lower, upper)
        projected_norm = float(np.linalg.norm(projected, ord=np.inf))
        if not bool(np.isfinite(gradient).all()) or not math.isfinite(projected_norm):
            raise failure("numeric-failure", "nonfinite gradient")
        try:
            singular = np.linalg.svd(jacobian[:, lower != upper], compute_uv=False)
        except np.linalg.LinAlgError as error:
            raise failure("numeric-failure", "scaled SVD failed") from error
        if not bool(np.isfinite(singular).all()):
            raise failure("numeric-failure", "nonfinite scaled singular values")
        largest = float(singular[0]) if singular.size else 0.0
        rank = int(np.count_nonzero(singular > largest * rank_tolerance))
        required_rank = int(np.count_nonzero(lower != upper))
        condition = (
            largest / float(singular[-1])
            if rank == required_rank and singular.size
            else (1.0 if required_rank == 0 else math.inf)
        )
        if rank < required_rank:
            raise failure(
                "rank-deficient",
                f"scaled Jacobian rank {rank} is below {required_rank}",
            )
        if projected_norm <= gradient_tolerance:
            return result("projected-gradient")
        if accepted_iterations >= max_iterations:
            raise failure("iteration-limit", "first-order stationarity not reached")

        valid_trials = 0
        geometry_rejections = 0
        accepted: tuple[Array, Array, Array, float] | None = None
        for trial_index in range(max_trials):
            try:
                direction = _svd_direction(
                    jacobian, residual, gradient, current, lower, upper, damping
                )
            except np.linalg.LinAlgError as error:
                raise failure("numeric-failure", "step SVD failed") from error
            # A fresh projected-gradient direction prevents a blocked or poor
            # Gauss-Newton direction from being the sole search direction.
            if trial_index >= max_trials // 2 or float(gradient @ direction) >= 0.0:
                direction = -projected / max(largest * largest + damping, 1.0)
            if not bool(np.isfinite(direction).all()):
                raise failure("numeric-failure", "nonfinite scaled step")
            with np.errstate(over="ignore", invalid="ignore"):
                physical_direction = direction * scales
                trial = np.clip(current + physical_direction, lower, upper)
                actual_step = (trial - current) / scales
            if not all(
                bool(np.isfinite(values).all())
                for values in (physical_direction, trial, actual_step)
            ):
                raise failure("numeric-failure", "physical step or trial overflowed")
            derivative = float(gradient @ actual_step)
            if derivative >= 0.0 or bool(np.array_equal(trial, current)):
                damping = max(damping * 10.0, largest * largest * 1e-6, 1e-12)
                continue
            if geometry_is_valid is not None and not geometry_is_valid(trial.copy()):
                geometry_rejections += 1
                damping = max(damping * 10.0, largest * largest * 1e-6, 1e-12)
                continue
            valid_trials += 1
            trial_residual, trial_jacobian, trial_objective = evaluate(trial)
            predicted = -derivative - 0.5 * float(
                (jacobian @ actual_step) @ (jacobian @ actual_step)
            )
            decrease = objective - trial_objective
            trial_gradient = trial_jacobian.T @ trial_residual
            trial_projected = _projected_gradient(trial_gradient, trial, lower, upper)
            trial_stationary = (
                float(np.linalg.norm(trial_projected, ord=np.inf)) <= gradient_tolerance
            )
            objective_roundoff = (
                8.0 * np.finfo(np.float64).eps * max(objective, trial_objective)
            )
            # An objective-neutral last refinement may establish stationarity
            # even when its predicted improvement is smaller than sum-of-squares
            # roundoff. This exception requires the newly evaluated gradient to
            # meet tolerance; a small step/objective change alone never suffices.
            if (decrease > 0.0 and decrease >= -1e-4 * derivative) or (
                trial_stationary and decrease >= -objective_roundoff
            ):
                ratio = decrease / predicted if predicted > 0.0 else 0.0
                damping = damping * 0.1 if ratio > 0.75 else damping
                accepted = trial, trial_residual, trial_jacobian, trial_objective
                break
            damping = max(damping * 10.0, largest * largest * 1e-6, 1e-12)
        if accepted is None:
            if valid_trials == 0 and geometry_rejections:
                raise failure(
                    "geometry-trial-exhaustion",
                    "all moving trial directions violate the geometry guard; nonlinear constrained stationarity has not been established",
                )
            if valid_trials == 0:
                raise failure(
                    "step-stagnation",
                    "no representable descending step at nonstationary point",
                )
            raise failure(
                "line-search-stagnation",
                "valid trials did not decrease objective at nonstationary point",
            )
        current, residual, jacobian, objective = accepted
        history.append(objective)
        accepted_iterations += 1
