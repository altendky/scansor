"""Exact rotated-plane geometry, analytic derivatives and shared-axis influence."""

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import fit_coaxial, residual_jacobian
from experiments.mesh_cylinder_fit import Array
from experiments.mesh_rotational_planes import (
    RotationalPlanes,
    axis_frame,
    initial_rotation,
    rotation_residual_jacobian,
)
from tests.test_mesh_coaxial_fit import geometry


def rotational_geometry():
    sides, plane, area = geometry()
    truth = np.array(
        [0.3, -0.4, 0.12, -0.08, 2.1, 3.2, 0.08, 2.4, 4, -0.2, 0.7, 0.25, -1.2]
    )
    empty = RotationalPlanes(
        tuple(np.empty((0, 3)) for _ in range(3)), tuple(np.empty(0) for _ in range(3))
    )
    equations = rotation_residual_jacobian(empty, truth, 10)[2]
    axis, u, v, *_ = axis_frame(truth)
    origin = np.array([truth[0], truth[1], 0.0])
    points: list[Array] = []
    for slot, eq in enumerate(equations):
        radial = (
            np.cos(0.25 + slot * 2 * np.pi / 3) * u
            + np.sin(0.25 + slot * 2 * np.pi / 3) * v
        )
        center = origin + 3 * radial
        center += (eq[3] - center @ eq[:3]) * eq[:3]
        tangent = np.cross(axis, eq[:3])
        tangent /= np.linalg.norm(tangent)
        other = np.cross(eq[:3], tangent)
        x, y = np.meshgrid(np.linspace(-0.6, 0.6, 7), np.linspace(-0.4, 0.4, 5))
        points.append(
            center + x.ravel()[:, None] * tangent + y.ravel()[:, None] * other
        )
    return (
        sides,
        plane,
        area,
        truth,
        RotationalPlanes(tuple(points), tuple(np.ones(len(p)) for p in points)),
    )


def test_rotational_recovery_and_exact_transform():
    sides, plane, area, truth, group = rotational_geometry()
    initial = truth.copy()
    initial[:4] = [0, 0, 0, 0]
    initial[10:] = initial_rotation(group, initial)
    result = fit_coaxial(sides, plane, area, initial, rotations=(group,))
    assert result.weighted_rms < 1e-9
    np.testing.assert_allclose(result.parameters[0][:4], truth[:4], atol=1e-8)
    eqs = result.rotational_equations[0]
    axis, *_ = axis_frame(truth)
    origin = np.array([truth[0], truth[1], 0.0])
    for i, eq in enumerate(eqs):
        angle = i * 2 * np.pi / 3
        n = eqs[0][:3]
        rotated = (
            n * np.cos(angle)
            + np.cross(axis, n) * np.sin(angle)
            + axis * (axis @ n) * (1 - np.cos(angle))
        )
        np.testing.assert_allclose(eq[:3], rotated, atol=1e-9)
        assert eq[3] - eq[:3] @ origin == pytest.approx(
            eqs[0][3] - eqs[0][:3] @ origin, abs=1e-9
        )
        assert np.max(np.abs(group.points[i] @ eq[:3] - eq[3])) < 1e-8


def test_rotational_analytic_derivatives():
    sides, plane, _, truth, group = rotational_geometry()
    parameters = truth + np.linspace(-0.01, 0.02, len(truth))
    _, actual = residual_jacobian(sides, plane, parameters, rotations=(group,))
    expected = np.empty_like(actual)
    for i in range(len(parameters)):
        step = np.zeros_like(parameters)
        step[i] = 1e-6
        plus, _ = residual_jacobian(sides, plane, parameters + step, rotations=(group,))
        minus, _ = residual_jacobian(
            sides, plane, parameters - step, rotations=(group,)
        )
        expected[:, i] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=1e-7)


def test_rotational_observations_move_axis_and_invalid_groups_fail():
    sides, plane, area, truth, group = rotational_geometry()
    points = [p.copy() for p in group.points]
    for p in points:
        p[:, 0] += 0.03 * p[:, 2]
    altered = RotationalPlanes(tuple(points), group.areas)
    result = fit_coaxial(sides, plane, area, truth, rotations=(altered,))
    assert np.linalg.norm(result.parameters[0][:4] - truth[:4]) > 1e-4
    assert np.all(np.diff(result.objective_history) <= 0)
    with pytest.raises(ValueError, match="three planes"):
        _ = fit_coaxial(
            sides,
            plane,
            area,
            truth,
            rotations=(RotationalPlanes(group.points[:2], group.areas[:2]),),
        )


def test_rotational_group_with_additional_perpendicular_plane():
    sides, plane, area, truth, group = rotational_geometry()
    axis, *_ = axis_frame(truth)
    other = plane + 1.7 * axis
    initial = np.insert(truth, 10, 3.8)
    result = fit_coaxial(sides, plane, area, initial, ((other, area),), (group,))
    assert result.weighted_rms < 1e-9
    np.testing.assert_allclose(result.plane_offsets, [2.1, 3.8], atol=1e-9)
    assert len(result.rotational_equations[0]) == 3


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_lateral_rotation_recovery_derivatives_and_exact_copies(kind: str):
    from experiments.mesh_cone_plane_fit import cone_plane_residual_jacobian
    from experiments.mesh_rotational_planes import rotation_matrix

    sides, plane, area, base, _ = rotational_geometry()
    seed = np.array(
        [1.7, -0.9, 0.21, -0.13, 0.45, 0.0, 0.06 if kind == "cone" else 0.0]
    )
    truth = np.concatenate(
        [base[:10], seed[[0, 1, 2, 3, 4, 6]][: 6 if kind == "cone" else 5]]
    )
    axis = np.array([seed[2], seed[3], 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle, z = np.meshgrid(
        np.linspace(0, 2 * np.pi, 19, endpoint=False), np.linspace(-0.5, 1.0, 9)
    )
    z, angle = z.ravel(), angle.ravel()
    first = (
        np.array([seed[0], seed[1], 0.0])
        + z[:, None] * axis
        + (seed[4] + seed[6] * z)[:, None]
        * (np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v)
    )
    origin = np.array([truth[0], truth[1], 0.0])
    points = tuple(
        origin + (first - origin) @ rotation_matrix(truth, slot).T for slot in range(3)
    )
    group = RotationalPlanes(
        points, tuple(np.ones(len(p)) for p in points), kind, tuple(seed), (-2, 5)
    )
    initial = truth + np.linspace(-0.003, 0.005, len(truth))
    result = fit_coaxial(sides, plane, area, initial, rotations=(group,))
    assert result.weighted_rms < 1e-8
    np.testing.assert_allclose(result.parameters[0][:4], truth[:4], atol=1e-8)
    for p, domain, observations in zip(
        result.rotational_equations[0],
        result.rotational_domains[0],
        points,
        strict=True,
    ):
        residual, _ = cone_plane_residual_jacobian(
            observations, np.empty((0, 3)), p, domain
        )
        assert np.max(np.abs(residual)) < 1e-8
        assert p[6] == pytest.approx(seed[6], abs=1e-8)
    _, actual = residual_jacobian(sides, plane, initial, rotations=(group,))
    expected = np.empty_like(actual)
    for i in range(len(initial)):
        step = np.zeros_like(initial)
        step[i] = 2e-6
        plus = residual_jacobian(sides, plane, initial + step, rotations=(group,))[0]
        minus = residual_jacobian(sides, plane, initial - step, rotations=(group,))[0]
        expected[:, i] = (plus - minus) / 4e-6
    np.testing.assert_allclose(actual, expected, atol=2e-8, rtol=1e-6)
