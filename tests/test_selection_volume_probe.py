"""The numerical runtime evidence must compare volume membership and diagnostics."""

from copy import deepcopy
from typing import Any

import pytest

from experiments.browser_runtime.probe import compare_numeric
from experiments.browser_runtime.selection_volume_probe import selection_volume_probe


def test_surface_volume_probe_preserves_painted_hole_and_replays() -> None:
    reports = selection_volume_probe()
    assert len(reports) == 4
    assert {item["kind"] for item in reports} == {"mesh", "plane"}
    for report in reports:
        assert report["source_ids"]
        assert not report["reverse_ids"]


@pytest.mark.parametrize("changed", ["dense_ids", "full_coverage", "volume"])
def test_worker_parity_rejects_changed_selection_volume(changed: str) -> None:
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
        "selection_volumes": [
            {"dense_ids": [0, 1], "full_coverage": 1.0, "volume": {"outward": 0.1}}
        ],
    }
    actual = deepcopy(expected)
    if changed == "dense_ids":
        actual["selection_volumes"][0][changed].append(2)
    elif changed == "full_coverage":
        actual["selection_volumes"][0][changed] = 0.5
    else:
        actual["selection_volumes"][0][changed]["outward"] = 0.2
    with pytest.raises(AssertionError, match="selection_volumes"):
        _ = compare_numeric(actual, expected)
