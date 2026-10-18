"""UI controls preserve exact supports, source choice and explicit adoption."""

from __future__ import annotations

import json
from typing import Any, Literal, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe, Selection, StaleGraph
from experiments.scan_workspace import ScanCancelled
from experiments.selection_entries import (
    SelectionEntry,
    VolumeReference,
    source_binding,
)
from experiments.selection_volume_authoring import authoring_fingerprint, resolve_volume
from experiments.selection_volume_operations import dispatch
from experiments.volume_archive import decode_selection_volume
from tests.test_selection_volume_transactions import (
    create,
    graph,
    reference,
    rescan,
    saved,
    token,
    workspace,
)


def call(graph: FeatureGraph[Any], operation: str, **payload: Any) -> dict[str, Any]:
    return dispatch(graph, operation, {"token": token(graph), **payload})


def test_choices_use_the_shared_output_catalogue_and_actual_authoring_closure() -> None:
    current = graph()
    before = current.snapshot()
    choices = call(current, "getSelectionVolumeChoices", selection="patch")
    assert choices["surfaces"] == [
        {
            "reference": {"feature": "plane", "surface": None},
            "label": "Plane — plane",
            "kind": "plane",
            "eligible": True,
            "reason": None,
        }
    ]
    assert choices["volumes"] == []
    assert current.snapshot() == before  # Reading never converts a legacy selection.
    payload = saved(current).model_dump(mode="json")
    payload["nodes"].insert(
        2,
        {
            "id": "other",
            "operation": "selection",
            "label": "Other",
            "source": "scan",
            "ids": [0, 1, 5, 6],
        },
    )
    payload["nodes"].append(
        {
            "id": "unrelated",
            "operation": "fit",
            "label": "Unrelated",
            "kind": "plane",
            "selections": ["other"],
        }
    )
    _ = current.replace(Recipe.model_validate(payload), token(current))
    choices = call(current, "getSelectionVolumeChoices", selection="patch")
    unrelated = next(
        choice
        for choice in choices["surfaces"]
        if choice["reference"]["feature"] == "unrelated"
    )
    assert not unrelated["eligible"]
    assert "owning selection" in unrelated["reason"]


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_create_pages_every_original_support_cell_and_resets_one_override(
    kind: Literal["mesh", "plane"],
) -> None:
    current = graph()
    _ = call(
        current,
        "authorSelectionVolume",
        selection="patch",
        mode="create",
        volume="region",
        label="Support",
        kind=kind,
        **({"fit": {"feature": "plane"}} if kind == "plane" else {}),
    )
    ref = reference(current)
    _, _, volume = resolve_volume(saved(current), ref)
    geometry = decode_selection_volume(volume.archive)
    positions: list[float] = []
    normals: list[float] = []
    offset = 0
    while True:
        page = call(
            current,
            "getSelectionVolumePreview",
            reference=ref.model_dump(),
            archive_sha256=volume.archive.sha256,
            offset=offset,
            limit=3,
        )
        assert page["total_cells"] == len(geometry["patch_vertices"])
        assert page["freshness"] == "current"
        assert 0 < page["returned_cells"] <= 3
        assert len(json.dumps(page).encode()) <= 1_000_000
        positions.extend(page["positions"])
        normals.extend(page["normals"])
        if page["next_offset"] is None:
            break
        assert page["next_offset"] > offset
        offset = page["next_offset"]
    np.testing.assert_allclose(
        np.asarray(positions).reshape((-1, 3, 3)),
        geometry["patch_vertices"],
        atol=0,
        rtol=0,
    )
    np.testing.assert_allclose(
        np.asarray(normals).reshape((-1, 3, 3)),
        geometry["patch_normals"],
        atol=0,
        rtol=0,
    )
    _ = call(
        current,
        "setSelectionVolumeOptions",
        reference=ref.model_dump(),
        options={"inward_thickness": 2.0, "outward_thickness": 3.0},
    )
    _ = call(
        current,
        "setSelectionVolumeOptions",
        reference=ref.model_dump(),
        options={"inward_thickness": None},
    )
    _, _, changed = resolve_volume(saved(current), ref)
    assert changed.options.inward_thickness is None
    assert changed.options.outward_thickness == 3.0
    assert (
        decode_selection_volume(changed.archive)["resolved"]["inward_thickness"]
        == geometry["defaults"]["inward_thickness"]
    )


def test_preview_rejects_stale_identity_and_unbounded_or_invalid_pages_atomically() -> (
    None
):
    current = graph()
    _ = create(current)
    ref = reference(current)
    _, _, volume = resolve_volume(saved(current), ref)
    payload = {
        "token": token(current),
        "reference": ref.model_dump(),
        "archive_sha256": volume.archive.sha256,
    }
    before = current.snapshot()
    for changes in (
        {"limit": 2049},
        {"limit": True},
        {"offset": -1},
        {"offset": 1_000_000},
        {"archive_sha256": "0" * 64},
        {"token": "stale"},
    ):
        with pytest.raises((ValueError, StaleGraph)):
            _ = dispatch(current, "getSelectionVolumePreview", {**payload, **changes})
        assert current.snapshot() == before
    with pytest.raises(ScanCancelled):
        _ = dispatch(
            current, "getSelectionVolumePreview", payload, cancelled=lambda: True
        )
    assert current.snapshot() == before


