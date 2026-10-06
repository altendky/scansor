"""Ensure requests reuse current graph work through the real HTTP adapter."""

import json
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest

import experiments.feature_graph as feature_graph
from experiments.feature_graph import Recipe, StaleGraph
from experiments.nozzle_browser import NozzleServer
from experiments.nozzle_session import NozzleWorkspace
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresFailure,
    solve_constrained_least_squares,
)


@dataclass
class HttpGraph:
    server: NozzleServer
    releases: list[Event] = field(default_factory=list)

    @property
    def token(self) -> str:
        return str(self.server.graph.snapshot()["token"])

    def snapshot(self) -> dict[str, Any]:
        return cast(dict[str, Any], self.server.graph.snapshot())

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def ensure(self, targets: list[str]) -> tuple[int, dict[str, Any]]:
        return self.post("/api/graph/ensure", {"token": self.token, "targets": targets})

    def gate(self, monkeypatch: pytest.MonkeyPatch) -> tuple[Event, Event, list[Any]]:
        entered, release = Event(), Event()
        self.releases.append(release)
        original = self.server.graph.ensure_current
        calls: list[Any] = []

        def ensure(*args: Any, **kwargs: Any) -> dict[str, object]:
            calls.append((args, kwargs))
            entered.set()
            assert release.wait(10), "test did not release evaluation worker"
            return original(*args, **kwargs)

        monkeypatch.setattr(self.server.graph, "ensure_current", ensure)
        return entered, release, calls


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def http_graph(workspace: NozzleWorkspace) -> Iterator[HttpGraph]:
    recipe = Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": key,
                    "label": key,
                    "operation": "point",
                    "initial_coordinates": coordinates,
                }
                for key, coordinates in (("a", [0, 0, 0]), ("b", [1, 0, 0]))
            ]
            + [
                {
                    "id": key,
                    "label": key,
                    "operation": "axis",
                    "initial_parameters": parameters,
                }
                for key, parameters in (("z", [0, 0, 0, 0]), ("x", [0, 0, 1, 0]))
            ]
            + [
                {
                    "id": "frame",
                    "label": "frame",
                    "operation": "frame",
                    "origin_point": "a",
                    "primary_reference": "z",
                    "primary_output_axis": "+Z",
                    "secondary_reference": "x",
                    "secondary_output_axis": "+X",
                },
                {
                    "id": "scale",
                    "label": "scale",
                    "operation": "scale",
                    "distances": [
                        {"first_point": "a", "second_point": "b", "known_distance": 1}
                    ],
                },
                {
                    "id": "transform",
                    "label": "transform",
                    "operation": "transform",
                    "frame": "frame",
                    "scale": "scale",
                },
            ],
            "output": "transform",
        }
    )
    with NozzleServer(workspace, recipe=recipe) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        harness = HttpGraph(server)
        try:
            yield harness
        finally:
            for release in harness.releases:
                release.set()
            server.shutdown()
            worker.join(timeout=10)


