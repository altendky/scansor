from __future__ import annotations

import copy
import math
from collections.abc import Iterator
from typing import Any

import pytest

from scansor.errors import ScansorError
from scansor.geometry_evaluator import (
    classify_declared_support,
    evaluate_fixed_pose_shape_support,
)
from scansor.model_declarations import (
    ModelDeclaration,
    ModelSemanticDeclaration,
    identify_model,
)
from scansor.stepped_model_declarations import stepped_model_declaration


def _shape(declaration: ModelDeclaration) -> tuple[float, ...]:
    return tuple(item.nominal for item in declaration.parameters)


def _element_cases() -> tuple[
    tuple[
        str,
        tuple[float, float, float],
        float,
        tuple[float, float, float],
        int | None,
        float,
    ],
    ...,
]:
    return (
        ("cylinder.band-1", (0.013, 0.0, 0.010), 0.001, (1.0, 0.0, 0.0), 0, -1.0),
        ("cylinder.band-2", (0.0, 0.019, 0.035), 0.001, (0.0, 1.0, 0.0), 1, -1.0),
        ("cylinder.band-3", (-0.015, 0.0, 0.065), 0.001, (-1.0, 0.0, 0.0), 2, -1.0),
        ("plane.station-0", (0.006, 0.0, 0.001), -0.001, (0.0, 0.0, -1.0), None, 0.0),
        ("plane.station-20", (0.0, 0.015, 0.021), -0.001, (0.0, 0.0, -1.0), 3, 1.0),
        ("plane.station-50", (0.0, 0.016, 0.049), -0.001, (0.0, 0.0, 1.0), 4, -1.0),
        ("plane.station-80", (0.007, 0.0, 0.081), 0.001, (0.0, 0.0, 1.0), 5, -1.0),
        ("plane.datum-flat", (0.017, 0.0, 0.035), 0.001, (1.0, 0.0, 0.0), 6, -1.0),
    )


@pytest.mark.parametrize(
    ("element_id", "point", "residual", "gradient", "column", "coefficient"),
    _element_cases(),
)
def test_every_stepped_support_matches_declared_semantics(
    element_id: str,
    point: tuple[float, float, float],
    residual: float,
    gradient: tuple[float, float, float],
    column: int | None,
    coefficient: float,
) -> None:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    shape = _shape(declaration)

    support = classify_declared_support(declaration, element_id, point, shape)
    differential = evaluate_fixed_pose_shape_support(
        declaration, element_id, point, shape
    )

    assert support.signed_distance_m == pytest.approx(residual)
    assert support.projected_inside
    assert support.projected_point_m is not None
    assert support.boundary_clearance_m is not None
    assert support.boundary_clearance_m >= 0.0
    assert all(item.inside for item in support.predicate_margins)
    assert differential.residual_m == pytest.approx(residual)
    assert differential.point_gradient == pytest.approx(gradient)
    expected = [0.0] * len(shape)
    if column is not None:
        expected[column] = coefficient
    assert differential.parameter_jacobian_row == pytest.approx(expected)


def test_axisymmetric_declaration_evaluates_its_complete_element_inventory() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    shape = _shape(declaration)
    for (
        element_id,
        point,
        residual,
        _gradient,
        _column,
        _coefficient,
    ) in _element_cases()[:-1]:
        support = classify_declared_support(declaration, element_id, point, shape)
        differential = evaluate_fixed_pose_shape_support(
            declaration, element_id, point, shape
        )
        assert support.projected_inside
        assert support.signed_distance_m == pytest.approx(residual)
        assert differential.residual_m == pytest.approx(residual)


def _finite_difference_row(
    declaration: ModelDeclaration,
    element_id: str,
    point: tuple[float, float, float],
    shape: tuple[float, ...],
    step: float = 1e-8,
) -> tuple[float, ...]:
    values: list[float] = []
    for index in range(len(shape)):
        lower = list(shape)
        upper = list(shape)
        lower[index] -= step
        upper[index] += step
        before = evaluate_fixed_pose_shape_support(
            declaration, element_id, point, tuple(lower)
        ).residual_m
        after = evaluate_fixed_pose_shape_support(
            declaration, element_id, point, tuple(upper)
        ).residual_m
        values.append((after - before) / (2.0 * step))
    return tuple(values)


