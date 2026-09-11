"""Opt-in S6 grid expectation freeze; run as python -m experiments.mesh_scale_freeze.

This constructs expected hashes independently, before generating or benchmarking
the corresponding production source. It does not report a successful stress run.
Working files must be outside Git; the small output manifest may be retained.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from experiments.mesh_scale_grid import build_grid_expectation

_ROOT = Path(__file__).resolve().parents[1]
_SOURCES = (
    "experiments/mesh_scale_freeze.py",
    "experiments/mesh_scale_grid.py",
    "experiments/mesh_scale_integer.py",
    "experiments/mesh_scale_rounding.py",
    "tests/mesh_rounding_oracle.py",
    "tests/fixtures/mesh-numeric-goldens-v1.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(65_536):
            digest.update(data)
    return digest.hexdigest()


def _implementation() -> dict[str, str]:
    return {name: _sha256(_ROOT / name) for name in _SOURCES}


def _json(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _outside_git(workdir: Path) -> Path:
    root = workdir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("oracle workdir must be an existing directory")
    # A checkout has a Git directory containing HEAD or a worktree pointer file.
    # Some sandboxes install empty .git directories as discovery boundaries.
    if any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in (root, *root.parents)
    ):
        raise ValueError(
            "oracle working files must be outside Git, including ignored paths"
        )
    return root


def freeze_grid(
    workdir: Path,
    output: Path,
    *,
    width: int,
    height: int,
    noisy: bool,
    seed: int = 7,
    chunk_rows: int = 65_536,
) -> dict[str, object]:
    root = _outside_git(workdir)
    if output.exists():
        raise FileExistsError(output)
    implementation = _implementation()
    native = importlib.util.find_spec("numpy._core._multiarray_umath")
    if native is None or native.origin is None:
        raise RuntimeError("cannot identify NumPy's integer-array extension")
    runtime = {
        "python": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "python_executable_sha256": _sha256(Path(sys.executable).resolve()),
        "numpy": np.__version__,
        "numpy_array_extension_sha256": _sha256(Path(native.origin)),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "byteorder": sys.byteorder,
    }
    started = time.monotonic_ns()
    started_utc = datetime.now(UTC).isoformat()
    last_time, last_phase = 0, ""

    def progress(phase: str, done: int, total: int) -> None:
        nonlocal last_time, last_phase
        now = time.monotonic_ns()
        if phase != last_phase or done == total or now - last_time >= 5_000_000_000:
            print(
                json.dumps(
                    {
                        "phase": phase,
                        "done": done,
                        "total": total,
                        "elapsed_ns": now - started,
                    }
                ),
                flush=True,
            )
            last_time, last_phase = now, phase

    result: dict[str, object] = {
        "revision": "mesh-scale-grid-expectation-v1",
        "scope": "independent expected bytes only; no production scale measurement",
        "started_utc": started_utc,
        "implementation": implementation,
        "runtime": runtime,
        "recipe": {
            "width": width,
            "height": height,
            "seed": seed,
            "noise_bits": 3 if noisy else 0,
            "quantum_exponent": -8,
        },
        "execution": {"chunk_rows": chunk_rows, "workdir": str(root)},
    }
    try:
        expected = build_grid_expectation(
            width,
            height,
            root,
            seed=seed,
            noisy=noisy,
            chunk_rows=chunk_rows,
            progress=progress,
        )
        if _implementation() != implementation:
            raise RuntimeError(
                "oracle implementation changed while freezing expectations"
            )
        result.update(
            status="complete",
            expectation=asdict(expected),
            expectation_sha256=hashlib.sha256(_json(asdict(expected))).hexdigest(),
        )
    except BaseException as error:
        result.update(
            status="failed",
            error={"type": type(error).__name__, "message": str(error)[:2048]},
        )
        raise
    finally:
        result.update(
            completed_utc=datetime.now(UTC).isoformat(),
            elapsed_ns=time.monotonic_ns() - started,
        )
        # Exclusive creation preserves previous successful/failed evidence. A
        # killed write can leave invalid JSON, which consumers must reject.
        with output.open("xb") as stream:
            _ = stream.write(_json(result))
            stream.flush()
            os.fsync(stream.fileno())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--workdir", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--width", type=int, required=True)
    _ = parser.add_argument("--height", type=int, required=True)
    _ = parser.add_argument("--noisy", action="store_true")
    _ = parser.add_argument("--seed", type=int, default=7)
    _ = parser.add_argument("--chunk-rows", type=int, default=65_536)
    options = parser.parse_args()
    _ = freeze_grid(
        options.workdir,
        options.output,
        width=options.width,
        height=options.height,
        noisy=options.noisy,
        seed=options.seed,
        chunk_rows=options.chunk_rows,
    )


if __name__ == "__main__":
    main()
