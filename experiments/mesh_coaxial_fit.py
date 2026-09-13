"""Bounded joint solve: coaxial cone/cylinder sides and one perpendicular plane."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
)
from experiments.mesh_cylinder_fit import Array


@dataclass(frozen=True)
class SideObservations:
    points: Array
    area: Array
    kind: str
    domain: tuple[float, float]


@dataclass(frozen=True)
class CoaxialResult:
    # Each side uses the existing [cx,cy,a,b,R,h,k] convention. The first four
    # parameters and plane offset are exactly shared, never fitted independently.
    parameters: list[Array]
    residuals: list[Array]
    plane_residuals: Array
    objective_history: list[float]
    weighted_rms: float
    condition: float
    gradient: float


def parameter_maps(sides: list[SideObservations]) -> tuple[list[list[int]], int]:
    offset = 5  # shared cx, cy, a, b, h
    maps: list[list[int]] = []
    for side in sides:
        if side.kind not in ("cone", "cylinder"):
            raise ValueError("coaxial sides must be cones or cylinders")
        maps.append([0, 1, 2, 3, offset, 4, offset + 1])
        offset += 2 if side.kind == "cone" else 1
    return maps, offset


def unpack(parameters: Array, mapping: list[int], kind: str) -> Array:
    p = np.zeros(7)
    p[:6] = parameters[mapping[:6]]
    if kind == "cone":
        p[6] = parameters[mapping[6]]
    return p


def residual_jacobian(
    sides: list[SideObservations], plane: Array, parameters: Array
) -> tuple[Array, Array]:
    maps, size = parameter_maps(sides)
    if parameters.shape != (size,):
        raise ValueError("incorrect joint parameter count")
    residuals: list[Array] = []
    jacobians: list[Array] = []
    for side, mapping in zip(sides, maps, strict=True):
        p = unpack(parameters, mapping, side.kind)
        residual, local = cone_plane_residual_jacobian(
            side.points, np.empty((0, 3)), p, side.domain
        )
        jac = np.zeros((len(residual), size))
        count = 7 if side.kind == "cone" else 6
        jac[:, mapping[:count]] = local[:, :count]
        residuals.append(residual)
        jacobians.append(jac)
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    jac = np.zeros((len(plane), size))
    jac[:, 2] = plane @ ((np.array([1.0, 0, 0]) - axis * axis[0]) / length)
    jac[:, 3] = plane @ ((np.array([0.0, 1, 0]) - axis * axis[1]) / length)
    jac[:, 4] = -1
    residuals.append(plane @ axis - parameters[4])
    jacobians.append(jac)
    return np.concatenate(residuals), np.vstack(jacobians)


def fit_coaxial(
    sides: list[SideObservations], plane: Array, plane_area: Array, initial: Array
) -> CoaxialResult:
    """Area-weighted simultaneous solve, exact axis sharing, fixed memberships."""
    if not sides:
        raise ValueError("at least one lateral surface is required")
    maps, size = parameter_maps(sides)
    for points, area in [*((s.points, s.area) for s in sides), (plane, plane_area)]:
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("each surface needs at least three XYZ observations")
        if area.shape != (len(points),) or not np.isfinite(points).all():
            raise ValueError("invalid point or area array")
        if not np.isfinite(area).all() or np.any(area <= 0):
            raise ValueError("areas must be finite and positive")
    if initial.shape != (size,) or not np.isfinite(initial).all():
        raise ValueError("invalid initial parameters")
    weights = np.concatenate([*(s.area for s in sides), plane_area])
    total = float(weights.sum())
    if not np.isfinite(total):
        raise ValueError("nonfinite total area")
    weights /= total
    parameters = initial.copy()
    history: list[float] = []
    for _ in range(80):
        residual, jac = residual_jacobian(sides, plane, parameters)
        objective = float(weights @ residual**2)
        history.append(objective)
        normal = jac.T @ (weights[:, None] * jac)
        condition = float(np.linalg.cond(normal))
        if not np.isfinite(condition) or condition > 1e12:
            raise ValueError("ill-conditioned joint geometry in this parameter frame")
        gradient = jac.T @ (weights * residual)
        step = np.linalg.solve(normal, -gradient)
        if np.max(np.abs(step)) < 1e-10 * max(1.0, float(np.max(np.abs(parameters)))):
            break
        for power in range(25):
            candidate = parameters + step * 2.0**-power
            try:
                r, _ = residual_jacobian(sides, plane, candidate)
            except InvalidConeDomain:
                continue
            if float(weights @ r**2) < objective:
                parameters = candidate
                break
        else:
            raise ValueError("joint fit failed to decrease objective")
    else:
        raise ValueError("joint fit did not converge")
    residual, jac = residual_jacobian(sides, plane, parameters)
    boundaries = np.cumsum([len(s.points) for s in sides])
    blocks = np.split(residual, boundaries)
    return CoaxialResult(
        parameters=[
            unpack(parameters, m, s.kind) for m, s in zip(maps, sides, strict=True)
        ],
        residuals=blocks[:-1],
        plane_residuals=blocks[-1],
        objective_history=history,
        weighted_rms=float(np.sqrt(weights @ residual**2)),
        condition=float(np.linalg.cond(jac.T @ (weights[:, None] * jac))),
        gradient=float(np.max(np.abs(jac.T @ (weights * residual)))),
    )
