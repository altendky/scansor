from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest

from scansor.mesh_columns import Column
from scansor.mesh_errors import MeshImportError
from scansor.mesh_semantics import ColumnSpec


@contextmanager
def _column(
    tmp_path: Path, *, disk: bool, width: int = 3, rows: int = 24, batch: int = 8
) -> Generator[tuple[Column, np.ndarray]]:
    spec = ColumnSpec("gather.bin", rows, width, "<f4")
    column = Column(
        spec,
        path=tmp_path / spec.name if disk else None,
        max_range_bytes=batch * spec.stride,
        ram_capacity_bytes=spec.byte_count,
    )
    values = np.arange(rows * width, dtype="<f4").reshape(spec.shape)
    try:
        for start in range(0, rows, batch):
            column.write_range(start, values[start : start + batch])
        column.finish()
        yield column, values
    finally:
        column.close()


@pytest.mark.parametrize("disk", (False, True))
@pytest.mark.parametrize("width", (1, 3))
@pytest.mark.parametrize("dtype", ("i1", "<i4", ">i8", "<u8"))
def test_gather_order_duplicates_and_ownership(
    tmp_path: Path, disk: bool, width: int, dtype: str
) -> None:
    with _column(tmp_path, disk=disk, width=width) as (column, values):
        ids = np.array([17, 0, 9, 17, 8, 0, 23, 5], dtype=dtype)
        result = column.read_rows(ids)
        expected = values[ids].tobytes()
        assert result.tobytes() == expected
        assert result.dtype == values.dtype
        assert result.flags.owndata and not result.flags.writeable
        ids[:] = 0
        assert result.tobytes() == expected
    assert result.tobytes() == expected


def test_disk_gather_reads_each_bounded_window_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[tuple[int, int]] = []
    original = Column.read_range

    def tracked(column: Column, start: int, stop: int) -> np.ndarray:
        assert (stop - start) * column.spec.stride <= column.max_range_bytes
        reads.append((start, stop))
        return original(column, start, stop)

    with _column(tmp_path, disk=True) as (column, values):
        monkeypatch.setattr(Column, "read_range", tracked)
        ids = np.array([17, 0, 9, 17, 8, 0, 23, 5])
        assert column.read_rows(ids).tobytes() == values[ids].tobytes()
        assert reads == [(0, 8), (8, 16), (16, 24)]


@pytest.mark.parametrize("disk", (False, True))
def test_gather_may_repeat_more_ids_than_the_source_has_rows(
    tmp_path: Path, disk: bool
) -> None:
    with _column(tmp_path, disk=disk, rows=2, batch=8) as (column, values):
        ids = np.array([1, 0] * 4)
        assert column.read_rows(ids).tobytes() == values[ids].tobytes()


@pytest.mark.parametrize("disk", (False, True))
def test_gather_requires_integer_bounded_written_ids(tmp_path: Path, disk: bool) -> None:
    spec = ColumnSpec("partial.bin", 24, 1, "<f8")
    column = Column(
        spec,
        path=tmp_path / spec.name if disk else None,
        ram_capacity_bytes=spec.byte_count,
        max_range_bytes=64,
    )
    try:
        column.write_range(0, np.arange(5, dtype="<f8"))
        assert column.read_rows(np.array([4, 0, 4])).tolist() == [4.0, 0.0, 4.0]
        for ids in (
            np.array([1.0]),
            np.array([True]),
            np.array([1], dtype=object),
            np.array([[1]]),
            np.array(1),
            np.array([-1]),
            np.array([24]),
            np.array([2**64 - 1], dtype="<u8"),
            np.array([], dtype=float),
        ):
            with pytest.raises(MeshImportError) as caught:
                _ = column.read_rows(ids)
            assert caught.value.category == "structure"
        with pytest.raises(MeshImportError, match="not valid written"):
            _ = column.read_rows(np.array([5]))
        with pytest.raises(MeshImportError, match="reservation"):
            _ = column.read_rows(np.zeros(9, dtype="<i4"))
        assert column.read_rows(np.empty(0, dtype="<u8")).shape == (0,)
    finally:
        column.close()
    with pytest.raises(MeshImportError, match="closed"):
        _ = column.read_rows(np.empty(0, dtype="<u8"))


@pytest.mark.parametrize("extra_bytes", (-1, 1))
@pytest.mark.parametrize("empty", (False, True))
def test_gather_detects_changed_disk_length(
    tmp_path: Path, extra_bytes: int, empty: bool
) -> None:
    with _column(tmp_path, disk=True) as (column, _values):
        assert column.path is not None
        with column.path.open("r+b") as stream:
            _ = stream.truncate(column.spec.byte_count + extra_bytes)
        ids = np.empty(0, dtype="<i4") if empty else np.array([0])
        with pytest.raises(MeshImportError, match="length changed"):
            _ = column.read_rows(ids)


def test_gather_checks_cancellation_between_disk_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reads: list[tuple[int, int]] = []
    original = Column.read_range

    def tracked(column: Column, start: int, stop: int) -> np.ndarray:
        reads.append((start, stop))
        return original(column, start, stop)

    def check() -> None:
        if reads:
            raise MeshImportError("cancelled", "gather-test", "cancelled between windows")

    with _column(tmp_path, disk=True) as (column, _values):
        monkeypatch.setattr(Column, "read_range", tracked)
        with pytest.raises(MeshImportError, match="cancelled between windows"):
            _ = column.read_rows(np.array([0, 9, 17]), check=check)
    assert reads == [(0, 8)]
