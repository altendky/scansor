"""Exploratory area-weighted sphere fitting, separate from fitting admission."""

from __future__ import annotations

from typing import NotRequired, TypedDict

import numpy as np

from experiments.fit_solver import SolverDiagnostics, normalized_weights
from experiments.mesh_cylinder_fit import Array
from scansor.nonlinear_least_squares import solve_least_squares


class SphereFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool
    solver: NotRequired[SolverDiagnostics]


def residual_jacobian(points: Array, parameters: Array) -> tuple[Array, Array]:
    """Signed radial distance and derivatives for center XYZ plus radius."""
    center, radius = parameters[:3], float(parameters[3])
    radial = points - center
    distance = np.linalg.norm(radial, axis=1)
    if np.any(distance <= 0):
        raise ValueError("sphere distance derivative is undefined at its center")
    return distance - radius, np.column_stack(
        (-radial / distance[:, None], -np.ones(len(points)))
    )


def fit_sphere(points: Array, weights: Array) -> SphereFitResult:
    """Fit center and radius using every selected positive-area observation."""
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4:
        raise ValueError("expected at least four XYZ rows")
    if weights.shape != (len(points),):
        raise ValueError("weight shape mismatch")
    if not np.isfinite(points).all() or not np.isfinite(weights).all():
        raise ValueError("nonfinite fit input")
    if np.any(weights <= 0):
        raise ValueError("weights must be positive")

    w = normalized_weights(weights)
    origin = w @ points
    centered = points - origin
    scale = float(np.sqrt(w @ np.sum(centered * centered, axis=1)))
    if scale <= 0 or not np.isfinite(scale):
        raise ValueError(
            "ill-conditioned sphere observations; paint a wider curved patch"
        )
    normalized = centered / scale
    design = np.column_stack((2 * normalized, np.ones(len(points))))
    weighted_design = np.sqrt(w)[:, None] * design
    condition = float(np.linalg.cond(weighted_design))
    if not np.isfinite(condition) or condition > 1e12:
        raise ValueError(
            "ill-conditioned sphere observations; paint a wider curved patch"
        )
    algebraic, _, rank, _ = np.linalg.lstsq(
        weighted_design,
        np.sqrt(w) * np.sum(normalized * normalized, axis=1),
        rcond=None,
    )
    if rank != 4:
        raise ValueError(
            "ill-conditioned sphere observations; paint a wider curved patch"
        )
    radius_squared = float(algebraic[3] + algebraic[:3] @ algebraic[:3])
    if radius_squared <= 0 or not np.isfinite(radius_squared):
        raise ValueError("sphere initialization produced a nonpositive radius")

    def evaluate(local: Array) -> tuple[Array, Array]:
        r, jac = residual_jacobian(normalized, local)
        return np.sqrt(w) * r, np.sqrt(w)[:, None] * jac

    numerical = solve_least_squares(
        np.array([*algebraic[:3], np.sqrt(radius_squared)]),
        evaluate,
        parameter_scales=np.ones(4),
        residual_scale=1.0,
        geometry_is_valid=lambda p: bool(
            p[3] > 0 and np.all(np.linalg.norm(normalized - p[:3], axis=1) > 0)
        ),
    )
    local = np.asarray(numerical.parameters)
    parameters = np.array([*(origin + scale * local[:3]), scale * local[3]])

    residual, jacobian = residual_jacobian(points, parameters)
    gradient = jacobian.T @ (w * residual)
    return {
        "parameters": parameters.tolist(),
        "objective_history": [
            2 * value * scale**2 for value in numerical.objective_history
        ],
        "weighted_rms": float(np.sqrt(w @ residual**2)),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": numerical.condition**2,
        "gradient_infinity_norm": float(np.max(np.abs(gradient))),
        "converged": True,
        "solver": {
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": numerical.rank,
            "scaled_jacobian_condition": numerical.condition,
            "scaled_projected_gradient_infinity_norm": numerical.projected_gradient_norm,
            "coordinate_origin": origin.tolist(),
            "coordinate_length": scale,
        },
    }
