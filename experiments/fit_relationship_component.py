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
from experiments.fit_solver import AttemptControl, normalized_weights
from experiments.mesh_cone_plane_fit import valid_cone_geometry
from experiments.support_intent import (
    PointSupport,
    SelectionSupport,
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    resolve_support,
    support_from_result,
    support_record,
)
from experiments.surface_primitives import basis, revolution_geometry
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


@dataclass(frozen=True)
class _AxisChart:
    origin: Array
    basis: Array


class RelationshipSupportFailure(SupportIntentFailure):
    """An explicitly declared support cannot be represented by this axis."""


def fit_relationship_component(
    surfaces: list[SurfaceInput],
    axes: dict[str, AxisInput],
    relationships: list[tuple[str, list[str]]],
    radii: list[list[str]],
    centers: dict[str, PointInput] | None = None,
    *,
    control: AttemptControl | None = None,
) -> dict[str, Any]:
    """Fit planes and lateral surfaces while retaining every declared equality.

    Canonical inputs use a separate local chart for each free axis. Wholly legacy
    inputs retain their positive-Z charts and publication. Fixed axes may have
    any direction. A referenced plane has one equation shared by all its fits.
    Parallel-to-axis planes retain an independent offset; contains-axis planes
    do not. Opposite plane orientations are reconciled together with offsets.
    """
    if control is not None:
        control.checkpoint()
    legacy = not any(
        "analytic" in surface.result or "support_intent" in surface.result
        for surface in surfaces
    ) and all(
        "parameters" in axis.result and not axis.result.get("axis_is_directed", False)
        for axis in axes.values()
    )

    def root_axis(key: str) -> str:
        seen: set[str] = set()
        while axes[key].source is not None:
            if key in seen:
                raise ValueError("axis provider aliases contain a cycle")
            seen.add(key)
            source = axes[key].source
            assert source is not None
            if source not in axes:
                raise ValueError("axis provider alias is unavailable")
            key = source
        return key

    axis_roots = {key: root_axis(key) for key in axes}
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
    axis_charts: dict[str, _AxisChart] = {}
    fixed_axes: dict[str, tuple[Array, Array]] = {}
    for key, axis in axes.items():
        if axis.source is not None:
            continue
        value = axis.result
        direction = np.array(value["axis_display"], dtype=float)
        direction /= np.linalg.norm(direction)
        point = (np.asarray(value["point_display"], dtype=float) - origin) / length
        if axis.free:
            axis_slots[key] = len(initial)
            if legacy:
                parameters = np.asarray(value["parameters"], dtype=float)
                a, b = parameters[2:4]
                axis_charts[key] = _AxisChart(np.zeros(3), np.eye(3))
                initial.extend(
                    [
                        (parameters[0] + a * origin[2] - origin[0]) / length,
                        (parameters[1] + b * origin[2] - origin[1]) / length,
                        a,
                        b,
                    ]
                )
            else:
                first, second = basis(direction)
                axis_charts[key] = _AxisChart(
                    point, np.column_stack((first, second, direction))
                )
                initial.extend([0.0, 0.0, 0.0, 0.0])
        else:
            if legacy:
                if direction[2] < 0:
                    direction = -direction
                if abs(direction[2]) > 1e-12:
                    point -= point[2] / direction[2] * direction
            fixed_axes[key] = (point, direction)
    plane_slots: dict[str, int] = {}
    plane_keys: dict[str, str] = {}
    side_slots: dict[str, int] = {}
    lateral_geometry: dict[str, dict[str, Any]] = {}
    lateral_support: dict[str, SupportIntent] = {}
    lateral_references: dict[str, Array] = {}
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
            side_slots[surface.id] = len(initial)
            if legacy:
                parameters = np.asarray(value["parameters"], dtype=float)
                taper = parameters[6] if surface.kind == "cone" else 0.0
                shift = origin[2] * np.sqrt(1 + parameters[2] ** 2 + parameters[3] ** 2)
                initial.append((parameters[4] + taper * shift) / length)
            else:
                geometry = revolution_geometry(value)
                lateral_geometry[surface.id] = geometry
                lateral_support[surface.id] = support_from_result(value)
                lateral_references[surface.id] = np.asarray(
                    value.get(
                        "anchor_reference",
                        origin if "support_intent" in value else geometry["origin"],
                    ),
                    dtype=float,
                )
                reference_axis = np.asarray(
                    axes[axis_roots[surface.axis]].result["axis_display"]
                )
                sign = 1.0 if reference_axis @ geometry["axis"] >= 0 else -1.0
                taper = geometry["slope"] * sign
                # Rebased again after coherent plane/axis initialization below.
                initial.append(geometry["radius"] / length)
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
        chart = axis_charts[key]
        point = chart.origin + chart.basis[:, :2] @ p[start : start + 2]
        raw = np.array([p[start + 2], p[start + 3], 1.0])
        norm = float(np.linalg.norm(raw))
        local_direction = raw / norm
        direction = chart.basis @ local_direction
        point_j = np.zeros((3, size))
        point_j[:, start : start + 2] = chart.basis[:, :2]
        direction_j = np.zeros((3, size))
        direction_j[:, start + 2 : start + 4] = (
            chart.basis
            @ (np.eye(3) - np.outer(local_direction, local_direction))[:, :2]
            / norm
        )
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
            other = perpendicular_keys.setdefault(axis_roots[surface.axis], key)
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
                and surface.axis is not None
                and axis_roots[surface.axis] in fixed_axes
                and surface.construction == "perpendicular_to_axis"
                and plane_keys[surface.id] in keys
            ):
                candidate = fixed_axes[axis_roots[surface.axis]][1]
                normal = candidate if normal @ candidate >= 0 else -candidate
                break
        for key in keys:
            start = plane_slots[key]
            p0[start : start + 3] = orientations[key] * normal
        for axis, key in perpendicular_keys.items():
            if key in keys and axis in axis_slots:
                start = axis_slots[axis]
                local = axis_charts[axis].basis.T @ normal
                if abs(local[2]) > 1e-8:
                    p0[start + 2 : start + 4] = local[:2] / local[2]
    for surface in surfaces:
        if surface.kind == "plane":
            key = plane_keys[surface.id]
            normal = plane_geometry(key, p0)[0]
            offset = normalized_weights(surface.weights) @ (points[surface.id] @ normal)
            if surface.axis is not None and surface.construction == "contains_axis":
                offset = normal @ axis_geometry(surface.axis, p0)[0]
            p0[plane_slots[key] + 3] = offset
        elif not legacy and surface.kind in {"cone", "cylinder"}:
            assert surface.axis is not None
            point, direction, _, _ = axis_geometry(surface.axis, p0)
            geometry = lateral_geometry[surface.id]
            shift = (origin + length * point - geometry["origin"]) @ direction
            taper = p0[side_slots[surface.id] + 1] if surface.kind == "cone" else 0.0
            p0[side_slots[surface.id]] = (geometry["radius"] + taper * shift) / length
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
        if not values:
            return np.empty(0), np.empty((0, size))
        return np.concatenate(values), np.vstack(jacobians)

    def canonical_surface(surface: SurfaceInput, p: Array) -> dict[str, Any]:
        """Publish about a semantic anchor, independent of the numerical chart."""
        assert surface.axis is not None
        point, direction, _, _ = axis_geometry(surface.axis, p)
        chart_point = origin + length * point
        reference = lateral_references[surface.id]
        if reference.shape != (3,) or not np.isfinite(reference).all():
            raise ValueError("surface anchor reference requires finite XYZ")
        anchor = chart_point + ((reference - chart_point) @ direction) * direction
        start = side_slots[surface.id]
        taper = p[start + 1] if surface.kind == "cone" else 0.0
        radius = length * p[start] + taper * ((anchor - chart_point) @ direction)
        support = lateral_support[surface.id]
        # Selection extent has no authored direction, but preserve the source
        # surface's existing representation independently of a reversed datum.
        if (
            isinstance(support, SelectionSupport)
            and direction @ lateral_geometry[surface.id]["axis"] < 0
        ):
            direction, taper = -direction, -taper
        try:
            resolved = resolve_support(
                support,
                anchor,
                direction,
                surface.points,
                reference=reference,
            )
        except SupportIntentFailure as error:
            raise RelationshipSupportFailure(str(error), code=error.code) from error
        return {
            "kind": surface.kind,
            "origin": list(resolved.origin),
            "axis": list(resolved.axis),
            "radius": float(radius + taper * resolved.origin_shift),
            "slope": float(resolved.axis_sign * taper),
            "axial_domain": list(resolved.domain),
        }

    def valid(p: Array) -> bool:
        if control is not None:
            control.checkpoint()
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
            if not legacy:
                try:
                    geometry = canonical_surface(surface, p)
                except SupportIntentFailure:
                    return False
                radius, taper = geometry["radius"], geometry["slope"]
                domain = geometry["axial_domain"]
                if (
                    domain is None
                    or not np.isfinite(
                        [radius, taper, *domain, *geometry["origin"], *geometry["axis"]]
                    ).all()
                    or domain[0] >= domain[1]
                ):
                    return False
                with np.errstate(over="ignore", invalid="ignore"):
                    end_radii = radius + taper * np.asarray(domain)
                if not np.isfinite(end_radii).all() or np.any(end_radii <= 0):
                    return False
                point, direction, _, _ = axis_geometry(surface.axis, p)
                relative = points[surface.id] - point
                radial = relative - (relative @ direction)[:, None] * direction
                rho = np.linalg.norm(radial, axis=1)
                if np.any(rho <= 0):
                    return False
                if surface.kind == "cone":
                    start = side_slots[surface.id]
                    local_taper = p[start + 1]
                    scale = float(np.hypot(1.0, local_taper))
                    # Positive finite support does not establish that every
                    # observation projects onto the positive cone sheet.
                    with np.errstate(over="ignore", invalid="ignore"):
                        projected_radius = (
                            p[start] / scale / scale
                            + (local_taper / scale) * ((relative @ direction) / scale)
                            + (local_taper / scale) ** 2 * rho
                        )
                    if not np.isfinite(projected_radius).all() or np.any(
                        projected_radius <= 0
                    ):
                        return False
                continue
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

    if not legacy:
        # Reject an unrepresentable authored support before entering callbacks;
        # later trial axes crossing its chart boundary are ordinary invalid steps.
        for surface in surfaces:
            if surface.kind in {"cylinder", "cone"}:
                _ = canonical_surface(surface, p0)
    fitted = solve_constrained_least_squares(
        p0,
        observations if control is None else lambda p: control.call(observations, p),
        equalities if control is None else lambda p: control.call(equalities, p),
        parameter_scales=np.ones(size),
        geometry_is_valid=valid,
        max_iterations=100 if control is None else control.limits.max_iterations,
        max_trials=32 if control is None else control.limits.max_trials,
    )
    parameters = np.asarray(fitted.parameters)
    final_constraints, _ = (
        equalities(parameters)
        if control is None
        else control.call(equalities, parameters)
    )
    if np.max(np.abs(final_constraints), initial=0.0) > 1e-10:
        raise ValueError("joint geometry does not satisfy its declared relationships")
    output_axes: dict[str, dict[str, Any]] = {}
    for key, axis in axes.items():
        point, direction, _, _ = axis_geometry(key, parameters)
        world_point = origin + length * point
        result = deepcopy(axis.result)
        if not legacy:
            provider = axes[axis_roots[key]].result
            sign = (
                1.0
                if np.asarray(axis.result["axis_display"])
                @ np.asarray(provider["axis_display"])
                >= 0
                else -1.0
            )
            _ = result.pop("parameters", None)
            result.update(
                point_display=world_point.tolist(),
                axis_display=(sign * direction).tolist(),
                axis_is_directed=True,
                resolved_by="plane_relationship",
            )
        elif axis.free or axis.source is not None:
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
            if legacy:
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
            else:
                geometry = canonical_surface(surface, parameters)
                result["analytic"] = geometry
                result["axial_domain"] = geometry["axial_domain"]
                support = lateral_support[surface.id]
                result["support_intent"] = support_record(support)
                result["anchor_reference"] = lateral_references[surface.id].tolist()
                if isinstance(support, SelectionSupport):
                    result["support_origin"] = "selection"
                    if support.reference is not None:
                        result["anchor_reference"] = list(support.reference)
                elif isinstance(support, PointSupport):
                    result["support_origin"] = "point"
                    result["anchor_reference"] = list(support.reference)
                    result["anchor_axial_domain"] = list(support.interval)
                    result["anchor_axis"] = list(support.direction)
                else:
                    source_domain = surface.result.get("source_axial_domain")
                    legacy_alias = (
                        surface.result.get("support_origin") == "legacy-source-z0"
                        and source_domain is not None
                        and support == legacy_support(tuple(source_domain))
                    )
                    if not legacy_alias:
                        result["support_origin"] = "reference-plane"
                        _ = result.pop("source_axial_domain", None)
                _ = result.pop("parameters", None)
                world_point = np.asarray(geometry["origin"])
                direction = np.asarray(geometry["axis"])
                radius, taper = geometry["radius"], geometry["slope"]
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
            "evaluations": fitted.evaluations,
            "constraint_evaluations": fitted.constraint_evaluations,
            "rank": fitted.rank,
            "constraint_rank": fitted.constraint_rank,
            "tangent_dimension": fitted.tangent_dimension,
            "condition": fitted.condition,
            "objective_history": list(fitted.objective_history),
            "coordinate_origin": origin.tolist(),
            "coordinate_length": length,
            "constraint_violation": fitted.constraint_violation,
            "projected_gradient_norm": fitted.projected_gradient_norm,
        },
        "weighted_rms": length * np.sqrt(2 * fitted.objective_history[-1]),
    }
