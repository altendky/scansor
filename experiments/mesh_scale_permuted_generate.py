"""Reorder a verified native grid source; compare every output byte to its oracle.

Preparation maps the existing PLY read-only and copies at most 65,536 records
per gather. Mapping/page-cache RSS is not bounded by the later import budget:
this is fixture construction, separate from all resource measurements. No
expectation implementation supplies source coordinates, topology or permutations.
"""

from __future__ import annotations

import argparse
import math
import mmap
import os
import shutil
import time
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

import numpy as np

from experiments.mesh_scale_check import file_hash, load_expectation
from experiments.mesh_scale_measure import save_report
from experiments.mesh_scale_metrics import integer, object_record
from experiments.mesh_scale_run import implementation_record, outside_git
from scansor._plyio import Layout, Writer
from scansor.mesh_artifact_io import ReadDirectory, ReadFile
from scansor.mesh_controls import Control
from scansor.mesh_ply import MeshPlyReader, mesh_header, mesh_layout


def _parameters(expected: dict[str, Control], name: str, count: int) -> tuple[int, int]:
    multiplier, offset = (
        integer(expected[name + "_multiplier"]),
        integer(expected[name + "_offset"]),
    )
    if (
        not 2 <= count <= 120_000_000
        or not 1 <= multiplier < 2**31
        or not 0 <= offset < count
        or math.gcd(multiplier, count) != 1
    ):
        raise ValueError("source permutation must be a bounded bijection")
    return multiplier, offset


def _write_mapped(
    stream: BinaryIO,
    mapped: mmap.mmap,
    source_layout: Layout,
    expected: dict[str, Control],
    chunk_rows: int,
) -> None:
    vertices, faces = integer(expected["vertices"]), integer(expected["faces"])
    vertex_a, vertex_b = _parameters(expected, "vertex", vertices)
    face_a, face_b = _parameters(expected, "face", faces)
    inverse_vertex = pow(vertex_a, -1, vertices)
    target = mesh_layout(
        mesh_header(vertices, faces, comments=("scansor-mesh-permuted-grid-v1",))
    )
    writer = Writer(stream, target, max_range_bytes=13 * chunk_rows)
    for name, count, multiplier, offset in (
        ("vertex", vertices, vertex_a, vertex_b),
        ("face", faces, face_a, face_b),
    ):
        layout = source_layout.element(name)
        if layout.element.count != count or layout.dtype != target.element(name).dtype:
            raise ValueError("baseline PLY layout differs from the frozen grid")
        view = np.ndarray(
            (count,), dtype=layout.dtype, buffer=mapped, offset=layout.offset
        )
        try:
            for start in range(0, count, chunk_rows):
                stop = min(start + chunk_rows, count)
                original = (
                    np.arange(start, stop, dtype="<i8") * multiplier + offset
                ) % count
                # Advanced indexing creates an owned bounded copy. Preserve XYZ
                # words exactly; only source row order and face references change.
                rows = view[original]
                if name == "face":
                    indices = rows["vertex_indices"]["values"].astype("<i8")
                    if np.any(indices < 0) or np.any(indices >= vertices):
                        raise ValueError("baseline grid contains an invalid reference")
                    rows["vertex_indices"]["values"] = (
                        ((indices - vertex_b) % vertices) * inverse_vertex % vertices
                    ).astype("<i4")
                writer.write_range(name, start, rows)
                del original, rows
        finally:
            # Release even on exceptions before the outer mmap context closes.
            del view
    writer.finish()


