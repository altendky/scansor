"""Archive locators, exact replay and bounded producer/consumer buffers."""

from __future__ import annotations

import hashlib
import io
import json
import random
import stat
import struct
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, BinaryIO, cast, override

import numpy as np
import pytest

from experiments.project_archive import (
    ProjectArchiveLimits,
    iter_project_archive,
    project_archive_profile,
    read_project_archive,
    write_project_archive,
)
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import ScanCancelled, ScanImportError


def actions(raw: bytes) -> dict[str, Any]:
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "schema_version": 2,
        "nodes": [
            {
                "id": "scan",
                "operation": "source",
                "label": "Source",
                "source_sha256": digest,
                "reference_sha256": "a" * 64,
                "descriptor": {
                    "source_sha256": digest,
                    "source_bytes": len(raw),
                    "filename": "original.ply",
                },
            }
        ],
        "groups": [],
        "output": "scan",
    }


def packed(
    raw: bytes,
    *,
    declaration: dict[str, Any] | None = None,
    method: int = zipfile.ZIP_DEFLATED,
    extras: list[tuple[str | zipfile.ZipInfo, bytes]] | None = None,
    manifest: bytes | None = None,
    payload: bytes | None = None,
) -> bytes:
    value = actions(raw) if declaration is None else declaration
    source = value["nodes"][0]
    entry = source.setdefault("archive_entry", "scans/input.ply")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=method) as archive:
        archive.writestr(
            "project.json",
            manifest
            if manifest is not None
            else json.dumps(
                {"format": "scansor-project", "version": 1, "actions": "actions.json"}
            ).encode(),
        )
        archive.writestr("actions.json", json.dumps(value).encode())
        archive.writestr(entry, raw if payload is None else payload)
        for name, data in extras or []:
            archive.writestr(name, data)
    return output.getvalue()


def contents(source: Any, chunk: int = 65536) -> bytes:
    pieces: list[bytes] = []
    with source.open() as stream:
        while block := stream.read(chunk):
            pieces.append(block)
    return b"".join(pieces)


@dataclass
class RecordedInput:
    data: bytes
    filename: str
    maximum: int
    reads: list[int] = field(default_factory=list)
    opened: int = 0

    @property
    def byte_length(self) -> int:
        return len(self.data)

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        self.opened += 1
        owner = self

        class Stream(io.BytesIO):
            @override
            def read(self, size: int | None = -1) -> bytes:
                assert size is not None
                assert 0 <= size <= owner.maximum, "unbounded authority read"
                owner.reads.append(size)
                return super().read(size)

        with Stream(self.data) as stream:
            yield cast(BinaryIO, stream)


def test_streamed_roundtrip_preserves_exact_source_and_normalizes_only_locator() -> (
    None
):
    raw = random.Random(17).randbytes(300_000)
    limits = replace(ProjectArchiveLimits(), chunk_bytes=1024)
    source = RecordedInput(raw, "original.ply", limits.chunk_bytes)
    recipe = actions(raw)
    output = io.BytesIO()
    receipt = write_project_archive(recipe, source, output, limits=limits)
    assert receipt.bytes_written == len(output.getvalue())
    assert receipt.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert "archive_entry" not in recipe["nodes"][0]
    authority = RecordedInput(output.getvalue(), "one.scansor", limits.chunk_bytes)
    opened = read_project_archive(authority, limits=limits)
    assert opened.actions == recipe
    assert opened.original is not None and opened.member_name == receipt.source_entry
    assert contents(opened.original, 1024) == raw
    assert contents(opened.original, 1024) == raw
    saved = io.BytesIO()
    second = write_project_archive(
        opened.actions, opened.original, saved, limits=limits
    )
    assert second.source_sha256 == receipt.source_sha256
    assert (
        contents(
            read_project_archive(
                BytesInput(saved.getvalue(), "again.scansor"), limits=limits
            ).original,
            1024,
        )
        == raw
    )
    assert source.reads and max(source.reads) <= 1024
    assert authority.reads and max(authority.reads) <= 1024


def test_iterator_backpressure_chunks_and_heartbeat_points() -> None:
    raw = b"x" * 300_000
    limits = replace(ProjectArchiveLimits(), chunk_bytes=1024)
    source = RecordedInput(raw, "source.ply", limits.chunk_bytes)
    producer = iter_project_archive(actions(raw), source, limits=limits)
    blocks: list[bytes] = []
    while True:
        try:
            block = next(producer)
        except StopIteration as stopped:
            receipt = stopped.value
            break
        assert len(block) <= limits.chunk_bytes
        blocks.append(block)
    assert b"" in blocks
    assert receipt.queued_peak_bytes <= max(
        limits.max_directory_bytes, 3 * limits.chunk_bytes + 65536
    )
    assert receipt.bytes_written == sum(map(len, blocks))
    assert (
        contents(
            read_project_archive(
                BytesInput(b"".join(blocks), "project.scansor"), limits=limits
            ).original,
            1024,
        )
        == raw
    )


