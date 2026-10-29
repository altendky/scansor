"""Native archive oracle and explicit legacy fixture for actual UI acceptance."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from experiments.feature_graph import Recipe, Source
from experiments.nozzle_session import NozzleWorkspace
from experiments.project_archive import (
    ProjectArchiveLimits,
    read_project_archive,
    write_project_archive,
)
from experiments.project_legacy import migrate_known_legacy
from experiments.scan_sources import BytesInput, PathInput


def resource_fixture(directory: Path) -> dict[str, Any]:
    """Outside measured Chromium: generate the existing admitted source tier."""
    from experiments.nozzle_browser import NATIVE_SCAN_LIMITS
    from experiments.scan_project import ScanProject
    from tests.native_scan_fixture import native_grid_fixture

    directory.mkdir()
    fixture = native_grid_fixture(512, 256)
    source_path = directory / "grid-512x256.ply"
    _ = source_path.write_bytes(fixture.raw)
    project = ScanProject()
    empty = project.accepted
    accepted = project.commit(
        project.prepare_add(
            PathInput(source_path),
            source_path.name,
            NATIVE_SCAN_LIMITS,
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
    )
    recipe = accepted.graph.export_recipe()
    archive_path = directory / "grid-512x256.scansor"
    with archive_path.open("wb") as sink:
        receipt = write_project_archive(recipe, accepted.workspace.source, sink)
    return {
        "archive_path": str(archive_path),
        "archive_bytes": receipt.bytes_written,
        "source_sha256": accepted.workspace.source_sha256,
        "source_bytes": len(fixture.raw),
        "vertices": len(accepted.workspace.local),
        "triangles": len(accepted.workspace.data.triangles),
        "member_name": receipt.source_entry,
        "recipe": recipe,
        "display": {
            name: {
                "bytes": array.nbytes,
                "sha256": hashlib.sha256(array.tobytes()).hexdigest(),
            }
            for name, array in (
                ("positions", accepted.positions),
                ("indices", accepted.indices),
            )
        },
    }


def inspect_file(path: Path) -> dict[str, Any]:
    """Verify a downloaded archive without materializing its original member."""
    opened = read_project_archive(PathInput(path), legacy_resolver=migrate_known_legacy)
    digest = hashlib.sha256()
    count = maximum = 0
    if opened.original is not None:
        with opened.original.open() as stream:
            while chunk := stream.read(65_536):
                digest.update(chunk)
                count += len(chunk)
                maximum = max(maximum, len(chunk))
    return {
        "archive_bytes": path.stat().st_size,
        "source_sha256": digest.hexdigest() if count else None,
        "source_bytes": count,
        "source_eof_verified": opened.original is not None,
        "max_source_chunk_bytes": maximum,
        "member_name": opened.member_name,
        "actions": opened.actions,
    }


def legacy_fixture() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    example = root / "examples/nozzle-bayonette-simplified"
    workspace = NozzleWorkspace(example)
    raw = (example / "nozzle-bayonette-simplified.ply").read_bytes()
    recipe = Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Captured legacy nozzle",
                operation="source",
                source_sha256=workspace.source_sha256,
                reference_sha256=workspace.reference_sha256,
            )
        ],
        output="scan",
    ).model_dump(mode="json")
    sink = io.BytesIO()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("actions.json", json.dumps(recipe))
        archive.writestr("nozzle-bayonette-simplified.ply", raw)
    return {
        "archive_base64": base64.b64encode(sink.getvalue()).decode(),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "reference_sha256": workspace.reference_sha256,
        "vertices": len(workspace.local),
        "triangles": len(workspace.data.triangles),
        "recipe": recipe,
    }


def inspect(raw: bytes) -> dict[str, Any]:
    opened = read_project_archive(
        BytesInput(raw, "downloaded.scansor"), legacy_resolver=migrate_known_legacy
    )
    digest = hashlib.sha256()
    count = largest = 0
    if opened.original is not None:
        with opened.original.open() as stream:
            while chunk := stream.read(65_536):
                digest.update(chunk)
                count += len(chunk)
                largest = max(largest, len(chunk))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.namelist()
        declarations = json.loads(archive.read("actions.json"))
    return {
        "archive_bytes": len(raw),
        "manifest": opened.manifest,
        "actions": opened.actions,
        "source_sha256": digest.hexdigest() if count else None,
        "source_bytes": count,
        "max_source_chunk_bytes": largest,
        "source_eof_verified": opened.original is not None,
        "member_name": opened.member_name,
        "members": members,
        "archive_entry": next(
            (
                node.get("archive_entry")
                for node in declarations["nodes"]
                if node["operation"] == "source"
            ),
            None,
        ),
        "legacy_reference": opened.legacy_reference,
        "migrated_legacy": opened.migrated_legacy,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "mode",
        choices=("legacy-fixture", "inspect", "resource-fixture", "inspect-file"),
    )
    _ = parser.add_argument("--directory", type=Path)
    _ = parser.add_argument("--file", type=Path)
    arguments = parser.parse_args()
    if arguments.mode == "resource-fixture":
        if arguments.directory is None:
            parser.error("resource-fixture requires --directory")
        result = resource_fixture(arguments.directory)
    elif arguments.mode == "inspect-file":
        if arguments.file is None:
            parser.error("inspect-file requires --file")
        result = inspect_file(arguments.file)
    elif arguments.mode == "legacy-fixture":
        result = legacy_fixture()
    else:
        raw = sys.stdin.buffer.read(ProjectArchiveLimits().max_archive_bytes + 1)
        if len(raw) > ProjectArchiveLimits().max_archive_bytes:
            raise ValueError("Acceptance archive exceeds its compressed-byte bound")
        result = inspect(raw)
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
