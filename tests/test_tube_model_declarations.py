from __future__ import annotations

from collections import Counter

import pytest

from scansor.declared_synthetic_fixtures import fixture_definition
from scansor.errors import ScansorError
from scansor.geometry_evaluator import (
    classify_declared_support,
    evaluate_fixed_pose_shape_support,
)
from scansor.model_declarations import (
    AxialIntervalPredicate,
    CoaxialCylinderPrimitive,
    OrientedOffsetRelationship,
    OrientedPlanePrimitive,
    ParameterScalarReference,
    RadialIntervalPredicate,
    RelationshipScalarReference,
    validate_runtime_parameter_structure,
    validate_runtime_parameter_vector,
)
from scansor.tube_model_declarations import (
    ELEMENT_IDS,
    PARAMETER_IDS,
    PARAMETER_SCALES,
    tube_model_declaration,
)


def _nominal() -> tuple[float, float, float]:
    declaration = tube_model_declaration()
    values = tuple(parameter.nominal for parameter in declaration.parameters)
    assert len(values) == 3
    return values[0], values[1], values[2]


def test_tube_declaration_has_exact_dimensions_and_bounded_topology() -> None:
    declaration = tube_model_declaration()

    assert tuple(parameter.parameter_id for parameter in declaration.parameters) == (
        "bore-radius",
        "outside-radius",
        "axial-length",
    )
    assert tuple(
        (parameter.lower, parameter.nominal, parameter.upper)
        for parameter in declaration.parameters
    ) == (
        (0.006, 0.008, 0.010),
        (0.011, 0.013, 0.016),
        (0.045, 0.060, 0.075),
    )
    assert (
        tuple(parameter.diagnostic_scale for parameter in declaration.parameters)
        == PARAMETER_SCALES
    )
    assert tuple(element.element_id for element in declaration.elements) == ELEMENT_IDS

    outer, bore, near, far = declaration.elements
    assert isinstance(outer.primitive, CoaxialCylinderPrimitive)
    assert outer.primitive.radial_orientation == 1
    assert outer.primitive.radius == ParameterScalarReference(
        parameter_id="outside-radius"
    )
    assert isinstance(bore.primitive, CoaxialCylinderPrimitive)
    assert bore.primitive.radial_orientation == -1
    assert bore.primitive.radius == ParameterScalarReference(parameter_id="bore-radius")
    assert isinstance(near.primitive, OrientedPlanePrimitive)
    assert near.primitive.normal == (0.0, 0.0, -1.0)
    assert isinstance(far.primitive, OrientedPlanePrimitive)
    assert far.primitive.normal == (0.0, 0.0, 1.0)
    assert far.primitive.offset == RelationshipScalarReference(
        relationship_id="offset.end.far"
    )

    assert len(declaration.relationships) == 1
    relationship = declaration.relationships[0]
    assert isinstance(relationship, OrientedOffsetRelationship)
    assert (
        relationship.parameter_id,
        relationship.coefficient,
        relationship.constant,
    ) == ("axial-length", 1.0, 0.0)

    for element in (outer, bore):
        assert len(element.domain.predicates) == 1
        predicate = element.domain.predicates[0]
        assert isinstance(predicate, AxialIntervalPredicate)
        assert predicate.upper == ParameterScalarReference(parameter_id="axial-length")
    for element in (near, far):
        assert len(element.domain.predicates) == 1
        predicate = element.domain.predicates[0]
        assert isinstance(predicate, RadialIntervalPredicate)
        assert predicate.lower == ParameterScalarReference(parameter_id="bore-radius")
        assert predicate.upper == ParameterScalarReference(
            parameter_id="outside-radius"
        )


@pytest.mark.parametrize(
    "values",
    (
        (0.006, 0.011, 0.045),
        (0.010, 0.016, 0.075),
    ),
)
def test_tube_parameter_bounds_are_inclusive(
    values: tuple[float, float, float],
) -> None:
    declaration = tube_model_declaration()
    validated = validate_runtime_parameter_vector(declaration, values)
    assert tuple(value for _, value in validated) == values


@pytest.mark.parametrize(
    "values",
    (
        (0.005999, 0.013, 0.060),
        (0.008, 0.016001, 0.060),
        (0.008, 0.013, 0.044999),
        (0.008, 0.013, 0.075001),
    ),
)
def test_tube_parameter_vectors_outside_bounds_fail_closed(
    values: tuple[float, float, float],
) -> None:
    with pytest.raises(ScansorError, match="outside inclusive bounds"):
        _ = validate_runtime_parameter_vector(tube_model_declaration(), values)


@pytest.mark.parametrize(
    ("values", "message"),
    (
        ((0.010, 0.011, 0.060), "structural predicate"),
        ((0.010, 0.012, 0.060), "structural predicate"),
        ((0.008, 0.013, 0.040), "structural predicate"),
        ((0.0, 0.013, 0.060), "nonpositive radius"),
    ),
)
def test_tube_structurally_invalid_vectors_fail_closed(
    values: tuple[float, float, float],
    message: str,
) -> None:
    with pytest.raises(ScansorError, match=message):
        _ = validate_runtime_parameter_structure(tube_model_declaration(), values)


