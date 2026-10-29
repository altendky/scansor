"""Declared stale publications can be authored before their geometry is rebuilt."""

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.relationship_inputs import relationship_reference
from experiments.surface_primitives import revolution_geometry
from tests.test_direction_graph import fixture as fixture
from tests.test_direction_graph import graph_with, reference


def pending_graph(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], context: str
) -> tuple[FeatureGraph[NozzleWorkspace], dict[str, str], str, str]:
    workspace, original = fixture
    nodes = deepcopy(original)
    if context == "named":
        graph = graph_with(
            (workspace, nodes),
            {
                "id": "parallel",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [reference("cap", "plane"), reference("other", "plane")],
            },
        )
        return graph, reference("cap", "plane", "parallel"), "left", "parallel"
    nodes.insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [-5.0, 0.0, 0.0, 0.0],
        },
    )
    for name in ("left", "cap"):
        next(node for node in nodes if node["id"] == name)["axis"] = "axis"
    graph = graph_with(
        (workspace, nodes),
        {
            "id": "datum",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 2.0,
        },
    )
    return graph, reference("datum", "plane", "@axis/axis"), "right", "datum"


def snapshot(graph: FeatureGraph[NozzleWorkspace]) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def publication(state: dict[str, Any], exact: dict[str, str]) -> dict[str, Any]:
    outputs = state["input_requirements"]["relationship_direction"]
    return next(
        output
        for output in [*outputs["choices"], *outputs["unavailable"]]
        if output["reference"] == exact
    )


def invalidate_upstream(graph: FeatureGraph[NozzleWorkspace], context: str) -> None:
    recipe = graph.export_recipe()
    if context == "named":
        provider = next(node for node in recipe["nodes"] if node["id"] == "parallel")
        provider["surfaces"].reverse()
    else:
        axis = next(node for node in recipe["nodes"] if node["id"] == "axis")
        axis["initial_parameters"] = list(axis["initial_parameters"])
        axis["initial_parameters"][2] += 0.01
    _ = graph.replace(Recipe.model_validate(recipe), str(snapshot(graph)["token"]))


def ensure_alignment(
    graph: FeatureGraph[NozzleWorkspace], exact: dict[str, str], member: str
) -> dict[str, Any]:
    state = cast(
        dict[str, Any],
        graph.ensure_current(str(snapshot(graph)["token"]), targets=["alignment"]),
    )
    assert state["states"]["alignment"] == "ready", state["errors"]
    assert not state["errors"]
    fixed = publication(state, exact)
    assert fixed["availability"] == "ready"
    assert fixed["parameter_owner"] is None
    axis = revolution_geometry(state["derived"]["alignment"]["surfaces"][member])[
        "axis"
    ]
    normal = fixed["preview"]["plane_equation"][:3]
    assert np.linalg.norm(np.cross(axis, normal)) < 1e-8
    assert graph.export_recipe()["nodes"][-1]["directions"][1] == exact
    return state


@pytest.mark.parametrize("context", ["named", "automatic"])
@pytest.mark.parametrize("pending_state", ["unevaluated", "stale"])
def test_relationship_can_be_authored_before_its_exact_publication_is_ready(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    context: str,
    pending_state: str,
) -> None:
    graph, exact, member, provider = pending_graph(fixture, context)
    if pending_state == "stale":
        ready = cast(
            dict[str, Any],
            graph.ensure_current(str(snapshot(graph)["token"]), targets=[provider]),
        )
        assert ready["states"][provider] == "ready", ready["errors"]
        invalidate_upstream(graph, context)
    before = snapshot(graph)
    pending = publication(before, exact)
    assert pending["compatible"] is True
    assert pending["availability"] == pending_state
    assert pending["preview"] is None
    assert (
        relationship_reference(
            exact,
            Recipe.model_validate(graph.export_recipe()),
            "relationship_direction",
        ).is_binding
        is False
    )

    recipe = graph.export_recipe()
    source_and_observations = deepcopy(recipe["nodes"])
    recipe["nodes"].append(
        {
            "id": "alignment",
            "label": "Deferred direction alignment",
            "operation": "direction_alignment",
            "directions": [reference(member), exact],
        }
    )
    recipe["output"] = "alignment"
    _ = graph.replace(Recipe.model_validate(recipe), str(before["token"]))
    authored = graph.export_recipe()
    unevaluated = snapshot(graph)
    assert unevaluated["states"]["alignment"] == "unevaluated"
    assert "alignment" not in unevaluated["derived"]
    assert "alignment" not in unevaluated["results"]
    assert publication(unevaluated, exact)["availability"] == pending_state
    assert authored["nodes"][:-1] == source_and_observations

    state = ensure_alignment(graph, exact, member)
    assert state["states"][provider] == "ready"
    unrelated = "right" if context == "named" else "other"
    assert state["states"][unrelated] == "unevaluated"
    assert graph.export_recipe() == authored


@pytest.mark.parametrize("context", ["named", "automatic"])
def test_upstream_changes_stale_the_relationship_without_rebinding_its_input(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], context: str
) -> None:
    graph, exact, member, _ = pending_graph(fixture, context)
    recipe = graph.export_recipe()
    recipe["nodes"].append(
        {
            "id": "alignment",
            "label": "Deferred direction alignment",
            "operation": "direction_alignment",
            "directions": [reference(member), exact],
        }
    )
    recipe["output"] = "alignment"
    _ = graph.replace(Recipe.model_validate(recipe), str(snapshot(graph)["token"]))
    _ = ensure_alignment(graph, exact, member)
    unchanged = {
        node["id"]: deepcopy(node)
        for node in graph.export_recipe()["nodes"]
        if node["operation"] in {"source", "selection"}
    }
    invalidate_upstream(graph, context)
    stale = snapshot(graph)
    assert stale["states"]["alignment"] == "stale"
    assert publication(stale, exact)["availability"] == "stale"
    assert "alignment" not in stale["derived"]
    assert graph.export_recipe()["nodes"][-1]["directions"][1] == exact
    authored = graph.export_recipe()

    _ = ensure_alignment(graph, exact, member)
    assert graph.export_recipe() == authored
    assert {
        node["id"]: node
        for node in authored["nodes"]
        if node["operation"] in {"source", "selection"}
    } == unchanged
