"""Reference role and fixed/read versus adjustable ownership are independent."""

from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from experiments.feature_graph import (
    AxisDefinition,
    DirectionAlignment,
    FeatureGraph,
    Recipe,
    plane_relationship_endpoints,
)
from experiments.feature_inputs import OutputReference, input_choices
from experiments.nozzle_session import NozzleWorkspace
from experiments.relationship_inputs import (
    RELATIONSHIP_SIGNATURES,
    normalize_endpoint,
    validate_relationship_inputs,
)
from tests.test_direction_graph import (
    evaluated,
    graph_with,
    reference,
)
from tests.test_direction_graph import (
    fixture as fixture,
)


def alignment(*, use: str | None = None, context: str | None = None) -> dict[str, Any]:
    return {
        "id": "alignment",
        "operation": "direction_alignment",
        "reference": reference("cap", "plane", context),
        "directions": [reference("other", "plane")],
        **({"reference_use": use} if use is not None else {}),
    }


@pytest.mark.parametrize("use", [None, "geometry", "binding"])
def test_reference_role_preserves_legacy_fixed_and_joint_adjustable_ownership(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], use: str | None
) -> None:
    workspace, nodes = fixture
    graph = graph_with(fixture, alignment(use=use))
    before = cast(dict[str, Any], graph.snapshot())
    assert before["states"]["alignment"] == "unevaluated"
    assert before["recipe"]["nodes"][-1]["reference_use"] == (use or "geometry")
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    normal = np.asarray(state["results"]["other"]["plane_equation"][:3])
    patches: list[NDArray[np.float64]] = []
    for name in ("cap", "other"):
        selection = next(node for node in nodes if node["id"] == name + "_selection")
        patches.append(workspace.local[selection["ids"]])
    if use == "binding":
        scatter = sum(
            (patch - patch.mean(0)).T @ (patch - patch.mean(0)) for patch in patches
        )
        _, eigenvectors = np.linalg.eigh(scatter)
        expected = eigenvectors[:, 0]
        source_normal = np.asarray(state["results"]["cap"]["plane_equation"][:3])
        assert abs(source_normal @ normal) == pytest.approx(1, abs=1e-9)
    else:
        _, _, vectors = np.linalg.svd(patches[0] - patches[0].mean(0))
        expected = vectors[-1]
    assert abs(expected @ normal) == pytest.approx(1, abs=1e-9)
    assert state["recipe"] == before["recipe"]
    restored = FeatureGraph(
        workspace,
        Recipe.model_validate_json(
            Recipe.model_validate(graph.export_recipe()).model_dump_json()
        ),
    )
    reopened = evaluated(restored)
    assert reopened["recipe"] == state["recipe"]
    np.testing.assert_allclose(
        reopened["results"]["other"]["plane_equation"],
        state["results"]["other"]["plane_equation"],
        atol=1e-8,
    )


