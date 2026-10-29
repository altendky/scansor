"""Independent raw-workflow observations and exact-source native replay.

The raw patch has no metrology oracle. CAD is checked against the geometry the
UI actually published, and native vectors are not represented as HTTP captures.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import stat
import sys
import tempfile
import zipfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

RAW_SHA256 = "537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358"
POLICY_SHA256 = "fe67038bde43464219f53d45de6c97ea6721bc49a684b28b97e69c643b485a0f"
PROJECTION = (
    "recipe",
    "token",
    "states",
    "errors",
    "diagnostics",
    "memberships",
    "results",
    "result",
    "derived",
    "input_catalogue",
    "input_requirements",
    "input_contracts",
    "relationship_contracts",
)
TRANSPORT = {
    "revision",
    "project_generation",
    "required_failures",
    "evaluation_running",
    "evaluation_error",
    "evaluation",
}
FORBIDDEN = {
    "initial_parameters",
    "initialization",
    "seed",
    "fit_geometry",
    "solver",
    "residuals",
    "weighted_rms",
}
FloatArray = NDArray[np.float64]


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def finite(value: Any, shape: tuple[int, ...] | None = None) -> FloatArray:
    array = np.asarray(value)
    require(
        array.dtype.kind in "fiu" and np.isfinite(array).all(),
        "nonfinite/nonnumeric observation",
    )
    require(shape is None or array.shape == shape, "observation shape differs")
    return array.astype(np.float64)


@dataclass(frozen=True)
class RawGeometry:
    positions: FloatArray
    normals: FloatArray
    triangles: NDArray[np.int32]
    weights: FloatArray
    original_normals: NDArray[np.float32] | None = None


def decode_ply(raw: bytes, *, normals: bool = True) -> RawGeometry:
    """Decode the two known layouts independently of the production PLY reader."""
    end = raw.index(b"end_header\n") + len(b"end_header\n")
    lines = [
        line
        for line in raw[:end].decode("ascii").splitlines()
        if not line.startswith("comment ")
    ]
    require(
        lines[:2] == ["ply", "format binary_little_endian 1.0"],
        "unexpected raw PLY format",
    )
    require(lines[2].startswith("element vertex "), "missing vertex count")
    vertices = int(lines[2].split()[-1])
    properties = (
        ["property float " + name for name in ("x", "y", "z", "nx", "ny", "nz")]
        if normals
        else ["property double " + name for name in ("x", "y", "z")]
    )
    face_line = 3 + len(properties)
    require(
        lines[3:face_line] == properties
        and lines[face_line].startswith("element face "),
        "unexpected PLY vertex layout",
    )
    faces = int(lines[face_line].split()[-1])
    require(
        lines[face_line + 1 :]
        == ["property list uchar int vertex_indices", "end_header"],
        "unexpected PLY face layout",
    )
    require(vertices > 0 and faces > 0, "empty raw fixture")
    components = 6 if normals else 3
    dtype = "<f4" if normals else "<f8"
    size = vertices * components * np.dtype(dtype).itemsize
    require(len(raw) == end + size + faces * 13, "PLY byte count differs")
    rows = np.frombuffer(
        raw, dtype=dtype, count=vertices * components, offset=end
    ).reshape(vertices, components)
    xyz = finite(rows[:, :3])
    face_rows = np.frombuffer(
        raw,
        dtype=np.dtype([("count", "u1"), ("ids", "<i4", (3,))]),
        count=faces,
        offset=end + size,
    )
    require(np.all(face_rows["count"] == 3), "nontriangle raw row")
    triangles = face_rows["ids"].copy()
    require(
        np.all(triangles >= 0) and np.all(triangles < vertices),
        "raw face index out of range",
    )
    directions = finite(rows[:, 3:]) if normals else np.zeros_like(xyz)
    if normals:
        lengths = np.linalg.norm(directions, axis=1)
        require(np.all(lengths > 0), "zero source normal")
        directions /= lengths[:, None]
    weights = np.zeros(vertices)
    # Original face order and scalar accumulation are independent of importer
    # chunking/scatter implementation, including all incident unselected faces.
    for triangle in triangles:
        a, b, c = xyz[triangle]
        contribution = float(np.linalg.norm(np.cross(b - a, c - a)) / 6)
        for vertex in triangle:
            weights[vertex] += contribution
    require(
        np.isfinite(weights).all() and np.any(weights > 0), "invalid incident areas"
    )
    return RawGeometry(
        xyz,
        directions,
        triangles,
        weights,
        rows[:, 3:].astype("<f4").copy() if normals else None,
    )


def project_graph(graph: dict[str, Any]) -> dict[str, Any]:
    require(
        set(PROJECTION) <= graph.keys() <= set(PROJECTION) | TRANSPORT,
        "graph semantic fields differ",
    )
    evaluation = graph.get("evaluation")
    if evaluation is not None:
        require(
            isinstance(evaluation, dict)
            and set(evaluation)
            == {"id", "sequence", "generation", "state", "kind", "roots"}
            and isinstance(evaluation["id"], str)
            and 0 < len(evaluation["id"]) <= 128
            and type(evaluation["sequence"]) is int
            and evaluation["sequence"] > 0
            and type(evaluation["generation"]) is int
            and evaluation["generation"] >= 0
            and evaluation["state"]
            in {
                "queued",
                "running",
                "cancel-requested",
                "cancelled",
                "complete",
                "failed",
            }
            and evaluation["kind"] in {"evaluate", "ensure", "preview"}
            and isinstance(evaluation["roots"], list)
            and all(isinstance(root, str) for root in evaluation["roots"])
            and len(evaluation["roots"]) == len(set(evaluation["roots"]))
            and evaluation["generation"]
            == graph.get("project_generation", evaluation["generation"]),
            "invalid evaluation lifecycle transport metadata",
        )
    return {key: graph[key] for key in PROJECTION}


def terminal(graph: dict[str, Any], generation: int | None = None) -> None:
    _ = project_graph(graph)
    require(
        graph.get("required_failures") == [] and graph.get("errors") == {},
        "graph has failures",
    )
    require(
        graph.get("evaluation_running", False) is False
        and graph.get("evaluation_error") is None,
        "graph is running/failed",
    )
    require(
        bool(graph.get("states"))
        and all(state == "ready" for state in graph["states"].values()),
        "graph is not ready",
    )
    if generation is not None:
        require(
            type(generation) is int and 0 <= generation <= 2**53 - 1,
            "invalid accepted epoch",
        )
        require(
            type(graph.get("project_generation")) is int
            and graph.get("project_generation") == generation,
            "graph epoch differs",
        )


def seed_free(value: Any) -> None:
    if isinstance(value, dict):
        require(not FORBIDDEN.intersection(value), "computed state in authored recipe")
        for child in value.values():
            seed_free(child)
    elif isinstance(value, list):
        for child in value:
            seed_free(child)


def projected_ids(geometry: RawGeometry, gesture: dict[str, Any]) -> list[int]:
    matrix = finite(gesture["matrix"], (16,)).reshape(4, 4, order="F")
    endpoints = gesture["delivered"]
    require(
        len(endpoints) == 2
        and [p["type"] for p in endpoints] == ["pointerdown", "pointerup"],
        "missing actual pointer events",
    )
    require(
        all(p["button"] == 0 for p in endpoints)
        and endpoints[0]["pointerId"] == endpoints[1]["pointerId"],
        "pointer identity differs",
    )
    xy = finite([[p["x"], p["y"]] for p in endpoints], (2, 2))
    clip = (
        np.column_stack((geometry.positions, np.ones(len(geometry.positions))))
        @ matrix.T
    )
    require(
        np.isfinite(clip).all() and np.all(clip[:, 3] != 0), "invalid projected source"
    )
    ndc = clip[:, :3] / clip[:, 3, None]
    box = gesture["box"]
    screen = np.column_stack(
        (
            box["x"] + (ndc[:, 0] + 1) * gesture["width"] / 2,
            box["y"] + (1 - ndc[:, 1]) * gesture["height"] / 2,
        )
    )
    inside = (
        (clip[:, 3] > 0)
        & (ndc[:, 2] >= -1)
        & (ndc[:, 2] <= 1)
        & np.all(screen >= xy.min(axis=0), axis=1)
        & np.all(screen <= xy.max(axis=0), axis=1)
    )
    return np.flatnonzero(inside).tolist()


def fit_arithmetic(
    fit: dict[str, Any], geometry: RawGeometry, ids: list[int]
) -> dict[str, Any]:
    require(
        ids
        and ids == sorted(set(ids))
        and all(type(i) is int and 0 <= i < len(geometry.positions) for i in ids),
        "invalid selected IDs",
    )
    require(
        fit.get("kind") == "cylinder" and fit.get("ids") == ids, "fitted rows differ"
    )
    g = fit["analytic"]
    require(
        g.get("kind") == "cylinder" and g.get("slope") == 0, "fitted family differs"
    )
    origin, axis = finite(g["origin"], (3,)), finite(g["axis"], (3,))
    require(abs(np.linalg.norm(axis) - 1) <= 1e-10, "nonunit fitted axis")
    radius = g["radius"]
    require(
        type(radius) in (int, float) and math.isfinite(radius) and radius > 0,
        "invalid fitted radius",
    )
    domain = finite(g["axial_domain"], (2,))
    require(domain[0] < domain[1], "invalid fitted support")
    points, weights = geometry.positions[ids], geometry.weights[ids]
    require(np.all(weights > 0), "selected observation has no incident area")
    relative = points - origin
    heights = relative @ axis
    radial = relative - heights[:, None] * axis
    residuals = np.linalg.norm(radial, axis=1) - radius
    published = finite(fit["residuals"], (len(ids),))
    scale = np.linalg.norm(points, axis=1) + np.linalg.norm(origin) + radius
    epsilon = np.finfo(np.float64).eps
    gamma32 = 32 * epsilon / (1 - 32 * epsilon)
    np.testing.assert_allclose(
        published / scale, residuals / scale, rtol=1e-12, atol=gamma32
    )
    rms = float(np.sqrt(np.sum(weights * residuals**2) / np.sum(weights)))
    publication_rms = float(np.sqrt(np.sum(weights * published**2) / np.sum(weights)))
    observed_rms = finite(fit["weighted_rms"], ())
    np.testing.assert_allclose(
        observed_rms, publication_rms, rtol=1e-12, atol=gamma32 * float(scale.max())
    )
    np.testing.assert_allclose(
        observed_rms, rms, rtol=1e-12, atol=gamma32 * float(scale.max())
    )
    require(
        fit.get("support_origin") == "selection"
        and fit.get("support_intent") == {"kind": "selection"},
        "automatic support intent differs",
    )
    np.testing.assert_allclose(
        domain,
        [heights.min(), heights.max()],
        rtol=0,
        atol=gamma32 * float(scale.max()),
    )
    inside = int(np.count_nonzero((heights >= domain[0]) & (heights <= domain[1])))
    require(
        fit["support_classification"]
        == {
            "inside_vertices": inside,
            "outside_vertices": len(ids) - inside,
            "role": "selection-and-display",
        },
        "support acceptance classification differs",
    )
    return {
        "rows": len(ids),
        "weight_sum": float(weights.sum()),
        "weighted_rms": rms,
        "support_heights": [float(heights.min()), float(heights.max())],
        "inside_vertices": inside,
    }


def painted_membership(
    selection: dict[str, Any],
    source: dict[str, Any],
    vertices: int,
    *,
    nested_required: bool = False,
) -> dict[str, Any]:
    """Independently check original membership, not the UI's resolver helper."""
    require(selection["source"] == source["id"], "selection source differs")
    entries = selection.get("entries")
    membership = selection
    if entries is not None:
        require(
            isinstance(entries, list) and len(entries) == 1,
            "initial entry count differs",
        )
        require(
            "ids" not in selection and "depth" not in selection,
            "group contains flat membership",
        )
        require(isinstance(entries[0], dict), "invalid initial entry record")
        membership = cast(dict[str, Any], entries[0])
        require(membership.get("mode") == "painted", "initial entry is not painted")
        identifier = membership.get("id")
        require(
            isinstance(identifier, str)
            and 1 <= len(identifier) <= 64
            and all(
                char.isascii() and (char.isalnum() or char in "_-")
                for char in identifier
            ),
            "invalid initial entry identity",
        )
        binding = membership["scan"]
        require(
            binding["source_sha256"] == source["source_sha256"]
            and binding["reference_sha256"] == source["reference_sha256"]
            and type(binding["vertices"]) is int
            and binding["vertices"] == vertices
            and binding["descriptor"] == source["descriptor"],
            "selection entry source binding differs",
        )
        require(membership.get("volumes") == [], "painting created an implicit volume")
        require(
            membership.get("source_volume") is None,
            "painted entry chose a source volume",
        )
        require(membership.get("depth") == "through_all", "painted entry depth differs")
    else:
        require(not nested_required, "grouped selection profile requires an entry")
    ids = membership.get("ids")
    require(
        isinstance(ids, list)
        and len(ids) <= 25_000
        and all(type(value) is int and 0 <= value < vertices for value in ids)
        and ids == sorted(set(ids)),
        "invalid painted original-row membership",
    )
    return membership


