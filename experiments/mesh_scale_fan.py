"""Independent full-byte oracle for unequal-edge fans and appended adverse faces.

The square has radius R and 4R edges alternating lengths one and three. Each
triangle's area is R/2 or 3R/2. The center receives every rounded corner third in
source order. Integer common-quanta folds round after each addition; no native
Scansor geometry, PLY writer or accumulation implementation is used here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from experiments.mesh_scale_grid import (
    ColumnHash,
    ColumnSink,
    Progress,
    _add_rows,  # pyright: ignore[reportPrivateUsage]
    _Columns,  # pyright: ignore[reportPrivateUsage]
    _Hash,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
)
from experiments.mesh_scale_integer import MAX_ROWS, dyadic_f32
from experiments.mesh_scale_rounding import fold_scaled, scaled_word, weight_words
from tests import mesh_rounding_oracle as rational


@dataclass(frozen=True)
class FanExpectation:
    radius: int
    adverse: bool
    vertices: int
    faces: int
    source: ColumnHash
    columns: dict[str, ColumnHash]
    row_digests: dict[str, str]
    in_range_source_corners: int
    face_area_sum_bits: str
    vertex_area_sum_bits: str
    weight_sum_bits: str
    vertex_area_range_bits: tuple[str, str]
    weight_range_bits: tuple[str, str]
    face_category_counts: dict[str, int]
    display_population: dict[str, int]
    center_reference_count: int
    center_area_bits: str


def _parameters(radius: int, adverse: bool, chunk: int) -> None:
    if type(radius) is not int or not 4 <= radius <= 262144 or radius & (radius - 1):
        raise ValueError("fan radius must be a power of two in 4..262144")
    if (
        type(adverse) is not bool
        or type(chunk) is not int
        or not 1 <= chunk <= MAX_ROWS
    ):
        raise ValueError("invalid bounded fan oracle parameters")


def _scaled(word: int, quantum: int) -> int:
    value = rational.rational(word) * (1 << -quantum)
    if value.denominator != 1 or value < 0:
        raise ValueError("fan common quantum is not exact")
    return value.numerator


def _vertices(radius: int, start: int, stop: int) -> np.ndarray:
    indices = np.arange(start, stop, dtype="<i8")
    ring = np.maximum(indices - 1, 0)
    side, slot = ring // radius, ring % radius
    distance = 2 * slot - slot % 2
    x = np.select(
        (side == 0, side == 1, side == 2),
        (distance - radius, radius, radius - distance),
        default=-radius,
    )
    y = np.select(
        (side == 0, side == 1, side == 2),
        (-radius, distance - radius, radius),
        default=radius - distance,
    )
    x[indices == 0] = y[indices == 0] = 0
    return np.column_stack(
        (dyadic_f32(x, 0), dyadic_f32(y, 0), np.zeros(len(indices), dtype="<u4"))
    )


def _extras(radius: int) -> tuple[tuple[int, int, int], ...]:
    count = 4 * radius
    duplicates = tuple(
        (0, face + 1, (face + 1) % count + 1)
        for face in (0, 65535 % count, 65536 % count, count - 1)
    )
    return (
        *duplicates,
        (0, 1, 1),
        (0, count // 2 + 1, count // 2 + 1),
        (1, 2, 3),
        (radius + 1, radius + 2, radius + 3),
    )


def build_fan_expectation(
    radius: int,
    *,
    adverse: bool = False,
    chunk_rows: int = MAX_ROWS,
    column_sink: ColumnSink | None = None,
    progress: Progress | None = None,
) -> FanExpectation:
    _parameters(radius, adverse, chunk_rows)
    ring, vertices = 4 * radius, 4 * radius + 1
    extras = _extras(radius) if adverse else ()
    faces = ring + len(extras)
    area_quantum = radius.bit_length() - 1 - 55
    # R/2 and 3R/2 are exact integers; their thirds require independent rounding.
    from fractions import Fraction

    areas = [rational.rounded(Fraction(radius * factor, 2)) for factor in (1, 3)]
    thirds = [rational.divide(word, rational.rounded(Fraction(3))) for word in areas]
    third_scaled = np.array(
        [_scaled(word, area_quantum) for word in thirds], dtype="<u8"
    )
    center = 0
    for start in range(0, ring, chunk_rows):
        stop = min(start + chunk_rows, ring)
        center = fold_scaled(
            third_scaled[np.arange(start, stop, dtype="<u8") % 2], center
        )
        if progress is not None:
            progress("oracle-fan-center", stop, ring)
    area_base = rational.add(thirds[0], thirds[1])
    special_areas = {0: scaled_word(center, quantum=area_quantum)}
    extra_refs: dict[int, int] = {}
    for ordinal, triangle in enumerate(extras):
        for vertex in triangle:
            extra_refs[vertex] = extra_refs.get(vertex, 0) + 1
            if ordinal < 4:
                third = thirds[(triangle[1] - 1) % 2]
                special_areas[vertex] = rational.add(
                    special_areas.get(vertex, area_base), third
                )

    def area_values(start: int, stop: int) -> np.ndarray:
        result = np.full(stop - start, area_base, dtype="<u8")
        for vertex, word in special_areas.items():
            if start <= vertex < stop:
                result[vertex - start] = word
        return result

    header = (
        "ply\nformat binary_little_endian 1.0\ncomment scansor-mesh-fan-v1\n"
        f"element vertex {vertices}\nproperty float x\nproperty float y\nproperty float z\n"
        f"element face {faces}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    source, columns = _Hash(header), _Columns(column_sink)
    vertex_rows = _rows("mesh-import-vertices-v1", vertices)
    face_rows = _rows("mesh-import-faces-v1", faces)
    contribution_rows = _rows("mesh-contribution-vertices-v1", vertices)
    total = _scaled(special_areas[0], area_quantum)
    for start in range(0, vertices, chunk_rows):
        stop = min(start + chunk_rows, vertices)
        xyz, zero = _vertices(radius, start, stop), np.zeros(stop - start, dtype="u1")
        counts = np.full(stop - start, 2, dtype="<u8")
        if start == 0:
            counts[0] = ring
        for vertex, count in extra_refs.items():
            if start <= vertex < stop:
                counts[vertex - start] += count
        source.update(xyz.tobytes())
        for name, array in zip(
            (
                "xyz.bin",
                "vertex-status.bin",
                "normal-status.bin",
                "reference-count.bin",
            ),
            (xyz, zero, zero, counts),
            strict=True,
        ):
            columns.add(name, array)
        _add_rows(vertex_rows, start, (xyz, zero, zero, counts))
        words = area_values(start, stop)
        columns.add("vertex-area.bin", words)
        # Only a bounded set of unequal append targets differs from the common
        # peripheral area. The center is a Python integer initial accumulator.
        peripheral = words[1:] if start == 0 else words
        scales = np.empty(len(peripheral), dtype="<u8")
        for word in np.unique(peripheral):
            scales[peripheral == word] = _scaled(int(word), area_quantum)
        total = fold_scaled(scales, total)
        if progress is not None:
            progress("oracle-fan-vertices", stop, vertices)
    total_word = scaled_word(total, quantum=area_quantum)
    weight_sum, weight_min, weight_max = 0, 2**64 - 1, 0
    for start in range(0, vertices, chunk_rows):
        stop = min(start + chunk_rows, vertices)
        words = area_values(start, stop)
        weights = weight_words(words, vertices, total_word)
        if start == 0:
            weight_sum = _scaled(int(weights[0]), -60)
        peripheral = weights[1:] if start == 0 else weights
        scales = np.empty(len(peripheral), dtype="<u8")
        for word in np.unique(peripheral):
            value = _scaled(int(word), -60)
            if value >= 2**64:
                raise ValueError(
                    "fan peripheral weight exceeds the explicit scale profile"
                )
            scales[peripheral == word] = value
        weight_sum = fold_scaled(scales, weight_sum)
        weight_min, weight_max = (
            min(weight_min, int(weights.min())),
            max(weight_max, int(weights.max())),
        )
        status = np.zeros(stop - start, dtype="u1")
        columns.add("contribution-status.bin", status)
        columns.add("weight.bin", weights)
        _add_rows(contribution_rows, start, (status, words, weights))
        if progress is not None:
            progress("oracle-fan-weights", stop, vertices)
    face_total = 0
    for start in range(0, faces, chunk_rows):
        stop = min(start + chunk_rows, faces)
        ordinals = np.arange(start, stop, dtype="<u8")
        triangles = np.column_stack(
            (
                np.zeros(len(ordinals), dtype="<u8"),
                ordinals + 1,
                (ordinals + 1) % ring + 1,
            )
        ).astype("<i4")
        status = np.zeros(len(ordinals), dtype="u1")
        words = np.array(areas, dtype="<u8")[ordinals % 2]
        for index, triangle in enumerate(extras):
            ordinal = ring + index
            if start <= ordinal < stop:
                offset = ordinal - start
                triangles[offset] = triangle
                status[offset] = 0 if index < 4 else 3 if index < 6 else 4
                words[offset] = areas[(triangle[1] - 1) % 2] if index < 4 else 0
        raw = np.empty(len(ordinals), dtype=[("count", "u1"), ("values", "<i4", (3,))])
        raw["count"], raw["values"] = 3, triangles
        source.update(raw.tobytes())
        for name, array in zip(
            ("triangles.bin", "face-status.bin", "face-area.bin"),
            (triangles, status, words),
            strict=True,
        ):
            columns.add(name, array)
        _add_rows(face_rows, start, (triangles, status, words))
        scales = np.empty(len(words), dtype="<u8")
        for word in np.unique(words):
            scales[words == word] = _scaled(int(word), area_quantum)
        face_total = fold_scaled(scales, face_total)
        if progress is not None:
            progress("oracle-fan-faces", stop, faces)
    all_areas = [area_base, *special_areas.values()]
    usable = ring + (4 if adverse else 0)
    return FanExpectation(
        radius,
        adverse,
        vertices,
        faces,
        source.record(),
        {name: digest.record() for name, digest in columns.hashes.items()},
        {
            "import_vertices": vertex_rows.record().sha256,
            "import_faces": face_rows.record().sha256,
            "contribution_vertices": contribution_rows.record().sha256,
        },
        3 * faces,
        f"{scaled_word(face_total, quantum=area_quantum):016x}",
        f"{total_word:016x}",
        f"{scaled_word(weight_sum, quantum=-60):016x}",
        (f"{min(all_areas):016x}", f"{max(all_areas):016x}"),
        (f"{weight_min:016x}", f"{weight_max:016x}"),
        {
            "usable": usable,
            "index-out-of-range": 0,
            "nonfinite-position": 0,
            "repeated-index": 2 if adverse else 0,
            "zero-computed-area": 2 if adverse else 0,
        },
        {
            "vertices_per_main_view": vertices,
            "usable_faces_per_main_view": usable,
            "rejected_corners": 12 if adverse else 0,
        },
        ring + extra_refs.get(0, 0),
        f"{special_areas[0]:016x}",
    )
