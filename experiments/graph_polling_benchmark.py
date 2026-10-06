"""Measure full and unchanged graph polling without a running HTTP server.

Run with ``uv run python -m experiments.graph_polling_benchmark``. Measurements
exclude lock acquisition, fixture generation, evaluation, and network latency.
Synthetic previews isolate copying and JSON encoding from numerical/CAD work.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, cast

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE, transfer_recipe

NOZZLE = Path("examples/nozzle-bayonette-simplified")
NOZZLE_RECIPE = NOZZLE / "recipes/nozzle-selection-and-fitting-demo.json"


def measure_polling(graph: FeatureGraph, repeats: int) -> dict[str, Any]:
    """Time snapshots while already holding the graph's reentrant lock."""
    revision = cast(int, graph.snapshot()["revision"])
    measurements: dict[str, Any] = {}
    for name, cursor in (("full", None), ("unchanged", revision)):
        snapshot_seconds: list[float] = []
        json_seconds: list[float] = []
        response_bytes: list[int] = []
        for _ in range(repeats):
            with graph.lock:
                start = time.perf_counter()
                state = graph.snapshot(if_revision=cursor)
                snapshot_seconds.append(time.perf_counter() - start)
            # Match the adapter's JSON shape for a running evaluation job.
            response = {**state, "evaluation_running": True}
            start = time.perf_counter()
            encoded = json.dumps(response, allow_nan=False).encode()
            json_seconds.append(time.perf_counter() - start)
            response_bytes.append(len(encoded))
        if len(set(response_bytes)) != 1:
            raise ValueError("benchmark graph changed during polling")
        measurements[name] = {
            "lock_held_snapshot_median_ms": statistics.median(snapshot_seconds) * 1000,
            "json_encoding_median_ms": statistics.median(json_seconds) * 1000,
            "response_bytes": response_bytes[0],
        }
    return measurements


def measure_recipe(
    name: str, workspace: NozzleWorkspace, recipe: Recipe, repeats: int
) -> dict[str, Any]:
    graph = FeatureGraph(workspace, recipe)
    evaluation_error = None
    try:
        _ = graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    except ValueError as error:
        evaluation_error = str(error)
    state = cast(dict[str, Any], graph.snapshot())
    return {
        "name": name,
        "action_count": len(recipe.nodes),
        "evaluation_error": evaluation_error,
        "states": dict(Counter(state["states"].values())),
        "action_errors": state["errors"],
        **measure_polling(graph, repeats),
    }


def measure_synthetic(
    workspace: NozzleWorkspace, vertex_count: int, repeats: int
) -> dict[str, Any]:
    recipe = Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": "point",
                    "label": "Synthetic preview",
                    "operation": "point",
                    "initial_coordinates": [0, 0, 0],
                }
            ],
            "output": "point",
        }
    )
    graph = FeatureGraph(workspace, recipe)
    # This graph is disposable: inject flat geometry before any polling begins.
    with graph.lock:
        graph._derived["point"] = {  # pyright: ignore[reportPrivateUsage]
            "preview": {
                "positions": [
                    value for vertex in range(vertex_count) for value in (vertex, 0, 0)
                ],
                "indices": list(range(vertex_count - vertex_count % 3)),
            }
        }
    return {
        "name": f"synthetic_{vertex_count}_vertices",
        "vertex_count": vertex_count,
        **measure_polling(graph, repeats),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repeats", type=int, default=5)
    _ = parser.add_argument("--work-dir", type=Path)
    _ = parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    work_dir = args.work_dir or Path(os.environ.get("TMPDIR", "/tmp")) / "agents"
    _ = work_dir.mkdir(parents=True, exist_ok=True)
    nozzle_workspace = NozzleWorkspace(NOZZLE)
    cases = [
        measure_recipe(
            "retained_nozzle",
            nozzle_workspace,
            Recipe.model_validate_json(NOZZLE_RECIPE.read_bytes()),
            args.repeats,
        )
    ]
    with tempfile.TemporaryDirectory(
        prefix="graph-polling-", dir=work_dir
    ) as temporary:
        root = Path(temporary)
        _ = publish_fixture(
            root / "baseline", DEFINITION, realization_ids=("scan-coarse",)
        )
        baseline = root / "baseline" / "scan-coarse"
        boss_workspace = NozzleWorkspace(baseline)
        boss_recipe = transfer_recipe(
            Recipe.model_validate_json(RECIPE.read_bytes()),
            baseline,
            baseline,
            boss_workspace,
        )
        cases.append(
            measure_recipe("retained_boss", boss_workspace, boss_recipe, args.repeats)
        )
    cases.extend(
        measure_synthetic(nozzle_workspace, vertex_count, args.repeats)
        for vertex_count in (1000, 10_000, 100_000)
    )
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "repeats": args.repeats,
        "method": (
            "Median lock-held snapshot time excludes lock acquisition; JSON encoding "
            "is outside the lock. Responses include evaluation_running=true. Recipes "
            "are evaluated before measurement; the boss recipe uses a regenerated "
            "scan-coarse baseline and its saved selection memberships. Synthetic "
            "previews are injected into disposable graphs. No HTTP/network timings "
            "or timing thresholds are included."
        ),
        "cases": cases,
    }
    encoded = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
