"""Bounded native source operations over the shared scan transaction core.

The HTTP caller owns socket timeouts and closes/joins its receivers before
``close``. This service never reads beyond Content-Length, never publishes a
candidate before cleanup, and never owns a second accepted-state pointer.
"""

from __future__ import annotations

import math
import tempfile
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Condition, Event, Lock, RLock
from typing import BinaryIO, cast
from uuid import uuid4

from experiments.scan_project import ScanProject
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    check_cancelled,
)


@dataclass(frozen=True)
class NativeImportContext:
    project: ScanProject
    generation: int
    token: str


@dataclass
class _Operation:
    id: str
    generation: int
    token: str
    filename: str
    size: int
    created: float
    project: ScanProject | None
    internal_generation: int
    cancelled: Event = field(default_factory=Event)
    revision: int = 0
    status: str = "reserved"
    phase: str = "reserved"
    completed: int = 0
    total: int = 0
    unit: str = "bytes"
    terminal: bool = False
    error: dict[str, str] | None = None
    accepted_generation: int | None = None
    path: Path | None = None

    def record(self) -> dict[str, object]:
        result: dict[str, object] = {
            "operationId": self.id,
            "generation": self.generation,
            "token": self.token,
            "revision": self.revision,
            "status": self.status,
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "unit": self.unit,
            "terminal": self.terminal,
        }
        if self.error is not None:
            result["error"] = self.error.copy()
        if self.accepted_generation is not None:
            result["acceptedGeneration"] = self.accepted_generation
        return result


