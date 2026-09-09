from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import ValidationError

from scansor.declared_execution_models import (
    AdapterDescriptor,
    AdapterInvocation,
    BackendResponse,
    execution_content_id,
)
from scansor.declared_factor_models import FactorEvaluation
from scansor.errors import ScansorError
from scansor.model_declarations import validate_runtime_parameter_vector

IMPLEMENTATION = "scansor.numpy-gauss-newton.declared-analytic-model"
REVISION = "provisional-1"
# Step truncation is a fixed adapter policy, independent of declared rank eligibility.
SVD_RELATIVE_CUTOFF = 1e-10
RESIDUAL_INFINITY_TOLERANCE_M = 1e-12
PROJECTED_GRADIENT_INFINITY_TOLERANCE = 1e-12
RELATIVE_SCALED_STEP_THRESHOLD = 1e-12
RELATIVE_OBJECTIVE_STAGNATION = 1e-15
OBJECTIVE_STAGNATION_STEPS = 3
ARMIJO = 1e-4
BACKTRACK_MULTIPLIER = 0.5
LINE_SEARCH_TRIALS = 13
MAX_ACCEPTED_ITERATIONS = 64
MAX_CALLBACKS = 256

RawCode = Literal[
    "residual-tolerance",
    "projected-gradient-tolerance",
    "iteration-limit",
    "callback-limit",
    "line-search-stagnation",
    "step-stagnation",
    "objective-stagnation",
    "rank-deficient",
    "svd-failed",
    "numeric-failure",
    "non-descent-direction",
]


def _descriptor() -> AdapterDescriptor:
    provisional = AdapterDescriptor.model_construct(
        adapter_id="", implementation=IMPLEMENTATION, revision=REVISION
    )
    return AdapterDescriptor(
        adapter_id=execution_content_id("adapter", provisional, "adapter_id"),
        implementation=IMPLEMENTATION,
        revision=REVISION,
    )


NUMPY_GAUSS_NEWTON_DESCRIPTOR = _descriptor()


def _response(
    invocation: AdapterInvocation, values: np.ndarray, code: RawCode
) -> BackendResponse:
    reported: Literal["converged", "limit", "stopped", "failure"]
    if code in {"residual-tolerance", "projected-gradient-tolerance"}:
        reported = "converged"
    elif code in {"iteration-limit", "callback-limit"}:
        reported = "limit"
    elif code in {
        "line-search-stagnation",
        "step-stagnation",
        "objective-stagnation",
    }:
        reported = "stopped"
    else:
        reported = "failure"
    provisional = BackendResponse.model_construct(
        adapter_id=invocation.adapter_id,
        final_values=tuple(float(value) for value in values),
        invocation_id=invocation.invocation_id,
        model_id=invocation.model_id,
        raw_code=code,
        raw_message=None,
        reported_termination=reported,
        request_id=invocation.request_id,
        response_id="",
    )
    return BackendResponse.model_validate(
        provisional.model_dump(mode="python")
        | {
            "response_id": execution_content_id(
                "backend-response", provisional, "response_id"
            )
        }
    )


def _validate_invocation(invocation: AdapterInvocation) -> AdapterInvocation:
    invocation = AdapterInvocation.model_validate(invocation.model_dump(mode="python"))
    if invocation.adapter_id != NUMPY_GAUSS_NEWTON_DESCRIPTOR.adapter_id:
        raise ValueError("unsupported declared NumPy backend invocation")
    _ = validate_runtime_parameter_vector(
        invocation.declaration, invocation.initial_values
    )
    return invocation


def _trial_is_structural(invocation: AdapterInvocation, values: np.ndarray) -> bool:
    try:
        _ = validate_runtime_parameter_vector(
            invocation.declaration, tuple(float(value) for value in values)
        )
    except (ScansorError, ValidationError, ValueError, FloatingPointError):
        return False
    return True


