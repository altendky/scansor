"""Numerical policy, topology, ownership, bounded preview and interruption."""

from dataclasses import replace

import numpy as np
import pytest

from experiments.fit_solver import FitCancelled
from experiments.seed_surface_selection import (
    SurfaceSelectionLimits,
    build_surface_topology,
    grow_surface_selection,
)
from tests.seed_surface_fixture import cylinder_lip, grid


def test_plane_isolated_seed_border_inset_and_protection() -> None:
    points, faces = grid()
    topology = build_surface_topology(points, faces)
    result = grow_surface_selection(topology, [112], [])
    assert result.ids.tolist() == list(range(225))
    assert result.diagnostics["patches"] == 1
    assert result.diagnostics["envelopes"][0]["mode"] == "cone"
    inset = grow_surface_selection(topology, [112, 0], [], setback=2)
    assert 0 in inset.protected_ids and 0 in inset.ids
    assert 1 not in inset.ids and 112 in inset.ids
    assert len(inset.ids) < len(result.ids)
    consumed = grow_surface_selection(topology, [112], [], setback=100)
    assert consumed.ids.tolist() == [112]
    assert consumed.protected_ids.tolist() == [112]


def test_unreferenced_seed_has_protected_only_membership() -> None:
    topology = build_surface_topology(
        np.asarray([[0.0, 0, 0]]), np.empty((0, 3), dtype=np.int32)
    )
    result = grow_surface_selection(topology, [0], [])
    assert result.ids.tolist() == result.protected_ids.tolist() == [0]
    assert result.diagnostics["unsupported_seed_count"] == 1
    assert result.diagnostics["ambiguous_seed_count"] == 0


def test_crease_and_ambiguous_seed_cannot_bridge() -> None:
    points, faces = grid(folded=True)
    topology = build_surface_topology(points, faces)
    result = grow_surface_selection(topology, [7 * 15 + 3], [])
    assert np.max(points[result.region_ids, 0]) <= 7
    ambiguous = grow_surface_selection(topology, [7 * 15 + 7], [])
    assert ambiguous.ids.tolist() == [112]
    assert ambiguous.diagnostics["ambiguous_seed_count"] == 1
    both = grow_surface_selection(topology, [108, 116], [])
    assert both.diagnostics["patches"] == 2
    assert 0 in both.ids and 224 in both.ids


@pytest.mark.parametrize("angular,fillet", [(64, 8), (128, 16)])
def test_full_cylinder_wrap_stops_on_finely_tessellated_fillet(
    angular: int, fillet: int
) -> None:
    points, faces, seeds, labels = cylinder_lip(angular, fillet)
    topology = build_surface_topology(points, faces)
    result = grow_surface_selection(topology, seeds, [])
    assert result.diagnostics["envelopes"][0]["mode"] == "band"
    cylinder_vertices = np.unique(faces[labels == "cylinder"])
    assert np.isin(cylinder_vertices, result.region_ids).all()
    # A complete circumference remains, unlike a cumulative angular budget.
    assert np.isin(np.arange(angular), result.ids).all()
    assert not np.isin(np.unique(faces[labels == "plane"]), result.region_ids).any()
    assert result.diagnostics["envelopes"][0]["region_faces"] == angular * (
        12 + fillet // 2
    )
    # Two/four early fillet strips remain; this is a heuristic angular boundary.
    assert np.max(points[result.region_ids, 2]) < 0.15
    sharper = grow_surface_selection(topology, seeds, [], angle_degrees=10)
    assert len(sharper.region_ids) < len(result.region_ids)


def test_rigid_pose_scale_winding_and_deterministic_repeat() -> None:
    points, faces, seeds, _ = cylinder_lip()
    baseline = grow_surface_selection(
        build_surface_topology(points, faces), seeds, [], setback=0.1
    )
    rotation = np.asarray([[0.0, 0, 1], [1, 0, 0], [0, 1, 0]])
    moved = grow_surface_selection(
        build_surface_topology(points @ rotation * 3 + [20, -30, 50], faces),
        seeds,
        [],
        setback=0.3,
    )
    reverse = grow_surface_selection(
        build_surface_topology(points, faces[:, ::-1]), seeds, [], setback=0.1
    )
    np.testing.assert_array_equal(baseline.ids, moved.ids)
    np.testing.assert_array_equal(baseline.ids, reverse.ids)
    again = grow_surface_selection(
        build_surface_topology(points, faces), seeds, [], setback=0.1
    )
    assert baseline.preview() == again.preview()


