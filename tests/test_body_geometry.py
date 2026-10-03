"""Body assembly does not turn incomplete or ambiguous surfaces into solids."""

from copy import deepcopy
from typing import Any

import numpy as np
import pytest
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_Transform,
)
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Face

from experiments import body_geometry
from experiments.body_geometry import (
    BodyAssemblyError,
    assemble_body,
    body_from_record,
    body_input_fingerprint,
)
from experiments.native_replay import native_replay_scope

Records = dict[str, dict[str, Any]]


def install_faces(monkeypatch: pytest.MonkeyPatch, shapes: list[TopoDS_Face]) -> None:
    def construct(record: dict[str, Any]) -> TopoDS_Face:
        return shapes[record["index"]]

    monkeypatch.setattr(body_geometry, "face_from_record", construct)


def native_faces() -> list[TopoDS_Face]:
    explorer = TopExp_Explorer(BRepPrimAPI_MakeBox(1, 2, 3).Shape(), TopAbs_FACE)
    result: list[TopoDS_Face] = []
    while explorer.More():
        result.append(TopoDS.Face(explorer.Current()))
        explorer.Next()
    return result


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, Any]]:
    shapes = native_faces()
    install_faces(monkeypatch, shapes)
    return {f"face-{i}": {"index": i, "bounded": True} for i in range(6)}


def test_box_is_oriented_closed_solid(box: Records):
    record = assemble_body(box, 1e-7)
    assert record["volume"] == pytest.approx(6)
    assert record["face_count"] == 6
    assert record["valid"] is True
    assert record["preview"]["indices"]
    assert all(edge["source_faces"] for edge in record["preview"]["edges"])
    solid = body_from_record(record, box)
    assert solid.ShapeType() == TopAbs_SOLID
    assert BRepCheck_Analyzer(solid).IsValid()


def test_missing_face_produces_source_linked_open_edges(box: Records):
    _ = box.pop("face-5")
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(box, 1e-7)
    problems = failure.value.diagnostic["problems"]
    free = [problem for problem in problems if problem["code"] == "free_edge"]
    assert len(free) == 4
    assert all(
        problem["source_faces"] and problem["preview"]["positions"] for problem in free
    )
    assert failure.value.diagnostic["preview"]["edges"]


def test_duplicate_face_rejected(box: Records):
    box["duplicate"] = deepcopy(box["face-0"])
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(box, 1e-7)
    assert {"changed_faces", "nonmanifold_edge"} & {
        p["code"] for p in failure.value.diagnostic["problems"]
    }


def test_reversed_input_faces_oriented(monkeypatch: pytest.MonkeyPatch):
    shapes = native_faces()
    for index in (0, 2, 3):
        shapes[index] = TopoDS.Face(shapes[index].Reversed())
    install_faces(monkeypatch, shapes)
    record = assemble_body(
        {f"face-{i}": {"index": i, "bounded": True} for i in range(6)}, 1e-7
    )
    assert record["volume"] == pytest.approx(6)


def test_disconnected_shells_rejected(monkeypatch: pytest.MonkeyPatch):
    shapes = native_faces()
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(10, 0, 0))
    shapes += [
        TopoDS.Face(BRepBuilderAPI_Transform(face, transform, True).Shape())
        for face in shapes
    ]
    install_faces(monkeypatch, shapes)
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(
            {f"face-{i}": {"index": i, "bounded": True} for i in range(12)}, 1e-7
        )
    assert "disconnected" in {
        problem["code"] for problem in failure.value.diagnostic["problems"]
    }


def test_replay_only_cached_within_pass(box: Records, monkeypatch: pytest.MonkeyPatch):
    calls = 0
    shapes = native_faces()

    def counted(record: dict[str, Any]) -> TopoDS_Face:
        nonlocal calls
        calls += 1
        return shapes[record["index"]]

    monkeypatch.setattr(body_geometry, "face_from_record", counted)
    with native_replay_scope():
        record = assemble_body(box, 1e-7)
        _ = body_from_record(record, box)
        assert calls == 6
    _ = body_from_record(record, box)
    assert calls == 12


