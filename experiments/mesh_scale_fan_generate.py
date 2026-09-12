"""Prepare one native fan source and compare its complete independently frozen hash."""

from __future__ import annotations

import argparse
import os
import shutil
import time
from pathlib import Path
from typing import cast

from experiments.mesh_scale_check import file_hash, load_expectation
from experiments.mesh_scale_fan_source import FanRecipe, write_fan
from experiments.mesh_scale_measure import save_report
from experiments.mesh_scale_metrics import integer, object_record
from experiments.mesh_scale_run import implementation_record, outside_git
from scansor.mesh_controls import Control


def prepare_fan_source(
    frozen: Path, workdir: Path, name: str, output: Path, *, chunk_rows: int = 65536
) -> dict[str, Control]:
    root = outside_git(workdir)
    if (
        not root.is_dir()
        or not name
        or Path(name).name != name
        or name in (".", "..")
        or "\\" in name
    ):
        raise ValueError(
            "source requires an existing outside-Git directory and plain filename"
        )
    source = root / name
    if output.exists() or output.resolve() in (source, frozen.resolve()):
        raise FileExistsError("report must be new and distinct from source/expectation")
    expected, frozen_sha = load_expectation(frozen)
    recipe = FanRecipe(
        cast(int, expected.get("radius")), cast(bool, expected.get("adverse"))
    )
    if (
        expected.get("vertices") != recipe.vertex_count
        or expected.get("faces") != recipe.face_count
    ):
        raise ValueError("frozen fan population differs from the recipe")
    identity, started = implementation_record(), time.monotonic_ns()
    result: dict[str, Control] = {
        "revision": "mesh-scale-fan-source-preparation-v1",
        "scope": "Complete native source compared to independent expected bytes; no measured import.",
        "source": str(source),
        "recipe": {"radius": recipe.radius, "adverse": recipe.adverse},
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
            raise OSError("insufficient disk for the complete fan source")
        with source.open("xb") as stream:
            write_fan(stream, recipe, chunk_rows=chunk_rows)
            stream.flush()
            os.fsync(stream.fileno())
        result["actual"] = file_hash(source)
        if result["actual"] != expected["source"]:
            raise ValueError(
                "complete native fan source differs from frozen expectation"
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
    for name in ("frozen", "workdir", "output"):
        _ = parser.add_argument("--" + name, type=Path, required=True)
    _ = parser.add_argument("--name", required=True)
    _ = parser.add_argument("--chunk-rows", type=int, default=65536)
    args = parser.parse_args()
    _ = prepare_fan_source(
        args.frozen, args.workdir, args.name, args.output, chunk_rows=args.chunk_rows
    )


if __name__ == "__main__":
    main()
