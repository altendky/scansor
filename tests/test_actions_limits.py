"""Portable definitions have explicit budgets independent of scan admission."""

import json
from pathlib import Path
from typing import Any

import pytest

from experiments import actions_limits
from experiments.browser_runtime import workspace as worker_workspace
from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe


def test_budget_counts_utf8_and_includes_json_framing() -> None:
    value = {"name": "é"}
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    actions_limits.check_json_budget(value, size, "Actions")
    with pytest.raises(ValueError, match="JSON limit"):
        actions_limits.check_json_budget(value, size - 1, "Actions")


def test_actions_envelope_limits_recipe_and_controls_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(actions_limits, "MAX_CONTROL_JSON_BYTES", 13)
    monkeypatch.setattr(actions_limits, "MAX_ACTIONS_JSON_BYTES", 20)
    actions_limits.check_actions_envelope({"token": "a", "recipe": "x" * 18})
    with pytest.raises(ValueError, match="Actions"):
        actions_limits.check_actions_envelope({"recipe": "x" * 19})
    with pytest.raises(ValueError, match="Controls"):
        actions_limits.check_actions_envelope({"token": "ab", "recipe": {}})


def test_worker_admits_large_definitions_but_keeps_controls_small() -> None:
    workspace = BrowserWorkspace(Path("examples/nozzle-bayonette-simplified"))
    original = json.loads(workspace.call_json("getGraph"))["result"]
    recipe: dict[str, Any] = {
        "schema_version": 2,
        "nodes": [
            {
                "id": f"p{index}",
                "operation": "point",
                "label": f"Point {index} " + "x" * 100,
                "initial_coordinates": [1.0, 2.0, 3.0],
            }
            for index in range(5000)
        ],
        "output": "p0",
    }
    recipe["nodes"].insert(0, original["recipe"]["nodes"][0])
    encoded = json.dumps(recipe)
    assert 1_000_000 < len(encoded) < actions_limits.MAX_ACTIONS_JSON_BYTES
    response = json.loads(
        workspace.call_json(
            "replaceRecipe",
            json.dumps(
                {
                    "token": original["token"],
                    "recipe": recipe,
                }
            ),
        )
    )
    assert response["ok"], response
    assert len(response["result"]["recipe"]["nodes"]) == 5001
    exported = json.loads(workspace.call_json("exportRecipe"))
    assert exported["ok"] and len(exported["result"]["nodes"]) == 5001
    control = json.loads(
        workspace.call_json("getGraph", json.dumps({"unknown": "x" * 1_000_000}))
    )
    assert not control["ok"] and control["error"]["status"] == 422
    assert "1,000,000-byte" in control["error"]["message"]
    assert (
        json.loads(workspace.call_json("getGraph"))["result"]["token"]
        == response["result"]["token"]
    )


def test_advertised_profile_is_detached() -> None:
    first = actions_limits.actions_limits()
    first["max_json_bytes"] = 1
    assert actions_limits.actions_limits()["max_json_bytes"] == 32 * 1024 * 1024


def test_normalized_generated_work_must_fit_saved_file_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BrowserWorkspace(Path("examples/nozzle-bayonette-simplified"))
    original = json.loads(workspace.call_json("getGraph"))["result"]
    recipe = original["recipe"]
    saved_size = len((json.dumps(recipe, ensure_ascii=False, indent=2) + "\n").encode())
    monkeypatch.setattr(actions_limits, "MAX_ACTIONS_JSON_BYTES", saved_size)
    # The normalized saved recipe exactly fits; generated authoring uses the
    # same graph transaction even though its incoming command can be tiny.
    _ = workspace.graph.replace(Recipe.model_validate(recipe), original["token"])
    recipe["nodes"].append(
        {
            "id": "extra",
            "operation": "point",
            "label": "Extra",
            "initial_coordinates": [1, 2, 3],
        }
    )
    with pytest.raises(ValueError, match="Saved actions"):
        _ = workspace.graph.replace(Recipe.model_validate(recipe), original["token"])
    retained = json.loads(workspace.call_json("getGraph"))["result"]
    assert retained["token"] == original["token"]
    assert len(retained["recipe"]["nodes"]) == len(original["states"])


def test_normalization_expansion_is_admitted_before_accepting_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BrowserWorkspace(Path("examples/nozzle-bayonette-simplified"))
    original = json.loads(workspace.call_json("getGraph"))["result"]
    raw = {
        "schema_version": 2,
        "nodes": [
            original["recipe"]["nodes"][0],
            {
                "id": "new",
                "operation": "point",
                "label": "New",
                "initial_coordinates": [0, 0, 0],
            },
        ],
        "output": "new",
    }
    monkeypatch.setattr(
        actions_limits, "MAX_ACTIONS_JSON_BYTES", len(json.dumps(raw).encode())
    )
    response = json.loads(
        workspace.call_json(
            "replaceRecipe",
            json.dumps(
                {
                    "token": original["token"],
                    "recipe": raw,
                }
            ),
        )
    )
    assert not response["ok"] and "Saved actions" in response["error"]["message"]
    assert (
        json.loads(workspace.call_json("getGraph"))["result"]["token"]
        == original["token"]
    )


def test_worker_actions_request_exact_byte_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BrowserWorkspace(None)
    original = json.loads(workspace.call_json("getGraph"))["result"]
    payload = json.dumps({"token": original["token"], "recipe": original["recipe"]})
    maximum = 512
    monkeypatch.setattr(worker_workspace, "MAX_ACTIONS_REQUEST_BYTES", maximum)
    admitted = payload + " " * (maximum - len(payload.encode()))
    assert json.loads(workspace.call_json("replaceRecipe", admitted))["ok"]
    rejected = json.loads(workspace.call_json("replaceRecipe", admitted + " "))
    assert not rejected["ok"] and "512-byte" in rejected["error"]["message"]
    assert (
        json.loads(workspace.call_json("getGraph"))["result"]["recipe"]
        == original["recipe"]
    )
