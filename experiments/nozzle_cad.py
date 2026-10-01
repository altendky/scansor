"""Experimental OCP STEP faces plus exact reference-mesh/provenance sidecars.

The bundle does not sew faces or infer solids. Observation-bounded fitted patches
remain display/export support; only declared trimmed-face bounds are physical.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Any, ClassVar, Literal, cast
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_DocumentTool
from pydantic import BaseModel, ConfigDict

from experiments.feature_graph import StaleGraph
from experiments.nozzle_session import NozzleWorkspace
from experiments.ocp_geometry import (
    face_from_record,
    kernel_operation,
    surface_patch,
    transform_shape,
)
from scansor._plyio import (
    Element,
    ListProperty,
    ScalarProperty,
    Writer,
    build_layout,
    make_header,
)

_STEP_LOCK = Lock()
_UNITS = {
    "Millimeters": ("MM", 1.0),
    "Centimeters": ("CM", 10.0),
    "Meters": ("M", 1000.0),
}


class CadExportRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    token: str
    target: str
    units: Literal["Millimeters", "Centimeters", "Meters"]
    axis_up: bool = True
    include_mesh: bool = True
    origin_plane: str | None = None
    transform: str | None = None


def joint_shapes(
    result: dict[str, Any], constraints: list[dict[str, Any]], positions: np.ndarray
) -> dict[str, TopoDS_Shape]:
    """Bound symmetry members in slot zero's frame, then transform one patch."""
    surfaces = result["surfaces"]
    patches: dict[str, TopoDS_Shape] = {}
    axis = np.asarray(result["axis_display"], dtype=float)
    axis /= np.linalg.norm(axis)
    center = np.asarray(result["point_display"], dtype=float)
    x, y, z = axis
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    for constraint in constraints:
        if not constraint.get("symmetric_extents", True):
            continue
        if constraint["operation"] == "rotational_symmetry":
            ids = constraint["planes"]
            rotations = [
                np.eye(3) * np.cos(slot * 2 * np.pi / 3)
                + cross * np.sin(slot * 2 * np.pi / 3)
                + np.outer(axis, axis) * (1 - np.cos(slot * 2 * np.pi / 3))
                for slot in range(3)
            ]
            combined = np.vstack(
                [
                    center + (positions[surfaces[id]["ids"]] - center) @ rotation
                    for id, rotation in zip(ids, rotations, strict=True)
                ]
            )
            base = surface_patch(
                {**surfaces[ids[0]], "ids": list(range(len(combined)))}, combined
            )
            for id, rotation in zip(ids, rotations, strict=True):
                matrix = np.eye(4)
                matrix[:3, :3] = rotation
                matrix[:3, 3] = center - rotation @ center
                patches[id] = transform_shape(base, matrix)
        elif constraint["operation"] == "mirror_symmetry":
            ids = constraint["surfaces"]
            mirror = result.get("mirror_planes", {}).get(constraint["plane"])
            if mirror is None:
                raise ValueError("mirror symmetry result omitted its reference plane")
            equation = np.asarray(mirror.get("plane_equation"), dtype=float)
            if equation.shape != (4,) or not np.isfinite(equation).all():
                raise ValueError(
                    "mirror plane equation must contain four finite values"
                )
            length = float(np.linalg.norm(equation[:3]))
            if length <= 1e-12:
                raise ValueError("mirror plane normal must be nonzero")
            normal, distance = equation[:3] / length, float(equation[3] / length)
            reflection = np.eye(3) - 2 * np.outer(normal, normal)
            offset = 2 * distance * normal
            combined = np.vstack(
                [
                    positions[surfaces[ids[0]]["ids"]],
                    positions[surfaces[ids[1]]["ids"]] @ reflection.T + offset,
                ]
            )
            base = surface_patch(
                {**surfaces[ids[0]], "ids": list(range(len(combined)))}, combined
            )
            matrix = np.eye(4)
            matrix[:3, :3], matrix[:3, 3] = reflection, offset
            patches[ids[0]], patches[ids[1]] = base, transform_shape(base, matrix)
    for id, surface in surfaces.items():
        if id not in patches:
            patches[id] = surface_patch(surface, positions)
    return patches


