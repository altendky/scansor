"""Conditional snapshots follow publications independently of recipe tokens."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest

import experiments.feature_graph as feature_graph
from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_session import NozzleWorkspace
from tests.test_body_graph import face_graph, fake_assembly
from tests.test_feature_graph_provider_cache import frame_graph


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def graph(workspace: NozzleWorkspace) -> FeatureGraph:
    return FeatureGraph(
        workspace,
        Recipe.model_validate(
            {
                "nodes": [
                    {
                        "id": "axis",
                        "label": "Axis",
                        "operation": "axis",
                        "initial_parameters": [0, 0, 0, 0],
                    },
                    {
                        "id": "plane",
                        "label": "Plane",
                        "operation": "reference_plane",
                        "axis": "axis",
                        "construction": "perpendicular_to_axis",
                        "initial_angle_degrees": None,
                        "offset": 0,
                    },
                    {
                        "id": "point",
                        "label": "Independent point",
                        "operation": "point",
                        "initial_coordinates": [1, 2, 3],
                    },
                ],
                "output": "plane",
            }
        ),
    )


def snapshot(graph: FeatureGraph, revision: int | None = None) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot(revision))


def test_unchanged_snapshot_bypasses_geometry_and_recipe_serialization(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(graph)
    ready = cast(dict[str, Any], graph.evaluate(before["token"], all_actions=True))
    assert ready["token"] == before["token"]
    assert ready["revision"] > before["revision"]

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unchanged snapshot must not inspect recipe or geometry")

    with monkeypatch.context() as conditional:
        conditional.setattr(feature_graph, "deepcopy", unexpected)
        conditional.setattr(Recipe, "model_dump", unexpected)
        conditional.setattr(Recipe, "model_dump_json", unexpected)
        conditional.setattr(feature_graph, "directed_axis_result", unexpected)
        assert snapshot(graph, ready["revision"]) == {
            "token": ready["token"],
            "revision": ready["revision"],
            "unchanged": True,
        }

    assert snapshot(graph, before["revision"]) == ready
    assert graph.evaluate(ready["token"], all_actions=True) == ready
    assert graph.ensure_current(ready["token"], all_actions=True) == ready
    ready["results"]["axis"]["axis_display"][0] = 99
    ready["derived"]["point"]["coordinates"][0] = 99
    ready["recipe"]["nodes"][0]["label"] = "Changed snapshot"
    fresh = snapshot(graph)
    assert fresh["results"]["axis"]["axis_display"][0] != 99
    assert fresh["derived"]["point"]["coordinates"] == [1, 2, 3]
    assert fresh["recipe"]["nodes"][0]["label"] == "Axis"


def test_large_preview_has_independent_full_copies_and_bounded_unchanged_response(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    vertex_count = 10_000
    preview = {
        "positions": [
            value for vertex in range(vertex_count) for value in (vertex, 0, 0)
        ],
        "indices": list(range(vertex_count - vertex_count % 3)),
    }
    # A disposable published preview isolates transport from numerical/CAD work.
    with graph.lock:
        graph._derived["point"] = {"preview": preview}  # pyright: ignore[reportPrivateUsage]
    full = snapshot(graph)
    full_bytes = len(json.dumps(full, allow_nan=False).encode())
    copied = full["results"]["point"]["preview"]
    copied["positions"][0] = -1
    copied["indices"][0] = -1
    assert full["derived"]["point"]["preview"] == preview
    assert preview["positions"][0] == preview["indices"][0] == 0
    assert snapshot(graph)["results"]["point"]["preview"] == preview

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("matching revision must not copy the large preview")

    monkeypatch.setattr(feature_graph, "deepcopy", unexpected)
    compact = snapshot(graph, full["revision"])
    assert compact == {
        "token": full["token"],
        "revision": full["revision"],
        "unchanged": True,
    }
    compact_bytes = len(json.dumps(compact, allow_nan=False).encode())
    assert compact_bytes < 256
    assert full_bytes > 1_000 * compact_bytes


@pytest.mark.parametrize("cancellation", [None, "same", "restore"])
def test_running_and_cancelled_publications_with_restored_token(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch, cancellation: str | None
) -> None:
    before = snapshot(graph)
    entered, release = Event(), Event()
    original = feature_graph.directed_axis_result

    def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(10), "test did not release evaluator"
        return original(*args, **kwargs)

    monkeypatch.setattr(feature_graph, "directed_axis_result", delayed)
    cancelled: dict[str, Any] | None = None
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(graph.evaluate, before["token"])
        try:
            assert entered.wait(10)
            running = snapshot(graph, before["revision"])
            assert running["revision"] > before["revision"]
            assert running["states"]["axis"] == "running"
            assert snapshot(graph, running["revision"])["unchanged"]
            if cancellation:
                if cancellation == "restore":
                    changed = Recipe.model_validate(before["recipe"])
                    changed.nodes[0] = changed.nodes[0].model_copy(
                        update={"initial_parameters": (1, 0, 0, 0)}
                    )
                    intermediate = cast(
                        dict[str, Any], graph.replace(changed, before["token"])
                    )
                    assert intermediate["token"] != before["token"]
                    assert intermediate["revision"] > running["revision"]
                else:
                    intermediate = running
                replacement = Recipe.model_validate(before["recipe"])
                cancelled = cast(
                    dict[str, Any], graph.replace(replacement, intermediate["token"])
                )
                assert cancelled["token"] == before["token"]
                assert cancelled["revision"] > intermediate["revision"]
                assert cancelled["states"]["axis"] == "stale"
        finally:
            release.set()
        if cancellation:
            assert cancelled is not None
            with pytest.raises(StaleGraph):
                _ = future.result(timeout=10)
            assert snapshot(graph) == cancelled
        else:
            ready = cast(dict[str, Any], future.result(timeout=10))
            assert ready["revision"] > running["revision"]
            assert ready["states"]["plane"] == "ready"
            assert ready["token"] == before["token"]


def test_failure_blocking_retained_ensure_and_explicit_retry(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = snapshot(graph)
    original = feature_graph.directed_axis_result
    attempts: list[int] = []

    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        attempts.append(1)
        raise ValueError("axis unavailable")

    monkeypatch.setattr(feature_graph, "directed_axis_result", fail)
    with pytest.raises(ValueError, match="axis unavailable"):
        _ = graph.evaluate(before["token"], all_actions=True)
    failed = snapshot(graph, before["revision"])
    assert failed["revision"] > before["revision"]
    assert failed["token"] == before["token"]
    assert failed["states"] == {
        "axis": "failed",
        "plane": "blocked",
        "point": "ready",
    }
    assert failed["diagnostics"]["plane"] == {"blocked_by": ["axis"]}
    assert graph.ensure_current(before["token"], all_actions=True) == failed
    assert attempts == [1]
    monkeypatch.setattr(feature_graph, "directed_axis_result", original)
    repaired = cast(dict[str, Any], graph.evaluate(before["token"], all_actions=True))
    assert repaired["revision"] > failed["revision"]
    assert set(repaired["states"].values()) == {"ready"}
    assert not repaired["errors"] and not repaired["diagnostics"]


def test_provider_removal_and_reinsertion_publish_resolved_geometry(
    workspace: NozzleWorkspace,
) -> None:
    graph = frame_graph(workspace, "datum")
    token = snapshot(graph)["token"]
    initial = cast(dict[str, Any], graph.evaluate(token, "frame"))
    removed = cast(dict[str, Any], graph.evaluate(token, "explicit_solve"))
    assert removed["revision"] > initial["revision"]
    assert removed["token"] == initial["token"]
    assert removed["states"]["frame"] == "stale"
    assert "resolved_by" not in removed["results"]["axis"]
    assert "frame" not in removed["results"]
    restored = cast(dict[str, Any], graph.evaluate(token, "frame"))
    assert restored["revision"] > removed["revision"]
    assert restored["results"]["axis"]["resolved_by"] == "connected_fits"
    assert restored["results"]["frame"] == initial["results"]["frame"]


def test_in_place_owner_review_publication_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = face_graph(owner=True)
    _ = fake_assembly(monkeypatch)
    marker = 1

    def review(*_args: Any) -> dict[str, Any]:
        return {"complete": True, "marker": marker}

    monkeypatch.setattr(graph, "_shared_boundary_review", review)
    token = snapshot(graph)["token"]
    before = cast(dict[str, Any], graph.evaluate(token))
    assert graph.evaluate(token) == before
    marker = 2
    changed = cast(dict[str, Any], graph.evaluate(token))
    assert changed["revision"] > before["revision"]
    assert changed["token"] == before["token"]
    assert changed["states"] == before["states"]
    assert changed["results"]["owner"]["shared_boundary_review"]["marker"] == 2
    assert graph.evaluate(token) == changed

    def fail(*_args: Any) -> dict[str, Any]:
        raise ValueError("review unavailable")

    monkeypatch.setattr(graph, "_shared_boundary_review", fail)
    with pytest.raises(ValueError, match="review unavailable"):
        _ = graph.evaluate(token)
    failed = snapshot(graph, changed["revision"])
    assert failed["revision"] > changed["revision"]
    assert failed["states"]["owner"] == "failed"
    assert failed["states"]["body"] == "blocked"
    assert "body" not in failed["results"]
