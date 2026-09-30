"""Bounded joint solve: coaxial cone/cylinder sides and perpendicular planes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.fit_coordinates import (
    AxisChart,
    CoordinateFrame,
    FitCoordinates,
    GlobalPlaneOffset,
    NormalCallback,
    RelativePlaneOffset,
)
from experiments.fit_solver import (
    SolverDiagnostics,
    normalized_weights,
    perpendicular_normal,
    solve_geometric_fit,
)
from experiments.mesh_cone_plane_fit import (
    cone_plane_residual_jacobian,
    valid_cone_geometry,
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
    solver: SolverDiagnostics


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
    normal, derivative = axis_plane_normal(group, size)(parameters)
    anchor = np.array([parameters[0], parameters[1], 0.0])
    plane_offset = float(normal @ anchor) if offset is None else parameters[offset]
    residual = group.points @ normal - plane_offset
    if offset is None:
        jacobian = (group.points - anchor) @ derivative
        jacobian[:, 0] = -normal[0]
        jacobian[:, 1] = -normal[1]
    else:
        jacobian = group.points @ derivative
    if offset is not None:
        jacobian[:, offset] = -1.0
    return residual, jacobian


def axis_plane_normal(group: AxisPlaneObservations, size: int) -> NormalCallback:
    """Differentiate the declared clocked frame, without world-origin steps."""

    def normal(parameters: Array) -> tuple[Array, Array]:
        axis, derivative = perpendicular_normal(size)(parameters)
        if group.construction == "perpendicular_to_axis":
            # Retain frame validity checks even though only the normal is used.
            _ = axis_plane_frame(group, parameters)
            return axis, derivative
        u, v, result = axis_plane_frame(group, parameters)
        # For axial planes axis_plane_frame returns (axis, radial, normal).
        basis = np.eye(3)[group.basis_index]
        cross = np.cross(axis, basis)
        cross_length = float(np.linalg.norm(cross))
        transverse = cross / cross_length
        dtransverse = np.cross(derivative.T, basis).T
        dtransverse = (
            dtransverse - transverse[:, None] * (transverse @ dtransverse)
        ) / cross_length
        dsecond = np.cross(derivative.T, transverse).T + np.cross(axis, dtransverse.T).T
        assert group.angle_radians is not None
        dradial = (
            np.cos(group.angle_radians) * dtransverse
            + np.sin(group.angle_radians) * dsecond
        )
        dnormal = np.asarray(
            np.cross(derivative.T, v).T + np.cross(u, dradial.T).T,
            dtype=np.float64,
        )
        return result, dnormal

    return normal


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
    weights = normalized_weights(weights)
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
    axes = [
        AxisChart(0, 1, 2, 3, mapping[4], mapping[6] if side.kind == "cone" else None)
        for side, mapping in zip(sides, maps, strict=True)
    ]
    shared_axis = axes[0]
    global_planes = [
        GlobalPlaneOffset(offset, perpendicular_normal(size))
        for offset in (
            *((4,) if plane is not None else ()),
            *range(base_size, plane_size),
        )
    ]
    global_planes.extend(
        GlobalPlaneOffset(offset, axis_plane_normal(group, size))
        for group, offset in zip(axis_planes, axis_plane_offsets, strict=True)
        if offset is not None
    )
    relative_planes: list[RelativePlaneOffset] = []
    for i, group in enumerate(rotations):
        offset = axis_plane_size + sum(g.size for g in rotations[:i])
        if group.kind == "plane":
            relative_planes.append(RelativePlaneOffset(offset + 2, shared_axis, offset))
        else:
            axes.append(
                AxisChart(
                    offset,
                    offset + 1,
                    offset + 2,
                    offset + 3,
                    offset + 4,
                    offset + 5 if group.kind == "cone" else None,
                )
            )
    for i, group in enumerate(mirrors):
        offset = mirror_offset + sum(g.size for g in mirrors[:i])
        if group.radius_side_index is not None:
            continue
        if group.kind == "plane":
            relative_planes.append(
                RelativePlaneOffset(offset + 3, shared_axis, offset + 1)
            )
        else:
            axes.append(
                AxisChart(
                    offset + 1,
                    offset + 2,
                    offset + 3,
                    offset + 4,
                    offset + 5,
                    offset + 6 if group.kind == "cone" else None,
                )
            )
    coordinates = FitCoordinates(
        CoordinateFrame.from_observations(all_points, weights),
        size,
        axes=tuple(axes),
        global_planes=tuple(global_planes),
        relative_planes=tuple(relative_planes),
    )

    def geometry_is_valid(parameters: Array) -> bool:
        if not np.isfinite(parameters).all():
            return False
        for side, mapping in zip(sides, maps, strict=True):
            p = unpack(parameters, mapping, side.kind)
            if not valid_cone_geometry(p, side.domain, side.points):
                return False
        try:
            for group in axis_planes:
                _ = axis_plane_frame(group, parameters)
            for i, group in enumerate(rotations):
                if group.kind == "plane":
                    continue
                offset = axis_plane_size + sum(g.size for g in rotations[:i])
                for slot in range(3):
                    p, domain = rotated_lateral(group, parameters, offset, slot)
                    if not valid_cone_geometry(p, domain, group.points[slot]):
                        return False
            for i, group in enumerate(mirrors):
                if group.kind == "plane":
                    continue
                offset = mirror_offset + sum(g.size for g in mirrors[:i])
                for slot in range(2):
                    p, domain = mirrored_lateral(group, parameters, offset, slot)
                    if not valid_cone_geometry(p, domain, group.points[slot]):
                        return False
        except ValueError:
            # Transformed axes may leave the explicit positive-Z chart.
            return False
        return True

    fitted = solve_geometric_fit(
        initial,
        weights,
        coordinates,
        lambda p: residual_jacobian(
            sides, plane, p, extra_points, rotations, mirrors, axis_planes
        ),
        geometry_is_valid,
    )
    parameters, residual = fitted.parameters, fitted.residual
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
        objective_history=fitted.objective_history,
        weighted_rms=float(np.sqrt(weights @ residual**2)),
        condition=fitted.condition,
        gradient=fitted.gradient,
        solver=fitted.solver,
    )
