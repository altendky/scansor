"""Surface-first authoring preserves explicit review and graph semantics."""

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

import experiments.face_proposals as proposals
from experiments.face_adjacency import classify_pair
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.feature_graph import BuildFaces, FeatureGraph, Recipe, StaleGraph
from experiments.guided_face_candidates import FaceCandidatesRequest, candidate_faces
from tests.test_arranged_face_workflow import evaluate, graph_with_diagonal

TARGET = {"feature": "plate", "surface": None}
REFS = [
    {"feature": name, "surface": None}
    for name in ("plate", "left", "right", "bottom", "top", "diagonal")
]


def draft(graph: FeatureGraph) -> tuple[dict[str, Any], dict[str, Any]]:
    before = evaluate(graph)
    payload = {"token": before["token"], "surfaces": REFS, "target": TARGET}
    return preview_faces(graph, FacesPreviewRequest.model_validate(payload)), payload


def test_guided_preview_partitions_only_target_and_omits_neighbor_pairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graph_with_diagonal()
    original = classify_pair
    pairs: list[int] = []

    def classify(*args: Any) -> dict[str, str]:
        pairs.append(1)
        return original(*args)

    monkeypatch.setattr(proposals, "classify_pair", classify)
    before = evaluate(graph)
    plan, _ = draft(graph)
    assert graph.snapshot() == before
    assert pairs == [1] * (len(REFS) - 1)
    assert len(plan["faces"]) == 1 and plan["faces"][0]["surface"] == TARGET
    assert all(
        TARGET in (pair["first"], pair["second"]) for pair in plan["adjacencies"]
    )


def test_generator_candidate_previews_are_read_only_and_keep_uncertainty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def seeded(
        points: NDArray[np.float64],
        weights: NDArray[np.float64],
        normals: NDArray[np.float64],
        kind: str,
        initial: NDArray[np.float64],
        domain: tuple[float, float],
    ) -> dict[str, Any]:
        del weights, normals, initial
        assert kind == "cylinder"
        return {
            "kind": kind,
            "parameters": [0, 0, 0, 0, 2, 0, 0],
            "axial_domain": domain,
            "residuals": [0.0] * len(points),
            "weighted_rms": 0.0,
        }

    monkeypatch.setattr("experiments.feature_graph.fit_seed", seeded)
    initial = graph_with_diagonal()
    payload = cast(dict[str, Any], initial.snapshot())["recipe"]
    payload["nodes"].extend(
        [
            {
                "id": "evidence",
                "label": "Cylinder evidence",
                "operation": "selection",
                "source": "scan",
                "ids": [int(initial.workspace.default.plane_ids[0])],
            },
            {
                "id": "cylinder",
                "label": "Cylinder",
                "operation": "fit",
                "kind": "cylinder",
                "selections": ["evidence"],
            },
            {
                "id": "tangent",
                "label": "Tangent",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "parallel_to_axis",
                "initial_angle_degrees": 0,
                "offset": 2,
            },
        ]
    )
    graph = FeatureGraph(initial.workspace, Recipe.model_validate(payload))
    before = evaluate(graph)
    refs = [{"feature": name} for name in ("cylinder", "diagonal", "tangent", "left")]
    result = candidate_faces(
        graph,
        FaceCandidatesRequest.model_validate(
            {"token": before["token"], "target": refs[0], "surfaces": refs}
        ),
    )
    candidates = {item["reference"]["feature"]: item for item in result["candidates"]}
    secant = candidates["diagonal"]
    assert secant["supported"] and secant["state"] == "proposed"
    assert secant["geometry"]["preview_only"]
    assert len(secant["geometry"]["curves"]) == 2
    tangent = candidates["tangent"]
    assert tangent["state"] == "uncertain" and not tangent["supported"]
    assert tangent["geometry"] is None
    assert candidates["left"]["geometry"] is None
    assert graph.snapshot() == before


def test_guided_apply_only_target_faces_and_replay() -> None:
    graph = graph_with_diagonal()
    plan, payload = draft(graph)
    regions = [r for r in plan["faces"][0]["regions"] if r["bounded"]]
    applied = apply_faces(
        graph,
        FacesApplyRequest.model_validate(
            {
                **payload,
                "proposal_token": plan["proposal_token"],
                "label": "Guided plate",
                "choices": [
                    {"surface": TARGET, "region_key": r["key"]} for r in regions
                ],
            }
        ),
    )
    owner = next(
        n for n in applied["recipe"]["nodes"] if n["operation"] == "build_faces"
    )
    assert owner["target"] == TARGET and owner["boundary_sources"] == []
    assert all(
        n["surface"] == TARGET
        for n in applied["recipe"]["nodes"]
        if n["operation"] in ("arranged_face", "trimmed_face")
    )
    state = evaluate(graph)
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert evaluate(replay)["results"] == state["results"]
    update = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": state["token"], "surfaces": REFS, "owner_id": owner["id"]}
        ),
    )
    assert update["target"] == TARGET


