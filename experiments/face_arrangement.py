"""Reviewed cells of native analytic-surface arrangements, never inferred solids.

Carriers are computation envelopes, not physical bounds. A cell touching an
envelope is preview-only. Replay selects by cutter signs, component multiplicity,
and an intrinsic chart witness rather than native enumeration order.
"""

from __future__ import annotations

import itertools
import json
import math
from collections import Counter
from copy import deepcopy
from typing import Any, final

import numpy as np
from numpy.typing import NDArray
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Splitter
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.collections import List_TopoDS_Shape
from OCP.GeomAbs import (
    GeomAbs_BSplineCurve,
    GeomAbs_Circle,
    GeomAbs_Ellipse,
    GeomAbs_Line,
)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt2d, gp_Vec2d
from OCP.GProp import GProp_GProps
from OCP.IntTools import IntTools_FClass2d
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_WIRE
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Edge, TopoDS_Face, TopoDS_Shape

from experiments.general_face_geometry import face_from_record as general_face
from experiments.general_face_geometry import intersection_record
from experiments.general_face_proposals import project_observations
from experiments.native_replay import cached_native_replay, native_replay_scope
from experiments.ocp_geometry import (
    checked_face,
    geometry_frame,
    kernel_operation,
    primitive_surface,
    tessellate_face,
    transform_shape,
)
from experiments.ocp_geometry import (
    face_from_record as circular_face,
)
from experiments.surface_primitives import basis, primitive

Array = NDArray[np.float64]
TOLERANCE = 1e-8


def _normalized(
    geometry: dict[str, Any], center: Array, scale: float
) -> dict[str, Any]:
    result = geometry.copy()
    if geometry["kind"] == "plane":
        result["offset"] = float(
            (geometry["offset"] - np.dot(geometry["axis"], center)) / scale
        )
        result["origin"] = (np.asarray(geometry["axis"]) * result["offset"]).tolist()
    else:
        axis = np.asarray(geometry["axis"])
        origin = np.asarray(geometry["origin"])
        shift = float((center - origin) @ axis)
        result["origin"] = ((origin + shift * axis - center) / scale).tolist()
        result["radius"] = float(
            (geometry["radius"] + geometry.get("slope", 0) * shift) / scale
        )
    u, v = basis(np.asarray(geometry["axis"]))
    result.setdefault("basis_u", u.tolist())
    result.setdefault("basis_v", v.tolist())
    return result


def _project(geometry: dict[str, Any], points: Array) -> Array:
    projected, defined = project_observations(
        {**geometry, "slope": geometry.get("slope", 0)}, points
    )
    projected[~defined] = np.nan
    return projected


def _implicit(geometry: dict[str, Any], point: Array) -> float:
    axis = np.asarray(geometry["axis"])
    if geometry["kind"] == "plane":
        return float(point @ axis - geometry["offset"])
    delta = point - geometry["origin"]
    axial = float(delta @ axis)
    radial = np.linalg.norm(delta - axial * axis)
    return float(radial - geometry["radius"] - geometry.get("slope", 0) * axial)


def _implicit_points(geometry: dict[str, Any], points: Array) -> Array:
    axis = np.asarray(geometry["axis"])
    if geometry["kind"] == "plane":
        return points @ axis - geometry["offset"]
    delta = points - geometry["origin"]
    axial = delta @ axis
    return (
        np.linalg.norm(delta - axial[:, None] * axis, axis=1)
        - geometry["radius"]
        - geometry.get("slope", 0) * axial
    )


def _gradient(geometry: dict[str, Any], point: Array) -> Array:
    axis = np.asarray(geometry["axis"])
    if geometry["kind"] == "plane":
        return axis.copy()
    delta = point - geometry["origin"]
    radial = delta - (delta @ axis) * axis
    return radial / np.linalg.norm(radial) - geometry.get("slope", 0) * axis


def _finite_edge_sides(
    face: TopoDS_Face,
    edge: TopoDS_Edge,
    surface: dict[str, Any],
    cutter: dict[str, Any],
) -> set[str]:
    """Sides incident to the actual finite edge, not its remote extension.

    Native containment decides the inward direction. If a segment cannot be
    distinguished at kernel precision, do not invent a replay identity.
    """
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    sides: set[str] = set()
    for fraction in (0.5, 0.25, 0.75):
        point = np.asarray(curve.Value(first + fraction * (last - first)).Coord())
        normal = _gradient(surface, point)
        normal /= np.linalg.norm(normal)
        across = _gradient(cutter, point)
        across -= (across @ normal) * normal
        length = float(np.linalg.norm(across))
        if not math.isfinite(length) or length <= TOLERANCE:
            continue
        across /= length
        # Descend toward the edge so narrow but representable regions are not
        # missed by a large fixed probing distance.
        for distance in (1e-4, 1e-5, 1e-6, 16 * TOLERANCE):
            probes = _project(
                surface,
                np.stack([point - distance * across, point + distance * across]),
            )
            states = [
                BRepClass_FaceClassifier(face, _pnt(probe), TOLERANCE).State()
                if np.isfinite(probe).all()
                else None
                for probe in probes
            ]
            if (states[0] == TopAbs_IN) != (states[1] == TopAbs_IN):
                probe = probes[0 if states[0] == TopAbs_IN else 1]
                value = _implicit(cutter, probe)
                if abs(value) > TOLERANCE:
                    sides.add("positive" if value > 0 else "negative")
                    break
    if not sides:
        raise ValueError(
            "finite boundary side is ambiguous at kernel precision; review the region"
        )
    return sides


def _chart(geometry: dict[str, Any], points: Array) -> Array:
    frame = geometry_frame(geometry)
    u, v = (
        np.asarray(frame.XDirection().Coord()),
        np.asarray(frame.YDirection().Coord()),
    )
    if geometry["kind"] == "plane":
        return np.column_stack([points @ u, points @ v])
    delta = points - geometry["origin"]
    return np.column_stack([np.arctan2(delta @ v, delta @ u), delta @ geometry["axis"]])


def _chart_point(geometry: dict[str, Any], chart: list[float]) -> Array:
    u, v = chart
    frame = geometry_frame(geometry)
    x, y, axis = (
        np.asarray(frame.XDirection().Coord()),
        np.asarray(frame.YDirection().Coord()),
        np.asarray(geometry["axis"]),
    )
    if geometry["kind"] == "plane":
        return axis * geometry["offset"] + u * x + v * y
    radius = geometry["radius"] + geometry.get("slope", 0) * v
    return (
        np.asarray(geometry["origin"])
        + v * axis
        + radius * (math.cos(u) * x + math.sin(u) * y)
    )