def validate_observations(
    report: dict[str, Any], geometry: RawGeometry, raw: bytes
) -> dict[str, Any]:
    require(
        report.get("schema") == "scansor-raw-source-workflow-v1"
        and report.get("complete") is True
        and report.get("page_errors") == [],
        "workflow did not complete",
    )
    require(
        report["raw_source"]["sha256"] == sha(raw)
        and report["raw_source"]["bytes"] == len(raw),
        "raw input identity differs",
    )
    require(
        report["gesture"].get("frozen_z_band") == [27.8, 29.2],
        "registered raw gesture band differs",
    )
    imported = report["checkpoints"]["imported"]
    np.testing.assert_array_equal(
        finite(imported["positions"]).reshape(-1, 3), geometry.positions
    )
    np.testing.assert_array_equal(
        finite(imported["indices"]).reshape(-1, 3), geometry.triangles
    )
    require(
        imported["metadata"]["vertices"] == len(geometry.positions)
        and imported["metadata"]["triangles"] == len(geometry.triangles),
        "display source counts differ",
    )
    expected_ids = projected_ids(geometry, report["gesture"])
    require(
        expected_ids
        and len(expected_ids) < len(geometry.positions)
        and report["gesture"]["expected_ids"] == expected_ids,
        "projected pointer rows differ",
    )
    recipes = report["saved_recipe"]
    seed_free(recipes)
    checkpoints = report["checkpoints"]
    original_epoch = report["graph_generations"]["original"]
    empty, reopened = checkpoints["empty"], checkpoints["reopened"]
    for checkpoint, epoch in (
        (empty, original_epoch + 1),
        (reopened, original_epoch + 2),
    ):
        require(
            type(checkpoint["generation"]) is int
            and checkpoint["generation"] == epoch
            and type(checkpoint["metadata"]["project_generation"]) is int
            and checkpoint["metadata"]["project_generation"] == epoch
            and type(checkpoint["graph"].get("project_generation")) is int
            and checkpoint["graph"]["project_generation"] == epoch,
            "reset/reopen epoch differs",
        )
    require(
        empty["metadata"]["source"] is None
        and empty["graph"]["recipe"]["nodes"] == []
        and empty["graph"]["results"] == {},
        "New project retained old source/actions",
    )
    require(
        reopened["graph"]["recipe"] == recipes
        and reopened["graph"]["states"].get(report["fit_id"])
        in {"unevaluated", "stale"}
        and report["fit_id"] not in reopened["graph"]["results"],
        "Open actions retained computed fit",
    )
    require(
        reopened["metadata"]["source"]["filename"]
        != imported["metadata"]["source"]["filename"]
        and reopened["metadata"]["source"]["source_sha256"] == sha(raw),
        "located source did not preserve identity under rename",
    )
    results: dict[str, Any] = {}
    for name in ("original", "replayed"):
        graph, checkpoint = report["graphs"][name], report["checkpoints"][name]
        epoch = report["graph_generations"][name]
        require(
            type(checkpoint["generation"]) is int
            and checkpoint["generation"] == epoch
            and type(checkpoint["metadata"]["project_generation"]) is int
            and checkpoint["metadata"]["project_generation"] == epoch,
            "accepted metadata epoch differs",
        )
        terminal(graph, epoch)
        require(graph.get("evaluation_running") is False, "UI graph is not settled")
        require(
            checkpoint["graph"] == graph and graph["recipe"] == recipes,
            "saved/replayed graph definitions differ",
        )
        require(
            epoch
            == report["checkpoints"]["imported" if name == "original" else "reopened"][
                "generation"
            ],
            "graph belongs to another source acquisition",
        )
        nodes = {node["id"]: node for node in graph["recipe"]["nodes"]}
        require(
            len(graph["recipe"]["nodes"]) == len(nodes) == 3
            and sorted(node["operation"] for node in nodes.values())
            == ["fit", "selection", "source"],
            "workflow must contain only source, selection and surface fit",
        )
        sources = [node for node in nodes.values() if node["operation"] == "source"]
        require(len(sources) == 1, "workflow has multiple sources")
        source = sources[0]
        descriptor = source["descriptor"]
        require(
            source["source_sha256"] == descriptor["source_sha256"] == sha(raw)
            and descriptor["source_bytes"] == len(raw)
            and descriptor["vertices"] == len(geometry.positions)
            and descriptor["triangles"] == len(geometry.triangles),
            "authored source binding differs",
        )
        require(
            source["reference_sha256"]
            == sha(canonical({k: v for k, v in descriptor.items() if k != "filename"})),
            "authored interpretation binding differs",
        )
        actual_descriptor = checkpoint["metadata"]["source"]
        require(
            {k: v for k, v in actual_descriptor.items() if k != "filename"}
            == {k: v for k, v in descriptor.items() if k != "filename"},
            "accepted source differs",
        )
        selection, fit_node = nodes[report["selection_id"]], nodes[report["fit_id"]]
        membership = painted_membership(
            selection,
            source,
            len(geometry.positions),
            nested_required=checkpoint["metadata"]
            .get("selection_entries", {})
            .get("version")
            == 1,
        )
        require(
            selection["source"] == source["id"]
            and membership["ids"] == expected_ids
            and graph["memberships"][selection["id"]] == expected_ids
            and fit_node["selections"] == [selection["id"]]
            and fit_node["kind"] == "cylinder",
            "browser-authored selection/fit differs",
        )
        results[name] = fit_arithmetic(
            graph["results"][fit_node["id"]], geometry, expected_ids
        )
        if "worker_sources" in report:
            worker = report["worker_sources"][name]
            require(
                type(worker["project_generation"]) is int
                and worker["project_generation"] == epoch
                and worker["source_sha256"] == sha(raw)
                and worker["original_bytes"] == len(raw)
                and worker["owned_fs_files"] == []
                and worker["owned_fs_bytes"] == 0,
                "actual worker source epoch/staging differs",
            )
            replay, storage = worker["source_replay"], worker["source_storage"]
            require(
                replay["sha256"] == sha(raw)
                and replay["bytes"] == len(raw)
                and replay["eof_verified"] is True
                and 0 < replay["max_chunk_bytes"] <= 65_536
                and replay["read_calls"] > 0
                and storage["authority_bytes"] == len(raw)
                and storage["owned_source_payload_bytes"] == 0,
                "actual worker source replay authority differs",
            )
            require(
                {k: v for k, v in worker["descriptor"].items() if k != "filename"}
                == {k: v for k, v in descriptor.items() if k != "filename"},
                "worker descriptor differs from accepted recipe",
            )
            for field, expected in (
                ("positions", geometry.positions),
                ("normals", geometry.normals),
                ("triangles", geometry.triangles),
                ("weights", geometry.weights),
            ):
                observed = finite(worker[field], expected.shape)
                np.testing.assert_allclose(observed, expected, rtol=1e-12, atol=0)
            if "original_normals" in worker:
                np.testing.assert_array_equal(
                    finite(worker["original_normals"]), geometry.original_normals
                )
    require(
        report["graph_generations"]["replayed"]
        > report["graph_generations"]["original"],
        "source epoch did not advance",
    )
    return results


