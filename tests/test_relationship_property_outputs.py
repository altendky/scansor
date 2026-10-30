"""Relationship ports alias actual common variables or read exact publications."""

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from experiments.feature_graph import (
    DirectionAlignment,
    FeatureGraph,
    Recipe,
    plane_relationship_endpoints,
)
from experiments.feature_inputs import (
    OutputReference,
    compatibility_reason,
    declared_output_specs,
    input_choices,
    output_catalogue,
    reference_key,
    resolve_output,
    resolve_property,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.relationship_inputs import normalize_endpoint
from experiments.surface_primitives import basis, revolution_geometry
from tests.test_direction_graph import (
    evaluated,
    graph_with,
    reference,
)
from tests.test_direction_graph import (
    fixture as fixture,
)


def common(feature: str = "upstream", output: str = "direction") -> dict[str, str]:
    return reference(feature, output)


def planes(fixture: tuple[NozzleWorkspace, list[dict[str, Any]]]) -> None:
    workspace, nodes = fixture
    node = next(node for node in nodes if node["id"] == "left")
    node["kind"] = "plane"
    ids = next(node["ids"] for node in nodes if node["id"] == "left_selection")
    normal = np.array([0.08, -0.04, 1.0])
    normal /= np.linalg.norm(normal)
    u, v = basis(normal)
    xy = np.array(
        [(x, y) for x in np.linspace(-3, 3, 10) for y in np.linspace(-1, 1, 12)]
    )
    workspace.local[ids] = np.array([-5, 2, 9]) + xy[:, :1] * u + xy[:, 1:] * v
    workspace.data.normals[ids] = normal @ workspace.frame.T
    for name, weight in (("cap", 0.7), ("other", 2.3), ("left", 1.1)):
        selected = next(
            node["ids"] for node in nodes if node["id"] == name + "_selection"
        )
        workspace.data.weights[selected] = weight


def upstream() -> dict[str, Any]:
    return {
        "id": "upstream",
        "operation": "direction_alignment",
        "directions": [reference("cap", "plane"), reference("other", "plane")],
    }


def downstream(use: str) -> dict[str, Any]:
    return {
        "id": "downstream",
        "operation": "direction_alignment",
        "reference": common(),
        "reference_use": use,
        "directions": [reference("left", "plane")],
    }


def expected_normal(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], names: tuple[str, ...]
) -> NDArray[np.float64]:
    workspace, nodes = fixture
    scatter = np.zeros((3, 3))
    for name in names:
        ids = next(node["ids"] for node in nodes if node["id"] == name + "_selection")
        xyz, weights = workspace.local[ids], workspace.data.weights[ids]
        centered = xyz - np.average(xyz, axis=0, weights=weights)
        scatter += centered.T @ (weights[:, None] * centered)
    return np.linalg.eigh(scatter)[1][:, 0]


