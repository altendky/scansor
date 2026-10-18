"""Replacement preserves definitions while isolating source-local observations."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from typing import Any, Literal, cast

import numpy as np
import pytest

from experiments import actions_limits
from experiments.feature_graph import FeatureGraph, Recipe, Selection, Source
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.scan_replacement import replacement_recipe
from experiments.scan_workspace import (
    ScanCancelled,
    ScanLimits,
    ScanWorkspace,
    prepare_scan,
)
from experiments.selection_entries import (
    SelectionEntry,
    SourceBinding,
    SurfaceAuthoringReference,
    VolumeReference,
    initial_entry_id,
    source_binding,
)
from experiments.selection_volume_authoring import authoring_fingerprint, resolve_volume
from experiments.selection_volume_transactions import author_volume, convert_selection
from tests.native_scan_fixture import native_grid_fixture


def workspace(count: int = 3, *, reordered: bool = False) -> ScanWorkspace:
    fixture = native_grid_fixture(count, count)
    raw = fixture.raw
    if reordered:
        header = raw.split(b"end_header\n", 1)[0] + b"end_header\n"
        rows = np.column_stack((fixture.positions, fixture.normals))[::-1].astype("<f4")
        faces = np.empty(
            len(fixture.triangles),
            dtype=np.dtype([("count", "u1"), ("indices", "<i4", (3,))]),
        )
        faces["count"] = 3
        faces["indices"] = len(rows) - 1 - fixture.triangles
        raw = header + rows.tobytes() + faces.tobytes()
    return prepare_scan(
        io.BytesIO(raw),
        "grid.ply",
        ScanLimits(1_000_000, 1000, 2000, 8_000_000),
    )


def recipe(scan: ScanWorkspace) -> Recipe:
    return Recipe.model_validate(
        {
            "schema_version": 2,
            "groups": [{"id": "group", "label": "Definitions"}],
            "nodes": [
                {
                    "id": "scan",
                    "operation": "source",
                    "label": "Authored scan name",
                    "source_sha256": scan.source_sha256,
                    "reference_sha256": scan.reference_sha256,
                    "descriptor": scan.source_descriptor.model_dump()
                    if scan.source_descriptor
                    else None,
                },
                {
                    "id": "patch",
                    "operation": "selection",
                    "label": "Patch",
                    "group_id": "group",
                    "source": "scan",
                    "ids": [0, 1, 3, 4],
                    "depth": "first_surface",
                },
                {
                    "id": "other",
                    "operation": "selection",
                    "label": "Other",
                    "source": "scan",
                    "ids": [4, 5, 7, 8],
                },
                {
                    "id": "plane",
                    "operation": "fit",
                    "label": "Plane",
                    "group_id": "group",
                    "selections": ["patch", "other"],
                    "kind": "plane",
                },
                {
                    "id": "growth",
                    "operation": "growth",
                    "label": "Growth",
                    "seed_fit": "plane",
                    "barriers": ["other"],
                    "distance": 0.1,
                    "angle_degrees": 10,
                },
                {
                    "id": "point",
                    "operation": "point",
                    "label": "Manual point",
                    "initial_coordinates": [1.0, 2.0, 3.0],
                },
            ],
            "output": "growth",
        }
    )


def selection(saved: Recipe, identifier: str = "patch") -> Selection:
    node = next(node for node in saved.nodes if node.id == identifier)
    assert isinstance(node, Selection)
    return node


def authored(scan: ScanWorkspace, kind: Literal["mesh", "plane"] = "mesh") -> Recipe:
    graph = FeatureGraph(scan, recipe(scan), refresh_volumes=False)
    _ = author_volume(
        graph,
        str(graph.snapshot()["token"]),
        selection="patch",
        volume="volume",
        label="Accepted volume",
        kind=kind,
        fit=SurfaceAuthoringReference(feature="plane") if kind == "plane" else None,
    )
    return Recipe.model_validate(graph.snapshot()["recipe"])


def volume_ref(saved: Recipe) -> VolumeReference:
    entries = selection(saved).entries
    assert entries
    return VolumeReference(selection="patch", entry=entries[0].id, volume="volume")


@pytest.mark.parametrize("count,reordered", [(5, False), (3, True)])
def test_new_density_or_row_order_never_inherits_old_ids(
    count: int, reordered: bool
) -> None:
    old, new = workspace(), workspace(count, reordered=reordered)
    original = recipe(old)
    before = original.model_dump(mode="json")
    old_xyz, new_xyz = old.data.xyz.copy(), new.data.xyz.copy()
    migrated = replacement_recipe(original, old, new)
    assert migrated.groups == original.groups and migrated.output == original.output
    assert [node.id for node in migrated.nodes] == [node.id for node in original.nodes]
    for previous, current in zip(original.nodes, migrated.nodes, strict=True):
        if isinstance(previous, Selection):
            assert isinstance(current, Selection) and current.entries
            first, target = current.entries
            assert first.scan == source_binding(old)
            assert first.ids == previous.ids and first.depth == previous.depth
            assert target.scan == source_binding(new)
            assert target.id == initial_entry_id(current.id, target.scan.key)
            assert target.mode == "generated" and target.ids is None
            assert target.source_volume is None and target.volumes == []
            assert (
                current.label == previous.label
                and current.group_id == previous.group_id
            )
        elif isinstance(previous, Source):
            assert isinstance(current, Source)
            assert current.label == previous.label
            assert current.source_sha256 == new.source_sha256
            assert current.reference_sha256 == new.reference_sha256
            assert current.descriptor == new.descriptor
        else:
            assert current == previous
    state = FeatureGraph(new, migrated).snapshot()
    states = cast(dict[str, str], state["states"])
    assert states["patch"] == states["plane"] == "needs_input"
    assert not state["errors"] and not state["results"]
    assert original.model_dump(mode="json") == before
    np.testing.assert_array_equal(old.data.xyz, old_xyz)
    np.testing.assert_array_equal(new.data.xyz, new_xyz)


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_accepted_volume_and_fingerprint_survive_all_flat_sibling_migration(
    kind: Literal["mesh", "plane"],
) -> None:
    old, new = workspace(), workspace(5)
    original = authored(old, kind)
    ref = volume_ref(original)
    owner, entry, volume = resolve_volume(original, ref)
    assert volume.authoring_fingerprint == authoring_fingerprint(
        original, owner, entry, volume
    )
    migrated = replacement_recipe(original, old, new)
    owner, entry, retained = resolve_volume(migrated, ref)
    assert retained == volume
    assert retained.authoring_fingerprint == authoring_fingerprint(
        migrated, owner, entry, retained
    )
    assert selection(original, "other").entries is None
    assert selection(migrated, "other").entries is not None


def test_previously_stale_volume_is_not_blessed_by_replacement() -> None:
    old, new = workspace(), workspace(5)
    payload = authored(old).model_dump(mode="json")
    payload["nodes"][1]["entries"][0]["ids"] = [0, 1, 2]
    original = Recipe.model_validate(payload)
    ref = volume_ref(original)
    owner, entry, volume = resolve_volume(original, ref)
    assert volume.authoring_fingerprint != authoring_fingerprint(
        original, owner, entry, volume
    )
    migrated = replacement_recipe(original, old, new)
    owner, entry, retained = resolve_volume(migrated, ref)
    assert retained == volume
    assert retained.authoring_fingerprint != authoring_fingerprint(
        migrated, owner, entry, retained
    )


def test_same_bytes_renamed_reuses_entry_without_creating_a_pending_choice() -> None:
    old = workspace()
    assert old.descriptor
    renamed = replace(
        old,
        name="renamed.ply",
        descriptor=old.descriptor.model_copy(update={"filename": "renamed.ply"}),
    )
    original = authored(old)
    migrated = replacement_recipe(original, old, renamed)
    assert selection(migrated).entries == selection(original).entries
    other = selection(migrated, "other")
    assert (
        other.entries
        and len(other.entries) == 1
        and other.entries[0].ids == [4, 5, 7, 8]
    )
    assert migrated.nodes[0].label == original.nodes[0].label


def test_returning_to_original_binding_restores_its_exact_painted_entry() -> None:
    old, new = workspace(), workspace(5)
    original = authored(old)
    first = replacement_recipe(original, old, new)
    returned = replacement_recipe(first, new, old)
    entries = selection(returned).entries
    original_entries = selection(original).entries
    first_entries = selection(first).entries
    assert entries and len(entries) == 2
    assert original_entries and first_entries
    assert entries[0] == original_entries[0]
    assert entries[1] == first_entries[1]
    states = cast(dict[str, str], FeatureGraph(old, returned).snapshot()["states"])
    assert states["patch"] != "needs_input"


def test_existing_generated_choice_is_preserved_on_reactivation() -> None:
    old, new = workspace(), workspace(5)
    first = replacement_recipe(authored(old), old, new)
    payload = first.model_dump(mode="json")
    payload["nodes"][1]["entries"][1]["source_volume"] = volume_ref(first).model_dump()
    chosen = Recipe.model_validate(payload)
    returned = replacement_recipe(chosen, new, old)
    again = replacement_recipe(returned, old, new)
    assert selection(again).entries == selection(chosen).entries


def test_managed_reuse_lineage_and_children_keep_authored_identities() -> None:
    old, new = workspace(), workspace(5)
    plan = reconcile_feature_reuse(
        recipe(old),
        "reuse",
        ReuseChanges(
            label="Reuse",
            fits=["plane"],
            reference_selection="patch",
            target_selections=["other"],
        ),
        "replacement-test",
        create=True,
    )
    migrated = replacement_recipe(plan.recipe, old, new)
    unchanged = {
        node.id: node
        for node in plan.recipe.nodes
        if not isinstance(node, (Source, Selection))
    }
    assert unchanged and plan.generated_ids
    assert {
        node.id: node
        for node in migrated.nodes
        if not isinstance(node, (Source, Selection))
    } == unchanged
    assert not FeatureGraph(new, migrated).snapshot()["results"]


def test_empty_missing_or_mismatched_source_and_frame_are_rejected() -> None:
    old, new = workspace(), workspace(5)
    with pytest.raises(ValueError, match="empty workspace"):
        _ = replacement_recipe(Recipe(), ScanWorkspace.empty(), new)
    with pytest.raises(ValueError, match="empty workspace"):
        _ = replacement_recipe(recipe(old), old, ScanWorkspace.empty())
    with pytest.raises(ValueError, match="exactly one"):
        _ = replacement_recipe(Recipe(), old, new)
    with pytest.raises(ValueError, match="binding mismatch"):
        _ = replacement_recipe(recipe(new), old, new)
    with pytest.raises(ValueError, match="coordinate frame"):
        _ = replacement_recipe(recipe(old), old, replace(new, origin=np.ones(3)))
    with pytest.raises(ValueError, match="coordinate frame"):
        _ = replacement_recipe(
            recipe(old), old, replace(new, frame=np.diag([-1.0, -1.0, 1.0]))
        )


@pytest.mark.parametrize("broken", ["fit", "volume"])
def test_invalid_references_fail_without_mutating_input(broken: str) -> None:
    old, new = workspace(), workspace(5)
    payload = authored(old).model_dump(mode="json")
    if broken == "fit":
        payload["nodes"][3]["selections"] = ["missing"]
    else:
        generated = SelectionEntry(
            id="bad",
            scan=source_binding(new),
            mode="generated",
            source_volume=VolumeReference(
                selection="patch", entry="missing", volume="missing"
            ),
        )
        payload["nodes"][1]["entries"].append(generated.model_dump(mode="json"))
    invalid = Recipe.model_validate(payload)
    before = invalid.model_dump(mode="json")
    with pytest.raises(ValueError):
        _ = replacement_recipe(invalid, old, new)
    assert invalid.model_dump(mode="json") == before


def test_capacity_rejects_new_binding_but_allows_existing_binding() -> None:
    old, new = workspace(), workspace(5)
    payload = convert_selection(recipe(old), old, "patch").model_dump(mode="json")
    entries: list[dict[str, Any]] = payload["nodes"][1]["entries"]
    for number in range(127):
        binding = SourceBinding(
            source_sha256=f"{number:064x}", reference_sha256="f" * 64, vertices=9
        )
        entries.append(
            SelectionEntry(
                id=f"retained_{number}", scan=binding, mode="generated"
            ).model_dump(mode="json")
        )
    full = Recipe.model_validate(payload)
    with pytest.raises(ValueError, match="128"):
        _ = replacement_recipe(full, old, new)
    assert len(selection(replacement_recipe(full, old, old)).entries or []) == 128
    assert len(selection(full).entries or []) == 128


def test_deterministic_new_entry_id_cannot_collide_with_retained_entry() -> None:
    old, new = workspace(), workspace(5)
    payload = convert_selection(recipe(old), old, "patch").model_dump(mode="json")
    payload["nodes"][1]["entries"][0]["id"] = initial_entry_id(
        "patch", source_binding(new).key
    )
    original = Recipe.model_validate(payload)
    with pytest.raises(ValueError, match="entry IDs"):
        _ = replacement_recipe(original, old, new)


def test_replacement_reserves_portable_saved_size_before_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old, new = workspace(), workspace(5)
    original = recipe(old)
    size = (
        len(
            json.dumps(
                original.model_dump(mode="json"), ensure_ascii=False, indent=2
            ).encode()
        )
        + 1
    )
    monkeypatch.setattr(actions_limits, "MAX_ACTIONS_JSON_BYTES", size)
    with pytest.raises(ValueError, match="Saved actions"):
        _ = replacement_recipe(original, old, new)


@pytest.mark.parametrize("boundary", [1, 3, 5, 11])
def test_cancellation_before_or_during_detached_validation_never_changes_recipe(
    boundary: int,
) -> None:
    old, new = workspace(), workspace(5)
    original = recipe(old)
    before = original.model_dump(mode="json")
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= boundary

    with pytest.raises(ScanCancelled):
        _ = replacement_recipe(original, old, new, cancelled=cancelled)
    assert original.model_dump(mode="json") == before
