"""Managed circular placements use actual observations and exact saved volumes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.feature_graph import (
    MAX_RECIPE_ACTIONS,
    CircularReuse,
    FeatureGraph,
    Recipe,
    dependencies,
)
from experiments.feature_reuse import circular_placements
from experiments.feature_reuse_authoring import (
    ReuseAuthoringRequest,
    ReuseChanges,
    apply_feature_reuse,
    preview_feature_reuse,
    reconcile_feature_reuse,
)
from experiments.selection_volume import apply_selection_volume, build_selection_volume
from experiments.selection_volume_operations import dispatch
from tests.circular_reuse_fixture import circular_fixture


def request(
    graph: FeatureGraph[Any], circular: dict[str, Any], *, create: bool = True
) -> ReuseAuthoringRequest:
    return ReuseAuthoringRequest(
        token=str(graph.snapshot()["token"]),
        reuse_id="pattern",
        allocation_seed="stable-test-seed",
        changes=ReuseChanges.model_validate(
            {
                "label": "Circular holes",
                "fits": ["bore", "chamfer"],
                "circular": circular,
            }
        ),
        create=create,
    )


def owned(recipe: Recipe) -> dict[str, Any]:
    return {
        str(node.managed_key): node
        for node in recipe.nodes
        if node.managed_by == "pattern"
    }


def changes(**values: Any) -> ReuseChanges:
    return ReuseChanges.model_validate(values)


def test_compound_tilted_translated_pattern_fits_actual_departures_and_excludes_opposite_sheet() -> (
    None
):
    _, workspace, recipe, circular, expected = circular_fixture()
    graph = FeatureGraph(workspace, recipe)
    intent = request(graph, circular)
    before = graph.snapshot()["recipe"]
    preview = preview_feature_reuse(graph, intent)
    assert (
        graph.snapshot()["recipe"] == before
    )  # Preview evaluates axis; never allocates children.
    assert preview["circular_preview"]["ready"] is True
    assert [
        item["angle_degrees"]
        for item in preview["circular_preview"]["placements"].values()
    ] == [72, 144, 216, 288]
    applied = apply_feature_reuse(graph, intent)
    assert applied["recipe"] == preview["recipe"]
    authored = Recipe.model_validate(applied["recipe"])
    state = cast(
        dict[str, Any], graph.ensure_current(str(applied["token"]), all_actions=True)
    )
    assert state["errors"] == {}
    children = owned(authored)
    assert len(children) == 16
    radii: list[float] = [state["results"]["bore"]["analytic"]["radius"]]
    for occurrence in range(1, 5):
        for role in ("bore", "chamfer"):
            selection = children[
                f"selection/occurrence_{occurrence}/{role}/{role}_input"
            ]
            fit = children[f"fit/occurrence_{occurrence}/{role}"]
            assert state["memberships"][selection.id] == expected[occurrence][role]
            assert state["states"][fit.id] == "ready"
            assert (
                state["results"][selection.id]["volume_reference"]
                == circular["volumes"][0 if role == "bore" else 1]["volume"]
            )
            assert "region" not in state["results"][selection.id]
            assert state["results"][fit.id]["weighted_rms"] < 1e-5
        radii.append(
            state["results"][children[f"fit/occurrence_{occurrence}/bore"].id][
                "analytic"
            ]["radius"]
        )
    np.testing.assert_allclose(radii, 0.5 + 0.003 * np.arange(5), atol=2e-6)
    assert all(
        node.operation not in {"rotational_symmetry", "build_faces", "body"}
        for node in authored.nodes
    )
    # Reopening retains declarations/identities and does not allocate from evaluation.
    reopened_recipe = Recipe.model_validate_json(authored.model_dump_json())
    reopened = FeatureGraph(workspace, reopened_recipe)
    assert reopened.snapshot()["recipe"] == authored.model_dump()
    _ = reopened.ensure_current(str(reopened.snapshot()["token"]), all_actions=True)
    assert reopened.snapshot()["recipe"] == authored.model_dump()


def test_count_axis_and_compound_edits_keep_surviving_roles_and_protect_downstream() -> (
    None
):
    _, _, recipe, circular, _ = circular_fixture()
    first = reconcile_feature_reuse(
        recipe,
        "pattern",
        changes(label="Pattern", fits=["bore", "chamfer"], circular=circular),
        "initial",
        create=True,
    )
    original_ids = {key: node.id for key, node in owned(first.recipe).items()}
    changed = deepcopy(circular)
    changed["count"] = 4
    updated = reconcile_feature_reuse(
        first.recipe, "pattern", changes(circular=changed), "ignored-new-seed"
    )
    assert updated.removed_ids
    assert all(
        original_ids[key] == node.id for key, node in owned(updated.recipe).items()
    )
    changed["volumes"] = changed["volumes"][:1]
    compound = reconcile_feature_reuse(
        updated.recipe,
        "pattern",
        changes(fits=["bore"], circular=changed),
        "different",
    )
    assert all(
        original_ids[key] == node.id for key, node in owned(compound.recipe).items()
    )
    payload = first.recipe.model_dump()
    last = owned(first.recipe)["fit/occurrence_4/bore"].id
    payload["nodes"].append(
        {
            "id": "downstream",
            "operation": "axis",
            "label": "Protected downstream",
            "source_fit": last,
        }
    )
    payload["output"] = "downstream"
    with pytest.raises(ValueError, match=r"Cannot remove.*Protected downstream"):
        _ = reconcile_feature_reuse(
            Recipe.model_validate(payload),
            "pattern",
            changes(circular=changed),
            "seed",
        )
    changed = deepcopy(circular)
    changed["axis"] = last
    with pytest.raises(ValueError, match="own generated outputs"):
        _ = reconcile_feature_reuse(
            first.recipe, "pattern", changes(circular=changed), "seed"
        )


def test_volume_option_and_axis_edits_invalidate_placed_memberships_and_fits() -> None:
    _, workspace, recipe, circular, _ = circular_fixture()
    graph = FeatureGraph(workspace, recipe)
    applied = apply_feature_reuse(graph, request(graph, circular))
    _ = graph.ensure_current(str(applied["token"]), all_actions=True)
    saved = Recipe.model_validate(graph.snapshot()["recipe"])
    children = owned(saved)
    # Update an accepted reusable support through its existing authoring operation.
    _ = dispatch(
        graph,
        "setSelectionVolumeOptions",
        {
            "token": graph.snapshot()["token"],
            "reference": circular["volumes"][0]["volume"],
            "options": {"outward_thickness": 0.01},
        },
    )
    state = cast(dict[str, Any], graph.snapshot())
    assert state["states"]["pattern"] == "stale"
    assert all(state["states"][node.id] == "stale" for node in children.values())
    payload = Recipe.model_validate(state["recipe"]).model_dump(mode="json")
    axis = next(node for node in payload["nodes"] if node["id"] == "pattern_axis")
    axis["initial_parameters"][0] += 0.02
    _ = graph.replace(Recipe.model_validate(payload), str(state["token"]))
    refreshed = cast(
        dict[str, Any],
        graph.ensure_current(str(graph.snapshot()["token"]), all_actions=True),
    )
    assert refreshed["states"]["pattern"] == "ready"
    assert set(owned(Recipe.model_validate(refreshed["recipe"]))) == set(children)


def test_missing_occurrence_keeps_empty_selection_and_inspectable_fit_failure() -> None:
    _, workspace, recipe, circular, _ = circular_fixture(missing=3)
    graph = FeatureGraph(workspace, recipe)
    applied = apply_feature_reuse(graph, request(graph, circular))
    with pytest.raises(ValueError):
        _ = graph.ensure_current(str(applied["token"]), all_actions=True)
    state = cast(dict[str, Any], graph.snapshot())
    children = owned(Recipe.model_validate(state["recipe"]))
    for role in ("bore", "chamfer"):
        selected = children[f"selection/occurrence_3/{role}/{role}_input"]
        fitted = children[f"fit/occurrence_3/{role}"]
        assert state["memberships"][selected.id] == []
        assert state["states"][selected.id] == "ready"
        assert state["states"][fitted.id] == "failed"
        assert fitted.id in state["errors"]
        assert fitted.id not in state["results"]


def test_equal_dimensions_are_visible_optional_managed_relationships() -> None:
    _, workspace, recipe, circular, _ = circular_fixture()
    first = reconcile_feature_reuse(
        recipe,
        "pattern",
        changes(
            label="Pattern",
            fits=["bore", "chamfer"],
            circular=circular,
            equal_corresponding_dimensions=True,
        ),
        "seed",
        create=True,
    )
    equal = owned(first.recipe)["equal-radius/bore"]
    assert equal.operation == "equal_radii" and len(equal.surfaces) == 5
    graph = FeatureGraph(workspace, first.recipe)
    state = cast(
        dict[str, Any],
        graph.ensure_current(str(graph.snapshot()["token"]), all_actions=True),
    )
    assert state["states"][equal.id] == "ready", state["errors"]


def test_circular_compound_preserves_enclosed_coaxial_relationships() -> None:
    _, workspace, recipe, circular, _ = circular_fixture()
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "source_coaxial",
            "label": "Bore and chamfer coaxial",
            "operation": "coaxial",
            "surface": "chamfer",
            "reference": "bore",
        }
    )
    payload["output"] = "source_coaxial"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    applied = apply_feature_reuse(graph, request(graph, circular))
    children = owned(Recipe.model_validate(applied["recipe"]))
    for occurrence in range(1, 5):
        relation = children[f"relationship/occurrence_{occurrence}/source_coaxial"]
        assert relation.surface == children[f"fit/occurrence_{occurrence}/chamfer"].id
        assert relation.reference == children[f"fit/occurrence_{occurrence}/bore"].id
    state = cast(
        dict[str, Any], graph.ensure_current(str(applied["token"]), all_actions=True)
    )
    assert all(
        state["states"][
            children[f"relationship/occurrence_{occurrence}/source_coaxial"].id
        ]
        == "ready"
        for occurrence in range(1, 5)
    ), state["errors"]


def test_explicit_authored_source_edit_reconciles_definitions_atomically() -> None:
    _, workspace, recipe, circular, _ = circular_fixture()
    graph = FeatureGraph(workspace, recipe)
    applied = apply_feature_reuse(graph, request(graph, circular))
    payload = deepcopy(applied["recipe"])
    previous = owned(Recipe.model_validate(payload))
    next(node for node in payload["nodes"] if node["id"] == "bore")["axial_domain"] = [
        -0.4,
        0.4,
    ]
    state = cast(dict[str, Any], graph.replace_authored(payload, str(applied["token"])))
    after = owned(Recipe.model_validate(state["recipe"]))
    assert {key: node.id for key, node in after.items()} == {
        key: node.id for key, node in previous.items()
    }
    assert all(
        node.axial_domain == (-0.4, 0.4)
        for key, node in after.items()
        if key.startswith("fit/") and key.endswith("/bore")
    )
    # The ordinary loader validates the saved authored graph without generating anything.
    restored = FeatureGraph(workspace, Recipe.model_validate(state["recipe"]))
    assert restored.snapshot()["recipe"] == state["recipe"]
    before = cast(dict[str, Any], graph.snapshot())
    invalid = deepcopy(before["recipe"])
    pattern = next(node for node in invalid["nodes"] if node["id"] == "pattern")
    pattern["circular"]["count"] = 4
    protected = after["fit/occurrence_4/bore"].id
    invalid["nodes"].append(
        {
            "id": "outside_axis",
            "label": "Protected downstream",
            "operation": "axis",
            "source_fit": protected,
        }
    )
    invalid["output"] = "outside_axis"
    with pytest.raises(ValueError, match="Protected downstream"):
        _ = graph.replace_authored(invalid, str(before["token"]))
    assert graph.snapshot() == before


def test_incomplete_saved_pattern_is_rejected_without_allocating_missing_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, workspace, recipe, circular, _ = circular_fixture()
    plan = reconcile_feature_reuse(
        recipe,
        "pattern",
        changes(label="Pattern", fits=["bore", "chamfer"], circular=circular),
        "seed",
        create=True,
    )
    payload = plan.recipe.model_dump()
    victim = owned(plan.recipe)["fit/occurrence_4/chamfer"].id
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != victim]
    payload["output"] = "pattern"

    def no_allocation(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Loading a pattern allocated a missing child")

    monkeypatch.setattr("experiments.feature_reuse_authoring.uuid5", no_allocation)
    with pytest.raises(ValueError, match="inventory"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_real_source_selection_can_share_a_circular_occurrence_key() -> None:
    _, workspace, recipe, circular, _ = circular_fixture(bore_selection="occurrence_1")
    graph = FeatureGraph(workspace, recipe)
    applied = apply_feature_reuse(graph, request(graph, circular))
    authored = Recipe.model_validate(applied["recipe"])
    selected = owned(authored)["selection/occurrence_1/bore/occurrence_1"]
    assert selected.source_selection == selected.target_selection == "occurrence_1"
    assert selected.occurrence == 1
    assert dependencies(selected).count("occurrence_1") == 1
    _ = FeatureGraph(workspace, Recipe.model_validate_json(authored.model_dump_json()))


@pytest.mark.parametrize("count", [True, 1, 33, 5.0])
def test_count_is_strict_and_bounded(count: object) -> None:
    with pytest.raises(ValidationError):
        _ = CircularReuse.model_validate(
            {
                "axis": "axis",
                "count": count,
                "volumes": [
                    {
                        "fit": "f",
                        "source_selection": "s",
                        "volume": {"selection": "s", "entry": "e", "volume": "v"},
                    }
                ],
            }
        )


def test_transformed_footprint_retains_hole_disconnected_support_and_normal_gate() -> (
    None
):
    points = np.asarray([[x, y, 0] for y in range(9) for x in range(9)], dtype=float)
    normals = np.tile([0.0, 0.0, 1.0], (len(points), 1))
    triangles: list[list[int]] = []
    for y in range(8):
        for x in range(8):
            a = 9 * y + x
            triangles.extend([[a, a + 1, a + 9], [a + 1, a + 10, a + 9]])
    selected = [
        index
        for index, (x, y, _) in enumerate(points)
        if x < 2 or x > 6 or ((y < 2 or y > 6) and x < 6)
    ]
    volume = build_selection_volume(
        points,
        normals,
        np.asarray(triangles),
        selected,
        kind="mesh",
        options={
            "edge_padding": 0,
            "inward_thickness": 0.01,
            "outward_thickness": 0.01,
            "normal_angle_degrees": 10,
        },
    )
    placement = circular_placements(
        {"point_display": [3, -5, 2], "axis_display": [0.2, -0.3, 1]}, 8
    )["occurrence_1"]
    rotation, translation = (
        np.asarray(placement["rotation"]),
        np.asarray(placement["translation"]),
    )
    order = np.random.default_rng(4).permutation(len(points))
    moved = points[order] @ rotation + translation
    moved_normals = normals[order] @ rotation
    query = np.vstack([moved, moved])
    directions = np.vstack([moved_normals, -moved_normals])
    ids = apply_selection_volume(
        query,
        directions,
        np.ones(len(query)),
        volume,
        query_rotation=rotation.T,
        query_translation=-translation @ rotation.T,
        chunk_size=13,
    )
    assert ids == [
        index for index, original in enumerate(order) if original in selected
    ]
    with pytest.raises(ValueError, match="membership budget"):
        _ = apply_selection_volume(
            query,
            directions,
            np.ones(len(query)),
            volume,
            query_rotation=rotation.T,
            query_translation=-translation @ rotation.T,
            max_selected=3,
        )


def test_action_budget_rejects_before_generated_child_id_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, recipe, circular, _ = circular_fixture()
    payload = recipe.model_dump()
    payload["nodes"].extend(
        {
            "id": f"extra_{index}",
            "label": f"Unrelated {index}",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        }
        for index in range(MAX_RECIPE_ACTIONS - len(recipe.nodes) - 2)
    )
    large = Recipe.model_validate(payload)
    before = large.model_dump()

    def no_allocation(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Generated child allocation preceded admission")

    monkeypatch.setattr("experiments.feature_reuse_authoring.uuid5", no_allocation)
    with pytest.raises(ValueError, match="action budget"):
        _ = reconcile_feature_reuse(
            large,
            "pattern",
            changes(label="Pattern", fits=["bore", "chamfer"], circular=circular),
            "seed",
            create=True,
        )
    assert large.model_dump() == before


def test_overlapping_occurrences_are_inspectable_instead_of_silently_reassigned() -> (
    None
):
    _, workspace, recipe, circular, _ = circular_fixture()
    circular.update(axis="bore", count=3)
    graph = FeatureGraph(workspace, recipe)
    applied = apply_feature_reuse(graph, request(graph, circular))
    state = cast(
        dict[str, Any], graph.ensure_current(str(applied["token"]), all_actions=True)
    )
    children = owned(Recipe.model_validate(state["recipe"]))
    selected = children["selection/occurrence_2/bore/bore_input"]
    diagnostics = state["results"][selected.id]["selection_diagnostics"]
    assert diagnostics["overlap_occurrence_zero"] == 96
    assert any(item["vertices"] == 96 for item in diagnostics["overlaps"])