@pytest.mark.parametrize("use", ["binding", "geometry"])
def test_common_direction_joint_matches_true_weighted_optimum_or_fixed_upstream(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    use: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planes(fixture)
    graph = graph_with(fixture, upstream(), downstream(use))
    before = cast(dict[str, Any], graph.snapshot())
    assert (
        before["states"]["upstream"] == before["states"]["downstream"] == "unevaluated"
    )
    raw = next(
        item for item in before["input_catalogue"] if item["reference"] == common()
    )
    assert raw["properties"]["direction"]["parameter_owner"] is None
    assert {
        item["feature"] for item in raw["properties"]["direction"]["binding_aliases"]
    } == {"cap", "other"}
    assert "axis_line" not in raw["capabilities"] and "point" not in raw["capabilities"]
    assert "downstream" not in raw["binding_dependencies"]
    if use == "binding":

        def forbidden(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                "common aliases must fit observations jointly, without standalone initialization"
            )

        monkeypatch.setattr("experiments.feature_graph.fit_seed", forbidden)
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    expected = expected_normal(
        fixture, ("cap", "other", "left") if use == "binding" else ("cap", "other")
    )
    normal = np.asarray(state["results"]["left"]["plane_equation"][:3])
    assert abs(normal @ expected) == pytest.approx(1, abs=1e-9)
    assert state["recipe"] == before["recipe"]
    output = resolve_output(
        OutputReference.model_validate(common()),
        {node.id: node for node in Recipe.model_validate(state["recipe"]).nodes},
        state["derived"],
    )
    assert set(output) == {"axis_display"}
    assert state["derived"]["upstream"]["common_outputs"]["direction"] == output
    assert abs(np.asarray(output["axis_display"]) @ expected) == pytest.approx(
        1, abs=1e-9
    )
    if use == "binding":
        assert set(state["derived"]["downstream"]["surfaces"]) == {
            "cap",
            "other",
            "left",
        }
        flat = evaluated(
            graph_with(
                fixture,
                {
                    **upstream(),
                    "directions": [
                        *upstream()["directions"],
                        reference("left", "plane"),
                    ],
                },
            )
        )
        for name in ("cap", "other", "left"):
            assert state["results"][name]["weighted_rms"] == pytest.approx(
                flat["results"][name]["weighted_rms"], abs=1e-9
            )
    else:
        assert set(state["derived"]["downstream"]["surfaces"]) == {"left"}
        direction_reads = [
            value
            for value in state["derived"]["downstream"]["axes"].values()
            if value.get("direction_only")
        ]
        assert len(direction_reads) == 1
        assert set(direction_reads[0]) == {"axis_display", "direction_only"}
    reopened = evaluated(
        FeatureGraph(
            fixture[0],
            Recipe.model_validate_json(
                Recipe.model_validate(state["recipe"]).model_dump_json()
            ),
        )
    )
    assert reopened["recipe"] == state["recipe"]
    np.testing.assert_allclose(
        reopened["results"]["left"]["plane_equation"],
        state["results"]["left"]["plane_equation"],
        atol=1e-8,
    )


def test_nested_binding_aliases_remain_authored_and_track_changed_membership(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    planes(fixture)
    fixture[1].insert(
        1,
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        },
    )
    graph = graph_with(
        fixture,
        upstream(),
        downstream("binding"),
        {
            "id": "nested",
            "operation": "direction_alignment",
            "reference": common("downstream"),
            "reference_use": "binding",
            "directions": [reference("axis")],
        },
    )
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    assert state["recipe"]["nodes"][-1]["reference"] == common("downstream")
    payload = graph.export_recipe()
    source = next(node for node in payload["nodes"] if node["id"] == "upstream")
    source["directions"] = [reference("cap", "plane"), reference("axis")]
    _ = graph.replace(Recipe.model_validate(payload), str(graph.snapshot()["token"]))
    updated = evaluated(graph)
    normal = np.asarray(updated["results"]["left"]["plane_equation"][:3])
    assert abs(normal @ expected_normal(fixture, ("cap", "left"))) == pytest.approx(
        1, abs=1e-9
    )
    assert updated["recipe"]["nodes"][-2]["reference"] == common()
    raw = next(
        item
        for item in updated["input_catalogue"]
        if item["reference"] == common("downstream")
    )
    assert {
        item["feature"] for item in raw["properties"]["direction"]["binding_aliases"]
    } == {"cap", "axis", "left"}


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("use", ["binding", "geometry"])
def test_coaxial_common_axis_publishes_actual_line_and_direction_alias(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    named: bool,
    use: str,
) -> None:
    workspace, nodes = fixture
    ids = next(node["ids"] for node in nodes if node["id"] == "right_selection")
    workspace.local[ids] -= np.array([10, 1, 0])
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
        "reference": common("coaxial", "axis"),
        "reference_use": use,
        "directions": [reference("cap", "plane")],
    }
    graph = graph_with(
        fixture,
        group,
        relation,
        *(
            [{"id": "joint", "operation": "joint_fit", "constraints": ["alignment"]}]
            if named
            else []
        ),
    )
    state = evaluated(graph)
    assert not state["errors"], state["errors"]
    catalogue = next(
        item
        for item in state["input_catalogue"]
        if item["reference"] == common("coaxial", "axis")
    )
    assert (
        catalogue["ownership"] == "relationship"
        and catalogue["parameter_owner"] is None
    )
    assert catalogue["properties"]["direction"]["binding_aliases"] == [
        reference("axis")
    ]
    assert not catalogue["properties"]["axis_line"]["binding_eligible"]
    assert compatibility_reason(catalogue, "coaxial_axis") is not None
    by_id = {node.id: node for node in Recipe.model_validate(state["recipe"]).nodes}
    exact = OutputReference(
        feature="coaxial",
        output="axis",
        context="joint" if named and use == "binding" else "coaxial",
    )
    output = resolve_output(
        exact,
        by_id,
        state["results"],
    )
    left = revolution_geometry(
        state["results"]["joint"]["surfaces"]["left"]
        if named and use == "binding"
        else state["results"]["left"]
    )
    np.testing.assert_allclose(
        np.cross(np.asarray(output["point_display"]) - left["origin"], left["axis"]),
        0,
        atol=1e-8,
    )
    assert abs(np.asarray(output["axis_display"]) @ left["axis"]) == pytest.approx(
        1, abs=1e-9
    )
    line = resolve_property(
        exact,
        "axis_line",
        by_id,
        state["results"],
    )
    assert set(line["value"]) == {"point", "direction"}


