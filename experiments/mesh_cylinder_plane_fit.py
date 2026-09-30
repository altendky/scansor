"""Exploratory joint cylinder/end-plane fit with exact shared-axis geometry."""

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
from experiments.mesh_cylinder_fit import (
    Array,
    residual_jacobian,
    valid_cylinder_geometry,
)


class CylinderPlaneFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    cylinder_weighted_rms: float
    plane_weighted_rms: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    solver: NotRequired[SolverDiagnostics]


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
    weights = normalized_weights(weights)
    coordinates = FitCoordinates(
        CoordinateFrame.from_observations(np.vstack((cylinder, plane)), weights),
        6,
        axes=(AxisChart(0, 1, 2, 3, 4),),
        global_planes=(GlobalPlaneOffset(5, perpendicular_normal(6)),),
    )
    fit = solve_geometric_fit(
        initial,
        weights,
        coordinates,
        lambda p: joint_residual_jacobian(cylinder, plane, p),
        lambda p: valid_cylinder_geometry(cylinder, p[:5]),
    )
    residual = fit.residual
    cylinder_residual = residual[: len(cylinder)]
    plane_residual = residual[len(cylinder) :]
    return {
        "parameters": fit.parameters.tolist(),
        "objective_history": fit.objective_history,
        "weighted_rms": float(np.sqrt(weights @ (residual * residual))),
        "cylinder_weighted_rms": float(
            np.sqrt(normalized_weights(cylinder_area) @ cylinder_residual**2)
        ),
        "plane_weighted_rms": float(
            np.sqrt(normalized_weights(plane_area) @ plane_residual**2)
        ),
        "normal_matrix_condition": fit.condition,
        "gradient_infinity_norm": fit.gradient,
        "solver": fit.solver,
    }