def test_blocking_strip_stops_traversal_before_subtraction() -> None:
    points, faces = grid()
    barriers = list(range(7, 225, 15))
    topology = build_surface_topology(points, faces)
    result = grow_surface_selection(topology, [108], barriers)
    assert np.max(points[result.region_ids, 0]) < 7
    assert not np.isin(barriers, result.ids).any()
    with pytest.raises(ValueError, match="overlaps"):
        _ = grow_surface_selection(topology, [7], barriers)


def test_holes_open_boundaries_and_disconnected_components() -> None:
    points, faces = grid(hole=True)
    offset = len(points)
    topology = build_surface_topology(
        np.vstack((points, points + np.asarray([0, 0, 0.001]))),
        np.vstack((faces, faces + offset)),
    )
    result = grow_surface_selection(topology, [32], [], setback=1.5)
    assert np.max(result.ids) < offset
    assert 5 * 15 + 5 in result.boundary_ids
    assert 5 * 15 + 5 not in result.ids
    multiple = grow_surface_selection(topology, [32, 32 + offset], [])
    assert multiple.diagnostics["patches"] == 2
    assert np.any(multiple.ids >= offset)


def test_invalid_triangles_winding_and_nonmanifold_do_not_connect() -> None:
    points = np.asarray(
        [
            [0.0, 0, 0],
            [1, 0, 0],
            [0, 1, 0],
            [1, 1, 0],
            [0, 0, 0.001],
            [1, 0, 0.001],
            [0, 1, 0.001],
        ]
    )
    faces = np.asarray([[0, 1, 2], [1, 2, 3], [4, 5, 6], [0, 4, 0]], dtype=np.int32)
    topology = build_surface_topology(points, faces)
    result = grow_surface_selection(topology, [0], [])
    assert result.ids.tolist() == [0, 1, 2]
    assert not topology.valid[-1]
    assert 2 in topology.cuts
    nonmanifold = build_surface_topology(points, np.vstack((faces[:2], [1, 2, 4])))
    assert 3 in nonmanifold.cuts


def test_noisy_nonuniform_plane_is_usable_without_positional_fitting() -> None:
    points, faces = grid()
    points[:, 0] = points[:, 0] ** 1.4
    points[:, 2] += 0.001 * np.sin(np.arange(len(points)) * 1.7)
    result = grow_surface_selection(
        build_surface_topology(points, faces), [112, 113, 127, 128], []
    )
    assert len(result.ids) == len(points)


def test_noisy_nonuniform_cylinder_still_wraps() -> None:
    points, faces, seeds, labels = cylinder_lip()
    angle = np.mod(np.arctan2(points[:, 1], points[:, 0]), 2 * np.pi)
    angle = (angle / (2 * np.pi)) ** 1.3 * 2 * np.pi
    radius = np.linalg.norm(points[:, :2], axis=1)
    radius += 0.00002 * np.sin(np.arange(len(points)) * 1.7)
    points[:, 0] = radius * np.cos(angle)
    points[:, 1] = radius * np.sin(angle)
    result = grow_surface_selection(build_surface_topology(points, faces), seeds, [])
    assert np.isin(np.unique(faces[labels == "cylinder"]), result.ids).all()
    assert not np.isin(np.unique(faces[labels == "plane"]), result.region_ids).any()


def test_separate_narrow_patches_never_train_a_broad_or_envelope() -> None:
    points, faces, _, _ = cylinder_lip(128, 16)
    seeds = [2 * 128, 2 * 128 + 20]
    result = grow_surface_selection(build_surface_topology(points, faces), seeds, [])
    assert result.diagnostics["patches"] == 2
    assert [item["mode"] for item in result.diagnostics["envelopes"]] == [
        "cone",
        "cone",
    ]
    assert 2 * 128 + 10 not in result.region_ids
    assert 2 * 128 + 64 not in result.region_ids


