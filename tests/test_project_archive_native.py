"""Real native project-file transactions and portable source ownership."""

from __future__ import annotations

import gc
import hashlib
import io
import json
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from experiments.feature_graph import Recipe, Source
from experiments.nozzle_browser import NATIVE_SCAN_LIMITS, NozzleServer
from experiments.nozzle_session import NozzleWorkspace
from experiments.project_archive import (
    ArchiveMemberInput,
    read_project_archive,
    write_project_archive,
)
from experiments.scan_sources import BytesInput, PathInput
from experiments.scan_workspace import ScanWorkspace, prepare_scan
from tests.native_scan_fixture import native_grid_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def server() -> Iterator[NozzleServer]:
    with NozzleServer(None, 0) as instance:
        thread = Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            thread.join(5)
            assert not thread.is_alive()


def request(
    server: NozzleServer,
    path: str,
    value: object = None,
    *,
    raw: bool = False,
    generation: int | None = None,
) -> tuple[int, bytes]:
    if raw:
        assert isinstance(value, bytes)
        body = value
    else:
        body = json.dumps(value).encode() if value is not None else None
    headers = {"X-Scansor-Request": "1"}
    if body is not None:
        headers["Content-Type"] = (
            "application/octet-stream" if raw else "application/json"
        )
    if generation is not None:
        headers["X-Scansor-Generation"] = str(generation)
    elif isinstance(value, dict) and "generation" in value:
        headers["X-Scansor-Generation"] = str(value["generation"])
    message = Request(
        f"http://127.0.0.1:{server.server_port}{path}", data=body, headers=headers
    )
    try:
        with urlopen(message, timeout=15) as response:
            return response.status, response.read(48 * 1024**2 + 1)
    except HTTPError as error:
        return error.code, error.read(1024**2)


def metadata(server: NozzleServer) -> dict[str, Any]:
    status, body = request(server, "/api/meta")
    assert status == 200, body
    return json.loads(body)


def cursor(server: NozzleServer) -> dict[str, Any]:
    meta = metadata(server)
    return {"generation": meta["project_generation"], "token": meta["graph"]["token"]}


def terminal(server: NozzleServer, operation: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while not operation["terminal"]:
        assert time.monotonic() < deadline, operation
        time.sleep(0.002)
        status, raw = request(
            server, f"/api/sources/imports/{operation['operationId']}"
        )
        assert status == 200, raw
        operation = json.loads(raw)
    return operation


def open_project(server: NozzleServer, raw: bytes) -> dict[str, Any]:
    status, body = request(
        server,
        "/api/project/open",
        {
            **cursor(server),
            "filename": "renamed.scansor",
            "size": len(raw),
            "confirmedDiscard": True,
        },
    )
    assert status == 202, body
    operation = json.loads(body)
    status, body = request(
        server, f"/api/sources/imports/{operation['operationId']}/bytes", raw, raw=True
    )
    assert status in {200, 202}, body
    return terminal(server, json.loads(body))


def generic_project() -> tuple[bytes, bytes]:
    raw = native_grid_fixture(16, 16).raw
    original = BytesInput(raw, "grid.ply")
    workspace = prepare_scan(original, original.filename, NATIVE_SCAN_LIMITS)
    assert workspace.descriptor is not None
    recipe = Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Original grid",
                operation="source",
                source_sha256=workspace.descriptor.source_sha256,
                reference_sha256=workspace.reference_sha256,
                descriptor=workspace.descriptor,
            )
        ],
        output="scan",
    )
    output = io.BytesIO()
    _ = write_project_archive(recipe.model_dump(mode="json"), original, output)
    return output.getvalue(), raw