class NativeScanImports:
    """One active reservation/upload/preparation, plus bounded small history."""

    def __init__(
        self,
        *,
        lock: RLock,
        context: Callable[[], NativeImportContext],
        limits: ScanLimits,
        staging_directory: Path | None = None,
        reservation_timeout_seconds: float = 60.0,
        max_terminal_history: int = 16,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not math.isfinite(reservation_timeout_seconds)
            or reservation_timeout_seconds <= 0
            or type(max_terminal_history) is not int
            or max_terminal_history < 1
        ):
            raise ValueError(
                "reservation lifetime and terminal history must be bounded"
            )
        self.limits: ScanLimits = limits
        self._lock: RLock = lock
        self._condition: Condition = Condition(lock)
        self._close_lock: Lock = Lock()
        self._context: Callable[[], NativeImportContext] = context
        self._clock: Callable[[], float] = clock
        self._reservation_timeout: float = reservation_timeout_seconds
        self._history_limit: int = max_terminal_history
        self._directory: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory(
            prefix="scansor-native-import-", dir=staging_directory
        )
        self._executor: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="scansor-source"
        )
        self._operations: dict[str, _Operation] = {}
        self._history: deque[str] = deque()
        self._active: str | None = None
        self._receivers: int = 0
        self._closing: bool = False
        self._closed: bool = False

    def _operation(self, operation_id: str) -> _Operation:
        try:
            return self._operations[operation_id]
        except KeyError as error:
            raise ScanImportError(
                "Unknown or expired source operation.", "unknown_operation"
            ) from error

    def _finish(
        self, operation: _Operation, status: str, error: ScanImportError | None = None
    ) -> None:
        """Called under the coordinator after owned staging reaches a safe end."""
        if operation.terminal:
            return
        operation.status = status
        operation.phase = status
        operation.terminal = True
        operation.revision += 1
        operation.project = None
        if error is not None:
            operation.error = {"code": error.code[:120], "message": str(error)[:2048]}
        if status == "accepted":
            operation.completed, operation.total, operation.unit = 1, 1, "steps"
        if self._active == operation.id:
            self._active = None
        self._history.append(operation.id)
        while len(self._history) > self._history_limit:
            del self._operations[self._history.popleft()]
        self._condition.notify_all()

    def _expire(self) -> None:
        if self._active is not None:
            operation = self._operations[self._active]
            if (
                operation.status == "reserved"
                and self._clock() - operation.created >= self._reservation_timeout
            ):
                self._finish(
                    operation,
                    "expired",
                    ScanImportError(
                        "The unuploaded source reservation expired.", "import_expired"
                    ),
                )

    @property
    def busy(self) -> bool:
        with self._lock:
            self._expire()
            return self._active is not None

    def reserve(
        self, *, generation: int, token: str, filename: str, size: int
    ) -> dict[str, object]:
        if (
            type(generation) is not int
            or generation < 0
            or not isinstance(cast(object, token), str)
            or not 0 < len(token) <= 256
        ):
            raise ScanImportError(
                "Expected a project generation and token.", "stale_source"
            )
        if not isinstance(cast(object, filename), str) or not 0 < len(filename) <= 512:
            raise ScanImportError("Expected a source filename hint.", "invalid_source")
        filename = PurePosixPath(filename.replace("\\", "/")).name
        if not filename:
            raise ScanImportError("Expected a source filename hint.", "invalid_source")
        if type(size) is not int or size <= 0:
            raise ScanImportError(
                "Expected a positive exact upload length.", "invalid_upload"
            )
        if size > self.limits.max_source_bytes:
            raise ScanImportError(
                "Upload exceeds the declared source-byte limit.", "source_limit"
            )
        with self._lock:
            self._expire()
            if self._closing:
                raise ScanImportError(
                    "Source import service is closing.", "import_closed"
                )
            if self._active is not None:
                raise ScanImportError("Another source import is active.", "source_busy")
            context = self._context()
            if generation != context.generation or token != context.token:
                raise ScanImportError(
                    "Project changed before source reservation.", "stale_source"
                )
            accepted = context.project.accepted
            context.project.check_add(generation=accepted.generation, token=token)
            operation = _Operation(
                uuid4().hex,
                generation,
                token,
                filename,
                size,
                self._clock(),
                context.project,
                accepted.generation,
                total=size,
            )
            self._operations[operation.id] = operation
            self._active = operation.id
            return operation.record()

    def status(self, operation_id: str) -> dict[str, object]:
        with self._lock:
            self._expire()
            return self._operation(operation_id).record()

    def cancel(
        self, operation_id: str, *, generation: int, token: str
    ) -> dict[str, object]:
        with self._lock:
            self._expire()
            operation = self._operation(operation_id)
            if generation != operation.generation or token != operation.token:
                raise ScanImportError(
                    "Cancellation cursor does not match its operation.", "stale_source"
                )
            if operation.terminal:
                return operation.record()
            operation.cancelled.set()
            if operation.status == "reserved":
                self._finish(operation, "cancelled", ScanCancelled())
            elif operation.status != "cancelling":
                operation.status = "cancelling"
                operation.revision += 1
            return operation.record()

    def _current(self, operation: _Operation) -> NativeImportContext:
        context = self._context()
        if (
            context.project is not operation.project
            or context.generation != operation.generation
            or context.token != operation.token
        ):
            raise ScanImportError(
                "Project changed before source commit.", "stale_source"
            )
        return context

    def _cleanup(self, operation: _Operation) -> None:
        path = operation.path
        if path is not None:
            path.unlink(missing_ok=True)
            with self._lock:
                operation.path = None

    def _failure(self, operation: _Operation, error: Exception) -> None:
        try:
            self._cleanup(operation)
        except OSError as cleanup_error:
            with self._lock:
                self._closing = True
            error = ScanImportError(
                f"Owned source staging cleanup failed: {cleanup_error}",
                "cleanup_failed",
            )
        if not isinstance(error, ScanImportError):
            error = ScanImportError(f"Source import failed: {error}", "import_failed")
        with self._lock:
            self._finish(
                operation,
                "cancelled" if isinstance(error, ScanCancelled) else "failed",
                error,
            )

    def receive(
        self, operation_id: str, stream: BinaryIO, content_length: int
    ) -> dict[str, object]:
        """Receive exactly one declared body; caller bounds blocking socket I/O."""
        with self._lock:
            self._expire()
            operation = self._operation(operation_id)
            if operation.terminal or operation.status != "reserved":
                raise ScanImportError(
                    "Source upload is not reserved or was already received.",
                    "invalid_upload",
                )
            if type(content_length) is not int or content_length != operation.size:
                error = ScanImportError(
                    "Content-Length does not match its reservation.", "invalid_upload"
                )
                self._finish(operation, "failed", error)
                raise error
            if self._closing:
                self._finish(operation, "cancelled", ScanCancelled())
                raise ScanCancelled()
            operation.status, operation.phase = "receiving", "upload"
            operation.revision += 1
            self._receivers += 1
        try:
            check_cancelled(operation.cancelled.is_set)
            path = Path(self._directory.name) / f"{operation.id}.upload"
            with path.open("xb") as staging:
                operation.path = path
                count = 0
                while count < operation.size:
                    check_cancelled(operation.cancelled.is_set)
                    requested = min(self.limits.chunk_bytes, operation.size - count)
                    try:
                        block = stream.read(requested)
                    except Exception:
                        check_cancelled(operation.cancelled.is_set)
                        raise
                    check_cancelled(operation.cancelled.is_set)
                    if (
                        not block
                        or not isinstance(cast(object, block), bytes)
                        or len(block) > requested
                    ):
                        raise ScanImportError(
                            "Upload ended early or violated its bounded byte stream.",
                            "invalid_upload",
                        )
                    check_cancelled(operation.cancelled.is_set)
                    _ = staging.write(block)
                    count += len(block)
                    with self._lock:
                        operation.completed = count
                        operation.revision += 1
            check_cancelled(operation.cancelled.is_set)
            with self._lock:
                _ = self._current(operation)
                check_cancelled(operation.cancelled.is_set)
                operation.status, operation.phase = "preparing", "preflight"
                operation.completed, operation.total, operation.unit = 0, 1, "steps"
                operation.revision += 1
                _ = self._executor.submit(self._prepare, operation.id)
                return operation.record()
        except Exception as error:
            self._failure(operation, error)
            if isinstance(error, ScanImportError):
                raise
            raise ScanImportError(
                f"Source upload failed: {error}", "invalid_upload"
            ) from error
        finally:
            with self._lock:
                self._receivers -= 1
                self._condition.notify_all()

    def _prepare(self, operation_id: str) -> None:
        with self._lock:
            operation = self._operation(operation_id)
            project, path = operation.project, operation.path
        assert project is not None and path is not None

        def progress(update: ScanProgress) -> None:
            with self._lock:
                check_cancelled(operation.cancelled.is_set)
                operation.phase = update.phase
                operation.completed, operation.total = update.completed, update.total
                operation.unit = (
                    "bytes"
                    if update.phase == "reading"
                    else "rows"
                    if update.phase in {"validation", "vertices", "faces", "prepared"}
                    else "steps"
                )
                operation.revision += 1

        try:
            with path.open("rb") as stream:
                candidate = project.prepare_add(
                    stream,
                    operation.filename,
                    self.limits,
                    generation=operation.internal_generation,
                    token=operation.token,
                    progress=progress,
                    cancelled=operation.cancelled.is_set,
                )
            # Duplicate staging is disposable once preparation owns exact bytes.
            # Complete fallible cleanup before the authoritative state commit.
            self._cleanup(operation)
            with self._lock:
                _ = self._current(operation)
                check_cancelled(operation.cancelled.is_set)
                operation.phase = "committing"
                operation.completed, operation.total, operation.unit = 0, 1, "steps"
                operation.revision += 1
                accepted = project.commit(
                    candidate, cancelled=operation.cancelled.is_set
                )
                operation.accepted_generation = (
                    operation.generation
                    + accepted.generation
                    - operation.internal_generation
                )
                self._finish(operation, "accepted")
        except Exception as error:
            self._failure(operation, error)

    def begin_close(self) -> None:
        """Fence new work and request cancellation without joining sockets."""
        with self._lock:
            self._closing = True
            if self._active is not None:
                operation = self._operations[self._active]
                operation.cancelled.set()
                if operation.status == "reserved":
                    self._finish(operation, "cancelled", ScanCancelled())
                elif operation.status != "cancelling":
                    operation.status = "cancelling"
                    operation.revision += 1

    def close(self) -> None:
        """Join safe receiver/preparation completion and release owned staging."""
        with self._close_lock:
            if self._closed:
                return
            self.begin_close()
            with self._condition:
                _ = self._condition.wait_for(lambda: self._receivers == 0)
            self._executor.shutdown(wait=True)
            self._directory.cleanup()
            self._closed = True
