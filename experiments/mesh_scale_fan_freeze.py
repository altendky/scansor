"""Opt-in independent fan expectation freeze, before source generation or timing."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import platform
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from experiments.mesh_scale_fan import build_fan_expectation
from experiments.mesh_scale_freeze import (
    _json,  # pyright: ignore[reportPrivateUsage]
    _outside_git,  # pyright: ignore[reportPrivateUsage]
    _sha256,  # pyright: ignore[reportPrivateUsage]
)

_ROOT = Path(__file__).resolve().parents[1]
_SOURCES = (
    "experiments/mesh_scale_fan.py",
    "experiments/mesh_scale_fan_freeze.py",
    "experiments/mesh_scale_freeze.py",
    "experiments/mesh_scale_grid.py",
    "experiments/mesh_scale_integer.py",
    "experiments/mesh_scale_rounding.py",
    "tests/mesh_rounding_oracle.py",
    "tests/fixtures/mesh-numeric-goldens-v1.json",
)


def freeze_fan(
    workdir: Path,
    output: Path,
    *,
    radius: int = 262144,
    adverse: bool = False,
    chunk_rows: int = 65536,
) -> dict[str, object]:
    root = _outside_git(workdir)
    if output.exists():
        raise FileExistsError(output)
    implementation = {name: _sha256(_ROOT / name) for name in _SOURCES}
    native = importlib.util.find_spec("numpy._core._multiarray_umath")
    if native is None or native.origin is None:
        raise RuntimeError("cannot identify NumPy's integer-array extension")
    result: dict[str, object] = {
        "revision": "mesh-scale-fan-expectation-v1",
        "scope": "Independent complete expected bytes; source construction and resource measurements are separate.",
        "started_utc": datetime.now(UTC).isoformat(),
        "recipe": {"radius": radius, "adverse": adverse},
        "implementation": implementation,
        "runtime": {
            "python": sys.version,
            "python_executable": str(Path(sys.executable).resolve()),
            "python_executable_sha256": _sha256(Path(sys.executable).resolve()),
            "numpy": np.__version__,
            "numpy_array_extension_sha256": _sha256(Path(native.origin)),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "byteorder": sys.byteorder,
        },
        "execution": {"workdir": str(root), "chunk_rows": chunk_rows},
    }
    started, last = time.monotonic_ns(), 0

    def progress(phase: str, done: int, total: int) -> None:
        nonlocal last
        now = time.monotonic_ns()
        if done == total or now - last >= 5_000_000_000:
            print(
                _json(
                    {
                        "phase": phase,
                        "done": done,
                        "total": total,
                        "elapsed_ns": now - started,
                    }
                )
                .decode()
                .strip(),
                flush=True,
            )
            last = now

    try:
        expected = asdict(
            build_fan_expectation(
                radius, adverse=adverse, chunk_rows=chunk_rows, progress=progress
            )
        )
        if {name: _sha256(_ROOT / name) for name in _SOURCES} != implementation:
            raise RuntimeError("fan oracle changed during expectation freeze")
        result.update(
            status="complete",
            expectation=expected,
            expectation_sha256=hashlib.sha256(_json(expected)).hexdigest(),
        )
    except BaseException as error:
        result.update(
            status="failed",
            error={"type": type(error).__name__, "message": str(error)[:2048]},
        )
        raise
    finally:
        result["elapsed_ns"] = time.monotonic_ns() - started
        with output.open("xb") as stream:
            _ = stream.write(_json(result))
            stream.flush()
            os.fsync(stream.fileno())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--workdir", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--radius", type=int, default=262144)
    _ = parser.add_argument("--adverse", action="store_true")
    _ = parser.add_argument("--chunk-rows", type=int, default=65536)
    args = parser.parse_args()
    _ = freeze_fan(
        args.workdir,
        args.output,
        radius=args.radius,
        adverse=args.adverse,
        chunk_rows=args.chunk_rows,
    )


if __name__ == "__main__":
    main()
