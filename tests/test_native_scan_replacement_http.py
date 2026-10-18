"""Imported-scan replacement uses the existing fenced native upload lease."""

# Deliberate coordinator ownership checks for stale/cancel/cleanup races.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import io
import struct
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from experiments.feature_graph import Recipe
from experiments.nozzle_browser import ExampleRequest
from experiments.scan_project import ScanProject
from experiments.selection_volume_transactions import author_volume
from tests.test_native_scan_http import NativeHttp, triangle_bytes
from tests.test_native_scan_http import native_http as native_http


def replacement_bytes() -> bytes:
    raw = bytearray(triangle_bytes())
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    struct.pack_into("<f", raw, start + 24, 2.0)
    struct.pack_into("<f", raw, start + 48 + 4, 2.0)
    return bytes(raw)


def authored(http: NativeHttp) -> dict[str, Any]:
    _ = http.import_triangle()
    current = http.metadata()
    recipe = deepcopy(current["graph"]["recipe"])
    recipe["nodes"].extend(
        [
            {
                "id": "patch",
                "operation": "selection",
                "label": "Patch",
                "source": "scan",
                "ids": [0, 1, 2],
                "group_id": "group",
            },
            {
                "id": "plane",
                "operation": "fit",
                "label": "Plane",
                "kind": "plane",
                "selections": ["patch"],
            },
            {
                "id": "datum",
                "operation": "point",
                "label": "Datum",
                "initial_coordinates": [1, 2, 3],
            },
        ]
    )
    recipe.update(output="plane", groups=[{"id": "group", "label": "Group"}])
    status, changed, _ = http.request(
        "/api/graph",
        {"token": current["graph"]["token"], "recipe": recipe},
        generation=current["project_generation"],
    )
    assert status == 200, changed
    _ = author_volume(
        http.server.graph,
        changed["token"],
        selection="patch",
        volume="region",
        label="Saved footprint",
        kind="mesh",
    )
    return http.metadata()


