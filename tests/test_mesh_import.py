from __future__ import annotations

import errno
import hashlib
import json
import shutil
import struct
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import duckdb
import numpy as np
import pytest

from scansor import mesh_resources, mesh_workspace
from scansor.files import rename_no_replace
from scansor.mesh_columns import Column
from scansor.mesh_controls import (
    Control,
    control_id,
    encode_control,
    implementation_inventory,
)
from scansor.mesh_dispositions import face_dispositions
from scansor.mesh_duckdb import DuckStaging, duckdb_failure
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_recipes import SMALL_RECIPES, GridRecipe, write_recipe
from scansor.mesh_resources import MIB, AllocationLedger, ResourceMonitor, plan_memory
from scansor.mesh_workspace import remove_owned_workspace


def _canonical_word(word: int) -> int:
    magnitude = word & 0x7FFFFFFF
    return 0 if magnitude == 0 else 0x7FC00000 if magnitude > 0x7F800000 else word


@pytest.mark.parametrize(
    "name", tuple(name for name in SMALL_RECIPES if name != "wrong-list-count-v1")
)
@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
def test_foundation_every_source_row_across_storage_and_budget(
    tmp_path: Path, name: str, chunk: int
) -> None:
    recipe = SMALL_RECIPES[name]
    source = tmp_path / "source.ply"
    with source.open("wb") as stream:
        write_recipe(stream, recipe)
    identities: list[str] = []
    expected_xyz = b"".join(
        struct.pack("<III", *(_canonical_word(word) for word in row))
        for row in recipe.xyz_bits
    )
    expected_faces = b"".join(struct.pack("<iii", *row) for row in recipe.triangles)
    for storage, budget in (("ram", 512 * MIB), ("disk", 1024 * MIB)):
        with prepare_import(
            source,
            tmp_path,
            storage=storage,
            budget_bytes=budget,
            chunk_rows=chunk,
            io_block_bytes=7,
        ) as data:
            assert (
                data.vertices == recipe.vertex_count and data.faces == recipe.face_count
            )
            inventory = data.inventory()
            identities.append(control_id(inventory))
            assert inventory["status"] == "foundation-ready"
            assert inventory["physical_unit"] == "unknown"
            assert inventory["normal_convention"] == "unverified-exporter-components"
            assert inventory["pending_columns"] == [
                "face-area.bin",
                "face-status.bin",
                "reference-count.bin",
            ]
            for key, expected in (
                ("xyz.bin", expected_xyz),
                ("triangles.bin", expected_faces),
            ):
                column = data.columns[key]
                actual = b"".join(
                    column.read_range(
                        start, min(start + chunk, column.spec.rows)
                    ).tobytes()
                    for start in range(0, column.spec.rows, chunk)
                )
                assert actual == expected
                assert (
                    column.inventory()["sha256"] == hashlib.sha256(expected).hexdigest()
                )
            if recipe.normal_bits is not None:
                expected_normals = b"".join(
                    struct.pack("<III", *(_canonical_word(word) for word in row))
                    for row in recipe.normal_bits
                )
                column = data.columns["normals.bin"]
                actual = b"".join(
                    column.read_range(
                        start, min(start + chunk, column.spec.rows)
                    ).tobytes()
                    for start in range(0, column.spec.rows, chunk)
                )
                assert actual == expected_normals
            for key in ("face-area.bin", "face-status.bin", "reference-count.bin"):
                with pytest.raises(MeshImportError, match="sealed"):
                    _ = data.columns[key].inventory()
            seen = 0
            for batch in data.staging.associated_faces():
                assert batch.start == seen and len(batch.indices) <= chunk
                assert (
                    not batch.indices.flags.writeable
                    and not batch.corners.flags.writeable
                )
                for local, triangle in enumerate(batch.indices):
                    assert triangle.tolist() == list(recipe.triangles[seen + local])
                    for corner, index in enumerate(triangle):
                        words = (
                            tuple(
                                _canonical_word(word) for word in recipe.xyz_bits[index]
                            )
                            if 0 <= index < recipe.vertex_count
                            else (0, 0, 0)
                        )
                        assert batch.corners[local, corner].tobytes() == struct.pack(
                            "<III", *words
                        )
                status, areas = face_dispositions(
                    batch.indices,
                    batch.corners,
                    vertices=recipe.vertex_count,
                    start=batch.start,
                )
                assert len(status) == len(areas) == len(batch.indices)
                assert np.all(np.isfinite(areas))
                seen += len(batch.indices)
                del batch
            assert seen == recipe.face_count
            assert data.staging.association_queries == 1
            report = data.execution_report()
            assert report["plan"] != inventory
            assert data.plan.reserved_bytes <= budget
            assert data.ledger.high_water <= data.ledger.capacity
            directory = data.directory
        assert not directory.exists()
        assert data.source.ply.stream.closed
        assert data.ledger.current == 0
        with pytest.raises(MeshImportError, match="closed"):
            _ = data.columns["xyz.bin"].read_range(0, 1)
    assert identities[0] == identities[1]
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_sidecar_interpretation_is_bound_but_paths_and_execution_are_not(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(17, 19, noise_bits=3))
    sidecar = tmp_path / "input.rsInfo"
    _ = sidecar.write_bytes(
        b'<Model transformToModel="not calibration" future="unknown"/>'
    )
    identities: list[str] = []
    for storage, chunk in (("ram", 7), ("disk", 127)):
        with prepare_import(
            source, tmp_path, sidecar=sidecar, storage=storage, chunk_rows=chunk
        ) as data:
            record = data.inventory()
            assert data.sidecar["status"] == "partially-interpreted-unknown-fields"
            assert str(tmp_path).encode() not in encode_control(record)
            identities.append(control_id(record))
            count = 0
            for part in data.staging.associated_faces():
                count += len(part.indices)
                status, area = face_dispositions(
                    part.indices, part.corners, vertices=data.vertices
                )
                assert not np.any(status)
                assert np.all(area > 0)
                del part
            assert count == data.faces
    assert identities[0] == identities[1]
    with prepare_import(source, tmp_path, chunk_rows=7) as data:
        assert control_id(data.inventory()) != identities[0]


