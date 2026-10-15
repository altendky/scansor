"""Declared compatibility is independent of evaluation and UI provider filters."""

import copy
import json
from typing import Any

import numpy as np
import pytest

from experiments.feature_graph import Recipe
from experiments.feature_inputs import (
    OutputReference,
    compatibility_reason,
    input_choices,
    output_catalogue,
    resolve_output,
    validate_output_reference,
    validate_output_references,
)


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
                    "id": "one",
                    "label": "One",
                    "operation": "selection",
                    "source": "scan",
                    "ids": [0],
                },
                {
                    "id": "many",
                    "label": "Many",
                    "operation": "selection",
                    "source": "scan",
                    "ids": [0, 1],
                },
                {
                    "id": "point",
                    "label": "Point",
                    "operation": "point",
                    "initial_coordinates": [0, 0, 0],
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
                {
                    "id": "sphere",
                    "label": "Sphere",
                    "operation": "fit",
                    "kind": "sphere",
                    "selections": ["many"],
                },
                {
                    "id": "plane",
                    "label": "Plane",
                    "operation": "fit",
                    "kind": "plane",
                    "selections": ["many"],
                },
            ],
            "output": "plane",
        }
    )


def ready_catalogue() -> list[dict[str, Any]]:
    graph = recipe()
    values: dict[str, Any] = {
        "point": {"point_display": [1, 2, 3]},
        "axis": {"axis_display": [0, 0, 1], "point_display": [0, 0, 0]},
        "datum": {
            "plane_equation": [0, 1, 0, 0],
            "point_display": [0, 0, 0],
            "normal_display": [0, 1, 0],
        },
        "sphere": {"parameters": [4, 5, 6, 2]},
        "plane": {"parameters": [0, 0, 1, 3]},
    }
    return output_catalogue(
        graph,
        dict.fromkeys((n.id for n in graph.nodes), "ready"),
        values,
        frame="frame",
        coordinates=np.array([[2, 3, 4], [5, 6, 7]]),
    )


def test_point_and_plane_outputs_have_explicit_meaning_and_no_centroid() -> None:
    catalogue = ready_catalogue()
    points = input_choices(recipe(), requirement="point", catalogue=catalogue)
    assert {
        (item["reference"]["feature"], item["reference"]["output"])
        for item in points["choices"]
    } == {("one", "single_node"), ("point", "point"), ("sphere", "center")}
    one = next(
        item for item in points["choices"] if item["reference"]["feature"] == "one"
    )
    assert one["preview"] == {"point_display": [2.0, 3.0, 4.0]}
    many = next(
        item for item in points["unavailable"] if item["reference"]["feature"] == "many"
    )
    assert "exactly one" in many["reason"]
    planes = input_choices(recipe(), requirement="plane", catalogue=catalogue)
    assert {item["reference"]["feature"] for item in planes["choices"]} == {
        "datum",
        "plane",
    }


def test_availability_changes_do_not_change_reference_identity() -> None:
    graph = recipe()
    for state in ("unevaluated", "running", "stale", "failed", "blocked"):
        catalogue = output_catalogue(graph, {"sphere": state})
        sphere = next(
            item for item in catalogue if item["reference"]["feature"] == "sphere"
        )
        assert sphere["reference"] == {
            "feature": "sphere",
            "output": "center",
            "context": "sphere",
        }
        assert sphere["availability"] == state
        assert sphere["reason"]
        assert sphere["preview"] is None
    # Structural validity does not demand numerical availability.
    assert validate_output_reference(
        OutputReference(feature="sphere", output="center", context="sphere"),
        graph,
        "point",
    )


