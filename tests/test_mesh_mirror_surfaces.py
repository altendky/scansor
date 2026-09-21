"""Exact axial mirror geometry, derivatives, and shared-axis integration."""

from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from experiments.mesh_coaxial_fit import fit_coaxial, residual_jacobian
from experiments.mesh_cone_plane_fit import cone_plane_residual_jacobian
from experiments.mesh_mirror_surfaces import (
    MirrorSurfaces,
    axis_frame,
    initial_mirror,
    mirror_residual_jacobian,
    mirror_surface_equations,
    mirror_transform,
    mirrored_lateral,
)
from experiments.nozzle_coaxial import FitSelection, fit_group
from experiments.nozzle_session import NozzleWorkspace
from tests.test_mesh_coaxial_fit import geometry
from tests.test_mesh_rotational_planes import rotational_geometry

BASE = np.array([0.3, -0.4, 0.12, -0.08, 2.1, 3.2, 0.08, 2.4, 4.0, -0.2])


def plane_group() -> tuple[MirrorSurfaces, np.ndarray]:
    parameters = np.concatenate([BASE, [0.43, 0.72, -0.31, 1.4]])
    empty = MirrorSurfaces(
        (np.empty((0, 3)), np.empty((0, 3))),
        (np.empty(0), np.empty(0)),
        "plane",
        (),
        (-4.0, 5.0),
    )
    equations = mirror_surface_equations(empty, parameters, len(BASE))
    point_sets: list[np.ndarray] = []
    for equation in equations:
        normal, intercept = equation[:3], equation[3]
        reference = np.eye(3)[int(np.argmin(np.abs(normal)))]
        u = np.cross(normal, reference)
        u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        x, y = np.meshgrid(np.linspace(-0.8, 0.8, 7), np.linspace(-0.5, 0.5, 5))
        point_sets.append(
            intercept * normal + x.ravel()[:, None] * u + y.ravel()[:, None] * v
        )
    seed = tuple(equations[0])
    return (
        MirrorSurfaces(
            (point_sets[0], point_sets[1]),
            tuple(np.linspace(0.4, 1.2, len(p)) for p in point_sets),
            "plane",
            seed,
            (-4.0, 5.0),
        ),
        parameters,
    )


def lateral_group(kind: str) -> tuple[MirrorSurfaces, np.ndarray]:
    taper = 0.06 if kind == "cone" else 0.0
    seed = np.array([1.7, -0.9, 0.21, -0.13, 0.45, 0.0, taper])
    canonical = seed[[0, 1, 2, 3, 4, 6]][: 6 if kind == "cone" else 5]
    parameters = np.concatenate([BASE, [0.43], canonical])
    axis = np.array([seed[2], seed[3], 1.0])
    axis /= np.linalg.norm(axis)
    reference = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = np.cross(axis, reference)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    angle, z = np.meshgrid(
        np.linspace(0.0, 2 * np.pi, 19, endpoint=False),
        np.linspace(-0.5, 1.0, 9),
    )
    angle, z = angle.ravel(), z.ravel()
    first = (
        np.array([seed[0], seed[1], 0.0])
        + z[:, None] * axis
        + (seed[4] + taper * z)[:, None]
        * (np.cos(angle)[:, None] * u + np.sin(angle)[:, None] * v)
    )
    matrix = mirror_transform(parameters, parameters[len(BASE)])[0]
    center = np.array([parameters[0], parameters[1], 0.0])
    second = center + (first - center) @ matrix
    areas = (np.ones(len(first)), np.linspace(0.5, 1.5, len(second)))
    return (
        MirrorSurfaces(
            (first, second),
            areas,
            kind,
            tuple(seed),
            (-2.0, 5.0),
            (-0.75, 1.25),
        ),
        parameters,
    )


def test_plane_pair_recovery_and_reported_mirror_plane() -> None:
    sides, plane, area = geometry()
    group, truth = plane_group()
    initial = truth + np.linspace(-0.006, 0.008, len(truth))
    result = fit_coaxial(sides, plane, area, initial, mirrors=(group,))
    assert result.weighted_rms < 1e-9
    for points, equation in zip(group.points, result.mirror_equations[0], strict=True):
        assert np.max(np.abs(points @ equation[:3] - equation[3])) < 1e-8
    mirror = result.mirror_plane_equations[0]
    axis, _, _ = axis_frame(result.parameters[0])
    center = np.array([result.parameters[0][0], result.parameters[0][1], 0.0])
    assert mirror[:3] @ axis == pytest.approx(0.0, abs=1e-12)
    assert mirror[:3] @ center == pytest.approx(mirror[3], abs=1e-12)
    np.testing.assert_allclose(
        result.mirror_plane_directions[0] @ mirror[:3], 0.0, atol=1e-12
    )


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_lateral_pair_recovery_and_exact_reflection(kind: str) -> None:
    sides, plane, area = geometry()
    group, truth = lateral_group(kind)
    initial = truth + np.linspace(-0.003, 0.004, len(truth))
    result = fit_coaxial(sides, plane, area, initial, mirrors=(group,))
    assert result.weighted_rms < 1e-8
    assert np.all(np.diff(result.objective_history) <= 0.0)
    for observations, fitted, domain in zip(
        group.points,
        result.mirror_equations[0],
        result.mirror_domains[0],
        strict=True,
    ):
        residual, _ = cone_plane_residual_jacobian(
            observations, np.empty((0, 3)), fitted, domain
        )
        assert np.max(np.abs(residual)) < 1e-8
        assert fitted[6] == pytest.approx(0.06 if kind == "cone" else 0.0, abs=1e-8)


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_lateral_pair_preserves_each_members_domain(kind: str) -> None:
    group, parameters = lateral_group(kind)
    domains = [
        mirrored_lateral(group, parameters, len(BASE), slot)[1] for slot in range(2)
    ]
    assert domains[0] == group.domain
    assert domains[1] != domains[0]


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_complete_mirror_jacobian(kind: str) -> None:
    sides, plane, _ = geometry()
    group, truth = plane_group() if kind == "plane" else lateral_group(kind)
    parameters = truth + np.linspace(-0.004, 0.005, len(truth))
    _, actual = residual_jacobian(sides, plane, parameters, mirrors=(group,))
    expected = np.empty_like(actual)
    for column in range(len(parameters)):
        step = np.zeros_like(parameters)
        step[column] = 2e-6
        plus = residual_jacobian(sides, plane, parameters + step, mirrors=(group,))[0]
        minus = residual_jacobian(sides, plane, parameters - step, mirrors=(group,))[0]
        expected[:, column] = (plus - minus) / 4e-6
    np.testing.assert_allclose(actual, expected, atol=3e-8, rtol=2e-6)


