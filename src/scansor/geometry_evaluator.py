from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

from scansor.errors import ScansorError
from scansor.model_declarations import (
    AxialIntervalPredicate,
    DirectedIntervalPredicate,
    DomainPredicate,
    LiteralScalarReference,
    ModelDeclaration,
    ModelElement,
    OrientedOffsetRelationship,
    OrientedPlanePrimitive,
    ParameterScalarReference,
    RadialIntervalPredicate,
    ScalarReference,
    SignedScalarEndpoint,
    Vector3,
    revalidate_model_declaration,
    validate_runtime_parameter_vector,
)


@dataclass(frozen=True)
class PredicateMargins:
    """Signed distances to one predicate's boundaries; nonnegative is inside."""

    predicate_id: str
    margins_m: tuple[float, ...]

    @property
    def inside(self) -> bool:
        return min(self.margins_m) >= 0.0

    @property
    def clearance_m(self) -> float:
        return min(self.margins_m)


@dataclass(frozen=True)
class SupportClassification:
    boundary_clearance_m: float | None
    predicate_margins: tuple[PredicateMargins, ...]
    projected_inside: bool
    projected_point_m: Vector3 | None
    signed_distance_m: float


@dataclass(frozen=True)
class FixedPoseShapeEvaluation:
    parameter_jacobian_row: tuple[float, ...]
    point_gradient: Vector3
    residual_m: float


@dataclass(frozen=True)
class _ResolvedScalar:
    derivative: tuple[float, ...]
    value: float


@dataclass(frozen=True)
class _PrimitiveEvaluation:
    parameter_jacobian_row: tuple[float, ...]
    point_gradient: Vector3 | None
    projected_point_m: Vector3 | None
    signed_distance_m: float


def _dot(left: Vector3, right: Vector3) -> float:
    try:
        result = math.fsum(a * b for a, b in zip(left, right, strict=True))
    except OverflowError as error:
        raise ScansorError("geometry evaluation produced nonfinite values") from error
    if not math.isfinite(result):
        raise ScansorError("geometry evaluation produced nonfinite values")
    return result


def _subtract(left: Vector3, right: Vector3) -> Vector3:
    return cast(Vector3, tuple(a - b for a, b in zip(left, right, strict=True)))


def _scaled_add(origin: Vector3, scale: float, vector: Vector3) -> Vector3:
    return cast(
        Vector3,
        tuple(a + scale * b for a, b in zip(origin, vector, strict=True)),
    )


def _finite_vector(value: object, label: str) -> Vector3:
    if not isinstance(value, Iterable):
        raise ScansorError(f"{label} must be a finite three-vector")
    vector = tuple(value)
    if len(vector) != 3 or not all(
        isinstance(item, (int, float))
        and not isinstance(item, bool)
        and math.isfinite(item)
        for item in vector
    ):
        raise ScansorError(f"{label} must be a finite three-vector")
    numeric = cast(tuple[int | float, int | float, int | float], vector)
    return (float(numeric[0]), float(numeric[1]), float(numeric[2]))


def _resolve_reference(
    reference: ScalarReference,
    parameters: dict[str, _ResolvedScalar],
    relationships: dict[str, _ResolvedScalar],
    size: int,
) -> _ResolvedScalar:
    if isinstance(reference, LiteralScalarReference):
        return _ResolvedScalar((0.0,) * size, reference.value)
    if isinstance(reference, ParameterScalarReference):
        return parameters[reference.parameter_id]
    return relationships[reference.relationship_id]


def _resolve_endpoint(
    endpoint: SignedScalarEndpoint,
    parameters: dict[str, _ResolvedScalar],
    relationships: dict[str, _ResolvedScalar],
    size: int,
) -> _ResolvedScalar:
    resolved = _resolve_reference(endpoint.reference, parameters, relationships, size)
    return _ResolvedScalar(
        tuple(endpoint.sign * item for item in resolved.derivative),
        endpoint.sign * resolved.value,
    )


