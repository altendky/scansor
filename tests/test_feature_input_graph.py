"""Retained graph replay and persistence of exact selectable outputs."""

import json
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    compile_execution_plan,
    describe_geometry_influence,
)
from experiments.feature_inputs import OutputReference
from experiments.nozzle_session import NozzleWorkspace


def ref(feature: str, output: str, context: str | None = None) -> dict[str, Any]:
    return {"feature": feature, "output": output, "context": context or feature}


@pytest.fixture
def graph() -> FeatureGraph:
    path = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(path)
    payload = Recipe.model_validate_json(
        (path / "recipes/cone-plane.json").read_text()
    ).model_dump()
    source = payload["nodes"][0]
    workspace.local[0] = [1.0, 2.0, 3.0]
    payload["nodes"] = [
        source,
        {
            "id": "picked",
            "label": "Picked",
            "operation": "selection",
            "source": source["id"],
            "ids": [0],
        },
        {
            "id": "other",
            "label": "Other",
            "operation": "point",
            "initial_coordinates": [5, 2, 3],
        },
        {
            "id": "up",
            "label": "Up",
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        },
        {
            "id": "forward",
            "label": "Forward",
            "operation": "axis",
            "source_points": [ref("picked", "single_node"), ref("other", "point")],
        },
        {
            "id": "plane",
            "label": "Datum plane",
            "operation": "reference_plane",
            "axis": "up",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
        },
        {
            "id": "frame",
            "label": "Frame",
            "operation": "frame",
            "origin_point": ref("picked", "single_node"),
            "primary_reference": ref("plane", "plane"),
            "primary_output_axis": "+Z",
            "secondary_reference": ref("forward", "axis"),
            "secondary_output_axis": "+X",
        },
        {
            "id": "scale",
            "label": "Scale",
            "operation": "scale",
            "distances": [
                {
                    "first_point": ref("picked", "single_node"),
                    "second_point": ref("other", "point"),
                    "known_distance": 8,
                }
            ],
        },
        {
            "id": "transform",
            "label": "Transform",
            "operation": "transform",
            "frame": "frame",
            "scale": "scale",
        },
    ]
    payload["output"] = "transform"
    return FeatureGraph(workspace, Recipe.model_validate(payload))


def test_structured_point_plane_readers_replay_and_serialize(
    graph: FeatureGraph,
) -> None:
    before = graph.snapshot()
    assert set(cast(dict[str, str], before["states"]).values()) == {"unevaluated"}
    state = graph.evaluate(str(before["token"]))
    results = cast(dict[str, Any], state["results"])
    assert results["scale"]["scale"] == pytest.approx(2)
    np.testing.assert_allclose(results["frame"]["origin_display"], [1, 2, 3])
    np.testing.assert_allclose(results["forward"]["axis_display"], [1, 0, 0])
    assert results["frame"]["primary_reference"] == ref("plane", "plane")
    assert results["forward"]["source_points"][0] == ref("picked", "single_node")
    assert json.loads(json.dumps(state, allow_nan=False))["results"]["scale"][
        "observations"
    ][0]["first_point"] == ref("picked", "single_node")
    saved = Recipe.model_validate_json(json.dumps(state["recipe"]))
    loaded = FeatureGraph(graph.workspace, saved)
    again = loaded.evaluate(str(loaded.snapshot()["token"]))
    assert again["results"] == state["results"]


def test_compatible_repairs_retain_output_identity_and_match_fresh_replay(
    graph: FeatureGraph,
) -> None:
    _ = graph.evaluate(str(graph.snapshot()["token"]))
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"][2]["initial_coordinates"] = [9, 2, 3]
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    warm = graph.evaluate(str(graph.snapshot()["token"]))
    cold = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    fresh = cold.evaluate(str(cold.snapshot()["token"]))
    assert warm["results"] == fresh["results"]
    assert list(cast(dict[str, Any], warm["recipe"])["nodes"][4]["source_points"]) == [
        ref("picked", "single_node"),
        ref("other", "point"),
    ]


