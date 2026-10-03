"""Axial datum clocking is fitted, while its relation to the axis remains exact."""

from typing import Any, cast

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import (
    AxisPlaneObservations,
    SideObservations,
    axis_plane_frame,
    fit_coaxial,
    residual_jacobian,
)
from tests.test_mesh_coaxial_fit import geometry
from tests.test_mesh_mirror_surfaces import plane_group
from tests.test_mesh_rotational_planes import rotational_geometry


@pytest.mark.parametrize("construction", ["contains_axis", "parallel_to_axis"])
@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("initial_phase", [0.0, np.radians(47) + np.pi / 2])
def test_clocking_recovery_and_origin_unit_covariance(
    construction: str, scale: float, initial_phase: float
) -> None:
    sides, _, _ = geometry()
    side = sides[1]
    truth = np.array([0.3, -0.4, 0.12, -0.08, 2.4])
    phase = np.radians(47)
    frame = AxisPlaneObservations(np.empty((0, 3)), np.empty(0), construction, phase, 0)
    axis, radial, normal = axis_plane_frame(frame, truth)
    anchor = np.array([*truth[:2], 0.0])
    distance = 0.0 if construction == "contains_axis" else -1.7
    along, across = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-2, 2, 6))
    points = (
        anchor
        + distance * normal
        + along.ravel()[:, None] * axis
        + across.ravel()[:, None] * radial
    )
    translation = scale * np.array([1000.0, -2000.0, 1500.0])
    group = AxisPlaneObservations(
        points * scale + translation,
        np.linspace(0.4, 1.1, len(points)) * scale**2,
        construction,
        initial_phase,
        0,
    )
    moved_side = SideObservations(
        side.points * scale + translation,
        side.area * scale**2,
        side.kind,
        tuple(scale * np.array(side.domain)),
    )
    initial = np.array(
        [
            translation[0],
            translation[1],
            0.0,
            0.0,
            2.2 * scale,
            initial_phase,
            *([translation[1]] if construction == "parallel_to_axis" else []),
        ]
    )
    result = fit_coaxial([moved_side], None, None, initial, axis_planes=(group,))
    equation = result.axis_plane_equations[0]
    if equation[:3] @ normal < 0:
        equation = -equation
    np.testing.assert_allclose(equation[:3], normal, atol=2e-8)
    assert equation[3] == pytest.approx(
        scale * (normal @ anchor + distance) + normal @ translation,
        abs=scale * 1e-7,
    )
    angle = result.axis_plane_angles[0]
    assert angle is not None
    assert abs(np.cos(angle - phase)) == pytest.approx(1.0, abs=1e-12)
    recovered = result.parameters[0]
    recovered_axis = np.array([*recovered[2:4], 1.0])
    recovered_axis /= np.linalg.norm(recovered_axis)
    assert equation[:3] @ recovered_axis == pytest.approx(0.0, abs=1e-12)
    if construction == "contains_axis":
        recovered_anchor = np.array([*recovered[:2], 0.0])
        assert equation[:3] @ recovered_anchor == pytest.approx(equation[3])
    assert result.weighted_rms / scale < 1e-8
    assert np.all(np.diff(result.objective_history) <= 0)
    assert len(result.axis_plane_residuals[0]) == len(points)


@pytest.mark.parametrize("kind", ["rotation", "mirror"])
def test_clocking_block_preserves_other_relationship_parameter_layout(
    kind: str,
) -> None:
    if kind == "rotation":
        sides, plane, area, truth, relationship = rotational_geometry()
        kwargs = {"rotations": (relationship,)}
    else:
        sides, plane, area = geometry()
        relationship, truth = plane_group()
        kwargs = {"mirrors": (relationship,)}
    frame = AxisPlaneObservations(
        np.empty((0, 3)), np.empty(0), "parallel_to_axis", 0.4, 0
    )
    axis, radial, normal = axis_plane_frame(frame, truth)
    along, across = np.meshgrid(np.linspace(-3, 3, 7), np.linspace(-2, 2, 6))
    points = (
        -1.7 * normal + along.ravel()[:, None] * axis + across.ravel()[:, None] * radial
    )
    group = AxisPlaneObservations(
        points, np.ones(len(points)), "parallel_to_axis", 0.0, 0
    )
    initial = np.concatenate([truth[:10], [0.0, -1.4], truth[10:]])
    result = fit_coaxial(
        sides, plane, area, initial, axis_planes=(group,), **cast(Any, kwargs)
    )
    assert result.weighted_rms < 1e-9
    np.testing.assert_allclose(
        result.axis_plane_equations[0], [*normal, -1.7], atol=1e-8
    )
    assert result.axis_plane_angles[0] == pytest.approx(0.4, abs=1e-8)

    _, jacobian = residual_jacobian(
        sides, plane, initial, axis_planes=(group,), **cast(Any, kwargs)
    )
    expected = np.empty_like(jacobian)
    for i in range(len(initial)):
        delta = np.zeros_like(initial)
        delta[i] = 1e-6
        plus = residual_jacobian(
            sides, plane, initial + delta, axis_planes=(group,), **cast(Any, kwargs)
        )[0]
        minus = residual_jacobian(
            sides, plane, initial - delta, axis_planes=(group,), **cast(Any, kwargs)
        )[0]
        expected[:, i] = (plus - minus) / 2e-6
    np.testing.assert_allclose(jacobian, expected, atol=2e-7, rtol=2e-6)
