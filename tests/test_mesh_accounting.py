from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import cast

import pytest

from scansor import mesh_accounting
from scansor.mesh_accounting import account_import
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, control_id
from scansor.mesh_corner_storage import CornerStaging
from scansor.mesh_errors import MeshImportError
from scansor.mesh_import import prepare_import
from scansor.mesh_numeric import NumericProfileError
from scansor.mesh_policy import contribution_request
from scansor.mesh_recipes import SMALL_RECIPES, SmallRecipe, write_recipe
from scansor.mesh_resources import MIB
from tests import mesh_rounding_oracle as oracle


def _canonical(word: int) -> int:
    magnitude = word & 0x7FFFFFFF
    return 0 if magnitude == 0 else 0x7FC00000 if magnitude > 0x7F800000 else word


def independent_artifacts(recipe: SmallRecipe) -> dict[str, bytes]:
    """Independent standard-library source/disposition/rounding/normalization path."""
    vertex_codes = [
        int(any(word & 0x7FFFFFFF >= 0x7F800000 for word in xyz))
        for xyz in recipe.xyz_bits
    ]
    normal_codes = (
        [0] * recipe.vertex_count
        if recipe.normal_bits is None
        else [
            3
            if any(word & 0x7FFFFFFF >= 0x7F800000 for word in row)
            else 2
            if all(word & 0x7FFFFFFF == 0 for word in row)
            else 1
            for row in recipe.normal_bits
        ]
    )
    counts = [0] * recipe.vertex_count
    vertex_areas = [0] * recipe.vertex_count
    face_codes: list[int] = []
    face_areas: list[int] = []
    for face in recipe.triangles:
        for index in face:
            if 0 <= index < recipe.vertex_count:
                counts[index] += 1
        invalid = any(not 0 <= index < recipe.vertex_count for index in face)
        nonfinite = not invalid and any(vertex_codes[index] for index in face)
        area = 0
        if invalid:
            code = 1
        elif nonfinite:
            code = 2
        elif len(set(face)) != 3:
            code = 3
        else:
            corners = [
                [
                    oracle.bits(float(struct.unpack("<f", struct.pack("<I", word))[0]))
                    for word in recipe.xyz_bits[index]
                ]
                for index in face
            ]
            area = oracle.face_area(corners)
            code = 4 if area == 0 else 0
        face_codes.append(code)
        face_areas.append(area)
        if code == 0:
            third = oracle.divide(area, oracle.bits(3.0))
            for index in face:
                vertex_areas[index] = oracle.add(vertex_areas[index], third)
    status = [
        1 if vertex_codes[i] else 2 if counts[i] == 0 else 3 if area == 0 else 0
        for i, area in enumerate(vertex_areas)
    ]
    eligible = status.count(0)
    total = oracle.fold(vertex_areas)
    weights = [
        0
        if status[i]
        else oracle.divide(oracle.multiply(area, oracle.bits(float(eligible))), total)
        for i, area in enumerate(vertex_areas)
    ]
    result = {
        "xyz.bin": b"".join(
            struct.pack("<III", *(_canonical(word) for word in row))
            for row in recipe.xyz_bits
        ),
        "vertex-status.bin": bytes(vertex_codes),
        "normal-status.bin": bytes(normal_codes),
        "reference-count.bin": b"".join(struct.pack("<Q", count) for count in counts),
        "triangles.bin": b"".join(
            struct.pack("<iii", *row) for row in recipe.triangles
        ),
        "face-status.bin": bytes(face_codes),
        "face-area.bin": b"".join(struct.pack("<Q", area) for area in face_areas),
        "contribution-status.bin": bytes(status),
        "vertex-area.bin": b"".join(struct.pack("<Q", area) for area in vertex_areas),
        "weight.bin": b"".join(struct.pack("<Q", weight) for weight in weights),
    }
    if recipe.normal_bits is not None:
        result["normals.bin"] = b"".join(
            struct.pack("<III", *(_canonical(word) for word in row))
            for row in recipe.normal_bits
        )
    return result


def _bytes(column: Column, chunk: int) -> bytes:
    return b"".join(
        column.read_range(start, min(start + chunk, column.spec.rows)).tobytes()
        for start in range(0, column.spec.rows, chunk)
    )


