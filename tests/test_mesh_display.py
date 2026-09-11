from __future__ import annotations

import errno
import hashlib
import struct
from pathlib import Path
from typing import cast

import numpy as np
import pytest

from scansor import mesh_display
from scansor.mesh_controls import Control, control_id, decode_control
from scansor.mesh_display import export_display
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_display_ply import KINDS, DisplayPlyReader
from scansor.mesh_display_records import PlyOutput
from scansor.mesh_display_staging import DisplayStaging
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import float_bits
from scansor.mesh_recipes import SMALL_RECIPES, SmallRecipe
from scansor.mesh_resources import MIB, ResourceMonitor
from tests import mesh_rounding_oracle as oracle
from tests.test_mesh_accounting import independent_artifacts
from tests.test_mesh_artifacts import artifact_state, published


def expected_views(recipe: SmallRecipe) -> tuple[dict[str, bytes], int, int]:
    """Small-only independent struct/Fraction oracle; no export helpers or NumPy."""
    data = independent_artifacts(recipe)
    finite = [i for i, status in enumerate(data["vertex-status.bin"]) if status == 0]
    usable = [i for i, status in enumerate(data["face-status.bin"]) if status == 0]
    corners = [
        (face, corner, vertex)
        for face, triangle in enumerate(recipe.triangles)
        if data["face-status.bin"][face] != 0
        for corner, vertex in enumerate(triangle)
        if 0 <= vertex < recipe.vertex_count and data["vertex-status.bin"][vertex] == 0
    ]
    areas = [word for (word,) in struct.iter_unpack("<Q", data["vertex-area.bin"])]
    maximum = max(areas, default=0)

    def record(vertex: int, kind: str, face: int = 0, corner: int = 0) -> bytes:
        xyz = struct.unpack_from("<fff", data["xyz.bin"], 12 * vertex)
        status = data["contribution-status.bin"][vertex]
        if kind == "validity":
            colors = {0: (40, 170, 80), 2: (255, 165, 0), 3: (220, 50, 50)}[status]
        elif kind == "weights":
            if status == 0:
                level = round(
                    oracle.rational(
                        oracle.multiply(
                            oracle.bits(255.0), oracle.divide(areas[vertex], maximum)
                        )
                    )
                )
                colors = (level, level, 255 - level)
            else:
                colors = (128, 128, 128)
        else:
            colors = {
                1: (180, 0, 180),
                2: (220, 50, 50),
                3: (255, 165, 0),
                4: (80, 120, 220),
            }[data["face-status.bin"][face]]
        result = struct.pack(
            "<dddBBBfff", *xyz, *colors, 0, data["normal-status.bin"][vertex], status
        )
        result += data["vertex-area.bin"][8 * vertex : 8 * vertex + 8]
        result += data["weight.bin"][8 * vertex : 8 * vertex + 8]
        result += struct.pack(
            "<ffff", *((vertex >> (16 * d)) & 65535 for d in range(4))
        )
        if kind == "rejected-face-corners":
            result += struct.pack(
                "<ffffff",
                *((face >> (16 * d)) & 65535 for d in range(4)),
                corner,
                data["face-status.bin"][face],
            )
        return result

    remap = {source: view for view, source in enumerate(finite)}
    faces = b"".join(
        struct.pack("<Biii", 3, *(remap[v] for v in recipe.triangles[f]))
        for f in usable
    )
    outputs = {
        kind + ".ply": b"".join(record(v, kind) for v in finite) + faces
        for kind in ("validity", "weights")
    }
    outputs["rejected-face-corners.ply"] = b"".join(
        record(v, "rejected-face-corners", f, c) for f, c, v in corners
    )
    outputs["view-vertices.bin"] = b"".join(struct.pack("<Q", v) for v in finite)
    outputs["view-faces.bin"] = b"".join(struct.pack("<Q", f) for f in usable)
    return outputs, len(finite), len(corners)


