"""Independent complete grid oracle with permuted vertices and reordered faces.

Affine bijections map source ordinals to logical grid ordinals. Every vertex
accumulates its six incident thirds in the new source-face order, and global
folds follow the new source-vertex order. No production generator, association,
database, or floating-point kernel supplies an expected value.
"""

from __future__ import annotations

import hashlib
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from experiments.mesh_scale_grid import (
    ColumnSink,
    GridExpectation,
    Progress,
    _add_rows,  # pyright: ignore[reportPrivateUsage]
    _Columns,  # pyright: ignore[reportPrivateUsage]
    _grid_words,  # pyright: ignore[reportPrivateUsage]
    _Hash,  # pyright: ignore[reportPrivateUsage]
    _incident,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
    _weight_scaled,  # pyright: ignore[reportPrivateUsage]
    area_table,
)
from experiments.mesh_scale_integer import MAX_ROWS, grid_vertices, philox
from experiments.mesh_scale_rounding import (
    add_grid_scaled,
    fold_grid_scaled,
    fold_scaled,
    grid_area_scaled,
    scaled_word,
    weight_words,
)


@dataclass(frozen=True)
class AffinePermutation:
    count: int
    multiplier: int
    offset: int

    def __post_init__(self) -> None:
        if (
            any(
                type(value) is not int
                for value in (self.count, self.multiplier, self.offset)
            )
            or not 2 <= self.count <= 120000000
            or not 1 <= self.multiplier < 2**31
            or not 0 <= self.offset < self.count
            or math.gcd(self.multiplier, self.count) != 1
        ):
            raise ValueError(
                "permutation requires an explicit bounded affine bijection"
            )

    def logical(self, source: np.ndarray) -> np.ndarray:
        return (
            source.astype("<u8") * np.uint64(self.multiplier) + np.uint64(self.offset)
        ) % np.uint64(self.count)

    def source(self, logical: np.ndarray) -> np.ndarray:
        # Signed subtraction avoids wrapping a negative offset through 2**64.
        shifted = (logical.astype("<i8") - self.offset) % self.count
        return (shifted * pow(self.multiplier, -1, self.count) % self.count).astype(
            "<u8"
        )


@dataclass(frozen=True)
class PermutedExpectation(GridExpectation):
    vertex_multiplier: int
    vertex_offset: int
    face_multiplier: int
    face_offset: int


