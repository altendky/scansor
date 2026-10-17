"""Reference-plane relationships retain declared roles and publication contexts."""

import importlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.fit_relationship_component import RelationshipFailure
from experiments.nozzle_browser import NATIVE_SCAN_LIMITS
from experiments.scan_workspace import ScanWorkspace, prepare_scan
from experiments.selection_growth import surface_distance
from experiments.surface_primitives import primitive
from tests.test_relationship_composition import composed_graph as composed_graph

FIXTURE = Path(__file__).with_name("fixtures") / "shared-axis-nozzle.json"


def captured_recipe() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text()))


@pytest.fixture(scope="module")
def workspace() -> ScanWorkspace:
    source = captured_recipe()["nodes"][0]
    path = (
        Path("examples/nozzle-bayonette-simplified") / source["descriptor"]["filename"]
    )
    with path.open("rb") as stream:
        result = prepare_scan(
            stream, source["descriptor"]["filename"], NATIVE_SCAN_LIMITS
        )
    assert result.descriptor is not None
    assert result.descriptor.model_dump() == source["descriptor"]
    return result


def plane_reference(feature: str, context: str | None = None) -> dict[str, str]:
    return {"feature": feature, "output": "plane", "context": context or feature}


def parallel_recipe() -> dict[str, Any]:
    recipe = captured_recipe()
    axis = next(node for node in recipe["nodes"] if node["operation"] == "axis")
    planes = [
        node
        for node in recipe["nodes"]
        if node["operation"] == "fit" and node["kind"] == "plane"
    ]
    for plane in planes:
        plane["axis"] = None
    datums: list[dict[str, Any]] = [
        {
            "id": key,
            "label": key,
            "operation": "reference_plane",
            "axis": axis["id"],
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": offset,
        }
        for key, offset in (("base", 0), ("other_base", 3))
    ]
    recipe["nodes"][2:2] = datums
    recipe["nodes"].append(
        {
            "id": "parallel",
            "label": "Parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": [plane_reference(node["id"]) for node in [*datums, *planes]],
        }
    )
    recipe["output"] = "parallel"
    return recipe


def evaluate(graph: FeatureGraph[ScanWorkspace]) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )


def assert_observations_unchanged(
    workspace: ScanWorkspace, state: dict[str, Any]
) -> None:
    nodes = {node["id"]: node for node in state["recipe"]["nodes"]}
    for node in nodes.values():
        if node["operation"] != "fit":
            continue
        ids = sorted(
            {vertex for key in node["selections"] for vertex in nodes[key]["ids"]}
        )
        result = state["results"][node["id"]]
        assert result["ids"] == ids
        distances = surface_distance(workspace.local[ids], result)[0]
        np.testing.assert_allclose(result["residuals"], distances, atol=1e-10)
        weights = workspace.data.weights[ids]
        assert result["weighted_rms"] == pytest.approx(
            np.sqrt(weights @ distances**2 / weights.sum()), abs=1e-10
        )


def test_multiple_datums_and_fits_share_orientation_without_activating_unused_offsets(
    workspace: ScanWorkspace,
) -> None:
    graph = FeatureGraph(workspace, Recipe.model_validate(parallel_recipe()))
    before = graph.snapshot()["recipe"]
    state = evaluate(graph)
    assert state["recipe"] == before
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert not graph.needs_evaluation(str(state["token"]), all_actions=True)
    axis_node = next(
        node for node in state["recipe"]["nodes"] if node["operation"] == "axis"
    )
    axis = state["results"][axis_node["id"]]
    direction = np.asarray(axis["axis_display"])
    anchor = np.asarray(axis["point_display"])
    for key, offset in (("base", 0), ("other_base", 3)):
        datum = state["results"][key]
        equation = np.asarray(datum["plane_equation"])
        np.testing.assert_allclose(equation[:3], direction, atol=1e-8)
        assert equation[3] - equation[:3] @ anchor == pytest.approx(offset, abs=1e-8)
    fits = [node for node in state["recipe"]["nodes"] if node["operation"] == "fit"]
    plane_offsets: list[float] = []
    for node in fits:
        result = state["results"][node["id"]]
        if node["kind"] == "plane":
            equation = np.asarray(result["plane_equation"])
            assert abs(equation[:3] @ direction) == pytest.approx(1, abs=1e-9)
            plane_offsets.append(equation[3])
        else:
            geometry = result["analytic"]
            assert abs(np.asarray(geometry["axis"]) @ direction) == pytest.approx(
                1, abs=1e-9
            )
            np.testing.assert_allclose(
                np.cross(np.asarray(geometry["origin"]) - anchor, direction),
                0,
                atol=1e-8,
            )
    assert abs(plane_offsets[0] - plane_offsets[1]) > 0.1
    assert_observations_unchanged(workspace, state)
    assert (
        state["relationship_contracts"]["parallel_planes"]["roles"]["participants"]
        == "relationship_plane_orientation"
    )
    restored = FeatureGraph(
        workspace, Recipe.model_validate_json(json.dumps(state["recipe"]))
    )
    reopened = evaluate(restored)
    assert reopened["recipe"] == state["recipe"]
    for node in fits:
        assert reopened["results"][node["id"]]["weighted_rms"] == pytest.approx(
            state["results"][node["id"]]["weighted_rms"]
        )


