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
    assert set(snapshot) == {
        "recipe",
        "token",
        "states",
        "errors",
        "diagnostics",
        "result",
        "derived",
        "memberships",
        "results",
    }
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
        ("side", {"selections": ["absent"]}, "missing"),
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
    with pytest.raises(ValueError, match="at least"):
        _ = graph.evaluate(token(graph))
    assert cast(dict[str, str], graph.snapshot()["states"])["end"] == "failed"
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
    payload["schema_version"] = 1
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
    if mutation in {"empty", "overlap"}:
        _ = graph.replace(recipe, token(graph))
        with pytest.raises(ValueError, match=r"at least|overlap"):
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


def test_first_surface_selection_depth_round_trip(graph: FeatureGraph) -> None:
    _ = graph.replace(changed(graph, "outer_band", depth="first_surface"), token(graph))
    restored = FeatureGraph(
        graph.workspace, Recipe.model_validate(graph.snapshot()["recipe"])
    )
    recipe = cast(dict[str, Any], restored.snapshot()["recipe"])
    assert (
        next(n for n in recipe["nodes"] if n["id"] == "outer_band")["depth"]
        == "first_surface"
    )
    with pytest.raises(ValueError):
        _ = changed(graph, "outer_band", depth="front_normals")


def proposal_recipe(graph: FeatureGraph) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "seed_fit",
                "label": "Seed only",
                "operation": "seed_fit",
                "selection": "outer_band",
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "growth",
                "label": "Connected additions",
                "operation": "growth",
                "seed_fit": "seed_fit",
                "barriers": ["top_face"],
                "distance": 0.05,
                "angle_degrees": 20,
            },
        ]
    )
    payload["schema_version"] = 1
    return Recipe.model_validate(payload)


def test_proposal_apply_replay_and_seed_invalidation(graph: FeatureGraph) -> None:
    original = graph.workspace.default.lateral_ids
    _ = graph.replace(proposal_recipe(graph), token(graph))
    state = graph.evaluate(token(graph), "growth")
    derived = cast(dict[str, Any], state["derived"])
    assert derived["growth"]["added_ids"]
    assert set(original) <= set(derived["growth"]["ids"])
    assert not set(derived["growth"]["ids"]).intersection(
        graph.workspace.default.plane_ids
    )
    assert state["result"] is None
    # Reusing a later proposal requires later fit/constraint/joint actions.
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "grown_fit",
                "label": "Grown cone",
                "operation": "fit",
                "selections": ["growth"],
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "grown_constraint",
                "label": "Grown perpendicular",
                "operation": "perpendicular",
                "lateral": "grown_fit",
                "plane": "end",
            },
            {
                "id": "grown_joint",
                "label": "Grown joint",
                "operation": "joint_fit",
                "constraints": ["grown_constraint"],
            },
        ]
    )
    payload["output"] = "grown_joint"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    assert state["result"] is not None
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == state["result"]
    state = graph.replace(changed(graph, "outer_band", ids=original[::2]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, Any], state["memberships"])["growth"] is None
    assert "growth" not in cast(dict[str, Any], state["derived"])


def test_proposal_needs_no_plane_observations(graph: FeatureGraph) -> None:
    _ = graph.replace(proposal_recipe(graph), token(graph))
    _ = graph.replace(changed(graph, "top_face", ids=[]), token(graph))
    assert "growth" in cast(
        dict[str, Any], graph.evaluate(token(graph), "growth")["derived"]
    )


def test_growth_cycles_and_invalid_thresholds_rejected(graph: FeatureGraph) -> None:
    _ = graph.replace(proposal_recipe(graph), token(graph))
    with pytest.raises(ValueError, match="cycle"):
        _ = graph.replace(changed(graph, "growth", barriers=["growth"]), token(graph))
    with pytest.raises(ValueError):
        _ = changed(graph, "growth", distance=float("nan"))


def test_late_seed_fit_cannot_publish_after_edit(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.feature_graph as module

    _ = graph.replace(proposal_recipe(graph), token(graph))
    started, release = Event(), Event()
    from experiments.selection_growth import fit_seed as original_fit

    def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(5)
        return original_fit(*args, **kwargs)

    monkeypatch.setattr(module, "fit_seed", delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(graph.evaluate, token(graph), "growth")
        assert started.wait(5)
        try:
            _ = graph.replace(
                changed(
                    graph, "outer_band", ids=graph.workspace.default.lateral_ids[::2]
                ),
                token(graph),
            )
        finally:
            release.set()
        with pytest.raises(StaleGraph):
            _ = pending.result(timeout=5)
    assert graph.snapshot()["derived"] == {}


def test_ordered_references_and_reorder_preserves_results(graph: FeatureGraph) -> None:
    before = graph.evaluate(token(graph))
    payload = cast(dict[str, Any], before["recipe"])
    assert payload["schema_version"] == 2
    # Independent selections can trade places without changing their identities.
    payload["nodes"][1:3] = reversed(payload["nodes"][1:3])
    after = graph.replace(Recipe.model_validate(payload), token(graph))
    assert after["results"] == before["results"]
    assert after["states"] == before["states"]
    payload["nodes"][0:2] = reversed(payload["nodes"][0:2])
    with pytest.raises(ValueError, match="earlier"):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))
    assert graph.snapshot() == after


