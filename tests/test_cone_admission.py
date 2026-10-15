"""Preregistered cone admission across aspect, taper and original observations."""

import hashlib
import json
from typing import Any

import numpy as np
import pytest

from experiments.fit_initialization import RecoveryPolicy, initialization_binding
from experiments.fit_solver import AttemptLimits, FitCancelled
from experiments.mesh_cone_fit import (
    CONE_POLICY_VERSION,
    ConeFitFailure,
    fit_selected_cone,
)
from experiments.support_intent import PlaneSupport, SupportIntent, support_record

ORIGIN = np.array([80.0, -60.0, 40.0])
AXIS = np.array([0.0, 0.0, 1.0])
ROTATION = np.array(
    [[-2 / 3, 2 / 15, 11 / 15], [2 / 3, -1 / 3, 2 / 3], [1 / 3, 14 / 15, 2 / 15]]
)
TRANSLATION = np.array([20.0, 30.0, -10.0])


def observations(
    *,
    height: float = 3.0,
    radius: float = 3.0,
    slope: float = 0.15,
    full: bool = False,
    noise: float = 0.0,
    levels: np.ndarray | None = None,
    angles: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if angles is None:
        angles = (
            np.linspace(0, 2 * np.pi, 32, endpoint=False)
            if full
            else np.linspace(-1.4, 1.6, 24)
        )
    theta, axial = np.meshgrid(
        angles, np.linspace(-height, height, 7) if levels is None else levels
    )
    h = axial.ravel()
    radial = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = (
        ORIGIN
        + h[:, None] * AXIS
        + (radius + slope * h + noise * np.sin(1.7 * np.arange(len(h))))[:, None]
        * radial
    )
    normals = (radial - slope * AXIS) / np.hypot(1, slope)
    return points, np.linspace(0.2, 2, len(points)), normals


def fit(
    points: np.ndarray,
    weights: np.ndarray,
    normals: np.ndarray,
    *,
    support: SupportIntent | None = None,
    policy: RecoveryPolicy | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    request = initialization_binding(
        hashlib.sha256(points.astype("<f8").tobytes()).hexdigest(),
        hashlib.sha256(
            weights.astype("<f8").tobytes() + normals.astype("<f8").tobytes()
        ).hexdigest(),
        list(range(len(points))),
        {
            "family": "cone",
            "support": None if support is None else support_record(support),
        },
        policy_version=CONE_POLICY_VERSION,
        policy=policy,
    )
    return fit_selected_cone(
        points, weights, normals, request, support=support, **kwargs
    )


def geometry(result: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float, float]:
    a = result["analytic"]
    return (
        np.asarray(a["origin"]),
        np.asarray(a["axis"]),
        float(a["radius"]),
        float(a["slope"]),
    )


def residual_and_gradient(
    points: np.ndarray, weights: np.ndarray, result: dict[str, Any]
) -> tuple[np.ndarray, float]:
    origin, axis, radius, slope = geometry(result)
    w = weights / weights.sum()
    center = points[0] + w @ (points - points[0])
    shift = (center - origin) @ axis
    anchor, radius = origin + shift * axis, radius + slope * shift
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    xyz = (points - anchor) @ np.column_stack((first, np.cross(axis, first), axis))
    x, y, h = xyz.T
    rho = np.hypot(x, y)
    ux, uy = x / rho, y / rho
    s = np.hypot(1, slope)
    residual = (rho - radius - slope * h) / s
    jacobian = np.column_stack(
        (
            -ux / s,
            -uy / s,
            (-h * ux - slope * x) / s,
            (-h * uy - slope * y) / s,
            -np.ones(len(points)) / s,
            -h / s - residual * slope / s**2,
        )
    )
    projected_radius = radius / s / s + (slope / s) * (h / s) + (slope / s) ** 2 * rho
    radial_scale = float(np.sqrt(w @ projected_radius**2))
    axial_scale = float(np.sqrt(w @ (h - w @ h) ** 2))
    parameter_scales = np.array(
        [
            radial_scale,
            radial_scale,
            radial_scale / axial_scale,
            radial_scale / axial_scale,
            radial_scale,
            radial_scale / axial_scale,
        ]
    )
    gradient = (
        np.max(np.abs((jacobian * parameter_scales).T @ (w * residual)))
        / radial_scale**2
    )
    return residual, float(gradient)


def assert_finite_sheet(points: np.ndarray, fitted: dict[str, Any]) -> None:
    origin, axis, radius, slope = geometry(fitted)
    bounds = np.asarray(fitted["analytic"]["axial_domain"])
    assert bounds.shape == (2,) and np.isfinite(bounds).all() and bounds[0] < bounds[1]
    assert np.all(radius + slope * bounds > 0)
    h = (points - origin) @ axis
    rho = np.linalg.norm(points - origin - h[:, None] * axis, axis=1)
    projected = (radius + slope * h + slope**2 * rho) / (1 + slope**2)
    assert np.all(projected > 0)
    _ = json.dumps(fitted, allow_nan=False)


def assert_admission_evidence(
    points: np.ndarray, weights: np.ndarray, fitted: dict[str, Any]
) -> None:
    origin, axis, radius, slope = geometry(fitted)
    w = weights / weights.sum()
    relative = points - origin
    h = relative @ axis
    radial = relative - h[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    s = np.hypot(1, slope)
    positive_radius = radius / s**2 + slope / s * (h / s) + (slope / s) ** 2 * rho
    r_rms = float(np.sqrt(w @ positive_radius**2))
    h_rms = float(np.sqrt(w @ (h - w @ h) ** 2))
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    xyz = relative @ np.column_stack((first, np.cross(axis, first), axis))
    xyz = (xyz - w @ xyz) / [r_rms, r_rms, h_rms]
    eigenvalues = np.linalg.eigvalsh(xyz.T @ (w[:, None] * xyz))
    expected = float(max(eigenvalues[0], 0) / eigenvalues[-1])
    initialization = fitted["initialization"]
    selected = initialization["recovery"]["selected"]
    assessment = initialization["assessments"][selected]
    assert assessment["coverage_ratio"] == pytest.approx(expected, rel=1e-8, abs=1e-12)
    assert assessment["projected_radius_rms"] > 0
    assert initialization["minimum_coverage_ratio"] == 1e-10
    assert initialization["maximum_aligned_jacobian_condition"] == 1e6
    assert fitted["solver"]["aligned_jacobian_condition"] <= 1e6
    assert fitted["solver"]["aligned_scaled_gradient_infinity_norm"] <= 1e-10
    scales = np.asarray(fitted["solver"]["parameter_scales"])
    assert scales.shape == (6,) and np.isfinite(scales).all() and np.all(scales > 0)
    assert fitted["solver"]["residual_scale"] > 0


def assert_truth(
    fitted: dict[str, Any],
    *,
    origin: np.ndarray = ORIGIN,
    axis: np.ndarray = AXIS,
    radius: float = 3.0,
    slope: float = 0.15,
    extent: float = 3.0,
    tolerance: float = 1e-7,
) -> None:
    point, direction, actual_radius, actual_slope = geometry(fitted)
    sign = 1 if direction @ axis >= 0 else -1
    nearest = point + ((origin - point) @ direction) * direction
    assert np.linalg.norm(nearest - origin) / radius < tolerance
    assert np.linalg.norm(np.cross(direction, axis)) * extent / radius < tolerance
    radius_at_source = actual_radius + actual_slope * ((origin - point) @ direction)
    assert radius_at_source / radius == pytest.approx(1, abs=tolerance)
    assert abs(actual_slope - sign * slope) * extent / radius < tolerance
    assert fitted["kind"] == fitted["analytic"]["kind"] == "cone"
    assert "parameters" not in fitted


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("posed", [False, True])
@pytest.mark.parametrize("scale", [1e-6, 1e6])
def test_long_identifiable_cone_is_not_rejected_for_world_aspect_ratio(
    full: bool, posed: bool, scale: float
) -> None:
    points, weights, normals = observations(height=6e5, slope=1e-7, full=full)
    origin, axis = ORIGIN.copy(), AXIS.copy()
    if posed:
        points, normals = points @ ROTATION + TRANSLATION, normals @ ROTATION
        origin, axis = origin @ ROTATION + TRANSLATION, axis @ ROTATION
    points, origin = points * scale, origin * scale
    fitted = fit(points, weights, normals)
    assert_truth(
        fitted,
        origin=origin,
        axis=axis,
        radius=3 * scale,
        slope=1e-7,
        extent=6e5 * scale,
    )
    residual, gradient = residual_and_gradient(points, weights, fitted)
    assert np.sqrt(np.average(residual**2, weights=weights)) / scale < 1e-8
    assert gradient < 1e-9
    assert_finite_sheet(points, fitted)
    assert fitted["initialization"]["raw_coverage_ratio"] < 1e-10
    assert_admission_evidence(points, weights, fitted)


@pytest.mark.parametrize(
    "case",
    [
        "ordinary-full",
        "ordinary-partial",
        "zero-taper",
        "varying-radius",
        "apex",
        "negative-anchor",
    ],
)
def test_admission_preserves_existing_taper_and_finite_sheet_controls(
    case: str,
) -> None:
    radius, slope, support = 3.0, 0.15, None
    kwargs: dict[str, Any] = {"full": case == "ordinary-full"}
    if case == "zero-taper":
        slope = 0.0
    elif case == "varying-radius":
        slope = 0.8
    elif case == "apex":
        radius = 0.3
        kwargs["levels"] = np.array([-1.99, -1.9, -1.5, -0.5, 1.0])
    elif case == "negative-anchor":
        # At plane Z0 this positive patch has radius3-.15*40=-3.
        support = PlaneSupport((0.0, 0.0, 1.0), 0.0, (37.0, 43.0))
    points, weights, normals = observations(radius=radius, slope=slope, **kwargs)
    fitted = fit(points, weights, normals, support=support)
    assert_truth(fitted, radius=radius, slope=slope)
    residual, gradient = residual_and_gradient(points, weights, fitted)
    assert np.max(np.abs(residual)) < 1e-9 and gradient < 1e-9
    assert_finite_sheet(points, fitted)
    if case == "varying-radius":
        assert_admission_evidence(points, weights, fitted)
    if support is not None:
        assert fitted["support_intent"] == support_record(support)
        origin, axis, r, k = geometry(fitted)
        intersection = origin - origin[2] / axis[2] * axis
        assert r + k * ((intersection - origin) @ axis) < 0


@pytest.mark.parametrize("height,slope", [(6e5, 1e-7), (3e15, 1e-16)])
def test_noisy_long_cone_reaches_radius_relative_stationarity(
    height: float, slope: float
) -> None:
    points, weights, normals = observations(height=height, slope=slope, noise=0.005)
    fitted = fit(points, weights, normals)
    assert_truth(fitted, slope=slope, extent=height, tolerance=0.005)
    residual, gradient = residual_and_gradient(points, weights, fitted)
    assert gradient < 1e-9
    assert fitted["solver"]["iterations"] > 0
    assert fitted["weighted_rms"] == pytest.approx(
        np.sqrt(np.average(residual**2, weights=weights)), abs=1e-12
    )
    assert 0.0034 < fitted["weighted_rms"] < 0.0037
    assert_finite_sheet(points, fitted)


@pytest.mark.parametrize("slope", [10.0, 100.0])
def test_steep_short_frustum_does_not_inherit_cylinder_tilt_scaling(
    slope: float,
) -> None:
    points, weights, normals = observations(height=0.003, slope=slope, full=True)
    fitted = fit(points, weights, normals)
    assert_truth(fitted, slope=slope, extent=0.003, tolerance=1e-7)
    residual, _gradient = residual_and_gradient(points, weights, fitted)
    assert np.max(np.abs(residual)) < 1e-9
    assert_finite_sheet(points, fitted)


@pytest.mark.parametrize(
    "family", ["strip", "ring", "two-generators", "plane", "noisy-plane"]
)
def test_bad_family_rank_and_wrong_local_solution_protections_remain(
    family: str,
) -> None:
    kwargs: dict[str, Any] = {}
    if family == "strip":
        kwargs["angles"] = np.linspace(-1e-7, 1e-7, 24)
    elif family == "ring":
        kwargs["levels"] = np.array([0.0])
    elif family == "two-generators":
        kwargs["angles"] = np.array([-0.5, 0.5])
    points, weights, normals = observations(**kwargs)
    if family in {"plane", "noisy-plane"}:
        x, z = np.meshgrid(np.linspace(-2, 2, 24), np.linspace(-3, 3, 7))
        points = ORIGIN + np.column_stack((x.ravel(), np.ones(x.size), z.ravel()))
        normals = np.tile([0.0, 1.0, 0.0], (len(points), 1))
        if family == "noisy-plane":
            points[:, 1] += 0.005 * np.sin(1.7 * np.arange(len(points)))
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit(points, weights, normals)
    assert failure.value.code in {
        "insufficient-coverage",
        "rank-deficient",
        "no-valid-candidates",
        "search-exhausted",
        "ambiguous",
    }
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "hint_kind", ["orthogonal", "distant", "huge", "stale", "rebased", "reversed"]
)
def test_long_cone_hints_cannot_reduce_or_perturb_fresh_recovery(
    hint_kind: str,
) -> None:
    points, weights, normals = observations(height=6e5, slope=1e-7)
    original = [v.copy() for v in (points, weights, normals)]
    baseline = fit(points, weights, normals)
    hint: dict[str, Any] = {
        "origin": ORIGIN.tolist(),
        "axis": AXIS.tolist(),
        "radius": 3.0,
        "slope": 1e-7,
    }
    if hint_kind == "orthogonal":
        hint["axis"] = [0.0, 1.0, 0.0]
    elif hint_kind == "distant":
        hint["origin"] = (ORIGIN + np.array([100, 100, 0])).tolist()
    elif hint_kind == "huge":
        hint["origin"] = [1e100, -1e100, 1e100]
    elif hint_kind in {"rebased", "reversed"}:
        hint["origin"], hint["radius"] = (ORIGIN - 30 * AXIS).tolist(), 3 - 30e-7
        if hint_kind == "reversed":
            hint["axis"], hint["slope"] = (-AXIS).tolist(), -1e-7
    fitted = fit(
        points,
        weights,
        normals,
        hint=hint,
        hint_binding_sha256="0" * 64 if hint_kind == "stale" else None,
    )
    assert_truth(fitted, slope=1e-7, extent=6e5)
    attempts = fitted["initialization"]["recovery"]["attempts"]
    assert [a for a in attempts if a["role"] == "fresh"] == baseline["initialization"][
        "recovery"
    ]["attempts"]
    assert attempts[-1]["role"] == "hint"
    assert len([a for a in attempts if a["role"] == "fresh"]) <= 7
    for value, before in zip((points, weights, normals), original, strict=True):
        np.testing.assert_array_equal(value, before)
    assert_finite_sheet(points, fitted)


def test_candidate_callbacks_remain_bounded_after_admission_change() -> None:
    points, weights, normals = observations(height=6e5, slope=1e-7, noise=0.005)
    policy = RecoveryPolicy(
        max_fresh_attempts=1, attempt=AttemptLimits(max_evaluations=1)
    )
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit(points, weights, normals, policy=policy)
    attempts = failure.value.diagnostic["recovery"]["attempts"]
    executed = [a for a in attempts if a["outcome"] != "fresh-attempt-limit"]
    assert len(executed) == 1
    assert all(a["evaluations"] <= 1 for a in attempts)
    assert sum(a["evaluations"] for a in attempts) <= 1
    assert all(
        a["evaluations"] == 0 for a in attempts if a["outcome"] == "fresh-attempt-limit"
    )
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_cancellation_is_checked_before_cone_admission_and_solver_work() -> None:
    points, weights, normals = observations(height=6e5, slope=1e-7)
    with pytest.raises(FitCancelled):
        _ = fit(points, weights, normals, cancelled=lambda: True)


def test_near_parallel_hard_plane_distinguishes_stationary_hint_endpoints() -> None:
    points, weights, normals = observations(slope=0.0, full=True)
    points -= ORIGIN
    normal = np.array([1.0, 0.0, 1e-7])
    normal /= np.linalg.norm(normal)
    support = PlaneSupport(tuple(normal), 0.0, (8.0, 15.0))
    baseline = fit(points, weights, normals, support=support)
    origin, axis, _radius, _slope = geometry(baseline)
    baseline_endpoints = (
        origin + np.asarray(baseline["analytic"]["axial_domain"])[:, None] * axis
    )
    np.testing.assert_allclose(baseline_endpoints, [[0, 0, 8], [0, 0, 15]], atol=3e-7)
    hint = {
        "origin": [3e-11, 0.0, 0.0],
        "axis": AXIS.tolist(),
        "radius": 3.0,
        "slope": 0.0,
    }
    hint_origin = np.asarray(hint["origin"])
    intersection = hint_origin - (normal @ hint_origin) / (normal @ AXIS) * AXIS
    hint_endpoints = intersection + np.array([8.0, 15.0])[:, None] * AXIS
    assert np.max(np.linalg.norm(hint_endpoints - baseline_endpoints, axis=1)) > 2.9e-4
    _residual, gradient = residual_and_gradient(points, weights, {"analytic": hint})
    assert gradient < 1e-10
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit(points, weights, normals, support=support, hint=hint)
    assert failure.value.code == "ambiguous"
    attempts = failure.value.diagnostic["recovery"]["attempts"]
    assert [a for a in attempts if a["role"] == "fresh"] == baseline["initialization"][
        "recovery"
    ]["attempts"]
    assert attempts[-1]["role"] == "hint"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)
