"""Fresh Linux workers with bounded IPC, lifetime RSS and owned cleanup.

The parent owns the disposable workspace before launch, so it can clean that
same inode after SIGKILL/OOM. Published stages live outside it. wait4 obtains the
specific child's kernel peak even when the child cannot write a final report.
No Popen.poll/wait/send_signal call may reap it before that measurement.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    import resource

from scansor.errors import ScansorError
from scansor.mesh_controls import Control, control_id, encode_control
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_publication_copy import directory_mount
from scansor.mesh_worker_protocol import FrameReader
from scansor.mesh_worker_request import WorkerRequest
from scansor.mesh_workspace import Workspace, create_workspace, remove_owned_workspace

# Leave scheduling and bounded protocol-processing headroom below S6's 10 ms
# observation target. Five-millisecond polls missed it in full-size runs. This
# requested interval is not a guarantee: retain actual gaps and kernel peaks.
SAMPLE_SECONDS = 0.001
CANCEL_GRACE_SECONDS = 3.0


def _kernel_usage(usage: resource.struct_rusage) -> dict[str, Control]:
    return {
        "user_cpu_ns": round(usage.ru_utime * 1_000_000_000),
        "system_cpu_ns": round(usage.ru_stime * 1_000_000_000),
        "minor_faults": usage.ru_minflt,
        "major_faults": usage.ru_majflt,
        "filesystem_inputs": usage.ru_inblock,
        "filesystem_outputs": usage.ru_oublock,
        "voluntary_context_switches": usage.ru_nvcsw,
        "involuntary_context_switches": usage.ru_nivcsw,
    }


def _failure(
    category: str, message: str, *, stage: str = "supervisor"
) -> dict[str, Control]:
    return {
        "category": category,
        "stage": stage,
        "message": message[:4096],
        "row": None,
        "notes": [],
    }


def _object(value: Control) -> dict[str, Control]:
    if not isinstance(value, dict):
        raise MeshImportError(
            "integrity", "worker-protocol", "expected an object frame field"
        )
    return value


def _retention(value: object) -> bool:
    if type(value) is not bool:
        raise MeshImportError(
            "structure", "supervisor", "retention must be explicit bool"
        )
    return value


@dataclass
class _Messages:
    request: WorkerRequest
    pid: int
    callback: Callable[[dict[str, Control]], None] | None
    started: bool = False
    result: dict[str, Control] | None = None
    last_progress: dict[str, Control] | None = None
    published: list[Control] = field(default_factory=list)

    def accept(self, message: dict[str, Control]) -> None:
        if self.result is not None:
            raise MeshImportError(
                "integrity", "worker-protocol", "worker emitted data after its result"
            )
        kind = message.get("type")
        if kind == "started":
            if (
                self.started
                or message.keys() != {"type", "pid", "request_id"}
                or type(message["pid"]) is not int
                or message["pid"] != self.pid
                or message["request_id"] != control_id(self.request.record())
            ):
                raise MeshImportError(
                    "integrity",
                    "worker-protocol",
                    "worker startup does not bind the launched request",
                )
            self.started = True
        elif kind == "progress":
            record = _object(message.get("record"))
            if (
                not self.started
                or message.keys() != {"type", "record"}
                or type(record.get("phase")) is not str
            ):
                raise MeshImportError(
                    "integrity", "worker-protocol", "invalid progress frame"
                )
            completed, total = record.get("completed"), record.get("total")
            if (
                type(completed) is not int
                or type(total) is not int
                or not 0 <= completed <= total
                or record.get("budget_bytes") != self.request.budget_bytes
            ):
                raise MeshImportError(
                    "integrity",
                    "worker-protocol",
                    "invalid progress population or budget",
                )
            self.last_progress = record
        elif kind == "published":
            stage = _object(message.get("stage"))
            identity = stage.get("identity")
            kinds = self.request.published_kinds
            expected_kind = (
                kinds[len(self.published)] if len(self.published) < len(kinds) else None
            )
            if (
                not self.started
                or expected_kind is None
                or message.keys() != {"type", "stage"}
                or stage.keys() != {"kind", "path", "identity"}
                or stage["kind"] != expected_kind
                or type(identity) is not str
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise MeshImportError(
                    "integrity", "worker-protocol", "invalid published-stage frame"
                )
            assert self.request.destination is not None
            if stage["path"] != str(
                self.request.destination / f"mesh-{expected_kind}-{identity}"
            ):
                raise MeshImportError(
                    "integrity",
                    "worker-protocol",
                    "published path is outside the requested destination",
                )
            self.published.append(stage)
        elif kind == "result":
            status = message.get("status")
            required = {
                "type",
                "status",
                "published",
                "memory_after_cleanup",
                "io_after_cleanup",
                "cgroup",
                "result" if status == "complete" else "failure",
            }
            if (
                message.keys() != required
                or status not in ("complete", "failed")
                or message["published"] != self.published
                or (status == "complete" and not self.started)
            ):
                raise MeshImportError(
                    "integrity", "worker-protocol", "invalid final worker report"
                )
            for name in (
                "memory_after_cleanup",
                "io_after_cleanup",
                "cgroup",
                "result" if status == "complete" else "failure",
            ):
                _ = _object(message[name])
            if status == "complete" and len(self.published) != len(
                self.request.published_kinds
            ):
                raise MeshImportError(
                    "integrity",
                    "worker-protocol",
                    "operation completed without its required published stages",
                )
            self.result = message
        else:
            raise MeshImportError(
                "integrity", "worker-protocol", "unknown worker frame"
            )
        if self.callback is not None:
            self.callback(message)


def _worker_command(descriptor: int, publication_descriptor: int | None) -> list[str]:
    command = [sys.executable, "-m", "scansor.mesh_worker", str(descriptor)]
    if publication_descriptor is not None:
        command.append(str(publication_descriptor))
    return command


def _write_record(path: Path, record: dict[str, Control]) -> None:
    raw = encode_control(record)
    with path.open("xb") as stream:
        view, done = memoryview(raw), 0
        while done < len(view):
            count = stream.write(view[done:])
            if type(count) is not int or not 0 < count <= len(view) - done:
                raise MeshImportError(
                    "execution", "supervisor", "control write made no progress"
                )
            done += count
        stream.flush()
        os.fsync(stream.fileno())


def run_worker(
    request: WorkerRequest,
    workdir: Path,
    *,
    progress: Callable[[dict[str, Control]], None] | None = None,
    cancel: threading.Event | None = None,
    retain_incomplete: bool = False,
) -> dict[str, Control]:
    """Run one fresh worker synchronously; failure always has an explicit report.

    The latest progress frame, up to two required published stages and one final
    result are retained. Cancellation first sends SIGTERM, then SIGKILL after three
    seconds if needed. Nothing outside the held workspace is cleaned.
    """
    if sys.platform != "linux" or not hasattr(os, "wait4"):
        raise MeshImportError(
            "unsupported", "supervisor", "Linux wait4 worker supervision is unavailable"
        )
    retain_incomplete = _retention(retain_incomplete)
    request = WorkerRequest.from_record(request.record())
    for path in request.readonly_artifacts:
        for writable in (workdir, request.destination):
            if writable is not None and writable.resolve(strict=True).is_relative_to(
                path.resolve(strict=True)
            ):
                raise MeshImportError(
                    "structure",
                    "supervisor",
                    "scratch and destination must be outside read-only artifact trees",
                )
    workspace = create_workspace(workdir)
    publication: Workspace | None = None
    access = workspace.access
    process: subprocess.Popen[bytes] | None = None
    failure: dict[str, Control] | None = None
    report: dict[str, Control] = {}
    messages: _Messages | None = None
    stderr = bytearray()
    stderr_total = 0
    peak = samples = max_gap = 0
    previous_sample: int | None = None
    first_sample: int | None = None
    launch_started: int | None = None
    launch_returned: int | None = None
    reaped: int | None = None
    gaps_over_10ms = 0
    kernel_peak = 0
    usage_record: dict[str, Control] = {}
    started_ns = time.monotonic_ns()
    stop_deadline: float | None = None
    killed = False
    receiving = True
    parser = FrameReader()
    try:
        (access / "work").mkdir(mode=0o700)
        _write_record(access / "request.json", request.record())
        if request.published_kinds:
            assert request.destination is not None
            target = os.open(
                request.destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            try:
                if directory_mount(target) != directory_mount(workspace.descriptor):
                    publication = create_workspace(request.destination)
            finally:
                os.close(target)
        roots = [workspace] + ([] if publication is None else [publication])
        for root in roots:
            _write_record(
                root.access / "owner.json",
                {
                    "revision": "mesh-worker-staging-v1",
                    "status": "incomplete",
                    "supervisor_pid": os.getpid(),
                    "request_id": control_id(request.record()),
                },
            )
        launch_started = time.monotonic_ns()
        process = subprocess.Popen(
            _worker_command(
                workspace.descriptor,
                None if publication is None else publication.descriptor,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=tuple(root.descriptor for root in roots),
            start_new_session=True,
        )
        launch_returned = time.monotonic_ns()
        assert process.stdout is not None and process.stderr is not None
        messages = _Messages(request, process.pid, progress)
        # Reuse identity metadata, not memory measurements. The sole wait4
        # reaper below prevents PID reuse while this live-process loop samples.
        observed_process = psutil.Process(process.pid)
        for root in roots:
            _write_record(
                root.access / "worker.json",
                {
                    "revision": "mesh-worker-process-v1",
                    "pid": process.pid,
                    "pid_namespace_inode": os.stat("/proc/self/ns/pid").st_ino,
                    "create_time_ns": int(
                        observed_process.create_time() * 1_000_000_000
                    ),
                },
            )
        with selectors.DefaultSelector() as selector:
            _ = selector.register(process.stdout, selectors.EVENT_READ, "stdout")
            _ = selector.register(process.stderr, selectors.EVENT_READ, "stderr")
            while process.returncode is None or selector.get_map():
                now = time.monotonic_ns()
                if process.returncode is None:
                    try:
                        rss = observed_process.memory_info().rss
                        peak = max(peak, rss)
                        samples += 1
                        if first_sample is None:
                            first_sample = now
                        if previous_sample is not None:
                            gap = now - previous_sample
                            max_gap = max(max_gap, gap)
                            gaps_over_10ms += int(gap > 10_000_000)
                        previous_sample = now
                        if rss > request.budget_bytes and failure is None:
                            failure = _failure(
                                "resource",
                                "sampled whole-worker RSS exceeded its budget",
                            )
                    except psutil.NoSuchProcess:
                        pass
                    if cancel is not None and cancel.is_set() and failure is None:
                        failure = _failure("cancelled", "operation cancelled")
                    if failure is not None and stop_deadline is None:
                        with suppress(ProcessLookupError):
                            os.kill(process.pid, signal.SIGTERM)
                        stop_deadline = time.monotonic() + CANCEL_GRACE_SECONDS
                    elif (
                        stop_deadline is not None
                        and time.monotonic() >= stop_deadline
                        and not killed
                    ):
                        with suppress(ProcessLookupError):
                            os.kill(process.pid, signal.SIGKILL)
                        killed = True
                for key, _events in selector.select(SAMPLE_SECONDS):
                    data = os.read(key.fd, 65_536)
                    if not data:
                        _ = selector.unregister(key.fd)
                    elif key.data == "stderr":
                        stderr_total += len(data)
                        stderr.extend(data[: max(0, 4096 - len(stderr))])
                    elif receiving:
                        # A requested stop does not invalidate the protocol.
                        # Keep cleanup/final observations and any publication
                        # that raced with cancellation; preserve the initiating
                        # resource/cancellation failure as the overall outcome.
                        try:
                            for message in parser.feed(data):
                                messages.accept(message)
                        except BaseException as error:
                            receiving = False
                            if failure is None:
                                failure = _failure(
                                    "worker-protocol"
                                    if isinstance(error, ScansorError)
                                    else "execution",
                                    str(error),
                                )
                            else:
                                failure["notes"] = [
                                    "Further worker telemetry could not be accepted: "
                                    + str(error)[:2048]
                                ]
                if process.returncode is None:
                    pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                    if pid:
                        reaped = time.monotonic_ns()
                        process.returncode = os.waitstatus_to_exitcode(status)
                        kernel_peak = int(usage.ru_maxrss) * 1024
                        usage_record = _kernel_usage(usage)
        if failure is None:
            try:
                parser.finish()
            except ScansorError as error:
                failure = _failure("worker-protocol", str(error))
        if failure is None and kernel_peak > request.budget_bytes:
            failure = _failure(
                "resource", "kernel whole-worker peak exceeded its budget"
            )
        if failure is None and (
            messages.result is None
            or process.returncode
            != (0 if messages.result.get("status") == "complete" else 1)
        ):
            failure = _failure(
                "worker-exit",
                f"worker exited abnormally or without a complete report (exit code {process.returncode})",
            )
        if (
            failure is None
            and messages.result is not None
            and messages.result["status"] == "failed"
        ):
            failure = _object(messages.result["failure"])
    except BaseException as error:
        if isinstance(error, OSError):
            error = io_failure(error, "supervisor")
        failure = _failure(
            error.category
            if isinstance(error, MeshImportError)
            else "resource"
            if isinstance(error, MemoryError)
            else "execution",
            str(error),
        )
    finally:
        if process is not None:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    os.kill(process.pid, signal.SIGKILL)
                _pid, status, usage = os.wait4(process.pid, 0)
                reaped = time.monotonic_ns()
                process.returncode = os.waitstatus_to_exitcode(status)
                kernel_peak = int(usage.ru_maxrss) * 1024
                usage_record = _kernel_usage(usage)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        initial_gap = (
            None
            if first_sample is None or launch_started is None
            else first_sample - launch_started
        )
        terminal_gap = (
            None
            if previous_sample is None or reaped is None
            else reaped - previous_sample
        )
        report = {
            "revision": "mesh-worker-outcome-v1",
            "status": "complete" if failure is None else "failed",
            "failure": failure,
            "worker_report": None if messages is None else messages.result,
            "published": [] if messages is None else messages.published,
            "last_progress": None if messages is None else messages.last_progress,
            "supervision": {
                "pid": None if process is None else process.pid,
                "exit_code": None if process is None else process.returncode,
                "elapsed_ns": time.monotonic_ns() - started_ns,
                "budget_bytes": request.budget_bytes,
                "sampled_peak_rss_bytes": peak,
                "kernel_peak_rss_bytes": kernel_peak,
                "requested_sample_interval_ns": round(SAMPLE_SECONDS * 1_000_000_000),
                "samples": samples,
                "largest_observed_sample_gap_ns": max_gap,
                "sampling_edges": {
                    "scope": "Conservative coverage from before process launch through wait4 reaping, including parent launch/observation overhead around the child's actual lifetime.",
                    "launch_started_ns": launch_started,
                    "launch_returned_ns": launch_returned,
                    "first_sample_ns": first_sample,
                    "last_sample_ns": previous_sample,
                    "reaped_ns": reaped,
                    "initial_gap_ns": initial_gap,
                    "terminal_gap_ns": terminal_gap,
                    "largest_gap_including_edges_ns": max(
                        max_gap, initial_gap or 0, terminal_gap or 0
                    ),
                    "gaps_over_10ms_including_edges": gaps_over_10ms
                    + int(initial_gap is not None and initial_gap > 10_000_000)
                    + int(terminal_gap is not None and terminal_gap > 10_000_000),
                },
                "kernel_usage": usage_record,
                "stderr": bytes(stderr).decode("utf-8", errors="replace"),
                "stderr_truncated": stderr_total > len(stderr),
            },
        }
        for candidate, prefix in ((publication, "publication_"), (workspace, "")):
            if candidate is None:
                continue
            try:
                if failure is not None and retain_incomplete:
                    _write_record(
                        candidate.access / "failure.json",
                        {
                            "revision": "mesh-incomplete-supervisor-v1",
                            "status": "incomplete",
                            "failure": failure,
                            "last_progress": report["last_progress"],
                            "exit_code": None
                            if process is None
                            else process.returncode,
                        },
                    )
                    entry = candidate.directory.stat(follow_symlinks=False)
                    if (entry.st_dev, entry.st_ino) != candidate.identity:
                        raise MeshImportError(
                            "integrity",
                            "cleanup",
                            "retained workspace path was replaced",
                        )
                    report[prefix + "incomplete_directory"] = str(candidate.directory)
                else:
                    remove_owned_workspace(candidate.directory, candidate.identity)
            except BaseException as cleanup_error:
                report["status"] = "failed"
                report[prefix + "cleanup_failure"] = str(cleanup_error)[:4096]
                if report["failure"] is None:
                    report["failure"] = _failure(
                        cleanup_error.category
                        if isinstance(cleanup_error, MeshImportError)
                        else "execution",
                        str(cleanup_error),
                        stage="cleanup",
                    )
                with suppress(OSError):
                    entry = candidate.directory.stat(follow_symlinks=False)
                    if (entry.st_dev, entry.st_ino) == candidate.identity:
                        report[prefix + "incomplete_directory"] = str(
                            candidate.directory
                        )
                    else:
                        report[prefix + "replaced_workspace_path"] = str(
                            candidate.directory
                        )
            finally:
                candidate.close()
    return report


def discover_staging(workdir: Path, *, limit: int = 128) -> dict[str, Control]:
    """Inspect bounded staging markers; never resume or remove a candidate.

    Matching PID creation time is a liveness hint, not an ownership grant. Unknown
    directories and changed/malformed records remain unrecognized and untouched.
    """
    from contextlib import ExitStack

    from scansor.mesh_artifact_io import ReadDirectory, ReadFile

    if type(limit) is not int or not 1 <= limit <= 256:
        raise MeshImportError("structure", "staging-discovery", "invalid result bound")
    entries: list[Control] = []
    truncated = False

    def control(
        stack: ExitStack, directory: ReadDirectory, name: str
    ) -> dict[str, Control]:
        file = ReadFile(directory, name)
        _ = stack.callback(file.close)
        if file.size > 65_536:
            raise MeshImportError(
                "structure", "staging-discovery", "staging marker exceeds 64 KiB"
            )
        return file.control()

    with os.scandir(workdir) as candidates:
        for candidate in candidates:
            if not candidate.name.startswith(".scansor-mesh-"):
                continue
            if len(entries) == limit:
                truncated = True
                break
            record: dict[str, Control] = {
                "path": str(workdir / candidate.name),
                "state": "unrecognized",
            }
            try:
                with ExitStack() as stack:
                    directory = ReadDirectory(workdir / candidate.name)
                    _ = stack.callback(directory.close)

                    owner = control(stack, directory, "owner.json")
                    if (
                        owner.keys()
                        != {"revision", "status", "supervisor_pid", "request_id"}
                        or owner["revision"] != "mesh-worker-staging-v1"
                        or owner["status"] != "incomplete"
                    ):
                        raise MeshImportError(
                            "structure",
                            "staging-discovery",
                            "unknown staging owner marker",
                        )
                    worker = control(stack, directory, "worker.json")
                    pid, created = worker.get("pid"), worker.get("create_time_ns")
                    if (
                        worker.keys()
                        != {"revision", "pid", "create_time_ns", "pid_namespace_inode"}
                        or worker["revision"] != "mesh-worker-process-v1"
                        or type(pid) is not int
                        or pid <= 0
                        or type(created) is not int
                        or created <= 0
                        or type(worker["pid_namespace_inode"]) is not int
                    ):
                        raise MeshImportError(
                            "structure", "staging-discovery", "unknown worker marker"
                        )
                    same_namespace = (
                        worker["pid_namespace_inode"]
                        == os.stat("/proc/self/ns/pid").st_ino
                    )
                    matching = False
                    if same_namespace:
                        with suppress(psutil.NoSuchProcess):
                            matching = (
                                int(psutil.Process(pid).create_time() * 1_000_000_000)
                                == created
                            )
                    record.update(
                        {
                            "state": "unknown-namespace"
                            if not same_namespace
                            else "possibly-active"
                            if matching
                            else "stale",
                            "worker_pid": pid,
                            "request_id": owner["request_id"],
                        }
                    )
                    directory.check()
            except (OSError, ScansorError, psutil.Error) as error:
                record["diagnostic"] = str(error)[:1024]
            entries.append(record)
    return {
        "revision": "mesh-staging-discovery-v1",
        "entries": entries,
        "truncated": truncated,
    }
