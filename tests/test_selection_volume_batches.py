"""Batch queries agree with scalar geometry without unbounded Cartesian work."""

# Deliberately compare the paired internal geometry kernel to scalar calls.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from experiments import selection_volume as kernel
from experiments.scan_workspace import ScanCancelled
from tests.test_selection_volume import volume


def scalar_mesh(
    points: np.ndarray, normals: np.ndarray, weights: np.ndarray, saved: dict[str, Any]
) -> list[int]:
    cells = np.asarray(saved["patch_vertices"])
    patch_normals = np.asarray(saved["patch_normals"])
    controls = saved["resolved"]
    inward, outward, padding = (
        controls[name]
        for name in ("inward_thickness", "outward_thickness", "edge_padding")
    )
    tolerance = (
        64 * np.finfo(float).eps * max(controls["spacing"], np.max(np.abs(cells)))
    )
    tree = kernel.TriangleIndex(cells, max(inward, outward) + padding + tolerance)
    cosine = np.cos(np.radians(controls["normal_angle_degrees"]))
    lengths = np.linalg.norm(normals, axis=1)
    result: list[int] = []
    for index, (point, direction, weight) in enumerate(
        zip(points, normals, weights, strict=True)
    ):
        length = lengths[index]
        if (
            not np.isfinite(point).all()
            or not np.isfinite(direction).all()
            or not np.isfinite(length)
            or length <= 0
            or not np.isfinite(weight)
            or weight <= 0
        ):
            continue
        direction = direction / length
        for candidates in tree.candidates(point):
            triangles = cells[candidates]
            closest, bary = kernel._closest(point, triangles)
            expected = np.sum(patch_normals[candidates] * bary[:, :, None], axis=1)
            expected /= np.linalg.norm(expected, axis=1)[:, None]
            normal = np.cross(
                triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
            )
            normal /= np.linalg.norm(normal, axis=1)[:, None]
            normal *= np.where(np.sum(normal * expected, axis=1) < 0, -1, 1)[:, None]
            displacement = point - closest
            signed = np.sum(displacement * normal, axis=1)
            tangent = np.linalg.norm(displacement - signed[:, None] * normal, axis=1)
            if np.any(
                (signed >= -inward - tolerance)
                & (signed <= outward + tolerance)
                & (tangent <= padding + tolerance)
                & (expected @ direction >= cosine - 1e-14)
            ):
                result.append(index)
                break
    return result


@pytest.mark.parametrize("dimension", [2, 3])
def test_paired_closest_matches_individual_point_triangle_calls(dimension: int) -> None:
    rng = np.random.default_rng(813)
    cells = rng.normal(size=(150, 3, dimension))
    cells[0, 1:] = cells[0, 0]
    cells[1, 2] = cells[1, 1]
    points = rng.normal(size=(150, dimension))
    closest, bary = kernel._closest(points, cells)
    for index, point in enumerate(points):
        one_closest, one_bary = kernel._closest(point, cells[index : index + 1])
        np.testing.assert_array_equal(closest[index], one_closest[0])
        np.testing.assert_array_equal(bary[index], one_bary[0])


@pytest.mark.parametrize("chunk", [1, 7, 1024, 65536])
def test_mesh_batches_match_scalar_holes_normals_boundaries_and_transforms(
    chunk: int,
) -> None:
    points, normals, _, _, saved = volume(
        "mesh",
        options={
            "inward_thickness": 0.1,
            "outward_thickness": 0.25,
            "edge_padding": 0.03,
            "normal_angle_degrees": 35,
        },
    )
    rng = np.random.default_rng(345)
    random = rng.uniform([-0.5, -0.5, -0.2], [4.5, 4.5, 0.4], size=(240, 3))
    query = np.concatenate(
        (
            points,
            random,
            points + np.array([0, 0, -0.1]),
            points + np.array([0, 0, 0.25]),
        )
    )
    directions = np.tile(normals[0], (len(query), 1))
    directions[::7] *= -1
    directions[5] = 0
    directions[6] = np.nan
    weights = np.ones(len(query))
    weights[::11] = 0
    weights[8] = np.inf
    query[9] = np.nan
    rotation = np.array([[0.8, -0.6, 0], [0.6, 0.8, 0], [0, 0, 1]])
    translation = np.array([3.0, -2.0, 0.5])
    world = (query - translation) @ rotation.T
    world_normals = directions @ rotation.T
    # Apply the actual transform again for the scalar oracle too; the query
    # coordinates after floating-point roundtrip are authoritative.
    expected = scalar_mesh(
        world @ rotation + translation, world_normals @ rotation, weights, saved
    )
    assert (
        kernel.apply_selection_volume(
            world,
            world_normals,
            weights,
            saved,
            chunk_size=chunk,
            query_rotation=rotation,
            query_translation=translation,
        )
        == expected
    )


