"""Backend reuse authorship preserves inspectable identities and references."""

from pathlib import Path
from typing import Any, cast

import pytest

from experiments.feature_graph import FeatureReuse, Recipe, dependencies
from experiments.feature_reuse_authoring import (
    ReuseChanges,
    ReusePlan,
    reconcile_feature_reuse,
)


def _node(operation: str, identity: str, **fields: Any) -> dict[str, Any]:
    return {"id": identity, "label": identity, "operation": operation, **fields}


def _recipe(*, constrained: bool = False) -> Recipe:
    nodes = [
        _node("source", "scan", source_sha256="source", reference_sha256="reference"),
        *[
            _node("selection", key, source="scan", ids=[1, 2, 3])
            for key in ("a", "b", "c")
        ],
    ]
    if constrained:
        nodes.extend(
            [
                _node(
                    "axis",
                    "axis",
                    initial_parameters=[0.1, -0.2, 3, 4],
                    direction_reversed=True,
                ),
                _node(
                    "reference_plane",
                    "axial",
                    axis="axis",
                    construction="perpendicular_to_axis",
                    initial_angle_degrees=None,
                    offset=7,
                ),
                _node(
                    "reference_plane",
                    "clock",
                    axis="axis",
                    construction="parallel_to_axis",
                    initial_angle_degrees=28,
                    offset=2,
                ),
            ]
        )
    nodes.append(
        _node(
            "fit",
            "outer",
            selections=["a"],
            kind="cylinder",
            **({"axis": "axis"} if constrained else {}),
        )
    )
    if constrained:
        nodes.extend(
            [
                _node("fit", "bore", selections=["a"], kind="cylinder", axis="axis"),
                _node(
                    "fit",
                    "shoulder",
                    selections=["a"],
                    kind="plane",
                    reference_plane="axial",
                ),
                _node(
                    "fit",
                    "flat",
                    selections=["a"],
                    kind="plane",
                    reference_plane="clock",
                ),
                _node("fit", "other", selections=["a"], kind="plane"),
                _node("coaxial", "coaxial", surface="bore", reference="outer"),
                _node(
                    "perpendicular", "perpendicular", lateral="outer", plane="shoulder"
                ),
                _node("parallel", "parallel", surface="flat", reference_plane="clock"),
                _node(
                    "mirror_symmetry",
                    "mirror",
                    plane="clock",
                    surfaces=["shoulder", "other"],
                    symmetric_extents=False,
                ),
                _node(
                    "rotational_symmetry",
                    "rotation",
                    axis="outer",
                    planes=["shoulder", "flat", "other"],
                ),
                _node(
                    "equal",
                    "equal",
                    left={"measurement": "radius", "surface": "outer"},
                    right={
                        "measurement": "plane_distance",
                        "surface": "shoulder",
                        "reference_plane": "axial",
                    },
                ),
                _node("equal_radii", "radii", surfaces=["outer", "bore"]),
                _node(
                    "plane_relationship",
                    "planes",
                    surfaces=["shoulder", "other"],
                    relation="parallel",
                ),
                _node(
                    "joint_fit",
                    "joint",
                    constraints=["coaxial", "perpendicular", "parallel", "equal"],
                ),
                _node(
                    "axis_solve",
                    "solve",
                    axis="axis",
                    factors=["outer", "bore", "shoulder", "flat"],
                ),
            ]
        )
    return Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": nodes,
            "groups": [{"id": "group", "label": "Group"}],
            "output": "outer",
        }
    )


def _create(recipe: Recipe, *, equal: bool = False) -> ReusePlan:
    fits = [node.id for node in recipe.nodes if node.operation == "fit"]
    return reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges(
            label="Reuse",
            group_id="group",
            fits=fits,
            reference_selection="a",
            target_selections=["b"],
            equal_corresponding_dimensions=equal,
        ),
        "creation",
        create=True,
    )


def _owned(recipe: Recipe, key: str) -> Any:
    return next(
        node
        for node in recipe.nodes
        if node.managed_by == "reuse" and node.managed_key == key
    )


def test_create_is_pure_deterministic_and_preserves_append_output_contract() -> None:
    recipe = _recipe()
    original = recipe.model_dump()
    plan = _create(recipe)
    assert recipe.model_dump() == original
    assert plan.recipe.nodes[: len(recipe.nodes)] == recipe.nodes
    assert plan.recipe.groups == recipe.groups
    assert plan.recipe.output == plan.selected_id == plan.recipe.nodes[-1].id
    assert plan.recipe.nodes[-1].managed_key == "fit/b/outer"
    assert set(plan.generated_ids) == {
        node.id for node in plan.recipe.nodes[len(recipe.nodes) :]
    } - {"reuse"}
    assert not plan.removed_ids
    assert _create(recipe).recipe == plan.recipe
    reuse = cast(
        FeatureReuse, next(node for node in plan.recipe.nodes if node.id == "reuse")
    )
    assert reuse.group_id == "group"
    assert reuse.lineage == ["scan", "a", "outer"]


