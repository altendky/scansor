"""Live pattern membership and fixed source-stage relationship inputs."""

from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    DirectionAlignment,
    FeatureGraph,
    Recipe,
    compile_execution_plan,
    describe_geometry_influence,
    plane_relationship_endpoints,
)
from experiments.feature_inputs import (
    OutputReference,
    compatibility_reason,
    output_catalogue,
    resolve_output,
)
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.relationship_collections import collection_members
from experiments.relationship_inputs import validate_relationship_inputs
from tests.circular_reuse_fixture import circular_fixture


def pattern(
    *, upstream: bool = False, equal: bool = False
) -> tuple[Any, Recipe, dict[str, Any]]:
    _, workspace, recipe, circular, _ = circular_fixture(count=4)
    if upstream:
        payload = recipe.model_dump()
        payload["nodes"].append(
            {
                "id": "upstream",
                "label": "Upstream",
                "operation": "direction_alignment",
                "directions": [
                    {"feature": "bore", "output": "axis"},
                    {"feature": "pattern_axis", "output": "axis"},
                ],
            }
        )
        payload["output"] = "upstream"
        recipe = Recipe.model_validate(payload)
    plan = reconcile_feature_reuse(
        recipe,
        "pattern",
        ReuseChanges.model_validate(
            {
                "label": "Pattern",
                "fits": ["bore", "chamfer"],
                "circular": circular,
                "equal_corresponding_dimensions": equal,
            }
        ),
        "collection-seed",
        create=True,
    )
    return workspace, plan.recipe, circular


def collection() -> OutputReference:
    return OutputReference(feature="pattern", output="direction:bore")


def grouped(recipe: Recipe, *, reference: dict[str, str] | None = None) -> Recipe:
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "alignment",
            "label": "Pattern axes",
            "operation": "direction_alignment",
            "directions": [collection().model_dump()],
            "reference": reference,
        }
    )
    payload["output"] = "alignment"
    return Recipe.model_validate(payload)


def test_collection_declares_all_members_and_restricts_single_geometry_consumers() -> (
    None
):
    _, recipe, _ = pattern()
    outputs = output_catalogue(recipe)
    output = next(
        item for item in outputs if item["reference"] == collection().model_dump()
    )
    assert output["collection"]["count"] == 4
    assert output["collection"]["source_count"] == 1
    assert output["collection"]["adjustable_count"] == 3
    assert output["collection"]["members"][0] == {
        "feature": "bore",
        "output": "axis",
        "context": "pattern",
    }
    assert compatibility_reason(output, "relationship_direction_targets") is None
    assert compatibility_reason(output, "direction")
    assert compatibility_reason(output, "relationship_direction_reference")
    augmented = grouped(recipe)
    node = cast(DirectionAlignment, augmented.nodes[-1])
    endpoints = plane_relationship_endpoints(
        node, {node.id: node for node in augmented.nodes}
    )
    assert endpoints[0].owner is None
    assert all(endpoint.owner is not None for endpoint in endpoints[1:])
    roles = validate_relationship_inputs(
        "direction_alignment", {"participants": node.directions}, augmented
    )
    assert len(roles["participants"]) == 4
    assert all(
        item["preview"] is None
        for item in outputs
        if item["reference"]["context"] == "pattern"
    )


def test_membership_tracks_count_changes_without_rewriting_the_collection() -> None:
    _, recipe, circular = pattern()
    recipe = grouped(recipe)
    original = collection_members(
        collection(), {node.id: node for node in recipe.nodes}
    )
    assert original is not None
    smaller = reconcile_feature_reuse(
        recipe,
        "pattern",
        ReuseChanges.model_validate({"circular": {**circular, "count": 3}}),
        "collection-seed",
    ).recipe
    assert smaller.nodes[-1].model_dump()["directions"] == [collection().model_dump()]
    reduced = collection_members(
        collection(), {node.id: node for node in smaller.nodes}
    )
    assert reduced == original[:3]
    restored = reconcile_feature_reuse(
        smaller,
        "pattern",
        ReuseChanges.model_validate({"circular": circular}),
        "collection-seed",
    ).recipe
    assert (
        collection_members(collection(), {node.id: node for node in restored.nodes})
        == original
    )
    assert (
        Recipe.model_validate_json(restored.model_dump_json()).model_dump()
        == restored.model_dump()
    )


def test_collection_rejects_incomplete_inventory_and_removed_templates() -> None:
    _, recipe, _ = pattern()
    nodes = {node.id: node for node in recipe.nodes}
    members = collection_members(collection(), nodes)
    assert members is not None
    del nodes[members[-1].feature]
    with pytest.raises(ValueError, match="inventory"):
        _ = collection_members(collection(), nodes)
    nodes = {node.id: node for node in recipe.nodes}
    nodes["pattern"] = nodes["pattern"].model_copy(update={"fits": ["chamfer"]})
    with pytest.raises(ValueError, match="template"):
        _ = collection_members(collection(), nodes)


