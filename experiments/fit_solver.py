"""Shared fixed-membership numerical adapter for the mesh experiments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import FitCoordinates, NormalCallback
from scansor.nonlinear_least_squares import solve_least_squares

Array = NDArray[np.float64]


class FitCancelled(RuntimeError):
    """Control signal; numerical callback ValueError handlers must not catch it."""


class FitBudgetExhausted(RuntimeError):
    """The attempt consumed its callback allowance without convergence."""


@dataclass(frozen=True)
class AttemptLimits:
    max_iterations: int = 100
    max_trials: int = 16
    max_evaluations: int = 512

    def __post_init__(self) -> None:
        for name in ("max_iterations", "max_trials", "max_evaluations"):
            value = getattr(self, name)
            minimum = 0 if name == "max_iterations" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer at least {minimum}")


class AttemptControl:
    """Shared observation/equality quota, also usable during feasibility solves.

    Family adapters must route every numerical callback through call(). The
    final full-problem assessment is separately mandatory, outside this quota.
    Cancellation is cooperative at callback/geometry boundaries.
    """

    def __init__(
        self,
        limits: AttemptLimits,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.limits: AttemptLimits = limits
        self.cancelled: Callable[[], bool] | None = cancelled
        self.evaluations: int = 0

    def checkpoint(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise FitCancelled("Fit cancelled before publishing a result")

    def call[Result](
        self, callback: Callable[[Array], Result], values: Array
    ) -> Result:
        self.checkpoint()
        if self.evaluations >= self.limits.max_evaluations:
            raise FitBudgetExhausted("Fit callback evaluation budget exhausted")
        self.evaluations += 1
        result = callback(values)
        self.checkpoint()
        return result


def normalized_weights(weights: Array) -> Array:
    """Preserve relative positive contributions without overflowing their sum."""
    if (
        weights.ndim != 1
        or not len(weights)
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
    ):
        raise ValueError("weights must be finite and positive")
    normalized = weights / np.max(weights)
    normalized /= normalized.sum()
    if np.any(normalized <= 0):
        raise ValueError("weight range underflows a positive observation contribution")
    return normalized


class SolverDiagnostics(TypedDict):
    implementation: str
    termination: str
    iterations: int
    evaluations: int
    rank: int
    scaled_jacobian_condition: float
    scaled_projected_gradient_infinity_norm: float
    coordinate_length: float
    coordinate_origin: list[float]


@dataclass(frozen=True)
class GeometricFit:
    parameters: Array
    residual: Array
    jacobian: Array
    objective_history: list[float]
    condition: float
    gradient: float
    solver: SolverDiagnostics


def perpendicular_normal(size: int, a: int = 2, b: int = 3) -> NormalCallback:
    def normal(parameters: Array) -> tuple[Array, Array]:
        raw = np.array([parameters[a], parameters[b], 1.0])
        length = float(np.linalg.norm(raw))
        axis = raw / length
        derivative = np.zeros((3, size))
        derivative[:, a] = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
        derivative[:, b] = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
        return axis, derivative

    return normal


def solve_geometric_fit(
    initial: Array,
    weights: Array,
    coordinates: FitCoordinates,
    evaluate: Callable[[Array], tuple[Array, Array]],
    geometry_is_valid: Callable[[Array], bool],
    *,
    active_parameters: tuple[int, ...] | None = None,
    control: AttemptControl | None = None,
) -> GeometricFit:
    """Use all fixed rows, retaining physical checks in the original chart.

    Objective/history and raw gradients are reported in original length units.
    The historical normal-condition field now describes the normalized solve
    chart; the explicit solver diagnostics report the scaled Jacobian condition.
    If supplied, active_parameters identifies free solve-local coordinates;
    all other local coordinates are held at their encoded initial values.
    An optional attempt control bounds and cancels new recovery paths; legacy
    callers retain the existing numerical limits.
    """
    normalized = normalized_weights(weights)
    square_root = np.sqrt(normalized)

    def callback(local: Array) -> tuple[Array, Array]:
        physical, tangent = coordinates.decode(local)
        residual, jacobian = evaluate(physical)
        if residual.shape != weights.shape:
            raise ValueError("fixed observation and weight rows differ")
        return square_root * residual, square_root[:, None] * (jacobian @ tangent)

    encoded = coordinates.encode(initial)
    lower = upper = None
    if active_parameters is not None:
        if not active_parameters or len(set(active_parameters)) != len(
            active_parameters
        ):
            raise ValueError("active parameters must be nonempty and distinct")
        if any(index < 0 or index >= len(initial) for index in active_parameters):
            raise ValueError("active parameter index is outside the fit chart")
        lower, upper = encoded.copy(), encoded.copy()
        lower[list(active_parameters)] = -np.inf
        upper[list(active_parameters)] = np.inf

    def controlled_callback(local: Array) -> tuple[Array, Array]:
        return callback(local) if control is None else control.call(callback, local)

    def valid(local: Array) -> bool:
        if control is not None:
            control.checkpoint()
        return geometry_is_valid(coordinates.decode(local)[0])

    numerical = solve_least_squares(
        encoded,
        controlled_callback,
        parameter_scales=np.ones(len(initial)),
        residual_scale=coordinates.frame.length,
        lower_bounds=lower,
        upper_bounds=upper,
        geometry_is_valid=valid,
        max_iterations=300 if control is None else control.limits.max_iterations,
        max_trials=32 if control is None else control.limits.max_trials,
    )
    parameters, _ = coordinates.decode(np.asarray(numerical.parameters))
    residual, jacobian = (
        evaluate(parameters) if control is None else control.call(evaluate, parameters)
    )
    return GeometricFit(
        parameters=parameters,
        residual=residual,
        jacobian=jacobian,
        objective_history=[
            2 * objective * coordinates.frame.length**2
            for objective in numerical.objective_history
        ],
        condition=numerical.condition**2,
        gradient=float(np.max(np.abs(jacobian.T @ (normalized * residual)))),
        solver={
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": numerical.rank,
            "scaled_jacobian_condition": numerical.condition,
            "scaled_projected_gradient_infinity_norm": numerical.projected_gradient_norm,
            "coordinate_length": coordinates.frame.length,
            "coordinate_origin": coordinates.frame.origin.tolist(),
        },
    )
