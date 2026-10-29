"""Datum-line declarations retain member ownership and bounded solve contexts."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    SurfaceReference,
    surface_reference_kind,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import ScanWorkspace
from experiments.surface_primitives import revolution_geometry
from tests.test_coaxial_reference_graph import coaxial_parallel_recipe
from tests.test_relationship_reference_graph import evaluate
from tests.test_relationship_reference_graph import workspace as workspace


def group_recipe() -> dict[str, Any]:
    payload = coaxial_parallel_recipe()
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "parallel"]
    payload["output"] = "coaxial"
    return payload


@pytest.mark.parametrize("parallel", [False, True])
@pytest.mark.parametrize("duplicate_member", [False, True])
def test_named_joint_expands_group_once_and_keeps_selected_plane_factors(
    workspace: ScanWorkspace,
    parallel: bool,
    duplicate_member: bool,
) -> None:
    payload = coaxial_parallel_recipe() if parallel else group_recipe()
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    group = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    factors = ["coaxial"] + (["parallel"] if parallel else [])
    if duplicate_member:
        factors.append(group["surfaces"][0])
    payload["nodes"].append(
        {
            "id": "joint",
            "label": "Joint",
            "operation": "axis_solve",
            "axis": axis["id"],
            "factors": factors,
        }
    )
    payload["output"] = "joint"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    state = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), target="joint")
    )
    solved = state["results"]["joint"]
    expected = {
        node["id"]
        for node in payload["nodes"]
        if node["operation"] == "fit" and (parallel or node["kind"] == "cone")
    }
    assert set(solved["surfaces"]) == expected
    assert not state["errors"]
    direction = np.asarray(solved["axis_display"])
    point = np.asarray(solved["point_display"])
    assert abs(point @ direction) < 1e-9
    if parallel:
        for result in solved["surfaces"].values():
            if result["kind"] == "plane":
                assert abs(
                    np.asarray(result["plane_equation"][:3]) @ direction
                ) == pytest.approx(1, abs=1e-9)
        assert solved["reference_planes"]["base"]["offset"] == pytest.approx(
            0, abs=1e-9
        )
    nodes = {node.id: node for node in Recipe.model_validate(state["recipe"]).nodes}
    for key in expected:
        assert surface_reference_kind(
            SurfaceReference(feature="joint", surface=key), nodes
        ) in {"cone", "plane"}
    if parallel:
        assert (
            surface_reference_kind(
                SurfaceReference(feature="joint", surface="base"), nodes
            )
            == "plane"
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_fixed_source_fit_axis_can_include_its_own_source_without_a_provider_cycle(
    workspace: ScanWorkspace,
    reverse: bool,
) -> None:
    payload = group_recipe()
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    group = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    source = next(
        node for node in payload["nodes"] if node["id"] == group["surfaces"][0]
    )
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["operation"] != "axis" and node["id"] != "base"
    ]
    fixed = {
        "id": axis["id"],
        "label": "Fixed source axis",
        "operation": "axis",
        "source_fit": source["id"],
        "direction_reversed": reverse,
    }
    payload["nodes"].insert(payload["nodes"].index(source) + 1, fixed)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    state = evaluate(graph)
    assert not state["errors"]
    solved = state["results"]["coaxial"]
    raw_axis = np.asarray(state["derived"][axis["id"]]["axis_display"])
    raw_point = np.asarray(state["derived"][axis["id"]]["point_display"])
    np.testing.assert_array_equal(solved["axis_display"], raw_axis)
    np.testing.assert_array_equal(solved["point_display"], raw_point)
    source_geometry = revolution_geometry(state["derived"][source["id"]])
    assert float(raw_axis @ source_geometry["axis"]) == pytest.approx(
        -1 if reverse else 1
    )
    for member in group["surfaces"]:
        geometry = solved["surfaces"][member]["analytic"]
        assert abs(np.asarray(geometry["axis"]) @ raw_axis) == pytest.approx(
            1, abs=1e-10
        )
        np.testing.assert_allclose(
            np.cross(np.asarray(geometry["origin"]) - raw_point, raw_axis), 0, atol=1e-9
        )


def test_conflicting_individual_and_group_axis_bindings_have_clear_diagnostics(
    workspace: ScanWorkspace,
) -> None:
    payload = group_recipe()
    group = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    other = {
        "id": "other_axis",
        "label": "Other",
        "operation": "axis",
        "initial_parameters": [1, 0, 0, 0],
    }
    payload["nodes"].insert(2, other)
    member = next(
        node for node in payload["nodes"] if node["id"] == group["surfaces"][0]
    )
    member["axis"] = "other_axis"
    with pytest.raises(ValueError, match="conflicting datum axes"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))
    member["axis"] = None
    second = deepcopy(group)
    second.update(id="conflict", label="Conflicting group", axis="other_axis")
    payload["nodes"].append(second)
    with pytest.raises(ValueError, match="conflicting datum axes"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_named_plane_factor_does_not_merge_different_axis_lines(
    workspace: ScanWorkspace,
) -> None:
    payload = coaxial_parallel_recipe()
    other_axis = {
        "id": "other_axis",
        "label": "Other",
        "operation": "axis",
        "initial_parameters": [1, 0, 0, 0],
    }
    other_base = {
        "id": "other_base",
        "label": "Other base",
        "operation": "reference_plane",
        "axis": "other_axis",
        "construction": "perpendicular_to_axis",
        "initial_angle_degrees": None,
        "offset": 0,
    }
    payload["nodes"][2:2] = [other_axis, other_base]
    relation = next(node for node in payload["nodes"] if node["id"] == "parallel")
    relation["surfaces"].append("other_base")
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    payload["nodes"].append(
        {
            "id": "joint",
            "label": "Joint",
            "operation": "axis_solve",
            "axis": axis["id"],
            "factors": ["coaxial", "parallel"],
        }
    )
    with pytest.raises(ValueError, match="only the selected axis"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_coincident_datum_uses_related_plane_observations_for_full_placement(
    workspace: ScanWorkspace,
) -> None:
    payload = coaxial_parallel_recipe()
    relation = next(node for node in payload["nodes"] if node["id"] == "parallel")
    relation["relation"] = "coincident"
    relation["surfaces"] = relation["surfaces"][:2]
    state = evaluate(FeatureGraph(workspace, Recipe.model_validate(payload)))
    assert not state["errors"]
    selected_plane = relation["surfaces"][1]["feature"]
    datum = np.asarray(state["results"]["base"]["plane_equation"])
    plane = np.asarray(state["results"][selected_plane]["plane_equation"])
    np.testing.assert_allclose(datum, plane * np.sign(datum[:3] @ plane[:3]), atol=1e-8)
    assert abs(state["results"]["base"]["offset"]) > 0.1


def mixed_recipe(
    direction: list[float], reverse: bool = False
) -> tuple[ScanWorkspace, dict[str, Any]]:
    from tests.test_relationship_composition import (
        _generic_mixed_group,  # pyright: ignore[reportPrivateUsage]
    )

    scan, sides, _, _, _ = _generic_mixed_group(np.asarray(direction))
    assert scan.source_descriptor is not None
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": scan.source_sha256,
            "reference_sha256": scan.reference_sha256,
            "descriptor": scan.source_descriptor.model_dump(),
        },
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [20, -30, 0.2, -0.1],
            "direction_reversed": reverse,
        },
    ]
    for side in sides:
        nodes.extend(
            [
                {
                    "id": side.id + "_selection",
                    "label": side.id + " selection",
                    "operation": "selection",
                    "source": "scan",
                    "ids": side.ids,
                },
                {
                    "id": side.id,
                    "label": side.id,
                    "operation": "fit",
                    "kind": side.kind,
                    "selections": [side.id + "_selection"],
                },
            ]
        )
    nodes.append(
        {
            "id": "coaxial",
            "label": "Coaxial",
            "operation": "coaxial_group",
            "axis": "axis",
            "surfaces": [side.id for side in sides],
        }
    )
    return scan, {"schema_version": 2, "nodes": nodes, "output": "coaxial"}


@pytest.mark.parametrize("fixed", [False, True])
def test_one_target_coaxial_group_refines_only_its_declared_axis_freedom(
    fixed: bool,
) -> None:
    scan, payload = mixed_recipe([1.0, 0.0, 0.0])
    relation = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    member_id = relation["surfaces"][0]
    relation["surfaces"] = [member_id]
    member = next(node for node in payload["nodes"] if node["id"] == member_id)
    kept = {"scan", "axis", "coaxial", member_id, *member["selections"]}
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] in kept]
    if fixed:
        payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "axis"]
        payload["nodes"].insert(
            payload["nodes"].index(member) + 1,
            {
                "id": "axis",
                "label": "Fixed source axis",
                "operation": "axis",
                "source_fit": member_id,
            },
        )
    graph = FeatureGraph(scan, Recipe.model_validate(payload))
    saved = graph.export_recipe()
    state = evaluate(graph)
    assert not state["errors"]
    assert state["states"]["coaxial"] == "ready"
    assert graph.export_recipe() == saved
    solved = state["results"]["coaxial"]
    assert set(solved["surfaces"]) == {member_id}
    result = revolution_geometry(solved["surfaces"][member_id])
    direction, point = (
        np.asarray(solved["axis_display"]),
        np.asarray(solved["point_display"]),
    )
    assert np.linalg.norm(np.cross(result["axis"], direction)) < 1e-8
    assert (
        np.linalg.norm(np.cross(np.asarray(result["origin"]) - point, direction)) < 1e-8
    )
    if fixed:
        np.testing.assert_array_equal(
            direction, state["results"]["axis"]["axis_display"]
        )
        np.testing.assert_array_equal(point, state["results"]["axis"]["point_display"])
    else:
        assert abs(direction[0]) == pytest.approx(1, abs=1e-8)
        assert solved["fit"]["weighted_rms"] < 1e-7
    reopened = FeatureGraph(scan, Recipe.model_validate(saved))
    again = evaluate(reopened)
    assert not again["errors"]
    np.testing.assert_allclose(
        again["results"]["coaxial"]["axis_display"], direction, atol=1e-8
    )


def test_named_axis_joint_retains_two_observation_minimum_for_single_target_group() -> (
    None
):
    scan, payload = mixed_recipe([1.0, 0.0, 0.0])
    relation = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    relation["surfaces"] = relation["surfaces"][:1]
    payload["nodes"].append(
        {
            "id": "joint",
            "label": "Joint",
            "operation": "axis_solve",
            "axis": "axis",
            "factors": ["coaxial"],
        }
    )
    payload["output"] = "joint"
    with pytest.raises(ValueError, match="at least two distinct fit observations"):
        _ = FeatureGraph(scan, Recipe.model_validate(payload))


@pytest.mark.parametrize("reverse", [False, True])
def test_mixed_free_group_keeps_independent_shapes_and_directed_publication(
    reverse: bool,
) -> None:
    direction = [1.0, 0.0, 0.0]
    scan, payload = mixed_recipe(direction, reverse)
    state = evaluate(FeatureGraph(scan, Recipe.model_validate(payload)))
    assert not state["errors"]
    solved = state["results"]["coaxial"]
    np.testing.assert_allclose(
        solved["axis_display"], [-1 if reverse else 1, 0, 0], atol=1e-8
    )
    assert solved["fit"]["weighted_rms"] < 1e-7
    shapes = {
        key: revolution_geometry(value) for key, value in solved["surfaces"].items()
    }
    assert shapes["cylinder"]["radius"] == pytest.approx(2.5, abs=1e-7)
    assert abs(shapes["cone"]["slope"]) == pytest.approx(0.5, abs=1e-7)
    assert abs(shapes["other_cone"]["slope"]) == pytest.approx(0.1, abs=1e-7)


def test_legacy_example_named_group_alone_uses_original_rows_and_fixed_axis() -> None:
    scan = NozzleWorkspace(
        Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
    )
    ids = scan.default.lateral_ids
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": scan.source_sha256,
            "reference_sha256": scan.reference_sha256,
        }
    ]
    for index, kind in enumerate(["cone", "cylinder"]):
        nodes.extend(
            [
                {
                    "id": f"selection{index}",
                    "label": f"Selection {index}",
                    "operation": "selection",
                    "source": "scan",
                    "ids": ids[index::2],
                },
                {
                    "id": f"fit{index}",
                    "label": kind,
                    "operation": "fit",
                    "kind": kind,
                    "selections": [f"selection{index}"],
                },
            ]
        )
    nodes.insert(
        3,
        {
            "id": "axis",
            "label": "Fixed axis",
            "operation": "axis",
            "source_fit": "fit0",
        },
    )
    nodes.extend(
        [
            {
                "id": "coaxial",
                "label": "Coaxial",
                "operation": "coaxial_group",
                "axis": "axis",
                "surfaces": ["fit0", "fit1"],
            },
            {
                "id": "joint",
                "label": "Joint",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["coaxial"],
            },
        ]
    )
    graph = FeatureGraph(
        scan,
        Recipe.model_validate({"schema_version": 2, "nodes": nodes, "output": "joint"}),
    )
    state = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert not state["errors"]
    solved = state["results"]["joint"]
    assert set(solved["surfaces"]) == {"fit0", "fit1"}
    np.testing.assert_allclose(
        solved["axis_display"],
        state["derived"]["axis"]["axis_display"],
        rtol=0,
        atol=3e-16,
    )
    np.testing.assert_array_equal(
        solved["point_display"], state["derived"]["axis"]["point_display"]
    )


def test_complete_bounded_group_does_not_require_standalone_member_success(
    workspace: ScanWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.feature_graph as module

    def reject_standalone(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("owned observations must reach the joint solve first")

    monkeypatch.setattr(module, "fit_seed", reject_standalone)
    state = evaluate(
        FeatureGraph(workspace, Recipe.model_validate(coaxial_parallel_recipe()))
    )
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}


def test_legacy_joint_cannot_compete_for_group_members(
    workspace: ScanWorkspace,
) -> None:
    payload = group_recipe()
    members = next(node for node in payload["nodes"] if node["id"] == "coaxial")[
        "surfaces"
    ]
    plane = next(node["id"] for node in payload["nodes"] if node.get("kind") == "plane")
    payload["nodes"].extend(
        [
            {
                "id": "old_coaxial",
                "label": "Old coaxial",
                "operation": "coaxial",
                "surface": members[0],
                "reference": members[1],
            },
            {
                "id": "old_perpendicular",
                "label": "Old perpendicular",
                "operation": "perpendicular",
                "lateral": members[0],
                "plane": plane,
            },
            {
                "id": "old_joint",
                "label": "Old joint",
                "operation": "joint_fit",
                "constraints": ["old_coaxial", "old_perpendicular"],
            },
        ]
    )
    with pytest.raises(ValueError, match="already belong to a legacy joint"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_named_joint_cannot_drop_unselected_hard_group_members(
    workspace: ScanWorkspace,
) -> None:
    payload = group_recipe()
    members = next(node for node in payload["nodes"] if node["id"] == "coaxial")[
        "surfaces"
    ]
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    plane = next(node for node in payload["nodes"] if node.get("kind") == "plane")
    plane["axis"] = axis["id"]
    payload["nodes"].append(
        {
            "id": "joint",
            "label": "Joint",
            "operation": "axis_solve",
            "axis": axis["id"],
            "factors": [members[0], plane["id"]],
        }
    )
    with pytest.raises(ValueError, match="complete coaxial group"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_retargeting_a_group_invalidates_old_members_without_rewriting_fits() -> None:
    scan, payload = mixed_recipe([0.2, -0.7, 0.3])
    graph = FeatureGraph(scan, Recipe.model_validate(payload))
    before = evaluate(graph)
    axis = deepcopy(next(node for node in payload["nodes"] if node["id"] == "axis"))
    axis.update(
        id="replacement_axis",
        label="Replacement axis",
        initial_parameters=[-50, 60, -0.1, 0.4],
    )
    payload["nodes"].insert(2, axis)
    group = next(node for node in payload["nodes"] if node["id"] == "coaxial")
    group["axis"] = axis["id"]
    edited = graph.replace(Recipe.model_validate(payload), str(before["token"]))
    assert cast(dict[str, str], edited["states"])["cone"] == "stale"
    after = evaluate(graph)
    assert not after["errors"]
    assert after["recipe"] == edited["recipe"]
    for key in group["surfaces"]:
        np.testing.assert_allclose(
            revolution_geometry(before["results"][key])["axis"],
            revolution_geometry(after["results"][key])["axis"],
            atol=1e-8,
        )


def test_new_group_adapter_keeps_cancellation_and_attempt_budgets(
    workspace: ScanWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.feature_graph as module
    from experiments.fit_initialization import RecoveryPolicy
    from experiments.fit_solver import AttemptLimits, FitCancelled
    from experiments.selected_symmetry_fit import (
        SelectedSymmetryGroup,
        SymmetryFitFailure,
        fit_selected_symmetry_group,
    )

    groups: list[SelectedSymmetryGroup] = []

    def captured(scan: Any, group: SelectedSymmetryGroup) -> dict[str, Any]:
        groups.append(group)
        return fit_selected_symmetry_group(scan, group)

    monkeypatch.setattr(module, "fit_selected_symmetry_group", captured)
    _ = evaluate(
        FeatureGraph(workspace, Recipe.model_validate(coaxial_parallel_recipe()))
    )
    group = groups[0]
    with pytest.raises(FitCancelled):
        _ = fit_selected_symmetry_group(workspace, group, cancelled=lambda: True)
    with pytest.raises(SymmetryFitFailure) as caught:
        _ = fit_selected_symmetry_group(
            workspace,
            group,
            policy=RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1)),
        )
    recovery = caught.value.diagnostic["recovery"]
    assert recovery["selected"] is None
    assert all(attempt["evaluations"] <= 1 for attempt in recovery["attempts"])


def test_legacy_plane_only_component_does_not_activate_manual_axis_dofs(
    workspace: ScanWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.fit_relationship_component as module

    payload = coaxial_parallel_recipe()
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["id"] not in {"base", "coaxial"} and node.get("kind") != "cone"
    ]
    axis = next(node for node in payload["nodes"] if node["operation"] == "axis")
    relation = next(node for node in payload["nodes"] if node["id"] == "parallel")
    relation["surfaces"] = [ref["feature"] for ref in relation["surfaces"][1:]]
    for node in payload["nodes"]:
        if node["operation"] == "fit":
            node["axis"] = axis["id"]
    original = module.fit_relationship_component
    roles: list[bool] = []

    def captured(*args: Any, **kwargs: Any) -> dict[str, Any]:
        roles.extend(value.free for value in args[1].values())
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "fit_relationship_component", captured)
    state = evaluate(FeatureGraph(workspace, Recipe.model_validate(payload)))
    assert not state["errors"]
    assert roles and not any(roles)
