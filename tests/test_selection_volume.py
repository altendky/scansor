"""Reusable surface-volume geometry, source independence and bounded queries."""

import copy
import json
from collections.abc import Callable
from typing import Any, Literal

import numpy as np
import pytest
from numpy.typing import NDArray

from experiments import selection_volume as volume_kernel
from experiments.selection_volume import (
    MAX_CELLS,
    TriangleIndex,
    VolumeOptions,
    apply_selection_volume,
    build_selection_volume,
    selection_volume_coverage,
    validate_selection_volume,
)

Array = NDArray[np.float64]
Faces = NDArray[np.int64]
Kind = Literal["mesh", "plane", "cylinder", "cone"]


def grid(
    count: int = 5, *, extent: float = 4, offset: float = 0
) -> tuple[Array, Array, Faces]:
    x, y = np.meshgrid(np.linspace(0, extent, count), np.linspace(0, extent, count))
    points = np.column_stack((x.ravel(), y.ravel(), np.full(x.size, offset)))
    normals = np.tile([0.0, 0, 1], (len(points), 1))
    triangles: list[list[int]] = []
    for i in range(count - 1):
        for j in range(count - 1):
            a = i * count + j
            triangles.extend(([a, a + 1, a + count + 1], [a, a + count + 1, a + count]))
    return points, normals, np.asarray(triangles, dtype=np.int64)


def volume(
    kind: Literal["mesh", "plane"] = "mesh",
    *,
    options: dict[str, float] | None = None,
    selected: list[int] | None = None,
    scale: float = 1,
) -> tuple[Array, Array, Faces, list[int], dict[str, Any]]:
    points, normals, triangles = grid()
    ids = [i for i in range(len(points)) if i != 12] if selected is None else selected
    result = build_selection_volume(
        points * scale,
        normals,
        triangles,
        ids,
        kind=kind,
        fit={"kind": "plane", "plane_equation": [0, 0, 1, 0]}
        if kind == "plane"
        else None,
        options=options,
        provenance={"source_binding": "author-scan", "selection": "painted"},
    )
    return points * scale, normals, triangles, ids, result


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_annular_footprint_retains_hole_and_exact_authored_node_membership(
    kind: Literal["mesh", "plane"],
) -> None:
    points, normals, _, ids, saved = volume(kind)
    assert saved["resolved"]["edge_padding"] == 0
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == ids
    assert apply_selection_volume(points, -normals, np.ones(len(points)), saved) == []
    assert saved["provenance"] == {
        "source_binding": "author-scan",
        "selection": "painted",
    }


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_disconnected_cells_do_not_bridge_gap(kind: Literal["mesh", "plane"]) -> None:
    points, normals, _, ids, saved = volume(kind, selected=[0, 24])
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == ids
    samples = np.array([[0.1, 0.1, 0], [2, 2, 0], [3.9, 3.9, 0]])
    assert apply_selection_volume(samples, normals[:3], np.ones(3), saved) == [0, 2]


@pytest.mark.parametrize("kind", ["mesh", "plane"])
@pytest.mark.parametrize("sign", [-1, 1])
def test_independent_thickness_respects_authored_normal_side(
    kind: Literal["mesh", "plane"], sign: int
) -> None:
    points, normals, triangles = grid()
    saved = build_selection_volume(
        points,
        sign * normals,
        triangles,
        np.arange(len(points)),
        kind=kind,
        fit={"kind": "plane", "plane_equation": [0, 0, 1, 0]}
        if kind == "plane"
        else None,
        options={"inward_thickness": 0.1, "outward_thickness": 0.4},
    )
    sample = np.tile([2.0, 2, 0], (5, 1))
    sample[:, 2] = sign * np.array([-0.2, -0.09, 0, 0.39, 0.41])
    assert apply_selection_volume(sample, sign * normals[:5], np.ones(5), saved) == [
        1,
        2,
        3,
    ]


def test_padding_is_independent_and_may_close_a_small_hole():
    points, normals, _, ids, saved = volume("plane", options={"edge_padding": 0.8})
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )
    assert 12 not in ids
    assert saved["options"]["edge_padding"] == 0.8
    assert saved["options"]["inward_thickness"] is None


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_automatic_controls_scale_and_source_buffers_can_be_discarded(
    scale: float,
) -> None:
    points, _, _, ids, saved = volume("plane", scale=scale)
    assert saved["resolved"]["spacing"] == pytest.approx(scale)
    assert saved["resolved"]["inward_thickness"] == pytest.approx(scale / 4)
    restored = json.loads(json.dumps(saved, allow_nan=False))
    points[:] += 10 * scale
    # Saved geometry is detached and has no dependency on source IDs or buffers.
    target, target_normals, _ = grid()
    order = np.arange(len(target))[::-1]
    actual = apply_selection_volume(
        target[order] * scale, target_normals, np.ones(len(target)), restored
    )
    assert actual == sorted(len(target) - 1 - i for i in ids)


