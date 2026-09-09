from __future__ import annotations

from typing import Literal

from scansor.model_declarations import (
    AxialIntervalPredicate,
    BoundedSupportDomain,
    CoaxialCylinderPrimitive,
    CoverageCell,
    DomainPredicate,
    FixedPoseShapeProblem,
    LiteralScalarReference,
    ModelDeclaration,
    ModelElement,
    ModelFrame,
    ModelSemanticDeclaration,
    OrderedMinimumSeparationPredicate,
    OrientedOffsetRelationship,
    OrientedPlanePrimitive,
    ParameterScalarReference,
    PolicyContext,
    RadialIntervalPredicate,
    RelationshipScalarReference,
    RelativeRankPolicy,
    RequiredSupport,
    ScalarParameter,
    StrictPositivePredicate,
    SyntheticObservationAdmission,
    identify_model,
)

PARAMETER_IDS = ("bore-radius", "outside-radius", "axial-length")
ELEMENT_IDS = ("wall.outer", "wall.bore", "end.near", "end.far")
PARAMETER_SCALES = (0.0005, 0.0015, 0.004)


def _literal(value: float) -> LiteralScalarReference:
    return LiteralScalarReference(value=value)


def _parameter(parameter_id: str) -> ParameterScalarReference:
    return ParameterScalarReference(parameter_id=parameter_id)


def _relationship(relationship_id: str) -> RelationshipScalarReference:
    return RelationshipScalarReference(relationship_id=relationship_id)


def _parameters() -> tuple[ScalarParameter, ...]:
    values = (
        ("bore-radius", 0.008, 0.006, 0.010, PARAMETER_SCALES[0]),
        ("outside-radius", 0.013, 0.011, 0.016, PARAMETER_SCALES[1]),
        ("axial-length", 0.060, 0.045, 0.075, PARAMETER_SCALES[2]),
    )
    return tuple(
        ScalarParameter(
            diagnostic_scale=diagnostic_scale,
            lower=lower,
            nominal=nominal,
            parameter_id=parameter_id,
            upper=upper,
        )
        for parameter_id, nominal, lower, upper, diagnostic_scale in values
    )


def _domain(
    element_id: str,
    prefix: str,
) -> BoundedSupportDomain:
    predicate_prefix = f"{prefix}.{element_id}"
    predicates: tuple[DomainPredicate, ...]
    if element_id in {"wall.outer", "wall.bore"}:
        predicates = (
            AxialIntervalPredicate(
                lower=_literal(0.0),
                predicate_id=f"{predicate_prefix}.axial",
                upper=_parameter("axial-length"),
            ),
        )
    else:
        predicates = (
            RadialIntervalPredicate(
                lower=_parameter("bore-radius"),
                predicate_id=f"{predicate_prefix}.radial",
                upper=_parameter("outside-radius"),
            ),
        )
    return BoundedSupportDomain(
        domain_id=f"{predicate_prefix}.domain",
        predicates=predicates,
    )


def _elements() -> tuple[ModelElement, ...]:
    primitive_by_id = {
        "wall.outer": CoaxialCylinderPrimitive(
            radial_orientation=1,
            radius=_parameter("outside-radius"),
        ),
        "wall.bore": CoaxialCylinderPrimitive(
            radial_orientation=-1,
            radius=_parameter("bore-radius"),
        ),
        "end.near": OrientedPlanePrimitive(
            normal=(0.0, 0.0, -1.0),
            offset=_literal(0.0),
        ),
        "end.far": OrientedPlanePrimitive(
            normal=(0.0, 0.0, 1.0),
            offset=_relationship("offset.end.far"),
        ),
    }
    return tuple(
        ModelElement(
            domain=_domain(element_id, "element-support"),
            element_id=element_id,
            primitive=primitive_by_id[element_id],
        )
        for element_id in ELEMENT_IDS
    )


def _policy(
    *,
    context: Literal["mapping-admission", "optimization-preflight"],
    support_minima: tuple[int, int, int, int],
    coverage_minima: tuple[int, int, int, int],
) -> PolicyContext:
    return PolicyContext(
        coverage_cells=tuple(
            CoverageCell(
                cell_id=f"{context}.coverage.{element_id}",
                domain=_domain(element_id, f"{context}-coverage"),
                element_id=element_id,
                minimum_count=minimum_count,
            )
            for element_id, minimum_count in zip(
                ELEMENT_IDS, coverage_minima, strict=True
            )
        ),
        relative_rank=RelativeRankPolicy(
            parameter_ids=PARAMETER_IDS,
            parameter_scales=PARAMETER_SCALES,
            relative_threshold=1e-10,
            required_rank=3,
            residual_scale=0.001,
        ),
        required_support=tuple(
            RequiredSupport(element_id=element_id, minimum_count=minimum_count)
            for element_id, minimum_count in zip(
                ELEMENT_IDS, support_minima, strict=True
            )
        ),
    )


def tube_model_semantic_declaration() -> ModelSemanticDeclaration:
    return ModelSemanticDeclaration(
        admission=SyntheticObservationAdmission(),
        elements=_elements(),
        frame=ModelFrame(
            frame_id="coaxial-tube-v1-model-frame",
            origin_m=(0.0, 0.0, 0.0),
            positive_x=(1.0, 0.0, 0.0),
            positive_z=(0.0, 0.0, 1.0),
        ),
        mapping_admission=_policy(
            context="mapping-admission",
            support_minima=(12, 12, 8, 8),
            coverage_minima=(8, 8, 4, 4),
        ),
        optimization_preflight=_policy(
            context="optimization-preflight",
            support_minima=(6, 6, 4, 4),
            coverage_minima=(4, 4, 2, 2),
        ),
        parameters=_parameters(),
        problem=FixedPoseShapeProblem(varied_parameter_ids=PARAMETER_IDS),
        relationships=(
            OrientedOffsetRelationship(
                coefficient=1.0,
                constant=0.0,
                parameter_id="axial-length",
                relationship_id="offset.end.far",
            ),
        ),
        structural_predicates=(
            StrictPositivePredicate(
                predicate_id="positive.bore-radius",
                value=_parameter("bore-radius"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_parameter("bore-radius"),
                minimum=0.002,
                predicate_id="separation.minimum-wall-thickness",
                right=_parameter("outside-radius"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_literal(0.0),
                minimum=0.040,
                predicate_id="separation.minimum-axial-length",
                right=_parameter("axial-length"),
            ),
        ),
    )


def tube_model_declaration() -> ModelDeclaration:
    return identify_model(tube_model_semantic_declaration())
