"""Bounded phase aggregation and sampled file/cgroup evidence for S6 runs.

All samples are observations, not an assertion that intervening peaks were seen.
File allocation excludes filesystem-wide metadata/journaling; cgroup charge is
separate from process RSS and may include other members of the caller's scope.
"""

from __future__ import annotations

import os
import platform
import stat
import sys
import threading
import time
from pathlib import Path
from typing import final

import psutil

from scansor.mesh_controls import Control
from scansor.mesh_resources import cgroup_snapshot


def object_record(value: Control) -> dict[str, Control]:
    if not isinstance(value, dict):
        raise ValueError("measurement requires an object record")
    return value


def integer(value: Control) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("measurement requires a nonnegative integer")
    return value


def cgroup_record() -> dict[str, Control]:
    result = cgroup_snapshot()
    if result.get("available") is not True:
        return result
    try:
        with Path("/proc/self/cgroup").open(encoding="ascii") as stream:
            lines = stream.read(4097)
        if len(lines) > 4096:
            raise ValueError("cgroup membership exceeds its bound")
        entry = next(line[3:] for line in lines.splitlines() if line.startswith("0::"))
        root = Path("/sys/fs/cgroup") / entry.lstrip("/")
        if not (root / "memory.current").is_file():
            root = Path("/sys/fs/cgroup")
        result.update(path=str(root), membership=entry)
        for name in (
            "memory.stat",
            "memory.events.local",
            "memory.swap.peak",
            "memory.pressure",
            "cgroup.procs",
        ):
            try:
                with (root / name).open(encoding="ascii") as stream:
                    value = stream.read(16385)
                if len(value) > 16384:
                    raise ValueError("cgroup measurement exceeds its bound")
                result[name] = value.strip()
            except OSError:
                result[name] = None
    except (OSError, StopIteration) as error:
        result["observation_error"] = str(error)[:1024]
    return result


def host_record(directory: Path) -> dict[str, Control]:
    memory, swap = psutil.virtual_memory(), psutil.swap_memory()
    disk = psutil.disk_usage(str(directory))
    affinity = (
        psutil.Process().cpu_affinity()
        if hasattr(psutil.Process, "cpu_affinity")
        else None
    )
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_logical": psutil.cpu_count(),
        "cpu_physical": psutil.cpu_count(logical=False),
        "cpu_affinity": None if affinity is None else [value for value in affinity],
        "load_average_milli": [round(value * 1000) for value in os.getloadavg()],
        "memory_total_bytes": memory.total,
        "memory_available_bytes": memory.available,
        "swap_total_bytes": swap.total,
        "swap_used_bytes": swap.used,
        "swap_in_bytes": swap.sin,
        "swap_out_bytes": swap.sout,
        "filesystem": {
            "path": str(directory),
            "total_bytes": disk.total,
            "free_bytes": disk.free,
        },
        "environment": {
            name: os.environ.get(name)
            for name in (
                "NPY_DISABLE_CPU_FEATURES",
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "PYTHONMALLOC",
                "PYTHONHASHSEED",
                "MALLOC_ARENA_MAX",
            )
        },
        "cgroup": cgroup_record(),
        "cache_conditions": "Uncontrolled host/page cache; no global cache drop. Source preparation and other host activity can affect cache residency.",
    }


def _published(parts: tuple[str, ...]) -> bool:
    if len(parts) < 3 or parts[0] != "out":
        return False
    for kind in ("import", "contribution", "display"):
        prefix = "mesh-" + kind + "-"
        suffix = parts[1].removeprefix(prefix)
        if (
            parts[1].startswith(prefix)
            and len(suffix) == 64
            and all(letter in "0123456789abcdef" for letter in suffix)
        ):
            return True
    return False


def file_sample(root: Path) -> dict[str, Control]:
    started = time.monotonic_ns()
    total = allocated = published = published_allocated = files = 0
    partial = False

    def walk_error(error: OSError) -> None:
        nonlocal partial
        if isinstance(error, FileNotFoundError):
            partial = True
        else:
            raise error

    for directory, _, names in os.walk(root, followlinks=False, onerror=walk_error):
        for name in names:
            path = Path(directory) / name
            try:
                info = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                partial = True
                continue
            total += info.st_size
            allocated += info.st_blocks * 512
            files += 1
            if stat.S_ISREG(info.st_mode) and _published(path.relative_to(root).parts):
                published += info.st_size
                published_allocated += info.st_blocks * 512
    return {
        "observation_started_ns": started,
        "observation_finished_ns": time.monotonic_ns(),
        "files": files,
        "logical_bytes": total,
        "allocated_bytes": allocated,
        "published_logical_bytes": published,
        "published_allocated_bytes": published_allocated,
        "temporary_logical_bytes": total - published,
        "temporary_allocated_bytes": allocated - published_allocated,
        "observed_concurrent_changes": partial,
    }


