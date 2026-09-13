"""Seed-only recovery and topology/barrier tests for connected proposals."""

from typing import Any

import numpy as np
import pytest

from experiments.selection_growth import connected_growth, fit_seed


def grid() -> tuple[Any, Any, Any, Any]:
    points = np.array([[float(x), float(y), 0.0] for x in range(7) for y in range(2)])
    faces = np.array(
        [[2 * x, 2 * x + 1, 2 * x + 2] for x in range(6)]
        + [[2 * x + 1, 2 * x + 3, 2 * x + 2] for x in range(6)],
        dtype=np.int32,
    )
    normals = np.tile([0.0, 0, 1], (len(points), 1))
    return points, normals, faces, np.ones(len(points))


def test_growth_stops_at_rejected_strip_and_barrier() -> None:
    points, normals, faces, weights = grid()
    fitted = fit_seed(
        points[:4], weights[:4], normals[:4], "plane", np.zeros(5), (-2, 5)
    )
    points[6:8, 2] = 0.5
    proposed = connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3], [], fitted, 0.1, 20
    )
    assert proposed["ids"] == list(range(6))
    points[6:8, 2] = 0
    proposed = connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3], [6, 7], fitted, 0.1, 20
    )
    assert proposed["ids"] == list(range(6))
    normals[6:8] = [1, 0, 0]
    assert connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3], [], fitted, 0.1, 20
    )["ids"] == list(range(6))


def test_disconnected_matching_patch_and_multiple_seed_components() -> None:
    points, normals, faces, weights = grid()
    points = np.vstack((points, points + np.array([0, 3, 0])))
    normals = np.vstack((normals, normals))
    weights = np.ones(len(points))
    faces = np.vstack(
        (faces, faces + 14, [[0, 14, 0]])
    )  # degenerate face cannot bridge
    fitted = fit_seed(
        points[:4], weights[:4], normals[:4], "plane", np.zeros(5), (-2, 5)
    )
    assert connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3], [], fitted, 0.1, 20
    )["ids"] == list(range(14))
    assert connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3, 14], [], fitted, 0.1, 20
    )["ids"] == list(range(28))
    points[6:8, 2] = 0.5
    proposed = connected_growth(
        points, normals, faces, weights, [0, 1, 2, 3, 6], [], fitted, 0.1, 20
    )
    assert proposed["ids"] == [0, 1, 2, 3, 4, 5, 6]
    assert proposed["rejected_seed_ids"] == [6]


@pytest.mark.parametrize("slopes", [(0.0, 0.0), (0.12, -0.08)])
@pytest.mark.parametrize("kind,taper", [("cone", 0.12), ("cylinder", 0.0)])
def test_seed_side_recovery_without_plane(
    kind: str, taper: float, slopes: tuple[float, float]
) -> None:
    theta, z = np.meshgrid(
        np.linspace(0, 2 * np.pi, 40, endpoint=False), np.linspace(-1, 1, 7)
    )
    axis = np.array([*slopes, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    radial = np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v
    points = (
        np.array([0.2, -0.3, 0])
        + z.ravel()[:, None] * axis
        + (3 + taper * z.ravel())[:, None] * radial
    )
    normals = (radial - taper * axis) / np.hypot(1, taper)
    fitted = fit_seed(
        points,
        np.ones(len(points)),
        normals,
        kind,
        np.array([0.0, 0, 0, 0, 3]),
        (-2, 2),
    )
    np.testing.assert_allclose(
        fitted["parameters"], [0.2, -0.3, *slopes, 3, 0, taper], atol=1e-8
    )
    assert fitted["weighted_rms"] < 1e-9


def test_insufficient_seed_coverage_fails() -> None:
    points = np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]])
    with pytest.raises(ValueError, match="ill-conditioned"):
        _ = fit_seed(
            points,
            np.ones(3),
            np.tile([0.0, 0, 1], (3, 1)),
            "plane",
            np.zeros(5),
            (-2, 5),
        )