@pytest.mark.parametrize("oblique", [False, True])
def test_batched_normal_filter_matches_scalar_at_angular_boundary(
    oblique: bool,
) -> None:
    points, normals, triangles, ids, saved = volume(
        "mesh", options={"normal_angle_degrees": 35}
    )
    normal = np.array([0.0, 0, 1])
    tangent = np.array([1.0, 0, 0])
    if oblique:
        normal = np.array([1.0, 2, 3]) / np.sqrt(14)
        tangent = np.cross(normal, [0, 1, 0])
        tangent /= np.linalg.norm(tangent)
        rotation = np.column_stack((tangent, np.cross(normal, tangent), normal))
        points = points @ rotation.T
        saved = kernel.build_selection_volume(
            points,
            normals @ rotation.T,
            triangles,
            ids,
            kind="mesh",
            options={"normal_angle_degrees": 35},
        )
    threshold = np.cos(np.radians(35)) - 1e-14
    z = threshold + np.arange(-20, 21) * np.finfo(float).eps
    normals = np.sqrt(1 - z[:, None] ** 2) * tangent + z[:, None] * normal
    query = np.tile(points[0], (len(normals), 1))
    expected = scalar_mesh(query, normals, np.ones(len(query)), saved)
    assert expected and len(expected) < len(query)
    assert (
        kernel.apply_selection_volume(query, normals, np.ones(len(query)), saved)
        == expected
    )


def test_mesh_batch_pairs_are_bounded_and_far_points_skip_narrow_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, normals, _, _, saved = volume("mesh")
    original = kernel._closest
    counts: list[int] = []

    def observe(point: np.ndarray, cells: np.ndarray):
        counts.append(len(cells))
        return original(point, cells)

    monkeypatch.setattr(kernel, "_closest", observe)
    query = np.tile(points[0], (4097, 1))
    directions = np.tile(normals[0], (len(query), 1))
    assert kernel.apply_selection_volume(
        query, directions, np.ones(len(query)), saved, chunk_size=65536
    ) == list(range(len(query)))
    assert counts and max(counts) <= 16384
    counts.clear()
    assert (
        kernel.apply_selection_volume(
            query + 1000, directions, np.ones(len(query)), saved
        )
        == []
    )
    assert counts == []
    assert kernel.apply_selection_volume(
        query[:10], directions[:10], np.ones(10), saved, max_selected=10
    ) == list(range(10))
    with pytest.raises(ValueError, match="membership budget"):
        _ = kernel.apply_selection_volume(
            query[:10], directions[:10], np.ones(10), saved, max_selected=9
        )


def test_mesh_cancellation_after_a_paired_kernel_discards_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, normals, _, _, saved = volume("mesh")
    original = kernel._closest
    cancelled = False

    def observe(point: np.ndarray, cells: np.ndarray):
        nonlocal cancelled
        result = original(point, cells)
        cancelled = True
        return result

    monkeypatch.setattr(kernel, "_closest", observe)
    with pytest.raises(ScanCancelled):
        _ = kernel.apply_selection_volume(
            points, normals, np.ones(len(points)), saved, cancelled=lambda: cancelled
        )
