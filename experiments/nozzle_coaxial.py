"""Captured-nozzle adapter for a connected coaxial group and end plane."""

from dataclasses import dataclass

import numpy as np

from experiments.mesh_coaxial_fit import SideObservations, fit_coaxial
from experiments.nozzle_session import NozzleWorkspace, SessionFit, SurfaceResult


@dataclass(frozen=True)
class FitSelection:
    id: str
    ids: list[int]
    kind: str
    domain: tuple[float, float]


def fit_group(
    workspace: NozzleWorkspace, sides: list[FitSelection], plane: FitSelection
) -> SessionFit:
    observations = [
        SideObservations(
            workspace.local[s.ids], workspace.data.weights[s.ids], s.kind, s.domain
        )
        for s in sides
    ]
    for s in sides:
        if len(s.ids) < 7:
            raise ValueError(f"{s.id}: select at least seven lateral vertices")
    if len(plane.ids) < 3:
        raise ValueError(f"{plane.id}: select at least three plane vertices")
    pp = workspace.local[plane.ids]
    pw = workspace.data.weights[plane.ids]
    seed = np.array(workspace.data.selection["initial_parameters"], dtype=float)
    axis = np.array([seed[2], seed[3], 1.0])
    axis /= np.linalg.norm(axis)
    initial = [*seed[:4], float(pw @ (pp @ axis) / pw.sum())]
    for side in observations:
        q = side.points - np.array([seed[0], seed[1], 0.0])
        z = q @ axis
        rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
        initial.append(float(side.area @ rho / side.area.sum()))
        if side.kind == "cone":
            initial.append(0.0)
    fitted = fit_coaxial(observations, pp, pw, np.array(initial))
    p = fitted.parameters[0]
    axis = np.array([p[2], p[3], 1.0])
    axis /= np.linalg.norm(axis)
    point = np.array([p[0], p[1], 0.0])
    plane_point = point + (p[5] - axis @ point) * axis
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
    plane_rms = float(np.sqrt(pw @ fitted.plane_residuals**2 / pw.sum()))
    surfaces[plane.id] = {
        "kind": "plane",
        "ids": plane.ids,
        "parameters": p.tolist(),
        "axial_domain": plane.domain,
        "residuals": fitted.plane_residuals.tolist(),
        "weighted_rms": plane_rms,
    }
    return {
        "session": {
            "lateral_ids": sides[0].ids,
            "plane_ids": plane.ids,
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
            plane.id: float(pw.sum()),
        },
        "surfaces": surfaces,
    }
