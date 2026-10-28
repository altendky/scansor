"""No-upload native virtual scans retain source pairs behind normal source fences."""

# Deliberately inspect coordinator ownership and inject preparation/cleanup races.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from copy import deepcopy
from threading import Event
from typing import Any

import pytest

from experiments.feature_graph import Recipe
from experiments.native_scan_import import NativeScanImports
from experiments.scan_project import ScanProject
from tests.native_scan_fixture import native_grid_fixture
from tests.scan_source_fixture import read_source_bytes
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http


def imported(http: NativeHttp) -> dict[str, Any]:
    raw = native_grid_fixture(17, 17).raw
    _, operation = http.reserve(raw)
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, result
    result = http.finish(operation)
    assert result["status"] == "accepted", result
    return http.metadata()


def change(http: NativeHttp, path: str, **controls: Any) -> dict[str, Any]:
    status, operation, _ = http.request(path, {**http.cursor(), **controls})
    assert status == 202, operation
    assert operation["uploadRequired"] is False
    result = http.finish(operation)
    assert result["status"] == "accepted", result
    return http.metadata()


def test_create_and_switch_pair_preserves_later_edits_and_source_entries(
    native_http: NativeHttp,
) -> None:
    http = native_http
    original = imported(http)
    original_bytes = read_source_bytes(http.server.workspace)
    recipe = deepcopy(original["graph"]["recipe"])
    recipe["nodes"].extend(
        [
            {
                "id": "patch",
                "label": "Patch",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(289)),
            },
            {
                "id": "datum",
                "label": "Before",
                "operation": "point",
                "initial_coordinates": [1, 2, 3],
            },
        ]
    )
    _ = http.server.graph.replace(
        Recipe.model_validate(recipe), original["graph"]["token"]
    )
    original = http.metadata()
    assert original["virtual_scans"]["can_create"] is True
    derived = change(http, "/api/sources/virtual", settings={"divisions": 4})
    profile = derived["virtual_scans"]
    assert profile["active_binding"] == profile["virtual"]["binding"]
    assert profile["virtual"]["vertices"] < profile["original"]["vertices"]
    assert derived["project_generation"] == original["project_generation"] + 1
    assert http.request(original["positions"]["url"])[0] == 409
    selection = derived["graph"]["recipe"]["nodes"][1]
    assert selection["entries"][0]["mode"] == "painted"
    assert selection["entries"][1]["mode"] == "generated"
    assert selection["entries"][1]["source_volume"] is None
    edited = deepcopy(derived["graph"]["recipe"])
    edited["nodes"][-1]["label"] = "Edited on reduced scan"
    _ = http.server.graph.replace(
        Recipe.model_validate(edited), derived["graph"]["token"]
    )
    restored = change(
        http, "/api/sources/activate", binding=profile["original"]["binding"]
    )
    assert read_source_bytes(http.server.workspace) == original_bytes
    assert restored["graph"]["recipe"]["nodes"][-1]["label"] == "Edited on reduced scan"
    assert restored["graph"]["memberships"]["patch"] == list(range(289))
    again = change(http, "/api/sources/activate", binding=profile["virtual"]["binding"])
    assert again["virtual_scans"]["active_binding"] == profile["virtual"]["binding"]
    assert again["graph"]["recipe"]["nodes"][1]["entries"] == selection["entries"]
    assert again["graph"]["recipe"]["nodes"][-1]["label"] == "Edited on reduced scan"
    assert not list(http.staging.rglob("*.upload"))


@pytest.mark.parametrize(
    ("path", "controls"),
    [
        ("/api/sources/virtual", {"settings": {"divisions": True}}),
        ("/api/sources/virtual", {"settings": {"divisions": 4}, "file": "unexpected"}),
        ("/api/sources/virtual", {"settings": {"divisions": 4, "extra": 1}}),
        ("/api/sources/activate", {"binding": "bad"}),
        ("/api/sources/activate", {"binding": "0" * 64, "settings": {"divisions": 4}}),
    ],
)
def test_no_upload_requests_are_strict(
    native_http: NativeHttp, path: str, controls: dict[str, Any]
) -> None:
    http = native_http
    before = imported(http)
    status, _, _ = http.request(path, {**http.cursor(), **controls})
    assert status == 422
    assert http.metadata()["graph"] == before["graph"]
    assert not http.server.source_imports.busy


def test_source_changes_require_import_and_current_cursor(
    native_http: NativeHttp,
) -> None:
    http = native_http
    empty_cursor = http.cursor()
    status, _, _ = http.request(
        "/api/sources/virtual", {**empty_cursor, "settings": {"divisions": 4}}
    )
    assert status == 422
    _ = imported(http)
    status, result, _ = http.request(
        "/api/sources/virtual", {**empty_cursor, "settings": {"divisions": 4}}
    )
    assert status == 409 and result["code"] == "stale_source"


