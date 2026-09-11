"""Run the mesh numeric gate in fresh default/baseline NumPy processes.

Run with the locked interpreter, e.g.:
  uv run --locked python -m experiments.mesh_numeric_conformance --output DIR

Evidence is execution provenance. Its host/settings never feed semantic IDs.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _cpu() -> dict[str, Any]:
    # NumPy exposes these as implementation diagnostics, not a stable public API.
    # On upgrades inspect its SIMD/dispatch changelog and verify names/effects;
    # missing diagnostics fail this gate rather than pretending baseline ran.
    module = importlib.import_module("numpy._core._multiarray_umath")
    return {
        "baseline": list(module.__cpu_baseline__),
        "dispatch": list(module.__cpu_dispatch__),
        "features": dict(module.__cpu_features__),
    }


def _worker(mode: str, output: Path) -> int:
    import numpy as np
    import pytest

    from scansor.mesh_controls import control_id, implementation_inventory
    from scansor.mesh_numeric import check_arithmetic
    from tests.test_mesh_recipes import GOLDENS, golden_artifacts

    before = _cpu()
    disabled = os.environ.get("NPY_DISABLE_CPU_FEATURES", "")
    requested = disabled.split(",") if disabled else []
    if mode == "default" and requested:
        raise RuntimeError("default worker unexpectedly has disabled CPU features")
    if mode == "baseline" and any(
        before["features"].get(name, True) for name in requested
    ):
        raise RuntimeError("requested dispatch features are still enabled")
    if mode == "baseline" and any(
        before["features"].get(name, False) for name in before["dispatch"]
    ):
        raise RuntimeError("baseline worker still has an enabled dispatch target")
    check_arithmetic()
    result = pytest.main(
        [
            "tests/test_mesh_ply.py",
            "tests/test_mesh_numeric.py",
            "tests/test_mesh_recipes.py",
            "tests/test_mesh_accumulation.py",
            "-q",
            "-W",
            "error",
        ]
    )
    if result != 0:
        return int(result)
    if before != _cpu():
        raise RuntimeError("CPU dispatch changed during the conformance run")
    diagnostics = io.StringIO()
    configuration = np.show_config(mode="dicts")
    with contextlib.redirect_stdout(diagnostics):
        np.show_runtime()
    golden = json.loads(GOLDENS.read_text(encoding="ascii"))
    actual = {
        name: hashlib.sha256(data).hexdigest()
        for name, data in golden_artifacts().items()
    }
    if actual != golden["right_triangle_artifacts"]:
        raise RuntimeError("golden artifacts changed after tests")
    report = {
        "revision": "mesh-numeric-conformance-v1",
        "scope": "S1 I/O, S2 primitives and S4 ordered accumulation/digests; full imports and resource gates are separate",
        "status": "passed",
        "commit": os.environ.get("GITHUB_SHA"),
        "mode": mode,
        "python": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "dependencies": {
            dist.metadata["Name"]: dist.version
            for dist in importlib.metadata.distributions()
        },
        "cpu": before,
        "disabled_dispatch": requested,
        "numpy_build": configuration,
        "numpy_runtime": diagnostics.getvalue(),
        "right_triangle_artifacts": actual,
        "small_recipe_hashes": {
            name: {
                "source_sha256": row["source_sha256"],
                "control_sha256": row["control_sha256"],
            }
            for name, row in golden["small_recipes"].items()
        },
        "philox_vectors": golden["philox_vectors"],
        "implementation_ids": {
            owner: control_id(implementation_inventory(owner))
            for owner in ("generator", "importer", "policy")
        },
        "chunks": [1, 2, 7, 127],
        "byte_orders": ["little", "byte-swapped"],
    }
    _ = output.write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    return 0


def compare_matrix(root: Path) -> None:
    """Require every expected OS/interpreter/dispatch report and identical IDs."""
    reference: dict[str, Any] | None = None
    compared: list[str] = []
    for system, arch in (
        ("Linux", "x86_64"),
        ("Windows", "AMD64"),
        ("Darwin", "arm64"),
    ):
        for version in ("3.12.13", "3.13.15"):
            directory = root / f"mesh-conformance-{system}-{version}"
            for mode in ("default", "baseline"):
                report: dict[str, Any] = json.loads(
                    (directory / f"{mode}.json").read_text(encoding="ascii")
                )
                if (
                    report["status"] != "passed"
                    or report["system"] != system
                    or report["machine"] != arch
                    or report["python"].split()[0] != version
                    or report["python_implementation"] != "CPython"
                    or report["mode"] != mode
                ):
                    raise RuntimeError(
                        f"incorrect conformance matrix entry: {directory.name}/{mode}"
                    )
                if reference is None:
                    reference = report
                for field in (
                    "right_triangle_artifacts",
                    "small_recipe_hashes",
                    "philox_vectors",
                    "implementation_ids",
                ):
                    if reference[field] != report[field]:
                        raise RuntimeError(
                            f"cross-platform mismatch in {field}: {directory.name}/{mode}"
                        )
                compared.append(f"{system}/{version}/{mode}")
    print(json.dumps({"status": "passed", "compared": compared}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--worker", choices=("default", "baseline"))
    _ = parser.add_argument("--describe", action="store_true")
    _ = parser.add_argument("--compare-matrix", action="store_true")
    _ = parser.add_argument("--expected-os")
    _ = parser.add_argument("--expected-arch", choices=("x86_64", "arm64"))
    args = parser.parse_args()
    if args.compare_matrix:
        compare_matrix(args.output)
        return 0
    if args.expected_os and platform.system() != args.expected_os:
        raise RuntimeError(f"wrong runner OS: {platform.system()}")
    architecture = {"amd64": "x86_64", "aarch64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    if args.expected_arch and architecture != args.expected_arch:
        raise RuntimeError(f"wrong runner architecture: {platform.machine()}")
    if args.describe:
        print(json.dumps(_cpu()))
        return 0
    if args.worker:
        return _worker(args.worker, args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    _ = environment.pop("NPY_DISABLE_CPU_FEATURES", None)
    _ = environment.pop("NPY_ENABLE_CPU_FEATURES", None)
    command = [sys.executable, "-m", "experiments.mesh_numeric_conformance"]
    described = subprocess.run(
        [*command, "--describe", "--output", str(args.output)],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    cpu = json.loads(described.stdout)
    available = [name for name in cpu["dispatch"] if cpu["features"].get(name, False)]
    reports: list[dict[str, Any]] = []
    # Sequential: different dispatch settings must not share an imported NumPy.
    for mode in ("default", "baseline"):
        if mode == "baseline":
            environment["NPY_DISABLE_CPU_FEATURES"] = ",".join(available)
        path = args.output / f"{mode}.json"
        _ = subprocess.run(
            [*command, "--worker", mode, "--output", str(path)],
            env=environment,
            check=True,
        )
        reports.append(json.loads(path.read_text(encoding="ascii")))
    for name in (
        "right_triangle_artifacts",
        "small_recipe_hashes",
        "philox_vectors",
        "implementation_ids",
    ):
        if reports[0][name] != reports[1][name]:
            raise RuntimeError(f"default/baseline disagreement: {name}")
    summary = {
        "status": "passed",
        "baseline_dispatch_disabled": available,
        "baseline_was_already_effective": not available,
        "reports": ["default.json", "baseline.json"],
    }
    _ = (args.output / "summary.json").write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n",
        encoding="ascii",
        newline="\n",
    )
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
