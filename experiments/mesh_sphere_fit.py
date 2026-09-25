"""Exploratory area-weighted sphere fitting, separate from fitting admission."""

from __future__ import annotations

from typing import TypedDict

import numpy as np

from experiments.mesh_cylinder_fit import Array


class SphereFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool


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

    w = weights / weights.sum()
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
    parameters = np.array(
        [*(origin + scale * algebraic[:3]), scale * np.sqrt(radius_squared)]
    )

    history: list[float] = []
    converged = False
    for _ in range(60):
        residual, jacobian = residual_jacobian(points, parameters)
        objective = float(w @ residual**2)
        history.append(objective)
        normal = jacobian.T @ (w[:, None] * jacobian)
        condition = float(np.linalg.cond(normal))
        if not np.isfinite(condition) or condition > 1e14:
            raise ValueError(
                "ill-conditioned sphere observations; paint a wider curved patch"
            )
        gradient = jacobian.T @ (w * residual)
        step = np.linalg.solve(normal, -gradient)
        parameter_scale = max(1.0, scale, abs(float(parameters[3])))
        if np.max(np.abs(step)) < 1e-10 * parameter_scale:
            converged = True
            break
        for power in range(25):
            candidate = parameters + step * 2.0**-power
            if candidate[3] <= 0:
                continue
            candidate_residual, _ = residual_jacobian(points, candidate)
            if float(w @ candidate_residual**2) < objective:
                parameters = candidate
                break
        else:
            resolution = 32 * np.finfo(float).eps * max(scale**2, objective)
            if float(step @ normal @ step) <= resolution:
                converged = True
                break
            raise ValueError("sphere step failed to decrease objective")
    if not converged:
        raise ValueError("sphere fit did not converge")

    residual, jacobian = residual_jacobian(points, parameters)
    normal = jacobian.T @ (w[:, None] * jacobian)
    gradient = jacobian.T @ (w * residual)
    return {
        "parameters": parameters.tolist(),
        "objective_history": history,
        "weighted_rms": float(np.sqrt(w @ residual**2)),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": float(np.linalg.cond(normal)),
        "gradient_infinity_norm": float(np.max(np.abs(gradient))),
        "converged": converged,
    }
