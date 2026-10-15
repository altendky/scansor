"""High-precision meridian feet for the observed long-cone worker failure."""

import json
from decimal import Decimal, localcontext
from typing import Any

import numpy as np
import pytest

from experiments.selection_growth import surface_distance
from experiments.surface_primitives import projected_axial_parameter

# Actual emitted geometry from cone-admission-diagnostic/browser-chromium.json,
# debug_actual/imported_cones[3]/axis_bound/results/bound. Native counterpart is
# retained too: the independently evaluated finite supports contain all60rows.
WORKER = {
    "kind": "cone",
    "origin": [80.00001306798195, -60.000023921917, 120022.31030870043],
    "axis": [5.4669643774463685e-11, 1.5580391546532362e-10, -1.0],
    "radius": 2.5119885995520277,
    "slope": -9.998821650413871e-8,
    "axial_domain": [-2880017.6896913, 3119982.310308701],
}
NATIVE = {
    "kind": "cone",
    "origin": [80.00001306798195, -60.000023921917, 120022.3103087009],
    "axis": [5.46696437744718e-11, 1.558039154653691e-10, -1.0],
    "radius": 2.5119885995520264,
    "slope": -9.998821650413924e-8,
    "axial_domain": [-2880017.6896912996, 3119982.3103087014],
}


def meridian_foot(z: float, rho: float, radius: float, slope: float) -> float:
    with localcontext() as context:
        context.prec = 80
        h, radial, r, k = map(Decimal, (z, rho, radius, slope))
        # Orthogonal projection onto meridian line (h,r+k*h).
        return float((h + k * (radial - r)) / (1 + k * k))


@pytest.mark.parametrize("sign", [-1.0, 1.0])
def test_observed_long_cone_foot_does_not_lose_a_height_ulp(sign: float) -> None:
    # Frozen admitted source row0: [80,-57.79999923706055,-2999960].
    # Dot/radial coordinates under WORKER were captured before the producer fix.
    z, rho = sign * 3119982.310308701, 2.199538587062088
    r, k = 2.5119885995520277, sign * -9.998821650413871e-8
    expected = meridian_foot(z, rho, r, k)
    actual = projected_axial_parameter(np.array([z]), np.array([rho]), r, k)
    np.testing.assert_array_equal(actual, [expected])
    assert expected == z


@pytest.mark.parametrize(
    "slope", [-1e160, -1.000000001, -1.0, -1e-7, 0.0, 1e-7, 1.0, 1.000000001, 1e160]
)
def test_meridian_projection_handles_steep_taper_and_negative_anchor(
    slope: float,
) -> None:
    z = np.array([-100.0, 0.0, 100.0])
    rho = np.array([0.01, 4.0, 100.0])
    radius = -2.0
    expected = [
        meridian_foot(float(h), float(p), radius, slope)
        for h, p in zip(z, rho, strict=True)
    ]
    actual = projected_axial_parameter(z, rho, radius, slope)
    assert np.isfinite(actual).all()
    factor = np.hypot(1, slope)
    # Cancellation near slope=-1 can leave a tiny foot from two large
    # contributions. Bound arithmetic error by operand magnitudes, while
    # retaining a tiny absolute bound for slopes1e160 and feet1e-160.
    operand_scale = (
        np.abs(z) / factor / factor
        + abs(slope / factor) * np.abs(rho - radius) / factor
    )
    assert np.all(np.abs(actual - expected) <= 16 * np.finfo(float).eps * operand_scale)
    _ = json.dumps({"projected_axial_parameter": actual.tolist()}, allow_nan=False)


def frozen_source_points() -> np.ndarray:
    # Explicit preregistered source construction, independent of runtime probe
    # functions. Round to admitted raw f32 before inspecting either emitted fit.
    theta, height = np.meshgrid(
        [0.0, 0.3, 0.8, 1.4, 1.9, 2.3, 2.9, 3.5, 4.0, 4.4, 5.0, 5.7],
        [-3.0, -1.3, 0.0, 1.2, 3.0],
    )
    h = height.ravel() * 1e6
    radial = np.column_stack(
        (-np.sin(theta.ravel()), np.cos(theta.ravel()), np.zeros(theta.size))
    )
    points = (
        np.array([80.0, -60.0, 40.0])
        + h[:, None] * [0, 0, 1]
        + (2.5 + 1e-7 * h + 0.001 * np.sin(1.7 * np.arange(60)))[:, None] * radial
    )
    return points.astype(np.float32).astype(np.float64)


def precise_inside(points: np.ndarray, geometry: dict[str, Any]) -> list[bool]:
    with localcontext() as context:
        context.prec = 80
        axis = [Decimal(float(v)) for v in geometry["axis"]]
        length = sum((v * v for v in axis), Decimal(0)).sqrt()
        axis = [v / length for v in axis]
        origin = [Decimal(float(v)) for v in geometry["origin"]]
        radius, k = Decimal(geometry["radius"]), Decimal(geometry["slope"])
        lo, hi = [Decimal(float(v)) for v in geometry["axial_domain"]]
        inside: list[bool] = []
        for point in points:
            q = [Decimal(float(p)) - a for p, a in zip(point, origin, strict=True)]
            h = sum((a * b for a, b in zip(q, axis, strict=True)), Decimal(0))
            rho = sum(
                ((a - h * b) ** 2 for a, b in zip(q, axis, strict=True)), Decimal(0)
            ).sqrt()
            foot = (h + k * (rho - radius)) / (1 + k * k)
            inside.append(lo <= foot <= hi and radius + k * foot > 0)
        return inside


@pytest.mark.parametrize("geometry", [WORKER, NATIVE], ids=["worker", "native"])
def test_emitted_long_cones_classify_frozen_rows_like_independent_decimal_feet(
    geometry: dict[str, Any],
) -> None:
    points = frozen_source_points()
    expected = precise_inside(points, geometry)
    assert expected == [True] * 60
    _residuals, _normals, actual = surface_distance(
        points, {"kind": "cone", "analytic": geometry}
    )
    np.testing.assert_array_equal(actual, expected)
