"""Native staging ownership, cancellation and authoritative commit races."""

from __future__ import annotations

import io
import json
import struct
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, BinaryIO, ClassVar, cast, override

import pytest

from experiments.native_scan_import import NativeImportContext, NativeScanImports
from experiments.scan_project import AcceptedScan, PreparedScan, ScanProject
from experiments.scan_workspace import ScanCancelled, ScanImportError, ScanLimits


def mesh_bytes() -> bytes:
    header = (
        b"ply\nformat binary_little_endian 1.0\n"
        b"element vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        b"property float nx\nproperty float ny\nproperty float nz\n"
        b"element face 1\nproperty list uchar int vertex_indices\nend_header\n"
    )
    return (
        header
        + b"".join(
            struct.pack("<6f", *point, 0, 0, 2)
            for point in ((0, 0, 0), (2, 0, 0), (0, 3, 0))
        )
        + struct.pack("<B3i", 3, 0, 1, 2)
    )


class World:
    def __init__(self) -> None:
        self.lock: RLock = RLock()
        self.project: ScanProject = ScanProject()
        self.offset: int = 100

    def context(self) -> NativeImportContext:
        accepted = self.project.accepted
        return NativeImportContext(
            self.project,
            self.offset + accepted.generation,
            str(accepted.graph.snapshot()["token"]),
        )

    def reserve(
        self, service: NativeScanImports, *, size: int | None = None
    ) -> dict[str, object]:
        context = self.context()
        return service.reserve(
            generation=context.generation,
            token=context.token,
            filename="../independent.ply",
            size=len(mesh_bytes()) if size is None else size,
        )


@pytest.fixture
def owned(tmp_path: Path) -> Iterator[tuple[World, NativeScanImports, Path]]:
    world = World()
    staging = tmp_path / "staging"
    staging.mkdir()
    service = NativeScanImports(
        lock=world.lock,
        context=world.context,
        limits=ScanLimits(4096, 100, 100, 10_000_000, chunk_bytes=64),
        staging_directory=staging,
    )
    try:
        yield world, service, staging
    finally:
        service.close()


def terminal(
    service: NativeScanImports, operation: dict[str, object]
) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while True:
        record = service.status(str(operation["operationId"]))
        _ = json.dumps(record, allow_nan=False)
        if record["terminal"]:
            return record
        assert time.monotonic() < deadline, record
        _ = Event().wait(0.002)


def files(staging: Path) -> list[Path]:
    return [path for path in staging.rglob("*") if path.is_file()]


def upload(
    service: NativeScanImports,
    operation: dict[str, object],
    stream: BinaryIO | None = None,
) -> dict[str, object]:
    return service.receive(
        str(operation["operationId"]),
        io.BytesIO(mesh_bytes()) if stream is None else stream,
        len(mesh_bytes()),
    )


def cancel(
    service: NativeScanImports, operation: dict[str, object]
) -> dict[str, object]:
    return service.cancel(
        str(operation["operationId"]),
        generation=cast(int, operation["generation"]),
        token=str(operation["token"]),
    )


def test_exact_bounded_upload_commits_source_only_and_releases_staging(
    owned: tuple[World, NativeScanImports, Path],
) -> None:
    world, service, staging = owned
    original = world.project.accepted
    operation = world.reserve(service)
    assert service.busy and world.project.accepted is original

    class ShortReads(io.BytesIO):
        requests: ClassVar[list[int]] = []

        @override
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= 64
            self.requests.append(size)
            return super().read(min(size, 7))

    stream = ShortReads(mesh_bytes() + b"next HTTP request")
    _ = upload(service, operation, stream)
    result = terminal(service, operation)
    assert result["status"] == "accepted" and result["acceptedGeneration"] == 101
    assert cast(int, result["revision"]) > cast(int, operation["revision"])
    assert stream.tell() == len(mesh_bytes())
    assert world.project.accepted.workspace.original_bytes == mesh_bytes()
    assert world.project.accepted.workspace.name == "independent.ply"
    assert (
        len(
            cast(dict[str, Any], world.project.accepted.graph.snapshot()["recipe"])[
                "nodes"
            ]
        )
        == 1
    )
    assert not service.busy and files(staging) == []
    assert len(json.dumps(result)) < 1000
    assert not any(key in result for key in ("path", "project", "candidate", "bytes"))
    assert cancel(service, operation) == result


