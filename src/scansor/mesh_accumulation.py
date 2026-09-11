"""Checked source-corner ordering and exact scalar folds across bounded batches.

The sorter is external to this semantic kernel. Its input must also be verified
against canonical source corners; ordering/counts alone cannot bind their values.
One arithmetic gate runs per input batch, not per vertex. Never substitute chunk
subtotals, a scatter reduction, or cumsum/subtraction for these ordered additions.
"""

from __future__ import annotations

import math
from collections.abc import Generator
from dataclasses import dataclass

import numpy as np

from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import NumericProfileError, check_arithmetic

CORNER_REVISION = "all-in-range-corners-vertex-face-corner-order-v1"


@dataclass(frozen=True)
class VertexContributions:
    start: int
    references: np.ndarray
    areas: np.ndarray


class CornerFold:
    """Single-use bounded fold; consume each yielded batch before advancing.

    All in-range corners count, even when their allocated area is zero. Orphan
    gaps are filled in bounded vector operations, without allocating by mesh size.
    Only one unfinished vertex accumulator survives an input-batch boundary.
    """

    def __init__(
        self, *, vertices: int, faces: int, corners: int, batch_rows: int
    ) -> None:
        if (
            type(vertices) is not int
            or not 1 <= vertices <= 2**31
            or type(faces) is not int
            or not 0 <= faces <= (2**64 - 1) // 3
            or type(corners) is not int
            or not 0 <= corners <= 3 * faces
            or type(batch_rows) is not int
            or batch_rows < 1
        ):
            raise MeshImportError(
                "structure", "corner-fold", "invalid population/batch bounds"
            )
        self.vertices: int = vertices
        self.faces: int = faces
        self.corners: int = corners
        self.batch_rows: int = batch_rows
        self.seen: int = 0
        self._last: tuple[int, int, int] | None = None
        self._vertex: int = 0
        self._references: int = 0
        self._area: float = 0.0
        self._start: int = 0
        self._filled: int = 0
        self._counts: np.ndarray = np.zeros(batch_rows, dtype="<u8")
        self._areas: np.ndarray = np.zeros(batch_rows, dtype="<f8")
        self._failed: bool = False
        self._active: bool = False
        self._finished: bool = False

    def _output(self) -> VertexContributions:
        counts, areas = (
            self._counts[: self._filled].copy(),
            self._areas[: self._filled].copy(),
        )
        counts.flags.writeable = areas.flags.writeable = False
        result = VertexContributions(self._start, counts, areas)
        self._start += self._filled
        self._filled = 0
        self._counts.fill(0)
        self._areas.fill(0)
        return result

    def _advance(self, stop: int) -> Generator[VertexContributions]:
        if stop > self._vertex:
            self._counts[self._filled] = self._references
            self._areas[self._filled] = self._area
        while self._vertex < stop:
            count = min(stop - self._vertex, self.batch_rows - self._filled)
            self._filled += count
            self._vertex += count
            if self._filled == self.batch_rows:
                yield self._output()
        self._references, self._area = 0, 0.0

    def _begin(self) -> None:
        if self._failed or self._finished or self._active:
            raise MeshImportError(
                "integrity", "corner-fold", "fold is failed, finished or still active"
            )
        self._active = True

    def consume(
        self,
        vertex: np.ndarray,
        face: np.ndarray,
        corner: np.ndarray,
        area_words: np.ndarray,
    ) -> Generator[VertexContributions]:
        self._begin()
        try:
            check_arithmetic()
            count = len(vertex) if vertex.ndim else -1
            arrays = (
                (vertex, "<u8"),
                (face, "<u8"),
                (corner, "u1"),
                (area_words, "<u8"),
            )
            if (
                not 0 <= count <= self.batch_rows
                or self.seen + count > self.corners
                or any(
                    a.shape != (count,) or a.dtype != np.dtype(dtype)
                    for a, dtype in arrays
                )
                or np.any(vertex >= self.vertices)
                or np.any(face >= self.faces)
                or np.any(corner > 2)
            ):
                raise MeshImportError(
                    "integrity",
                    "corner-fold",
                    "invalid corner dtype, bounds or cardinality",
                )
            areas = area_words.view("<f8")
            if not np.all(np.isfinite(areas)) or np.any(np.signbit(areas)):
                raise NumericProfileError(
                    "corner allocations require finite nonnegative values and +0"
                )
            # Strict total order rejects duplicate source keys inside and between batches.
            if count:
                ordered = (vertex[1:] > vertex[:-1]) | (
                    (vertex[1:] == vertex[:-1])
                    & (
                        (face[1:] > face[:-1])
                        | ((face[1:] == face[:-1]) & (corner[1:] > corner[:-1]))
                    )
                )
                first = (int(vertex[0]), int(face[0]), int(corner[0]))
                if not np.all(ordered) or (
                    self._last is not None and first <= self._last
                ):
                    raise MeshImportError(
                        "integrity",
                        "corner-fold",
                        "duplicated or reordered source corner",
                    )
                for raw_vertex, raw_area in zip(vertex, areas, strict=True):
                    current = int(raw_vertex)
                    if current != self._vertex:
                        yield from self._advance(current)
                    self._references += 1
                    if self._references > 2**64 - 1:
                        raise MeshImportError(
                            "integrity",
                            "corner-fold",
                            "reference count overflow",
                            row=current,
                        )
                    value = float(raw_area)
                    if value > 0:
                        self._area = self._area + value
                        if not math.isfinite(self._area):
                            raise NumericProfileError(
                                f"nonfinite vertex accumulation at source vertex {current}"
                            )
                self._last = (int(vertex[-1]), int(face[-1]), int(corner[-1]))
            self.seen += count
        except BaseException:
            self._failed = True
            raise
        finally:
            self._active = False

    def finish(self) -> Generator[VertexContributions]:
        self._begin()
        try:
            if self.seen != self.corners:
                raise MeshImportError(
                    "integrity", "corner-fold", "missing source corners"
                )
            yield from self._advance(self.vertices)
            if self._filled:
                yield self._output()
            self._finished = True
        except BaseException:
            self._failed = True
            raise
        finally:
            self._active = False