def _world_frame(geometry: dict[str, Any]) -> dict[str, Any]:
    if "axis" not in geometry:
        geometry = primitive(geometry)
    result = geometry.copy()
    if geometry["kind"] == "plane":
        result.setdefault(
            "origin", (np.asarray(geometry["axis"]) * geometry["offset"]).tolist()
        )
    u, v = basis(np.asarray(geometry["axis"]))
    result.setdefault("basis_u", u.tolist())
    result.setdefault("basis_v", v.tolist())
    return result


def _domain_shape(record: dict[str, Any]) -> TopoDS_Face:
    if "arrangement" in record["bounds"]:
        return face_from_record(record)
    if "loops" in record["bounds"] or "cuts" in record["bounds"]:
        return general_face(record)
    return circular_face(record)


def _local_record(
    record: dict[str, Any], center: Array, scale: float
) -> dict[str, Any]:
    """Recenter declared data instead of a lossy native world round trip."""
    local = deepcopy(record)
    geometry = local["geometry"]
    geometry["origin"] = ((np.asarray(geometry["origin"]) - center) / scale).tolist()
    if "offset" in geometry:
        geometry["offset"] = float(
            (geometry["offset"] - np.dot(geometry["axis"], center)) / scale
        )
    if "radius" in geometry:
        geometry["radius"] /= scale
    bounds = local["bounds"]
    if "arrangement" in bounds:
        # Arrangement snapshots already have a self-contained normalization.
        # Recenter that context and all current primitive/domain data together.
        intent = bounds["arrangement"]
        for primitive_geometry in [
            intent["surface"],
            *(c["geometry"] for c in intent["cutters"]),
        ]:
            if primitive_geometry["kind"] == "plane":
                primitive_geometry["offset"] = float(
                    (
                        primitive_geometry["offset"]
                        - np.dot(primitive_geometry["axis"], center)
                    )
                    / scale
                )
                primitive_geometry["origin"] = (
                    np.asarray(primitive_geometry["axis"])
                    * primitive_geometry["offset"]
                ).tolist()
            else:
                primitive_geometry["origin"] = (
                    (np.asarray(primitive_geometry["origin"]) - center) / scale
                ).tolist()
                primitive_geometry["radius"] /= scale
        context = intent["carrier"]
        context["center"] = ((np.asarray(context["center"]) - center) / scale).tolist()
        context["scale"] /= scale
        selector = intent["selector"]
        original = _world_frame(record["bounds"]["arrangement"]["surface"])
        if original["kind"] == "plane":
            frame = geometry_frame(original)
            shift = np.array(
                [
                    np.dot(center, frame.XDirection().Coord()),
                    np.dot(center, frame.YDirection().Coord()),
                ]
            )
            selector["witness_chart"] = (
                (np.asarray(selector["witness_chart"]) - shift) / scale
            ).tolist()
        else:
            selector["witness_chart"][1] /= scale
        intent["domains"] = [
            _local_record(domain, center, scale) for domain in intent["domains"]
        ]
        for cutter in intent["cutters"]:
            if "face_domains" in cutter:
                cutter["face_domains"] = [
                    _local_record(domain, center, scale)
                    for domain in cutter["face_domains"]
                ]
        local["region_identity"] = selector
        return local
    for key in ("radial", "axial"):
        if key in bounds:
            bounds[key] = [
                None if value is None else value / scale for value in bounds[key]
            ]
    for loop in bounds.get("loops", []):
        if loop["kind"] == "polygon":
            loop["positions"] = (
                (np.asarray(loop["positions"]) - center) / scale
            ).tolist()
        else:
            loop["center_display"] = (
                (np.asarray(loop["center_display"]) - center) / scale
            ).tolist()
            for key in ("radius", "major_radius", "minor_radius"):
                if key in loop:
                    loop[key] /= scale
    for cut in bounds.get("cuts", []):
        plane = cut["plane"]
        plane["offset"] = float(
            (plane["offset"] - np.dot(plane["axis"], center)) / scale
        )
    return local


def _local_domain(record: dict[str, Any], center: Array, scale: float) -> TopoDS_Face:
    if "arrangement" in record["bounds"]:
        if not record.get("bounded"):
            raise ValueError(
                "display-envelope boundaries are not physical caps; this arrangement cell cannot be exported"
            )
        # Replay in the declaration's own normalized frame; recentering the
        # intent for each consumer defeats exact-input reuse and rebuilds all
        # nested domains. Transform a copy directly into the consumer's frame.
        return _comparison_domain(record, center, scale)
    return _domain_shape(_local_record(record, center, scale))


def _shape_faces(shape: TopoDS_Shape) -> list[TopoDS_Face]:
    result: list[TopoDS_Face] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        if not any(face.IsSame(old) for old in result):
            result.append(face)
        explorer.Next()
    return result


def _edges(shape: TopoDS_Shape) -> list[TopoDS_Edge]:
    result: list[TopoDS_Edge] = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        if not any(edge.IsSame(old) for old in result):
            result.append(edge)
        explorer.Next()
    return result


def _box(shape: TopoDS_Shape) -> tuple[Array, Array]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box, False)
    return np.asarray(box.CornerMin().Coord()), np.asarray(box.CornerMax().Coord())


def _side_patch(geometry: dict[str, Any], lower: float, upper: float) -> TopoDS_Face:
    radius, slope = float(geometry["radius"]), float(geometry.get("slope", 0))
    reference, factor = 0.0, 1.0
    if geometry["kind"] == "cone" and slope:
        apex = -radius / slope
        if slope > 0:
            lower = max(lower, apex)
        else:
            upper = min(upper, apex)
        reference = apex if radius < 0 else 0.0
        factor = math.cos(math.atan(slope))
    if upper <= lower:
        raise ValueError("arrangement carrier is outside the positive-radius sheet")
    return checked_face(
        BRepBuilderAPI_MakeFace(
            primitive_surface(geometry),
            0,
            2 * math.pi,
            (lower - reference) / factor,
            (upper - reference) / factor,
            TOLERANCE,
        )
    )


