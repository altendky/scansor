from __future__ import annotations

import math
from typing import Annotated, ClassVar, Literal, cast

from pydantic import (
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from scansor.errors import ScansorError
from scansor.models import StrictModel
from scansor.serialization import canonical_json, parse_canonical_json, sha256

DECLARATION_REVISION = "scansor-declared-analytic-model-v2"
DECLARATION_STATUS = "internal/provisional/synthetic-only/non-public-contract"
FRAME_TOLERANCE = 1e-10
Identifier = Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$",
    ),
]
Vector3 = tuple[float, float, float]


class DeclarationRecord(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class LiteralScalarReference(DeclarationRecord):
    kind: Literal["literal"] = "literal"
    value: float


class ParameterScalarReference(DeclarationRecord):
    kind: Literal["parameter"] = "parameter"
    parameter_id: Identifier


class RelationshipScalarReference(DeclarationRecord):
    kind: Literal["relationship"] = "relationship"
    relationship_id: Identifier


ScalarReference = Annotated[
    LiteralScalarReference | ParameterScalarReference | RelationshipScalarReference,
    Field(discriminator="kind"),
]


class SignedScalarEndpoint(DeclarationRecord):
    """An exact endpoint sign, not a general scalar relationship."""

    reference: ScalarReference
    sign: Literal[-1, 1]


class OrientedOffsetRelationship(DeclarationRecord):
    coefficient: float
    constant: float
    kind: Literal["oriented-offset"] = "oriented-offset"
    parameter_id: Identifier
    relationship_id: Identifier

    @model_validator(mode="after")
    def validate_coefficient(self) -> OrientedOffsetRelationship:
        if self.coefficient == 0.0:
            raise ValueError("oriented-offset coefficient must be nonzero")
        return self


class RightTriangleLegRelationship(DeclarationRecord):
    hypotenuse: ScalarReference
    kind: Literal["right-triangle-leg"] = "right-triangle-leg"
    other_leg: ScalarReference
    relationship_id: Identifier


ScalarRelationship = Annotated[
    OrientedOffsetRelationship | RightTriangleLegRelationship,
    Field(discriminator="kind"),
]


class StrictPositivePredicate(DeclarationRecord):
    kind: Literal["strict-positive"] = "strict-positive"
    predicate_id: Identifier
    value: ScalarReference


class OrderedMinimumSeparationPredicate(DeclarationRecord):
    kind: Literal["ordered-minimum-separation"] = "ordered-minimum-separation"
    left: ScalarReference
    minimum: float = Field(ge=0.0)
    predicate_id: Identifier
    right: ScalarReference


StructuralPredicate = Annotated[
    StrictPositivePredicate | OrderedMinimumSeparationPredicate,
    Field(discriminator="kind"),
]


class AxialIntervalPredicate(DeclarationRecord):
    kind: Literal["axial-interval"] = "axial-interval"
    lower: ScalarReference
    predicate_id: Identifier
    upper: ScalarReference


class RadialIntervalPredicate(DeclarationRecord):
    kind: Literal["radial-interval"] = "radial-interval"
    lower: ScalarReference
    predicate_id: Identifier
    upper: ScalarReference


class DirectedIntervalPredicate(DeclarationRecord):
    anchor: tuple[ScalarReference, ScalarReference, ScalarReference]
    direction: Vector3
    kind: Literal["directed-interval"] = "directed-interval"
    lower: SignedScalarEndpoint
    predicate_id: Identifier
    upper: SignedScalarEndpoint

    @field_validator("anchor", "direction", mode="before")
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_direction(self) -> DirectedIntervalPredicate:
        _validate_unit(self.direction, "directed-interval direction")
        return self


class OrientedHalfSpacePredicate(DeclarationRecord):
    kind: Literal["oriented-half-space"] = "oriented-half-space"
    normal: Vector3
    offset: ScalarReference
    predicate_id: Identifier
    sense: Literal["less-than-or-equal", "greater-than-or-equal"]

    @field_validator("normal", mode="before")
    @classmethod
    def restore_normal(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_normal(self) -> OrientedHalfSpacePredicate:
        _validate_unit(self.normal, "half-space normal")
        return self


DomainPredicate = Annotated[
    AxialIntervalPredicate
    | RadialIntervalPredicate
    | DirectedIntervalPredicate
    | OrientedHalfSpacePredicate,
    Field(discriminator="kind"),
]


class BoundedSupportDomain(DeclarationRecord):
    domain_id: Identifier
    predicates: tuple[DomainPredicate, ...]

    @field_validator("predicates", mode="before")
    @classmethod
    def restore_predicates(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def require_predicates(self) -> BoundedSupportDomain:
        if not self.predicates:
            raise ValueError("bounded support domain must contain predicates")
        return self


class OrientedPlanePrimitive(DeclarationRecord):
    kind: Literal["oriented-plane"] = "oriented-plane"
    normal: Vector3
    offset: ScalarReference

    @field_validator("normal", mode="before")
    @classmethod
    def restore_normal(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_normal(self) -> OrientedPlanePrimitive:
        _validate_unit(self.normal, "oriented-plane normal")
        return self


class CoaxialCylinderPrimitive(DeclarationRecord):
    kind: Literal["coaxial-cylinder"] = "coaxial-cylinder"
    radial_orientation: Literal[-1, 1]
    radius: ScalarReference


Primitive = Annotated[
    OrientedPlanePrimitive | CoaxialCylinderPrimitive,
    Field(discriminator="kind"),
]


class ModelFrame(DeclarationRecord):
    coordinate_unit: Literal["m"] = "m"
    frame_id: Identifier
    origin_m: Vector3
    positive_x: Vector3
    positive_z: Vector3

    @field_validator("origin_m", "positive_x", "positive_z", mode="before")
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_axes(self) -> ModelFrame:
        _validate_unit(self.positive_x, "model-frame +X")
        _validate_unit(self.positive_z, "model-frame +Z")
        if abs(_dot(self.positive_x, self.positive_z)) > FRAME_TOLERANCE:
            raise ValueError("model-frame +X and +Z must be perpendicular")
        return self


class ScalarParameter(DeclarationRecord):
    diagnostic_scale: float = Field(gt=0.0)
    lower: float
    nominal: float
    parameter_id: Identifier
    unit: Literal["m"] = "m"
    upper: float

    @model_validator(mode="after")
    def validate_bounds(self) -> ScalarParameter:
        if not self.lower <= self.nominal <= self.upper:
            raise ValueError("parameter requires lower <= nominal <= upper")
        return self


class ModelElement(DeclarationRecord):
    domain: BoundedSupportDomain
    element_id: Identifier
    primitive: Primitive


class RequiredSupport(DeclarationRecord):
    element_id: Identifier
    minimum_count: int = Field(gt=0)


class CoverageCell(DeclarationRecord):
    cell_id: Identifier
    domain: BoundedSupportDomain
    element_id: Identifier
    minimum_count: int = Field(gt=0)


class RelativeRankPolicy(DeclarationRecord):
    parameter_ids: tuple[Identifier, ...]
    parameter_scales: tuple[float, ...]
    relative_threshold: float = Field(gt=0.0, lt=1.0)
    required_rank: int = Field(ge=0)
    residual_scale: float = Field(gt=0.0)

    @field_validator("parameter_ids", "parameter_scales", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_dimensions(self) -> RelativeRankPolicy:
        if len(self.parameter_ids) != len(self.parameter_scales):
            raise ValueError("relative-rank parameter IDs and scales disagree")
        if len(self.parameter_ids) != len(set(self.parameter_ids)):
            raise ValueError("relative-rank parameter IDs must be unique")
        if self.required_rank > len(self.parameter_ids):
            raise ValueError("required rank exceeds rank parameter count")
        if any(scale <= 0.0 for scale in self.parameter_scales):
            raise ValueError("relative-rank parameter scales must be positive")
        return self


class PolicyContext(DeclarationRecord):
    coverage_cells: tuple[CoverageCell, ...]
    relative_rank: RelativeRankPolicy
    required_support: tuple[RequiredSupport, ...]

    @field_validator("coverage_cells", "required_support", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class FixedPoseShapeProblem(DeclarationRecord):
    kind: Literal["fixed-pose-shape"] = "fixed-pose-shape"
    varied_parameter_ids: tuple[Identifier, ...]

    @field_validator("varied_parameter_ids", mode="before")
    @classmethod
    def restore_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class SyntheticObservationAdmission(DeclarationRecord):
    kind: Literal["project-generated-synthetic-only"] = (
        "project-generated-synthetic-only"
    )
    read_only_replay_verification_required: Literal[True] = True


class ModelSemanticDeclaration(DeclarationRecord):
    admission: SyntheticObservationAdmission
    elements: tuple[ModelElement, ...]
    format_status: Literal[
        "internal/provisional/synthetic-only/non-public-contract"
    ] = DECLARATION_STATUS
    frame: ModelFrame
    mapping_admission: PolicyContext
    optimization_preflight: PolicyContext
    parameters: tuple[ScalarParameter, ...]
    problem: FixedPoseShapeProblem
    relationships: tuple[ScalarRelationship, ...]
    revision: Literal["scansor-declared-analytic-model-v2"] = DECLARATION_REVISION
    structural_predicates: tuple[StructuralPredicate, ...]

    @field_validator(
        "elements",
        "parameters",
        "relationships",
        "structural_predicates",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_declaration(self) -> ModelSemanticDeclaration:
        _validate_declaration(self)
        return self


class ModelDeclaration(ModelSemanticDeclaration):
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_model_id(self) -> ModelDeclaration:
        expected = f"model.{sha256(_identity_bytes_unchecked(self))}"
        if self.model_id != expected:
            raise ValueError("model ID does not match canonical semantic content")
        return self


def _dot(left: Vector3, right: Vector3) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _validate_unit(vector: Vector3, label: str) -> None:
    norm = math.sqrt(_dot(vector, vector))
    if abs(norm - 1.0) > FRAME_TOLERANCE:
        raise ValueError(f"{label} must be unit length within {FRAME_TOLERANCE}")


def _scalar_reference_ids(reference: ScalarReference) -> tuple[str | None, str | None]:
    if isinstance(reference, ParameterScalarReference):
        return reference.parameter_id, None
    if isinstance(reference, RelationshipScalarReference):
        return None, reference.relationship_id
    return None, None


def _references_in_relationship(
    relationship: ScalarRelationship,
) -> tuple[ScalarReference, ...]:
    if isinstance(relationship, OrientedOffsetRelationship):
        return ()
    return relationship.hypotenuse, relationship.other_leg


def _references_in_domain(predicate: DomainPredicate) -> tuple[ScalarReference, ...]:
    if isinstance(predicate, (AxialIntervalPredicate, RadialIntervalPredicate)):
        return predicate.lower, predicate.upper
    if isinstance(predicate, DirectedIntervalPredicate):
        return (
            *predicate.anchor,
            predicate.lower.reference,
            predicate.upper.reference,
        )
    return (predicate.offset,)


def _all_domain_ids(domain: BoundedSupportDomain) -> tuple[str, ...]:
    return (domain.domain_id, *(item.predicate_id for item in domain.predicates))


def _validate_unique(values: tuple[str, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} ID")


def _validate_reference(
    reference: ScalarReference,
    parameter_ids: set[str],
    relationship_ids: set[str],
    label: str,
) -> None:
    parameter_id, relationship_id = _scalar_reference_ids(reference)
    if parameter_id is not None and parameter_id not in parameter_ids:
        raise ValueError(f"{label} references unknown parameter ID {parameter_id}")
    if relationship_id is not None and relationship_id not in relationship_ids:
        raise ValueError(
            f"{label} references unavailable relationship ID {relationship_id}"
        )


def _validate_policy(
    policy: PolicyContext,
    element_by_id: dict[str, ModelElement],
    parameter_ids: set[str],
    relationship_ids: set[str],
    label: str,
) -> None:
    support_ids = tuple(item.element_id for item in policy.required_support)
    _validate_unique(support_ids, f"{label} required-support element")
    if unknown := [item for item in support_ids if item not in element_by_id]:
        raise ValueError(
            f"{label} required support references unknown element {unknown[0]}"
        )
    cell_ids = tuple(item.cell_id for item in policy.coverage_cells)
    _validate_unique(cell_ids, f"{label} coverage-cell")
    for cell in policy.coverage_cells:
        element = element_by_id.get(cell.element_id)
        if element is None:
            raise ValueError(
                f"{label} coverage cell references unknown element {cell.element_id}"
            )
        for predicate in cell.domain.predicates:
            for reference in _references_in_domain(predicate):
                _validate_reference(
                    reference, parameter_ids, relationship_ids, f"{label} coverage"
                )
        _validate_domain_bounded(cell.domain, element.primitive, element_by_id)
    rank_ids = policy.relative_rank.parameter_ids
    if unknown := [item for item in rank_ids if item not in parameter_ids]:
        raise ValueError(f"{label} rank references unknown parameter {unknown[0]}")


def _project_from_plane(vector: Vector3, normal: Vector3) -> Vector3:
    scale = _dot(vector, normal)
    return cast(
        Vector3, tuple(a - scale * b for a, b in zip(vector, normal, strict=True))
    )


def _independent_on_plane(vectors: list[Vector3], normal: Vector3) -> bool:
    projected = [_project_from_plane(vector, normal) for vector in vectors]
    for index, first in enumerate(projected):
        first_norm = math.sqrt(_dot(first, first))
        if first_norm <= FRAME_TOLERANCE:
            continue
        for second in projected[index + 1 :]:
            second_norm = math.sqrt(_dot(second, second))
            if second_norm <= FRAME_TOLERANCE:
                continue
            cosine = _dot(first, second) / (first_norm * second_norm)
            if abs(cosine) < 1.0 - FRAME_TOLERANCE:
                return True
    return False


def _validate_domain_bounded(
    domain: BoundedSupportDomain,
    primitive: Primitive,
    _element_by_id: dict[str, ModelElement],
) -> None:
    if isinstance(primitive, CoaxialCylinderPrimitive):
        if not any(
            isinstance(item, AxialIntervalPredicate) for item in domain.predicates
        ):
            raise ValueError("coaxial-cylinder domain requires an axial interval")
        return
    vectors: list[Vector3] = []
    for predicate in domain.predicates:
        if isinstance(predicate, AxialIntervalPredicate):
            vectors.append((0.0, 0.0, 1.0))
        elif isinstance(predicate, RadialIntervalPredicate):
            vectors.extend(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)))
        elif isinstance(predicate, DirectedIntervalPredicate):
            vectors.append(predicate.direction)
    if not _independent_on_plane(vectors, primitive.normal):
        raise ValueError("oriented-plane domain is not bounded in two directions")


def _resolve_reference(
    reference: ScalarReference,
    parameters: dict[str, float],
    relationships: dict[str, float],
) -> float:
    if isinstance(reference, LiteralScalarReference):
        return reference.value
    if isinstance(reference, ParameterScalarReference):
        return parameters[reference.parameter_id]
    return relationships[reference.relationship_id]


def _resolve_endpoint(
    endpoint: SignedScalarEndpoint,
    parameters: dict[str, float],
    relationships: dict[str, float],
) -> float:
    return endpoint.sign * _resolve_reference(
        endpoint.reference, parameters, relationships
    )


def _resolve_relationships(
    declaration: ModelSemanticDeclaration,
    parameters: dict[str, float],
) -> dict[str, float]:
    resolved: dict[str, float] = {}
    for relationship in declaration.relationships:
        if isinstance(relationship, OrientedOffsetRelationship):
            value = (
                relationship.constant
                + relationship.coefficient * parameters[relationship.parameter_id]
            )
        else:
            hypotenuse = _resolve_reference(
                relationship.hypotenuse, parameters, resolved
            )
            other_leg = _resolve_reference(relationship.other_leg, parameters, resolved)
            radicand = hypotenuse * hypotenuse - other_leg * other_leg
            if radicand <= 0.0:
                raise ValueError(
                    f"relationship {relationship.relationship_id} has a nonpositive radicand"
                )
            value = math.sqrt(radicand)
        if not math.isfinite(value):
            raise ValueError(
                f"relationship {relationship.relationship_id} resolved nonfinite"
            )
        resolved[relationship.relationship_id] = value
    return resolved


def _validate_resolved_domain(
    domain: BoundedSupportDomain,
    parameters: dict[str, float],
    relationships: dict[str, float],
) -> None:
    for predicate in domain.predicates:
        if isinstance(predicate, (AxialIntervalPredicate, RadialIntervalPredicate)):
            lower = _resolve_reference(predicate.lower, parameters, relationships)
            upper = _resolve_reference(predicate.upper, parameters, relationships)
            if lower > upper:
                raise ValueError(
                    f"domain predicate {predicate.predicate_id} has lower > upper"
                )
            if isinstance(predicate, RadialIntervalPredicate) and lower < 0.0:
                raise ValueError(
                    f"radial predicate {predicate.predicate_id} has a negative lower endpoint"
                )
        elif isinstance(predicate, DirectedIntervalPredicate):
            lower = _resolve_endpoint(predicate.lower, parameters, relationships)
            upper = _resolve_endpoint(predicate.upper, parameters, relationships)
            if lower > upper:
                raise ValueError(
                    f"domain predicate {predicate.predicate_id} has lower > upper"
                )
            for reference in predicate.anchor:
                _ = _resolve_reference(reference, parameters, relationships)
        else:
            _ = _resolve_reference(predicate.offset, parameters, relationships)


def _validate_vector(
    declaration: ModelSemanticDeclaration,
    values: tuple[float, ...],
    *,
    check_bounds: bool,
) -> tuple[tuple[str, float], ...]:
    if len(values) != len(declaration.parameters):
        raise ValueError("parameter vector dimension disagrees with declaration")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("parameter vector must be finite")
    parameters = {
        parameter.parameter_id: value
        for parameter, value in zip(declaration.parameters, values, strict=True)
    }
    if check_bounds:
        for parameter, value in zip(declaration.parameters, values, strict=True):
            if value < parameter.lower or value > parameter.upper:
                raise ValueError(
                    f"parameter {parameter.parameter_id} is outside inclusive bounds"
                )
    relationships = _resolve_relationships(declaration, parameters)
    for element in declaration.elements:
        if isinstance(element.primitive, CoaxialCylinderPrimitive):
            radius = _resolve_reference(
                element.primitive.radius, parameters, relationships
            )
            if radius <= 0.0:
                raise ValueError(f"element {element.element_id} has nonpositive radius")
        else:
            _ = _resolve_reference(element.primitive.offset, parameters, relationships)
        _validate_resolved_domain(element.domain, parameters, relationships)
    for policy in (declaration.mapping_admission, declaration.optimization_preflight):
        for cell in policy.coverage_cells:
            _validate_resolved_domain(cell.domain, parameters, relationships)
    for predicate in declaration.structural_predicates:
        if isinstance(predicate, StrictPositivePredicate):
            valid = _resolve_reference(predicate.value, parameters, relationships) > 0.0
        else:
            left = _resolve_reference(predicate.left, parameters, relationships)
            right = _resolve_reference(predicate.right, parameters, relationships)
            valid = right - left > predicate.minimum
        if not valid:
            raise ValueError(f"structural predicate {predicate.predicate_id} failed")
    return tuple(parameters.items())


def _validate_declaration(declaration: ModelSemanticDeclaration) -> None:
    parameter_ids = tuple(item.parameter_id for item in declaration.parameters)
    relationship_ids = tuple(item.relationship_id for item in declaration.relationships)
    element_ids = tuple(item.element_id for item in declaration.elements)
    structural_ids = tuple(
        item.predicate_id for item in declaration.structural_predicates
    )
    _validate_unique(parameter_ids, "parameter")
    _validate_unique(relationship_ids, "relationship")
    _validate_unique(element_ids, "element")
    _validate_unique(structural_ids, "structural-predicate")
    if not declaration.parameters:
        raise ValueError("model declaration must contain parameters")
    if not declaration.elements:
        raise ValueError("model declaration must contain elements")
    if declaration.problem.varied_parameter_ids != parameter_ids:
        raise ValueError(
            "fixed-pose-shape varied parameters must equal declaration parameter order"
        )
    parameter_set = set(parameter_ids)
    available_relationships: set[str] = set()
    for relationship in declaration.relationships:
        if (
            isinstance(relationship, OrientedOffsetRelationship)
            and relationship.parameter_id not in parameter_set
        ):
            raise ValueError(
                f"relationship {relationship.relationship_id} references unknown parameter"
            )
        for reference in _references_in_relationship(relationship):
            _validate_reference(
                reference,
                parameter_set,
                available_relationships,
                f"relationship {relationship.relationship_id}",
            )
        available_relationships.add(relationship.relationship_id)
    element_by_id = {item.element_id: item for item in declaration.elements}
    domain_ids: list[str] = []
    for element in declaration.elements:
        domain_ids.extend(_all_domain_ids(element.domain))
        primitive_references = (
            (element.primitive.offset,)
            if isinstance(element.primitive, OrientedPlanePrimitive)
            else (element.primitive.radius,)
        )
        for reference in primitive_references:
            _validate_reference(
                reference,
                parameter_set,
                available_relationships,
                f"element {element.element_id}",
            )
        for predicate in element.domain.predicates:
            for reference in _references_in_domain(predicate):
                _validate_reference(
                    reference,
                    parameter_set,
                    available_relationships,
                    f"domain {element.domain.domain_id}",
                )
        _validate_domain_bounded(element.domain, element.primitive, element_by_id)
    _validate_unique(tuple(domain_ids), "element-domain or domain-predicate")
    for predicate in declaration.structural_predicates:
        references = (
            (predicate.value,)
            if isinstance(predicate, StrictPositivePredicate)
            else (predicate.left, predicate.right)
        )
        for reference in references:
            _validate_reference(
                reference,
                parameter_set,
                available_relationships,
                f"structural predicate {predicate.predicate_id}",
            )
    _validate_policy(
        declaration.mapping_admission,
        element_by_id,
        parameter_set,
        available_relationships,
        "mapping-admission",
    )
    _validate_policy(
        declaration.optimization_preflight,
        element_by_id,
        parameter_set,
        available_relationships,
        "optimization-preflight",
    )
    owned_ids = [
        declaration.frame.frame_id,
        *parameter_ids,
        *relationship_ids,
        *element_ids,
        *structural_ids,
        *domain_ids,
    ]
    for policy in (declaration.mapping_admission, declaration.optimization_preflight):
        for cell in policy.coverage_cells:
            owned_ids.extend((cell.cell_id, *_all_domain_ids(cell.domain)))
    _validate_unique(tuple(owned_ids), "declared")
    nominal = tuple(item.nominal for item in declaration.parameters)
    _ = _validate_vector(declaration, nominal, check_bounds=True)


def _identity_bytes_unchecked(
    declaration: ModelSemanticDeclaration | ModelDeclaration,
) -> bytes:
    return canonical_json(declaration.model_dump(mode="json", exclude={"model_id"}))


def _revalidated_semantic(
    declaration: ModelSemanticDeclaration,
) -> ModelSemanticDeclaration:
    try:
        return ModelSemanticDeclaration.model_validate(
            declaration.model_dump(mode="python", exclude={"model_id"})
        )
    except (TypeError, ValidationError, ValueError) as error:
        raise ScansorError(f"invalid model semantic declaration: {error}") from error


def _revalidated_model(declaration: ModelDeclaration) -> ModelDeclaration:
    try:
        return ModelDeclaration.model_validate(declaration.model_dump(mode="python"))
    except (TypeError, ValidationError, ValueError) as error:
        raise ScansorError(f"invalid model declaration: {error}") from error


def revalidate_model_declaration(declaration: ModelDeclaration) -> ModelDeclaration:
    """Reconstruct and validate a complete internal model declaration."""

    return _revalidated_model(declaration)


def identify_model(declaration: ModelSemanticDeclaration) -> ModelDeclaration:
    declaration = _revalidated_semantic(declaration)
    values = declaration.model_dump(mode="python")
    values["model_id"] = f"model.{sha256(_identity_bytes_unchecked(declaration))}"
    return ModelDeclaration.model_validate(values)


def model_identity_bytes(declaration: ModelDeclaration) -> bytes:
    declaration = revalidate_model_declaration(declaration)
    return _identity_bytes_unchecked(declaration)


def canonical_model_bytes(declaration: ModelDeclaration) -> bytes:
    declaration = revalidate_model_declaration(declaration)
    return canonical_json(declaration)


def parse_model_declaration(
    data: bytes, *, max_bytes: int = 1024 * 1024
) -> ModelDeclaration:
    parsed = parse_canonical_json(data, "model declaration", max_bytes)
    try:
        declaration = ModelDeclaration.model_validate(parsed)
    except (TypeError, ValidationError, ValueError) as error:
        raise ScansorError(f"invalid model declaration: {error}") from error
    if canonical_json(declaration) != data:
        raise ScansorError("model declaration does not reproduce canonical bytes")
    return declaration


def validate_runtime_parameter_vector(
    declaration: ModelDeclaration,
    values: tuple[float, ...],
) -> tuple[tuple[str, float], ...]:
    declaration = revalidate_model_declaration(declaration)
    try:
        return _validate_vector(declaration, values, check_bounds=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ScansorError(f"invalid runtime parameter vector: {error}") from error


def validate_runtime_parameter_structure(
    declaration: ModelDeclaration,
    values: tuple[float, ...],
) -> tuple[tuple[str, float], ...]:
    """Validate runtime structure independently of inclusive parameter bounds."""

    declaration = revalidate_model_declaration(declaration)
    try:
        return _validate_vector(declaration, values, check_bounds=False)
    except (KeyError, TypeError, ValueError) as error:
        raise ScansorError(f"invalid runtime parameter structure: {error}") from error
