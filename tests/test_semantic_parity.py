"""Mutation evidence for bounded semantic parity, independent of the verifier.

Small native producer records cover real chart recovery and poisoned hints. The
constrained counter fixture preserves the selected public schema/numbers from
the retained #273 native/worker diagnosis, without copying its large snapshots.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime import semantic_parity as parity
from experiments.feature_graph import FeatureGraph, Recipe, Selection, SurfaceFit
from experiments.fit_initialization import initialization_binding
from experiments.mesh_cylinder_fit import fit_selected_cylinder
from experiments.mesh_sphere_fit import fit_shared_sphere_center, fit_sphere
from experiments.nozzle_coaxial import FitSelection, fit_group
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits, prepare_scan
from tests.native_scan_fixture import native_scene_fixture


def fingerprint(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def recovery(record: dict[str, Any]) -> dict[str, Any]:
    return record["initialization"]["recovery"]


def selected(record: dict[str, Any]) -> dict[str, Any]:
    envelope = recovery(record)
    return next(
        attempt
        for attempt in envelope["attempts"]
        if (attempt["candidate"]["id"], attempt["role"])
        == (envelope["selected"], envelope["selected_role"])
    )


def discarded(record: dict[str, Any]) -> dict[str, Any]:
    return next(
        attempt
        for attempt in recovery(record)["attempts"]
        if attempt["solver"] is not None
    )


def rejected(actual: object, expected: object, *, profile: str = "values") -> None:
    before = fingerprint([actual, expected])
    with pytest.raises(parity.SemanticParityError) as caught:
        _ = parity.compare_semantic(actual, expected, profile=profile)
    assert caught.value.receipt["complete"] is False
    assert caught.value.receipt["semantic_complete"] is False
    assert before == fingerprint([actual, expected])
    _ = json.dumps(caught.value.receipt, allow_nan=False)


def accepted(
    actual: object, expected: object, *, profile: str = "values"
) -> dict[str, Any]:
    before = fingerprint([actual, expected])
    receipt = parity.compare_semantic(actual, expected, profile=profile)
    assert receipt["complete"] is True and receipt["semantic_complete"] is True
    assert len(receipt["policy_sha256"]) == 64 and receipt["policy_version"]
    assert receipt["diagnostic_difference_count"] == len(
        receipt["diagnostic_differences"]
    )
    assert before == fingerprint([actual, expected])
    _ = json.dumps(receipt, allow_nan=False)
    return receipt


@pytest.fixture(scope="module")
def cylinder() -> dict[str, Any]:
    theta, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 12, endpoint=False), np.linspace(-3, 3, 5)
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = (
        np.array([3.0, -4, 5])
        + np.column_stack((np.zeros(theta.size), np.zeros(theta.size), height.ravel()))
        + 2.5 * normals
    )
    weights = np.linspace(0.2, 2, len(points))
    binding = initialization_binding(
        hashlib.sha256(points.tobytes()).hexdigest(),
        hashlib.sha256(weights.tobytes() + normals.tobytes()).hexdigest(),
        list(range(len(points))),
        {"family": "cylinder", "support": None},
    )
    result = fit_selected_cylinder(points, weights, normals, binding)
    assert discarded(result)["outcome"] in {
        "line-search-stagnation",
        "rank-deficient",
        "iteration-limit",
        "evaluation-limit",
    }
    assert selected(result)["assessment"]["valid"] is True
    return result


@pytest.fixture(scope="module")
def sphere() -> dict[str, Any]:
    theta = np.linspace(0, 2 * np.pi, 12, endpoint=False)
    center = np.array([2.0, -1, 3])
    points = [
        center + 2 * np.column_stack((np.cos(theta), np.sin(theta), np.zeros(12))),
        center + 3 * np.column_stack((np.zeros(12), np.cos(theta), np.sin(theta))),
    ]
    weights = [np.linspace(0.2, 2, 12), np.linspace(2, 0.2, 12)]
    binding = initialization_binding(
        hashlib.sha256(np.vstack(points).tobytes()).hexdigest(),
        hashlib.sha256(np.concatenate(weights).tobytes()).hexdigest(),
        list(range(24)),
        {"family": "shared-sphere"},
    )
    return dict(
        fit_shared_sphere_center(
            points, weights, binding, hint_center=np.full(3, np.nan)
        )
    )


@pytest.fixture(scope="module")
def graph() -> dict[str, Any]:
    fixture = native_scene_fixture()
    project = ScanProject()
    initial = project.accepted
    candidate = project.prepare_add(
        io.BytesIO(fixture.raw),
        "independent.ply",
        ScanLimits(100_000, 1000, 1000, 10_000_000),
        generation=initial.generation,
        token=str(initial.graph.snapshot()["token"]),
    )
    state = project.commit(candidate)
    recipe = Recipe.model_validate(state.graph.snapshot()["recipe"])
    recipe.nodes.extend(
        [
            Selection(
                id="patch",
                label="Painted cylinder",
                operation="selection",
                source="scan",
                ids=fixture.selections["cylinder"],
            ),
            SurfaceFit(
                id="cylinder",
                label="Cylinder",
                operation="fit",
                selections=["patch"],
                kind="cylinder",
            ),
        ]
    )
    recipe = recipe.model_copy(update={"output": "cylinder"})
    active = FeatureGraph(state.workspace, recipe)
    evaluated = active.evaluate(str(active.snapshot()["token"]))
    assert not evaluated["errors"]
    return json.loads(json.dumps(evaluated, allow_nan=False))


def constrained() -> dict[str, Any]:
    """Compact selected #273 schema: 2 observations+3 equalities+1 certificate."""
    policy = {
        "version": "selected-data-recovery-v2",
        "max_proposals": 16,
        "max_fresh_attempts": 8,
        "attempt": {"max_iterations": 100, "max_trials": 16, "max_evaluations": 512},
        "objective_atol": 1e-12,
        "objective_rtol": 1e-9,
    }
    solver: dict[str, Any] = {
        "termination": "constrained-stationarity",
        "iterations": 1,
        "evaluations": 2,
        "constraint_evaluations": 3,
        "rank": 12,
        "constraint_rank": 13,
        "tangent_dimension": 12,
        "condition": 5.679275317911534,
        "objective_history": [6.100449093375113e-16, 5.530709135882373e-16],
        "coordinate_origin": [
            11.565120125016485,
            -5.2673835771158615,
            10.337341051083694,
        ],
        "coordinate_length": 6.879463129431035,
        "constraint_violation": 9.992007221626409e-16,
        "projected_gradient_norm": 5.497635321513419e-17,
    }
    binding = "db04bb0a8ddae5408932cdbd1f980f3e1cac1215462a3d3cb01e31b284d826ff"
    candidate = {
        "id": "selected-joint-0",
        "parameters": [
            11.48427192969421,
            -5.475575801704141,
            10.441582127198277,
            0.4877001802184413,
            0.23200471930231503,
            0.8416188831272465,
        ],
        "derivation": "selected moment axes and algebraic orbit/phase evidence",
        "chart": "workspace-joint-state-v1",
        "binding_sha256": binding,
    }
    attempt = {
        "candidate": candidate,
        "role": "fresh",
        "outcome": "locally-converged",
        "evaluations": 6,
        "assessment": {
            "objective": 1.1061418269890841e-15,
            "valid": True,
            "rank": 12,
            "condition": 5.679275317911534,
            "termination": "constrained-stationarity",
            "iterations": 1,
        },
        "solver": None,
    }
    return {
        "weighted_rms": 2.2880207293299142e-7,
        "objective_history": solver["objective_history"].copy(),
        "solver": solver,
        "initialization": {
            "algorithm": "selected-symmetry-group-recovery-v1",
            "recovery": {
                "policy": policy,
                "binding_sha256": binding,
                "attempts": [attempt],
                "selected": "selected-joint-0",
                "termination": "best-attempted-local-solution",
                "selected_role": "fresh",
            },
        },
    }


