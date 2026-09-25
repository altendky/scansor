"""Experimental exact-surface and source-mesh Rhino export for the nozzle viewer."""

from __future__ import annotations

import base64
import json
from typing import Any, ClassVar, Literal, cast

import numpy as np
import rhino3dm
from pydantic import BaseModel, ConfigDict

from experiments.feature_graph import StaleGraph
from experiments.nozzle_session import NozzleWorkspace

# The wheel's bundled stubs omit working overloads, setters, and Encode.
# Keep the dynamic native API boundary here; round-trip tests exercise it.
rhino: Any = rhino3dm


class RhinoExportRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    token: str
    target: str
    units: Literal["Millimeters", "Centimeters", "Meters"]
    axis_up: bool = True
    include_mesh: bool = True
    origin_plane: str | None = None
    transform: str | None = None


def point(xyz: Any) -> rhino3dm.Point3d:
    return rhino.Point3d(*map(float, xyz))


def vector(xyz: Any) -> rhino3dm.Vector3d:
    return rhino.Vector3d(*map(float, xyz))


def surface_brep(surface: dict[str, Any], positions: Any) -> Any:
    """Simple bounded patches, not inferred topology or a watertight solid."""
    p = np.asarray(surface.get("plane_equation", surface["parameters"]), dtype=float)
    ids = surface["ids"]
    if not ids:
        raise ValueError("surface has no selected observations")
    selected = positions[ids]
    kind = surface["kind"]
    if kind == "plane":
        if len(p) == 7:  # perpendicular plane in the joint's shared parameter frame
            normal = np.array([p[2], p[3], 1.0])
            normal /= np.linalg.norm(normal)
            distance = p[5]
        else:
            normal, distance = p[:3], p[3]
        plane = rhino.Plane(point(normal * distance), vector(normal))
        x, y = (
            np.array([plane.XAxis.X, plane.XAxis.Y, plane.XAxis.Z]),
            np.array([plane.YAxis.X, plane.YAxis.Y, plane.YAxis.Z]),
        )
        uv = np.column_stack([selected @ x, selected @ y])
        lo, hi = uv.min(axis=0), uv.max(axis=0)
        if np.any(hi - lo <= 1e-12):
            raise ValueError("plane patch needs two-dimensional coverage")
        pad = (hi - lo) * 0.05
        surface_geometry = rhino.PlaneSurface(
            plane,
            rhino.Interval(lo[0] - pad[0], hi[0] + pad[0]),
            rhino.Interval(lo[1] - pad[1], hi[1] + pad[1]),
        )
        brep = rhino.Brep.CreateFromSurface(surface_geometry)
    elif kind == "sphere":
        if p.shape != (4,) or p[3] <= 0:
            raise ValueError("sphere parameters require a positive radius")
        brep = rhino.Sphere(point(p[:3]), float(p[3])).ToBrep()
    elif kind in ("cone", "cylinder"):
        origin = np.array([p[0], p[1], 0.0])
        axis = np.array([p[2], p[3], 1.0])
        axis /= np.linalg.norm(axis)
        radial = np.cross(axis, [0.0, 1.0, 0.0])
        radial /= np.linalg.norm(radial)
        z = (selected - origin) @ axis
        lo, hi = float(z.min()), float(z.max())
        if hi - lo <= 1e-12:
            raise ValueError("lateral patch needs axial coverage")
        pad = (hi - lo) * 0.05
        domain = surface["axial_domain"]
        lo, hi = max(domain[0], lo - pad), min(domain[1], hi + pad)
        if hi <= lo:
            raise ValueError("surface observations lie outside axial support")
        radii = p[4] + p[6] * np.array([lo, hi])
        if np.any(radii <= 0):
            raise ValueError("exported side crosses the cone apex")
        curve = rhino.LineCurve(
            point(origin + lo * axis + radii[0] * radial),
            point(origin + hi * axis + radii[1] * radial),
        )
        rev = rhino.RevSurface.Create(
            curve, rhino.Line(point(origin), point(origin + axis)), 0.0, 2 * np.pi
        )
        brep = rhino.Brep.CreateFromRevSurface(rev, False, False)
    else:
        raise ValueError("unsupported fitted surface type")
    if brep is None or not brep.IsValid:
        raise ValueError("Rhino could not construct a valid surface")
    return brep


