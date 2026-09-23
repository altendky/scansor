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


def test_cone_region_reports_bounded_first_slice() -> None:
    with pytest.raises(ValueError, match="cone selection regions"):
        _ = build_selection_region(
            np.eye(3),
            np.eye(3),
            {"kind": "cone", "parameters": [0, 0, 0, 0, 5, 0, 0.1]},
            np.zeros(3),
            np.eye(3),
            tangent_margin=0.1,
            normal_margin=0.2,
            normal_angle_degrees=20.0,
        )
