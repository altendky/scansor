"""Source-stage analysis scales with reuse owners, not generated occurrences."""

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest

import experiments.feature_graph as graph_module
from experiments.feature_graph import (
    DirectionAlignment,
    Feature,
    FeatureGraph,
    Recipe,
    ReuseSelection,
    automatic_plane_relationship_components,
    compile_execution_plan,
    describe_geometry_influence,
    reuse_transfer_descendants,
)
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from tests.test_relationship_collections import grouped, pattern
from tests.test_shared_center_group import center_graph


def patterned_recipe(count: int) -> Recipe:
    _, recipe, circular = pattern(upstream=True, equal=True)
    return grouped(
        reconcile_feature_reuse(
            recipe,
            "pattern",
            ReuseChanges.model_validate({"circular": {**circular, "count": count}}),
            "collection-seed",
        ).recipe
    )


def rename(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: "/".join(ids.get(part, part) for part in item.split("/"))
            if key == "managed_key" and isinstance(item, str)
            else rename(item, ids)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [rename(item, ids) for item in value]
    if isinstance(value, str):
        if value.startswith("direction:"):
            return "direction:" + ids.get(value[10:], value[10:])
        return ids.get(value, value)
    return value


def independent_patterns(recipe: Recipe, count: int) -> Recipe:
    payload = recipe.model_dump()
    originals = deepcopy(payload["nodes"])
    for index in range(1, count):
        ids = {
            node["id"]: f"other{index}_{node['id']}"
            for node in originals
            if node["operation"] != "source"
        }

        for node in originals:
            if node["operation"] != "source":
                copied = rename(node, ids)
                copied["label"] = f"Other {index}: {node['label']}"
                payload["nodes"].append(copied)
    return Recipe.model_validate(payload)


@pytest.mark.parametrize("occurrences", [3, 29])
@pytest.mark.parametrize("reuse_count", [1, 2])
def test_compile_analyzes_each_reuse_once(occurrences: int, reuse_count: int) -> None:
    recipe = independent_patterns(patterned_recipe(occurrences), reuse_count)
    nodes = {node.id: node for node in recipe.nodes}
    influence = describe_geometry_influence(nodes)
    saved = recipe.model_dump()
    with (
        patch.object(
            graph_module,
            "reuse_transfer_descendants",
            wraps=graph_module.reuse_transfer_descendants,
        ) as descendants,
        patch.object(
            graph_module,
            "prior_stage_exclusions",
            wraps=graph_module.prior_stage_exclusions,
        ) as exclusions,
        patch.object(
            graph_module,
            "automatic_plane_relationship_components",
            wraps=graph_module.automatic_plane_relationship_components,
        ) as components,
    ):
        plan = compile_execution_plan(recipe, set(nodes), influence)
    assert descendants.call_count == reuse_count
    assert exclusions.call_count == reuse_count
    assert components.call_count == reuse_count
    assert all(
        call.kwargs["plane_components"] is influence.plane_components
        for call in exclusions.call_args_list
    )
    assert len(plan.prior_solves) == reuse_count
    assert recipe.model_dump() == saved


def test_prior_stage_keeps_upstream_context_and_excludes_transfer_feedback() -> None:
    recipe = independent_patterns(patterned_recipe(4), 2)
    nodes: dict[str, Feature] = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe, set(nodes), describe_geometry_influence(nodes)
    )
    assert plan.reuse_source_contexts == frozenset({"pattern", "other1_pattern"})
    for prefix in ("", "other1_"):
        reuse = prefix + "pattern"
        upstream = prefix + "upstream"
        source = prefix + "bore"
        task = "@reuse_planes/" + reuse + "/" + upstream
        stage = plan.prior_solves[task]
        after = reuse_transfer_descendants(reuse, nodes)
        assert [surface.id for surface in stage.surfaces] == [source]
        assert [relation.id for relation in stage.planes] == [upstream]
        assert not plan.dependencies[task].intersection(after)
        assert plan.dependencies[task] == {
            prefix + "pattern_axis",
            prefix + "bore_input",
        }
        assert plan.resolved_reads[task] == {source}
        assert task in plan.dependencies[reuse]
        assert prefix + "alignment" in stage.excluded
        assert (
            all(not key.startswith("other1_") for key in stage.excluded)
            if not prefix
            else all(key.startswith(prefix) for key in stage.excluded)
        )
        for node in nodes.values():
            if isinstance(node, ReuseSelection) and node.reuse == reuse:
                assert stage.excluded <= plan.read_exclusions[node.id]


