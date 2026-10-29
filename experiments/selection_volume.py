"""Saved surface-selection volumes for already-aligned triangle scans.

The support is the barycentric dual of selected source vertices, not a hull.
Analytic support uses straight triangles in a developable surface chart; this
is a discrete footprint definition, not an exact curved-boundary CAD offset.
Mesh membership uses closest points on saved cells, with a normal slab and
tangential boundary padding. At sharp folds this is a union of local slabs,
not a watertight Boolean solid. Explicit padding can close holes or join patches.

Coverage is deterministic area-weighted quadrature against target triangles,
with at most 4096 samples by default, independent of target vertex counts.
Larger support uses area-stratified sampling. Small gaps can be missed: there
is no guaranteed error bound. This is a warning, not a fit acceptance criterion.
All query working sets are bounded by a BVH leaf and the requested chunk size.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any, ClassVar, Literal, cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, field_validator

from experiments.scan_workspace import check_cancelled
from experiments.surface_primitives import (
    basis,
    projected_axial_parameter,
    revolution_geometry,
)

Array = NDArray[np.float64]
FORMAT = "scansor-selection-volume-v2"
MAX_CELLS = 600_000
_LEAF_SIZE = 16


class VolumeOptions(BaseModel):
    """None requests the versioned source-derived default for that control."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    inward_thickness: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    outward_thickness: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    edge_padding: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    normal_angle_degrees: float | None = Field(
        default=None, gt=0, le=90, allow_inf_nan=False
    )

    @field_validator("*", mode="before")
    @classmethod
    def real_controls(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("volume controls must be finite real numbers or null")
        return value


def _array(value: object, shape: tuple[int | None, ...], name: str) -> Array:
    raw = np.asarray(value)
    if raw.dtype.kind not in "iuf":
        raise ValueError(f"{name} must contain real coordinates")
    result = np.asarray(value, dtype=float)
    if (
        result.ndim != len(shape)
        or any(
            size is not None and result.shape[i] != size for i, size in enumerate(shape)
        )
        or not np.isfinite(result).all()
    ):
        raise ValueError(f"{name} must contain finite coordinates with shape {shape}")
    return result


def _normals(value: object, count: int, name: str) -> Array:
    normals = _array(value, (count, 3), name)
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 0) or not np.isfinite(lengths).all():
        raise ValueError(f"{name} must be normalizable")
    return normals / lengths[:, None]


def _triangles(value: object, count: int) -> NDArray[np.int64]:
    raw = np.asarray(value)
    if raw.size == 0:
        return np.empty((0, 3), dtype=np.int64)
    if raw.ndim != 2 or raw.shape[1] != 3 or raw.dtype.kind not in "iu":
        raise ValueError("triangles must contain integer vertex triples")
    if np.any(raw < 0) or np.any(raw >= count):
        raise ValueError("triangle vertex index is out of range")
    return raw.astype(np.int64, copy=False)


def _surface(kind: str, fit: dict[str, Any] | None) -> dict[str, Any] | None:
    if kind == "mesh":
        if fit is not None:
            raise ValueError("mesh volumes do not use an analytic fit")
        return None
    if fit is None or fit.get("kind") != kind:
        raise ValueError("an analytic volume requires a matching fitted surface")
    if kind == "plane":
        equation = _array(
            fit.get("plane_equation", fit.get("parameters", [])[:4]),
            (4,),
            "plane equation",
        )
        length = float(np.linalg.norm(equation[:3]))
        if not np.isfinite(length) or length <= 0:
            raise ValueError("plane normal must be nonzero")
        normal = equation[:3] / length
        u, v = basis(normal)
        return {
            "kind": kind,
            "normal": normal.tolist(),
            "offset": float(equation[3] / length),
            "basis_u": u.tolist(),
            "basis_v": v.tolist(),
        }
    if kind not in ("cylinder", "cone"):
        raise ValueError(f"unsupported volume kind {kind!r}")
    geometry = revolution_geometry(fit)
    u, v = basis(np.asarray(geometry["axis"]))
    return {**geometry, "basis_u": u.tolist(), "basis_v": v.tolist()}


def _project(
    points: Array, surface: dict[str, Any]
) -> tuple[Array, Array, Array, Array]:
    """Return projected world points, expected normals, signed distance, chart."""
    u, v = np.asarray(surface["basis_u"]), np.asarray(surface["basis_v"])
    if surface["kind"] == "plane":
        normal = np.asarray(surface["normal"])
        signed = points @ normal - float(surface["offset"])
        projected = points - signed[:, None] * normal
        chart = np.column_stack((projected @ u, projected @ v))
        return projected, np.tile(normal, (len(points), 1)), signed, chart
    relative = points - np.asarray(surface["origin"])
    axis = np.asarray(surface["axis"])
    axial = relative @ axis
    radial_vectors = relative - axial[:, None] * axis
    radial = np.linalg.norm(radial_vectors, axis=1)
    direction = np.zeros_like(points)
    nonzero = radial > 0
    direction[nonzero] = radial_vectors[nonzero] / radial[nonzero, None]
    radius, slope = float(surface["radius"]), float(surface["slope"])
    scale = float(np.hypot(1.0, slope))
    projected_axial = projected_axial_parameter(axial, radial, radius, slope)
    projected_radius = radius + slope * projected_axial
    expected = direction / scale - (slope / scale) * axis
    signed = (radial - radius - slope * axial) / scale
    projected = (
        np.asarray(surface["origin"])
        + projected_axial[:, None] * axis
        + projected_radius[:, None] * direction
    )
    theta = np.arctan2(direction @ v, direction @ u)
    chart = np.column_stack((theta, projected_axial * scale))
    invalid = (~nonzero) | (projected_radius <= 0)
    chart[invalid] = np.nan
    return projected, expected, signed, chart