def test_real_native_records_and_poisoned_hint_are_recognized(
    cylinder: dict[str, Any], sphere: dict[str, Any], graph: dict[str, Any]
) -> None:
    for record in (cylinder, sphere, graph):
        receipt = accepted(record, copy.deepcopy(record))
        assert receipt["diagnostic_differences"] == []
        assert receipt["budgets_validated"]
    hint = recovery(sphere)["attempts"][-1]
    assert hint["candidate"]["parameters"] == ["NaN"] * 5
    assert hint["outcome"] == "screened-invalid-or-stale" and hint["evaluations"] == 0


def test_discarded_real_gradient_and_parameter_differences_are_retained(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    failure = discarded(actual)
    old_gradient = failure["solver"]["projected_gradient_norm"]
    failure["solver"]["projected_gradient_norm"] += 2.5e-8
    failure["solver"]["parameters"][0] += 2
    receipt = accepted(actual, cylinder)
    ledger = receipt["diagnostic_differences"]
    assert any(
        item["path"].endswith(".projected_gradient_norm")
        and item["expected"] == old_gradient
        for item in ledger
    )
    assert any(".parameters[0]" in item["path"] for item in ledger)
    assert discarded(actual)["solver"] == failure["solver"]


def test_real_discarded_outcome_can_become_evaluation_limit(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    failure = discarded(actual)
    failure.update(
        outcome="evaluation-limit", evaluations=512, assessment=None, solver=None
    )
    receipt = accepted(actual, cylinder)
    assert any(
        item["path"].endswith(".outcome") for item in receipt["diagnostic_differences"]
    )
    assert any(
        item["path"].endswith(".solver") for item in receipt["diagnostic_differences"]
    )


@pytest.fixture
def assessed_discard(
    cylinder: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """CI #265: one discarded solve reaches assessment, the other stagnates."""
    expected = copy.deepcopy(cylinder)
    actual = copy.deepcopy(expected)
    attempt = discarded(actual)
    candidate = attempt["candidate"]["id"]
    assert candidate not in expected["initialization"]["assessments"]
    assessment = copy.deepcopy(selected(actual)["assessment"])
    assessment.update(valid=False, objective=1.0)
    attempt.update(outcome="invalid-result", assessment=assessment, solver=None)
    metrics = copy.deepcopy(
        actual["initialization"]["assessments"][recovery(actual)["selected"]]
    )
    metrics["aligned_scaled_gradient_infinity_norm"] = 0.1
    actual["initialization"]["assessments"][candidate] = metrics
    return actual, expected, candidate


@pytest.mark.parametrize("reverse", [False, True])
def test_discarded_assessment_inventory_follows_differing_outcomes(
    assessed_discard: tuple[dict[str, Any], dict[str, Any], str], reverse: bool
) -> None:
    actual, expected, candidate = assessed_discard
    if reverse:
        actual, expected = expected, actual
    receipt = accepted(actual, expected)
    entry = next(
        item
        for item in receipt["diagnostic_differences"]
        if item["path"] == "$.initialization.assessments." + candidate
    )
    assert entry["actual"] == actual["initialization"]["assessments"].get(candidate)
    assert entry["expected"] == expected["initialization"]["assessments"].get(candidate)
    assert "differing outcomes" in entry["reason"]
    assert receipt["budgets_validated"]


@pytest.mark.parametrize(
    "mutation",
    [
        "selected-missing",
        "unknown-candidate",
        "unknown-metric",
        "nonobject-metrics",
        "same-outcome-delete",
        "initializers-missing",
        "overbudget",
        "geometry",
    ],
)
def test_assessment_inventory_exception_preserves_certificates_and_schema(
    assessed_discard: tuple[dict[str, Any], dict[str, Any], str], mutation: str
) -> None:
    actual, expected, candidate = assessed_discard
    if mutation == "selected-missing":
        for record in (actual, expected):
            del record["initialization"]["assessments"][recovery(record)["selected"]]
    elif mutation == "unknown-candidate":
        for record in (actual, expected):
            record["initialization"]["assessments"]["invented-candidate"] = {}
    elif mutation == "unknown-metric":
        actual["initialization"]["assessments"][candidate]["invented-metric"] = 0
    elif mutation == "nonobject-metrics":
        actual["initialization"]["assessments"][candidate] = []
    elif mutation == "same-outcome-delete":
        expected = copy.deepcopy(actual)
        del expected["initialization"]["assessments"][candidate]
    elif mutation == "initializers-missing":
        del actual["initialization"]["initializers"][candidate]
    elif mutation == "overbudget":
        for record in (actual, expected):
            recovery(record)["attempts"][1]["evaluations"] = 513
    elif mutation == "geometry":
        actual["analytic"]["radius"] += 1e-3
    else:
        raise AssertionError(mutation)
    rejected(actual, expected)


def test_assessment_shared_chart_cannot_attribute_another_attempts_outcome(
    assessed_discard: tuple[dict[str, Any], dict[str, Any], str],
) -> None:
    assessed, expected, candidate = assessed_discard
    expected["initialization"]["assessments"]["shared-discard-chart"] = copy.deepcopy(
        assessed["initialization"]["assessments"][candidate]
    )
    for attempt in recovery(expected)["attempts"][1:3]:
        attempt["candidate"]["chart"] = "shared-discard-chart"
    actual = copy.deepcopy(expected)
    changed = recovery(actual)["attempts"][2]
    changed.update(
        outcome="invalid-result",
        assessment=copy.deepcopy(recovery(assessed)["attempts"][1]["assessment"]),
        solver=None,
    )
    # Only attempt2 changed; a shared chart cannot prove whose metrics vanished.
    del actual["initialization"]["assessments"]["shared-discard-chart"]
    rejected(actual, expected)
    rejected(expected, copy.deepcopy(expected))


@pytest.mark.parametrize("selected_alias", [False, True])
def test_fitted_metrics_allow_unique_chart_aliases(
    assessed_discard: tuple[dict[str, Any], dict[str, Any], str], selected_alias: bool
) -> None:
    actual, expected, candidate = assessed_discard
    if selected_alias:
        candidate = recovery(actual)["selected"]
    for record in (actual, expected):
        attempt = next(
            item
            for item in recovery(record)["attempts"]
            if item["candidate"]["id"] == candidate
        )
        attempt["candidate"]["chart"] = "unique-assessment-chart"
        metrics = record["initialization"]["assessments"].pop(candidate, None)
        if metrics is not None:
            record["initialization"]["assessments"]["unique-assessment-chart"] = metrics
    _ = accepted(actual, expected)


@pytest.mark.parametrize(
    ("metric", "value"),
    [("aligned_scaled_gradient_infinity_norm", 0.1), ("coverage_ratio", 0.0)],
)
def test_selected_alias_metrics_cannot_bypass_independent_admission(
    cylinder: dict[str, Any], metric: str, value: float
) -> None:
    actual = copy.deepcopy(cylinder)
    candidate = recovery(actual)["selected"]
    selected(actual)["candidate"]["chart"] = "selected-alias"
    metrics = actual["initialization"]["assessments"].pop(candidate)
    metrics[metric] = value
    actual["initialization"]["assessments"]["selected-alias"] = metrics
    rejected(actual, copy.deepcopy(actual))


def test_selected_constraint_callback_variation_is_bounded_not_ignored() -> None:
    expected = constrained()
    actual = copy.deepcopy(expected)
    actual["solver"]["constraint_evaluations"] = 4
    selected(actual)["evaluations"] = 7
    receipt = accepted(actual, expected)
    assert any(
        item["path"].endswith(".constraint_evaluations")
        for item in receipt["diagnostic_differences"]
    )
    actual["solver"]["constraint_evaluations"] = 513
    rejected(actual, expected)


def test_shared_sphere_stationarity_boundary_cannot_hide_inside_numeric_tolerance(
    sphere: dict[str, Any],
) -> None:
    expected = copy.deepcopy(sphere)
    expected["solver"]["scaled_projected_gradient_infinity_norm"] = 9e-11
    _ = accepted(expected, copy.deepcopy(expected))
    actual = copy.deepcopy(expected)
    actual["solver"]["scaled_projected_gradient_infinity_norm"] = 1.1e-10
    assert abs(1.1e-10 - 9e-11) < 1e-10
    rejected(actual, expected)
    rejected(actual, copy.deepcopy(actual))


def test_selected_cylinder_coverage_boundary_remains_an_admission_classification(
    cylinder: dict[str, Any],
) -> None:
    expected = copy.deepcopy(cylinder)
    expected["initialization"]["coverage_ratio"] = 1.01e-10
    _ = accepted(expected, copy.deepcopy(expected))
    actual = copy.deepcopy(expected)
    actual["initialization"]["coverage_ratio"] = 0.99e-10
    rejected(actual, expected)
    rejected(actual, copy.deepcopy(actual))


def test_explicit_fitted_scale_gradient_guard_keeps_its_existing_threshold(
    cylinder: dict[str, Any],
) -> None:
    expected = copy.deepcopy(cylinder)
    expected["initialization"]["maximum_aligned_scaled_gradient"] = 1e-10
    expected["solver"]["aligned_scaled_gradient_infinity_norm"] = 9e-11
    _ = accepted(expected, copy.deepcopy(expected))
    actual = copy.deepcopy(expected)
    actual["solver"]["aligned_scaled_gradient_infinity_norm"] = 1.1e-10
    rejected(actual, expected)
    rejected(actual, copy.deepcopy(actual))


def test_selected_feasibility_boundary_cannot_hide_inside_numeric_tolerance() -> None:
    expected = constrained()
    expected["solver"]["constraint_violation"] = 9e-12
    _ = accepted(expected, copy.deepcopy(expected))
    actual = copy.deepcopy(expected)
    actual["solver"]["constraint_violation"] = 1.1e-11
    rejected(actual, expected)
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "field,value",
    [
        ("rank", 11),
        ("constraint_rank", 12),
        ("tangent_dimension", 11),
        ("constraint_violation", 0.01),
        ("projected_gradient_norm", 0.01),
        ("termination", "rank-deficient"),
        ("coordinate_length", 7.0),
    ],
)
def test_selected_constraints_rank_stationarity_and_frame_remain_strict(
    field: str, value: object
) -> None:
    expected = constrained()
    actual = copy.deepcopy(expected)
    actual["solver"][field] = value
    rejected(actual, expected)


@pytest.mark.parametrize(
    "field,value",
    [
        ("objective", 1.0),
        ("valid", False),
        ("rank", 4),
        ("condition", 999999.0),
        ("termination", "rank-deficient"),
    ],
)
def test_selected_full_problem_assessment_cannot_change(
    cylinder: dict[str, Any], field: str, value: object
) -> None:
    actual = copy.deepcopy(cylinder)
    selected(actual)["assessment"][field] = value
    rejected(actual, cylinder)


@pytest.mark.parametrize(
    "field,value",
    [
        ("radius", 3.0),
        ("slope", 0.01),
        ("origin", [3.0, -3, 5]),
        ("axis", [1.0, 0, 0]),
        ("axial_domain", [-1.0, 10]),
    ],
)
def test_published_cylinder_geometry_and_support_cannot_hide_in_diagnostics(
    cylinder: dict[str, Any], field: str, value: object
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["analytic"][field] = value
    rejected(actual, cylinder)


@pytest.mark.parametrize(
    "path",
    [
        ("residuals", 0),
        ("solver", "scaled_projected_gradient_infinity_norm"),
        ("solver", "scaled_jacobian_condition"),
    ],
)
def test_published_residual_and_selected_certificate_changes_reject(
    cylinder: dict[str, Any], path: tuple[str, str | int]
) -> None:
    actual = copy.deepcopy(cylinder)
    actual[path[0]][path[1]] += 0.01
    rejected(actual, cylinder)


@pytest.mark.parametrize(
    "count", [-1, 0.5, True, "512", "Infinity", float("inf"), float("nan"), 513]
)
def test_attempt_counts_reject_invalid_values_even_if_both_reports_agree(
    cylinder: dict[str, Any], count: object
) -> None:
    actual = copy.deepcopy(cylinder)
    selected(actual)["evaluations"] = count
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "field,value", [("iterations", 101), ("evaluations", 513), ("evaluations", 2)]
)
def test_selected_solver_counts_link_to_actual_attempt_budget(
    cylinder: dict[str, Any], field: str, value: object
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["solver"][field] = value
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_fresh_attempts", 9),
        ("max_proposals", 17),
        ("version", "unreviewed-v3"),
        ("objective_atol", 1.0),
        ("objective_rtol", 0.1),
    ],
)
def test_actual_policy_cannot_raise_its_own_allowance(
    cylinder: dict[str, Any], field: str, value: object
) -> None:
    actual = copy.deepcopy(cylinder)
    recovery(actual)["policy"][field] = value
    rejected(actual, cylinder)


def test_matching_overbudget_and_extra_hint_records_reject(
    sphere: dict[str, Any],
) -> None:
    actual = copy.deepcopy(sphere)
    envelope = recovery(actual)
    hint = copy.deepcopy(envelope["attempts"][-1])
    hint["candidate"]["id"] = "second-hint"
    envelope["attempts"].append(hint)
    rejected(actual, copy.deepcopy(actual))
    actual = copy.deepcopy(sphere)
    envelope = recovery(actual)
    attempts: list[dict[str, Any]] = []
    envelope["attempts"] = attempts
    for index in range(9):
        attempt = copy.deepcopy(selected(sphere))
        attempt["candidate"]["id"] = "fresh-" + str(index)
        attempts.append(attempt)
    envelope["selected"] = "fresh-0"
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "change",
    [
        "missing-policy",
        "unknown-policy-key",
        "unknown-attempt-key",
        "unknown-solver-key",
        "missing-role",
        "missing-selected",
        "selected-not-valid",
        "screened-executed",
        "unknown-outcome",
        "wrong-selected-binding",
        "dropped-diagnostic",
    ],
)
def test_unknown_or_broken_recovery_schema_fails_closed(
    cylinder: dict[str, Any], sphere: dict[str, Any], change: str
) -> None:
    actual = copy.deepcopy(sphere if change == "screened-executed" else cylinder)
    envelope = recovery(actual)
    if change == "missing-policy":
        del envelope["policy"]
    elif change == "unknown-policy-key":
        envelope["policy"]["unreviewed"] = 1
    elif change == "unknown-attempt-key":
        envelope["attempts"][0]["hidden_quota"] = 1
    elif change == "unknown-solver-key":
        actual["solver"]["unreviewed_count"] = 1
    elif change == "missing-role":
        del envelope["attempts"][0]["role"]
    elif change == "missing-selected":
        envelope["selected"] = "does-not-exist"
    elif change == "selected-not-valid":
        selected(actual)["assessment"]["valid"] = False
    elif change == "screened-executed":
        envelope["attempts"][-1]["evaluations"] = 1
    elif change == "unknown-outcome":
        discarded(actual)["outcome"] = "mysterious-new-solver-state"
    elif change == "wrong-selected-binding":
        selected(actual)["candidate"]["binding_sha256"] = "0" * 64
    elif change == "dropped-diagnostic":
        del discarded(actual)["solver"]["objective_history"]
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "recipe-number",
        "ordered-ids",
        "hard-reference",
        "binding",
        "geometry",
        "residual",
        "membership",
        "public-error",
        "derived-copy",
    ],
)
def test_full_graph_semantics_remain_strict(graph: dict[str, Any], change: str) -> None:
    actual = copy.deepcopy(graph)
    nodes = actual["recipe"]["nodes"]
    if change == "source":
        nodes[0]["source_sha256"] = "0" * 64
    elif change == "recipe-number":
        # This authored scalar is below the numerical tolerance: intent remains
        # exact rather than being treated as a fitted geometry coefficient.
        nodes[-1]["axial_domain"] = [0.0, 1.0]
        expected = copy.deepcopy(actual)
        nodes[-1]["axial_domain"][1] += 1e-12
        rejected(actual, expected, profile="graph-snapshot")
        return
    elif change == "ordered-ids":
        nodes[1]["ids"][0], nodes[1]["ids"][1] = nodes[1]["ids"][1], nodes[1]["ids"][0]
    elif change == "hard-reference":
        nodes[-1]["axis"] = "other-provider"
    elif change == "binding":
        recovery(actual["results"]["cylinder"])["binding_sha256"] = "0" * 64
    elif change == "geometry":
        actual["results"]["cylinder"]["analytic"]["radius"] += 0.01
    elif change == "residual":
        actual["results"]["cylinder"]["residuals"][0] += 0.01
    elif change == "membership":
        actual["states"]["patch"] = "failed"
    elif change == "public-error":
        actual["errors"]["cylinder"] = "failed"
    elif change == "derived-copy":
        actual["derived"]["cylinder"]["analytic"]["radius"] += 0.01
    rejected(actual, graph, profile="graph-snapshot")


