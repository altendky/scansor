"""STL originals cross worker/native portable archives without byte snapshots."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any

import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.project_archive_limits import ProjectArchiveLimits
from experiments.scan_sources import BytesInput
from tests.test_browser_scan_workspace import graph, promote, success
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_project_archive_worker import export
from tests.test_stl_integration import (
    LIMITS,
    assert_plane,
    author_shared_volume,
    choose_volume,
)
from tests.test_stl_virtual_scan import grid_bytes


def request(
    workspace: BrowserWorkspace, purpose: str | None = None, **controls: Any
) -> str:
    return json.dumps(
        {
            "operationId": "stl-archive",
            "kind": "scan",
            "generation": workspace.workspace_generation,
            "token": graph(workspace)["token"],
            **({"purpose": purpose} if purpose is not None else {}),
            **controls,
        }
    )


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
@pytest.mark.parametrize("active_original", [True, False])
def test_stl_worker_native_archive_reopen_and_archive_backed_force(
    native_http: NativeHttp, encoding: str, active_original: bool
) -> None:
    raw = grid_bytes(encoding)
    workspace = BrowserWorkspace(
        None, scan_limits=LIMITS, project_limits=ProjectArchiveLimits(chunk_bytes=4096)
    )
    candidate = success(
        workspace.prepare_source_json(
            request(workspace, filename="original.stl"),
            source_input=BytesInput(raw, "original.stl"),
        )
    )
    _ = promote(workspace, candidate)
    original = workspace.workspace.reference_sha256
    author_shared_volume(workspace)
    candidate = success(
        workspace.prepare_source_json(
            request(workspace, "create-virtual-scan", settings={"divisions": 4})
        )
    )
    _ = promote(workspace, candidate)
    _ = workspace.graph.replace(
        choose_volume(graph(workspace)["recipe"]), graph(workspace)["token"]
    )
    if active_original:
        _ = promote(
            workspace,
            success(
                workspace.prepare_source_json(
                    request(workspace, "activate-scan", binding=original)
                )
            ),
        )
    rows = 289 if active_original else 25
    assert_plane(workspace.graph.ensure_current(graph(workspace)["token"], ["plane"]))
    saved = graph(workspace)["recipe"]
    packed = export(workspace)
    member = f"scans/{hashlib.sha256(raw).hexdigest()}.stl"
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        assert archive.namelist().count(member) == 1
        assert archive.read(member) == raw
        assert not any(name.endswith(".ply") for name in archive.namelist())
    http = native_http
    status, operation, _ = http.request(
        "/api/project/open",
        {
            **http.cursor(),
            "filename": "renamed.scansor",
            "size": len(packed),
            "confirmedDiscard": True,
        },
    )
    assert status == 202, operation
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", packed
    )
    assert status in {200, 202}, result
    finished = http.finish(operation)
    assert finished["status"] == "accepted", json.dumps(finished)
    assert http.metadata()["graph"]["recipe"] == saved
    assert http.metadata()["vertices"] == rows
    assert_plane(http.server.graph.ensure_current(http.cursor()["token"], ["plane"]))
    status, repacked, _ = http.request("/api/project/export", http.cursor())
    assert status == 200 and isinstance(repacked, bytes)
    fresh = BrowserWorkspace(
        None, scan_limits=LIMITS, project_limits=ProjectArchiveLimits(chunk_bytes=4096)
    )
    opened = success(
        fresh.prepare_source_json(
            request(fresh, "open-project", confirmedDiscard=True),
            source_input=BytesInput(repacked, "portable.scansor"),
        )
    )
    _ = promote(fresh, opened)
    assert graph(fresh)["recipe"] == saved
    assert_plane(fresh.graph.ensure_current(graph(fresh)["token"], ["plane"]))
    recovered = BrowserWorkspace(
        None, scan_limits=LIMITS, project_limits=ProjectArchiveLimits(chunk_bytes=4096)
    )
    candidate = success(
        recovered.prepare_source_json(
            request(recovered, filename="portable.scansor"),
            source_input=BytesInput(repacked, "portable.scansor"),
            recovery_generation=fresh.workspace_generation,
            recovery_identity=fresh.source_identity,
            recovery_recipe=saved,
            recovery_authority=opened["source_authority"],
        )
    )
    _ = promote(recovered, candidate)
    assert graph(recovered)["recipe"] == saved
    assert recovered.workspace_generation == fresh.workspace_generation
    assert recovered.source_identity == fresh.source_identity
    assert_plane(recovered.graph.ensure_current(graph(recovered)["token"], ["plane"]))
    for owner in (workspace, fresh, recovered):
        assert json.loads(owner.call_json("close"))["ok"]
