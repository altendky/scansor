"""Direct DuckDB corner staging, source binding and global ordering.

Only integer words cross Arrow/DuckDB. Query semantics are bound by
mesh_accumulation.CORNER_REVISION; execution tuning and engine versions are not.
All canonical reads and engine output batches are bounded by the existing plan.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

import numpy as np

from scansor.mesh_digests import RowDigest
from scansor.mesh_duckdb import _numpy, pa  # pyright: ignore[reportPrivateUsage]
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import ImportFoundation
from scansor.mesh_numeric import corner_areas
from scansor.mesh_semantics import ColumnSpec

type CornerColumns = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


class CornerStaging:
    """Verified source tuples, then one total-order query for contribution folds.

    Materialization happens after coordinate association has closed its reader.
    No query writes on a connection with an active output reader. Changes to Arrow
    reader ownership, DuckDB sorting/spilling or cancellation require release-note
    review and renewed boundary/resource tests on dependency updates.
    """

    def __init__(self, data: ImportFoundation, *, count: int) -> None:
        self.data: ImportFoundation = data
        self.count: int = count
        self.specs: tuple[ColumnSpec, ...] = tuple(
            ColumnSpec(name + ".bin", count, 1, dtype)
            for name, dtype in (
                ("vertex", "<u8"),
                ("face", "<u8"),
                ("corner", "u1"),
                ("allocation", "<u8"),
            )
        )
        self.source_digest: str | None = None
        self._verified: bool = False
        self._consumed: bool = False

    def _digest(self) -> RowDigest:
        return RowDigest(
            "mesh-working-source-corners-v1",
            self.specs,
            max_rows=self.data.plan.batch_rows,
        )

    def load(self) -> None:
        data, seen = self.data, 0
        phase = "stage-source-corners"
        digest = self._digest()
        _ = data.staging.connection.execute(
            "CREATE TABLE mesh_corners(vertex UBIGINT, face UBIGINT, corner UTINYINT, allocation UBIGINT)"
        )
        data.monitor.progress(phase, 0, self.count)
        for start in range(0, data.faces, data.plan.batch_rows):
            stop = min(start + data.plan.batch_rows, data.faces)
            indices = data.columns["triangles.bin"].read_range(start, stop).reshape(-1)
            thirds = corner_areas(
                data.columns["face-area.bin"].read_range(start, stop)
            ).view("<u8")
            valid = (indices >= 0) & (indices.astype(np.int64) < data.vertices)
            offsets = np.flatnonzero(valid).astype("<u8")
            for offset in range(0, len(offsets), data.plan.batch_rows):
                data.monitor.check()
                positions = offsets[offset : offset + data.plan.batch_rows]
                columns: CornerColumns = (
                    indices[positions].astype("<u8"),
                    np.add(positions // 3, np.uint64(start), dtype="<u8"),
                    (positions % 3).astype("u1"),
                    thirds[positions // 3].copy(),
                )
                digest.update(seen, columns)
                batch = pa.record_batch(
                    [pa.array(column) for column in columns],
                    names=["vertex", "face", "corner", "allocation"],
                )
                with pa.RecordBatchReader.from_batches(batch.schema, [batch]) as reader:
                    _ = data.staging.connection.register("scansor_corner_batch", reader)
                    try:
                        _ = data.staging.connection.execute(
                            "INSERT INTO mesh_corners SELECT * FROM scansor_corner_batch"
                        )
                    finally:
                        _ = data.staging.connection.unregister("scansor_corner_batch")
                seen += len(positions)
                data.monitor.progress(phase, seen, self.count)
                del positions, columns, batch
            del indices, thirds, valid, offsets
        self.source_digest = digest.finish()

    def _batches(self, query: str, phase: str) -> Generator[CornerColumns]:
        data, seen = self.data, 0
        data.monitor.progress(phase, 0, self.count)
        with cast(Any, data.staging.connection.sql(query)).to_arrow_reader(
            batch_size=data.plan.batch_rows
        ) as reader:
            for batch in reader:
                data.monitor.check()
                if (
                    not 0 < batch.num_rows <= data.plan.batch_rows
                    or seen + batch.num_rows > self.count
                ):
                    raise MeshImportError(
                        "integrity", phase, "unexpected corner count or batch size"
                    )
                columns: CornerColumns = (
                    _numpy(batch, "vertex", "<u8"),
                    _numpy(batch, "face", "<u8"),
                    _numpy(batch, "corner", "u1"),
                    _numpy(batch, "allocation", "<u8"),
                )
                yield columns
                seen += batch.num_rows
                data.monitor.progress(phase, seen, self.count)
                del columns, batch
        if seen != self.count:
            raise MeshImportError("integrity", phase, "missing source corners")

    def verify(self) -> None:
        if self.source_digest is None or self._consumed:
            raise MeshImportError(
                "integrity",
                "verify-source-corners",
                "requires unconsumed source corner staging",
            )
        digest, seen = self._digest(), 0
        # Source face/corner is unique. The remaining integer fields therefore
        # cannot change a valid source order, and putting every selected field
        # in the key lets DuckDB omit separate sort payload buffers. The full
        # 360M-corner diagnostic at 227 MiB completed with this form while the
        # payload form failed. Keep the digest check: duplicate/corrupt keys
        # are invalid regardless of how their added tie-breakers sort.
        for columns in self._batches(
            "SELECT vertex, face, corner, allocation FROM mesh_corners ORDER BY face, corner, vertex, allocation",
            "verify-source-corners",
        ):
            digest.update(seen, columns)
            seen += len(columns[0])
            del columns
        if digest.finish() != self.source_digest:
            raise MeshImportError(
                "integrity",
                "verify-source-corners",
                "working tuples differ from canonical source corners",
            )
        self._verified = True

    def ordered(self) -> Generator[CornerColumns]:
        if not self._verified or self._consumed:
            raise MeshImportError(
                "integrity",
                "order-source-corners",
                "requires verified unconsumed corner staging",
            )
        self._consumed = True
        # Vertex/face/corner already uniquely orders every valid tuple. As in
        # source verification, including allocation avoids a separate payload
        # without changing the required contribution fold order or precision.
        yield from self._batches(
            "SELECT vertex, face, corner, allocation FROM mesh_corners ORDER BY vertex, face, corner, allocation",
            "order-source-corners",
        )
