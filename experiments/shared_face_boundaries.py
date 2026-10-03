"""Kernel checks for approved finite boundary segments, without sewing faces.

An approved edge may be split between several target cells. Comparisons operate
on their aggregate native edges, never tessellation or relative length totals.
"""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
from numpy.typing import NDArray
from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.Geom import Geom_ConicalSurface, Geom_CylindricalSurface, Geom_Plane
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Shape

from experiments.face_geometry import face_from_record
from experiments.native_replay import native_replay_scope
from experiments.ocp_geometry import (
    kernel_operation,
    primitive_surface,
    transform_shape,
)
from experiments.surface_primitives import primitive

TOLERANCE = 1e-8


def _primitive(geometry: dict[str, Any]) -> dict[str, Any]:
    if "axis" in geometry:
        return geometry
    return primitive(geometry)


def _edges(shape: TopoDS_Shape) -> list[TopoDS_Edge]:
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    edges: list[TopoDS_Edge] = []
    while explorer.More():
        edge = TopoDS.Edge(explorer.Current())
        if not BRep_Tool.Degenerated_s(edge) and not any(
            edge.IsSame(old) for old in edges
        ):
            edges.append(edge)
        explorer.Next()
    return edges


def _physical_edges(
    record: dict[str, Any], against: dict[str, Any] | None = None
) -> list[TopoDS_Edge]:
    bounds = record["bounds"]
    if "arrangement" in bounds:
        # Replay obtains current native provenance, not stored preview geometry.
        from experiments.face_arrangement import (
            _selected,  # pyright: ignore[reportPrivateUsage]
        )

        intent = bounds["arrangement"]
        cell, center, scale = _selected(intent, intent["selector"])
        transform = np.eye(4)
        transform[:3, :3] *= scale
        transform[:3, 3] = center
        wires = TopExp_Explorer(cell["face"], TopAbs_WIRE)
        result: list[TopoDS_Edge] = []
        physical: list[TopoDS_Edge] = []
        artificial_vertices: list[TopoDS_Shape] = []
        for loop in cell["loops"]:
            if not wires.More():
                raise ValueError("native boundary provenance is incomplete")
            explorer = BRepTools_WireExplorer(
                TopoDS.Wire(wires.Current()), cell["face"]
            )
            for descriptor in loop["edges"]:
                if not explorer.More():
                    raise ValueError("native boundary provenance is incomplete")
                edge = explorer.Current()
                if not descriptor["artificial"] and not descriptor["seam"]:
                    physical.append(edge)
                if descriptor["artificial"]:
                    vertices = TopExp_Explorer(edge, TopAbs_VERTEX)
                    while vertices.More():
                        artificial_vertices.append(vertices.Current())
                        vertices.Next()
                explorer.Next()
            if explorer.More():
                raise ValueError("native boundary provenance is incomplete")
            wires.Next()
        if wires.More():
            raise ValueError("native boundary provenance is incomplete")
        for edge in physical:
            world = _edges(transform_shape(edge, transform))
            clipped = False
            vertices = TopExp_Explorer(edge, TopAbs_VERTEX)
            while vertices.More():
                if any(
                    vertices.Current().IsSame(vertex) for vertex in artificial_vertices
                ):
                    clipped = True
                vertices.Next()
            if clipped:
                if against is None or _on_surface(world, against):
                    raise ValueError(
                        "approved boundary is clipped by a display envelope; declare finite extents"
                    )
                continue
            result.extend(world)
        return result
    if not record.get("bounded"):
        # Only the finite declared rings of an open scalar interval are physical.
        geometry = record["geometry"]
        interval = bounds.get(
            "radial" if record["surface_kind"] == "plane" else "axial"
        )
        if interval is None:
            raise ValueError("open face has no supported explicit boundary segments")
        axis = np.asarray(geometry["axis"])
        origin = np.asarray(geometry["origin"])
        result = []
        for bound in interval:
            if bound is None:
                continue
            center = (
                origin if record["surface_kind"] == "plane" else origin + axis * bound
            )
            radius = (
                bound
                if record["surface_kind"] == "plane"
                else geometry["radius"] + geometry.get("slope", 0) * bound
            )
            if radius <= 0:
                continue
            frame = gp_Ax2(gp_Pnt(*center), gp_Dir(*axis))
            result.append(BRepBuilderAPI_MakeEdge(gp_Circ(frame, radius)).Edge())
        return result
    face = face_from_record(record)
    return [edge for edge in _edges(face) if not BRep_Tool.IsClosed_s(edge, face)]


