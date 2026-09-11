"""Bounded source-key and oriented-topology comparisons of display PLY resaves.

DuckDB supplies disk-backed joins and ordering. NumPy compares owned bounded
numeric batches. No viewer resave becomes authoritative input. Review DuckDB
and Arrow changelogs and renew spill/lifetime evidence when upgrading.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np

from scansor.mesh_artifact_io import ReadDirectory, ReadFile
from scansor.mesh_controls import Control
from scansor.mesh_display_ply import (
    CORNER_FIELDS,
    FACE_DIGITS,
    SCALAR_FIELDS,
    VERTEX_DIGITS,
    DisplayPlyReader,
    id_values,
)
from scansor.mesh_duckdb import (
    _numpy,  # pyright: ignore[reportPrivateUsage]
    duckdb_failure,
    pa,
    warm_baseline,
)
from scansor.mesh_errors import MeshImportError, io_failure
from scansor.mesh_resources import (
    MemoryPlan,
    ResourceMonitor,
    check_disk_space,
    memory_snapshot,
    plan_memory,
)
from scansor.mesh_workspace import create_workspace, remove_owned_workspace


def _count(connection: duckdb.DuckDBPyConnection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None or type(row[0]) is not int or row[0] < 0:
        raise MeshImportError("integrity", "viewer-compare", "invalid query population")
    return row[0]


def _insert(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    names: list[str],
    columns: list[np.ndarray],
) -> None:
    batch = pa.record_batch([pa.array(column) for column in columns], names=names)
    with pa.RecordBatchReader.from_batches(batch.schema, [batch]) as reader:
        _ = connection.register("viewer_input", reader)
        try:
            _ = connection.execute(f"INSERT INTO {table} SELECT * FROM viewer_input")
        finally:
            _ = connection.unregister("viewer_input")


def _load(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    reader: DisplayPlyReader,
    fields: tuple[str, ...],
    monitor: ResourceMonitor,
) -> str | None:
    key_fields = VERTEX_DIGITS + (
        (*FACE_DIGITS, "scalar_source_corner")
        if reader.kind == "rejected-face-corners"
        else ()
    )
    missing = [name for name in key_fields if name not in reader.fields()]
    if missing:
        _validate(reader, monitor, table)
        return "source key fields dropped or renamed: " + ", ".join(missing)
    definitions = ", ".join(f"v{i} UBIGINT" for i in range(len(fields)))
    _ = connection.execute(
        f"CREATE TABLE {table}(ordinal UBIGINT PRIMARY KEY, source UBIGINT, key_a UBIGINT, key_b UTINYINT, {definitions})"
    )
    _ = connection.execute(
        f"CREATE TABLE {table}_faces(a INTEGER, b INTEGER, c INTEGER)"
    )
    chunk = reader.chunk_rows
    phase = "stage-viewer-" + table + "-vertices"
    monitor.progress(phase, 0, reader.vertices)
    for start in range(0, reader.vertices, chunk):
        stop = min(start + chunk, reader.vertices)
        rows = reader.read_range("vertex", start, stop)
        try:
            source = id_values(rows, VERTEX_DIGITS)
            key_a = (
                id_values(rows, FACE_DIGITS)
                if reader.kind == "rejected-face-corners"
                else source
            )
            key_b = (
                rows["scalar_source_corner"]
                if reader.kind == "rejected-face-corners"
                else np.zeros(len(rows), dtype="u1")
            )
            if not np.all(np.isin(key_b, (0, 1, 2))):
                raise MeshImportError(
                    "integrity", "viewer-compare", "invalid source corner keys"
                )
        except MeshImportError as error:
            # This is a measured preservation failure, not permission to infer IDs.
            _validate(reader, monitor, table)
            return str(error)
        names = ["ordinal", "source", "key_a", "key_b"] + [
            f"v{i}" for i in range(len(fields))
        ]
        columns = [
            np.arange(start, stop, dtype="<u8"),
            source,
            key_a,
            key_b.astype("u1"),
        ]
        columns.extend(
            rows[name].astype("<f8").view("<u8")
            if name in reader.fields()
            else np.zeros(len(rows), dtype="<u8")
            for name in fields
        )
        _insert(connection, table, names, columns)
        monitor.progress(phase, stop, reader.vertices)
        del rows, columns, source, key_a, key_b
    phase = "stage-viewer-" + table + "-faces"
    monitor.progress(phase, 0, reader.faces)
    for start in range(0, reader.faces, chunk):
        stop = min(start + chunk, reader.faces)
        rows = reader.read_range("face", start, stop)["vertex_indices"]["values"]
        _insert(
            connection,
            table + "_faces",
            ["a", "b", "c"],
            [rows[:, i].copy() for i in range(3)],
        )
        monitor.progress(phase, stop, reader.faces)
        del rows
    return None


def _validate(reader: DisplayPlyReader, monitor: ResourceMonitor, table: str) -> None:
    for element in reader.layout.elements:
        phase = "validate-unmapped-" + table + "-" + element.element.name
        count = element.element.count
        monitor.progress(phase, 0, count)
        for start in range(0, count, reader.chunk_rows):
            stop = min(start + reader.chunk_rows, count)
            monitor.check()
            _ = reader.read_range(element.element.name, start, stop)
            monitor.progress(phase, stop, count)


def _topology(
    connection: duckdb.DuckDBPyConnection, monitor: ResourceMonitor
) -> dict[str, Control]:
    for table in ("original", "resaved"):
        monitor.progress("compare-viewer-" + table + "-topology", 0, 1)
        # Lexicographically least cyclic rotation preserves winding. Reversal
        # is deliberately a different tuple; GROUP BY preserves multiplicity.
        _ = connection.execute(f"""CREATE TABLE {table}_topology AS
