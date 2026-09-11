"""Bounded display-only ID, color and coordinate operations for mesh audit views.

These never change authoritative mesh columns or imply viewer preservation.
Origin translation and power-of-two scaling have explicit binary64 rounding
steps; coordinate information lost by the forward/inverse pair is counted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from scansor.mesh_controls import Control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import bits_float, check_arithmetic

MAX_ROWS = 65_536
ZERO_BITS = "0000000000000000"


def _shape(array: np.ndarray, width: int | None = None) -> int:
    if (
        array.ndim != (1 if width is None else 2)
        or not 0 <= len(array) <= MAX_ROWS
        or (width is not None and array.shape[1] != width)
    ):
        raise MeshImportError(
            "structure", "display-numeric", "invalid bounded array shape"
        )
    return len(array)


def split_ids(values: np.ndarray) -> np.ndarray:
    """Four exact binary32 integers per uint64 ID, least-significant digit first."""
    count = _shape(values)
    if values.dtype.kind != "u" or values.dtype.itemsize != 8:
        raise MeshImportError("structure", "display-ids", "IDs must be uint64")
    result = np.empty((count, 4), dtype="<f4")
    for digit in range(4):
        result[:, digit] = (values >> np.uint64(16 * digit)) & np.uint64(0xFFFF)
    result.flags.writeable = False
    return result


def join_ids(digits: np.ndarray) -> np.ndarray:
    """Reject lossy or invalid ID fields before using them as source row ordinals."""
    count = _shape(digits, 4)
    if (
        digits.dtype.kind != "f"
        or digits.dtype.itemsize not in (4, 8)
        or not np.all(np.isfinite(digits))
        or np.any(digits < 0)
        or np.any(digits > 65535)
        or np.any(digits != np.floor(digits))
    ):
        raise MeshImportError("integrity", "display-ids", "invalid 16-bit ID digit")
    result = np.zeros(count, dtype="<u8")
    for digit in range(4):
        result |= digits[:, digit].astype("<u8") << np.uint64(16 * digit)
    result.flags.writeable = False
    return result


def _statuses(values: np.ndarray, allowed: tuple[int, ...]) -> int:
    count = _shape(values)
    if values.dtype != np.dtype("u1") or not np.all(np.isin(values, allowed)):
        raise MeshImportError("integrity", "display-colors", "invalid display status")
    return count


def validity_colors(status: np.ndarray) -> np.ndarray:
    _ = _statuses(status, (0, 2, 3))
    palette = np.array(
        ((40, 170, 80), (0, 0, 0), (255, 165, 0), (220, 50, 50)), dtype="u1"
    )
    result = palette[status]
    result.flags.writeable = False
    return result


def rejected_colors(face_status: np.ndarray) -> np.ndarray:
    _ = _statuses(face_status, (1, 2, 3, 4))
    palette = np.array(
        ((180, 0, 180), (220, 50, 50), (255, 165, 0), (80, 120, 220)), dtype="u1"
    )
    result = palette[face_status - 1]
    result.flags.writeable = False
    return result


def weight_colors(
    status: np.ndarray, areas: np.ndarray, *, maximum: float
) -> np.ndarray:
    """Raw-area ramp using the complete eligible maximum, not a batch maximum."""
    check_arithmetic()
    count = _statuses(status, (0, 2, 3))
    if (
        _shape(areas) != count
        or areas.dtype.kind != "f"
        or areas.dtype.itemsize != 8
        or not np.all(np.isfinite(areas))
        or np.any(areas < 0)
        or not math.isfinite(maximum)
        or maximum < 0
        or np.any(areas > maximum)
        or np.any((status == 0) != (areas > 0))
    ):
        raise MeshImportError(
            "integrity", "display-colors", "invalid area ramp population"
        )
    result = np.full((count, 3), 128, dtype="u1")
    eligible = status == 0
    if np.any(eligible):
        with np.errstate(under="ignore", over="raise", invalid="raise", divide="raise"):
            ratio = np.divide(areas[eligible], np.float64(maximum), dtype=np.float64)
            scaled = np.multiply(np.float64(255), ratio, dtype=np.float64)
            level = np.rint(scaled).astype("u1")
        result[eligible, 0] = level
        result[eligible, 1] = level
        result[eligible, 2] = 255 - level
    result.flags.writeable = False
    return result


@dataclass(frozen=True)
class TransformedRows:
    coordinates: np.ndarray
    roundtrip_mismatch_rows: int


@dataclass(frozen=True)
class DisplayTransform:
    origin_bits: tuple[str, str, str] = (ZERO_BITS, ZERO_BITS, ZERO_BITS)
    scale_power: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.origin_bits) is not tuple
            or len(self.origin_bits) != 3
            or any(type(value) is not str for value in self.origin_bits)
            or type(self.scale_power) is not int
            or not -1023 <= self.scale_power <= 1023
        ):
            raise MeshImportError(
                "structure", "display-transform", "invalid display transform"
            )
        if not all(math.isfinite(bits_float(value)) for value in self.origin_bits):
            raise MeshImportError(
                "structure", "display-transform", "origin must be finite"
            )

    def record(self) -> dict[str, Control]:
        return {
            "revision": "mesh-display-transform-v1",
            "origin_binary64_bits": list(self.origin_bits),
            "scale_power_of_two": self.scale_power,
            "forward_operations": ["subtract-origin", "multiply-by-scale"],
            "inverse_operations": ["divide-by-scale", "add-origin"],
            "meaning": "display-only; not calibration or physical units",
        }

    def apply(self, positions: np.ndarray) -> TransformedRows:
        check_arithmetic()
        _ = _shape(positions, 3)
        if (
            positions.dtype.kind != "f"
            or positions.dtype.itemsize != 4
            or not np.all(np.isfinite(positions))
        ):
            raise MeshImportError(
                "structure", "display-transform", "positions must be finite binary32"
            )
        source = positions.astype("<f8")
        origin = np.array(
            [bits_float(value) for value in self.origin_bits], dtype="<f8"
        )
        scale = np.float64(math.ldexp(1.0, self.scale_power))
        try:
            with np.errstate(
                under="ignore", over="raise", invalid="raise", divide="raise"
            ):
                coordinates = np.subtract(source, origin)
                _ = np.multiply(coordinates, scale, out=coordinates)
                inverse = np.divide(coordinates, scale)
                _ = np.add(inverse, origin, out=inverse)
        except FloatingPointError as error:
            raise MeshImportError(
                "numeric-profile-failure",
                "display-transform",
                "nonfinite transform intermediate",
            ) from error
        lost = int(np.count_nonzero(np.any(inverse != source, axis=1)))
        coordinates.flags.writeable = False
        return TransformedRows(coordinates, lost)