def _export_transform(
    workspace: NozzleWorkspace,
    snapshot: dict[str, Any],
    request: CadExportRequest,
    nodes: dict[str, Any],
    surfaces: dict[str, Any],
    axis: np.ndarray,
    anchor: np.ndarray,
) -> np.ndarray:
    matrix = np.eye(4)
    if request.transform is not None:
        if request.axis_up or request.origin_plane is not None:
            raise ValueError(
                "an explicit transform cannot be combined with axis-up export"
            )
        node = nodes.get(request.transform)
        if node is None or node["operation"] != "transform":
            raise ValueError("export transform must name a transform feature")
        if snapshot["states"].get(request.transform) != "ready":
            raise ValueError("evaluate the selected transform before export")
        matrix = np.asarray(
            snapshot["results"][request.transform]["matrix"], dtype=float
        )
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("export transform must contain a finite 4x4 matrix")
    elif request.origin_plane is not None and not request.axis_up:
        raise ValueError("an origin plane requires axis-up export")
    elif request.axis_up:
        axis = axis / np.linalg.norm(axis)
        if request.origin_plane is not None:
            selected = surfaces.get(request.origin_plane)
            if selected is None or selected["kind"] != "plane":
                raise ValueError(
                    "origin plane must be a plane in the exported fit or solve"
                )
            p = np.asarray(
                selected.get("plane_equation", selected["parameters"]), dtype=float
            )
            if len(p) == 7:
                normal = np.array([p[2], p[3], 1.0])
                normal /= np.linalg.norm(normal)
                distance = p[5]
            else:
                normal, distance = p[:3], p[3]
            denominator = float(normal @ axis)
            if abs(denominator) <= 1e-10 * np.linalg.norm(normal):
                raise ValueError(
                    "origin plane is parallel to the axis; no unique intersection"
                )
            anchor = anchor + axis * ((distance - normal @ anchor) / denominator)
        # Minimal rotation taking the fitted axis to +Z, including the antipodal case.
        target = np.array([0.0, 0.0, 1.0])
        cross = np.cross(axis, target)
        cosine = float(axis @ target)
        if cosine < -1 + 1e-14:
            rotation = np.diag([1.0, -1.0, -1.0])
        else:
            x, y, z = cross
            skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
            rotation = np.eye(3) + skew + skew @ skew / (1 + cosine)
        matrix[:3, :3] = rotation
        rotated_anchor = rotation @ anchor
        matrix[:2, 3] = -rotated_anchor[:2]
        if request.origin_plane is not None:
            matrix[2, 3] = -rotated_anchor[2]
    else:
        matrix[:3, :3], matrix[:3, 3] = workspace.frame, workspace.origin
    return matrix


def _step_bytes(
    patches: dict[str, TopoDS_Shape], names: dict[str, str], units: str
) -> bytes:
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    unit_name, unit_mm = _UNITS[units]
    XCAFDoc_DocumentTool.SetLengthUnit_s(document, unit_mm / 1000)
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    for id, shape in patches.items():
        label = shapes.AddShape(shape, False)
        _ = TDataStd_Name.Set_s(label, TCollection_ExtendedString(names[id]))
    # This binding does not expose DESTEP_Parameters. The native writer consults
    # a process-global setting despite its model unit setters. Serialize, restore
    # it even on failure, and set the document's input unit to prevent rescaling.
    with _STEP_LOCK:
        writer = STEPCAFControl_Writer()
        previous = Interface_Static.CVal_s("write.step.unit")
        try:
            if not Interface_Static.SetCVal_s("write.step.unit", unit_name):
                raise ValueError("OCP could not configure STEP export units")
            writer.SetNameMode(True)
            # The stubs include unknown DESTEP_Parameters overloads which the
            # wheel does not expose. Bind only the tested document/mode overload.
            transfer = cast(
                Callable[[TDocStd_Document, STEPControl_StepModelType], bool],
                cast(Any, writer).Transfer,
            )
            if not transfer(document, STEPControl_AsIs):
                raise ValueError("OCP could not transfer fitted faces to STEP")
            with TemporaryDirectory(prefix="scansor-step-") as temporary:
                path = Path(temporary) / "model.step"
                if writer.Write(str(path)) != IFSelect_RetDone:
                    raise ValueError("OCP could not write STEP geometry")
                return path.read_bytes()
        finally:
            _ = Interface_Static.SetCVal_s("write.step.unit", previous)


def _mesh_bytes(positions: np.ndarray, triangles: np.ndarray) -> bytes:
    """Double XYZ and original triangle order, using the independent PLY writer."""
    if (
        positions.ndim != 2
        or positions.shape[1] != 3
        or not np.isfinite(positions).all()
        or triangles.ndim != 2
        or triangles.shape[1] != 3
        or np.any(triangles < 0)
        or np.any(triangles >= len(positions))
        or len(positions) > 2**31
    ):
        raise ValueError("reference mesh is invalid")
    header = make_header(
        (
            Element(
                "vertex",
                len(positions),
                tuple(ScalarProperty(n, "double") for n in ("x", "y", "z")),
            ),
            Element(
                "face",
                len(triangles),
                (ListProperty("vertex_indices", "uchar", "int"),),
            ),
        ),
        comments=("Scansor reference mesh; units and provenance in metadata.json",),
    )
    layout = build_layout(header, fixed_lists={("face", "vertex_indices"): 3})
    output = io.BytesIO()
    writer = Writer(output, layout, max_range_bytes=24 * 65_536)
    for start in range(0, len(positions), 65_536):
        xyz = positions[start : start + 65_536]
        rows = np.empty(len(xyz), dtype=layout.element("vertex").dtype)
        for i, name in enumerate(("x", "y", "z")):
            rows[name] = xyz[:, i]
        writer.write_range("vertex", start, rows)
    for start in range(0, len(triangles), 65_536):
        faces = triangles[start : start + 65_536]
        rows = np.empty(len(faces), dtype=layout.element("face").dtype)
        rows["vertex_indices"]["count"] = 3
        rows["vertex_indices"]["values"] = faces
        writer.write_range("face", start, rows)
    writer.finish()
    return output.getvalue()