@pytest.mark.parametrize(
    "shape",
    (
        (0.012, 0.018, 0.014, 0.020, 0.050, 0.080, 0.016),
        (0.0122, 0.0184, 0.0138, 0.0205, 0.0508, 0.0805, 0.0157),
        (0.0100001, 0.0199999, 0.0120001, 0.0180001, 0.0529999, 0.0829999, 0.0150001),
    ),
    ids=("nominal", "perturbed", "legal-near-bound"),
)
@pytest.mark.parametrize(
    ("element_id", "point"),
    tuple((item[0], item[1]) for item in _element_cases()),
)
def test_parameter_jacobians_match_independent_finite_differences(
    shape: tuple[float, ...],
    element_id: str,
    point: tuple[float, float, float],
) -> None:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    analytic = evaluate_fixed_pose_shape_support(
        declaration, element_id, point, shape
    ).parameter_jacobian_row

    assert analytic == pytest.approx(
        _finite_difference_row(declaration, element_id, point, shape), abs=1e-8
    )


def test_domain_predicates_expose_signed_boundary_margins() -> None:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    shape = _shape(declaration)

    cylinder = classify_declared_support(
        declaration, "cylinder.band-2", (0.019, 0.0, 0.035), shape
    )
    assert not cylinder.projected_inside
    assert cylinder.predicate_margins[0].margins_m == pytest.approx((0.015, 0.015))
    assert cylinder.predicate_margins[1].margins_m == pytest.approx((-0.002,))
    assert cylinder.boundary_clearance_m == pytest.approx(-0.002)

    datum = classify_declared_support(
        declaration, "plane.datum-flat", (0.017, 0.008, 0.035), shape
    )
    half_width = math.sqrt(0.018**2 - 0.016**2)
    assert datum.predicate_margins[0].margins_m == pytest.approx((0.015, 0.015))
    assert datum.predicate_margins[1].margins_m == pytest.approx(
        (0.008 + half_width, half_width - 0.008)
    )
    assert datum.boundary_clearance_m == pytest.approx(half_width - 0.008)


def test_closed_domain_boundaries_are_inclusive_without_clamping() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    shape = _shape(declaration)
    support = classify_declared_support(
        declaration, "cylinder.band-1", (0.013, 0.0, 0.0), shape
    )

    assert support.projected_inside
    assert support.boundary_clearance_m == 0.0
    assert support.projected_point_m == pytest.approx((0.012, 0.0, 0.0))


def _replace_strings(value: object, replacements: dict[str, str]) -> object:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, tuple):
        return tuple(_replace_strings(item, replacements) for item in value)
    if isinstance(value, dict):
        return {
            key: _replace_strings(item, replacements) for key, item in value.items()
        }
    return value


def _renamed_reordered_declaration() -> ModelDeclaration:
    source = stepped_model_declaration("axisymmetric")
    parameter_replacements = {
        "r1": "width-a",
        "r2": "width-b",
        "r3": "width-c",
        "s20": "position-a",
        "s50": "position-b",
        "s80": "position-c",
    }
    element_replacements = {
        element.element_id: f"support-{index}"
        for index, element in enumerate(source.elements)
    }
    record = _replace_strings(
        copy.deepcopy(source.model_dump(mode="python", exclude={"model_id"})),
        parameter_replacements | element_replacements,
    )
    assert isinstance(record, dict)
    parameters = tuple(reversed(record["parameters"]))
    record["parameters"] = parameters
    parameter_ids = tuple(item["parameter_id"] for item in parameters)
    record["problem"]["varied_parameter_ids"] = parameter_ids
    for context in ("mapping_admission", "optimization_preflight"):
        rank = record[context]["relative_rank"]
        scales = dict(zip(rank["parameter_ids"], rank["parameter_scales"], strict=True))
        rank["parameter_ids"] = parameter_ids
        rank["parameter_scales"] = tuple(scales[item] for item in parameter_ids)
    record["elements"][0]["primitive"]["radial_orientation"] = -1
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def test_dispatch_uses_references_not_names_or_fixed_parameter_indices() -> None:
    declaration = _renamed_reordered_declaration()
    shape = _shape(declaration)
    evaluation = evaluate_fixed_pose_shape_support(
        declaration, "support-0", (0.013, 0.0, 0.010), shape
    )

    radius_index = next(
        index
        for index, parameter in enumerate(declaration.parameters)
        if parameter.parameter_id == "width-a"
    )
    expected = [0.0] * len(shape)
    expected[radius_index] = 1.0
    assert evaluation.residual_m == pytest.approx(-0.001)
    assert evaluation.point_gradient == pytest.approx((-1.0, 0.0, 0.0))
    assert evaluation.parameter_jacobian_row == pytest.approx(expected)


