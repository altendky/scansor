"""Independent canonical symmetry fixtures; graph orchestration belongs to #273."""

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Literal, cast

import numpy as np
import pytest

import experiments.fit_relationship_component as component
from experiments.fit_relationship_component import (
    AxisInput,
    MirrorInput,
    PlaneInput,
    RadiusDistanceInput,
    RelationshipFailure,
    RotationInput,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import (
    AttemptControl,
    AttemptLimits,
    FitBudgetExhausted,
    FitCancelled,
)
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresResult,
    Evaluation,
    solve_constrained_least_squares,
)

Kind = Literal["plane", "cylinder", "cone"]
Mode = Literal["rotation", "mirror"]


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def _frame(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    first = np.cross(direction, np.eye(3)[np.argmin(abs(direction))])
    first = _unit(first)
    return first, np.cross(direction, first)


def _rotation(direction: np.ndarray, angle: float) -> np.ndarray:
    """Independent column-vector Rodrigues oracle; no production transforms."""
    x, y, z = direction
    cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * cross
        + (1 - np.cos(angle)) * np.outer(direction, direction)
    )


@dataclass
class _Case:
    surfaces: list[SurfaceInput]
    axes: dict[str, AxisInput]
    planes: dict[str, PlaneInput]
    members: tuple[str, ...]
    origins: list[np.ndarray]
    directions: list[np.ndarray]
    intervals: list[list[float]]
    center: np.ndarray
    central_direction: np.ndarray
    mirror_normal: np.ndarray
    central_key: str


