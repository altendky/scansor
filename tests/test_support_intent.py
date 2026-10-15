"""Independent equations for persistent support coordinates and rigid reuse."""

import json
from dataclasses import FrozenInstanceError
from typing import Any, cast

import numpy as np
import pytest

from experiments.selection_growth import fit_seed
from experiments.support_intent import (
    PlaneSupport,
    PointSupport,
    SelectionSupport,
    SupportIntentFailure,
    legacy_support,
    parse_support_record,
    resolve_support,
    support_from_result,
    support_record,
    transform_support,
)


def endpoints(resolved: Any) -> np.ndarray:
    return np.asarray(resolved.origin) + np.asarray(resolved.domain)[
        :, None
    ] * np.asarray(resolved.axis)


def plane_endpoints(
    normal: np.ndarray,
    offset: float,
    interval: tuple[float, float],
    point: np.ndarray,
    direction: np.ndarray,
) -> np.ndarray:
    direction = direction / np.linalg.norm(direction)
    if normal @ direction < 0:
        direction = -direction
    intersection = point + (offset - normal @ point) / (normal @ direction) * direction
    return intersection + np.asarray(interval)[:, None] * direction


@pytest.mark.parametrize("reverse", [False, True])
def test_legacy_z0_is_a_plane_and_not_a_frozen_intersection_point(
    reverse: bool,
) -> None:
    point = np.array([1.0, -2, 3])
    axis = np.array([0.2, -0.3, 1.0])
    axis /= np.linalg.norm(axis)
    if reverse:
        axis = -axis
    selected = np.array([[0.0, 0, 0], [2, 1, 4]])
    intent = legacy_support((-2.0, 5.0))
    assert intent == PlaneSupport((0.0, 0.0, 1.0), 0.0, (-2.0, 5.0))
    resolved = resolve_support(intent, point, axis, selected)
    expected = plane_endpoints(np.array([0.0, 0, 1]), 0, (-2.0, 5.0), point, axis)
    np.testing.assert_allclose(endpoints(resolved), expected, atol=1e-14)
    np.testing.assert_array_equal(resolved.origin, point)
    assert resolved.origin_shift == 0
    assert resolved.axis_sign == (-1 if reverse else 1)


