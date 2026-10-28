"""Bounded scan-entry authoring, refresh and transfer evidence for both runtimes."""

from __future__ import annotations

import io
import json
import struct
from typing import Any, cast

from experiments.feature_graph import FeatureGraph, Recipe, Selection, Source
from experiments.scan_project import AcceptedScan, ScanProject
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_entries import VolumeReference
from experiments.selection_volume_authoring import authoring_fingerprint, resolve_volume
from experiments.selection_volume_operations import dispatch
from experiments.selection_volume_transactions import author_volume


def _scan(count: int) -> ScanWorkspace:
    """A tiny binary PLY with exactly representable coordinates and unit normals."""
    if count not in {3, 5}:
        raise ValueError("the entry probe supports only its two bounded grids")
    points = [
        (2.0 * column / (count - 1), 2.0 * row / (count - 1), 0.0)
        for row in range(count)
        for column in range(count)
    ]
    faces: list[tuple[int, int, int]] = []
    for row in range(count - 1):
        for column in range(count - 1):
            a = row * count + column
            faces.extend(((a, a + 1, a + count), (a + 1, a + count + 1, a + count)))
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\nproperty float x\nproperty float y\n"
        "property float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    raw = (
        header
        + b"".join(struct.pack("<6f", *point, 0, 0, 1) for point in points)
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )
    return prepare_scan(
        io.BytesIO(raw),
        f"entry-probe-{count}.ply",
        ScanLimits(8192, 25, 32, 2_000_000, chunk_bytes=1024),
    )


def _source(scan: ScanWorkspace) -> Source:
    assert scan.source_sha256 is not None
    return Source(
        id="scan",
        label="Probe scan",
        operation="source",
        source_sha256=scan.source_sha256,
        reference_sha256=scan.reference_sha256,
        descriptor=scan.source_descriptor,
    )


def _snapshot(graph: FeatureGraph[ScanWorkspace]) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def _plane(state: dict[str, Any]) -> dict[str, Any]:
    value = state["results"]["plane"]
    return {
        "kind": value["kind"],
        "plane_equation": value.get("plane_equation", value["parameters"][:4]),
        "weighted_rms": value["weighted_rms"],
        "ids": value["ids"],
    }


