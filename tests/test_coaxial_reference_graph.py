"""Coaxial and datum-plane authoring work together on the captured nozzle."""

import json
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.scan_workspace import ScanWorkspace
from tests.test_relationship_reference_graph import (
    assert_observations_unchanged,
    captured_recipe,
    evaluate,
    plane_reference,
)
from tests.test_relationship_reference_graph import workspace as workspace


def coaxial_parallel_recipe() -> dict[str, Any]:
    payload = captured_recipe()
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    fits = [node for node in payload["nodes"] if node["operation"] == "fit"]
    for fit in fits:
        fit["axis"] = None
    payload["nodes"].insert(
        2,
        {
            "id": "base",
            "label": "Base",
            "operation": "reference_plane",
            "axis": axis["id"],
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 0,
        },
    )
    payload["nodes"].extend(
        [
            {
                "id": "coaxial",
                "label": "Coaxial cones",
                "operation": "coaxial_group",
                "axis": axis["id"],
                "surfaces": [fit["id"] for fit in fits if fit["kind"] == "cone"],
            },
            {
                "id": "parallel",
                "label": "Parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [
                    plane_reference("base"),
                    *[
                        plane_reference(fit["id"])
                        for fit in fits
                        if fit["kind"] == "plane"
                    ],
                ],
            },
        ]
    )
    payload["output"] = "parallel"
    return payload


@pytest.mark.parametrize("hint", [(0.0, 0.0), (-0.02, -0.03), (0.2, -0.1)])
def test_captured_nozzle_uses_coaxial_and_parallel_without_rebinding_individual_fits(
    workspace: ScanWorkspace,
    hint: tuple[float, float],
) -> None:
    payload = coaxial_parallel_recipe()
    seed = next(node for node in payload["nodes"] if node["operation"] == "axis")
    seed["initial_parameters"][2:] = hint
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    authored = cast(dict[str, Any], graph.snapshot()["recipe"])
    state = evaluate(graph)
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert state["recipe"] == authored
    assert not graph.needs_evaluation(str(state["token"]), all_actions=True)
    axis_id = next(
        node["id"] for node in authored["nodes"] if node["operation"] == "axis"
    )
    axis = state["results"][axis_id]
    direction = np.asarray(axis["axis_display"])
    anchor = np.asarray(axis["point_display"])
    assert anchor @ direction == pytest.approx(0, abs=1e-8)
    radii: list[float] = []
    offsets: list[float] = []
    for node in authored["nodes"]:
        if node["operation"] != "fit":
            continue
        assert node["axis"] is None
        result = state["results"][node["id"]]
        assert result["kind"] == node["kind"]
        if node["kind"] == "cone":
            geometry = result["analytic"]
            assert abs(np.asarray(geometry["axis"]) @ direction) == pytest.approx(
                1, abs=1e-9
            )
            np.testing.assert_allclose(
                np.cross(np.asarray(geometry["origin"]) - anchor, direction),
                0,
                atol=1e-8,
            )
            radii.append(float(geometry["radius"]))
        else:
            equation = np.asarray(result["plane_equation"])
            assert abs(equation[:3] @ direction) == pytest.approx(1, abs=1e-9)
            offsets.append(float(equation[3]))
    assert abs(radii[0] - radii[1]) > 0.1
    assert abs(offsets[0] - offsets[1]) > 0.1
    base = np.asarray(state["results"]["base"]["plane_equation"])
    np.testing.assert_allclose(base[:3], direction, atol=1e-8)
    assert base[3] - base[:3] @ anchor == pytest.approx(0, abs=1e-8)
    assert_observations_unchanged(workspace, state)
    restored = FeatureGraph(workspace, Recipe.model_validate_json(json.dumps(authored)))
    reopened = evaluate(restored)
    assert reopened["recipe"] == authored
    for node in authored["nodes"]:
        if node["operation"] == "fit":
            assert reopened["results"][node["id"]]["weighted_rms"] == pytest.approx(
                state["results"][node["id"]]["weighted_rms"], abs=1e-9
            )


def test_removing_relationships_restores_authored_unbound_fit_evaluation(
    workspace: ScanWorkspace,
) -> None:
    graph = FeatureGraph(workspace, Recipe.model_validate(coaxial_parallel_recipe()))
    initial = evaluate(graph)
    payload = initial["recipe"]
    payload["nodes"] = [
        node for node in payload["nodes"] if node["id"] not in {"coaxial", "parallel"}
    ]
    payload["output"] = next(
        node["id"] for node in reversed(payload["nodes"]) if node["operation"] == "fit"
    )
    _ = graph.replace(Recipe.model_validate(payload), str(initial["token"]))
    state = evaluate(graph)
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert not {"coaxial", "parallel"}.intersection(state["derived"])
    for node in state["recipe"]["nodes"]:
        if node["operation"] == "fit":
            assert node["axis"] is None
            assert "resolved_by" not in state["results"][node["id"]]
    assert_observations_unchanged(workspace, state)
