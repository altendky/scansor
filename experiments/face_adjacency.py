"""Conservative primitive-pair rejection, not physical adjacency inference.

Only exact parallel/coaxial relationships justify global empty-intersection
proofs. Observation bounds and solver support never enter these decisions.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


def _vector(value: object, label: str) -> Array:
    result = np.asarray(value, dtype=float)
    if result.shape != (3,) or not np.isfinite(result).all():
        raise ValueError(f"{label} must be a finite three-vector")
    return result


def _axis(value: object) -> Array:
    result = _vector(value, "primitive axis")
    if not np.isclose(np.linalg.norm(result), 1, rtol=0, atol=8 * np.finfo(float).eps):
        raise ValueError("primitive axis must be unit length")
    return result


def _number(value: object, label: str) -> float:
    result = float(cast(Any, value))
    if not np.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _exact(value: Array) -> list[Fraction]:
    return [Fraction.from_float(float(component)) for component in value]


def _parallel(first: list[Fraction], second: list[Fraction]) -> bool:
    return all(
        first[i] * second[j] == first[j] * second[i]
        for i, j in ((0, 1), (0, 2), (1, 2))
    )


def _perpendicular(first: Array, second: Array) -> bool:
    return sum(a * b for a, b in zip(_exact(first), _exact(second), strict=True)) == 0


def _tolerance(scale: float, *positions: Array) -> float:
    magnitude = max([abs(scale), *(float(np.max(np.abs(p))) for p in positions)])
    return 1e-10 * abs(scale) + 64 * np.finfo(float).eps * magnitude


def _result(status: str, category: str, reason: str) -> dict[str, str]:
    return {"status": status, "category": category, "reason": reason}


def classify_pair(first: dict[str, Any], second: dict[str, Any]) -> dict[str, str]:
    """Classify existing primitive records without constructing general curves.

    candidate means a mathematical intersection, NEVER confirmed adjacency.
    uncertain includes unsupported geometry, coincidence, and near tangency.
    proven_empty is reserved for globally disjoint positive-radius primitives.
    """
    kinds = [first.get("kind"), second.get("kind")]
    if any(kind not in ("plane", "cylinder", "cone") for kind in kinds):
        return _result(
            "uncertain", "unsupported_primitive", "Unsupported primitive kind."
        )
    axes = [_axis(item["axis"]) for item in (first, second)]
    parallel = _parallel(_exact(axes[0]), _exact(axes[1]))
    if kinds == ["plane", "plane"]:
        offsets = [_number(item["offset"], "plane offset") for item in (first, second)]
        if not parallel:
            return _result(
                "candidate", "plane_line", "Nonparallel planes intersect in a line."
            )
        sign = 1 if float(axes[0] @ axes[1]) > 0 else -1
        gap = abs(offsets[0] - sign * offsets[1])
        tolerance = _tolerance(max(abs(value) for value in offsets))
        if gap > tolerance:
            return _result(
                "proven_empty",
                "parallel_planes",
                "Exactly parallel planes have distinct offsets.",
            )
        return _result(
            "uncertain",
            "coincident_or_near_planes",
            "Coincident or numerically indistinguishable planes do not define a unique edge.",
        )
    if "plane" in kinds:
        plane, side = (first, second) if kinds[0] == "plane" else (second, first)
        normal, axis = _axis(plane["axis"]), _axis(side["axis"])
        origin = _vector(side["origin"], "revolution origin")
        radius = _number(side["radius"], "revolution radius")
        slope = _number(side.get("slope", 0), "revolution slope")
        offset = _number(plane["offset"], "plane offset")
        if side["kind"] == "cylinder" and (radius <= 0 or slope != 0):
            raise ValueError("cylinder requires positive radius and zero taper")
        if slope == 0 and radius <= 0:
            return _result(
                "proven_empty",
                "empty_positive_sheet",
                "Zero-taper cone has no positive-radius sheet.",
            )
        tolerance = _tolerance(radius, origin, normal * offset)
        if _parallel(_exact(normal), _exact(axis)):
            axial = (offset - float(normal @ origin)) / float(normal @ axis)
            cut_radius = radius + slope * axial
            if not np.isfinite([axial, cut_radius]).all():
                return _result(
                    "uncertain",
                    "unrepresentable_cut",
                    "Intersection coordinates cannot be represented reliably.",
                )
            tolerance = tolerance * (1 + abs(slope)) + _tolerance(
                max(radius, abs(slope * axial)), origin
            )
            if cut_radius < -tolerance:
                return _result(
                    "proven_empty",
                    "outside_positive_cone",
                    "Perpendicular cut lies outside the cone's positive-radius sheet.",
                )
            if cut_radius <= tolerance:
                return _result(
                    "uncertain", "cone_apex", "Cut meets or approaches the cone apex."
                )
            return _result(
                "candidate",
                "circle",
                "Perpendicular plane cuts a positive-radius circle.",
            )
        if slope == 0 and _perpendicular(normal, axis):
            distance = abs(offset - float(normal @ origin))
            if not np.isfinite(distance):
                return _result(
                    "uncertain",
                    "unrepresentable_distance",
                    "Plane/axis distance cannot be represented reliably.",
                )
            if distance > radius + tolerance:
                return _result(
                    "proven_empty",
                    "plane_outside_cylinder",
                    "Exactly axis-parallel plane lies outside the cylinder.",
                )
            if abs(distance - radius) <= tolerance:
                return _result(
                    "uncertain",
                    "tangent_generators",
                    "Plane is tangent or nearly tangent to the cylinder.",
                )
            return _result(
                "candidate",
                "generator_lines",
                "Axis-parallel secant plane cuts two generator lines.",
            )
        if slope == 0:
            return _result(
                "candidate",
                "ellipse",
                "Oblique plane intersects the infinite cylinder; finite-face adjacency is unknown.",
            )
        return _result(
            "uncertain",
            "general_conic",
            "General cone/plane intersection requires conic and branch classification.",
        )
    origins = [_vector(item["origin"], "revolution origin") for item in (first, second)]
    radii = [_number(item["radius"], "revolution radius") for item in (first, second)]
    slopes = [
        _number(item.get("slope", 0), "revolution slope") for item in (first, second)
    ]
    if any(
        kind == "cylinder" and (radius <= 0 or slope != 0)
        for kind, radius, slope in zip(kinds, radii, slopes, strict=True)
    ):
        raise ValueError("cylinder requires positive radius and zero taper")
    if any(
        radius <= 0 and slope == 0 for radius, slope in zip(radii, slopes, strict=True)
    ):
        return _result(
            "proven_empty",
            "empty_positive_sheet",
            "Zero-taper cone has no positive-radius sheet.",
        )
    if not parallel:
        return _result(
            "uncertain",
            "general_quadric_pair",
            "Nonparallel revolution surfaces require general intersection branches.",
        )
    sign = 1 if float(axes[0] @ axes[1]) > 0 else -1
    delta = origins[0] - origins[1]
    axial = float(delta @ axes[1])
    distance = float(np.linalg.norm(delta - axial * axes[1]))
    tolerance = _tolerance(max(abs(radius) for radius in radii), *origins)
    exact_delta = [
        a - b for a, b in zip(_exact(origins[0]), _exact(origins[1]), strict=True)
    ]
    coaxial = _parallel(exact_delta, _exact(axes[1]))
    if slopes == [0, 0]:
        if not np.isfinite(distance):
            return _result(
                "uncertain",
                "unrepresentable_distance",
                "Axis separation cannot be represented reliably.",
            )
        outer, inner = sum(radii), abs(radii[0] - radii[1])
        if distance > outer + tolerance or distance < inner - tolerance:
            return _result(
                "proven_empty",
                "disjoint_parallel_cylinders",
                "Exactly parallel cylinders are externally disjoint or strictly nested.",
            )
        if coaxial and abs(radii[0] - radii[1]) <= tolerance:
            return _result(
                "uncertain",
                "coincident_cylinders",
                "Coaxial equal-radius surfaces do not define a unique edge.",
            )
        if min(abs(distance - outer), abs(distance - inner)) <= tolerance:
            return _result(
                "uncertain",
                "tangent_cylinders",
                "Parallel cylinders are tangent or nearly tangent.",
            )
        return _result(
            "candidate",
            "generator_lines",
            "Parallel cylinders meet along generator lines.",
        )
    if not coaxial:
        return _result(
            "uncertain",
            "general_parallel_quadric_pair",
            "Noncoaxial tapered surfaces require general intersection branches.",
        )
    second_radius = radii[1] + slopes[1] * axial
    second_slope = sign * slopes[1]
    tolerance *= 1 + max(abs(slope) for slope in slopes)
    if not np.isfinite(second_radius):
        return _result(
            "uncertain",
            "unrepresentable_profile",
            "Recharted profile cannot be represented reliably.",
        )
    if slopes[0] == second_slope:
        if abs(radii[0] - second_radius) > tolerance:
            return _result(
                "proven_empty",
                "distinct_coaxial_profiles",
                "Coaxial equal-slope positive-radius sheets have distinct radii everywhere.",
            )
        return _result(
            "uncertain",
            "coincident_profiles",
            "Coaxial profiles coincide or cannot be distinguished numerically.",
        )
    difference = slopes[0] - second_slope
    if abs(difference) < 1e-12:
        return _result(
            "uncertain",
            "near_parallel_profiles",
            "Nearly equal slopes make the crossing ill-conditioned.",
        )
    crossing = (second_radius - radii[0]) / difference
    crossing_radius = radii[0] + slopes[0] * crossing
    if not np.isfinite([crossing, crossing_radius]).all():
        return _result(
            "uncertain",
            "unrepresentable_crossing",
            "Profile crossing cannot be represented reliably.",
        )
    # Rechart/difference roundoff is amplified by division by the slope gap.
    # Carry that uncertainty into radius classification near an apex.
    crossing_tolerance = tolerance / abs(difference) + _tolerance(abs(crossing))
    tolerance += abs(slopes[0]) * crossing_tolerance + _tolerance(
        max(
            abs(crossing_radius),
            abs(slopes[0] * crossing),
            *(abs(radius) for radius in radii),
        ),
        *origins,
    )
    if crossing_radius < -tolerance:
        return _result(
            "proven_empty",
            "outside_positive_sheets",
            "The sole coaxial profile crossing has negative radius.",
        )
    if crossing_radius <= tolerance:
        return _result(
            "uncertain",
            "apex_crossing",
            "Profile crossing meets or approaches an apex.",
        )
    return _result(
        "candidate", "circle", "Coaxial profiles cross at a positive-radius circle."
    )


def circle_in_face_domains(
    edge: dict[str, Any], domains: list[dict[str, Any]]
) -> bool | None:
    """Does a circle meet the UNION of explicitly scoped physical face domains?

    Caller verifies every domain's exact owning surface context. False proves
    exclusion from every supplied domain; an empty/unsupported union is unknown.
    Unbounded endpoints remain open. No observation or fit-support bounds apply.
    Lateral slab exclusion bounds the whole circle, without assuming parallel
    axes. Nonparallel membership and near-boundary exclusions remain unknown.
    """
    if edge.get("kind") != "circle" or not domains:
        return None
    center = _vector(edge["center_display"], "circle center")
    normal = _axis(edge["axis_display"])
    radius = _number(edge["radius"], "circle radius")
    if radius <= 0:
        raise ValueError("circle radius must be positive")
    unknown = False
    for face in domains:
        kind = face.get("surface_kind")
        geometry = face.get("geometry", {})
        if kind not in ("plane", "cylinder", "cone"):
            unknown = True
            continue
        origin = _vector(geometry["origin"], "face origin")
        axis = _axis(geometry["axis"])
        tolerance = _tolerance(radius, center, origin)
        parallel = _parallel(_exact(normal), _exact(axis))
        delta = center - origin
        axial = float(delta @ axis)
        if kind == "plane":
            if not parallel or abs(axial) > tolerance:
                unknown = True
                continue
            distance = float(np.linalg.norm(delta - axial * axis))
            lower_value, upper_value = abs(distance - radius), distance + radius
            interval = face.get("bounds", {}).get("radial")
        else:
            interval = face.get("bounds", {}).get("axial")
            # Every point of this finite circle lies in this axial envelope.
            # A tiny axis mismatch must not erase a well-separated exclusion,
            # nor may snapping it to zero hide a tilted circle reaching a face.
            half_span = radius * float(np.linalg.norm(np.cross(normal, axis)))
            lower_value, upper_value = axial - half_span, axial + half_span
            distance = float(np.linalg.norm(delta - axial * axis))
            expected = (
                _number(geometry["radius"], "face radius")
                + _number(geometry.get("slope", 0), "face slope") * axial
            )
            if distance > tolerance or abs(expected - radius) > tolerance:
                unknown = True
                continue
        if (
            not np.isfinite([lower_value, upper_value]).all()
            or not isinstance(interval, (list, tuple))
            or len(interval) != 2
        ):
            unknown = True
            continue
        lower = (
            None
            if interval[0] is None
            else _number(interval[0], "lower physical bound")
        )
        upper = (
            None
            if interval[1] is None
            else _number(interval[1], "upper physical bound")
        )
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("physical bounds must be ordered")
        if (lower is not None and upper_value < lower - tolerance) or (
            upper is not None and lower_value > upper + tolerance
        ):
            continue
        if not parallel:
            unknown = True
            continue
        if (lower is None or upper_value >= lower) and (
            upper is None or lower_value <= upper
        ):
            return True
        unknown = True
    return None if unknown else False
