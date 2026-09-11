"""Read-only display verification by complete replay from authoritative columns.

Caller-pinned import/contribution IDs establish the authority boundary. This does
not replace mesh_replay's geometry-from-source verification of those stages.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import cast

import duckdb

from scansor.errors import ScansorError
from scansor.mesh_artifact_io import ReadDirectory, ReadFile
from scansor.mesh_artifacts import open_contributions, open_import
from scansor.mesh_controls import Control, control_id, encode_control
from scansor.mesh_display import prepare_display
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_duckdb import duckdb_failure, warm_baseline
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_resources import (
    ResourceMonitor,
    check_disk_space,
    memory_snapshot,
    plan_memory,
)
from scansor.mesh_workspace import create_workspace, remove_owned_workspace


def read_transform(legend: dict[str, Control]) -> DisplayTransform:
    record = legend.get("transform")
    if not isinstance(record, dict):
        raise MeshImportError(
            "structure", "display-verify", "missing display transform"
        )
    origin, power = record.get("origin_binary64_bits"), record.get("scale_power_of_two")
    if (
        not isinstance(origin, list)
        or len(origin) != 3
        or not all(type(value) is str for value in origin)
        or type(power) is not int
    ):
        raise MeshImportError(
            "structure", "display-verify", "invalid display transform"
        )
    result = DisplayTransform(cast(tuple[str, str, str], tuple(origin)), power)
    if encode_control(result.record()) != encode_control(record):
        raise MeshImportError(
            "integrity", "display-verify", "unknown transform metadata"
        )
    return result


def verify_display(
    display_path: Path,
    import_path: Path,
    contribution_path: Path,
    workdir: Path,
    *,
    expected_display_id: str | None = None,
    expected_import_id: str | None = None,
    expected_contribution_id: str | None = None,
    budget_bytes: int = 512 * 1024 * 1024,
    chunk_rows: int | None = None,
    progress: Callable[[dict[str, Control]], None] | None = None,
) -> dict[str, Control]:
    """Regenerate every display byte, then compare held originals without repair."""
    for artifact in (display_path, import_path, contribution_path):
        if workdir.resolve(strict=True).is_relative_to(artifact.resolve(strict=True)):
            raise MeshImportError(
                "structure", "display-verify", "scratch must be outside artifact trees"
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
            root = ReadDirectory(display_path)
            _ = stack.callback(root.close)
            names = {
                "inventory.json",
                "legend.json",
                "validity.ply",
                "weights.ply",
                "rejected-face-corners.ply",
                "view-vertices.bin",
                "view-faces.bin",
            }
            root.require_names(names)
            files: dict[str, ReadFile] = {}
            for name in sorted(names):
                file = ReadFile(root, name)
                _ = stack.callback(file.close)
                files[name] = file
            inventory = files["inventory.json"].control()
            legend = files["legend.json"].control()
            identity = control_id(inventory)
            if expected_display_id is not None and identity != expected_display_id:
                raise MeshImportError(
                    "integrity",
                    "display-verify",
                    "display identity differs from expected root",
                )
            transform = read_transform(legend)
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
            monitor.set_plan(plan)
            check_disk_space(workspace.access, plan.disk_estimate_bytes)
            _, rebuilt_legend, rebuilt = prepare_display(
                data, workspace.access, plan, monitor, transform
            )
            if encode_control(legend) != encode_control(
                rebuilt_legend
            ) or encode_control(inventory) != encode_control(rebuilt):
                raise MeshImportError(
                    "integrity",
                    "display-verify",
                    "display metadata or hashes differ from complete authoritative replay",
                )
            records = cast(list[dict[str, Control]], rebuilt["files"])
            for record in records:
                name = str(record["name"])
                file = files[name]
                if (
                    file.size != record["byte_count"]
                    or file.sha256(
                        phase="verify-display-" + name, progress=monitor.progress
                    )
                    != record["sha256"]
                ):
                    raise MeshImportError(
                        "integrity",
                        "display-verify",
                        "display file differs from complete authoritative replay",
                    )
            for file in files.values():
                file.check()
            data.check()
            monitor.check()
        return {
            "revision": "mesh-display-verification-v1",
            "status": "verified",
            "display_id": identity,
            "import_id": data.imported.identity,
            "contribution_id": data.identity,
            "verified_data_files": len(records),
            "plan": plan.record(),
            "monitor": monitor.record(),
            "authority": "Replayed from supplied authoritative columns; source geometry replay is a separate check.",
        }
    except BaseException as error:
        failure = error
        monitor.check()
        if isinstance(error, duckdb.Error):
            raise duckdb_failure(error, "display-verify") from error
        if isinstance(error, OSError):
            raise io_failure(error, "display-verify") from error
        if isinstance(error, ScansorError) and not isinstance(error, MeshImportError):
            raise MeshImportError("integrity", "display-verify", str(error)) from error
        raise
    finally:
        try:
            try:
                remove_owned_workspace(workspace.directory, workspace.identity)
            except BaseException as error:
                if failure is None:
                    raise
                failure.add_note(f"Owned display verification cleanup failed: {error}")
        finally:
            workspace.close()
