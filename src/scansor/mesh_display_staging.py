"""Direct checked DuckDB staging for full display remapping and rejected corners.

Every staged scalar is an integer or an IEEE word. Source-order digests bind
working rows before lookup. Arrow inputs are consumed synchronously; outputs
are copied into bounded owned arrays before leaving each reader iteration.
Check DuckDB/Arrow changelogs and renew lifetime/spill tests on upgrades.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np

from scansor.mesh_artifacts import ContributionArtifact
from scansor.mesh_controls import Control
from scansor.mesh_digests import RowDigest
from scansor.mesh_duckdb import _numpy, pa  # pyright: ignore[reportPrivateUsage]
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MemoryPlan, ResourceMonitor
from scansor.mesh_semantics import ColumnSpec

VERTEX_FIELDS = (
    ("source_id", "<u8"),
    ("view_id", "<i4"),
    ("x", "<u4"),
    ("y", "<u4"),
    ("z", "<u4"),
    ("normal_status", "u1"),
    ("contribution_status", "u1"),
    ("area", "<u8"),
    ("weight", "<u8"),
)
FACE_FIELDS = (
    ("face_id", "<u8"),
    ("i0", "<i4"),
    ("i1", "<i4"),
    ("i2", "<i4"),
    ("status", "u1"),
)
CORNER_FIELDS = (
    ("face_id", "<u8"),
    ("corner", "u1"),
    ("face_status", "u1"),
    *VERTEX_FIELDS,
)


def summary_count(summary: dict[str, Control], category: str, name: str) -> int:
    counts = summary.get(category)
    value = counts.get(name) if isinstance(counts, dict) else None
    if type(value) is not int or value < 0:
        raise MeshImportError(
            "integrity", "display-summary", "invalid source population count"
        )
    return value


class DisplayStaging:
    def __init__(
        self,
        directory: Path,
        plan: MemoryPlan,
        monitor: ResourceMonitor,
        data: ContributionArtifact,
    ) -> None:
        self.plan: MemoryPlan = plan
        self.monitor: ResourceMonitor = monitor
        self.data: ContributionArtifact = data
        self.vertices: int = summary_count(
            data.imported.summary, "vertex_category_counts", "finite-position"
        )
        self.faces: int = summary_count(
            data.imported.summary, "face_category_counts", "usable"
        )
        self.corners: int = 0
        self.ready: bool = False
        self.closed: bool = False
        self.connection: duckdb.DuckDBPyConnection = duckdb.connect(
            str(directory / "display.duckdb"),
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
                "CREATE TABLE display_vertices(source_id UBIGINT, view_id INTEGER, x UINTEGER, y UINTEGER, z UINTEGER, normal_status UTINYINT, contribution_status UTINYINT, area UBIGINT, weight UBIGINT)"
            )
            _ = self.connection.execute(
                "CREATE TABLE display_faces(face_id UBIGINT, i0 INTEGER, i1 INTEGER, i2 INTEGER, status UTINYINT)"
            )
            monitor.set_interrupt(self.connection.interrupt)
        except BaseException:
            self.connection.close()
            raise

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.monitor.set_interrupt(None)
            self.connection.close()

    def _insert(
        self,
        table: str,
        fields: tuple[tuple[str, str], ...],
        columns: tuple[np.ndarray, ...],
    ) -> None:
        self.monitor.check()
        if (
            self.ready
            or self.closed
            or table not in ("display_vertices", "display_faces")
        ):
            raise MeshImportError(
                "execution", "display-staging", "staging is sealed or closed"
            )
        batch = pa.record_batch(
            [pa.array(column) for column in columns], names=[name for name, _ in fields]
        )
        with pa.RecordBatchReader.from_batches(batch.schema, [batch]) as reader:
            _ = self.connection.register("display_input", reader)
            try:
                _ = self.connection.execute(
                    f"INSERT INTO {table} SELECT * FROM display_input"
                )
            finally:
                _ = self.connection.unregister("display_input")

    def batches(
        self, query: str, fields: tuple[tuple[str, str], ...], total: int, phase: str
    ) -> Generator[tuple[np.ndarray, ...]]:
        self.monitor.progress(phase, 0, total)
        seen = 0
        with cast(Any, self.connection.sql(query)).to_arrow_reader(
            batch_size=self.plan.batch_rows
        ) as reader:
            for batch in reader:
                self.monitor.check()
                count = batch.num_rows
                if not 0 < count <= self.plan.batch_rows or seen + count > total:
                    raise MeshImportError(
                        "integrity", phase, "unexpected output cardinality"
                    )
                columns = tuple(
                    _numpy(batch, name, dtype).copy() for name, dtype in fields
                )
                for column in columns:
                    column.flags.writeable = False
                yield columns
                seen += count
                self.monitor.progress(phase, seen, total)
                del columns, batch
        if seen != total:
            raise MeshImportError("integrity", phase, "incomplete output cardinality")

    def _digest(
        self, table: str, fields: tuple[tuple[str, str], ...], total: int
    ) -> RowDigest:
        return RowDigest(
            "mesh-display-working-" + table + "-v1",
            tuple(
                ColumnSpec(
                    f"field-{chr(97 + index)}.bin",
                    total,
                    1,
                    "<i4" if dtype == "<u4" else dtype,
                )
                for index, (_name, dtype) in enumerate(fields)
            ),
            max_rows=self.plan.batch_rows,
        )

    def _verify(
        self, table: str, fields: tuple[tuple[str, str], ...], total: int, expected: str
    ) -> None:
        digest, seen = self._digest(table, fields, total), 0
        query = f"SELECT * FROM {table} ORDER BY {fields[0][0]}"
        for columns in self.batches(query, fields, total, "verify-" + table):
            self._update(digest, seen, columns)
            seen += len(columns[0])
            del columns
        if digest.finish() != expected:
            raise MeshImportError(
                "integrity",
                "display-staging",
                "staged tuples differ from authoritative source order",
            )

    def load(self) -> None:
        if self.ready or self.closed:
            raise MeshImportError(
                "execution", "display-staging", "staging is single-use"
            )
        data, chunk = self.data, self.plan.batch_rows
        digest, seen = self._digest("display_vertices", VERTEX_FIELDS, self.vertices), 0
        phase = "stage-display-vertices"
        self.monitor.progress(phase, 0, data.imported.vertices)
        for start in range(0, data.imported.vertices, chunk):
            stop = min(start + chunk, data.imported.vertices)
            values = data.read_vertices(start, stop).columns
            mask = values["vertex-status.bin"] == 0
            positions = np.flatnonzero(mask)
            count = len(positions)
            xyz = values["xyz.bin"][mask].view("<u4")
            columns = (
                (positions.astype("<u8") + np.uint64(start)),
                np.arange(seen, seen + count, dtype="<i4"),
                *(xyz[:, axis].copy() for axis in range(3)),
                values["normal-status.bin"][mask],
                values["contribution-status.bin"][mask],
                values["vertex-area.bin"][mask].view("<u8"),
                values["weight.bin"][mask].view("<u8"),
            )
            if count:
                self._update(digest, seen, columns)
                self._insert("display_vertices", VERTEX_FIELDS, columns)
            seen += count
            self.monitor.progress(phase, stop, data.imported.vertices)
            del values, mask, positions, xyz, columns
        self._verify("display_vertices", VERTEX_FIELDS, self.vertices, digest.finish())
        digest = self._digest("display_faces", FACE_FIELDS, data.imported.faces)
        phase = "stage-display-faces"
        self.monitor.progress(phase, 0, data.imported.faces)
        for start in range(0, data.imported.faces, chunk):
            stop = min(start + chunk, data.imported.faces)
            values = data.imported.read_faces(start, stop).columns
            indices = values["triangles.bin"]
            columns = (
                np.arange(start, stop, dtype="<u8"),
                *(indices[:, axis].copy() for axis in range(3)),
                values["face-status.bin"],
            )
            self._update(digest, start, columns)
            self._insert("display_faces", FACE_FIELDS, columns)
            self.monitor.progress(phase, stop, data.imported.faces)
            del values, indices, columns
        self._verify("display_faces", FACE_FIELDS, data.imported.faces, digest.finish())
        self.monitor.progress("count-display-rejected-corners", 0, 1)
        row = self.connection.execute(self.rejected_query("count(*)")).fetchone()
        if (
            row is None
            or type(row[0]) is not int
            or not 0 <= row[0] <= 3 * (data.imported.faces - self.faces)
        ):
            raise MeshImportError(
                "integrity", "display-staging", "invalid rejected-corner count"
            )
        self.corners = row[0]
        self.monitor.progress("count-display-rejected-corners", 1, 1)
        data.check()
        self.ready = True

    def rejected_query(self, selection: str, *, ordered: bool = False) -> str:
        # The population comes from the checked source PLY header. CASE validates
        # both bounds before conversion/lookup; invalid corners cannot wrap.
        population = self.data.imported.vertices
        return f"""
