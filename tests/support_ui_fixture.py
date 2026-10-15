"""The two frozen support-probe sources and seed-free actions for real UI tests."""

import base64
import io
import struct
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import numpy as np

from experiments.browser_runtime.probe import create_fixture
from experiments.feature_graph import Recipe, Selection, SurfaceFit
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits


def support_ui_fixture(kind: Literal["cylinder", "cone"]) -> dict[str, Any]:
    """Serialize the unchanged original120-row probe geometry; do not fit it."""
    with TemporaryDirectory(prefix="scansor-support-ui-") as directory:
        fixture = Path(directory) / "fixture.npz"
        create_fixture(fixture)
        with np.load(fixture, allow_pickle=False) as data:
            index = 0 if kind == "cylinder" else 1
            points = data["support_points"][index].copy()
            normals = data["support_normals"][index].copy()
    rotation = np.array([[0.0, 0, -1], [0, 1, 0], [1, 0, 0]])
    translation = np.array([20.0, 10, 30])
    positions = np.vstack((points, points @ rotation + translation))
    directions = np.vstack((normals, normals @ rotation))
    faces = [
        tuple(offset + vertex for vertex in face)
        for offset in (0, 60)
        for row in range(4)
        for column in range(12)
        for a, b in [(12 * row + column, 12 * row + (column + 1) % 12)]
        for face in ((a, b, a + 12), (b, b + 12, a + 12))
    ]
    header = (
        "ply\nformat binary_little_endian 1.0\nelement vertex 120\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    original = (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(positions, directions, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )
    filename = f"support-{kind}.ply"
    project = ScanProject()
    empty = project.accepted
    accepted = project.commit(
        project.prepare_add(
            io.BytesIO(original),
            filename,
            ScanLimits(100_000, 200, 300, 10_000_000),
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
    )
    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    recipe.nodes.extend(
        [
            Selection(
                id="patch",
                label="Source observations",
                operation="selection",
                source="scan",
                ids=list(range(60)),
            ),
            Selection(
                id="target",
                label="Horizontal occurrence",
                operation="selection",
                source="scan",
                ids=list(range(60, 120)),
            ),
            SurfaceFit(
                id="surface",
                label="Explicit source patch",
                operation="fit",
                selections=["patch"],
                kind=kind,
                axial_domain=(1.0, 9.0),
            ),
        ]
    )
    plan = reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges(
            label="Transfer finite support",
            fits=["surface"],
            reference_selection="patch",
            target_selections=["target"],
            tangent_margin=0.02,
            normal_margin=0.02,
        ),
        "frozen-explicit-support",
        create=True,
    )
    clone = next(
        node for node in plan.recipe.nodes if node.managed_key == "fit/target/surface"
    )
    return {
        "kind": kind,
        "filename": filename,
        "raw": base64.b64encode(original).decode("ascii"),
        "recipe": plan.recipe.model_dump(mode="json"),
        "clone": clone.id,
        "source_sha256": accepted.workspace.source_sha256,
        "positions": positions.astype("<f4").astype(float).tolist(),
        "target_ids": list(range(60, 120)),
        "prototype_endpoints": [[21.0, 6.0, 27.0], [29.0, 6.0, 27.0]],
    }
