from __future__ import annotations

import copy
import math
from typing import Any

import pytest
from pydantic import ValidationError

from scansor.errors import ScansorError
from scansor.model_declarations import (
    BoundedSupportDomain,
    CoverageCell,
    FixedPoseShapeProblem,
    LiteralScalarReference,
    ModelElement,
    ModelFrame,
    ModelSemanticDeclaration,
    OrientedPlanePrimitive,
    ParameterScalarReference,
    PolicyContext,
    RadialIntervalPredicate,
    RelativeRankPolicy,
    RequiredSupport,
    ScalarParameter,
    StrictPositivePredicate,
    SyntheticObservationAdmission,
    canonical_model_bytes,
    identify_model,
    model_identity_bytes,
    parse_model_declaration,
    validate_runtime_parameter_vector,
)
from scansor.serialization import canonical_json
from scansor.stepped_model_declarations import (
    ELEMENT_IDS,
    Variant,
    stepped_model_declaration,
    stepped_model_semantic_declaration,
)


def _record(
    variant: Variant = "asymmetric-datum-flat",
) -> dict[str, Any]:
    declaration = stepped_model_semantic_declaration(variant)
    return copy.deepcopy(declaration.model_dump(mode="python"))


def _parameter(record: dict[str, Any], parameter_id: str) -> dict[str, Any]:
    parameters = record["parameters"]
    assert isinstance(parameters, tuple)
    return next(item for item in parameters if item["parameter_id"] == parameter_id)


def _one_parameter_model() -> ModelSemanticDeclaration:
    parameter_id = "radius"

    def disk_domain(prefix: str) -> BoundedSupportDomain:
        return BoundedSupportDomain(
            domain_id=f"{prefix}.domain",
            predicates=(
                RadialIntervalPredicate(
                    lower=LiteralScalarReference(value=0.0),
                    predicate_id=f"{prefix}.radial",
                    upper=ParameterScalarReference(parameter_id=parameter_id),
                ),
            ),
        )

    domain = disk_domain("disk")
    rank = RelativeRankPolicy(
        parameter_ids=(parameter_id,),
        parameter_scales=(0.001,),
        relative_threshold=1e-10,
        required_rank=1,
        residual_scale=0.001,
    )
    return ModelSemanticDeclaration(
        admission=SyntheticObservationAdmission(),
        elements=(
            ModelElement(
                domain=domain,
                element_id="disk",
                primitive=OrientedPlanePrimitive(
                    normal=(0.0, 0.0, 1.0),
                    offset=LiteralScalarReference(value=0.0),
                ),
            ),
        ),
        frame=ModelFrame(
            frame_id="one-parameter-frame",
            origin_m=(0.0, 0.0, 0.0),
            positive_x=(1.0, 0.0, 0.0),
            positive_z=(0.0, 0.0, 1.0),
        ),
        mapping_admission=PolicyContext(
            coverage_cells=(
                CoverageCell(
                    cell_id="mapping.disk",
                    domain=disk_domain("mapping.disk"),
                    element_id="disk",
                    minimum_count=2,
                ),
            ),
            relative_rank=rank,
            required_support=(RequiredSupport(element_id="disk", minimum_count=2),),
        ),
        optimization_preflight=PolicyContext(
            coverage_cells=(
                CoverageCell(
                    cell_id="preflight.disk",
                    domain=disk_domain("preflight.disk"),
                    element_id="disk",
                    minimum_count=1,
                ),
            ),
            relative_rank=rank,
            required_support=(RequiredSupport(element_id="disk", minimum_count=1),),
        ),
        parameters=(
            ScalarParameter(
                diagnostic_scale=0.001,
                lower=0.005,
                nominal=0.01,
                parameter_id=parameter_id,
                upper=0.02,
            ),
        ),
        problem=FixedPoseShapeProblem(varied_parameter_ids=(parameter_id,)),
        relationships=(),
        structural_predicates=(
            StrictPositivePredicate(
                predicate_id="positive.radius",
                value=ParameterScalarReference(parameter_id=parameter_id),
            ),
        ),
    )


