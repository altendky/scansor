"""Actual local transport, publication fences and source-operation leases."""

from __future__ import annotations

import json
import socket
import struct
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from experiments.nozzle_browser import NozzleServer
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits, ScanWorkspace


def triangle_bytes() -> bytes:
    header = (
        b"ply\nformat binary_little_endian 1.0\nelement vertex 3\n"
        b"property float x\nproperty float y\nproperty float z\n"
        b"property float nx\nproperty float ny\nproperty float nz\n"
        b"element face 1\nproperty list uchar int vertex_indices\nend_header\n"
    )
    return (
        header
        + b"".join(
            struct.pack("<6f", *row)
            for row in [(0, 0, 0, 0, 0, 1), (1, 0, 0, 0, 0, 1), (0, 1, 0, 0, 0, 1)]
        )
        + struct.pack("<B3i", 3, 0, 1, 2)
    )


@dataclass
class NativeHttp:
    server: NozzleServer
    staging: Path

    def request(
        self,
        path: str,
        payload: dict[str, Any] | bytes | None = None,
        *,
        generation: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]:
        values = dict(headers or {})
        if payload is not None:
            _ = values.setdefault("X-Scansor-Request", "1")
            _ = values.setdefault(
                "Content-Type",
                "application/octet-stream"
                if isinstance(payload, bytes)
                else "application/json",
            )
        if generation is not None:
            values["X-Scansor-Generation"] = str(generation)
        data = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode()
            if payload is not None
            else None
        )
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}{path}",
            data=data,
            headers=values,
        )
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            body = (
                json.loads(raw)
                if response.headers.get_content_type() == "application/json"
                else raw
            )
            assert isinstance(response.status, int)
            return response.status, body, dict(response.headers)

    def metadata(self) -> dict[str, Any]:
        status, body, _ = self.request("/api/meta")
        assert status == 200
        return body

    def cursor(self) -> dict[str, Any]:
        metadata = self.metadata()
        return {
            "generation": metadata["project_generation"],
            "token": metadata["graph"]["token"],
        }

    def reserve(
        self, raw: bytes | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        cursor = self.cursor()
        status, operation, _ = self.request(
            "/api/sources/imports",
            {
                **cursor,
                "filename": "generated.ply",
                "size": len(triangle_bytes() if raw is None else raw),
            },
        )
        assert status == 202, operation
        return cursor, operation

    def finish(self, operation: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, result, _ = self.request(
                f"/api/sources/imports/{operation['operationId']}"
            )
            assert status == 200, result
            if result["terminal"]:
                return result
            time.sleep(0.01)
        raise AssertionError("source import did not terminate")

    def import_triangle(self) -> dict[str, Any]:
        _, operation = self.reserve()
        status, body, _ = self.request(
            f"/api/sources/imports/{operation['operationId']}/bytes", triangle_bytes()
        )
        assert status in {200, 202}, body
        result = self.finish(operation)
        assert result["status"] == "accepted", result
        return result


@pytest.fixture
def native_http(tmp_path: Path) -> Iterator[NativeHttp]:
    staging = tmp_path / "staging"
    staging.mkdir()
    with NozzleServer(
        scan_limits=ScanLimits(2_000_000, 30_000, 60_000, 100_000_000),
        staging_directory=staging,
    ) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield NativeHttp(server, staging)
        finally:
            server.shutdown()
            thread.join(timeout=10)
    assert not list(staging.iterdir())


def test_empty_import_has_one_authoritative_generation_and_owned_original(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    assert before["source"] is None
    assert before["graph"]["recipe"]["nodes"] == []
    assert (
        before["capabilities"]["source_import"]["limits"]["max_source_bytes"]
        == 2_000_000
    )
    accepted = native_http.import_triangle()
    after = native_http.metadata()
    generation = after["project_generation"]
    assert generation > before["project_generation"]
    assert accepted["acceptedGeneration"] == generation
    assert after["graph"]["project_generation"] == generation
    assert len(after["graph"]["recipe"]["nodes"]) == 1
    assert after["graph"]["recipe"]["nodes"][0]["operation"] == "source"
    workspace = native_http.server.workspace
    assert isinstance(workspace, ScanWorkspace)
    assert workspace.original_bytes == triangle_bytes()
    assert native_http.server.graph is native_http.server.accepted().graph
    for name in ("positions", "indices"):
        status, body, headers = native_http.request(after[name]["url"])
        assert status == 200 and body
        assert headers["X-Scansor-Generation"] == str(generation)
        assert native_http.request(before[name]["url"])[0] == 409
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))
    assert native_http.request("/mesh/positions")[0] == 409


def test_empty_reset_aba_and_same_byte_import_need_generation_fences(
    native_http: NativeHttp,
) -> None:
    old = native_http.cursor()
    status, reset, _ = native_http.request(
        "/api/project/reset", {**old, "confirmedDiscard": True}
    )
    assert status == 200
    assert reset["project_generation"] > old["generation"]
    assert reset["graph"]["token"] == old["token"]  # The actual empty-token ABA.
    payload = {"token": old["token"], "recipe": reset["graph"]["recipe"]}
    for generation in (None, old["generation"]):
        assert (
            native_http.request("/api/graph", payload, generation=generation)[0] == 409
        )
    assert (
        native_http.request(
            "/api/sources/imports",
            {**old, "filename": "old.ply", "size": len(triangle_bytes())},
        )[0]
        == 409
    )
    first = native_http.import_triangle()
    cursor = native_http.cursor()
    assert (
        native_http.request(
            "/api/project/reset", {**cursor, "confirmedDiscard": False}
        )[0]
        == 422
    )
    assert (
        native_http.request("/api/project/reset", {**cursor, "confirmedDiscard": True})[
            0
        ]
        == 200
    )
    second = native_http.import_triangle()
    assert second["acceptedGeneration"] > first["acceptedGeneration"]


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Scansor-Request": "0"},
        {"Origin": "https://other.invalid"},
        {"Host": "other.invalid"},
    ],
)
def test_source_controls_retain_explicit_mutation_and_origin_protection(
    native_http: NativeHttp, headers: dict[str, str]
) -> None:
    before = native_http.metadata()
    status, _, _ = native_http.request(
        "/api/sources/imports",
        {**native_http.cursor(), "filename": "x.ply", "size": 1},
        headers=headers,
    )
    assert status == 403
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


