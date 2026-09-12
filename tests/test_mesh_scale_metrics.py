from __future__ import annotations

import threading
from pathlib import Path

import pytest

from experiments import mesh_scale_metrics
from experiments.mesh_scale_metrics import (
    DiskSampler,
    PhaseTotals,
    file_sample,
    integer,
    object_record,
)
from scansor.mesh_controls import Control


def test_file_accounting_includes_moved_publication_and_sparse_allocation(
    tmp_path: Path,
) -> None:
    work, out = tmp_path / "work", tmp_path / "out"
    work.mkdir()
    out.mkdir()
    source = work / "stage"
    source.mkdir()
    with (source / "column.bin").open("wb") as stream:
        _ = stream.seek(16 * 1024 * 1024)
        _ = stream.write(b"x")
    before = file_sample(tmp_path)
    assert before["logical_bytes"] == 16 * 1024 * 1024 + 1
    assert integer(before["allocated_bytes"]) < integer(before["logical_bytes"])
    assert before["published_logical_bytes"] == 0
    _ = source.rename(out / ("mesh-import-" + "a" * 64))
    after = file_sample(tmp_path)
    assert after["logical_bytes"] == before["logical_bytes"]
    assert after["temporary_logical_bytes"] == 0
    assert after["published_logical_bytes"] == before["logical_bytes"]
    assert after["published_allocated_bytes"] == before["allocated_bytes"]


def test_disk_sampler_propagates_thread_failure_and_joins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampled, failure = threading.Event(), OSError("measurement denied")
    actual = mesh_scale_metrics.file_sample
    calls = 0

    def fail(root: Path) -> dict[str, Control]:
        nonlocal calls
        calls += 1
        if calls > 1:
            sampled.set()
            raise failure
        return actual(root)

    monkeypatch.setattr(mesh_scale_metrics, "file_sample", fail)
    disk = DiskSampler(tmp_path, interval_seconds=0.001)
    with pytest.raises(OSError) as caught, disk:
        assert sampled.wait(2)
    assert caught.value is failure
    assert disk.record()["observation_error"] == "measurement denied"
    assert not any(
        thread.name == "scansor-scale-disk" for thread in threading.enumerate()
    )


def test_disk_cleanup_preserves_initiating_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = RuntimeError("initiating error")

    def fail(_root: Path) -> dict[str, Control]:
        raise OSError("observation also failed")

    disk = DiskSampler(tmp_path)
    with pytest.raises(RuntimeError) as caught, disk:
        monkeypatch.setattr(mesh_scale_metrics, "file_sample", fail)
        raise primary
    assert caught.value is primary
    assert disk.record()["observation_error"] == "observation also failed"


def test_phase_aggregation_closes_repeated_phases_and_keeps_signed_io() -> None:
    phases = PhaseTotals()
    events: list[dict[str, Control]] = []
    for ordinal, name in enumerate(("a", "b", "a")):
        events.append(
            {
                "event": "phase",
                "phase": name,
                "phase_ordinal": ordinal,
                "monotonic_ns": 100 * ordinal,
                "completed": 0,
                "total": 10,
                "process_io": {
                    "rchar": 100 * ordinal,
                    "cancelled_write_bytes": 100 - ordinal,
                },
                "previous_phase": None
                if ordinal == 0
                else {
                    "phase": "a" if ordinal == 1 else "b",
                    "phase_ordinal": ordinal - 1,
                    "completed": 10,
                    "total": 10,
                },
            }
        )
    events.append(
        {
            **events[-1],
            "event": "final",
            "monotonic_ns": 300,
            "completed": 10,
            "process_io": {"rchar": 300, "cancelled_write_bytes": 97},
        }
    )
    for event in events:
        phases.accept(event)
    result = object_record(phases.record()["phases"])
    a = object_record(result["a"])
    assert a["occurrences"] == 2 and a["elapsed_ns"] == 200
    assert a["io_delta"] == {"rchar": 200, "cancelled_write_bytes": -2}
    assert a["last_completed"] == a["last_total"] == 10
    assert phases.record()["final_observation_seen"] is True
    with pytest.raises(ValueError, match="after its final"):
        phases.accept(events[-1])
