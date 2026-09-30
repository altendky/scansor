"""Moving lateral-surface Jacobians must not use world-coordinate step sizes."""

from dataclasses import replace

import numpy as np
import pytest

from experiments.fit_coordinates import AxisChart, CoordinateFrame, FitCoordinates
from experiments.mesh_mirror_surfaces import MirrorSurfaces, mirror_residual_jacobian
from experiments.mesh_rotational_planes import (
    RotationalPlanes,
    lateral_rotation_residual_jacobian,
    rotation_matrix,
)
from tests.test_mesh_mirror_surfaces import lateral_group


@pytest.mark.parametrize("transform", ["mirror", "rotation"])
@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("scale", [1e-3, 1.0, 1e3])
def test_lateral_transform_derivatives_are_origin_and_unit_covariant(
    transform: str, kind: str, scale: float
) -> None:
    original, truth = lateral_group(kind)
    offset = 10
    canonical_offset = 11 if transform == "mirror" else 10
    if transform == "mirror":
        group: MirrorSurfaces | RotationalPlanes = original
    else:
        truth = np.concatenate([truth[:10], truth[11:]])
        center = np.array([truth[0], truth[1], 0.0])
        points = tuple(
            center + (original.points[0] - center) @ rotation_matrix(truth, slot).T
            for slot in range(3)
        )
        group = RotationalPlanes(
            points,
            tuple(np.ones(len(points[0])) for _ in range(3)),
            kind,
            original.seed,
            original.domain,
        )

    def evaluate(
        observations: MirrorSurfaces | RotationalPlanes, parameters: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(observations, MirrorSurfaces):
            residual, jacobian, _ = mirror_residual_jacobian(
                observations, parameters, offset
            )
        else:
            residual, jacobian, _ = lateral_rotation_residual_jacobian(
                observations, parameters, offset
            )
        return np.concatenate(residual), np.vstack(jacobian)

    baseline_residual, baseline_jacobian = evaluate(group, truth)
    translation = scale * np.array([1e5, -2e5, 0.5])
    coordinate_map = FitCoordinates(
        CoordinateFrame(translation, scale),
        len(truth),
        axes=(
            AxisChart(0, 1, 2, 3, 5, 6),
            AxisChart(
                canonical_offset,
                canonical_offset + 1,
                canonical_offset + 2,
                canonical_offset + 3,
                canonical_offset + 4,
                canonical_offset + 5 if kind == "cone" else None,
            ),
        ),
        length_parameters=(4, 7, 8),
    )
    moved, tangent = coordinate_map.decode(truth)
    moved_points = tuple(points * scale + translation for points in group.points)
    delta = (
        np.linalg.norm([*truth[canonical_offset + 2 : canonical_offset + 4], 1.0])
        * translation[2]
    )
    domain = (scale * group.domain[0] + delta, scale * group.domain[1] + delta)
    if isinstance(group, MirrorSurfaces):
        assert group.other_domain is not None
        moved_group = replace(
            group,
            points=(moved_points[0], moved_points[1]),
            domain=domain,
            other_domain=(
                scale * group.other_domain[0] + delta,
                scale * group.other_domain[1] + delta,
            ),
        )
    else:
        moved_group = replace(group, points=moved_points, domain=domain)
    residual, jacobian = evaluate(moved_group, moved)
    np.testing.assert_allclose(residual / scale, baseline_residual, atol=1e-9)
    # The axial translation rebases z=0 anchors and cone reference radii.
    # Compare derivatives after that chart's exact chain rule, not raw columns.
    np.testing.assert_allclose(
        jacobian @ tangent / scale, baseline_jacobian, atol=3e-9, rtol=3e-8
    )
