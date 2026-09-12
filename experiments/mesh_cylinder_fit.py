"""Exploratory area-weighted cylinder fitting, separate from fitting admission."""

from __future__ import annotations

from typing import TypedDict

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


class CylinderFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool


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
    w = weights / weights.sum()
    parameters = initial.copy()
    history: list[float] = []
    converged = False
    for _ in range(60):
        residual, jacobian = residual_jacobian(points, parameters)
        objective = float(w @ (residual * residual))
        history.append(objective)
        hessian = jacobian.T @ (w[:, None] * jacobian)
        gradient = jacobian.T @ (w * residual)
        if np.linalg.cond(hessian) > 1e12:
            raise ValueError(
                "ill-conditioned cylinder geometry in this parameter frame"
            )
        step = np.linalg.solve(hessian, -gradient)
        if np.max(np.abs(step)) < 1e-10 * max(1.0, float(np.max(np.abs(parameters)))):
            converged = True
            break
        for power in range(25):
            candidate = parameters + step * 2.0**-power
            if candidate[4] <= 0:
                continue
            r, _ = residual_jacobian(points, candidate)
            if float(w @ (r * r)) < objective:
                parameters = candidate
                break
        else:
            raise ValueError("cylinder step failed to decrease objective")
    if not converged:
        raise ValueError("cylinder fit did not converge")
    residual, jacobian = residual_jacobian(points, parameters)
    return {
        "parameters": parameters.tolist(),
        "objective_history": history,
        "weighted_rms": float(np.sqrt(w @ (residual * residual))),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": float(
            np.linalg.cond(jacobian.T @ (w[:, None] * jacobian))
        ),
        "gradient_infinity_norm": float(np.max(np.abs(jacobian.T @ (w * residual)))),
        "converged": converged,
    }