def test_readonly_geometry_does_not_become_a_constrainable_fit() -> None:
    choices = input_choices(
        recipe(), requirement="constrainable_plane", catalogue=ready_catalogue()
    )
    assert {item["reference"]["feature"] for item in choices["choices"]} == {"plane"}
    datum = next(
        item
        for item in choices["unavailable"]
        if item["reference"]["feature"] == "datum"
    )
    assert "parameter owner" in datum["reason"]
    assert datum["compatible"] is False
    sphere = next(
        item for item in ready_catalogue() if item["reference"]["feature"] == "sphere"
    )
    assert compatibility_reason(sphere, "constrainable_point")
    assert compatibility_reason(sphere, "point", source="other")
    assert compatibility_reason(sphere, "point", frame="other")


def test_multiple_outputs_and_requirement_cardinality_are_declared() -> None:
    catalogue = ready_catalogue()
    assert {
        item["reference"]["output"]
        for item in catalogue
        if item["reference"]["feature"] == "sphere"
    } == {"surface", "center", "radius", "observations"}
    assert {
        item["reference"]["output"]
        for item in catalogue
        if item["reference"]["feature"] == "one"
    } == {"observations", "single_node"}
    with pytest.raises(ValueError, match="cardinality"):
        _ = validate_output_references(
            [OutputReference(feature="plane", output="plane", context="plane")],
            recipe(),
            "constrainable_plane",
        )


