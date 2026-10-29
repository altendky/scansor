"""Relationship bindings and exact published geometry have distinct roles."""

import copy
from typing import Any

import pytest

from experiments.feature_graph import Recipe
from experiments.feature_inputs import (
    REQUIREMENTS,
    OutputReference,
    input_choices,
    output_catalogue,
)
from experiments.relationship_inputs import (
    RELATIONSHIP_SIGNATURES,
    normalize_endpoint,
    relationship_reference,
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
                {
                    "id": "axis",
                    "label": "Axis",
                    "operation": "axis",
                    "initial_parameters": [0, 0, 0, 0],
                },
                {
                    "id": "datum",
                    "label": "Datum",
                    "operation": "reference_plane",
                    "axis": "axis",
                },
                *[
                    {
                        "id": key,
                        "label": key,
                        "operation": "fit",
                        "kind": "plane",
                        "selections": ["selected"],
                    }
                    for key in ("first", "second")
                ],
                {
                    "id": "side",
                    "label": "Side",
                    "operation": "fit",
                    "kind": "cylinder",
                    "selections": ["selected"],
                },
                {
                    "id": "solved",
                    "label": "Solved planes",
                    "operation": "plane_relationship",
                    "relation": "parallel",
                    "surfaces": ["first", "second"],
                },
            ],
            "output": "solved",
        }
    )


def catalogue(recipe: Recipe, state: str = "ready") -> list[dict[str, Any]]:
    values = {
        "axis": {"axis_display": [0, 0, 1], "point_display": [0, 0, 0]},
        "datum": {"plane_equation": [0, 1, 0, 0]},
        "first": {"plane_equation": [0, 0, 1, 1]},
        "second": {"plane_equation": [0, 0, 1, 2]},
        "side": {
            "kind": "cylinder",
            "analytic": {
                "kind": "cylinder",
                "origin": [0, 0, 0],
                "axis": [0, 0, 1],
                "radius": 2,
                "slope": 0,
                "axial_domain": [-1, 1],
            },
        },
        "solved": {
            "surfaces": {
                "first": {"plane_equation": [0, 0, 1, 1]},
                "second": {"plane_equation": [0, 0, 1, 2]},
            }
        },
    }
    return output_catalogue(
        recipe, dict.fromkeys((node.id for node in recipe.nodes), state), values
    )


def test_owner_additions_leave_existing_geometry_reader_contexts_unchanged(
    recipe: Recipe,
) -> None:
    outputs = catalogue(recipe)
    reads = input_choices(recipe, requirement="plane", catalogue=outputs)
    first = [
        item
        for item in reads["choices"] + reads["unavailable"]
        if item["reference"]["feature"] == "first"
    ]
    assert len(first) == 1
    assert first[0]["reference"]["context"] == "solved"
    bindings = input_choices(
        recipe, requirement="relationship_plane", catalogue=outputs
    )
    first = [
        item for item in bindings["choices"] if item["reference"]["feature"] == "first"
    ]
    assert {item["reference"]["context"] for item in first} == {"first", "solved"}
    direct = next(item for item in first if item["reference"]["context"] == "first")
    solved = next(item for item in first if item["reference"]["context"] == "solved")
    assert direct["parameter_owner"]["feature"] == "first"
    assert direct["uses"] == ["binding"]
    assert solved["parameter_owner"] is None
    assert solved["uses"] == ["geometry"]
    identities = [OutputReference.model_validate(item["reference"]) for item in outputs]
    assert len(identities) == len(set(identities))


def test_failed_coupled_publication_does_not_block_corrective_binding(
    recipe: Recipe,
) -> None:
    outputs = catalogue(recipe, "failed")
    bindings = input_choices(
        recipe, requirement="relationship_plane", catalogue=outputs
    )
    assert {item["reference"]["feature"] for item in bindings["choices"]} >= {
        "first",
        "second",
        "datum",
    }
    assert all(item["parameter_owner"] for item in bindings["choices"])
    assert not input_choices(recipe, requirement="plane", catalogue=outputs)["choices"]
    assert all(
        item["availability"] == "failed"
        for item in outputs
        if "plane" in item["capabilities"] and "geometry" in item["uses"]
    )