def _tool(geometry: dict[str, Any], lower: Array, upper: Array) -> TopoDS_Face | None:
    if geometry["kind"] == "plane":
        return checked_face(
            BRepBuilderAPI_MakeFace(primitive_surface(geometry), TOLERANCE)
        )
    corners = np.array(
        [
            [x, y, z]
            for x in (lower[0], upper[0])
            for y in (lower[1], upper[1])
            for z in (lower[2], upper[2])
        ]
    )
    axial = (corners - geometry["origin"]) @ geometry["axis"]
    lo, hi = float(axial.min()) - 2, float(axial.max()) + 2
    radius, slope = geometry["radius"], geometry.get("slope", 0)
    if slope and max(radius + slope * lo, radius + slope * hi) <= 0:
        return None
    return _side_patch(geometry, lo, hi)


def _carrier_intent(
    surface: dict[str, Any],
    cutters: list[dict[str, Any]],
    domains: list[dict[str, Any]],
    observations: Array | None,
) -> dict[str, Any]:
    points: list[Array] = []
    if observations is not None and len(observations):
        points.append(np.asarray(observations, dtype=float))
    for domain in [
        *domains,
        *(domain for cutter in cutters for domain in cutter.get("face_domains", [])),
    ]:
        preview = domain.get("preview", {}).get("positions", [])
        if preview:
            points.append(np.asarray(preview, dtype=float).reshape(-1, 3))
        elif domain.get("bounded"):
            lo, hi = _box(_domain_shape(domain))
            points.append(np.stack([lo, hi]))
        else:
            geometry = domain["geometry"]
            origin, axis = np.asarray(geometry["origin"]), np.asarray(geometry["axis"])
            for bound in domain["bounds"].get(
                "radial", domain["bounds"].get("axial", [])
            ):
                if bound is None:
                    continue
                if domain["surface_kind"] == "plane":
                    points.append(
                        origin + abs(bound) * np.vstack([np.eye(3), -np.eye(3)])
                    )
                else:
                    radius = abs(geometry["radius"] + geometry.get("slope", 0) * bound)
                    points.append(
                        origin
                        + bound * axis
                        + radius * np.vstack([np.eye(3), -np.eye(3)])
                    )
    # Cutter-derived points enlarge a computational envelope, never author caps.
    planes = [surface] if surface["kind"] == "plane" else []
    planes.extend(c["geometry"] for c in cutters if c["geometry"]["kind"] == "plane")
    sides = [surface] if surface["kind"] != "plane" else []
    sides.extend(c["geometry"] for c in cutters if c["geometry"]["kind"] != "plane")
    for side in sides:
        origin = np.asarray(side["origin"])
        radius = abs(side["radius"])
        if radius > 0:
            points.append(origin + radius * np.vstack([np.eye(3), -np.eye(3)]))
    initial = np.concatenate(points) if points else None
    anchor = np.mean(initial, axis=0) if initial is not None else None
    context_radius = (
        float(np.max(np.linalg.norm(initial - anchor, axis=1)))
        if initial is not None and anchor is not None
        else 0.0
    )
    hints: list[Array] = []
    for side in sides:
        origin = np.asarray(side["origin"])
        radius = abs(side["radius"])
        for plane in planes:
            normal, axis = np.asarray(plane["axis"]), np.asarray(side["axis"])
            denominator = float(normal @ axis)
            if abs(denominator) > TOLERANCE:
                axial = (plane["offset"] - normal @ origin) / denominator
                middle = origin + axial * axis
                cut_radius = abs(side["radius"] + side.get("slope", 0) * axial)
                extent = max(
                    cut_radius / abs(denominator), radius, np.finfo(float).tiny
                )
                hints.append(middle + extent * np.vstack([np.eye(3), -np.eye(3)]))
            try:
                intersection = intersection_record(plane, side)
            except ValueError:
                continue  # Unavailable coverage hint, not an empty proof.
            curve = intersection["curves"][0]
            if curve["kind"] in ("circle", "ellipse"):
                middle, extent = (
                    np.asarray(curve["center_display"]),
                    curve["major_radius"],
                )
                hints.append(middle + extent * np.vstack([np.eye(3), -np.eye(3)]))
    for triple in itertools.combinations(planes, 3):
        matrix = np.asarray([p["axis"] for p in triple])
        if abs(np.linalg.det(matrix)) > TOLERANCE:
            hints.append(
                np.linalg.solve(matrix, [p["offset"] for p in triple])[None, :]
            )
    for hint in hints:
        # This is a finite DISPLAY neighborhood, not a global intersection
        # search. Remote near-parallel intersections must not inflate local
        # coordinates until the kernel loses all meaningful part features.
        if (
            anchor is None
            or context_radius == 0
            or np.max(np.linalg.norm(hint - anchor, axis=1)) <= 16 * context_radius
        ):
            points.append(hint)
    if not points:
        origin = (
            np.asarray(surface["axis"]) * surface["offset"]
            if surface["kind"] == "plane"
            else np.asarray(surface["origin"])
        )
        points.append(origin + np.vstack([np.eye(3), -np.eye(3)]))
    support = np.concatenate(points)
    if not np.isfinite(support).all():
        raise ValueError("arrangement observations and domain coverage must be finite")
    center = np.mean(support, axis=0)
    axis = np.asarray(surface["axis"])
    if surface["kind"] == "plane":
        center -= (center @ axis - surface["offset"]) * axis
    else:
        origin = np.asarray(surface["origin"])
        center = origin + ((center - origin) @ axis) * axis
    scale = float(
        max(
            np.max(np.linalg.norm(support - center, axis=1)),
            abs(surface.get("radius", 0)),
        )
    )
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("arrangement coverage has no representable positive scale")
    normalized = _normalized(surface, center, scale)
    projected = _project(normalized, (support - center) / scale)
    valid = projected[np.isfinite(projected).all(axis=1)]
    if len(valid) == 0:
        raise ValueError(
            "arrangement coverage cannot be projected onto its physical surface"
        )
    chart = _chart(normalized, valid)
    if surface["kind"] == "plane":
        lower, upper = chart.min(axis=0), chart.max(axis=0)
        padding = np.maximum((upper - lower) * 0.5, 0.25)
        envelope = {
            "u": [float(lower[0] - padding[0]), float(upper[0] + padding[0])],
            "v": [float(lower[1] - padding[1]), float(upper[1] + padding[1])],
        }
    else:
        lower, upper = float(chart[:, 1].min()), float(chart[:, 1].max())
        padding = max((upper - lower) * 0.5, 0.25)
        envelope = {"u": [0.0, 2 * math.pi], "v": [lower - padding, upper + padding]}
    return {"center": center.tolist(), "scale": scale, "envelope": envelope}