def checked_download(path: Path, record: dict[str, Any]) -> bytes:
    require(
        path.resolve() == Path(record["path"]).resolve()
        and path.name == record["name"],
        "download path/name differs",
    )
    raw = path.read_bytes()
    require(
        record["bytes"] == len(raw) and record["sha256"] == sha(raw),
        "download bytes differ",
    )
    return raw


def cad_readback(
    raw: bytes, report: dict[str, Any], geometry: RawGeometry, output_parent: Path
) -> dict[str, Any]:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(
            archive.namelist() == ["model.step", "metadata.json", "reference.ply"],
            "CAD ZIP members differ",
        )
        metadata = json.loads(archive.read("metadata.json"))
        reference = decode_ply(archive.read("reference.ply"), normals=False)
        step_bytes = archive.read("model.step")
    graph = report["graphs"]["original"]
    require(
        metadata.get("revision") == "scansor-cad-export-v1"
        and metadata["recipe"] == graph["recipe"]
        and metadata["source_sha256"] == report["raw_source"]["sha256"],
        "download metadata source/recipe differs",
    )
    require(
        metadata["units"] == "Millimeters"
        and metadata["geometry_mode"] == "separate_faces"
        and metadata["sewn"] is False
        and metadata["solid"] is False,
        "CAD export mode differs",
    )
    request = metadata["export"]
    require(
        request["token"] == graph["token"]
        and request["scope"] == "target"
        and request["target"] == report["fit_id"]
        and request["axis_up"] is False
        and request["transform"] is None
        and request["include_mesh"] is True,
        "CAD export request differs",
    )
    require(request["units"] == metadata["units"], "CAD unit declarations differ")
    np.testing.assert_array_equal(
        finite(metadata["transform_local_to_export"], (4, 4)), np.eye(4)
    )
    require(
        len(metadata["objects"]) == 1
        and metadata["objects"][0]["feature"] == report["fit_id"]
        and metadata["objects"][0]["surface_kind"] == "cylinder",
        "CAD object identity differs",
    )
    require(
        metadata["reference_mesh"]["file"] == "reference.ply"
        and metadata["reference_mesh"]["vertices"] == len(geometry.positions)
        and metadata["reference_mesh"]["triangles"] == len(geometry.triangles),
        "reference counts differ",
    )
    np.testing.assert_array_equal(reference.positions, geometry.positions)
    np.testing.assert_array_equal(reference.triangles, geometry.triangles)
    with tempfile.TemporaryDirectory(
        prefix="raw-step-", dir=output_parent
    ) as temporary:
        path = Path(temporary) / "model.step"
        _ = path.write_bytes(step_bytes)
        reader = STEPControl_Reader()
        read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
        require(
            read(str(path)) == IFSelect_RetDone and reader.TransferRoots() == 1,
            "STEP cannot be reopened",
        )
        shape = reader.OneShape()
        require(BRepCheck_Analyzer(shape).IsValid(), "STEP is invalid")
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        require(faces.More(), "STEP has no face")
        face = TopoDS.Face(faces.Current())
        faces.Next()
        require(not faces.More(), "STEP has extra faces")
        surface = BRepAdaptor_Surface(face)
        require(surface.GetType() == GeomAbs_Cylinder, "STEP family differs")
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        np.testing.assert_allclose(u1 - u0, 2 * np.pi, rtol=0, atol=1e-7)
        points = np.array(
            [
                surface.Value(float(u), v).Coord()
                for v in (v0, v1)
                for u in np.linspace(u0, u1, 7)
            ]
        )
        g = graph["results"][report["fit_id"]]["analytic"]
        axis, origin = finite(g["axis"], (3,)), finite(g["origin"], (3,))
        relative = points - origin
        h = relative @ axis
        radii = np.linalg.norm(relative - h[:, None] * axis, axis=1)
        np.testing.assert_allclose(
            [h.min(), h.max()], g["axial_domain"], rtol=0, atol=1e-7
        )
        np.testing.assert_allclose(radii, g["radius"], rtol=0, atol=1e-7)
        np.testing.assert_allclose(
            surface.Cylinder().Radius(), g["radius"], rtol=0, atol=1e-7
        )
        step_axis = surface.Cylinder().Axis()
        step_origin = np.array(step_axis.Location().Coord())
        step_direction = np.array(step_axis.Direction().Coord())
        centers = np.array(
            [
                step_origin
                + np.dot(
                    np.array(surface.Value(u0, v).Coord()) - step_origin, step_direction
                )
                * step_direction
                for v in (v0, v1)
            ]
        )
        centers = centers[np.argsort((centers - origin) @ axis)]
        np.testing.assert_allclose(
            centers,
            origin + finite(g["axial_domain"], (2,))[:, None] * axis,
            rtol=0,
            atol=1e-7,
        )
    return {
        "valid": True,
        "family": "cylinder",
        "radius": float(surface.Cylinder().Radius()),
        "axial_bounds": [float(h.min()), float(h.max())],
        "world_endpoint_centers": centers.tolist(),
        "angular_span": float(u1 - u0),
        "precision": 1e-7,
        "metadata": metadata,
    }


