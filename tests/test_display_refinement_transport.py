"""Private display work owns lifecycle, never accepted graph publications."""

# Inspect the pristine control flag and reuse the existing generated box fixture.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import pytest

import experiments.display_geometry as display
import experiments.nozzle_browser as native
from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe, StaleGraph
from tests.test_browser_workspace import EXAMPLE, call, snapshot, success
from tests.test_browser_workspace_cancellation import evaluate
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_surface_selection_http import prepared
from tests.test_surface_selection_http import snapshot as native_snapshot


def payload(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "token": state["token"],
        "revision": state["revision"],
        "feature": "plane",
        "quality": "fine",
    }


def result(request: display.DisplayRefinementRequest) -> dict[str, object]:
    return {
        **request.model_dump(),
        "display": {
            "preview": {"positions": [0, 0, 0, 1, 0, 0, 0, 1, 0], "indices": [0, 1, 2]},
            "edges": [],
            "stats": {"triangles": 1, "edge_points": 0, "bytes": 84},
        },
    }


def start(http: NativeHttp, state: dict[str, Any]) -> dict[str, Any]:
    status, started, _ = http.request(
        "/api/graph/display/refine",
        payload(state),
        generation=http.cursor()["generation"],
    )
    assert status == 202, started
    assert started["evaluation"]["operation"] == "refineDisplay"
    assert started["evaluation"]["kind"] == "preview"
    return cast(dict[str, Any], started["evaluation"])


def poll(http: NativeHttp, evaluation: dict[str, Any]) -> tuple[int, Any]:
    status, state, _ = http.request(
        f"/api/graph/display/refine/{evaluation['id']}",
        generation=evaluation["generation"],
    )
    return status, state


