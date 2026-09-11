"""Bound semantic category names and floating summary encodings for mesh stages."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from scansor.mesh_controls import Control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import float_bits

FACE_NAMES = (
    "usable",
    "index-out-of-range",
    "nonfinite-position",
    "repeated-index",
    "zero-computed-area",
)
VERTEX_NAMES = ("finite-position", "nonfinite-position")
NORMAL_NAMES = ("absent", "finite-nonzero", "zero-vector", "nonfinite-vector")
CONTRIBUTION_NAMES = ("eligible", "nonfinite-position", "isolated", "no-usable-area")


def add_category_counts(status: np.ndarray, totals: list[int]) -> None:
    if np.any(status >= len(totals)):
        raise MeshImportError(
            "integrity", "category-counts", "unknown disposition code"
        )
    for code in range(len(totals)):
        totals[code] += int(np.count_nonzero(status == code))


def named_counts(names: tuple[str, ...], values: list[int]) -> dict[str, Control]:
    return dict(zip(names, values, strict=True))


def measure(
    population: str, measure: str, unit: str, value: float
) -> dict[str, Control]:
    return {
        "population": population,
        "measure": measure,
        "unit": unit,
        "value_bits": float_bits(value),
    }


@dataclass
class ValueRange:
    minimum: float | None = None
    maximum: float | None = None

    def add(self, values: np.ndarray) -> None:
        if len(values):
            minimum, maximum = float(np.min(values)), float(np.max(values))
            self.minimum = (
                minimum if self.minimum is None else min(self.minimum, minimum)
            )
            self.maximum = (
                maximum if self.maximum is None else max(self.maximum, maximum)
            )

    def record(self, measure: str, unit: str) -> dict[str, Control]:
        return {
            "population": "eligible-vertices",
            "measure": measure,
            "unit": unit,
            "minimum_bits": None if self.minimum is None else float_bits(self.minimum),
            "maximum_bits": None if self.maximum is None else float_bits(self.maximum),
        }


def import_summary(
    *,
    vertices: int,
    faces: int,
    vertex_counts: list[int],
    normal_counts: list[int],
    face_counts: list[int],
    references: int,
    invalid_corners: int,
    face_total: float,
) -> dict[str, Control]:
    return {
        "revision": "mesh-import-summary-v1",
        "vertices": vertices,
        "faces": faces,
        "vertex_category_counts": named_counts(VERTEX_NAMES, vertex_counts),
        "normal_category_counts": named_counts(NORMAL_NAMES, normal_counts),
        "face_category_counts": named_counts(FACE_NAMES, face_counts),
        "in_range_source_corners": references,
        "out_of_range_source_corners": invalid_corners,
        "usable_face_area_sum": measure(
            "usable-source-faces",
            "source-order-left-fold-of-face-areas",
            "source-coordinate-squared",
            face_total,
        ),
    }


def contribution_summary(
    *,
    vertices: int,
    counts: list[int],
    area_total: float,
    area_range: ValueRange,
    weight_total: float,
    weight_range: ValueRange,
) -> dict[str, Control]:
    return {
        "revision": "mesh-contribution-summary-v1",
        "vertices": vertices,
        "category_counts": named_counts(CONTRIBUTION_NAMES, counts),
        "eligible_area_sum": measure(
            "eligible-vertices",
            "source-order-left-fold-of-vertex-areas",
            "source-coordinate-squared",
            area_total,
        ),
        "vertex_area_range": area_range.record(
            "accumulated-vertex-area", "source-coordinate-squared"
        ),
        "weight_range": weight_range.record("normalized-weight", "dimensionless"),
        "weight_sum": measure(
            "eligible-vertices",
            "source-order-left-fold-of-normalized-weights",
            "dimensionless",
            weight_total,
        ),
    }
