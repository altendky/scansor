"""Coordinate changes must preserve parameters, residuals, and derivatives."""

import numpy as np
import pytest

from experiments.fit_coordinates import (
    AxisChart,
    CoordinateFrame,
    FitCoordinates,
    GlobalPlaneOffset,
    RelativePlaneOffset,
)
from experiments.mesh_cylinder_fit import residual_jacobian


def axis_normal(parameters: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    raw = np.array([parameters[2], parameters[3], 1.0])
    length = np.linalg.norm(raw)
    normal = raw / length
    derivative = np.zeros((3, len(parameters)))
    derivative[:, 2] = (np.eye(3)[0] - normal * normal[0]) / length
    derivative[:, 3] = (np.eye(3)[1] - normal * normal[1]) / length
    return normal, derivative


@pytest.mark.parametrize("length", [1e-6, 1.0, 1e6])
def test_compound_map_roundtrip_and_tangent(length: float) -> None:
    axis = AxisChart(0, 1, 2, 3, 4, taper=6)
    coordinates = FitCoordinates(
        CoordinateFrame(np.array([100.0, -40.0, 20.0]), length),
        size=11,
        axes=(axis, AxisChart(0, 1, 2, 3, 7)),
        global_planes=(GlobalPlaneOffset(5, axis_normal),),
        relative_planes=(RelativePlaneOffset(9, axis, 8),),
        length_parameters=(10,),
    )
    physical = np.array([3.0, 4.0, 0.2, -0.1, 5.0, 6.0, 0.03, 8.0, 0.7, 9.0, 12.0])
    local = coordinates.encode(physical)
    decoded, tangent = coordinates.decode(local)
    np.testing.assert_allclose(decoded, physical, atol=2e-14)
    for column in range(len(local)):
        step = 1e-5 * max(abs(local[column]), 1.0)
        delta = np.zeros_like(local)
        delta[column] = step
        plus, _ = coordinates.decode(local + delta)
        minus, _ = coordinates.decode(local - delta)
        numerical = (plus - minus) / (2 * step)
        np.testing.assert_allclose(tangent[:, column], numerical, rtol=1e-6, atol=1e-7)


def test_cylinder_map_residual_equivalence_and_jacobian_chain() -> None:
    points = np.array([[5.0, 2.0, 8.0], [-4.0, 3.0, -2.0], [2.0, 6.0, 9.0]])
    parameters = np.array([1.0, 2.0, 0.1, -0.2, 3.0])
    frame = CoordinateFrame.from_observations(points, np.array([2.0, 3.0, 4.0]))
    coordinates = FitCoordinates(frame, 5, axes=(AxisChart(0, 1, 2, 3, 4),))
    local = coordinates.encode(parameters)
    decoded, tangent = coordinates.decode(local)
    residual, jacobian = residual_jacobian(points, decoded)
    local_residual, local_jacobian = residual_jacobian(
        (points - frame.origin) / frame.length, local
    )
    np.testing.assert_allclose(residual / frame.length, local_residual, atol=1e-14)
    np.testing.assert_allclose(
        jacobian @ tangent / frame.length, local_jacobian, atol=1e-14
    )


def test_frame_covariance_and_weight_scale() -> None:
    points = np.array([[1.0, 2.0, 3.0], [4.0, -1.0, 2.0], [2.0, 7.0, 5.0]])
    weights = np.array([1.0, 2.0, 3.0])
    original = CoordinateFrame.from_observations(points, weights)
    moved = CoordinateFrame.from_observations(
        points * 7 + [100.0, -40.0, 20.0], weights
    )
    scaled_weights = CoordinateFrame.from_observations(points, weights * 1e200)
    np.testing.assert_allclose(moved.origin, original.origin * 7 + [100.0, -40.0, 20.0])
    assert moved.length == pytest.approx(original.length * 7)
    np.testing.assert_allclose(scaled_weights.origin, original.origin)
    assert scaled_weights.length == pytest.approx(original.length)


def test_degenerate_frame_is_rejected() -> None:
    with pytest.raises(ValueError, match="distinct"):
        _ = CoordinateFrame.from_observations(np.ones((3, 3)))
