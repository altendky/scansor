from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import cast

import pytest

from scansor import mesh_accounting, mesh_publication
from scansor.files import rename_no_replace
from scansor.mesh_accounting import account_import
from scansor.mesh_controls import Control, decode_control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_numeric import NumericProfileError
from scansor.mesh_publication import (
    PublishedStage,
    publish_contributions,
    publish_import,
)
from scansor.mesh_recipes import SMALL_RECIPES, write_recipe
from tests.test_mesh_accounting import independent_artifacts


def _source(directory: Path) -> Path:
    source = directory / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["exceptional-values-v1"])
    return source


@pytest.mark.parametrize("storage", ("ram", "disk"))
def test_independent_closed_stage_publication(tmp_path: Path, storage: str) -> None:
    source = _source(tmp_path)
    sidecar = tmp_path / "info.xml"
    _ = sidecar.write_bytes(b'<Model strange="retained"/>')
    expected = independent_artifacts(SMALL_RECIPES["exceptional-values-v1"])
    with (
        prepare_import(
            source, tmp_path, storage=storage, chunk_rows=2, sidecar=sidecar
        ) as data,
        account_import(data) as imported,
    ):
        first = publish_import(imported, tmp_path)
        assert first.path.name == "mesh-import-" + imported.identity
        assert first.path.is_dir()
        assert not list(tmp_path.glob("mesh-contribution-*"))
        assert data.source.ply.stream.closed
        assert data.source.sidecar is not None and data.source.sidecar.stream.closed
        for column in data.columns.values():
            with pytest.raises(MeshImportError, match="closed"):
                _ = column.read_range(0, 0)
        contributions = imported.complete_contributions()
        second = publish_contributions(imported, contributions, tmp_path)
        assert second.identity == contributions.identity
        for column in contributions.columns.values():
            with pytest.raises(MeshImportError, match="closed"):
                _ = column.read_range(0, 0)
    assert not list(tmp_path.glob(".scansor-mesh-*"))
    assert (first.path / "source/observations.ply").read_bytes() == source.read_bytes()
    assert (
        first.path / "source/observations.rsInfo"
    ).read_bytes() == sidecar.read_bytes()
    for name, raw in expected.items():
        directory = (
            second.path
            if name in ("contribution-status.bin", "vertex-area.bin", "weight.bin")
            else first.path
        )
        assert (directory / name).read_bytes() == raw
    for published in (first, second):
        control = (published.path / "inventory.json").read_bytes()
        assert hashlib.sha256(control).hexdigest() == published.identity
        inventory = cast(dict[str, Control], decode_control(control))
        for key in ("summary", "request"):
            if key in inventory:
                child = cast(dict[str, Control], inventory[key])
                raw = (published.path / str(child["name"])).read_bytes()
                assert len(raw) == child["byte_count"]
                assert hashlib.sha256(raw).hexdigest() == child["sha256"]


