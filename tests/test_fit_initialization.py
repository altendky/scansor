"""Recovery policy evidence from independent, fixed-observation problems.

These fixtures preregister recovery for identifiable scalar nonlinear problems;
they do not establish cylinder/cone initialization or physical fit acceptance.
"""

import json
from collections.abc import Iterator
from dataclasses import replace

import numpy as np
import pytest

from experiments.fit_coordinates import CoordinateFrame, FitCoordinates
from experiments.fit_initialization import (
    FitAssessment,
    FitCandidate,
    InitializationBinding,
    RecoveryFailure,
    RecoveryPolicy,
    RecoveryProblem,
    initialization_binding,
    recover_fit,
)
from experiments.fit_solver import (
    AttemptControl,
    AttemptLimits,
    FitBudgetExhausted,
    FitCancelled,
    GeometricFit,
    normalized_weights,
    solve_geometric_fit,
)
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresResult,
    solve_constrained_least_squares,
)
from scansor.nonlinear_least_squares import (
    LeastSquaresFailure,
    LeastSquaresResult,
)

INTENT: dict[str, object] = {
    "family": "squared-observation-test",
    "references": [],
    "constraints": {"nonnegative": True},
    "support": None,
    "settings": {},
}
BINDING = initialization_binding("a" * 64, "b" * 64, [4, 9, 12], INTENT)


def candidate(
    name: str, value: float, binding: InitializationBinding = BINDING
) -> FitCandidate:
    return FitCandidate(
        name, (value,), "selected observations", "scalar-v1", binding.sha256
    )


def bound_problem[Result](
    problem: RecoveryProblem[Result], policy: RecoveryPolicy
) -> RecoveryProblem[Result]:
    binding = initialization_binding(
        "a" * 64, "b" * 64, [4, 9, 12], INTENT, policy=policy
    )
    return replace(problem, binding=binding)


def coordinates(size: int) -> FitCoordinates:
    return FitCoordinates(CoordinateFrame(np.zeros(3), 1.0), size)


def square_problem() -> tuple[RecoveryProblem[GeometricFit], float]:
    """All positive noisy observations identify one nonnegative square root."""
    observations = np.array([3.8, 4.0, 4.1])
    weights = np.array([1.0, 3.0, 2.0])
    normalized = weights / weights.sum()
    expected = float(np.sqrt(normalized @ observations))

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return values[0] ** 2 - observations, np.full((3, 1), 2 * values[0])

    def solve(start: FitCandidate, control: AttemptControl) -> GeometricFit:
        return solve_geometric_fit(
            np.asarray(start.parameters),
            weights,
            coordinates(1),
            evaluate,
            lambda values: bool(values[0] >= 0),
            control=control,
        )

    def assess(fitted: GeometricFit) -> FitAssessment:
        # Reconstruct the full objective independently of the solver history.
        residual = fitted.parameters[0] ** 2 - observations
        return FitAssessment(
            float(normalized @ residual**2),
            bool(fitted.parameters[0] >= 0),
            fitted.solver["rank"],
            fitted.solver["scaled_jacobian_condition"],
        )

    return (
        RecoveryProblem(
            BINDING,
            {"scalar-v1": 1},
            solve,
            assess,
            lambda left, right: bool(
                np.allclose(left.parameters, right.parameters, atol=1e-8, rtol=0)
            ),
        ),
        expected,
    )


@pytest.mark.parametrize("hint_value", [0.0, 1e12])
def test_data_derived_start_recovers_despite_rank_deficient_or_distant_hint(
    hint_value: float,
) -> None:
    problem, expected = square_problem()
    # A fresh start is computed from these observations, never from the hint.
    fresh = [candidate("fresh", expected / 2)]
    without_hint = recover_fit(problem, fresh)
    recovered = recover_fit(problem, fresh, hint=candidate("bad-hint", hint_value))
    assert recovered.result.parameters == pytest.approx([expected], abs=1e-9)
    assert recovered.result.parameters == pytest.approx(without_hint.result.parameters)
    assert recovered.diagnostics.attempts[0].role == "fresh"
    assert recovered.diagnostics.attempts[1].role == "hint"
    if hint_value == 0:
        assert recovered.diagnostics.attempts[1].outcome == "rank-deficient"


