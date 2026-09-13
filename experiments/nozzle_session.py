"""Frontend-independent selections and fitting for the captured nozzle experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, ClassVar, Literal, NotRequired, TypedDict, final

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from experiments.mesh_cone_plane_fit import (
    ConePlaneFitResult,
    cone_plane_residual_jacobian,
    fit_cone_plane,
)
from experiments.mesh_cylinder_fit import fit_cylinder
from experiments.mesh_cylinder_plane_fit import (
    fit_cylinder_plane,
    joint_residual_jacobian,
)
from experiments.run_nozzle_cylinder import load_example
from experiments.run_nozzle_cylinder_plane import load_plane_ids

VertexId = Annotated[StrictInt, Field(ge=0)]


class NozzleSession(BaseModel):
    """Portable, experimental selection recipe; no camera or browser objects."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    source_sha256: str
    model_sha256: str
    lateral_ids: list[VertexId] = Field(max_length=25_000)
    plane_ids: list[VertexId] = Field(max_length=25_000)


class SurfaceResult(TypedDict):
    kind: str
    ids: list[int]
    parameters: list[float]
    axial_domain: tuple[float, float]
    residuals: list[float]
    weighted_rms: float


class SessionFit(TypedDict):
    surfaces: NotRequired[dict[str, SurfaceResult]]
    session: dict[str, object]
    fit: ConePlaneFitResult
    axis_display: list[float]
    point_display: list[float]
    plane_point_display: list[float]
    axial_domain: tuple[float, float]
    lateral_residuals: list[float]
    plane_residuals: list[float]
    reference_diameter: float
    signed_half_angle_degrees: float
    selected_area: dict[str, float]


@final
class NozzleWorkspace:
    """Small-example adapter with immutable source geometry and per-request fits."""

    def __init__(self, example: Path) -> None:
        self.data = load_example(example)
        model_bytes = (example / "models/cone-plane.json").read_bytes()
        self.model_sha256: str = hashlib.sha256(model_bytes).hexdigest()
        model = json.loads(model_bytes)
        lo, hi = model["axial_domain"]
        self.support: tuple[float, float] = (float(lo), float(hi))
        self.origin = np.array(self.data.selection["fit_frame"]["origin"])
        self.frame = np.array(self.data.selection["fit_frame"]["columns"])
        self.local = (self.data.xyz - self.origin) @ self.frame
        self.default = NozzleSession(
            source_sha256=self.data.selection["source_sha256"],
            model_sha256=self.model_sha256,
            lateral_ids=self.data.ids.tolist(),
            plane_ids=load_plane_ids(example, self.data).tolist(),
        )

    def validate(self, session: NozzleSession) -> NozzleSession:
        """Bind IDs to source/model; allow empty selections for editing, not fitting."""
        if session.source_sha256 != self.default.source_sha256:
            raise ValueError("session belongs to another source mesh")
        if session.model_sha256 != self.model_sha256:
            raise ValueError("session belongs to another model declaration")
        for ids in (session.lateral_ids, session.plane_ids):
            if ids != sorted(set(ids)):
                raise ValueError("vertex IDs must be unique and in ascending order")
            if ids and ids[-1] >= len(self.local):
                raise ValueError("vertex ID outside source mesh")
            if ids and np.any(self.data.weights[ids] <= 0):
                raise ValueError("selected vertices must have positive incident area")
        if set(session.lateral_ids).intersection(session.plane_ids):
            raise ValueError("cone and plane selections must be disjoint")
        return session

    def metadata(self) -> dict[str, object]:
        return {
            "name": "nozzle-bayonette-simplified",
            "vertices": len(self.local),
            "triangles": len(self.data.triangles),
            "session": self.default.model_dump(),
            "display_frame": self.data.selection["fit_frame"],
            "axial_domain": self.support,
            "positions": {"url": "/mesh/positions", "dtype": "<f4", "components": 3},
            "indices": {"url": "/mesh/indices", "dtype": "<u4", "components": 3},
        }

    def fit(
        self,
        session: NozzleSession,
        kind: str = "cone",
        support: tuple[float, float] | None = None,
    ) -> SessionFit:
        """Reuse the existing joint solver; every selected observation participates."""
        session = self.validate(session)
        support = self.support if support is None else support
        if len(session.lateral_ids) < 7 or len(session.plane_ids) < 3:
            raise ValueError(
                "select at least seven cone vertices and three plane vertices"
            )
        cone = self.local[session.lateral_ids]
        plane = self.local[session.plane_ids]
        cw = self.data.weights[session.lateral_ids]
        pw = self.data.weights[session.plane_ids]
        cp = np.array(
            fit_cylinder(cone, cw, np.array(self.data.selection["initial_parameters"]))[
                "parameters"
            ]
        )
        axis = np.array([cp[2], cp[3], 1.0])
        axis /= np.linalg.norm(axis)
        h = float(pw @ (plane @ axis) / pw.sum())
        baseline = fit_cylinder_plane(cone, plane, cw, pw, np.concatenate((cp, [h])))
        if kind == "cone":
            result = fit_cone_plane(
                cone, plane, cw, pw, np.array([*baseline["parameters"], 0.0]), support
            )
            p = np.array(result["parameters"])
            residual, _ = cone_plane_residual_jacobian(cone, plane, p, support)
        elif kind == "cylinder":
            result: ConePlaneFitResult = {
                "parameters": [*baseline["parameters"], 0.0],
                "objective_history": baseline["objective_history"],
                "weighted_rms": baseline["weighted_rms"],
                "cone_weighted_rms": baseline["cylinder_weighted_rms"],
                "plane_weighted_rms": baseline["plane_weighted_rms"],
                "normal_matrix_condition": baseline["normal_matrix_condition"],
                "gradient_infinity_norm": baseline["gradient_infinity_norm"],
            }
            p = np.array(result["parameters"])
            residual, _ = joint_residual_jacobian(cone, plane, p[:6])
        else:
            raise ValueError("unsupported lateral surface type")
        axis = np.array([p[2], p[3], 1.0])
        axis /= np.linalg.norm(axis)
        point = np.array([p[0], p[1], 0.0])
        plane_z = float(p[5] - axis @ point)
        if not support[0] <= plane_z <= support[1]:
            raise ValueError("fitted plane lies outside the example's declared support")
        return {
            "session": session.model_dump(),
            "fit": result,
            "axis_display": axis.tolist(),
            "point_display": point.tolist(),
            "plane_point_display": (point + plane_z * axis).tolist(),
            "axial_domain": support,
            "lateral_residuals": residual[: len(cone)].tolist(),
            "plane_residuals": residual[len(cone) :].tolist(),
            "reference_diameter": float(2 * p[4]),
            "signed_half_angle_degrees": float(np.degrees(np.arctan(p[6]))),
            "selected_area": {"lateral": float(cw.sum()), "plane": float(pw.sum())},
        }
