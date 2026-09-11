"""Verified source and storage foundation for full-resolution mesh processing.

S3 prepares canonical source rows and direct DuckDB staging. S4 fills the remaining
import columns, computes contributions, and publishes/replays independent stages.
A foundation-ready inventory is never a complete import/contribution marker.
"""

from __future__ import annotations

import errno
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import duckdb
import numpy as np
import psutil

from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_dispositions import normal_status, vertex_status
from scansor.mesh_duckdb import DuckStaging, duckdb_failure, warm_baseline
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import check_arithmetic
from scansor.mesh_ply import MeshPlyError, MeshPlyReader
from scansor.mesh_resources import (
    AllocationLedger,
    MemoryPlan,
    ResourceMonitor,
    cgroup_snapshot,
    check_disk_space,
    disk_snapshot,
    memory_snapshot,
    plan_memory,
)
from scansor.mesh_semantics import (
    PENDING_IMPORT_COLUMNS,
    contribution_specs,
    decode_coordinates,
    foundation_inventory,
    import_specs,
)
from scansor.mesh_sidecar import MAX_SIDECAR_BYTES, interpret_sidecar
from scansor.mesh_snapshot import SourceBundle, snapshot_bundle
from scansor.mesh_workspace import create_workspace, remove_owned_workspace


@dataclass
class ImportFoundation:
    directory: Path
    import_directory: Path
    source: SourceBundle
    columns: dict[str, Column]
    staging: DuckStaging
    plan: MemoryPlan
    ledger: AllocationLedger
    monitor: ResourceMonitor
    sidecar: dict[str, Control]
    vertices: int
    faces: int
    has_normals: bool

    def inventory(self) -> dict[str, Control]:
        """Only columns actually completed in S3 appear in this inventory."""
        self.monitor.check()
        self.source.verify(progress=self.monitor.progress)
        artifacts: list[Control] = [
            column.inventory()
            for name, column in self.columns.items()
            if name not in PENDING_IMPORT_COLUMNS
        ]
        return foundation_inventory(
            source=self.source.inventory(),
            ply_sha256=self.source.ply.sha256,
            vertices=self.vertices,
            faces=self.faces,
            sidecar=self.sidecar,
            artifacts=artifacts,
        )

    def execution_report(self) -> dict[str, Control]:
        return {
            "revision": "mesh-import-execution-v1",
            "plan": self.plan.record(),
            "monitor": self.monitor.record(),
            "cgroup": cgroup_snapshot(),
            "current_disk": disk_snapshot(self.directory),
            "managed_reservation_high_water_bytes": self.ledger.high_water,
            "association_queries": self.staging.association_queries,
            "dependencies": {
                "duckdb": duckdb.__version__,
                "pyarrow": version("pyarrow"),
                "psutil": psutil.__version__,
            },
        }


def _prepare(
    directory: Path,
    source: SourceBundle,
    monitor: ResourceMonitor,
    stack: ExitStack,
    *,
    budget_bytes: int,
    storage: str,
    chunk_rows: int | None,
    io_block_bytes: int,
) -> ImportFoundation:
    reader = MeshPlyReader(
        source.ply.stream, max_range_bytes=24 * 65_536, io_block_bytes=io_block_bytes
    )
    vertex_layout, face_layout = reader.layout.elements
    vertices, faces = vertex_layout.element.count, face_layout.element.count
    normals = "nx" in (vertex_layout.dtype.names or ())
    specs = import_specs(vertices, faces, normals=normals)
    all_specs = specs + contribution_specs(vertices)
    total_source = source.ply.byte_count + (
        0 if source.sidecar is None else source.sidecar.byte_count
    )
    plan = plan_memory(
        budget_bytes=budget_bytes,
        baseline_bytes=int(str(memory_snapshot()["rss_bytes"])),
        canonical_bytes=sum(spec.byte_count for spec in all_specs),
        vertices=vertices,
        faces=faces,
        source_bytes=total_source,
        storage=storage,
        chunk_rows=chunk_rows,
    )
    check_disk_space(directory, max(0, plan.disk_estimate_bytes - total_source))
    ledger = AllocationLedger(plan.resident_columns_bytes + plan.batch_bytes)
    _ = stack.enter_context(
        ledger.reserve("resident-columns", plan.resident_columns_bytes)
    )
    _ = stack.enter_context(ledger.reserve("bounded-processing-work", plan.batch_bytes))
    info = source.sidecar
    if info is None:
        sidecar = interpret_sidecar(None)
    elif info.byte_count > MAX_SIDECAR_BYTES:
        sidecar = interpret_sidecar(None, byte_count=info.byte_count)
    else:
        _ = info.stream.seek(0)
        sidecar = interpret_sidecar(
            info.stream.read(MAX_SIDECAR_BYTES + 1), byte_count=info.byte_count
        )
    import_directory = directory / "import"
    columns: dict[str, Column] = {}
    for spec in specs:
        column = Column(
            spec,
            max_range_bytes=plan.batch_rows * spec.stride,
            path=import_directory / spec.name if plan.storage == "disk" else None,
            ram_capacity_bytes=spec.byte_count if plan.storage == "ram" else 0,
        )
        _ = stack.callback(column.close)
        columns[spec.name] = column
    working = directory / "working"
    working.mkdir()
    staging = DuckStaging(working, plan, monitor, vertices=vertices, faces=faces)
    _ = stack.callback(staging.close)
    reader.reader.max_range_bytes = plan.batch_rows * 24
    monitor.progress("decode-vertices", 0, vertices)
    for start in range(0, vertices, plan.batch_rows):
        stop = min(start + plan.batch_rows, vertices)
        monitor.check()
        rows = reader.read_range("vertex", start, stop)
        xyz = decode_coordinates(rows, ("x", "y", "z"))
        columns["xyz.bin"].write_range(start, xyz)
        columns["vertex-status.bin"].write_range(start, vertex_status(xyz))
        source_normals = (
            decode_coordinates(rows, ("nx", "ny", "nz")) if normals else None
        )
        if source_normals is not None:
            columns["normals.bin"].write_range(start, source_normals)
        columns["normal-status.bin"].write_range(
            start, normal_status(source_normals, len(rows))
        )
        staging.stage_vertices(start, xyz)
        monitor.progress("decode-vertices", stop, vertices)
        del rows, xyz, source_normals
    for name, column in columns.items():
        if name not in PENDING_IMPORT_COLUMNS and name != "triangles.bin":
            column.finish()
    monitor.progress("decode-faces", 0, faces)
    for start in range(0, faces, plan.batch_rows):
        stop = min(start + plan.batch_rows, faces)
        monitor.check()
        rows = reader.read_range("face", start, stop)
        indices = np.ascontiguousarray(rows["vertex_indices"]["values"])
        columns["triangles.bin"].write_range(start, indices)
        staging.stage_faces(start, indices)
        monitor.progress("decode-faces", stop, faces)
        del rows, indices
    columns["triangles.bin"].finish()
    staging.finish()
    source.verify(chunk_bytes=io_block_bytes, progress=monitor.progress)
    monitor.check()
    return ImportFoundation(
        directory,
        import_directory,
        source,
        columns,
        staging,
        plan,
        ledger,
        monitor,
        sidecar,
        vertices,
        faces,
        normals,
    )


