"""Actual native HTTP Cancel keeps source ownership until all work retires."""

# Gated evaluator internals expose the physical retirement boundary under test.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from threading import Event
from typing import Any, cast

import numpy as np
import pytest

import experiments.feature_graph as graph_module
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.scan_workspace import ScanWorkspace
from experiments.selection_growth import PlaneFitFailure, fit_seed
from tests.scan_source_fixture import read_source_bytes
from tests.test_native_scan_http import NativeHttp
from tests.test_native_scan_http import native_http as native_http
from tests.test_parallel_numerical_fits import setup_graph


def current(http: NativeHttp, suffix: str = "") -> dict[str, Any]:
    status, state, _ = http.request("/api/graph" + suffix)
    assert status == 200, state
    return cast(dict[str, Any], state)


def prepared(http: NativeHttp) -> FeatureGraph[ScanWorkspace]:
    template = setup_graph(None)
    raw = read_source_bytes(template.workspace)
    _, operation = http.reserve(raw)
    status, result, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, result
    assert http.finish(operation)["status"] == "accepted"
    cursor = http.cursor()
    status, state, _ = http.request(
        "/api/graph",
        {"token": cursor["token"], "recipe": template.snapshot()["recipe"]},
        generation=cursor["generation"],
    )
    assert status == 200, state
    return cast(FeatureGraph[ScanWorkspace], http.server.graph)


def start(http: NativeHttp, kind: str = "evaluate") -> dict[str, Any]:
    cursor = http.cursor()
    status, state, _ = http.request(
        "/api/graph/" + kind,
        {
            "token": cursor["token"],
            **(
                {"targets": ["f0", "f1"]} if kind == "ensure" else {"all_actions": True}
            ),
        },
        generation=cursor["generation"],
    )
    assert status == 202 and state["evaluation_running"], state
    assert state["evaluation"]["kind"] == kind
    return cast(dict[str, Any], state["evaluation"])


def cancel(http: NativeHttp, evaluation: dict[str, Any]) -> dict[str, Any]:
    status, state, _ = http.request(
        "/api/graph/cancel",
        {"generation": evaluation["generation"], "evaluationId": evaluation["id"]},
        generation=evaluation["generation"],
    )
    assert status in {200, 202}, state
    assert state["evaluation"]["id"] == evaluation["id"]
    return cast(dict[str, Any], state)


