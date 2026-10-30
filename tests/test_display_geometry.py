"""Renderer buffers refine without changing accepted CAD or review evidence."""

import json
from copy import deepcopy
from itertools import pairwise
from typing import Any, Literal

import numpy as np
import pytest
from numpy.typing import NDArray
from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shape
from pydantic import ValidationError

from experiments import display_geometry, ocp_geometry
from experiments.body_geometry import (
    assemble_body,
    body_from_record,
    body_input_fingerprint,
)
from experiments.display_geometry import (
    DisplayRefinementRequest,
    refine_display_geometry,
)
from experiments.native_replay import native_replay_scope
from experiments.scan_workspace import ScanCancelled
from experiments.surface_extents import circle_intersection, trimmed_face
from tests.test_body_geometry import cylinder_faces
from tests.test_ocp_geometry import bounded_side, cut, side


def refine(
    record: dict[str, Any],
    quality: Literal["draft", "standard", "fine"] = "standard",
    scale: float = 10,
) -> dict[str, Any]:
    return refine_display_geometry(record, scale=scale, quality=quality)


def paths(display: dict[str, Any]) -> list[NDArray[np.float64]]:
    return [np.asarray(edge["positions"]).reshape(-1, 3) for edge in display["edges"]]


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
@pytest.mark.parametrize("slope", [0, 0.2])
def test_presets_refine_lateral_chord_and_mesh_outline_at_every_scale(
    scale: float, slope: float
) -> None:
    record = bounded_side(3 * scale, slope, 1 * scale, 4 * scale)
    before = deepcopy(record)
    displays = {
        quality: refine(record, quality, 10 * scale)
        for quality in ("draft", "standard", "fine")
    }
    assert displays["draft"]["preview"] == record["preview"]
    counts = [display["stats"]["triangles"] for display in displays.values()]
    assert counts[0] < counts[1] < counts[2]
    for quality in ("standard", "fine"):
        display = displays[quality]
        vertices = np.asarray(display["preview"]["positions"]).reshape(-1, 3)
        assert len(paths(display)) == 2
        for line in paths(display):
            # Outlines use actual triangle vertices, rather than an independently
            # sampled, visibly coarser approximation of the same boundary.
            assert all(np.any(np.all(vertices == point, axis=1)) for point in line)
            for a, b in pairwise(line):
                middle = (a + b) / 2
                radius = 3 * scale + slope * middle[2]
                error = radius - np.linalg.norm(middle[:2] - [1, 2])
                assert (
                    error
                    <= 10 * scale * display_geometry.DISPLAY_QUALITY[quality][0] * 1.01
                )
        assert display["stats"]["bytes"] == len(
            json.dumps(display, separators=(",", ":")).encode()
        )
    assert record == before


def test_annulus_has_two_coherent_curved_outlines_and_unchanged_record() -> None:
    surface = cut(2)
    lateral = side(3)
    record = trimmed_face(
        surface,
        [
            (
                {"intersection": "outer", "keep": "inside"},
                circle_intersection(surface, lateral),
            ),
            (
                {"intersection": "inner", "keep": "outside"},
                circle_intersection(surface, side(1)),
            ),
        ],
        np.empty((0, 3)),
    )
    before = deepcopy(record)
    display = refine(record)
    assert len(display["edges"]) == 2
    assert all(edge["closed"] for edge in display["edges"])
    assert record == before


def test_sewn_body_meshes_once_and_all_shared_outlines_are_mesh_vertices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    faces = cylinder_faces()
    with native_replay_scope():
        body = assemble_body(faces, 1e-7)
        solid = body_from_record(body, faces)
        before = deepcopy(body), deepcopy(faces), body_input_fingerprint(faces, 1e-7)
        properties = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, properties)
        volume = properties.Mass()
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        original_meshes: list[Any] = []
        while explorer.More():
            original_meshes.append(
                BRep_Tool.Triangulation_s(
                    TopoDS.Face(explorer.Current()), TopLoc_Location()
                )
            )
            explorer.Next()
        original = vars(ocp_geometry)["BRepMesh_IncrementalMesh"]
        calls: list[TopoDS_Shape] = []

        def measured(shape: Any, *args: Any) -> Any:
            calls.append(shape)
            return original(shape, *args)

        monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", measured)
        display = refine_display_geometry(
            body, faces=faces, scale=10, quality="standard"
        )
        assert len(calls) == 1
        assert len(display["edges"]) == 4  # Four shared circles, no axial seams.
        vertices = np.asarray(display["preview"]["positions"]).reshape(-1, 3)
        for line in paths(display):
            # Each boundary is shared by two independently indexed face meshes.
            assert all(
                np.count_nonzero(
                    np.all(np.isclose(vertices, point, rtol=0, atol=1e-10), axis=1)
                )
                >= 2
                for point in line
            )
        indices = np.asarray(display["preview"]["indices"])
        cursor = 0
        assert len(display["face_ranges"]) == 4
        for piece in display["face_ranges"]:
            assert piece["start"] == cursor
            assert piece["count"] > 0 and piece["count"] % 3 == 0
            assert len(piece["face_ids"]) == 1
            identifier = piece["face_ids"][0]
            assert identifier in faces
            points = vertices[indices[cursor : cursor + piece["count"]]]
            if identifier in ("inner", "outer"):
                np.testing.assert_allclose(
                    np.linalg.norm(points[:, :2], axis=1),
                    1 if identifier == "inner" else 2,
                    atol=1e-9,
                )
            else:
                np.testing.assert_allclose(
                    points[:, 2], 0 if identifier == "bottom" else 3, atol=1e-9
                )
            cursor += piece["count"]
        assert cursor == len(indices)
        explorer = TopExp_Explorer(solid, TopAbs_FACE)
        for mesh in original_meshes:
            assert (
                BRep_Tool.Triangulation_s(
                    TopoDS.Face(explorer.Current()), TopLoc_Location()
                )
                == mesh
            )
            explorer.Next()
        BRepGProp.VolumeProperties_s(solid, properties)
        assert properties.Mass() == volume
        assert (body, faces, body_input_fingerprint(faces, 1e-7)) == before


