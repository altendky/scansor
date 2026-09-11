from __future__ import annotations

from pathlib import Path

import pytest

from experiments.mesh_scale_fan_freeze import freeze_fan
from experiments.mesh_scale_fan_generate import prepare_fan_source
from experiments.mesh_scale_metrics import object_record
from experiments.mesh_scale_run import run_case
from scansor.mesh_controls import decode_control


@pytest.mark.parametrize("adverse", (False, True))
def test_frozen_native_fan_runs_complete_canonical_and_display_checks(
    tmp_path: Path, adverse: bool
) -> None:
    frozen, prepared = tmp_path / "fan.json", tmp_path / "prepared.json"
    _ = freeze_fan(tmp_path, frozen, radius=16, adverse=adverse, chunk_rows=11)
    source = prepare_fan_source(frozen, tmp_path, "source.ply", prepared, chunk_rows=7)
    assert source["status"] == "verified-source"
    assert source["actual"] == source["expected"]
    run = run_case(
        frozen,
        tmp_path / "source.ply",
        tmp_path,
        "case",
        tmp_path / "run.json",
        budget_bytes=512 * 1024 * 1024,
        storage="disk",
        chunk_rows=7,
    )
    assert run["revision"] == "mesh-scale-fan-run-v1"
    assert run["status"] == "complete", run.get("error")
    checks = object_record(run["checks"])
    canonical, display = (
        object_record(checks["canonical"]),
        object_record(checks["display"]),
    )
    categories = object_record(
        object_record(canonical["import_summary"])["face_category_counts"]
    )
    assert categories["usable"] == 64 + (4 if adverse else 0)
    assert categories["repeated-index"] == (2 if adverse else 0)
    assert categories["zero-computed-area"] == (2 if adverse else 0)
    assert object_record(display["population"])["rejected_corners"] == (
        12 if adverse else 0
    )
    assert decode_control((tmp_path / "run.json").read_bytes()) == run
    original = (tmp_path / "source.ply").read_bytes()
    with pytest.raises(FileExistsError):
        _ = prepare_fan_source(frozen, tmp_path, "source.ply", tmp_path / "failed.json")
    assert (tmp_path / "source.ply").read_bytes() == original
    assert (
        object_record(decode_control((tmp_path / "failed.json").read_bytes()))["status"]
        == "failed"
    )
