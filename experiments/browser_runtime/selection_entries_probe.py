"""Bounded scan-entry authoring, refresh and transfer evidence for both runtimes."""

from __future__ import annotations

import io
import json
import struct
from typing import Any, cast

from experiments.feature_graph import FeatureGraph, Recipe, Selection, Source
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_entries import (
    SelectionEntry,
    VolumeReference,
    source_binding,
)
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
    portable = json.loads(json.dumps(refreshed["recipe"], allow_nan=False))
    portable["nodes"][0] = _source(target).model_dump(mode="json")
    target_entry = SelectionEntry(
        id="target", scan=source_binding(target), mode="generated"
    )
    portable["nodes"][1]["entries"].append(target_entry.model_dump(mode="json"))
    transferred = FeatureGraph(target, Recipe.model_validate(portable))
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
    reopened = FeatureGraph(target, stored)
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
    }