def test_preview_uses_active_display_frame_even_when_author_scan_is_unloaded() -> None:
    original = graph()
    _ = create(original)
    target = rescan(original)
    ref = reference(original)
    _, _, volume = resolve_volume(saved(target), ref)
    page = call(
        target,
        "getSelectionVolumePreview",
        reference=ref.model_dump(),
        archive_sha256=volume.archive.sha256,
    )
    geometry = decode_selection_volume(volume.archive)
    frame = geometry["provenance"]["source_frame"]
    expected = (
        np.asarray(geometry["patch_vertices"]) @ np.asarray(frame["basis"]).T
        + frame["origin"]
        - target.workspace.origin
    ) @ target.workspace.frame
    np.testing.assert_allclose(page["positions"], expected.ravel(), atol=1e-14)
    choices = call(
        target, "getSelectionVolumeChoices", selection="patch", entry=ref.entry
    )
    assert all(
        not choice["eligible"] and "Load" in choice["reason"]
        for choice in choices["surfaces"]
    )


def test_preview_metadata_cannot_mutate_the_accepted_archive_cache() -> None:
    current = graph()
    _ = create(current)
    ref = reference(current)
    _, _, volume = resolve_volume(saved(current), ref)
    payload = {"reference": ref.model_dump(), "archive_sha256": volume.archive.sha256}
    first = call(current, "getSelectionVolumePreview", **payload)
    expected = {
        name: first[name].copy() for name in ("options", "defaults", "resolved")
    }
    for name in expected:
        first[name]["edge_padding"] = 123.0
    second = call(current, "getSelectionVolumePreview", **payload)
    assert {name: second[name] for name in expected} == expected


def test_source_choice_is_explicit_and_linked_volume_deletion_is_refused() -> None:
    original = graph()
    _ = create(original)
    target = rescan(original)
    ref = reference(original)
    _ = call(
        target,
        "chooseSelectionVolume",
        selection="patch",
        entry="rescan",
        source_volume=None,
    )
    assert cast(dict[str, Any], target.snapshot()["states"])["patch"] == "needs_input"
    choices = call(target, "getSelectionVolumeChoices", selection="patch")
    assert len(choices["volumes"]) == 1
    assert cast(dict[str, Any], target.snapshot()["states"])["patch"] == "needs_input"
    _ = call(
        target,
        "chooseSelectionVolume",
        selection="patch",
        entry="rescan",
        source_volume=ref.model_dump(),
    )
    before = target.snapshot()
    with pytest.raises(ValueError, match="linked scan"):
        _ = call(target, "deleteSelectionVolume", reference=ref.model_dump())
    assert target.snapshot() == before
    _ = call(
        target,
        "chooseSelectionVolume",
        selection="patch",
        entry="rescan",
        source_volume=None,
    )
    _ = call(target, "deleteSelectionVolume", reference=ref.model_dump())
    assert call(target, "getSelectionVolumeChoices", selection="patch")["volumes"] == []


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_cross_scan_replace_adopts_frozen_observations_and_repoints_links(
    kind: Literal["mesh", "plane"],
) -> None:
    original = graph()
    _ = create(original, kind=kind)
    old_ref = reference(original)
    old_owner, old_entry, old_volume = resolve_volume(saved(original), old_ref)
    target = rescan(original)
    _ = target.evaluate(token(target))
    before_ids = cast(dict[str, Any], target.snapshot()["memberships"])["patch"]
    assert before_ids
    _ = call(
        target,
        "authorSelectionVolume",
        selection="patch",
        entry="rescan",
        mode="replace",
        target=old_ref.model_dump(),
        label="Adopt rescan",
        kind=kind,
        **({"fit": {"feature": "plane"}} if kind == "plane" else {}),
    )
    moved = VolumeReference(selection="patch", entry="rescan", volume="region")
    owner, entry, volume = resolve_volume(saved(target), moved)
    assert entry.source_volume == moved
    assert volume.captured_ids == before_ids
    assert volume.authoring_fingerprint == authoring_fingerprint(
        saved(target), owner, entry, volume
    )
    retained = next(item for item in owner.entries or [] if item.id == old_entry.id)
    assert retained.ids == old_entry.ids and retained.volumes == []
    assert volume.archive.sha256 != old_volume.archive.sha256
    assert old_owner.id == owner.id
    with pytest.raises(ValueError):
        _ = resolve_volume(saved(target), old_ref)
    reopened = FeatureGraph(target.workspace, saved(target), refresh_volumes=False)
    _ = reopened.evaluate(token(reopened))
    assert (
        cast(dict[str, Any], reopened.snapshot()["memberships"])["patch"] == before_ids
    )
    assert cast(dict[str, Any], reopened.snapshot()["states"])["plane"] == "ready"


