"""Frozen source reproducibility and strict packaged symmetry graph evidence."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime.probe import (
    compare_local_replay,
    compare_symmetry_graphs,
)
from experiments.browser_runtime.semantic_parity import SemanticParityError
from experiments.browser_runtime.symmetry_probe import (
    SYMMETRY_FIXTURES,
    symmetry_graph_probe,
    symmetry_graph_probes,
)
from tests.symmetry_fixture import raw_symmetry_fixture
from tests.test_scan_symmetry_graph import symmetry_recipe

CASES = [
    f"{kind}-{mode}"
    for kind in ("plane", "cylinder", "cone")
    for mode in ("rotation", "mirror")
]


@pytest.fixture(scope="module")
def symmetry_evidence() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    replay: list[dict[str, Any]] = []
    reports = symmetry_graph_probes(
        Path.cwd(), compare_replay=compare_local_replay, replay_evidence=replay
    )
    return reports, replay


@pytest.mark.parametrize("case", CASES)
def test_frozen_symmetry_source_and_authored_recipe_are_reproducible(case: str) -> None:
    directory = Path(SYMMETRY_FIXTURES)
    raw = (directory / f"{case}.ply").read_bytes()
    record = json.loads((directory / f"{case}.json").read_text())
    fixture = raw_symmetry_fixture(**record["generation"])
    assert fixture.raw == raw
    assert len(raw) == record["source_bytes"]
    assert hashlib.sha256(raw).hexdigest() == record["source_sha256"]
    assert fixture.ids == record["ids"]
    assert fixture.truth == record["truth"]
    np.testing.assert_allclose(
        fixture.incident_weights, record["incident_weights"], rtol=1e-14, atol=0
    )
    assert fixture.quantization_bound == record["quantization_bound"]
    assert (
        symmetry_recipe(fixture, **record["recipe_options"]).model_dump(mode="json")
        == record["recipe"]
    )


@pytest.mark.parametrize("case", CASES)
def test_actual_imported_symmetry_probe_checks_rows_support_truth_and_replay(
    case: str,
    symmetry_evidence: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    reports, replay = symmetry_evidence
    report = next(value for value in reports if value["id"] == case)
    original = deepcopy(report)
    encoded = json.loads(json.dumps(report, allow_nan=False))
    receipts = compare_symmetry_graphs([report], [encoded])
    assert len(receipts) == 1
    assert receipts[0]["semantic_complete"] is True
    assert receipts[0]["budgets_validated"] is True
    assert receipts[0]["budget_records_validated"] > 0
    assert [value["id"] for value in reports] == CASES
    assert [value["path"] for value in replay] == [
        f"symmetry.{name}.replay" for name in CASES
    ]
    evidence = replay[CASES.index(case)]
    assert evidence["receipt"]["semantic_complete"] is True
    assert evidence["receipt"]["budgets_validated"] is True
    assert evidence["receipt"]["budget_records_validated"] > 0
    for side in ("actual", "expected"):
        assert {
            "memberships",
            "derived",
            "input_catalogue",
            "input_requirements",
        } <= evidence[side].keys()
        assert (
            json.loads(json.dumps(evidence[side], allow_nan=False)) == encoded["state"]
        )
    assert report == original


@pytest.mark.parametrize(
    "mutation",
    [
        "source",
        "ids",
        "residual",
        "support",
        "solver",
        "computed-start",
        "over-budget",
        "membership",
        "derived",
        "recipe",
    ],
)
def test_symmetry_comparison_keeps_full_results_and_discarded_evidence(
    mutation: str,
    symmetry_evidence: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    case = "cone-rotation" if mutation == "recipe" else "cylinder-rotation"
    expected = next(value for value in symmetry_evidence[0] if value["id"] == case)
    assert (
        compare_symmetry_graphs([expected], [deepcopy(expected)])[0]["complete"] is True
    )
    actual = deepcopy(expected)
    joint = actual["state"]["results"]["joint"]
    member = joint["surfaces"]["member_0"]
    if mutation == "source":
        actual["source_sha256"] = "changed"
    elif mutation == "ids":
        member["ids"][0] += 1
    elif mutation == "residual":
        member["residuals"][0] += 0.01
    elif mutation == "support":
        member["analytic"]["axial_domain"][0] += 0.01
    elif mutation == "solver":
        joint["fit"]["solver"]["rank"] += 1
    elif mutation == "computed-start":
        # The cone-only six-coordinate exception must not leak into joint charts.
        recovery = joint["initialization"]["recovery"]
        attempt = next(
            value
            for value in recovery["attempts"]
            if value["candidate"]["id"] != recovery["selected"]
        )
        attempt["candidate"]["parameters"][0] += 1e-4
    elif mutation == "over-budget":
        joint["initialization"]["recovery"]["attempts"][0]["evaluations"] = 513
    elif mutation == "membership":
        actual["state"]["memberships"]["member_0_selection"] = []
    elif mutation == "derived":
        del actual["state"]["derived"]
    else:
        # Authored numeric intent is exact, even below the derived float tolerance.
        node = next(
            value
            for value in actual["state"]["recipe"]["nodes"]
            if value["id"] == "member_0"
        )
        node["axial_domain"][0] += 1e-12
    before = deepcopy(actual)
    with pytest.raises(AssertionError, match="imported_symmetries"):
        _ = compare_symmetry_graphs([actual], [expected])
    assert actual == before


def test_allowed_discarded_callbacks_remain_in_the_full_symmetry_receipt(
    symmetry_evidence: tuple[list[dict[str, Any]], list[dict[str, Any]]],
) -> None:
    expected = next(
        value for value in symmetry_evidence[0] if value["id"] == "cylinder-rotation"
    )
    actual = deepcopy(expected)
    recovery = actual["state"]["results"]["joint"]["initialization"]["recovery"]
    attempt = next(
        value
        for value in recovery["attempts"]
        if value["candidate"]["id"] != recovery["selected"]
        and value["outcome"] == "locally-converged"
        and value["evaluations"] < 512
    )
    attempt["evaluations"] += 1
    before = deepcopy(actual)
    receipt = compare_symmetry_graphs([actual], [expected])[0]
    assert receipt["complete"] is True
    assert receipt["budgets_validated"] is True
    assert any(
        entry["path"].endswith(".evaluations")
        and entry["actual"] == attempt["evaluations"]
        and entry["reason"] == "realized callbacks within unchanged quota"
        for entry in receipt["diagnostic_differences"]
    )
    assert actual == before


@pytest.mark.parametrize("field", ["memberships", "derived"])
def test_symmetry_replay_failure_retains_complete_independent_graphs(
    field: str,
) -> None:
    evidence: list[dict[str, Any]] = []

    def fail_replay(
        actual: dict[str, Any],
        expected: dict[str, Any],
        path: str,
        collector: list[dict[str, Any]] | None,
    ) -> None:
        changed = deepcopy(actual)
        del changed[field]
        compare_local_replay(changed, expected, path, collector)

    with pytest.raises(SemanticParityError) as failure:
        _ = symmetry_graph_probe(
            Path.cwd(),
            "cylinder-mirror",
            compare_replay=fail_replay,
            replay_evidence=evidence,
        )
    assert len(evidence) == 1
    retained = evidence[0]
    assert retained["path"] == "symmetry.cylinder-mirror.replay"
    assert field not in retained["actual"] and field in retained["expected"]
    assert "input_catalogue" in retained["actual"]
    assert "results" in retained["expected"]
    assert retained["receipt"] == failure.value.receipt
    assert retained["receipt"]["complete"] is False