def _case(
    kind: Kind,
    mode: Mode,
    *,
    oblique: bool = False,
    noise: float = 0,
    free: bool = False,
    reverse_alias: bool = False,
    fixed_members: bool = False,
) -> _Case:
    # Both a +120 rotation and this independent reflection send world Z to a
    # horizontal member. This is the exact legacy source-Z chart counterexample.
    central = np.array([np.sqrt(2), 0, 1]) / np.sqrt(3)
    mirror_normal = np.array([-0.5, 0.5, 1 / np.sqrt(2)])
    center = np.array([2.0, -3, 4])
    base_origin = center + np.array([4.0, 3, -2])
    base_direction = np.array([0.0, 0, 1])
    plane_normal = _unit(np.array([0.3, 0.6, 0.7]))
    pose = _rotation(_unit(np.array([1.0, -2, 3])), 0.67) if oblique else np.eye(3)
    translation = np.array([7.0, -4, 2]) if oblique else np.zeros(3)
    center, base_origin = pose @ center + translation, pose @ base_origin + translation
    central, base_direction = pose @ central, pose @ base_direction
    mirror_normal, plane_normal = pose @ mirror_normal, pose @ plane_normal
    central_key = "central_alias" if reverse_alias else "central"
    effective = -central if reverse_alias else central
    first, second = _frame(central)
    axes = {
        "central": AxisInput(
            {
                "point_display": (center + (0.015 * first if free else 0)).tolist(),
                "axis_display": _unit(
                    central + (0.01 * second if free else 0)
                ).tolist(),
                "axis_is_directed": True,
            },
            free=free,
        )
    }
    if reverse_alias:
        axes[central_key] = AxisInput(
            {
                "point_display": center.tolist(),
                "axis_display": (-central).tolist(),
                "axis_is_directed": True,
            },
            free=False,
            source="central",
        )
    count = 3 if mode == "rotation" else 2
    members = tuple(f"member_{index}" for index in range(count))
    intervals = [[8.0 + index, 13.0 + index] for index in range(count)]
    surfaces: list[SurfaceInput] = []
    origins: list[np.ndarray] = []
    directions: list[np.ndarray] = []
    for index, name in enumerate(members):
        matrix = (
            _rotation(effective, index * 2 * np.pi / 3)
            if mode == "rotation"
            else np.eye(3) - 2 * index * np.outer(mirror_normal, mirror_normal)
        )
        anchor = center + matrix @ (base_origin - center)
        direction = matrix @ base_direction
        origins.append(anchor)
        directions.append(direction)
        if kind == "plane":
            normal = matrix @ plane_normal
            first, second = _frame(normal)
            x, y = np.meshgrid([-2.0, -0.5, 0.7, 2.5], [-1.5, -0.2, 1.8])
            points = anchor + x.ravel()[:, None] * first + y.ravel()[:, None] * second
            points += noise * np.sin(np.arange(len(points)) * 1.7)[:, None] * normal
            initial_normal = _unit(normal + 0.012 * first)
            result = {
                "kind": kind,
                "plane_equation": [
                    *initial_normal,
                    float(initial_normal @ anchor + 0.02),
                ],
            }
            surfaces.append(
                SurfaceInput(
                    name, kind, points, np.linspace(0.2, 2, len(points)), result
                )
            )
            continue
        first, second = _frame(direction)
        angle, height = np.meshgrid(
            np.linspace(0, 2 * np.pi, 16, endpoint=False),
            np.linspace(intervals[index][0], intervals[index][1], 4),
        )
        radial = (
            np.cos(angle.ravel())[:, None] * first
            + np.sin(angle.ravel())[:, None] * second
        )
        radius, taper = (-1.0, 0.5) if kind == "cone" else (2.0, 0.0)
        noisy_radius = (
            radius
            + taper * height.ravel()
            + noise * np.sin(np.arange(angle.size) * 1.7)
        )
        points = (
            anchor
            + height.ravel()[:, None] * direction
            + noisy_radius[:, None] * radial
        )
        axis_key = name + "_axis"
        seed_point = anchor if fixed_members else anchor + 0.015 * first
        seed_direction = (
            direction if fixed_members else _unit(direction + 0.008 * second)
        )
        axes[axis_key] = AxisInput(
            {
                "point_display": seed_point.tolist(),
                "axis_display": seed_direction.tolist(),
                "axis_is_directed": True,
            },
            free=not fixed_members,
        )
        result = {
            "kind": kind,
            "analytic": {
                "kind": kind,
                "origin": seed_point.tolist(),
                "axis": seed_direction.tolist(),
                "radius": radius + 0.025,
                "slope": taper,
                "axial_domain": intervals[index].copy(),
            },
            "anchor_reference": anchor.tolist(),
            "support_intent": {
                "kind": "point",
                "reference": anchor.tolist(),
                "direction": direction.tolist(),
                "interval": intervals[index].copy(),
            },
        }
        surfaces.append(
            SurfaceInput(
                name,
                kind,
                points,
                (1 + 0.1 * index) * np.linspace(0.2, 2, len(points)),
                result,
                axis=axis_key,
            )
        )
    planes: dict[str, PlaneInput] = {}
    if mode == "mirror":
        seed = _unit(mirror_normal + 0.012 * np.cross(central, mirror_normal))
        planes["mirror"] = PlaneInput(
            {"plane_equation": [*seed, float(seed @ center)]},
            free=True,
            axis=central_key,
            construction="contains_axis",
        )
    return _Case(
        surfaces,
        axes,
        planes,
        members,
        origins,
        directions,
        intervals,
        center,
        central,
        mirror_normal,
        central_key,
    )


def _fit(case: _Case, mode: Mode, **kwargs: Any) -> dict[str, Any]:
    return fit_relationship_component(
        case.surfaces,
        case.axes,
        [],
        [],
        planes=case.planes,
        rotations=(
            RotationInput(case.central_key, cast(tuple[str, str, str], case.members)),
        )
        if mode == "rotation"
        else (),
        mirrors=(MirrorInput("mirror", cast(tuple[str, str], case.members)),)
        if mode == "mirror"
        else (),
        **kwargs,
    )


def _equation(result: dict[str, Any], truth_normal: np.ndarray) -> np.ndarray:
    equation = np.asarray(result["plane_equation"])
    return equation if equation[:3] @ truth_normal >= 0 else -equation


def _check_derivatives(
    initial: np.ndarray, observations: Evaluation, equalities: Evaluation
) -> None:
    # Perturb every slot, including raw plane normals away from the unit sphere
    # and private axis slopes away from their chart pole.
    sample = initial + 0.003 * np.sin(np.arange(len(initial)) + 0.3)
    for evaluate in (observations, equalities):
        _, actual = evaluate(sample)
        columns: list[np.ndarray] = []
        for index in range(len(sample)):
            step = np.zeros_like(sample)
            step[index] = 1e-6
            columns.append(
                (evaluate(sample + step)[0] - evaluate(sample - step)[0]) / 2e-6
            )
        np.testing.assert_allclose(
            actual, np.column_stack(columns), atol=3e-8, rtol=3e-6
        )


