"""Rigid occurrence matching from user-painted corresponding selections."""

from __future__ import annotations

from itertools import permutations, product
from typing import Any

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def _normalized(values: FloatArray) -> FloatArray:
    lengths = np.linalg.norm(values, axis=1)
    if np.any(lengths <= 0.0) or not np.isfinite(lengths).all():
        raise ValueError("matching-selection normals must be finite and nonzero")
    return np.asarray(values / lengths[:, None], dtype=np.float64)


def _eigenbasis(covariance: FloatArray) -> FloatArray:
    _, vectors = np.linalg.eigh(covariance)
    basis = vectors[:, ::-1]
    if np.linalg.det(basis) < 0.0:
        basis[:, -1] *= -1.0
    return np.asarray(basis, dtype=np.float64)


def _bases(points: FloatArray, normals: FloatArray) -> tuple[FloatArray, ...]:
    centered = points - np.mean(points, axis=0)
    scale = max(float(np.linalg.norm(centered, axis=1).max()), 1.0)
    point_covariance = centered.T @ centered / max(len(points), 1)
    normal_covariance = normals.T @ normals / len(normals)
    combined = point_covariance + (scale * 0.35) ** 2 * normal_covariance
    return tuple(
        _eigenbasis(covariance)
        for covariance in (point_covariance, normal_covariance, combined)
    )


def _proper_axis_maps() -> list[FloatArray]:
    maps: list[FloatArray] = []
    for order in permutations(range(3)):
        for signs in product((-1.0, 1.0), repeat=3):
            mapping = np.zeros((3, 3), dtype=float)
            for source_axis, target_axis in enumerate(order):
                mapping[source_axis, target_axis] = signs[source_axis]
            if np.linalg.det(mapping) > 0.0:
                maps.append(mapping)
    return maps


def _representative_sample(
    points: FloatArray,
    normals: FloatArray,
    *,
    limit: int = 256,
) -> tuple[FloatArray, FloatArray]:
    """Select a deterministic, spatially distributed matching sample."""
    if len(points) <= limit:
        return points, normals
    center = np.mean(points, axis=0)
    first = int(np.argmax(np.sum((points - center) ** 2, axis=1)))
    selected = np.empty(limit, dtype=np.int64)
    selected[0] = first
    nearest_squared = np.sum((points - points[first]) ** 2, axis=1)
    for index in range(1, limit):
        next_index = int(np.argmax(nearest_squared))
        selected[index] = next_index
        squared = np.sum((points - points[next_index]) ** 2, axis=1)
        nearest_squared = np.minimum(nearest_squared, squared)
    return points[selected], normals[selected]


def _nearest_pairs(
    source: FloatArray,
    source_normals: FloatArray,
    target: FloatArray,
    target_normals: FloatArray,
    normal_scale: float,
) -> tuple[NDArray[np.int64], FloatArray]:
    delta = source[:, None, :] - target[None, :, :]
    distances = np.sum(delta * delta, axis=2)
    alignment = np.clip(source_normals @ target_normals.T, -1.0, 1.0)
    cost = distances + normal_scale**2 * (1.0 - alignment) ** 2
    nearest = np.argmin(cost, axis=1).astype(np.int64)
    return nearest, distances[np.arange(len(source)), nearest]


def _kabsch(source: FloatArray, target: FloatArray) -> tuple[FloatArray, FloatArray]:
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    left, _, right_t = np.linalg.svd(
        (source - source_center).T @ (target - target_center)
    )
    rotation = left @ right_t
    if np.linalg.det(rotation) < 0.0:
        left[:, -1] *= -1.0
        rotation = left @ right_t
    translation = target_center - source_center @ rotation
    return (
        np.asarray(rotation, dtype=np.float64),
        np.asarray(translation, dtype=np.float64),
    )


def _trimmed_indices(
    distances: FloatArray, fraction: float = 0.72
) -> NDArray[np.int64]:
    count = min(len(distances), max(6, int(np.ceil(len(distances) * fraction))))
    return np.asarray(np.argsort(distances)[:count], dtype=np.int64)


