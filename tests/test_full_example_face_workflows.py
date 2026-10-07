"""Complete example recipes exercise face review, not just isolated curves."""

from pathlib import Path
from typing import Any, cast

import numpy as np

from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )


def all_fits(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"feature": node["id"]}
        for node in snapshot["recipe"]["nodes"]
        if node["operation"] == "fit" and node["kind"] in ("plane", "cylinder", "cone")
    ]


def apply_populated(
    graph: FeatureGraph, before: dict[str, Any], *, bounded_only: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    refs = all_fits(before)
    plan = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": before["token"], "surfaces": refs}
        ),
    )
    assert graph.snapshot() == before
    assert all(
        face["regions"] and not face.get("blocked_by_geometry")
        for face in plan["faces"]
    ), [
        (face["label"], face["status"], face["diagnostics"])
        for face in plan["faces"]
        if not face["regions"] or face.get("blocked_by_geometry")
    ]
    choices: list[dict[str, Any]] = []
    for face in plan["faces"]:
        assert face["status"] in (
            "suggested",
            "ambiguous",
            "requires_adjacency_review",
            "missing_boundaries",
        )
        assert all(
            region["evidence"]["total_count"]
            == len(before["results"][face["surface"]["feature"]]["ids"])
            for region in face["regions"]
        )
        for region in face["regions"]:
            if region["evidence"]["interior_count"] and (
                region["bounded"] or not bounded_only
            ):
                choices.append(
                    {"surface": face["surface"], "region_key": region["key"]}
                )
    assert choices
    _ = apply_faces(
        graph,
        FacesApplyRequest.model_validate(
            {
                "token": plan["token"],
                "surfaces": refs,
                "proposal_token": plan["proposal_token"],
                "label": "Example reviewed cells",
                "choices": choices,
            }
        ),
    )
    after = evaluate(graph)
    assert not after["errors"]
    assert after["memberships"] == before["memberships"]
    for key, result in before["results"].items():
        assert after["results"][key] == result
    faces = [
        node
        for node in after["recipe"]["nodes"]
        if node["operation"] in ("trimmed_face", "arranged_face")
    ]
    assert len(faces) == len(choices)
    assert all(after["states"][node["id"]] == "ready" for node in faces)
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(after["recipe"]))
    assert evaluate(replay)["results"] == after["results"]
    return plan, after


def test_full_boss_all_twenty_two_fits_apply_and_replay(tmp_path: Path) -> None:
    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    example = root / "scan-coarse"
    workspace = NozzleWorkspace(example)
    payload = Recipe.model_validate_json(RECIPE.read_bytes()).model_dump()
    normals = np.load(example / "truth/normal-part.npy", allow_pickle=False)
    # Fixture truth supplies selections only. All five perimeter/bottom planes
    # are fitted to noisy observations; truth never supplies their geometry.
    for name, normal in (
        ("left", [-1, 0, 0]),
        ("right", [1, 0, 0]),
        ("front", [0, -1, 0]),
        ("back", [0, 1, 0]),
        ("bottom", [0, 0, -1]),
    ):
        ids = np.flatnonzero(
            np.all(normals == normal, axis=1) & (workspace.data.weights > 0)
        ).tolist()
        assert len(ids) >= 3
        payload["nodes"].extend(
            [
                {
                    "id": "plate_" + name + "_selection",
                    "label": "Plate " + name + " observations",
                    "operation": "selection",
                    "source": "scan",
                    "ids": ids,
                },
                {
                    "id": "plate_" + name,
                    "label": "Plate " + name + " fit",
                    "operation": "fit",
                    "kind": "plane",
                    "selections": ["plate_" + name + "_selection"],
                },
            ]
        )
    for index, (first, second) in enumerate(
        (
            ("plate_left", "plate_right"),
            ("plate_front", "plate_back"),
            ("plate-top-fit", "plate_bottom"),
        )
    ):
        payload["nodes"].append(
            {
                "id": f"perimeter_parallel_{index}",
                "label": f"Perimeter parallel pair {index}",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [first, second],
            }
        )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    before = evaluate(graph)
    assert not before["errors"] and len(all_fits(before)) == 22
    plan, after = apply_populated(graph, before, bounded_only=True)
    assert len(plan["faces"]) == 22
    assert all(
        any(
            region["bounded"] and region["evidence"]["interior_count"]
            for region in face["regions"]
        )
        for face in plan["faces"]
    )
    assert len(after["recipe"]["nodes"]) > 100


def test_captured_boss_recipe_replays_its_reviewed_faces_and_body(
    tmp_path: Path,
) -> None:
    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    recipe = Recipe.model_validate_json(
        Path(
            "examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json"
        ).read_bytes()
    )
    state = evaluate(FeatureGraph(NozzleWorkspace(root / "scan-coarse"), recipe))
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    bodies = [node for node in recipe.nodes if node.operation == "body"]
    assert len(bodies) == 1
    body = state["results"][bodies[0].id]
    assert body["valid"] and body["volume"] > 0


def test_two_cone_nozzle_recipe_preserves_its_open_physical_extents() -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    graph = FeatureGraph(
        NozzleWorkspace(example),
        Recipe.model_validate_json(
            (example / "recipes/nozzle-two-cone-open-extents.json").read_bytes()
        ),
    )
    before = evaluate(graph)
    assert not before["errors"] and len(all_fits(before)) == 2
    plan, after = apply_populated(graph, before, bounded_only=False)
    assert len(plan["faces"]) == 2
    # This fixture declares only two fitted cones, not end caps or
    # complete face adjacency. Reconstruction must not manufacture a solid.
    assert any(
        not region["bounded"]
        for face in plan["faces"]
        for region in face["regions"]
        if region["evidence"]["interior_count"]
    )
    assert any(
        result.get("preview_clipped")
        for result in after["results"].values()
        if result.get("kind") == "trimmed_face"
    )
