"""Reusable fitted-selection volumes and rigid datum-frame placement."""

import numpy as np
import pytest

from experiments.selection_region import (
    apply_selection_region,
    build_selection_region,
    datum_frame,
)


def frame_results(
    origin: np.ndarray, rotation: np.ndarray
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    radial, _, axis_direction = rotation.T
    return (
        {
            "axis_display": axis_direction.tolist(),
            "point_display": origin.tolist(),
        },
        {"plane_equation": [*axis_direction.tolist(), float(axis_direction @ origin)]},
        {"plane_equation": [*radial.tolist(), float(radial @ origin)]},
    )


def test_cylinder_region_transfers_between_datum_frames() -> None:
    source_origin = np.zeros(3)
    source_rotation = np.eye(3)
    source_datums = frame_results(source_origin, source_rotation)
    recovered_origin, recovered_rotation = datum_frame(*source_datums)
    np.testing.assert_allclose(recovered_origin, source_origin)
    np.testing.assert_allclose(recovered_rotation, source_rotation)

    angles = np.array((0.35, 0.50, 0.65, 0.80))
    axial = np.array((1.0, 1.4, 1.8, 2.2))
    radius = 5.0
    source_points = np.column_stack(
        (radius * np.cos(angles), radius * np.sin(angles), axial)
    )
    source_normals = np.column_stack(
        (np.cos(angles), np.sin(angles), np.zeros(len(angles)))
    )
    region = build_selection_region(
        source_points,
        source_normals,
        {"kind": "cylinder", "parameters": [0, 0, 0, 0, radius, 0, 0]},
        recovered_origin,
        recovered_rotation,
        tangent_margin=0.1,
        normal_margin=0.2,
        normal_angle_degrees=20.0,
    )

    azimuth = np.radians(37.0)
    target_rotation = np.array(
        (
            (np.cos(azimuth), -np.sin(azimuth), 0.0),
            (np.sin(azimuth), np.cos(azimuth), 0.0),
            (0.0, 0.0, 1.0),
        )
    )
    target_origin = np.array((12.0, -7.0, 3.0))
    target_datums = frame_results(target_origin, target_rotation)
    placed_origin, placed_rotation = datum_frame(*target_datums)
    intended = source_points @ target_rotation.T + target_origin
    intended_normals = source_normals @ target_rotation.T
    local_distractors = np.array(
        (
            (7.0 * np.cos(0.5), 7.0 * np.sin(0.5), 1.5),
            (5.0 * np.cos(2.0), 5.0 * np.sin(2.0), 1.5),
            (5.0 * np.cos(0.5), 5.0 * np.sin(0.5), 5.0),
            (5.0 * np.cos(0.5), 5.0 * np.sin(0.5), 1.5),
        )
    )
    distractors = local_distractors @ target_rotation.T + target_origin
    distractor_normals = (
        np.array(
            (
                (np.cos(0.5), np.sin(0.5), 0.0),
                (np.cos(2.0), np.sin(2.0), 0.0),
                (np.cos(0.5), np.sin(0.5), 0.0),
                (-np.cos(0.5), -np.sin(0.5), 0.0),
            )
        )
        @ target_rotation.T
    )
    points = np.vstack((intended, distractors))
    normals = np.vstack((intended_normals, distractor_normals))
    assert apply_selection_region(
        points,
        normals,
        np.ones(len(points)),
        region,
        placed_origin,
        placed_rotation,
    ) == [0, 1, 2, 3]


def test_plane_region_transfers_footprint_and_normal_side() -> None:
    origin = np.zeros(3)
    rotation = np.eye(3)
    source_points = np.array(
        ((1.0, 2.0, 4.0), (2.0, 2.0, 4.0), (1.0, 3.0, 4.0), (2.0, 3.0, 4.0))
    )
    source_normals = np.tile((0.0, 0.0, 1.0), (len(source_points), 1))
    region = build_selection_region(
        source_points,
        source_normals,
        {"kind": "plane", "plane_equation": [0.0, 0.0, 1.0, 4.0]},
        origin,
        rotation,
        tangent_margin=0.1,
        normal_margin=0.2,
        normal_angle_degrees=15.0,
    )
    points = np.vstack(
        (
            source_points,
            (4.0, 2.5, 4.0),
            (1.5, 2.5, 5.0),
            (1.5, 2.5, 4.0),
        )
    )
    normals = np.vstack(
        (
            source_normals,
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
        )
    )
    assert apply_selection_region(
        points,
        normals,
        np.ones(len(points)),
        region,
        origin,
        rotation,
    ) == [0, 1, 2, 3]


@pytest.mark.parametrize("slope", [0.2, -0.2, 1e-7, 0.0])
def test_cone_region_rigidly_transfers_a_positive_sheet_and_normal_side(
    slope: float,
) -> None:
    theta, height = np.meshgrid(np.linspace(-0.4, 0.8, 5), np.linspace(-1, 2, 4))
    radial = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    source = (3 + slope * height.ravel())[:, None] * radial + height.ravel()[
        :, None
    ] * [0, 0, 1]
    normals = (radial - slope * np.array([0, 0, 1])) / np.hypot(1, slope)
    fit = {
        "kind": "cone",
        "analytic": {
            "kind": "cone",
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": 3.0,
            "slope": slope,
            "axial_domain": [-1.0, 2.0],
        },
    }
    region = build_selection_region(
        source,
        normals,
        fit,
        np.zeros(3),
        np.eye(3),
        tangent_margin=0.05,
        normal_margin=0.03,
        normal_angle_degrees=10,
    )
    assert region["kind"] == "cone" and region["slope"] == slope
    np.testing.assert_allclose(
        region["generator_bounds"],
        [-np.hypot(1, slope) - 0.05, 2 * np.hypot(1, slope) + 0.05],
    )
    rotation = np.array([[0.0, 0, 1], [1.0, 0, 0], [0.0, 1, 0]])
    origin = np.array([10.0, -20, 30])
    local = np.vstack(
        (
            source,
            source[0] + 0.2 * normals[0],
            source[0],
            [0, 0, 0],
            [3 * np.cos(2.5), 3 * np.sin(2.5), 0],
            [3 + 5 * slope, 0, 5],
        )
    )
    local_normals = np.vstack(
        (
            normals,
            normals[0],
            -normals[0],
            [1, 0, 0],
            [np.cos(2.5), np.sin(2.5), -slope],
            [1, 0, -slope],
        )
    )
    selected = apply_selection_region(
        local @ rotation.T + origin,
        local_normals @ rotation.T,
        np.ones(len(local)),
        region,
        origin,
        rotation,
    )
    assert selected == list(range(len(source)))


def test_cone_region_allows_negative_anchor_radius_but_rejects_apex_crossing_margin() -> (
    None
):
    angles = np.array([-0.3, 0.0, 0.3])
    height = np.array([6.0, 7.0, 8.0])
    radial = np.column_stack((np.cos(angles), np.sin(angles), np.zeros(3)))
    points = (-2 + 0.5 * height)[:, None] * radial + height[:, None] * [0, 0, 1]
    normals = (radial - [0, 0, 0.5]) / np.hypot(1, 0.5)
    fit = {
        "kind": "cone",
        "analytic": {
            "kind": "cone",
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": -2.0,
            "slope": 0.5,
            "axial_domain": [6.0, 8.0],
        },
    }
    region = build_selection_region(
        points,
        normals,
        fit,
        np.zeros(3),
        np.eye(3),
        tangent_margin=0.1,
        normal_margin=0.05,
        normal_angle_degrees=10,
    )
    assert region["radius"] == -2
    assert apply_selection_region(
        points, normals, np.ones(3), region, np.zeros(3), np.eye(3)
    ) == [0, 1, 2]
    with pytest.raises(ValueError, match="margin crosses the apex"):
        _ = build_selection_region(
            points,
            normals,
            fit,
            np.zeros(3),
            np.eye(3),
            tangent_margin=3.0,
            normal_margin=0.05,
            normal_angle_degrees=10,
        )


def test_cone_region_does_not_mirror_the_negative_sheet_into_its_footprint() -> None:
    region = {
        "format": "scansor-fitted-selection-region-v1",
        "kind": "cone",
        "radius": 1.0,
        "slope": 1.0,
        "angle_start": 0.0,
        "angle_span": 2 * np.pi,
        "generator_bounds": [-100.0, 100.0],
        "normal_bounds": [-100.0, 100.0],
        "normal_sign": 1.0,
        "normal_angle_degrees": 90.0,
    }
    points = np.array([[1.0, 0, -5], [1.0, 0, 0]])
    normals = np.tile(np.array([1.0, 0, -1]) / np.sqrt(2), (2, 1))
    assert apply_selection_region(
        points, normals, np.ones(2), region, np.zeros(3), np.eye(3)
    ) == [1]


def test_finite_steep_cone_region_preserves_projection_and_signed_normal_side() -> None:
    slope = 1e160
    height = np.array([1e-160, 2e-160, 3e-160])
    theta = np.array([-0.2, 0.0, 0.2])
    radial = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(3)))
    points = (1 + slope * height)[:, None] * radial + height[:, None] * [0, 0, 1]
    scale = np.hypot(1.0, slope)
    outward = radial / scale - np.array([0.0, 0.0, slope / scale])
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
    assert np.isfinite(points).all() and np.isfinite(outward).all()
    for sign in (1.0, -1.0):
        normals = sign * outward
        region = build_selection_region(
            points,
            normals,
            fit,
            np.zeros(3),
            np.eye(3),
            tangent_margin=0.01,
            normal_margin=0.01,
            normal_angle_degrees=10,
        )
        assert region["normal_sign"] == sign
        np.testing.assert_allclose(region["generator_bounds"], [0.99, 3.01])
        assert apply_selection_region(
            points, normals, np.ones(3), region, np.zeros(3), np.eye(3)
        ) == [0, 1, 2]
        assert (
            apply_selection_region(
                points, -normals, np.ones(3), region, np.zeros(3), np.eye(3)
            )
            == []
        )


