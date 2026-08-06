from __future__ import annotations

import math
from typing import Literal

import numpy as np

from scansor.execution_models import (
    AdapterDescriptor,
    AdapterInvocation,
    BackendResponse,
    execution_content_id,
)
from scansor.factor_models import (
    ASYMMETRIC_SHAPE_PARAMETERS,
    AXISYMMETRIC_SHAPE_PARAMETERS,
    POSE_LOWER,
    POSE_PARAMETERS,
    POSE_SCALES,
    POSE_UPPER,
    SHAPE_LOWER,
    SHAPE_SCALES,
    SHAPE_UPPER,
    FactorEvaluation,
    factor_contract,
)

IMPLEMENTATION = "scansor.numpy-gauss-newton.stepped-rotational-v0"
REVISION = "provisional-1"
RESIDUAL_SCALE_M = 0.001
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
    "unexpected-rank",
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
    invocation: AdapterInvocation,
    values: np.ndarray,
    code: RawCode,
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
    final_values = tuple(float(value) for value in values)
    provisional = BackendResponse.model_construct(
        adapter_id=invocation.adapter_id,
        final_values=final_values,
        invocation_id=invocation.invocation_id,
        raw_code=code,
        raw_message=None,
        reported_termination=reported,
        request_id=invocation.request_id,
        response_id="",
    )
    return BackendResponse(
        adapter_id=invocation.adapter_id,
        final_values=final_values,
        invocation_id=invocation.invocation_id,
        raw_code=code,
        raw_message=None,
        reported_termination=reported,
        request_id=invocation.request_id,
        response_id=execution_content_id(
            "backend-response", provisional, "response_id"
        ),
    )


def _expected_policy(
    invocation: AdapterInvocation,
) -> tuple[
    tuple[str, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...], int
]:
    if invocation.problem == "fixed-pose-shape":
        if invocation.variant == "axisymmetric":
            return (
                AXISYMMETRIC_SHAPE_PARAMETERS,
                SHAPE_LOWER[:6],
                SHAPE_UPPER[:6],
                SHAPE_SCALES[:6],
                6,
            )
        return (
            ASYMMETRIC_SHAPE_PARAMETERS,
            SHAPE_LOWER,
            SHAPE_UPPER,
            SHAPE_SCALES,
            7,
        )
    return (
        POSE_PARAMETERS,
        POSE_LOWER,
        POSE_UPPER,
        POSE_SCALES,
        5 if invocation.variant == "axisymmetric" else 6,
    )


def _validate_invocation(
    invocation: AdapterInvocation,
) -> tuple[AdapterInvocation, int]:
    invocation = AdapterInvocation.model_validate(invocation.model_dump(mode="python"))
    order, lower, upper, scales, rank = _expected_policy(invocation)
    if (
        invocation.adapter_id != NUMPY_GAUSS_NEWTON_DESCRIPTOR.adapter_id
        or invocation.callback_protocol_revision != "stepped-rotational-callback-v2"
        or invocation.contract_id != factor_contract().contract_id
        or invocation.parameter_order != order
        or invocation.lower_bounds != lower
        or invocation.upper_bounds != upper
        or invocation.parameter_scales != scales
        or invocation.parameter_dimension != len(order)
        or invocation.factor_count != invocation.residual_dimension
        or invocation.factor_count < rank
    ):
        raise ValueError("unsupported stepped-rotational NumPy backend invocation")
    return invocation, rank


def _shape_structure_valid(values: np.ndarray) -> bool:
    if values.size not in {6, 7} or not bool(np.isfinite(values).all()):
        return False
    r1, r2, r3, s20, s50, s80 = (float(value) for value in values[:6])
    if not (
        r1 > 0.0
        and r2 > 0.0
        and r3 > 0.0
        and 0.0 < s20 < s50 < s80
        and s20 > 0.004
        and s50 - s20 > 0.004
        and s80 - s50 > 0.004
        and r2 - r1 > 0.002
        and r2 - r3 > 0.002
    ):
        return False
    if values.size == 7:
        datum_x = float(values[6])
        return (
            0.0 < datum_x < r2
            and math.sqrt(max(0.0, r2 * r2 - datum_x * datum_x)) > 0.001
        )
    return True


def _trial_is_structural(invocation: AdapterInvocation, values: np.ndarray) -> bool:
    return invocation.problem != "fixed-pose-shape" or _shape_structure_valid(values)


def _arrays(
    evaluation: FactorEvaluation,
) -> tuple[np.ndarray, np.ndarray] | None:
    residual = np.asarray(evaluation.raw_residuals_m, dtype=np.float64)
    jacobian = np.asarray(evaluation.jacobian, dtype=np.float64)
    if (
        residual.ndim != 1
        or jacobian.shape != (residual.size, len(evaluation.parameter_order))
        or not bool(np.isfinite(residual).all())
        or not bool(np.isfinite(jacobian).all())
    ):
        return None
    return residual, jacobian