@pytest.mark.parametrize("change", ["removed", "cardinality", "wrong_output", "cycle"])
def test_backend_rejects_invalid_committed_outputs(
    graph: FeatureGraph, change: str
) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    if change == "removed":
        payload["nodes"][4]["source_points"][0]["feature"] = "missing"
    elif change == "cardinality":
        payload["nodes"][1]["ids"] = [0, 1]
    elif change == "wrong_output":
        payload["nodes"][4]["source_points"][0]["output"] = "centroid"
    else:
        payload["nodes"][4]["source_points"] = [
            ref("forward", "axis"),
            ref("other", "point"),
        ]
    with pytest.raises(ValueError):
        _ = graph.replace(
            Recipe.model_validate(payload), str(graph.snapshot()["token"])
        )


def test_discovery_does_not_publish_or_evaluate_and_snapshot_is_independent(
    graph: FeatureGraph,
) -> None:
    first = graph.snapshot()
    catalogue = cast(list[dict[str, Any]], first["input_catalogue"])
    catalogue[0]["reference"]["feature"] = "mutated"
    second = graph.snapshot()
    assert first["revision"] == second["revision"] == 0
    assert second["results"] == {}
    assert (
        cast(list[dict[str, Any]], second["input_catalogue"])[0]["reference"]["feature"]
        != "mutated"
    )
    assert (
        OutputReference.model_validate(ref("picked", "single_node")).context == "picked"
    )


def test_evaluated_catalogue_previews_do_not_alias_publication_stores(
    graph: FeatureGraph,
) -> None:
    first = graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    catalogue = cast(list[dict[str, Any]], first["input_catalogue"])
    point = next(
        item for item in catalogue if item["reference"] == ref("other", "point")
    )
    point["preview"]["point_display"][0] = 999
    cast(dict[str, Any], first["results"])["other"]["point_display"][1] = 999
    second = graph.snapshot()
    fresh = next(
        item
        for item in cast(list[dict[str, Any]], second["input_catalogue"])
        if item["reference"] == ref("other", "point")
    )
    assert fresh["preview"]["point_display"] == [5, 2, 3]
    assert cast(dict[str, Any], second["results"])["other"]["point_display"] == [
        5,
        2,
        3,
    ]


