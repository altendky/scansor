"""Experimental OCP STEP geometry plus reference-mesh/provenance sidecars.

Face collection exports remain separate faces. Only an explicitly validated Body
exports a solid; observation bounds never define its physical boundary.
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
from pydantic import BaseModel, ConfigDict, Field

from experiments.body_geometry import body_from_record, body_input_fingerprint
from experiments.face_geometry import face_from_record
from experiments.feature_graph import Recipe, StaleGraph, dependencies
from experiments.native_replay import native_replay_scope
from experiments.nozzle_session import NozzleWorkspace
from experiments.ocp_geometry import (
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
    scope: Literal["all_faces", "selected_faces", "body", "target"] = "target"
    target: str | None = None
    targets: list[str] | None = None
    review_owners: list[str] = Field(default_factory=list)
    units: Literal["Millimeters", "Centimeters", "Meters"]
    axis_up: bool = True
    include_mesh: bool = True
    origin_plane: str | None = None
    transform: str | None = None


def export_face_targets(
    snapshot: dict[str, Any], request: CadExportRequest
) -> list[str]:
    """Resolve an export selection without silently dropping unavailable faces."""
    nodes = {node["id"]: node for node in snapshot["recipe"]["nodes"]}
    if request.review_owners and request.scope != "selected_faces":
        raise ValueError("review owners are only valid for selected faces export")
    for key in request.review_owners:
        node = nodes.get(key)
        if node is None:
            raise ValueError(f"unknown export face review {key!r}")
        if node["operation"] != "build_faces":
            raise ValueError(f"{node['label']!r} is not a face review")
    if request.scope == "body":
        if request.target is None or request.targets is not None:
            raise ValueError("body export requires one Body and no targets list")
        node = nodes.get(request.target)
        if node is None or node["operation"] != "body":
            raise ValueError("select a Body to export a solid")
        if request.axis_up or request.origin_plane is not None:
            raise ValueError(
                "body export requires axis_up=false and no origin plane; use a Transform"
            )
        return [request.target]
    if request.scope == "target":
        if request.target is None or request.targets is not None:
            raise ValueError("target export requires one target and no targets list")
        return [request.target]
    if request.target is not None:
        raise ValueError("face collection export cannot include a target")
    if request.axis_up or request.origin_plane is not None:
        raise ValueError(
            "face collection export requires axis_up=false and no origin plane; use a Transform for a common output frame"
        )
    if request.scope == "all_faces":
        if request.targets is not None:
            raise ValueError("all faces export cannot include a targets list")
        selected = {
            key
            for key, node in nodes.items()
            if node["operation"] in {"trimmed_face", "arranged_face"}
        }
    else:
        selected = set(request.targets or [])
        for key in selected:
            node = nodes.get(key)
            if node is None:
                raise ValueError(f"unknown export face {key!r}")
            if node["operation"] not in {"trimmed_face", "arranged_face"}:
                raise ValueError(f"{node['label']!r} is not a built face")
    if not selected:
        raise ValueError("select at least one built face to export")
    return [key for key in nodes if key in selected]


def _preflight_faces(
    snapshot: dict[str, Any],
    targets: list[str],
    nodes: dict[str, Any],
    request: CadExportRequest,
) -> None:
    """Check retained physical outputs and all authoring inputs before replay."""
    recipe = Recipe.model_validate(snapshot["recipe"])
    typed = {node.id: node for node in recipe.nodes}
    required = set(targets)
    required.update(request.review_owners)
    if request.scope == "all_faces":
        required.update(
            node.id for node in recipe.nodes if node.operation == "build_faces"
        )
    if request.transform is not None:
        required.add(request.transform)
    pending = list(required)
    while pending:
        key = pending.pop()
        if key not in typed:
            raise ValueError(f"missing export dependency {key!r}")
        related = dependencies(typed[key])
        if typed[key].operation == "build_faces":
            related.extend(node.id for node in recipe.nodes if node.managed_by == key)
        for dependency in related:
            if dependency not in required:
                required.add(dependency)
                pending.append(dependency)
    problems: list[str] = []
    for node in recipe.nodes:
        if node.id not in required:
            continue
        state = snapshot["states"].get(node.id, "unevaluated")
        if state != "ready":
            error = snapshot.get("errors", {}).get(node.id)
            problems.append(
                f"{node.label!r}: {state}" + (f" ({error})" if error else "")
            )
        raw = nodes[node.id]
        if (
            raw["operation"] == "build_faces"
            and raw.get("boundary_sources")
            and not (
                snapshot.get("derived", {})
                .get(node.id, {})
                .get("shared_boundary_review", {})
                .get("complete", False)
            )
        ):
            problems.append(f"{node.label!r}: shared-boundary review is incomplete")
    for key in targets:
        node = nodes[key]
        record = snapshot["results"].get(key)
        if record is None:
            problems.append(f"{node['label']!r}: no current geometry result")
        elif node["operation"] == "body":
            if record.get("valid") is not True or record.get("kind") != "body":
                problems.append(f"{node['label']!r}: no validated solid")
            if (
                set(record.get("face_ids", [])) != set(node["faces"])
                or len(record.get("face_ids", [])) != len(node["faces"])
                or record.get("sewing_tolerance") != node.get("sewing_tolerance", 1e-7)
                or record.get("input_fingerprint")
                != body_input_fingerprint(
                    {
                        face_id: snapshot["results"].get(face_id)
                        for face_id in node["faces"]
                    },
                    node.get("sewing_tolerance", 1e-7),
                )
            ):
                problems.append(f"{node['label']!r}: assembly inputs are not current")
        elif not record.get("bounded", False):
            problems.append(f"{node['label']!r}: open region has no physical bounds")
    if problems:
        raise ValueError(
            (
                "cannot export Body: "
                if request.scope == "body"
                else "cannot export built faces: "
            )
            + "; ".join(dict.fromkeys(problems))
        )


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
@native_replay_scope()
def export_cad(
    workspace: NozzleWorkspace, snapshot: dict[str, Any], request: CadExportRequest
) -> bytes:
    if snapshot["token"] != request.token:
        raise StaleGraph("graph changed before export; refresh and export again")
    nodes = {n["id"]: n for n in snapshot["recipe"]["nodes"]}
    targets = export_face_targets(snapshot, request)
    body = request.scope == "body"
    collection = request.scope in {"all_faces", "selected_faces"}
    if collection or body:
        _preflight_faces(snapshot, targets, nodes, request)
    target = targets[0]
    node = nodes.get(target)
    if node is None or node["operation"] not in (
        "fit",
        "joint_fit",
        "axis_solve",
        "trimmed_face",
        "arranged_face",
        "body",
    ):
        raise ValueError("select a fit, solve, or trimmed face to export")
    if node["operation"] == "body" and not body:
        raise ValueError("use Body export for a validated solid")
    if snapshot["states"].get(target) != "ready":
        raise ValueError("evaluate the selected fit or solve before export")
    result = snapshot["results"][target]
    if collection or body:
        surfaces = {id: snapshot["results"][id] for id in targets}
        axis, anchor = np.array([0.0, 0.0, 1.0]), np.zeros(3)
    elif node["operation"] in ("trimmed_face", "arranged_face"):
        surfaces = {target: result}
        axis = np.asarray(result["geometry"]["axis"], dtype=float)
        anchor = np.asarray(result["geometry"]["origin"], dtype=float)
    elif node["operation"] in ("joint_fit", "axis_solve"):
        surfaces = result["surfaces"]
        axis = np.asarray(result["axis_display"], dtype=float)
        anchor = np.asarray(result["point_display"], dtype=float)
    else:
        surfaces = {target: result}
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
        {
            target: body_from_record(
                result,
                {face_id: snapshot["results"][face_id] for face_id in node["faces"]},
            )
        }
        if body
        else {id: face_from_record(surface) for id, surface in surfaces.items()}
        if collection
        else joint_shapes(result, relationships, workspace.local)
        if node["operation"] in ("joint_fit", "axis_solve")
        else {target: face_from_record(result)}
        if node["operation"] in ("trimmed_face", "arranged_face")
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
            "layer": "Bodies" if body else "Fitted surfaces",
            "extent_authority": "declared_boundaries"
            if collection
            or body
            or node["operation"] in ("trimmed_face", "arranged_face")
            else "observation_bounded_patch",
            **(
                {
                    "source_surface": nodes[id]["surface"],
                    "boundary_uses": nodes[id].get("boundaries", []),
                    "boundary_sources": nodes[id].get("boundary_sources", []),
                    "managed_by": nodes[id].get("managed_by"),
                    **(
                        {
                            "cutters": nodes[id]["cutters"],
                            "domains": nodes[id]["domains"],
                            "region_selector": nodes[id]["selector"],
                        }
                        if nodes[id]["operation"] == "arranged_face"
                        else {}
                    ),
                    "bounds": fitted["bounds"],
                }
                if collection or node["operation"] in ("trimmed_face", "arranged_face")
                else {}
            ),
        }
        for id, fitted in surfaces.items()
    ]
    if body:
        objects = [
            {
                "feature": target,
                "name": node["label"],
                "layer": "Bodies",
                "extent_authority": "declared_boundaries",
                "source_faces": node["faces"],
                "sewing_tolerance": result["sewing_tolerance"],
                "volume_local": result["volume"],
                "input_fingerprint": result["input_fingerprint"],
                "faces": {
                    face_id: {
                        "name": nodes[face_id]["label"],
                        "surface": nodes[face_id]["surface"],
                        "boundary_uses": nodes[face_id].get("boundaries", []),
                        "boundary_sources": nodes[face_id].get("boundary_sources", []),
                        "cutters": nodes[face_id].get("cutters", []),
                        "domains": nodes[face_id].get("domains", []),
                        "region_selector": nodes[face_id].get("selector"),
                    }
                    for face_id in node["faces"]
                },
            }
        ]
    metadata = {
        "revision": "scansor-cad-export-v1",
        "recipe": snapshot["recipe"],
        "export": request.model_dump(mode="json"),
        "units": request.units,
        "geometry_mode": "solid" if body else "separate_faces",
        "sewn": body,
        "solid": body,
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
