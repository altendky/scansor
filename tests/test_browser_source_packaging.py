"""Source capabilities and frozen harness inputs fail closed before execution."""

import json
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runpy.run_path(str(ROOT / "scripts/build-browser-runtime.py"))
INPUTS = runpy.run_path(
    str(ROOT / "experiments/browser_static/source_import_expected.py")
)


@pytest.mark.parametrize(
    "fault",
    ["old", "missing", "bool", "float", "zero", "unknown", "huge", "zero-faces"],
)
def test_worker_source_profile_rejects_malformed_or_old_contract(fault: str) -> None:
    protocol = RUNTIME["worker_protocol"]()
    if fault == "old":
        protocol["version"] = 4
    elif fault == "missing":
        del protocol["source_import"]
    elif fault == "bool":
        protocol["source_import"]["limits"]["max_vertices"] = True
    elif fault == "float":
        protocol["source_import"]["limits"]["max_source_bytes"] = 1.5
    elif fault == "zero":
        protocol["source_import"]["limits"]["chunk_bytes"] = 0
    elif fault == "huge":
        protocol["source_import"]["limits"]["max_vertices"] = 2**53
    elif fault == "zero-faces":
        protocol["source_import"]["limits"]["max_faces"] = 0
    else:
        protocol["source_import"]["limits"]["max_threads"] = 2
    with pytest.raises(ValueError):
        RUNTIME["validate_worker_protocol"](protocol)


def test_source_profile_is_detached_and_has_shared_owned_closure() -> None:
    protocol = RUNTIME["worker_protocol"]()
    RUNTIME["validate_worker_protocol"](protocol)
    protocol["source_import"]["limits"]["max_vertices"] = 7
    assert RUNTIME["worker_protocol"]()["source_import"]["limits"]["max_vertices"] != 7
    lock = json.loads(
        (ROOT / "experiments/browser_runtime/runtime-lock.json").read_text()
    )
    numeric = lock["sources"]["numeric"]
    assert {
        "experiments/scan_project.py",
        "experiments/scan_workspace.py",
        "src/scansor/mesh_ply.py",
    } <= set(numeric)
    assert "experiments/native_scan_import.py" not in numeric


@pytest.mark.parametrize(
    "fault", ["old", "missing", "bool", "float", "unknown", "limit"]
)
def test_selection_volume_profile_requires_exact_bounded_contract(fault: str) -> None:
    protocol = RUNTIME["worker_protocol"]()
    assert protocol["version"] == 10
    assert protocol["selection_volumes"] == {"version": 1, "max_preview_cells": 2048}
    if fault == "old":
        protocol["version"] = 9
    elif fault == "missing":
        del protocol["selection_volumes"]
    elif fault == "bool":
        protocol["selection_volumes"]["version"] = True
    elif fault == "float":
        protocol["selection_volumes"]["max_preview_cells"] = 2048.0
    elif fault == "unknown":
        protocol["selection_volumes"]["unexpected"] = True
    else:
        protocol["selection_volumes"]["max_preview_cells"] = 2049
    with pytest.raises(ValueError):
        RUNTIME["validate_worker_protocol"](protocol)


@pytest.mark.parametrize(
    "fault",
    [
        "old",
        "missing",
        "disabled",
        "bool",
        "number",
        "unknown",
        "limit-float",
        "limit-missing",
    ],
)
def test_worker_actions_profile_requires_the_exact_supported_contract(
    fault: str,
) -> None:
    protocol = RUNTIME["worker_protocol"]()
    if fault == "old":
        protocol["version"] = 5
    elif fault == "missing":
        del protocol["source_actions"]
    elif fault == "disabled":
        protocol["source_actions"]["supported"] = False
    elif fault == "bool":
        protocol["source_actions"]["version"] = True
    elif fault == "number":
        protocol["source_actions"]["supported"] = 1
    elif fault == "limit-float":
        protocol["source_actions"]["limits"]["max_json_bytes"] = float(32 * 1024 * 1024)
    elif fault == "limit-missing":
        del protocol["source_actions"]["limits"]["max_volume_expanded_bytes"]
    else:
        protocol["source_actions"]["unrecognized"] = True
    with pytest.raises(ValueError):
        RUNTIME["validate_worker_protocol"](protocol)


def test_source_fixture_generator_keeps_preregistered_bytes(tmp_path: Path) -> None:
    INPUTS["inputs"](ROOT, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert {item["name"]: item["sha256"] for item in manifest["cases"]} == INPUTS[
        "FROZEN"
    ]