def test_reserved_cancel_and_single_active_quota_allocate_no_payload(
    owned: tuple[World, NativeScanImports, Path],
) -> None:
    world, service, staging = owned
    operation = world.reserve(service)
    with pytest.raises(ScanImportError) as conflict:
        _ = world.reserve(service)
    assert conflict.value.code == "source_busy"
    result = cancel(service, operation)
    assert result["terminal"] and result["status"] == "cancelled"
    assert not service.busy and files(staging) == []
    with pytest.raises(ScanImportError, match="not reserved"):
        _ = upload(service, operation)
    assert service.status(str(operation["operationId"])) == result


@pytest.mark.parametrize("length", [0, -1, 1, True])
def test_wrong_upload_length_fails_before_read_or_file_creation(
    owned: tuple[World, NativeScanImports, Path], length: int
) -> None:
    world, service, staging = owned
    operation = world.reserve(service)
    stream = io.BytesIO(mesh_bytes())
    with pytest.raises(ScanImportError) as failure:
        _ = service.receive(str(operation["operationId"]), stream, length)
    assert failure.value.code == "invalid_upload" and stream.tell() == 0
    assert terminal(service, operation)["status"] == "failed"
    assert not service.busy and files(staging) == []


@pytest.mark.parametrize(
    "mode", ["short", "timeout", "oversized-read", "malformed-ply"]
)
def test_bad_or_disconnected_body_cleans_and_preserves_accepted(
    owned: tuple[World, NativeScanImports, Path], mode: str
) -> None:
    world, service, staging = owned
    original = world.project.accepted
    operation = world.reserve(service)

    class Body(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            if mode == "timeout":
                raise TimeoutError("bounded socket idle timeout")
            if mode == "oversized-read":
                assert size is not None
                return b"x" * (size + 1)
            return super().read(size)

    raw = (
        mesh_bytes()[:-7]
        if mode == "short"
        else b"x" * len(mesh_bytes())
        if mode == "malformed-ply"
        else mesh_bytes()
    )
    if mode == "malformed-ply":
        _ = upload(service, operation, Body(raw))
    else:
        with pytest.raises(ScanImportError):
            _ = upload(service, operation, Body(raw))
    result = terminal(service, operation)
    assert result["status"] == "failed" and "error" in result
    assert world.project.accepted is original and files(staging) == []


def test_cancel_blocked_receiver_waits_for_safe_stop_without_holding_coordinator(
    owned: tuple[World, NativeScanImports, Path],
) -> None:
    world, service, staging = owned
    operation = world.reserve(service)
    entered, release = Event(), Event()
    errors: list[Exception] = []

    class Blocked(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            entered.set()
            assert release.wait(5)
            return super().read(size)

    def receive() -> None:
        try:
            _ = upload(service, operation, Blocked(mesh_bytes()))
        except Exception as error:
            errors.append(error)

    thread = Thread(target=receive)
    thread.start()
    try:
        assert entered.wait(5)
        assert len(files(staging)) == 1
        result = cancel(service, operation)
        assert result["status"] == "cancelling" and not result["terminal"]
        assert service.busy and service.status(str(operation["operationId"])) == result
        with pytest.raises(ScanImportError) as duplicate:
            _ = upload(service, operation)
        assert duplicate.value.code == "invalid_upload"
    finally:
        release.set()
        thread.join(5)
    assert (
        not thread.is_alive()
        and len(errors) == 1
        and isinstance(errors[0], ScanCancelled)
    )
    assert terminal(service, operation)["status"] == "cancelled"
    assert files(staging) == [] and world.project.accepted.workspace.descriptor is None


def gate_preparation(
    monkeypatch: pytest.MonkeyPatch, world: World
) -> tuple[Event, Event]:
    entered, release = Event(), Event()
    prepare = world.project.prepare_add

    def gated(*args: Any, **kwargs: Any) -> PreparedScan:
        candidate = prepare(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return candidate

    monkeypatch.setattr(world.project, "prepare_add", gated)
    return entered, release


def test_cancel_completed_preparation_cleans_before_terminal_without_commit(
    owned: tuple[World, NativeScanImports, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, service, staging = owned
    entered, release = gate_preparation(monkeypatch, world)
    original = world.project.accepted
    operation = world.reserve(service)
    _ = upload(service, operation)
    try:
        assert entered.wait(5)
        result = cancel(service, operation)
        assert result["status"] == "cancelling" and not result["terminal"]
        assert world.project.accepted is original
        assert len(files(staging)) == 1
    finally:
        release.set()
    assert terminal(service, operation)["status"] == "cancelled"
    assert world.project.accepted is original and files(staging) == []


@pytest.mark.parametrize("change", ["generation", "project-identity"])
def test_changed_context_cannot_publish_prepared_candidate(
    owned: tuple[World, NativeScanImports, Path],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    world, service, staging = owned
    entered, release = gate_preparation(monkeypatch, world)
    operation = world.reserve(service)
    _ = upload(service, operation)
    try:
        assert entered.wait(5)
        with world.lock:
            if change == "generation":
                old = world.project.accepted
                _ = world.project.reset(
                    generation=old.generation, token=str(old.graph.snapshot()["token"])
                )
            else:
                # Even an identical empty cursor does not authorize another owner.
                world.project = ScanProject()
    finally:
        release.set()
    result = terminal(service, operation)
    assert result["status"] == "failed"
    assert cast(dict[str, str], result["error"])["code"] == "stale_source"
    assert world.project.accepted.workspace.descriptor is None and files(staging) == []


def test_commit_wins_cancel_without_retroactive_rollback(
    owned: tuple[World, NativeScanImports, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, service, staging = owned
    entered, release, requested, cancelled = Event(), Event(), Event(), Event()
    commit = world.project.commit
    records: list[dict[str, object]] = []

    def gated(*args: Any, **kwargs: Any) -> AcceptedScan:
        entered.set()
        assert files(staging) == []
        assert release.wait(5)
        return commit(*args, **kwargs)

    monkeypatch.setattr(world.project, "commit", gated)
    operation = world.reserve(service)
    _ = upload(service, operation)

    def cancelling() -> None:
        requested.set()
        records.append(cancel(service, operation))
        cancelled.set()

    thread = Thread(target=cancelling)
    try:
        assert entered.wait(5)
        thread.start()
        assert requested.wait(5)
        assert not cancelled.wait(0.02)
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(5)
    assert cancelled.is_set() and not thread.is_alive()
    result = terminal(service, operation)
    assert result["status"] == "accepted" and records == [result]
    assert world.project.accepted.workspace.original_bytes == mesh_bytes()


def test_expired_reservations_and_terminal_history_are_bounded(tmp_path: Path) -> None:
    world = World()
    now = [10.0]
    service = NativeScanImports(
        lock=world.lock,
        context=world.context,
        limits=ScanLimits(4096, 100, 100, 10_000_000),
        staging_directory=tmp_path,
        reservation_timeout_seconds=2,
        max_terminal_history=2,
        clock=lambda: now[0],
    )
    try:
        first = world.reserve(service)
        now[0] += 3
        assert not service.busy
        assert service.status(str(first["operationId"]))["status"] == "expired"
        second = world.reserve(service)
        _ = cancel(service, second)
        third = world.reserve(service)
        _ = cancel(service, third)
        with pytest.raises(ScanImportError) as missing:
            _ = service.status(str(first["operationId"]))
        assert missing.value.code == "unknown_operation"
        assert files(tmp_path) == []
    finally:
        service.close()


def test_preflight_and_cancel_authentication_do_not_change_reserved_state(
    owned: tuple[World, NativeScanImports, Path],
) -> None:
    world, service, staging = owned
    context = world.context()
    with pytest.raises(ScanImportError) as large:
        _ = world.reserve(service, size=4097)
    assert large.value.code == "source_limit"
    with pytest.raises(ScanImportError) as stale:
        _ = service.reserve(
            generation=context.generation + 1,
            token=context.token,
            filename="x.ply",
            size=1,
        )
    assert stale.value.code == "stale_source" and not service.busy
    operation = world.reserve(service)
    with pytest.raises(ScanImportError):
        _ = service.cancel(
            str(operation["operationId"]), generation=context.generation, token="wrong"
        )
    assert (
        service.status(str(operation["operationId"])) == operation
        and files(staging) == []
    )


def test_close_cancels_preparation_joins_and_preserves_foreign_files(
    owned: tuple[World, NativeScanImports, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, service, staging = owned
    sentinel = staging / "foreign.txt"
    _ = sentinel.write_text("keep")
    entered, release = gate_preparation(monkeypatch, world)
    operation = world.reserve(service)
    _ = upload(service, operation)
    done = Event()

    def closing() -> None:
        service.close()
        done.set()

    thread = Thread(target=closing)
    try:
        assert entered.wait(5)
        service.begin_close()
        assert service.status(str(operation["operationId"]))["status"] == "cancelling"
        with pytest.raises(ScanImportError) as stopped:
            _ = world.reserve(service)
        assert stopped.value.code == "import_closed"
        thread.start()
        assert not done.wait(0.02)
    finally:
        release.set()
        if thread.ident is not None:
            thread.join(5)
    assert done.is_set() and not thread.is_alive()
    assert service.status(str(operation["operationId"]))["status"] == "cancelled"
    assert files(staging) == [sentinel] and sentinel.read_text() == "keep"
    assert world.project.accepted.workspace.descriptor is None


def test_progress_records_are_ordered_and_contain_no_private_candidate(
    owned: tuple[World, NativeScanImports, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    world, service, _ = owned
    entered, release = gate_preparation(monkeypatch, world)
    operation = world.reserve(service)
    _ = upload(service, operation)
    try:
        assert entered.wait(5)
        update = service.status(str(operation["operationId"]))
        assert update["phase"] == "prepared" and not update["terminal"]
        assert update["unit"] == "rows" and update["completed"] == update["total"] == 4
        assert cast(int, update["revision"]) > cast(int, operation["revision"])
        _ = json.dumps(update, allow_nan=False)
        assert not any(
            key in update for key in ("path", "project", "candidate", "bytes")
        )
    finally:
        release.set()
    assert terminal(service, operation)["status"] == "accepted"


def test_evaluation_and_nonempty_source_reject_before_upload_allocation(
    owned: tuple[World, NativeScanImports, Path],
) -> None:
    world, service, staging = owned
    with world.project.evaluation(), pytest.raises(ScanImportError) as busy:
        _ = world.reserve(service)
    assert busy.value.code == "source_busy"
    assert files(staging) == []
    operation = world.reserve(service)
    _ = upload(service, operation)
    assert terminal(service, operation)["status"] == "accepted"
    with pytest.raises(ScanImportError) as nonempty:
        _ = world.reserve(service)
    assert nonempty.value.code == "source_exists"
    assert files(staging) == []


@pytest.mark.parametrize("cancelled", [False, True])
def test_cleanup_failure_prevents_commit_and_fences_more_staging(
    owned: tuple[World, NativeScanImports, Path],
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    world, service, staging = owned
    original = Path.unlink

    def fail_owned(path: Path, missing_ok: bool = False) -> None:
        if path.suffix == ".upload":
            raise OSError("injected staging cleanup failure")
        original(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_owned)
    operation = world.reserve(service)

    class CancelledEOF(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            _ = cancel(service, operation)
            return b""

    if cancelled:
        with pytest.raises(ScanCancelled):
            _ = upload(service, operation, CancelledEOF())
    else:
        _ = upload(service, operation)
    record = terminal(service, operation)
    assert record["status"] == "failed"
    assert cast(dict[str, object], record["error"])["code"] == "cleanup_failed"
    assert world.project.accepted.workspace.descriptor is None
    assert len(files(staging)) == 1
    with pytest.raises(ScanImportError) as closed:
        _ = world.reserve(service)
    assert closed.value.code == "import_closed"
    _ = json.dumps(record, allow_nan=False)
    cast(dict[str, object], record["error"])["message"] = "changed caller copy"
    assert "injected staging cleanup failure" in str(
        service.status(str(operation["operationId"]))["error"]
    )
    monkeypatch.setattr(Path, "unlink", original)


@pytest.mark.parametrize("stop", ["eof", "timeout"])
def test_explicit_cancel_wins_when_upload_read_stops_with_eof_or_timeout(
    owned: tuple[World, NativeScanImports, Path], stop: str
) -> None:
    world, service, staging = owned
    original = world.project.accepted
    operation = world.reserve(service)
    observed: list[dict[str, object]] = []

    class CancelledRead(io.BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            observed.append(cancel(service, operation))
            if stop == "timeout":
                raise TimeoutError("socket timeout after explicit cancellation")
            return b""

    with pytest.raises(ScanCancelled):
        _ = upload(service, operation, CancelledRead())
    assert observed[0]["status"] == "cancelling" and not observed[0]["terminal"]
    record = terminal(service, operation)
    assert record["status"] == "cancelled"
    assert cast(dict[str, object], record["error"])["code"] == "cancelled"
    assert world.project.accepted is original and files(staging) == []
