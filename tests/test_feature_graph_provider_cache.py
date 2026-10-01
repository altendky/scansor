"""Resolved consumers follow both insertion and removal of an axis provider."""

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace

EXAMPLE = Path("examples/nozzle-bayonette-simplified")


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(EXAMPLE)


def frame_graph(workspace: NozzleWorkspace, reference: str) -> FeatureGraph:
    recipe = Recipe.model_validate_json(
        (EXAMPLE / "recipes/cone-plane.json").read_text()
    ).model_dump()
    original = {node["id"]: node for node in recipe["nodes"]}
    recipe["nodes"] = [original[key] for key in ("scan", "outer_band", "top_face")]
    recipe["nodes"].extend(
        [
            {
                "id": "axis",
                "label": "Free axis",
                "operation": "axis",
                "initial_parameters": workspace.data.selection["initial_parameters"][
                    :4
                ],
            },
            {
                "id": "side_factor",
                "label": "Cylinder factor",
                "operation": "fit",
                "selections": ["outer_band"],
                "kind": "cylinder",
                "axial_domain": [-2, 5],
                "axis": "axis",
            },
            {
                "id": "plane_factor",
                "label": "Plane factor",
                "operation": "fit",
                "selections": ["top_face"],
                "kind": "plane",
                "axis": "axis",
            },
            {
                "id": "explicit_solve",
                "label": "Named explicit solve",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["side_factor", "plane_factor"],
            },
            {
                "id": "datum",
                "label": "Unfitted axial datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 2,
            },
            {
                "id": "origin",
                "label": "Frame origin",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
            {
                "id": "secondary",
                "label": "Secondary axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 1, 0],
            },
            {
                "id": "frame",
                "label": "Resolved frame consumer",
                "operation": "frame",
                "origin_point": "origin",
                "primary_reference": reference,
                "primary_output_axis": "+Z",
                "secondary_reference": "secondary",
                "secondary_output_axis": "+X",
            },
        ]
    )
    recipe["output"] = "explicit_solve"
    return FeatureGraph(workspace, Recipe.model_validate(recipe))


def evaluate(graph: FeatureGraph, target: str) -> dict[str, Any]:
    return cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), target))


@pytest.mark.parametrize("reference", ["axis", "datum"])
def test_explicit_solve_removal_invalidates_cached_resolved_frame(
    workspace: NozzleWorkspace, reference: str
) -> None:
    graph = frame_graph(workspace, reference)
    before = evaluate(graph, "frame")
    before_axis = np.asarray(before["results"]["axis"]["axis_display"])
    np.testing.assert_allclose(
        before["results"]["frame"]["z_axis_display"], before_axis
    )
    assert before["results"]["axis"]["resolved_by"] == "connected_fits"

    explicit = evaluate(graph, "explicit_solve")
    assert explicit["token"] == before["token"]
    assert "resolved_by" not in explicit["results"]["axis"]
    assert explicit["states"]["frame"] == "stale"
    assert "frame" not in explicit["results"]
    assert (
        np.linalg.norm(
            before_axis - np.asarray(explicit["results"]["axis"]["axis_display"])
        )
        > 1e-3
    )

    replay = evaluate(graph, "frame")
    assert replay["states"]["frame"] == "ready"
    assert replay["results"]["frame"] == before["results"]["frame"]
    np.testing.assert_allclose(
        replay["results"]["frame"]["z_axis_display"],
        replay["results"]["axis"]["axis_display"],
    )


@pytest.mark.parametrize("reference", ["axis", "datum"])
def test_automatic_provider_insertion_recomputes_cached_resolved_frame(
    workspace: NozzleWorkspace, reference: str
) -> None:
    graph = frame_graph(workspace, reference)
    before = cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), all_actions=True),
    )
    assert before["states"]["frame"] == "ready"
    assert "resolved_by" not in before["results"]["axis"]
    np.testing.assert_allclose(
        before["results"]["frame"]["z_axis_display"],
        before["results"]["axis"]["axis_display"],
    )

    after = evaluate(graph, "frame")
    assert after["token"] == before["token"]
    assert after["states"]["frame"] == "ready"
    assert after["results"]["axis"]["resolved_by"] == "connected_fits"
    assert after["results"]["frame"] != before["results"]["frame"]
    np.testing.assert_allclose(
        after["results"]["frame"]["z_axis_display"],
        after["results"]["axis"]["axis_display"],
    )
    fresh = evaluate(frame_graph(workspace, reference), "frame")
    assert after["results"]["frame"] == fresh["results"]["frame"]
    assert evaluate(graph, "frame")["results"]["frame"] == after["results"]["frame"]
