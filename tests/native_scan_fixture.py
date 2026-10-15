"""Independent strict source bytes for native import and resource evidence."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class NativeScanFixture:
    raw: bytes
    positions: NDArray[np.float64]
    normals: NDArray[np.float64]
    triangles: NDArray[np.int32]
    weights: NDArray[np.float64]
    selections: dict[str, list[int]]


def _fixture(
    positions: NDArray[np.float64],
    normals: NDArray[np.float64],
    triangles: NDArray[np.int32],
    selections: dict[str, list[int]],
) -> NativeScanFixture:
    rows = np.column_stack((positions, normals)).astype("<f4")
    faces = np.empty(
        len(triangles), dtype=np.dtype([("count", "u1"), ("indices", "<i4", (3,))])
    )
    faces["count"] = 3
    faces["indices"] = triangles
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(rows)}\nproperty float x\nproperty float y\n"
        "property float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    xyz = rows[:, :3].astype(np.float64)
    directions = rows[:, 3:].astype(np.float64)
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    a, b, c = (xyz[triangles[:, index]] for index in range(3))
    contributions = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 6
    weights = np.zeros(len(xyz))
    for corner in range(3):
        np.add.at(weights, triangles[:, corner], contributions)
    return NativeScanFixture(
        header + rows.tobytes() + faces.tobytes(),
        xyz,
        directions,
        triangles,
        weights,
        selections,
    )


def native_scene_fixture() -> NativeScanFixture:
    """A horizontal cylinder and independent plane, without production transforms."""
    angles, heights = np.meshgrid(
        np.linspace(0, 1, 13)[:-1] ** 1.3 * 2 * np.pi,
        np.array([-3.0, -2.2, -0.3, 0.8, 2.7, 4.0]),
    )
    positions = np.column_stack(
        (
            3 + heights.ravel(),
            -4 + 2.5 * np.cos(angles.ravel()),
            5 + 2.5 * np.sin(angles.ravel()),
        )
    )
    normals = np.column_stack(
        (np.zeros(angles.size), np.cos(angles.ravel()), np.sin(angles.ravel()))
    )
    faces = [
        face
        for row in range(5)
        for col in range(12)
        for a, b in [(12 * row + col, 12 * row + (col + 1) % 12)]
        for face in ((a, b, a + 12), (b, b + 12, a + 12))
    ]
    cylinder_count = len(positions)
    y, z = np.meshgrid([-3.0, -0.5, 1.0, 3.5], [-2.0, 0.7, 2.0])
    plane = np.column_stack((np.full(y.size, 8.0), y.ravel(), z.ravel()))
    positions = np.vstack((positions, plane))
    normals = np.vstack((normals, np.tile([1.0, 0, 0], (len(plane), 1))))
    faces.extend(
        (cylinder_count + face[0], cylinder_count + face[1], cylinder_count + face[2])
        for row in range(2)
        for col in range(3)
        for a in [4 * row + col]
        for face in ((a, a + 1, a + 4), (a + 1, a + 5, a + 4))
    )
    return _fixture(
        positions,
        normals,
        np.asarray(faces, dtype=np.int32),
        {
            "cylinder": list(range(cylinder_count)),
            "plane": list(range(cylinder_count, len(positions))),
        },
    )


def native_grid_fixture(nx: int = 512, ny: int = 256) -> NativeScanFixture:
    """Larger source-only tier; its rows are not a fitting-selection capacity claim."""
    if nx < 2 or ny < 2:
        raise ValueError("grid dimensions must be at least two")
    x, y = np.meshgrid(np.linspace(-256, 256, nx), np.linspace(-128, 128, ny))
    positions = np.column_stack(
        (x.ravel(), y.ravel(), (0.0001 * x + 0.0002 * y).ravel())
    )
    normal = np.array([-0.0001, -0.0002, 1.0])
    normal /= np.linalg.norm(normal)
    a = (np.arange(ny - 1)[:, None] * nx + np.arange(nx - 1)).ravel()
    triangles = (
        np.stack(
            (
                np.column_stack((a, a + 1, a + nx)),
                np.column_stack((a + 1, a + nx + 1, a + nx)),
            ),
            axis=1,
        )
        .reshape(-1, 3)
        .astype(np.int32)
    )
    return _fixture(positions, np.tile(normal, (len(positions), 1)), triangles, {})