def cylinder_faces(
    radius: float = 2, inner: float = 1, height: float = 3, shift: float = 0
) -> dict[str, dict[str, Any]]:
    geometry = {
        "origin": [0, 0, shift],
        "axis": [0, 0, 1],
        "basis_u": [1, 0, 0],
        "basis_v": [0, 1, 0],
        "radius": radius,
        "slope": 0,
    }
    return {
        "outer": {
            "surface_kind": "cylinder",
            "geometry": geometry,
            "bounded": True,
            "bounds": {"axial": [0, height]},
        },
        "inner": {
            "surface_kind": "cylinder",
            "geometry": {**geometry, "radius": inner},
            "bounded": True,
            "bounds": {"axial": [0, height]},
        },
        "bottom": {
            "surface_kind": "plane",
            "geometry": geometry,
            "bounded": True,
            "bounds": {"radial": [inner, radius]},
        },
        "top": {
            "surface_kind": "plane",
            "geometry": {**geometry, "origin": [0, 0, shift + height]},
            "bounded": True,
            "bounds": {"radial": [inner, radius]},
        },
    }


def test_hollow_cylinder_actual_face_records():
    faces = cylinder_faces()
    record = assemble_body(faces, 1e-7)
    assert record["volume"] == pytest.approx(9 * np.pi)
    assert BRepCheck_Analyzer(body_from_record(record, faces)).IsValid()


@pytest.mark.parametrize(
    "gap,tolerance,accepted", [(1e-6, 1e-7, False), (1e-6, 1e-5, True)]
)
def test_gap_only_joins_within_explicit_tolerance(
    gap: float, tolerance: float, accepted: bool
):
    faces = cylinder_faces()
    faces["top"]["geometry"]["origin"][2] += gap
    if accepted:
        assert assemble_body(faces, tolerance)["valid"]
    else:
        with pytest.raises(BodyAssemblyError) as failure:
            _ = assemble_body(faces, tolerance)
        assert any(
            p["code"] == "free_edge" for p in failure.value.diagnostic["problems"]
        )


def test_declared_tolerance_cannot_hide_input_uncertainty(
    box: Records, monkeypatch: pytest.MonkeyPatch
):
    shapes = native_faces()
    BRep_Builder().UpdateFace(shapes[0], 1e-4)
    install_faces(monkeypatch, shapes)
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(box, 1e-7)
    assert failure.value.diagnostic["problems"][0]["code"] == "input_tolerance"
    assert failure.value.diagnostic["problems"][0][
        "required_tolerance"
    ] == pytest.approx(1e-4)


def test_success_flag_cannot_bypass_replay_validation():
    faces = cylinder_faces()
    _ = faces.pop("top")
    record: dict[str, Any] = {
        "kind": "body",
        "valid": True,
        "face_ids": list(faces),
        "input_fingerprint": body_input_fingerprint(faces, 1e-7),
        "sewing_tolerance": 1e-7,
    }
    with pytest.raises(BodyAssemblyError):
        _ = body_from_record(record, faces)


def test_self_intersecting_closed_shell_is_rejected(monkeypatch: pytest.MonkeyPatch):
    ring = [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]
    # The two apexes are on the same side, one outside the equatorial polygon.
    # Every triangle is valid and every edge has two uses, but triangles cross.
    shapes: list[TopoDS_Face] = []
    for apex in [(0, 0, 1), (2, 2, 1)]:
        for i in range(4):
            polygon = BRepBuilderAPI_MakePolygon()
            for xyz in (ring[i], ring[(i + 1) % 4], apex):
                polygon.Add(gp_Pnt(*xyz))
            polygon.Close()
            shapes.append(BRepBuilderAPI_MakeFace(polygon.Wire()).Face())
    install_faces(monkeypatch, shapes)
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(
            {f"face-{i}": {"index": i, "bounded": True} for i in range(8)}, 1e-7
        )
    assert "self_intersection" in {
        p["code"] for p in failure.value.diagnostic["problems"]
    }
    assert failure.value.diagnostic["preview"]["edges"]
    assert all(p["source_faces"] for p in failure.value.diagnostic["problems"])


@pytest.mark.parametrize("scale,shift", [(0.001, 0), (1000, 1e6)])
def test_model_scale_and_translation_preserve_volume(scale: float, shift: float):
    record = assemble_body(cylinder_faces(2 * scale, scale, 3 * scale, shift), 1e-6)
    assert record["volume"] == pytest.approx(9 * np.pi * scale**3, rel=1e-8)


def test_display_coverage_never_closes_physical_openings():
    faces = cylinder_faces()
    faces["top"]["bounded"] = False
    faces["top"]["preview"] = {"positions": [0, 0, 3], "indices": []}
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(faces, 1e-7)
    assert failure.value.diagnostic["problems"][0]["code"] == "open_face"


