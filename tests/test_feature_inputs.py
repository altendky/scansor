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


def test_direct_catalogue_outputs_identify_the_feature_without_changing_labels() -> (
    None
):
    graph = recipe()
    labels = {node.id: node.label for node in graph.nodes}
    outputs = output_catalogue(graph)
    for output in outputs:
        reference = output["reference"]
        assert reference["context"] == reference["feature"]
        assert output["feature_label"] == labels[reference["feature"]]
        assert output["producing_context"] == {
            "id": reference["context"],
            "label": labels[reference["feature"]],
            "kind": "feature",
        }
    assert (
        next(
            output["label"]
            for output in outputs
            if output["reference"]
            == {"feature": "plane", "output": "plane", "context": "plane"}
        )
        == "Plane — plane"
    )


@pytest.mark.parametrize("state", ["unevaluated", "stale"])
def test_pending_automatic_publications_share_human_readable_producing_contexts(
    state: str,
) -> None:
    payload = recipe().model_dump()
    nodes = payload["nodes"]
    next(node for node in nodes if node["id"] == "sphere")["point"] = "point"
    nodes.extend(
        [
            {
                "id": "lateral",
                "label": "Long hole feature at Occurrence 30",
                "operation": "fit",
                "kind": "cylinder",
                "axis": "axis",
                "selections": ["many"],
            },
            {
                "id": "free_plane",
                "label": "Independent datum",
                "operation": "reference_plane",
                "construction": "independent",
                "initial_normal": [0, 0, 1],
                "initial_angle_degrees": None,
            },
            {
                "id": "bound_plane",
                "label": "Bound plane",
                "operation": "fit",
                "kind": "plane",
                "reference_plane": "free_plane",
                "selections": ["many"],
            },
        ]
    )
    graph = Recipe.model_validate(payload)
    outputs = output_catalogue(
        graph, dict.fromkeys((node.id for node in graph.nodes), state)
    )
    expected = {
        "@axis/axis": ("Shared-axis solve · Axis", "axis_solve", {"axis", "lateral"}),
        "@point/point": (
            "Shared-point solve · Point",
            "point_solve",
            {"point", "sphere"},
        ),
        "@plane/free_plane": (
            "Shared-plane solve · Independent datum",
            "plane_solve",
            {"free_plane", "bound_plane"},
        ),
    }
    for context, (label, kind, aliases) in expected.items():
        publications = [
            output for output in outputs if output["reference"]["context"] == context
        ]
        assert aliases <= {output["reference"]["feature"] for output in publications}
        assert all(
            output["producing_context"] == {"id": context, "label": label, "kind": kind}
            for output in publications
        )
        assert all(
            output["availability"] == state and output["preview"] is None
            for output in publications
        )
    lateral = [
        output for output in outputs if output["reference"]["feature"] == "lateral"
    ]
    assert {output["reference"]["output"] for output in lateral} >= {
        "surface",
        "axis",
        "radius",
    }
    assert all(
        output["feature_label"] == "Long hole feature at Occurrence 30"
        for output in lateral
    )
    assert all(
        output["label"]
        == f"Long hole feature at Occurrence 30 — {output['reference']['output']}"
        for output in lateral
    )
    published_axis = next(
        output
        for output in outputs
        if output["reference"]
        == {"feature": "axis", "output": "axis", "context": "@axis/axis"}
    )
    direct_axis = next(
        output
        for output in outputs
        if output["reference"]
        == {"feature": "axis", "output": "axis", "context": "axis"}
    )
    assert published_axis["parameter_owner"] is None
    assert direct_axis["parameter_owner"] is not None
    assert direct_axis["producing_context"] == {
        "id": "axis",
        "label": "Axis",
        "kind": "feature",
    }


def test_named_publications_retain_exact_context_and_solve_label() -> None:
    payload = recipe().model_dump()
    payload["nodes"].extend(
        [
            {
                "id": "other_plane",
                "label": "Other plane",
                "operation": "fit",
                "kind": "plane",
                "selections": ["many"],
            },
            {
                "id": "relation",
                "label": "Parallel faces",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["plane", "other_plane"],
            },
            {
                "id": "alignment",
                "label": "Aligned faces",
                "operation": "direction_alignment",
                "directions": ["plane", "other_plane"],
            },
            {
                "id": "joint",
                "label": "Reviewed joint",
                "operation": "joint_fit",
                "constraints": ["relation", "alignment"],
            },
        ]
    )
    graph = Recipe.model_validate(payload)
    outputs = output_catalogue(graph)
    for context, label in [("relation", "Parallel faces"), ("joint", "Reviewed joint")]:
        publications = [
            output for output in outputs if output["reference"]["context"] == context
        ]
        assert {output["reference"]["feature"] for output in publications} >= {
            "plane",
            "other_plane",
        }
        assert all(
            output["producing_context"]
            == {"id": context, "label": label, "kind": "solve"}
            for output in publications
        )
        plane = next(
            output
            for output in publications
            if output["reference"]["feature"] == "plane"
        )
        assert plane["feature_label"] == "Plane"
        assert plane["label"] == f"Plane — plane · {label}"
        assert plane["reference"] == {
            "feature": "plane",
            "output": "plane",
            "context": context,
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