def _check_rows(case: _Case, fitted: dict[str, Any]) -> None:
    all_residuals: list[np.ndarray] = []
    all_weights: list[np.ndarray] = []
    for surface in case.surfaces:
        result = fitted["surfaces"][surface.id]
        if surface.kind == "plane":
            equation = np.asarray(result["plane_equation"])
            residual = surface.points @ equation[:3] - equation[3]
        else:
            geometry = result["analytic"]
            direction = np.asarray(geometry["axis"])
            q = surface.points - np.asarray(geometry["origin"])
            height = q @ direction
            rho = np.linalg.norm(q - height[:, None] * direction, axis=1)
            residual = (
                rho - geometry["radius"] - geometry["slope"] * height
            ) / np.hypot(1, geometry["slope"])
        assert len(result["residuals"]) == len(surface.points)
        np.testing.assert_allclose(
            result["residuals"], residual, atol=2e-12, rtol=1e-10
        )
        expected = np.sqrt(surface.weights @ residual**2 / surface.weights.sum())
        assert result["weighted_rms"] == pytest.approx(expected, abs=2e-12)
        all_residuals.append(residual)
        all_weights.append(surface.weights)
    residual, weights = np.concatenate(all_residuals), np.concatenate(all_weights)
    assert fitted["weighted_rms"] == pytest.approx(
        np.sqrt(weights @ residual**2 / weights.sum()), abs=2e-11
    )
    _ = json.dumps(fitted, allow_nan=False)


def _check_member_support(case: _Case, fitted: dict[str, Any]) -> None:
    for index, name in enumerate(case.members):
        result = fitted["surfaces"][name]
        if result["kind"] == "plane":
            continue
        geometry = result["analytic"]
        np.testing.assert_allclose(
            geometry["axial_domain"], case.intervals[index], atol=1e-12
        )
        assert result["support_intent"] == case.surfaces[index].result["support_intent"]
        direction = np.asarray(geometry["axis"])
        anchor = np.asarray(geometry["origin"])
        assert abs((case.origins[index] - anchor) @ direction) < 2e-12
        if result["kind"] == "cone":
            assert geometry["radius"] < 0
            assert (
                min(
                    geometry["radius"]
                    + geometry["slope"] * np.asarray(geometry["axial_domain"])
                )
                > 0
            )


def _check_symmetry(case: _Case, fitted: dict[str, Any], mode: Mode) -> None:
    central = fitted["axes"][case.central_key]
    center, direction = (
        np.asarray(central["point_display"]),
        np.asarray(central["axis_display"]),
    )
    normal = np.zeros(3)
    if mode == "mirror":
        equation = np.asarray(fitted["equations"]["mirror"])
        normal = equation[:3]
        assert abs(normal @ direction) < 1e-10
        assert abs(normal @ center - equation[3]) < 1e-10
    first = fitted["surfaces"][case.members[0]]
    for index, name in enumerate(case.members[1:], start=1):
        matrix = (
            _rotation(direction, index * 2 * np.pi / 3)
            if mode == "rotation"
            else np.eye(3) - 2 * np.outer(normal, normal)
        )
        other = fitted["surfaces"][name]
        if first["kind"] == "plane":
            base = np.asarray(first["plane_equation"])
            expected_normal = matrix @ base[:3]
            expected_offset = base[3] + (expected_normal - base[:3]) @ center
            equation = _equation(other, expected_normal)
            np.testing.assert_allclose(
                equation, [*expected_normal, expected_offset], atol=2e-9
            )
            continue
        base, geometry = first["analytic"], other["analytic"]
        expected_anchor = center + matrix @ (np.asarray(base["origin"]) - center)
        expected_axis = matrix @ np.asarray(base["axis"])
        actual_axis = np.asarray(geometry["axis"])
        sign = 1 if actual_axis @ expected_axis >= 0 else -1
        np.testing.assert_allclose(sign * actual_axis, expected_axis, atol=2e-9)
        delta = expected_anchor - np.asarray(geometry["origin"])
        np.testing.assert_allclose(np.cross(delta, expected_axis), 0, atol=2e-9)
        assert geometry["radius"] + geometry["slope"] * (
            delta @ actual_axis
        ) == pytest.approx(base["radius"], abs=2e-9)
        assert sign * geometry["slope"] == pytest.approx(base["slope"], abs=2e-9)