def test_all_datum_factor_and_relationship_references_are_remapped() -> None:
    recipe = _recipe(constrained=True)
    plan = _create(recipe, equal=True)
    copied_axis = _owned(plan.recipe, "datum/b/axis")
    assert copied_axis.initial_parameters == (0.1, -0.2, 3, 4)
    assert copied_axis.direction_reversed
    for source in ("axis", "axial", "clock"):
        datum = _owned(plan.recipe, f"datum/b/{source}")
        assert datum.placement.model_dump() == {
            "reuse": "reuse",
            "source": source,
            "target_selection": "b",
        }
        assert datum.group_id is None
    assert _owned(plan.recipe, "datum/b/axial").axis == copied_axis.id
    assert _owned(plan.recipe, "datum/b/clock").offset == 2
    assert _owned(plan.recipe, "fit/b/outer").axis == copied_axis.id
    assert (
        _owned(plan.recipe, "fit/b/shoulder").reference_plane
        == _owned(plan.recipe, "datum/b/axial").id
    )
    assert (
        _owned(plan.recipe, "fit/b/flat").reference_plane
        == _owned(plan.recipe, "datum/b/clock").id
    )
    by_id = {node.id: node for node in plan.recipe.nodes}
    for source in (
        "coaxial",
        "perpendicular",
        "parallel",
        "mirror",
        "rotation",
        "equal",
        "radii",
        "planes",
        "joint",
        "solve",
    ):
        child = _owned(plan.recipe, f"relationship/b/{source}")
        assert dependencies(child)
        assert all(by_id[key].managed_by == "reuse" for key in dependencies(child))
    assert not _owned(plan.recipe, "relationship/b/mirror").symmetric_extents
    assert (
        _owned(plan.recipe, "relationship/b/equal").right.reference_plane
        == _owned(plan.recipe, "datum/b/axial").id
    )
    assert {
        node.managed_key
        for node in plan.recipe.nodes
        if (node.managed_key or "").startswith("equal-radius/")
    } == {"equal-radius/outer", "equal-radius/bore"}
    seen: set[str] = set()
    for node in plan.recipe.nodes:
        assert set(dependencies(node)) <= seen
        seen.add(node.id)


def test_idempotence_target_updates_and_equality_toggle_retain_identities() -> None:
    initial = _create(_recipe(), equal=True)
    stable = reconcile_feature_reuse(
        initial.recipe, "reuse", ReuseChanges(), "different-seed"
    )
    assert stable.recipe == initial.recipe
    assert not stable.generated_ids
    assert not stable.removed_ids
    extended = reconcile_feature_reuse(
        stable.recipe, "reuse", ReuseChanges(target_selections=["b", "c"]), "extension"
    )
    for child in initial.recipe.nodes:
        if child.managed_by == "reuse":
            retained = next(
                node for node in extended.recipe.nodes if node.id == child.id
            )
            assert retained.label == child.label
    equality = _owned(extended.recipe, "equal-radius/outer")
    assert equality.surfaces == [
        "outer",
        _owned(extended.recipe, "fit/b/outer").id,
        _owned(extended.recipe, "fit/c/outer").id,
    ]
    reduced = reconcile_feature_reuse(
        extended.recipe, "reuse", ReuseChanges(target_selections=["c"]), "reduction"
    )
    assert (
        _owned(reduced.recipe, "fit/c/outer").id
        == _owned(extended.recipe, "fit/c/outer").id
    )
    assert _owned(reduced.recipe, "equal-radius/outer").id == equality.id
    assert set(reduced.removed_ids) == {
        _owned(extended.recipe, "fit/b/outer").id,
        _owned(extended.recipe, "selection/b/outer/a").id,
    }
    assert reduced.recipe.output == initial.recipe.output
    independent = reconcile_feature_reuse(
        reduced.recipe,
        "reuse",
        ReuseChanges(equal_corresponding_dimensions=False),
        "independent",
    )
    assert equality.id in independent.removed_ids
    assert not any(
        node.managed_key == "equal-radius/outer" for node in independent.recipe.nodes
    )


def test_reusing_an_owners_generated_fit_is_rejected_without_mutation() -> None:
    initial = _create(_recipe())
    before = initial.recipe.model_dump()
    child = _owned(initial.recipe, "fit/b/outer")
    with pytest.raises(ValueError, match="own generated fits"):
        _ = reconcile_feature_reuse(
            initial.recipe, "reuse", ReuseChanges(fits=[child.id]), "recursive"
        )
    assert initial.recipe.model_dump() == before


def test_removing_current_output_selects_owner_and_group_updates_are_retained() -> None:
    initial = _create(_recipe())
    plan = reconcile_feature_reuse(
        initial.recipe,
        "reuse",
        ReuseChanges(target_selections=["c"], group_id=None),
        "replacement",
    )
    assert initial.recipe.output in plan.removed_ids
    assert plan.recipe.output == "reuse"
    assert (
        next(node for node in plan.recipe.nodes if node.id == "reuse").group_id is None
    )


