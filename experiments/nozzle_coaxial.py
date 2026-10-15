"""Captured-nozzle adapter for a connected coaxial group and perpendicular planes."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast

import numpy as np

from experiments.fit_coordinates import AxisChart, CoordinateFrame, FitCoordinates
from experiments.fit_initialization import (
    DEFAULT_ATTEMPT_LIMITS,
    FitAssessment,
    FitCandidate,
    RecoveryFailure,
    RecoveryProblem,
    initialization_binding,
    recover_fit,
)
from experiments.fit_relationship_component import (
    AxisInput,
    RelationshipSupportFailure,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import (
    AttemptControl,
    normalized_weights,
    solve_geometric_fit,
)
from experiments.mesh_coaxial_fit import (
    AxisPlaneObservations,
    SideObservations,
    axis_plane_frame,
    fit_coaxial,
)
from experiments.mesh_cone_plane_fit import (
    cone_plane_residual_jacobian,
    valid_cone_geometry,
)
from experiments.mesh_cylinder_fit import CylinderFitFailure, cylinder_basis
from experiments.mesh_mirror_surfaces import MirrorSurfaces, initial_mirror
from experiments.mesh_rotational_planes import RotationalPlanes, initial_rotation
from experiments.nozzle_session import NozzleWorkspace, SurfaceResult
from experiments.scan_workspace import ScanWorkspace, Workspace
from experiments.selection_growth import fit_seed, surface_distance
from experiments.support_intent import (
    SUPPORT_POLICY_VERSION,
    SelectionSupport,
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    resolve_support,
    support_record,
)
from scansor.nonlinear_least_squares import LeastSquaresFailure


@dataclass(frozen=True)
class FitSelection:
    id: str
    ids: list[int]
    kind: str
    domain: tuple[float, float] | None
    support: SupportIntent | None = None


@dataclass(frozen=True)
class ReferencePlaneGroup:
    id: str
    surfaces: tuple[FitSelection, ...]
    construction: str
    angle_degrees: float | None


def _explicit_domain(selection: FitSelection) -> tuple[float, float]:
    if selection.domain is None:
        raise ValueError("legacy numerical charts require explicit support")
    return selection.domain


def _axis_reference_vector(value: Any) -> np.ndarray | None:
    """Decode evaluated geometry without coercing malformed hints into intent."""
    try:
        raw = np.asarray(value)
        if (
            raw.shape != (3,)
            or raw.dtype.kind not in "iuf"
            or (
                isinstance(value, (list, tuple))
                and any(isinstance(item, (bool, np.bool_)) for item in value)
            )
        ):
            return None
        vector = np.asarray(value, dtype=float)
        return vector if np.isfinite(vector).all() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fit_selected_group(
    workspace: ScanWorkspace,
    sides: list[FitSelection],
    planes: list[FitSelection],
    axis_initial: np.ndarray | dict[str, Any] | None,
    *,
    fixed: bool = False,
    reference_plane_groups: tuple[ReferencePlaneGroup, ...] = (),
    semantic_intent: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Generic source adapter; joint identifiability, no fixture workspace seed."""
    guard = AttemptControl(DEFAULT_ATTEMPT_LIMITS, cancelled)
    guard.checkpoint()
    all_selected = [
        *sides,
        *planes,
        *(s for group in reference_plane_groups for s in group.surfaces),
    ]
    if not all_selected or (not sides and not fixed):
        raise CylinderFitFailure(
            "insufficient-observations", "a free shared axis needs lateral observations"
        )
    if any(s.kind not in {"cylinder", "cone"} for s in sides):
        raise ValueError("shared-axis lateral surfaces must be cylinders or cones")
    has_cones = any(s.kind == "cone" for s in sides)
    supports: dict[str, SupportIntent] = {}
    for side in sides:
        if (
            side.support is not None
            and side.domain is not None
            and side.support != legacy_support(side.domain)
        ):
            raise SupportIntentFailure(
                "explicit domain and support must describe the same source-Z0 intent"
            )
        supports[side.id] = (
            side.support if side.support is not None else legacy_support(side.domain)
        )
    policy_version = (
        "selected-revolution-group-recovery-v1"
        if has_cones
        else "selected-cylinder-group-recovery-v1"
    )
    for selected in all_selected:
        minimum = 3 if selected.kind == "plane" else 6
        if len(selected.ids) < minimum:
            raise CylinderFitFailure(
                "insufficient-observations",
                f"{selected.id}: select at least {minimum} vertices",
            )
    if workspace.descriptor is None or workspace.source_sha256 is None:
        raise CylinderFitFailure(
            "invalid-input", "a fit requires an accepted scan source"
        )
    ids = list(dict.fromkeys(i for selected in all_selected for i in selected.ids))
    observations = workspace.local[ids]
    weights = workspace.data.weights[ids]
    frame = CoordinateFrame.from_observations(observations, weights)
    axis_record = axis_initial if isinstance(axis_initial, dict) else None
    if fixed and axis_record is None:
        raise CylinderFitFailure(
            "invalid-reference", "fixed axis requires evaluated geometry"
        )
    reversed_role = False
    if axis_record is not None and "direction_reversed" in axis_record:
        if type(axis_record["direction_reversed"]) is not bool:
            raise CylinderFitFailure(
                "invalid-reference", "axis direction_reversed must be a boolean role"
            )
        reversed_role = axis_record["direction_reversed"]
    reference_direction = None
    if axis_record is not None:
        reference_direction = _axis_reference_vector(axis_record.get("axis_display"))
        if reference_direction is not None:
            with np.errstate(over="ignore", under="ignore", invalid="ignore"):
                norm = float(np.linalg.norm(reference_direction))
            reference_direction = (
                reference_direction / norm if np.isfinite(norm) and norm > 0 else None
            )
        if fixed and reference_direction is None:
            raise CylinderFitFailure(
                "invalid-reference", "axis direction must be finite and nonzero"
            )
        if fixed and _axis_reference_vector(axis_record.get("point_display")) is None:
            raise CylinderFitFailure(
                "invalid-reference",
                "fixed axis point must contain three finite coordinates",
            )
    intent: dict[str, Any] = {
        "family": "shared-revolution-group" if has_cones else "shared-cylinder-group",
        "fixed": fixed,
        "surfaces": [
            {"id": s.id, "ids": s.ids, "kind": s.kind, "domain": s.domain}
            for s in all_selected
        ],
        "reference_planes": [
            {
                "id": g.id,
                "surfaces": [s.id for s in g.surfaces],
                "construction": g.construction,
            }
            for g in reference_plane_groups
        ],
        "axis": (
            {
                "point": axis_record["point_display"],
                "direction": axis_record["axis_display"],
            }
            if axis_record is not None
            else None
        )
        if fixed and semantic_intent is None
        else {
            "direction": None
            if reference_direction is None or semantic_intent is not None
            else reference_direction.tolist()
        },
        "semantic_intent": semantic_intent,
        "support_policy": SUPPORT_POLICY_VERSION,
    }
    if semantic_intent is None or "recipe" not in semantic_intent:
        intent["support"] = {
            side.id: support_record(supports[side.id]) for side in sides
        }
    if semantic_intent is None and reference_direction is None and reversed_role:
        # Numerical garbage supplies no direction, but an explicit role survives.
        intent["axis"]["direction_reversed"] = True
    binding = initialization_binding(
        workspace.source_sha256,
        workspace.descriptor.interpretation_sha256,
        ids,
        intent,
        policy_version=policy_version,
    )
    guard.checkpoint()
    directions: list[np.ndarray] = []
    if fixed:
        assert reference_direction is not None
        directions.append(reference_direction)
    else:
        lateral_ids = list(dict.fromkeys(i for side in sides for i in side.ids))
        normals = workspace.data.normals[lateral_ids] @ workspace.frame
        lw = normalized_weights(workspace.data.weights[lateral_ids])
        centered = (workspace.local[lateral_ids] - frame.origin) / frame.length
        centered -= lw @ centered
        matrices = [
            normals.T @ (lw[:, None] * normals),
            centered.T @ (lw[:, None] * centered),
        ]
        if has_cones:
            # Each side has its own constant normal-axis dot product: mixing
            # different tapers before centering would erase their common null.
            scatter = np.zeros((3, 3))
            for side in sides:
                guard.checkpoint()
                side_normals = workspace.data.normals[side.ids] @ workspace.frame
                side_normals /= np.linalg.norm(side_normals, axis=1)[:, None]
                sw = workspace.data.weights[side.ids] / np.max(weights)
                side_normals -= normalized_weights(sw) @ side_normals
                scatter += side_normals.T @ (sw[:, None] * side_normals)
            _, vectors = np.linalg.eigh(scatter)
            direction = vectors[:, 0].copy()
            if direction[int(np.argmax(np.abs(direction)))] < 0:
                direction = -direction
            directions.append(direction)
        for matrix in matrices:
            guard.checkpoint()
            _, vectors = np.linalg.eigh(matrix)
            for index in range(3):
                direction = vectors[:, index].copy()
                if direction[int(np.argmax(np.abs(direction)))] < 0:
                    direction = -direction
                if not any(
                    abs(float(direction @ old)) > 1 - 1e-10 for old in directions
                ):
                    directions.append(direction)
    fresh = [
        FitCandidate(
            f"selected-axis-{index}",
            tuple(direction.tolist()),
            "selected normal/position moment axis",
            "world-unit-axis-v1",
            binding.sha256,
        )
        for index, direction in enumerate(directions)
    ]

    def solve(candidate: FitCandidate, control: AttemptControl) -> dict[str, Any]:
        control.checkpoint()
        direction = np.array(candidate.parameters)
        direction /= np.linalg.norm(direction)
        if reference_direction is not None and direction @ reference_direction < 0:
            direction = -direction
        anchor = frame.origin.copy()
        if fixed:
            assert axis_record is not None
            anchor = np.asarray(axis_record["point_display"], dtype=float)
        else:
            # All radii share one projected center; group indicators allow
            # distinct radii without requiring each patch to identify an axis.
            rotation = cylinder_basis(direction)
            points = np.concatenate([workspace.local[s.ids] for s in sides])
            side_weights = normalized_weights(
                np.concatenate([workspace.data.weights[s.ids] for s in sides])
            )
            normalized = (points - frame.origin) / frame.length
            xy = normalized @ rotation[:, :2]
            group_ids = np.repeat(np.arange(len(sides)), [len(s.ids) for s in sides])
            indicators = np.eye(len(sides))[group_ids]
            if has_cones:
                local = normalized @ rotation
                # Cone squared radii vary quadratically along the proposed
                # axis. Remove each side's nuisance span before solving the
                # two shared center columns; deficient starts remain proposals
                # for the complete component's constrained rank assessment.
                nuisance = [indicators]
                for index, side in enumerate(sides):
                    if side.kind == "cone":
                        mask = indicators[:, index]
                        nuisance.extend(
                            [
                                (mask * local[:, 2])[:, None],
                                (mask * local[:, 2] ** 2)[:, None],
                            ]
                        )
                root_weights = np.sqrt(side_weights)
                design = root_weights[:, None] * (2 * xy)
                target = root_weights * np.sum(xy**2, axis=1)
                nuisance_design = root_weights[:, None] * np.column_stack(nuisance)
                design -= (
                    nuisance_design
                    @ np.linalg.lstsq(nuisance_design, design, rcond=None)[0]
                )
                target -= (
                    nuisance_design
                    @ np.linalg.lstsq(nuisance_design, target, rcond=None)[0]
                )
                center = np.linalg.lstsq(design, target, rcond=None)[0]
            else:
                design = np.column_stack((2 * xy, indicators))
                algebraic, *_ = np.linalg.lstsq(
                    np.sqrt(side_weights)[:, None] * design,
                    np.sqrt(side_weights) * np.sum(xy**2, axis=1),
                    rcond=None,
                )
                center = algebraic[:2]
            anchor += frame.length * (rotation[:, :2] @ center)
        axis = {
            "axis_display": direction.tolist(),
            "point_display": anchor.tolist(),
            "axis_is_directed": True,
        }
        surfaces: list[SurfaceInput] = []
        for side in sides:
            control.checkpoint()
            p = workspace.local[side.ids]
            sw = normalized_weights(workspace.data.weights[side.ids])
            height = (p - anchor) @ direction
            radial = (p - anchor) - height[:, None] * direction
            rho = np.linalg.norm(radial, axis=1)
            radius = float(sw @ rho)
            taper = 0.0
            if side.kind == "cone":
                regression = np.column_stack((np.ones(len(p)), height / frame.length))
                coefficients = np.linalg.lstsq(
                    np.sqrt(sw)[:, None] * regression,
                    np.sqrt(sw) * rho / frame.length,
                    rcond=None,
                )[0]
                radius, taper = (
                    float(frame.length * coefficients[0]),
                    float(coefficients[1]),
                )
            geometry: dict[str, Any] = {
                "kind": side.kind,
                "origin": anchor.tolist(),
                "axis": direction.tolist(),
                "radius": radius,
                "slope": taper,
                "axial_domain": [float(min(height)), float(max(height))],
            }
            if not isinstance(supports[side.id], SelectionSupport):
                # This initial analytic record must describe the authored
                # segment, even when selected observations extend outside it.
                try:
                    resolved = resolve_support(
                        supports[side.id], anchor, direction, p, reference=frame.origin
                    )
                except SupportIntentFailure as error:
                    raise LeastSquaresFailure(
                        "invalid-initial-geometry", str(error)
                    ) from error
                radius += taper * resolved.origin_shift
                taper *= resolved.axis_sign
                geometry.update(
                    origin=list(resolved.origin),
                    axis=list(resolved.axis),
                    radius=radius,
                    slope=taper,
                    axial_domain=list(resolved.domain),
                )
            if geometry["axial_domain"][0] == geometry["axial_domain"][1]:
                # A fixed/shared axis can identify a radius from one circle;
                # its finite support must still have nonzero observed extent.
                raise LeastSquaresFailure(
                    "invalid-initial-geometry", "proposal has no selected axial extent"
                )
            if not np.isfinite([radius, taper]).all() or np.any(
                radius + taper * np.asarray(geometry["axial_domain"]) <= 0
            ):
                raise LeastSquaresFailure(
                    "invalid-initial-geometry", "proposal has nonpositive cone support"
                )
            result: dict[str, Any] = {
                "kind": side.kind,
                "analytic": geometry,
                "anchor_reference": frame.origin.tolist(),
                "support_intent": support_record(supports[side.id]),
                "support_origin": "selection"
                if isinstance(supports[side.id], SelectionSupport)
                else "legacy-source-z0"
                if side.domain is not None
                else "reference-plane",
            }
            if side.domain is not None:
                result["source_axial_domain"] = list(side.domain)
            surfaces.append(
                SurfaceInput(
                    side.id,
                    side.kind,
                    p,
                    workspace.data.weights[side.ids],
                    result,
                    axis="axis",
                )
            )
        plane_metadata: dict[str, ReferencePlaneGroup | None] = {}
        for selected, group in [
            *((p, None) for p in planes),
            *(
                (p, reference)
                for reference in reference_plane_groups
                for p in reference.surfaces
            ),
        ]:
            control.checkpoint()
            points = workspace.local[selected.ids]
            result = fit_seed(
                points,
                workspace.data.weights[selected.ids],
                workspace.data.normals[selected.ids] @ workspace.frame,
                "plane",
                None,
                None,
            )
            plane_metadata[selected.id] = group
            surfaces.append(
                SurfaceInput(
                    selected.id,
                    "plane",
                    points,
                    workspace.data.weights[selected.ids],
                    result,
                    axis="axis",
                    datum=None if group is None else group.id,
                    construction="perpendicular_to_axis"
                    if group is None
                    else group.construction,
                )
            )
        try:
            output = fit_relationship_component(
                surfaces, {"axis": AxisInput(axis, not fixed)}, [], [], control=control
            )
        except RelationshipSupportFailure as error:
            raise LeastSquaresFailure("invalid-initial-geometry", str(error)) from error
        output["plane_metadata"] = plane_metadata
        return output

    def assess(result: dict[str, Any]) -> FitAssessment:
        squared = 0.0
        total = 0.0
        for selected in all_selected:
            residual, _, _ = surface_distance(
                workspace.local[selected.ids], result["surfaces"][selected.id]
            )
            residual = residual / frame.length
            weight = workspace.data.weights[selected.ids] / float(np.max(weights))
            squared += float(weight @ residual**2)
            total += float(weight.sum())
        solver = result["solver"]
        return FitAssessment(
            squared / total,
            bool(solver.get("condition", 1) <= 1e6),
            solver.get("rank"),
            solver.get("condition"),
            solver["termination"],
            solver["iterations"],
        )

    def equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
        da, db = (
            np.asarray(result["axes"]["axis"]["axis_display"]) for result in (a, b)
        )
        aa, ab = (
            np.asarray(result["axes"]["axis"]["point_display"]) for result in (a, b)
        )
        aa = (aa - frame.origin) / frame.length
        ab = (ab - frame.origin) / frame.length
        aa -= (aa @ da) * da
        ab -= (ab @ db) * db
        same_axis = bool(
            abs(float(da @ db)) > 1 - 1e-10
            and np.allclose(aa, ab, atol=1e-7, rtol=1e-7)
        )
        if not same_axis:
            return False
        for selected in all_selected:
            left, right = (result["surfaces"][selected.id] for result in (a, b))
            if selected.kind == "cone":
                left_geometry, right_geometry = left["analytic"], right["analytic"]
                sign = float(np.dot(left_geometry["axis"], right_geometry["axis"]))
                right_radius = right_geometry["radius"] + right_geometry[
                    "slope"
                ] * float(
                    (np.asarray(left_geometry["origin"]) - right_geometry["origin"])
                    @ right_geometry["axis"]
                )
                if not np.isclose(
                    left_geometry["radius"] / frame.length,
                    right_radius / frame.length,
                    atol=1e-7,
                    rtol=1e-7,
                ) or not np.isclose(
                    left_geometry["slope"],
                    sign * right_geometry["slope"],
                    atol=1e-7,
                    rtol=1e-7,
                ):
                    return False
            elif selected.kind == "cylinder":
                if not np.isclose(
                    left["analytic"]["radius"] / frame.length,
                    right["analytic"]["radius"] / frame.length,
                    atol=1e-7,
                    rtol=1e-7,
                ):
                    return False
            else:
                le, re = (
                    np.array(surface["plane_equation"], dtype=float)
                    for surface in (left, right)
                )
                le[3] = (le[3] - le[:3] @ frame.origin) / frame.length
                re[3] = (re[3] - re[:3] @ frame.origin) / frame.length
                if le[:3] @ re[:3] < 0:
                    re = -re
                if not np.allclose(le, re, atol=1e-7, rtol=1e-7):
                    return False
        return True

    hint = None
    if axis_record is not None and not fixed:
        hint = FitCandidate(
            "optional-free-axis",
            () if reference_direction is None else tuple(reference_direction.tolist()),
            "optional authored free-axis direction",
            "world-unit-axis-v1",
            binding.sha256,
        )
    try:
        recovered = recover_fit(
            RecoveryProblem(
                binding, {"world-unit-axis-v1": 3}, solve, assess, equivalent
            ),
            fresh,
            hint=hint,
            cancelled=cancelled,
        )
    except RecoveryFailure as error:
        raise CylinderFitFailure(
            error.code, str(error), {"recovery": error.diagnostics.json()}
        ) from error
    guard.checkpoint()
    output = recovered.result
    axis = output["axes"]["axis"]
    direction = np.asarray(axis["axis_display"])
    if (
        reference_direction is not None
        and abs(float(direction @ reference_direction)) <= 1e-10
    ) or (reference_direction is None and reversed_role):
        # An orthogonal free-axis start supplies no sign preference. Retain a
        # deterministic sign and apply the authored reversal bit once.
        if direction[int(np.argmax(np.abs(direction)))] < 0:
            direction = -direction
        if reversed_role:
            direction = -direction
    anchor = np.asarray(axis["point_display"])
    surfaces = output["surfaces"]
    for selected in all_selected:
        guard.checkpoint()
        _, expected, inside = surface_distance(
            workspace.local[selected.ids], surfaces[selected.id]
        )
        normalized = normalized_weights(workspace.data.weights[selected.ids])
        normals = workspace.data.normals[selected.ids] @ workspace.frame
        surfaces[selected.id]["normal_sign"] = (
            1.0 if float(normalized @ np.sum(expected * normals, axis=1)) >= 0 else -1.0
        )
        surfaces[selected.id]["support_classification"] = {
            "inside_vertices": int(inside.sum()),
            "outside_vertices": int((~inside).sum()),
            "role": "selection-and-display",
        }
        surfaces[selected.id]["ids"] = selected.ids.copy()
        surfaces[selected.id]["initialization"] = {
            "algorithm": policy_version,
            "support_policy": SUPPORT_POLICY_VERSION,
            "observations": sum(len(s.ids) for s in all_selected),
            "recovery": recovered.diagnostics.json(),
        }
    reference_planes: dict[str, dict[str, Any]] = {}
    from experiments.surface_primitives import basis

    first, second = basis(direction)
    for group in reference_plane_groups:
        guard.checkpoint()
        equation = np.asarray(output["equations"][group.id])
        normal = equation[:3]
        point = anchor + (equation[3] - normal @ anchor) * normal
        radial = normal - (normal @ direction) * direction
        if np.linalg.norm(radial) > 1e-12:
            radial /= np.linalg.norm(radial)
        else:
            radial = first
        angle = (
            None
            if group.construction == "perpendicular_to_axis"
            else float(np.degrees(np.arctan2(normal @ second, normal @ first)))
        )
        reference_planes[group.id] = {
            "axis_display": direction.tolist(),
            "basis_u_display": first.tolist(),
            "basis_v_display": second.tolist(),
            "point_display": point.tolist(),
            "radial_display": radial.tolist(),
            "normal_display": normal.tolist(),
            "plane_equation": equation.tolist(),
            "angle_degrees": angle,
            "offset": float(equation[3] - normal @ anchor),
            "construction": group.construction,
        }
    first_side = surfaces[sides[0].id] if sides else None
    first_plane = surfaces[planes[0].id] if planes else None
    condition = assess(output).condition
    published = {
        "session": {"source_sha256": workspace.source_sha256, "schema_version": 2},
        "fit": {
            "weighted_rms": float(output["weighted_rms"]),
            "cone_weighted_rms": 0.0
            if first_side is None
            else first_side["weighted_rms"],
            "plane_weighted_rms": 0.0
            if first_plane is None
            else first_plane["weighted_rms"],
            "gradient_infinity_norm": output["solver"]["projected_gradient_norm"],
            "objective_history": output["solver"]["objective_history"],
            "normal_matrix_condition": float(condition or 1) ** 2,
            "solver": output["solver"],
            "initialization": {
                "algorithm": policy_version,
                "support_policy": SUPPORT_POLICY_VERSION,
                "recovery": recovered.diagnostics.json(),
            },
        },
        "axis_display": direction.tolist(),
        "point_display": anchor.tolist(),
        "axis_is_directed": True,
        "plane_point_display": anchor.tolist(),
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
        "selected_area": {
            s.id: float(workspace.data.weights[s.ids].sum()) for s in all_selected
        },
        "surfaces": surfaces,
        "reference_planes": reference_planes,
    }
    guard.checkpoint()
    return published


