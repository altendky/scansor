"""Generate one complete production grid and check its independently frozen hash.

Run sequentially as python -m experiments.mesh_scale_generate. Source files must
be outside Git. This is source preparation, not an importer/resource benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

from scansor.mesh_controls import (
    Control,
    encode_control,
    implementation_inventory,
)
from scansor.mesh_recipes import GridRecipe, generate_file, generation_request


def _object(value: Control) -> dict[str, Control]:
    if not isinstance(value, dict):
        raise ValueError("expected an object in the frozen grid manifest")
    return value


def _load(path: Path) -> tuple[GridRecipe, dict[str, Control], str]:
    with path.open("rb") as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        raise ValueError("frozen grid manifest exceeds 128 KiB")
    record = _object(json.loads(raw))
    _ = encode_control(record)  # Validate the float-free value/depth boundary.
    # The independent oracle uses compact JSON, not the runtime control format.
    # Exact re-encoding also rejects duplicate keys accepted by json.loads.
    if (
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8") != raw:
        raise ValueError("frozen grid manifest is not its canonical compact JSON")
    if (
        record.get("revision") != "mesh-scale-grid-expectation-v1"
        or record.get("status") != "complete"
    ):
        raise ValueError("a complete independently frozen grid manifest is required")
    expected = _object(record.get("expectation"))
    expected_bytes = (
        json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")
    if hashlib.sha256(expected_bytes).hexdigest() != record.get("expectation_sha256"):
        raise ValueError("frozen expectation payload digest differs")
    width, height, seed, noisy = (
        expected.get(name) for name in ("width", "height", "seed", "noisy")
    )
    if (
        type(width) is not int
        or not 2 <= width <= 10000
        or type(height) is not int
        or not 2 <= height <= 6000
        or type(seed) is not int
        or type(noisy) is not bool
    ):
        raise ValueError("invalid frozen grid recipe")
    recipe = GridRecipe(width, height, seed=seed, noise_bits=3 if noisy else 0)
    if (
        expected.get("vertices") != recipe.vertex_count
        or expected.get("faces") != recipe.face_count
        or record.get("recipe")
        != {
            "width": width,
            "height": height,
            "seed": seed,
            "noise_bits": recipe.noise_bits,
            "quantum_exponent": -8,
        }
    ):
        raise ValueError("inconsistent frozen grid populations or recipe")
    source = _object(expected.get("source"))
    digest, size = source.get("sha256"), source.get("bytes")
    if (
        type(size) is not int
        or size <= 0
        or type(digest) is not str
        or len(digest) != 64
        or any(letter not in "0123456789abcdef" for letter in digest)
    ):
        raise ValueError("invalid frozen source hash or size")
    return recipe, source, hashlib.sha256(raw).hexdigest()


def prepare_source(
    frozen: Path,
    workdir: Path,
    name: str,
    output: Path,
    *,
    chunk_rows: int = 65536,
) -> dict[str, Control]:
    recipe, expected, frozen_hash = _load(frozen)
    if output.exists() or output.resolve() == (workdir / name).resolve():
        raise ValueError(
            "generation report requires a new path distinct from the source"
        )
    implementation = implementation_inventory("generator")
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    started = time.monotonic_ns()
    report: dict[str, Control] = {
        "revision": "mesh-scale-source-preparation-v1",
        "scope": "complete production source compared to independent expected bytes; no importer/resource measurement",
        "frozen_manifest_sha256": frozen_hash,
        "request": generation_request(recipe),
        "generator_implementation": implementation,
        "preparation_script_sha256": script_hash,
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "chunk_rows": chunk_rows,
        "source": str((workdir / name).absolute()),
        "expected": expected,
    }
    try:
        if shutil.disk_usage(workdir).free < int(str(expected["bytes"])) + 65536:
            raise OSError("available disk cannot hold the complete expected source")
        source = generate_file(workdir, name, recipe, chunk_rows=chunk_rows)
        digest, count = hashlib.sha256(), 0
        with source.open("rb") as stream:
            while data := stream.read(65536):
                digest.update(data)
                count += len(data)
        actual: dict[str, Control] = {"bytes": count, "sha256": digest.hexdigest()}
        report["actual"] = actual
        if actual != expected:
            raise ValueError(
                "complete production source differs from frozen expectation"
            )
        if (
            implementation_inventory("generator") != implementation
            or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != script_hash
        ):
            raise RuntimeError(
                "source preparation implementation changed during generation"
            )
        report["status"] = "verified-source"
    except BaseException as error:
        report.update(
            status="failed",
            error={"type": type(error).__name__, "message": str(error)[:2048]},
        )
        raise
    finally:
        report["elapsed_ns"] = time.monotonic_ns() - started
        with output.open("xb") as stream:
            _ = stream.write(encode_control(report))
            stream.flush()
            os.fsync(stream.fileno())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--frozen", type=Path, required=True)
    _ = parser.add_argument("--workdir", type=Path, required=True)
    _ = parser.add_argument("--name", required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--chunk-rows", type=int, default=65536)
    options = parser.parse_args()
    result = prepare_source(
        options.frozen,
        options.workdir,
        options.name,
        options.output,
        chunk_rows=options.chunk_rows,
    )
    print(
        json.dumps(
            {key: result[key] for key in ("status", "source", "actual", "elapsed_ns")}
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
