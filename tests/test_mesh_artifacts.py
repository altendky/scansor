from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from scansor.mesh_accounting import account_import
from scansor.mesh_artifacts import open_contributions, open_import
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, decode_control, encode_control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_publication import (
    PublishedStage,
    publish_contributions,
    publish_import,
)
from scansor.mesh_recipes import SMALL_RECIPES, write_recipe
from scansor.mesh_replay import verify_mesh
from scansor.mesh_resources import MIB
from tests.test_mesh_accounting import independent_artifacts


def published(
    directory: Path, name: str, *, storage: str = "disk", sidecar: bool = False
) -> tuple[PublishedStage, PublishedStage]:
    source = directory / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES[name])
    info = directory / "input.xml" if sidecar else None
    if info is not None:
        _ = info.write_bytes(
            b'<Model unknown="retained"/><ModelExport formatAndVersionUID="binary"/>'
        )
    with (
        prepare_import(
            source, directory, chunk_rows=2, storage=storage, sidecar=info
        ) as data,
        account_import(data) as imported,
    ):
        first = publish_import(imported, directory)
        second = publish_contributions(
            imported, imported.complete_contributions(), directory
        )
    return first, second


def artifact_state(path: Path) -> dict[str, tuple[int, int, int, int, str | None]]:
    result: dict[str, tuple[int, int, int, int, str | None]] = {}
    for entry in (path, *sorted(path.rglob("*"))):
        info = entry.stat(follow_symlinks=False)
        result[str(entry.relative_to(path))] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
            hashlib.sha256(entry.read_bytes()).hexdigest() if entry.is_file() else None,
        )
    return result


@pytest.mark.parametrize(
    "name", tuple(name for name in SMALL_RECIPES if name != "wrong-list-count-v1")
)
def test_readonly_queries_and_full_source_replay(tmp_path: Path, name: str) -> None:
    first, second = published(tmp_path, name, sidecar=True)
    expected = independent_artifacts(SMALL_RECIPES[name])
    before = artifact_state(first.path), artifact_state(second.path)
    for chunk in (1, 7):
        with (
            open_import(
                first.path, chunk_rows=chunk, expected_id=first.identity
            ) as imported,
            open_contributions(
                second.path, imported, expected_id=second.identity
            ) as contributions,
        ):
            vertex_bytes = {
                name: bytearray()
                for name in expected
                if name not in ("triangles.bin", "face-status.bin", "face-area.bin")
            }
            for start in range(0, imported.vertices, chunk):
                result = contributions.read_vertices(
                    start, min(start + chunk, imported.vertices)
                )
                assert result.source_id == imported.source_id
                assert result.stage_id == second.identity and result.table == "vertices"
                for field, rows in result.columns.items():
                    assert not rows.flags.writeable
                    vertex_bytes[field].extend(rows.tobytes())
            assert all(
                bytes(raw) == expected[field] for field, raw in vertex_bytes.items()
            )
            for start in range(0, imported.faces, chunk):
                result = imported.read_faces(start, min(start + chunk, imported.faces))
                assert (
                    result.source_id == imported.source_id
                    and result.stage_id == first.identity
                )
                for field, rows in result.columns.items():
                    stride = rows.dtype.itemsize * (
                        rows.shape[1] if rows.ndim == 2 else 1
                    )
                    assert (
                        rows.tobytes()
                        == expected[field][start * stride : result.stop * stride]
                    )
            empty = contributions.read_vertices(imported.vertices, imported.vertices)
            assert all(len(value) == 0 for value in empty.columns.values())
            with pytest.raises(MeshImportError, match="range"):
                _ = imported.read_vertices(-1, 0)
            if imported.vertices > chunk:
                with pytest.raises(MeshImportError, match="reservation"):
                    _ = contributions.read_vertices(0, chunk + 1)
            retained = contributions.read_vertices(0, 1).columns["xyz.bin"]
        assert retained.tobytes() == expected["xyz.bin"][:12]
        with pytest.raises(MeshImportError, match="closed"):
            _ = contributions.read_vertices(0, 1)
    for storage, budget, chunk in (("ram", 512, 1), ("disk", 2048, 7)):
        report = verify_mesh(
            first.path,
            tmp_path,
            contribution_path=second.path,
            expected_import_id=first.identity,
            expected_contribution_id=second.identity,
            storage=storage,
            budget_bytes=budget * MIB,
            chunk_rows=chunk,
        )
        assert report["status"] == "verified"
        assert (
            report["import_id"] == first.identity
            and report["contribution_id"] == second.identity
        )
        assert report["verified_columns"] == len(expected)
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_query_copies_are_independent_of_canonical_files(tmp_path: Path) -> None:
    first, _ = published(tmp_path, "unequal-adjacent-v1")
    with open_import(first.path, chunk_rows=2) as imported:
        original = imported.read_vertices(0, 2).columns["xyz.bin"]
        changed = imported.read_vertices(0, 2).columns["xyz.bin"]
        changed.flags.writeable = True
        changed[:] = 999
        assert np.array_equal(original, imported.read_vertices(0, 2).columns["xyz.bin"])