def test_multi_selection_union_and_independent_plane(graph: FeatureGraph) -> None:
    baseline = cast(dict[str, Any], graph.evaluate(token(graph), "end")["results"])[
        "end"
    ]
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    ids = graph.workspace.default.plane_ids
    payload["nodes"].extend(
        [
            {
                "id": "patch_a",
                "label": "Plane patch A",
                "operation": "selection",
                "source": "scan",
                "ids": ids[::2],
            },
            {
                "id": "patch_b",
                "label": "Plane patch B",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            },
            {
                "id": "other_plane",
                "label": "Another plane",
                "operation": "fit",
                "selections": ["patch_a", "patch_b"],
                "kind": "plane",
            },
        ]
    )
    payload["output"] = "other_plane"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["results"])["other_plane"]
    assert result["ids"] == ids
    np.testing.assert_allclose(result["parameters"], baseline["parameters"])
    assert result["weighted_rms"] == baseline["weighted_rms"]
    assert state["result"] is None  # No joint solve was requested.


def test_joint_keeps_standalone_results(graph: FeatureGraph) -> None:
    standalone = cast(dict[str, Any], graph.evaluate(token(graph), "side")["results"])[
        "side"
    ]
    state = graph.evaluate(token(graph))
    results = cast(dict[str, Any], state["results"])
    assert results["side"] == standalone
    assert not np.allclose(
        results["fit"]["surfaces"]["side"]["parameters"],
        standalone["parameters"],
        atol=1e-8,
        rtol=0,
    )


