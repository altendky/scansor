"""Replayable sources decode without seeking, snapshots, or unbounded reads."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast, override

import numpy as np
import pytest

from experiments.scan_project import ScanProject
from experiments.scan_sources import BytesInput, PathInput
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    prepare_scan,
    prepare_scan_input,
)
from experiments.virtual_scan_reduction import ReductionSettings, prepare_virtual_scan
from tests.native_scan_fixture import native_grid_fixture

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000, chunk_bytes=240)


class SequentialInput:
    def __init__(self, raw: bytes, *, short: int = 7) -> None:
        self.raw: bytes = raw
        self.byte_length: int = len(raw)
        self.filename: str = "sequential.ply"
        self.short: int = short
        self.opens: int = 0
        self.closes: int = 0
        self.requests: list[int] = []
        self.read_bytes: int = 0

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        owner = self

        class Stream(io.RawIOBase):
            position: int = 0

            @override
            def read(self, size: int = -1) -> bytes:
                assert 0 < size <= LIMITS.chunk_bytes, (
                    "unbounded or oversized underlying read"
                )
                owner.requests.append(size)
                chunk = owner.raw[
                    self.position : self.position + min(size, owner.short)
                ]
                self.position += len(chunk)
                owner.read_bytes += len(chunk)
                return chunk

            @override
            def seek(self, offset: int, whence: int = 0) -> int:
                raise AssertionError("sequential source must never seek")

        self.opens += 1
        try:
            with Stream() as stream:
                yield cast(BinaryIO, cast(object, stream))
        finally:
            self.closes += 1


def test_one_pass_nonseeking_decode_preserves_original_byte_identity_and_geometry() -> (
    None
):
    fixture = native_grid_fixture(5, 5)
    source = SequentialInput(fixture.raw)
    workspace = prepare_scan_input(source, LIMITS)
    assert workspace.source is not None
    assert workspace.source.input is source
    assert source.opens == source.closes == 1
    assert source.read_bytes == len(fixture.raw)
    assert max(source.requests) <= LIMITS.chunk_bytes
    assert not hasattr(workspace, "original_bytes")
    assert (
        workspace.source.descriptor.source_sha256
        == hashlib.sha256(fixture.raw).hexdigest()
    )
    assert workspace.source.byte_length == len(fixture.raw)
    np.testing.assert_array_equal(workspace.local, fixture.positions)
    np.testing.assert_array_equal(workspace.data.triangles, fixture.triangles)
    np.testing.assert_allclose(workspace.data.weights, fixture.weights, rtol=1e-15)


def test_exact_replay_verifies_hash_and_rejects_mutation_truncation_and_growth(
    tmp_path: Path,
) -> None:
    raw = native_grid_fixture(5, 5).raw
    path = tmp_path / "scan.ply"
    _ = path.write_bytes(raw)
    authority = PathInput(path, "source.ply", owner=object())
    workspace = prepare_scan_input(authority, LIMITS)
    assert workspace.source is not None
    workspace.source.verify()
    for changed in (raw + b"x", raw[:-1], raw[:-4] + b"\0\0\0\0"):
        assert changed != raw
        _ = path.write_bytes(changed)
        with pytest.raises(ScanImportError) as error:
            workspace.source.verify()
        assert error.value.code == "source_mismatch"
    _ = path.write_bytes(raw)
    workspace.source.verify()
    with pytest.raises(ScanImportError, match="byte cap"):
        _ = workspace.source.read_all(len(raw) - 1)


def test_declared_size_limits_precede_open_and_layout_length_must_match() -> None:
    raw = native_grid_fixture(5, 5).raw
    source = SequentialInput(raw)
    with pytest.raises(ScanImportError) as limited:
        _ = prepare_scan_input(source, replace(LIMITS, max_source_bytes=len(raw) - 1))
    assert limited.value.code == "source_limit" and source.opens == 0
    for declared in (len(raw) - 1, len(raw) + 1):
        source = SequentialInput(raw)
        source.byte_length = declared
        with pytest.raises(ScanImportError):
            _ = prepare_scan_input(source, LIMITS)
        assert source.opens == source.closes == 1


def test_sequential_cancellation_closes_source_before_reading_all_payload() -> None:
    source = SequentialInput(native_grid_fixture(17, 17).raw)
    cancelled = False

    def progress(record: ScanProgress) -> None:
        nonlocal cancelled
        if record.phase == "vertices" and record.completed > 0:
            cancelled = True

    with pytest.raises(ScanCancelled):
        _ = prepare_scan_input(
            source, LIMITS, progress=progress, cancelled=lambda: cancelled
        )
    assert source.opens == source.closes == 1
    assert source.read_bytes < source.byte_length


def test_legacy_small_bytesio_remains_explicitly_bounded_and_snapshotted() -> None:
    raw = native_grid_fixture(5, 5).raw
    stream = io.BytesIO(raw)
    workspace = prepare_scan(stream, "legacy.ply", LIMITS)
    stream.close()
    assert workspace.source is not None
    assert isinstance(workspace.source.input, BytesInput)
    assert workspace.source.read_all(2_000_000) == raw


def test_reduced_source_replays_from_accepted_arrays_without_packed_byte_ownership() -> (
    None
):
    original = prepare_scan_input(
        BytesInput(native_grid_fixture(17, 17).raw, "original.ply"), LIMITS
    )
    reduced = prepare_virtual_scan(original, ReductionSettings(divisions=4), LIMITS)
    workspace = reduced.workspace
    assert workspace.source is not None
    assert not isinstance(workspace.source.input, BytesInput)
    source = workspace.source.input
    assert vars(source)["xyz"] is workspace.data.xyz
    assert vars(source)["normals"] is workspace.data.original_normals
    assert vars(source)["triangles"] is workspace.data.triangles
    reopened = prepare_scan_input(workspace.source, LIMITS)
    assert reopened.reference_sha256 == workspace.reference_sha256
    np.testing.assert_array_equal(reopened.local, workspace.local)


def test_activation_revalidates_retained_path_authority_without_replacing_current_project(
    tmp_path: Path,
) -> None:
    raw = native_grid_fixture(17, 17).raw
    path = tmp_path / "scan.ply"
    _ = path.write_bytes(raw)
    project = ScanProject()
    candidate = project.prepare_add(
        PathInput(path),
        path.name,
        LIMITS,
        generation=0,
        token=str(project.accepted.graph.snapshot()["token"]),
    )
    original = project.commit(candidate)
    candidate = project.prepare_virtual_scan(
        ReductionSettings(divisions=4),
        LIMITS,
        generation=original.generation,
        token=str(original.graph.snapshot()["token"]),
    )
    accepted = project.commit_replace(candidate)
    _ = path.write_bytes(raw + b"changed")
    with pytest.raises(ScanImportError) as error:
        _ = project.prepare_activate_scan(
            original.workspace.reference_sha256,
            LIMITS,
            generation=accepted.generation,
            token=str(accepted.graph.snapshot()["token"]),
        )
    assert error.value.code == "source_mismatch"
    assert project.accepted is accepted