def reserve(
    http: NativeHttp, raw: bytes, **changes: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    cursor = http.cursor()
    status, operation, _ = http.request(
        "/api/sources/imports",
        {
            **cursor,
            "filename": "replacement.ply",
            "size": len(raw),
            "purpose": "replace-scan",
            **changes,
        },
    )
    assert status == 202, operation
    assert operation["purpose"] == "replace-scan" and operation["uploadRequired"]
    return cursor, operation


def upload(http: NativeHttp, operation: dict[str, Any], raw: bytes) -> dict[str, Any]:
    status, response, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, response
    return http.finish(operation)


def test_native_replacement_retains_definitions_and_archives_with_pending_new_entries(
    native_http: NativeHttp,
) -> None:
    http = native_http
    before = authored(http)
    assert before["source_replacement"] == {
        "supported": True,
        "version": 1,
        "alignment": "already-aligned",
        "source_kind": "generic-scan",
    }
    old_recipe = before["graph"]["recipe"]
    old_entry = old_recipe["nodes"][1]["entries"][0]
    cursor, operation = reserve(http, replacement_bytes())
    assert http.metadata()["graph"] == before["graph"]
    result = upload(http, operation, replacement_bytes())
    assert result["status"] == "accepted", result.get("error", result)
    after = http.metadata()
    assert (
        result["acceptedGeneration"]
        == after["project_generation"]
        == cursor["generation"] + 1
    )
    assert after["source"]["source_sha256"] != before["source"]["source_sha256"]
    assert http.server.workspace.original_bytes == replacement_bytes()
    recipe = after["graph"]["recipe"]
    assert [node["id"] for node in recipe["nodes"]] == [
        node["id"] for node in old_recipe["nodes"]
    ]
    assert recipe["nodes"][2:] == old_recipe["nodes"][2:]
    assert (
        recipe["groups"] == old_recipe["groups"]
        and recipe["output"] == old_recipe["output"]
    )
    entries = recipe["nodes"][1]["entries"]
    assert entries[0] == old_entry
    assert len(entries) == 2
    assert entries[1]["mode"] == "generated" and entries[1]["source_volume"] is None
    assert entries[1]["ids"] is None and entries[1]["volumes"] == []
    assert (
        after["graph"]["states"]["patch"]
        == after["graph"]["states"]["plane"]
        == "needs_input"
    )
    assert after["graph"]["errors"] == {} and after["graph"]["results"] == {}
    assert after["graph"]["diagnostics"]["patch"]["action"] == "choose_source_volume"
    assert http.request(before["positions"]["url"])[0] == 409
    assert http.request(after["positions"]["url"])[0] == 200
    assert not list(http.staging.rglob("*.upload"))
    # Returning to known bytes finds the saved original entry without painting
    # again, while retaining the intermediate scan's separate pending entry.
    _, returning = reserve(http, triangle_bytes())
    restored = upload(http, returning, triangle_bytes())
    assert restored["status"] == "accepted", restored.get("error", restored)
    returned = http.metadata()
    assert returned["project_generation"] == after["project_generation"] + 1
    assert returned["graph"]["recipe"]["nodes"][1]["entries"] == entries
    assert returned["graph"]["memberships"]["patch"] == [0, 1, 2]


def test_same_scan_replacement_reuses_the_existing_entry_without_reselection(
    native_http: NativeHttp,
) -> None:
    http = native_http
    before = authored(http)
    _, operation = reserve(http, triangle_bytes())
    result = upload(http, operation, triangle_bytes())
    assert result["status"] == "accepted", result.get("error", result)
    after = http.metadata()
    assert after["project_generation"] == before["project_generation"] + 1
    assert (
        after["graph"]["recipe"]["nodes"][1:] == before["graph"]["recipe"]["nodes"][1:]
    )
    assert (
        after["graph"]["recipe"]["nodes"][0]["descriptor"]["filename"]
        == "replacement.ply"
    )
    assert after["graph"]["memberships"]["patch"] == [0, 1, 2]


def test_invalid_replacement_preserves_exact_accepted_owner_and_archive(
    native_http: NativeHttp,
) -> None:
    http = native_http
    before = authored(http)
    owner, accepted = http.server._project, http.server.accepted()
    raw = b"not a PLY scan"
    _, operation = reserve(http, raw)
    result = upload(http, operation, raw)
    assert result["status"] == "failed" and result["error"]
    assert http.server._project is owner and http.server.workspace is accepted.workspace
    assert http.server.graph is accepted.graph
    assert http.metadata()["graph"] == before["graph"]
    assert not http.server.source_imports.busy and not list(
        http.staging.rglob("*.upload")
    )


def test_reserved_replacement_shares_the_upload_lease_and_cancel_preserves_work(
    native_http: NativeHttp,
) -> None:
    http = native_http
    before = authored(http)
    cursor, operation = reserve(http, replacement_bytes())
    status, busy, _ = http.request(
        "/api/sources/imports",
        {**cursor, "filename": "other.ply", "size": len(triangle_bytes())},
    )
    assert status == 409 and busy["code"] == "source_busy"
    status, cancelled, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/cancel", cursor
    )
    assert status == 200 and cancelled["status"] == "cancelled"
    assert http.metadata()["graph"] == before["graph"]
    assert not http.server.source_imports.busy
    status, response, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", replacement_bytes()
    )
    assert status == 422 and response["code"] == "invalid_upload"
    assert http.metadata()["graph"] == before["graph"]


def test_replacement_requires_current_cursor_existing_import_and_bounded_upload(
    native_http: NativeHttp,
) -> None:
    http = native_http
    assert http.metadata()["source_replacement"]["supported"] is False
    empty_cursor = http.cursor()
    payload = {
        **empty_cursor,
        "filename": "scan.ply",
        "size": len(triangle_bytes()),
        "purpose": "replace-scan",
    }
    status, missing, _ = http.request("/api/sources/imports", payload)
    assert status == 422 and missing["code"] == "source_required"
    _ = http.import_triangle()
    status, stale, _ = http.request("/api/sources/imports", payload)
    assert status == 409 and stale["code"] == "stale_source"
    before = http.metadata()
    payload.update(http.cursor())
    payload["size"] = http.server.source_imports.limits.max_source_bytes + 1
    status, limited, _ = http.request("/api/sources/imports", payload)
    assert status == 422 and limited["code"] == "source_limit"
    assert (
        http.metadata()["graph"] == before["graph"]
        and not http.server.source_imports.busy
    )


