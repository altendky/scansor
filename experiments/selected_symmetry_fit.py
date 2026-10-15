"""Selected-data initialization and bounded recovery for canonical joint fits.

This adapter consumes declarations and original selected rows. Independent
member fits are never prerequisites, and authored provider/support roles remain
separate from disposable numerical starts. Graph enablement is a later layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field, replace
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
    MirrorInput,
    PlaneInput,
    RadiusDistanceInput,
    RelationshipFailure,
    RotationInput,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import AttemptControl, normalized_weights
from experiments.mesh_cylinder_fit import cylinder_basis
from experiments.scan_workspace import ScanWorkspace
from experiments.selection_growth import surface_distance
from experiments.support_intent import (
    SUPPORT_POLICY_VERSION,
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    resolve_support,
    support_record,
)
from scansor.nonlinear_least_squares import LeastSquaresFailure

if TYPE_CHECKING:
    from experiments.nozzle_coaxial import FitSelection

Array = NDArray[np.float64]
SYMMETRY_POLICY_VERSION = "selected-symmetry-group-recovery-v1"
_CHART = "workspace-joint-state-v1"


class SymmetryFitFailure(ValueError):
    """A joint adapter never publishes an unsuccessful iterate as geometry."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "symmetry_fit_failure",
            "code": code,
            "message": message,
            **deepcopy(details or {}),
        }
        super().__init__(message)


@dataclass(frozen=True)
class SelectedSurface:
    selection: FitSelection
    axis: str | None = None
    datum: str | None = None
    construction: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "selection", replace(self.selection, ids=list(self.selection.ids))
        )


@dataclass(frozen=True)
class SelectedSymmetryGroup:
    surfaces: tuple[SelectedSurface, ...]
    common_axis: str
    axes: Mapping[str, AxisInput]
    planes: Mapping[str, PlaneInput] = field(default_factory=dict)
    rotations: tuple[RotationInput, ...] = ()
    mirrors: tuple[MirrorInput, ...] = ()
    relationships: tuple[tuple[str, tuple[str, ...]], ...] = ()
    radii: tuple[tuple[str, ...], ...] = ()
    radius_distances: tuple[RadiusDistanceInput, ...] = ()
    common_axis_directed: bool = False
    semantic_intent: dict[str, Any] | None = None
    axis_alias_signs: Mapping[str, int] = field(default_factory=dict)
    plane_alias_signs: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "surfaces", tuple(self.surfaces))
        for name in ("axes", "planes", "axis_alias_signs", "plane_alias_signs"):
            object.__setattr__(
                self, name, MappingProxyType(deepcopy(dict(getattr(self, name))))
            )
        for name in ("rotations", "mirrors", "radius_distances"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "relationships",
            tuple((kind, tuple(ids)) for kind, ids in self.relationships),
        )
        object.__setattr__(self, "radii", tuple(tuple(ids) for ids in self.radii))
        object.__setattr__(self, "semantic_intent", deepcopy(self.semantic_intent))


@dataclass(frozen=True)
class _Geometry:
    origin: Array
    axis: Array
    radius: float = 0.0
    slope: float = 0.0


def _array(value: Any, shape: tuple[int, ...]) -> Array:
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf":
        raise ValueError("expected real numbers")
    result = np.array(raw, dtype=float, copy=True)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError("invalid shape or nonfinite numbers")
    return result


def _unit(value: Any) -> Array:
    vector = _array(value, (3,))
    maximum = float(np.max(np.abs(vector)))
    if maximum <= 0:
        raise ValueError("a direction must be nonzero")
    vector /= maximum
    return vector / np.linalg.norm(vector)


