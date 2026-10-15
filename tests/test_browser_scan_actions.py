"""Saved definitions use the same private worker source/adoption boundary."""

from __future__ import annotations

import json
import struct
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import Recipe
from experiments.scan_actions import inspect_actions
from experiments.scan_workspace import ScanLimits
from tests.native_scan_fixture import NativeScanFixture, native_scene_fixture

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
LIMITS = ScanLimits(2_000_000, 30_000, 60_000, 100_000_000, chunk_bytes=64)


def unpack(value: str) -> dict[str, Any]:
    envelope = cast(dict[str, Any], json.loads(value))
    _ = json.dumps(envelope, allow_nan=False)
    assert envelope["ok"], envelope
    return cast(dict[str, Any], envelope["result"])


def rejected(value: str, code: str) -> None:
    envelope = json.loads(value)
    assert not envelope["ok"], envelope
    assert envelope["error"]["code"] == code, envelope


def graph(workspace: BrowserWorkspace) -> dict[str, Any]:
    return unpack(workspace.call_json("getGraph"))


def promote(workspace: BrowserWorkspace, candidate: dict[str, Any]) -> dict[str, Any]:
    return unpack(
        workspace.promote_source_json(
            json.dumps(
                {
                    "operationId": candidate["operationId"],
                    "workspaceGeneration": candidate["workspaceGeneration"],
                }
            )
        )
    )


def intent(
    workspace: BrowserWorkspace, recipe: dict[str, Any], **changes: Any
) -> dict[str, Any]:
    return {
        "operationId": "actions-1",
        "purpose": "open-actions",
        "generation": workspace.workspace_generation,
        "token": graph(workspace)["token"],
        "recipe": recipe,
        "confirmedDiscard": True,
        **changes,
    }


def inspect_payload(payload: dict[str, Any]) -> str:
    return json.dumps({key: payload[key] for key in ("generation", "token", "recipe")})


@pytest.fixture
def scene(tmp_path: Path) -> tuple[BrowserWorkspace, Path, NativeScanFixture]:
    fixture = native_scene_fixture()
    path = tmp_path / "owned.ply"
    _ = path.write_bytes(fixture.raw)
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    candidate = unpack(
        workspace.prepare_source_json(
            json.dumps(
                {
                    "operationId": "initial-source",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(workspace)["token"],
                    "filename": "original.ply",
                }
            ),
            str(path),
        )
    )
    _ = promote(workspace, candidate)
    return workspace, path, fixture


def authored(workspace: BrowserWorkspace, *, collision: bool = False) -> dict[str, Any]:
    recipe = deepcopy(graph(workspace)["recipe"])
    recipe["nodes"].extend(
        {
            "id": name,
            "operation": "point",
            "label": "Same label" if collision else name,
            "initial_coordinates": [float(index), 2.0, 3.0],
        }
        for index, name in enumerate(("first-point", "second-point"))
    )
    return recipe