def test_sphere_center_is_an_explicit_point_for_all_readers(
    graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    ids = [int(i) for i in graph.workspace.default.lateral_ids if i != 0][:120]
    height = np.linspace(-0.95, 0.95, len(ids))
    angle = np.arange(len(ids)) * np.pi * (3 - np.sqrt(5))
    radial = np.sqrt(1 - height**2)
    center = np.array([1.0, 2.0, 3.0])
    graph.workspace.local[ids] = center + 2 * np.column_stack(
        (radial * np.cos(angle), radial * np.sin(angle), height)
    )
    payload["nodes"][1:1] = [
        {
            "id": "sphere_samples",
            "label": "Sphere samples",
            "operation": "selection",
            "source": payload["nodes"][0]["id"],
            "ids": sorted(ids),
        },
        {
            "id": "sphere",
            "label": "Sphere",
            "operation": "fit",
            "kind": "sphere",
            "selections": ["sphere_samples"],
        },
    ]
    for node in payload["nodes"]:
        if node["operation"] == "axis" and node.get("source_points"):
            node["source_points"] = [ref("sphere", "center"), ref("other", "point")]
        elif node["operation"] == "frame":
            node["origin_point"] = ref("sphere", "center")
        elif node["operation"] == "scale":
            node["distances"][0]["first_point"] = ref("sphere", "center")
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    results = cast(dict[str, Any], state["results"])
    np.testing.assert_allclose(results["frame"]["origin_display"], center, atol=1e-8)
    assert results["scale"]["scale"] == pytest.approx(2)
    saved_frame = next(
        node
        for node in json.loads(json.dumps(state))["recipe"]["nodes"]
        if node["id"] == "frame"
    )
    assert saved_frame["origin_point"] == ref("sphere", "center")


def test_exact_named_solve_reader_excludes_an_unrelated_failing_publisher() -> None:
    path = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(path)
    payload = Recipe.model_validate_json(
        (path / "recipes/cone-plane.json").read_text()
    ).model_dump()
    payload["nodes"] += [
        {
            "id": "bad_selection",
            "label": "Bad selection",
            "operation": "selection",
            "source": "scan",
            "ids": [],
        },
        {
            "id": "bad_plane",
            "label": "Bad plane",
            "operation": "fit",
            "kind": "plane",
            "selections": ["bad_selection"],
        },
        {
            "id": "bad_relation",
            "label": "Bad relation",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["end", "bad_plane"],
        },
        {
            "id": "origin",
            "label": "Origin",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
        {
            "id": "direction",
            "label": "Direction",
            "operation": "axis",
            "initial_parameters": [0, 0, 1, 0],
        },
        {
            "id": "consumer",
            "label": "Consumer",
            "operation": "frame",
            "origin_point": ref("origin", "point"),
            "primary_reference": ref("end", "plane", "fit"),
            "primary_output_axis": "+Z",
            "secondary_reference": ref("direction", "axis"),
            "secondary_output_axis": "+X",
        },
    ]
    payload["output"] = "consumer"
    recipe = Recipe.model_validate(payload)
    graph = FeatureGraph(workspace, recipe)
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe, {"consumer"}, describe_geometry_influence(nodes)
    )
    assert {"bad_plane", "bad_relation", "bad_selection"}.isdisjoint(plan.order)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert cast(dict[str, str], state["states"])["consumer"] == "ready"
    assert cast(dict[str, str], state["states"])["bad_plane"] == "unevaluated"


def test_independent_legacy_root_retains_its_implicit_publication() -> None:
    path = Path("examples/nozzle-bayonette-simplified")
    payload = Recipe.model_validate_json(
        (path / "recipes/cone-plane.json").read_text()
    ).model_dump()
    payload["nodes"] += [
        {
            "id": "other_plane",
            "label": "Other plane",
            "operation": "fit",
            "kind": "plane",
            "selections": ["top_face"],
        },
        {
            "id": "relation",
            "label": "Relation",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["end", "other_plane"],
        },
        {
            "id": "growth",
            "label": "Growth",
            "operation": "growth",
            "seed_fit": "end",
            "distance": 0.1,
            "angle_degrees": 90,
        },
        {
            "id": "sphere",
            "label": "Sphere",
            "operation": "fit",
            "kind": "sphere",
            "selections": ["growth"],
        },
        {
            "id": "other_point",
            "label": "Other point",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "source_points": [ref("sphere", "center"), ref("other_point", "point")],
        },
    ]
    recipe = Recipe.model_validate(payload)
    nodes = {node.id: node for node in recipe.nodes}
    influence = describe_geometry_influence(nodes)
    legacy = compile_execution_plan(recipe, {"growth"}, influence)
    exact = compile_execution_plan(recipe, {"axis"}, influence)
    together = compile_execution_plan(recipe, {"growth", "axis"}, influence)
    assert "relation" in legacy.order
    assert "relation" not in exact.order
    assert set(legacy.order) <= set(together.order)


def test_stored_connected_point_context_waits_for_late_sphere(
    graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    ids = [int(i) for i in graph.workspace.default.lateral_ids if i != 0][:120]
    height = np.linspace(-0.95, 0.95, len(ids))
    angle = np.arange(len(ids)) * np.pi * (3 - np.sqrt(5))
    radial = np.sqrt(1 - height**2)
    center = np.array([9.0, 2.0, 3.0])
    graph.workspace.local[ids] = center + 2 * np.column_stack(
        (radial * np.cos(angle), radial * np.sin(angle), height)
    )
    payload["nodes"].insert(
        2,
        {
            "id": "samples",
            "label": "Samples",
            "operation": "selection",
            "source": payload["nodes"][0]["id"],
            "ids": sorted(ids),
        },
    )
    payload["nodes"].append(
        {
            "id": "late_sphere",
            "label": "Late sphere",
            "operation": "fit",
            "kind": "sphere",
            "selections": ["samples"],
            "point": "other",
        }
    )
    for node in payload["nodes"]:
        if node["operation"] == "axis" and node.get("source_points"):
            node["source_points"][1]["context"] = "@point/other"
        elif node["operation"] == "scale":
            node["distances"][0]["second_point"]["context"] = "@point/other"
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    results = cast(dict[str, Any], state["results"])
    np.testing.assert_allclose(results["other"]["point_display"], center, atol=1e-8)
    assert results["scale"]["scale"] == pytest.approx(1)
    descriptor = next(
        item
        for item in cast(list[dict[str, Any]], state["input_catalogue"])
        if item["reference"] == ref("other", "point", "@point/other")
    )
    assert descriptor["availability"] == "ready"
    assert "late_sphere" in descriptor["dependencies"]
    assert (
        json.loads(json.dumps(state))["results"]["forward"]["source_points"][1][
            "context"
        ]
        == "@point/other"
    )


def test_plane_publication_identity_survives_relationship_reordering(
    graph: FeatureGraph,
) -> None:
    source = cast(dict[str, Any], graph.snapshot()["recipe"])["nodes"][0]
    nodes: list[dict[str, Any]] = [source]
    ids = graph.workspace.default.plane_ids
    for index, name in enumerate(("a", "b", "c")):
        selected = ids[index::3]
        angles = np.linspace(0, 2 * np.pi, len(selected), endpoint=False)
        graph.workspace.local[selected] = np.column_stack(
            (np.cos(angles), np.sin(angles), np.full(len(selected), index + 1))
        )
        nodes += [
            {
                "id": f"{name}_selection",
                "label": f"{name} samples",
                "operation": "selection",
                "source": source["id"],
                "ids": sorted(selected),
            },
            {
                "id": name,
                "label": name,
                "operation": "fit",
                "kind": "plane",
                "selections": [f"{name}_selection"],
            },
        ]
    nodes += [
        {
            "id": "ab",
            "label": "AB",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["a", "b"],
        },
        {
            "id": "bc",
            "label": "BC",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["b", "c"],
        },
        {
            "id": "origin",
            "label": "Origin",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
        {
            "id": "direction",
            "label": "Direction",
            "operation": "axis",
            "initial_parameters": [0, 0, 1, 0],
        },
        {
            "id": "frame",
            "label": "Frame",
            "operation": "frame",
            "origin_point": ref("origin", "point"),
            "primary_reference": ref("a", "plane", "ab"),
            "primary_output_axis": "+Z",
            "secondary_reference": ref("direction", "axis"),
            "secondary_output_axis": "+X",
        },
    ]
    payload = {"nodes": nodes, "output": "frame"}
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    before = graph.evaluate(str(graph.snapshot()["token"]))
    nodes[7], nodes[8] = nodes[8], nodes[7]
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    warm = graph.evaluate(str(graph.snapshot()["token"]))
    fresh_graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    fresh = fresh_graph.evaluate(str(fresh_graph.snapshot()["token"]))
    assert cast(dict[str, Any], warm["results"])["frame"]["primary_reference"] == ref(
        "a", "plane", "ab"
    )
    np.testing.assert_allclose(
        cast(dict[str, Any], warm["results"])["frame"]["rotation"],
        cast(dict[str, Any], fresh["results"])["frame"]["rotation"],
        atol=1e-9,
    )
    assert cast(dict[str, Any], before["results"])["frame"]["primary_reference"] == ref(
        "a", "plane", "ab"
    )
