"""Private fresh-process entry point used by mesh_supervisor on Linux."""

from __future__ import annotations

import os
import signal
import stat
import sys
from pathlib import Path
from types import FrameType
from typing import BinaryIO, cast

from scansor.mesh_accounting import account_import
from scansor.mesh_controls import MAX_CONTROL_BYTES, Control, control_id, decode_control
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_import import prepare_import
from scansor.mesh_numeric import NumericProfileError
from scansor.mesh_publication import (
    PublishedStage,
    publish_contributions,
    publish_import,
)
from scansor.mesh_replay import verify_mesh
from scansor.mesh_resources import cgroup_snapshot, memory_snapshot
from scansor.mesh_worker_protocol import FrameWriter
from scansor.mesh_worker_request import WorkerRequest

_cancel_requested = False


def _cancel(_signal: int, _frame: FrameType | None) -> None:
    # A Python signal handler must not acquire monitor/IPC locks. The regular
    # progress callback raises and triggers DuckDB interruption; the parent can
    # kill the worker if native execution cannot finish within the grace period.
    global _cancel_requested
    _cancel_requested = True


def _check_cancel() -> None:
    if _cancel_requested:
        raise MeshImportError("cancelled", "worker", "operation cancelled")


def stage_record(stage: PublishedStage) -> dict[str, Control]:
    return {"kind": stage.kind, "path": str(stage.path), "identity": stage.identity}


def process_io() -> dict[str, Control]:
    """Kernel process counters include metadata and IPC, not only mesh payloads."""
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


def _run(
    request: WorkerRequest,
    work: Path,
    writer: FrameWriter,
    published: list[Control],
    publication_staging: Path | None,
) -> dict[str, Control]:
    def progress(record: dict[str, Control]) -> None:
        _check_cancel()
        writer.send({"type": "progress", "record": record})

    if request.operation == "verify":
        return verify_mesh(
            request.source,
            work,
            contribution_path=request.contribution,
            expected_import_id=request.expected_import_id,
            expected_contribution_id=request.expected_contribution_id,
            budget_bytes=request.budget_bytes,
            storage=request.storage,
            chunk_rows=request.chunk_rows,
            progress=progress,
        )
    assert request.destination is not None
    if request.destination.resolve(strict=True).is_relative_to(
        work.parent.resolve(strict=True)
    ):
        raise MeshImportError(
            "structure",
            "worker-request",
            "published artifacts must be outside disposable worker storage",
        )
    with (
        prepare_import(
            request.source,
            work,
            sidecar=request.sidecar,
            budget_bytes=request.budget_bytes,
            storage=request.storage,
            chunk_rows=request.chunk_rows,
            progress=progress,
        ) as foundation,
        account_import(foundation) as imported,
    ):
        _check_cancel()
        first = publish_import(
            imported, request.destination, staging_directory=publication_staging
        )
        published.append(stage_record(first))
        writer.send({"type": "published", "stage": stage_record(first)})
        _check_cancel()
        contributions = imported.complete_contributions()
        second = publish_contributions(
            imported,
            contributions,
            request.destination,
            staging_directory=publication_staging,
        )
        published.append(stage_record(second))
        writer.send({"type": "published", "stage": stage_record(second)})
        result: dict[str, Control] = {
            "revision": "mesh-import-run-v1",
            "status": "complete",
            "import": stage_record(first),
            "contribution": stage_record(second),
            "source_id": imported.inventory["source_id"],
            "vertices": foundation.vertices,
            "faces": foundation.faces,
            "execution": foundation.execution_report(),
        }
    return result


def main() -> int:
    writer = FrameWriter(cast(BinaryIO, sys.stdout.buffer))
    published: list[Control] = []
    _ = signal.signal(signal.SIGTERM, _cancel)
    _ = signal.signal(signal.SIGINT, _cancel)
    try:
        if len(sys.argv) not in (2, 3):
            raise MeshImportError(
                "structure",
                "worker-request",
                "a work descriptor and optional publication descriptor are required",
            )
        descriptor = int(sys.argv[1])
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise MeshImportError(
                "integrity", "worker-request", "inherited workspace is not private"
            )
        access = Path(f"/proc/self/fd/{descriptor}")
        publication_staging: Path | None = None
        if len(sys.argv) == 3:
            publication_fd = int(sys.argv[2])
            publication_info = os.fstat(publication_fd)
            if (
                not stat.S_ISDIR(publication_info.st_mode)
                or publication_info.st_uid != os.geteuid()
                or stat.S_IMODE(publication_info.st_mode) & 0o077
            ):
                raise MeshImportError(
                    "integrity",
                    "worker-request",
                    "publication workspace is not private",
                )
            publication_staging = Path(f"/proc/self/fd/{publication_fd}")
        request_fd = os.open(
            access / "request.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(request_fd, "rb") as stream:
            source = os.fstat(stream.fileno())
            if not stat.S_ISREG(source.st_mode) or source.st_size > MAX_CONTROL_BYTES:
                raise MeshImportError(
                    "structure",
                    "worker-request",
                    "request must be a bounded regular file",
                )
            request = WorkerRequest.from_record(
                decode_control(stream.read(MAX_CONTROL_BYTES + 1))
            )
        work = access / "work"
        entry = work.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(entry.st_mode)
            or stat.S_IMODE(entry.st_mode) & 0o077
            or any(work.iterdir())
        ):
            raise MeshImportError(
                "integrity",
                "worker-request",
                "worker scratch is not private and empty; resume is unsupported",
            )
        _check_cancel()
        writer.send(
            {
                "type": "started",
                "pid": os.getpid(),
                "request_id": control_id(request.record()),
            }
        )
        result = _run(request, work, writer, published, publication_staging)
        _check_cancel()
        writer.send(
            {
                "type": "result",
                "status": "complete",
                "result": result,
                "published": published,
                "memory_after_cleanup": memory_snapshot(),
                "io_after_cleanup": process_io(),
                "cgroup": cgroup_snapshot(),
            }
        )
        return 0
    except BaseException as error:
        if isinstance(error, OSError):
            error = io_failure(error, "worker")
        elif isinstance(error, NumericProfileError):
            error = MeshImportError("numeric-profile-failure", "worker", str(error))
        category = (
            error.category
            if isinstance(error, MeshImportError)
            else "resource"
            if isinstance(error, MemoryError)
            else "execution"
        )
        writer.send(
            {
                "type": "result",
                "status": "failed",
                "published": published,
                "failure": {
                    "category": category,
                    "stage": error.stage
                    if isinstance(error, MeshImportError)
                    else "worker",
                    "message": str(error)[:4096],
                    "row": error.row if isinstance(error, MeshImportError) else None,
                    "notes": [
                        str(note)[:4096] for note in getattr(error, "__notes__", [])[:8]
                    ],
                },
                "memory_after_cleanup": memory_snapshot(),
                "io_after_cleanup": process_io(),
                "cgroup": cgroup_snapshot(),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
