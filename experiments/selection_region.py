"""Surface-following selection volumes expressed in explicit datum frames."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def datum_frame(
    axis: dict[str, Any],
    axial_plane: dict[str, Any],
    clock_plane: dict[str, Any],
) -> tuple[FloatArray, FloatArray]:
    """Return an origin and right-handed local-to-workspace rotation."""
    direction = np.asarray(axis["axis_display"], dtype=float)
    direction /= np.linalg.norm(direction)
    axis_point = np.asarray(axis["point_display"], dtype=float)

    axial_equation = np.asarray(axial_plane["plane_equation"], dtype=float)
    axial_normal = axial_equation[:3]
    axial_normal /= np.linalg.norm(axial_normal)
    axial_dot = float(axial_normal @ direction)
    if abs(axial_dot) < 0.9:
        raise ValueError("the axial datum must be perpendicular to the frame axis")
    origin = (
        axis_point
        + ((float(axial_equation[3]) - float(axial_normal @ axis_point)) / axial_dot)
        * direction
    )

    clock_normal = np.asarray(clock_plane["plane_equation"][:3], dtype=float)
    clock_normal /= np.linalg.norm(clock_normal)
    radial = clock_normal - float(clock_normal @ direction) * direction
    radial_length = float(np.linalg.norm(radial))
    if radial_length < 0.9:
        raise ValueError("the clock datum must be parallel to the frame axis")
    radial /= radial_length
    tangential = np.cross(direction, radial)
    tangential /= np.linalg.norm(tangential)
    rotation = np.column_stack((radial, tangential, direction))
    return origin, rotation


def _angular_interval(values: FloatArray, padding: float) -> tuple[float, float]:
    wrapped = np.sort(np.mod(values, 2.0 * np.pi))
    if len(wrapped) == 1:
        start = float(wrapped[0])
        span = 0.0
    else:
        gaps = np.diff(np.concatenate((wrapped, wrapped[:1] + 2.0 * np.pi)))
        largest = int(np.argmax(gaps))
        start = float(wrapped[(largest + 1) % len(wrapped)])
        span = float(2.0 * np.pi - gaps[largest])
    if span + 2.0 * padding >= 2.0 * np.pi:
        return 0.0, float(2.0 * np.pi)
    return float((start - padding) % (2.0 * np.pi)), span + 2.0 * padding


def _plane_basis(normal: FloatArray) -> tuple[FloatArray, FloatArray]:
    candidate = np.eye(3)[int(np.argmin(np.abs(normal)))]
    first = candidate - float(candidate @ normal) * normal
    first /= np.linalg.norm(first)
    second = np.cross(normal, first)
    second /= np.linalg.norm(second)
    return (
        np.asarray(first, dtype=np.float64),
        np.asarray(second, dtype=np.float64),
    )


def build_selection_region(
    points: FloatArray,
    normals: FloatArray,
    fit: dict[str, Any],
    origin: FloatArray,
    rotation: FloatArray,
    *,
    tangent_margin: float,
    normal_margin: float,
    normal_angle_degrees: float,
) -> dict[str, Any]:
    """Lift selected observations into a reusable surface-relative volume."""
    if len(points) < 3:
        raise ValueError("a reusable region requires at least three selected vertices")
    if tangent_margin < 0.0 or normal_margin < 0.0:
        raise ValueError("selection-region margins cannot be negative")
    if not 0.0 < normal_angle_degrees <= 90.0:
        raise ValueError("selection-region normal angle must be in (0, 90]")
    local = (np.asarray(points, dtype=float) - origin) @ rotation
    local_normals = np.asarray(normals, dtype=float) @ rotation
    lengths = np.linalg.norm(local_normals, axis=1)
    if np.any(lengths <= 0.0):
        raise ValueError("selection-region source normals must be nonzero")
    local_normals /= lengths[:, None]
    kind = str(fit["kind"])
    common = {
        "format": "scansor-fitted-selection-region-v1",
        "kind": kind,
        "selected_count": len(points),
        "tangent_margin": float(tangent_margin),
        "normal_margin": float(normal_margin),
        "normal_angle_degrees": float(normal_angle_degrees),
    }
    if kind == "cylinder":
        radius = float(fit["parameters"][4])
        if not np.isfinite(radius) or radius <= 0.0:
            raise ValueError("a cylinder selection region requires a positive radius")
        radial = np.linalg.norm(local[:, :2], axis=1)
        if np.any(radial <= 0.0):
            raise ValueError("cylinder selection-region points cannot lie on its axis")
        radial_directions = local[:, :2] / radial[:, None]
        alignment = np.sum(local_normals[:, :2] * radial_directions, axis=1)
        normal_sign = 1.0 if float(np.median(alignment)) >= 0.0 else -1.0
        angular_padding = tangent_margin / radius
        angle_start, angle_span = _angular_interval(
            np.arctan2(local[:, 1], local[:, 0]), angular_padding
        )
        return {
            **common,
            "radius": radius,
            "angle_start": angle_start,
            "angle_span": angle_span,
            "axial_bounds": [
                float(np.min(local[:, 2]) - tangent_margin),
                float(np.max(local[:, 2]) + tangent_margin),
            ],
            "normal_bounds": [
                float(np.min(radial - radius) - normal_margin),
                float(np.max(radial - radius) + normal_margin),
            ],
            "normal_sign": normal_sign,
        }
    if kind == "plane":
        equation = np.asarray(
            fit["plane_equation"] if "plane_equation" in fit else fit["parameters"][:4],
            dtype=float,
        )
        normal = equation[:3]
        length = float(np.linalg.norm(normal))
        if length <= 0.0:
            raise ValueError("a plane selection region requires a nonzero normal")
        normal /= length
        offset = float(equation[3]) / length
        local_normal = normal @ rotation
        local_offset = offset - float(normal @ origin)
        first, second = _plane_basis(local_normal)
        plane_point = local_normal * local_offset
        relative = local - plane_point
        first_values = relative @ first
        second_values = relative @ second
        signed = local @ local_normal - local_offset
        alignment = local_normals @ local_normal
        normal_sign = 1.0 if float(np.median(alignment)) >= 0.0 else -1.0
        return {
            **common,
            "plane_normal": local_normal.tolist(),
            "plane_offset": local_offset,
            "basis_u": first.tolist(),
            "basis_v": second.tolist(),
            "u_bounds": [
                float(np.min(first_values) - tangent_margin),
                float(np.max(first_values) + tangent_margin),
            ],
            "v_bounds": [
                float(np.min(second_values) - tangent_margin),
                float(np.max(second_values) + tangent_margin),
            ],
            "normal_bounds": [
                float(np.min(signed) - normal_margin),
                float(np.max(signed) + normal_margin),
            ],
            "normal_sign": normal_sign,
        }
    if kind == "cone":
        raise ValueError("cone selection regions are not implemented yet")
    raise ValueError(f"unsupported selection-region fit kind {kind!r}")


def apply_selection_region(
    points: FloatArray,
    normals: FloatArray,
    weights: FloatArray,
    region: dict[str, Any],
    origin: FloatArray,
    rotation: FloatArray,
) -> list[int]:
    """Resolve source vertex IDs inside a placed reusable region."""
    if region.get("format") != "scansor-fitted-selection-region-v1":
        raise ValueError("unsupported fitted selection-region format")
    local = (np.asarray(points, dtype=float) - origin) @ rotation
    local_normals = np.asarray(normals, dtype=float) @ rotation
    lengths = np.linalg.norm(local_normals, axis=1)
    valid = (
        np.isfinite(local).all(axis=1)
        & np.isfinite(local_normals).all(axis=1)
        & (lengths > 0.0)
        & (np.asarray(weights, dtype=float) > 0.0)
    )
    local_normals[valid] /= lengths[valid, None]
    cosine = float(np.cos(np.radians(region["normal_angle_degrees"])))
    lower_normal, upper_normal = region["normal_bounds"]
    if region["kind"] == "cylinder":
        radial = np.linalg.norm(local[:, :2], axis=1)
        nonzero = radial > 0.0
        angles = np.mod(np.arctan2(local[:, 1], local[:, 0]), 2.0 * np.pi)
        relative_angles = np.mod(angles - float(region["angle_start"]), 2.0 * np.pi)
        radial_directions = np.zeros((len(local), 3), dtype=float)
        radial_directions[nonzero, :2] = local[nonzero, :2] / radial[nonzero, None]
        alignment = np.sum(local_normals * radial_directions, axis=1) * float(
            region["normal_sign"]
        )
        lower_axial, upper_axial = region["axial_bounds"]
        radial_offset = radial - float(region["radius"])
        mask = (
            valid
            & nonzero
            & (relative_angles <= float(region["angle_span"]) + 1e-12)
            & (local[:, 2] >= lower_axial)
            & (local[:, 2] <= upper_axial)
            & (radial_offset >= lower_normal)
            & (radial_offset <= upper_normal)
            & (alignment >= cosine)
        )
    elif region["kind"] == "plane":
        normal = np.asarray(region["plane_normal"], dtype=float)
        first = np.asarray(region["basis_u"], dtype=float)
        second = np.asarray(region["basis_v"], dtype=float)
        offset = float(region["plane_offset"])
        plane_point = normal * offset
        relative = local - plane_point
        first_values = relative @ first
        second_values = relative @ second
        signed = local @ normal - offset
        alignment = (local_normals @ normal) * float(region["normal_sign"])
        lower_u, upper_u = region["u_bounds"]
        lower_v, upper_v = region["v_bounds"]
        mask = (
            valid
            & (first_values >= lower_u)
            & (first_values <= upper_u)
            & (second_values >= lower_v)
            & (second_values <= upper_v)
            & (signed >= lower_normal)
            & (signed <= upper_normal)
            & (alignment >= cosine)
        )
    else:
        raise ValueError(f"unsupported selection-region kind {region['kind']!r}")
    return [int(value) for value in np.flatnonzero(mask)]
