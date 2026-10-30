"""Independent geometry, physical scales, bounded work and cut-aware averaging."""

import inspect
from dataclasses import replace

import numpy as np
import pytest

from experiments import local_surface_boundaries as kernel
from experiments.fit_solver import FitCancelled
from experiments.seed_surface_selection import (
    BoolArray,
    IntArray,
    SurfaceSelectionTopology,
    build_surface_topology,
)
from tests.seed_surface_fixture import cylinder_lip, grid


def neighbors(
    topology: SurfaceSelectionTopology, barriers: list[int] | None = None
) -> IntArray:
    result = topology.neighbors.copy()
    result[topology.cuts != 0] = -1
    if barriers:
        blocked = np.isin(topology.triangles, barriers).any(axis=1)
        result[blocked] = -1
        result[(result >= 0) & blocked[np.maximum(result, 0)]] = -1
    return result


def region(
    topology: SurfaceSelectionTopology,
    near: IntArray,
    eligible: BoolArray,
    seeds: list[int],
) -> BoolArray:
    reached = np.zeros(len(eligible), dtype=bool)
    pending = np.flatnonzero(
        np.isin(topology.triangles, seeds).any(axis=1) & eligible
    ).tolist()
    reached[pending] = True
    while pending:
        for other in near[pending.pop()]:
            if other >= 0 and eligible[other] and not reached[other]:
                reached[other] = True
                pending.append(int(other))
    return reached


@pytest.mark.parametrize("angular,fillet", [(64, 8), (128, 16)])
def test_physical_neighborhood_wraps_cylinder_and_stops_fine_fillet(
    angular: int, fillet: int
) -> None:
    points, faces, seeds, labels = cylinder_lip(angular, fillet)
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, near, np.asarray(seeds, dtype=np.int32), 20, 0.2, None
    )
    reached = region(topology, near, eligible, seeds)
    assert reached[labels == "cylinder"].all()
    assert not reached[labels == "plane"].any()
    assert np.isin(np.arange(angular), faces[reached]).all()
    assert diagnostics["resolved_neighborhood_distance"] == 0.2
    assert diagnostics["neighborhood_source"] == "manual"
    assert max(diagnostics["iterations"]) <= 256
    assert diagnostics["max_normal_dispersion_degrees"] > 20
    assert max(diagnostics["relative_residuals"]) <= 4e-7
    assert max(diagnostics["max_normal_residuals"]) <= 4e-7


def test_auto_distance_uses_seed_local_physical_dual_edges() -> None:
    points, faces, seeds, labels = cylinder_lip()
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, near, np.asarray(seeds, dtype=np.int32), 20, None, None
    )
    assert 0.1 < diagnostics["resolved_neighborhood_distance"] < 0.3
    assert diagnostics["neighborhood_source"] == "auto-seed-face-dual-edge-median"
    reached = region(topology, near, eligible, seeds)
    assert reached[labels == "cylinder"].all()
    assert not reached[labels == "plane"].any()


@pytest.mark.parametrize("size", [41, 81])
def test_crease_uses_physical_distance_across_different_tessellations(
    size: int,
) -> None:
    points, faces = grid(size, folded=True)
    points *= 40 / (size - 1)
    seed = (size // 2) * size + size // 5
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, near, np.asarray([seed], dtype=np.int32), 20, 1.0, None
    )
    reached = region(topology, near, eligible, [seed])
    xmax = float(points[np.unique(faces[reached]), 0].max())
    assert 18 <= xmax < 20
    assert diagnostics["max_normal_dispersion_degrees"] > 30


def test_noisy_nonuniform_plane_has_no_artificial_normal_boundary() -> None:
    points, faces = grid(41)
    points[:, 0] = points[:, 0] ** 1.15
    points[:, 2] += 0.02 * np.sin(np.arange(len(points)) * 1.7)
    topology = build_surface_topology(points, faces)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, neighbors(topology), np.asarray([840], dtype=np.int32), 20, 2, None
    )
    assert eligible.all()
    assert diagnostics["max_normal_dispersion_degrees"] < 5