@pytest.mark.parametrize("defect", ["binding", "nonfinite", "chart", "shape"])
def test_unusable_hint_preserves_fresh_order_and_budget(defect: str) -> None:
    calls: list[str] = []

    def solve(start: FitCandidate, control: AttemptControl) -> float:
        calls.append(start.id)
        return control.call(lambda values: float(values[0]), np.array(start.parameters))

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        solve,
        lambda value: FitAssessment(value**2),
        lambda left, right: left == right,
    )
    policy = RecoveryPolicy(max_fresh_attempts=2)
    problem = bound_problem(problem, policy)
    fresh = [candidate(str(index), float(index), problem.binding) for index in range(4)]
    baseline = recover_fit(problem, fresh, policy=policy)
    assert calls == ["0", "1"]
    calls.clear()
    hint = candidate("hint", 0.0, problem.binding)
    if defect == "binding":
        hint = replace(hint, binding_sha256="c" * 64)
    elif defect == "nonfinite":
        hint = replace(hint, parameters=(float("nan"),))
    elif defect == "chart":
        hint = replace(hint, chart="unknown-chart")
    else:
        hint = replace(hint, parameters=(0.0, 1.0))
    recovered = recover_fit(problem, fresh, hint=hint, policy=policy)
    assert calls == ["0", "1"]
    assert recovered.diagnostics.attempts[:-1] == baseline.diagnostics.attempts
    assert recovered.diagnostics.attempts[-1].outcome == "screened-invalid-or-stale"


def test_endless_proposals_and_extra_hint_have_independent_finite_budgets() -> None:
    generated: list[int] = []
    attempted: list[str] = []
    policy = RecoveryPolicy(max_proposals=5, max_fresh_attempts=2)
    binding = initialization_binding(
        "a" * 64, "b" * 64, [4, 9, 12], INTENT, policy=policy
    )

    def proposals() -> Iterator[FitCandidate]:
        index = 0
        while True:
            generated.append(index)
            yield candidate(str(index), float(index + 1), binding)
            index += 1

    def solve(start: FitCandidate, control: AttemptControl) -> float:
        attempted.append(start.id)
        return control.call(lambda values: float(values[0]), np.array(start.parameters))

    problem = RecoveryProblem(
        binding,
        {"scalar-v1": 1},
        solve,
        lambda value: FitAssessment(value**2),
        lambda left, right: left == right,
    )
    result = recover_fit(
        problem,
        proposals(),
        hint=candidate("extra-hint", 0.0, binding),
        policy=policy,
    )
    assert generated == list(range(5))
    assert attempted == ["0", "1", "extra-hint"]
    assert result.result == 0.0
    assert result.diagnostics.selected == "extra-hint"
    assert (
        sum(
            entry.outcome == "fresh-attempt-limit"
            for entry in result.diagnostics.attempts
        )
        == 3
    )


def test_callback_budget_is_a_failure_and_does_not_publish_a_partial_fit() -> None:
    problem, expected = square_problem()
    policy = RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1))
    problem = bound_problem(problem, policy)
    with pytest.raises(RecoveryFailure) as failure:
        _ = recover_fit(
            problem,
            [candidate("fresh", expected / 2, problem.binding)],
            policy=policy,
        )
    assert failure.value.code == "search-exhausted"
    diagnostic = failure.value.diagnostics
    assert diagnostic.selected is None
    assert diagnostic.attempts[0].outcome == "evaluation-limit"
    assert diagnostic.attempts[0].evaluations == 1


def test_zero_iteration_budget_does_not_claim_stationarity_at_the_initial_guess() -> (
    None
):
    problem, expected = square_problem()
    policy = RecoveryPolicy(attempt=AttemptLimits(max_iterations=0))
    problem = bound_problem(problem, policy)
    with pytest.raises(RecoveryFailure) as failure:
        _ = recover_fit(
            problem,
            [candidate("not-stationary", expected / 2, problem.binding)],
        )
    diagnostic = failure.value.diagnostics.attempts[0]
    assert diagnostic.outcome == "iteration-limit"
    assert diagnostic.solver is not None
    assert diagnostic.solver["iterations"] == 0