@pytest.mark.parametrize(
    ("element_id", "point", "residual", "gradient", "jacobian"),
    (
        (
            "wall.outer",
            (0.014, 0.0, 0.030),
            0.001,
            (1.0, 0.0, 0.0),
            (0.0, -1.0, 0.0),
        ),
        (
            "wall.bore",
            (0.007, 0.0, 0.030),
            0.001,
            (-1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0),
        ),
        (
            "end.near",
            (0.010, 0.0, -0.001),
            0.001,
            (0.0, 0.0, -1.0),
            (0.0, 0.0, 0.0),
        ),
        (
            "end.far",
            (0.010, 0.0, 0.061),
            0.001,
            (0.0, 0.0, 1.0),
            (0.0, 0.0, -1.0),
        ),
    ),
)
def test_every_tube_support_has_declared_geometry_and_jacobian(
    element_id: str,
    point: tuple[float, float, float],
    residual: float,
    gradient: tuple[float, float, float],
    jacobian: tuple[float, float, float],
) -> None:
    declaration = tube_model_declaration()
    support = classify_declared_support(declaration, element_id, point, _nominal())
    evaluation = evaluate_fixed_pose_shape_support(
        declaration, element_id, point, _nominal()
    )

    assert support.projected_inside
    assert support.boundary_clearance_m is not None
    assert support.boundary_clearance_m >= 0.0
    assert support.signed_distance_m == pytest.approx(residual)
    assert evaluation.residual_m == pytest.approx(residual)
    assert evaluation.point_gradient == pytest.approx(gradient)
    assert evaluation.parameter_jacobian_row == pytest.approx(jacobian)


def test_tube_policy_is_three_parameter_ranked_and_context_specific() -> None:
    declaration = tube_model_declaration()
    mapping = declaration.mapping_admission
    preflight = declaration.optimization_preflight

    assert declaration.problem.varied_parameter_ids == PARAMETER_IDS
    assert mapping.relative_rank.parameter_ids == PARAMETER_IDS
    assert preflight.relative_rank.parameter_ids == PARAMETER_IDS
    assert mapping.relative_rank.parameter_scales == PARAMETER_SCALES
    assert preflight.relative_rank.parameter_scales == PARAMETER_SCALES
    assert mapping.relative_rank.required_rank == 3
    assert preflight.relative_rank.required_rank == 3
    assert tuple(item.minimum_count for item in mapping.required_support) == (
        12,
        12,
        8,
        8,
    )
    assert tuple(item.minimum_count for item in mapping.coverage_cells) == (8, 8, 4, 4)
    assert tuple(item.minimum_count for item in preflight.required_support) == (
        6,
        6,
        4,
        4,
    )
    assert tuple(item.minimum_count for item in preflight.coverage_cells) == (
        4,
        4,
        2,
        2,
    )

    rows = tuple(
        evaluate_fixed_pose_shape_support(
            declaration, element_id, point, _nominal()
        ).parameter_jacobian_row
        for element_id, point in (
            ("wall.outer", (0.013, 0.0, 0.030)),
            ("wall.bore", (0.008, 0.0, 0.030)),
            ("end.near", (0.010, 0.0, 0.0)),
            ("end.far", (0.010, 0.0, 0.060)),
        )
    )
    assert rows == (
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
    )


def test_declared_fixtures_preserve_roles_guards_and_canonical_order() -> None:
    stepped = fixture_definition("asymmetric-stepped-v1")
    tube = fixture_definition("coaxial-tube-v1")

    assert Counter(sample.role for sample in stepped.samples) == {
        "training": 230,
        "held-out": 87,
    }
    assert Counter(sample.role for sample in tube.samples) == {
        "training": 140,
        "held-out": 52,
    }
    assert Counter(
        sample.element_id for sample in tube.samples if sample.role == "training"
    ) == {
        "wall.outer": 56,
        "wall.bore": 56,
        "end.near": 14,
        "end.far": 14,
    }

    for fixture in (stepped, tube):
        order = {
            element.element_id: index
            for index, element in enumerate(fixture.declaration.elements)
        }
        ordering = tuple(
            (order[sample.element_id], sample.key) for sample in fixture.samples
        )
        assert ordering == tuple(sorted(ordering))

    evaluator_shape = tuple(
        parameter.nominal for parameter in tube.declaration.parameters
    )
    for sample in tube.samples:
        support = classify_declared_support(
            tube.declaration,
            sample.element_id,
            sample.point_model_m,
            evaluator_shape,
        )
        assert support.signed_distance_m == pytest.approx(0.0, abs=1e-15)
        assert support.boundary_clearance_m is not None
        assert support.boundary_clearance_m > 0.0006

    assert stepped.transform.rotation == (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    assert stepped.transform.translation_m == (0.0, 0.0, 0.0)
    assert tube.transform.rotation == (
        (1.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
        (0.0, 1.0, 0.0),
    )
    assert tube.transform.translation_m == (0.031, -0.017, 0.009)


def test_fixture_selection_is_explicit_and_closed() -> None:
    with pytest.raises(ValueError, match="unsupported declared synthetic fixture ID"):
        _ = fixture_definition("unknown")
