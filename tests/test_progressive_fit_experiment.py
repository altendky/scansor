"""Experiment isolation, fixed observations, cumulative limits and gate checks."""

import ast
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np
import pytest

import experiments.progressive_fit_experiment as experiment
from experiments.fit_solver import (
    AttemptControl,
    AttemptLimits,
    FitBudgetExhausted,
    FitCancelled,
)
from scansor.nonlinear_least_squares import LeastSquaresFailure, LeastSquaresResult


def numerical(iterations: int = 2) -> LeastSquaresResult:
    return LeastSquaresResult(
        (1.0,), (1.0,), iterations, 1, 1, 1.0, 0.0, "projected-gradient"
    )


@pytest.mark.parametrize("family", ["cone", "cylinder"])
def test_ast_changes_only_the_one_nested_solver_call(family: experiment.Family) -> None:
    source = (experiment.ROOT / f"experiments/mesh_{family}_fit.py").read_text()
    tree = experiment.rewrite_source(source, family)
    changed = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_experiment_solve"
    ]
    assert len(changed) == 1
    changed[0].func = ast.Name(id="solve_least_squares", ctx=ast.Load())
    changed[0].keywords = [
        item
        for item in changed[0].keywords
        if item.arg and not item.arg.startswith("_")
    ]
    assert ast.dump(tree) == ast.dump(ast.parse(source))
    with pytest.raises(ValueError, match="exactly one"):
        _ = experiment.rewrite_source(
            source.replace(
                "numerical = solve_least_squares(", "numerical = renamed_solver("
            ),
            family,
        )
    with pytest.raises(ValueError, match="contract changed"):
        _ = experiment.rewrite_source(
            source.replace(
                "rank_tolerance=1e-6,", "rank_tolerance=1e-6, unexpected=1,"
            ),
            family,
        )


