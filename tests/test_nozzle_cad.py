"""STEP/PLY round trips preserve analytic patches, physical bounds and metadata."""

import io
import json
import subprocess
import sys
from collections.abc import Callable, Generator
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.collections import Sequence_TDF_Label
from OCP.GeomAbs import (
    GeomAbs_Circle,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
)
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.Standard import Standard_ConstructionError
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_cad import (
    CadExportRequest,
    export_cad,
    export_face_targets,
    joint_shapes,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.ocp_geometry import face_from_record, surface_patch
from scansor._plyio import Reader, build_layout, read_header


def faces(shape: TopoDS_Shape) -> list[Any]:
    result: list[Any] = []
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        result.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return result


def circles(shape: TopoDS_Shape) -> list[Any]:
    result: list[Any] = []
    explorer = TopExp_Explorer(shape, TopAbs_EDGE)
    while explorer.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(explorer.Current()))
        if curve.GetType() == GeomAbs_Circle:
            result.append(curve.Circle())
        explorer.Next()
    return result


def bounds(shape: TopoDS_Shape) -> np.ndarray:
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    return np.array([box.CornerMin().Coord(), box.CornerMax().Coord()])


def samples(shape: TopoDS_Shape) -> np.ndarray:
    face = faces(shape)[0]
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    surface = BRepAdaptor_Surface(face)
    return np.array(
        [
            surface.Value(u0 + (u1 - u0) * a, v0 + (v1 - v0) * b).Coord()
            for a, b in [(0, 0), (0.2, 0.4), (0.8, 1), (1, 1)]
        ]
    )


def roundtrip(
    data: bytes, temporary: Path
) -> tuple[
    dict[str, Any], dict[str, TopoDS_Shape], tuple[np.ndarray, np.ndarray] | None, str
]:
    with ZipFile(io.BytesIO(data)) as bundle:
        metadata = json.loads(bundle.read("metadata.json"))
        step = bundle.read("model.step")
        path = temporary / "model.step"
        _ = path.write_bytes(step)
        reader = STEPCAFControl_Reader()
        read_file = cast(
            Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile
        )
        assert read_file(str(path)) == IFSelect_RetDone
        unit = {"Millimeters": 0.001, "Centimeters": 0.01, "Meters": 1.0}[
            metadata["units"]
        ]
        document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        XCAFDoc_DocumentTool.SetLengthUnit_s(document, unit)
        assert reader.Transfer(document)
        tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        labels = Sequence_TDF_Label()
        tool.GetFreeShapes(labels)
        shapes = {}
        for i in range(1, labels.Length() + 1):
            label = labels.Value(i)
            name = TDataStd_Name()
            assert label.FindAttribute(TDataStd_Name.GetID_s(), name)
            shapes[name.Get().ToExtString()] = tool.GetShape_s(label)
        mesh = None
        if "reference.ply" in bundle.namelist():
            stream = io.BytesIO(bundle.read("reference.ply"))
            layout = build_layout(
                read_header(stream), fixed_lists={("face", "vertex_indices"): 3}
            )
            ply = Reader(stream, layout, max_range_bytes=max(layout.byte_count, 1))
            vertices = ply.read_range(
                "vertex", 0, layout.element("vertex").element.count
            )
            triangles = ply.read_range("face", 0, layout.element("face").element.count)
            mesh = (
                np.column_stack([vertices[n] for n in ("x", "y", "z")]),
                triangles["vertex_indices"]["values"],
            )
        assert set(bundle.namelist()) == {
            "metadata.json",
            "model.step",
            *(["reference.ply"] if mesh is not None else []),
        }
    return metadata, shapes, mesh, step.decode("utf-8")


def trimmed_face(kind: str, lo: float, hi: float) -> dict[str, Any]:
    return {
        "kind": "trimmed_face",
        "surface_kind": kind,
        "geometry": {
            "origin": [1.0, -2.0, 3.0],
            "axis": [0.0, 0.0, 1.0],
            "basis_u": [1.0, 0.0, 0.0],
            "basis_v": [0.0, 1.0, 0.0],
            "radius": 2.0,
            "slope": 0.2 if kind == "cone" else 0.0,
        },
        "bounds": {"radial" if kind == "plane" else "axial": [lo, hi]},
        "bounded": True,
        "boundary_ids": ["first", "second"],
        "boundary_uses": [
            {"intersection": "first", "keep": "outside"},
            {"intersection": "second", "keep": "inside"},
        ],
    }


@pytest.fixture(scope="module")
def evaluated() -> tuple[NozzleWorkspace, dict[str, Any]]:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate_json((example / "recipes/cone-plane.json").read_text()),
    )
    return workspace, cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]))
    )


@pytest.fixture
def initialized_step_unit() -> Generator[str]:
    # The first writer registers the native setting with an MM default.
    _ = STEPCAFControl_Writer()
    previous = Interface_Static.CVal_s("write.step.unit")
    try:
        assert Interface_Static.SetCVal_s("write.step.unit", "CM")
        yield "CM"
    finally:
        assert Interface_Static.SetCVal_s("write.step.unit", previous)


def test_step_cold_start_initializes_default_unit() -> None:
    # Use a fresh process so preceding tests cannot initialize the native writer.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.Interface import Interface_Static
from OCP.STEPCAFControl import STEPCAFControl_Writer
from experiments.nozzle_cad import _step_bytes