def test_duplicate_targets_are_rejected_but_source_reference_overlap_is_explicitly_supported() -> (
    None
):
    _, recipe, _ = pattern()
    node = grouped(recipe).nodes[-1]
    assert isinstance(node, DirectionAlignment)
    with pytest.raises(ValueError, match="distinct"):
        _ = validate_relationship_inputs(
            "direction_alignment",
            {"participants": [collection(), collection()]},
            recipe,
        )
    source = OutputReference(feature="bore", output="axis", context="pattern")
    roles = validate_relationship_inputs(
        "direction_alignment",
        {"reference": [source], "participants": [collection()]},
        recipe,
    )
    assert len(roles["reference"]) == 1
    assert len(roles["participants"]) == 3
    with pytest.raises(ValueError, match="distinct"):
        _ = validate_relationship_inputs(
            "direction_alignment",
            {"reference": [source], "participants": [source]},
            recipe,
        )


@pytest.mark.parametrize("upstream", [False, True])
def test_collection_evaluates_with_fixed_source_without_placement_feedback(
    upstream: bool,
) -> None:
    workspace, recipe, _ = pattern(upstream=upstream)
    graph = FeatureGraph(workspace, grouped(recipe))
    saved = graph.export_recipe()
    pending = cast(dict[str, Any], graph.snapshot())
    assert pending["states"]["alignment"] == "unevaluated"
    state = cast(
        dict[str, Any], graph.evaluate(str(pending["token"]), target="alignment")
    )
    assert state["errors"] == {}
    assert state["states"]["alignment"] == "ready"
    source = resolve_output(
        OutputReference(feature="bore", output="axis", context="pattern"),
        {node.id: node for node in recipe.nodes},
        state["derived"],
    )
    reference_axis = np.asarray(source["axis_display"])
    for value in state["derived"]["alignment"]["surfaces"].values():
        assert (
            np.linalg.norm(np.cross(reference_axis, value["analytic"]["axis"])) < 1e-7
        )
    assert "bore" not in state["derived"]["alignment"]["surfaces"]
    assert graph.export_recipe() == saved


@pytest.mark.parametrize("upstream", [False, True])
def test_source_port_precedes_constraints_that_already_couple_original_and_copies(
    upstream: bool,
) -> None:
    workspace, recipe, _ = pattern(upstream=upstream, equal=True)
    graph = FeatureGraph(workspace, grouped(recipe))
    state = cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), target="alignment"),
    )
    assert state["errors"] == {}
    assert state["states"]["alignment"] == "ready"
    assert "bore" in state["derived"]["pattern"]["outputs"]
    source = state["derived"]["pattern"]["outputs"]["bore"]["analytic"]["radius"]
    assert source == pytest.approx(0.5, abs=1e-6)
    current = state["derived"]["alignment"]["surfaces"]["bore"]["analytic"]["radius"]
    assert abs(current - source) > 0.001


def test_reuse_clones_optional_fixed_reference_into_the_matching_owned_context() -> (
    None
):
    _, _, recipe, circular, _ = circular_fixture(count=4)
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "upstream",
            "label": "Upstream",
            "operation": "direction_alignment",
            "reference": {"feature": "pattern_axis", "output": "axis"},
            "directions": [{"feature": "bore", "output": "axis"}],
        }
    )
    payload["output"] = "upstream"
    plan = reconcile_feature_reuse(
        Recipe.model_validate(payload),
        "pattern",
        ReuseChanges.model_validate(
            {"label": "Pattern", "fits": ["bore", "chamfer"], "circular": circular}
        ),
        "collection-seed",
        create=True,
    )
    children = {
        node.managed_key: node
        for node in plan.recipe.nodes
        if node.managed_by == "pattern"
    }
    copied = children["relationship/occurrence_1/upstream"]
    assert isinstance(copied, DirectionAlignment)
    axis = children["datum/occurrence_1/pattern_axis"]
    assert copied.reference == OutputReference(feature=axis.id, output="axis")


def test_expanded_capacity_counts_members_instead_of_authored_chips() -> None:
    _, recipe, _ = pattern()
    payload = recipe.model_dump()
    payload["nodes"].extend(
        {
            "id": f"axis_{index}",
            "label": f"Axis {index}",
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        }
        for index in range(29)
    )
    recipe = Recipe.model_validate(payload)
    targets = [
        collection(),
        *(
            OutputReference(feature=f"axis_{index}", output="axis")
            for index in range(28)
        ),
    ]
    reference = OutputReference(feature="pattern_axis", output="axis")
    roles = validate_relationship_inputs(
        "direction_alignment",
        {"reference": [reference], "participants": targets},
        recipe,
    )
    assert sum(map(len, roles.values())) == 33
    with pytest.raises(ValueError, match="2 to 33"):
        _ = validate_relationship_inputs(
            "direction_alignment",
            {
                "reference": [reference],
                "participants": [
                    *targets,
                    OutputReference(feature="axis_28", output="axis"),
                ],
            },
            recipe,
        )


