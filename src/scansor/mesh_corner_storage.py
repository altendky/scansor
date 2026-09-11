"""Direct DuckDB corner staging, source binding and global ordering.

Only integer words cross Arrow/DuckDB. Query semantics are bound by
mesh_accumulation.CORNER_REVISION; execution tuning and engine versions are not.
All canonical reads and engine output batches are bounded by the existing plan.
"""

from __future__ import annotations

import hashlib
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
        self.area_digest: str | None = None
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
        areas_digest = hashlib.sha256()
        _ = data.staging.connection.execute(
            "CREATE TABLE mesh_corners(vertex UBIGINT, face UBIGINT, corner UTINYINT)"
        )
        data.monitor.progress(phase, 0, self.count)
        for start in range(0, data.faces, data.plan.batch_rows):
            data.monitor.check()
            stop = min(start + data.plan.batch_rows, data.faces)
            indices = data.columns["triangles.bin"].read_range(start, stop).reshape(-1)
            areas = data.columns["face-area.bin"].read_range(start, stop)
            areas_digest.update(memoryview(areas).cast("B"))
            thirds = corner_areas(areas).view("<u8")
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
                    [pa.array(column) for column in columns[:3]],
                    names=["vertex", "face", "corner"],
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
            del indices, areas, thirds, valid, offsets
        self.source_digest = digest.finish()
        self.area_digest = areas_digest.hexdigest()

    def _verify_areas(self, phase: str) -> None:
        """Bind later allocation gathers to the complete original area column."""
        data, digest = self.data, hashlib.sha256()
        column = data.columns["face-area.bin"]
        # Also check the held file length for zero-row inputs.
        _ = column.read_range(0, 0)
        for start in range(0, data.faces, data.plan.batch_rows):
            data.monitor.check()
            areas = column.read_range(
                start, min(start + data.plan.batch_rows, data.faces)
            )
            digest.update(memoryview(areas).cast("B"))
            del areas
        data.monitor.check()
        if digest.hexdigest() != self.area_digest:
            raise MeshImportError("integrity", phase, "canonical face areas changed")

    def _batches(self, query: str, phase: str) -> Generator[CornerColumns]:
        data, seen = self.data, 0
        data.monitor.progress(phase, 0, self.count)
        self._verify_areas(phase)
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
                vertices = _numpy(batch, "vertex", "<u8")
                faces = _numpy(batch, "face", "<u8")
                corners = _numpy(batch, "corner", "u1")
                if np.any(faces >= data.faces):
                    raise MeshImportError("integrity", phase, "invalid corner face ID")
                areas = data.columns["face-area.bin"].read_rows(
                    faces, check=data.monitor.check
                )
                allocation = corner_areas(areas).view("<u8")
                allocation.flags.writeable = False
                columns: CornerColumns = (vertices, faces, corners, allocation)
                yield columns
                seen += batch.num_rows
                data.monitor.progress(phase, seen, self.count)
                del columns, batch, vertices, faces, corners, areas, allocation
        if seen != self.count:
            raise MeshImportError("integrity", phase, "missing source corners")
        self._verify_areas(phase)

    def verify(self) -> None:
        if self.source_digest is None or self._consumed:
            raise MeshImportError(
                "integrity",
                "verify-source-corners",
                "requires unconsumed source corner staging",
            )
        digest, seen = self._digest(), 0
        # Sort only IDs, with every selected field in the key. The matched
        # 360M-corner diagnostic at 213.7 MiB completed with this form; including
        # allocation bits exhausted the same engine allowance. Bounded canonical
        # area gathers reconstruct those bits with the original arithmetic.
        # The full four-field digest still rejects changed or duplicate tuples.
        for columns in self._batches(
            "SELECT vertex, face, corner FROM mesh_corners ORDER BY face, corner, vertex",
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
        # Vertex/face/corner uniquely orders every valid tuple. Reconstructing
        # allocation leaves the required fold order and precision unchanged.
        yield from self._batches(
            "SELECT vertex, face, corner FROM mesh_corners ORDER BY vertex, face, corner",
            "order-source-corners",
        )
