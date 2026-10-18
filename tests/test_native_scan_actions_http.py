"""Saved actions use the native source lease and one accepted publication."""

# These race tests deliberately inspect coordinator ownership/history internals.
# pyright: reportPrivateUsage=false

import io
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pytest

from experiments import native_scan_import, nozzle_browser
from experiments.actions_limits import actions_limits
from experiments.feature_graph import Recipe
from experiments.feature_recipe_import import import_recipe
from experiments.nozzle_browser import ExampleRequest, ProjectResetRequest
from experiments.scan_actions import inspect_actions
from experiments.scan_project import ScanProject
from tests.test_native_scan_http import NativeHttp, triangle_bytes
from tests.test_native_scan_http import native_http as native_http

EMPTY = {"schema_version": 2, "nodes": [], "output": None}


def test_native_actions_request_exact_byte_boundary(
    native_http: NativeHttp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = native_http
    original = http.metadata()["graph"]
    payload = json.dumps({"token": original["token"], "recipe": EMPTY}).encode()
    maximum = 512
    monkeypatch.setattr(nozzle_browser, "MAX_ACTIONS_REQUEST_BYTES", maximum)
    admitted = payload + b" " * (maximum - len(payload))
    headers = {"Content-Type": "application/json"}
    status, state, _ = http.request(
        "/api/graph", admitted, generation=0, headers=headers
    )
    assert status == 200, state
    status, rejected, _ = http.request(
        "/api/graph", admitted + b" ", generation=0, headers=headers
    )
    assert status == 422 and "512-byte" in rejected["error"]
    assert http.metadata()["graph"]["recipe"] == original["recipe"]


def open_actions(
    http: NativeHttp, recipe: dict[str, Any], raw: bytes | None = None
) -> dict[str, Any]:
    payload = {**http.cursor(), "recipe": recipe, "confirmedDiscard": True}
    if raw is not None:
        payload["source"] = {"filename": "renamed.ply", "size": len(raw)}
    status, operation, _ = http.request("/api/actions/open", payload)
    assert status in {200, 202}, operation
    assert operation["purpose"] == "open-actions"
    assert operation["uploadRequired"] is (raw is not None)
    if raw is not None:
        status, result, _ = http.request(
            f"/api/sources/imports/{operation['operationId']}/bytes", raw
        )
        assert status in {200, 202}, result
    return http.finish(operation)


def saved(http: NativeHttp) -> dict[str, Any]:
    _ = http.import_triangle()
    return http.metadata()["graph"]["recipe"]


def test_native_large_actions_and_known_recipe_polling(native_http: NativeHttp) -> None:
    http = native_http
    recipe = saved(http)
    recipe["nodes"].extend(
        {
            "id": f"p{index}",
            "operation": "point",
            "label": f"Point {index} " + "界" * 100,
            "initial_coordinates": [1, 2, 3],
        }
        for index in range(1500)
    )
    original = http.metadata()["graph"]
    assert len(json.dumps(recipe).encode()) > 1_000_000
    assert http.metadata()["actions_limits"] == actions_limits()
    status, changed, _ = http.request(
        "/api/graph",
        {"token": original["token"], "recipe": recipe},
        generation=http.cursor()["generation"],
    )
    assert status == 200, changed
    status, full, _ = http.request("/api/graph")
    assert status == 200 and len(full["recipe"]["nodes"]) == 1501
    status, compact, _ = http.request(
        f"/api/graph?revision={full['revision']}&recipe_token={full['token']}"
    )
    assert status == 200 and compact["unchanged"] and "recipe" not in compact
    status, wrong, _ = http.request(
        f"/api/graph?revision={full['revision']}&recipe_token=obsolete"
    )
    assert (
        status == 200
        and wrong["recipe"] == full["recipe"]
        and not wrong.get("unchanged")
    )
    status, error, _ = http.request(
        "/api/project/reset", {**http.cursor(), "unknown": "x" * 1_000_000}
    )
    assert status == 422 and "1,000,000-byte" in error["error"]
    assert http.metadata()["graph"]["token"] == full["token"]


def test_inspect_is_read_only_and_matching_open_reuses_source_with_fresh_graph(
    native_http: NativeHttp,
) -> None:
    http = native_http
    recipe = saved(http)
    old = http.server.accepted()
    owner = http.server._project
    status, result, _ = http.request(
        "/api/actions/inspect", {**http.cursor(), "recipe": recipe}
    )
    assert status == 200 and result["classification"] == "matching"
    assert result["descriptor"] == recipe["nodes"][0]["descriptor"]
    assert not result["uploadRequired"] and not http.server.source_imports.busy
    assert http.server._project is owner and http.server.graph is old.graph
    record = open_actions(http, recipe)
    assert (
        record["status"] == "accepted"
        and record["acceptedGeneration"] == old.generation + 1
    )
    assert http.server._project is not owner
    assert http.server.workspace is old.workspace and http.server.graph is not old.graph
    assert set(
        cast(dict[str, str], http.server.graph.snapshot()["states"]).values()
    ) <= {"unevaluated"}


def test_locate_after_reset_validates_exact_bytes_and_preserves_owner_on_wrong_file(
    native_http: NativeHttp,
) -> None:
    http = native_http
    recipe = saved(http)
    _ = http.server.reset_project(
        ProjectResetRequest(**http.cursor(), confirmedDiscard=True)
    )
    owner = http.server._project
    cursor = http.cursor()
    status, result, _ = http.request(
        "/api/actions/inspect", {**cursor, "recipe": recipe}
    )
    assert (
        status == 200
        and result["classification"] == "locate"
        and result["uploadRequired"]
    )
    wrong = bytearray(triangle_bytes())
    wrong[-9] ^= 1
    failed = open_actions(http, recipe, bytes(wrong))
    assert failed["status"] == "failed"
    assert http.server._project is owner and http.cursor() == cursor
    assert not list(http.staging.rglob("*.upload"))
    accepted = open_actions(http, recipe, triangle_bytes())
    assert accepted["status"] == "accepted"
    assert accepted["acceptedGeneration"] == cursor["generation"] + 1
    assert http.server.workspace.original_bytes == triangle_bytes()


@pytest.mark.parametrize(
    "case", ["discard", "unexpected", "stale", "length", "invalid"]
)
def test_open_preflight_failures_do_not_reserve_or_replace(
    native_http: NativeHttp, case: str
) -> None:
    http = native_http
    recipe = saved(http)
    if case == "length":
        _ = http.server.reset_project(
            ProjectResetRequest(**http.cursor(), confirmedDiscard=True)
        )
    owner, cursor = http.server._project, http.cursor()
    payload = {**cursor, "recipe": recipe, "confirmedDiscard": True}
    expected = "discard_required"
    if case == "discard":
        payload["confirmedDiscard"] = False
    elif case == "unexpected":
        payload["source"] = {"filename": "/etc/passwd", "size": 1}
        expected = "unexpected_source"
    elif case == "stale":
        payload["generation"] = cursor["generation"] - 1
        expected = "stale_source"
    elif case == "length":
        payload["source"] = {"filename": "same.ply", "size": 1}
        expected = "source_mismatch"
    else:
        payload["recipe"] = {"schema_version": 999, "nodes": []}
        expected = "invalid_actions"
    status, response, _ = http.request("/api/actions/open", payload)
    assert status in {409, 422} and response["code"] == expected
    assert http.server._project is owner and http.cursor() == cursor
    assert not http.server.source_imports.busy and not list(
        http.staging.rglob("*.upload")
    )


def test_empty_open_advances_epoch_and_old_cursor_cannot_reopen(
    native_http: NativeHttp,
) -> None:
    http = native_http
    before = http.cursor()
    result = open_actions(http, EMPTY)
    assert (
        result["status"] == "accepted"
        and result["acceptedGeneration"] == before["generation"] + 1
    )
    status, response, _ = http.request(
        "/api/actions/inspect", {**before, "recipe": EMPTY}
    )
    assert status == 409 and response["code"] == "stale_source"
    again = open_actions(http, EMPTY)
    assert again["acceptedGeneration"] == result["acceptedGeneration"] + 1


def test_native_warnings_survive_lost_open_reply_in_accepted_metadata_and_history_is_small(
    native_http: NativeHttp,
) -> None:
    http = native_http
    recipe = saved(http)
    source = recipe["nodes"][0]
    recipe["nodes"].append(
        {
            "id": "selected",
            "label": source["label"],
            "operation": "selection",
            "source": source["id"],
            "ids": [0, 1, 2],
        }
    )
    recipe["output"] = "selected"
    cursor = http.cursor()
    status, inspected, _ = http.request(
        "/api/actions/inspect", {**cursor, "recipe": recipe}
    )
    assert status == 200 and len(inspected["import_warnings"]) == 1
    # Send the ORIGINAL recipe; a normalized inspection would lose this warning.
    record = open_actions(http, recipe)
    assert record["status"] == "accepted" and record["import_warning_count"] == 1
    assert http.metadata()["import_warnings"] == inspected["import_warnings"]
    retained = http.server.source_imports._operations[record["operationId"]]
    assert (
        retained.context is None
        and retained.recipe is None
        and retained.project is None
    )
    assert retained.filename == "" and retained.path is None
    assert "import_warnings" not in record and "recipe" not in record
    _, late, _ = http.request(
        f"/api/sources/imports/{record['operationId']}/cancel", cursor
    )
    assert late == record
    for _ in range(17):
        assert open_actions(http, EMPTY)["status"] == "accepted"
    assert len(http.server.source_imports._operations) == 16
    assert http.metadata()["import_warnings"] == []


@pytest.mark.parametrize("path", ["/api/actions/inspect", "/api/actions/open"])
def test_actions_routes_retain_origin_and_explicit_request_protection(
    native_http: NativeHttp, path: str
) -> None:
    http = native_http
    owner, cursor = http.server._project, http.cursor()
    for headers in (
        {"X-Scansor-Request": "0"},
        {"Content-Type": "text/plain"},
        {"Origin": "https://unrelated.invalid"},
    ):
        status, _, _ = http.request(
            path,
            {**cursor, "recipe": EMPTY, "confirmedDiscard": True}
            if path.endswith("open")
            else {**cursor, "recipe": EMPTY},
            headers=headers,
        )
        assert status == 403
    assert http.server._project is owner and http.cursor() == cursor


def test_reserved_open_shares_add_lease_cancel_and_terminal_releases_payload(
    native_http: NativeHttp,
) -> None:
    http = native_http
    recipe = saved(http)
    _ = http.server.reset_project(
        ProjectResetRequest(**http.cursor(), confirmedDiscard=True)
    )
    cursor = http.cursor()
    status, operation, _ = http.request(
        "/api/actions/open",
        {
            **cursor,
            "recipe": recipe,
            "confirmedDiscard": True,
            "source": {"filename": "x.ply", "size": len(triangle_bytes())},
        },
    )
    assert status == 202 and operation["status"] == "reserved"
    status, error, _ = http.request(
        "/api/sources/imports",
        {**cursor, "filename": "x.ply", "size": len(triangle_bytes())},
    )
    assert status == 409 and error["code"] == "source_busy"
    status, cancelled, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/cancel", cursor
    )
    assert status == 200 and cancelled["status"] == "cancelled"
    retained = http.server.source_imports._operations[operation["operationId"]]
    assert (
        retained.context is None and retained.recipe is None and retained.path is None
    )
    assert http.cursor() == cursor