def test_ensure_joins_work_and_reuses_ready_multi_root_outputs(
    http_graph: HttpGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, calls = http_graph.gate(monkeypatch)
    status, response = http_graph.ensure(["a", "b"])
    assert status == 202 and response["evaluation_running"]
    assert entered.wait(10)
    job = http_graph.server.graph_job
    assert job is not None
    assert http_graph.ensure(["a", "b"])[0] == 202
    assert http_graph.server.graph_job is job and len(calls) == 1
    release.set()
    _ = job.result(timeout=10)

    status, response = http_graph.ensure(["a", "b"])
    assert status == 200 and not response["evaluation_running"]
    assert response["states"]["a"] == response["states"]["b"] == "ready"
    assert response["states"]["frame"] == "unevaluated"
    assert http_graph.server.graph_job is job and len(calls) == 1


def test_ensure_rechecks_own_targets_after_joining_incompatible_work(
    http_graph: HttpGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, calls = http_graph.gate(monkeypatch)
    assert http_graph.ensure(["a"])[0] == 202
    assert entered.wait(10)
    first = http_graph.server.graph_job
    assert first is not None
    assert http_graph.ensure(["b"])[0] == 202
    assert http_graph.server.graph_job is first and len(calls) == 1
    release.set()
    _ = first.result(timeout=10)
    assert http_graph.snapshot()["states"]["b"] == "unevaluated"

    assert http_graph.ensure(["b"])[0] == 202
    second = http_graph.server.graph_job
    assert second is not None and second is not first
    _ = second.result(timeout=10)
    assert len(calls) == 2
    assert http_graph.ensure(["a", "b"])[0] == 200


@pytest.mark.parametrize("change_away", [False, True])
def test_ensure_restarts_cancelled_work_when_recipe_token_is_restored(
    http_graph: HttpGraph, monkeypatch: pytest.MonkeyPatch, change_away: bool
) -> None:
    entered, release = Event(), Event()
    http_graph.releases.append(release)
    original = feature_graph.frame_result
    attempts: list[Any] = []

    def frame(*args: Any, **kwargs: Any) -> dict[str, Any]:
        attempts.append(args)
        entered.set()
        assert release.wait(10), "test did not release frame evaluation"
        return original(*args, **kwargs)

    monkeypatch.setattr(feature_graph, "frame_result", frame)
    token = http_graph.token
    recipe = http_graph.snapshot()["recipe"]
    assert http_graph.ensure(["frame"])[0] == 202
    assert entered.wait(10)
    cancelled_job = http_graph.server.graph_job
    assert cancelled_job is not None
    assert http_graph.snapshot()["states"]["frame"] == "running"

    if change_away:
        changed = deepcopy(recipe)
        next(node for node in changed["nodes"] if node["id"] == "a")[
            "initial_coordinates"
        ] = [2, 0, 0]
        status, _ = http_graph.post(
            "/api/graph", {"token": http_graph.token, "recipe": changed}
        )
        assert status == 200 and http_graph.token != token
    status, _ = http_graph.post(
        "/api/graph", {"token": http_graph.token, "recipe": recipe}
    )
    assert status == 200 and http_graph.token == token

    # An identical content token must not let the old epoch publish its result.
    release.set()
    with pytest.raises(StaleGraph, match="graph changed during evaluation"):
        _ = cancelled_job.result(timeout=10)
    cancelled = http_graph.snapshot()
    assert cancelled["states"]["frame"] == "stale"
    assert "frame" not in cancelled["derived"]
    assert not cancelled["errors"]
    with urlopen(
        f"http://127.0.0.1:{http_graph.server.server_port}/api/graph", timeout=10
    ) as response:
        reported = json.load(response)
    assert not reported["evaluation_running"]
    assert "evaluation_error" not in reported

    status, response = http_graph.ensure(["frame"])
    assert status == 202 and response["evaluation_running"]
    retry_job = http_graph.server.graph_job
    assert retry_job is not None and retry_job is not cancelled_job
    _ = retry_job.result(timeout=10)
    status, response = http_graph.ensure(["frame"])
    assert status == 200 and not response["evaluation_running"]
    assert response["states"]["frame"] == "ready" and len(attempts) == 2
    assert "evaluation_error" not in response
    assert not http_graph.server.graph_job_errors


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"targets": []}, 422),
        ({"targets": "a"}, 422),
        ({"targets": ["missing"]}, 422),
        ({"targets": ["a"], "target": "b"}, 422),
        ({"targets": ["a"], "all_actions": True}, 422),
        ({"targets": ["a"], "token": "old"}, 409),
    ],
)
def test_invalid_ensure_request_starts_no_work(
    http_graph: HttpGraph, payload: dict[str, Any], status: int
) -> None:
    actual, response = http_graph.post(
        "/api/graph/ensure", {"token": http_graph.token, **payload}
    )
    assert actual == status and response["error"]
    assert http_graph.server.graph_job is None
    assert set(http_graph.snapshot()["states"].values()) == {"unevaluated"}


