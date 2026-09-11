"""Read-only source replay into independent, owned temporary storage.

Hashes/accounting alone cannot prove derivation. This recomputes from retained
source bytes and compares every canonical column and control. A caller-supplied
root ID authenticates prior identity; an unsigned, wholly regenerated different
source/result cannot be recognized as changed history without that trusted ID.
Run through the fresh-worker supervisor for attributable whole-worker evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import numpy as np

from scansor.mesh_accounting import account_import
from scansor.mesh_artifacts import open_contributions, open_import
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_duckdb import warm_baseline
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_resources import ResourceMonitor, memory_snapshot, plan_memory
from scansor.mesh_workspace import create_workspace, remove_owned_workspace


def _equal(actual: Control, replayed: Control, label: str) -> None:
    if encode_control(actual) != encode_control(replayed):
        raise MeshImportError(
            "integrity", "source-replay", f"{label} differs from source recomputation"
        )


def _compare_columns(
    actual: dict[str, Column], replayed: dict[str, Column], monitor: ResourceMonitor
) -> None:
    if actual.keys() != replayed.keys():
        raise MeshImportError(
            "integrity", "source-replay", "canonical column set differs"
        )
    for name, expected in actual.items():
        generated = replayed[name]
        if expected.spec != generated.spec:
            raise MeshImportError(
                "integrity", "source-replay", "column shape/encoding differs"
            )
        chunk = (
            min(expected.max_range_bytes, generated.max_range_bytes)
            // expected.spec.stride
        )
        phase = "replay-compare-" + name
        monitor.progress(phase, 0, expected.spec.rows)
        for start in range(0, expected.spec.rows, chunk):
            monitor.check()
            stop = min(start + chunk, expected.spec.rows)
            left, right = (
                expected.read_range(start, stop),
                generated.read_range(start, stop),
            )
            if not np.array_equal(left.view("u1"), right.view("u1")):
                raise MeshImportError(
                    "integrity",
                    "source-replay",
                    f"{name} differs from source recomputation",
                    row=start,
                )
            monitor.progress(phase, stop, expected.spec.rows)
            del left, right


def verify_mesh(
    import_path: Path,
    workdir: Path,
    *,
    contribution_path: Path | None = None,
    expected_import_id: str | None = None,
    expected_contribution_id: str | None = None,
    budget_bytes: int = 512 * 1024 * 1024,
    storage: str = "auto",
    chunk_rows: int | None = None,
    progress: Callable[[dict[str, Control]], None] | None = None,
) -> dict[str, Control]:
    """Verify both stages, or an independently published import, without repair.

    Scratch must be outside the artifact trees. The caller owns the workdir;
    only this invocation's newly created workspace is cleaned. Existing artifacts
    are held read-only for the entire operation, including source copying/replay.
    """
    if contribution_path is None and expected_contribution_id is not None:
        raise MeshImportError(
            "structure",
            "source-replay",
            "contribution identity requires a contribution artifact",
        )
    resolved_work = workdir.resolve(strict=True)
    for path in (import_path, contribution_path):
        if path is not None and resolved_work.is_relative_to(path.resolve(strict=True)):
            raise MeshImportError(
                "structure",
                "source-replay",
                "scratch must be outside read-only artifact trees",
            )
    warm_baseline()
    initial = plan_memory(
        budget_bytes=budget_bytes,
        baseline_bytes=int(str(memory_snapshot()["rss_bytes"])),
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
    result: dict[str, Control] = {}
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
            contributions = (
                None
                if contribution_path is None
                else stack.enter_context(
                    open_contributions(
                        contribution_path,
                        imported,
                        expected_id=expected_contribution_id,
                        progress=monitor.progress,
                    )
                )
            )

            relay_active = True

            def relay(record: dict[str, Control]) -> None:
                # Inner foundation monitoring owns DuckDB interruption. Raising
                # here forwards outer budget/cancellation failure to that monitor.
                monitor.check()
                if not relay_active:
                    return
                monitor.progress(
                    "replay-" + str(record["phase"]),
                    int(str(record["completed"])),
                    int(str(record["total"])),
                )

            # Keep a real final parent component for the source snapshotter's
            # no-symlink-parent check; /proc/self/fd/<source_fd> itself is a link.
            source_parent = imported.directory.access / "source"
            scratch = workspace.access / "replay"
            scratch.mkdir()
            with (
                prepare_import(
                    source_parent / "observations.ply",
                    scratch,
                    sidecar=None
                    if imported.source.sidecar is None
                    else source_parent / "observations.rsInfo",
                    budget_bytes=budget_bytes,
                    storage=storage,
                    chunk_rows=chunk_rows,
                    progress=relay,
                ) as foundation,
                account_import(foundation) as replayed,
            ):
                relay_active = False
                _equal(
                    imported.source.inventory(),
                    foundation.source.inventory(),
                    "source bundle",
                )
                _compare_columns(imported.columns, foundation.columns, monitor)
                _equal(imported.inventory, replayed.inventory, "import inventory")
                _equal(imported.summary, replayed.summary, "import summary")
                if contributions is not None:
                    relay_active = True
                    try:
                        weighted = replayed.complete_contributions()
                    finally:
                        relay_active = False
                    _compare_columns(contributions.columns, weighted.columns, monitor)
                    _equal(
                        contributions.inventory,
                        weighted.inventory,
                        "contribution inventory",
                    )
                    _equal(
                        contributions.request, weighted.request, "contribution request"
                    )
                    _equal(
                        contributions.summary, weighted.summary, "contribution summary"
                    )
                    contributions.check()
                imported.check()
                result = {
                    "revision": "mesh-source-replay-v1",
                    "status": "verified",
                    "source_id": imported.source_id,
                    "import_id": imported.identity,
                    "contribution_id": None
                    if contributions is None
                    else contributions.identity,
                    "vertices": imported.vertices,
                    "faces": imported.faces,
                    "verified_columns": len(imported.columns)
                    + (0 if contributions is None else len(contributions.columns)),
                    "replay_execution": foundation.execution_report(),
                }
        result["verification_monitor"] = monitor.record()
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            try:
                remove_owned_workspace(workspace.directory, workspace.identity)
            except BaseException as cleanup_error:
                if failure is None:
                    raise
                failure.add_note(
                    f"Owned verification workspace cleanup failed: {cleanup_error}"
                )
        finally:
            workspace.close()
    final_memory = memory_snapshot()
    result["memory_after_cleanup"] = final_memory
    if int(str(final_memory["os_peak_rss_bytes"])) > budget_bytes:
        raise MeshImportError(
            "resource",
            "source-replay",
            "whole-process kernel peak exceeded the verification budget",
        )
    return result
