"""Portable selections, solver replay and the local HTTP boundary."""

import json
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.nozzle_browser import NozzleServer
from experiments.nozzle_session import NozzleSession, NozzleWorkspace, SessionFit


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


def test_saved_session_replays_fit_without_browser(workspace: NozzleWorkspace) -> None:
    session = NozzleSession.model_validate_json(workspace.default.model_dump_json())
    result = workspace.fit(session)
    assert result["session"] == workspace.default.model_dump()
    assert result["reference_diameter"] == pytest.approx(18.79278, abs=1e-5)
    assert result["signed_half_angle_degrees"] == pytest.approx(0.892, abs=0.001)
    axis = np.array(result["axis_display"])
    assert np.linalg.norm(axis) == pytest.approx(1)
    assert len(result["lateral_residuals"]) == 1261
    assert len(result["plane_residuals"]) == 642
    # Independent world-space plane distance agrees with the returned residuals.
    point = np.array(result["plane_point_display"])
    expected = (workspace.local[session.plane_ids] - point) @ axis
    np.testing.assert_allclose(expected, result["plane_residuals"], atol=1e-12)


@pytest.mark.parametrize(
    "change",
    [
        {"source_sha256": "wrong"},
        {"model_sha256": "wrong"},
        {"lateral_ids": [2, 1]},
        {"lateral_ids": [1, 1]},
        {"lateral_ids": [25000]},
        {"lateral_ids": [-1]},
        {"lateral_ids": [True]},
        {"lateral_ids": [1.0]},
        {"lateral_ids": [1], "plane_ids": [1]},
        {"surprise": 1},
    ],
)
def test_rejects_invalid_session(
    workspace: NozzleWorkspace, change: dict[str, object]
) -> None:
    payload = workspace.default.model_dump() | change
    with pytest.raises((ValueError, ValidationError)):
        _ = workspace.validate(NozzleSession.model_validate(payload))


def test_empty_selection_can_be_saved_but_not_fitted(
    workspace: NozzleWorkspace,
) -> None:
    session = NozzleSession.model_validate(
        workspace.default.model_dump() | {"plane_ids": []}
    )
    assert workspace.validate(session) == session
    with pytest.raises(ValueError, match="select at least"):
        _ = workspace.fit(session)


def test_edited_selection_is_the_one_fitted(workspace: NozzleWorkspace) -> None:
    ids = workspace.default.lateral_ids[::2]
    session = NozzleSession.model_validate(
        workspace.default.model_dump() | {"lateral_ids": ids}
    )
    result = workspace.fit(session)
    assert result["session"] == session.model_dump()
    assert len(result["lateral_residuals"]) == len(ids)
    assert workspace.default.lateral_ids != ids


@pytest.fixture
def server(workspace: NozzleWorkspace) -> Iterator[NozzleServer]:
    with NozzleServer(workspace) as instance:
        thread = Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            thread.join(timeout=5)


def test_http_binary_geometry_and_origin_boundary(server: NozzleServer) -> None:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/mesh/positions")
        response = connection.getresponse()
        assert response.status == 200
        actual = np.frombuffer(response.read(), dtype="<f4").reshape(-1, 3)
        np.testing.assert_allclose(actual, server.workspace.local, atol=1e-6)
        connection.request("GET", "/../../pyproject.toml")
        response = connection.getresponse()
        assert response.status == 404
        _ = response.read()
        connection.request("GET", "/api/meta", headers={"Host": "attacker.invalid"})
        response = connection.getresponse()
        assert response.status == 403
        _ = response.read()
        connection.request(
            "POST",
            "/api/session",
            body=server.workspace.default.model_dump_json(),
            headers={
                "Content-Type": "application/json",
                "X-Scansor-Request": "1",
                "Origin": "https://attacker.invalid",
            },
        )
        response = connection.getresponse()
        assert response.status == 403
        _ = response.read()
    finally:
        connection.close()


def test_fit_worker_does_not_block_reads_or_queue_more_fits(
    server: NozzleServer,
) -> None:
    release = Event()
    started = Event()

    def wait() -> SessionFit:
        started.set()
        assert release.wait(5)
        return server.workspace.fit(server.workspace.default)

    server.job = server.worker.submit(wait)
    server.job_id = "test"
    assert started.wait(5)
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/fit/test")
        response = connection.getresponse()
        assert json.loads(response.read()) == {"status": "running"}
        connection.request("GET", "/api/meta")
        response = connection.getresponse()
        assert response.status == 200
        _ = response.read()
        connection.request(
            "POST",
            "/api/fit",
            body=server.workspace.default.model_dump_json(),
            headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
        )
        response = connection.getresponse()
        assert response.status == 409
        _ = response.read()
    finally:
        release.set()
        connection.close()


def test_graph_http_edits_and_evaluation(server: NozzleServer) -> None:
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = {"Content-Type": "application/json", "X-Scansor-Request": "1"}
    try:
        connection.request("GET", "/api/graph")
        state = json.loads(connection.getresponse().read())
        for node in state["recipe"]["nodes"]:
            if node["id"] == "side":
                node["kind"] = "cylinder"
        body = json.dumps({"token": state["token"], "recipe": state["recipe"]})
        connection.request("POST", "/api/graph", body=body, headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        updated = json.loads(response.read())
        assert updated["result"] is None
        connection.request("POST", "/api/graph", body=body, headers=headers)
        response = connection.getresponse()
        assert response.status == 409
        _ = response.read()
        connection.request(
            "POST",
            "/api/graph/evaluate",
            body=json.dumps({"token": updated["token"]}),
            headers=headers,
        )
        response = connection.getresponse()
        assert response.status == 202
        _ = response.read()
        assert server.graph_job is not None
        _ = server.graph_job.result(timeout=5)
        connection.request("GET", "/api/graph")
        result = json.loads(connection.getresponse().read())
        assert result["states"]["fit"] == "ready"
        assert result["result"]["signed_half_angle_degrees"] == 0
    finally:
        connection.close()
