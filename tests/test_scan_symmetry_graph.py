"""Imported named symmetry solves consume observations, not raw fit admission."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    AxisDefinition,
    AxisSolve,
    Coaxial,
    EqualQuantities,
    Feature,
    FeatureGraph,
    FrameDefinition,
    Growth,
    JointFit,
    MirrorSymmetry,
    ParallelToPlane,
    Perpendicular,
    PlaneDefinition,
    PlaneDistance,
    PointDefinition,
    Recipe,
    RotationalSymmetry,
    Selection,
    Source,
    SurfaceFit,
    SurfaceRadius,
    compile_execution_plan,
    describe_geometry_influence,
)
from experiments.feature_inputs import OutputReference, resolve_output
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.scan_workspace import ScanWorkspace
from experiments.support_intent import PlaneSupport
from tests.symmetry_fixture import RawSymmetryFixture, SurfaceKind, raw_symmetry_fixture
from tests.test_selected_symmetry_fit import check_rows, check_truth


def symmetry_recipe(
    fixture: RawSymmetryFixture,
    *,
    fixed: bool = False,
    huge_hint: bool = False,
    explicit_support: bool = False,
    reverse_axis: bool = False,
) -> Recipe:
    """Native test/fixture authoring only; saved actions contain no fit results."""
    workspace = fixture.workspace
    assert workspace.source_sha256 is not None
    actions: list[Feature] = [
        Source(
            id="scan",
            label="Scan",
            operation="source",
            source_sha256=workspace.source_sha256,
            reference_sha256=workspace.reference_sha256,
            descriptor=workspace.descriptor,
        )
    ]
    if fixture.mode == "mirror":
        if fixed:
            for index, point in enumerate(
                (fixture.common_point, fixture.common_point + fixture.common_axis)
            ):
                actions.append(
                    PointDefinition(
                        id=f"point{index}",
                        label=f"Point {index}",
                        operation="point",
                        initial_coordinates=tuple(point),
                    )
                )
            axis = AxisDefinition(
                id="axis",
                label="Axis",
                operation="axis",
                source_points=("point0", "point1"),
                direction_reversed=reverse_axis,
            )
        else:
            axis = AxisDefinition(
                id="axis",
                label="Axis",
                operation="axis",
                initial_parameters=(1e200, -1e200, 1e200, -1e200)
                if huge_hint
                else (0, 0, 0, 0),
                direction_reversed=reverse_axis,
            )
        actions.append(axis)
        # Independently author the known clock for a fixed world provider.
        direction = -fixture.common_axis if reverse_axis else fixture.common_axis
        reference = np.eye(3)[int(np.argmin(abs(direction)))]
        u = np.cross(direction, reference)
        u /= np.linalg.norm(u)
        v = np.cross(direction, u)
        radial = np.cross(fixture.mirror_normal, direction)
        angle = float(np.degrees(np.arctan2(radial @ v, radial @ u)))
        actions.append(
            PlaneDefinition(
                id="clock",
                label="Clock",
                operation="reference_plane",
                axis="axis",
                initial_angle_degrees=angle if fixed else 137,
            )
        )
    for name in ("main", "other", "cap", *fixture.members):
        kind = (
            "plane"
            if name == "cap"
            else "cylinder"
            if name in {"main", "other"}
            else fixture.kind
        )
        actions.append(
            Selection(
                id=name + "_selection",
                label=name + " selection",
                operation="selection",
                source="scan",
                ids=fixture.ids[name],
            )
        )
        domain = None
        if explicit_support and kind != "plane":
            truth = fixture.truth[name]
            direction = np.asarray(truth["axis"])
            origin = np.asarray(truth["origin"])
            if abs(direction[2]) <= 1e-8:
                raise ValueError(
                    "this saved explicit interval needs a source-Z intersection"
                )
            direction = direction if direction[2] > 0 else -direction
            selected_h = (workspace.local[fixture.ids[name]] - origin) @ direction
            z0_h = -origin[2] / direction[2]
            domain = (
                float(selected_h.min() - z0_h - 0.1),
                float(selected_h.max() - z0_h + 0.2),
            )
        actions.append(
            SurfaceFit(
                id=name,
                label=name,
                operation="fit",
                selections=[name + "_selection"],
                kind=kind,
                axial_domain=domain,
                axis="axis"
                if fixture.mode == "mirror" and name in {"main", "other", "cap"}
                else None,
            )
        )
    if fixture.mode == "rotation":
        actions.extend(
            [
                Coaxial(
                    id="coaxial",
                    label="Coaxial",
                    operation="coaxial",
                    surface="other",
                    reference="main",
                ),
                Perpendicular(
                    id="perpendicular",
                    label="Perpendicular",
                    operation="perpendicular",
                    lateral="main",
                    plane="cap",
                ),
                RotationalSymmetry(
                    id="rotation",
                    label="Rotation",
                    operation="rotational_symmetry",
                    axis="main",
                    planes=list(fixture.members),
                ),
                JointFit(
                    id="joint",
                    label="Joint",
                    operation="joint_fit",
                    constraints=["coaxial", "perpendicular", "rotation"],
                ),
            ]
        )
    else:
        actions.extend(
            [
                MirrorSymmetry(
                    id="mirror",
                    label="Mirror",
                    operation="mirror_symmetry",
                    plane="clock",
                    surfaces=list(fixture.members),
                ),
                AxisSolve(
                    id="joint",
                    label="Joint",
                    operation="axis_solve",
                    axis="axis",
                    factors=["main", "other", "cap", "mirror"],
                ),
            ]
        )
    return Recipe(schema_version=2, nodes=actions, output="joint")


def graph_token(graph: FeatureGraph[ScanWorkspace]) -> str:
    return str(graph.snapshot()["token"])


def named_result(state: dict[str, object]) -> dict[str, Any]:
    return cast(dict[str, dict[str, Any]], state["results"])["joint"]


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize(
    "mode,fixed", [("rotation", False), ("mirror", False), ("mirror", True)]
)
def test_named_symmetry_imports_all_rows_without_raw_admission(
    kind: SurfaceKind, mode: str, fixed: bool
) -> None:
    fixture = raw_symmetry_fixture(
        kind, cast(Any, mode), oblique=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture, fixed=fixed)
    graph = FeatureGraph(fixture.workspace, recipe)
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe,
        {"joint"},
        describe_geometry_influence(nodes),
        selected_named_solves=True,
    )
    assert not set(fixture.truth).intersection(plan.order)
    assert {name + "_selection" for name in fixture.truth} <= set(plan.order)
    state = graph.evaluate(graph_token(graph))
    assert cast(dict[str, str], state["states"])["joint"] == "ready", state["errors"]
    result = named_result(state)
    check_rows(fixture, result)
    check_truth(fixture, result)
    assert set(result["surfaces"]) == set(fixture.truth)
    assert all(
        cast(dict[str, str], state["states"])[name] != "ready" for name in fixture.truth
    )
    assert not graph.needs_evaluation(graph_token(graph))
    assert not graph.readiness_snapshot(graph_token(graph))["required_failures"]
    saved = recipe.model_dump_json()
    assert (
        "residuals" not in saved
        and "analytic" not in saved
        and "initialization" not in saved
    )
    replay = FeatureGraph(fixture.workspace, Recipe.model_validate_json(saved))
    assert named_result(replay.evaluate(graph_token(replay))) == result
    assert Recipe.model_validate(graph.snapshot()["recipe"]).model_dump(
        mode="json"
    ) == json.loads(saved)


def test_joint_only_collinear_planes_keep_independent_raw_failure() -> None:
    fixture = raw_symmetry_fixture(
        "plane", "rotation", oblique=True, joint_only=True, support_kind="auto"
    )
    graph = FeatureGraph(fixture.workspace, symmetry_recipe(fixture))
    with pytest.raises(ValueError):
        _ = graph.evaluate(graph_token(graph), target="member_0")
    state = graph.snapshot()
    assert cast(dict[str, str], state["states"])["member_0"] == "failed"
    diagnostic = cast(dict[str, dict[str, Any]], state["diagnostics"])["member_0"]
    assert diagnostic["code"] == "insufficient-coverage"
    result = graph.evaluate(graph_token(graph))
    assert cast(dict[str, str], result["states"])["member_0"] == "failed"
    assert cast(dict[str, str], result["states"])["joint"] == "ready", result["errors"]
    check_rows(fixture, named_result(result))
    check_truth(fixture, named_result(result))
    assert not graph.needs_evaluation(graph_token(graph))
    assert not graph.readiness_snapshot(graph_token(graph))["required_failures"]


def test_free_axis_and_clock_huge_hint_does_not_gate_fresh_named_recovery() -> None:
    fixture = raw_symmetry_fixture(
        "cylinder", "mirror", oblique=True, support_kind="auto"
    )
    graph = FeatureGraph(fixture.workspace, symmetry_recipe(fixture, huge_hint=True))
    state = graph.evaluate(graph_token(graph))
    assert cast(dict[str, str], state["states"])["joint"] == "ready", state["errors"]
    check_rows(fixture, named_result(state))
    check_truth(fixture, named_result(state))


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_saved_unequal_local_support_survives_named_fit_and_replay(
    kind: SurfaceKind, mode: str
) -> None:
    fixture = raw_symmetry_fixture(
        kind, cast(Any, mode), oblique=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture, fixed=mode == "mirror", explicit_support=True)
    supports = dict(fixture.supports)
    for node in recipe.nodes:
        if isinstance(node, SurfaceFit) and node.axial_domain is not None:
            supports[node.id] = PlaneSupport((0, 0, 1), 0, node.axial_domain)
    oracle = replace(fixture, supports=supports)
    graph = FeatureGraph(fixture.workspace, recipe)
    state = graph.evaluate(graph_token(graph))
    check_rows(oracle, named_result(state))
    check_truth(oracle, named_result(state))
    intervals = [
        surface.axial_domain
        for surface in recipe.nodes
        if isinstance(surface, SurfaceFit) and surface.id in fixture.members
    ]
    assert len(set(intervals)) == len(fixture.members)
    replay = FeatureGraph(
        fixture.workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    assert named_result(replay.evaluate(graph_token(replay))) == named_result(state)


def test_exact_named_plane_context_consumers_do_not_require_raw_admission() -> None:
    fixture = raw_symmetry_fixture(
        "plane", "rotation", oblique=True, joint_only=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture)
    first = OutputReference(feature="member_0", output="plane", context="joint")
    second = OutputReference(feature="cap", output="plane", context="joint")
    recipe = recipe.model_copy(
        update={
            "nodes": [
                *recipe.nodes,
                PointDefinition(
                    id="origin",
                    label="Origin",
                    operation="point",
                    initial_coordinates=(0, 0, 0),
                ),
                FrameDefinition(
                    id="frame",
                    label="Frame",
                    operation="frame",
                    origin_point="origin",
                    primary_reference=first,
                    primary_output_axis="+X",
                    secondary_reference=second,
                    secondary_output_axis="+Y",
                ),
            ],
            "output": "frame",
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe,
        {"frame"},
        describe_geometry_influence(nodes),
        selected_named_solves=True,
    )
    assert "member_0" not in plan.order and "cap" not in plan.order
    state = graph.evaluate(graph_token(graph))
    assert cast(dict[str, str], state["states"])["frame"] == "ready", state["errors"]
    assert cast(dict[str, str], state["states"])["member_0"] != "ready"
    value = resolve_output(
        first, nodes, cast(dict[str, dict[str, Any]], state["results"])
    )
    np.testing.assert_array_equal(
        value["normal_display"],
        named_result(state)["surfaces"]["member_0"]["plane_equation"][:3],
    )
    assert not graph.needs_evaluation(graph_token(graph))


def test_raw_growth_and_all_actions_do_not_publish_named_results_as_raw() -> None:
    fixture = raw_symmetry_fixture(
        "plane", "rotation", oblique=True, joint_only=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture)
    recipe = recipe.model_copy(
        update={
            "nodes": [
                *recipe.nodes,
                Growth(
                    id="growth",
                    label="Growth",
                    operation="growth",
                    seed_fit="member_0",
                    distance=0.01,
                    angle_degrees=10,
                ),
            ]
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    with pytest.raises(ValueError):
        _ = graph.evaluate(graph_token(graph), all_actions=True)
    state = graph.snapshot()
    states = cast(dict[str, str], state["states"])
    assert states["member_0"] == "failed"
    assert states["growth"] == "blocked"
    assert states["joint"] == "ready", state["errors"]
    assert "member_0" not in cast(dict[str, Any], state["results"])
    assert not graph.readiness_snapshot(graph_token(graph))["required_failures"]
    assert graph.readiness_snapshot(graph_token(graph), targets=["growth"])[
        "required_failures"
    ]


def test_fixed_reversed_point_provider_and_plane_are_exact_hard_inputs() -> None:
    fixture = raw_symmetry_fixture("cone", "mirror", oblique=True, support_kind="auto")
    graph = FeatureGraph(
        fixture.workspace, symmetry_recipe(fixture, fixed=True, reverse_axis=True)
    )
    state = graph.evaluate(graph_token(graph))
    result = named_result(state)
    stored = cast(dict[str, dict[str, Any]], state["results"])
    assert result["axes"]["axis"]["point_display"] == stored["axis"]["point_display"]
    assert result["axes"]["axis"]["axis_display"] == stored["axis"]["axis_display"]
    assert (
        result["planes"]["clock"]["plane_equation"] == stored["clock"]["plane_equation"]
    )
    np.testing.assert_allclose(result["axis_display"], -fixture.common_axis, atol=1e-14)
    check_truth(fixture, result)


def test_invalid_hard_point_provider_blocks_named_recovery() -> None:
    fixture = raw_symmetry_fixture("cylinder", "mirror", support_kind="auto")
    recipe = symmetry_recipe(fixture, fixed=True)
    recipe = recipe.model_copy(
        update={
            "nodes": [
                node.model_copy(
                    update={"initial_coordinates": tuple(fixture.common_point)}
                )
                if node.id == "point1"
                else node
                for node in recipe.nodes
            ]
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    with pytest.raises(ValueError, match="coincide"):
        _ = graph.evaluate(graph_token(graph))
    state = graph.snapshot()
    assert cast(dict[str, str], state["states"])["axis"] == "failed"
    assert cast(dict[str, str], state["states"])["joint"] == "blocked"
    assert "joint" not in cast(dict[str, Any], state["results"])


def test_source_fit_axis_is_required_but_other_members_are_not_raw_initializers() -> (
    None
):
    fixture = raw_symmetry_fixture(
        "cylinder", "mirror", oblique=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture, fixed=True)
    provider = [
        Selection(
            id="provider_selection",
            label="Provider selection",
            operation="selection",
            source="scan",
            ids=fixture.ids["main"],
        ),
        SurfaceFit(
            id="provider",
            label="Provider",
            operation="fit",
            selections=["provider_selection"],
            kind="cylinder",
        ),
    ]
    actions: list[Feature] = []
    for node in recipe.nodes:
        if isinstance(node, AxisDefinition):
            actions.extend(provider)
            node = AxisDefinition(
                id=node.id,
                label=node.label,
                operation="axis",
                source_fit="provider",
                direction_reversed=True,
            )
        actions.append(node)
    # Author the fixed clock for the reversed provider orientation. Its tiny
    # raw-fit error is a hard provider value, not a joint initialization error.
    reversed_recipe = symmetry_recipe(fixture, fixed=True, reverse_axis=True)
    clock = next(
        node for node in reversed_recipe.nodes if isinstance(node, PlaneDefinition)
    )
    actions = [clock if isinstance(node, PlaneDefinition) else node for node in actions]
    recipe = recipe.model_copy(update={"nodes": actions})
    graph = FeatureGraph(fixture.workspace, recipe)
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe,
        {"joint"},
        describe_geometry_influence(nodes),
        selected_named_solves=True,
    )
    assert "provider" in plan.order
    assert not set(fixture.truth).intersection(plan.order)
    state = graph.evaluate(graph_token(graph))
    result = named_result(state)
    stored = cast(dict[str, dict[str, Any]], state["results"])
    assert result["axes"]["axis"]["axis_display"] == stored["axis"]["axis_display"]
    assert (
        result["planes"]["clock"]["plane_equation"] == stored["clock"]["plane_equation"]
    )
    check_rows(fixture, result)
    check_truth(fixture, result)


@pytest.mark.parametrize("fixed", [False, True])
def test_complete_mirror_parallel_radius_distance_cluster_is_lowered(
    fixed: bool,
) -> None:
    fixture = raw_symmetry_fixture(
        "plane", "mirror", oblique=True, tangent_mirror=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture, fixed=fixed)
    parallel = ParallelToPlane(
        id="parallel",
        label="Parallel",
        operation="parallel",
        surface="member_0",
        reference_plane="clock",
    )
    equality = EqualQuantities(
        id="equality",
        label="Equality",
        operation="equal",
        left=SurfaceRadius(measurement="radius", surface="main"),
        right=PlaneDistance(
            measurement="plane_distance", surface="member_0", reference_plane="clock"
        ),
    )
    joint = cast(AxisSolve, recipe.nodes[-1])
    recipe = recipe.model_copy(
        update={
            "nodes": [
                *recipe.nodes[:-1],
                parallel,
                equality,
                joint.model_copy(
                    update={"factors": [*joint.factors, "parallel", "equality"]}
                ),
            ]
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    state = graph.evaluate(graph_token(graph))
    result = named_result(state)
    check_rows(fixture, result)
    check_truth(fixture, result)
    radius = result["surfaces"]["main"]["analytic"]["radius"]
    plane = np.asarray(result["surfaces"]["member_0"]["plane_equation"])
    reference = np.asarray(result["planes"]["clock"]["plane_equation"])
    alignment = 1 if plane[:3] @ reference[:3] >= 0 else -1
    np.testing.assert_allclose(alignment * plane[:3], reference[:3], atol=1e-9)
    assert abs(alignment * plane[3] - reference[3]) == pytest.approx(radius, abs=1e-8)
    assert cast(dict[str, str], state["states"])["parallel"] != "ready"
    assert cast(dict[str, str], state["states"])["equality"] != "ready"


@pytest.mark.parametrize("edit", ["selection", "support"])
def test_named_saved_intent_edits_invalidate_recovery_without_saving_results(
    edit: str,
) -> None:
    fixture = raw_symmetry_fixture(
        "cylinder", "rotation", oblique=True, support_kind="auto"
    )
    recipe = symmetry_recipe(fixture, explicit_support=True)
    graph = FeatureGraph(fixture.workspace, recipe)
    before = named_result(graph.evaluate(graph_token(graph)))
    original_binding = before["initialization"]["recovery"]["binding_sha256"]
    changed: list[Feature] = []
    for node in recipe.nodes:
        if edit == "selection" and node.id == "member_0_selection":
            assert isinstance(node, Selection)
            node = node.model_copy(update={"ids": node.ids[:-1]})
        if edit == "support" and node.id == "member_0":
            assert isinstance(node, SurfaceFit) and node.axial_domain is not None
            node = node.model_copy(
                update={
                    "axial_domain": (node.axial_domain[0] + 0.01, node.axial_domain[1])
                }
            )
        changed.append(node)
    updated = recipe.model_copy(update={"nodes": changed})
    replaced = graph.replace(updated, graph_token(graph))
    assert cast(dict[str, str], replaced["states"])["joint"] == "stale"
    assert graph.needs_evaluation(graph_token(graph))
    result = named_result(graph.evaluate(graph_token(graph)))
    assert result["initialization"]["recovery"]["binding_sha256"] != original_binding
    assert (
        Recipe.model_validate(graph.snapshot()["recipe"]).model_dump_json()
        == updated.model_dump_json()
    )
    replay = FeatureGraph(
        fixture.workspace, Recipe.model_validate_json(updated.model_dump_json())
    )
    assert named_result(replay.evaluate(graph_token(replay))) == result


def test_named_overlapping_members_fail_with_selection_diagnostic() -> None:
    fixture = raw_symmetry_fixture("plane", "rotation", support_kind="auto")
    recipe = symmetry_recipe(fixture)
    recipe = recipe.model_copy(
        update={
            "nodes": [
                node.model_copy(update={"ids": fixture.ids["member_0"]})
                if node.id == "member_1_selection"
                else node
                for node in recipe.nodes
            ]
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    with pytest.raises(ValueError, match="overlap"):
        _ = graph.evaluate(graph_token(graph))
    state = graph.snapshot()
    diagnostic = cast(dict[str, dict[str, Any]], state["diagnostics"])["joint"]
    assert diagnostic["kind"] == "selection_overlap"
    assert diagnostic["ids"] == fixture.ids["member_0"]
    assert "joint" not in cast(dict[str, Any], state["results"])


def test_named_member_transferred_selection_retains_actual_prior_source_dependency() -> (
    None
):
    fixture = raw_symmetry_fixture(
        "cylinder", "rotation", oblique=True, support_kind="auto"
    )
    original = symmetry_recipe(fixture, explicit_support=True)
    base = original.model_copy(
        update={"nodes": original.nodes[:-4], "output": "member_0"}
    )
    reuse = reconcile_feature_reuse(
        base,
        "reuse",
        ReuseChanges(
            label="Reuse",
            fits=["member_0"],
            reference_selection="member_0_selection",
            target_selections=["member_1_selection"],
            tangent_margin=0.01,
            normal_margin=0.01,
            normal_angle_degrees=10,
        ),
        "symmetry-copy",
        create=True,
    )
    clone = next(
        node
        for node in reuse.recipe.nodes
        if isinstance(node, SurfaceFit) and node.managed_by == "reuse"
    )
    rotation = cast(RotationalSymmetry, original.nodes[-2])
    recipe = original.model_copy(
        update={
            "nodes": [
                *reuse.recipe.nodes,
                *original.nodes[-4:-2],
                rotation.model_copy(
                    update={"planes": ["member_0", clone.id, "member_2"]}
                ),
                original.nodes[-1],
            ]
        }
    )
    graph = FeatureGraph(fixture.workspace, recipe)
    nodes = {node.id: node for node in recipe.nodes}
    plan = compile_execution_plan(
        recipe,
        {"joint"},
        describe_geometry_influence(nodes),
        selected_named_solves=True,
    )
    assert "member_0" in plan.order  # Real source geometry prepares transfer.
    assert clone.id not in plan.order and "member_2" not in plan.order
    state = graph.evaluate(graph_token(graph))
    result = named_result(state)
    assert cast(dict[str, str], state["states"])["joint"] == "ready"
    fitted = result["surfaces"][clone.id]
    transferred = cast(dict[str, dict[str, Any]], state["results"])[clone.selections[0]]
    assert fitted["ids"] == transferred["ids"]
    assert set(fixture.ids["member_1"]) <= set(fitted["ids"])
    # The volume can also include unpainted satellite rows. Every such original
    # row, weight and the separately transferred hard support must survive.
    assert fitted["support_intent"] == transferred["support_intent"]
    ids = fitted["ids"]
    geometry = fitted["analytic"]
    relative = fixture.workspace.local[ids] - np.asarray(geometry["origin"])
    axis = np.asarray(geometry["axis"])
    radial = relative - (relative @ axis)[:, None] * axis
    residual = np.linalg.norm(radial, axis=1) - geometry["radius"]
    weights = fixture.workspace.data.weights[ids]
    np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-12)
    assert fitted["weighted_rms"] ** 2 == pytest.approx(
        float(weights @ residual**2 / weights.sum()), rel=1e-9
    )
    assert result["selected_area"][clone.id] == pytest.approx(float(weights.sum()))
    assert not graph.needs_evaluation(graph_token(graph))
    replay = FeatureGraph(
        fixture.workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    assert named_result(replay.evaluate(graph_token(replay))) == result
