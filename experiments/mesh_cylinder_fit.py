"""Exploratory area-weighted cylinder fitting, separate from fitting admission."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import AxisChart, CoordinateFrame, FitCoordinates
from experiments.fit_initialization import (
    FitAssessment,
    FitCandidate,
    InitializationBinding,
    RecoveryFailure,
    RecoveryProblem,
    recover_fit,
)
from experiments.fit_solver import (
    AttemptControl,
    SolverDiagnostics,
    normalized_weights,
    solve_geometric_fit,
)
from experiments.support_intent import (
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    resolve_support,
    support_record,
)
from scansor.nonlinear_least_squares import LeastSquaresResult, solve_least_squares

Array = NDArray[np.float64]


class CylinderFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool
    solver: NotRequired[SolverDiagnostics]


def valid_cylinder_geometry(points: Array, parameters: Array) -> bool:
    if not np.isfinite(parameters).all() or parameters[4] <= 0:
        return False
    raw = np.array([parameters[2], parameters[3], 1.0])
    axis = raw / np.linalg.norm(raw)
    q = points - np.array([parameters[0], parameters[1], 0.0])
    radial = q - (q @ axis)[:, None] * axis
    return bool(np.all(np.linalg.norm(radial, axis=1) > 0))


def residual_jacobian(points: Array, parameters: Array) -> tuple[Array, Array]:
    """Local axis (a,b,1); center (cx,cy,0) fixes axial translation freedom."""
    cx, cy, a, b, radius = parameters
    raw = np.array([a, b, 1.0])
    length = float(np.linalg.norm(raw))
    axis = raw / length
    q = points - np.array([cx, cy, 0.0])
    axial = q @ axis
    radial = q - axial[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    if np.any(rho <= 0):
        raise ValueError("cylinder distance derivative is undefined on its axis")
    unit = radial / rho[:, None]
    da = (np.array([1.0, 0, 0]) - axis * axis[0]) / length
    db = (np.array([0.0, 1, 0]) - axis * axis[1]) / length
    jacobian = np.column_stack(
        (
            -unit[:, 0],
            -unit[:, 1],
            -axial * (unit @ da),
            -axial * (unit @ db),
            -np.ones(len(points)),
        )
    )
    return rho - radius, jacobian


def fit_cylinder(points: Array, weights: Array, initial: Array) -> CylinderFitResult:
    """All selected rows, ordinary weighted least squares, no residual trimming."""
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 6:
        raise ValueError("expected at least six XYZ rows")
    if weights.shape != (len(points),) or initial.shape != (5,):
        raise ValueError("weight or parameter shape mismatch")
    if not all(np.isfinite(x).all() for x in (points, weights, initial)):
        raise ValueError("nonfinite fit input")
    if np.any(weights <= 0) or initial[4] <= 0:
        raise ValueError("weights and initial radius must be positive")
    w = normalized_weights(weights)
    coordinates = FitCoordinates(
        CoordinateFrame.from_observations(points, weights),
        5,
        axes=(AxisChart(0, 1, 2, 3, 4),),
    )
    fit = solve_geometric_fit(
        initial,
        weights,
        coordinates,
        lambda p: residual_jacobian(points, p),
        lambda p: valid_cylinder_geometry(points, p),
    )
    residual = fit.residual
    return {
        "parameters": fit.parameters.tolist(),
        "objective_history": fit.objective_history,
        "weighted_rms": float(np.sqrt(w @ (residual * residual))),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": fit.condition,
        "gradient_infinity_norm": fit.gradient,
        "converged": True,
        "solver": fit.solver,
    }


class CylinderFitFailure(ValueError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "cylinder_fit_failure",
            "code": code,
            **(details or {}),
        }
        super().__init__(message)


def cylinder_basis(axis: Array) -> Array:
    """Orthonormal columns, with the last column following the supplied axis."""
    direction = axis / np.linalg.norm(axis)
    first = np.cross(direction, np.eye(3)[int(np.argmin(np.abs(direction)))])
    first /= np.linalg.norm(first)
    return np.asarray(
        np.column_stack((first, np.cross(direction, first), direction)),
        dtype=np.float64,
    )


@dataclass(frozen=True)
class _SelectedCylinder:
    numerical: LeastSquaresResult
    basis: Array


def fit_selected_cylinder(
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
    """Bounded positional cylinder fitting; normals propose starts only.

    Each proposal owns a rotated, centered, length-scaled five-variable chart.
    All attempts retain the same observations and objective. Computed starts are
    disposable, whereas an explicit legacy domain retains its source-Z0 anchor.
    """
    guard = AttemptControl(binding.policy.attempt, cancelled)
    guard.checkpoint()
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 6:
        raise CylinderFitFailure(
            "insufficient-observations", "select at least six XYZ observations"
        )
    if (
        weights.shape != (len(points),)
        or normals.shape != points.shape
        or not all(np.isfinite(a).all() for a in (points, weights, normals))
        or np.any(weights <= 0)
    ):
        raise CylinderFitFailure(
            "invalid-input", "expected finite positive-area observations and normals"
        )
    normal_lengths = np.linalg.norm(normals, axis=1)
    if not np.isfinite(normal_lengths).all() or np.any(normal_lengths <= 0):
        raise CylinderFitFailure("invalid-normals", "selected normals must be usable")
    if domain is not None and (
        len(domain) != 2 or not np.isfinite(domain).all() or domain[0] >= domain[1]
    ):
        raise CylinderFitFailure(
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
        raise CylinderFitFailure("invalid-input", str(error)) from error
    q = (points - frame.origin) / frame.length
    unit_normals = normals / normal_lengths[:, None]
    values, position_vectors = np.linalg.eigh(q.T @ (w[:, None] * q))
    coverage = float(max(values[0], 0) / values[-1])
    initialization: dict[str, Any] = {
        "algorithm": "selected-cylinder-recovery-v1",
        "chart": "rotated-normalized-cylinder-v1",
        "origin": frame.origin.tolist(),
        "length": frame.length,
        "observations": len(points),
        "coverage_ratio": coverage,
        "normalized_covariance_eigenvalues": np.maximum(values, 0).tolist(),
        "minimum_coverage_ratio": 1e-10,
        "maximum_aligned_jacobian_condition": 1e6,
    }
    if coverage <= 1e-10:
        raise CylinderFitFailure(
            "insufficient-coverage",
            "select a wider curved patch with axial coverage",
            initialization,
        )
    _, normal_vectors = np.linalg.eigh(unit_normals.T @ (w[:, None] * unit_normals))
    bases: dict[str, Array] = {}
    fresh: list[FitCandidate] = []
    directions: list[Array] = []
    for family, vectors in (
        ("normal-moment", normal_vectors),
        ("position-covariance", position_vectors),
    ):
        for index in range(3):
            guard.checkpoint()
            direction = vectors[:, index].copy()
            if direction[int(np.argmax(np.abs(direction)))] < 0:
                direction = -direction
            if any(abs(float(direction @ old)) > 1 - 1e-10 for old in directions):
                continue
            directions.append(direction)
            chart = f"{family}-{index}"
            rotation = cylinder_basis(direction)
            local = q @ rotation
            design = np.column_stack((2 * local[:, :2], np.ones(len(q))))
            algebraic, *_ = np.linalg.lstsq(
                np.sqrt(w)[:, None] * design,
                np.sqrt(w) * np.sum(local[:, :2] ** 2, axis=1),
                rcond=None,
            )
            center = algebraic[:2]
            radius = float(w @ np.linalg.norm(local[:, :2] - center, axis=1))
            bases[chart] = rotation
            fresh.append(
                FitCandidate(
                    chart,
                    (float(center[0]), float(center[1]), 0.0, 0.0, radius),
                    f"selected {family} eigenvector {index}; weighted projected circle",
                    chart,
                    binding.sha256,
                )
            )

    def geometry(result: _SelectedCylinder) -> tuple[Array, Array, float]:
        p = np.asarray(result.numerical.parameters)
        direction = result.basis @ np.r_[p[2:4], 1.0]
        direction /= np.linalg.norm(direction)
        anchor = result.basis @ np.r_[p[:2], 0.0]
        anchor -= float(anchor @ direction) * direction
        if domain is not None:
            if direction[2] < 0:
                direction = -direction
        elif direction[int(np.argmax(np.abs(direction)))] < 0:
            direction = -direction
        return anchor, direction, float(p[4])

    def aligned(result: _SelectedCylinder) -> tuple[Array, Array]:
        anchor, axis, radius = geometry(result)
        rotation = cylinder_basis(axis)
        residual, jac = residual_jacobian(
            (q - anchor) @ rotation, np.array([0.0, 0, 0, 0, radius])
        )
        return residual, jac

    def solve(candidate: FitCandidate, attempt: AttemptControl) -> _SelectedCylinder:
        rotation = bases[candidate.chart]
        local = q @ rotation

        def evaluate(p: Array) -> tuple[Array, Array]:
            residual, jac = residual_jacobian(local, p)
            return np.sqrt(w) * residual, np.sqrt(w)[:, None] * jac

        numerical = solve_least_squares(
            candidate.parameters,
            lambda p: attempt.call(evaluate, p),
            parameter_scales=np.ones(5),
            residual_scale=1.0,
            geometry_is_valid=lambda p: valid_cylinder_geometry(local, p),
            max_iterations=attempt.limits.max_iterations,
            max_trials=attempt.limits.max_trials,
        )
        return _SelectedCylinder(numerical, rotation)

    def assess(result: _SelectedCylinder) -> FitAssessment:
        residual, jac = aligned(result)
        singular = np.linalg.svd(np.sqrt(w)[:, None] * jac, compute_uv=False)
        condition = (
            float(singular[0] / singular[-1]) if singular[-1] > 0 else float("inf")
        )
        anchor, axis, radius = geometry(result)
        valid = bool(np.isfinite(anchor).all() and radius > 0 and condition <= 1e6)
        try:
            _ = resolve_support(
                intent,
                frame.origin + frame.length * anchor,
                axis,
                points,
                reference=frame.origin,
            )
        except SupportIntentFailure:
            valid = False
        return FitAssessment(
            float(w @ residual**2),
            valid,
            int(np.sum(singular > 1e-12 * singular[0])),
            condition,
            result.numerical.termination,
            result.numerical.iterations,
        )

    def equivalent(a: _SelectedCylinder, b: _SelectedCylinder) -> bool:
        aa, da, ra = geometry(a)
        ab, db, rb = geometry(b)
        return bool(
            abs(float(da @ db)) > 1 - 1e-10
            and np.allclose(aa, ab, atol=1e-7, rtol=1e-7)
            and np.isclose(ra, rb, atol=1e-7, rtol=1e-7)
        )

    proposed_hint = None
    if hint is not None:
        chart = "invalid-cylinder-hint"
        parameters: tuple[float, ...] = ()
        try:
            raw = hint.get("analytic", hint)
            anchor = np.asarray(raw["origin"], dtype=float)
            direction = np.asarray(raw["axis"], dtype=float)
            radius = float(raw["radius"])
            if (
                anchor.shape == (3,)
                and direction.shape == (3,)
                and np.isfinite(anchor).all()
                and np.isfinite(direction).all()
                and np.linalg.norm(direction) > 0
                and np.isfinite(radius)
                and radius > 0
            ):
                rotation = cylinder_basis(direction)
                with np.errstate(over="ignore", invalid="ignore"):
                    center = ((anchor - frame.origin) / frame.length) @ rotation
                    translated = q - ((anchor - frame.origin) / frame.length)
                    normalized_radius = radius / frame.length
                if np.isfinite(translated).all() and not np.all(
                    translated == translated[0]
                ):
                    chart = "optional-cylinder-hint"
                    bases[chart] = rotation
                    parameters = (
                        float(center[0]),
                        float(center[1]),
                        0.0,
                        0.0,
                        normalized_radius,
                    )
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        proposed_hint = FitCandidate(
            "optional-cylinder-hint",
            parameters,
            "optional disposable geometry",
            chart,
            hint_binding_sha256 or binding.sha256,
        )
    problem = RecoveryProblem(
        binding, {key: 5 for key in bases}, solve, assess, equivalent
    )
    try:
        recovered = recover_fit(problem, fresh, hint=proposed_hint, cancelled=cancelled)
    except RecoveryFailure as error:
        raise CylinderFitFailure(
            error.code,
            str(error),
            {**initialization, "recovery": error.diagnostics.json()},
        ) from error
    result = recovered.result
    anchor, axis, radius = geometry(result)
    resolved = resolve_support(
        intent,
        frame.origin + frame.length * anchor,
        axis,
        points,
        reference=frame.origin,
    )
    world_anchor = np.asarray(resolved.origin)
    axis = np.asarray(resolved.axis)
    anchor = (world_anchor - frame.origin) / frame.length
    heights = (points - world_anchor) @ axis
    bounds = list(resolved.domain)
    if not np.isfinite(bounds).all() or bounds[0] >= bounds[1]:
        raise CylinderFitFailure(
            "unsupported-support",
            "axial support cannot be represented at the fitted anchor",
            initialization,
        )
    residual, jac = aligned(result)
    radial = q - anchor - ((q - anchor) @ axis)[:, None] * axis
    radial /= np.linalg.norm(radial, axis=1)[:, None]
    assessment = assess(result)
    initialization["recovery"] = recovered.diagnostics.json()
    numerical = result.numerical
    fitted: dict[str, Any] = {
        "kind": "cylinder",
        "analytic": {
            "kind": "cylinder",
            "origin": world_anchor.tolist(),
            "axis": axis.tolist(),
            "radius": frame.length * radius,
            "slope": 0.0,
            "axial_domain": bounds,
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
        if float(w @ np.sum(radial * unit_normals, axis=1)) >= 0
        else -1.0,
        "solver": {
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": assessment.rank,
            "scaled_jacobian_condition": assessment.condition,
            "scaled_projected_gradient_infinity_norm": float(
                np.max(np.abs(jac.T @ (w * residual)))
            ),
            "coordinate_origin": frame.origin.tolist(),
            "coordinate_length": frame.length,
        },
        "initialization": initialization,
    }
    if domain is not None:
        fitted["source_axial_domain"] = list(domain)
    fitted["support_classification"] = {
        "inside_vertices": int(np.sum((heights >= bounds[0]) & (heights <= bounds[1]))),
        "outside_vertices": int(np.sum((heights < bounds[0]) | (heights > bounds[1]))),
        "role": "selection-and-display",
    }
    return fitted
