"""Independent plane datums own orientation without aliasing observed offsets."""

from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.feature_graph import (
    PlaneDefinition,
    Recipe,
    compile_execution_plan,
    describe_geometry_influence,
    reference_plane_result,
)
from experiments.feature_inputs import (
    OutputReference,
    declared_output_specs,
    resolve_output,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import ScanCancelled
from experiments.surface_primitives import revolution_geometry
from tests.test_direction_graph import aligned, evaluated, graph_with
from tests.test_direction_graph import fixture as fixture


def independent(identifier: str = "datum", **changes: Any) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": identifier,
        "operation": "reference_plane",
        "construction": "independent",
        "initial_normal": [0, 0, 2],
        "initial_angle_degrees": None,
        "offset": 30,
        **changes,
    }


def test_schema_geometry_and_own_property_freedoms() -> None:
    node = PlaneDefinition.model_validate(independent(initial_normal=[0, 3e300, 0]))
    result = reference_plane_result(None, node)
    np.testing.assert_allclose(result["plane_equation"], [0, 1, 0, 30])
    np.testing.assert_allclose(result["point_display"], [0, 30, 0])
    properties = declared_output_specs(node)[0]["properties"]
    assert properties["direction"]["parameter_owner"]["quantities"] == {
        "orientation": 2
    }
    assert properties["plane"]["parameter_owner"]["quantities"] == {
        "orientation": 2,
        "offset": 1,
    }
    assert (
        PlaneDefinition.model_validate(
            {
                "id": "legacy",
                "label": "legacy",
                "operation": "reference_plane",
                "axis": "a",
            }
        ).construction
        == "contains_axis"
    )
    omitted = independent()
    del omitted["initial_angle_degrees"]
    assert PlaneDefinition.model_validate(omitted).initial_angle_degrees is None