def test_virtual_active_source_locator_references_original_not_derived_bytes() -> None:
    raw = b"original mesh bytes"
    recipe = actions(raw)
    original = dict(recipe["nodes"][0]["descriptor"])
    recipe["nodes"][0].update(
        source_sha256="b" * 64,
        reference_sha256="c" * 64,
        descriptor={"source_sha256": "b" * 64, "source_bytes": 1},
        virtual_scan={
            "original": original,
            "derived": {"source_sha256": "b" * 64},
            "settings": {"divisions": 4},
        },
    )
    output = io.BytesIO()
    receipt = write_project_archive(recipe, BytesInput(raw, "original.ply"), output)
    assert receipt.source_sha256 == hashlib.sha256(raw).hexdigest()
    opened = read_project_archive(BytesInput(output.getvalue(), "virtual.scansor"))
    assert opened.actions == recipe and contents(opened.original) == raw


@pytest.mark.parametrize("fault", ["hash", "length", "crc", "truncated"])
def test_member_corruption_never_becomes_verified_source(fault: str) -> None:
    raw = b"raw source payload" * 100
    value = actions(raw)
    if fault == "hash":
        value["nodes"][0]["descriptor"]["source_sha256"] = "0" * 64
    if fault == "length":
        value["nodes"][0]["descriptor"]["source_bytes"] += 1
    archive = packed(raw, declaration=value, method=zipfile.ZIP_STORED)
    if fault == "truncated":
        archive = archive[:-3]
    if fault == "crc":
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            info = zipped.getinfo("scans/input.ply")
        changed = bytearray(archive)
        name, extra = struct.unpack_from("<HH", changed, info.header_offset + 26)
        changed[info.header_offset + 30 + name + extra] ^= 1
        archive = bytes(changed)
    with pytest.raises(ScanImportError):
        opened = read_project_archive(BytesInput(archive, "corrupt.scansor"))
        _ = contents(opened.original)


@pytest.mark.parametrize(
    "name",
    [
        "../scan.ply",
        "/scan.ply",
        "a/../scan.ply",
        "a//scan.ply",
        "C:/scan.ply",
        "a\\scan.ply",
    ],
)
def test_unsafe_names_are_rejected_without_extraction(name: str) -> None:
    with pytest.raises(ScanImportError, match="relative names"):
        _ = read_project_archive(
            BytesInput(packed(b"source", extras=[(name, b"unused")]), "unsafe.scansor")
        )


def test_duplicate_entries_links_and_unsupported_compression_are_rejected() -> None:
    with pytest.warns(UserWarning), pytest.raises(ScanImportError, match="duplicate"):
        _ = read_project_archive(
            BytesInput(
                packed(b"source", extras=[("actions.json", b"{}")]), "duplicate.scansor"
            )
        )
    info = zipfile.ZipInfo("link.ply")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(ScanImportError, match="links"):
        _ = read_project_archive(
            BytesInput(packed(b"source", extras=[(info, b"source")]), "link.scansor")
        )
    with pytest.raises(ScanImportError, match="compression"):
        _ = read_project_archive(
            BytesInput(packed(b"source", method=zipfile.ZIP_BZIP2), "bzip.scansor")
        )


@pytest.mark.parametrize(
    "fault", ["entries", "directory", "expanded", "compressed", "actions"]
)
def test_archive_limits_precede_large_allocations(fault: str) -> None:
    data = packed(b"source" * 100)
    controls = {
        "entries": {"max_entries": 2},
        "directory": {"max_directory_bytes": 1},
        "expanded": {"max_expanded_bytes": 1},
        "compressed": {"max_archive_bytes": len(data) - 1},
        "actions": {"max_actions_bytes": 1},
    }
    source = RecordedInput(data, "limited.scansor", 65536)
    with pytest.raises(ScanImportError) as failed:
        _ = read_project_archive(
            source, limits=replace(ProjectArchiveLimits(), **controls[fault])
        )
    assert failed.value.code == "source_limit"
    if fault == "compressed":
        assert source.opened == 0


def test_encrypted_zip64_duplicate_json_and_bad_version_are_explicit() -> None:
    data = packed(b"source")
    for fault in ("encrypted", "zip64"):
        changed = bytearray(data)
        directory = changed.index(b"PK\x01\x02")
        struct.pack_into(
            "<H" if fault == "encrypted" else "<I",
            changed,
            directory + (8 if fault == "encrypted" else 24),
            1 if fault == "encrypted" else 0xFFFFFFFF,
        )
        with pytest.raises(ScanImportError):
            _ = read_project_archive(BytesInput(bytes(changed), "unsupported.scansor"))
    for manifest in (
        b'{"format":"scansor-project","version":1,"version":1,"actions":"actions.json"}',
        b'{"format":"scansor-project","version":2,"actions":"actions.json"}',
    ):
        with pytest.raises(ScanImportError):
            _ = read_project_archive(
                BytesInput(packed(b"source", manifest=manifest), "bad-json.scansor")
            )


