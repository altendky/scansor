"""Exact reviewed analytic boundaries, without inferred caps or solid assembly.

JSON curve descriptors are authoritative inputs; kernel topology is reconstructed.
This slice supports single-branch circles/ellipses, planar convex line loops, and
bounded cylindrical regions cut by planes. Other branches fail explicitly.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass import BRepClass_FaceClassifier
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.collections import List_TopoDS_Shape
from OCP.Geom import Geom_Circle, Geom_Ellipse, Geom_Line, Geom_TrimmedCurve
from OCP.GeomAPI import GeomAPI_IntSS
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Elips, gp_Pln, gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_IN, TopAbs_ON, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face, TopoDS_Wire

from experiments.ocp_geometry import (
    checked_face as _checked_face,
)
from experiments.ocp_geometry import (
    geometry_frame as _frame,
)
from experiments.ocp_geometry import (
    kernel_operation,
    primitive_surface,
    tessellate_face,
    transform_shape,
)
from experiments.surface_primitives import basis

TOLERANCE = 1e-8
Array = NDArray[np.float64]


def _normalize_geometry(
    item: dict[str, Any], center: Array, scale: float
) -> dict[str, Any]:
    result = item.copy()
    if item["kind"] == "plane":
        result["offset"] = (item["offset"] - np.dot(item["axis"], center)) / scale
    else:
        result["origin"] = ((np.asarray(item["origin"]) - center) / scale).tolist()
        result["radius"] = item["radius"] / scale
    return result


@kernel_operation
def generator_intersection_preview(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    """Display both secant cylinder generators, never an authored edge record."""
    from experiments.face_adjacency import classify_pair

    if sorted((first["kind"], second["kind"])) != ["cylinder", "plane"]:
        raise ValueError("generator previews require a plane and cylinder")
    if classify_pair(first, second)["status"] != "candidate":
        raise ValueError("generator previews require an unambiguous secant cut")
    side = first if first["kind"] == "cylinder" else second
    plane = second if first["kind"] == "cylinder" else first
    center = np.asarray(side["origin"], dtype=float)
    scale = max(
        abs(float(side["radius"])),
        abs(float(plane["offset"] - np.dot(plane["axis"], center))),
    )
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("generator preview scale must be positive and finite")
    operation = GeomAPI_IntSS(
        primitive_surface(_normalize_geometry(first, center, scale)),
        primitive_surface(_normalize_geometry(second, center, scale)),
        TOLERANCE,
    )
    if not operation.IsDone() or operation.NbLines() != 2:
        raise ValueError("intersection is not a pair of native generator lines")
    curves: list[dict[str, Any]] = []
    for number in (1, 2):
        curve = operation.Line(number)
        curve = curve.BasisCurve() if isinstance(curve, Geom_TrimmedCurve) else curve
        if not isinstance(curve, Geom_Line):
            raise ValueError("intersection branches are not native generator lines")
        line = curve.Lin()
        positions = (
            (np.array([curve.Value(t).Coord() for t in (-1, 1)]) * scale + center)
            .ravel()
            .tolist()
        )
        origin = (np.asarray(line.Location().Coord()) * scale + center).tolist()
        if not np.isfinite([*positions, *origin]).all():
            raise ValueError("generator preview coordinates are not finite")
        curves.append(
            {
                "kind": "line",
                "origin_display": origin,
                "direction_display": list(line.Direction().Coord()),
                "closed": False,
                "preview": {"positions": positions, "indices": []},
            }
        )
    return {
        "kind": "intersection_preview",
        "preview_only": True,
        "preview_clipped": True,
        "curves": curves,
    }


@kernel_operation
def intersection_record(
    first: dict[str, Any], second: dict[str, Any], segments: int = 128
) -> dict[str, Any]:
    """One exact supported branch; zero/multiple branches are diagnostics."""
    sides = [i for i in (first, second) if i["kind"] != "plane"]
    if sides:
        center = np.asarray(sides[0]["origin"], dtype=float)
        measures = [abs(i.get("radius", 0)) for i in sides]
        measures.extend(
            abs(i["offset"] - np.dot(i["axis"], center))
            for i in (first, second)
            if i["kind"] == "plane"
        )
        scale = max(measures)
    else:
        normals = np.asarray([first["axis"], second["axis"]])
        center = np.linalg.lstsq(
            normals, [first["offset"], second["offset"]], rcond=None
        )[0]
        scale = 1.0
    scale = float(scale) if scale > 0 else 1.0
    operation = GeomAPI_IntSS(
        primitive_surface(_normalize_geometry(first, center, scale)),
        primitive_surface(_normalize_geometry(second, center, scale)),
        TOLERANCE,
    )
    if not operation.IsDone():
        raise ValueError("surface intersection failed; adjacency remains uncertain")
    if operation.NbLines() != 1:
        raise ValueError(
            "intersection has zero or multiple branches; explicit branch review is required"
        )
    curve = operation.Line(1)
    curve = curve.BasisCurve() if isinstance(curve, Geom_TrimmedCurve) else curve
    if isinstance(curve, Geom_Line):
        if first["kind"] != "plane" or second["kind"] != "plane":
            raise ValueError(
                "open generator or tangent branches require explicit branch review"
            )
        line = curve.Lin()
        descriptor = {
            "kind": "line",
            "origin_display": (
                np.asarray(line.Location().Coord()) * scale + center
            ).tolist(),
            "direction_display": list(line.Direction().Coord()),
            "planes": [first.copy(), second.copy()],
        }
        # Display extent is explicitly not a physical edge interval.
        points = [curve.Value(-1), curve.Value(1)]
    elif isinstance(curve, (Geom_Circle, Geom_Ellipse)):
        if isinstance(curve, Geom_Circle):
            conic = curve.Circ()
            major_radius = minor_radius = conic.Radius()
        else:
            conic = curve.Elips()
            major_radius, minor_radius = conic.MajorRadius(), conic.MinorRadius()
        frame = conic.Position()
        # OCCT's analytic cone is double-sided. Scansor's chart admits only the
        # positive-radius sheet; validate the complete closed conic, not samples.
        for geometry in (first, second):
            if geometry["kind"] != "cone":
                continue
            normalized = _normalize_geometry(geometry, center, scale)
            axis = np.asarray(geometry["axis"])
            axial_center = float(
                np.dot(
                    np.asarray(conic.Location().Coord()) - normalized["origin"], axis
                )
            )
            amplitude = math.hypot(
                major_radius * float(np.dot(frame.XDirection().Coord(), axis)),
                minor_radius * float(np.dot(frame.YDirection().Coord(), axis)),
            )
            slope = float(geometry.get("slope", 0))
            minimum_radius = (
                normalized["radius"] + slope * axial_center - abs(slope) * amplitude
            )
            if minimum_radius <= TOLERANCE:
                raise ValueError(
                    "intersection lies on or approaches the cone apex or negative-radius sheet"
                )
        descriptor = {
            "kind": "circle" if isinstance(curve, Geom_Circle) else "ellipse",
            "center_display": (
                np.asarray(conic.Location().Coord()) * scale + center
            ).tolist(),
            "axis_display": list(frame.Direction().Coord()),
            "basis_u_display": list(frame.XDirection().Coord()),
            "basis_v_display": list(frame.YDirection().Coord()),
            "major_radius": major_radius * scale,
            "minor_radius": minor_radius * scale,
            "primitives": [first.copy(), second.copy()],
        }
        if descriptor["kind"] == "circle":
            descriptor["radius"] = descriptor["major_radius"]
        points = [curve.Value(2 * math.pi * i / segments) for i in range(segments)]
    else:
        raise ValueError(
            "intersection branch is not an authored circle, ellipse, or line"
        )
    positions = (
        (np.array([p.Coord() for p in points]) * scale + center).ravel().tolist()
    )
    descriptor["preview"] = {"positions": positions, "indices": []}
    return {
        "kind": "surface_intersection",
        "curves": [descriptor],
        "preview": descriptor["preview"],
        "preview_clipped": descriptor["kind"] == "line",
    }


def _curve(edge: dict[str, Any]) -> dict[str, Any]:
    curves = edge.get("curves", [edge])
    if len(curves) != 1:
        raise ValueError("face boundary requires an explicitly reviewed single branch")
    return curves[0]


def curve_descriptor(edge: dict[str, Any]) -> dict[str, Any]:
    return _curve(edge)


def cutting_plane(edge: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    curve = _curve(edge)
    planes = [
        p
        for p in curve.get("primitives", curve.get("planes", []))
        if p["kind"] == "plane"
    ]
    if not planes and geometry["kind"] != "plane" and curve["kind"] == "circle":
        return {
            "kind": "plane",
            "axis": curve["axis_display"],
            "offset": float(np.dot(curve["axis_display"], curve["center_display"])),
        }
    if geometry["kind"] == "plane":
        planes = [
            p for p in planes if abs(np.dot(p["axis"], geometry["axis"])) < 1 - 1e-12
        ]
    if len(planes) != 1:
        raise ValueError("boundary does not identify one distinct cutting plane")
    return planes[0]


@kernel_operation
def classify_loop_pair(
    first: dict[str, Any], second: dict[str, Any], geometry: dict[str, Any]
) -> str:
    first, second = _curve(first), _curve(second)
    origin = np.asarray(first["center_display"])
    scale = max(
        float(np.linalg.norm(np.asarray(loop["center_display"]) - origin))
        + float(loop.get("major_radius", loop.get("radius", 0)))
        for loop in (first, second)
    )
    plane_origin = geometry.get("origin")
    if plane_origin is None:
        plane_origin = (np.asarray(geometry["axis"]) * geometry["offset"]).tolist()
    adapted = {**geometry, "origin": plane_origin}
    u, v = basis(np.asarray(geometry["axis"]))
    adapted.setdefault("basis_u", u.tolist())
    adapted.setdefault("basis_v", v.tolist())
    frame = {**adapted, "origin": [0, 0, 0]}
    plane = gp_Pln(_frame(frame))
    wires = [_wire(loop, adapted, origin, scale) for loop in (first, second)]
    distance = BRepExtrema_DistShapeShape(*wires)
    if not distance.IsDone() or distance.Value() <= TOLERANCE:
        return "ambiguous"
    disks = [
        _checked_face(BRepBuilderAPI_MakeFace(plane, wire, True)) for wire in wires
    ]
    states = [
        BRepClass_FaceClassifier(
            disks[i], gp_Pnt(*((_loop_point(loop) - origin) / scale)), TOLERANCE
        ).State()
        for i, loop in ((0, second), (1, first))
    ]
    if states[0] == TopAbs_IN:
        return "first_contains_second"
    if states[1] == TopAbs_IN:
        return "second_contains_first"
    return "disjoint"


def _wire(
    loop: dict[str, Any], geometry: dict[str, Any], center: Array, scale: float
) -> TopoDS_Wire:
    axis = np.asarray(geometry["axis"])
    plane_origin = np.asarray(geometry["origin"])
    if loop["kind"] == "polygon":
        builder = BRepBuilderAPI_MakePolygon()
        for position in loop["positions"]:
            if (
                abs(np.dot(np.asarray(position) - plane_origin, axis))
                > TOLERANCE * scale
            ):
                raise ValueError("polygon boundary does not lie on the face plane")
            builder.Add(gp_Pnt(*((np.asarray(position) - center) / scale)))
        builder.Close()
        if not builder.IsDone():
            raise ValueError("boundary polygon did not close")
        return builder.Wire()
    normal = np.asarray(loop["axis_display"])
    if (
        np.linalg.norm(np.cross(normal, axis)) > TOLERANCE
        or abs(np.dot(np.asarray(loop["center_display"]) - plane_origin, axis))
        > TOLERANCE * scale
    ):
        raise ValueError("boundary loop does not lie on the face plane")
    origin = (np.asarray(loop["center_display"]) - center) / scale
    major = float(loop.get("major_radius", loop.get("radius", 0))) / scale
    minor = float(loop.get("minor_radius", major * scale)) / scale
    if (
        not np.isfinite([*origin, major, minor]).all()
        or minor <= TOLERANCE
        or major < minor
    ):
        raise ValueError("boundary loop is degenerate or has invalid radii")
    normal = normal if np.dot(normal, geometry["axis"]) > 0 else -normal
    frame = gp_Ax2(gp_Pnt(*origin), gp_Dir(*normal), gp_Dir(*loop["basis_u_display"]))
    conic = (
        gp_Circ(frame, major)
        if loop["kind"] == "circle"
        else gp_Elips(frame, major, minor)
    )
    edge = BRepBuilderAPI_MakeEdge(conic)
    if not edge.IsDone():
        raise ValueError("boundary conic edge could not be constructed")
    return BRepBuilderAPI_MakeWire(edge.Edge()).Wire()


def _loop_point(loop: dict[str, Any]) -> Array:
    if loop["kind"] == "polygon":
        return np.asarray(loop["positions"][0])
    return np.asarray(loop["center_display"]) + np.asarray(
        loop["basis_u_display"]
    ) * loop.get("major_radius", loop.get("radius"))


def _planar(record: dict[str, Any], center: Array, scale: float) -> TopoDS_Face:
    geometry = record["geometry"]
    normalized = {**geometry, "origin": [0, 0, 0]}
    plane = gp_Pln(_frame(normalized))
    loops = record["bounds"]["loops"]
    wires = [_wire(loop, geometry, center, scale) for loop in loops]
    disks = [
        _checked_face(BRepBuilderAPI_MakeFace(plane, wire, True)) for wire in wires
    ]
    for i in range(1, len(wires)):
        point = gp_Pnt(*((_loop_point(loops[i]) - center) / scale))
        if BRepClass_FaceClassifier(disks[0], point, TOLERANCE).State() != TopAbs_IN:
            raise ValueError("hole loop is not strictly inside its outer boundary")
        for j in range(i):
            distance = BRepExtrema_DistShapeShape(wires[i], wires[j])
            if not distance.IsDone() or distance.Value() <= TOLERANCE:
                raise ValueError(
                    "boundary loops intersect, touch, or have an unresolved gap"
                )
            if j and (
                BRepClass_FaceClassifier(disks[j], point, TOLERANCE).State()
                != TopAbs_IN
                and BRepClass_FaceClassifier(
                    disks[i],
                    gp_Pnt(*((_loop_point(loops[j]) - center) / scale)),
                    TOLERANCE,
                ).State()
                != TopAbs_IN
            ):
                continue
            if j:
                raise ValueError(
                    "hole loops overlap or are nested; review the retained regions"
                )
    builder = BRepBuilderAPI_MakeFace(plane, wires[0], True)
    for wire in wires[1:]:
        wire.Reverse()
        builder.Add(wire)
    return _checked_face(builder)


def _line_polygon(
    geometry: dict[str, Any], uses: list[tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    axis = np.asarray(geometry["axis"])
    origin = np.asarray(geometry["origin"])
    u, v = basis(axis)
    frame = np.column_stack([u, v])
    inequalities: list[tuple[Array, float]] = []
    for use, descriptor in uses:
        planes = descriptor.get("planes", [])
        candidates = [p for p in planes if abs(np.dot(p["axis"], axis)) < 1 - 1e-12]
        if len(candidates) != 1 or use["keep"] not in ("positive", "negative"):
            raise ValueError("line boundaries require a signed cutting-plane choice")
        plane = candidates[0]
        sign = 1 if use["keep"] == "positive" else -1
        normal = np.asarray(plane["axis"])
        inequalities.append(
            (sign * (normal @ frame), sign * (plane["offset"] - normal @ origin))
        )
    if len(inequalities) < 3:
        raise ValueError("line boundaries do not form a closed finite loop")
    # Translate the 2D solve before vertex comparison. Plane charts anchored at
    # the global closest-to-origin point can otherwise turn a small plate far
    # from that origin into a numerically enormous polygon.
    chart_center = np.linalg.lstsq(
        np.stack([n for n, _ in inequalities]),
        np.array([h for _, h in inequalities]),
        rcond=None,
    )[0]
    origin = origin + frame @ chart_center
    inequalities = [
        (normal, float(height - normal @ chart_center))
        for normal, height in inequalities
    ]
    # A recession direction exists iff the normals fit within a closed semicircle.
    angles = sorted(math.atan2(a[1], a[0]) % (2 * math.pi) for a, _ in inequalities)
    gaps = [
        angles[(i + 1) % len(angles)]
        - angles[i]
        + (2 * math.pi if i == len(angles) - 1 else 0)
        for i in range(len(angles))
    ]
    if max(gaps) >= math.pi - 1e-12:
        raise ValueError("line boundaries leave an incomplete or unbounded loop")
    vertices: list[Array] = []
    for (a, b), (c, d) in itertools.combinations(inequalities, 2):
        matrix = np.stack([a, c])
        if abs(np.linalg.det(matrix)) <= 1e-12:
            continue
        point = np.linalg.solve(matrix, [b, d])
        tolerance = 1e-9 * max(
            np.linalg.norm(point), abs(b), abs(d), np.finfo(float).tiny
        )
        if all(np.dot(n, point) >= h - tolerance for n, h in inequalities) and not any(
            np.linalg.norm(point - old) <= tolerance for old in vertices
        ):
            vertices.append(point)
    if len(vertices) < 3:
        raise ValueError("line choices leave an empty or degenerate polygon")
    centroid = np.mean(vertices, axis=0)
    vertices.sort(key=lambda p: math.atan2(*(p - centroid)[::-1]))
    return {
        "kind": "polygon",
        "positions": [(origin + frame @ p).tolist() for p in vertices],
    }


def _scale(record: dict[str, Any]) -> tuple[Array, float]:
    geometry = record["geometry"]
    loops = record["bounds"].get("loops", [])
    if loops:
        center = (
            np.mean(loops[0]["positions"], axis=0)
            if loops[0]["kind"] == "polygon"
            else np.asarray(loops[0]["center_display"], dtype=float)
        )
    else:
        center = np.asarray(geometry["origin"], dtype=float)
    measures = [abs(geometry.get("radius", 0))]
    for loop in loops:
        if loop["kind"] == "polygon":
            measures.extend(
                np.linalg.norm(np.asarray(loop["positions"]) - center, axis=1)
            )
        else:
            measures.append(
                np.linalg.norm(np.asarray(loop["center_display"]) - center)
                + loop.get("major_radius", loop.get("radius", 0))
            )
    for cut in record["bounds"].get("cuts", []):
        measures.append(
            abs(cut["plane"]["offset"] - np.dot(cut["plane"]["axis"], center))
        )
    scale = float(max(measures))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("face geometry has no representable positive scale")
    return center, scale


def _lateral(record: dict[str, Any], center: Array, scale: float) -> TopoDS_Face:
    geometry = record["geometry"]
    if record["surface_kind"] != "cylinder":
        raise ValueError("general cone boundary authoring remains unsupported")
    normalized = _normalize_geometry({**geometry, "kind": "cylinder"}, center, scale)
    axis = np.asarray(geometry["axis"])
    cuts = record["bounds"]["cuts"]
    limits: list[float] = []
    for cut in cuts:
        plane = cut["plane"]
        normal = np.asarray(plane["axis"])
        denominator = float(normal @ axis)
        if abs(denominator) <= 1e-8:
            raise ValueError(
                "near-axis-parallel cylinder cuts require explicit open-branch review"
            )
        mid = (plane["offset"] - normal @ center) / denominator / scale
        amplitude = (
            normalized["radius"]
            * np.linalg.norm(normal - denominator * axis)
            / abs(denominator)
        )
        limits.extend([mid - amplitude, mid + amplitude])
    lower, upper = min(limits), max(limits)
    pad = max(upper - lower, normalized["radius"])
    lower, upper = lower - pad, upper + pad
    carrier = _checked_face(
        BRepBuilderAPI_MakeFace(
            primitive_surface(normalized), 0, 2 * math.pi, lower, upper, TOLERANCE
        )
    )
    objects, tools = List_TopoDS_Shape(), List_TopoDS_Shape()
    _ = objects.Append(carrier)
    for cut in cuts:
        plane = _normalize_geometry(cut["plane"], center, scale)
        _ = tools.Append(
            _checked_face(BRepBuilderAPI_MakeFace(primitive_surface(plane), TOLERANCE))
        )
    splitter = BRepAlgoAPI_Splitter()
    splitter.SetArguments(objects)
    splitter.SetTools(tools)
    splitter.Build()
    if not splitter.IsDone():
        raise ValueError("kernel could not split the reviewed cylindrical face")
    retained: list[TopoDS_Face] = []
    explorer = TopExp_Explorer(splitter.Shape(), TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face(explorer.Current())
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        point = np.asarray(props.CentreOfMass().Coord()) * scale + center
        if all(
            (1 if cut["side"] == "positive" else -1)
            * (np.dot(cut["plane"]["axis"], point) - cut["plane"]["offset"])
            > TOLERANCE * scale
            for cut in cuts
        ):
            retained.append(face)
        explorer.Next()
    if len(retained) != 1:
        raise ValueError("plane cuts leave empty or multiple disconnected face regions")
    face = retained[0]
    vertices = TopExp_Explorer(face, TopAbs_VERTEX)
    while vertices.More():
        z = np.dot(BRep_Tool.Pnt_s(TopoDS.Vertex(vertices.Current())).Coord(), axis)
        if min(abs(z - lower), abs(z - upper)) <= 10 * TOLERANCE:
            raise ValueError(
                "plane cuts leave an unbounded face; add explicit closing boundaries"
            )
        vertices.Next()
    if not BRepCheck_Analyzer(face).IsValid():
        raise ValueError(
            "reviewed cylindrical face has invalid seam or boundary topology"
        )
    return face


def _validate_cylindrical_curve(
    geometry: dict[str, Any], curve: dict[str, Any]
) -> None:
    """Check the whole conic's radial polynomial, not sampled preview points."""
    axis = np.asarray(geometry["axis"])
    relative = np.asarray(curve["center_display"]) - np.asarray(geometry["origin"])
    major = float(curve.get("major_radius", curve.get("radius", 0)))
    minor = float(curve.get("minor_radius", curve.get("radius", 0)))
    u = major * np.asarray(curve["basis_u_display"])
    v = minor * np.asarray(curve["basis_v_display"])
    d, e, f = [value - np.dot(value, axis) * axis for value in (relative, u, v)]
    radius = float(geometry["radius"])
    scale = max(major, minor, radius, float(np.linalg.norm(d)))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("cylindrical boundary has no finite positive scale")
    d, e, f = d / scale, e / scale, f / scale
    coefficients = np.array(
        [
            2 * np.dot(d, e),
            2 * np.dot(d, f),
            np.dot(e, f),
            np.dot(e, e) - np.dot(f, f),
            np.dot(d, d) + (np.dot(e, e) + np.dot(f, f)) / 2 - (radius / scale) ** 2,
        ]
    )
    if (
        not np.isfinite(coefficients).all()
        or np.max(np.abs(coefficients)) > 10 * TOLERANCE
    ):
        raise ValueError("shared boundary conic does not lie on the cylindrical face")