def _refine(
    source: FloatArray,
    source_normals: FloatArray,
    target: FloatArray,
    target_normals: FloatArray,
    rotation: FloatArray,
    translation: FloatArray,
    normal_scale: float,
) -> tuple[FloatArray, FloatArray]:
    for _ in range(30):
        moved = source @ rotation + translation
        moved_normals = source_normals @ rotation
        forward, forward_distances = _nearest_pairs(
            moved, moved_normals, target, target_normals, normal_scale
        )
        reverse, reverse_distances = _nearest_pairs(
            target, target_normals, moved, moved_normals, normal_scale
        )
        keep_forward = _trimmed_indices(forward_distances)
        keep_reverse = _trimmed_indices(reverse_distances)
        paired_source = np.vstack((source[keep_forward], source[reverse[keep_reverse]]))
        paired_target = np.vstack((target[forward[keep_forward]], target[keep_reverse]))
        updated_rotation, updated_translation = _kabsch(paired_source, paired_target)
        change = float(
            np.linalg.norm(updated_rotation - rotation)
            + np.linalg.norm(updated_translation - translation) / normal_scale
        )
        rotation, translation = updated_rotation, updated_translation
        if change < 1e-8:
            break
    return rotation, translation


def _score(
    source: FloatArray,
    source_normals: FloatArray,
    target: FloatArray,
    target_normals: FloatArray,
    rotation: FloatArray,
    translation: FloatArray,
    normal_scale: float,
) -> tuple[float, float, float]:
    moved = source @ rotation + translation
    moved_normals = source_normals @ rotation
    forward, forward_distances = _nearest_pairs(
        moved, moved_normals, target, target_normals, normal_scale
    )
    reverse, reverse_distances = _nearest_pairs(
        target, target_normals, moved, moved_normals, normal_scale
    )
    forward_keep = _trimmed_indices(forward_distances)
    reverse_keep = _trimmed_indices(reverse_distances)
    squared = np.concatenate(
        (forward_distances[forward_keep], reverse_distances[reverse_keep])
    )
    alignments = np.concatenate(
        (
            np.sum(
                moved_normals[forward_keep] * target_normals[forward[forward_keep]],
                axis=1,
            ),
            np.sum(
                target_normals[reverse_keep] * moved_normals[reverse[reverse_keep]],
                axis=1,
            ),
        )
    )
    rms = float(np.sqrt(np.mean(squared)))
    normal_error = float(np.mean(1.0 - np.clip(alignments, -1.0, 1.0)))
    return rms + normal_scale * normal_error, rms, normal_error


def estimate_rigid_match(
    source_points: FloatArray,
    source_normals: FloatArray,
    target_points: FloatArray,
    target_normals: FloatArray,
) -> dict[str, Any]:
    """Estimate a source-to-target row-vector rigid transform.

    The selections need only describe corresponding local geometry; their mesh
    vertices and painted extents need not match exactly.
    """
    source = np.asarray(source_points, dtype=float)
    target = np.asarray(target_points, dtype=float)
    if source.shape[1:] != (3,) or target.shape[1:] != (3,):
        raise ValueError("matching selections must contain 3D points")
    if len(source) < 6 or len(target) < 6:
        raise ValueError("matching selections require at least six vertices each")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("matching selections must contain finite points")
    source_n = _normalized(np.asarray(source_normals, dtype=float))
    target_n = _normalized(np.asarray(target_normals, dtype=float))
    if len(source_n) != len(source) or len(target_n) != len(target):
        raise ValueError("matching points and normals must have equal lengths")

    source_count = len(source)
    target_count = len(target)
    source, source_n = _representative_sample(source, source_n)
    target, target_n = _representative_sample(target, target_n)
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    scale = max(
        float(np.median(np.linalg.norm(source - source_center, axis=1))),
        float(np.median(np.linalg.norm(target - target_center, axis=1))),
        1e-6,
    )
    source_bases = _bases(source, source_n)
    target_bases = _bases(target, target_n)
    initial_candidates: list[tuple[float, float, float, FloatArray, FloatArray]] = []
    for source_basis, target_basis in zip(source_bases, target_bases, strict=True):
        for mapping in _proper_axis_maps():
            rotation = source_basis @ mapping @ target_basis.T
            translation = target_center - source_center @ rotation
            score, rms, normal_error = _score(
                source,
                source_n,
                target,
                target_n,
                rotation,
                translation,
                scale * 0.35,
            )
            initial_candidates.append((score, rms, normal_error, rotation, translation))
    initial_candidates.sort(key=lambda value: value[0])
    candidates: list[tuple[float, float, float, FloatArray, FloatArray]] = []
    for _, _, _, rotation, translation in initial_candidates[:12]:
        rotation, translation = _refine(
            source,
            source_n,
            target,
            target_n,
            rotation,
            translation,
            scale * 0.35,
        )
        score, rms, normal_error = _score(
            source,
            source_n,
            target,
            target_n,
            rotation,
            translation,
            scale * 0.35,
        )
        candidates.append((score, rms, normal_error, rotation, translation))
    candidates.sort(key=lambda value: value[0])
    best = candidates[0]
    second = next(
        (
            candidate
            for candidate in candidates[1:]
            if np.linalg.norm(candidate[3] - best[3]) > 1e-3
        ),
        candidates[1],
    )
    ambiguity_ratio = float(second[0] / max(best[0], 1e-12))
    centered = source - source_center
    singular = np.linalg.svd(centered, compute_uv=False)
    geometric_rank = int(np.sum(singular > max(singular[0] * 1e-3, 1e-8)))
    return {
        "format": "scansor-rigid-occurrence-match-v1",
        "rotation": best[3].tolist(),
        "translation": best[4].tolist(),
        "rms": best[1],
        "normal_error": best[2],
        "ambiguity_ratio": ambiguity_ratio,
        "geometric_rank": geometric_rank,
        "source_count": source_count,
        "target_count": target_count,
    }


