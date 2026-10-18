"""Volume authoring captures ordinary solves before one fenced publication."""

from __future__ import annotations

import io
from typing import Any, Literal, cast

import pytest

from experiments import selection_entry_runtime as entry_runtime
from experiments import selection_volume_transactions as transactions
from experiments.feature_graph import FeatureGraph, Recipe, Selection, StaleGraph
from experiments.scan_workspace import (
    ScanCancelled,
    ScanLimits,
    ScanWorkspace,
    prepare_scan,
)
from experiments.selection_entries import (
    SelectionEntry,
    SurfaceAuthoringReference,
    VolumeReference,
    source_binding,
)
from experiments.selection_volume import apply_selection_volume
from experiments.selection_volume_authoring import (
    authoring_fingerprint,
    capture_volume,
    resolve_volume,
)
from experiments.selection_volume_transactions import (
    author_volume,
    convert_selection,
    set_volume_options,
    staged_refresh,
)
from experiments.volume_archive import decode_selection_volume
from tests.native_scan_fixture import native_grid_fixture


def workspace(count: int = 5) -> ScanWorkspace:
    fixture = native_grid_fixture(count, count)
    return prepare_scan(
        io.BytesIO(fixture.raw),
        f"grid-{count}.ply",
        ScanLimits(1_000_000, 1000, 2000, 8_000_000),
    )


def recipe(scan: ScanWorkspace) -> Recipe:
    return Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": "scan",
                    "operation": "source",
                    "label": "Scan",
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
                    "source": "scan",
                    "ids": list(range(len(scan.local))),
                },
                {
                    "id": "plane",
                    "operation": "fit",
                    "label": "Plane",
                    "selections": ["patch"],
                    "kind": "plane",
                },
            ],
            "output": "plane",
        }
    )


def graph() -> FeatureGraph[ScanWorkspace]:
    scan = workspace()
    return FeatureGraph(scan, recipe(scan), refresh_volumes=False)


def token(graph: FeatureGraph[Any]) -> str:
    return str(graph.snapshot()["token"])


def saved(graph: FeatureGraph[Any]) -> Recipe:
    return Recipe.model_validate(graph.snapshot()["recipe"])


def reference(
    graph: FeatureGraph[Any], volume: str = "region", *, entry: str | None = None
) -> VolumeReference:
    node = next(node for node in saved(graph).nodes if node.id == "patch")
    assert isinstance(node, Selection) and node.entries
    return VolumeReference(
        selection="patch", entry=entry or node.entries[0].id, volume=volume
    )


def create(
    graph: FeatureGraph[Any], kind: Literal["mesh", "plane"] = "mesh", **changes: Any
) -> dict[str, object]:
    return author_volume(
        graph,
        token(graph),
        selection="patch",
        volume="region",
        label="Region",
        kind=kind,
        fit=SurfaceAuthoringReference(feature="plane") if kind == "plane" else None,
        **changes,
    )


def changed_membership(graph: FeatureGraph[Any], ids: list[int]) -> Recipe:
    payload = saved(graph).model_dump(mode="json")
    payload["nodes"][1]["entries"][0]["ids"] = ids
    return Recipe.model_validate(payload)


def rescan(graph: FeatureGraph[Any]) -> FeatureGraph[ScanWorkspace]:
    scan = workspace(6)
    payload = saved(graph).model_dump(mode="json")
    source = recipe(scan).nodes[0]
    payload["nodes"][0] = source.model_dump(mode="json")
    target = SelectionEntry(
        id="rescan",
        scan=source_binding(scan),
        mode="generated",
        source_volume=reference(graph),
    )
    payload["nodes"][1]["entries"].append(target.model_dump(mode="json"))
    return FeatureGraph(scan, Recipe.model_validate(payload), refresh_volumes=False)


