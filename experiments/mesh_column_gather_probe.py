"""Opt-in row-gather I/O diagnostic; never a full-import capacity measurement."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, cast

import numpy as np

from experiments.mesh_scale_run import outside_git
from scansor.mesh_columns import Column
from scansor.mesh_resources import memory_snapshot, process_io_snapshot
from scansor.mesh_semantics import ColumnSpec


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--column-module", type=Path, required=True)
    _ = parser.add_argument("--xyz", type=Path, required=True)
    _ = parser.add_argument("--triangles", type=Path, required=True)
    _ = parser.add_argument("--vertices", type=int, required=True)
    _ = parser.add_argument("--faces", type=int, required=True)
    _ = parser.add_argument("--batch-rows", type=int, default=65_536)
    _ = parser.add_argument("--batches", type=int, default=4)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    xyz, triangles = outside_git(args.xyz), outside_git(args.triangles)
    output = outside_git(args.output.parent) / args.output.name
    if (
        args.batch_rows < 1
        or args.batches < 1
        or args.batch_rows * args.batches > 3 * args.faces
        or xyz.stat().st_size != args.vertices * 12
        or triangles.stat().st_size != args.faces * 12
        or output.exists()
    ):
        raise ValueError("invalid input dimensions, request size or existing output")
    spec = importlib.util.spec_from_file_location(
        "gather_column_snapshot", args.column_module
    )
    if spec is None or spec.loader is None:
        raise ValueError("column module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    column_type = cast(type[Column], cast(object, module.Column))
    # Both cases prehash the same complete inputs, warming uncontrolled caches.
    inputs = {"xyz": _sha256(xyz), "triangles": _sha256(triangles)}
    column = column_type(
        ColumnSpec("xyz.bin", args.vertices, 3, "<f4"),
        path=xyz,
        reopen=True,
        max_range_bytes=args.batch_rows * 12,
    )
    original = column.read_range
    reads = transferred = largest = 0

    def observed(start: int, stop: int) -> np.ndarray:
        nonlocal reads, transferred, largest
        size = (stop - start) * column.spec.stride
        reads += 1
        transferred += size
        largest = max(largest, size)
        return original(start, stop)

    # Deliberate dynamic instrumentation of a separately loaded source snapshot.
    # Timing includes this identical per-range accounting on both implementations.
    cast(Any, column).read_range = observed
    batches: list[dict[str, int | str]] = []
    before_io = process_io_snapshot()
    started = time.monotonic_ns()
    try:
        for index in range(args.batches):
            ids = np.fromfile(
                triangles,
                dtype="<i4",
                count=args.batch_rows,
                offset=index * args.batch_rows * 4,
            )
            if len(ids) != args.batch_rows:
                raise ValueError("incomplete triangle index batch")
            result = column.read_rows(ids)
            if result.shape != (len(ids), 3) or result.flags.writeable:
                raise ValueError("unexpected gather output contract")
            batches.append(
                {
                    "index": index,
                    "ids_sha256": hashlib.sha256(ids.tobytes()).hexdigest(),
                    "result_sha256": hashlib.sha256(result.tobytes()).hexdigest(),
                }
            )
            del ids, result
        elapsed = time.monotonic_ns() - started
        after_io = process_io_snapshot()
        memory = memory_snapshot()
    finally:
        column.close()
    report = {
        "scope": "Bounded gather diagnostic only; no full-import or RSS-budget claim. Sequential fresh processes, input prehash warms uncontrolled caches, elapsed time includes per-range accounting. Logical row bytes and process I/O counters are distinct from physical device reads.",
        "column_module_sha256": _sha256(args.column_module),
        "probe_sha256": _sha256(Path(__file__)),
        "python": sys.version,
        "numpy": np.__version__,
        "platform": platform.platform(),
        "inputs_sha256": inputs,
        "vertices": args.vertices,
        "faces": args.faces,
        "batch_rows": args.batch_rows,
        "batches": batches,
        "elapsed_ns": elapsed,
        "range_reads": reads,
        "logical_coordinate_bytes_read": transferred,
        "largest_range_bytes": largest,
        "requested_coordinate_bytes": args.batches * args.batch_rows * 12,
        "process_io_before": before_io,
        "process_io_after": after_io,
        "memory_after": memory,
    }
    with output.open("x", encoding="utf-8") as stream:
        _ = stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
