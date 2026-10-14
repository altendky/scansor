"""Owned synthetic CAD regression fixtures; never application example sources.

The same production geometry calls run natively and in a serial browser worker.
No pytest imports or thread creation are required. JSON metrics are separate from
STEP artifacts, whose timestamps/serialization are not compared byte-for-byte.
"""

from __future__ import annotations

import base64
import io
import json
import math
from contextvars import copy_context
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import OCP
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.collections import Sequence_TDF_Label
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from experiments.body_geometry import BodyAssemblyError, assemble_body, body_from_record
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.general_face_geometry import (
    classify_face_points,
    face_from_boundaries,
    intersection_record,
)
from experiments.general_face_geometry import (
    face_from_record as loop_face,
)
from experiments.native_replay import native_replay_scope
from experiments.nozzle_cad import (
    CadExportRequest,
    _step_bytes,  # pyright: ignore[reportPrivateUsage]
    export_cad,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.ocp_geometry import face_from_record, tessellate_face, transform_shape
from experiments.surface_extents import circle_intersection, trimmed_face
from scansor._plyio import Reader, build_layout, read_header

UNITS = {
    "Millimeters": (0.001, "SI_UNIT(.MILLI.,.METRE.)"),
    "Centimeters": (0.01, "SI_UNIT(.CENTI.,.METRE.)"),
    "Meters": (1.0, "SI_UNIT($,.METRE.)"),
}
_operation = 0


def area(shape: Any) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return properties.Mass()


def volume(shape: Any) -> float:
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def shape_metrics(shape: Any) -> dict[str, Any]:
    box = Bnd_Box()
    # Match native STEP round-trip tests: compare geometric extrema, rather than
    # tolerance inflation that can change when topology is serialized.
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    face_count = 0
    while faces.More():
        face_count += 1
        faces.Next()
    return {
        "valid": BRepCheck_Analyzer(shape).IsValid(),
        "area": area(shape),
        "volume": volume(shape),
        "bounds": [*box.CornerMin().Coord(), *box.CornerMax().Coord()],
        "solid": shape.ShapeType() == TopAbs_SOLID,
        "face_count": face_count,
    }


def _plane(
    axis: tuple[float, float, float] = (0, 0, 1), offset: float = 0
) -> dict[str, Any]:
    vector = np.asarray(axis, dtype=float)
    vector /= np.linalg.norm(vector)
    return {"kind": "plane", "axis": vector.tolist(), "offset": offset}


def _cylinder(
    radius: float, origin: tuple[float, float, float] = (0, 0, 0)
) -> dict[str, Any]:
    return {
        "kind": "cylinder",
        "origin": list(origin),
        "axis": [0, 0, 1],
        "radius": radius,
        "slope": 0,
    }


def _use(keep: str, number: int) -> dict[str, Any]:
    return {"keep": keep, "intersection": f"edge-{number}"}


def _bounded_side(slope: float) -> dict[str, Any]:
    surface = {
        "kind": "cone" if slope else "cylinder",
        "parameters": [1, 2, 0, 0, 3, 0, slope],
    }
    boundaries: list[tuple[dict[str, str], dict[str, Any]]] = []
    for value, keep in ((1, "positive"), (4, "negative")):
        plane = {"kind": "plane", "plane_equation": [0, 0, 1, value]}
        boundaries.append(
            (
                {"intersection": str(value), "keep": keep},
                circle_intersection(surface, plane),
            )
        )
    return trimmed_face(surface, boundaries, np.empty((0, 3)))


def hollow_faces() -> dict[str, dict[str, Any]]:
    """The actual annular-cylinder records used by native body tests."""
    geometry = {
        "origin": [0, 0, 0],
        "axis": [0, 0, 1],
        "basis_u": [1, 0, 0],
        "basis_v": [0, 1, 0],
        "radius": 2,
        "slope": 0,
    }
    return {
        "outer": {
            "surface_kind": "cylinder",
            "geometry": geometry,
            "bounded": True,
            "bounds": {"axial": [0, 3]},
        },
        "inner": {
            "surface_kind": "cylinder",
            "geometry": {**geometry, "radius": 1},
            "bounded": True,
            "bounds": {"axial": [0, 3]},
        },
        "bottom": {
            "surface_kind": "plane",
            "geometry": geometry,
            "bounded": True,
            "bounds": {"radial": [1, 2]},
        },
        "top": {
            "surface_kind": "plane",
            "geometry": {**geometry, "origin": [0, 0, 3]},
            "bounded": True,
            "bounds": {"radial": [1, 2]},
        },
    }


def _step_shapes(data: bytes, units: str) -> dict[str, Any]:
    factor, token = UNITS[units]
    assert token in data.decode("utf-8")
    with TemporaryDirectory(prefix="scansor-cad-read-") as temporary:
        path = Path(temporary) / "model.step"
        _ = path.write_bytes(data)
        reader = STEPCAFControl_Reader()
        assert reader.ReadFile(str(path)) == IFSelect_RetDone  # pyright: ignore[reportUnknownMemberType]
        document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        XCAFDoc_DocumentTool.SetLengthUnit_s(document, factor)
        assert reader.Transfer(document)
        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        labels = Sequence_TDF_Label()
        tool.GetFreeShapes(labels)
        shapes = {}
        for index in range(1, labels.Length() + 1):
            label, name = labels.Value(index), TDataStd_Name()
            assert label.FindAttribute(TDataStd_Name.GetID_s(), name)
            text = name.Get().ToExtString()
            assert text not in shapes
            shapes[text] = tool.GetShape_s(label)
        return shapes


def inspect_bundle(
    data: bytes, workspace: NozzleWorkspace | None = None
) -> dict[str, Any]:
    """Reopen exported STEP and validate float64 reference mesh/provenance."""
    with ZipFile(io.BytesIO(data)) as bundle:
        metadata = json.loads(bundle.read("metadata.json"))
        shapes = _step_shapes(bundle.read("model.step"), metadata["units"])
        expected = {item["name"] for item in metadata["objects"]}
        assert set(shapes) == expected
        metrics = {name: shape_metrics(shape) for name, shape in shapes.items()}
        assert all(item["valid"] for item in metrics.values())
        result = {"metadata": metadata, "shapes": metrics, "reference": None}
        if metadata["reference_mesh"] is not None:
            stream = io.BytesIO(bundle.read("reference.ply"))
            layout = build_layout(
                read_header(stream), fixed_lists={("face", "vertex_indices"): 3}
            )
            reader = Reader(stream, layout, max_range_bytes=max(layout.byte_count, 1))
            vertices = reader.read_range(
                "vertex", 0, layout.element("vertex").element.count
            )
            triangles = reader.read_range(
                "face", 0, layout.element("face").element.count
            )
            positions = np.column_stack([vertices[name] for name in ("x", "y", "z")])
            indices = triangles["vertex_indices"]["values"]
            assert positions.dtype == np.dtype("<f8")
            reference: dict[str, Any] = {
                "dtype": str(positions.dtype),
                "vertices": len(positions),
                "triangles": len(indices),
                "max_source_difference": None,
            }
            if workspace is not None:
                assert metadata["source_sha256"] == workspace.default.source_sha256
                matrix = np.asarray(
                    metadata["transform_local_to_export"], dtype=np.float64
                )
                expected_positions = workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
                np.testing.assert_allclose(
                    positions, expected_positions, rtol=0, atol=1e-11
                )
                np.testing.assert_array_equal(indices, workspace.data.triangles)
                reference["max_source_difference"] = float(
                    np.max(np.abs(positions - expected_positions))
                )
            result["reference"] = reference
        assert set(bundle.namelist()) == {
            "model.step",
            "metadata.json",
            *(["reference.ply"] if result["reference"] else []),
        }
        return result


def body_export_cases(matrix: np.ndarray) -> tuple[dict[str, Any], dict[str, str]]:
    """Inject owned analytic records into a real source-bound evaluated snapshot.

    This follows native body export tests. The injected authoring/results prove
    the existing conservative export path, not a closed-body UI fitting recipe.
    """
    example = (
        Path(__file__).resolve().parents[2] / "examples/nozzle-bayonette-simplified"
    )
    workspace = NozzleWorkspace(example)
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate_json((example / "recipes/cone-plane.json").read_text()),
    )
    snapshot = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    for identifier in ("first", "second"):
        snapshot["recipe"]["nodes"].append(
            {
                "id": identifier,
                "label": f"Injected boundary {identifier}",
                "operation": "surface_intersection",
                "first": {"feature": "fit", "surface": "end"},
                "second": {"feature": "fit", "surface": "side"},
            }
        )
        snapshot["states"][identifier] = "ready"
        snapshot["results"][identifier] = {"kind": "circle"}
    records = {
        identifier: {"kind": "trimmed_face", **record}
        for identifier, record in hollow_faces().items()
    }
    for identifier, record in records.items():
        snapshot["recipe"]["nodes"].append(
            {
                "id": identifier,
                "label": f"Injected face {identifier}",
                "operation": "trimmed_face",
                "surface": {
                    "feature": "fit",
                    "surface": "end" if record["surface_kind"] == "plane" else "side",
                },
                "boundaries": [
                    {"intersection": "first", "keep": "outside"},
                    {"intersection": "second", "keep": "inside"},
                ],
            }
        )
        snapshot["states"][identifier] = "ready"
        snapshot["results"][identifier] = record
    body = assemble_body(records, 1e-7)
    label = "中空体 Ø4 — café"
    snapshot["recipe"]["nodes"].append(
        {
            "id": "body",
            "label": label,
            "operation": "body",
            "faces": list(records),
            "sewing_tolerance": 1e-7,
        }
    )
    snapshot["states"]["body"] = "ready"
    snapshot["results"]["body"] = body
    # As in native tests, retain a structurally valid output authoring chain
    # and inject its already-solved numeric similarity transform explicitly.
    transform_nodes = [
        {
            "id": "p0",
            "label": "Injected origin",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
        {
            "id": "p1",
            "label": "Injected distance",
            "operation": "point",
            "initial_coordinates": [1, 0, 0],
        },
        {"id": "a0", "label": "Injected Z", "operation": "axis", "source_fit": "side"},
        {
            "id": "a1",
            "label": "Injected X",
            "operation": "axis",
            "source_points": ["p0", "p1"],
        },
        {
            "id": "frame",
            "label": "Injected frame",
            "operation": "frame",
            "origin_point": "p0",
            "primary_reference": "a0",
            "primary_output_axis": "+Z",
            "secondary_reference": "a1",
            "secondary_output_axis": "+X",
        },
        {
            "id": "scale",
            "label": "Injected scale",
            "operation": "scale",
            "distances": [
                {"first_point": "p0", "second_point": "p1", "known_distance": 2.0}
            ],
        },
        {
            "id": "output_transform",
            "label": "Injected transform",
            "operation": "transform",
            "frame": "frame",
            "scale": "scale",
        },
    ]
    for node in transform_nodes:
        snapshot["recipe"]["nodes"].append(node)
        snapshot["states"][node["id"]] = "ready"
    snapshot["results"]["output_transform"] = {"matrix": matrix.tolist()}
    recipe = Recipe.model_validate(snapshot["recipe"])
    snapshot["recipe"] = recipe.model_dump(mode="json")
    snapshot["token"] = sha256(recipe.model_dump_json().encode()).hexdigest()
    metrics, artifacts = {}, {}
    for units in UNITS:
        request = CadExportRequest(
            token=snapshot["token"],
            scope="body",
            target="body",
            units=cast(Any, units),
            axis_up=False,
            transform="output_transform",
            include_mesh=units == "Centimeters",
        )
        data = export_cad(workspace, snapshot, request)
        inspected = inspect_bundle(data, workspace)
        metadata, shapes = inspected["metadata"], inspected["shapes"]
        assert (
            metadata["solid"]
            and metadata["sewn"]
            and metadata["geometry_mode"] == "solid"
        )
        assert metadata["recipe"] == snapshot["recipe"]
        assert metadata["export"] == request.model_dump(mode="json")
        assert metadata["source_sha256"] == workspace.default.source_sha256
        np.testing.assert_array_equal(metadata["transform_local_to_export"], matrix)
        item = metadata["objects"][0]
        assert (
            item["source_faces"] == list(records) and item["sewing_tolerance"] == 1e-7
        )
        assert item["input_fingerprint"] == body["input_fingerprint"]
        assert math.isclose(item["volume_local"], 9 * math.pi, rel_tol=1e-8)
        assert set(shapes) == {label}
        assert shapes[label]["solid"] and shapes[label]["face_count"] == 4
        assert math.isclose(shapes[label]["volume"], 72 * math.pi, rel_tol=1e-8)
        metrics[f"body_zip_{units}"] = inspected
        artifacts[units] = base64.b64encode(data).decode("ascii")
    rejected = {}
    request = CadExportRequest(
        token=snapshot["token"],
        scope="body",
        target="body",
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    for failure in ("failed", "open", "changed_tolerance"):
        invalid = json.loads(json.dumps(snapshot))
        if failure == "failed":
            invalid["states"]["body"] = "failed"
        elif failure == "open":
            invalid["results"]["body"] = {"kind": "body", "valid": False}
        else:
            next(node for node in invalid["recipe"]["nodes"] if node["id"] == "body")[
                "sewing_tolerance"
            ] = 1e-6
        try:
            _ = export_cad(workspace, invalid, request)
        except ValueError as error:
            assert "cannot export Body" in str(error)
            rejected[failure] = str(error)
        else:
            raise AssertionError(f"Body export accepted {failure}")
    metrics["body_zip_rejections"] = rejected
    return metrics, artifacts


def run_cases() -> dict[str, Any]:
    """Return JSON-safe deterministic metrics plus separate STEP artifacts."""
    global _operation
    _operation += 1
    cases: dict[str, Any] = {}
    for slope in (0.0, 0.2, -0.2, 1e-10):
        record = _bounded_side(slope)
        face = face_from_record(json.loads(json.dumps(record)))
        expected = math.pi * (6 + 5 * slope) * 3 * math.sqrt(1 + slope * slope)
        mesh = tessellate_face(face, 3.0)
        assert BRepCheck_Analyzer(face).IsValid() and math.isclose(
            area(face), expected, rel_tol=1e-8
        )
        cases[f"lateral_{slope}"] = {
            "area": area(face),
            "expected_area": expected,
            "bounds": record["bounds"],
            "bounded": record["bounded"],
            "mesh_triangles": len(mesh["indices"]) // 3,
        }
    plane = _plane()
    intersections = [
        intersection_record(plane, _cylinder(10)),
        *[intersection_record(plane, _cylinder(1, (x, 0, 0))) for x in (-3, 3)],
    ]
    record = face_from_boundaries(
        plane,
        [
            (_use("inside", 0), intersections[0]),
            *[
                (_use("outside", index), edge)
                for index, edge in enumerate(intersections[1:], 1)
            ],
        ],
        np.array([[999.0, 999.0, 999.0]]),
    )
    face = loop_face(json.loads(json.dumps(record)))
    interior, boundary = classify_face_points(
        record, np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    )
    assert math.isclose(area(face), 98 * math.pi, rel_tol=1e-8)
    assert interior.tolist() == [True, False, False] and boundary.tolist() == [
        False,
        False,
        True,
    ]
    cases["two_holes"] = {
        "area": area(face),
        "interior": interior.tolist(),
        "boundary": boundary.tolist(),
    }
    oblique = _plane((0.4, 0, 1))
    ellipse = intersection_record(oblique, _cylinder(2))
    record = face_from_boundaries(oblique, [(_use("inside", 0), ellipse)])
    face = loop_face(record)
    assert ellipse["curves"][0]["kind"] == "ellipse"
    assert math.isclose(area(face), 4 * math.pi * math.sqrt(1.16), rel_tol=1e-8)
    cases["oblique_ellipse"] = {"area": area(face), "kind": "ellipse"}
    side = _cylinder(2)
    edges = [
        intersection_record(side, p)
        for p in (_plane((0.4, 0, 1), 4), _plane((-0.2, 0, 1), 0))
    ]
    record = face_from_boundaries(
        side, [(_use("negative", 0), edges[0]), (_use("positive", 1), edges[1])]
    )
    face = loop_face(record)
    assert (
        record["bounded"]
        and not record["preview_clipped"]
        and BRepCheck_Analyzer(face).IsValid()
    )
    cases["periodic_band"] = {"area": area(face), "bounds": record["bounds"]}

    records = hollow_faces()
    body = assemble_body(records, 1e-7)
    solid = body_from_record(body, records)
    assert (
        math.isclose(volume(solid), 9 * math.pi, rel_tol=1e-8)
        and body["face_count"] == 4
    )
    cases["hollow_body"] = {
        "volume": volume(solid),
        "valid": BRepCheck_Analyzer(solid).IsValid(),
        "face_count": body["face_count"],
        "sewing_tolerance": body["sewing_tolerance"],
        "input_fingerprint": body["input_fingerprint"],
    }
    try:
        _ = assemble_body(
            {key: value for key, value in records.items() if key != "top"}, 1e-7
        )
    except BodyAssemblyError as error:
        free = [
            item for item in error.diagnostic["problems"] if item["code"] == "free_edge"
        ]
        assert free and all(
            item["source_faces"] and item["preview"]["positions"] for item in free
        )
        cases["open_body_rejection"] = {"free_edges": len(free), "source_linked": True}
    else:
        raise AssertionError("open body was accepted")

    with native_replay_scope():
        first = body_from_record(body, records)
        with native_replay_scope():
            nested = body_from_record(body, records)
        assert first.IsSame(nested)
        disposed_context = copy_context()
    with native_replay_scope():
        later = body_from_record(body, records)
    assert not first.IsSame(later)
    copied = disposed_context.run(body_from_record, body, records)
    assert not first.IsSame(copied)
    interrupted = None
    try:
        with native_replay_scope():
            interrupted = body_from_record(body, records)
            raise RuntimeError("disposable replay pass failed")
    except RuntimeError:
        pass
    with native_replay_scope():
        recovered = body_from_record(body, records)
    assert (
        interrupted is not None
        and not interrupted.IsSame(recovered)
        and math.isclose(volume(recovered), 9 * math.pi, rel_tol=1e-8)
    )
    cases["serial_ownership"] = {
        "same_nested_pass": True,
        "later_reconstructed": True,
        "disposed_context_reconstructed": True,
        "exception_cleanup": True,
    }

    matrix = np.array(
        [
            [0.0, -2.0, 0.0, 10.0],
            [2.0, 0.0, 0.0, -20.0],
            [0.0, 0.0, 2.0, 3.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    transformed = transform_shape(solid, matrix)
    assert math.isclose(volume(transformed), 8 * volume(solid), rel_tol=1e-8)
    assert math.isclose(area(transformed), 4 * area(solid), rel_tol=1e-8)
    cases["uniform_transform"] = shape_metrics(transformed)
    body_metrics, body_artifacts = body_export_cases(matrix)
    cases.update(body_metrics)
    _ = STEPCAFControl_Writer()
    previous = Interface_Static.CVal_s("write.step.unit")
    artifacts = {}
    for units in UNITS:
        label = "肩面 Ø12 — café"
        data = _step_bytes({"body": transformed}, {"body": label}, units)
        assert Interface_Static.CVal_s("write.step.unit") == previous
        shapes = _step_shapes(data, units)
        assert set(shapes) == {label}
        metrics = shape_metrics(shapes[label])
        assert metrics["valid"] and metrics["solid"]
        assert math.isclose(metrics["volume"], 72 * math.pi, rel_tol=1e-8)
        cases[f"step_{units}"] = {
            **metrics,
            "name": label,
            "unit_setting_restored": True,
        }
        artifacts[units] = base64.b64encode(data).decode("ascii")
    return {
        "operation": _operation,
        "version": OCP.__version__,
        "inventory": "synthetic direct geometry; not UI-authored nozzle bodies",
        "cases": cases,
        "step_base64": artifacts,
        "body_zip_base64": body_artifacts,
    }
