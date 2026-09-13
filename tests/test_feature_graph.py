"""Current-graph replay, dependency invalidation and no retained edit history."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_session import NozzleSession, NozzleWorkspace, SessionFit

EXAMPLE = Path("examples/nozzle-bayonette-simplified")


@pytest.fixture
def graph() -> FeatureGraph:
    return FeatureGraph(
        NozzleWorkspace(EXAMPLE),
        Recipe.model_validate_json((EXAMPLE / "recipes/cone-plane.json").read_text()),
    )


def token(graph: FeatureGraph) -> str:
    return str(graph.snapshot()["token"])


def changed(graph: FeatureGraph, node_id: str, **changes: object) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    assert isinstance(payload, dict)
    for node in payload["nodes"]:
        if node["id"] == node_id:
            node.update(changes)
    return Recipe.model_validate(payload)


def test_recipe_replays_and_surface_kind_is_declared(graph: FeatureGraph) -> None:
    state = graph.evaluate(token(graph))
    result = state["result"]
    assert isinstance(result, dict)
    assert result["reference_diameter"] == pytest.approx(18.79278, abs=1e-5)
    old_token = token(graph)
    state = graph.replace(changed(graph, "side", kind="cylinder"), old_token)
    assert state["result"] is None
    assert state["states"] == {
        "scan": "ready",
        "outer_band": "ready",
        "top_face": "ready",
        "side": "stale",
        "end": "ready",
        "perpendicular": "stale",
        "fit": "stale",
    }
    result = graph.evaluate(token(graph))["result"]
    assert isinstance(result, dict)
    assert result["reference_diameter"] == pytest.approx(18.79299, abs=1e-5)
    assert result["signed_half_angle_degrees"] == 0
    with pytest.raises(StaleGraph):
        _ = graph.replace(changed(graph, "side", kind="cone"), old_token)


def test_save_load_has_current_graph_only_and_independent_snapshots(
    graph: FeatureGraph,
) -> None:
    _ = graph.evaluate(token(graph))
    recipe = changed(graph, "outer_band", ids=graph.workspace.default.lateral_ids[::2])
    _ = graph.replace(recipe, token(graph))
    snapshot = graph.snapshot()
    assert snapshot["result"] is None
    assert set(snapshot) == {"recipe", "token", "states", "errors", "result"}
    assert set(recipe.model_dump()) == {"schema_version", "nodes", "output"}
    loaded = FeatureGraph(
        graph.workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    first, second = (
        graph.evaluate(token(graph))["result"],
        loaded.evaluate(token(loaded))["result"],
    )
    assert isinstance(first, dict) and isinstance(second, dict)
    np.testing.assert_allclose(
        first["reference_diameter"], second["reference_diameter"]
    )
    first["reference_diameter"] = -1
    assert graph.snapshot()["result"] != first


@pytest.mark.parametrize(
    ("node_id", "changes", "message"),
    [
        ("outer_band", {"source": "outer_band"}, "cycle"),
        ("side", {"selection": "absent"}, "missing"),
        ("scan", {"source_sha256": "wrong"}, "binding"),
        ("scan", {"reference_sha256": "wrong"}, "binding"),
        ("outer_band", {"ids": [1, 1]}, "unique"),
        ("side", {"kind": "plane"}, "relationship"),
    ],
)
def test_rejects_bad_graph_without_changing_current_state(
    graph: FeatureGraph, node_id: str, changes: dict[str, object], message: str
) -> None:
    before = graph.snapshot()
    with pytest.raises(ValueError, match=message):
        _ = graph.replace(changed(graph, node_id, **changes), token(graph))
    assert graph.snapshot() == before


def test_failure_status_is_authoritative(graph: FeatureGraph) -> None:
    _ = graph.replace(changed(graph, "top_face", ids=[]), token(graph))
    with pytest.raises(ValueError, match="select at least"):
        _ = graph.evaluate(token(graph))
    assert cast(dict[str, str], graph.snapshot()["states"])["fit"] == "failed"
    assert graph.snapshot()["result"] is None


def test_stale_worker_cannot_publish_after_edit(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = Event(), Event()
    original = graph.workspace.fit

    def delayed(
        session: NozzleSession,
        kind: str = "cone",
        support: tuple[float, float] | None = None,
    ) -> SessionFit:
        started.set()
        assert release.wait(5)
        return original(session, kind, support)

    monkeypatch.setattr(graph.workspace, "fit", delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(graph.evaluate, token(graph))
        assert started.wait(5)
        try:
            _ = graph.replace(changed(graph, "side", kind="cylinder"), token(graph))
        finally:
            release.set()
        with pytest.raises(StaleGraph):
            _ = future.result()
    assert graph.snapshot()["result"] is None
    assert cast(dict[str, str], graph.snapshot()["states"])["fit"] == "stale"


def coaxial_recipe(graph: FeatureGraph) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    ids = nodes["outer_band"]["ids"]
    nodes["outer_band"]["ids"] = ids[::2]
    payload["nodes"].extend(
        [
            {
                "id": "extra_selection",
                "label": "Other band observations",
                "operation": "selection",
                "source": "scan",
                "ids": ids[1::2],
            },
            {
                "id": "extra",
                "label": "Additional cone",
                "operation": "surface",
                "selection": "extra_selection",
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "axis",
                "label": "Coaxial",
                "operation": "coaxial",
                "surface": "extra",
                "reference": "side",
            },
        ]
    )
    nodes["fit"]["constraints"].append("axis")
    return Recipe.model_validate(payload)


def test_coaxial_graph_replay_and_invalidation(graph: FeatureGraph) -> None:
    _ = graph.replace(coaxial_recipe(graph), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert set(result["surfaces"]) == {"side", "end", "extra"}
    np.testing.assert_array_equal(
        result["surfaces"]["side"]["parameters"][:4],
        result["surfaces"]["extra"]["parameters"][:4],
    )
    assert result["reference_diameter"] == pytest.approx(18.79278, abs=0.002)
    saved = Recipe.model_validate(graph.snapshot()["recipe"])
    replay = FeatureGraph(graph.workspace, saved)
    assert replay.evaluate(token(replay))["result"] == result
    state = graph.replace(changed(graph, "extra", kind="cylinder"), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["fit"] == "stale"
    result = cast(dict[str, Any], graph.evaluate(token(graph))["result"])
    assert result["surfaces"]["extra"]["parameters"][6] == 0


@pytest.mark.parametrize(
    "mutation", ["self", "plane_axis", "overlap", "disconnected", "empty"]
)
def test_coaxial_invalid_graphs_and_empty_fit(
    graph: FeatureGraph, mutation: str
) -> None:
    payload = coaxial_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    if mutation == "self":
        nodes["axis"]["reference"] = "extra"
    elif mutation == "plane_axis":
        nodes["axis"]["reference"] = "end"
    elif mutation == "overlap":
        nodes["extra_selection"]["ids"] = nodes["outer_band"]["ids"]
    elif mutation == "disconnected":
        nodes["axis"].update(operation="perpendicular", lateral="extra", plane="end")
        del nodes["axis"]["surface"], nodes["axis"]["reference"]
    else:
        nodes["extra_selection"]["ids"] = []
    recipe = Recipe.model_validate(payload)
    if mutation == "empty":
        _ = graph.replace(recipe, token(graph))
        with pytest.raises(ValueError, match="seven"):
            _ = graph.evaluate(token(graph))
        assert graph.snapshot()["result"] is None
    else:
        with pytest.raises(ValueError):
            _ = graph.replace(recipe, token(graph))


def test_reads_original_single_constraint_recipe(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    fit = next(n for n in payload["nodes"] if n["id"] == "fit")
    fit["constraint"] = fit.pop("constraints")[0]
    recipe = Recipe.model_validate(payload)
    restored = FeatureGraph(graph.workspace, recipe).snapshot()
    stored = cast(dict[str, Any], restored["recipe"])
    fit = next(n for n in stored["nodes"] if n["id"] == "fit")
    assert fit["constraints"] == ["perpendicular"]
    assert "constraint" not in fit