def test_adjustable_reference_joins_its_existing_direction_component_once(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = {
        "id": "upstream",
        "operation": "direction_alignment",
        "directions": [reference("left"), reference("cap", "plane")],
    }
    graph = graph_with(fixture, upstream, alignment(use="binding"))
    flat = graph_with(
        fixture,
        {
            **upstream,
            "directions": [*upstream["directions"], reference("other", "plane")],
        },
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a connected reference must not fit members independently")

    monkeypatch.setattr("experiments.feature_graph.fit_seed", forbidden)
    state, baseline = evaluated(graph), evaluated(flat)
    assert not state["errors"], state["errors"]
    assert state["states"]["upstream"] == "ready"
    assert set(state["derived"]["alignment"]["surfaces"]) == {"left", "cap", "other"}
    for key in ("left", "cap", "other"):
        assert state["results"][key]["weighted_rms"] == pytest.approx(
            baseline["results"][key]["weighted_rms"], abs=1e-9
        )


def test_binding_reference_rejects_an_exact_publication(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    with pytest.raises(ValueError, match=r"binding.*direct|direct.*binding"):
        _ = graph_with(
            fixture,
            {
                "id": "upstream",
                "operation": "direction_alignment",
                "directions": [reference("left"), reference("cap", "plane")],
            },
            alignment(use="binding", context="upstream"),
        )


def test_explicit_reference_use_is_validated_and_cannot_bind_a_read_only_property(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, alignment())
    recipe = Recipe.model_validate(graph.export_recipe())
    nodes = {node.id: node for node in recipe.nodes}
    fixed = normalize_endpoint(
        OutputReference(feature="cap", output="plane"),
        nodes,
        "relationship_direction_reference",
        use="geometry",
    )
    free = normalize_endpoint(
        OutputReference(feature="cap", output="plane"),
        nodes,
        "relationship_direction_reference",
        use="binding",
    )
    assert not fixed.is_binding and free.owner == "cap"
    with pytest.raises(ValueError, match="input use"):
        _ = normalize_endpoint(
            "cap", nodes, "relationship_direction_reference", use="unknown"
        )
    # Build just the structural normalizer input: source-fit axes have a read-only direction.
    nodes["axis"] = AxisDefinition.model_validate(
        {"id": "axis", "label": "Axis", "operation": "axis", "source_fit": "left"}
    )
    with pytest.raises(ValueError, match=r"read.only|adjustable|binding"):
        _ = normalize_endpoint(
            OutputReference(feature="axis", output="axis"),
            nodes,
            "relationship_direction_reference",
            use="binding",
        )
    with pytest.raises(ValueError, match="reference_use"):
        _ = graph_with(fixture, alignment(use="unknown"))


def test_role_validation_uses_legacy_fixed_default_and_explicit_binding(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, alignment())
    recipe = Recipe.model_validate(graph.export_recipe())
    roles = {
        "reference": [OutputReference(feature="cap", output="plane")],
        "participants": [OutputReference(feature="other", output="plane")],
    }
    legacy = validate_relationship_inputs("direction_alignment", roles, recipe)
    adjustable = validate_relationship_inputs(
        "direction_alignment", roles, recipe, role_uses={"reference": "binding"}
    )
    assert not legacy["reference"][0].is_binding
    assert adjustable["reference"][0].owner == "cap"
    original = recipe.nodes[-1]
    assert isinstance(original, DirectionAlignment)
    node = original.model_copy(update={"reference_use": "binding"})
    compiled = plane_relationship_endpoints(
        node, {item.id: item for item in recipe.nodes}
    )
    assert {endpoint.owner for endpoint in compiled} == {"cap", "other"}


def test_pending_role_catalogue_keeps_adjustable_owner_and_exact_fixed_publication_distinct(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "upstream",
            "operation": "direction_alignment",
            "directions": [reference("left"), reference("cap", "plane")],
        },
    )
    recipe = Recipe.model_validate(graph.export_recipe())
    choices = input_choices(recipe, requirement="relationship_direction_reference")
    cap = {
        item["reference"]["context"]: item
        for item in choices["choices"] + choices["unavailable"]
        if item["reference"]["feature"] == "cap"
    }
    assert cap["cap"]["uses"] == ["binding"]
    assert cap["cap"]["availability"] == "ready"
    assert cap["upstream"]["uses"] == ["geometry"]
    assert cap["upstream"]["availability"] == "unevaluated"
    with pytest.raises(ValueError, match="exact published geometry context"):
        _ = graph_with(
            fixture, graph.export_recipe()["nodes"][-1], alignment(use="geometry")
        )


@pytest.mark.parametrize("use", [None, "geometry"])
def test_exact_fixed_reference_preserves_upstream_solution_without_joining_it(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], use: str | None
) -> None:
    upstream = {
        "id": "upstream",
        "operation": "direction_alignment",
        "directions": [reference("left"), reference("cap", "plane")],
    }
    baseline = evaluated(graph_with(fixture, upstream))
    graph = graph_with(fixture, upstream, alignment(use=use, context="upstream"))
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    assert set(state["derived"]["alignment"]["surfaces"]) == {"other"}
    np.testing.assert_allclose(
        state["derived"]["upstream"]["surfaces"]["cap"]["plane_equation"],
        baseline["derived"]["upstream"]["surfaces"]["cap"]["plane_equation"],
        atol=1e-9,
    )


def test_changing_reference_ownership_invalidates_and_rebuilds_the_true_component(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, alignment())
    initial = evaluated(graph)
    fixed_normal = np.asarray(initial["results"]["other"]["plane_equation"][:3])
    for use in ("binding", "geometry"):
        payload = graph.export_recipe()
        payload["nodes"][-1]["reference_use"] = use
        changed = cast(
            dict[str, Any],
            graph.replace(
                Recipe.model_validate(payload), str(graph.snapshot()["token"])
            ),
        )
        assert changed["states"]["alignment"] == "stale"
        state = evaluated(graph)
        assert not state["errors"], state["errors"]
        normal = np.asarray(state["results"]["other"]["plane_equation"][:3])
        if use == "binding":
            assert np.linalg.norm(np.cross(normal, fixed_normal)) > 0.01
        else:
            assert abs(normal @ fixed_normal) == pytest.approx(1, abs=1e-9)


def test_reference_use_metadata_matches_model_and_rejects_invalid_empty_role_use(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(fixture, alignment())
    recipe = Recipe.model_validate(graph.export_recipe())
    field = RELATIONSHIP_SIGNATURES["direction_alignment"]["node_fields"]["reference"]
    assert field["use_field"] == "reference_use"
    node = recipe.nodes[-1]
    assert isinstance(node, DirectionAlignment)
    assert field["use_default"] == node.reference_use == "geometry"
    assert {choice["value"] for choice in field["use_choices"]} == {
        "geometry",
        "binding",
    }
    with pytest.raises(ValueError, match="input use"):
        _ = validate_relationship_inputs(
            "direction_alignment",
            {"participants": [reference("cap", "plane"), reference("other", "plane")]},
            recipe,
            role_uses={"reference": "unknown"},
        )
    with pytest.raises(ValueError, match="not configurable"):
        _ = validate_relationship_inputs(
            "direction_alignment",
            {"participants": [reference("cap", "plane"), reference("other", "plane")]},
            recipe,
            role_uses={"participants": "geometry"},
        )


def test_adjustable_reference_axis_retains_existing_coaxial_line_and_member_fits(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, nodes = fixture
    ids = next(node["ids"] for node in nodes if node["id"] == "right_selection")
    workspace.local[ids] -= np.array([10.0, 1.0, 0.0])
    nodes.insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [-5, 0, 0, 0],
        },
    )
    group = {
        "id": "coaxial",
        "operation": "coaxial_group",
        "axis": "axis",
        "surfaces": ["left", "right"],
    }
    relation = {
        "id": "alignment",
        "operation": "direction_alignment",
        "reference": reference("axis"),
        "reference_use": "binding",
        "directions": [reference("cap", "plane")],
    }
    state = evaluated(graph_with(fixture, group, relation))
    baseline = evaluated(
        graph_with(
            fixture,
            group,
            {
                "id": "alignment",
                "operation": "direction_alignment",
                "directions": [reference("axis"), reference("cap", "plane")],
            },
        )
    )
    assert not state["errors"], state["errors"]
    assert state["states"]["coaxial"] == "ready"
    for key in ("left", "right", "cap"):
        assert state["results"][key]["weighted_rms"] == pytest.approx(
            baseline["results"][key]["weighted_rms"], abs=1e-9
        )
    axis = np.asarray(state["derived"]["coaxial"]["resolved"]["axis"]["axis_display"])
    normal = np.asarray(state["results"]["cap"]["plane_equation"][:3])
    assert abs(axis @ normal) == pytest.approx(1, abs=1e-9)
    assert np.linalg.norm(np.cross(axis, [0, 0, 1])) > 0.001