@pytest.mark.parametrize(
    "mutation", ("vertex-duplicate", "vertex-missing", "face-duplicate", "face-missing")
)
def test_staging_verifies_ids_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(3, 3))
    original = DuckStaging.finish

    def corrupted(stage: DuckStaging) -> None:
        table, key = (
            ("vertices", "vertex")
            if mutation.startswith("vertex")
            else ("faces", "face")
        )
        if mutation.endswith("duplicate"):
            _ = stage.connection.execute(f"UPDATE {table} SET {key}=0 WHERE {key}=1")
        else:
            _ = stage.connection.execute(f"DELETE FROM {table} WHERE {key}=1")
        original(stage)

    monkeypatch.setattr(DuckStaging, "finish", corrupted)
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path, chunk_rows=2),
    ):
        pytest.fail("corrupt working IDs were accepted")
    assert caught.value.category == "integrity"
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_missing_lookup_and_repeated_operations(tmp_path: Path) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["right-triangle-orphan-v1"])
    with prepare_import(source, tmp_path, chunk_rows=1) as data:
        for _attempt in range(2):
            for part in data.staging.associated_faces():
                assert part.indices.tolist() == [[0, 1, 2]]
                del part
        assert data.staging.association_queries == 2
        _ = data.staging.connection.execute("DELETE FROM vertices WHERE vertex=1")
        with pytest.raises(MeshImportError, match="incorrect coordinate lookup"):
            _ = list(data.staging.associated_faces())


@pytest.mark.parametrize("retain", (False, True))
def test_resource_failure_retention_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retain: bool
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(2, 2))
    sentinel = tmp_path / "unrelated"
    sentinel.mkdir()
    _ = (sentinel / "keep").write_bytes(b"unchanged")

    def disk_full(_self: Column, _start: int, _values: np.ndarray) -> None:
        raise OSError(errno.ENOSPC, "injected full disk")

    monkeypatch.setattr(Column, "write_range", disk_full)
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path, storage="disk", retain_incomplete=retain),
    ):
        pytest.fail("disk full produced a foundation")
    assert caught.value.category == "resource"
    assert (sentinel / "keep").read_bytes() == b"unchanged"
    retained = list(tmp_path.glob(".scansor-mesh-*"))
    assert len(retained) == int(retain)
    if retain:
        report = json.loads((retained[0] / "failure.json").read_bytes())
        assert report["status"] == "incomplete" and report["category"] == "resource"
        assert not (retained[0] / "import" / "inventory.json").exists()


