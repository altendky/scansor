"""Bounded full-resolution audit export from read-only authoritative mesh stages."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np

from scansor._plyio import PlyError
from scansor.mesh_artifacts import ContributionArtifact, open_contributions, open_import
from scansor.mesh_controls import Control, control_id
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_display_ply import KINDS, MAX_VERTICES, DisplayPlyReader
from scansor.mesh_display_records import (
    MapOutput,
    PlyOutput,
    face_records,
    vertex_records,
)
from scansor.mesh_display_semantics import display_inventory, display_legend
from scansor.mesh_display_staging import DisplayStaging
from scansor.mesh_duckdb import duckdb_failure, warm_baseline
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_numeric import NumericProfileError, bits_float
from scansor.mesh_publication import (
    PublishedStage,
    _check_control_child,  # pyright: ignore[reportPrivateUsage]
    _publish,  # pyright: ignore[reportPrivateUsage]
    _write_control,  # pyright: ignore[reportPrivateUsage]
)
from scansor.mesh_resources import (
    MemoryPlan,
    ResourceMonitor,
    check_disk_space,
    memory_snapshot,
    plan_memory,
)
from scansor.mesh_workspace import create_workspace, remove_owned_workspace


@dataclass(frozen=True)
class DisplayExport:
    stage: PublishedStage
    inventory: dict[str, Control]
    legend: dict[str, Control]
    execution: dict[str, Control]


def _maximum(data: ContributionArtifact) -> float:
    record = data.summary["vertex_area_range"]
    assert isinstance(record, dict)
    value = record["maximum_bits"]
    return 0.0 if value is None else bits_float(str(value))


def write_views(
    directory: Path,
    staging: DisplayStaging,
    transform: DisplayTransform,
) -> tuple[list[Control], int, int]:
    """Write every expected row, retaining hashes of intended bytes before close."""
    if max(staging.vertices, staging.corners) > MAX_VERTICES:
        raise MeshImportError(
            "unsupported",
            "display-export",
            "full view exceeds the PLY/view index limit",
        )
    chunk, maximum = staging.plan.batch_rows, _maximum(staging.data)
    with ExitStack() as stack:
        outputs: dict[str, PlyOutput] = {}
        for kind in KINDS:
            count = (
                staging.corners if kind == "rejected-face-corners" else staging.vertices
            )
            faces = 0 if kind == "rejected-face-corners" else staging.faces
            output = PlyOutput(directory / (kind + ".ply"), kind, count, faces, chunk)
            _ = stack.callback(output.close)
            outputs[kind] = output
        vertex_map = MapOutput(directory / "view-vertices.bin", staging.vertices, chunk)
        _ = stack.callback(vertex_map.close)
        face_map = MapOutput(directory / "view-faces.bin", staging.faces, chunk)
        _ = stack.callback(face_map.close)
        seen, previous, vertex_loss = 0, -1, 0
        for columns in staging.vertex_batches():
            ids, view = columns[:2]
            count = len(ids)
            if (
                not np.array_equal(view, np.arange(seen, seen + count, dtype="<i4"))
                or int(ids[0]) <= previous
                or np.any(ids[1:] <= ids[:-1])
            ):
                raise MeshImportError(
                    "integrity",
                    "display-export",
                    "missing or reordered view vertex/source IDs",
                )
            for kind in ("validity", "weights"):
                output = outputs[kind]
                rows, lost = vertex_records(
                    output.layout.elements[0].dtype,
                    kind,
                    columns,
                    transform,
                    maximum=maximum,
                )
                output.write("vertex", seen, rows)
                if kind == "validity":
                    vertex_loss += lost
                del rows
            vertex_map.write(seen, ids)
            seen += count
            previous = int(ids[-1])
            del columns, ids, view
        seen, previous = 0, -1
        for columns in staging.face_batches():
            ids, *corners = columns
            indices = np.column_stack(corners)
            if int(ids[0]) <= previous or np.any(ids[1:] <= ids[:-1]):
                raise MeshImportError(
                    "integrity", "display-export", "invalid remapped usable face"
                )
            rows = face_records(
                outputs["validity"].layout.elements[1].dtype, indices, staging.vertices
            )
            for kind in ("validity", "weights"):
                outputs[kind].write("face", seen, rows)
            face_map.write(seen, ids)
            seen += len(ids)
            previous = int(ids[-1])
            del columns, ids, corners, indices, rows
        seen, last, corner_loss = 0, (-1, -1), 0
        for columns in staging.corner_batches():
            face, corner, status, *vertex = columns
            ordered = (face[1:] > face[:-1]) | (
                (face[1:] == face[:-1]) & (corner[1:] > corner[:-1])
            )
            if (int(face[0]), int(corner[0])) <= last or not np.all(ordered):
                raise MeshImportError(
                    "integrity",
                    "display-export",
                    "missing or reordered rejected-corner keys",
                )
            output = outputs["rejected-face-corners"]
            rows, lost = vertex_records(
                output.layout.elements[0].dtype,
                "rejected-face-corners",
                tuple(vertex),
                transform,
                maximum=maximum,
                face_ids=face,
                corner_ids=corner,
                face_status=status,
            )
            output.write("vertex", seen, rows)
            seen += len(rows)
            corner_loss += lost
            last = int(face[-1]), int(corner[-1])
            del columns, face, corner, status, vertex, rows
        files: list[Control] = [outputs[kind].finish() for kind in KINDS]
        files.extend((vertex_map.finish(), face_map.finish()))
    return files, vertex_loss, corner_loss


def prepare_display(
    data: ContributionArtifact,
    directory: Path,
    plan: MemoryPlan,
    monitor: ResourceMonitor,
    transform: DisplayTransform,
) -> tuple[Path, dict[str, Control], dict[str, Control]]:
    """Rebuild all views from authority, usable for export or read-only replay."""
    with ExitStack() as stack:
        staging = DisplayStaging(directory, plan, monitor, data)
        _ = stack.callback(staging.close)
        staging.load()
        stage = directory / "display"
        stage.mkdir()
        files, vertex_loss, corner_loss = write_views(stage, staging, transform)
        staging.close()
        # Semantic profile validation supplements the intended byte hashes.
        for kind in KINDS:
            with (stage / (kind + ".ply")).open("rb") as stream:
                reader = DisplayPlyReader(stream, kind, chunk_rows=plan.batch_rows)
                for element in reader.layout.elements:
                    phase = "validate-display-" + kind + "-" + element.element.name
                    monitor.progress(phase, 0, element.element.count)
                    for start in range(0, element.element.count, plan.batch_rows):
                        monitor.check()
                        _ = reader.read_range(
                            element.element.name,
                            start,
                            min(start + plan.batch_rows, element.element.count),
                        )
                        monitor.progress(
                            phase,
                            min(start + plan.batch_rows, element.element.count),
                            element.element.count,
                        )
        legend = display_legend(
            data,
            corners=staging.corners,
            maximum=_maximum(data),
            transform=transform,
            vertex_loss=vertex_loss,
            corner_loss=corner_loss,
        )
        inventory = display_inventory(data, legend, files)
        return stage, legend, inventory


def export_display(
    import_path: Path,
    contribution_path: Path,
    destination: Path,
    workdir: Path,
    *,
    transform: DisplayTransform | None = None,
    expected_import_id: str | None = None,
    expected_contribution_id: str | None = None,
    budget_bytes: int = 512 * 1024 * 1024,
    chunk_rows: int | None = None,
    progress: Callable[[dict[str, Control]], None] | None = None,
    publication_staging: Path | None = None,
    on_published: Callable[[PublishedStage], None] | None = None,
) -> DisplayExport:
    """Scoped internal exporter; use a fresh worker for attributable RSS evidence."""
    if transform is None:
        transform = DisplayTransform()
    writable_paths = [destination, workdir]
    if publication_staging is not None:
        writable_paths.append(publication_staging)
    for artifact in (import_path, contribution_path):
        root = artifact.resolve(strict=True)
        if any(
            path.resolve(strict=True).is_relative_to(root) for path in writable_paths
        ):
            raise MeshImportError(
                "structure",
                "display-export",
                "output, publication staging and scratch must be outside authoritative artifact trees",
            )
    warm_baseline()
    baseline = int(str(memory_snapshot()["rss_bytes"]))
    initial = plan_memory(
        budget_bytes=budget_bytes,
        baseline_bytes=baseline,
        canonical_bytes=0,
        vertices=0,
        faces=0,
        source_bytes=0,
        storage="disk",
        chunk_rows=chunk_rows,
    )
    workspace = create_workspace(workdir)
    monitor = ResourceMonitor(budget_bytes, workspace.access, callback=progress)
    failure: BaseException | None = None
    try:
        with monitor, ExitStack() as stack:
            imported = stack.enter_context(
                open_import(
                    import_path,
                    chunk_rows=initial.batch_rows,
                    expected_id=expected_import_id,
                    progress=monitor.progress,
                )
            )
            data = stack.enter_context(
                open_contributions(
                    contribution_path,
                    imported,
                    expected_id=expected_contribution_id,
                    progress=monitor.progress,
                )
            )
            # Packed outputs/maps plus source association and bounded sort work.
            # All output files/maps are streamed; no full resident display array.
            estimate = 150 * imported.vertices + 319 * imported.faces + 65536
            plan = plan_memory(
                budget_bytes=budget_bytes,
                baseline_bytes=baseline,
                canonical_bytes=0,
                vertices=imported.vertices,
                faces=imported.faces,
                source_bytes=estimate,
                storage="disk",
                chunk_rows=initial.batch_rows,
            )
            check_disk_space(workspace.access, plan.disk_estimate_bytes)
            check_disk_space(destination, estimate)
            stage, legend, inventory = prepare_display(
                data, workspace.access, plan, monitor, transform
            )
            files = inventory["files"]
            assert isinstance(files, list)
            identity = control_id(inventory)
            expected: dict[str, tuple[int, str]] = {}
            for record in files:
                assert isinstance(record, dict)
                expected[str(record["name"])] = (
                    int(str(record["byte_count"])),
                    str(record["sha256"]),
                )
            data.check()

            def prepare(held: int) -> dict[str, tuple[int, str]]:
                result = dict(expected)
                result["legend.json"] = _write_control(held, "legend.json", legend)
                _check_control_child(
                    "legend.json", result["legend.json"], inventory["legend"]
                )
                result["inventory.json"] = _write_control(
                    held, "inventory.json", inventory
                )
                if result["inventory.json"][1] != identity:
                    raise MeshImportError(
                        "integrity", "display-publication", "display inventory changed"
                    )
                return result

            monitor.progress("publish-display", 0, 1)
            published = _publish(
                stage,
                destination,
                "display",
                identity,
                prepare,
                monitor.check,
                publication_staging,
            )
            if on_published is not None:
                on_published(published)
            monitor.progress("publish-display", 1, 1)
            data.check()
        execution: dict[str, Control] = {
            "plan": plan.record(),
            "monitor": monitor.record(),
            "duckdb": duckdb.__version__,
            "storage": "streamed files and direct DuckDB; no full resident view",
        }
        return DisplayExport(published, inventory, legend, execution)
    except BaseException as error:
        failure = error
        monitor.check()
        if isinstance(error, duckdb.Error):
            raise duckdb_failure(error, "display-export") from error
        if isinstance(error, NumericProfileError):
            raise MeshImportError(
                "numeric-profile-failure", "display-export", str(error)
            ) from error
        if isinstance(error, OSError):
            raise io_failure(error, "display-export") from error
        if isinstance(error, PlyError):
            cause: BaseException | None = error
            while cause is not None:
                if isinstance(cause, OSError):
                    raise io_failure(cause, "display-export") from error
                cause = cause.__cause__
            raise MeshImportError(
                error.category, "display-export", str(error)
            ) from error
        raise
    finally:
        try:
            try:
                remove_owned_workspace(workspace.directory, workspace.identity)
            except BaseException as error:
                if failure is None:
                    raise
                failure.add_note(f"Owned display workspace cleanup failed: {error}")
        finally:
            workspace.close()
