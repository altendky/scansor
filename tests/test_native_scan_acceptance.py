"""Raw source-to-HTTP-to-fit-to-STEP acceptance with independent geometry."""

import hashlib
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from threading import Thread
from typing import Any

import numpy as np
import pytest

from experiments.feature_graph import Recipe
from experiments.nozzle_browser import NozzleServer
from experiments.scan_workspace import ScanWorkspace
from tests.native_scan_fixture import native_scene_fixture
from tests.scan_source_fixture import read_source_bytes
from tests.test_native_scan_http import NativeHttp
from tests.test_nozzle_cad import roundtrip, samples


@pytest.fixture
def native_acceptance(tmp_path: Path) -> Iterator[NativeHttp]:
    staging = tmp_path / "upload"
    staging.mkdir()
    with NozzleServer(staging_directory=staging) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield NativeHttp(server, staging)
        finally:
            server.shutdown()
            thread.join(timeout=10)
    assert not list(staging.iterdir())


def upload(http: NativeHttp, raw: bytes, filename: str) -> dict[str, Any]:
    status, operation, _ = http.request(
        "/api/sources/imports",
        {**http.cursor(), "filename": filename, "size": len(raw)},
    )
    assert status == 202, operation
    status, response, _ = http.request(
        f"/api/sources/imports/{operation['operationId']}/bytes", raw
    )
    assert status in {200, 202}, response
    result = http.finish(operation)
    assert result["status"] == "accepted", result
    return http.metadata()


def evaluate(http: NativeHttp, recipe: dict[str, Any]) -> dict[str, Any]:
    cursor = http.cursor()
    status, snapshot, _ = http.request(
        "/api/graph",
        {"token": cursor["token"], "recipe": recipe},
        generation=cursor["generation"],
    )
    assert status == 200, snapshot
    status, response, _ = http.request(
        "/api/graph/evaluate",
        {"token": snapshot["token"], "all_actions": True},
        generation=cursor["generation"],
    )
    assert status == 202, response
    assert http.server.graph_job is not None
    _ = http.server.graph_job.result(timeout=30)
    status, snapshot, _ = http.request("/api/graph", generation=cursor["generation"])
    assert status == 200 and not snapshot["errors"], snapshot
    return snapshot


def test_raw_nozzle_exceeds_json_limit_and_opens_source_only(
    native_acceptance: NativeHttp,
) -> None:
    raw = Path(
        "examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply"
    ).read_bytes()
    assert len(raw) > 1_000_000
    meta = upload(native_acceptance, raw, "unrelated-name.ply")
    workspace = native_acceptance.server.workspace
    assert isinstance(workspace, ScanWorkspace)
    assert read_source_bytes(workspace) == raw
    assert workspace.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert len(workspace.local) == 24_999
    assert len(workspace.data.triangles) == 49_994
    assert np.all(workspace.data.weights > 0)
    assert len(meta["graph"]["recipe"]["nodes"]) == 1
    source = meta["graph"]["recipe"]["nodes"][0]
    assert source["operation"] == "source"
    assert source["descriptor"]["filename"] == "unrelated-name.ply"
    assert workspace.descriptor is not None
    assert workspace.descriptor.units == "unknown"
    state = evaluate(native_acceptance, meta["graph"]["recipe"])
    assert state["states"] == {source["id"]: "ready"}


def test_independent_source_rows_fits_and_step_roundtrip(
    native_acceptance: NativeHttp,
    tmp_path: Path,
) -> None:
    fixture = native_scene_fixture()
    meta = upload(native_acceptance, fixture.raw, "horizontal.ply")
    generation = meta["project_generation"]
    workspace = native_acceptance.server.workspace
    assert isinstance(workspace, ScanWorkspace)
    assert read_source_bytes(workspace) == fixture.raw
    np.testing.assert_array_equal(workspace.local, fixture.positions)
    np.testing.assert_array_equal(workspace.data.triangles, fixture.triangles)
    np.testing.assert_allclose(workspace.data.normals, fixture.normals, atol=1e-15)
    np.testing.assert_allclose(workspace.data.weights, fixture.weights, atol=1e-14)
    for name, expected, dtype in (
        ("positions", fixture.positions, "<f4"),
        ("indices", fixture.triangles, "<u4"),
    ):
        status, raw, headers = native_acceptance.request(meta[name]["url"])
        assert status == 200
        assert headers["X-Scansor-Generation"] == str(generation)
        np.testing.assert_array_equal(np.frombuffer(raw, dtype=dtype), expected.ravel())
    recipe = deepcopy(meta["graph"]["recipe"])
    source = recipe["nodes"][0]["id"]
    for kind, ids in fixture.selections.items():
        selection = kind + "-rows"
        recipe["nodes"].extend(
            [
                {
                    "id": selection,
                    "label": selection,
                    "operation": "selection",
                    "source": source,
                    "ids": ids,
                },
                {
                    "id": kind,
                    "label": kind,
                    "operation": "fit",
                    "kind": kind,
                    "selections": [selection],
                },
            ]
        )
    recipe["output"] = "cylinder"
    state = evaluate(native_acceptance, recipe)
    # Schema defaults are authored normalization; numerical starts remain derived.
    assert state["recipe"] == Recipe.model_validate(recipe).model_dump(mode="json")
    cylinder, plane = state["results"]["cylinder"], state["results"]["plane"]
    assert cylinder["analytic"]["radius"] == pytest.approx(2.5, abs=1e-6)
    np.testing.assert_allclose(
        np.abs(cylinder["analytic"]["axis"]), [1, 0, 0], atol=1e-6
    )
    assert cylinder["weighted_rms"] < 1e-6
    assert plane["weighted_rms"] < 1e-12
    equation = np.asarray(plane["parameters"])
    np.testing.assert_allclose(np.abs(equation[:3]), [1, 0, 0], atol=1e-12)
    assert abs(equation[3]) == pytest.approx(8.0, abs=1e-12)
    for name, fitted in (("cylinder", cylinder), ("plane", plane)):
        ids = fixture.selections[name]
        points = fixture.positions[ids]
        if name == "plane":
            independent_residuals = points @ equation[:3] - equation[3]
        else:
            geometry = cylinder["analytic"]
            axis = np.asarray(geometry["axis"])
            offset = points - geometry["origin"]
            radial = offset - (offset @ axis)[:, None] * axis
            independent_residuals = np.linalg.norm(radial, axis=1) - geometry["radius"]
        residuals = np.asarray(fitted["residuals"])
        assert len(residuals) == len(ids)
        np.testing.assert_allclose(residuals, independent_residuals, atol=1e-12)
        independent_rms = np.sqrt(
            fixture.weights[ids] @ independent_residuals**2 / fixture.weights[ids].sum()
        )
        assert fitted["weighted_rms"] == pytest.approx(independent_rms, abs=1e-12)
    status, archive, _ = native_acceptance.request(
        "/api/export/cad",
        {
            "token": state["token"],
            "target": "cylinder",
            "units": "Millimeters",
            "axis_up": False,
            "include_mesh": False,
        },
        generation=generation,
    )
    assert status == 200, archive
    exported, shapes, mesh, _ = roundtrip(archive, tmp_path)
    assert exported["units"] == "Millimeters" and mesh is None
    assert len(shapes) == 1
    points = samples(next(iter(shapes.values())))
    np.testing.assert_allclose(
        np.linalg.norm(points[:, 1:] - [-4, 5], axis=1), 2.5, atol=1e-6
    )
    np.testing.assert_allclose(
        [points[:, 0].min(), points[:, 0].max()], [0, 7], atol=1e-6
    )