def surface_region_frame(
    points: FloatArray, fit: dict[str, Any]
) -> tuple[FloatArray, FloatArray]:
    """Construct a stable local frame for a fitted selection region."""
    selected = np.asarray(points, dtype=float)
    if len(selected) < 3:
        raise ValueError("a reusable fit selection requires at least three vertices")
    center = np.mean(selected, axis=0)
    if fit.get("kind") != "cylinder":
        return np.asarray(center, dtype=np.float64), np.eye(3, dtype=np.float64)
    parameters = np.asarray(fit["parameters"], dtype=float)
    direction = np.asarray(
        fit.get("axis_display", [parameters[2], parameters[3], 1.0]),
        dtype=float,
    )
    direction /= np.linalg.norm(direction)
    point = np.asarray(
        fit.get("point_display", [parameters[0], parameters[1], 0.0]),
        dtype=float,
    )
    origin = point + float((center - point) @ direction) * direction
    radial = center - origin
    if np.linalg.norm(radial) < 1e-8:
        candidate = np.eye(3)[int(np.argmin(np.abs(direction)))]
        radial = candidate - float(candidate @ direction) * direction
    radial /= np.linalg.norm(radial)
    tangential = np.cross(direction, radial)
    tangential /= np.linalg.norm(tangential)
    return (
        np.asarray(origin, dtype=np.float64),
        np.asarray(np.column_stack((radial, tangential, direction)), dtype=np.float64),
    )


def transformed_fit_seed(
    fit: dict[str, Any],
    rotation: FloatArray,
    translation: FloatArray,
) -> tuple[list[float] | None, tuple[float, float]]:
    """Move a fitted surface initializer and its axial chart into a target pose."""
    domain_values = fit["axial_domain"]
    domain = (float(domain_values[0]), float(domain_values[1]))
    if fit.get("kind") not in ("cone", "cylinder"):
        return None, domain
    parameters = np.asarray(fit["parameters"], dtype=float)
    point = np.asarray([parameters[0], parameters[1], 0.0])
    raw_direction = np.asarray([parameters[2], parameters[3], 1.0])
    moved_point = point @ rotation + translation
    moved_direction = raw_direction @ rotation
    if abs(float(moved_direction[2])) < 1e-6:
        raise ValueError(
            "reused surface axis is outside the current local Z parameter chart"
        )
    chart_direction = moved_direction / moved_direction[2]
    chart_point = moved_point - chart_direction * moved_point[2]
    moved_domain = sorted(
        moved_point[2] + moved_direction[2] * np.asarray(domain, dtype=float)
    )
    return (
        [
            float(chart_point[0]),
            float(chart_point[1]),
            float(chart_direction[0]),
            float(chart_direction[1]),
            float(parameters[4]),
        ],
        (float(moved_domain[0]), float(moved_domain[1])),
    )
