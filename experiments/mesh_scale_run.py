"""Opt-in sequential full grid import/contribution/display/replay benchmark.

Run one case at a time as python -m experiments.mesh_scale_run. Large sources,
workspaces, telemetry and outputs must be outside Git. Expectations must already
be frozen. Failure reports and any completed publications remain inspectable.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import cast

from experiments.mesh_scale_check import (
    check_grid_display,
    check_import,
    file_hash,
    load_expectation,
)
from experiments.mesh_scale_measure import ProgressPrinter, measure_worker, save_report
from experiments.mesh_scale_metrics import host_record, object_record
from scansor.mesh_controls import Control
from scansor.mesh_worker_request import WorkerRequest


def outside_git(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if any(
        (parent / ".git").is_file() or (parent / ".git" / "HEAD").is_file()
        for parent in (resolved, *resolved.parents)
    ):
        raise ValueError("benchmark files must be outside Git, including ignored files")
    return resolved


def implementation_record() -> dict[str, Control]:
    root = Path(__file__).resolve().parents[1]
    sources: dict[str, Control] = {}
    for directory in (root / "src/scansor", root / "experiments"):
        for path in sorted(directory.rglob("*.py")):
            if directory.name == "experiments" and not path.name.startswith(
                "mesh_scale_"
            ):
                continue
            sources[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    native: dict[str, Control] = {}
    for name in (
        "numpy._core._multiarray_umath",
        "_duckdb",
        "pyarrow.lib",
        "psutil._psutil_linux",
    ):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None:
            raise ValueError("cannot identify native extension " + name)
        native[name] = file_hash(Path(spec.origin))
    return {
        "sources": sources,
        "native_extensions": native,
        "python_executable": str(Path(sys.executable).resolve()),
        "python_executable_hash": file_hash(Path(sys.executable).resolve()),
        "versions": {
            name: version(name)
            for name in ("numpy", "duckdb", "pyarrow", "psutil", "defusedxml")
        },
    }


def published(measurement: dict[str, Control], kind: str) -> tuple[Path, str]:
    outcome = object_record(measurement["outcome"])
    stages = [
        object_record(value) for value in cast(list[Control], outcome["published"])
    ]
    matches = [stage for stage in stages if stage["kind"] == kind]
    if len(matches) != 1:
        raise ValueError("expected exactly one completed " + kind + " publication")
    return Path(str(matches[0]["path"])), str(matches[0]["identity"])


def run_case(
    frozen: Path,
    source: Path,
    workdir: Path,
    name: str,
    output: Path,
    *,
    budget_bytes: int,
    storage: str = "auto",
    chunk_rows: int | None = None,
    through: str = "verify-display",
    cancel_phase: str | None = None,
    cancel_completed: int = 0,
) -> dict[str, Control]:
    workdir, source = outside_git(workdir), outside_git(source)
    output = outside_git(output.parent) / output.name
    if (
        not workdir.is_dir()
        or not name
        or name in (".", "..")
        or Path(name).name != name
        or "\\" in name
    ):
        raise ValueError("workdir must exist and case name must be one plain component")
    if through not in ("import", "display", "verify-display") or cancel_completed < 0:
        raise ValueError("invalid case execution selection")
    root = workdir / name
    telemetry_paths = {
        operation: workdir / (name + "-" + operation + ".ndjson")
        for operation in ("import", "display", "verify-display")
    }
    if (
        root.exists()
        or output.exists()
        or any(path.exists() for path in telemetry_paths.values())
    ):
        raise FileExistsError("case, report or telemetry path already exists")
    if (
        output == source
        or output == frozen.resolve()
        or output in telemetry_paths.values()
        or output.is_relative_to(root)
    ):
        raise ValueError(
            "report must be distinct from source/expectation/telemetry and outside the measured root"
        )
    expected, frozen_sha = load_expectation(frozen)
    implementation = implementation_record()
    report: dict[str, Control] = {
        "revision": "mesh-scale-grid-run-v1",
        "status": "failed",
        "started_utc": datetime.now(UTC).isoformat(),
        "case": name,
        "run_root": str(root),
        "frozen_manifest_sha256": frozen_sha,
        "expectation": expected,
        "implementation": implementation,
        "through": through,
        "operations": {},
        "checks": {},
        "scope": "One sequential complete generated grid case. Post-worker checks scan every canonical column and compare frozen hashes and row digests; display replay rebuilds every exported data file. Independent checks run outside measured workers. No viewer interaction or physical validation is measured.",
    }
    started = time.monotonic_ns()
    try:
        # Prehash precedes the worker and deliberately warms an uncontrolled cache.
        # It also prevents measuring a differently configured/missing source.
        checked_source = file_hash(source, progress=ProgressPrinter())
        report["source"] = {"path": str(source), "actual": checked_source}
        if checked_source != expected["source"]:
            raise ValueError("complete source differs from frozen expectation")
        root.mkdir(mode=0o700)
        (root / "work").mkdir(mode=0o700)
        (root / "out").mkdir(mode=0o700)
        operations, checks = (
            object_record(report["operations"]),
            object_record(report["checks"]),
        )

        def measure(request: WorkerRequest) -> dict[str, Control]:
            result = measure_worker(
                request,
                root,
                telemetry_paths[request.operation],
                cancel_phase=cancel_phase,
                cancel_completed=cancel_completed,
            )
            operations[request.operation] = result
            if result["status"] != "complete":
                raise RuntimeError(
                    "measured "
                    + request.operation
                    + " failed; inspect operation outcome"
                )
            return result

        imported = measure(
            WorkerRequest(
                "import",
                source,
                destination=root / "out",
                budget_bytes=budget_bytes,
                storage=storage,
                chunk_rows=chunk_rows,
            )
        )
        first, first_id = published(imported, "import")
        second, second_id = published(imported, "contribution")
        check_started = time.monotonic_ns()
        checks["canonical"] = check_import(
            first,
            second,
            expected,
            import_id=first_id,
            contribution_id=second_id,
            progress=ProgressPrinter(),
        )
        checks["canonical_elapsed_ns"] = time.monotonic_ns() - check_started
        if through != "import":
            displayed = measure(
                WorkerRequest(
                    "display",
                    first,
                    contribution=second,
                    destination=root / "out",
                    expected_import_id=first_id,
                    expected_contribution_id=second_id,
                    budget_bytes=budget_bytes,
                    storage="disk",
                    chunk_rows=chunk_rows,
                )
            )
            third, third_id = published(displayed, "display")
            check_started = time.monotonic_ns()
            checks["display"] = check_grid_display(
                third,
                expected,
                display_id=third_id,
                import_id=first_id,
                contribution_id=second_id,
                progress=ProgressPrinter(),
            )
            checks["display_elapsed_ns"] = time.monotonic_ns() - check_started
            if through == "verify-display":
                _ = measure(
                    WorkerRequest(
                        "verify-display",
                        first,
                        contribution=second,
                        display=third,
                        expected_import_id=first_id,
                        expected_contribution_id=second_id,
                        expected_display_id=third_id,
                        budget_bytes=budget_bytes,
                        storage="disk",
                        chunk_rows=chunk_rows,
                    )
                )
        if implementation_record() != implementation:
            raise RuntimeError("benchmark implementation changed during the case")
        report["status"] = "complete"
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)[:2048]}
    finally:
        report["elapsed_including_checks_ns"] = time.monotonic_ns() - started
        report["host_after_checks"] = host_record(workdir)
        save_report(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("frozen", "source", "workdir", "output"):
        _ = parser.add_argument("--" + name, type=Path, required=True)
    _ = parser.add_argument("--name", required=True)
    _ = parser.add_argument("--budget-mib", type=int, required=True)
    _ = parser.add_argument(
        "--storage", choices=("auto", "ram", "disk"), default="auto"
    )
    _ = parser.add_argument("--chunk-rows", type=int)
    _ = parser.add_argument(
        "--through",
        choices=("import", "display", "verify-display"),
        default="verify-display",
    )
    _ = parser.add_argument("--cancel-phase")
    _ = parser.add_argument("--cancel-completed", type=int, default=0)
    args = parser.parse_args()
    result = run_case(
        args.frozen,
        args.source,
        args.workdir,
        args.name,
        args.output,
        budget_bytes=args.budget_mib * 1024 * 1024,
        storage=args.storage,
        chunk_rows=args.chunk_rows,
        through=args.through,
        cancel_phase=args.cancel_phase,
        cancel_completed=args.cancel_completed,
    )
    print(
        json.dumps({"status": result["status"], "report": str(args.output)}), flush=True
    )
    raise SystemExit(0 if result["status"] == "complete" else 1)


if __name__ == "__main__":
    main()
