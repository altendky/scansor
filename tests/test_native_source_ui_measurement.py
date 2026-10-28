"""Independent report/lifecycle controls for the observational native UI driver."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/measure-native-source-ui.py"
SPEC = importlib.util.spec_from_file_location("native_source_ui_measurement", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
measurement: Any = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measurement)


def evidence() -> tuple[dict[str, Any], dict[str, Any]]:
    """Truth records independent of the validator's construction logic."""
    source = {
        "id": "scene",
        "source_sha256": "f" * 64,
        "vertices": 84,
        "triangles": 132,
    }
    imported = {
        "pid": 77,
        "project_generation": 1,
        "source_sha256": source["source_sha256"],
        "vertices": 84,
        "triangles": 132,
        "original_bytes": 2048,
        "source_storage": {
            "kind": "PathInput",
            "backing_kind": "PathInput",
            "authority_bytes": 2048,
            "owned_source_payload_bytes": 0,
        },
        "source_replay": {
            "bytes": 2048,
            "sha256": source["source_sha256"],
            "max_chunk_bytes": 2048,
            "read_calls": 2,
            "eof_verified": True,
        },
        "recipe_nodes": 1,
        "import_busy": False,
        "staging_file_count": 0,
        "operations": [
            {
                "terminal": True,
                "retained_references": {
                    "project": False,
                    "context": False,
                    "recipe": False,
                    "path": False,
                },
            }
        ],
    }
    empty = {
        **imported,
        "project_generation": 0,
        "source_sha256": None,
        "vertices": 0,
        "triangles": 0,
        "original_bytes": 0,
        "source_storage": {
            "kind": "none",
            "backing_kind": "none",
            "authority_bytes": 0,
            "owned_source_payload_bytes": 0,
        },
        "source_replay": {
            "bytes": 0,
            "sha256": None,
            "max_chunk_bytes": 0,
            "read_calls": 0,
            "eof_verified": False,
        },
        "recipe_nodes": 0,
        "operations": [],
    }
    stages: list[dict[str, Any]] = []
    for stage, state in (
        ("empty-baseline", empty),
        # File chooser notification races acceptance; it is not a publication
        # fence. The following imported boundary must still advance from empty.
        ("file-selected", imported),
        ("imported", imported),
        ("inspected", imported),
        ("selection-overlay", {**imported, "recipe_nodes": 2}),
        ("reset", {**empty, "project_generation": 2}),
    ):
        stages.append(
            {
                "event": {"cycle": 1, "source": "scene", "stage": stage},
                "server": copy.deepcopy(state),
            }
        )
    return {
        "browser_report": {"complete": True},
        "browser_finished": {"complete": True},
        "server_pid": 77,
        "stages": stages,
        "sampler": {"sample_count": 10, "failure": None, "limit_reached": False},
        "rss": {"valid_sample_count": 10},
        "cleanup": {
            "node": {"returncode": 0, "forced": []},
            "server": {"returncode": 0, "forced": []},
            "remaining_staging_files": [],
        },
    }, {"sources": [source]}


def test_frozen_complete_evidence_allows_fast_acceptance_and_no_memory_shrink() -> None:
    report, manifest = evidence()
    report["rss"].update(baseline=10, after_reset=999999999999)
    assert measurement.validate_observations(report, manifest, 1) == []


def test_source_observation_hashes_replayable_path_and_counts_explicit_byte_backing(
    tmp_path: Path,
) -> None:
    from experiments.scan_sources import BytesInput, PathInput
    from experiments.scan_workspace import ScanImportError, ScanLimits, prepare_scan
    from tests.native_scan_fixture import native_grid_fixture

    raw = native_grid_fixture(4, 4).raw
    path = tmp_path / "source.ply"
    _ = path.write_bytes(raw)
    limits = ScanLimits(2_000_000, 10_000, 20_000, 20_000_000)
    for provider, owned in (
        (PathInput(path), 0),
        (BytesInput(raw, path.name), len(raw)),
    ):
        workspace = prepare_scan(provider, path.name, limits)
        observed = measurement.source_authority_observation(workspace)
        assert observed["original_bytes"] == len(raw)
        assert observed["source_storage"]["owned_source_payload_bytes"] == owned
        assert observed["source_replay"]["sha256"] == workspace.source_sha256
        assert observed["source_replay"]["bytes"] == len(raw)
        assert observed["source_replay"]["eof_verified"] is True
    workspace = prepare_scan(PathInput(path), path.name, limits)
    _ = path.write_bytes(raw[:-1])
    with pytest.raises(ScanImportError, match=r"truncated|length changed"):
        _ = measurement.source_authority_observation(workspace)


