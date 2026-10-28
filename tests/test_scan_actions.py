"""Descriptor-first saved actions and atomic whole-project preparation."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast, override

import numpy as np
import pytest

from experiments.feature_graph import Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_actions import inspect_actions, preflight_actions
from experiments.scan_project import (
    AcceptedScan,
    PreparedProject,
    PreparedScan,
    ScanProject,
)
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    Workspace,
)
from tests.native_scan_fixture import (
    NativeScanFixture,
    native_grid_fixture,
    native_scene_fixture,
)
from tests.scan_source_fixture import read_source_bytes

LIMITS = ScanLimits(2_000_000, 30_000, 60_000, 100_000_000, chunk_bytes=64)
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"


def token(project: ScanProject) -> str:
    return str(project.accepted.graph.snapshot()["token"])


def admitted(
    fixture: NativeScanFixture | None = None, filename: str = "saved.ply"
) -> ScanProject:
    project = ScanProject()
    data = fixture or native_scene_fixture()
    candidate = project.prepare_add(
        io.BytesIO(data.raw), filename, LIMITS, generation=0, token=token(project)
    )
    _ = project.commit(candidate)
    return project


def actions(accepted: AcceptedScan, *, collision: bool = False) -> dict[str, Any]:
    recipe = cast(dict[str, Any], accepted.graph.snapshot()["recipe"])
    recipe["nodes"].extend(
        [
            {
                "id": "paint",
                "label": recipe["nodes"][0]["label"] if collision else "Painted rows",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(72, 84)),
                "depth": "through_all",
            },
            {
                "id": "plane",
                "label": "Plane",
                "operation": "fit",
                "selections": ["paint"],
                "kind": "plane",
            },
        ]
    )
    recipe["output"] = "plane"
    return recipe


def prepare(project: ScanProject, recipe: object, **kwargs: Any) -> PreparedProject:
    return project.prepare_open(
        recipe,
        generation=project.accepted.generation,
        token=token(project),
        confirmed_discard=True,
        **kwargs,
    )


@pytest.fixture
def scene() -> tuple[NativeScanFixture, ScanProject, dict[str, Any]]:
    fixture = native_scene_fixture()
    project = admitted(fixture)
    return fixture, project, actions(project.accepted)


def test_preflight_normalizes_only_display_names_without_changing_source_or_ids(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, project, _ = scene
    raw = actions(project.accepted, collision=True)
    original = deepcopy(raw)
    result = preflight_actions(raw)
    assert raw == original and result.kind == "generic"
    assert result.recipe.nodes[1].label != raw["nodes"][1]["label"]
    assert result.warnings == (
        {
            "id": "paint",
            "old_name": "saved.ply",
            "new_name": "saved.ply selection",
            "kind": "feature",
        },
    )
    assert result.recipe.nodes[0].model_dump(mode="json") == raw["nodes"][0]
    assert result.recipe.nodes[1].id == "paint"
    inspected = inspect_actions(raw, project.accepted.workspace).json()
    assert (
        inspected["classification"] == "matching"
        and inspected["uploadRequired"] is False
    )
    assert inspected["import_warnings"] == list(result.warnings)
    _ = json.dumps(inspected, allow_nan=False)


def test_matching_uses_actual_interpretation_and_allows_renamed_exact_bytes(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    fixture, project, raw = scene
    renamed = admitted(fixture, "moved-and-renamed.ply")
    assert inspect_actions(raw, renamed.accepted.workspace).disposition == "matching"
    assert inspect_actions(raw, ScanWorkspace.empty()).disposition == "locate"
    other = admitted(native_grid_fixture(4, 3), "saved.ply")
    assert inspect_actions(raw, other.accepted.workspace).disposition == "locate"
    assert project.accepted.workspace.source_descriptor is not None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("interpretation_sha256", "0" * 64, "unsupported_interpretation"),
        ("profile", "ascii-ply", "invalid_actions"),
        ("version", "unknown-v9", "invalid_actions"),
        ("units", "meters", "invalid_actions"),
        ("source_bytes", True, "invalid_actions"),
        ("vertices", 0, "invalid_actions"),
        ("source_sha256", "1" * 64, "invalid_source_binding"),
    ],
)
def test_descriptor_rejection_precedes_workspace_inspection_or_source_io(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
    field: str,
    value: object,
    code: str,
) -> None:
    raw = deepcopy(scene[2])
    raw["nodes"][0]["descriptor"][field] = value
    with pytest.raises(ScanImportError) as rejected:
        _ = inspect_actions(raw, cast(Workspace, object()))
    assert rejected.value.code == code and len(str(rejected.value)) <= 1200


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "2" * 64),
        ("reference_sha256", "3" * 64),
        ("source_sha256", "not-a-hash"),
    ],
)
def test_source_and_descriptor_must_agree_before_lookup(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]], field: str, value: str
) -> None:
    raw = deepcopy(scene[2])
    raw["nodes"][0][field] = value
    with pytest.raises(ScanImportError) as rejected:
        _ = preflight_actions(raw)
    assert rejected.value.code == "invalid_source_binding"


def test_empty_unbound_and_multiple_source_classification(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    empty = {
        "schema_version": 2,
        "nodes": [],
        "output": None,
        "groups": [{"id": "empty_group", "label": "Empty group"}],
    }
    assert preflight_actions(empty).kind == "empty"
    assert inspect_actions(empty, scene[1].accepted.workspace).disposition == "empty"
    for raw in (
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": "point",
                    "label": "Point",
                    "operation": "point",
                    "initial_parameters": [0, 0, 0],
                }
            ],
            "output": "point",
        },
        {
            **scene[2],
            "nodes": [
                scene[2]["nodes"][0],
                {**scene[2]["nodes"][0], "id": "other_source"},
            ],
        },
    ):
        with pytest.raises(ScanImportError):
            _ = preflight_actions(raw)


def test_legacy_recognition_requires_explicit_compatible_workspace() -> None:
    legacy = NozzleWorkspace(EXAMPLE)
    raw = {
        "schema_version": 2,
        "nodes": [
            {
                "id": "legacy",
                "label": "Example source",
                "operation": "source",
                "source_sha256": legacy.source_sha256,
                "reference_sha256": legacy.reference_sha256,
            }
        ],
        "output": "legacy",
    }
    assert preflight_actions(raw).kind == "legacy"
    assert inspect_actions(raw, legacy).disposition == "matching"
    for workspace in (ScanWorkspace.empty(), admitted().accepted.workspace):
        with pytest.raises(ScanImportError) as rejected:
            _ = inspect_actions(raw, workspace)
        assert rejected.value.code == "incompatible_legacy"


def test_matching_open_reuses_immutable_evidence_but_installs_fresh_dirty_definitions(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, project, raw = scene
    original = project.accepted
    _ = original.graph.evaluate(token(project), all_actions=True)
    before = original.graph.snapshot()
    candidate = prepare(project, raw)
    assert project.accepted is original and original.graph.snapshot() == before
    assert candidate.accepted.workspace is original.workspace
    assert (
        candidate.accepted.positions is original.positions
        and candidate.accepted.indices is original.indices
    )
    assert candidate.accepted.graph is not original.graph
    state = candidate.accepted.graph.snapshot()
    assert set(cast(dict[str, str], state["states"]).values()) == {"unevaluated"}
    assert state["results"] == {}
    accepted = project.commit_open(candidate)
    assert accepted.generation == 2 and project.accepted is accepted
    evaluated = accepted.graph.evaluate(token(project), all_actions=True)
    plane = cast(dict[str, Any], evaluated["results"])["plane"]
    np.testing.assert_allclose(plane["parameters"], [1, 0, 0, 8], atol=1e-12)


def test_private_owner_can_reuse_matching_snapshot_and_owns_warnings_for_recovery(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, public, _ = scene
    previous = public.accepted
    raw = actions(previous, collision=True)
    private = ScanProject()
    candidate = prepare(private, raw, matching=previous)
    accepted = private.commit_open(candidate)
    assert public.accepted is previous and accepted.generation == 1
    assert (
        accepted.workspace is previous.workspace
        and accepted.positions is previous.positions
    )
    assert accepted.import_warnings[0]["id"] == "paint"
    assert accepted.graph.snapshot()["results"] == {}
    assert private.accepted is accepted


def test_different_source_plus_actions_remains_private_until_single_commit(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    fixture, target, raw = scene
    public = admitted(native_grid_fixture(4, 3), "other.ply")
    previous = public.accepted
    seen: list[str] = []

    def progress(update: ScanProgress) -> None:
        assert public.accepted is previous
        seen.append(update.phase)

    candidate = prepare(
        public,
        raw,
        stream=io.BytesIO(fixture.raw),
        filename="renamed.ply",
        limits=LIMITS,
        progress=progress,
    )
    assert "prepared" in seen and public.accepted is previous
    assert read_source_bytes(candidate.accepted.workspace) == fixture.raw
    assert (
        candidate.accepted.graph.snapshot()["recipe"]
        == target.accepted.graph.validate(Recipe.model_validate(raw)).model_dump()
    )
    accepted = public.commit_open(candidate)
    assert accepted.generation == previous.generation + 1
    assert (
        accepted.workspace.name == "renamed.ply"
        and accepted.graph.snapshot()["results"] == {}
    )


@pytest.mark.parametrize(
    "failure", ["missing", "wrong", "invalid-recipe", "resource", "capability"]
)
def test_failed_open_preserves_exact_previous_source_graph_arrays(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]], failure: str
) -> None:
    fixture, _, raw = scene
    public = admitted(native_grid_fixture(4, 3))
    before = public.accepted
    original = before.graph.snapshot()
    options: dict[str, Any] = {
        "stream": io.BytesIO(fixture.raw),
        "filename": "saved.ply",
        "limits": LIMITS,
    }
    if failure == "missing":
        options = {}
    elif failure == "wrong":
        options["stream"] = io.BytesIO(native_grid_fixture(3, 4).raw)
    elif failure == "invalid-recipe":
        raw = deepcopy(raw)
        raw["nodes"][1]["ids"] = [99999]
    elif failure == "resource":
        options["limits"] = ScanLimits(100, 1, 0, 1000, chunk_bytes=24)
    elif failure == "capability":

        def unsupported(_recipe: Recipe) -> None:
            raise ScanImportError(
                "Explicit adapter capability is unavailable.", "unsupported_capability"
            )

        options["check_recipe"] = unsupported
    with pytest.raises(ValueError):
        _ = prepare(public, raw, **options)
    assert public.accepted is before and before.graph.snapshot() == original


@pytest.mark.parametrize(
    "phase", ["before", "reading", "vertices", "prepared", "commit"]
)
def test_cancelled_open_never_installs_source_only_intermediate_state(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]], phase: str
) -> None:
    fixture, _, raw = scene
    public = admitted(native_grid_fixture(4, 3))
    before = public.accepted
    cancelled = phase == "before"

    def progress(update: ScanProgress) -> None:
        nonlocal cancelled
        assert public.accepted is before
        cancelled = update.phase == phase

    if phase == "commit":
        candidate = prepare(
            public,
            raw,
            stream=io.BytesIO(fixture.raw),
            filename="saved.ply",
            limits=LIMITS,
        )
        with pytest.raises(ScanCancelled):
            _ = public.commit_open(candidate, cancelled=lambda: True)
    else:
        with pytest.raises(ScanCancelled):
            _ = prepare(
                public,
                raw,
                stream=io.BytesIO(fixture.raw),
                filename="saved.ply",
                limits=LIMITS,
                progress=progress,
                cancelled=lambda: cancelled,
            )
    assert public.accepted is before


def test_confirmation_and_preflight_rejection_do_not_read_supplied_stream(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, public, raw = scene

    class ForbiddenRead(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("Rejected request attempted source reading")

    before = public.accepted
    with pytest.raises(ScanImportError) as confirmation:
        _ = public.prepare_open(
            raw,
            generation=1,
            token=token(public),
            confirmed_discard=False,
            stream=ForbiddenRead(),
        )
    assert confirmation.value.code == "discard_required"
    raw["nodes"][0]["descriptor"]["interpretation_sha256"] = "0" * 64
    with pytest.raises(ScanImportError) as unsupported:
        _ = prepare(
            public, raw, stream=ForbiddenRead(), filename="x.ply", limits=LIMITS
        )
    assert (
        unsupported.value.code == "unsupported_interpretation"
        and public.accepted is before
    )


@pytest.mark.parametrize(
    "change", ["foreign", "recipe", "evaluation", "stale", "busy", "generation"]
)
def test_candidate_guards_preserve_authoritative_state(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]], change: str
) -> None:
    _, project, raw = scene
    candidate = prepare(project, raw)
    before = project.accepted
    if change == "foreign":
        project = admitted()
        before = project.accepted
    elif change == "recipe":
        recipe = candidate.accepted.graph.snapshot()["recipe"]
        changed = cast(dict[str, Any], recipe)
        changed["groups"].append({"id": "changed", "label": "Changed"})
        _ = candidate.accepted.graph.replace(
            Recipe.model_validate(changed), candidate.prepared_token
        )
    elif change == "evaluation":
        _ = candidate.accepted.graph.evaluate(
            candidate.prepared_token, all_actions=True
        )
    elif change == "stale":
        _ = project.reset(generation=before.generation, token=token(project))
        before = project.accepted
    elif change == "generation":
        candidate = replace(
            candidate, accepted=replace(candidate.accepted, generation=99)
        )
    if change == "busy":
        with project.evaluation(), pytest.raises(ScanImportError):
            _ = project.commit_open(candidate)
    else:
        with pytest.raises(ScanImportError):
            _ = project.commit_open(candidate)
    assert project.accepted is before


def test_add_scan_and_open_actions_candidates_are_not_interchangeable(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    fixture, public, raw = scene
    candidate = prepare(public, raw)
    with pytest.raises(ScanImportError):
        _ = public.commit(cast(PreparedScan, cast(object, candidate)))
    empty = ScanProject()
    added = empty.prepare_add(
        io.BytesIO(fixture.raw), "scan.ply", LIMITS, generation=0, token=token(empty)
    )
    with pytest.raises(ScanImportError):
        _ = empty.commit_open(cast(PreparedProject, cast(object, added)))
    _ = empty.commit(added)
    with pytest.raises(ScanImportError) as second:
        _ = empty.prepare_add(
            io.BytesIO(fixture.raw),
            "scan.ply",
            LIMITS,
            generation=1,
            token=token(empty),
        )
    assert second.value.code == "source_exists"


def test_empty_and_same_byte_opens_advance_epoch_and_keep_no_computed_outputs(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, project, raw = scene
    first = prepare(project, raw)
    _ = project.commit_open(first)
    second = prepare(project, raw)
    _ = project.commit_open(second)
    assert project.accepted.generation == 3
    candidate = prepare(project, {"schema_version": 2, "nodes": [], "output": None})
    _ = project.commit_open(candidate)
    before_token = token(project)
    candidate = prepare(project, {"schema_version": 2, "nodes": [], "output": None})
    _ = project.commit_open(candidate)
    assert project.accepted.generation == 5 and token(project) == before_token
    assert (
        project.accepted.workspace.descriptor is None
        and project.accepted.graph.snapshot()["results"] == {}
    )


def test_numerical_failure_is_evaluated_after_acceptance_not_precommit_rejection(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    _, project, raw = scene
    raw["nodes"][1]["ids"] = []
    candidate = prepare(project, raw)
    assert candidate.accepted.graph.snapshot()["results"] == {}
    accepted = project.commit_open(candidate)
    with pytest.raises(ValueError):
        _ = accepted.graph.evaluate(token(project), all_actions=True)
    state = accepted.graph.snapshot()
    assert cast(dict[str, str], state["states"])["plane"] == "failed"
    assert project.accepted is accepted


def test_edit_during_source_preparation_invalidates_candidate_without_undoing_edit(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
) -> None:
    fixture, _, raw = scene
    project = admitted(native_grid_fixture(4, 3))
    original = project.accepted
    edited = False

    def progress(update: ScanProgress) -> None:
        nonlocal edited
        if edited or update.phase != "reading":
            return
        edited = True
        current = cast(dict[str, Any], original.graph.snapshot()["recipe"])
        current["groups"].append({"id": "retained", "label": "Concurrent edit"})
        _ = original.graph.replace(Recipe.model_validate(current), token(project))

    candidate = prepare(
        project,
        raw,
        stream=io.BytesIO(fixture.raw),
        filename="located.ply",
        limits=LIMITS,
        progress=progress,
    )
    after_edit = original.graph.snapshot()
    with pytest.raises(ScanImportError) as stale:
        _ = project.commit_open(candidate)
    assert stale.value.code == "stale_source"
    assert (
        edited
        and project.accepted is original
        and original.graph.snapshot() == after_edit
    )


@pytest.mark.parametrize("kind", ["empty", "matching"])
def test_no_upload_open_rejects_supplied_stream_instead_of_ignoring_wrong_bytes(
    scene: tuple[NativeScanFixture, ScanProject, dict[str, Any]],
    kind: str,
) -> None:
    _, project, raw = scene
    previous = project.accepted
    if kind == "empty":
        raw = {"schema_version": 2, "nodes": [], "output": None}

    class Unreadable(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("Unexpected stream must not be consumed")

    with pytest.raises(ScanImportError) as unexpected:
        _ = prepare(project, raw, stream=Unreadable(b"wrong source"))
    assert unexpected.value.code == "unexpected_source" and project.accepted is previous
