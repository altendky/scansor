"""Explicit physical face boundaries, separate from observation/solver support.

This bounded experiment handles circular plane/revolution intersections and
concentric planar or axial side regions. It does not infer adjacency, sew faces,
or construct solids. Preview tessellation is display-only, never fit evidence.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.surface_primitives import basis as basis
from experiments.surface_primitives import primitive as primitive
from experiments.surface_primitives import unit as unit
from experiments.surface_primitives import vector as vector

Array = NDArray[np.float64]
ANGULAR_TOLERANCE = 1e-10
PREVIEW_SEGMENTS = 96


def circle_intersection(
    first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    from experiments.ocp_geometry import circular_intersection

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
    radius = float(side["radius"] + side["slope"] * t)
    if (
        not np.isfinite(origin + t * axis).all()
        or not np.isfinite(radius)
        or radius <= 0
    ):
        raise ValueError(
            "intersection has no positive finite circular radius (missing intersection or cone apex)"
        )
    return circular_intersection(plane, side, PREVIEW_SEGMENTS)


def distance_tolerance(points: list[Array], scale: float) -> float:
    # Account for representation precision at large translations, not just size.
    magnitude = max(float(np.max(np.abs(p))) for p in points)
    return 1e-10 * scale + 32 * np.finfo(float).eps * magnitude


def face_preview(
    geometry: dict[str, Any], interval: tuple[float, float], planar: bool
) -> dict[str, Any]:
    from experiments.ocp_geometry import face_from_record, tessellate_face

    if planar:
        kind, scale = "plane", interval[1]
    else:
        kind = "cone" if geometry["slope"] else "cylinder"
        scale = max(
            geometry["radius"] + geometry["slope"] * value for value in interval
        )
    # This finite record exists only for constructing the display mesh. It never
    # replaces the caller's physical record, whose missing endpoints remain null.
    preview_face = face_from_record(
        {
            "bounded": True,
            "surface_kind": kind,
            "geometry": geometry,
            "bounds": {"radial" if planar else "axial": list(interval)},
        }
    )
    return tessellate_face(preview_face, scale)


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
        if np.any(radii <= tolerance) or not np.isfinite(radii).all():
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
