"""Prescribed indexed geometry, independent of the selection implementation."""

import numpy as np
from numpy.typing import NDArray


def grid(
    size: int = 15, *, folded: bool = False, hole: bool = False
) -> tuple[NDArray[np.float64], NDArray[np.int32]]:
    points = np.asarray(
        [
            (float(x), float(y), float(max(x - size // 2, 0)) if folded else 0.0)
            for y in range(size)
            for x in range(size)
        ]
    )
    faces: list[tuple[int, int, int]] = []
    for y in range(size - 1):
        for x in range(size - 1):
            if (
                hole
                and size // 3 <= x < 2 * size // 3
                and size // 3 <= y < 2 * size // 3
            ):
                continue
            a = y * size + x
            faces.extend([(a, a + 1, a + size), (a + 1, a + size + 1, a + size)])
    return points, np.asarray(faces, dtype=np.int32)


def cylinder_lip(
    angular: int = 64, fillet: int = 8
) -> tuple[NDArray[np.float64], NDArray[np.int32], list[int], NDArray[np.str_]]:
    radius, rounding = 2.5, 0.3
    profile = [(radius, float(z), "cylinder") for z in np.linspace(-2, 0, 7)]
    profile += [
        (radius + rounding * (1 - np.cos(phi)), rounding * np.sin(phi), "fillet")
        for phi in np.linspace(0, np.pi / 2, fillet + 1)[1:]
    ]
    profile += [
        (float(r), rounding, "plane")
        for r in np.linspace(radius + rounding, radius + 1.5, 5)[1:]
    ]
    points = np.asarray(
        [
            (r * np.cos(theta), r * np.sin(theta), z)
            for r, z, _ in profile
            for theta in np.arange(angular) * 2 * np.pi / angular
        ]
    )
    faces: list[tuple[int, int, int]] = []
    labels: list[str] = []
    for row in range(len(profile) - 1):
        for column in range(angular):
            a, b = row * angular + column, row * angular + (column + 1) % angular
            faces.extend([(a, b, a + angular), (b, b + angular, a + angular)])
            labels.extend([profile[row + 1][2]] * 2)
    seeds = [
        row * angular + column
        for row in [1, 2, 3]
        for column in range(angular // 8 + 1)
    ]
    return points, np.asarray(faces, dtype=np.int32), seeds, np.asarray(labels)