@contextmanager
def source_context(runtime: Path | None, parent: Path) -> Generator[Path | None]:
    """Import packaged code only after exact manifest/ZIP member verification."""
    if runtime is None:
        yield None
        return
    manifest = json.loads((runtime / "manifest.json").read_bytes())
    raw = (runtime / "scansor.zip").read_bytes()
    record = next(item for item in manifest["assets"] if item["path"] == "scansor.zip")
    require(
        record["bytes"] == len(raw) and record["sha256"] == sha(raw),
        "packaged source ZIP identity differs",
    )
    with tempfile.TemporaryDirectory(
        prefix="raw-native-source-", dir=parent
    ) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            records = {item["archive_path"]: item for item in manifest["source_files"]}
            require(
                len(records) == len(manifest["source_files"]) == len(members)
                and set(records) == {item.filename for item in members},
                "packaged source closure differs",
            )
            for item in members:
                require(
                    not item.is_dir()
                    and not stat.S_ISLNK(item.external_attr >> 16)
                    and not Path(item.filename).is_absolute()
                    and all(
                        part not in ("", ".", "..") for part in item.filename.split("/")
                    ),
                    "unsafe packaged source member",
                )
                data = archive.read(item)
                require(
                    sha(data) == records[item.filename]["sha256"],
                    "packaged source member differs",
                )
                path = root / item.filename
                path.parent.mkdir(parents=True, exist_ok=True)
                _ = path.write_bytes(data)
        sys.path.insert(0, str(root))
        try:
            yield root
        finally:
            sys.path.remove(str(root))


