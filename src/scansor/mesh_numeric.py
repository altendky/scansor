"""Ordered mesh arithmetic; independent faces vectorize, dependent folds do not.

Do not replace these operations with reductions, fused expressions, or fast-math.
Changes require the independent oracle and the platform/dispatch conformance gate.
The caller bounds every array passed here and budgets the documented work arrays.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Iterable

import numpy as np

from scansor.errors import ScansorError

REVISION = "ordered-binary64-v1"


class NumericProfileError(ScansorError):
    def __init__(self, message: str) -> None:
        super().__init__(f"numeric-profile-failure: {message}")


def float_bits(value: float) -> str:
    return struct.pack(">d", value).hex()


def bits_float(value: str) -> float:
    if len(value) != 16 or any(c not in "0123456789abcdef" for c in value):
        raise NumericProfileError("binary64 control must have 16 lowercase hex digits")
    return struct.unpack(">d", bytes.fromhex(value))[0]


def canonical_f32(values: np.ndarray) -> np.ndarray:
    """Owned little-endian result; integer operations preserve exceptional bits.

    Peak extra arrays: result (4 bytes/value) and masks (at most 6 bytes/value).
    Endian conversion is on integer words, never a floating NaN conversion.
    """
    if values.dtype.kind != "f" or values.dtype.itemsize != 4:
        raise NumericProfileError("canonical source values must be binary32")
    words = values.view(values.dtype.str.replace("f", "u")).astype("<u4", copy=True)
    magnitude = words & np.uint32(0x7FFFFFFF)
    words[magnitude == 0] = 0
    words[magnitude > 0x7F800000] = 0x7FC00000
    return words.view("<f4")


def face_areas(corners: np.ndarray) -> np.ndarray:
    """Compute areas of finite binary32 corners shaped (faces, 3, 3).

    Source indices/disposition precedence are the importer's responsibility.
    Work is bounded by the input batch: at most 192 bytes/face plus masks.
    Each ufunc is a separate binary64 rounding step, including sqrt.
    """
    check_arithmetic()
    if (
        corners.dtype.kind != "f"
        or corners.dtype.itemsize != 4
        or corners.ndim != 3
        or corners.shape[1:] != (3, 3)
    ):
        raise NumericProfileError("face corners must have shape (n, 3, 3), binary32")
    points = corners.astype(np.float64)
    if not np.all(np.isfinite(points)):
        raise NumericProfileError("face kernel requires finite positions")
    try:
        with np.errstate(all="raise"):
            u = np.subtract(points[:, 1], points[:, 0])
            v = np.subtract(points[:, 2], points[:, 0])
            del points
            cross = np.empty_like(u)
            first = np.empty(len(u), dtype=np.float64)
            second = np.empty_like(first)
            for axis, left, right in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
                _ = np.multiply(u[:, left], v[:, right], out=first)
                _ = np.multiply(u[:, right], v[:, left], out=second)
                _ = np.subtract(first, second, out=cross[:, axis])
            del u, v
            _ = np.multiply(cross[:, 0], cross[:, 0], out=first)
            _ = np.multiply(cross[:, 1], cross[:, 1], out=second)
            _ = np.add(first, second, out=first)
            _ = np.multiply(cross[:, 2], cross[:, 2], out=second)
            _ = np.add(first, second, out=first)
            _ = np.sqrt(first, out=first)
            _ = np.multiply(first, np.float64(0.5), out=first)
    except FloatingPointError as error:
        raise NumericProfileError(
            "nonfinite or underflowing area intermediate"
        ) from error
    if not np.all(np.isfinite(first)):
        raise NumericProfileError("nonfinite area result")
    first[first == 0] = 0.0
    return first.astype("<f8", copy=False)


def corner_areas(areas: np.ndarray) -> np.ndarray:
    check_arithmetic()
    _areas(areas)
    result = np.divide(areas, np.float64(3.0), dtype=np.float64)
    if np.any((areas > 0) & (result <= 0)):
        raise NumericProfileError("positive face area lost during allocation")
    return result.astype("<f8", copy=False)


def _areas(values: np.ndarray) -> None:
    if (
        values.ndim != 1
        or values.dtype.kind != "f"
        or values.dtype.itemsize != 8
        or not np.all(np.isfinite(values))
        or np.any(values < 0)
        or np.any(np.signbit(values))
    ):
        raise NumericProfileError("expected finite nonnegative binary64 areas with +0")


def ordered_fold(values: Iterable[float], *, initial: float = 0.0) -> float:
    """Continue a scalar left fold, never a chunk subtotal.

    The caller supplies source order, including when completing chunks out of
    order. Passing a previous result as initial preserves each addition boundary.
    """
    check_arithmetic()
    total = float(initial)
    if not math.isfinite(total) or total < 0 or math.copysign(1, total) < 0:
        raise NumericProfileError("invalid initial accumulation")
    for item in values:
        value = float(item)
        if not math.isfinite(value) or value < 0 or math.copysign(1, value) < 0:
            raise NumericProfileError("invalid contribution to ordered accumulation")
        total = total + value
        if not math.isfinite(total):
            raise NumericProfileError("nonfinite ordered accumulation")
    return total


def normalized_weights(
    areas: np.ndarray, *, eligible_count: int, total: float
) -> np.ndarray:
    """Normalize one bounded range using the full ordered population's K and S.

    Zero rows are exclusions already determined by the caller. No local count,
    reduction, remainder redistribution, or alternate normalization is allowed.
    """
    check_arithmetic()
    _areas(areas)
    if type(eligible_count) is not int or not 0 <= eligible_count <= 2**31:
        raise NumericProfileError("eligible count outside 0..2^31")
    if eligible_count == 0:
        if float_bits(total) != "0000000000000000" or np.any(areas != 0):
            raise NumericProfileError("empty eligible population requires zero area")
        return np.zeros(len(areas), dtype="<f8")
    if not math.isfinite(total) or total <= 0:
        raise NumericProfileError("eligible population requires positive finite sum")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise", under="ignore"):
            result = np.multiply(areas, np.float64(eligible_count), dtype=np.float64)
            _ = np.divide(result, np.float64(total), out=result)
    except FloatingPointError as error:
        raise NumericProfileError("nonfinite normalization intermediate") from error
    if not np.all(np.isfinite(result)) or np.any((areas > 0) & (result <= 0)):
        raise NumericProfileError("eligible weight is not positive and finite")
    return result.astype("<f8", copy=False)


def check_arithmetic() -> None:
    """Small runtime rejection gate, in addition to the full independent CI oracle.

    Deliberately not cached: a native library can change the thread's rounding
    mode or denormal handling after import. This checks both scalar fold and
    NumPy elementwise paths each time a bounded operation begins.
    """
    one = bits_float("3ff0000000000000")
    half_ulp = bits_float("3ca0000000000000")
    next_one = bits_float("3ff0000000000001")
    tiny = bits_float("0000000000000001")
    cases = (
        (one + half_ulp, "3ff0000000000000"),
        (next_one + half_ulp, "3ff0000000000002"),
        (tiny + tiny, "0000000000000002"),
        (tiny * one, "0000000000000001"),
        (one - one, "0000000000000000"),
        (-0.0 * one, "8000000000000000"),
        (one / 3.0, "3fd5555555555555"),
        (math.sqrt(2.0), "3ff6a09e667f3bcd"),
    )
    with np.errstate(all="ignore"):
        arrays = (
            (
                np.add([one, next_one, tiny], [half_ulp, half_ulp, tiny]),
                ("3ff0000000000000", "3ff0000000000002", "0000000000000002"),
            ),
            (
                np.multiply([tiny, -0.0], [one, one]),
                ("0000000000000001", "8000000000000000"),
            ),
            (np.subtract([one], [one]), ("0000000000000000",)),
            (np.divide([one], [3.0]), ("3fd5555555555555",)),
            (np.sqrt([2.0]), ("3ff6a09e667f3bcd",)),
            (
                np.array([1, 0x80000001], dtype="<u4").view("<f4").astype(np.float64),
                ("36a0000000000000", "b6a0000000000000"),
            ),
        )
    if any(float_bits(value) != expected for value, expected in cases) or any(
        float_bits(float(value)) != expected
        for values, expected_bits in arrays
        for value, expected in zip(values, expected_bits, strict=True)
    ):
        raise NumericProfileError(
            "unsupported rounding, square root, or denormal environment"
        )