def test_phase_initializer_accepts_radians_and_pair_validation() -> None:
    sides, plane, area = geometry()
    group, truth = plane_group()
    initialized = initial_mirror(group, truth, 0.27)
    assert initialized[0] == pytest.approx(0.27)
    assert len(initialized) == group.size
    invalid = MirrorSurfaces(
        (group.points[0],),  # pyright: ignore[reportArgumentType]
        (group.areas[0],),  # pyright: ignore[reportArgumentType]
        "plane",
        group.seed,
    )
    with pytest.raises(ValueError, match="exactly two"):
        _ = fit_coaxial(sides, plane, area, truth, mirrors=(invalid,))


def test_direct_group_jacobian_shapes() -> None:
    group, parameters = plane_group()
    residuals, jacobians, equations = mirror_residual_jacobian(
        group, parameters, len(BASE)
    )
    assert [len(values) for values in residuals] == [35, 35]
    assert [values.shape for values in jacobians] == [(35, 14), (35, 14)]
    assert [values.shape for values in equations] == [(4,), (4,)]


def test_mirror_and_rotational_groups_keep_independent_parameter_blocks() -> None:
    sides, plane, area, rotation_truth, rotation = rotational_geometry()
    mirror, mirror_truth = plane_group()
    truth = np.concatenate([rotation_truth, mirror_truth[len(BASE) :]])
    result = fit_coaxial(
        sides,
        plane,
        area,
        truth,
        rotations=(rotation,),
        mirrors=(mirror,),
    )
    assert result.weighted_rms < 1e-9
    assert len(result.rotational_residuals[0]) == 3
    assert len(result.mirror_residuals[0]) == 2


def test_nozzle_adapter_reports_members_and_fitted_plane() -> None:
    sides, anchor_plane, anchor_area = geometry()
    mirror, truth = plane_group()
    point_sets = [sides[0].points, anchor_plane, *mirror.points]
    starts = np.cumsum([0, *(len(points) for points in point_sets)])
    ids = [list(range(starts[i], starts[i + 1])) for i in range(len(point_sets))]
    local = np.vstack(point_sets)
    weights = np.concatenate([sides[0].area, anchor_area, *mirror.areas])
    normals = np.zeros_like(local)
    equations = mirror_surface_equations(mirror, truth, len(BASE))
    normals[ids[2]] = equations[0][:3]
    normals[ids[3]] = equations[1][:3]
    workspace = cast(
        NozzleWorkspace,
        SimpleNamespace(
            local=local,
            frame=np.eye(3),
            data=SimpleNamespace(
                weights=weights,
                normals=normals,
                selection={"initial_parameters": [0.3, -0.4, 0.12, -0.08, 3.2]},
            ),
            default=SimpleNamespace(source_sha256="source"),
            model_sha256="model",
        ),
    )
    fitted = fit_group(
        workspace,
        [FitSelection("anchor_side", ids[0], "cone", (-4.0, 4.0))],
        [FitSelection("anchor_plane", ids[1], "plane", (-4.0, 4.0))],
        mirror_groups=(
            (
                FitSelection("mirror_a", ids[2], "plane", (-4.0, 5.0)),
                FitSelection("mirror_b", ids[3], "plane", (-4.0, 5.0)),
            ),
        ),
        mirror_phases_radians=(truth[len(BASE)],),
    )
    assert fitted["fit"]["weighted_rms"] < 1e-9
    surfaces = cast(dict[str, Any], fitted.get("surfaces"))
    mirror_planes = cast(list[dict[str, Any]], fitted.get("mirror_planes"))
    assert set(surfaces) == {
        "anchor_side",
        "anchor_plane",
        "mirror_a",
        "mirror_b",
    }
    assert len(mirror_planes) == 1
    assert len(mirror_planes[0]["equation"]) == 4
