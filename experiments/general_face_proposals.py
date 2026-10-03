"""Primitive projection for physical-face evidence; no observation filtering."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def project_observations(
    geometry: dict[str, Any], points: Array
) -> tuple[Array, NDArray[np.bool_]]:
    axis = np.asarray(geometry["axis"])
    if geometry["kind"] == "plane":
        with np.errstate(over="ignore", invalid="ignore"):
            projected = points - (points @ axis - geometry["offset"])[:, None] * axis
        return projected, np.isfinite(projected).all(axis=1)
    origin = np.asarray(geometry["origin"])
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        relative = points - origin
        axial = relative @ axis
        radial_vectors = relative - axial[:, None] * axis
        radial = np.linalg.norm(radial_vectors, axis=1)
        slope = geometry["slope"]
        projected_axial = (axial + slope * (radial - geometry["radius"])) / (
            1 + slope * slope
        )
        radius = geometry["radius"] + slope * projected_axial
    defined = (
        (radial > 0) & (radius > 0) & np.isfinite(projected_axial) & np.isfinite(radius)
    )
    projected = np.zeros_like(points)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        projected[defined] = (
            origin
            + projected_axial[defined, None] * axis
            + radial_vectors[defined] * (radius[defined] / radial[defined])[:, None]
        )
    defined &= np.isfinite(projected).all(axis=1)
    return projected, defined
