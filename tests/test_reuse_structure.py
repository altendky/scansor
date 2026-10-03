"""Per-occurrence datum constraints survive rigid selection transfer."""

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from experiments.feature_graph import (
    AxisDefinition,
    FeatureGraph,
    PlaneDefinition,
    Recipe,
    ReuseSelection,
    SurfaceFit,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE
from experiments.surface_extents import primitive


@pytest.fixture
def reused_boss_graph(tmp_path: Path) -> FeatureGraph:
    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    # External plate-orientation relationships are not part of the reused
    # feature. Isolate its local datum contract from those separate overlays.
    payload = Recipe.model_validate_json(RECIPE.read_bytes()).model_dump()
    payload["nodes"] = [
        node for node in payload["nodes"] if node["operation"] != "plane_relationship"
    ]
    return FeatureGraph(
        NozzleWorkspace(root / "scan-coarse"), Recipe.model_validate(payload)
    )


@pytest.mark.parametrize("reversed_source", [False, True])
def test_reused_instances_keep_exact_local_constraints_and_independent_poses(
    reused_boss_graph: FeatureGraph,
    reversed_source: bool,
) -> None:
    graph = reused_boss_graph
    if reversed_source:
        payload = cast(dict[str, Any], graph.snapshot()["recipe"])
        source_axis = next(
            node
            for node in payload["nodes"]
            if node["id"] == "axis_b970d98ea84144dcbcda2d9331e79ab3"
        )
        source_axis["direction_reversed"] = True
        _ = graph.replace(
            Recipe.model_validate(payload), str(graph.snapshot()["token"])
        )
    state = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert not state["errors"]
    recipe = Recipe.model_validate(state["recipe"])
    nodes = {node.id: node for node in recipe.nodes}
    fits = [node for node in recipe.nodes if isinstance(node, SurfaceFit)]
    source_outer = next(node for node in fits if node.label == "outer fit")
    source_clock = next(node for node in fits if node.label == "clock fit")
    axes: list[NDArray[np.float64]] = []
    for target in next(
        node for node in recipe.nodes if node.operation == "feature_reuse"
    ).target_selections:
        copies = [
            node
            for node in fits
            if node.managed_key and node.managed_key.startswith(f"fit/{target}/")
        ]
        assert len(copies) == 4
        outer = next(
            node
            for node in copies
            if (node.managed_key or "").endswith("/" + source_outer.id)
        )
        clock = next(
            node
            for node in copies
            if (node.managed_key or "").endswith("/" + source_clock.id)
        )
        axis_node = nodes[cast(str, outer.axis)]
        assert isinstance(axis_node, AxisDefinition)
        assert axis_node.placement is not None
        assert axis_node.placement.target_selection == target
        assert state["results"][axis_node.id]["direction_reversed"] == reversed_source
        assert outer.axis != source_outer.axis
        assert clock.reference_plane != source_clock.reference_plane
        for fitted in copies:
            surface = primitive(state["results"][fitted.id])
            axis = primitive(state["results"][outer.id])["axis"]
            if fitted.kind == "cylinder":
                assert fitted.axis == outer.axis
                np.testing.assert_allclose(surface["axis"], axis, atol=1e-14)
                assert (
                    state["results"][fitted.id]["parameters"][:2]
                    == state["results"][outer.id]["parameters"][:2]
                )
            else:
                datum = nodes[cast(str, fitted.reference_plane)]
                assert isinstance(datum, PlaneDefinition) and datum.axis == outer.axis
                alignment = abs(float(np.dot(surface["axis"], axis)))
                if datum.construction == "parallel_to_axis":
                    assert alignment < 2e-14
                else:
                    assert alignment == pytest.approx(1.0, abs=2e-14)
        axes.append(np.asarray(state["results"][axis_node.id]["axis_display"]))
    assert len(axes) == 3
    # The deliberately tilted boss must not be forced onto an upright pose.
    assert np.linalg.norm(np.cross(axes[0], axes[1])) > 0.1
    assert (
        len(
            {node.axis for node in fits if node.managed_key and node.kind == "cylinder"}
        )
        == 3
    )
    # Across-instance dimensions do not couple their axes or clocking poses.
    radii = [
        state["results"][node.id]["parameters"][4]
        for node in fits
        if node.kind == "cylinder"
        and (
            node.id == source_outer.id
            or (node.managed_key or "").endswith("/" + source_outer.id)
        )
    ]
    assert len(radii) == 4 and max(radii) == min(radii)


def test_source_geometry_changes_invalidate_placed_datums_and_targeted_replay(
    reused_boss_graph: FeatureGraph,
) -> None:
    graph = reused_boss_graph
    initial = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    payload = Recipe.model_validate(initial["recipe"]).model_dump()
    source_selection = next(
        node for node in payload["nodes"] if node["id"] == "boss-a-outer"
    )
    source_selection["ids"] = source_selection["ids"][::2]
    stale = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), initial["token"])
    )
    for node in Recipe.model_validate(stale["recipe"]).nodes:
        if (
            isinstance(node, (AxisDefinition, PlaneDefinition))
            and node.placement is not None
        ):
            assert stale["states"][node.id] == "stale"
    replay = cast(
        dict[str, Any], graph.evaluate(stale["token"], target="fit_boss_d_clock")
    )
    assert not replay["errors"]
    first, second = (
        primitive(replay["results"][key])
        for key in ("fit_boss_d_outer", "fit_boss_d_clock")
    )
    assert abs(float(np.dot(first["axis"], second["axis"]))) < 2e-14
    for node in Recipe.model_validate(replay["recipe"]).nodes:
        if isinstance(node, ReuseSelection):
            assert node.id in initial["memberships"]


def test_placement_rejects_a_target_outside_its_reuse_action(
    reused_boss_graph: FeatureGraph,
) -> None:
    graph = reused_boss_graph
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    axis = next(
        node
        for node in payload["nodes"]
        if node["operation"] == "axis" and node.get("placement")
    )
    axis["placement"]["target_selection"] = "boss-a-outer"
    with pytest.raises(ValueError, match="declared reuse target"):
        _ = graph.replace(
            Recipe.model_validate(payload), str(graph.snapshot()["token"])
        )


def test_placed_planes_follow_a_locally_refitted_fixed_axis(
    reused_boss_graph: FeatureGraph,
) -> None:
    graph = reused_boss_graph
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    outer = next(node for node in payload["nodes"] if node["id"] == "fit_boss_d_outer")
    axis = next(node for node in payload["nodes"] if node["id"] == outer["axis"])
    # A datum initialized by a copied standalone fit must use that fit's actual
    # target pose, not the approximate rigid matching pose.
    seed = {
        **outer,
        "id": "target_axis_seed",
        "label": "Target axis seed",
        "axis": None,
        "managed_by": None,
        "managed_key": None,
    }
    payload["nodes"].insert(payload["nodes"].index(axis), seed)
    axis.update(source_fit=seed["id"], initial_parameters=None, placement=None)
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    state = cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), target="fit_boss_d_clock"),
    )
    assert not state["errors"]
    normal = primitive(state["results"]["fit_boss_d_clock"])["axis"]
    direction = primitive(state["results"]["target_axis_seed"])["axis"]
    assert abs(float(np.dot(normal, direction))) < 2e-14
    np.testing.assert_allclose(
        state["results"][axis["id"]]["parameters"][:4],
        state["results"][seed["id"]]["parameters"][:4],
        atol=1e-14,
    )
