"""One fresh worker, streamed telemetry, and explicitly scoped S6 measurements."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import final

from experiments.mesh_scale_metrics import (
    DiskSampler,
    PhaseTotals,
    host_record,
    integer,
    object_record,
)
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_supervisor import run_worker
from scansor.mesh_worker_request import WorkerRequest


@final
class ProgressPrinter:
    def __init__(self) -> None:
        self.started = time.monotonic_ns()
        self.last = 0

    def __call__(self, phase: str, completed: int, total: int) -> None:
        now = time.monotonic_ns()
        if now - self.last >= 5_000_000_000:
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "completed": completed,
                        "total": total,
                        "elapsed_ns": now - self.started,
                    }
                ),
                flush=True,
            )
            self.last = now


def measure_worker(
    request: WorkerRequest,
    root: Path,
    telemetry: Path,
    *,
    cancel_phase: str | None = None,
    cancel_completed: int = 0,
) -> dict[str, Control]:
    """Telemetry is outside the measured run tree; no other worker runs here."""
    if telemetry.resolve().is_relative_to(root.resolve()):
        raise ValueError("telemetry must be outside the measured run root")
    phases, disk, printer = PhaseTotals(), DiskSampler(root), ProgressPrinter()
    cancelled, cancel_record = threading.Event(), None
    before = host_record(root)
    started = time.monotonic_ns()
    digest, count, records = hashlib.sha256(), 0, 0
    outcome: dict[str, Control] = {}
    error_record: dict[str, Control] | None = None
    with telemetry.open("xb") as stream:

        def progress(message: dict[str, Control]) -> None:
            nonlocal count, records, cancel_record
            # The protocol already bounds each record. Stream rather than retain
            # growing per-batch lists; no filesystem walk/fsync in the RSS loop.
            raw = (
                json.dumps(message, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            _ = stream.write(raw)
            digest.update(raw)
            count += len(raw)
            records += 1
            if message.get("type") != "progress":
                if message.get("type") == "published":
                    disk.request_observation()
                return
            record = object_record(message.get("record"))
            phases.accept(record)
            if record.get("event") in ("phase", "final"):
                disk.request_observation()
            printer(
                str(record.get("phase")),
                integer(record.get("completed")),
                integer(record.get("total")),
            )
            if (
                cancel_phase is not None
                and record.get("phase") == cancel_phase
                and integer(record.get("completed")) >= cancel_completed
                and not cancelled.is_set()
            ):
                cancel_record = record
                cancelled.set()

        try:
            with disk:
                outcome = run_worker(
                    request, root / "work", progress=progress, cancel=cancelled
                )
        except BaseException as error:
            error_record = {"type": type(error).__name__, "message": str(error)[:2048]}
        finally:
            stream.flush()
            os.fsync(stream.fileno())
    elapsed = time.monotonic_ns() - started
    after = host_record(root)
    supervision = object_record(outcome.get("supervision", {}))
    edges = object_record(supervision.get("sampling_edges", {}))
    peak, samples = (
        integer(supervision.get("kernel_peak_rss_bytes", 0)),
        integer(supervision.get("samples", 0)),
    )
    status = outcome.get("status") == "complete" and error_record is None
    return {
        "status": "complete" if status else "failed",
        "request": request.record(),
        "elapsed_including_measurement_ns": elapsed,
        "outcome": outcome,
        "measurement_error": error_record,
        "host_before": before,
        "host_after": after,
        "disk": disk.record(),
        "phase_totals": phases.record(),
        "telemetry": {
            "path": str(telemetry),
            "bytes": count,
            "sha256": digest.hexdigest(),
            "records": records,
        },
        "cancellation": {
            "requested_phase": cancel_phase,
            "requested_completed": cancel_completed,
            "triggered": cancelled.is_set(),
            "trigger_record": cancel_record,
        },
        "observations": {
            "whole_worker_rss_within_budget": peak > 0 and peak <= request.budget_bytes,
            "sample_gaps_including_edges_within_10ms": samples > 0
            and edges.get("initial_gap_ns") is not None
            and edges.get("terminal_gap_ns") is not None
            and integer(edges.get("largest_gap_including_edges_ns", 10_000_001))
            <= 10_000_000,
            "work_directory_empty_after_exit": not any((root / "work").iterdir()),
            "phase_coverage_complete": phases.final_seen,
        },
        "scope": "Whole-worker kernel RSS/CPU/fault counters include startup through exit. Parent telemetry, file sampling, and subsequent independent artifact validation are outside worker RSS. Cgroup charge can include the parent, file cache and other listed members; it is not RSS. The cgroup kernel peak may predate this operation. A completed operation does not by itself pass resource or sampling-coverage observations.",
    }


def save_report(path: Path, report: dict[str, Control]) -> None:
    with path.open("xb") as stream:
        _ = stream.write(encode_control(report))
        stream.flush()
        os.fsync(stream.fileno())
