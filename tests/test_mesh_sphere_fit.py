"""Independent geometry and derivative checks for the exploratory sphere fit."""

import numpy as np
import pytest

from experiments.mesh_sphere_fit import fit_sphere, residual_jacobian


def sphere_points(center: np.ndarray, radius: float) -> np.ndarray:
    azimuth, elevation = np.meshgrid(
        np.linspace(0, 2 * np.pi, 24, endpoint=False),
        np.linspace(-0.9, 1.1, 9),
    )
    directions = np.column_stack(
        (
            np.cos(elevation.ravel()) * np.cos(azimuth.ravel()),
            np.cos(elevation.ravel()) * np.sin(azimuth.ravel()),
            np.sin(elevation.ravel()),
        )
    )
    return center + radius * directions


def test_sphere_recovery_uses_area_weights() -> None:
    center = np.array([1.2, -3.4, 0.7])
    points = sphere_points(center, 2.6)
    weights = np.linspace(0.2, 2.0, len(points))

    result = fit_sphere(points, weights)

    np.testing.assert_allclose(result["parameters"], [*center, 2.6], atol=1e-10)
    assert result["weighted_rms"] < 1e-12
    assert result["converged"]


def test_noisy_sphere_fit_is_translation_invariant() -> None:
    points = sphere_points(np.zeros(3), 5.0)
    radial = points / np.linalg.norm(points, axis=1)[:, None]
    noise = 0.015 * np.sin(np.arange(len(points)) * 1.7)
    noisy = points + noise[:, None] * radial
    weights = np.linspace(0.2, 2.0, len(points))
    translation = np.array([1e8, -2e8, 3e8])

    local = fit_sphere(noisy, weights)
    translated = fit_sphere(noisy + translation, weights)

    np.testing.assert_allclose(
        np.asarray(translated["parameters"][:3]) - translation,
        local["parameters"][:3],
        atol=1e-7,
        rtol=0,
    )
    assert translated["parameters"][3] == pytest.approx(
        local["parameters"][3], abs=1e-8
    )
    assert translated["weighted_rms"] == pytest.approx(local["weighted_rms"], abs=1e-9)


def test_sphere_jacobian_matches_central_differences() -> None:
    points = np.array(
        [[2.0, 3.0, 1.0], [-3.0, 2.0, -2.0], [4.0, -1.0, 3.0], [-1.0, -4.0, -1.0]]
    )
    parameters = np.array([0.2, -0.1, 0.4, 3.0])
    _, actual = residual_jacobian(points, parameters)
    expected = np.empty_like(actual)
    for column in range(4):
        delta = np.zeros(4)
        delta[column] = 1e-6
        plus, _ = residual_jacobian(points, parameters + delta)
        minus, _ = residual_jacobian(points, parameters - delta)
        expected[:, column] = (plus - minus) / (2e-6)
    np.testing.assert_allclose(actual, expected, rtol=1e-8, atol=1e-9)


def test_sphere_rejects_coplanar_observations() -> None:
    points = np.array(
        [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0], [-1.0, 1.0, 0.0]]
    )
    with pytest.raises(ValueError, match="wider curved patch"):
        _ = fit_sphere(points, np.ones(len(points)))


@pytest.mark.parametrize(
    "points,weights",
    [
        (np.zeros((3, 3)), np.ones(3)),
        (np.zeros((4, 2)), np.ones(4)),
        (np.eye(4, 3), np.ones(3)),
        (np.eye(4, 3), np.array([1.0, 1.0, 1.0, 0.0])),
    ],
)
def test_sphere_rejects_invalid_inputs(points: np.ndarray, weights: np.ndarray) -> None:
    with pytest.raises(ValueError):
        _ = fit_sphere(points, weights)
