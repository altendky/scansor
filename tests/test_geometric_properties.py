"""Typed projections share output identity without widening parameter bindings."""

from typing import Any

import numpy as np
import pytest

from experiments.feature_graph import AxisDefinition, Recipe
from experiments.feature_inputs import (
    OutputReference,
    declared_output_specs,
    input_choices,
    output_catalogue,
    resolve_output,
    resolve_property,
    validate_output_reference,
)


@pytest.fixture
def recipe() -> Recipe:
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "operation": "source",
            "source_sha256": "a",
            "reference_sha256": "b",
        },
        {"id": "selection", "operation": "selection", "source": "scan", "ids": [0, 1]},
        {"id": "point", "operation": "point", "initial_coordinates": [0, 0, 0]},
        {"id": "axis", "operation": "axis", "initial_parameters": [0, 0, 0, 0]},
        {"id": "datum", "operation": "reference_plane", "axis": "axis"},
        {
            "id": "perpendicular",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
        },
        *[
            {"id": kind, "operation": "fit", "kind": kind, "selections": ["selection"]}
            for kind in ("cylinder", "cone", "plane", "sphere")
        ],
        {
            "id": "bound",
            "operation": "fit",
            "kind": "cylinder",
            "axis": "axis",
            "selections": ["selection"],
        },
        {
            "id": "bound_plane",
            "operation": "fit",
            "kind": "plane",
            "reference_plane": "datum",
            "selections": ["selection"],
        },
        {
            "id": "bound_sphere",
            "operation": "fit",
            "kind": "sphere",
            "point": "point",
            "selections": ["selection"],
        },
        {"id": "initialized", "operation": "axis", "source_fit": "cylinder"},
        {
            "id": "other",
            "operation": "fit",
            "kind": "plane",
            "selections": ["selection"],
        },
        {
            "id": "parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["plane", "other"],
        },
    ]
    return Recipe.model_validate(
        {
            "nodes": [{**node, "label": node["id"]} for node in nodes],
            "output": "parallel",
        }
    )


@pytest.fixture
def values() -> dict[str, dict[str, Any]]:
    return {
        "axis": {"axis_display": [0, 0, -4], "point_display": [3, 4, 5]},
        "datum": {"plane_equation": [0, 2, 0, 6]},
        "plane": {"parameters": [0, 0, 2, 6]},
        "cylinder": {"kind": "cylinder", "parameters": [3, 4, 0, 0, 2, 0, 0]},
        "cone": {"kind": "cone", "parameters": [3, 4, 0, 0, 2, 0, 0.3]},
        "sphere": {"parameters": [1, 2, 3, 4]},
        "point": {"point_display": [1, 2, 3]},
        "parallel": {"surfaces": {"plane": {"parameters": [0, 1, 0, 9]}}},
    }


def descriptor(
    recipe: Recipe, feature: str, output: str, context: str | None = None
) -> dict[str, Any]:
    return next(
        item
        for item in output_catalogue(recipe)
        if item["reference"]
        == OutputReference(feature=feature, output=output, context=context).model_dump()
    )


@pytest.mark.parametrize(
    "feature,output,expected,sense",
    [
        ("axis", "axis", [0, 0, -1], "directed"),
        ("cylinder", "axis", [0, 0, 1], "unsigned"),
        ("cone", "axis", [0, 0, 1], "unsigned"),
        ("plane", "plane", [0, 0, 1], "signed_normal"),
        ("datum", "plane", [0, 1, 0], "signed_normal"),
    ],
)
def test_one_direction_contract_projects_supported_geometry(
    recipe: Recipe,
    values: dict[str, dict[str, Any]],
    feature: str,
    output: str,
    expected: list[int],
    sense: str,
) -> None:
    reference = OutputReference(feature=feature, output=output)
    uncoupled = Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": [n.model_dump() for n in recipe.nodes[:10]],
            "output": "sphere",
        }
    )
    assert validate_output_reference(reference, uncoupled, "direction")
    result = resolve_property(
        reference, "direction", {n.id: n for n in recipe.nodes}, values
    )
    np.testing.assert_allclose(result["value"], expected)
    assert result["sense"] == sense
    assert result["frame"] == "workspace"
    assert result["reference"] == reference.model_dump()
    assert "free" not in result


def test_distinct_properties_do_not_imply_position_or_radius_binding(
    recipe: Recipe, values: dict[str, dict[str, Any]]
) -> None:
    nodes = {n.id: n for n in recipe.nodes}
    ref = OutputReference(feature="cylinder", output="axis")
    direction = resolve_property(ref, "direction", nodes, values)
    assert direction["parameter_owner"]["quantities"] == {"orientation": 2}
    line = resolve_property(ref, "axis_line", nodes, values)
    assert line["parameter_owner"]["quantities"] == {
        "orientation": 2,
        "transverse_position": 2,
    }
    assert line["value"] == {"point": [3, 4, 0], "direction": [0, 0, 1]}
    assert (
        resolve_property(
            OutputReference(feature="sphere", output="radius"), "radius", nodes, values
        )["value"]
        == 4
    )
    assert resolve_property(
        OutputReference(feature="plane", output="plane"), "plane", nodes, values
    )["value"] == {"normal": [0, 0, 1], "offset": 3}
    assert resolve_output(ref, nodes, values) == {
        "axis_display": [0, 0, 1],
        "point_display": [3, 4, 0],
    }


