"""Declared compatibility is independent of evaluation and UI provider filters."""

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