def _develop(chart: Array, surface: dict[str, Any]) -> Array:
    """Isometric cylinder/cone development, stable at tiny nonzero taper."""
    if surface["kind"] == "plane":
        return chart.copy()
    theta, generator = chart[:, 0], chart[:, 1]
    slope, radius = float(surface["slope"]), float(surface["radius"])
    if slope == 0:
        return np.column_stack((theta * radius, generator))
    scale = float(np.hypot(1, slope))
    factor = abs(slope) / scale
    radius_at = radius + (slope / scale) * generator
    angle = factor * theta
    # These are rho*sin(angle)/factor and rho*cos(angle)/factor minus
    # a common anchor. The sinc forms avoid catastrophic cancellation and
    # enormous apex distances for near-cylindrical cones.
    x = radius_at * theta * np.sinc(angle / np.pi)
    y = np.copysign(1, slope) * generator - radius_at * theta * np.sin(
        angle / 2
    ) * np.sinc(angle / (2 * np.pi))
    return np.column_stack((x, y))


def _dual_cells(
    points: Array,
    normals: Array,
    triangles: NDArray[np.int64],
    ids: NDArray[np.int64],
    cancelled: Callable[[], bool] | None,
) -> tuple[Array, Array, float]:
    selected = np.zeros(len(points), dtype=bool)
    selected[ids] = True
    cells: list[Array] = []
    cell_normals: list[Array] = []
    spacing: list[float] = []
    for start in range(0, len(triangles), 4096):
        check_cancelled(cancelled)
        faces = triangles[start : start + 4096]
        faces = faces[np.any(selected[faces], axis=1)]
        for indices in faces:
            corners = points[indices]
            cross = np.cross(corners[1] - corners[0], corners[2] - corners[0])
            if not np.isfinite(cross).all() or np.linalg.norm(cross) <= 0:
                continue
            face_normals = normals[indices]
            center, normal_center = corners.mean(axis=0), face_normals.mean(axis=0)
            for i in range(3):
                if not selected[indices[i]]:
                    continue
                j, k = (i + 1) % 3, (i + 2) % 3
                left, right = (
                    (corners[i] + corners[j]) / 2,
                    (corners[i] + corners[k]) / 2,
                )
                nl = (face_normals[i] + face_normals[j]) / 2
                nr = (face_normals[i] + face_normals[k]) / 2
                cells.extend(
                    (
                        np.array([corners[i], left, center]),
                        np.array([corners[i], center, right]),
                    )
                )
                cell_normals.extend(
                    (
                        np.array([face_normals[i], nl, normal_center]),
                        np.array([face_normals[i], normal_center, nr]),
                    )
                )
                spacing.extend(
                    (
                        float(np.linalg.norm(corners[i] - corners[j])),
                        float(np.linalg.norm(corners[i] - corners[k])),
                    )
                )
                if len(cells) > MAX_CELLS:
                    raise ValueError(
                        f"selection volume exceeds {MAX_CELLS} support cells"
                    )
    if not cells:
        raise ValueError("selected vertices have no positive-area triangle support")
    result, orientations = np.asarray(cells), np.asarray(cell_normals)
    lengths = np.linalg.norm(orientations, axis=2)
    if np.any(lengths <= 1e-12):
        raise ValueError("selected mesh has incompatible opposing incident normals")
    orientations /= lengths[:, :, None]
    return result, orientations, float(np.median(spacing))