def native_replay(
    raw: bytes,
    report: dict[str, Any],
    result: dict[str, Any],
    packaged_root: Path | None,
) -> None:
    from experiments.browser_runtime import semantic_parity
    from experiments.feature_graph import Recipe
    from experiments.scan_project import ScanProject
    from experiments.scan_workspace import ScanLimits

    require(
        semantic_parity.policy_sha256() == POLICY_SHA256,
        "native semantic policy differs",
    )
    if packaged_root is not None:
        for name in (
            "experiments.feature_graph",
            "experiments.scan_project",
            "experiments.scan_workspace",
            "experiments.browser_runtime.semantic_parity",
        ):
            require(
                Path(str(sys.modules[name].__file__))
                .resolve()
                .is_relative_to(packaged_root),
                "native reference imported checkout instead of packaged source",
            )
    result["native_graphs"], result["native_generations"] = {}, {}
    result["native_source_checks"] = {}
    result["native_source_bindings"] = {}
    independent = decode_ply(raw)
    limits = ScanLimits(8 * 1024**2, 131_072, 262_144, 96 * 1024**2)
    for name in ("original", "replayed"):
        project = ScanProject()
        empty = project.accepted
        token = str(empty.graph.snapshot()["token"])
        filename = report["checkpoints"][
            "imported" if name == "original" else "reopened"
        ]["metadata"]["source"]["filename"]
        if name == "original":
            accepted = project.commit(
                project.prepare_add(
                    io.BytesIO(raw),
                    filename,
                    limits,
                    generation=empty.generation,
                    token=token,
                )
            )
            graph = accepted.graph.replace(
                Recipe.model_validate(report["graphs"][name]["recipe"]),
                str(accepted.graph.snapshot()["token"]),
            )
        else:
            accepted = project.commit_open(
                project.prepare_open(
                    report["saved_recipe"],
                    generation=empty.generation,
                    token=token,
                    confirmed_discard=True,
                    stream=io.BytesIO(raw),
                    filename=filename,
                    limits=limits,
                )
            )
            graph = accepted.graph.snapshot()
        for field, expected in (
            ("xyz", independent.positions),
            ("normals", independent.normals),
            ("triangles", independent.triangles),
            ("weights", independent.weights),
        ):
            np.testing.assert_allclose(
                getattr(accepted.workspace.data, field), expected, rtol=1e-12, atol=0
            )
        source = accepted.workspace.source
        require(source is not None, "native replayable source missing")
        assert source is not None
        digest = hashlib.sha256()
        source_bytes = 0
        with source.open() as stream:
            while chunk := stream.read(65_536):
                source_bytes += len(chunk)
                digest.update(chunk)
        require(
            source_bytes == len(raw) and digest.hexdigest() == sha(raw),
            "native source bytes differ",
        )
        result["native_source_checks"][name] = {
            "original_bytes": True,
            "positions": True,
            "normals": True,
            "triangles": True,
            "incident_weights": True,
        }
        try:
            _ = accepted.graph.evaluate(str(graph["token"]), all_actions=True)
        finally:
            observed: dict[str, Any] = dict(
                accepted.graph.readiness_snapshot(str(graph["token"]), all_actions=True)
            )
            result["native_graphs"][name] = observed
            result["native_generations"][name] = accepted.generation
        terminal(observed)
        owner = project.accepted
        owned: dict[str, Any] = dict(owner.graph.snapshot())
        require(
            owner is accepted and accepted.graph is owner.graph,
            "native accepted owner differs",
        )
        require(
            observed["recipe"] == owned["recipe"]
            and observed["token"] == owned["token"],
            "native snapshot is not owned graph",
        )
        source_nodes = [
            node
            for node in observed["recipe"]["nodes"]
            if node["operation"] == "source"
        ]
        require(len(source_nodes) == 1, "native source ownership differs")
        descriptor = accepted.workspace.descriptor
        if descriptor is None:
            raise ValueError("native source descriptor missing")
        require(
            source_nodes[0]["source_sha256"]
            == accepted.workspace.source_sha256
            == sha(raw)
            and source_nodes[0]["reference_sha256"]
            == accepted.workspace.reference_sha256
            and {
                k: v
                for k, v in source_nodes[0]["descriptor"].items()
                if k != "filename"
            }
            == {
                k: v
                for k, v in descriptor.model_dump(mode="json").items()
                if k != "filename"
            },
            "native source binding differs",
        )
        if "project_generation" in observed:
            require(
                type(observed["project_generation"]) is int
                and observed["project_generation"] == accepted.generation,
                "native snapshot epoch differs",
            )
        result["native_source_bindings"][name] = {
            "accepted_generation": accepted.generation,
            "owner_generation": owner.generation,
            "graph_is_owner": accepted.graph is owner.graph,
            "source_sha256": accepted.workspace.source_sha256,
            "source_descriptor": descriptor.model_dump(mode="json"),
            "reference_sha256": accepted.workspace.reference_sha256,
            "recipe": owned["recipe"],
            "token": owned["token"],
            "native_snapshot_has_generation": "project_generation" in observed,
        }
    cases = [
        {
            "id": "raw-" + name,
            "profile": "graph-snapshot",
            "path": "raw_workflow." + name,
            "actual": project_graph(report["graphs"][name]),
            "expected": project_graph(result["native_graphs"][name]),
        }
        for name in ("original", "replayed")
    ]
    for label, graphs in (
        ("ui", report["graphs"]),
        ("native", result["native_graphs"]),
    ):
        cases.append(
            {
                "id": "raw-" + label + "-replay-equivalence",
                "profile": "graph-snapshot",
                "path": "raw_workflow." + label + ".replay",
                "actual": project_graph(graphs["replayed"]),
                "expected": project_graph(graphs["original"]),
            }
        )
    raw_input = canonical({"cases": cases})
    result["semantic_parity_evidence"] = {
        "json": raw_input.decode(),
        "bytes": len(raw_input),
        "sha256": sha(raw_input),
    }
    try:
        result["semantic_parity"] = semantic_parity.compare_batch(cases)
    except semantic_parity.SemanticParityError as error:
        result["semantic_parity"] = error.receipt
        raise
    finally:
        result["semantic_parity"]["input_sha256"] = sha(raw_input)