def joint_breps(
    result: dict[str, Any], constraints: list[dict[str, Any]], positions: Any
) -> dict[str, Any]:
    """Bound symmetry members in slot zero's frame, then transform one patch."""
    surfaces = result["surfaces"]
    breps: dict[str, Any] = {}
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
            local: list[Any] = []
            for slot, id in enumerate(ids):
                angle = slot * 2 * np.pi / 3
                rotation = (
                    np.eye(3) * np.cos(angle)
                    + cross * np.sin(angle)
                    + np.outer(axis, axis) * (1 - np.cos(angle))
                )
                local.append(
                    center + (positions[surfaces[id]["ids"]] - center) @ rotation
                )
            combined = np.vstack(local)
            base = surface_brep(
                {**surfaces[ids[0]], "ids": list(range(len(combined)))}, combined
            )
            for slot, id in enumerate(ids):
                brep = base.Duplicate()
                rotation = rhino.Transform.Rotation(
                    slot * 2 * np.pi / 3, vector(axis), point(center)
                )
                if not brep.Transform(rotation):
                    raise ValueError("symmetry extent transform failed")
                breps[id] = brep
        elif constraint["operation"] == "mirror_symmetry":
            ids = constraint["surfaces"]
            mirror_plane = result.get("mirror_planes", {}).get(constraint["plane"])
            if mirror_plane is None:
                raise ValueError("mirror symmetry result omitted its reference plane")
            equation = np.asarray(mirror_plane.get("plane_equation"), dtype=float)
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
            first = positions[surfaces[ids[0]]["ids"]]
            second = positions[surfaces[ids[1]]["ids"]] @ reflection.T + offset
            combined = np.vstack([first, second])
            base = surface_brep(
                {**surfaces[ids[0]], "ids": list(range(len(combined)))}, combined
            )
            transform = rhino.Transform.Identity()
            for row in range(3):
                for col in range(3):
                    setattr(transform, f"M{row}{col}", float(reflection[row, col]))
                setattr(transform, f"M{row}3", float(offset[row]))
            breps[ids[0]] = base
            reflected = base.Duplicate()
            if not reflected.Transform(transform):
                raise ValueError("mirror extent transform failed")
            breps[ids[1]] = reflected
    for id, surface in surfaces.items():
        if id not in breps:
            breps[id] = surface_brep(surface, positions)
    return breps


