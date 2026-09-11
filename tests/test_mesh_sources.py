from __future__ import annotations

import errno
import hashlib
import io
import os
from collections.abc import Buffer
from pathlib import Path
from typing import Any, BinaryIO, cast, override

import numpy as np
import pytest

from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_dispositions import face_dispositions, normal_status, vertex_status
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import canonical_f32
from scansor.mesh_recipes import SMALL_RECIPES, write_recipe
from scansor.mesh_semantics import ColumnSpec, contribution_specs, import_specs
from scansor.mesh_sidecar import MAX_SIDECAR_BYTES, interpret_sidecar
from scansor.mesh_snapshot import snapshot_bundle


def test_sidecar_strings_absence_size_and_degradation() -> None:
    raw = b'<?xml version="1.0" encoding="UTF-8"?><Model transformToModel="1 0 -0" globalCoordinateSystemName="a&amp;b"><Header magic="m" version="1"/></Model><ModelExport exportVertexNormals="1"/><CalibrationExportSettings exportDisabled="true"/>'
    result = interpret_sidecar(raw)
    assert result == {
        "revision": "realityscan-rsinfo-strings-v1",
        "status": "interpreted",
        "reason": None,
        "fields": {
            "Model": {
                "attributes": {
                    "transformToModel": "1 0 -0",
                    "globalCoordinateSystemName": "a&b",
                },
                "header": {"magic": "m", "version": "1"},
            },
            "ModelExport": {"attributes": {"exportVertexNormals": "1"}, "header": None},
            "CalibrationExportSettings": {
                "attributes": {"exportDisabled": "true"},
                "header": None,
            },
        },
    }
    assert interpret_sidecar(None)["status"] == "absent"
    assert interpret_sidecar(b"")["status"] == "not-interpreted-malformed"
    assert (
        interpret_sidecar(None, byte_count=MAX_SIDECAR_BYTES + 1)["status"]
        == "not-interpreted-size-limit"
    )
    assert (
        interpret_sidecar(b"a" * (MAX_SIDECAR_BYTES + 1))["status"]
        == "not-interpreted-size-limit"
    )
    unknown = interpret_sidecar(
        b'<Model transformToModel="1" mystery="x"><Other/></Model><Future/>'
    )
    assert unknown["status"] == "partially-interpreted-unknown-fields"
    assert b"mystery" not in encode_control(unknown)
    assert b'"transformToModel": "1"' in encode_control(unknown)
    assert (
        interpret_sidecar(
            '<Model globalCoordinateSystemName="e\u0301 \u00e9"/>'.encode()
        )["status"]
        == "interpreted"
    )
    for length in (-1, True):
        with pytest.raises(ValueError):
            _ = interpret_sidecar(None, byte_count=length)
    with pytest.raises(ValueError):
        _ = interpret_sidecar(b"x", byte_count=2)


@pytest.mark.parametrize(
    "raw",
    [
        b"<Model/><Model/>",
        b'<Model x="1" x="2"/>',
        b"<Model><Header/><Header/></Model>",
        b"<Model>",
        b"\xff",
        b'<?xml encoding="utf-8"?><Model/>',
        b'<?xml version="1.0" encoding="latin-1"?><Model/>',
        b'<?xml version="1.0"?><?xml version="1.0"?><Model/>',
        b'<!DOCTYPE Model [<!ENTITY x "expanded">]><Model componentId="&x;"/>',
        b'<!DOCTYPE Model SYSTEM "file:///never-read-this"><Model/>',
        b'<!DOCTYPE Model SYSTEM "https://never-contact.invalid/"><Model/>',
        b"<x>" * 40 + b"</x>" * 40,
        b"<Model>" + b"<Header/>" * 5000 + b"</Model>",
        b"text<Model/>",
        b"<Model/>text<ModelExport/>",
    ],
)
def test_sidecar_malformed_and_external_requests_remain_diagnostic(raw: bytes) -> None:
    result = interpret_sidecar(raw)
    assert result["status"] == "not-interpreted-malformed"
    assert result["fields"] == {}
    assert isinstance(result["reason"], str)
    assert len(result["reason"]) <= 256