def test_fixed_reference_and_source_stage_recompute_after_upstream_edit() -> None:
    workspace, recipe, _ = pattern(upstream=True)
    graph = FeatureGraph(
        workspace,
        grouped(
            recipe,
            reference={
                "feature": "pattern_axis",
                "output": "axis",
                "context": "upstream",
            },
        ),
    )
    first = cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), target="alignment"),
    )
    assert first["errors"] == {}
    saved = graph.export_recipe()
    axis = next(node for node in saved["nodes"] if node["id"] == "pattern_axis")
    axis["direction_reversed"] = not axis["direction_reversed"]
    _ = graph.replace(Recipe.model_validate(saved), str(first["token"]))
    pending = cast(dict[str, Any], graph.snapshot())
    assert pending["states"]["alignment"] == "stale"
    again = cast(
        dict[str, Any], graph.evaluate(str(pending["token"]), target="alignment")
    )
    assert again["errors"] == {}
    assert again["states"]["alignment"] == "ready"
    assert (
        graph.export_recipe()["nodes"][-1]["reference"]
        == saved["nodes"][-1]["reference"]
    )
    np.testing.assert_allclose(
        again["results"]["pattern_axis"]["axis_display"],
        -np.asarray(first["results"]["pattern_axis"]["axis_display"]),
        atol=1e-7,
    )


def test_reconciling_after_source_port_reference_edit_keeps_relation_downstream() -> (
    None
):
    _, recipe, _ = pattern()
    recipe = grouped(
        recipe, reference={"feature": "bore", "output": "axis", "context": "pattern"}
    )
    plan = reconcile_feature_reuse(recipe, "pattern", ReuseChanges(), "collection-seed")
    assert plan.recipe.nodes[-1].model_dump()["reference"] == {
        "feature": "bore",
        "output": "axis",
        "context": "pattern",
    }
    owner = next(node for node in plan.recipe.nodes if node.id == "pattern")
    assert "alignment" not in owner.model_dump()["lineage"]


def test_ready_ordinary_reuse_requests_source_ports_only_for_authored_consumers() -> (
    None
):
    workspace, recipe, _ = pattern()
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe, set(nodes), describe_geometry_influence(nodes)
    )
    assert plan.reuse_source_contexts == frozenset()
    assert not any(stage.standalone for stage in plan.prior_solves.values())
    graph = FeatureGraph(workspace, recipe)
    before = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert before["errors"] == {}
    assert before["states"]["pattern"] == "ready"
    assert "outputs" not in before["derived"]["pattern"]
    source_ref = {"feature": "bore", "output": "axis", "context": "pattern"}
    source = next(
        item for item in before["input_catalogue"] if item["reference"] == source_ref
    )
    aggregate = next(
        item
        for item in before["input_catalogue"]
        if item["reference"] == collection().model_dump()
    )
    assert source["availability"] == aggregate["availability"] == "unevaluated"
    assert source["preview"] is None
    assert compatibility_reason(source, "relationship_direction_reference") is None
    assert compatibility_reason(aggregate, "relationship_direction_targets") is None

    authored = grouped(Recipe.model_validate(graph.export_recipe()))
    pending = cast(dict[str, Any], graph.replace(authored, str(before["token"])))
    assert pending["states"]["pattern"] == "stale"
    assert "pattern" not in pending["derived"]
    after = cast(
        dict[str, Any], graph.evaluate(str(pending["token"]), target="alignment")
    )
    assert after["errors"] == {}
    assert "bore" in after["derived"]["pattern"]["outputs"]

    removed = graph.export_recipe()
    removed["nodes"] = [node for node in removed["nodes"] if node["id"] != "alignment"]
    removed["output"] = "pattern"
    pending = cast(
        dict[str, Any],
        graph.replace(Recipe.model_validate(removed), str(after["token"])),
    )
    assert pending["states"]["pattern"] == "stale"
    assert "pattern" not in pending["derived"]
    final = cast(
        dict[str, Any], graph.evaluate(str(pending["token"]), target="pattern")
    )
    assert final["errors"] == {}
    assert "outputs" not in final["derived"]["pattern"]


@pytest.mark.parametrize("state", ["failed", "blocked", "running", "stale"])
def test_unmaterialized_ports_preserve_their_owner_failure_and_progress(
    state: str,
) -> None:
    _, recipe, _ = pattern()
    states = dict.fromkeys((node.id for node in recipe.nodes), "unevaluated")
    states["pattern"] = state
    catalogue = output_catalogue(recipe, states)
    source = next(
        item
        for item in catalogue
        if item["reference"]
        == {"feature": "bore", "output": "axis", "context": "pattern"}
    )
    aggregate = next(
        item for item in catalogue if item["reference"] == collection().model_dump()
    )
    assert source["availability"] == aggregate["availability"] == state