def test_normalization_failure_keeps_published_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    first: PublishedStage | None = None

    def fail(*_args: object, **_kwargs: object) -> None:
        raise NumericProfileError("injected after import publication")

    with (
        pytest.raises(MeshImportError, match="injected"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        first = publish_import(imported, tmp_path)
        monkeypatch.setattr(mesh_accounting, "normalized_weights", fail)
        _ = imported.complete_contributions()
    assert first is not None and first.path.is_dir()
    assert not list(tmp_path.glob("mesh-contribution-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_publication_never_overwrites_existing_output(tmp_path: Path) -> None:
    source = _source(tmp_path)
    target: Path | None = None
    sentinel: Path | None = None
    with (
        pytest.raises(MeshImportError, match="already exists"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        target = tmp_path / ("mesh-import-" + imported.identity)
        target.mkdir()
        sentinel = target / "unrelated.txt"
        _ = sentinel.write_bytes(b"keep")
        _ = publish_import(imported, tmp_path)
    assert sentinel is not None and sentinel.read_bytes() == b"keep"
    assert target is not None
    assert tuple(target.iterdir()) == (sentinel,)


@pytest.mark.parametrize(
    "mutation", ("extra", "truncated", "changed", "symlink", "summary", "source")
)
def test_closed_staging_is_verified_before_atomic_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = _source(tmp_path)
    verify = mesh_publication.verify_closed_tree
    first = True

    def corrupt(
        directory: int,
        expected: dict[str, tuple[int, str]],
        check: mesh_publication.Check,
    ) -> None:
        nonlocal first
        access = Path(f"/proc/self/fd/{directory}")
        if first:
            first = False
            if mutation == "extra":
                _ = (access / "extra.txt").write_bytes(b"unexpected")
            elif mutation == "truncated":
                _ = (access / "face-area.bin").write_bytes(b"")
            elif mutation == "changed":
                raw = bytearray((access / "xyz.bin").read_bytes())
                raw[12] ^= 1
                _ = (access / "xyz.bin").write_bytes(raw)
            elif mutation == "symlink":
                (access / "xyz.bin").unlink()
                (access / "xyz.bin").symlink_to(source)
            elif mutation == "summary":
                _ = (access / "summary.json").write_bytes(b"{}\n")
            else:
                _ = (access / "source/observations.ply").write_bytes(b"bad")
        verify(directory, expected, check)

    monkeypatch.setattr(mesh_publication, "verify_closed_tree", corrupt)
    with (
        pytest.raises(MeshImportError),
        prepare_import(source, tmp_path, storage="disk", chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        _ = publish_import(imported, tmp_path)
    assert source.read_bytes().startswith(b"ply\n")
    assert not list(tmp_path.glob("mesh-import-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_changed_summary_object_cannot_publish_with_stale_child_hash(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    with (
        pytest.raises(MeshImportError, match="control child"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        imported.summary["out_of_range_source_corners"] = 100
        _ = publish_import(imported, tmp_path)
    assert not list(tmp_path.glob("mesh-import-*"))


def test_publication_barrier_failure_preserves_import_and_sentinels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    original = rename_no_replace
    first: PublishedStage | None = None

    def fail(
        source_fd: int, source_name: str, target_fd: int, target_name: str
    ) -> None:
        if source_name == "contribution":
            raise OSError(28, "injected disk full at publication")
        original(source_fd, source_name, target_fd, target_name)

    monkeypatch.setattr(mesh_publication, "rename_no_replace", fail)
    sentinel = tmp_path / "keep.txt"
    _ = sentinel.write_bytes(b"keep")
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        first = publish_import(imported, tmp_path)
        _ = publish_contributions(imported, imported.complete_contributions(), tmp_path)
    assert caught.value.category == "resource"
    assert first is not None and first.path.is_dir()
    assert sentinel.read_bytes() == b"keep"
    assert not list(tmp_path.glob("mesh-contribution-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_source_tree_directory_symlink_is_rejected(tmp_path: Path) -> None:
    # Exercise the recursive verifier directly on an unrelated fixture, without
    # asking publication or cleanup to own that fixture's contents.
    root = tmp_path / "root"
    root.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _ = (foreign / "data.bin").write_bytes(b"keep")
    (root / "source").symlink_to(foreign, target_is_directory=True)
    held = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError):
            mesh_publication.verify_closed_tree(
                held,
                {"source/data.bin": (4, hashlib.sha256(b"keep").hexdigest())},
                lambda: None,
            )
    finally:
        os.close(held)
    assert (foreign / "data.bin").read_bytes() == b"keep"


def test_replaced_publication_destination_does_not_receive_a_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    destination, moved = tmp_path / "destination", tmp_path / "moved"
    destination.mkdir()
    verify = mesh_publication.verify_closed_tree
    replaced = False

    def replace(
        directory: int,
        expected: dict[str, tuple[int, str]],
        check: mesh_publication.Check,
    ) -> None:
        nonlocal replaced
        verify(directory, expected, check)
        if not replaced:
            replaced = True
            _ = destination.rename(moved)
            destination.mkdir()
            _ = (destination / "keep.txt").write_bytes(b"unrelated")

    monkeypatch.setattr(mesh_publication, "verify_closed_tree", replace)
    with (
        pytest.raises(MeshImportError, match="destination path changed"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        _ = publish_import(imported, destination)
    assert (destination / "keep.txt").read_bytes() == b"unrelated"
    assert {path.name for path in destination.iterdir()} == {"keep.txt"}
    assert not list(moved.iterdir())
    assert not list(tmp_path.glob(".scansor-mesh-*"))