def terminal(http: NativeHttp, evaluation: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        status, state = poll(http, evaluation)
        assert status == 200, state
        if not state["evaluation_running"]:
            return cast(dict[str, Any], state)
        assert time.monotonic() < deadline
        _ = Event().wait(0.005)


@pytest.mark.parametrize("queued", [False, True])
def test_native_private_display_cancel_waits_without_discarding_ready_graph(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, queued: bool
) -> None:
    http = native_http
    before = prepared(http)
    graph, source = http.server.graph, http.server.accepted().workspace
    entered, release = Event(), Event()

    def prepare(graph: Any, request: display.DisplayRefinementRequest) -> Any:
        graph.assert_display_current(request.token, request.revision)

        def run(_cancelled: Callable[[], bool]) -> dict[str, object]:
            entered.set()
            assert release.wait(10)
            return result(request)  # Deliberately noncooperative until this boundary.

        return run

    monkeypatch.setattr(native, "prepare_display_refinement", prepare)
    occupying = None
    if queued:

        def occupy() -> None:
            entered.set()
            assert release.wait(10)

        occupying = http.server.worker.submit(occupy)
        assert entered.wait(10)
    evaluation = start(http, before)
    try:
        assert entered.wait(10)
        status, wrong, _ = http.request(
            f"/api/graph/surface-selection/preview/{evaluation['id']}",
            generation=evaluation["generation"],
        )
        assert status == 409 and wrong["code"] == "stale_evaluation"
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
        assert stopped["results"] == before["results"]
        assert stopped["revision"] == before["revision"]
        assert stopped["evaluation"]["state"] == (
            "cancelled" if queued else "cancel-requested"
        )
        assert http.server.busy() is not queued
    finally:
        release.set()
    if occupying is not None:
        _ = occupying.result(timeout=10)
    stopped = terminal(http, evaluation)
    assert stopped["evaluation"]["state"] == "cancelled" and stopped["display"] is None
    after = native_snapshot(http)
    for key in ("recipe", "token", "revision", "results", "states", "errors"):
        assert after[key] == before[key]
    assert not http.server.graph_job_errors and "evaluation_error" not in after
    assert http.server.graph is graph and http.server.accepted().workspace is source
    fresh = start(http, before)
    completed = terminal(http, fresh)
    assert fresh["sequence"] > evaluation["sequence"]
    assert completed["display"]["stats"]["triangles"] == 1
    assert poll(http, evaluation)[0] == 409
    assert native_snapshot(http)["results"] == before["results"]


def test_native_display_failure_and_exact_publication_fences(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    before = prepared(http)

    def prepare(graph: Any, request: display.DisplayRefinementRequest) -> Any:
        graph.assert_display_current(request.token, request.revision)

        def fail(_cancelled: Any) -> dict[str, object]:
            raise ValueError("Fixture display failure")

        return fail

    monkeypatch.setattr(native, "prepare_display_refinement", prepare)
    evaluation = start(http, before)
    failed = terminal(http, evaluation)
    assert failed["evaluation"]["state"] == "failed"
    assert failed["error"]["code"] == "display_refinement_failed"
    assert failed["error"]["message"] == "Fixture display failure"
    assert "evaluation_error" not in native_snapshot(http)
    assert not http.server.graph_job_errors
    status, wrong_generation, _ = http.request(
        f"/api/graph/display/refine/{evaluation['id']}",
        generation=evaluation["generation"] + 1,
    )
    assert status == 409 and wrong_generation["code"] == "stale_source"
    _ = http.server.graph.discard_evaluation(http.server.graph.failure_checkpoint())
    _ = http.server.graph.evaluate(before["token"], all_actions=True)
    current = native_snapshot(http)
    assert (
        current["token"] == before["token"] and current["revision"] > before["revision"]
    )
    assert poll(http, evaluation)[0] == 409
    status, stale, _ = http.request(
        "/api/graph/display/refine",
        payload(before),
        generation=evaluation["generation"],
    )
    assert status == 409, stale


@pytest.mark.parametrize(
    "field,value",
    [("revision", True), ("revision", -1), ("quality", "ultra"), ("feature", "")],
)
def test_native_display_rejects_malformed_control_before_start(
    native_http: NativeHttp, field: str, value: object
) -> None:
    before = native_snapshot(native_http)
    status, rejected, _ = native_http.request(
        "/api/graph/display/refine",
        {**payload(before), field: value},
        generation=native_http.cursor()["generation"],
    )
    assert status == 422, rejected
    assert native_snapshot(native_http)["revision"] == before["revision"]


@pytest.mark.parametrize("before_dispatch", [False, True])
def test_worker_private_display_cancel_preserves_accepted_results_and_pristine(
    monkeypatch: pytest.MonkeyPatch, before_dispatch: bool
) -> None:
    workspace = BrowserWorkspace(EXAMPLE)
    workspace.enable_cad()
    before = evaluate(workspace)
    pristine = workspace._pristine
    graph, source = workspace.graph, workspace.workspace
    workspace.checkpoint_operation(None if before_dispatch else "refineDisplay")

    def prepare(graph: Any, request: display.DisplayRefinementRequest) -> Any:
        graph.assert_display_current(request.token, request.revision)

        def interrupted(_cancelled: Any) -> dict[str, object]:
            raise KeyboardInterrupt

        return interrupted

    monkeypatch.setattr(display, "prepare_display_refinement", prepare)
    if not before_dispatch:
        response = call(workspace, "refineDisplay", **payload(before))
        assert response["error"]["code"] == "computation_cancelled"
    recovered = success(
        json.loads(
            workspace.restore_cancelled_json(
                json.dumps(before["recipe"]), operation="refineDisplay"
            )
        )
    )
    for key in ("token", "revision", "recipe", "results", "states", "errors"):
        assert recovered[key] == before[key]
    assert workspace.graph is graph and workspace.workspace is source
    assert workspace._pristine is pristine


def test_worker_display_capability_and_success_remain_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = BrowserWorkspace(EXAMPLE)
    _ = evaluate(workspace)
    before = snapshot(workspace)
    rejected = call(workspace, "refineDisplay", **payload(before))
    assert rejected["error"]["code"] == "unsupported_capability"
    workspace.enable_cad()
    pristine = workspace._pristine

    def prepare(graph: Any, request: display.DisplayRefinementRequest) -> Any:
        graph.assert_display_current(request.token, request.revision)

        def completed(_cancelled: Any) -> dict[str, object]:
            return result(request)

        return completed

    monkeypatch.setattr(display, "prepare_display_refinement", prepare)
    response = success(call(workspace, "refineDisplay", **payload(before)))
    assert response["display"]["stats"]["triangles"] == 1
    assert response["project_generation"] == before["project_generation"]
    assert snapshot(workspace) == before and workspace._pristine is pristine
    stale = call(
        workspace,
        "refineDisplay",
        **{**payload(before), "revision": before["revision"] - 1},
    )
    assert stale["error"]["code"] == "stale_graph"


def test_display_accessor_copies_only_ready_face_inputs_and_fences_revision() -> None:
    from experiments.face_builder import apply_faces
    from tests.test_arranged_face_workflow import evaluate as evaluate_graph
    from tests.test_arranged_face_workflow import graph_with_diagonal, reviewed_plate

    graph = graph_with_diagonal()
    _, request = reviewed_plate(graph)
    applied = apply_faces(graph, request)
    _ = evaluate_graph(graph)
    state = cast(dict[str, Any], graph.snapshot())
    face = next(
        node["id"]
        for node in state["recipe"]["nodes"]
        if node["operation"] == "arranged_face"
    )
    copied = graph.ready_display_inputs(state["token"], state["revision"], face)
    assert copied["record"] == state["results"][face] and copied["faces"] == {}
    assert copied["scale"] > 0
    copied["record"]["preview"]["positions"].clear()
    assert graph.snapshot() == state
    with pytest.raises(ValueError, match="constructed face"):
        _ = graph.ready_display_inputs(state["token"], state["revision"], "plate")
    with pytest.raises(StaleGraph):
        graph.assert_display_current(state["token"], state["revision"] - 1)
    assert applied


def test_body_display_accessor_includes_exact_ready_faces_without_other_results() -> (
    None
):
    from tests.test_body_graph import _box_graph

    graph = _box_graph()
    state = cast(dict[str, Any], graph.snapshot())
    with pytest.raises(ValueError, match="Evaluate display inputs"):
        _ = graph.ready_display_inputs(state["token"], state["revision"], "body")
    assert graph.snapshot() == state
    state = cast(dict[str, Any], graph.evaluate(state["token"]))
    copied = graph.ready_display_inputs(state["token"], state["revision"], "body")
    assert copied["record"] == state["results"]["body"]
    assert copied["faces"] == {
        key: state["results"][key] for key in state["results"]["body"]["face_ids"]
    }
    assert "axis" not in copied["faces"]
    next(iter(copied["faces"].values()))["preview"]["positions"].clear()
    assert graph.snapshot() == state


def test_real_body_refinement_crosses_http_without_graph_or_review_changes(
    tmp_path: Path,
) -> None:
    from tests.test_body_graph import _box_graph

    graph = _box_graph()
    _ = graph.evaluate(str(graph.snapshot()["token"]))
    before = cast(dict[str, Any], graph.snapshot())
    with native.NozzleServer(
        graph.workspace, recipe=Recipe.model_validate(before["recipe"])
    ) as server:
        server.graph = graph
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        http = NativeHttp(server, tmp_path)
        try:
            request = {**payload(before), "feature": "body", "quality": "draft"}
            status, started, _ = http.request("/api/graph/display/refine", request)
            assert status == 202, started
            completed = terminal(http, started["evaluation"])
            assert completed["evaluation"]["state"] == "complete", completed
            assert {key: completed[key] for key in request} == request
            assert completed["display"]["preview"] == {
                key: before["results"]["body"]["preview"][key]
                for key in ("positions", "indices")
            }
            assert completed["display"]["stats"]["triangles"] > 0
            assert completed["display"]["edges"]
            assert http.metadata()["display_refinement"] == {
                "supported": True,
                "version": 1,
                "qualities": ["draft", "standard", "fine"],
            }
            assert graph.snapshot() == before
        finally:
            server.shutdown()
            worker.join(timeout=10)
            assert not worker.is_alive()
