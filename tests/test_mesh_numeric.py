from __future__ import annotations

import math
import struct
from collections.abc import Callable
from fractions import Fraction

import numpy as np
import pytest

from scansor.mesh_numeric import (
    NumericProfileError,
    bits_float,
    canonical_f32,
    check_arithmetic,
    corner_areas,
    face_areas,
    float_bits,
    normalized_weights,
    ordered_fold,
)
from tests import mesh_rounding_oracle as oracle


@pytest.mark.parametrize("sign", (1, -1))
def test_oracle_rounding_neighbors_and_ties(sign: int) -> None:
    for lower in (
        0,
        1,
        2,
        0x000FFFFFFFFFFFFE,
        0x000FFFFFFFFFFFFF,
        0x0010000000000000,
        0x3FEFFFFFFFFFFFFF,
        0x3FF0000000000000,
        0x3FF0000000000001,
        0x7FEFFFFFFFFFFFFD,
    ):
        left, right = oracle.rational(lower), oracle.rational(lower + 1)
        midpoint = (left + right) / 2
        epsilon = (right - left) / 8
        sign_bit = oracle.SIGN if sign < 0 else 0
        assert oracle.rounded(sign * (midpoint - epsilon)) == lower | sign_bit
        assert oracle.rounded(sign * midpoint) == (lower + (lower % 2)) | sign_bit
        assert oracle.rounded(sign * (midpoint + epsilon)) == (lower + 1) | sign_bit
    assert oracle.rounded(Fraction(2) ** 1024) == 0x7FF0000000000000
    assert oracle.rounded(-(Fraction(2) ** 1024)) == 0xFFF0000000000000


@pytest.mark.parametrize(
    "word",
    [
        0,
        oracle.SIGN,
        1,
        2,
        0x0010000000000000,
        0x3FF0000000000000,
        0x3FFFFFFFFFFFFFFF,
        0x4000000000000000,
        0x4000000000000001,
        oracle.MAX_FINITE,
    ],
)
def test_square_root_neighbors(word: int) -> None:
    expected = oracle.square_root(word)
    assert oracle.bits(math.sqrt(oracle.value(word))) == expected
    assert oracle.bits(float(np.sqrt(np.float64(oracle.value(word))))) == expected
    if word not in (0, oracle.SIGN):
        exact = oracle.rational(word)
        midpoint_below = (oracle.rational(expected - 1) + oracle.rational(expected)) / 2
        midpoint_above = (oracle.rational(expected) + oracle.rational(expected + 1)) / 2
        assert midpoint_below**2 <= exact <= midpoint_above**2


@pytest.mark.parametrize(
    "words",
    [
        (0x3FF0000000000000, 0x3CA0000000000000),
        (0x3FF0000000000001, 0x3CA0000000000000),
        (0x3FF0000000000000, 0xBFF0000000000000),
        (0, oracle.SIGN),
        (oracle.SIGN, oracle.SIGN),
        (1, 2),
        (0x0010000000000000, 0x3FE0000000000000),
        (0x7FDFFFFFFFFFFFFF, 0x3FF0000000000001),
        (0x3FF0000000000001, 0x3FEFFFFFFFFFFFFF),
    ],
)
def test_individual_operations_match_integer_oracle(words: tuple[int, int]) -> None:
    left, right = map(oracle.value, words)
    operations: list[
        tuple[Callable[[float, float], float], Callable[[int, int], int]]
    ] = [
        (lambda a, b: a + b, oracle.add),
        (lambda a, b: a - b, oracle.subtract),
        (lambda a, b: a * b, oracle.multiply),
    ]
    if right != 0:
        operations.append((lambda a, b: a / b, oracle.divide))
    for operation, reference in operations:
        assert oracle.bits(operation(left, right)) == reference(*words)


@pytest.mark.parametrize("endian", ("<", ">"))
def test_canonical_bits_without_float_arithmetic(endian: str) -> None:
    words = [
        0,
        0x80000000,
        1,
        0x80000001,
        0x7F7FFFFF,
        0xFF7FFFFF,
        0x7F800000,
        0xFF800000,
        0x7F800001,
        0xFFC12345,
        0x7FFFFFFF,
    ]
    source = np.array(words, dtype=f"{endian}u4").view(f"{endian}f4")
    original = source.tobytes()
    result = canonical_f32(source)
    assert result.dtype.str == "<f4"
    assert result.tobytes() == struct.pack(
        "<11I", *words[:1], 0, *words[2:8], *([0x7FC00000] * 3)
    )
    assert source.tobytes() == original
    assert not np.shares_memory(source, result)