def test_members_require_bounded_complete_consumption_and_keep_original_errors() -> (
    None
):
    opened = read_project_archive(BytesInput(packed(b"source"), "source.scansor"))
    assert opened.original is not None
    with (
        pytest.raises(ScanImportError, match="validated EOF"),
        opened.original.open() as stream,
    ):
        assert stream.read(1) == b"s"
    with (
        pytest.raises(ScanImportError, match="bounded"),
        opened.original.open() as stream,
    ):
        _ = stream.read()
    with (
        pytest.raises(RuntimeError, match="original validation"),
        opened.original.open(),
    ):
        raise RuntimeError("original validation")


@pytest.mark.parametrize("fault", ["flags", "name", "zip64", "size"])
def test_local_headers_must_agree_with_bounded_central_directory(fault: str) -> None:
    changed = bytearray(packed(b"source"))
    with zipfile.ZipFile(io.BytesIO(changed)) as archive:
        offset = archive.getinfo("scans/input.ply").header_offset
    if fault == "flags":
        struct.pack_into("<H", changed, offset + 6, 1)
    elif fault == "name":
        changed[offset + 30] ^= 1
    elif fault == "zip64":
        struct.pack_into("<H", changed, offset + 4, 45)
    else:
        struct.pack_into("<I", changed, offset + 18, 1)
    with pytest.raises(ScanImportError):
        _ = read_project_archive(BytesInput(bytes(changed), "local.scansor"))


def test_retained_member_does_not_keep_finished_operation_cancellation() -> None:
    cancelled = False
    opened = read_project_archive(
        BytesInput(packed(b"source"), "retained.scansor"),
        cancelled=lambda: cancelled,
    )
    cancelled = True
    assert contents(opened.original) == b"source"


def test_explicit_large_reads_return_bounded_short_blocks_through_verified_eof() -> (
    None
):
    raw = b"source" * 3000
    limits = replace(ProjectArchiveLimits(), chunk_bytes=1024)
    opened = read_project_archive(
        BytesInput(packed(raw), "small-chunks.scansor"), limits=limits
    )
    assert opened.original is not None
    chunks: list[bytes] = []
    with opened.original.open() as stream:
        while block := stream.read(65_536):
            assert len(block) <= limits.chunk_bytes
            chunks.append(block)
    assert b"".join(chunks) == raw


def test_broken_deflate_is_reported_as_archive_validation() -> None:
    changed = bytearray(packed(b"source" * 100))
    with zipfile.ZipFile(io.BytesIO(changed)) as archive:
        info = archive.getinfo("scans/input.ply")
    name_size, extra_size = struct.unpack_from("<HH", changed, info.header_offset + 26)
    payload = info.header_offset + 30 + name_size + extra_size
    changed[payload] = 0xFF  # Reserved DEFLATE block type.
    opened = read_project_archive(BytesInput(bytes(changed), "deflate.scansor"))
    with pytest.raises(ScanImportError, match="validation"):
        _ = contents(opened.original)


def test_cancellation_output_budget_and_changed_source_fail_before_receipt() -> None:
    raw = b"source" * 100
    with pytest.raises(ScanCancelled):
        _ = read_project_archive(
            BytesInput(packed(raw), "cancel.scansor"), cancelled=lambda: True
        )
    with pytest.raises(ScanImportError, match="output budget"):
        _ = write_project_archive(
            actions(raw),
            BytesInput(raw, "raw.ply"),
            io.BytesIO(),
            limits=replace(ProjectArchiveLimits(), max_archive_bytes=40),
        )
    with pytest.raises(ScanImportError, match="changed"):
        _ = write_project_archive(
            actions(raw), BytesInput(b"broken" * 100, "raw.ply"), io.BytesIO()
        )


def test_empty_project_and_profile_are_versioned_without_source_bytes() -> None:
    output = io.BytesIO()
    recipe = {"schema_version": 2, "nodes": [], "groups": [], "output": None}
    _ = write_project_archive(recipe, None, output)
    opened = read_project_archive(BytesInput(output.getvalue(), "empty.scansor"))
    assert opened.original is None and opened.actions == recipe
    profile_limits = cast(dict[str, int], project_archive_profile()["limits"])
    assert profile_limits["max_archive_bytes"] == 48 * 1024**2