@pytest.mark.parametrize(
    "field,value",
    [
        ("eof_verified", False),
        ("sha256", "0" * 64),
        ("max_chunk_bytes", 65537),
        ("read_calls", 0),
    ],
)
def test_native_source_report_rejects_unverified_replay(
    field: str, value: object
) -> None:
    report, manifest = evidence()
    report["stages"][2]["server"]["source_replay"][field] = value
    assert measurement.validate_observations(report, manifest, 1)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_sha256", "0" * 64),
        ("vertices", 83),
        ("triangles", 131),
        ("pid", 78),
        ("project_generation", 0),
        ("project_generation", None),
    ],
)
def test_wrong_accepted_source_count_cursor_or_server_rejects(
    field: str, value: object
) -> None:
    report, manifest = evidence()
    report["stages"][2]["server"][field] = value
    assert measurement.validate_observations(report, manifest, 1)


@pytest.mark.parametrize("stage", ["empty-baseline", "reset"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("original_bytes", 123),
        ("recipe_nodes", 1),
        ("source_sha256", "f" * 64),
        ("staging_file_count", 1),
        ("import_busy", True),
    ],
)
def test_empty_reset_does_not_hide_retained_source_or_staging(
    stage: str, field: str, value: object
) -> None:
    report, manifest = evidence()
    next(s["server"] for s in report["stages"] if s["event"]["stage"] == stage)[
        field
    ] = value
    assert measurement.validate_observations(report, manifest, 1)


@pytest.mark.parametrize("name", ["project", "context", "recipe", "path"])
def test_terminal_history_cannot_retain_candidate_references(name: str) -> None:
    report, manifest = evidence()
    report["stages"][3]["server"]["operations"][0]["retained_references"][name] = True
    assert any(
        "terminal operation" in e
        for e in measurement.validate_observations(report, manifest, 1)
    )


@pytest.mark.parametrize("field", ["browser_report", "browser_finished"])
@pytest.mark.parametrize("value", [None, {}, {"complete": False}, {"complete": "true"}])
def test_absent_false_or_untyped_completion_is_not_success(
    field: str, value: object
) -> None:
    report, manifest = evidence()
    if value is None:
        del report[field]
    else:
        report[field] = value
    assert measurement.validate_observations(report, manifest, 1)


@pytest.mark.parametrize(
    "change",
    [
        "missing-stage",
        "repeat-stage",
        "backward-epoch",
        "reset-epoch",
        "second-cycle-missing",
        "nonzero-exit",
        "forced-exit",
        "cleanup",
        "sampler-empty",
        "sampler-failed",
        "sampler-cap",
        "sampler-partial-errors",
        "forced-descendant",
        "remaining-descendant",
        "descendant-error",
    ],
)
def test_incomplete_lifecycle_never_reports_complete(change: str) -> None:
    report, manifest = evidence()
    cycles = 1
    if change == "missing-stage":
        report["stages"].pop(3)
    elif change == "repeat-stage":
        report["stages"].append(report["stages"][-1])
    elif change == "backward-epoch":
        report["stages"][3]["server"]["project_generation"] = 0
    elif change == "reset-epoch":
        report["stages"][-1]["server"]["project_generation"] = 1
    elif change == "second-cycle-missing":
        cycles = 2
    elif change == "nonzero-exit":
        report["cleanup"]["node"]["returncode"] = 17
    elif change == "forced-exit":
        report["cleanup"]["server"]["forced"] = ["terminate"]
    elif change == "cleanup":
        report["cleanup"]["remaining_staging_files"] = ["source.part"]
    elif change == "sampler-empty":
        report["sampler"]["sample_count"] = 0
    elif change == "sampler-failed":
        report["sampler"]["failure"] = "disk full"
    elif change == "sampler-cap":
        report["sampler"]["limit_reached"] = True
    elif change == "sampler-partial-errors":
        report["sampler"]["error_count"] = 1
    elif change == "forced-descendant":
        report["cleanup"]["node_descendants"] = {"forced": [{"pid": 78}]}
    elif change == "remaining-descendant":
        report["cleanup"]["node_descendants"] = {"remaining": [{"pid": 78}]}
    elif change == "descendant-error":
        report["cleanup"]["node_descendants"] = {"errors": ["AccessDenied"]}
    assert measurement.validate_observations(report, manifest, cycles)


