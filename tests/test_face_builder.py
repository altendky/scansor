"""Reviewed batch authoring is atomic, replayable, and ownership scoped."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

import experiments.face_builder as face_builder
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.face_proposals import propose_faces
from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_session import NozzleWorkspace

REFERENCES = [
    {"feature": "fit", "surface": "side"},
    {"feature": "fit", "surface": "end"},
]


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


def recipe() -> dict[str, Any]:
    return Recipe.model_validate_json(
        Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json").read_text()
    ).model_dump()


def state(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.evaluate(state(graph)["token"], all_actions=True))


@pytest.fixture
def graph(workspace: NozzleWorkspace) -> FeatureGraph:
    result = FeatureGraph(workspace, Recipe.model_validate(recipe()))
    _ = evaluate(result)
    return result


def preview(graph: FeatureGraph, owner: str | None = None) -> dict[str, Any]:
    return preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": state(graph)["token"], "owner_id": owner, "surfaces": REFERENCES}
        ),
    )


def request(
    plan: dict[str, Any], *, members: tuple[str, ...] = ("side", "end")
) -> FacesApplyRequest:
    choices = [
        {"surface": face["surface"], "region_key": face["suggested_region_key"]}
        for face in plan["faces"]
        if face["surface"]["surface"] in members
    ]
    return FacesApplyRequest.model_validate(
        {
            "token": plan["token"],
            "owner_id": plan["owner_id"],
            "surfaces": REFERENCES,
            "proposal_token": plan["proposal_token"],
            "label": "Reviewed faces",
            "choices": choices,
        }
    )


def owner_id(snapshot: dict[str, Any]) -> str:
    return next(
        node["id"]
        for node in snapshot["recipe"]["nodes"]
        if node["operation"] == "build_faces"
    )


def owned(snapshot: dict[str, Any], owner: str) -> dict[str, dict[str, Any]]:
    return {
        node["managed_key"]: node
        for node in snapshot["recipe"]["nodes"]
        if node["managed_by"] == owner
    }


def test_preview_is_read_only_and_apply_evaluation_preserves_fit_evidence(
    graph: FeatureGraph, workspace: NozzleWorkspace
) -> None:
    before = state(graph)
    plan = preview(graph)
    assert state(graph) == before
    assert plan == preview(graph)
    applied = apply_faces(graph, request(plan))
    owner = owner_id(applied)
    outputs = owned(applied, owner)
    assert len(outputs) == 3
    assert applied["recipe"]["output"] == owner
    assert (
        sum(node["operation"] == "surface_intersection" for node in outputs.values())
        == 1
    )
    result = evaluate(graph)
    assert result["results"]["fit"] == before["results"]["fit"]
    assert result["memberships"] == before["memberships"]
    assert all(result["states"][node["id"]] == "ready" for node in outputs.values())
    summary = result["results"][owner]
    assert len(summary["generated_faces"]) == 2
    assert len(summary["generated_intersections"]) == 1
    replay = FeatureGraph(workspace, Recipe.model_validate(result["recipe"]))
    assert evaluate(replay)["results"] == result["results"]


def test_owner_update_keeps_ids_and_inventory_changes_with_removed_face(
    graph: FeatureGraph,
) -> None:
    first = apply_faces(graph, request(preview(graph)))
    owner = owner_id(first)
    before = evaluate(graph)
    first_outputs = owned(before, owner)
    same = apply_faces(graph, request(preview(graph, owner)))
    assert owned(same, owner) == first_outputs
    _ = evaluate(graph)
    changed_request = request(preview(graph, owner))
    target = next(
        face
        for face in preview(graph, owner)["faces"]
        if face["surface"]["surface"] == "side"
    )
    alternative = next(
        region
        for region in target["regions"]
        if region["key"] != target["suggested_region_key"]
    )
    changed = changed_request.model_dump()
    next(
        choice
        for choice in changed["choices"]
        if choice["surface"]["surface"] == "side"
    )["region_key"] = alternative["key"]
    result = apply_faces(graph, FacesApplyRequest.model_validate(changed))
    assert {key: node["id"] for key, node in owned(result, owner).items()} == {
        key: node["id"] for key, node in first_outputs.items()
    }
    _ = evaluate(graph)
    reduced = apply_faces(graph, request(preview(graph, owner), members=("end",)))
    assert reduced["states"][owner] == "stale"
    refreshed = evaluate(graph)
    assert len(refreshed["results"][owner]["generated_faces"]) == 1
    assert len(refreshed["results"][owner]["generated_intersections"]) == 1
    remaining = owned(refreshed, owner)
    assert len(remaining) == 2
    assert all(
        node["id"] == first_outputs[key]["id"] for key, node in remaining.items()
    )


def test_manual_reversed_edge_and_identical_face_are_reused_unchanged(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    manual = [
        {
            "id": "manual_edge",
            "label": "Manual shared edge",
            "operation": "surface_intersection",
            "first": REFERENCES[1],
            "second": REFERENCES[0],
        },
        {
            "id": "manual_face",
            "label": "Manual shoulder",
            "operation": "trimmed_face",
            "surface": REFERENCES[1],
            "boundaries": [{"intersection": "manual_edge", "keep": "inside"}],
        },
    ]
    payload["nodes"].extend(manual)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    before = evaluate(graph)
    result = apply_faces(graph, request(preview(graph)))
    owner = owner_id(result)
    assert len(owned(result, owner)) == 1
    for key in ("manual_edge", "manual_face"):
        assert next(
            node for node in result["recipe"]["nodes"] if node["id"] == key
        ) == next(node for node in before["recipe"]["nodes"] if node["id"] == key)
    summary = evaluate(graph)["results"][owner]
    assert summary["reused_faces"] == ["manual_face"]
    assert summary["reused_intersections"] == ["manual_edge"]
    assert len(summary["generated_faces"]) == 1
    assert not summary["generated_intersections"]


@pytest.mark.parametrize(
    "mutation",
    ["unknown_region", "duplicate_surface", "unknown_surface", "duplicate_label"],
)
def test_invalid_choices_are_atomic(graph: FeatureGraph, mutation: str) -> None:
    plan = preview(graph)
    payload = request(plan).model_dump()
    if mutation == "unknown_region":
        payload["choices"][0]["region_key"] = "not-a-reviewed-region"
    elif mutation == "duplicate_surface":
        payload["choices"].append(deepcopy(payload["choices"][0]))
    elif mutation == "unknown_surface":
        payload["choices"][0]["surface"] = {"feature": "fit", "surface": "unknown"}
    else:
        payload["label"] = "Outer band"
    before = state(graph)
    with pytest.raises(ValueError):
        _ = apply_faces(graph, FacesApplyRequest.model_validate(payload))
    assert state(graph) == before


def test_stale_recipe_token_rejects_apply_atomically(graph: FeatureGraph) -> None:
    reviewed = request(preview(graph))
    payload = state(graph)["recipe"]
    payload["nodes"][1]["label"] = "Renamed band"
    _ = graph.replace(Recipe.model_validate(payload), reviewed.token)
    before = state(graph)
    with pytest.raises(StaleGraph, match="preview again"):
        _ = apply_faces(graph, reviewed)
    assert state(graph) == before


def test_same_recipe_token_but_changed_resolved_geometry_rejects_apply(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = request(preview(graph))
    # Simulate a geometry-cache refresh without a recipe change: the proposal
    # hash must guard actual resolved coefficients, not just authoring state.
    original_snapshot = graph.snapshot

    def changed_geometry() -> dict[str, object]:
        snapshot = original_snapshot()
        results = cast(dict[str, Any], snapshot["results"])
        results["fit"]["surfaces"]["side"]["parameters"][4] += 0.1
        return snapshot

    monkeypatch.setattr(graph, "snapshot", changed_geometry)
    before = state(graph)
    assert before["token"] == reviewed.token
    with pytest.raises(StaleGraph, match="proposals changed"):
        _ = apply_faces(graph, reviewed)
    assert state(graph) == before


def test_empty_preview_region_cannot_be_applied(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_preview(inputs: list[dict[str, Any]]) -> dict[str, Any]:
        result = propose_faces(inputs)
        for face in result["faces"]:
            for region in face["regions"]:
                region["preview"] = {"positions": [], "indices": []}
        return result

    monkeypatch.setattr(face_builder, "propose_faces", broken_preview)
    reviewed = request(preview(graph))
    before = state(graph)
    with pytest.raises(ValueError, match="no valid preview"):
        _ = apply_faces(graph, reviewed)
    assert state(graph) == before


def test_external_consumer_prevents_owned_face_removal(graph: FeatureGraph) -> None:
    first = apply_faces(graph, request(preview(graph)))
    owner = owner_id(first)
    evaluated = evaluate(graph)
    wall = next(
        node["id"]
        for node in owned(evaluated, owner).values()
        if node["operation"] == "trimmed_face" and node["surface"]["surface"] == "side"
    )
    payload = evaluated["recipe"]
    payload["nodes"].append(
        {
            "id": "consumer",
            "label": "External consumer",
            "operation": "build_faces",
            "surfaces": REFERENCES,
            "reused_faces": [wall],
        }
    )
    _ = graph.replace(Recipe.model_validate(payload), evaluated["token"])
    _ = evaluate(graph)
    reviewed = request(preview(graph, owner), members=("end",))
    before = state(graph)
    with pytest.raises(ValueError, match="used by 'External consumer'"):
        _ = apply_faces(graph, reviewed)
    assert state(graph) == before


def test_recipe_limit_rejects_whole_batch_without_partial_children(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    for index in range(100 - len(payload["nodes"])):
        payload["nodes"].append(
            {
                "id": f"extra_{index}",
                "label": f"Extra {index}",
                "operation": "selection",
                "source": "scan",
                "ids": [2023],
            }
        )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    _ = evaluate(graph)
    reviewed = request(preview(graph))
    before = state(graph)
    with pytest.raises(ValueError, match="100-action recipe limit"):
        _ = apply_faces(graph, reviewed)
    assert state(graph) == before


def test_preview_requires_evaluated_explicit_member_geometry(
    workspace: NozzleWorkspace,
) -> None:
    graph = FeatureGraph(workspace, Recipe.model_validate(recipe()))
    before = state(graph)
    with pytest.raises(ValueError, match="evaluate the selected geometry"):
        _ = preview(graph)
    assert state(graph) == before


def test_targeting_owner_evaluates_generated_children_and_replays(
    graph: FeatureGraph, workspace: NozzleWorkspace
) -> None:
    applied = apply_faces(graph, request(preview(graph)))
    owner = owner_id(applied)
    result = cast(dict[str, Any], graph.evaluate(applied["token"], target=owner))
    assert all(
        result["states"][node["id"]] == "ready"
        for node in owned(result, owner).values()
    )
    replay = FeatureGraph(workspace, Recipe.model_validate(result["recipe"]))
    replayed = cast(
        dict[str, Any], replay.evaluate(state(replay)["token"], target=owner)
    )
    assert replayed["results"] == result["results"]


def test_apply_and_owner_update_preserve_existing_output_transform(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    payload["nodes"].extend(
        [
            {
                "id": "origin",
                "label": "Output origin",
                "operation": "point",
                "initial_coordinates": [1, 2, 3],
            },
            {
                "id": "forward_point",
                "label": "Output forward point",
                "operation": "point",
                "initial_coordinates": [5, 2, 3],
            },
            {
                "id": "forward",
                "label": "Output forward axis",
                "operation": "axis",
                "source_points": ["origin", "forward_point"],
            },
            {
                "id": "up",
                "label": "Output up axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 0, 0],
            },
            {
                "id": "frame",
                "label": "Output frame",
                "operation": "frame",
                "origin_point": "origin",
                "primary_reference": "up",
                "primary_output_axis": "+Z",
                "secondary_reference": "forward",
                "secondary_output_axis": "+X",
            },
            {
                "id": "scale",
                "label": "Output scale",
                "operation": "scale",
                "distances": [
                    {
                        "first_point": "origin",
                        "second_point": "forward_point",
                        "known_distance": 8,
                    }
                ],
            },
            {
                "id": "output_transform",
                "label": "Output transform",
                "operation": "transform",
                "frame": "frame",
                "scale": "scale",
            },
        ]
    )
    payload["output"] = "output_transform"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    before = evaluate(graph)
    applied = apply_faces(graph, request(preview(graph)))
    owner = owner_id(applied)
    assert applied["recipe"]["output"] == "output_transform"
    assert (
        applied["results"]["output_transform"] == before["results"]["output_transform"]
    )
    _ = graph.evaluate(applied["token"], target=owner)
    updated = apply_faces(graph, request(preview(graph, owner), members=("end",)))
    assert updated["recipe"]["output"] == "output_transform"
    assert (
        evaluate(graph)["results"]["output_transform"]
        == before["results"]["output_transform"]
    )


def test_reopening_preserves_previously_reused_outside_manual_region(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    payload["nodes"].extend(
        [
            {
                "id": "manual_edge",
                "label": "Manual shared edge",
                "operation": "surface_intersection",
                "first": REFERENCES[0],
                "second": REFERENCES[1],
            },
            {
                "id": "manual_inside",
                "label": "Manual inside",
                "operation": "trimmed_face",
                "surface": REFERENCES[1],
                "boundaries": [{"intersection": "manual_edge", "keep": "inside"}],
            },
            {
                "id": "manual_outside",
                "label": "Manual outside",
                "operation": "trimmed_face",
                "surface": REFERENCES[1],
                "boundaries": [{"intersection": "manual_edge", "keep": "outside"}],
            },
        ]
    )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    _ = evaluate(graph)
    plan = preview(graph)
    plane_face = next(
        face for face in plan["faces"] if face["surface"]["surface"] == "end"
    )
    outside = next(
        region
        for region in plane_face["regions"]
        if region["existing_face_id"] == "manual_outside"
    )
    payload = request(plan, members=("end",)).model_dump()
    payload["choices"][0]["region_key"] = outside["key"]
    applied = apply_faces(graph, FacesApplyRequest.model_validate(payload))
    owner = owner_id(applied)
    _ = evaluate(graph)
    reopened = preview(graph, owner)
    plane_face = next(
        face for face in reopened["faces"] if face["surface"]["surface"] == "end"
    )
    previous = [
        region for region in plane_face["regions"] if region["previously_selected"]
    ]
    assert len(previous) == 1
    assert previous[0]["key"] == outside["key"]
    assert previous[0]["existing_face_id"] == "manual_outside"
    assert previous[0]["key"] != plane_face["suggested_region_key"]
