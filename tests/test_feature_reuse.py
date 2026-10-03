"""Rigid reuse preserves physical unit-axis support in the target chart."""

from copy import deepcopy

import numpy as np
import pytest

from experiments.feature_graph import (
    AxisDefinition,
    PlaneDefinition,
    directed_axis_result,
    reference_plane_result,
)
from experiments.feature_reuse import (
    transformed_axis_initial,
    transformed_fit_seed,
    transformed_plane_initial,
)


@pytest.mark.parametrize("angle", [0.0, 0.7, np.pi])
def test_transformed_cylinder_support_preserves_physical_endpoints(
    angle: float,
) -> None:
    parameters = np.array([1.0, -2.0, 0.8, -0.35, 3.0])
    source_domain = (-4.0, 7.0)
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])
    translation = np.array([4.0, 5.0, 6.0])
    fit = {
        "kind": "cylinder",
        "parameters": parameters.tolist(),
        "axial_domain": source_domain,
    }

    initial, domain = transformed_fit_seed(fit, rotation, translation)

    assert initial is not None
    assert initial[4] == parameters[4]
    source_origin = np.array([parameters[0], parameters[1], 0])
    source_axis = np.array([parameters[2], parameters[3], 1])
    source_axis /= np.linalg.norm(source_axis)
    endpoints = (
        source_origin + np.asarray(source_domain)[:, None] * source_axis
    ) @ rotation + translation
    chart_origin = np.array([initial[0], initial[1], 0])
    chart_axis = np.array([initial[2], initial[3], 1])
    chart_axis /= np.linalg.norm(chart_axis)
    projected = (endpoints - chart_origin) @ chart_axis
    np.testing.assert_allclose(domain, np.sort(projected), atol=1e-12)
    np.testing.assert_allclose(
        endpoints - chart_origin, projected[:, None] * chart_axis, atol=1e-12
    )
    assert domain[1] - domain[0] == pytest.approx(source_domain[1] - source_domain[0])


def test_identity_reuse_preserves_tilted_cylinder_support() -> None:
    parameters = [1.0, -2.0, 0.8, -0.35, 3.0]
    fit = {"kind": "cylinder", "parameters": parameters, "axial_domain": [-4.0, 7.0]}

    initial, domain = transformed_fit_seed(fit, np.eye(3), np.zeros(3))

    assert initial == parameters
    assert domain == pytest.approx((-4.0, 7.0))