def _row_hash(tag: str, count: int, columns: tuple[bytes, ...]) -> str:
    digest = hashlib.sha256(tag.encode("ascii") + b"\0" + struct.pack("<Q", count))
    widths = [len(column) // count for column in columns] if count else []
    for index in range(count):
        digest.update(struct.pack("<Q", index))
        for column, width in zip(columns, widths, strict=True):
            digest.update(column[index * width : (index + 1) * width])
    return digest.hexdigest()


@pytest.mark.parametrize(
    "name", tuple(name for name in SMALL_RECIPES if name != "wrong-list-count-v1")
)
def test_full_accounting_independent_bytes_and_ids_across_storage_chunks(
    tmp_path: Path, name: str
) -> None:
    recipe = SMALL_RECIPES[name]
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, recipe)
    expected = independent_artifacts(recipe)
    identities: list[tuple[str, str]] = []
    for storage, budget, chunk in (
        ("ram", 512, 1),
        ("disk", 2048, 2),
        ("ram", 2048, 7),
        ("disk", 512, 127),
    ):
        with (
            prepare_import(
                source,
                tmp_path,
                storage=storage,
                budget_bytes=budget * MIB,
                chunk_rows=chunk,
            ) as data,
            account_import(data) as imported,
        ):
            assert imported.inventory["status"] == "complete"
            assert "pending_columns" not in imported.inventory
            with pytest.raises(MeshImportError, match="sealed"):
                _ = imported.contribution_columns["weight.bin"].inventory()
            contributions = imported.complete_contributions()
            identities.append((imported.identity, contributions.identity))
            for filename, column in (data.columns | contributions.columns).items():
                assert _bytes(column, chunk) == expected[filename], filename
            vertex_names = (
                "xyz.bin",
                *(("normals.bin",) if recipe.normal_bits is not None else ()),
                "vertex-status.bin",
                "normal-status.bin",
                "reference-count.bin",
            )
            assert imported.inventory["row_digests"] == {
                "vertices": _row_hash(
                    "mesh-import-vertices-v1",
                    recipe.vertex_count,
                    tuple(expected[name] for name in vertex_names),
                ),
                "faces": _row_hash(
                    "mesh-import-faces-v1",
                    recipe.face_count,
                    tuple(
                        expected[name]
                        for name in (
                            "triangles.bin",
                            "face-status.bin",
                            "face-area.bin",
                        )
                    ),
                ),
            }
            assert contributions.inventory["row_digests"] == {
                "vertices": _row_hash(
                    "mesh-contribution-vertices-v1",
                    recipe.vertex_count,
                    tuple(
                        expected[name]
                        for name in (
                            "contribution-status.bin",
                            "vertex-area.bin",
                            "weight.bin",
                        )
                    ),
                )
            }
            assert contributions.inventory["import_id"] == imported.identity
            assert contributions.request["policy"] == "triangle-area-mean-one-v1"
            assert contributions.request["configuration"] == {}
            assert imported.summary["in_range_source_corners"] == sum(
                struct.unpack(
                    f"<{recipe.vertex_count}Q", expected["reference-count.bin"]
                )
            )
            assert imported.summary["out_of_range_source_corners"] == sum(
                not 0 <= index < recipe.vertex_count
                for row in recipe.triangles
                for index in row
            )
            for summary, key, data_bytes in (
                (imported.summary, "usable_face_area_sum", expected["face-area.bin"]),
                (
                    contributions.summary,
                    "eligible_area_sum",
                    expected["vertex-area.bin"],
                ),
                (contributions.summary, "weight_sum", expected["weight.bin"]),
            ):
                total = oracle.fold(
                    list(struct.unpack(f"<{len(data_bytes) // 8}Q", data_bytes))
                )
                measure = cast(dict[str, Control], summary[key])
                assert measure["value_bits"] == f"{total:016x}"
                assert "population" in measure and "unit" in measure
            eligible = expected["contribution-status.bin"].count(0)
            assert contributions.inventory["status"] == (
                "complete" if eligible else "complete-no-eligible-points"
            )
            if not eligible:
                assert (
                    cast(dict[str, Control], contributions.summary["weight_range"])[
                        "minimum_bits"
                    ]
                    is None
                )
            assert data.staging.association_queries == 1
            with pytest.raises(MeshImportError, match="single-use"):
                _ = imported.complete_contributions()
        assert not list(tmp_path.glob(".scansor-mesh-*"))
    assert len(set(identities)) == 1


def test_high_valence_source_order_repeated_winding_and_orphans(tmp_path: Path) -> None:
    base = SMALL_RECIPES["unequal-adjacent-v1"]
    recipe = SmallRecipe(
        "high-valence-v1",
        (*base.xyz_bits, (0, 0, 0)),
        tuple((0, 2, 1) if index % 3 else (0, 1, 3) for index in range(263)),
    )
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, recipe)
    expected = independent_artifacts(recipe)
    ids: list[tuple[str, str]] = []
    for storage, chunk in (("ram", 7), ("disk", 127), ("disk", 4093)):
        with (
            prepare_import(
                source,
                tmp_path,
                storage=storage,
                budget_bytes=512 * MIB,
                chunk_rows=chunk,
            ) as data,
            account_import(data) as imported,
        ):
            result = imported.complete_contributions()
            ids.append((imported.identity, result.identity))
            assert {
                name: _bytes(column, chunk)
                for name, column in (data.columns | result.columns).items()
            } == expected
    assert len(set(ids)) == 1