def test_legacy_without_manifest_requires_explicit_recognizer_and_resaves_metadata() -> (
    None
):
    raw = b"known legacy source"
    recipe = actions(raw)
    recipe["nodes"][0]["descriptor"] = None
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("actions.json", json.dumps(recipe))
        archive.writestr("legacy.ply", raw)
    authority = BytesInput(output.getvalue(), "legacy.scansor")
    with pytest.raises(ScanImportError, match="known legacy"):
        _ = read_project_archive(authority)
    reference = {"format": "test-explicit-reference", "frame": "verified by caller"}
    opened = read_project_archive(
        authority,
        legacy_resolver=lambda value, member: (
            reference if value == recipe and member.byte_length == len(raw) else {}
        ),
    )
    assert opened.migrated_legacy is True and opened.legacy_reference == reference
    assert contents(opened.original) == raw
    saved = io.BytesIO()
    _ = write_project_archive(
        opened.actions, opened.original, saved, legacy_reference=reference
    )
    restored = read_project_archive(BytesInput(saved.getvalue(), "migrated.scansor"))
    assert restored.migrated_legacy is False and restored.legacy_reference == reference
    assert restored.actions == recipe and contents(restored.original) == raw


def test_known_nozzle_migration_is_portable_with_repository_reads_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments.nozzle_session import NozzleWorkspace
    from experiments.project_legacy import (
        export_legacy_project,
        legacy_workspace,
        migrate_known_legacy,
    )
    from experiments.scan_workspace import ScanLimits

    example = (
        Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
    )
    expected = NozzleWorkspace(example)
    raw = (example / "nozzle-bayonette-simplified.ply").read_bytes()
    recipe = {
        "schema_version": 2,
        "nodes": [
            {
                "id": "scan",
                "operation": "source",
                "label": "Legacy nozzle",
                "source_sha256": expected.source_sha256,
                "reference_sha256": expected.reference_sha256,
                "descriptor": None,
            }
        ],
        "groups": [],
        "output": "scan",
    }
    fixture = io.BytesIO()
    with zipfile.ZipFile(fixture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("actions.json", json.dumps(recipe))
        archive.writestr("nozzle-bayonette-simplified.ply", raw)
    opened = read_project_archive(
        BytesInput(fixture.getvalue(), "nozzle.scansor"),
        legacy_resolver=migrate_known_legacy,
    )
    assert (
        opened.migrated_legacy is True
        and opened.original is not None
        and opened.legacy_reference is not None
    )
    saved = io.BytesIO()
    _ = write_project_archive(
        opened.actions, opened.original, saved, legacy_reference=opened.legacy_reference
    )

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("portable project read repository metadata")

    monkeypatch.setattr(Path, "open", forbidden)
    portable = read_project_archive(BytesInput(saved.getvalue(), "portable.scansor"))
    assert portable.original is not None and portable.legacy_reference is not None
    restored = legacy_workspace(
        portable.original,
        portable.legacy_reference,
        ScanLimits(2_000_000, 30_000, 60_000, 96_000_000),
    )
    assert restored.reference_sha256 == expected.reference_sha256
    assert restored.source_sha256 == expected.source_sha256
    assert restored.default == expected.default
    assert np.array_equal(restored.local, expected.local)
    assert np.array_equal(restored.data.normals, expected.data.normals)
    assert np.array_equal(restored.data.weights, expected.data.weights)
    assert np.array_equal(restored.origin, expected.origin)
    assert np.array_equal(restored.frame, expected.frame)
    source, reference = export_legacy_project(restored, portable.original)
    assert contents(source) == raw and reference == portable.legacy_reference


def test_legacy_recognizer_rejects_unknown_bindings_and_changed_frame() -> None:
    from experiments.nozzle_session import NozzleWorkspace
    from experiments.project_legacy import (
        export_legacy_project,
        legacy_workspace,
        migrate_known_legacy,
    )
    from experiments.scan_workspace import ScanLimits

    with pytest.raises(ScanImportError, match="not recognized"):
        _ = migrate_known_legacy(
            {
                "nodes": [
                    {
                        "operation": "source",
                        "source_sha256": "0" * 64,
                        "reference_sha256": "0" * 64,
                    }
                ]
            },
            BytesInput(b"unknown", "unknown.ply"),
        )
    workspace = NozzleWorkspace(
        Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
    )
    source, reference = export_legacy_project(workspace)
    reference["selection"]["fit_frame"]["origin"][0] += 1
    with pytest.raises(ScanImportError, match="reference"):
        _ = legacy_workspace(
            source, reference, ScanLimits(2_000_000, 30_000, 60_000, 96_000_000)
        )
    _, reference = export_legacy_project(workspace)
    reference["selection"]["fit_frame"]["columns"][1][0] = False
    with pytest.raises(ScanImportError, match="reference"):
        _ = legacy_workspace(
            source, reference, ScanLimits(2_000_000, 30_000, 60_000, 96_000_000)
        )
