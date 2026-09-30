"""Fixed observations and recovered geometry must not depend on display support."""

from types import SimpleNamespace
from typing import cast

import numpy as np
import pytest

from experiments.fit_solver import normalized_weights
from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
    fit_cone_plane,
)
from experiments.mesh_cylinder_fit import fit_cylinder, residual_jacobian
from experiments.mesh_cylinder_plane_fit import fit_cylinder_plane
from experiments.nozzle_coaxial import FitSelection, fit_fixed_axis_group
from experiments.nozzle_session import NozzleWorkspace
from experiments.selection_growth import fit_seed, surface_distance


def side(taper: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    slopes = (0.12, -0.08)
    axis = np.array([*slopes, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle, z = np.meshgrid(
        np.linspace(0, 2 * np.pi, 40, endpoint=False), np.linspace(-1, 1, 7)
    )
    radial = np.cos(angle.ravel())[:, None] * u + np.sin(angle.ravel())[:, None] * v
    points = (
        [0.2, -0.3, 0.0]
        + z.ravel()[:, None] * axis
        + (3.0 + taper * z.ravel())[:, None] * radial
    )
    normals = (radial - taper * axis) / np.hypot(1.0, taper)
    plane = 4.0 * axis + radial[:40]
    return points, normals, plane


def test_primitive_residual_and_membership_are_separate() -> None:
    angle = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    points = np.column_stack((3 * np.cos(angle), 3 * np.sin(angle), np.full(32, 2.0)))
    parameters = np.array([0.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0])
    residual, jacobian = cone_plane_residual_jacobian(
        points, np.empty((0, 3)), parameters, (0.0, 1.0)
    )
    expected, derivative = residual_jacobian(points, parameters[:5])
    np.testing.assert_array_equal(residual, expected)
    np.testing.assert_array_equal(jacobian[:, :5], derivative)
    _, _, inside = surface_distance(
        points,
        {"kind": "cylinder", "parameters": parameters, "axial_domain": (0.0, 1.0)},
    )
    assert not inside.any()


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("shift_z", [0.0, -1000.0])
def test_fixed_axis_cone_uses_the_same_orthogonal_objective(
    scale: float, shift_z: float
) -> None:
    points, normals, _ = side(0.3)
    points += 0.05 * np.cos(np.arange(len(points)) * 1.7)[:, None] * normals
    points *= scale
    translation = scale * np.array([800.0, -600.0, shift_z])
    points += translation
    weights = np.linspace(0.3, 1.3, len(points))
    workspace = cast(
        NozzleWorkspace,
        cast(
            object,
            SimpleNamespace(
                local=points,
                data=SimpleNamespace(weights=weights),
                default=SimpleNamespace(source_sha256="test"),
                model_sha256="test",
            ),
        ),
    )
    axis = np.array([0.2 * scale, -0.3 * scale, 0.12, -0.08, 3 * scale, 0.0, 0.3])
    axis[:2] += translation[:2] - axis[2:4] * translation[2]
    delta = np.linalg.norm([*axis[2:4], 1.0]) * translation[2]
    axis[4] -= axis[6] * delta
    domain = (-0.5 * scale + delta, 0.5 * scale + delta)
    result = fit_fixed_axis_group(
        workspace,
        [FitSelection("cone", list(range(len(points))), "cone", domain)],
        [],
        axis,
    )
    assert "surfaces" in result
    fitted = result["surfaces"]["cone"]
    parameters = np.asarray(fitted["parameters"])
    residual, jacobian = cone_plane_residual_jacobian(
        points, np.empty((0, 3)), parameters, domain
    )
    np.testing.assert_allclose(parameters[:4], axis[:4], rtol=0, atol=1e-12 * scale)
    np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-10 * scale)
    # The old radial-regression answer is not stationary for this objective.
    gradient = jacobian[:, [4, 6]].T @ (weights * residual / weights.sum())
    gradient[1] += delta * gradient[0]
    assert np.max(np.abs(gradient / [scale, scale**2])) < 1e-8
    assert len(residual) == len(points)
    assert "solver" in fitted
    assert fitted["solver"]["rank"] == 2


def test_extreme_weight_range_is_not_silently_zeroed() -> None:
    with pytest.raises(ValueError, match="underflows"):
        _ = normalized_weights(np.array([1e-300, 1e300]))


def test_cone_structural_validity_is_not_relaxed() -> None:
    points, _, plane = side()
    with pytest.raises(InvalidConeDomain, match="positive"):
        _ = cone_plane_residual_jacobian(
            points, plane, np.array([0.0, 0, 0, 0, 3, 4, 1]), (-4, 6)
        )
    with pytest.raises(InvalidConeDomain, match="increasing"):
        _ = cone_plane_residual_jacobian(
            points, plane, np.array([0.0, 0, 0, 0, 3, 4, 0]), (2, 1)
        )


@pytest.mark.parametrize("kind,taper", [("cylinder", 0.0), ("cone", 0.08)])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_seed_geometry_recovery_is_unit_covariant(
    kind: str, taper: float, scale: float
) -> None:
    points, normals, _ = side(taper)
    fitted = fit_seed(
        points * scale,
        np.linspace(0.2, 2.0, len(points)),
        normals,
        kind,
        np.array([0.0, 0.0, 0.12, -0.08, 2.9 * scale]),
        (-2 * scale, 2 * scale),
    )
    recovered = np.asarray(fitted["parameters"])
    recovered[[0, 1, 4]] /= scale
    np.testing.assert_allclose(
        recovered, [0.2, -0.3, 0.12, -0.08, 3.0, 0.0, taper], atol=1e-8
    )
    assert fitted["weighted_rms"] / scale < 1e-9


@pytest.mark.parametrize("solver", ["seed", "cylinder", "joint"])
@pytest.mark.parametrize("shift", [0.0, 1000.0])
def test_cylinder_geometry_recovery_is_translation_covariant(
    solver: str, shift: float
) -> None:
    points, normals, plane = side()
    offset = np.array([1200.0, -2400.0, shift])
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    initial = np.array(
        [offset[0] - 0.12 * shift, offset[1] + 0.08 * shift, 0.12, -0.08, 2.9]
    )
    weights = np.linspace(0.2, 2, len(points))
    if solver == "seed":
        fitted = fit_seed(
            points + offset,
            weights,
            normals,
            "cylinder",
            initial,
            tuple(np.array([-2, 2]) + shift / axis[2]),
        )
    elif solver == "cylinder":
        fitted = fit_cylinder(points + offset, weights, initial)
    else:
        fitted = fit_cylinder_plane(
            points + offset,
            plane + offset,
            weights,
            np.ones(len(plane)),
            np.append(initial, 4.0 + axis @ offset),
        )
    parameters = np.asarray(fitted["parameters"])
    # Compare the line at the original patch, not its distant z=0 gauge anchor.
    anchor = parameters[:2] + parameters[2:4] * shift - offset[:2]
    np.testing.assert_allclose(anchor, [0.2, -0.3], atol=1e-8)
    np.testing.assert_allclose(parameters[2:5], [0.12, -0.08, 3.0], atol=1e-8)
    assert fitted["weighted_rms"] < 1e-9


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_cone_plane_recovery_is_unit_covariant(scale: float) -> None:
    points, _, plane = side(0.08)
    fitted = fit_cone_plane(
        points * scale,
        plane * scale,
        np.ones(len(points)),
        np.ones(len(plane)),
        np.array([0.0, 0, 0.12, -0.08, 2.9 * scale, 3.9 * scale, 0.0]),
        (-2 * scale, 6 * scale),
    )
    recovered = np.asarray(fitted["parameters"])
    recovered[[0, 1, 4, 5]] /= scale
    np.testing.assert_allclose(
        recovered, [0.2, -0.3, 0.12, -0.08, 3.0, 4.0, 0.08], atol=1e-8
    )


def test_unobservable_taper_is_still_rejected() -> None:
    angle = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    ring = np.column_stack((3 * np.cos(angle), 3 * np.sin(angle), np.zeros(40)))
    normals = ring / 3
    with pytest.raises(ValueError, match="rank-deficient"):
        _ = fit_seed(
            ring,
            np.ones(len(ring)),
            normals,
            "cone",
            np.array([0, 0, 0, 0, 3.0]),
            (-2, 2),
        )


def test_support_crossing_keeps_all_selected_observations() -> None:
    points, normals, _ = side()
    fitted = fit_seed(
        points,
        np.ones(len(points)),
        normals,
        "cylinder",
        np.array([0, 0, 0, 0, 2.9]),
        (-0.2, 0.2),
    )
    assert len(fitted["residuals"]) == len(points)
    assert fitted["support_classification"]["outside_vertices"] > 0
    assert fitted["weighted_rms"] < 1e-9
    assert fitted["solver"]["termination"] == "projected-gradient"


@pytest.mark.parametrize("angle", [0.0, 0.7, 1.9])
def test_cylinder_recovery_under_rigid_rotation_within_chart(angle: float) -> None:
    points, normals, _ = side()
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, -sine, 0], [sine, cosine, 0], [0, 0, 1]])
    fitted = fit_seed(
        points @ rotation.T,
        np.ones(len(points)),
        normals @ rotation.T,
        "cylinder",
        np.array([0, 0, 0, 0, 2.9]),
        (-2, 2),
    )
    expected_anchor = np.array([0.2, -0.3, 0]) @ rotation.T
    expected_axis = np.array([0.12, -0.08, 1.0]) @ rotation.T
    np.testing.assert_allclose(fitted["parameters"][:2], expected_anchor[:2], atol=1e-8)
    np.testing.assert_allclose(fitted["parameters"][2:4], expected_axis[:2], atol=1e-8)


