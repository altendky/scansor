"""One strict-default native/worker comparison policy, with retained diagnostics.

Only recognized bounded-recovery records have diagnostic fields. The caller keeps
the original complete records; this module never rewrites either input. The CLI
accepts {"cases": [...]} and writes a receipt even when comparison fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

POLICY_VERSION = "bounded-semantic-parity-v1"
ATOL = RTOL = 1e-10
# A nearly planar cylinder patch has aligned condition sensitivity approximately
# proportional to radius squared; the public normal-matrix condition squares it
# again. This is a bounded relative precision allowance, not an admission change
# or a universal sensitivity bound. Only complete source-bound cylinder-v2 graph
# fit owners enable it.
_CYLINDER_CONDITION_RTOL = 4e-10
_PROFILES = {"values", "numeric-fit", "result-map", "graph-snapshot"}
_RECOVERY_KEYS = {
    "policy",
    "binding_sha256",
    "attempts",
    "selected",
    "termination",
    "selected_role",
}
_POLICY_KEYS = {
    "version",
    "max_proposals",
    "max_fresh_attempts",
    "attempt",
    "objective_atol",
    "objective_rtol",
}
_CANDIDATE_KEYS = {"id", "parameters", "derivation", "chart", "binding_sha256"}
_ATTEMPT_KEYS = {"candidate", "role", "outcome", "evaluations", "assessment", "solver"}
_ASSESSMENT_KEYS = {
    "objective",
    "valid",
    "rank",
    "condition",
    "termination",
    "iterations",
}
_SCREENED = {
    "screened-invalid-or-stale",
    "screened-duplicate-id",
    "screened-no-fresh-start",
    "fresh-attempt-limit",
}
_FAILURES = {
    "invalid-input",
    "invalid-initial-geometry",
    "numeric-failure",
    "rank-deficient",
    "infeasible-constraints",
    "geometry-trial-exhaustion",
    "constraint-projection-exhaustion",
    "step-stagnation",
    "line-search-stagnation",
    "iteration-limit",
    "evaluation-limit",
}
_SUCCESSES = {"projected-gradient", "constrained-stationarity", "locally-converged"}
_OUTCOMES = _SCREENED | _FAILURES | {"locally-converged", "invalid-result"}
_DIAGNOSTIC_SOLVER_KEYS = {
    "parameters",
    "objective_history",
    "iterations",
    "evaluations",
    "constraint_evaluations",
    "rank",
    "constraint_rank",
    "tangent_dimension",
    "condition",
    "projected_gradient_norm",
    "constraint_violation",
    "termination",
}
_PUBLIC_SOLVER_KEYS = {
    "implementation",
    "termination",
    "iterations",
    "evaluations",
    "constraint_evaluations",
    "rank",
    "constraint_rank",
    "tangent_dimension",
    "condition",
    "objective_history",
    "coordinate_origin",
    "coordinate_length",
    "constraint_violation",
    "projected_gradient_norm",
    "scaled_jacobian_condition",
    "scaled_projected_gradient_infinity_norm",
    "aligned_jacobian_condition",
    "aligned_scaled_gradient_infinity_norm",
    "parameter_scales",
    "residual_scale",
    "scale_units",
}
_METRICS = {
    "axial_rms",
    "projected_radius_rms",
    "normal_tilt_rms",
    "coverage_ratio",
    "covariance_eigenvalues",
    "aligned_gradient_infinity_norm",
    "aligned_scaled_gradient_infinity_norm",
    "standardized_covariance_eigenvalues",
}
_ALGORITHMS = {
    "selected-cylinder-recovery-v1",
    "selected-cylinder-recovery-v2",
    "selected-cone-recovery-v1",
    "selected-cone-recovery-v2",
    "joint-algebraic-shared-sphere-v1",
    "selected-cylinder-group-recovery-v1",
    "selected-revolution-group-recovery-v1",
    "selected-symmetry-group-recovery-v1",
}
_GROUP_ALGORITHMS = {
    "selected-cylinder-group-recovery-v1",
    "selected-revolution-group-recovery-v1",
    "selected-symmetry-group-recovery-v1",
}
_CONE_FIT_FIELDS = {
    "kind",
    "analytic",
    "anchor_reference",
    "support_origin",
    "support_intent",
    "residuals",
    "weighted_rms",
    "condition",
    "normal_sign",
    "solver",
    "initialization",
    "support_classification",
}
_CONE_FRESH_DERIVATIONS = {
    f"{family}-{index}": (
        f"selected {family} axis; residualized center and radial regression"
    )
    for family, count in (
        ("centered-normal", 1),
        ("normal-moment", 3),
        ("position-covariance", 3),
    )
    for index in range(count)
}
_CYLINDER_CONDITION_FIELDS = {
    "scaled_jacobian_condition",
    "aligned_jacobian_condition",
}
_CYLINDER_FIT_FIELDS = _CONE_FIT_FIELDS | {"ids"}
_CYLINDER_INITIALIZATION_FIELDS = {
    "algorithm",
    "chart",
    "origin",
    "length",
    "observations",
    "raw_coverage_ratio",
    "raw_normalized_covariance_eigenvalues",
    "coverage_metric",
    "minimum_coverage_ratio",
    "maximum_aligned_jacobian_condition",
    "maximum_aligned_scaled_gradient",
    "minimum_solve_singular_ratio",
    "chart_slope_bounds",
    "initializers",
    "assessments",
    "recovery",
    "coverage_ratio",
    "axial_rms",
    "standardized_covariance_eigenvalues",
    "aligned_scaled_gradient_infinity_norm",
}
_CYLINDER_SOLVER_FIELDS = {
    "implementation",
    "termination",
    "iterations",
    "evaluations",
    "rank",
    "scaled_jacobian_condition",
    "aligned_jacobian_condition",
    "aligned_scaled_gradient_infinity_norm",
    "scaled_projected_gradient_infinity_norm",
    "parameter_scales",
    "residual_scale",
    "scale_units",
    "coordinate_origin",
    "coordinate_length",
}
_EXACT_FIELDS = {
    "recipe",
    "ids",
    "selected_ids",
    "added_ids",
    "rejected_seed_ids",
    "source_ids",
    "target_ids",
    "memberships",
    "rank",
    "constraint_rank",
    "tangent_dimension",
    "observations",
    "inside_vertices",
    "outside_vertices",
    "source_count",
    "target_count",
    "geometric_rank",
    "vertex_count",
    "selected_count",
    "center_rank",
    "radial_rank",
}
_ENCODED_NONFINITE = {"NaN", "Infinity", "-Infinity"}


def _number(value: Any) -> bool:
    # Float subclasses used by producer records remain JSON numbers. Bool is
    # deliberately excluded despite inheriting from int in Python.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def policy_sha256() -> str:
    """Bind receipts to the exact shared policy source, not an artifact's claim."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class SemanticParityError(AssertionError):
    """Comparison failed; receipt retains the differences observed before failure."""

    def __init__(self, message: str, receipt: dict[str, Any]) -> None:
        self.receipt: dict[str, Any] = receipt
        super().__init__(message)


def _new_receipt() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "policy_sha256": policy_sha256(),
        "complete": False,
        "semantic_complete": False,
        "budgets_validated": False,
        "strict_fields_compared": 0,
        "budget_records_validated": 0,
        "diagnostic_differences": [],
        "diagnostic_difference_count": 0,
    }


