"""Independent geometry and derivative checks for the exploratory cylinder fit."""

import numpy as np

from experiments.mesh_cylinder_fit import fit_cylinder, residual_jacobian


def test_cylinder_recovery() -> None:
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    theta, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 48, endpoint=False), np.linspace(-2, 2, 9)
    )
    points = (
        np.array([0.3, -0.4, 0])
        + height.ravel()[:, None] * axis
        + 3.2
        * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v)
    )
    weights = np.linspace(0.1, 2, len(points))
    result = fit_cylinder(points, weights, np.array([0.0, 0, 0, 0, 3]))
    np.testing.assert_allclose(
        result["parameters"], [0.3, -0.4, 0.12, -0.08, 3.2], atol=1e-8
    )
    assert result["converged"]


def test_jacobian_against_central_differences() -> None:
    points = np.array([[2.0, 3, 1], [-3, 2, -2], [4, -1, 3], [-1, -4, -1]])
    parameters = np.array([0.2, -0.1, 0.05, -0.03, 3])
    _, actual = residual_jacobian(points, parameters)
    expected = np.empty_like(actual)
    for j in range(5):
        delta = np.zeros(5)
        delta[j] = 1e-6
        plus, _ = residual_jacobian(points, parameters + delta)
        minus, _ = residual_jacobian(points, parameters - delta)
        expected[:, j] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-8)
