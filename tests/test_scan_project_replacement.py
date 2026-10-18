"""Aligned replacement publishes a fresh source and retained definitions once."""

import io
from copy import deepcopy
from typing import Any

import pytest

from experiments.feature_graph import Recipe
from experiments.scan_project import PreparedReplacement, ScanProject
from experiments.scan_workspace import ScanCancelled, ScanImportError, ScanLimits
from experiments.selection_volume_transactions import author_volume
from tests.native_scan_fixture import native_grid_fixture
from tests.test_selection_volume_transactions import recipe

LIMITS = ScanLimits(2_000_000, 1000, 2000, 8_000_000)


def state(project: ScanProject) -> dict[str, Any]:
    return dict(project.accepted.graph.snapshot())


def initial_project() -> ScanProject:
    project = ScanProject()
    raw = native_grid_fixture(5, 5).raw
    prepared = project.prepare_add(
        io.BytesIO(raw),
        "original.ply",
        LIMITS,
        generation=0,
        token=state(project)["token"],
    )
    accepted = project.commit(prepared)
    _ = accepted.graph.replace(recipe(accepted.workspace), state(project)["token"])
    _ = author_volume(
        accepted.graph,
        state(project)["token"],
        selection="patch",
        volume="mesh",
        label="Footprint",
        kind="mesh",
    )
    _ = accepted.graph.evaluate(state(project)["token"], all_actions=True)
    return project


def prepare(project: ScanProject, count: int = 7, **kwargs: Any) -> PreparedReplacement:
    return project.prepare_replace(
        io.BytesIO(native_grid_fixture(count, count).raw),
        "replacement.ply",
        LIMITS,
        generation=project.accepted.generation,
        token=state(project)["token"],
        **kwargs,
    )


def test_replacement_stages_pending_entries_without_old_geometry_then_fits_choice() -> (
    None
):
    project = initial_project()
    previous = project.accepted
    before = state(project)
    candidate = prepare(project)
    pending: dict[str, Any] = dict(candidate.accepted.graph.snapshot())
    assert project.accepted is previous and state(project) == before
    assert pending["states"] == {
        "scan": "unevaluated",
        "patch": "needs_input",
        "plane": "needs_input",
    }
    assert pending["results"] == {} and pending["errors"] == {}
    entries = pending["recipe"]["nodes"][1]["entries"]
    assert entries[0] == before["recipe"]["nodes"][1]["entries"][0]
    assert len(entries) == 2 and entries[1]["source_volume"] is None
    assert entries[1]["ids"] is None and entries[1]["volumes"] == []
    accepted = project.commit_replace(candidate)
    assert (
        project.accepted is accepted and accepted.generation == previous.generation + 1
    )
    assert accepted.workspace.original_bytes == native_grid_fixture(7, 7).raw
    payload = deepcopy(pending["recipe"])
    payload["nodes"][1]["entries"][1]["source_volume"] = {
        "selection": "patch",
        "entry": entries[0]["id"],
        "volume": "mesh",
    }
    selected = accepted.graph.replace(Recipe.model_validate(payload), pending["token"])
    result: dict[str, Any] = accepted.graph.ensure_current(
        str(selected["token"]), ["plane"]
    )
    assert result["states"]["plane"] == "ready"
    assert result["memberships"]["patch"] == list(range(49))
    assert result["recipe"]["nodes"][1]["entries"][0] == entries[0]
    assert previous.graph.snapshot() == before


def test_failed_and_cancelled_preparation_or_commit_preserve_the_previous_project() -> (
    None
):
    project = initial_project()
    previous, before = project.accepted, state(project)
    with pytest.raises(ScanImportError):
        _ = project.prepare_replace(
            io.BytesIO(b"invalid"),
            "bad.ply",
            LIMITS,
            generation=previous.generation,
            token=before["token"],
        )
    with pytest.raises(ScanCancelled):
        _ = prepare(project, cancelled=lambda: True)
    candidate = prepare(project)
    with pytest.raises(ScanCancelled):
        _ = project.commit_replace(candidate, cancelled=lambda: True)
    assert project.accepted is previous and state(project) == before