def test_conversion_preserves_feature_and_authored_ids_without_implicit_volume() -> (
    None
):
    scan = workspace()
    original = recipe(scan)
    converted = convert_selection(original, scan, "patch")
    node = converted.nodes[1]
    assert isinstance(node, Selection) and node.entries
    assert node.id == original.nodes[1].id
    assert node.entries[0].ids == list(range(25))
    assert node.entries[0].volumes == []
    assert "ids" not in node.model_dump() and "depth" not in node.model_dump()
    assert convert_selection(converted, scan, "patch") == converted
    assert (
        isinstance(original.nodes[1], Selection) and original.nodes[1].entries is None
    )
    with pytest.raises(ValueError, match="loaded scan"):
        _ = convert_selection(original, workspace(6), "patch")


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_capture_and_explicit_create_replace_choice(
    kind: Literal["mesh", "plane"],
) -> None:
    current = graph()
    before = token(current)
    state = create(current, kind)
    assert state["token"] != before
    owner, entry, volume = resolve_volume(saved(current), reference(current))
    assert volume.kind == kind
    assert volume.authoring_fingerprint == authoring_fingerprint(
        saved(current), owner, entry, volume
    )
    geometry = decode_selection_volume(volume.archive)
    assert (
        geometry["provenance"]["source_binding"]
        == source_binding(current.workspace).key
    )
    assert geometry["selected_count"] == 25
    with pytest.raises(ValueError, match="choose replace"):
        _ = create(current, kind)
    with pytest.raises(ValueError, match="choose replace"):
        _ = author_volume(
            current,
            token(current),
            selection="patch",
            volume="missing",
            label="New",
            kind="mesh",
            replace=True,
        )
    _ = create(current, kind, replace=True)
    assert len(resolve_volume(saved(current), reference(current))[1].volumes) == 1


def test_capture_rejects_stale_request_and_capture_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = graph()
    before = token(current)
    payload = saved(current).model_dump(mode="json")
    payload["nodes"][1]["label"] = "Renamed"
    _ = current.replace(Recipe.model_validate(payload), before)
    with pytest.raises(StaleGraph):
        _ = author_volume(
            current,
            before,
            selection="patch",
            volume="region",
            label="Region",
            kind="mesh",
        )
    original = capture_volume

    def racing(*args: Any, **kwargs: Any) -> Any:
        captured = original(*args, **kwargs)
        payload = saved(current).model_dump(mode="json")
        payload["nodes"][1]["label"] = "Another edit"
        _ = current.replace(Recipe.model_validate(payload), token(current))
        return captured

    monkeypatch.setattr(transactions, "capture_volume", racing)
    with pytest.raises(StaleGraph):
        _ = create(current)
    node = saved(current).nodes[1]
    assert isinstance(node, Selection) and node.entries is None


def test_cancellation_after_capture_preserves_live_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = graph()
    before = current.snapshot()
    cancelled = False
    original = capture_volume

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        nonlocal cancelled
        captured = original(*args, **kwargs)
        cancelled = True
        return captured

    monkeypatch.setattr(transactions, "capture_volume", interrupted)
    with pytest.raises(ScanCancelled):
        _ = create(current, cancelled=lambda: cancelled)
    assert current.snapshot() == before


def test_unloaded_options_restore_saved_auto_without_blessing_stale_geometry() -> None:
    original = graph()
    _ = create(original, "plane", options={"outward_thickness": 0.7})
    current = rescan(original)
    ref = reference(current)
    _, _, previous = resolve_volume(saved(current), ref)
    baseline = decode_selection_volume(previous.archive)
    _ = set_volume_options(
        current, token(current), ref, {"outward_thickness": None, "edge_padding": 0.2}
    )
    owner, entry, updated = resolve_volume(saved(current), ref)
    geometry = decode_selection_volume(updated.archive)
    assert (
        geometry["resolved"]["outward_thickness"]
        == baseline["defaults"]["outward_thickness"]
    )
    assert geometry["resolved"]["edge_padding"] == 0.2
    assert updated.authoring_fingerprint == authoring_fingerprint(
        saved(current), owner, entry, updated
    )

    payload = changed_membership(current, [0, 1, 5, 6])
    _ = current.replace(payload, token(current))
    _, _, stale = resolve_volume(saved(current), ref)
    assert stale.authoring_fingerprint != authoring_fingerprint(
        saved(current), *resolve_volume(saved(current), ref)
    )
    _ = set_volume_options(current, token(current), ref, {"edge_padding": 0.3})
    owner, entry, updated = resolve_volume(saved(current), ref)
    assert updated.authoring_fingerprint == stale.authoring_fingerprint
    assert updated.authoring_fingerprint != authoring_fingerprint(
        saved(current), owner, entry, updated
    )


