"""Small generated virtual-scan evidence shared by native Python and Pyodide."""

from __future__ import annotations

import hashlib
import io
import struct
from copy import deepcopy
from dataclasses import asdict
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from experiments.feature_graph import Recipe
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_volume_transactions import author_volume
from experiments.virtual_scan_reduction import ReductionSettings, prepare_virtual_scan

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 16_000_000, chunk_bytes=4096)


def _source_hash(workspace: ScanWorkspace) -> str:
    assert workspace.source is not None
    digest = hashlib.sha256()
    with workspace.source.open() as stream:
        while chunk := stream.read(4096):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_source(workspace: ScanWorkspace, expected: bytes) -> None:
    assert workspace.source is not None and workspace.source.byte_length == len(
        expected
    )
    offset = 0
    with workspace.source.open() as stream:
        while chunk := stream.read(4096):
            assert chunk == expected[offset : offset + len(chunk)]
            offset += len(chunk)
    assert offset == len(expected)


def _sheet_bytes(
    scale: float, sheets: int, opposed: bool, refinement_case: bool = False
) -> bytes:
    """Independently pack dyadic grids; nearby sheets share cells, never edges."""
    count = 17
    points = [
        (scale * column / 16, scale * row / 16, scale * sheet / 64)
        for sheet in range(sheets)
        for row in range(count)
        for column in range(count)
    ]
    faces: list[tuple[int, int, int]] = []
    for sheet in range(sheets):
        for row in range(count - 1):
            for column in range(count - 1):
                a = sheet * count * count + row * count + column
                pair = ((a, a + 1, a + count), (a + 1, a + count + 1, a + count))
                faces.extend(
                    pair
                    if sheet == 0 or not opposed
                    else tuple((a, c, b) for a, b, c in pair)
                )
    normals = [
        (0.0, 0.0, 1.0 if index < count * count or not opposed else -1.0)
        for index in range(len(points))
    ]
    if refinement_case:
        # A disconnected patch whose initial connected cell collapses reverse a
        # retained face. Conservative refinement must split those clusters.
        offset = len(points)
        points.extend(
            [
                (0.4, 0.4, 0),
                (0.6, 0.1, 0),
                (0.1, 0.6, 0),
                (0.4, 0.4, 0),
                (0.6, 0.45, 0),
                (0.45, 0.6, 0),
            ]
        )
        normals.extend([(0.0, 0.0, 1.0)] * 6)
        faces.extend(
            (offset + a, offset + b, offset + c)
            for a, b, c in ((0, 3, 1), (1, 4, 2), (2, 5, 0), (3, 4, 5))
        )
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\nproperty float x\nproperty float y\n"
        "property float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    return (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(points, normals, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )


def _component_sizes(triangles: NDArray[np.int32], vertices: int) -> list[int]:
    """An independent graph traversal checks that nearby sheets stay separate."""
    neighbors: list[set[int]] = [set[int]() for _ in range(vertices)]
    for face in triangles:
        triangle = [int(value) for value in face]
        for vertex in triangle:
            neighbors[vertex].update(triangle)
    unseen = set(range(vertices))
    sizes: list[int] = []
    while unseen:
        pending = [min(unseen)]
        unseen.remove(pending[0])
        size = 0
        while pending:
            vertex = pending.pop()
            size += 1
            for other in neighbors[vertex]:
                if other in unseen:
                    unseen.remove(other)
                    pending.append(other)
        sizes.append(size)
    return sorted(sizes)


