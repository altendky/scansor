"""Explicit physical face boundaries, separate from observation/solver support.

This bounded experiment handles circular plane/revolution intersections and
concentric planar or axial side regions. It does not infer adjacency, sew faces,
or construct solids. Preview tessellation is display-only, never fit evidence.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]
ANGULAR_TOLERANCE = 1e-10
PREVIEW_SEGMENTS = 96


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
    """Adapt existing fit charts without modifying or filtering any observations."""
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


def circle_intersection(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    geometries = [primitive(first), primitive(second)]
    planes = [g for g in geometries if g["kind"] == "plane"]
    sides = [g for g in geometries if g["kind"] in ("cylinder", "cone")]
    if len(planes) != 1 or len(sides) != 1:
        raise ValueError(
            "circular extents currently require a plane and cylinder or cone"
        )
    plane, side = planes[0], sides[0]
    normal, axis = np.asarray(plane["axis"]), np.asarray(side["axis"])
    if np.linalg.norm(np.cross(normal, axis)) > ANGULAR_TOLERANCE:
        raise ValueError(
            "oblique intersections are not supported; declare a perpendicular relationship for a circular edge"
        )
    origin = np.asarray(side["origin"])
    t = (plane["offset"] - normal @ origin) / float(normal @ axis)
    center = origin + t * axis
    radius = float(side["radius"] + side["slope"] * t)
    if not np.isfinite(center).all() or not np.isfinite(radius) or radius <= 0:
        raise ValueError(
            "intersection has no positive finite circular radius (missing intersection or cone apex)"
        )
    u, v = basis(normal)
    angles = np.arange(PREVIEW_SEGMENTS) * (2 * np.pi / PREVIEW_SEGMENTS)
    points = center + radius * (
        np.cos(angles)[:, None] * u + np.sin(angles)[:, None] * v
    )
    if not np.isfinite(points).all():
        raise ValueError("intersection preview coordinates overflow")
    return {
        "kind": "circle",
        "center_display": center.tolist(),
        "axis_display": normal.tolist(),
        "basis_u_display": u.tolist(),
        "basis_v_display": v.tolist(),
        "radius": radius,
        "preview": {"positions": points.ravel().tolist(), "indices": []},
    }


def distance_tolerance(points: list[Array], scale: float) -> float:
    # Account for representation precision at large translations, not just size.
    magnitude = max(float(np.max(np.abs(p))) for p in points)
    return 1e-10 * scale + 32 * np.finfo(float).eps * magnitude


def face_preview(
    geometry: dict[str, Any], interval: tuple[float, float], planar: bool
) -> dict[str, Any]:
    origin, axis = np.asarray(geometry["origin"]), np.asarray(geometry["axis"])
    u, v = np.asarray(geometry["basis_u"]), np.asarray(geometry["basis_v"])
    angles = np.arange(PREVIEW_SEGMENTS) * (2 * np.pi / PREVIEW_SEGMENTS)
    radial = np.cos(angles)[:, None] * u + np.sin(angles)[:, None] * v
    rings = [
        origin + value * radial
        if planar
        else origin
        + value * axis
        + (geometry["radius"] + geometry["slope"] * value) * radial
        for value in interval
    ]
    triangles: list[list[int]] = []
    if planar and interval[0] == 0:
        points = np.vstack([origin, rings[1]])
        triangles = [
            [0, i + 1, (i + 1) % PREVIEW_SEGMENTS + 1] for i in range(PREVIEW_SEGMENTS)
        ]
    else:
        points = np.vstack(rings)
        for i in range(PREVIEW_SEGMENTS):
            j = (i + 1) % PREVIEW_SEGMENTS
            triangles.extend(
                [
                    [i, j, j + PREVIEW_SEGMENTS],
                    [i, j + PREVIEW_SEGMENTS, i + PREVIEW_SEGMENTS],
                ]
            )
        if planar:
            triangles = [triangle[::-1] for triangle in triangles]
    if not np.isfinite(points).all():
        raise ValueError("face preview coordinates overflow")
    return {
        "positions": points.ravel().tolist(),
        "indices": np.asarray(triangles).ravel().tolist(),
    }


def trimmed_face(
    surface: dict[str, Any],
    boundaries: list[tuple[dict[str, Any], dict[str, Any]]],
    observations: Array,
) -> dict[str, Any]:
    """Intersect declared retained regions; incomplete regions remain unbounded.

    Observation coverage is used ONLY to crop an otherwise infinite preview.
    Missing physical endpoints remain null and cannot be exported as a face.
    """
    geometry = primitive(surface)
    planar = geometry["kind"] == "plane"
    lower: float | None = 0.0 if planar else None
    upper: float | None = None
    centers = [vector(edge["center_display"], "edge center") for _, edge in boundaries]
    scale = max(float(edge["radius"]) for _, edge in boundaries)
    tolerance = distance_tolerance(centers, scale)
    axis = np.asarray(geometry["axis"])
    origin = centers[0] if planar else np.asarray(geometry["origin"])
    for (use, edge), center in zip(boundaries, centers, strict=True):
        normal = unit(edge["axis_display"], "edge normal")
        radius = float(edge["radius"])
        if not np.isfinite(radius) or radius <= 0:
            raise ValueError("boundary circle radius must be positive and finite")
        if np.linalg.norm(np.cross(normal, axis)) > ANGULAR_TOLERANCE:
            raise ValueError("boundary is not perpendicular to the face axis")
        keep = use["keep"]
        if planar:
            if keep not in ("inside", "outside"):
                raise ValueError(
                    "plane faces require inside/outside circular boundary choices"
                )
            if np.linalg.norm(center - origin) > tolerance:
                raise ValueError("nonconcentric planar boundaries are not supported")
            if abs(axis @ center - geometry["offset"]) > tolerance:
                raise ValueError("boundary does not lie on the face plane")
            value = radius
            retains_lower = keep == "outside"
        else:
            if keep not in ("positive", "negative"):
                raise ValueError(
                    "cylinder/cone faces require positive/negative cutting-plane choices"
                )
            value = float((center - origin) @ axis)
            if np.linalg.norm(center - origin - value * axis) > tolerance:
                raise ValueError("boundary center does not lie on the face axis")
            expected = geometry["radius"] + geometry["slope"] * value
            if abs(radius - expected) > tolerance:
                raise ValueError("boundary radius does not match the face")
            retains_lower = (keep == "positive") == (normal @ axis > 0)
        if retains_lower:
            lower = value if lower is None else max(lower, value)
        else:
            upper = value if upper is None else min(upper, value)
    if lower is not None and upper is not None:
        span = upper - lower
        if not np.isfinite(span):
            raise ValueError("face interval span must be finite")
        if span <= tolerance:
            raise ValueError("boundary choices leave an empty or degenerate face")
    bounded = lower is not None and upper is not None
    u, v = basis(axis)
    geometry.update(origin=origin.tolist(), basis_u=u.tolist(), basis_v=v.tolist())
    observations = np.asarray(observations, dtype=float)
    if (
        observations.ndim != 2
        or observations.shape[1] != 3
        or not np.isfinite(observations).all()
    ):
        raise ValueError("preview observations must be finite XYZ coordinates")
    if planar:
        preview_lower = float(lower or 0)
        distances = np.linalg.norm(
            (observations - origin) @ np.column_stack([u, v]), axis=1
        )
        preview_upper = (
            float(upper)
            if upper is not None
            else max(
                float(np.max(distances)) * 1.05 if len(distances) else 0,
                preview_lower + scale,
            )
        )
    else:
        axial = (observations - origin) @ axis
        cuts = [float((center - origin) @ axis) for center in centers]
        span = max(float(np.ptp(axial)) if len(axial) else 0.0, scale)
        preview_lower = (
            float(lower)
            if lower is not None
            else min(
                float(np.min(axial)) if len(axial) else min(cuts),
                (upper if upper is not None else min(cuts)) - span,
            )
        )
        preview_upper = (
            float(upper)
            if upper is not None
            else max(
                float(np.max(axial)) if len(axial) else max(cuts), preview_lower + span
            )
        )
        radii = geometry["radius"] + geometry["slope"] * np.array(
            [preview_lower, preview_upper]
        )
        if np.any(radii <= 0) or not np.isfinite(radii).all():
            raise ValueError(
                "face or preview crosses the cone apex; add a positive-radius boundary"
            )
    if not np.isfinite(
        [preview_lower, preview_upper, preview_upper - preview_lower]
    ).all():
        raise ValueError("face preview interval span must be finite")
    return {
        "kind": "trimmed_face",
        "surface_kind": geometry.pop("kind"),
        "geometry": geometry,
        "bounds": {"radial" if planar else "axial": [lower, upper]},
        "bounded": bounded,
        "boundary_ids": [use["intersection"] for use, _ in boundaries],
        "boundary_uses": [use.copy() for use, _ in boundaries],
        "preview_clipped": not bounded,
        "preview": face_preview(geometry, (preview_lower, preview_upper), planar),
    }