def build_selection_volume(
    points: object,
    normals: object,
    triangles: object,
    selected_ids: object,
    *,
    kind: Literal["mesh", "plane", "cylinder", "cone"] = "mesh",
    fit: dict[str, Any] | None = None,
    options: VolumeOptions | dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Capture support independent of loaded source buffers or future row IDs.

    source-spacing-v1: spacing is the median selected incident edge length;
    thickness is the complete oriented source residual envelope plus spacing/4;
    automatic edge padding is zero (preserves holes and disconnected support);
    angle is maximum source normal deviation plus 5 degrees, bounded to [5,90].
    These deliberately conservative defaults are independent of target density.
    """
    check_cancelled(cancelled)
    xyz = _array(points, (None, 3), "source points")
    unit_normals = _normals(normals, len(xyz), "source normals")
    faces = _triangles(triangles, len(xyz))
    raw_ids = np.asarray(selected_ids)
    if (
        raw_ids.ndim != 1
        or raw_ids.dtype.kind not in "iu"
        or not 1 <= len(raw_ids) <= 25_000
    ):
        raise ValueError("selection requires 1 to 25000 integer vertex IDs")
    if (
        np.any(raw_ids < 0)
        or np.any(raw_ids >= len(xyz))
        or len(np.unique(raw_ids)) != len(raw_ids)
    ):
        raise ValueError("selected vertex IDs must be unique and in range")
    ids = raw_ids.astype(np.int64, copy=False)
    controls = (
        options
        if isinstance(options, VolumeOptions)
        else VolumeOptions.model_validate(options or {})
    )
    surface = _surface(kind, fit)
    patch, patch_normals, spacing = _dual_cells(
        xyz, unit_normals, faces, ids, cancelled
    )
    footprint: list[Any] = []
    sign = 1.0
    residual = np.zeros(len(ids))
    deviation = np.zeros(len(ids))
    if surface is not None:
        _, expected, residual, _ = _project(xyz[ids], surface)
        alignment = np.sum(unit_normals[ids] * expected, axis=1)
        sign = 1.0 if float(np.median(alignment)) >= 0 else -1.0
        residual *= sign
        deviation = np.degrees(np.arccos(np.clip(alignment * sign, -1, 1)))
        projected, expected_patch, _, chart = _project(patch.reshape(-1, 3), surface)
        if not np.isfinite(chart).all():
            raise ValueError("selected support crosses the analytic axis or cone apex")
        if kind != "plane":
            angles = chart[:, 0].reshape(-1, 3)
            angles[:] = (
                angles[:, :1] + (angles - angles[:, :1] + np.pi) % (2 * np.pi) - np.pi
            )
            if np.any(np.ptp(angles, axis=1) >= np.pi):
                raise ValueError(
                    "selected analytic cell spans an ambiguous angular sheet"
                )
        developed = _develop(chart, surface).reshape(-1, 3, 2)
        area = np.abs(
            _cross2(
                developed[:, 1] - developed[:, 0], developed[:, 2] - developed[:, 0]
            )
        )
        good = area > 0
        if not np.all(good):
            raise ValueError("selected support degenerates on the fitted surface")
        footprint = developed.tolist()
        patch = projected.reshape(-1, 3, 3)
        patch_normals = sign * expected_patch.reshape(-1, 3, 3)
    else:
        means = patch_normals.mean(axis=1)
        means /= np.linalg.norm(means, axis=1)[:, None]
        deviation = np.degrees(
            np.arccos(np.clip(np.sum(patch_normals * means[:, None], axis=2), -1, 1))
        ).ravel()
    defaults = {
        "inward_thickness": max(0.0, float(-residual.min())) + spacing / 4,
        "outward_thickness": max(0.0, float(residual.max())) + spacing / 4,
        "edge_padding": 0.0,
        "normal_angle_degrees": float(np.clip(deviation.max() + 5, 5, 90)),
    }
    resolved = {
        name: defaults[name] if value is None else value
        for name, value in controls.model_dump().items()
    }
    return validate_selection_volume(
        {
            "format": FORMAT,
            "kind": kind,
            "surface": surface,
            "patch_vertices": patch.tolist(),
            "patch_normals": patch_normals.tolist(),
            "footprint": footprint,
            "normal_sign": sign,
            "options": controls.model_dump(),
            "defaults": defaults,
            "resolved": {
                **resolved,
                "spacing": spacing,
                "algorithm": "source-spacing-v1",
            },
            "provenance": provenance or {},
            "selected_count": len(ids),
        }
    )


def validate_selection_volume(payload: object) -> dict[str, Any]:
    """Validate saved geometry and return a detached JSON-safe representation."""
    if not isinstance(payload, dict) or set(payload) != {
        "format",
        "kind",
        "surface",
        "patch_vertices",
        "patch_normals",
        "footprint",
        "normal_sign",
        "options",
        "defaults",
        "resolved",
        "provenance",
        "selected_count",
    }:
        raise ValueError("invalid selection volume record fields")
    if payload["format"] != FORMAT or payload["kind"] not in (
        "mesh",
        "plane",
        "cylinder",
        "cone",
    ):
        raise ValueError("unsupported selection volume format or kind")
    patch = _array(payload["patch_vertices"], (None, 3, 3), "saved patch")
    if not 1 <= len(patch) <= MAX_CELLS:
        raise ValueError("saved patch cell count is out of range")
    normals = _array(payload["patch_normals"], patch.shape, "saved normals")
    if not np.allclose(np.linalg.norm(normals, axis=2), 1, rtol=0, atol=1e-10):
        raise ValueError("saved normals must be unit vectors")
    if payload["normal_sign"] not in (-1.0, 1.0) or isinstance(
        payload["normal_sign"], bool
    ):
        raise ValueError("saved normal side must be +1 or -1")
    if (
        type(payload["selected_count"]) is not int
        or not 1 <= payload["selected_count"] <= 25_000
    ):
        raise ValueError("invalid saved selected count")
    controls = VolumeOptions.model_validate(payload["options"])
    defaults = payload["defaults"]
    if not isinstance(defaults, dict) or set(defaults) != set(
        VolumeOptions.model_fields
    ):
        raise ValueError("invalid saved automatic volume controls")
    if any(value is None for value in defaults.values()):
        raise ValueError("saved automatic controls cannot be null")
    _ = VolumeOptions.model_validate(defaults)
    resolved = payload["resolved"]
    if not isinstance(resolved, dict) or set(resolved) != {
        *VolumeOptions.model_fields,
        "spacing",
        "algorithm",
    }:
        raise ValueError("invalid resolved volume controls")
    if resolved["algorithm"] != "source-spacing-v1":
        raise ValueError("unsupported volume default algorithm")
    numeric = {key: resolved[key] for key in VolumeOptions.model_fields}
    if any(value is None for value in numeric.values()):
        raise ValueError("resolved controls cannot be null")
    _ = VolumeOptions.model_validate(numeric)
    spacing = resolved["spacing"]
    if (
        isinstance(spacing, bool)
        or not isinstance(spacing, (int, float))
        or not np.isfinite(spacing)
        or spacing <= 0
    ):
        raise ValueError("saved spacing must be positive and finite")
    for key, value in controls.model_dump().items():
        expected_value = defaults[key] if value is None else value
        if expected_value != resolved[key]:
            raise ValueError(
                "saved automatic or overridden controls disagree with resolved controls"
            )
    surface = payload["surface"]
    if payload["kind"] == "mesh":
        if surface is not None or payload["footprint"] != []:
            raise ValueError("mesh volume cannot contain analytic geometry")
    else:
        if not isinstance(surface, dict):
            raise ValueError("saved analytic surface kind mismatch")
        surface = cast(dict[str, Any], surface)
        if surface.get("kind") != payload["kind"]:
            raise ValueError("saved analytic surface kind mismatch")
        footprint = _array(payload["footprint"], (len(patch), 3, 2), "saved footprint")
        if np.any(
            np.abs(
                _cross2(
                    footprint[:, 1] - footprint[:, 0], footprint[:, 2] - footprint[:, 0]
                )
            )
            <= 0
        ):
            raise ValueError("saved footprint must contain positive-area triangles")
        if surface["kind"] == "plane":
            if set(surface) != {"kind", "normal", "offset", "basis_u", "basis_v"}:
                raise ValueError("invalid saved plane surface")
            normal = _array(surface["normal"], (3,), "saved plane normal")
            if not np.isclose(np.linalg.norm(normal), 1, atol=1e-10, rtol=0):
                raise ValueError("saved plane normal must be unit")
            if (
                isinstance(surface["offset"], bool)
                or not isinstance(surface["offset"], (int, float))
                or not np.isfinite(surface["offset"])
            ):
                raise ValueError("saved plane offset must be finite")
            axis = normal
        else:
            if set(surface) != {
                "kind",
                "origin",
                "axis",
                "radius",
                "slope",
                "axial_domain",
                "basis_u",
                "basis_v",
            }:
                raise ValueError("invalid saved revolution surface")
            geometry = {
                key: surface[key]
                for key in ("kind", "origin", "axis", "radius", "slope", "axial_domain")
            }
            if surface["axial_domain"] is not None:
                _ = revolution_geometry({"kind": surface["kind"], "analytic": geometry})
            for name in ("radius", "slope"):
                value = surface[name]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not np.isfinite(value)
                ):
                    raise ValueError(
                        "saved revolution parameters must be finite real numbers"
                    )
            _ = _array(surface["origin"], (3,), "saved revolution origin")
            axis = _array(surface["axis"], (3,), "saved revolution axis")
            if (
                not np.isclose(np.linalg.norm(axis), 1, atol=1e-10, rtol=0)
                or not np.isfinite([surface["radius"], surface["slope"]]).all()
            ):
                raise ValueError("invalid saved revolution geometry")
            if surface["kind"] == "cylinder" and (
                surface["radius"] <= 0 or surface["slope"] != 0
            ):
                raise ValueError("invalid saved cylinder geometry")
        u = _array(surface["basis_u"], (3,), "saved surface basis")
        v = _array(surface["basis_v"], (3,), "saved surface basis")
        matrix = np.column_stack((u, v, axis))
        if (
            not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-10, rtol=0)
            or np.linalg.det(matrix) < 0
        ):
            raise ValueError("saved surface basis must be right-handed orthonormal")
        projected, expected, _, chart = _project(patch.reshape(-1, 3), surface)
        magnitude = max(float(np.max(np.abs(patch))), float(spacing))
        tolerance = 128 * np.finfo(float).eps * magnitude
        if not np.isfinite(chart).all() or not np.allclose(
            projected, patch.reshape(-1, 3), atol=tolerance, rtol=1e-12
        ):
            raise ValueError("saved patch must lie on its analytic surface")
        if not np.allclose(
            normals.reshape(-1, 3),
            expected * payload["normal_sign"],
            atol=1e-10,
            rtol=0,
        ):
            raise ValueError("saved normals disagree with the analytic surface")
        if surface["kind"] != "plane":
            angles = chart[:, 0].reshape(-1, 3)
            angles[:] = (
                angles[:, :1] + (angles - angles[:, :1] + np.pi) % (2 * np.pi) - np.pi
            )
        developed = _develop(chart, surface).reshape(-1, 3, 2)
        chart_tolerance = (
            128
            * np.finfo(float).eps
            * max(float(np.max(np.abs(developed))), float(spacing))
        )
        if not np.allclose(footprint, developed, atol=chart_tolerance, rtol=1e-12):
            raise ValueError("saved footprint disagrees with the analytic patch")
    area = np.linalg.norm(
        np.cross(patch[:, 1] - patch[:, 0], patch[:, 2] - patch[:, 0]), axis=1
    )
    if np.any(area <= 0) or not np.isfinite(area).all():
        raise ValueError("saved patch must contain positive-area triangles")
    if not isinstance(payload["provenance"], dict):
        raise ValueError("saved provenance must be a JSON object")
    try:
        return json.loads(json.dumps(payload, allow_nan=False))
    except (ValueError, TypeError) as error:
        raise ValueError("selection volume must contain finite JSON values") from error


def update_selection_volume_options(
    volume: object, options: VolumeOptions | dict[str, Any]
) -> dict[str, Any]:
    """Update supplied controls without source buffers or rebuilding geometry.

    Only explicitly supplied fields change; null restores that field's saved
    automatic value. Omitted fields retain their previous overrides. Persisted
    defaults include the original residual envelope, which cannot be recovered
    from a fitted volume's projected support after its source is unloaded.
    """
    saved = validate_selection_volume(volume)
    changes = (
        options
        if isinstance(options, VolumeOptions)
        else VolumeOptions.model_validate(options)
    )
    saved["options"].update(changes.model_dump(exclude_unset=True))
    for name, value in saved["options"].items():
        saved["resolved"][name] = saved["defaults"][name] if value is None else value
    return validate_selection_volume(saved)


def _cross2(a: Array, b: Array) -> Array:
    return a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]


class TriangleIndex:
    """Median-split AABB tree; leaves hold at most sixteen triangles."""

    def __init__(
        self,
        cells: Array,
        padding: float = 0,
        cancelled: Callable[[], bool] | None = None,
    ):
        self.cells: Array = cells
        self.cancelled: Callable[[], bool] | None = cancelled
        lower, upper = cells.min(axis=1) - padding, cells.max(axis=1) + padding
        self.nodes: list[tuple[Array, Array, int, int, NDArray[np.int64]]] = []

        def build(ids: NDArray[np.int64]) -> int:
            check_cancelled(cancelled)
            lo, hi = lower[ids].min(axis=0), upper[ids].max(axis=0)
            node = len(self.nodes)
            self.nodes.append((lo, hi, -1, -1, np.empty(0, dtype=np.int64)))
            if len(ids) <= _LEAF_SIZE:
                self.nodes[node] = (lo, hi, -1, -1, ids)
            else:
                centers = (lower[ids] + upper[ids]) / 2
                dimension = int(np.argmax(np.ptp(centers, axis=0)))
                order = np.argsort(centers[:, dimension], kind="stable")
                middle = len(ids) // 2
                left, right = build(ids[order[:middle]]), build(ids[order[middle:]])
                self.nodes[node] = (lo, hi, left, right, np.empty(0, dtype=np.int64))
            return node

        _ = build(np.arange(len(cells), dtype=np.int64))

    def candidates(self, point: Array) -> Iterator[NDArray[np.int64]]:
        stack = [0]
        while stack:
            check_cancelled(self.cancelled)
            lo, hi, left, right, ids = self.nodes[stack.pop()]
            if np.any(point < lo) or np.any(point > hi):
                continue
            if left < 0:
                yield ids
            else:
                stack.extend((right, left))


def _closest(point: Array, cells: Array) -> tuple[Array, Array]:
    """Closest triangle point and barycentric weights, in two or three dimensions."""
    a, b, c = cells[:, 0], cells[:, 1], cells[:, 2]
    e0, e1, relative = b - a, c - a, point - a
    if cells.shape[2] == 2:
        determinant = _cross2(e0, e1)
        safe = determinant != 0
        denominator = np.where(safe, determinant, 1)
        u, v = _cross2(relative, e1) / denominator, _cross2(e0, relative) / denominator
        projected_point = np.broadcast_to(point, a.shape)
    else:
        cross = np.cross(e0, e1)
        determinant = np.hypot.reduce(cross, axis=1)
        safe = determinant > 0
        denominator = np.where(safe, determinant, 1)
        normal = cross / denominator[:, None]
        u = np.sum(np.cross(relative, e1) * normal, axis=1) / denominator
        v = np.sum(np.cross(e0, relative) * normal, axis=1) / denominator
        projected_point = point - np.sum(relative * normal, axis=1)[:, None] * normal
    bary = np.column_stack((1 - u - v, u, v))
    interior = safe & np.all(bary >= 0, axis=1)
    best = np.full(len(cells), np.inf)
    closest = np.zeros_like(a)
    result = np.zeros((len(cells), 3))
    for i, j in ((0, 1), (1, 2), (2, 0)):
        edge = cells[:, j] - cells[:, i]
        squared = np.sum(edge * edge, axis=1)
        t = np.clip(
            np.sum((point - cells[:, i]) * edge, axis=1)
            / np.where(squared > 0, squared, 1),
            0,
            1,
        )
        candidate = cells[:, i] + t[:, None] * edge
        distance = np.sum((point - candidate) ** 2, axis=1)
        better = distance < best
        closest[better], best[better] = candidate[better], distance[better]
        result[better] = 0
        result[better, i], result[better, j] = 1 - t[better], t[better]
    closest[interior] = projected_point[interior]
    result[interior] = bary[interior]
    return closest, result


def _chunk_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 65_536:
        raise ValueError("chunk size must be an integer in [1, 65536]")
    return value


def _mesh_query(
    query: Array,
    directions: Array,
    valid: NDArray[np.bool_],
    cells: Array,
    patch_normals: Array,
    index: TriangleIndex,
    inward: float,
    outward: float,
    padding: float,
    tolerance: float,
    cosine: float,
    cancelled: Callable[[], bool] | None,
) -> NDArray[np.int64]:
    """Batch point/tree and point/triangle tests; at most 1024 x 16 pairs."""
    accepted = np.zeros(len(query), dtype=np.bool_)
    stack = [(0, np.flatnonzero(valid))]
    while stack:
        check_cancelled(cancelled)
        node, ids = stack.pop()
        lo, hi, left, right, triangles = index.nodes[node]
        coordinates = query[ids]
        ids = ids[
            ~accepted[ids]
            & np.all(coordinates >= lo, axis=1)
            & np.all(coordinates <= hi, axis=1)
        ]
        if not len(ids):
            continue
        if left >= 0:
            stack.extend(((right, ids), (left, ids)))
            continue
        point_ids = np.repeat(ids, len(triangles))
        triangle_ids = np.tile(triangles, len(ids))
        paired_cells = cells[triangle_ids]
        closest, bary = _closest(query[point_ids], paired_cells)
        expected = np.sum(patch_normals[triangle_ids] * bary[:, :, None], axis=1)
        expected /= np.linalg.norm(expected, axis=1)[:, None]
        normal = np.cross(
            paired_cells[:, 1] - paired_cells[:, 0],
            paired_cells[:, 2] - paired_cells[:, 0],
        )
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        normal *= np.where(np.sum(normal * expected, axis=1) < 0, -1, 1)[:, None]
        displacement = query[point_ids] - closest
        signed = np.sum(displacement * normal, axis=1)
        tangent = np.linalg.norm(displacement - signed[:, None] * normal, axis=1)
        alignment = np.sum(expected * directions[point_ids], axis=1)
        # Match the original per-point matrix/vector dot at angular boundaries;
        # the batched row reduction can differ by a few rounding bits.
        threshold = cosine - 1e-14
        near = np.any(
            (np.abs(alignment - threshold) <= 32 * np.finfo(float).eps).reshape(
                len(ids), len(triangles)
            ),
            axis=1,
        )
        for row in np.flatnonzero(near):
            group = slice(row * len(triangles), (row + 1) * len(triangles))
            alignment[group] = expected[group] @ directions[ids[row]]
        matched = (
            (signed >= -inward - tolerance)
            & (signed <= outward + tolerance)
            & (tangent <= padding + tolerance)
            & (alignment >= threshold)
        )
        accepted[point_ids[matched]] = True
    check_cancelled(cancelled)
    return np.flatnonzero(accepted)


def apply_selection_volume(
    points: object,
    normals: object,
    weights: object,
    volume: object,
    *,
    chunk_size: int = 1024,
    cancelled: Callable[[], bool] | None = None,
    query_rotation: object | None = None,
    query_translation: object | None = None,
    max_selected: int | None = None,
) -> list[int]:
    """Resolve positive-weight observations; saved authoring row IDs play no role."""
    check_cancelled(cancelled)
    saved = validate_selection_volume(volume)
    if max_selected is not None and (type(max_selected) is not int or max_selected < 1):
        raise ValueError("selection membership budget must be a positive integer")
    # Even a caller requesting a larger chunk cannot expand a BVH leaf into
    # more than 16,384 paired narrow-phase tests at once.
    size = min(_chunk_size(chunk_size), 1024)
    xyz = np.asarray(points, dtype=float)
    directions = np.asarray(normals, dtype=float)
    area_weights = np.asarray(weights, dtype=float)
    rotation = (
        np.eye(3)
        if query_rotation is None
        else _array(query_rotation, (3, 3), "query rotation")
    )
    translation = (
        np.zeros(3)
        if query_translation is None
        else _array(query_translation, (3,), "query translation")
    )
    if (
        not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-10, rtol=0)
        or np.linalg.det(rotation) < 0
    ):
        raise ValueError("volume query rotation must be right-handed orthonormal")
    if (
        xyz.ndim != 2
        or xyz.shape[1] != 3
        or directions.shape != xyz.shape
        or area_weights.shape != (len(xyz),)
    ):
        raise ValueError("observations require aligned points, normals and weights")
    controls, surface = saved["resolved"], saved["surface"]
    inward, outward, padding = (
        float(controls[name])
        for name in ("inward_thickness", "outward_thickness", "edge_padding")
    )
    patch = np.asarray(saved["patch_vertices"])
    patch_normals = np.asarray(saved["patch_normals"])
    cells = patch if surface is None else np.asarray(saved["footprint"])
    tolerance = (
        64
        * np.finfo(float).eps
        * max(float(controls["spacing"]), float(np.max(np.abs(patch))))
    )
    index = TriangleIndex(
        cells,
        (max(inward, outward) + padding if surface is None else padding) + tolerance,
        cancelled,
    )
    source_angles = None
    if surface is not None and surface["kind"] == "cone" and surface["slope"] != 0:
        _, _, _, source_chart = _project(patch.reshape(-1, 3), surface)
        source_angles = source_chart[:, 0].reshape(-1, 3)
        source_angles[:] = (
            source_angles[:, :1]
            + (source_angles - source_angles[:, :1] + np.pi) % (2 * np.pi)
            - np.pi
        )
    cosine = np.cos(np.radians(controls["normal_angle_degrees"]))
    accepted: list[int] = []
    for start in range(0, len(xyz), size):
        check_cancelled(cancelled)
        query = xyz[start : start + size]
        query_normals = directions[start : start + size]
        if query_rotation is not None or query_translation is not None:
            with np.errstate(invalid="ignore", over="ignore"):
                query = query @ rotation + translation
                query_normals = query_normals @ rotation
        lengths = np.linalg.norm(query_normals, axis=1)
        valid = (
            np.isfinite(query).all(axis=1)
            & np.isfinite(query_normals).all(axis=1)
            & np.isfinite(lengths)
            & (lengths > 0)
            & np.isfinite(area_weights[start : start + size])
            & (area_weights[start : start + size] > 0)
        )
        if surface is None:
            unit = np.zeros_like(query_normals)
            _ = np.divide(
                query_normals, lengths[:, None], out=unit, where=valid[:, None]
            )
            ids = _mesh_query(
                query,
                unit,
                valid,
                cells,
                patch_normals,
                index,
                inward,
                outward,
                padding,
                tolerance,
                cosine,
                cancelled,
            )
            if max_selected is not None and len(accepted) + len(ids) > max_selected:
                raise ValueError(
                    "Placed volume exceeds its selection membership budget"
                )
            accepted.extend((ids + start).tolist())
            continue
        with np.errstate(invalid="ignore", over="ignore"):
            _, expected, residual, chart = _project(query, surface)
        residual *= saved["normal_sign"]
        # These are the same necessary tests as the scalar narrow phase. Do
        # them together so distant analytic observations never enter its loop.
        valid &= (
            np.isfinite(chart).all(axis=1)
            & (residual >= -inward - tolerance)
            & (residual <= outward + tolerance)
        )
        usable_ids = np.flatnonzero(valid)
        for local_id in usable_ids:
            check_cancelled(cancelled)
            direction = query_normals[local_id] / lengths[local_id]
            if direction @ expected[local_id] * saved["normal_sign"] < cosine - 1e-14:
                continue
            alternatives = [chart[local_id].copy()]
            if surface["kind"] != "plane":
                alternatives += [
                    chart[local_id] + [shift, 0] for shift in (-2 * np.pi, 2 * np.pi)
                ]
            found = False
            for coordinates in alternatives:
                location = _develop(coordinates[None], surface)[0]
                for candidates in index.candidates(location):
                    closest, _ = _closest(location, cells[candidates])
                    same_sheet = np.ones(len(candidates), dtype=bool)
                    if source_angles is not None and surface["slope"] != 0:
                        factor = abs(surface["slope"]) / np.hypot(1, surface["slope"])
                        angle = np.arctan2(
                            factor * closest[:, 0],
                            factor * closest[:, 1] + surface["radius"],
                        )
                        center = source_angles[candidates].mean(axis=1)
                        delta = angle - factor * center
                        support_theta = (
                            center + np.arctan2(np.sin(delta), np.cos(delta)) / factor
                        )
                        same_sheet = (
                            np.abs(coordinates[0] - support_theta) <= np.pi + 1e-12
                        )
                    if np.any(
                        same_sheet
                        & (
                            np.linalg.norm(closest - location, axis=1)
                            <= padding + tolerance
                        )
                    ):
                        found = True
                        break
                if found:
                    break
            if found:
                if max_selected is not None and len(accepted) >= max_selected:
                    raise ValueError(
                        "Placed volume exceeds its selection membership budget"
                    )
                accepted.append(start + int(local_id))
    return accepted


def selection_volume_coverage(
    points: object,
    normals: object,
    triangles: object,
    volume: object,
    *,
    chunk_size: int = 1024,
    max_samples: int = 4096,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Estimate supported original patch area using target triangles, not counts.

    Use three fixed barycentric points per saved triangle when under budget;
    otherwise sample equal-area strata of the source patch with the same
    barycentric rule. Small gaps can be missed; no guaranteed error bound.
    Coverage uses original unpadded support and normal-segment intersections.
    """
    check_cancelled(cancelled)
    saved = validate_selection_volume(volume)
    size = _chunk_size(chunk_size)
    if type(max_samples) is not int or not 1 <= max_samples <= 65_536:
        raise ValueError("coverage sample budget must be an integer in [1, 65536]")
    xyz = _array(points, (None, 3), "coverage points")
    unit_normals = _normals(normals, len(xyz), "coverage normals")
    faces = _triangles(triangles, len(xyz))
    patch, patch_normals = (
        np.asarray(saved["patch_vertices"]),
        np.asarray(saved["patch_normals"]),
    )
    areas = (
        np.linalg.norm(
            np.cross(patch[:, 1] - patch[:, 0], patch[:, 2] - patch[:, 0]), axis=1
        )
        / 2
    )
    total = float(areas.sum())
    available = len(patch) * 3
    sample_count = min(available, max_samples)
    strategy = (
        "three-point-per-cell" if available <= max_samples else "stratified-patch-area"
    )
    diagnostics = {
        "algorithm": "saved-patch-area-quadrature-v1",
        "total_area": total,
        "samples": sample_count,
        "available_samples": available,
        "sampling_strategy": strategy,
    }
    controls = saved["resolved"]
    distance = max(controls["inward_thickness"], controls["outward_thickness"])
    tolerance = (
        64
        * np.finfo(float).eps
        * max(controls["spacing"], float(np.max(np.abs(patch))))
    )
    if not len(faces):
        return {
            **diagnostics,
            "fraction": 0.0,
            "covered_area": 0.0,
            "partial": True,
        }
    target = xyz[faces]
    good = (
        np.linalg.norm(
            np.cross(target[:, 1] - target[:, 0], target[:, 2] - target[:, 0]), axis=1
        )
        > 0
    )
    target, target_normals = target[good], unit_normals[faces[good]]
    if not len(target):
        return {
            **diagnostics,
            "fraction": 0.0,
            "covered_area": 0.0,
            "partial": True,
        }
    index = TriangleIndex(target, distance + tolerance, cancelled)
    cosine = np.cos(np.radians(controls["normal_angle_degrees"]))
    rule = np.array(
        [[2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]]
    )
    if available <= max_samples:
        sample_cells = np.repeat(np.arange(len(patch)), 3)
        sample_areas = np.repeat(areas / 3, 3)
    else:
        strata = (np.arange(sample_count) + 0.5) * (total / sample_count)
        sample_cells = np.searchsorted(np.cumsum(areas), strata)
        sample_areas = np.full(sample_count, total / sample_count)
    covered = 0.0
    for start in range(0, sample_count, size):
        check_cancelled(cancelled)
        for sample in range(start, min(start + size, sample_count)):
            check_cancelled(cancelled)
            cell_id = sample_cells[sample]
            barycentric = rule[sample % 3]
            point = barycentric @ patch[cell_id]
            normal = barycentric @ patch_normals[cell_id]
            normal /= np.linalg.norm(normal)
            supported = False
            for candidates in index.candidates(point):
                candidate_cells = target[candidates]
                face_normal = np.cross(
                    candidate_cells[:, 1] - candidate_cells[:, 0],
                    candidate_cells[:, 2] - candidate_cells[:, 0],
                )
                face_normal /= np.linalg.norm(face_normal, axis=1)[:, None]
                denominator = face_normal @ normal
                nonparallel = np.abs(denominator) > 1e-12
                signed = np.sum(
                    (candidate_cells[:, 0] - point) * face_normal, axis=1
                ) / np.where(nonparallel, denominator, 1)
                intersection = point + signed[:, None] * normal
                closest, bary = _closest(intersection, candidate_cells)
                misses = np.linalg.norm(closest - intersection, axis=1)
                target_direction = np.sum(
                    target_normals[candidates] * bary[:, :, None], axis=1
                )
                target_direction /= np.linalg.norm(target_direction, axis=1)[:, None]
                if np.any(
                    nonparallel
                    & (signed >= -controls["inward_thickness"] - tolerance)
                    & (signed <= controls["outward_thickness"] + tolerance)
                    & (misses <= tolerance)
                    & (target_direction @ normal >= cosine - 1e-14)
                ):
                    supported = True
                    break
            if supported:
                covered += float(sample_areas[sample])
    fraction = float(np.clip(covered / total, 0, 1))
    return {
        **diagnostics,
        "fraction": fraction,
        "covered_area": min(covered, total),
        "partial": fraction < 1 - 1e-10,
    }