def test_cone_apex_crossing_and_axis_projection_are_geometry_failures() -> None:
    points, normals, _ = side()
    invalid = points.copy()
    invalid[0] = 0.0
    with pytest.raises(ValueError, match="invalid-initial-geometry"):
        _ = fit_seed(
            invalid,
            np.ones(len(points)),
            normals,
            "cylinder",
            np.array([0, 0, 0, 0, 3.0]),
            (-2, 2),
        )
    with pytest.raises(InvalidConeDomain, match="apex"):
        _ = cone_plane_residual_jacobian(
            np.array([[1.0, 0.0, -10.0]]),
            np.empty((0, 3)),
            np.array([0, 0, 0, 0, 3.0, 0, 1.0]),
            (0, 1),
        )


@pytest.mark.parametrize("count", [16, 32, 64])
def test_noisy_geometry_recovery_and_independent_held_out_residuals(count: int) -> None:
    angle, z = np.meshgrid(
        np.linspace(0, 2 * np.pi, count, endpoint=False), np.linspace(-1, 1, 7)
    )
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    radial = np.cos(angle.ravel())[:, None] * u + np.sin(angle.ravel())[:, None] * v
    center = np.array([0.2, -0.3, 0.0])
    noise = 0.01 * np.cos(3 * angle.ravel())
    points = center + z.ravel()[:, None] * axis + (3 + noise)[:, None] * radial
    fitted = fit_seed(
        points,
        np.ones(len(points)),
        radial,
        "cylinder",
        np.array([0, 0, 0, 0, 2.9]),
        (-2, 2),
    )
    np.testing.assert_allclose(
        fitted["parameters"][:5], [0.2, -0.3, 0.12, -0.08, 3], atol=1e-8
    )
    assert fitted["weighted_rms"] == pytest.approx(0.01 / np.sqrt(2), abs=1e-10)
    assert len(fitted["residuals"]) == len(points)
    # Different angles and deviation profile, never passed to fitting.
    held_angle = np.linspace(0, 2 * np.pi, 73, endpoint=False) + 0.13
    held_radial = np.cos(held_angle)[:, None] * u + np.sin(held_angle)[:, None] * v
    held_noise = 0.02 * np.sin(5 * held_angle)
    held_points = center + 0.37 * axis + (3 + held_noise)[:, None] * held_radial
    residual, _ = residual_jacobian(held_points, np.asarray(fitted["parameters"][:5]))
    np.testing.assert_allclose(residual, held_noise, atol=1e-8)


def test_relative_weights_survive_a_large_common_multiplier() -> None:
    points, normals, plane = side()
    weights = np.linspace(0.2, 2.0, len(points))
    start = np.array([0, 0, 0.12, -0.08, 2.9])
    small = fit_seed(points, weights, normals, "cylinder", start, (-2, 2))
    large = fit_seed(points, weights * 1e307, normals, "cylinder", start, (-2, 2))
    np.testing.assert_allclose(large["parameters"], small["parameters"], atol=1e-10)
    assert np.isfinite(large["weighted_rms"])
    assert large["normal_sign"] == small["normal_sign"]
    joint = fit_cylinder_plane(
        points,
        plane,
        weights * 1e307,
        np.ones(len(plane)) * 1e307,
        np.append(start, 4.0),
    )
    assert np.isfinite(joint["cylinder_weighted_rms"])
    np.testing.assert_allclose(
        joint["parameters"], [0.2, -0.3, 0.12, -0.08, 3, 4], atol=1e-8
    )