def test_rigid_pose_units_and_reversed_winding_preserve_passability() -> None:
    points, faces, seeds, _ = cylinder_lip()
    seed_ids = np.asarray(seeds, dtype=np.int32)
    base = build_surface_topology(points, faces)
    eligible, _ = kernel.local_boundary_eligibility(
        base, neighbors(base), seed_ids, 20, 0.2, None
    )
    rotation = np.asarray([[0.0, 0, 1], [1, 0, 0], [0, 1, 0]])
    moved = build_surface_topology(points @ rotation * 3 + [20, -30, 50], faces)
    transformed, _ = kernel.local_boundary_eligibility(
        moved, neighbors(moved), seed_ids, 20, 0.6, None
    )
    reversed_topology = build_surface_topology(points, faces[:, ::-1])
    reversed_result, _ = kernel.local_boundary_eligibility(
        reversed_topology, neighbors(reversed_topology), seed_ids, 20, 0.2, None
    )
    np.testing.assert_array_equal(eligible, transformed)
    np.testing.assert_array_equal(eligible, reversed_result)


def test_barriers_cut_normal_averaging_before_the_flood() -> None:
    points, faces = grid(41, folded=True)
    topology = build_surface_topology(points, faces)
    barrier = list(range(20, len(points), 41))
    near = neighbors(topology, barrier)
    blocked = np.isin(faces, barrier).any(axis=1)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, near, np.asarray([20 * 41 + 8], dtype=np.int32), 20, 4, None
    )
    assert eligible[~blocked].all()
    assert diagnostics["max_normal_dispersion_degrees"] < 1e-3
    reached = region(topology, near, eligible & ~blocked, [20 * 41 + 8])
    assert points[np.unique(faces[reached]), 0].max() < 20


def test_nearby_disconnected_opposite_sheet_never_contributes_normals() -> None:
    points, faces = grid()
    count = len(points)
    topology = build_surface_topology(
        np.vstack((points, points + np.asarray([0, 0, 0.001]))),
        np.vstack((faces, faces[:, ::-1] + count)),
    )
    near = neighbors(topology)
    eligible, _ = kernel.local_boundary_eligibility(
        topology, near, np.asarray([112], dtype=np.int32), 20, 5, None
    )
    assert eligible.all()
    reached = region(topology, near, eligible, [112])
    assert reached[: len(faces)].all() and not reached[len(faces) :].any()


def test_huge_disconnected_flat_area_cannot_hide_local_crease_residual() -> None:
    points, faces = grid(41, folded=True)
    seeds = np.asarray([828], dtype=np.int32)
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    eligible, diagnostics = kernel.local_boundary_eligibility(
        topology, near, seeds, 20, 1, None
    )
    assert max(diagnostics["iterations"]) > 0
    giant = np.asarray(
        [[1e8, 1e8, 1e8], [2e8, 0, 1e8], [2e8, 1e8, 0]], dtype=np.float64
    )
    expanded = build_surface_topology(
        np.vstack((points, giant)),
        np.vstack((faces, np.asarray([[0, 1, 2]], dtype=np.int32) + len(points))),
    )
    expanded_near = neighbors(expanded)
    revised, revised_diagnostics = kernel.local_boundary_eligibility(
        expanded, expanded_near, seeds, 20, 1, None
    )
    np.testing.assert_array_equal(eligible, revised[: len(faces)])
    original_region = region(topology, near, eligible, seeds.tolist())
    revised_region = region(expanded, expanded_near, revised, seeds.tolist())
    np.testing.assert_array_equal(original_region, revised_region[: len(faces)])
    assert points[np.unique(faces[original_region]), 0].max() < 20
    assert max(revised_diagnostics["iterations"]) > 0
    assert max(revised_diagnostics["max_normal_residuals"]) <= 4e-7


