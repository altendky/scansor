"""Physical-scale normal dispersion on a bounded, cut surface graph.

The screened averaging kernel has a characteristic surface distance, rather
than a hard geodesic-ball cutoff. Geometry and source normals are never edited.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from experiments.fit_solver import FitCancelled

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int32]
BoolArray = NDArray[np.bool_]
Cancel = Callable[[], bool] | None


class _Limits(Protocol):
    @property
    def max_auxiliary_bytes(self) -> int: ...

    @property
    def chunk_size(self) -> int: ...


class SurfaceSelectionTopology(Protocol):
    """Read-only structural view avoids an import cycle with the caller."""

    @property
    def points(self) -> FloatArray: ...

    @property
    def triangles(self) -> IntArray: ...

    @property
    def normals(self) -> FloatArray: ...

    @property
    def areas(self) -> FloatArray: ...

    @property
    def valid(self) -> BoolArray: ...

    @property
    def neighbors(self) -> IntArray: ...

    @property
    def cuts(self) -> NDArray[np.uint8]: ...

    @property
    def halfedge_edges(self) -> IntArray: ...

    @property
    def edges(self) -> IntArray: ...

    @property
    def lengths(self) -> FloatArray: ...

    @property
    def limits(self) -> _Limits: ...


POLICY = "local-normal-dispersion-v1"
_MAX_ITERATIONS = 256
_NORMAL_TOLERANCE = 1e-7
_MAX_SAMPLES = 65_536
_MAX_CHUNK = 16_384


def _check(cancelled: Cancel) -> None:
    if cancelled is not None and cancelled():
        raise FitCancelled("Surface selection cancelled")


def _retained_bytes(topology: SurfaceSelectionTopology, neighbors: IntArray) -> int:
    arrays = [
        topology.points,
        topology.triangles,
        topology.normals,
        topology.areas,
        topology.valid,
        topology.neighbors,
        topology.cuts,
        topology.halfedge_edges,
        topology.edges,
        topology.lengths,
        neighbors,
    ]
    distinct: list[np.ndarray[Any, Any]] = []
    for value in arrays:
        if not any(np.shares_memory(value, old) for old in distinct):
            distinct.append(value)
    return sum(value.nbytes for value in distinct)


def _sample_median(
    values: FloatArray,
    count: int,
    selected_faces: BoolArray | None,
    chunk: int,
    cancelled: Cancel,
) -> float:
    """Evenly sample the complete positive stream, without a large gather."""
    positions = np.linspace(0, count - 1, min(count, _MAX_SAMPLES), dtype=np.int64)
    samples = np.empty(len(positions), dtype=np.float64)
    offset = written = 0
    for start in range(0, len(values), chunk):
        _check(cancelled)
        block = values[start : start + chunk]
        if selected_faces is not None:
            block = block[selected_faces[start : start + len(block)]]
        positive = block[block > 0]
        stop = int(np.searchsorted(positions, offset + len(positive)))
        if stop > written:
            samples[written:stop] = positive[positions[written:stop] - offset]
            written = stop
        offset += len(positive)
    if written != len(samples):
        raise ValueError("local boundary neighborhood sample is inconsistent")
    return float(np.median(samples))


def local_boundary_eligibility(
    topology: SurfaceSelectionTopology,
    neighbors: IntArray,
    seeds: IntArray,
    angle_degrees: float,
    neighborhood_distance: float | None,
    cancelled: Cancel,
) -> tuple[BoolArray, dict[str, Any]]:
    """Return intrinsic passability; callers still exclude barrier faces.

    For unit normals, ``1 - |mean(normal)|²`` is the local vector variance.
    ``2 acos(|mean(normal)|)`` equals the angle between two equally weighted
    planar sides of a crease. Averaging solves ``(area + r²/4 L)m = area*n``.
    Positive symmetric conductances use paths through shared edge midpoints.
    This allows gradual curvature without a seed direction or fixed hop count.
    """
    _check(cancelled)
    if (
        isinstance(angle_degrees, bool)
        or not np.isfinite(angle_degrees)
        or not 0 < angle_degrees <= 90
        or (
            neighborhood_distance is not None
            and (
                isinstance(neighborhood_distance, bool)
                or not np.isfinite(neighborhood_distance)
                or neighborhood_distance <= 0
            )
        )
    ):
        raise ValueError("local boundary angle must be in (0,90] and distance positive")
    count = len(topology.triangles)
    if neighbors.shape != (count, 3) or neighbors.dtype != np.dtype(np.int32):
        raise ValueError("local boundary neighbors must be an int32 face graph")
    chunk = min(topology.limits.chunk_size, _MAX_CHUNK)
    # Eight scalar float64 work vectors and three float64 edge weights per face.
    # Include caller-side masks, bounded geometry temporaries and median samples.
    # Topology's build/sort peak has retired; do not add that peak a second time.
    estimated = (
        _retained_bytes(topology, neighbors)
        + 88 * count
        + 8 * len(topology.points)
        + 8 * count
        + seeds.nbytes
        + 1024 * min(chunk, count)
        + 24 * _MAX_SAMPLES
    )
    if estimated > topology.limits.max_auxiliary_bytes:
        raise ValueError(
            "local boundary averaging exceeds its declared resource limits"
        )
    diagnostics: dict[str, Any] = {
        "policy": POLICY,
        "normal_criterion": "screened-surface-normal-dispersion",
        "distance_metric": "surface-dual-path-characteristic-rms",
        "neighborhood_source": "manual"
        if neighborhood_distance is not None
        else "auto-seed-face-dual-edge-median",
        "requested_neighborhood_distance": neighborhood_distance,
        "estimated_auxiliary_bytes": estimated,
        "solver": "bounded-jacobi-pcg",
        "max_iterations": _MAX_ITERATIONS,
        "normal_error_tolerance": _NORMAL_TOLERANCE,
        "iterations": [],
    }
    weights = np.zeros((count, 3), dtype=np.float64)
    seed_mask = np.zeros(len(topology.points), dtype=bool)
    seed_mask[seeds] = True
    seed_faces = np.zeros(count, dtype=bool)
    seed_edges = all_edges = 0
    for start in range(0, count, chunk):
        _check(cancelled)
        stop = min(start + chunk, count)
        rows = topology.triangles[start:stop]
        near = neighbors[start:stop]
        if np.any(near < -1) or np.any(near >= count):
            raise ValueError("local boundary neighbor is out of range")
        if np.any((near >= 0) & (near != topology.neighbors[start:stop])):
            raise ValueError("local boundary neighbors must retain source topology")
        selected = seed_mask[rows].any(axis=1) & topology.valid[start:stop]
        seed_faces[start:stop] = selected
        corners = topology.points[rows]
        center = corners.mean(axis=1)
        for slot in range(3):
            linked = near[:, slot] >= 0
            if not linked.any():
                continue
            current = np.arange(start, stop, dtype=np.int32)[linked]
            other = near[linked, slot]
            if not np.all(np.any(neighbors[other] == current[:, None], axis=1)):
                raise ValueError("local boundary neighbors must be symmetric")
            if not np.all(topology.valid[current] & topology.valid[other]):
                raise ValueError("local boundary neighbors must join valid faces")
            midpoint = (corners[linked, slot] + corners[linked, (slot + 1) % 3]) / 2
            other_center = topology.points[topology.triangles[other]].mean(axis=1)
            distance = np.linalg.norm(center[linked] - midpoint, axis=1)
            distance += np.linalg.norm(other_center - midpoint, axis=1)
            if not np.isfinite(distance).all() or np.any(distance <= 0):
                raise ValueError("local boundary surface distances must be positive")
            weights[start:stop, slot][linked] = distance
            all_edges += len(distance)
            seed_edges += int(selected[linked].sum())
    if neighborhood_distance is None:
        if seed_edges:
            neighborhood_distance = _sample_median(
                weights, seed_edges, seed_faces, chunk, cancelled
            )
        elif all_edges:
            neighborhood_distance = _sample_median(
                weights, all_edges, None, chunk, cancelled
            )
            diagnostics["neighborhood_source"] = "auto-source-dual-edge-median"
        elif len(topology.lengths) and np.any(topology.lengths > 0):
            neighborhood_distance = _sample_median(
                topology.lengths[:, None],
                int(np.count_nonzero(topology.lengths > 0)),
                None,
                chunk,
                cancelled,
            )
            diagnostics["neighborhood_source"] = "auto-source-edge-median"
        else:
            diagnostics.update(
                neighborhood_source="unresolved-no-surface-support",
                neighborhood_distance=None,
                resolved_neighborhood_distance=None,
                max_normal_dispersion_degrees=0.0,
                iterations=[0, 0, 0],
            )
            return topology.valid.copy(), diagnostics
    del seed_mask, seed_faces
    distance = float(neighborhood_distance)
    diffusion_time = distance * distance / 4
    if not np.isfinite(diffusion_time) or diffusion_time <= 0:
        raise ValueError("local boundary distance is outside the numerical range")
    diagnostics["neighborhood_distance"] = distance
    diagnostics["resolved_neighborhood_distance"] = distance
    # Convert the temporary path distances into symmetric finite-volume weights.
    for start in range(0, count, chunk):
        _check(cancelled)
        stop = min(start + chunk, count)
        corners = topology.points[topology.triangles[start:stop]]
        block = weights[start:stop]
        for slot in range(3):
            edge_length = np.linalg.norm(
                corners[:, slot] - corners[:, (slot + 1) % 3], axis=1
            )
            _ = np.divide(
                edge_length,
                block[:, slot],
                out=block[:, slot],
                where=block[:, slot] > 0,
            )
    diagonal = topology.areas + diffusion_time * weights.sum(axis=1)
    diagonal[~topology.valid] = 1
    if not np.isfinite(diagonal).all() or np.any(diagonal <= 0):
        raise ValueError(
            "local boundary averaging coefficients are not finite/positive"
        )
    squared = np.zeros(count, dtype=np.float64)
    x, residual, z, direction, applied, scratch = (
        np.empty(count, dtype=np.float64) for _ in range(6)
    )

    def apply(value: FloatArray, output: FloatArray) -> None:
        for start in range(0, count, chunk):
            _check(cancelled)
            stop = min(start + chunk, count)
            near = neighbors[start:stop]
            delta = value[start:stop, None] - value[np.maximum(near, 0)]
            delta *= weights[start:stop]
            output[start:stop] = topology.areas[start:stop] * value[
                start:stop
            ] + diffusion_time * delta.sum(axis=1)
            output[start:stop][~topology.valid[start:stop]] = value[start:stop][
                ~topology.valid[start:stop]
            ]

    def normal_error_bound(value: FloatArray) -> float:
        # A = M + tL is an M-matrix with A*1 = M*1. Thus max |r_i|/M_i
        # bounds the maximum normal-component error independently of face area
        # or disconnected components. A global RHS norm does not give that bound.
        largest = 0.0
        for start in range(0, count, chunk):
            _check(cancelled)
            stop = min(start + chunk, count)
            active = topology.valid[start:stop]
            if not active.any():
                continue
            scaled = np.abs(value[start:stop][active])
            scaled /= topology.areas[start:stop][active]
            maximum = float(scaled.max())
            if not np.isfinite(maximum):
                raise ValueError("local boundary averaging has a nonfinite residual")
            largest = max(largest, maximum)
        return largest

    iterations: list[int] = []
    relative_residuals: list[float] = []
    normal_error_bounds: list[float] = []
    for channel in range(3):
        _check(cancelled)
        x[:] = topology.normals[:, channel]
        _ = np.multiply(topology.areas, x, out=residual)
        rhs_norm = float(np.linalg.norm(residual))
        apply(x, applied)
        residual -= applied
        _ = np.divide(residual, diagonal, out=z)
        direction[:] = z
        rz = float(residual @ z)
        iteration = 0
        while normal_error_bound(residual) > _NORMAL_TOLERANCE / 4:
            _check(cancelled)
            if iteration >= _MAX_ITERATIONS:
                raise ValueError(
                    "local boundary averaging did not converge within its iteration budget; choose a smaller neighborhood distance"
                )
            apply(direction, applied)
            curvature = float(direction @ applied)
            if not np.isfinite(curvature) or curvature <= 0 or not np.isfinite(rz):
                raise ValueError(
                    "local boundary averaging lost positive numerical support"
                )
            alpha = rz / curvature
            _ = np.multiply(direction, alpha, out=scratch)
            x += scratch
            _ = np.multiply(applied, alpha, out=scratch)
            residual -= scratch
            _ = np.divide(residual, diagonal, out=z)
            next_rz = float(residual @ z)
            direction *= next_rz / rz
            direction += z
            rz = next_rz
            iteration += 1
        # Reject a misleading recursive residual rather than publishing a partial solve.
        apply(x, applied)
        _ = np.multiply(topology.areas, topology.normals[:, channel], out=scratch)
        scratch -= applied
        true_norm = float(np.linalg.norm(scratch))
        true_bound = normal_error_bound(scratch)
        if true_bound > _NORMAL_TOLERANCE:
            raise ValueError(
                "local boundary averaging did not reach its residual tolerance"
            )
        _ = np.multiply(x, x, out=scratch)
        squared += scratch
        iterations.append(iteration)
        relative_residuals.append(true_norm / rhs_norm if rhs_norm else 0.0)
        normal_error_bounds.append(true_bound)
    _check(cancelled)
    if not np.isfinite(squared).all() or np.any(squared > 1 + 1e-4):
        raise ValueError("local boundary normal averaging produced invalid dispersion")
    threshold = float(np.cos(np.radians(angle_degrees) / 2)) ** 2
    eligible = topology.valid & (squared >= threshold)
    smallest = float(np.min(squared[topology.valid])) if topology.valid.any() else 1.0
    diagnostics.update(
        iterations=iterations,
        relative_residuals=relative_residuals,
        max_normal_residuals=normal_error_bounds,
        max_normal_dispersion_degrees=float(
            np.degrees(2 * np.arccos(np.sqrt(np.clip(smallest, 0, 1))))
        ),
    )
    return eligible, diagnostics
