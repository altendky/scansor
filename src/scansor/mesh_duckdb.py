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

_ASSOCIATION = """
WITH corners AS (
    SELECT face, corner,
           CASE corner WHEN 0 THEN i0 WHEN 1 THEN i1 ELSE i2 END AS vertex
    FROM faces CROSS JOIN
         (VALUES (0::UTINYINT), (1::UTINYINT), (2::UTINYINT)) AS c(corner)
)
SELECT c.face, c.corner, c.vertex,
       CAST(v.vertex IS NOT NULL AS UTINYINT) AS found,
       coalesce(v.x, 0::UINTEGER) AS x,
       coalesce(v.y, 0::UINTEGER) AS y,
       coalesce(v.z, 0::UINTEGER) AS z
FROM corners c LEFT JOIN vertices v
  ON (CASE WHEN c.vertex >= 0 THEN c.vertex::UBIGINT ELSE NULL END) = v.vertex
"""


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

    def associated_faces(self) -> Generator[AssociatedFaces]:
        if self._closed or self._failed or not self._ready:
            raise MeshImportError(
                "integrity",
                "coordinate-association",
                "requires complete, verified staging",
            )
        self.association_queries += 1
        phase = f"coordinate-association-{self.association_queries}"
        capacity = self.plan.batch_rows * 3
        indices = np.empty((self.plan.batch_rows, 3), dtype="<i4")
        points = np.empty((self.plan.batch_rows, 3, 3), dtype="<f4")
        filled = ordinal = output_start = 0
        try:
            # JOIN and ORDER BY in one query exhausted the engine reservation
            # on the 60M/120M fixture. Materialize the join in our disposable
            # disk database before starting the independent external sort.
            # Recheck this separation against DuckDB release notes on upgrades.
            # Rebuild on each call so checked staging mutations cannot be hidden
            # by cached association results. Workspace cleanup owns an unfinished
            # table if cancellation or an abandoned generator interrupts us.
            self.monitor.progress(phase + "-join", 0, self.faces)
            _ = self.connection.execute(
                "CREATE OR REPLACE TABLE associated_corners AS " + _ASSOCIATION
            )
            self.monitor.progress(phase + "-join", self.faces, self.faces)
            self.monitor.progress(phase, 0, self.faces)
            with cast(
                Any,
                self.connection.sql(
                    "SELECT * FROM associated_corners ORDER BY face, corner"
                ),
            ).to_arrow_reader(batch_size=self.plan.batch_rows) as reader:
                for batch in reader:
                    self.monitor.check()
                    if batch.num_rows > self.plan.batch_rows:
                        raise MeshImportError(
                            "resource",
                            "coordinate-association",
                            "engine output exceeds batch reservation",
                        )
                    face = _numpy(batch, "face", "<u8")
                    corner = _numpy(batch, "corner", "u1")
                    vertex = _numpy(batch, "vertex", "<i4")
                    found = _numpy(batch, "found", "u1")
                    words = [_numpy(batch, axis, "<u4") for axis in ("x", "y", "z")]
                    expected = np.arange(
                        ordinal, ordinal + batch.num_rows, dtype=np.uint64
                    )
                    in_range = (vertex.astype(np.int64) >= 0) & (
                        vertex.astype(np.int64) < self.vertices
                    )
                    if (
                        not np.array_equal(face, expected // 3)
                        or not np.array_equal(corner, expected % 3)
                        or not np.array_equal(found, in_range.astype("u1"))
                    ):
                        raise MeshImportError(
                            "integrity",
                            "coordinate-association",
                            "missing/duplicated corner or incorrect coordinate lookup",
                            row=ordinal // 3,
                        )
                    ordinal += batch.num_rows
                    if ordinal > 3 * self.faces:
                        raise MeshImportError(
                            "integrity",
                            "coordinate-association",
                            "extra source corners",
                        )
                    offset = 0
                    while offset < batch.num_rows:
                        count = min(capacity - filled, batch.num_rows - offset)
                        indices.reshape(-1)[filled : filled + count] = vertex[
                            offset : offset + count
                        ]
                        target = points.view("<u4").reshape(-1, 3)
                        for axis in range(3):
                            target[filled : filled + count, axis] = words[axis][
                                offset : offset + count
                            ]
                        filled += count
                        offset += count
                        if filled == capacity:
                            emitted_indices, emitted_points = (
                                indices.copy(),
                                points.copy(),
                            )
                            emitted_indices.flags.writeable = (
                                emitted_points.flags.writeable
                            ) = False
                            yield AssociatedFaces(
                                output_start, emitted_indices, emitted_points
                            )
                            output_start += self.plan.batch_rows
                            filled = 0
                            self.monitor.progress(phase, output_start, self.faces)
                            del emitted_indices, emitted_points
                    del face, corner, vertex, found, words, expected, in_range, batch
            if ordinal != self.faces * 3 or filled % 3:
                raise MeshImportError(
                    "integrity",
                    "coordinate-association",
                    "incomplete source corner stream",
                )
            if filled:
                rows = filled // 3
                emitted_indices, emitted_points = (
                    indices[:rows].copy(),
                    points[:rows].copy(),
                )
                emitted_indices.flags.writeable = emitted_points.flags.writeable = False
                yield AssociatedFaces(output_start, emitted_indices, emitted_points)
                output_start += rows
            _ = self.connection.execute("DROP TABLE associated_corners")
            self.monitor.progress(phase, output_start, self.faces)
        except GeneratorExit:
            raise
        except BaseException as error:
            self._error(error, "coordinate-association")

    def close(self) -> None:
        self.monitor.set_interrupt(None)
        self._closed = True
        self.connection.close()