WITH corners AS (
 SELECT f.face_id, c.corner, f.status AS face_status,
 CASE c.corner WHEN 0 THEN f.i0 WHEN 1 THEN f.i1 ELSE f.i2 END AS vertex
 FROM display_faces f CROSS JOIN
 (VALUES (0::UTINYINT), (1::UTINYINT), (2::UTINYINT)) c(corner)
 WHERE f.status <> 0
)
SELECT {selection} FROM corners c JOIN display_vertices v
ON (CASE WHEN c.vertex >= 0 AND c.vertex < {population}
 THEN c.vertex::UBIGINT ELSE NULL END) = v.source_id
""" + (" ORDER BY c.face_id, c.corner" if ordered else "")

    def vertex_batches(self) -> Generator[tuple[np.ndarray, ...]]:
        self._ready()
        yield from self.batches(
            "SELECT * FROM display_vertices ORDER BY view_id",
            VERTEX_FIELDS,
            self.vertices,
            "export-display-vertices",
        )

    def face_batches(self) -> Generator[tuple[np.ndarray, ...]]:
        self._ready()
        # Three simultaneous vertex joins exhausted the 135/256 MiB engine
        # reservations on the six-million-vertex S6 grid. Associate corners once
        # and regroup in bounded NumPy buffers, as in the import association.
        # Selection, source-face order and remapped indices remain identical.
        # Include the remapped ID after the unique face/corner key so DuckDB
        # can return every field from its sort keys without separate payloads.
        population = self.data.imported.vertices
        query = f"""WITH corners AS (
SELECT f.face_id, c.corner,
CASE c.corner WHEN 0 THEN f.i0 WHEN 1 THEN f.i1 ELSE f.i2 END AS vertex
FROM display_faces f CROSS JOIN
(VALUES (0::UTINYINT), (1::UTINYINT), (2::UTINYINT)) c(corner)
WHERE f.status = 0
)
SELECT c.face_id, c.corner, v.view_id FROM corners c
LEFT JOIN display_vertices v ON
(CASE WHEN c.vertex >= 0 AND c.vertex < {population}
THEN c.vertex::UBIGINT ELSE NULL END) = v.source_id
ORDER BY c.face_id, c.corner, v.view_id"""
        capacity = self.plan.batch_rows * 3
        faces = np.empty(capacity, dtype="<u8")
        views = np.empty(capacity, dtype="<i4")
        filled = ordinal = 0
        fields = (("face_id", "<u8"), ("corner", "u1"), ("view_id", "<i4"))
        for columns in self.batches(
            query, fields, 3 * self.faces, "export-display-face-corners"
        ):
            face, corner, view = columns
            if not np.array_equal(
                corner, np.arange(ordinal, ordinal + len(face), dtype="<u8") % 3
            ):
                raise MeshImportError(
                    "integrity", "display-remap", "missing or reordered usable corner"
                )
            ordinal += len(face)
            offset = 0
            while offset < len(face):
                count = min(capacity - filled, len(face) - offset)
                faces[filled : filled + count] = face[offset : offset + count]
                views[filled : filled + count] = view[offset : offset + count]
                filled += count
                offset += count
                if filled == capacity:
                    yield self._remapped_faces(faces, views)
                    filled = 0
            del columns, face, corner, view
        if filled:
            yield self._remapped_faces(faces[:filled], views[:filled])

    @staticmethod
    def _remapped_faces(faces: np.ndarray, views: np.ndarray) -> tuple[np.ndarray, ...]:
        if len(faces) % 3:
            raise MeshImportError(
                "integrity", "display-remap", "incomplete usable face corners"
            )
        triples = faces.reshape(-1, 3)
        if np.any(triples != triples[:, :1]):
            raise MeshImportError(
                "integrity", "display-remap", "corners have different source face IDs"
            )
        indices = views.reshape(-1, 3)
        result = (triples[:, 0].copy(), *(indices[:, axis].copy() for axis in range(3)))
        for column in result:
            column.flags.writeable = False
        return result

    def corner_batches(self) -> Generator[tuple[np.ndarray, ...]]:
        self._ready()
        query = self.rejected_query(
            "c.face_id, c.corner, c.face_status, v.*", ordered=True
        )
        yield from self.batches(
            query, CORNER_FIELDS, self.corners, "export-display-rejected-corners"
        )

    def _ready(self) -> None:
        if not self.ready or self.closed:
            raise MeshImportError(
                "execution", "display-staging", "requires verified open staging"
            )

    @staticmethod
    def _update(digest: RowDigest, start: int, columns: tuple[np.ndarray, ...]) -> None:
        # Reinterpret unsigned coordinate words as same-width signed bytes for
        # S4's existing digest schema; never change authoritative ColumnSpec or
        # convert a floating payload while checking execution staging.
        digest.update(
            start,
            tuple(
                column.view("<i4") if column.dtype == np.dtype("<u4") else column
                for column in columns
            ),
        )