def test_migrates_unordered_legacy_recipe(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["schema_version"] = 1
    for node in payload["nodes"]:
        if node["operation"] == "fit":
            node["operation"] = "surface"
            node["selection"] = node.pop("selections")[0]
    payload["nodes"].reverse()
    migrated = Recipe.model_validate(payload)
    restored = FeatureGraph(graph.workspace, migrated)
    assert migrated.schema_version == 2
    assert (
        restored.evaluate(token(restored))["result"]
        == graph.evaluate(token(graph))["result"]
    )


def test_additional_joint_plane_replay_and_invalidation(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    ids = nodes["top_face"]["ids"]
    nodes["top_face"]["ids"] = ids[::2]
    joint = nodes["fit"]
    payload["nodes"].remove(joint)
    payload["nodes"].extend(
        [
            {
                "id": "other_patch",
                "label": "Other plane patch",
                "operation": "selection",
                "source": "scan",
                "ids": ids[1::2],
            },
            {
                "id": "other_plane",
                "label": "Other plane",
                "operation": "fit",
                "selections": ["other_patch"],
                "kind": "plane",
            },
            {
                "id": "other_perpendicular",
                "label": "Other perpendicular",
                "operation": "perpendicular",
                "lateral": "side",
                "plane": "other_plane",
            },
            joint,
        ]
    )
    joint["constraints"].append("other_perpendicular")
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert set(result["surfaces"]) == {"side", "end", "other_plane"}
    np.testing.assert_array_equal(
        result["surfaces"]["end"]["parameters"][:4],
        result["surfaces"]["other_plane"]["parameters"][:4],
    )
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    state = graph.replace(changed(graph, "other_patch", ids=ids[1::4]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["end"] == "ready"
    assert graph.evaluate(token(graph))["result"] is not None


def test_overlap_diagnostics_exact_vertices_and_invalidation(
    graph: FeatureGraph,
) -> None:
    payload = coaxial_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    shared = nodes["outer_band"]["ids"][::5]
    nodes["extra_selection"]["ids"] = sorted(
        set(nodes["extra_selection"]["ids"]) | set(shared)
    )
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    with pytest.raises(ValueError, match="overlap"):
        _ = graph.evaluate(token(graph))
    state = graph.snapshot()
    diagnostics = cast(dict[str, Any], state["diagnostics"])
    assert diagnostics["fit"] == {
        "kind": "selection_overlap",
        "ids": shared,
        "conflicts": [{"fits": ["side", "extra"], "ids": shared}],
    }
    diagnostics["fit"]["ids"].clear()
    assert cast(dict[str, Any], graph.snapshot()["diagnostics"])["fit"]["ids"] == shared
    ids = sorted(set(nodes["extra_selection"]["ids"]) - set(shared))
    state = graph.replace(changed(graph, "extra_selection", ids=ids), token(graph))
    assert state["diagnostics"] == {}
    assert graph.evaluate(token(graph))["result"] is not None


def rotational_recipe(graph: FeatureGraph, kind: str = "plane") -> Recipe:
    # Replace only in-memory test observations with known generated geometry.
    # The source file and the user's viewer are never touched.
    from tests.test_mesh_rotational_planes import rotational_geometry

    sides, plane, _, _, group = rotational_geometry()
    if kind != "plane":
        from experiments.mesh_rotational_planes import RotationalPlanes, rotation_matrix

        # Same-type surfaces with complete angular coverage, transformed exactly.
        _, _, _, truth, _ = rotational_geometry()
        origin = np.array([truth[0], truth[1], 0.0])
        points = sides[0].points if kind == "cone" else sides[1].points
        repeated = tuple(
            origin + (points - origin) @ rotation_matrix(truth, i).T for i in range(3)
        )
        group = RotationalPlanes(repeated, tuple(np.ones(len(p)) for p in repeated))
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    graph.workspace.local = graph.workspace.local.copy()
    offset = 0
    selections: list[list[int]] = []
    for points in [sides[0].points, plane, *group.points]:
        ids = list(range(offset, offset + len(points)))
        graph.workspace.local[ids] = points
        selections.append(ids)
        offset += len(points)
    nodes["outer_band"]["ids"], nodes["top_face"]["ids"] = selections[:2]
    joint = nodes["fit"]
    payload["nodes"].remove(joint)
    for i in range(3):
        payload["nodes"].extend(
            [
                {
                    "id": f"rot_selection_{i}",
                    "label": f"Slope {i}",
                    "operation": "selection",
                    "source": "scan",
                    "ids": selections[i + 2],
                },
                {
                    "id": f"rot_plane_{i}",
                    "label": f"Slope plane {i}",
                    "operation": "fit",
                    "selections": [f"rot_selection_{i}"],
                    "kind": kind,
                    "axial_domain": [-4, 5],
                },
            ]
        )
    payload["nodes"].extend(
        [
            {
                "id": "rotation",
                "label": "Threefold",
                "operation": "rotational_symmetry",
                "axis": "side",
                "planes": [f"rot_plane_{i}" for i in range(3)],
            },
            joint,
        ]
    )
    joint["constraints"].append("rotation")
    return Recipe.model_validate(payload)


def test_rotational_graph_replay_and_input_invalidation(graph: FeatureGraph) -> None:
    _ = graph.replace(rotational_recipe(graph), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert result["fit"]["weighted_rms"] < 1e-8
    assert len(result["surfaces"]) == 5
    for i in range(3):
        assert len(result["surfaces"][f"rot_plane_{i}"]["plane_equation"]) == 4
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    ids = cast(dict[str, Any], state["memberships"])["rot_selection_0"]
    state = graph.replace(changed(graph, "rot_selection_0", ids=ids[::2]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["rotation"] == "stale"
    assert "side" in cast(dict[str, Any], state["results"])
    assert graph.evaluate(token(graph))["result"] is not None


@pytest.mark.parametrize("mutation", ["duplicate", "axis", "perpendicular", "forward"])
def test_invalid_rotational_constraints_rejected(
    graph: FeatureGraph, mutation: str
) -> None:
    payload = rotational_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    if mutation == "duplicate":
        nodes["rotation"]["planes"][1] = "rot_plane_0"
    elif mutation == "axis":
        nodes["rotation"]["axis"] = "end"
    elif mutation == "perpendicular":
        nodes["perpendicular"]["plane"] = "rot_plane_0"
        payload["nodes"].remove(nodes["perpendicular"])
        payload["nodes"].insert(-1, nodes["perpendicular"])
    else:
        payload["nodes"].remove(nodes["rotation"])
        payload["nodes"].insert(1, nodes["rotation"])
    with pytest.raises(ValueError):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_rotational_lateral_graph_preserves_types_and_replays(
    graph: FeatureGraph, kind: str
) -> None:
    recipe = rotational_recipe(graph, kind)
    _ = graph.replace(recipe, token(graph))
    before = graph.snapshot()["memberships"]
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert result["fit"]["weighted_rms"] < 1e-8
    for i in range(3):
        fitted = result["surfaces"][f"rot_plane_{i}"]
        assert fitted["kind"] == kind
        assert "plane_equation" not in fitted
        assert len(fitted["parameters"]) == 7
    assert state["memberships"] == before
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    payload = recipe.model_dump()
    next(n for n in payload["nodes"] if n["id"] == "rot_plane_1")["kind"] = "plane"
    with pytest.raises(ValueError, match="matching fit types"):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))
    assert graph.snapshot()["recipe"] == state["recipe"]
