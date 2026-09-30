"""Exploratory finite cone-side/end-plane fit with a shared axis and cylinder limit."""

from __future__ import annotations

from typing import NotRequired, TypedDict

import numpy as np

from experiments.fit_coordinates import (
    AxisChart,
    CoordinateFrame,
    FitCoordinates,
    GlobalPlaneOffset,
)
from experiments.fit_solver import (
    SolverDiagnostics,
    normalized_weights,
    perpendicular_normal,
    solve_geometric_fit,
)
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
    solver: NotRequired[SolverDiagnostics]


class InvalidConeDomain(ValueError):
    """The declared side interval or positive-radius geometry is invalid."""


def valid_cone_geometry(
    parameters: Array, axial_domain: tuple[float, float], points: Array | None = None
) -> bool:
    lo, hi = axial_domain
    radius, taper = parameters[4], parameters[6]
    valid = bool(
        np.isfinite([lo, hi]).all()
        and lo < hi
        and np.isfinite(parameters).all()
        and min(radius, radius + taper * lo, radius + taper * hi) > 0
    )
    if not valid or points is None:
        return valid
    raw = np.array([parameters[2], parameters[3], 1.0])
    axis = raw / np.linalg.norm(raw)
    q = points - np.array([parameters[0], parameters[1], 0.0])
    z = q @ axis
    rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
    projected_z = (z + taper * (rho - radius)) / (1 + taper * taper)
    return bool(np.all(rho > 0) and np.all(radius + taper * projected_z > 0))


def cone_plane_residual_jacobian(
    cone: Array, plane: Array, parameters: Array, axial_domain: tuple[float, float]
) -> tuple[Array, Array]:
    """[cx,cy,a,b,R,h,k], radius(z)=R+k*z; orthogonal side and plane distances.

    z is measured along the unit axis from (cx,cy,0). The finite interval is
    side-surface support, not a cap-distance model or a constraint on fixed
    observation rows. Membership classification is separate from fitting.
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
    if np.any(radius + taper * projected_z <= 0):
        raise InvalidConeDomain("orthogonal side projection crosses the cone apex")
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
    weights = normalized_weights(weights)
    coordinates = FitCoordinates(
        CoordinateFrame.from_observations(np.vstack((cone, plane)), weights),
        7,
        axes=(AxisChart(0, 1, 2, 3, 4, 6),),
        global_planes=(GlobalPlaneOffset(5, perpendicular_normal(7)),),
    )
    fit = solve_geometric_fit(
        initial,
        weights,
        coordinates,
        lambda p: cone_plane_residual_jacobian(cone, plane, p, axial_domain),
        lambda p: valid_cone_geometry(p, axial_domain, cone),
    )
    residual = fit.residual
    cone_residual = residual[: len(cone)]
    plane_residual = residual[len(cone) :]
    return {
        "parameters": fit.parameters.tolist(),
        "objective_history": fit.objective_history,
        "weighted_rms": float(np.sqrt(weights @ (residual * residual))),
        "cone_weighted_rms": float(
            np.sqrt(normalized_weights(cone_area) @ cone_residual**2)
        ),
        "plane_weighted_rms": float(
            np.sqrt(normalized_weights(plane_area) @ plane_residual**2)
        ),
        "normal_matrix_condition": fit.condition,
        "gradient_infinity_norm": fit.gradient,
        "solver": fit.solver,
    }
