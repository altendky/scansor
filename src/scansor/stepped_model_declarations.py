from __future__ import annotations

from typing import Literal

from scansor.model_declarations import (
    AxialIntervalPredicate,
    BoundedSupportDomain,
    CoaxialCylinderPrimitive,
    CoverageCell,
    DirectedIntervalPredicate,
    DomainPredicate,
    FixedPoseShapeProblem,
    LiteralScalarReference,
    ModelDeclaration,
    ModelElement,
    ModelFrame,
    ModelSemanticDeclaration,
    OrderedMinimumSeparationPredicate,
    OrientedHalfSpacePredicate,
    OrientedOffsetRelationship,
    OrientedPlanePrimitive,
    ParameterScalarReference,
    PolicyContext,
    RadialIntervalPredicate,
    RelationshipScalarReference,
    RelativeRankPolicy,
    RequiredSupport,
    RightTriangleLegRelationship,
    ScalarParameter,
    ScalarRelationship,
    SignedScalarEndpoint,
    StrictPositivePredicate,
    StructuralPredicate,
    SyntheticObservationAdmission,
    identify_model,
)

Variant = Literal["axisymmetric", "asymmetric-datum-flat"]
PARAMETER_IDS = ("r1", "r2", "r3", "s20", "s50", "s80", "datum_x")
ELEMENT_IDS = (
    "cylinder.band-1",
    "cylinder.band-2",
    "cylinder.band-3",
    "plane.station-0",
    "plane.station-20",
    "plane.station-50",
    "plane.station-80",
    "plane.datum-flat",
)


def _literal(value: float) -> LiteralScalarReference:
    return LiteralScalarReference(value=value)


def _parameter(parameter_id: str) -> ParameterScalarReference:
    return ParameterScalarReference(parameter_id=parameter_id)


def _relationship(relationship_id: str) -> RelationshipScalarReference:
    return RelationshipScalarReference(relationship_id=relationship_id)


def _parameters(asymmetric: bool) -> tuple[ScalarParameter, ...]:
    values = (
        ("r1", 0.012, 0.010, 0.0145),
        ("r2", 0.018, 0.017, 0.020),
        ("r3", 0.014, 0.012, 0.0145),
        ("s20", 0.020, 0.018, 0.022),
        ("s50", 0.050, 0.047, 0.053),
        ("s80", 0.080, 0.077, 0.083),
        ("datum_x", 0.016, 0.015, 0.0165),
    )
    if not asymmetric:
        values = values[:-1]
    return tuple(
        ScalarParameter(
            diagnostic_scale=0.001,
            lower=lower,
            nominal=nominal,
            parameter_id=parameter_id,
            upper=upper,
        )
        for parameter_id, nominal, lower, upper in values
    )


def _relationships(asymmetric: bool) -> tuple[ScalarRelationship, ...]:
    relationships: list[ScalarRelationship] = [
        OrientedOffsetRelationship(
            coefficient=-1.0,
            constant=0.0,
            parameter_id="s20",
            relationship_id="offset.station-20",
        ),
        OrientedOffsetRelationship(
            coefficient=1.0,
            constant=0.0,
            parameter_id="s50",
            relationship_id="offset.station-50",
        ),
        OrientedOffsetRelationship(
            coefficient=1.0,
            constant=0.0,
            parameter_id="s80",
            relationship_id="offset.station-80",
        ),
    ]
    if asymmetric:
        relationships.extend(
            (
                OrientedOffsetRelationship(
                    coefficient=1.0,
                    constant=0.0,
                    parameter_id="datum_x",
                    relationship_id="offset.datum-flat",
                ),
                RightTriangleLegRelationship(
                    hypotenuse=_parameter("r2"),
                    other_leg=_parameter("datum_x"),
                    relationship_id="length.datum-half-width",
                ),
            )
        )
    return tuple(relationships)