def test_failed_or_cancelled_adoption_leaves_original_archive_links_and_results() -> (
    None
):
    original = graph()
    _ = create(original)
    target = rescan(original)
    _ = target.evaluate(token(target))
    ref = reference(original)
    payload = {
        "token": token(target),
        "selection": "patch",
        "entry": "rescan",
        "mode": "replace",
        "target": ref.model_dump(),
        "kind": "mesh",
        "label": "Adopt",
    }
    before = target.snapshot()
    with pytest.raises(ScanCancelled):
        _ = dispatch(target, "authorSelectionVolume", payload, cancelled=lambda: True)
    assert target.snapshot() == before
    for changes in ({"entry": ref.entry}, {"token": "stale"}, {"volume": "extra"}):
        with pytest.raises((ValueError, StaleGraph)):
            _ = dispatch(target, "authorSelectionVolume", {**payload, **changes})
        assert target.snapshot() == before


def test_painted_entry_never_acquires_a_generated_link() -> None:
    current = graph()
    _ = create(current)
    ref = reference(current)
    before = current.snapshot()
    with pytest.raises(ValueError, match="only generated"):
        _ = call(
            current,
            "chooseSelectionVolume",
            selection="patch",
            entry=ref.entry,
            source_volume=ref.model_dump(),
        )
    assert current.snapshot() == before
    node = saved(current).nodes[1]
    assert isinstance(node, Selection) and node.entries
    assert node.entries[0].ids == list(range(25))


def test_reparenting_updates_multiple_links_and_freezes_connected_generated_inputs() -> (
    None
):
    original = graph()
    payload = saved(original).model_dump(mode="json")
    payload["nodes"][1]["ids"] = [i for i in range(25) if i % 5 < 2]
    payload["nodes"].extend(
        [
            {
                "id": "other",
                "operation": "selection",
                "label": "Other patch",
                "source": "scan",
                "ids": [i for i in range(25) if i % 5 > 2],
            },
            {
                "id": "other_plane",
                "operation": "fit",
                "label": "Other plane",
                "kind": "plane",
                "selections": ["other"],
            },
            {
                "id": "parallel",
                "operation": "plane_relationship",
                "label": "Parallel",
                "relation": "parallel",
                "surfaces": ["plane", "other_plane"],
            },
        ]
    )
    _ = original.replace(Recipe.model_validate(payload), token(original))
    _ = create(original)
    _ = call(
        original,
        "authorSelectionVolume",
        selection="other",
        mode="create",
        volume="other_region",
        label="Other region",
        kind="mesh",
    )
    other_owner = saved(original).nodes[3]
    assert isinstance(other_owner, Selection) and other_owner.entries
    other_ref = VolumeReference(
        selection="other", entry=other_owner.entries[0].id, volume="other_region"
    )
    old_ref = reference(original)
    current = rescan(original)
    payload = saved(current).model_dump(mode="json")
    payload["nodes"][3]["entries"].append(
        SelectionEntry(
            id="rescan",
            scan=source_binding(current.workspace),
            mode="generated",
            source_volume=other_ref,
        ).model_dump(mode="json")
    )
    payload["nodes"][1]["entries"].append(
        SelectionEntry(
            id="another_scan",
            scan=source_binding(workspace(7)),
            mode="generated",
            source_volume=old_ref,
        ).model_dump(mode="json")
    )
    _ = current.replace(Recipe.model_validate(payload), token(current))
    _ = call(
        current,
        "authorSelectionVolume",
        selection="patch",
        entry="rescan",
        mode="replace",
        target=old_ref.model_dump(),
        label="Adopt connected solve",
        kind="plane",
        fit={"feature": "plane"},
    )
    moved = VolumeReference(selection="patch", entry="rescan", volume="region")
    owner, entry, volume = resolve_volume(saved(current), moved)
    assert volume.captured_ids and set(volume.captured_inputs) == {"other"}
    assert len(volume.captured_inputs["other"]) >= 3
    assert entry.source_volume == moved
    assert (
        next(
            item for item in owner.entries or [] if item.id == "another_scan"
        ).source_volume
        == moved
    )
    assert next(
        item for item in owner.entries or [] if item.id == old_ref.entry
    ).ids == [i for i in range(25) if i % 5 < 2]
    before = authoring_fingerprint(saved(current), owner, entry, volume)
    _ = call(
        current,
        "setSelectionVolumeOptions",
        reference=other_ref.model_dump(),
        options={"edge_padding": 0.01},
    )
    assert (
        authoring_fingerprint(saved(current), *resolve_volume(saved(current), moved))
        == before
    )
    reopened = FeatureGraph(current.workspace, saved(current), refresh_volumes=False)
    _ = reopened.evaluate(token(reopened))
    assert cast(dict[str, Any], reopened.snapshot()["states"])["plane"] == "ready"
