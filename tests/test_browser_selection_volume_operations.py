"""Worker volume transports use real owned records and external source epochs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from tests.test_browser_scan_replacement import accepted as replacement_accepted
from tests.test_browser_scan_replacement import request
from tests.test_browser_scan_workspace import graph, promote, success

accepted = replacement_accepted


def invoke(
    workspace: BrowserWorkspace, operation: str, **payload: Any
) -> dict[str, Any]:
    return success(workspace.call_json(operation, json.dumps(payload)))


def test_owned_volume_routes_publish_full_graph_and_queries_leave_recipe_unchanged(
    accepted: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, _ = accepted
    before = graph(workspace)
    metadata = invoke(workspace, "loadWorkspace")
    assert metadata["selection_volumes"] == {"version": 1, "max_preview_cells": 2048}
    selection = before["recipe"]["nodes"][1]
    entry = selection["entries"][0]
    ref = {"selection": "patch", "entry": entry["id"], "volume": "footprint"}
    choices = invoke(
        workspace, "getSelectionVolumeChoices", token=before["token"], selection="patch"
    )
    assert choices["volumes"][0]["reference"] == ref
    preview = invoke(
        workspace,
        "getSelectionVolumePreview",
        token=before["token"],
        reference=ref,
        archive_sha256=entry["volumes"][0]["archive"]["sha256"],
        limit=2,
    )
    assert 0 < preview["returned_cells"] <= 2
    assert graph(workspace) == before
    authored = invoke(
        workspace,
        "authorSelectionVolume",
        token=before["token"],
        selection="patch",
        mode="create",
        volume="extra",
        label="Extra volume",
        kind="mesh",
    )
    assert authored["project_generation"] == workspace.workspace_generation
    assert not authored["evaluation_running"]
    extra_ref = {**ref, "volume": "extra"}
    changed = invoke(
        workspace,
        "setSelectionVolumeOptions",
        token=authored["token"],
        reference=extra_ref,
        options={"inward_thickness": 0.25},
    )
    assert (
        changed["recipe"]["nodes"][1]["entries"][0]["volumes"][1]["options"][
            "inward_thickness"
        ]
        == 0.25
    )
    deleted = invoke(
        workspace, "deleteSelectionVolume", token=changed["token"], reference=extra_ref
    )
    assert [
        volume["id"]
        for volume in deleted["recipe"]["nodes"][1]["entries"][0]["volumes"]
    ] == ["footprint"]


def test_choice_uses_replacement_entry_and_old_token_cannot_mutate(
    accepted: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, path = accepted
    before = graph(workspace)
    candidate = success(
        workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
    )
    _ = promote(workspace, candidate)
    pending = graph(workspace)
    entries = pending["recipe"]["nodes"][1]["entries"]
    reference = {"selection": "patch", "entry": entries[0]["id"], "volume": "footprint"}
    stale = json.loads(
        workspace.call_json(
            "chooseSelectionVolume",
            json.dumps(
                {
                    "token": before["token"],
                    "selection": "patch",
                    "source_volume": reference,
                }
            ),
        )
    )
    assert stale["error"]["code"] == "stale_graph"
    assert graph(workspace) == pending
    chosen = invoke(
        workspace,
        "chooseSelectionVolume",
        token=pending["token"],
        selection="patch",
        source_volume=reference,
    )
    assert chosen["project_generation"] == workspace.workspace_generation == 2
    assert chosen["recipe"]["nodes"][1]["entries"][1]["source_volume"] == reference
    assert chosen["recipe"]["nodes"][1]["entries"][0] == entries[0]


@pytest.mark.parametrize(
    "operation",
    [
        "authorSelectionVolume",
        "setSelectionVolumeOptions",
        "chooseSelectionVolume",
        "getSelectionVolumeChoices",
        "getSelectionVolumePreview",
        "deleteSelectionVolume",
    ],
)
def test_volume_operations_reject_pending_evaluation_before_dispatch(
    accepted: tuple[BrowserWorkspace, Path],
    operation: str,
) -> None:
    workspace, _ = accepted
    before = graph(workspace)
    recipe = before["recipe"]
    recipe["nodes"][2]["label"] = "Changed fit"
    edited = invoke(workspace, "replaceRecipe", token=before["token"], recipe=recipe)
    _ = success(
        workspace.prepare_evaluation_json(
            "evaluateGraph", json.dumps({"token": edited["token"], "all_actions": True})
        )
    )
    response = json.loads(
        workspace.call_json(operation, json.dumps({"token": edited["token"]}))
    )
    assert response["error"]["code"] == "evaluation_busy"