def test_default_strict_comparison_preserves_tolerance_and_types() -> None:
    _ = accepted({"geometry": [1.0 + 1e-11]}, {"geometry": [1.0]})
    rejected({"geometry": [1.0 + 1e-8]}, {"geometry": [1.0]})
    rejected({"count": True}, {"count": 1})
    rejected({"geometry": float("nan")}, {"geometry": float("nan")})
    rejected({"geometry": "NaN"}, {"geometry": 0.0})


def test_direct_sphere_without_recovery_retains_strict_solver_counts() -> None:
    points = np.array(
        [[1.0, 0, 0], [-1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0], [0, 0, -1]]
    )
    expected: dict[str, Any] = dict(fit_sphere(points, np.ones(6)))
    actual = copy.deepcopy(expected)
    actual["solver"]["evaluations"] += 1
    rejected(actual, expected, profile="numeric-fit")


def test_batch_retains_per_case_identity_and_full_diagnostic_ledger(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    discarded(actual)["solver"]["parameters"][0] += 1
    cases = [
        {
            "id": "native-worker",
            "actual": actual,
            "expected": cylinder,
            "profile": "values",
            "path": "case.original",
        },
        {
            "id": "replay",
            "actual": copy.deepcopy(actual),
            "expected": copy.deepcopy(cylinder),
            "profile": "values",
            "path": "case.replayed",
        },
    ]
    before = fingerprint(cases)
    receipt = parity.compare_batch(cases)
    assert receipt["complete"] is True
    paths = [item["path"] for item in receipt["diagnostic_differences"]]
    assert any("case.original" in path for path in paths)
    assert any("case.replayed" in path for path in paths)
    assert before == fingerprint(cases)


def test_cli_writes_failure_receipt_without_overwriting_existing_output(
    tmp_path: Path,
) -> None:
    helper = Path(parity.__file__).resolve()
    input_path, output_path = tmp_path / "batch.json", tmp_path / "receipt.json"
    _ = input_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "meaningful-mutation",
                        "actual": {"axis": [1, 0, 0]},
                        "expected": {"axis": [0, 0, 1]},
                        "profile": "values",
                    }
                ]
            }
        )
    )
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(helper),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert json.loads(output_path.read_text())["complete"] is False
    original = output_path.read_bytes()
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(helper),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode != 0
    assert output_path.read_bytes() == original