@kernel_operation
def face_from_record(record: dict[str, Any]) -> TopoDS_Face:
    if not record.get("bounded"):
        raise ValueError("general face requires explicit finite closed boundaries")
    face, center, scale = _normalized_face(record)
    matrix = np.eye(4)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = center
    return TopoDS.Face(transform_shape(face, matrix))


def _normalized_face(record: dict[str, Any]) -> tuple[TopoDS_Face, Array, float]:
    center, scale = _scale(record)
    face = (
        _planar(record, center, scale)
        if record["surface_kind"] == "plane"
        else _lateral(record, center, scale)
    )
    return face, center, scale


@kernel_operation
def face_from_boundaries(
    geometry: dict[str, Any],
    boundaries: list[tuple[dict[str, Any], dict[str, Any]]],
    observations: Array | None = None,
) -> dict[str, Any]:
    del observations  # Scan support never supplies physical bounds.
    kind = geometry["kind"]
    adapted = geometry.copy()
    if kind == "plane":
        adapted.setdefault(
            "origin", (np.asarray(geometry["axis"]) * geometry["offset"]).tolist()
        )
    u, v = basis(np.asarray(geometry["axis"]))
    adapted.setdefault("basis_u", u.tolist())
    adapted.setdefault("basis_v", v.tolist())
    curves = [(use, _curve(edge)) for use, edge in boundaries]
    if kind == "plane":
        closed = [
            (use, curve)
            for use, curve in curves
            if curve["kind"] in ("circle", "ellipse")
        ]
        lines = [(use, curve) for use, curve in curves if curve["kind"] == "line"]
        outers = [curve for use, curve in closed if use["keep"] == "inside"]
        holes = [curve for use, curve in closed if use["keep"] == "outside"]
        if len(closed) + len(lines) != len(curves) or len(outers) + len(holes) != len(
            closed
        ):
            raise ValueError(
                "planar boundaries require inside/outside closed loops or signed lines"
            )
        if lines:
            if outers:
                if len(outers) != 1:
                    raise ValueError(
                        "planar face requires one explicit closed outer loop"
                    )
                outer = outers[0]
                for use, line in lines:
                    cutter = cutting_plane(line, adapted)
                    sign = 1 if use["keep"] == "positive" else -1
                    if use["keep"] not in ("positive", "negative"):
                        raise ValueError(
                            "line boundaries require a signed cutting-plane choice"
                        )
                    normal = np.asarray(cutter["axis"])
                    margin = sign * (
                        np.dot(normal, outer["center_display"]) - cutter["offset"]
                    )
                    amplitude = math.hypot(
                        float(outer.get("major_radius", outer.get("radius", 0)))
                        * float(np.dot(normal, outer["basis_u_display"])),
                        float(outer.get("minor_radius", outer.get("radius", 0)))
                        * float(np.dot(normal, outer["basis_v_display"])),
                    )
                    if margin - amplitude <= TOLERANCE * max(
                        amplitude, outer.get("major_radius", outer.get("radius", 0))
                    ):
                        raise ValueError(
                            "line cuts or touches an outer conic; explicit arrangement review is required"
                        )
            else:
                outers = [_line_polygon(adapted, lines)]
        if len(outers) != 1:
            raise ValueError("planar face requires one explicit closed outer loop")
        loops: list[dict[str, Any]] = []
        for role, selected in (("outer", outers), ("hole", holes)):
            for loop in selected:
                sources = [
                    use["intersection"]
                    for use, curve in curves
                    if (loop["kind"] == "polygon" and curve["kind"] == "line")
                    or curve is loop
                ]
                loops.append({**loop, "role": role, "sources": sources})
        bounds = {"loops": loops}
    elif kind == "cylinder":
        cuts: list[dict[str, Any]] = []
        for use, curve in curves:
            if use["keep"] not in ("positive", "negative") or curve["kind"] not in (
                "circle",
                "ellipse",
            ):
                raise ValueError(
                    "cylindrical face requires signed closed plane-cut boundaries"
                )
            _validate_cylindrical_curve(adapted, curve)
            planes = [p for p in curve.get("primitives", []) if p["kind"] == "plane"]
            if not planes and curve["kind"] == "circle":
                planes = [
                    {
                        "kind": "plane",
                        "axis": curve["axis_display"],
                        "offset": float(
                            np.dot(curve["axis_display"], curve["center_display"])
                        ),
                    }
                ]
            if len(planes) != 1:
                raise ValueError("cylindrical cut requires its explicit source plane")
            cuts.append({"plane": planes[0], "side": use["keep"]})
        if len(cuts) < 2:
            raise ValueError("cylindrical face requires explicit closing boundaries")
        bounds = {"cuts": cuts}
    else:
        raise ValueError(
            "general boundary authoring supports planes and cylinders in this slice"
        )
    record = {
        "kind": "trimmed_face",
        "surface_kind": kind,
        "geometry": adapted,
        "bounds": bounds,
        "bounded": True,
        "preview_clipped": False,
        "boundary_ids": [use["intersection"] for use, _ in boundaries],
        "boundary_uses": [use.copy() for use, _ in boundaries],
    }
    # Keep display meshing in the native local chart. Transforming a small face
    # to a distant world origin and back can corrupt OCCT pcurve consistency.
    _ = face_from_record(record)
    face, center, scale = _normalized_face(record)
    preview = tessellate_face(face, 1.0)
    preview["positions"] = (
        (np.asarray(preview["positions"]).reshape(-1, 3) * scale + center)
        .ravel()
        .tolist()
    )
    record["preview"] = preview
    return record


@kernel_operation
def classify_face_points(
    record: dict[str, Any], projected_points: Array
) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """Classify already projected evidence; never change physical boundaries."""
    face, center, scale = _normalized_face(record)
    states = [
        BRepClass_FaceClassifier(
            face, gp_Pnt(*((point - center) / scale)), TOLERANCE
        ).State()
        for point in projected_points
    ]
    return np.array([s == TopAbs_IN for s in states], dtype=bool), np.array(
        [s == TopAbs_ON for s in states], dtype=bool
    )
