"""Independent geometry, derivatives and shared-axis influence checks."""

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import (
    SideObservations,
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