def test_empty_precomputed_components_do_not_trigger_rediscovery() -> None:
    _, recipe, _ = pattern()
    nodes = {node.id: node for node in recipe.nodes}
    influence = describe_geometry_influence(nodes)
    assert influence.plane_components == {}
    with patch.object(
        graph_module,
        "automatic_plane_relationship_components",
        side_effect=AssertionError(
            "empty precomputed components are still authoritative"
        ),
    ):
        plan = compile_execution_plan(recipe, set(nodes), influence)
    assert plan.prior_solves == {}
    assert plan.reuse_source_contexts == frozenset()


def test_filtered_source_stage_does_not_validate_unused_point_bindings() -> None:
    workspace, recipe, _ = pattern(equal=True)
    generated = next(node for node in recipe.nodes if isinstance(node, ReuseSelection))
    payload = recipe.model_dump()
    payload["nodes"].extend(
        [
            {
                "id": "center",
                "label": "Shared sphere center",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
            {
                "id": "up_sphere",
                "label": "Upstream sphere",
                "operation": "fit",
                "kind": "sphere",
                "selections": ["bore_input"],
            },
            {
                "id": "down_sphere",
                "label": "Downstream sphere",
                "operation": "fit",
                "kind": "sphere",
                "selections": [generated.id],
            },
            {
                "id": "centers",
                "label": "Related sphere centers",
                "operation": "shared_center_group",
                "point": "center",
                "surfaces": ["up_sphere", "down_sphere"],
            },
        ]
    )
    payload["output"] = "centers"
    recipe = Recipe.model_validate(payload)
    nodes = {node.id: node for node in recipe.nodes}
    after = reuse_transfer_descendants("pattern", nodes)
    assert "down_sphere" in after
    assert "up_sphere" not in after
    graph = FeatureGraph(workspace, recipe)
    assert graph.export_recipe() == recipe.model_dump()
    plan = compile_execution_plan(
        recipe, set(nodes), describe_geometry_influence(nodes)
    )
    assert "centers" in plan.order
    assert "up_sphere" in plan.order
    assert "down_sphere" in plan.order


def test_analysis_is_fresh_for_each_compilation_and_mutable_results_are_isolated() -> (
    None
):
    recipe = patterned_recipe(4)
    nodes = {node.id: node for node in recipe.nodes}
    first = compile_execution_plan(
        recipe, set(nodes), describe_geometry_influence(nodes)
    )
    original = deepcopy(first)
    first.prior_solves["@reuse_planes/pattern/upstream"].excluded.clear()
    assert "alignment" in first.read_exclusions["pattern"]
    first.read_exclusions["pattern"].clear()
    second = compile_execution_plan(
        recipe, set(nodes), describe_geometry_influence(nodes)
    )
    assert second == original
    payload = recipe.model_dump()
    alignment = next(node for node in recipe.nodes if node.id == "alignment")
    assert isinstance(alignment, DirectionAlignment)
    payload["nodes"].append(
        alignment.model_copy(
            update={"id": "new_alignment", "label": "New alignment"}
        ).model_dump()
    )
    edited = Recipe.model_validate(payload)
    edited_nodes = {node.id: node for node in edited.nodes}
    updated = compile_execution_plan(
        edited, set(edited_nodes), describe_geometry_influence(edited_nodes)
    )
    assert (
        "new_alignment"
        in updated.prior_solves["@reuse_planes/pattern/upstream"].excluded
    )
    assert (
        "new_alignment"
        not in second.prior_solves["@reuse_planes/pattern/upstream"].excluded
    )


def test_plane_component_discovery_compiles_point_bindings_once() -> None:
    recipe = Recipe.model_validate(center_graph().export_recipe())
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "radii",
            "label": "Radii",
            "operation": "equal_radii",
            "surfaces": ["left", "right"],
        }
    )
    recipe = Recipe.model_validate(payload)
    nodes = {node.id: node for node in recipe.nodes}
    expected = automatic_plane_relationship_components(nodes)
    assert expected
    with (
        patch.object(
            graph_module,
            "shared_center_bindings",
            wraps=graph_module.shared_center_bindings,
        ) as bindings,
        patch.object(graph_module, "fit_point", wraps=graph_module.fit_point) as points,
    ):
        actual = automatic_plane_relationship_components(nodes)
    assert actual == expected
    assert bindings.call_count == 1
    assert points.call_count > 1
    assert all(
        len(call.args) == 3 and call.args[2] is not None
        for call in points.call_args_list
    )
