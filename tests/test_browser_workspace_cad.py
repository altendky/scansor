"""CAD adapter transactions use real helpers and retain native export invariants."""

import importlib
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from OCP.IFSelect import IFSelect_RetFail
from OCP.Interface import Interface_Static
from OCP.Standard import Standard_ConstructionError
from OCP.STEPCAFControl import STEPCAFControl_Writer

import experiments.nozzle_cad as nozzle_cad
from experiments.browser_runtime.workspace import BrowserWorkspace, WorkspaceError
from experiments.native_replay import cached_native_replay
from tests.test_arranged_face_workflow import graph_with_diagonal, reviewed_plate
from tests.test_browser_workspace import (
    EXAMPLE,
    call,
    finish,
    prepare,
    snapshot,
    success,
)
from tests.test_nozzle_cad import roundtrip


@pytest.fixture
def workspace() -> BrowserWorkspace:
    adapter = BrowserWorkspace(EXAMPLE)
    adapter.enable_cad()
    return adapter


@pytest.fixture
def fitted(workspace: BrowserWorkspace) -> BrowserWorkspace:
    recipe = json.loads((EXAMPLE / "recipes/cone-plane.json").read_text())
    recipe["nodes"][3]["label"] = "肩面 Ø12 — café"
    success(
        call(
            workspace,
            "replaceRecipe",
            token=snapshot(workspace)["token"],
            recipe=recipe,
        )
    )
    success(
        prepare(
            workspace,
            "evaluateGraph",
            token=snapshot(workspace)["token"],
            all_actions=True,
        )
    )
    state = success(finish(workspace))
    assert not state["errors"]
    return workspace


@pytest.fixture
def plate(workspace: BrowserWorkspace) -> BrowserWorkspace:
    recipe = graph_with_diagonal().snapshot()["recipe"]
    success(
        call(
            workspace,
            "replaceRecipe",
            token=snapshot(workspace)["token"],
            recipe=recipe,
        )
    )
    success(
        prepare(
            workspace,
            "evaluateGraph",
            token=snapshot(workspace)["token"],
            all_actions=True,
        )
    )
    success(finish(workspace))
    return workspace


def export(workspace: BrowserWorkspace, **changes: Any) -> dict[str, Any]:
    payload = {
        "token": snapshot(workspace)["token"],
        "target": "fit",
        "units": "Millimeters",
        "axis_up": False,
        "include_mesh": False,
        **changes,
    }
    return call(workspace, "exportCad", **payload)


def test_missing_or_incompatible_cad_does_not_poison_numeric_workspace(
    monkeypatch: pytest.MonkeyPatch,
):
    original = importlib.import_module

    def missing(name: str, package: str | None = None) -> Any:
        if name == "OCP":
            raise ImportError("missing investigated wheel")
        return original(name, package)

    monkeypatch.setattr(importlib, "import_module", missing)
    adapter = BrowserWorkspace(EXAMPLE)
    assert adapter.cad_enabled is False
    with pytest.raises(WorkspaceError, match="unavailable"):
        adapter.enable_cad()
    assert adapter.cad_enabled is False
    assert success(call(adapter, "loadWorkspace"))["vertices"] == 24999
    monkeypatch.setattr(importlib, "import_module", original)
    ocp = importlib.import_module("OCP")
    monkeypatch.setattr(ocp, "__version__", "different")
    with pytest.raises(WorkspaceError, match=r"8\.0\.1\.1"):
        adapter.enable_cad()
    assert adapter.cad_enabled is False
    assert snapshot(adapter)["evaluation_running"] is False


