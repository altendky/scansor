"""Typed fitted directions retain exact identities through real graph/archive replay."""

import io
import json
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.feature_inputs import OutputReference, resolve_output, resolve_property
from experiments.project_archive import ProjectArchiveLimits, write_project_archive
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import ScanLimits, prepare_scan
from tests.test_project_archive_worker import promote, success
from tests.test_scan_cone_fit import cone_grid
from tests.test_scan_cylinder_fit import cylinder_grid, strict_ply


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_fitted_axis_direction_constructs_frame_and_survives_actions_and_project(
    kind: str,
) -> None:
    points, normals, triangles = (
        cylinder_grid(noise=0.0001)
        if kind == "cylinder"
        else cone_grid(full=True, noise=0.0001)
    )
    raw = strict_ply(points, normals, triangles)
    original = BytesInput(raw, f"{kind}.ply")
    limits = ScanLimits(100_000, 1_000, 2_000, 10_000_000)
    workspace = prepare_scan(original, original.filename, limits)
    assert workspace.descriptor is not None
    reference = {"feature": "fitted", "output": "axis", "context": "fitted"}
    recipe = Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": "scan",
                    "label": "Scan",
                    "operation": "source",
                    "source_sha256": workspace.source_sha256,
                    "reference_sha256": workspace.reference_sha256,
                    "descriptor": workspace.descriptor.model_dump(),
                },
                {
                    "id": "observations",
                    "label": "Observations",
                    "operation": "selection",
                    "source": "scan",
                    "ids": list(range(len(points))),
                },
                {
                    "id": "fitted",
                    "label": "Fitted surface",
                    "operation": "fit",
                    "kind": kind,
                    "selections": ["observations"],
                },
                {
                    "id": "origin",
                    "label": "Origin",
                    "operation": "point",
                    "initial_coordinates": [1, 2, 3],
                },
                {
                    "id": "secondary",
                    "label": "Secondary",
                    "operation": "axis",
                    "initial_parameters": [0, 0, 0, 1],
                },
                {
                    "id": "frame",
                    "label": "Frame",
                    "operation": "frame",
                    "origin_point": {
                        "feature": "origin",
                        "output": "point",
                        "context": "origin",
                    },
                    "primary_reference": reference,
                    "primary_output_axis": "+Z",
                    "secondary_reference": {
                        "feature": "secondary",
                        "output": "axis",
                        "context": "secondary",
                    },
                    "secondary_output_axis": "+X",
                },
            ],
            "output": "frame",
        }
    )
    native = FeatureGraph(workspace, recipe)
    ready = cast(dict[str, Any], native.evaluate(str(native.snapshot()["token"])))
    assert not ready["errors"] and ready["states"]["frame"] == "ready"
    nodes = {node.id: node for node in recipe.nodes}
    typed = OutputReference.model_validate(reference)
    direction = resolve_property(typed, "direction", nodes, ready["results"])
    line = resolve_output(typed, nodes, ready["results"])
    assert set(line) == {"point_display", "axis_display"}
    assert direction["sense"] == "unsigned"
    expected_frame = ready["results"]["frame"]
    assert expected_frame["primary_reference"] == reference
    np.testing.assert_allclose(
        expected_frame["z_axis_display"], direction["value"], atol=1e-12
    )

    renamed = native.export_recipe()
    next(node for node in renamed["nodes"] if node["id"] == "fitted")["label"] = (
        "Renamed measured surface"
    )
    saved = json.dumps(renamed)
    reopened = FeatureGraph(workspace, Recipe.model_validate_json(saved))
    again = cast(dict[str, Any], reopened.evaluate(str(reopened.snapshot()["token"])))
    assert again["results"]["frame"] == expected_frame
    assert again["recipe"]["nodes"][-1]["primary_reference"] == reference

    sink = io.BytesIO()
    _ = write_project_archive(renamed, original, sink)
    browser = BrowserWorkspace(
        None, scan_limits=limits, project_limits=ProjectArchiveLimits()
    )
    try:
        empty = success(browser.call_json("getGraph"))
        candidate = success(
            browser.prepare_source_json(
                json.dumps(
                    {
                        "operationId": "typed-directions",
                        "kind": "scan",
                        "purpose": "open-project",
                        "confirmedDiscard": True,
                        "generation": 0,
                        "token": empty["token"],
                    }
                ),
                source_input=BytesInput(sink.getvalue(), "directions.scansor"),
            )
        )
        promote(browser, candidate)
        restored = cast(
            dict[str, Any],
            browser.graph.evaluate(str(browser.graph.snapshot()["token"])),
        )
        assert restored["results"]["frame"] == expected_frame
        assert restored["recipe"]["nodes"][-1]["primary_reference"] == reference
        assert browser.workspace.source_sha256 == workspace.source_sha256
        selected = next(
            output
            for output in restored["input_catalogue"]
            if output["reference"] == reference
        )
        assert selected["properties"]["direction"]["sense"] == "unsigned"
    finally:
        _ = success(browser.call_json("close"))