def test_prepared_candidate_rejects_intervening_recipe_edits() -> None:
    project = initial_project()
    candidate = prepare(project)
    before = state(project)
    payload = deepcopy(before["recipe"])
    payload["nodes"][1]["label"] = "User edit"
    _ = project.accepted.graph.replace(Recipe.model_validate(payload), before["token"])
    edited = state(project)
    with pytest.raises(ScanImportError) as rejected:
        _ = project.commit_replace(candidate)
    assert rejected.value.code == "stale_source"
    assert state(project) == edited


@pytest.mark.parametrize("mutation", ["recipe", "evaluation"])
def test_prepared_candidate_rejects_private_mutation(mutation: str) -> None:
    project = initial_project()
    before = state(project)
    candidate = prepare(project)
    graph = candidate.accepted.graph
    raw: dict[str, Any] = dict(graph.snapshot())
    if mutation == "recipe":
        payload = deepcopy(raw["recipe"])
        payload["nodes"][0]["label"] = "Changed private source"
        _ = graph.replace(Recipe.model_validate(payload), raw["token"])
    else:
        _ = graph.evaluate(raw["token"], all_actions=True)
        assert graph.snapshot()["token"] == raw["token"]
    with pytest.raises(ScanImportError) as rejected:
        _ = project.commit_replace(candidate)
    assert rejected.value.code == "invalid_candidate"
    assert state(project) == before


def test_foreign_owner_busy_and_generation_aba_are_rejected() -> None:
    project = initial_project()
    candidate = prepare(project)
    other = initial_project()
    with pytest.raises(ScanImportError) as foreign:
        _ = other.commit_replace(candidate)
    assert foreign.value.code == "invalid_candidate"
    with project.evaluation():
        with pytest.raises(ScanImportError) as busy:
            _ = prepare(project)
        assert busy.value.code == "source_busy"
    before = state(project)
    empty = project.reset(generation=project.accepted.generation, token=before["token"])
    added = project.prepare_add(
        io.BytesIO(native_grid_fixture(5, 5).raw),
        "original.ply",
        LIMITS,
        generation=empty.generation,
        token=state(project)["token"],
    )
    _ = project.commit(added)
    _ = project.accepted.graph.replace(
        Recipe.model_validate(before["recipe"]), state(project)["token"]
    )
    assert state(project)["token"] == before["token"]
    with pytest.raises(ScanImportError) as aba:
        _ = project.commit_replace(candidate)
    assert aba.value.code == "stale_source"


def test_private_owner_can_stage_a_snapshot_and_returning_to_original_reuses_entry() -> (
    None
):
    original = initial_project()
    previous = original.accepted
    private = ScanProject()
    with pytest.raises(ScanImportError) as missing:
        _ = prepare(private)
    assert missing.value.code == "source_required"
    candidate = private.prepare_replace(
        io.BytesIO(native_grid_fixture(7, 7).raw),
        "rescan.ply",
        LIMITS,
        generation=0,
        token=state(private)["token"],
        matching=previous,
    )
    _ = private.commit_replace(candidate)
    assert original.accepted is previous
    returned = private.prepare_replace(
        io.BytesIO(previous.workspace.original_bytes),
        "renamed-original.ply",
        LIMITS,
        generation=private.accepted.generation,
        token=state(private)["token"],
    )
    accepted = private.commit_replace(returned)
    raw: dict[str, Any] = dict(accepted.graph.snapshot())
    entries = raw["recipe"]["nodes"][1]["entries"]
    assert len(entries) == 2 and entries[0]["mode"] == "painted"
    result: dict[str, Any] = accepted.graph.ensure_current(raw["token"], ["plane"])
    assert result["states"]["plane"] == "ready"
    assert result["memberships"]["patch"] == list(range(25))