class _RandomHeights:
    def __init__(self, width: int, seed: int, noisy: bool) -> None:
        self.width: int = width
        self.seed: int = seed
        self.noisy: bool = noisy

    def at(self, indices: np.ndarray) -> np.ndarray:
        if not self.noisy:
            return np.zeros(len(indices), dtype="<i8")
        return (
            2 * (philox(self.seed, indices.astype("<u8"))[:, 0] & np.uint64(7))
        ).astype("<i8") - 7

    def slopes(
        self, cells: np.ndarray, odd: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        a = self.at(cells + odd)
        b = self.at(cells + 1 + odd * self.width)
        c = self.at(cells + self.width)
        dx, dy = np.where(odd != 0, b - c, b - a), np.where(odd != 0, b - a, c - a)
        return np.abs(dx) // 2, np.abs(dy) // 2


def build_permuted_expectation(
    width: int,
    height: int,
    workdir: Path,
    *,
    seed: int = 7,
    noisy: bool = True,
    vertex_multiplier: int = 131071,
    vertex_offset: int = 17,
    face_multiplier: int = 524287,
    face_offset: int = 29,
    chunk_rows: int = MAX_ROWS,
    column_sink: ColumnSink | None = None,
    progress: Progress | None = None,
) -> PermutedExpectation:
    if (
        type(width) is not int
        or type(height) is not int
        or not 2 <= width <= 10000
        or not 2 <= height <= 6000
        or type(seed) is not int
        or not 0 <= seed < 2**64
        or type(noisy) is not bool
        or type(chunk_rows) is not int
        or not 1 <= chunk_rows <= MAX_ROWS
    ):
        raise ValueError("permuted grid exceeds its explicit S6 profile")
    vertices, faces = width * height, 2 * (width - 1) * (height - 1)
    # Offsets are explicit recipe inputs, not silently reduced modulo population.
    vertex_map, face_map = (
        AffinePermutation(vertices, vertex_multiplier, vertex_offset),
        AffinePermutation(faces, face_multiplier, face_offset),
    )
    areas, thirds = area_table()
    table_hash = hashlib.sha256(areas.tobytes() + thirds.tobytes()).hexdigest()
    heights = _RandomHeights(width, seed, noisy)
    source = _Hash(
        (
            "ply\nformat binary_little_endian 1.0\ncomment scansor-mesh-permuted-grid-v1\n"
            f"element vertex {vertices}\nproperty float x\nproperty float y\nproperty float z\n"
            f"element face {faces}\nproperty list uchar int vertex_indices\nend_header\n"
        ).encode("ascii")
    )
    columns = _Columns(column_sink)
    vertex_rows, face_rows, contribution_rows = (
        _rows("mesh-import-vertices-v1", vertices),
        _rows("mesh-import-faces-v1", faces),
        _rows("mesh-contribution-vertices-v1", vertices),
    )
    face_total = vertex_total = weight_total = references = 0
    area_min = weight_min = 2**64 - 1
    area_max = weight_max = 0
    with (
        tempfile.TemporaryDirectory(
            prefix="scansor-permuted-oracle-", dir=workdir
        ) as directory,
        (Path(directory) / "areas.bin").open("w+b") as stream,
    ):
        for start in range(0, vertices, chunk_rows):
            stop = min(start + chunk_rows, vertices)
            logical = vertex_map.logical(np.arange(start, stop, dtype="<u8"))
            xyz = grid_vertices(width, height, logical, seed=seed, noisy=noisy)
            source.update(xyz.tobytes())
            keys = np.full((len(logical), 6), np.iinfo(np.uint64).max, dtype="<u8")
            terms = np.zeros((len(logical), 6), dtype="<u8")
            counts = np.zeros(len(logical), dtype="<u8")
            for slot, (mask, cells, odd) in enumerate(
                _incident(logical.astype("<i8"), width, height)
            ):
                if len(cells):
                    source_faces = face_map.source(
                        2 * ((cells // width) * (width - 1) + cells % width) + odd
                    )
                    dx, dy = heights.slopes(cells, odd)
                    keys[mask, slot] = source_faces
                    terms[mask, slot] = grid_area_scaled(thirds[dx, dy])
                    counts += mask.astype("<u8")
            ordered = np.take_along_axis(terms, np.argsort(keys, axis=1), axis=1)
            accumulator = np.zeros(len(logical), dtype="<u8")
            for slot in range(6):
                accumulator = add_grid_scaled(accumulator, ordered[:, slot])
            words = _grid_words(accumulator)
            _ = stream.write(words.tobytes())
            vertex_total = fold_grid_scaled(accumulator, vertex_total)
            area_min, area_max = (
                min(area_min, int(words.min())),
                max(area_max, int(words.max())),
            )
            zero = np.zeros(len(logical), dtype="u1")
            for name, array in zip(
                (
                    "xyz.bin",
                    "vertex-status.bin",
                    "normal-status.bin",
                    "reference-count.bin",
                    "vertex-area.bin",
                ),
                (xyz, zero, zero, counts, words),
                strict=True,
            ):
                columns.add(name, array)
            _add_rows(vertex_rows, start, (xyz, zero, zero, counts))
            references += int(counts.sum(dtype="<u8"))
            if progress is not None:
                progress("oracle-permuted-vertices-and-areas", stop, vertices)
        for start in range(0, faces, chunk_rows):
            stop = min(start + chunk_rows, faces)
            logical = face_map.logical(np.arange(start, stop, dtype="<u8"))
            cells = (logical // 2 // (width - 1)) * width + (logical // 2 % (width - 1))
            odd = logical % 2
            corners = np.column_stack(
                (cells + odd, cells + 1 + odd * width, cells + width)
            )
            triangles = vertex_map.source(corners).astype("<i4")
            dx, dy = heights.slopes(cells, odd)
            words, zero = areas[dx, dy], np.zeros(len(logical), dtype="u1")
            face_total = fold_grid_scaled(grid_area_scaled(words), face_total)
            packed = np.empty(
                len(logical), dtype=[("count", "u1"), ("values", "<i4", (3,))]
            )
            packed["count"], packed["values"] = 3, triangles
            source.update(packed.tobytes())
            for name, array in zip(
                ("triangles.bin", "face-status.bin", "face-area.bin"),
                (triangles, zero, words),
                strict=True,
            ):
                columns.add(name, array)
            _add_rows(face_rows, start, (triangles, zero, words))
            if progress is not None:
                progress("oracle-permuted-faces", stop, faces)
        stream.flush()
        _ = stream.seek(0)
        for start in range(0, vertices, chunk_rows):
            stop = min(start + chunk_rows, vertices)
            raw = stream.read(8 * (stop - start))
            if len(raw) != 8 * (stop - start):
                raise ValueError("permuted area file is truncated")
            words = np.frombuffer(raw, dtype="<u8")
            weights = weight_words(words, vertices, scaled_word(vertex_total))
            weight_total = fold_scaled(_weight_scaled(weights), weight_total)
            weight_min, weight_max = (
                min(weight_min, int(weights.min())),
                max(weight_max, int(weights.max())),
            )
            status = np.zeros(len(words), dtype="u1")
            columns.add("contribution-status.bin", status)
            columns.add("weight.bin", weights)
            _add_rows(contribution_rows, start, (status, words, weights))
            if progress is not None:
                progress("oracle-permuted-weights", stop, vertices)
    if references != 3 * faces:
        raise ValueError("permuted reference population differs")
    return PermutedExpectation(
        width=width,
        height=height,
        seed=seed,
        noisy=noisy,
        vertices=vertices,
        faces=faces,
        source=source.record(),
        columns={name: digest.record() for name, digest in columns.hashes.items()},
        row_digests={
            "import_vertices": vertex_rows.record().sha256,
            "import_faces": face_rows.record().sha256,
            "contribution_vertices": contribution_rows.record().sha256,
        },
        area_table_sha256=table_hash,
        in_range_source_corners=references,
        face_area_sum_bits=f"{scaled_word(face_total):016x}",
        vertex_area_sum_bits=f"{scaled_word(vertex_total):016x}",
        weight_sum_bits=f"{scaled_word(weight_total, quantum=-55):016x}",
        vertex_area_range_bits=(f"{area_min:016x}", f"{area_max:016x}"),
        weight_range_bits=(f"{weight_min:016x}", f"{weight_max:016x}"),
        vertex_multiplier=vertex_multiplier,
        vertex_offset=vertex_offset,
        face_multiplier=face_multiplier,
        face_offset=face_offset,
    )
