from __future__ import annotations

import json
import struct
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from experiments.mesh_scale_integer import (
    dyadic_f32,
    grid_vertices,
    multiply_wide,
    philox,
)
from scansor.mesh_recipes import GridRecipe, philox_words


def test_wide_integer_multiply_against_python_bigints() -> None:
    values = (0, 1, 2**32 - 1, 2**32, 2**63 - 1, 2**63, 2**64 - 1)
    for multiplier in (*values, 0xD2E7470EE14C6C93, 0xCA5A826395121157):
        high, low = multiply_wide(np.array(values, dtype="<u8"), multiplier)
        for index, value in enumerate(values):
            assert (int(high[index]) << 64) | int(low[index]) == value * multiplier


def test_integer_philox_matches_preexisting_frozen_vectors_and_counter_carry() -> None:
    records = json.loads(
        (
            Path(__file__).parent / "fixtures" / "mesh-numeric-goldens-v1.json"
        ).read_bytes()
    )["philox_vectors"]
    for record in records:
        words = philox(7, np.array([record["source_index"]], dtype="<u8"))[0]
        assert [f"{int(word):016x}" for word in words] == record["words_hex"]
    for seed in (0, 7, 2**64 - 1):
        for index in (0, 1, 2**53 + 1, 2**64 - 1):
            assert np.array_equal(
                philox(seed, np.array([index], dtype="<u8")),
                philox_words(seed, index, index + 1),
            )


@pytest.mark.parametrize("quantum", (-149, -140, -126, -8, 0, 50))
def test_integer_coordinate_encoding_matches_fraction_oracle(quantum: int) -> None:
    values = (0, 1, -1, 3, -7, 255, -65535, 2**24, 2**24 + 2)
    result = dyadic_f32(np.array(values, dtype="<i8"), quantum)
    assert result.tolist() == [
        struct.unpack(
            "<I", struct.pack("<f", float(Fraction(value) * Fraction(2) ** quantum))
        )[0]
        for value in values
    ]


@pytest.mark.parametrize("noisy", (False, True))
def test_independent_grid_vertices_match_full_small_and_large_boundary_queries(
    noisy: bool,
) -> None:
    for width, height in ((17, 19), (3000, 2000), (10000, 6000)):
        recipe = GridRecipe(width, height, noise_bits=3 if noisy else 0)
        starts = (0, width - 1, width, recipe.vertex_count - 7)
        for start in starts:
            stop = min(start + 7, recipe.vertex_count)
            expected = grid_vertices(
                width, height, np.arange(start, stop, dtype="<u8"), noisy=noisy
            )
            assert np.array_equal(expected, recipe.vertices(start, stop).view("<u4"))