def test_independent_truth_transforms_have_horizontal_members_and_group_identities() -> (
    None
):
    direction = np.array([np.sqrt(2), 0, 1]) / np.sqrt(3)
    matrix = _rotation(direction, 2 * np.pi / 3)
    normal = np.array([-0.5, 0.5, 1 / np.sqrt(2)])
    mirror = np.eye(3) - 2 * np.outer(normal, normal)
    expected = np.array([1 / np.sqrt(2), -1 / np.sqrt(2), 0])
    np.testing.assert_allclose(matrix @ [0, 0, 1], expected, atol=4e-16)
    np.testing.assert_allclose(mirror @ [0, 0, 1], expected, atol=4e-16)
    np.testing.assert_allclose(matrix @ matrix @ matrix, np.eye(3), atol=1e-15)
    np.testing.assert_allclose(mirror @ mirror, np.eye(3), atol=1e-15)
    assert np.linalg.det(matrix) == pytest.approx(1)
    assert np.linalg.det(mirror) == pytest.approx(-1)
    assert abs(direction @ normal) < 1e-15


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("oblique", [False, True])
@pytest.mark.parametrize("free", [False, True])
def test_ordered_rotation_recovers_independent_canonical_geometry(
    kind: Kind, oblique: bool, free: bool
) -> None:
    case = _case(kind, "rotation", oblique=oblique, free=free)
    before = deepcopy(case)
    fitted = _fit(case, "rotation")
    _check_symmetry(case, fitted, "rotation")
    _check_member_support(case, fitted)
    _check_rows(case, fitted)
    for index, surface in enumerate(case.surfaces):
        result = fitted["surfaces"][surface.id]
        assert result["weighted_rms"] < 1e-8
        if kind != "plane":
            np.testing.assert_allclose(
                np.cross(result["analytic"]["axis"], case.directions[index]),
                0,
                atol=1e-8,
            )
            np.testing.assert_allclose(
                np.cross(
                    np.asarray(result["analytic"]["origin"]) - case.origins[index],
                    case.directions[index],
                ),
                0,
                atol=1e-8,
            )
        np.testing.assert_array_equal(surface.points, before.surfaces[index].points)
        np.testing.assert_array_equal(surface.weights, before.surfaces[index].weights)
        assert surface.result == before.surfaces[index].result
    assert case.axes == before.axes
    assert case.planes == before.planes
    if not oblique and kind != "plane":
        assert abs(case.directions[1][2]) < 4 * np.finfo(float).eps
        assert abs(fitted["surfaces"][case.members[1]]["analytic"]["axis"][2]) < 1e-8


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_noisy_symmetry_retains_hard_geometry_and_every_weighted_row(
    kind: Kind, mode: Mode
) -> None:
    case = _case(kind, mode, oblique=True, noise=0.002)
    fitted = _fit(case, mode)
    _check_symmetry(case, fitted, mode)
    _check_member_support(case, fitted)
    _check_rows(case, fitted)
    assert all(result["weighted_rms"] > 0 for result in fitted["surfaces"].values())


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("oblique", [False, True])
def test_free_mirror_clock_recovers_independent_reflection(
    kind: Kind, oblique: bool
) -> None:
    case = _case(kind, "mirror", oblique=oblique)
    fitted = _fit(case, "mirror")
    _check_symmetry(case, fitted, "mirror")
    _check_member_support(case, fitted)
    _check_rows(case, fitted)
    normal = np.asarray(fitted["equations"]["mirror"])[:3]
    np.testing.assert_allclose(np.cross(normal, case.mirror_normal), 0, atol=1e-8)
    assert all(result["weighted_rms"] < 1e-8 for result in fitted["surfaces"].values())


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_rotation_about_reversed_directed_alias_preserves_positive_order(
    kind: Kind,
) -> None:
    case = _case(kind, "rotation", reverse_alias=True, fixed_members=kind != "plane")
    fitted = _fit(case, "rotation")
    _check_symmetry(case, fitted, "rotation")
    _check_rows(case, fitted)
    np.testing.assert_array_equal(
        fitted["axes"]["central_alias"]["axis_display"],
        case.axes["central_alias"].result["axis_display"],
    )
    for key, axis in case.axes.items():
        np.testing.assert_allclose(
            fitted["axes"][key]["point_display"],
            axis.result["point_display"],
            atol=2e-14,
        )
        np.testing.assert_array_equal(
            fitted["axes"][key]["axis_display"], axis.result["axis_display"]
        )