def _box(edges: list[TopoDS_Edge]) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    box = Bnd_Box()
    for edge in edges:
        BRepBndLib.AddOptimal_s(edge, box, False, False)
    if box.IsVoid():
        raise ValueError("approved shared boundary has no finite physical segments")
    return np.asarray(box.CornerMin().Coord()), np.asarray(box.CornerMax().Coord())


def _operation(
    first: TopoDS_Shape, second: TopoDS_Shape, *, common: bool
) -> list[TopoDS_Edge]:
    operation = (
        BRepAlgoAPI_Common(first, second) if common else BRepAlgoAPI_Cut(first, second)
    )
    if not operation.IsDone() or not BRepCheck_Analyzer(operation.Shape()).IsValid():
        raise ValueError(
            "native shared-boundary comparison failed; review the adjacency"
        )
    return _edges(operation.Shape())


def _on_surface(
    edges: list[TopoDS_Edge], geometry: dict[str, Any]
) -> list[TopoDS_Edge]:
    if not edges:
        return []
    lower, upper = _box(edges)
    center = (lower + upper) / 2
    scale = float(np.linalg.norm(upper - lower))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("shared boundary has no representable positive scale")
    normalize = np.eye(4)
    normalize[:3, :3] /= scale
    normalize[:3, 3] = -center / scale
    restore = np.eye(4)
    restore[:3, :3] *= scale
    restore[:3, 3] = center
    edges = [TopoDS.Edge(transform_shape(edge, normalize)) for edge in edges]
    geometry = _primitive(geometry).copy()
    if geometry["kind"] == "plane":
        geometry["offset"] = (
            geometry["offset"] - np.asarray(geometry["axis"]) @ center
        ) / scale
    else:
        geometry["origin"] = (
            (np.asarray(geometry["origin"]) - center) / scale
        ).tolist()
        geometry["radius"] /= scale
    lower, upper = (lower - center) / scale, (upper - center) / scale
    center, span = np.zeros(3), 1.0
    surface = primitive_surface(geometry)
    # A computation carrier encompasses the complete source edges. Its edges
    # are never returned or promoted to physical boundaries.
    if isinstance(surface, Geom_Plane):
        # Project the box corners into the surface chart via its frame.
        position = surface.Position()
        origin = np.asarray(position.Location().Coord())
        u, v = (
            np.asarray(position.XDirection().Coord()),
            np.asarray(position.YDirection().Coord()),
        )
        corners = np.array(
            [
                [x, y, z]
                for x in (lower[0], upper[0])
                for y in (lower[1], upper[1])
                for z in (lower[2], upper[2])
            ]
        )
        projected = np.column_stack(((corners - origin) @ u, (corners - origin) @ v))
        lo, hi = projected.min(axis=0) - span, projected.max(axis=0) + span
        builder = BRepBuilderAPI_MakeFace(
            surface, lo[0], hi[0], lo[1], hi[1], TOLERANCE
        )
    elif isinstance(surface, (Geom_CylindricalSurface, Geom_ConicalSurface)):
        position = surface.Position()
        origin = np.asarray(position.Location().Coord())
        axis = np.asarray(position.Direction().Coord())
        axial = float((center - origin) @ axis)
        factor = (
            math.cos(surface.SemiAngle())
            if isinstance(surface, Geom_ConicalSurface)
            else 1.0
        )
        first, last = (axial - 2 * span) / factor, (axial + 2 * span) / factor
        if isinstance(surface, Geom_ConicalSurface):
            sine = math.sin(surface.SemiAngle())
            apex = -surface.RefRadius() / sine
            if sine > 0:
                first = max(first, apex + TOLERANCE)
            else:
                last = min(last, apex - TOLERANCE)
            if last <= first:
                return []
        builder = BRepBuilderAPI_MakeFace(
            surface,
            0.0,
            2 * math.pi,
            first,
            last,
            TOLERANCE,
        )
    else:
        raise ValueError("unsupported shared-boundary analytic surface")
    if not builder.IsDone():
        raise ValueError("cannot construct shared-boundary computation carrier")
    return [
        TopoDS.Edge(transform_shape(part, restore))
        for edge in edges
        for part in _operation(edge, builder.Face(), common=True)
    ]


