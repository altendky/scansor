"""Cancellation discards computation without changing source or failure policy."""

import json
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace, WorkspaceError
from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from tests.test_browser_workspace import (
    EXAMPLE,
    call,
    cone_recipe,
    finish,
    prepare,
    replace,
    snapshot,
    success,
)


@pytest.fixture
def workspace() -> BrowserWorkspace:
    adapter = BrowserWorkspace(EXAMPLE)
    success(replace(adapter, cone_recipe()))
    return adapter


def restore(workspace: BrowserWorkspace, recipe: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads(workspace.restore_cancelled_json(json.dumps(recipe)))
    )


def evaluate(workspace: BrowserWorkspace) -> dict[str, Any]:
    success(
        prepare(
            workspace,
            "evaluateGraph",
            token=snapshot(workspace)["token"],
            all_actions=True,
        )
    )
    return cast(dict[str, Any], success(finish(workspace)))


@pytest.mark.parametrize("terminal", [False, True])
def test_discard_intermediate_or_terminal_results_and_continue(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch, terminal: bool
):
    before = snapshot(workspace)
    source = workspace.workspace
    arrays = (source.local, source.data.xyz, source.data.weights)
    workspace.checkpoint_operation()
    original = workspace.graph.evaluate

    def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        if terminal:
            _ = original(before["token"], all_actions=True)
            assert set(snapshot(workspace)["states"].values()) == {"ready"}
        else:
            _ = original(before["token"], target="outer_band")
            assert snapshot(workspace)["states"]["outer_band"] == "ready"
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace.graph, "evaluate", interrupted)
    success(
        prepare(workspace, "evaluateGraph", token=before["token"], all_actions=True)
    )
    cancelled = finish(workspace)
    assert cancelled["error"] == {
        "code": "computation_cancelled",
        "status": 409,
        "message": "Browser computation was cancelled.",
    }
    assert snapshot(workspace)["evaluation_running"] is False
    recovered = success(restore(workspace, before["recipe"]))
    assert recovered["token"] == before["token"]
    assert recovered["recipe"] == before["recipe"]
    assert set(recovered["states"].values()) == {"unevaluated"}
    assert recovered["results"] == {} and recovered["errors"] == {}
    assert "evaluation_error" not in recovered
    assert workspace.workspace is source
    for original_array, current in zip(
        arrays, (source.local, source.data.xyz, source.data.weights), strict=True
    ):
        assert current is original_array and current.dtype == np.float64
        assert not current.flags.writeable
    assert set(evaluate(workspace)["states"].values()) == {"ready"}


def test_restore_discards_a_completed_uncancelled_envelope(
    workspace: BrowserWorkspace,
):
    # The coordinator may receive Cancel after Python returns but before delivery.
    before = snapshot(workspace)
    workspace.checkpoint_operation()
    assert set(evaluate(workspace)["states"].values()) == {"ready"}
    recovered = success(restore(workspace, before["recipe"]))
    assert recovered["results"] == {}
    assert recovered["evaluation_running"] is False


def test_cancelled_retry_preserves_node_failure_and_explicit_retry_policy(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    recipe = cone_recipe()
    recipe["nodes"][1]["ids"] = []
    failed = success(replace(workspace, recipe))
    success(prepare(workspace, "ensureGraph", token=failed["token"], targets=["side"]))
    failed = success(finish(workspace))
    assert failed["states"]["side"] == "failed"
    workspace.checkpoint_operation()

    def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace.graph, "evaluate", interrupted)
    success(prepare(workspace, "evaluateGraph", token=failed["token"], target="side"))
    assert finish(workspace)["error"]["code"] == "computation_cancelled"
    recovered = success(restore(workspace, recipe))
    assert recovered["states"]["side"] == "failed"
    assert recovered["errors"]["side"] == failed["errors"]["side"]

    attempts: list[bool] = []
    original = workspace.workspace.fit

    def observed(*args: Any, **kwargs: Any):
        attempts.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace.workspace, "fit", observed)
    planned = success(
        prepare(workspace, "ensureGraph", token=failed["token"], targets=["side"])
    )
    settled = success(finish(workspace)) if planned["run"] else planned["state"]
    assert settled["states"]["side"] == "failed"
    assert "side" in settled["required_failures"] and attempts == []
    # An edit invalidates the failure using the existing graph replace rules.
    fixed = success(replace(workspace, cone_recipe()))
    success(prepare(workspace, "evaluateGraph", token=fixed["token"], all_actions=True))
    assert set(success(finish(workspace))["states"].values()) == {"ready"}
    assert attempts


