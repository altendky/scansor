"""Isolate full source-corner sorting from the other import staging tables.

This diagnostic reuses a previously verified generated import. It preserves its
owned database for a separate fresh-process reopen comparison. It is not a full
import, a sampling-coverage result, or a whole-worker budget guarantee.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import pyarrow as pa  # pyright: ignore[reportMissingTypeStubs]

from experiments.mesh_scale_check import file_hash
from experiments.mesh_scale_run import implementation_record, outside_git
from scansor.mesh_digests import RowDigest
from scansor.mesh_numeric import corner_areas
from scansor.mesh_resources import (
    cgroup_snapshot,
    memory_snapshot,
    process_io_snapshot,
)
from scansor.mesh_semantics import ColumnSpec

FIELDS = (("vertex", "<u8"), ("face", "<u8"), ("corner", "u1"), ("allocation", "<u8"))
BATCH = 65_536


def digest_for(count: int) -> RowDigest:
    return RowDigest(
        "mesh-working-source-corners-v1",
        tuple(ColumnSpec(name + ".bin", count, 1, dtype) for name, dtype in FIELDS),
        max_rows=BATCH,
    )


def prepare(connection: Any, prior: Path) -> dict[str, Any]:
    report = json.loads(prior.read_text())
    if report["status"] != "complete":
        raise ValueError("requires a previously complete generated run")
    imports = [
        stage
        for stage in report["operations"]["import"]["outcome"]["published"]
        if stage["kind"] == "import"
    ]
    if len(imports) != 1:
        raise ValueError("requires exactly one verified import")
    source = outside_git(Path(imports[0]["path"]))
    inputs = {}
    for name in ("triangles.bin", "face-area.bin"):
        observed = file_hash(source / name)
        if observed != report["checks"]["canonical"]["columns"][name]:
            raise ValueError("full canonical input hash differs: " + name)
        inputs[name] = observed
    faces = (source / "triangles.bin").stat().st_size // 12
    vertices = report["checks"]["canonical"]["vertices"]
    if (source / "face-area.bin").stat().st_size != faces * 8 or faces != report[
        "checks"
    ]["canonical"]["faces"]:
        raise ValueError("inconsistent canonical face columns")
    expected, seen = digest_for(3 * faces), 0
    last_progress = time.monotonic()
    connection.execute(
        "CREATE TABLE mesh_corners(vertex UBIGINT, face UBIGINT, corner UTINYINT, allocation UBIGINT)"
    )
    with (
        (source / "triangles.bin").open("rb", buffering=0) as triangles,
        (source / "face-area.bin").open("rb", buffering=0) as areas,
    ):
        for start in range(0, faces, BATCH):
            count = min(BATCH, faces - start)
            indices = np.fromfile(triangles, dtype="<i4", count=count * 3)
            area = np.fromfile(areas, dtype="<f8", count=count)
            if (
                len(indices) != count * 3
                or len(area) != count
                or np.any(indices < 0)
                or np.any(indices >= vertices)
            ):
                raise ValueError("requires complete generated in-range face inputs")
            thirds = corner_areas(area).view("<u8")
            for offset in range(0, count * 3, BATCH):
                positions = np.arange(
                    offset, min(offset + BATCH, count * 3), dtype="<u8"
                )
                columns = (
                    indices[positions].astype("<u8"),
                    positions // 3 + np.uint64(start),
                    (positions % 3).astype("u1"),
                    thirds[positions // 3].copy(),
                )
                expected.update(seen, columns)
                batch = cast(Any, pa).record_batch(
                    [cast(Any, pa).array(column) for column in columns],
                    names=[name for name, _ in FIELDS],
                )
                with cast(Any, pa).RecordBatchReader.from_batches(
                    batch.schema, [batch]
                ) as reader:
                    connection.register("corner_input", reader)
                    try:
                        connection.execute(
                            "INSERT INTO mesh_corners SELECT * FROM corner_input"
                        )
                    finally:
                        connection.unregister("corner_input")
                seen += len(positions)
                del positions, columns, batch
            del indices, area, thirds
            if time.monotonic() - last_progress >= 5:
                print(
                    json.dumps(
                        {
                            "phase": "stage-only-corners",
                            "completed": seen,
                            "total": 3 * faces,
                        }
                    ),
                    flush=True,
                )
                last_progress = time.monotonic()
    return {
        "rows": seen,
        "source_digest": expected.finish(),
        "inputs": inputs,
        "prior_run_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--directory", type=Path, required=True)
    _ = parser.add_argument("--prior-run", type=Path)
    _ = parser.add_argument("--engine-bytes", type=int, required=True)
    _ = parser.add_argument("--force-external", action="store_true")
    _ = parser.add_argument("--keys-only", action="store_true")
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    directory = outside_git(args.directory.parent) / args.directory.name
    output = outside_git(args.output.parent) / args.output.name
    if output.exists() or args.engine_bytes < 32 * 1024**2:
        raise ValueError("invalid engine allowance or existing output")
    if args.prior_run:
        directory.mkdir(mode=0o700)
    else:
        _ = outside_git(directory / "corners.duckdb")
    manifest = directory / "input.json"
    config: dict[str, str | bool | int | float | list[str]] = {
        "memory_limit": f"{args.engine_bytes}B",
        "threads": 1,
        "temp_directory": str(directory / "spill"),
        "max_temp_directory_size": "40GiB",
        "preserve_insertion_order": False,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
    }
    result: dict[str, Any] = {
        "scope": __doc__,
        "mode": "prepare-and-sort" if args.prior_run else "reopen-and-sort",
        "force_external": args.force_external,
        "keys_only": args.keys_only,
        "config": config,
        "implementation": implementation_record(),
        "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "memory_at_start": memory_snapshot(),
        "io_at_start": process_io_snapshot(),
        "cgroup_at_start": cgroup_snapshot(),
        "status": "failed",
    }
    started = time.monotonic_ns()
    connection = duckdb.connect(str(directory / "corners.duckdb"), config=config)
    try:
        if args.prior_run:
            source = prepare(connection, outside_git(args.prior_run))
            with manifest.open("x") as stream:
                _ = stream.write(json.dumps(source, indent=2) + "\n")
        else:
            source = json.loads(manifest.read_text())
        result["source"] = source
        result["before_sort"] = {
            "elapsed_ns": time.monotonic_ns() - started,
            "memory": memory_snapshot(),
            "duckdb_memory": connection.execute(
                "SELECT * FROM duckdb_memory()"
            ).fetchall(),
            "io": process_io_snapshot(),
        }
        print(
            json.dumps(
                {"phase": "sort-only-corners", "before_sort": result["before_sort"]}
            ),
            flush=True,
        )
        if args.force_external:
            # Diagnostic only: this debug control is not adopted by production.
            _ = connection.execute("SET debug_force_external=true")
        digest, seen = digest_for(source["rows"]), 0
        sort_started = time.monotonic_ns()
        # Face/corner is unique in the generated source. Appending the remaining
        # fields preserves its order while allowing DuckDB to omit sort payloads.
        order = "face, corner, vertex, allocation" if args.keys_only else "face, corner"
        result["order_by"] = order
        with cast(
            Any,
            connection.sql(
                "SELECT vertex, face, corner, allocation FROM mesh_corners ORDER BY "
                + order
            ),
        ).to_arrow_reader(batch_size=BATCH) as reader:
            for batch in reader:
                columns = tuple(
                    batch.column(name)
                    .to_numpy(zero_copy_only=False)
                    .astype(dtype, copy=True)
                    for name, dtype in FIELDS
                )
                digest.update(seen, columns)
                seen += batch.num_rows
                del columns, batch
        result["sort_elapsed_ns"] = time.monotonic_ns() - sort_started
        result["rows"] = seen
        result["digest"] = digest.finish()
        if result["digest"] != source["source_digest"]:
            raise ValueError("complete sorted tuples differ from source")
        result["status"] = "complete"
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        connection.close()
        result["elapsed_ns"] = time.monotonic_ns() - started
        result["memory_after_close"] = memory_snapshot()
        result["io_after_close"] = process_io_snapshot()
        result["cgroup_after_close"] = cgroup_snapshot()
        with output.open("x") as stream:
            _ = stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": result["status"], "report": str(output)}), flush=True)
    sys.exit(0 if result["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
