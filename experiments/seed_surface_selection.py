"""Bounded seed-normal envelopes and mesh-edge setback; no positional fitting.

Independent seed patches never relay growth through another patch's envelope.
Face adjacency preserves cuts at creases, winding defects and nonmanifold edges.
Setback measures shortest edge paths, not continuous geodesics. Requiring whole
surviving faces adds a conservative, tessellation-dependent boundary band.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.fit_solver import FitCancelled

POLICY = "seed-normal-envelope-v1"
LOCAL_POLICY = "local-boundaries-v1"
MAX_PREVIEW_IDS = 2048
FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int32]
BoolArray = NDArray[np.bool_]
Cancel = Callable[[], bool] | None


@dataclass(frozen=True)
class SurfaceSelectionLimits:
    max_vertices: int = 1_500_000
    max_faces: int = 2_500_000
    max_auxiliary_bytes: int = 512 * 1024 * 1024
    max_patches: int = 32
    chunk_size: int = 16_384

    def __post_init__(self) -> None:
        for value in (
            self.max_vertices,
            self.max_faces,
            self.max_auxiliary_bytes,
            self.max_patches,
            self.chunk_size,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("surface-selection limits must be positive integers")
        if self.max_patches > 32:
            raise ValueError("at most 32 independent seed patches are supported")


def _check(cancelled: Cancel) -> None:
    if cancelled is not None and cancelled():
        raise FitCancelled("Surface selection cancelled")


def _readonly[T: np.generic](value: NDArray[T]) -> NDArray[T]:
    value.flags.writeable = False
    return value


@dataclass(frozen=True)
class SurfaceSelectionTopology:
    points: FloatArray
    triangles: IntArray
    normals: FloatArray
    areas: FloatArray
    valid: BoolArray
    neighbors: IntArray
    # 0=manifold, 1=open, 2=winding defect, 3=nonmanifold, 4=invalid face.
    cuts: NDArray[np.uint8]
    halfedge_edges: IntArray
    edges: IntArray
    lengths: FloatArray
    limits: SurfaceSelectionLimits
    estimated_auxiliary_bytes: int


def build_surface_topology(
    points: FloatArray,
    triangles: NDArray[np.signedinteger[Any]],
    *,
    limits: SurfaceSelectionLimits | None = None,
    cancelled: Cancel = None,
) -> SurfaceSelectionTopology:
    """Cache once per immutable workspace, dropping the cache with its owner.

    The declared estimate includes geometry snapshots, sort work, topology and
    one reusable query's arrays/queue. It is not a process-memory measurement.
    NumPy's bounded in-place edge sort checks cancellation before and after.
    """
    _check(cancelled)
    limits = limits or SurfaceSelectionLimits()
    if points.ndim != 2 or points.shape[1] != 3:
        raise ValueError("surface points must have shape (vertices, 3)")
    if (
        triangles.ndim != 2
        or triangles.shape[1] != 3
        or triangles.dtype.kind not in "iu"
    ):
        raise ValueError("surface connectivity must contain integer triangles")
    count, faces = len(points), len(triangles)
    estimate = 192 * faces + 96 * count + 8 * 1024 * 1024
    if (
        count > limits.max_vertices
        or faces > limits.max_faces
        or estimate > limits.max_auxiliary_bytes
    ):
        raise ValueError(
            "surface-selection topology exceeds its declared resource limits"
        )
    for start in range(0, max(count, faces), limits.chunk_size):
        _check(cancelled)
        if not np.isfinite(points[start : start + limits.chunk_size]).all():
            raise ValueError("surface coordinates must be finite")
        row = triangles[start : start + limits.chunk_size]
        if np.any(row < 0) or np.any(row >= count):
            raise ValueError("surface triangle index is out of range")
    xyz = np.array(points, dtype=np.float64, copy=True)
    tri = np.array(triangles, dtype=np.int32, copy=True)
    normals = np.zeros((faces, 3), dtype=np.float64)
    areas = np.zeros(faces, dtype=np.float64)
    for start in range(0, faces, limits.chunk_size):
        _check(cancelled)
        rows = tri[start : start + limits.chunk_size]
        corners = xyz[rows]
        with np.errstate(over="ignore", invalid="ignore"):
            vectors = np.cross(
                corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
            )
            lengths = np.linalg.norm(vectors, axis=1)
        if not np.isfinite(lengths).all():
            raise ValueError("surface triangle area is nonfinite")
        areas[start : start + len(rows)] = lengths / 2
        normals[start : start + len(rows)] = (
            vectors / np.where(lengths > 0, lengths, 1)[:, None]
        )
    valid = areas > 0
    records = np.empty(
        3 * int(valid.sum()), dtype=[("key", "<u8"), ("half", "<u4"), ("forward", "?")]
    )
    offset = 0
    for start in range(0, faces, limits.chunk_size):
        _check(cancelled)
        ids = np.flatnonzero(valid[start : start + limits.chunk_size]) + start
        rows = tri[ids]
        left, right = rows.ravel(), rows[:, [1, 2, 0]].ravel()
        size = len(left)
        records["key"][offset : offset + size] = (
            np.minimum(left, right).astype(np.uint64) << np.uint64(32)
        ) | np.maximum(left, right).astype(np.uint64)
        records["half"][offset : offset + size] = (
            ids[:, None] * 3 + np.arange(3)
        ).ravel()
        records["forward"][offset : offset + size] = left < right
        offset += size
    _check(cancelled)
    records.sort(order=["key", "half"], kind="quicksort")
    _check(cancelled)
    starts = (
        np.r_[0, np.flatnonzero(records["key"][1:] != records["key"][:-1]) + 1]
        if len(records)
        else np.empty(0, dtype=np.int64)
    )
    sizes = np.diff(np.r_[starts, len(records)])
    estimate = max(
        estimate,
        111 * faces + 32 * len(starts) + 24 * count + 8 * 1024 * 1024,
        181 * faces + 16 * len(starts) + 64 * count + 8 * 1024 * 1024,
        84 * faces + 80 * len(starts) + 64 * count + 8 * 1024 * 1024,
    )
    if estimate > limits.max_auxiliary_bytes:
        raise ValueError(
            "surface-selection edge work exceeds its declared resource limits"
        )
    neighbors = np.full((faces, 3), -1, dtype=np.int32)
    cuts = np.full((faces, 3), 4, dtype=np.uint8)
    cuts[valid] = 1
    halfedge_edges = np.full((faces, 3), -1, dtype=np.int32)
    edges = np.empty((len(starts), 2), dtype=np.int32)
    lengths = np.empty(len(starts), dtype=np.float64)
    for start in range(0, len(starts), limits.chunk_size):
        _check(cancelled)
        slots = starts[start : start + limits.chunk_size]
        counts = sizes[start : start + limits.chunk_size]
        keys = records["key"][slots]
        endpoints = np.column_stack(
            (keys >> np.uint64(32), keys & np.uint64(0xFFFFFFFF))
        ).astype(np.int32)
        edges[start : start + len(slots)] = endpoints
        lengths[start : start + len(slots)] = np.linalg.norm(
            xyz[endpoints[:, 0]] - xyz[endpoints[:, 1]], axis=1
        )
        if not np.isfinite(lengths[start : start + len(slots)]).all():
            raise ValueError("surface edge length is nonfinite")
        first, last = int(slots[0]), int(slots[-1] + counts[-1])
        edge_ids = np.repeat(
            np.arange(start, start + len(slots), dtype=np.int32), counts
        )
        halves = records["half"][first:last]
        halfedge_edges.ravel()[halves] = edge_ids
        cuts.ravel()[halves] = np.repeat(
            np.where(counts > 2, 3, 1).astype(np.uint8), counts
        )
        pairs = slots[counts == 2]
        a, b = records["half"][pairs], records["half"][pairs + 1]
        opposite = records["forward"][pairs] != records["forward"][pairs + 1]
        neighbors.ravel()[a] = b // 3
        neighbors.ravel()[b] = a // 3
        cuts.ravel()[a] = cuts.ravel()[b] = np.where(opposite, 0, 2)
    _check(cancelled)
    return SurfaceSelectionTopology(
        _readonly(xyz),
        _readonly(tri),
        _readonly(normals),
        _readonly(areas),
        _readonly(valid),
        _readonly(neighbors),
        _readonly(cuts),
        _readonly(halfedge_edges),
        _readonly(edges),
        _readonly(lengths),
        limits,
        estimate,
    )


@dataclass(frozen=True)
class SurfaceSelectionResult:
    ids: IntArray
    region_ids: IntArray
    boundary_ids: IntArray
    seed_ids: IntArray
    barrier_ids: IntArray
    protected_ids: IntArray
    diagnostics: dict[str, Any]

    def preview(self, max_ids: int = MAX_PREVIEW_IDS) -> dict[str, Any]:
        if type(max_ids) is not int or not 1 <= max_ids <= MAX_PREVIEW_IDS:
            raise ValueError("preview sample cap must be in 1..2048")
        phases = {
            "seeds": self.seed_ids,
            "region": self.region_ids,
            "boundaries": self.boundary_ids,
            "barriers": self.barrier_ids,
            "inset": self.ids,
        }
        return {
            "policy": self.diagnostics["policy"],
            "counts": {
                **{key: len(value) for key, value in phases.items()},
                "protected_seeds": len(self.protected_ids),
                "patches": self.diagnostics["patches"],
            },
            "samples": {
                key: value[
                    np.linspace(
                        0, len(value) - 1, min(len(value), max_ids), dtype=np.int64
                    )
                ].tolist()
                if len(value)
                else []
                for key, value in phases.items()
            },
            "diagnostics": self.diagnostics,
        }


def _ids(values: Sequence[int] | IntArray, count: int, name: str) -> IntArray:
    if not isinstance(values, np.ndarray) and any(
        isinstance(value, (bool, np.bool_)) for value in values
    ):
        raise ValueError(f"{name} must contain source vertex integers, not booleans")
    raw = np.asarray(values)
    if raw.ndim != 1 or (len(raw) and raw.dtype.kind not in "iu"):
        raise ValueError(f"{name} must contain source vertex integers")
    if len(raw) and (np.any(raw < 0) or np.any(raw >= count)):
        raise ValueError(f"{name} vertex is out of range")
    return np.unique(raw).astype(np.int32)


def _flood(
    neighbors: IntArray, eligible: BoolArray, roots: IntArray, cancelled: Cancel
) -> BoolArray:
    reached = np.zeros(len(eligible), dtype=bool)
    roots = roots[eligible[roots]]
    queue = np.empty(len(eligible), dtype=np.int32)
    queue[: len(roots)] = roots
    reached[roots] = True
    head, tail = 0, len(roots)
    while head < tail:
        if head % 256 == 0:
            _check(cancelled)
        face = int(queue[head])
        head += 1
        for other in neighbors[face]:
            vertex = int(other)
            if vertex >= 0 and eligible[vertex] and not reached[vertex]:
                reached[vertex] = True
                queue[tail] = vertex
                tail += 1
    return reached


def _patches(
    topology: SurfaceSelectionTopology,
    seeds: IntArray,
    neighbors: IntArray,
    blocked: BoolArray,
    cancelled: Cancel,
) -> tuple[list[IntArray], IntArray, IntArray]:
    """Only seed-local lists are materialized, not a Python set per source vertex."""
    tri, count = topology.triangles, len(topology.points)
    seed_mask = np.zeros(count, dtype=bool)
    seed_mask[seeds] = True
    vertex_rows: list[IntArray] = []
    face_rows: list[IntArray] = []
    for start in range(0, len(tri), topology.limits.chunk_size):
        _check(cancelled)
        rows = tri[start : start + topology.limits.chunk_size]
        selected = (
            seed_mask[rows]
            & (
                topology.valid[start : start + len(rows)]
                & ~blocked[start : start + len(rows)]
            )[:, None]
        )
        vertex_rows.append(rows[selected])
        face_rows.append(
            np.broadcast_to(
                np.arange(start, start + len(rows), dtype=np.int32)[:, None], rows.shape
            )[selected]
        )
    vertices = (
        np.concatenate(vertex_rows) if vertex_rows else np.empty(0, dtype=np.int32)
    )
    faces = np.concatenate(face_rows) if face_rows else np.empty(0, dtype=np.int32)
    del vertex_rows, face_rows
    order = np.argsort(vertices, kind="stable")
    vertices, faces = vertices[order], faces[order]
    roots = np.zeros(count, dtype=bool)
    ambiguous: list[int] = []
    unsupported: list[int] = []
    starts = np.searchsorted(vertices, seeds)
    stops = np.searchsorted(vertices, seeds, side="right")
    for index, vertex in enumerate(seeds):
        if index % 256 == 0:
            _check(cancelled)
        incident = faces[starts[index] : stops[index]]
        if not len(incident):
            unsupported.append(int(vertex))
            continue
        seen = np.zeros(len(incident), dtype=bool)
        seen[0] = True
        pending = [0]
        visited = 0
        while pending:
            if visited % 256 == 0:
                _check(cancelled)
            visited += 1
            row = pending.pop()
            face = int(incident[row])
            for slot in range(3):
                if vertex not in (tri[face, slot], tri[face, (slot + 1) % 3]):
                    continue
                other = int(neighbors[face, slot])
                pos = int(np.searchsorted(incident, other))
                if (
                    other >= 0
                    and pos < len(incident)
                    and incident[pos] == other
                    and not seen[pos]
                ):
                    seen[pos] = True
                    pending.append(pos)
        if seen.all():
            roots[vertex] = True
        else:
            ambiguous.append(int(vertex))
    # Seed-only connectivity; patches keep distinct immutable envelopes.
    lookup = np.full(count, -1, dtype=np.int32)
    lookup[seeds] = np.arange(len(seeds), dtype=np.int32)
    parent = np.arange(len(seeds), dtype=np.int32)

    def find(index: int) -> int:
        while int(parent[index]) != index:
            parent[index] = parent[parent[index]]
            index = int(parent[index])
        return index

    for start in range(0, len(tri), topology.limits.chunk_size):
        _check(cancelled)
        rows = tri[start : start + topology.limits.chunk_size]
        selected = (
            topology.valid[start : start + len(rows)]
            & ~blocked[start : start + len(rows)]
        )
        for slot in range(3):
            left, right = rows[:, slot], rows[:, (slot + 1) % 3]
            mask = selected & roots[left] & roots[right]
            for a, b in zip(lookup[left[mask]], lookup[right[mask]], strict=True):
                ra, rb = find(int(a)), find(int(b))
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[int]] = {}
    for index, vertex in enumerate(seeds):
        if roots[vertex]:
            groups.setdefault(find(index), []).append(int(vertex))
            if len(groups) > topology.limits.max_patches:
                raise ValueError(
                    "surface selection exceeds 32 independent seed patches"
                )
    return (
        [np.asarray(value, dtype=np.int32) for _, value in sorted(groups.items())],
        np.asarray(ambiguous, dtype=np.int32),
        np.asarray(unsupported, dtype=np.int32),
    )


def _clearance(
    topology: SurfaceSelectionTopology,
    region: BoolArray,
    boundary: IntArray,
    setback: float,
    cancelled: Cancel,
) -> BoolArray:
    """Indexed heap has at most one entry per vertex; stop at the setback."""
    n = len(topology.points)
    allowed = np.ones(n, dtype=bool)
    if setback == 0 or not len(boundary):
        return allowed
    used = np.zeros(len(topology.edges), dtype=bool)
    for start in range(0, len(region), topology.limits.chunk_size):
        _check(cancelled)
        edge_ids = topology.halfedge_edges[start : start + topology.limits.chunk_size][
            region[start : start + topology.limits.chunk_size]
        ]
        used[edge_ids.ravel()] = True
    edges, weights = topology.edges[used], topology.lengths[used]
    left = np.concatenate((edges[:, 0], edges[:, 1]))
    right = np.concatenate((edges[:, 1], edges[:, 0]))
    del edges
    order = np.argsort(left, kind="stable")
    adjacent = right[order]
    del right
    offsets = np.r_[0, np.cumsum(np.bincount(left, minlength=n))]
    del left
    lengths = weights[order % len(weights)]
    del order, weights
    distance = np.full(n, np.inf)
    positions = np.full(n, -1, dtype=np.int32)
    heap = np.empty(n, dtype=np.int32)
    size = 0

    def before(a: int, b: int) -> bool:
        return distance[a] < distance[b] or (distance[a] == distance[b] and a < b)

    def push(vertex: int, value: float) -> None:
        nonlocal size
        distance[vertex] = value
        slot = int(positions[vertex])
        if slot < 0:
            slot = size
            size += 1
        while slot:
            parent = (slot - 1) // 2
            other = int(heap[parent])
            if not before(vertex, other):
                break
            heap[slot] = other
            positions[other] = slot
            slot = parent
        heap[slot] = vertex
        positions[vertex] = slot

    for index, vertex in enumerate(boundary):
        if index % 256 == 0:
            _check(cancelled)
        push(int(vertex), 0.0)
    iterations = 0
    while size:
        if iterations % 256 == 0:
            _check(cancelled)
        iterations += 1
        vertex = int(heap[0])
        size -= 1
        if size:
            replacement = int(heap[size])
            slot = 0
            while slot * 2 + 1 < size:
                child = slot * 2 + 1
                if child + 1 < size and before(int(heap[child + 1]), int(heap[child])):
                    child += 1
                other = int(heap[child])
                if not before(other, replacement):
                    break
                heap[slot] = other
                positions[other] = slot
                slot = child
            heap[slot] = replacement
            positions[replacement] = slot
        positions[vertex] = -2
        if distance[vertex] >= setback:
            break
        allowed[vertex] = False
        for edge in range(int(offsets[vertex]), int(offsets[vertex + 1])):
            other = int(adjacent[edge])
            value = float(distance[vertex] + lengths[edge])
            if positions[other] != -2 and value < setback and value < distance[other]:
                push(other, value)
    return allowed


def grow_surface_selection(
    topology: SurfaceSelectionTopology,
    seed_ids: Sequence[int] | IntArray,
    barrier_ids: Sequence[int] | IntArray,
    *,
    angle_degrees: float = 20.0,
    setback: float = 0.0,
    policy: str = POLICY,
    neighborhood_distance: float | None = None,
    cancelled: Cancel = None,
) -> SurfaceSelectionResult:
    _check(cancelled)
    if policy not in {POLICY, LOCAL_POLICY}:
        raise ValueError("unknown surface-selection policy")
    if neighborhood_distance is not None and (
        isinstance(neighborhood_distance, bool)
        or not np.isfinite(neighborhood_distance)
        or neighborhood_distance <= 0
    ):
        raise ValueError("surface neighborhood distance must be finite and positive")
    if (
        isinstance(angle_degrees, bool)
        or not np.isfinite(angle_degrees)
        or not 0 < angle_degrees <= 90
        or isinstance(setback, bool)
        or not np.isfinite(setback)
        or setback < 0
    ):
        raise ValueError(
            "surface angle must be in (0,90] and setback finite/nonnegative"
        )
    tri, count = topology.triangles, len(topology.points)
    seeds, barriers = _ids(seed_ids, count, "seed"), _ids(barrier_ids, count, "barrier")
    if not len(seeds):
        raise ValueError("surface selection needs seed vertices")
    if np.intersect1d(seeds, barriers).size:
        raise ValueError("seed overlaps a barrier selection")
    barrier_mask = np.zeros(count, dtype=bool)
    barrier_mask[barriers] = True
    blocked = np.zeros(len(tri), dtype=bool)
    neighbors = topology.neighbors.copy()
    cosine, sine = (
        float(np.cos(np.radians(angle_degrees))),
        float(np.sin(np.radians(angle_degrees))),
    )
    for start in range(0, len(tri), topology.limits.chunk_size):
        _check(cancelled)
        rows = tri[start : start + topology.limits.chunk_size]
        blocked[start : start + len(rows)] = barrier_mask[rows].any(axis=1)
        near = neighbors[start : start + len(rows)]
        dots = (
            np.sum(
                topology.normals[start : start + len(rows), None, :]
                * topology.normals[np.maximum(near, 0)],
                axis=2,
            )
            if len(tri)
            else np.empty((0, 3))
        )
        cut = topology.cuts[start : start + len(rows)] != 0
        if policy == POLICY:
            cut |= dots < cosine
        near[cut] = -1
    local_eligible: BoolArray | None = None
    local_diagnostics: dict[str, Any] | None = None
    if policy == LOCAL_POLICY:
        from experiments.local_surface_boundaries import local_boundary_eligibility

        for start in range(0, len(tri), topology.limits.chunk_size):
            _check(cancelled)
            near = neighbors[start : start + topology.limits.chunk_size]
            near[
                blocked[start : start + len(near), None] | blocked[np.maximum(near, 0)]
            ] = -1
        local_eligible, local_diagnostics = local_boundary_eligibility(
            topology, neighbors, seeds, angle_degrees, neighborhood_distance, cancelled
        )
        local_eligible &= topology.valid & ~blocked
    groups, ambiguous, unsupported = _patches(
        topology, seeds, neighbors, blocked, cancelled
    )
    region_vertices = np.zeros(count, dtype=bool)
    boundary_vertices = np.zeros(count, dtype=bool)
    final = np.zeros(count, dtype=bool)
    naturally_retained = np.zeros(count, dtype=bool)
    envelopes: list[dict[str, Any]] = []
    processing_groups = (
        [np.concatenate(groups)] if policy == LOCAL_POLICY and groups else groups
    )
    for patch, group in enumerate(processing_groups):
        _check(cancelled)
        group_mask = np.zeros(count, dtype=bool)
        group_mask[group] = True
        roots_mask = np.zeros(len(tri), dtype=bool)
        for start in range(0, len(tri), topology.limits.chunk_size):
            _check(cancelled)
            rows = tri[start : start + topology.limits.chunk_size]
            roots_mask[start : start + len(rows)] = group_mask[rows].any(axis=1)
        roots_mask &= topology.valid & ~blocked
        roots = np.flatnonzero(roots_mask).astype(np.int32)
        if policy == LOCAL_POLICY:
            assert local_eligible is not None
            eligible = local_eligible
            mode, reason = "local-boundaries", None
            eigenvalues, direction = np.zeros(3), np.zeros(3)
        else:
            n = topology.normals[roots]
            weights = topology.areas[roots]
            total_area = float(weights.sum())
            if not np.isfinite(total_area) or total_area <= 0:
                raise ValueError(
                    "seed face area accumulation is not finite and positive"
                )
            weights = weights / total_area
            eigenvalues, basis = np.linalg.eigh((n.T * weights) @ n)
            direction = basis[:, 0]
            mode, reason = "band", None
            if (
                eigenvalues[1] < np.sin(np.radians(3.0)) ** 2
                or eigenvalues[0] > 0.1 * eigenvalues[1]
            ):
                mode = "cone"
                reason = "narrow-or-nonplanar-seed-normals"
                direction = weights @ n
                length = float(np.linalg.norm(direction))
                if length <= 1e-12:
                    envelopes.append(
                        {
                            "patch": patch,
                            "mode": "protected-only",
                            "confidence": "unresolved",
                            "reason": "opposing-seed-normals",
                            "seed_vertices": len(group),
                        }
                    )
                    continue
                direction /= length
            if mode == "band" and direction[int(np.argmax(np.abs(direction)))] < 0:
                direction = -direction
            del n, weights
            eligible = topology.valid & ~blocked
            for start in range(0, len(tri), topology.limits.chunk_size):
                _check(cancelled)
                dots = (
                    topology.normals[start : start + topology.limits.chunk_size]
                    @ direction
                )
                eligible[start : start + len(dots)] &= (
                    np.abs(dots) <= sine if mode == "band" else dots >= cosine
                )
        region = _flood(neighbors, eligible, roots, cancelled)
        region_vertices[tri[region].ravel()] = True
        boundary_mask = region[:, None] & (
            (neighbors < 0) | ~region[np.maximum(neighbors, 0)]
        )
        boundaries = np.zeros(count, dtype=bool)
        for slot in range(3):
            faces = np.flatnonzero(boundary_mask[:, slot])
            boundaries[tri[faces, slot]] = True
            boundaries[tri[faces, (slot + 1) % 3]] = True
        boundary_vertices |= boundaries
        allowed = _clearance(
            topology,
            region,
            np.flatnonzero(boundaries).astype(np.int32),
            setback,
            cancelled,
        )
        surviving = region.copy()
        for start in range(0, len(tri), topology.limits.chunk_size):
            _check(cancelled)
            rows = tri[start : start + topology.limits.chunk_size]
            surviving[start : start + len(rows)] &= allowed[rows].all(axis=1)
        retained = _flood(neighbors, surviving, roots, cancelled)
        accepted = np.unique(tri[retained])
        final[accepted] = True
        naturally_retained[accepted] = True
        envelopes.append(
            {
                "patch": patch,
                "mode": mode,
                "confidence": "local-neighborhood"
                if mode == "local-boundaries"
                else "supported-band"
                if mode == "band"
                else "conservative-cone",
                "reason": reason,
                "seed_vertices": len(group),
                "seed_faces": len(roots),
                "direction": direction.tolist(),
                "eigenvalues": eigenvalues.tolist(),
                "normal_spread_degrees": float(
                    np.degrees(np.arcsin(np.sqrt(np.clip(eigenvalues[1], 0, 1))))
                ),
                "planarity_ratio": float(
                    max(float(eigenvalues[0]), 0.0)
                    / max(float(eigenvalues[1]), np.finfo(float).eps)
                ),
                "rejected_seed_faces": int((~eligible[roots]).sum()),
                "region_faces": int(region.sum()),
                "retained_faces": int(retained.sum()),
            }
        )
    final[seeds] = True
    final[barriers] = False
    protected = seeds[~naturally_retained[seeds]]
    _check(cancelled)
    diagnostics: dict[str, Any] = {
        "policy": policy,
        "patches": len(groups),
        "envelopes": envelopes,
        "ambiguous_seed_count": len(ambiguous),
        "ambiguous_seed_sample": ambiguous[:MAX_PREVIEW_IDS].tolist(),
        "unsupported_seed_count": len(unsupported),
        "unsupported_seed_sample": unsupported[:MAX_PREVIEW_IDS].tolist(),
        "protected_seed_count": len(protected),
        "setback": float(setback),
        "angle_degrees": float(angle_degrees),
        "distance_metric": "source-mesh-edge-length",
        "inset_support": "whole-surviving-faces-reflood",
        "invalid_faces": int((~topology.valid).sum()),
        "winding_cut_edges": int((topology.cuts == 2).sum()) // 2,
        "nonmanifold_halfedges": int((topology.cuts == 3).sum()),
        "estimated_auxiliary_bytes": topology.estimated_auxiliary_bytes,
    }
    if local_diagnostics is not None:
        diagnostics["local_boundaries"] = local_diagnostics
        diagnostics["estimated_auxiliary_bytes"] = max(
            topology.estimated_auxiliary_bytes,
            local_diagnostics["estimated_auxiliary_bytes"],
        )
    return SurfaceSelectionResult(
        _readonly(np.flatnonzero(final).astype(np.int32)),
        _readonly(np.flatnonzero(region_vertices).astype(np.int32)),
        _readonly(np.flatnonzero(boundary_vertices).astype(np.int32)),
        _readonly(seeds),
        _readonly(barriers),
        _readonly(protected),
        diagnostics,
    )