def test_row_rigid_transform_composition_preserves_physical_plane_endpoints() -> None:
    intent = PlaneSupport((0.0, 0, 1), 0.0, (-2.0, 5.0))
    point, axis = np.array([1.0, -2, 3]), np.array([0.0, 0, 1])
    first = np.array([[0.0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    second = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    t1, t2 = np.array([7.0, 8, 9]), np.array([-2.0, 3, 4])
    original = resolve_support(intent, point, axis, np.array([point]))
    moved = transform_support(intent, first, t1)
    assert isinstance(moved, PlaneSupport)
    np.testing.assert_allclose(moved.normal, [-1, 0, 0], atol=1e-15)
    assert moved.offset == -7
    twice = transform_support(moved, second, t2)
    composed = transform_support(intent, first @ second, t1 @ second + t2)
    assert twice == composed
    transformed_point = (point @ first + t1) @ second + t2
    transformed_axis = axis @ first @ second
    resolved = resolve_support(
        twice, transformed_point, transformed_axis, np.array([transformed_point])
    )
    np.testing.assert_allclose(
        endpoints(resolved),
        (endpoints(original) @ first + t1) @ second + t2,
        atol=1e-14,
    )


def test_refined_axis_intersects_original_plane_instead_of_projecting_old_point() -> (
    None
):
    normal = np.array([-1.0, 0, 0])
    intent = PlaneSupport(tuple(normal), -7.0, (8.0, 15.0))
    old_point = np.array([4.0, 6, 10])
    old_axis = np.array([-1.0, 0, 0])
    old_intersection = old_point + (-7 - normal @ old_point) * old_axis
    point = old_point + np.array([0.2, 0.4, 0.7])
    direction = old_axis + np.array([0, 0.12, 0.18])
    direction /= np.linalg.norm(direction)
    resolved = resolve_support(intent, point, direction, np.array([point]))
    expected = plane_endpoints(normal, -7, (8.0, 15.0), point, direction)
    np.testing.assert_allclose(endpoints(resolved), expected, atol=1e-14)
    new_intersection = endpoints(resolved)[0] - 8 * np.asarray(resolved.axis)
    assert normal @ new_intersection == pytest.approx(-7, abs=1e-14)
    projected_old = point + ((old_intersection - point) @ direction) * direction
    assert abs(normal @ projected_old + 7) > 0.01
    # Negative reference radius is valid if the finite declared patch is positive.
    assert np.all(-1 + 0.5 * np.asarray(resolved.domain) > 0)


@pytest.mark.parametrize("reverse", [False, True])
def test_point_support_rebases_radius_and_taper_using_incoming_axis(
    reverse: bool,
) -> None:
    intent = PointSupport((1.0, 2, 0), (0.0, 0, 1), (2.0, 5.0))
    point = np.array([1.0, 2, 7])
    axis = np.array([0.0, 0, -1 if reverse else 1])
    old_slope = -0.5 if reverse else 0.5
    old_radius = 3.0
    resolved = resolve_support(intent, point, axis, np.array([point]))
    assert resolved.origin_shift == (7 if reverse else -7)
    assert resolved.axis_sign == (-1 if reverse else 1)
    radius = old_radius + old_slope * resolved.origin_shift
    slope = old_slope * resolved.axis_sign
    assert radius == -0.5 and slope == 0.5
    np.testing.assert_array_equal(resolved.origin, [1, 2, 0])
    np.testing.assert_array_equal(endpoints(resolved), [[1, 2, 2], [1, 2, 5]])
    assert np.all(radius + slope * np.asarray(resolved.domain) > 0)


def test_selection_reference_is_projected_but_extent_is_from_current_observations() -> (
    None
):
    point, axis = np.array([2.0, 3, 8]), np.array([0.0, 0, 1])
    selected = np.array([[1.0, 0, -4], [4, 1, 6], [7, 3, 2]])
    automatic = legacy_support(None)
    assert automatic == SelectionSupport()
    resolved = resolve_support(automatic, point, axis, selected, reference=(1.0, 2, 1))
    np.testing.assert_array_equal(resolved.origin, [2, 3, 1])
    np.testing.assert_array_equal(resolved.domain, [-5, 5])
    assert transform_support(automatic, np.eye(3), np.ones(3)) == automatic
    explicit = resolve_support(SelectionSupport((0.0, 0, 2)), point, axis, selected)
    np.testing.assert_array_equal(explicit.origin, [2, 3, 2])
    np.testing.assert_array_equal(explicit.domain, [-6, 4])


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "selection"},
        {"kind": "selection", "reference": [1.0, 2, 3]},
        {
            "kind": "reference-plane",
            "normal": [0.0, 0, 2],
            "offset": 6.0,
            "interval": [-2.0, 5],
        },
        {
            "kind": "point",
            "reference": [1.0, 2, 3],
            "direction": [0.0, 0, 2],
            "interval": [-2.0, 5],
        },
    ],
)
def test_serialization_is_immutable_copied_and_strict_json(
    record: dict[str, Any],
) -> None:
    before = json.dumps(record, allow_nan=False)
    intent = parse_support_record(record)
    assert json.dumps(record, allow_nan=False) == before
    encoded = support_record(intent)
    assert (
        parse_support_record(json.loads(json.dumps(encoded, allow_nan=False))) == intent
    )
    if "interval" in encoded:
        encoded["interval"][0] = -999
        assert support_record(intent)["interval"] == [-2.0, 5.0]
    if isinstance(intent, PlaneSupport):
        assert isinstance(intent.normal, tuple) and isinstance(intent.interval, tuple)
        assert intent.offset == 3.0
        with pytest.raises(FrozenInstanceError):
            cast(Any, intent).offset = 10
    assert (
        support_from_result(
            {"support_intent": support_record(intent), "support_origin": "bogus"}
        )
        == intent
    )


def test_legacy_result_adapters_preserve_distinct_modes() -> None:
    assert support_from_result(
        {"support_origin": "legacy-source-z0", "source_axial_domain": [-2.0, 5]}
    ) == legacy_support((-2.0, 5.0))
    assert (
        support_from_result(
            {"support_origin": "selection", "anchor_reference": [1.0, 2, 3]}
        )
        == SelectionSupport()
    )
    assert support_from_result(
        {
            "support_origin": "point",
            "anchor_reference": [1.0, 2, 3],
            "anchor_axis": [0.0, 0, 1],
            "anchor_axial_domain": [-2.0, 5],
            "analytic": {
                "origin": [1.0, 2, 3],
                "axis": [0.0, 0, 1],
                "axial_domain": [-2.0, 5],
            },
        }
    ) == PointSupport((1.0, 2, 3), (0.0, 0, 1), (-2.0, 5.0))


def test_mutable_constructor_inputs_are_copied_before_normalizing() -> None:
    normal, interval = [0.0, 0.0, 2.0], [-2.0, 5.0]
    intent = PlaneSupport(cast(Any, normal), 6.0, cast(Any, interval))
    normal[2], interval[0] = 99, -99
    assert intent == PlaneSupport((0.0, 0.0, 1.0), 3.0, (-2.0, 5.0))
    direction, reference = [0.0, 0.0, 2.0], [1.0, 2.0, 3.0]
    point = PointSupport(cast(Any, reference), cast(Any, direction), (-2.0, 5.0))
    direction[2], reference[0] = 0, 99
    assert point == PointSupport((1.0, 2.0, 3.0), (0.0, 0.0, 1.0), (-2.0, 5.0))