def test_stepped_declarations_preserve_existing_model_data() -> None:
    axisymmetric = stepped_model_declaration("axisymmetric")
    asymmetric = stepped_model_declaration("asymmetric-datum-flat")

    assert tuple(item.parameter_id for item in axisymmetric.parameters) == (
        "r1",
        "r2",
        "r3",
        "s20",
        "s50",
        "s80",
    )
    assert tuple(item.parameter_id for item in asymmetric.parameters) == (
        "r1",
        "r2",
        "r3",
        "s20",
        "s50",
        "s80",
        "datum_x",
    )
    assert tuple(item.nominal for item in asymmetric.parameters) == (
        0.012,
        0.018,
        0.014,
        0.020,
        0.050,
        0.080,
        0.016,
    )
    assert tuple(item.element_id for item in axisymmetric.elements) == ELEMENT_IDS[:-1]
    assert tuple(item.element_id for item in asymmetric.elements) == ELEMENT_IDS
    assert all(
        item.minimum_count == 3
        for item in asymmetric.mapping_admission.required_support
    )
    assert all(
        item.minimum_count == 1
        for item in asymmetric.optimization_preflight.required_support
    )
    assert asymmetric.mapping_admission.relative_rank.required_rank == 7
    assert axisymmetric.mapping_admission.relative_rank.required_rank == 6
    assert all(
        "model_id" not in type(item).model_fields for item in asymmetric.parameters
    )
    assert asymmetric.model_id != axisymmetric.model_id


def test_identity_and_canonical_round_trip_are_deterministic() -> None:
    first = stepped_model_declaration("asymmetric-datum-flat")
    second = stepped_model_declaration("asymmetric-datum-flat")
    encoded = canonical_model_bytes(first)

    assert first == second
    assert encoded == canonical_model_bytes(second)
    assert first.model_id.removeprefix("model.") != ""
    assert model_identity_bytes(first) == canonical_json(
        first.model_dump(mode="json", exclude={"model_id"})
    )
    assert parse_model_declaration(encoded) == first


def test_canonical_parser_rejects_duplicate_keys_and_noncanonical_bytes() -> None:
    encoded = canonical_model_bytes(stepped_model_declaration("asymmetric-datum-flat"))
    duplicate = b'{\n  "admission": null,\n' + encoded[2:]
    with pytest.raises(ScansorError, match="duplicate JSON object key: admission"):
        _ = parse_model_declaration(duplicate)
    with pytest.raises(ScansorError, match="not canonical JSON"):
        _ = parse_model_declaration(encoded.rstrip())


def test_records_are_strict_frozen_and_nonfinite_rejecting() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    assert isinstance(declaration.parameters, tuple)
    with pytest.raises(ValidationError, match="frozen"):
        declaration.parameters[0].nominal = 1.0

    record = _record("axisymmetric")
    record["unexpected"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _ = ModelSemanticDeclaration.model_validate(record)

    record = _record("axisymmetric")
    frame = record["frame"]
    assert isinstance(frame, dict)
    frame["origin_m"] = (math.inf, 0.0, 0.0)
    with pytest.raises(ValidationError, match="finite number"):
        _ = ModelSemanticDeclaration.model_validate(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("diagnostic_scale", 0.0, "greater than 0"),
        ("lower", 0.013, "lower <= nominal <= upper"),
    ),
)
def test_invalid_parameter_bounds_and_scales_are_rejected(
    field: str, value: float, message: str
) -> None:
    record = _record("axisymmetric")
    _parameter(record, "r1")[field] = value
    with pytest.raises(ValidationError, match=message):
        _ = ModelSemanticDeclaration.model_validate(record)


def test_invalid_rank_scale_is_rejected() -> None:
    record = _record("axisymmetric")
    mapping = record["mapping_admission"]
    assert isinstance(mapping, dict)
    mapping["relative_rank"]["parameter_scales"] = (0.0,) * 6
    with pytest.raises(ValidationError, match="scales must be positive"):
        _ = ModelSemanticDeclaration.model_validate(record)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("duplicate-parameter", "duplicate parameter ID"),
        ("unknown-primitive-reference", "references unknown parameter ID"),
        ("invalid-domain", "has lower > upper"),
        ("unknown-policy-element", "references unknown element"),
    ),
)
def test_static_graph_validation_fails_closed(mutation: str, message: str) -> None:
    record = _record("axisymmetric")
    parameters = record["parameters"]
    elements = record["elements"]
    mapping = record["mapping_admission"]
    assert isinstance(parameters, tuple)
    assert isinstance(elements, tuple)
    assert isinstance(mapping, dict)
    if mutation == "duplicate-parameter":
        parameters[1]["parameter_id"] = parameters[0]["parameter_id"]
    elif mutation == "unknown-primitive-reference":
        elements[0]["primitive"]["radius"]["parameter_id"] = "unknown"
    elif mutation == "invalid-domain":
        predicate = elements[0]["domain"]["predicates"][0]
        predicate["lower"] = {"kind": "literal", "value": 1.0}
        predicate["upper"] = {"kind": "literal", "value": 0.0}
    else:
        mapping["required_support"][0]["element_id"] = "unknown"
    with pytest.raises(ValidationError, match=message):
        _ = ModelSemanticDeclaration.model_validate(record)


