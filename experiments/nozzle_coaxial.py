"""Captured-nozzle adapter for a connected coaxial group and perpendicular planes."""

from dataclasses import dataclass

import numpy as np

from experiments.mesh_coaxial_fit import SideObservations, fit_coaxial
from experiments.mesh_mirror_surfaces import MirrorSurfaces, initial_mirror
from experiments.mesh_rotational_planes import RotationalPlanes, initial_rotation
from experiments.nozzle_session import NozzleWorkspace, SessionFit, SurfaceResult
from experiments.selection_growth import fit_seed


@dataclass(frozen=True)
class FitSelection:
    id: str
    ids: list[int]
    kind: str
    domain: tuple[float, float]


def fit_group(
    workspace: NozzleWorkspace,
    sides: list[FitSelection],
    planes: list[FitSelection],
    rotational_groups: tuple[tuple[FitSelection, ...], ...] = (),
    axis_initial: np.ndarray | None = None,
    mirror_groups: tuple[tuple[FitSelection, FitSelection], ...] = (),
    mirror_phases_radians: tuple[float, ...] | None = None,
    mirror_radius_surface_ids: tuple[str | None, ...] | None = None,
) -> SessionFit:
    """Joint fit; a mirror's optional radius source makes its planes tangent."""
    observations = [
        SideObservations(
            workspace.local[s.ids], workspace.data.weights[s.ids], s.kind, s.domain
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
            domain=group[0].domain,
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
                group[0].domain,
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
            "axial_domain": side.domain,
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
            "axial_domain": selected_plane.domain,
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(weights @ residual**2 / weights.sum())),
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
                    *(s for group in rotational_groups for s in group),
                    *(s for group in mirror_groups for s in group),
                ]
            },
        },
        "surfaces": surfaces,
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
    workspace: NozzleWorkspace,
    sides: list[FitSelection],
    planes: list[FitSelection],
    axis_parameters: np.ndarray,
) -> SessionFit:
    """Fit radii, taper, and plane offsets without moving the supplied axis."""
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

    for side in sides:
        if len(side.ids) < 7:
            raise ValueError(f"{side.id}: select at least seven lateral vertices")
        points = workspace.local[side.ids]
        weights = workspace.data.weights[side.ids]
        relative = points - point
        z = relative @ axis
        radial = np.linalg.norm(relative - z[:, None] * axis, axis=1)
        if side.kind == "cylinder":
            radius = float(weights @ radial / weights.sum())
            if radius <= 0.0:
                raise ValueError(f"{side.id}: fitted cylinder radius must be positive")
            taper = 0.0
            local_condition = 1.0
        elif side.kind == "cone":
            design = np.column_stack([np.ones(len(z)), z])
            normal = design.T @ (weights[:, None] * design)
            local_condition = float(np.linalg.cond(normal))
            if not np.isfinite(local_condition) or local_condition > 1e12:
                raise ValueError(f"{side.id}: ill-conditioned cone observations")
            radius, taper = np.linalg.solve(normal, design.T @ (weights * radial))
            if min(radius + taper * np.asarray(side.domain)) <= 0.0:
                raise ValueError(f"{side.id}: fitted cone crosses its apex")
        else:
            raise ValueError("fixed-axis lateral surfaces must be cones or cylinders")
        residual = radial - (radius + taper * z)
        parameters = axis_parameters.copy()
        parameters[4], parameters[6] = radius, taper
        area = float(weights.sum())
        weighted_squares += float(weights @ residual**2)
        total_area += area
        condition = max(condition, local_condition)
        surfaces[side.id] = {
            "kind": side.kind,
            "ids": side.ids,
            "parameters": parameters.tolist(),
            "axial_domain": side.domain,
            "residuals": residual.tolist(),
            "weighted_rms": float(np.sqrt(weights @ residual**2 / area)),
        }

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
            "axial_domain": plane.domain,
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
            "gradient_infinity_norm": 0.0,
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
