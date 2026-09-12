"""Exploratory joint cylinder/end-plane fit with exact shared-axis geometry."""

from __future__ import annotations

from typing import TypedDict

import numpy as np

from experiments.mesh_cylinder_fit import Array, residual_jacobian


class CylinderPlaneFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    cylinder_weighted_rms: float
    plane_weighted_rms: float
    normal_matrix_condition: float
    gradient_infinity_norm: float


def joint_residual_jacobian(
    cylinder: Array, plane: Array, parameters: Array
) -> tuple[Array, Array]:
    """[cx,cy,a,b,R,h]: cylinder axis normalize(a,b,1), plane p·axis=h."""
    cylinder_residual, cylinder_jacobian = residual_jacobian(cylinder, parameters[:5])
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    da = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
    db = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
    plane_jacobian = np.zeros((len(plane), 6))
    plane_jacobian[:, 2] = plane @ da
    plane_jacobian[:, 3] = plane @ db
    plane_jacobian[:, 5] = -1
    jacobian = np.vstack(
        (np.column_stack((cylinder_jacobian, np.zeros(len(cylinder)))), plane_jacobian)
    )
    return np.concatenate((cylinder_residual, plane @ axis - parameters[5])), jacobian


def fit_cylinder_plane(
    cylinder: Array,
    plane: Array,
    cylinder_area: Array,
    plane_area: Array,
    initial: Array,
) -> CylinderPlaneFitResult:
    """Minimize total area-weighted error with fixed memberships and no trimming."""
    for points, area in ((cylinder, cylinder_area), (plane, plane_area)):
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("each surface needs at least three XYZ observations")
        if area.shape != (len(points),) or not np.isfinite(points).all():
            raise ValueError("invalid point or area array")
        if not np.isfinite(area).all() or np.any(area <= 0):
            raise ValueError("areas must be finite and positive")
    if initial.shape != (6,) or not np.isfinite(initial).all() or initial[4] <= 0:
        raise ValueError("invalid initial parameters")
    weights = np.concatenate((cylinder_area, plane_area))
    total = float(weights.sum())
    if not np.isfinite(total):
        raise ValueError("nonfinite total area")
    weights /= total
    parameters = initial.copy()
    history: list[float] = []
    for _ in range(60):
        residual, jacobian = joint_residual_jacobian(cylinder, plane, parameters)
        objective = float(weights @ (residual * residual))
        history.append(objective)
        normal = jacobian.T @ (weights[:, None] * jacobian)
        condition = float(np.linalg.cond(normal))
        if not np.isfinite(condition) or condition > 1e12:
            raise ValueError("ill-conditioned joint geometry in this parameter frame")
        gradient = jacobian.T @ (weights * residual)
        step = np.linalg.solve(normal, -gradient)
        if np.max(np.abs(step)) < 1e-10 * max(1.0, float(np.max(np.abs(parameters)))):
            break
        for power in range(25):
            candidate = parameters + step * 2.0**-power
            if candidate[4] <= 0:
                continue
            r, _ = joint_residual_jacobian(cylinder, plane, candidate)
            if float(weights @ (r * r)) < objective:
                parameters = candidate
                break
        else:
            raise ValueError("joint fit failed to decrease objective")
    else:
        raise ValueError("joint fit did not converge")
    residual, jacobian = joint_residual_jacobian(cylinder, plane, parameters)
    cylinder_residual = residual[: len(cylinder)]
    plane_residual = residual[len(cylinder) :]
    return {
        "parameters": parameters.tolist(),
        "objective_history": history,
        "weighted_rms": float(np.sqrt(weights @ (residual * residual))),
        "cylinder_weighted_rms": float(
            np.sqrt(cylinder_area @ (cylinder_residual**2) / cylinder_area.sum())
        ),
        "plane_weighted_rms": float(
            np.sqrt(plane_area @ (plane_residual**2) / plane_area.sum())
        ),
        "normal_matrix_condition": float(
            np.linalg.cond(jacobian.T @ (weights[:, None] * jacobian))
        ),
        "gradient_infinity_norm": float(
            np.max(np.abs(jacobian.T @ (weights * residual)))
        ),
    }