def test_a_hint_cannot_be_the_only_available_path_to_success() -> None:
    problem, expected = square_problem()
    with pytest.raises(RecoveryFailure) as failure:
        _ = recover_fit(problem, [], hint=candidate("hint-only", expected))
    assert failure.value.code == "no-valid-candidates"
    assert failure.value.diagnostics.attempts[0].outcome == "screened-no-fresh-start"
    assert failure.value.diagnostics.attempts[0].evaluations == 0


def test_cancellation_before_generation_consumes_no_proposals() -> None:
    problem, _ = square_problem()
    generated: list[str] = []

    def never_generate() -> Iterator[FitCandidate]:
        generated.append("requested")
        yield candidate("unused", 1.0)

    with pytest.raises(FitCancelled):
        _ = recover_fit(problem, never_generate(), cancelled=lambda: True)
    assert not generated


def test_cancellation_during_callback_is_not_a_numeric_failure_or_restart() -> None:
    cancelled = False
    calls: list[str] = []

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        nonlocal cancelled
        cancelled = True
        return values - 2, np.eye(1)

    def solve(start: FitCandidate, control: AttemptControl) -> GeometricFit:
        calls.append(start.id)
        return solve_geometric_fit(
            np.array(start.parameters),
            np.ones(1),
            coordinates(1),
            evaluate,
            lambda _values: True,
            control=control,
        )

    def never_assess(_fitted: GeometricFit) -> FitAssessment:
        pytest.fail("cancelled computation must not publish an assessment")

    problem = RecoveryProblem(
        BINDING, {"scalar-v1": 1}, solve, never_assess, lambda _left, _right: True
    )
    with pytest.raises(FitCancelled):
        _ = recover_fit(
            problem,
            [candidate("first", 0), candidate("second", 1)],
            cancelled=lambda: cancelled,
        )
    assert calls == ["first"]


def test_full_weighted_assessment_selects_later_better_local_solution() -> None:
    weights = np.array([2.0, 1.0, 0.5])
    coefficients = np.array([1.0, 0.2, 0.3])
    attempted: list[str] = []
    row_counts: list[int] = []
    assessed: list[float] = []

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        residual = coefficients * np.array(
            [values[0] ** 2 - 1, values[0] - 1, values[0] - 1]
        )
        row_counts.append(len(residual))
        return residual, (coefficients * [2 * values[0], 1, 1])[:, None]

    def solve(start: FitCandidate, control: AttemptControl) -> GeometricFit:
        attempted.append(start.id)
        fitted = solve_geometric_fit(
            np.array(start.parameters),
            weights,
            coordinates(1),
            evaluate,
            lambda _values: True,
            control=control,
        )
        # A private chart's history is deliberately misleading for comparison.
        return replace(
            fitted, objective_history=[-10 if fitted.parameters[0] < 0 else 10]
        )

    def assess(fitted: GeometricFit) -> FitAssessment:
        x = float(fitted.parameters[0])
        assessed.append(x)
        # Compute the complete weighted objective without reading fitted residuals.
        score = (
            2 * (x * x - 1) ** 2 + (0.2 * (x - 1)) ** 2 + 0.5 * (0.3 * (x - 1)) ** 2
        ) / 3.5
        gradient = 4 * x * (x * x - 1) + (0.04 + 0.045) * (x - 1)
        assert abs(gradient) < 1e-8
        return FitAssessment(score)

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        solve,
        assess,
        lambda left, right: bool(np.allclose(left.parameters, right.parameters)),
    )
    result = recover_fit(problem, [candidate("negative", -2), candidate("positive", 2)])
    assert attempted == ["negative", "positive"]
    assert len(assessed) == 2 and assessed[0] < 0 and assessed[1] > 0
    assert row_counts and set(row_counts) == {3}
    assert result.result.parameters == pytest.approx([1.0], abs=1e-9)
    assert result.diagnostics.selected == "positive"