def test_alias_cycle_is_structural_and_never_selects_an_arbitrary_member() -> None:
    recipe = Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": "a",
                    "label": "a",
                    "operation": "direction_alignment",
                    "directions": [common("b"), common("b")],
                },
                {
                    "id": "b",
                    "label": "b",
                    "operation": "direction_alignment",
                    "directions": [common("a"), common("a")],
                },
            ],
            "output": "b",
        }
    )
    with pytest.raises(ValueError, match=r"alias.*cycle"):
        _ = normalize_endpoint(
            common("a"),
            {node.id: node for node in recipe.nodes},
            "relationship_direction_targets",
        )


def test_logical_alias_validation_compiles_only_physical_owners(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    planes(fixture)
    graph = graph_with(fixture, upstream(), downstream("binding"))
    recipe = Recipe.model_validate(graph.export_recipe())
    nodes = {node.id: node for node in recipe.nodes}
    endpoint = normalize_endpoint(
        common(), nodes, "relationship_direction_reference", use="binding"
    )
    assert (
        endpoint.reference == OutputReference.model_validate(common())
        and endpoint.owner is None
        and endpoint.is_binding
    )
    node = nodes["downstream"]
    assert isinstance(node, DirectionAlignment)
    physical = plane_relationship_endpoints(node, nodes)
    assert {endpoint.owner for endpoint in physical} == {"cap", "other", "left"}
    catalogue = output_catalogue(recipe)
    choices = input_choices(
        recipe, requirement="relationship_direction_reference", catalogue=catalogue
    )
    assert any(item["reference"] == common() for item in choices["choices"])


def test_fixed_common_read_back_into_its_own_component_rejects_a_real_cycle(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    planes(fixture)
    with pytest.raises(ValueError, match="geometry influence cycle"):
        _ = graph_with(
            fixture,
            upstream(),
            {**downstream("geometry"), "directions": [reference("cap", "plane")]},
        )


def test_fixed_only_common_property_cannot_invent_adjustable_owners(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {"id": "first_axis", "operation": "axis", "source_fit": "left"},
        {"id": "second_axis", "operation": "axis", "source_fit": "right"},
        {
            "id": "upstream",
            "operation": "direction_alignment",
            "directions": [reference("first_axis"), reference("second_axis")],
        },
    )
    snapshot = cast(dict[str, Any], graph.snapshot())
    descriptor = next(
        item for item in snapshot["input_catalogue"] if item["reference"] == common()
    )
    assert descriptor["uses"] == ["geometry"]
    assert descriptor["properties"]["direction"]["binding_aliases"] == []
    recipe = Recipe.model_validate(graph.export_recipe())
    with pytest.raises(ValueError, match="read-only"):
        _ = normalize_endpoint(
            common(),
            {node.id: node for node in recipe.nodes},
            "relationship_direction_reference",
            use="binding",
        )


def test_binding_alias_dependencies_and_availability_are_independent_of_fixed_publication(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    planes(fixture)
    graph = graph_with(fixture, upstream(), downstream("binding"))
    recipe = Recipe.model_validate(graph.export_recipe())
    catalogue = output_catalogue(recipe, {node.id: "failed" for node in recipe.nodes})
    binding = input_choices(
        recipe,
        requirement="relationship_direction_reference",
        catalogue=catalogue,
        consumer_id="downstream",
        use="binding",
    )
    source = next(item for item in binding["choices"] if item["reference"] == common())
    assert "left_selection" not in source["binding_dependencies"]
    assert "upstream" in source["binding_dependencies"]
    fixed = input_choices(
        recipe,
        requirement="relationship_direction_reference",
        catalogue=catalogue,
        use="geometry",
    )
    assert not any(item["reference"] == common() for item in fixed["choices"])
    assert (
        next(item for item in fixed["unavailable"] if item["reference"] == common())[
            "availability"
        ]
        == "failed"
    )


@pytest.mark.parametrize("use", ["binding", "geometry"])
def test_named_joint_keeps_common_alias_ownership_and_original_relationship_constraints(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    use: str,
) -> None:
    planes(fixture)
    graph = graph_with(
        fixture,
        upstream(),
        downstream(use),
        {"id": "joint", "operation": "joint_fit", "constraints": ["downstream"]},
    )
    state = evaluated(graph, "joint")
    assert not state["errors"], state["errors"]
    normal = np.asarray(
        state["results"]["joint"]["surfaces"]["left"]["plane_equation"][:3]
    )
    assert abs(
        normal
        @ expected_normal(
            fixture, ("cap", "other", "left") if use == "binding" else ("cap", "other")
        )
    ) == pytest.approx(1, abs=1e-9)
    if use == "geometry":
        assert set(state["results"]["joint"]["surfaces"]) == {"left"}
        return
    exact = OutputReference(feature="upstream", output="direction", context="joint")
    descriptor = next(
        item
        for item in state["input_catalogue"]
        if item["reference"] == exact.model_dump()
    )
    assert descriptor["availability"] == "ready", descriptor["reason"]
    assert descriptor["uses"] == ["geometry"]
    assert descriptor["properties"]["direction"]["binding_aliases"] == []
    value = resolve_output(
        exact,
        {node.id: node for node in Recipe.model_validate(state["recipe"]).nodes},
        state["results"],
    )
    assert set(value) == {"axis_display"}
    assert abs(np.asarray(value["axis_display"]) @ normal) == pytest.approx(1, abs=1e-9)


def test_overlapping_alias_dag_expands_each_common_output_once_per_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.relationship_outputs as outputs

    groups = 24
    payload: list[dict[str, Any]] = [
        {
            "id": name,
            "label": name,
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        }
        for name in ("a", "b")
    ]
    for index in range(groups):
        members = (
            [reference("a"), reference("b")]
            if index < 2
            else [common(f"r{index - 1}"), common(f"r{index - 2}")]
        )
        payload.append(
            {
                "id": f"r{index}",
                "label": f"r{index}",
                "operation": "direction_alignment",
                "directions": members,
            }
        )
    recipe = Recipe.model_validate(
        {"schema_version": 2, "nodes": payload, "output": f"r{groups - 1}"}
    )
    nodes = {node.id: node for node in recipe.nodes}
    target = nodes[f"r{groups - 1}"]
    original_aliases = outputs._uncached_aliases  # pyright: ignore[reportPrivateUsage]
    expansions = 0

    def counted_aliases(*args: Any, **kwargs: Any) -> Any:
        nonlocal expansions
        expansions += 1
        assert expansions <= groups, (
            "overlapping aliases must not recursively expand shared DAG branches again"
        )
        return original_aliases(*args, **kwargs)

    monkeypatch.setattr(outputs, "_uncached_aliases", counted_aliases)
    cache: dict[str, tuple[OutputReference, ...]] = {}
    endpoint = normalize_endpoint(
        common(target.id), nodes, "relationship_direction_targets", _alias_cache=cache
    )
    assert expansions == groups
    assert {ref.feature for ref in endpoint.binding_aliases} == {"a", "b"}
    assert isinstance(cache[reference_key(endpoint.reference)], tuple)
    with pytest.raises(ValueError, match="aliases contain a cycle"):
        _ = outputs.enrich_aliases(
            declared_output_specs(target)[0],
            target,
            nodes,
            frozenset({reference_key(endpoint.reference)}),
            cache,
        )
    monkeypatch.setattr(outputs, "_uncached_aliases", original_aliases)

    original_geometry = outputs._uncached_common_geometry  # pyright: ignore[reportPrivateUsage]
    projections = 0

    def counted_geometry(*args: Any, **kwargs: Any) -> Any:
        nonlocal projections
        projections += 1
        assert projections <= groups, (
            "common publication projection must preserve shared DAG branches"
        )
        return original_geometry(*args, **kwargs)

    monkeypatch.setattr(outputs, "_uncached_common_geometry", counted_geometry)
    raw = {
        "resolved": {
            name: {"axis_display": [0, 0, 1], "point_display": [0, 0, 0]}
            for name in ("a", "b")
        }
    }
    value = outputs.common_geometry(target, raw, nodes, {target.id: raw})
    assert projections == groups and value == {"axis_display": [0, 0, 1]}


def test_snapshot_common_outputs_preserve_all_existing_derived_metadata(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planes(fixture)
    graph = graph_with(fixture, upstream(), downstream("binding"))
    _ = evaluated(graph)
    # Canonical input projections intentionally omit selection coverage, while
    # the raw derived publication retains it for diagnostics and inspection.
    store = graph._derived  # pyright: ignore[reportPrivateUsage]
    store.setdefault("cap_selection", {})["coverage"] = {"fraction": 1.0}
    input_values = graph._input_values  # pyright: ignore[reportPrivateUsage]

    def projected_values() -> dict[str, dict[str, Any]]:
        values = input_values()
        values["cap_selection"] = {
            key: value
            for key, value in values["cap_selection"].items()
            if key != "coverage"
        }
        return values

    monkeypatch.setattr(graph, "_input_values", projected_values)
    state = cast(dict[str, Any], graph.snapshot())
    original = deepcopy(graph._derived)  # pyright: ignore[reportPrivateUsage]
    for key, value in original.items():
        assert {
            name: item
            for name, item in state["derived"][key].items()
            if name != "common_outputs"
        } == value
    assert state["derived"]["upstream"]["common_outputs"]["direction"]["axis_display"]
    assert state["derived"]["cap_selection"]["coverage"] == {"fraction": 1.0}
