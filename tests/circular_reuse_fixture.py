"""Independent tilted circular-hole observations and source-only authored work."""

from __future__ import annotations

import io
import struct
from typing import Any, cast

import numpy as np

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_volume_operations import dispatch


def circular_fixture(
    *, count: int = 5, missing: int | None = None, bore_selection: str = "bore_input"
) -> tuple[
    bytes, ScanWorkspace, Recipe, dict[str, Any], dict[int, dict[str, list[int]]]
]:
    axis = np.asarray([0.2, -0.1, 1.0])
    axis /= np.linalg.norm(axis)
    origin = np.asarray([3.0, -2.0, 1.0])
    u = np.cross([0, 1, 0], axis)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    rows: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []
    expected: dict[int, dict[str, list[int]]] = {}
    angles = np.arange(24) * (2 * np.pi / 24)
    for occurrence in range(count):
        expected[occurrence] = {"bore": [], "chamfer": []}
        if occurrence == missing:
            continue
        phase = 2 * np.pi * occurrence / count
        radial = np.cos(phase) * u + np.sin(phase) * v
        tangent = np.cross(axis, radial)
        center = origin + (5 + 0.01 * occurrence) * radial
        for role, heights in (
            ("bore", np.linspace(-0.6, 0.6, 4)),
            ("chamfer", np.linspace(0.8, 1.0, 3)),
        ):
            first = len(rows)
            for height in heights:
                radius = (
                    0.5
                    + 0.003 * occurrence
                    + (0.4 * (height - 0.8) if role == "chamfer" else 0)
                )
                for angle in angles:
                    normal = np.cos(angle) * radial + np.sin(angle) * tangent
                    point = center + height * axis + radius * normal
                    if role == "chamfer":
                        normal = normal - 0.4 * axis
                        normal /= np.linalg.norm(normal)
                    rows.append([*point, *normal])
                    expected[occurrence][role].append(len(rows) - 1)
            for ring in range(len(heights) - 1):
                for index in range(24):
                    a, b = (
                        first + ring * 24 + index,
                        first + ring * 24 + (index + 1) % 24,
                    )
                    faces.extend([(a, b, a + 24), (b, b + 24, a + 24)])
    # A coincident opposite-facing sheet is a deliberate orientation distractor.
    distractor = expected.get(2, {}).get("bore", [])
    if distractor:
        start = len(rows)
        rows.extend(
            [
                [*rows[index][:3], *(-np.asarray(rows[index][3:]))]
                for index in distractor
            ]
        )
        source_start = distractor[0]
        faces.extend(
            [
                cast(
                    tuple[int, int, int],
                    tuple(start + index - source_start for index in face),
                )
                for face in list(faces)
                if all(index in distractor for index in face)
            ]
        )
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        + f"element vertex {len(rows)}\n"
        + "property float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        + f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode()
    raw = (
        header
        + b"".join(struct.pack("<6f", *row) for row in rows)
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )
    workspace = prepare_scan(
        io.BytesIO(raw),
        "circular-holes.ply",
        ScanLimits(2_000_000, 30_000, 60_000, 96_000_000),
    )
    point = origin - axis * origin[2] / axis[2]
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "operation": "source",
            "label": "Tilted circular holes",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
            "descriptor": workspace.descriptor.model_dump()
            if workspace.descriptor
            else None,
        },
        {
            "id": "pattern_axis",
            "operation": "axis",
            "label": "Translated tilted pattern axis",
            "initial_parameters": [
                point[0],
                point[1],
                axis[0] / axis[2],
                axis[1] / axis[2],
            ],
        },
    ]
    input_ids = {"bore": bore_selection, "chamfer": "chamfer_input"}
    for role in ("bore", "chamfer"):
        nodes.append(
            {
                "id": input_ids[role],
                "operation": "selection",
                "label": role + " source observations",
                "source": "scan",
                "ids": expected[0][role],
            }
        )
        nodes.append(
            {
                "id": role,
                "operation": "fit",
                "label": role,
                "kind": "cylinder" if role == "bore" else "cone",
                "selections": [input_ids[role]],
            }
        )
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate(
            {"schema_version": 2, "nodes": nodes, "output": "chamfer"}
        ),
    )
    volumes: list[dict[str, Any]] = []
    for role in ("bore", "chamfer"):
        _ = dispatch(
            graph,
            "authorSelectionVolume",
            {
                "token": graph.snapshot()["token"],
                "selection": input_ids[role],
                "mode": "create",
                "volume": role + "_volume",
                "label": role + " captured footprint",
                "kind": "mesh",
                "options": {
                    "inward_thickness": 0.08,
                    "outward_thickness": 0.08,
                    "edge_padding": 0.08,
                    "normal_angle_degrees": 15,
                },
            },
        )
        selection = next(
            item
            for item in cast(dict[str, Any], graph.snapshot()["recipe"])["nodes"]
            if item["id"] == input_ids[role]
        )
        volumes.append(
            {
                "fit": role,
                "source_selection": input_ids[role],
                "volume": {
                    "selection": input_ids[role],
                    "entry": selection["entries"][0]["id"],
                    "volume": role + "_volume",
                },
            }
        )
    recipe = Recipe.model_validate(graph.snapshot()["recipe"])
    return (
        raw,
        workspace,
        recipe,
        {"axis": "pattern_axis", "count": count, "volumes": volumes},
        expected,
    )
