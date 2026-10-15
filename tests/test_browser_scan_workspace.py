"""Private worker source candidates and recovery through the shared owner."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe
from experiments.scan_project import AcceptedScan, ScanProject
from experiments.scan_workspace import ScanImportError, ScanLimits, ScanProgress
from tests.native_scan_fixture import NativeScanFixture, native_scene_fixture

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
LIMITS = ScanLimits(2_000_000, 30_000, 60_000, 100_000_000, chunk_bytes=64)


def envelope(value: str) -> dict[str, Any]:
    record = cast(dict[str, Any], json.loads(value))
    _ = json.dumps(record, allow_nan=False)
    return record


def success(value: str) -> dict[str, Any]:
    record = envelope(value)
    assert record["ok"], record
    return cast(dict[str, Any], record["result"])


def graph(workspace: BrowserWorkspace) -> dict[str, Any]:
    return success(workspace.call_json("getGraph"))


def payload(workspace: BrowserWorkspace, **overrides: Any) -> dict[str, Any]:
    return {
        "operationId": "source-1",
        "kind": "scan",
        "generation": workspace.workspace_generation,
        "token": graph(workspace)["token"],
        "filename": "independent.ply",
        **overrides,
    }


def promote(workspace: BrowserWorkspace, candidate: dict[str, Any]) -> dict[str, Any]:
    return success(
        workspace.promote_source_json(
            json.dumps(
                {
                    "operationId": candidate["operationId"],
                    "workspaceGeneration": candidate["workspaceGeneration"],
                }
            )
        )
    )


@pytest.fixture
def staged(tmp_path: Path) -> tuple[Path, NativeScanFixture]:
    fixture = native_scene_fixture()
    path = tmp_path / "owned-upload.ply"
    _ = path.write_bytes(fixture.raw)
    return path, fixture


def imported(staged: tuple[Path, NativeScanFixture]) -> BrowserWorkspace:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    _ = promote(workspace, candidate)
    return workspace


def test_private_candidate_does_not_publish_source_graph_or_display_before_promotion(
    staged: tuple[Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, fixture = staged
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    original_graph = workspace.graph
    original_workspace = workspace.workspace
    original_positions = workspace.display_positions
    before = graph(workspace)
    progress: list[ScanProgress] = []
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(payload(workspace)),
            str(path),
            progress=progress.append,
        )
    )
    assert graph(workspace) == before and workspace.graph is original_graph
    assert workspace.workspace is original_workspace
    assert workspace.display_positions is original_positions
    assert (
        workspace.source_mode == {"kind": "empty"} and workspace.source_identity == {}
    )
    assert candidate["source_mode"] == {"kind": "scan"}
    assert (
        candidate["workspaceGeneration"]
        == candidate["metadata"]["project_generation"]
        == candidate["graph"]["project_generation"]
        == 1
    )
    assert len(candidate["graph"]["recipe"]["nodes"]) == 1
    positions, indices = (
        workspace.candidate_display_positions,
        workspace.candidate_display_indices,
    )
    assert positions.dtype == np.float32 and indices.dtype == np.uint32
    assert not positions.flags.writeable and not indices.flags.writeable
    assert progress[-1].phase == "prepared"
    # The worker owns its staged path: Python closed it and never removes it.
    assert path.read_bytes() == fixture.raw
    path.unlink()
    monkeypatch.setattr(
        workspace, "_export_buffer", b"disposable previous-source export"
    )
    accepted = promote(workspace, candidate)
    assert workspace.export_buffer == b""
    assert accepted["project_generation"] == workspace.workspace_generation == 1
    assert (
        workspace.graph is not original_graph
        and workspace.workspace is not original_workspace
    )
    assert (
        workspace.display_positions is positions
        and workspace.display_indices is indices
    )
    np.testing.assert_array_equal(workspace.workspace.data.xyz, fixture.positions)
    np.testing.assert_array_equal(workspace.workspace.data.normals, fixture.normals)
    np.testing.assert_array_equal(workspace.workspace.data.triangles, fixture.triangles)
    np.testing.assert_allclose(
        workspace.workspace.data.weights, fixture.weights, rtol=2e-15
    )
    assert workspace.workspace.original_bytes == fixture.raw
    assert (
        candidate["source_identity"]["descriptor"]["source_sha256"]
        == hashlib.sha256(fixture.raw).hexdigest()
    )
    assert workspace.source_identity == candidate["source_identity"]
    metadata = success(workspace.call_json("loadWorkspace"))
    assert metadata["graph"] == accepted and metadata["source_mode"] == {"kind": "scan"}
    assert metadata["capabilities"]["source_import"]["limits"]["chunk_bytes"] == 64
    with pytest.raises(ScanImportError):
        _ = workspace.candidate_display_positions


def test_discard_is_idempotent_and_wrong_operation_cannot_release_candidate(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(workspace)
    _ = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    assert (
        envelope(workspace.discard_source_json("other"))["error"]["code"]
        == "unknown_operation"
    )
    assert workspace.candidate_display_positions.size > 0
    assert success(workspace.discard_source_json("source-1")) == before
    assert success(workspace.discard_source_json("source-1")) == before
    assert workspace.source_mode == {"kind": "empty"}
    assert staged[0].exists()


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"generation": True}, "stale_source"),
        ({"generation": 1}, "stale_source"),
        ({"token": "old"}, "stale_source"),
        ({"operationId": "../other"}, "invalid_source"),
        ({"kind": "unknown"}, "invalid_source"),
        ({"filename": ""}, "invalid_source"),
    ],
)
def test_invalid_cursor_or_request_never_allocates_candidate(
    staged: tuple[Path, NativeScanFixture],
    overrides: dict[str, Any],
    code: str,
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(workspace)
    response = envelope(
        workspace.prepare_source_json(
            json.dumps(payload(workspace, **overrides)), str(staged[0])
        )
    )
    assert response["error"]["code"] == code and graph(workspace) == before
    with pytest.raises(ScanImportError):
        _ = workspace.candidate_display_indices


@pytest.mark.parametrize(
    "phase", ["before", "reading", "vertices", "faces", "prepared"]
)
def test_cancel_at_shared_preparation_boundaries_preserves_current_source(
    staged: tuple[Path, NativeScanFixture],
    phase: str,
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(workspace)
    cancelled = phase == "before"

    def progress(update: ScanProgress) -> None:
        nonlocal cancelled
        cancelled = update.phase == phase

    response = envelope(
        workspace.prepare_source_json(
            json.dumps(payload(workspace)),
            str(staged[0]),
            progress=progress,
            cancelled=lambda: cancelled,
        )
    )
    assert response["error"]["code"] == "cancelled"
    assert graph(workspace) == before and workspace.source_mode == {"kind": "empty"}
    assert staged[0].exists()
    with pytest.raises(ScanImportError):
        _ = workspace.candidate_display_positions


def test_limit_and_invalid_source_fail_without_a_candidate(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    path, _ = staged
    workspace = BrowserWorkspace(
        None, scan_limits=ScanLimits(100, 1, 0, 1000, chunk_bytes=24)
    )
    before = graph(workspace)
    assert (
        envelope(
            workspace.prepare_source_json(json.dumps(payload(workspace)), str(path))
        )["error"]["code"]
        == "source_limit"
    )
    _ = path.write_bytes(b"unsupported input")
    assert not envelope(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(path))
    )["ok"]
    assert graph(workspace) == before
    with pytest.raises(ScanImportError):
        _ = workspace.candidate_display_positions


def test_candidate_quota_and_wrong_promotion_leave_accepted_state_untouched(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(workspace)
    candidate = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    assert (
        envelope(
            workspace.prepare_source_json(
                json.dumps(payload(workspace, operationId="second")), str(staged[0])
            )
        )["error"]["code"]
        == "source_busy"
    )
    for changes in (
        {"operationId": "other"},
        {"workspaceGeneration": True},
        {"workspaceGeneration": 2},
    ):
        response = envelope(
            workspace.promote_source_json(
                json.dumps(
                    {"operationId": "source-1", "workspaceGeneration": 1, **changes}
                )
            )
        )
        assert (
            response["error"]["code"] == "invalid_candidate"
            and graph(workspace) == before
        )
    assert (
        envelope(
            workspace.call_json(
                "replaceRecipe",
                json.dumps({"token": before["token"], "recipe": before["recipe"]}),
            )
        )["error"]["code"]
        == "source_busy"
    )
    _ = promote(workspace, candidate)
    assert (
        envelope(
            workspace.prepare_source_json(
                json.dumps(payload(workspace, operationId="second")), str(staged[0])
            )
        )["error"]["code"]
        == "source_exists"
    )


def test_empty_aba_generation_and_confirmed_reset_preserve_source_until_promotion(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    first = graph(workspace)
    empty = payload(workspace, kind="empty")
    assert (
        envelope(workspace.prepare_source_json(json.dumps(empty)))["error"]["code"]
        == "discard_required"
    )
    empty["confirmedDiscard"] = True
    candidate = success(workspace.prepare_source_json(json.dumps(empty)))
    assert graph(workspace) == first
    reset = promote(workspace, candidate)
    assert reset["token"] == first["token"] and reset["project_generation"] == 1
    assert (
        envelope(
            workspace.prepare_source_json(
                json.dumps({**payload(workspace), "generation": 0}), str(staged[0])
            )
        )["error"]["code"]
        == "stale_source"
    )
    candidate = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    _ = promote(workspace, candidate)
    assert workspace.workspace_generation == 2
    before = workspace.workspace
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(payload(workspace, kind="empty", confirmedDiscard=True))
        )
    )
    assert workspace.workspace is before and candidate["source_mode"] == {
        "kind": "empty"
    }
    _ = promote(workspace, candidate)
    assert workspace.workspace_generation == 3 and workspace.source_mode == {
        "kind": "empty"
    }


def test_force_reconstructs_private_source_identity_and_recipe_before_publication(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    original = imported(staged)
    recipe = graph(original)["recipe"]
    recipe["nodes"].append(
        {
            "id": "painted",
            "label": "Rows",
            "operation": "selection",
            "source": "scan",
            "ids": [0, 1, 2],
            "depth": "through_all",
        }
    )
    recipe["output"] = "painted"
    accepted = success(
        original.call_json(
            "replaceRecipe",
            json.dumps({"token": graph(original)["token"], "recipe": recipe}),
        )
    )
    fresh = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(fresh)
    candidate = success(
        fresh.prepare_source_json(
            json.dumps(payload(fresh, filename="renamed.ply")),
            str(staged[0]),
            recovery_generation=9,
            recovery_identity=original.source_identity,
            recovery_recipe=recipe,
        )
    )
    assert graph(fresh) == before and candidate["graph"]["recipe"] == accepted["recipe"]
    assert candidate["graph"]["token"] == accepted["token"]
    assert candidate["workspaceGeneration"] == 9
    _ = promote(fresh, candidate)
    assert (
        fresh.workspace_generation == 9
        and fresh.source_identity == original.source_identity
    )
    assert fresh.workspace.name == "renamed.ply"


@pytest.mark.parametrize(
    "failure", ["identity", "recipe", "used-runtime", "incomplete"]
)
def test_failed_reconstruction_preserves_current_and_exposes_no_candidate(
    staged: tuple[Path, NativeScanFixture], failure: str
) -> None:
    original = imported(staged)
    fresh = BrowserWorkspace(None, scan_limits=LIMITS)
    identity, recipe = original.source_identity, graph(original)["recipe"]
    if failure == "identity":
        identity["descriptor"]["interpretation_sha256"] = "0" * 64
    elif failure == "recipe":
        recipe["nodes"][0]["source_sha256"] = "0" * 64
    elif failure == "used-runtime":
        before = graph(fresh)
        _ = success(
            fresh.call_json(
                "replaceRecipe",
                json.dumps({"token": before["token"], "recipe": before["recipe"]}),
            )
        )
    before = graph(fresh)
    response = envelope(
        fresh.prepare_source_json(
            json.dumps(payload(fresh)),
            str(staged[0]),
            recovery_generation=7,
            recovery_identity=identity,
            recovery_recipe=None if failure == "incomplete" else recipe,
        )
    )
    assert not response["ok"] and graph(fresh) == before
    with pytest.raises(ScanImportError):
        _ = fresh.candidate_display_positions


def test_explicit_example_transition_and_force_keep_legacy_geometry(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = imported(staged)
    before = workspace.workspace
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(payload(workspace, kind="example", example="nozzle"))
        )
    )
    assert workspace.workspace is before
    assert candidate["source_mode"] == {"kind": "example", "example": "nozzle"}
    _ = promote(workspace, candidate)
    assert (
        workspace.source_mode == candidate["source_mode"]
        and workspace.workspace_generation == 2
    )
    legacy = BrowserWorkspace(EXAMPLE)
    assert workspace.source_identity == legacy.source_identity
    np.testing.assert_array_equal(workspace.workspace.local, legacy.workspace.local)
    fresh = BrowserWorkspace(None, scan_limits=LIMITS)
    restored = success(
        fresh.prepare_source_json(
            json.dumps(payload(fresh, kind="example", example="nozzle")),
            recovery_generation=2,
            recovery_identity=workspace.source_identity,
            recovery_recipe=graph(workspace)["recipe"],
        )
    )
    assert promote(fresh, restored)["token"] == graph(workspace)["token"]


@pytest.mark.parametrize("interrupt", [False, True])
def test_generic_evaluation_lease_and_cancel_recovery_use_core_graph_owner(
    staged: tuple[Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
    interrupt: bool,
) -> None:
    workspace = imported(staged)
    before = graph(workspace)
    original_source, original_positions = (
        workspace.workspace,
        workspace.display_positions,
    )
    held: list[ScanProject] = []
    original_evaluation = ScanProject.evaluation

    @contextmanager
    def observed(self: ScanProject) -> Generator[AcceptedScan]:
        with original_evaluation(self) as accepted:
            held.append(self)
            yield accepted

    monkeypatch.setattr(ScanProject, "evaluation", observed)
    workspace.checkpoint_operation()
    _ = success(
        workspace.prepare_evaluation_json(
            "evaluateGraph", json.dumps({"token": before["token"], "all_actions": True})
        )
    )
    project = held[0]
    with pytest.raises(ScanImportError) as busy:
        _ = project.reset(generation=project.accepted.generation, token=before["token"])
    assert busy.value.code == "source_busy"
    if interrupt:

        def interrupted(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise KeyboardInterrupt

        monkeypatch.setattr(workspace.graph, "evaluate", interrupted)
        assert (
            envelope(workspace.finish_evaluation_json())["error"]["code"]
            == "computation_cancelled"
        )
    else:
        _ = success(workspace.finish_evaluation_json())
        assert graph(workspace)["states"] == {"scan": "ready"}
    accepted_before = project.accepted
    restored = success(workspace.restore_cancelled_json(json.dumps(before["recipe"])))
    assert restored["states"] == {"scan": "unevaluated"} and restored["results"] == {}
    assert (
        workspace.graph is project.accepted.graph
        and project.accepted.graph is not accepted_before.graph
    )
    assert (
        workspace.workspace is original_source
        and workspace.display_positions is original_positions
    )
    assert project.accepted.positions is accepted_before.positions
    assert project.accepted.indices is accepted_before.indices
    assert restored["project_generation"] == 1


def test_core_recovery_rejects_stale_and_invalid_recipe_atomically(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    project = ScanProject()
    empty = project.accepted
    with staged[0].open("rb") as stream:
        candidate = project.prepare_add(
            stream,
            "source.ply",
            LIMITS,
            generation=0,
            token=str(empty.graph.snapshot()["token"]),
        )
    accepted = project.commit(candidate)
    checkpoint = accepted.graph.failure_checkpoint()
    recipe = checkpoint.recipe.model_copy(deep=True)
    for generation in (0, 2):
        with pytest.raises(ScanImportError):
            _ = project.recover_graph(
                generation=generation,
                token=checkpoint.token,
                recipe=recipe,
                checkpoint=checkpoint,
            )
        assert project.accepted is accepted
    invalid = recipe.model_dump(mode="json")
    invalid["nodes"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        _ = project.recover_graph(
            generation=1,
            token=checkpoint.token,
            recipe=Recipe.model_validate(invalid),
            checkpoint=checkpoint,
        )
    assert project.accepted is accepted


def test_closed_facade_releases_candidate_and_rejects_late_promotion(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    _ = workspace.call_json("close")
    assert (
        envelope(
            workspace.promote_source_json(
                json.dumps(
                    {"operationId": candidate["operationId"], "workspaceGeneration": 1}
                )
            )
        )["error"]["code"]
        == "backend_closed"
    )
    with pytest.raises(ValueError):
        _ = workspace.candidate_display_positions


def test_stale_private_candidate_cannot_overwrite_a_graph_edit(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = success(
        workspace.prepare_source_json(json.dumps(payload(workspace)), str(staged[0]))
    )
    before = graph(workspace)
    recipe = before["recipe"]
    recipe["groups"].append({"id": "authored", "label": "Keep this edit"})
    _ = workspace.graph.replace(Recipe.model_validate(recipe), before["token"])
    edited = graph(workspace)
    response = envelope(
        workspace.promote_source_json(
            json.dumps(
                {"operationId": candidate["operationId"], "workspaceGeneration": 1}
            )
        )
    )
    assert response["error"]["code"] == "stale_source"
    assert graph(workspace) == edited and workspace.source_mode == {"kind": "empty"}
    assert success(workspace.discard_source_json("source-1")) == edited


def test_progress_callback_cannot_reenter_import_or_mutate_current_graph(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    request = payload(workspace)
    before = graph(workspace)
    responses: list[dict[str, Any]] = []

    def progress(_update: ScanProgress) -> None:
        if responses:
            return
        responses.append(
            envelope(workspace.prepare_source_json(json.dumps(request), str(staged[0])))
        )
        responses.append(
            envelope(
                workspace.call_json(
                    "replaceRecipe",
                    json.dumps({"token": before["token"], "recipe": before["recipe"]}),
                )
            )
        )

    _ = success(
        workspace.prepare_source_json(
            json.dumps(request), str(staged[0]), progress=progress
        )
    )
    assert [response["error"]["code"] for response in responses] == [
        "source_busy",
        "source_busy",
    ]
    assert graph(workspace) == before


def test_unavailable_staging_and_unconfigured_limits_are_typed_without_candidate(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    for workspace, path, code in (
        (BrowserWorkspace(None), staged[0], "unsupported_capability"),
        (
            BrowserWorkspace(None, scan_limits=LIMITS),
            staged[0].with_name("missing.ply"),
            "source_unavailable",
        ),
    ):
        before = graph(workspace)
        response = envelope(
            workspace.prepare_source_json(json.dumps(payload(workspace)), str(path))
        )
        assert response["error"]["code"] == code
        assert graph(workspace) == before
        with pytest.raises(ScanImportError):
            _ = workspace.candidate_display_positions


def test_generic_cancel_retains_failed_nodes_and_cannot_restore_across_source_boundary(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = imported(staged)
    initial = graph(workspace)
    recipe = initial["recipe"]
    recipe["nodes"].extend(
        [
            {
                "id": "empty_rows",
                "label": "Empty",
                "operation": "selection",
                "source": "scan",
                "ids": [],
                "depth": "through_all",
            },
            {
                "id": "plane",
                "label": "Plane",
                "operation": "fit",
                "selections": ["empty_rows"],
                "kind": "plane",
            },
        ]
    )
    recipe["output"] = "plane"
    edited = success(
        workspace.call_json(
            "replaceRecipe", json.dumps({"token": initial["token"], "recipe": recipe})
        )
    )
    _ = success(
        workspace.prepare_evaluation_json(
            "evaluateGraph", json.dumps({"token": edited["token"], "all_actions": True})
        )
    )
    failed = success(workspace.finish_evaluation_json())
    assert failed["states"]["plane"] == "failed"
    workspace.checkpoint_operation()
    restored = success(workspace.restore_cancelled_json(json.dumps(failed["recipe"])))
    assert restored["states"]["plane"] == "failed"
    assert restored["errors"] == failed["errors"] and restored["results"] == {}
    workspace.checkpoint_operation()
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(payload(workspace, kind="empty", confirmedDiscard=True))
        )
    )
    _ = promote(workspace, candidate)
    assert (
        envelope(workspace.restore_cancelled_json(json.dumps(failed["recipe"])))[
            "error"
        ]["code"]
        == "checkpoint_missing"
    )
    assert (
        workspace.source_mode == {"kind": "empty"}
        and graph(workspace)["recipe"]["nodes"] == []
    )


def test_force_recovers_explicit_empty_mode_at_host_generation() -> None:
    workspace = BrowserWorkspace(EXAMPLE, scan_limits=LIMITS)
    before = graph(workspace)
    empty_recipe = {"schema_version": 2, "nodes": [], "output": None}
    candidate = success(
        workspace.prepare_source_json(
            json.dumps(payload(workspace, kind="empty")),
            recovery_generation=12,
            recovery_identity={},
            recovery_recipe=empty_recipe,
        )
    )
    assert graph(workspace) == before and workspace.source_mode["kind"] == "example"
    assert candidate["source_mode"] == {"kind": "empty"}
    _ = promote(workspace, candidate)
    assert workspace.workspace_generation == 12 and workspace.source_identity == {}


def test_prepare_running_joined_and_ready_states_all_bind_current_source_generation(
    staged: tuple[Path, NativeScanFixture],
) -> None:
    workspace = imported(staged)
    token = graph(workspace)["token"]
    request = json.dumps({"token": token, "all_actions": True})
    running = success(workspace.prepare_evaluation_json("ensureGraph", request))
    assert running["run"] and running["state"]["evaluation_running"]
    assert running["state"]["project_generation"] == 1
    joined = success(workspace.prepare_evaluation_json("ensureGraph", request))
    assert not joined["run"] and joined["state"]["evaluation_running"]
    assert joined["state"]["project_generation"] == 1
    assert success(workspace.finish_evaluation_json())["project_generation"] == 1
    ready = success(workspace.prepare_evaluation_json("ensureGraph", request))
    assert not ready["run"] and not ready["state"]["evaluation_running"]
    assert ready["state"]["project_generation"] == 1
    retry = success(workspace.prepare_evaluation_json("evaluateGraph", request))
    assert retry["run"] and retry["state"]["project_generation"] == 1
    _ = success(workspace.finish_evaluation_json())