def _carrier(surface: dict[str, Any], carrier: dict[str, Any]) -> TopoDS_Face:
    envelope = carrier["envelope"]
    if surface["kind"] == "plane":
        return checked_face(
            BRepBuilderAPI_MakeFace(
                primitive_surface(surface),
                float(envelope["u"][0]),
                float(envelope["u"][1]),
                float(envelope["v"][0]),
                float(envelope["v"][1]),
                TOLERANCE,
            )
        )
    return _side_patch(surface, *envelope["v"])


def _witness(
    surface: dict[str, Any], face: TopoDS_Face
) -> tuple[Array, dict[str, Any]]:
    lower, upper = _box(face)
    cell_scale = float(np.linalg.norm(upper - lower))
    if not math.isfinite(cell_scale) or cell_scale <= 0:
        raise ValueError("arrangement cell has no representable geometric coverage")
    preview = tessellate_face(face, cell_scale)
    points = np.asarray(preview["positions"]).reshape(-1, 3)
    triangles = np.asarray(preview["indices"]).reshape(-1, 3)
    vertices = points[triangles]
    areas = np.linalg.norm(
        np.cross(vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]),
        axis=1,
    )
    for index in np.argsort(areas)[::-1]:
        point = _project(surface, np.mean(vertices[index], axis=0)[None, :])[0]
        if (
            np.isfinite(point).all()
            and BRepClass_FaceClassifier(face, _pnt(point), TOLERANCE).State()
            == TopAbs_IN
        ):
            return point, preview
    return _native_witness(face), preview


def _native_witness(face: TopoDS_Face) -> Array:
    """Classify exact split fragments without requiring a display mesh."""
    native = BRep_Tool.Surface_s(face)
    bounds = BRepTools.UVBounds_s(face)
    u0, u1, v0, v1 = bounds
    span = max(u1 - u0, v1 - v0)
    tolerance = 64 * np.finfo(float).eps * max(1, *(abs(value) for value in bounds))
    # Classify in the intrinsic chart. BRepClass scales a 3D tolerance through
    # the surface resolution and can report every point of a thin cylindrical
    # strip as ON, even when the supplied tolerance is zero.
    classifier = IntTools_FClass2d(face, tolerance)
    candidates = [gp_Pnt2d((u0 + u1) / 2, (v0 + v1) / 2)]
    properties = GProp_GProps()
    _ = BRepGProp.SurfaceProperties_s(face, properties, 1e-10, False)
    projection = GeomAPI_ProjectPointOnSurf(properties.CentreOfMass(), native)
    if projection.IsDone() and projection.NbPoints():
        candidates.append(gp_Pnt2d(*projection.LowerDistanceParameters()))
    for edge in _edges(face):
        if BRep_Tool.Degenerated_s(edge):
            continue
        curve = BRepAdaptor_Curve2d(edge, face)
        for fraction in (0.25, 0.5, 0.75):
            parameter = curve.FirstParameter() + fraction * (
                curve.LastParameter() - curve.FirstParameter()
            )
            point, tangent = gp_Pnt2d(), gp_Vec2d()
            curve.D1(parameter, point, tangent)
            normal = np.array([-tangent.Y(), tangent.X()])
            length = float(np.linalg.norm(normal))
            if length == 0:
                continue
            normal /= length
            for distance in (1e-3, 1e-6, 1e-9, 1e-12):
                for sign in (-1, 1):
                    xy = np.asarray(point.Coord()) + sign * span * distance * normal
                    candidates.append(gp_Pnt2d(*xy))
    for candidate in candidates:
        if classifier.Perform(candidate) == TopAbs_IN:
            point = np.asarray(native.Value(candidate.X(), candidate.Y()).Coord())
            return point
    for resolution in (9, 17, 33):
        for u in np.linspace(u0, u1, resolution)[1:-1]:
            for v in np.linspace(v0, v1, resolution)[1:-1]:
                if classifier.Perform(gp_Pnt2d(float(u), float(v))) == TopAbs_IN:
                    return np.asarray(native.Value(float(u), float(v)).Coord())
    raise ValueError("arrangement cell has no unambiguous interior witness")


def _strictly_inside(face: TopoDS_Face, point: Array) -> bool:
    surface = BRep_Tool.Surface_s(face)
    projection = GeomAPI_ProjectPointOnSurf(_pnt(point), surface)
    if not projection.IsDone() or not projection.NbPoints():
        return False
    uv = projection.LowerDistanceParameters()
    tolerance = 64 * np.finfo(float).eps * max(1, *(abs(value) for value in uv))
    return IntTools_FClass2d(face, tolerance).Perform(gp_Pnt2d(*uv)) == TopAbs_IN


def _pnt(point: Array):
    from OCP.gp import gp_Pnt

    return gp_Pnt(*point)


def _signature(cutters: list[dict[str, Any]], point: Array) -> dict[str, str]:
    signs = {}
    for cutter in cutters:
        value = _implicit(cutter["geometry"], point)
        geometry = cutter["geometry"]
        magnitude = max(
            1,
            float(np.max(np.abs(point))),
            abs(geometry.get("offset", 0)),
            abs(geometry.get("radius", 0)),
            *(abs(float(value)) for value in geometry.get("origin", [])),
        )
        if abs(value) <= 64 * np.finfo(float).eps * magnitude:
            raise ValueError(
                "coincident or near-degenerate cutters do not identify an unambiguous arrangement cell"
            )
        signs[cutter["key"]] = "positive" if value > 0 else "negative"
    return signs


def _signature_key(signs: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(signs.items()))


