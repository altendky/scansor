"""Whole-worker planning and measured telemetry; execution provenance only.

An engine limit is one reservation within the RSS budget. The plan does not
prove a scale target: measured worker high-water must also fit. Run large work
in a fresh worker so lifetime peak counters describe that operation.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import psutil

from scansor.mesh_controls import Control
from scansor.mesh_errors import MeshImportError

MIB = 1024 * 1024
PER_ROW_SCRATCH = 768
MAX_BATCH_ROWS = 65_536


@dataclass(frozen=True)
class MemoryPlan:
    budget_bytes: int
    baseline_bytes: int
    safety_bytes: int
    fixed_buffers_bytes: int
    engine_bytes: int
    resident_columns_bytes: int
    batch_rows: int
    batch_bytes: int
    reserved_bytes: int
    storage: str
    disk_estimate_bytes: int
    engine_temp_limit_bytes: int

    def record(self) -> dict[str, Control]:
        return asdict(self)


def plan_memory(
    *,
    budget_bytes: int,
    baseline_bytes: int,
    canonical_bytes: int,
    vertices: int,
    faces: int,
    source_bytes: int,
    storage: str = "auto",
    chunk_rows: int | None = None,
) -> MemoryPlan:
    for name, value in (
        ("budget", budget_bytes),
        ("baseline", baseline_bytes),
        ("canonical bytes", canonical_bytes),
        ("vertices", vertices),
        ("faces", faces),
        ("source bytes", source_bytes),
    ):
        if type(value) is not int or value < 0:
            raise MeshImportError(
                "resource-budget-too-small", "planning", f"invalid {name}"
            )
    if storage not in ("auto", "ram", "disk"):
        raise MeshImportError("execution", "planning", "unknown column storage")
    if chunk_rows is not None and (
        type(chunk_rows) is not int or not 1 <= chunk_rows <= MAX_BATCH_ROWS
    ):
        raise MeshImportError(
            "resource-budget-too-small", "planning", "invalid requested chunk size"
        )
    # Controls/XML, snapshot/hash buffers, Arrow metadata and monitor overhead.
    # Per-row scratch includes decoder records, canonical copies, Arrow input
    # and output, two live face buffers, numeric scratch and status/index masks.
    safety, fixed = max(64 * MIB, budget_bytes // 10), 32 * MIB
    usable = budget_bytes - baseline_bytes - safety - fixed
    if usable < 32 * MIB + PER_ROW_SCRATCH:
        raise MeshImportError(
            "resource-budget-too-small",
            "planning",
            "baseline, safety and minimum engine/batch do not fit",
        )
    engine = min(256 * MIB, max(32 * MIB, usable // 2))
    available = usable - engine
    requested = MAX_BATCH_ROWS if chunk_rows is None else chunk_rows
    # Reserve the complete import+contribution column family even during S3.
    use_ram = storage == "ram" or (
        storage == "auto" and canonical_bytes + requested * PER_ROW_SCRATCH <= available
    )
    resident = canonical_bytes if use_ram else 0
    rows = min(requested, (available - resident) // PER_ROW_SCRATCH)
    if rows < 1 or (chunk_rows is not None and rows != chunk_rows):
        raise MeshImportError(
            "resource-budget-too-small",
            "planning",
            "requested columns and chunk do not fit",
        )
    # Batch sizes are capped, so the initial half-share can leave usable memory
    # unassigned. Give that remainder to DuckDB without reducing the baseline,
    # safety, resident-column or batch reservations. Retain the 256 MiB ceiling;
    # engine memory is only one part of the measured whole-worker working set.
    engine = min(256 * MIB, usable - resident - rows * PER_ROW_SCRATCH)
    # Conservative working estimate: staged coordinates/faces, long-corner
    # association, two ordering generations, database pages and conversion slack.
    # It is deliberately separate from the packed 25-byte reference tuple model.
    working = 128 * vertices + 384 * faces + 64 * MIB
    temporary_limit = max(64 * MIB, 256 * faces + 64 * vertices)
    disk_estimate = source_bytes + canonical_bytes + working + temporary_limit
    reserved = (
        baseline_bytes + safety + fixed + engine + resident + rows * PER_ROW_SCRATCH
    )
    return MemoryPlan(
        budget_bytes,
        baseline_bytes,
        safety,
        fixed,
        engine,
        resident,
        rows,
        rows * PER_ROW_SCRATCH,
        reserved,
        "ram" if use_ram else "disk",
        disk_estimate,
        temporary_limit,
    )


class AllocationLedger:
    """Reservations for caller-managed resident columns and bounded batch work.

    This is not a native-allocation profiler. RSS/OS peaks independently cover
    engine allocation, allocator retention, metadata and library overhead.
    """

    def __init__(self, capacity: int) -> None:
        self.capacity: int = capacity
        self.current: int = 0
        self.high_water: int = 0
        self._active: dict[str, int] = {}

    @contextmanager
    def reserve(self, name: str, count: int) -> Generator[None]:
        if (
            type(count) is not int
            or count < 0
            or name in self._active
            or self.current + count > self.capacity
        ):
            raise MeshImportError(
                "resource-budget-too-small",
                "allocation",
                "reservation exceeds plan or repeats an active name",
            )
        self._active[name] = count
        self.current += count
        self.high_water = max(self.high_water, self.current)
        try:
            yield
        finally:
            self.current -= self._active.pop(name)


def memory_snapshot() -> dict[str, Control]:
    info = psutil.Process().memory_info()
    peak: int | None
    if sys.platform == "win32":
        peak = int(info.peak_wset)
    else:
        import resource

        peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform != "darwin":
            peak *= 1024
    return {"rss_bytes": int(info.rss), "os_peak_rss_bytes": peak}


def process_io_snapshot() -> dict[str, Control]:
    """Linux process counters include telemetry, metadata and IPC, not just data."""
    if sys.platform != "linux":
        return {"available": False}
    with Path("/proc/self/io").open("r", encoding="ascii") as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise MeshImportError(
            "execution", "worker-measurement", "unexpected process I/O record size"
        )
    return {
        name: int(value)
        for name, value in (line.split(":", 1) for line in raw.splitlines())
    }


def cgroup_snapshot() -> dict[str, Control]:
    """Linux cgroup charge is separate from process RSS; do not equate them."""
    if sys.platform != "linux":
        return {"available": False}
    try:
        entry = next(
            line[3:]
            for line in Path("/proc/self/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        path = Path("/sys/fs/cgroup") / entry.lstrip("/")
        # In a cgroup namespace the mount itself can already be our group.
        if not (path / "memory.current").is_file():
            path = Path("/sys/fs/cgroup")
        result: dict[str, Control] = {"available": True}
        for name in (
            "memory.current",
            "memory.peak",
            "memory.max",
            "memory.swap.current",
            "memory.swap.max",
            "memory.events",
        ):
            try:
                result[name] = (path / name).read_text().strip()
            except OSError:
                result[name] = None
        return result
    except (OSError, StopIteration):
        return {"available": False}


def disk_snapshot(root: Path) -> dict[str, Control]:
    logical = allocated = 0
    partial = False
    allocation_available = True
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                info = (Path(directory) / name).stat(follow_symlinks=False)
            except FileNotFoundError:
                partial = True
                continue
            logical += info.st_size
            if hasattr(info, "st_blocks"):
                allocated += int(info.st_blocks) * 512
            else:
                allocation_available = False
    return {
        "logical_bytes": logical,
        "allocated_bytes": allocated if allocation_available else None,
        "concurrent_changes": partial,
    }


def check_disk_space(directory: Path, required: int) -> None:
    if shutil.disk_usage(directory).free < required:
        raise MeshImportError(
            "resource",
            "planning",
            "available disk is below the conservative run estimate",
        )


class ResourceMonitor:
    """Owned sampling, periodic progress, immediate phase records and cancellation.

    check() propagates monitor/callback failures and budget violations. The owner
    installs DuckDB's interrupt callback while that connection is alive, then
    clears it before closing. Phase callbacks run synchronously; observation
    windows expose telemetry overhead. A killed process requires the supervisor.
    """

    def __init__(
        self,
        budget_bytes: int,
        directory: Path,
        *,
        callback: Callable[[dict[str, Control]], None] | None = None,
        sample_seconds: float = 0.01,
        report_seconds: float = 1.0,
    ) -> None:
        if (
            type(budget_bytes) is not int
            or budget_bytes <= 0
            or not 0 < sample_seconds <= 0.01
            or not 0 < report_seconds <= 5
        ):
            raise MeshImportError(
                "resource-budget-too-small",
                "monitor",
                "invalid budget or telemetry interval",
            )
        self.budget_bytes: int = budget_bytes
        self.directory: Path = directory
        self.callback: Callable[[dict[str, Control]], None] | None = callback
        self.sample_seconds: float = sample_seconds
        self.report_seconds: float = report_seconds
        self._stop: threading.Event = threading.Event()
        self._lock: threading.RLock = threading.RLock()
        self._report_lock: threading.RLock = threading.RLock()
        self._failure: BaseException | None = None
        self._interrupt: Callable[[], None] | None = None
        self._phase: str = "initializing"
        self._completed: int = 0
        self._total: int = 0
        self._peak: int = 0
        self._rss: int = 0
        self._disk: dict[str, Control] = {}
        self._disk_peak: int = 0
        self._disk_allocated_peak: int | None = None
        self._started: float = time.monotonic()
        self._phase_started_ns: int = time.monotonic_ns()
        self._phase_ordinal: int = 0
        self._thread: threading.Thread | None = None
        self._plan: MemoryPlan | None = None

    def __enter__(self) -> ResourceMonitor:
        self.sample()
        self.check()
        if self.callback is not None:
            self._emit("phase", boundary_ns=self._phase_started_ns)
            self.check()
        self._thread = threading.Thread(
            target=self._run, name="scansor-mesh-monitor", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        if exc_type is None:
            self.sample()
            self.check()
            if self.callback is not None:
                self._emit("final")
                self.check()
        elif self.callback is not None:
            # Preserve the initiating failure if final telemetry also fails.
            with suppress(BaseException):
                self._emit("final")

    def set_interrupt(self, interrupt: Callable[[], None] | None) -> None:
        with self._lock:
            self._interrupt = interrupt

    def set_plan(self, plan: MemoryPlan) -> None:
        """Retain immutable reservations in phase/failure telemetry, not just success."""
        if plan.budget_bytes != self.budget_bytes:
            raise MeshImportError("execution", "planning", "monitor budget differs")
        with self._lock:
            self._plan = plan

    def _fail(self, failure: BaseException) -> None:
        with self._lock:
            if self._failure is not None:
                return
            self._failure = failure
            interrupt = self._interrupt
            if interrupt is not None:
                # Serialize with clearing the callback before connection.close().
                # Preserve the initiating resource/cancellation failure.
                with suppress(Exception):
                    interrupt()

    def cancel(self) -> None:
        self._fail(MeshImportError("cancelled", self._phase, "operation cancelled"))

    def check(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def progress(self, phase: str, completed: int, total: int) -> None:
        self.check()
        # Serialize phase changes with periodic delivery, without holding the
        # memory-sampling/interrupt lock while invoking a caller's callback.
        with self._report_lock:
            with self._lock:
                if not 0 <= completed <= total or (
                    phase == self._phase
                    and (completed < self._completed or total != self._total)
                ):
                    raise MeshImportError(
                        "execution", phase, "progress counters are not monotone"
                    )
                changed = phase != self._phase
                previous: dict[str, Control] = {
                    "phase": self._phase,
                    "phase_ordinal": self._phase_ordinal,
                    "phase_started_ns": self._phase_started_ns,
                    "completed": self._completed,
                    "total": self._total,
                }
                if changed:
                    self._phase_ordinal += 1
                    self._phase_started_ns = time.monotonic_ns()
                self._phase, self._completed, self._total = phase, completed, total
            if changed and self.callback is not None:
                self._emit(
                    "phase", boundary_ns=self._phase_started_ns, previous=previous
                )
                self.check()

    def sample(self) -> None:
        current = memory_snapshot()
        rss, peak = (
            int(str(current["rss_bytes"])),
            int(str(current["os_peak_rss_bytes"])),
        )
        with self._lock:
            self._rss = rss
            self._peak = max(self._peak, rss, peak)
        if self._peak > self.budget_bytes:
            self._fail(
                MeshImportError(
                    "resource",
                    self._phase,
                    "measured worker RSS high-water exceeds budget",
                )
            )

    def record(self) -> dict[str, Control]:
        with self._lock:
            return {
                "phase": self._phase,
                "completed": self._completed,
                "total": self._total,
                "rss_bytes": self._rss,
                "peak_rss_bytes": self._peak,
                "budget_bytes": self.budget_bytes,
                "plan": None if self._plan is None else self._plan.record(),
                "elapsed_ns": int((time.monotonic() - self._started) * 1_000_000_000),
                "disk": dict(self._disk),
                "sampled_disk_peak_logical_bytes": self._disk_peak,
                "sampled_disk_peak_allocated_bytes": self._disk_allocated_peak,
                "phase_ordinal": self._phase_ordinal,
                "phase_started_ns": self._phase_started_ns,
            }

    def _emit(
        self,
        event: str,
        *,
        boundary_ns: int | None = None,
        previous: dict[str, Control] | None = None,
    ) -> None:
        with self._report_lock:
            observation_start = time.monotonic_ns()
            disk = disk_snapshot(self.directory)
            io = process_io_snapshot()
            with self._lock:
                self._disk = disk
                self._disk_peak = max(self._disk_peak, int(str(disk["logical_bytes"])))
                allocated = disk["allocated_bytes"]
                if type(allocated) is int:
                    self._disk_allocated_peak = max(
                        self._disk_allocated_peak or 0, allocated
                    )
                record = self.record()
            record.update(
                event=event,
                monotonic_ns=observation_start if boundary_ns is None else boundary_ns,
                observation_started_ns=observation_start,
                observation_finished_ns=time.monotonic_ns(),
                process_io=io,
                previous_phase=previous,
            )
            if self.callback is not None:
                self.callback(record)

    def _run(self) -> None:
        reported = 0.0
        try:
            while not self._stop.is_set():
                self.sample()
                now = time.monotonic()
                if now - reported >= self.report_seconds:
                    self._emit("periodic")
                    reported = now
                if self._stop.wait(self.sample_seconds):
                    break
        except BaseException as error:
            self._fail(error)