@pytest.mark.parametrize(
    "failure_code", [None, "invalid-input", "infeasible-constraints", "iteration-limit"]
)
def test_ensure_preserves_failure_and_explicit_evaluation_retries(
    http_graph: HttpGraph, monkeypatch: pytest.MonkeyPatch, failure_code: str | None
) -> None:
    original = feature_graph.frame_result
    attempts: list[Any] = []
    failure: ValueError = ValueError("temporary frame failure")
    if failure_code is not None:
        with pytest.raises(ConstrainedLeastSquaresFailure) as raised:
            _ = solve_constrained_least_squares(
                [0.0],
                lambda values: (values - 1.0, np.eye(1)),
                lambda values: (
                    (np.asarray([values[0], values[0] - 1.0]), np.ones((2, 1)))
                    if failure_code == "infeasible-constraints"
                    else (np.empty(0), np.empty((0, 1)))
                ),
                parameter_scales=[0.0 if failure_code == "invalid-input" else 1.0],
                max_iterations=0,
            )
        failure = raised.value
        assert raised.value.code == failure_code
        if failure_code == "iteration-limit":
            assert raised.value.diagnostics is not None
            # Exercise nonfinite transport in both scalar and sequence fields.
            raised.value.diagnostics = replace(
                raised.value.diagnostics,
                parameters=(float("nan"),),
                objective_history=(float("inf"), float("-inf"), float("nan")),
                condition=float("inf"),
                projected_gradient_norm=float("-inf"),
                constraint_violation=float("nan"),
            )
    recipe = http_graph.snapshot()["recipe"]
    recipe["nodes"].append(
        {
            "id": "independent",
            "label": "Independent point",
            "operation": "point",
            "initial_coordinates": [1, 2, 3],
        }
    )
    assert (
        http_graph.post("/api/graph", {"token": http_graph.token, "recipe": recipe})[0]
        == 200
    )

    def frame(*args: Any, **kwargs: Any) -> dict[str, Any]:
        attempts.append(args)
        if len(attempts) == 1:
            raise failure
        return original(*args, **kwargs)

    monkeypatch.setattr(feature_graph, "frame_result", frame)
    assert http_graph.ensure(["transform"])[0] == 202
    failed_job = http_graph.server.graph_job
    assert failed_job is not None
    with pytest.raises(ValueError, match=str(failure)):
        _ = failed_job.result(timeout=10)
    failed = http_graph.snapshot()
    assert failed["states"]["frame"] == "failed"
    assert failed["states"]["transform"] == "blocked"
    assert failed["diagnostics"]["transform"]["blocked_by"] == ["frame"]
    assert "frame" not in failed["results"] and "frame" not in failed["derived"]
    if failure_code is not None:
        diagnostic = failed["diagnostics"]["frame"]
        assert diagnostic["kind"] == "constrained_solver_failure"
        assert diagnostic["code"] == failure_code
        if failure_code == "invalid-input":
            assert diagnostic["solver"] is None
        else:
            solver = diagnostic["solver"]
            assert solver["termination"] == failure_code
            assert solver["constraint_evaluations"] > 0
            if failure_code == "iteration-limit":
                assert solver["parameters"] == ["NaN"]
                assert solver["objective_history"] == ["Infinity", "-Infinity", "NaN"]
                assert solver["condition"] == "Infinity"
                assert solver["projected_gradient_norm"] == "-Infinity"
                assert solver["constraint_violation"] == "NaN"
                altered = http_graph.snapshot()
                altered["diagnostics"]["frame"]["solver"]["parameters"].clear()
                assert http_graph.snapshot()["diagnostics"] == failed["diagnostics"]

    status, response = http_graph.ensure(["transform"])
    assert status == 200 and response["errors"] == failed["errors"]
    assert response["diagnostics"] == failed["diagnostics"]
    assert response["required_failures"] == ["frame", "transform"]
    assert len(attempts) == 1 and http_graph.server.graph_job is failed_job

    # New work must preserve diagnostics when reusing an existing failed node.
    assert http_graph.ensure(["transform", "independent"])[0] == 202
    independent_job = http_graph.server.graph_job
    assert independent_job is not None and independent_job is not failed_job
    with pytest.raises(ValueError, match=str(failure)):
        _ = independent_job.result(timeout=10)
    preserved = http_graph.snapshot()
    assert preserved["states"]["independent"] == "ready"
    assert preserved["diagnostics"] == failed["diagnostics"] and len(attempts) == 1

    status, independent = http_graph.ensure(["a"])
    assert status == 200 and independent["required_failures"] == []

    status, _ = http_graph.post(
        "/api/graph/evaluate", {"token": http_graph.token, "target": "transform"}
    )
    assert status == 202
    retry_job = http_graph.server.graph_job
    assert retry_job is not None and retry_job is not failed_job
    _ = retry_job.result(timeout=10)
    status, response = http_graph.ensure(["transform"])
    assert status == 200 and len(attempts) == 2
    assert response["states"]["transform"] == "ready"
    assert not response["errors"] and not response["diagnostics"]


def test_unclassified_worker_failure_is_not_implicitly_resubmitted(
    http_graph: HttpGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = http_graph.server.graph.ensure_current
    attempts: list[list[str] | None] = []

    def unexpected_failure(
        token: str, targets: list[str] | None = None, all_actions: bool = False
    ) -> dict[str, object]:
        attempts.append(targets)
        if len(attempts) == 1:
            raise RuntimeError("unexpected worker failure")
        return original(token, targets, all_actions)

    monkeypatch.setattr(http_graph.server.graph, "ensure_current", unexpected_failure)
    assert http_graph.ensure(["a", "b"])[0] == 202
    failed_job = http_graph.server.graph_job
    assert failed_job is not None
    with pytest.raises(RuntimeError, match="unexpected worker failure"):
        _ = failed_job.result(timeout=10)

    # Root order and duplicates do not turn the same request into a retry.
    status, response = http_graph.ensure(["b", "a", "b"])
    assert status == 200 and not response["evaluation_running"]
    assert response["evaluation_error"] == "unexpected worker failure"
    assert len(attempts) == 1
    assert http_graph.server.graph_job is failed_job
    assert response["states"]["a"] == response["states"]["b"] == "unevaluated"

    # An unrelated requested closure can still make progress.
    assert http_graph.ensure(["x"])[0] == 202
    independent_job = http_graph.server.graph_job
    assert independent_job is not None and independent_job is not failed_job
    _ = independent_job.result(timeout=10)
    status, response = http_graph.ensure(["x"])
    assert status == 200 and response["states"]["x"] == "ready"
    assert len(attempts) == 2

    status, response = http_graph.ensure(["a", "b"])
    assert status == 200 and response["evaluation_error"] == "unexpected worker failure"
    assert len(attempts) == 2

    status, _ = http_graph.post(
        "/api/graph/evaluate", {"token": http_graph.token, "all_actions": True}
    )
    assert status == 202
    retry = http_graph.server.graph_job
    assert retry is not None
    _ = retry.result(timeout=10)
    status, response = http_graph.ensure(["a", "b"])
    assert status == 200 and not response.get("evaluation_error")
