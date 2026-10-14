"""The worker adapter preserves graph semantics without a computation server."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"


@pytest.fixture
def workspace() -> BrowserWorkspace:
    return BrowserWorkspace(EXAMPLE)


def call(workspace: BrowserWorkspace, operation: str, **payload: Any) -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads(workspace.call_json(operation, json.dumps(payload)))
    )


def success(response: dict[str, Any]) -> Any:
    assert response["ok"], response
    return response["result"]


def snapshot(workspace: BrowserWorkspace) -> dict[str, Any]:
    return cast(dict[str, Any], success(call(workspace, "getGraph")))


def prepare(
    workspace: BrowserWorkspace, operation: str, **payload: Any
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(workspace.prepare_evaluation_json(operation, json.dumps(payload))),
    )


def finish(workspace: BrowserWorkspace) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(workspace.finish_evaluation_json()))


def replace(workspace: BrowserWorkspace, recipe: dict[str, Any]) -> dict[str, Any]:
    return call(
        workspace, "replaceRecipe", token=snapshot(workspace)["token"], recipe=recipe
    )


def cone_recipe() -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads((EXAMPLE / "recipes/cone-plane.json").read_text())
    )


def test_source_metadata_and_display_buffers_preserve_authoritative_evidence(
    workspace: BrowserWorkspace,
):
    metadata = success(call(workspace, "loadWorkspace"))
    assert metadata["vertices"] == 24999 and metadata["triangles"] == 49994
    assert (
        metadata["session"]["source_sha256"]
        == workspace.workspace.default.source_sha256
    )
    assert metadata["positions"] == {"dtype": "<f4", "components": 3}
    assert metadata["indices"] == {"dtype": "<u4", "components": 3}
    assert workspace.workspace.local.dtype == np.float64
    assert workspace.workspace.data.weights.dtype == np.float64
    assert workspace.display_positions.dtype == np.dtype("<f4")
    assert workspace.display_indices.dtype == np.dtype("<u4")
    assert workspace.display_positions.flags.c_contiguous
    assert workspace.display_indices.flags.c_contiguous
    assert not np.shares_memory(workspace.display_positions, workspace.workspace.local)
    np.testing.assert_array_equal(
        workspace.display_positions, workspace.workspace.local.astype("<f4")
    )
    np.testing.assert_array_equal(
        workspace.display_indices, workspace.workspace.data.triangles
    )
    with pytest.raises(ValueError, match="read-only"):
        workspace.workspace.local[0, 0] = 0
    with pytest.raises(ValueError, match="read-only"):
        workspace.workspace.data.weights[0] = 0
    with pytest.raises(ValueError, match="read-only"):
        workspace.display_positions[0, 0] = 0


def test_default_demo_and_replayed_recipe_evaluate_all_thirty_actions(
    workspace: BrowserWorkspace,
):
    state = snapshot(workspace)
    assert len(state["recipe"]["nodes"]) == 30
    assert state["evaluation_running"] is False
    planned = success(
        prepare(workspace, "ensureGraph", token=state["token"], all_actions=True)
    )
    assert planned["run"] is True and planned["state"]["evaluation_running"] is True
    assert snapshot(workspace)["evaluation_running"] is True
    result = success(finish(workspace))
    assert set(result["states"].values()) == {"ready"}
    assert not result["errors"] and not result["diagnostics"]
    assert not result["required_failures"] and result["evaluation_running"] is False
    assert result["revision"] > state["revision"]
    recipe = success(call(workspace, "exportRecipe"))
    replay = BrowserWorkspace(EXAMPLE)
    imported = success(
        call(replay, "importRecipe", token=snapshot(replay)["token"], recipe=recipe)
    )
    success(prepare(replay, "evaluateGraph", token=imported["token"], all_actions=True))
    recovered = success(finish(replay))
    assert recovered["token"] == result["token"]
    assert recovered["results"] == result["results"]
    assert recovered["derived"] == result["derived"]


@pytest.mark.parametrize("field", ["source_sha256", "reference_sha256"])
def test_source_identity_mismatch_does_not_mutate_graph(
    workspace: BrowserWorkspace, field: str
):
    before = snapshot(workspace)
    recipe = deepcopy(before["recipe"])
    recipe["nodes"][0][field] = "0" * 64
    rejected = replace(workspace, recipe)
    assert rejected["error"]["code"] == "validation_error"
    assert rejected["error"]["status"] == 422
    assert snapshot(workspace) == before


def test_source_bound_selection_validation_and_stale_edits_are_atomic(
    workspace: BrowserWorkspace,
):
    before = snapshot(workspace)
    recipe = deepcopy(before["recipe"])
    selection = next(
        node for node in recipe["nodes"] if node["operation"] == "selection"
    )
    selection["ids"] = [True]
    assert replace(workspace, recipe)["error"]["code"] == "validation_error"
    selection["ids"] = [24999]
    assert replace(workspace, recipe)["error"]["code"] == "validation_error"
    assert snapshot(workspace) == before
    stale = call(workspace, "replaceRecipe", token="obsolete", recipe=before["recipe"])
    assert stale["error"]["code"] == "stale_graph" and stale["error"]["status"] == 409
    assert snapshot(workspace) == before


@pytest.mark.parametrize("operation", ["replaceRecipe", "importRecipe"])
def test_cad_recipe_is_rejected_without_partial_mutation(
    workspace: BrowserWorkspace, operation: str
):
    before = snapshot(workspace)
    recipe = deepcopy(before["recipe"])
    recipe["nodes"].append(
        {
            "id": "solid",
            "label": "Body",
            "operation": "body",
            "faces": [recipe["output"]],
            "sewing_tolerance": 1e-7,
        }
    )
    recipe["output"] = "solid"
    response = call(workspace, operation, token=before["token"], recipe=recipe)
    assert response["error"]["code"] == "unsupported_capability"
    assert "body" in response["error"]["message"]
    assert snapshot(workspace) == before


def test_recipe_import_normalizes_duplicate_names_and_preserves_input(
    workspace: BrowserWorkspace,
):
    recipe = cone_recipe()
    recipe["nodes"][1]["label"] = recipe["nodes"][0]["label"]
    original = deepcopy(recipe)
    response = success(
        call(
            workspace, "importRecipe", token=snapshot(workspace)["token"], recipe=recipe
        )
    )
    assert recipe == original
    assert response["import_warnings"][0]["id"] == recipe["nodes"][1]["id"]
    labels = [node["label"].strip().casefold() for node in response["recipe"]["nodes"]]
    assert len(labels) == len(set(labels))
    assert success(call(workspace, "exportRecipe")) == response["recipe"]


def test_graph_revision_cursor_and_lifecycle_only_status(workspace: BrowserWorkspace):
    original = snapshot(workspace)
    compact = success(call(workspace, "getGraph", revision=original["revision"]))
    assert compact == {
        "token": original["token"],
        "revision": original["revision"],
        "unchanged": True,
        "evaluation_running": False,
    }
    success(
        prepare(
            workspace,
            "evaluateGraph",
            token=original["token"],
            target=original["recipe"]["nodes"][0]["id"],
        )
    )
    assert (
        success(call(workspace, "getGraph", revision=original["revision"]))[
            "evaluation_running"
        ]
        is True
    )
    success(finish(workspace))
    changed = success(call(workspace, "getGraph", revision=original["revision"]))
    assert changed["revision"] > original["revision"] and not changed.get("unchanged")
    assert call(workspace, "getGraph", revision=True)["error"]["status"] == 422


def test_ensure_retains_failed_nodes_until_explicit_evaluation_retry(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    recipe = cone_recipe()
    recipe["nodes"][1]["ids"] = []
    state = success(replace(workspace, recipe))
    success(prepare(workspace, "ensureGraph", token=state["token"], targets=["side"]))
    failed = success(finish(workspace))
    assert (
        "side" in failed["required_failures"] and failed["states"]["side"] == "failed"
    )
    settled = success(
        prepare(workspace, "ensureGraph", token=state["token"], targets=["side"])
    )
    assert settled["run"] is False
    assert "side" in settled["state"]["required_failures"]
    attempted: list[bool] = []
    original = workspace.graph.evaluate

    def observed(*args: Any, **kwargs: Any) -> dict[str, object]:
        attempted.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(workspace.graph, "evaluate", observed)
    success(prepare(workspace, "evaluateGraph", token=state["token"], target="side"))
    success(finish(workspace))
    assert attempted == [True]


def test_job_failure_is_memoized_for_same_ensure_roots_and_cleared_on_retry(
    workspace: BrowserWorkspace, monkeypatch: pytest.MonkeyPatch
):
    state = success(replace(workspace, cone_recipe()))
    original = workspace.graph.ensure_current

    def broken(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        raise RuntimeError("bounded job failure")

    monkeypatch.setattr(workspace.graph, "ensure_current", broken)
    success(prepare(workspace, "ensureGraph", token=state["token"], targets=["side"]))
    terminal = success(finish(workspace))
    assert terminal["evaluation_error"] == "bounded job failure"
    settled = success(
        prepare(workspace, "ensureGraph", token=state["token"], targets=["side"])
    )
    assert (
        settled["run"] is False
        and settled["state"]["evaluation_error"] == "bounded job failure"
    )
    monkeypatch.setattr(workspace.graph, "ensure_current", original)
    success(prepare(workspace, "evaluateGraph", token=state["token"], target="side"))
    terminal = success(finish(workspace))
    assert terminal["states"]["side"] == "ready" and "evaluation_error" not in terminal
    assert "evaluation_error" not in snapshot(workspace)


def test_running_ensure_joins_without_scheduling_another_job(
    workspace: BrowserWorkspace,
):
    state = snapshot(workspace)
    started = success(
        prepare(
            workspace,
            "ensureGraph",
            token=state["token"],
            targets=[state["recipe"]["nodes"][0]["id"]],
        )
    )
    assert started["run"] is True
    joined = success(
        prepare(
            workspace,
            "ensureGraph",
            token=state["token"],
            targets=[state["recipe"]["output"]],
        )
    )
    assert joined["run"] is False and joined["state"]["evaluation_running"] is True
    assert (
        prepare(workspace, "evaluateGraph", token=state["token"])["error"]["code"]
        == "evaluation_busy"
    )
    success(finish(workspace))
    assert finish(workspace)["error"]["code"] == "evaluation_not_pending"


def test_mutation_supersedes_pending_evaluation_without_retaining_failure(
    workspace: BrowserWorkspace,
):
    state = snapshot(workspace)
    success(prepare(workspace, "evaluateGraph", token=state["token"]))
    recipe = deepcopy(state["recipe"])
    recipe["nodes"][0]["label"] = "Renamed scan"
    updated = success(replace(workspace, recipe))
    rejected = finish(workspace)
    assert rejected["error"]["code"] == "stale_graph"
    assert snapshot(workspace)["token"] == updated["token"]
    assert snapshot(workspace)["evaluation_running"] is False
    assert "evaluation_error" not in snapshot(workspace)


def test_catalogue_only_offers_nozzle_and_switching_resets_recipe(
    workspace: BrowserWorkspace,
):
    assert success(call(workspace, "listExamples")) == {
        "active": "nozzle",
        "examples": [{"id": "nozzle", "label": "Nozzle"}],
    }
    state = success(replace(workspace, cone_recipe()))
    assert (
        call(workspace, "selectExample", token=state["token"], example="repeated-boss")[
            "error"
        ]["code"]
        == "unsupported_capability"
    )
    success(call(workspace, "selectExample", token=state["token"], example="nozzle"))
    assert snapshot(workspace)["recipe"] == success(call(workspace, "getExampleRecipe"))
    assert len(snapshot(workspace)["recipe"]["nodes"]) == 30


def test_reuse_authoring_uses_existing_recipe_allocator(workspace: BrowserWorkspace):
    recipe = cone_recipe()
    side = next(node for node in recipe["nodes"] if node["id"] == "side")
    side["kind"] = "cylinder"
    target = deepcopy(recipe["nodes"][1])
    target.update(id="target", label="Target")
    recipe["nodes"].insert(3, target)
    state = success(replace(workspace, recipe))
    authored = success(
        call(
            workspace,
            "applyFeatureReuse",
            token=state["token"],
            reuse_id="reuse",
            allocation_seed="deterministic-browser-test",
            create=True,
            changes={
                "label": "Reusable cylinder",
                "fits": ["side"],
                "reference_selection": "outer_band",
                "target_selections": ["target"],
            },
        )
    )
    assert authored["reuse_authoring"]["generated_ids"]
    assert any(
        node["operation"] == "reuse_selection" for node in authored["recipe"]["nodes"]
    )
    assert authored["token"] != state["token"]


def test_structured_errors_and_close_are_bounded(workspace: BrowserWorkspace):
    for operation in ("faceCandidates", "previewFaces", "applyFaces", "exportCad"):
        assert call(workspace, operation)["error"]["code"] == "unsupported_capability"
    malformed = json.loads(workspace.call_json("replaceRecipe", "not JSON"))
    assert malformed["error"]["code"] == "validation_error"
    assert "Traceback" not in malformed["error"]["message"]
    assert call(workspace, "unknown")["error"]["code"] == "unsupported_operation"
    success(call(workspace, "close"))
    assert call(workspace, "getGraph")["error"]["code"] == "backend_closed"
    assert (
        prepare(workspace, "ensureGraph", token="closed")["error"]["code"]
        == "backend_closed"
    )
    success(call(workspace, "close"))