def test_legacy_source_frame_replacement_is_explicitly_unsupported(
    native_http: NativeHttp,
) -> None:
    http = native_http
    cursor = http.cursor()
    http.server.select_example(
        ExampleRequest(example="nozzle", token=cursor["token"]),
        generation=cursor["generation"],
    )
    before = http.metadata()
    assert http.server._project is None
    assert before["source_replacement"]["supported"] is False
    status, response, _ = http.request(
        "/api/sources/imports",
        {
            **http.cursor(),
            "filename": "replacement.ply",
            "size": len(replacement_bytes()),
            "purpose": "replace-scan",
        },
    )
    assert status == 422 and response["code"] == "unsupported_replacement"
    assert "example frames" in response["error"]
    assert http.metadata()["graph"] == before["graph"]
    assert not http.server.source_imports.busy


@pytest.mark.parametrize("race", ["cancel", "edit", "owner", "cleanup"])
def test_prepared_replacement_is_private_until_cleanup_and_final_owner_fence(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    http = native_http
    before = authored(http)
    owner, accepted = http.server._project, http.server.accepted()
    entered, release = Event(), Event()
    original = ScanProject.prepare_replace

    def gated(self: ScanProject, *args: Any, **kwargs: Any) -> Any:
        candidate = original(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return candidate

    monkeypatch.setattr(ScanProject, "prepare_replace", gated)
    cursor, operation = reserve(http, replacement_bytes())
    status, body, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", replacement_bytes()
    )
    assert status in {200, 202}, body
    assert entered.wait(10)
    try:
        assert http.metadata()["graph"] == before["graph"]
        status, progress, _ = http.request(
            f"/api/sources/imports/{operation['operationId']}"
        )
        assert (
            status == 200
            and progress["status"] == "preparing"
            and not progress["terminal"]
        )
        if race == "cancel":
            status, cancelled, _ = http.request(
                f"/api/sources/imports/{operation['operationId']}/cancel", cursor
            )
            assert status == 200 and cancelled["status"] == "cancelling"
        elif race == "edit":
            changed = deepcopy(before["graph"]["recipe"])
            changed["nodes"][-1]["label"] = "User edit while preparing"
            _ = http.server.graph.replace(
                Recipe.model_validate(changed), cursor["token"]
            )
        elif race == "owner":
            replacement = ScanProject()
            initial = replacement.accepted
            prepared = replacement.prepare_add(
                io.BytesIO(triangle_bytes()),
                "generated.ply",
                http.server.source_imports.limits,
                generation=initial.generation,
                token=str(initial.graph.snapshot()["token"]),
            )
            fresh = replacement.commit(prepared)
            _ = fresh.graph.replace(
                Recipe.model_validate(before["graph"]["recipe"]),
                str(fresh.graph.snapshot()["token"]),
            )
            with http.server.lock:
                http.server._project = replacement
                http.server._generation_offset = cursor["generation"] - fresh.generation
            assert http.cursor() == cursor
        else:
            unlink = Path.unlink

            def fail(path: Path, *args: Any, **kwargs: Any) -> None:
                if path.suffix == ".upload":
                    raise OSError("owned replacement cleanup injection")
                unlink(path, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", fail)
    finally:
        release.set()
    result = http.finish(operation)
    assert result["status"] == ("cancelled" if race == "cancel" else "failed"), result
    if race == "owner":
        assert http.server._project is not owner and http.cursor() == cursor
    else:
        assert (
            http.server._project is owner
            and http.server.workspace is accepted.workspace
        )
        assert http.server.graph is accepted.graph
        if race == "edit":
            assert (
                http.metadata()["graph"]["recipe"]["nodes"][-1]["label"]
                == "User edit while preparing"
            )
        else:
            assert http.metadata()["graph"] == before["graph"]
    retained = http.server.source_imports._operations[operation["operationId"]]
    assert (
        retained.context is None and retained.recipe is None and retained.path is None
    )
    monkeypatch.undo()
