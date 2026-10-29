"""Native admission and opt-in actual 104 MiB import/reduction/archive evidence."""

from __future__ import annotations

import json
import os
import shutil
import struct
import time
from dataclasses import asdict, replace
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pytest

from experiments.nozzle_browser import (
    NATIVE_PROJECT_LIMITS,
    NozzleServer,
)
from experiments.scan_workspace import ScanImportError
from tests.test_native_scan_http import NativeHttp, triangle_bytes


def test_native_project_limits_are_advertised_and_enforced(tmp_path: Path) -> None:
    limits = replace(NATIVE_PROJECT_LIMITS, max_archive_bytes=4096, max_member_bytes=1)
    with NozzleServer(project_limits=limits, staging_directory=tmp_path) as server:
        assert server.source_imports.project_limits == limits
        context = server.import_context()
        with pytest.raises(ScanImportError, match="archive byte limit"):
            _ = server.source_imports.open_project(
                generation=context.generation,
                token=context.token,
                filename="too-large.scansor",
                size=4097,
                confirmed_discard=True,
            )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        http = NativeHttp(server, tmp_path)
        try:
            assert http.metadata()["project_archive"]["limits"] == asdict(limits)
            _, operation = http.reserve(triangle_bytes())
            status, result, _ = http.request(
                f"/api/sources/imports/{operation['operationId']}/bytes",
                triangle_bytes(),
            )
            assert status in {200, 202}, result
            assert http.finish(operation)["status"] == "accepted"
            status, result, _ = http.request("/api/project/export", http.cursor())
            assert status == 422 and result["code"] == "source_limit", result
        finally:
            server.shutdown()
            thread.join(timeout=10)


@pytest.mark.skipif(
    os.environ.get("SCANSOR_LARGE_STL_TEST") != "1",
    reason="Opt-in multi-million-triangle native scale measurement",
)
def test_104_mib_stl_import_reduce_save_and_reopen(tmp_path: Path) -> None:
    cells = 1045
    facets = 2 * cells**2
    source = tmp_path / "104-mib.stl"
    dtype = np.dtype(
        [("normal", "<f4", 3), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
    )
    with source.open("wb") as output:
        _ = output.write(
            b"Generated native scale fixture".ljust(80, b"\0")
            + struct.pack("<I", facets)
        )
        for start in range(0, cells, 4):
            rows = min(4, cells - start)
            x = np.tile(np.arange(cells, dtype=np.float32), rows)
            y = np.repeat(np.arange(start, start + rows, dtype=np.float32), cells)
            records = np.zeros(2 * len(x), dtype=dtype)
            records["normal"][:, 2] = 1
            points = records["vertices"]
            points[0::2, :, 0] = np.stack((x, x + 1, x), axis=1)
            points[0::2, :, 1] = np.stack((y, y, y + 1), axis=1)
            points[1::2, :, 0] = np.stack((x + 1, x + 1, x), axis=1)
            points[1::2, :, 1] = np.stack((y, y + 1, y + 1), axis=1)
            _ = output.write(records.tobytes())
    assert source.stat().st_size > 104 * 1024**2
    staging = tmp_path / "staging"
    staging.mkdir()
    with NozzleServer(staging_directory=staging) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        http = NativeHttp(server, staging)
        origin = f"http://127.0.0.1:{server.server_port}"

        def upload(path: Path, endpoint: str) -> dict[str, Any]:
            status, operation, _ = http.request(
                endpoint,
                {
                    **http.cursor(),
                    "filename": path.name,
                    "size": path.stat().st_size,
                    **({"confirmedDiscard": True} if endpoint.endswith("open") else {}),
                },
            )
            assert status == 202, operation
            with path.open("rb") as stream:
                request = Request(
                    origin + f"/api/sources/imports/{operation['operationId']}/bytes",
                    data=stream,
                    headers={
                        "Content-Length": str(path.stat().st_size),
                        "Content-Type": "application/octet-stream",
                        "X-Scansor-Request": "1",
                    },
                )
                with urlopen(request, timeout=600) as response:
                    assert response.status in {200, 202}
            return finish(operation)

        def finish(operation: dict[str, Any]) -> dict[str, Any]:
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                status, result, _ = http.request(
                    f"/api/sources/imports/{operation['operationId']}"
                )
                assert status == 200, result
                if result["terminal"]:
                    assert result["status"] == "accepted", result
                    return http.metadata()
                time.sleep(0.1)
            raise AssertionError("Large native operation did not terminate")

        try:
            assert http.metadata()["project_archive"]["limits"] == asdict(
                NATIVE_PROJECT_LIMITS
            )
            original = upload(source, "/api/sources/imports")
            assert original["vertices"] == (cells + 1) ** 2
            assert original["triangles"] == facets
            status, operation, _ = http.request(
                "/api/sources/virtual",
                {
                    **http.cursor(),
                    "settings": {"divisions": 32},
                },
            )
            assert status == 202, operation
            reduced = finish(operation)
            assert reduced["vertices"] < 2000
            assert reduced["triangles"] < 3000
            saved_recipe = reduced["graph"]["recipe"]
            archive = tmp_path / "large.scansor"
            request = Request(
                origin + "/api/project/export",
                data=json.dumps(http.cursor()).encode(),
                headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
            )
            with (
                urlopen(request, timeout=600) as response,
                archive.open("wb") as output,
            ):
                shutil.copyfileobj(response, output, length=65536)
            reopened = upload(archive, "/api/project/open")
            assert reopened["graph"]["recipe"] == saved_recipe
            assert reopened["virtual_scans"] == reduced["virtual_scans"]
            assert reopened["vertices"] == reduced["vertices"]
            print(
                json.dumps(
                    {
                        "source_bytes": source.stat().st_size,
                        "original_vertices": original["vertices"],
                        "original_triangles": facets,
                        "reduced_vertices": reduced["vertices"],
                        "reduced_triangles": reduced["triangles"],
                        "archive_bytes": archive.stat().st_size,
                    }
                )
            )
        finally:
            server.shutdown()
            thread.join(timeout=10)