@pytest.mark.parametrize(
    "name", tuple(n for n in SMALL_RECIPES if n != "wrong-list-count-v1")
)
def test_export_exact_independent_fields_maps_topology_and_invariance(
    tmp_path: Path, name: str
) -> None:
    expected, finite, corners = expected_views(SMALL_RECIPES[name])
    previous: dict[str, bytes] | None = None
    ids: list[str] = []
    for storage, budget, chunk in (("ram", 512, 1), ("disk", 2048, 7)):
        root = tmp_path / storage
        root.mkdir()
        first, second = published(root, name, storage=storage)
        before = artifact_state(first.path), artifact_state(second.path)
        result = export_display(
            first.path,
            second.path,
            root,
            root,
            budget_bytes=budget * MIB,
            chunk_rows=chunk,
            expected_import_id=first.identity,
            expected_contribution_id=second.identity,
        )
        ids.append(result.stage.identity)
        files = {path.name: path.read_bytes() for path in result.stage.path.iterdir()}
        if previous is not None:
            assert files == previous
        previous = files
        assert set(files) == {*expected, "legend.json", "inventory.json"}
        assert result.inventory["source_id"] == result.legend["source_id"]
        assert result.inventory["import_id"] == first.identity
        assert result.inventory["contribution_id"] == second.identity
        assert result.stage.identity == control_id(
            decode_control(files["inventory.json"])
        )
        for field, raw in expected.items():
            payload = (
                files[field].split(b"end_header\n", 1)[1]
                if field.endswith(".ply")
                else files[field]
            )
            assert payload == raw, (name, field)
        for kind in KINDS:
            with (result.stage.path / (kind + ".ply")).open("rb") as stream:
                reader = DisplayPlyReader(stream, kind, chunk_rows=chunk)
                assert reader.vertices == (
                    corners if kind == "rejected-face-corners" else finite
                )
                assert all(
                    reader.fields()[axis] == "double" for axis in ("x", "y", "z")
                )
                reader.validate_all()
        for record in cast(list[dict[str, Control]], result.inventory["files"]):
            raw = files[str(record["name"])]
            assert record["sha256"] == hashlib.sha256(raw).hexdigest()
            assert record["byte_count"] == len(raw)
        assert (artifact_state(first.path), artifact_state(second.path)) == before
        assert not list(root.glob(".scansor-mesh-*"))
    assert len(set(ids)) == 1


def test_transform_changes_only_display_and_is_bound_to_identity(
    tmp_path: Path,
) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    before = artifact_state(first.path), artifact_state(second.path)
    original = export_display(first.path, second.path, tmp_path, tmp_path, chunk_rows=2)
    transform = DisplayTransform((float_bits(1.0), float_bits(2.0), float_bits(3.0)), 2)
    shifted = export_display(
        first.path, second.path, tmp_path, tmp_path, chunk_rows=2, transform=transform
    )
    assert original.stage.identity != shifted.stage.identity
    assert shifted.legend["transform"] == transform.record()
    assert shifted.legend["transform_roundtrip_mismatch_rows"] == {
        "finite_source_vertices": 0,
        "displayable_rejected_corners": 0,
    }
    with (shifted.stage.path / "validity.ply").open("rb") as stream:
        reader = DisplayPlyReader(stream, "validity", chunk_rows=2)
        row = reader.read_range("vertex", 0, 1)
        assert tuple(row[axis][0] for axis in ("x", "y", "z")) == (-4, -8, -12)
    assert (artifact_state(first.path), artifact_state(second.path)) == before


@pytest.mark.parametrize("location", ("source-output", "contribution-scratch"))
def test_export_refuses_output_or_scratch_inside_authoritative_data(
    tmp_path: Path, location: str
) -> None:
    first, second = published(tmp_path, "no-faces-v1")
    before = artifact_state(first.path), artifact_state(second.path)
    with pytest.raises(MeshImportError, match="outside authoritative"):
        _ = export_display(
            first.path,
            second.path,
            first.path if location == "source-output" else tmp_path,
            second.path if location == "contribution-scratch" else tmp_path,
        )
    assert (artifact_state(first.path), artifact_state(second.path)) == before


