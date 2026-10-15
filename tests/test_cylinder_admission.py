"""Preregistered numerical admission evidence, independent of physical tolerances."""

import hashlib
import json
from typing import Any

import numpy as np
import pytest

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import InitializationBinding, initialization_binding
from experiments.mesh_cylinder_fit import CylinderFitFailure, fit_selected_cylinder

ORIGIN = np.array([3.0, -4.0, 5.0])
ROTATION = np.array(
    [[-2 / 3, 2 / 15, 11 / 15], [2 / 3, -1 / 3, 2 / 3], [1 / 3, 14 / 15, 2 / 15]]
)
TRANSLATION = np.array([80.0, -60.0, 40.0])


def observations(
    *,
    height: float = 3.0,
    partial: bool = False,
    noise: float = 0.0,
    scale: float = 1.0,
    rotated: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    angles = (
        np.linspace(-0.8, 1.2, 24)
        if partial
        else np.linspace(0, 2 * np.pi, 32, endpoint=False)
    )
    theta, axial = np.meshgrid(angles, np.linspace(-height, height, 7))
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = (
        ORIGIN
        + axial.ravel()[:, None] * [0, 0, 1]
        + (2.5 + noise * np.sin(np.arange(theta.size) * 1.7))[:, None] * normals
    )
    if rotated:
        points = points @ ROTATION + TRANSLATION
        normals = normals @ ROTATION
    return scale * points, np.linspace(0.2, 2.0, len(points)), normals


def binding(
    points: np.ndarray, weights: np.ndarray, normals: np.ndarray
) -> InitializationBinding:
    return initialization_binding(
        hashlib.sha256(points.astype("<f8").tobytes()).hexdigest(),
        hashlib.sha256(
            weights.astype("<f8").tobytes() + normals.astype("<f8").tobytes()
        ).hexdigest(),
        list(range(len(points))),
        {
            "family": "cylinder",
            "references": {},
            "constraints": {},
            "support": None,
            "settings": {},
        },
        policy_version="selected-cylinder-recovery-v2",
    )


def geometry(result: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float]:
    value = result["analytic"]
    return np.array(value["origin"]), np.array(value["axis"]), float(value["radius"])


def independent_residual(points: np.ndarray, result: dict[str, Any]) -> np.ndarray:
    origin, axis, radius = geometry(result)
    relative = points - origin
    return np.linalg.norm(relative - (relative @ axis)[:, None] * axis, axis=1) - radius


def independent_stationarity(
    points: np.ndarray, weights: np.ndarray, result: dict[str, Any]
) -> float:
    """Five geometric directions measured in radius/axial units, not source extent."""
    frame = CoordinateFrame.from_observations(points, weights)
    origin, axis, radius = geometry(result)
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    anchor = origin + ((frame.origin - origin) @ axis) * axis
    relative = (points - anchor) / frame.length
    axial = relative @ axis
    radial = relative - axial[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    unit = radial / rho[:, None]
    normalized = weights / weights.sum()
    local_radius = radius / frame.length
    axial_rms = np.sqrt(normalized @ ((axial - normalized @ axial) ** 2))
    angular = local_radius / axial_rms
    scales = np.array([local_radius, local_radius, angular, angular, local_radius])
    jacobian = np.column_stack(
        (
            -unit @ first,
            -unit @ second,
            -axial * (unit @ first),
            -axial * (unit @ second),
            -np.ones(len(points)),
        )
    )
    return float(
        np.max(np.abs((jacobian * scales).T @ (normalized * (rho - local_radius))))
        / local_radius**2
    )


def assert_geometry(
    result: dict[str, Any], *, scale: float, rotated: bool, tolerance: float
) -> None:
    origin, axis, radius = geometry(result)
    expected_origin = scale * (ORIGIN @ ROTATION + TRANSLATION if rotated else ORIGIN)
    expected_axis = np.array([0.0, 0, 1]) @ (ROTATION if rotated else np.eye(3))
    nearest = origin + ((expected_origin - origin) @ axis) * axis
    assert np.linalg.norm(np.cross(axis, expected_axis)) < tolerance
    assert np.linalg.norm((nearest - expected_origin) / scale) < tolerance
    assert radius / scale == pytest.approx(2.5, abs=tolerance)
    assert "parameters" not in result


def assert_evidence(result: dict[str, Any], count: int) -> None:
    initialization = result["initialization"]
    assert initialization["algorithm"] == "selected-cylinder-recovery-v2"
    assert initialization["coverage_metric"] == "radius-and-axial-rms-covariance-v1"
    assert initialization["observations"] == count
    assert initialization["minimum_coverage_ratio"] == 1e-10
    assert initialization["coverage_ratio"] > 1e-10
    assert len(initialization["raw_normalized_covariance_eigenvalues"]) == 3
    recovery = initialization["recovery"]
    selected = recovery["selected"]
    assert (
        initialization["assessments"][selected]["coverage_ratio"]
        == initialization["coverage_ratio"]
    )
    fresh = [attempt for attempt in recovery["attempts"] if attempt["role"] == "fresh"]
    assert 1 <= len(fresh) <= 6
    assert initialization["chart_slope_bounds"] == [-2.0, 2.0]
    for attempt in fresh:
        if attempt["solver"] is not None:
            # Wrong-axis long-cylinder attempts previously escaped toward the
            # chart horizon (slopes >100), amplifying native/worker roundoff.
            slopes = np.asarray(attempt["solver"]["parameters"])[2:4]
            assert np.isfinite(slopes).all() and np.all(np.abs(slopes) <= 2)
    if recovery["selected_role"] == "fresh":
        assert initialization["initializers"][selected]["rank"] == 3
        assert np.isfinite(initialization["initializers"][selected]["condition"])
    scales = np.array(result["solver"]["parameter_scales"])
    assert scales.shape == (5,) and np.isfinite(scales).all() and np.all(scales > 0)
    assert np.isfinite(result["solver"]["residual_scale"])
    assert result["solver"]["residual_scale"] > 0
    assert result["solver"]["scaled_projected_gradient_infinity_norm"] <= 1e-10
    assert np.isfinite(result["solver"]["scaled_jacobian_condition"])
    assert result["solver"]["aligned_jacobian_condition"] <= 1e6
    _ = json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("rotated", [False, True])
def test_long_cylinder_geometry_overcomes_raw_extent_false_rejection(
    partial: bool, scale: float, rotated: bool
) -> None:
    points, weights, normals = observations(
        height=3e5, partial=partial, scale=scale, rotated=rotated
    )
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_geometry(fitted, scale=scale, rotated=rotated, tolerance=1e-8)
    assert fitted["initialization"]["raw_coverage_ratio"] < 1e-10
    assert fitted["weighted_rms"] / scale < 1e-8
    assert independent_stationarity(points, weights, fitted) < 1e-8
    assert_evidence(fitted, len(points))


@pytest.mark.parametrize("noise", [0.0, 0.005])
def test_ordinary_partial_cylinder_keeps_its_weighted_geometric_optimum(
    noise: float,
) -> None:
    points, weights, normals = observations(partial=True, noise=noise)
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_geometry(fitted, scale=1, rotated=False, tolerance=0.001 if noise else 1e-9)
    assert independent_stationarity(points, weights, fitted) < 1e-8
    residual = independent_residual(points, fitted)
    np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-12)
    assert fitted["weighted_rms"] == pytest.approx(
        np.sqrt(np.average(residual**2, weights=weights)), abs=1e-12
    )
    if noise:
        assert fitted["analytic"]["radius"] == pytest.approx(2.50023436408978, abs=2e-8)
        assert fitted["weighted_rms"] == pytest.approx(0.00354172308697244, abs=1e-11)
        assert fitted["solver"]["iterations"] > 0
    assert_evidence(fitted, len(points))


@pytest.mark.parametrize("noise", [0.0, 0.005])
def test_extreme_aspect_partial_never_publishes_a_wrong_radius(noise: float) -> None:
    points, weights, normals = observations(height=3e15, partial=True, noise=noise)
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_geometry(fitted, scale=1, rotated=False, tolerance=0.001 if noise else 1e-8)
    assert independent_stationarity(points, weights, fitted) < 1e-8
    if noise:
        assert fitted["analytic"]["radius"] == pytest.approx(2.50023436408978, abs=2e-8)
        assert fitted["weighted_rms"] == pytest.approx(0.00354172308697244, abs=1e-10)
        assert fitted["solver"]["iterations"] > 0
    assert_evidence(fitted, len(points))
    selected = fitted["initialization"]["recovery"]["selected"]
    assert fitted["initialization"]["initializers"][selected]["condition"] < 4
    # The solve scales must resolve radius changes despite their tiny size in
    # the chart normalized by an axial length of order1e15.
    assert fitted["solver"]["residual_scale"] < 1e-14


def test_misleading_normals_do_not_replace_the_long_partial_positions() -> None:
    points, weights, normals = observations(height=3e5, partial=True, rotated=True)
    normals[:] = [0, 0, 1]
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_geometry(fitted, scale=1, rotated=True, tolerance=1e-8)
    assert independent_stationarity(points, weights, fitted) < 1e-8
    assert_evidence(fitted, len(points))


def test_short_but_identifiable_cylinder_retains_axial_sensitivity() -> None:
    points, weights, normals = observations(height=3e-5)
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_geometry(fitted, scale=1, rotated=False, tolerance=1e-8)
    assert fitted["solver"]["aligned_jacobian_condition"] > 1e5
    assert_evidence(fitted, len(points))


@pytest.mark.parametrize("defect", ["stale", "invalid", "huge"])
def test_optional_hints_preserve_complete_fresh_evidence(defect: str) -> None:
    points, weights, normals = observations(partial=True, noise=0.005, rotated=True)
    request = binding(points, weights, normals)
    before = [array.copy() for array in (points, weights, normals)]
    baseline = fit_selected_cylinder(points, weights, normals, request)
    hint: dict[str, Any] = {
        "origin": [3.0, -4.0, 5.0],
        "axis": [0.0, 0.0, 1.0],
        "radius": 2.5,
    }
    if defect == "invalid":
        hint["origin"] = [np.nan, 0, 0]
    elif defect == "huge":
        hint["origin"], hint["radius"] = [1e100, -1e100, 1e100], 1e100
    fitted = fit_selected_cylinder(
        points,
        weights,
        normals,
        request,
        hint=hint,
        hint_binding_sha256="0" * 64 if defect == "stale" else None,
    )
    expected = baseline["initialization"]["recovery"]["attempts"]
    actual = fitted["initialization"]["recovery"]["attempts"]
    assert [attempt for attempt in actual if attempt["role"] == "fresh"] == expected
    assert actual[-1]["role"] == "hint" and actual[-1]["evaluations"] == 0
    assert actual[-1]["outcome"] == "screened-invalid-or-stale"
    np.testing.assert_allclose(
        independent_residual(points, fitted),
        independent_residual(points, baseline),
        atol=1e-12,
    )
    for observed, original in zip((points, weights, normals), before, strict=True):
        np.testing.assert_array_equal(observed, original)
    assert_evidence(fitted, len(points))


@pytest.mark.parametrize(
    "family", ["strip", "circle", "generators", "plane", "noisy-plane"]
)
def test_new_admission_keeps_unsupported_data_from_publishing_geometry(
    family: str,
) -> None:
    angles = (
        np.linspace(-1e-7, 1e-7, 32)
        if family == "strip"
        else np.array([-0.5, 0.5])
        if family == "generators"
        else np.linspace(0, 2 * np.pi, 32, endpoint=False)
    )
    theta, axial = np.meshgrid(
        angles, [0.0] if family == "circle" else np.linspace(-3, 3, 7)
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = 2.5 * normals + axial.ravel()[:, None] * [0, 0, 1]
    if "plane" in family:
        x, y = np.meshgrid(np.linspace(-3, 3, 12), np.linspace(-2, 2, 11))
        points = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        normals = np.tile([0.0, 0, 1], (len(points), 1))
        if family == "noisy-plane":
            points[:, 2] = 0.005 * np.sin(np.arange(len(points)) * 1.7)
    weights = np.linspace(0.2, 2, len(points))
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(
            points, weights, normals, binding(points, weights, normals)
        )
    assert failure.value.code
    diagnostic = failure.value.diagnostic
    assert diagnostic["raw_coverage_ratio"] >= 0
    assert diagnostic["coverage_metric"] == "radius-and-axial-rms-covariance-v1"
    assert len(diagnostic["recovery"]["attempts"]) <= 6
    _ = json.dumps(diagnostic, allow_nan=False)


def test_underflowing_transverse_extent_keeps_all_admission_evidence_strict_json() -> (
    None
):
    theta, axial = np.meshgrid(
        np.linspace(0, 2 * np.pi, 16, endpoint=False), np.linspace(-1, 1, 5)
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = np.column_stack(
        (1e-310 * normals[:, 0], 1e-310 * normals[:, 1], axial.ravel())
    )
    weights = np.ones(len(points))
    assert np.isfinite(points).all() and np.isfinite(normals).all()
    try:
        evidence = fit_selected_cylinder(
            points, weights, normals, binding(points, weights, normals)
        )
    except CylinderFitFailure as error:
        assert error.code
        evidence = error.diagnostic
    # Numerical rejection is acceptable here; diagnostics from every attempted
    # projected circle must still survive the strict native/worker JSON boundary.
    _ = json.dumps(evidence, allow_nan=False)
    initialization = evidence.get("initialization", evidence)
    for initializer in initialization["initializers"].values():
        assert initializer["condition"] is None or np.isfinite(initializer["condition"])


def test_large_radius_hint_cannot_pass_with_only_proposal_scaled_stationarity() -> None:
    points, weights, normals = observations(height=3e5, partial=True)
    request = binding(points, weights, normals)
    baseline = fit_selected_cylinder(points, weights, normals, request)
    fitted = fit_selected_cylinder(
        points,
        weights,
        normals,
        request,
        hint={
            "origin": [3.1, -4.0, 5.0],
            "axis": [0.0, 0.0, 1.0],
            "radius": 1e10,
        },
    )
    expected = baseline["initialization"]["recovery"]["attempts"]
    actual = fitted["initialization"]["recovery"]["attempts"]
    assert [attempt for attempt in actual if attempt["role"] == "fresh"] == expected
    assert actual[-1]["role"] == "hint"
    assert actual[-1]["outcome"] == "invalid-result"
    assert actual[-1]["evaluations"] > 0
    hint_assessment = fitted["initialization"]["assessments"]["optional-cylinder-hint"]
    assert hint_assessment["aligned_scaled_gradient_infinity_norm"] > 1e-10
    assert fitted["analytic"] == baseline["analytic"]
    assert_geometry(fitted, scale=1, rotated=False, tolerance=1e-8)
    assert independent_stationarity(points, weights, fitted) < 1e-10
    assert_evidence(fitted, len(points))
