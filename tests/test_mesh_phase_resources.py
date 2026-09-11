from __future__ import annotations

import threading
from pathlib import Path

import pytest

from scansor import mesh_resources
from scansor.mesh_controls import Control
from scansor.mesh_resources import ResourceMonitor
from tests.test_mesh_supervisor import control_object


def test_short_and_empty_phases_capture_boundaries_io_and_both_disk_peaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mesh_resources,
        "memory_snapshot",
        lambda: {"rss_bytes": 100, "os_peak_rss_bytes": 100},
    )
    events: list[dict[str, Control]] = []
    payload = b"x" * 70000
    with ResourceMonitor(
        200, tmp_path, callback=events.append, report_seconds=5
    ) as monitor:
        monitor.progress("write", 0, 1)
        with (tmp_path / "owned.bin").open("wb") as stream:
            _ = stream.write(payload)
        monitor.progress("write", 1, 1)
        monitor.progress("empty", 0, 0)
        boundary = [event for event in events if event["event"] == "phase"][-1]
        assert boundary["phase"] == "empty"
        assert boundary["previous_phase"] == {
            "phase": "write",
            "phase_ordinal": 1,
            "phase_started_ns": next(
                event["phase_started_ns"]
                for event in events
                if event["phase"] == "write"
            ),
            "completed": 1,
            "total": 1,
        }
        (tmp_path / "owned.bin").unlink()
        monitor.progress("finish", 0, 1)
        monitor.progress("finish", 1, 1)
    phases = [event for event in events if event["event"] == "phase"]
    assert [event["phase"] for event in phases] == [
        "initializing",
        "write",
        "empty",
        "finish",
    ]
    assert [event["phase_ordinal"] for event in phases] == [0, 1, 2, 3]
    assert [event["monotonic_ns"] for event in events] == sorted(
        int(str(event["monotonic_ns"])) for event in events
    )
    for event in events:
        assert (
            int(str(event["monotonic_ns"]))
            <= int(str(event["observation_started_ns"]))
            <= int(str(event["observation_finished_ns"]))
        )
    assert control_object(phases[2]["disk"])["logical_bytes"] == len(payload)
    assert control_object(events[-1]["disk"])["logical_bytes"] == 0
    assert events[-1]["sampled_disk_peak_logical_bytes"] == len(payload)
    assert int(str(events[-1]["sampled_disk_peak_allocated_bytes"])) >= len(payload)
    before, after = (
        control_object(phases[1]["process_io"]),
        control_object(phases[2]["process_io"]),
    )
    assert int(str(after["wchar"])) >= int(str(before["wchar"])) + len(payload)
    assert events[-1]["event"] == "final" and events[-1]["completed"] == 1


def test_phase_callback_failure_is_immediate_and_preserves_initiating_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mesh_resources,
        "memory_snapshot",
        lambda: {"rss_bytes": 100, "os_peak_rss_bytes": 100},
    )
    failure = RuntimeError("cannot retain phase evidence")

    def callback(record: dict[str, Control]) -> None:
        if record["phase"] == "failing":
            raise failure

    with (
        pytest.raises(RuntimeError) as caught,
        ResourceMonitor(200, tmp_path, callback=callback) as monitor,
    ):
        monitor.progress("failing", 0, 1)
        pytest.fail("phase callback was deferred")
    assert caught.value is failure
    assert not any(
        thread.name == "scansor-mesh-monitor" for thread in threading.enumerate()
    )


def test_callback_can_cancel_at_phase_boundary_without_deadlock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scansor.mesh_errors import MeshImportError

    monkeypatch.setattr(
        mesh_resources,
        "memory_snapshot",
        lambda: {"rss_bytes": 100, "os_peak_rss_bytes": 100},
    )
    monitor = ResourceMonitor(200, tmp_path)

    def callback(record: dict[str, Control]) -> None:
        if record["phase"] == "cancel-here":
            monitor.cancel()

    monitor.callback = callback
    with pytest.raises(MeshImportError, match="cancelled"), monitor:
        monitor.progress("cancel-here", 0, 1)
        monitor.check()


@pytest.mark.parametrize("event", ("phase", "final"))
def test_initial_and_final_callback_cancellation_cannot_return_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    from scansor.mesh_errors import MeshImportError

    monkeypatch.setattr(
        mesh_resources,
        "memory_snapshot",
        lambda: {"rss_bytes": 100, "os_peak_rss_bytes": 100},
    )
    monitor = ResourceMonitor(200, tmp_path)

    def callback(record: dict[str, Control]) -> None:
        if record["event"] == event:
            monitor.cancel()

    monitor.callback = callback
    with pytest.raises(MeshImportError, match="cancelled"), monitor:
        pass
    assert not any(
        thread.name == "scansor-mesh-monitor" for thread in threading.enumerate()
    )
