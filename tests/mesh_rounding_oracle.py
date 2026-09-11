"""Independent binary64 oracle: integer encodings, rational arithmetic, no NumPy.

Only unpacking/packing test inputs uses host floats. Arithmetic and sqrt rounding
use exact integers/Fraction, including comparisons with squared midpoints.
"""

from __future__ import annotations

import struct
from fractions import Fraction

SIGN = 1 << 63
MAX_FINITE = 0x7FEFFFFFFFFFFFFF


def bits(value: float) -> int:
    return struct.unpack(">Q", struct.pack(">d", value))[0]


def value(word: int) -> float:
    return struct.unpack(">d", struct.pack(">Q", word))[0]


def rational(word: int) -> Fraction:
    exponent = (word >> 52) & 2047
    fraction = word & ((1 << 52) - 1)
    if exponent == 2047:
        raise ValueError("oracle requires finite operands")
    mantissa = fraction if exponent == 0 else fraction + (1 << 52)
    quantum = -1074 if exponent == 0 else exponent - 1075
    numerator = -mantissa if word & SIGN else mantissa
    if quantum >= 0:
        return Fraction(numerator << quantum)
    return Fraction(numerator, 1 << -quantum)


def rounded(number: Fraction, *, negative_zero: bool = False) -> int:
    if number == 0:
        return SIGN if negative_zero else 0
    sign = SIGN if number < 0 else 0
    numerator = abs(number.numerator)
    denominator = number.denominator
    exponent = numerator.bit_length() - denominator.bit_length()
    below = (
        (numerator < denominator << exponent)
        if exponent >= 0
        else (numerator << -exponent < denominator)
    )
    if below:
        exponent -= 1
    quantum = max(-1074, exponent - 52)
    if quantum >= 0:
        denominator <<= quantum
    else:
        numerator <<= -quantum
    mantissa, remainder = divmod(numerator, denominator)
    if remainder * 2 > denominator or (remainder * 2 == denominator and mantissa % 2):
        mantissa += 1
    if mantissa >= 1 << 53:
        mantissa >>= 1
        quantum += 1
    if mantissa < 1 << 52:
        return sign | mantissa
    exponent = quantum + 52
    if exponent > 1023:
        return sign | 0x7FF0000000000000
    return sign | ((exponent + 1023) << 52) | (mantissa - (1 << 52))


def add(left: int, right: int) -> int:
    return rounded(
        rational(left) + rational(right), negative_zero=bool(left & right & SIGN)
    )


def subtract(left: int, right: int) -> int:
    return add(left, right ^ SIGN)


def multiply(left: int, right: int) -> int:
    return rounded(
        rational(left) * rational(right), negative_zero=bool((left ^ right) & SIGN)
    )


def divide(left: int, right: int) -> int:
    return rounded(
        rational(left) / rational(right), negative_zero=bool((left ^ right) & SIGN)
    )


def square_root(word: int) -> int:
    number = rational(word)
    if number < 0:
        raise ValueError("negative square root")
    if number == 0:
        return word
    low, high = 0, MAX_FINITE
    while low < high:
        middle = (low + high + 1) // 2
        candidate = rational(middle)
        if candidate * candidate <= number:
            low = middle
        else:
            high = middle - 1
    root = rational(low)
    if root * root == number:
        return low
    midpoint = (root + rational(low + 1)) / 2
    boundary = midpoint * midpoint
    return low + int(number > boundary or (number == boundary and low % 2 != 0))


def face_area(corners: list[list[int]]) -> int:
    """Input corners are binary64 bit words, exactly promoted from binary32."""
    p0, p1, p2 = corners
    u = [subtract(b, a) for a, b in zip(p0, p1, strict=True)]
    v = [subtract(b, a) for a, b in zip(p0, p2, strict=True)]
    cross = [
        subtract(multiply(u[j], v[k]), multiply(u[k], v[j]))
        for j, k in ((1, 2), (2, 0), (0, 1))
    ]
    squared = [multiply(c, c) for c in cross]
    total = add(add(squared[0], squared[1]), squared[2])
    return multiply(square_root(total), 0x3FE0000000000000)


def fold(words: list[int], *, initial: int = 0) -> int:
    total = initial
    for word in words:
        total = add(total, word)
    return total


def independent_ply(
    xyz: list[tuple[int, int, int]],
    faces: list[tuple[int, int, int]],
    normals: list[tuple[int, int, int]] | None = None,
) -> bytes:
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment scansor-mesh-recipe-v1\n"
        + f"element vertex {len(xyz)}\nproperty float x\nproperty float y\nproperty float z\n"
        + (
            "property float nx\nproperty float ny\nproperty float nz\n"
            if normals is not None
            else ""
        )
        + f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    vertices = b"".join(
        struct.pack("<III", *row)
        + (b"" if normals is None else struct.pack("<III", *normals[i]))
        for i, row in enumerate(xyz)
    )
    triangles = b"".join(struct.pack("<Biii", 3, *face) for face in faces)
    return header + vertices + triangles
