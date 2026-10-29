"""Small independent offset observations for actual direction-alignment UI runs."""

from __future__ import annotations

import base64
import io
import json
import struct
from typing import Any

import numpy as np

from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits
from experiments.surface_primitives import basis


def direction_ui_fixture() -> dict[str, Any]:
    points: list[list[float]] = []
    normals: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []
    ids: dict[str, list[int]] = {}
    truth: dict[str, Any] = {}

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
                a = start + row * width + col
                b = start + row * width + (col + 1) % width
                faces.extend([(a, b, a + width), (b, b + width, a + width)])

    for name, anchor, tilt, radius in [
        ("axis-cylinder", [8.0, -3.0, 0.0], [0.055, -0.015, 1.0], 2.0),
        ("plane-cylinder", [-8.0, -3.0, 0.0], [-0.025, 0.045, 1.0], 2.5),
        ("joint-cylinder", [0.0, 7.0, 0.0], [0.08, 0.0, 1.0], 1.75),
    ]:
        direction = np.asarray(tilt)
        direction /= np.linalg.norm(direction)
        u, v = basis(direction)
        h, theta = np.meshgrid(
            np.linspace(-2.0, 2.0, 7),
            np.linspace(0.0, 2 * np.pi, 32, endpoint=False),
            indexing="ij",
        )
        radial = np.cos(theta)[..., None] * u + np.sin(theta)[..., None] * v
        patch(
            name,
            np.asarray(anchor) + h[..., None] * direction + radius * radial,
            radial,
            True,
        )
        truth[name] = {
            "direction": direction.tolist(),
            "anchor": anchor,
            "radius": radius,
        }
    normal = np.array([0.0, 0.07, 1.0])
    normal /= np.linalg.norm(normal)
    u, v = basis(normal)
    x, y = np.meshgrid(
        np.linspace(-3.0, 3.0, 9), np.linspace(-2.0, 2.0, 7), indexing="ij"
    )
    patch(
        "joint-plane",
        np.array([0.0, 7.0, 6.0]) + x[..., None] * u + y[..., None] * v,
        np.broadcast_to(normal, (*x.shape, 3)),
        False,
    )
    truth["joint-plane"] = {"direction": normal.tolist()}
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode()
    raw = header + b"".join(
        struct.pack("<6f", *p, *n) for p, n in zip(points, normals, strict=True)
    )
    raw += b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    project = ScanProject()
    accepted = project.commit(
        project.prepare_add(
            io.BytesIO(raw),
            "direction-composite.ply",
            ScanLimits(2_000_000, 10_000, 20_000, 100_000_000),
            generation=0,
            token=str(project.accepted.graph.snapshot()["token"]),
        )
    )
    recipe = accepted.graph.export_recipe()
    source = recipe["nodes"][0]["id"]
    recipe["nodes"].extend(
        [
            {
                "id": "axis-start",
                "label": "Fixed axis start",
                "operation": "point",
                "initial_coordinates": [0.0, 0.0, 0.0],
            },
            {
                "id": "axis-end",
                "label": "Fixed axis end",
                "operation": "point",
                "initial_coordinates": [0.0, 0.0, 2.0],
            },
            {
                "id": "fixed-axis",
                "label": "Fixed axis",
                "operation": "axis",
                "source_points": ["axis-start", "axis-end"],
            },
            {
                "id": "fixed-plane",
                "label": "Fixed plane",
                "operation": "reference_plane",
                "axis": "fixed-axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": -12.0,
            },
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
                    "label": name.replace("-", " ").title(),
                    "operation": "fit",
                    "kind": "plane" if name == "joint-plane" else "cylinder",
                    "selections": [name + "-selection"],
                }
                for name in ids
            ],
        ]
    )
    recipe["output"] = "joint-plane"
    return {
        "filename": "direction-composite.ply",
        "raw_base64": base64.b64encode(raw).decode(),
        "recipe": recipe,
        "ids": ids,
        "truth": truth,
        "vertices": len(points),
        "triangles": len(faces),
    }


if __name__ == "__main__":
    print(json.dumps(direction_ui_fixture()))
