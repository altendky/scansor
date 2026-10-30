"""Bounded renderer-only CAD refinement over one accepted graph publication.

No CAD bindings load at import time. Accepted preview records, physical selectors,
review fingerprints and export geometry are never replaced by these buffers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from experiments.scan_workspace import check_cancelled

if TYPE_CHECKING:
    from experiments.feature_graph import FeatureGraph

MAX_DISPLAY_FACES = 512
MAX_DISPLAY_EDGES = 8192
MAX_DISPLAY_TRIANGLES = 200_000
MAX_DISPLAY_VERTICES = 200_000
MAX_DISPLAY_EDGE_POINTS = 200_000
MAX_DISPLAY_BYTES = 16 * 1024**2
# Conservative space for JSON numbers (including exponents and punctuation),
# before constructing Python lists or encoding the final bounded response.
MAX_DISPLAY_SCALARS = MAX_DISPLAY_BYTES // 40
DISPLAY_QUALITY = {"standard": (1e-4, 0.04), "fine": (1e-5, 0.02)}


class DisplayRefinementRequest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    token: str
    revision: int = Field(ge=0)
    feature: str = Field(min_length=1, max_length=256)
    quality: Literal["draft", "standard", "fine"] = "standard"


def _finish(display: dict[str, Any]) -> dict[str, Any]:
    preview, edges = display["preview"], display["edges"]
    positions, indices = preview["positions"], preview["indices"]
    if len(positions) % 3 or len(indices) % 3 or not positions or not indices:
        raise ValueError("display preview requires a nonempty triangle mesh")
    vertex_count, triangles = len(positions) // 3, len(indices) // 3
    edge_points = sum(len(edge["positions"]) // 3 for edge in edges)
    if (
        vertex_count > MAX_DISPLAY_VERTICES
        or triangles > MAX_DISPLAY_TRIANGLES
        or edge_points > MAX_DISPLAY_EDGE_POINTS
        or len(edges) > MAX_DISPLAY_EDGES
    ):
        raise ValueError("display buffers exceed the refinement limit")
    if len(positions) + len(indices) + 3 * edge_points > MAX_DISPLAY_SCALARS:
        raise ValueError("display buffers exceed the byte limit")
    if not all(math.isfinite(value) for value in positions) or any(
        not isinstance(index, int)
        or isinstance(index, bool)
        or not 0 <= index < vertex_count
        for index in indices
    ):
        raise ValueError("display mesh contains invalid coordinates or indices")
    for edge in edges:
        values = edge["positions"]
        if (
            len(values) < 6
            or len(values) % 3
            or not all(math.isfinite(value) for value in values)
        ):
            raise ValueError("display outline contains invalid coordinates")
    display["stats"] = {"triangles": triangles, "edge_points": edge_points, "bytes": 0}
    while True:
        size = len(json.dumps(display, allow_nan=False, separators=(",", ":")).encode())
        if size > MAX_DISPLAY_BYTES:
            raise ValueError("display response exceeds the byte limit")
        if display["stats"]["bytes"] == size:
            break
        display["stats"]["bytes"] = size
    return display


def _draft(record: dict[str, Any]) -> dict[str, Any]:
    original = record["preview"]
    edges = [
        {
            "positions": item["positions"],
            "closed": bool(item.get("closed", False)),
            **(
                {"source_faces": item["source_faces"]} if "source_faces" in item else {}
            ),
        }
        for item in original.get("edges", [])
        if len(item.get("positions", [])) >= 6
    ]
    if "edges" not in original:
        for loop in record.get("loops", record.get("bounds", {}).get("loops", [])):
            if loop.get("kind") == "polygon":
                edges.append(
                    {
                        "positions": np.asarray(loop["positions"]).ravel().tolist(),
                        "closed": True,
                    }
                )
            for edge in loop.get("edges", []):
                if not edge.get("artificial") and not edge.get("seam"):
                    values = edge.get("preview", {}).get("positions", [])
                    if len(values) >= 6:
                        edges.append(
                            {"positions": values, "closed": bool(edge.get("closed"))}
                        )
        bounds, geometry = record.get("bounds", {}), record.get("geometry", {})
        interval = bounds.get("radial", bounds.get("axial"))
        if interval is not None:
            points = np.asarray(original["positions"]).reshape(-1, 3)
            axis, center = np.asarray(geometry["axis"]), np.asarray(geometry["origin"])
            delta = points - center
            planar = "radial" in bounds
            axial = delta @ axis
            radial = delta - axial[:, None] * axis
            coordinates = np.linalg.norm(radial, axis=1) if planar else axial
            u, v = np.asarray(geometry["basis_u"]), np.asarray(geometry["basis_v"])
            for bound in interval:
                if bound is None or (planar and bound == 0):
                    continue
                scale = max(
                    float(np.max(np.abs(coordinates))), abs(bound), np.finfo(float).tiny
                )
                selected = points[np.abs(coordinates - bound) <= scale * 1e-9]
                if len(selected) < 3:
                    continue
                offsets = selected - center
                selected = selected[np.argsort(np.arctan2(offsets @ v, offsets @ u))]
                edges.append({"positions": selected.ravel().tolist(), "closed": True})
    return _finish(
        deepcopy(
            {
                "preview": {
                    "positions": original["positions"],
                    "indices": original["indices"],
                },
                "edges": edges,
            }
        )
    )


def _interval_face(record: dict[str, Any]) -> tuple[Any, np.ndarray, float, list[Any]]:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from experiments.ocp_geometry import face_from_record

    geometry = deepcopy(record["geometry"])
    center = np.asarray(geometry["origin"], dtype=float)
    axis = np.asarray(geometry["axis"], dtype=float)
    planar = record["surface_kind"] == "plane"
    name = "radial" if planar else "axial"
    interval = record["bounds"][name].copy()
    points = np.asarray(record.get("preview", {}).get("positions", [])).reshape(-1, 3)
    if any(value is None for value in interval):
        if not len(points):
            raise ValueError("clipped display face has no finite accepted envelope")
        relative = points - center
        coordinates = (
            np.linalg.norm(relative - (relative @ axis)[:, None] * axis, axis=1)
            if planar
            else relative @ axis
        )
        interval = [
            float(np.min(coordinates)) if interval[0] is None else interval[0],
            float(np.max(coordinates)) if interval[1] is None else interval[1],
        ]
    scale = max(abs(float(value)) for value in interval)
    if not planar:
        scale = max(
            scale, abs(float(geometry["radius"])), abs(float(interval[1] - interval[0]))
        )
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("display interval scale is not representable")
    geometry["origin"] = [0.0, 0.0, 0.0]
    if "offset" in geometry:
        geometry["offset"] = 0.0
    if "radius" in geometry:
        geometry["radius"] /= scale
    face = face_from_record(
        {
            "bounded": True,
            "surface_kind": record["surface_kind"],
            "geometry": geometry,
            "bounds": {name: [value / scale for value in interval]},
        }
    )
    omitted: list[Any] = []
    if not record.get("bounded"):
        explorer = TopExp_Explorer(face, TopAbs_EDGE)
        while explorer.More():
            edge = TopoDS.Edge(explorer.Current())
            curve = BRepAdaptor_Curve(edge)
            point = np.asarray(
                curve.Value(
                    (curve.FirstParameter() + curve.LastParameter()) / 2
                ).Coord()
            )
            coordinate = (
                float(np.linalg.norm(point - (point @ axis) * axis))
                if planar
                else float(point @ axis)
            )
            if any(
                original is None and abs(coordinate - bound / scale) < 1e-7
                for original, bound in zip(
                    record["bounds"][name], interval, strict=True
                )
            ):
                omitted.append(edge)
            explorer.Next()
    return face, center, scale, omitted


def refine_display_geometry(
    record: dict[str, Any],
    *,
    faces: dict[str, dict[str, Any]] | None = None,
    scale: float,
    quality: Literal["draft", "standard", "fine"],
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Produce disposable buffers; supplied accepted records remain untouched."""
    check_cancelled(cancelled)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("model display scale must be positive and finite")
    if quality == "draft":
        return _draft(record)
    if quality not in DISPLAY_QUALITY:
        raise ValueError("unknown CAD display quality")
    from experiments.native_replay import native_replay_scope
    from experiments.ocp_geometry import tessellate_display_shape

    with native_replay_scope():
        omitted = []
        provenance = None
        if record.get("kind") == "body":
            from experiments.body_geometry import body_display_shape

            if not faces or len(faces) > MAX_DISPLAY_FACES:
                raise ValueError("display body inputs exceed the face limit")
            shape, provenance = body_display_shape(record, faces, cancelled=cancelled)
            center, local_scale = np.zeros(3), 1.0
        elif "arrangement" in record.get("bounds", {}):
            from experiments.face_arrangement import normalized_display_face

            shape, center, local_scale, omitted = normalized_display_face(record)
        elif "loops" in record.get("bounds", {}) or "cuts" in record.get("bounds", {}):
            from experiments.general_face_geometry import normalized_display_face

            shape, center, local_scale = normalized_display_face(record)
        else:
            shape, center, local_scale, omitted = _interval_face(record)
        check_cancelled(cancelled)
        chord, angle = DISPLAY_QUALITY[quality]
        display = tessellate_display_shape(
            shape,
            scale / local_scale * chord,
            angle,
            max_faces=MAX_DISPLAY_FACES,
            max_edges=MAX_DISPLAY_EDGES,
            max_triangles=MAX_DISPLAY_TRIANGLES,
            max_vertices=MAX_DISPLAY_VERTICES,
            max_edge_points=MAX_DISPLAY_EDGE_POINTS,
            max_scalars=MAX_DISPLAY_SCALARS,
            omitted_edges=omitted,
            source_faces=provenance,
            cancelled=cancelled,
        )
        for values in [display["preview"], *display["edges"]]:
            points = np.asarray(values["positions"]).reshape(-1, 3)
            values["positions"] = (points * local_scale + center).ravel().tolist()
        display["quality"] = {
            "relative_chord": chord,
            "angular_deflection": angle,
            "model_scale": scale,
        }
        check_cancelled(cancelled)
        return _finish(display)


def prepare_display_refinement(
    graph: FeatureGraph,
    request: DisplayRefinementRequest,
) -> Callable[[Callable[[], bool] | None], dict[str, Any]]:
    inputs = graph.ready_display_inputs(
        request.token, request.revision, request.feature
    )

    def compute(cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
        def stopped() -> bool:
            check_cancelled(cancelled)
            graph.assert_display_current(request.token, request.revision)
            return False

        display = refine_display_geometry(
            **inputs, quality=request.quality, cancelled=stopped
        )
        graph.assert_display_current(request.token, request.revision)
        return {**request.model_dump(), "display": display}

    return compute
