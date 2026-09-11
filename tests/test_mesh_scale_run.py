from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest

from experiments.mesh_scale_freeze import freeze_grid
from experiments.mesh_scale_generate import prepare_source
from experiments.mesh_scale_metrics import integer, object_record
from experiments.mesh_scale_run import published, run_case
from scansor.mesh_controls import Control, decode_control


def fixture(root: Path) -> tuple[Path, Path]:
    frozen, source = root / "frozen.json", root / "source.ply"
    _ = freeze_grid(root, frozen, width=5, height=7, noisy=True, chunk_rows=3)
    _ = prepare_source(frozen, root, source.name, root / "prepared.json", chunk_rows=11)
    return frozen, source


def test_complete_harness_checks_all_bytes_and_retains_scoped_resource_evidence(
    tmp_path: Path,
) -> None:
    frozen, source = fixture(tmp_path)
    result = run_case(
        frozen,
        source,
        tmp_path,
        "complete",
        tmp_path / "run.json",
        budget_bytes=512 * 1024 * 1024,
        storage="disk",
        chunk_rows=7,
    )
    assert result["status"] == "complete", result.get("error")
    assert decode_control((tmp_path / "run.json").read_bytes()) == result
    operations, checks = (
        object_record(result["operations"]),
        object_record(result["checks"]),
    )
    assert set(operations) == {"import", "display", "verify-display"}
    canonical = object_record(checks["canonical"])
    assert canonical["columns"] == object_record(result["expectation"])["columns"]
    assert len(object_record(canonical["columns"])) == 10
    assert canonical["vertices"] == 35 and canonical["faces"] == 48
    for value in operations.values():
        measured = object_record(value)
        assert measured["status"] == "complete", measured
        observations = object_record(measured["observations"])
        assert observations["whole_worker_rss_within_budget"] is True
        assert observations["work_directory_empty_after_exit"] is True
        assert observations["phase_coverage_complete"] is True
        assert type(observations["sample_gaps_including_edges_within_10ms"]) is bool
        disk = object_record(measured["disk"])
        assert integer(disk["samples"]) > 1
        assert (
            integer(object_record(disk["sampled_peaks"])["temporary_logical_bytes"]) > 0
        )
        assert object_record(disk["final"])["temporary_logical_bytes"] == 0
        phases = object_record(object_record(measured["phase_totals"])["phases"])
        assert phases and all(
            integer(object_record(phase)["elapsed_ns"]) >= 0
            for phase in phases.values()
        )
        telemetry = object_record(measured["telemetry"])
        raw = Path(str(telemetry["path"])).read_bytes()
        assert len(raw) == telemetry["bytes"]
        assert hashlib.sha256(raw).hexdigest() == telemetry["sha256"]
        events = [json.loads(line) for line in raw.splitlines()]
        assert len(events) == telemetry["records"]
        assert (
            sum(
                event["type"] == "progress" and event["record"]["event"] == "final"
                for event in events
            )
            == 1
        )
    first, identity = published(object_record(operations["import"]), "import")
    assert first.name == "mesh-import-" + identity
    assert object_record(checks["display"])["population"] == {
        "vertices_per_main_view": 35,
        "usable_faces_per_main_view": 48,
        "rejected_corners": 0,
    }
    assert not any((tmp_path / "complete/work").iterdir())
    before = (tmp_path / "run.json").read_bytes()
    with pytest.raises(FileExistsError):
        _ = run_case(
            frozen,
            source,
            tmp_path,
            "complete",
            tmp_path / "run.json",
            budget_bytes=512 * 1024 * 1024,
        )
    assert (tmp_path / "run.json").read_bytes() == before


@pytest.mark.parametrize("cancel", (False, True))
def test_failed_worker_is_retained_and_dependent_steps_do_not_run(
    tmp_path: Path, cancel: bool
) -> None:
    frozen, source = fixture(tmp_path)
    sibling = tmp_path / "unrelated.bin"
    _ = sibling.write_bytes(b"preserve")
    result = run_case(
        frozen,
        source,
        tmp_path,
        "failure",
        tmp_path / "failure.json",
        budget_bytes=512 * 1024 * 1024 if cancel else 1,
        cancel_phase="decode-vertices" if cancel else None,
    )
    assert result["status"] == "failed"
    operations = object_record(result["operations"])
    assert set(operations) == {"import"}
    measured = object_record(operations["import"])
    assert measured["status"] == "failed"
    failure = object_record(object_record(measured["outcome"])["failure"])
    assert failure["category"] == ("cancelled" if cancel else "resource")
    assert object_record(measured["cancellation"])["triggered"] is cancel
    assert (
        object_record(measured["observations"])["work_directory_empty_after_exit"]
        is True
    )
    assert sibling.read_bytes() == b"preserve"
    assert result["checks"] == {}
    assert decode_control((tmp_path / "failure.json").read_bytes()) == result


def test_mismatched_full_source_is_rejected_before_any_worker(tmp_path: Path) -> None:
    frozen, source = fixture(tmp_path)
    with source.open("r+b") as stream:
        _ = stream.seek(-1, 2)
        _ = stream.write(b"x")
    result = run_case(
        frozen,
        source,
        tmp_path,
        "mismatch",
        tmp_path / "mismatch.json",
        budget_bytes=512 * 1024 * 1024,
    )
    assert result["status"] == "failed"
    assert "complete source differs" in str(object_record(result["error"])["message"])
    assert result["operations"] == {}
    assert not (tmp_path / "mismatch").exists()


def test_full_column_comparison_rejects_a_self_consistent_wrong_expectation(
    tmp_path: Path,
) -> None:
    frozen, source = fixture(tmp_path)
    record = json.loads(frozen.read_bytes())
    record["expectation"]["columns"]["weight.bin"]["sha256"] = "0" * 64
    payload = (
        json.dumps(record["expectation"], sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    record["expectation_sha256"] = hashlib.sha256(payload).hexdigest()
    _ = frozen.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    result = run_case(
        frozen,
        source,
        tmp_path,
        "wrong-columns",
        tmp_path / "wrong-columns.json",
        budget_bytes=512 * 1024 * 1024,
        through="import",
    )
    assert result["status"] == "failed"
    assert "all canonical column bytes" in str(
        object_record(result["error"])["message"]
    )
    operations = object_record(result["operations"])
    assert object_record(operations["import"])["status"] == "complete"
    outcome = object_record(object_record(operations["import"])["outcome"])
    assert len(cast(list[Control], outcome["published"])) == 2
