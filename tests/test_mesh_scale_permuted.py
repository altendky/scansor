from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import numpy as np
import pytest

from experiments.mesh_scale_permuted import (
    AffinePermutation,
    PermutedExpectation,
    build_permuted_expectation,
)
from scansor.mesh_recipes import GridRecipe, SmallRecipe
from tests import mesh_rounding_oracle as oracle
from tests.test_mesh_accounting import independent_artifacts
from tests.test_mesh_scale_grid import _row_hash  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("noisy", (False, True))
def test_permuted_complete_bytes_follow_new_face_and_vertex_orders(
    tmp_path: Path, noisy: bool
) -> None:
    grid = GridRecipe(17, 19, seed=7, noise_bits=3 if noisy else 0)
    logical_vertices = [
        (131071 * source + 17) % grid.vertex_count
        for source in range(grid.vertex_count)
    ]
    logical_faces = [
        (524287 * source + 29) % grid.face_count for source in range(grid.face_count)
    ]
    inverse = {logical: source for source, logical in enumerate(logical_vertices)}
    xyz = grid.vertices(0, grid.vertex_count).view("<u4")
    faces = grid.faces(0, grid.face_count)
    original = SmallRecipe(
        "original-grid-comparison-v1",
        tuple((int(row[0]), int(row[1]), int(row[2])) for row in xyz),
        tuple((int(row[0]), int(row[1]), int(row[2])) for row in faces),
    )
    permuted = SmallRecipe(
        "permuted-grid-comparison-v1",
        tuple(original.xyz_bits[logical] for logical in logical_vertices),
        tuple(
            (
                inverse[original.triangles[logical][0]],
                inverse[original.triangles[logical][1]],
                inverse[original.triangles[logical][2]],
            )
            for logical in logical_faces
        ),
    )
    expected = independent_artifacts(permuted)
    ply = oracle.independent_ply(
        list(permuted.xyz_bits), list(permuted.triangles)
    ).replace(b"scansor-mesh-recipe-v1", b"scansor-mesh-permuted-grid-v1", 1)
    results: list[PermutedExpectation] = []
    for chunk in (7, 65536):
        collected: dict[str, bytearray] = {}

        def collect(
            name: str, data: bytes, output: dict[str, bytearray] = collected
        ) -> None:
            output.setdefault(name, bytearray()).extend(data)

        result = build_permuted_expectation(
            17, 19, tmp_path, noisy=noisy, chunk_rows=chunk, column_sink=collect
        )
        results.append(result)
        assert collected == expected
        assert result.source.bytes == len(ply)
        assert result.source.sha256 == hashlib.sha256(ply).hexdigest()
        for name, data in expected.items():
            assert result.columns[name].sha256 == hashlib.sha256(data).hexdigest()
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
            total = 0
            for (word,) in struct.iter_unpack("<Q", expected[name]):
                total = oracle.add(total, word)
            assert actual == f"{total:016x}"
    assert results[0] == results[1]
    assert not list(tmp_path.glob("scansor-permuted-oracle-*"))
    if noisy:
        old = np.frombuffer(
            independent_artifacts(original)["vertex-area.bin"], dtype="<u8"
        )
        assert old[logical_vertices].tobytes() != expected["vertex-area.bin"]


def test_affine_bijection_handles_negative_offset_before_modulo() -> None:
    mapping = AffinePermutation(60_000_000, 131071, 17)
    source = np.array([0, 1, 16, 17, 59999999], dtype="<u8")
    assert np.array_equal(mapping.source(mapping.logical(source)), source)
    assert np.array_equal(mapping.logical(mapping.source(source)), source)


@pytest.mark.parametrize("parameters", ((6, 2, 1), (6, 1, 6), (6, 1, -1), (True, 1, 0)))
def test_nonbijective_or_ambiguous_mapping_is_rejected(
    parameters: tuple[int, int, int],
) -> None:
    with pytest.raises(ValueError):
        _ = AffinePermutation(*parameters)
