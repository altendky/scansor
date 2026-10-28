"""Replayable browser source streams, archive transactions and bounded export."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.source_streams import BlobInput
from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.project_archive import ProjectArchiveLimits, write_project_archive
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import ScanLimits
from tests.native_scan_fixture import native_grid_fixture

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 20_000_000, chunk_bytes=4096)
ARCHIVE_LIMITS = ProjectArchiveLimits(chunk_bytes=4096)


def success(value: str) -> dict[str, Any]:
    envelope = json.loads(value)
    assert envelope["ok"], envelope
    return envelope["result"]


def graph(workspace: BrowserWorkspace) -> dict[str, Any]:
    return success(workspace.call_json("getGraph"))


def promote(workspace: BrowserWorkspace, candidate: dict[str, Any]) -> None:
    _ = success(
        workspace.promote_source_json(
            json.dumps(
                {
                    "operationId": candidate["operationId"],
                    "workspaceGeneration": candidate["workspaceGeneration"],
                }
            )
        )
    )


def original() -> BrowserWorkspace:
    workspace = BrowserWorkspace(
        None, scan_limits=LIMITS, project_limits=ARCHIVE_LIMITS
    )
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "original",
                    "kind": "scan",
                    "filename": "original.ply",
                    "generation": 0,
                    "token": graph(workspace)["token"],
                }
            ),
            source_input=BytesInput(native_grid_fixture(17, 17).raw, "original.ply"),
        )
    )
    promote(workspace, candidate)
    return workspace


def export(workspace: BrowserWorkspace) -> bytes:
    _ = success(
        workspace.prepare_project_export_json(
            json.dumps(
                {
                    "generation": workspace.workspace_generation,
                    "token": graph(workspace)["token"],
                }
            )
        )
    )
    chunks: list[bytes] = []
    try:
        while True:
            state = success(workspace.next_project_export_json())
            if state["complete"]:
                assert state["receipt"]["bytes_written"] == sum(map(len, chunks))
                break
            chunk = workspace.project_export_chunk
            assert (
                len(chunk) == state["bytes"]
                and len(chunk) <= ARCHIVE_LIMITS.chunk_bytes
            )
            chunks.append(chunk)
    finally:
        workspace.clear_project_export()
    return b"".join(chunks)


def test_blob_input_is_seekable_replayable_and_ranges_are_bounded() -> None:
    raw = bytes(range(256)) * 100
    calls: list[tuple[int, int]] = []

    def read(offset: int, count: int) -> bytes:
        calls.append((offset, count))
        return raw[offset : offset + count]

    source = BlobInput(len(raw), "selected.scansor", read, 128)
    for _ in range(2):
        with source.open() as stream:
            assert stream.read(1000) == raw[:1000]
            assert stream.seek(-25, io.SEEK_END) == len(raw) - 25
            assert stream.read() == raw[-25:]
            assert stream.seek(0) == 0
            assert stream.read() == raw
    assert calls and max(count for _, count in calls) <= 128


def test_blob_input_converts_js_ranges_and_rejects_truncation() -> None:
    class Converted:
        def to_py(self) -> bytes:
            return b"abc"

    with BlobInput(3, "input", lambda _offset, _count: Converted(), 3).open() as stream:
        assert stream.read() == b"abc"
    with (
        BlobInput(3, "input", lambda _offset, _count: b"ab", 3).open() as stream,
        pytest.raises(OSError, match="incomplete range"),
    ):
        _ = stream.read()


def test_archive_open_replays_blob_without_source_snapshot_and_export_reopens() -> None:
    authored = original()
    actions = graph(authored)["recipe"]
    sink = io.BytesIO()
    _ = write_project_archive(
        actions, authored.workspace.source, sink, limits=ARCHIVE_LIMITS
    )
    raw = sink.getvalue()
    reads: list[int] = []

    def read(offset: int, count: int) -> bytes:
        reads.append(count)
        return raw[offset : offset + count]

    incoming = BlobInput(len(raw), "single.scansor", read, 4096)
    fresh = BrowserWorkspace(None, scan_limits=LIMITS, project_limits=ARCHIVE_LIMITS)
    candidate = success(
        fresh.prepare_source_json(
            json.dumps(
                {
                    "operationId": "open",
                    "kind": "scan",
                    "purpose": "open-project",
                    "confirmedDiscard": True,
                    "generation": 0,
                    "token": graph(fresh)["token"],
                }
            ),
            source_input=incoming,
        )
    )
    assert graph(fresh)["recipe"]["nodes"] == []
    assert candidate["source_authority"]["kind"] == "project-file"
    assert candidate["source_authority"]["member"].startswith("scans/")
    promote(fresh, candidate)
    assert graph(fresh)["recipe"] == actions
    assert fresh.workspace.source is not None
    assert not hasattr(fresh.workspace, "original_bytes")
    assert max(reads) <= 4096
    saved = export(fresh)
    another = BrowserWorkspace(None, scan_limits=LIMITS, project_limits=ARCHIVE_LIMITS)
    reopened = success(
        another.prepare_source_json(
            json.dumps(
                {
                    "operationId": "reopen",
                    "kind": "scan",
                    "purpose": "open-project",
                    "confirmedDiscard": True,
                    "generation": 0,
                    "token": graph(another)["token"],
                }
            ),
            source_input=BytesInput(saved, "roundtrip.scansor"),
        )
    )
    promote(another, reopened)
    assert graph(another)["recipe"] == actions
    assert another.source_identity == fresh.source_identity


@pytest.mark.parametrize("active_original", [False, True])
def test_archive_force_reconstructs_current_virtual_choice(
    active_original: bool,
) -> None:
    workspace = original()
    original_binding = workspace.workspace.reference_sha256
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "virtual",
                    "kind": "scan",
                    "purpose": "create-virtual-scan",
                    "settings": {"divisions": 4},
                    "generation": workspace.workspace_generation,
                    "token": graph(workspace)["token"],
                }
            )
        )
    )
    promote(workspace, candidate)
    if active_original:
        candidate = success(
            workspace.prepare_source_json(
                json.dumps(
                    {
                        "operationId": "activate",
                        "kind": "scan",
                        "purpose": "activate-scan",
                        "binding": original_binding,
                        "generation": workspace.workspace_generation,
                        "token": graph(workspace)["token"],
                    }
                )
            )
        )
        promote(workspace, candidate)
    saved = export(workspace)
    fresh = BrowserWorkspace(None, scan_limits=LIMITS, project_limits=ARCHIVE_LIMITS)
    opened = success(
        fresh.prepare_source_json(
            json.dumps(
                {
                    "operationId": "open",
                    "kind": "scan",
                    "purpose": "open-project",
                    "confirmedDiscard": True,
                    "generation": 0,
                    "token": graph(fresh)["token"],
                }
            ),
            source_input=BytesInput(saved, "source.scansor"),
        )
    )
    promote(fresh, opened)
    restored = BrowserWorkspace(None, scan_limits=LIMITS, project_limits=ARCHIVE_LIMITS)
    recovered = success(
        restored.prepare_source_json(
            json.dumps(
                {
                    "operationId": "recover",
                    "kind": "scan",
                    "filename": "source.scansor",
                    "generation": 0,
                    "token": graph(restored)["token"],
                }
            ),
            source_input=BytesInput(saved, "source.scansor"),
            recovery_generation=fresh.workspace_generation,
            recovery_identity=fresh.source_identity,
            recovery_recipe=graph(fresh)["recipe"],
            recovery_authority=opened["source_authority"],
        )
    )
    promote(restored, recovered)
    assert restored.source_identity == fresh.source_identity
    assert graph(restored)["recipe"] == graph(fresh)["recipe"]
    assert restored.workspace_generation == fresh.workspace_generation


def test_failed_or_cancelled_archive_open_keeps_current_source() -> None:
    workspace = original()
    before, identity = graph(workspace), workspace.source_identity
    for cancelled in (False, True):
        failed = json.loads(
            workspace.prepare_source_json(
                json.dumps(
                    {
                        "operationId": "invalid",
                        "kind": "scan",
                        "purpose": "open-project",
                        "confirmedDiscard": True,
                        "generation": workspace.workspace_generation,
                        "token": before["token"],
                    }
                ),
                source_input=BytesInput(b"invalid archive", "bad.scansor"),
                cancelled=lambda value=cancelled: value,
            )
        )
        assert failed["ok"] is False
        assert graph(workspace) == before and workspace.source_identity == identity


def test_portable_legacy_archive_can_be_saved_reopened_and_force_replayed() -> None:
    limits = ScanLimits(2_000_000, 50_000, 100_000, 100_000_000, chunk_bytes=4096)
    packaged = BrowserWorkspace(
        Path("examples/nozzle-bayonette-simplified"),
        scan_limits=limits,
        project_limits=ARCHIVE_LIMITS,
    )
    saved = export(packaged)
    fresh = BrowserWorkspace(None, scan_limits=limits, project_limits=ARCHIVE_LIMITS)
    candidate = success(
        fresh.prepare_source_json(
            json.dumps(
                {
                    "operationId": "legacy",
                    "kind": "scan",
                    "purpose": "open-project",
                    "confirmedDiscard": True,
                    "generation": 0,
                    "token": graph(fresh)["token"],
                }
            ),
            source_input=BytesInput(saved, "portable.scansor"),
        )
    )
    promote(fresh, candidate)
    assert graph(fresh)["recipe"] == graph(packaged)["recipe"]
    assert fresh.source_identity == packaged.source_identity
    assert candidate["source_mode"]["kind"] == "example"
    reopened_bytes = export(fresh)
    recovered = BrowserWorkspace(
        None, scan_limits=limits, project_limits=ARCHIVE_LIMITS
    )
    receipt = success(
        recovered.prepare_source_json(
            json.dumps(
                {
                    "operationId": "recover",
                    "kind": "example",
                    "example": "nozzle",
                    "filename": "portable.scansor",
                    "generation": 0,
                    "token": graph(recovered)["token"],
                }
            ),
            source_input=BytesInput(reopened_bytes, "portable.scansor"),
            recovery_generation=fresh.workspace_generation,
            recovery_identity=fresh.source_identity,
            recovery_recipe=graph(fresh)["recipe"],
            recovery_authority=candidate["source_authority"],
        )
    )
    promote(recovered, receipt)
    assert recovered.source_identity == packaged.source_identity
    assert graph(recovered)["recipe"] == graph(packaged)["recipe"]
