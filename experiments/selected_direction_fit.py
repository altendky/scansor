"""Bounded joint recovery from unchanged selections with independent axis lines.

Direction classes share disposable orientation proposals only. Each lateral
member initializes its own transverse center, radius and taper; a shared line
exists only when the caller explicitly assigns the same axis provider.
"""

# The reused selected-data helpers below initialize one member at a time;
# SelectedSymmetryGroup and its common-line/orbit initialization are not used.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import (
    FitAssessment,
    FitCandidate,
    RecoveryFailure,
    RecoveryPolicy,
    RecoveryProblem,
    initialization_binding,
    recover_fit,
)
from experiments.fit_relationship_component import (
    AxisInput,
    DirectionAlignmentInput,
    DirectionOperand,
    MirrorInput,
    PlaneInput,
    PointInput,
    RadiusDistanceInput,
    RelationshipFailure,
    RotationInput,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import (
    AttemptControl,
    FitBudgetExhausted,
    normalized_weights,
)
from experiments.scan_workspace import Workspace
from experiments.selected_symmetry_fit import (
    _axis_value,
    _circle_start,
    _directions,
    _Geometry,
    _line_start,
    _plane_value,
    _start,
    _unit,
)
from experiments.selection_growth import surface_distance
from experiments.support_intent import (
    SUPPORT_POLICY_VERSION,
    SupportIntent,
    legacy_support,
    resolve_support,
    support_record,
)
from scansor.nonlinear_least_squares import LeastSquaresFailure

if TYPE_CHECKING:
    from experiments.nozzle_coaxial import FitSelection

Array = NDArray[np.float64]
DIRECTION_POLICY_VERSION = "selected-direction-component-recovery-v1"
_CHART = "independent-member-direction-starts-v1"


def private_direction_axis(fit_id: str) -> str:
    return "@direction-member:" + fit_id


class DirectionFitFailure(ValueError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "direction_fit_failure",
            "code": code,
            "message": message,
            **deepcopy(details or {}),
        }
        super().__init__(message)


@dataclass(frozen=True)
class SelectedDirectionSurface:
    selection: FitSelection
    axis: str | None = None
    datum: str | None = None
    construction: str | None = None
    point: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selection", replace(self.selection, ids=list(self.selection.ids))
        )


@dataclass(frozen=True)
class SelectedDirectionComponent:
    surfaces: tuple[SelectedDirectionSurface, ...]
    axes: Mapping[str, AxisInput]
    directions: tuple[DirectionAlignmentInput, ...]
    planes: Mapping[str, PlaneInput] = field(default_factory=dict)
    centers: Mapping[str, PointInput] = field(default_factory=dict)
    relationships: tuple[tuple[str, tuple[str, ...]], ...] = ()
    radii: tuple[tuple[str, ...], ...] = ()
    rotations: tuple[RotationInput, ...] = ()
    mirrors: tuple[MirrorInput, ...] = ()
    radius_distances: tuple[RadiusDistanceInput, ...] = ()
    semantic_intent: dict[str, Any] | None = None
    gradient_tolerance: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "surfaces",
            "directions",
            "rotations",
            "mirrors",
            "radius_distances",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        for name in ("axes", "planes", "centers"):
            object.__setattr__(
                self, name, MappingProxyType(deepcopy(dict(getattr(self, name))))
            )
        object.__setattr__(
            self,
            "relationships",
            tuple((kind, tuple(ids)) for kind, ids in self.relationships),
        )
        object.__setattr__(self, "radii", tuple(tuple(ids) for ids in self.radii))
        object.__setattr__(self, "semantic_intent", deepcopy(self.semantic_intent))


