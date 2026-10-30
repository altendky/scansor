"""Direction factors retain exact contexts and independent member positions."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    StaleGraph,
    SurfaceReference,
    describe_geometry_influence,
    surface_reference_kind,
)
from experiments.feature_inputs import REQUIREMENTS, OutputReference, resolve_output
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.nozzle_session import NozzleWorkspace
from experiments.relationship_inputs import normalize_endpoint
from experiments.scan_workspace import ScanCancelled
from experiments.surface_primitives import basis, revolution_geometry
from tests.circular_reuse_fixture import circular_fixture


def reference(
    feature: str, output: str = "axis", context: str | None = None
) -> dict[str, str]:
    return {"feature": feature, "output": output, "context": context or feature}


@pytest.fixture
def fixture() -> tuple[NozzleWorkspace, list[dict[str, Any]]]:
    workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
        }
    ]
    cursor = 0
    for name, kind, origin, tilt in [
        ("left", "cylinder", [-5.0, 0.0, 0.0], [0.035, -0.015, 1.0]),
        ("right", "cone", [5.0, 1.0, 0.0], [-0.02, 0.025, 1.0]),
        ("cap", "plane", [0.0, 0.0, 4.0], [0.01, 0.02, 1.0]),
        ("other", "plane", [0.0, 0.0, 8.0], [-0.02, -0.01, 1.0]),
    ]:
        direction = np.asarray(tilt)
        direction /= np.linalg.norm(direction)
        u, v = basis(direction)
        if kind == "plane":
            uv = np.array(
                [(a, b) for a in np.linspace(-2, 2, 5) for b in np.linspace(-2, 2, 5)]
            )
            points = np.asarray(origin) + uv[:, :1] * u + uv[:, 1:] * v
            normals = np.tile(direction, (len(points), 1))
        else:
            t = np.repeat(np.linspace(-2, 2, 5), 24)
            angle = np.tile(np.linspace(0, 2 * np.pi, 24, endpoint=False), 5)
            radial = np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v
            slope = 0.2 if kind == "cone" else 0.0
            points = (
                np.asarray(origin)
                + t[:, None] * direction
                + (2 + slope * t)[:, None] * radial
            )
            normals = radial - slope * direction
            normals /= np.linalg.norm(normals, axis=1)[:, None]
        ids = list(range(cursor, cursor + len(points)))
        cursor += len(points)
        workspace.local[ids] = points
        workspace.data.normals[ids] = normals @ workspace.frame.T
        workspace.data.weights[ids] = 1.0
        nodes.append(
            {
                "id": name + "_selection",
                "label": name + " observations",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            }
        )
        nodes.append(
            {
                "id": name,
                "label": name,
                "operation": "fit",
                "kind": kind,
                "selections": [name + "_selection"],
            }
        )
    return workspace, nodes


def graph_with(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], *extra: dict[str, Any]
) -> FeatureGraph:
    workspace, original = fixture
    nodes = deepcopy(original)
    nodes.extend({"label": node["id"], **node} for node in extra)
    return FeatureGraph(
        workspace, Recipe.model_validate({"nodes": nodes, "output": nodes[-1]["id"]})
    )


def aligned(
    identifier: str,
    left: str,
    right: str,
    left_output: str = "axis",
    right_output: str = "axis",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "operation": "direction_alignment",
        "directions": [reference(left, left_output), reference(right, right_output)],
        **extra,
    }


def evaluated(graph: FeatureGraph[Any], target: str | None = None) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), target=target)
    )


@pytest.mark.parametrize(
    "left,right,lo,ro",
    [
        ("left", "right", "axis", "axis"),
        ("left", "cap", "axis", "plane"),
        ("cap", "other", "plane", "plane"),
    ],
)
def test_generic_direction_alignment_uses_selected_observations_and_keeps_offsets(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    left: str,
    right: str,
    lo: str,
    ro: str,
) -> None:
    graph = graph_with(fixture, aligned("alignment", left, right, lo, ro))
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    values = state["results"]
    nodes = {
        node.id: node for node in Recipe.model_validate(graph.export_recipe()).nodes
    }
    a = resolve_output(
        OutputReference(feature=left, output=lo, context="alignment"),
        nodes,
        {"alignment": state["derived"]["alignment"]},
    )
    b = resolve_output(
        OutputReference(feature=right, output=ro, context="alignment"),
        nodes,
        {"alignment": state["derived"]["alignment"]},
    )
    av = np.asarray(a.get("axis_display", a.get("normal_display")))
    bv = np.asarray(b.get("axis_display", b.get("normal_display")))
    assert np.linalg.norm(np.cross(av, bv)) < 1e-7
    if left == "left" and right == "right":
        first, second = (
            revolution_geometry(values[left]),
            revolution_geometry(values[right]),
        )
        delta = np.asarray(second["origin"]) - np.asarray(first["origin"])
        assert np.linalg.norm(np.cross(delta, av)) > 8
        assert second["slope"] != 0
    if left == "cap":
        assert (
            abs(values[left]["plane_equation"][3] - values[right]["plane_equation"][3])
            > 3
        )


def test_named_joint_does_not_require_standalone_fit_success(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = graph_with(
        fixture,
        aligned("alignment", "left", "cap", "axis", "plane"),
        {"id": "joint", "operation": "joint_fit", "constraints": ["alignment"]},
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("standalone initializer must not run")

    monkeypatch.setattr("experiments.feature_graph.fit_seed", forbidden)
    state = evaluated(graph, "joint")
    assert state["states"]["joint"] == "ready", state["errors"]
    assert set(state["result"]["surfaces"]) == {"left", "cap"}
    assert state["states"]["left"] != "failed"


def test_fixed_geometry_common_reference_does_not_join_free_components(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    nodes.insert(
        1,
        {
            "id": "manual",
            "label": "Manual",
            "operation": "axis",
            "initial_parameters": [0.0, 0.0, 0.0, 0.0],
        },
    )
    nodes.append(
        {"id": "fixed", "label": "Fixed", "operation": "axis", "source_fit": "left"}
    )
    graph = graph_with(
        (workspace, nodes),
        aligned("a", "right", "fixed"),
        aligned("b", "cap", "fixed", "plane", "axis"),
    )
    by_id = {
        node.id: node for node in Recipe.model_validate(graph.export_recipe()).nodes
    }
    assert not normalize_endpoint(
        reference("fixed"), by_id, "relationship_direction"
    ).is_binding
    influence = describe_geometry_influence(by_id)
    assert influence.plane_components["a"][0] != influence.plane_components["b"][0]
    assert REQUIREMENTS["relationship_direction"]["property"] == "direction"


def test_actions_roundtrip_and_unsigned_only(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, aligned("alignment", "left", "right"))
    saved = graph.export_recipe()
    assert saved["nodes"][-1]["sense"] == "unsigned"
    assert (
        FeatureGraph(fixture[0], Recipe.model_validate(saved)).export_recipe() == saved
    )
    with pytest.raises(ValueError):
        _ = graph_with(fixture, aligned("bad", "left", "right", sense="directed"))


def test_plane_and_radius_factors_have_one_direction_component_publisher(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    next(node for node in nodes if node["id"] == "right")["kind"] = "cylinder"
    graph = graph_with(
        (workspace, nodes),
        aligned("alignment", "left", "cap", "axis", "plane"),
        {
            "id": "parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": [reference("cap", "plane"), reference("other", "plane")],
        },
        {"id": "radii", "operation": "equal_radii", "surfaces": ["left", "right"]},
    )
    state = evaluated(graph, "alignment")
    assert all(
        state["states"][key] == "ready" for key in ("alignment", "parallel", "radii")
    ), state["errors"]
    values = state["results"]
    assert (
        abs(
            revolution_geometry(values["left"])["radius"]
            - revolution_geometry(values["right"])["radius"]
        )
        < 1e-8
    )
    normal = np.asarray(values["cap"]["plane_equation"][:3])
    assert (
        np.linalg.norm(np.cross(normal, values["other"]["plane_equation"][:3])) < 1e-8
    )
    assert (
        np.linalg.norm(np.cross(normal, revolution_geometry(values["right"])["axis"]))
        > 1e-4
    )
    by_id = {
        node.id: node for node in Recipe.model_validate(graph.export_recipe()).nodes
    }
    influence = describe_geometry_influence(by_id)
    assert "radii" not in influence.providers
    providers = [
        provider
        for provider in influence.providers.values()
        if "left" in provider.aliases
    ]
    assert len(providers) == 1


def test_surface_reference_admits_only_declared_component_publications(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    next(node for node in nodes if node["id"] == "right")["kind"] = "cylinder"
    graph = graph_with(
        (workspace, nodes),
        aligned("alignment", "left", "cap", "axis", "plane"),
        {"id": "radii", "operation": "equal_radii", "surfaces": ["left", "right"]},
    )
    state = evaluated(graph, "alignment")
    assert state["states"]["alignment"] == "ready", state["errors"]
    by_id = {
        node.id: node for node in Recipe.model_validate(graph.export_recipe()).nodes
    }
    assert any(
        output["reference"] == reference("right", "surface", "alignment")
        for output in state["input_catalogue"]
    )
    assert (
        surface_reference_kind(
            SurfaceReference(feature="alignment", surface="right"), by_id
        )
        == "cylinder"
    )
    assert (
        surface_reference_kind(
            SurfaceReference(feature="alignment", surface="cap"), by_id
        )
        == "plane"
    )
    with pytest.raises(ValueError, match="explicit member surface"):
        _ = surface_reference_kind(
            SurfaceReference(feature="alignment", surface="other"), by_id
        )


def test_joint_accepts_explicit_member_factors_without_duplicate_observations(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        aligned("alignment", "left", "cap", "axis", "plane"),
        {
            "id": "joint",
            "operation": "joint_fit",
            "constraints": ["left", "cap", "alignment"],
        },
    )
    state = evaluated(graph)
    assert state["states"]["joint"] == "ready", state["errors"]
    assert set(state["result"]["surfaces"]) == {"left", "cap"}
    assert len(state["result"]["surfaces"]["left"]["ids"]) == 120


def test_cancel_and_stale_requests_publish_no_partial_direction_result(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, aligned("alignment", "left", "cap", "axis", "plane"))
    saved = graph.export_recipe()
    with pytest.raises((ScanCancelled,)):
        _ = graph.evaluate(str(graph.snapshot()["token"]), cancelled=lambda: True)
    assert "alignment" not in cast(dict[str, Any], graph.snapshot()["derived"])
    with pytest.raises(StaleGraph):
        _ = graph.replace(Recipe.model_validate(saved), "wrong-token")
    assert graph.export_recipe() == saved


def test_exact_solved_context_is_read_only_and_does_not_feed_back(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": [reference("cap", "plane"), reference("other", "plane")],
        },
        {
            "id": "alignment",
            "operation": "direction_alignment",
            "directions": [reference("left"), reference("cap", "plane", "parallel")],
        },
    )
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    nodes = {
        node.id: node for node in Recipe.model_validate(graph.export_recipe()).nodes
    }
    endpoint = normalize_endpoint(
        reference("cap", "plane", "parallel"), nodes, "relationship_direction"
    )
    assert not endpoint.is_binding
    assert "other" not in state["derived"]["alignment"]["surfaces"]


def test_ordered_point_axis_is_a_fixed_direction_reference(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {"id": "p", "operation": "point", "initial_coordinates": [0.0, 0.0, 0.0]},
        {"id": "q", "operation": "point", "initial_coordinates": [0.0, 0.0, 2.0]},
        {"id": "fixed", "operation": "axis", "source_points": ["p", "q"]},
        aligned("alignment", "left", "fixed"),
    )
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    assert abs(abs(revolution_geometry(state["results"]["left"])["axis"][2]) - 1) < 1e-8


def test_direction_only_clock_keeps_its_parent_axis_and_authored_offset(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "parent",
            "operation": "axis",
            "initial_parameters": [3.0, 4.0, 0.0, 0.0],
        },
        {
            "id": "clock",
            "operation": "reference_plane",
            "axis": "parent",
            "construction": "parallel_to_axis",
            "offset": 2.0,
            "initial_angle_degrees": 20.0,
        },
        {"id": "p", "operation": "point", "initial_coordinates": [0.0, 0.0, 0.0]},
        {"id": "q", "operation": "point", "initial_coordinates": [2.0, 0.0, 0.0]},
        {"id": "fixed", "operation": "axis", "source_points": ["p", "q"]},
        aligned("alignment", "clock", "fixed", "plane", "axis"),
    )
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    assert state["results"]["parent"]["axis_display"] == [0.0, 0.0, 1.0]
    assert state["results"]["parent"]["point_display"] == [3.0, 4.0, 0.0]
    plane = state["results"]["clock"]
    assert abs(abs(plane["plane_equation"][0]) - 1) < 1e-8
    assert (
        abs(
            plane["plane_equation"][3]
            - np.dot(plane["plane_equation"][:3], [3.0, 4.0, 0.0])
            - 2.0
        )
        < 1e-8
    )


def test_circular_reuse_clones_exact_direction_endpoints_without_allocating_on_reopen() -> (
    None
):
    _, workspace, recipe, circular, _ = circular_fixture()
    raw = recipe.model_dump()
    raw["nodes"].append(
        {"label": "Prototype directions", **aligned("alignment", "bore", "chamfer")}
    )
    recipe = Recipe.model_validate(raw)
    plan = reconcile_feature_reuse(
        recipe,
        "pattern",
        ReuseChanges.model_validate(
            {"label": "Pattern", "fits": ["bore", "chamfer"], "circular": circular}
        ),
        "direction-reuse-seed",
        create=True,
    )
    children = {
        node.managed_key: node
        for node in plan.recipe.nodes
        if node.managed_by == "pattern"
    }
    child = children["relationship/occurrence_1/alignment"]
    assert child.operation == "direction_alignment"
    assert child.directions == [
        OutputReference(feature=children["fit/occurrence_1/bore"].id, output="axis"),
        OutputReference(feature=children["fit/occurrence_1/chamfer"].id, output="axis"),
    ]
    graph = FeatureGraph(workspace, plan.recipe)
    saved = graph.export_recipe()
    reopened = FeatureGraph(workspace, Recipe.model_validate(saved))
    assert reopened.export_recipe() == saved
    state = evaluated(reopened, child.id)
    assert state["states"][child.id] == "ready", state["errors"]
    assert reopened.export_recipe() == saved


def test_unsupported_active_factor_overlap_is_explicit(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    nodes.insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [0.0, 0.0, 0.0, 0.0],
        },
    )
    next(node for node in nodes if node["id"] == "left")["axis"] = "axis"
    next(node for node in nodes if node["id"] == "cap")["axis"] = "axis"
    with pytest.raises(ValueError, match="unsupported active factor"):
        _ = graph_with(
            (workspace, nodes),
            aligned("alignment", "axis", "other", "axis", "plane"),
            {
                "id": "solve",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["left", "cap"],
            },
        )


def test_source_fit_axis_publishes_read_only_exact_upstream_component_geometry(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "derived_axis",
            "operation": "axis",
            "source_fit": "left",
            "direction_reversed": True,
        },
        aligned("upstream", "left", "right"),
        {
            "id": "downstream",
            "operation": "direction_alignment",
            "directions": [
                reference("cap", "plane"),
                reference("derived_axis", "axis", "upstream"),
            ],
        },
    )
    state = evaluated(graph)
    assert state["states"]["downstream"] == "ready", state["errors"]
    upstream = state["derived"]["upstream"]
    source = revolution_geometry(upstream["surfaces"]["left"])
    np.testing.assert_allclose(
        upstream["resolved"]["derived_axis"]["axis_display"],
        -np.asarray(source["axis"]),
        atol=1e-8,
    )
    assert "derived_axis" not in state["derived"]["downstream"]["resolved"]
    expected = np.asarray(upstream["resolved"]["derived_axis"]["axis_display"])
    actual = state["results"]["cap"]["plane_equation"][:3]
    assert np.linalg.norm(np.cross(expected, actual)) < 1e-8


def test_direction_edit_invalidates_its_members_without_changing_old_accepted_geometry(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, aligned("alignment", "left", "cap", "axis", "plane"))
    state = evaluated(graph)
    before = deepcopy(state["derived"]["alignment"])
    saved = graph.export_recipe()
    saved["nodes"][-1]["directions"] = [reference("right"), reference("cap", "plane")]
    _ = graph.replace(Recipe.model_validate(saved), str(state["token"]))
    snapshot = graph.snapshot()
    assert cast(dict[str, str], snapshot["states"])["alignment"] == "stale"
    result = evaluated(graph)
    assert result["states"]["alignment"] == "ready", result["errors"]
    assert set(result["derived"]["alignment"]["surfaces"]) == {"right", "cap"}
    assert set(before["surfaces"]) == {"left", "cap"}


def test_project_recovery_setting_invalidates_only_affected_geometry(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, aligned("alignment", "left", "cap", "axis", "plane"))
    initial = evaluated(graph)
    assert initial["states"]["alignment"] == "ready", initial["errors"]
    before = initial["derived"]["alignment"]["initialization"]
    assert before["geometric_equivalence_ppm"] == 1
    saved = graph.export_recipe()
    unchanged = graph.replace(Recipe.model_validate(saved), str(initial["token"]))
    assert unchanged["states"] == initial["states"]
    saved["fit_recovery"]["geometric_equivalence_ppm"] = 0.25
    pending = graph.replace(Recipe.model_validate(saved), str(unchanged["token"]))
    states = cast(dict[str, str], pending["states"])
    assert all(states[key] == "stale" for key in ("left", "cap", "alignment"))
    assert states["scan"] == "ready"
    assert states["right"] == initial["states"]["right"]
    assert "alignment" not in cast(dict[str, Any], pending["derived"])
    final = evaluated(graph)
    assert final["states"]["alignment"] == "ready", final["errors"]
    after = final["derived"]["alignment"]["initialization"]
    assert after["geometric_equivalence_ppm"] == 0.25
    assert after["binding_sha256"] != before["binding_sha256"]
    reopened = FeatureGraph(fixture[0], Recipe.model_validate(graph.export_recipe()))
    restored = evaluated(reopened)
    assert (
        restored["derived"]["alignment"]["initialization"]["binding_sha256"]
        == after["binding_sha256"]
    )


def test_project_recovery_setting_clears_settled_failure_and_diagnostics(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import selected_direction_fit
    from experiments.selected_direction_fit import DirectionFitFailure

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise DirectionFitFailure("ambiguous", "competing geometry")

    original = selected_direction_fit.fit_selected_direction_component
    monkeypatch.setattr(
        selected_direction_fit, "fit_selected_direction_component", fail
    )
    graph = graph_with(fixture, aligned("alignment", "left", "cap", "axis", "plane"))
    with pytest.raises(DirectionFitFailure, match="competing geometry"):
        _ = evaluated(graph)
    failed = cast(dict[str, Any], graph.snapshot())
    assert failed["states"]["alignment"] == "failed"
    assert failed["diagnostics"]["alignment"]["code"] == "ambiguous"
    saved = graph.export_recipe()
    saved["fit_recovery"]["geometric_equivalence_ppm"] = 2.0
    pending = graph.replace(Recipe.model_validate(saved), str(failed["token"]))
    assert cast(dict[str, str], pending["states"])["alignment"] == "stale"
    assert "alignment" not in cast(dict[str, Any], pending["diagnostics"])
    assert "alignment" not in cast(dict[str, Any], pending["errors"])
    monkeypatch.setattr(
        selected_direction_fit, "fit_selected_direction_component", original
    )
    final = evaluated(graph)
    assert final["states"]["alignment"] == "ready", final["errors"]
    assert (
        final["derived"]["alignment"]["initialization"]["geometric_equivalence_ppm"]
        == 2
    )


def test_existing_coaxial_full_line_binding_is_retained_in_direction_component(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    ids = next(node for node in nodes if node["id"] == "right_selection")["ids"]
    workspace.local[ids] -= np.array([10.0, 1.0, 0.0])
    nodes.insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [-5.0, 0.0, 0.0, 0.0],
        },
    )
    graph = graph_with(
        (workspace, nodes),
        {
            "id": "coaxial",
            "operation": "coaxial_group",
            "axis": "axis",
            "surfaces": ["left", "right"],
        },
        aligned("alignment", "axis", "cap", "axis", "plane"),
    )
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    assert state["states"]["coaxial"] == "ready"
    first = revolution_geometry(state["results"]["left"])
    second = revolution_geometry(state["results"]["right"])
    np.testing.assert_allclose(first["axis"], second["axis"], atol=1e-8)
    assert (
        np.linalg.norm(
            np.cross(
                np.asarray(first["origin"]) - np.asarray(second["origin"]),
                first["axis"],
            )
        )
        < 1e-8
    )
    assert set(state["derived"]["coaxial"]["surfaces"]) == {"left", "right"}


def test_bound_fit_direction_requires_its_published_geometry_context(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    nodes.insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [-5.0, 0.0, 0.0, 0.0],
        },
    )
    next(node for node in nodes if node["id"] == "left")["axis"] = "axis"
    with pytest.raises(ValueError, match="exact published geometry context"):
        _ = graph_with(
            (workspace, nodes), aligned("alignment", "left", "cap", "axis", "plane")
        )


def replace_cylinder_patch(
    workspace: NozzleWorkspace,
    nodes: list[dict[str, Any]],
    name: str,
    origin: list[float],
    direction: list[float],
    radius: float,
) -> None:
    ids = next(node for node in nodes if node["id"] == name + "_selection")["ids"]
    n = np.asarray(direction, dtype=float)
    n /= np.linalg.norm(n)
    u, v = basis(n)
    theta = np.tile(np.linspace(0, 2 * np.pi, 24, endpoint=False), len(ids) // 24)
    height = np.repeat(np.linspace(-2, 2, len(ids) // 24), 24)
    radial = np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
    workspace.local[ids] = np.asarray(origin) + height[:, None] * n + radius * radial
    workspace.data.normals[ids] = radial @ workspace.frame.T


@pytest.mark.parametrize("named", [False, True])
def test_active_source_fit_axis_is_one_alias_without_standalone_prerequisite(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    named: bool,
) -> None:
    workspace, nodes = fixture
    right = next(node for node in nodes if node["id"] == "right")
    right.update(kind="cylinder", axis="derived_axis")
    replace_cylinder_patch(
        workspace, nodes, "right", [-5, 0, 0], [0.035, -0.015, 1], 2.5
    )
    nodes.insert(
        nodes.index(right),
        {
            "id": "derived_axis",
            "label": "Derived axis",
            "operation": "axis",
            "source_fit": "left",
            "direction_reversed": True,
        },
    )
    extras = [
        aligned("alignment", "left", "cap", "axis", "plane"),
        {"id": "radii", "operation": "equal_radii", "surfaces": ["left", "right"]},
    ]
    if named:
        extras.append(
            {
                "id": "joint",
                "operation": "joint_fit",
                "constraints": ["alignment", "radii"],
            }
        )
    graph = graph_with((workspace, nodes), *extras)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "an active source-fit axis must not require standalone geometry"
        )

    monkeypatch.setattr("experiments.feature_graph.fit_seed", forbidden)
    state = evaluated(graph, "joint" if named else "alignment")
    assert state["states"]["joint" if named else "alignment"] == "ready", state[
        "errors"
    ]
    output = state["result"] if named else state["derived"]["alignment"]
    a, b = [revolution_geometry(output["surfaces"][key]) for key in ("left", "right")]
    assert (
        np.linalg.norm(np.cross(np.asarray(a["origin"]) - b["origin"], a["axis"]))
        < 1e-8
    )
    assert abs(abs(np.dot(a["axis"], b["axis"])) - 1) < 1e-8
    np.testing.assert_allclose(
        output["resolved"]["derived_axis"]["axis_display"],
        -np.asarray(a["axis"]),
        atol=1e-8,
    )
    saved = graph.export_recipe()
    reopened = FeatureGraph(workspace, Recipe.model_validate(saved))
    again = evaluated(reopened, "joint" if named else "alignment")
    assert again["states"]["joint" if named else "alignment"] == "ready", again[
        "errors"
    ]
    assert reopened.export_recipe() == saved


@pytest.mark.parametrize("named", [False, True])
def test_active_source_fit_point_shares_its_source_center_without_standalone_gate(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    named: bool,
) -> None:
    workspace, nodes = fixture
    for name, radius in (("right", 3.0), ("other", 2.6)):
        node = next(node for node in nodes if node["id"] == name)
        node["kind"] = "sphere"
        ids = next(node for node in nodes if node["id"] == name + "_selection")["ids"]
        z = np.linspace(-0.9, 0.9, len(ids))
        theta = np.arange(len(ids)) * 2.399963229728653
        normals = np.column_stack(
            (np.sqrt(1 - z * z) * np.cos(theta), np.sqrt(1 - z * z) * np.sin(theta), z)
        )
        workspace.local[ids] = np.array([5.0, 1, 0]) + radius * normals
        workspace.data.normals[ids] = normals @ workspace.frame.T
    other = next(node for node in nodes if node["id"] == "other")
    other["point"] = "derived_point"
    nodes.insert(
        nodes.index(other),
        {
            "id": "derived_point",
            "label": "Derived point",
            "operation": "point",
            "source_fit": "right",
        },
    )
    extras = [
        aligned("alignment", "left", "cap", "axis", "plane"),
        {
            "id": "radii",
            "operation": "equal_radii",
            "surfaces": ["left", "right", "other"],
        },
    ]
    if named:
        extras.append(
            {
                "id": "joint",
                "operation": "joint_fit",
                "constraints": ["alignment", "radii"],
            }
        )
    graph = graph_with((workspace, nodes), *extras)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "an active source-fit point must not require standalone geometry"
        )

    monkeypatch.setattr("experiments.feature_graph.fit_seed", forbidden)
    state = evaluated(graph, "joint" if named else "alignment")
    assert state["states"]["joint" if named else "alignment"] == "ready", state[
        "errors"
    ]
    output = state["result"] if named else state["derived"]["alignment"]
    center = output["surfaces"]["right"]["parameters"][:3]
    np.testing.assert_allclose(
        output["surfaces"]["other"]["parameters"][:3], center, atol=1e-10
    )
    np.testing.assert_allclose(
        output["resolved"]["derived_point"]["point_display"], center, atol=1e-10
    )
    assert set(output["surfaces"]) == {"left", "right", "other", "cap"}


@pytest.mark.parametrize("include_radius", [False, True])
def test_named_direction_joint_applies_only_its_authored_radius_factors(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], include_radius: bool
) -> None:
    workspace, nodes = fixture
    next(node for node in nodes if node["id"] == "right")["kind"] = "cylinder"
    replace_cylinder_patch(workspace, nodes, "right", [5, 1, 0], [-0.02, 0.025, 1], 3)
    constraints = ["alignment", "radii"] if include_radius else ["alignment"]
    graph = graph_with(
        (workspace, nodes),
        aligned("alignment", "left", "right"),
        {"id": "radii", "operation": "equal_radii", "surfaces": ["left", "right"]},
        {"id": "joint", "operation": "joint_fit", "constraints": constraints},
    )
    state = evaluated(graph, "joint")
    assert state["states"]["joint"] == "ready", state["errors"]
    output = state["result"]
    radii = [
        revolution_geometry(output["surfaces"][key])["radius"]
        for key in ("left", "right")
    ]
    assert ("radii" in output["resolved"]) is include_radius
    if include_radius:
        assert abs(radii[0] - radii[1]) < 1e-10
    else:
        assert radii[0] == pytest.approx(2, abs=0.01)
        assert radii[1] == pytest.approx(3, abs=0.01)
    assert graph.export_recipe()["nodes"][-1]["constraints"] == constraints


@pytest.mark.parametrize("reverse", [False, True])
def test_active_source_axis_perpendicular_plane_keeps_directed_normal_and_offset(
    reverse: bool,
) -> None:
    from tests.test_selected_direction_fit import lateral, plane, workspace_for

    direction = np.array([0.0, 0, 1])
    workspace, ids = workspace_for(
        {
            "side": lateral(direction, np.array([5.0, 1, 0]), 2),
            "cap": plane(direction, np.array([0.0, 0, 6])),
            "other": plane(direction, np.array([0.0, 0, 10])),
        }
    )
    assert workspace.descriptor is not None
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
            "descriptor": workspace.descriptor.model_dump(),
        }
    ]
    for name in ids:
        nodes.extend(
            [
                {
                    "id": name + "_selection",
                    "label": name + " selection",
                    "operation": "selection",
                    "source": "scan",
                    "ids": ids[name],
                },
                {
                    "id": name,
                    "label": name,
                    "operation": "fit",
                    "kind": "cylinder" if name == "side" else "plane",
                    "selections": [name + "_selection"],
                },
            ]
        )
    nodes.extend(
        [
            {
                "id": "derived_axis",
                "label": "Derived axis",
                "operation": "axis",
                "source_fit": "side",
                "direction_reversed": reverse,
            },
            {
                "id": "derived_plane",
                "label": "Derived plane",
                "operation": "reference_plane",
                "axis": "derived_axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 2,
            },
            {
                "label": "Alignment",
                **aligned("alignment", "side", "cap", "axis", "plane"),
            },
            {
                "id": "parallel",
                "label": "Parallel",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [
                    reference("derived_plane", "plane"),
                    reference("other", "plane"),
                ],
            },
        ]
    )
    graph = FeatureGraph(
        workspace, Recipe.model_validate({"nodes": nodes, "output": "alignment"})
    )
    state = evaluated(graph, "alignment")
    assert state["states"]["alignment"] == "ready", state["errors"]
    resolved = state["derived"]["alignment"]["resolved"]
    axis_value, plane_value = resolved["derived_axis"], resolved["derived_plane"]
    normal = np.asarray(plane_value["plane_equation"][:3])
    np.testing.assert_allclose(normal, axis_value["axis_display"], atol=1e-8)
    assert plane_value["plane_equation"][3] - normal @ np.asarray(
        axis_value["point_display"]
    ) == pytest.approx(2.0, abs=1e-8)
