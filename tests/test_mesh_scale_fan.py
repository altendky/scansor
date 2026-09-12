from __future__ import annotations

import hashlib
import io
import struct

import pytest

from experiments.mesh_scale_fan import FanExpectation, build_fan_expectation
from experiments.mesh_scale_fan_source import FanRecipe, write_fan
from scansor.mesh_recipes import SmallRecipe
from tests import mesh_rounding_oracle as oracle
from tests.test_mesh_accounting import independent_artifacts
from tests.test_mesh_scale_grid import _row_hash  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("radius", (4, 8, 16))
@pytest.mark.parametrize("adverse", (False, True))
def test_full_fan_oracle_matches_independent_per_face_fraction_data(
    radius: int, adverse: bool
) -> None:
    recipe = FanRecipe(radius, adverse)
    xyz, faces = (
        recipe.vertices(0, recipe.vertex_count).view("<u4"),
        recipe.faces(0, recipe.face_count),
    )
    small = SmallRecipe(
        "fan-comparison-v1",
        tuple((int(row[0]), int(row[1]), int(row[2])) for row in xyz),
        tuple((int(row[0]), int(row[1]), int(row[2])) for row in faces),
    )
    expected = independent_artifacts(small)
    ply = oracle.independent_ply(list(small.xyz_bits), list(small.triangles)).replace(
        b"scansor-mesh-recipe-v1", b"scansor-mesh-fan-v1", 1
    )
    native = io.BytesIO()
    write_fan(native, recipe, chunk_rows=7)
    assert native.getvalue() == ply
    results: list[FanExpectation] = []
    for chunk in (1, 7, 31):
        collected: dict[str, bytearray] = {}

        def collect(
            name: str, data: bytes, output: dict[str, bytearray] = collected
        ) -> None:
            output.setdefault(name, bytearray()).extend(data)

        result = build_fan_expectation(
            radius, adverse=adverse, chunk_rows=chunk, column_sink=collect
        )
        results.append(result)
        assert collected == expected
        assert result.source.sha256 == hashlib.sha256(ply).hexdigest()
        assert result.source.bytes == len(ply)
        for name, payload in expected.items():
            assert result.columns[name].bytes == len(payload)
            assert result.columns[name].sha256 == hashlib.sha256(payload).hexdigest()
        assert result.row_digests == {
            "import_vertices": _row_hash(
                "mesh-import-vertices-v1",
                recipe.vertex_count,
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
                recipe.face_count,
                tuple(
                    expected[name]
                    for name in ("triangles.bin", "face-status.bin", "face-area.bin")
                ),
            ),
            "contribution_vertices": _row_hash(
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
        assert result.in_range_source_corners == 3 * recipe.face_count
        assert result.center_reference_count == 4 * radius + (6 if adverse else 0)
        assert (
            result.center_area_bits
            == f"{struct.unpack_from('<Q', expected['vertex-area.bin'])[0]:016x}"
        )
    assert all(result == results[0] for result in results)


def test_fan_center_fold_and_full_hashes_cross_maximum_batch_boundary() -> None:
    first = build_fan_expectation(16384, adverse=True, chunk_rows=65536)
    second = build_fan_expectation(16384, adverse=True, chunk_rows=4093)
    assert first == second
    assert first.center_reference_count == 65542
    assert first.vertices == 65537 and first.faces == 65544
    assert first.face_category_counts["usable"] == 65540
    assert first.display_population["rejected_corners"] == 12


@pytest.mark.parametrize("radius", (0, 2, 6, 262145, True))
def test_fan_profiles_reject_invalid_radius(radius: int) -> None:
    with pytest.raises(ValueError):
        _ = FanRecipe(radius)
    with pytest.raises(ValueError):
        _ = build_fan_expectation(radius)
