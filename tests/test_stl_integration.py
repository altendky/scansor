"""STL bytes use the same native and worker source transactions as PLY."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe
from experiments.scan_workspace import ScanLimits
from experiments.selection_volume_transactions import author_volume
from tests.test_browser_scan_workspace import graph, promote, success
from tests.test_native_scan_actions_http import open_actions
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_stl_virtual_scan import grid_bytes
from tests.test_virtual_scan_native import change

FORMATS = ["binary-triangle-ply-v1", "triangle-stl-f32-v1"]
LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000, chunk_bytes=4096)


def fitted_recipe(recipe: dict[str, Any], count: int) -> Recipe:
    value = deepcopy(recipe)
    value["nodes"].extend(
        [
            {
                "id": "patch",
                "label": "Shared patch",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(count)),
            },
            {
                "id": "plane",
                "label": "Shared plane",
                "operation": "fit",
                "kind": "plane",
                "selections": ["patch"],
            },
        ]
    )
    value["output"] = "plane"
    return Recipe.model_validate(value)


def choose_volume(recipe: dict[str, Any]) -> Recipe:
    value = deepcopy(recipe)
    selection = next(node for node in value["nodes"] if node["id"] == "patch")
    original, virtual = selection["entries"]
    virtual["source_volume"] = {
        "selection": "patch",
        "entry": original["id"],
        "volume": "footprint",
    }
    return Recipe.model_validate(value)


def author_shared_volume(workspace: BrowserWorkspace) -> None:
    before = graph(workspace)
    _ = workspace.graph.replace(
        fitted_recipe(before["recipe"], len(workspace.workspace.local)), before["token"]
    )
    _ = author_volume(
        workspace.graph,
        graph(workspace)["token"],
        selection="patch",
        volume="footprint",
        label="Original footprint",
        kind="mesh",
    )


def assert_plane(state: dict[str, Any]) -> None:
    assert state["states"]["plane"] == "ready", state
    assert not state["errors"]
    assert state["results"]["plane"]["weighted_rms"] == 0


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
def test_native_stl_import_fitting_actions_and_virtual_pair(
    native_http: NativeHttp, encoding: str
) -> None:
    http = native_http
    assert http.metadata()["capabilities"]["source_import"]["formats"] == FORMATS
    raw = grid_bytes(encoding)
    _, operation = http.reserve(raw)  # Deliberately retains a misleading .ply name.
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, result
    assert http.finish(operation)["status"] == "accepted"
    original = http.metadata()
    assert original["source"]["profile"] == FORMATS[1]
    assert original["normal_source"] == "geometry-area-weighted-smoothed"
    assert original["capabilities"]["normals_required"] is False
    _ = http.server.graph.replace(
        fitted_recipe(original["graph"]["recipe"], 289), original["graph"]["token"]
    )
    _ = author_volume(
        http.server.graph,
        http.metadata()["graph"]["token"],
        selection="patch",
        volume="footprint",
        label="Original footprint",
        kind="mesh",
    )
    assert_plane(http.server.graph.ensure_current(http.cursor()["token"], ["plane"]))
    reduced = change(http, "/api/sources/virtual", settings={"divisions": 4})
    assert reduced["source"]["profile"] == FORMATS[0]
    _ = http.server.graph.replace(
        choose_volume(reduced["graph"]["recipe"]), reduced["graph"]["token"]
    )
    assert_plane(http.server.graph.ensure_current(http.cursor()["token"], ["plane"]))
    saved = http.metadata()["graph"]["recipe"]
    profile = http.metadata()["virtual_scans"]
    _ = change(http, "/api/sources/activate", binding=profile["original"]["binding"])
    assert_plane(http.server.graph.ensure_current(http.cursor()["token"], ["plane"]))
    result = open_actions(http, saved)
    assert result["status"] == "accepted", result
    assert (
        http.metadata()["virtual_scans"]["active_binding"]
        == profile["virtual"]["binding"]
    )
    assert_plane(http.server.graph.ensure_current(http.cursor()["token"], ["plane"]))


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
def test_worker_stl_import_virtual_actions_and_force_reconstruction(
    tmp_path: Path, encoding: str
) -> None:
    path = tmp_path / "misleading.ply"
    _ = path.write_bytes(grid_bytes(encoding))
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "stl-import",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(workspace)["token"],
                    "filename": path.name,
                }
            ),
            str(path),
        )
    )
    assert candidate["metadata"]["capabilities"]["source_import"]["formats"] == FORMATS
    assert candidate["metadata"]["normal_source"] == "geometry-area-weighted-smoothed"
    _ = promote(workspace, candidate)
    author_shared_volume(workspace)
    assert_plane(workspace.graph.ensure_current(graph(workspace)["token"], ["plane"]))
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "stl-virtual",
                    "kind": "scan",
                    "purpose": "create-virtual-scan",
                    "generation": workspace.workspace_generation,
                    "token": graph(workspace)["token"],
                    "settings": {"divisions": 4},
                }
            )
        )
    )
    _ = promote(workspace, candidate)
    _ = workspace.graph.replace(
        choose_volume(graph(workspace)["recipe"]), graph(workspace)["token"]
    )
    assert_plane(workspace.graph.ensure_current(graph(workspace)["token"], ["plane"]))
    saved, identity, generation = (
        graph(workspace)["recipe"],
        workspace.source_identity,
        workspace.workspace_generation,
    )
    recovered = BrowserWorkspace(None, scan_limits=LIMITS)
    restored = success(
        recovered.prepare_source_json(
            json.dumps(
                {
                    "operationId": "stl-recover",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(recovered)["token"],
                    "filename": "renamed.stl",
                }
            ),
            str(path),
            recovery_generation=generation,
            recovery_identity=identity,
            recovery_recipe=saved,
        )
    )
    _ = promote(recovered, restored)
    assert graph(recovered)["recipe"] == saved
    assert recovered.source_identity == identity
    assert recovered.workspace_generation == generation
    assert_plane(recovered.graph.ensure_current(graph(recovered)["token"], ["plane"]))
    assert json.loads(workspace.call_json("close"))["ok"]
    assert json.loads(recovered.call_json("close"))["ok"]