@pytest.mark.parametrize(
    "feature,output",
    [
        ("sphere", "radius"),
        ("sphere", "center"),
        ("cylinder", "surface"),
        ("selection", "observations"),
    ],
)
def test_unavailable_direction_is_rejected_by_same_registry(
    recipe: Recipe, values: dict[str, dict[str, Any]], feature: str, output: str
) -> None:
    ref = OutputReference(feature=feature, output=output)
    with pytest.raises(ValueError, match="capability"):
        _ = validate_output_reference(ref, recipe, "direction")
    with pytest.raises(ValueError, match="property"):
        _ = resolve_property(ref, "direction", {n.id: n for n in recipe.nodes}, values)


def test_bound_and_derived_properties_do_not_claim_upstream_parameters(
    recipe: Recipe,
) -> None:
    for feature, output, name in [
        ("bound", "axis", "direction"),
        ("bound_plane", "plane", "direction"),
        ("bound_sphere", "center", "point"),
        ("initialized", "axis", "direction"),
        ("perpendicular", "plane", "direction"),
    ]:
        item = descriptor(recipe, feature, output)
        prop = item["properties"][name]
        assert prop["parameter_owner"] is None
        assert prop["binding_eligible"] is False
        assert prop["binding_reason"]
        assert prop["uses"] == ["geometry"]
        # Existing output-level binding contracts remain independent.
        assert item["parameter_owner"]["feature"] == feature
    assert descriptor(recipe, "bound", "radius")["properties"]["radius"][
        "parameter_owner"
    ]["quantities"] == {"radius": 1}
    assert descriptor(recipe, "datum", "plane")["properties"]["direction"][
        "parameter_owner"
    ]["quantities"] == {"clocking": 1}


def test_each_output_has_its_own_property_map_and_correct_owner_flag(
    recipe: Recipe,
) -> None:
    assert descriptor(recipe, "cylinder", "surface")["properties"] == {}
    assert set(descriptor(recipe, "cylinder", "axis")["properties"]) == {
        "direction",
        "axis_line",
    }
    assert set(descriptor(recipe, "cylinder", "radius")["properties"]) == {"radius"}
    observations = descriptor(recipe, "cylinder", "observations")
    assert observations["properties"] == {}
    assert observations["constrainable"] is False
    assert descriptor(recipe, "cylinder", "axis")["constrainable"] is True


def test_solved_context_is_exact_readonly_and_cannot_recover_owner(
    recipe: Recipe, values: dict[str, dict[str, Any]]
) -> None:
    ref = OutputReference(feature="plane", output="plane", context="parallel")
    item = descriptor(recipe, "plane", "plane", "parallel")
    assert item["constrainable"] is False
    assert item["parameter_owner"] is None
    assert item["properties"]["direction"]["binding_eligible"] is False
    result = resolve_property(ref, "direction", {n.id: n for n in recipe.nodes}, values)
    assert result["value"] == [0, 1, 0]
    assert result["parameter_owner"] is None
    values["plane"]["parameters"] = [1, 0, 0, 0]
    assert resolve_property(ref, "direction", {n.id: n for n in recipe.nodes}, values)[
        "value"
    ] == [0, 1, 0]
    with pytest.raises(ValueError, match="parameter owner"):
        _ = validate_output_reference(ref, recipe, "constrainable_plane")
    del values["parallel"]
    with pytest.raises(KeyError):
        _ = resolve_property(ref, "direction", {n.id: n for n in recipe.nodes}, values)


@pytest.mark.parametrize(
    "direction", [[0, 0, 0], [float("nan"), 0, 1], [0, float("inf"), 1], [0, 1]]
)
def test_direction_projection_rejects_invalid_vectors(
    recipe: Recipe, values: dict[str, dict[str, Any]], direction: list[float]
) -> None:
    values["axis"]["axis_display"] = direction
    with pytest.raises(ValueError, match=r"finite|nonzero"):
        _ = resolve_property(
            OutputReference(feature="axis", output="axis"),
            "direction",
            {n.id: n for n in recipe.nodes},
            values,
        )


def test_unit_projection_does_not_overflow_for_finite_axis(
    recipe: Recipe, values: dict[str, dict[str, Any]]
) -> None:
    values["axis"]["axis_display"] = [1e308, -1e308, 0]
    result = resolve_property(
        OutputReference(feature="axis", output="axis"),
        "direction",
        {n.id: n for n in recipe.nodes},
        values,
    )
    np.testing.assert_allclose(result["value"], [2**-0.5, -(2**-0.5), 0])


