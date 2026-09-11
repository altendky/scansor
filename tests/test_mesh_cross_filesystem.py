from __future__ import annotations

import errno
import tempfile
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from scansor import mesh_publication_copy
from scansor.mesh_accounting import account_import
from scansor.mesh_controls import Control, decode_control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_publication import publish_contributions, publish_import
from scansor.mesh_recipes import SMALL_RECIPES
from scansor.mesh_replay import verify_mesh
from scansor.mesh_supervisor import discover_staging, run_worker
from tests.test_mesh_accounting import independent_artifacts
from tests.test_mesh_supervisor import control_object, fault_command, worker_request


@pytest.fixture
def output_filesystem(tmp_path: Path) -> Generator[Path]:
    base = Path("/dev/shm")
    if not base.is_dir():
        pytest.skip("a separate writable Linux tmpfs is required")
    with tempfile.TemporaryDirectory(prefix="scansor-publish-test-", dir=base) as raw:
        directory = Path(raw)
        if directory.stat().st_dev == tmp_path.stat().st_dev:
            pytest.skip("test requires genuinely different filesystems")
        yield directory


@pytest.mark.parametrize("storage", ("ram", "disk"))
def test_direct_publication_copies_closed_columns_to_output_filesystem(
    tmp_path: Path, output_filesystem: Path, storage: str
) -> None:
    request = worker_request(tmp_path)
    with (
        prepare_import(request.source, tmp_path, storage=storage, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        first = publish_import(imported, output_filesystem)
        second = publish_contributions(
            imported, imported.complete_contributions(), output_filesystem
        )
    expected = independent_artifacts(SMALL_RECIPES["unequal-adjacent-v1"])
    for name, raw in expected.items():
        path = first.path / name
        if not path.exists():
            path = second.path / name
        assert path.read_bytes() == raw
    assert (
        verify_mesh(
            first.path,
            tmp_path,
            contribution_path=second.path,
            expected_import_id=first.identity,
            expected_contribution_id=second.identity,
            chunk_rows=7,
        )["status"]
        == "verified"
    )
    assert not list(output_filesystem.glob(".scansor-*"))
    assert not list(tmp_path.glob(".scansor-*"))


def test_supervised_cross_filesystem_publication_has_identical_stage_ids(
    tmp_path: Path, output_filesystem: Path
) -> None:
    request = worker_request(tmp_path)
    first = run_worker(request, tmp_path)
    second = run_worker(replace(request, destination=output_filesystem), tmp_path)
    assert first["status"] == second["status"] == "complete"
    before = cast(list[dict[str, Control]], first["published"])
    after = cast(list[dict[str, Control]], second["published"])
    assert [stage["identity"] for stage in before] == [
        stage["identity"] for stage in after
    ]
    assert not list(output_filesystem.glob(".scansor-*"))
    assert not list(tmp_path.glob(".scansor-*"))


@pytest.mark.parametrize("mode", ("crash-copy", "crash-after-import"))
def test_killed_worker_cleans_both_filesystems_and_keeps_completed_import(
    tmp_path: Path, output_filesystem: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    fault_command(monkeypatch, mode)
    request = replace(worker_request(tmp_path), destination=output_filesystem)
    _ = (output_filesystem / "keep.txt").write_bytes(b"unrelated")
    result = run_worker(request, tmp_path)
    assert result["status"] == "failed"
    assert control_object(result["failure"])["category"] == "worker-exit"
    stages = cast(list[dict[str, Control]], result["published"])
    assert len(stages) == (1 if mode == "crash-after-import" else 0)
    if stages:
        assert (
            verify_mesh(Path(str(stages[0]["path"])), tmp_path)["status"] == "verified"
        )
    assert (output_filesystem / "keep.txt").read_bytes() == b"unrelated"
    assert not list(output_filesystem.glob(".scansor-*"))
    assert not list(output_filesystem.glob("mesh-contribution-*"))
    assert not list(tmp_path.glob(".scansor-*"))


@pytest.mark.parametrize("failure", ("corruption", "disk-full"))
def test_failed_destination_copy_never_publishes(
    tmp_path: Path,
    output_filesystem: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    copy = mesh_publication_copy.copy_stage_files

    def fail(
        source: int,
        target: int,
        expected: dict[str, tuple[int, str]],
        check: mesh_publication_copy.Check,
    ) -> None:
        copy(source, target, expected, check)
        if "xyz.bin" in expected:
            if failure == "disk-full":
                raise OSError(
                    errno.ENOSPC, "injected destination filesystem exhaustion"
                )
            path = Path(f"/proc/self/fd/{target}") / "xyz.bin"
            raw = bytearray(path.read_bytes())
            raw[12] ^= 1
            _ = path.write_bytes(raw)

    monkeypatch.setattr(mesh_publication_copy, "copy_stage_files", fail)
    request = worker_request(tmp_path)
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(request.source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        _ = publish_import(imported, output_filesystem)
    assert caught.value.category == (
        "resource" if failure == "disk-full" else "integrity"
    )
    assert not list(output_filesystem.iterdir())
    assert not list(tmp_path.glob(".scansor-*"))


def test_cross_filesystem_publication_never_replaces_existing_output(
    tmp_path: Path, output_filesystem: Path
) -> None:
    request = worker_request(tmp_path)
    with (
        prepare_import(request.source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        existing = output_filesystem / ("mesh-import-" + imported.identity)
        existing.mkdir()
        _ = (existing / "keep.txt").write_bytes(b"unrelated")
        with pytest.raises(MeshImportError, match="already exists"):
            _ = publish_import(imported, output_filesystem)
    assert (existing / "keep.txt").read_bytes() == b"unrelated"
    assert {path.name for path in output_filesystem.iterdir()} == {existing.name}


def test_failed_cross_filesystem_retention_reports_both_owned_locations(
    tmp_path: Path, output_filesystem: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault_command(monkeypatch, "crash-copy")
    result = run_worker(
        replace(worker_request(tmp_path), destination=output_filesystem),
        tmp_path,
        retain_incomplete=True,
    )
    assert result["status"] == "failed"
    for key, parent in (
        ("incomplete_directory", tmp_path),
        ("publication_incomplete_directory", output_filesystem),
    ):
        path = Path(str(result[key]))
        assert path.parent == parent
        assert (
            control_object(decode_control((path / "failure.json").read_bytes()))[
                "status"
            ]
            == "incomplete"
        )
        entries = cast(list[dict[str, Control]], discover_staging(parent)["entries"])
        assert len(entries) == 1 and entries[0]["state"] == "stale"