def test_structural_normalizer_preserves_exact_context_and_never_discovers_solves(
    recipe: Recipe, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("pure normalization must not discover solve contexts")

    monkeypatch.setattr("experiments.feature_inputs.output_catalogue", forbidden)
    nodes = {node.id: node for node in recipe.nodes}
    assert normalize_endpoint("first", nodes).owner == "first"
    explicit = OutputReference(feature="first", output="plane", context="solved")
    endpoint = normalize_endpoint(explicit, nodes)
    assert endpoint.reference == explicit
    assert not endpoint.is_binding
    assert endpoint.context == "solved"
    assert endpoint.key != normalize_endpoint("first", nodes).key


def test_exact_context_validation_and_legacy_serialization(recipe: Recipe) -> None:
    before = copy.deepcopy(recipe.model_dump())
    assert relationship_reference("first", recipe).owner == "first"
    explicit = {"feature": "first", "output": "plane", "context": "solved"}
    assert relationship_reference(explicit, recipe).reference.model_dump() == explicit
    with pytest.raises(ValueError, match="reference"):
        _ = relationship_reference({**explicit, "context": "missing"}, recipe)
    assert recipe.model_dump() == before
    assert before["nodes"][-1]["surfaces"] == ["first", "second"]


def test_cylinder_axes_supply_line_and_direction_capabilities(
    recipe: Recipe,
) -> None:
    outputs = catalogue(recipe)
    lines = input_choices(recipe, requirement="relationship_axis", catalogue=outputs)
    assert {item["reference"]["feature"] for item in lines["choices"]} == {
        "axis",
        "side",
    }
    assert (
        normalize_endpoint(
            "side", {node.id: node for node in recipe.nodes}, "relationship_axis"
        ).output
        == "axis"
    )
    directions = input_choices(recipe, requirement="direction", catalogue=outputs)
    assert "side" in {item["reference"]["feature"] for item in directions["choices"]}
    direction = next(
        item for item in directions["choices"] if item["reference"]["feature"] == "side"
    )
    line = next(
        item for item in lines["choices"] if item["reference"]["feature"] == "side"
    )
    assert direction["reference"] == line["reference"]
    assert {"direction", "axis_line"} <= set(direction["capabilities"])


def test_roles_and_counts_have_one_authoritative_contract() -> None:
    for signature in RELATIONSHIP_SIGNATURES.values():
        assert signature["selection_pattern"] in {"peers", "reference_targets", "roles"}
        assert set(signature["role_labels"]) == set(signature["roles"])
        if signature["selection_pattern"] == "reference_targets":
            assert signature["reference_role"] in signature["roles"]
        if signature["operation"] != "equal":
            assert set(signature["node_fields"]) == set(signature["roles"])
            for field in signature["node_fields"].values():
                assert field["name"]
                assert field["encoding"] in {"reference", "feature"}
                assert type(field["multiple"]) is bool
        for requirement in signature["roles"].values():
            assert requirement in REQUIREMENTS
            assert REQUIREMENTS[requirement]["min_count"] >= 1
            assert (
                REQUIREMENTS[requirement]["max_count"]
                >= REQUIREMENTS[requirement]["min_count"]
            )


def test_explicit_roles_do_not_infer_reference_from_compatible_peer_outputs(
    recipe: Recipe, monkeypatch: pytest.MonkeyPatch
) -> None:
    for role, maximum in [("reference", 1), ("targets", 33)]:
        monkeypatch.setitem(
            REQUIREMENTS,
            "test_" + role,
            {
                **REQUIREMENTS["relationship_plane"],
                "min_count": 1,
                "max_count": maximum,
            },
        )
    monkeypatch.setitem(
        RELATIONSHIP_SIGNATURES,
        "test_explicit",
        {
            "roles": {"reference": "test_reference", "targets": "test_targets"},
            "role_labels": {"reference": "Reference plane", "targets": "Target planes"},
        },
    )
    refs = {"targets": ["first", "second"], "reference": ["datum"]}
    roles = validate_relationship_inputs("test_explicit", refs, recipe)
    assert [endpoint.feature for endpoint in roles["reference"]] == ["datum"]
    assert [endpoint.feature for endpoint in roles["targets"]] == ["first", "second"]
    with pytest.raises(ValueError, match=r"cardinality|matches multiple roles"):
        _ = validate_relationship_inputs(
            "test_explicit", ["datum", "first", "second"], recipe
        )
    with pytest.raises(ValueError, match="distinct across roles"):
        _ = validate_relationship_inputs(
            "test_explicit", {"reference": ["datum"], "targets": ["datum"]}, recipe
        )
    with pytest.raises(ValueError, match="cardinality"):
        _ = validate_relationship_inputs(
            "test_explicit",
            {"reference": ["datum", "first"], "targets": ["second"]},
            recipe,
        )


def test_parallel_and_coincident_request_distinct_geometric_information(
    recipe: Recipe,
) -> None:
    assert (
        RELATIONSHIP_SIGNATURES["parallel_planes"]["roles"]["participants"]
        == "relationship_plane_orientation"
    )
    assert (
        RELATIONSHIP_SIGNATURES["coincident_planes"]["roles"]["participants"]
        == "relationship_plane"
    )
    for kind in ("parallel_planes", "coincident_planes"):
        roles = validate_relationship_inputs(kind, ["datum", "first", "second"], recipe)
        assert [endpoint.owner for endpoint in roles["participants"]] == [
            "datum",
            "first",
            "second",
        ]
    with pytest.raises(ValueError, match="distinct"):
        _ = validate_relationship_inputs(
            "parallel_planes",
            ["first", {"feature": "first", "output": "plane", "context": "first"}],
            recipe,
        )


def test_existing_relationship_roles_use_the_registry_and_keep_semantic_pair_checks(
    recipe: Recipe,
) -> None:
    mirror = validate_relationship_inputs(
        "mirror", ["datum", "first", "second"], recipe
    )
    assert [endpoint.owner for endpoint in mirror["members"]] == ["first", "second"]
    assert mirror["datum"][0].owner == "datum"
    with pytest.raises(ValueError, match="same geometry kind"):
        _ = validate_relationship_inputs("mirror", ["datum", "first", "side"], recipe)
    payload = recipe.model_dump()
    next(node for node in payload["nodes"] if node["id"] == "side")["axis"] = "axis"
    bound = Recipe.model_validate(payload)
    roles = validate_relationship_inputs(
        "radius_plane_distance", ["side", "first", "datum"], bound
    )
    assert roles["radius"][0].output == "radius"
    assert roles["plane"][0].output == "plane"
    with pytest.raises(ValueError, match=r"cardinality|incompatible"):
        _ = validate_relationship_inputs("equal_radii", ["first", "second"], recipe)