def test_generated_capture_freezes_membership_and_keeps_original_volume() -> None:
    original = graph()
    _ = create(original)
    current = rescan(original)
    old_reference = reference(current)
    _ = author_volume(
        current,
        token(current),
        selection="patch",
        entry="rescan",
        volume="adopted",
        label="Adopted",
        kind="plane",
        fit={"feature": "plane"},
    )
    _, _, adopted = resolve_volume(
        saved(current), reference(current, "adopted", entry="rescan")
    )
    assert adopted.captured_ids and len(adopted.captured_ids) > 3
    assert (
        resolve_volume(saved(current), old_reference)[2].archive
        == resolve_volume(saved(original), old_reference)[2].archive
    )
    assert adopted.captured_inputs == {}
    adopted_reference = reference(current, "adopted", entry="rescan")
    payload = saved(current).model_dump(mode="json")
    payload["nodes"][1]["entries"][1]["source_volume"] = adopted_reference.model_dump(
        mode="json"
    )
    _ = current.replace(Recipe.model_validate(payload), token(current))
    _ = author_volume(
        current,
        token(current),
        selection="patch",
        entry="rescan",
        volume="adopted",
        label="Adopted mesh",
        kind="mesh",
        replace=True,
    )
    owner, entry, replaced = resolve_volume(saved(current), adopted_reference)
    assert replaced.captured_ids == adopted.captured_ids
    assert replaced.authoring_fingerprint == authoring_fingerprint(
        saved(current), owner, entry, replaced
    )
    staged, errors = staged_refresh(current, saved(current))
    assert errors == {} and staged == saved(current)
    _ = current.evaluate(token(current), target="plane")
    assert cast(dict[str, str], current.snapshot()["states"])["plane"] == "ready"


def test_option_cancellation_keeps_previous_accepted_controls() -> None:
    current = graph()
    _ = create(current)
    before = current.snapshot()
    with pytest.raises(ScanCancelled):
        _ = set_volume_options(
            current,
            token(current),
            reference(current),
            {"edge_padding": 0.2},
            cancelled=lambda: True,
        )
    assert current.snapshot() == before


def test_staged_refresh_updates_source_edit_without_live_publication() -> None:
    current = graph()
    _ = create(current, "plane")
    ref = reference(current)
    old = resolve_volume(saved(current), ref)[2]
    _ = current.replace(changed_membership(current, [0, 1, 5, 6]), token(current))
    before = current.snapshot()
    candidate, errors = staged_refresh(current, saved(current))
    assert errors == {}
    owner, entry, updated = resolve_volume(candidate, ref)
    assert updated.archive.sha256 != old.archive.sha256
    assert updated.authoring_fingerprint == authoring_fingerprint(
        candidate, owner, entry, updated
    )
    assert current.snapshot() == before
    assert resolve_volume(saved(current), ref)[2] == old


def test_failed_staged_refresh_retains_archive_and_records_reason() -> None:
    current = graph()
    _ = create(current, "plane")
    ref = reference(current)
    _ = current.replace(changed_membership(current, [0, 1]), token(current))
    before = current.snapshot()
    candidate, errors = staged_refresh(current, saved(current))
    assert candidate == saved(current)
    assert f"{ref.selection}/{ref.entry}/{ref.volume}" in errors
    assert current.snapshot() == before
    with pytest.raises(ScanCancelled):
        _ = staged_refresh(current, saved(current), cancelled=lambda: True)