def test_reuse_still_rejects_an_axis_outside_the_z_chart() -> None:
    fit = {"kind": "cylinder", "parameters": [0, 0, 0, 0, 3], "axial_domain": [-4, 7]}
    rotation = np.array([[0.0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    with pytest.raises(ValueError, match="outside the current local Z parameter chart"):
        _ = transformed_fit_seed(fit, rotation, np.zeros(3))


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("angle", [0.0, 0.3, 0.7, np.pi])
@pytest.mark.parametrize("reversed_axis", [False, True])
@pytest.mark.parametrize(
    "construction", ["contains_axis", "parallel_to_axis", "perpendicular_to_axis"]
)
def test_placed_datums_preserve_rigid_axis_and_plane_geometry(
    scale: float, angle: float, reversed_axis: bool, construction: str
) -> None:
    source_axis_node = AxisDefinition.model_validate(
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [scale, -2 * scale, 0.3, -0.2],
            "direction_reversed": reversed_axis,
        }
    )
    source_axis = directed_axis_result(
        source_axis_node,
        parameters=[scale, -2 * scale, 0.3, -0.2, 0, 0, 0],
    )
    source_plane_node = PlaneDefinition.model_validate(
        {
            "id": "plane",
            "label": "Plane",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": construction,
            "initial_angle_degrees": None
            if construction == "perpendicular_to_axis"
            else 37,
            "offset": 0 if construction == "contains_axis" else 2.3 * scale,
        }
    )
    source_plane = reference_plane_result(source_axis, source_plane_node)
    originals = deepcopy((source_axis, source_plane))
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])
    translation = np.array([4.0, 5.0, 6.0]) * scale

    parameters, target_reversed = transformed_axis_initial(
        source_axis, rotation, translation
    )
    target_axis = directed_axis_result(
        source_axis_node.model_copy(update={"direction_reversed": target_reversed}),
        parameters=[*parameters, 0, 0, 0],
    )
    initial = transformed_plane_initial(
        source_plane, rotation, translation, target_axis
    )
    target_plane = reference_plane_result(
        target_axis, source_plane_node.model_copy(update=initial)
    )

    expected_direction = np.asarray(source_axis["axis_display"]) @ rotation
    np.testing.assert_allclose(
        target_axis["axis_display"], expected_direction, atol=1e-14
    )
    moved_axis_point = np.asarray(source_axis["point_display"]) @ rotation + translation
    axis_delta = moved_axis_point - np.asarray(target_axis["point_display"])
    np.testing.assert_allclose(
        np.cross(axis_delta, expected_direction) / scale, 0, atol=1e-13
    )
    expected_normal = np.asarray(source_plane["normal_display"]) @ rotation
    expected_offset = source_plane["plane_equation"][3] + expected_normal @ translation
    np.testing.assert_allclose(
        target_plane["normal_display"], expected_normal, atol=1e-14
    )
    assert target_plane["plane_equation"][3] / scale == pytest.approx(
        expected_offset / scale, abs=1e-12
    )
    if construction == "contains_axis":
        assert initial["offset"] == 0
    if construction == "perpendicular_to_axis":
        assert initial["initial_angle_degrees"] is None
    assert (source_axis, source_plane) == originals


def test_placed_axis_parameter_fallback_preserves_reversed_direction() -> None:
    source = {"parameters": [2, -3, 0.2, 0.4], "direction_reversed": True}
    parameters, reversed_axis = transformed_axis_initial(source, np.eye(3), np.zeros(3))
    assert parameters == source["parameters"]
    assert reversed_axis is True


def test_placed_perpendicular_plane_handles_opposite_target_axis_direction() -> None:
    source = {"construction": "perpendicular_to_axis", "plane_equation": [0, 0, 1, 5]}
    initial = transformed_plane_initial(
        source,
        np.eye(3),
        np.array([2.0, 3.0, 7.0]),
        {"point_display": [2, 3, 0], "axis_display": [0, 0, -1]},
    )
    assert initial == {"initial_angle_degrees": None, "offset": -12.0}


def test_placed_containing_plane_handles_large_translation() -> None:
    rotation = np.eye(3)
    translation = np.array([1e9, -2e9, 3e9])
    initial = transformed_plane_initial(
        {"construction": "contains_axis", "plane_equation": [1, 0, 0, 2]},
        rotation,
        translation,
        {"point_display": [1e9 + 2, -2e9, 0], "axis_display": [0, 0, 1]},
    )
    assert initial["offset"] == 0
    assert initial["initial_angle_degrees"] == pytest.approx(180)


def test_placed_datum_axis_rejects_horizontal_chart() -> None:
    with pytest.raises(ValueError, match="outside the current local Z parameter chart"):
        _ = transformed_axis_initial(
            {"point_display": [0, 0, 0], "axis_display": [1, 0, 0]},
            np.eye(3),
            np.zeros(3),
        )


