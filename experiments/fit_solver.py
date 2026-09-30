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
) -> GeometricFit:
    """Use all fixed rows, retaining physical checks in the original chart.

    Objective/history and raw gradients are reported in original length units.
    The historical normal-condition field now describes the normalized solve
    chart; the explicit solver diagnostics report the scaled Jacobian condition.
    If supplied, active_parameters identifies free solve-local coordinates;
    all other local coordinates are held at their encoded initial values.
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
    numerical = solve_least_squares(
        encoded,
        callback,
        parameter_scales=np.ones(len(initial)),
        residual_scale=coordinates.frame.length,
        lower_bounds=lower,
        upper_bounds=upper,
        geometry_is_valid=lambda local: geometry_is_valid(coordinates.decode(local)[0]),
    )
    parameters, _ = coordinates.decode(np.asarray(numerical.parameters))
    residual, jacobian = evaluate(parameters)
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
