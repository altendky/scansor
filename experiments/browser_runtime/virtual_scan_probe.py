"""Small generated virtual-scan evidence shared by native Python and Pyodide."""

from __future__ import annotations

import hashlib
import io
import struct
from dataclasses import asdict
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.scan_workspace import ScanLimits, prepare_scan
from experiments.virtual_scan_reduction import ReductionSettings, prepare_virtual_scan

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 16_000_000, chunk_bytes=4096)


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
        original_hash = hashlib.sha256(original.original_bytes).hexdigest()
        settings = ReductionSettings(divisions=2 if refinement_case else 4)
        reduced = prepare_virtual_scan(original, settings, LIMITS)
        workspace = reduced.workspace
        replay = prepare_virtual_scan(
            original,
            ReductionSettings.model_validate_json(settings.model_dump_json()),
            LIMITS,
        )
        assert workspace.original_bytes == replay.workspace.original_bytes
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
        assert hashlib.sha256(original.original_bytes).hexdigest() == original_hash
        assert original.original_bytes == raw
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
        reopened = prepare_scan(
            io.BytesIO(workspace.original_bytes), "renamed.ply", LIMITS
        )
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
                "derived_bytes_sha256": hashlib.sha256(
                    workspace.original_bytes
                ).hexdigest(),
                "component_sizes": components,
                "positions": workspace.local.tolist(),
                "normals": workspace.data.normals.tolist(),
                "triangles": workspace.data.triangles.tolist(),
                "weights": workspace.data.weights.tolist(),
                "total_area": float(np.sum(workspace.data.weights)),
            }
        )
    return reports
