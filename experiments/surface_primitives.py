"""Pure NumPy fit-chart adapters shared by physical intent and CAD geometry."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def vector(value: object, name: str) -> Array:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{name} must contain three finite coordinates")
    return result


def unit(value: object, name: str) -> Array:
    result = vector(value, name)
    length = float(np.linalg.norm(result))
    if not np.isfinite(length) or length <= 0:
        raise ValueError(f"{name} must be nonzero and normalizable")
    return result / length


def basis(axis: Array) -> tuple[Array, Array]:
    reference = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = unit(np.cross(reference, axis), "surface basis")
    return u, np.cross(axis, u)


def primitive(surface: dict[str, Any]) -> dict[str, Any]:
    """Adapt existing fit charts without modifying or filtering observations."""
    kind = surface.get("kind", "plane" if "plane_equation" in surface else None)
    p = np.asarray(surface.get("parameters", []), dtype=float)
    if kind == "plane":
        equation = np.asarray(surface.get("plane_equation", []), dtype=float)
        if equation.size == 0:
            if p.shape == (7,):
                equation = np.r_[unit([p[2], p[3], 1.0], "plane normal"), p[5]]
            else:
                equation = p
        if equation.shape != (4,) or not np.isfinite(equation).all():
            raise ValueError("plane equation must contain four finite values")
        length = float(np.linalg.norm(equation[:3]))
        normal = unit(equation[:3], "plane normal")
        return {
            "kind": kind,
            "axis": normal.tolist(),
            "offset": float(equation[3] / length),
        }
    if kind not in ("cylinder", "cone"):
        raise ValueError(
            "circular extents currently require a plane and cylinder or cone"
        )
    if p.shape != (7,) or not np.isfinite(p).all():
        raise ValueError("revolution parameters must contain seven finite values")
    if kind == "cylinder" and p[6] != 0:
        raise ValueError("cylinder parameters must have zero taper")
    axis = unit([p[2], p[3], 1.0], "revolution axis")
    return {
        "kind": kind,
        "origin": [float(p[0]), float(p[1]), 0.0],
        "axis": axis.tolist(),
        "radius": float(p[4]),
        "slope": float(p[6]),
    }