def curved(
    kind: Literal["cylinder", "cone"],
    *,
    slope: float = 0.3,
    start: float = 2.9,
    stop: float = 3.4,
) -> tuple[Array, Array, Faces, dict[str, Any]]:
    theta, height = np.meshgrid(np.linspace(start, stop, 5), np.linspace(-1, 1, 4))
    radius = 3 + slope * height
    points = np.column_stack(
        (
            radius.ravel() * np.cos(theta.ravel()),
            radius.ravel() * np.sin(theta.ravel()),
            height.ravel(),
        )
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.full(theta.size, -slope))
    ) / np.hypot(1, slope)
    triangles: list[list[int]] = []
    for i in range(3):
        for j in range(4):
            a = i * 5 + j
            triangles.extend(([a, a + 1, a + 6], [a, a + 6, a + 5]))
    fit: dict[str, Any] = {
        "kind": kind,
        "analytic": {
            "kind": kind,
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": 3.0,
            "slope": slope,
            "axial_domain": [-1, 1],
        },
    }
    return points, normals, np.asarray(triangles, dtype=np.int64), fit


@pytest.mark.parametrize(
    "kind,slope",
    [("cylinder", 0), ("cone", 0.3), ("cone", -0.3), ("cone", 1e-12), ("cone", 0)],
)
def test_analytic_footprint_transfers_across_angular_seam(
    kind: Literal["cylinder", "cone"], slope: float
) -> None:
    points, normals, triangles, fit = curved(kind, slope=slope)
    ids = [i for i in range(len(points)) if i not in (7, 12)]
    saved = build_selection_volume(points, normals, triangles, ids, kind=kind, fit=fit)
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == ids
    assert apply_selection_volume(points, -normals, np.ones(len(points)), saved) == []
    theta = 0.0
    distractors = np.array(
        [
            [3 * np.cos(theta), 3 * np.sin(theta), 0],
            [4 * np.cos(3.1), 4 * np.sin(3.1), 0],
        ]
    )
    assert apply_selection_volume(distractors, normals[:2], np.ones(2), saved) == []


def test_cone_footprint_supports_negative_anchor_radius_on_positive_sheet():
    points, normals, triangles, fit = curved("cone", slope=0.5)
    points[:, 2] += 10
    fit["analytic"]["radius"] = -2.0
    fit["analytic"]["axial_domain"] = [9, 11]
    saved = build_selection_volume(
        points, normals, triangles, np.arange(len(points)), kind="cone", fit=fit
    )
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )


def test_analytic_volume_is_independent_of_workspace_axis_orientation():
    points, normals, triangles, fit = curved("cone")
    rotation = np.array([[0.0, 1, 0], [0, 0, 1], [1, 0, 0]])
    offset = np.array([20.0, -7, 11])
    fit["analytic"]["origin"] = offset.tolist()
    fit["analytic"]["axis"] = ([0, 0, 1] @ rotation).tolist()
    points = points @ rotation + offset
    normals = normals @ rotation
    saved = build_selection_volume(
        points, normals, triangles, np.arange(len(points)), kind="cone", fit=fit
    )
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_coverage_is_area_based_and_independent_of_target_density(
    kind: Literal["mesh", "plane"],
) -> None:
    _, _, _, _, saved = volume(kind, selected=list(range(25)))
    diagnostics: list[dict[str, Any]] = []
    for count in (2, 3, 11):
        points, normals, triangles = grid(count)
        result = selection_volume_coverage(
            points, normals, triangles, saved, chunk_size=1
        )
        assert result["fraction"] == pytest.approx(1)
        assert result["total_area"] == pytest.approx(16)
        assert not result["partial"]
        diagnostics.append(result)
    assert diagnostics[0] == diagnostics[-1]


