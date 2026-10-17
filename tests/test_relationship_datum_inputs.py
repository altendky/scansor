"""Plane declarations participate in hard relationships without invented evidence."""

import json
from copy import deepcopy
from typing import Any

import numpy as np
import pytest

from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_relationship_component import (
    AxisInput,
    PlaneInput,
    RelationshipFailure,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import AttemptControl, AttemptLimits, FitBudgetExhausted
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresFailure,
    solve_constrained_least_squares,
)


def plane(equation: list[float], *, free: bool = False, **kwargs: Any) -> PlaneInput:
    return PlaneInput({"plane_equation": equation}, free, **kwargs)


def test_empty_observation_core_support_is_explicit_and_requires_zero_free_tangent() -> (
    None
):
    def observations(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.empty(0), np.empty((0, len(values)))

    def equality(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return values - 3, np.eye(len(values))

    with pytest.raises(ConstrainedLeastSquaresFailure) as rejected:
        _ = solve_constrained_least_squares(
            [1.0], observations, equality, parameter_scales=[1.0]
        )
    assert rejected.value.code == "numeric-failure"
    solved = solve_constrained_least_squares(
        [1.0],
        observations,
        equality,
        parameter_scales=[1.0],
        allow_empty_observations=True,
    )
    assert solved.parameters == pytest.approx([3.0])
    assert solved.rank == solved.tangent_dimension == 0
    assert solved.constraint_rank == 1
    assert solved.objective_history == (0.0,)
    with pytest.raises(ConstrainedLeastSquaresFailure) as underidentified:
        _ = solve_constrained_least_squares(
            [1.0],
            observations,
            lambda _: (np.empty(0), np.empty((0, 1))),
            parameter_scales=[1.0],
            allow_empty_observations=True,
        )
    assert underidentified.value.code == "rank-deficient"


def test_fixed_parallel_datums_preserve_offsets_without_observation_rows() -> None:
    datums = {
        "first": plane([0.0, 0, 1, 4]),
        "second": plane([0.0, 0, -1, -9]),
    }
    before = deepcopy(datums)
    fitted = fit_relationship_component(
        [],
        {},
        [("parallel", list(datums))],
        [],
        planes=datums,
    )
    assert datums == before
    assert fitted["surfaces"] == {}
    assert fitted["equations"] == {
        key: value.result["plane_equation"] for key, value in datums.items()
    }
    assert fitted["weighted_rms"] == 0
    assert fitted["solver"]["objective_history"] == [0.0]
    assert fitted["solver"]["tangent_dimension"] == 0
    assert fitted["solver"]["iterations"] == 0
    _ = json.dumps(fitted, allow_nan=False)


@pytest.mark.parametrize(
    ("relation", "equation"),
    [("parallel", [1.0, 0, 0, 4]), ("coincident", [0.0, 0, 1, 9])],
)
def test_conflicting_fixed_datums_are_infeasible(
    relation: str,
    equation: list[float],
) -> None:
    with pytest.raises(RelationshipFailure) as caught:
        _ = fit_relationship_component(
            [],
            {},
            [(relation, ["first", "second"])],
            [],
            planes={"first": plane([0.0, 0, 1, 4]), "second": plane(equation)},
        )
    assert caught.value.code == "infeasible-constraints"


def test_coincidence_determines_free_datum_with_reversed_fixed_alias_and_scaled_chart() -> (
    None
):
    fitted = fit_relationship_component(
        [],
        {},
        [("coincident", ["moving", "alias"])],
        [],
        planes={
            "fixed": plane([0.0, 0, 1, 4]),
            "alias": plane([0.0, 0, -1, -4], source="fixed"),
            "moving": plane([0.2, 0.1, 1, 7], free=True),
        },
        coordinate_frame=CoordinateFrame(np.array([5.0, -2, 3]), 7.0),
    )
    np.testing.assert_allclose(fitted["equations"]["moving"], [0, 0, 1, 4], atol=1e-10)
    assert fitted["equations"]["alias"] == [0, 0, -1, -4]
    assert fitted["planes"]["moving"]["point_display"] == pytest.approx([0, 0, 4])
    assert fitted["solver"]["constraint_rank"] == 4
    assert fitted["solver"]["tangent_dimension"] == 0
    assert fitted["weighted_rms"] == 0


@pytest.mark.parametrize("fixed_reference", [False, True])
def test_parallel_datums_without_evidence_reject_remaining_free_geometry(
    fixed_reference: bool,
) -> None:
    with pytest.raises(RelationshipFailure) as caught:
        _ = fit_relationship_component(
            [],
            {},
            [("parallel", ["first", "second"])],
            [],
            planes={
                "first": plane([0.0, 0, 1, 4], free=not fixed_reference),
                "second": plane([0.1, 0, 1, 9], free=True),
            },
        )
    assert caught.value.code == "rank-deficient"
    assert caught.value.diagnostic["details"]["tangent_dimension"] > 0


def test_observed_plane_and_datum_share_orientation_and_keep_weighted_offset() -> None:
    points = np.array([[0.0, 0, 2.1], [2, 0, 1.9], [0, 3, 2.2], [2, 3, 1.8]])
    weights = np.array([1.0, 2, 3, 5])
    fitted = fit_relationship_component(
        [
            SurfaceInput(
                "observed", "plane", points, weights, {"plane_equation": [0.1, 0, 1, 1]}
            )
        ],
        {},
        [("parallel", ["observed", "fixed"])],
        [],
        planes={"fixed": plane([0.0, 0, -1, -8])},
    )
    expected_offset = float(weights @ points[:, 2] / weights.sum())
    result = fitted["surfaces"]["observed"]
    np.testing.assert_allclose(
        result["plane_equation"], [0, 0, 1, expected_offset], atol=1e-10
    )
    assert fitted["equations"]["fixed"] == [0, 0, -1, -8]
    np.testing.assert_allclose(result["residuals"], points[:, 2] - expected_offset)
    assert result["weighted_rms"] == pytest.approx(
        np.sqrt(weights @ (points[:, 2] - expected_offset) ** 2 / weights.sum())
    )


def test_datum_only_axis_construction_keeps_fixed_axis_and_resolves_plane() -> None:
    axis = {"point_display": [3.0, 2, 1], "axis_display": [0.0, 0, -1]}
    fitted = fit_relationship_component(
        [],
        {"axis": AxisInput(axis, False)},
        [("coincident", ["moving", "fixed"])],
        [],
        planes={
            "moving": plane(
                [0.1, 0, -1, -7],
                free=True,
                axis="axis",
                construction="perpendicular_to_axis",
            ),
            "fixed": plane([0.0, 0, 1, 4]),
        },
    )
    assert fitted["axes"]["axis"]["point_display"] == axis["point_display"]
    assert fitted["axes"]["axis"]["axis_display"] == axis["axis_display"]
    record = fitted["planes"]["moving"]
    np.testing.assert_allclose(record["plane_equation"], [0, 0, -1, -4], atol=1e-10)
    np.testing.assert_allclose(record["point_display"], [3, 2, 4], atol=1e-10)
    assert abs(
        np.dot(record["normal_display"], record["axis_display"])
    ) == pytest.approx(1)
    assert np.linalg.norm(record["basis_u_display"]) == pytest.approx(1)
    assert np.dot(record["basis_u_display"], record["normal_display"]) == pytest.approx(
        0
    )


def test_plane_construction_does_not_freeze_unobserved_axis_position() -> None:
    with pytest.raises(RelationshipFailure) as caught:
        _ = fit_relationship_component(
            [],
            {
                "axis": AxisInput(
                    {"point_display": [3.0, 2, 1], "axis_display": [0.0, 0, 1]}, True
                )
            },
            [("coincident", ["moving", "fixed"])],
            [],
            planes={
                "moving": plane(
                    [0.1, 0, 1, 7],
                    free=True,
                    axis="axis",
                    construction="perpendicular_to_axis",
                ),
                "fixed": plane([0.0, 0, 1, 4]),
            },
        )
    assert caught.value.code == "rank-deficient"
    assert caught.value.diagnostic["details"]["tangent_dimension"] == 2


@pytest.mark.parametrize("full_plane", [False, True])
def test_orientation_only_placement_tracks_refined_axis_and_full_plane_can_move(
    full_plane: bool,
) -> None:
    angle, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 12, endpoint=False), [-2, 0, 2]
    )
    points = np.column_stack(
        (2 + 2 * np.cos(angle.ravel()), 3 + 2 * np.sin(angle.ravel()), height.ravel())
    )
    axis = {"point_display": [1.8, 2.7, 0.0], "axis_display": [0.0, 0, 1]}
    fitted = fit_relationship_component(
        [
            SurfaceInput(
                "side",
                "cylinder",
                points,
                np.ones(len(points)),
                {
                    "kind": "cylinder",
                    "analytic": {
                        "kind": "cylinder",
                        "origin": [1.8, 2.7, 0],
                        "axis": [0, 0, 1],
                        "radius": 2.0,
                        "slope": 0.0,
                        "axial_domain": [-2, 2],
                    },
                    "axial_domain": [-2, 2],
                },
                axis="axis",
            )
        ],
        {"axis": AxisInput(axis, True)},
        [("coincident" if full_plane else "parallel", ["moving", "fixed"])],
        [],
        planes={
            "moving": plane(
                [1.0, 0, 0, 5.8],
                free=True,
                axis="axis",
                construction="parallel_to_axis",
                offset_from_axis=None if full_plane else 4.0,
            ),
            "fixed": plane([1.0, 0, 0, 9]),
        },
    )
    np.testing.assert_allclose(
        fitted["axes"]["axis"]["point_display"], [2, 3, 0], atol=1e-9
    )
    assert fitted["equations"]["moving"][3] == pytest.approx(
        9 if full_plane else 6, abs=1e-9
    )
    assert fitted["planes"]["moving"]["offset"] == pytest.approx(
        7 if full_plane else 4, abs=1e-9
    )


@pytest.mark.parametrize(
    "relationships",
    [
        [("parallel", ["fixed", "missing"])],
        [("angle", ["fixed", "other"])],
        [("parallel", ["fixed"])],
        [("parallel", ["fixed", "fixed"])],
    ],
)
def test_invalid_plane_relationship_endpoints_have_clear_failures(
    relationships: list[tuple[str, list[str]]],
) -> None:
    with pytest.raises(RelationshipFailure):
        _ = fit_relationship_component(
            [],
            {},
            relationships,
            [],
            planes={"fixed": plane([0.0, 0, 1, 4]), "other": plane([0.0, 0, 1, 9])},
        )


def test_observation_free_component_still_enforces_callback_budget() -> None:
    control = AttemptControl(AttemptLimits(max_evaluations=1))
    with pytest.raises(FitBudgetExhausted):
        _ = fit_relationship_component(
            [],
            {},
            [("parallel", ["first", "second"])],
            [],
            planes={"first": plane([0.0, 0, 1, 4]), "second": plane([0.0, 0, 1, 9])},
            control=control,
        )
    assert control.evaluations == 1
