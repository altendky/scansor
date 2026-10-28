"""Generated native/worker reduction evidence must reject identity/geometry drift."""

from copy import deepcopy
from typing import Any

import pytest

from experiments.browser_runtime.probe import (
    compare_virtual_scan_transactions,
    compare_virtual_scans,
)
from experiments.browser_runtime.virtual_scan_probe import (
    virtual_scan_probe,
    virtual_scan_transactions_probe,
)


@pytest.fixture(scope="module")
def reports() -> list[dict[str, Any]]:
    return virtual_scan_probe()


def test_virtual_scan_probe_checks_reduction_weights_and_separate_sheets(
    reports: list[dict[str, Any]],
) -> None:
    assert len(reports) == 5
    assert reports[2]["opposed"] is False
    assert len(reports[2]["component_sizes"]) == 2
    assert len(reports[3]["component_sizes"]) == 2
    assert reports[4]["diagnostics"]["refinement_passes"] > 0
    assert reports[4]["diagnostics"]["split_vertices"] > 0
    assert reports[1]["total_area"] == pytest.approx(256 * reports[0]["total_area"])
    assert compare_virtual_scans(deepcopy(reports), reports)


@pytest.mark.parametrize(
    "key",
    ["derived_bytes_sha256", "derived_binding", "original_binding"],
)
def test_virtual_scan_parity_rejects_identity_drift(
    reports: list[dict[str, Any]], key: str
) -> None:
    changed = deepcopy(reports)
    changed[0][key] = "0" * 64
    with pytest.raises(AssertionError, match=key):
        _ = compare_virtual_scans(changed, reports)


def test_virtual_scan_parity_requires_exact_descriptor(
    reports: list[dict[str, Any]],
) -> None:
    changed = deepcopy(reports)
    changed[0]["derived_descriptor"]["source_bytes"] += 1
    with pytest.raises(AssertionError, match=r"derived_descriptor\.source_bytes"):
        _ = compare_virtual_scans(changed, reports)


def test_virtual_scan_parity_rejects_changed_observation_weights(
    reports: list[dict[str, Any]],
) -> None:
    changed = deepcopy(reports)
    changed[0]["weights"][0] *= 2
    with pytest.raises(AssertionError, match="weights"):
        _ = compare_virtual_scans(changed, reports)


def test_virtual_scan_parity_rejects_a_cross_sheet_triangle(
    reports: list[dict[str, Any]],
) -> None:
    changed = deepcopy(reports)
    changed[3]["triangles"][0][0] = len(changed[3]["positions"]) - 1
    with pytest.raises(AssertionError, match="triangles"):
        _ = compare_virtual_scans(changed, reports)


def test_virtual_scan_parity_rejects_changed_refinement_evidence(
    reports: list[dict[str, Any]],
) -> None:
    changed = deepcopy(reports)
    changed[-1]["diagnostics"]["refinement_passes"] += 1
    with pytest.raises(AssertionError, match=r"diagnostics\.refinement_passes"):
        _ = compare_virtual_scans(changed, reports)


@pytest.fixture(scope="module")
def transactions() -> dict[str, Any]:
    return virtual_scan_transactions_probe()


def test_transaction_probe_reconstructs_either_active_choice_and_refits_all_rows(
    transactions: dict[str, Any],
) -> None:
    assert transactions["original_vertices"] == len(transactions["full"]["membership"])
    assert transactions["reduced_vertices"] == len(transactions["coarse"]["membership"])
    assert len(transactions["reopens"]) == 2
    assert (
        transactions["reopens"][0]["active_binding"]
        != transactions["reopens"][1]["active_binding"]
    )
    assert transactions["returned_label"] == "Edited on original"
    assert compare_virtual_scan_transactions(deepcopy(transactions), transactions)


def test_transaction_probe_parity_rejects_changed_derivation(
    transactions: dict[str, Any],
) -> None:
    changed = deepcopy(transactions)
    changed["derivation"]["original"]["vertices"] += 1
    with pytest.raises(AssertionError, match=r"derivation\.original\.vertices"):
        _ = compare_virtual_scan_transactions(changed, transactions)


def test_transaction_probe_parity_rejects_changed_reopened_geometry(
    transactions: dict[str, Any],
) -> None:
    changed = deepcopy(transactions)
    changed["reopens"][0]["fit"]["parameters"][3] += 0.1
    with pytest.raises(AssertionError, match="parameters"):
        _ = compare_virtual_scan_transactions(changed, transactions)
