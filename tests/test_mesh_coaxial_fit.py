"""Independent geometry, derivatives and shared-axis influence checks."""

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import (
    AxisPlaneObservations,
    SideObservations,
    axis_plane_frame,
    fit_coaxial,
    residual_jacobian,
)
from experiments.mesh_cylinder_fit import Array


def geometry() -> tuple[list[SideObservations], Array, Array]:
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    center = np.array([0.3, -0.4, 0.0])
    sides: list[SideObservations] = []
    for kind, radius, taper, lo, hi in [
        ("cone", 3.2, 0.08, -1, 1),
        ("cylinder", 2.4, 0.0, -3, -1.5),
        ("cone", 4.0, -0.2, -0.5, 1.5),
    ]:
        theta, z = np.meshgrid(
            np.linspace(0, 1, 40) ** 1.4 * 1.8 * np.pi, np.linspace(lo, hi, 8)
        )
        points = (
            center
            + z.ravel()[:, None] * axis
            + (radius + taper * z.ravel())[:, None]
            * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v)
        )
        sides.append(
            SideObservations(points, np.linspace(0.2, 1.2, len(points)), kind, (-4, 4))
        )
    x, y = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-3, 3, 7))
    plane = 2.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    return sides, plane, np.linspace(0.3, 1.0, len(plane))


def test_three_sides_recover_exact_geometry() -> None:
    sides, plane, area = geometry()
    result = fit_coaxial(
        sides, plane, area, np.array([0.0, 0, 0, 0, 2, 3, 0, 2.5, 3.8, 0])
    )
    for p, radius, taper in zip(
        result.parameters, [3.2, 2.4, 4.0], [0.08, 0, -0.2], strict=True
    ):
        np.testing.assert_allclose(
            p, [0.3, -0.4, 0.12, -0.08, radius, 2.1, taper], atol=1e-8
        )
        np.testing.assert_array_equal(p[:4], result.parameters[0][:4])
    assert result.weighted_rms < 1e-9


def test_joint_analytic_derivative() -> None:
    sides, plane, _ = geometry()
    parameters = np.array([0.2, -0.1, 0.1, -0.05, 2, 3, 0.04, 2.5, 3.8, -0.1])
    _, actual = residual_jacobian(sides, plane, parameters)
    expected = np.empty_like(actual)
    for i in range(len(parameters)):
        delta = np.zeros_like(parameters)
        delta[i] = 1e-6
        plus, _ = residual_jacobian(sides, plane, parameters + delta)
        minus, _ = residual_jacobian(sides, plane, parameters - delta)
        expected[:, i] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=1e-7)


def test_added_surface_moves_existing_axis() -> None:
    sides, plane, area = geometry()
    first = fit_coaxial(sides[:1], plane, area, np.array([0.0, 0, 0, 0, 2, 3, 0]))
    # Give the second side a slightly conflicting slope; the joint solution must
    # compromise rather than holding the original fitted axis fixed.
    points = sides[1].points.copy()
    points[:, 0] += 0.02 * points[:, 2]
    altered = SideObservations(points, sides[1].area, "cylinder", (-4, 4))
    joint = fit_coaxial(
        [sides[0], altered], plane, area, np.array([0.0, 0, 0, 0, 2, 3, 0, 2.5])
    )
    assert abs(joint.parameters[0][2] - first.parameters[0][2]) > 1e-4
    assert np.linalg.norm(joint.residuals[0]) > 1e-3
    np.testing.assert_array_equal(joint.parameters[0][:4], joint.parameters[1][:4])
    assert np.all(np.diff(joint.objective_history) <= 0)


def test_empty_and_unobservable_groups_fail() -> None:
    sides, plane, area = geometry()
    with pytest.raises(ValueError, match="at least one"):
        _ = fit_coaxial([], plane, area, np.zeros(5))
    empty = SideObservations(np.empty((0, 3)), np.empty(0), "cone", (-4, 4))
    with pytest.raises(ValueError, match="three XYZ"):
        _ = fit_coaxial([empty], plane, area, np.zeros(7))
    points = sides[0].points[:40]  # one axial ring does not identify taper
    ring = SideObservations(points, np.ones(len(points)), "cone", (-4, 4))
    with pytest.raises(ValueError, match="ill-conditioned"):
        _ = fit_coaxial(
            [ring], plane, area, np.array([0.3, -0.4, 0.12, -0.08, 2.1, 3.2, 0.08])
        )


