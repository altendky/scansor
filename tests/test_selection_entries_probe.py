"""The shared numerical probe must exercise scan-entry semantics and publication."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime.probe import compare_numeric, compare_values
from experiments.browser_runtime.selection_entries_probe import selection_entries_probe


@pytest.fixture(scope="module")
def evidence() -> dict[str, Any]:
    return selection_entries_probe()


def test_probe_transfers_a_generated_entry_and_retains_one_shared_fit(
    evidence: dict[str, Any],
) -> None:
    assert evidence["source_vertices"] == 9 and evidence["target_vertices"] == 25
    assert evidence["source_membership"] == [0, 1, 3, 4]
    assert evidence["entry_volume_counts"] == [1, 0]
    assert evidence["fit_count"] == 1
    assert evidence["waiting"] == {
        "states": {"patch": "needs_input", "plane": "needs_input"},
        "errors": {},
        "action": "choose_source_volume",
        "settled": True,
    }
    assert len(evidence["transferred"]["membership"]) == 15
    assert evidence["transferred"]["coverage"]["fraction"] == pytest.approx(1)
    assert evidence["transferred"]["fit"]["weighted_rms"] == 0
    assert (
        evidence["preserved_original"]
        and evidence["generated_only"]
        and evidence["replay_equal"]
    )
    assert all(evidence["refresh"].values())
    _ = compare_values(evidence, deepcopy(evidence), "selection_entries")
    serialized = json.dumps(evidence, allow_nan=False)
    assert "sha256" not in serialized and "authoring_fingerprint" not in serialized


@pytest.mark.parametrize(
    "mutation", ["membership", "waiting", "receipt", "coverage", "geometry", "replay"]
)
def test_parity_rejects_changed_scan_entry_behavior(
    evidence: dict[str, Any], mutation: str
) -> None:
    expected: dict[str, Any] = {
        "fixture_sha256": "fixture",
        "counts": {},
        "source_sha256": "source",
        "model_sha256": "model",
        "graph": {
            "recipe": {},
            "token": "token",
            "states": {},
            "errors": {},
            "diagnostics": {},
            "results": {},
        },
        "imported_planes": [],
        "imported_spheres": [],
        "imported_cylinders": [],
        "imported_cones": [],
        "support_transfers": [],
        "imported_symmetries": [],
        "analytic_geometry": {},
        "selection_volumes": [],
        "selection_entries": evidence,
    }
    actual = deepcopy(expected)
    entry = actual["selection_entries"]
    if mutation == "membership":
        entry["transferred"]["membership"].append(24)
    elif mutation == "waiting":
        entry["waiting"]["states"]["plane"] = "failed"
    elif mutation == "receipt":
        entry["refresh"]["request_receipt_matches"] = False
    elif mutation == "coverage":
        entry["transferred"]["coverage"]["fraction"] = 0.5
    elif mutation == "geometry":
        entry["transferred"]["fit"]["plane_equation"][0] = 0.25
    else:
        entry["replay_equal"] = False
    with pytest.raises(AssertionError, match="selection_entries"):
        _ = compare_numeric(actual, expected)


def test_probe_is_shipped_and_redistribution_binding_matches_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = root / "experiments/browser_runtime/runtime-lock.json"
    lock = json.loads(runtime.read_text())
    assert (
        "experiments/browser_runtime/selection_entries_probe.py"
        in lock["sources"]["numeric"]
    )
    inventory = json.loads(
        (root / "scripts/browser-redistribution-lock.json").read_text()
    )
    assert (
        inventory["bindings"]["runtime_lock_sha256"]
        == hashlib.sha256(runtime.read_bytes()).hexdigest()
    )
