"""Joint geometric fitting across axis, datum, and plane relationship boundaries.

All observation distances are centered and length-scaled. Local axis/datum and
authored relationship equations are hard equalities, not weighted residuals.
The graph adapter owns membership, initialization stages, and publication.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_solver import normalized_weights
from experiments.mesh_cone_plane_fit import valid_cone_geometry
from scansor.constrained_least_squares import solve_constrained_least_squares

Array = NDArray[np.float64]


@dataclass(frozen=True)
class AxisInput:
    result: dict[str, Any]
    free: bool
    source: str | None = None


@dataclass(frozen=True)
class PointInput:
    result: dict[str, Any]
    free: bool
    source: str | None = None


@dataclass(frozen=True)
class SurfaceInput:
    id: str
    kind: str
    points: Array
    weights: Array
    result: dict[str, Any]
    axis: str | None = None
    datum: str | None = None
    construction: str | None = None
    point: str | None = None


def fit_relationship_component(
    surfaces: list[SurfaceInput],
    axes: dict[str, AxisInput],
    relationships: list[tuple[str, list[str]]],
    radii: list[list[str]],
    centers: dict[str, PointInput] | None = None,
) -> dict[str, Any]:
    """Fit planes and lateral surfaces while retaining every declared equality.

    Free axes use the existing positive-Z chart. Fixed axes may have any
    direction. A referenced plane has one equation shared by all its fits.
    Parallel-to-axis planes retain an independent offset; contains-axis planes
    do not. Opposite plane orientations are reconciled together with offsets.
    """
    all_weights = np.concatenate([surface.weights for surface in surfaces])
    frame = CoordinateFrame.from_observations(
        np.concatenate([surface.points for surface in surfaces]), all_weights
    )
    origin, length = frame.origin, frame.length
    initial: list[float] = []
    centers = centers or {}
    point_slots: dict[str, int] = {}
    fixed_points: dict[str, Array] = {}
    for key, center in centers.items():
        if center.source is not None:
            continue
        point = (np.asarray(center.result["point_display"]) - origin) / length
        if center.free:
            point_slots[key] = len(initial)
            initial.extend(point.tolist())
        else:
            fixed_points[key] = point
    axis_slots: dict[str, int] = {}
    fixed_axes: dict[str, tuple[Array, Array]] = {}
    for key, axis in axes.items():
        if axis.source is not None:
            continue
        value = axis.result
        direction = np.asarray(value["axis_display"], dtype=float)
        direction /= np.linalg.norm(direction)
        point = (np.asarray(value["point_display"], dtype=float) - origin) / length
        if axis.free:
            parameters = np.asarray(value["parameters"], dtype=float)
            axis_slots[key] = len(initial)
            a, b = parameters[2:4]
            initial.extend(
                [
                    (parameters[0] + a * origin[2] - origin[0]) / length,
                    (parameters[1] + b * origin[2] - origin[1]) / length,
                    a,
                    b,
                ]
            )
        else:
            if direction[2] < 0:
                direction = -direction
            if abs(direction[2]) > 1e-12:
                point -= point[2] / direction[2] * direction
            fixed_axes[key] = (point, direction)
    plane_slots: dict[str, int] = {}
    plane_keys: dict[str, str] = {}
    side_slots: dict[str, int] = {}
    for surface in surfaces:
        value = surface.result
        if surface.kind == "plane":
            key = surface.datum or surface.id
            plane_keys[surface.id] = key
            if key in plane_slots:
                continue
            equation_value = value.get("plane_equation")
            if equation_value is None:
                parameters = np.asarray(value["parameters"], dtype=float)
                if len(parameters) == 4:
                    equation_value = parameters
                else:
                    normal = np.array([parameters[2], parameters[3], 1.0])
                    normal /= np.linalg.norm(normal)
                    equation_value = [*normal, parameters[5]]
            equation = np.asarray(equation_value, dtype=float)
            equation /= np.linalg.norm(equation[:3])
            plane_slots[key] = len(initial)
            initial.extend(
                [*equation[:3], (equation[3] - equation[:3] @ origin) / length]
            )
        elif surface.kind in {"cylinder", "cone"}:
            if surface.axis not in axes:
                raise ValueError("coupled lateral fits require an evaluated axis")
            parameters = np.asarray(value["parameters"], dtype=float)
            taper = parameters[6] if surface.kind == "cone" else 0.0
            shift = origin[2] * np.sqrt(1 + parameters[2] ** 2 + parameters[3] ** 2)
            side_slots[surface.id] = len(initial)
            initial.append((parameters[4] + taper * shift) / length)
            if surface.kind == "cone":
                initial.append(float(taper))
        elif surface.kind == "sphere":
            if surface.point not in centers:
                raise ValueError("coupled sphere fits require an evaluated center")
            side_slots[surface.id] = len(initial)
            initial.append(surface.result["parameters"][3] / length)
        else:
            raise ValueError(f"unsupported coupled surface: {surface.kind}")
    size = len(initial)
    identity = np.eye(size)

    def axis_geometry(key: str, p: Array) -> tuple[Array, Array, Array, Array]:
        source = axes[key].source
        if source is not None:
            return axis_geometry(source, p)
        if key in fixed_axes:
            point, direction = fixed_axes[key]
            return point, direction, np.zeros((3, size)), np.zeros((3, size))
        start = axis_slots[key]
        point = np.array([p[start], p[start + 1], 0.0])
        raw = np.array([p[start + 2], p[start + 3], 1.0])
        norm = float(np.linalg.norm(raw))
        direction = raw / norm
        point_j = np.zeros((3, size))
        point_j[:2, start : start + 2] = np.eye(2)
        direction_j = np.zeros((3, size))
        direction_j[:, start + 2 : start + 4] = (
            np.eye(3) - np.outer(direction, direction)
        )[:, :2] / norm
        return point, direction, point_j, direction_j

    def plane_geometry(key: str, p: Array) -> tuple[Array, float, Array, Array]:
        start = plane_slots[key]
        return (
            p[start : start + 3],
            float(p[start + 3]),
            identity[start : start + 3],
            identity[start + 3],
        )

    def point_geometry(key: str, p: Array) -> tuple[Array, Array]:
        source = centers[key].source
        if source is not None:
            return point_geometry(source, p)
        if key in fixed_points:
            return fixed_points[key], np.zeros((3, size))
        start = point_slots[key]
        return p[start : start + 3], identity[start : start + 3]

    points = {surface.id: (surface.points - origin) / length for surface in surfaces}
    square_weights = np.sqrt(normalized_weights(all_weights))

    def observations(p: Array) -> tuple[Array, Array]:
        residuals: list[Array] = []
        jacobians: list[Array] = []
        for surface in surfaces:
            xyz = points[surface.id]
            if surface.kind == "plane":
                normal, offset, normal_j, offset_j = plane_geometry(
                    plane_keys[surface.id], p
                )
                residuals.append(xyz @ normal - offset)
                jacobians.append(xyz @ normal_j - offset_j)
                continue
            if surface.kind == "sphere":
                assert surface.point is not None
                point, point_j = point_geometry(surface.point, p)
                q = xyz - point
                distance = np.linalg.norm(q, axis=1)
                if np.any(distance <= 0):
                    raise ValueError("sphere observations must not lie at the center")
                start = side_slots[surface.id]
                residuals.append(distance - p[start])
                jacobians.append(-(q / distance[:, None]) @ point_j - identity[start])
                continue
            assert surface.axis is not None
            point, direction, point_j, direction_j = axis_geometry(surface.axis, p)
            q = xyz - point
            z = q @ direction
            radial = q - z[:, None] * direction
            rho = np.linalg.norm(radial, axis=1)
            if np.any(rho <= 0):
                raise ValueError("lateral observations must not lie on the axis")
            radial_unit = radial / rho[:, None]
            rho_j = -radial_unit @ point_j - (z[:, None] * radial_unit) @ direction_j
            z_j = q @ direction_j - direction @ point_j
            start = side_slots[surface.id]
            radius = p[start]
            taper = p[start + 1] if surface.kind == "cone" else 0.0
            scale = float(np.hypot(1.0, taper))
            error = rho - radius - taper * z
            jacobian = (rho_j - identity[start] - taper * z_j) / scale
            if surface.kind == "cone":
                jacobian[:, start + 1] += -z / scale - error * taper / scale**3
            residuals.append(error / scale)
            jacobians.append(jacobian)
        return np.concatenate(residuals) * square_weights, np.vstack(
            jacobians
        ) * square_weights[:, None]

    # Choose signs per normal-equivalence class, not independently per edge:
    # pairwise initial dot products can create an impossible odd sign cycle.
    p0 = np.asarray(initial)
    parents = {key: key for key in plane_slots}

    def root(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for _, group in relationships:
        anchor = root(plane_keys[group[0]])
        for key in group[1:]:
            parents[root(plane_keys[key])] = anchor
    perpendicular_keys: dict[str, str] = {}
    for surface in surfaces:
        if (
            surface.kind == "plane"
            and surface.axis is not None
            and surface.construction == "perpendicular_to_axis"
        ):
            key = plane_keys[surface.id]
            other = perpendicular_keys.setdefault(surface.axis, key)
            parents[root(key)] = root(other)
    orientations = {
        key: 1.0
        if plane_geometry(key, p0)[0] @ plane_geometry(root(key), p0)[0] >= 0
        else -1.0
        for key in plane_slots
    }
    relation_signs = [
        [
            orientations[plane_keys[group[0]]] * orientations[plane_keys[key]]
            for key in group[1:]
        ]
        for _, group in relationships
    ]

    # Start each parallel-normal class coherently. This is initialization only;
    # the equality solver still checks feasibility and fits all observations.
    # Independent near-opposite seeds can otherwise trap Newton projection on
    # incompatible orientation branches despite feasible unoriented planes.
    for representative in dict.fromkeys(root(key) for key in plane_slots):
        keys = [key for key in plane_slots if root(key) == representative]
        # Parallel planes share a normal, not necessarily an offset. Center
        # each datum's evidence separately before combining its scatter. This
        # gives an unoriented seed even when initial signed normals disagree.
        scatter = np.zeros((3, 3))
        for key in keys:
            members = [
                surface for surface in surfaces if plane_keys.get(surface.id) == key
            ]
            xyz = np.concatenate([points[surface.id] for surface in members])
            weights = np.concatenate([surface.weights for surface in members])
            weights = weights / np.max(all_weights)
            center = normalized_weights(weights) @ xyz
            centered = xyz - center
            scatter += centered.T @ (weights[:, None] * centered)
        _, vectors = np.linalg.eigh(scatter)
        normal = vectors[:, 0]
        if normal @ plane_geometry(representative, p0)[0] < 0:
            normal = -normal
        for surface in surfaces:
            if (
                surface.kind == "plane"
                and surface.axis in fixed_axes
                and surface.construction == "perpendicular_to_axis"
                and plane_keys[surface.id] in keys
            ):
                candidate = fixed_axes[surface.axis][1]
                normal = candidate if normal @ candidate >= 0 else -candidate
                break
        for key in keys:
            start = plane_slots[key]
            p0[start : start + 3] = orientations[key] * normal
        for axis, key in perpendicular_keys.items():
            if key in keys and axis in axis_slots and abs(normal[2]) > 1e-8:
                start = axis_slots[axis]
                p0[start + 2 : start + 4] = normal[:2] / normal[2]
    for surface in surfaces:
        if surface.kind == "plane":
            key = plane_keys[surface.id]
            normal = plane_geometry(key, p0)[0]
            offset = normalized_weights(surface.weights) @ (points[surface.id] @ normal)
            if surface.axis is not None and surface.construction == "contains_axis":
                offset = normal @ axis_geometry(surface.axis, p0)[0]
            p0[plane_slots[key] + 3] = offset
        elif surface.kind == "cone":
            assert surface.axis is not None
            direction = axis_geometry(surface.axis, p0)[1]
            taper = p0[side_slots[surface.id] + 1]
            p0[side_slots[surface.id]] = (
                surface.result["parameters"][4] + taper * origin[2] / direction[2]
            ) / length

    def equalities(p: Array) -> tuple[Array, Array]:
        values: list[Array] = []
        jacobians: list[Array] = []

        def append(value: float | Array, jacobian: Array) -> None:
            values.append(np.atleast_1d(value))
            jacobians.append(np.atleast_2d(jacobian))

        for key in plane_slots:
            normal, _, normal_j, _ = plane_geometry(key, p)
            append((normal @ normal - 1) / 2, normal @ normal_j)
        bound: set[str] = set()
        for surface in surfaces:
            if surface.kind != "plane" or surface.axis is None:
                continue
            key = plane_keys[surface.id]
            if key in bound:
                continue
            bound.add(key)
            point, direction, point_j, direction_j = axis_geometry(surface.axis, p)
            normal, offset, normal_j, offset_j = plane_geometry(key, p)
            if surface.construction == "perpendicular_to_axis":
                # Cross product has rank two; the solver handles redundancy.
                append(
                    np.cross(normal, direction),
                    np.cross(normal_j.T, direction).T
                    + np.cross(normal, direction_j.T).T,
                )
            else:
                append(normal @ direction, direction @ normal_j + normal @ direction_j)
                if surface.construction == "contains_axis":
                    append(
                        offset - normal @ point,
                        offset_j - point @ normal_j - normal @ point_j,
                    )
        for (relation, group), signs in zip(relationships, relation_signs, strict=True):
            normal, offset, normal_j, offset_j = plane_geometry(plane_keys[group[0]], p)
            for key, sign in zip(group[1:], signs, strict=True):
                other, other_offset, other_j, other_offset_j = plane_geometry(
                    plane_keys[key], p
                )
                append(normal - sign * other, normal_j - sign * other_j)
                if relation == "coincident":
                    append(
                        offset - sign * other_offset, offset_j - sign * other_offset_j
                    )
        for group in radii:
            first = side_slots[group[0]]
            for key in group[1:]:
                other = side_slots[key]
                append(p[first] - p[other], identity[first] - identity[other])
        return np.concatenate(values), np.vstack(jacobians)

    def valid(p: Array) -> bool:
        if not np.isfinite(p).all():
            return False
        for surface in surfaces:
            if surface.kind == "plane":
                continue
            if surface.kind == "sphere":
                if p[side_slots[surface.id]] <= 0:
                    return False
                continue
            assert surface.axis is not None
            _, direction, _, _ = axis_geometry(surface.axis, p)
            start = side_slots[surface.id]
            taper = p[start + 1] if surface.kind == "cone" else 0.0
            if abs(direction[2]) < 1e-12:
                return False
            radius = length * p[start] - taper * origin[2] / direction[2]
            domain = surface.result["axial_domain"]
            if min(radius, radius + taper * domain[0], radius + taper * domain[1]) <= 0:
                return False
            point, _, _, _ = axis_geometry(surface.axis, p)
            world_point = origin + length * point
            world_point -= world_point[2] / direction[2] * direction
            physical = np.array(
                [*world_point[:2], *(direction[:2] / direction[2]), radius, 0.0, taper]
            )
            if not valid_cone_geometry(physical, tuple(domain), surface.points):
                return False
        return True

    fitted = solve_constrained_least_squares(
        p0,
        observations,
        equalities,
        parameter_scales=np.ones(size),
        geometry_is_valid=valid,
    )
    parameters = np.asarray(fitted.parameters)
    final_constraints, _ = equalities(parameters)
    if np.max(np.abs(final_constraints), initial=0.0) > 1e-10:
        raise ValueError("joint geometry does not satisfy its declared relationships")
    output_axes: dict[str, dict[str, Any]] = {}
    for key, axis in axes.items():
        point, direction, _, _ = axis_geometry(key, parameters)
        world_point = origin + length * point
        result = deepcopy(axis.result)
        if axis.free or axis.source is not None:
            world_point -= world_point[2] / direction[2] * direction
            result["parameters"] = [
                *world_point[:2].tolist(),
                *(direction[:2] / direction[2]).tolist(),
                *result["parameters"][4:],
            ]
            if result.get("direction_reversed"):
                direction = -direction
            result.update(
                point_display=world_point.tolist(),
                axis_display=direction.tolist(),
                resolved_by="plane_relationship",
            )
        output_axes[key] = result
    output_surfaces: dict[str, dict[str, Any]] = {}
    output_points: dict[str, dict[str, Any]] = {
        key: {
            **deepcopy(center.result),
            "point_display": (
                np.asarray(origin + length * point_geometry(key, parameters)[0])
            ).tolist(),
            "resolved_by": "plane_relationship",
        }
        for key, center in centers.items()
    }
    for value in output_points.values():
        if "coordinates" in value:
            value["coordinates"] = value["point_display"].copy()
    equations: dict[str, list[float]] = {}
    for surface in surfaces:
        result = deepcopy(surface.result)
        if surface.kind == "plane":
            normal, offset, _, _ = plane_geometry(plane_keys[surface.id], parameters)
            equation = [*map(float, normal), float(length * offset + normal @ origin)]
            equations[plane_keys[surface.id]] = equation
            result.update(parameters=equation, plane_equation=equation)
            residual = surface.points @ normal - equation[3]
        elif surface.kind == "sphere":
            assert surface.point is not None
            point = np.asarray(output_points[surface.point]["point_display"])
            radius = length * parameters[side_slots[surface.id]]
            result["parameters"] = [*point, radius]
            residual = np.linalg.norm(surface.points - point, axis=1) - radius
        else:
            assert surface.axis is not None
            point, direction, _, _ = axis_geometry(surface.axis, parameters)
            world_point = origin + length * point
            world_point -= world_point[2] / direction[2] * direction
            if direction[2] < 0:
                direction = -direction
            start = side_slots[surface.id]
            taper = parameters[start + 1] if surface.kind == "cone" else 0.0
            radius = length * parameters[start] - taper * origin[2] / direction[2]
            result["parameters"] = [
                *world_point[:2],
                *(direction[:2] / direction[2]),
                radius,
                result["parameters"][5],
                taper,
            ]
            q = surface.points - world_point
            z = q @ direction
            residual = (
                np.linalg.norm(q - z[:, None] * direction, axis=1) - radius - taper * z
            ) / np.hypot(1.0, taper)
        result.update(
            residuals=np.asarray(residual).tolist(),
            weighted_rms=float(
                np.sqrt(normalized_weights(surface.weights) @ residual**2)
            ),
            condition=fitted.condition,
            resolved_by="plane_relationship",
        )
        output_surfaces[surface.id] = result
    return {
        "surfaces": output_surfaces,
        "axes": output_axes,
        "points": output_points,
        "equations": equations,
        "solver": {
            "termination": fitted.termination,
            "iterations": fitted.iterations,
            "constraint_violation": fitted.constraint_violation,
            "projected_gradient_norm": fitted.projected_gradient_norm,
        },
        "weighted_rms": length * np.sqrt(2 * fitted.objective_history[-1]),
    }
