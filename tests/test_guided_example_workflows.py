"""Guided face review on generated scan evidence, not fixture-supplied bounds."""

from pathlib import Path
from typing import Any, cast

from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.guided_face_candidates import FaceCandidatesRequest, candidate_faces
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )


def fitted_reference(snapshot: dict[str, Any], label: str) -> dict[str, Any]:
    node = next(node for node in snapshot["recipe"]["nodes"] if node["label"] == label)
    return {"feature": node["id"], "surface": None}


def retain_populated_bounded_cell(
    graph: FeatureGraph,
    target: dict[str, Any],
    neighbors: list[dict[str, Any]],
    label: str,
    sources: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    before = cast(dict[str, Any], graph.snapshot())
    inputs = {
        "token": before["token"],
        "target": target,
        "surfaces": [target, *neighbors],
        "boundary_sources": sources,
    }
    plan = preview_faces(graph, FacesPreviewRequest.model_validate(inputs))
    assert graph.snapshot() == before
    assert len(plan["faces"]) == 1 and plan["faces"][0]["surface"] == target
    assert len(plan["adjacencies"]) == len(neighbors)
    face = plan["faces"][0]
    assert not face.get("blocked_by_geometry")
    region = max(
        (region for region in face["regions"] if region["bounded"]),
        key=lambda region: region["evidence"]["interior_count"],
    )
    assert region["evidence"]["interior_count"] > 0
    applied = apply_faces(
        graph,
        FacesApplyRequest.model_validate(
            {
                **inputs,
                "proposal_token": plan["proposal_token"],
                "label": label,
                "choices": [{"surface": target, "region_key": region["key"]}],
            }
        ),
    )
    owner = next(node for node in applied["recipe"]["nodes"] if node["label"] == label)
    assert owner["target"] == target
    outputs = [
        node
        for node in applied["recipe"]["nodes"]
        if node["managed_by"] == owner["id"]
        and node["operation"] in ("trimmed_face", "arranged_face")
    ]
    assert len(outputs) == 1 and outputs[0]["surface"] == target
    after = evaluate(graph)
    assert not after["errors"]
    assert after["memberships"] == before["memberships"]
    for key, result in before["results"].items():
        assert after["results"][key] == result
    return after, owner, outputs[0]


def test_guided_boss_shoulder_then_outer_preserves_shared_arc_and_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    workspace = NozzleWorkspace(root / "scan-coarse")
    graph = FeatureGraph(workspace, Recipe.model_validate_json(RECIPE.read_bytes()))
    original = evaluate(graph)
    assert not original["errors"]
    shoulder, outer, bore, clock, plate = (
        fitted_reference(original, label)
        for label in (
            "shoulder fit",
            "outer fit",
            "bore fit",
            "clock fit",
            "plate top fit",
        )
    )
    available = [
        {"feature": node["id"], "surface": None}
        for node in original["recipe"]["nodes"]
        if node["operation"] == "fit" and node["kind"] in ("plane", "cylinder", "cone")
    ]
    candidates = candidate_faces(
        graph,
        FaceCandidatesRequest.model_validate(
            {"token": original["token"], "target": shoulder, "surfaces": available}
        ),
    )
    assert graph.snapshot() == original
    assert all(
        candidate["reference"] != shoulder for candidate in candidates["candidates"]
    )
    after_shoulder, shoulder_owner, shoulder_face = retain_populated_bounded_cell(
        graph, shoulder, [outer, bore, clock], "Guided shoulder", []
    )
    reverse = candidate_faces(
        graph,
        FaceCandidatesRequest.model_validate(
            {"token": after_shoulder["token"], "target": outer, "surfaces": available}
        ),
    )
    known = next(
        candidate
        for candidate in reverse["candidates"]
        if candidate["reference"] == shoulder
    )
    assert any(
        decision["owner_id"] == shoulder_owner["id"]
        and decision["state"] == "confirmed"
        for decision in known["known_adjacencies"]
    )
    assert any(source["id"] == shoulder_face["id"] for source in known["shared_faces"])
    after_outer, outer_owner, outer_face = retain_populated_bounded_cell(
        graph, outer, [shoulder, clock, plate], "Guided outer", [shoulder_face["id"]]
    )
    assert outer_owner["boundary_sources"] == [shoulder_face["id"]]
    assert outer_face["boundary_sources"] == [shoulder_face["id"]]
    assert (
        after_outer["results"][shoulder_face["id"]]
        == after_shoulder["results"][shoulder_face["id"]]
    )
    replay = FeatureGraph(workspace, Recipe.model_validate(after_outer["recipe"]))
    assert evaluate(replay)["results"] == after_outer["results"]