def _domain(
    element_id: str,
    prefix: str,
    asymmetric: bool,
) -> BoundedSupportDomain:
    zero = _literal(0.0)
    predicate_prefix = f"{prefix}.{element_id}"
    predicates: tuple[DomainPredicate, ...]
    if element_id == "cylinder.band-1":
        predicates = (
            AxialIntervalPredicate(
                lower=zero,
                predicate_id=f"{predicate_prefix}.axial",
                upper=_parameter("s20"),
            ),
        )
    elif element_id == "cylinder.band-2":
        items: list[DomainPredicate] = [
            AxialIntervalPredicate(
                lower=_parameter("s20"),
                predicate_id=f"{predicate_prefix}.axial",
                upper=_parameter("s50"),
            )
        ]
        if asymmetric:
            items.append(
                OrientedHalfSpacePredicate(
                    normal=(1.0, 0.0, 0.0),
                    offset=_parameter("datum_x"),
                    predicate_id=f"{predicate_prefix}.datum-trim",
                    sense="less-than-or-equal",
                )
            )
        predicates = tuple(items)
    elif element_id == "cylinder.band-3":
        predicates = (
            AxialIntervalPredicate(
                lower=_parameter("s50"),
                predicate_id=f"{predicate_prefix}.axial",
                upper=_parameter("s80"),
            ),
        )
    elif element_id == "plane.station-0":
        predicates = (
            RadialIntervalPredicate(
                lower=zero,
                predicate_id=f"{predicate_prefix}.radial",
                upper=_parameter("r1"),
            ),
        )
    elif element_id == "plane.station-20":
        items = [
            RadialIntervalPredicate(
                lower=_parameter("r1"),
                predicate_id=f"{predicate_prefix}.radial",
                upper=_parameter("r2"),
            )
        ]
        if asymmetric:
            items.append(
                OrientedHalfSpacePredicate(
                    normal=(1.0, 0.0, 0.0),
                    offset=_parameter("datum_x"),
                    predicate_id=f"{predicate_prefix}.datum-trim",
                    sense="less-than-or-equal",
                )
            )
        predicates = tuple(items)
    elif element_id == "plane.station-50":
        items = [
            RadialIntervalPredicate(
                lower=_parameter("r3"),
                predicate_id=f"{predicate_prefix}.radial",
                upper=_parameter("r2"),
            )
        ]
        if asymmetric:
            items.append(
                OrientedHalfSpacePredicate(
                    normal=(1.0, 0.0, 0.0),
                    offset=_parameter("datum_x"),
                    predicate_id=f"{predicate_prefix}.datum-trim",
                    sense="less-than-or-equal",
                )
            )
        predicates = tuple(items)
    elif element_id == "plane.station-80":
        predicates = (
            RadialIntervalPredicate(
                lower=zero,
                predicate_id=f"{predicate_prefix}.radial",
                upper=_parameter("r3"),
            ),
        )
    else:
        half_width = _relationship("length.datum-half-width")
        predicates = (
            AxialIntervalPredicate(
                lower=_parameter("s20"),
                predicate_id=f"{predicate_prefix}.axial",
                upper=_parameter("s50"),
            ),
            DirectedIntervalPredicate(
                anchor=(zero, zero, zero),
                direction=(0.0, 1.0, 0.0),
                lower=SignedScalarEndpoint(reference=half_width, sign=-1),
                predicate_id=f"{predicate_prefix}.directed-y",
                upper=SignedScalarEndpoint(reference=half_width, sign=1),
            ),
        )
    return BoundedSupportDomain(
        domain_id=f"{predicate_prefix}.domain",
        predicates=predicates,
    )


def _elements(asymmetric: bool) -> tuple[ModelElement, ...]:
    ids = ELEMENT_IDS if asymmetric else ELEMENT_IDS[:-1]
    primitive_by_id = {
        "cylinder.band-1": CoaxialCylinderPrimitive(
            radial_orientation=1, radius=_parameter("r1")
        ),
        "cylinder.band-2": CoaxialCylinderPrimitive(
            radial_orientation=1, radius=_parameter("r2")
        ),
        "cylinder.band-3": CoaxialCylinderPrimitive(
            radial_orientation=1, radius=_parameter("r3")
        ),
        "plane.station-0": OrientedPlanePrimitive(
            normal=(0.0, 0.0, -1.0), offset=_literal(0.0)
        ),
        "plane.station-20": OrientedPlanePrimitive(
            normal=(0.0, 0.0, -1.0),
            offset=_relationship("offset.station-20"),
        ),
        "plane.station-50": OrientedPlanePrimitive(
            normal=(0.0, 0.0, 1.0),
            offset=_relationship("offset.station-50"),
        ),
        "plane.station-80": OrientedPlanePrimitive(
            normal=(0.0, 0.0, 1.0),
            offset=_relationship("offset.station-80"),
        ),
        "plane.datum-flat": OrientedPlanePrimitive(
            normal=(1.0, 0.0, 0.0),
            offset=_relationship("offset.datum-flat"),
        ),
    }
    elements: list[ModelElement] = []
    for element_id in ids:
        elements.append(
            ModelElement(
                domain=_domain(element_id, "element-support", asymmetric),
                element_id=element_id,
                primitive=primitive_by_id[element_id],
            )
        )
    return tuple(elements)


