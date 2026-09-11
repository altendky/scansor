from __future__ import annotations

import errno
import io
import os
import selectors
import signal
import struct
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import psutil
import pytest

from scansor import mesh_supervisor
from scansor.errors import ScansorError
from scansor.mesh_artifacts import open_import
from scansor.mesh_controls import (
    MAX_CONTROL_BYTES,
    Control,
    decode_control,
    encode_control,
)
from scansor.mesh_errors import MeshImportError
from scansor.mesh_recipes import SMALL_RECIPES, write_recipe
from scansor.mesh_supervisor import discover_staging, run_worker
from scansor.mesh_worker_protocol import FrameReader, FrameWriter
from scansor.mesh_worker_request import WorkerRequest
from scansor.mesh_workspace import remove_owned_workspace
from tests.test_mesh_artifacts import artifact_state


def worker_request(root: Path, *, budget: int = 512 * 1024 * 1024) -> WorkerRequest:
    source = root / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["unequal-adjacent-v1"])
    return WorkerRequest(
        "import", source, destination=root, budget_bytes=budget, chunk_rows=2
    )


def test_slow_progress_callback_is_visible_in_actual_rss_sample_gaps(
    tmp_path: Path,
) -> None:
    delayed = False

    def slow(_record: dict[str, Control]) -> None:
        nonlocal delayed
        if not delayed:
            delayed = True
            time.sleep(0.04)

    result = run_worker(worker_request(tmp_path), tmp_path, progress=slow)
    assert result["status"] == "complete"
    edges = control_object(control_object(result["supervision"])["sampling_edges"])
    assert int(str(edges["largest_gap_including_edges_ns"])) >= 40_000_000
    assert int(str(edges["gaps_over_10ms_including_edges"])) >= 1


def test_exception_reaper_keeps_kernel_usage_and_reports_unsampled_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_selector() -> None:
        raise RuntimeError("injected selector startup failure")

    monkeypatch.setattr(selectors, "DefaultSelector", fail_selector)
    result = run_worker(worker_request(tmp_path), tmp_path)
    assert result["status"] == "failed"
    supervision = control_object(result["supervision"])
    assert supervision["exit_code"] == -signal.SIGKILL
    assert supervision["samples"] == 0
    assert "system_cpu_ns" in control_object(supervision["kernel_usage"])
    edges = control_object(supervision["sampling_edges"])
    assert edges["first_sample_ns"] is None and edges["reaped_ns"] is not None
    with pytest.raises(ChildProcessError):
        _ = os.waitpid(int(str(supervision["pid"])), os.WNOHANG)


def control_object(value: Control) -> dict[str, Control]:
    assert isinstance(value, dict)
    return value