@kernel_operation
def export_cad(
    workspace: NozzleWorkspace, snapshot: dict[str, Any], request: CadExportRequest
) -> bytes:
    if snapshot["token"] != request.token:
        raise StaleGraph("graph changed before export; refresh and export again")
    nodes = {n["id"]: n for n in snapshot["recipe"]["nodes"]}
    node = nodes.get(request.target)
    if node is None or node["operation"] not in (
        "fit",
        "joint_fit",
        "axis_solve",
        "trimmed_face",
    ):
        raise ValueError("select a fit, solve, or trimmed face to export")
    if snapshot["states"].get(request.target) != "ready":
        raise ValueError("evaluate the selected fit or solve before export")
    result = snapshot["results"][request.target]
    if node["operation"] == "trimmed_face":
        surfaces = {request.target: result}
        axis = np.asarray(result["geometry"]["axis"], dtype=float)
        anchor = np.asarray(result["geometry"]["origin"], dtype=float)
    elif node["operation"] in ("joint_fit", "axis_solve"):
        surfaces = result["surfaces"]
        axis = np.asarray(result["axis_display"], dtype=float)
        anchor = np.asarray(result["point_display"], dtype=float)
    else:
        surfaces = {request.target: result}
        p = np.asarray(result.get("plane_equation", result["parameters"]), dtype=float)
        if node["kind"] == "plane":
            axis = p[:3] / np.linalg.norm(p[:3])
            anchor = axis * (p[3] / np.linalg.norm(p[:3]))
        elif node["kind"] == "sphere":
            axis, anchor = np.array([0.0, 0.0, 1.0]), p[:3]
        else:
            axis, anchor = np.array([p[2], p[3], 1.0]), np.array([p[0], p[1], 0.0])
    matrix = _export_transform(
        workspace, snapshot, request, nodes, surfaces, axis, anchor
    )
    relationships = (
        [nodes[id] for id in node["constraints"]]
        if node["operation"] == "joint_fit"
        else [
            nodes[id]
            for id in node.get("factors", [])
            if nodes[id]["operation"] == "mirror_symmetry"
        ]
        if node["operation"] == "axis_solve"
        else []
    )
    patches = (
        joint_shapes(result, relationships, workspace.local)
        if node["operation"] in ("joint_fit", "axis_solve")
        else {request.target: face_from_record(result)}
        if node["operation"] == "trimmed_face"
        else {
            id: surface_patch(surface, workspace.local)
            for id, surface in surfaces.items()
        }
    )
    patches = {id: transform_shape(shape, matrix) for id, shape in patches.items()}
    objects = [
        {
            "feature": id,
            "name": nodes[id]["label"],
            "surface_kind": fitted.get("surface_kind", fitted["kind"]),
            "layer": "Fitted surfaces",
            "extent_authority": "declared_boundaries"
            if node["operation"] == "trimmed_face"
            else "observation_bounded_patch",
            **(
                {
                    "source_surface": node["surface"],
                    "boundary_uses": node["boundaries"],
                    "bounds": fitted["bounds"],
                }
                if node["operation"] == "trimmed_face"
                else {}
            ),
        }
        for id, fitted in surfaces.items()
    ]
    metadata = {
        "revision": "scansor-cad-export-v1",
        "recipe": snapshot["recipe"],
        "export": request.model_dump(mode="json"),
        "units": request.units,
        "transform_local_to_export": matrix.tolist(),
        "source_sha256": workspace.default.source_sha256,
        "objects": objects,
        "reference_mesh": {
            "file": "reference.ply",
            "name": "Original reference mesh",
            "layer": "Reference mesh",
            "vertices": len(workspace.local),
            "triangles": len(workspace.data.triangles),
        }
        if request.include_mesh
        else None,
    }
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as bundle:
        bundle.writestr(
            "model.step",
            _step_bytes(
                patches, {id: nodes[id]["label"] for id in patches}, request.units
            ),
        )
        bundle.writestr(
            "metadata.json", json.dumps(metadata, indent=2, allow_nan=False)
        )
        if request.include_mesh:
            positions = workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
            bundle.writestr(
                "reference.ply", _mesh_bytes(positions, workspace.data.triangles)
            )
    return output.getvalue()
