"""Three planes related by exact 120-degree rotations about a shared axis."""

from dataclasses import dataclass

import numpy as np

from experiments.mesh_cone_plane_fit import cone_plane_residual_jacobian
from experiments.mesh_cylinder_fit import Array


@dataclass(frozen=True)
class RotationalPlanes:
    points: tuple[Array, ...]
    areas: tuple[Array, ...]
    kind: str = "plane"
    seed: tuple[float, ...] = ()
    domain: tuple[float, float] = (-2.0, 5.0)

    @property
    def size(self) -> int:
        return {"plane": 3, "cylinder": 5, "cone": 6}[self.kind]


def axis_frame(parameters: Array) -> tuple[Array, Array, Array, Array, Array, Array]:
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    da = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
    db = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
    derivatives = np.stack([da, db])
    cross = np.cross(axis, [0.0, 1, 0])
    u = cross / np.linalg.norm(cross)
    dcross = np.cross(derivatives, [0.0, 1, 0])
    du = (dcross - (dcross @ u)[:, None] * u) / np.linalg.norm(cross)
    v = np.cross(axis, u)
    dv = np.cross(derivatives, u) + np.cross(axis, du)
    return axis, u, v, derivatives, du, dv


def initial_rotation(group: RotationalPlanes, parameters: Array) -> Array:
    """Back-rotate the declared slots and fit one common plane for initialization."""
    if group.kind != "plane":
        seed = np.array(group.seed)
        if seed.shape != (7,):
            raise ValueError(
                "rotational surfaces require a fitted seed of the original type"
            )
        return seed[[0, 1, 2, 3, 4, 6]][: group.size]
    axis, u, v, *_ = axis_frame(parameters)
    center = np.array([parameters[0], parameters[1], 0.0])
    local: list[Array] = []
    for slot, points in enumerate(group.points):
        angle = -slot * 2 * np.pi / 3
        q = points - center
        local.append(
            q * np.cos(angle)
            + np.cross(axis, q) * np.sin(angle)
            + (q @ axis)[:, None] * axis * (1 - np.cos(angle))
        )
    points = np.vstack(local)
    weights = np.concatenate(group.areas)
    weights = weights / weights.sum()
    mean = weights @ points
    q = points - mean
    values, vectors = np.linalg.eigh(q.T @ (weights[:, None] * q))
    if values[1] <= max(values[-1], 1e-30) * 1e-10:
        raise ValueError("rotational planes need wider two-dimensional coverage")
    normal = vectors[:, 0]
    if normal @ axis < 0:
        normal = -normal
    return np.array(
        [
            np.arccos(np.clip(normal @ axis, -1, 1)),
            np.arctan2(normal @ v, normal @ u),
            mean @ normal,
        ]
    )


def rotation_residual_jacobian(
    group: RotationalPlanes,
    parameters: Array,
    offset: int,
) -> tuple[list[Array], list[Array], list[Array]]:
    if group.kind != "plane":
        return lateral_rotation_residual_jacobian(group, parameters, offset)
    axis, u, v, da, du, dv = axis_frame(parameters)
    tilt, phase, distance = parameters[offset : offset + 3]
    center = np.array([parameters[0], parameters[1], 0.0])
    residuals: list[Array] = []
    jacobians: list[Array] = []
    equations: list[Array] = []
    for slot, points in enumerate(group.points):
        angle = phase + slot * 2 * np.pi / 3
        radial = np.cos(angle) * u + np.sin(angle) * v
        normal = np.cos(tilt) * axis + np.sin(tilt) * radial
        dn = np.cos(tilt) * da + np.sin(tilt) * (
            np.cos(angle) * du + np.sin(angle) * dv
        )
        q = points - center
        jac = np.zeros((len(points), len(parameters)))
        jac[:, 0] = -normal[0]
        jac[:, 1] = -normal[1]
        jac[:, 2:4] = q @ dn.T
        jac[:, offset] = q @ (-np.sin(tilt) * axis + np.cos(tilt) * radial)
        jac[:, offset + 1] = q @ (
            np.sin(tilt) * (-np.sin(angle) * u + np.cos(angle) * v)
        )
        jac[:, offset + 2] = -1
        residuals.append(q @ normal - distance)
        jacobians.append(jac)
        equations.append(np.array([*normal, distance + normal @ center]))
    return residuals, jacobians, equations