def exercise(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tolerance: float = 1e-10,
    fail_coarse: bool = False,
    cancelled: bool = False,
    quota: int = 512,
) -> tuple[
    experiment.Recorder,
    list[dict[str, Any]],
    list[np.ndarray],
    list[tuple[np.ndarray, np.ndarray]],
    list[np.ndarray],
]:
    points = np.column_stack((np.arange(2048, dtype=float), np.zeros((2048, 2))))
    weights = np.linspace(0.2, 2, len(points))
    control = AttemptControl(
        AttemptLimits(max_iterations=10, max_trials=3, max_evaluations=quota),
        cancelled=lambda: cancelled,
    )
    calls: list[dict[str, Any]] = []
    selected: list[np.ndarray] = []
    evaluated: list[tuple[np.ndarray, np.ndarray]] = []
    full_inputs: list[np.ndarray] = []

    def primitive(
        local: np.ndarray, parameters: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        selected.append(local[:, 0].astype(int))
        return local[:, 0] - parameters[0], np.ones((len(local), 1))

    def full(parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        full_inputs.append(parameters.copy())
        return control.call(
            lambda p: (points[:, 0] - p[0], np.ones((len(points), 1))), parameters
        )

    def solver(
        initial: Sequence[float] | np.ndarray,
        callback: experiment.Evaluation,
        **kwargs: Any,
    ) -> LeastSquaresResult:
        calls.append({"initial": np.asarray(initial).copy(), **kwargs})
        evaluated.append(callback(np.asarray(initial)))
        if fail_coarse and len(calls) == 1:
            raise LeastSquaresFailure(
                "iteration-limit", "test coarse failure", numerical(3)
            )
        return numerical()

    monkeypatch.setattr(experiment, "solve_least_squares", solver)
    recorder = experiment.Recorder()
    _ = experiment.progressive_solve(
        [0.0],
        full,
        _local=points,
        _weights=weights,
        _primitive=primitive,
        _control=control,
        recorder=recorder,
        parameter_scales=[1.0],
        residual_scale=1.0,
        lower_bounds=[-np.inf],
        upper_bounds=[np.inf],
        rank_tolerance=1e-6,
        gradient_tolerance=tolerance,
        geometry_is_valid=feasible,
        max_iterations=10,
        max_trials=3,
    )
    return recorder, calls, selected, evaluated, full_inputs


@pytest.mark.parametrize("tolerance", [1e-10, 1e-12, 1e-3])
def test_nested_direct_subsets_original_weights_and_full_final(
    monkeypatch: pytest.MonkeyPatch, tolerance: float
) -> None:
    recorder, calls, selected, evaluated, full = exercise(
        monkeypatch, tolerance=tolerance
    )
    assert [len(ids) for ids in selected] == [256, 1024]
    assert set(selected[0]) < set(selected[1])
    assert not np.array_equal(selected[0], np.arange(256))
    weights = np.linspace(0.2, 2, 2048)
    for index, (ids, (residual, jacobian)) in enumerate(
        zip(selected, evaluated[:2], strict=True)
    ):
        roots = np.sqrt(weights[ids] / weights[ids].sum())
        np.testing.assert_allclose(residual, roots * (ids - calls[index]["initial"][0]))
        np.testing.assert_allclose(jacobian[:, 0], roots)
    assert len(full) == 1
    assert len(evaluated[-1][0]) == 2048
    assert [call["gradient_tolerance"] for call in calls] == [
        max(1e-4, tolerance),
        max(1e-4, tolerance),
        tolerance,
    ]
    assert [call["max_iterations"] for call in calls] == [10, 8, 6]
    assert [call["max_trials"] for call in calls] == [3, 3, 3]
    assert recorder.attempts[0]["stages"][-1]["cumulative_callbacks"] == 3
    assert recorder.attempts[0]["stages"][-1]["cumulative_iterations"] == 6


def test_coarse_failure_restores_original_candidate_and_charges_its_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder, calls, selected, _, full = exercise(monkeypatch, fail_coarse=True)
    assert len(selected) == 1 and len(calls) == 2 and len(full) == 1
    np.testing.assert_array_equal(calls[-1]["initial"], [0.0])
    assert calls[-1]["max_iterations"] == 7
    assert recorder.attempts[0]["coarse_fallback"] is True
    assert recorder.attempts[0]["stages"][-1]["cumulative_iterations"] == 5


def test_cancellation_and_callback_quota_are_not_restarted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(FitCancelled):
        _ = exercise(monkeypatch, cancelled=True)
    with pytest.raises(FitBudgetExhausted):
        _ = exercise(monkeypatch, quota=1)


@pytest.mark.parametrize("family", ["cone", "cylinder"])
def test_actual_small_bypass_preserves_geometry_and_full_certificate(
    family: experiment.Family,
) -> None:
    case = experiment.Case(family, 512, 7)
    points, weights, normals, truth = experiment.fixture(case)
    binding = experiment.binding(family, points, weights, normals)
    recorder = experiment.Recorder()
    fit = experiment.instrumented_family(family, recorder)
    result = fit(points, weights, normals, binding)
    assert experiment.certificate(points, weights, result, truth)["passed"]
    assert all(
        len(attempt["stages"]) == 1 and attempt["stages"][0]["rows"] == 512
        for attempt in recorder.attempts
    )
    assert all("order_sha256" not in attempt for attempt in recorder.attempts)
    baseline_recorder = experiment.Recorder()
    baseline = experiment.instrumented_family(
        family, baseline_recorder, progressive=False
    )
    before = baseline(points, weights, normals, binding)
    assert before == result
    equivalent = baseline_recorder.equivalent
    assert equivalent is not None and equivalent(
        baseline_recorder.selected, recorder.selected
    )
    moved = list(recorder.selected.numerical.parameters)
    moved[0] += 0.001
    altered = replace(
        recorder.selected,
        numerical=replace(recorder.selected.numerical, parameters=tuple(moved)),
    )
    assert not equivalent(baseline_recorder.selected, altered)
    result["solver"]["coordinate_length"] *= 2
    assert not experiment.certificate(points, weights, result, truth)["passed"]


def fake_gate_records() -> list[dict[str, Any]]:
    return [
        {
            "id": case.id,
            "case": {"family": case.family, "count": case.count},
            "medians": {"baseline": 1.0, "progressive": 0.4},
            "comparisons": [
                {
                    "classification_preserved": True,
                    "accuracy_preserved": True,
                    "original_family_equivalent": True,
                    "full_normalized_objective_preserved": True,
                    "original_proposals_preserved": True,
                }
            ]
            * 3,
        }
        for case in experiment.registered_cases()
    ]


def fake_controls() -> list[dict[str, Any]]:
    return [
        {"id": name, "correct": True, "overhead_pass": True}
        for name in ("sphere-216", "cone-plane-4096", "public-failure-regressions")
    ]


@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "classification",
        "accuracy",
        "original_family_equivalent",
        "full_normalized_objective_preserved",
        "original_proposals_preserved",
        "slow",
        "small-overhead",
        "not-twofold",
    ],
)
def test_gate_cannot_pass_missing_cases_inaccurate_or_regressing_results(
    defect: str,
) -> None:
    records = fake_gate_records()
    assert experiment.evaluate_gate(records, fake_controls())["cone"]["continue"]
    if defect == "missing":
        _ = records.pop(0)
    elif defect in ("classification", "accuracy"):
        records[0]["comparisons"] = [
            {
                "classification_preserved": defect != "classification",
                "accuracy_preserved": defect != "accuracy",
            }
        ] * 3
    elif defect in (
        "original_family_equivalent",
        "full_normalized_objective_preserved",
        "original_proposals_preserved",
    ):
        records[0]["comparisons"][0][defect] = False
    elif defect in ("slow", "small-overhead"):
        record = next(
            record
            for record in records
            if record["case"]["family"] == "cone"
            and (record["case"]["count"] == 512) == (defect == "small-overhead")
        )
        record["medians"]["progressive"] = 1.12
    else:
        for record in records:
            record["medians"]["progressive"] = 0.6
    assert not experiment.evaluate_gate(records, fake_controls())["cone"]["continue"]


def test_missing_or_failed_controls_prevent_continuation() -> None:
    records = fake_gate_records()
    assert not experiment.evaluate_gate(records)["cone"]["continue"]
    controls = fake_controls()
    controls[0]["correct"] = False
    assert not experiment.evaluate_gate(records, controls)["cone"]["continue"]


def test_reordering_is_only_a_row_permutation_and_inputs_are_deterministic() -> None:
    case = experiment.Case("cone", 4096, 19)
    original = experiment.fixture(case)
    reordered = experiment.fixture(replace(case, control="reordered"))
    order = np.random.default_rng(34).permutation(case.count)
    for before, after, repeated in zip(
        original[:3], reordered[:3], experiment.fixture(case)[:3], strict=True
    ):
        np.testing.assert_array_equal(before, repeated)
        np.testing.assert_array_equal(before[order], after)


def feasible(_parameters: experiment.Array) -> bool:
    return True


def test_actual_family_failure_controls_preserve_rejections() -> None:
    record = experiment.run_failure_regressions()
    assert record["correct"]
    assert {
        item["classification"]
        for item in record["records"]
        if item["case"] == "near-parallel-hard-plane"
    } == {"ambiguous"}