def _triangles() -> np.ndarray:
    tiny = struct.unpack("<f", struct.pack("<I", 1))[0]
    maximum = struct.unpack("<f", struct.pack("<I", 0x7F7FFFFF))[0]
    return np.array(
        [
            [[0, 0, 0], [3, 0, 0], [0, 4, 0]],
            [[0, 0, 0], [1, 0, 0], [0, 1, 1]],
            [[-0.0, 0, 0], [1, 2, 3], [2, 4, 6]],
            [[0, 0, 0], [tiny, 0, 0], [0, tiny, tiny]],
            [
                [-maximum, maximum, maximum],
                [maximum, -maximum, maximum],
                [maximum, maximum, -maximum],
            ],
            [[2**24, 1, -(2**20)], [2**24 + 2, -1, 2**-100], [-1, 2**23, 2**20]],
            [[1, 1, 1], [1 + 2**-23, 1, 1], [1, 1 + 2**-23, 1 + 2**-23]],
        ],
        dtype="<f4",
    )


@pytest.mark.parametrize("chunk", (1, 2, 7, 127))
@pytest.mark.parametrize("endian", ("<", ">"))
def test_area_oracle_and_shuffled_chunk_execution(chunk: int, endian: str) -> None:
    triangles = np.tile(_triangles(), (19, 1, 1)).astype(f"{endian}f4")
    expected = [
        oracle.face_area([[oracle.bits(float(v)) for v in point] for point in face])
        for face in triangles
    ]
    actual = np.empty(len(triangles), dtype="<f8")
    for start in reversed(range(0, len(triangles), chunk)):
        actual[start : start + chunk] = face_areas(triangles[start : start + chunk])
    assert actual.view("<u8").tolist() == expected
    assert corner_areas(actual).view("<u8").tolist() == [
        oracle.divide(word, oracle.bits(3)) for word in expected
    ]


@pytest.mark.parametrize("chunk", (1, 2, 7, 127, 4093))
def test_high_valence_fold_continuation_and_normalization(chunk: int) -> None:
    # Losing a chunk's individually rounded additions changes this answer.
    contributions = [float(2**53), *([1.0] * 8193), 3.0, 2**-100]
    expected = oracle.fold([oracle.bits(v) for v in contributions])
    total = 0.0
    for start in range(0, len(contributions), chunk):
        total = ordered_fold(contributions[start : start + chunk], initial=total)
    assert oracle.bits(total) == expected
    assert total != float(sum(map(Fraction, contributions)))
    areas = np.array([total, 2, 0, 7, 2**-120], dtype="<f8")
    population_sum = ordered_fold(areas)
    weights = normalized_weights(areas, eligible_count=4, total=population_sum)
    assert weights.view("<u8").tolist() == [
        oracle.divide(
            oracle.multiply(oracle.bits(float(a)), oracle.bits(4)),
            oracle.bits(population_sum),
        )
        for a in areas
    ]


def test_empty_and_invalid_numerics_fail_explicitly() -> None:
    assert face_areas(np.empty((0, 3, 3), dtype="<f4")).shape == (0,)
    assert normalized_weights(
        np.zeros(3), eligible_count=0, total=0
    ).tobytes() == bytes(24)
    for invalid in (-0.0, -1, float("inf"), float("nan")):
        with pytest.raises(NumericProfileError):
            _ = ordered_fold([invalid])
        with pytest.raises(NumericProfileError):
            _ = corner_areas(np.array([invalid], dtype="<f8"))
    with pytest.raises(NumericProfileError, match="nonfinite ordered"):
        _ = ordered_fold([float.fromhex("0x1.fffffffffffffp1023")] * 2)
    with pytest.raises(NumericProfileError):
        _ = normalized_weights(np.array([1.0]), eligible_count=0, total=0)
    with pytest.raises(NumericProfileError):
        _ = normalized_weights(np.array([1.0]), eligible_count=True, total=1)
    with pytest.raises(NumericProfileError):
        _ = normalized_weights(
            np.array([float.fromhex("0x1.fffffffffffffp1023")]),
            eligible_count=2,
            total=1,
        )
    with pytest.raises(NumericProfileError):
        _ = normalized_weights(
            np.array([float.fromhex("0x0.0000000000001p-1022")]),
            eligible_count=1,
            total=1e300,
        )
    with pytest.raises(NumericProfileError):
        _ = face_areas(np.zeros((1, 3, 3), dtype="f8"))
    with pytest.raises(NumericProfileError):
        _ = face_areas(np.full((1, 3, 3), np.nan, dtype="f4"))


def test_runtime_rejects_changed_arithmetic(monkeypatch: pytest.MonkeyPatch) -> None:
    check_arithmetic()

    def wrong_sqrt(_value: float) -> float:
        return 1.0

    monkeypatch.setattr(math, "sqrt", wrong_sqrt)
    with pytest.raises(NumericProfileError, match="unsupported"):
        _ = face_areas(_triangles())
    with pytest.raises(NumericProfileError, match="unsupported"):
        _ = ordered_fold([1.0])


def test_float_controls() -> None:
    for bits in ("0000000000000000", "8000000000000000", "3ff0000000000001"):
        assert float_bits(bits_float(bits)) == bits
    for invalid in ("0", "3FF0000000000000", "x" * 16):
        with pytest.raises(NumericProfileError):
            _ = bits_float(invalid)