def fault_command(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    def command(descriptor: int, publication_descriptor: int | None) -> list[str]:
        result = [
            sys.executable,
            "-m",
            "tests.mesh_fault_worker",
            mode,
            str(descriptor),
        ]
        if publication_descriptor is not None:
            result.append(str(publication_descriptor))
        return result

    monkeypatch.setattr(
        mesh_supervisor,
        "_worker_command",
        command,
    )


def test_fresh_import_and_verification_workers(tmp_path: Path) -> None:
    request = worker_request(tmp_path)
    events: list[dict[str, Control]] = []
    result = run_worker(request, tmp_path, progress=events.append)
    assert result["status"] == "complete" and result["failure"] is None
    stages = cast(list[dict[str, Control]], result["published"])
    assert len(stages) == 2 and [stage["kind"] for stage in stages] == [
        "import",
        "contribution",
    ]
    first, second = Path(str(stages[0]["path"])), Path(str(stages[1]["path"]))
    before = artifact_state(first), artifact_state(second)
    verified = run_worker(
        WorkerRequest(
            "verify",
            first,
            contribution=second,
            expected_import_id=str(stages[0]["identity"]),
            expected_contribution_id=str(stages[1]["identity"]),
            storage="disk",
            chunk_rows=7,
        ),
        tmp_path,
    )
    assert verified["status"] == "complete"
    assert (
        control_object(control_object(verified["worker_report"])["result"])["status"]
        == "verified"
    )
    assert (artifact_state(first), artifact_state(second)) == before
    for report in (result, verified):
        supervision = control_object(report["supervision"])
        assert supervision["exit_code"] == 0
        assert type(supervision["pid"]) is int and supervision["pid"] != os.getpid()
        assert 0 < int(str(supervision["kernel_peak_rss_bytes"])) < request.budget_bytes
        assert int(str(supervision["requested_sample_interval_ns"])) <= 10_000_000
        assert int(str(supervision["samples"])) > 0
        edges = control_object(supervision["sampling_edges"])
        assert (
            0
            <= int(str(edges["initial_gap_ns"]))
            <= int(str(edges["largest_gap_including_edges_ns"]))
        )
        assert (
            0
            <= int(str(edges["terminal_gap_ns"]))
            <= int(str(edges["largest_gap_including_edges_ns"]))
        )
        assert (
            int(str(edges["launch_started_ns"]))
            <= int(str(edges["launch_returned_ns"]))
            <= int(str(edges["first_sample_ns"]))
            <= int(str(edges["last_sample_ns"]))
            <= int(str(edges["reaped_ns"]))
        )
        worker_memory = control_object(
            control_object(report["worker_report"])["memory_after_cleanup"]
        )
        assert int(str(supervision["kernel_peak_rss_bytes"])) >= int(
            str(worker_memory["os_peak_rss_bytes"])
        )
        with pytest.raises(ChildProcessError):
            _ = os.waitpid(int(str(supervision["pid"])), os.WNOHANG)
    assert (
        control_object(result["supervision"])["pid"]
        != control_object(verified["supervision"])["pid"]
    )
    assert events[0]["type"] == "started"
    assert events[-1]["type"] == "result"
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize(
    "mode,category",
    (
        ("crash-before", "worker-exit"),
        ("exit-zero", "worker-exit"),
        ("oversized-frame", "worker-protocol"),
        ("truncated-frame", "worker-protocol"),
        ("wrong-start", "worker-protocol"),
        ("double-result", "worker-protocol"),
        ("crash-after-result", "worker-exit"),
    ),
)
def test_abnormal_process_and_protocol_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, category: str
) -> None:
    request = worker_request(tmp_path)
    sentinel = tmp_path / "keep.txt"
    _ = sentinel.write_bytes(b"unrelated")
    fault_command(monkeypatch, mode)
    result = run_worker(request, tmp_path)
    assert result["status"] == "failed"
    assert control_object(result["failure"])["category"] == category
    assert int(str(control_object(result["supervision"])["kernel_peak_rss_bytes"])) > 0
    assert sentinel.read_bytes() == b"unrelated"
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_sigkill_after_import_preserves_only_complete_published_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = worker_request(tmp_path)
    fault_command(monkeypatch, "crash-after-import")
    result = run_worker(request, tmp_path)
    assert (
        result["status"] == "failed"
        and control_object(result["failure"])["category"] == "worker-exit"
    )
    assert control_object(result["supervision"])["exit_code"] == -signal.SIGKILL
    stages = cast(list[dict[str, Control]], result["published"])
    assert len(stages) == 1 and stages[0]["kind"] == "import"
    with open_import(
        Path(str(stages[0]["path"])), expected_id=str(stages[0]["identity"])
    ) as imported:
        assert imported.vertices == 4 and imported.faces == 2
    assert not list(tmp_path.glob("mesh-contribution-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_cancellation_forces_unresponsive_worker_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = worker_request(tmp_path)
    fault_command(monkeypatch, "ignore-term")
    monkeypatch.setattr(mesh_supervisor, "CANCEL_GRACE_SECONDS", 0.1)
    cancel = threading.Event()

    def on_event(event: dict[str, Control]) -> None:
        if event["type"] == "started":
            cancel.set()

    result = run_worker(request, tmp_path, cancel=cancel, progress=on_event)
    assert (
        result["status"] == "failed"
        and control_object(result["failure"])["category"] == "cancelled"
    )
    assert control_object(result["supervision"])["exit_code"] == -signal.SIGKILL
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_budget_covers_worker_startup(tmp_path: Path) -> None:
    result = run_worker(worker_request(tmp_path, budget=1024 * 1024), tmp_path)
    assert (
        result["status"] == "failed"
        and control_object(result["failure"])["category"] == "resource"
    )
    assert not list(tmp_path.glob("mesh-import-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize("reason", ("cancelled", "resource"))
def test_requested_stop_retains_worker_cleanup_and_final_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    request = worker_request(tmp_path)
    fault_command(monkeypatch, "cooperative-stop")
    cancel = threading.Event()
    events: list[dict[str, Control]] = []

    def oversized_rss(_process: object) -> SimpleNamespace:
        return SimpleNamespace(rss=request.budget_bytes + 1)

    def progress(event: dict[str, Control]) -> None:
        events.append(event)
        if event["type"] == "started":
            if reason == "cancelled":
                cancel.set()
            else:
                monkeypatch.setattr(psutil.Process, "memory_info", oversized_rss)

    outcome = run_worker(request, tmp_path, cancel=cancel, progress=progress)
    assert outcome["status"] == "failed"
    assert control_object(outcome["failure"])["category"] == reason
    assert control_object(outcome["supervision"])["exit_code"] == 1
    final = control_object(outcome["worker_report"])
    assert final["status"] == "failed"
    assert control_object(final["failure"])["category"] == "cancelled"
    assert int(str(control_object(final["memory_after_cleanup"])["rss_bytes"])) > 0
    assert control_object(outcome["last_progress"])["event"] == "final"
    assert [event["type"] for event in events] == ["started", "progress", "result"]
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_worker_failure_report_and_explicit_stale_retention(tmp_path: Path) -> None:
    request = WorkerRequest(
        "import", tmp_path / "missing.ply", destination=tmp_path, chunk_rows=2
    )
    unknown = tmp_path / ".scansor-mesh-unrelated"
    unknown.mkdir()
    _ = (unknown / "keep.txt").write_bytes(b"unrelated")
    result = run_worker(request, tmp_path, retain_incomplete=True)
    assert (
        result["status"] == "failed"
        and control_object(result["failure"])["category"] == "input"
    )
    directory = Path(str(result["incomplete_directory"]))
    assert (
        control_object(decode_control((directory / "failure.json").read_bytes()))[
            "status"
        ]
        == "incomplete"
    )
    before = artifact_state(directory), artifact_state(unknown)
    discovery = discover_staging(tmp_path)
    entries = cast(list[dict[str, Control]], discovery["entries"])
    assert (
        next(entry for entry in entries if entry["path"] == str(directory))["state"]
        == "stale"
    )
    assert (
        next(entry for entry in entries if entry["path"] == str(unknown))["state"]
        == "unrecognized"
    )
    assert (artifact_state(directory), artifact_state(unknown)) == before
    next_run = run_worker(worker_request(tmp_path), tmp_path)
    assert next_run["status"] == "complete"
    assert (artifact_state(directory), artifact_state(unknown)) == before


def test_stale_discovery_is_bounded_and_never_follows_directory_symlinks(
    tmp_path: Path,
) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _ = (foreign / "keep.txt").write_bytes(b"keep")
    for index in range(3):
        (tmp_path / f".scansor-mesh-{index}").symlink_to(
            foreign, target_is_directory=True
        )
    result = discover_staging(tmp_path, limit=2)
    assert len(cast(list[Control], result["entries"])) == 2
    assert result["truncated"] is True
    assert (foreign / "keep.txt").read_bytes() == b"keep"


def test_stderr_is_bounded_without_blocking_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault_command(monkeypatch, "noisy-stderr")
    result = run_worker(worker_request(tmp_path), tmp_path)
    assert result["status"] == "complete"
    info = control_object(result["supervision"])
    assert info["stderr"] == "x" * 4096 and info["stderr_truncated"] is True


def test_worker_request_is_strict_and_float_free(tmp_path: Path) -> None:
    request = worker_request(tmp_path)
    record = request.record()
    assert WorkerRequest.from_record(decode_control(encode_control(record))) == request
    for field, invalid in (
        ("operation", "resume"),
        ("storage", "other"),
        ("source", "relative.ply"),
        ("source", None),
        ("budget_bytes", True),
        ("chunk_rows", True),
        ("chunk_rows", 0),
        ("contribution", str(tmp_path)),
        ("expected_import_id", "bad"),
    ):
        with pytest.raises(MeshImportError):
            _ = WorkerRequest.from_record(record | {field: invalid})
    with pytest.raises(MeshImportError):
        _ = WorkerRequest.from_record(record | {"unknown": 0})


def test_staging_from_another_pid_namespace_is_not_called_stale(tmp_path: Path) -> None:
    result = run_worker(
        WorkerRequest("import", tmp_path / "missing", destination=tmp_path),
        tmp_path,
        retain_incomplete=True,
    )
    directory = Path(str(result["incomplete_directory"]))
    path = directory / "worker.json"
    marker = control_object(decode_control(path.read_bytes()))
    marker["pid_namespace_inode"] = os.stat("/proc/self/ns/pid").st_ino + 1
    _ = path.write_bytes(encode_control(marker))
    before = artifact_state(directory)
    entries = cast(list[dict[str, Control]], discover_staging(tmp_path)["entries"])
    assert len(entries) == 1 and entries[0]["state"] == "unknown-namespace"
    assert artifact_state(directory) == before


def test_supervisor_control_disk_failure_cleans_owned_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(_path: Path, _record: dict[str, Control]) -> None:
        raise OSError(errno.ENOSPC, "injected control write failure")

    monkeypatch.setattr(mesh_supervisor, "_write_record", full)
    result = run_worker(worker_request(tmp_path), tmp_path)
    assert result["status"] == "failed"
    assert control_object(result["failure"])["category"] == "resource"
    assert control_object(result["supervision"])["pid"] is None
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize("retain", (False, True))
def test_replaced_public_workspace_is_preserved_and_not_reported_as_owned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retain: bool
) -> None:
    fault_command(monkeypatch, "ignore-term")
    monkeypatch.setattr(mesh_supervisor, "CANCEL_GRACE_SECONDS", 0.1)
    cancel = threading.Event()
    held_away = tmp_path / "held-away"
    moved: list[tuple[Path, tuple[int, int]]] = []

    def replace(event: dict[str, Control]) -> None:
        if event["type"] == "started":
            public = next(tmp_path.glob(".scansor-mesh-*"))
            entry = public.stat()
            moved.append((public, (entry.st_dev, entry.st_ino)))
            _ = public.rename(held_away)
            public.mkdir()
            _ = (public / "keep.txt").write_bytes(b"unrelated")
            cancel.set()

    result = run_worker(
        worker_request(tmp_path),
        tmp_path,
        progress=replace,
        cancel=cancel,
        retain_incomplete=retain,
    )
    assert len(moved) == 1
    public, identity = moved[0]
    assert result["status"] == "failed"
    assert control_object(result["failure"])["category"] == "cancelled"
    assert "cleanup_failure" in result
    assert "incomplete_directory" not in result
    assert result["replaced_workspace_path"] == str(public)
    assert (public / "keep.txt").read_bytes() == b"unrelated"
    assert {path.name for path in public.iterdir()} == {"keep.txt"}
    # The test moved its own inode; the supervisor cannot infer the new path.
    remove_owned_workspace(held_away, identity)


@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
def test_ipc_frames_survive_short_pipe_boundaries(chunk: int) -> None:
    stream = io.BytesIO()
    writer = FrameWriter(stream)
    expected: list[dict[str, Control]] = [
        {"type": "progress", "i": index, "text": "é"} for index in range(5)
    ]
    for value in expected:
        writer.send(value)
    raw, reader = stream.getvalue(), FrameReader()
    actual: list[dict[str, Control]] = []
    for offset in range(0, len(raw), chunk):
        actual.extend(reader.feed(raw[offset : offset + chunk]))
    reader.finish()
    assert actual == expected


def test_ipc_rejects_oversized_noncanonical_and_partial_frames() -> None:
    for raw in (
        struct.pack(">I", MAX_CONTROL_BYTES + 1),
        struct.pack(">I", 0),
        struct.pack(">I", 2) + b"{}",
        struct.pack(">I", 3) + b"[]\n",
    ):
        with pytest.raises(ScansorError):
            _ = FrameReader().feed(raw)
    reader = FrameReader()
    _ = reader.feed(struct.pack(">I", 100) + b"{")
    with pytest.raises(MeshImportError, match="incomplete"):
        reader.finish()
