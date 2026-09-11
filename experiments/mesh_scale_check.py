"""Complete read-only comparisons against independently frozen S6 expectations."""

from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack, closing
from pathlib import Path
from typing import cast

from experiments.mesh_scale_metrics import integer, object_record
from scansor.mesh_artifact_io import Progress, ReadDirectory, ReadFile, no_progress
from scansor.mesh_artifacts import open_contributions, open_import
from scansor.mesh_controls import Control, control_id, encode_control
from scansor.mesh_display_ply import KINDS, DisplayPlyReader


def load_expectation(path: Path) -> tuple[dict[str, Control], str]:
    with path.open("rb") as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        raise ValueError("expectation manifest exceeds 128 KiB")
    record = object_record(json.loads(raw))
    _ = encode_control(record)
    canonical = (
        json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode()
    if (
        canonical != raw
        or record.get("revision")
        not in ("mesh-scale-grid-expectation-v1", "mesh-scale-fan-expectation-v1")
        or record.get("status") != "complete"
    ):
        raise ValueError("a complete canonical expectation freeze is required")
    expected = object_record(record.get("expectation"))
    digest = hashlib.sha256(
        (
            json.dumps(expected, sort_keys=True, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode()
    ).hexdigest()
    if digest != record.get("expectation_sha256"):
        raise ValueError("frozen expectation payload digest differs")
    return expected, hashlib.sha256(raw).hexdigest()


def file_hash(path: Path, *, progress: Progress = no_progress) -> dict[str, Control]:
    with (
        closing(ReadDirectory(path.parent)) as directory,
        closing(ReadFile(directory, path.name)) as file,
    ):
        return {
            "bytes": file.size,
            "sha256": file.sha256(phase="check-sha256-" + path.name, progress=progress),
        }


def _same(actual: Control, expected: Control, label: str) -> None:
    if encode_control(actual) != encode_control(expected):
        raise ValueError("frozen expectation differs: " + label)


def check_import(
    first: Path,
    second: Path,
    expected: dict[str, Control],
    *,
    import_id: str,
    contribution_id: str,
    progress: Progress = no_progress,
) -> dict[str, Control]:
    """Opening scans/hashes every column and rechecks all ordered row digests."""
    with (
        open_import(first, expected_id=import_id, progress=progress) as imported,
        open_contributions(
            second, imported, expected_id=contribution_id, progress=progress
        ) as contributed,
    ):
        n, m = integer(expected["vertices"]), integer(expected["faces"])
        _same([imported.vertices, imported.faces], [n, m], "source populations")
        _same(
            {
                "bytes": imported.source.ply.byte_count,
                "sha256": imported.source.ply.sha256,
            },
            expected["source"],
            "copied complete source",
        )
        columns: dict[str, Control] = {}
        for inventory in (imported.inventory, contributed.inventory):
            for value in cast(list[Control], inventory["columns"]):
                entry = object_record(value)
                columns[str(entry["name"])] = {
                    "bytes": entry["byte_count"],
                    "sha256": entry["sha256"],
                }
        _same(columns, expected["columns"], "all canonical column bytes and hashes")
        imports, contributions = (
            object_record(imported.inventory["row_digests"]),
            object_record(contributed.inventory["row_digests"]),
        )
        digests: dict[str, Control] = {
            "import_vertices": imports["vertices"],
            "import_faces": imports["faces"],
            "contribution_vertices": contributions["vertices"],
        }
        _same(digests, expected["row_digests"], "all source-order row digests")
        # Both admitted expectation profiles have finite positions, absent
        # normals and complete in-range references; fans may reject some faces.
        _same(
            imported.summary["vertex_category_counts"],
            {"finite-position": n, "nonfinite-position": 0},
            "vertex categories",
        )
        _same(
            imported.summary["normal_category_counts"],
            {"absent": n, "finite-nonzero": 0, "zero-vector": 0, "nonfinite-vector": 0},
            "normal categories",
        )
        _same(
            imported.summary["face_category_counts"],
            expected.get(
                "face_category_counts",
                {
                    "usable": m,
                    "index-out-of-range": 0,
                    "nonfinite-position": 0,
                    "repeated-index": 0,
                    "zero-computed-area": 0,
                },
            ),
            "face categories",
        )
        _same(
            contributed.summary["category_counts"],
            {
                "eligible": n,
                "nonfinite-position": 0,
                "isolated": 0,
                "no-usable-area": 0,
            },
            "contribution categories",
        )
        _same(
            imported.summary["in_range_source_corners"],
            expected["in_range_source_corners"],
            "source corner references",
        )
        _same(
            imported.summary["out_of_range_source_corners"], 0, "out-of-range corners"
        )
        for summary, name, key in (
            (imported.summary, "usable_face_area_sum", "face_area_sum_bits"),
            (contributed.summary, "eligible_area_sum", "vertex_area_sum_bits"),
            (contributed.summary, "weight_sum", "weight_sum_bits"),
        ):
            _same(object_record(summary[name])["value_bits"], expected[key], name)
        for name, key in (
            ("vertex_area_range", "vertex_area_range_bits"),
            ("weight_range", "weight_range_bits"),
        ):
            record = object_record(contributed.summary[name])
            _same([record["minimum_bits"], record["maximum_bits"]], expected[key], name)
        contributed.check()
        return {
            "status": "verified-complete-canonical-data",
            "vertices": n,
            "faces": m,
            "source_id": imported.source_id,
            "import_id": imported.identity,
            "contribution_id": contributed.identity,
            "columns": columns,
            "row_digests": digests,
            "import_summary": imported.summary,
            "contribution_summary": contributed.summary,
        }


def check_display(
    path: Path,
    expected: dict[str, Control],
    *,
    display_id: str,
    import_id: str,
    contribution_id: str,
    progress: Progress = no_progress,
) -> dict[str, Control]:
    """Validate every exported row and full file hash; replay is a separate worker."""
    with ExitStack() as stack:
        directory = stack.enter_context(closing(ReadDirectory(path)))
        names = {
            "inventory.json",
            "legend.json",
            "view-vertices.bin",
            "view-faces.bin",
            *(kind + ".ply" for kind in KINDS),
        }
        directory.require_names(names)
        files = {
            name: stack.enter_context(closing(ReadFile(directory, name)))
            for name in names
        }
        return _check_display(
            files,
            expected,
            display_id=display_id,
            import_id=import_id,
            contribution_id=contribution_id,
            progress=progress,
        )


def _check_display(
    opened: dict[str, ReadFile],
    expected: dict[str, Control],
    *,
    display_id: str,
    import_id: str,
    contribution_id: str,
    progress: Progress,
) -> dict[str, Control]:
    inventory, legend = (
        opened["inventory.json"].control(),
        opened["legend.json"].control(),
    )
    _same(control_id(inventory), display_id, "display identity")
    _same(inventory["import_id"], import_id, "display import binding")
    _same(inventory["contribution_id"], contribution_id, "display contribution binding")
    _same(
        object_record(inventory["legend"])["sha256"],
        opened["legend.json"].sha256(phase="check-legend", progress=progress),
        "display legend hash",
    )
    n, m = integer(expected["vertices"]), integer(expected["faces"])
    _same(
        legend["source_population"],
        {"vertices": n, "faces": m},
        "display source populations",
    )
    population = object_record(
        expected.get(
            "display_population",
            {
                "vertices_per_main_view": n,
                "usable_faces_per_main_view": m,
                "rejected_corners": 0,
            },
        )
    )
    _same(
        legend["display_population"],
        population,
        "full displayable-row population",
    )
    _same(
        legend["omissions"],
        {
            "nonfinite_source_vertices": 0,
            "rejected_corners_out_of_range": 0,
            "rejected_corners_nonfinite_position": 0,
        },
        "display omissions",
    )
    files = cast(list[Control], inventory["files"])
    if {str(object_record(value)["name"]) for value in files} != {
        "validity.ply",
        "weights.ply",
        "rejected-face-corners.ply",
        "view-vertices.bin",
        "view-faces.bin",
    }:
        raise ValueError("unexpected display file inventory")
    for value in files:
        item = object_record(value)
        file = opened[str(item["name"])]
        actual: dict[str, Control] = {
            "bytes": file.size,
            "sha256": file.sha256(phase="check-display-sha256", progress=progress),
        }
        _same(
            actual,
            {"bytes": item["byte_count"], "sha256": item["sha256"]},
            "complete display file",
        )
    for kind in KINDS:
        file = opened[kind + ".ply"]
        _ = file.stream.seek(0)
        reader = DisplayPlyReader(file.stream, kind, chunk_rows=65536)
        _same(
            [reader.vertices, reader.faces],
            [integer(population["rejected_corners"]), 0]
            if kind == "rejected-face-corners"
            else [n, integer(population["usable_faces_per_main_view"])],
            "display PLY populations",
        )
        reader.validate_all()
    for file in opened.values():
        file.check()
    return {
        "status": "verified-full-display-rows-and-hashes",
        "display_id": display_id,
        "import_id": import_id,
        "contribution_id": contribution_id,
        "population": legend["display_population"],
        "files": files,
        "semantic_replay": "requires separate verify-display worker",
    }
