"""Ordinary shared-center relationships consume current observations implicitly."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    describe_geometry_influence,
    discover_reuse_lineage,
)
from experiments.feature_inputs import OutputReference, resolve_output
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.nozzle_session import NozzleWorkspace


def evaluated(graph: FeatureGraph) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        graph.ensure_current(str(graph.snapshot()["token"]), all_actions=True),
    )


def sphere_points(center: np.ndarray, radius: float, count: int = 96) -> np.ndarray:
    z = np.linspace(-0.95, 0.95, count)
    angle = np.arange(count) * np.pi * (3 - np.sqrt(5))
    radial = np.sqrt(1 - z**2)
    return center + radius * np.column_stack(
        (radial * np.cos(angle), radial * np.sin(angle), z)
    )


def center_graph(*, fixed: bool = False) -> FeatureGraph:
    workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
    center = np.array([2.5, -1.75, 3.25])
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
        }
    ]
    for index, (name, delta, radius) in enumerate(
        (
            ("seed", [0, 0, 0], 1),
            ("left", [0.08, -0.03, 0.04], 2),
            ("right", [-0.06, 0.05, -0.02], 3),
        )
    ):
        ids = list(range(index * 96, (index + 1) * 96))
        workspace.local[ids] = sphere_points(center + delta, radius)
        workspace.data.weights[ids] = 1
        nodes.append(
            {
                "id": name + "-selection",
                "label": name + " observations",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            }
        )
    if fixed:
        nodes.append(
            {
                "id": "seed",
                "label": "Seed sphere",
                "operation": "fit",
                "kind": "sphere",
                "selections": ["seed-selection"],
            }
        )
    nodes.append(
        {
            "id": "center",
            "label": "Center",
            "operation": "point",
            **({"source_fit": "seed"} if fixed else {"initial_coordinates": [0, 0, 0]}),
        }
    )
    nodes.extend(
        {
            "id": name,
            "label": name,
            "operation": "fit",
            "kind": "sphere",
            "selections": [name + "-selection"],
        }
        for name in ("left", "right")
    )
    nodes.append(
        {
            "id": "shared",
            "label": "Shared center",
            "operation": "shared_center_group",
            "point": "center",
            "surfaces": ["left", "right"],
        }
    )
    return FeatureGraph(
        workspace, Recipe.model_validate({"nodes": nodes, "output": "shared"})
    )


@pytest.mark.parametrize("fixed", [False, True])
def test_shared_center_is_authorable_before_values_and_refits_without_joint(
    fixed: bool,
) -> None:
    graph = center_graph(fixed=fixed)
    before = cast(dict[str, Any], graph.snapshot())
    assert before["states"]["shared"] == "unevaluated"
    original = deepcopy(before["recipe"])
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert graph.snapshot()["recipe"] == original
    left, right = state["results"]["left"], state["results"]["right"]
    np.testing.assert_allclose(
        left["parameters"][:3], right["parameters"][:3], atol=1e-10
    )
    np.testing.assert_allclose(
        left["parameters"][:3], state["results"]["center"]["point_display"], atol=1e-10
    )
    assert abs(left["parameters"][3] - right["parameters"][3]) > 0.8
    if fixed:
        np.testing.assert_allclose(
            left["parameters"][:3], [2.5, -1.75, 3.25], atol=1e-8
        )
    else:
        assert np.linalg.norm(left["parameters"][:3]) > 2
    nodes = {node.id: node for node in Recipe.model_validate(original).nodes}
    published = resolve_output(
        OutputReference(feature="left", output="center", context="shared"),
        nodes,
        state["derived"],
    )
    np.testing.assert_allclose(
        published["point_display"], left["parameters"][:3], atol=1e-10
    )
    reopened = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    restored = evaluated(reopened)
    np.testing.assert_allclose(
        restored["results"]["left"]["parameters"], left["parameters"], atol=1e-8
    )


def test_shared_center_selection_edit_invalidates_entire_implicit_component() -> None:
    graph = center_graph()
    before = evaluated(graph)
    recipe = deepcopy(before["recipe"])
    selection = next(node for node in recipe["nodes"] if node["id"] == "left-selection")
    selection["ids"] = selection["ids"][:64]
    pending = cast(
        dict[str, Any],
        graph.replace(Recipe.model_validate(recipe), str(before["token"])),
    )
    assert all(
        pending["states"][key] != "ready"
        for key in ("center", "left", "right", "shared")
    )
    state = evaluated(graph)
    assert not state["errors"]
    np.testing.assert_allclose(
        state["results"]["left"]["parameters"][:3],
        state["results"]["right"]["parameters"][:3],
        atol=1e-10,
    )


def test_shared_center_rejects_conflicting_bindings_and_non_spheres() -> None:
    graph = center_graph()
    payload = graph.export_recipe()
    next(node for node in payload["nodes"] if node["id"] == "left")["kind"] = "cylinder"
    with pytest.raises(ValueError, match="sphere"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    payload = graph.export_recipe()
    payload["nodes"].insert(
        1,
        {
            "id": "other",
            "label": "Other",
            "operation": "point",
            "initial_coordinates": [1, 2, 3],
        },
    )
    next(node for node in payload["nodes"] if node["id"] == "left")["point"] = "other"
    with pytest.raises(ValueError, match=r"conflicting|another point"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(payload))


def test_shared_center_and_equal_radii_are_one_stationary_observation_fit() -> None:
    graph = center_graph()
    payload = graph.export_recipe()
    payload["nodes"].append(
        {
            "id": "radii",
            "label": "Equal radii",
            "operation": "equal_radii",
            "surfaces": ["left", "right"],
        }
    )
    payload["output"] = "radii"
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    left, right = state["results"]["left"], state["results"]["right"]
    np.testing.assert_allclose(left["parameters"], right["parameters"], atol=1e-9)
    np.testing.assert_allclose(
        left["parameters"][:3], state["results"]["center"]["point_display"], atol=1e-9
    )
    assert left["initialization"]["observations"] == 192
    ids = [*range(96, 192), *range(192, 288)]
    delta = graph.workspace.local[ids] - np.asarray(left["parameters"][:3])
    distance = np.linalg.norm(delta, axis=1)
    residual = distance - left["parameters"][3]
    gradient = np.mean(residual[:, None] * delta / distance[:, None], axis=0)
    assert np.linalg.norm(gradient) < 1e-7, gradient


def test_fixed_shared_center_and_equal_radius_keep_upstream_geometry_fixed() -> None:
    graph = center_graph(fixed=True)
    payload = graph.export_recipe()
    payload["nodes"].append(
        {
            "id": "radii",
            "label": "Equal radii",
            "operation": "equal_radii",
            "surfaces": ["left", "right"],
        }
    )
    payload["output"] = "radii"
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    expected = np.array([2.5, -1.75, 3.25])
    for name in ("seed", "left", "right"):
        np.testing.assert_allclose(
            state["results"][name]["parameters"][:3], expected, atol=1e-8
        )
    radius = np.linalg.norm(graph.workspace.local[96:288] - expected, axis=1).mean()
    assert state["results"]["left"]["parameters"][3] == pytest.approx(radius, abs=1e-8)
    assert state["results"]["right"]["parameters"][3] == pytest.approx(radius, abs=1e-8)


def test_equal_radius_connects_two_free_center_groups_into_one_provider() -> None:
    graph = center_graph()
    graph.workspace.local[:96] = sphere_points(np.array([7, 2, 1]), 1.6)
    payload = graph.export_recipe()
    payload["nodes"].insert(
        1,
        {
            "id": "other-center",
            "label": "Other center",
            "operation": "point",
            "initial_coordinates": [6, 1, 0],
        },
    )
    payload["nodes"].extend(
        [
            {
                "id": "third",
                "label": "Third sphere",
                "operation": "fit",
                "kind": "sphere",
                "selections": ["seed-selection"],
            },
            {
                "id": "other-shared",
                "label": "Other shared center",
                "operation": "shared_center_group",
                "point": "other-center",
                "surfaces": ["third"],
            },
            {
                "id": "radii",
                "label": "Equal radii",
                "operation": "equal_radii",
                "surfaces": ["left", "third"],
            },
        ]
    )
    payload["output"] = "radii"
    recipe = Recipe.model_validate(payload)
    influence = describe_geometry_influence({node.id: node for node in recipe.nodes})
    assert len(influence.providers) == 1
    graph = FeatureGraph(graph.workspace, recipe)
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    left, right, third = (state["results"][name] for name in ("left", "right", "third"))
    np.testing.assert_allclose(
        left["parameters"][:3], right["parameters"][:3], atol=1e-9
    )
    assert (
        np.linalg.norm(np.array(left["parameters"][:3]) - third["parameters"][:3]) > 3
    )
    assert left["parameters"][3] == pytest.approx(third["parameters"][3], abs=1e-9)
    assert left["initialization"]["observations"] == 288


def test_composed_center_constraints_reject_overlapping_evidence_without_fallback() -> (
    None
):
    graph = center_graph()
    payload = graph.export_recipe()
    next(node for node in payload["nodes"] if node["id"] == "right")["selections"] = [
        "left-selection"
    ]
    payload["nodes"].append(
        {
            "id": "radii",
            "label": "Equal radii",
            "operation": "equal_radii",
            "surfaces": ["left", "right"],
        }
    )
    payload["output"] = "radii"
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    with pytest.raises(ValueError, match="overlap"):
        _ = graph.ensure_current(str(graph.snapshot()["token"]), all_actions=True)
    state = cast(dict[str, Any], graph.snapshot())
    assert state["errors"]
    assert state["states"]["shared"] in {"failed", "blocked"}
    assert state["states"]["radii"] in {"failed", "blocked"}


def test_fixed_point_initializer_can_be_listed_without_binding_it_back_to_itself() -> (
    None
):
    graph = center_graph(fixed=True)
    payload = graph.export_recipe()
    payload["nodes"][-1]["surfaces"].insert(0, "seed")
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    nodes = {node.id: node for node in Recipe.model_validate(payload).nodes}
    result = resolve_output(
        OutputReference(feature="seed", output="center", context="shared"),
        nodes,
        state["derived"],
    )
    np.testing.assert_allclose(result["point_display"], [2.5, -1.75, 3.25], atol=1e-8)


def test_shared_center_lineage_preserves_explicit_unsupported_spherical_reuse_boundary() -> (
    None
):
    graph = center_graph()
    recipe = Recipe.model_validate(graph.export_recipe())
    nodes = {node.id: node for node in recipe.nodes}
    assert "shared" in discover_reuse_lineage(["left", "right"], nodes)
    with pytest.raises(ValueError, match="not point datums"):
        _ = reconcile_feature_reuse(
            recipe,
            "reuse",
            ReuseChanges(
                label="Reuse",
                fits=["left", "right"],
                reference_selection="left-selection",
                target_selections=["seed-selection"],
            ),
            "center-test",
            create=True,
        )


def test_removing_shared_center_restores_independent_fit_freedom() -> None:
    graph = center_graph()
    before = evaluated(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "shared"]
    payload["output"] = "left"
    pending = cast(
        dict[str, Any],
        graph.replace(Recipe.model_validate(payload), str(before["token"])),
    )
    assert pending["states"]["left"] != "ready"
    assert pending["states"]["right"] != "ready"
    state = evaluated(graph)
    np.testing.assert_allclose(state["results"]["center"]["point_display"], [0, 0, 0])
    left, right = (
        np.asarray(state["results"][name]["parameters"][:3])
        for name in ("left", "right")
    )
    assert np.linalg.norm(left - right) > 0.1


def test_changing_shared_point_discards_its_old_resolved_center() -> None:
    graph = center_graph()
    before = evaluated(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"].insert(
        1,
        {
            "id": "other",
            "label": "Other point",
            "operation": "point",
            "initial_coordinates": [1, 1, 1],
        },
    )
    payload["nodes"][-1]["point"] = "other"
    _ = graph.replace(Recipe.model_validate(payload), str(before["token"]))
    state = evaluated(graph)
    np.testing.assert_allclose(state["results"]["center"]["point_display"], [0, 0, 0])
    np.testing.assert_allclose(
        state["results"]["other"]["point_display"],
        state["results"]["left"]["parameters"][:3],
        atol=1e-9,
    )


def test_shared_center_composes_with_a_cylinder_radius_without_a_joint() -> None:
    graph = center_graph()
    angle = np.tile(np.linspace(0, 2 * np.pi, 24, endpoint=False), 4)
    z = np.repeat(np.linspace(-2, 2, 4), 24)
    radial = np.column_stack((np.cos(angle), np.sin(angle), np.zeros(96)))
    graph.workspace.local[:96] = (
        np.array([9, 0, 0])
        + np.column_stack((np.zeros(96), np.zeros(96), z))
        + 2.4 * radial
    )
    graph.workspace.data.normals[:96] = radial @ graph.workspace.frame.T
    payload = graph.export_recipe()
    payload["nodes"].extend(
        [
            {
                "id": "cylinder",
                "label": "Cylinder",
                "operation": "fit",
                "kind": "cylinder",
                "selections": ["seed-selection"],
            },
            {
                "id": "radii",
                "label": "Equal radii",
                "operation": "equal_radii",
                "surfaces": ["left", "cylinder"],
            },
        ]
    )
    payload["output"] = "radii"
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    sphere, cylinder = (state["results"][name] for name in ("left", "cylinder"))
    assert sphere["parameters"][3] == pytest.approx(
        cylinder["analytic"]["radius"], abs=1e-8
    )
    np.testing.assert_allclose(
        sphere["parameters"][:3], state["results"]["right"]["parameters"][:3], atol=1e-8
    )
    assert sphere["initialization"]["observations"] == 288


@pytest.mark.parametrize("fixed", [False, True])
def test_two_center_relationships_cannot_bind_the_same_fit_to_different_points(
    fixed: bool,
) -> None:
    graph = center_graph(fixed=fixed)
    payload = graph.export_recipe()
    payload["nodes"].insert(
        -1,
        {
            "id": "other-point",
            "label": "Other point",
            "operation": "point",
            **({"source_fit": "seed"} if fixed else {"initial_coordinates": [9, 2, 1]}),
        },
    )
    payload["nodes"].append(
        {
            "id": "conflict",
            "label": "Conflicting center",
            "operation": "shared_center_group",
            "point": "other-point",
            "surfaces": ["left"],
        }
    )
    payload["output"] = "conflict"
    with pytest.raises(ValueError, match="conflicting point"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(payload))


def test_source_fit_point_alias_tracks_its_sources_shared_center() -> None:
    graph = center_graph(fixed=True)
    payload = graph.export_recipe()
    payload["nodes"].insert(
        1,
        {
            "id": "source-center",
            "label": "Source shared point",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
    )
    payload["nodes"].extend(
        [
            {
                "id": "source-group",
                "label": "Source center binding",
                "operation": "shared_center_group",
                "point": "source-center",
                "surfaces": ["seed"],
            },
            {
                "id": "radii",
                "label": "Equal radii",
                "operation": "equal_radii",
                "surfaces": ["seed", "left"],
            },
        ]
    )
    payload["output"] = "radii"
    graph = FeatureGraph(graph.workspace, Recipe.model_validate(payload))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    for name in ("source-center", "center"):
        np.testing.assert_allclose(
            state["results"][name]["point_display"],
            state["results"]["seed"]["parameters"][:3],
            atol=1e-8,
        )
    for name in ("left", "right"):
        np.testing.assert_allclose(
            state["results"][name]["parameters"][:3],
            state["results"]["seed"]["parameters"][:3],
            atol=1e-8,
        )
