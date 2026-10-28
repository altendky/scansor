"""Worker source transactions and Force reconstruction retain one scan pair."""

from __future__ import annotations

import gc
import json
import weakref
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe
from experiments.scan_workspace import ScanLimits, ScanProgress
from experiments.selection_volume_transactions import author_volume
from tests.native_scan_fixture import native_grid_fixture
from tests.test_browser_scan_workspace import graph, promote, success

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 20_000_000, chunk_bytes=4096)


def _request(
    workspace: BrowserWorkspace, purpose: str, **controls: Any
) -> dict[str, Any]:
    return {
        "operationId": "virtual-change",
        "kind": "scan",
        "purpose": purpose,
        "generation": workspace.workspace_generation,
        "token": graph(workspace)["token"],
        **controls,
    }


@pytest.fixture
def imported(tmp_path: Path) -> tuple[BrowserWorkspace, Path]:
    path = tmp_path / "original.ply"
    _ = path.write_bytes(native_grid_fixture(17, 17).raw)
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    prepared = success(
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
    _ = promote(workspace, prepared)
    return workspace, path


def _create(workspace: BrowserWorkspace) -> dict[str, Any]:
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                _request(workspace, "create-virtual-scan", settings={"divisions": 4})
            )
        )
    )
    _ = promote(workspace, candidate)
    return candidate


def _activate(workspace: BrowserWorkspace, binding: str) -> dict[str, Any]:
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(_request(workspace, "activate-scan", binding=binding))
        )
    )
    _ = promote(workspace, candidate)
    return candidate


def test_coarse_authoring_transfers_explicitly_and_round_trip_keeps_current_definitions(
    imported: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, _ = imported
    before, original = graph(workspace), workspace.workspace
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                _request(workspace, "create-virtual-scan", settings={"divisions": 4})
            )
        )
    )
    assert graph(workspace) == before and workspace.workspace is original
    assert candidate["purpose"] == "create-virtual-scan"
    assert candidate["metadata"]["vertices"] < len(original.local)
    _ = promote(workspace, candidate)
    reduced = workspace.workspace
    payload = deepcopy(graph(workspace)["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "patch",
                "operation": "selection",
                "label": "Painted coarse patch",
                "source": "scan",
                "ids": list(range(len(reduced.local))),
            },
            {
                "id": "plane",
                "operation": "fit",
                "label": "Shared plane",
                "kind": "plane",
                "selections": ["patch"],
            },
        ]
    )
    payload["output"] = "plane"
    _ = workspace.graph.replace(
        Recipe.model_validate(payload), graph(workspace)["token"]
    )
    _ = author_volume(
        workspace.graph,
        graph(workspace)["token"],
        selection="patch",
        volume="footprint",
        label="Coarse footprint",
        kind="mesh",
    )
    coarse = cast(
        dict[str, Any],
        workspace.graph.ensure_current(graph(workspace)["token"], ["plane"]),
    )
    assert coarse["states"]["plane"] == "ready"
    _ = _activate(workspace, original.reference_sha256)
    pending = graph(workspace)
    assert pending["states"]["plane"] == "needs_input"
    payload = deepcopy(pending["recipe"])
    source_entry, target_entry = payload["nodes"][1]["entries"]
    target_entry["source_volume"] = {
        "selection": "patch",
        "entry": source_entry["id"],
        "volume": "footprint",
    }
    payload["nodes"][2]["label"] = "Edited on original"
    _ = workspace.graph.replace(Recipe.model_validate(payload), pending["token"])
    full = cast(
        dict[str, Any],
        workspace.graph.ensure_current(graph(workspace)["token"], ["plane"]),
    )
    assert full["states"]["plane"] == "ready"
    assert len(full["memberships"]["patch"]) == len(original.local)
    _ = _activate(workspace, reduced.reference_sha256)
    returned = graph(workspace)
    assert returned["recipe"]["nodes"][2]["label"] == "Edited on original"
    assert len(returned["recipe"]["nodes"][1]["entries"]) == 2
    result = cast(
        dict[str, Any], workspace.graph.ensure_current(returned["token"], ["plane"])
    )
    assert result["states"]["plane"] == "ready"
    assert result["memberships"]["patch"] == list(range(len(reduced.local)))


@pytest.mark.parametrize("active_original", [False, True])
def test_force_reconstructs_either_active_choice_from_the_original_file(
    imported: tuple[BrowserWorkspace, Path],
    active_original: bool,
) -> None:
    workspace, path = imported
    original_binding = workspace.workspace.reference_sha256
    _ = _create(workspace)
    reduced_binding = workspace.workspace.reference_sha256
    if active_original:
        _ = _activate(workspace, original_binding)
    saved = graph(workspace)["recipe"]
    identity = workspace.source_identity
    generation = workspace.workspace_generation
    recovered = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        recovered.prepare_source_json(
            json.dumps(
                {
                    "operationId": "recovery",
                    "kind": "scan",
                    "filename": path.name,
                    "generation": 0,
                    "token": graph(recovered)["token"],
                }
            ),
            str(path),
            recovery_generation=generation,
            recovery_identity=identity,
            recovery_recipe=saved,
        )
    )
    assert candidate["source_identity"] == identity
    _ = promote(recovered, candidate)
    assert recovered.workspace_generation == generation
    assert graph(recovered)["recipe"] == saved
    target = reduced_binding if active_original else original_binding
    _ = _activate(recovered, target)
    assert recovered.workspace.reference_sha256 == target


@pytest.mark.parametrize(
    "fault", ["extra", "upload", "stale", "settings", "cancel", "cancel-mid"]
)
def test_failed_creation_preserves_the_accepted_project(
    imported: tuple[BrowserWorkspace, Path],
    fault: str,
) -> None:
    workspace, path = imported
    before, original = graph(workspace), workspace.workspace
    request = _request(workspace, "create-virtual-scan", settings={"divisions": 4})
    if fault == "extra":
        request["binding"] = "0" * 64
    elif fault == "stale":
        request["token"] = "0" * 64
    elif fault == "settings":
        request["settings"] = {"divisions": True}
    stopped = fault == "cancel"

    def progress(value: ScanProgress) -> None:
        nonlocal stopped
        if fault == "cancel-mid" and value.phase == "cluster-edges":
            stopped = True

    response = json.loads(
        workspace.prepare_source_json(
            json.dumps(request),
            str(path) if fault == "upload" else "",
            progress=progress,
            cancelled=lambda: stopped,
        )
    )
    assert response["ok"] is False
    assert graph(workspace) == before and workspace.workspace is original


def test_switches_do_not_retain_old_graphs_and_unknown_bindings_do_not_publish(
    imported: tuple[BrowserWorkspace, Path],
) -> None:
    workspace, _ = imported
    original = workspace.workspace.reference_sha256
    _ = _create(workspace)
    reduced = workspace.workspace.reference_sha256
    retired: list[weakref.ReferenceType[object]] = []
    for index in range(8):
        retired.append(weakref.ref(workspace.graph))
        _ = _activate(workspace, original if index % 2 == 0 else reduced)
    _ = gc.collect()
    assert all(reference() is None for reference in retired)
    before = graph(workspace)
    failure = json.loads(
        workspace.prepare_source_json(
            json.dumps(_request(workspace, "activate-scan", binding="0" * 64))
        )
    )
    assert failure["ok"] is False and graph(workspace) == before
