"""Display-only PLY exports for the exploratory nozzle cylinder/end-plane fit."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from experiments.mesh_cylinder_fit import Array
from experiments.run_nozzle_cylinder import NozzleExample


def write_mesh(
    path: Path,
    points: Array,
    normals: Array,
    rgb: NDArray[np.uint8],
    faces: NDArray[np.int32],
) -> None:
    rows = np.empty(
        len(points),
        dtype=[("xyz", "<f4", (3,)), ("normal", "<f4", (3,)), ("rgb", "u1", (3,))],
    )
    rows["xyz"] = points
    rows["normal"] = normals
    rows["rgb"] = rgb
    triangles = np.empty(len(faces), dtype=[("count", "u1"), ("values", "<i4", (3,))])
    triangles["count"] = 3
    triangles["values"] = faces
    header = f"ply\nformat binary_little_endian 1.0\nelement vertex {len(points)}\nproperty float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nelement face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    with path.open("xb") as stream:
        _ = stream.write(header.encode("ascii"))
        rows.tofile(stream)
        triangles.tofile(stream)


def guide_mesh(
    paths: list[tuple[Array, bool]], color: tuple[int, int, int], output: Path
) -> None:
    """Thin display tubes around analytic guide curves; thickness is not uncertainty."""
    vertices: list[Array] = []
    normals: list[Array] = []
    faces: list[tuple[int, int, int]] = []
    phi = np.linspace(0, 2 * np.pi, 8, endpoint=False)
    for path, closed in paths:
        # Central tangents for rings; straight-line tangent for open generators.
        tangent = (
            np.roll(path, -1, axis=0) - np.roll(path, 1, axis=0)
            if closed
            else np.broadcast_to(path[-1] - path[0], path.shape).copy()
        )
        tangent /= np.linalg.norm(tangent, axis=1)[:, None]
        reference = np.tile([0.0, 0, 1], (len(path), 1))
        reference[np.abs(tangent[:, 2]) > 0.9] = [0, 1, 0]
        u = np.cross(tangent, reference)
        u /= np.linalg.norm(u, axis=1)[:, None]
        v = np.cross(tangent, u)
        ns = (
            np.cos(phi)[None, :, None] * u[:, None, :]
            + np.sin(phi)[None, :, None] * v[:, None, :]
        )
        base = sum(len(p) for p in vertices)
        vertices.append((path[:, None, :] + 0.012 * ns).reshape(-1, 3))
        normals.append(ns.reshape(-1, 3))
        for i in range(len(path) if closed else len(path) - 1):
            for j in range(8):
                a = base + i * 8 + j
                b = base + ((i + 1) % len(path)) * 8 + j
                c = base + ((i + 1) % len(path)) * 8 + (j + 1) % 8
                d = base + i * 8 + (j + 1) % 8
                faces.extend(((a, b, c), (a, c, d)))
    points = np.vstack(vertices)
    colors = np.tile(np.array(color, dtype=np.uint8), (len(points), 1))
    write_mesh(
        output, points, np.vstack(normals), colors, np.array(faces, dtype=np.int32)
    )


def export_view(
    output: Path,
    data: NozzleExample,
    plane_ids: NDArray[np.int64],
    residual: Array,
    axis: Array,
    axis_point: Array,
    plane_point: Array,
    radius: float,
) -> None:
    rgb = np.tile(np.array([125, 140, 150], dtype=np.uint8), (len(data.xyz), 1))
    # Independent symmetric scales retain each region's residual detail without clipping.
    limits: list[float] = []
    for ids, values in (
        (data.ids, residual[: len(data.ids)]),
        (plane_ids, residual[len(data.ids) :]),
    ):
        limit = max(float(np.max(np.abs(values))), 1e-12)
        limits.append(limit)
        t = np.abs(values) / limit
        end = np.where(
            (values >= 0)[:, None], np.array([210, 40, 40]), np.array([30, 85, 230])
        )
        rgb[ids] = np.rint(248 * (1 - t[:, None]) + end * t[:, None]).astype(np.uint8)
    write_mesh(
        output / "scan-joint-residuals.ply", data.xyz, data.normals, rgb, data.triangles
    )
    u = np.cross([0.0, 1, 0], axis)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    theta = np.linspace(0, 2 * np.pi, 1024, endpoint=False)
    radial = np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
    low = float(np.min((data.xyz[data.ids] - axis_point) @ axis))
    high = float((plane_point - axis_point) @ axis)
    cylinder_paths = [
        (axis_point + height * axis + radius * radial, True) for height in (low, high)
    ]
    for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        rr = np.cos(angle) * u + np.sin(angle) * v
        cylinder_paths.append(
            (axis_point + np.array([low, high])[:, None] * axis + radius * rr, False)
        )
    guide_mesh(cylinder_paths, (20, 240, 220), output / "joint-cylinder-guide.ply")
    plane_paths = [(plane_point + r * radial, True) for r in (8.2, 10.2)]
    for angle in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        rr = np.cos(angle) * u + np.sin(angle) * v
        plane_paths.append((plane_point + np.array([8.2, 10.2])[:, None] * rr, False))
    guide_mesh(plane_paths, (240, 60, 215), output / "perpendicular-plane-guide.ply")
    _ = (output / "VIEW.txt").write_text(
        "Open the three PLY files together in CloudCompare. Cyan: jointly fitted cylinder; magenta: perpendicular plane.\n"
        + "Guides extend outside the selected regions to show the relationship. Tube radius 0.012 is for visibility, not an error bound.\n"
        + "Gray: unselected context. Blue/white/red: negative/zero/positive signed residual. All source vertices and triangles are retained.\n"
        + f"Cylinder color range: +/-{limits[0]:.8f}; plane color range: +/-{limits[1]:.8f} source units. Separate scales, no clipping.\n"
        + "Cylinder positive means radially outside; plane positive means along the shared +axis normal. Unknown source units.\n"
        + "PLY guides are tessellated display geometry, not additional observations. Toggle the guide objects to see residual colors unobstructed.\n"
    )