class _Comparator:
    def __init__(self) -> None:
        self.receipt: dict[str, Any] = _new_receipt()

    def fail(self, path: str, message: str) -> Any:
        detail = f"{path}: {message}"
        self.receipt["error"] = detail
        raise SemanticParityError(detail, self.receipt)

    def require(self, condition: bool, path: str, message: str) -> None:
        if not condition:
            self.fail(path, message)

    def keys(self, value: Any, required: set[str], path: str) -> None:
        self.require(isinstance(value, dict), path, "expected an object")
        self.require(set(value) == required, path, "unexpected or missing schema keys")

    def finite(self, value: Any, path: str, *, nonnegative: bool = False) -> None:
        valid = _number(value)
        try:
            valid = valid and math.isfinite(value)
        except OverflowError:
            valid = False
        self.require(valid, path, "expected a finite JSON number")
        if nonnegative:
            self.require(value >= 0, path, "expected a nonnegative number")

    def count(self, value: Any, maximum: int, path: str) -> None:
        self.finite(value, path, nonnegative=True)
        self.require(
            value == int(value) and value <= maximum,
            path,
            "invalid finite count/budget",
        )

    def diagnostic(self, actual: Any, expected: Any, path: str, reason: str) -> None:
        # JSON equality deliberately distinguishes bool from numeric values.
        if json.dumps(actual, sort_keys=True, allow_nan=False) == json.dumps(
            expected, sort_keys=True, allow_nan=False
        ):
            return
        if (
            isinstance(actual, dict)
            and isinstance(expected, dict)
            and actual.keys() == expected.keys()
        ):
            for key in sorted(expected):
                self.diagnostic(actual[key], expected[key], path + "." + key, reason)
            return
        if (
            isinstance(actual, (list, tuple))
            and isinstance(expected, (list, tuple))
            and len(actual) == len(expected)
        ):
            for index, (value, reference) in enumerate(
                zip(actual, expected, strict=True)
            ):
                self.diagnostic(value, reference, f"{path}[{index}]", reason)
            return
        self.receipt["diagnostic_differences"].append(
            {
                "path": path,
                "actual": deepcopy(actual),
                "expected": deepcopy(expected),
                "reason": reason,
            }
        )
        self.receipt["diagnostic_difference_count"] += 1

    def exact(self, actual: Any, expected: Any, path: str) -> None:
        if _number(expected):
            self.finite(actual, path)
            self.finite(expected, path)
            self.require(actual == expected, path, "exact numeric intent differs")
        elif isinstance(expected, dict):
            self.keys(actual, set(expected), path)
            for key in expected:
                self.exact(actual[key], expected[key], f"{path}.{key}")
        elif isinstance(expected, (list, tuple)):
            self.require(isinstance(actual, (list, tuple)), path, "expected an array")
            self.require(len(actual) == len(expected), path, "array length differs")
            for index, value in enumerate(expected):
                self.exact(actual[index], value, f"{path}[{index}]")
        else:
            self.require(
                type(actual) is type(expected) and actual == expected,
                path,
                "exact value differs",
            )
        self.receipt["strict_fields_compared"] += 1

    def plain(self, actual: Any, expected: Any, path: str) -> None:
        if _number(expected):
            self.finite(actual, path)
            self.finite(expected, path)
            self.require(
                abs(actual - expected) <= ATOL + RTOL * abs(expected),
                path,
                "numeric value differs",
            )
            self.receipt["strict_fields_compared"] += 1
        elif isinstance(expected, dict):
            self.keys(actual, set(expected), path)
            for key in expected:
                if key in _EXACT_FIELDS:
                    self.exact(actual[key], expected[key], f"{path}.{key}")
                else:
                    self.walk(actual[key], expected[key], f"{path}.{key}")
        elif isinstance(expected, (list, tuple)):
            self.require(isinstance(actual, (list, tuple)), path, "expected an array")
            self.require(len(actual) == len(expected), path, "array length differs")
            for index, value in enumerate(expected):
                self.walk(actual[index], value, f"{path}[{index}]")
        else:
            self.exact(actual, expected, path)

    def validate_diagnostic(self, value: Any, path: str) -> None:
        """The existing recovery encoder spells nonfinite diagnostic numbers."""
        if _number(value):
            self.finite(value, path)
        elif isinstance(value, dict):
            for key, item in value.items():
                self.require(isinstance(key, str), path, "nonstring JSON key")
                self.validate_diagnostic(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                self.validate_diagnostic(item, f"{path}[{index}]")
        else:
            self.require(
                value is None or type(value) in (str, bool),
                path,
                "unsupported diagnostic value",
            )

    def cylinder_condition(
        self, actual: Any, expected: Any, path: str, *, squared: bool = False
    ) -> None:
        """Compare selected certificates; these remain bounded strict fields."""
        for value in (actual, expected):
            self.finite(value, path)
            self.require(
                1 <= value <= (1e12 if squared else 1e6),
                path,
                "selected cylinder condition failed admission",
            )
        self.require(
            abs(actual - expected)
            <= _CYLINDER_CONDITION_RTOL * max(abs(actual), abs(expected)),
            path,
            "selected cylinder condition differs",
        )
        self.receipt["strict_fields_compared"] += 1
        self.diagnostic(
            actual,
            expected,
            path,
            "selected cylinder condition within bounded relative precision",
        )

    def validate_solver(
        self,
        solver: Any,
        limits: dict[str, Any],
        total: int | float,
        path: str,
        *,
        public: bool = False,
    ) -> None:
        self.require(isinstance(solver, dict), path, "expected solver object")
        allowed = _PUBLIC_SOLVER_KEYS if public else _DIAGNOSTIC_SOLVER_KEYS
        required = {"termination", "iterations", "evaluations", "rank"}
        if public:
            required |= {"coordinate_origin", "coordinate_length"}
            if "constraint_evaluations" in solver:
                required |= {
                    "condition",
                    "objective_history",
                    "projected_gradient_norm",
                    "constraint_rank",
                    "tangent_dimension",
                    "constraint_violation",
                }
            else:
                required |= {
                    "implementation",
                    "scaled_jacobian_condition",
                    "scaled_projected_gradient_infinity_norm",
                }
                self.require(
                    solver.get("implementation") == "scaled-svd-least-squares-v1",
                    path,
                    "unsupported bounded solver implementation",
                )
        if not public:
            required |= {
                "parameters",
                "objective_history",
                "condition",
                "projected_gradient_norm",
            }
        self.require(
            required <= set(solver) <= allowed, path, "unknown/missing solver fields"
        )
        self.count(solver["iterations"], limits["max_iterations"], path + ".iterations")
        self.count(
            solver["evaluations"], limits["max_evaluations"], path + ".evaluations"
        )
        self.require(
            isinstance(solver["termination"], str)
            and solver["termination"] in _FAILURES | _SUCCESSES,
            path,
            "unknown solver termination",
        )
        callbacks = solver["evaluations"]
        if "constraint_evaluations" in solver:
            self.count(
                solver["constraint_evaluations"],
                limits["max_evaluations"],
                path + ".constraint_evaluations",
            )
            callbacks += solver["constraint_evaluations"]
            self.require(
                {"constraint_rank", "tangent_dimension", "constraint_violation"}
                <= set(solver),
                path,
                "incomplete constrained solver",
            )
        # Direct families charge each observation. The component additionally
        # charges its mandatory final equality certificate after the solver.
        offset = 1 if public and "constraint_evaluations" in solver else 0
        self.require(
            callbacks + offset == total,
            path,
            "solver/attempt callback accounting differs",
        )
        for key in ("rank", "constraint_rank", "tangent_dimension"):
            if key in solver:
                self.count(solver[key], 2**53 - 1, path + "." + key)
        if "objective_history" in solver:
            history = solver["objective_history"]
            self.require(
                isinstance(history, (list, tuple)), path, "invalid objective history"
            )
            self.require(
                len(history) <= solver["iterations"] + 1,
                path,
                "history exceeds accepted iterations",
            )
            for index, value in enumerate(history):
                self.finite(
                    value, f"{path}.objective_history[{index}]", nonnegative=True
                )
        if public:
            # Successful published certificates cannot contain encoded Infinity.
            for key, value in solver.items():
                if key not in {"implementation", "termination", "scale_units"}:
                    self.finite_tree(value, path + "." + key)
            self.require(
                solver["coordinate_length"] > 0,
                path,
                "invalid normalized coordinate length",
            )
            self.require(
                isinstance(solver["coordinate_origin"], (list, tuple))
                and len(solver["coordinate_origin"]) == 3,
                path,
                "invalid normalized coordinate origin",
            )
            if "constraint_evaluations" in solver:
                self.require(
                    solver["constraint_violation"] <= 1e-11
                    and solver["constraint_violation"] >= 0,
                    path,
                    "failed constrained feasibility certificate",
                )
                self.require(
                    0 <= solver["projected_gradient_norm"] <= 1e-9,
                    path,
                    "failed constrained stationarity certificate",
                )
                self.require(
                    solver["rank"] == solver["tangent_dimension"],
                    path,
                    "selected tangent rank deficient",
                )
            if "aligned_scaled_gradient_infinity_norm" in solver:
                self.require(
                    0 <= solver["aligned_scaled_gradient_infinity_norm"] <= 1e-10,
                    path,
                    "failed fitted-scale stationarity certificate",
                )
        else:
            self.validate_diagnostic(solver, path)
            self.require(
                isinstance(solver["parameters"], (list, tuple)),
                path,
                "invalid failed parameter trace",
            )
            for index, parameter in enumerate(solver["parameters"]):
                self.diagnostic_number(parameter, f"{path}.parameters[{index}]")
            for key in ("condition", "projected_gradient_norm", "constraint_violation"):
                if key in solver:
                    self.diagnostic_number(solver[key], path + "." + key)

    def finite_tree(self, value: Any, path: str) -> None:
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                self.finite_tree(item, f"{path}[{index}]")
        else:
            self.finite(value, path)

    def diagnostic_number(self, value: Any, path: str) -> None:
        if isinstance(value, str):
            self.require(
                value in _ENCODED_NONFINITE, path, "invalid encoded diagnostic number"
            )
        else:
            self.finite(value, path)

    def validate_recovery(
        self, record: Any, path: str, *, candidate_arity: int | None = None
    ) -> dict[str, Any]:
        self.keys(record, _RECOVERY_KEYS, path)
        policy = record["policy"]
        self.keys(policy, _POLICY_KEYS, path + ".policy")
        self.require(
            policy["version"] == "selected-data-recovery-v2",
            path,
            "unsupported recovery policy version",
        )
        for key, cap in (("max_proposals", 16), ("max_fresh_attempts", 8)):
            self.count(policy[key], cap, path + ".policy." + key)
            self.require(policy[key] > 0, path, "empty recovery budget")
        limits = policy["attempt"]
        self.keys(
            limits,
            {"max_iterations", "max_trials", "max_evaluations"},
            path + ".policy.attempt",
        )
        for key, cap in (
            ("max_iterations", 100),
            ("max_trials", 16),
            ("max_evaluations", 512),
        ):
            self.count(limits[key], cap, path + ".policy.attempt." + key)
            self.require(
                limits[key] > 0 or key == "max_iterations",
                path,
                "empty callback/trial budget",
            )
        for key in ("objective_atol", "objective_rtol"):
            self.finite(policy[key], path + ".policy." + key, nonnegative=True)
            self.require(
                policy[key] == {"objective_atol": 1e-12, "objective_rtol": 1e-9}[key],
                path,
                "unsupported objective tie tolerance",
            )
        binding = record["binding_sha256"]
        self.require(
            isinstance(binding, str)
            and len(binding) == 64
            and all(c in "0123456789abcdef" for c in binding),
            path,
            "invalid recovery binding",
        )
        attempts = record["attempts"]
        self.require(
            isinstance(attempts, (list, tuple)), path, "attempts must be an array"
        )
        fresh = launched = hints = 0
        seen: set[str] = set()
        selected: dict[str, Any] | None = None
        for index, attempt in enumerate(attempts):
            location = f"{path}.attempts[{index}]"
            self.keys(attempt, _ATTEMPT_KEYS, location)
            candidate = attempt["candidate"]
            self.keys(candidate, _CANDIDATE_KEYS, location + ".candidate")
            self.require(
                isinstance(attempt["role"], str)
                and attempt["role"] in {"fresh", "hint"},
                location,
                "unknown candidate role",
            )
            self.require(
                isinstance(attempt["outcome"], str) and attempt["outcome"] in _OUTCOMES,
                location,
                "unknown attempt outcome",
            )
            self.count(
                attempt["evaluations"],
                limits["max_evaluations"],
                location + ".evaluations",
            )
            for key in ("id", "derivation", "chart", "binding_sha256"):
                self.require(
                    isinstance(candidate[key], str), location, "invalid candidate text"
                )
            self.require(
                isinstance(candidate["parameters"], (list, tuple)),
                location,
                "invalid proposed parameter array",
            )
            role, outcome = attempt["role"], attempt["outcome"]
            if role == "fresh":
                fresh += 1
                self.require(hints == 0, location, "fresh proposal after hint")
            else:
                hints += 1
            if outcome in _SCREENED:
                self.require(
                    attempt["evaluations"] == 0
                    and attempt["assessment"] is None
                    and attempt["solver"] is None,
                    location,
                    "screened proposal contains executed trace",
                )
                self.validate_diagnostic(
                    candidate["parameters"], location + ".candidate.parameters"
                )
                for parameter in candidate["parameters"]:
                    self.diagnostic_number(
                        parameter, location + ".candidate.parameters"
                    )
                if outcome == "screened-duplicate-id":
                    self.require(
                        role == "fresh" and candidate["id"] in seen,
                        location,
                        "invalid duplicate screening",
                    )
                if outcome == "fresh-attempt-limit":
                    self.require(
                        role == "fresh" and launched == policy["max_fresh_attempts"],
                        location,
                        "invalid fresh-budget screening",
                    )
                if outcome == "screened-no-fresh-start":
                    self.require(
                        role == "hint" and launched == 0,
                        location,
                        "invalid hint screening",
                    )
            else:
                if candidate_arity is not None:
                    self.require(
                        len(candidate["parameters"]) == candidate_arity,
                        location + ".candidate.parameters",
                        "invalid executed cone parameter count",
                    )
                    for parameter in candidate["parameters"]:
                        self.finite(parameter, location + ".candidate.parameters")
                self.require(
                    bool(
                        candidate["id"]
                        and candidate["chart"]
                        and candidate["derivation"]
                    ),
                    location,
                    "empty executed candidate identity",
                )
                self.require(
                    candidate["binding_sha256"] == binding
                    and bool(candidate["parameters"]),
                    location,
                    "executed stale/empty candidate",
                )
                self.finite_tree(
                    candidate["parameters"], location + ".candidate.parameters"
                )
                if role == "fresh":
                    self.require(
                        candidate["id"] not in seen,
                        location,
                        "duplicate executed candidate",
                    )
                    seen.add(candidate["id"])
                    launched += 1
                else:
                    self.require(launched > 0, location, "hint consumed fresh start")
                assessment = attempt["assessment"]
                if outcome in {"locally-converged", "invalid-result"}:
                    self.keys(assessment, _ASSESSMENT_KEYS, location + ".assessment")
                    self.require(
                        type(assessment["valid"]) is bool,
                        location,
                        "invalid assessment validity",
                    )
                    self.require(
                        attempt["solver"] is None,
                        location,
                        "assessed result has a failure solver",
                    )
                    if outcome == "locally-converged":
                        self.require(
                            assessment["valid"] is True,
                            location,
                            "invalid successful assessment",
                        )
                        self.finite(
                            assessment["objective"],
                            location + ".assessment.objective",
                            nonnegative=True,
                        )
                        if assessment["condition"] is not None:
                            self.finite(
                                assessment["condition"],
                                location + ".assessment.condition",
                                nonnegative=True,
                            )
                        self.require(
                            isinstance(assessment["termination"], str)
                            and assessment["termination"] in _SUCCESSES,
                            location,
                            "unsuccessful assessment termination",
                        )
                    else:
                        self.validate_diagnostic(assessment, location + ".assessment")
                        self.diagnostic_number(
                            assessment["objective"], location + ".assessment.objective"
                        )
                        if assessment["condition"] is not None:
                            self.diagnostic_number(
                                assessment["condition"],
                                location + ".assessment.condition",
                            )
                    if assessment["iterations"] is not None:
                        self.count(
                            assessment["iterations"],
                            limits["max_iterations"],
                            location + ".assessment.iterations",
                        )
                    if assessment["rank"] is not None:
                        self.count(
                            assessment["rank"], 2**53 - 1, location + ".assessment.rank"
                        )
                else:
                    self.require(
                        assessment is None,
                        location,
                        "failure has a successful assessment",
                    )
                    if attempt["solver"] is not None:
                        self.validate_solver(
                            attempt["solver"],
                            limits,
                            attempt["evaluations"],
                            location + ".solver",
                        )
                        self.require(
                            attempt["solver"]["termination"] == outcome,
                            location,
                            "failure outcome differs from its solver trace",
                        )
                    if outcome == "evaluation-limit":
                        self.require(
                            attempt["evaluations"] == limits["max_evaluations"]
                            and attempt["solver"] is None,
                            location,
                            "evaluation limit without exhausting quota",
                        )
            if (
                candidate["id"] == record["selected"]
                and role == record["selected_role"]
                and outcome == "locally-converged"
            ):
                self.require(selected is None, location, "nonunique selected attempt")
                selected = attempt
        self.require(
            fresh <= policy["max_proposals"]
            and launched <= policy["max_fresh_attempts"]
            and hints <= 1,
            path,
            "recovery exceeded finite proposal/attempt budgets",
        )
        if record["selected"] is None:
            self.require(
                record["selected_role"] is None
                and record["termination"]
                in {
                    "ambiguous",
                    "no-valid-candidates",
                    "rank-deficient",
                    "search-exhausted",
                },
                path,
                "invalid failed recovery classification",
            )
            successes = [
                item for item in attempts if item["outcome"] == "locally-converged"
            ]
            if record["termination"] == "ambiguous":
                self.require(
                    len(successes) >= 2,
                    path,
                    "ambiguity without competing successful attempts",
                )
            else:
                self.require(
                    not successes, path, "failed recovery discarded a valid solution"
                )
                outcomes = {
                    item["outcome"]
                    for item in attempts
                    if item["role"] == "fresh"
                    and not item["outcome"].startswith("screened-")
                }
                classification = (
                    "no-valid-candidates"
                    if not outcomes
                    else "rank-deficient"
                    if outcomes == {"rank-deficient"}
                    else "search-exhausted"
                )
                self.require(
                    record["termination"] == classification,
                    path,
                    "failure classification inconsistent with attempts",
                )
        else:
            self.require(
                record["termination"] == "best-attempted-local-solution"
                and selected is not None
                and selected["outcome"] == "locally-converged",
                path,
                "invalid selected recovery",
            )
            assert selected is not None
            successes = [
                item for item in attempts if item["outcome"] == "locally-converged"
            ]
            minimum = min(item["assessment"]["objective"] for item in successes)
            first_tied = next(
                item
                for item in successes
                if math.isclose(
                    item["assessment"]["objective"],
                    minimum,
                    abs_tol=policy["objective_atol"],
                    rel_tol=policy["objective_rtol"],
                )
            )
            self.require(
                selected is first_tied,
                path,
                "selection violates immutable-minimum/stable-order policy",
            )
        self.receipt["budget_records_validated"] += 1
        return {"record": record, "selected": selected, "limits": limits}

    def recovery(
        self,
        actual: Any,
        expected: Any,
        path: str,
        *,
        candidate_arity: int | None = None,
        cylinder_condition: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        left = self.validate_recovery(
            actual, path + "(actual)", candidate_arity=candidate_arity
        )
        right = self.validate_recovery(
            expected, path + "(expected)", candidate_arity=candidate_arity
        )
        for key in _RECOVERY_KEYS - {"attempts"}:
            self.exact(actual[key], expected[key], path + "." + key)
        self.require(
            len(actual["attempts"]) == len(expected["attempts"]),
            path,
            "proposal count differs",
        )
        for index, (value, reference) in enumerate(
            zip(actual["attempts"], expected["attempts"], strict=True)
        ):
            location = f"{path}.attempts[{index}]"
            for key in _CANDIDATE_KEYS - {"parameters"}:
                self.exact(
                    value["candidate"][key],
                    reference["candidate"][key],
                    location + ".candidate." + key,
                )
            self.exact(value["role"], reference["role"], location + ".role")
            selected = reference is right["selected"]
            screened = (
                value["outcome"] in _SCREENED or reference["outcome"] in _SCREENED
            )
            computed = (
                candidate_arity is not None
                and not screened
                and value is not left["selected"]
                and reference is not right["selected"]
                and value["role"] == reference["role"] == "fresh"
                and all(
                    candidate["id"] in _CONE_FRESH_DERIVATIONS
                    and candidate["chart"] == candidate["id"]
                    and candidate["derivation"]
                    == _CONE_FRESH_DERIVATIONS[candidate["id"]]
                    for candidate in (value["candidate"], reference["candidate"])
                )
            )
            parameters_path = location + ".candidate.parameters"
            if computed:
                self.diagnostic(
                    value["candidate"]["parameters"],
                    reference["candidate"]["parameters"],
                    parameters_path,
                    "discarded computed cone-v2 selected-data initializer",
                )
            else:
                self.plain(
                    value["candidate"]["parameters"],
                    reference["candidate"]["parameters"],
                    parameters_path,
                )
            if screened or selected:
                self.exact(
                    value["outcome"], reference["outcome"], location + ".outcome"
                )
            else:
                self.diagnostic(
                    value["outcome"],
                    reference["outcome"],
                    location + ".outcome",
                    "discarded attempt outcome",
                )
            self.diagnostic(
                value["evaluations"],
                reference["evaluations"],
                location + ".evaluations",
                "realized callbacks within unchanged quota",
            )
            if selected:
                self.keys(
                    value["assessment"],
                    set(reference["assessment"]),
                    location + ".assessment",
                )
                for key in reference["assessment"]:
                    if key == "iterations":
                        self.diagnostic(
                            value["assessment"][key],
                            reference["assessment"][key],
                            location + ".assessment." + key,
                            "realized iterations within unchanged quota",
                        )
                    elif key == "condition" and cylinder_condition:
                        self.cylinder_condition(
                            value["assessment"][key],
                            reference["assessment"][key],
                            location + ".assessment." + key,
                        )
                    else:
                        self.plain(
                            value["assessment"][key],
                            reference["assessment"][key],
                            location + ".assessment." + key,
                        )
                self.exact(value["solver"], reference["solver"], location + ".solver")
            elif screened:
                self.exact(
                    value["assessment"],
                    reference["assessment"],
                    location + ".assessment",
                )
                self.exact(value["solver"], reference["solver"], location + ".solver")
            else:
                for key in ("assessment", "solver"):
                    self.diagnostic(
                        value[key],
                        reference[key],
                        location + "." + key,
                        "discarded numerical trace; validated schema and finite budget",
                    )
        return left, right

    def selected_solver(
        self,
        actual: Any,
        expected: Any,
        path: str,
        context: tuple[dict[str, Any], dict[str, Any]],
        *,
        cylinder_condition: bool = False,
    ) -> None:
        for value, info in zip((actual, expected), context, strict=True):
            selected = info["selected"]
            self.require(
                selected is not None, path, "public solver without selected attempt"
            )
            self.validate_solver(
                value, info["limits"], selected["evaluations"], path, public=True
            )
            self.require(
                value["evaluations"] >= 1,
                path,
                "selected solver has no observation callback",
            )
            self.require(
                value["rank"] == selected["assessment"]["rank"],
                path,
                "public rank differs from selected assessment",
            )
            self.require(
                value["termination"] == selected["assessment"]["termination"],
                path,
                "public termination differs from selected assessment",
            )
            self.require(
                value["iterations"] == selected["assessment"]["iterations"],
                path,
                "public iterations differ from selected assessment",
            )
            if "constraint_evaluations" not in value:
                self.require(
                    value["rank"] == len(selected["candidate"]["parameters"]),
                    path,
                    "selected direct solve rank deficient",
                )
            if cylinder_condition:
                self.require(
                    value["rank"] == 5
                    and math.isclose(
                        value["aligned_jacobian_condition"],
                        selected["assessment"]["condition"],
                        rel_tol=32 * math.ulp(1.0),
                        abs_tol=0,
                    ),
                    path,
                    "inconsistent selected cylinder aligned certificate",
                )
                self.require(
                    # The strict singular-value test can round its reported
                    # quotient to exactly 1e6; rank and threshold stay fixed.
                    value["scaled_jacobian_condition"] <= 1e6,
                    path,
                    "selected cylinder scaled solve failed rank admission",
                )
        self.keys(actual, set(expected), path)
        for key in expected:
            if key in {"iterations", "evaluations", "constraint_evaluations"}:
                self.diagnostic(
                    actual[key],
                    expected[key],
                    path + "." + key,
                    "realized solver count within unchanged quota",
                )
            elif key == "objective_history":
                self.history(actual[key], expected[key], path + "." + key)
            elif cylinder_condition and key in _CYLINDER_CONDITION_FIELDS:
                self.cylinder_condition(actual[key], expected[key], path + "." + key)
            else:
                self.plain(actual[key], expected[key], path + "." + key)

    def history(self, actual: Any, expected: Any, path: str) -> None:
        for value in (actual, expected):
            self.require(
                isinstance(value, (list, tuple)) and bool(value),
                path,
                "missing successful objective history",
            )
            for item in value:
                self.finite(item, path, nonnegative=True)
        self.plain(actual[-1], expected[-1], path + "[-1]")
        self.diagnostic(
            actual,
            expected,
            path,
            "intermediate objective path; final objective compared",
        )

    def initializer_tree(self, actual: Any, expected: Any, path: str) -> None:
        self.keys(actual, set(expected), path)
        condition_keys = {
            "center_condition",
            "condition",
            "equilibrated_condition",
        } & set(expected)
        if condition_keys:
            if "center_condition" in expected:
                self.require(
                    set(expected) == {"center_rank", "radial_rank", "center_condition"},
                    path,
                    "unknown center initializer schema",
                )
            else:
                self.require(
                    {"rank", "condition"}
                    <= set(expected)
                    <= {
                        "rank",
                        "condition",
                        "transverse_scale",
                        "equilibrated_condition",
                    },
                    path,
                    "unknown circle initializer schema",
                )
        for key in expected:
            if key in condition_keys:
                for value in (actual[key], expected[key]):
                    if value is not None:
                        self.finite(value, path + "." + key, nonnegative=True)
                self.diagnostic(
                    actual[key],
                    expected[key],
                    path + "." + key,
                    "discarded algebraic initializer condition",
                )
            elif isinstance(expected[key], dict):
                self.initializer_tree(actual[key], expected[key], path + "." + key)
            else:
                self.plain(actual[key], expected[key], path + "." + key)

    def initialization(
        self,
        actual: Any,
        expected: Any,
        path: str,
        *,
        candidate_arity: int | None = None,
        cylinder_condition: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.keys(actual, set(expected), path)
        context = self.recovery(
            actual["recovery"],
            expected["recovery"],
            path + ".recovery",
            candidate_arity=candidate_arity,
            cylinder_condition=cylinder_condition,
        )
        for value, info in zip((actual, expected), context, strict=True):
            self.validate_initialization(value, info, path)
        selected = context[1]["selected"]
        selected_ids = (
            set()
            if selected is None
            else {selected["candidate"]["id"], selected["candidate"]["chart"]}
        )
        for key in expected:
            if key == "recovery":
                continue
            value, reference = actual[key], expected[key]
            if key == "assessments":
                self.fitted_assessments(value, reference, path + "." + key, context)
            elif key == "initializers":
                self.keys(value, set(reference), path + "." + key)
                candidates = {
                    item["candidate"][field]
                    for item in context[1]["record"]["attempts"]
                    for field in ("id", "chart")
                }
                for candidate in reference:
                    self.require(
                        candidate in candidates,
                        path + "." + key,
                        "diagnostic for unknown candidate",
                    )
                    location = f"{path}.{key}.{candidate}"
                    if candidate in selected_ids:
                        self.walk(value[candidate], reference[candidate], location)
                    else:
                        self.initializer_tree(
                            value[candidate], reference[candidate], location
                        )
            elif key == "attempt_failures":
                self.discarded_failures(value, reference, path + "." + key, context)
            elif key in _EXACT_FIELDS:
                self.exact(value, reference, path + "." + key)
            else:
                self.walk(value, reference, path + "." + key)
        return context

    def assessment_attempt(
        self, key: str, info: dict[str, Any], path: str
    ) -> dict[str, Any]:
        matches = [
            attempt
            for attempt in info["record"]["attempts"]
            if key in (attempt["candidate"]["id"], attempt["candidate"]["chart"])
        ]
        self.require(bool(matches), path, "diagnostic for unknown candidate")
        self.require(
            len(matches) == 1, path, "ambiguous fitted assessment candidate alias"
        )
        return matches[0]

    def fitted_assessments(
        self,
        actual: Any,
        expected: Any,
        path: str,
        context: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        self.require(
            isinstance(actual, dict) and isinstance(expected, dict),
            path,
            "invalid fitted assessment map",
        )
        actual = cast(dict[str, Any], actual)
        expected = cast(dict[str, Any], expected)
        keys = actual.keys() | expected.keys()
        by_side = [
            {key: self.assessment_attempt(key, info, path + "." + key) for key in keys}
            for info in context
        ]
        for side, candidates, info in zip(
            (actual, expected), by_side, context, strict=True
        ):
            self.require(
                info["selected"] is None
                or any(candidates[key] is info["selected"] for key in side),
                path,
                "selected fitted assessment missing",
            )
            for candidate, metrics in side.items():
                self.require(
                    isinstance(metrics, dict) and set(metrics) <= _METRICS,
                    path + "." + candidate,
                    "unknown fitted assessment metric",
                )
                self.validate_diagnostic(metrics, path + "." + candidate)
        for candidate in sorted(keys):
            location = path + "." + candidate
            if by_side[1][candidate] is context[1]["selected"]:
                self.require(
                    candidate in actual and candidate in expected,
                    location,
                    "selected fitted assessment missing",
                )
                self.walk(actual[candidate], expected[candidate], location)
            else:
                if (candidate in actual) != (candidate in expected):
                    self.require(
                        by_side[0][candidate]["outcome"]
                        != by_side[1][candidate]["outcome"],
                        location,
                        "discarded fitted assessment removed without outcome change",
                    )
                else:
                    self.keys(actual[candidate], set(expected[candidate]), location)
                self.diagnostic(
                    actual.get(candidate),
                    expected.get(candidate),
                    location,
                    "discarded fitted assessment metrics; presence follows differing outcomes",
                )

    def validate_initialization(
        self, value: Any, info: dict[str, Any], path: str
    ) -> None:
        algorithm = value.get("algorithm")
        if algorithm is not None:
            self.require(
                isinstance(algorithm, str) and algorithm in _ALGORITHMS,
                path,
                "unsupported bounded family algorithm",
            )
        fixed = {
            "minimum_coverage_ratio": 1e-10,
            "maximum_aligned_jacobian_condition": 1e6,
            "maximum_aligned_scaled_gradient": 1e-10,
            "minimum_solve_singular_ratio": 1e-6,
        }
        for key, threshold in fixed.items():
            if key in value:
                self.require(
                    _number(value[key]) and value[key] == threshold,
                    path,
                    "unsupported admission threshold " + key,
                )
        selected = info["selected"]
        if selected is None:
            return
        if (
            "minimum_coverage_ratio" in value
            and value.get("raw_coverage_gate_applied", True) is not False
            and "coverage_ratio" in value
        ):
            self.finite(value["coverage_ratio"], path + ".coverage_ratio")
            self.require(
                value["coverage_ratio"] > value["minimum_coverage_ratio"],
                path,
                "selected coverage failed admission",
            )
        if "maximum_aligned_jacobian_condition" in value:
            condition = selected["assessment"]["condition"]
            self.finite(condition, path + ".selected.condition", nonnegative=True)
            self.require(
                condition <= value["maximum_aligned_jacobian_condition"],
                path,
                "selected aligned condition failed admission",
            )
        assessments = value.get("assessments", {})
        self.require(
            isinstance(assessments, dict), path, "invalid fitted assessment map"
        )
        assessments = cast(dict[str, Any], assessments)
        for key, metrics in assessments.items():
            attempt = self.assessment_attempt(key, info, path + ".assessments." + key)
            if attempt is not selected:
                continue
            self.require(
                isinstance(metrics, dict), path, "invalid selected fitted metrics"
            )
            if "coverage_ratio" in metrics and "minimum_coverage_ratio" in value:
                self.finite(
                    metrics["coverage_ratio"], path + ".selected.coverage_ratio"
                )
                self.require(
                    metrics["coverage_ratio"] > value["minimum_coverage_ratio"],
                    path,
                    "selected fitted coverage failed admission",
                )
            if "aligned_scaled_gradient_infinity_norm" in metrics:
                gradient = metrics["aligned_scaled_gradient_infinity_norm"]
                self.finite(
                    gradient,
                    path + ".selected.aligned_scaled_gradient_infinity_norm",
                    nonnegative=True,
                )
                self.require(
                    gradient <= 1e-10,
                    path,
                    "selected fitted stationarity failed admission",
                )

    def discarded_failures(
        self,
        actual: Any,
        expected: Any,
        path: str,
        context: tuple[dict[str, Any], dict[str, Any]],
    ) -> None:
        self.require(
            isinstance(actual, dict) and isinstance(expected, dict),
            path,
            "invalid attempt failure map",
        )
        by_side = [
            {item["candidate"]["id"]: item for item in info["record"]["attempts"]}
            for info in context
        ]
        actual = cast(dict[str, Any], actual)
        expected = cast(dict[str, Any], expected)
        for key in sorted(actual.keys() | expected.keys()):
            self.require(
                all(key in side for side in by_side),
                path,
                "failure for unknown candidate",
            )
            self.require(
                key != context[1]["record"]["selected"],
                path,
                "selected candidate has failure trace",
            )
            for value in (actual.get(key), expected.get(key)):
                self.validate_diagnostic(value, path + "." + key)
                if value is not None:
                    self.require(
                        isinstance(value, dict)
                        and set(value) <= {"kind", "code", "message", "details"},
                        path,
                        "unknown failure diagnostic schema",
                    )
            if (key in actual) != (key in expected):
                self.require(
                    by_side[0][key]["outcome"] != by_side[1][key]["outcome"],
                    path,
                    "discarded failure trace removed",
                )
            self.diagnostic(
                actual.get(key),
                expected.get(key),
                path + "." + key,
                "discarded typed attempt failure",
            )

    def registration(self, actual: Any, expected: Any, path: str) -> None:
        scores = {
            "best_score",
            "runner_up_score",
            "normal_scale",
            "ambiguity_denominator_floor",
        }
        fields = scores | {
            "format",
            "rotation",
            "translation",
            "rms",
            "normal_error",
            "ambiguity_ratio",
            "geometric_rank",
            "source_count",
            "target_count",
        }
        self.keys(actual, fields, path + "(actual)")
        self.keys(expected, fields, path + "(expected)")
        for value in (actual, expected):
            self.count(value["geometric_rank"], 3, path + ".geometric_rank")
            for key in ("source_count", "target_count"):
                self.count(value[key], 2**53 - 1, path + "." + key)
                self.require(
                    value[key] >= 6, path, "insufficient registration observations"
                )
            self.vector(value["translation"], 3, path + ".translation")
            self.require(
                isinstance(value["rotation"], (list, tuple))
                and len(value["rotation"]) == 3,
                path,
                "invalid registration matrix",
            )
            for row in value["rotation"]:
                self.vector(row, 3, path + ".rotation")
            for key in scores | {"ambiguity_ratio"}:
                self.finite(value[key], path + "." + key, nonnegative=True)
            self.require(
                value["ambiguity_denominator_floor"] == 1e-12
                and value["normal_scale"] > 0,
                path,
                "invalid registration scale/floor",
            )
            self.finite(value["rms"], path + ".rms", nonnegative=True)
            self.finite(value["normal_error"], path + ".normal_error", nonnegative=True)
            best = value["rms"] + value["normal_scale"] * value["normal_error"]
            self.require(
                math.isfinite(best)
                and math.isclose(
                    value["best_score"], best, rel_tol=32 * math.ulp(1.0), abs_tol=0
                ),
                path,
                "best score inconsistent with emitted RMS/normal score",
            )
            self.require(
                value["runner_up_score"] >= value["best_score"],
                path,
                "runner-up precedes best score",
            )
            quotient = value["runner_up_score"] / max(
                value["best_score"], value["ambiguity_denominator_floor"]
            )
            self.require(
                math.isfinite(quotient)
                and math.isclose(
                    value["ambiguity_ratio"],
                    quotient,
                    rel_tol=32 * math.ulp(1.0),
                    abs_tol=0,
                ),
                path,
                "ratio inconsistent with underlying scores",
            )
        for key in expected:
            if key == "ambiguity_ratio":
                self.diagnostic(
                    actual[key],
                    expected[key],
                    path + "." + key,
                    "derived registration ratio; underlying scores compared",
                )
            else:
                self.walk(actual[key], expected[key], path + "." + key)

    def physical_geometry(self, record: dict[str, Any], path: str) -> None:
        """Encoded nonfinite diagnostics are never physical numeric geometry.

        In particular a cone's radius at its datum may be negative. This checks
        representation, not a replacement cone support/admission calculation.
        """
        for key in ("weighted_rms", "cone_weighted_rms", "plane_weighted_rms"):
            if key in record:
                self.finite(record[key], path + "." + key, nonnegative=True)
        for key in ("residuals", "lateral_residuals", "plane_residuals"):
            if key in record:
                self.require(
                    isinstance(record[key], (list, tuple)),
                    path + "." + key,
                    "invalid published residual array",
                )
                for index, residual in enumerate(record[key]):
                    self.finite(residual, f"{path}.{key}[{index}]")
        if (
            "initialization" in record
            and "solver" in record
            and "parameters" in record
            and "analytic" not in record
        ):
            self.require(
                isinstance(record["parameters"], (list, tuple)),
                path,
                "invalid published fitted parameter array",
            )
            for index, parameter in enumerate(record["parameters"]):
                self.finite(parameter, f"{path}.parameters[{index}]")
        if "analytic" in record:
            geometry = record["analytic"]
            self.keys(
                geometry,
                {"kind", "origin", "axis", "radius", "slope", "axial_domain"},
                path + ".analytic",
            )
            self.require(
                isinstance(geometry["kind"], str)
                and geometry["kind"] in {"cylinder", "cone"},
                path,
                "unsupported canonical family",
            )
            for key in ("origin", "axis"):
                self.vector(geometry[key], 3, path + ".analytic." + key)
            for key in ("radius", "slope"):
                self.finite(geometry[key], path + ".analytic." + key)
            self.vector(geometry["axial_domain"], 2, path + ".analytic.axial_domain")
        for key in (
            "axis_display",
            "point_display",
            "plane_point_display",
            "basis_u_display",
            "basis_v_display",
            "normal_display",
            "radial_display",
        ):
            if key in record:
                self.vector(record[key], 3, path + "." + key)
        if "plane_equation" in record:
            self.vector(record["plane_equation"], 4, path + ".plane_equation")
        if (
            isinstance(record.get("kind"), str)
            and record["kind"] in {"plane", "sphere", "cylinder", "cone"}
            and "parameters" in record
            and "analytic" not in record
        ):
            self.require(
                isinstance(record["parameters"], (list, tuple)),
                path,
                "invalid physical parameter array",
            )
            self.finite_tree(record["parameters"], path + ".parameters")

    def vector(self, value: Any, size: int, path: str) -> None:
        self.require(
            isinstance(value, (list, tuple)) and len(value) == size,
            path,
            "invalid physical vector shape",
        )
        self.finite_tree(value, path)

    @staticmethod
    def cylinder_condition_owner(record: dict[str, Any]) -> bool:
        """Recognize a complete source-bound graph fit, never a bare version."""
        geometry = record.get("analytic")
        initialization = record.get("initialization")
        solver = record.get("solver")
        if not (
            isinstance(geometry, dict)
            and isinstance(initialization, dict)
            and isinstance(solver, dict)
        ):
            return False
        geometry = cast(dict[str, Any], geometry)
        initialization = cast(dict[str, Any], initialization)
        solver = cast(dict[str, Any], solver)
        return (
            record.keys() >= _CYLINDER_FIT_FIELDS
            and record.get("kind") == "cylinder"
            and geometry.get("kind") == "cylinder"
            and initialization.keys() >= _CYLINDER_INITIALIZATION_FIELDS
            and initialization["algorithm"] == "selected-cylinder-recovery-v2"
            and initialization["chart"] == "rotated-normalized-cylinder-v1"
            and solver.keys() >= _CYLINDER_SOLVER_FIELDS
            and solver.get("implementation") == "scaled-svd-least-squares-v1"
            and "constraint_evaluations" not in solver
        )

    def cylinder_condition_consistency(
        self, record: dict[str, Any], info: dict[str, Any], path: str
    ) -> None:
        selected = info["selected"]
        self.require(selected is not None, path, "missing selected cylinder attempt")
        assessment = selected["assessment"]
        self.require(
            assessment["rank"] == 5 and assessment["valid"] is True,
            path,
            "selected cylinder failed rank/validity admission",
        )
        initialization = record["initialization"]
        ids = record["ids"]
        self.require(
            isinstance(ids, (list, tuple))
            and bool(ids)
            and all(type(value) is int and value >= 0 for value in ids)
            and len(set(ids)) == len(ids)
            and len(ids) == initialization["observations"] == len(record["residuals"]),
            path,
            "invalid selected cylinder source rows",
        )
        classification = record["support_classification"]
        self.keys(
            classification,
            {"inside_vertices", "outside_vertices", "role"},
            path + ".support_classification",
        )
        for key in ("inside_vertices", "outside_vertices"):
            self.count(
                classification[key], len(ids), path + ".support_classification." + key
            )
        self.require(
            classification["inside_vertices"] + classification["outside_vertices"]
            == len(ids)
            and classification["role"] == "selection-and-display",
            path,
            "invalid selected cylinder support classifications",
        )
        for key, metrics in initialization["assessments"].items():
            if self.assessment_attempt(key, info, path) is selected:
                self.require(
                    metrics.keys()
                    >= {
                        "coverage_ratio",
                        "axial_rms",
                        "standardized_covariance_eigenvalues",
                        "aligned_scaled_gradient_infinity_norm",
                    },
                    path,
                    "incomplete selected cylinder fitted admission metrics",
                )
        self.finite(
            initialization["aligned_scaled_gradient_infinity_norm"],
            path + ".initialization.aligned_scaled_gradient_infinity_norm",
            nonnegative=True,
        )
        self.require(
            initialization["aligned_scaled_gradient_infinity_norm"] <= 1e-10,
            path,
            "selected cylinder initialization stationarity failed admission",
        )
        self.finite(record["condition"], path + ".condition")
        self.require(
            math.isclose(
                record["condition"],
                assessment["condition"] ** 2,
                rel_tol=32 * math.ulp(1.0),
                abs_tol=0,
            ),
            path,
            "inconsistent selected cylinder squared certificate",
        )

    @staticmethod
    def computed_cone_owner(record: dict[str, Any]) -> bool:
        """Recognize the complete fixed-arity producer, never a bare version tag."""
        geometry = record.get("analytic")
        initialization = record.get("initialization")
        solver = record.get("solver")
        if not (
            isinstance(geometry, dict)
            and isinstance(initialization, dict)
            and isinstance(solver, dict)
        ):
            return False
        geometry = cast(dict[str, Any], geometry)
        initialization = cast(dict[str, Any], initialization)
        solver = cast(dict[str, Any], solver)
        return (
            record.keys() >= _CONE_FIT_FIELDS
            and record.get("kind") == "cone"
            and geometry.get("kind") == "cone"
            and initialization.get("algorithm") == "selected-cone-recovery-v2"
            and initialization.get("chart") == "rotated-normalized-cone-v1"
            and solver.get("implementation") == "scaled-svd-least-squares-v1"
            and "constraint_evaluations" not in solver
        )

    def walk(self, actual: Any, expected: Any, path: str) -> None:
        if isinstance(expected, dict):
            self.require(isinstance(actual, dict), path, "expected an object")
            expected = cast(dict[str, Any], expected)
            self.physical_geometry(actual, path + "(actual)")
            self.physical_geometry(expected, path + "(expected)")
            for record in (actual, expected):
                algorithm = record.get("algorithm")
                if (
                    isinstance(algorithm, str)
                    and algorithm in _ALGORITHMS
                    and "recovery" not in record
                ):
                    # These producers may fail preflight before entering recovery.
                    # Such typed error records are still compared in full; they
                    # must not make a successful fit's missing provenance valid.
                    preflight = (
                        isinstance(record.get("kind"), str)
                        and isinstance(record.get("code"), str)
                        and record.get("kind")
                        in {
                            "sphere_fit_failure",
                            "cylinder_fit_failure",
                            "cone_fit_failure",
                            "symmetry_fit_failure",
                        }
                        and record.get("code")
                        in {
                            "rank-deficient",
                            "insufficient-coverage",
                            "insufficient-observations",
                            "invalid-input",
                            "invalid-normals",
                            "invalid-support",
                            "unsupported-support",
                        }
                    )
                    self.require(
                        preflight,
                        path,
                        "known bounded algorithm missing recovery envelope",
                    )
            if (
                "attempts" in expected and "policy" in expected
            ) or "selected_role" in expected:
                _ = self.recovery(actual, expected, path)
                return
            if "recovery" in expected:
                _ = self.initialization(actual, expected, path)
                return
            initialization = expected.get("initialization")
            if isinstance(initialization, dict) and "recovery" in initialization:
                self.keys(actual, set(expected), path)
                initialization = cast(dict[str, Any], initialization)
                cylinder_condition = self.cylinder_condition_owner(
                    actual
                ) and self.cylinder_condition_owner(expected)
                algorithm = initialization.get("algorithm")
                if isinstance(algorithm, str) and algorithm in _ALGORITHMS:
                    group_holder = algorithm in _GROUP_ALGORITHMS and (
                        (
                            isinstance(expected.get("kind"), str)
                            and expected.get("kind")
                            in {"plane", "sphere", "cylinder", "cone"}
                        )
                        or "fit" in expected
                    )
                    self.require(
                        "solver" in expected or group_holder,
                        path,
                        "successful bounded fit missing public solver certificate",
                    )
                context = self.initialization(
                    actual["initialization"],
                    initialization,
                    path + ".initialization",
                    # cone_residual_jacobian owns exactly cx,cy,a,b,R,k.
                    candidate_arity=6
                    if self.computed_cone_owner(actual)
                    and self.computed_cone_owner(expected)
                    else None,
                    cylinder_condition=cylinder_condition,
                )
                if cylinder_condition:
                    for value, info in zip((actual, expected), context, strict=True):
                        self.cylinder_condition_consistency(value, info, path)
                for key in expected:
                    if key == "initialization":
                        continue
                    if key == "solver":
                        self.selected_solver(
                            actual[key],
                            expected[key],
                            path + ".solver",
                            context,
                            cylinder_condition=cylinder_condition,
                        )
                        for value, init in (
                            (actual[key], actual["initialization"]),
                            (expected[key], initialization),
                        ):
                            init = cast(dict[str, Any], init)
                            algorithm = init.get("algorithm")
                            if "constraint_evaluations" in value:
                                if algorithm == "selected-symmetry-group-recovery-v1":
                                    self.require(
                                        value["projected_gradient_norm"] <= 1e-10,
                                        path,
                                        "selected symmetry stationarity failed",
                                    )
                            elif algorithm != "selected-cylinder-recovery-v1":
                                self.require(
                                    0
                                    <= value["scaled_projected_gradient_infinity_norm"]
                                    <= 1e-10,
                                    path,
                                    "selected numerical stationarity failed",
                                )
                    elif key == "objective_history":
                        self.history(
                            actual[key], expected[key], path + ".objective_history"
                        )
                    elif key == "condition" and cylinder_condition:
                        self.cylinder_condition(
                            actual[key],
                            expected[key],
                            path + ".condition",
                            squared=True,
                        )
                    elif key in _EXACT_FIELDS:
                        self.exact(actual[key], expected[key], path + "." + key)
                    else:
                        self.walk(actual[key], expected[key], path + "." + key)
                return
            if expected.get("format") == "scansor-rigid-occurrence-match-v1":
                self.registration(actual, expected, path)
                return
        self.plain(actual, expected, path)


def compare_semantic(
    actual: Any, expected: Any, *, profile: str = "values", path: str = "$"
) -> dict[str, Any]:
    """Compare complete values, retaining a ledger of permitted trace differences."""
    comparator = _Comparator()
    comparator.require(
        isinstance(cast(object, profile), str) and profile in _PROFILES,
        path,
        "unsupported comparison profile",
    )
    if profile == "graph-snapshot":
        comparator.require(
            isinstance(actual, dict) and isinstance(expected, dict),
            path,
            "graph snapshot must be an object",
        )
        actual = {key: value for key, value in actual.items() if key != "revision"}
        expected = {key: value for key, value in expected.items() if key != "revision"}
    comparator.walk(actual, expected, path)
    comparator.receipt.update(
        complete=True, semantic_complete=True, budgets_validated=True
    )
    return comparator.receipt


def compare_batch(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compare a finite named batch; failures retain receipts for earlier cases."""
    receipt = _new_receipt()
    results: list[dict[str, Any]] = []
    receipt["cases"] = results
    try:
        if not isinstance(cases, (list, tuple)) or not cases:
            raise SemanticParityError(
                "batch: expected nonempty cases array", _new_receipt()
            )
        seen: set[str] = set()
        for case in cases:
            if not isinstance(case, dict) or not {"id", "actual", "expected"} <= set(
                case
            ) <= {"id", "actual", "expected", "profile", "path"}:
                raise SemanticParityError(
                    "batch: invalid comparison case", _new_receipt()
                )
            identifier = case["id"]
            if not isinstance(identifier, str) or not identifier or identifier in seen:
                raise SemanticParityError(
                    "batch: invalid/duplicate case ID", _new_receipt()
                )
            seen.add(identifier)
            try:
                result = compare_semantic(
                    case["actual"],
                    case["expected"],
                    profile=case.get("profile", "values"),
                    path=case.get("path", identifier),
                )
            except SemanticParityError as error:
                results.append({"id": identifier, **error.receipt})
                for key in (
                    "strict_fields_compared",
                    "budget_records_validated",
                    "diagnostic_difference_count",
                ):
                    receipt[key] += error.receipt[key]
                receipt["diagnostic_differences"].extend(
                    error.receipt["diagnostic_differences"]
                )
                raise
            results.append({"id": identifier, **result})
            for key in (
                "strict_fields_compared",
                "budget_records_validated",
                "diagnostic_difference_count",
            ):
                receipt[key] += result[key]
            receipt["diagnostic_differences"].extend(result["diagnostic_differences"])
    except SemanticParityError as error:
        receipt["error"] = str(error)
        raise SemanticParityError(str(error), receipt) from error
    receipt.update(complete=True, semantic_complete=True, budgets_validated=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--input", required=True, type=Path)
    _ = parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    try:
        payload = json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"nonfinite JSON constant {value}")
            ),
        )
        if not isinstance(payload, dict) or set(payload) != {"cases"}:
            raise ValueError("CLI input must contain only cases")
        receipt = compare_batch(payload["cases"])
    except SemanticParityError as error:
        receipt = error.receipt
    except (ValueError, TypeError, KeyError) as error:
        receipt = _new_receipt()
        receipt["error"] = str(error)
    receipt["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with args.output.open("x") as target:
        _ = target.write(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    return 0 if receipt["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