def _policy(
    element_ids: tuple[str, ...],
    parameter_ids: tuple[str, ...],
    asymmetric: bool,
    *,
    context: Literal["mapping-admission", "optimization-preflight"],
    minimum_count: int,
) -> PolicyContext:
    return PolicyContext(
        coverage_cells=tuple(
            CoverageCell(
                cell_id=f"{context}.coverage.{element_id}",
                domain=_domain(element_id, f"{context}-coverage", asymmetric),
                element_id=element_id,
                minimum_count=minimum_count,
            )
            for element_id in element_ids
        ),
        relative_rank=RelativeRankPolicy(
            parameter_ids=parameter_ids,
            parameter_scales=(0.001,) * len(parameter_ids),
            relative_threshold=1e-10,
            required_rank=len(parameter_ids),
            residual_scale=0.001,
        ),
        required_support=tuple(
            RequiredSupport(element_id=element_id, minimum_count=minimum_count)
            for element_id in element_ids
        ),
    )


def _structural_predicates(asymmetric: bool) -> tuple[StructuralPredicate, ...]:
    zero = _literal(0.0)
    predicates: list[StructuralPredicate] = [
        StrictPositivePredicate(
            predicate_id=f"positive.{parameter_id}", value=_parameter(parameter_id)
        )
        for parameter_id in ("r1", "r2", "r3")
    ]
    predicates.extend(
        (
            OrderedMinimumSeparationPredicate(
                left=zero,
                minimum=0.004,
                predicate_id="separation.station-0.station-20",
                right=_parameter("s20"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_parameter("s20"),
                minimum=0.004,
                predicate_id="separation.station-20.station-50",
                right=_parameter("s50"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_parameter("s50"),
                minimum=0.004,
                predicate_id="separation.station-50.station-80",
                right=_parameter("s80"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_parameter("r1"),
                minimum=0.002,
                predicate_id="separation.radius-1.radius-2",
                right=_parameter("r2"),
            ),
            OrderedMinimumSeparationPredicate(
                left=_parameter("r3"),
                minimum=0.002,
                predicate_id="separation.radius-3.radius-2",
                right=_parameter("r2"),
            ),
        )
    )
    if asymmetric:
        predicates.extend(
            (
                OrderedMinimumSeparationPredicate(
                    left=zero,
                    minimum=0.0,
                    predicate_id="separation.axis.datum",
                    right=_parameter("datum_x"),
                ),
                OrderedMinimumSeparationPredicate(
                    left=_parameter("datum_x"),
                    minimum=0.0,
                    predicate_id="separation.datum.radius-2",
                    right=_parameter("r2"),
                ),
                OrderedMinimumSeparationPredicate(
                    left=_literal(0.001),
                    minimum=0.0,
                    predicate_id="separation.minimum.datum-half-width",
                    right=_relationship("length.datum-half-width"),
                ),
            )
        )
    return tuple(predicates)


def stepped_model_semantic_declaration(
    variant: Variant,
) -> ModelSemanticDeclaration:
    asymmetric = variant == "asymmetric-datum-flat"
    parameters = _parameters(asymmetric)
    parameter_ids = tuple(item.parameter_id for item in parameters)
    element_ids = ELEMENT_IDS if asymmetric else ELEMENT_IDS[:-1]
    return ModelSemanticDeclaration(
        admission=SyntheticObservationAdmission(),
        elements=_elements(asymmetric),
        frame=ModelFrame(
            frame_id="stepped-rotational-v0-synthetic-model-frame",
            origin_m=(0.0, 0.0, 0.0),
            positive_x=(1.0, 0.0, 0.0),
            positive_z=(0.0, 0.0, 1.0),
        ),
        mapping_admission=_policy(
            element_ids,
            parameter_ids,
            asymmetric,
            context="mapping-admission",
            minimum_count=3,
        ),
        optimization_preflight=_policy(
            element_ids,
            parameter_ids,
            asymmetric,
            context="optimization-preflight",
            minimum_count=1,
        ),
        parameters=parameters,
        problem=FixedPoseShapeProblem(varied_parameter_ids=parameter_ids),
        relationships=_relationships(asymmetric),
        structural_predicates=_structural_predicates(asymmetric),
    )


def stepped_model_declaration(variant: Variant) -> ModelDeclaration:
    return identify_model(stepped_model_semantic_declaration(variant))
