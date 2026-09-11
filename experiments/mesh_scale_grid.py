"""Freeze full S6 grid expectations without production readers or arithmetic.

The grid's dyadic cross products and squared norm are exact before square root.
Only 64 noisy slope pairs exist; a rational oracle independently rounds their
areas and corner thirds. Integer operations then construct every source record,
canonical column, row digest, ordered accumulation, and normalized weight.

Working arrays contain at most 65,536 rows. Heights and vertex areas use owned
temporary files, accessed through bounded reads, never population-sized maps.
This module is an opt-in experiment oracle, not an ingestion backend.
"""

from __future__ import annotations

import hashlib
import struct
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO, final

import numpy as np

from experiments.mesh_scale_integer import MAX_ROWS, dyadic_f32, grid_vertices, philox
from experiments.mesh_scale_rounding import (
    FRACTION,
    LEADING,
    add_grid_scaled,
    fold_grid_scaled,
    fold_scaled,
    grid_area_scaled,
    scaled_word,
    weight_words,
)
from tests import mesh_rounding_oracle as rational_oracle

type ColumnSink = Callable[[str, bytes], None]
type Progress = Callable[[str, int, int], None]

_VERTEX_NAMES = (
    "xyz.bin",
    "vertex-status.bin",
    "normal-status.bin",
    "reference-count.bin",
)
_FACE_NAMES = ("triangles.bin", "face-status.bin", "face-area.bin")
_CONTRIBUTION_NAMES = (
    "contribution-status.bin",
    "vertex-area.bin",
    "weight.bin",
)


@dataclass(frozen=True)
class ColumnHash:
    bytes: int
    sha256: str


@dataclass(frozen=True)
class GridExpectation:
    width: int
    height: int
    seed: int
    noisy: bool
    vertices: int
    faces: int
    source: ColumnHash
    columns: dict[str, ColumnHash]
    row_digests: dict[str, str]
    area_table_sha256: str
    in_range_source_corners: int
    face_area_sum_bits: str
    vertex_area_sum_bits: str
    weight_sum_bits: str
    vertex_area_range_bits: tuple[str, str]
    weight_range_bits: tuple[str, str]


@final
class _Hash:
    def __init__(self, initial: bytes = b"") -> None:
        self.digest = hashlib.sha256(initial)
        self.bytes = len(initial)

    def update(self, data: bytes) -> None:
        self.digest.update(data)
        self.bytes += len(data)

    def record(self) -> ColumnHash:
        return ColumnHash(self.bytes, self.digest.hexdigest())


@final
class _Columns:
    def __init__(self, sink: ColumnSink | None) -> None:
        self.sink = sink
        self.hashes = {
            name: _Hash()
            for name in (*_VERTEX_NAMES, *_FACE_NAMES, *_CONTRIBUTION_NAMES)
        }

    def add(self, name: str, values: np.ndarray) -> None:
        data = values.tobytes(order="C")
        self.hashes[name].update(data)
        if self.sink is not None:
            self.sink(name, data)


def _rows(tag: str, count: int) -> _Hash:
    return _Hash(tag.encode("ascii") + b"\0" + struct.pack("<Q", count))


def _add_rows(digest: _Hash, start: int, arrays: tuple[np.ndarray, ...]) -> None:
    count = len(arrays[0])
    dtype = np.dtype(
        [("ordinal", "<u8")]
        + [
            (f"column{i}", array.dtype, array.shape[1:])
            for i, array in enumerate(arrays)
        ]
    )
    records = np.empty(count, dtype=dtype)
    records["ordinal"] = np.arange(start, start + count, dtype="<u8")
    for i, array in enumerate(arrays):
        records[f"column{i}"] = array
    digest.update(records.tobytes())


def area_table() -> tuple[np.ndarray, np.ndarray]:
    """Index by abs(dx)/2, abs(dy)/2, with heights measured in 1/256 units."""
    areas, thirds = np.empty((8, 8), dtype="<u8"), np.empty((8, 8), dtype="<u8")
    for x in range(8):
        for y in range(8):
            squared_norm = Fraction(144) + Fraction(64 * x * x + 36 * y * y, 65536)
            area = rational_oracle.multiply(
                rational_oracle.square_root(rational_oracle.rounded(squared_norm)),
                0x3FE0000000000000,
            )
            areas[x, y] = area
            thirds[x, y] = rational_oracle.divide(area, 0x4008000000000000)
    areas.flags.writeable = thirds.flags.writeable = False
    return areas, thirds