def test_generated_adoption_freezes_every_input_in_connected_plane_context() -> None:
    scan = workspace()
    payload = recipe(scan).model_dump(mode="json")
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
                "selections": ["other"],
                "kind": "plane",
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
    original = FeatureGraph(scan, Recipe.model_validate(payload), refresh_volumes=False)
    _ = create(original)
    _ = author_volume(
        original,
        token(original),
        selection="other",
        volume="other_region",
        label="Other region",
        kind="mesh",
    )
    other = saved(original).nodes[3]
    assert isinstance(other, Selection) and other.entries
    other_reference = VolumeReference(
        selection="other", entry=other.entries[0].id, volume="other_region"
    )
    current = rescan(original)
    payload = saved(current).model_dump(mode="json")
    payload["nodes"][3]["entries"].append(
        SelectionEntry(
            id="rescan",
            scan=source_binding(current.workspace),
            mode="generated",
            source_volume=other_reference,
        ).model_dump(mode="json")
    )
    _ = current.replace(Recipe.model_validate(payload), token(current))
    _ = author_volume(
        current,
        token(current),
        selection="patch",
        entry="rescan",
        volume="adopted",
        label="Adopted",
        kind="plane",
        fit={"feature": "plane"},
    )
    adopted_reference = reference(current, "adopted", entry="rescan")
    owner, entry, accepted = resolve_volume(saved(current), adopted_reference)
    assert accepted.captured_ids and len(accepted.captured_ids) >= 3
    assert set(accepted.captured_inputs) == {"other"}
    assert len(accepted.captured_inputs["other"]) >= 3
    before = authoring_fingerprint(saved(current), owner, entry, accepted)
    _ = set_volume_options(
        current, token(current), other_reference, {"edge_padding": 0.01}
    )
    assert (
        authoring_fingerprint(
            saved(current), *resolve_volume(saved(current), adopted_reference)
        )
        == before
    )


def test_cancel_during_generated_query_propagates_without_adoption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = graph()
    _ = create(original)
    current = rescan(original)
    before = current.snapshot()
    cancelled = False

    def cancellation() -> bool:
        return cancelled

    def interrupt_query(*args: Any, **kwargs: Any) -> list[int]:
        nonlocal cancelled
        if kwargs.get("cancelled") is not None:
            assert kwargs["cancelled"] is cancellation
            cancelled = True
        return apply_selection_volume(*args, **kwargs)

    monkeypatch.setattr(entry_runtime, "apply_selection_volume", interrupt_query)
    with pytest.raises(ScanCancelled):
        _ = author_volume(
            current,
            token(current),
            selection="patch",
            entry="rescan",
            volume="adopted",
            label="Adopted",
            kind="mesh",
            cancelled=cancellation,
        )
    assert cancelled
    assert current.snapshot() == before


def test_staged_refresh_without_volumes_accepts_an_empty_workspace() -> None:
    empty = Recipe(nodes=[], output=None)
    current = FeatureGraph(ScanWorkspace.empty(), empty, refresh_volumes=False)
    assert staged_refresh(current, empty) == (empty, {})


def test_refresh_reference_filter_leaves_unrequested_volumes_unchanged() -> None:
    current = graph()
    _ = create(current, "plane")
    _ = author_volume(
        current,
        token(current),
        selection="patch",
        volume="other_region",
        label="Other region",
        kind="mesh",
    )
    requested, untouched = reference(current), reference(current, "other_region")
    old = resolve_volume(saved(current), untouched)[2]
    _ = current.replace(changed_membership(current, [0, 1, 5, 6]), token(current))
    edited = saved(current)
    key = f"{requested.selection}/{requested.entry}/{requested.volume}"
    candidate, errors = staged_refresh(current, edited, references={key})
    assert errors == {}
    owner, entry, updated = resolve_volume(candidate, requested)
    assert updated.authoring_fingerprint == authoring_fingerprint(
        candidate, owner, entry, updated
    )
    assert resolve_volume(candidate, untouched)[2] == old
    assert staged_refresh(current, edited, references=set()) == (edited, {})
    assert saved(current) == edited
