"""Integer-only binary64 rounding used to freeze large generated expectations.

This is a bounded experimental oracle for finite, nonnegative mesh areas. It
does not call Scansor's arithmetic kernels or NumPy floating-point arithmetic.
It is intentionally not a general IEEE implementation or runtime dependency.
"""

from __future__ import annotations

import numpy as np

from experiments.mesh_scale_integer import MAX_ROWS, multiply_wide

FRACTION = np.uint64(2**52 - 1)
LEADING = np.uint64(2**52)


def _shape(words: np.ndarray) -> None:
    if words.dtype != np.dtype("<u8") or words.ndim != 1 or len(words) > MAX_ROWS:
        raise ValueError("oracle requires bounded uint64 words")


def _length(words: np.ndarray) -> np.ndarray:
    remaining = words.copy()
    result = np.zeros(len(words), dtype="<u8")
    for shift in (32, 16, 8, 4, 2, 1):
        selected = remaining >= np.uint64(1 << shift)
        result[selected] += np.uint64(shift)
        remaining[selected] >>= np.uint64(shift)
    return result + (words != 0).astype("<u8")


def _round_product(
    high: np.ndarray, low: np.ndarray, exponent: np.ndarray
) -> np.ndarray:
    length = np.where(high != 0, _length(high) + np.uint64(64), _length(low))
    shift = length - np.uint64(53)
    if np.any(shift >= 64):
        raise ValueError("oracle product exceeds its bounded integer profile")
    mantissa = (low >> shift) | (high << (np.uint64(64) - shift))
    remainder = low & ((np.uint64(1) << shift) - np.uint64(1))
    half = np.uint64(1) << (np.maximum(shift, np.uint64(1)) - np.uint64(1))
    mantissa += (
        (shift != 0)
        & (
            (remainder > half)
            | ((remainder == half) & ((mantissa & np.uint64(1)) != 0))
        )
    ).astype("<u8")
    carry = (mantissa == np.uint64(2**53)).astype("<u8")
    mantissa >>= carry
    result_exponent = (
        exponent.astype("<i8") + length.astype("<i8") - 53 + carry.astype("<i8")
    )
    if np.any(result_exponent <= 0) or np.any(result_exponent >= 2047):
        raise ValueError("oracle product must be finite normal binary64")
    return (result_exponent.astype("<u8") << np.uint64(52)) | (mantissa & FRACTION)


def weight_words(areas: np.ndarray, eligible: int, total_word: int) -> np.ndarray:
    """Encode RN(RN(eligible * area) / total) with integer products/division."""
    _shape(areas)
    if (
        type(eligible) is not int
        or not 0 <= eligible <= 2**31
        or type(total_word) is not int
        or not 0 <= total_word < 2**64
    ):
        raise ValueError("invalid oracle weight parameters")
    result = np.zeros(len(areas), dtype="<u8")
    positive = areas != 0
    if not np.any(positive):
        return result
    exponents = (areas[positive] >> np.uint64(52)).astype("<i8")
    total_exponent = total_word >> 52
    if (
        eligible == 0
        or np.any(exponents <= 0)
        or np.any(exponents >= 2047)
        or not 1 <= total_exponent <= 2046
    ):
        raise ValueError("oracle weights require finite positive normal operands")
    high, low = multiply_wide((areas[positive] & FRACTION) | LEADING, eligible)
    numerator = _round_product(high, low, exponents)
    denominator = np.uint64((total_word & int(FRACTION)) | int(LEADING))
    mantissa = (numerator & FRACTION) | LEADING
    extra_shift = (mantissa < denominator).astype("<u8")
    remainder = (mantissa << extra_shift) - denominator
    quotient = np.ones(len(numerator), dtype="<u8")
    for _ in range(52):
        remainder <<= np.uint64(1)
        bit = remainder >= denominator
        quotient = (quotient << np.uint64(1)) | bit.astype("<u8")
        remainder[bit] -= denominator
    doubled = remainder << np.uint64(1)
    quotient += (
        (doubled > denominator)
        | ((doubled == denominator) & ((quotient & np.uint64(1)) != 0))
    ).astype("<u8")
    carry = (quotient == np.uint64(2**53)).astype("<u8")
    quotient >>= carry
    exponent = (
        (numerator >> np.uint64(52)).astype("<i8")
        - total_exponent
        + 1023
        - extra_shift.astype("<i8")
        + carry.astype("<i8")
    )
    if np.any(exponent <= 0) or np.any(exponent >= 2047):
        raise ValueError("oracle weight must be finite normal binary64")
    result[positive] = (exponent.astype("<u8") << np.uint64(52)) | (quotient & FRACTION)
    return result