def _edge_descriptor(edge: TopoDS_Edge, center: Array, scale: float) -> dict[str, Any]:
    if BRep_Tool.Degenerated_s(edge):
        vertex = TopExp.FirstVertex_s(edge)
        point = np.asarray(BRep_Tool.Pnt_s(vertex).Coord()) * scale + center
        return {
            "kind": "point",
            "position": point.tolist(),
            "closed": True,
            "orientation": str(edge.Orientation()),
            "preview": {"positions": point.tolist(), "indices": []},
        }
    curve = BRepAdaptor_Curve(edge)
    first, last = curve.FirstParameter(), curve.LastParameter()
    record: dict[str, Any] = {
        "range": [first, last],
        "orientation": str(edge.Orientation()),
        "closed": edge.Closed(),
    }
    kind = curve.GetType()
    if kind == GeomAbs_Line:
        line = curve.Line()
        record.update(
            kind="line",
            range=[first * scale, last * scale],
            origin=(np.asarray(line.Location().Coord()) * scale + center).tolist(),
            direction=list(line.Direction().Coord()),
        )
    elif kind in (GeomAbs_Circle, GeomAbs_Ellipse):
        if kind == GeomAbs_Circle:
            conic = curve.Circle()
            major = minor = conic.Radius()
        else:
            conic = curve.Ellipse()
            major, minor = conic.MajorRadius(), conic.MinorRadius()
        frame = conic.Position()
        record.update(
            kind="circle" if kind == GeomAbs_Circle else "ellipse",
            center=(np.asarray(conic.Location().Coord()) * scale + center).tolist(),
            axis=list(frame.Direction().Coord()),
            basis_u=list(frame.XDirection().Coord()),
            basis_v=list(frame.YDirection().Coord()),
            major_radius=major * scale,
            minor_radius=minor * scale,
        )
    elif kind == GeomAbs_BSplineCurve:
        spline = curve.BSpline()
        record.update(
            kind="bspline",
            degree=spline.Degree(),
            periodic=spline.IsPeriodic(),
            poles=[
                (np.asarray(spline.Pole(i).Coord()) * scale + center).tolist()
                for i in range(1, spline.NbPoles() + 1)
            ],
            weights=[spline.Weight(i) for i in range(1, spline.NbPoles() + 1)],
            knots=[spline.Knot(i) for i in range(1, spline.NbKnots() + 1)],
            multiplicities=[
                spline.Multiplicity(i) for i in range(1, spline.NbKnots() + 1)
            ],
        )
    else:
        record.update(kind=str(kind))
    samples = [curve.Value(first + (last - first) * i / 32) for i in range(33)]
    record["preview"] = {
        "positions": (np.array([p.Coord() for p in samples]) * scale + center)
        .ravel()
        .tolist(),
        "indices": [],
    }
    return record


def _transport(history: Any, provenance: list[tuple[str, list[TopoDS_Edge]]]) -> None:
    for _, descendants in provenance:
        descendants.extend(
            TopoDS.Edge(shape)
            for edge in list(descendants)
            for shape in history.Modified(edge)
            if shape.ShapeType() == TopAbs_EDGE
        )


def _scoped_carriers(
    surface: dict[str, Any],
    carrier: dict[str, Any],
    domains: list[dict[str, Any]],
    center: Array,
    scale: float,
) -> tuple[list[TopoDS_Face], list[tuple[str, list[TopoDS_Edge]]]]:
    base = _carrier(surface, carrier)
    if not domains:
        return [base], [
            ("display-envelope", [edge])
            for edge in _edges(base)
            if not BRep_Tool.IsClosed_s(edge, base)
        ]
    result: list[TopoDS_Face] = []
    provenance: list[tuple[str, list[TopoDS_Edge]]] = []
    for index, domain in enumerate(domains):
        label = f"domain:{index}"
        if domain.get("bounded"):
            face = _local_domain(domain, center, scale)
            result.append(face)
            provenance.extend(
                (label, [edge])
                for edge in _edges(face)
                if not BRep_Tool.IsClosed_s(edge, face)
            )
            continue
        faces = [base]
        sources = [
            ("display-envelope", [edge])
            for edge in _edges(base)
            if not BRep_Tool.IsClosed_s(edge, base)
        ]
        geometry = domain["geometry"]
        if domain["surface_kind"] == "plane":
            interval = domain["bounds"]["radial"]
            for bound, remove in ((interval[0], True), (interval[1], False)):
                if bound is None or (remove and bound == 0):
                    continue
                if not math.isfinite(bound) or bound <= 0:
                    raise ValueError("open planar scope has an invalid radial boundary")
                mask_record = {
                    "bounded": True,
                    "surface_kind": "plane",
                    "geometry": geometry,
                    "bounds": {"radial": [0, bound]},
                }
                mask = _local_domain(mask_record, center, scale)
                retained: list[TopoDS_Face] = []
                for face in faces:
                    operation = (
                        BRepAlgoAPI_Cut(face, mask)
                        if remove
                        else BRepAlgoAPI_Common(face, mask)
                    )
                    if (
                        not operation.IsDone()
                        or not BRepCheck_Analyzer(operation.Shape()).IsValid()
                    ):
                        raise ValueError("native open radial scope clipping failed")
                    _transport(operation, sources)
                    edges = _edges(mask)
                    descendants = [
                        *edges,
                        *(
                            TopoDS.Edge(shape)
                            for edge in edges
                            for shape in operation.Modified(edge)
                            if shape.ShapeType() == TopAbs_EDGE
                        ),
                    ]
                    sources.append((label, descendants))
                    retained.extend(_shape_faces(operation.Shape()))
                faces = retained
        else:
            interval = domain["bounds"]["axial"]
            axis = np.asarray(geometry["axis"])
            for bound, sign in ((interval[0], 1), (interval[1], -1)):
                if bound is None:
                    continue
                if not math.isfinite(bound):
                    raise ValueError("open lateral scope has an invalid axial boundary")
                offset = float(axis @ geometry["origin"] + bound)
                plane = _normalized(
                    {"kind": "plane", "axis": axis.tolist(), "offset": offset},
                    center,
                    scale,
                )
                tool = checked_face(
                    BRepBuilderAPI_MakeFace(primitive_surface(plane), TOLERANCE)
                )
                retained = []
                for face in faces:
                    objects, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
                    _ = objects.Append(face)
                    _ = tools.Append(tool)
                    operation = BRepAlgoAPI_Splitter()
                    operation.SetArguments(objects)
                    operation.SetTools(tools)
                    operation.Build()
                    if (
                        not operation.IsDone()
                        or not BRepCheck_Analyzer(operation.Shape()).IsValid()
                    ):
                        raise ValueError("native open axial scope clipping failed")
                    _transport(operation, sources)
                    sources.append(
                        (
                            label,
                            [
                                TopoDS.Edge(shape)
                                for shape in operation.Generated(tool)
                                if shape.ShapeType() == TopAbs_EDGE
                            ],
                        )
                    )
                    for piece in _shape_faces(operation.Shape()):
                        point, _ = _witness(surface, piece)
                        if sign * _implicit(plane, point) > TOLERANCE:
                            retained.append(piece)
                faces = retained
        result.extend(faces)
        provenance.extend(sources)
    return result, provenance