def test_project_edit_virtual_save_and_one_file_reopen(server: NozzleServer) -> None:
    archive, original = generic_project()
    assert open_project(server, archive)["status"] == "accepted"
    meta = metadata(server)
    assert meta["source"]["source_sha256"] == hashlib.sha256(original).hexdigest()
    recipe = meta["graph"]["recipe"]
    recipe["nodes"][0]["label"] = "Edited project scan"
    status, body = request(
        server,
        "/api/graph",
        {"token": meta["graph"]["token"], "recipe": recipe},
        generation=meta["project_generation"],
    )
    assert status == 200, body
    status, body = request(
        server,
        "/api/sources/virtual",
        {
            **cursor(server),
            "settings": {"version": "edge-cell-reduction-v1", "divisions": 4},
        },
    )
    assert status == 202, body
    assert terminal(server, json.loads(body))["status"] == "accepted"
    reduced = metadata(server)
    assert reduced["vertices"] < meta["vertices"]
    status, saved = request(server, "/api/project/export", cursor(server))
    assert status == 200, saved
    parsed = read_project_archive(BytesInput(saved, "saved.scansor"))
    assert parsed.original is not None
    with parsed.original.open() as member:
        assert member.read(len(original) + 1) == original
        assert member.read(1) == b""
    assert parsed.actions["nodes"][0]["label"] == "Edited project scan"
    status, body = request(
        server, "/api/project/reset", {**cursor(server), "confirmedDiscard": True}
    )
    assert status == 200, body
    assert open_project(server, saved)["status"] == "accepted"
    reopened = metadata(server)
    assert reopened["source"] == reduced["source"]
    assert reopened["graph"]["recipe"] == reduced["graph"]["recipe"]
    assert (
        reopened["virtual_scans"]["active_binding"]
        == reduced["virtual_scans"]["active_binding"]
    )


def test_failed_archive_open_retains_accepted_project_and_stale_export_rejects(
    server: NozzleServer,
) -> None:
    archive, _ = generic_project()
    assert open_project(server, archive)["status"] == "accepted"
    before = metadata(server)
    result = open_project(server, b"not a zip archive")
    assert result["status"] == "failed"
    after = metadata(server)
    assert after["project_generation"] == before["project_generation"]
    assert after["graph"]["recipe"] == before["graph"]["recipe"]
    status, _ = request(
        server, "/api/project/export", {**cursor(server), "token": "stale"}
    )
    assert status == 409


def test_closed_server_releases_its_owned_archive_source_lease() -> None:
    instance = NozzleServer(None, 0)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    path: Path | None = None
    try:
        archive, _ = generic_project()
        assert open_project(instance, archive)["status"] == "accepted"
        workspace = instance.accepted().workspace
        assert isinstance(workspace, ScanWorkspace)
        source = workspace.source
        assert source is not None
        assert isinstance(source.input, ArchiveMemberInput)
        assert isinstance(source.input.authority, PathInput)
        path = source.input.authority.path
        del source, workspace
        assert path.is_file()
    finally:
        instance.shutdown()
        thread.join(5)
        instance.server_close()
    _ = gc.collect()
    assert path is not None and not path.exists()


def test_known_legacy_fixture_migrates_and_resaves_portable_reference(
    server: NozzleServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = ROOT / "examples/nozzle-bayonette-simplified"
    expected = NozzleWorkspace(example)
    raw = (example / "nozzle-bayonette-simplified.ply").read_bytes()
    actions = Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Nozzle scan",
                operation="source",
                source_sha256=expected.source_sha256,
                reference_sha256=expected.reference_sha256,
            )
        ],
        output="scan",
    ).model_dump(mode="json")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("actions.json", json.dumps(actions))
        archive.writestr("nozzle-bayonette-simplified.ply", raw)
    assert open_project(server, output.getvalue())["status"] == "accepted"
    loaded = metadata(server)
    status, positions = request(server, loaded["positions"]["url"])
    assert status == 200 and positions == expected.local.astype("<f4").tobytes()
    status, saved = request(server, "/api/project/export", cursor(server))
    assert status == 200, saved
    with zipfile.ZipFile(io.BytesIO(saved)) as archive:
        assert "references/legacy.json" in archive.namelist()
    original_open = Path.open

    def no_example_reads(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.is_relative_to(example):
            raise AssertionError("Portable project attempted repository example lookup")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", no_example_reads)
    assert open_project(server, saved)["status"] == "accepted"
    reopened = metadata(server)
    assert reopened["graph"]["recipe"] == loaded["graph"]["recipe"]
    status, positions = request(server, reopened["positions"]["url"])
    assert status == 200 and positions == expected.local.astype("<f4").tobytes()