def virtual_scan_probe() -> list[dict[str, Any]]:
    """Verify reproducible reduction, disconnected support and derived area mass."""
    reports: list[dict[str, Any]] = []
    for scale, sheets, opposed, refinement_case in (
        (1.0, 1, False, False),
        (16.0, 1, False, False),
        (1.0, 2, False, False),
        (1.0, 2, True, False),
        (1.0, 1, False, True),
    ):
        raw = _sheet_bytes(scale, sheets, opposed, refinement_case)
        original = prepare_scan(io.BytesIO(raw), "virtual-probe.ply", LIMITS)
        original_hash = _source_hash(original)
        settings = ReductionSettings(divisions=2 if refinement_case else 4)
        reduced = prepare_virtual_scan(original, settings, LIMITS)
        workspace = reduced.workspace
        replay = prepare_virtual_scan(
            original,
            ReductionSettings.model_validate_json(settings.model_dump_json()),
            LIMITS,
        )
        assert _source_hash(workspace) == _source_hash(replay.workspace)
        assert reduced.derivation == replay.derivation
        assert reduced.diagnostics == replay.diagnostics
        if refinement_case:
            assert reduced.diagnostics.refinement_passes > 0
            assert reduced.diagnostics.split_vertices > 0
            expected_face = original.local[np.array([-3, -2, -1])]
            assert any(
                np.array_equal(workspace.local[face], expected_face)
                for face in workspace.data.triangles
            )
        assert _source_hash(original) == original_hash
        _assert_source(original, raw)
        assert workspace.descriptor is not None and original.descriptor is not None
        assert workspace.reference_sha256 == replay.workspace.reference_sha256
        assert len(workspace.local) < len(original.local) // 2
        assert 0 < len(workspace.data.triangles) < len(original.data.triangles)
        assert workspace.descriptor.source_sha256 != original.descriptor.source_sha256
        assert (
            workspace.descriptor.interpretation_sha256
            == original.descriptor.interpretation_sha256
        )
        np.testing.assert_array_equal(workspace.origin, original.origin)
        np.testing.assert_array_equal(workspace.frame, original.frame)
        np.testing.assert_array_equal(
            workspace.local.min(axis=0), original.local.min(axis=0)
        )
        np.testing.assert_array_equal(
            workspace.local.max(axis=0), original.local.max(axis=0)
        )
        np.testing.assert_allclose(np.linalg.norm(workspace.data.normals, axis=1), 1)
        assert set(workspace.local[:, 2].tolist()) == {
            scale * sheet / 64 for sheet in range(sheets)
        }
        assert np.all(np.ptp(workspace.local[workspace.data.triangles, 2], axis=1) == 0)
        components = _component_sizes(workspace.data.triangles, len(workspace.local))
        assert len(components) == sheets + int(refinement_case)
        expected_weights = np.zeros(len(workspace.local))
        for triangle in workspace.data.triangles:
            first, second, third = workspace.local[triangle]
            area = float(np.linalg.norm(np.cross(second - first, third - first))) / 2
            assert area > 0
            for vertex in triangle:
                expected_weights[vertex] += area / 3
        np.testing.assert_allclose(
            workspace.data.weights, expected_weights, rtol=1e-13, atol=0
        )
        assert np.all(workspace.data.weights > 0)
        np.testing.assert_allclose(
            np.sum(workspace.data.weights),
            np.sum(original.data.weights) if refinement_case else sheets * scale**2,
            rtol=1e-13,
        )
        # Every retained normal must still identify its own disconnected sheet.
        expected_sign = np.where((workspace.local[:, 2] == 0) | (not opposed), 1, -1)
        np.testing.assert_array_equal(workspace.data.normals[:, 2], expected_sign)
        assert workspace.source is not None
        reopened = prepare_scan(workspace.source, "renamed.ply", LIMITS)
        assert reopened.reference_sha256 == workspace.reference_sha256
        np.testing.assert_array_equal(reopened.data.weights, workspace.data.weights)
        reports.append(
            {
                "scale": scale,
                "sheets": sheets,
                "opposed": opposed,
                "refinement_case": refinement_case,
                "diagnostics": asdict(reduced.diagnostics),
                "derivation": reduced.derivation.model_dump(mode="json"),
                "original_descriptor": original.descriptor.semantic_record(),
                "derived_descriptor": workspace.descriptor.semantic_record(),
                "original_binding": original.reference_sha256,
                "derived_binding": workspace.reference_sha256,
                "derived_bytes_sha256": _source_hash(workspace),
                "component_sizes": components,
                "positions": workspace.local.tolist(),
                "normals": workspace.data.normals.tolist(),
                "triangles": workspace.data.triangles.tolist(),
                "weights": workspace.data.weights.tolist(),
                "total_area": float(np.sum(workspace.data.weights)),
            }
        )
    return reports


def _project_state(project: ScanProject) -> dict[str, Any]:
    return cast(dict[str, Any], project.accepted.graph.snapshot())


def _plane_record(state: dict[str, Any]) -> dict[str, Any]:
    value = state["results"]["plane"]
    return {
        "kind": value["kind"],
        "parameters": value["parameters"],
        "weighted_rms": value["weighted_rms"],
        "ids": value["ids"],
    }


def _portable_intent(recipe: dict[str, Any]) -> dict[str, Any]:
    """Retain declared intent and archive content IDs, excluding compression bytes."""
    result = deepcopy(recipe)
    for node in result["nodes"]:
        if node["operation"] != "selection":
            continue
        for entry in node.get("entries") or []:
            for volume in entry["volumes"]:
                archive = cast(dict[str, Any], volume["archive"])
                _ = archive.pop("data")
    return result