def test_cad_recipes_require_explicit_enablement_and_keep_atomic_validation():
    adapter = BrowserWorkspace(EXAMPLE)
    recipe = graph_with_diagonal().snapshot()["recipe"]
    recipe = cast(dict[str, Any], deepcopy(recipe))
    recipe["nodes"].append(
        {
            "id": "cut",
            "label": "Cut",
            "operation": "surface_intersection",
            "first": {"feature": "plate"},
            "second": {"feature": "left"},
        }
    )
    before = snapshot(adapter)
    assert (
        call(adapter, "importRecipe", token=before["token"], recipe=recipe)["error"][
            "code"
        ]
        == "unsupported_capability"
    )
    assert snapshot(adapter) == before
    adapter.enable_cad()
    imported = success(
        call(adapter, "importRecipe", token=before["token"], recipe=recipe)
    )
    assert any(node["id"] == "cut" for node in imported["recipe"]["nodes"])
    invalid = deepcopy(imported["recipe"])
    invalid["nodes"][-1]["second"] = {"feature": "missing"}
    assert (
        call(adapter, "replaceRecipe", token=imported["token"], recipe=invalid)[
            "error"
        ]["code"]
        == "validation_error"
    )
    assert snapshot(adapter)["token"] == imported["token"]
    assert snapshot(adapter)["recipe"] == imported["recipe"]


def test_face_candidate_preview_apply_use_real_helpers_and_preserve_lifecycle(
    plate: BrowserWorkspace,
):
    before = snapshot(plate)
    refs = [
        {"feature": name}
        for name in ("plate", "left", "right", "bottom", "top", "diagonal")
    ]
    candidates = success(
        call(
            plate,
            "faceCandidates",
            token=before["token"],
            target={"feature": "plate"},
            surfaces=refs,
        )
    )
    assert candidates["token"] == before["token"]
    assert snapshot(plate) == before
    native_plan, request = reviewed_plate(plate.graph)
    before = snapshot(plate)
    payload = request.model_dump()
    preview_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"proposal_token", "label", "choices"}
    }
    assert success(call(plate, "previewFaces", **preview_payload)) == native_plan
    assert snapshot(plate) == before
    stale = dict(payload, proposal_token="obsolete")
    assert call(plate, "applyFaces", **stale)["error"]["code"] == "stale_graph"
    assert snapshot(plate) == before
    applied = success(call(plate, "applyFaces", **payload))
    assert (
        applied["token"] != before["token"] and applied["revision"] > before["revision"]
    )
    assert applied["evaluation_running"] is False
    success(prepare(plate, "evaluateGraph", token=applied["token"], all_actions=True))
    completed = success(finish(plate))
    assert not completed["errors"] and not completed["required_failures"]


def test_face_authoring_rejects_pending_and_stale_requests(plate: BrowserWorkspace):
    token = snapshot(plate)["token"]
    success(prepare(plate, "evaluateGraph", token=token, all_actions=True))
    payload = {
        "token": token,
        "target": {"feature": "plate"},
        "surfaces": [{"feature": "left"}],
    }
    assert (
        call(plate, "faceCandidates", **payload)["error"]["code"] == "evaluation_busy"
    )
    success(finish(plate))
    payload["token"] = "old"
    assert call(plate, "faceCandidates", **payload)["error"]["code"] == "stale_graph"


@pytest.mark.parametrize(
    "units,unit_token",
    [
        ("Millimeters", "SI_UNIT(.MILLI.,.METRE.)"),
        ("Centimeters", "SI_UNIT(.CENTI.,.METRE.)"),
        ("Meters", "SI_UNIT($,.METRE.)"),
    ],
)
def test_repeated_export_preserves_units_unicode_float64_mesh_and_recipe(
    fitted: BrowserWorkspace, tmp_path: Path, units: str, unit_token: str
):
    before = snapshot(fitted)
    source = fitted.workspace.local.copy()
    _ = STEPCAFControl_Writer()
    previous = Interface_Static.CVal_s("write.step.unit")
    for _ in range(2):
        descriptor = success(export(fitted, units=units, include_mesh=True))
        assert descriptor == {
            "bytes": len(fitted.export_buffer),
            "content_type": "application/zip",
        }
        metadata, shapes, mesh, step = roundtrip(fitted.export_buffer, tmp_path)
        assert "肩面 Ø12 — café" in shapes and unit_token in step
        assert metadata["units"] == units
        assert metadata["source_sha256"] == fitted.workspace.default.source_sha256
        assert metadata["recipe"] == before["recipe"]
        assert metadata["export"]["token"] == before["token"]
        assert mesh is not None and mesh[0].dtype == np.float64
        matrix = np.asarray(metadata["transform_local_to_export"])
        np.testing.assert_array_equal(
            mesh[0], source @ matrix[:3, :3].T + matrix[:3, 3]
        )
        np.testing.assert_array_equal(mesh[1], fitted.workspace.data.triangles)
        assert snapshot(fitted) == before
        assert Interface_Static.CVal_s("write.step.unit") == previous
        fitted.clear_export()
        assert fitted.export_buffer == b""
    np.testing.assert_array_equal(fitted.workspace.local, source)


