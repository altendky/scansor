"""Reviewed connected cells remain graph-backed, replayable physical faces."""

import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import pytest
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

import experiments.face_arrangement as arrangement
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.face_geometry import face_from_record
from experiments.face_proposals import intersection_key, reference_key
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.nozzle_session import NozzleWorkspace


def graph_with_diagonal() -> FeatureGraph:
    example = Path("examples/nozzle-bayonette-simplified")
    payload = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_bytes()
    ).model_dump()
    payload["nodes"] = [
        payload["nodes"][0],
        {
            "id": "axis",
            "label": "Normal",
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        },
    ]
    for name, angle, offset in (
        ("plate", None, 0),
        ("left", 180, -4),
        ("right", 180, 4),
        ("bottom", 270, -3),
        ("top", 270, 3),
        ("diagonal", 45, 0),
    ):
        payload["nodes"].append(
            {
                "id": name,
                "label": name.title(),
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis"
                if angle is None
                else "parallel_to_axis",
                "initial_angle_degrees": angle,
                "offset": offset,
            }
        )
    payload["output"] = "plate"
    return FeatureGraph(NozzleWorkspace(example), Recipe.model_validate(payload))


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )


def reviewed_plate(graph: FeatureGraph) -> tuple[dict[str, Any], FacesApplyRequest]:
    snapshot = evaluate(graph)
    refs = [
        {"feature": name}
        for name in ("plate", "left", "right", "bottom", "top", "diagonal")
    ]
    plan = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": snapshot["token"], "surfaces": refs}
        ),
    )
    assert graph.snapshot() == snapshot
    plate = next(
        face for face in plan["faces"] if face["surface"]["feature"] == "plate"
    )
    assert plate["status"] == "no_observations"
    kept_sides = {
        reference_key({"feature": name}): side
        for name, side in (
            ("left", "positive"),
            ("right", "negative"),
            ("bottom", "positive"),
            ("top", "negative"),
        )
    }
    regions = [
        region
        for region in plate["regions"]
        if region["bounded"]
        and all(
            region["arrangement"]["selector"]["signs"][key] == side
            for key, side in kept_sides.items()
        )
    ]
    assert len(regions) == 2
    return plan, FacesApplyRequest.model_validate(
        {
            "token": plan["token"],
            "proposal_token": plan["proposal_token"],
            "label": "Reviewed plate patches",
            "surfaces": refs,
            "choices": [
                {"surface": plate["surface"], "region_key": region["key"]}
                for region in regions
            ],
        }
    )


def area(record: dict[str, Any]) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face_from_record(record), properties)
    return properties.Mass()