def _arrays(
    evaluation: object, invocation: AdapterInvocation, values: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    if not isinstance(evaluation, FactorEvaluation):
        return None
    try:
        evaluation = FactorEvaluation.model_validate(
            evaluation.model_dump(mode="python")
        )
    except (ValidationError, ValueError):
        return None
    if (
        evaluation.model_id != invocation.model_id
        or evaluation.factor_set_id != invocation.factor_set_id
        or evaluation.selection_id != invocation.selection_id
        or evaluation.parameter_order != invocation.parameter_order
        or evaluation.parameters.values != tuple(float(value) for value in values)
        or len(evaluation.active_factor_ids) != invocation.factor_count
    ):
        return None
    residual = np.asarray(evaluation.raw_residuals_m, dtype=np.float64)
    jacobian = np.asarray(evaluation.jacobian, dtype=np.float64)
    if (
        residual.shape != (invocation.residual_dimension,)
        or jacobian.shape
        != (invocation.residual_dimension, invocation.parameter_dimension)
        or not bool(np.isfinite(residual).all())
        or not bool(np.isfinite(jacobian).all())
    ):
        return None
    return residual, jacobian


def _svd(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return np.linalg.svd(matrix, full_matrices=False)


def _rank_failure(
    invocation: AdapterInvocation, jacobian: np.ndarray
) -> RawCode | None:
    policy = invocation.declaration.optimization_preflight.relative_rank
    indices = {
        parameter: index for index, parameter in enumerate(invocation.parameter_order)
    }
    columns = [indices[parameter] for parameter in policy.parameter_ids]
    scaled = (
        jacobian[:, columns]
        * np.asarray(policy.parameter_scales, dtype=np.float64)[np.newaxis, :]
        / policy.residual_scale
    )
    if not bool(np.isfinite(scaled).all()):
        return "numeric-failure"
    try:
        singular = (
            np.linalg.svd(scaled, compute_uv=False)
            if columns
            else np.asarray((), dtype=np.float64)
        )
    except (np.linalg.LinAlgError, ValueError, FloatingPointError):
        return "svd-failed"
    if not bool(np.isfinite(singular).all()):
        return "svd-failed"
    cutoff = float(singular[0]) * policy.relative_threshold if singular.size else 0.0
    observed_rank = int(np.count_nonzero(singular > cutoff))
    return "rank-deficient" if observed_rank < policy.required_rank else None


def _projected_gradient(
    gradient: np.ndarray, values: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    projected = gradient.copy()
    projected[(values == lower) & (gradient > 0.0)] = 0.0
    projected[(values == upper) & (gradient < 0.0)] = 0.0
    return projected


def _feasible_step(
    values: np.ndarray, direction: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> tuple[float, tuple[tuple[int, float], ...]]:
    limiting: list[tuple[int, float]] = []
    maximum = 1.0
    for index, delta in enumerate(direction):
        if delta > 0.0:
            ratio = float((upper[index] - values[index]) / delta)
            bound = float(upper[index])
        elif delta < 0.0:
            ratio = float((lower[index] - values[index]) / delta)
            bound = float(lower[index])
        else:
            continue
        if ratio < maximum:
            maximum = ratio
            limiting = [(index, bound)]
        elif ratio == maximum:
            limiting.append((index, bound))
    return maximum, tuple(limiting)


def _active_set_step(
    scaled_jacobian: np.ndarray,
    residual: np.ndarray,
    values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    scales: np.ndarray,
    initial_step: np.ndarray,
) -> np.ndarray:
    step = initial_step
    free = np.ones(step.size, dtype=np.bool_)
    while True:
        direction = step * scales
        blocked = free & (
            ((values == lower) & (direction < 0.0))
            | ((values == upper) & (direction > 0.0))
        )
        if not bool(np.any(blocked)):
            return step
        free[blocked] = False
        step = np.zeros_like(step)
        if not bool(np.any(free)):
            return step
        left, singular, right = _svd(scaled_jacobian[:, free])
        if singular.size == 0 or not bool(np.isfinite(singular).all()):
            raise np.linalg.LinAlgError
        retained = singular > float(singular[0]) * SVD_RELATIVE_CUTOFF
        coefficients = left[:, retained].T @ residual
        step[free] = -(right[retained, :].T @ (coefficients / singular[retained]))


class DeclaredNumpyBackend:
    """The fixed, bounded internal adapter for declared fixed-pose shape factors."""

    descriptor: AdapterDescriptor = NUMPY_GAUSS_NEWTON_DESCRIPTOR

    def execute(self, invocation: AdapterInvocation, callback: object) -> object:
        invocation = _validate_invocation(invocation)
        if not callable(callback):
            raise TypeError("backend callback is not callable")

        current = np.asarray(invocation.initial_values, dtype=np.float64)
        lower = np.asarray(invocation.lower_bounds, dtype=np.float64)
        upper = np.asarray(invocation.upper_bounds, dtype=np.float64)
        scales = np.asarray(invocation.parameter_scales, dtype=np.float64)
        residual_scale = (
            invocation.declaration.optimization_preflight.relative_rank.residual_scale
        )
        callback_cap = min(invocation.callback_limit, MAX_CALLBACKS)
        callback_count = 1
        evaluation = callback(tuple(float(value) for value in current))
        accepted_iterations = 0
        objective_stagnation_count = 0

        while True:
            arrays = _arrays(evaluation, invocation, current)
            if arrays is None:
                return _response(invocation, current, "numeric-failure")
            residual_m, jacobian = arrays
            failure = _rank_failure(invocation, jacobian)
            if failure is not None:
                return _response(invocation, current, failure)
            if (
                float(np.linalg.norm(residual_m, ord=np.inf))
                <= RESIDUAL_INFINITY_TOLERANCE_M
            ):
                return _response(invocation, current, "residual-tolerance")

            residual = residual_m / residual_scale
            scaled_jacobian = jacobian * scales[np.newaxis, :] / residual_scale
            objective = 0.5 * float(residual @ residual)
            gradient = scaled_jacobian.T @ residual
            projected = _projected_gradient(gradient, current, lower, upper)
            if not all(
                bool(np.isfinite(value).all())
                for value in (residual, scaled_jacobian, gradient, projected)
            ) or not math.isfinite(objective):
                return _response(invocation, current, "numeric-failure")
            if (
                float(np.linalg.norm(projected, ord=np.inf))
                <= PROJECTED_GRADIENT_INFINITY_TOLERANCE
            ):
                return _response(invocation, current, "projected-gradient-tolerance")
            if accepted_iterations >= MAX_ACCEPTED_ITERATIONS:
                return _response(invocation, current, "iteration-limit")

            try:
                left, singular, right = _svd(scaled_jacobian)
            except (np.linalg.LinAlgError, ValueError, FloatingPointError):
                return _response(invocation, current, "svd-failed")
            if singular.size == 0 or not bool(np.isfinite(singular).all()):
                return _response(invocation, current, "svd-failed")
            retained = singular > float(singular[0]) * SVD_RELATIVE_CUTOFF
            coefficients = left[:, retained].T @ residual
            scaled_step = -(right[retained, :].T @ (coefficients / singular[retained]))
            try:
                scaled_step = _active_set_step(
                    scaled_jacobian,
                    residual,
                    current,
                    lower,
                    upper,
                    scales,
                    scaled_step,
                )
            except (np.linalg.LinAlgError, ValueError, FloatingPointError):
                return _response(invocation, current, "svd-failed")
            direction = scaled_step * scales
            directional_derivative = float(gradient @ scaled_step)
            if directional_derivative >= 0.0 and bool(np.any(projected != 0.0)):
                scaled_step = -projected
                direction = scaled_step * scales
                directional_derivative = float(gradient @ scaled_step)
            if not bool(np.isfinite(direction).all()) or not math.isfinite(
                directional_derivative
            ):
                return _response(invocation, current, "numeric-failure")
            if directional_derivative >= 0.0:
                return _response(invocation, current, "non-descent-direction")

            feasible, limiting = _feasible_step(current, direction, lower, upper)
            if not math.isfinite(feasible) or feasible <= 0.0:
                return _response(invocation, current, "line-search-stagnation")
            accepted: (
                tuple[np.ndarray, object, float, float, np.ndarray, np.ndarray] | None
            ) = None
            for trial_index in range(LINE_SEARCH_TRIALS):
                multiplier = feasible * BACKTRACK_MULTIPLIER**trial_index
                trial = current + multiplier * direction
                if trial_index == 0:
                    for index, bound in limiting:
                        trial[index] = bound
                if not bool(np.isfinite(trial).all()):
                    return _response(invocation, current, "numeric-failure")
                if not _trial_is_structural(invocation, trial):
                    continue
                if callback_count >= callback_cap:
                    return _response(invocation, current, "callback-limit")
                trial_evaluation = callback(tuple(float(value) for value in trial))
                callback_count += 1
                trial_arrays = _arrays(trial_evaluation, invocation, trial)
                if trial_arrays is None:
                    return _response(invocation, current, "numeric-failure")
                trial_residual = trial_arrays[0] / residual_scale
                trial_objective = 0.5 * float(trial_residual @ trial_residual)
                if not math.isfinite(trial_objective):
                    return _response(invocation, current, "numeric-failure")
                if (
                    trial_objective
                    <= objective + ARMIJO * multiplier * directional_derivative
                ):
                    accepted = (
                        trial,
                        trial_evaluation,
                        trial_objective,
                        multiplier,
                        trial_arrays[0],
                        trial_arrays[1],
                    )
                    break
            if accepted is None:
                return _response(invocation, current, "line-search-stagnation")

            (
                trial,
                evaluation,
                trial_objective,
                multiplier,
                trial_raw_residual,
                trial_jacobian,
            ) = accepted
            previous = current
            current = trial
            accepted_iterations += 1
            failure = _rank_failure(invocation, trial_jacobian)
            if failure is not None:
                return _response(invocation, current, failure)
            if (
                float(np.linalg.norm(trial_raw_residual, ord=np.inf))
                <= RESIDUAL_INFINITY_TOLERANCE_M
            ):
                return _response(invocation, current, "residual-tolerance")
            trial_residual = trial_raw_residual / residual_scale
            trial_scaled_jacobian = (
                trial_jacobian * scales[np.newaxis, :] / residual_scale
            )
            trial_gradient = trial_scaled_jacobian.T @ trial_residual
            trial_projected = _projected_gradient(trial_gradient, current, lower, upper)
            if not bool(np.isfinite(trial_projected).all()):
                return _response(invocation, current, "numeric-failure")
            if (
                float(np.linalg.norm(trial_projected, ord=np.inf))
                <= PROJECTED_GRADIENT_INFINITY_TOLERANCE
            ):
                return _response(invocation, current, "projected-gradient-tolerance")
            with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
                scaled_previous = previous / scales
            if not bool(np.isfinite(scaled_previous).all()):
                return _response(invocation, current, "numeric-failure")
            relative_step = float(
                np.linalg.norm(multiplier * scaled_step, ord=np.inf)
            ) / max(1.0, float(np.linalg.norm(scaled_previous, ord=np.inf)))
            relative_objective_change = abs(objective - trial_objective) / max(
                1.0, abs(objective)
            )
            if relative_step <= RELATIVE_SCALED_STEP_THRESHOLD:
                return _response(invocation, current, "step-stagnation")
            if relative_objective_change <= RELATIVE_OBJECTIVE_STAGNATION:
                objective_stagnation_count += 1
            else:
                objective_stagnation_count = 0
            if objective_stagnation_count >= OBJECTIVE_STAGNATION_STEPS:
                return _response(invocation, current, "objective-stagnation")