assert Interface_Static.CVal_s("write.step.unit") == ""
data = _step_bytes(
    {"box": BRepPrimAPI_MakeBox(1, 2, 3).Shape()}, {"box": "Box"}, "Meters"
)
assert b"SI_UNIT($,.METRE.)" in data
assert Interface_Static.CVal_s("write.step.unit") == "MM"
_ = STEPCAFControl_Writer()
assert Interface_Static.CVal_s("write.step.unit") == "MM"
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def collection(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
) -> tuple[NozzleWorkspace, dict[str, Any]]:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    for key in ("first", "second"):
        snapshot["recipe"]["nodes"].append(
            {
                "id": key,
                "label": f"Boundary {key}",
                "operation": "surface_intersection",
                "first": {"feature": "fit", "surface": "end"},
                "second": {"feature": "fit", "surface": "side"},
            }
        )
        snapshot["states"][key] = "ready"
        snapshot["results"][key] = {"kind": "circle"}
    for key, kind, lo, hi in (
        ("shoulder_face", "plane", 1.0, 3.0),
        ("outer_face", "cylinder", -1.0, 2.0),
        ("other_face", "plane", 0.0, 1.0),
    ):
        record = trimmed_face(kind, lo, hi)
        if key == "other_face":
            record["geometry"]["origin"] = [-4.0, 2.0, 1.0]
        snapshot["recipe"]["nodes"].append(
            {
                "id": key,
                "label": key.replace("_", " ").title(),
                "operation": "trimmed_face",
                "surface": {
                    "feature": "fit",
                    "surface": "end" if kind == "plane" else "side",
                },
                "boundaries": record["boundary_uses"],
            }
        )
        snapshot["states"][key] = "ready"
        snapshot["results"][key] = record
    _ = Recipe.model_validate(snapshot["recipe"])
    return workspace, snapshot