def _resolve_scalars(
    declaration: ModelDeclaration,
    values: tuple[float, ...],
) -> tuple[dict[str, _ResolvedScalar], dict[str, _ResolvedScalar]]:
    validated = validate_runtime_parameter_vector(declaration, values)
    size = len(validated)
    parameters: dict[str, _ResolvedScalar] = {}
    for index, (parameter_id, value) in enumerate(validated):
        derivative = [0.0] * size
        derivative[index] = 1.0
        parameters[parameter_id] = _ResolvedScalar(tuple(derivative), value)

    relationships: dict[str, _ResolvedScalar] = {}
    for relationship in declaration.relationships:
        if isinstance(relationship, OrientedOffsetRelationship):
            source = parameters[relationship.parameter_id]
            resolved = _ResolvedScalar(
                tuple(relationship.coefficient * item for item in source.derivative),
                relationship.constant + relationship.coefficient * source.value,
            )
        else:
            hypotenuse = _resolve_reference(
                relationship.hypotenuse, parameters, relationships, size
            )
            other_leg = _resolve_reference(
                relationship.other_leg, parameters, relationships, size
            )
            value = math.sqrt(
                hypotenuse.value * hypotenuse.value - other_leg.value * other_leg.value
            )
            resolved = _ResolvedScalar(
                tuple(
                    (hypotenuse.value * dh - other_leg.value * do) / value
                    for dh, do in zip(
                        hypotenuse.derivative, other_leg.derivative, strict=True
                    )
                ),
                value,
            )
        if not math.isfinite(resolved.value) or not all(
            math.isfinite(item) for item in resolved.derivative
        ):
            raise ScansorError(
                f"relationship {relationship.relationship_id} resolved nonfinite"
            )
        relationships[relationship.relationship_id] = resolved
    return parameters, relationships


def _element(declaration: ModelDeclaration, element_id: str) -> ModelElement:
    for element in declaration.elements:
        if element.element_id == element_id:
            return element
    raise ScansorError(f"model does not declare element ID {element_id}")


def _primitive(
    declaration: ModelDeclaration,
    element: ModelElement,
    point: Vector3,
    parameters: dict[str, _ResolvedScalar],
    relationships: dict[str, _ResolvedScalar],
) -> _PrimitiveEvaluation:
    size = len(parameters)
    primitive = element.primitive
    if isinstance(primitive, OrientedPlanePrimitive):
        offset = _resolve_reference(primitive.offset, parameters, relationships, size)
        distance = _dot(primitive.normal, point) - offset.value
        projected = _scaled_add(point, -distance, primitive.normal)
        if not math.isfinite(distance) or not all(
            math.isfinite(item) for item in projected
        ):
            raise ScansorError("primitive evaluation produced nonfinite values")
        return _PrimitiveEvaluation(
            parameter_jacobian_row=tuple(-item for item in offset.derivative),
            point_gradient=primitive.normal,
            projected_point_m=projected,
            signed_distance_m=distance,
        )
    radius = _resolve_reference(primitive.radius, parameters, relationships, size)
    origin = declaration.frame.origin_m
    axis = declaration.frame.positive_z
    relative = _subtract(point, origin)
    axial = _dot(axis, relative)
    radial = _scaled_add(relative, -axial, axis)
    rho = math.sqrt(_dot(radial, radial))
    distance = primitive.radial_orientation * (rho - radius.value)
    jacobian = tuple(-primitive.radial_orientation * item for item in radius.derivative)
    if rho == 0.0:
        return _PrimitiveEvaluation(jacobian, None, None, distance)
    radial_direction = cast(Vector3, tuple(item / rho for item in radial))
    gradient = cast(
        Vector3,
        tuple(primitive.radial_orientation * item for item in radial_direction),
    )
    projected = _scaled_add(
        _scaled_add(origin, axial, axis), radius.value, radial_direction
    )
    if not math.isfinite(distance) or not all(
        math.isfinite(item) for item in projected
    ):
        raise ScansorError("primitive evaluation produced nonfinite values")
    return _PrimitiveEvaluation(
        parameter_jacobian_row=jacobian,
        point_gradient=gradient,
        projected_point_m=projected,
        signed_distance_m=distance,
    )


