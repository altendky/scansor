"""Bounded joint solve: coaxial cone/cylinder sides and perpendicular planes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
)
from experiments.mesh_cylinder_fit import Array
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
class CoaxialResult:
    # Each side uses the existing [cx,cy,a,b,R,h,k] convention. The first four
    # parameters are exactly shared. h denotes the first plane; all planes have
    # separate offsets and the same normal.
    parameters: list[Array]
    residuals: list[Array]
    plane_residuals: Array
    plane_offsets: list[float]
    extra_plane_residuals: list[Array]
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
    sides: list[SideObservations],
    plane: Array,
    parameters: Array,
    extra_planes: tuple[Array, ...] = (),
    rotations: tuple[RotationalPlanes, ...] = (),
    mirrors: tuple[MirrorSurfaces, ...] = (),
) -> tuple[Array, Array]:
    maps, base_size = parameter_maps(sides)
    plane_size = base_size + len(extra_planes)
    rotation_size = sum(group.size for group in rotations)
    size = plane_size + rotation_size + sum(group.size for group in mirrors)
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
    for points, offset in zip(
        (plane, *extra_planes), (4, *range(base_size, plane_size)), strict=True
    ):
        jac = np.zeros((len(points), size))
        jac[:, 2] = points @ ((np.array([1.0, 0, 0]) - axis * axis[0]) / length)
        jac[:, 3] = points @ ((np.array([0.0, 1, 0]) - axis * axis[1]) / length)
        jac[:, offset] = -1
        residuals.append(points @ axis - parameters[offset])
        jacobians.append(jac)
    for i, group in enumerate(rotations):
        r, j, _ = rotation_residual_jacobian(
            group, parameters, plane_size + sum(g.size for g in rotations[:i])
        )
        residuals.extend(r)
        jacobians.extend(j)
    mirror_offset = plane_size + rotation_size
    for i, group in enumerate(mirrors):
        r, j, _ = mirror_residual_jacobian(
            group, parameters, mirror_offset + sum(g.size for g in mirrors[:i])
        )
        residuals.extend(r)
        jacobians.extend(j)
    return np.concatenate(residuals), np.vstack(jacobians)


def fit_coaxial(
    sides: list[SideObservations],
    plane: Array,
    plane_area: Array,
    initial: Array,
    extra_planes: tuple[tuple[Array, Array], ...] = (),
    rotations: tuple[RotationalPlanes, ...] = (),
    mirrors: tuple[MirrorSurfaces, ...] = (),
) -> CoaxialResult:
    """Area-weighted simultaneous solve, exact axis sharing, fixed memberships."""
    if not sides:
        raise ValueError("at least one lateral surface is required")
    maps, base_size = parameter_maps(sides)
    plane_size = base_size + len(extra_planes)
    rotation_size = sum(group.size for group in rotations)
    mirror_offset = plane_size + rotation_size
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
    for points, area in [
        *((s.points, s.area) for s in sides),
        (plane, plane_area),
        *extra_planes,
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
            plane_area,
            *(area for _, area in extra_planes),
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
            sides, plane, parameters, extra_points, rotations, mirrors
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
                    sides, plane, candidate, extra_points, rotations, mirrors
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
                    plane,
                    *extra_points,
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
        sides, plane, parameters, extra_points, rotations, mirrors
    )
    boundaries = np.cumsum(
        [
            *(len(s.points) for s in sides),
            len(plane),
            *(len(points) for points in extra_points),
            *(len(points) for group in rotations for points in group.points),
            *(len(points) for group in mirrors for points in group.points),
        ]
    )[:-1]
    blocks = np.split(residual, boundaries)
    return CoaxialResult(
        parameters=[
            unpack(parameters, m, s.kind) for m, s in zip(maps, sides, strict=True)
        ],
        residuals=blocks[: len(sides)],
        plane_residuals=blocks[len(sides)],
        plane_offsets=[
            float(parameters[i]) for i in (4, *range(base_size, plane_size))
        ],
        extra_plane_residuals=blocks[
            len(sides) + 1 : len(sides) + 1 + len(extra_planes)
        ],
        rotational_equations=[
            rotation_residual_jacobian(
                group, parameters, plane_size + sum(g.size for g in rotations[:i])
            )[2]
            for i, group in enumerate(rotations)
        ],
        rotational_domains=[
            [
                rotated_lateral(
                    group,
                    parameters,
                    plane_size + sum(g.size for g in rotations[:i]),
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
                len(sides) + 1 + len(extra_planes) + 3 * i : len(sides)
                + 1
                + len(extra_planes)
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
                len(sides) + 1 + len(extra_planes) + 3 * len(rotations) + 2 * i : len(
                    sides
                )
                + 1
                + len(extra_planes)
                + 3 * len(rotations)
                + 2 * i
                + 2
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