def test_invalid_authoritative_record_never_falls_back_to_valid_legacy_aliases() -> (
    None
):
    with pytest.raises(SupportIntentFailure) as failure:
        _ = support_from_result(
            {
                "support_intent": {"kind": "invalid"},
                "support_origin": "legacy-source-z0",
                "source_axial_domain": [0, 1],
            }
        )
    assert failure.value.code == "invalid-support"


@pytest.mark.parametrize("mode", ["reference-plane", "bogus"])
def test_missing_or_unknown_support_carrier_cannot_reinterpret_geometry_as_point(
    mode: str,
) -> None:
    with pytest.raises(SupportIntentFailure) as failure:
        _ = support_from_result(
            {
                "support_origin": mode,
                "analytic": {
                    "kind": "cone",
                    "origin": [1.0, 2.0, 3.0],
                    "axis": [0.0, 0.0, 1.0],
                    "radius": 2.0,
                    "slope": 0.1,
                    "axial_domain": [-2.0, 5.0],
                },
            }
        )
    assert failure.value.code == "invalid-support"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("kind", ["cylinder", "plane"])
def test_unbound_fit_route_rejects_authored_support_instead_of_discarding_it(
    kind: str,
) -> None:
    first, second = np.meshgrid([-1.0, 0.0, 1.0], [-1.0, 0.0, 1.0])
    if kind == "plane":
        points = np.column_stack((first.ravel(), second.ravel(), np.zeros(first.size)))
        normals = np.tile([0.0, 0.0, 1.0], (len(points), 1))
    else:
        angle = first.ravel()
        normals = np.column_stack((np.cos(angle), np.sin(angle), np.zeros(first.size)))
        points = 2 * normals + np.column_stack(
            (np.zeros(first.size), np.zeros(first.size), second.ravel())
        )
    with pytest.raises(SupportIntentFailure) as failure:
        _ = fit_seed(
            points,
            np.ones(len(points)),
            normals,
            kind,
            None,
            None,
            binding=None,
            support=PlaneSupport((0.0, 0.0, 1.0), 0.0, (-1.0, 1.0)),
        )
    assert failure.value.code == "unsupported-support"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "bogus"},
        {"kind": "selection", "reference": [True, 0, 0]},
        {"kind": "selection", "reference": ["1", 0, 0]},
        {"kind": "selection", "unexpected": 1},
        {
            "kind": "reference-plane",
            "normal": [0, 0, 0],
            "offset": 0,
            "interval": [0, 1],
        },
        {
            "kind": "reference-plane",
            "normal": [0, 0, 1],
            "offset": float("nan"),
            "interval": [0, 1],
        },
        {
            "kind": "reference-plane",
            "normal": [0, 0, 1],
            "offset": 0,
            "interval": [1, 1],
        },
        {
            "kind": "point",
            "reference": [0, 0, float("inf")],
            "direction": [0, 0, 1],
            "interval": [0, 1],
        },
        {
            "kind": "point",
            "reference": [0, 0, 0],
            "direction": [0, 0, 0],
            "interval": [0, 1],
        },
    ],
)
def test_invalid_intent_is_a_typed_strict_json_failure(record: dict[str, Any]) -> None:
    with pytest.raises(SupportIntentFailure) as failure:
        _ = parse_support_record(record)
    assert failure.value.code == "invalid-support"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("axis", [(1.0, 0, 0), (1.0, 0, 1e-10)])
def test_parallel_plane_never_silently_changes_to_a_point_reference(
    axis: tuple[float, ...],
) -> None:
    with pytest.raises(SupportIntentFailure) as failure:
        _ = resolve_support(
            legacy_support((0.0, 1.0)), (0.0, 0, 1), axis, np.zeros((2, 3))
        )
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_finite_input_span_that_collapses_after_rebasing_is_rejected() -> None:
    with pytest.raises(SupportIntentFailure):
        _ = resolve_support(
            PlaneSupport((0.0, 0, 1), 1e20, (0.0, 1.0)),
            (0.0, 0, 0),
            (0.0, 0, 1),
            np.zeros((2, 3)),
        )


@pytest.mark.parametrize(
    "rotation", [np.diag([-1.0, 1, 1]), np.diag([2.0, 1, 1]), np.full((3, 3), np.nan)]
)
def test_support_transform_requires_proper_rigid_motion(rotation: np.ndarray) -> None:
    with pytest.raises(SupportIntentFailure):
        _ = transform_support(legacy_support((0.0, 1.0)), rotation, np.zeros(3))
