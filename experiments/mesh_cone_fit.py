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
from scansor.nonlinear_least_squares import (
    LeastSquaresResult,
    solve_least_squares,
)

CONE_POLICY_VERSION = "selected-cone-recovery-v1"


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


def fit_selected_cone(
    points: Array,
    weights: Array,
    normals: Array,
    binding: InitializationBinding,
    *,
    hint: dict[str, Any] | None = None,
    hint_binding_sha256: str | None = None,
    domain: tuple[float, float] | None = None,
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
        "normalized_covariance_eigenvalues": np.maximum(values, 0).tolist(),
        "centered_normal_covariance_eigenvalues": np.maximum(normal_values, 0).tolist(),
        "minimum_coverage_ratio": 1e-10,
        "maximum_aligned_jacobian_condition": 1e6,
    }
    if coverage <= 1e-10:
        raise ConeFitFailure(
            "insufficient-coverage",
            "select a wider curved patch with axial coverage",
            initialization,
        )
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

    def support(anchor: Array, direction: Array) -> list[float] | None:
        if domain is None:
            heights = (q - anchor) @ direction
            return [float(np.min(heights)), float(np.max(heights))]
        if abs(float(direction[2])) <= 1e-8:
            return None
        world_anchor = frame.origin + frame.length * anchor
        shift = float(world_anchor[2] / direction[2])
        return [(domain[0] - shift) / frame.length, (domain[1] - shift) / frame.length]

    def valid(parameters: Array, rotation: Array) -> bool:
        if not np.isfinite(parameters).all():
            return False
        anchor, direction, radius, taper = geometry(parameters, rotation)
        bounds = support(anchor, direction)
        if bounds is None or not np.isfinite(bounds).all() or bounds[0] >= bounds[1]:
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

        def evaluate(parameters: Array) -> tuple[Array, Array]:
            residual, jac = cone_residual_jacobian(local, parameters)
            return np.sqrt(w) * residual, np.sqrt(w)[:, None] * jac

        numerical = solve_least_squares(
            candidate.parameters,
            lambda p: control.call(evaluate, p),
            parameter_scales=np.ones(6),
            residual_scale=1.0,
            geometry_is_valid=lambda p: valid(p, rotation),
            max_iterations=control.limits.max_iterations,
            max_trials=control.limits.max_trials,
        )
        return _SelectedCone(numerical, rotation, candidate.id)

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
        residual, jac, _ = aligned(result)
        singular = np.linalg.svd(np.sqrt(w)[:, None] * jac, compute_uv=False)
        condition = (
            float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
        )
        gradient = float(np.max(np.abs(jac.T @ (w * residual))))
        assessments[result.candidate_id] = {
            "aligned_gradient_infinity_norm": gradient
            if np.isfinite(gradient)
            else None
        }
        return FitAssessment(
            float(w @ residual**2),
            valid(np.asarray(result.numerical.parameters), result.basis)
            and condition <= 1e6
            and gradient <= 1e-10,
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
        scale = min(abs(ra), abs(rb))
        return bool(
            np.linalg.norm(aa - ab) <= 1e-7 * scale
            and abs(ra - rb) <= 1e-7 * scale
            and np.linalg.norm(np.cross(da, db)) <= 1e-7
            and abs(ka - kb) <= 1e-7
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
    bounds = support(anchor, direction)
    assert bounds is not None
    world_anchor = frame.origin + frame.length * anchor
    relative = q - anchor
    heights = relative @ direction
    radial = relative - heights[:, None] * direction
    rho = np.linalg.norm(radial, axis=1)
    expected = (radial / rho[:, None] - taper * direction) / np.hypot(1, taper)
    scale = float(np.hypot(1, taper))
    projected = (heights / scale + (taper / scale) * (rho - radius)) / scale
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
        "support_origin": "selection" if domain is None else "legacy-source-z0",
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