def test_multiple_planes_recovery_and_derivatives() -> None:
    sides, plane, area = geometry()
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    other = plane - 4.3 * axis
    initial = np.array([0.0, 0, 0, 0, 2, 3, 0, 2.5, 3.8, 0, -2])
    result = fit_coaxial(sides, plane, area, initial, ((other, area * 2),))
    assert result.weighted_rms < 1e-9
    np.testing.assert_allclose(result.plane_offsets, [2.1, -2.2], atol=1e-8)
    assert np.max(np.abs(result.extra_plane_residuals[0])) < 1e-8
    np.testing.assert_allclose(
        result.parameters[0][:4], [0.3, -0.4, 0.12, -0.08], atol=1e-8
    )
    _, actual = residual_jacobian(sides, plane, initial, (other,))
    expected = np.empty_like(actual)
    for i in range(len(initial)):
        delta = np.zeros_like(initial)
        delta[i] = 1e-6
        plus, _ = residual_jacobian(sides, plane, initial + delta, (other,))
        minus, _ = residual_jacobian(sides, plane, initial - delta, (other,))
        expected[:, i] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=1e-7)


def test_additional_plane_influences_shared_axis() -> None:
    sides, plane, area = geometry()
    initial = np.array([0.0, 0, 0, 0, 2, 3, 0])
    original = fit_coaxial(sides[:1], plane, area, initial)
    other = plane.copy()
    other[:, 2] += 0.04 * other[:, 0] - 4
    result = fit_coaxial(
        sides[:1], plane, area, np.append(initial, -2), ((other, area),)
    )
    assert abs(result.parameters[0][2] - original.parameters[0][2]) > 1e-4
    assert result.weighted_rms > 0
    assert np.all(np.diff(result.objective_history) <= 0)
    with pytest.raises(ValueError, match="three XYZ"):
        _ = fit_coaxial(
            sides[:1],
            plane,
            area,
            np.append(initial, -2),
            ((np.empty((0, 3)), np.empty(0)),),
        )


def test_axis_derived_plane_factors_move_the_shared_axis() -> None:
    sides, perpendicular_points, perpendicular_area = geometry()
    cylinder = sides[1]
    truth = np.array([0.3, -0.4, 0.12, -0.08, 2.4, 2.1, -1.7])
    perpendicular = AxisPlaneObservations(
        perpendicular_points,
        perpendicular_area,
        "perpendicular_to_axis",
        None,
        0,
    )
    empty_parallel = AxisPlaneObservations(
        np.empty((0, 3)), np.empty(0), "parallel_to_axis", np.radians(31), 0
    )
    axis, radial, normal = axis_plane_frame(empty_parallel, truth)
    along_axis, across = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-2, 2, 6))
    parallel_points = (
        -1.7 * normal
        + along_axis.ravel()[:, None] * axis
        + across.ravel()[:, None] * radial
    )
    parallel = AxisPlaneObservations(
        parallel_points,
        np.linspace(0.4, 1.1, len(parallel_points)),
        "parallel_to_axis",
        np.radians(31),
        0,
    )
    initial = np.array([0.0, 0.0, 0.02, 0.01, 2.2, 1.8, -1.4])
    result = fit_coaxial(
        [cylinder], None, None, initial, axis_planes=(perpendicular, parallel)
    )
    np.testing.assert_allclose(result.parameters[0][:5], truth[:5], atol=1e-8)
    np.testing.assert_allclose(result.axis_plane_equations[0], [*axis, 2.1], atol=1e-8)
    np.testing.assert_allclose(
        result.axis_plane_equations[1], [*normal, -1.7], atol=1e-8
    )
    assert result.weighted_rms < 1e-9

    _, actual = residual_jacobian(
        [cylinder], None, truth, axis_planes=(perpendicular, parallel)
    )
    expected = np.empty_like(actual)
    for i in range(len(truth)):
        delta = np.zeros_like(truth)
        delta[i] = 1e-6
        plus = residual_jacobian(
            [cylinder], None, truth + delta, axis_planes=(perpendicular, parallel)
        )[0]
        minus = residual_jacobian(
            [cylinder], None, truth - delta, axis_planes=(perpendicular, parallel)
        )[0]
        expected[:, i] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, atol=2e-7, rtol=2e-6)
