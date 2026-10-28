"""Actual saved-source reopening, evaluated definitions and STEP readback."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.feature_graph import Recipe
from experiments.scan_workspace import ScanWorkspace
from tests.native_scan_fixture import native_scene_fixture
from tests.scan_source_fixture import read_source_bytes
from tests.test_native_scan_acceptance import evaluate, upload
from tests.test_native_scan_acceptance import native_acceptance as native_acceptance
from tests.test_native_scan_actions_http import open_actions
from tests.test_native_scan_http import NativeHttp
from tests.test_nozzle_cad import roundtrip, samples


def reset(http: NativeHttp) -> None:
    status, result, _ = http.request(
        "/api/project/reset", {**http.cursor(), "confirmedDiscard": True}
    )
    assert status == 200, result


def inspect_locate(http: NativeHttp, recipe: dict[str, Any]) -> None:
    status, inspected, _ = http.request(
        "/api/actions/inspect", {**http.cursor(), "recipe": recipe}
    )
    assert status == 200 and inspected["classification"] == "locate", inspected
    assert inspected["uploadRequired"]


def evaluate_reopened(http: NativeHttp) -> dict[str, Any]:
    """Evaluate the installed graph directly, with no recipe replacement."""
    cursor = http.cursor()
    status, result, _ = http.request(
        "/api/graph/evaluate",
        {"token": cursor["token"], "all_actions": True},
        generation=cursor["generation"],
    )
    assert status == 202, result
    assert http.server.graph_job is not None
    _ = http.server.graph_job.result(timeout=30)
    status, snapshot, _ = http.request("/api/graph", generation=cursor["generation"])
    assert status == 200 and not snapshot["errors"], snapshot
    return snapshot


def test_raw_nozzle_saved_source_only_actions_reopen_renamed_exact_bytes(
    native_acceptance: NativeHttp,
) -> None:
    http = native_acceptance
    raw = Path(
        "examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply"
    ).read_bytes()
    assert len(raw) > 1_000_000
    meta = upload(http, raw, "original-realityscan.ply")
    previous = http.server.accepted()
    workspace = previous.workspace
    assert isinstance(workspace, ScanWorkspace)
    saved = json.loads(json.dumps(meta["graph"]["recipe"]))
    assert len(saved["nodes"]) == 1
    source = saved["nodes"][0]
    assert source["operation"] == "source"
    reset(http)
    inspect_locate(http, saved)
    cursor = http.cursor()
    accepted = open_actions(http, saved, raw)
    assert accepted["status"] == "accepted", accepted
    assert accepted["acceptedGeneration"] == cursor["generation"] + 1
    reopened = http.server.accepted()
    assert reopened.graph is not previous.graph and reopened.workspace is not workspace
    assert isinstance(reopened.workspace, ScanWorkspace)
    assert read_source_bytes(reopened.workspace) == raw
    assert reopened.workspace.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(reopened.workspace.local) == 24_999
    assert len(reopened.workspace.data.triangles) == 49_994
    assert np.all(reopened.workspace.data.weights > 0)
    for actual, expected in (
        (reopened.workspace.local, workspace.local),
        (reopened.workspace.data.triangles, workspace.data.triangles),
        (reopened.workspace.data.normals, workspace.data.normals),
        (reopened.workspace.data.weights, workspace.data.weights),
    ):
        np.testing.assert_array_equal(actual, expected)
    current = http.metadata()
    assert current["graph"]["recipe"] == saved
    assert current["graph"]["states"] == {source["id"]: "unevaluated"}
    assert current["import_warnings"] == []
    assert (
        current["graph"]["recipe"]["nodes"][0]["descriptor"]["filename"]
        == "original-realityscan.ply"
    )
    for name, expected, dtype in (
        ("positions", workspace.local, "<f4"),
        ("indices", workspace.data.triangles, "<u4"),
    ):
        status, display, headers = http.request(current[name]["url"])
        assert status == 200
        assert headers["X-Scansor-Generation"] == str(accepted["acceptedGeneration"])
        np.testing.assert_array_equal(
            np.frombuffer(display, dtype=dtype), expected.astype(dtype).ravel()
        )
    assert evaluate_reopened(http)["states"] == {source["id"]: "ready"}


def test_saved_independent_plane_cylinder_reopen_evaluate_and_step_readback(
    native_acceptance: NativeHttp, tmp_path: Path
) -> None:
    http = native_acceptance
    fixture = native_scene_fixture()
    meta = upload(http, fixture.raw, "horizontal.ply")
    recipe = deepcopy(meta["graph"]["recipe"])
    source = recipe["nodes"][0]["id"]
    for kind, ids in fixture.selections.items():
        selection = kind + "-rows"
        recipe["nodes"].extend(
            [
                {
                    "id": selection,
                    "label": selection,
                    "operation": "selection",
                    "source": source,
                    "ids": ids,
                },
                {
                    "id": kind,
                    "label": kind,
                    "operation": "fit",
                    "kind": kind,
                    "selections": [selection],
                },
            ]
        )
    recipe["output"] = "cylinder"
    authored = evaluate(http, recipe)
    saved = json.loads(json.dumps(authored["recipe"]))
    assert saved == Recipe.model_validate(recipe).model_dump(mode="json")
    old = http.server.accepted()
    reset(http)
    inspect_locate(http, saved)
    cursor = http.cursor()
    accepted = open_actions(http, saved, fixture.raw)
    assert accepted["status"] == "accepted", accepted
    generation = accepted["acceptedGeneration"]
    assert generation == cursor["generation"] + 1
    installed = http.metadata()
    assert installed["graph"]["recipe"] == saved
    assert installed["import_warnings"] == []
    assert installed["graph"]["states"] == {
        node["id"]: "unevaluated" for node in saved["nodes"]
    }
    assert installed["graph"]["results"] == {}
    assert http.server.graph is not old.graph
    workspace = http.server.workspace
    assert isinstance(workspace, ScanWorkspace)
    assert read_source_bytes(workspace) == fixture.raw
    np.testing.assert_array_equal(workspace.local, fixture.positions)
    np.testing.assert_array_equal(workspace.data.triangles, fixture.triangles)
    np.testing.assert_allclose(workspace.data.normals, fixture.normals, atol=1e-15)
    np.testing.assert_allclose(workspace.data.weights, fixture.weights, atol=1e-14)
    for name, expected, dtype in (
        ("positions", fixture.positions, "<f4"),
        ("indices", fixture.triangles, "<u4"),
    ):
        status, raw, headers = http.request(installed[name]["url"])
        assert status == 200
        assert headers["X-Scansor-Generation"] == str(generation)
        np.testing.assert_array_equal(np.frombuffer(raw, dtype=dtype), expected.ravel())
    state = evaluate_reopened(http)
    assert state["recipe"] == saved
    cylinder, plane = state["results"]["cylinder"], state["results"]["plane"]
    assert cylinder["analytic"]["radius"] == pytest.approx(2.5, abs=1e-6)
    np.testing.assert_allclose(
        np.abs(cylinder["analytic"]["axis"]), [1, 0, 0], atol=1e-6
    )
    assert cylinder["weighted_rms"] < 1e-6
    assert plane["weighted_rms"] < 1e-12
    equation = np.asarray(plane["parameters"])
    np.testing.assert_allclose(np.abs(equation[:3]), [1, 0, 0], atol=1e-12)
    assert abs(equation[3]) == pytest.approx(8.0, abs=1e-12)
    for name, fitted in (("cylinder", cylinder), ("plane", plane)):
        ids = fixture.selections[name]
        points = fixture.positions[ids]
        if name == "plane":
            independent_residuals = points @ equation[:3] - equation[3]
        else:
            geometry = cylinder["analytic"]
            axis = np.asarray(geometry["axis"])
            offset = points - geometry["origin"]
            radial = offset - (offset @ axis)[:, None] * axis
            independent_residuals = np.linalg.norm(radial, axis=1) - geometry["radius"]
        residuals = np.asarray(fitted["residuals"])
        assert len(residuals) == len(ids)
        np.testing.assert_allclose(residuals, independent_residuals, atol=1e-12)
        independent_rms = np.sqrt(
            fixture.weights[ids] @ independent_residuals**2 / fixture.weights[ids].sum()
        )
        assert fitted["weighted_rms"] == pytest.approx(independent_rms, abs=1e-12)
    status, archive, _ = http.request(
        "/api/export/cad",
        {
            "token": state["token"],
            "target": "cylinder",
            "units": "Millimeters",
            "axis_up": False,
            "include_mesh": False,
        },
        generation=generation,
    )
    assert status == 200, archive
    exported, shapes, mesh, _ = roundtrip(archive, tmp_path)
    assert exported["units"] == "Millimeters" and mesh is None
    assert len(shapes) == 1
    points = samples(next(iter(shapes.values())))
    np.testing.assert_allclose(
        np.linalg.norm(points[:, 1:] - [-4, 5], axis=1), 2.5, atol=1e-6
    )
    np.testing.assert_allclose(
        [points[:, 0].min(), points[:, 0].max()], [0, 7], atol=1e-6
    )
