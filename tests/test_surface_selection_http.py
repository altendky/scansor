"""Actual asynchronous native preview, cancellation and publication fences."""

from __future__ import annotations

import time
from concurrent.futures import Future
from threading import Event
from typing import Any, cast

import pytest

import experiments.seed_surface_authoring as authoring
from experiments.scan_workspace import ScanWorkspace
from experiments.seed_surface_selection import (
    SurfaceSelectionResult,
    grow_surface_selection,
)
from tests.native_scan_fixture import native_grid_fixture
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_surface_selection_integration import recipe


def snapshot(http: NativeHttp) -> dict[str, Any]:
    status, state, _ = http.request("/api/graph")
    assert status == 200, state
    return cast(dict[str, Any], state)


def prepared(http: NativeHttp) -> dict[str, Any]:
    raw = native_grid_fixture(5, 5).raw
    _, operation = http.reserve(raw)
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, result
    assert http.finish(operation)["status"] == "accepted"
    cursor = http.cursor()
    status, state, _ = http.request(
        "/api/graph",
        {
            "token": cursor["token"],
            "recipe": recipe(
                cast(ScanWorkspace, http.server.accepted().workspace)
            ).model_dump(mode="json"),
        },
        generation=cursor["generation"],
    )
    assert status == 200, state
    status, state, _ = http.request(
        "/api/graph/ensure",
        {"token": state["token"], "all_actions": True},
        generation=cursor["generation"],
    )
    assert status == 202, state
    deadline = time.monotonic() + 10
    while (state := snapshot(http))["evaluation_running"]:
        assert time.monotonic() < deadline
        _ = Event().wait(0.005)
    assert state["states"]["plane"] == "ready"
    return state


def start(http: NativeHttp, token: str) -> dict[str, Any]:
    cursor = http.cursor()
    status, state, _ = http.request(
        "/api/graph/surface-selection/preview",
        {"token": token, "seed_selection": "seeds"},
        generation=cursor["generation"],
    )
    assert status == 202, state
    assert state["evaluation"]["kind"] == "preview" and state["evaluation_running"]
    return cast(dict[str, Any], state["evaluation"])


