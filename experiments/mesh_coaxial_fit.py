"""Bounded joint solve: coaxial cone/cylinder sides and perpendicular planes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
)
from experiments.mesh_cylinder_fit import Array
from experiments.mesh_cylinder_fit import residual_jacobian as cylinder_residual
from experiments.mesh_mirror_surfaces import (
    MirrorSurfaces,
    mirror_plane,
    mirror_residual_jacobian,
    mirror_surface_equations,
    mirrored_lateral,
)
from experiments.mesh_rotational_planes import (
    RotationalPlanes,
    rotated_lateral,
    rotation_residual_jacobian,
)


@dataclass(frozen=True)
class SideObservations:
    points: Array
    area: Array
    kind: str
    domain: tuple[float, float]


@dataclass(frozen=True)
class AxisPlaneObservations:
    """Plane observations whose orientation is constructed from the shared axis."""

    points: Array
    area: Array
    construction: str
    angle_radians: float | None
    basis_index: int

    @property
    def size(self) -> int:
        return 0 if self.construction == "contains_axis" else 1


@dataclass(frozen=True)
class CoaxialResult:
    # Each side uses the existing [cx,cy,a,b,R,h,k] convention. The first four
    # parameters are exactly shared. h denotes the first plane; all planes have
    # separate offsets and the same normal.
    parameters: list[Array]
    residuals: list[Array]
    plane_residuals: Array
    plane_offsets: list[float]
    extra_plane_residuals: list[Array]
    axis_plane_equations: list[Array]
    axis_plane_basis_u: list[Array]
    axis_plane_basis_v: list[Array]
    axis_plane_residuals: list[Array]
    rotational_equations: list[list[Array]]
    rotational_domains: list[list[tuple[float, float]]]
    rotational_residuals: list[list[Array]]
    mirror_equations: list[list[Array]]
    mirror_domains: list[list[tuple[float, float]]]
    mirror_residuals: list[list[Array]]
    mirror_plane_equations: list[Array]
    mirror_plane_directions: list[Array]
    mirror_phases: list[float]
    objective_history: list[float]
    weighted_rms: float
    condition: float
    gradient: float


def parameter_maps(
    sides: list[SideObservations], *, has_primary_plane: bool = True
) -> tuple[list[list[int]], int]:
    offset = 5 if has_primary_plane else 4  # shared cx, cy, a, b[, h]
    plane_offset = 4 if has_primary_plane else -1
    maps: list[list[int]] = []
    for side in sides:
        if side.kind not in ("cone", "cylinder"):
            raise ValueError("coaxial sides must be cones or cylinders")
        maps.append([0, 1, 2, 3, offset, plane_offset, offset + 1])
        offset += 2 if side.kind == "cone" else 1
    return maps, offset


def unpack(parameters: Array, mapping: list[int], kind: str) -> Array:
    p = np.zeros(7)
    p[:5] = parameters[mapping[:5]]
    if mapping[5] >= 0:
        p[5] = parameters[mapping[5]]
    if kind == "cone":
        p[6] = parameters[mapping[6]]
    return p


def mirror_radius_parameter(
    group: MirrorSurfaces,
    sides: list[SideObservations],
    maps: list[list[int]],
) -> int | None:
    index = group.radius_side_index
    if index is None:
        return None
    if index < 0 or index >= len(sides) or sides[index].kind != "cylinder":
        raise ValueError("a shared-radius mirror group must reference a cylinder side")
    if group.kind != "plane":
        raise ValueError("a shared-radius mirror group requires plane surfaces")
    return maps[index][4]


def axis_plane_frame(
    group: AxisPlaneObservations, parameters: Array
) -> tuple[Array, Array, Array]:
    raw = np.array([parameters[2], parameters[3], 1.0])
    axis = raw / np.linalg.norm(raw)
    basis = np.eye(3)[group.basis_index]
    u = np.asarray(np.cross(axis, basis), dtype=float)
    u_length = float(np.linalg.norm(u))
    if u_length < 1e-8:
        raise ValueError("shared axis left the reference-plane parameter frame")
    u /= u_length
    v = np.asarray(np.cross(axis, u), dtype=float)
    if group.construction == "perpendicular_to_axis":
        return u, v, axis
    if group.construction not in ("contains_axis", "parallel_to_axis"):
        raise ValueError("unsupported reference-plane construction")
    if group.angle_radians is None:
        raise ValueError("an axial reference plane requires a clocking angle")
    radial = np.asarray(
        np.cos(group.angle_radians) * u + np.sin(group.angle_radians) * v,
        dtype=float,
    )
    normal = np.asarray(np.cross(axis, radial), dtype=float)
    return axis, radial, normal


def axis_plane_residual_jacobian(
    group: AxisPlaneObservations,
    parameters: Array,
    offset: int | None,
    size: int,
) -> tuple[Array, Array]:
    _, _, normal = axis_plane_frame(group, parameters)
    anchor = np.array([parameters[0], parameters[1], 0.0])
    plane_offset = float(normal @ anchor) if offset is None else parameters[offset]
    residual = group.points @ normal - plane_offset
    jacobian = np.zeros((len(group.points), size))
    columns = (0, 1, 2, 3) if offset is None else (2, 3)
    for column in columns:
        step = 1e-6 * max(1.0, abs(float(parameters[column])))
        delta = np.zeros_like(parameters)
        delta[column] = step
        plus_normal = axis_plane_frame(group, parameters + delta)[2]
        minus_normal = axis_plane_frame(group, parameters - delta)[2]
        if offset is None:
            plus_anchor = np.array(
                [parameters[0] + delta[0], parameters[1] + delta[1], 0.0]
            )
            minus_anchor = np.array(
                [parameters[0] - delta[0], parameters[1] - delta[1], 0.0]
            )
            plus = (group.points - plus_anchor) @ plus_normal
            minus = (group.points - minus_anchor) @ minus_normal
            jacobian[:, column] = (plus - minus) / (2 * step)
        else:
            jacobian[:, column] = group.points @ (
                (plus_normal - minus_normal) / (2 * step)
            )
    if offset is not None:
        jacobian[:, offset] = -1.0
    return residual, jacobian


def axis_plane_parameter_offsets(
    start: int, groups: tuple[AxisPlaneObservations, ...]
) -> tuple[list[int | None], int]:
    offsets: list[int | None] = []
    offset = start
    for group in groups:
        offsets.append(None if group.size == 0 else offset)
        offset += group.size
    return offsets, offset


def residual_jacobian(
    sides: list[SideObservations],
    plane: Array | None,
    parameters: Array,
    extra_planes: tuple[Array, ...] = (),
    rotations: tuple[RotationalPlanes, ...] = (),
    mirrors: tuple[MirrorSurfaces, ...] = (),
    axis_planes: tuple[AxisPlaneObservations, ...] = (),
) -> tuple[Array, Array]:
    maps, base_size = parameter_maps(sides, has_primary_plane=plane is not None)
    plane_size = base_size + len(extra_planes)
    axis_plane_offsets, axis_plane_size = axis_plane_parameter_offsets(
        plane_size, axis_planes
    )
    rotation_size = sum(group.size for group in rotations)
    size = axis_plane_size + rotation_size + sum(group.size for group in mirrors)
    if parameters.shape != (size,):
        raise ValueError("incorrect joint parameter count")
    residuals: list[Array] = []
    jacobians: list[Array] = []
    for side, mapping in zip(sides, maps, strict=True):
        p = unpack(parameters, mapping, side.kind)
        if side.kind == "cylinder":
            residual, cylinder_jacobian = cylinder_residual(side.points, p[:5])
            local = np.zeros((len(residual), 7))
            local[:, :5] = cylinder_jacobian
        else:
            residual, local = cone_plane_residual_jacobian(
                side.points, np.empty((0, 3)), p, side.domain
            )
        jac = np.zeros((len(residual), size))
        local_columns = [0, 1, 2, 3, 4, *([6] if side.kind == "cone" else [])]
        jac[:, [mapping[column] for column in local_columns]] = local[:, local_columns]
        residuals.append(residual)
        jacobians.append(jac)
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    plane_points = (*((plane,) if plane is not None else ()), *extra_planes)
    plane_offsets = (
        *((4,) if plane is not None else ()),
        *range(base_size, plane_size),
    )
    for points, offset in zip(plane_points, plane_offsets, strict=True):
        jac = np.zeros((len(points), size))
        jac[:, 2] = points @ ((np.array([1.0, 0, 0]) - axis * axis[0]) / length)
        jac[:, 3] = points @ ((np.array([0.0, 1, 0]) - axis * axis[1]) / length)
        jac[:, offset] = -1
        residuals.append(points @ axis - parameters[offset])
        jacobians.append(jac)
    for group, offset in zip(axis_planes, axis_plane_offsets, strict=True):
        residual, jacobian = axis_plane_residual_jacobian(
            group, parameters, offset, size
        )
        residuals.append(residual)
        jacobians.append(jacobian)
    for i, group in enumerate(rotations):
        r, j, _ = rotation_residual_jacobian(
            group,
            parameters,
            axis_plane_size + sum(g.size for g in rotations[:i]),
        )
        residuals.extend(r)
        jacobians.extend(j)
    mirror_offset = axis_plane_size + rotation_size
    for i, group in enumerate(mirrors):
        r, j, _ = mirror_residual_jacobian(
            group,
            parameters,
            mirror_offset + sum(g.size for g in mirrors[:i]),
            mirror_radius_parameter(group, sides, maps),
        )
        residuals.extend(r)
        jacobians.extend(j)
    return np.concatenate(residuals), np.vstack(jacobians)


def fit_coaxial(
    sides: list[SideObservations],
    plane: Array | None,
    plane_area: Array | None,
    initial: Array,
    extra_planes: tuple[tuple[Array, Array], ...] = (),
    rotations: tuple[RotationalPlanes, ...] = (),
    mirrors: tuple[MirrorSurfaces, ...] = (),
    axis_planes: tuple[AxisPlaneObservations, ...] = (),
) -> CoaxialResult:
    """Area-weighted simultaneous solve, exact axis sharing, fixed memberships."""
    if not sides:
        raise ValueError("at least one lateral surface is required")
    if (plane is None) != (plane_area is None):
        raise ValueError("primary plane points and areas must be supplied together")
    maps, base_size = parameter_maps(sides, has_primary_plane=plane is not None)
    plane_size = base_size + len(extra_planes)
    axis_plane_offsets, axis_plane_size = axis_plane_parameter_offsets(
        plane_size, axis_planes
    )
    rotation_size = sum(group.size for group in rotations)
    mirror_offset = axis_plane_size + rotation_size
    size = mirror_offset + sum(group.size for group in mirrors)
    extra_points = tuple(points for points, _ in extra_planes)
    for group in rotations:
        if len(group.points) != 3 or len(group.areas) != 3:
            raise ValueError(
                "rotational symmetry requires exactly three planes or same-type lateral surfaces"
            )
    for group in mirrors:
        if len(group.points) != 2 or len(group.areas) != 2:
            raise ValueError("mirror symmetry requires exactly two same-type surfaces")
        _ = mirror_radius_parameter(group, sides, maps)
    for points, area in [
        *((s.points, s.area) for s in sides),
        *(
            ((plane, plane_area),)
            if plane is not None and plane_area is not None
            else ()
        ),
        *extra_planes,
        *((group.points, group.area) for group in axis_planes),
        *(
            pair
            for group in rotations
            for pair in zip(group.points, group.areas, strict=True)
        ),
        *(
            pair
            for group in mirrors
            for pair in zip(group.points, group.areas, strict=True)
        ),
    ]:
        if points.ndim != 2 or points.shape[1] != 3 or len(points) < 3:
            raise ValueError("each surface needs at least three XYZ observations")
        if area.shape != (len(points),) or not np.isfinite(points).all():
            raise ValueError("invalid point or area array")
        if not np.isfinite(area).all() or np.any(area <= 0):
            raise ValueError("areas must be finite and positive")
    if initial.shape != (size,) or not np.isfinite(initial).all():
        raise ValueError("invalid initial parameters")
    weights = np.concatenate(
        [
            *(s.area for s in sides),
            *((plane_area,) if plane_area is not None else ()),
            *(area for _, area in extra_planes),
            *(group.area for group in axis_planes),
            *(area for group in rotations for area in group.areas),
            *(area for group in mirrors for area in group.areas),
        ]
    )
    total = float(weights.sum())
    if not np.isfinite(total):
        raise ValueError("nonfinite total area")
    weights /= total
    parameters = initial.copy()
    history: list[float] = []
    for _ in range(80):
        residual, jac = residual_jacobian(
            sides,
            plane,
            parameters,
            extra_points,
            rotations,
            mirrors,
            axis_planes,
        )
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
                r, _ = residual_jacobian(
                    sides,
                    plane,
                    candidate,
                    extra_points,
                    rotations,
                    mirrors,
                    axis_planes,
                )
            except InvalidConeDomain:
                continue
            if float(weights @ r**2) < objective:
                parameters = candidate
                break
        else:
            # Accept only roundoff-limited stagnation, not a material failed step.
            all_points = np.vstack(
                [
                    *(s.points for s in sides),
                    *((plane,) if plane is not None else ()),
                    *extra_points,
                    *(group.points for group in axis_planes),
                    *(points for group in rotations for points in group.points),
                    *(points for group in mirrors for points in group.points),
                ]
            )
            scale = float(weights @ np.sum(all_points**2, axis=1))
            resolution = 32 * np.finfo(float).eps * float(np.sqrt(objective * scale))
            if float(step @ normal @ step) <= resolution:
                break
            raise ValueError("joint fit failed to decrease objective")
    else:
        raise ValueError("joint fit did not converge")
    residual, jac = residual_jacobian(
        sides,
        plane,
        parameters,
        extra_points,
        rotations,
        mirrors,
        axis_planes,
    )
    boundaries = np.cumsum(
        [
            *(len(s.points) for s in sides),
            *((len(plane),) if plane is not None else ()),
            *(len(points) for points in extra_points),
            *(len(group.points) for group in axis_planes),
            *(len(points) for group in rotations for points in group.points),
            *(len(points) for group in mirrors for points in group.points),
        ]
    )[:-1]
    blocks = np.split(residual, boundaries)
    plane_block_offset = len(sides)
    primary_plane_residual = (
        blocks[plane_block_offset] if plane is not None else np.empty(0)
    )
    extra_plane_block_offset = plane_block_offset + (1 if plane is not None else 0)
    axis_plane_block_offset = extra_plane_block_offset + len(extra_planes)
    relationship_block_offset = axis_plane_block_offset + len(axis_planes)
    return CoaxialResult(
        parameters=[
            unpack(parameters, m, s.kind) for m, s in zip(maps, sides, strict=True)
        ],
        residuals=blocks[: len(sides)],
        plane_residuals=primary_plane_residual,
        plane_offsets=[
            float(parameters[i])
            for i in (
                *((4,) if plane is not None else ()),
                *range(base_size, plane_size),
            )
        ],
        extra_plane_residuals=blocks[
            extra_plane_block_offset : extra_plane_block_offset + len(extra_planes)
        ],
        axis_plane_equations=[
            np.array(
                [
                    *axis_plane_frame(group, parameters)[2],
                    (
                        axis_plane_frame(group, parameters)[2]
                        @ np.array([parameters[0], parameters[1], 0.0])
                        if offset is None
                        else parameters[offset]
                    ),
                ]
            )
            for group, offset in zip(axis_planes, axis_plane_offsets, strict=True)
        ],
        axis_plane_basis_u=[
            axis_plane_frame(group, parameters)[0] for group in axis_planes
        ],
        axis_plane_basis_v=[
            axis_plane_frame(group, parameters)[1] for group in axis_planes
        ],
        axis_plane_residuals=blocks[
            axis_plane_block_offset : axis_plane_block_offset + len(axis_planes)
        ],
        rotational_equations=[
            rotation_residual_jacobian(
                group,
                parameters,
                axis_plane_size + sum(g.size for g in rotations[:i]),
            )[2]
            for i, group in enumerate(rotations)
        ],
        rotational_domains=[
            [
                rotated_lateral(
                    group,
                    parameters,
                    axis_plane_size + sum(g.size for g in rotations[:i]),
                    slot,
                )[1]
                if group.kind != "plane"
                else group.domain
                for slot in range(3)
            ]
            for i, group in enumerate(rotations)
        ],
        rotational_residuals=[
            blocks[
                relationship_block_offset + 3 * i : relationship_block_offset
                + 3 * i
                + 3
            ]
            for i in range(len(rotations))
        ],
        mirror_equations=[
            mirror_surface_equations(
                group,
                parameters,
                mirror_offset + sum(g.size for g in mirrors[:i]),
                mirror_radius_parameter(group, sides, maps),
            )
            for i, group in enumerate(mirrors)
        ],
        mirror_domains=[
            [
                mirrored_lateral(
                    group,
                    parameters,
                    mirror_offset + sum(g.size for g in mirrors[:i]),
                    slot,
                )[1]
                if group.kind != "plane"
                else group.domain_for(slot)
                for slot in range(2)
            ]
            for i, group in enumerate(mirrors)
        ],
        mirror_residuals=[
            blocks[
                relationship_block_offset
                + 3 * len(rotations)
                + 2 * i : relationship_block_offset + 3 * len(rotations) + 2 * i + 2
            ]
            for i in range(len(mirrors))
        ],
        mirror_plane_equations=[
            mirror_plane(
                parameters,
                mirror_offset + sum(g.size for g in mirrors[:i]),
            )[0]
            for i in range(len(mirrors))
        ],
        mirror_plane_directions=[
            mirror_plane(
                parameters,
                mirror_offset + sum(g.size for g in mirrors[:i]),
            )[1]
            for i in range(len(mirrors))
        ],
        mirror_phases=[
            float(parameters[mirror_offset + sum(g.size for g in mirrors[:i])])
            for i in range(len(mirrors))
        ],
        objective_history=history,
        weighted_rms=float(np.sqrt(weights @ residual**2)),
        condition=float(np.linalg.cond(jac.T @ (weights[:, None] * jac))),
        gradient=float(np.max(np.abs(jac.T @ (weights * residual)))),
    )
