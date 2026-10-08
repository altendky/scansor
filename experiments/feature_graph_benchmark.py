"""Measure fresh graph evaluation, its publication, and HTTP JSON encoding.

Unlike the fitting-only scaling benchmark, this runs every action in a supplied
recipe, including retained face construction, validation and body assembly.
Module imports are warmed; every measured graph and native replay cache is new.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import statistics
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace


def measure(workspace_path: Path, recipe_path: Path, runs: int) -> dict[str, Any]:
    if runs < 1:
        raise ValueError("runs must be positive")
    payload = json.loads(recipe_path.read_bytes())
    recipe = Recipe.model_validate(payload.get("recipe", payload))
    started = perf_counter()
    workspace = NozzleWorkspace(workspace_path)
    load_seconds = perf_counter() - started
    # Separate lazy imports from repeated graph work, consistently across runs.
    started = perf_counter()
    for module in (
        "face_arrangement",
        "face_proposals",
        "shared_face_boundaries",
        "body_geometry",
        "general_face_geometry",
        "surface_primitives",
        "fit_relationship_component",
        "fit_solver",
        "nozzle_coaxial",
        "selection_growth",
        "selection_region",
        "feature_reuse",
        "surface_extents",
        "feature_inputs",
    ):
        _ = importlib.import_module("experiments." + module)
    import_seconds = perf_counter() - started
    samples: list[dict[str, Any]] = []
    for _ in range(runs):
        started = perf_counter()
        graph = FeatureGraph(workspace, recipe.model_copy(deep=True))
        initial = graph.snapshot()
        setup_seconds = perf_counter() - started
        assert set(cast(dict[str, str], initial["states"]).values()) == {"unevaluated"}
        started = perf_counter()
        state = graph.evaluate(str(initial["token"]), all_actions=True)
        evaluation_seconds = perf_counter() - started
        states = cast(dict[str, str], state["states"])
        started = perf_counter()
        encoded = json.dumps(
            {**state, "evaluation_running": False}, allow_nan=False
        ).encode()
        encoding_seconds = perf_counter() - started
        sample = {
            "setup_seconds": setup_seconds,
            "evaluate_and_snapshot_seconds": evaluation_seconds,
            "response_encode_seconds": encoding_seconds,
            "total_seconds": setup_seconds + evaluation_seconds + encoding_seconds,
            "response_bytes": len(encoded),
            "states": dict(Counter(states.values())),
            "errors": state["errors"],
        }
        samples.append(sample)
        if state["errors"] or set(states.values()) != {"ready"}:
            raise RuntimeError(f"full evaluation failed: {sample}")
    return {
        "workspace": str(workspace_path),
        "recipe": str(recipe_path),
        "features": len(recipe.nodes),
        "vertices": len(workspace.local),
        "workspace_load_seconds": load_seconds,
        "domain_import_seconds": import_seconds,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "dependencies": {
                name: version(name)
                for name in ("numpy", "pydantic", "cadquery-ocp-novtk")
            },
            "threads": {
                name: os.environ.get(name)
                for name in (
                    "OPENBLAS_NUM_THREADS",
                    "OMP_NUM_THREADS",
                    "MKL_NUM_THREADS",
                )
            },
        },
        "samples": samples,
        "medians": {
            name: statistics.median(sample[name] for sample in samples)
            for name in (
                "setup_seconds",
                "evaluate_and_snapshot_seconds",
                "response_encode_seconds",
                "total_seconds",
                "response_bytes",
            )
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("workspace", type=Path)
    _ = parser.add_argument(
        "recipe", type=Path, help="recipe JSON or graph snapshot JSON"
    )
    _ = parser.add_argument("--runs", type=int, default=3)
    _ = parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = measure(args.workspace, args.recipe, args.runs)
    encoded = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output is not None:
        _ = args.output.write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