def test_guided_rejects_cutters_as_kept_faces_and_invalid_sources() -> None:
    graph = graph_with_diagonal()
    plan, payload = draft(graph)
    before = cast(dict[str, Any], graph.snapshot())
    with pytest.raises(ValueError, match="only retain cells"):
        _ = apply_faces(
            graph,
            FacesApplyRequest.model_validate(
                {
                    **payload,
                    "proposal_token": plan["proposal_token"],
                    "label": "Wrong target",
                    "choices": [
                        {
                            "surface": REFS[1],
                            "region_key": plan["faces"][0]["regions"][0]["key"],
                        }
                    ],
                }
            ),
        )
    with pytest.raises(ValueError, match="actual target boundary"):
        _ = preview_faces(
            graph,
            FacesPreviewRequest.model_validate(
                {**payload, "boundary_sources": ["left"]}
            ),
        )
    assert graph.snapshot() == before


def test_candidates_are_read_only_show_all_and_conflicts() -> None:
    graph = graph_with_diagonal()
    snapshot = evaluate(graph)
    payload = deepcopy(snapshot["recipe"])
    pair = {"first": TARGET, "second": REFS[1]}
    payload["nodes"].extend(
        [
            BuildFaces.model_validate(
                {
                    "id": "known_a",
                    "label": "First review",
                    "operation": "build_faces",
                    "surfaces": REFS[:2],
                    "target": TARGET,
                    "adjacencies": [{**pair, "state": "confirmed"}],
                }
            ).model_dump(),
            BuildFaces.model_validate(
                {
                    "id": "known_b",
                    "label": "Second review",
                    "operation": "build_faces",
                    "surfaces": REFS[:2],
                    "target": TARGET,
                    "adjacencies": [{**pair, "state": "rejected"}],
                }
            ).model_dump(),
        ]
    )
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    before = evaluate(graph)
    plan = candidate_faces(
        graph,
        FaceCandidatesRequest.model_validate(
            {"token": before["token"], "surfaces": REFS, "target": TARGET}
        ),
    )
    assert graph.snapshot() == before
    assert len(plan["candidates"]) == len(REFS) - 1
    left = next(c for c in plan["candidates"] if c["reference"] == REFS[1])
    assert left["conflict"] and left["state"] == "conflict"
    assert {item["state"] for item in left["known_adjacencies"]} == {
        "confirmed",
        "rejected",
    }
    with pytest.raises(StaleGraph):
        _ = candidate_faces(
            graph,
            FaceCandidatesRequest.model_validate(
                {"token": "old", "surfaces": REFS, "target": TARGET}
            ),
        )


def test_guided_target_and_neighbor_decisions_validate() -> None:
    graph = graph_with_diagonal()
    before = evaluate(graph)
    with pytest.raises(ValueError, match="must be a selected"):
        _ = preview_faces(
            graph,
            FacesPreviewRequest.model_validate(
                {"token": before["token"], "surfaces": REFS[1:], "target": TARGET}
            ),
        )

    with pytest.raises(ValueError, match="must involve the target"):
        _ = preview_faces(
            graph,
            FacesPreviewRequest.model_validate(
                {
                    "token": before["token"],
                    "surfaces": REFS,
                    "target": TARGET,
                    "adjacencies": [
                        {"first": REFS[1], "second": REFS[2], "state": "rejected"}
                    ],
                }
            ),
        )