def _svd(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return np.linalg.svd(matrix, full_matrices=False)


def _projected_gradient(
    gradient: np.ndarray,
    values: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
) -> np.ndarray:
    projected = gradient.copy()
    projected[(values == lower) & (gradient > 0.0)] = 0.0
    projected[(values == upper) & (gradient < 0.0)] = 0.0
    return projected


def _feasible_step(
    values: np.ndarray,
    direction: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
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


class SteppedRotationalNumpyBackend:
    descriptor: AdapterDescriptor = NUMPY_GAUSS_NEWTON_DESCRIPTOR

    def execute(self, invocation: AdapterInvocation, callback: object) -> object:
        invocation, expected_rank = _validate_invocation(invocation)
        callback_function = callback
        if not callable(callback_function):
            raise TypeError("backend callback is not callable")

        current = np.asarray(invocation.initial_values, dtype=np.float64)
        lower = np.asarray(invocation.lower_bounds, dtype=np.float64)
        upper = np.asarray(invocation.upper_bounds, dtype=np.float64)
        scales = np.asarray(invocation.parameter_scales, dtype=np.float64)
        callback_cap = min(invocation.callback_limit, MAX_CALLBACKS)
        callback_count = 0

        evaluation = callback_function(tuple(float(value) for value in current))
        callback_count += 1
        if not isinstance(evaluation, FactorEvaluation):
            return _response(invocation, current, "numeric-failure")
        accepted_iterations = 0
        objective_stagnation_count = 0

        while True:
            arrays = _arrays(evaluation)
            if arrays is None:
                return _response(invocation, current, "numeric-failure")
            residual_m, jacobian = arrays
            if float(np.linalg.norm(residual_m, ord=np.inf)) <= (
                RESIDUAL_INFINITY_TOLERANCE_M
            ):
                return _response(invocation, current, "residual-tolerance")

            residual = residual_m / RESIDUAL_SCALE_M
            scaled_jacobian = jacobian * scales[np.newaxis, :] / RESIDUAL_SCALE_M
            objective = 0.5 * float(residual @ residual)
            gradient = scaled_jacobian.T @ residual
            projected = _projected_gradient(gradient, current, lower, upper)
            if not all(
                bool(np.isfinite(value).all())
                for value in (residual, scaled_jacobian, gradient, projected)
            ) or not math.isfinite(objective):
                return _response(invocation, current, "numeric-failure")
            if float(np.linalg.norm(projected, ord=np.inf)) <= (
                PROJECTED_GRADIENT_INFINITY_TOLERANCE
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
            cutoff = float(singular[0]) * SVD_RELATIVE_CUTOFF
            retained = singular > cutoff
            observed_rank = int(np.count_nonzero(retained))
            if observed_rank < expected_rank:
                return _response(invocation, current, "rank-deficient")
            if observed_rank > expected_rank:
                return _response(invocation, current, "unexpected-rank")
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
                tuple[
                    np.ndarray,
                    FactorEvaluation,
                    float,
                    float,
                    np.ndarray,
                    np.ndarray,
                ]
                | None
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
                trial_evaluation = callback_function(
                    tuple(float(value) for value in trial)
                )
                callback_count += 1
                if not isinstance(trial_evaluation, FactorEvaluation):
                    return _response(invocation, current, "numeric-failure")
                trial_arrays = _arrays(trial_evaluation)
                if trial_arrays is None:
                    return _response(invocation, current, "numeric-failure")
                trial_residual = trial_arrays[0] / RESIDUAL_SCALE_M
                trial_objective = 0.5 * float(trial_residual @ trial_residual)
                if not math.isfinite(trial_objective):
                    return _response(invocation, current, "numeric-failure")
                if trial_objective <= (
                    objective + ARMIJO * multiplier * directional_derivative
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
            relative_step = float(
                np.linalg.norm(multiplier * scaled_step, ord=np.inf)
            ) / max(1.0, float(np.linalg.norm(current / scales, ord=np.inf)))
            relative_objective_change = abs(objective - trial_objective) / max(
                1.0, abs(objective)
            )
            current = trial
            accepted_iterations += 1
            trial_residual = trial_raw_residual / RESIDUAL_SCALE_M
            trial_scaled_jacobian = (
                trial_jacobian * scales[np.newaxis, :] / RESIDUAL_SCALE_M
            )
            trial_gradient = trial_scaled_jacobian.T @ trial_residual
            trial_projected = _projected_gradient(trial_gradient, current, lower, upper)
            if not bool(np.isfinite(trial_projected).all()):
                return _response(invocation, current, "numeric-failure")
            if float(np.linalg.norm(trial_raw_residual, ord=np.inf)) <= (
                RESIDUAL_INFINITY_TOLERANCE_M
            ):
                return _response(invocation, current, "residual-tolerance")
            if float(np.linalg.norm(trial_projected, ord=np.inf)) <= (
                PROJECTED_GRADIENT_INFINITY_TOLERANCE
            ):
                return _response(invocation, current, "projected-gradient-tolerance")
            if relative_step <= RELATIVE_SCALED_STEP_THRESHOLD:
                return _response(invocation, current, "step-stagnation")
            if relative_objective_change <= RELATIVE_OBJECTIVE_STAGNATION:
                objective_stagnation_count += 1
            else:
                objective_stagnation_count = 0
            if objective_stagnation_count >= OBJECTIVE_STAGNATION_STEPS:
                return _response(invocation, current, "objective-stagnation")