def rotation_matrix(parameters: Array, slot: int) -> Array:
    axis = axis_frame(parameters)[0]
    angle = slot * 2 * np.pi / 3
    x, y, z = axis
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return (
        np.eye(3) * np.cos(angle)
        + cross * np.sin(angle)
        + np.outer(axis, axis) * (1 - np.cos(angle))
    )


def lateral_parameters(
    group: RotationalPlanes, parameters: Array, offset: int
) -> Array:
    p = np.zeros(7)
    p[:5] = parameters[offset : offset + 5]
    if group.kind == "cone":
        p[6] = parameters[offset + 5]
    return p


def rotated_lateral(
    group: RotationalPlanes, parameters: Array, offset: int, slot: int
) -> tuple[Array, tuple[float, float]]:
    """Rigidly rotate a side, then change its axial reference to global z=0."""
    p = lateral_parameters(group, parameters, offset)
    matrix = rotation_matrix(parameters, slot)
    center = np.array([parameters[0], parameters[1], 0.0])
    point = center + matrix @ (np.array([p[0], p[1], 0.0]) - center)
    axis = matrix @ (np.array([p[2], p[3], 1.0]) / np.linalg.norm([p[2], p[3], 1.0]))
    if abs(axis[2]) < 1e-8:
        raise ValueError(
            "rotated surface axis is outside the current z-axis parameter chart"
        )
    shift = -point[2] / axis[2]
    origin = point + shift * axis
    sign = 1.0 if axis[2] > 0 else -1.0
    domain = sorted(sign * (z - shift) for z in group.domain)
    result = np.array(
        [
            origin[0],
            origin[1],
            axis[0] / axis[2],
            axis[1] / axis[2],
            p[4] + p[6] * shift,
            0,
            sign * p[6],
        ]
    )
    return result, (domain[0], domain[1])


def lateral_rotation_residual_jacobian(
    group: RotationalPlanes, parameters: Array, offset: int
) -> tuple[list[Array], list[Array], list[Array]]:
    p = lateral_parameters(group, parameters, offset)
    center = np.array([parameters[0], parameters[1], 0.0])
    residuals: list[Array] = []
    jacobians: list[Array] = []
    equations: list[Array] = []
    columns = [0, 1, 2, 3, 4, 6][: group.size]
    for slot, points in enumerate(group.points):
        # Back-rotate observations to one shared cylinder/cone. This keeps the
        # relationship exact while all four common-axis parameters can move.
        local = center + (points - center) @ rotation_matrix(parameters, slot)
        residual, full = cone_plane_residual_jacobian(
            local, np.empty((0, 3)), p, group.domain
        )
        jac = np.zeros((len(points), len(parameters)))
        jac[:, offset : offset + group.size] = full[:, columns]
        # Only the moving symmetry transform uses central differences. The
        # surface derivatives above remain analytic; test both against an
        # independent finite-difference step over the complete joint residual.
        for col in range(4):
            step = 1e-5 * max(1.0, abs(parameters[col]))
            values: list[Array] = []
            for direction in (1, -1):
                q = parameters.copy()
                q[col] += direction * step
                c = np.array([q[0], q[1], 0.0])
                moved = c + (points - c) @ rotation_matrix(q, slot)
                values.append(
                    cone_plane_residual_jacobian(
                        moved, np.empty((0, 3)), p, group.domain
                    )[0]
                )
            jac[:, col] = (values[0] - values[1]) / (2 * step)
        residuals.append(residual)
        jacobians.append(jac)
        equations.append(rotated_lateral(group, parameters, offset, slot)[0])
    return residuals, jacobians, equations