@pytest.mark.parametrize(
    "mutation",
    (
        "unknown-root",
        "pending",
        "unknown-column",
        "column-shape",
        "unknown-summary",
        "summary-count-bool",
        "row-digest-field",
        "policy-config",
        "extra-file",
        "missing-file",
        "truncated-file",
    ),
)
def test_artifact_schema_failures_precede_column_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    first, second = published(tmp_path, "unequal-adjacent-v1")
    target = second.path if mutation == "policy-config" else first.path
    control_name = (
        "request.json"
        if mutation == "policy-config"
        else "summary.json"
        if mutation in ("unknown-summary", "summary-count-bool")
        else "inventory.json"
    )
    control = cast(
        dict[str, Control], decode_control((target / control_name).read_bytes())
    )
    if mutation == "unknown-root":
        control["unrecognized"] = 0
    elif mutation == "pending":
        control["status"] = "foundation-ready"
    elif mutation == "unknown-column":
        cast(list[Control], control["columns"]).append({"name": "extra.bin"})
    elif mutation == "column-shape":
        cast(dict[str, Control], cast(list[Control], control["columns"])[0])[
            "shape"
        ] = [1, 3]
    elif mutation == "unknown-summary":
        cast(dict[str, Control], control["usable_face_area_sum"])["other"] = 1
    elif mutation == "summary-count-bool":
        control["vertices"] = True
    elif mutation == "row-digest-field":
        cast(dict[str, Control], control["row_digests"])["extra"] = "a" * 64
    elif mutation == "policy-config":
        control["configuration"] = {"threshold": 0}
    elif mutation == "extra-file":
        _ = (target / "extra.bin").write_bytes(b"unexpected")
    elif mutation == "missing-file":
        (target / "xyz.bin").unlink()
    elif mutation == "truncated-file":
        _ = (target / "xyz.bin").write_bytes(b"")
    _ = (target / control_name).write_bytes(encode_control(control))

    def fail_read(*_args: object, **_kwargs: object) -> None:
        pytest.fail("column range read happened before malformed artifact rejection")

    if mutation == "policy-config":
        with open_import(first.path, chunk_rows=2) as imported:
            monkeypatch.setattr(Column, "read_range", fail_read)
            with (
                pytest.raises(MeshImportError),
                open_contributions(second.path, imported),
            ):
                pytest.fail("unknown policy configuration accepted")
    else:
        monkeypatch.setattr(Column, "read_range", fail_read)
        with pytest.raises(MeshImportError), open_import(first.path, chunk_rows=2):
            pytest.fail("malformed artifact accepted")


@pytest.mark.parametrize("mutation", ("payload", "path", "directory", "extra"))
def test_held_artifact_detects_ordinary_changes_during_queries(
    tmp_path: Path, mutation: str
) -> None:
    first, _ = published(tmp_path, "unequal-adjacent-v1")
    with (
        pytest.raises(MeshImportError),
        open_import(first.path, chunk_rows=2) as imported,
    ):
        if mutation == "payload":
            with (first.path / "xyz.bin").open("r+b") as stream:
                _ = stream.write(b"bad!")
        elif mutation == "path":
            _ = (first.path / "xyz.bin").rename(first.path / "old.bin")
            _ = (first.path / "xyz.bin").write_bytes(b"\0" * 48)
        elif mutation == "directory":
            _ = first.path.rename(tmp_path / "moved")
            first.path.mkdir()
            _ = (first.path / "keep.txt").write_bytes(b"keep")
        else:
            _ = (first.path / "new.bin").write_bytes(b"extra")
        _ = imported.read_vertices(0, 1)
    if mutation == "directory":
        assert (first.path / "keep.txt").read_bytes() == b"keep"


def test_expected_identity_and_import_only_replay(tmp_path: Path) -> None:
    first, _ = published(tmp_path, "no-faces-v1")
    with (
        pytest.raises(MeshImportError, match="expected root"),
        open_import(first.path, expected_id="a" * 64),
    ):
        pytest.fail("wrong root accepted")
    result = verify_mesh(
        first.path, tmp_path, expected_import_id=first.identity, chunk_rows=2
    )
    assert result["contribution_id"] is None
    assert result["verified_columns"] == 7


def test_replay_refuses_scratch_inside_readonly_artifact(tmp_path: Path) -> None:
    first, second = published(tmp_path, "no-faces-v1")
    before = artifact_state(first.path), artifact_state(second.path)
    for scratch in (first.path, first.path / "source", second.path):
        with pytest.raises(MeshImportError, match="outside"):
            _ = verify_mesh(first.path, scratch, contribution_path=second.path)
    assert (artifact_state(first.path), artifact_state(second.path)) == before


def test_readonly_permission_modes_are_preserved(tmp_path: Path) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    for root in (first.path, second.path):
        for path in root.rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        root.chmod(0o555)
    before = artifact_state(first.path), artifact_state(second.path)
    try:
        result = verify_mesh(
            first.path, tmp_path, contribution_path=second.path, chunk_rows=2
        )
        assert result["status"] == "verified"
        assert (artifact_state(first.path), artifact_state(second.path)) == before
    finally:
        for root in (first.path, second.path):
            root.chmod(0o755)
            for path in root.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)


def test_symlinks_are_not_artifact_children(tmp_path: Path) -> None:
    first, _ = published(tmp_path, "unequal-adjacent-v1")
    original = (first.path / "xyz.bin").read_bytes()
    foreign = tmp_path / "foreign.bin"
    _ = foreign.write_bytes(original)
    (first.path / "xyz.bin").unlink()
    (first.path / "xyz.bin").symlink_to(foreign)
    with pytest.raises(MeshImportError, match="non-symlink"), open_import(first.path):
        pytest.fail("symlink accepted")
    assert foreign.read_bytes() == original


def test_unsupported_readonly_platform_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scansor import mesh_artifact_io

    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(MeshImportError, match="unavailable"):
        _ = mesh_artifact_io.ReadDirectory(tmp_path)