def grid_area_scaled(words: np.ndarray) -> np.ndarray:
    """Represent grid vertex/corner area exactly in units of 2^-51."""
    _shape(words)
    exponent = (words >> np.uint64(52)).astype("<i8")
    positive = words != 0
    if np.any(positive & ((exponent < 1024) | (exponent > 1026))):
        raise ValueError("grid area oracle requires zero or values in [2,16)")
    result = np.zeros(len(words), dtype="<u8")
    result[positive] = ((words[positive] & FRACTION) | LEADING) << (
        exponent[positive] - 1024
    ).astype("<u8")
    return result


def add_grid_scaled(accumulator: np.ndarray, terms: np.ndarray) -> np.ndarray:
    _shape(accumulator)
    _shape(terms)
    if (
        len(accumulator) != len(terms)
        or np.any(accumulator >= 2**55)
        or np.any(terms >= 2**55)
    ):
        raise ValueError("grid accumulation exceeds the six-corner profile")
    exact = accumulator + terms
    if np.any(exact >= 2**55):
        raise ValueError("grid accumulated area is at least 16")
    result = exact.copy()
    for shift in (1, 2):
        selected = (exact >= 2 ** (52 + shift)) & (exact < 2 ** (53 + shift))
        quotient = exact[selected] >> np.uint64(shift)
        remainder = exact[selected] & np.uint64(2**shift - 1)
        half = np.uint64(2 ** (shift - 1))
        quotient += (
            (remainder > half)
            | ((remainder == half) & ((quotient & np.uint64(1)) != 0))
        ).astype("<u8")
        result[selected] = quotient << np.uint64(shift)
    return result


def fold_grid_scaled(values: np.ndarray, initial: int = 0) -> int:
    """Left fold RN additions; Python integers retain the common exact quantum."""
    _shape(values)
    if type(initial) is not int or initial < 0 or np.any(values >= 2**55):
        raise ValueError("invalid grid fold input")
    return fold_scaled(values, initial)


def fold_scaled(values: np.ndarray, initial: int = 0) -> int:
    """Fold positive integers in one common quantum, rounding each sum to 53 bits.

    The caller establishes a common quantum with finite-normal binary64 results;
    this operation neither changes that quantum nor rescales individual terms.
    """
    _shape(values)
    if type(initial) is not int or initial < 0:
        raise ValueError("invalid scaled fold input")
    total = initial
    for value in values:
        total += int(value)
        shift = max(0, total.bit_length() - 53)
        if shift:
            quotient, remainder = divmod(total, 1 << shift)
            half = 1 << (shift - 1)
            if remainder > half or (remainder == half and quotient & 1):
                quotient += 1
            total = quotient << shift
    return total


def scaled_word(value: int, *, quantum: int = -51) -> int:
    if type(value) is not int or value < 0 or type(quantum) is not int:
        raise ValueError("invalid scaled binary64 encoding")
    if value == 0:
        return 0
    length = value.bit_length()
    shift = length - 53
    exponent = length + quantum + 1022
    if shift < 0 or value & ((1 << shift) - 1) or not 1 <= exponent <= 2046:
        raise ValueError("scaled value is not rounded normal binary64")
    return (exponent << 52) | ((value >> shift) & int(FRACTION))
