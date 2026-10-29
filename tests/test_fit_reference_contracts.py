"""Fit consumers use supported typed properties without changing legacy families."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.feature_inputs import (
    REQUIREMENTS,
    OutputReference,
    compatibility_reason,
    fit_reference_requirement,
    input_choices,
    output_catalogue,
    validate_fit_reference,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.relationship_inputs import normalize_endpoint
from experiments.scan_actions import preflight_actions


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def recipe(workspace: NozzleWorkspace) -> Recipe:
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
        },
        {
            "id": "selection",
            "operation": "selection",
            "source": "scan",
            "ids": [0, 1, 2],
        },
        {"id": "axis", "operation": "axis", "initial_parameters": [0, 0, 0, 0]},
        {"id": "datum", "operation": "reference_plane", "axis": "axis"},
        {
            "id": "perpendicular_datum",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
        },
        {"id": "point", "operation": "point", "initial_coordinates": [0, 0, 0]},
        *[
            {"id": kind, "operation": "fit", "kind": kind, "selections": ["selection"]}
            for kind in ("cylinder", "cone", "plane", "sphere")
        ],
        {"id": "fixed_axis", "operation": "axis", "source_fit": "cylinder"},
        {"id": "fixed_point", "operation": "point", "source_fit": "sphere"},
        {
            "id": "other_plane",
            "operation": "fit",
            "kind": "plane",
            "selections": ["selection"],
        },
        {
            "id": "parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["plane", "other_plane"],
        },
    ]
    return Recipe.model_validate(
        {
            "nodes": [{**node, "label": node["id"]} for node in nodes],
            "output": "parallel",
        }
    )


@pytest.mark.parametrize(
    "kind,field,requirement,feature,output",
    [
        ("cylinder", "axis", "fit_axis_line", "axis", "axis"),
        ("cone", "axis", "fit_axis_line", "axis", "axis"),
        ("plane", "axis", "fit_axis_direction", "axis", "axis"),
        ("plane", "reference_plane", "fit_plane_orientation", "datum", "plane"),
        ("sphere", "point", "fit_point_center", "point", "point"),
    ],
)
def test_supported_fit_modes_come_from_one_registry_and_keep_exact_legacy_identity(
    recipe: Recipe, kind: str, field: str, requirement: str, feature: str, output: str
) -> None:
    assert fit_reference_requirement(kind, field) == requirement
    contract = REQUIREMENTS[requirement]
    assert contract["fit_reference"]["field"] == field
    assert kind in contract["fit_reference"]["kinds"]
    assert contract["fit_reference"]["output"] == output
    assert contract["fit_reference"]["label"]
    assert contract["property"]
    assert contract["direct_context"] is True
    nodes = {node.id: node for node in recipe.nodes}
    reference = validate_fit_reference(kind, field, feature, nodes)
    assert reference == OutputReference(feature=feature, output=output, context=feature)
    assert (
        validate_fit_reference(kind, field, reference.model_dump(), nodes) == reference
    )
    catalogue = output_catalogue(recipe)
    matching = next(
        item for item in catalogue if item["reference"] == reference.model_dump()
    )
    assert compatibility_reason(matching, requirement) is None
    assert contract["property"] in matching["properties"]


@pytest.mark.parametrize(
    "kind,field,feature,property_name",
    [
        ("cylinder", "axis", "fixed_axis", "axis_line"),
        ("cone", "axis", "fixed_axis", "axis_line"),
        ("plane", "axis", "fixed_axis", "direction"),
        ("sphere", "point", "fixed_point", "point"),
    ],
)
def test_fixed_upstream_datums_remain_valid_fit_references(
    recipe: Recipe,
    workspace: NozzleWorkspace,
    kind: str,
    field: str,
    feature: str,
    property_name: str,
) -> None:
    nodes = {node.id: node for node in recipe.nodes}
    ref = validate_fit_reference(kind, field, feature, nodes)
    item = next(
        item
        for item in output_catalogue(recipe)
        if item["reference"] == ref.model_dump()
    )
    assert item["properties"][property_name]["binding_eligible"] is False
    assert item["properties"][property_name]["parameter_owner"] is None
    # Validation checks supported direct datum roles, not independent DOFs.
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "consumer",
            "label": "Consumer",
            "operation": "fit",
            "kind": kind,
            "selections": ["selection"],
            field: feature,
        }
    )
    payload["output"] = "consumer"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    saved = cast(dict[str, Any], graph.snapshot()["recipe"])
    assert saved["nodes"][-1]["kind"] == kind
    assert saved["nodes"][-1][field] == feature
    reopened = FeatureGraph(workspace, preflight_actions(saved).recipe)
    assert reopened.snapshot()["recipe"] == saved


@pytest.mark.parametrize(
    "kind,field",
    [
        ("cylinder", "reference_plane"),
        ("cone", "point"),
        ("sphere", "axis"),
        ("plane", "point"),
    ],
)
def test_unsupported_modes_cannot_change_the_selected_fit_family(
    recipe: Recipe, kind: str, field: str
) -> None:
    before = recipe.model_dump()
    with pytest.raises(ValueError, match="unavailable"):
        _ = validate_fit_reference(
            kind, field, "axis", {node.id: node for node in recipe.nodes}
        )
    assert recipe.model_dump() == before


@pytest.mark.parametrize(
    "kind,field,feature",
    [
        ("cylinder", "axis", "datum"),
        ("cylinder", "axis", "cylinder"),
        ("plane", "axis", "datum"),
        ("plane", "reference_plane", "plane"),
        ("sphere", "point", "sphere"),
        ("sphere", "point", "selection"),
    ],
)
def test_wrong_datums_are_rejected_by_picker_and_graph(
    recipe: Recipe, workspace: NozzleWorkspace, kind: str, field: str, feature: str
) -> None:
    nodes = {node.id: node for node in recipe.nodes}
    with pytest.raises(ValueError):
        _ = validate_fit_reference(kind, field, feature, nodes)
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "consumer",
            "label": "Consumer",
            "operation": "fit",
            "kind": kind,
            "selections": ["selection"],
            field: feature,
        }
    )
    payload["output"] = "consumer"
    with pytest.raises(ValueError):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))
    choices = input_choices(
        recipe,
        requirement=fit_reference_requirement(kind, field),
        catalogue=output_catalogue(recipe),
    )
    assert feature not in {item["reference"]["feature"] for item in choices["choices"]}
    assert not any(
        item["reference"]["feature"] == feature and item["compatible"]
        for item in choices["unavailable"]
    )


def test_solved_contexts_are_rejected_before_a_legacy_field_can_flatten_them(
    recipe: Recipe,
) -> None:
    nodes = {node.id: node for node in recipe.nodes}
    reference = OutputReference(feature="axis", output="axis", context="@axis/axis")
    with pytest.raises(ValueError, match="direct datum context"):
        _ = validate_fit_reference("cylinder", "axis", reference, nodes)
    direct = next(
        item
        for item in output_catalogue(recipe)
        if item["reference"]
        == OutputReference(feature="axis", output="axis").model_dump()
    )
    solved = deepcopy(direct)
    solved["reference"] = reference.model_dump()
    assert "direct datum context" in str(compatibility_reason(solved, "fit_axis_line"))
    wrong_property = deepcopy(direct)
    del wrong_property["properties"]["axis_line"]
    assert "geometric property" in str(
        compatibility_reason(wrong_property, "fit_axis_line")
    )
    with pytest.raises(ValueError, match="explicit datum"):
        _ = validate_fit_reference(
            "sphere", "point", OutputReference(feature="sphere", output="center"), nodes
        )


def test_relationships_consume_properties_without_reinterpreting_legacy_bindings(
    recipe: Recipe,
) -> None:
    nodes = {node.id: node for node in recipe.nodes}
    for requirement, property_name in [
        ("relationship_axis", "axis_line"),
        ("relationship_plane", "plane"),
        ("relationship_plane_orientation", "plane_orientation"),
        ("relationship_radius", "radius"),
    ]:
        assert REQUIREMENTS[requirement]["property"] == property_name
    # A constructed plane has only a clocking coordinate. It remains the same
    # legacy binding; property eligibility is not solve-local freedom.
    assert normalize_endpoint(
        "datum", nodes, "relationship_plane_orientation"
    ).is_binding
    perpendicular = next(
        item
        for item in output_catalogue(recipe)
        if item["reference"]
        == OutputReference(feature="perpendicular_datum", output="plane").model_dump()
    )
    assert perpendicular["properties"]["plane_orientation"]["binding_eligible"] is False
    assert normalize_endpoint(
        "perpendicular_datum", nodes, "relationship_plane_orientation"
    ).is_binding
    assert (
        validate_fit_reference(
            "plane", "reference_plane", "perpendicular_datum", nodes
        ).feature
        == "perpendicular_datum"
    )
    assert normalize_endpoint("fixed_axis", nodes, "coaxial_axis").is_binding
    assert normalize_endpoint("cylinder", nodes, "coaxial_members").output == "axis"
    assert normalize_endpoint("sphere", nodes, "relationship_radius").output == "radius"
    fixed = normalize_endpoint(
        OutputReference(feature="plane", output="plane", context="parallel"),
        nodes,
        "relationship_plane_orientation",
    )
    assert fixed.context == "parallel" and not fixed.is_binding


def test_fit_contract_metadata_is_published_without_adding_saved_fields(
    recipe: Recipe, workspace: NozzleWorkspace
) -> None:
    state = FeatureGraph(workspace, recipe).snapshot()
    contracts = cast(dict[str, Any], state["input_contracts"])
    assert {key for key, value in contracts.items() if "fit_reference" in value} == {
        "fit_axis_line",
        "fit_axis_direction",
        "fit_plane_orientation",
        "fit_point_center",
    }
    assert contracts["fit_axis_line"] == REQUIREMENTS["fit_axis_line"]
    assert "fit_reference" not in recipe.model_dump_json()