@pytest.mark.parametrize(
    ("construction", "equation", "message"),
    [
        ("contains_axis", [1, 0, 0, 2], "does not contain"),
        ("parallel_to_axis", [0, 0, 1, 2], "not parallel"),
        ("perpendicular_to_axis", [1, 0, 0, 2], "does not match"),
        ("unexpected", [1, 0, 0, 0], "unsupported"),
        ("parallel_to_axis", [0, 0, 0, 0], "nonzero plane"),
    ],
)
def test_placed_plane_rejects_incompatible_geometry(
    construction: str, equation: list[float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _ = transformed_plane_initial(
            {"construction": construction, "plane_equation": equation},
            np.eye(3),
            np.zeros(3),
            {"point_display": [0, 0, 0], "axis_display": [0, 0, 1]},
        )


@pytest.mark.parametrize("rotation", [np.eye(2), np.eye(3) * 2, np.diag([1, 1, -1])])
def test_placed_datum_rejects_nonrigid_matches(rotation: np.ndarray) -> None:
    with pytest.raises(ValueError, match="proper rigid transform"):
        _ = transformed_axis_initial(
            {"point_display": [0, 0, 0], "axis_display": [0, 0, 1]},
            rotation,
            np.zeros(3),
        )


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize("reversed_axis", [False, True])
@pytest.mark.parametrize(
    "construction", ["contains_axis", "parallel_to_axis", "perpendicular_to_axis"]
)
def test_placed_planes_transport_initializer_onto_refined_target_axis(
    scale: float, reversed_axis: bool, construction: str
) -> None:
    source_axis_node = AxisDefinition.model_validate(
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": [scale, -2 * scale, 0.2, -0.1],
            "direction_reversed": reversed_axis,
        }
    )
    source_axis = directed_axis_result(
        source_axis_node, parameters=[scale, -2 * scale, 0.2, -0.1, 0, 0, 0]
    )
    source_plane_node = PlaneDefinition.model_validate(
        {
            "id": "plane",
            "label": "Plane",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": construction,
            "initial_angle_degrees": None
            if construction == "perpendicular_to_axis"
            else 37,
            "offset": 0 if construction == "contains_axis" else 2.3 * scale,
        }
    )
    source_plane = reference_plane_result(source_axis, source_plane_node)
    angle = 0.7
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])
    translation = np.array([4.0, 5.0, 6.0]) * scale
    parameters, target_reversed = transformed_axis_initial(
        source_axis, rotation, translation
    )
    # A copied source fit independently refines both direction and location.
    parameters[0] += 0.03 * scale
    parameters[1] -= 0.04 * scale
    parameters[2] += 0.02
    parameters[3] -= 0.01
    target_axis = directed_axis_result(
        source_axis_node.model_copy(update={"direction_reversed": target_reversed}),
        parameters=[*parameters, 0, 0, 0],
    )
    with pytest.raises(ValueError, match=r"does not match|not parallel"):
        _ = transformed_plane_initial(source_plane, rotation, translation, target_axis)
    initial = transformed_plane_initial(
        source_plane, rotation, translation, target_axis, refine_axis=True
    )
    target_plane = reference_plane_result(
        target_axis, source_plane_node.model_copy(update=initial)
    )
    normal = np.asarray(target_plane["normal_display"])
    axis = np.asarray(target_axis["axis_display"])
    moved_point = np.asarray(source_plane["point_display"]) @ rotation + translation
    moved_normal = np.asarray(source_plane["normal_display"]) @ rotation
    if construction == "perpendicular_to_axis":
        np.testing.assert_allclose(normal, axis, atol=1e-14)
        assert initial["initial_angle_degrees"] is None
    else:
        assert abs(float(normal @ axis)) < 1e-14
        expected = moved_normal - (moved_normal @ axis) * axis
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(normal, expected, atol=1e-14)
        # A small axis refinement must not flip the intended clocking face.
        assert normal @ moved_normal > 0.999
    if construction == "contains_axis":
        assert initial["offset"] == 0
    else:
        assert (normal @ moved_point - target_plane["plane_equation"][3]) / scale == (
            pytest.approx(0, abs=1e-12)
        )


def test_refined_plane_rejects_lost_clocking_orientation() -> None:
    with pytest.raises(ValueError, match="ill-conditioned clock orientation"):
        _ = transformed_plane_initial(
            {
                "construction": "parallel_to_axis",
                "plane_equation": [0, 0, 1, 2],
                "point_display": [0, 0, 2],
            },
            np.eye(3),
            np.zeros(3),
            {"point_display": [0, 0, 0], "axis_display": [0, 0, 1]},
            refine_axis=True,
        )