WITH triples AS (
 SELECT least(struct_pack(x:=a.source,y:=b.source,z:=c.source),
              struct_pack(x:=b.source,y:=c.source,z:=a.source),
              struct_pack(x:=c.source,y:=a.source,z:=b.source)) AS oriented
 FROM {table}_faces f JOIN {table} a ON f.a = a.ordinal
 JOIN {table} b ON f.b = b.ordinal JOIN {table} c ON f.c = c.ordinal
) SELECT oriented, count(*)::UBIGINT AS multiplicity FROM triples GROUP BY oriented""")
        monitor.progress("compare-viewer-" + table + "-topology", 1, 1)
    query = """FROM original_topology a FULL OUTER JOIN resaved_topology b
ON a.oriented = b.oriented WHERE a.multiplicity IS DISTINCT FROM b.multiplicity"""
    differences = _count(connection, "SELECT count(*) " + query)
    duplicate_groups = _count(
        connection, "SELECT count(*) FROM original_topology WHERE multiplicity > 1"
    )
    return {
        "status": "exact" if differences == 0 else "changed",
        "different_oriented_face_groups": differences,
        "original_indistinguishable_duplicate_groups": duplicate_groups,
        "comparison": "source vertex triples modulo cyclic rotation, retaining winding and multiplicity",
        "individual_duplicate_face_identity": "not recoverable from resaved vertex triples"
        if duplicate_groups
        else "no indistinguishable duplicate faces in original view",
    }


def _compare(
    connection: duckdb.DuckDBPyConnection,
    original: DisplayPlyReader,
    resaved: DisplayPlyReader,
    fields: tuple[str, ...],
    plan: MemoryPlan,
    monitor: ResourceMonitor,
) -> dict[str, Control]:
    originals, saved = original.fields(), resaved.fields()
    field_reports: dict[str, Control] = {}
    dropped = sorted(set(originals) - set(saved))
    added = sorted(set(saved) - set(originals))
    reports: dict[str, Control] = {
        "original_population": {"vertices": original.vertices, "faces": original.faces},
        "resaved_population": {"vertices": resaved.vertices, "faces": resaved.faces},
        "original_property_types": dict(originals),
        "resaved_property_types": dict(saved),
        "dropped_or_renamed_fields": list(dropped),
        "added_or_renamed_fields": list(added),
        "fields": field_reports,
    }
    for table, reader in (("original", original), ("resaved", resaved)):
        problem = _load(connection, table, reader, fields, monitor)
        if problem is not None:
            reports["mapping"] = {"status": "unavailable", "reason": problem}
            reports["topology"] = {
                "status": "unverified",
                "reason": "source keys unavailable",
            }
            return reports
        duplicate_groups = _count(
            connection,
            f"SELECT count(*) FROM (SELECT key_a,key_b FROM {table} GROUP BY key_a,key_b HAVING count(*) > 1)",
        )
        if duplicate_groups:
            reports["mapping"] = {
                "status": "ambiguous",
                "table": table,
                "duplicate_key_groups": duplicate_groups,
            }
            reports["topology"] = {
                "status": "unverified",
                "reason": "source keys are not unique",
            }
            return reports
    join = "FROM original a FULL OUTER JOIN resaved b ON a.key_a = b.key_a AND a.key_b = b.key_b"
    missing = _count(connection, "SELECT count(*) " + join + " WHERE b.ordinal IS NULL")
    extra = _count(connection, "SELECT count(*) " + join + " WHERE a.ordinal IS NULL")
    moved = _count(
        connection, "SELECT count(*) " + join + " WHERE a.ordinal <> b.ordinal"
    )
    wrong_source = _count(
        connection, "SELECT count(*) " + join + " WHERE a.source <> b.source"
    )
    reports["mapping"] = {
        "status": "exact" if missing == extra == wrong_source == 0 else "changed",
        "missing_keys": missing,
        "extra_keys": extra,
        "reordered_matched_rows": moved,
        "matched_keys_with_changed_source_vertex": wrong_source,
    }
    common = [(i, name) for i, name in enumerate(fields) if name in saved]
    counts = [
        {"different": 0, "to_zero": 0, "to_nonfinite": 0, "finite_rounding": 0}
        for _ in common
    ]
    examples: list[list[Control]] = [[] for _ in common]
    selection = ["a.key_a AS key_a", "a.key_b AS key_b"]
    for index, _ in common:
        selection.extend((f"a.v{index} AS a{index}", f"b.v{index} AS b{index}"))
    total = original.vertices - missing
    monitor.progress("compare-viewer-fields", 0, total)
    query = (
        "SELECT "
        + ",".join(selection)
        + " "
        + join
        + " WHERE a.ordinal IS NOT NULL AND b.ordinal IS NOT NULL ORDER BY a.key_a,a.key_b"
    )
    seen = 0
    with cast(Any, connection.sql(query)).to_arrow_reader(
        batch_size=plan.batch_rows
    ) as reader:
        for batch in reader:
            monitor.check()
            if (
                not 0 < batch.num_rows <= plan.batch_rows
                or seen + batch.num_rows > total
            ):
                raise MeshImportError(
                    "integrity", "viewer-compare", "invalid comparison cardinality"
                )
            key_a = _numpy(batch, "key_a", "<u8").copy()
            key_b = _numpy(batch, "key_b", "u1").copy()
            for position, (index, _name) in enumerate(common):
                left = _numpy(batch, f"a{index}", "<u8").copy()
                right = _numpy(batch, f"b{index}", "<u8").copy()
                a, b = left.view("<f8"), right.view("<f8")
                different = left != right
                finite = np.isfinite(b)
                count = counts[position]
                count["different"] += int(np.count_nonzero(different))
                count["to_zero"] += int(np.count_nonzero((a != 0) & (b == 0)))
                count["to_nonfinite"] += int(np.count_nonzero(~finite))
                count["finite_rounding"] += int(
                    np.count_nonzero(different & finite & (b != 0))
                )
                for row in np.flatnonzero(different)[
                    : max(0, 8 - len(examples[position]))
                ]:
                    examples[position].append(
                        {
                            "source_key": [int(key_a[row]), int(key_b[row])],
                            "original_binary64_bits": f"{int(left[row]):016x}",
                            "resaved_binary64_bits": f"{int(right[row]):016x}",
                        }
                    )
                del left, right, a, b, different, finite
            seen += batch.num_rows
            monitor.progress("compare-viewer-fields", seen, total)
            del batch, key_a, key_b
    if seen != total:
        raise MeshImportError("integrity", "viewer-compare", "incomplete comparison")
    for position, (_, name) in enumerate(common):
        count = counts[position]
        field_reports[name] = {
            "compared_rows": seen,
            **count,
            "examples": examples[position],
        }
    reports["topology"] = _topology(connection, monitor)
    return reports


def compare_viewer_resave(
    original_path: Path,
    resaved_path: Path,
    kind: str,
    workdir: Path,
    *,
    budget_bytes: int = 512 * 1024 * 1024,
    chunk_rows: int | None = None,
    progress: Callable[[dict[str, Control]], None] | None = None,
) -> dict[str, Control]:
    """Report preservation and degradation without inventing missing source keys."""
    warm_baseline()
    baseline = int(str(memory_snapshot()["rss_bytes"]))
    plan = plan_memory(
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
            files: list[ReadFile] = []
            hashes: list[str] = []
            readers: list[DisplayPlyReader] = []
            for index, path in enumerate((original_path, resaved_path)):
                root = ReadDirectory(path.parent)
                _ = stack.callback(root.close)
                file = ReadFile(root, path.name)
                _ = stack.callback(file.close)
                files.append(file)
                hashes.append(
                    file.sha256(
                        phase="hash-viewer-input-" + str(index),
                        progress=monitor.progress,
                    )
                )
                _ = file.stream.seek(0)
                readers.append(
                    DisplayPlyReader(
                        file.stream,
                        kind,
                        chunk_rows=plan.batch_rows,
                        resaved=index == 1,
                    )
                )
            # Table words, face joins and sorts are disk-backed. Only one joined
            # batch is retained; scalar pairs are compared one field at a time.
            estimate = sum(
                512 * reader.vertices + 128 * reader.faces for reader in readers
            )
            plan = plan_memory(
                budget_bytes=budget_bytes,
                baseline_bytes=baseline,
                canonical_bytes=0,
                vertices=sum(reader.vertices for reader in readers),
                faces=sum(reader.faces for reader in readers),
                source_bytes=estimate,
                storage="disk",
                chunk_rows=plan.batch_rows,
            )
            check_disk_space(workspace.access, estimate + plan.engine_temp_limit_bytes)
            connection = duckdb.connect(
                str(workspace.access / "viewer.duckdb"),
                config={
                    "memory_limit": f"{plan.engine_bytes}B",
                    "threads": 1,
                    "temp_directory": str(workspace.access / "spill"),
                    "max_temp_directory_size": f"{plan.engine_temp_limit_bytes}B",
                    "preserve_insertion_order": False,
                    "autoinstall_known_extensions": False,
                    "autoload_known_extensions": False,
                },
            )
            _ = stack.callback(connection.close)
            monitor.set_interrupt(connection.interrupt)
            _ = stack.callback(monitor.set_interrupt, None)
            names = (
                "x",
                "y",
                "z",
                "red",
                "green",
                "blue",
                *SCALAR_FIELDS,
                *(CORNER_FIELDS if kind == "rejected-face-corners" else ()),
            )
            fields = tuple(
                name
                for name in names
                if name not in (*VERTEX_DIGITS, *FACE_DIGITS, "scalar_source_corner")
            )
            report = _compare(connection, readers[0], readers[1], fields, plan, monitor)
            for file in files:
                file.check()
            monitor.check()
        return {
            "revision": "mesh-viewer-comparison-v1",
            "kind": kind,
            "original_sha256": hashes[0],
            "resaved_sha256": hashes[1],
            **report,
            "plan": plan.record(),
            "monitor": monitor.record(),
            "numeric_counts": "Compared matched source keys only; finite rounding, nonzero-to-zero and nonfinite results are observations, not inferred viewer implementation causes.",
            "authority": "Viewer files and this report do not replace authoritative mesh stages.",
        }
    except BaseException as error:
        failure = error
        monitor.check()
        if isinstance(error, duckdb.Error):
            raise duckdb_failure(error, "viewer-compare") from error
        if isinstance(error, OSError):
            raise io_failure(error, "viewer-compare") from error
        raise
    finally:
        try:
            try:
                remove_owned_workspace(workspace.directory, workspace.identity)
            except BaseException as error:
                if failure is None:
                    raise
                failure.add_note(f"Owned viewer comparison cleanup failed: {error}")
        finally:
            workspace.close()
