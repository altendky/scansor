"""Readiness includes exact validation inputs and the requested solve context."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

import experiments.feature_graph as feature_graph
import experiments.shared_face_boundaries as shared
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    SharedBoundaryInputs,
    StaleGraph,
)
from experiments.nozzle_session import NozzleWorkspace
from tests.test_arranged_face_workflow import graph_with_diagonal
from tests.test_feature_graph_provider_cache import frame_graph
from tests.test_guided_face_builder import REFS, TARGET


def snapshot(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


@pytest.fixture(scope="module")
def provider_workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def guided_graph() -> tuple[FeatureGraph, str]:
    initial = graph_with_diagonal()
    recipe = snapshot(initial)["recipe"]
    for node in recipe["nodes"]:
        if node["id"] == "diagonal":
            node.update(initial_angle_degrees=270, offset=0)
    recipe["nodes"].extend(
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
                "label": "Upper clip",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "parallel_to_axis",
                "initial_angle_degrees": 270,
                "offset": 3,
            },
        ]
    )
    graph = FeatureGraph(initial.workspace, Recipe.model_validate(recipe))
    _ = graph.evaluate(str(snapshot(graph)["token"]), all_actions=True)

    def retain(
        target: dict[str, Any],
        surfaces: list[dict[str, Any]],
        sources: list[str],
        label: str,
    ) -> None:
        payload = {
            "token": snapshot(graph)["token"],
            "target": target,
            "surfaces": surfaces,
            "boundary_sources": sources,
        }
        plan = preview_faces(graph, FacesPreviewRequest.model_validate(payload))
        _ = apply_faces(
            graph,
            FacesApplyRequest.model_validate(
                {
                    **payload,
                    "proposal_token": plan["proposal_token"],
                    "label": label,
                    "choices": [
                        {"surface": target, "region_key": region["key"]}
                        for region in plan["faces"][0]["regions"]
                        if region["bounded"]
                    ],
                }
            ),
        )
        _ = graph.evaluate(str(snapshot(graph)["token"]), all_actions=True)

    left = {"feature": "left", "surface": None}
    retain(
        left,
        [left, TARGET, {"feature": "ceiling"}, REFS[3], REFS[4]],
        [],
        "Approved wall",
    )
    source = next(
        node["id"]
        for node in snapshot(graph)["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    )
    retain(
        TARGET,
        [TARGET, REFS[1], REFS[2], REFS[3], {"feature": "upper_clip"}, REFS[5]],
        [source],
        "Guided patches",
    )
    owner = next(
        node["id"]
        for node in snapshot(graph)["recipe"]["nodes"]
        if node["label"] == "Guided patches"
    )
    return graph, owner


def spy_checks(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    original = shared.check_shared_boundaries
    calls: list[bool] = []

    def check(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs.get("require_complete", True))
        return original(*args, **kwargs)

    monkeypatch.setattr(shared, "check_shared_boundaries", check)
    return calls


def test_ready_ensure_is_noop_and_explicit_evaluation_reuses_validation(
    guided_graph: tuple[FeatureGraph, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, owner = guided_graph
    before = snapshot(graph)
    calls = spy_checks(monkeypatch)
    assert not graph.needs_evaluation(before["token"], [owner])
    assert graph.ensure_current(before["token"], [owner]) == before
    assert graph.evaluate(before["token"], target=owner) == before
    assert graph.evaluate(before["token"], all_actions=True) == before
    assert calls == []
    original_snapshot = graph.snapshot
    snapshots = 0

    def counted_snapshot() -> dict[str, object]:
        nonlocal snapshots
        snapshots += 1
        return original_snapshot()

    monkeypatch.setattr(graph, "snapshot", counted_snapshot)
    assert graph.ensure_current(before["token"], [owner]) == before
    assert snapshots == 1
    snapshots = 0
    assert graph.evaluate(before["token"], target=owner) == before
    assert snapshots == 1
    snapshots = 0
    assert graph.evaluate(before["token"], all_actions=True) == before
    assert snapshots == 1
    snapshots = 0
    needed, current = graph.readiness_status(before["token"], [owner])
    assert not needed and current is not None and current["required_failures"] == []
    assert snapshots == 1


def test_unrelated_edit_preserves_review_and_target_scope(
    guided_graph: tuple[FeatureGraph, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, owner = guided_graph
    before = snapshot(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"].append(
        {
            "id": "independent",
            "label": "Independent point",
            "operation": "point",
            "initial_coordinates": [1, 2, 3],
        }
    )
    changed = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), before["token"])
    )
    calls = spy_checks(monkeypatch)
    current = graph.ensure_current(changed["token"], [owner])
    assert cast(dict[str, Any], current)["states"]["independent"] == "unevaluated"
    _ = graph.ensure_current(changed["token"], ["independent"])
    assert calls == []
    assert snapshot(graph)["results"][owner] == before["results"][owner]


def test_changed_geometry_invalidates_review_and_preserves_failures_until_retry(
    guided_graph: tuple[FeatureGraph, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, owner = guided_graph
    before = snapshot(graph)
    payload = deepcopy(before["recipe"])
    next(node for node in payload["nodes"] if node["id"] == "upper_clip")["offset"] = (
        2.5
    )
    changed = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), before["token"])
    )
    calls = spy_checks(monkeypatch)
    assert graph.needs_evaluation(changed["token"], [owner])
    with pytest.raises(ValueError, match="do not cover approved shared boundary"):
        _ = graph.ensure_current(changed["token"], [owner])
    failed = snapshot(graph)
    assert failed["states"][owner] == "failed"
    assert True in calls
    calls.clear()
    assert graph.ensure_current(changed["token"], [owner]) == failed
    assert not calls
    with pytest.raises(ValueError, match="do not cover approved shared boundary"):
        _ = graph.evaluate(changed["token"], target=owner)
    assert calls
    restored = cast(
        dict[str, Any],
        graph.replace(Recipe.model_validate(before["recipe"]), changed["token"]),
    )
    ready = cast(dict[str, Any], graph.ensure_current(restored["token"], [owner]))
    assert ready["states"][owner] == "ready"
    assert ready["results"][owner]["shared_boundary_review"]["complete"]


def test_validation_success_cannot_publish_after_inflight_edit(
    guided_graph: tuple[FeatureGraph, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, owner = guided_graph
    before = snapshot(graph)
    graph._validation_reviews.clear()  # pyright: ignore[reportPrivateUsage]
    original = shared.check_shared_boundaries

    def edit_during_review(*args: Any, **kwargs: Any) -> dict[str, Any]:
        review = original(*args, **kwargs)
        payload = deepcopy(snapshot(graph)["recipe"])
        payload["nodes"].append(
            {
                "id": "new_point",
                "label": "New point",
                "operation": "point",
                "initial_coordinates": [1, 2, 3],
            }
        )
        _ = graph.replace(Recipe.model_validate(payload), str(snapshot(graph)["token"]))
        return review

    monkeypatch.setattr(shared, "check_shared_boundaries", edit_during_review)
    with pytest.raises(StaleGraph, match="shared-boundary review"):
        _ = graph.ensure_current(before["token"], [owner])
    assert (owner, True) not in graph._validation_reviews  # pyright: ignore[reportPrivateUsage]


def test_signature_covers_identity_guidance_and_exact_geometry() -> None:
    inputs = SharedBoundaryInputs(
        ["face"],
        [{"surface": {"feature": "top"}, "geometry": {"origin": [0, 0, 0]}}],
        {"feature": "top"},
        [
            {
                "id": "source",
                "record": {"geometry": {"origin": [0, 0, 0]}},
                "boundary_uses": [
                    {"intersection": {"point": [0, 0, 0]}, "keep": "positive"}
                ],
            }
        ],
        {"kind": "plane", "axis": [0, 0, 1], "origin": [0, 0, 0]},
    )
    signature = FeatureGraph._review_signature  # pyright: ignore[reportPrivateUsage]
    original = signature(inputs, True)
    variations = [deepcopy(inputs) for _ in range(5)]
    variations[0].face_ids[0] = "replacement"
    variations[1].sources[0]["boundary_uses"][0]["keep"] = "negative"
    variations[2].sources[0]["boundary_uses"][0]["intersection"]["point"][0] = 1e-12
    variations[3].faces[0]["geometry"]["origin"][0] = 1e-12
    variations[4].geometry["origin"][0] = 1e-12
    assert all(signature(item, True) != original for item in variations)
    inputs.faces[0]["shared_boundary_review"] = {"complete": True}
    inputs.sources[0]["record"]["shared_boundary_review"] = {"complete": False}
    assert signature(inputs, True) == original
    assert signature(inputs, False) != original


def test_unexpected_validation_error_marks_owner_and_children_failed(
    guided_graph: tuple[FeatureGraph, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph, owner = guided_graph
    graph._validation_reviews.clear()  # pyright: ignore[reportPrivateUsage]

    def unexpected(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("unexpected validation failure")

    monkeypatch.setattr(shared, "check_shared_boundaries", unexpected)
    with pytest.raises(RuntimeError, match="unexpected validation failure"):
        _ = graph.ensure_current(str(snapshot(graph)["token"]), [owner])
    failed = snapshot(graph)
    assert failed["states"][owner] == "failed"
    assert all(
        failed["states"][node["id"]] == "failed"
        for node in failed["recipe"]["nodes"]
        if node["managed_by"] == owner and node["operation"] == "arranged_face"
    )
    assert not graph.needs_evaluation(failed["token"], [owner])


@pytest.mark.parametrize("reference", ["axis", "datum"])
def test_ensure_detects_provider_switch_without_recipe_change(
    provider_workspace: NozzleWorkspace, reference: str
) -> None:
    graph = frame_graph(provider_workspace, reference)
    before = cast(
        dict[str, Any], graph.ensure_current(str(snapshot(graph)["token"]), ["frame"])
    )
    assert not graph.needs_evaluation(before["token"], ["frame"])
    assert graph.needs_evaluation(before["token"], ["explicit_solve"])
    explicit = cast(
        dict[str, Any], graph.ensure_current(before["token"], ["explicit_solve"])
    )
    assert explicit["token"] == before["token"]
    assert "resolved_by" not in explicit["results"]["axis"]
    assert graph.needs_evaluation(before["token"], ["frame"])
    after = cast(dict[str, Any], graph.ensure_current(before["token"], ["frame"]))
    assert after["results"]["frame"] == before["results"]["frame"]


def test_ensure_does_not_retry_failed_plane_provider_for_independent_work(
    provider_workspace: NozzleWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = Recipe.model_validate_json(
        Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json").read_text()
    ).model_dump()
    nodes = [next(node for node in payload["nodes"] if node["id"] == "scan")]
    ids = provider_workspace.default.plane_ids
    for name, selected_ids in (("a", ids[::2]), ("b", ids[1::2])):
        nodes.extend(
            [
                {
                    "id": name + "_selection",
                    "label": name + " selection",
                    "operation": "selection",
                    "source": "scan",
                    "ids": selected_ids,
                },
                {
                    "id": name,
                    "label": name,
                    "operation": "fit",
                    "kind": "plane",
                    "selections": [name + "_selection"],
                },
            ]
        )
    nodes.extend(
        [
            {
                "id": "ab",
                "label": "Parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["a", "b"],
            },
            {
                "id": "independent",
                "label": "Independent point",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
        ]
    )
    graph = FeatureGraph(
        provider_workspace,
        Recipe.model_validate({**payload, "nodes": nodes, "output": "ab"}),
    )
    original = feature_graph.fit_plane_relationships
    attempts: list[int] = []

    def fail_once(*args: Any, **kwargs: Any) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("temporary plane provider failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(feature_graph, "fit_plane_relationships", fail_once)
    token = str(snapshot(graph)["token"])
    with pytest.raises(ValueError, match="temporary plane provider failure"):
        _ = graph.ensure_current(token, ["ab"])
    with pytest.raises(ValueError, match="temporary plane provider failure"):
        _ = graph.ensure_current(token, ["ab", "independent"])
    state = snapshot(graph)
    assert len(attempts) == 1
    assert state["states"]["ab"] == "failed"
    assert state["states"]["independent"] == "ready"

    ready = cast(dict[str, Any], graph.evaluate(token, target="ab"))
    assert len(attempts) == 2
    assert ready["states"]["ab"] == "ready"
