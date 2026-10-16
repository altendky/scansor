"""Noisy shared cones must agree geometrically before recovery publishes them."""

import importlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import DEFAULT_POLICY
from experiments.mesh_cylinder_fit import CylinderFitFailure
from experiments.nozzle_browser import NATIVE_SCAN_LIMITS
from experiments.scan_workspace import ScanWorkspace, prepare_scan
from experiments.selection_growth import surface_distance

FIXTURE = Path(__file__).with_name("fixtures") / "shared-axis-nozzle.json"
EXAMPLE = Path("examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply")


@pytest.fixture(scope="module")
def workspace() -> ScanWorkspace:
    source = json.loads(FIXTURE.read_text())["nodes"][0]
    with EXAMPLE.open("rb") as stream:
        result = prepare_scan(
            stream, source["descriptor"]["filename"], NATIVE_SCAN_LIMITS
        )
    assert result.descriptor is not None
    assert result.descriptor.model_dump() == source["descriptor"]
    assert result.reference_sha256 == source["reference_sha256"]
    return result


def graph_for_hint(
    workspace: ScanWorkspace, direction: tuple[float, float]
) -> FeatureGraph[ScanWorkspace]:
    payload = json.loads(FIXTURE.read_text())
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    axis["initial_parameters"][2:] = direction
    return FeatureGraph(workspace, Recipe.model_validate(payload))


@pytest.mark.parametrize("direction", [(0.0, 0.0), (-0.02, -0.03), (0.2, -0.1)])
def test_noisy_shared_cones_resolve_without_weakening_geometry_or_budgets(
    workspace: ScanWorkspace, direction: tuple[float, float]
) -> None:
    graph = graph_for_hint(workspace, direction)
    before = graph.snapshot()
    state = cast(dict[str, Any], graph.evaluate(str(before["token"]), all_actions=True))

    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert state["recipe"] == before["recipe"]
    assert not graph.needs_evaluation(str(state["token"]), all_actions=True)
    nodes = {node["id"]: node for node in state["recipe"]["nodes"]}
    axis = next(node for node in nodes.values() if node["operation"] == "axis")
    resolved_axis = state["results"][axis["id"]]
    assert resolved_axis["resolved_by"] == "connected_fits"
    direction_vector = np.asarray(resolved_axis["axis_display"])
    anchor = np.asarray(resolved_axis["point_display"])
    all_ids: list[int] = []
    squared = total_weight = 0.0
    recovery = None
    for node in nodes.values():
        if node["operation"] != "fit":
            continue
        assert node["axis"] == axis["id"]
        ids = sorted(
            {
                vertex
                for selection in node["selections"]
                for vertex in nodes[selection]["ids"]
            }
        )
        result = state["results"][node["id"]]
        assert result["ids"] == ids
        residuals = surface_distance(workspace.local[ids], result)[0]
        np.testing.assert_allclose(
            result["residuals"], residuals, atol=1e-12, rtol=1e-10
        )
        weights = workspace.data.weights[ids]
        assert result["weighted_rms"] == pytest.approx(
            float(np.sqrt(weights @ residuals**2 / weights.sum()))
        )
        squared += float(weights @ residuals**2)
        total_weight += float(weights.sum())
        all_ids.extend(ids)
        if node["kind"] == "cone":
            geometry = result["analytic"]
            assert abs(
                float(np.asarray(geometry["axis"]) @ direction_vector)
            ) == pytest.approx(1, abs=1e-10)
            np.testing.assert_allclose(
                np.cross(np.asarray(geometry["origin"]) - anchor, direction_vector),
                0,
                atol=1e-8,
            )
        else:
            assert abs(
                float(np.asarray(result["plane_equation"][:3]) @ direction_vector)
            ) == pytest.approx(1, abs=1e-10)
        current = result["initialization"]["recovery"]
        if recovery is None:
            recovery = current
        else:
            assert current == recovery
    frame = CoordinateFrame.from_observations(
        workspace.local[all_ids], workspace.data.weights[all_ids]
    )
    # Preserve the complete area-weighted objective of the captured noisy scan.
    assert squared / total_weight / frame.length**2 == pytest.approx(
        2.7230009004e-6, rel=1e-6
    )
    assert recovery is not None
    assert recovery["termination"] == "best-attempted-local-solution"
    assert recovery["selected_role"] == "fresh"
    assert recovery["policy"] == asdict(DEFAULT_POLICY)
    attempts = recovery["attempts"]
    assert len(attempts) <= DEFAULT_POLICY.max_proposals + 1
    assert sum(attempt["role"] == "hint" for attempt in attempts) == 1
    assert all(
        attempt["evaluations"] <= DEFAULT_POLICY.attempt.max_evaluations
        for attempt in attempts
    )


def test_captured_shared_cones_reject_the_previous_imprecise_solutions(
    workspace: ScanWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    component = importlib.import_module("experiments.fit_relationship_component")
    original = component.solve_constrained_least_squares

    def previous_precision(*args: Any, **kwargs: Any) -> Any:
        kwargs["gradient_tolerance"] = 1e-9
        return original(*args, **kwargs)

    monkeypatch.setattr(
        component, "solve_constrained_least_squares", previous_precision
    )
    graph = graph_for_hint(workspace, (0.0, 0.0))
    with pytest.raises(CylinderFitFailure, match="ambiguous") as caught:
        _ = graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    assert caught.value.code == "ambiguous"
    assert caught.value.diagnostic["recovery"]["selected"] is None