def test_independent_neighbor_outlines_agree_within_declared_chord() -> None:
    faces = cylinder_faces()
    a = refine(faces["outer"])
    b = refine(faces["top"])
    a_line = max(paths(a), key=lambda line: line[:, 2].mean())
    b_line = max(paths(b), key=lambda line: np.linalg.norm(line[:, :2], axis=1).mean())
    # Distinct face charts may rotate/reverse a circular discretization. Compare
    # their actual polygon chords, rather than assuming vertex-index identity.
    for line, other in ((a_line, b_line), (b_line, a_line)):
        start, end = other[:-1], other[1:]
        segment = end - start
        length2 = np.sum(segment * segment, axis=1)
        for point in line:
            t = np.clip(
                np.sum((point - start) * segment, axis=1) / np.maximum(length2, 1e-30),
                0,
                1,
            )
            distance = float(
                np.min(np.linalg.norm(start + t[:, None] * segment - point, axis=1))
            )
            assert distance <= 2 * 10 * display_geometry.DISPLAY_QUALITY["standard"][0]


def test_unbounded_interval_keeps_existing_envelope_and_omits_artificial_cap() -> None:
    surface = side()
    record = trimmed_face(
        surface,
        [
            (
                {"intersection": "start", "keep": "positive"},
                circle_intersection(surface, cut(2)),
            )
        ],
        np.array([[4, 2, 3], [4, 2, 7]]),
    )
    before = deepcopy(record)
    display = refine(record)
    assert record == before and not record["bounded"]
    assert len(display["edges"]) == 1
    np.testing.assert_allclose(paths(display)[0][:, 2], 2)
    accepted = np.asarray(record["preview"]["positions"]).reshape(-1, 3)
    refined = np.asarray(display["preview"]["positions"]).reshape(-1, 3)
    assert refined[:, 2].min() == pytest.approx(accepted[:, 2].min())
    assert refined[:, 2].max() == pytest.approx(accepted[:, 2].max())


@pytest.mark.parametrize("bounded", [False, True])
def test_arrangement_replays_exact_selector_and_hides_carrier_edges(
    bounded: bool,
) -> None:
    from experiments.face_arrangement import arrange_faces
    from tests.test_face_arrangement import (
        cutter,
        cylinder,
        observations,
        plane,
        region,
    )

    record = region(
        arrange_faces(
            plane(), [cutter("circle", cylinder())], observations=observations()
        ),
        {"circle": "negative" if bounded else "positive"},
    )
    assert record["bounded"] is bounded
    before = deepcopy(record)
    display = refine(record)
    assert len(display["edges"]) == 1
    for line in paths(display):
        np.testing.assert_allclose(np.linalg.norm(line[:, :2], axis=1), 2, atol=1e-8)
    assert record == before


def test_general_face_reconstruction_avoids_large_translation_roundtrip() -> None:
    from experiments.general_face_geometry import (
        face_from_boundaries,
        intersection_record,
    )
    from tests.test_general_face_geometry import cylinder, plane, use

    shift = np.array([1e9, -2e9, 3e9])
    surface = plane(offset=shift[2])
    boundary = intersection_record(surface, cylinder(10, tuple(shift)))
    record = face_from_boundaries(surface, [(use("inside", 0), boundary)])
    before = deepcopy(record)
    display = refine(record, scale=30)
    for line in paths(display):
        np.testing.assert_allclose(
            np.linalg.norm(line[:, :2] - shift[:2], axis=1), 10, atol=1e-6
        )
        np.testing.assert_allclose(line[:, 2], shift[2], rtol=0, atol=1e-6)
    assert record == before