def test_partial_coverage_warns_and_does_not_control_membership():
    _, _, _, _, saved = volume("plane", selected=list(range(25)))
    points, normals, triangles = grid(3, extent=2)
    result = selection_volume_coverage(points, normals, triangles, saved)
    assert result["fraction"] == pytest.approx(0.25)
    assert result["partial"]
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )
    assert selection_volume_coverage(points, normals, [], saved)["fraction"] == 0
    assert (
        selection_volume_coverage(points, -normals, triangles, saved)["fraction"] == 0
    )


def test_coverage_follows_source_normal_rays_to_a_tilted_target():
    _, _, _, _, saved = volume("plane", selected=list(range(25)))
    points, normals, triangles = grid(3)
    points[:, 2] = 0.05 * points[:, 0] - 0.1
    normals[:] = np.array([-0.05, 0, 1]) / np.hypot(1, 0.05)
    result = selection_volume_coverage(points, normals, triangles, saved)
    assert result["fraction"] == pytest.approx(1)
    assert not result["partial"]


@pytest.mark.parametrize("kind,slope", [("cylinder", 0), ("cone", 0.3)])
def test_curved_coverage_survives_different_tessellation(
    kind: Literal["cylinder", "cone"], slope: float
) -> None:
    points, normals, triangles, fit = curved(kind, slope=slope)
    saved = build_selection_volume(
        points,
        normals,
        triangles,
        np.arange(len(points)),
        kind=kind,
        fit=fit,
        options={"inward_thickness": 0.3, "outward_thickness": 0.3},
    )
    theta, height = np.meshgrid(np.linspace(2.9, 3.4, 3), np.linspace(-1, 1, 3))
    radius = 3 + slope * height
    target = np.column_stack(
        (
            radius.ravel() * np.cos(theta.ravel()),
            radius.ravel() * np.sin(theta.ravel()),
            height.ravel(),
        )
    )
    target_normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.full(theta.size, -slope))
    ) / np.hypot(1, slope)
    _, _, faces = grid(3)
    assert selection_volume_coverage(target, target_normals, faces, saved)[
        "fraction"
    ] == pytest.approx(1)


@pytest.mark.parametrize("kind,slope", [("cylinder", 0), ("cone", 0.3)])
def test_complete_periodic_wrap_accepts_both_sides_of_seam(
    kind: Literal["cylinder", "cone"], slope: float
) -> None:
    points, normals, triangles, fit = curved(kind, slope=slope, start=0, stop=2 * np.pi)
    saved = build_selection_volume(
        points, normals, triangles, np.arange(len(points)), kind=kind, fit=fit
    )
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )


def test_steep_finite_cone_has_a_finite_development():
    slope = 1e160
    theta, height = np.meshgrid(
        np.linspace(-0.2, 0.2, 3), np.linspace(1e-160, 3e-160, 3)
    )
    radius = 1 + slope * height
    points = np.column_stack(
        (
            radius.ravel() * np.cos(theta.ravel()),
            radius.ravel() * np.sin(theta.ravel()),
            height.ravel(),
        )
    )
    normals = np.column_stack(
        (
            np.cos(theta.ravel()) / slope,
            np.sin(theta.ravel()) / slope,
            -np.ones(theta.size),
        )
    )
    _, _, faces = grid(3)
    fit = {
        "kind": "cone",
        "analytic": {
            "kind": "cone",
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": 1.0,
            "slope": slope,
            "axial_domain": [1e-160, 3e-160],
        },
    }
    saved = build_selection_volume(
        points, normals, faces, np.arange(len(points)), kind="cone", fit=fit
    )
    assert np.isfinite(saved["footprint"]).all()
    assert apply_selection_volume(points, normals, np.ones(len(points)), saved) == list(
        range(len(points))
    )