def test_boundary_solver_is_cancellable_during_iterations() -> None:
    points, faces = grid(41, folded=True)
    topology = build_surface_topology(points, faces)
    applies = 0

    def cancelled() -> bool:
        nonlocal applies
        frame = inspect.currentframe()
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is not None and caller.f_code.co_name == "apply":
            applies += 1
        return applies >= 3

    with pytest.raises(FitCancelled):
        _ = kernel.local_boundary_eligibility(
            topology,
            neighbors(topology),
            np.asarray([828], dtype=np.int32),
            20,
            2,
            cancelled,
        )
    assert applies == 3


def test_resource_guard_accounts_retained_topology_not_retired_sort_peak() -> None:
    points, faces = grid()
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    seeds = np.asarray([112], dtype=np.int32)
    _, diagnostics = kernel.local_boundary_eligibility(
        topology, near, seeds, 20, 1, None
    )
    peak = diagnostics["estimated_auxiliary_bytes"]
    allowed = replace(
        topology, limits=replace(topology.limits, max_auxiliary_bytes=peak)
    )
    allowed = replace(allowed, estimated_auxiliary_bytes=peak * 10)
    assert kernel.local_boundary_eligibility(allowed, near, seeds, 20, 1, None)[0].all()
    too_small = replace(
        allowed, limits=replace(allowed.limits, max_auxiliary_bytes=peak - 1)
    )
    with pytest.raises(ValueError, match="resource limits"):
        _ = kernel.local_boundary_eligibility(too_small, near, seeds, 20, 1, None)


def test_iteration_exhaustion_has_no_partial_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, faces = grid(41, folded=True)
    topology = build_surface_topology(points, faces)
    monkeypatch.setattr(kernel, "_MAX_ITERATIONS", 1)
    with pytest.raises(ValueError, match="iteration budget"):
        _ = kernel.local_boundary_eligibility(
            topology,
            neighbors(topology),
            np.asarray([828], dtype=np.int32),
            20,
            2,
            None,
        )


def test_empty_and_isolated_support_and_invalid_controls() -> None:
    points = np.asarray([[0.0, 0, 0]])
    topology = build_surface_topology(points, np.empty((0, 3), dtype=np.int32))
    result, diagnostics = kernel.local_boundary_eligibility(
        topology, neighbors(topology), np.asarray([0], dtype=np.int32), 20, None, None
    )
    assert not len(result)
    assert diagnostics["resolved_neighborhood_distance"] is None
    for angle, distance in [
        (0, 1),
        (91, 1),
        (True, 1),
        (20, 0),
        (20, np.inf),
        (20, True),
    ]:
        with pytest.raises(ValueError, match="distance positive"):
            _ = kernel.local_boundary_eligibility(
                topology,
                neighbors(topology),
                np.asarray([0], dtype=np.int32),
                angle,
                distance,
                None,
            )
    points = np.asarray([[0.0, 0, 0], [1, 0, 0], [0, 1, 0]])
    topology = build_surface_topology(points, np.asarray([[0, 1, 2]], dtype=np.int32))
    result, diagnostics = kernel.local_boundary_eligibility(
        topology, neighbors(topology), np.asarray([0], dtype=np.int32), 20, None, None
    )
    assert result.tolist() == [True]
    assert diagnostics["neighborhood_source"] == "auto-source-edge-median"


def test_asymmetric_graph_is_rejected_before_cg() -> None:
    points, faces = grid()
    topology = build_surface_topology(points, faces)
    near = neighbors(topology)
    first, slot = np.argwhere(near >= 0)[0]
    near[first, slot] = -1
    with pytest.raises(ValueError, match="symmetric"):
        _ = kernel.local_boundary_eligibility(
            topology, near, np.asarray([112], dtype=np.int32), 20, 1, None
        )