def selection_entries_probe() -> dict[str, Any]:
    """Return numerical/behavioral evidence, excluding archive-dependent hashes."""
    original = _scan(3)
    recipe = Recipe.model_validate(
        {
            "nodes": [
                _source(original).model_dump(mode="json"),
                {
                    "id": "patch",
                    "label": "Probe patch",
                    "operation": "selection",
                    "source": "scan",
                    "ids": list(range(9)),
                },
                {
                    "id": "plane",
                    "label": "Probe plane",
                    "operation": "fit",
                    "kind": "plane",
                    "selections": ["patch"],
                },
            ],
            "output": "plane",
        }
    )
    graph = FeatureGraph(original, recipe)
    _ = author_volume(
        graph,
        str(_snapshot(graph)["token"]),
        selection="patch",
        volume="region",
        label="Probe volume",
        kind="mesh",
    )
    authored = _snapshot(graph)
    payload = authored["recipe"]
    entry = payload["nodes"][1]["entries"][0]
    assert len(entry["volumes"]) == 1
    before_archive = entry["volumes"][0]["archive"]
    entry["ids"] = [0, 1, 3, 4]
    edited = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), authored["token"])
    )
    request_token = str(edited["token"])
    refreshed = cast(dict[str, Any], graph.evaluate(request_token, target="plane"))
    assert refreshed["token"] != request_token
    assert graph.publication_token(request_token) == refreshed["token"]
    assert refreshed["evaluation_origin_token"] == request_token
    owner = Recipe.model_validate(refreshed["recipe"]).nodes[1]
    assert isinstance(owner, Selection) and owner.entries
    accepted = owner.entries[0].volumes[0]
    assert accepted.archive.model_dump(mode="json") != before_archive
    assert refreshed["memberships"]["patch"] == [0, 1, 3, 4]

    target = _scan(5)
    assert target.source is not None
    project = ScanProject()
    positions, indices = original.display_buffers()
    original_accepted = AcceptedScan(1, original, graph, positions, indices)
    limits = ScanLimits(8192, 25, 32, 2_000_000, chunk_bytes=1024)
    candidate = project.prepare_replace(
        target.source,
        target.name,
        limits,
        generation=0,
        token=str(project.accepted.graph.snapshot()["token"]),
        matching=original_accepted,
    )
    assert project.accepted.workspace.descriptor is None
    assert _snapshot(graph) == refreshed
    transferred = project.commit_replace(candidate).graph
    assert transferred.workspace.source_sha256 == target.source_sha256
    assert transferred.workspace.source_sha256 != original.source_sha256
    waiting = cast(
        dict[str, Any],
        transferred.evaluate(str(_snapshot(transferred)["token"]), target="plane"),
    )
    assert waiting["states"]["patch"] == waiting["states"]["plane"] == "needs_input"
    assert waiting["errors"] == {} and "plane" not in waiting["results"]
    assert not transferred.needs_evaluation(str(waiting["token"]), ["plane"])
    assert waiting["diagnostics"]["patch"]["action"] == "choose_source_volume"

    reference = VolumeReference(
        selection="patch", entry=owner.entries[0].id, volume="region"
    )
    chosen = waiting["recipe"]
    chosen["nodes"][1]["entries"][1]["source_volume"] = reference.model_dump(
        mode="json"
    )
    choosing = transferred.replace(Recipe.model_validate(chosen), str(waiting["token"]))
    ready = cast(
        dict[str, Any], transferred.evaluate(str(choosing["token"]), target="plane")
    )
    assert ready["states"]["plane"] == "ready" and ready["errors"] == {}
    ids = ready["memberships"]["patch"]
    assert len(ids) > 4 and 24 not in ids
    stored = Recipe.model_validate_json(json.dumps(ready["recipe"], allow_nan=False))
    replay_project = ScanProject()
    opened = replay_project.prepare_open(
        stored.model_dump(mode="json"),
        generation=0,
        token=str(replay_project.accepted.graph.snapshot()["token"]),
        confirmed_discard=True,
        stream=target.source,
        filename="renamed-target.ply",
        limits=limits,
    )
    reopened = replay_project.commit_open(opened).graph
    replay = cast(
        dict[str, Any],
        reopened.evaluate(str(_snapshot(reopened)["token"]), target="plane"),
    )
    assert replay["token"] == ready["token"]
    assert replay["recipe"] == ready["recipe"]
    assert replay["memberships"] == ready["memberships"]
    assert _plane(replay) == _plane(ready)
    final_owner = stored.nodes[1]
    assert isinstance(final_owner, Selection) and final_owner.entries
    assert final_owner.entries[0].ids == [0, 1, 3, 4]
    assert final_owner.entries[0].volumes[0].archive == accepted.archive
    assert final_owner.entries[1].ids is None
    assert final_owner.entries[1].volumes == []

    # Exercise the same bounded commands used by the owned-volume editor, in
    # both native Python and the actually shipped numerical browser runtime.
    ui_choices = dispatch(
        reopened,
        "getSelectionVolumeChoices",
        {
            "token": replay["token"],
            "selection": "patch",
        },
    )
    preview_positions: list[float] = []
    preview_normals: list[float] = []
    offset = 0
    pages = 0
    while True:
        page = dispatch(
            reopened,
            "getSelectionVolumePreview",
            {
                "token": replay["token"],
                "reference": reference.model_dump(),
                "archive_sha256": accepted.archive.sha256,
                "offset": offset,
                "limit": 3,
            },
        )
        preview_positions.extend(page["positions"])
        preview_normals.extend(page["normals"])
        pages += 1
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    adopting = dispatch(
        reopened,
        "authorSelectionVolume",
        {
            "token": replay["token"],
            "selection": "patch",
            "entry": final_owner.entries[1].id,
            "mode": "replace",
            "target": reference.model_dump(),
            "label": "Adopted target support",
            "kind": "plane",
            "fit": {"feature": "plane"},
        },
    )
    adopted_recipe = Recipe.model_validate(adopting["recipe"])
    adopted_ref = VolumeReference(
        selection="patch", entry=final_owner.entries[1].id, volume="region"
    )
    adopted_owner, adopted_entry, adopted_volume = resolve_volume(
        adopted_recipe, adopted_ref
    )
    assert adopted_entry.source_volume == adopted_ref
    assert adopted_volume.captured_ids == ids
    assert adopted_volume.authoring_fingerprint == authoring_fingerprint(
        adopted_recipe, adopted_owner, adopted_entry, adopted_volume
    )
    assert adopted_owner.entries and adopted_owner.entries[0].ids == [0, 1, 3, 4]
    assert adopted_owner.entries[0].volumes == []
    adopted_state = cast(
        dict[str, Any], reopened.evaluate(str(adopting["token"]), target="plane")
    )
    assert adopted_state["states"]["plane"] == "ready"
    return {
        "format": "selection-entries-probe-v1",
        "source_vertices": len(original.local),
        "target_vertices": len(target.local),
        "entry_volume_counts": [len(item.volumes) for item in final_owner.entries],
        "fit_count": sum(node.operation == "fit" for node in stored.nodes),
        "source_membership": refreshed["memberships"]["patch"],
        "source_fit": _plane(refreshed),
        "refresh": {
            "archive_changed": True,
            "token_changed": True,
            "request_receipt_matches": True,
        },
        "replacement": {
            "staged_without_publication": True,
            "new_entry_pending": True,
            "source_observations_changed": True,
            "reopen_active_bytes_only": True,
        },
        "waiting": {
            "states": {key: waiting["states"][key] for key in ("patch", "plane")},
            "errors": waiting["errors"],
            "action": waiting["diagnostics"]["patch"]["action"],
            "settled": True,
        },
        "transferred": {
            "states": {key: ready["states"][key] for key in ("patch", "plane")},
            "membership": ids,
            "coverage": ready["derived"]["patch"]["coverage"],
            "fit": _plane(ready),
        },
        "preserved_original": True,
        "generated_only": True,
        "replay_equal": True,
        "volume_interface": {
            "surface_kinds": [
                choice["kind"]
                for choice in ui_choices["surfaces"]
                if choice["eligible"]
            ],
            "source_choices": len(ui_choices["volumes"]),
            "preview_pages": pages,
            "support_positions": preview_positions,
            "support_normals": preview_normals,
            "adopted_captured_ids": adopted_volume.captured_ids,
            "adopted_membership": adopted_state["memberships"]["patch"],
            "adopted_fit": _plane(adopted_state),
            "reparented_and_fresh": True,
            "original_paint_preserved": True,
        },
    }
