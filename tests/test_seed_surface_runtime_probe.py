"""Worker/native parity requires identical selection membership and policy."""

from copy import deepcopy

import pytest

from experiments.browser_runtime.probe import compare_surface_selections
from experiments.browser_runtime.seed_surface_probe import seed_surface_probe


def test_seed_surface_probe_refits_and_reopens_both_setback_cases() -> None:
    reports = seed_surface_probe()
    assert len(reports) == 2
    assert reports[0]["ids"] == reports[0]["fit"]["ids"]
    assert reports[1]["preview"]["policy"] == "seed-normal-envelope-v1"
    assert compare_surface_selections(deepcopy(reports), reports)


@pytest.mark.parametrize("field", ["ids", "reopened_ids", "preview", "recipe"])
def test_parity_rejects_changed_membership_policy_or_definition(field: str) -> None:
    reports = seed_surface_probe()
    changed = deepcopy(reports)
    if field in {"ids", "reopened_ids"}:
        changed[0][field][0] += 1
    elif field == "preview":
        changed[0][field]["samples"]["region"][0] += 1
    else:
        changed[0][field]["nodes"][3]["setback"] = 0.1
    with pytest.raises(AssertionError, match="surface_selections"):
        _ = compare_surface_selections(changed, reports)