def until(
    http: NativeHttp, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        state = current(http)
        if predicate(state):
            return state
        assert time.monotonic() < deadline, state
        _ = Event().wait(0.005)


def terminal(http: NativeHttp) -> dict[str, Any]:
    return until(http, lambda state: not state["evaluation_running"])


def test_cancel_queued_evaluation_is_idempotent_and_preserves_source(
    native_http: NativeHttp,
) -> None:
    http = native_http
    _ = prepared(http)
    source = http.server.accepted().workspace
    entered, release = Event(), Event()

    def occupy() -> None:
        entered.set()
        assert release.wait(10)

    occupying = http.server.worker.submit(occupy)
    assert entered.wait(10)
    try:
        evaluation = start(http)
        assert evaluation["state"] == "queued"
        state = cancel(http, evaluation)
        assert state["evaluation"]["state"] == "cancelled"
        assert not state["evaluation_running"] and not http.server.busy()
        revision = state["revision"]
        assert cancel(http, evaluation)["revision"] == revision
        assert http.server.accepted().workspace is source
        assert not state["errors"] and not state["results"]
    finally:
        release.set()
    _ = occupying.result(timeout=10)
    fresh = start(http)
    assert fresh["id"] != evaluation["id"]
    assert fresh["sequence"] > evaluation["sequence"]
    ready = terminal(http)
    assert ready["evaluation"]["state"] == "complete"
    assert ready["states"]["f0"] == ready["states"]["f1"] == "ready"
    # A terminal idle Cancel does not discard successfully completed geometry.
    assert cancel(http, fresh)["results"] == ready["results"]
    cursor = http.cursor()
    status, reset, _ = http.request(
        "/api/project/reset", {**cursor, "confirmedDiscard": True}
    )
    assert status == 200, reset
    _ = prepared(http)
    next_source = start(http)
    assert next_source["sequence"] > fresh["sequence"]
    assert terminal(http)["evaluation"]["state"] == "complete"


@pytest.mark.parametrize("kind", ["evaluate", "ensure"])
def test_active_numerical_stop_is_observable_and_old_cancel_cannot_stop_new_work(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    http = native_http
    _ = prepared(http)
    entered = [Event(), Event()]
    release = Event()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        entered[int(points[:, 0].mean() > 5)].set()
        assert release.wait(10)
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    evaluation = start(http, kind)
    try:
        assert all(event.wait(10) for event in entered)
        before = current(http)
        assert before["evaluation"]["state"] == "running"
        assert http.metadata()["graph"]["evaluation"] == before["evaluation"]
        unchanged = current(
            http, f"?revision={before['revision']}&recipe_token={before['token']}"
        )
        assert (
            unchanged["unchanged"] and unchanged["evaluation"] == before["evaluation"]
        )
        # A targeted ensure joins the exact current operation rather than replacing it.
        cursor = http.cursor()
        status, joined, _ = http.request(
            "/api/graph/ensure",
            {"token": cursor["token"], "targets": ["f0"]},
            generation=cursor["generation"],
        )
        assert status == 202 and joined["evaluation"]["id"] == evaluation["id"]
        stopping = cancel(http, evaluation)
        assert stopping["evaluation_running"]
        assert stopping["evaluation"]["state"] == "cancel-requested"
        assert not stopping["results"] and not stopping["errors"]
        repeat = cancel(http, evaluation)
        assert repeat["revision"] == stopping["revision"]
        assert http.server.busy()
    finally:
        release.set()
    stopped = terminal(http)
    assert stopped["evaluation"]["state"] == "cancelled"
    assert "evaluation_error" not in stopped
    assert not http.server.graph_job_errors
    assert not http.server.busy()
    # A new evaluation can have the same recipe token but must have another ID.
    release.clear()
    for event in entered:
        event.clear()
    fresh = start(http, kind)
    assert fresh["sequence"] > evaluation["sequence"]
    try:
        assert all(event.wait(10) for event in entered)
        status, error, _ = http.request(
            "/api/graph/cancel",
            {"generation": evaluation["generation"], "evaluationId": evaluation["id"]},
            generation=evaluation["generation"],
        )
        assert status == 409 and error["code"] == "stale_evaluation"
        assert current(http)["evaluation"]["id"] == fresh["id"]
        assert current(http)["evaluation"]["state"] == "running"
    finally:
        release.set()
    assert terminal(http)["evaluation"]["state"] == "complete"


def test_partial_results_discarded_and_current_authored_edits_survive(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    graph = prepared(http)
    source = http.server.accepted().workspace
    entered, release = Event(), Event()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if points[:, 0].mean() > 5:
            entered.set()
            assert release.wait(10)
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    evaluation = start(http)
    try:
        assert entered.wait(10)
        partial = until(http, lambda state: state["states"]["f0"] == "ready")
        assert "f0" in partial["results"]
        authored = Recipe.model_validate(partial["recipe"])
        authored.nodes.append(
            graph_module.PointDefinition(
                id="datum",
                label="Keep new datum",
                operation="point",
                initial_coordinates=(1, 2, 3),
            )
        )
        status, edited, _ = http.request(
            "/api/graph",
            {"token": partial["token"], "recipe": authored.model_dump(mode="json")},
            generation=evaluation["generation"],
        )
        assert status == 200, edited
        stopping = cancel(http, evaluation)
        assert stopping["recipe"] == edited["recipe"]
        assert "f0" not in stopping["results"]
        assert http.server.accepted().workspace is source and http.server.graph is graph
    finally:
        release.set()
    stopped = terminal(http)
    assert stopped["recipe"] == authored.model_dump(mode="json")
    _ = start(http)
    ready = terminal(http)
    assert (
        ready["states"]["datum"]
        == ready["states"]["f0"]
        == ready["states"]["f1"]
        == "ready"
    )


def test_cancel_waits_until_noncooperative_numerical_child_retires(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    http = native_http
    graph = prepared(http)
    entered = [Event(), Event()]
    release = [Event(), Event()]
    coordinator_unwinding = Event()
    original = graph._evaluate_impl

    def observe_exit(*args: Any, **kwargs: Any) -> dict[str, object]:
        try:
            return original(*args, **kwargs)
        finally:
            coordinator_unwinding.set()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        index = int(points[:, 0].mean() > 5)
        entered[index].set()
        assert release[index].wait(10)
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph, "_evaluate_impl", observe_exit)
    monkeypatch.setattr(graph_module, "fit_seed", gated)
    evaluation = start(http)
    try:
        assert all(event.wait(10) for event in entered)
        _ = cancel(http, evaluation)
        release[0].set()
        assert coordinator_unwinding.wait(10)
        job = http.server.graph_job
        assert job is not None
        with pytest.raises(FutureTimeout):
            _ = job.result(timeout=0.05)
        waiting = current(http)
        assert waiting["evaluation_running"] and http.server.busy()
        assert waiting["evaluation"]["state"] == "cancel-requested"
        assert not waiting["results"]
    finally:
        for event in release:
            event.set()
    assert terminal(http)["evaluation"]["state"] == "cancelled"
    assert not http.server.busy()


def test_discard_restores_unchanged_failures_without_reverting_authored_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = setup_graph(None)

    def fail(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if points[:, 0].mean() < 5:
            raise PlaneFitFailure("test-failure", "retained failed fit")
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", fail)
    token = str(graph.snapshot()["token"])
    with pytest.raises(PlaneFitFailure):
        _ = graph.evaluate(token, all_actions=True)
    checkpoint = graph.failure_checkpoint()
    monkeypatch.setattr(graph_module, "fit_seed", fit_seed)
    _ = graph.evaluate(token, "f0")
    recipe = Recipe.model_validate(graph.snapshot()["recipe"])
    recipe.nodes[-1] = recipe.nodes[-1].model_copy(
        update={"label": "Renamed current fit"}
    )
    edited = graph.replace(recipe, token)
    restored = cast(dict[str, Any], graph.discard_evaluation(checkpoint))
    assert restored["recipe"] == edited["recipe"]
    assert restored["states"]["f0"] == "failed"
    assert restored["errors"]["f0"] == "retained failed fit"
    assert not restored["results"]


@pytest.mark.parametrize(
    "changes",
    [{"generation": True}, {"evaluationId": ""}, {"extra": 1}, {"generation": -1}],
)
def test_cancel_request_schema_is_strict(
    native_http: NativeHttp, changes: dict[str, Any]
) -> None:
    http = native_http
    cursor = http.cursor()
    status, _, _ = http.request(
        "/api/graph/cancel",
        {"generation": cursor["generation"], "evaluationId": "unknown", **changes},
        generation=cursor["generation"],
    )
    assert status == 422