def test_legacy_generated_selection_and_fit_are_adopted_without_replacing_ids() -> None:
    payload = _create(_recipe()).recipe.model_dump()
    before_ids: list[str] = []
    for node in payload["nodes"]:
        if node.get("managed_by") == "reuse":
            before_ids.append(node["id"])
            node["managed_by"] = None
            node["managed_key"] = None
    legacy = Recipe.model_validate(payload)
    plan = reconcile_feature_reuse(
        legacy, "reuse", ReuseChanges(target_selections=["b", "c"]), "legacy-update"
    )
    assert set(before_ids) <= {node.id for node in plan.recipe.nodes}
    assert all(
        node.managed_by == "reuse" and node.managed_key
        for node in plan.recipe.nodes
        if node.id in before_ids
    )
    assert plan.recipe.output == legacy.output


@pytest.mark.parametrize("consumer", ["datum", "face", "equality"])
def test_external_consumers_block_removal_atomically(consumer: str) -> None:
    initial = _create(_recipe(), equal=True)
    fitted = _owned(initial.recipe, "fit/b/outer")
    equality = _owned(initial.recipe, "equal-radius/outer")
    if consumer == "datum":
        external = _node("axis", "external", source_fit=fitted.id)
        changes = ReuseChanges(target_selections=["c"])
    elif consumer == "face":
        external = _node(
            "arranged_face",
            "external",
            surface={"feature": fitted.id},
            cutters=[{"feature": "outer"}],
            domains=[],
            selector={"signs": {}, "component_count": 1, "witness_chart": [0, 0]},
        )
        changes = ReuseChanges(target_selections=["c"])
    else:
        external = _node("joint_fit", "external", constraints=[equality.id])
        changes = ReuseChanges(equal_corresponding_dimensions=False)
    payload = initial.recipe.model_dump()
    payload["nodes"].append(external)
    recipe = Recipe.model_validate(payload)
    before = recipe.model_dump()
    with pytest.raises(ValueError, match="external"):
        _ = reconcile_feature_reuse(recipe, "reuse", changes, "blocked")
    assert recipe.model_dump() == before


def test_fit_derived_axis_reuses_seed_fit_and_rejects_an_omitted_initializer() -> None:
    payload = _recipe().model_dump()
    payload["nodes"].extend(
        [
            _node("axis", "initialized", source_fit="outer"),
            _node(
                "fit", "bound", kind="cylinder", selections=["a"], axis="initialized"
            ),
        ]
    )
    recipe = Recipe.model_validate(payload)
    plan = _create(recipe)
    axis = _owned(plan.recipe, "datum/b/initialized")
    assert axis.placement is None
    assert axis.source_fit == _owned(plan.recipe, "fit/b/outer").id
    with pytest.raises(ValueError, match=r"[Ii]nclude.*outer"):
        _ = reconcile_feature_reuse(
            plan.recipe, "reuse", ReuseChanges(fits=["bound"]), "omission"
        )


@pytest.mark.parametrize("unsupported", ["sphere", "points"])
def test_unsupported_source_geometry_is_rejected(unsupported: str) -> None:
    payload = _recipe().model_dump()
    if unsupported == "sphere":
        payload["nodes"][-1]["kind"] = "sphere"
    else:
        payload["nodes"][-1:-1] = [
            _node("point", "p", initial_coordinates=[0, 0, 0]),
            _node("point", "q", initial_coordinates=[0, 0, 1]),
            _node("axis", "point_axis", source_points=["p", "q"]),
        ]
        payload["nodes"][-1]["axis"] = "point_axis"
    with pytest.raises(ValueError):
        _ = _create(Recipe.model_validate(payload))


def test_retained_reuse_recipe_preserves_managed_ids_and_dependency_order() -> None:
    recipe = Recipe.model_validate_json(
        Path(
            "examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json"
        ).read_bytes()
    )
    original = recipe.model_dump()
    reuse = next(node for node in recipe.nodes if node.operation == "feature_reuse")
    plan = reconcile_feature_reuse(recipe, reuse.id, ReuseChanges(), "retained")
    assert recipe.model_dump() == original
    assert not plan.generated_ids
    assert not plan.removed_ids
    assert plan.recipe.output == recipe.output
    assert plan.recipe == recipe
    before = {
        node.managed_key: node for node in recipe.nodes if node.managed_by == reuse.id
    }
    after = {
        node.managed_key: node
        for node in plan.recipe.nodes
        if node.managed_by == reuse.id
    }
    assert before.keys() == after.keys()
    assert all(
        (after[key].id, after[key].label) == (node.id, node.label)
        for key, node in before.items()
    )
    seen: set[str] = set()
    for node in plan.recipe.nodes:
        assert set(dependencies(node)) <= seen
        seen.add(node.id)
