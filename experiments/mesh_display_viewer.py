"""Actual small S5 viewer evidence; generated files stay in a new outside-Git dir.

CloudCompare is an explicitly authorized, separately installed GPL executable.
No CloudCompare or plyfile source is consulted or linked. The CLI syntax follows
https://cloudcompare.org/doc/wiki/index.php/Command_line_mode .
This experiment is intentionally small and is not S6 memory/scale evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import struct
import subprocess
import tempfile
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import pyarrow  # pyright: ignore[reportMissingTypeStubs]

from scansor._plyio import make_header
from scansor.mesh_accounting import account_import
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_display import export_display
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_display_ply import KINDS, VERTEX_DIGITS, DisplayPlyReader
from scansor.mesh_display_verify import verify_display
from scansor.mesh_import import prepare_import
from scansor.mesh_numeric import float_bits
from scansor.mesh_publication import publish_contributions, publish_import
from scansor.mesh_recipes import SMALL_RECIPES, SmallRecipe, write_recipe
from scansor.mesh_viewer_compare import compare_viewer_resave


def fixtures() -> tuple[SmallRecipe, ...]:
    exact = SmallRecipe(
        "viewer-exact-v1",
        (
            *SMALL_RECIPES["right-triangle-orphan-v1"].xyz_bits,
            (0x40A00000, 0x40A00000, 0x40A00000),
            (0x7FC00000, 0, 0),
        ),
        ((0, 1, 2), (0, 1, 2), (2, 1, 0), (-1, 0, 1), (4, 4, 4), (0, 5, 1)),
        (
            (0x3F800000, 0, 0),
            (0x7FC00000, 0, 0),
            (0, 0, 0),
            (0, 0x3F800000, 0),
            (0, 0, 0),
            (0, 0, 0),
        ),
    )
    coordinates = (
        (0.0, 0.0, 0.0),
        (2.0**66, 0.0, 0.0),
        (0.0, 2.0**65, 0.0),
        (0.0, 0.0, 0.0),
        (2.0**-74, 0.0, 0.0),
        (0.0, 2.0**-75, 0.0),
        (2.0**24, 1.0, 0.0),
    )
    words = cast(
        tuple[tuple[int, int, int], ...],
        tuple(
            tuple(struct.unpack("<I", struct.pack("<f", value))[0] for value in row)
            for row in coordinates
        ),
    )
    precision = SmallRecipe("viewer-precision-v1", words, ((0, 1, 2), (3, 4, 5)))
    return (
        SMALL_RECIPES["right-triangle-orphan-v1"],
        exact,
        precision,
        SMALL_RECIPES["all-invalid-v1"],
    )


def viewer_roundtrip(
    viewer: Path,
    source: Path,
    kind: str,
    directory: Path,
    *,
    shift: tuple[str, str, str] = ("0", "0", "0"),
    edits: tuple[str, ...] = (),
) -> dict[str, Control]:
    directory.mkdir()
    output, log = directory / "resaved.ply", directory / "viewer.log"
    with source.open("rb") as stream:
        reader = DisplayPlyReader(stream, kind)
        fields = [name for name in reader.fields() if name.startswith("scalar_")]
        vertices, faces = reader.vertices, reader.faces
    command = [
        str(viewer),
        "-SILENT",
        "-AUTO_SAVE",
        "OFF",
        "-O",
        "-GLOBAL_SHIFT",
        *shift,
        str(source),
    ]
    for index in range(len(fields)):
        command.extend(("-SET_ACTIVE_SF", str(index)))
    command.extend(edits)
    command.extend(
        (
            "-M_EXPORT_FMT" if faces else "-C_EXPORT_FMT",
            "PLY",
            "-PLY_EXPORT_FMT",
            "BINARY_LE",
            "-SAVE_MESHES" if faces else "-SAVE_CLOUDS",
            "FILE",
            str(output),
        )
    )
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    _ = (directory / "command.json").write_bytes(encode_control(list(command)))
    with log.open("xb") as stream:
        result = subprocess.run(
            command,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            check=False,
            timeout=45,
        )
    with log.open("rb") as stream:
        log_bytes = stream.read(4 * 1024 * 1024 + 1)
    if len(log_bytes) > 4 * 1024 * 1024:
        raise RuntimeError("small viewer log exceeded 4 MiB")
    text = log_bytes.decode("utf-8", errors="replace")
    report: dict[str, Control] = {
        "command": list(command),
        "returncode": result.returncode,
        "log_sha256": hashlib.sha256(log_bytes).hexdigest(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_vertices": vertices,
        "source_faces": faces,
        "scalar_index_mapping": list(fields),
        "successful_field_selections": text.count("[SET ACTIVE SF] finished"),
        "global_shift_requested": list(shift),
        "viewer_scale_request": "none",
        "scope": "actual CLI load, selection of every scalar, and save; no visual GUI or physical-validation claim",
    }
    if not output.exists():
        report["status"] = "empty-view-refused" if vertices == 0 else "viewer-failed"
        report["log_excerpt"] = text[-8192:]
    else:
        report["status"] = "saved"
        with output.open("rb") as stream:
            saved = DisplayPlyReader(stream, kind, resaved=True)
            report["saved_header"] = saved.layout.header.raw.decode("ascii")
        report["comparison"] = compare_viewer_resave(
            source, output, kind, directory.parent, chunk_rows=2
        )
    _ = (directory / "report.json").write_bytes(encode_control(report))
    return report


def codec_diagnostic(viewer: Path, source: Path, directory: Path) -> dict[str, Control]:
    """Actual viewer test of ID encodings, explicitly separate from mesh row counts."""
    directory.mkdir()
    with source.open("rb") as stream:
        reader = DisplayPlyReader(stream, "validity")
        rows = reader.read_range("vertex", 0, reader.vertices).copy()
        faces = reader.read_range("face", 0, reader.faces)
        header = make_header(reader.layout.header.elements)
    ids = (2**24 + 1, 2**53 + 1, 2**63 + 1, 2**64 - 1)
    for index, source_id in enumerate(ids):
        for digit, field in enumerate(VERTEX_DIGITS):
            rows[field][index] = (source_id >> (16 * digit)) & 65535
    probe = directory / "codec.ply"
    _ = probe.write_bytes(header.raw + rows.tobytes() + faces.tobytes())
    result = viewer_roundtrip(viewer, probe, "validity", directory / "roundtrip")
    result["meaning"] = (
        "Standalone ID codec diagnostic; these are artificial labels, not a claim that a four-vertex mesh has these source row ordinals."
    )
    result["diagnostic_ids"] = list(ids)
    return result


def exact_roundtrip(record: Control) -> bool:
    if not isinstance(record, dict):
        return False
    comparison = record.get("comparison")
    if not isinstance(comparison, dict):
        return False
    mapping, topology = comparison.get("mapping"), comparison.get("topology")
    fields = comparison.get("fields")
    selections = record.get("scalar_index_mapping")
    return (
        record.get("status") == "saved"
        and record.get("returncode") == 0
        and isinstance(selections, list)
        and record.get("successful_field_selections") == len(selections)
        and isinstance(mapping, dict)
        and mapping.get("status") == "exact"
        and isinstance(topology, dict)
        and topology.get("status") == "exact"
        and comparison.get("dropped_or_renamed_fields") == []
        and comparison.get("added_or_renamed_fields") == []
        and isinstance(fields, dict)
        and bool(fields)
        and all(
            isinstance(field, dict) and field.get("different") == 0
            for field in fields.values()
        )
    )


def provenance() -> dict[str, Control]:
    root = resources.files("scansor")
    names = (
        "_plyio/format.py",
        "_plyio/stream.py",
        "mesh_display.py",
        "mesh_display_ply.py",
        "mesh_display_records.py",
        "mesh_display_numeric.py",
        "mesh_display_semantics.py",
        "mesh_display_staging.py",
        "mesh_display_verify.py",
        "mesh_viewer_compare.py",
    )
    return {
        "experiment_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files": {
            name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
            for name in names
        },
        "dependencies": {
            "numpy": np.__version__,
            "duckdb": duckdb.__version__,
            "pyarrow": pyarrow.__version__,
        },
    }


def run(options: Any) -> dict[str, Control]:
    directory = Path(tempfile.mkdtemp(prefix="scansor-s5-viewer-", dir=options.workdir))
    print(str(directory), flush=True)
    viewer = options.viewer.resolve(strict=True)
    digest = hashlib.sha256()
    with viewer.open("rb") as stream:
        while block := stream.read(65536):
            digest.update(block)
    if digest.hexdigest() != options.viewer_sha256:
        raise RuntimeError("viewer executable differs from explicitly pinned build")
    report: dict[str, Control] = {
        "revision": "mesh-display-viewer-experiment-v1",
        "started_at": datetime.now(UTC).isoformat(),
        "viewer": {
            "path": str(viewer),
            "sha256": digest.hexdigest(),
            "license": "GPL; separately installed executable, not a Scansor dependency",
        },
        "platform": platform.platform(),
        "python": platform.python_version(),
        "directory": str(directory),
        "fixtures": [],
        "implementation": provenance(),
        "authority": "Generated inputs and read-only authoritative artifacts are retained separately from viewer resaves.",
    }
    for recipe in fixtures():
        root = directory / recipe.name
        root.mkdir()
        source = root / "input.ply"
        with source.open("xb") as stream:
            write_recipe(stream, recipe)
        _ = (root / "recipe.json").write_bytes(encode_control(recipe.record()))
        with (
            prepare_import(source, root, chunk_rows=2, storage="disk") as data,
            account_import(data) as accounted,
        ):
            imported = publish_import(accounted, root)
            contributions = publish_contributions(
                accounted, accounted.complete_contributions(), root
            )
        transform = (
            DisplayTransform((float_bits(0.5), float_bits(0.0), float_bits(0.0)), -1)
            if recipe.name == "viewer-precision-v1"
            else DisplayTransform()
        )
        exported = export_display(
            imported.path,
            contributions.path,
            root,
            root,
            chunk_rows=2,
            transform=transform,
        )
        verified = verify_display(
            exported.stage.path,
            imported.path,
            contributions.path,
            root,
            expected_display_id=exported.stage.identity,
            chunk_rows=1,
        )
        entry: dict[str, Control] = {
            "name": recipe.name,
            "recipe": recipe.record(),
            "import_id": imported.identity,
            "contribution_id": contributions.identity,
            "display_id": exported.stage.identity,
            "legend": exported.legend,
            "display_verification": verified,
            "roundtrips": {},
        }
        for kind in KINDS:
            record = viewer_roundtrip(
                viewer, exported.stage.path / (kind + ".ply"), kind, root / kind
            )
            cast(dict[str, Control], entry["roundtrips"])[kind] = record
            print(recipe.name, kind, record["status"], flush=True)
        if recipe.name == "right-triangle-orphan-v1":
            entry["large_id_codec_diagnostic"] = codec_diagnostic(
                viewer, exported.stage.path / "validity.ply", root / "codec-diagnostic"
            )
            cast(dict[str, Control], entry["roundtrips"])["nonzero-shift"] = (
                viewer_roundtrip(
                    viewer,
                    exported.stage.path / "validity.ply",
                    "validity",
                    root / "nonzero-shift",
                    shift=("10", "20", "30"),
                )
            )
            cast(dict[str, Control], entry["roundtrips"])["dropped-source-key"] = (
                viewer_roundtrip(
                    viewer,
                    exported.stage.path / "validity.ply",
                    "validity",
                    root / "dropped-source-key",
                    edits=("-REMOVE_SF", "5"),
                )
            )
        cast(list[Control], report["fixtures"]).append(entry)
        _ = (directory / "report.json").write_bytes(encode_control(report))
    report["finished_at"] = datetime.now(UTC).isoformat()
    entries = cast(list[dict[str, Control]], report["fixtures"])
    by_name = {str(entry["name"]): entry for entry in entries}
    exact = cast(dict[str, Control], by_name["viewer-exact-v1"]["roundtrips"])
    golden = by_name["right-triangle-orphan-v1"]
    simple = cast(dict[str, Control], golden["roundtrips"])
    precision = cast(dict[str, Control], by_name["viewer-precision-v1"]["roundtrips"])
    report["gates"] = {
        "representable_views": all(exact_roundtrip(exact[kind]) for kind in KINDS)
        and all(exact_roundtrip(simple[kind]) for kind in ("validity", "weights")),
        "large_id_codec": exact_roundtrip(golden["large_id_codec_diagnostic"]),
        "explicit_nonzero_global_shift": exact_roundtrip(simple["nonzero-shift"]),
        "precision_preservation": all(
            exact_roundtrip(precision[kind]) for kind in ("validity", "weights")
        ),
        "precision_preservation_meaning": "Use per-field observed differences; binary64 export is not a viewer precision guarantee.",
        "gui_visual_inspection": "not performed by this CLI experiment",
        "scale_resource_evidence": "not measured by this small experiment",
    }
    if report["implementation"] != provenance():
        raise RuntimeError("implementation changed during the viewer experiment")
    _ = (directory / "report.json").write_bytes(encode_control(report))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--viewer", type=Path, required=True)
    _ = parser.add_argument("--viewer-sha256", required=True)
    _ = parser.add_argument("--workdir", type=Path, required=True)
    options = parser.parse_args()
    _ = run(options)


if __name__ == "__main__":
    main()