def test_multiple_cells_apply_replay_and_export_without_changing_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graph_with_diagonal()
    before = evaluate(graph)
    _, request = reviewed_plate(graph)
    applied = apply_faces(graph, request)
    faces = [
        node
        for node in applied["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    ]
    assert len(faces) == 2 and len({node["id"] for node in faces}) == 2
    assert all(
        node["surface"] == {"feature": "plate", "surface": None} for node in faces
    )
    original_prepare = arrangement.prepare_faces
    calls: list[int] = []

    def prepare(*args: Any, **kwargs: Any) -> arrangement.Arrangement:
        calls.append(1)
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr(arrangement, "prepare_faces", prepare)
    snapshot = evaluate(graph)
    assert not snapshot["errors"]
    assert calls == [1]  # Two reviewed cells share one native arrangement.
    assert snapshot["memberships"] == before["memberships"]
    for key, result in before["results"].items():
        assert snapshot["results"][key] == result
    assert sum(
        area(snapshot["results"][node["id"]]) for node in faces
    ) == pytest.approx(48, rel=1e-8)
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(snapshot["recipe"]))
    assert evaluate(replay)["results"] == snapshot["results"]
    pinned = deepcopy(snapshot)
    data = export_cad(
        graph.workspace,
        snapshot,
        CadExportRequest(
            token=snapshot["token"],
            target=faces[0]["id"],
            units="Millimeters",
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(data)) as bundle:
        assert set(bundle.namelist()) == {"model.step", "metadata.json"}
        metadata = json.loads(bundle.read("metadata.json"))
        assert metadata["objects"][0]["region_selector"] == faces[0]["selector"]
        assert metadata["objects"][0]["extent_authority"] == "declared_boundaries"
    assert graph.snapshot() == pinned


def test_scoped_reused_arranged_face_preserves_actual_boundary_adjacencies() -> None:
    graph = graph_with_diagonal()
    _, request = reviewed_plate(graph)
    _ = apply_faces(graph, request)
    before = evaluate(graph)
    existing = next(
        node
        for node in before["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    )
    scope = [{"surface": existing["surface"], "faces": [existing["id"]]}]
    plan = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {
                "token": before["token"],
                "surfaces": request.model_dump()["surfaces"],
                "face_scopes": scope,
            }
        ),
    )
    face = next(
        face for face in plan["faces"] if face["surface"] == existing["surface"]
    )
    region = next(
        region
        for region in face["regions"]
        if region["existing_face_id"] == existing["id"]
    )
    assert not region["boundary_keys"]  # Physical bounds are inherited from scope.
    applied = apply_faces(
        graph,
        FacesApplyRequest.model_validate(
            {
                "token": plan["token"],
                "proposal_token": plan["proposal_token"],
                "label": "Reused scoped patch",
                "surfaces": request.model_dump()["surfaces"],
                "face_scopes": scope,
                "choices": [{"surface": face["surface"], "region_key": region["key"]}],
            }
        ),
    )
    owner = next(
        node
        for node in applied["recipe"]["nodes"]
        if node["label"] == "Reused scoped patch"
    )
    assert owner["reused_faces"] == [existing["id"]]
    physical_keys = before["results"][existing["id"]]["boundary_keys"]
    expected = {
        intersection_key(existing["surface"], cutter)
        for cutter in existing["cutters"]
        if reference_key(cutter) in physical_keys
    }
    assert len(expected) == 4  # Do not confirm a cutter absent from this cell.
    assert {
        intersection_key(choice["first"], choice["second"])
        for choice in owner["adjacencies"]
        if choice["state"] == "confirmed"
    } == expected


def test_duplicate_cell_choice_rejected_atomically() -> None:
    graph = graph_with_diagonal()
    _, request = reviewed_plate(graph)
    before = graph.snapshot()
    request = request.model_copy(
        update={"choices": [request.choices[0], request.choices[0]]}
    )
    with pytest.raises(ValueError, match="region choices must be unique"):
        _ = apply_faces(graph, request)
    assert graph.snapshot() == before


def test_refit_rebuilds_cell_and_invalidates_crossed_witness() -> None:
    graph = graph_with_diagonal()
    _, request = reviewed_plate(graph)
    _ = apply_faces(graph, request)
    snapshot = evaluate(graph)
    faces = [
        node
        for node in snapshot["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    ]
    recipe = deepcopy(snapshot["recipe"])
    next(node for node in recipe["nodes"] if node["id"] == "right")["offset"] = 6
    _ = graph.replace(Recipe.model_validate(recipe), snapshot["token"])
    rebuilt = evaluate(graph)
    assert not rebuilt["errors"]
    assert sum(area(rebuilt["results"][node["id"]]) for node in faces) == pytest.approx(
        60, rel=1e-8
    )
    assert [node["selector"] for node in faces] == [
        node["selector"]
        for node in rebuilt["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    ]
    next(node for node in recipe["nodes"] if node["id"] == "right")["offset"] = -10
    _ = graph.replace(Recipe.model_validate(recipe), rebuilt["token"])
    with pytest.raises(ValueError, match="review the region again"):
        _ = evaluate(graph)
    failed = cast(dict[str, Any], graph.snapshot())
    assert all(failed["states"][node["id"]] != "ready" for node in faces)
    assert any("review" in failed["errors"].get(node["id"], "") for node in faces)
