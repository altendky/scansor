"""Coaxial inputs bind declared owners without retargeting published reads."""

import pytest

from experiments.feature_graph import Recipe
from experiments.feature_inputs import (
    OutputReference,
    input_choices,
    output_catalogue,
    validate_output_reference,
)
from experiments.relationship_inputs import (
    RELATIONSHIP_SIGNATURES,
    normalize_endpoint,
    validate_relationship_inputs,
)


@pytest.fixture
def recipe() -> Recipe:
    return Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": "scan",
                    "label": "Scan",
                    "operation": "source",
                    "source_sha256": "a",
                    "reference_sha256": "b",
                },
                {
                    "id": "selected",
                    "label": "Selected",
                    "operation": "selection",
                    "source": "scan",
                    "ids": [0, 1, 2],
                },
                *[
                    {
                        "id": key,
                        "label": key,
                        "operation": "axis",
                        "initial_parameters": [0, 0, 0, 0],
                    }
                    for key in ("axis", "other_axis")
                ],
                *[
                    {
                        "id": key,
                        "label": key,
                        "operation": "fit",
                        "kind": kind,
                        "selections": ["selected"],
                    }
                    for key, kind in (
                        ("outer", "cone"),
                        ("inner", "cylinder"),
                        ("plane", "plane"),
                        ("ball", "sphere"),
                    )
                ],
            ],
            "output": "inner",
        }
    )


def test_coaxial_roles_share_capabilities_and_accept_mixed_lateral_shapes(
    recipe: Recipe,
) -> None:
    roles = validate_relationship_inputs("coaxial", ["inner", "axis", "outer"], recipe)
    assert [endpoint.feature for endpoint in roles["axis"]] == ["axis"]
    assert [endpoint.feature for endpoint in roles["members"]] == ["inner", "outer"]
    assert all(
        endpoint.is_binding for endpoints in roles.values() for endpoint in endpoints
    )
    assert RELATIONSHIP_SIGNATURES["coaxial"]["operation"] == "coaxial_group"
    assert all(
        getattr(node, "axis", None) is None
        for node in recipe.nodes
        if node.operation == "fit"
    )


@pytest.mark.parametrize(
    "participants",
    [
        ["axis", "outer"],
        ["axis", "outer", "outer"],
        ["axis", "other_axis", "outer", "inner"],
        ["axis", "outer", "plane"],
        ["axis", "outer", "ball"],
    ],
)
def test_coaxial_rejects_missing_roles_duplicate_owners_and_wrong_shapes(
    recipe: Recipe, participants: list[str]
) -> None:
    with pytest.raises(ValueError):
        _ = validate_relationship_inputs("coaxial", participants, recipe)


def test_coaxial_does_not_rebind_named_geometry_contexts(recipe: Recipe) -> None:
    with pytest.raises(ValueError, match="input role"):
        _ = normalize_endpoint(
            OutputReference(feature="outer", output="axis", context="publication"),
            {node.id: node for node in recipe.nodes},
            "coaxial_members",
        )
    direct = normalize_endpoint(
        OutputReference(feature="outer", output="axis"),
        {node.id: node for node in recipe.nodes},
        "coaxial_members",
    )
    assert direct.owner == "outer"


def test_coaxial_reports_conflicting_existing_axis_and_allows_compatible_bindings(
    recipe: Recipe,
) -> None:
    def bound(axis: str) -> Recipe:
        return recipe.model_copy(
            update={
                "nodes": [
                    node.model_copy(update={"axis": axis})
                    if node.id == "outer"
                    else node
                    for node in recipe.nodes
                ]
            }
        )

    assert validate_relationship_inputs(
        "coaxial", ["axis", "outer", "inner"], bound("axis")
    )
    with pytest.raises(ValueError, match="another axis"):
        _ = validate_relationship_inputs(
            "coaxial", ["axis", "outer", "inner"], bound("other_axis")
        )


def test_failed_geometry_still_allows_owner_repair_without_exposing_solved_bindings(
    recipe: Recipe,
) -> None:
    outputs = output_catalogue(recipe, {node.id: "failed" for node in recipe.nodes})
    axis = input_choices(recipe, requirement="coaxial_axis", catalogue=outputs)
    members = input_choices(recipe, requirement="coaxial_members", catalogue=outputs)
    assert {item["reference"]["feature"] for item in axis["choices"]} == {
        "axis",
        "other_axis",
    }
    assert {item["reference"]["feature"] for item in members["choices"]} == {
        "outer",
        "inner",
    }
    assert all(item["parameter_owner"] for item in members["choices"])


def test_coaxial_rejects_reusing_a_member_under_a_different_group_axis(
    recipe: Recipe,
) -> None:
    payload = recipe.model_dump()
    payload["nodes"].append(
        {
            "id": "existing",
            "label": "Existing",
            "operation": "coaxial_group",
            "axis": "axis",
            "surfaces": ["outer", "inner"],
        }
    )
    grouped = Recipe.model_validate(payload)
    assert validate_relationship_inputs("coaxial", ["axis", "outer", "inner"], grouped)
    with pytest.raises(ValueError, match="relationship using another axis"):
        _ = validate_relationship_inputs(
            "coaxial", ["other_axis", "outer", "inner"], grouped
        )


def test_explicit_joint_owns_member_geometry_without_retargeting_coaxial_context(
    recipe: Recipe,
) -> None:
    payload = recipe.model_dump()
    payload["nodes"].extend(
        [
            {
                "id": "group",
                "label": "Group",
                "operation": "coaxial_group",
                "axis": "axis",
                "surfaces": ["outer", "inner"],
            },
            {
                "id": "joint",
                "label": "Joint",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["group"],
            },
        ]
    )
    grouped = Recipe.model_validate(payload)
    geometry = {
        "kind": "cylinder",
        "analytic": {
            "kind": "cylinder",
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": 2,
            "slope": 0,
            "axial_domain": [-1, 1],
        },
    }
    values = {
        "joint": {"surfaces": {"outer": geometry, "inner": geometry}},
        "group": {},
    }
    outputs = output_catalogue(
        grouped, {node.id: "ready" for node in grouped.nodes}, values
    )
    suppressed = [
        output for output in outputs if output["reference"]["context"] == "group"
    ]
    assert suppressed
    assert all(output["availability"] == "ambiguous" for output in suppressed)
    reads = input_choices(grouped, requirement="relationship_axis", catalogue=outputs)
    assert {
        output["reference"]["feature"]
        for output in reads["choices"]
        if output["reference"]["context"] == "joint"
    } == {"outer", "inner"}
    assert not any(
        output["reference"]["context"] == "group" for output in reads["choices"]
    )
    with pytest.raises(ValueError, match="explicit joint"):
        _ = validate_output_reference(
            OutputReference(feature="outer", output="axis", context="group"),
            grouped,
            "relationship_axis",
        )