def test_source_dependencies_and_aggregate_gap_after_refit() -> None:
    initial = graph_with_diagonal()
    payload = cast(dict[str, Any], initial.snapshot())["recipe"]
    for node in payload["nodes"]:
        if node["id"] == "diagonal":
            node["initial_angle_degrees"] = 270
            node["offset"] = 0
    payload["nodes"].extend(
        [
            {
                "id": "ceiling",
                "label": "Ceiling",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 1,
            },
            {
                "id": "upper_clip",
                "label": "Independent upper clip",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "parallel_to_axis",
                "initial_angle_degrees": 270,
                "offset": 3,
            },
        ]
    )
    graph = FeatureGraph(initial.workspace, Recipe.model_validate(payload))
    before = evaluate(graph)

    def retain(
        target: dict[str, Any],
        refs: list[dict[str, Any]],
        label: str,
        sources: list[str],
    ) -> dict[str, Any]:
        snapshot = cast(dict[str, Any], graph.snapshot())
        draft_payload = {
            "token": snapshot["token"],
            "target": target,
            "surfaces": refs,
            "boundary_sources": sources,
        }
        plan = preview_faces(graph, FacesPreviewRequest.model_validate(draft_payload))
        regions = [r for r in plan["faces"][0]["regions"] if r["bounded"]]
        return apply_faces(
            graph,
            FacesApplyRequest.model_validate(
                {
                    **draft_payload,
                    "proposal_token": plan["proposal_token"],
                    "label": label,
                    "choices": [
                        {"surface": target, "region_key": r["key"]} for r in regions
                    ],
                }
            ),
        )

    left = {"feature": "left", "surface": None}
    _ = retain(
        left,
        [left, TARGET, {"feature": "ceiling"}, REFS[3], REFS[4]],
        "Approved left wall",
        [],
    )
    source_state = evaluate(graph)
    source_face = next(
        node["id"]
        for node in source_state["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    )
    _ = retain(
        TARGET,
        [TARGET, REFS[1], REFS[2], REFS[3], {"feature": "upper_clip"}, REFS[5]],
        "Guided patches",
        [source_face],
    )
    state = evaluate(graph)
    owner = next(
        node for node in state["recipe"]["nodes"] if node["label"] == "Guided patches"
    )
    assert state["results"][owner["id"]]["shared_boundary_review"]["complete"]
    faces = [
        node for node in state["recipe"]["nodes"] if node["managed_by"] == owner["id"]
    ]
    assert len(faces) == 2 and all(
        source_face in node["boundary_sources"] for node in faces
    )
    assert all(node["finite_boundary_sources"] == [source_face] for node in faces)
    assert all(node["selector"]["finite_sides"] for node in faces)
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert evaluate(replay)["results"] == state["results"]
    # Older declarations used approved sources only for agreement checks. They
    # must not silently acquire a different finite-cut topology on re-evaluation.
    legacy = deepcopy(state["recipe"])
    for node in legacy["nodes"]:
        if node["id"] in {face["id"] for face in faces}:
            node.pop("finite_boundary_sources")
            node["selector"]["signs"][proposals.reference_key(left)] = "positive"
            node["selector"].pop("finite_sides")
    legacy_graph = FeatureGraph(graph.workspace, Recipe.model_validate(legacy))
    assert not evaluate(legacy_graph)["errors"]
    invalid = deepcopy(state["recipe"])
    invalid_face = next(
        node for node in invalid["nodes"] if node["id"] == faces[0]["id"]
    )
    invalid_face["finite_boundary_sources"] = ["left"]
    with pytest.raises(ValueError, match="unique approved boundary sources"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(invalid))
    invalid_face["finite_boundary_sources"] = [source_face]
    invalid_face["cutters"] = [ref for ref in invalid_face["cutters"] if ref != left]
    with pytest.raises(ValueError, match="must belong to a selected cutter"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(invalid))
    assert state["memberships"] == before["memberships"]
    changed = deepcopy(state["recipe"])
    next(node for node in changed["nodes"] if node["id"] == "upper_clip")["offset"] = (
        2.5
    )
    changed["nodes"].extend(
        [
            {
                "id": "downstream_review",
                "label": "Downstream review",
                "operation": "build_faces",
                "surfaces": owner["surfaces"],
                "reused_faces": [node["id"] for node in faces],
            },
            {
                "id": "independent_point",
                "label": "Independent point",
                "operation": "point",
                "initial_coordinates": [1, 2, 3],
            },
        ]
    )
    _ = graph.replace(Recipe.model_validate(changed), state["token"])
    with pytest.raises(ValueError, match="do not cover approved shared boundary"):
        _ = evaluate(graph)
    failed = cast(dict[str, Any], graph.snapshot())
    assert failed["states"][owner["id"]] == "failed"
    assert all(failed["states"][node["id"]] == "failed" for node in faces)
    assert failed["states"]["downstream_review"] == "blocked"
    assert "downstream_review" not in failed["results"]
    assert set(failed["diagnostics"]["downstream_review"]["blocked_by"]) == {
        owner["id"],
        *(node["id"] for node in faces),
    }
    assert failed["states"]["independent_point"] == "ready"