def test_cancelled_retry_preserves_job_failure_memo(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    before = snapshot(workspace)

    def failed(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise RuntimeError("known failure")

    monkeypatch.setattr(workspace.graph, "ensure_current", failed)
    success(prepare(workspace, "ensureGraph", token=before["token"], targets=["side"]))
    assert success(finish(workspace))["evaluation_error"] == "known failure"
    workspace.checkpoint_operation()

    def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace.graph, "evaluate", interrupted)
    success(prepare(workspace, "evaluateGraph", token=before["token"], target="side"))
    assert finish(workspace)["error"]["code"] == "computation_cancelled"
    recovered = success(restore(workspace, before["recipe"]))
    assert recovered["evaluation_error"] == "known failure"
    settled = success(
        prepare(workspace, "ensureGraph", token=before["token"], targets=["side"])
    )
    assert (
        not settled["run"] and settled["state"]["evaluation_error"] == "known failure"
    )
    success(prepare(workspace, "evaluateGraph", token=before["token"], target="side"))
    retried = success(finish(workspace))
    assert retried["states"]["side"] == "ready"
    assert "evaluation_error" not in retried


def test_restore_uses_coordinator_recipe_without_collision_renaming(
    workspace: BrowserWorkspace,
):
    before = snapshot(workspace)
    workspace.checkpoint_operation()
    recipe = deepcopy(before["recipe"])
    recipe["nodes"][1]["label"] = recipe["nodes"][0]["label"]
    rejected = restore(workspace, recipe)
    assert rejected["error"]["code"] == "validation_error"
    assert "unique" in rejected["error"]["message"]
    assert snapshot(workspace) == before
    recipe["nodes"][1]["label"] = "Selection Ø12 — café"
    accepted = success(restore(workspace, recipe))
    assert accepted["recipe"]["nodes"][1]["label"] == recipe["nodes"][1]["label"]
    assert accepted["token"] != before["token"]


@pytest.mark.parametrize("identity", ["source_sha256", "reference_sha256"])
def test_invalid_restore_is_atomic_and_clears_export_bytes(
    workspace: BrowserWorkspace, identity: str, monkeypatch: pytest.MonkeyPatch
):
    before = snapshot(workspace)
    workspace.checkpoint_operation()
    monkeypatch.setattr(workspace, "_export_buffer", b"obsolete ZIP")
    invalid = deepcopy(before["recipe"])
    invalid["nodes"][0][identity] = "0" * 64
    rejected = restore(workspace, invalid)
    assert rejected["error"]["code"] == "validation_error"
    assert snapshot(workspace) == before
    assert workspace.export_buffer == b""
    assert success(restore(workspace, before["recipe"]))["token"] == before["token"]


def test_checkpoint_lifecycle_and_capability_guard(workspace: BrowserWorkspace):
    assert restore(workspace, cone_recipe())["error"]["code"] == "checkpoint_missing"
    before = snapshot(workspace)
    workspace.checkpoint_operation()
    success(prepare(workspace, "evaluateGraph", token=before["token"]))
    with pytest.raises(WorkspaceError, match="active evaluation"):
        workspace.checkpoint_operation()
    success(restore(workspace, before["recipe"]))
    assert finish(workspace)["error"]["code"] == "evaluation_not_pending"
    assert restore(workspace, cone_recipe())["error"]["code"] == "checkpoint_missing"
    workspace.checkpoint_operation()
    recipe = cone_recipe()
    recipe["nodes"].append(
        {"id": "body", "label": "Body", "operation": "body", "faces": ["side"]}
    )
    assert restore(workspace, recipe)["error"]["code"] == "unsupported_capability"
    success(call(workspace, "close"))
    assert restore(workspace, cone_recipe())["error"]["code"] == "backend_closed"


def test_failure_checkpoint_is_recipe_bound_and_requires_fresh_graph(
    workspace: BrowserWorkspace,
):
    checkpoint = workspace.graph.failure_checkpoint()
    changed = cone_recipe()
    changed["nodes"][0]["label"] = "Changed"
    other = FeatureGraph(workspace.workspace, Recipe.model_validate(changed))
    with pytest.raises(StaleGraph, match="another recipe"):
        other.restore_failures(checkpoint)
    _ = evaluate(workspace)
    with pytest.raises(ValueError, match="fresh graph"):
        workspace.graph.restore_failures(checkpoint)


def test_cancelled_export_restores_same_cad_source_and_next_export_succeeds(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    import experiments.nozzle_cad as nozzle_cad

    workspace.enable_cad()
    state = evaluate(workspace)
    payload = {
        "token": state["token"],
        "target": "fit",
        "units": "Millimeters",
        "axis_up": False,
        "include_mesh": False,
    }
    success(call(workspace, "exportCad", **payload))
    assert workspace.export_buffer.startswith(b"PK")
    workspace.checkpoint_operation()

    def interrupted(*_args: Any, **_kwargs: Any):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(nozzle_cad, "export_cad", interrupted)
        assert (
            call(workspace, "exportCad", **payload)["error"]["code"]
            == "computation_cancelled"
        )
    assert workspace.export_buffer == b""
    source = workspace.workspace
    success(restore(workspace, state["recipe"]))
    assert workspace.cad_enabled and workspace.workspace is source
    _ = evaluate(workspace)
    success(call(workspace, "exportCad", **payload))
    assert workspace.export_buffer.startswith(b"PK")


def test_failed_body_survives_cancelled_retry_without_kernel_reassembly(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    import experiments.body_geometry as body_geometry

    workspace.enable_cad()
    recipe = cone_recipe()
    recipe["nodes"].extend(
        [
            {
                "id": "edge",
                "label": "Edge",
                "operation": "surface_intersection",
                "first": {"feature": "fit", "surface": "end"},
                "second": {"feature": "fit", "surface": "side"},
            },
            {
                "id": "cap",
                "label": "Cap",
                "operation": "trimmed_face",
                "surface": {"feature": "fit", "surface": "end"},
                "boundaries": [{"intersection": "edge", "keep": "inside"}],
            },
            {"id": "body", "label": "Open body", "operation": "body", "faces": ["cap"]},
        ]
    )
    recipe["output"] = "body"
    success(replace(workspace, recipe))
    failed = evaluate(workspace)
    assert (
        failed["states"]["body"] == "failed" and "body" in failed["required_failures"]
    )
    workspace.checkpoint_operation()

    def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(workspace.graph, "evaluate", interrupted)
    success(
        prepare(workspace, "evaluateGraph", token=failed["token"], all_actions=True)
    )
    assert finish(workspace)["error"]["code"] == "computation_cancelled"
    restored = success(restore(workspace, failed["recipe"]))
    assert restored["states"]["body"] == "failed"
    assert restored["diagnostics"]["body"] == failed["diagnostics"]["body"]
    original = body_geometry.assemble_body

    def forbidden(*_args: Any, **_kwargs: Any):
        pytest.fail("ensure retried an unchanged failed Body")

    monkeypatch.setattr(body_geometry, "assemble_body", forbidden)
    planned = success(
        prepare(workspace, "ensureGraph", token=failed["token"], all_actions=True)
    )
    settled = success(finish(workspace)) if planned["run"] else planned["state"]
    assert (
        settled["states"]["body"] == "failed" and "body" in settled["required_failures"]
    )
    attempts: list[bool] = []

    def observed(*args: Any, **kwargs: Any):
        attempts.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(body_geometry, "assemble_body", observed)
    success(
        prepare(workspace, "evaluateGraph", token=failed["token"], all_actions=True)
    )
    assert success(finish(workspace))["states"]["body"] == "failed"
    assert attempts == [True]


def test_interrupt_during_step_write_restores_units_files_and_native_scope(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from OCP.Interface import Interface_Static
    from OCP.STEPCAFControl import STEPCAFControl_Writer

    import experiments.nozzle_cad as nozzle_cad
    from experiments.native_replay import cached_native_replay

    workspace.enable_cad()
    before = evaluate(workspace)
    workspace.checkpoint_operation()
    _ = STEPCAFControl_Writer()
    previous = Interface_Static.CVal_s("write.step.unit")
    assert Interface_Static.SetCVal_s("write.step.unit", "CM")
    cached: list[object] = []

    def construct() -> object:
        value = object()
        cached.append(value)
        return value

    class InterruptedWriter:
        def SetNameMode(self, _value: bool) -> None:
            pass

        def Transfer(self, *_args: Any) -> bool:
            return True

        def Write(self, path: str) -> Any:
            assert Interface_Static.CVal_s("write.step.unit") == "M"
            value = cached_native_replay(("cancel-test", "step"), construct)
            assert cached_native_replay(("cancel-test", "step"), construct) is value
            _ = Path(path).write_bytes(b"interrupted STEP")
            raise KeyboardInterrupt

    payload = {
        "token": before["token"],
        "target": "fit",
        "units": "Meters",
        "axis_up": False,
        "include_mesh": False,
    }
    try:
        with monkeypatch.context() as patch:
            patch.setattr(tempfile, "tempdir", str(tmp_path))
            patch.setattr(nozzle_cad, "STEPCAFControl_Writer", InterruptedWriter)
            cancelled = call(workspace, "exportCad", **payload)
            assert cancelled["error"]["code"] == "computation_cancelled"
            assert not workspace.export_buffer and list(tmp_path.iterdir()) == []
            assert Interface_Static.CVal_s("write.step.unit") == "CM"
        assert len(cached) == 1
        assert cached_native_replay(("cancel-test", "step"), construct) is not cached[0]
        success(restore(workspace, before["recipe"]))
        _ = evaluate(workspace)
        success(call(workspace, "exportCad", **payload))
        assert workspace.export_buffer.startswith(b"PK")
        assert Interface_Static.CVal_s("write.step.unit") == "CM"
    finally:
        assert Interface_Static.SetCVal_s("write.step.unit", previous)