def test_inspection_and_preflight_are_native_shaped_read_only_and_keep_raw_intent(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _, _ = scene
    raw = authored(workspace, collision=True)
    payload = intent(workspace, raw)
    before, original, original_graph = graph(workspace), deepcopy(raw), workspace.graph

    def no_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Inspection must not read source files")

    monkeypatch.setattr(Path, "open", no_read)
    inspected = unpack(workspace.call_json("inspectActions", inspect_payload(payload)))
    assert inspected == {
        "generation": workspace.workspace_generation,
        "token": before["token"],
        **inspect_actions(raw, workspace.workspace).json(),
    }
    assert len(inspected["import_warnings"]) == 1
    preflight = unpack(workspace.preflight_actions_json(json.dumps(payload)))
    assert preflight == {
        **inspected,
        "purpose": "open-actions",
        "sourceKind": "generic",
        "requiresCad": False,
    }
    assert graph(workspace) == before and workspace.graph is original_graph
    assert raw == original
    assert unpack(workspace.call_json("loadWorkspace"))["import_warnings"] == []
    inspected["recipe"]["nodes"].clear()
    preflight["import_warnings"][0]["new_name"] = "not accepted"
    assert graph(workspace) == before


def test_matching_open_reuses_only_immutable_source_and_publishes_fresh_definitions(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, _, _ = scene
    raw = authored(workspace, collision=True)
    payload = intent(workspace, raw)
    original_graph, original_source = workspace.graph, workspace.workspace
    positions, indices, before = (
        workspace.display_positions,
        workspace.display_indices,
        graph(workspace),
    )

    def no_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Matching actions must not reopen source bytes")

    monkeypatch.setattr(Path, "open", no_read)
    candidate = unpack(workspace.prepare_source_json(json.dumps(payload)))
    assert graph(workspace) == before and workspace.graph is original_graph
    assert candidate["purpose"] == "open-actions"
    assert candidate["sourceKind"] == "generic" and not candidate["uploadRequired"]
    assert candidate["import_warning_count"] == 1
    assert candidate["source_mode"] == {"kind": "scan"}
    assert set(candidate["graph"]["states"].values()) == {"unevaluated"}
    assert candidate["graph"]["results"] == {}
    expected = inspect_actions(raw, original_source).json()
    assert candidate["graph"]["recipe"] == expected["recipe"]
    assert candidate["metadata"]["import_warnings"] == expected["import_warnings"]
    raw["nodes"][-1]["initial_coordinates"][0] = 99.0
    candidate["metadata"]["import_warnings"][0]["new_name"] = "caller mutation"
    published = promote(workspace, candidate)
    assert workspace.workspace_generation == before["project_generation"] + 1
    assert (
        workspace.graph is not original_graph and workspace.workspace is original_source
    )
    assert (
        workspace.display_positions is positions
        and workspace.display_indices is indices
    )
    assert published["recipe"] == expected["recipe"]
    metadata = unpack(workspace.call_json("loadWorkspace"))
    assert metadata["import_warnings"] == expected["import_warnings"]
    prepared = unpack(
        workspace.prepare_evaluation_json(
            "evaluateGraph",
            json.dumps({"token": published["token"], "all_actions": True}),
        )
    )
    assert prepared["run"]
    evaluated = unpack(workspace.finish_evaluation_json())
    assert evaluated["required_failures"] == []
    assert evaluated["results"]["second-point"]["coordinates"] == [1.0, 2.0, 3.0]


def test_located_renamed_exact_source_and_empty_open_each_advance_once(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
) -> None:
    original, path, fixture = scene
    raw = authored(original)
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    before = graph(workspace)
    payload = intent(
        workspace,
        raw,
        source={"filename": "different-name.ply", "size": len(fixture.raw)},
    )
    preflight = unpack(workspace.preflight_actions_json(json.dumps(payload)))
    assert preflight["classification"] == "locate" and preflight["uploadRequired"]
    candidate = unpack(workspace.prepare_source_json(json.dumps(payload), str(path)))
    assert candidate["uploadRequired"] and candidate["sourceKind"] == "generic"
    assert graph(workspace) == before
    _ = promote(workspace, candidate)
    assert workspace.workspace_generation == 1
    assert workspace.workspace is not original.workspace
    for name in ("local",):
        np.testing.assert_array_equal(
            getattr(workspace.workspace, name), getattr(original.workspace, name)
        )
    np.testing.assert_array_equal(
        workspace.workspace.data.normals, original.workspace.data.normals
    )
    np.testing.assert_array_equal(
        workspace.workspace.data.weights, original.workspace.data.weights
    )
    assert graph(workspace)["recipe"] == Recipe.model_validate(raw).model_dump(
        mode="json"
    )
    assert (
        unpack(workspace.call_json("loadWorkspace"))["source"]["filename"]
        == "different-name.ply"
    )
    empty = {"schema_version": 2, "nodes": [], "output": None}
    candidate = unpack(
        workspace.prepare_source_json(json.dumps(intent(workspace, empty)))
    )
    assert candidate["sourceKind"] == "empty" and candidate["source_mode"] == {
        "kind": "empty"
    }
    assert not candidate["uploadRequired"]
    _ = promote(workspace, candidate)
    assert workspace.workspace_generation == 2
    assert graph(workspace)["recipe"] == Recipe.model_validate(empty).model_dump(
        mode="json"
    )
    assert unpack(workspace.call_json("loadWorkspace"))["import_warnings"] == []


@pytest.mark.parametrize(
    "change,code",
    [
        ({"generation": 0}, "stale_source"),
        ({"generation": True}, "stale_source"),
        ({"token": "stale"}, "stale_source"),
        ({"confirmedDiscard": False}, "discard_required"),
        ({"source": {"filename": "unexpected.ply", "size": 1}}, "unexpected_source"),
        ({"source": None}, "unexpected_source"),
        ({"operationId": "../bad"}, "invalid_source"),
    ],
)
def test_preflight_rejects_cursor_discard_and_unexpected_upload_without_reads(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    code: str,
) -> None:
    workspace, _, _ = scene
    before = graph(workspace)
    payload = intent(workspace, authored(workspace), **change)

    def no_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Rejected actions must not read source bytes")

    monkeypatch.setattr(Path, "open", no_read)
    rejected(workspace.preflight_actions_json(json.dumps(payload)), code)
    rejected(workspace.prepare_source_json(json.dumps(payload)), code)
    assert graph(workspace) == before


@pytest.mark.parametrize(
    "source,code",
    [
        (None, "source_required"),
        ({"filename": "located.ply", "size": 1}, "source_mismatch"),
        ({"filename": "located.ply", "size": 2_000_001}, "source_limit"),
        ({"filename": "located.ply", "size": True}, "invalid_source"),
        ({"filename": "", "size": 1}, "invalid_source"),
    ],
)
def test_locate_preflight_rejects_wrong_declared_length_before_source_access(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, Any] | None,
    code: str,
) -> None:
    original, _, _ = scene
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    payload = intent(workspace, authored(original))
    if source is not None:
        payload["source"] = source

    def no_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Preflight must precede source access")

    monkeypatch.setattr(Path, "open", no_read)
    rejected(workspace.preflight_actions_json(json.dumps(payload)), code)
    rejected(workspace.prepare_source_json(json.dumps(payload), "/unread.ply"), code)
    assert workspace.workspace_generation == 0


def test_unsupported_schema_interpretation_and_cad_are_rejected_before_bytes(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, _, fixture = scene
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    raw = authored(original)
    source = {"filename": "located.ply", "size": len(fixture.raw)}

    def no_read(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Invalid definitions must not read source bytes")

    monkeypatch.setattr(Path, "open", no_read)
    invalid = deepcopy(raw)
    invalid["nodes"][0]["descriptor"]["interpretation_sha256"] = "0" * 64
    rejected(
        workspace.preflight_actions_json(
            json.dumps(intent(workspace, invalid, source=source))
        ),
        "unsupported_interpretation",
    )
    invalid = deepcopy(raw)
    invalid["nodes"][0]["descriptor"]["units"] = "meters"
    rejected(
        workspace.preflight_actions_json(
            json.dumps(intent(workspace, invalid, source=source))
        ),
        "invalid_actions",
    )
    cad = deepcopy(raw)
    cad["nodes"].append(
        {
            "id": "body",
            "operation": "body",
            "label": "Body",
            "faces": ["not-yet-resolved"],
        }
    )
    rejected(
        workspace.preflight_actions_json(
            json.dumps(intent(workspace, cad, source=source))
        ),
        "unsupported_capability",
    )
    available = BrowserWorkspace(None, scan_limits=LIMITS, cad_available=True)
    payload = intent(available, cad, source=source)
    assert unpack(available.preflight_actions_json(json.dumps(payload)))["requiresCad"]
    rejected(
        available.prepare_source_json(json.dumps(payload), "/unread.ply"),
        "unsupported_capability",
    )
    assert graph(available)["recipe"]["nodes"] == []


def test_same_length_wrong_bytes_and_cancel_never_publish(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture], tmp_path: Path
) -> None:
    original, path, fixture = scene
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    payload = intent(
        workspace,
        authored(original),
        source={"filename": "original.ply", "size": len(fixture.raw)},
    )
    wrong = bytearray(fixture.raw)
    offset = wrong.index(b"end_header\n") + len(b"end_header\n")
    struct.pack_into(
        "<f", wrong, offset, struct.unpack_from("<f", wrong, offset)[0] + 0.25
    )
    wrong_path = tmp_path / "wrong.ply"
    _ = wrong_path.write_bytes(wrong)
    rejected(
        workspace.prepare_source_json(json.dumps(payload), str(wrong_path)),
        "source_mismatch",
    )
    rejected(
        workspace.prepare_source_json(
            json.dumps(payload), str(path), cancelled=lambda: True
        ),
        "cancelled",
    )
    assert (
        workspace.workspace_generation == 0
        and graph(workspace)["recipe"]["nodes"] == []
    )
    assert path.read_bytes() == fixture.raw


def test_cancel_during_located_source_preparation_and_untrusted_warning_fields(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
) -> None:
    original, path, fixture = scene
    workspace = BrowserWorkspace(None, scan_limits=LIMITS)
    payload = intent(
        workspace,
        authored(original),
        source={"filename": "located.ply", "size": len(fixture.raw)},
    )
    cancel = False
    phases: list[str] = []

    def progress(value: Any) -> None:
        nonlocal cancel
        phases.append(value.phase)
        if value.phase == "vertices":
            cancel = True

    rejected(
        workspace.prepare_source_json(
            json.dumps(payload), str(path), progress=progress, cancelled=lambda: cancel
        ),
        "cancelled",
    )
    assert "vertices" in phases
    assert workspace.workspace_generation == 0
    assert graph(workspace)["recipe"]["nodes"] == []
    assert unpack(workspace.call_json("loadWorkspace"))["import_warnings"] == []
    payload["import_warnings"] = [{"id": "forged"}]
    rejected(workspace.preflight_actions_json(json.dumps(payload)), "validation_error")
    rejected(
        workspace.prepare_source_json(json.dumps(payload), str(path)),
        "validation_error",
    )


def test_candidate_and_active_evaluation_guards_and_candidate_revision_aba(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
) -> None:
    workspace, _, _ = scene
    payload = intent(workspace, authored(workspace))
    candidate = unpack(workspace.prepare_source_json(json.dumps(payload)))
    rejected(workspace.preflight_actions_json(json.dumps(payload)), "source_busy")
    rejected(workspace.prepare_source_json(json.dumps(payload)), "source_busy")
    # The private graph is not a public API, but mutation of that object must
    # still be detected even if its authored token returns to the original.
    private = workspace._candidate  # pyright: ignore[reportPrivateUsage]
    assert private is not None
    original = private.source.graph.snapshot()
    _ = private.source.graph.replace(
        Recipe.model_validate(original["recipe"]), str(original["token"])
    )
    rejected(
        workspace.promote_source_json(
            json.dumps(
                {
                    "operationId": candidate["operationId"],
                    "workspaceGeneration": candidate["workspaceGeneration"],
                }
            )
        ),
        "invalid_candidate",
    )
    _ = unpack(workspace.discard_source_json(candidate["operationId"]))
    prepared = unpack(
        workspace.prepare_evaluation_json(
            "evaluateGraph",
            json.dumps({"token": graph(workspace)["token"], "all_actions": True}),
        )
    )
    assert prepared["run"]
    rejected(workspace.preflight_actions_json(json.dumps(payload)), "source_busy")
    _ = unpack(workspace.finish_evaluation_json())


def test_explicit_matching_legacy_open_keeps_example_restore_authority() -> None:
    workspace = BrowserWorkspace(EXAMPLE, scan_limits=LIMITS)
    raw, restore = (
        authored(workspace, collision=True),
        workspace.example_recipe.model_dump(mode="json"),
    )
    previous, positions = workspace.workspace, workspace.display_positions
    payload = intent(workspace, raw)
    inspection = unpack(workspace.preflight_actions_json(json.dumps(payload)))
    assert inspection["sourceKind"] == "legacy" and not inspection["uploadRequired"]
    candidate = unpack(workspace.prepare_source_json(json.dumps(payload)))
    assert candidate["source_mode"] == {"kind": "example", "example": "nozzle"}
    _ = promote(workspace, candidate)
    assert workspace.workspace is previous and workspace.display_positions is positions
    assert unpack(workspace.call_json("getExampleRecipe")) == restore
    assert workspace.workspace_generation == 1
    assert len(unpack(workspace.call_json("loadWorkspace"))["import_warnings"]) == 1
    empty = BrowserWorkspace(None, scan_limits=LIMITS)
    rejected(
        empty.call_json("inspectActions", inspect_payload(intent(empty, raw))),
        "incompatible_legacy",
    )


def test_warning_checkpoint_survives_normalized_force_and_cancel_reconstruction(
    scene: tuple[BrowserWorkspace, Path, NativeScanFixture],
) -> None:
    workspace, path, _ = scene
    candidate = unpack(
        workspace.prepare_source_json(
            json.dumps(intent(workspace, authored(workspace, collision=True)))
        )
    )
    accepted = promote(workspace, candidate)
    metadata = unpack(workspace.call_json("loadWorkspace"))
    warnings = metadata["import_warnings"]
    assert len(warnings) == 1
    fresh = BrowserWorkspace(None, scan_limits=LIMITS)
    recovered = unpack(
        fresh.prepare_source_json(
            json.dumps(
                {
                    "operationId": "force-1",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(fresh)["token"],
                    "filename": "retained.ply",
                }
            ),
            str(path),
            recovery_generation=workspace.workspace_generation,
            recovery_identity=workspace.source_identity,
            recovery_recipe=accepted["recipe"],
            recovery_import_warnings=warnings,
        )
    )
    assert recovered["metadata"]["import_warnings"] == warnings
    _ = promote(fresh, recovered)
    assert fresh.workspace_generation == workspace.workspace_generation
    assert unpack(fresh.call_json("loadWorkspace"))["import_warnings"] == warnings
    fresh.checkpoint_operation()
    _ = unpack(fresh.restore_cancelled_json(json.dumps(accepted["recipe"])))
    assert unpack(fresh.call_json("loadWorkspace"))["import_warnings"] == warnings
    invalid = BrowserWorkspace(None, scan_limits=LIMITS)
    rejected(
        invalid.prepare_source_json(
            json.dumps(
                {
                    "operationId": "force-2",
                    "kind": "scan",
                    "generation": 0,
                    "token": graph(invalid)["token"],
                    "filename": "retained.ply",
                }
            ),
            str(path),
            recovery_generation=1,
            recovery_identity=workspace.source_identity,
            recovery_recipe=accepted["recipe"],
            recovery_import_warnings=cast(Any, [{"warning": "untrusted schema"}]),
        ),
        "invalid_recovery",
    )
