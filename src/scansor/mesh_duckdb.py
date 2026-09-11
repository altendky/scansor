"""Direct DuckDB working tables and bounded Arrow/NumPy association.

These tables are disposable execution artifacts. Canonical columns own content;
the versioned association rules in mesh_semantics define the semantic row contract.
No generic backend protocol, full-result fetch, or query-per-output-batch loop.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import pyarrow as _pa  # pyright: ignore[reportMissingTypeStubs]

from scansor.mesh_columns import Column
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MemoryPlan, ResourceMonitor


def duckdb_failure(error: duckdb.Error, phase: str) -> MeshImportError:
    # DuckDB does not expose filesystem errno on IOException. Match the resource
    # diagnostics from the engine/OS, retaining other I/O failures as execution.
    # Recheck these diagnostics on upgrades (including localized OS messages).
    message = str(error)
    # Commit can wrap an allocator error in TransactionException instead of
    # OutOfMemoryException (observed in the full 60M, 4,093-row-batch run).
    # Match that specific native diagnostic, not arbitrary transaction text.
    commit_memory = isinstance(
        error, duckdb.TransactionException
    ) and message.startswith(
        (
            "TransactionContext Error: Failed to commit: failed to pin block of size ",
            "TransactionContext Error: Failed to commit: failed to allocate data of size ",
        )
    )
    disk_capacity = isinstance(error, duckdb.IOException) and any(
        marker in message.lower()
        for marker in (
            os.strerror(errno.ENOSPC).lower(),
            os.strerror(errno.EDQUOT).lower(),
            "no space left on device",
            "disk quota exceeded",
            "not enough space on the disk",
            "disk is full",
            "errno 28",
            "errno 122",
            "max_temp_directory_size",
        )
    )
    category = (
        "resource"
        if isinstance(error, duckdb.OutOfMemoryException)
        or disk_capacity
        or commit_memory
        else "execution"
    )
    return MeshImportError(category, phase, message)


# PyArrow ships no type stubs. Keep its dynamic API at this execution boundary;
# _numpy checks every output dtype, null count and ownership contract at runtime.
pa: Any = _pa


def warm_baseline() -> None:
    """Initialize the actual input/output path before measuring worker baseline."""
    batch = pa.record_batch([pa.array(np.zeros(1, dtype=np.uint32))], names=["value"])
    with duckdb.connect(
        config={
            "threads": 1,
            "memory_limit": "32MiB",
            "autoinstall_known_extensions": False,
            "autoload_known_extensions": False,
        }
    ) as connection:
        _ = connection.register("warm_input", batch)
        with cast(
            Any, connection.sql("SELECT value FROM warm_input ORDER BY value")
        ).to_arrow_reader(batch_size=1) as reader:
            for part in reader:
                _ = part.column(0).to_numpy(zero_copy_only=True)
        _ = connection.unregister("warm_input")


def _numpy(batch: Any, name: str, dtype: str) -> np.ndarray:
    column = batch.column(name)
    if column.null_count:
        raise MeshImportError(
            "integrity", "duckdb-output", f"unexpected null in {name}"
        )
    result = column.to_numpy(zero_copy_only=True, writable=False)
    if result.dtype != np.dtype(dtype) or result.ndim != 1 or result.flags.writeable:
        raise MeshImportError(
            "integrity",
            "duckdb-output",
            f"unexpected dtype/lifetime contract for {name}",
        )
    return result


@dataclass(frozen=True)
class AssociatedFaces:
    start: int
    indices: np.ndarray
    corners: np.ndarray


class DuckStaging:
    """One directly accessible connection with checked source-prefix staging.

    Arrow registration consumes a bounded batch synchronously before returning;
    NumPy input and Arrow ownership remain live until the INSERT finishes. Output
    Arrow views are consumed within the batch; yielded face arrays are owned,
    readonly copies. Drop each yielded face batch before requesting another.
    """

    def __init__(
        self,
        directory: Path,
        plan: MemoryPlan,
        monitor: ResourceMonitor,
        *,
        vertices: int,
        faces: int,
    ) -> None:
        self.plan: MemoryPlan = plan
        self.monitor: ResourceMonitor = monitor
        self.vertices: int = vertices
        self.faces: int = faces
        self.vertices_written: int = 0
        self.faces_written: int = 0
        self._ready: bool = False
        self._failed: bool = False
        self._closed: bool = False
        self.association_queries: int = 0
        self.connection: duckdb.DuckDBPyConnection = duckdb.connect(
            str(directory / "stage.duckdb"),
            config={
                "memory_limit": f"{plan.engine_bytes}B",
                "threads": 1,
                "temp_directory": str(directory / "spill"),
                "max_temp_directory_size": f"{plan.engine_temp_limit_bytes}B",
                "preserve_insertion_order": False,
                "autoinstall_known_extensions": False,
                "autoload_known_extensions": False,
            },
        )
        try:
            _ = self.connection.execute(
                "CREATE TABLE vertices(vertex UBIGINT, x UINTEGER, y UINTEGER, z UINTEGER)"
            )
            _ = self.connection.execute(
                "CREATE TABLE faces(face UBIGINT, i0 INTEGER, i1 INTEGER, i2 INTEGER)"
            )
            monitor.set_interrupt(self.connection.interrupt)
        except BaseException:
            self.connection.close()
            raise

    def _error(self, error: BaseException, phase: str) -> None:
        self._failed = True
        self.monitor.check()
        if isinstance(error, duckdb.Error):
            raise duckdb_failure(error, phase) from error
        raise error

    def _insert(self, table: str, batch: Any) -> None:
        self.monitor.check()
        if self._closed or self._failed or self._ready:
            raise MeshImportError(
                "execution", "duckdb-stage", "staging is closed, failed or sealed"
            )
        # Review Arrow reader/copy/close behavior and DuckDB release notes on
        # upgrades; bounded output alone does not establish bounded engine RSS.
        try:
            with pa.RecordBatchReader.from_batches(batch.schema, [batch]) as reader:
                _ = self.connection.register("scansor_batch", reader)
                try:
                    if table == "vertices":
                        _ = self.connection.execute(
                            "INSERT INTO vertices SELECT * FROM scansor_batch"
                        )
                    elif table == "faces":
                        _ = self.connection.execute(
                            "INSERT INTO faces SELECT * FROM scansor_batch"
                        )
                    else:
                        raise MeshImportError(
                            "execution", "duckdb-stage", "unknown staging table"
                        )
                finally:
                    _ = self.connection.unregister("scansor_batch")
        except BaseException as error:
            self._error(error, "duckdb-stage")

    def stage_vertices(self, start: int, xyz: np.ndarray) -> None:
        if (
            type(start) is not int
            or start != self.vertices_written
            or xyz.ndim != 2
            or xyz.shape[1] != 3
            or xyz.dtype != np.dtype("<f4")
            or len(xyz) > self.plan.batch_rows
            or start + len(xyz) > self.vertices
        ):
            raise MeshImportError(
                "integrity",
                "duckdb-stage",
                "vertex IDs/dtype/range do not cover the next source prefix",
            )
        words = xyz.view("<u4")
        batch = pa.record_batch(
            [
                pa.array(np.arange(start, start + len(xyz), dtype=np.uint64)),
                *(
                    pa.array(np.ascontiguousarray(words[:, axis]), type=pa.uint32())
                    for axis in range(3)
                ),
            ],
            names=["vertex", "x", "y", "z"],
        )
        self._insert("vertices", batch)
        self.vertices_written += len(xyz)

    def stage_faces(self, start: int, indices: np.ndarray) -> None:
        if (
            type(start) is not int
            or start != self.faces_written
            or indices.ndim != 2
            or indices.shape[1] != 3
            or indices.dtype != np.dtype("<i4")
            or len(indices) > self.plan.batch_rows
            or start + len(indices) > self.faces
        ):
            raise MeshImportError(
                "integrity",
                "duckdb-stage",
                "face IDs/dtype/range do not cover the next source prefix",
            )
        batch = pa.record_batch(
            [
                pa.array(np.arange(start, start + len(indices), dtype=np.uint64)),
                *(
                    pa.array(np.ascontiguousarray(indices[:, axis]), type=pa.int32())
                    for axis in range(3)
                ),
            ],
            names=["face", "i0", "i1", "i2"],
        )
        self._insert("faces", batch)
        self.faces_written += len(indices)

    def finish(self) -> None:
        if (
            self._closed
            or self._failed
            or self.vertices_written != self.vertices
            or self.faces_written != self.faces
        ):
            raise MeshImportError(
                "integrity", "duckdb-stage", "staging lacks complete source coverage"
            )
        try:
            for table, name, count in (
                ("vertices", "vertex", self.vertices),
                ("faces", "face", self.faces),
            ):
                query = (
                    "SELECT vertex FROM vertices ORDER BY vertex"
                    if table == "vertices"
                    else "SELECT face FROM faces ORDER BY face"
                )
                seen = 0
                phase = "verify-staged-" + table
                self.monitor.progress(phase, 0, count)
                with cast(Any, self.connection.sql(query)).to_arrow_reader(
                    batch_size=self.plan.batch_rows
                ) as reader:
                    for batch in reader:
                        ids = _numpy(batch, name, "<u8")
                        if len(ids) > self.plan.batch_rows or not np.array_equal(
                            ids, np.arange(seen, seen + len(ids), dtype=np.uint64)
                        ):
                            raise MeshImportError(
                                "integrity",
                                phase,
                                "missing, duplicated or reordered source ID",
                                row=seen,
                            )
                        seen += len(ids)
                        self.monitor.progress(phase, seen, count)
                        del ids, batch
                if seen != count:
                    raise MeshImportError(
                        "integrity", phase, "staged row count differs from source"
                    )
            self._ready = True
        except BaseException as error:
            self._error(error, "duckdb-verify")

    def _verify_coordinates(self, xyz: Column) -> None:
        """Bind every staged coordinate word before each association pass."""
        phase, seen = "verify-coordinate-words", 0
        self.monitor.progress(phase, 0, self.vertices)
        with cast(
            Any,
            self.connection.sql("SELECT vertex, x, y, z FROM vertices ORDER BY vertex"),
        ).to_arrow_reader(batch_size=self.plan.batch_rows) as reader:
            for batch in reader:
                self.monitor.check()
                count = batch.num_rows
                if (
                    not 0 < count <= self.plan.batch_rows
                    or seen + count > self.vertices
                ):
                    raise MeshImportError(
                        "integrity", phase, "unexpected staged coordinate count"
                    )
                ids = _numpy(batch, "vertex", "<u8")
                expected = xyz.read_range(seen, seen + count).view("<u4")
                if not np.array_equal(
                    ids, np.arange(seen, seen + count, dtype="<u8")
                ) or any(
                    not np.array_equal(
                        _numpy(batch, axis, "<u4"), expected[:, position]
                    )
                    for position, axis in enumerate(("x", "y", "z"))
                ):
                    raise MeshImportError(
                        "integrity",
                        phase,
                        "staged source ID or coordinate differs from canonical column",
                        row=seen,
                    )
                seen += count
                self.monitor.progress(phase, seen, self.vertices)
                del batch, ids, expected
        if seen != self.vertices:
            raise MeshImportError("integrity", phase, "missing staged coordinates")

    def associated_faces(
        self, *, xyz: Column, triangles: Column
    ) -> Generator[AssociatedFaces]:
        if self._closed or self._failed or not self._ready:
            raise MeshImportError(
                "integrity",
                "coordinate-association",
                "requires complete, verified staging",
            )
        if (
            xyz.spec.rows != self.vertices
            or triangles.spec.rows != self.faces
            or xyz.spec.width != 3
            or triangles.spec.width != 3
            or xyz.spec.dtype != np.dtype("<f4")
            or triangles.spec.dtype != np.dtype("<i4")
        ):
            raise MeshImportError(
                "integrity", "coordinate-association", "incompatible canonical columns"
            )
        self.association_queries += 1
        phase = f"coordinate-association-{self.association_queries}-lookup"
        seen = 0
        try:
            # The full 60M fixture exceeded the 512 MiB plan's engine reservation
            # in a global coordinate hash join. Canonical columns already supply
            # bounded row-ID access. Keep DuckDB's source ordering and verify its
            # coordinate/index copies on every pass; do not cache checked results.
            self._verify_coordinates(xyz)
            self.monitor.progress(phase, 0, self.faces)
            with cast(
                Any,
                self.connection.sql("SELECT face, i0, i1, i2 FROM faces ORDER BY face"),
            ).to_arrow_reader(batch_size=self.plan.batch_rows) as reader:
                for batch in reader:
                    self.monitor.check()
                    count = batch.num_rows
                    if (
                        not 0 < count <= self.plan.batch_rows
                        or seen + count > self.faces
                    ):
                        raise MeshImportError(
                            "integrity", phase, "unexpected staged face count"
                        )
                    face = _numpy(batch, "face", "<u8")
                    if not np.array_equal(
                        face, np.arange(seen, seen + count, dtype="<u8")
                    ):
                        raise MeshImportError(
                            "integrity",
                            phase,
                            "missing or duplicated source face ID",
                            row=seen,
                        )
                    indices = np.column_stack(
                        [_numpy(batch, name, "<i4") for name in ("i0", "i1", "i2")]
                    )
                    if not np.array_equal(
                        indices, triangles.read_range(seen, seen + count)
                    ):
                        raise MeshImportError(
                            "integrity",
                            phase,
                            "associated indices differ from canonical source faces",
                            row=seen,
                        )
                    points = np.zeros((count, 3, 3), dtype="<f4")
                    flat = indices.reshape(-1)
                    valid = np.flatnonzero(
                        (flat >= 0) & (flat.astype(np.int64) < self.vertices)
                    )
                    gather_rows = min(
                        self.plan.batch_rows, xyz.max_range_bytes // xyz.spec.stride
                    )
                    target = points.view("<u4").reshape(-1, 3)
                    for start in range(0, len(valid), gather_rows):
                        positions = valid[start : start + gather_rows]
                        gathered = xyz.read_rows(
                            flat[positions], check=self.monitor.check
                        )
                        target[positions] = gathered.view("<u4")
                        del positions, gathered
                    indices.flags.writeable = points.flags.writeable = False
                    yield AssociatedFaces(seen, indices, points)
                    seen += count
                    self.monitor.progress(phase, seen, self.faces)
                    del face, indices, points, flat, valid, target, batch
            if seen != self.faces:
                raise MeshImportError("integrity", phase, "missing staged faces")
            self.monitor.progress(phase, seen, self.faces)
        except GeneratorExit:
            raise
        except BaseException as error:
            self._error(error, phase)

    def close(self) -> None:
        self.monitor.set_interrupt(None)
        self._closed = True
        self.connection.close()