def _roots(records: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in records:
        current, seen = key, set[str]()
        while True:
            if current not in records or current in seen:
                raise DirectionFitFailure(
                    "invalid-input", "missing or cyclic geometry provider"
                )
            seen.add(current)
            if records[current].source is None:
                result[key] = current
                break
            current = records[current].source
    return result


def _orientation_free(value: AxisInput | PlaneInput) -> bool:
    return value.free if value.orientation_free is None else value.orientation_free


def _provider_intent(value: AxisInput | PlaneInput | PointInput) -> dict[str, Any]:
    """Bind fixed geometric properties, excluding disposable solver metadata."""
    record: dict[str, Any] = {"free": value.free, "source": value.source}
    geometry: dict[str, Any] = {}
    if isinstance(value, AxisInput):
        record.update(
            orientation_free=value.orientation_free, position_free=value.position_free
        )
        if "direction_reversed" in value.result:
            record["direction_reversed"] = value.result["direction_reversed"]
        if not (value.free if value.position_free is None else value.position_free):
            geometry["point_display"] = value.result.get("point_display")
        if not _orientation_free(value):
            geometry["axis_display"] = value.result.get("axis_display")
    elif isinstance(value, PlaneInput):
        record.update(
            orientation_free=value.orientation_free,
            offset_free=value.offset_free,
            axis=value.axis,
            construction=value.construction,
            offset_from_axis=value.offset_from_axis,
        )
        if not _orientation_free(value) or not (
            value.free if value.offset_free is None else value.offset_free
        ):
            geometry["plane_equation"] = value.result.get("plane_equation")
    elif not value.free:
        geometry["point_display"] = value.result.get("point_display")
    record["geometry"] = geometry
    return record


def _sphere_start(points: Array, weights: Array) -> Array:
    w = normalized_weights(weights)
    design = np.column_stack([2 * points, np.ones(len(points))])
    solution = np.linalg.lstsq(
        np.sqrt(w)[:, None] * design, np.sqrt(w) * np.sum(points**2, axis=1), rcond=None
    )[0]
    # This is only a disposable algebraic start. Joint rank is assessed after
    # all point/radius relationships participate, including weak member patches.
    return np.asarray(solution[:3], dtype=float)


def fit_selected_direction_component(
    workspace: Workspace,
    component: SelectedDirectionComponent,
    *,
    policy: RecoveryPolicy | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    try:
        return _fit_selected_direction_component(
            workspace, component, policy=policy, cancelled=cancelled
        )
    except DirectionFitFailure:
        raise
    except FitBudgetExhausted as error:
        raise DirectionFitFailure(
            "search-exhausted", str(error), {"initializer": "authored-datums"}
        ) from error
    except (ValueError, np.linalg.LinAlgError) as error:
        raise DirectionFitFailure("invalid-input", str(error)) from error


def _fit_selected_direction_component(
    workspace: Workspace,
    component: SelectedDirectionComponent,
    *,
    policy: RecoveryPolicy | None,
    cancelled: Callable[[], bool] | None,
) -> dict[str, Any]:
    policy = policy or RecoveryPolicy()
    guard = AttemptControl(policy.attempt, cancelled)
    guard.checkpoint()
    if (
        any(
            not isinstance(cast(object, value), AxisInput)
            for value in component.axes.values()
        )
        or any(
            not isinstance(cast(object, value), PlaneInput)
            for value in component.planes.values()
        )
        or any(
            not isinstance(cast(object, value), PointInput)
            for value in component.centers.values()
        )
        or any(
            not isinstance(cast(object, value), DirectionAlignmentInput)
            for value in component.directions
        )
    ):
        raise DirectionFitFailure(
            "invalid-input", "invalid direction component declaration records"
        )
    if workspace.source_sha256 is None or not (
        component.directions or component.relationships
    ):
        raise DirectionFitFailure(
            "invalid-input",
            "accepted source and direction or plane relationships are required",
        )
    if not component.surfaces:
        try:
            output = fit_relationship_component(
                [],
                dict(component.axes),
                [(kind, list(ids)) for kind, ids in component.relationships],
                [list(ids) for ids in component.radii],
                dict(component.centers),
                control=guard,
                planes=dict(component.planes),
                directions=component.directions,
                rotations=component.rotations,
                mirrors=component.mirrors,
                radius_distances=component.radius_distances,
                gradient_tolerance=component.gradient_tolerance,
                coordinate_frame=CoordinateFrame(np.zeros(3), 1.0),
            )
        except RelationshipFailure as error:
            raise DirectionFitFailure(
                error.code, str(error), {"solver": error.diagnostic}
            ) from error
        output.update(
            initialization={
                "algorithm": DIRECTION_POLICY_VERSION,
                "initializer": "authored-datums",
                "observations": 0,
            },
            selected_area={},
            session={"source_sha256": workspace.source_sha256, "schema_version": 2},
        )
        guard.checkpoint()
        return output
    members = {member.selection.id: member for member in component.surfaces}
    if len(members) != len(component.surfaces):
        raise DirectionFitFailure(
            "invalid-input", "selected members require distinct IDs"
        )
    selected_ids: list[int] = []
    for member in component.surfaces:
        guard.checkpoint()
        selection = member.selection
        if (
            selection.kind not in {"cylinder", "cone", "plane", "sphere"}
            or not selection.ids
        ):
            raise DirectionFitFailure(
                "invalid-input", "members require supported nonempty selections"
            )
        if any(
            type(index) is not int or index < 0 or index >= len(workspace.local)
            for index in selection.ids
        ):
            raise DirectionFitFailure("invalid-input", "invalid selected source IDs")
        if len(set(selection.ids)) != len(selection.ids):
            raise DirectionFitFailure(
                "invalid-input", "a selection repeats source rows"
            )
        selected_ids.extend(selection.ids)
    if len(set(selected_ids)) != len(selected_ids):
        raise DirectionFitFailure("selection-overlap", "joint selections overlap")
    points = {key: workspace.local[m.selection.ids] for key, m in members.items()}
    weights = {
        key: workspace.data.weights[m.selection.ids] for key, m in members.items()
    }
    normals = {
        key: workspace.data.normals[m.selection.ids] @ workspace.frame
        for key, m in members.items()
    }
    frame = CoordinateFrame.from_observations(
        workspace.local[selected_ids], workspace.data.weights[selected_ids]
    )
    q = {key: (value - frame.origin) / frame.length for key, value in points.items()}
    maximum_weight = float(np.max(workspace.data.weights[selected_ids]))
    for key in members:
        _ = normalized_weights(weights[key])
        if not np.isfinite(normals[key]).all() or np.any(
            np.linalg.norm(normals[key], axis=1) <= 0
        ):
            raise DirectionFitFailure(
                "invalid-input", "selected normals must be finite nonzero directions"
            )
    axes, planes, centers = (
        dict(component.axes),
        dict(component.planes),
        dict(component.centers),
    )
    if any(
        not isinstance(cast(object, key), str) or not key
        for key in [*members, *axes, *planes, *centers]
    ):
        raise DirectionFitFailure(
            "invalid-input", "geometry provider IDs must be nonempty strings"
        )
    surface_axes: dict[str, str] = {}
    surface_points: dict[str, str] = {}
    supports: dict[str, SupportIntent] = {}
    for key, member in members.items():
        kind = member.selection.kind
        if kind in {"cylinder", "cone"}:
            axis = member.axis or private_direction_axis(key)
            if member.axis is None:
                _ = axes.setdefault(axis, AxisInput({}, True))
            if axis not in axes:
                raise DirectionFitFailure(
                    "invalid-input", "selected lateral axis provider is unavailable"
                )
            surface_axes[key] = axis
            support = member.selection.support or legacy_support(
                member.selection.domain
            )
            if member.selection.domain is not None and support != legacy_support(
                member.selection.domain
            ):
                raise DirectionFitFailure(
                    "invalid-support", "conflicting authored support"
                )
            supports[key] = support
        elif kind == "sphere":
            center = member.point or "@direction-point:" + key
            if member.point is None:
                _ = centers.setdefault(center, PointInput({}, True))
            if center not in centers:
                raise DirectionFitFailure(
                    "invalid-input", "selected sphere point provider is unavailable"
                )
            surface_points[key] = center
    ar, pr, cr = _roots(axes), _roots(planes), _roots(centers)
    plane_keys = {
        key: pr.get(member.datum, member.datum) if member.datum else key
        for key, member in members.items()
        if member.selection.kind == "plane"
    }
    if any(
        member.datum is not None and member.datum not in planes
        for member in members.values()
    ):
        raise DirectionFitFailure(
            "invalid-input", "selected plane datum is unavailable"
        )
    # A direction class never aliases positions or plane offsets.
    parent = {("axis", key): ("axis", key) for key in dict.fromkeys(ar.values())}
    parent.update(
        {
            ("plane", key): ("plane", key)
            for key in dict.fromkeys([*pr.values(), *plane_keys.values()])
        }
    )

    def operand(value: DirectionOperand) -> tuple[str, str]:
        if value.kind == "axis" and value.key in ar:
            return "axis", ar[value.key]
        if value.kind == "plane" and value.key in {*planes, *plane_keys}:
            return "plane", pr.get(value.key, plane_keys.get(value.key, value.key))
        raise DirectionFitFailure("invalid-input", "direction operand is unavailable")

    def root(key: tuple[str, str]) -> tuple[str, str]:
        while parent[key] != key:
            key = parent[key]
        return key

    def union(left: tuple[str, str], right: tuple[str, str]) -> None:
        parent[root(right)] = root(left)

    for relation in component.directions:
        union(operand(relation.left), operand(relation.right))
    for _, keys in component.relationships:
        for key in keys[1:]:
            union(
                operand(DirectionOperand("plane", keys[0])),
                operand(DirectionOperand("plane", key)),
            )
    for key, member in members.items():
        if (
            member.selection.kind == "plane"
            and member.axis
            and member.construction == "perpendicular_to_axis"
        ):
            union(("plane", plane_keys[key]), ("axis", ar[member.axis]))
    for key, plane in planes.items():
        if plane.axis and plane.construction == "perpendicular_to_axis":
            union(("plane", pr[key]), ("axis", ar[plane.axis]))
    classes = list(dict.fromkeys(root(key) for key in parent))
    class_members: dict[tuple[str, str], list[str]] = {key: [] for key in classes}
    for key, member in members.items():
        if member.selection.kind in {"cylinder", "cone"}:
            class_members[root(("axis", ar[surface_axes[key]]))].append(key)
        elif member.selection.kind == "plane":
            class_members[root(("plane", plane_keys[key]))].append(key)

    def member_start(key: str, direction: Array) -> _Geometry:
        if members[key].selection.kind == "plane":
            return _Geometry(normalized_weights(weights[key]) @ q[key], direction)
        return _circle_start(
            q[key], weights[key], direction, members[key].selection.kind == "cone"
        )[0]

    proposals: dict[tuple[str, str], list[Array]] = {}
    for cluster in classes:
        guard.checkpoint()
        candidates: list[Array] = []
        fixed: list[Array] = []
        for kind, key in parent:
            if root((kind, key)) != cluster:
                continue
            if kind == "axis":
                value = _axis_value(axes[key])
                record = axes[key]
                direction = None if value is None else value[1]
            else:
                record = planes.get(key)
                equation = None if record is None else _plane_value(record)
                direction = None if equation is None else equation[:3]
            if direction is not None:
                (
                    fixed
                    if record is not None and not _orientation_free(record)
                    else candidates
                ).append(direction)
            elif record is not None and not _orientation_free(record):
                raise DirectionFitFailure(
                    "invalid-input", "fixed direction provider is malformed"
                )
        if fixed:
            proposals[cluster] = [fixed[0]]
            continue
        for key in class_members[cluster]:
            if members[key].selection.kind == "plane":
                candidates.append(
                    _start(q[key], weights[key], normals[key], "plane")[0].axis
                )
                average = normalized_weights(weights[key]) @ normals[key]
                if np.linalg.norm(average) > 1e-12:
                    candidates.append(_unit(average))
            else:
                candidates.extend(
                    _directions(
                        q[key],
                        weights[key],
                        normals[key],
                        members[key].selection.kind == "cone",
                    )
                )
        unique: list[Array] = []
        for direction in candidates:
            direction = _unit(direction)
            if not any(abs(float(direction @ old)) > 1 - 1e-10 for old in unique):
                unique.append(direction)

        def score(direction: Array, cluster: tuple[str, str] = cluster) -> float:
            guard.checkpoint()
            total = area = 0.0
            for key in class_members[cluster]:
                guard.checkpoint()
                try:
                    geometry = member_start(key, direction)
                except (ValueError, np.linalg.LinAlgError):
                    return float("inf")
                delta = q[key] - geometry.origin
                height = delta @ direction
                residual = (
                    height
                    if members[key].selection.kind == "plane"
                    else (
                        np.linalg.norm(delta - height[:, None] * direction, axis=1)
                        - geometry.radius
                        - geometry.slope * height
                    )
                    / np.hypot(1, geometry.slope)
                )
                w = weights[key] / maximum_weight
                total += float(w @ residual**2)
                area += float(w.sum())
            return total / area if area else 0.0

        proposals[cluster] = sorted(unique, key=score)[:8]
        if not proposals[cluster]:
            raise DirectionFitFailure(
                "invalid-input",
                "a free direction has no selected evidence or finite initializer",
            )
    intent: dict[str, Any] = {
        "family": "selected-direction-component",
        "semantic_intent": component.semantic_intent,
        "members": [
            {
                "id": key,
                "kind": m.selection.kind,
                "ids": m.selection.ids,
                "axis": m.axis,
                "datum": m.datum,
                "construction": m.construction,
                "point": m.point,
            }
            for key, m in members.items()
        ],
        "directions": [asdict(value) for value in component.directions],
        "relationships": component.relationships,
        "radii": component.radii,
        "rotations": [asdict(value) for value in component.rotations],
        "mirrors": [asdict(value) for value in component.mirrors],
        "radius_distances": [asdict(value) for value in component.radius_distances],
        "axes": {key: _provider_intent(value) for key, value in axes.items()},
        "planes": {key: _provider_intent(value) for key, value in planes.items()},
        "centers": {key: _provider_intent(value) for key, value in centers.items()},
        "support": {key: support_record(value) for key, value in supports.items()},
        "support_policy": SUPPORT_POLICY_VERSION,
        "gradient_tolerance": component.gradient_tolerance,
    }
    binding = initialization_binding(
        workspace.source_sha256,
        workspace.source_descriptor.interpretation_sha256
        if workspace.source_descriptor
        else workspace.reference_sha256,
        selected_ids,
        intent,
        policy_version=DIRECTION_POLICY_VERSION,
        policy=policy,
    )
    fresh: list[FitCandidate] = []
    signatures: set[tuple[float, ...]] = set()
    for index in range(min(16, policy.max_proposals)):
        values = tuple(
            float(v)
            for cluster in classes
            for v in proposals[cluster][index % len(proposals[cluster])]
        )
        if values in signatures:
            continue
        signatures.add(values)
        fresh.append(
            FitCandidate(
                f"selected-direction-{index}",
                values,
                "bounded selected-normal/covariance directions with independent member placement",
                _CHART,
                binding.sha256,
            )
        )
    failures: dict[str, Any] = {}

    def solve(candidate: FitCandidate, control: AttemptControl) -> dict[str, Any]:
        try:
            control.checkpoint()
            directions = {
                key: _unit(candidate.parameters[3 * i : 3 * i + 3])
                for i, key in enumerate(classes)
            }
            axis_records: dict[str, AxisInput] = {}
            plane_records: dict[str, PlaneInput] = {}
            point_records: dict[str, PointInput] = {}
            for key in dict.fromkeys(ar.values()):
                original = axes[key]
                old = _axis_value(original)
                direction = directions[root(("axis", key))]
                if old is not None and direction @ old[1] < 0:
                    direction = -direction
                position_free = (
                    original.free
                    if original.position_free is None
                    else original.position_free
                )
                if not _orientation_free(original) and not position_free:
                    axis_records[key] = deepcopy(original)
                    continue
                observed = next(
                    (name for name in surface_axes if ar[surface_axes[name]] == key),
                    None,
                )
                if observed is not None and position_free:
                    geometry = member_start(observed, direction)
                    anchor = frame.origin + frame.length * geometry.origin
                else:
                    anchor = frame.origin if old is None else old[0]
                axis_records[key] = replace(
                    original,
                    result={
                        "point_display": anchor.tolist(),
                        "axis_display": direction.tolist(),
                        "axis_is_directed": True,
                    },
                )
            for key, provider in ar.items():
                if key != provider:
                    old_alias, old_provider = (
                        _axis_value(axes[key]),
                        _axis_value(axes[provider]),
                    )
                    sign = (
                        (-1 if old_alias[1] @ old_provider[1] < 0 else 1)
                        if old_alias is not None and old_provider is not None
                        else (-1 if axes[key].result.get("direction_reversed") else 1)
                    )
                    value = deepcopy(axis_records[provider].result)
                    value["axis_display"] = (
                        sign * np.asarray(value["axis_display"])
                    ).tolist()
                    axis_records[key] = replace(axes[key], result=value)
            for key in dict.fromkeys(pr.values()):
                original = planes[key]
                old = _plane_value(original)
                offset_free = (
                    original.free
                    if original.offset_free is None
                    else original.offset_free
                )
                if not _orientation_free(original) and not offset_free:
                    plane_records[key] = deepcopy(original)
                    continue
                direction = directions[root(("plane", key))]
                orientation_hint = (
                    np.asarray(axis_records[original.axis].result["axis_display"])
                    if original.axis is not None
                    and original.construction == "perpendicular_to_axis"
                    else old[:3]
                    if old is not None
                    else None
                )
                if orientation_hint is not None and direction @ orientation_hint < 0:
                    direction = -direction
                observed = next(
                    (name for name in plane_keys if plane_keys[name] == key), None
                )
                if observed is not None and offset_free:
                    offset = float(
                        direction
                        @ (normalized_weights(weights[observed]) @ points[observed])
                    )
                elif old is not None:
                    offset = float(old[3])
                elif original.axis:
                    anchor = np.asarray(
                        axis_records[original.axis].result["point_display"]
                    )
                    offset = float(direction @ anchor) + (
                        original.offset_from_axis or 0
                    )
                else:
                    offset = float(direction @ frame.origin)
                plane_records[key] = replace(
                    original, result={"plane_equation": [*direction.tolist(), offset]}
                )
            for key, provider in pr.items():
                if key != provider:
                    old_alias, old_provider = (
                        _plane_value(planes[key]),
                        _plane_value(planes[provider]),
                    )
                    sign = (
                        -1
                        if old_alias is not None
                        and old_provider is not None
                        and old_alias[:3] @ old_provider[:3] < 0
                        else 1
                    )
                    plane_records[key] = replace(
                        planes[key],
                        result={
                            "plane_equation": (
                                sign
                                * np.asarray(
                                    plane_records[provider].result["plane_equation"]
                                )
                            ).tolist()
                        },
                    )
            for key in dict.fromkeys(cr.values()):
                original = centers[key]
                observed = next(
                    (
                        name
                        for name in surface_points
                        if cr[surface_points[name]] == key
                    ),
                    None,
                )
                if original.free and observed is not None:
                    point = frame.origin + frame.length * _sphere_start(
                        q[observed], weights[observed]
                    )
                    point_records[key] = replace(
                        original, result={"point_display": point.tolist()}
                    )
                else:
                    point_records[key] = deepcopy(original)
            for key, provider in cr.items():
                if key != provider:
                    point_records[key] = replace(
                        centers[key], result=deepcopy(point_records[provider].result)
                    )
            inputs: list[SurfaceInput] = []
            for key, member in members.items():
                control.checkpoint()
                kind = member.selection.kind
                if kind == "plane":
                    direction = directions[root(("plane", plane_keys[key]))]
                    mean = normalized_weights(weights[key]) @ points[key]
                    value = {
                        "kind": kind,
                        "plane_equation": [
                            *direction.tolist(),
                            float(direction @ mean),
                        ],
                    }
                elif kind == "sphere":
                    center = np.asarray(
                        point_records[surface_points[key]].result["point_display"]
                    )
                    radius = float(
                        normalized_weights(weights[key])
                        @ np.linalg.norm(points[key] - center, axis=1)
                    )
                    value = {"kind": kind, "parameters": [*center.tolist(), radius]}
                else:
                    axis = axis_records[surface_axes[key]].result
                    anchor, direction = (
                        np.asarray(axis["point_display"]),
                        np.asarray(axis["axis_display"]),
                    )
                    geometry = _line_start(
                        q[key],
                        weights[key],
                        (anchor - frame.origin) / frame.length,
                        direction,
                        kind == "cone",
                    )
                    resolved = resolve_support(
                        supports[key],
                        anchor,
                        direction,
                        points[key],
                        reference=frame.origin,
                    )
                    radius = (
                        frame.length * geometry.radius
                        + geometry.slope * resolved.origin_shift
                    )
                    taper = geometry.slope * resolved.axis_sign
                    if np.any(radius + taper * np.asarray(resolved.domain) <= 0):
                        raise ValueError("nonpositive candidate finite support")
                    value = {
                        "kind": kind,
                        "analytic": {
                            "kind": kind,
                            "origin": list(resolved.origin),
                            "axis": list(resolved.axis),
                            "radius": float(radius),
                            "slope": float(taper),
                            "axial_domain": list(resolved.domain),
                        },
                        "anchor_reference": frame.origin.tolist(),
                        "support_intent": support_record(supports[key]),
                    }
                inputs.append(
                    SurfaceInput(
                        key,
                        kind,
                        points[key],
                        weights[key],
                        value,
                        axis=surface_axes.get(key, member.axis),
                        datum=member.datum,
                        construction=member.construction,
                        point=surface_points.get(key),
                    )
                )
            return fit_relationship_component(
                inputs,
                axis_records,
                [(kind, list(ids)) for kind, ids in component.relationships],
                [list(ids) for ids in component.radii],
                point_records,
                control=control,
                planes=plane_records,
                directions=component.directions,
                rotations=component.rotations,
                mirrors=component.mirrors,
                radius_distances=component.radius_distances,
                gradient_tolerance=component.gradient_tolerance,
                coordinate_frame=frame,
            )
        except RelationshipFailure as error:
            failures[candidate.id] = deepcopy(error.diagnostic)
            raise LeastSquaresFailure(cast(Any, error.code), str(error)) from error
        except (ValueError, np.linalg.LinAlgError) as error:
            failures[candidate.id] = deepcopy(
                getattr(
                    error,
                    "diagnostic",
                    {"kind": "initializer_failure", "message": str(error)},
                )
            )
            raise LeastSquaresFailure("invalid-initial-geometry", str(error)) from error

    def assess(output: dict[str, Any]) -> FitAssessment:
        total = area = 0.0
        for key in members:
            residual = (
                surface_distance(points[key], output["surfaces"][key])[0] / frame.length
            )
            w = weights[key] / maximum_weight
            total += float(w @ residual**2)
            area += float(w.sum())
        solver = output["solver"]
        return FitAssessment(
            total / area,
            bool(solver["condition"] <= 1e6),
            solver["rank"],
            solver["condition"],
            solver["termination"],
            solver["iterations"],
        )

    def equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
        tolerance = 1e-7
        for key, member in members.items():
            a, b = left["surfaces"][key], right["surfaces"][key]
            if member.selection.kind == "sphere":
                if (
                    np.linalg.norm(np.asarray(a["parameters"]) - b["parameters"])
                    > tolerance * frame.length
                ):
                    return False
            elif member.selection.kind == "plane":
                ea, eb = (
                    np.asarray(a["plane_equation"]),
                    np.asarray(b["plane_equation"]),
                )
                if ea[:3] @ eb[:3] < 0:
                    eb = -eb
                if (
                    np.linalg.norm(ea[:3] - eb[:3]) > tolerance
                    or abs(ea[3] - eb[3] - (ea[:3] - eb[:3]) @ frame.origin)
                    > tolerance * frame.length
                ):
                    return False
            else:
                ga, gb = a["analytic"], b["analytic"]
                da, db = np.asarray(ga["axis"]), np.asarray(gb["axis"])
                oa, ob = np.asarray(ga["origin"]), np.asarray(gb["origin"])
                mean = normalized_weights(weights[key]) @ points[key]
                ra = ga["radius"] + ga["slope"] * float((mean - oa) @ da)
                rb = gb["radius"] + gb["slope"] * float((mean - ob) @ db)
                sign = 1 if da @ db >= 0 else -1
                if (
                    np.linalg.norm(np.cross(da, db)) > tolerance
                    or np.linalg.norm(np.cross(ob - oa, da)) > tolerance * frame.length
                    or abs(ra - rb) > tolerance * frame.length
                    or abs(ga["slope"] - sign * gb["slope"]) > tolerance
                ):
                    return False
        return True

    try:
        recovered = recover_fit(
            RecoveryProblem(
                binding, {_CHART: 3 * len(classes)}, solve, assess, equivalent
            ),
            fresh,
            cancelled=cancelled,
        )
    except RecoveryFailure as error:
        raise DirectionFitFailure(
            error.code,
            str(error),
            {"recovery": error.diagnostics.json(), "attempt_failures": failures},
        ) from error
    guard.checkpoint()
    output = recovered.result
    initialization = {
        "algorithm": DIRECTION_POLICY_VERSION,
        "chart": _CHART,
        "binding_sha256": binding.sha256,
        "recovery": recovered.diagnostics.json(),
        "attempt_failures": failures,
        "observations": len(selected_ids),
        "origin": frame.origin.tolist(),
        "length": frame.length,
        "independent_axes": surface_axes.copy(),
        "support_policy": SUPPORT_POLICY_VERSION,
    }
    for key, member in members.items():
        value = output["surfaces"][key]
        _, expected, inside = surface_distance(points[key], value)
        orientation = float(
            normalized_weights(weights[key]) @ np.sum(expected * normals[key], axis=1)
        )
        value.update(
            ids=member.selection.ids.copy(),
            normal_sign=1.0 if orientation >= 0 else -1.0,
            support_classification={
                "inside_vertices": int(inside.sum()),
                "outside_vertices": int((~inside).sum()),
                "role": "selection-and-display",
            },
            initialization=deepcopy(initialization),
        )
    output.update(
        initialization=initialization,
        selected_area={key: float(value.sum()) for key, value in weights.items()},
        session={"source_sha256": workspace.source_sha256, "schema_version": 2},
    )
    guard.checkpoint()
    return output