def test_export_disk_failure_preserves_category_authority_and_unrelated_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    before = artifact_state(first.path), artifact_state(second.path)
    sentinel = tmp_path / "unrelated.txt"
    _ = sentinel.write_bytes(b"keep me")

    def fail_write(
        _self: PlyOutput, _element: str, _start: int, _rows: np.ndarray
    ) -> None:
        raise OSError(errno.ENOSPC, "injected full output filesystem")

    monkeypatch.setattr(PlyOutput, "write", fail_write)
    with pytest.raises(MeshImportError) as caught:
        _ = export_display(first.path, second.path, tmp_path, tmp_path, chunk_rows=2)
    assert caught.value.category == "resource"
    assert sentinel.read_bytes() == b"keep me"
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))
    assert not list(tmp_path.glob("mesh-display-*"))


def test_publication_rejects_changed_staged_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    original = mesh_display.write_views

    def changed(*args: object, **kwargs: object) -> tuple[list[Control], int, int]:
        files, vertices, corners = original(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        path = cast(Path, args[0]) / "validity.ply"
        # Still a valid display record: corrupt one color after its intended hash.
        with path.open("r+b") as stream:
            header = DisplayPlyReader(stream, "validity").layout.header.raw
            _ = stream.seek(len(header) + 24)
            _ = stream.write(b"\x01")
        return files, vertices, corners

    monkeypatch.setattr(mesh_display, "write_views", changed)
    with pytest.raises(MeshImportError, match=r"hash|differ|changed"):
        _ = export_display(first.path, second.path, tmp_path, tmp_path, chunk_rows=2)
    assert not list(tmp_path.glob("mesh-display-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize("target", ("display_vertices", "display_faces"))
def test_export_rejects_changed_working_tuples_before_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    first, second = published(tmp_path, "unequal-adjacent-v1")
    verify = DisplayStaging._verify  # pyright: ignore[reportPrivateUsage]

    def corrupt(
        self: DisplayStaging,
        table: str,
        fields: tuple[tuple[str, str], ...],
        total: int,
        expected: str,
    ) -> None:
        if table == target:
            if target == "display_vertices":
                _ = self.connection.execute(
                    "UPDATE display_vertices SET area = area + 1 WHERE source_id = 0"
                )
            else:
                _ = self.connection.execute(
                    "DELETE FROM display_faces WHERE face_id = 1"
                )
                _ = self.connection.execute(
                    "INSERT INTO display_faces SELECT * FROM display_faces WHERE face_id = 0"
                )
        verify(self, table, fields, total, expected)

    monkeypatch.setattr(DisplayStaging, "_verify", corrupt)
    with pytest.raises(MeshImportError, match="staged tuples"):
        _ = export_display(first.path, second.path, tmp_path, tmp_path, chunk_rows=2)
    assert not list(tmp_path.glob("mesh-display-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_display_cancellation_cleans_staging_and_preserves_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = published(tmp_path, "unequal-adjacent-v1")
    before = artifact_state(first.path), artifact_state(second.path)

    progress = ResourceMonitor.progress

    def cancel(self: ResourceMonitor, phase: str, completed: int, total: int) -> None:
        progress(self, phase, completed, total)
        if phase == "export-display-vertices":
            self.cancel()
            self.check()

    monkeypatch.setattr(ResourceMonitor, "progress", cancel)
    with pytest.raises(MeshImportError) as caught:
        _ = export_display(first.path, second.path, tmp_path, tmp_path, chunk_rows=1)
    assert caught.value.category == "cancelled"
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert not list(tmp_path.glob("mesh-display-*"))
    assert not list(tmp_path.glob(".scansor-mesh-*"))