def _split(
    intent: dict[str, Any],
) -> tuple[list[dict[str, Any]], Array, float, dict[str, Any]]:
    carrier = intent["carrier"]
    center, scale = np.asarray(carrier["center"]), float(carrier["scale"])
    surface = _normalized(intent["surface"], center, scale)
    cutters: list[dict[str, Any]] = [
        {"key": c["key"], "geometry": _normalized(c["geometry"], center, scale)}
        for c in intent["cutters"]
        if "face_domains" not in c
    ]
    finite_geometry = {
        c["key"]: _normalized(c["geometry"], center, scale)
        for c in intent["cutters"]
        if "face_domains" in c
    }
    if any(
        geometry["kind"] == "cylinder" and geometry["radius"] <= 100 * TOLERANCE
        for geometry in [surface, *(c["geometry"] for c in cutters)]
    ):
        raise ValueError(
            "arrangement display context would erase a cylindrical feature at kernel precision; provide a tighter physical scope"
        )
    domains = intent["domains"]
    carriers, provenance = _scoped_carriers(surface, carrier, domains, center, scale)
    if not carriers:
        return [], center, scale, surface
    lo = np.min(np.stack([_box(face)[0] for face in carriers]), axis=0)
    hi = np.max(np.stack([_box(face)[1] for face in carriers]), axis=0)
    native_tools: list[tuple[str, TopoDS_Face]] = []
    for cutter in cutters:
        tool = _tool(cutter["geometry"], lo, hi)
        if tool is not None:
            native_tools.append((cutter["key"], tool))
    # History transports original envelope/domain edges through all splits.
    cutter_provenance: list[tuple[str, list[TopoDS_Edge]]] = []
    # Apply every cutter, but avoid one fragile all-tools Boolean operation:
    # near-parallel analytic surfaces can create remote tool/tool intersections
    # that are irrelevant to the finite carriers and destabilize native BOP.
    # The first no-tool stage unions overlapping physical carrier scopes.
    current_faces = carriers
    for key, tool in [("", None), *native_tools]:
        if tool is None and len(current_faces) == 1:
            continue
        objects, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        for face in current_faces:
            _ = objects.Append(face)
        if tool is not None:
            _ = tools.Append(tool)
        splitter = BRepAlgoAPI_Splitter()
        splitter.SetArguments(objects)
        splitter.SetTools(tools)
        splitter.Build()
        if not splitter.IsDone() or not BRepCheck_Analyzer(splitter.Shape()).IsValid():
            raise ValueError(
                "native surface arrangement failed or produced invalid topology"
            )
        for _, descendants in [*provenance, *cutter_provenance]:
            descendants.extend(
                TopoDS.Edge(shape)
                for edge in list(descendants)
                for shape in splitter.Modified(edge)
                if shape.ShapeType() == TopAbs_EDGE
            )
        if tool is not None:
            cutter_provenance.append(
                (
                    key,
                    [
                        TopoDS.Edge(shape)
                        for shape in splitter.Generated(tool)
                        if shape.ShapeType() == TopAbs_EDGE
                    ],
                )
            )
        current_faces = _shape_faces(splitter.Shape())
    # Periodic seams and overlapping domain scopes can subdivide one connected
    # cell into several native faces. Unify only faces with the same semantic
    # cutter signature, never across a reviewed cutter boundary.
    groups: dict[tuple[tuple[str, str], ...], list[TopoDS_Face]] = {}
    for face in current_faces:
        point = _native_witness(face)
        groups.setdefault(_signature_key(_signature(cutters, point)), []).append(face)
    region_faces: list[TopoDS_Face] = []
    for faces in groups.values():
        if len(faces) == 1:
            region_faces.extend(faces)
            continue
        compound = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(compound)
        for face in faces:
            builder.Add(compound, face)
        unify = ShapeUpgrade_UnifySameDomain(compound, False, True, False)
        unify.Build()
        result = unify.Shape()
        if not BRepCheck_Analyzer(result).IsValid():
            raise ValueError(
                "arrangement components have ambiguous touching or invalid merged topology"
            )
        region_faces.extend(_shape_faces(result))
        for _, descendants in [*provenance, *cutter_provenance]:
            descendants.extend(
                TopoDS.Edge(shape)
                for edge in list(descendants)
                for shape in unify.History().Modified(edge)
                if shape.ShapeType() == TopAbs_EDGE
            )
    # Finite neighboring faces contribute only their physical intersection
    # segments. Apply them together: individual open arcs may only separate a
    # region after their endpoints join other approved segments. Their cells
    # are not infinite halfspaces and must never be unified by cutter signs.
    finite_tools = [
        (cutter["key"], _local_domain(domain, center, scale))
        for cutter in intent["cutters"]
        for domain in cutter.get("face_domains", [])
    ]
    if finite_tools:
        objects, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
        for face in region_faces:
            _ = objects.Append(face)
        for _, tool in finite_tools:
            _ = tools.Append(tool)
        splitter = BRepAlgoAPI_Splitter()
        splitter.SetArguments(objects)
        splitter.SetTools(tools)
        splitter.Build()
        if not splitter.IsDone() or not BRepCheck_Analyzer(splitter.Shape()).IsValid():
            raise ValueError("native finite boundary arrangement failed")
        _transport(splitter, [*provenance, *cutter_provenance])
        for key, tool in finite_tools:
            edges = _edges(tool)
            cutter_provenance.append(
                (
                    key,
                    [
                        *edges,
                        *(
                            TopoDS.Edge(shape)
                            for edge in edges
                            for shape in splitter.Modified(edge)
                            if shape.ShapeType() == TopAbs_EDGE
                        ),
                        *(
                            TopoDS.Edge(shape)
                            for shape in splitter.Generated(tool)
                            if shape.ShapeType() == TopAbs_EDGE
                        ),
                    ],
                )
            )
        region_faces = _shape_faces(splitter.Shape())
    cells: list[dict[str, Any]] = []
    projected = _project(
        surface,
        (
            np.asarray(intent.get("observations", []), dtype=float).reshape(-1, 3)
            - center
        )
        / scale,
    )
    for face in region_faces:
        point, preview = _witness(surface, face)
        signs = _signature(cutters, point)
        loops: list[dict[str, Any]] = []
        boundary_keys: set[str] = set()
        finite_sides: dict[str, set[str]] = {}
        artificial = False
        wires = TopExp_Explorer(face, TopAbs_WIRE)
        while wires.More():
            wire = TopoDS.Wire(wires.Current())
            edges = BRepTools_WireExplorer(wire, face)
            loop: list[dict[str, Any]] = []
            while edges.More():
                edge = edges.Current()
                seam = BRep_Tool.IsClosed_s(edge, face)
                labels = [
                    label
                    for label, descendants in provenance
                    if any(edge.IsSame(old) for old in descendants)
                ]
                artificial |= "display-envelope" in labels
                descriptor = _edge_descriptor(edge, center, scale)
                keys = (
                    [
                        key
                        for key, descendants in cutter_provenance
                        if any(edge.IsSame(old) for old in descendants)
                    ]
                    if not seam
                    else []
                )
                boundary_keys.update(keys)
                for key in keys:
                    if key in finite_geometry:
                        finite_sides.setdefault(key, set()).update(
                            _finite_edge_sides(
                                face, edge, surface, finite_geometry[key]
                            )
                        )
                descriptor.update(
                    sources=keys,
                    domain_sources=labels,
                    artificial="display-envelope" in labels,
                    seam=seam,
                )
                loop.append(descriptor)
                edges.Next()
            loops.append({"closed": BRep_Tool.IsClosed_s(wire), "edges": loop})
            wires.Next()
        values = (
            np.column_stack(
                [
                    _implicit_points(cutter["geometry"], projected)
                    * (1 if signs[cutter["key"]] == "positive" else -1)
                    for cutter in cutters
                ]
            )
            if cutters
            else np.empty((len(projected), 0))
        )
        strict = (values > TOLERANCE).all(axis=1)
        closure = (values >= -TOLERANCE).all(axis=1)
        on_cut = (np.abs(values) <= TOLERANCE).any(axis=1)
        interior = [
            bool(
                np.isfinite(p).all()
                and strict[i]
                and BRepClass_FaceClassifier(face, _pnt(p), TOLERANCE).State()
                == TopAbs_IN
            )
            for i, p in enumerate(projected)
        ]
        boundary = [
            bool(
                np.isfinite(p).all()
                and closure[i]
                and (
                    BRepClass_FaceClassifier(face, _pnt(p), TOLERANCE).State()
                    == TopAbs_ON
                    or (
                        on_cut[i]
                        and BRepClass_FaceClassifier(face, _pnt(p), TOLERANCE).State()
                        == TopAbs_IN
                    )
                )
            )
            for i, p in enumerate(projected)
        ]
        cells.append(
            {
                "face": face,
                "point": point,
                "signs": signs,
                "finite_sides": {
                    key: sorted(sides) for key, sides in sorted(finite_sides.items())
                },
                "bounded": not artificial,
                "preview": preview,
                "loops": loops,
                "boundary_keys": sorted(boundary_keys),
                "interior": interior,
                "boundary": boundary,
                "defined": np.isfinite(projected).all(axis=1).tolist(),
            }
        )

    def identity(cell: dict[str, Any]) -> tuple[Any, ...]:
        return (
            _signature_key(cell["signs"]),
            tuple((key, tuple(sides)) for key, sides in cell["finite_sides"].items()),
        )

    counts = Counter(identity(cell) for cell in cells)
    if cells and np.any(np.sum([cell["interior"] for cell in cells], axis=0) > 1):
        raise ValueError(
            "native arrangement cells overlap in their interior evidence; review the ambiguous topology"
        )
    for cell in cells:
        cell["component_count"] = counts[identity(cell)]
    return cells, center, scale, surface