@pytest.mark.parametrize("padding", [0, 0.01])
@pytest.mark.parametrize("slope", [-3, 3])
@pytest.mark.parametrize("sign", [-1, 1])
def test_cone_development_does_not_alias_a_different_physical_angular_sheet(
    padding: float,
    slope: float,
    sign: int,
) -> None:
    points, normals, triangles, fit = curved(
        "cone", slope=slope * 0.2, start=-3.1, stop=-3.0
    )
    points[:, 2] *= 0.2
    source_theta = np.arctan2(points[:, 1], points[:, 0])
    normals = (
        sign
        * np.column_stack(
            (np.cos(source_theta), np.sin(source_theta), np.full(len(points), -slope))
        )
        / np.hypot(1, slope)
    )
    fit["analytic"]["slope"] = slope
    fit["analytic"]["axial_domain"] = [-0.2, 0.2]
    saved = build_selection_volume(
        points,
        normals,
        triangles,
        np.arange(len(points)),
        kind="cone",
        fit=fit,
        options={"edge_padding": padding},
    )
    theta = -3.05 + (2 * np.pi / (abs(slope) / np.hypot(1, slope)) - 2 * np.pi)
    query = np.array([[3 * np.cos(theta), 3 * np.sin(theta), 0]])
    direction = (
        sign * np.array([[np.cos(theta), np.sin(theta), -slope]]) / np.hypot(1, slope)
    )
    assert apply_selection_volume(query, direction, [1], saved) == []
    theta = -3.05 + 2 * np.pi
    intended = np.array([[3 * np.cos(theta), 3 * np.sin(theta), 0]])
    direction = (
        sign * np.array([[np.cos(theta), np.sin(theta), -slope]]) / np.hypot(1, slope)
    )
    assert apply_selection_volume(intended, direction, [1], saved) == [0]


def test_queries_and_construction_can_be_cancelled():
    from experiments.scan_workspace import ScanCancelled

    points, normals, triangles, _, saved = volume()
    with pytest.raises(ScanCancelled):
        _ = build_selection_volume(
            points, normals, triangles, [0], cancelled=lambda: True
        )
    with pytest.raises(ScanCancelled):
        _ = apply_selection_volume(
            points, normals, np.ones(len(points)), saved, cancelled=lambda: True
        )
    with pytest.raises(ScanCancelled):
        _ = selection_volume_coverage(
            points, normals, triangles, saved, cancelled=lambda: True
        )

    calls = 0

    def after_index() -> bool:
        nonlocal calls
        calls += 1
        return calls > 4

    with pytest.raises(ScanCancelled):
        _ = apply_selection_volume(
            points,
            normals,
            np.ones(len(points)),
            saved,
            chunk_size=1,
            cancelled=after_index,
        )


def test_invalid_and_zero_weight_observations_are_excluded():
    _, _, _, _, saved = volume()
    points = np.tile([0.1, 0.1, 0], (6, 1))
    normals = np.tile([0.0, 0, 1], (6, 1))
    points[1] = np.nan
    normals[2] = 0
    normals[3] = np.inf
    weights = [1, 1, 1, 1, 0, np.nan]
    assert apply_selection_volume(points, normals, weights, saved) == [0]


def test_queries_are_chunk_invariant_and_tree_leaves_are_bounded():
    points, normals, _, ids, saved = volume("plane")
    points = np.tile(points, (5, 1))
    normals = np.tile(normals, (5, 1))
    expected = [i + block * 25 for block in range(5) for i in ids]
    for size in (1, 7, 1024):
        assert (
            apply_selection_volume(
                points, normals, np.ones(len(points)), saved, chunk_size=size
            )
            == expected
        )
    cells = np.asarray(saved["footprint"])
    index = TriangleIndex(cells)
    assert all(len(node[4]) <= 16 for node in index.nodes)
    assert sum(len(node[4]) for node in index.nodes) == len(cells)
    assert len(cells) <= MAX_CELLS


@pytest.mark.parametrize(
    "name,value",
    [
        ("inward_thickness", -1),
        ("outward_thickness", np.inf),
        ("edge_padding", np.nan),
        ("normal_angle_degrees", 0),
        ("normal_angle_degrees", 91),
        ("edge_padding", True),
        ("edge_padding", "1"),
    ],
)
def test_invalid_overrides_are_rejected(name: str, value: object) -> None:
    with pytest.raises(ValueError):
        _ = VolumeOptions.model_validate({name: value})


MALFORMED_CHANGES: list[Callable[[dict[str, Any]], None]] = [
    lambda p: p.update(format="unknown"),
    lambda p: p.update(extra=1),
    lambda p: p.update(normal_sign=True),
    lambda p: p.update(selected_count=0),
    lambda p: p["resolved"].update(spacing=0),
    lambda p: p["resolved"].update(algorithm="unknown"),
    lambda p: p["resolved"].update(edge_padding=None),
    lambda p: p["options"].update(edge_padding=1),
    lambda p: p["patch_vertices"][0][0].__setitem__(0, np.nan),
    lambda p: p["patch_normals"][0][0].__setitem__(0, 4),
    lambda p: p["surface"].update(basis_u=[1, 1, 0]),
    lambda p: p["footprint"][0][0].__setitem__(0, 100),
    lambda p: p["footprint"].__setitem__(0, [[0, 0], [0, 0], [0, 0]]),
    lambda p: p["provenance"].update(bad=np.inf),
]


