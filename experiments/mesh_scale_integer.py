"""Independent integer construction for S6 expectations, not production kernels.

Philox4x64-10 implements the mathematical round/Weyl constants in Salmon et al.,
Parallel Random Numbers: As Easy as 1, 2, 3 (SC11), section 4.2:
https://www.thesalmons.org/john/random123/papers/random123sc11.pdf
It does not call NumPy's random generator or Scansor recipe/numeric functions.
The recipe's block at source index i is Philox(counter=[i+1,0,0,0]);
the carry at uint64 overflow is retained. Frozen vectors check this convention.
"""

from __future__ import annotations

import numpy as np

MASK32 = np.uint64(2**32 - 1)
MASK64 = 2**64 - 1
MAX_ROWS = 65_536


def _words(values: np.ndarray) -> np.ndarray:
    if values.dtype != np.dtype("<u8") or values.ndim != 1 or len(values) > MAX_ROWS:
        raise ValueError("oracle requires a bounded uint64 vector")
    return values


def multiply_wide(left: np.ndarray, right: int) -> tuple[np.ndarray, np.ndarray]:
    """Return exact high/low words of independent 64x64 integer products."""
    _ = _words(left)
    if type(right) is not int or not 0 <= right <= MASK64:
        raise ValueError("oracle multiplier must fit uint64")
    a, b = left & MASK32, left >> np.uint64(32)
    c, d = np.uint64(right & int(MASK32)), np.uint64(right >> 32)
    first = a * c
    middle = b * c + (first >> np.uint64(32))
    carry = middle >> np.uint64(32)
    middle = a * d + (middle & MASK32)
    high = b * d + carry + (middle >> np.uint64(32))
    low = (middle << np.uint64(32)) | (first & MASK32)
    return high, low


def philox(seed: int, indices: np.ndarray) -> np.ndarray:
    _ = _words(indices)
    if type(seed) is not int or not 0 <= seed <= MASK64:
        raise ValueError("oracle seed must fit uint64")
    a = indices + np.uint64(1)
    b = (indices == np.uint64(MASK64)).astype("<u8")
    c, d = np.zeros(len(indices), dtype="<u8"), np.zeros(len(indices), dtype="<u8")
    key_a, key_b = seed, 0
    for _ in range(10):
        high_a, low_a = multiply_wide(a, 0xD2E7470EE14C6C93)
        high_c, low_c = multiply_wide(c, 0xCA5A826395121157)
        a, b, c, d = (
            high_c ^ b ^ np.uint64(key_a),
            low_c,
            high_a ^ d ^ np.uint64(key_b),
            low_a,
        )
        key_a = (key_a + 0x9E3779B97F4A7C15) & MASK64
        key_b = (key_b + 0xBB67AE8584CAA73B) & MASK64
    return np.column_stack((a, b, c, d))


def dyadic_f32(coefficients: np.ndarray, quantum: int) -> np.ndarray:
    """Exact integer encoding for the scale recipes' small dyadic coordinates."""
    if (
        coefficients.dtype != np.dtype("<i8")
        or coefficients.ndim != 1
        or len(coefficients) > MAX_ROWS
    ):
        raise ValueError("oracle requires bounded int64 coefficients")
    if (
        type(quantum) is not int
        or not -149 <= quantum <= 127
        or np.any(coefficients <= -(2**31))
        or np.any(coefficients >= 2**31)
    ):
        raise ValueError("oracle dyadic input is outside its explicit range")
    magnitude = np.abs(coefficients).astype("<u8")
    result = np.zeros(len(coefficients), dtype="<u4")
    for top in range(31):
        selected = (magnitude >= 2**top) & (magnitude < 2 ** (top + 1))
        if not np.any(selected):
            continue
        values = magnitude[selected]
        exponent = top + quantum
        if exponent > 127:
            raise ValueError("dyadic coordinate overflows binary32")
        if exponent < -126:
            words = values << np.uint64(quantum + 149)
        else:
            if top > 23:
                discarded = 2 ** (top - 23) - 1
                if np.any(values & np.uint64(discarded)):
                    raise ValueError("dyadic coordinate is not exact binary32")
                mantissa = values >> np.uint64(top - 23)
            else:
                mantissa = values << np.uint64(23 - top)
            words = np.uint64((exponent + 127) << 23) | (
                mantissa & np.uint64(2**23 - 1)
            )
        result[selected] = words.astype("<u4")
    result[coefficients < 0] |= np.uint32(1 << 31)
    return result


def grid_vertices(
    width: int, height: int, indices: np.ndarray, *, seed: int = 7, noisy: bool = False
) -> np.ndarray:
    """Construct exact packed XYZ words independently at arbitrary source IDs."""
    _ = _words(indices)
    if (
        type(width) is not int
        or type(height) is not int
        or min(width, height) < 1
        or width * height > 2**31
        or np.any(indices >= width * height)
    ):
        raise ValueError("invalid scale grid population or source indices")
    column = (indices % np.uint64(width)).astype("<i8")
    row = (indices // np.uint64(width)).astype("<i8")
    result = np.zeros((len(indices), 3), dtype="<u4")
    result[:, 0] = dyadic_f32(3 * column, 0)
    result[:, 1] = dyadic_f32(4 * row, 0)
    if noisy:
        coefficients = (2 * (philox(seed, indices)[:, 0] & np.uint64(7))).astype(
            "<i8"
        ) - 7
        result[:, 2] = dyadic_f32(coefficients, -8)
    return result