@contextmanager
def prepare_import(
    ply: Path,
    destination: Path,
    *,
    sidecar: Path | None = None,
    budget_bytes: int = 512 * 1024 * 1024,
    storage: str = "auto",
    chunk_rows: int | None = None,
    io_block_bytes: int = 65_536,
    progress: Callable[[dict[str, Control]], None] | None = None,
    retain_incomplete: bool = False,
) -> Generator[ImportFoundation]:
    """Use the foundation within this scope; exit closes and removes owned work.

    S4 can consume staging/columns and publish closed stage directories inside
    the scope. Failure retention is explicitly incomplete and never a resume or
    success marker. No unrelated destination contents are cleaned.
    """
    if type(io_block_bytes) is not int or not 1 <= io_block_bytes <= 8 * 1024 * 1024:
        raise MeshImportError(
            "resource-budget-too-small", "planning", "invalid I/O buffer size"
        )
    warm_baseline()
    check_arithmetic()
    _ = plan_memory(
        budget_bytes=budget_bytes,
        baseline_bytes=int(str(memory_snapshot()["rss_bytes"])),
        canonical_bytes=0,
        vertices=0,
        faces=0,
        source_bytes=0,
        storage="disk",
        chunk_rows=1,
    )
    workspace = create_workspace(destination)
    directory = workspace.directory
    access = workspace.access
    failure_error: BaseException | None = None
    monitor = ResourceMonitor(budget_bytes, access, callback=progress)
    try:
        with ExitStack() as stack:
            _ = stack.enter_context(monitor)
            import_directory = access / "import"
            source_directory = import_directory / "source"
            source_directory.mkdir(parents=True)
            source = snapshot_bundle(
                source_directory,
                ply,
                sidecar,
                chunk_bytes=io_block_bytes,
                progress=monitor.progress,
            )
            _ = stack.callback(source.close)
            foundation = _prepare(
                access,
                source,
                monitor,
                stack,
                budget_bytes=budget_bytes,
                storage=storage,
                chunk_rows=chunk_rows,
                io_block_bytes=io_block_bytes,
            )
            foundation.directory = directory
            yield foundation
    except BaseException as error:
        if isinstance(error, MeshPlyError):
            error = MeshImportError(
                error.detail.category, "source-decode", str(error), row=error.detail.row
            )
        elif isinstance(error, duckdb.Error):
            error = duckdb_failure(error, "import-foundation")
        elif isinstance(error, MemoryError):
            error = MeshImportError("resource", "import-foundation", str(error))
        elif isinstance(error, OSError):
            category = (
                "resource"
                if error.errno in (errno.ENOSPC, errno.EDQUOT, errno.ENOMEM)
                else "execution"
            )
            error = MeshImportError(category, "import-foundation", str(error))
        failure_error = error
        if retain_incomplete:
            try:
                _check_owned_directory(directory, *workspace.identity)
                failure: dict[str, Control] = {
                    "revision": "mesh-incomplete-execution-v1",
                    "status": "incomplete",
                    "category": error.category
                    if isinstance(error, MeshImportError)
                    else "execution",
                    "message": str(error)[:4096],
                    "last_progress": monitor.record(),
                }
                with (access / "failure.json").open("xb") as stream:
                    _ = stream.write(encode_control(failure))
                error.add_note(f"Incomplete owned diagnostic directory: {directory}")
            except (OSError, MeshImportError) as report_error:
                error.add_note(f"Failure report could not be written: {report_error}")
        raise error
    finally:
        try:
            if not (failure_error is not None and retain_incomplete):
                try:
                    remove_owned_workspace(directory, workspace.identity)
                except (OSError, MeshImportError) as cleanup_error:
                    if failure_error is None:
                        raise
                    failure_error.add_note(
                        f"Owned workspace cleanup failed: {cleanup_error}"
                    )
        finally:
            workspace.close()


def _check_owned_directory(directory: Path, device: int, inode: int) -> None:
    current = directory.stat(follow_symlinks=False)
    if (current.st_dev, current.st_ino) != (device, inode):
        raise MeshImportError(
            "integrity",
            "cleanup",
            "owned directory path was replaced; left it untouched",
        )
