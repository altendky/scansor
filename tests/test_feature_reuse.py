"""Rigid reuse preserves physical unit-axis support in the target chart."""

import numpy as np
import pytest

from experiments.feature_reuse import transformed_fit_seed


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