@pytest.mark.parametrize("free", [False, True])
@pytest.mark.parametrize("plane_alias", [False, True])
def test_mirror_parallel_planes_compose_radius_distances_and_equal_radii(
    monkeypatch: pytest.MonkeyPatch, free: bool, plane_alias: bool
) -> None:
    case = _case("plane", "mirror", oblique=True, free=free)
    center, direction, normal = case.center, case.central_direction, case.mirror_normal
    first, second = _frame(normal)
    x, y = np.meshgrid([-2.0, -0.3, 1.8], [-1.7, 0.2, 1.5])
    for index, name in enumerate(case.members):
        sign = 1 if index == 0 else -1
        point = center + sign * 2 * normal
        points = point + x.ravel()[:, None] * first + y.ravel()[:, None] * second
        equation = [*(sign * normal), float(sign * normal @ point)]
        case.surfaces[index] = SurfaceInput(
            name,
            "plane",
            points,
            np.linspace(0.2, 2, len(points)),
            {"kind": "plane", "plane_equation": equation},
        )
    first, second = _frame(direction)
    angle, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 20, endpoint=False), [-3.0, 0.5, 4]
    )
    radial = (
        np.cos(angle.ravel())[:, None] * first + np.sin(angle.ravel())[:, None] * second
    )
    points = center + height.ravel()[:, None] * direction + 2 * radial
    for index, name in enumerate(("radius", "equal_radius")):
        case.surfaces.append(
            SurfaceInput(
                name,
                "cylinder",
                points.copy(),
                np.linspace(0.3 + index, 2.3 + index, len(points)),
                {
                    "kind": "cylinder",
                    "analytic": {
                        "kind": "cylinder",
                        "origin": center.tolist(),
                        "axis": direction.tolist(),
                        "radius": 2.03,
                        "slope": 0.0,
                        "axial_domain": [-3.0, 4.0],
                    },
                    "support_intent": {"kind": "selection"},
                },
                axis="central",
            )
        )
    datum = "mirror"
    if plane_alias:
        datum = "mirror_alias"
        case.planes[datum] = PlaneInput(
            {"plane_equation": [*(-normal), float(-normal @ center)]},
            free=False,
            source="mirror",
        )
    distances = tuple(
        RadiusDistanceInput(
            "radius",
            member,
            datum,
            (1 if index == 0 else -1) * (-1 if plane_alias else 1),
        )
        for index, member in enumerate(case.members)
    )
    checked = False

    def inspect(
        initial: np.ndarray,
        observations: Evaluation,
        equalities: Evaluation,
        **kwargs: Any,
    ) -> ConstrainedLeastSquaresResult:
        nonlocal checked
        _check_derivatives(initial, observations, equalities)
        checked = True
        return solve_constrained_least_squares(
            initial, observations, equalities, **kwargs
        )

    if free and plane_alias:
        monkeypatch.setattr(component, "solve_constrained_least_squares", inspect)
    fitted = fit_relationship_component(
        case.surfaces,
        case.axes,
        [("parallel", list(case.members))],
        [["radius", "equal_radius"]],
        planes=case.planes,
        mirrors=(MirrorInput(datum, cast(tuple[str, str], case.members)),),
        radius_distances=distances,
    )
    if free and plane_alias:
        assert checked
    _check_rows(case, fitted)
    _check_symmetry(case, fitted, "mirror")
    radius = fitted["surfaces"]["radius"]["analytic"]["radius"]
    assert radius == pytest.approx(2, abs=1e-8)
    assert fitted["surfaces"]["equal_radius"]["analytic"]["radius"] == pytest.approx(
        radius, abs=1e-10
    )
    reference = np.asarray(fitted["equations"][datum])
    for index, member in enumerate(case.members):
        equation = _equation(fitted["surfaces"][member], reference[:3])
        expected_sign = (1 if index == 0 else -1) * (-1 if plane_alias else 1)
        assert equation[3] - reference[3] == pytest.approx(
            expected_sign * radius, abs=1e-9
        )
    if not free:
        np.testing.assert_array_equal(
            fitted["axes"]["central"]["axis_display"],
            case.axes["central"].result["axis_display"],
        )
    np.testing.assert_allclose(
        np.cross(fitted["axes"]["central"]["axis_display"], direction), 0, atol=1e-8
    )


