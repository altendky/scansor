"""Sample only an explicitly owned browser root and its current descendants.

The driver starts this project module with its pinned native interpreter. Input
is newline JSON: {operation: mark, label: ...} or {operation: stop}. No process is
signalled, inspected for command-line/environment data, or searched globally.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, final

import psutil


def process_high_water(pid: int) -> int | None:
    """Linux per-process RSS high water; it is not a simultaneous tree peak."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return None
    return None


@final
class BrowserTreeSampler:
    def __init__(self, pid: int, period: float = 0.05) -> None:
        if type(pid) is not int or pid <= 0 or not 0.01 <= period <= 1:
            raise ValueError("Expected an owned PID and sampling period in [0.01,1]")
        self.root = psutil.Process(pid)
        self.created = self.root.create_time()
        self.period = period
        self.started = time.monotonic()
        self.phase = "startup"
        self.samples: list[dict[str, Any]] = []
        self.processes: dict[tuple[int, float], dict[str, Any]] = {}
        self.errors: list[str] = []
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def sample(self) -> dict[str, Any]:
        with self.lock:
            if not self.root.is_running() or self.root.create_time() != self.created:
                raise RuntimeError("Owned browser root exited or changed identity")
            processes = [self.root, *self.root.children(recursive=True)]
            rss = pss = available = observed = vanished = 0
            for process in processes:
                try:
                    created = process.create_time()
                    if not process.is_running():
                        vanished += 1
                        continue
                    unavailable = None
                    try:
                        memory = process.memory_full_info()
                    except psutil.AccessDenied:
                        memory = process.memory_info()
                        unavailable = "memory_full_info access denied"
                    resident = int(memory.rss)
                    proportional = getattr(memory, "pss", None)
                    if proportional is not None:
                        available += 1
                        pss += int(proportional)
                    rss += resident
                    observed += 1
                    key = (process.pid, created)
                    entry = self.processes.setdefault(
                        key,
                        {
                            "pid": process.pid,
                            "created": created,
                            "root": process.pid == self.root.pid,
                            "first_observed_seconds": time.monotonic() - self.started,
                            "sampled_rss_peak_bytes": 0,
                            "sampled_pss_peak_bytes": None,
                            "rss_high_water_bytes": None,
                        },
                    )
                    entry["sampled_rss_peak_bytes"] = max(
                        entry["sampled_rss_peak_bytes"], resident
                    )
                    if proportional is not None:
                        entry["sampled_pss_peak_bytes"] = max(
                            entry["sampled_pss_peak_bytes"] or 0, int(proportional)
                        )
                    high = process_high_water(process.pid)
                    if high is not None:
                        entry["rss_high_water_bytes"] = max(
                            entry["rss_high_water_bytes"] or 0, high
                        )
                    if unavailable:
                        entry["pss_unavailable_reason"] = unavailable
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    vanished += 1
            if observed == 0 or rss <= 0:
                raise RuntimeError(
                    "Owned browser tree has no resident-memory observation"
                )
            sample = {
                "seconds": time.monotonic() - self.started,
                "phase": self.phase,
                "rss_bytes": rss,
                "pss_bytes": pss if available == observed else None,
                "observed_pss_bytes": pss,
                "pss_processes": available,
                "processes": observed,
                "vanished_during_sample": vanished,
            }
            if len(self.samples) >= 30_000:
                raise RuntimeError(
                    "Browser memory sample history exceeded its bounded run budget"
                )
            self.samples.append(sample)
            return dict(sample)

    def _run(self) -> None:
        while not self.stop_event.wait(self.period):
            try:
                _ = self.sample()
            except Exception as error:
                self.errors.append(f"{type(error).__name__}: {error}")
                self.stop_event.set()

    def mark(self, label: object) -> dict[str, Any]:
        if not isinstance(label, str) or not 0 < len(label) <= 128:
            raise ValueError("Expected a bounded operation phase")
        with self.lock:
            self.phase = label
            return self.sample()

    def finish(self) -> dict[str, Any]:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise RuntimeError("Browser sampler thread did not stop")
        with self.lock:
            proportional = [
                s["pss_bytes"] for s in self.samples if s["pss_bytes"] is not None
            ]
            return {
                "complete": not self.errors,
                "root_pid": self.root.pid,
                "root_created": self.created,
                "requested_period_seconds": self.period,
                "elapsed_seconds": time.monotonic() - self.started,
                "sample_count": len(self.samples),
                "baseline": self.samples[0] if self.samples else None,
                "sampled_tree_rss_peak_bytes": max(
                    (s["rss_bytes"] for s in self.samples), default=0
                ),
                "sampled_tree_pss_peak_bytes": max(proportional, default=None),
                "max_actual_gap_seconds": max(
                    (
                        b["seconds"] - a["seconds"]
                        for a, b in zip(self.samples, self.samples[1:], strict=False)
                    ),
                    default=0,
                ),
                "processes": list(self.processes.values()),
                "samples": self.samples,
                "errors": self.errors,
                "caveats": [
                    "Only the launched browser PID and descendants observed under that root are sampled; Node preview, native oracle, driver and OS caches are excluded.",
                    "RSS sums count shared pages repeatedly; complete PSS totals are separate and must not be added to RSS or stage heap/Wasm/array/Blob counters.",
                    "Sampling and process enumeration are not atomic, add overhead, and can miss short-lived allocations or processes; requested period is not guaranteed.",
                    "Per-process RSS high-water counters may precede sampling and are not simultaneous tree peaks; no capacity or performance threshold is inferred.",
                ],
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--pid", type=int, required=True)
    _ = parser.add_argument("--period", type=float, default=0.05)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing to overwrite a memory observation")
    sampler = BrowserTreeSampler(args.pid, args.period)
    print(
        json.dumps(
            {
                "ready": True,
                "sampler_pid": os.getpid(),
                "root_pid": args.pid,
                "root_created": sampler.created,
            }
        ),
        flush=True,
    )
    _ = sampler.sample()
    sampler.thread.start()
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command.get("operation") == "stop":
                break
            if command.get("operation") != "mark":
                raise ValueError("Unknown browser sampler operation")
            print(
                json.dumps(
                    {"id": command.get("id"), "sample": sampler.mark(command["label"])}
                ),
                flush=True,
            )
    finally:
        report = sampler.finish()
        temporary = args.output.with_suffix(".tmp")
        _ = temporary.write_text(json.dumps(report, allow_nan=False) + "\n")
        _ = temporary.replace(args.output)
        print(
            json.dumps(
                {
                    "stopped": True,
                    "complete": report["complete"],
                    "sample_count": report["sample_count"],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