def fixed_recipe(
    relation: str = "parallel", *, incompatible_axis: bool = False
) -> dict[str, Any]:
    source = captured_recipe()["nodes"][0]
    nodes = [source]
    for key, xyz in (("origin", [0, 0, 0]), ("z", [0, 0, 1]), ("x", [1, 0, 0])):
        nodes.append(
            {"id": key, "label": key, "operation": "point", "initial_coordinates": xyz}
        )
    for key, points in (
        ("first_axis", ["origin", "z"]),
        ("second_axis", ["origin", "x"] if incompatible_axis else ["z", "origin"]),
    ):
        nodes.append(
            {"id": key, "label": key, "operation": "axis", "source_points": points}
        )
    for key, axis, offset in (("first", "first_axis", 1), ("second", "second_axis", 4)):
        nodes.append(
            {
                "id": key,
                "label": key,
                "operation": "reference_plane",
                "axis": axis,
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": offset,
            }
        )
    nodes.append(
        {
            "id": "relationship",
            "label": "Relationship",
            "operation": "plane_relationship",
            "relation": relation,
            "surfaces": [plane_reference("first"), plane_reference("second")],
        }
    )
    return {"schema_version": 2, "nodes": nodes, "output": "relationship"}


def test_datum_only_parallel_validates_fixed_geometry_and_preserves_placement(
    workspace: ScanWorkspace,
) -> None:
    graph = FeatureGraph(workspace, Recipe.model_validate(fixed_recipe()))
    state = evaluate(graph)
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert state["results"]["first"]["offset"] == 1
    assert state["results"]["second"]["offset"] == 4
    first = np.asarray(state["results"]["first"]["plane_equation"])
    second = np.asarray(state["results"]["second"]["plane_equation"])
    assert first[:3] @ second[:3] == pytest.approx(-1)
    assert state["derived"]["relationship"]["weighted_rms"] == 0


@pytest.mark.parametrize(
    ("relation", "incompatible_axis"), [("parallel", True), ("coincident", False)]
)
def test_conflicting_fixed_references_fail_without_changing_their_geometry(
    workspace: ScanWorkspace, relation: str, incompatible_axis: bool
) -> None:
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate(
            fixed_recipe(relation, incompatible_axis=incompatible_axis)
        ),
    )
    with pytest.raises(RelationshipFailure) as caught:
        _ = evaluate(graph)
    assert caught.value.code == "infeasible-constraints"
    state = cast(dict[str, Any], graph.snapshot())
    assert state["states"]["relationship"] == "failed"
    assert state["results"]["first"]["offset"] == 1
    assert state["results"]["second"]["offset"] == 4