def test_scalar_predicates_cannot_be_used_as_scalar_references() -> None:
    record = _record("axisymmetric")
    elements = record["elements"]
    assert isinstance(elements, tuple)
    elements[0]["primitive"]["radius"] = {
        "kind": "strict-positive",
        "predicate_id": "positive.r1",
        "value": {"kind": "parameter", "parameter_id": "r1"},
    }
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        _ = ModelSemanticDeclaration.model_validate(record)


def test_coherent_parameter_reorder_is_valid_and_changes_identity() -> None:
    original = stepped_model_declaration("axisymmetric")
    record = _record("axisymmetric")
    parameters = record["parameters"]
    problem = record["problem"]
    assert isinstance(parameters, tuple)
    assert isinstance(problem, dict)
    reordered = (parameters[1], parameters[0], *parameters[2:])
    record["parameters"] = reordered
    ids = tuple(item["parameter_id"] for item in reordered)
    problem["varied_parameter_ids"] = ids
    for key in ("mapping_admission", "optimization_preflight"):
        policy = record[key]
        assert isinstance(policy, dict)
        rank = policy["relative_rank"]
        scales = dict(zip(rank["parameter_ids"], rank["parameter_scales"], strict=True))
        rank["parameter_ids"] = ids
        rank["parameter_scales"] = tuple(scales[item] for item in ids)

    changed = identify_model(ModelSemanticDeclaration.model_validate(record))
    assert changed.model_id != original.model_id
    assert tuple(item.parameter_id for item in changed.parameters) == ids


def test_relationship_dependency_order_is_enforced() -> None:
    record = _record()
    relationships = list(record["relationships"])
    half_width = relationships[-1]
    half_width["hypotenuse"] = {
        "kind": "relationship",
        "relationship_id": "offset.station-80",
    }
    coherent = copy.deepcopy(record)
    coherent["relationships"] = tuple(relationships)
    _ = ModelSemanticDeclaration.model_validate(coherent)

    relationships.insert(0, relationships.pop())
    record["relationships"] = tuple(relationships)
    with pytest.raises(ValidationError, match="unavailable relationship ID"):
        _ = ModelSemanticDeclaration.model_validate(record)


def test_nominal_and_runtime_structural_validity_are_distinct() -> None:
    record = _record()
    r2 = _parameter(record, "r2")
    r2["lower"] = 0.012
    semantic = ModelSemanticDeclaration.model_validate(record)
    declaration = identify_model(semantic)

    nominal = tuple(item.nominal for item in declaration.parameters)
    assert validate_runtime_parameter_vector(declaration, nominal)[1] == (
        "r2",
        0.018,
    )
    invalid_trial = list(nominal)
    invalid_trial[1] = 0.012
    with pytest.raises(ScansorError, match="invalid runtime parameter vector"):
        _ = validate_runtime_parameter_vector(declaration, tuple(invalid_trial))
    out_of_bounds = list(nominal)
    out_of_bounds[0] = 0.1
    with pytest.raises(ScansorError, match="outside inclusive bounds"):
        _ = validate_runtime_parameter_vector(declaration, tuple(out_of_bounds))

    invalid_nominal = _record()
    datum = _parameter(invalid_nominal, "datum_x")
    datum["upper"] = 0.019
    datum["nominal"] = 0.019
    with pytest.raises(ValidationError, match="nonpositive radicand"):
        _ = ModelSemanticDeclaration.model_validate(invalid_nominal)


def test_public_boundaries_revalidate_tampered_frozen_copies() -> None:
    declaration = stepped_model_declaration("axisymmetric")
    tampered = declaration.model_copy(
        update={"parameters": declaration.parameters[::-1]}
    )
    with pytest.raises(ScansorError, match="invalid model declaration"):
        _ = canonical_model_bytes(tampered)
    with pytest.raises(ScansorError, match="invalid model declaration"):
        _ = validate_runtime_parameter_vector(
            tampered, tuple(item.nominal for item in tampered.parameters)
        )

    stale_identity = declaration.model_dump(mode="python")
    stale_identity["frame"]["frame_id"] = "tampered-frame"
    with pytest.raises(ValidationError, match="model ID does not match"):
        _ = type(declaration).model_validate(stale_identity)


def test_validation_does_not_assume_stepped_counts() -> None:
    declaration = identify_model(_one_parameter_model())
    assert len(declaration.parameters) == 1
    assert len(declaration.elements) == 1
    assert validate_runtime_parameter_vector(declaration, (0.012,)) == (
        ("radius", 0.012),
    )
