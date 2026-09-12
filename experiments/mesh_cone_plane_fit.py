"""Exploratory finite cone-side/end-plane fit with a shared axis and cylinder limit."""

from __future__ import annotations

from typing import TypedDict

import numpy as np

from experiments.mesh_cylinder_fit import Array
from experiments.mesh_cylinder_plane_fit import joint_residual_jacobian


class ConePlaneFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    cone_weighted_rms: float
    plane_weighted_rms: float
    normal_matrix_condition: float
    gradient_infinity_norm: float


class InvalidConeDomain(ValueError):
    """Parameters or orthogonal projections leave the declared positive-radius side."""


def cone_plane_residual_jacobian(
    cone: Array, plane: Array, parameters: Array, axial_domain: tuple[float, float]
) -> tuple[Array, Array]:
    """[cx,cy,a,b,R,h,k], radius(z)=R+k*z; orthogonal side and plane distances.

    z is measured along the unit axis from (cx,cy,0). The finite interval is
    side-surface support, not a cap-distance model; projections outside it fail.
    """
    lo, hi = axial_domain
    if not np.isfinite([lo, hi]).all() or lo >= hi:
        raise InvalidConeDomain("expected a finite increasing axial domain")
    radius, taper = parameters[4], parameters[6]
    if (
        not np.isfinite(parameters).all()
        or radius <= 0
        or min(radius + taper * lo, radius + taper * hi) <= 0
    ):
        raise InvalidConeDomain(
            "radius must be positive at reference and both domain endpoints"
        )
    residual, jacobian6 = joint_residual_jacobian(cone, plane, parameters[:6])
    count = len(cone)
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    q = cone - np.array([parameters[0], parameters[1], 0])
    z = q @ axis
    radial_error = residual[:count].copy()
    projected_z = (z + taper * radial_error) / (1 + taper * taper)
    if np.any(projected_z < lo) or np.any(projected_z > hi):
        raise InvalidConeDomain(
            "orthogonal side projection outside declared axial support"
        )
    da = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
    db = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
    dz = np.zeros((count, 6))
    dz[:, 0] = -axis[0]
    dz[:, 1] = -axis[1]
    dz[:, 2] = q @ da
    dz[:, 3] = q @ db
    scale = float(np.hypot(1.0, taper))
    g = radial_error - taper * z
    jacobian = np.column_stack((jacobian6, np.zeros(len(residual))))
    jacobian[:count, :6] = (jacobian6[:count] - taper * dz) / scale
    jacobian[:count, 6] = -z / scale - g * taper / scale**3
    residual[:count] = g / scale
    return residual, jacobian


def fit_cone_plane(
    cone: Array,
    plane: Array,
    cone_area: Array,
    plane_area: Array,
    initial: Array,
    axial_domain: tuple[float, float],
) -> ConePlaneFitResult:
    """Minimize total area-weighted error with fixed memberships and no trimming."""
    for points, area in ((cone, cone_area), (plane, plane_area)):
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("each surface needs at least three XYZ observations")
        if area.shape != (len(points),) or not np.isfinite(points).all():
            raise ValueError("invalid point or area array")
        if not np.isfinite(area).all() or np.any(area <= 0):
            raise ValueError("areas must be finite and positive")
    if initial.shape != (7,) or not np.isfinite(initial).all() or initial[4] <= 0:
        raise ValueError("invalid initial parameters")
    weights = np.concatenate((cone_area, plane_area))
    total = float(weights.sum())
    if not np.isfinite(total):
        raise ValueError("nonfinite total area")
    weights /= total
    parameters = initial.copy()
    history: list[float] = []
    for _ in range(60):
        residual, jacobian = cone_plane_residual_jacobian(
            cone, plane, parameters, axial_domain
        )
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
            try:
                r, _ = cone_plane_residual_jacobian(
                    cone, plane, candidate, axial_domain
                )
            except InvalidConeDomain:
                continue
            if float(weights @ (r * r)) < objective:
                parameters = candidate
                break
        else:
            raise ValueError("joint fit failed to decrease objective")
    else:
        raise ValueError("joint fit did not converge")
    residual, jacobian = cone_plane_residual_jacobian(
        cone, plane, parameters, axial_domain
    )
    cone_residual = residual[: len(cone)]
    plane_residual = residual[len(cone) :]
    return {
        "parameters": parameters.tolist(),
        "objective_history": history,
        "weighted_rms": float(np.sqrt(weights @ (residual * residual))),
        "cone_weighted_rms": float(
            np.sqrt(cone_area @ (cone_residual**2) / cone_area.sum())
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
