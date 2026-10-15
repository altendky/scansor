"""Frozen source reproducibility and strict packaged symmetry graph evidence."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime.probe import compare_symmetry_graphs
from experiments.browser_runtime.symmetry_probe import (
    SYMMETRY_FIXTURES,
    symmetry_graph_probe,
)
from tests.symmetry_fixture import raw_symmetry_fixture
from tests.test_scan_symmetry_graph import symmetry_recipe

CASES = [
    f"{kind}-{mode}"
    for kind in ("plane", "cylinder", "cone")
    for mode in ("rotation", "mirror")
]


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
) -> None:
    report = symmetry_graph_probe(Path.cwd(), case)
    encoded = json.loads(json.dumps(report, allow_nan=False))
    compare_symmetry_graphs([report], [encoded])


@pytest.mark.parametrize(
    "mutation",
    ["source", "ids", "residual", "support", "solver", "discarded_trace", "recipe"],
)
def test_symmetry_comparison_keeps_full_results_and_discarded_evidence(
    mutation: str,
) -> None:
    expected: dict[str, Any] = {
        "id": "case",
        "source_sha256": "source",
        "state": {
            "recipe": {"interval": [1.0, 2.0]},
            "results": {
                "joint": {
                    "surfaces": {
                        "member": {
                            "ids": [1, 2],
                            "residuals": [0.01, 0.02],
                            "analytic": {"axial_domain": [1.0, 2.0]},
                        }
                    },
                    "initialization": {
                        "recovery": {
                            "attempts": [
                                {
                                    "outcome": "rank-deficient",
                                    "solver": {"gradient": [0.5]},
                                }
                            ]
                        }
                    },
                    "solver": {"evaluations": 4},
                }
            },
        },
    }
    actual = deepcopy(expected)
    joint = actual["state"]["results"]["joint"]
    if mutation == "source":
        actual["source_sha256"] = "changed"
    elif mutation == "ids":
        joint["surfaces"]["member"]["ids"][0] = 3
    elif mutation == "residual":
        joint["surfaces"]["member"]["residuals"][0] += 0.01
    elif mutation == "support":
        joint["surfaces"]["member"]["analytic"]["axial_domain"][0] += 0.01
    elif mutation == "solver":
        joint["solver"]["evaluations"] += 1
    elif mutation == "discarded_trace":
        joint["initialization"]["recovery"]["attempts"][0]["solver"]["gradient"][0] += (
            0.01
        )
    else:
        # Authored numeric intent is exact, even below the derived float tolerance.
        actual["state"]["recipe"]["interval"][0] += 1e-12
    with pytest.raises(AssertionError, match="imported_symmetries"):
        compare_symmetry_graphs([actual], [expected])