def test_exact_contexts_of_the_same_feature_remain_fixed_distinct_reads(
    workspace: ScanWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = captured_recipe()
    planes = [
        node
        for node in payload["nodes"]
        if node["operation"] == "fit" and node["kind"] == "plane"
    ]
    selection_ids = {key for node in planes for key in node["selections"]}
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["operation"] == "source" or node["id"] in selection_ids
    ]
    for plane in planes:
        plane["axis"] = None
    payload["nodes"].extend(planes)
    for key, relation in (
        ("earlier_parallel", "parallel"),
        ("earlier_coincident", "coincident"),
    ):
        payload["nodes"].append(
            {
                "id": key,
                "label": key,
                "operation": "plane_relationship",
                "relation": relation,
                "surfaces": [plane["id"] for plane in planes],
            }
        )
    references = [
        plane_reference(planes[0]["id"], context)
        for context in ("earlier_parallel", "earlier_coincident")
    ]
    payload["nodes"].append(
        {
            "id": "fixed_reads",
            "label": "Fixed reads",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": references,
        }
    )
    payload["output"] = "fixed_reads"
    component = importlib.import_module("experiments.fit_relationship_component")
    original = component.fit_relationship_component
    observation_counts: list[int] = []

    def observe(surfaces: list[Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        observation_counts.append(len(surfaces))
        return cast(dict[str, Any], original(surfaces, *args, **kwargs))

    monkeypatch.setattr(component, "fit_relationship_component", observe)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    state = evaluate(graph)
    assert not state["errors"]
    assert state["recipe"]["nodes"][-1]["surfaces"] == references
    assert observation_counts == [0]
    np.testing.assert_allclose(
        state["derived"]["earlier_parallel"]["surfaces"][planes[0]["id"]][
            "plane_equation"
        ],
        state["derived"]["earlier_coincident"]["surfaces"][planes[0]["id"]][
            "plane_equation"
        ],
    )


def test_changing_orientation_only_datum_offset_preserves_observation_fit(
    workspace: ScanWorkspace,
) -> None:
    graph = FeatureGraph(workspace, Recipe.model_validate(parallel_recipe()))
    initial = evaluate(graph)
    payload = initial["recipe"]
    next(node for node in payload["nodes"] if node["id"] == "base")["offset"] = 2
    _ = graph.replace(Recipe.model_validate(payload), str(initial["token"]))
    changed = evaluate(graph)
    assert not changed["errors"]
    axis_id = next(
        node["id"] for node in payload["nodes"] if node["operation"] == "axis"
    )
    axis = changed["results"][axis_id]
    equation = np.asarray(changed["results"]["base"]["plane_equation"])
    assert equation[3] - equation[:3] @ np.asarray(
        axis["point_display"]
    ) == pytest.approx(2, abs=1e-8)
    for node in payload["nodes"]:
        if node["operation"] == "fit":
            assert changed["results"][node["id"]]["weighted_rms"] == pytest.approx(
                initial["results"][node["id"]]["weighted_rms"], abs=1e-9
            )
    assert_observations_unchanged(workspace, changed)


def fixed_axis_context_chain(
    *, reuse_datum: bool, free_axis: bool = False
) -> dict[str, Any]:
    payload = captured_recipe()
    source = payload["nodes"][0]
    side = next(
        node
        for node in payload["nodes"]
        if node["operation"] == "fit" and node["kind"] == "cone"
    )
    end = next(
        node
        for node in payload["nodes"]
        if node["operation"] == "fit" and node["kind"] == "plane"
    )
    side["axis"] = end["axis"] = None
    selected = {key for fit in (side, end) for key in fit["selections"]}
    selections = {
        node["id"]: node for node in payload["nodes"] if node["id"] in selected
    }
    axis: dict[str, Any] = {"id": "fixed_axis", "label": "Axis", "operation": "axis"}
    if free_axis:
        axis["initial_parameters"] = [0.0, 0, 0, 0]
    else:
        axis["source_fit"] = side["id"]
    datums = [
        {
            "id": key,
            "label": key,
            "operation": "reference_plane",
            "axis": "fixed_axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": offset,
        }
        for key, offset in (("first_datum", 0), ("third_datum", 3))
    ]
    nodes = [
        source,
        *[selections[key] for key in side["selections"]],
        side,
        axis,
        *datums,
        *[selections[key] for key in end["selections"]],
        end,
    ]
    nodes.extend(
        [
            {
                "id": "first_publication",
                "label": "First publication",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [end["id"], "first_datum"],
            },
            {
                "id": "later_read",
                "label": "Later read",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [
                    plane_reference(end["id"], "first_publication"),
                    plane_reference("first_datum" if reuse_datum else "third_datum"),
                ],
            },
        ]
    )
    return {"schema_version": 2, "nodes": nodes, "output": "later_read"}


@pytest.mark.parametrize("reuse_datum", [False, True])
def test_fixed_axis_and_datum_anchors_do_not_join_independent_contexts(
    workspace: ScanWorkspace,
    monkeypatch: pytest.MonkeyPatch,
    reuse_datum: bool,
) -> None:
    component = importlib.import_module("experiments.fit_relationship_component")
    original = component.fit_relationship_component
    observation_counts: list[int] = []

    def observe(surfaces: list[Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
        observation_counts.append(len(surfaces))
        return cast(dict[str, Any], original(surfaces, *args, **kwargs))

    monkeypatch.setattr(component, "fit_relationship_component", observe)
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate(fixed_axis_context_chain(reuse_datum=reuse_datum)),
    )
    state = evaluate(graph)
    assert not state["errors"]
    assert observation_counts == [1, 0]
    assert state["derived"]["later_read"]["weighted_rms"] == 0
    assert set(state["derived"]["first_publication"]["resolved"]) == {
        node["id"]
        for node in state["recipe"]["nodes"]
        if node["operation"] == "fit" and node["kind"] == "plane"
    }
    for key, output in (
        ("fixed_axis", "axis"),
        ("first_datum", "plane"),
        ("third_datum", "plane"),
    ):
        contexts = {
            record["reference"]["context"]
            for record in state["input_catalogue"]
            if record["reference"]["feature"] == key
            and record["reference"]["output"] == output
        }
        assert contexts == {key}
    assert not graph.needs_evaluation(str(state["token"]), all_actions=True)


def test_exact_read_back_into_shared_free_axis_still_rejects_a_real_cycle(
    workspace: ScanWorkspace,
) -> None:
    with pytest.raises(ValueError, match="geometry influence cycle"):
        _ = FeatureGraph(
            workspace,
            Recipe.model_validate(
                fixed_axis_context_chain(reuse_datum=False, free_axis=True)
            ),
        )


def test_source_fit_datum_remains_fixed_in_a_later_independent_plane_solve(
    composed_graph: FeatureGraph,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    selection = deepcopy(
        next(node for node in payload["nodes"] if node["id"] == "side_selection_0")
    )
    selection.update(id="source_selection", label="Source selection")
    selection["ids"] = [
        vertex
        for index, vertex in enumerate(selection["ids"])
        if 2 * (index // 24) <= index % 24 < 12 + 2 * (index // 24)
    ]
    source = deepcopy(next(node for node in payload["nodes"] if node["id"] == "side_0"))
    source.update(
        id="source_cylinder",
        label="Source cylinder",
        axis=None,
        selections=[selection["id"]],
    )
    radius = next(node for node in payload["nodes"] if node["id"] == "same_radii")
    position = payload["nodes"].index(radius)
    payload["nodes"][position:position] = [
        selection,
        source,
        {
            "id": "source_axis",
            "label": "Source axis",
            "operation": "axis",
            "source_fit": source["id"],
        },
        {
            "id": "source_datum",
            "label": "Source datum",
            "operation": "reference_plane",
            "axis": "source_axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 2.0,
        },
    ]
    radius["surfaces"].append(source["id"])
    payload["nodes"].extend(
        [
            {
                "id": "downstream_plane",
                "label": "Downstream plane",
                "operation": "fit",
                "kind": "plane",
                "selections": ["plate_selection"],
            },
            {
                "id": "downstream_relation",
                "label": "Downstream relation",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["source_datum", "downstream_plane"],
            },
        ]
    )
    payload["output"] = "downstream_relation"
    component = importlib.import_module("experiments.fit_relationship_component")
    original = component.fit_relationship_component
    calls: list[tuple[list[str], dict[str, bool]]] = []

    def observe(
        surfaces: list[Any], axes: dict[str, Any], *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        calls.append(
            (
                [surface.id for surface in surfaces],
                {key: axis.free for key, axis in axes.items()},
            )
        )
        return cast(dict[str, Any], original(surfaces, axes, *args, **kwargs))

    monkeypatch.setattr(component, "fit_relationship_component", observe)
    graph = FeatureGraph(composed_graph.workspace, Recipe.model_validate(payload))
    state = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert not state["errors"]
    downstream = [call for call in calls if "downstream_plane" in call[0]]
    assert downstream == [(["downstream_plane"], {"source_axis": False})]
    assert sum(source["id"] in members for members, _ in calls) == 1
    assert "source_axis" not in state["derived"]["downstream_relation"]["resolved"]
    assert "source_datum" not in state["derived"]["downstream_relation"]["resolved"]
    resolved_source = primitive(state["results"][source["id"]])
    np.testing.assert_allclose(
        state["results"]["source_axis"]["axis_display"],
        resolved_source["axis"],
        atol=1e-10,
    )
    equation = np.asarray(state["results"]["downstream_plane"]["plane_equation"])
    assert abs(equation[:3] @ np.asarray(resolved_source["axis"])) == pytest.approx(
        1, abs=1e-10
    )