def test_cli_success_uses_no_project_or_numerical_imports(tmp_path: Path) -> None:
    helper = Path(parity.__file__).resolve()
    input_path, output_path = tmp_path / "batch.json", tmp_path / "receipt.json"
    _ = input_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "strict",
                        "actual": {"x": [1.0, 2.0]},
                        "expected": {"x": [1.0, 2.0]},
                    }
                ]
            }
        )
    )
    process = subprocess.run(
        [
            sys.executable,
            "-I",
            str(helper),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(output_path.read_text())["complete"] is True
    code = "import runpy,sys;runpy.run_path(sys.argv[1],run_name='import_only');assert not any(n in sys.modules for n in ['numpy','pydantic','OCP','experiments.feature_graph'])"
    isolated = subprocess.run(
        [sys.executable, "-I", "-c", code, str(helper)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert isolated.returncode == 0, isolated.stderr


def registration() -> dict[str, Any]:
    """Small real #264 scalar evidence plus its explicit score provenance."""
    result: dict[str, Any] = {
        "format": "scansor-rigid-occurrence-match-v1",
        "rotation": [[0.0, 0, -1], [0, 1, 0], [1, 0, 0]],
        "translation": [20.0, 10, 30],
        "rms": 5.381475864821221e-7,
        "normal_error": 2.5232341468753557e-17,
        "normal_scale": 0.9950487922828751,
        "runner_up_score": 0.12266395725160678,
        "ambiguity_denominator_floor": 1e-12,
        "geometric_rank": 3,
        "source_count": 60,
        "target_count": 60,
    }
    result["best_score"] = (
        result["rms"] + result["normal_scale"] * result["normal_error"]
    )
    result["ambiguity_ratio"] = result["runner_up_score"] / max(
        result["best_score"], result["ambiguity_denominator_floor"]
    )
    return result


def test_consistent_sensitive_registration_ratio_is_diagnostic_only() -> None:
    expected = registration()
    actual = copy.deepcopy(expected)
    actual["rms"] += 5e-16
    actual["best_score"] = (
        actual["rms"] + actual["normal_scale"] * actual["normal_error"]
    )
    actual["ambiguity_ratio"] = actual["runner_up_score"] / max(
        actual["best_score"], actual["ambiguity_denominator_floor"]
    )
    assert abs(actual["ambiguity_ratio"] - expected["ambiguity_ratio"]) > 1e-10 * (
        1 + expected["ambiguity_ratio"]
    )
    receipt = accepted(actual, expected)
    assert any(
        item["path"].endswith(".ambiguity_ratio")
        for item in receipt["diagnostic_differences"]
    )


@pytest.mark.parametrize(
    "field",
    [
        "best_score",
        "runner_up_score",
        "rms",
        "normal_error",
        "rotation",
        "translation",
        "geometric_rank",
        "source_count",
    ],
)
def test_registration_underlying_scores_transform_and_classification_remain_strict(
    field: str,
) -> None:
    expected = registration()
    actual = copy.deepcopy(expected)
    if field == "rotation":
        actual[field][0][0] = 0.01
    elif field == "translation":
        actual[field][0] += 0.01
    else:
        actual[field] += 0.01 if field not in ("geometric_rank", "source_count") else 1
    rejected(actual, expected)


@pytest.mark.parametrize(
    "change",
    [
        "stale-ratio",
        "score-arithmetic",
        "floor",
        "negative-best",
        "nonfinite-ratio",
        "missing-score",
    ],
)
def test_registration_diagnostic_must_be_consistent_with_its_own_scores(
    change: str,
) -> None:
    actual = registration()
    if change == "stale-ratio":
        actual["ambiguity_ratio"] *= 2
    elif change == "score-arithmetic":
        actual["best_score"] *= 2
        actual["ambiguity_ratio"] = actual["runner_up_score"] / actual["best_score"]
    elif change == "floor":
        actual["ambiguity_denominator_floor"] = 1e-6
        actual["ambiguity_ratio"] = actual["runner_up_score"] / 1e-6
    elif change == "negative-best":
        actual["best_score"] = -1
        actual["ambiguity_ratio"] = actual["runner_up_score"] / 1e-12
    elif change == "nonfinite-ratio":
        actual["ambiguity_ratio"] = float("inf")
    elif change == "missing-score":
        del actual["runner_up_score"]
    rejected(actual, copy.deepcopy(actual))


def test_selected_objective_endpoint_stays_strict_when_intermediate_path_is_diagnostic() -> (
    None
):
    expected = constrained()
    endpoint = expected["solver"]["objective_history"][-1]
    expected["solver"]["objective_history"] = [0.4, 0.3, endpoint]
    expected["solver"]["iterations"] = selected(expected)["assessment"][
        "iterations"
    ] = 2
    expected["solver"]["evaluations"] = 3
    selected(expected)["evaluations"] = 7
    expected["objective_history"] = [0.4, 0.3, endpoint]
    actual = copy.deepcopy(expected)
    actual["solver"]["objective_history"] = [0.4, 0.35, endpoint]
    actual["objective_history"] = [0.4, 0.35, endpoint]
    _ = accepted(actual, expected)
    actual["solver"]["objective_history"][-1] += 0.01
    actual["objective_history"][-1] += 0.01
    rejected(actual, expected)


def test_unknown_profile_and_recovery_algorithm_fail_closed(
    cylinder: dict[str, Any],
) -> None:
    rejected(cylinder, cylinder, profile="unreviewed-profile")
    actual = copy.deepcopy(cylinder)
    actual["initialization"]["algorithm"] = "future-unreviewed-fit-v99"
    rejected(actual, copy.deepcopy(actual))


def test_matching_raised_policy_caps_do_not_authorize_more_work(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    recovery(actual)["policy"]["attempt"]["max_evaluations"] = 513
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "field,value", [("radius", "NaN"), ("radius", True), ("axis", ["Infinity", 0, 0])]
)
def test_diagnostic_nonfinite_encoding_is_not_a_physical_geometry_schema(
    cylinder: dict[str, Any],
    field: str,
    value: object,
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["analytic"][field] = value
    rejected(actual, copy.deepcopy(actual))


def test_screened_stale_hint_can_retain_its_original_invalid_binding_and_chart(
    sphere: dict[str, Any],
) -> None:
    expected = copy.deepcopy(sphere)
    hint = recovery(expected)["attempts"][-1]
    hint["candidate"]["binding_sha256"] = "0" * 64
    hint["candidate"]["chart"] = "unrepresentable-center-hint"
    _ = accepted(expected, copy.deepcopy(expected))


def test_registration_exact_match_uses_the_existing_positive_denominator_floor() -> (
    None
):
    expected = registration()
    expected["rms"] = expected["normal_error"] = expected["best_score"] = 0.0
    expected["ambiguity_ratio"] = expected["runner_up_score"] / 1e-12
    _ = accepted(expected, copy.deepcopy(expected))


@pytest.mark.parametrize(
    "value", ["Infinity", "NaN", True, None, [0.0], float("inf"), float("nan"), -0.001]
)
def test_matching_malformed_published_rms_is_not_valid_numeric_evidence(
    cylinder: dict[str, Any],
    value: object,
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["weighted_rms"] = value
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize(
    "value", ["Infinity", "NaN", True, None, [0.0], float("inf"), float("nan")]
)
def test_matching_malformed_published_residual_elements_reject(
    cylinder: dict[str, Any],
    value: object,
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["residuals"][0] = value
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize("value", ["Infinity", 0.0, True, None, {"0": 0.0}])
def test_published_residual_vector_requires_an_array(
    cylinder: dict[str, Any],
    value: object,
) -> None:
    actual = copy.deepcopy(cylinder)
    actual["residuals"] = value
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize("value", ["Infinity", True, None, [0.0], float("inf")])
def test_matching_malformed_published_weighted_objective_endpoint_rejects(
    value: object,
) -> None:
    actual = constrained()
    actual["objective_history"][-1] = value
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize("value", ["Infinity", True, None, [0.0], float("inf")])
def test_selected_full_objective_never_uses_failed_trace_nonfinite_encoding(
    cylinder: dict[str, Any],
    value: object,
) -> None:
    actual = copy.deepcopy(cylinder)
    selected(actual)["assessment"]["objective"] = value
    rejected(actual, copy.deepcopy(actual))


def test_encoded_infinite_condition_remains_valid_in_discarded_rank_failure(
    cylinder: dict[str, Any],
) -> None:
    expected = copy.deepcopy(cylinder)
    failure = discarded(expected)
    failure["outcome"] = failure["solver"]["termination"] = "rank-deficient"
    failure["solver"]["rank"] = 4
    failure["solver"]["condition"] = "Infinity"
    _ = accepted(expected, copy.deepcopy(expected))
    assert failure["solver"]["condition"] == "Infinity"


def test_known_registration_cannot_drop_best_score_to_bypass_its_schema() -> None:
    actual = registration()
    del actual["best_score"]
    rejected(actual, copy.deepcopy(actual))


def test_successful_standalone_cylinder_cannot_drop_its_published_solver(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    del actual["solver"]
    rejected(actual, copy.deepcopy(actual))


def test_known_bounded_algorithm_cannot_drop_its_recovery_budget_envelope(
    cylinder: dict[str, Any],
) -> None:
    actual = copy.deepcopy(cylinder)
    del actual["initialization"]["recovery"]
    rejected(actual, copy.deepcopy(actual))


def test_genuine_group_members_share_recovery_but_joint_fit_requires_solver() -> None:
    source = native_scene_fixture()
    workspace = prepare_scan(
        io.BytesIO(source.raw),
        "independent-joint.ply",
        ScanLimits(100_000, 1000, 1000, 10_000_000),
    )
    joint = fit_group(
        workspace,
        [FitSelection("side", source.selections["cylinder"], "cylinder", None)],
        [FitSelection("cap", source.selections["plane"], "plane", None)],
    )
    assert "solver" in joint["fit"]
    for member in joint["surfaces"].values():
        assert "solver" not in member
        assert recovery(member) == recovery(joint["fit"])
    _ = accepted(joint, copy.deepcopy(joint))
    actual = copy.deepcopy(joint)
    del actual["fit"]["solver"]
    rejected(actual, copy.deepcopy(actual))


@pytest.mark.parametrize("legacy", [None, ["stale"]])
def test_canonical_geometry_precedes_redundant_legacy_parameters(
    cylinder: dict[str, Any], legacy: object
) -> None:
    expected = copy.deepcopy(cylinder)
    expected["parameters"] = legacy
    _ = accepted(expected, copy.deepcopy(expected))

    actual = copy.deepcopy(expected)
    actual["parameters"] = ["different-stale"]
    rejected(actual, expected)

    invalid = copy.deepcopy(expected)
    invalid["analytic"]["radius"] = "NaN"
    rejected(invalid, copy.deepcopy(invalid))
