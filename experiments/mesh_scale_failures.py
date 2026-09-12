"""Opt-in worker stop probes with complete source and unrelated-file preservation.

Run separately from throughput measurements. The ordinary measured-case report
remains a failed outcome; this wrapper records whether that expected failure and
owned-workspace cleanup were actually demonstrated. Marker files remain available
outside the worker's owned workspace for inspection.
"""

from __future__ import annotations

import argparse
import stat
from pathlib import Path

from experiments.mesh_scale_check import file_hash
from experiments.mesh_scale_measure import save_report
from experiments.mesh_scale_metrics import object_record
from experiments.mesh_scale_run import outside_git, run_case
from scansor.mesh_controls import Control


def _marker_state(root: Path) -> dict[str, Control]:
    result: dict[str, Control] = {}
    for name in (".", "keep.bin", "nested", "nested/keep.bin"):
        path = root / name
        info = path.stat(follow_symlinks=False)
        kind = "file" if name.endswith(".bin") else "directory"
        if (kind == "file" and not stat.S_ISREG(info.st_mode)) or (
            kind == "directory" and not stat.S_ISDIR(info.st_mode)
        ):
            raise ValueError("unrelated marker entry changed type")
        result[name] = {
            "kind": kind,
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": info.st_mode,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns,
            "content": file_hash(path) if kind == "file" else None,
        }
    if {path.name for path in root.iterdir()} != {"keep.bin", "nested"} or {
        path.name for path in (root / "nested").iterdir()
    } != {"keep.bin"}:
        raise ValueError("unrelated marker directory entries changed")
    return result


def probe_failure(
    frozen: Path,
    source: Path,
    workdir: Path,
    name: str,
    output: Path,
    *,
    kind: str,
    cancel_phase: str = "coordinate-association-1-lookup",
    chunk_rows: int | None = None,
) -> dict[str, Control]:
    workdir, source = outside_git(workdir), outside_git(source)
    output = outside_git(output.parent) / output.name
    if (
        not workdir.is_dir()
        or kind not in ("cancel", "startup-budget")
        or not name
        or Path(name).name != name
        or name in (".", "..")
        or "\\" in name
    ):
        raise ValueError("probe requires an explicit kind and plain case name")
    marker = workdir / (name + "-unrelated")
    measured_report = workdir / (name + "-run.json")
    case = workdir / name
    if marker.exists() or measured_report.exists() or output.exists() or case.exists():
        raise FileExistsError("probe, marker and measured report must all be new")
    if (
        output in (source, frozen.resolve(), measured_report)
        or output
        in {
            workdir / (name + "-" + operation + ".ndjson")
            for operation in ("import", "display", "verify-display")
        }
        or output.is_relative_to(case)
        or output.is_relative_to(marker)
    ):
        raise ValueError("probe report must be distinct from all inputs/run files")
    marker.mkdir(mode=0o700)
    (marker / "nested").mkdir(mode=0o700)
    _ = (marker / "keep.bin").write_bytes(b"unrelated sibling: preserve exactly\n")
    _ = (marker / "nested" / "keep.bin").write_bytes(b"nested unrelated content\n")
    result: dict[str, Control] = {
        "revision": "mesh-scale-failure-probe-v1",
        "kind": kind,
        "status": "failed",
        "scope": "Intentional worker stop, not a throughput or completed full-import case. Complete source hashes and unrelated marker contents/metadata are checked after worker exit; original failed worker measurements are retained separately.",
        "marker": str(marker),
        "measured_report": str(measured_report),
        "source": str(source),
    }
    try:
        result["marker_before"] = _marker_state(marker)
        measured = run_case(
            frozen,
            source,
            workdir,
            name,
            measured_report,
            budget_bytes=512 * 1024 * 1024 if kind == "cancel" else 1024 * 1024,
            storage="disk",
            chunk_rows=chunk_rows,
            cancel_phase=cancel_phase if kind == "cancel" else None,
        )
        result["measured_report_hash"] = file_hash(measured_report)
        result["measured_status"] = measured["status"]
        result["marker_after"] = _marker_state(marker)
        source_before = object_record(measured["source"])["actual"]
        source_after = file_hash(source)
        result["source_before"] = source_before
        result["source_after"] = source_after
        operations = object_record(measured["operations"])
        imported = object_record(operations["import"])
        outcome = object_record(imported["outcome"])
        failure = object_record(outcome["failure"])
        result["worker_failure"] = failure
        wanted = "cancelled" if kind == "cancel" else "resource"
        checks: dict[str, Control] = {
            "requested_failure_observed": measured["status"] == "failed"
            and failure.get("category") == wanted,
            "source_bytes_unchanged": source_before == source_after,
            "unrelated_contents_and_metadata_unchanged": result["marker_before"]
            == result["marker_after"],
            "owned_work_removed": object_record(imported["observations"])[
                "work_directory_empty_after_exit"
            ],
            "dependent_operations_not_started": set(operations) == {"import"},
            "nothing_published": outcome["published"] == []
            and not any((case / "out").iterdir()),
            "cancellation_triggered": object_record(imported["cancellation"])[
                "triggered"
            ]
            == (kind == "cancel"),
        }
        if kind == "cancel":
            checks["final_phase_observed"] = (
                object_record(imported["observations"])["phase_coverage_complete"]
                is True
                and object_record(outcome["last_progress"])["event"] == "final"
            )
            checks["worker_cancellation_preserved"] = (
                object_record(object_record(outcome["worker_report"])["failure"])[
                    "category"
                ]
                == "cancelled"
            )
        result["checks"] = checks
        if not all(value is True for value in checks.values()):
            raise ValueError("failure probe did not establish every requested check")
        result["status"] = "expected-failure-verified"
    except BaseException as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)[:2048]}
        raise
    finally:
        save_report(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("frozen", "source", "workdir", "output"):
        _ = parser.add_argument("--" + name, type=Path, required=True)
    _ = parser.add_argument("--name", required=True)
    _ = parser.add_argument(
        "--kind", choices=("cancel", "startup-budget"), required=True
    )
    _ = parser.add_argument("--cancel-phase", default="coordinate-association-1-lookup")
    _ = parser.add_argument("--chunk-rows", type=int)
    args = parser.parse_args()
    _ = probe_failure(
        args.frozen,
        args.source,
        args.workdir,
        args.name,
        args.output,
        kind=args.kind,
        cancel_phase=args.cancel_phase,
        chunk_rows=args.chunk_rows,
    )


if __name__ == "__main__":
    main()