def _record(
    intent: dict[str, Any], cell: dict[str, Any], center: Array, scale: float
) -> dict[str, Any]:
    preview = cell["preview"].copy()
    preview["positions"] = (
        (np.asarray(preview["positions"]).reshape(-1, 3) * scale + center)
        .ravel()
        .tolist()
    )
    point = cell["point"] * scale + center
    geometry = _world_frame(intent["surface"])
    selector = {
        "signs": cell["signs"],
        "component_count": cell["component_count"],
        "witness_chart": _chart(geometry, point[None, :])[0].tolist(),
    }
    if cell["finite_sides"]:
        selector["finite_sides"] = cell["finite_sides"]
    stored_intent = {**intent, "selector": selector}
    _ = stored_intent.pop("observations", None)
    return {
        "kind": "trimmed_face",
        "surface_kind": geometry["kind"],
        "geometry": geometry,
        "region_identity": selector,
        "bounded": cell["bounded"],
        "preview_clipped": not cell["bounded"],
        "preview": preview,
        "bounds": {"arrangement": stored_intent, "loops": cell["loops"]},
        "boundary_keys": cell["boundary_keys"],
        "loops": cell["loops"],
        "evidence": {
            "interior": cell["interior"],
            "boundary": cell["boundary"],
            "defined": cell["defined"],
        },
        "boundary_ids": cell["boundary_keys"],
        "boundary_uses": [],
    }


def _intent(
    surface: dict[str, Any],
    cutters: list[dict[str, Any]],
    domains: list[dict[str, Any]] | None,
    observations: Array | None,
    coverage: Array | None = None,
) -> dict[str, Any]:
    surface = _world_frame(surface)
    cutters = [{**c, "geometry": _world_frame(c["geometry"])} for c in cutters]
    keys = [c["key"] for c in cutters]
    if len(set(keys)) != len(keys):
        raise ValueError("arrangement cutter keys must be unique")
    for cutter in cutters:
        if "face_domains" in cutter and (
            not cutter["face_domains"]
            or any(not domain.get("bounded") for domain in cutter["face_domains"])
        ):
            raise ValueError(
                "finite cutter domains must be nonempty bounded physical faces"
            )
    physical = domains or []
    if any(
        not domain.get("bounded")
        and not ("radial" in domain["bounds"] or "axial" in domain["bounds"])
        for domain in physical
    ):
        raise ValueError(
            "unbounded arrangement scopes need explicit physical closing bounds; only declared open axial/radial intervals can currently be reused"
        )
    return {
        "surface": _world_frame(surface),
        "cutters": cutters,
        "domains": physical,
        "carrier": _carrier_intent(
            surface,
            cutters,
            physical,
            coverage if coverage is not None else observations,
        ),
        "observations": observations.tolist() if observations is not None else [],
    }


@kernel_operation
def arrange_faces(
    surface: dict[str, Any],
    cutters: list[dict[str, Any]],
    domains: list[dict[str, Any]] | None = None,
    observations: Array | None = None,
    coverage: Array | None = None,
) -> list[dict[str, Any]]:
    return prepare_faces(surface, cutters, domains, observations, coverage).records()


