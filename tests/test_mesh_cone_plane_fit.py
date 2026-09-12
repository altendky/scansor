"""Independent cone geometry, cylinder-limit, derivative and domain checks."""

from pathlib import Path

import numpy as np
import pytest

from experiments.mesh_cone_plane_fit import (
    InvalidConeDomain,
    cone_plane_residual_jacobian,
    fit_cone_plane,
)
from experiments.mesh_cylinder_plane_fit import joint_residual_jacobian


@pytest.mark.parametrize("taper", [0.0, 1e-7, 0.08, -0.08])
@pytest.mark.parametrize("slopes", [(0.12, -0.08), (-0.2, 0.15)])
def test_cone_recovery(taper: float, slopes: tuple[float, float]) -> None:
    axis = np.array([*slopes, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    center = np.array([0.3, -0.4, 0.0])
    # Uneven azimuth spacing and partial circumference exercise incomplete coverage.
    theta, z = np.meshgrid(
        np.linspace(0, 1, 48) ** 1.4 * 1.6 * np.pi, np.linspace(-1, 1, 9)
    )
    cone = (
        center
        + z.ravel()[:, None] * axis
        + (3.2 + taper * z.ravel())[:, None]
        * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v)
    )
    x, y = np.meshgrid(np.linspace(-3, 3, 8), np.linspace(-3, 3, 8))
    plane = 4.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    result = fit_cone_plane(
        cone,
        plane,
        np.linspace(0.1, 2, len(cone)),
        np.linspace(0.2, 1, len(plane)),
        np.array([0.0, 0, 0, 0, 3, 4, 0]),
        (-3, 6),
    )
    np.testing.assert_allclose(
        result["parameters"], [0.3, -0.4, *slopes, 3.2, 4.1, taper], atol=1e-8
    )


def test_cone_derivative_and_cylinder_limit() -> None:
    cone = np.array([[2.0, 3, 1], [-3, 2, -2], [4, -1, 3], [-1, -4, -1]])
    plane = np.array([[2.0, 1, 4], [-2, 3, 4], [-1, -3, 4]])
    parameters = np.array([0.2, -0.1, 0.05, -0.03, 3, 4, 0.06])
    _, actual = cone_plane_residual_jacobian(cone, plane, parameters, (-5, 6))
    expected = np.empty_like(actual)
    for j in range(7):
        delta = np.zeros(7)
        delta[j] = 1e-6
        plus, _ = cone_plane_residual_jacobian(cone, plane, parameters + delta, (-5, 6))
        minus, _ = cone_plane_residual_jacobian(
            cone, plane, parameters - delta, (-5, 6)
        )
        expected[:, j] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, atol=1e-8, rtol=1e-7)
    parameters[6] = 0
    r, jac = cone_plane_residual_jacobian(cone, plane, parameters, (-5, 6))
    cr, cj = joint_residual_jacobian(cone, plane, parameters[:6])
    np.testing.assert_array_equal(r, cr)
    np.testing.assert_array_equal(jac[:, :6], cj)


def test_invalid_domain_and_unobservable_taper() -> None:
    theta = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    ring = np.column_stack((3 * np.cos(theta), 3 * np.sin(theta), np.zeros(32)))
    plane = np.array([[1.0, 0, 4], [0, 1, 4], [-1, -1, 4]])
    p = np.array([0.0, 0, 0, 0, 3, 4, 0])
    with pytest.raises(InvalidConeDomain, match="outside"):
        _ = cone_plane_residual_jacobian(ring, plane, p, (1, 2))
    p[6] = 1
    with pytest.raises(InvalidConeDomain, match="positive"):
        _ = cone_plane_residual_jacobian(ring, plane, p, (-4, 6))
    p[6] = 0
    with pytest.raises(ValueError, match="ill-conditioned"):
        _ = fit_cone_plane(
            ring, plane, np.ones(len(ring)), np.ones(len(plane)), p, (-2, 6)
        )


def test_saved_cone_example_and_frustum_view(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from experiments.run_nozzle_cone_plane import run
    from scansor._plyio import Reader, build_layout, read_header

    example = (
        Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
    )
    output = tmp_path / "cone"
    run(example, output)
    report = json.loads((output / "fit.json").read_text())
    assert report["counts"] == {"lateral": 1261, "plane": 642}
    assert (
        report["cone_plane_fit"]["cone_weighted_rms"]
        < report["cylinder_plane_baseline"]["cylinder_weighted_rms"]
    )
    assert (
        report["cone_plane_fit"]["weighted_rms"]
        < report["cylinder_plane_baseline"]["weighted_rms"]
    )
    assert report["taper"] == pytest.approx(0.01556439, abs=1e-6)
    assert report["multistart_max_parameter_difference"] < 1e-7
    assert sum(b["count"] for b in report["axial_profile"]) == 1261
    np.testing.assert_array_equal(report["axis_world"], report["plane_normal_world"])
    assert report["world_residual_max_difference"] < 1e-12
    assert report["orthogonal_distance_max_difference"] < 1e-12
    with np.load(output / "residuals.npz") as data:
        np.testing.assert_allclose(
            data["cone_radial_residual"] / np.hypot(1, report["taper"]),
            data["cone_normal_residual"],
            atol=1e-14,
        )
        assert len(data["lateral_ids"]) == 1261
    # The display guide follows the taper, rather than rendering the old cylinder.
    with (output / "joint-cone-guide.ply").open("rb") as stream:
        layout = build_layout(
            read_header(stream), fixed_lists={("face", "vertex_indices"): 3}
        )
        reader = Reader(stream, layout, max_range_bytes=2_000_000)
        rows = reader.read_range("vertex", 0, layout.element("vertex").element.count)
    xyz = np.column_stack([rows[k] for k in ("x", "y", "z")]).astype(float)
    axis = np.array(report["axis_world"])
    q = xyz - report["axis_point_world"]
    z = q @ axis
    rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
    radius = report["reference_diameter"] / 2
    distance = (rho - radius - report["taper"] * z) / np.hypot(1, report["taper"])
    assert np.max(np.abs(distance)) < 0.01201
    assert np.ptp(rho) > 0.05
    _ = capsys.readouterr()