@pytest.mark.parametrize(
    "mutation",
    ("coordinate", "index", "missing", "duplicate", "allocation", "wrong-vertex"),
)
def test_working_relations_are_bound_to_canonical_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["unequal-adjacent-v1"])
    verify = CornerStaging.verify

    def corrupt(corners: CornerStaging) -> None:
        query = {
            "missing": "DELETE FROM mesh_corners WHERE face=0 AND corner=0",
            "duplicate": "UPDATE mesh_corners SET face=1 WHERE face=0",
            "allocation": "UPDATE mesh_corners SET allocation=0 WHERE face=0",
            "wrong-vertex": "UPDATE mesh_corners SET vertex=3 WHERE face=0 AND corner=0",
        }[mutation]
        _ = corners.data.staging.connection.execute(query)
        verify(corners)

    if mutation not in ("coordinate", "index"):
        monkeypatch.setattr(CornerStaging, "verify", corrupt)
    with (
        pytest.raises(MeshImportError) as caught,
        prepare_import(source, tmp_path, storage="disk", chunk_rows=2) as data,
    ):
        if mutation == "coordinate":
            _ = data.staging.connection.execute(
                "UPDATE vertices SET x=0 WHERE vertex=1"
            )
        elif mutation == "index":
            _ = data.staging.connection.execute("UPDATE faces SET i0=3 WHERE face=0")
        with account_import(data):
            pytest.fail("corrupt working relation accepted")
    assert caught.value.category == "integrity"
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_normalization_failure_preserves_complete_import_data_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["right-triangle-orphan-v1"])

    def fail(*_args: object, **_kwargs: object) -> None:
        raise NumericProfileError("injected unsupported arithmetic")

    with (
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        original_id = imported.identity
        monkeypatch.setattr(mesh_accounting, "normalized_weights", fail)
        with pytest.raises(NumericProfileError, match="injected"):
            _ = imported.complete_contributions()
        assert (
            imported.inventory["status"] == "complete"
            and imported.identity == original_id
        )
        assert all(column.inventory()["sha256"] for column in data.columns.values())
        with pytest.raises(MeshImportError, match="sealed"):
            _ = imported.contribution_columns["weight.bin"].inventory()
        with pytest.raises(MeshImportError, match="single-use"):
            _ = imported.complete_contributions()


def test_accounting_cancellation_cleans_only_owned_work(tmp_path: Path) -> None:
    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["right-triangle-orphan-v1"])
    sentinel = tmp_path / "keep.txt"
    _ = sentinel.write_bytes(b"unrelated")
    with (
        pytest.raises(MeshImportError, match="cancel"),
        prepare_import(source, tmp_path, chunk_rows=2) as data,
        account_import(data) as imported,
    ):
        data.monitor.cancel()
        _ = imported.complete_contributions()
    assert sentinel.read_bytes() == b"unrelated"
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_semantic_ownership_excludes_execution_tuning() -> None:
    from scansor.mesh_controls import implementation_inventory

    for owner in ("importer", "policy"):
        inventory = implementation_inventory(owner)
        assert "mesh_accumulation.py" in str(inventory)
        assert "mesh_digests.py" in str(inventory)
        assert "mesh_accounting.py" not in str(inventory)
        assert "mesh_corner_storage.py" not in str(inventory)
        assert len(control_id(inventory)) == 64


def test_policy_rejects_unknown_configuration() -> None:
    with pytest.raises(MeshImportError, match="empty"):
        _ = contribution_request("a" * 64, configuration={"threshold": 0})
    with pytest.raises(MeshImportError, match="identity"):
        _ = contribution_request("a" * 63)


@pytest.mark.parametrize("cancel", (False, True))
def test_mid_fold_failure_closes_and_cleans_owned_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    from scansor.mesh_accumulation import VertexContributions
    from scansor.mesh_import import ImportFoundation

    source = tmp_path / "input.ply"
    with source.open("wb") as stream:
        write_recipe(stream, SMALL_RECIPES["unequal-adjacent-v1"])
    sentinel = tmp_path / "keep.txt"
    _ = sentinel.write_bytes(b"unrelated")
    write = mesh_accounting._write_fold  # pyright: ignore[reportPrivateUsage]

    def interrupt(
        data: ImportFoundation, columns: dict[str, Column], rows: VertexContributions
    ) -> None:
        if rows.start:
            if cancel:
                data.monitor.cancel()
            else:
                raise MeshImportError("execution", "fold-test", "injected worker error")
        write(data, columns, rows)

    monkeypatch.setattr(mesh_accounting, "_write_fold", interrupt)
    with (
        pytest.raises(MeshImportError, match=r"cancel|injected"),
        prepare_import(source, tmp_path, storage="disk", chunk_rows=1) as data,
        account_import(data),
    ):
        pytest.fail("partial fold accepted")
    assert sentinel.read_bytes() == b"unrelated"
    assert not list(tmp_path.glob(".scansor-mesh-*"))
