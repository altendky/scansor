from __future__ import annotations

import hashlib
import io
import struct
from fractions import Fraction
from pathlib import Path

import pytest

from experiments.mesh_scale_grid import (
    GridExpectation,
    area_table,
    build_grid_expectation,
)
from scansor.mesh_accounting import account_import
from scansor.mesh_import import prepare_import
from scansor.mesh_recipes import GridRecipe, SmallRecipe, write_recipe
from tests import mesh_rounding_oracle as oracle
from tests.test_mesh_accounting import independent_artifacts


def _row_hash(tag: str, count: int, columns: tuple[bytes, ...]) -> str:
    digest = hashlib.sha256(tag.encode("ascii") + b"\0" + struct.pack("<Q", count))
    strides = tuple(len(column) // count for column in columns)
    for index in range(count):
        digest.update(struct.pack("<Q", index))
        for column, stride in zip(columns, strides, strict=True):
            digest.update(column[index * stride : (index + 1) * stride])
    return digest.hexdigest()


def test_grid_area_table_matches_ordered_fraction_geometry_for_every_slope() -> None:
    areas, thirds = area_table()
    for x in range(-7, 8):
        for y in range(-7, 8):
            dx, dy = Fraction(x, 128), Fraction(y, 128)
            for coordinates in (
                ((0, 0, 0), (3, 0, dx), (0, 4, dy)),
                ((3, 0, 0), (3, 4, dy), (0, 4, dy - dx)),
            ):
                corners = [
                    [oracle.rounded(Fraction(value)) for value in row]
                    for row in coordinates
                ]
                word = oracle.face_area(corners)
                assert int(areas[abs(x), abs(y)]) == word
                assert int(thirds[abs(x), abs(y)]) == oracle.divide(
                    word, oracle.bits(3.0)
                )
    assert not areas.flags.writeable and not thirds.flags.writeable


@pytest.mark.parametrize("width,height", ((2, 2), (3, 3), (17, 19)))
@pytest.mark.parametrize("noisy", (False, True))
def test_full_grid_expectations_match_independent_fraction_bytes(
    tmp_path: Path, width: int, height: int, noisy: bool
) -> None:
    grid = GridRecipe(width, height, noise_bits=3 if noisy else 0)
    small = SmallRecipe(
        "grid-oracle-comparison-v1",
        tuple(
            (int(row[0]), int(row[1]), int(row[2]))
            for row in grid.vertices(0, grid.vertex_count).view("<u4")
        ),
        tuple(
            (int(row[0]), int(row[1]), int(row[2]))
            for row in grid.faces(0, grid.face_count)
        ),
    )
    expected = independent_artifacts(small)
    ply = oracle.independent_ply(list(small.xyz_bits), list(small.triangles))
    actual_source = io.BytesIO()
    write_recipe(actual_source, grid, chunk_rows=7)
    assert actual_source.getvalue() == ply
    results: list[GridExpectation] = []
    for chunk in (3, 127):
        collected: dict[str, bytearray] = {}

        def collect(
            name: str, data: bytes, output: dict[str, bytearray] = collected
        ) -> None:
            output.setdefault(name, bytearray()).extend(data)

        result = build_grid_expectation(
            width, height, tmp_path, noisy=noisy, chunk_rows=chunk, column_sink=collect
        )
        results.append(result)
        assert collected == expected
        assert result.source.sha256 == hashlib.sha256(ply).hexdigest()
        assert result.source.bytes == len(ply)
        assert result.in_range_source_corners == grid.face_count * 3
        for name, payload in expected.items():
            assert result.columns[name].bytes == len(payload)
            assert result.columns[name].sha256 == hashlib.sha256(payload).hexdigest()
        assert result.row_digests == {
            "import_vertices": _row_hash(
                "mesh-import-vertices-v1",
                grid.vertex_count,
                tuple(
                    expected[name]
                    for name in (
                        "xyz.bin",
                        "vertex-status.bin",
                        "normal-status.bin",
                        "reference-count.bin",
                    )
                ),
            ),
            "import_faces": _row_hash(
                "mesh-import-faces-v1",
                grid.face_count,
                tuple(
                    expected[name]
                    for name in ("triangles.bin", "face-status.bin", "face-area.bin")
                ),
            ),
            "contribution_vertices": _row_hash(
                "mesh-contribution-vertices-v1",
                grid.vertex_count,
                tuple(
                    expected[name]
                    for name in (
                        "contribution-status.bin",
                        "vertex-area.bin",
                        "weight.bin",
                    )
                ),
            ),
        }
        for name, actual in (
            ("face-area.bin", result.face_area_sum_bits),
            ("vertex-area.bin", result.vertex_area_sum_bits),
            ("weight.bin", result.weight_sum_bits),
        ):
            words = [word for (word,) in struct.iter_unpack("<Q", expected[name])]
            assert actual == f"{oracle.fold(words):016x}"
        for name, actual in (
            ("vertex-area.bin", result.vertex_area_range_bits),
            ("weight.bin", result.weight_range_bits),
        ):
            words = [word for (word,) in struct.iter_unpack("<Q", expected[name])]
            assert actual == (f"{min(words):016x}", f"{max(words):016x}")
    assert results[0] == results[1]
    assert list(tmp_path.iterdir()) == []


def test_frozen_grid_hashes_match_actual_ram_and_disk_pipeline(tmp_path: Path) -> None:
    grid = GridRecipe(7, 11, noise_bits=3)
    frozen = build_grid_expectation(7, 11, tmp_path, noisy=True, chunk_rows=13)
    source = tmp_path / "grid.ply"
    with source.open("wb") as stream:
        write_recipe(stream, grid, chunk_rows=7)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == frozen.source.sha256
    identities: list[tuple[str, str]] = []
    for storage, chunk in (("ram", 7), ("disk", 31)):
        with (
            prepare_import(source, tmp_path, storage=storage, chunk_rows=chunk) as data,
            account_import(data) as imported,
        ):
            contribution = imported.complete_contributions()
            identities.append((imported.identity, contribution.identity))
            for name, column in (data.columns | contribution.columns).items():
                digest = hashlib.sha256()
                for start in range(0, column.spec.rows, chunk):
                    digest.update(
                        column.read_range(
                            start, min(start + chunk, column.spec.rows)
                        ).tobytes()
                    )
                assert digest.hexdigest() == frozen.columns[name].sha256
            assert imported.inventory["row_digests"] == {
                "vertices": frozen.row_digests["import_vertices"],
                "faces": frozen.row_digests["import_faces"],
            }
            assert contribution.inventory["row_digests"] == {
                "vertices": frozen.row_digests["contribution_vertices"]
            }
    assert identities[0] == identities[1]


def test_grid_oracle_crosses_maximum_batch_without_changing_hashes(
    tmp_path: Path,
) -> None:
    # 66,049 vertices: crosses the largest batch, both row boundaries, and the
    # final partial batch. This remains a small correctness case, not a benchmark.
    first = build_grid_expectation(257, 257, tmp_path, noisy=True)
    second = build_grid_expectation(257, 257, tmp_path, noisy=True, chunk_rows=4093)
    assert first == second


@pytest.mark.parametrize(
    "phase", ("oracle-vertices", "oracle-faces", "oracle-areas", "oracle-weights")
)
def test_grid_oracle_failure_cleans_only_its_owned_files(
    tmp_path: Path, phase: str
) -> None:
    sentinel = tmp_path / "heights.bin"
    _ = sentinel.write_bytes(b"unrelated")

    def interrupt(current: str, _done: int, _total: int) -> None:
        if current == phase:
            raise RuntimeError("injected cancellation")

    with pytest.raises(RuntimeError, match="injected cancellation"):
        _ = build_grid_expectation(3, 3, tmp_path, progress=interrupt)
    assert sentinel.read_bytes() == b"unrelated"
    assert list(tmp_path.iterdir()) == [sentinel]


@pytest.mark.parametrize(
    "width,height,chunk",
    ((1, 2, 3), (2, 1, 3), (10001, 2, 3), (2, 6001, 3), (2, 2, 65537)),
)
def test_grid_oracle_rejects_parameters_outside_bounded_profile(
    tmp_path: Path, width: int, height: int, chunk: int
) -> None:
    with pytest.raises(ValueError, match="explicit S6 profile"):
        _ = build_grid_expectation(width, height, tmp_path, chunk_rows=chunk)
    assert list(tmp_path.iterdir()) == []