@pytest.mark.parametrize("change", MALFORMED_CHANGES)
def test_malformed_saved_volumes_are_rejected(
    change: Callable[[dict[str, Any]], None],
) -> None:
    *_, saved = volume("plane")
    malformed = copy.deepcopy(saved)
    change(malformed)
    with pytest.raises(ValueError):
        _ = validate_selection_volume(malformed)


def test_bad_source_geometry_and_indices_are_rejected():
    points, normals, triangles = grid()
    for selected in ([], [0, 0], [25], [-1], [True], [1.5]):
        with pytest.raises(ValueError):
            _ = build_selection_volume(points, normals, triangles, selected)
    with pytest.raises(ValueError, match="no positive-area"):
        _ = build_selection_volume(points, normals, [], [0])
    with pytest.raises(ValueError, match="integer"):
        _ = build_selection_volume(points, normals, triangles.astype(float), [0])
    with pytest.raises(ValueError, match="normalizable"):
        _ = build_selection_volume(points, normals * 0, triangles, [0])
    with pytest.raises(ValueError, match="matching"):
        _ = build_selection_volume(points, normals, triangles, [0], kind="plane")
    with pytest.raises(ValueError, match="chunk"):
        _ = apply_selection_volume(
            points, normals, np.ones(len(points)), volume()[-1], chunk_size=0
        )


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_thin_positive_area_triangles_keep_interior_membership_and_coverage(
    kind: Literal["mesh", "plane"],
) -> None:
    points = np.array([[0.0, 0, 0], [1, 1, 0], [1 + 1e-8, 1 - 1e-8, 0]])
    normals = np.tile([0.0, 0, 1], (3, 1))
    faces = [[0, 1, 2]]
    saved = build_selection_volume(
        points,
        normals,
        faces,
        [0, 1, 2],
        kind=kind,
        fit={"kind": "plane", "plane_equation": [0, 0, 1, 0]}
        if kind == "plane"
        else None,
        options={"inward_thickness": 0, "outward_thickness": 0},
    )
    query = np.array([[0.55, 0.1, 0.35]]) @ points
    assert apply_selection_volume(query, normals[:1], [1], saved) == [0]
    assert selection_volume_coverage(points, normals, faces, saved)[
        "fraction"
    ] == pytest.approx(1)


def test_budgeted_coverage_is_deterministic_area_based_and_cancellable() -> None:
    from experiments.scan_workspace import ScanCancelled

    points, normals, faces = grid(10)
    saved = build_selection_volume(points, normals, faces, np.arange(len(points)))
    results: list[dict[str, Any]] = []
    for count in (2, 7):
        target, target_normals, target_faces = grid(count)
        result = selection_volume_coverage(
            target, target_normals, target_faces, saved, max_samples=64, chunk_size=7
        )
        assert result["fraction"] == pytest.approx(1)
        assert result["samples"] == 64
        assert result["available_samples"] > 64
        assert result["sampling_strategy"] == "stratified-patch-area"
        results.append(result)
    assert results[0] == results[1]
    target, target_normals, target_faces = grid(3, extent=2)
    partial = selection_volume_coverage(
        target, target_normals, target_faces, saved, max_samples=64
    )
    assert partial["fraction"] == pytest.approx(0.25, abs=0.08)
    assert partial["partial"]
    calls = 0

    def during_sampling() -> bool:
        nonlocal calls
        calls += 1
        return calls > 25

    with pytest.raises(ScanCancelled):
        _ = selection_volume_coverage(
            target,
            target_normals,
            target_faces,
            saved,
            max_samples=64,
            cancelled=during_sampling,
        )
    assert calls == 26


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, 65537])
def test_invalid_coverage_budgets_are_rejected(budget: Any) -> None:
    points, normals, faces, _, saved = volume()
    with pytest.raises(ValueError, match="budget"):
        _ = selection_volume_coverage(points, normals, faces, saved, max_samples=budget)


def test_support_cell_budget_reports_a_clear_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    points, normals, faces = grid()
    monkeypatch.setattr(volume_kernel, "MAX_CELLS", 2)
    with pytest.raises(ValueError, match="exceeds 2 support cells"):
        _ = build_selection_volume(points, normals, faces, [0])
