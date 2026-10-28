"""One current graph and an exact retained original/virtual source pair."""

from __future__ import annotations

import io
from copy import deepcopy
from dataclasses import replace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from experiments.feature_graph import FeatureGraph, Recipe, Source
from experiments.scan_actions import inspect_actions, preflight_actions
from experiments.scan_project import (
    AcceptedScan,
    PreparedReplacement,
    ScanProject,
    reconstruct_virtual_scan,
    virtual_scan_metadata,
)
from experiments.scan_workspace import ScanCancelled, ScanImportError, ScanLimits
from experiments.selection_volume_transactions import author_volume
from experiments.virtual_scan_reduction import ReductionSettings, VirtualScanDerivation
from tests.native_scan_fixture import native_grid_fixture
from tests.test_selection_volume_transactions import recipe

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000)
SETTINGS = ReductionSettings(divisions=4)


def state(project: ScanProject) -> dict[str, Any]:
    return dict(project.accepted.graph.snapshot())


def cursor(project: ScanProject) -> dict[str, Any]:
    return {"generation": project.accepted.generation, "token": state(project)["token"]}


def initial_project(*, author: bool = True) -> ScanProject:
    project = ScanProject()
    candidate = project.prepare_add(
        io.BytesIO(native_grid_fixture(17, 17).raw),
        "original.ply",
        LIMITS,
        **cursor(project),
    )
    accepted = project.commit(candidate)
    _ = accepted.graph.replace(recipe(accepted.workspace), state(project)["token"])
    if author:
        _ = author_volume(
            accepted.graph,
            state(project)["token"],
            selection="patch",
            volume="mesh",
            label="Footprint",
            kind="mesh",
        )
    return project


def create(project: ScanProject, **kwargs: Any) -> PreparedReplacement:
    return project.prepare_virtual_scan(SETTINGS, LIMITS, **cursor(project), **kwargs)


def activate(project: ScanProject, binding: str, **kwargs: Any) -> AcceptedScan:
    return project.commit_replace(
        project.prepare_activate_scan(binding, LIMITS, **cursor(project), **kwargs)
    )


def lineage(project: ScanProject) -> VirtualScanDerivation:
    source = Source.model_validate(state(project)["recipe"]["nodes"][0])
    assert source.virtual_scan is not None
    return source.virtual_scan


def test_creation_stages_pair_and_requires_explicit_volume_transfer() -> None:
    project = initial_project()
    original, before = project.accepted, state(project)
    candidate = create(project)
    assert project.accepted is original and state(project) == before
    pending: dict[str, Any] = dict(candidate.accepted.graph.snapshot())
    assert pending["states"]["patch"] == pending["states"]["plane"] == "needs_input"
    assert pending["results"] == {}
    assert len(candidate.accepted.retained_sources) == 2
    assert candidate.accepted.retained_sources[0] is original.workspace
    assert candidate.accepted.workspace is candidate.accepted.retained_sources[1]
    accepted = project.commit_replace(candidate)
    entries = pending["recipe"]["nodes"][1]["entries"]
    assert len(entries) == 2 and entries[1]["source_volume"] is None
    payload = deepcopy(pending["recipe"])
    payload["nodes"][1]["entries"][1]["source_volume"] = {
        "selection": "patch",
        "entry": entries[0]["id"],
        "volume": "mesh",
    }
    payload["nodes"][2]["label"] = "Edited on virtual scan"
    changed = accepted.graph.replace(Recipe.model_validate(payload), pending["token"])
    evaluated: dict[str, Any] = accepted.graph.ensure_current(
        str(changed["token"]), ["plane"]
    )
    assert evaluated["states"]["plane"] == "ready"
    assert evaluated["memberships"]["patch"] == list(
        range(len(accepted.workspace.local))
    )
    pair = accepted.retained_sources
    original_binding = lineage(project).original.binding_sha256
    virtual_binding = lineage(project).derived.binding_sha256
    returned = activate(project, original_binding)
    assert (
        returned.workspace is original.workspace and returned.retained_sources == pair
    )
    assert state(project)["recipe"]["nodes"][2]["label"] == "Edited on virtual scan"
    assert state(project)["results"] == {}
    assert state(project)["recipe"]["nodes"][1]["entries"][0] == entries[0]
    assert original.graph.snapshot() == before
    final = activate(project, virtual_binding)
    assert final.workspace is pair[1]
    assert (
        state(project)["recipe"]["nodes"][1]["entries"][1]["source_volume"] is not None
    )
    assert len(state(project)["recipe"]["nodes"][1]["entries"]) == 2