def _select_cell(
    intent: dict[str, Any],
    selector: dict[str, Any],
    cells: list[dict[str, Any]],
    center: Array,
    scale: float,
    surface: dict[str, Any],
) -> dict[str, Any]:
    candidates = [
        cell
        for cell in cells
        if cell["signs"] == selector["signs"]
        and cell["finite_sides"] == selector.get("finite_sides", {})
    ]
    if len(candidates) != selector["component_count"]:
        raise ValueError(
            "arrangement components changed or became ambiguous; review the region again"
        )
    world = _chart_point(_world_frame(intent["surface"]), selector["witness_chart"])
    point = _project(surface, ((world - center) / scale)[None, :])[0]
    current_cutters = [
        {
            "key": cutter["key"],
            "geometry": _normalized(cutter["geometry"], center, scale),
        }
        for cutter in intent["cutters"]
        if "face_domains" not in cutter
    ]
    if (
        not np.isfinite(point).all()
        or _signature(current_cutters, point) != selector["signs"]
    ):
        raise ValueError(
            "arrangement witness crossed a cutter boundary; review the region again"
        )
    selected = [
        cell
        for cell in candidates
        if np.isfinite(point).all() and _strictly_inside(cell["face"], point)
    ]
    if len(selected) != 1:
        raise ValueError(
            "arrangement witness crossed a boundary or no longer selects one component; review the region again"
        )
    return selected[0]


def _replay_split(
    intent: dict[str, Any],
) -> tuple[list[dict[str, Any]], Array, float, dict[str, Any]]:
    # Sibling selectors share topology, but nested physical-domain selectors,
    # geometry, computation carriers and evidence remain exact cache inputs.
    key = json.dumps(
        {key: value for key, value in intent.items() if key != "selector"},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return cached_native_replay(("arrangement", key), lambda: _split(intent))


def _selected(
    intent: dict[str, Any], selector: dict[str, Any]
) -> tuple[dict[str, Any], Array, float]:
    cells, center, scale, surface = _replay_split(intent)
    return _select_cell(intent, selector, cells, center, scale, surface), center, scale


@final
class Arrangement:
    """Native cells scoped to one evaluation epoch, never a persisted cache."""

    def __init__(self, intent: dict[str, Any]):
        self._intent = deepcopy(intent)
        self._cells, self._center, self._scale, self._surface = _replay_split(
            self._intent
        )

    @kernel_operation
    def records(self) -> list[dict[str, Any]]:
        return [
            deepcopy(_record(self._intent, cell, self._center, self._scale))
            for cell in self._cells
        ]

    @kernel_operation
    def select(self, selector: dict[str, Any]) -> dict[str, Any]:
        cell = _select_cell(
            self._intent,
            selector,
            self._cells,
            self._center,
            self._scale,
            self._surface,
        )
        result = _record(self._intent, cell, self._center, self._scale)
        result["region_identity"] = selector
        result["bounds"]["arrangement"]["selector"] = selector
        return deepcopy(result)


@kernel_operation
@native_replay_scope()
def prepare_faces(
    surface: dict[str, Any],
    cutters: list[dict[str, Any]],
    domains: list[dict[str, Any]] | None = None,
    observations: Array | None = None,
    coverage: Array | None = None,
) -> Arrangement:
    return Arrangement(_intent(surface, cutters, domains, observations, coverage))


@kernel_operation
@native_replay_scope()
def select_face(
    surface: dict[str, Any],
    cutters: list[dict[str, Any]],
    selector: dict[str, Any],
    domains: list[dict[str, Any]] | None = None,
    observations: Array | None = None,
    coverage: Array | None = None,
) -> dict[str, Any]:
    return prepare_faces(surface, cutters, domains, observations, coverage).select(
        selector
    )


@kernel_operation
@native_replay_scope()
def face_from_record(record: dict[str, Any]) -> TopoDS_Face:
    if not record.get("bounded"):
        raise ValueError(
            "display-envelope boundaries are not physical caps; this arrangement cell cannot be exported"
        )
    intent = record["bounds"]["arrangement"]
    cell, center, scale = _selected(intent, intent["selector"])
    if not cell["bounded"]:
        raise ValueError(
            "arrangement replay touches a display envelope; explicit closing bounds are required"
        )
    transform = np.eye(4)
    transform[:3, :3] *= scale
    transform[:3, 3] = center
    return TopoDS.Face(transform_shape(cell["face"], transform))


def _comparison_domain(
    record: dict[str, Any], center: Array, scale: float
) -> TopoDS_Face:
    if "arrangement" not in record["bounds"]:
        # Manual faces still need declaration-level recentering before native
        # construction, not a lossy round trip through large world coordinates.
        return _local_domain(record, center, scale)
    intent = record["bounds"]["arrangement"]
    cell, native_center, native_scale = _selected(intent, intent["selector"])
    if not cell["bounded"]:
        raise ValueError(
            "arrangement replay touches a display envelope; explicit closing bounds are required"
        )
    # Replay in the arrangement's own well-conditioned frame, then copy into
    # the comparison frame directly. Rewriting the declared arrangement for
    # every pair would repeat all its splits (including finite source faces).
    # Never hand cached topology to a potentially destructive Boolean operation.
    transform = np.eye(4)
    transform[:3, :3] *= native_scale / scale
    transform[:3, 3] = (native_center - center) / scale
    return TopoDS.Face(transform_shape(cell["face"], transform))


@kernel_operation
@native_replay_scope()
def same_region(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Prove finite region equality without absorbing small physical features."""
    if not first.get("bounded") or not second.get("bounded"):
        return False
    faces = [_domain_shape(record) for record in (first, second)]
    lower = np.min(np.stack([_box(face)[0] for face in faces]), axis=0)
    upper = np.max(np.stack([_box(face)[1] for face in faces]), axis=0)
    scale = float(np.linalg.norm(upper - lower))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("region comparison has no representable positive scale")
    normalized = [
        _comparison_domain(record, (lower + upper) / 2, scale)
        for record in (first, second)
    ]
    # A relative area comparison can erase a real small hole or notch. Even if
    # the Boolean kernel suppresses a sub-tolerance difference, different wire
    # topology cannot establish equality. Fail closed rather than fill a hole.
    wire_counts: list[int] = []
    for face in normalized:
        explorer = TopExp_Explorer(face, TopAbs_WIRE)
        count = 0
        while explorer.More():
            count += 1
            explorer.Next()
        wire_counts.append(count)
    if wire_counts[0] != wire_counts[1]:
        return False
    for left, right in (normalized, normalized[::-1]):
        difference = BRepAlgoAPI_Cut(left, right)
        if (
            not difference.IsDone()
            or not BRepCheck_Analyzer(difference.Shape()).IsValid()
        ):
            raise ValueError(
                "native region comparison failed or produced invalid topology"
            )
        # Every native face is a material difference, however small. Edges and
        # vertices alone do not change the two-dimensional retained region.
        if _shape_faces(difference.Shape()):
            return False
    return True
