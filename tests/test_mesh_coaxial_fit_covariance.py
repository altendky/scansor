"""Shared-axis recovery must not depend on length units or coordinate origin."""

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import SideObservations, fit_coaxial
from experiments.mesh_mirror_surfaces import MirrorSurfaces, mirror_surface_equations
from experiments.mesh_rotational_planes import (
    RotationalPlanes,
    rotation_residual_jacobian,
)
from tests.test_mesh_coaxial_fit import geometry
from tests.test_mesh_mirror_surfaces import plane_group
from tests.test_mesh_rotational_planes import rotational_geometry


def observations() -> tuple[list[SideObservations], np.ndarray, np.ndarray]:
    slopes = np.array([0.12, -0.08, 1.0])
    axis = slopes / np.linalg.norm(slopes)
    u = np.cross(axis, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    anchor = np.array([0.3, -0.4, 0.0])
    theta, axial = np.meshgrid(
        np.linspace(0, 2 * np.pi, 32, endpoint=False), np.linspace(-3, 3, 7)
    )
    sides = [
        SideObservations(
            anchor
            + axial.ravel()[:, None] * axis
            + radius
            * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v),
            np.linspace(0.2, 1.2, theta.size),
            "cylinder",
            (-4.0, 4.0),
        )
        for radius in (3.2, 2.4)
    ]
    x, y = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-3, 3, 7))
    plane = 2.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    return sides, plane, np.linspace(0.3, 1.0, len(plane))


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("shift", [(0.0, 0.0, 0.0), (1000.0, -2000.0, 1500.0)])
def test_shared_axis_fit_origin_and_unit_covariance(
    scale: float, shift: tuple[float, float, float]
) -> None:
    sides, plane, area = observations()
    translation = scale * np.array(shift)
    moved_sides = [
        SideObservations(
            side.points * scale + translation,
            side.area * scale**2,
            side.kind,
            tuple(scale * np.array(side.domain)),
        )
        for side in sides
    ]
    initial = np.array([0.0, 0.0, 0.0, 0.0, 2.0, 3.0, 2.2])
    initial[:2] = translation[:2]
    initial[4] = initial[4] * scale + translation[2]
    initial[5:] *= scale
    result = fit_coaxial(
        moved_sides, plane * scale + translation, area * scale**2, initial
    )
    for parameters, radius in zip(result.parameters, (3.2, 2.4), strict=True):
        axis = np.array([*parameters[2:4], 1.0])
        axis /= np.linalg.norm(axis)
        recovered = parameters.copy()
        recovered[0] = (
            parameters[0] - translation[0] + parameters[2] * translation[2]
        ) / scale
        recovered[1] = (
            parameters[1] - translation[1] + parameters[3] * translation[2]
        ) / scale
        recovered[4] /= scale
        recovered[5] = (parameters[5] - axis @ translation) / scale
        np.testing.assert_allclose(
            recovered, [0.3, -0.4, 0.12, -0.08, radius, 2.1, 0.0], atol=2e-7
        )
        np.testing.assert_array_equal(parameters[:4], result.parameters[0][:4])
    assert result.weighted_rms / scale < 1e-8
    assert len(result.residuals[0]) == len(sides[0].points)
    assert len(result.residuals[1]) == len(sides[1].points)
    assert len(result.plane_residuals) == len(plane)
    assert result.solver["termination"] == "projected-gradient"
    assert result.solver["rank"] == 7
    assert np.all(np.diff(result.objective_history) <= 0)


def test_coaxial_negative_cylinder_radius_is_invalid_geometry() -> None:
    sides, plane, area = observations()
    with pytest.raises(ValueError, match="invalid-initial-geometry"):
        _ = fit_coaxial(
            sides, plane, area, np.array([0.0, 0.0, 0.0, 0.0, 2.0, -3.0, 2.2])
        )


@pytest.mark.parametrize("kind", ["rotation", "mirror"])
@pytest.mark.parametrize("scale", [1e-3, 1e3])
def test_canonical_plane_symmetry_origin_and_unit_covariance(
    kind: str, scale: float
) -> None:
    if kind == "rotation":
        sides, plane, area, truth, group = rotational_geometry()
        original_equations = rotation_residual_jacobian(group, truth, 10)[2]
        tilt_index, distance_index = 10, 12
    else:
        sides, plane, area = geometry()
        group, truth = plane_group()
        original_equations = mirror_surface_equations(group, truth, 10)
        tilt_index, distance_index = 11, 13
    translation = scale * np.array([800.0, -700.0, 0.5])
    raw = np.array([*truth[2:4], 1.0])
    axis = raw / np.linalg.norm(raw)
    delta = np.linalg.norm(raw) * translation[2]
    initial = truth.copy()
    initial[0] = translation[0] + scale * truth[0] - truth[2] * translation[2]
    initial[1] = translation[1] + scale * truth[1] - truth[3] * translation[2]
    initial[4] = scale * truth[4] + axis @ translation
    for radius_index, taper in ((5, truth[6]), (7, 0.0), (8, truth[9])):
        initial[radius_index] = scale * truth[radius_index] - taper * delta
    initial[distance_index] = scale * truth[distance_index] + delta * np.cos(
        truth[tilt_index]
    )
    initial[0] += 0.01 * scale
    moved_sides = [
        SideObservations(
            side.points * scale + translation,
            side.area * scale**2,
            side.kind,
            tuple(scale * np.array(side.domain) + delta),
        )
        for side in sides
    ]
    moved_points = tuple(points * scale + translation for points in group.points)
    moved_areas = tuple(weights * scale**2 for weights in group.areas)
    if kind == "rotation":
        moved_group = RotationalPlanes(moved_points, moved_areas)
        result = fit_coaxial(
            moved_sides,
            plane * scale + translation,
            area * scale**2,
            initial,
            rotations=(moved_group,),
        )
        equations = result.rotational_equations[0]
    else:
        moved_group = MirrorSurfaces(
            (moved_points[0], moved_points[1]),
            (moved_areas[0], moved_areas[1]),
            "plane",
            (),
        )
        result = fit_coaxial(
            moved_sides,
            plane * scale + translation,
            area * scale**2,
            initial,
            mirrors=(moved_group,),
        )
        equations = result.mirror_equations[0]
    assert result.weighted_rms / scale < 1e-8
    for actual, original in zip(equations, original_equations, strict=True):
        np.testing.assert_allclose(actual[:3], original[:3], atol=1e-8)
        assert actual[3] == pytest.approx(
            scale * original[3] + original[:3] @ translation, rel=1e-8
        )