def test_metadata_counts_active_binding_and_single_derivative_boundary() -> None:
    project = initial_project(author=False)
    before = virtual_scan_metadata(project.accepted)
    assert before["supported"] is True and before["can_create"] is True
    assert before["virtual"] is None
    _ = project.commit_replace(create(project))
    metadata = virtual_scan_metadata(project.accepted)
    assert metadata["can_create"] is False and metadata["can_activate"] is True
    assert metadata["active_binding"] == lineage(project).derived.binding_sha256
    assert metadata["settings"] == SETTINGS.model_dump(mode="json")
    virtual = metadata["virtual"]
    assert isinstance(virtual, dict) and virtual["vertices"] == len(
        project.accepted.workspace.local
    )
    with pytest.raises(ScanImportError) as duplicate:
        _ = create(project)
    assert duplicate.value.code == "virtual_scan_exists"
    assert virtual_scan_metadata(None)["supported"] is False


def test_stale_cancelled_foreign_and_mutated_candidates_leave_current_project() -> None:
    project = initial_project()
    previous, before = project.accepted, state(project)
    with pytest.raises(ScanCancelled):
        _ = create(project, cancelled=lambda: True)
    candidate = create(project)
    with pytest.raises(ScanCancelled):
        _ = project.commit_replace(candidate, cancelled=lambda: True)
    assert project.accepted is previous and state(project) == before
    other = initial_project()
    with pytest.raises(ScanImportError) as foreign:
        _ = other.commit_replace(candidate)
    assert foreign.value.code == "invalid_candidate"
    payload = deepcopy(before["recipe"])
    payload["nodes"][2]["label"] = "Intervening edit"
    _ = previous.graph.replace(Recipe.model_validate(payload), before["token"])
    with pytest.raises(ScanImportError) as stale:
        _ = project.commit_replace(candidate)
    assert stale.value.code == "stale_source"
    candidate = create(project)
    _ = candidate.accepted.graph.evaluate(
        str(candidate.accepted.graph.snapshot()["token"]), all_actions=True
    )
    with pytest.raises(ScanImportError) as modified:
        _ = project.commit_replace(candidate)
    assert modified.value.code == "invalid_candidate"


def test_private_owner_uses_current_matching_graph_and_catalog() -> None:
    main = initial_project()
    original = main.accepted
    private = ScanProject()
    candidate = private.prepare_virtual_scan(
        SETTINGS, LIMITS, matching=original, **cursor(private)
    )
    accepted = private.commit_replace(candidate)
    assert main.accepted is original
    third = ScanProject()
    switched = third.prepare_activate_scan(
        lineage(private).original.binding_sha256,
        LIMITS,
        matching=accepted,
        **cursor(third),
    )
    result = third.commit_replace(switched)
    assert result.workspace is original.workspace
    assert result.retained_sources == accepted.retained_sources


@pytest.mark.parametrize("category", ["bytes", "vertices", "faces", "estimated"])
def test_aggregate_retained_admission_preserves_accepted_project(category: str) -> None:
    project = initial_project()
    original = project.accepted
    candidate = create(project)
    pair = candidate.accepted.retained_sources
    settings = {
        "bytes": {
            "max_source_bytes": sum(len(item.original_bytes) for item in pair) - 1
        },
        "vertices": {"max_vertices": sum(len(item.local) for item in pair) - 1},
        "faces": {"max_faces": sum(len(item.data.triangles) for item in pair) - 1},
        "estimated": {
            "max_estimated_bytes": sum(
                item.diagnostics.estimated_bytes for item in pair
            )
            - 1
        },
    }
    tighter = replace(LIMITS, **settings[category])
    with pytest.raises(ScanImportError) as rejected:
        _ = project.prepare_virtual_scan(SETTINGS, tighter, **cursor(project))
    assert rejected.value.code == "source_limit"
    assert project.accepted is original


def test_invalid_binding_missing_catalog_and_busy_activation_reject() -> None:
    project = initial_project()
    with pytest.raises(ScanImportError):
        _ = project.prepare_activate_scan("0" * 64, LIMITS, **cursor(project))
    _ = project.commit_replace(create(project))
    before = project.accepted
    with pytest.raises(ScanImportError) as wrong:
        _ = project.prepare_activate_scan("0" * 64, LIMITS, **cursor(project))
    assert wrong.value.code == "source_mismatch"
    with project.evaluation():
        with pytest.raises(ScanImportError) as busy:
            _ = project.prepare_activate_scan(
                lineage(project).original.binding_sha256, LIMITS, **cursor(project)
            )
        assert busy.value.code == "source_busy"
    private = ScanProject()
    with pytest.raises(ScanImportError):
        _ = private.prepare_activate_scan(
            lineage(project).original.binding_sha256,
            LIMITS,
            matching=replace(before, retained_sources=()),
            **cursor(private),
        )
    assert project.accepted is before