def test_restarts_preserve_an_explicit_fixed_parameter() -> None:
    times = np.array([-2.0, 0.0, 1.0, 3.0])
    observations = np.array([2.0, 6.0, 9.0, 10.0])
    weights = np.array([1.0, 2.0, 3.0, 4.0])
    solved: list[np.ndarray] = []

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        assert values[0] == 7.0
        return values[0] + values[1] * times - observations, np.column_stack(
            (np.ones(4), times)
        )

    def solve(start: FitCandidate, control: AttemptControl) -> GeometricFit:
        fitted = solve_geometric_fit(
            np.array([7.0, start.parameters[0]]),
            weights,
            coordinates(2),
            evaluate,
            lambda values: bool(values[0] == 7.0),
            active_parameters=(1,),
            control=control,
        )
        solved.append(fitted.parameters)
        return fitted

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        solve,
        lambda fitted: FitAssessment(
            float(normalized_weights(weights) @ fitted.residual**2)
        ),
        lambda left, right: bool(np.allclose(left.parameters, right.parameters)),
    )
    result = recover_fit(
        problem,
        [candidate("fresh", -100)],
        hint=candidate("hint", 100),
    )
    expected_slope = (weights * times) @ (observations - 7) / (weights @ times**2)
    assert len(solved) == 2
    assert all(values[0] == 7.0 for values in solved)
    assert result.result.parameters == pytest.approx([7.0, expected_slope])


def test_equalities_and_projection_share_the_attempt_control() -> None:
    calls: list[str] = []

    def observe(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        calls.append("observation")
        return values - [5.0, -1.0], np.eye(2)

    def equality(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        calls.append("equality")
        return np.array([values.sum() - 3.0]), np.ones((1, 2))

    controls: list[AttemptControl] = []

    def solve(
        start: FitCandidate, control: AttemptControl
    ) -> ConstrainedLeastSquaresResult:
        controls.append(control)
        return solve_constrained_least_squares(
            [start.parameters[0], 0.0],
            lambda values: control.call(observe, values),
            lambda values: control.call(equality, values),
            parameter_scales=[1.0, 1.0],
            max_iterations=control.limits.max_iterations,
            max_trials=control.limits.max_trials,
        )

    def assess(fitted: ConstrainedLeastSquaresResult) -> FitAssessment:
        values = np.array(fitted.parameters)
        return FitAssessment(
            float(np.sum((values - [5.0, -1.0]) ** 2)),
            bool(abs(values.sum() - 3.0) < 1e-10),
        )

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        solve,
        assess,
        lambda left, right: bool(np.allclose(left.parameters, right.parameters)),
    )
    result = recover_fit(problem, [candidate("infeasible-start", 100)])
    assert result.result.parameters == pytest.approx([4.5, -1.5], abs=1e-9)
    assert calls.count("equality") > 1 and "observation" in calls
    assert controls[0].evaluations == len(calls)
    assert result.diagnostics.attempts[0].evaluations == len(calls)
    calls.clear()
    policy = RecoveryPolicy(attempt=AttemptLimits(max_evaluations=1))
    problem = bound_problem(problem, policy)
    with pytest.raises(RecoveryFailure) as failure:
        _ = recover_fit(
            problem,
            [candidate("infeasible-start", 100, problem.binding)],
            policy=policy,
        )
    assert calls == ["equality"]
    assert failure.value.diagnostics.attempts[0].outcome == "evaluation-limit"


@pytest.mark.parametrize("equivalent", [True, False])
def test_equal_objectives_distinguish_equivalent_and_competing_geometry(
    equivalent: bool,
) -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment((value**2 - 1) ** 2),
        lambda left, right: abs(left) == abs(right) if equivalent else left == right,
    )
    fresh = [candidate("first", -1), candidate("second", 1)]
    if equivalent:
        result = recover_fit(problem, fresh)
        assert result.diagnostics.selected == "first"
    else:
        with pytest.raises(RecoveryFailure, match="ambiguous") as failure:
            _ = recover_fit(problem, fresh)
        assert failure.value.diagnostics.selected is None


@pytest.mark.parametrize("with_hint", [False, True])
def test_equivalent_roundoff_ties_keep_first_fresh_and_measured_scores(
    with_hint: bool,
) -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment(value),
        lambda _a, _b: True,
    )
    later = candidate("later", 0.0)
    result = recover_fit(
        problem,
        [candidate("first", 1e-24), *([] if with_hint else [later])],
        hint=later if with_hint else None,
    )
    assert result.result == 1e-24
    assert result.diagnostics.selected == "first"
    assert result.diagnostics.selected_role == "fresh"
    assert [
        item.assessment.objective
        for item in result.diagnostics.attempts
        if item.assessment is not None
    ] == [1e-24, 0.0]