@pytest.mark.parametrize(
    "output,context", [("centroid", "one"), ("center", "missing"), ("plane", "sphere")]
)
def test_invalid_output_or_context_is_rejected(output: str, context: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        _ = validate_output_reference(
            OutputReference(feature="sphere", output=output, context=context),
            recipe(),
            "point",
        )


def test_exact_context_reader_does_not_use_another_ready_publisher() -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    values: dict[str, Any] = {
        "plane": {"parameters": [0, 0, 1, 1]},
        "first": {"surfaces": {"plane": {"parameters": [1, 0, 0, 2]}}},
        "second": {"surfaces": {"plane": {"parameters": [0, 1, 0, 3]}}},
    }
    ref = OutputReference(feature="plane", output="plane", context="first")
    assert resolve_output(ref, nodes, values)["normal_display"] == [1.0, 0.0, 0.0]
    values["second"]["surfaces"]["plane"]["parameters"] = [0, 0, 1, 8]
    assert resolve_output(ref, nodes, values)["normal_display"] == [1.0, 0.0, 0.0]


@pytest.mark.parametrize("context", [None, ""])
def test_direct_output_references_have_one_identity(context: str | None) -> None:
    assert OutputReference(
        feature="point", output="point", context=context
    ) == OutputReference(feature="point", output="point", context="point")
    payload = recipe().model_dump()
    payload["nodes"].append(
        {
            "id": "same",
            "label": "Same",
            "operation": "axis",
            "source_points": [
                {"feature": "point", "output": "point", "context": context},
                {"feature": "point", "output": "point", "context": "point"},
            ],
        }
    )
    with pytest.raises(ValueError, match="distinct points"):
        _ = Recipe.model_validate(payload)


@pytest.mark.parametrize(
    "parameters",
    [
        [11.0, -7, 3, 4, 99, -2, 8],
        [-5.0, 19, -0.5, 0.25, 7, 6.5, -0.1],
    ],
)
def test_legacy_joint_plane_output_decodes_normal_and_signed_offset(
    parameters: list[float],
) -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    values: dict[str, Any] = {
        "plane": {"parameters": [0, 0, 1, 123]},
        "joint": {"surfaces": {"plane": {"kind": "plane", "parameters": parameters}}},
        "other": {"surfaces": {"plane": {"parameters": [1, 0, 0, 999]}}},
    }
    before = copy.deepcopy(values)
    reference = OutputReference(feature="plane", output="plane", context="joint")
    actual = resolve_output(reference, nodes, values)
    # Independent chart definition: center/radius/taper are not plane slots.
    a, b, height = parameters[2], parameters[3], parameters[5]
    normal = np.array([a, b, 1]) / np.sqrt(a * a + b * b + 1)
    np.testing.assert_allclose(actual["normal_display"], normal, rtol=0, atol=1e-15)
    np.testing.assert_allclose(
        actual["plane_equation"], [*normal, height], rtol=0, atol=1e-15
    )
    np.testing.assert_allclose(
        actual["point_display"], height * normal, rtol=0, atol=2e-15
    )
    assert normal @ actual["point_display"] == pytest.approx(height, abs=2e-15)
    assert len(actual["plane_equation"]) == 4
    assert values == before
    actual["normal_display"][0] = actual["plane_equation"][0] = 999
    actual["point_display"][0] = 999
    assert values == before


@pytest.mark.parametrize("explicit", [False, True])
def test_four_coefficient_plane_keeps_its_nonunit_representation(
    explicit: bool,
) -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    equation = [2.0, -3, 6, 14]
    raw: dict[str, Any] = {"plane_equation" if explicit else "parameters": equation}
    if explicit:
        raw["parameters"] = ["stale legacy chart"]
    values = {"plane": raw}
    before = copy.deepcopy(values)
    actual = resolve_output(
        OutputReference(feature="plane", output="plane"), nodes, values
    )
    assert actual["plane_equation"] == equation
    assert actual["normal_display"] == equation[:3]
    np.testing.assert_allclose(
        actual["point_display"], 14 * np.array(equation[:3]) / 49
    )
    assert np.dot(equation[:3], actual["point_display"]) == pytest.approx(14)
    assert values == before


def test_datum_plane_normal_and_point_fallback_is_preserved() -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    raw = {"normal_display": [2.0, -3, 6], "point_display": [11.0, -7, 4]}
    values = {"datum": raw}
    actual = resolve_output(
        OutputReference(feature="datum", output="plane"), nodes, values
    )
    assert actual == {
        "normal_display": raw["normal_display"],
        "point_display": raw["point_display"],
        "plane_equation": [2.0, -3, 6, 67],
    }


@pytest.mark.parametrize("point", [[1, 2], [1, 2, float("nan")], [1, "invalid", 3]])
def test_malformed_datum_point_is_rejected_before_computing_plane(
    point: list[Any],
) -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    with pytest.raises(ValueError):
        _ = resolve_output(
            OutputReference(feature="datum", output="plane"),
            nodes,
            {"datum": {"normal_display": [0, 0, 1], "point_display": point}},
        )


@pytest.mark.parametrize(
    "raw",
    [
        {"parameters": []},
        {"parameters": [1, 2, 3]},
        {"parameters": [1, 2, 3, 4, 5]},
        {"parameters": [1, 2, 3, 4, 5, 6]},
        {"parameters": [1, 2, 3, 4, 5, 6, 7, 8]},
        {"parameters": [[11, -7, 3, 4, 99, -2, 8]]},
        {"parameters": [float("nan"), -7, 3, 4, 99, -2, 8]},
        {"parameters": [11, -7, 3, 4, 99, float("inf"), 8]},
        {"parameters": [0, 0, 0, 1]},
        {"parameters": [0, 0, 1, float("inf")]},
        {"plane_equation": [11, -7, 3, 4, 99, -2, 8]},
        {"plane_equation": [0, 0, 1, 2], "normal_display": [0, 1]},
        {"plane_equation": [0, 0, 1, 2], "normal_display": [0, 0, float("nan")]},
        {"plane_equation": [0, 0, 1, 2], "point_display": [1, 2]},
    ],
)
def test_malformed_plane_output_is_unavailable_without_mutating_source(
    raw: dict[str, Any],
) -> None:
    graph = recipe()
    nodes = {node.id: node for node in graph.nodes}
    values = {"plane": raw}
    before = json.dumps(values, sort_keys=True)
    with pytest.raises(ValueError, match="plane"):
        _ = resolve_output(
            OutputReference(feature="plane", output="plane"), nodes, values
        )
    assert json.dumps(values, sort_keys=True) == before