@final
class _Heights:
    def __init__(self, stream: BinaryIO, width: int) -> None:
        self.stream = stream
        self.width = width

    def at(self, indices: np.ndarray) -> np.ndarray:
        first, last = int(indices.min()), int(indices.max()) + 1
        if first < 0 or last - first > MAX_ROWS + 2 * self.width + 2:
            raise ValueError("grid oracle height read exceeds its bounded window")
        _ = self.stream.seek(first)
        data = self.stream.read(last - first)
        if len(data) != last - first:
            raise ValueError("grid oracle height file is truncated")
        return np.frombuffer(data, dtype="i1")[indices - first].astype("<i8")

    def slopes(
        self, cells: np.ndarray, odd: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        heights = self.at(
            np.column_stack(
                (cells + odd, cells + 1 + odd * self.width, cells + self.width)
            )
        )
        dx = np.where(
            odd != 0, heights[:, 1] - heights[:, 2], heights[:, 1] - heights[:, 0]
        )
        dy = np.where(
            odd != 0, heights[:, 1] - heights[:, 0], heights[:, 2] - heights[:, 0]
        )
        return np.abs(dx) // 2, np.abs(dy) // 2


def _incident(
    indices: np.ndarray, width: int, height: int
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    row, column = indices // width, indices % width
    result: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    # The source face ordinal is 2 * (cell_row * (width-1) + cell_column) + odd.
    # These six slots are sorted by that ordinal, including boundary omissions.
    for dr, dc, odd in (
        (-1, -1, 1),
        (-1, 0, 0),
        (-1, 0, 1),
        (0, -1, 0),
        (0, -1, 1),
        (0, 0, 0),
    ):
        r, c = row + dr, column + dc
        mask = (r >= 0) & (r < height - 1) & (c >= 0) & (c < width - 1)
        cells = r[mask] * width + c[mask]
        result.append((mask, cells, np.full(len(cells), odd, dtype="<i8")))
    return result


def _grid_words(scaled: np.ndarray) -> np.ndarray:
    result = np.zeros(len(scaled), dtype="<u8")
    for shift in (0, 1, 2):
        mask = (scaled >= 2 ** (52 + shift)) & (scaled < 2 ** (53 + shift))
        if np.any(scaled[mask] & np.uint64(2**shift - 1)):
            raise ValueError("grid oracle vertex area was not rounded")
        result[mask] = np.uint64((1024 + shift) << 52) | (
            (scaled[mask] >> np.uint64(shift)) & FRACTION
        )
    if np.any((scaled != 0) & (result == 0)):
        raise ValueError("grid oracle vertex area outside [2,16)")
    return result


def _weight_scaled(words: np.ndarray) -> np.ndarray:
    exponents = (words >> np.uint64(52)).astype("<i8")
    if np.any(exponents < 1020) or np.any(exponents > 1024):
        raise ValueError("grid oracle normalized weight outside [1/8,4)")
    return ((words & FRACTION) | LEADING) << (exponents - 1020).astype("<u8")


def build_grid_expectation(
    width: int,
    height: int,
    workdir: Path,
    *,
    seed: int = 7,
    noisy: bool = False,
    chunk_rows: int = MAX_ROWS,
    column_sink: ColumnSink | None = None,
    progress: Progress | None = None,
) -> GridExpectation:
    """Visit every row and return hashes; no Scansor source or pipeline is used."""
    if (
        type(width) is not int
        or not 2 <= width <= 10_000
        or type(height) is not int
        or not 2 <= height <= 6_000
        or type(seed) is not int
        or not 0 <= seed < 2**64
        or type(noisy) is not bool
        or type(chunk_rows) is not int
        or not 1 <= chunk_rows <= MAX_ROWS
    ):
        raise ValueError("grid oracle parameters exceed its explicit S6 profile")
    vertices, faces = width * height, 2 * (width - 1) * (height - 1)
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment scansor-mesh-recipe-v1\n"
        f"element vertex {vertices}\nproperty float x\nproperty float y\nproperty float z\n"
        f"element face {faces}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    source = _Hash(header)
    columns = _Columns(column_sink)
    vertex_rows = _rows("mesh-import-vertices-v1", vertices)
    face_rows = _rows("mesh-import-faces-v1", faces)
    contribution_rows = _rows("mesh-contribution-vertices-v1", vertices)
    areas, thirds = area_table()
    table_hash = hashlib.sha256(areas.tobytes() + thirds.tobytes()).hexdigest()
    references, face_total, vertex_total, weight_total = 0, 0, 0, 0
    area_min, area_max, weight_min, weight_max = 2**64 - 1, 0, 2**64 - 1, 0

    def report(phase: str, rows: int, count: int) -> None:
        if progress is not None:
            progress(phase, rows, count)

    with tempfile.TemporaryDirectory(
        prefix="scansor-grid-oracle-", dir=workdir
    ) as name:
        root = Path(name)
        with (
            (root / "heights.bin").open("w+b") as height_stream,
            (root / "areas.bin").open("w+b") as area_stream,
        ):
            heights = _Heights(height_stream, width)
            report("oracle-vertices", 0, vertices)
            for start in range(0, vertices, chunk_rows):
                stop = min(start + chunk_rows, vertices)
                indices = np.arange(start, stop, dtype="<u8")
                xyz = grid_vertices(width, height, indices)
                coefficients = np.zeros(stop - start, dtype="<i8")
                if noisy:
                    coefficients = (
                        2 * (philox(seed, indices)[:, 0] & np.uint64(7))
                    ).astype("<i8") - 7
                    xyz[:, 2] = dyadic_f32(coefficients, -8)
                _ = height_stream.write(coefficients.astype("i1").tobytes())
                source.update(xyz.tobytes())
                counts = np.zeros(stop - start, dtype="<u8")
                for mask, _, _ in _incident(indices.astype("<i8"), width, height):
                    counts += mask.astype("<u8")
                references += int(counts.sum(dtype="<u8"))
                zero = np.zeros(stop - start, dtype="u1")
                values = (xyz, zero, zero, counts)
                for filename, data in zip(_VERTEX_NAMES, values, strict=True):
                    columns.add(filename, data)
                _add_rows(vertex_rows, start, values)
                report("oracle-vertices", stop, vertices)
            height_stream.flush()
            report("oracle-faces", 0, faces)
            for start in range(0, faces, chunk_rows):
                stop = min(start + chunk_rows, faces)
                indices = np.arange(start, stop, dtype="<i8")
                cells, odd = indices // 2, indices % 2
                a = (cells // (width - 1)) * width + cells % (width - 1)
                triangles = np.column_stack(
                    (a + odd, a + 1 + odd * width, a + width)
                ).astype("<i4")
                packed = np.empty(
                    stop - start, dtype=[("count", "u1"), ("indices", "<i4", (3,))]
                )
                packed["count"], packed["indices"] = 3, triangles
                source.update(packed.tobytes())
                dx, dy = heights.slopes(a, odd)
                face_areas = areas[dx, dy]
                face_total = fold_grid_scaled(grid_area_scaled(face_areas), face_total)
                values = (triangles, np.zeros(stop - start, dtype="u1"), face_areas)
                for filename, data in zip(_FACE_NAMES, values, strict=True):
                    columns.add(filename, data)
                _add_rows(face_rows, start, values)
                report("oracle-faces", stop, faces)
            report("oracle-areas", 0, vertices)
            for start in range(0, vertices, chunk_rows):
                stop = min(start + chunk_rows, vertices)
                indices = np.arange(start, stop, dtype="<i8")
                accumulator = np.zeros(stop - start, dtype="<u8")
                for mask, cells, odd in _incident(indices, width, height):
                    if len(cells):
                        dx, dy = heights.slopes(cells, odd)
                        accumulator[mask] = add_grid_scaled(
                            accumulator[mask], grid_area_scaled(thirds[dx, dy])
                        )
                vertex_areas = _grid_words(accumulator)
                _ = area_stream.write(vertex_areas.tobytes())
                vertex_total = fold_grid_scaled(accumulator, vertex_total)
                area_min = min(area_min, int(vertex_areas.min()))
                area_max = max(area_max, int(vertex_areas.max()))
                columns.add("vertex-area.bin", vertex_areas)
                report("oracle-areas", stop, vertices)
            area_stream.flush()
            _ = area_stream.seek(0)
            report("oracle-weights", 0, vertices)
            for start in range(0, vertices, chunk_rows):
                stop = min(start + chunk_rows, vertices)
                payload = area_stream.read(8 * (stop - start))
                if len(payload) != 8 * (stop - start):
                    raise ValueError("grid oracle area file is truncated")
                vertex_areas = np.frombuffer(payload, dtype="<u8")
                weights = weight_words(
                    vertex_areas, vertices, scaled_word(vertex_total)
                )
                weight_total = fold_scaled(_weight_scaled(weights), weight_total)
                weight_min = min(weight_min, int(weights.min()))
                weight_max = max(weight_max, int(weights.max()))
                status = np.zeros(stop - start, dtype="u1")
                columns.add("contribution-status.bin", status)
                columns.add("weight.bin", weights)
                _add_rows(contribution_rows, start, (status, vertex_areas, weights))
                report("oracle-weights", stop, vertices)
    if references != 3 * faces:
        raise ValueError("independent grid corner accounting disagrees")
    return GridExpectation(
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
    )
