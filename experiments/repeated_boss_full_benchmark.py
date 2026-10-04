"""Compare the complete boss demo with deterministic resampled selections."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np

from experiments.feature_graph import (
    Feature,
    FeatureGraph,
    Recipe,
    Selection,
    Source,
    SurfaceFit,
    workspace_reference_sha256,
)
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION, THREAD_VARIABLES
from experiments.repeated_boss_fixture import publish_fixture
from scansor.serialization import canonical_json

RECIPE = Path(
    "examples/repeated-boss-selection/recipes/repeated-boss-fitting-benchmark.json"
)


def transfer_recipe(
    original: Recipe,
    baseline: Path,
    target: Path,
    workspace: NozzleWorkspace,
) -> Recipe:
    """Propagate saved membership over nominal-space nearest-vertex cells.

    This generated-fixture-only reconstruction approximates the painted footprint;
    it does not reconstruct a lost brush gesture or use truth to fit geometry.
    Plate faces and analytic occurrence/role patches cannot bleed into each other.
    """
    base_manifest = json.loads((baseline / "manifest.json").read_bytes())
    base_source = base_manifest["files"][base_manifest["source_file"]]["sha256"]
    for node in original.nodes:
        if isinstance(node, Source) and node.source_sha256 != base_source:
            raise ValueError("benchmark baseline does not match the saved recipe")

    def provenance(root: Path) -> tuple[np.ndarray, np.ndarray]:
        truth = root / "truth"
        positions = np.load(truth / "nominal-part.npy", allow_pickle=False)
        occurrence = np.load(truth / "occurrence-code.npy", allow_pickle=False)
        role = np.load(truth / "role-code.npy", allow_pickle=False)
        normals = np.load(truth / "normal-part.npy", allow_pickle=False)
        # Only the plate needs an extra key: its six patches share occurrence/role.
        dominant = np.argmax(np.abs(normals), axis=1)
        orientation = 2 * dominant + (normals[np.arange(len(normals)), dominant] > 0)
        face = np.where(occurrence == 0, orientation, -1)
        return positions, np.column_stack((occurrence, role, face))

    if baseline == target:
        nearest = np.arange(len(workspace.local))
    else:
        base_positions, base_keys = provenance(baseline)
        target_positions, target_keys = provenance(target)
        if len(target_positions) != len(workspace.local):
            raise ValueError("target provenance has another vertex count")
        selections = [node for node in original.nodes if isinstance(node, Selection)]
        selected_ids = sorted({vertex for node in selections for vertex in node.ids})
        keys = np.unique(base_keys[selected_ids], axis=0)
        nearest = np.full(len(target_positions), -1, dtype=np.int64)
        for key in keys:
            base_ids = np.flatnonzero(np.all(base_keys == key, axis=1))
            target_ids = np.flatnonzero(np.all(target_keys == key, axis=1))
            # Bounded temporary distance matrices; ascending IDs resolve ties.
            for start in range(0, len(target_ids), 512):
                ids = target_ids[start : start + 512]
                delta = target_positions[ids, None, :] - base_positions[base_ids]
                squared = np.sum(delta * delta, axis=2)
                nearest[ids] = base_ids[np.argmin(squared, axis=1)]

    nodes: list[Feature] = []
    for node in original.nodes:
        if isinstance(node, Source):
            node = node.model_copy(
                update={
                    "source_sha256": workspace.default.source_sha256,
                    "reference_sha256": workspace_reference_sha256(workspace),
                }
            )
        elif isinstance(node, Selection):
            ids = np.flatnonzero(
                np.isin(nearest, node.ids) & (workspace.data.weights > 0)
            ).tolist()
            node = node.model_copy(update={"ids": ids})
        nodes.append(node)
    # Enforce normal recipe limits, including the 25,000-vertex selection limit.
    return Recipe.model_validate(
        original.model_copy(update={"nodes": nodes}).model_dump()
    )


def measure_case(root: Path, scale: int) -> dict[str, Any]:
    phases: dict[str, dict[str, float]] = {}

    def measure(name: str, operation: Any) -> Any:
        wall, cpu = time.perf_counter(), time.process_time()
        value = operation()
        phases[name] = {
            "wall_seconds": time.perf_counter() - wall,
            "cpu_seconds": time.process_time() - cpu,
        }
        return value

    measure(
        "generate_baseline",
        lambda: publish_fixture(
            root / "baseline", DEFINITION, realization_ids=("scan-coarse",)
        ),
    )
    baseline = root / "baseline" / "scan-coarse"
    if scale == 1:
        target = baseline
    else:
        measure(
            "generate_target",
            lambda: publish_fixture(
                root / "target",
                DEFINITION,
                realization_ids=("scan-coarse",),
                tessellation_scale=scale,
            ),
        )
        target = root / "target" / "scan-coarse"
    workspace = measure("load_workspace", lambda: NozzleWorkspace(target))
    original = Recipe.model_validate_json(RECIPE.read_bytes())
    recipe = measure(
        "transfer_selections",
        lambda: transfer_recipe(original, baseline, target, workspace),
    )
    graph = measure("construct_graph", lambda: FeatureGraph(workspace, recipe))
    token = str(graph.snapshot()["token"])
    evaluation_error = None
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        state = graph.evaluate(token, all_actions=True)
    except ValueError as error:
        evaluation_error = str(error)
        state = graph.snapshot()
    phases["evaluate_all"] = {
        "wall_seconds": time.perf_counter() - wall,
        "cpu_seconds": time.process_time() - cpu,
    }
    encoded = measure(
        "serialize_response", lambda: json.dumps(state, allow_nan=False).encode()
    )
    results = state["results"]
    fit_counts = {
        node.label: len(results.get(node.id, {}).get("ids", []))
        for node in recipe.nodes
        if isinstance(node, SurfaceFit)
    }
    status = Path("/proc/self/status").read_text()
    # getrusage can inherit the launcher's pre-exec high-water mark on Linux.
    peak = (
        int(
            next(
                line.split()[1]
                for line in status.splitlines()
                if line.startswith("VmHWM:")
            )
        )
        * 1024
    )
    return {
        "scale": scale,
        "vertices": len(workspace.local),
        "triangles": len(workspace.data.triangles),
        "source_sha256": workspace.default.source_sha256,
        "resampled_recipe_sha256": hashlib.sha256(canonical_json(recipe)).hexdigest(),
        "response_sha256": hashlib.sha256(encoded).hexdigest(),
        "response_bytes": len(encoded),
        "action_count": len(recipe.nodes),
        "operations": dict(Counter(node.operation for node in recipe.nodes)),
        "states": dict(Counter(state["states"].values())),
        "errors": state["errors"],
        "evaluation_error": evaluation_error,
        "fit_vertices": fit_counts,
        "fit_vertex_entries": sum(fit_counts.values()),
        "selection_vertices": {
            node.label: len(node.ids)
            for node in recipe.nodes
            if isinstance(node, Selection)
        },
        "worker_peak_rss_bytes": peak,
        "phases": phases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--scales", nargs="+", type=int, default=[1, 2, 4, 8])
    _ = parser.add_argument("--repeats", type=int, default=3)
    _ = parser.add_argument("--threads", type=int, default=2)
    _ = parser.add_argument("--output", type=Path)
    _ = parser.add_argument("--worker-root", type=Path, help=argparse.SUPPRESS)
    _ = parser.add_argument("--worker-scale", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this benchmark uses Linux process memory counters")
    if args.worker_root is not None:
        if args.worker_scale is None or args.worker_scale < 1:
            parser.error("a positive worker scale is required")
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
    for repeat in range(args.repeats):
        for scale in args.scales:
            with tempfile.TemporaryDirectory(dir=temporary_parent) as directory:
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "experiments.repeated_boss_full_benchmark",
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
                sample["repeat"] = repeat + 1
                samples.append(sample)
                print(json.dumps(sample), flush=True)
    summaries = {}
    for scale in args.scales:
        rows = [sample for sample in samples if sample["scale"] == scale]
        summaries[str(scale)] = {
            "phase_wall_seconds_median": {
                phase: statistics.median(
                    row["phases"][phase]["wall_seconds"] for row in rows
                )
                for phase in rows[0]["phases"]
            },
            "worker_peak_rss_bytes_median": statistics.median(
                row["worker_peak_rss_bytes"] for row in rows
            ),
            "successful_evaluations": sum(
                row["evaluation_error"] is None for row in rows
            ),
        }
    report = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "scope": "Full boss fitting demo, fresh serial workers. Same saved action graph; only source bindings and explicit selection memberships change. Selection footprints are reconstructed by nearest ordinary nominal-space vertex within the same occurrence/role and plate face (Voronoi cells), using fixture truth only for benchmark preparation, never for fitting. Scale 1 retains exact saved IDs. Generation and selection transfer are timed separately; Evaluate all includes reuse matching, generated selection resolution, fitting, relationships, references, output transform, and returned snapshot. JSON serialization is separate. No face reconstruction, body assembly, browser/GPU, HTTP, export, cold-cache, or interpreter/import timing. Peak RSS covers the entire worker and comes from Linux VmHWM.",
        "platform": platform.platform(),
        "python": sys.version,
        "versions": {name: version(name) for name in ("numpy", "pydantic")},
        "thread_limits": {name: env[name] for name in THREAD_VARIABLES},
        "recipe_sha256": hashlib.sha256(RECIPE.read_bytes()).hexdigest(),
        "definition_sha256": hashlib.sha256(DEFINITION.read_bytes()).hexdigest(),
        "implementation_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                [*Path("src/scansor").rglob("*.py"), *Path("experiments").glob("*.py")]
            )
        },
        "samples": samples,
        "summaries": summaries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        _ = stream.write(canonical_json(report))


if __name__ == "__main__":
    main()