def test_sample_gaps_errors_and_empty_intervals_remain_explicit() -> None:
    samples = [
        {"monotonic_seconds": 1, "rss_bytes": 100},
        {"monotonic_seconds": 2, "rss_bytes": None, "error": "AccessDenied"},
        {"monotonic_seconds": 5, "rss_bytes": 120},
    ]
    summary = measurement.sample_summary(samples, 0, 6)
    assert summary["sample_count"] == 3
    assert summary["valid_sample_count"] == 2
    assert summary["error_count"] == 1
    assert summary["maximum_observed_gap_seconds"] == 3
    assert summary["sampled_rss_min_bytes"] == 100
    assert summary["sampled_rss_max_bytes"] == 120
    assert summary["availability"] == "observed-with-errors"
    for start, stop in [(2, 2), (7, 8)]:
        unavailable = measurement.sample_summary(samples, start, stop)
        assert unavailable["availability"] == "unavailable"
        assert unavailable["sampled_rss_max_bytes"] is None
        assert unavailable["sampled_rss_min_bytes"] is None
        assert unavailable["maximum_observed_gap_seconds"] is None
    _ = json.dumps(summary, allow_nan=False)


def test_sampler_preserves_actual_exception_and_can_stop_without_fake_zero(
    tmp_path: Path,
) -> None:
    def unavailable() -> int:
        raise PermissionError("RSS unavailable")

    sampler = measurement.Sampler(unavailable, tmp_path / "rss.ndjson", 0.01)
    failed = sampler.sample()
    assert failed["rss_bytes"] is None
    assert "PermissionError" in failed["error"]
    sampler.stop.set()
    sampler.thread.start()
    sampler.close()
    assert sampler.path.read_text() == ""
    assert sampler.count == 0


def test_only_failed_rss_reads_cannot_establish_complete_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(measurement, "MAX_SAMPLES", 2)

    def unavailable() -> int:
        raise PermissionError("RSS unavailable")

    sampler = measurement.Sampler(unavailable, tmp_path / "failed.ndjson", 0.001)
    sampler.thread.start()
    sampler.thread.join(5)
    samples = [json.loads(line) for line in sampler.path.read_text().splitlines()]
    assert len(samples) == 2 and sampler.errors == 2
    report, manifest = evidence()
    report["sampler"].update(sample_count=2, limit_reached=False)
    report["rss"] = measurement.sample_summary(
        samples,
        samples[0]["monotonic_seconds"] - 1,
        samples[-1]["monotonic_seconds"] + 1,
    )
    assert report["rss"]["availability"] == "unavailable"
    assert any(
        "RSS sampler" in error
        for error in measurement.validate_observations(report, manifest, 1)
    )


def test_sampler_is_bounded_and_keeps_observed_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(measurement, "MAX_SAMPLES", 2)
    sampler = measurement.Sampler(lambda: 321, tmp_path / "rss.ndjson", 0.001)
    sampler.thread.start()
    sampler.thread.join(5)
    assert not sampler.thread.is_alive()
    assert sampler.limit_reached and sampler.count == 2
    assert [
        json.loads(s)["rss_bytes"] for s in sampler.path.read_text().splitlines()
    ] == [321, 321]