@pytest.mark.parametrize("race", ["cancel", "edit", "cleanup"])
def test_no_upload_candidate_is_private_until_final_fence(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    http = native_http
    before = imported(http)
    owner, accepted = http.server._project, http.server.accepted()
    entered, release = Event(), Event()
    prepare = ScanProject.prepare_virtual_scan

    def gated(self: ScanProject, *args: Any, **kwargs: Any) -> Any:
        candidate = prepare(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return candidate

    monkeypatch.setattr(ScanProject, "prepare_virtual_scan", gated)
    cursor = http.cursor()
    status, operation, _ = http.request(
        "/api/sources/virtual", {**cursor, "settings": {"divisions": 4}}
    )
    assert status == 202 and operation["uploadRequired"] is False
    assert entered.wait(10)
    try:
        assert (
            http.server._project is owner
            and http.server.workspace is accepted.workspace
        )
        assert http.metadata()["graph"] == before["graph"]
        status, invalid, _ = http.request(
            f"/api/sources/imports/{operation['operationId']}/bytes", b"unexpected"
        )
        assert status == 422 and invalid["code"] == "invalid_upload"
        assert not list(http.staging.rglob("*.upload"))
        status, busy, _ = http.request(
            "/api/sources/activate", {**cursor, "binding": "0" * 64}
        )
        assert status == 409 and busy["code"] == "source_busy"
        if race == "cancel":
            status, result, _ = http.request(
                f"/api/sources/imports/{operation['operationId']}/cancel", cursor
            )
            assert status == 200 and result["status"] == "cancelling"
        elif race == "edit":
            changed = deepcopy(before["graph"]["recipe"])
            changed["nodes"][0]["label"] = "Intervening edit"
            _ = http.server.graph.replace(
                Recipe.model_validate(changed), cursor["token"]
            )
        else:

            def fail_cleanup(_self: NativeScanImports, _operation: Any) -> None:
                raise OSError("injected no-upload cleanup failure")

            monkeypatch.setattr(NativeScanImports, "_cleanup", fail_cleanup)
    finally:
        release.set()
    result = http.finish(operation)
    assert result["status"] == ("cancelled" if race == "cancel" else "failed"), result
    assert http.server._project is owner and http.server.workspace is accepted.workspace
    assert not http.server.source_imports.busy
    retained = http.server.source_imports._operations[operation["operationId"]]
    assert (
        retained.context is None
        and retained.source_change is None
        and retained.path is None
    )
    monkeypatch.undo()


def test_saved_derived_actions_reconstruct_from_original_upload(
    native_http: NativeHttp,
) -> None:
    http = native_http
    _ = imported(http)
    original_bytes = read_source_bytes(http.server.workspace)
    derived = change(http, "/api/sources/virtual", settings={"divisions": 4})
    recipe = derived["graph"]["recipe"]
    status, _, _ = http.request(
        "/api/project/reset", {**http.cursor(), "confirmedDiscard": True}
    )
    assert status == 200
    cursor = http.cursor()
    status, inspection, _ = http.request(
        "/api/actions/inspect", {**cursor, "recipe": recipe}
    )
    assert status == 200 and inspection["uploadRequired"] is True
    assert inspection["descriptor"]["source_bytes"] == len(original_bytes)
    status, operation, _ = http.request(
        "/api/actions/open",
        {
            **cursor,
            "recipe": recipe,
            "confirmedDiscard": True,
            "source": {"filename": "renamed-original.ply", "size": len(original_bytes)},
        },
    )
    assert status == 202, operation
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", original_bytes
    )
    assert status in {200, 202}, result
    result = http.finish(operation)
    assert result["status"] == "accepted", result
    reopened = http.metadata()
    assert (
        reopened["virtual_scans"]["active_binding"]
        == derived["virtual_scans"]["active_binding"]
    )
    _ = change(
        http,
        "/api/sources/activate",
        binding=reopened["virtual_scans"]["original"]["binding"],
    )
    assert read_source_bytes(http.server.workspace) == original_bytes


def test_saved_original_actions_reuse_retained_original_without_upload(
    native_http: NativeHttp,
) -> None:
    http = native_http
    original = imported(http)
    original_bytes = read_source_bytes(http.server.workspace)
    _ = change(http, "/api/sources/virtual", settings={"divisions": 4})
    recipe = original["graph"]["recipe"]
    cursor = http.cursor()
    status, inspection, _ = http.request(
        "/api/actions/inspect", {**cursor, "recipe": recipe}
    )
    assert status == 200 and inspection["uploadRequired"] is False
    assert inspection["classification"] == "matching"
    status, operation, _ = http.request(
        "/api/actions/open", {**cursor, "recipe": recipe, "confirmedDiscard": True}
    )
    assert status == 202 and operation["uploadRequired"] is False
    result = http.finish(operation)
    assert result["status"] == "accepted", result
    assert read_source_bytes(http.server.workspace) == original_bytes
    assert http.metadata()["virtual_scans"]["virtual"] is None