def test_negative_anchor_cone_region_rebases_to_shifted_and_reversed_datum() -> None:
    height = np.array([8.0, 10.0, 12.0, 15.0])
    theta = np.linspace(-0.4, 0.4, 4)
    radial = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(4)))
    points = (-1 + 0.5 * height)[:, None] * radial + height[:, None] * [0, 0, 1]
    normals = (radial - np.array([0, 0, 0.5])) / np.hypot(1.0, 0.5)
    fit = {
        "kind": "cone",
        "analytic": {
            "kind": "cone",
            "origin": [0, 0, 0],
            "axis": [0, 0, 1],
            "radius": -1.0,
            "slope": 0.5,
            "axial_domain": [8.0, 15.0],
        },
    }
    origin = np.array([0.0, 0.0, 10.0])
    for direction in (1.0, -1.0):
        rotation = np.diag([1.0, direction, direction])
        region = build_selection_region(
            points,
            normals,
            fit,
            origin,
            rotation,
            tangent_margin=0.1,
            normal_margin=0.1,
            normal_angle_degrees=10,
        )
        assert region["radius"] == 4.0
        assert region["slope"] == direction * 0.5
        expected_generator = direction * (height - 10) * np.hypot(1.0, 0.5)
        np.testing.assert_allclose(
            region["generator_bounds"],
            [expected_generator.min() - 0.1, expected_generator.max() + 0.1],
        )
        assert apply_selection_region(
            points, normals, np.ones(4), region, origin, rotation
        ) == [0, 1, 2, 3]