def test_objective_tie_set_is_anchored_to_raw_minimum_without_chaining() -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment(value),
        lambda _a, _b: True,
    )
    result = recover_fit(
        problem,
        [
            candidate("outside", 1.5e-12),
            candidate("first-tied", 0.75e-12),
            candidate("raw-min", 0.0),
        ],
    )
    assert result.diagnostics.selected == "first-tied"


def test_nontransitive_geometry_equivalence_cannot_hide_competing_tied_pair() -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment(0.0 if value == 0.6 else 0.5e-12),
        lambda a, b: abs(a - b) < 1,
    )
    with pytest.raises(RecoveryFailure, match="ambiguous"):
        _ = recover_fit(
            problem,
            [
                candidate("left", 0.0),
                candidate("raw-min", 0.6),
                candidate("right", 1.2),
            ],
        )


@pytest.mark.parametrize(
    ("fresh_value", "hint_value", "selected_role"),
    [(1.0, 0.0, "hint"), (0.0, 1.0, "fresh")],
)
def test_selected_role_disambiguates_colliding_fresh_and_hint_ids(
    fresh_value: float, hint_value: float, selected_role: str
) -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment(value**2),
        lambda left, right: left == right,
    )
    result = recover_fit(
        problem,
        [candidate("same-id", fresh_value)],
        hint=candidate("same-id", hint_value),
    )
    assert result.result == 0.0
    assert result.diagnostics.selected == "same-id"
    assert result.diagnostics.selected_role == selected_role
    payload = json.loads(json.dumps(result.diagnostics.json(), allow_nan=False))
    assert payload["selected_role"] == selected_role


def test_failure_diagnostics_are_strict_json_and_keep_failed_solver_evidence() -> None:
    solver = LeastSquaresResult(
        (0.0,), (float("nan"),), 0, 1, 0, float("inf"), float("nan"), "rank-deficient"
    )

    def fail(_start: FitCandidate, control: AttemptControl) -> float:
        _ = control.call(lambda values: values, np.zeros(1))
        raise LeastSquaresFailure("rank-deficient", "unobservable", solver)

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        fail,
        lambda value: FitAssessment(value),
        lambda left, right: left == right,
    )
    with pytest.raises(RecoveryFailure) as failure:
        _ = recover_fit(
            problem,
            [candidate("rank-deficient", 0)],
            hint=candidate("nonfinite", float("nan")),
        )
    assert failure.value.code == "rank-deficient"
    payload = json.loads(json.dumps(failure.value.diagnostics.json(), allow_nan=False))
    diagnostic = payload["attempts"][0]
    assert diagnostic["solver"]["condition"] == "Infinity"
    assert diagnostic["solver"]["projected_gradient_norm"] == "NaN"
    assert diagnostic["solver"]["objective_history"] == ["NaN"]
    assert payload["attempts"][1]["candidate"]["parameters"] == ["NaN"]


def test_invalid_assessments_cannot_win_a_lower_private_objective() -> None:
    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        lambda start, _control: start.parameters[0],
        lambda value: FitAssessment(value, valid=value != 0),
        lambda left, right: left == right,
    )
    result = recover_fit(problem, [candidate("invalid", 0), candidate("valid", 1)])
    assert result.result == 1
    assert result.diagnostics.attempts[0].outcome == "invalid-result"


def test_unexpected_programming_errors_are_not_retried() -> None:
    def fail(_start: FitCandidate, _control: AttemptControl) -> float:
        raise KeyError("broken family adapter")

    problem = RecoveryProblem(
        BINDING,
        {"scalar-v1": 1},
        fail,
        lambda value: FitAssessment(value),
        lambda left, right: left == right,
    )
    with pytest.raises(KeyError, match="broken family adapter"):
        _ = recover_fit(problem, [candidate("fresh", 1)])