def test_setback_uses_edge_lengths_and_keeps_exact_threshold_vertices() -> None:
    points, faces = grid()
    topology = build_surface_topology(points, faces)
    at = grow_surface_selection(topology, [112], [], setback=2)
    beyond = grow_surface_selection(topology, [112], [], setback=2.01)
    assert 7 * 15 + 2 in at.ids
    assert 7 * 15 + 2 not in beyond.ids


def test_cancellation_reaches_inset_heap(monkeypatch: pytest.MonkeyPatch) -> None:
    import experiments.seed_surface_selection as kernel

    points, faces = grid(40)
    topology = build_surface_topology(points, faces)
    # Instrument the phase boundary to distinguish heap cancellation from the
    # easier cancellation before traversal starts.
    clearance = kernel._clearance  # pyright: ignore[reportPrivateUsage]
    active = False
    checks = 0

    def cancel() -> bool:
        nonlocal checks
        if active:
            checks += 1
        return checks >= 4

    def entering(
        topology: kernel.SurfaceSelectionTopology,
        region: kernel.BoolArray,
        boundary: kernel.IntArray,
        setback: float,
        cancelled: kernel.Cancel,
    ) -> kernel.BoolArray:
        nonlocal active
        active = True
        return clearance(topology, region, boundary, setback, cancelled)

    monkeypatch.setattr(kernel, "_clearance", entering)
    with pytest.raises(FitCancelled):
        _ = grow_surface_selection(
            topology, [20 * 40 + 20], [], setback=8, cancelled=cancel
        )
    assert checks >= 4


def test_inset_reflood_discards_unseeded_lobe_behind_eroded_neck() -> None:
    points, faces = grid(21)
    # Two broad lobes joined by a one-cell-high neck.
    centers = points[faces].mean(axis=1)
    keep = (
        (centers[:, 0] < 8)
        | (centers[:, 0] > 12)
        | ((centers[:, 1] >= 10) & (centers[:, 1] <= 11))
    )
    topology = build_surface_topology(points, faces[keep])
    original = grow_surface_selection(topology, [10 * 21 + 4], [])
    assert np.max(points[original.ids, 0]) == 20
    inset = grow_surface_selection(topology, [10 * 21 + 4], [], setback=1.1)
    assert np.max(points[inset.ids, 0]) < 8


def test_preview_is_bounded_sorted_and_topology_owns_immutable_inputs() -> None:
    points, faces = grid(60)
    topology = build_surface_topology(points, faces)
    points[:] = 100
    faces[:] = 0
    result = grow_surface_selection(topology, [1830], [])
    preview = result.preview()
    assert preview["counts"]["inset"] == 3600
    assert len(preview["samples"]["inset"]) == 2048
    assert preview["samples"]["inset"] == sorted(set(preview["samples"]["inset"]))
    assert not result.ids.flags.writeable and not topology.points.flags.writeable
    with pytest.raises(ValueError):
        _ = result.preview(2049)


def test_limits_validation_and_cancellation_during_work() -> None:
    points, faces = grid()
    with pytest.raises(ValueError, match="resource"):
        _ = build_surface_topology(
            points, faces, limits=SurfaceSelectionLimits(max_auxiliary_bytes=100)
        )
    topology = build_surface_topology(points, faces)
    with pytest.raises(FitCancelled):
        _ = build_surface_topology(points, faces, cancelled=lambda: True)
    calls = 0

    def cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls > 6

    with pytest.raises(FitCancelled):
        _ = grow_surface_selection(topology, [112], [], setback=2, cancelled=cancel)
    for angle, setback in [
        (0, 0),
        (91, 0),
        (20, -1),
        (float("nan"), 0),
        (20, float("inf")),
    ]:
        with pytest.raises(ValueError):
            _ = grow_surface_selection(
                topology, [112], [], angle_degrees=angle, setback=setback
            )
    with pytest.raises(ValueError):
        _ = grow_surface_selection(topology, [True], [])
    with pytest.raises(ValueError, match="32"):
        _ = grow_surface_selection(
            replace(topology, limits=SurfaceSelectionLimits(max_patches=1)),
            [16, 208],
            [],
        )