@pytest.mark.parametrize(
    "changes",
    [
        {"initial_normal": [0, 0, 0]},
        {"initial_normal": [0, float("inf"), 0]},
        {"initial_normal": None},
        {"axis": "a"},
        {"initial_angle_degrees": 0},
    ],
)
def test_invalid_independent_initializers(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ = PlaneDefinition.model_validate(independent(**changes))


def test_automatic_normal_binding_refines_orientation_and_keeps_world_offset(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    # Datums precede their consuming fits, just as they do in authored recipes.
    nodes.insert(1, independent(initial_normal=[0.1, -0.2, 1]))
    for node in nodes:
        if node["id"] in {"cap", "other"}:
            node["reference_plane"] = "datum"
    graph = graph_with((workspace, nodes))
    state = evaluated(graph, "cap")
    assert state["states"]["cap"] == "ready", state["errors"]
    result = state["results"]
    normal = np.asarray(result["datum"]["normal_display"])
    assert np.linalg.norm(np.cross(normal, [0.1, -0.2, 1])) > 0.1
    assert result["datum"]["plane_equation"][3] == pytest.approx(30)
    assert (
        abs(result["cap"]["plane_equation"][3] - result["other"]["plane_equation"][3])
        > 3
    )
    context = state["derived"]["@plane/datum"]
    for key in ("cap", "other"):
        assert (
            np.linalg.norm(
                np.cross(normal, context["resolved"][key]["plane_equation"][:3])
            )
            < 1e-7
        )
    recipe = Recipe.model_validate(graph.export_recipe())
    by_id = {node.id: node for node in recipe.nodes}
    exact = resolve_output(
        OutputReference(feature="datum", output="plane", context="@plane/datum"),
        by_id,
        state["derived"],
    )
    np.testing.assert_allclose(
        exact["plane_equation"], result["datum"]["plane_equation"]
    )
    influence = describe_geometry_influence(by_id)
    assert list(influence.providers) == ["@plane/datum"]
    plan = compile_execution_plan(
        recipe, {"cap"}, influence, selected_named_solves=False
    )
    assert (
        plan.order.index("datum")
        < plan.order.index("@plane/datum")
        < plan.order.index("cap")
    )


@pytest.mark.parametrize("relation", ["parallel", "coincident"])
def test_plane_relationship_to_observations_uses_own_offset(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    relation: str,
) -> None:
    graph = graph_with(
        fixture,
        independent(initial_normal=[0.2, 0.1, 1]),
        {
            "id": "relation",
            "operation": "plane_relationship",
            "relation": relation,
            "surfaces": ["cap", "datum"],
        },
    )
    state = evaluated(graph)
    assert state["states"]["relation"] == "ready", state["errors"]
    result = state["results"]
    datum, cap = result["datum"]["plane_equation"], result["cap"]["plane_equation"]
    assert result["datum"]["offset"] == pytest.approx(datum[3])
    assert np.linalg.norm(np.cross(datum[:3], cap[:3])) < 1e-7
    if relation == "parallel":
        assert datum[3] == pytest.approx(30)
        assert abs(cap[3]) < 5
    else:
        assert datum[3] == pytest.approx(cap[3], abs=1e-7)
        assert abs(datum[3]) < 5


def test_cylinder_direction_alignment_preserves_unused_datum_offset(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture, independent(), aligned("alignment", "left", "datum", "axis", "plane")
    )
    state = evaluated(graph)
    assert state["states"]["alignment"] == "ready", state["errors"]
    assert state["results"]["datum"]["plane_equation"][3] == pytest.approx(30)
    assert (
        np.linalg.norm(
            np.cross(
                state["results"]["datum"]["normal_display"],
                revolution_geometry(state["results"]["left"])["axis"],
            )
        )
        < 1e-7
    )


def normal_bound_graph(fixture: tuple[NozzleWorkspace, list[dict[str, Any]]]) -> Any:
    workspace, nodes = fixture
    nodes.insert(1, independent(initial_normal=[0.1, -0.2, 1]))
    next(node for node in nodes if node["id"] == "cap")["reference_plane"] = "datum"
    return graph_with((workspace, nodes))


def test_exact_automatic_context_is_fixed_without_feedback_and_invalidates(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = normal_bound_graph(fixture)
    first = evaluated(graph, "cap")
    saved = graph.export_recipe()
    saved["nodes"].extend(
        [
            independent("follower", initial_normal=[0, 1, 0], offset=7),
            {
                "id": "alignment",
                "label": "alignment",
                "operation": "direction_alignment",
                "directions": [
                    {"feature": "cap", "output": "plane", "context": "@plane/datum"},
                    {"feature": "follower", "output": "plane", "context": "follower"},
                ],
            },
        ]
    )
    _ = graph.replace(Recipe.model_validate(saved), str(first["token"]))
    state = evaluated(graph, "alignment")
    assert state["states"]["alignment"] == "ready", state["errors"]
    np.testing.assert_allclose(
        first["results"]["cap"]["plane_equation"],
        state["results"]["cap"]["plane_equation"],
    )
    assert abs(
        np.dot(
            state["results"]["follower"]["normal_display"],
            state["results"]["datum"]["normal_display"],
        )
    ) == pytest.approx(1)
    assert state["results"]["follower"]["plane_equation"][3] == pytest.approx(7)
    saved = graph.export_recipe()
    next(node for node in saved["nodes"] if node["id"] == "datum")["offset"] = -9
    edited = graph.replace(Recipe.model_validate(saved), str(state["token"]))
    assert edited["states"]["alignment"] == "stale"
    assert "@plane/datum" not in edited["derived"]
    again = evaluated(graph, "alignment")
    assert again["results"]["datum"]["plane_equation"][3] == pytest.approx(-9)
    assert again["results"]["follower"]["plane_equation"][3] == pytest.approx(7)
    assert graph.export_recipe() == saved


def test_exact_fixed_plane_coincidence_publishes_solved_offset_metadata(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = normal_bound_graph(fixture)
    first = evaluated(graph, "cap")
    saved = graph.export_recipe()
    saved["nodes"].extend(
        [
            independent("follower", initial_normal=[0, 1, 0], offset=30),
            {
                "id": "coincidence",
                "label": "coincidence",
                "operation": "plane_relationship",
                "relation": "coincident",
                "surfaces": [
                    {"feature": "cap", "output": "plane", "context": "@plane/datum"},
                    {"feature": "follower", "output": "plane", "context": "follower"},
                ],
            },
        ]
    )
    _ = graph.replace(Recipe.model_validate(saved), str(first["token"]))
    saved = graph.export_recipe()
    nodes = {node.id: node for node in Recipe.model_validate(saved).nodes}
    component = describe_geometry_influence(nodes).plane_components["coincidence"]
    assert not component[1], (
        "the exact published plane is a fixed read, not raw evidence"
    )
    state = evaluated(graph, "coincidence")
    assert state["states"]["coincidence"] == "ready", state["errors"]
    follower = state["results"]["follower"]
    assert follower["offset"] == pytest.approx(follower["plane_equation"][3])
    np.testing.assert_allclose(
        follower["plane_equation"], first["results"]["cap"]["plane_equation"], atol=1e-8
    )
    assert abs(follower["offset"] - 30) > 20
    assert state["derived"]["follower"]["offset"] == 30
    assert graph.export_recipe() == saved


def test_automatic_component_cancellation_publishes_nothing_and_retries(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = normal_bound_graph(fixture)
    saved = graph.export_recipe()
    with pytest.raises(ScanCancelled):
        _ = graph.evaluate(
            str(graph.snapshot()["token"]), target="cap", cancelled=lambda: True
        )
    assert "@plane/datum" not in graph.snapshot()["derived"]
    assert graph.export_recipe() == saved
    state = evaluated(graph, "cap")
    assert state["states"]["cap"] == "ready", state["errors"]


def test_legacy_constructors_require_axis_and_reject_independent_normal() -> None:
    for extra in ({}, {"axis": "axis", "initial_normal": [0, 0, 1]}):
        with pytest.raises(ValidationError):
            _ = PlaneDefinition.model_validate(
                {
                    "id": "legacy",
                    "label": "legacy",
                    "operation": "reference_plane",
                    **extra,
                }
            )


def test_independent_plane_supplies_frame_normal_without_an_axis(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        independent(),
        independent("secondary", initial_normal=[1, 0, 0]),
        {"id": "origin", "operation": "point", "initial_coordinates": [1, 2, 3]},
        {
            "id": "frame",
            "operation": "frame",
            "origin_point": "origin",
            "primary_reference": "datum",
            "primary_output_axis": "+Z",
            "secondary_reference": "secondary",
            "secondary_output_axis": "+X",
        },
    )
    state = evaluated(graph)
    np.testing.assert_allclose(state["derived"]["frame"]["rotation"], np.eye(3))
    np.testing.assert_allclose(state["derived"]["frame"]["origin_display"], [1, 2, 3])


def test_legacy_mirror_rejects_independent_construction_explicitly(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    with pytest.raises(ValueError, match="containing that axis"):
        _ = graph_with(
            fixture,
            independent(),
            {
                "id": "mirror",
                "operation": "mirror_symmetry",
                "plane": "datum",
                "surfaces": ["cap", "other"],
            },
        )
