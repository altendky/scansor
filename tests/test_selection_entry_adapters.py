"""Automatic archive publication remains one recipe across worker jobs."""

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from tests.native_scan_fixture import native_grid_fixture
from tests.test_browser_scan_actions import LIMITS, graph, promote, unpack
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_selection_entry_graph import generated_graph, snapshot


@pytest.mark.parametrize("fit_failure", [False, True])
def test_worker_finish_follows_refresh_and_exports_the_published_recipe(
    tmp_path: Path, fit_failure: bool
) -> None:
    fixture = native_grid_fixture(5, 5)
    path = tmp_path / "source.ply"
    _ = path.write_bytes(fixture.raw)
    worker = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = unpack(
        worker.prepare_source_json(
            json.dumps(
                {
                    "operationId": "initial",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(worker)["token"],
                    "filename": "source.ply",
                }
            ),
            str(path),
        )
    )
    _ = promote(worker, candidate)
    initial = snapshot(generated_graph())["recipe"]
    _ = unpack(
        worker.call_json(
            "replaceRecipe",
            json.dumps(
                {
                    "token": graph(worker)["token"],
                    "recipe": initial,
                }
            ),
        )
    )
    _ = unpack(
        worker.prepare_evaluation_json(
            "evaluateGraph",
            json.dumps(
                {
                    "token": graph(worker)["token"],
                    "all_actions": True,
                }
            ),
        )
    )
    _ = unpack(worker.finish_evaluation_json())
    payload = deepcopy(graph(worker)["recipe"])
    payload["nodes"][1]["entries"][0]["ids"] = [0, 1] if fit_failure else [0, 1, 5, 6]
    changed = unpack(
        worker.call_json(
            "replaceRecipe",
            json.dumps(
                {
                    "token": graph(worker)["token"],
                    "recipe": payload,
                }
            ),
        )
    )
    prepared = unpack(
        worker.prepare_evaluation_json(
            "ensureGraph",
            json.dumps(
                {
                    "token": changed["token"],
                    "targets": ["plane"],
                }
            ),
        )
    )
    assert prepared["run"] is True
    finished = unpack(worker.finish_evaluation_json(if_recipe_token=changed["token"]))
    assert finished["token"] != changed["token"]
    assert finished["evaluation_origin_token"] == changed["token"]
    assert "recipe" in finished
    assert finished["evaluation_running"] is False
    assert finished["states"]["plane"] == ("failed" if fit_failure else "ready")
    assert ("evaluation_error" in finished) == fit_failure
    exported = unpack(worker.call_json("exportRecipe"))
    assert exported == finished["recipe"]
    settled: dict[str, Any] = unpack(
        worker.prepare_evaluation_json(
            "ensureGraph",
            json.dumps(
                {
                    "token": finished["token"],
                    "targets": ["plane"],
                }
            ),
        )
    )
    assert settled["run"] is False
    assert settled["state"]["token"] == finished["token"]
    assert ("evaluation_error" in settled["state"]) == fit_failure


@pytest.mark.parametrize("fit_failure", [False, True])
def test_native_job_completion_follows_refreshed_recipe(
    native_http: NativeHttp, fit_failure: bool
) -> None:
    http = native_http
    raw = native_grid_fixture(5, 5).raw
    _, operation = http.reserve(raw)
    status, uploaded, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, uploaded
    assert http.finish(operation)["status"] == "accepted"
    initial = snapshot(generated_graph())["recipe"]
    cursor = http.cursor()
    status, saved, _ = http.request(
        "/api/graph",
        {"token": cursor["token"], "recipe": initial},
        generation=cursor["generation"],
    )
    assert status == 200, saved
    payload = deepcopy(saved["recipe"])
    payload["nodes"][1]["entries"][0]["ids"] = [0, 1] if fit_failure else [0, 1, 5, 6]
    status, changed, _ = http.request(
        "/api/graph",
        {"token": saved["token"], "recipe": payload},
        generation=cursor["generation"],
    )
    assert status == 200, changed
    status, running, _ = http.request(
        "/api/graph/ensure",
        {"token": changed["token"], "targets": ["plane"]},
        generation=cursor["generation"],
    )
    assert status in {200, 202}, running
    deadline = time.monotonic() + 10
    while True:
        status, finished, _ = http.request(
            f"/api/graph?recipe_token={changed['token']}"
        )
        assert status == 200, finished
        if not finished["evaluation_running"]:
            break
        assert time.monotonic() < deadline, "refresh did not complete"
        time.sleep(0.01)
    assert finished["token"] != changed["token"]
    assert finished["evaluation_origin_token"] == changed["token"]
    assert "recipe" in finished
    assert finished["states"]["plane"] == ("failed" if fit_failure else "ready")
    assert ("evaluation_error" in finished) == fit_failure
    status, settled, _ = http.request(
        "/api/graph/ensure",
        {"token": finished["token"], "targets": ["plane"]},
        generation=cursor["generation"],
    )
    assert status == 200, settled
    assert settled["evaluation_running"] is False
    assert ("evaluation_error" in settled) == fit_failure