def test_fresh_reports_reject_existing_repository_and_symlink_destinations(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    marker = repository / "keep"
    _ = marker.write_text("unchanged")
    for path in [repository, repository / "reports"]:
        with pytest.raises(ValueError, match="outside"):
            measurement.fresh_output(path, repository)
    alias = tmp_path / "alias"
    alias.symlink_to(repository, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        measurement.fresh_output(alias / "reports", repository)
    with pytest.raises(FileExistsError):
        measurement.fresh_output(repository, tmp_path / "other")
    assert marker.read_text() == "unchanged"
    assert not (repository / "reports").exists()


def test_staging_snapshot_records_file_vanishing_at_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_path / "upload.part"
    _ = pending.write_bytes(b"original")
    original = Path.stat

    def removed(path: Path, *, follow_symlinks: bool = True) -> Any:
        if path == pending:
            pending.unlink()
            raise FileNotFoundError("completed receiver cleanup")
        return original(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", removed)
    snapshot = measurement.staging_inventory(tmp_path)
    assert snapshot["staging_files"] == []
    assert snapshot["staging_file_count"] == 0
    assert snapshot["staging_vanished_during_observation"] == ["upload.part"]


def test_staging_snapshot_does_not_hide_permission_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pending = tmp_path / "upload.part"
    _ = pending.write_bytes(b"original")
    original = Path.stat

    def denied(path: Path, *, follow_symlinks: bool = True) -> Any:
        if path == pending:
            raise PermissionError("unreadable owned staging")
        return original(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", denied)
    with pytest.raises(PermissionError, match="unreadable"):
        measurement.staging_inventory(tmp_path)


def test_server_observer_reuses_arrays_without_encoding_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from experiments.nozzle_browser import NozzleServer

    server: Any = NozzleServer(staging_directory=tmp_path)
    try:
        accepted = server._project.accepted
        original = server.accepted

        def capture(*, buffers: bool = False) -> Any:
            assert buffers is False
            return original(buffers=buffers)

        monkeypatch.setattr(server, "accepted", capture)
        monkeypatch.setattr(
            type(server.source_imports),
            "busy",
            property(lambda _: pytest.fail("observer expired reservations")),
        )
        # Full snapshots contain recipe/selection values; this observer must
        # not request one merely to count nodes/read the token.
        monkeypatch.setattr(
            accepted.graph, "snapshot", lambda: pytest.fail("observer serialized graph")
        )
        first = measurement.server_snapshot(server, tmp_path)
        second = measurement.server_snapshot(server, tmp_path)
        assert first["arrays"] == second["arrays"]
        assert first["original_bytes"] == 0
        assert first["project_generation"] == 0
        assert first["staging_file_count"] == 0
        assert server._project.accepted is accepted
        assert (
            first["process_high_water"]["bytes"] is None
            or first["process_high_water"]["bytes"] > 0
        )
    finally:
        server.server_close()


def test_abnormal_node_exit_preserves_partial_report_and_cleans_real_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No browser/benchmark: one empty server and a deliberately failing peer."""
    fake_node = tmp_path / "failing-peer.mjs"
    _ = fake_node.write_text("""
import fs from 'node:fs';
import path from 'node:path';
const output=process.argv[process.argv.indexOf('--output')+1];
fs.mkdirSync(output);
fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify({complete:false,errors:['intentional peer failure']}));
console.log(JSON.stringify({schema_version:1,event:'finished',complete:false}));
process.exitCode=17;
""")
    monkeypatch.setattr(measurement, "NODE_SCRIPT", fake_node)
    real_run = subprocess.run

    def fixture_only(command: list[str], **kwargs: Any) -> Any:
        if "--internal-fixtures" not in command:
            return real_run(command, **kwargs)
        directory = Path(command[-1])
        directory.mkdir()
        measurement.write_json(
            directory.parent / "inputs.json",
            {"repository_revision": "frozen-before-server", "sources": []},
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(measurement.subprocess, "run", fixture_only)
    output = tmp_path / "report"
    args = argparse.Namespace(
        output=output,
        native_python=Path(sys.executable),
        playwright_directory=tmp_path,
        cycles=1,
        sample_interval=0.01,
        timeout_seconds=30,
    )
    assert measurement.native_run(args) == 1
    report = json.loads((output / "native-source-ui.json").read_text())
    assert report["complete"] is False
    assert report["browser_report"]["errors"] == ["intentional peer failure"]
    assert report["cleanup"]["node"]["returncode"] == 17
    assert report["cleanup"]["server"]["returncode"] == 0
    assert report["cleanup"]["server"]["forced"] == []
    assert report["cleanup"]["remaining_staging_files"] == []
    assert report["sampler"]["sample_count"] > 0
    assert report["sampler"]["error_count"] == 0
    assert (output / "browser-events.ndjson").is_file()
    assert (output / "server-events.ndjson").is_file()
    assert (output / "node.stderr.log").is_file()
    assert (output / "server.stderr.log").is_file()
    # Restore before any subprocess helper used by pytest itself can execute.
    monkeypatch.setattr(measurement.subprocess, "run", real_run)


def test_graceful_pipe_failure_still_reaps_owned_process() -> None:
    process = subprocess.Popen([sys.executable, "-c", "pass"], text=True)

    def broken_pipe() -> None:
        raise BrokenPipeError("closed control pipe")

    result = measurement.stop_process(process, graceful=broken_pipe)
    assert result["returncode"] == 0
    assert "BrokenPipeError" in result.get("graceful_error", "") or process.poll() == 0


def test_hung_node_detached_child_is_creation_verified_and_cleaned() -> None:
    import psutil

    code = """
const {spawn}=require('node:child_process');
const child=spawn(process.execPath,['-e',"console.log('ready');setInterval(()=>{},1000)"],
  {detached:process.platform!=='win32',stdio:['ignore','pipe','ignore']});
child.stdout.once('data',()=>console.log(child.pid));
setInterval(()=>{},1000);
"""
    node = subprocess.Popen(["node", "-e", code], stdout=subprocess.PIPE, text=True)
    tree = measurement.OwnedDescendants(node)
    child: Any = None
    try:
        assert node.stdout is not None
        child_pid = int(node.stdout.readline())
        child = psutil.Process(child_pid)
        assert tree.changed.wait(5), "Node child was not observed"
        with tree.lock:
            proof = next(
                item for _, item in tree.observed.values() if item["pid"] == child_pid
            )
        assert proof["create_time"] == child.create_time()
        assert proof["observed_parent_pid"] == node.pid
        if os.name == "posix":
            assert os.getsid(child_pid) == child_pid
            assert os.getsid(child_pid) != os.getsid(node.pid)
        direct = measurement.stop_process(node, grace_seconds=0.01)
        assert direct["forced"] == ["terminate"]
        cleanup = tree.cleanup()
        assert cleanup["errors"] == [] and cleanup["remaining"] == []
        assert any(item["pid"] == child_pid for item in cleanup["forced"])
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if node.poll() is None:
            node.kill()
            _ = node.wait(timeout=5)
        _ = tree.cleanup()
        if (
            child is not None
            and child.is_running()
            and child.status() != psutil.STATUS_ZOMBIE
        ):
            child.kill()
            _ = child.wait(timeout=5)


def test_creation_mismatch_never_signals_unrelated_process() -> None:
    import psutil

    root = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"], text=True
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(60)"], text=True
    )
    tree = measurement.OwnedDescendants(root)
    try:
        handle = psutil.Process(unrelated.pid)
        # A reused PID must never inherit authority from an earlier observation.
        wrong_creation = handle.create_time() - 1
        with tree.lock:
            tree.observed[(unrelated.pid, wrong_creation)] = (
                handle,
                {"pid": unrelated.pid, "create_time": wrong_creation},
            )
        cleanup = tree.cleanup()
        assert cleanup["forced"] == [] and cleanup["remaining"] == []
        assert unrelated.poll() is None
    finally:
        _ = tree.cleanup()
        _ = measurement.stop_process(root, grace_seconds=0.01)
        _ = measurement.stop_process(unrelated, grace_seconds=0.01)