def export_rhino(
    workspace: NozzleWorkspace, snapshot: dict[str, Any], request: RhinoExportRequest
) -> bytes:
    if snapshot["token"] != request.token:
        raise StaleGraph("graph changed before export; refresh and export again")
    nodes = {n["id"]: n for n in snapshot["recipe"]["nodes"]}
    node = nodes.get(request.target)
    if node is None or node["operation"] not in ("fit", "joint_fit", "axis_solve"):
        raise ValueError("select a fit or solve to export")
    if snapshot["states"].get(request.target) != "ready":
        raise ValueError("evaluate the selected fit or solve before export")
    result = snapshot["results"][request.target]
    if node["operation"] in ("joint_fit", "axis_solve"):
        surfaces = result["surfaces"]
        axis = np.asarray(result["axis_display"], dtype=float)
        anchor = np.asarray(result["point_display"], dtype=float)
    else:
        surfaces = {request.target: result}
        p = np.asarray(result.get("plane_equation", result["parameters"]), dtype=float)
        if node["kind"] == "plane":
            axis, anchor = p[:3], p[:3] * p[3]
        elif node["kind"] == "sphere":
            axis, anchor = np.array([0.0, 0.0, 1.0]), p[:3]
        else:
            axis, anchor = np.array([p[2], p[3], 1.0]), np.array([p[0], p[1], 0.0])
    if request.transform is not None:
        if request.axis_up or request.origin_plane is not None:
            raise ValueError(
                "an explicit transform cannot be combined with legacy axis-up export"
            )
        transform_node = nodes.get(request.transform)
        if transform_node is None or transform_node["operation"] != "transform":
            raise ValueError("export transform must name a transform feature")
        if snapshot["states"].get(request.transform) != "ready":
            raise ValueError("evaluate the selected transform before export")
        matrix = np.asarray(
            snapshot["results"][request.transform]["matrix"], dtype=float
        )
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("export transform must contain a finite 4x4 matrix")
        transform = rhino.Transform.Identity()
        for row in range(4):
            for col in range(4):
                setattr(transform, f"M{row}{col}", float(matrix[row, col]))
    elif request.origin_plane is not None and not request.axis_up:
        raise ValueError("an origin plane requires axis-up export")
    elif request.axis_up:
        if request.origin_plane is not None:
            selected_plane = surfaces.get(request.origin_plane)
            if selected_plane is None or selected_plane["kind"] != "plane":
                raise ValueError(
                    "origin plane must be a plane in the exported fit or solve"
                )
            p = np.asarray(
                selected_plane.get("plane_equation", selected_plane["parameters"]),
                dtype=float,
            )
            if len(p) == 7:
                normal = np.array([p[2], p[3], 1.0])
                normal /= np.linalg.norm(normal)
                distance = p[5]
            else:
                normal, distance = p[:3], p[3]
            denominator = float(normal @ axis)
            if abs(denominator) <= 1e-10 * np.linalg.norm(normal) * np.linalg.norm(
                axis
            ):
                raise ValueError(
                    "origin plane is parallel to the axis; no unique intersection"
                )
            anchor = anchor + axis * ((distance - normal @ anchor) / denominator)
        transform = rhino.Transform.Rotation(
            vector(axis), vector([0, 0, 1]), point([0, 0, 0])
        )
        # Center the rotated axis on Z, preserving the rotated axial heights.
        rotation = np.array(
            [[getattr(transform, f"M{i}{j}") for j in range(3)] for i in range(3)]
        )
        rotated_anchor = rotation @ anchor
        transform.M03 = -float(rotated_anchor[0])
        transform.M13 = -float(rotated_anchor[1])
        if request.origin_plane is not None:
            transform.M23 = -float(rotated_anchor[2])
    else:
        transform = rhino.Transform.Identity()
        # Local fitting coordinates -> original source coordinates.
        for row in range(3):
            for col in range(3):
                setattr(transform, f"M{row}{col}", float(workspace.frame[row, col]))
            setattr(transform, f"M{row}3", float(workspace.origin[row]))
    model = rhino.File3dm()
    model.Settings.ModelUnitSystem = getattr(rhino.UnitSystem, request.units)
    model.Settings.ModelAbsoluteTolerance = 1e-7
    model.ApplicationName = "Scansor nozzle export experiment"
    model.Strings["scansor_recipe"] = json.dumps(snapshot["recipe"])
    model.Strings["scansor_export"] = json.dumps(request.model_dump())
    model.Strings["scansor_transform_local_to_export"] = json.dumps(
        [[getattr(transform, f"M{i}{j}") for j in range(4)] for i in range(4)]
    )
    for name, color in [
        ("Fitted surfaces", (70, 180, 230, 255)),
        ("Reference mesh", (160, 165, 175, 255)),
    ]:
        layer = rhino.Layer()
        layer.Name = name
        layer.Color = color
        _ = model.Layers.Add(layer)
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
        joint_breps(result, relationships, workspace.local)
        if node["operation"] in ("joint_fit", "axis_solve")
        else {
            id: surface_brep(surface, workspace.local)
            for id, surface in surfaces.items()
        }
    )
    for id, fitted in surfaces.items():
        brep = patches[id]
        if not brep.Transform(transform):
            raise ValueError("surface transform failed")
        attributes = rhino.ObjectAttributes()
        attributes.Name = nodes[id]["label"]
        attributes.LayerIndex = 0
        _ = attributes.SetUserString("scansor_fit_id", id)
        _ = attributes.SetUserString("scansor_surface_type", fitted["kind"])
        _ = model.Objects.AddBrep(brep, attributes)
    if request.include_mesh:
        mesh = rhino.Mesh()
        mesh.Vertices.UseDoublePrecisionVertices = True
        for xyz in workspace.local:
            _ = mesh.Vertices.AddPoint3d(*map(float, xyz))
        for triangle in workspace.data.triangles:
            _ = mesh.Faces.AddFace(*map(int, triangle))
        _ = mesh.Normals.ComputeNormals()
        if not mesh.IsValid or not mesh.Transform(transform):
            raise ValueError("reference mesh is invalid")
        attributes = rhino.ObjectAttributes()
        attributes.Name = "Original reference mesh"
        attributes.LayerIndex = 1
        _ = attributes.SetUserString(
            "scansor_source_sha256", workspace.default.source_sha256
        )
        _ = model.Objects.AddMesh(mesh, attributes)
    options = rhino.File3dmWriteOptions()
    options.Version = 8
    return base64.b64decode(cast(str, model.Encode(options)))