def fit_group(
    workspace: Workspace,
    sides: list[FitSelection],
    planes: list[FitSelection],
    rotational_groups: tuple[tuple[FitSelection, ...], ...] = (),
    axis_initial: np.ndarray | dict[str, Any] | None = None,
    mirror_groups: tuple[tuple[FitSelection, FitSelection], ...] = (),
    mirror_phases_radians: tuple[float, ...] | None = None,
    mirror_radius_surface_ids: tuple[str | None, ...] | None = None,
    reference_plane_groups: tuple[ReferencePlaneGroup, ...] = (),
    *,
    semantic_intent: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Joint fit; a mirror's optional radius source makes its planes tangent.

    Imported groups support cooperative numerical cancellation.
    """
    if isinstance(workspace, ScanWorkspace):
        if rotational_groups or mirror_groups:
            raise ValueError(
                "Imported rotational/mirror groups await generic workflow integration"
            )
        return _fit_selected_group(
            workspace,
            sides,
            planes,
            axis_initial,
            reference_plane_groups=reference_plane_groups,
            semantic_intent=semantic_intent,
            cancelled=cancelled,
        )
    if cancelled is not None:
        raise ValueError("legacy example groups do not support cancellation")
    workspace = cast(NozzleWorkspace, workspace)
    sides = [
        replace(s, domain=workspace.support) if s.domain is None else s for s in sides
    ]
    planes = [
        replace(s, domain=workspace.support) if s.domain is None else s for s in planes
    ]
    rotational_groups = tuple(
        tuple(
            replace(s, domain=workspace.support) if s.domain is None else s
            for s in group
        )
        for group in rotational_groups
    )
    mirror_groups = tuple(
        (
            replace(left, domain=workspace.support) if left.domain is None else left,
            replace(right, domain=workspace.support) if right.domain is None else right,
        )
        for left, right in mirror_groups
    )
    reference_plane_groups = tuple(
        replace(
            group,
            surfaces=tuple(
                replace(s, domain=workspace.support) if s.domain is None else s
                for s in group.surfaces
            ),
        )
        for group in reference_plane_groups
    )
    if isinstance(axis_initial, dict):
        raise ValueError("legacy example groups require their explicit numerical chart")
    observations = [
        SideObservations(
            workspace.local[s.ids],
            workspace.data.weights[s.ids],
            s.kind,
            cast(tuple[float, float], s.domain),
        )
        for s in sides
    ]
    for s in sides:
        if len(s.ids) < 7:
            raise ValueError(f"{s.id}: select at least seven lateral vertices")
    for selected_plane in planes:
        if len(selected_plane.ids) < 3:
            raise ValueError(
                f"{selected_plane.id}: select at least three plane vertices"
            )
    plane = planes[0] if planes else None
    pp = workspace.local[plane.ids] if plane is not None else None
    pw = workspace.data.weights[plane.ids] if plane is not None else None
    seed = np.array(workspace.data.selection["initial_parameters"], dtype=float)
    if axis_initial is not None:
        if axis_initial.shape != (7,) or not np.isfinite(axis_initial).all():
            raise ValueError("axis initialization must contain seven finite parameters")
        seed[:4] = axis_initial[:4]
    axis = np.array([seed[2], seed[3], 1.0])
    axis /= np.linalg.norm(axis)
    initial = [*seed[:4]]
    if pp is not None and pw is not None:
        initial.append(float(pw @ (pp @ axis) / pw.sum()))
    for side in observations:
        q = side.points - np.array([seed[0], seed[1], 0.0])
        z = q @ axis
        rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
        initial.append(float(side.area @ rho / side.area.sum()))
        if side.kind == "cone":
            initial.append(0.0)
    extra_planes = tuple(
        (workspace.local[s.ids], workspace.data.weights[s.ids]) for s in planes[1:]
    )
    initial.extend(float(w @ (points @ axis) / w.sum()) for points, w in extra_planes)
    basis_index = int(np.argmin(np.abs(axis)))
    axis_plane_observations = tuple(
        AxisPlaneObservations(
            np.concatenate([workspace.local[s.ids] for s in group.surfaces]),
            np.concatenate([workspace.data.weights[s.ids] for s in group.surfaces]),
            group.construction,
            (
                None
                if group.angle_degrees is None
                else float(np.radians(group.angle_degrees))
            ),
            basis_index,
        )
        for group in reference_plane_groups
    )
    for group, observations_for_plane in zip(
        reference_plane_groups, axis_plane_observations, strict=True
    ):
        if not group.surfaces:
            raise ValueError("a reference plane requires at least one fitted surface")
        if any(surface.kind != "plane" for surface in group.surfaces):
            raise ValueError("reference-plane factors must be plane fits")
        if any(len(surface.ids) < 3 for surface in group.surfaces):
            raise ValueError("each reference-plane fit needs at least three vertices")
        if observations_for_plane.construction != "perpendicular_to_axis":
            assert observations_for_plane.angle_radians is not None
            initial.append(observations_for_plane.angle_radians)
        if observations_for_plane.construction != "contains_axis":
            normal = axis_plane_frame(observations_for_plane, seed)[2]
            initial.append(
                float(
                    observations_for_plane.area
                    @ (observations_for_plane.points @ normal)
                    / observations_for_plane.area.sum()
                )
            )
    rotations = tuple(
        RotationalPlanes(
            tuple(workspace.local[s.ids] for s in group),
            tuple(workspace.data.weights[s.ids] for s in group),
            kind=group[0].kind,
            seed=tuple(
                fit_seed(
                    workspace.local[group[0].ids],
                    workspace.data.weights[group[0].ids],
                    workspace.data.normals[group[0].ids] @ workspace.frame,
                    group[0].kind,
                    seed,
                    group[0].domain,
                )["parameters"]
            )
            if group[0].kind != "plane"
            else (),
            domain=_explicit_domain(group[0]),
        )
        for group in rotational_groups
    )
    for group in rotations:
        if any(len(points) < 3 for points in group.points):
            raise ValueError("each rotational surface needs at least three vertices")
        initial.extend(initial_rotation(group, np.array(initial)).tolist())
    if mirror_radius_surface_ids is not None and len(mirror_radius_surface_ids) != len(
        mirror_groups
    ):
        raise ValueError(
            "provide exactly one optional radius source for each mirror group"
        )
    side_indices = {side.id: index for index, side in enumerate(sides)}
    mirrors: list[MirrorSurfaces] = []
    for group_index, group in enumerate(mirror_groups):
        if len(group) != 2 or group[0].id == group[1].id:
            raise ValueError("mirror symmetry requires two distinct surface fits")
        if group[0].kind != group[1].kind:
            raise ValueError("mirror symmetry requires two same-type surface fits")
        if any(
            len(surface.ids) < (3 if surface.kind == "plane" else 7)
            for surface in group
        ):
            raise ValueError(
                "each mirror surface needs enough observations for its fit type"
            )
        seed_result = fit_seed(
            workspace.local[group[0].ids],
            workspace.data.weights[group[0].ids],
            workspace.data.normals[group[0].ids] @ workspace.frame,
            group[0].kind,
            seed,
            group[0].domain,
        )
        radius_source = (
            None
            if mirror_radius_surface_ids is None
            else mirror_radius_surface_ids[group_index]
        )
        radius_side_index = None
        if radius_source is not None:
            radius_side_index = side_indices.get(radius_source)
            if radius_side_index is None or sides[radius_side_index].kind != "cylinder":
                raise ValueError(
                    "a mirror radius source must name a cylinder in this joint"
                )
            if group[0].kind != "plane":
                raise ValueError(
                    "a mirror radius source can constrain only a pair of planes"
                )
        mirrors.append(
            MirrorSurfaces(
                (workspace.local[group[0].ids], workspace.local[group[1].ids]),
                (
                    workspace.data.weights[group[0].ids],
                    workspace.data.weights[group[1].ids],
                ),
                group[0].kind,
                tuple(seed_result["parameters"]),
                _explicit_domain(group[0]),
                group[1].domain,
                radius_side_index,
            )
        )
    if mirror_phases_radians is not None and len(mirror_phases_radians) != len(mirrors):
        raise ValueError("provide exactly one initial phase for each mirror group")
    for index, group in enumerate(mirrors):
        phase = None if mirror_phases_radians is None else mirror_phases_radians[index]
        initial.extend(initial_mirror(group, np.array(initial), phase).tolist())
    fitted = fit_coaxial(
        observations,
        pp,
        pw,
        np.array(initial),
        extra_planes,
        rotations,
        tuple(mirrors),
        axis_plane_observations,
    )
    p = fitted.parameters[0]
    axis = np.array([p[2], p[3], 1.0])
    axis /= np.linalg.norm(axis)
    point = np.array([p[0], p[1], 0.0])
    plane_point = point + (p[5] - axis @ point) * axis if plane is not None else point
    surfaces: dict[str, SurfaceResult] = {}
    for side, obs, parameters, residual in zip(
        sides, observations, fitted.parameters, fitted.residuals, strict=True
    ):
        surfaces[side.id] = {
            "kind": side.kind,
            "ids": side.ids,
            "parameters": parameters.tolist(),
            "axial_domain": _explicit_domain(side),
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(obs.area @ residual**2 / obs.area.sum())),
        }
    plane_rms = (
        float(np.sqrt(pw @ fitted.plane_residuals**2 / pw.sum()))
        if pw is not None
        else 0.0
    )
    for selected_plane, offset, residual in zip(
        planes,
        fitted.plane_offsets,
        [
            *((fitted.plane_residuals,) if plane is not None else ()),
            *fitted.extra_plane_residuals,
        ],
        strict=True,
    ):
        parameters = p.copy()
        parameters[5] = offset
        weights = workspace.data.weights[selected_plane.ids]
        surfaces[selected_plane.id] = {
            "kind": "plane",
            "ids": selected_plane.ids,
            "parameters": parameters.tolist(),
            "axial_domain": _explicit_domain(selected_plane),
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(weights @ residual**2 / weights.sum())),
        }
    reference_planes: dict[str, dict[str, object]] = {}
    for group, equation, basis_u, basis_v, angle, residual in zip(
        reference_plane_groups,
        fitted.axis_plane_equations,
        fitted.axis_plane_basis_u,
        fitted.axis_plane_basis_v,
        fitted.axis_plane_angles,
        fitted.axis_plane_residuals,
        strict=True,
    ):
        start = 0
        for selected_plane in group.surfaces:
            stop = start + len(selected_plane.ids)
            selected_residual = residual[start:stop]
            weights = workspace.data.weights[selected_plane.ids]
            parameters = p.copy()
            parameters[5] = equation[3]
            surfaces[selected_plane.id] = {
                "kind": "plane",
                "ids": selected_plane.ids,
                "parameters": parameters.tolist(),
                "plane_equation": equation.tolist(),
                "axial_domain": _explicit_domain(selected_plane),
                "residuals": selected_residual.tolist(),
                "weighted_rms": float(
                    np.sqrt(weights @ selected_residual**2 / weights.sum())
                ),
            }
            start = stop
        normal = equation[:3]
        resolved_point = point + (equation[3] - normal @ point) * normal
        reference_planes[group.id] = {
            "axis_display": axis.tolist(),
            "basis_u_display": basis_u.tolist(),
            "basis_v_display": basis_v.tolist(),
            "point_display": resolved_point.tolist(),
            "radial_display": basis_v.tolist(),
            "normal_display": normal.tolist(),
            "plane_equation": equation.tolist(),
            "angle_degrees": None if angle is None else float(np.degrees(angle)),
            "offset": float(equation[3] - normal @ point),
            "construction": group.construction,
        }
    for group, equations, residuals, domains in zip(
        rotational_groups,
        fitted.rotational_equations,
        fitted.rotational_residuals,
        fitted.rotational_domains,
        strict=True,
    ):
        for surface, equation, residual, domain in zip(
            group, equations, residuals, domains, strict=True
        ):
            weights = workspace.data.weights[surface.ids]
            surfaces[surface.id] = {
                "kind": surface.kind,
                "ids": surface.ids,
                "parameters": equation.tolist(),
                "axial_domain": domain,
                "residuals": residual.tolist(),
                "weighted_rms": float(np.sqrt(weights @ residual**2 / weights.sum())),
            }
            if surface.kind == "plane":
                surfaces[surface.id]["plane_equation"] = equation.tolist()
    for group, equations, residuals, domains in zip(
        mirror_groups,
        fitted.mirror_equations,
        fitted.mirror_residuals,
        fitted.mirror_domains,
        strict=True,
    ):
        for surface, equation, residual, domain in zip(
            group, equations, residuals, domains, strict=True
        ):
            weights = workspace.data.weights[surface.ids]
            surfaces[surface.id] = {
                "kind": surface.kind,
                "ids": surface.ids,
                "parameters": equation.tolist(),
                "axial_domain": domain,
                "residuals": residual.tolist(),
                "weighted_rms": float(np.sqrt(weights @ residual**2 / weights.sum())),
            }
            if surface.kind == "plane":
                surfaces[surface.id]["plane_equation"] = equation.tolist()
    return {
        "session": {
            "lateral_ids": sides[0].ids,
            "plane_ids": plane.ids if plane is not None else [],
            "source_sha256": workspace.default.source_sha256,
            "model_sha256": workspace.model_sha256,
            "schema_version": 1,
        },
        "fit": {
            "parameters": p.tolist(),
            "objective_history": fitted.objective_history,
            "weighted_rms": fitted.weighted_rms,
            "cone_weighted_rms": surfaces[sides[0].id]["weighted_rms"],
            "plane_weighted_rms": plane_rms,
            "normal_matrix_condition": fitted.condition,
            "gradient_infinity_norm": fitted.gradient,
            "solver": fitted.solver,
        },
        "axis_display": axis.tolist(),
        "point_display": point.tolist(),
        "plane_point_display": plane_point.tolist(),
        "axial_domain": sides[0].domain,
        "lateral_residuals": fitted.residuals[0].tolist(),
        "plane_residuals": fitted.plane_residuals.tolist(),
        "reference_diameter": float(2 * p[4]),
        "signed_half_angle_degrees": float(np.degrees(np.arctan(p[6]))),
        "selected_area": {
            **{
                s.id: float(o.area.sum())
                for s, o in zip(sides, observations, strict=True)
            },
            **{
                s.id: float(workspace.data.weights[s.ids].sum())
                for s in [
                    *planes,
                    *(
                        surface
                        for group in reference_plane_groups
                        for surface in group.surfaces
                    ),
                    *(s for group in rotational_groups for s in group),
                    *(s for group in mirror_groups for s in group),
                ]
            },
        },
        "surfaces": surfaces,
        "reference_planes": reference_planes,
        "mirror_planes": [
            {
                "phase_radians": phase,
                "equation": equation.tolist(),
                "direction": direction.tolist(),
            }
            for phase, equation, direction in zip(
                fitted.mirror_phases,
                fitted.mirror_plane_equations,
                fitted.mirror_plane_directions,
                strict=True,
            )
        ],
    }


def fit_fixed_axis_group(
    workspace: Workspace,
    sides: list[FitSelection],
    planes: list[FitSelection],
    axis_parameters: np.ndarray | dict[str, Any],
    *,
    semantic_intent: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fit radii, taper, and plane offsets without moving the supplied axis."""
    if isinstance(workspace, ScanWorkspace):
        if not isinstance(axis_parameters, dict):
            raise CylinderFitFailure(
                "invalid-reference",
                "imported fixed-axis fitting requires evaluated axis geometry",
            )
        return _fit_selected_group(
            workspace,
            sides,
            planes,
            axis_parameters,
            fixed=True,
            semantic_intent=semantic_intent,
            cancelled=cancelled,
        )
    if cancelled is not None:
        raise ValueError("legacy example groups do not support cancellation")
    workspace = cast(NozzleWorkspace, workspace)
    sides = [
        replace(s, domain=workspace.support) if s.domain is None else s for s in sides
    ]
    planes = [
        replace(s, domain=workspace.support) if s.domain is None else s for s in planes
    ]
    if isinstance(axis_parameters, dict):
        raise ValueError("legacy example groups require their explicit numerical chart")
    if not sides and not planes:
        raise ValueError("a fixed-axis fit requires at least one surface")
    if axis_parameters.shape != (7,) or not np.isfinite(axis_parameters).all():
        raise ValueError("fixed axis must contain seven finite source-fit parameters")
    raw_axis = np.array([axis_parameters[2], axis_parameters[3], 1.0])
    axis = raw_axis / np.linalg.norm(raw_axis)
    point = np.array([axis_parameters[0], axis_parameters[1], 0.0])
    surfaces: dict[str, SurfaceResult] = {}
    weighted_squares = 0.0
    total_area = 0.0
    condition = 1.0
    gradient = 0.0

    for side in sides:
        if len(side.ids) < 7:
            raise ValueError(f"{side.id}: select at least seven lateral vertices")
        points = workspace.local[side.ids]
        weights = workspace.data.weights[side.ids]
        normalized = normalized_weights(weights)
        relative = points - point
        z = relative @ axis
        radial = np.linalg.norm(relative - z[:, None] * axis, axis=1)
        solver = None
        if side.kind == "cylinder":
            radius = float(normalized @ radial)
            if radius <= 0.0:
                raise ValueError(f"{side.id}: fitted cylinder radius must be positive")
            taper = 0.0
            local_condition = 1.0
        elif side.kind == "cone":
            # Radial regression is only an initializer. The optimized objective
            # is the same orthogonal distance used by the free-axis cone fit.
            center_z = float(normalized @ z)
            length = CoordinateFrame.from_observations(points, weights).length
            design = np.column_stack([np.ones(len(z)), (z - center_z) / length])
            guess, _, rank, _ = np.linalg.lstsq(
                np.sqrt(normalized)[:, None] * design,
                np.sqrt(normalized) * radial,
                rcond=None,
            )
            if rank != 2:
                raise ValueError(f"{side.id}: rank-deficient cone observations")
            taper = float(guess[1] / length)
            radius = float(guess[0] - taper * center_z)
            initial = axis_parameters.copy()
            initial[4], initial[6] = radius, taper
            coordinates = FitCoordinates(
                CoordinateFrame.from_observations(points, weights),
                7,
                axes=(AxisChart(0, 1, 2, 3, 4, 6),),
            )
            side_domain = _explicit_domain(side)
            fitted = solve_geometric_fit(
                initial,
                weights,
                coordinates,
                lambda p, points=points, domain=side_domain: (
                    cone_plane_residual_jacobian(points, np.empty((0, 3)), p, domain)
                ),
                lambda p, points=points, domain=side_domain: valid_cone_geometry(
                    p, domain, points
                ),
                active_parameters=(4, 6),
            )
            radius, taper = fitted.parameters[[4, 6]]
            local_condition = fitted.condition
            solver = fitted.solver
            gradient = max(
                gradient,
                float(
                    np.max(
                        np.abs(
                            fitted.jacobian[:, [4, 6]].T @ (weights * fitted.residual)
                        )
                    )
                ),
            )
        else:
            raise ValueError("fixed-axis lateral surfaces must be cones or cylinders")
        residual = (radial - (radius + taper * z)) / np.hypot(1.0, taper)
        parameters = axis_parameters.copy()
        parameters[4], parameters[6] = radius, taper
        if not valid_cone_geometry(parameters, _explicit_domain(side), points):
            raise ValueError(f"{side.id}: invalid lateral geometry")
        area = float(weights.sum())
        weighted_squares += float(weights @ residual**2)
        total_area += area
        condition = max(condition, local_condition)
        surfaces[side.id] = {
            "kind": side.kind,
            "ids": side.ids,
            "parameters": parameters.tolist(),
            "axial_domain": _explicit_domain(side),
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(weights @ residual**2 / area)),
        }
        if solver is not None:
            surfaces[side.id]["solver"] = solver

    plane_offsets: list[float] = []
    plane_residuals: list[np.ndarray] = []
    for plane in planes:
        if plane.kind != "plane":
            raise ValueError("fixed-axis plane inputs must be planes")
        if len(plane.ids) < 3:
            raise ValueError(f"{plane.id}: select at least three plane vertices")
        points = workspace.local[plane.ids]
        weights = workspace.data.weights[plane.ids]
        offset = float(weights @ (points @ axis) / weights.sum())
        residual = points @ axis - offset
        parameters = axis_parameters.copy()
        parameters[5] = offset
        area = float(weights.sum())
        weighted_squares += float(weights @ residual**2)
        total_area += area
        plane_offsets.append(offset)
        plane_residuals.append(residual)
        surfaces[plane.id] = {
            "kind": "plane",
            "ids": plane.ids,
            "parameters": parameters.tolist(),
            "axial_domain": _explicit_domain(plane),
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(weights @ residual**2 / area)),
        }

    summary = axis_parameters.copy()
    if sides:
        summary = np.asarray(surfaces[sides[0].id]["parameters"], dtype=float)
    if plane_offsets:
        summary[5] = plane_offsets[0]
    plane_point = point + (summary[5] - axis @ point) * axis
    weighted_rms = float(np.sqrt(weighted_squares / total_area))
    first_side_residuals = surfaces[sides[0].id]["residuals"] if sides else []
    first_plane_residuals = plane_residuals[0].tolist() if planes else []
    first_side_rms = surfaces[sides[0].id]["weighted_rms"] if sides else 0.0
    first_plane_rms = surfaces[planes[0].id]["weighted_rms"] if planes else 0.0
    return {
        "session": {
            "lateral_ids": sides[0].ids if sides else [],
            "plane_ids": planes[0].ids if planes else [],
            "source_sha256": workspace.default.source_sha256,
            "model_sha256": workspace.model_sha256,
            "schema_version": 1,
        },
        "fit": {
            "parameters": summary.tolist(),
            "objective_history": [weighted_rms**2],
            "weighted_rms": weighted_rms,
            "cone_weighted_rms": float(first_side_rms),
            "plane_weighted_rms": float(first_plane_rms),
            "normal_matrix_condition": condition,
            "gradient_infinity_norm": gradient / total_area,
        },
        "axis_display": axis.tolist(),
        "point_display": point.tolist(),
        "plane_point_display": plane_point.tolist(),
        "axial_domain": sides[0].domain if sides else (-2.0, 5.0),
        "lateral_residuals": list(first_side_residuals),
        "plane_residuals": first_plane_residuals,
        "reference_diameter": float(2 * summary[4]),
        "signed_half_angle_degrees": float(np.degrees(np.arctan(summary[6]))),
        "selected_area": {
            surface.id: float(workspace.data.weights[surface.ids].sum())
            for surface in [*sides, *planes]
        },
        "surfaces": surfaces,
    }
