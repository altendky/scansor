"""Native fan source construction through the production isolated PLY writer.

No expectation/oracle code supplies source coordinates or triangles. Compare the
complete resulting file with an independently frozen fan expectation before use.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO

import numpy as np

from scansor._plyio import Writer
from scansor.mesh_ply import mesh_header, mesh_layout


@dataclass(frozen=True)
class FanRecipe:
    radius: int
    adverse: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.radius) is not int
            or self.radius < 4
            or self.radius > 262144
            or self.radius.bit_count() != 1
            or type(self.adverse) is not bool
        ):
            raise ValueError(
                "fan requires a power-of-two radius in 4..262144 and a bool variant"
            )

    @property
    def vertex_count(self) -> int:
        return 4 * self.radius + 1

    @property
    def face_count(self) -> int:
        return self.vertex_count - 1 + (8 if self.adverse else 0)

    def _range(self, start: int, stop: int, population: int) -> None:
        if (
            type(start) is not int
            or type(stop) is not int
            or not 0 <= start <= stop <= population
            or stop - start > 65536
        ):
            raise ValueError("fan source request exceeds its bounded range")

    def vertices(self, start: int, stop: int) -> np.ndarray:
        self._range(start, stop, self.vertex_count)
        ids = np.arange(start, stop, dtype="<i8")
        perimeter = ids[ids != 0] - 1
        side, within = np.divmod(perimeter, self.radius)
        bottom = np.column_stack(
            (
                4 * (within // 2) + within % 2 - self.radius,
                np.full(len(within), -self.radius, dtype="<i8"),
            )
        )
        xy = bottom.copy()
        # Rotate integer coordinates before binary32 conversion, so zero remains
        # positive zero and every admitted coordinate remains exactly encoded.
        for rotation in range(1, 4):
            bottom = np.column_stack((-bottom[:, 1], bottom[:, 0]))
            xy[side == rotation] = bottom[side == rotation]
        result = np.zeros((len(ids), 3), dtype="<f4")
        result[ids != 0, :2] = xy
        return result

    def faces(self, start: int, stop: int) -> np.ndarray:
        self._range(start, stop, self.face_count)
        count = self.vertex_count - 1
        ids = np.arange(start, stop, dtype="<i8")
        source = ids.copy()
        if self.adverse:
            for appended, original in enumerate(
                (0, 65535 % count, 65536 % count, count - 1)
            ):
                source[ids == count + appended] = original
        result = np.column_stack(
            (np.zeros(len(ids), dtype="<i8"), source + 1, (source + 1) % count + 1)
        ).astype("<i4")
        if self.adverse:
            for appended, triple in enumerate(
                (
                    (0, 1, 1),
                    (0, count // 2 + 1, count // 2 + 1),
                    (1, 2, 3),
                    (self.radius + 1, self.radius + 2, self.radius + 3),
                ),
                start=4,
            ):
                result[ids == count + appended] = triple
        return result


def write_fan(stream: BinaryIO, recipe: FanRecipe, *, chunk_rows: int = 65536) -> None:
    if type(chunk_rows) is not int or not 1 <= chunk_rows <= 65536:
        raise ValueError("invalid fan writer batch bound")
    layout = mesh_layout(
        mesh_header(
            recipe.vertex_count, recipe.face_count, comments=("scansor-mesh-fan-v1",)
        )
    )
    writer = Writer(stream, layout, max_range_bytes=13 * chunk_rows)
    for start in range(0, recipe.vertex_count, chunk_rows):
        stop = min(start + chunk_rows, recipe.vertex_count)
        xyz = recipe.vertices(start, stop)
        rows = np.empty(len(xyz), dtype=layout.element("vertex").dtype)
        for axis, name in enumerate(("x", "y", "z")):
            rows[name] = xyz[:, axis]
        writer.write_range("vertex", start, rows)
    for start in range(0, recipe.face_count, chunk_rows):
        stop = min(start + chunk_rows, recipe.face_count)
        rows = np.empty(stop - start, dtype=layout.element("face").dtype)
        rows["vertex_indices"]["count"] = 3
        rows["vertex_indices"]["values"] = recipe.faces(start, stop)
        writer.write_range("face", start, rows)
    writer.finish()
