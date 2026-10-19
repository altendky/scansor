"""Opt-in issue #344 experiment; production modules are never modified.

Run serially with all three BLAS/OpenMP thread variables set to 1. The matrix,
schedule and gate below are fixed before timing. Private AST copies change only
the selected family's nested numerical solve; every full-data proposal, chart,
guard, assessment and ambiguity decision remains the original implementation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
import math
import os
import platform
import resource
import statistics
import subprocess
import sys
import time
import types
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import DEFAULT_POLICY, initialization_binding
from experiments.fit_solver import AttemptControl, normalized_weights
from scansor.nonlinear_least_squares import (
    LeastSquaresFailure,
    LeastSquaresResult,
    solve_least_squares,
)

type Array = NDArray[np.float64]
type Family = Literal["cone", "cylinder"]
type Evaluation = Callable[[Array], tuple[Array, Array]]
ROOT = Path(__file__).resolve().parents[1]
SCHEDULE = (256, 1024)
SMALL_BYPASS = 1024
COARSE_ITERATIONS = 12
COARSE_GRADIENT_TOLERANCE = 1e-4
COUNTS = (512, 4096, 16384)
SEEDS = (7, 19)
SHAPES = ("wide", "narrow")
THREADS = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
PREREGISTRATION: dict[str, Any] = {
    "version": "progressive-selected-fit-experiment-v1",
    "counts": COUNTS,
    "seeds": SEEDS,
    "shapes": SHAPES,
    "schedule": SCHEDULE,
    "small_bypass": SMALL_BYPASS,
    "coarse_max_iterations": COARSE_ITERATIONS,
    "coarse_gradient_tolerance": COARSE_GRADIENT_TOLERANCE,
    "timed_repeats": 3,
    "warmups_per_variant": 1,
    "large_minimum_baseline_seconds": 0.05,
    "large_minimum_median_speedup": 2.0,
    "large_max_regression": {"relative": 0.10, "seconds": 0.010},
    "small_max_regression": {"relative": 0.10, "seconds": 0.002},
    "accuracy": {
        "paired_geometry": "original RecoveryProblem.equivalent callback",
        "objective_atol": DEFAULT_POLICY.objective_atol,
        "objective_rtol": DEFAULT_POLICY.objective_rtol,
        "residual_difference_over_length": 1e-7,
        "axis_cross_difference": 1e-6,
        "aligned_scaled_gradient": 1e-10,
        "independent_gradient": 1e-9,
        "aligned_condition": 1e6,
        "truth_axis_cross": 0.02,
        "truth_axis_distance": 0.05,
        "truth_radius": 0.05,
        "truth_slope": 0.02,
    },
    "controls": (
        "reordered-4096",
        "nonuniform-density-4096",
        "sphere-216",
        "cone-plane-4096",
        "public-failure-regressions",
    ),
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass
class Recorder:
    attempts: list[dict[str, Any]] = field(default_factory=list)
    selected: Any = None
    equivalent: Callable[[Any, Any], bool] | None = None


def rewrite_source(source: str, family: Family) -> ast.Module:
    """Fail closed if the exact bounded instrumentation location changes."""
    tree = ast.parse(source)
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == f"fit_selected_{family}"
    ]
    if len(functions) != 1:
        raise ValueError("expected one selected-family adapter")
    solves = [
        node
        for node in functions[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "solve"
    ]
    if len(solves) != 1:
        raise ValueError("expected one nested candidate solve")
    calls = [
        node
        for node in ast.walk(solves[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "solve_least_squares"
    ]
    if len(calls) != 1:
        raise ValueError("expected exactly one nested numerical solve")
    call = calls[0]
    expected = {
        "parameter_scales",
        "residual_scale",
        "lower_bounds",
        "upper_bounds",
        "rank_tolerance",
        "geometry_is_valid",
        "max_iterations",
        "max_trials",
    }
    control = "control" if family == "cone" else "attempt"
    if (
        len(call.args) != 2
        or {item.arg for item in call.keywords} != expected
        or ast.unparse(call.args[0]) != "candidate.parameters"
        or ast.unparse(call.args[1]) != f"lambda p: {control}.call(evaluate, p)"
    ):
        raise ValueError("selected numerical call contract changed")
    call.func = ast.Name(id="_experiment_solve", ctx=ast.Load())
    for key, name in (
        ("local", "local"),
        ("weights", "w"),
        ("control", control),
        (
            "primitive",
            "cone_residual_jacobian" if family == "cone" else "residual_jacobian",
        ),
    ):
        call.keywords.append(
            ast.keyword(arg="_" + key, value=ast.Name(id=name, ctx=ast.Load()))
        )
    return ast.fix_missing_locations(tree)


def progressive_solve(
    initial: Sequence[float] | Array,
    full_callback: Evaluation,
    *,
    _local: Array,
    _weights: Array,
    _primitive: Callable[[Array, Array], tuple[Array, Array]],
    _control: AttemptControl,
    recorder: Recorder,
    **original: Any,
) -> LeastSquaresResult:
    """Separate fixed-row solves, one cumulative attempt budget, full final pass.

    Full-data geometry guards remain intentionally expensive. A failed coarse
    solve restarts from the original candidate, with spent work still charged.
    Such spending can exhaust a difficult case: classification preservation is
    measured, never assumed. Cancellation and the global quota are not caught.
    """
    _control.checkpoint()
    count = len(_local)
    attempt: dict[str, Any] = {"rows": count, "stages": [], "sampling_seconds": 0.0}
    recorder.attempts.append(attempt)
    start = np.asarray(initial)
    used_iterations = 0
    limit = int(original["max_iterations"])
    if count > SMALL_BYPASS:
        began = time.perf_counter()
        order = np.random.default_rng(344).permutation(count)
        attempt["order_sha256"] = digest(order.astype("<i8").tobytes())
        attempt["sampling_seconds"] = time.perf_counter() - began
        subsets = [np.sort(order[:size]) for size in SCHEDULE if size < count]
    else:
        subsets = []
    for indices in [*subsets, None]:
        final = indices is None
        if not final and attempt.get("coarse_fallback"):
            continue
        _control.checkpoint()
        remaining = max(0, limit - used_iterations)
        kwargs = dict(original)
        kwargs["max_iterations"] = (
            remaining if final else min(COARSE_ITERATIONS, remaining)
        )
        if not final:
            kwargs["gradient_tolerance"] = max(
                COARSE_GRADIENT_TOLERANCE,
                float(original.get("gradient_tolerance", 1e-10)),
            )
        stage: dict[str, Any] = {
            "rows": count if final else len(indices),
            "final": final,
            "iteration_limit": kwargs["max_iterations"],
            "trial_limit": kwargs["max_trials"],
            "gradient_tolerance": kwargs.get("gradient_tolerance", 1e-10),
            "callback_seconds": 0.0,
            "geometry_seconds": 0.0,
            "geometry_calls": 0,
        }
        attempt["stages"].append(stage)
        callback: Evaluation
        if final:
            callback = full_callback
        else:
            assert indices is not None
            local = _local[indices]
            root_weights = np.sqrt(normalized_weights(_weights[indices]))

            def subset_evaluate(
                parameters: Array,
                _points: Array = local,
                _root_weights: Array = root_weights,
            ) -> tuple[Array, Array]:
                residual, jacobian = _primitive(_points, parameters)
                return _root_weights * residual, _root_weights[:, None] * jacobian

            def subset_callback(
                parameters: Array, _evaluate: Evaluation = subset_evaluate
            ) -> tuple[Array, Array]:
                return _control.call(_evaluate, parameters)

            callback = subset_callback

        def timed_callback(
            parameters: Array,
            _callback: Evaluation = callback,
            _stage: dict[str, Any] = stage,
        ) -> tuple[Array, Array]:
            began = time.perf_counter()
            try:
                return _callback(parameters)
            finally:
                _stage["callback_seconds"] += time.perf_counter() - began

        guard = original["geometry_is_valid"]

        def timed_guard(
            parameters: Array,
            _guard: Callable[[Array], bool] = guard,
            _stage: dict[str, Any] = stage,
        ) -> bool:
            _control.checkpoint()
            began = time.perf_counter()
            try:
                return bool(_guard(parameters))
            finally:
                _stage["geometry_seconds"] += time.perf_counter() - began
                _stage["geometry_calls"] += 1

        kwargs["geometry_is_valid"] = timed_guard
        before = _control.evaluations
        began = time.perf_counter()
        result: LeastSquaresResult | None = None
        try:
            result = solve_least_squares(start, timed_callback, **kwargs)
            stage["outcome"] = "success"
        except LeastSquaresFailure as error:
            stage["outcome"] = error.code
            result = error.diagnostics
            if final:
                raise
            start = np.asarray(initial)
            attempt["coarse_fallback"] = True
        finally:
            stage["seconds"] = time.perf_counter() - began
            stage["callbacks"] = _control.evaluations - before
            stage["processed_rows"] = stage["callbacks"] * stage["rows"]
            stage["geometry_processed_rows"] = stage["geometry_calls"] * count
            iterations = (
                result.iterations
                if result is not None
                else (kwargs["max_iterations"] if stage["callbacks"] else 0)
            )
            used_iterations += iterations
            stage["iterations"] = iterations
            stage["cumulative_iterations"] = used_iterations
            stage["cumulative_callbacks"] = _control.evaluations
            stage["solver_other_seconds"] = max(
                0.0,
                stage["seconds"]
                - stage["callback_seconds"]
                - stage["geometry_seconds"],
            )
        if final:
            assert result is not None
            return result
        if attempt.get("coarse_fallback"):
            continue
        assert result is not None
        start = np.asarray(result.parameters)
    raise AssertionError("missing final full-data solve")


def instrumented_family(
    family: Family, recorder: Recorder, *, progressive: bool = True
) -> Callable[..., dict[str, Any]]:
    path = ROOT / f"experiments/mesh_{family}_fit.py"
    name = f"experiments._progressive_{family}_experiment_{progressive}"
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module

    def solve(*args: Any, **kwargs: Any) -> LeastSquaresResult:
        return progressive_solve(*args, recorder=recorder, **kwargs)

    module.__dict__["_experiment_solve"] = solve
    exec(
        compile(
            rewrite_source(path.read_text(), family)
            if progressive
            else ast.parse(path.read_text()),
            str(path),
            "exec",
        ),
        module.__dict__,
    )
    original_recover = module.__dict__["recover_fit"]

    def observe_recovery(problem: Any, *args: Any, **kwargs: Any) -> Any:
        recovered = original_recover(problem, *args, **kwargs)
        recorder.selected = recovered.result
        recorder.equivalent = problem.equivalent
        return recovered

    module.__dict__["recover_fit"] = observe_recovery
    return cast(
        Callable[..., dict[str, Any]], getattr(module, f"fit_selected_{family}")
    )


@dataclass(frozen=True)
class Case:
    family: Family
    count: int
    seed: int
    shape: str = "wide"
    control: str = "primary"

    @property
    def id(self) -> str:
        return f"{self.family}-{self.count}-{self.seed}-{self.shape}-{self.control}"


def registered_cases() -> list[Case]:
    return [
        Case(family, count, seed, shape)
        for family in ("cone", "cylinder")
        for count in COUNTS
        for seed in SEEDS
        for shape in SHAPES
    ] + [
        Case(family, 4096, 19, control=control)
        for family in ("cone", "cylinder")
        for control in ("reordered", "density")
    ]


def fixture(case: Case) -> tuple[Array, Array, Array, dict[str, Any]]:
    """Analytic partial patch; truth is recorded but never passed to a fit."""
    rng = np.random.default_rng(case.seed)
    axis = np.array([0.2, -0.7, 0.3] if case.shape == "wide" else [0.3, 0.5, 1.0])
    axis /= np.linalg.norm(axis)
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    u = np.linspace(0, 1, case.count // 16)
    if case.control == "density":
        u = u**2
    angle = (-1.4 + 3.0 * u) if case.shape == "wide" else (-0.7 + 1.5 * u)
    theta, height = np.meshgrid(angle, np.linspace(-1, 2, 16))
    radial = (
        np.cos(theta.ravel())[:, None] * first + np.sin(theta.ravel())[:, None] * second
    )
    slope = (0.15 if case.shape == "wide" else -0.2) if case.family == "cone" else 0.0
    origin = np.array([80.0, -60.0, 40.0])
    noise = rng.normal(0.0, 0.002, case.count)
    points = (
        origin
        + height.ravel()[:, None] * axis
        + (3.0 + slope * height.ravel() + noise)[:, None] * radial
    )
    normals = (radial - slope * axis) / np.hypot(1, slope)
    weights = (0.2 + 1.8 * (0.5 + 0.5 * np.cos(theta.ravel() - 0.2))) * (
        0.7 + 0.2 * (height.ravel() + 1)
    )
    if case.control == "reordered":
        order = np.random.default_rng(34).permutation(case.count)
        points, normals, weights = points[order], normals[order], weights[order]
    truth = {
        "origin": origin.tolist(),
        "axis": axis.tolist(),
        "radius": 3.0,
        "slope": slope,
    }
    return points, weights, normals, truth


def binding(family: Family, points: Array, weights: Array, normals: Array) -> Any:
    module = importlib.import_module(f"experiments.mesh_{family}_fit")
    return initialization_binding(
        digest(points.astype("<f8").tobytes()),
        digest(weights.astype("<f8").tobytes() + normals.astype("<f8").tobytes()),
        list(range(len(points))),
        {
            "family": family,
            "references": {},
            "constraints": {},
            "support": None,
            "settings": {},
        },
        policy_version=getattr(module, family.upper() + "_POLICY_VERSION"),
    )


def surface_residual(points: Array, analytic: dict[str, Any]) -> Array:
    delta = points - np.asarray(analytic["origin"])
    axis = np.asarray(analytic["axis"])
    axial = delta @ axis
    radial = delta - axial[:, None] * axis
    return (
        np.linalg.norm(radial, axis=1) - analytic["radius"] - analytic["slope"] * axial
    ) / np.hypot(1, analytic["slope"])


def certificate(
    points: Array, weights: Array, result: dict[str, Any], truth: dict[str, Any]
) -> dict[str, Any]:
    """Independent all-row residual, aligned-gradient and known-truth checks."""
    analytic = result["analytic"]
    origin, axis = np.asarray(analytic["origin"]), np.asarray(analytic["axis"])
    slope = float(analytic["slope"])
    residual = surface_residual(points, analytic)
    w = normalized_weights(weights)
    frame = CoordinateFrame.from_observations(points, weights)
    length = frame.length
    frame_correct = math.isclose(
        float(result["solver"]["coordinate_length"]), length, rel_tol=1e-12, abs_tol=0.0
    ) and bool(
        np.allclose(
            result["solver"]["coordinate_origin"],
            frame.origin,
            atol=1e-12 * length,
            rtol=0,
        )
    )
    center = w @ points
    anchor = origin + ((center - origin) @ axis) * axis
    first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
    first /= np.linalg.norm(first)
    x, y, z = (
        (points - anchor)
        @ np.column_stack((first, np.cross(axis, first), axis))
        / length
    ).T
    rho = np.hypot(x, y)
    ux, uy = x / rho, y / rho
    scale = float(np.hypot(1, slope))
    jac = np.column_stack(
        (
            -ux / scale,
            -uy / scale,
            (-z * ux - slope * x) / scale,
            (-z * uy - slope * y) / scale,
            -np.ones(len(points)) / scale,
        )
    )
    if analytic["kind"] == "cone":
        jac = np.column_stack(
            (jac, -z / scale - (residual / length) * slope / scale**2)
        )
    gradient = float(np.max(np.abs(jac.T @ (w * residual / length))))
    truth_axis = np.asarray(truth["axis"])
    truth_origin = np.asarray(truth["origin"])
    sign = 1 if float(axis @ truth_axis) >= 0 else -1
    nearest = origin + ((truth_origin - origin) @ axis) * axis
    radius_at_truth_origin = float(
        analytic["radius"] + slope * ((truth_origin - origin) @ axis)
    )
    domain = np.asarray(analytic["axial_domain"])
    delta = points - origin
    axial = delta @ axis
    radial = np.linalg.norm(delta - axial[:, None] * axis, axis=1)
    projected = (axial + slope * (radial - analytic["radius"])) / (1 + slope**2)
    measured: dict[str, Any] = {
        "independent_gradient": gradient,
        "normalized_objective": float(w @ (residual / length) ** 2),
        "rms_reconstruction_error": abs(
            float(np.sqrt(w @ residual**2)) - result["weighted_rms"]
        ),
        "published_residual_error": float(
            np.max(np.abs(np.asarray(result["residuals"]) - residual))
        ),
        "truth_axis_cross": float(np.linalg.norm(np.cross(axis, truth_axis))),
        "truth_axis_distance": float(np.linalg.norm(nearest - truth_origin)),
        "truth_radius_error": abs(radius_at_truth_origin - truth["radius"]),
        "truth_slope_error": abs(slope - sign * truth["slope"]),
        "positive_sheet": bool(
            np.all(analytic["radius"] + slope * domain > 0)
            and np.all(analytic["radius"] + slope * projected > 0)
        ),
        "all_observations": len(residual),
    }
    limits = PREREGISTRATION["accuracy"]
    checks = {
        "full_coordinate_frame": frame_correct,
        "independent_gradient": gradient <= limits["independent_gradient"],
        "production_stationarity": result["solver"][
            "aligned_scaled_gradient_infinity_norm"
        ]
        <= limits["aligned_scaled_gradient"],
        "condition": result["solver"]["aligned_jacobian_condition"]
        <= limits["aligned_condition"],
        "rank": result["solver"]["rank"] == (6 if analytic["kind"] == "cone" else 5),
        "residual_reconstruction": measured["published_residual_error"]
        <= 1e-10 * length
        and measured["rms_reconstruction_error"] <= 1e-10 * length,
        "truth": measured["truth_axis_cross"] <= limits["truth_axis_cross"]
        and measured["truth_axis_distance"] <= limits["truth_axis_distance"]
        and measured["truth_radius_error"] <= limits["truth_radius"]
        and measured["truth_slope_error"] <= limits["truth_slope"],
        "positive_sheet": measured["positive_sheet"],
    }
    return {"passed": all(checks.values()), "checks": checks, "measured": measured}


def run_case(case: Case, repeats: int = 3) -> dict[str, Any]:
    points, weights, normals, truth = fixture(case)
    bind = binding(case.family, points, weights, normals)
    recorder = Recorder()
    baseline_recorder = Recorder()
    ordinary = instrumented_family(case.family, baseline_recorder, progressive=False)
    progressive = instrumented_family(case.family, recorder)
    selected_results: dict[str, list[Any]] = {"baseline": [], "progressive": []}
    equivalences: list[Callable[[Any, Any], bool] | None] = []
    samples: dict[str, list[dict[str, Any]]] = {"baseline": [], "progressive": []}
    for repetition in range(-1, repeats):
        order = (
            ("baseline", "progressive")
            if repetition % 2 == 0
            else ("progressive", "baseline")
        )
        for variant in order:
            recorder.attempts.clear()
            current_recorder = baseline_recorder if variant == "baseline" else recorder
            current_recorder.selected = None
            current_recorder.equivalent = None
            began = time.perf_counter()
            try:
                result = (ordinary if variant == "baseline" else progressive)(
                    points, weights, normals, bind
                )
                classification = "success"
            except ValueError as error:
                result = {
                    "error": str(error),
                    "diagnostic": getattr(error, "diagnostic", {}),
                }
                classification = str(getattr(error, "code", type(error).__name__))
            seconds = time.perf_counter() - began
            if repetition >= 0:
                selected_results[variant].append(current_recorder.selected)
                if variant == "baseline":
                    equivalences.append(current_recorder.equivalent)
                cert = (
                    certificate(points, weights, result, truth)
                    if classification == "success"
                    else {"passed": False}
                )
                compact = {
                    key: value for key, value in result.items() if key != "residuals"
                }
                samples[variant].append(
                    {
                        "seconds": seconds,
                        "classification": classification,
                        "result": compact,
                        "certificate": cert,
                        "work": list(recorder.attempts)
                        if variant == "progressive"
                        else None,
                    }
                )
    comparisons: list[dict[str, Any]] = []
    limits = PREREGISTRATION["accuracy"]
    for index, (baseline, staged) in enumerate(
        zip(samples["baseline"], samples["progressive"], strict=True)
    ):
        same_class = baseline["classification"] == staged["classification"]
        comparison: dict[str, Any] = {
            "classification_preserved": same_class,
            "accuracy_preserved": False,
        }
        if baseline["classification"] == staged["classification"] == "success":
            a, b = baseline["result"], staged["result"]
            length = a["solver"]["coordinate_length"]
            delta = (
                float(
                    np.max(
                        np.abs(
                            surface_residual(points, a["analytic"])
                            - surface_residual(points, b["analytic"])
                        )
                    )
                )
                / length
            )
            cross = float(
                np.linalg.norm(np.cross(a["analytic"]["axis"], b["analytic"]["axis"]))
            )
            equivalent = equivalences[index]
            family_equivalent = equivalent is not None and equivalent(
                selected_results["baseline"][index],
                selected_results["progressive"][index],
            )
            objective_equal = math.isclose(
                baseline["certificate"]["measured"]["normalized_objective"],
                staged["certificate"]["measured"]["normalized_objective"],
                rel_tol=DEFAULT_POLICY.objective_rtol,
                abs_tol=DEFAULT_POLICY.objective_atol,
            )
            proposals_equal = [
                (item["candidate"], item["role"])
                for item in a["initialization"]["recovery"]["attempts"]
            ] == [
                (item["candidate"], item["role"])
                for item in b["initialization"]["recovery"]["attempts"]
            ]
            comparison.update(
                original_proposals_preserved=proposals_equal,
                original_family_equivalent=family_equivalent,
                full_normalized_objective_preserved=objective_equal,
                residual_difference_over_length=delta,
                axis_cross_difference=cross,
                accuracy_preserved=bool(
                    baseline["certificate"]["passed"]
                    and staged["certificate"]["passed"]
                    and family_equivalent
                    and objective_equal
                    and proposals_equal
                    and delta <= limits["residual_difference_over_length"]
                    and cross <= limits["axis_cross_difference"]
                ),
            )
        comparisons.append(comparison)
    return {
        "case": asdict(case),
        "id": case.id,
        "input_sha256": digest(
            points.astype("<f8").tobytes()
            + weights.astype("<f8").tobytes()
            + normals.astype("<f8").tobytes()
        ),
        "truth": truth,
        "samples": samples,
        "comparisons": comparisons,
        "medians": {
            variant: statistics.median(sample["seconds"] for sample in values)
            for variant, values in samples.items()
        },
    }


def run_controls() -> list[dict[str, Any]]:
    """Unchanged ordinary paths, reported separately from selected-family gains."""
    from experiments.browser_static.workload_cases import SUPPORT
    from experiments.browser_static.workload_cases import fixture as joint_fixture
    from experiments.mesh_cone_plane_fit import (
        cone_plane_residual_jacobian,
        fit_cone_plane,
        valid_cone_geometry,
    )
    from experiments.mesh_sphere_fit import fit_sphere

    azimuth, elevation = np.meshgrid(
        np.linspace(0, 2 * np.pi, 24, endpoint=False), np.linspace(-0.9, 1.1, 9)
    )
    directions = np.column_stack(
        (
            np.cos(elevation.ravel()) * np.cos(azimuth.ravel()),
            np.cos(elevation.ravel()) * np.sin(azimuth.ravel()),
            np.sin(elevation.ravel()),
        )
    )
    center = np.array([1.2, -3.4, 0.7])
    points = (
        center
        + (2.6 + 0.002 * np.sin(np.arange(len(directions)) * 1.7))[:, None] * directions
    )
    weights = np.linspace(0.2, 2, len(points))
    joint = joint_fixture(256, 14, 32, 16)

    def sphere() -> dict[str, Any]:
        return dict(fit_sphere(points, weights))

    def cone_plane() -> dict[str, Any]:
        return dict(
            fit_cone_plane(
                joint["cone"],
                joint["plane"],
                joint["cw"],
                joint["pw"],
                joint["initial"],
                SUPPORT,
            )
        )

    output: list[dict[str, Any]] = []
    for name, fit, truth in (
        ("sphere-216", sphere, np.r_[center, 2.6]),
        (
            "cone-plane-4096",
            cone_plane,
            np.array([0.3, -0.4, 0.12, -0.08, 3.2, 4.1, 0.08]),
        ),
    ):
        samples: dict[str, list[dict[str, Any]]] = {"baseline": [], "progressive": []}
        for repetition in range(-1, 3):
            for variant in (
                ("baseline", "progressive")
                if repetition % 2 == 0
                else ("progressive", "baseline")
            ):
                began = time.perf_counter()
                result = fit()
                seconds = time.perf_counter() - began
                if repetition >= 0:
                    parameters = np.asarray(result["parameters"])
                    if name == "sphere-216":
                        residual = (
                            np.linalg.norm(points - parameters[:3], axis=1)
                            - parameters[3]
                        )
                        full_weights = normalized_weights(weights)
                        geometry_valid = bool(parameters[3] > 0)
                        expected_rank = 4
                    else:
                        residual, _ = cone_plane_residual_jacobian(
                            joint["cone"], joint["plane"], parameters, SUPPORT
                        )
                        full_weights = normalized_weights(
                            np.r_[joint["cw"], joint["pw"]]
                        )
                        geometry_valid = valid_cone_geometry(
                            parameters, SUPPORT, joint["cone"]
                        )
                        expected_rank = 7
                    rms_error = abs(
                        float(np.sqrt(full_weights @ residual**2))
                        - result["weighted_rms"]
                    )
                    solver = result["solver"]
                    valid = bool(
                        solver["termination"] == "projected-gradient"
                        and solver["scaled_projected_gradient_infinity_norm"] <= 1e-10
                        and solver["rank"] == expected_rank
                        and np.isfinite(residual).all()
                        and np.isfinite(parameters).all()
                        and np.isfinite(solver["scaled_jacobian_condition"])
                        and rms_error <= 1e-12
                        and geometry_valid
                        and result["weighted_rms"] < 0.01
                        and np.max(np.abs(parameters - truth)) < 0.02
                    )
                    samples[variant].append(
                        {
                            "seconds": seconds,
                            "correct": valid,
                            "weighted_rms": result["weighted_rms"],
                            "parameters": result["parameters"],
                            "solver": solver,
                            "full_residual_count": len(residual),
                            "rms_reconstruction_error": rms_error,
                        }
                    )
        medians = {
            variant: statistics.median(sample["seconds"] for sample in values)
            for variant, values in samples.items()
        }
        output.append(
            {
                "id": name,
                "path": "same ordinary entry point in both variants; no progressive joint/sphere implementation",
                "samples": samples,
                "medians": medians,
                "correct": all(
                    sample["correct"]
                    for values in samples.values()
                    for sample in values
                ),
                "overhead_pass": medians["progressive"]
                <= 1.1 * medians["baseline"] + 0.002,
            }
        )
    output.append(run_failure_regressions())
    return output


def run_failure_regressions() -> dict[str, Any]:
    """Original hard-plane ambiguity and rank/input rejection classifications."""
    from experiments.support_intent import PlaneSupport, support_record

    theta, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 32, endpoint=False), np.linspace(-3, 3, 7)
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = 3 * normals + np.column_stack((np.zeros((theta.size, 2)), height.ravel()))
    weights = np.linspace(0.2, 2, len(points))
    normal = np.array([1.0, 0, 1e-7])
    normal /= np.linalg.norm(normal)
    support = PlaneSupport(tuple(normal), 0.0, (8.0, 15.0))
    hint = {
        "origin": [3e-11, 0.0, 0.0],
        "axis": [0.0, 0.0, 1.0],
        "radius": 3.0,
        "slope": 0.0,
    }
    records: list[dict[str, Any]] = []
    for family in ("cone", "cylinder"):
        for progressive in (False, True):
            fit = instrumented_family(family, Recorder(), progressive=progressive)
            if family == "cone":
                module = importlib.import_module("experiments.mesh_cone_fit")
                bind = initialization_binding(
                    digest(points.astype("<f8").tobytes()),
                    digest(
                        weights.astype("<f8").tobytes()
                        + normals.astype("<f8").tobytes()
                    ),
                    list(range(len(points))),
                    {"family": family, "support": support_record(support)},
                    policy_version=module.CONE_POLICY_VERSION,
                )
                ordinary = fit(points, weights, normals, bind, support=support)
                a = ordinary["analytic"]
                endpoints = np.asarray(a["origin"]) + np.asarray(a["axial_domain"])[
                    :, None
                ] * np.asarray(a["axis"])
                endpoint_ok = bool(
                    np.allclose(endpoints, [[0, 0, 8], [0, 0, 15]], atol=3e-7, rtol=0)
                )
                try:
                    _ = fit(points, weights, normals, bind, support=support, hint=hint)
                    classification = "success"
                except ValueError as error:
                    classification = str(getattr(error, "code", type(error).__name__))
                records.append(
                    {
                        "case": "near-parallel-hard-plane",
                        "progressive": progressive,
                        "classification": classification,
                        "correct": endpoint_ok and classification == "ambiguous",
                    }
                )
            else:
                angle = np.linspace(0, 2 * np.pi, 4096, endpoint=False)
                circle_normals = np.column_stack(
                    (np.cos(angle), np.sin(angle), np.zeros(len(angle)))
                )
                circle = 3 * circle_normals
                x, y = np.meshgrid(np.linspace(-2, 2, 64), np.linspace(-2, 2, 64))
                plane = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
                for label, p, n, invalid in (
                    ("circle-4096", circle, circle_normals, False),
                    ("plane-4096", plane, np.tile([0.0, 0.0, 1.0], (4096, 1)), False),
                    ("invalid-weight", circle, circle_normals, True),
                ):
                    w = np.linspace(0.2, 2, len(p))
                    if invalid:
                        w[0] = 0.0
                    bind = binding("cylinder", p, w, n)
                    try:
                        _ = fit(p, w, n, bind)
                        classification = "success"
                    except ValueError as error:
                        classification = str(
                            getattr(error, "code", type(error).__name__)
                        )
                    records.append(
                        {
                            "case": label,
                            "progressive": progressive,
                            "classification": classification,
                            "correct": classification != "success",
                        }
                    )
    matched = all(
        len({record["classification"] for record in records if record["case"] == label})
        == 1
        for label in {record["case"] for record in records}
    )
    return {
        "id": "public-failure-regressions",
        "records": records,
        "correct": matched and all(record["correct"] for record in records),
        "overhead_pass": True,
        "timing": "classification-only controls, not a speedup claim",
    }


def evaluate_gate(
    records: list[dict[str, Any]], controls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    controls = [] if controls is None else controls
    controls_valid = (
        {record["id"] for record in controls}
        == {"sphere-216", "cone-plane-4096", "public-failure-regressions"}
        and len(controls) == 3
        and all(
            record.get("correct") is True and record.get("overhead_pass") is True
            for record in controls
        )
    )
    outcomes: dict[str, Any] = {}
    for family in ("cone", "cylinder"):
        registered = {case.id for case in registered_cases() if case.family == family}
        actual = [record for record in records if record["case"]["family"] == family]
        complete = {record["id"] for record in actual} == registered and len(
            actual
        ) == len(registered)
        valid = all(
            len(record["comparisons"]) == 3
            and all(
                item["classification_preserved"]
                and item["accuracy_preserved"]
                and item.get("original_family_equivalent") is True
                and item.get("full_normalized_objective_preserved") is True
                and item.get("original_proposals_preserved") is True
                for item in record["comparisons"]
            )
            for record in actual
        )
        large = [
            record
            for record in actual
            if record["case"]["count"] >= 4096 and record["medians"]["baseline"] >= 0.05
        ]
        ratios = [
            record["medians"]["baseline"] / record["medians"]["progressive"]
            for record in large
        ]
        speedup = statistics.median(ratios) if ratios else None
        regressions = [
            record["id"]
            for record in actual
            if record["medians"]["progressive"]
            > 1.1 * record["medians"]["baseline"]
            + (0.002 if record["case"]["count"] <= SMALL_BYPASS else 0.010)
        ]
        outcomes[family] = {
            "continue": bool(
                complete
                and controls_valid
                and valid
                and speedup is not None
                and speedup >= 2.0
                and not regressions
            ),
            "complete": complete,
            "controls_valid": controls_valid,
            "accuracy_and_classification": valid,
            "large_cases": len(large),
            "median_large_speedup": speedup,
            "regressions": regressions,
            "missing_cases": sorted(registered - {record["id"] for record in actual}),
        }
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--pilot", action="store_true")
    _ = parser.add_argument("--large-pilot", action="store_true")
    args = parser.parse_args()
    if any(os.environ.get(name) != "1" for name in THREADS):
        parser.error(
            "set OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1 before starting"
        )
    if sys.platform != "linux":
        parser.error("this experiment records Linux process-lifetime peak RSS")
    if args.output.exists():
        raise FileExistsError(args.output)
    cases = registered_cases()
    if args.pilot:
        cases = [
            case
            for case in cases
            if case.seed == 7
            and case.shape == "wide"
            and case.count in (512, 4096)
            and case.control == "primary"
        ]
    if args.large_pilot:
        cases = [
            case
            for case in cases
            if case.seed == 7
            and case.shape == "wide"
            and case.count == 16384
            and case.control == "primary"
        ]
    report: dict[str, Any] = {
        "started_utc": datetime.now(UTC).isoformat(),
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "command": sys.argv,
        "preregistration": PREREGISTRATION,
        "preregistration_sha256": digest(
            json.dumps(PREREGISTRATION, sort_keys=True).encode()
        ),
        "registered_case_ids": [case.id for case in registered_cases()],
        "pilot": args.pilot or args.large_pilot,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "threads": {name: os.environ.get(name) for name in THREADS},
        "sources": {
            str(path.relative_to(ROOT)): digest(path.read_bytes())
            for path in (
                Path(__file__),
                ROOT / "experiments/mesh_cone_fit.py",
                ROOT / "experiments/mesh_cylinder_fit.py",
                ROOT / "experiments/fit_initialization.py",
                ROOT / "experiments/fit_solver.py",
                ROOT / "experiments/fit_coordinates.py",
                ROOT / "experiments/support_intent.py",
                ROOT / "experiments/surface_primitives.py",
                ROOT / "experiments/mesh_sphere_fit.py",
                ROOT / "experiments/mesh_cone_plane_fit.py",
                ROOT / "experiments/browser_static/workload_cases.py",
                ROOT / "src/scansor/nonlinear_least_squares.py",
            )
        },
        "caveats": [
            "Private source copies with identical recovery-observer bookkeeping on both variants; baseline numerical call untouched, progressive changes only that call. Production defaults unchanged; native fitting only.",
            "Complete clocks include sampling, all recovery work, full geometry guards, final refinement/assessment and result construction; imports, generation and certificate recomputation excluded.",
            "Stage solver_other_seconds combines linear algebra and all non-callback/non-guard solver overhead; not a pure linear-algebra measurement.",
            "sampling_seconds measures permutation+hash only; sorting, extraction and normalization remain included in complete fit clocks.",
            "Independent certificates recompute the full coordinate frame, all residuals and aligned unscaled gradient; strict scaled stationarity/condition checks use original producer certificates. Original family equivalence and full normalized objective tie tolerances also checked.",
            "Geometry rows counted as full source rows per guard call; initialization and assessment rows are not instrumented, but their cost remains inside complete clocks.",
            "Process lifetime peak RSS includes both variants, fixtures, imports and reporting; cannot attribute it to a variant.",
        ],
        "cases": [],
    }
    for case in cases:
        record = run_case(case)
        report["cases"].append(record)
        print(case.id, record["medians"], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        _ = args.output.write_text(json.dumps(report, allow_nan=False) + "\n")
    report["controls"] = run_controls()
    report["gate"] = evaluate_gate(report["cases"], report["controls"])
    report["process_lifetime_peak_rss_bytes"] = (
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    )
    report["finished_utc"] = datetime.now(UTC).isoformat()
    _ = args.output.write_text(json.dumps(report, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
