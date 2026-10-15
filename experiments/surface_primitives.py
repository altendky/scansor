"""Pure NumPy fit-chart adapters shared by physical intent and CAD geometry."""

from __future__ import annotations

from numbers import Real
from typing import Any, cast

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


def projected_axial_parameter(
    axial: Array, radial_distance: Array, radius: float, slope: float
) -> Array:
    """Orthogonal meridian foot without losing a tiny near-cylinder correction.

    Dividing a long axial coordinate twice can introduce whole axial ULPs.
    Conversely, subtracting that coordinate from itself loses steep-cone feet;
    use the scaled rational expression in that regime.
    """
    scale = float(np.hypot(1, slope))
    if abs(slope) <= 1:
        residual = (radial_distance - radius - slope * axial) / scale
        return axial + (slope / scale) * residual
    return (axial / scale + (slope / scale) * (radial_distance - radius)) / scale


def _geometry_array(value: object, name: str) -> Array:
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf" or (
        isinstance(value, (list, tuple))
        and any(isinstance(item, (bool, np.bool_)) for item in value)
    ):
        raise ValueError(f"{name} must contain finite real numbers")
    return np.asarray(value, dtype=float)


def _revolution_record(
    value: dict[str, Any], *, require_support: bool
) -> dict[str, Any]:
    kind = value.get("kind")
    if kind not in ("cylinder", "cone"):
        raise ValueError("revolution geometry requires a cylinder or cone kind")
    if not {"origin", "axis", "radius", "slope"}.issubset(value):
        raise ValueError("revolution geometry requires origin, axis, radius and slope")
    origin = vector(_geometry_array(value["origin"], "origin"), "revolution origin")
    direction = vector(_geometry_array(value["axis"], "axis"), "revolution axis")
    length = float(np.linalg.norm(direction))
    if not np.isfinite(length) or not np.isclose(length, 1, rtol=0, atol=1e-10):
        raise ValueError("canonical revolution axis must be a unit vector")
    direction = direction / length
    if any(
        not isinstance(value[key], Real) or isinstance(value[key], (bool, np.bool_))
        for key in ("radius", "slope")
    ):
        raise ValueError("revolution radius and taper must be finite real numbers")
    radius, slope = float(value["radius"]), float(value["slope"])
    if not np.isfinite([radius, slope]).all():
        raise ValueError("revolution radius and taper must be finite")
    if kind == "cylinder" and (radius <= 0 or slope != 0):
        raise ValueError(
            "cylinder geometry requires a positive finite radius and zero taper"
        )
    support = value.get("axial_domain")
    domain = None
    if support is None:
        if require_support:
            raise ValueError("canonical revolution geometry requires finite support")
    else:
        values = _geometry_array(support, "revolution support")
        if (
            values.shape != (2,)
            or not np.isfinite(values).all()
            or values[0] >= values[1]
        ):
            raise ValueError("revolution support must contain finite ordered bounds")
        with np.errstate(over="ignore", invalid="ignore"):
            radii = radius + slope * values
        if not np.isfinite(radii).all() or np.any(radii <= 0):
            raise ValueError("revolution support must have positive finite radii")
        domain = values.tolist()
    return {
        "kind": kind,
        "origin": origin.tolist(),
        "axis": direction.tolist(),
        "radius": radius,
        "slope": slope,
        "axial_domain": domain,
    }


def revolution_geometry(surface: dict[str, Any]) -> dict[str, Any]:
    """Read authoritative analytic geometry or explicitly adapt the legacy chart.

    Canonical support is relative to its own anchor. Legacy callers sometimes
    describe an unbounded surface without support; retain that absence as None.
    An explicit malformed analytic record never falls back to legacy parameters.
    """
    kind = surface.get("kind")
    if kind not in ("cylinder", "cone"):
        raise ValueError("revolution geometry requires a cylinder or cone kind")
    if "analytic" in surface:
        raw_analytic = surface["analytic"]
        if not isinstance(raw_analytic, dict):
            raise ValueError("canonical revolution kind must match the fitted surface")
        analytic = cast(dict[str, Any], raw_analytic)
        if analytic.get("kind") != kind:
            raise ValueError("canonical revolution kind must match the fitted surface")
        return _revolution_record(analytic, require_support=True)
    p = _geometry_array(surface.get("parameters", []), "legacy revolution parameters")
    if p.shape != (7,) or not np.isfinite(p).all():
        raise ValueError("revolution parameters must contain seven finite values")
    return _revolution_record(
        {
            "kind": kind,
            "origin": [float(p[0]), float(p[1]), 0.0],
            "axis": unit([p[2], p[3], 1.0], "revolution axis").tolist(),
            "radius": float(p[4]),
            "slope": float(p[6]),
            "axial_domain": surface.get("axial_domain"),
        },
        require_support=False,
    )


def rebase_revolution(geometry: dict[str, Any], offset: float) -> dict[str, Any]:
    """Move an axis anchor while preserving physical radius and support endpoints."""
    result = _revolution_record(geometry, require_support=False)
    if not np.isfinite(offset):
        raise ValueError("revolution anchor offset must be finite")
    result["origin"] = (
        np.asarray(result["origin"]) + offset * np.asarray(result["axis"])
    ).tolist()
    result["radius"] += result["slope"] * offset
    if result["axial_domain"] is not None:
        result["axial_domain"] = (np.asarray(result["axial_domain"]) - offset).tolist()
    return _revolution_record(result, require_support=False)


def reverse_revolution(geometry: dict[str, Any]) -> dict[str, Any]:
    """Reverse the surface axis without changing its positive-radius finite sheet."""
    result = _revolution_record(geometry, require_support=False)
    result["axis"] = (-np.asarray(result["axis"])).tolist()
    result["slope"] = -result["slope"]
    if result["axial_domain"] is not None:
        lo, hi = result["axial_domain"]
        result["axial_domain"] = [-hi, -lo]
    return result


def transform_revolution(
    geometry: dict[str, Any], rotation: Array, translation: Array
) -> dict[str, Any]:
    """Rigidly transform geometry using the reuse row-vector convention X @ R + t."""
    result = _revolution_record(geometry, require_support=False)
    rotation = np.asarray(rotation, dtype=float)
    translation = vector(translation, "revolution translation")
    if (
        rotation.shape != (3, 3)
        or not np.isfinite(rotation).all()
        or not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0, atol=1e-10)
        or not np.isclose(np.linalg.det(rotation), 1, rtol=0, atol=1e-10)
    ):
        raise ValueError("revolution transform requires a proper rigid rotation")
    result["origin"] = (np.asarray(result["origin"]) @ rotation + translation).tolist()
    result["axis"] = (np.asarray(result["axis"]) @ rotation).tolist()
    return _revolution_record(result, require_support=False)


def primitive(surface: dict[str, Any]) -> dict[str, Any]:
    """Adapt existing fit charts without modifying or filtering observations."""
    kind = surface.get("kind", "plane" if "plane_equation" in surface else None)
    if kind == "plane":
        p = np.asarray(surface.get("parameters", []), dtype=float)
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
    geometry = revolution_geometry(surface)
    # Analytic extents use unbounded surfaces; finite fitting support remains a
    # separate authority and must not silently trim their physical boundaries.
    return {key: geometry[key] for key in ("kind", "origin", "axis", "radius", "slope")}
