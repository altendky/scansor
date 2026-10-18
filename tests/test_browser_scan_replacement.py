"""Worker replacement candidates preserve accepted work until promotion."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe
from experiments.scan_workspace import ScanLimits, ScanProgress
from experiments.selection_entries import source_binding
from experiments.selection_volume_transactions import author_volume
from tests.native_scan_fixture import native_grid_fixture
from tests.test_browser_scan_workspace import graph, promote, success

LIMITS = ScanLimits(2_000_000, 1000, 2000, 10_000_000, chunk_bytes=64)


def request(workspace: BrowserWorkspace, **changes: Any) -> dict[str, Any]:
    return {
        "operationId": "replacement",
        "kind": "scan",
        "purpose": "replace-scan",
        "filename": "replacement.ply",
        "generation": workspace.workspace_generation,
        "token": graph(workspace)["token"],
        **changes,
    }


@pytest.fixture
def accepted(tmp_path: Path) -> tuple[BrowserWorkspace, Path]:
    original = native_grid_fixture(5, 5)
    path = tmp_path / "original.ply"
    _ = path.write_bytes(original.raw)
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "original",
                    "kind": "scan",
                    "filename": path.name,
                    "generation": 0,
                    "token": graph(workspace)["token"],
                }
            ),
            str(path),
        )
    )
    _ = promote(workspace, candidate)
    before = graph(workspace)
    recipe = deepcopy(before["recipe"])
    recipe["nodes"].extend(
        [
            {
                "id": "patch",
                "operation": "selection",
                "label": "Painted patch",
                "source": "scan",
                "ids": list(range(25)),
            },
            {
                "id": "plane",
                "operation": "fit",
                "label": "Plane",
                "kind": "plane",
                "selections": ["patch"],
            },
        ]
    )
    recipe["output"] = "plane"
    _ = success(
        workspace.call_json(
            "replaceRecipe", json.dumps({"token": before["token"], "recipe": recipe})
        )
    )
    _ = author_volume(
        workspace.graph,
        graph(workspace)["token"],
        selection="patch",
        volume="footprint",
        label="Footprint",
        kind="mesh",
    )
    _ = workspace.graph.evaluate(graph(workspace)["token"], all_actions=True)
    target = tmp_path / "replacement.ply"
    _ = target.write_bytes(native_grid_fixture(6, 6).raw)
    return workspace, target


def test_candidate_changes_tessellation_without_publishing_or_reusing_old_ids(
    accepted: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, path = accepted
    before, old_graph, old_workspace = (
        graph(workspace),
        workspace.graph,
        workspace.workspace,
    )
    old_positions = workspace.display_positions
    pending = success(
        workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
    )
    assert graph(workspace) == before
    assert workspace.graph is old_graph and workspace.workspace is old_workspace
    assert workspace.display_positions is old_positions
    assert pending["purpose"] == "replace-scan"
    assert pending["source_mode"] == {"kind": "scan"}
    assert pending["metadata"]["vertices"] == 36
    assert pending["workspaceGeneration"] == workspace.workspace_generation + 1
    selection = pending["graph"]["recipe"]["nodes"][1]
    assert selection["entries"][0] == before["recipe"]["nodes"][1]["entries"][0]
    assert selection["entries"][1]["mode"] == "generated"
    assert selection["entries"][1]["ids"] is None
    assert selection["entries"][1]["source_volume"] is None
    assert selection["entries"][1]["volumes"] == []
    assert pending["graph"]["states"]["plane"] == "needs_input"
    assert pending["graph"]["results"] == {}
    published = promote(workspace, pending)
    assert published["project_generation"] == pending["workspaceGeneration"]
    assert len(workspace.workspace.local) == 36
    assert workspace.workspace is not old_workspace
    assert graph(workspace)["recipe"]["nodes"][2] == before["recipe"]["nodes"][2]


def test_explicit_choice_generates_new_rows_and_roundtrips_active_source_with_archived_volume(
    accepted: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, path = accepted
    candidate = success(
        workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
    )
    _ = promote(workspace, candidate)
    pending = graph(workspace)
    _ = workspace.graph.ensure_current(pending["token"], ["plane"])
    assert not workspace.graph.needs_evaluation(pending["token"], ["plane"])
    recipe = deepcopy(pending["recipe"])
    original, target = recipe["nodes"][1]["entries"]
    target["source_volume"] = {
        "selection": "patch",
        "entry": original["id"],
        "volume": "footprint",
    }
    assert target["scan"]["reference_sha256"] == workspace.workspace.reference_sha256
    assert (
        source_binding(workspace.workspace).key != original["scan"]["reference_sha256"]
    )
    _ = success(
        workspace.call_json(
            "replaceRecipe", json.dumps({"recipe": recipe, "token": pending["token"]})
        )
    )
    prepared = success(
        workspace.prepare_evaluation_json(
            "ensureGraph",
            json.dumps({"token": graph(workspace)["token"], "targets": ["plane"]}),
        )
    )
    assert prepared["run"]
    done = success(workspace.finish_evaluation_json())
    assert done["states"]["plane"] == "ready"
    assert done["memberships"]["patch"] == list(range(36))
    assert done["errors"] == {}
    saved = success(workspace.call_json("exportRecipe"))
    reopened = BrowserWorkspace(None, scan_limits=LIMITS)
    fresh = graph(reopened)
    opened = success(
        reopened.prepare_source_json(
            json.dumps(
                {
                    "operationId": "reopen",
                    "purpose": "open-actions",
                    "recipe": saved,
                    "generation": reopened.workspace_generation,
                    "token": fresh["token"],
                    "confirmedDiscard": True,
                    "source": {"filename": path.name, "size": path.stat().st_size},
                }
            ),
            str(path),
        )
    )
    _ = promote(reopened, opened)
    assert graph(reopened)["recipe"] == saved
    assert graph(reopened)["results"] == {}
    assert len(reopened.workspace.local) == 36
    assert saved["nodes"][1]["entries"][0]["ids"] == list(range(25))


@pytest.mark.parametrize(
    "fault",
    [
        "malformed",
        "stale-token",
        "stale-generation",
        "cancelled",
        "cancelled-mid",
        "wrong-purpose",
    ],
)
def test_rejected_and_cancelled_preparation_keeps_previous_authority(
    accepted: tuple[BrowserWorkspace, Path], tmp_path: Path, fault: str
) -> None:
    workspace, path = accepted
    before, previous = graph(workspace), workspace.workspace
    value = request(workspace)
    if fault == "malformed":
        path = tmp_path / "bad.ply"
        _ = path.write_bytes(b"not a PLY")
    elif fault == "stale-token":
        value["token"] = "0" * 64
    elif fault == "stale-generation":
        value["generation"] -= 1
    elif fault == "wrong-purpose":
        value["recipe"] = before["recipe"]
    during = False

    def progress(_record: ScanProgress) -> None:
        nonlocal during
        during = fault == "cancelled-mid"

    result = json.loads(
        workspace.prepare_source_json(
            json.dumps(value),
            str(path),
            progress=progress,
            cancelled=lambda: fault == "cancelled" or during,
        )
    )
    assert not result["ok"]
    if fault == "cancelled-mid":
        assert during
    assert graph(workspace) == before and workspace.workspace is previous
    with pytest.raises(ValueError, match="No source candidate"):
        _ = workspace.candidate_display_positions


def test_discard_and_stale_promotion_preserve_old_graph(
    accepted: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, path = accepted
    before = graph(workspace)
    candidate = success(
        workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
    )
    _ = success(workspace.discard_source_json(candidate["operationId"]))
    assert graph(workspace) == before
    candidate = success(
        workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
    )
    recipe = Recipe.model_validate(before["recipe"])
    changed = recipe.model_dump()
    changed["nodes"][0]["label"] = "User edit"
    _ = workspace.graph.replace(Recipe.model_validate(changed), before["token"])
    result = json.loads(
        workspace.promote_source_json(
            json.dumps(
                {
                    "operationId": candidate["operationId"],
                    "workspaceGeneration": candidate["workspaceGeneration"],
                }
            )
        )
    )
    assert not result["ok"] and result["error"]["code"] == "stale_source"
    assert graph(workspace)["recipe"]["nodes"][0]["label"] == "User edit"
    assert len(workspace.workspace.local) == 25


def test_empty_and_legacy_workspaces_explicitly_decline_replacement(
    tmp_path: Path,
) -> None:
    path = tmp_path / "target.ply"
    _ = path.write_bytes(native_grid_fixture(3, 3).raw)
    for workspace in (
        BrowserWorkspace(None, scan_limits=LIMITS),
        BrowserWorkspace(
            Path("examples/nozzle-bayonette-simplified"), scan_limits=LIMITS
        ),
    ):
        before = graph(workspace)
        metadata = success(workspace.call_json("loadWorkspace"))
        assert metadata["source_replacement"]["supported"] is False
        result = json.loads(
            workspace.prepare_source_json(json.dumps(request(workspace)), str(path))
        )
        assert not result["ok"] and result["error"]["code"] == "unsupported_capability"
        assert graph(workspace) == before