def test_topology_and_output_limits_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 1, 4)
    monkeypatch.setattr(display_geometry, "MAX_DISPLAY_TRIANGLES", 1)
    with pytest.raises(ValueError, match="refinement limit"):
        _ = refine(record)
    monkeypatch.setattr(display_geometry, "MAX_DISPLAY_FACES", 0)
    calls: list[Any] = []

    def unexpected(*args: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", unexpected)
    with pytest.raises(ValueError, match="topology"):
        _ = refine(record)
    assert not calls


def test_curved_boundary_budget_rejects_before_native_meshing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 1, 4)
    calls: list[Any] = []

    def unexpected(*args: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(display_geometry, "MAX_DISPLAY_EDGE_POINTS", 100)
    monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", unexpected)
    with pytest.raises(ValueError, match="boundary preflight"):
        _ = refine(record)
    assert not calls


def test_cancel_after_native_meshing_never_publishes_private_buffers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 1, 4)
    before = deepcopy(record)
    stopped = False
    original = vars(ocp_geometry)["BRepMesh_IncrementalMesh"]

    def cancel_after(shape: Any, *args: Any) -> Any:
        nonlocal stopped
        result = original(shape, *args)
        stopped = True
        return result

    monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", cancel_after)
    with pytest.raises(ScanCancelled):
        _ = refine_display_geometry(
            record, scale=10, quality="standard", cancelled=lambda: stopped
        )
    assert record == before


def test_thin_mesh_failure_retries_fresh_copies_without_affecting_semantic_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 2, 2.000001)
    original = vars(ocp_geometry)["BRepMesh_IncrementalMesh"]
    calls: list[TopoDS_Shape] = []

    class Failed:
        def IsDone(self) -> bool:
            return False

    def first_failure(shape: Any, *args: Any) -> Any:
        calls.append(shape)
        return Failed() if len(calls) == 1 else original(shape, *args)

    monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", first_failure)
    display = refine(record)
    assert display["preview"]["indices"] and len(calls) == 2
    assert not calls[0].IsSame(calls[1])


def test_mesh_failure_exhaustion_never_returns_coarse_or_empty_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 1, 4)
    calls: list[Any] = []

    class Failed:
        def IsDone(self) -> bool:
            return False

    def always_failed(*args: Any) -> Failed:
        calls.append(args)
        return Failed()

    monkeypatch.setattr(ocp_geometry, "BRepMesh_IncrementalMesh", always_failed)
    with pytest.raises(ValueError, match="meshing failed"):
        _ = refine(record)
    assert len(calls) == 6
    assert [args[2] for args in calls] == [False, False, False, True, True, True]


def test_unrepresentable_quality_and_byte_budget_fail_without_changing_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = bounded_side(3, 0, 1, 4)
    before = deepcopy(record)
    with pytest.raises(ValueError, match="normalized resolution"):
        _ = refine(record, scale=1e-8)
    monkeypatch.setattr(display_geometry, "MAX_DISPLAY_SCALARS", 3)
    with pytest.raises(ValueError, match="refinement limit"):
        _ = refine(record)
    assert record == before


def test_body_cancel_stops_between_source_face_reconstruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import body_geometry

    faces = cylinder_faces()
    record = assemble_body(faces, 1e-7)
    calls: list[dict[str, Any]] = []
    original = vars(body_geometry)["face_from_record"]

    def stopped_after_first(value: dict[str, Any]) -> Any:
        calls.append(value)
        return original(value)

    monkeypatch.setattr(body_geometry, "face_from_record", stopped_after_first)
    with pytest.raises(ScanCancelled):
        _ = refine_display_geometry(
            record,
            faces=faces,
            scale=10,
            quality="standard",
            cancelled=lambda: bool(calls),
        )
    assert len(calls) == 1


def test_body_refinement_rejects_lost_sewing_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import body_geometry

    faces = cylinder_faces()
    record = assemble_body(faces, 1e-7)
    before = deepcopy(record)

    def lost(*_args: Any) -> list[str]:
        return []

    monkeypatch.setattr(body_geometry, "_source_ids", lost)
    with pytest.raises(ValueError, match="provenance"):
        _ = refine_display_geometry(record, faces=faces, scale=10, quality="standard")
    assert record == before


@pytest.mark.parametrize(
    "changes",
    [
        {"revision": True},
        {"revision": -1},
        {"quality": "ultra"},
        {"unexpected": 1},
        {"feature": ""},
    ],
)
def test_display_request_rejects_invalid_control_input(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ = DisplayRefinementRequest.model_validate(
            {
                "token": "a",
                "revision": 1,
                "feature": "face",
                "quality": "standard",
                **changes,
            }
        )
