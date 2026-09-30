"""Sequential, fresh-process timings for repeated-boss generation and fitting."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from scansor.serialization import canonical_json

DEFINITION = Path("examples/repeated-boss-selection/fixture.json")
NOZZLE = Path("examples/nozzle-bayonette-simplified")
THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def measure_case(root: Path, scale: int) -> dict[str, Any]:
    # Imports are outside phase timers; the parent measures complete worker time.
    from experiments.nozzle_session import NozzleWorkspace
    from experiments.repeated_boss_fixture import publish_fixture

    phases: dict[str, dict[str, float]] = {}

    def stamp() -> tuple[float, float]:
        return time.perf_counter(), time.process_time()

    def finish(name: str, started: tuple[float, float]) -> None:
        phases[name] = {
            "wall_seconds": time.perf_counter() - started[0],
            "cpu_seconds": time.process_time() - started[1],
        }

    if scale:
        started = stamp()
        manifest = publish_fixture(
            root / "fixture",
            DEFINITION,
            realization_ids=("scan-coarse",),
            tessellation_scale=scale,
        )
        finish("generate_and_publish", started)
        example = root / "fixture" / "scan-coarse"
        generated_bytes = sum(item["bytes"] for item in manifest["artifacts"].values())
    else:
        example = NOZZLE
        generated_bytes = None

    started = stamp()
    workspace = NozzleWorkspace(example)
    finish("load_workspace", started)
    started = stamp()
    try:
        result = workspace.fit(workspace.default, "cylinder")
        fit_error = None
    except ValueError as error:
        result = None
        fit_error = str(error)
    finish("fit_cylinder_plane", started)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "case": "nozzle-simplified" if scale == 0 else f"boss-{scale}x",
        "tessellation_scale": scale or None,
        "vertices": len(workspace.local),
        "triangles": len(workspace.data.triangles),
        "lateral_selected_vertices": len(workspace.default.lateral_ids),
        "plane_selected_vertices": len(workspace.default.plane_ids),
        "source_sha256": workspace.default.source_sha256,
        "generated_artifact_bytes": generated_bytes,
        "phases": phases,
        "fit_iterations": (
            None if result is None else len(result["fit"]["objective_history"])
        ),
        "weighted_rms": None if result is None else result["fit"]["weighted_rms"],
        "fit_error": fit_error,
        "worker_peak_rss_bytes": peak * (1 if sys.platform == "darwin" else 1024),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--scales", type=int, nargs="+", default=[1, 2, 4, 8])
    _ = parser.add_argument("--repeats", type=int, default=3)
    _ = parser.add_argument("--threads", type=int, default=2)
    _ = parser.add_argument("--output", type=Path)
    _ = parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    _ = parser.add_argument("--worker-scale", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_root is not None:
        if args.worker_scale is None:
            parser.error("worker scale required")
        print(json.dumps(measure_case(args.worker_root, args.worker_scale)))
        return
    if args.output is None:
        parser.error("--output is required")
    if args.repeats < 1 or args.threads < 1 or any(scale < 1 for scale in args.scales):
        parser.error("scales, repeats, and threads must be positive")
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")

    env = os.environ | {name: str(args.threads) for name in THREAD_VARIABLES}
    temporary_parent = Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
    temporary_parent.mkdir(parents=True, exist_ok=True)
    samples: list[dict[str, Any]] = []
    # Interleave sizes between repetitions; only one measured worker runs at once.
    for repeat in range(args.repeats):
        for scale in [0, *args.scales]:
            with tempfile.TemporaryDirectory(dir=temporary_parent) as directory:
                started = time.perf_counter()
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "experiments.repeated_boss_benchmark",
                        "--worker-root",
                        directory,
                        "--worker-scale",
                        str(scale),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(completed.stderr)
                sample = json.loads(completed.stdout)
                sample["worker_wall_seconds"] = time.perf_counter() - started
                sample["repeat"] = repeat + 1
                samples.append(sample)
                print(json.dumps(sample), flush=True)

    summaries: dict[str, object] = {}
    for case in dict.fromkeys(str(sample["case"]) for sample in samples):
        rows = [sample for sample in samples if sample["case"] == case]
        phases = rows[0]["phases"]
        summaries[case] = {
            "phase_wall_seconds_median": {
                phase: statistics.median(
                    row["phases"][phase]["wall_seconds"] for row in rows
                )
                for phase in phases
            },
            "worker_peak_rss_bytes_median": statistics.median(
                row["worker_peak_rss_bytes"] for row in rows
            ),
            "successful_fits": sum(row["fit_error"] is None for row in rows),
            "fit_errors": [row["fit_error"] for row in rows if row["fit_error"]],
        }
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "scope": (
            "Fresh serial workers; phase times exclude interpreter/import startup. "
            "Generation includes PLY, truth, selections, and hashes. Loading includes "
            "source verification, mesh areas/weights, gate replay, and workspace frame. "
            "Fit includes cylinder initialization, joint cylinder/plane solve, and "
            "result construction on the saved selections. Peak RSS covers the whole "
            "worker, including generation. No browser/GPU or cold-cache measurement; "
            "loading newly generated files benefits from filesystem cache."
        ),
        "platform": platform.platform(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "thread_limits": {name: env[name] for name in THREAD_VARIABLES},
        "versions": {name: version(name) for name in ("numpy", "pydantic")},
        "implementation_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [*Path("src/scansor").rglob("*.py"), *Path("experiments").glob("*.py")]
            )
        },
        "definition_sha256": hashlib.sha256(DEFINITION.read_bytes()).hexdigest(),
        "samples": samples,
        "summaries": summaries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        _ = stream.write(canonical_json(report))


if __name__ == "__main__":
    main()
