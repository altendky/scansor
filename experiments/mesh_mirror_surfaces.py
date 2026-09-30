"""Pairs of surfaces related by reflection in a plane containing a shared axis."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
)
from experiments.mesh_cylinder_fit import Array
from experiments.mesh_rotational_planes import lateral_point_gradient


@dataclass(frozen=True)
class MirrorSurfaces:
    points: tuple[Array, Array]
    areas: tuple[Array, Array]
    kind: str
    seed: tuple[float, ...]
    domain: tuple[float, float] = (-2.0, 5.0)
    other_domain: tuple[float, float] | None = None
    radius_side_index: int | None = None

    @property
    def surface_size(self) -> int:
        return {"plane": 3, "cylinder": 5, "cone": 6}[self.kind]

    @property
    def size(self) -> int:
        # One mirror-plane phase followed by one canonical surface.
        if self.radius_side_index is not None:
            if self.kind != "plane":
                raise ValueError("a shared-radius mirror group requires plane surfaces")
            return 1
        return 1 + self.surface_size

    def domain_for(self, slot: int) -> tuple[float, float]:
        if slot == 1 and self.other_domain is not None:
            return self.other_domain
        return self.domain


def axis_frame(parameters: Array) -> tuple[Array, Array, Array]:
    """Deterministic transverse frame; phase zero is the returned ``u`` direction."""
    axis = np.array([parameters[2], parameters[3], 1.0])
    axis /= np.linalg.norm(axis)
    reference = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = np.cross(axis, reference)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    return axis, u, v


def axis_frame_derivatives(
    parameters: Array,
) -> tuple[Array, Array, Array, Array, Array, Array]:
    """Exact derivatives within the currently selected transverse-frame branch.

    The existing least-aligned-reference choice is piecewise smooth. This does
    not assert differentiability where that discrete reference changes.
    """
    axis, u, v = axis_frame(parameters)
    length = float(np.linalg.norm([parameters[2], parameters[3], 1.0]))
    derivative = np.asarray(
        [(np.eye(3)[index] - axis * axis[index]) / length for index in range(2)],
        dtype=np.float64,
    )
    reference = np.eye(3)[int(np.argmin(np.abs(axis)))]
    cross_length = float(np.linalg.norm(np.cross(axis, reference)))
    dcross = np.asarray(np.cross(derivative, reference), dtype=np.float64)
    du = (dcross - (dcross @ u)[:, None] * u) / cross_length
    dv = np.asarray(np.cross(derivative, u) + np.cross(axis, du), dtype=np.float64)
    return axis, u, v, derivative, du, dv


def mirror_transform_derivatives(
    parameters: Array, phase: float
) -> tuple[Array, Array, Array, Array]:
    """Matrix/normal and their derivatives with respect to slopes and phase."""
    _, u, v, _, du, dv = axis_frame_derivatives(parameters)
    matrix, _, normal = mirror_transform(parameters, phase)
    derivative = np.vstack(
        [
            np.cos(phase) * dv - np.sin(phase) * du,
            -np.sin(phase) * v - np.cos(phase) * u,
        ]
    )
    matrices = np.asarray(
        [-2.0 * (np.outer(dn, normal) + np.outer(normal, dn)) for dn in derivative],
        dtype=np.float64,
    )
    return matrix, normal, matrices, derivative


def mirror_transform(parameters: Array, phase: float) -> tuple[Array, Array, Array]:
    """Return reflection matrix, radial plane direction, and plane normal."""
    axis, u, v = axis_frame(parameters)
    radial = np.cos(phase) * u + np.sin(phase) * v
    normal = np.cross(axis, radial)
    matrix = np.eye(3) - 2 * np.outer(normal, normal)
    return (
        np.asarray(matrix, dtype=np.float64),
        np.asarray(radial, dtype=np.float64),
        np.asarray(normal, dtype=np.float64),
    )


def mirror_plane(parameters: Array, offset: int) -> tuple[Array, Array]:
    """Return global ``[nx, ny, nz, d]`` plane equation and in-plane direction."""
    _, radial, normal = mirror_transform(parameters, float(parameters[offset]))
    center = np.array([parameters[0], parameters[1], 0.0])
    return np.array([*normal, normal @ center]), radial


def _lateral_parameters(group: MirrorSurfaces, parameters: Array, offset: int) -> Array:
    p = np.zeros(7)
    p[:5] = parameters[offset + 1 : offset + 6]
    if group.kind == "cone":
        p[6] = parameters[offset + 6]
    return p


def _plane_parameters(
    parameters: Array, offset: int
) -> tuple[Array, Array, Array, float]:
    axis, u, v = axis_frame(parameters)
    tilt, phase, distance = parameters[offset + 1 : offset + 4]
    radial = np.cos(phase) * u + np.sin(phase) * v
    tangent = -np.sin(phase) * u + np.cos(phase) * v
    normal = np.cos(tilt) * axis + np.sin(tilt) * radial
    dtilt = -np.sin(tilt) * axis + np.cos(tilt) * radial
    dphase = np.sin(tilt) * tangent
    return normal, dtilt, dphase, float(distance)


def _local_points(points: Array, parameters: Array, offset: int, slot: int) -> Array:
    if slot == 0:
        return points
    center = np.array([parameters[0], parameters[1], 0.0])
    matrix = mirror_transform(parameters, float(parameters[offset]))[0]
    return center + (points - center) @ matrix


def _residuals(
    group: MirrorSurfaces,
    parameters: Array,
    offset: int,
    radius_parameter_index: int | None = None,
) -> list[Array]:
    residuals: list[Array] = []
    if group.radius_side_index is not None:
        if group.kind != "plane" or radius_parameter_index is None:
            raise ValueError(
                "a shared-radius mirror group requires a cylinder radius parameter"
            )
        center = np.array([parameters[0], parameters[1], 0.0])
        normal = mirror_transform(parameters, float(parameters[offset]))[2]
        radius = float(parameters[radius_parameter_index])
        for slot, points in enumerate(group.points):
            sign = 1.0 if slot == 0 else -1.0
            residuals.append(sign * ((points - center) @ normal) - radius)
        return residuals
    if group.kind == "plane":
        normal, _, _, distance = _plane_parameters(parameters, offset)
        center = np.array([parameters[0], parameters[1], 0.0])
        for slot, points in enumerate(group.points):
            local = _local_points(points, parameters, offset, slot)
            residuals.append((local - center) @ normal - distance)
        return residuals
    p = _lateral_parameters(group, parameters, offset)
    for slot, points in enumerate(group.points):
        local = _local_points(points, parameters, offset, slot)
        residuals.append(
            cone_plane_residual_jacobian(
                local, np.empty((0, 3)), p, group.domain_for(slot)
            )[0]
        )
    return residuals


def mirrored_lateral(
    group: MirrorSurfaces, parameters: Array, offset: int, slot: int
) -> tuple[Array, tuple[float, float]]:
    """Reflect a lateral surface and rebase its axial chart at global z=0."""
    p = _lateral_parameters(group, parameters, offset)
    if slot == 0:
        return p, group.domain_for(slot)
    matrix = mirror_transform(parameters, float(parameters[offset]))[0]
    center = np.array([parameters[0], parameters[1], 0.0])
    point = center + matrix @ (np.array([p[0], p[1], 0.0]) - center)
    local_axis = np.array([p[2], p[3], 1.0])
    local_axis /= np.linalg.norm(local_axis)
    axis = matrix @ local_axis
    if abs(axis[2]) < 1e-8:
        raise ValueError(
            "mirrored surface axis is outside the current z-axis parameter chart"
        )
    shift = -point[2] / axis[2]
    origin = point + shift * axis
    sign = 1.0 if axis[2] > 0 else -1.0
    domain = sorted(sign * (z - shift) for z in group.domain_for(slot))
    return (
        np.array(
            [
                origin[0],
                origin[1],
                axis[0] / axis[2],
                axis[1] / axis[2],
                p[4] + p[6] * shift,
                0.0,
                sign * p[6],
            ]
        ),
        (domain[0], domain[1]),
    )


def mirror_surface_equations(
    group: MirrorSurfaces,
    parameters: Array,
    offset: int,
    radius_parameter_index: int | None = None,
) -> list[Array]:
    if group.radius_side_index is not None:
        if group.kind != "plane" or radius_parameter_index is None:
            raise ValueError(
                "a shared-radius mirror group requires a cylinder radius parameter"
            )
        center = np.array([parameters[0], parameters[1], 0.0])
        normal = mirror_transform(parameters, float(parameters[offset]))[2]
        radius = float(parameters[radius_parameter_index])
        return [
            np.array([*(sign * normal), radius + sign * normal @ center])
            for sign in (1.0, -1.0)
        ]
    if group.kind != "plane":
        return [
            mirrored_lateral(group, parameters, offset, slot)[0] for slot in range(2)
        ]
    normal, _, _, distance = _plane_parameters(parameters, offset)
    matrix = mirror_transform(parameters, float(parameters[offset]))[0]
    center = np.array([parameters[0], parameters[1], 0.0])
    normals = (normal, matrix @ normal)
    return [np.array([*n, distance + n @ center]) for n in normals]


def mirror_residual_jacobian(
    group: MirrorSurfaces,
    parameters: Array,
    offset: int,
    radius_parameter_index: int | None = None,
) -> tuple[list[Array], list[Array], list[Array]]:
    """Exact tied-surface residuals and analytic transform/surface derivatives."""
    residuals = _residuals(group, parameters, offset, radius_parameter_index)
    jacobians: list[Array] = []
    center = np.array([parameters[0], parameters[1], 0.0])
    matrix, mirror_normal, dmatrix, dmirror_normal = mirror_transform_derivatives(
        parameters, float(parameters[offset])
    )
    if group.radius_side_index is not None:
        if radius_parameter_index is None:
            raise ValueError(
                "a shared-radius mirror group requires a cylinder radius parameter"
            )
        for slot, points in enumerate(group.points):
            sign = 1.0 if slot == 0 else -1.0
            q = points - center
            jac = np.zeros((len(points), len(parameters)))
            jac[:, radius_parameter_index] = -1.0
            jac[:, :2] = -sign * mirror_normal[:2]
            jac[:, 2:4] = sign * q @ dmirror_normal[:2].T
            jac[:, offset] = sign * q @ dmirror_normal[2]
            jacobians.append(jac)
    elif group.kind == "plane":
        normal, dtilt, dphase, _ = _plane_parameters(parameters, offset)
        _, _, _, daxis, du, dv = axis_frame_derivatives(parameters)
        tilt, phase = parameters[offset + 1 : offset + 3]
        dnormal = np.cos(tilt) * daxis + np.sin(tilt) * (
            np.cos(phase) * du + np.sin(phase) * dv
        )
        for slot, points in enumerate(group.points):
            local = _local_points(points, parameters, offset, slot)
            q = local - center
            jac = np.zeros((len(points), len(parameters)))
            jac[:, offset + 1] = q @ dtilt
            jac[:, offset + 2] = q @ dphase
            jac[:, offset + 3] = -1.0
            jac[:, :2] = -(normal if slot == 0 else matrix @ normal)[:2]
            jac[:, 2:4] = q @ dnormal.T
            if slot == 1:
                for column, derivative in zip((2, 3, offset), dmatrix, strict=True):
                    jac[:, column] += (points - center) @ derivative @ normal
            jacobians.append(jac)
    else:
        p = _lateral_parameters(group, parameters, offset)
        columns = [0, 1, 2, 3, 4, 6][: group.surface_size]
        for slot, points in enumerate(group.points):
            local = _local_points(points, parameters, offset, slot)
            _, full = cone_plane_residual_jacobian(
                local, np.empty((0, 3)), p, group.domain_for(slot)
            )
            jac = np.zeros((len(points), len(parameters)))
            jac[:, offset + 1 : offset + 1 + group.surface_size] = full[:, columns]
            if slot == 1:
                gradient = lateral_point_gradient(local, p)
                jac[:, :2] = gradient @ (np.eye(3) - matrix)[:2].T
                for column, derivative in zip((2, 3, offset), dmatrix, strict=True):
                    jac[:, column] = np.sum(
                        gradient * ((points - center) @ derivative), axis=1
                    )
            jacobians.append(jac)
    return (
        residuals,
        jacobians,
        mirror_surface_equations(group, parameters, offset, radius_parameter_index),
    )


def initial_mirror(
    group: MirrorSurfaces,
    parameters: Array,
    phase_radians: float | None = None,
) -> Array:
    """Initialize one group; explicit and inferred phases are expressed in radians."""
    seed = np.asarray(group.seed, dtype=float)
    if group.radius_side_index is not None:
        if group.kind != "plane" or seed.shape != (4,) or not np.isfinite(seed).all():
            raise ValueError(
                "a shared-radius mirror group requires a fitted plane seed"
            )
        if phase_radians is not None:
            if not np.isfinite(phase_radians):
                raise ValueError("mirror phase must be finite")
            return np.array([phase_radians])
        axis, u, v = axis_frame(parameters)
        normal = seed[:3].copy()
        normal /= np.linalg.norm(normal)
        normal -= (normal @ axis) * axis
        length = float(np.linalg.norm(normal))
        if length <= 1e-10:
            raise ValueError(
                "parallel mirror planes require a seed normal transverse to the axis"
            )
        normal /= length
        # mirror_transform defines the plane normal as cos(phase)*v - sin(phase)*u.
        return np.array([np.arctan2(-(normal @ u), normal @ v)])
    if group.kind == "plane":
        if seed.shape != (4,) or not np.isfinite(seed).all():
            raise ValueError("mirror planes require a fitted plane seed")
        axis, u, v = axis_frame(parameters)
        normal, intercept = seed[:3].copy(), float(seed[3])
        if normal @ axis < 0:
            normal, intercept = -normal, -intercept
        center = np.array([parameters[0], parameters[1], 0.0])
        canonical = np.array(
            [
                np.arccos(np.clip(normal @ axis, -1.0, 1.0)),
                np.arctan2(normal @ v, normal @ u),
                intercept - normal @ center,
            ]
        )
    else:
        if seed.shape != (7,) or not np.isfinite(seed).all():
            raise ValueError("mirror lateral surfaces require a fitted seed")
        canonical = seed[[0, 1, 2, 3, 4, 6]][: group.surface_size]

    if phase_radians is not None:
        if not np.isfinite(phase_radians):
            raise ValueError("mirror phase must be finite")
        return np.array([phase_radians, *canonical])

    # A standalone fit of the first member supplies the canonical surface. Search
    # the one-dimensional, pi-periodic mirror-plane phase against the second.
    def objective(phase: float) -> float:
        trial = np.concatenate((parameters[:4], [phase], canonical))
        temporary = MirrorSurfaces(
            group.points,
            group.areas,
            group.kind,
            group.seed,
            group.domain,
            group.other_domain,
            group.radius_side_index,
        )
        try:
            residual = _residuals(temporary, trial, 4)[1]
        except InvalidConeDomain:
            return float("inf")
        weights = group.areas[1]
        return float(weights @ residual**2 / weights.sum())

    count = 180
    spacing = np.pi / count
    grid = np.arange(count) * spacing
    values = np.array([objective(float(phase)) for phase in grid])
    if not np.isfinite(values).any():
        raise ValueError("could not initialize mirror phase from the supplied pair")
    best = float(grid[int(np.nanargmin(values))])
    lo, hi = best - spacing, best + spacing
    ratio = (np.sqrt(5.0) - 1.0) / 2.0
    left = hi - ratio * (hi - lo)
    right = lo + ratio * (hi - lo)
    for _ in range(24):
        if objective(left) <= objective(right):
            hi, right = right, left
            left = hi - ratio * (hi - lo)
        else:
            lo, left = left, right
            right = lo + ratio * (hi - lo)
    return np.array([(lo + hi) / 2.0, *canonical])