def test_display_preview_is_not_physical_input():
    faces = cylinder_faces()
    for face in faces.values():
        face["preview"] = {"positions": [1e9, -1e9, 1e9], "indices": []}
    assert assemble_body(faces, 1e-7)["volume"] == pytest.approx(9 * np.pi)


def test_independently_replayed_duplicate_faces_rejected():
    faces = cylinder_faces()
    faces["duplicate_cap"] = deepcopy(faces["bottom"])
    with pytest.raises(BodyAssemblyError):
        _ = assemble_body(faces, 1e-7)


def test_three_faces_sharing_one_edge_are_nonmanifold(monkeypatch: pytest.MonkeyPatch):
    shapes: list[TopoDS_Face] = []
    for third in [(0, 1, 0), (0, 0, 1), (0, -1, 0)]:
        polygon = BRepBuilderAPI_MakePolygon()
        for xyz in [(0, 0, 0), (1, 0, 0), third]:
            polygon.Add(gp_Pnt(*xyz))
        polygon.Close()
        shapes.append(BRepBuilderAPI_MakeFace(polygon.Wire()).Face())
    install_faces(monkeypatch, shapes)
    with pytest.raises(BodyAssemblyError) as failure:
        _ = assemble_body(
            {f"face-{i}": {"index": i, "bounded": True} for i in range(3)}, 1e-7
        )
    nonmanifold = [
        p
        for p in failure.value.diagnostic["problems"]
        if p["code"] == "nonmanifold_edge"
    ]
    assert len(nonmanifold) == 1
    assert set(nonmanifold[0]["source_faces"]) == {"face-0", "face-1", "face-2"}


@pytest.mark.parametrize("duplicate", [False, True])
def test_assembly_does_not_modify_shared_native_inputs(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool
):
    shapes = native_faces()
    shapes[0] = TopoDS.Face(shapes[0].Reversed())
    install_faces(monkeypatch, shapes)

    def signatures() -> list[Any]:
        result: list[Any] = []
        for face in shapes:
            values: list[Any] = [face.Orientation(), BRep_Tool.Tolerance_s(face)]
            u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
            values.append(
                BRepAdaptor_Surface(face).Value((u0 + u1) / 2, (v0 + v1) / 2).Coord()
            )
            for kind in (TopAbs_EDGE, TopAbs_VERTEX):
                explorer = TopExp_Explorer(face, kind)
                while explorer.More():
                    shape = explorer.Current()
                    values.append(
                        BRep_Tool.Tolerance_s(TopoDS.Edge(shape))
                        if kind == TopAbs_EDGE
                        else BRep_Tool.Tolerance_s(TopoDS.Vertex(shape))
                    )
                    explorer.Next()
            result.append(values)
        return result

    before = signatures()
    records = {f"face-{i}": {"index": i, "bounded": True} for i in range(6)}
    if duplicate:
        records["duplicate"] = deepcopy(records["face-0"])
        with pytest.raises(BodyAssemblyError):
            _ = assemble_body(records, 1e-7)
    else:
        _ = assemble_body(records, 1e-7)
    assert signatures() == before


def test_body_record_is_compact_and_rejects_changed_inputs():
    faces = cylinder_faces()
    record = assemble_body(faces, 1e-7)
    assert "input_faces" not in record
    assert record["face_ids"] == list(faces)
    assert record["input_fingerprint"] == body_input_fingerprint(faces, 1e-7)
    faces["outer"]["bounds"]["axial"][1] += 1
    with pytest.raises(ValueError, match="changed since assembly"):
        _ = body_from_record(record, faces)


def test_body_export_requires_all_and_only_its_evaluated_faces():
    faces = cylinder_faces()
    record = assemble_body(faces, 1e-7)
    _ = faces.pop("top")
    with pytest.raises(ValueError, match="do not match"):
        _ = body_from_record(record, faces)


def test_failed_native_assembly_is_not_cached(monkeypatch: pytest.MonkeyPatch):
    shapes = native_faces()
    calls = 0

    def construct(record: dict[str, Any]) -> TopoDS_Face:
        nonlocal calls
        calls += 1
        return shapes[record["index"]]

    monkeypatch.setattr(body_geometry, "face_from_record", construct)
    records = {f"face-{i}": {"index": i, "bounded": True} for i in range(5)}
    with native_replay_scope():
        for _ in range(2):
            with pytest.raises(BodyAssemblyError):
                _ = assemble_body(records, 1e-7)
    assert calls == 10
