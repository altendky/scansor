"""Canonical NumPy range storage with explicit ownership and sequential coverage.

Disk columns use buffered I/O, never whole-file mappings. Reads are owned and
readonly on both paths. The worker plan must reserve resident RAM columns and
each simultaneously retained result; max_range_bytes bounds a single read/write.
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np

from scansor.mesh_controls import Control
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_semantics import ColumnSpec


class Column:
    """Sequential writable column, sealed readonly by finish(); explicit close.

    A disk reopen validates exact length and exposes the full sealed payload.
    A failed write cannot be finished. Partial columns expose only their written
    prefix and are never eligible for an inventory. RAM capacity is an explicit
    reservation passed by the worker's whole-operation planner.
    """

    def __init__(
        self,
        spec: ColumnSpec,
        *,
        max_range_bytes: int,
        path: Path | None = None,
        ram_capacity_bytes: int = 0,
        reopen: bool = False,
    ) -> None:
        if type(max_range_bytes) is not int or max_range_bytes < spec.stride:
            raise MeshImportError(
                "resource-budget-too-small",
                "columns",
                "range budget cannot hold one row",
            )
        self.spec: ColumnSpec = spec
        self.max_range_bytes: int = max_range_bytes
        self.path: Path | None = path
        self._rows: np.ndarray | None = None
        self._stream: io.BufferedIOBase | None = None
        self._written: int = 0
        self._sealed: bool = False
        self._failed: bool = False
        self._closed: bool = False
        if path is None:
            if (
                reopen
                or type(ram_capacity_bytes) is not int
                or ram_capacity_bytes < spec.byte_count
            ):
                raise MeshImportError(
                    "resource-budget-too-small",
                    "columns",
                    "RAM column lacks a complete reservation",
                )
            try:
                self._rows = np.empty(spec.shape, dtype=spec.dtype)
            except (MemoryError, ValueError) as error:
                raise MeshImportError(
                    "resource", "columns", "RAM column allocation failed"
                ) from error
        else:
            try:
                if reopen:
                    entry = path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(entry.st_mode):
                        raise MeshImportError(
                            "integrity",
                            "columns",
                            "column must be a regular non-symlink file",
                        )
                    fd = os.open(
                        path,
                        os.O_RDONLY
                        | getattr(os, "O_BINARY", 0)
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_NONBLOCK", 0),
                    )
                    opened = os.fdopen(fd, "rb")
                    self._stream = cast(io.BufferedIOBase, cast(object, opened))
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (
                        entry.st_dev,
                        entry.st_ino,
                    ):
                        raise MeshImportError(
                            "integrity", "columns", "column changed while opening"
                        )
                else:
                    self._stream = cast(
                        io.BufferedIOBase, cast(object, path.open("x+b"))
                    )
                if reopen:
                    if os.fstat(self._stream.fileno()).st_size != spec.byte_count:
                        raise MeshImportError(
                            "integrity",
                            "columns",
                            "column length does not match its shape",
                        )
                    self._written, self._sealed = spec.rows, True
                else:
                    _ = self._stream.truncate(spec.byte_count)
            except BaseException as error:
                if self._stream is not None:
                    self._stream.close()
                    if not reopen:
                        path.unlink(missing_ok=True)
                if isinstance(error, OSError):
                    raise io_failure(error, "columns") from error
                raise

    def _range(self, start: int, stop: int) -> int:
        if self._closed:
            raise MeshImportError("execution", "columns", "column is closed")
        if (
            type(start) is not int
            or type(stop) is not int
            or not 0 <= start <= stop <= self.spec.rows
        ):
            raise MeshImportError("structure", "columns", "invalid row range")
        size = (stop - start) * self.spec.stride
        if size > self.max_range_bytes:
            raise MeshImportError(
                "resource-budget-too-small",
                "columns",
                "requested range exceeds its reservation",
                row=start,
            )
        return size

    def read_range(self, start: int, stop: int) -> np.ndarray:
        size = self._range(start, stop)
        if self._failed or stop > self._written:
            raise MeshImportError(
                "integrity",
                "columns",
                "requested rows are not valid written data",
                row=start,
            )
        if self._rows is not None:
            result = self._rows[start:stop].copy()
        else:
            assert self._stream is not None
            shape = (
                (stop - start,)
                if self.spec.width == 1
                else (stop - start, self.spec.width)
            )
            result = np.empty(shape, dtype=self.spec.dtype)
            try:
                if os.fstat(self._stream.fileno()).st_size != self.spec.byte_count:
                    raise MeshImportError(
                        "integrity", "columns", "column length changed"
                    )
                _ = self._stream.seek(start * self.spec.stride)
                done = 0
                target = memoryview(result).cast("B") if size else memoryview(b"")
                while done < size:
                    count = self._stream.readinto(target[done:])
                    if type(count) is not int or not 0 < count <= size - done:
                        raise MeshImportError(
                            "integrity",
                            "columns",
                            "truncated column read",
                            row=start + done // self.spec.stride,
                        )
                    done += count
            except OSError as error:
                raise io_failure(error, "columns", row=start) from error
        result.flags.writeable = False
        return result

    def read_rows(
        self, indices: np.ndarray, *, check: Callable[[], None] | None = None
    ) -> np.ndarray:
        """Gather a bounded integer ID batch, preserving its order and duplicates.

        Disk reads share the range reader's integrity checks and hold at most one
        aligned window at a time. The caller reserves the result, a window of at
        most max_range_bytes, and O(len(indices)) sorting/scatter scratch. This
        is a bounded gather, not a cache or a global external sorting backend.
        """
        if self._closed:
            raise MeshImportError("execution", "columns", "column is closed")
        if indices.ndim != 1 or indices.dtype.kind not in "iu":
            raise MeshImportError(
                "structure",
                "columns",
                "row IDs require a one-dimensional integer array",
            )
        if len(indices) * self.spec.stride > self.max_range_bytes:
            raise MeshImportError(
                "resource-budget-too-small", "columns", "gather exceeds its reservation"
            )
        if len(indices) and (
            int(indices.min()) < 0 or int(indices.max()) >= self.spec.rows
        ):
            raise MeshImportError("structure", "columns", "row ID is out of range")
        if self._failed or (len(indices) and int(indices.max()) >= self._written):
            raise MeshImportError(
                "integrity", "columns", "requested rows are not valid written data"
            )
        if check is not None:
            check()
        if self._rows is not None:
            result = self._rows[indices]
        elif not len(indices):
            # Even an empty gather must detect a changed disk-column length.
            return self.read_range(0, 0)
        else:
            shape = (
                (len(indices),)
                if self.spec.width == 1
                else (len(indices), self.spec.width)
            )
            result = np.empty(shape, dtype=self.spec.dtype)
            order = np.argsort(indices)
            sorted_ids = indices[order].astype(np.intp, copy=False)
            window_rows = min(65_536, self.max_range_bytes // self.spec.stride)
            target = result.view("u1").reshape(len(indices), self.spec.stride)
            cursor = 0
            while cursor < len(indices):
                if check is not None:
                    check()
                start = int(sorted_ids[cursor]) // window_rows * window_rows
                stop = min(start + window_rows, self._written)
                limit = int(np.searchsorted(sorted_ids, stop))
                window = self.read_range(start, stop)
                raw = window.view("u1").reshape(stop - start, self.spec.stride)
                target[order[cursor:limit]] = raw[sorted_ids[cursor:limit] - start]
                cursor = limit
                del raw, window
        result.flags.writeable = False
        return result

    def write_range(self, start: int, values: np.ndarray) -> None:
        if values.ndim == 0:
            raise MeshImportError(
                "structure", "columns", "write requires an array of rows"
            )
        stop = start + len(values)
        size = self._range(start, stop)
        expected = (
            (len(values),) if self.spec.width == 1 else (len(values), self.spec.width)
        )
        if self._failed or self._sealed or start != self._written:
            raise MeshImportError(
                "integrity",
                "columns",
                "writes require an unsealed contiguous source prefix",
                row=start,
            )
        if (
            values.shape != expected
            or values.dtype != self.spec.dtype
            or not values.flags.c_contiguous
        ):
            raise MeshImportError(
                "structure",
                "columns",
                "write requires exact contiguous dtype/shape",
                row=start,
            )
        if size == 0:
            return
        try:
            if self._rows is not None:
                # Byte assignment avoids floating NaN conversions during storage.
                memoryview(self._rows[start:stop]).cast("B")[:] = memoryview(
                    values
                ).cast("B")
            else:
                assert self._stream is not None
                _ = self._stream.seek(start * self.spec.stride)
                view = memoryview(values).cast("B")
                done = 0
                while done < size:
                    count = self._stream.write(view[done:])
                    if type(count) is not int or not 0 < count <= size - done:
                        raise MeshImportError(
                            "execution",
                            "columns",
                            "short write made no progress",
                            row=start + done // self.spec.stride,
                        )
                    done += count
            self._written = stop
        except BaseException as error:
            self._failed = True
            if isinstance(error, OSError):
                raise io_failure(error, "columns", row=start) from error
            raise

    def finish(self) -> None:
        if self._closed or self._failed or self._written != self.spec.rows:
            raise MeshImportError(
                "integrity", "columns", "cannot seal incomplete/failed column"
            )
        try:
            if self._stream is not None:
                self._stream.flush()
                if not self._sealed:
                    os.fsync(self._stream.fileno())
                if os.fstat(self._stream.fileno()).st_size != self.spec.byte_count:
                    raise MeshImportError(
                        "integrity", "columns", "column byte count changed"
                    )
            self._sealed = True
        except BaseException as error:
            self._failed = True
            if isinstance(error, OSError):
                raise io_failure(error, "columns") from error
            raise

    def inventory(self) -> dict[str, Control]:
        if not self._sealed or self._failed or self._closed:
            raise MeshImportError(
                "integrity", "columns", "only a sealed column has an inventory"
            )
        # Include zero-row files: they have no read_range call to check length.
        if (
            self._stream is not None
            and os.fstat(self._stream.fileno()).st_size != self.spec.byte_count
        ):
            raise MeshImportError("integrity", "columns", "column length changed")
        digest = hashlib.sha256()
        chunk = max(1, self.max_range_bytes // self.spec.stride)
        for start in range(0, self.spec.rows, chunk):
            rows = self.read_range(start, min(start + chunk, self.spec.rows))
            digest.update(memoryview(rows).cast("B"))
            del rows
        return {
            "name": self.spec.name,
            "dtype": self.spec.encoding,
            "shape": list(self.spec.shape),
            "byte_count": self.spec.byte_count,
            "sha256": digest.hexdigest(),
        }

    def close(self) -> None:
        self._closed = True
        self._rows = None
        if self._stream is not None:
            self._stream.close()
            self._stream = None