def _subtract(
    edges: list[TopoDS_Edge], cutters: list[TopoDS_Edge]
) -> list[TopoDS_Edge]:
    remaining = edges
    for cutter in cutters:
        remaining = [
            part
            for edge in remaining
            for part in _operation(edge, cutter, common=False)
        ]
    return remaining


def _boundary_union(groups: list[list[TopoDS_Edge]]) -> list[TopoDS_Edge]:
    """Remove native segments internal to two retained cells on one surface."""
    return [
        part
        for index, edges in enumerate(groups)
        for part in _subtract(
            edges,
            [
                edge
                for other, items in enumerate(groups)
                if other != index
                for edge in items
            ],
        )
    ]


def _record(source: dict[str, Any]) -> dict[str, Any]:
    return source.get("record", source)


@kernel_operation
@native_replay_scope()
def approved_boundary_previews(
    source_records: list[dict[str, Any]],
    target_ref: dict[str, Any],
    target_geometry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Finite display paths for guidance; these samples are never comparison inputs."""
    del target_ref
    result: list[dict[str, Any]] = []
    for source in source_records:
        for edge in _on_surface(
            _physical_edges(_record(source), target_geometry), target_geometry
        ):
            curve = BRepAdaptor_Curve(edge)
            points = [
                curve.Value(float(t)).Coord()
                for t in np.linspace(curve.FirstParameter(), curve.LastParameter(), 65)
            ]
            result.append(
                {
                    "source_id": source.get("id"),
                    "positions": np.asarray(points).ravel().tolist(),
                    "closed": edge.Closed(),
                }
            )
    return result


@kernel_operation
@native_replay_scope()
def check_shared_boundaries(
    target_records: list[dict[str, Any]],
    target_ref: dict[str, Any],
    source_records: list[dict[str, Any]],
    target_geometry: dict[str, Any],
    require_complete: bool = True,
) -> dict[str, Any]:
    """Require shared physical segment containment, and optionally full coverage.

    No residual native edge is discarded for being small relative to the model.
    This checks geometry, not face orientation, edge manifoldness, or solids.
    """
    if any(record.get("surface") != target_ref for record in target_records):
        raise ValueError("shared-boundary target surface context does not match")
    checked: list[str] = []
    groups: dict[str, tuple[dict[str, Any], list[list[TopoDS_Edge]], list[str]]] = {}
    for source in source_records:
        record = _record(source)
        expected = _on_surface(
            _physical_edges(record, target_geometry), target_geometry
        )
        if not expected:
            raise ValueError(
                f"approved face {source.get('id', '')} has no physical boundary on this surface"
            )
        key = json.dumps(record.get("surface", record["geometry"]), sort_keys=True)
        if key not in groups:
            groups[key] = (record, [], [])
        groups[key][1].append(expected)
        groups[key][2].append(str(source.get("id", "")))
    for record, expected_groups, source_ids in groups.values():
        source_geometry = {**record["geometry"], "kind": record["surface_kind"]}
        actual_groups = [
            _on_surface(_physical_edges(target, source_geometry), source_geometry)
            for target in target_records
        ]
        expected = [edge for items in expected_groups for edge in items]
        actual = [edge for items in actual_groups for edge in items]
        # Normalize both sets together, independently of display coordinates.
        lower, upper = _box(expected + actual)
        scale = float(np.linalg.norm(upper - lower))
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("shared boundary has no representable positive scale")
        matrix = np.eye(4)
        matrix[:3, :3] /= scale
        matrix[:3, 3] = -(lower + upper) / (2 * scale)
        expected = _boundary_union(
            [
                [TopoDS.Edge(transform_shape(edge, matrix)) for edge in items]
                for items in expected_groups
            ]
        )
        actual = _boundary_union(
            [
                [TopoDS.Edge(transform_shape(edge, matrix)) for edge in items]
                for items in actual_groups
            ]
        )
        if not expected:
            raise ValueError(
                "approved segments are internal to the source face union; review the adjacency"
            )
        if _subtract(actual, expected):
            raise ValueError(
                f"selected faces extend beyond approved shared boundary of {', '.join(source_ids)}"
            )
        if require_complete and _subtract(expected, actual):
            raise ValueError(
                f"selected faces do not cover approved shared boundary of {', '.join(source_ids)}"
            )
        checked.extend(source_ids)
    return {"checked_sources": checked, "complete": require_complete}