def test_structural_failure_and_insufficient_budget_are_not_dispositions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["wrong-list-count-v1"])
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path, chunk_rows=1),
    ):
        pytest.fail("bad list count reached foundation")
    assert caught.value.category == "unsupported"
    with (
        pytest.raises(MeshImportError, match="resource-budget-too-small"),
        prepare_import(source, tmp_path, budget_bytes=1),
    ):
        pytest.fail("insufficient memory reached source processing")
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_memory_plan_and_managed_reservations() -> None:
    arguments: dict[str, Any] = dict(
        budget_bytes=512 * MIB,
        baseline_bytes=128 * MIB,
        canonical_bytes=39 * 60_000_000 + 21 * 119_968_002,
        vertices=60_000_000,
        faces=119_968_002,
        source_bytes=2_300_000_000,
    )
    plan = plan_memory(**arguments)
    assert plan.storage == "disk"
    assert plan.reserved_bytes <= plan.budget_bytes
    assert plan.engine_bytes + plan.baseline_bytes < plan.reserved_bytes
    assert (
        plan.disk_estimate_bytes
        > arguments["canonical_bytes"] + arguments["source_bytes"]
    )
    with pytest.raises(MeshImportError):
        _ = plan_memory(**arguments, storage="ram")
    for invalid in (True, 0, -1, 65_537):
        with pytest.raises(MeshImportError):
            _ = plan_memory(**arguments, chunk_rows=invalid)
    ledger = AllocationLedger(100)
    with ledger.reserve("a", 60):
        assert ledger.current == 60
        with pytest.raises(MeshImportError), ledger.reserve("b", 41):
            pytest.fail("over-reservation accepted")
        with ledger.reserve("b", 40):
            assert ledger.high_water == 100
    assert ledger.current == 0 and ledger.high_water == 100


def test_monitor_interrupts_native_work_and_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exceed = threading.Event()
    interrupted = threading.Event()
    reported = threading.Event()
    events: list[dict[str, Control]] = []

    def memory() -> dict[str, Control]:
        value = 300 if exceed.is_set() else 100
        return {"rss_bytes": value, "os_peak_rss_bytes": value}

    def report(event: dict[str, Control]) -> None:
        events.append(event)
        reported.set()

    monkeypatch.setattr(mesh_resources, "memory_snapshot", memory)
    with (
        pytest.raises(MeshImportError, match="exceeds budget"),
        ResourceMonitor(
            200, tmp_path, callback=report, sample_seconds=0.005, report_seconds=0.01
        ) as monitor,
    ):
        monitor.set_interrupt(interrupted.set)
        monitor.progress("blocking-native-query", 0, 20)
        assert reported.wait(1)
        exceed.set()
        assert interrupted.wait(1)
        monitor.check()
    assert events and monitor.record()["peak_rss_bytes"] == 300
    assert not any(
        thread.name == "scansor-mesh-monitor" for thread in threading.enumerate()
    )