def test_sidecar_does_not_follow_inclusion_or_stylesheet(tmp_path: Path) -> None:
    outside = tmp_path / "external.xml"
    _ = outside.write_text("<Model componentId='must-not-appear'/>")
    raw = f'<?xml-stylesheet href="{outside.as_uri()}"?><Model xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="{outside.as_uri()}"/></Model>'.encode()
    result = interpret_sidecar(raw)
    assert result["status"] == "partially-interpreted-unknown-fields"
    assert b"must-not-appear" not in encode_control(result)


@pytest.mark.parametrize("chunk", (1, 7, 13, 23, 4093))
def test_source_snapshot_independent_bytes_and_sidecar_identity(
    tmp_path: Path, chunk: int
) -> None:
    source = tmp_path / "source.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["right-triangle-orphan-v1"])
    original = source.read_bytes()
    sidecar = tmp_path / "source.rsInfo"
    _ = sidecar.write_bytes(b"")
    identities: list[str] = []
    for name, attached in (("absent", None), ("present", sidecar)):
        target = tmp_path / name
        target.mkdir()
        progress: list[tuple[str, int, int]] = []

        def report(
            phase: str,
            done: int,
            total: int,
            *,
            sink: list[tuple[str, int, int]] = progress,
        ) -> None:
            sink.append((phase, done, total))

        bundle = snapshot_bundle(
            target,
            source,
            attached,
            chunk_bytes=chunk,
            progress=report,
        )
        assert bundle.ply.stream.read() == original
        assert bundle.ply.sha256 == hashlib.sha256(original).hexdigest()
        assert bundle.ply.path.stat().st_ino != source.stat().st_ino
        identities.append(bundle.identity)
        assert all(0 <= completed <= total for _, completed, total in progress)
        bundle.close()
        assert bundle.ply.stream.closed
        assert attached is None or (
            bundle.sidecar is not None and bundle.sidecar.stream.closed
        )
    assert identities[0] != identities[1]
    _ = source.write_bytes(b"changed after copying")
    assert (tmp_path / "absent" / "observations.ply").read_bytes() == original


@pytest.mark.parametrize(
    "mutation", ("overwrite", "grow", "truncate", "replace", "remove", "private")
)
def test_snapshot_rejects_mutation_and_cleans_only_owned_files(
    tmp_path: Path, mutation: str
) -> None:
    source = tmp_path / "source.ply"
    _ = source.write_bytes(bytes(range(256)))
    target = tmp_path / "private"
    target.mkdir()
    sentinel = target / "unrelated"
    _ = sentinel.write_bytes(b"keep")
    changed = False

    def change(phase: str, done: int, total: int) -> None:
        nonlocal changed
        desired = "-private-rehash" if mutation == "private" else "-copy"
        if changed or not phase.endswith(desired) or done != total:
            return
        changed = True
        path = target / "observations.ply" if mutation == "private" else source
        if mutation == "grow":
            with path.open("ab") as stream:
                _ = stream.write(b"new")
        elif mutation == "truncate":
            _ = path.write_bytes(b"short")
        elif mutation == "replace":
            alternate = tmp_path / "replacement"
            _ = alternate.write_bytes(bytes(range(256)))
            _ = alternate.replace(path)
        elif mutation == "remove":
            path.unlink()
        else:
            with path.open("r+b") as stream:
                _ = stream.write(b"changed")

    with pytest.raises(MeshImportError) as caught:
        _ = snapshot_bundle(target, source, chunk_bytes=7, progress=change)
    assert caught.value.category == "integrity"
    assert not (target / "observations.ply").exists()
    assert sentinel.read_bytes() == b"keep"