@final
class DiskSampler:
    def __init__(self, root: Path, *, interval_seconds: float = 0.05) -> None:
        if not 0 < interval_seconds <= 1:
            raise ValueError("invalid file sampling interval")
        self.root = root
        self.interval = interval_seconds
        self._wake, self._stop = threading.Event(), threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._failure: BaseException | None = None
        self._latest: dict[str, Control] = {}
        self._peaks: dict[str, int] = {}
        self._samples = self._max_gap = 0
        self._previous: int | None = None
        self._partial = False

    def observe(self) -> None:
        sample = file_sample(self.root)
        when = integer(sample["observation_started_ns"])
        with self._lock:
            self._samples += 1
            if self._previous is not None:
                self._max_gap = max(self._max_gap, when - self._previous)
            self._previous = when
            self._latest = sample
            self._partial |= sample["observed_concurrent_changes"] is True
            for key, value in sample.items():
                if key.endswith("_bytes"):
                    self._peaks[key] = max(self._peaks.get(key, 0), integer(value))

    def request_observation(self) -> None:
        self.check()
        self._wake.set()

    def check(self) -> None:
        if self._failure is not None:
            raise self._failure

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                _ = self._wake.wait(self.interval)
                self._wake.clear()
                self.observe()
        except BaseException as error:
            self._failure = error

    def __enter__(self) -> DiskSampler:
        self.observe()
        self._thread = threading.Thread(
            target=self._run, name="scansor-scale-disk", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, _value: object, _traceback: object) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()
        try:
            self.check()
            self.observe()
        except BaseException as error:
            self._failure = error
            if exc_type is None:
                raise

    def record(self) -> dict[str, Control]:
        with self._lock:
            return {
                "scope": "Sampled file lengths and st_blocks for the entire owned run root, including completed outputs. Filesystem-wide metadata/journal space is excluded; non-atomic samples are lower bounds on intervening peaks.",
                "requested_interval_ns": round(self.interval * 1000000000),
                "largest_observed_sample_gap_ns": self._max_gap,
                "samples": self._samples,
                "any_observed_concurrent_changes": self._partial,
                "sampled_peaks": dict(self._peaks),
                "final": dict(self._latest),
                "observation_error": None
                if self._failure is None
                else str(self._failure)[:1024],
            }


@final
class PhaseTotals:
    def __init__(self) -> None:
        self.previous: dict[str, Control] | None = None
        self.totals: dict[str, dict[str, Control]] = {}
        self.boundaries = 0
        self.final_seen = False

    def accept(self, record: dict[str, Control]) -> None:
        if record.get("event") not in ("phase", "final"):
            return
        when = integer(record.get("monotonic_ns"))
        if self.final_seen:
            raise ValueError("phase evidence continued after its final observation")
        if self.previous is not None:
            previous = self.previous
            name = previous.get("phase")
            if type(name) is not str or len(name) > 256:
                raise ValueError("invalid bounded phase name")
            duration = when - integer(previous.get("monotonic_ns"))
            if duration < 0:
                raise ValueError("phase timestamps regressed")
            if name not in self.totals:
                if len(self.totals) >= 512:
                    raise ValueError("phase cardinality exceeds its explicit bound")
                self.totals[name] = {
                    "occurrences": 0,
                    "elapsed_ns": 0,
                    "io_delta": {},
                    "last_completed": 0,
                    "last_total": 0,
                }
            total = self.totals[name]
            total["occurrences"] = integer(total["occurrences"]) + 1
            total["elapsed_ns"] = integer(total["elapsed_ns"]) + duration
            before_io, after_io = (
                object_record(previous.get("process_io")),
                object_record(record.get("process_io")),
            )
            delta = object_record(total["io_delta"])
            for key, value in before_io.items():
                if type(value) is int and type(after_io.get(key)) is int:
                    # cancelled_write_bytes can fall; retain signed kernel deltas.
                    delta[key] = (
                        int(str(delta.get(key, 0))) + int(str(after_io[key])) - value
                    )
            end = (
                record
                if record["event"] == "final"
                else object_record(record.get("previous_phase"))
            )
            if end.get("phase") != name or end.get("phase_ordinal") != previous.get(
                "phase_ordinal"
            ):
                raise ValueError("phase boundary does not close the preceding phase")
            total.update(
                last_completed=integer(end.get("completed")),
                last_total=integer(end.get("total")),
            )
        self.boundaries += 1
        self.final_seen = record["event"] == "final"
        self.previous = record

    def record(self) -> dict[str, Control]:
        return {
            "scope": "Worker phase boundary intervals; I/O includes telemetry/IPC and observations have explicit start/end windows. Startup and post-monitor cleanup are covered separately by whole-worker supervision.",
            "boundaries": self.boundaries,
            "final_observation_seen": self.final_seen,
            "phases": dict(self.totals),
        }
