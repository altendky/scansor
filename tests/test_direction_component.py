"""Exact unsigned orientation constraints retain independent positional geometry."""

from dataclasses import replace
from typing import Any, cast

import numpy as np
import pytest

import experiments.fit_relationship_component as component
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_relationship_component import (
    AxisInput,
    DirectionAlignmentInput,
    DirectionKind,
    DirectionOperand,
    PlaneInput,
    RelationshipFailure,
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


def unit(vector: Any) -> np.ndarray:
    array = np.asarray(vector, dtype=float)
    return array / np.linalg.norm(array)


def frame(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    u = unit(np.cross(direction, np.eye(3)[np.argmin(abs(direction))]))
    return u, np.cross(direction, u)


def axis(direction: Any, point: Any, *, free: bool = False, **kwargs: Any) -> AxisInput:
    return AxisInput(
        {
            "axis_display": unit(direction).tolist(),
            "point_display": list(point),
            "axis_is_directed": True,
        },
        free,
        **kwargs,
    )


def align(
    left: str,
    right: str,
    left_kind: DirectionKind = "axis",
    right_kind: DirectionKind = "axis",
) -> DirectionAlignmentInput:
    return DirectionAlignmentInput(
        DirectionOperand(left_kind, left), DirectionOperand(right_kind, right)
    )


def cylinder(
    name: str, direction: Any, point: Any, radius: float, *, noise: float = 0.001
) -> SurfaceInput:
    direction, point = unit(direction), np.asarray(point, dtype=float)
    u, v = frame(direction)
    theta = np.tile(np.linspace(0, 2 * np.pi, 24, endpoint=False), 5)
    height = np.repeat(np.linspace(-3, 3, 5), 24)
    radial = radius + noise * np.sin(theta * 7 + height * 2)
    xyz = (
        point
        + height[:, None] * direction
        + radial[:, None] * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v)
    )
    return SurfaceInput(
        name,
        "cylinder",
        xyz,
        np.ones(len(xyz)),
        {
            "kind": "cylinder",
            "analytic": {
                "kind": "cylinder",
                "origin": point.tolist(),
                "axis": direction.tolist(),
                "radius": radius,
                "slope": 0.0,
                "axial_domain": [-3, 3],
            },
            "anchor_reference": point.tolist(),
        },
        axis=name + "_axis",
    )


def observed_plane(name: str, normal: Any, offset: float) -> SurfaceInput:
    normal = unit(normal)
    u, v = frame(normal)
    xy = np.array([(x, y) for x in (-4, -2, 0, 2, 4) for y in (-3, -1, 1, 3)])
    points = offset * normal + xy[:, :1] * u + xy[:, 1:] * v
    return SurfaceInput(
        name,
        "plane",
        points,
        np.ones(len(points)),
        {"kind": "plane", "plane_equation": [*normal, offset]},
    )


@pytest.mark.parametrize("reference_kind", ["axis", "plane"])
@pytest.mark.parametrize("reverse", [False, True])
def test_offset_noisy_cylinder_aligns_without_axis_coincidence(
    reference_kind: DirectionKind, reverse: bool
) -> None:
    surface = cylinder("side", [0.04, -0.02, 1], [8, -3, 2], 2.0)
    direction = [0, 0, -1 if reverse else 1]
    axes = {"side_axis": axis([0.04, -0.02, 1], [8, -3, 2], free=True)}
    planes: dict[str, PlaneInput] = {}
    if reference_kind == "axis":
        axes["fixed"] = axis(direction, [-40, 70, 10])
    else:
        planes["fixed"] = PlaneInput(
            {"plane_equation": [*direction, -27 if reverse else 27]}, False
        )
    result = fit_relationship_component(
        [surface],
        axes,
        [],
        [],
        planes=planes,
        directions=(align("side_axis", "fixed", right_kind=reference_kind),),
    )
    fitted = result["surfaces"]["side"]["analytic"]
    assert np.linalg.norm(np.cross(fitted["axis"], direction)) < 1e-10
    assert np.linalg.norm(np.asarray(fitted["origin"])[:2] - [8, -3]) < 0.02
    assert fitted["radius"] == pytest.approx(2, abs=0.01)
    assert result["weighted_rms"] > 0.01
    assert result["solver"]["constraint_rank"] == 2
    if reference_kind == "axis":
        assert result["axes"]["fixed"]["point_display"] == [-40, 70, 10]
    else:
        assert result["equations"]["fixed"] == [*direction, -27 if reverse else 27]


@pytest.mark.parametrize("equal_radius", [False, True])
def test_two_free_lateral_axes_keep_distinct_positions_and_radii(
    equal_radius: bool,
) -> None:
    first = cylinder("first", [0.03, 0, 1], [8, -3, 2], 2)
    second = cylinder("second", [-0.05, 0.02, 1], [-7, 5, 6], 3)
    result = fit_relationship_component(
        [first, second],
        {
            "first_axis": axis([0.03, 0, 1], [8, -3, 2], free=True),
            "second_axis": axis([-0.05, 0.02, 1], [-7, 5, 6], free=True),
        },
        [],
        [["first", "second"]] if equal_radius else [],
        directions=(align("first_axis", "second_axis"),),
    )
    a, b = [result["surfaces"][name]["analytic"] for name in ("first", "second")]
    assert np.linalg.norm(np.cross(a["axis"], b["axis"])) < 1e-10
    assert np.linalg.norm(np.asarray(a["origin"]) - b["origin"]) > 10
    if equal_radius:
        assert a["radius"] == pytest.approx(b["radius"], abs=1e-10)
        assert a["radius"] == pytest.approx(2.5, abs=0.03)
    else:
        assert a["radius"] == pytest.approx(2, abs=0.02)
        assert b["radius"] == pytest.approx(3, abs=0.02)
        assert a["radius"] != b["radius"]


def test_plane_and_cylinder_observations_both_influence_common_orientation() -> None:
    side = cylinder("side", [0, 0, 1], [5, -2, 1], 2, noise=0)
    plane = observed_plane("plate", [0.15, 0, 1], 7)
    result = fit_relationship_component(
        [side, plane],
        {"side_axis": axis([0, 0, 1], [5, -2, 1], free=True)},
        [],
        [],
        directions=(align("side_axis", "plate", right_kind="plane"),),
    )
    a = np.asarray(result["surfaces"]["side"]["analytic"]["axis"])
    n = np.asarray(result["surfaces"]["plate"]["plane_equation"][:3])
    assert np.linalg.norm(np.cross(a, n)) < 1e-10
    assert 0.001 < abs(a[0]) < unit([0.15, 0, 1])[0] - 0.001
    assert result["surfaces"]["side"]["weighted_rms"] > 0.001
    assert result["surfaces"]["plate"]["weighted_rms"] > 0.001


@pytest.mark.parametrize("kind", ["axis", "plane"])
@pytest.mark.parametrize("initial_direction", [[0.2, -0.1, 1], [1, 0, 0]])
def test_orientation_only_datums_leave_world_position_and_offset_frozen(
    kind: DirectionKind, initial_direction: list[float]
) -> None:
    axes = {"fixed": axis([0, 0, 1], [-1, 2, 30])}
    planes: dict[str, PlaneInput] = {}
    if kind == "axis":
        axes["moving"] = axis(
            initial_direction, [8, -5, 4], free=True, position_free=False
        )
    else:
        n = unit(initial_direction)
        planes["moving"] = PlaneInput(
            {"plane_equation": [*n, 12.0]}, True, offset_free=False
        )
    result = fit_relationship_component(
        [],
        axes,
        [],
        [],
        planes=planes,
        directions=(align("moving", "fixed", left_kind=kind),),
        coordinate_frame=CoordinateFrame(np.array([23.0, -11, 7]), 3.0),
    )
    direction = (
        result["axes"]["moving"]["axis_display"]
        if kind == "axis"
        else result["equations"]["moving"][:3]
    )
    np.testing.assert_allclose(direction, [0, 0, 1], atol=1e-10)
    if kind == "axis":
        assert result["axes"]["moving"]["point_display"] == [8, -5, 4]
        assert result["solver"]["constraint_rank"] == 2
    else:
        assert result["equations"]["moving"][3] == pytest.approx(12, abs=1e-12)
        assert result["solver"]["constraint_rank"] == 3
    assert result["solver"]["tangent_dimension"] == 0


def test_direction_and_world_offset_jacobians_match_finite_differences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked = False

    def inspect(
        initial: np.ndarray,
        observations: Evaluation,
        equalities: Evaluation,
        **kwargs: Any,
    ) -> ConstrainedLeastSquaresResult:
        nonlocal checked
        for p in (initial, initial + np.linspace(-0.02, 0.03, len(initial))):
            for evaluate in (observations, equalities):
                _, analytic = evaluate(p)
                columns: list[np.ndarray] = []
                for i in range(len(p)):
                    step = np.zeros_like(p)
                    step[i] = 1e-6
                    columns.append(
                        (evaluate(p + step)[0] - evaluate(p - step)[0]) / 2e-6
                    )
                np.testing.assert_allclose(
                    analytic, np.column_stack(columns), rtol=2e-7, atol=3e-8
                )
        checked = True
        return solve_constrained_least_squares(
            initial, observations, equalities, **kwargs
        )

    monkeypatch.setattr(component, "solve_constrained_least_squares", inspect)
    plane = observed_plane("observed", [0.1, -0.1, 1], 5)
    n = unit([0.08, -0.12, 1])
    _ = fit_relationship_component(
        [plane, replace(observed_plane("bound", n, 11), datum="moving")],
        {"fixed": axis([0, 0, 1], [2, 3, 4])},
        [],
        [],
        planes={
            "moving": PlaneInput({"plane_equation": [*n, 11]}, True, offset_free=False)
        },
        directions=(
            align("moving", "observed", "plane", "plane"),
            align("observed", "fixed", "plane", "axis"),
        ),
    )
    assert checked


def test_redundancy_conflicts_and_remaining_freedom_are_explicit() -> None:
    fixed = {"a": axis([0, 0, 1], [0, 0, 0]), "b": axis([0, 0, -1], [8, 4, 2])}
    repeated = (align("a", "b"), align("b", "a"), align("a", "a"))
    assert (
        fit_relationship_component([], fixed, [], [], directions=repeated)["solver"][
            "constraint_violation"
        ]
        == 0
    )
    with pytest.raises(RelationshipFailure) as conflict:
        _ = fit_relationship_component(
            [],
            {**fixed, "b": axis([1, 0, 0], [8, 4, 2])},
            [],
            [],
            directions=(align("a", "b"),),
        )
    assert conflict.value.code == "infeasible-constraints"
    with pytest.raises(RelationshipFailure) as remaining:
        _ = fit_relationship_component(
            [],
            {**fixed, "b": replace(fixed["b"], free=True)},
            [],
            [],
            directions=(align("a", "b"),),
        )
    assert remaining.value.code == "rank-deficient"


def test_cancellation_and_callback_budget_escape_without_publication() -> None:
    axes = {
        "a": axis([0, 0, 1], [0, 0, 0]),
        "b": axis([0.2, 0, 1], [8, 4, 2], free=True, position_free=False),
    }
    with pytest.raises(FitCancelled):
        _ = fit_relationship_component(
            [],
            axes,
            [],
            [],
            directions=(align("a", "b"),),
            control=AttemptControl(AttemptLimits(), lambda: True),
        )
    with pytest.raises(FitBudgetExhausted):
        _ = fit_relationship_component(
            [],
            axes,
            [],
            [],
            directions=(align("a", "b"),),
            control=AttemptControl(AttemptLimits(max_evaluations=1)),
        )


def test_constructed_plane_keeps_clocking_freedom_about_a_fixed_parent_axis() -> None:
    result = fit_relationship_component(
        [],
        {"parent": axis([0, 0, 1], [3, 4, 5])},
        [],
        [],
        planes={
            "moving": PlaneInput(
                {"plane_equation": [1.0, 0, 0, 3]},
                True,
                axis="parent",
                construction="contains_axis",
                offset_from_axis=0.0,
                orientation_free=True,
                offset_free=True,
            ),
            "fixed": PlaneInput({"plane_equation": [0.0, 1, 0, 12]}, False),
        },
        directions=(align("moving", "fixed", "plane", "plane"),),
    )
    np.testing.assert_allclose(result["equations"]["moving"], [0, 1, 0, 4], atol=1e-10)
    assert result["axes"]["parent"]["point_display"] == [3, 4, 5]
    assert result["equations"]["fixed"] == [0, 1, 0, 12]
    assert result["solver"]["tangent_dimension"] == 0


def test_rigid_rotation_and_translation_preserve_fit_geometry_and_error() -> None:
    source = cylinder("side", [0.04, -0.02, 1], [8, -3, 2], 2)
    axes = {
        "side_axis": axis([0.04, -0.02, 1], [8, -3, 2], free=True),
        "fixed": axis([0.1, 0.2, 1], [-4, 7, 10]),
    }
    result = fit_relationship_component(
        [source], axes, [], [], directions=(align("side_axis", "fixed"),)
    )
    r = unit([1, 2, -0.5])
    angle = 0.63
    cross = np.array([[0, -r[2], r[1]], [r[2], 0, -r[0]], [-r[1], r[0], 0]])
    rotation = (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * cross
        + (1 - np.cos(angle)) * np.outer(r, r)
    )
    translation = np.array([7e4, -9e4, 2e4])
    geometry = source.result["analytic"]
    moved_geometry = {
        **geometry,
        "origin": (rotation @ geometry["origin"] + translation).tolist(),
        "axis": (rotation @ geometry["axis"]).tolist(),
    }
    moved = replace(
        source,
        points=source.points @ rotation.T + translation,
        result={
            **source.result,
            "analytic": moved_geometry,
            "anchor_reference": moved_geometry["origin"],
        },
    )
    moved_axes = {
        key: replace(
            value,
            result={
                **value.result,
                "point_display": (
                    rotation @ value.result["point_display"] + translation
                ).tolist(),
                "axis_display": (rotation @ value.result["axis_display"]).tolist(),
            },
        )
        for key, value in axes.items()
    }
    again = fit_relationship_component(
        [moved], moved_axes, [], [], directions=(align("side_axis", "fixed"),)
    )
    before, after = (
        result["surfaces"]["side"]["analytic"],
        again["surfaces"]["side"]["analytic"],
    )
    np.testing.assert_allclose(
        after["origin"], rotation @ before["origin"] + translation, atol=1e-7
    )
    np.testing.assert_allclose(after["axis"], rotation @ before["axis"], atol=1e-10)
    assert after["radius"] == pytest.approx(before["radius"], abs=1e-9)
    assert again["weighted_rms"] == pytest.approx(result["weighted_rms"], abs=1e-10)


def test_redundant_direction_rows_keep_rank_two_for_orientation_only_axis() -> None:
    result = fit_relationship_component(
        [],
        {
            "a": axis([0, 0, 1], [0, 0, 0]),
            "b": axis([0.2, -0.1, 1], [8, 4, 2], free=True, position_free=False),
        },
        [],
        [],
        directions=(align("a", "b"), align("b", "a"), align("b", "b")),
    )
    assert result["solver"]["constraint_rank"] == 2
    assert result["solver"]["tangent_dimension"] == 0
    assert result["axes"]["b"]["point_display"] == [8, 4, 2]


def test_a_free_operand_does_not_hide_conflicting_fixed_references() -> None:
    with pytest.raises(RelationshipFailure) as conflict:
        _ = fit_relationship_component(
            [],
            {
                "a": axis([0, 0, 1], [0, 0, 0]),
                "b": axis([1, 0, 0], [8, 4, 2]),
                "moving": axis([0.1, 0, 1], [2, 3, 4], free=True, position_free=False),
            },
            [],
            [],
            directions=(align("a", "moving"), align("b", "moving")),
        )
    assert conflict.value.code == "infeasible-constraints"


@pytest.mark.parametrize(
    "operand",
    [
        DirectionOperand("axis", "missing"),
        DirectionOperand("plane", "fixed"),
        DirectionOperand(cast(DirectionKind, cast(object, "point")), "fixed"),
        DirectionOperand("axis", ""),
    ],
)
def test_unavailable_or_wrong_kind_direction_operands_are_rejected(
    operand: DirectionOperand,
) -> None:
    with pytest.raises(RelationshipFailure):
        _ = fit_relationship_component(
            [],
            {"fixed": axis([0, 0, 1], [0, 0, 0])},
            [],
            [],
            directions=(
                DirectionAlignmentInput(operand, DirectionOperand("axis", "fixed")),
            ),
        )


@pytest.mark.parametrize("kind", ["axis", "plane"])
def test_property_freedom_requires_explicit_boolean_flags(kind: str) -> None:
    axes = {"fixed": axis([0, 0, 1], [0, 0, 0])}
    planes: dict[str, PlaneInput] = {}
    if kind == "axis":
        axes["bad"] = axis([0, 0, 1], [0, 0, 0], free=True, orientation_free="yes")
    else:
        planes["bad"] = PlaneInput(
            {"plane_equation": [0, 0, 1, 2]}, True, offset_free=cast(Any, "yes")
        )
    with pytest.raises(RelationshipFailure, match="freedom must be boolean"):
        _ = fit_relationship_component(
            [], axes, [], [], planes=planes, directions=(align("fixed", "fixed"),)
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_cone_alignment_preserves_taper_and_independent_axis_position(
    reverse: bool,
) -> None:
    direction = unit([0.03, -0.02, 1])
    anchor = np.array([8.0, -3, 2])
    source = cylinder("cone", direction, anchor, 2, noise=0.001)
    q = source.points - anchor
    height = q @ direction
    radial = q - height[:, None] * direction
    radial /= np.linalg.norm(radial, axis=1)[:, None]
    source = replace(
        source,
        kind="cone",
        points=source.points + 0.2 * height[:, None] * radial,
        result={
            **source.result,
            "kind": "cone",
            "analytic": {**source.result["analytic"], "kind": "cone", "slope": 0.2},
        },
    )
    result = fit_relationship_component(
        [source],
        {
            "cone_axis": axis(direction, anchor, free=True),
            "fixed": axis([0, 0, -1 if reverse else 1], [-40, 70, 10]),
        },
        [],
        [],
        directions=(align("cone_axis", "fixed"),),
    )
    fitted = result["surfaces"]["cone"]["analytic"]
    assert np.linalg.norm(np.cross(fitted["axis"], [0, 0, 1])) < 1e-10
    assert np.linalg.norm(np.asarray(fitted["origin"])[:2] - anchor[:2]) < 0.03
    assert fitted["radius"] == pytest.approx(2, abs=0.02)
    assert abs(fitted["slope"]) == pytest.approx(0.2, abs=0.01)


def test_free_axis_position_can_retain_fixed_orientation() -> None:
    source = cylinder("side", [0, 0, 1], [8, -3, 2], 2, noise=0)
    result = fit_relationship_component(
        [source],
        {
            "side_axis": axis(
                [0, 0, 1], [8.5, -3.2, 2], free=True, orientation_free=False
            ),
            "fixed": axis([0, 0, -1], [-40, 70, 10]),
        },
        [],
        [],
        directions=(align("side_axis", "fixed"),),
    )
    fitted = result["surfaces"]["side"]["analytic"]
    np.testing.assert_allclose(fitted["origin"], [8, -3, 2], atol=1e-9)
    assert fitted["axis"] == [0, 0, 1]
    assert result["solver"]["constraint_rank"] == 0


def test_alignment_composes_with_existing_unsigned_parallel_plane_relationship() -> (
    None
):
    first = observed_plane("first", [0.1, 0, 1], 3)
    second = observed_plane("second", [-0.1, 0.2, -1], -9)
    result = fit_relationship_component(
        [first, second],
        {"fixed": axis([0, 0, 1], [2, 3, 4])},
        [("parallel", ["first", "second"])],
        [],
        directions=(align("fixed", "first", "axis", "plane"),),
    )
    a, b = [
        np.asarray(result["surfaces"][name]["plane_equation"])
        for name in ("first", "second")
    ]
    np.testing.assert_allclose(a[:3], [0, 0, 1], atol=1e-10)
    np.testing.assert_allclose(b[:3], [0, 0, -1], atol=1e-10)
    assert a[3] == pytest.approx(np.mean(first.points[:, 2]), abs=1e-10)
    assert b[3] == pytest.approx(-np.mean(second.points[:, 2]), abs=1e-10)
    assert abs(a[3] + b[3]) > 5
