"""Small deterministic surface-volume evidence shared by native and worker runs."""

from __future__ import annotations

import json
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.selection_volume import (
    apply_selection_volume,
    build_selection_volume,
    selection_volume_coverage,
    validate_selection_volume,
)


def _grid(count: int, scale: float) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    first, second = np.meshgrid(np.linspace(0, 6, count), np.linspace(0, 6, count))
    points = scale * np.column_stack(
        (first.ravel(), second.ravel(), np.zeros(first.size))
    )
    faces: list[tuple[int, int, int]] = []
    for row in range(count - 1):
        for column in range(count - 1):
            index = row * count + column
            faces.extend(
                (
                    (index, index + 1, index + count),
                    (index + 1, index + count + 1, index + count),
                )
            )
    return points, np.asarray(faces, dtype=np.int64)


def selection_volume_probe() -> list[dict[str, Any]]:
    """Transfer a painted annulus to denser aligned data without closing its hole."""
    reports: list[dict[str, Any]] = []
    for scale in (1.0, 16.0):
        source, faces = _grid(7, scale)
        normals = np.tile([0.0, 0, 1], (len(source), 1))
        selected = np.flatnonzero(
            (source[:, 0] < 2 * scale)
            | (source[:, 0] > 4 * scale)
            | (source[:, 1] < 2 * scale)
            | (source[:, 1] > 4 * scale)
        ).tolist()
        target, target_faces = _grid(13, scale)
        target_normals = np.tile([0.0, 0, 1], (len(target), 1))
        partial_faces = target_faces[
            np.mean(target[target_faces, 0], axis=1) < 3 * scale
        ]
        for kind in ("mesh", "plane"):
            volume = build_selection_volume(
                source,
                normals,
                faces,
                selected,
                kind=kind,
                fit={"kind": "plane", "plane_equation": [0.0, 0, 1, 0]}
                if kind == "plane"
                else None,
                provenance={"probe": "painted-annulus", "scale": scale},
            )
            encoded = json.loads(json.dumps(volume, allow_nan=False))
            restored = validate_selection_volume(encoded)
            original_ids = apply_selection_volume(
                source, normals, np.ones(len(source)), restored
            )
            assert original_ids == selected
            dense_ids = apply_selection_volume(
                target, target_normals, np.ones(len(target)), restored, chunk_size=7
            )
            assert dense_ids == apply_selection_volume(
                target, target_normals, np.ones(len(target)), volume, chunk_size=64
            )
            assert 6 * 13 + 6 not in dense_ids, "painted hole was filled"
            assert len(dense_ids) > len(selected)
            reverse_ids = apply_selection_volume(
                source, -normals, np.ones(len(source)), restored
            )
            assert not reverse_ids, "opposite-facing surface was admitted"
            full = selection_volume_coverage(
                target, target_normals, target_faces, restored
            )
            partial = selection_volume_coverage(
                target, target_normals, partial_faces, restored
            )
            reports.append(
                {
                    "kind": kind,
                    "scale": scale,
                    "volume": restored,
                    "source_ids": original_ids,
                    "dense_ids": dense_ids,
                    "reverse_ids": reverse_ids,
                    "full_coverage": full,
                    "partial_coverage": partial,
                }
            )
    return reports