def test_cancel_reserved_operation_and_reject_concurrent_or_second_source(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    cursor, operation = native_http.reserve()
    assert (
        native_http.request(
            "/api/sources/imports", {**cursor, "filename": "other.ply", "size": 1}
        )[0]
        == 409
    )
    assert (
        native_http.request("/api/project/reset", {**cursor, "confirmedDiscard": True})[
            0
        ]
        == 409
    )
    status, result, _ = native_http.request(
        f"/api/sources/imports/{operation['operationId']}/cancel", cursor
    )
    assert status == 200 and result["status"] == "cancelled"
    assert native_http.metadata() == before
    _ = native_http.import_triangle()
    accepted = native_http.metadata()
    assert (
        native_http.request(
            "/api/sources/imports",
            {**native_http.cursor(), "filename": "second.ply", "size": 1},
        )[0]
        == 409
    )
    assert native_http.metadata() == accepted


def test_upload_validation_failure_and_wrong_length_preserve_state(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    _, operation = native_http.reserve(b"not a PLY")
    assert native_http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", b"not a PLY"
    )[0] in {200, 202}
    result = native_http.finish(operation)
    assert result["status"] == "failed" and result["error"]["code"] == "invalid_source"
    assert native_http.metadata() == before
    _, operation = native_http.reserve()
    assert (
        native_http.request(
            f"/api/sources/imports/{operation['operationId']}/bytes", b"short"
        )[0]
        == 422
    )
    assert native_http.finish(operation)["status"] == "failed"
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


@pytest.mark.parametrize(
    "change,code",
    [
        ({"size": 2_000_001}, "source_limit"),
        ({"path": "/etc/passwd"}, "invalid_upload"),
        ({"settings": {"units": "millimeters"}}, "invalid_upload"),
    ],
)
def test_reservation_rejects_resource_or_unapproved_control_fields(
    native_http: NativeHttp, change: dict[str, Any], code: str
) -> None:
    before = native_http.metadata()
    status, result, _ = native_http.request(
        "/api/sources/imports",
        {**native_http.cursor(), "filename": "generated.ply", "size": 1, **change},
    )
    assert status == 422 and result["code"] == code
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Scansor-Request": "0"},
        {"Content-Type": "application/json"},
        {"Origin": "https://other.invalid"},
        {"Host": "other.invalid"},
    ],
)
def test_binary_upload_retains_transport_protection(
    native_http: NativeHttp, headers: dict[str, str]
) -> None:
    before = native_http.metadata()
    cursor, operation = native_http.reserve()
    assert (
        native_http.request(
            f"/api/sources/imports/{operation['operationId']}/bytes",
            triangle_bytes(),
            headers=headers,
        )[0]
        == 403
    )
    assert native_http.metadata() == before
    assert (
        native_http.request(
            f"/api/sources/imports/{operation['operationId']}/cancel", cursor
        )[1]["status"]
        == "cancelled"
    )
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


