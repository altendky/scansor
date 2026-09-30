"""Exploratory area-weighted cylinder fitting, separate from fitting admission."""

from __future__ import annotations

from typing import NotRequired, TypedDict

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import AxisChart, CoordinateFrame, FitCoordinates
from experiments.fit_solver import (
    SolverDiagnostics,
    normalized_weights,
    solve_geometric_fit,
)

Array = NDArray[np.float64]


class CylinderFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool
    solver: NotRequired[SolverDiagnostics]


def valid_cylinder_geometry(points: Array, parameters: Array) -> bool:
    if not np.isfinite(parameters).all() or parameters[4] <= 0:
        return False
    raw = np.array([parameters[2], parameters[3], 1.0])
    axis = raw / np.linalg.norm(raw)
    q = points - np.array([parameters[0], parameters[1], 0.0])
    radial = q - (q @ axis)[:, None] * axis
    return bool(np.all(np.linalg.norm(radial, axis=1) > 0))


def residual_jacobian(points: Array, parameters: Array) -> tuple[Array, Array]:
    """Local axis (a,b,1); center (cx,cy,0) fixes axial translation freedom."""
    cx, cy, a, b, radius = parameters
    raw = np.array([a, b, 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    q = points - np.array([cx, cy, 0.0])
    axial = q @ axis
    radial = q - axial[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    if np.any(rho <= 0):
        raise ValueError("cylinder distance derivative is undefined on its axis")
    unit = radial / rho[:, None]
    da = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
    db = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
    jacobian = np.column_stack(
        (
            -unit[:, 0],
            -unit[:, 1],
            -axial * (unit @ da),
            -axial * (unit @ db),
            -np.ones(len(points)),
        )
    )
    return rho - radius, jacobian


def fit_cylinder(points: Array, weights: Array, initial: Array) -> CylinderFitResult:
    """All selected rows, ordinary weighted least squares, no residual trimming."""
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 6:
        raise ValueError("expected at least six XYZ rows")
    if weights.shape != (len(points),) or initial.shape != (5,):
        raise ValueError("weight or parameter shape mismatch")
    if not all(np.isfinite(x).all() for x in (points, weights, initial)):
        raise ValueError("nonfinite fit input")
    if np.any(weights <= 0) or initial[4] <= 0:
        raise ValueError("weights and initial radius must be positive")
    w = normalized_weights(weights)
    coordinates = FitCoordinates(
        CoordinateFrame.from_observations(points, weights),
        5,
        axes=(AxisChart(0, 1, 2, 3, 4),),
    )
    fit = solve_geometric_fit(
        initial,
        weights,
        coordinates,
        lambda p: residual_jacobian(points, p),
        lambda p: valid_cylinder_geometry(points, p),
    )
    residual = fit.residual
    return {
        "parameters": fit.parameters.tolist(),
        "objective_history": fit.objective_history,
        "weighted_rms": float(np.sqrt(w @ (residual * residual))),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": fit.condition,
        "gradient_infinity_norm": fit.gradient,
        "converged": True,
        "solver": fit.solver,
    }