def _axis_value(value: AxisInput) -> tuple[Array, Array] | None:
    if not isinstance(cast(object, value.result), Mapping):
        return None
    try:
        return (
            _array(value.result.get("point_display"), (3,)),
            _unit(value.result.get("axis_display")),
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _plane_value(value: PlaneInput) -> Array | None:
    if not isinstance(cast(object, value.result), Mapping):
        return None
    try:
        equation = _array(value.result.get("plane_equation"), (4,))
        maximum = float(np.max(np.abs(equation[:3])))
        if maximum <= 0:
            return None
        scale = maximum * float(np.linalg.norm(equation[:3] / maximum))
        if not np.isfinite(scale) or scale <= 0:
            return None
        equation /= scale
        return equation if np.isfinite(equation).all() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _roots(values: Mapping[str, Any]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for key in values:
        seen: set[str] = set()
        current = key
        while True:
            if current not in values or current in seen:
                raise SymmetryFitFailure("invalid-input", "missing or cyclic provider")
            seen.add(current)
            source = values[current].source
            if source is None:
                roots[key] = current
                break
            current = source
    return roots


def _signs(
    values: Mapping[str, Any], roots: dict[str, str], declared: Mapping[str, int]
) -> dict[str, int]:
    if any(
        key not in values or type(sign) is not int or sign not in (-1, 1)
        for key, sign in declared.items()
    ):
        raise SymmetryFitFailure("invalid-input", "invalid provider alias sign")
    signs: dict[str, int] = {}
    for key, root in roots.items():
        if key == root:
            if declared.get(key, 1) != 1:
                raise SymmetryFitFailure(
                    "invalid-input", "a root cannot reverse itself"
                )
            signs[key] = 1
            continue
        if key in declared:
            signs[key] = declared[key]
            continue
        left, right = values[key], values[root]
        if isinstance(left, AxisInput):
            a, b = _axis_value(left), _axis_value(right)
            valid = a is not None and b is not None
            dot = 0.0 if a is None or b is None else float(a[1] @ b[1])
        else:
            a, b = _plane_value(left), _plane_value(right)
            valid = a is not None and b is not None
            dot = 0.0 if a is None or b is None else float(a[:3] @ b[:3])
        if not valid or dot == 0:
            raise SymmetryFitFailure(
                "invalid-input", "provider alias needs an explicit orientation sign"
            )
        signs[key] = 1 if dot > 0 else -1
    return signs


def _directions(
    points: Array, weights: Array, normals: Array, cone: bool
) -> list[Array]:
    w = normalized_weights(weights)
    p = points - w @ points
    n = normals / np.linalg.norm(normals, axis=1)[:, None]
    matrices: list[Array] = []
    if cone:
        centered = n - w @ n
        matrices.append(centered.T @ (w[:, None] * centered))
    matrices.extend((n.T @ (w[:, None] * n), p.T @ (w[:, None] * p)))
    result: list[Array] = []
    for matrix in matrices:
        _, vectors = np.linalg.eigh(matrix)
        for index in range(3):
            vector = vectors[:, index].copy()
            if vector[int(np.argmax(np.abs(vector)))] < 0:
                vector *= -1
            if not any(abs(float(vector @ old)) > 1 - 1e-10 for old in result):
                result.append(vector)
    return result


def _circle_start(
    points: Array, weights: Array, axis: Array, cone: bool
) -> tuple[_Geometry, dict[str, Any]]:
    w = normalized_weights(weights)
    origin = w @ points
    basis = cylinder_basis(axis)
    local = (points - origin) @ basis
    height, xy = local[:, 2], local[:, :2]
    transverse_scale = float(np.sqrt(w @ np.sum(xy * xy, axis=1)))
    if transverse_scale <= 0 or not np.isfinite(transverse_scale):
        raise ValueError("no transverse initializer coverage")
    xy = xy / transverse_scale
    axial_scale = float(np.sqrt(w @ (height * height)))
    h = height / axial_scale if axial_scale > 0 else height
    nuisance = (
        np.column_stack((np.ones(len(points)), h, h * h))
        if cone
        else np.ones((len(points), 1))
    )
    root = np.sqrt(w)
    nuisance = root[:, None] * nuisance
    design = root[:, None] * (2 * xy)
    target = root * np.sum(xy * xy, axis=1)
    design -= nuisance @ np.linalg.lstsq(nuisance, design, rcond=None)[0]
    target -= nuisance @ np.linalg.lstsq(nuisance, target, rcond=None)[0]
    columns = np.linalg.norm(design, axis=0)
    if np.any(columns <= 0) or not np.isfinite(columns).all():
        raise ValueError("unresolved algebraic center")
    solution, _, rank, singular = np.linalg.lstsq(design / columns, target, rcond=None)
    if rank != 2:
        raise ValueError("rank-deficient algebraic center")
    center = solution / columns * transverse_scale
    anchor = origin + basis[:, :2] @ center
    rho = np.linalg.norm(local[:, :2] - center, axis=1)
    regression = (
        np.column_stack((np.ones(len(points)), h))
        if cone
        else np.ones((len(points), 1))
    )
    coefficients, _, radial_rank, _ = np.linalg.lstsq(
        root[:, None] * regression, root * rho, rcond=None
    )
    if radial_rank != regression.shape[1]:
        raise ValueError("rank-deficient radius/taper initializer")
    radius = float(coefficients[0])
    slope = float(coefficients[1] / axial_scale) if cone else 0.0
    return _Geometry(anchor, axis, radius, slope), {
        "center_rank": int(rank),
        "radial_rank": int(radial_rank),
        "center_condition": float(singular[0] / singular[-1]),
    }


def _line_start(
    points: Array, weights: Array, origin: Array, axis: Array, cone: bool
) -> _Geometry:
    w = normalized_weights(weights)
    relative = points - origin
    height = relative @ axis
    rho = np.linalg.norm(relative - height[:, None] * axis, axis=1)
    scale = float(np.sqrt(w @ height**2))
    regression = (
        np.column_stack((np.ones(len(points)), height / scale))
        if cone and scale > 0
        else np.ones((len(points), 1))
    )
    coefficients = np.linalg.lstsq(
        np.sqrt(w)[:, None] * regression, np.sqrt(w) * rho, rcond=None
    )[0]
    return _Geometry(
        origin,
        axis,
        float(coefficients[0]),
        float(coefficients[1] / scale) if len(coefficients) > 1 else 0.0,
    )


def _start(
    points: Array, weights: Array, normals: Array, kind: str
) -> tuple[_Geometry, dict[str, Any]]:
    w = normalized_weights(weights)
    if kind == "plane":
        origin = w @ points
        p = points - origin
        values, vectors = np.linalg.eigh(p.T @ (w[:, None] * p))
        normal = vectors[:, 0]
        if normal @ (w @ normals) < 0:
            normal = -normal
        return _Geometry(origin, normal), {"covariance_eigenvalues": values.tolist()}
    best: tuple[float, _Geometry, dict[str, Any]] | None = None
    for direction in _directions(points, weights, normals, kind == "cone"):
        try:
            geometry, evidence = _circle_start(
                points, weights, direction, kind == "cone"
            )
        except (ValueError, np.linalg.LinAlgError):
            continue
        p = points - geometry.origin
        height = p @ geometry.axis
        rho = np.linalg.norm(p - height[:, None] * geometry.axis, axis=1)
        residual = (rho - geometry.radius - geometry.slope * height) / np.hypot(
            1, geometry.slope
        )
        objective = float(w @ residual**2)
        if np.isfinite(objective) and (best is None or objective < best[0]):
            best = objective, geometry, evidence
    if best is None:
        raise ValueError("no finite algebraic lateral initializer")
    return best[1], best[2]


def _rotation(axis: Array, angle: float) -> Array:
    x, y, z = axis
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * cross
        + (1 - np.cos(angle)) * np.outer(axis, axis)
    )


def fit_selected_symmetry_group(
    workspace: ScanWorkspace,
    group: SelectedSymmetryGroup,
    *,
    policy: RecoveryPolicy | None = None,
    hint_binding_sha256: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fit one declared group from its original selected observations."""
    try:
        return _fit_selected_symmetry_group(
            workspace,
            group,
            policy=policy,
            hint_binding_sha256=hint_binding_sha256,
            cancelled=cancelled,
        )
    except np.linalg.LinAlgError as error:
        raise SymmetryFitFailure("numeric-failure", str(error)) from error


def _fit_selected_symmetry_group(
    workspace: ScanWorkspace,
    group: SelectedSymmetryGroup,
    *,
    policy: RecoveryPolicy | None,
    hint_binding_sha256: str | None,
    cancelled: Callable[[], bool] | None,
) -> dict[str, Any]:
    policy = RecoveryPolicy() if policy is None else policy
    guard = AttemptControl(policy.attempt, cancelled)
    guard.checkpoint()
    if workspace.descriptor is None or workspace.source_sha256 is None:
        raise SymmetryFitFailure("invalid-input", "an accepted source is required")
    if (
        any(
            not isinstance(cast(object, value), AxisInput)
            for value in group.axes.values()
        )
        or any(
            not isinstance(cast(object, value), PlaneInput)
            for value in group.planes.values()
        )
        or any(
            not isinstance(cast(object, value), RotationInput)
            for value in group.rotations
        )
        or any(
            not isinstance(cast(object, value), MirrorInput) for value in group.mirrors
        )
        or any(
            not isinstance(cast(object, value), RadiusDistanceInput)
            for value in group.radius_distances
        )
        or type(group.common_axis_directed) is not bool
    ):
        raise SymmetryFitFailure("invalid-input", "invalid declaration record types")
    for value in (*group.axes.values(), *group.planes.values()):
        if type(value.free) is not bool or (
            value.source is not None and not isinstance(cast(object, value.source), str)
        ):
            raise SymmetryFitFailure("invalid-input", "invalid provider role")
    by_id = {surface.selection.id: surface for surface in group.surfaces}
    if (
        not by_id
        or len(by_id) != len(group.surfaces)
        or group.common_axis not in group.axes
    ):
        raise SymmetryFitFailure(
            "invalid-input", "unique members and a common axis are required"
        )
    if any(
        not isinstance(cast(object, key), str) or not key
        for key in [*by_id, *group.axes, *group.planes]
    ):
        raise SymmetryFitFailure(
            "invalid-input", "declaration IDs must be nonempty strings"
        )
    ar = _roots(group.axes)
    pr = _roots(group.planes)
    axis_sign = _signs(group.axes, ar, group.axis_alias_signs)
    plane_sign = _signs(group.planes, pr, group.plane_alias_signs)
    common_root = ar[group.common_axis]
    constructions = {None, "contains_axis", "parallel_to_axis", "perpendicular_to_axis"}
    for value in group.planes.values():
        if (
            value.construction not in constructions
            or ((value.axis is None) != (value.construction is None))
            or (value.axis is not None and value.axis not in ar)
        ):
            raise SymmetryFitFailure(
                "invalid-input", "invalid plane datum construction"
            )
    for value in group.surfaces:
        if value.axis is not None and value.axis not in ar:
            raise SymmetryFitFailure("invalid-input", "surface axis is unavailable")
        if value.datum is not None and value.datum not in pr:
            raise SymmetryFitFailure("invalid-input", "surface datum is unavailable")
        if value.construction not in constructions:
            raise SymmetryFitFailure("invalid-input", "invalid surface construction")
        if value.selection.kind != "plane" and (
            value.datum is not None or value.construction is not None
        ):
            raise SymmetryFitFailure(
                "invalid-input", "only planes can use plane constructions"
            )
    for relationship, members in group.relationships:
        if (
            relationship not in {"parallel", "coincident"}
            or len(members) < 2
            or len(set(members)) != len(members)
            or any(
                key not in by_id or by_id[key].selection.kind != "plane"
                for key in members
            )
        ):
            raise SymmetryFitFailure("invalid-input", "invalid plane relationship")
    for members in group.radii:
        if (
            len(members) < 2
            or len(set(members)) != len(members)
            or any(
                key not in by_id
                or by_id[key].selection.kind not in {"cylinder", "cone"}
                for key in members
            )
        ):
            raise SymmetryFitFailure("invalid-input", "invalid equal-radius group")
    for relation in group.radius_distances:
        if (
            relation.radius_surface not in by_id
            or by_id[relation.radius_surface].selection.kind != "cylinder"
            or relation.plane_surface not in by_id
            or by_id[relation.plane_surface].selection.kind != "plane"
            or relation.reference_plane not in pr
            or (
                relation.sign is not None
                and (type(relation.sign) is not int or relation.sign not in (-1, 1))
            )
        ):
            raise SymmetryFitFailure(
                "invalid-input", "invalid cylinder radius-distance relation"
            )
    for key in set(ar.values()):
        if not group.axes[key].free:
            value = _axis_value(group.axes[key])
            if value is None:
                raise SymmetryFitFailure(
                    "invalid-input", "fixed axis provider is malformed"
                )
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                norm = float(
                    np.linalg.norm(
                        np.asarray(
                            group.axes[key].result.get("axis_display"), dtype=float
                        )
                    )
                )
            if not np.isfinite(norm) or norm <= 0:
                raise SymmetryFitFailure(
                    "invalid-input", "fixed axis provider is malformed"
                )
    for key in set(pr.values()):
        if not group.planes[key].free:
            if _plane_value(group.planes[key]) is None:
                raise SymmetryFitFailure(
                    "invalid-input", "fixed plane provider is malformed"
                )
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                norm = float(
                    np.linalg.norm(
                        np.asarray(group.planes[key].result["plane_equation"])[:3]
                    )
                )
            if not np.isfinite(norm) or norm <= 0:
                raise SymmetryFitFailure(
                    "invalid-input", "fixed plane provider is malformed"
                )
    orbit_members: set[str] = set()
    for relation in (*group.rotations, *group.mirrors):
        required = 3 if isinstance(relation, RotationInput) else 2
        if (
            len(relation.members) != required
            or len(set(relation.members)) != required
            or any(key not in by_id for key in relation.members)
        ):
            raise SymmetryFitFailure("invalid-input", "invalid symmetry membership")
        if len({by_id[key].selection.kind for key in relation.members}) != 1:
            raise SymmetryFitFailure(
                "invalid-input", "symmetry members must have the same kind"
            )
        if orbit_members.intersection(relation.members):
            raise SymmetryFitFailure(
                "invalid-input", "a member belongs to multiple symmetry groups"
            )
        orbit_members.update(relation.members)
        if isinstance(relation, RotationInput) and (
            relation.axis not in ar or ar[relation.axis] != common_root
        ):
            raise SymmetryFitFailure(
                "invalid-input", "rotation must use the declared common provider"
            )
        if isinstance(relation, MirrorInput) and relation.plane not in pr:
            raise SymmetryFitFailure("invalid-input", "mirror plane is unavailable")
    selections = [surface.selection for surface in group.surfaces]
    selected_ids: list[int] = []
    for selected in selections:
        guard.checkpoint()
        if selected.kind not in {"plane", "cylinder", "cone"} or not selected.ids:
            raise SymmetryFitFailure(
                "invalid-input", "members need supported nonempty selections"
            )
        if any(
            type(index) is not int or index < 0 or index >= len(workspace.local)
            for index in selected.ids
        ):
            raise SymmetryFitFailure("invalid-input", "selection indices are invalid")
        selected_ids.extend(selected.ids)
    if len(set(selected_ids)) != len(selected_ids):
        raise SymmetryFitFailure("selection-overlap", "joint selections overlap")
    points = {s.id: workspace.local[s.ids] for s in selections}
    weights = {s.id: workspace.data.weights[s.ids] for s in selections}
    normals = {
        s.id: workspace.data.normals[s.ids] @ workspace.frame for s in selections
    }
    try:
        frame = CoordinateFrame.from_observations(
            workspace.local[selected_ids], workspace.data.weights[selected_ids]
        )
        for key in by_id:
            if not np.isfinite(normals[key]).all() or np.any(
                np.linalg.norm(normals[key], axis=1) <= 0
            ):
                raise ValueError("invalid selected normal directions")
            _ = normalized_weights(weights[key])
    except ValueError as error:
        raise SymmetryFitFailure("invalid-input", str(error)) from error
    q = {key: (value - frame.origin) / frame.length for key, value in points.items()}
    supports: dict[str, SupportIntent] = {}
    for selected in selections:
        if selected.kind == "plane":
            continue
        support = (
            selected.support
            if selected.support is not None
            else legacy_support(selected.domain)
        )
        if selected.domain is not None and support != legacy_support(selected.domain):
            raise SymmetryFitFailure("invalid-support", "conflicting authored support")
        supports[selected.id] = support
    surface_axes = {
        s.selection.id: s.axis
        or (
            f"@member:{s.selection.id}"
            if s.selection.id in orbit_members
            else group.common_axis
        )
        for s in group.surfaces
        if s.selection.kind != "plane"
    }
    axes = dict(group.axes)
    for key in surface_axes.values():
        if key not in axes:
            axes[key] = AxisInput({}, True)
    ar = _roots(axes)
    axis_sign.update({key: 1 for key in axes if key not in axis_sign})
    mains = [
        s
        for s in selections
        if s.kind != "plane"
        and s.id not in orbit_members
        and ar[surface_axes[s.id]] == common_root
    ]
    common_fixed = not axes[common_root].free
    directed = common_fixed or group.common_axis_directed
    intent: dict[str, Any] = {
        "family": "selected-symmetry-group",
        "semantic_intent": group.semantic_intent,
        "members": [
            {
                "id": s.selection.id,
                "ids": s.selection.ids,
                "kind": s.selection.kind,
                "axis": s.axis,
                "datum": s.datum,
                "construction": s.construction,
            }
            for s in group.surfaces
        ],
        "common_axis": group.common_axis,
        "common_axis_directed": directed,
        "axes": {
            key: {"free": value.free, "source": value.source, "sign": axis_sign[key]}
            for key, value in axes.items()
        },
        "planes": {
            key: {
                "free": value.free,
                "source": value.source,
                "axis": value.axis,
                "construction": value.construction,
                "sign": plane_sign[key],
            }
            for key, value in group.planes.items()
        },
        "rotations": [{"axis": r.axis, "members": r.members} for r in group.rotations],
        "mirrors": [{"plane": m.plane, "members": m.members} for m in group.mirrors],
        "relationships": group.relationships,
        "radii": group.radii,
        "radius_distances": [
            {
                "radius_surface": r.radius_surface,
                "plane_surface": r.plane_surface,
                "reference_plane": r.reference_plane,
                "sign": r.sign,
            }
            for r in group.radius_distances
        ],
        "support_policy": SUPPORT_POLICY_VERSION,
    }
    if group.semantic_intent is None or "recipe" not in group.semantic_intent:
        intent["support"] = {
            key: support_record(value) for key, value in supports.items()
        }
        intent["fixed_axes"] = {
            key: {
                name: value.result.get(name)
                for name in ("point_display", "axis_display")
            }
            for key, value in axes.items()
            if not value.free and value.source is None
        }
        intent["fixed_planes"] = {
            key: {"plane_equation": value.result.get("plane_equation")}
            for key, value in group.planes.items()
            if not value.free and value.source is None
        }
    try:
        binding = initialization_binding(
            workspace.source_sha256,
            workspace.descriptor.interpretation_sha256,
            selected_ids,
            intent,
            policy_version=SYMMETRY_POLICY_VERSION,
            policy=policy,
        )
    except (TypeError, ValueError) as error:
        raise SymmetryFitFailure(
            "invalid-input", f"invalid semantic binding: {error}"
        ) from error
    initialization: dict[str, Any] = {
        "algorithm": SYMMETRY_POLICY_VERSION,
        "chart": _CHART,
        "origin": frame.origin.tolist(),
        "length": frame.length,
        "observations": len(selected_ids),
        "support_policy": SUPPORT_POLICY_VERSION,
        "binding_sha256": binding.sha256,
        "common_axis_directed": directed,
    }
    failures: dict[str, Any] = {}
    evidence: dict[str, Any] = {}

    def common_point(direction: Array) -> Array:
        fixed_value = _axis_value(axes[common_root])
        if common_fixed:
            assert fixed_value is not None
            return fixed_value[0].copy()
        if not mains:
            return frame.origin.copy()
        p = np.vstack([q[s.id] for s in mains])
        w = normalized_weights(np.concatenate([weights[s.id] for s in mains]))
        basis = cylinder_basis(direction)
        local = p @ basis
        xy, h = local[:, :2], local[:, 2]
        indicators = np.zeros((len(p), len(mains)))
        offset = 0
        extra: list[Array] = []
        for index, selected in enumerate(mains):
            indicators[offset : offset + len(selected.ids), index] = 1
            mask = indicators[:, index]
            if selected.kind == "cone":
                extra.extend((mask * h, mask * h * h))
            offset += len(selected.ids)
        nuisance = np.sqrt(w)[:, None] * np.column_stack([indicators, *extra])
        design = np.sqrt(w)[:, None] * (2 * xy)
        target = np.sqrt(w) * np.sum(xy * xy, axis=1)
        design -= nuisance @ np.linalg.lstsq(nuisance, design, rcond=None)[0]
        target -= nuisance @ np.linalg.lstsq(nuisance, target, rcond=None)[0]
        center = np.linalg.lstsq(design, target, rcond=None)[0]
        return frame.origin + frame.length * (basis[:, :2] @ center)

    if common_fixed:
        fixed_value = _axis_value(axes[common_root])
        assert fixed_value is not None
        common_directions = [axis_sign[group.common_axis] * fixed_value[1]]
    else:
        main_ids = [s.id for s in mains] or list(by_id)
        common_directions = _directions(
            np.vstack([q[key] for key in main_ids]),
            np.concatenate([weights[key] for key in main_ids]),
            np.vstack([normals[key] for key in main_ids]),
            any(by_id[key].selection.kind == "cone" for key in main_ids),
        )
        if mains and any(selected.kind == "cone" for selected in mains):
            # Different cone tapers have different constant normal-axis dots.
            # Center each factor before pooling, preserving its observation mass.
            scatter = np.zeros((3, 3))
            maximum = max(float(np.max(weights[selected.id])) for selected in mains)
            for selected in mains:
                n = normals[selected.id]
                n = n / np.linalg.norm(n, axis=1)[:, None]
                w = weights[selected.id] / maximum
                n = n - normalized_weights(w) @ n
                scatter += n.T @ (w[:, None] * n)
            preferred = np.linalg.eigh(scatter)[1][:, 0]
            if preferred[int(np.argmax(np.abs(preferred)))] < 0:
                preferred = -preferred
            common_directions = [
                preferred,
                *(
                    old
                    for old in common_directions
                    if abs(float(preferred @ old)) <= 1 - 1e-10
                ),
            ]
    if mains and not common_fixed:
        # Identical normals have an arbitrary two-dimensional null space.
        # Rank the bounded algebraic proposals using independent positions so
        # those arbitrary axes cannot starve the useful positional proposal.
        scores: list[float] = []
        maximum = max(float(np.max(weights[selected.id])) for selected in mains)
        for candidate_direction in common_directions:
            guard.checkpoint()
            candidate_point = common_point(candidate_direction)
            score = total = 0.0
            for selected in mains:
                geometry = _line_start(
                    q[selected.id],
                    weights[selected.id],
                    (candidate_point - frame.origin) / frame.length,
                    candidate_direction,
                    selected.kind == "cone",
                )
                delta = q[selected.id] - geometry.origin
                height = delta @ geometry.axis
                rho = np.linalg.norm(delta - height[:, None] * geometry.axis, axis=1)
                residual = (rho - geometry.radius - geometry.slope * height) / np.hypot(
                    1, geometry.slope
                )
                w = weights[selected.id] / maximum
                score += float(w @ residual**2)
                total += float(w.sum())
            scores.append(score / total)
        remaining = list(range(len(common_directions)))
        order: list[int] = []
        while remaining:
            best = min(scores[index] for index in remaining)
            tied = [
                index
                for index in remaining
                if scores[index]
                <= best + policy.objective_atol + policy.objective_rtol * abs(best)
            ]
            order.extend(tied)
            remaining = [index for index in remaining if index not in tied]
        common_directions = [common_directions[index] for index in order]
        initialization["common_axis_proposals"] = [
            {"source_index": index, "algebraic_objective": scores[index]}
            for index in order
        ]
    mirror_roots = list(dict.fromkeys(pr[m.plane] for m in group.mirrors))
    oriented_phases = {
        pr[relation.reference_plane]
        for relation in group.radius_distances
        if relation.sign is not None and group.planes[pr[relation.reference_plane]].free
    }
    initialization["signed_phase_roots"] = [
        key for key in mirror_roots if key in oriented_phases
    ]
    individual: dict[str, _Geometry] = {}
    for selected in selections:
        guard.checkpoint()
        with suppress(ValueError, np.linalg.LinAlgError):
            individual[selected.id] = _start(
                q[selected.id],
                weights[selected.id],
                normals[selected.id],
                selected.kind,
            )[0]

    def phase_options(point: Array, direction: Array) -> list[dict[str, Array]]:
        per_plane: dict[str, list[Array]] = {}
        c = (point - frame.origin) / frame.length
        for root in mirror_roots:
            definition = group.planes[root]
            fixed_equation = _plane_value(definition)
            if not definition.free:
                assert fixed_equation is not None
                per_plane[root] = [fixed_equation[:3]]
                continue
            vectors: list[Array] = []
            for mirror in group.mirrors:
                if pr[mirror.plane] != root:
                    continue
                left, right = (individual.get(key) for key in mirror.members)
                if left is None or right is None:
                    continue
                if by_id[mirror.members[0]].selection.kind != "plane":
                    a = left.origin + left.axis * ((c - left.origin) @ left.axis)
                    b = right.origin + right.axis * ((c - right.origin) @ right.axis)
                    vectors.append(a - b)
                vectors.extend((left.axis - right.axis, left.axis + right.axis))
            options: list[Array] = []
            for vector in vectors:
                transverse = vector - (vector @ direction) * direction
                length = float(np.linalg.norm(transverse))
                if not np.isfinite(length) or length <= 32 * np.finfo(float).eps * max(
                    float(np.linalg.norm(vector)), 1
                ):
                    continue
                transverse /= length
                if transverse[int(np.argmax(np.abs(transverse)))] < 0:
                    transverse *= -1
                if not any(abs(float(transverse @ old)) > 1 - 1e-10 for old in options):
                    options.append(transverse)
            # Data-derived common-axis basis proposals remain numerical starts;
            # an unobserved clock is still rejected by the joint tangent rank.
            for vector in common_directions:
                transverse = vector - (vector @ direction) * direction
                if np.linalg.norm(transverse) > 1e-10:
                    transverse /= np.linalg.norm(transverse)
                    if not any(
                        abs(float(transverse @ old)) > 1 - 1e-10 for old in options
                    ):
                        options.append(transverse)
            if not options:
                options.extend(cylinder_basis(direction)[:, :2].T)
            per_plane[root] = options[:4]
        variants = [{root: options[0] for root, options in per_plane.items()}]
        for index in range(1, 4):
            variants.append(
                {
                    root: options[min(index, len(options) - 1)]
                    for root, options in per_plane.items()
                }
            )
        for root, options in per_plane.items():
            for option in options[1:]:
                variants.append({**variants[0], root: option})
        signed_variants: list[dict[str, Array]] = []
        for variant in variants:
            signed_variants.append(variant)
            if oriented_phases:
                signed_variants.append(
                    {
                        key: -normal if key in oriented_phases else normal
                        for key, normal in variant.items()
                    }
                )
                for root in variant:
                    if root in oriented_phases and len(oriented_phases) > 1:
                        signed_variants.append({**variant, root: -variant[root]})
        result: list[dict[str, Array]] = []
        for variant in signed_variants:
            if not any(
                all(
                    (
                        float(variant[key] @ old[key])
                        if key in oriented_phases
                        else abs(float(variant[key] @ old[key]))
                    )
                    > 1 - 1e-10
                    for key in variant
                )
                for old in result
            ):
                result.append(variant)
        return result or [{}]

    states: list[tuple[Array, Array, dict[str, Array]]] = []
    rotations_unsigned = bool(group.rotations) and not directed
    for direction in common_directions[: 4 if not group.mirrors else 2]:
        guard.checkpoint()
        point = common_point(direction)
        options = phase_options(point, direction)
        for phases in options[: 2 if rotations_unsigned else 4]:
            states.append((point, direction, phases))
            if rotations_unsigned:
                states.append((point, -direction, phases))
    fresh = [
        FitCandidate(
            f"selected-joint-{index}",
            tuple(
                float(v)
                for v in np.concatenate(
                    [point, direction, *(phases[key] for key in mirror_roots)]
                )
            ),
            "selected moment axes and algebraic orbit/phase evidence",
            _CHART,
            binding.sha256,
        )
        for index, (point, direction, phases) in enumerate(states[:16])
    ]
    dimensions = 6 + 3 * len(mirror_roots)

    def solve_geometry(
        candidate: FitCandidate, control: AttemptControl
    ) -> dict[str, Any]:
        control.checkpoint()
        values = np.array(candidate.parameters)
        point, direction = values[:3], _unit(values[3:6])
        phases = {
            key: _unit(values[6 + 3 * index : 9 + 3 * index])
            for index, key in enumerate(mirror_roots)
        }
        root_records: dict[str, AxisInput] = {}
        for key in set(ar.values()):
            original = axes[key]
            if not original.free:
                root_records[key] = deepcopy(original)
            elif key == common_root:
                root_records[key] = AxisInput(
                    {
                        "point_display": point.tolist(),
                        "axis_display": (
                            axis_sign[group.common_axis] * direction
                        ).tolist(),
                        "axis_is_directed": directed,
                    },
                    True,
                )
        datum_records: dict[str, PlaneInput] = {}
        for key in set(pr.values()):
            original = group.planes[key]
            if not original.free:
                datum_records[key] = deepcopy(original)
                continue
            normal = phases.get(key)
            if normal is None:
                observed = [
                    s.selection.id
                    for s in group.surfaces
                    if s.datum is not None and pr.get(s.datum) == key
                ]
                if observed:
                    p = np.vstack([q[k] for k in observed])
                    w = np.concatenate([weights[k] for k in observed])
                    n = np.vstack([normals[k] for k in observed])
                    geometry = _start(p, w, n, "plane")[0]
                    normal = geometry.axis
                    offset = float(
                        normal @ (frame.origin + frame.length * geometry.origin)
                    )
                else:
                    raise LeastSquaresFailure(
                        "invalid-initial-geometry",
                        "free datum has no selected-data initializer",
                    )
            else:
                offset = float(normal @ point)
            datum_records[key] = replace(
                original, result={"plane_equation": [*normal.tolist(), offset]}
            )
        for key, root in pr.items():
            if key != root:
                equation = (
                    np.asarray(datum_records[root].result["plane_equation"])
                    * plane_sign[key]
                )
                datum_records[key] = replace(
                    group.planes[key], result={"plane_equation": equation.tolist()}
                )
        initial_geometry: dict[str, _Geometry] = {}
        candidate_evidence: dict[str, Any] = {}

        def pool(
            members: tuple[str, ...], transforms: list[tuple[Array, Array]]
        ) -> None:
            pooled = np.vstack(
                [
                    (q[key] - shift) @ rotation
                    for key, (rotation, shift) in zip(members, transforms, strict=True)
                ]
            )
            pooled_normals = np.vstack(
                [
                    normals[key] @ rotation
                    for key, (rotation, _) in zip(members, transforms, strict=True)
                ]
            )
            pooled_weights = np.concatenate([weights[key] for key in members])
            kind = by_id[members[0]].selection.kind
            base, data = _start(pooled, pooled_weights, pooled_normals, kind)
            candidate_evidence[members[0]] = data
            for key, (rotation, shift) in zip(members, transforms, strict=True):
                initial_geometry[key] = _Geometry(
                    rotation @ base.origin + shift,
                    rotation @ base.axis,
                    base.radius,
                    base.slope,
                )

        c = (point - frame.origin) / frame.length
        for relation in group.rotations:
            effective = (
                axis_sign[relation.axis] * axis_sign[group.common_axis] * direction
            )
            rotations = [
                _rotation(effective, index * 2 * np.pi / 3) for index in range(3)
            ]
            pool(
                relation.members,
                [(rotation, c - rotation @ c) for rotation in rotations],
            )
        for relation in group.mirrors:
            equation = np.asarray(
                datum_records[relation.plane].result["plane_equation"]
            )
            normal = equation[:3]
            offset = (equation[3] - normal @ frame.origin) / frame.length
            reflection = np.eye(3) - 2 * np.outer(normal, normal)
            pool(
                relation.members,
                [(np.eye(3), np.zeros(3)), (reflection, 2 * offset * normal)],
            )
        surfaces: list[SurfaceInput] = []
        for selected in group.surfaces:
            control.checkpoint()
            key, kind = selected.selection.id, selected.selection.kind
            if kind == "plane":
                geometry = initial_geometry.get(key)
                if (
                    selected.axis is not None
                    and selected.construction == "perpendicular_to_axis"
                ):
                    axis_root = ar[selected.axis]
                    if axis_root == common_root:
                        normal = direction
                    else:
                        if axis_root not in root_records:
                            provider = next(
                                (
                                    member
                                    for member in selections
                                    if member.kind != "plane"
                                    and ar[surface_axes[member.id]] == axis_root
                                ),
                                None,
                            )
                            if provider is None:
                                raise LeastSquaresFailure(
                                    "invalid-initial-geometry",
                                    "free plane axis has no selected lateral initializer",
                                )
                            member_geometry = initial_geometry.get(provider.id)
                            if member_geometry is None:
                                member_geometry, data = _start(
                                    q[provider.id],
                                    weights[provider.id],
                                    normals[provider.id],
                                    provider.kind,
                                )
                                candidate_evidence[provider.id] = data
                            root_records[axis_root] = AxisInput(
                                {
                                    "point_display": (
                                        frame.origin
                                        + frame.length * member_geometry.origin
                                    ).tolist(),
                                    "axis_display": (
                                        axis_sign[surface_axes[provider.id]]
                                        * member_geometry.axis
                                    ).tolist(),
                                    "axis_is_directed": True,
                                },
                                True,
                            )
                        normal = axis_sign[selected.axis] * _unit(
                            root_records[axis_root].result["axis_display"]
                        )
                    geometry = _Geometry(
                        normalized_weights(weights[key]) @ q[key], normal
                    )
                if geometry is None:
                    geometry, data = _start(q[key], weights[key], normals[key], kind)
                    candidate_evidence[key] = data
                world_origin = frame.origin + frame.length * geometry.origin
                result = {
                    "kind": kind,
                    "plane_equation": [
                        *geometry.axis.tolist(),
                        float(geometry.axis @ world_origin),
                    ],
                }
                surfaces.append(
                    SurfaceInput(
                        key,
                        kind,
                        points[key],
                        weights[key],
                        result,
                        axis=selected.axis,
                        datum=selected.datum,
                        construction=selected.construction,
                    )
                )
                continue
            axis_key = surface_axes[key]
            axis_root = ar[axis_key]
            root_record = root_records.get(axis_root)
            if root_record is not None:
                axis_value = _axis_value(root_record)
                assert axis_value is not None
                anchor, member_axis = axis_value
                geometry = _line_start(
                    q[key],
                    weights[key],
                    (anchor - frame.origin) / frame.length,
                    member_axis,
                    kind == "cone",
                )
            else:
                geometry = initial_geometry.get(key)
                if geometry is None:
                    geometry, data = _start(q[key], weights[key], normals[key], kind)
                    candidate_evidence[key] = data
                root_records[axis_root] = AxisInput(
                    {
                        "point_display": (
                            frame.origin + frame.length * geometry.origin
                        ).tolist(),
                        "axis_display": (axis_sign[axis_key] * geometry.axis).tolist(),
                        "axis_is_directed": True,
                    },
                    True,
                )
            origin = frame.origin + frame.length * geometry.origin
            resolved = resolve_support(
                supports[key],
                origin,
                geometry.axis,
                points[key],
                reference=frame.origin,
            )
            radius = (
                frame.length * geometry.radius + geometry.slope * resolved.origin_shift
            )
            taper = geometry.slope * resolved.axis_sign
            if not np.isfinite([radius, taper]).all() or np.any(
                radius + taper * np.asarray(resolved.domain) <= 0
            ):
                raise LeastSquaresFailure(
                    "invalid-initial-geometry", "nonpositive candidate finite support"
                )
            result = {
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
            surfaces.append(
                SurfaceInput(
                    key, kind, points[key], weights[key], result, axis=axis_key
                )
            )
        for key, root in ar.items():
            if key == root:
                if key not in root_records:
                    original = _axis_value(axes[key])
                    if original is None:
                        raise LeastSquaresFailure(
                            "invalid-initial-geometry",
                            "unused free axis has no data initializer",
                        )
                    root_records[key] = deepcopy(axes[key])
                continue
            value = root_records[root].result
            root_records[key] = replace(
                axes[key],
                result={
                    **deepcopy(value),
                    "axis_display": (
                        axis_sign[key] * np.asarray(value["axis_display"])
                    ).tolist(),
                },
            )
        evidence[candidate.id] = candidate_evidence
        try:
            return fit_relationship_component(
                surfaces,
                root_records,
                [(kind, list(ids)) for kind, ids in group.relationships],
                [list(ids) for ids in group.radii],
                control=control,
                planes=datum_records,
                rotations=group.rotations,
                mirrors=group.mirrors,
                radius_distances=group.radius_distances,
            )
        except RelationshipFailure as error:
            failures[candidate.id] = deepcopy(error.diagnostic)
            raise LeastSquaresFailure(cast(Any, error.code), str(error)) from error

    def solve(candidate: FitCandidate, control: AttemptControl) -> dict[str, Any]:
        try:
            return solve_geometry(candidate, control)
        except LeastSquaresFailure:
            raise
        except SupportIntentFailure as error:
            failures[candidate.id] = deepcopy(error.diagnostic)
            raise LeastSquaresFailure("invalid-initial-geometry", str(error)) from error
        except (ValueError, np.linalg.LinAlgError) as error:
            failures[candidate.id] = {
                "kind": "algebraic_initializer_failure",
                "message": str(error),
            }
            raise LeastSquaresFailure("invalid-initial-geometry", str(error)) from error

    def assess(output: dict[str, Any]) -> FitAssessment:
        squared = total = 0.0
        maximum = float(np.max(workspace.data.weights[selected_ids]))
        for selected in selections:
            residual = (
                surface_distance(points[selected.id], output["surfaces"][selected.id])[
                    0
                ]
                / frame.length
            )
            w = weights[selected.id] / maximum
            squared += float(w @ residual**2)
            total += float(w.sum())
        solver = output["solver"]
        return FitAssessment(
            squared / total,
            bool(solver["condition"] <= 1e6),
            solver["rank"],
            solver["condition"],
            solver["termination"],
            solver["iterations"],
        )

    def same_plane(left: Array, right: Array) -> bool:
        if left[:3] @ right[:3] < 0:
            right = -right
        return bool(
            np.linalg.norm(left[:3] - right[:3]) <= 1e-7
            and abs((left[3] - right[3]) - (left[:3] - right[:3]) @ frame.origin)
            <= 1e-7 * frame.length
        )

    def equivalent(left: dict[str, Any], right: dict[str, Any]) -> bool:
        for selected in selections:
            a, b = (output["surfaces"][selected.id] for output in (left, right))
            if selected.kind == "plane":
                if not same_plane(
                    np.asarray(a["plane_equation"]), np.asarray(b["plane_equation"])
                ):
                    return False
                continue
            ga, gb = a["analytic"], b["analytic"]
            da, db = np.asarray(ga["axis"]), np.asarray(gb["axis"])
            oa, ob = np.asarray(ga["origin"]), np.asarray(gb["origin"])
            ea, eb = (
                np.asarray(geometry["origin"])
                + np.asarray(geometry["axial_domain"])[:, None]
                * np.asarray(geometry["axis"])
                for geometry in (ga, gb)
            )
            ra, rb = (
                geometry["radius"]
                + geometry["slope"] * np.asarray(geometry["axial_domain"])
                for geometry in (ga, gb)
            )
            scale = max(
                float(min(np.min(ra), np.min(rb))), np.finfo(float).eps * frame.length
            )
            extent = max(
                float(np.linalg.norm(points[selected.id] - oa, axis=1).max()), scale
            )
            extent = max(extent, float(np.max(np.linalg.norm(ea - oa, axis=1))))
            reference = normalized_weights(weights[selected.id]) @ points[selected.id]
            radius_a = ga["radius"] + ga["slope"] * float((reference - oa) @ da)
            radius_b = gb["radius"] + gb["slope"] * float((reference - ob) @ db)
            taper_b = gb["slope"] if da @ db >= 0 else -gb["slope"]
            if (
                abs(radius_a - radius_b) > 1e-7 * scale
                or abs(ga["slope"] - taper_b) * extent > 1e-7 * scale
            ):
                return False
            if (
                np.linalg.norm(np.cross(da, db)) * extent > 1e-7 * scale
                or np.linalg.norm(np.cross(ob - oa, da)) > 1e-7 * scale
            ):
                return False
            if da @ db < 0:
                eb, rb = eb[::-1], rb[::-1]
            if (
                np.max(np.linalg.norm(ea - eb, axis=1)) > 1e-7 * scale
                or np.max(np.abs(ra - rb)) > 1e-7 * scale
            ):
                return False
        return all(
            same_plane(
                np.asarray(left["equations"][key]), np.asarray(right["equations"][key])
            )
            for key in group.planes
        )

    hint = None
    common_hint = _axis_value(axes[common_root])
    if not common_fixed and common_hint is not None:
        hp, hd = common_hint
        # A finite but unrepresentable hint cannot supply meaningful row geometry.
        relative = workspace.local[selected_ids] - hp
        if not np.all(relative == relative[0]):
            options = phase_options(hp, axis_sign[group.common_axis] * hd)[0]
            for key in mirror_roots:
                value = _plane_value(group.planes[key])
                if group.planes[key].free and value is not None:
                    options[key] = value[:3]
            hint = FitCandidate(
                "optional-authored-joint",
                tuple(
                    float(v)
                    for v in np.concatenate(
                        [
                            hp,
                            axis_sign[group.common_axis] * hd,
                            *(options[key] for key in mirror_roots),
                        ]
                    )
                ),
                "optional authored free-axis/clock hint",
                _CHART,
                binding.sha256 if hint_binding_sha256 is None else hint_binding_sha256,
            )
    elif fresh and any(
        group.planes[key].free and _plane_value(group.planes[key]) is not None
        for key in mirror_roots
    ):
        parameters = list(fresh[0].parameters)
        for index, key in enumerate(mirror_roots):
            equation = _plane_value(group.planes[key])
            if group.planes[key].free and equation is not None:
                parameters[6 + 3 * index : 9 + 3 * index] = equation[:3].tolist()
        hint = FitCandidate(
            "optional-authored-clock",
            tuple(parameters),
            "optional authored free-clock hint",
            _CHART,
            binding.sha256 if hint_binding_sha256 is None else hint_binding_sha256,
        )
    try:
        recovered = recover_fit(
            RecoveryProblem(binding, {_CHART: dimensions}, solve, assess, equivalent),
            fresh,
            hint=hint,
            cancelled=cancelled,
        )
    except RecoveryFailure as error:
        raise SymmetryFitFailure(
            error.code,
            str(error),
            {
                "recovery": error.diagnostics.json(),
                "attempt_failures": failures,
                "initializers": evidence,
            },
        ) from error
    output = recovered.result
    initialization.update(
        recovery=recovered.diagnostics.json(),
        attempt_failures=failures,
        initializers=evidence,
    )
    axis = output["axes"][group.common_axis]
    for selected in selections:
        value = output["surfaces"][selected.id]
        _, expected, inside = surface_distance(points[selected.id], value)
        orientation = float(
            normalized_weights(weights[selected.id])
            @ np.sum(expected * normals[selected.id], axis=1)
        )
        value.update(
            ids=selected.ids.copy(),
            normal_sign=1.0 if orientation >= 0 else -1.0,
            support_classification={
                "inside_vertices": int(inside.sum()),
                "outside_vertices": int((~inside).sum()),
                "role": "selection-and-display",
            },
            initialization=deepcopy(initialization),
        )
    first_side = next(
        (output["surfaces"][s.id] for s in selections if s.kind != "plane"), None
    )
    first_plane = next(
        (output["surfaces"][s.id] for s in selections if s.kind == "plane"), None
    )
    return {
        "session": {"source_sha256": workspace.source_sha256, "schema_version": 2},
        "fit": {
            "weighted_rms": output["weighted_rms"],
            "cone_weighted_rms": 0.0
            if first_side is None
            else first_side["weighted_rms"],
            "plane_weighted_rms": 0.0
            if first_plane is None
            else first_plane["weighted_rms"],
            "gradient_infinity_norm": output["solver"]["projected_gradient_norm"],
            "objective_history": output["solver"]["objective_history"],
            "normal_matrix_condition": float(output["solver"]["condition"]) ** 2,
            "solver": output["solver"],
            "initialization": deepcopy(initialization),
        },
        "axis_display": axis["axis_display"],
        "point_display": axis["point_display"],
        "axis_is_directed": True,
        "plane_point_display": axis["point_display"],
        "axial_domain": None
        if first_side is None
        else first_side["analytic"]["axial_domain"],
        "lateral_residuals": [] if first_side is None else first_side["residuals"],
        "plane_residuals": [] if first_plane is None else first_plane["residuals"],
        "reference_diameter": 0.0
        if first_side is None
        else 2 * first_side["analytic"]["radius"],
        "signed_half_angle_degrees": 0.0
        if first_side is None
        else float(np.degrees(np.arctan(first_side["analytic"]["slope"]))),
        "selected_area": {s.id: float(weights[s.id].sum()) for s in selections},
        "surfaces": output["surfaces"],
        "axes": output["axes"],
        "planes": output["planes"],
        "equations": output["equations"],
        "mirror_planes": {
            key: output["planes"][key]
            for key in dict.fromkeys(m.plane for m in group.mirrors)
        },
        "reference_planes": output["planes"],
        "initialization": initialization,
    }
