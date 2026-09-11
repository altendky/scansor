from __future__ import annotations

import numpy as np
import pytest

from experiments.mesh_scale_rounding import (
    add_grid_scaled,
    fold_grid_scaled,
    grid_area_scaled,
    scaled_word,
    weight_words,
)
from tests import mesh_rounding_oracle as oracle


@pytest.mark.parametrize("eligible", (1, 3, 323, 6_000_000, 60_000_000, 2**31))
def test_integer_weight_product_and_long_division_match_fraction_oracle(
    eligible: int,
) -> None:
    words = [
        0,
        oracle.bits(2.0),
        oracle.bits(2.0) + 1,
        oracle.bits(12.0) - 1,
        oracle.bits(2.0**-300),
        oracle.bits(2.0**255),
    ]
    for denominator in (
        oracle.bits(3.0),
        oracle.bits(719_808_012.5),
        oracle.bits(2.0**300),
    ):
        actual = weight_words(np.array(words, dtype="<u8"), eligible, denominator)
        expected = [
            0
            if word == 0
            else oracle.divide(
                oracle.multiply(oracle.bits(float(eligible)), word), denominator
            )
            for word in words
        ]
        assert actual.tolist() == expected


def test_grid_accumulation_and_fold_match_fraction_oracle_including_ties() -> None:
    # Six terms per vertex; integer offsets exercise even/odd half-way rounding.
    base = oracle.bits(2.0)
    columns = [
        np.array([base + i, base + 1, base + 3, 0], dtype="<u8") for i in range(6)
    ]
    accumulator = np.zeros(4, dtype="<u8")
    expected = [0] * 4
    for column in columns:
        accumulator = add_grid_scaled(accumulator, grid_area_scaled(column))
        expected = [
            oracle.add(before, int(term))
            for before, term in zip(expected, column, strict=True)
        ]
        assert [scaled_word(int(value)) for value in accumulator] == expected
    repeated = np.tile(accumulator, 1000)
    expected_total = oracle.fold(expected * 1000)
    total = 0
    for start in range(0, len(repeated), 7):
        total = fold_grid_scaled(repeated[start : start + 7], initial=total)
    assert scaled_word(total) == expected_total


def test_weight_oracle_rejects_nonfinite_and_negative_areas() -> None:
    for word in (oracle.bits(float("inf")), oracle.bits(-1.0), 1):
        with pytest.raises(ValueError, match="normal operands"):
            _ = weight_words(np.array([word], dtype="<u8"), 1, oracle.bits(1.0))