def test_unrelated_replacement_and_reset_clear_lineage_and_retained_buffers() -> None:
    project = initial_project()
    _ = project.commit_replace(create(project))
    candidate = project.prepare_replace(
        io.BytesIO(native_grid_fixture(9, 9).raw),
        "other.ply",
        LIMITS,
        **cursor(project),
    )
    accepted = project.commit_replace(candidate)
    assert accepted.retained_sources == ()
    assert "virtual_scan" not in state(project)["recipe"]["nodes"][0]
    assert virtual_scan_metadata(accepted)["can_create"] is True
    _ = project.commit_replace(create(project))
    empty = project.reset(**cursor(project))
    assert empty.retained_sources == () and len(empty.workspace.local) == 0
    assert virtual_scan_metadata(empty)["supported"] is False


def test_graph_edits_cannot_attach_remove_or_change_lineage() -> None:
    project = initial_project()
    ordinary = state(project)
    candidate = create(project)
    virtual: dict[str, Any] = dict(candidate.accepted.graph.snapshot())
    payload = deepcopy(ordinary["recipe"])
    payload["nodes"][0]["virtual_scan"] = virtual["recipe"]["nodes"][0]["virtual_scan"]
    with pytest.raises(ValueError, match="source transaction"):
        _ = project.accepted.graph.replace(
            Recipe.model_validate(payload), ordinary["token"]
        )
    accepted = project.commit_replace(candidate)
    for mutation in (
        None,
        {**virtual["recipe"]["nodes"][0]["virtual_scan"], "settings": {"divisions": 8}},
    ):
        payload = deepcopy(virtual["recipe"])
        payload["nodes"][0]["virtual_scan"] = mutation
        with pytest.raises(ValueError, match="source transaction"):
            _ = accepted.graph.replace(Recipe.model_validate(payload), virtual["token"])
    payload = deepcopy(virtual["recipe"])
    payload["nodes"][0]["virtual_scan"]["original"]["source_sha256"] = "0" * 64
    payload["nodes"][0]["virtual_scan"]["derived"]["source_sha256"] = "1" * 64
    with pytest.raises(ValidationError, match="lineage endpoint"):
        _ = Recipe.model_validate(payload)


@pytest.mark.parametrize("active", ["original", "virtual"])
def test_saved_actions_reconstruct_both_active_choices_from_renamed_original(
    active: str,
) -> None:
    project = initial_project()
    original = project.accepted.workspace
    _ = project.commit_replace(create(project))
    if active == "original":
        _ = activate(project, lineage(project).original.binding_sha256)
    saved = state(project)["recipe"]
    fresh = ScanProject()
    candidate = fresh.prepare_open(
        saved,
        confirmed_discard=True,
        stream=io.BytesIO(original.original_bytes),
        filename="renamed-original.ply",
        limits=LIMITS,
        **cursor(fresh),
    )
    reopened = fresh.commit_open(candidate)
    assert (
        reopened.workspace.reference_sha256
        == project.accepted.workspace.reference_sha256
    )
    assert len(reopened.retained_sources) == 2
    assert state(fresh)["recipe"] == saved
    np.testing.assert_array_equal(
        reopened.workspace.data.xyz, project.accepted.workspace.data.xyz
    )
    assert state(fresh)["results"] == {}
    inspected = inspect_actions(saved, original)
    assert inspected.disposition == "matching"
    assert inspected.json()["uploadRequired"] is False
    assert inspected.json()["descriptor"] == lineage(project).original.model_dump(
        mode="json"
    )


def test_reopen_uses_retained_original_for_ordinary_actions_and_can_rebuild_pair() -> (
    None
):
    project = initial_project()
    original_actions = state(project)["recipe"]
    original = project.accepted
    _ = project.commit_replace(create(project))
    virtual_actions = state(project)["recipe"]
    assert (
        inspect_actions(original_actions, project.accepted.workspace).disposition
        == "locate"
    )
    assert (
        inspect_actions(
            original_actions,
            project.accepted.workspace,
            retained_sources=project.accepted.retained_sources,
        ).disposition
        == "matching"
    )
    candidate = project.prepare_open(
        original_actions, confirmed_discard=True, limits=LIMITS, **cursor(project)
    )
    opened = project.commit_open(candidate)
    assert opened.workspace is original.workspace and opened.retained_sources == ()
    restored = project.prepare_open(
        virtual_actions, confirmed_discard=True, limits=LIMITS, **cursor(project)
    )
    _ = project.commit_open(restored)
    assert len(project.accepted.retained_sources) == 2
    assert state(project)["recipe"] == virtual_actions