def test_located_service_owns_original_raw_intent_before_inspection_and_upload(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    recipe = saved(http)
    source = recipe["nodes"][0]
    recipe["nodes"].append(
        {
            "id": "original-selection",
            "label": source["label"],
            "operation": "selection",
            "source": source["id"],
            "ids": [0, 1, 2],
        }
    )
    recipe["output"] = "original-selection"
    original = deepcopy(recipe)
    expected, warnings = import_recipe(original)
    assert len(warnings) == 1
    _ = http.server.reset_project(
        ProjectResetRequest(**http.cursor(), confirmedDiscard=True)
    )
    cursor = http.cursor()
    entered, release = Event(), Event()

    def gated(value: object, workspace: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return inspect_actions(value, workspace)

    monkeypatch.setattr(native_scan_import, "inspect_actions", gated)
    service = http.server.source_imports
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(
            service.open_actions,
            **cursor,
            recipe=recipe,
            confirmed_discard=True,
            filename="renamed.ply",
            size=len(triangle_bytes()),
        )
        assert entered.wait(10)
        try:
            # Change nested caller data after the owned snapshot but before inspect.
            recipe["nodes"][1]["label"] = "Changed during inspection"
            recipe["nodes"][1]["ids"][:] = [0]
        finally:
            release.set()
        operation = pending.result(timeout=10)
    assert operation["status"] == "reserved" and operation["uploadRequired"]
    recipe["nodes"].clear()
    recipe["output"] = None
    _ = service.receive(
        str(operation["operationId"]),
        io.BytesIO(triangle_bytes()),
        len(triangle_bytes()),
    )
    accepted = http.finish(operation)
    assert accepted["status"] == "accepted", accepted
    assert accepted["import_warning_count"] == 1
    metadata = http.metadata()
    assert metadata["graph"]["recipe"] == expected.model_dump(mode="json")
    assert metadata["import_warnings"] == warnings
    assert metadata["graph"]["recipe"]["nodes"][1]["ids"] == [0, 1, 2]


@pytest.mark.parametrize("race", ["cancel", "edit", "owner", "cleanup"])
def test_prepared_open_races_preserve_exact_old_owner(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    http = native_http
    recipe = saved(http)
    _ = http.server.reset_project(
        ProjectResetRequest(**http.cursor(), confirmedDiscard=True)
    )
    owner, old = http.server._project, http.server.accepted()
    entered, release = Event(), Event()
    original = ScanProject.prepare_open

    def gated(self: ScanProject, *args: Any, **kwargs: Any) -> Any:
        candidate = original(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return candidate

    monkeypatch.setattr(ScanProject, "prepare_open", gated)
    cursor = http.cursor()
    status, operation, _ = http.request(
        "/api/actions/open",
        {
            **cursor,
            "recipe": recipe,
            "confirmedDiscard": True,
            "source": {"filename": "x.ply", "size": len(triangle_bytes())},
        },
    )
    assert status == 202
    status, _, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", triangle_bytes()
    )
    assert status in {200, 202} and entered.wait(10)
    try:
        if race == "cancel":
            _, result, _ = http.request(
                f"/api/sources/imports/{operation['operationId']}/cancel", cursor
            )
            assert result["status"] == "cancelling"
        elif race == "edit":
            _ = http.server.graph.replace(
                Recipe.model_validate(
                    {**EMPTY, "groups": [{"id": "new-group", "label": "Changed"}]}
                ),
                cursor["token"],
            )
        elif race == "owner":
            # Identical empty content/cursor cannot authorize replacing a new owner.
            with http.server.lock:
                http.server._project = ScanProject()
                http.server._generation_offset = cursor["generation"]
            assert http.cursor() == cursor
        else:
            unlink = Path.unlink

            def fail(path: Path, *args: Any, **kwargs: Any) -> None:
                if path.suffix == ".upload":
                    raise OSError("owned cleanup injection")
                unlink(path, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", fail)
    finally:
        release.set()
    result = http.finish(operation)
    assert result["status"] == ("cancelled" if race == "cancel" else "failed"), result
    if race == "owner":
        assert http.server._project is not owner and http.cursor() == cursor
    else:
        assert http.server._project is owner and http.server.workspace is old.workspace
        assert http.server.graph is old.graph
    retained = http.server.source_imports._operations[operation["operationId"]]
    assert retained.context is None and retained.recipe is None
    monkeypatch.undo()


def test_accepted_open_wins_cancel_blocked_at_publication_lock(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    cursor = http.cursor()
    entered, attempted, release = Event(), Event(), Event()
    service = http.server.source_imports
    finish = service._finish
    records: list[dict[str, object]] = []
    operation_ids: list[str] = []

    def gated(operation: Any, status: str, error: Any = None) -> None:
        if status == "accepted":
            operation_ids.append(operation.id)
            entered.set()
            assert release.wait(10)
        finish(operation, status, error)

    monkeypatch.setattr(service, "_finish", gated)
    operation = service.open_actions(**cursor, recipe=EMPTY, confirmed_discard=True)
    assert entered.wait(10)

    def cancel() -> None:
        attempted.set()
        records.append(service.cancel(operation_ids[0], **cursor))

    thread = Thread(target=cancel)
    thread.start()
    assert attempted.wait(10)
    release.set()
    thread.join(10)
    assert not thread.is_alive()
    terminal = service.status(str(operation["operationId"]))
    assert terminal["status"] == "accepted" and records == [terminal]
    assert terminal["acceptedGeneration"] == cursor["generation"] + 1


def test_legacy_matching_then_generic_locate_never_manufactures_legacy_descriptor(
    native_http: NativeHttp,
) -> None:
    http = native_http
    generic = saved(http)
    http.server.select_example(
        ExampleRequest(example="nozzle", token=http.cursor()["token"]),
        http.cursor()["generation"],
    )
    legacy = http.server.accepted()
    recipe = cast(dict[str, Any], legacy.graph.snapshot()["recipe"])
    status, inspection, _ = http.request(
        "/api/actions/inspect", {**http.cursor(), "recipe": recipe}
    )
    assert (
        status == 200
        and inspection["classification"] == "matching"
        and inspection["descriptor"] is None
    )
    record = open_actions(http, recipe)
    assert record["status"] == "accepted" and http.server.workspace is legacy.workspace
    assert http.server.graph is not legacy.graph and not http.server.is_generic
    assert http.server.active_example == "nozzle"
    assert open_actions(http, generic, triangle_bytes())["status"] == "accepted"
    assert http.server.is_generic
    status, error, _ = http.request(
        "/api/actions/inspect", {**http.cursor(), "recipe": recipe}
    )
    assert status == 422 and error["code"] == "incompatible_legacy"