def prepare_permuted_source(
    frozen: Path,
    baseline_frozen: Path,
    baseline_source: Path,
    workdir: Path,
    name: str,
    output: Path,
    *,
    chunk_rows: int = 65536,
) -> dict[str, Control]:
    root = outside_git(workdir)
    baseline_source = outside_git(baseline_source)
    if (
        not root.is_dir()
        or not name
        or Path(name).name != name
        or name in (".", "..")
        or "\\" in name
        or type(chunk_rows) is not int
        or not 1 <= chunk_rows <= 65536
    ):
        raise ValueError(
            "requires an outside-Git directory, plain filename and bounded batch"
        )
    source = root / name
    if output.exists() or output.resolve() in (
        source,
        baseline_source,
        frozen.resolve(),
        baseline_frozen.resolve(),
    ):
        raise FileExistsError("report must be new and distinct from all inputs/output")
    expected, frozen_sha = load_expectation(frozen)
    baseline, baseline_sha = load_expectation(baseline_frozen)
    for key in ("width", "height", "seed", "noisy", "vertices", "faces"):
        if key not in expected or expected[key] != baseline.get(key):
            raise ValueError("baseline and permutation must describe the same grid")
    if "radius" in baseline or "vertex_multiplier" in baseline:
        raise ValueError("baseline must be an unpermuted grid")
    for element, population in (("vertex", "vertices"), ("face", "faces")):
        _ = _parameters(expected, element, integer(expected[population]))
    identity, started = implementation_record(), time.monotonic_ns()
    result: dict[str, Control] = {
        "revision": "mesh-scale-permuted-grid-source-preparation-v1",
        "scope": "Complete native PLY reorder with read-only source mapping and bounded gathers; preparation RSS is not an import benchmark.",
        "source": str(source),
        "baseline_source": str(baseline_source),
        "baseline_frozen_sha256": baseline_sha,
        "frozen_manifest_sha256": frozen_sha,
        "implementation": identity,
        "chunk_rows": chunk_rows,
        "expected": expected["source"],
    }
    try:
        if (
            shutil.disk_usage(root).free
            < integer(object_record(expected["source"])["bytes"]) + 65536
        ):
            raise OSError("insufficient disk for the complete permuted source")
        # Create the output before anchoring the baseline directory: when they
        # share a parent, the creation must not invalidate that fixed entry set.
        with (
            source.open("xb") as stream,
            closing(ReadDirectory(baseline_source.parent)) as directory,
            closing(ReadFile(directory, baseline_source.name)) as held,
        ):
            actual: dict[str, Control] = {
                "bytes": held.size,
                "sha256": held.sha256(phase="verify-baseline-before-reorder"),
            }
            result["baseline_actual"] = actual
            if actual != baseline["source"]:
                raise ValueError(
                    "baseline source differs from its complete frozen hash"
                )
            _ = held.stream.seek(0)
            reader = MeshPlyReader(held.stream, max_range_bytes=13 * chunk_rows)
            with mmap.mmap(held.stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                _write_mapped(stream, mapped, reader.layout, expected, chunk_rows)
            stream.flush()
            os.fsync(stream.fileno())
            if (
                held.sha256(phase="verify-baseline-after-reorder")
                != object_record(baseline["source"])["sha256"]
            ):
                raise ValueError(
                    "baseline changed while producing the reordered source"
                )
            held.check()
        result["actual"] = file_hash(source)
        if result["actual"] != expected["source"]:
            raise ValueError(
                "complete native reordered source differs from frozen expectation"
            )
        if implementation_record() != identity:
            raise RuntimeError("source preparation implementation changed")
        result["status"] = "verified-source"
    except BaseException as error:
        result.update(
            status="failed",
            error={"type": type(error).__name__, "message": str(error)[:2048]},
        )
        raise
    finally:
        result["elapsed_ns"] = time.monotonic_ns() - started
        save_report(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("frozen", "baseline-frozen", "baseline-source", "workdir", "output"):
        _ = parser.add_argument("--" + name, type=Path, required=True)
    _ = parser.add_argument("--name", required=True)
    _ = parser.add_argument("--chunk-rows", type=int, default=65536)
    args = parser.parse_args()
    _ = prepare_permuted_source(
        args.frozen,
        args.baseline_frozen,
        args.baseline_source,
        args.workdir,
        args.name,
        args.output,
        chunk_rows=args.chunk_rows,
    )


if __name__ == "__main__":
    main()
