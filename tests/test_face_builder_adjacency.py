"""Reviewed adjacency drafts and explicit face scopes remain atomic and replayable."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.feature_graph import (
    BuildFaces,
    FeatureGraph,
    Recipe,
    StaleGraph,
    dependencies,
)
from experiments.nozzle_session import NozzleWorkspace

REFS = [{"feature": "fit", "surface": "side"}, {"feature": "fit", "surface": "end"}]
CONFIRMED = [{"first": REFS[0], "second": REFS[1], "state": "confirmed"}]
SCOPES = [{"surface": REFS[0], "faces": ["manual_wall"]}]


def state(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.evaluate(state(graph)["token"], all_actions=True))


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def graph(workspace: NozzleWorkspace) -> FeatureGraph:
    recipe = Recipe.model_validate_json(
        Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json").read_text()
    ).model_dump()
    recipe["nodes"].extend(
        [
            {
                "id": "manual_edge",
                "label": "Manual rim",
                "operation": "surface_intersection",
                "first": REFS[0],
                "second": REFS[1],
            },
            {
                "id": "manual_wall",
                "label": "Manual outer face",
                "operation": "trimmed_face",
                "surface": REFS[0],
                "boundaries": [{"intersection": "manual_edge", "keep": "negative"}],
            },
            {
                "id": "manual_plane",
                "label": "Manual end face",
                "operation": "trimmed_face",
                "surface": REFS[1],
                "boundaries": [{"intersection": "manual_edge", "keep": "inside"}],
            },
        ]
    )
    result = FeatureGraph(workspace, Recipe.model_validate(recipe))
    _ = evaluate(result)
    return result


def preview(graph: FeatureGraph, **kwargs: Any) -> dict[str, Any]:
    return preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": state(graph)["token"], "surfaces": REFS, **kwargs}
        ),
    )


def request(plan: dict[str, Any], **kwargs: Any) -> FacesApplyRequest:
    return FacesApplyRequest.model_validate(
        {
            "token": plan["token"],
            "surfaces": REFS,
            "owner_id": plan["owner_id"],
            "proposal_token": plan["proposal_token"],
            "label": "Reviewed physical faces",
            "choices": [
                {
                    "surface": face["surface"],
                    # Exercise explicit legacy open faces, not UI suggestions.
                    "region_key": face["suggested_region_key"]
                    or next(
                        region["key"]
                        for region in face["regions"]
                        if region["evidence"]["interior_count"]
                    ),
                }
                for face in plan["faces"]
            ],
            **kwargs,
        }
    )


def owner(snapshot: dict[str, Any]) -> dict[str, Any]:
    return next(
        node
        for node in snapshot["recipe"]["nodes"]
        if node["operation"] == "build_faces"
    )


def test_scopes_and_adjacency_drafts_persist_replay_and_inherit_until_explicitly_cleared(
    graph: FeatureGraph, workspace: NozzleWorkspace
) -> None:
    before = state(graph)
    plan = preview(graph, adjacencies=CONFIRMED, face_scopes=SCOPES)
    assert state(graph) == before
    applied = apply_faces(
        graph, request(plan, adjacencies=CONFIRMED, face_scopes=SCOPES)
    )
    action = owner(applied)
    assert action["adjacencies"] == CONFIRMED
    assert action["face_scopes"] == SCOPES
    assert "manual_wall" in dependencies(BuildFaces.model_validate(action))
    _ = evaluate(graph)
    inherited = preview(graph, owner_id=action["id"])
    explicit = preview(
        graph, owner_id=action["id"], adjacencies=CONFIRMED, face_scopes=SCOPES
    )
    assert inherited == explicit
    replay = FeatureGraph(workspace, Recipe.model_validate(state(graph)["recipe"]))
    assert evaluate(replay)["results"] == state(graph)["results"]
    cleared = preview(graph, owner_id=action["id"], adjacencies=[], face_scopes=[])
    assert cleared["adjacencies"][0]["state"] == "proposed"
    changed = apply_faces(graph, request(cleared, adjacencies=[], face_scopes=[]))
    # Accepting this boundary is itself a new confirmation, not inherited draft.
    assert owner(changed)["adjacencies"] == CONFIRMED
    assert owner(changed)["face_scopes"] == []


def test_changing_adjacency_decision_rejects_stale_proposal_without_graph_mutation(
    graph: FeatureGraph,
) -> None:
    plan = preview(graph, adjacencies=CONFIRMED)
    before = state(graph)
    rejected = deepcopy(CONFIRMED)
    rejected[0]["state"] = "rejected"
    with pytest.raises(StaleGraph):
        _ = apply_faces(graph, request(plan, adjacencies=rejected))
    assert state(graph) == before


@pytest.mark.parametrize(
    "invalid",
    ["unknown", "wrong_surface", "not_face", "duplicate_face", "duplicate_scope"],
)
def test_invalid_face_scopes_are_rejected_atomically(
    graph: FeatureGraph, invalid: str
) -> None:
    scopes: list[dict[str, Any]] = deepcopy(SCOPES)
    if invalid == "unknown":
        scopes[0]["faces"] = ["unknown"]
    elif invalid == "wrong_surface":
        scopes[0]["faces"] = ["manual_plane"]
    elif invalid == "not_face":
        scopes[0]["faces"] = ["manual_edge"]
    elif invalid == "duplicate_face":
        scopes[0]["faces"] *= 2
    else:
        scopes *= 2
    before = state(graph)
    with pytest.raises(ValueError):
        _ = preview(graph, face_scopes=scopes)
    assert state(graph) == before


def test_scope_requires_the_exact_solve_context_not_identical_member_coefficients(
    graph: FeatureGraph,
) -> None:
    before = state(graph)
    # Standalone `side` and resolved joint `fit -> side` are different references.
    with pytest.raises(ValueError):
        _ = preview_faces(
            graph,
            FacesPreviewRequest.model_validate(
                {
                    "token": before["token"],
                    "surfaces": [{"feature": "side"}, REFS[1]],
                    "face_scopes": [
                        {"surface": {"feature": "side"}, "faces": ["manual_wall"]}
                    ],
                }
            ),
        )
    assert state(graph) == before


def test_scope_cannot_depend_on_owner_descendant_face(graph: FeatureGraph) -> None:
    plan = preview(graph)
    payload = request(plan).model_dump()
    # Pick the opposite axial half instead of reusing the existing manual wall.
    wall = next(face for face in plan["faces"] if face["surface"] == REFS[0])
    choice = next(
        choice for choice in payload["choices"] if choice["surface"] == REFS[0]
    )
    choice["region_key"] = next(
        region["key"]
        for region in wall["regions"]
        if region["existing_face_id"] is None
    )
    applied = apply_faces(graph, FacesApplyRequest.model_validate(payload))
    action = owner(applied)
    child = next(
        node
        for node in applied["recipe"]["nodes"]
        if node["operation"] == "trimmed_face" and node["managed_by"] == action["id"]
    )
    _ = evaluate(graph)
    before = state(graph)
    with pytest.raises(ValueError):
        _ = preview(
            graph,
            owner_id=action["id"],
            face_scopes=[{"surface": REFS[0], "faces": [child["id"]]}],
        )
    assert state(graph) == before


def test_scoped_face_edit_invalidates_owner_and_old_proposal(
    graph: FeatureGraph,
) -> None:
    plan = preview(graph, face_scopes=SCOPES)
    applied = apply_faces(graph, request(plan, face_scopes=SCOPES))
    action = owner(applied)
    current = evaluate(graph)
    old = preview(graph, owner_id=action["id"])
    recipe = deepcopy(current["recipe"])
    next(node for node in recipe["nodes"] if node["id"] == "manual_wall")["boundaries"][
        0
    ]["keep"] = "positive"
    changed = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(recipe), current["token"])
    )
    assert changed["states"][action["id"]] == "stale"
    before = state(graph)
    with pytest.raises(StaleGraph):
        _ = apply_faces(graph, request(old))
    assert state(graph) == before
    with pytest.raises(ValueError):
        _ = preview(graph, owner_id=action["id"])
    assert state(graph) == before


@pytest.mark.parametrize("confirmed", [False, True])
def test_oblique_adjacency_is_supported_with_or_without_prior_confirmation(
    graph: FeatureGraph, confirmed: bool
) -> None:
    refs = [*REFS, {"feature": "end"}]
    decisions = (
        [{"first": REFS[0], "second": refs[2], "state": "confirmed"}]
        if confirmed
        else []
    )
    plan = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": state(graph)["token"], "surfaces": refs, "adjacencies": decisions}
        ),
    )
    target = next(face for face in plan["faces"] if face["surface"] == REFS[0])
    assert target["regions"]
    assert not target["blocked_by_adjacency"]
    reviewed = FacesApplyRequest.model_validate(
        {
            "token": plan["token"],
            "surfaces": refs,
            "proposal_token": plan["proposal_token"],
            "label": "Reviewed partial wall",
            "adjacencies": decisions,
            "choices": [
                {"surface": REFS[0], "region_key": target["regions"][0]["key"]}
            ],
        }
    )
    before = state(graph)
    applied = apply_faces(graph, reviewed)
    assert owner(applied)["surfaces"] == [
        {"feature": ref["feature"], "surface": ref.get("surface")} for ref in refs
    ]
    assert state(graph)["results"]["fit"] == before["results"]["fit"]


def test_duplicate_and_unknown_adjacency_decisions_are_atomic(
    graph: FeatureGraph,
) -> None:
    for decisions in (
        CONFIRMED + CONFIRMED,
        [{"first": REFS[0], "second": {"feature": "unknown"}, "state": "rejected"}],
    ):
        before = state(graph)
        with pytest.raises(ValueError):
            _ = preview(graph, adjacencies=decisions)
        assert state(graph) == before