def test_missing_sidecar_and_cancellation_do_not_leave_partial_mesh(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.ply"
    _ = source.write_bytes(b"source")
    target = tmp_path / "private"
    target.mkdir()
    with pytest.raises(MeshImportError) as caught:
        _ = snapshot_bundle(target, source, tmp_path / "missing.rsInfo")
    assert caught.value.category == "input"
    assert not list(target.iterdir())

    def cancel(_phase: str, _done: int, _total: int) -> None:
        raise MeshImportError("cancelled", "snapshot", "test cancellation")

    with pytest.raises(MeshImportError, match="cancelled"):
        _ = snapshot_bundle(target, source, progress=cancel)
    assert not list(target.iterdir())


def test_snapshot_replaced_private_path_is_not_deleted(tmp_path: Path) -> None:
    source = tmp_path / "source.ply"
    _ = source.write_bytes(b"original")
    target = tmp_path / "private"
    target.mkdir()
    replaced = False

    def replace(phase: str, done: int, total: int) -> None:
        nonlocal replaced
        if replaced or not phase.endswith("-source-rehash") or done != total:
            return
        replaced = True
        original = target / "observations.ply"
        _ = original.rename(target / "moved-original")
        _ = original.write_bytes(b"replacement must survive")

    with pytest.raises(MeshImportError, match="replaced"):
        _ = snapshot_bundle(target, source, progress=replace)
    assert (target / "observations.ply").read_bytes() == b"replacement must survive"


def test_snapshot_detects_parent_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "source-parent"
    parent.mkdir()
    source = parent / "source.ply"
    _ = source.write_bytes(b"original")
    target = tmp_path / "private"
    target.mkdir()
    changed = False

    def replace(phase: str, done: int, total: int) -> None:
        nonlocal changed
        if changed or not phase.endswith("-private-rehash") or done != total:
            return
        changed = True
        _ = parent.rename(tmp_path / "old-parent")
        parent.mkdir()
        _ = source.write_bytes(b"original")

    with pytest.raises(MeshImportError, match="path changed"):
        _ = snapshot_bundle(target, source, progress=replace)
    assert not list(target.iterdir())


def test_unreadable_requested_sidecar_is_input_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, sidecar = tmp_path / "source.ply", tmp_path / "source.rsInfo"
    _ = source.write_bytes(b"source")
    _ = sidecar.write_bytes(b"sidecar")
    target = tmp_path / "private"
    target.mkdir()
    native_open = os.open

    def denied(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if path == sidecar:
            raise PermissionError(errno.EACCES, "injected source permission failure")
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(MeshImportError) as caught:
        _ = snapshot_bundle(target, source, sidecar)
    assert caught.value.category == "input"
    assert not list(target.iterdir())


@pytest.mark.parametrize("length", (0, 7, 9, 16))
def test_column_reopen_rejects_wrong_exact_length(tmp_path: Path, length: int) -> None:
    path = tmp_path / "x.bin"
    _ = path.write_bytes(bytes(length))
    with pytest.raises(MeshImportError, match="length"):
        _ = Column(
            ColumnSpec("x.bin", 1, 1, "<f8"), path=path, max_range_bytes=8, reopen=True
        )
    assert path.read_bytes() == bytes(length)


def test_empty_column_inventory_checks_file_length(tmp_path: Path) -> None:
    path = tmp_path / "x.bin"
    column = Column(ColumnSpec("x.bin", 0, 1, "<f8"), path=path, max_range_bytes=8)
    try:
        column.finish()
        _ = path.write_bytes(b"unexpected")
        with pytest.raises(MeshImportError, match="length"):
            _ = column.inventory()
    finally:
        column.close()


class ShortFile(io.BufferedIOBase):
    def __init__(
        self, stream: BinaryIO, limit: int, *, fail: int | None = None
    ) -> None:
        super().__init__()
        self.stream: BinaryIO = stream
        self.limit: int = limit
        self.fail: int | None = fail

    @override
    def fileno(self) -> int:
        return self.stream.fileno()

    @override
    def write(self, data: Buffer, /) -> int:
        if self.fail is not None:
            raise OSError(self.fail, "injected write failure")
        return self.stream.write(memoryview(data)[: self.limit])

    @override
    def readinto(self, buffer: Buffer, /) -> int:
        data = self.stream.read(min(len(memoryview(buffer)), self.limit))
        memoryview(buffer)[: len(data)] = data
        return len(data)

    @override
    def seek(self, offset: int, whence: int = 0, /) -> int:
        return self.stream.seek(offset, whence)

    @override
    def truncate(self, size: int | None = None, /) -> int:
        return self.stream.truncate(size)

    @override
    def flush(self) -> None:
        if not self.stream.closed:
            self.stream.flush()

    @override
    def close(self) -> None:
        self.stream.close()
        super().close()


@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
@pytest.mark.parametrize("io_limit", (1, 7, 13, 23, 4093))
def test_ram_disk_column_exact_bytes_and_lifetimes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chunk: int, io_limit: int
) -> None:
    native_open = Path.open

    def opened(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        stream = native_open(path, mode, *args, **kwargs)
        return (
            ShortFile(cast(BinaryIO, stream), io_limit)
            if path.suffix == ".bin"
            else stream
        )

    monkeypatch.setattr(Path, "open", opened)
    words = np.tile(
        np.array([0, 0x80000000, 1, 0x7F800001, 0xFFC12345, 0xFF800000], dtype="<u4"),
        70,
    ).reshape(-1, 3)
    source = words.view("<f4")
    spec = ColumnSpec("xyz.bin", len(source), 3, "<f4")
    inventories: list[dict[str, Control]] = []
    for path in (None, tmp_path / "xyz.bin"):
        column = Column(
            spec,
            path=path,
            ram_capacity_bytes=spec.byte_count,
            max_range_bytes=chunk * 12,
        )
        with pytest.raises(MeshImportError, match="not valid written"):
            _ = column.read_range(0, 1)
        for start in range(0, spec.rows, chunk):
            column.write_range(start, source[start : start + chunk])
        column.finish()
        for start in reversed(range(0, spec.rows, chunk)):
            result = column.read_range(start, min(start + chunk, spec.rows))
            assert result.tobytes() == source[start : start + chunk].tobytes()
            assert not result.flags.writeable
            assert not np.shares_memory(result, source)
        ids = np.resize(np.array([spec.rows - 1, 0, spec.rows // 2, 0]), chunk)
        gathered = column.read_rows(ids)
        assert gathered.tobytes() == source[ids].tobytes()
        assert gathered.flags.owndata and not gathered.flags.writeable
        inventories.append(column.inventory())
        kept = column.read_range(0, 1)
        column.close()
        assert kept.tobytes() == source[:1].tobytes()
        assert gathered.tobytes() == source[ids].tobytes()
        with pytest.raises(MeshImportError, match="closed"):
            _ = column.read_range(0, 1)
        with pytest.raises(MeshImportError, match="closed"):
            _ = column.read_rows(ids[:0])
    assert inventories[0] == inventories[1]
    assert inventories[0]["sha256"] == hashlib.sha256(source.tobytes()).hexdigest()
    disk = Column(
        spec, path=tmp_path / "xyz.bin", max_range_bytes=chunk * 12, reopen=True
    )
    assert disk.inventory() == inventories[0]
    disk.close()


def test_column_invalid_inputs_empty_and_unsealed_data(tmp_path: Path) -> None:
    spec = ColumnSpec("empty.bin", 0, 3, "<i4")
    for path in (None, tmp_path / "empty.bin"):
        column = Column(spec, path=path, max_range_bytes=12)
        assert column.read_range(0, 0).shape == (0, 3)
        assert column.read_rows(np.empty(0, dtype="<u8")).shape == (0, 3)
        column.write_range(0, np.empty((0, 3), dtype="<i4"))
        column.finish()
        assert column.inventory()["sha256"] == hashlib.sha256(b"").hexdigest()
        column.close()
    spec = ColumnSpec("x.bin", 3, 1, "<f8")
    with pytest.raises(MeshImportError, match="reservation"):
        _ = Column(spec, max_range_bytes=8, ram_capacity_bytes=23)
    column = Column(spec, max_range_bytes=8, ram_capacity_bytes=24)
    for start, stop in ((True, 1), (-1, 1), (0, 4), (0, 2), (2, 1)):
        with pytest.raises(MeshImportError):
            _ = column.read_range(start, stop)
    for data in (np.array(1.0), np.array([1], dtype="<f4"), np.array([[1.0]])):
        with pytest.raises(MeshImportError):
            column.write_range(0, data)
    column.write_range(0, np.array([1.0]))
    with pytest.raises(MeshImportError):
        column.finish()
    with pytest.raises(MeshImportError):
        _ = column.inventory()
    column.close()
    for dtype in ("O", ">f8", "<i8", "V8"):
        with pytest.raises(MeshImportError):
            _ = ColumnSpec("bad.bin", 1, 1, dtype)
    with pytest.raises(MeshImportError):
        _ = ColumnSpec("huge.bin", 2**62, 3, "<f8")
    assert (
        sum(
            s.byte_count
            for s in import_specs(4, 1, normals=True) + contribution_specs(4)
        )
        == 51 * 4 + 21
    )


@pytest.mark.parametrize("failure", (errno.ENOSPC, errno.EACCES, 0))
def test_snapshot_and_column_write_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: int
) -> None:
    native_open = Path.open

    def opened(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        stream = native_open(path, mode, *args, **kwargs)
        return (
            ShortFile(cast(BinaryIO, stream), 0, fail=failure or None)
            if "x" in mode
            else stream
        )

    source = tmp_path / "source.ply"
    _ = source.write_bytes(b"source bytes")
    target = tmp_path / "private"
    target.mkdir()
    monkeypatch.setattr(Path, "open", opened)
    with pytest.raises(MeshImportError):
        _ = snapshot_bundle(target, source)
    assert not list(target.iterdir())
    column = Column(
        ColumnSpec("x.bin", 2, 1, "<f8"), path=target / "x.bin", max_range_bytes=16
    )
    with pytest.raises(MeshImportError):
        column.write_range(0, np.ones(2))
    with pytest.raises(MeshImportError):
        column.finish()
    with pytest.raises(MeshImportError, match="not valid written"):
        _ = column.read_rows(np.array([0]))
    column.close()


def test_dispositions_preserve_precedence_and_bad_normals() -> None:
    recipe = SMALL_RECIPES["exceptional-values-v1"]
    xyz = canonical_f32(recipe.vertices(0, recipe.vertex_count))
    normals = np.array(recipe.normal_bits, dtype="<u4").view("<f4")
    assert vertex_status(xyz).tolist() == [0, 0, 0, 0, 1, 1]
    assert normal_status(normals, 6).tolist() == [1, 2, 3, 3, 2, 2]
    assert normal_status(None, 6).tolist() == [0] * 6
    indices = recipe.faces(0, recipe.face_count)
    corners = np.zeros((len(indices), 3, 3), dtype="<f4")
    for face, triangle in enumerate(indices):
        for corner, index in enumerate(triangle):
            if 0 <= index < len(xyz):
                corners[face, corner] = xyz[index]
    status, areas = face_dispositions(indices, corners, vertices=len(xyz))
    assert status.tolist() == [0, 1, 2, 2, 1]
    assert areas.tolist() == [6, 0, 0, 0, 0]
    status, areas = face_dispositions(
        np.array([[0, 0, 1], [0, 1, 2]], dtype="<i4"),
        np.zeros((2, 3, 3), dtype="<f4"),
        vertices=2**31,
    )
    assert status.tolist() == [3, 4]
    assert areas.tobytes() == bytes(16)