def test_cancel_during_socket_upload_cannot_accept_late_bytes(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    cursor, operation = native_http.reserve()
    raw = triangle_bytes()
    with socket.create_connection(
        ("127.0.0.1", native_http.server.server_port), timeout=5
    ) as connection:
        request = (
            f"POST /api/sources/imports/{operation['operationId']}/bytes HTTP/1.0\r\n"
            f"Host: 127.0.0.1:{native_http.server.server_port}\r\n"
            "Content-Type: application/octet-stream\r\nX-Scansor-Request: 1\r\n"
            f"Content-Length: {len(raw)}\r\n\r\n"
        ).encode()
        connection.sendall(request + raw[:8])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, result, _ = native_http.request(
                f"/api/sources/imports/{operation['operationId']}"
            )
            assert status == 200
            if result["status"] == "receiving":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("socket upload did not enter its bounded read")
        assert native_http.metadata() == before
        status, result, _ = native_http.request(
            f"/api/sources/imports/{operation['operationId']}/cancel", cursor
        )
        assert status == 200 and result["status"] == "cancelling"
        connection.sendall(raw[8:])
        connection.shutdown(socket.SHUT_WR)
        _ = connection.recv(4096)
    assert native_http.finish(operation)["status"] == "cancelled"
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


def test_cancelled_queued_future_releases_its_source_lease(
    native_http: NativeHttp,
) -> None:
    entered, release = Event(), Event()

    def occupy() -> None:
        entered.set()
        assert release.wait(10)

    blocked = native_http.server.worker.submit(occupy)
    assert entered.wait(5)
    try:
        queued = native_http.server.submit(lambda: None)
        assert native_http.server.busy()
        assert queued.cancel()
        assert not native_http.server.busy()
        assert (
            native_http.request(
                "/api/project/reset", {**native_http.cursor(), "confirmedDiscard": True}
            )[0]
            == 200
        )
    finally:
        release.set()
    _ = blocked.result(timeout=5)


@pytest.mark.parametrize("action", ["cancel", "edit"])
def test_preparation_has_short_locks_and_late_cancel_or_edits_cannot_publish(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    entered, release = Event(), Event()
    original = ScanProject.prepare_add

    def held(self: ScanProject, *args: Any, **kwargs: Any) -> Any:
        candidate = original(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return candidate

    monkeypatch.setattr(ScanProject, "prepare_add", held)
    cursor, operation = native_http.reserve()
    try:
        assert native_http.request(
            f"/api/sources/imports/{operation['operationId']}/bytes", triangle_bytes()
        )[0] in {200, 202}
        assert entered.wait(5)
        before = native_http.metadata()  # Returns while preparation remains gated.
        if action == "cancel":
            status, body, _ = native_http.request(
                f"/api/sources/imports/{operation['operationId']}/cancel", cursor
            )
            assert status == 200 and body["status"] == "cancelling"
        else:
            recipe = before["graph"]["recipe"]
            recipe["groups"].append({"id": "user_group", "label": "User group"})
            status, _, _ = native_http.request(
                "/api/graph",
                {"token": cursor["token"], "recipe": recipe},
                generation=cursor["generation"],
            )
            assert status == 200
        preserved = native_http.metadata()
    finally:
        release.set()
    result = native_http.finish(operation)
    assert result["status"] == ("cancelled" if action == "cancel" else "failed")
    assert native_http.metadata() == preserved
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


def test_queued_graph_work_holds_source_lease_before_executor_start(
    native_http: NativeHttp,
) -> None:
    _ = native_http.import_triangle()
    cursor = native_http.cursor()
    entered, release = Event(), Event()

    def occupy() -> None:
        entered.set()
        assert release.wait(10)

    blocked = native_http.server.worker.submit(occupy)
    assert entered.wait(5)
    try:
        status, _, _ = native_http.request(
            "/api/graph/evaluate",
            {"token": cursor["token"]},
            generation=cursor["generation"],
        )
        assert status == 202
        assert (
            native_http.request(
                "/api/project/reset", {**cursor, "confirmedDiscard": True}
            )[0]
            == 409
        )
        assert (
            native_http.request(
                "/api/examples/select",
                {"example": "nozzle", "token": cursor["token"]},
                generation=cursor["generation"],
            )[0]
            == 409
        )
    finally:
        release.set()
    _ = blocked.result(timeout=5)
    assert native_http.server.graph_job is not None
    _ = native_http.server.graph_job.result(timeout=5)
    assert (
        native_http.request(
            "/api/project/reset", {**native_http.cursor(), "confirmedDiscard": True}
        )[0]
        == 200
    )


def test_cad_export_holds_source_lease_until_kernel_work_finishes(
    native_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.nozzle_cad as cad

    _ = native_http.import_triangle()
    cursor = native_http.cursor()
    entered, release = Event(), Event()

    def export(*_args: Any, **_kwargs: Any) -> bytes:
        entered.set()
        assert release.wait(10)
        return b"PK-native-lease"

    monkeypatch.setattr(cad, "export_cad", export)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(
            native_http.request,
            "/api/export/cad",
            {"token": cursor["token"], "target": "scan", "units": "Millimeters"},
            generation=cursor["generation"],
        )
        try:
            assert entered.wait(5)
            assert (
                native_http.request(
                    "/api/project/reset", {**cursor, "confirmedDiscard": True}
                )[0]
                == 409
            )
            assert native_http.metadata()["project_generation"] == cursor["generation"]
        finally:
            release.set()
        status, body, headers = job.result(timeout=5)
    assert status == 200 and body == b"PK-native-lease"
    assert headers["X-Scansor-Generation"] == str(cursor["generation"])
    assert (
        native_http.request("/api/project/reset", {**cursor, "confirmedDiscard": True})[
            0
        ]
        == 200
    )


def test_short_disconnected_upload_cleans_owned_staging(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    _, operation = native_http.reserve()
    with socket.create_connection(
        ("127.0.0.1", native_http.server.server_port), timeout=5
    ) as connection:
        request = (
            f"POST /api/sources/imports/{operation['operationId']}/bytes HTTP/1.0\r\n"
            f"Host: 127.0.0.1:{native_http.server.server_port}\r\n"
            "Content-Type: application/octet-stream\r\nX-Scansor-Request: 1\r\n"
            f"Content-Length: {len(triangle_bytes())}\r\n\r\n"
        ).encode()
        connection.sendall(request + b"short")
        connection.shutdown(socket.SHUT_WR)
        _ = connection.recv(4096)
    assert native_http.finish(operation)["status"] == "failed"
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))


def test_eof_after_explicit_cancel_remains_cancelled_and_preserves_project(
    native_http: NativeHttp,
) -> None:
    before = native_http.metadata()
    cursor, operation = native_http.reserve()
    with socket.create_connection(
        ("127.0.0.1", native_http.server.server_port), timeout=5
    ) as connection:
        # Send headers only: the receiver is waiting for its first body bytes
        # when Cancel arrives, followed by EOF rather than a completed payload.
        request = (
            f"POST /api/sources/imports/{operation['operationId']}/bytes HTTP/1.0\r\n"
            f"Host: 127.0.0.1:{native_http.server.server_port}\r\n"
            "Content-Type: application/octet-stream\r\nX-Scansor-Request: 1\r\n"
            f"Content-Length: {len(triangle_bytes())}\r\n\r\n"
        ).encode()
        connection.sendall(request)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, result, _ = native_http.request(
                f"/api/sources/imports/{operation['operationId']}"
            )
            assert status == 200
            if result["status"] == "receiving":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("socket upload did not enter its first read")
        status, result, _ = native_http.request(
            f"/api/sources/imports/{operation['operationId']}/cancel", cursor
        )
        assert status == 200 and result["status"] == "cancelling"
        connection.shutdown(socket.SHUT_WR)
        _ = connection.recv(4096)
    result = native_http.finish(operation)
    assert result["status"] == "cancelled"
    assert result["error"]["code"] == "cancelled"
    assert native_http.metadata() == before
    assert all(path.is_dir() for path in native_http.staging.rglob("*"))