def test_wrong_original_tampered_derivation_and_derived_only_recovery_reject() -> None:
    project = initial_project()
    _ = project.commit_replace(create(project))
    saved = state(project)["recipe"]
    lineage_record = lineage(project)
    fresh = ScanProject()
    with pytest.raises(ScanImportError) as wrong:
        _ = fresh.prepare_open(
            saved,
            confirmed_discard=True,
            stream=io.BytesIO(native_grid_fixture(9, 9).raw),
            filename="wrong.ply",
            limits=LIMITS,
            **cursor(fresh),
        )
    assert wrong.value.code == "source_mismatch"
    altered = lineage_record.model_copy(
        update={"settings": ReductionSettings(divisions=8)}
    )
    with pytest.raises(ScanImportError) as mismatch:
        _ = reconstruct_virtual_scan(
            project.accepted.retained_sources[0], altered, LIMITS
        )
    assert mismatch.value.code == "source_mismatch"
    added = fresh.prepare_add(
        io.BytesIO(project.accepted.workspace.original_bytes),
        "derived-only.ply",
        LIMITS,
        **cursor(fresh),
    )
    _ = fresh.commit(added)
    inspected = inspect_actions(saved, fresh.accepted.workspace)
    assert inspected.disposition == "locate"
    assert inspected.json()["uploadRequired"] is True
    assert inspected.json()["descriptor"] == lineage_record.original.model_dump(
        mode="json"
    )
    with pytest.raises(ScanImportError) as absent:
        _ = fresh.prepare_open(
            saved, confirmed_discard=True, limits=LIMITS, **cursor(fresh)
        )
    assert absent.value.code == "source_required"
    with pytest.raises(ScanCancelled):
        _ = reconstruct_virtual_scan(
            project.accepted.retained_sources[0],
            lineage_record,
            LIMITS,
            cancelled=lambda: True,
        )
    assert fresh.accepted.retained_sources == ()


def test_plain_source_serialization_is_unchanged_and_lineage_is_preflight_validated() -> (
    None
):
    project = initial_project(author=False)
    assert "virtual_scan" not in state(project)["recipe"]["nodes"][0]
    _ = project.commit_replace(create(project))
    saved = state(project)["recipe"]
    parsed = preflight_actions(saved)
    assert parsed.source is not None and parsed.source.virtual_scan is not None
    payload = deepcopy(saved)
    payload["nodes"][0]["virtual_scan"]["settings"]["divisions"] = True
    with pytest.raises(ScanImportError) as invalid:
        _ = preflight_actions(payload)
    assert invalid.value.code == "invalid_actions"
    _ = FeatureGraph(
        project.accepted.workspace, Recipe.model_validate(saved), refresh_volumes=False
    )


@pytest.mark.parametrize("active", ["original", "virtual"])
def test_dropping_virtual_lineage_requires_exact_derived_upload(active: str) -> None:
    project = initial_project(author=False)
    original = project.accepted.workspace
    _ = project.commit_replace(create(project))
    derived = project.accepted.workspace
    ordinary_actions = deepcopy(state(project)["recipe"])
    del ordinary_actions["nodes"][0]["virtual_scan"]
    if active == "original":
        _ = activate(project, lineage(project).original.binding_sha256)
    previous, before = project.accepted, state(project)
    inspected = inspect_actions(
        ordinary_actions,
        previous.workspace,
        retained_sources=previous.retained_sources,
    )
    assert inspected.disposition == "locate"
    assert inspected.json()["uploadRequired"] is True
    assert derived.descriptor is not None
    assert inspected.json()["descriptor"] == derived.descriptor.model_dump(mode="json")
    with pytest.raises(ScanImportError) as missing:
        _ = project.prepare_open(
            ordinary_actions, confirmed_discard=True, limits=LIMITS, **cursor(project)
        )
    assert missing.value.code == "source_required"
    with pytest.raises(ScanImportError) as wrong:
        _ = project.prepare_open(
            ordinary_actions,
            confirmed_discard=True,
            stream=io.BytesIO(original.original_bytes),
            filename="original.ply",
            limits=LIMITS,
            **cursor(project),
        )
    assert wrong.value.code == "source_mismatch"
    assert project.accepted is previous and state(project) == before
    candidate = project.prepare_open(
        ordinary_actions,
        confirmed_discard=True,
        stream=io.BytesIO(derived.original_bytes),
        filename="explicit-derived.ply",
        limits=LIMITS,
        **cursor(project),
    )
    accepted = project.commit_open(candidate)
    assert accepted.retained_sources == ()
    assert accepted.workspace.original_bytes == derived.original_bytes
    assert state(project)["recipe"] == ordinary_actions
    assert virtual_scan_metadata(accepted)["virtual"] is None