def test_step_object_labels_preserve_unicode_and_ascii(
    collection: tuple[NozzleWorkspace, dict[str, Any]], tmp_path: Path
) -> None:
    workspace, snapshot = collection
    labels = {
        "shoulder_face": "Build faces 16 · bore",
        "outer_face": "肩面 Ø12 — café",
        "other_face": "ASCII face",
    }
    for node in snapshot["recipe"]["nodes"]:
        if node["id"] in labels:
            node["label"] = labels[node["id"]]
    request = CadExportRequest(
        token=snapshot["token"],
        scope="all_faces",
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert set(shapes) == set(labels.values())
    assert {obj["feature"]: obj["name"] for obj in metadata["objects"]} == labels
    assert mesh is None


@pytest.mark.parametrize(
    "units,step_unit",
    [
        ("Millimeters", "SI_UNIT(.MILLI.,.METRE.)"),
        ("Centimeters", "SI_UNIT(.CENTI.,.METRE.)"),
        ("Meters", "SI_UNIT($,.METRE.)"),
    ],
)
@pytest.mark.parametrize("explicit_transform", [False, True])
def test_all_faces_named_step_mesh_units_and_common_transform(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    units: Any,
    step_unit: str,
    explicit_transform: bool,
    tmp_path: Path,
) -> None:
    workspace, snapshot = collection
    transform = None
    if explicit_transform:
        transform = "output_transform"
        # A valid authoring chain whose numeric results are already retained.
        for node in (
            {
                "id": "p0",
                "label": "Origin",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
            {
                "id": "p1",
                "label": "Scale point",
                "operation": "point",
                "initial_coordinates": [1, 0, 0],
            },
            {"id": "a0", "label": "Z", "operation": "axis", "source_fit": "side"},
            {
                "id": "a1",
                "label": "X",
                "operation": "axis",
                "source_points": ["p0", "p1"],
            },
            {
                "id": "frame",
                "label": "Frame",
                "operation": "frame",
                "origin_point": "p0",
                "primary_reference": "a0",
                "primary_output_axis": "+Z",
                "secondary_reference": "a1",
                "secondary_output_axis": "+X",
            },
            {
                "id": "scale",
                "label": "Scale",
                "operation": "scale",
                "distances": [
                    {"first_point": "p0", "second_point": "p1", "known_distance": 2.0}
                ],
            },
            {
                "id": transform,
                "label": "Output transform",
                "operation": "transform",
                "frame": "frame",
                "scale": "scale",
            },
        ):
            snapshot["recipe"]["nodes"].append(node)
            snapshot["states"][node["id"]] = "ready"
        snapshot["results"][transform] = {
            "matrix": [[0, -2, 0, 7], [2, 0, 0, -3], [0, 0, 2, 5], [0, 0, 0, 1]]
        }
    request = CadExportRequest(
        token=snapshot["token"],
        scope="all_faces",
        units=units,
        axis_up=False,
        transform=transform,
    )
    metadata, shapes, mesh, step = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    expected_ids = ["shoulder_face", "outer_face", "other_face"]
    assert [obj["feature"] for obj in metadata["objects"]] == expected_ids
    assert set(shapes) == {"Shoulder Face", "Outer Face", "Other Face"}
    assert step_unit in step
    assert metadata["geometry_mode"] == "separate_faces"
    assert metadata["sewn"] is False and metadata["solid"] is False
    matrix = np.asarray(metadata["transform_local_to_export"])
    if not explicit_transform:
        np.testing.assert_array_equal(matrix[:3, :3], workspace.frame)
        np.testing.assert_array_equal(matrix[:3, 3], workspace.origin)
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    np.testing.assert_array_equal(mesh[1], workspace.data.triangles)
    from experiments.ocp_geometry import transform_shape

    for obj in metadata["objects"]:
        key = obj["feature"]
        shape = shapes[obj["name"]]
        assert len(faces(shape)) == 1 and BRepCheck_Analyzer(shape).IsValid()
        assert not TopExp_Explorer(shape, TopAbs_SOLID).More()
        assert obj["extent_authority"] == "declared_boundaries"
        assert (
            obj["source_surface"]
            == next(n for n in snapshot["recipe"]["nodes"] if n["id"] == key)["surface"]
        )
        np.testing.assert_allclose(
            bounds(shape),
            bounds(transform_shape(face_from_record(snapshot["results"][key]), matrix)),
            atol=1e-8,
            rtol=0,
        )


def test_selected_faces_deduplicated_recipe_order_no_sources_exported(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    tmp_path: Path,
) -> None:
    workspace, snapshot = collection
    snapshot["states"]["other_face"] = "failed"
    request = CadExportRequest(
        token=snapshot["token"],
        scope="selected_faces",
        targets=["outer_face", "shoulder_face", "outer_face"],
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    assert export_face_targets(snapshot, request) == ["shoulder_face", "outer_face"]
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert [obj["feature"] for obj in metadata["objects"]] == [
        "shoulder_face",
        "outer_face",
    ]
    assert set(shapes) == {"Shoulder Face", "Outer Face"} and mesh is None


def test_arranged_siblings_export_independent_faces_with_one_native_replay(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import face_arrangement

    workspace, snapshot = collection
    snapshot["recipe"]["nodes"].extend(
        [
            {"id": "axis", "label": "Axis", "operation": "axis", "source_fit": "side"},
            {
                "id": "clock",
                "label": "Clock",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "parallel_to_axis",
                "offset": 1.0,
            },
        ]
    )
    snapshot["states"].update({"axis": "ready", "clock": "ready"})
    records = face_arrangement.arrange_faces(
        {"kind": "plane", "axis": [0, 0, 1], "offset": 0},
        [
            {
                "key": "outer",
                "geometry": {
                    "kind": "cylinder",
                    "axis": [0, 0, 1],
                    "origin": [0, 0, 0],
                    "radius": 2,
                    "slope": 0,
                },
            },
            {
                "key": "clock",
                "geometry": {"kind": "plane", "axis": [1, 0, 0], "offset": 1},
            },
        ],
    )
    retained = [record for record in records if record["bounded"]]
    assert len(retained) == 2
    ids: list[str] = []
    for index, record in enumerate(retained):
        key = f"arranged_{index}"
        ids.append(key)
        snapshot["recipe"]["nodes"].append(
            {
                "id": key,
                "label": f"Arranged {index}",
                "operation": "arranged_face",
                "surface": {"feature": "fit", "surface": "end"},
                "cutters": [
                    {"feature": "fit", "surface": "side"},
                    {"feature": "clock"},
                ],
                "domains": [],
                "selector": record["bounds"]["arrangement"]["selector"],
            }
        )
        snapshot["states"][key] = "ready"
        snapshot["results"][key] = record
    split = cast(Callable[[dict[str, Any]], Any], vars(face_arrangement)["_split"])
    calls = 0

    def counted(intent: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        return split(intent)

    monkeypatch.setattr(face_arrangement, "_split", counted)
    request = CadExportRequest(
        token=snapshot["token"],
        scope="selected_faces",
        targets=ids,
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert calls == 1 and mesh is None
    assert set(shapes) == {"Arranged 0", "Arranged 1"}
    total_area = 0.0
    for shape in shapes.values():
        properties = GProp_GProps()
        BRepGProp.SurfaceProperties_s(shape, properties)
        total_area += properties.Mass()
        assert BRepCheck_Analyzer(shape).IsValid()
    assert total_area == pytest.approx(4 * np.pi, rel=1e-8)
    assert all(
        obj["cutters"] == [{"feature": "fit", "surface": "side"}, {"feature": "clock"}]
        for obj in metadata["objects"]
    )


@pytest.mark.parametrize("scope", ["all_faces", "selected_faces"])
@pytest.mark.parametrize(
    "change,message",
    [
        ({"state": "stale"}, "Outer Face.*stale"),
        ({"state": "failed", "error": "bad cutter"}, "Outer Face.*failed.*bad cutter"),
        ({"state": "blocked"}, "Outer Face.*blocked"),
        ({"bounded": False}, "Outer Face.*open region"),
        ({"dependency": "stale"}, "Boundary first.*stale"),
        ({"owner": "failed"}, "Face review.*failed"),
        ({"complete": False}, "Face review.*shared-boundary review is incomplete"),
    ],
)
def test_face_collection_preflight_before_kernel(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    scope: Any,
    change: dict[str, Any],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, snapshot = collection
    if "state" in change:
        snapshot["states"]["outer_face"] = change["state"]
        snapshot["errors"]["outer_face"] = change.get("error", "")
    if "bounded" in change:
        snapshot["results"]["outer_face"]["bounded"] = False
    if "dependency" in change:
        snapshot["states"]["first"] = change["dependency"]
    if "owner" in change or "complete" in change:
        snapshot["recipe"]["nodes"].insert(
            next(
                i
                for i, n in enumerate(snapshot["recipe"]["nodes"])
                if n["id"] == "outer_face"
            ),
            {
                "id": "review",
                "label": "Face review",
                "operation": "build_faces",
                "surfaces": [
                    {"feature": "fit", "surface": "end"},
                    {"feature": "fit", "surface": "side"},
                ],
                "target": {"feature": "fit", "surface": "side"},
                "boundary_sources": ["shoulder_face"],
            },
        )
        next(n for n in snapshot["recipe"]["nodes"] if n["id"] == "outer_face")[
            "managed_by"
        ] = "review"
        next(n for n in snapshot["recipe"]["nodes"] if n["id"] == "outer_face")[
            "managed_key"
        ] = "outer"
        snapshot["states"]["review"] = change.get("owner", "ready")
        snapshot["derived"]["review"] = {
            "shared_boundary_review": {"complete": change.get("complete", True)}
        }

    def no_native(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("kernel construction must not happen before preflight")

    monkeypatch.setattr("experiments.nozzle_cad.face_from_record", no_native)
    monkeypatch.setattr("experiments.nozzle_cad.transform_shape", no_native)
    monkeypatch.setattr("experiments.nozzle_cad._step_bytes", no_native)
    request = CadExportRequest(
        token=snapshot["token"],
        scope=scope,
        targets=["outer_face"] if scope == "selected_faces" else None,
        units="Millimeters",
        axis_up=False,
    )
    with pytest.raises(ValueError, match=message):
        _ = export_cad(workspace, snapshot, request)


@pytest.mark.parametrize(
    "updates,message",
    [
        ({"scope": "all_faces", "target": "fit"}, "cannot include a target"),
        (
            {"scope": "all_faces", "targets": ["outer_face"]},
            "cannot include a targets list",
        ),
        ({"scope": "selected_faces", "targets": []}, "at least one"),
        ({"scope": "selected_faces", "targets": ["missing"]}, "unknown export face"),
        ({"scope": "selected_faces", "targets": ["fit"]}, "not a built face"),
        ({"scope": "all_faces", "axis_up": True}, "axis_up=false"),
        ({"scope": "all_faces", "origin_plane": "end"}, "no origin plane"),
        (
            {"scope": "target", "target": "fit", "targets": ["outer_face"]},
            "no targets list",
        ),
        ({"scope": "target"}, "requires one target"),
        (
            {"scope": "all_faces", "review_owners": ["fit"]},
            "only valid for selected faces",
        ),
        (
            {"scope": "target", "target": "fit", "review_owners": ["fit"]},
            "only valid for selected faces",
        ),
        (
            {
                "scope": "selected_faces",
                "targets": ["shoulder_face"],
                "review_owners": ["missing"],
            },
            "unknown export face review",
        ),
        (
            {
                "scope": "selected_faces",
                "targets": ["shoulder_face"],
                "review_owners": ["fit"],
            },
            "not a face review",
        ),
    ],
)
def test_collection_scope_validation(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    updates: dict[str, Any],
    message: str,
) -> None:
    workspace, snapshot = collection
    request = CadExportRequest.model_validate(
        {
            "token": snapshot["token"],
            "units": "Millimeters",
            "axis_up": False,
            **updates,
        }
    )
    with pytest.raises(ValueError, match=message):
        _ = export_cad(workspace, snapshot, request)


def test_all_faces_empty_and_reused_only_review_failure(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
    collection: tuple[NozzleWorkspace, dict[str, Any]],
    tmp_path: Path,
) -> None:
    workspace, empty = evaluated
    request = CadExportRequest(
        token=empty["token"], scope="all_faces", units="Millimeters", axis_up=False
    )
    with pytest.raises(ValueError, match="at least one"):
        _ = export_cad(workspace, empty, request)
    _, snapshot = collection
    snapshot["recipe"]["nodes"].append(
        {
            "id": "review",
            "label": "Reused-only review",
            "operation": "build_faces",
            "surfaces": [
                {"feature": "fit", "surface": "end"},
                {"feature": "fit", "surface": "side"},
            ],
            "reused_faces": ["shoulder_face"],
        }
    )
    snapshot["states"]["review"] = "failed"
    with pytest.raises(ValueError, match=r"Reused-only review.*failed"):
        _ = export_cad(workspace, snapshot, request)
    selected = request.model_copy(
        update={"scope": "selected_faces", "targets": ["shoulder_face"]}
    )
    metadata, shapes, _, _ = roundtrip(
        export_cad(workspace, snapshot, selected), tmp_path
    )
    assert set(shapes) == {"Shoulder Face"}
    assert metadata["export"]["review_owners"] == []
    with pytest.raises(ValueError, match=r"Reused-only review.*failed"):
        _ = export_cad(
            workspace,
            snapshot,
            selected.model_copy(update={"review_owners": ["review"]}),
        )
    snapshot["states"]["review"] = "ready"
    contextual = selected.model_copy(update={"review_owners": ["review"]})
    metadata, shapes, _, _ = roundtrip(
        export_cad(workspace, snapshot, contextual), tmp_path
    )
    assert set(shapes) == {"Shoulder Face"}
    assert metadata["export"]["review_owners"] == ["review"]


@pytest.mark.parametrize("inner", [0.0, 1.0])
def test_explicit_plane_is_exact_disk_or_annulus(inner: float) -> None:
    shape = face_from_record(trimmed_face("plane", inner, 3))
    assert BRepCheck_Analyzer(shape).IsValid()
    assert len(faces(shape)) == 1
    assert BRepAdaptor_Surface(faces(shape)[0]).GetType() == GeomAbs_Plane
    np.testing.assert_allclose(
        sorted(c.Radius() for c in circles(shape)),
        [3.0] if inner == 0 else [inner, 3.0],
        atol=1e-10,
        rtol=0,
    )
    assert not TopExp_Explorer(shape, TopAbs_SOLID).More()


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_explicit_lateral_exact_end_circles(kind: str) -> None:
    record = trimmed_face(kind, -1, 2)
    shape = face_from_record(record)
    assert BRepCheck_Analyzer(shape).IsValid() and len(faces(shape)) == 1
    assert BRepAdaptor_Surface(faces(shape)[0]).GetType() == (
        GeomAbs_Cylinder if kind == "cylinder" else GeomAbs_Cone
    )
    slope = record["geometry"]["slope"]
    actual = sorted((c.Location().Z(), c.Radius()) for c in circles(shape))
    np.testing.assert_allclose(
        actual, [[2, 2 - slope], [5, 2 + 2 * slope]], atol=1e-10, rtol=0
    )


@pytest.mark.parametrize(
    ("kind", "updates", "error"),
    [
        ("plane", {"bounded": False}, "finite explicit"),
        ("plane", {"bounds": {"radial": [1, None]}}, "finite explicit"),
        ("plane", {"bounds": {"radial": [3, 1]}}, "ordered"),
        ("cylinder", {"bounds": {"axial": [None, 1]}}, "finite explicit"),
        ("cone", {"bounds": {"axial": [-20, 1]}}, "apex"),
    ],
)
def test_invalid_bounds_rejected(
    kind: str, updates: dict[str, Any], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        _ = face_from_record({**trimmed_face(kind, 1, 3), **updates})


def test_invalid_frame_rejected() -> None:
    face = trimmed_face("plane", 1, 3)
    face["geometry"]["axis"] = [0, 0, 2]
    with pytest.raises(ValueError, match="orthonormal"):
        _ = face_from_record(face)


@pytest.mark.parametrize("mode", ["source", "axis_up", "explicit"])
def test_trimmed_annulus_export_metadata_transform(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], mode: str, tmp_path: Path
) -> None:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    record = trimmed_face("plane", 1, 3)
    node = {
        "id": "shoulder_face",
        "label": "Shoulder face",
        "operation": "trimmed_face",
        "surface": {"feature": "fit", "surface": "end"},
        "boundaries": record["boundary_uses"],
    }
    snapshot["recipe"]["nodes"].append(node)
    snapshot["states"]["shoulder_face"] = "ready"
    snapshot["results"]["shoulder_face"] = record
    transform = None
    if mode == "explicit":
        transform = "explicit_output"
        snapshot["recipe"]["nodes"].append(
            {"id": transform, "operation": "transform", "label": "Explicit output"}
        )
        snapshot["states"][transform] = "ready"
        snapshot["results"][transform] = {
            "matrix": [[2, 0, 0, 4], [0, 2, 0, -3], [0, 0, 2, 1], [0, 0, 0, 1]]
        }
    request = CadExportRequest(
        token=snapshot["token"],
        target="shoulder_face",
        units="Millimeters",
        axis_up=mode == "axis_up",
        transform=transform,
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert list(shapes) == ["Shoulder face"]
    shape = shapes["Shoulder face"]
    assert BRepCheck_Analyzer(shape).IsValid() and len(faces(shape)) == 1
    assert BRepAdaptor_Surface(faces(shape)[0]).GetType() == GeomAbs_Plane
    assert metadata["objects"][0]["source_surface"] == node["surface"]
    assert metadata["objects"][0]["boundary_uses"] == node["boundaries"]
    assert metadata["objects"][0]["bounds"] == record["bounds"]
    assert metadata["objects"][0]["extent_authority"] == "declared_boundaries"
    matrix = np.array(metadata["transform_local_to_export"])
    center = (matrix @ [1, -2, 3, 1])[:3]
    scale = np.linalg.norm(matrix[:3, 0])
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    # The inner circle must remain a hole after STEP transfer, not just an edge.
    assert properties.Mass() == pytest.approx(8 * np.pi * scale**2, rel=1e-9)
    np.testing.assert_allclose(
        sorted(c.Radius() for c in circles(shape)),
        scale * np.array([1, 3]),
        atol=1e-8,
        rtol=0,
    )
    for circle in circles(shape):
        np.testing.assert_allclose(circle.Location().Coord(), center, atol=1e-8, rtol=0)
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    np.testing.assert_array_equal(mesh[1], workspace.data.triangles)


@pytest.mark.parametrize(
    "units,step_unit",
    [
        ("Millimeters", "SI_UNIT(.MILLI.,.METRE.)"),
        ("Centimeters", "SI_UNIT(.CENTI.,.METRE.)"),
        ("Meters", "SI_UNIT($,.METRE.)"),
    ],
)
@pytest.mark.parametrize("axis_up", [False, True])
def test_joint_export_geometry_units_mesh_and_names(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
    initialized_step_unit: str,
    units: Any,
    step_unit: str,
    axis_up: bool,
    tmp_path: Path,
) -> None:
    workspace, snapshot = evaluated
    before = workspace.local.copy()
    request = CadExportRequest(
        token=snapshot["token"], target="fit", units=units, axis_up=axis_up
    )
    metadata, shapes, mesh, step = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert Interface_Static.CVal_s("write.step.unit") == initialized_step_unit
    assert step_unit in step
    nodes = {n["id"]: n for n in snapshot["recipe"]["nodes"]}
    assert set(shapes) == {
        nodes[id]["label"] for id in snapshot["results"]["fit"]["surfaces"]
    }
    types = [BRepAdaptor_Surface(faces(s)[0]).GetType() for s in shapes.values()]
    assert len(types) == 2 and set(types) == {GeomAbs_Cone, GeomAbs_Plane}
    assert all(
        BRepCheck_Analyzer(s).IsValid()
        and len(faces(s)) == 1
        and not TopExp_Explorer(s, TopAbs_SOLID).More()
        for s in shapes.values()
    )
    matrix = np.array(metadata["transform_local_to_export"])
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    np.testing.assert_array_equal(mesh[1], workspace.data.triangles)
    if axis_up:
        np.testing.assert_allclose(
            matrix[:3, :3] @ snapshot["result"]["axis_display"], [0, 0, 1], atol=1e-12
        )
        cone = next(
            BRepAdaptor_Surface(faces(s)[0]).Cone()
            for s in shapes.values()
            if BRepAdaptor_Surface(faces(s)[0]).GetType() == GeomAbs_Cone
        )
        np.testing.assert_allclose(cone.Location().Coord()[:2], [0, 0], atol=1e-8)
    else:
        np.testing.assert_allclose(mesh[0], workspace.data.xyz, atol=1e-11, rtol=0)
    from experiments.ocp_geometry import transform_shape

    for id, surface in snapshot["results"]["fit"]["surfaces"].items():
        expected_shape = transform_shape(
            surface_patch(surface, workspace.local), matrix
        )
        np.testing.assert_allclose(
            bounds(shapes[nodes[id]["label"]]),
            bounds(expected_shape),
            atol=1e-8,
            rtol=0,
        )
    assert metadata["recipe"] == json.loads(json.dumps(snapshot["recipe"]))
    assert metadata["export"] == request.model_dump(mode="json")
    assert metadata["source_sha256"] == workspace.default.source_sha256
    np.testing.assert_array_equal(workspace.local, before)


@pytest.fixture
def assembled_body(
    collection: tuple[NozzleWorkspace, dict[str, Any]],
) -> tuple[NozzleWorkspace, dict[str, Any]]:
    from experiments.body_geometry import assemble_body

    workspace, snapshot = collection
    records = {
        "shoulder_face": trimmed_face("plane", 0, 2),
        "outer_face": trimmed_face("cylinder", 0, 2),
        "other_face": trimmed_face("plane", 0, 2),
    }
    records["other_face"]["geometry"]["origin"][2] += 2
    snapshot["results"].update(records)
    snapshot["recipe"]["nodes"].append(
        {
            "id": "body",
            "label": "Closed cylinder",
            "operation": "body",
            "faces": list(records),
            "sewing_tolerance": 1e-7,
        }
    )
    snapshot["results"]["body"] = assemble_body(records, 1e-7)
    snapshot["states"]["body"] = "ready"
    return workspace, snapshot


@pytest.mark.parametrize("units", ["Millimeters", "Centimeters", "Meters"])
def test_body_step_roundtrip_is_one_valid_solid(
    assembled_body: tuple[NozzleWorkspace, dict[str, Any]],
    units: str,
    tmp_path: Path,
) -> None:
    workspace, snapshot = assembled_body
    request = CadExportRequest.model_validate(
        dict(
            token=snapshot["token"],
            scope="body",
            target="body",
            units=units,
            axis_up=False,
            include_mesh=False,
        )
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert set(shapes) == {"Closed cylinder"}
    shape = shapes["Closed cylinder"]
    assert shape.ShapeType() == TopAbs_SOLID
    assert BRepCheck_Analyzer(shape).IsValid()
    assert len(faces(shape)) == 3
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    assert props.Mass() == pytest.approx(8 * np.pi)
    assert mesh is None
    assert metadata["solid"] and metadata["sewn"]
    assert metadata["geometry_mode"] == "solid"
    assert metadata["objects"][0]["source_faces"] == [
        "shoulder_face",
        "outer_face",
        "other_face",
    ]


@pytest.mark.parametrize(
    "failure",
    ["body_failed", "face_stale", "face_changed", "tolerance_changed", "unvalidated"],
)
def test_body_export_rejects_unavailable_or_changed_inputs_before_kernel(
    assembled_body: tuple[NozzleWorkspace, dict[str, Any]],
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.nozzle_cad as geometry

    workspace, snapshot = assembled_body
    if failure == "body_failed":
        snapshot["states"]["body"] = "failed"
    elif failure == "face_stale":
        snapshot["states"]["outer_face"] = "stale"
    elif failure == "face_changed":
        snapshot["results"]["outer_face"] = deepcopy(snapshot["results"]["outer_face"])
        snapshot["results"]["outer_face"]["bounds"]["axial"][1] = 3
    elif failure == "tolerance_changed":
        snapshot["recipe"]["nodes"][-1]["sewing_tolerance"] = 1e-6
    else:
        snapshot["results"]["body"]["valid"] = False

    def unexpected_replay(_record: dict[str, Any], _faces: dict[str, Any]) -> None:
        pytest.fail("invalid Body reached native replay")

    monkeypatch.setattr(geometry, "body_from_record", unexpected_replay)
    request = CadExportRequest(
        token=snapshot["token"],
        scope="body",
        target="body",
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    with pytest.raises(ValueError, match="cannot export Body"):
        _ = export_cad(workspace, snapshot, request)


@pytest.mark.parametrize(
    "overrides",
    [
        {"target": "outer_face"},
        {"target": None},
        {"targets": ["outer_face"]},
        {"axis_up": True},
        {"origin_plane": "end"},
        {"review_owners": ["fit"]},
    ],
)
def test_body_export_scope_validation(
    assembled_body: tuple[NozzleWorkspace, dict[str, Any]], overrides: dict[str, Any]
) -> None:
    _, snapshot = assembled_body
    payload = dict(
        token=snapshot["token"],
        scope="body",
        target="body",
        units="Millimeters",
        axis_up=False,
    )
    payload.update(overrides)
    with pytest.raises(ValueError):
        _ = export_face_targets(snapshot, CadExportRequest.model_validate(payload))


def test_body_transform_scales_volume_and_reference_mesh_together(
    assembled_body: tuple[NozzleWorkspace, dict[str, Any]], tmp_path: Path
) -> None:
    workspace, snapshot = assembled_body
    declarations = [
        dict(id="p0", label="Origin", operation="point", initial_coordinates=[0, 0, 0]),
        dict(
            id="p1",
            label="Scale point",
            operation="point",
            initial_coordinates=[1, 0, 0],
        ),
        dict(id="a0", label="Z", operation="axis", source_fit="side"),
        dict(id="a1", label="X", operation="axis", source_points=["p0", "p1"]),
        dict(
            id="frame",
            label="Frame",
            operation="frame",
            origin_point="p0",
            primary_reference="a0",
            primary_output_axis="+Z",
            secondary_reference="a1",
            secondary_output_axis="+X",
        ),
        dict(
            id="scale",
            label="Scale",
            operation="scale",
            distances=[dict(first_point="p0", second_point="p1", known_distance=2)],
        ),
        dict(
            id="output_transform",
            label="Output",
            operation="transform",
            frame="frame",
            scale="scale",
        ),
    ]
    snapshot["recipe"]["nodes"].extend(declarations)
    snapshot["states"].update({node["id"]: "ready" for node in declarations})
    matrix = np.array(
        [[0, -2, 0, 7], [2, 0, 0, -3], [0, 0, 2, 5], [0, 0, 0, 1]], dtype=float
    )
    snapshot["results"]["output_transform"] = {"matrix": matrix.tolist()}
    request = CadExportRequest(
        token=snapshot["token"],
        scope="body",
        target="body",
        units="Millimeters",
        axis_up=False,
        transform="output_transform",
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    solid = shapes["Closed cylinder"]
    assert solid.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(solid).IsValid()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, props)
    assert props.Mass() == pytest.approx(64 * np.pi)
    assert metadata["objects"][0]["volume_local"] == pytest.approx(8 * np.pi)
    np.testing.assert_array_equal(metadata["transform_local_to_export"], matrix)
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    np.testing.assert_array_equal(mesh[1], workspace.data.triangles)


def test_explicit_transform_geometry_and_mesh(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], tmp_path: Path
) -> None:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    snapshot["recipe"]["nodes"].append(
        {
            "id": "output_transform",
            "label": "Output transform",
            "operation": "transform",
        }
    )
    snapshot["states"]["output_transform"] = "ready"
    matrix = np.array(
        [[0, -2, 0, 7], [2, 0, 0, -3], [0, 0, 2, 5], [0, 0, 0, 1]], dtype=float
    )
    snapshot["results"]["output_transform"] = {"matrix": matrix.tolist()}
    request = CadExportRequest(
        token=snapshot["token"],
        target="fit",
        units="Millimeters",
        axis_up=False,
        transform="output_transform",
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    np.testing.assert_array_equal(metadata["transform_local_to_export"], matrix)
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    assert all(BRepCheck_Analyzer(s).IsValid() for s in shapes.values())
    with pytest.raises(ValueError, match="cannot be combined"):
        _ = export_cad(
            workspace, snapshot, request.model_copy(update={"axis_up": True})
        )


def test_options_and_stale_checks(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], tmp_path: Path
) -> None:
    workspace, snapshot = evaluated
    request = CadExportRequest(
        token=snapshot["token"], target="fit", units="Meters", include_mesh=False
    )
    _, shapes, mesh, _ = roundtrip(export_cad(workspace, snapshot, request), tmp_path)
    assert len(shapes) == 2 and mesh is None
    with pytest.raises(StaleGraph):
        _ = export_cad(
            workspace, snapshot, request.model_copy(update={"token": "stale"})
        )
    with pytest.raises(ValueError, match="evaluate"):
        _ = export_cad(workspace, {**snapshot, "states": {"fit": "stale"}}, request)
    with pytest.raises(ValueError, match="select"):
        _ = export_cad(
            workspace, snapshot, request.model_copy(update={"target": "scan"})
        )


@pytest.mark.parametrize("side_kind", ["cone", "cylinder"])
def test_shared_axis_solve_all_active_surfaces_and_standalone_plane(
    side_kind: str, tmp_path: Path
) -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    payload = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    ).model_dump()
    base = {node["id"]: node for node in payload["nodes"]}
    base["side"]["kind"] = side_kind
    payload["nodes"] = [base[key] for key in ("scan", "outer_band", "top_face", "side")]
    payload["nodes"].extend(
        [
            {"id": "axis", "label": "Axis", "operation": "axis", "source_fit": "side"},
            {
                "id": "side_factor",
                "label": "Side factor",
                "operation": "fit",
                "selections": ["outer_band"],
                "kind": side_kind,
                "axial_domain": [-2, 5],
                "axis": "axis",
            },
            {
                "id": "plane_factor",
                "label": "Plane factor",
                "operation": "fit",
                "selections": ["top_face"],
                "kind": "plane",
                "axial_domain": [-2, 5],
                "axis": "axis",
            },
            {
                "id": "solve",
                "label": "Shared solve",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["side_factor", "plane_factor"],
            },
        ]
    )
    payload["output"] = "solve"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    request = CadExportRequest(
        token=snapshot["token"], target="solve", units="Millimeters", include_mesh=False
    )
    metadata, shapes, _, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    assert {obj["feature"] for obj in metadata["objects"]} == {
        "side_factor",
        "plane_factor",
    }
    assert BRepAdaptor_Surface(faces(shapes["Side factor"])[0]).GetType() == (
        GeomAbs_Cone if side_kind == "cone" else GeomAbs_Cylinder
    )
    _, plane_shapes, _, _ = roundtrip(
        export_cad(
            workspace, snapshot, request.model_copy(update={"target": "plane_factor"})
        ),
        tmp_path,
    )
    xyz = samples(plane_shapes["Plane factor"])
    np.testing.assert_allclose(xyz[:, 2], xyz[0, 2], atol=1e-8)


@pytest.mark.parametrize(
    "change,message",
    [
        ({"missing": True}, "must name a transform"),
        ({"operation": "frame"}, "must name a transform"),
        ({"state": "stale"}, "evaluate the selected transform"),
        ({"matrix": [[1, 0, 0]]}, "finite 4x4"),
        (
            {
                "matrix": [
                    [1, 0, 0, float("nan")],
                    [0, 1, 0, 0],
                    [0, 0, 1, 0],
                    [0, 0, 0, 1],
                ]
            },
            "finite 4x4",
        ),
    ],
)
def test_export_transform_validation(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
    change: dict[str, Any],
    message: str,
) -> None:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    if not change.get("missing"):
        snapshot["recipe"]["nodes"].append(
            {
                "id": "output_transform",
                "label": "Output transform",
                "operation": change.get("operation", "transform"),
            }
        )
    snapshot["states"]["output_transform"] = change.get("state", "ready")
    snapshot["results"]["output_transform"] = {
        "matrix": change.get("matrix", np.eye(4).tolist())
    }
    request = CadExportRequest(
        token=snapshot["token"],
        target="fit",
        units="Millimeters",
        axis_up=False,
        transform="output_transform",
    )
    with pytest.raises(ValueError, match=message):
        _ = export_cad(workspace, snapshot, request)


def test_origin_plane_at_zero_and_validation(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], tmp_path: Path
) -> None:
    workspace, snapshot = evaluated
    request = CadExportRequest(
        token=snapshot["token"], target="fit", units="Millimeters", origin_plane="end"
    )
    metadata, shapes, _, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    plane = next(
        s
        for s in shapes.values()
        if BRepAdaptor_Surface(faces(s)[0]).GetType() == GeomAbs_Plane
    )
    np.testing.assert_allclose(samples(plane)[:, 2], 0, atol=1e-8)
    matrix = np.array(metadata["transform_local_to_export"])
    np.testing.assert_allclose(
        (matrix @ [*snapshot["result"]["plane_point_display"], 1])[:3], 0, atol=1e-11
    )
    for changes, message in [
        ({"axis_up": False}, "requires axis-up"),
        ({"origin_plane": "side"}, "must be a plane"),
        ({"origin_plane": "missing"}, "must be a plane"),
    ]:
        with pytest.raises(ValueError, match=message):
            _ = export_cad(workspace, snapshot, request.model_copy(update=changes))
    parallel = deepcopy(snapshot)
    parallel["results"]["fit"]["surfaces"]["end"]["plane_equation"] = [
        1,
        0,
        -snapshot["result"]["axis_display"][0] / snapshot["result"]["axis_display"][2],
        1,
    ]
    with pytest.raises(ValueError, match="parallel"):
        _ = export_cad(workspace, parallel, request)


@pytest.mark.parametrize("kind,taper", [("cylinder", 0.0), ("cone", 0.2)])
def test_exact_revolved_side(kind: str, taper: float) -> None:
    positions = np.array([[2, 0, 0], [2 + taper, 0, 1], [0, 2, 0]])
    fitted = {
        "kind": kind,
        "parameters": [0, 0, 0, 0, 2, 0, taper],
        "ids": [0, 1, 2],
        "axial_domain": [-1, 2],
    }
    shape = surface_patch(fitted, positions)
    assert BRepCheck_Analyzer(shape).IsValid()
    xyz = samples(shape)
    np.testing.assert_allclose(
        np.hypot(xyz[:, 0], xyz[:, 1]), 2 + taper * xyz[:, 2], atol=1e-10
    )


@pytest.mark.parametrize("axis_up", [False, True])
def test_standalone_sphere_export(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], axis_up: bool, tmp_path: Path
) -> None:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    snapshot["recipe"]["nodes"].append(
        {"id": "sphere", "label": "Sphere", "operation": "fit", "kind": "sphere"}
    )
    snapshot["states"]["sphere"] = "ready"
    snapshot["results"]["sphere"] = {
        "kind": "sphere",
        "parameters": [1.2, -0.8, 3.4, 2.5],
        "ids": [0, 1, 2, 3],
        "axial_domain": [-2, 5],
    }
    request = CadExportRequest(
        token=snapshot["token"], target="sphere", units="Millimeters", axis_up=axis_up
    )
    metadata, shapes, mesh, _ = roundtrip(
        export_cad(workspace, snapshot, request), tmp_path
    )
    surface = BRepAdaptor_Surface(faces(shapes["Sphere"])[0])
    assert surface.GetType() == GeomAbs_Sphere
    assert surface.Sphere().Radius() == pytest.approx(2.5, abs=1e-9)
    matrix = np.array(metadata["transform_local_to_export"])
    np.testing.assert_allclose(
        surface.Sphere().Location().Coord(),
        (matrix @ [1.2, -0.8, 3.4, 1])[:3],
        atol=1e-9,
    )
    assert mesh is not None
    np.testing.assert_array_equal(
        mesh[0], workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("operation", ["mirror_symmetry", "rotational_symmetry"])
def test_symmetry_identical_bounds_and_disable(kind: str, operation: str) -> None:
    positions: list[Any] = []
    surfaces = {}
    transforms: list[np.ndarray] = []
    for slot in range(2 if operation == "mirror_symmetry" else 3):
        matrix = np.eye(4)
        if operation == "mirror_symmetry" and slot == 1:
            matrix[0, 0], matrix[0, 3] = -1, 2
        elif operation == "rotational_symmetry":
            angle = slot * 2 * np.pi / 3
            matrix[:3, :3] = [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        transforms.append(matrix)
        a, z = np.meshgrid(
            np.linspace(-0.35, 0.45 + 0.25 * slot, 7),
            np.linspace(0.1 - 0.3 * slot, 0.7 + 0.7 * slot, 6),
        )
        if kind == "plane":
            canonical = np.column_stack(
                [3 + a.ravel(), z.ravel(), np.full(a.size, 2.0)]
            )
            parameters = [0, 0, 1, 2]
        else:
            taper = 0.15 if kind == "cone" else 0.0
            canonical = np.column_stack(
                [
                    3 + (1 + taper * z.ravel()) * np.cos(a.ravel()),
                    (1 + taper * z.ravel()) * np.sin(a.ravel()),
                    z.ravel(),
                ]
            )
            center = matrix[:3, :3] @ [3, 0, 0] + matrix[:3, 3]
            parameters = [center[0], center[1], 0, 0, 1, 0, taper]
        observed = canonical @ matrix[:3, :3].T + matrix[:3, 3]
        ids = list(range(len(positions), len(positions) + len(observed)))
        positions.extend(observed)
        surfaces[str(slot)] = {
            "kind": kind,
            "parameters": parameters,
            "ids": ids,
            "axial_domain": [-2, 5],
        }
    result = {
        "surfaces": surfaces,
        "axis_display": [0, 0, 1],
        "point_display": [0, 0, 0],
        "mirror_planes": {"mirror_plane": {"plane_equation": [1, 0, 0, 1]}},
    }
    constraint = {
        "operation": operation,
        "planes" if operation == "rotational_symmetry" else "surfaces": list(surfaces),
        "plane": "mirror_plane",
    }
    patches = joint_shapes(result, [constraint], np.array(positions))
    base = samples(patches["0"])
    for id, matrix in zip(surfaces, transforms, strict=True):
        actual = (samples(patches[id]) - matrix[:3, 3]) @ matrix[:3, :3]
        np.testing.assert_allclose(actual, base, atol=1e-10)
    independent = joint_shapes(
        result, [{**constraint, "symmetric_extents": False}], np.array(positions)
    )
    if kind != "plane":
        heights = [np.diff(bounds(s)[:, 2])[0] for s in independent.values()]
        assert heights[1] > heights[0] * 1.5
    else:
        assert np.linalg.norm(
            bounds(independent["1"])[1] - bounds(independent["1"])[0]
        ) > np.linalg.norm(bounds(independent["0"])[1] - bounds(independent["0"])[0])


def test_axis_solve_mirror_factors_passed(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import experiments.nozzle_cad as nozzle_cad

    workspace, original = evaluated
    snapshot = deepcopy(original)
    snapshot["recipe"]["nodes"].extend(
        [
            {
                "id": "mirror",
                "operation": "mirror_symmetry",
                "surfaces": ["side", "end"],
            },
            {
                "id": "solve",
                "label": "Mirror solve",
                "operation": "axis_solve",
                "factors": ["side", "end", "mirror"],
            },
        ]
    )
    snapshot["states"]["solve"] = "ready"
    snapshot["results"]["solve"] = deepcopy(snapshot["result"])
    captured: list[dict[str, Any]] = []

    def capture(
        result: dict[str, Any],
        relationships: list[dict[str, Any]],
        positions: np.ndarray,
    ) -> dict[str, TopoDS_Shape]:
        captured.extend(relationships)
        return {
            id: surface_patch(surface, positions)
            for id, surface in result["surfaces"].items()
        }

    monkeypatch.setattr(nozzle_cad, "joint_shapes", capture)
    request = CadExportRequest(
        token=snapshot["token"], target="solve", units="Millimeters", include_mesh=False
    )
    _, shapes, _, _ = roundtrip(export_cad(workspace, snapshot, request), tmp_path)
    assert len(shapes) == 2 and [r["id"] for r in captured] == ["mirror"]


@pytest.mark.parametrize("native_exception", [False, True])
def test_step_setting_restored_on_failure(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
    initialized_step_unit: str,
    monkeypatch: pytest.MonkeyPatch,
    native_exception: bool,
) -> None:
    import experiments.nozzle_cad as nozzle_cad

    workspace, snapshot = evaluated

    class FailedWriter:
        def __init__(self) -> None:
            _ = STEPCAFControl_Writer()

        def SetNameMode(self, _enabled: bool) -> None:
            pass

        def Transfer(self, *_args: Any) -> bool:
            assert Interface_Static.CVal_s("write.step.unit") == "M"
            if native_exception:
                raise Standard_ConstructionError("native transfer failure")
            return False

    monkeypatch.setattr(nozzle_cad, "STEPCAFControl_Writer", FailedWriter)
    request = CadExportRequest(
        token=snapshot["token"], target="fit", units="Meters", include_mesh=False
    )
    with pytest.raises(ValueError, match="transfer"):
        _ = export_cad(workspace, snapshot, request)
    assert Interface_Static.CVal_s("write.step.unit") == initialized_step_unit