def test_requested_frame_never_relabels_actual_geometry(
    recipe: Recipe, values: dict[str, dict[str, Any]]
) -> None:
    recipe = Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": [n.model_dump() for n in recipe.nodes[:10]],
            "output": "sphere",
        }
    )
    ref = OutputReference(feature="axis", output="axis")
    with pytest.raises(ValueError, match="coordinate frame"):
        _ = validate_output_reference(ref, recipe, "direction", frame="other")
    with pytest.raises(ValueError, match="coordinate frame"):
        _ = resolve_property(
            ref,
            "direction",
            {n.id: n for n in recipe.nodes},
            values,
            required_frame="other",
        )
    declared = resolve_property(
        ref,
        "direction",
        {n.id: n for n in recipe.nodes},
        values,
        frame="declared",
        required_frame="declared",
    )
    catalogue = output_catalogue(recipe, frame="declared")
    assert (
        declared["frame"]
        == next(item for item in catalogue if item["reference"] == ref.model_dump())[
            "properties"
        ]["direction"]["frame"]
    )
    with pytest.raises(ValueError, match="coordinate frame"):
        _ = resolve_property(
            ref,
            "direction",
            {n.id: n for n in recipe.nodes},
            values,
            frame="declared",
            required_frame="other",
        )
    choices = input_choices(
        recipe,
        {n.id: "ready" for n in recipe.nodes},
        values,
        requirement="direction",
        frame="other",
    )
    assert not choices["choices"]
    assert all(item["frame"] == "workspace" for item in choices["unavailable"])
    assert all("coordinate frame" in item["reason"] for item in choices["unavailable"])


def test_renaming_and_recipe_reopen_preserve_reference_and_projection_identity(
    recipe: Recipe,
) -> None:
    before = OutputReference(feature="cylinder", output="axis")
    saved = recipe.model_dump()
    for node in saved["nodes"]:
        node["label"] = "Renamed " + node["id"]
    reopened = Recipe.model_validate_json(
        Recipe.model_validate(saved).model_dump_json()
    )
    after = validate_output_reference(before, reopened, "direction")
    assert after["reference"] == before.model_dump()
    assert (
        after["properties"]["direction"]
        == descriptor(recipe, "cylinder", "axis")["properties"]["direction"]
    )
    assert "properties" not in reopened.model_dump_json()
    assert declared_output_specs(reopened.nodes[6])[0]["output"] == "surface"


@pytest.mark.parametrize(
    "initializer,authority,eligible",
    [
        ({"initial_parameters": [0, 0, 0, 0]}, "chart_representative", True),
        (
            {"initial_parameters": [0, 0, 0, 0], "direction_reversed": True},
            "explicit_reversal",
            True,
        ),
        ({"source_fit": "cylinder"}, "fit_representative", False),
        ({"source_points": ["point", "second"]}, "ordered_points", False),
    ],
)
def test_direction_authority_does_not_claim_authored_fit_sense(
    initializer: dict[str, Any], authority: str, eligible: bool
) -> None:
    node = AxisDefinition.model_validate(
        {"id": "axis", "label": "Axis", "operation": "axis", **initializer}
    )
    prop = declared_output_specs(node)[0]["properties"]["direction"]
    assert prop["sense"] == "directed"
    assert prop["orientation_authority"] == authority
    assert prop["binding_eligible"] is eligible


@pytest.mark.parametrize(
    "output,property_name,parameters",
    [
        ("center", "point", [float("inf"), 0, 0, 1]),
        ("radius", "radius", [0, 0, 0, float("nan")]),
        ("radius", "radius", [0, 0, 0, 0]),
    ],
)
def test_other_projected_properties_require_finite_valid_geometry(
    recipe: Recipe,
    values: dict[str, dict[str, Any]],
    output: str,
    property_name: str,
    parameters: list[float],
) -> None:
    values["sphere"]["parameters"] = parameters
    with pytest.raises(ValueError, match="finite"):
        _ = resolve_property(
            OutputReference(feature="sphere", output=output),
            property_name,
            {n.id: n for n in recipe.nodes},
            values,
        )


@pytest.mark.parametrize(
    "feature,output,property_name",
    [
        ("plane", "plane", "axis_line"),
        ("cylinder", "axis", "point"),
        ("point", "point", "direction"),
    ],
)
def test_direction_availability_never_implies_another_geometric_property(
    recipe: Recipe,
    values: dict[str, dict[str, Any]],
    feature: str,
    output: str,
    property_name: str,
) -> None:
    with pytest.raises(ValueError, match="property"):
        _ = resolve_property(
            OutputReference(feature=feature, output=output),
            property_name,
            {n.id: n for n in recipe.nodes},
            values,
        )
