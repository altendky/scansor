"""Small generated observations for actual native and packaged Fit UI tests."""

from __future__ import annotations

import base64
import io
import json
import struct
from typing import Any

import numpy as np

from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits


def fit_reference_fixture() -> dict[str, Any]:
    points: list[list[float]] = []
    normals: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []
    ids: dict[str, list[int]] = {}

    def patch(
        name: str, positions: np.ndarray, directions: np.ndarray, periodic: bool
    ) -> None:
        start = len(points)
        height, width, _ = positions.shape
        points.extend(positions.reshape(-1, 3).tolist())
        normals.extend(directions.reshape(-1, 3).tolist())
        ids[name] = list(range(start, len(points)))
        for row in range(height - 1):
            for col in range(width if periodic else width - 1):
                a, b = (
                    start + row * width + col,
                    start + row * width + (col + 1) % width,
                )
                faces.extend([(a, b, a + width), (b, b + width, a + width)])

    angle = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    height, angle_grid = np.meshgrid(np.linspace(-2, 2, 5), angle, indexing="ij")
    for name, slope in [("cylinder", 0.0), ("cone", 0.25)]:
        radius = 2 + slope * height
        positions = np.stack(
            [radius * np.cos(angle_grid), radius * np.sin(angle_grid), height], axis=-1
        )
        directions = np.stack(
            [np.cos(angle_grid), np.sin(angle_grid), np.full_like(height, -slope)],
            axis=-1,
        )
        directions /= np.linalg.norm(directions, axis=-1, keepdims=True)
        patch(name, positions, directions, True)
    elevation, angle_grid = np.meshgrid(np.linspace(-0.9, 1.0, 9), angle, indexing="ij")
    directions = np.stack(
        [
            np.cos(elevation) * np.cos(angle_grid),
            np.cos(elevation) * np.sin(angle_grid),
            np.sin(elevation),
        ],
        axis=-1,
    )
    patch("sphere", np.array([6, 0, 1]) + 1.5 * directions, directions, True)
    for name, y_offset in [("plane", 7), ("plane-other", 14)]:
        y, x = np.meshgrid(np.linspace(-2, 2, 5), np.linspace(-2, 2, 5), indexing="ij")
        positions = np.stack([x, y + y_offset, np.zeros_like(x)], axis=-1)
        directions = np.zeros_like(positions)
        directions[..., 2] = 1
        patch(name, positions, directions, False)

    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode()
    raw = header + b"".join(
        struct.pack("<6f", *point, *normal)
        for point, normal in zip(points, normals, strict=True)
    )
    raw += b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    filename = "fit-reference-composite.ply"
    project = ScanProject()
    accepted = project.commit(
        project.prepare_add(
            io.BytesIO(raw),
            filename,
            ScanLimits(2_000_000, 10_000, 20_000, 100_000_000),
            generation=0,
            token=str(project.accepted.graph.snapshot()["token"]),
        )
    )
    recipe = accepted.graph.export_recipe()
    source = recipe["nodes"][0]["id"]
    recipe["nodes"].extend(
        [
            *[
                {
                    "id": name + "-selection",
                    "label": name + " observations",
                    "operation": "selection",
                    "source": source,
                    "ids": rows,
                }
                for name, rows in ids.items()
            ],
            *[
                {
                    "id": name,
                    "label": "Baseline " + name,
                    "operation": "fit",
                    "kind": kind,
                    "selections": [name + "-selection"],
                }
                for name, kind in [
                    ("cylinder", "cylinder"),
                    ("sphere", "sphere"),
                    ("plane", "plane"),
                    ("plane-other", "plane"),
                ]
            ],
            {
                "id": "axis",
                "label": "Fixed cylinder axis",
                "operation": "axis",
                "source_fit": "cylinder",
            },
            {
                "id": "center",
                "label": "Fixed sphere center",
                "operation": "point",
                "source_fit": "sphere",
            },
            {
                "id": "plane-datum",
                "label": "Perpendicular datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
            },
            {
                "id": "parallel-planes",
                "label": "Solved parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["plane", "plane-other"],
            },
        ]
    )
    recipe["output"] = "parallel-planes"
    return {
        "filename": filename,
        "raw_base64": base64.b64encode(raw).decode(),
        "recipe": recipe,
        "ids": ids,
        "vertices": len(points),
        "triangles": len(faces),
    }


if __name__ == "__main__":
    print(json.dumps(fit_reference_fixture()))