def verify(
    raw_path: Path,
    report_path: Path,
    cad_path: Path,
    output: Path,
    runtime: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "scansor-raw-source-workflow-reference-v1",
        "complete": False,
        "checks": {},
        "scope": "Raw patch workflow evidence, not physical accuracy. Native weights are reconstructed independently, not captured from HTTP; worker vectors are checked only when actually observed.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    require(not output.exists(), "reference output must be fresh")
    try:
        raw, report = raw_path.read_bytes(), json.loads(report_path.read_bytes())
        require(sha(raw) == RAW_SHA256, "frozen raw bytes changed")
        geometry = decode_ply(raw)
        require(
            len(geometry.positions) == 24_999 and len(geometry.triangles) == 49_994,
            "frozen raw row counts changed",
        )
        result["observations"] = validate_observations(report, geometry, raw)
        result["checks"].update(
            raw_decode=True,
            incident_weights=True,
            all_selected_rows=True,
            residual_objective=True,
            support=True,
        )
        result["weight_capture"] = (
            "actual-worker-vectors"
            if "worker_sources" in report
            else "independent-raw-and-native-reconstruction; no HTTP weight-vector capture"
        )
        actions = checked_download(
            Path(report["downloads"]["actions"]["path"]), report["downloads"]["actions"]
        )
        require(
            json.loads(actions) == report["saved_recipe"], "downloaded actions differ"
        )
        cad = checked_download(cad_path, report["downloads"]["cad"])
        result["cad"] = cad_readback(cad, report, geometry, output.parent)
        result["checks"].update(
            download_metadata=True, reference_mesh=True, step_readback=True
        )
        with source_context(runtime, output.parent) as root:
            native_replay(raw, report, result, root)
        result["checks"].update(native_replay=True, semantic_parity=True)
        result["complete"] = True
    except Exception as error:
        result["failure"] = f"{type(error).__name__}: {error}"
    _ = output.write_text(json.dumps(result, allow_nan=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("raw", "report", "cad", "output"):
        _ = parser.add_argument("--" + name, type=Path, required=True)
    _ = parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()
    result = verify(args.raw, args.report, args.cad, args.output, args.runtime)
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "checks": result["checks"],
                **({"failure": result["failure"]} if "failure" in result else {}),
            }
        )
    )
    if not result["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