def test_jacobian_propagates_through_right_triangle_relationship() -> None:
    source = stepped_model_declaration("asymmetric-datum-flat")
    record: dict[str, Any] = copy.deepcopy(
        source.model_dump(mode="python", exclude={"model_id"})
    )
    record["elements"][0]["primitive"]["radius"] = {
        "kind": "relationship",
        "relationship_id": "length.datum-half-width",
    }
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    shape = _shape(declaration)
    half_width = math.sqrt(shape[1] ** 2 - shape[6] ** 2)
    point = (half_width + 0.001, 0.0, 0.010)

    evaluation = evaluate_fixed_pose_shape_support(
        declaration, "cylinder.band-1", point, shape
    )
    expected = [0.0] * len(shape)
    expected[1] = -shape[1] / half_width
    expected[6] = shape[6] / half_width
    assert evaluation.residual_m == pytest.approx(0.001)
    assert evaluation.parameter_jacobian_row == pytest.approx(expected)
    assert evaluation.parameter_jacobian_row == pytest.approx(
        _finite_difference_row(declaration, "cylinder.band-1", point, shape),
        abs=1e-8,
    )


def test_coaxial_geometry_uses_declared_axis_and_origin() -> None:
    source = stepped_model_declaration("axisymmetric")
    record: dict[str, Any] = copy.deepcopy(
        source.model_dump(mode="python", exclude={"model_id"})
    )
    record["frame"]["origin_m"] = (1.0, 2.0, 3.0)
    record["frame"]["positive_x"] = (1.0, 0.0, 0.0)
    record["frame"]["positive_z"] = (0.0, 1.0, 0.0)
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))

    support = classify_declared_support(
        declaration,
        "cylinder.band-1",
        (1.013, 2.010, 3.0),
        _shape(declaration),
    )
    differential = evaluate_fixed_pose_shape_support(
        declaration,
        "cylinder.band-1",
        (1.013, 2.010, 3.0),
        _shape(declaration),
    )
    assert support.signed_distance_m == pytest.approx(0.001)
    assert support.projected_point_m == pytest.approx((1.012, 2.010, 3.0))
    assert support.projected_inside
    assert differential.point_gradient == pytest.approx((1.0, 0.0, 0.0))


def test_cylinder_axis_has_distance_but_no_projection_or_gradient() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    shape = _shape(declaration)

    support = classify_declared_support(
        declaration, "cylinder.band-1", (0.0, 0.0, 0.010), shape
    )
    assert support.signed_distance_m == pytest.approx(-0.012)
    assert support.projected_point_m is None
    assert not support.projected_inside
    assert support.boundary_clearance_m is None
    assert support.predicate_margins == ()
    with pytest.raises(ScansorError, match="undefined point gradient"):
        _ = evaluate_fixed_pose_shape_support(
            declaration, "cylinder.band-1", (0.0, 0.0, 0.010), shape
        )


def _invalid_vectors(declaration: ModelDeclaration) -> Iterator[tuple[float, ...]]:
    shape = _shape(declaration)
    yield shape[:-1]
    yield (*shape[:-1], math.nan)
    yield (0.1, *shape[1:])


def test_invalid_inputs_and_declarations_fail_closed() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    shape = _shape(declaration)
    with pytest.raises(ScansorError, match="finite three-vector"):
        _ = classify_declared_support(
            declaration, "cylinder.band-1", (math.inf, 0.0, 0.0), shape
        )
    with pytest.raises(ScansorError, match="does not declare element"):
        _ = classify_declared_support(declaration, "missing", (0.02, 0.0, 0.01), shape)
    for invalid in _invalid_vectors(declaration):
        with pytest.raises(ScansorError, match="invalid runtime parameter vector"):
            _ = evaluate_fixed_pose_shape_support(
                declaration, "cylinder.band-1", (0.02, 0.0, 0.01), invalid
            )

    malformed_element = declaration.elements[0].model_copy(
        update={"primitive": {"kind": "unsupported"}}
    )
    malformed = declaration.model_copy(
        update={"elements": (malformed_element, *declaration.elements[1:])}
    )
    with (
        pytest.warns(UserWarning, match="Pydantic serializer warnings"),
        pytest.raises(ScansorError, match="invalid model declaration"),
    ):
        _ = classify_declared_support(
            malformed,  # type: ignore[arg-type]
            "cylinder.band-1",
            (0.02, 0.0, 0.01),
            shape,
        )


def test_in_bounds_structurally_invalid_vector_fails_before_evaluation() -> None:
    source = stepped_model_declaration("axisymmetric")
    record: dict[str, Any] = copy.deepcopy(
        source.model_dump(mode="python", exclude={"model_id"})
    )
    record["parameters"][1]["lower"] = 0.012
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    invalid = list(_shape(declaration))
    invalid[1] = 0.012

    with pytest.raises(ScansorError, match="invalid runtime parameter vector"):
        _ = evaluate_fixed_pose_shape_support(
            declaration, "cylinder.band-1", (0.02, 0.0, 0.01), tuple(invalid)
        )
