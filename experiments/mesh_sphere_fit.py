"""Exploratory area-weighted sphere fitting, separate from fitting admission."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, NotRequired, TypedDict

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
from experiments.fit_solver import AttemptControl, SolverDiagnostics, normalized_weights
from experiments.mesh_cylinder_fit import Array
from scansor.nonlinear_least_squares import LeastSquaresResult, solve_least_squares


class SphereFitFailure(ValueError):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "sphere_fit_failure",
            "code": code,
            **(details or {}),
        }
        super().__init__(message)


def _condition(singular: Array) -> float:
    return float(singular[0] / singular[-1]) if singular[-1] > 0 else math.inf


def _condition_json(value: float) -> float | str:
    return value if math.isfinite(value) else "Infinity"


class SphereFitResult(TypedDict):
    parameters: list[float]
    objective_history: list[float]
    weighted_rms: float
    weighted_mean_residual: float
    normal_matrix_condition: float
    gradient_infinity_norm: float
    converged: bool
    solver: NotRequired[SolverDiagnostics]
    initialization: NotRequired[dict[str, Any]]


def residual_jacobian(points: Array, parameters: Array) -> tuple[Array, Array]:
    """Signed radial distance and derivatives for center XYZ plus radius."""
    center, radius = parameters[:3], float(parameters[3])
    radial = points - center
    distance = np.linalg.norm(radial, axis=1)
    if np.any(distance <= 0):
        raise ValueError("sphere distance derivative is undefined at its center")
    return distance - radius, np.column_stack(
        (-radial / distance[:, None], -np.ones(len(points)))
    )


def fit_sphere(points: Array, weights: Array) -> SphereFitResult:
    """Fit center and radius using every selected positive-area observation."""
    if points.ndim != 2 or points.shape[1] != 3 or len(points) < 4:
        raise SphereFitFailure(
            "insufficient-observations", "expected at least four XYZ rows"
        )
    if weights.shape != (len(points),):
        raise SphereFitFailure("invalid-input", "weight shape mismatch")
    if not np.isfinite(points).all() or not np.isfinite(weights).all():
        raise SphereFitFailure("invalid-input", "nonfinite fit input")
    if np.any(weights <= 0):
        raise SphereFitFailure("invalid-input", "weights must be positive")

    try:
        w = normalized_weights(weights)
    except ValueError as error:
        raise SphereFitFailure("invalid-input", str(error)) from error
    try:
        frame = CoordinateFrame.from_observations(points, weights)
    except ValueError as error:
        raise SphereFitFailure(
            "rank-deficient",
            "ill-conditioned sphere observations; paint a wider curved patch",
        ) from error
    origin, scale = frame.origin, frame.length
    normalized = (points - origin) / scale
    design = np.column_stack((2 * normalized, np.ones(len(points))))
    weighted_design = np.sqrt(w)[:, None] * design
    algebraic, _, rank, singular = np.linalg.lstsq(
        weighted_design,
        np.sqrt(w) * np.sum(normalized * normalized, axis=1),
        rcond=None,
    )
    condition = _condition(singular)
    initialization: dict[str, Any] = {
        "algorithm": "centered-algebraic-sphere-v1",
        "chart": "normalized-center-radius-v1",
        "origin": origin.tolist(),
        "length": scale,
        "observations": len(points),
        "algebraic_rank": int(rank),
        "algebraic_condition": _condition_json(condition),
    }
    if rank != 4 or not np.isfinite(condition) or condition > 1e12:
        raise SphereFitFailure(
            "rank-deficient",
            "ill-conditioned sphere observations; paint a wider curved patch",
            initialization,
        )
    radius_squared = float(algebraic[3] + algebraic[:3] @ algebraic[:3])
    if radius_squared <= 0 or not np.isfinite(radius_squared):
        raise SphereFitFailure(
            "invalid-initialization",
            "sphere initialization produced a nonpositive radius",
            initialization,
        )
    initial = np.array([*algebraic[:3], np.sqrt(radius_squared)])
    initialization["initial_parameters"] = initial.tolist()

    def evaluate(local: Array) -> tuple[Array, Array]:
        r, jac = residual_jacobian(normalized, local)
        return np.sqrt(w) * r, np.sqrt(w)[:, None] * jac

    numerical = solve_least_squares(
        initial,
        evaluate,
        parameter_scales=np.ones(4),
        residual_scale=1.0,
        geometry_is_valid=lambda p: bool(
            p[3] > 0 and np.all(np.linalg.norm(normalized - p[:3], axis=1) > 0)
        ),
    )
    local = np.asarray(numerical.parameters)
    parameters = np.array([*(origin + scale * local[:3]), scale * local[3]])

    residual, jacobian = residual_jacobian(points, parameters)
    gradient = jacobian.T @ (w * residual)
    return {
        "parameters": parameters.tolist(),
        "objective_history": [
            2 * value * scale**2 for value in numerical.objective_history
        ],
        "weighted_rms": float(np.sqrt(w @ residual**2)),
        "weighted_mean_residual": float(w @ residual),
        "normal_matrix_condition": numerical.condition**2,
        "gradient_infinity_norm": float(np.max(np.abs(gradient))),
        "converged": True,
        "initialization": initialization,
        "solver": {
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": numerical.rank,
            "scaled_jacobian_condition": numerical.condition,
            "scaled_projected_gradient_infinity_norm": numerical.projected_gradient_norm,
            "coordinate_origin": origin.tolist(),
            "coordinate_length": scale,
        },
    }


class SharedSphereFitResult(TypedDict):
    parameters: list[float]
    normal_matrix_condition: float
    solver: SolverDiagnostics
    initialization: dict[str, Any]


def fit_shared_sphere_center(
    points: list[Array],
    weights: list[Array],
    binding: InitializationBinding,
    *,
    hint_center: Array | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> SharedSphereFitResult:
    """Fit one center and independent radii from the complete joint observations.

    The caller supplies real source/intent identity. No source identity or hard
    center is inferred from an optional numerical hint. Combined rank matters:
    individual circular patches can jointly identify the shared center.
    """
    if not points or len(points) != len(weights):
        raise SphereFitFailure("invalid-input", "sphere groups and weights must match")
    if any(p.ndim != 2 or p.shape[1] != 3 or len(p) < 4 for p in points):
        raise SphereFitFailure(
            "insufficient-observations",
            "each point-bound sphere needs at least four XYZ observations",
        )
    if any(
        w.shape != (len(p),)
        or not np.isfinite(p).all()
        or not np.isfinite(w).all()
        or np.any(w <= 0)
        for p, w in zip(points, weights, strict=True)
    ):
        raise SphereFitFailure(
            "invalid-input", "sphere groups need finite positive-area observations"
        )
    control = AttemptControl(binding.policy.attempt, cancelled)
    control.checkpoint()
    pooled = np.concatenate(points)
    pooled_weights = np.concatenate(weights)
    try:
        w = normalized_weights(pooled_weights)
    except ValueError as error:
        raise SphereFitFailure("invalid-input", str(error)) from error
    try:
        frame = CoordinateFrame.from_observations(pooled, pooled_weights)
    except ValueError as error:
        raise SphereFitFailure(
            "rank-deficient", "point-bound sphere geometry is ill-conditioned"
        ) from error
    q = (pooled - frame.origin) / frame.length
    group = np.repeat(np.arange(len(points)), [len(p) for p in points])
    rows = np.arange(len(q))
    size = 3 + len(points)
    indicators = np.zeros((len(q), len(points)))
    indicators[rows, group] = 1
    design = np.column_stack((2 * q, indicators))
    square_root = np.sqrt(w)
    algebraic, _, rank, singular = np.linalg.lstsq(
        square_root[:, None] * design,
        square_root * np.sum(q * q, axis=1),
        rcond=None,
    )
    condition = _condition(singular)
    initialization: dict[str, Any] = {
        "algorithm": "joint-algebraic-shared-sphere-v1",
        "chart": "normalized-center-radii-v1",
        "origin": frame.origin.tolist(),
        "length": frame.length,
        "observations": len(q),
        "algebraic_rank": int(rank),
        "algebraic_condition": _condition_json(condition),
    }
    control.checkpoint()
    if rank != size or not math.isfinite(condition) or condition > 1e12:
        raise SphereFitFailure(
            "rank-deficient",
            "point-bound sphere geometry is ill-conditioned; select joint patches that identify the center",
            initialization,
        )
    group_weights = np.bincount(group, weights=w)

    def start_at(center: Array) -> Array:
        distance = np.linalg.norm(q - center, axis=1)
        radii = np.bincount(group, weights=w * distance) / group_weights
        return np.r_[center, radii]

    def valid(parameters: Array) -> bool:
        return bool(
            np.isfinite(parameters).all()
            and np.all(parameters[3:] > 0)
            and np.all(np.linalg.norm(q - parameters[:3], axis=1) > 0)
        )

    def evaluate(parameters: Array) -> tuple[Array, Array]:
        delta = q - parameters[:3]
        distance = np.linalg.norm(delta, axis=1)
        if np.any(distance <= 0):
            raise ValueError("sphere observations cannot coincide with their center")
        residual = distance - parameters[3 + group]
        jacobian = np.zeros((len(q), size))
        jacobian[:, :3] = -delta / distance[:, None]
        jacobian[rows, 3 + group] = -1
        return residual, jacobian

    def solve(candidate: FitCandidate, attempt: AttemptControl) -> LeastSquaresResult:
        def callback(parameters: Array) -> tuple[Array, Array]:
            residual, jacobian = evaluate(parameters)
            return square_root * residual, square_root[:, None] * jacobian

        def geometry(parameters: Array) -> bool:
            attempt.checkpoint()
            return valid(parameters)

        return solve_least_squares(
            candidate.parameters,
            lambda p: attempt.call(callback, p),
            parameter_scales=np.ones(size),
            residual_scale=1,
            geometry_is_valid=geometry,
            max_iterations=attempt.limits.max_iterations,
            max_trials=attempt.limits.max_trials,
        )

    def assess(result: LeastSquaresResult) -> FitAssessment:
        parameters = np.asarray(result.parameters)
        residual, _ = evaluate(parameters)
        return FitAssessment(
            float(w @ residual**2),
            valid(parameters),
            result.rank,
            result.condition,
            result.termination,
            result.iterations,
        )

    chart = "normalized-center-radii-v1"
    fresh = FitCandidate(
        "joint-algebraic",
        tuple(start_at(algebraic[:3]).tolist()),
        "all selected joint observations",
        chart,
        binding.sha256,
    )
    hint = None
    if hint_center is not None:
        # Invalid hints are diagnostics, never a prerequisite to fresh fitting.
        hint_chart = chart if hint_center.shape == (3,) else "invalid-center-hint"
        with np.errstate(over="ignore", invalid="ignore"):
            if hint_center.shape == (3,):
                center = (hint_center - frame.origin) / frame.length
                translated = q - center
                if np.isfinite(translated).all() and np.all(
                    translated == translated[0]
                ):
                    # The candidate chart has erased every source difference.
                    # Do not manufacture an objective by subtracting enormous
                    # indistinguishable distances; the fresh chart still owns
                    # the complete, spatially distinct selected observations.
                    hint_chart = "unrepresentable-center-hint"
                values = start_at(center)
            else:
                values = hint_center.ravel()
        hint = FitCandidate(
            "free-center-hint",
            tuple(values.tolist()),
            "optional authored free center",
            hint_chart,
            binding.sha256,
        )
    problem = RecoveryProblem(
        binding,
        {chart: size},
        solve,
        assess,
        lambda a, b: bool(
            np.allclose(a.parameters, b.parameters, rtol=1e-8, atol=1e-8)
        ),
    )
    try:
        recovered = recover_fit(problem, [fresh], hint=hint, cancelled=cancelled)
    except RecoveryFailure as error:
        raise SphereFitFailure(
            error.code,
            str(error),
            {**initialization, "recovery": error.diagnostics.json()},
        ) from error
    numerical = recovered.result
    local = np.asarray(numerical.parameters)
    parameters = np.r_[
        frame.origin + frame.length * local[:3], frame.length * local[3:]
    ]
    initialization["recovery"] = recovered.diagnostics.json()
    return {
        "parameters": parameters.tolist(),
        "normal_matrix_condition": numerical.condition**2,
        "initialization": initialization,
        "solver": {
            "implementation": "scaled-svd-least-squares-v1",
            "termination": numerical.termination,
            "iterations": numerical.iterations,
            "evaluations": numerical.evaluations,
            "rank": numerical.rank,
            "scaled_jacobian_condition": numerical.condition,
            "scaled_projected_gradient_infinity_norm": numerical.projected_gradient_norm,
            "coordinate_origin": frame.origin.tolist(),
            "coordinate_length": frame.length,
        },
    }