def test_cancelled_association_closes_reader_connection_and_workspace(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(10, 10))
    with (
        pytest.raises(MeshImportError, match="cancelled"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        closing(data.staging.associated_faces()) as batches,
    ):
        first = next(batches)
        assert first.start == 0
        del first
        data.monitor.cancel()
        _ = next(batches)
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_execution_modules_are_outside_semantic_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = implementation_inventory("importer")
    files = cast(list[dict[str, Control]], before["files"])
    names = [cast(str, entry["name"]) for entry in files]
    assert "mesh_semantics.py" in names and "mesh_sidecar.py" in names
    assert not set(names) & {
        "mesh_duckdb.py",
        "mesh_resources.py",
        "mesh_columns.py",
        "mesh_import.py",
    }
    import duckdb

    monkeypatch.setattr(duckdb, "__version__", "execution-only-test-version")
    assert before == implementation_inventory("importer")


@pytest.mark.parametrize("role", ("ply", "sidecar"))
def test_snapshot_mutation_during_decode_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    source, sidecar = tmp_path / "input.ply", tmp_path / "input.rsInfo"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(3, 3))
    _ = sidecar.write_bytes(b"<Model/>")
    original = DuckStaging.stage_vertices

    def mutate(stage: DuckStaging, start: int, xyz: np.ndarray) -> None:
        original(stage, start, xyz)
        if start != 0:
            return
        private = stage.monitor.directory / "import" / "source"
        if role == "ply":
            path = private / "observations.ply"
            raw = bytearray(path.read_bytes())
            offset = raw.index(b"end_header\n") + len(b"end_header\n")
            raw[offset : offset + 4] = struct.pack("<f", 2.0)
            _ = path.write_bytes(raw)
        else:
            _ = (private / "observations.rsInfo").write_bytes(b"<Bogus/>")

    monkeypatch.setattr(DuckStaging, "stage_vertices", mutate)
    with (
        pytest.raises(MeshImportError, match="private snapshot changed"),
        prepare_import(source, tmp_path, sidecar=sidecar, chunk_rows=1),
    ):
        pytest.fail("changed source bytes reached a foundation")
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_inventory_rechecks_snapshot_after_foundation_is_ready(tmp_path: Path) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(2, 2))
    with prepare_import(source, tmp_path, chunk_rows=1) as data:
        _ = data.source.ply.path.write_bytes(b"changed")
        with pytest.raises(MeshImportError, match="private snapshot changed"):
            _ = data.inventory()


def test_cleanup_does_not_resolve_public_path_during_recursive_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "owned"
    root.mkdir()
    _ = (root / "owned-data").write_bytes(b"disposable")
    info = root.stat()
    original = shutil.rmtree

    def swapped(path: str, *, dir_fd: int | None = None) -> None:
        # The owned root has already moved into the private quarantine. Replacing
        # its original public path now must not redirect recursive deletion.
        root.mkdir()
        _ = (root / "unrelated").write_bytes(b"keep")
        original(path, dir_fd=dir_fd, onexc=None)

    monkeypatch.setattr(shutil, "rmtree", swapped)
    # Preserve the native implementation's descriptor-safety capability marker.
    monkeypatch.setattr(swapped, "avoids_symlink_attacks", True, raising=False)
    remove_owned_workspace(root, (info.st_dev, info.st_ino))
    assert (root / "unrelated").read_bytes() == b"keep"
    assert not list(tmp_path.glob(".scansor-cleanup-*"))


def test_cleanup_verifies_entry_after_atomic_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, moved = tmp_path / "owned", tmp_path / "moved"
    root.mkdir()
    _ = (root / "original").write_bytes(b"owned")
    info = root.stat()
    original = rename_no_replace
    calls = 0

    def swapped(source_fd: int, source: str, target_fd: int, target: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            _ = root.rename(moved)
            root.mkdir()
            _ = (root / "unrelated").write_bytes(b"keep")
        original(source_fd, source, target_fd, target)

    monkeypatch.setattr(mesh_workspace, "rename_no_replace", swapped)
    with pytest.raises(MeshImportError, match="replaced"):
        remove_owned_workspace(root, (info.st_dev, info.st_ino))
    assert (root / "unrelated").read_bytes() == b"keep"
    assert (moved / "original").read_bytes() == b"owned"
    assert not list(tmp_path.glob(".scansor-cleanup-*"))


@pytest.mark.parametrize(
    ("message", "category"),
    (
        ("Could not write file (OS error: No space left on device)", "resource"),
        ("Disk quota exceeded", "resource"),
        ("There is not enough space on the disk", "resource"),
        ("max_temp_directory_size exceeded", "resource"),
        ("Permission denied", "execution"),
        ("Invalid database file", "execution"),
    ),
)
def test_duckdb_capacity_errors_are_resource_failures(
    message: str, category: str
) -> None:
    assert duckdb_failure(duckdb.IOException(message), "staging").category == category


def test_duckdb_connect_disk_exhaustion_keeps_resource_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, GridRecipe(2, 2))
    original = duckdb.connect

    def full(*args: Any, **kwargs: Any) -> Any:
        if args and str(args[0]).endswith("stage.duckdb"):
            raise duckdb.IOException("Could not write file: No space left on device")
        return original(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", full)
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path),
    ):
        pytest.fail("disk exhaustion reached foundation")
    assert caught.value.category == "resource"
    assert not list(tmp_path.glob(".scansor-mesh-*"))