def test_binding_tracks_semantics_and_order_without_computed_seed_fields() -> None:
    intent: dict[str, object] = {
        "family": "sphere",
        "references": {"center": [1, 2, 3]},
        "constraints": {"center": "fixed"},
        "support": None,
        "settings": {},
    }
    original = initialization_binding("a" * 64, "b" * 64, [4, 9], intent)
    reordered_keys = dict(reversed(list(intent.items())))
    assert (
        initialization_binding("a" * 64, "b" * 64, [4, 9], reordered_keys).sha256
        == original.sha256
    )
    for ids, changed_intent, policy in (
        ([9, 4], intent, "selected-data-recovery-v2"),
        (
            [4, 9],
            {**intent, "constraints": {"center": "free"}},
            "selected-data-recovery-v2",
        ),
        ([4, 9], intent, "changed-policy"),
    ):
        assert (
            initialization_binding(
                "a" * 64, "b" * 64, ids, changed_intent, policy_version=policy
            ).sha256
            != original.sha256
        )


@pytest.mark.parametrize(
    "policy",
    [
        RecoveryPolicy(max_proposals=4),
        RecoveryPolicy(max_fresh_attempts=2),
        RecoveryPolicy(attempt=AttemptLimits(max_iterations=50)),
        RecoveryPolicy(attempt=AttemptLimits(max_trials=8)),
        RecoveryPolicy(attempt=AttemptLimits(max_evaluations=100)),
        RecoveryPolicy(objective_atol=1e-8),
        RecoveryPolicy(objective_rtol=1e-5),
        RecoveryPolicy(version="recovery-policy-v2"),
    ],
)
def test_binding_includes_effective_limits_and_objective_tolerances(
    policy: RecoveryPolicy,
) -> None:
    changed = initialization_binding(
        "a" * 64, "b" * 64, [4, 9, 12], INTENT, policy=policy
    )
    assert changed.sha256 != BINDING.sha256
    assert changed.policy == policy
    assert changed == initialization_binding(
        "a" * 64, "b" * 64, [4, 9, 12], INTENT, policy=replace(policy)
    )


def test_executing_a_different_policy_rejects_before_candidate_generation() -> None:
    problem, expected = square_problem()
    generated: list[str] = []

    def proposals() -> Iterator[FitCandidate]:
        generated.append("requested")
        yield candidate("unused", expected)

    with pytest.raises(ValueError):
        _ = recover_fit(
            problem,
            proposals(),
            policy=replace(BINDING.policy, max_fresh_attempts=1),
        )
    assert not generated


def test_actual_policy_change_invalidates_a_cached_start() -> None:
    problem, expected = square_problem()
    problem = bound_problem(problem, replace(BINDING.policy, max_fresh_attempts=1))
    result = recover_fit(
        problem,
        [candidate("fresh", expected / 2, problem.binding)],
        hint=candidate("old-policy-cache", expected),
    )
    assert result.result.parameters == pytest.approx([expected], abs=1e-9)
    assert result.diagnostics.selected_role == "fresh"
    assert result.diagnostics.attempts[-1].outcome == "screened-invalid-or-stale"


def test_legacy_geometric_fit_defaults_preserve_values_and_diagnostics() -> None:
    observations = np.array([1.0, 2.0, 6.0])
    weights = np.array([1.0, 2.0, 1.0])

    def evaluate(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return values[0] - observations, np.ones((3, 1))

    result = solve_geometric_fit(
        np.zeros(1), weights, coordinates(1), evaluate, lambda _values: True
    )
    expected = float(weights @ observations / weights.sum())
    assert result.parameters == pytest.approx([expected])
    assert result.residual == pytest.approx(expected - observations)
    assert result.objective_history[-1] == pytest.approx(
        (weights @ (expected - observations) ** 2) / weights.sum()
    )
    assert result.solver["implementation"] == "scaled-svd-least-squares-v1"
    assert result.solver["termination"] == "projected-gradient"
    assert result.solver["rank"] == 1
    assert result.solver["coordinate_origin"] == [0.0, 0.0, 0.0]
    assert result.solver["coordinate_length"] == 1.0


def test_attempt_control_never_evaluates_beyond_its_quota() -> None:
    control = AttemptControl(AttemptLimits(max_evaluations=1))
    calls: list[float] = []

    def evaluate(values: np.ndarray) -> float:
        calls.append(float(values[0]))
        return float(values[0])

    assert control.call(evaluate, np.array([1.0])) == 1.0
    with pytest.raises(FitBudgetExhausted):
        _ = control.call(evaluate, np.array([2.0]))
    assert calls == [1.0]