def test_incompatible_fixed_member_axes_fail_without_moving_hard_references() -> None:
    case = _case("cylinder", "rotation", fixed_members=True)
    key = case.members[1] + "_axis"
    result = deepcopy(case.axes[key].result)
    result["axis_display"] = _unit(
        np.asarray(result["axis_display"]) + np.array([0.0, 0.03, 0.02])
    ).tolist()
    case.axes[key] = AxisInput(result, free=False)
    before = deepcopy(case.axes)
    with pytest.raises(RelationshipFailure) as captured:
        _ = _fit(case, "rotation")
    assert captured.value.code
    _ = json.dumps(captured.value.diagnostic, allow_nan=False)
    assert case.axes == before


@pytest.mark.parametrize("kind", ["plane", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_symmetry_observation_and_equality_derivatives_include_off_unit_datums(
    monkeypatch: pytest.MonkeyPatch, kind: Kind, mode: Mode
) -> None:
    case = _case(kind, mode, oblique=True, free=mode == "rotation")
    checked = False

    def inspect(
        initial: np.ndarray,
        observations: Evaluation,
        equalities: Evaluation,
        **kwargs: Any,
    ) -> ConstrainedLeastSquaresResult:
        nonlocal checked
        _check_derivatives(initial, observations, equalities)
        checked = True
        return solve_constrained_least_squares(
            initial, observations, equalities, **kwargs
        )

    monkeypatch.setattr(component, "solve_constrained_least_squares", inspect)
    _ = _fit(case, mode)
    assert checked


def test_symmetry_callbacks_are_bounded_and_cancel_without_mutating_intent() -> None:
    case = _case("cone", "rotation", free=True)
    before = deepcopy(case)
    control = AttemptControl(AttemptLimits())
    fitted = _fit(case, "rotation", control=control)
    assert (
        control.evaluations
        == fitted["solver"]["evaluations"]
        + fitted["solver"]["constraint_evaluations"]
        + 1
    )
    with pytest.raises(FitBudgetExhausted):
        _ = _fit(
            case, "rotation", control=AttemptControl(AttemptLimits(max_evaluations=1))
        )
    with pytest.raises(FitCancelled):
        _ = _fit(
            case,
            "rotation",
            control=AttemptControl(AttemptLimits(), cancelled=lambda: True),
        )
    during = AttemptControl(AttemptLimits())
    during.cancelled = lambda: during.evaluations >= 2
    with pytest.raises(FitCancelled):
        _ = _fit(case, "rotation", control=during)
    assert during.evaluations == 2
    assert case.axes == before.axes
    for index, surface in enumerate(case.surfaces):
        assert surface.result == before.surfaces[index].result
        np.testing.assert_array_equal(surface.points, before.surfaces[index].points)


@pytest.mark.parametrize(
    "defect", ["duplicate", "mixed-kind", "missing-axis", "missing-plane"]
)
def test_invalid_symmetry_has_typed_strict_json_diagnostic(defect: str) -> None:
    case = _case("cylinder", "rotation")
    rotations, mirrors = (), ()
    if defect == "duplicate":
        rotations = (
            RotationInput(
                "central", (case.members[0], case.members[0], case.members[2])
            ),
        )
    elif defect == "mixed-kind":
        case.surfaces[1] = replace(case.surfaces[1], kind="cone")
        rotations = (
            RotationInput("central", cast(tuple[str, str, str], case.members)),
        )
    elif defect == "missing-axis":
        rotations = (
            RotationInput("missing", cast(tuple[str, str, str], case.members)),
        )
    else:
        mirrors = (MirrorInput("missing", cast(tuple[str, str], case.members[:2])),)
    with pytest.raises(RelationshipFailure) as captured:
        _ = fit_relationship_component(
            case.surfaces, case.axes, [], [], rotations=rotations, mirrors=mirrors
        )
    assert captured.value.code
    _ = json.dumps(captured.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("free_clock", [False, True])
def test_coaxial_mirror_pair_does_not_invent_unobserved_clock_evidence(
    free_clock: bool,
) -> None:
    case = _case("cylinder", "mirror")
    center, direction = case.center, case.central_direction
    first, second = _frame(direction)
    angle, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 16, endpoint=False), [-3.0, 0.5, 4]
    )
    radial = (
        np.cos(angle.ravel())[:, None] * first + np.sin(angle.ravel())[:, None] * second
    )
    points = center + height.ravel()[:, None] * direction + 2 * radial
    for index, name in enumerate(case.members):
        axis_key = name + "_axis"
        case.axes[axis_key] = AxisInput(
            {
                "point_display": center.tolist(),
                "axis_display": direction.tolist(),
                "axis_is_directed": True,
            },
            free=False,
        )
        case.surfaces[index] = SurfaceInput(
            name,
            "cylinder",
            points.copy(),
            np.linspace(0.2, 2, len(points)),
            {
                "kind": "cylinder",
                "analytic": {
                    "kind": "cylinder",
                    "origin": center.tolist(),
                    "axis": direction.tolist(),
                    "radius": 2.01,
                    "slope": 0.0,
                    "axial_domain": [-3.0, 4.0],
                },
                "support_intent": {"kind": "selection"},
            },
            axis=axis_key,
        )
    case.planes["mirror"] = replace(case.planes["mirror"], free=free_clock)
    if free_clock:
        with pytest.raises(RelationshipFailure) as captured:
            _ = _fit(case, "mirror")
        assert captured.value.code == "rank-deficient"
        _ = json.dumps(captured.value.diagnostic, allow_nan=False)
    else:
        fitted = _fit(case, "mirror")
        _check_rows(case, fitted)
        _check_symmetry(case, fitted, "mirror")


@pytest.mark.parametrize("incompatible", [False, True])
def test_entirely_fixed_rotational_datums_have_no_invented_free_parameters(
    incompatible: bool,
) -> None:
    case = _case("plane", "rotation", oblique=True)
    for index, surface in enumerate(case.surfaces):
        # Three prescribed noncollinear fixture rows determine this analytic
        # plane directly; no standalone fitting call supplies the datum.
        normal = _unit(
            np.cross(
                surface.points[1] - surface.points[0],
                surface.points[4] - surface.points[0],
            )
        )
        if normal @ np.asarray(surface.result["plane_equation"])[:3] < 0:
            normal = -normal
        offset = float(normal @ surface.points[0])
        if incompatible and index == 1:
            offset += 0.1
        datum = surface.id + "_datum"
        case.planes[datum] = PlaneInput(
            {"plane_equation": [*normal, offset]},
            free=False,
        )
        case.surfaces[index] = replace(surface, datum=datum)
    before = deepcopy(case.planes)
    if incompatible:
        with pytest.raises(RelationshipFailure) as captured:
            _ = _fit(case, "rotation")
        assert captured.value.code == "infeasible-constraints"
        _ = json.dumps(captured.value.diagnostic, allow_nan=False)
    else:
        fitted = _fit(case, "rotation")
        assert fitted["solver"]["tangent_dimension"] == 0
        assert fitted["solver"]["iterations"] == 0
        _check_rows(case, fitted)
        _check_symmetry(case, fitted, "rotation")
    assert case.planes == before


def test_rotational_lines_jointly_identify_plane_without_individual_plane_fits() -> (
    None
):
    case = _case("plane", "rotation", oblique=True)
    original = case.surfaces[0].points
    normal = _unit(np.cross(original[1] - original[0], original[4] - original[0]))
    first, second = _frame(normal)
    for index, angle in enumerate((0.0, np.pi / 3, -np.pi / 5)):
        matrix = _rotation(case.central_direction, index * 2 * np.pi / 3)
        tangent = np.cos(angle) * first + np.sin(angle) * second
        local = case.origins[0] + np.array([-1.0, 0, 2])[:, None] * tangent
        points = case.center + (local - case.center) @ matrix.T
        assert np.linalg.matrix_rank(points - points.mean(axis=0), tol=1e-12) == 1
        case.surfaces[index] = replace(
            case.surfaces[index],
            points=points,
            weights=(1 + 0.1 * index) * np.array([0.2, 0.7, 1.3]),
        )
    fitted = _fit(case, "rotation")
    _check_symmetry(case, fitted, "rotation")
    _check_rows(case, fitted)
    for surface in case.surfaces:
        assert fitted["surfaces"][surface.id]["weighted_rms"] < 1e-8
