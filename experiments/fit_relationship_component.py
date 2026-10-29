"""Joint geometric fitting across axis, datum, and plane relationship boundaries.

All observation distances are centered and length-scaled. Local axis/datum and
authored relationship equations are hard equalities, not weighted residuals.
The graph adapter owns membership, initialization stages, and publication.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast

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
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresFailure,
    ConstrainedLeastSquaresResult,
    solve_constrained_least_squares,
)

Array = NDArray[np.float64]
DirectionKind = Literal["axis", "plane"]
DirectionKey = tuple[DirectionKind, str]


@dataclass(frozen=True)
class AxisInput:
    """Axis roles; optional property freedoms override the legacy free flag."""

    result: dict[str, Any]
    free: bool
    source: str | None = None
    orientation_free: bool | None = None
    position_free: bool | None = None


@dataclass(frozen=True)
class PlaneInput:
    """Plane roles; an optional signed placement derives from its current axis.

    Omit offset_from_axis to retain a free plane's independent offset. Supplying
    it preserves a construction's placement while only orientation participates.
    A false offset_free instead freezes the normalized signed WORLD offset.
    Property freedoms omitted by old callers inherit the existing free flag.
    """

    result: dict[str, Any]
    free: bool
    axis: str | None = None
    construction: str | None = None
    source: str | None = None
    offset_from_axis: float | None = None
    orientation_free: bool | None = None
    offset_free: bool | None = None


@dataclass(frozen=True)
class DirectionOperand:
    kind: DirectionKind
    key: str


@dataclass(frozen=True)
class DirectionAlignmentInput:
    """Unsigned alignment, independent of axis position or plane offset."""

    left: DirectionOperand
    right: DirectionOperand


@dataclass(frozen=True)
class RotationInput:
    axis: str
    members: tuple[str, str, str]


@dataclass(frozen=True)
class MirrorInput:
    plane: str
    members: tuple[str, str]


@dataclass(frozen=True)
class RadiusDistanceInput:
    radius_surface: str
    plane_surface: str
    reference_plane: str
    sign: int | None = None


def _json_safe(value: Any) -> Any:
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


class RelationshipFailure(ValueError):
    def __init__(
        self, message: str, *, code: str = "invalid-input", details: Any = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = _json_safe(
            {
                "kind": "relationship_fit_failure",
                "code": code,
                "message": message,
                "details": details,
            }
        )
        super().__init__(message)


def _finite_array(value: Any, shape: tuple[int, ...], label: str) -> Array:
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf":
            raise ValueError("expected real numeric values")
        result = np.array(raw, dtype=float, copy=True)
    except (TypeError, ValueError, OverflowError) as error:
        raise RelationshipFailure(f"{label} must contain finite real values") from error
    if result.shape != shape or not np.isfinite(result).all():
        raise RelationshipFailure(f"{label} has invalid shape or nonfinite values")
    return result


def _checked_norm(value: Array, label: str) -> float:
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 0:
        raise RelationshipFailure(f"{label} has an unrepresentable direction length")
    return norm


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
    planes: dict[str, PlaneInput] | None = None,
    rotations: tuple[RotationInput, ...] = (),
    mirrors: tuple[MirrorInput, ...] = (),
    radius_distances: tuple[RadiusDistanceInput, ...] = (),
    directions: tuple[DirectionAlignmentInput, ...] = (),
    gradient_tolerance: float | None = None,
    coordinate_frame: CoordinateFrame | None = None,
) -> dict[str, Any]:
    """Fit planes and lateral surfaces while retaining every declared equality.

    Canonical inputs use a separate local chart for each free axis. Wholly legacy
    inputs retain their positive-Z charts and publication. Fixed axes may have
    any direction. A referenced plane has one equation shared by all its fits.
    Parallel-to-axis planes retain an independent offset; contains-axis planes
    do not. Opposite plane orientations are reconciled together with offsets.
    Plane relationships can name either observed plane surfaces or plane datums.
    Without observations, an optional coordinate frame supplies numerical scale;
    otherwise an identity chart is used. Equalities must identify every free DOF.
    """
    if control is not None:
        control.checkpoint()
    planes = planes or {}
    symmetry = bool(
        planes
        or rotations
        or mirrors
        or radius_distances
        or directions
        or any(
            axis.orientation_free is not None or axis.position_free is not None
            for axis in axes.values()
        )
    )
    by_id = {surface.id: surface for surface in surfaces}
    if symmetry:
        if any(
            not isinstance(cast(object, key), str) or not key
            for key in [*by_id, *axes, *planes]
        ):
            raise RelationshipFailure("relationship IDs must be nonempty strings")
        if (
            any(
                not isinstance(cast(object, value), PlaneInput)
                for value in planes.values()
            )
            or any(
                not isinstance(cast(object, value), RotationInput)
                for value in rotations
            )
            or any(
                not isinstance(cast(object, value), MirrorInput) for value in mirrors
            )
            or any(
                not isinstance(cast(object, value), RadiusDistanceInput)
                for value in radius_distances
            )
            or any(
                not isinstance(cast(object, value), DirectionAlignmentInput)
                for value in directions
            )
        ):
            raise RelationshipFailure(
                "relationship inputs have unsupported record types"
            )
        if len(by_id) != len(surfaces):
            raise RelationshipFailure(
                "relationship observations need distinct surface IDs"
            )
        for surface in surfaces:
            if (
                surface.points.ndim != 2
                or surface.points.shape[1] != 3
                or surface.weights.shape != (len(surface.points),)
                or not len(surface.points)
                or not np.isfinite(surface.points).all()
                or not np.isfinite(surface.weights).all()
                or np.any(surface.weights <= 0)
            ):
                raise RelationshipFailure(
                    "relationship observations need finite XYZ rows and positive weights"
                )
        for relation in (*rotations, *mirrors):
            count = 3 if isinstance(relation, RotationInput) else 2
            if not isinstance(cast(object, relation.members), (tuple, list)) or any(
                not isinstance(cast(object, key), str) or not key
                for key in relation.members
            ):
                raise RelationshipFailure(
                    "symmetry member IDs must be nonempty strings"
                )
            if (
                len(relation.members) != count
                or len(set(relation.members)) != count
                or any(key not in by_id for key in relation.members)
            ):
                raise RelationshipFailure(
                    "symmetry requires distinct available members"
                )
            kinds = {by_id[key].kind for key in relation.members}
            if len(kinds) != 1 or not kinds <= {"plane", "cylinder", "cone"}:
                raise RelationshipFailure(
                    "symmetry members must have the same supported kind"
                )
            if isinstance(relation, RotationInput):
                if (
                    not isinstance(cast(object, relation.axis), str)
                    or relation.axis not in axes
                ):
                    raise RelationshipFailure("rotation axis is unavailable")
            elif not isinstance(cast(object, relation.plane), str):
                raise RelationshipFailure("mirror datum ID must be a string")
        for datum in planes.values():
            if any(
                value is not None and type(value) is not bool
                for value in (datum.orientation_free, datum.offset_free)
            ):
                raise RelationshipFailure(
                    "plane property freedom must be boolean or absent"
                )
            if datum.source is not None and not isinstance(
                cast(object, datum.source), str
            ):
                raise RelationshipFailure("plane provider alias ID must be a string")
            if datum.axis is not None and not isinstance(cast(object, datum.axis), str):
                raise RelationshipFailure("datum axis ID must be a string")
            if datum.offset_from_axis is not None and (
                datum.axis is None
                or isinstance(datum.offset_from_axis, bool)
                or not isinstance(cast(object, datum.offset_from_axis), (int, float))
                or not np.isfinite(datum.offset_from_axis)
            ):
                raise RelationshipFailure(
                    "constructed plane placement needs an axis and finite relative offset"
                )
        for relation in radius_distances:
            if any(
                not isinstance(cast(object, key), str) or not key
                for key in (
                    relation.radius_surface,
                    relation.plane_surface,
                    relation.reference_plane,
                )
            ):
                raise RelationshipFailure(
                    "radius-distance IDs must be nonempty strings"
                )
        for key, axis in axes.items():
            if not isinstance(cast(object, axis), AxisInput):
                raise RelationshipFailure("axis inputs have unsupported record types")
            if any(
                value is not None and type(value) is not bool
                for value in (axis.orientation_free, axis.position_free)
            ):
                raise RelationshipFailure(
                    "axis property freedom must be boolean or absent"
                )
            if axis.source is not None and not isinstance(
                cast(object, axis.source), str
            ):
                raise RelationshipFailure("axis provider alias ID must be a string")
            direction = _finite_array(
                axis.result.get("axis_display"), (3,), "axis direction"
            )
            point = _finite_array(axis.result.get("point_display"), (3,), "axis point")
            _ = _checked_norm(direction, "axis")
            if (
                direction.shape != (3,)
                or point.shape != (3,)
                or not np.isfinite(direction).all()
                or not np.isfinite(point).all()
                or np.linalg.norm(direction) <= 0
            ):
                raise RelationshipFailure(
                    f"axis {key} needs finite point and direction"
                )
        for relation in directions:
            for operand in (relation.left, relation.right):
                if (
                    not isinstance(cast(object, operand), DirectionOperand)
                    or operand.kind not in {"axis", "plane"}
                    or not isinstance(cast(object, operand.key), str)
                    or not operand.key
                ):
                    raise RelationshipFailure(
                        "direction operands need an axis or plane kind and a nonempty ID"
                    )
    legacy = (
        not symmetry
        and not any(
            "analytic" in surface.result or "support_intent" in surface.result
            for surface in surfaces
        )
        and all(
            "parameters" in axis.result
            and not axis.result.get("axis_is_directed", False)
            for axis in axes.values()
        )
    )

    def root_axis(key: str) -> str:
        seen: set[str] = set()
        while axes[key].source is not None:
            if key in seen:
                if symmetry:
                    raise RelationshipFailure("axis provider aliases contain a cycle")
                raise ValueError("axis provider aliases contain a cycle")
            seen.add(key)
            source = axes[key].source
            assert source is not None
            if source not in axes:
                if symmetry:
                    raise RelationshipFailure("axis provider alias is unavailable")
                raise ValueError("axis provider alias is unavailable")
            key = source
        return key

    axis_roots = {key: root_axis(key) for key in axes}
    plane_roots: dict[str, str] = {}
    for key in planes:
        current, seen = key, set[str]()
        while planes[current].source is not None:
            if current in seen:
                raise RelationshipFailure("plane provider aliases contain a cycle")
            seen.add(current)
            source = planes[current].source
            if source not in planes:
                raise RelationshipFailure("plane provider alias is unavailable")
            assert source is not None
            current = source
        plane_roots[key] = current
    if surfaces:
        all_weights = np.concatenate([surface.weights for surface in surfaces])
        frame = CoordinateFrame.from_observations(
            np.concatenate([surface.points for surface in surfaces]), all_weights
        )
    else:
        if not planes and not (axes and directions):
            raise RelationshipFailure("relationship needs observations or plane datums")
        all_weights = np.empty(0)
        frame = coordinate_frame or CoordinateFrame(np.zeros(3), 1.0)
        if (
            frame.origin.shape != (3,)
            or not np.isfinite(frame.origin).all()
            or not np.isfinite(frame.length)
            or frame.length <= 0
        ):
            raise RelationshipFailure(
                "coordinate frame needs finite origin and positive length"
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
    axis_position_slots: dict[str, int] = {}
    axis_orientation_slots: dict[str, int] = {}
    axis_charts: dict[str, _AxisChart] = {}
    fixed_axes: dict[str, tuple[Array, Array]] = {}
    for key, axis in axes.items():
        if axis.source is not None:
            continue
        value = axis.result
        position_free = axis.free if axis.position_free is None else axis.position_free
        orientation_free = (
            axis.free if axis.orientation_free is None else axis.orientation_free
        )
        direction = np.array(value["axis_display"], dtype=float)
        direction_length = float(np.linalg.norm(direction))
        if not (
            symmetry
            and not orientation_free
            and abs(direction_length - 1) <= 8 * np.finfo(float).eps
        ):
            direction /= direction_length
        point = (np.asarray(value["point_display"], dtype=float) - origin) / length
        if position_free or orientation_free:
            axis_slots[key] = len(initial)
            if legacy:
                axis_position_slots[key] = len(initial)
                axis_orientation_slots[key] = len(initial) + 2
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
                if position_free:
                    axis_position_slots[key] = len(initial)
                    initial.extend([0.0, 0.0])
                if orientation_free:
                    axis_orientation_slots[key] = len(initial)
                    initial.extend([0.0, 0.0])
        else:
            if legacy:
                if direction[2] < 0:
                    direction = -direction
                if abs(direction[2]) > 1e-12:
                    point -= point[2] / direction[2] * direction
            fixed_axes[key] = (point, direction)
    plane_slots: dict[str, int] = {}
    plane_normal_slots: dict[str, int] = {}
    plane_offset_slots: dict[str, int] = {}
    plane_fixed_normals: dict[str, Array] = {}
    plane_world_offsets: dict[str, float] = {}
    fixed_planes: dict[str, tuple[Array, float]] = {}
    plane_alias_signs: dict[str, float] = {}
    plane_specs: dict[str, tuple[str | None, str | None]] = {}
    plane_equations: dict[str, Array] = {}
    for key, datum in planes.items():
        equation = _finite_array(
            datum.result.get("plane_equation"), (4,), "datum plane equation"
        )
        norm = _checked_norm(equation[:3], "datum plane")
        with np.errstate(over="ignore", invalid="ignore"):
            plane_equations[key] = (
                equation
                if abs(norm - 1) <= 8 * np.finfo(float).eps
                else equation / norm
            )
        if not np.isfinite(plane_equations[key]).all():
            raise RelationshipFailure("normalized datum plane is unrepresentable")
    for key, datum in planes.items():
        equation = plane_equations[key]
        if (
            equation.shape != (4,)
            or not np.isfinite(equation).all()
            or np.linalg.norm(equation[:3]) <= 0
        ):
            raise RelationshipFailure(
                "datum plane needs a finite nonzero world equation"
            )
        if datum.axis is not None and datum.axis not in axes:
            raise RelationshipFailure("datum plane axis is unavailable")
        if datum.construction not in {
            None,
            "contains_axis",
            "parallel_to_axis",
            "perpendicular_to_axis",
        }:
            raise RelationshipFailure("unsupported datum plane construction")
        if (datum.axis is None) != (datum.construction is None):
            raise RelationshipFailure(
                "datum plane axis and construction must be declared together"
            )
        provider_key = plane_roots[key]
        reference = plane_equations[provider_key]
        plane_alias_signs[key] = 1.0 if equation[:3] @ reference[:3] >= 0 else -1.0
        if datum.source is not None:
            continue
        offset = float((equation[3] - equation[:3] @ origin) / length)
        plane_specs[key] = (datum.axis, datum.construction)
        orientation_free = (
            datum.free if datum.orientation_free is None else datum.orientation_free
        )
        offset_free = datum.free if datum.offset_free is None else datum.offset_free
        if orientation_free or offset_free:
            plane_slots[key] = len(initial)
            if orientation_free:
                plane_normal_slots[key] = len(initial)
                initial.extend(equation[:3].tolist())
            else:
                plane_fixed_normals[key] = equation[:3].copy()
            if offset_free:
                plane_offset_slots[key] = len(initial)
                initial.append(offset)
            else:
                plane_world_offsets[key] = float(equation[3])
        else:
            fixed_planes[key] = (equation[:3].copy(), offset)
    plane_keys: dict[str, str] = {key: key for key in planes}
    side_slots: dict[str, int] = {}
    lateral_geometry: dict[str, dict[str, Any]] = {}
    lateral_support: dict[str, SupportIntent] = {}
    lateral_references: dict[str, Array] = {}
    for surface in surfaces:
        value = surface.result
        if surface.kind == "plane":
            key = surface.datum or surface.id
            plane_keys[surface.id] = key
            _ = plane_roots.setdefault(key, key)
            _ = plane_alias_signs.setdefault(key, 1.0)
            _ = plane_specs.setdefault(key, (surface.axis, surface.construction))
            if key in planes or key in plane_slots:
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
            equation = (
                _finite_array(equation_value, (4,), "surface plane equation")
                if symmetry
                else np.asarray(equation_value, dtype=float)
            )
            if symmetry:
                _ = _checked_norm(equation[:3], "surface plane")
            equation /= np.linalg.norm(equation[:3])
            plane_slots[key] = len(initial)
            plane_normal_slots[key] = len(initial)
            plane_offset_slots[key] = len(initial) + 3
            initial.extend(
                [*equation[:3], (equation[3] - equation[:3] @ origin) / length]
            )
        elif surface.kind in {"cylinder", "cone"}:
            if surface.axis not in axes:
                if symmetry:
                    raise RelationshipFailure(
                        "coupled lateral fits require an evaluated axis"
                    )
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
        chart = axis_charts[key]
        point = chart.origin.copy()
        direction = chart.basis[:, 2].copy()
        point_j = np.zeros((3, size))
        direction_j = np.zeros((3, size))
        if key in axis_position_slots:
            start = axis_position_slots[key]
            point += chart.basis[:, :2] @ p[start : start + 2]
            point_j[:, start : start + 2] = chart.basis[:, :2]
        if key in axis_orientation_slots:
            start = axis_orientation_slots[key]
            raw = np.array([p[start], p[start + 1], 1.0])
            norm = float(np.linalg.norm(raw))
            local_direction = raw / norm
            direction = chart.basis @ local_direction
            direction_j[:, start : start + 2] = (
                chart.basis
                @ (np.eye(3) - np.outer(local_direction, local_direction))[:, :2]
                / norm
            )
        return point, direction, point_j, direction_j

    def plane_geometry(key: str, p: Array) -> tuple[Array, float, Array, Array]:
        sign = plane_alias_signs[key]
        key = plane_roots[key]
        if key in fixed_planes:
            normal, offset = fixed_planes[key]
            return sign * normal, sign * offset, np.zeros((3, size)), np.zeros(size)
        if key in plane_normal_slots:
            start = plane_normal_slots[key]
            normal, normal_j = p[start : start + 3], identity[start : start + 3]
        else:
            normal, normal_j = plane_fixed_normals[key], np.zeros((3, size))
        if key in plane_offset_slots:
            start = plane_offset_slots[key]
            offset, offset_j = float(p[start]), identity[start]
        else:
            # Freeze the signed WORLD offset, not its normal-dependent local
            # coordinate. Rotating a plane must not translate its world equation.
            offset = float((plane_world_offsets[key] - normal @ origin) / length)
            offset_j = -(origin @ normal_j) / length
        return (
            sign * normal,
            sign * offset,
            sign * normal_j,
            sign * offset_j,
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
    square_weights = (
        np.sqrt(normalized_weights(all_weights)) if surfaces else np.empty(0)
    )

    def observations(p: Array) -> tuple[Array, Array]:
        if not surfaces:
            return np.empty(0), np.empty((0, size))
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

    for relation, group in relationships:
        if relation not in {"parallel", "coincident"}:
            raise RelationshipFailure("unsupported plane relationship")
        if len(group) < 2 or len(set(group)) != len(group):
            raise RelationshipFailure("plane relationships need distinct plane inputs")
        if any(key not in plane_keys for key in group):
            raise RelationshipFailure(
                "plane relationship input is unavailable or not a plane"
            )
    for relation in directions:
        for operand in (relation.left, relation.right):
            if operand.kind == "axis":
                available = operand.key in axes
            else:
                available = plane_keys.get(operand.key, operand.key) in plane_roots
            if not available:
                raise RelationshipFailure(
                    "direction operand is unavailable or has the wrong kind"
                )

    def direction_geometry(operand: DirectionOperand, p: Array) -> tuple[Array, Array]:
        if operand.kind == "axis":
            _, direction, _, jacobian = axis_geometry(operand.key, p)
        else:
            direction, _, jacobian, _ = plane_geometry(
                plane_keys.get(operand.key, operand.key), p
            )
        return direction, jacobian

    # Choose signs per normal-equivalence class, not independently per edge:
    # pairwise initial dot products can create an impossible odd sign cycle.
    p0 = np.asarray(initial)
    parents = {key: key for key in (*plane_slots, *fixed_planes)}

    def root(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for _, group in relationships:
        anchor = root(plane_roots[plane_keys[group[0]]])
        for key in group[1:]:
            parents[root(plane_roots[plane_keys[key]])] = anchor
    perpendicular_keys: dict[str, str] = {}
    for plane_key, (axis, construction) in plane_specs.items():
        if axis is not None and construction == "perpendicular_to_axis":
            key = plane_roots[plane_key]
            other = perpendicular_keys.setdefault(axis_roots[axis], key)
            parents[root(key)] = root(other)
    orientations = {
        key: 1.0
        if plane_geometry(key, p0)[0] @ plane_geometry(root(key), p0)[0] >= 0
        else -1.0
        for key in parents
    }
    relation_signs = [
        [
            orientations[plane_roots[plane_keys[group[0]]]]
            * orientations[plane_roots[plane_keys[key]]]
            * plane_alias_signs[plane_keys[group[0]]]
            * plane_alias_signs[plane_keys[key]]
            for key in group[1:]
        ]
        for _, group in relationships
    ]

    if directions:
        # A positive-hemisphere axis chart cannot project an exactly orthogonal
        # start onto its target. Seed only incoherent direction components;
        # coherent recovery candidates retain their independent starting pose.
        direction_parents: dict[DirectionKey, DirectionKey] = {}

        def operand_key(operand: DirectionOperand) -> DirectionKey:
            return (
                ("axis", axis_roots[operand.key])
                if operand.kind == "axis"
                else ("plane", plane_roots[plane_keys.get(operand.key, operand.key)])
            )

        def direction_root(key: DirectionKey) -> DirectionKey:
            _ = direction_parents.setdefault(key, key)
            while direction_parents[key] != key:
                direction_parents[key] = direction_parents[direction_parents[key]]
                key = direction_parents[key]
            return key

        def join(a: DirectionKey, b: DirectionKey) -> None:
            direction_parents[direction_root(b)] = direction_root(a)

        touched = {
            operand_key(operand)
            for relation in directions
            for operand in (relation.left, relation.right)
        }
        for relation in directions:
            join(operand_key(relation.left), operand_key(relation.right))
        for _, group in relationships:
            first_plane_key: DirectionKey = ("plane", plane_roots[plane_keys[group[0]]])
            for key in group[1:]:
                join(first_plane_key, ("plane", plane_roots[plane_keys[key]]))
        for plane_key, (axis_key, construction) in plane_specs.items():
            if axis_key is not None and construction == "perpendicular_to_axis":
                join(("plane", plane_roots[plane_key]), ("axis", axis_roots[axis_key]))
        groups: dict[DirectionKey, list[DirectionKey]] = {}
        for key in direction_parents:
            groups.setdefault(direction_root(key), []).append(key)
        for keys in groups.values():
            if not touched.intersection(keys):
                continue
            vectors = [
                direction_geometry(DirectionOperand(kind, key), p0)[0]
                for kind, key in keys
            ]
            if all(
                np.linalg.norm(np.cross(vectors[0], value)) <= 1e-8
                for value in vectors[1:]
            ):
                continue
            fixed = [
                value
                for (kind, key), value in zip(keys, vectors, strict=True)
                if key
                not in (
                    axis_orientation_slots if kind == "axis" else plane_normal_slots
                )
            ]
            seed = (
                fixed[0].copy()
                if fixed
                else np.sum(
                    [value if value @ vectors[0] >= 0 else -value for value in vectors],
                    axis=0,
                )
            )
            seed /= np.linalg.norm(seed)
            for (kind, key), value in zip(keys, vectors, strict=True):
                if kind == "axis" and key in axis_orientation_slots:
                    direction = seed if value @ seed >= 0 else -seed
                    first, second = basis(direction)
                    chart = axis_charts[key]
                    axis_charts[key] = _AxisChart(
                        chart.origin, np.column_stack((first, second, direction))
                    )
                    start = axis_orientation_slots[key]
                    p0[start : start + 2] = 0
                elif kind == "plane" and key in plane_normal_slots:
                    representative = plane_geometry(root(key), p0)[0]
                    sign = orientations[key] * (
                        1.0 if representative @ seed >= 0 else -1.0
                    )
                    start = plane_normal_slots[key]
                    p0[start : start + 3] = sign * seed

    # Start each parallel-normal class coherently. This is initialization only;
    # the equality solver still checks feasibility and fits all observations.
    # Independent near-opposite seeds can otherwise trap Newton projection on
    # incompatible orientation branches despite feasible unoriented planes.
    for representative in dict.fromkeys(root(key) for key in plane_slots):
        if symmetry:
            # Explicit datums may have no observation rows; fixed parameters
            # and free clocking starts retain their declared provider roles.
            continue
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
            if symmetry and (key in planes or plane_roots[key] in fixed_planes):
                continue
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

    if symmetry:
        for relation in mirrors:
            if relation.plane not in plane_roots:
                raise RelationshipFailure("mirror datum plane is unavailable")
            axis, construction = plane_specs[plane_roots[relation.plane]]
            if axis is None or construction != "contains_axis":
                raise RelationshipFailure("mirror datum must contain an available axis")
        for relation in radius_distances:
            if (
                relation.radius_surface not in by_id
                or by_id[relation.radius_surface].kind != "cylinder"
                or relation.plane_surface not in plane_keys
                or relation.reference_plane not in plane_roots
            ):
                raise RelationshipFailure(
                    "radius-distance requires a cylinder, plane surface and datum"
                )
            if relation.sign not in (None, -1, 1) or isinstance(
                cast(object, relation.sign), bool
            ):
                raise RelationshipFailure("radius-distance side must be -1 or +1")

    def transformed(
        relation: RotationInput | MirrorInput, slot: int, p: Array
    ) -> tuple[Array, Array, Array, Array]:
        if isinstance(relation, RotationInput):
            center, direction, center_j, direction_j = axis_geometry(relation.axis, p)
            provider = axes[axis_roots[relation.axis]].result
            sign = (
                1.0
                if np.asarray(axes[relation.axis].result["axis_display"])
                @ np.asarray(provider["axis_display"])
                >= 0
                else -1.0
            )
            direction, direction_j = sign * direction, sign * direction_j
            angle = slot * 2 * np.pi / 3
            c, s = np.cos(angle), np.sin(angle)

            def skew(v: Array) -> Array:
                x, y, z = v
                return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])

            matrix = (
                c * np.eye(3)
                + s * skew(direction)
                + (1 - c) * np.outer(direction, direction)
            )
            matrix_j = (
                np.stack(
                    [
                        s * skew(v)
                        + (1 - c) * (np.outer(v, direction) + np.outer(direction, v))
                        for v in direction_j.T
                    ],
                    axis=2,
                )
                if size
                else np.zeros((3, 3, 0))
            )
            translation = center - matrix @ center
            translation_j = (
                center_j - matrix @ center_j - np.einsum("ijk,j->ik", matrix_j, center)
            )
        else:
            normal, offset, normal_j, offset_j = plane_geometry(relation.plane, p)
            matrix = np.eye(3) - 2 * np.outer(normal, normal)
            matrix_j = -2 * (
                np.einsum("ik,j->ijk", normal_j, normal)
                + np.einsum("i,jk->ijk", normal, normal_j)
            )
            translation = 2 * offset * normal
            translation_j = 2 * (offset * normal_j + np.outer(normal, offset_j))
        return matrix, translation, matrix_j, translation_j

    symmetry_signs: list[list[float]] = []
    for relation in (*rotations, *mirrors):
        first = by_id[relation.members[0]]
        signs: list[float] = []
        for slot, key in enumerate(relation.members[1:], start=1):
            matrix = transformed(relation, slot, p0)[0]
            if first.kind == "plane":
                a = plane_geometry(plane_keys[first.id], p0)[0]
                b = plane_geometry(plane_keys[key], p0)[0]
            else:
                other_axis = by_id[key].axis
                assert first.axis is not None and other_axis is not None
                a = axis_geometry(first.axis, p0)[1]
                b = axis_geometry(other_axis, p0)[1]
            signs.append(1.0 if b @ (matrix @ a) >= 0 else -1.0)
        symmetry_signs.append(signs)
    distance_branches: list[tuple[float, float]] = []
    for relation in radius_distances:
        n, h, _, _ = plane_geometry(plane_keys[relation.plane_surface], p0)
        ref, offset, _, _ = plane_geometry(relation.reference_plane, p0)
        alignment = 1.0 if n @ ref >= 0 else -1.0
        separation = alignment * h - offset
        if relation.sign is None and separation == 0:
            raise RelationshipFailure(
                "radius-distance side is undefined at the initial planes",
                code="ambiguous",
            )
        distance_branches.append(
            (
                alignment,
                float(relation.sign)
                if relation.sign is not None
                else (1.0 if separation > 0 else -1.0),
            )
        )

    def equalities(p: Array) -> tuple[Array, Array]:
        values: list[Array] = []
        jacobians: list[Array] = []

        def append(value: float | Array, jacobian: Array) -> None:
            values.append(np.atleast_1d(value))
            jacobians.append(np.atleast_2d(jacobian))

        for key in plane_slots:
            normal, _, normal_j, _ = plane_geometry(key, p)
            append((normal @ normal - 1) / 2, normal @ normal_j)
        for key, (axis, construction) in plane_specs.items():
            if key not in planes or axis is None:
                continue
            point, direction, point_j, direction_j = axis_geometry(axis, p)
            normal, offset, normal_j, offset_j = plane_geometry(key, p)
            relative_offset = planes[key].offset_from_axis
            if relative_offset is not None:
                # An orientation-only operand keeps its authored placement as
                # a dependency of the current axis, not a fixed world plane.
                append(
                    offset - normal @ point - relative_offset / length,
                    offset_j - point @ normal_j - normal @ point_j,
                )
            if construction == "perpendicular_to_axis":
                append(
                    np.cross(normal, direction),
                    np.cross(normal_j.T, direction).T
                    + np.cross(normal, direction_j.T).T,
                )
            elif construction in {"contains_axis", "parallel_to_axis"}:
                append(normal @ direction, direction @ normal_j + normal @ direction_j)
                if construction == "contains_axis":
                    append(
                        offset - normal @ point,
                        offset_j - point @ normal_j - normal @ point_j,
                    )
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
        for relation in directions:
            a, a_j = direction_geometry(relation.left, p)
            b, b_j = direction_geometry(relation.right, p)
            # cross(a,b)=0 is unsigned and rank two at alignment. The existing
            # equality SVD retains its independent rows without a penalty term.
            append(np.cross(a, b), np.cross(a_j.T, b).T + np.cross(a, b_j.T).T)
        for relation, signs in zip((*rotations, *mirrors), symmetry_signs, strict=True):
            first = by_id[relation.members[0]]
            for slot, (key, sign) in enumerate(
                zip(relation.members[1:], signs, strict=True), start=1
            ):
                matrix, translation, matrix_j, translation_j = transformed(
                    relation, slot, p
                )
                if first.kind == "plane":
                    n, h, n_j, h_j = plane_geometry(plane_keys[first.id], p)
                    target = matrix @ n
                    target_j = matrix @ n_j + np.einsum("ijk,j->ik", matrix_j, n)
                    offset = h + target @ translation
                    offset_j = h_j + translation @ target_j + target @ translation_j
                    other, other_h, other_j, other_h_j = plane_geometry(
                        plane_keys[key], p
                    )
                    append(other - sign * target, other_j - sign * target_j)
                    append(other_h - sign * offset, other_h_j - sign * offset_j)
                else:
                    other_axis = by_id[key].axis
                    assert first.axis is not None and other_axis is not None
                    point, direction, point_j, direction_j = axis_geometry(
                        first.axis, p
                    )
                    target_point = matrix @ point + translation
                    target_point_j = (
                        matrix @ point_j
                        + np.einsum("ijk,j->ik", matrix_j, point)
                        + translation_j
                    )
                    target_direction = matrix @ direction
                    target_direction_j = matrix @ direction_j + np.einsum(
                        "ijk,j->ik", matrix_j, direction
                    )
                    other, other_direction, other_j, other_direction_j = axis_geometry(
                        other_axis, p
                    )
                    delta = np.asarray(other - target_point, dtype=float)
                    delta_j = np.asarray(other_j - target_point_j, dtype=float)
                    append(
                        other_direction - sign * target_direction,
                        other_direction_j - sign * target_direction_j,
                    )
                    append(
                        np.cross(other_direction, delta),
                        np.cross(other_direction_j.T, delta).T
                        + np.cross(other_direction, delta_j.T).T,
                    )
                    a, b = side_slots[first.id], side_slots[key]
                    taper = p[a + 1] if first.kind == "cone" else 0.0
                    taper_j = (
                        identity[a + 1] if first.kind == "cone" else np.zeros(size)
                    )
                    shift = delta @ target_direction
                    shift_j = target_direction @ delta_j + delta @ target_direction_j
                    append(
                        p[b] - p[a] - taper * shift,
                        identity[b] - identity[a] - taper_j * shift - taper * shift_j,
                    )
                    if first.kind == "cone":
                        append(
                            p[b + 1] - sign * taper, identity[b + 1] - sign * taper_j
                        )
        for relation, (alignment, side) in zip(
            radius_distances, distance_branches, strict=True
        ):
            n, h, n_j, h_j = plane_geometry(plane_keys[relation.plane_surface], p)
            ref, offset, ref_j, offset_j = plane_geometry(relation.reference_plane, p)
            append(alignment * n - ref, alignment * n_j - ref_j)
            start = side_slots[relation.radius_surface]
            append(
                side * (alignment * h - offset) - p[start],
                side * (alignment * h_j - offset_j) - identity[start],
            )
        if not values:
            return np.empty(0), np.empty((0, size))
        return np.concatenate(values), np.vstack(jacobians)

    def canonical_symmetry_surface(surface: SurfaceInput, p: Array) -> dict[str, Any]:
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
            raise RelationshipFailure(
                str(error), code=error.code, details=error.diagnostic
            ) from error
        return {
            "kind": surface.kind,
            "origin": list(resolved.origin),
            "axis": list(resolved.axis),
            "radius": float(radius + taper * resolved.origin_shift),
            "slope": float(resolved.axis_sign * taper),
            "axial_domain": list(resolved.domain),
        }

    def canonical_surface(surface: SurfaceInput, p: Array) -> dict[str, Any]:
        """Publish about a semantic anchor, independent of the numerical chart."""
        if symmetry:
            return canonical_symmetry_surface(surface, p)
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
                except (SupportIntentFailure, RelationshipFailure):
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
    try:
        if size == 0 and symmetry:
            residual, _ = (
                observations(p0) if control is None else control.call(observations, p0)
            )
            constraints, _ = (
                equalities(p0) if control is None else control.call(equalities, p0)
            )
            violation = float(np.max(np.abs(constraints), initial=0.0))
            if not valid(p0) or violation > 1e-10:
                raise RelationshipFailure(
                    "fixed geometry violates declared relationships",
                    code="infeasible-constraints",
                )
            fitted = ConstrainedLeastSquaresResult(
                (),
                (float(residual @ residual / 2),),
                0,
                1,
                1,
                0,
                0,
                0,
                1.0,
                0.0,
                violation,
                "constrained-stationarity",
            )
        else:
            fitted = solve_constrained_least_squares(
                p0,
                observations
                if control is None
                else lambda p: control.call(observations, p),
                equalities
                if control is None
                else lambda p: control.call(equalities, p),
                parameter_scales=np.ones(size),
                # The new exact-symmetry path needs tighter local numerical
                # stationarity than the historical component default. This is
                # independent of physical acceptance and preserves old callers.
                gradient_tolerance=(1e-10 if symmetry else 1e-9)
                if gradient_tolerance is None
                else gradient_tolerance,
                geometry_is_valid=valid,
                max_iterations=100
                if control is None
                else control.limits.max_iterations,
                max_trials=32 if control is None else control.limits.max_trials,
                allow_empty_observations=not surfaces,
            )
    except ConstrainedLeastSquaresFailure as error:
        if not symmetry:
            raise
        raise RelationshipFailure(
            str(error),
            code=error.code,
            details=None if error.diagnostics is None else asdict(error.diagnostics),
        ) from error
    parameters = np.asarray(fitted.parameters)
    final_constraints, _ = (
        equalities(parameters)
        if control is None
        else control.call(equalities, parameters)
    )
    if np.max(np.abs(final_constraints), initial=0.0) > 1e-10:
        if symmetry:
            raise RelationshipFailure(
                "joint geometry does not satisfy declared relationships",
                code="infeasible-constraints",
            )
        raise ValueError("joint geometry does not satisfy its declared relationships")
    output_axes: dict[str, dict[str, Any]] = {}
    for key, axis in axes.items():
        point, direction, _, _ = axis_geometry(key, parameters)
        world_point = origin + length * point
        if symmetry and axis_roots[key] not in axis_position_slots:
            world_point = np.asarray(
                axes[axis_roots[key]].result["point_display"]
            ).copy()
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
    output_planes: dict[str, dict[str, Any]] = {}

    def world_plane_equation(key: str) -> list[float]:
        provider = plane_roots[key]
        if provider in fixed_planes:
            return (plane_alias_signs[key] * plane_equations[provider]).tolist()
        normal, offset, _, _ = plane_geometry(key, parameters)
        if provider in plane_world_offsets:
            return [
                *map(float, normal),
                plane_alias_signs[key] * plane_world_offsets[provider],
            ]
        return [*map(float, normal), float(length * offset + normal @ origin)]

    for key, datum in planes.items():
        equation = world_plane_equation(key)
        normal, world_offset = np.asarray(equation[:3]), equation[3]
        equations[key] = equation
        record = {
            **deepcopy(datum.result),
            "plane_equation": equation,
            "normal_display": normal.tolist(),
            "resolved_by": "plane_relationship",
        }
        axis_key, construction = plane_specs[plane_roots[key]]
        if axis_key is not None:
            axis_record = output_axes[axis_key]
            anchor, direction = (
                np.asarray(axis_record["point_display"]),
                np.asarray(axis_record["axis_display"]),
            )
            first, second = basis(direction)
            # Reference-plane clocking uses axis x reference; surface charts
            # use the opposite transverse basis.
            first, second = -first, -second
            radial = np.cross(normal, direction)
            if construction == "perpendicular_to_axis":
                plane_u = first
                plane_v = np.cross(normal, plane_u)
            else:
                radial /= np.linalg.norm(radial)
                plane_u, plane_v = direction, radial
            plane_v /= np.linalg.norm(plane_v)
            record.update(
                axis_display=direction.tolist(),
                basis_u_display=plane_u.tolist(),
                basis_v_display=plane_v.tolist(),
                radial_display=plane_v.tolist(),
                point_display=(
                    anchor + (world_offset - normal @ anchor) * normal
                ).tolist(),
                offset=float(world_offset - normal @ anchor),
                construction=construction,
                angle_degrees=None
                if construction == "perpendicular_to_axis"
                else float(np.degrees(np.arctan2(radial @ second, radial @ first))),
            )
        else:
            record["point_display"] = (world_offset * normal).tolist()
            plane_u, plane_v = basis(normal)
            record.update(
                basis_u_display=plane_u.tolist(),
                basis_v_display=plane_v.tolist(),
                radial_display=plane_v.tolist(),
            )
        if "parameters" in record:
            record["parameters"] = equation.copy()
        output_planes[key] = record
    for surface in surfaces:
        result = deepcopy(surface.result)
        if surface.kind == "plane":
            normal, offset, _, _ = plane_geometry(plane_keys[surface.id], parameters)
            equation = (
                world_plane_equation(plane_keys[surface.id])
                if symmetry
                else [*map(float, normal), float(length * offset + normal @ origin)]
            )
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
        **({"planes": output_planes} if symmetry else {}),
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
