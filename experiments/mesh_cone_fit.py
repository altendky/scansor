"""Bounded selected-data finite-cone fitting, independent of example fixtures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import (
    FitAssessment,
    FitCandidate,
    InitializationBinding,
    RecoveryFailure,
    RecoveryProblem,
    recover_fit,
)
from experiments.fit_solver import AttemptControl, normalized_weights
from experiments.mesh_cylinder_fit import Array, cylinder_basis
from experiments.support_intent import (
    SelectionSupport,
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    resolve_support,
    support_record,
)
from experiments.surface_primitives import projected_axial_parameter
from scansor.nonlinear_least_squares import (
    LeastSquaresFailure,
    LeastSquaresResult,
    solve_least_squares,
)

CONE_POLICY_VERSION = "selected-cone-recovery-v2"


class ConeFitFailure(ValueError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "cone_fit_failure",
            "code": code,
            **(details or {}),
        }
        super().__init__(message)


def cone_residual_jacobian(points: Array, parameters: Array) -> tuple[Array, Array]:
    """[cx,cy,a,b,r,k], signed orthogonal distance to the positive lateral sheet."""
    cx, cy, a, b, radius, taper = parameters
    raw = np.array([a, b, 1.0])
    length = float(np.linalg.norm(raw))
    direction = raw / length
    relative = points - np.array([cx, cy, 0.0])
    axial = relative @ direction
    radial = relative - axial[:, None] * direction
    rho = np.linalg.norm(radial, axis=1)
    if np.any(rho <= 0):
        raise ValueError("cone distance derivative is undefined on its axis")
    unit = radial / rho[:, None]
    da = (np.array([1.0, 0, 0]) - direction * direction[0]) / length
    db = (np.array([0.0, 1, 0]) - direction * direction[1]) / length
    scale = float(np.hypot(1.0, taper))
    residual = (rho - radius - taper * axial) / scale
    jacobian = np.column_stack(
        (
            (-unit[:, 0] + taper * direction[0]) / scale,
            (-unit[:, 1] + taper * direction[1]) / scale,
            (-axial * (unit @ da) - taper * (relative @ da)) / scale,
            (-axial * (unit @ db) - taper * (relative @ db)) / scale,
            -np.ones(len(points)) / scale,
            -axial / scale - residual * (taper / scale) / scale,
        )
    )
    return residual, jacobian


@dataclass(frozen=True)
class _SelectedCone:
    numerical: LeastSquaresResult
    basis: Array
    candidate_id: str
    parameter_scales: Array
    residual_scale: float


def _weighted_rms(values: Array, weights: Array) -> float:
    maximum = float(np.max(np.abs(values)))
    if maximum == 0 or not np.isfinite(maximum):
        return maximum
    return maximum * float(np.sqrt(weights @ (values / maximum) ** 2))


def _cone_metrics(
    points: Array,
    weights: Array,
    anchor: Array,
    axis: Array,
    radius: float,
    taper: float,
) -> dict[str, Any]:
    """Affine positional coverage in fitted positive-radius/axial units.

    One global transverse scale preserves positional rank. Dividing each row by
    its own radius would manufacture apparent curved coverage from a plane.
    The tilt scale follows the actual cone derivative h*u+k*xy, not just height.
    """
    relative = points - anchor
    heights = relative @ axis
    transverse = relative - heights[:, None] * axis
    rho = np.linalg.norm(transverse, axis=1)
    factor = float(np.hypot(1, taper))
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        projected = (
            radius / factor / factor
            + (taper / factor) * (heights / factor)
            + (taper / factor) ** 2 * rho
        )
        axial_rms = _weighted_rms(heights - weights @ heights, weights)
        radial_rms = _weighted_rms(projected, weights)
        normal_tilt_rms = _weighted_rms(
            heights / factor + (taper / factor) * rho, weights
        )
    metrics: dict[str, Any] = {
        "axial_rms": axial_rms if np.isfinite(axial_rms) else None,
        "projected_radius_rms": radial_rms if np.isfinite(radial_rms) else None,
        "normal_tilt_rms": normal_tilt_rms if np.isfinite(normal_tilt_rms) else None,
        "coverage_ratio": 0.0,
        "covariance_eigenvalues": None,
    }
    if (
        not np.isfinite([axial_rms, radial_rms]).all()
        or min(axial_rms, radial_rms) <= 0
        or not np.isfinite(projected).all()
        or np.any(projected <= 0)
    ):
        return metrics
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        balanced = (points - weights @ points) @ cylinder_basis(axis)
        balanced /= np.array([radial_rms, radial_rms, axial_rms])
        covariance = balanced.T @ (weights[:, None] * balanced)
    if not np.isfinite(covariance).all():
        return metrics
    spectrum = np.maximum(np.linalg.eigvalsh(covariance), 0)
    metrics["covariance_eigenvalues"] = spectrum.tolist()
    metrics["coverage_ratio"] = (
        float(spectrum[0] / spectrum[-1]) if spectrum[-1] > 0 else 0.0
    )
    return metrics


def _cone_scales(metrics: dict[str, Any], taper: float) -> tuple[Array, float]:
    radius, axial, tilt = (
        metrics["projected_radius_rms"],
        metrics["axial_rms"],
        metrics["normal_tilt_rms"],
    )
    if any(value is None or value <= 0 for value in (radius, axial, tilt)):
        raise LeastSquaresFailure("rank-deficient", "unresolved cone geometric scale")
    residual_scale = radius / np.hypot(1, taper)
    scales = np.array(
        [
            radius,
            radius,
            residual_scale / tilt,
            residual_scale / tilt,
            radius,
            radius / axial,
        ]
    )
    if (
        not np.isfinite(scales).all()
        or np.any(scales <= 0)
        or not np.isfinite(residual_scale)
        or residual_scale <= 0
    ):
        raise LeastSquaresFailure("invalid-input", "unrepresentable cone solve scales")
    return scales, float(residual_scale)


def fit_selected_cone(
    points: Array,
    weights: Array,
    normals: Array,
    binding: InitializationBinding,
    *,
    hint: dict[str, Any] | None = None,
    hint_binding_sha256: str | None = None,
    domain: tuple[float, float] | None = None,
    support: SupportIntent | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Fit every selected row; normal directions propose starts, never constraints."""
    guard = AttemptControl(binding.policy.attempt, cancelled)
    guard.checkpoint()
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 7:
        raise ConeFitFailure(
            "insufficient-observations", "select at least seven XYZ observations"
        )
    if (
        weights.shape != (len(points),)
        or normals.shape != points.shape
        or not all(np.isfinite(v).all() for v in (points, weights, normals))
        or np.any(weights <= 0)
    ):
        raise ConeFitFailure(
            "invalid-input", "expected finite positive-area observations and normals"
        )
    normal_lengths = np.linalg.norm(normals, axis=1)
    if not np.isfinite(normal_lengths).all() or np.any(normal_lengths <= 0):
        raise ConeFitFailure("invalid-normals", "selected normals must be usable")
    if domain is not None and (
        len(domain) != 2 or not np.isfinite(domain).all() or domain[0] >= domain[1]
    ):
        raise ConeFitFailure(
            "invalid-support", "explicit axial bounds must be finite and ordered"
        )
    intent = legacy_support(domain) if support is None else support
    _ = support_record(intent)
    if domain is not None and intent != legacy_support(domain):
        raise SupportIntentFailure(
            "conflicting explicit support declarations", code="invalid-support"
        )
    try:
        w = normalized_weights(weights)
        frame = CoordinateFrame.from_observations(points, weights)
    except ValueError as error:
        raise ConeFitFailure("invalid-input", str(error)) from error
    q = (points - frame.origin) / frame.length
    unit_normals = normals / normal_lengths[:, None]
    values, position_vectors = np.linalg.eigh(q.T @ (w[:, None] * q))
    centered_normals = unit_normals - w @ unit_normals
    normal_values, normal_vectors = np.linalg.eigh(
        centered_normals.T @ (w[:, None] * centered_normals)
    )
    _, raw_vectors = np.linalg.eigh(unit_normals.T @ (w[:, None] * unit_normals))
    coverage = float(max(values[0], 0) / values[-1])
    initialization: dict[str, Any] = {
        "algorithm": CONE_POLICY_VERSION,
        "chart": "rotated-normalized-cone-v1",
        "origin": frame.origin.tolist(),
        "length": frame.length,
        "observations": len(points),
        "coverage_ratio": coverage,
        "raw_coverage_ratio": coverage,
        "raw_coverage_gate_applied": False,
        "coverage_measure": "affine-projected-positive-radius-and-axial-rms",
        "normalized_covariance_eigenvalues": np.maximum(values, 0).tolist(),
        "centered_normal_covariance_eigenvalues": np.maximum(normal_values, 0).tolist(),
        "minimum_coverage_ratio": 1e-10,
        "maximum_aligned_jacobian_condition": 1e6,
    }
    bases: dict[str, Array] = {}
    directions: list[Array] = []
    fresh: list[FitCandidate] = []
    initializers: dict[str, Any] = {}
    assessments: dict[str, Any] = {}
    initialization["initializers"] = initializers
    initialization["assessments"] = assessments
    for family, vectors in (
        ("centered-normal", normal_vectors[:, :1]),
        ("normal-moment", raw_vectors),
        ("position-covariance", position_vectors),
    ):
        for index in range(vectors.shape[1]):
            guard.checkpoint()
            direction = vectors[:, index].copy()
            if direction[int(np.argmax(np.abs(direction)))] < 0:
                direction = -direction
            if any(abs(float(direction @ old)) > 1 - 1e-10 for old in directions):
                continue
            directions.append(direction)
            chart = f"{family}-{index}"
            rotation = cylinder_basis(direction)
            bases[chart] = rotation
            local = q @ rotation
            xy, z = local[:, :2], local[:, 2]
            # Remove the polynomial nuisance range. Two axial levels can still
            # identify the center even when [1,z,z²] has only rank two.
            nuisance = np.column_stack((np.ones(len(q)), z, z * z))
            target = np.sum(xy**2, axis=1)
            sw = np.sqrt(w)
            nuisance *= sw[:, None]
            design = 2 * xy * sw[:, None]
            target *= sw
            design -= nuisance @ np.linalg.lstsq(nuisance, design, rcond=None)[0]
            target -= nuisance @ np.linalg.lstsq(nuisance, target, rcond=None)[0]
            center, _, rank, singular = np.linalg.lstsq(design, target, rcond=None)
            radial = np.linalg.norm(xy - center, axis=1)
            radial_design = sw[:, None] * np.column_stack((np.ones(len(q)), z))
            rk, _, radial_rank, _ = np.linalg.lstsq(
                radial_design, sw * radial, rcond=None
            )
            initializers[chart] = {
                "center_rank": int(rank),
                "radial_rank": int(radial_rank),
                "center_condition": float(singular[0] / singular[-1])
                if rank == 2 and singular[-1] > 0
                else None,
            }
            parameters = (
                tuple(float(v) for v in [*center, 0.0, 0.0, *rk])
                if rank == 2 and radial_rank == 2
                else ()
            )
            fresh.append(
                FitCandidate(
                    chart,
                    parameters,
                    f"selected {family} axis; residualized center and radial regression",
                    chart,
                    binding.sha256,
                )
            )

    def geometry(
        parameters: Array, rotation: Array
    ) -> tuple[Array, Array, float, float]:
        direction = rotation @ np.r_[parameters[2:4], 1.0]
        direction /= np.linalg.norm(direction)
        center = rotation @ np.r_[parameters[:2], 0.0]
        shift = -float(center @ direction)
        anchor = center + shift * direction
        radius = float(parameters[4] + parameters[5] * shift)
        taper = float(parameters[5])
        if (
            direction[2] < 0
            if domain is not None
            else direction[int(np.argmax(np.abs(direction)))] < 0
        ):
            direction, taper = -direction, -taper
        return anchor, direction, radius, taper

    def supported_geometry(
        anchor: Array, direction: Array, radius: float, taper: float
    ) -> tuple[Array, Array, float, float, list[float]]:
        resolved = resolve_support(
            intent,
            frame.origin + frame.length * anchor,
            direction,
            points,
            reference=frame.origin,
        )
        return (
            (np.asarray(resolved.origin) - frame.origin) / frame.length,
            np.asarray(resolved.axis),
            radius + taper * resolved.origin_shift / frame.length,
            taper * resolved.axis_sign,
            (np.asarray(resolved.domain) / frame.length).tolist(),
        )

    def valid(parameters: Array, rotation: Array) -> bool:
        if not np.isfinite(parameters).all():
            return False
        try:
            anchor, direction, radius, taper, bounds = supported_geometry(
                *geometry(parameters, rotation)
            )
        except SupportIntentFailure:
            return False
        with np.errstate(over="ignore", invalid="ignore"):
            ends = radius + taper * np.asarray(bounds)
            relative = q - anchor
            heights = relative @ direction
            radial = relative - heights[:, None] * direction
            rho = np.linalg.norm(radial, axis=1)
            scale = float(np.hypot(1, taper))
            projected_radius = (
                radius / scale / scale
                + (taper / scale) * (heights / scale)
                + (taper / scale) ** 2 * rho
            )
        return bool(
            np.isfinite(ends).all()
            and np.all(ends > 0)
            and np.all(rho > 0)
            and np.isfinite(projected_radius).all()
            and np.all(projected_radius > 0)
        )

    def solve(candidate: FitCandidate, control: AttemptControl) -> _SelectedCone:
        rotation = bases[candidate.chart]
        local = q @ rotation
        parameters = np.asarray(candidate.parameters)
        if not valid(parameters, rotation):
            raise LeastSquaresFailure(
                "invalid-initial-geometry", "cone proposal has invalid positive support"
            )
        anchor, axis, radius, taper = geometry(parameters, rotation)
        scales, residual_scale = _cone_scales(
            _cone_metrics(q, w, anchor, axis, radius, taper), taper
        )

        def evaluate(parameters: Array) -> tuple[Array, Array]:
            residual, jac = cone_residual_jacobian(local, parameters)
            return np.sqrt(w) * residual, np.sqrt(w)[:, None] * jac

        numerical = solve_least_squares(
            candidate.parameters,
            lambda p: control.call(evaluate, p),
            parameter_scales=scales,
            residual_scale=residual_scale,
            # In one of the three orthonormal normal-moment charts every
            # unsigned direction has both slopes <=sqrt(2). The overlapping
            # boxes retain that coverage and avoid the wrong-chart horizon.
            lower_bounds=[-np.inf, -np.inf, -2.0, -2.0, -np.inf, -np.inf],
            upper_bounds=[np.inf, np.inf, 2.0, 2.0, np.inf, np.inf],
            rank_tolerance=1e-6,
            geometry_is_valid=lambda p: valid(p, rotation),
            max_iterations=control.limits.max_iterations,
            max_trials=control.limits.max_trials,
        )
        return _SelectedCone(numerical, rotation, candidate.id, scales, residual_scale)

    def aligned(
        result: _SelectedCone,
    ) -> tuple[Array, Array, tuple[Array, Array, float, float]]:
        geometry_value = geometry(np.asarray(result.numerical.parameters), result.basis)
        anchor, direction, radius, taper = geometry_value
        residual, jac = cone_residual_jacobian(
            (q - anchor) @ cylinder_basis(direction),
            np.array([0.0, 0, 0, 0, radius, taper]),
        )
        return residual, jac, geometry_value

    def assess(result: _SelectedCone) -> FitAssessment:
        residual, jac, (anchor, axis, radius, taper) = aligned(result)
        singular = np.linalg.svd(np.sqrt(w)[:, None] * jac, compute_uv=False)
        condition = (
            float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
        )
        gradient = float(np.max(np.abs(jac.T @ (w * residual))))
        metrics = _cone_metrics(q, w, anchor, axis, radius, taper)
        try:
            scales, residual_scale = _cone_scales(metrics, taper)
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                scaled_gradient = float(
                    np.max(
                        np.abs(
                            (jac * (scales / residual_scale)).T
                            @ (w * (residual / residual_scale))
                        )
                    )
                )
        except LeastSquaresFailure:
            scaled_gradient = float("inf")
        metrics["aligned_gradient_infinity_norm"] = (
            gradient if np.isfinite(gradient) else None
        )
        metrics["aligned_scaled_gradient_infinity_norm"] = (
            scaled_gradient if np.isfinite(scaled_gradient) else None
        )
        assessments[result.candidate_id] = metrics
        return FitAssessment(
            float(w @ residual**2),
            valid(np.asarray(result.numerical.parameters), result.basis)
            and condition <= 1e6
            and metrics["coverage_ratio"] > 1e-10
            and np.isfinite(scaled_gradient)
            and scaled_gradient <= 1e-10,
            int(np.sum(singular > 1e-12 * singular[0])),
            condition,
            result.numerical.termination,
            result.numerical.iterations,
        )

    def equivalent(a: _SelectedCone, b: _SelectedCone) -> bool:
        aa, da, ra, ka = geometry(np.asarray(a.numerical.parameters), a.basis)
        ab, db, rb, kb = geometry(np.asarray(b.numerical.parameters), b.basis)
        if da @ db < 0:
            db, kb = -db, -kb
        scale = min(
            _cone_metrics(q, w, aa, da, ra, ka)["projected_radius_rms"],
            _cone_metrics(q, w, ab, db, rb, kb)["projected_radius_rms"],
        )
        extent = 0.0
        segments: list[tuple[Array, Array, Array]] = []
        for anchor, axis, radius, taper in ((aa, da, ra, ka), (ab, db, rb, kb)):
            (
                supported_anchor,
                supported_axis,
                supported_radius,
                supported_taper,
                bounds,
            ) = supported_geometry(anchor, axis, radius, taper)
            endpoints = supported_anchor + np.asarray(bounds)[:, None] * supported_axis
            endpoint_radii = supported_radius + supported_taper * np.asarray(bounds)
            segments.append((endpoints, endpoint_radii, supported_axis))
            extent = max(
                extent,
                float(np.max(np.abs((q - anchor) @ axis))),
                float(np.max(np.abs((endpoints - anchor) @ axis))),
            )
        # A minimal rotation moves a meridian point by at most its distance
        # from the common gauge times ||d1-d2||. Convexity puts the largest
        # distance at an axial interval endpoint, including varying radius.
        angular_lever = max(
            float(np.hypot(extent, abs(radius) + abs(taper) * extent))
            for radius, taper in ((ra, ka), (rb, kb))
        )
        ends_a, radii_a, axis_a = segments[0]
        ends_b, radii_b, axis_b = segments[1]
        if isinstance(intent, SelectionSupport) and axis_a @ axis_b < 0:
            ends_b, radii_b = ends_b[::-1], radii_b[::-1]
        tolerance = 1e-7 * scale
        return bool(
            np.isfinite([scale, extent, angular_lever]).all()
            and scale > 0
            and np.linalg.norm(aa - ab) <= tolerance
            and abs(ra - rb) <= tolerance
            and np.linalg.norm(da - db) * angular_lever <= tolerance
            and abs(ka - kb) * extent <= tolerance
            # Near-parallel plane intersection can amplify a tiny transverse
            # line shift even with exactly equal directions and radius.
            and np.max(np.linalg.norm(ends_a - ends_b, axis=1)) <= tolerance
            and np.max(np.abs(radii_a - radii_b)) <= tolerance
        )

    proposed_hint = None
    if hint is not None:
        chart, parameters = "invalid-cone-hint", ()
        try:
            raw = hint.get("analytic", hint)
            anchor, direction = (
                np.asarray(raw["origin"], dtype=float),
                np.asarray(raw["axis"], dtype=float),
            )
            radius, taper = float(raw["radius"]), float(raw["slope"])
            if (
                anchor.shape == (3,)
                and direction.shape == (3,)
                and np.isfinite(anchor).all()
                and np.isfinite(direction).all()
                and np.linalg.norm(direction) > 0
                and np.isfinite([radius, taper]).all()
            ):
                rotation = cylinder_basis(direction)
                normalized_anchor = ((anchor - frame.origin) / frame.length) @ rotation
                with np.errstate(over="ignore", invalid="ignore"):
                    translated = q - (anchor - frame.origin) / frame.length
                    local_radius = radius / frame.length - taper * normalized_anchor[2]
                if np.isfinite(translated).all() and not np.all(
                    translated == translated[0]
                ):
                    chart = "optional-cone-hint"
                    bases[chart] = rotation
                    parameters = (
                        float(normalized_anchor[0]),
                        float(normalized_anchor[1]),
                        0.0,
                        0.0,
                        float(local_radius),
                        taper,
                    )
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        proposed_hint = FitCandidate(
            "optional-cone-hint",
            parameters,
            "optional disposable cone geometry",
            chart,
            hint_binding_sha256 or binding.sha256,
        )
    try:
        recovered = recover_fit(
            RecoveryProblem(
                binding, {key: 6 for key in bases}, solve, assess, equivalent
            ),
            fresh,
            hint=proposed_hint,
            cancelled=cancelled,
        )
    except RecoveryFailure as error:
        raise ConeFitFailure(
            error.code,
            str(error),
            {**initialization, "recovery": error.diagnostics.json()},
        ) from error
    result = recovered.result
    residual, _jac, (anchor, direction, radius, taper) = aligned(result)
    anchor, direction, radius, taper, bounds = supported_geometry(
        anchor, direction, radius, taper
    )
    world_anchor = frame.origin + frame.length * anchor
    relative = q - anchor
    heights = relative @ direction
    radial = relative - heights[:, None] * direction
    rho = np.linalg.norm(radial, axis=1)
    expected = (radial / rho[:, None] - taper * direction) / np.hypot(1, taper)
    projected = projected_axial_parameter(heights, rho, radius, taper)
    inside = (projected >= bounds[0]) & (projected <= bounds[1])
    assessment = assess(result)
    numerical = result.numerical
    initialization["recovery"] = recovered.diagnostics.json()
    output: dict[str, Any] = {
        "kind": "cone",
        "analytic": {
            "kind": "cone",
            "origin": world_anchor.tolist(),
            "axis": direction.tolist(),
            "radius": frame.length * radius,
            "slope": taper,
            "axial_domain": (frame.length * np.asarray(bounds)).tolist(),
        },
        "anchor_reference": frame.origin.tolist(),
        "support_origin": "legacy-source-z0"
        if domain is not None
        else support_record(intent)["kind"],
        "support_intent": support_record(intent),
        "residuals": (frame.length * residual).tolist(),
        "weighted_rms": frame.length * float(np.sqrt(w @ residual**2)),
        "condition": float(assessment.condition or 1) ** 2,
        "normal_sign": 1.0
        if float(w @ np.sum(expected * unit_normals, axis=1)) >= 0
        else -1.0,
        "solver": {
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": assessment.rank,
            "scaled_jacobian_condition": numerical.condition,
            "aligned_jacobian_condition": assessment.condition,
            "aligned_scaled_gradient_infinity_norm": assessments[result.candidate_id][
                "aligned_scaled_gradient_infinity_norm"
            ],
            "parameter_scales": result.parameter_scales.tolist(),
            "residual_scale": result.residual_scale,
            "scale_units": "dimensionless rotated (source-origin)/coordinate_length chart",
            "scaled_projected_gradient_infinity_norm": numerical.projected_gradient_norm,
            "coordinate_origin": frame.origin.tolist(),
            "coordinate_length": frame.length,
        },
        "initialization": initialization,
        "support_classification": {
            "inside_vertices": int(np.sum(inside)),
            "outside_vertices": int(np.sum(~inside)),
            "role": "selection-and-display",
        },
    }
    if domain is not None:
        output["source_axial_domain"] = list(domain)
    return output