def _predicate_margins(
    declaration: ModelDeclaration,
    predicate: DomainPredicate,
    projected: Vector3,
    parameters: dict[str, _ResolvedScalar],
    relationships: dict[str, _ResolvedScalar],
) -> PredicateMargins:
    size = len(parameters)
    origin = declaration.frame.origin_m
    axis = declaration.frame.positive_z
    relative = _subtract(projected, origin)
    if isinstance(predicate, (AxialIntervalPredicate, RadialIntervalPredicate)):
        lower = _resolve_reference(
            predicate.lower, parameters, relationships, size
        ).value
        upper = _resolve_reference(
            predicate.upper, parameters, relationships, size
        ).value
        if isinstance(predicate, AxialIntervalPredicate):
            coordinate = _dot(axis, relative)
        else:
            axial = _dot(axis, relative)
            radial = _scaled_add(relative, -axial, axis)
            coordinate = math.sqrt(_dot(radial, radial))
        margins = (coordinate - lower, upper - coordinate)
    elif isinstance(predicate, DirectedIntervalPredicate):
        anchor = cast(
            Vector3,
            tuple(
                _resolve_reference(item, parameters, relationships, size).value
                for item in predicate.anchor
            ),
        )
        lower = _resolve_endpoint(
            predicate.lower, parameters, relationships, size
        ).value
        upper = _resolve_endpoint(
            predicate.upper, parameters, relationships, size
        ).value
        coordinate = _dot(predicate.direction, _subtract(projected, anchor))
        margins = (coordinate - lower, upper - coordinate)
    else:
        offset = _resolve_reference(
            predicate.offset, parameters, relationships, size
        ).value
        signed = _dot(predicate.normal, projected) - offset
        margins = (-signed,) if predicate.sense == "less-than-or-equal" else (signed,)
    if not all(math.isfinite(item) for item in margins):
        raise ScansorError(
            f"domain predicate {predicate.predicate_id} produced nonfinite margins"
        )
    return PredicateMargins(predicate_id=predicate.predicate_id, margins_m=margins)


def _inputs(
    declaration: ModelDeclaration,
    element_id: str,
    point_model_m: Vector3,
    shape_values: tuple[float, ...],
) -> tuple[
    ModelDeclaration,
    ModelElement,
    Vector3,
    dict[str, _ResolvedScalar],
    dict[str, _ResolvedScalar],
]:
    declaration = revalidate_model_declaration(declaration)
    point = _finite_vector(point_model_m, "model-frame point")
    parameters, relationships = _resolve_scalars(declaration, shape_values)
    return (
        declaration,
        _element(declaration, element_id),
        point,
        parameters,
        relationships,
    )


def classify_declared_support(
    declaration: ModelDeclaration,
    element_id: str,
    point_model_m: Vector3,
    shape_values: tuple[float, ...],
) -> SupportClassification:
    """Evaluate distance and bounded projected-domain membership for one element."""

    declaration, element, point, parameters, relationships = _inputs(
        declaration, element_id, point_model_m, shape_values
    )
    primitive = _primitive(declaration, element, point, parameters, relationships)
    if primitive.projected_point_m is None:
        return SupportClassification(
            boundary_clearance_m=None,
            predicate_margins=(),
            projected_inside=False,
            projected_point_m=None,
            signed_distance_m=primitive.signed_distance_m,
        )
    margins = tuple(
        _predicate_margins(
            declaration,
            predicate,
            primitive.projected_point_m,
            parameters,
            relationships,
        )
        for predicate in element.domain.predicates
    )
    return SupportClassification(
        boundary_clearance_m=min(item.clearance_m for item in margins),
        predicate_margins=margins,
        projected_inside=all(item.inside for item in margins),
        projected_point_m=primitive.projected_point_m,
        signed_distance_m=primitive.signed_distance_m,
    )


def evaluate_fixed_pose_shape_support(
    declaration: ModelDeclaration,
    element_id: str,
    point_model_m: Vector3,
    shape_values: tuple[float, ...],
) -> FixedPoseShapeEvaluation:
    """Evaluate one declared fixed-pose shape residual and its derivatives."""

    declaration, element, point, parameters, relationships = _inputs(
        declaration, element_id, point_model_m, shape_values
    )
    primitive = _primitive(declaration, element, point, parameters, relationships)
    if primitive.point_gradient is None:
        raise ScansorError(
            f"element {element.element_id} has an undefined point gradient"
        )
    return FixedPoseShapeEvaluation(
        parameter_jacobian_row=primitive.parameter_jacobian_row,
        point_gradient=primitive.point_gradient,
        residual_m=primitive.signed_distance_m,
    )