def test_failed_and_malformed_exports_clear_previous_bytes(fitted: BrowserWorkspace):
    success(export(fitted))
    assert fitted.export_buffer
    assert export(fitted, token="old")["error"]["code"] == "stale_graph"
    assert not fitted.export_buffer
    for changes in (
        {"units": "inches"},
        {"axis_up": False, "origin_plane": "end"},
        {"transform": "absent"},
    ):
        success(export(fitted))
        assert export(fitted, **changes)["error"]["code"] == "validation_error"
        assert not fitted.export_buffer
    success(export(fitted))
    assert (
        json.loads(fitted.call_json("exportCad", "invalid JSON"))["error"]["code"]
        == "validation_error"
    )
    assert not fitted.export_buffer
    success(export(fitted))
    success(call(fitted, "close"))
    assert not fitted.export_buffer


def test_explicit_transform_is_shared_by_step_metadata_and_reference_mesh(
    fitted: BrowserWorkspace, tmp_path: Path
):
    recipe = snapshot(fitted)["recipe"]
    recipe["nodes"].extend(
        [
            {
                "id": "p0",
                "label": "Origin",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
            {
                "id": "p1",
                "label": "Scale point",
                "operation": "point",
                "initial_coordinates": [1, 0, 0],
            },
            {
                "id": "a0",
                "label": "Output Z",
                "operation": "axis",
                "source_fit": "side",
            },
            {
                "id": "a1",
                "label": "Output X",
                "operation": "axis",
                "source_points": ["p0", "p1"],
            },
            {
                "id": "frame",
                "label": "Output frame",
                "operation": "frame",
                "origin_point": "p0",
                "primary_reference": "a0",
                "primary_output_axis": "+Z",
                "secondary_reference": "a1",
                "secondary_output_axis": "+X",
            },
            {
                "id": "scale",
                "label": "Output scale",
                "operation": "scale",
                "distances": [
                    {"first_point": "p0", "second_point": "p1", "known_distance": 2}
                ],
            },
            {
                "id": "transform",
                "label": "Output transform",
                "operation": "transform",
                "frame": "frame",
                "scale": "scale",
            },
        ]
    )
    updated = success(
        call(fitted, "replaceRecipe", token=snapshot(fitted)["token"], recipe=recipe)
    )
    success(prepare(fitted, "evaluateGraph", token=updated["token"], all_actions=True))
    ready = success(finish(fitted))
    success(export(fitted, transform="transform", include_mesh=True))
    metadata, shapes, mesh, _ = roundtrip(fitted.export_buffer, tmp_path)
    assert shapes and mesh is not None
    matrix = np.asarray(ready["results"]["transform"]["matrix"])
    np.testing.assert_array_equal(metadata["transform_local_to_export"], matrix)
    np.testing.assert_array_equal(
        mesh[0], fitted.workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    )
    assert (
        export(fitted, transform="transform", axis_up=True)["error"]["code"]
        == "validation_error"
    )


def test_collection_export_checks_authoring_roots_before_kernel(
    plate: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    _, request = reviewed_plate(plate.graph)
    authored = success(call(plate, "applyFaces", **request.model_dump()))
    token = authored["token"]
    calls: list[bool] = []

    def forbidden(*_args: Any, **_kwargs: Any) -> bytes:
        calls.append(True)
        raise AssertionError("preflight must reject before exporting")

    monkeypatch.setattr(nozzle_cad, "export_cad", forbidden)
    response = export(plate, target=None, scope="all_faces")
    assert response["error"]["code"] == "validation_error"
    assert "not current" in response["error"]["message"] and not calls
    success(prepare(plate, "evaluateGraph", token=token, all_actions=True))
    ready = success(finish(plate))
    face = next(
        node["id"]
        for node in ready["recipe"]["nodes"]
        if node["operation"] in {"trimmed_face", "arranged_face"}
    )
    response = export(plate, target=face, scope="body")
    assert response["error"]["code"] == "validation_error" and not calls


def test_selected_export_requires_review_owner_even_when_face_geometry_is_ready(
    plate: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    _, request = reviewed_plate(plate.graph)
    authored = success(call(plate, "applyFaces", **request.model_dump()))
    success(prepare(plate, "evaluateGraph", token=authored["token"], all_actions=True))
    ready = success(finish(plate))
    owner = next(
        node["id"]
        for node in ready["recipe"]["nodes"]
        if node["operation"] == "build_faces"
    )
    face = next(
        node["id"]
        for node in ready["recipe"]["nodes"]
        if node["operation"] in {"trimmed_face", "arranged_face"}
    )
    observed: list[list[str]] = []
    original = plate.graph.readiness_status

    def failed_review(
        token: str, targets: list[str] | None = None, all_actions: bool = False
    ) -> tuple[bool, dict[str, object] | None]:
        observed.append(list(targets or []))
        needed, state = original(token, targets, all_actions)
        assert not needed and state is not None
        failed = cast(dict[str, Any], deepcopy(state))
        failed["required_failures"] = [owner]
        failed["states"][owner] = "failed"
        failed["errors"][owner] = "shared boundary review failed"
        return False, failed

    def forbidden(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("failed review cannot reach kernel export")

    monkeypatch.setattr(plate.graph, "readiness_status", failed_review)
    monkeypatch.setattr(nozzle_cad, "export_cad", forbidden)
    response = export(
        plate,
        target=None,
        scope="selected_faces",
        targets=[face],
        review_owners=[owner],
    )
    assert response["error"]["code"] == "validation_error"
    assert "shared boundary review failed" in response["error"]["message"]
    assert observed == [[face, owner]] and plate.export_buffer == b""


@pytest.mark.parametrize("native_error", [False, True])
def test_writer_failure_restores_global_units_cleans_files_and_replay_scope(
    fitted: BrowserWorkspace,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    native_error: bool,
):
    original = STEPCAFControl_Writer
    _ = original()
    previous = Interface_Static.CVal_s("write.step.unit")
    assert Interface_Static.SetCVal_s("write.step.unit", "CM")
    calls: list[object] = []

    def construct() -> object:
        value = object()
        calls.append(value)
        return value

    class FailedWriter:
        def __init__(self) -> None:
            _ = original()

        def SetNameMode(self, _value: bool) -> None:
            pass

        def Transfer(self, *_args: Any) -> bool:
            return True

        def Write(self, path: str) -> Any:
            assert Interface_Static.CVal_s("write.step.unit") == "M"
            first = cached_native_replay(("test", "export-owner"), construct)
            assert cached_native_replay(("test", "export-owner"), construct) is first
            _ = Path(path).write_bytes(b"partial disposable STEP")
            if native_error:
                raise Standard_ConstructionError("injected native writer failure")
            return IFSelect_RetFail

    try:
        with monkeypatch.context() as patch:
            patch.setattr(tempfile, "tempdir", str(tmp_path))
            patch.setattr(nozzle_cad, "STEPCAFControl_Writer", FailedWriter)
            response = export(fitted, units="Meters")
            assert response["error"]["code"] == "validation_error"
            assert not fitted.export_buffer
            assert list(tmp_path.iterdir()) == []
            assert Interface_Static.CVal_s("write.step.unit") == "CM"
        assert len(calls) == 1
        assert cached_native_replay(("test", "export-owner"), construct) is not calls[0]
        assert cached_native_replay(("test", "export-owner"), construct) is not calls[0]
        assert len(calls) == 3
        success(export(fitted, units="Meters"))
        assert Interface_Static.CVal_s("write.step.unit") == "CM"
    finally:
        assert Interface_Static.SetCVal_s("write.step.unit", previous)