def virtual_scan_transactions_probe() -> dict[str, Any]:
    """Author on reduced observations, transfer to the original and reconstruct both."""
    raw = _sheet_bytes(1.0, 1, False)
    project = ScanProject()
    initial = project.accepted
    candidate = project.prepare_add(
        io.BytesIO(raw),
        "transaction-original.ply",
        LIMITS,
        generation=initial.generation,
        token=str(_project_state(project)["token"]),
    )
    original = project.commit(candidate).workspace
    before = project.accepted
    candidate = project.prepare_virtual_scan(
        ReductionSettings(divisions=4),
        LIMITS,
        generation=before.generation,
        token=str(_project_state(project)["token"]),
    )
    assert project.accepted is before
    reduced = project.commit_replace(candidate).workspace
    assert len(reduced.local) < len(original.local)
    payload = deepcopy(_project_state(project)["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "patch",
                "label": "Coarse painted patch",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(len(reduced.local))),
            },
            {
                "id": "plane",
                "label": "Shared plane",
                "operation": "fit",
                "kind": "plane",
                "selections": ["patch"],
            },
        ]
    )
    payload["output"] = "plane"
    _ = project.accepted.graph.replace(
        Recipe.model_validate(payload), str(_project_state(project)["token"])
    )
    _ = author_volume(
        project.accepted.graph,
        str(_project_state(project)["token"]),
        selection="patch",
        volume="footprint",
        label="Coarse footprint",
        kind="mesh",
    )
    coarse = cast(
        dict[str, Any],
        project.accepted.graph.ensure_current(
            str(_project_state(project)["token"]), ["plane"]
        ),
    )
    assert coarse["states"]["plane"] == "ready"
    _ = project.commit_replace(
        project.prepare_activate_scan(
            original.reference_sha256,
            LIMITS,
            generation=project.accepted.generation,
            token=str(coarse["token"]),
        )
    )
    waiting = _project_state(project)
    assert waiting["states"]["plane"] == "needs_input"
    assert waiting["results"] == {}
    assert waiting["diagnostics"]["patch"]["action"] == "choose_source_volume"
    payload = deepcopy(waiting["recipe"])
    coarse_entry, target_entry = payload["nodes"][1]["entries"]
    assert target_entry["source_volume"] is None
    target_entry["source_volume"] = {
        "selection": "patch",
        "entry": coarse_entry["id"],
        "volume": "footprint",
    }
    payload["nodes"][2]["label"] = "Edited on original"
    _ = project.accepted.graph.replace(
        Recipe.model_validate(payload), str(waiting["token"])
    )
    full = cast(
        dict[str, Any],
        project.accepted.graph.ensure_current(
            str(_project_state(project)["token"]), ["plane"]
        ),
    )
    assert full["states"]["plane"] == "ready" and full["errors"] == {}
    assert full["memberships"]["patch"] == list(range(len(original.local)))
    saved_full = deepcopy(full["recipe"])
    _ = project.commit_replace(
        project.prepare_activate_scan(
            reduced.reference_sha256,
            LIMITS,
            generation=project.accepted.generation,
            token=str(full["token"]),
        )
    )
    returned = _project_state(project)
    assert returned["results"] == {}
    assert returned["recipe"]["nodes"][2]["label"] == "Edited on original"
    assert len(returned["recipe"]["nodes"][1]["entries"]) == 2
    coarse_again = cast(
        dict[str, Any],
        project.accepted.graph.ensure_current(str(returned["token"]), ["plane"]),
    )
    assert _plane_record(coarse_again) == _plane_record(coarse)
    assert coarse_again["memberships"]["patch"] == list(range(len(reduced.local)))
    reopens: list[dict[str, Any]] = []
    for saved, active, expected in (
        (saved_full, original, full),
        (coarse_again["recipe"], reduced, coarse_again),
    ):
        reopened = ScanProject()
        prepared = reopened.prepare_open(
            saved,
            generation=reopened.accepted.generation,
            token=str(_project_state(reopened)["token"]),
            confirmed_discard=True,
            stream=io.BytesIO(raw),
            filename="renamed-original.ply",
            limits=LIMITS,
        )
        accepted = reopened.commit_open(prepared)
        assert accepted.workspace.reference_sha256 == active.reference_sha256
        assert len(accepted.retained_sources) == 2
        _assert_source(accepted.retained_sources[0], raw)
        assert _source_hash(accepted.retained_sources[1]) == _source_hash(reduced)
        evaluated = cast(
            dict[str, Any],
            accepted.graph.ensure_current(
                str(_project_state(reopened)["token"]), ["plane"]
            ),
        )
        assert evaluated["recipe"] == saved
        assert _plane_record(evaluated) == _plane_record(expected)
        reopens.append(
            {
                "active_binding": active.reference_sha256,
                "intent": _portable_intent(evaluated["recipe"]),
                "membership": evaluated["memberships"]["patch"],
                "fit": _plane_record(evaluated),
                "retained_bindings": [
                    item.reference_sha256 for item in accepted.retained_sources
                ],
            }
        )
    return {
        "format": "virtual-scan-transactions-probe-v1",
        "derivation": coarse["recipe"]["nodes"][0]["virtual_scan"],
        "original_vertices": len(original.local),
        "reduced_vertices": len(reduced.local),
        "private_preparation": True,
        "waiting": {
            "states": {key: waiting["states"][key] for key in ("patch", "plane")},
            "action": waiting["diagnostics"]["patch"]["action"],
            "results": waiting["results"],
        },
        "coarse": {
            "membership": coarse["memberships"]["patch"],
            "fit": _plane_record(coarse),
        },
        "full": {
            "membership": full["memberships"]["patch"],
            "fit": _plane_record(full),
            "coverage": full["derived"]["patch"]["coverage"],
        },
        "returned_label": returned["recipe"]["nodes"][2]["label"],
        "reopens": reopens,
    }