def poll(http: NativeHttp, evaluation: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, state, _ = http.request(
        f"/api/graph/surface-selection/preview/{evaluation['id']}",
        generation=evaluation["generation"],
    )
    return status, cast(dict[str, Any], state)


def terminal(http: NativeHttp, evaluation: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        status, state = poll(http, evaluation)
        assert status == 200, state
        if not state["evaluation_running"]:
            return state
        assert time.monotonic() < deadline
        _ = Event().wait(0.005)


@pytest.mark.parametrize("queued", [False, True])
def test_native_preview_cancel_waits_and_preserves_accepted_geometry(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, queued: bool
) -> None:
    http = native_http
    before = prepared(http)
    source, graph = http.server.accepted().workspace, http.server.graph
    entered, release = Event(), Event()
    original = grow_surface_selection

    def gate(*args: Any, **kwargs: Any) -> SurfaceSelectionResult:
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(authoring, "grow_surface_selection", gate)
    occupying = None
    if queued:

        def occupy() -> None:
            entered.set()
            assert release.wait(10)

        occupying = http.server.worker.submit(occupy)
        assert entered.wait(10)
    evaluation = start(http, before["token"])
    try:
        assert entered.wait(10)
        assert snapshot(http)["evaluation"]["kind"] == "preview"
        assert http.metadata()["surface_selection"]["max_preview_ids"] == 2048
        status, busy, _ = http.request(
            "/api/graph",
            {"token": before["token"], "recipe": before["recipe"]},
            generation=evaluation["generation"],
        )
        assert status == 409 and busy["code"] == "source_busy"
        status, stopped, _ = http.request(
            "/api/graph/cancel",
            {"generation": evaluation["generation"], "evaluationId": evaluation["id"]},
            generation=evaluation["generation"],
        )
        assert status in {200, 202}, stopped
        assert (
            stopped["results"] == before["results"]
            and stopped["revision"] == before["revision"]
        )
        if not queued:
            assert (
                stopped["evaluation"]["state"] == "cancel-requested"
                and http.server.busy()
            )
        else:
            assert (
                stopped["evaluation"]["state"] == "cancelled" and not http.server.busy()
            )
    finally:
        release.set()
    if occupying is not None:
        _ = occupying.result(timeout=10)
    stopped = terminal(http, evaluation)
    assert stopped["evaluation"]["state"] == "cancelled" and stopped["preview"] is None
    after = snapshot(http)
    assert after["recipe"] == before["recipe"] and after["results"] == before["results"]
    assert "evaluation_error" not in after and not http.server.graph_job_errors
    assert http.server.graph is graph and http.server.accepted().workspace is source
    fresh = start(http, before["token"])
    assert fresh["sequence"] > evaluation["sequence"]
    status, stale = poll(http, evaluation)
    assert status == 409 and stale["code"] == "stale_evaluation"
    preview = terminal(http, fresh)
    assert (
        preview["evaluation"]["state"] == "complete"
        and preview["preview"]["counts"]["region"] == 25
    )
    assert snapshot(http)["results"] == before["results"]


def test_preview_failure_is_private_and_polling_fences_recipe_and_generation(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    before = prepared(http)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("Fixture surface preview failed")

    monkeypatch.setattr(authoring, "grow_surface_selection", fail)
    evaluation = start(http, before["token"])
    failed = terminal(http, evaluation)
    assert failed["evaluation"]["state"] == "failed"
    assert failed["error"] == {
        "code": "surface_selection_failed",
        "message": "Fixture surface preview failed",
    }
    after = snapshot(http)
    assert "evaluation_error" not in after and after["results"] == before["results"]
    status, ensured, _ = http.request(
        "/api/graph/ensure",
        {"token": before["token"], "targets": ["plane"]},
        generation=evaluation["generation"],
    )
    assert (
        status == 200
        and "evaluation_error" not in ensured
        and not http.server.graph_job_errors
    )
    status, stale, _ = http.request(
        f"/api/graph/surface-selection/preview/{evaluation['id']}",
        generation=evaluation["generation"] + 1,
    )
    assert status == 409 and stale["code"] == "stale_source"
    changed = before["recipe"]
    changed["nodes"][1]["label"] = "Edited seeds"
    status, result, _ = http.request(
        "/api/graph",
        {"token": before["token"], "recipe": changed},
        generation=evaluation["generation"],
    )
    assert status == 200, result
    status, stale = poll(http, evaluation)
    assert status == 409 and stale["code"] == "stale_evaluation"


def test_unready_preview_rejects_without_hidden_ensure(native_http: NativeHttp) -> None:
    http = native_http
    before = prepared(http)
    changed = before["recipe"]
    changed["nodes"][1]["ids"] = [7]
    status, replaced, _ = http.request(
        "/api/graph",
        {"token": before["token"], "recipe": changed},
        generation=http.cursor()["generation"],
    )
    assert status == 200, replaced
    status, result, _ = http.request(
        "/api/graph/surface-selection/preview",
        {"token": replaced["token"], "seed_selection": "seeds"},
        generation=http.cursor()["generation"],
    )
    assert status == 422 and "Evaluate the seed" in result["error"]
    assert snapshot(http)["results"] == replaced["results"]


def test_preview_poll_observes_completion_once(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    before = prepared(http)
    evaluation = start(http, before["token"])
    complete = terminal(http, evaluation)
    future: Future[dict[str, object]] = Future()
    future.set_result({"token": before["token"], "preview": complete["preview"]})
    observations = iter([False, True])
    monkeypatch.setattr(future, "done", lambda: next(observations))
    with http.server.lock:
        record = http.server.evaluation
        assert record is not None
        record.state = "running"
        record.future = future
        http.server.graph_job = future
    status, running = poll(http, evaluation)
    assert status == 200 and running["evaluation_running"]
    assert running["evaluation"]["state"] == "running" and running["preview"] is None
    status, stopped = poll(http, evaluation)
    assert status == 200 and not stopped["evaluation_running"]
    assert stopped["evaluation"]["state"] == "complete"
    assert stopped["preview"] == complete["preview"]
