"""Bounded full mesh accounting on the concrete DuckDB foundation.

This scope computes sealed data, not publication. Import accounting completes
before normalization; a publisher can commit that stage independently and then
request contributions. Execution tuning is outside the bound semantic revisions.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

import duckdb
import numpy as np

from scansor.mesh_accumulation import CORNER_REVISION, CornerFold, VertexContributions
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, control_id
from scansor.mesh_corner_storage import CornerStaging
from scansor.mesh_digests import RowDigest
from scansor.mesh_dispositions import face_dispositions
from scansor.mesh_duckdb import duckdb_failure
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import ImportFoundation
from scansor.mesh_numeric import (
    NumericProfileError,
    normalized_weights,
    ordered_fold,
)
from scansor.mesh_policy import (
    contribution_inventory,
    contribution_request,
    contribution_status,
)
from scansor.mesh_semantics import (
    complete_import_inventory,
    contribution_specs,
    import_specs,
)
from scansor.mesh_statistics import (
    ValueRange,
    add_category_counts,
    contribution_summary,
    import_summary,
)


@dataclass(frozen=True)
class CompleteContributions:
    columns: dict[str, Column]
    request: dict[str, Control]
    inventory: dict[str, Control]
    summary: dict[str, Control]

    @property
    def identity(self) -> str:
        return control_id(self.inventory)


@dataclass
class AccountedImport:
    foundation: ImportFoundation
    contribution_columns: dict[str, Column]
    inventory: dict[str, Control]
    summary: dict[str, Control]
    contribution_counts: list[int]
    eligible_total: float
    eligible_range: ValueRange
    _attempted: bool = False

    @property
    def identity(self) -> str:
        return control_id(self.inventory)

    def complete_contributions(self) -> CompleteContributions:
        """Run normalization after the caller has had a chance to publish import.

        Does not read import columns or snapshot paths: publication may already
        have closed/moved those. The contribution columns remain private here.
        """
        if self._attempted:
            raise MeshImportError(
                "integrity", "contributions", "contribution completion is single-use"
            )
        self._attempted = True
        data, columns, eligible = (
            self.foundation,
            self.contribution_columns,
            self.contribution_counts[0],
        )
        request = contribution_request(self.identity)
        specs = contribution_specs(data.vertices)
        digest = RowDigest(
            "mesh-contribution-vertices-v1", specs, max_rows=data.plan.batch_rows
        )
        weight_range, weight_total = ValueRange(), 0.0
        phase = "normalize-vertex-weights"
        data.monitor.progress(phase, 0, data.vertices)
        for start in range(0, data.vertices, data.plan.batch_rows):
            stop = min(start + data.plan.batch_rows, data.vertices)
            data.monitor.check()
            status = columns["contribution-status.bin"].read_range(start, stop)
            areas = columns["vertex-area.bin"].read_range(start, stop)
            weights = normalized_weights(
                areas, eligible_count=eligible, total=self.eligible_total
            )
            if np.any((status != 0) & (weights.view("<u8") != 0)):
                raise MeshImportError(
                    "integrity", phase, "excluded vertex has nonzero weight", row=start
                )
            columns["weight.bin"].write_range(start, weights)
            digest.update(start, (status, areas, weights))
            weight_range.add(weights[status == 0])
            weight_total = ordered_fold(weights, initial=weight_total)
            data.monitor.progress(phase, stop, data.vertices)
            del status, areas, weights
        columns["weight.bin"].finish()
        summary = contribution_summary(
            vertices=data.vertices,
            counts=self.contribution_counts,
            area_total=self.eligible_total,
            area_range=self.eligible_range,
            weight_total=weight_total,
            weight_range=weight_range,
        )
        inventory = contribution_inventory(
            import_id=self.identity,
            eligible=eligible,
            request=request,
            summary=summary,
            artifacts=[columns[spec.name].inventory() for spec in specs],
            row_digest=digest.finish(),
        )
        return CompleteContributions(columns, request, inventory, summary)


def _faces(data: ImportFoundation) -> tuple[list[int], int, float, str]:
    counts, invalid, total, seen = [0] * 5, 0, 0.0, 0
    specs = import_specs(data.vertices, data.faces, normals=data.has_normals)[-3:]
    digest = RowDigest("mesh-import-faces-v1", specs, max_rows=data.plan.batch_rows)
    for batch in data.staging.associated_faces(
        xyz=data.columns["xyz.bin"], triangles=data.columns["triangles.bin"]
    ):
        data.monitor.check()
        stop = batch.start + len(batch.indices)
        if batch.start != seen:
            raise MeshImportError(
                "integrity",
                "account-faces",
                "associated batches are not in source order",
                row=seen,
            )
        status, areas = face_dispositions(
            batch.indices, batch.corners, vertices=data.vertices, start=seen
        )
        add_category_counts(status, counts)
        invalid += int(
            np.count_nonzero(
                (batch.indices < 0) | (batch.indices.astype(np.int64) >= data.vertices)
            )
        )
        total = ordered_fold(areas, initial=total)
        data.columns["face-status.bin"].write_range(seen, status)
        data.columns["face-area.bin"].write_range(seen, areas)
        digest.update(seen, (batch.indices, status, areas))
        seen = stop
        del batch, status, areas
    data.columns["face-status.bin"].finish()
    data.columns["face-area.bin"].finish()
    if seen != data.faces or sum(counts) != data.faces:
        raise MeshImportError(
            "integrity", "account-faces", "incomplete face accounting"
        )
    return counts, invalid, total, digest.finish()


def _write_fold(
    data: ImportFoundation, columns: dict[str, Column], rows: VertexContributions
) -> None:
    data.monitor.check()
    data.columns["reference-count.bin"].write_range(rows.start, rows.references)
    columns["vertex-area.bin"].write_range(rows.start, rows.areas)
    data.monitor.progress(
        "fold-vertex-corners", rows.start + len(rows.references), data.vertices
    )


def _account(data: ImportFoundation, columns: dict[str, Column]) -> AccountedImport:
    face_counts, invalid, face_total, face_digest = _faces(data)
    corner_count = 3 * data.faces - invalid
    corners = CornerStaging(data, count=corner_count)
    corners.load()
    corners.verify()
    fold = CornerFold(
        vertices=data.vertices,
        faces=data.faces,
        corners=corner_count,
        batch_rows=data.plan.batch_rows,
    )
    data.monitor.progress("fold-vertex-corners", 0, data.vertices)
    for batch in corners.ordered():
        for rows in fold.consume(*batch):
            _write_fold(data, columns, rows)
            del rows
        del batch
    for rows in fold.finish():
        _write_fold(data, columns, rows)
        del rows
    data.columns["reference-count.bin"].finish()
    columns["vertex-area.bin"].finish()
    specs = import_specs(data.vertices, data.faces, normals=data.has_normals)[:-3]
    digest = RowDigest("mesh-import-vertices-v1", specs, max_rows=data.plan.batch_rows)
    vertex_counts, normal_counts, contribution_counts = [0] * 2, [0] * 4, [0] * 4
    references, total, area_range = 0, 0.0, ValueRange()
    phase = "account-source-vertices"
    data.monitor.progress(phase, 0, data.vertices)
    for start in range(0, data.vertices, data.plan.batch_rows):
        data.monitor.check()
        stop = min(start + data.plan.batch_rows, data.vertices)
        vertex_columns = tuple(
            data.columns[spec.name].read_range(start, stop) for spec in specs
        )
        vertex_status, normal_status, counts = vertex_columns[-3:]
        areas = columns["vertex-area.bin"].read_range(start, stop)
        status = contribution_status(vertex_status, counts, areas)
        columns["contribution-status.bin"].write_range(start, status)
        add_category_counts(vertex_status, vertex_counts)
        add_category_counts(normal_status, normal_counts)
        add_category_counts(status, contribution_counts)
        references += sum(int(value) for value in counts)
        if references > 2**64 - 1:
            raise MeshImportError(
                "integrity", phase, "aggregate reference count overflow"
            )
        total = ordered_fold(areas, initial=total)
        area_range.add(areas[status == 0])
        digest.update(start, vertex_columns)
        data.monitor.progress(phase, stop, data.vertices)
        del vertex_columns, vertex_status, normal_status, counts, areas, status
    columns["contribution-status.bin"].finish()
    if references != corner_count or any(
        sum(counts) != data.vertices
        for counts in (vertex_counts, normal_counts, contribution_counts)
    ):
        raise MeshImportError(
            "integrity", phase, "incomplete vertex/reference accounting"
        )
    summary = import_summary(
        vertices=data.vertices,
        faces=data.faces,
        vertex_counts=vertex_counts,
        normal_counts=normal_counts,
        face_counts=face_counts,
        references=references,
        invalid_corners=invalid,
        face_total=face_total,
    )
    data.source.verify(progress=data.monitor.progress)
    inventory = complete_import_inventory(
        source=data.source.inventory(),
        ply_sha256=data.source.ply.sha256,
        vertices=data.vertices,
        faces=data.faces,
        sidecar=data.sidecar,
        artifacts=[
            data.columns[spec.name].inventory()
            for spec in import_specs(
                data.vertices, data.faces, normals=data.has_normals
            )
        ],
        summary=summary,
        row_digests={"vertices": digest.finish(), "faces": face_digest},
    )
    inventory["corner_revision"] = CORNER_REVISION
    return AccountedImport(
        data, columns, inventory, summary, contribution_counts, total, area_range
    )


@contextmanager
def account_import(data: ImportFoundation) -> Generator[AccountedImport]:
    """Own contribution column lifetimes inside the enclosing foundation scope."""
    try:
        with ExitStack() as stack:
            directory = data.import_directory.parent / "contribution"
            directory.mkdir()
            columns: dict[str, Column] = {}
            for spec in contribution_specs(data.vertices):
                column = Column(
                    spec,
                    max_range_bytes=data.plan.batch_rows * spec.stride,
                    path=directory / spec.name if data.plan.storage == "disk" else None,
                    ram_capacity_bytes=spec.byte_count
                    if data.plan.storage == "ram"
                    else 0,
                )
                _ = stack.callback(column.close)
                columns[spec.name] = column
            yield _account(data, columns)
    except BaseException as error:
        data.monitor.check()
        if isinstance(error, NumericProfileError):
            raise MeshImportError(
                "numeric-profile-failure", "mesh-accounting", str(error)
            ) from error
        if isinstance(error, duckdb.Error):
            raise duckdb_failure(error, "mesh-accounting") from error
        raise
