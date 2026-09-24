from __future__ import annotations

import math
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from scansor.reference_geometry import (
    AxisLineDefinition,
    AxisLineQuantity,
    AxisParallelDirectionDefinition,
    CoordinateFrameDefinition,
    CylinderElement,
    CylinderObservationFactor,
    FixedAxisLineRole,
    FixedScalarRole,
    FreeAxisLineRole,
    FreeScalarRole,
    LiteralAxisLineSource,
    LiteralScalarSource,
    OrientedPlaneDefinition,
    PlaneObservationFactor,
    ReferenceGeometryModel,
    ReferenceGeometrySolveRequest,
    ResultAxisLineSource,
    ResultScalarSource,
    ScalarQuantity,
    compile_reference_geometry_solve,
    reference_geometry_model_sha256,
    resolved_axis_line_source,
    resolved_scalar_source,
    validate_selection_bindings,
)
from scansor.selection_bundle import SelectionBundle

ROOT = Path(__file__).parents[1]
USER_BUNDLE = (
    ROOT
    / "examples"
    / "nozzle-bayonette-simplified"
    / "selections"
    / "user-selection-bundle.json"
)
SOURCE_SHA256 = "537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358"
OUTER_IDS_SHA256 = "dc519876d45d3a7a68cbac42b98c3673f9dc2a3fbb2bcfd622cf3037105867dd"
TOP_IDS_SHA256 = "7cc32c5be045c1e709209998283d18173b8e936e50cfa837b55d9cd19d776811"


def user_bundle() -> SelectionBundle:
    return SelectionBundle.model_validate_json(USER_BUNDLE.read_bytes())


def model() -> ReferenceGeometryModel:
    return ReferenceGeometryModel(
        model_id="nozzle-reference-model",
        frames=(CoordinateFrameDefinition(frame_id="mesh-frame"),),
        quantities=(
            AxisLineQuantity(quantity_id="shared-axis", frame_id="mesh-frame"),
            ScalarQuantity(quantity_id="cylinder-radius", semantic="radius"),
            ScalarQuantity(quantity_id="plane-offset", semantic="plane-offset"),
        ),
        geometry=(
            AxisLineDefinition(geometry_id="axis", quantity_id="shared-axis"),
            AxisParallelDirectionDefinition(
                geometry_id="axis-positive",
                axis_id="axis",
                positive_alignment_hint=(0.0, 0.0, 1.0),
            ),
            OrientedPlaneDefinition(
                geometry_id="end-plane",
                normal_direction_id="axis-positive",
                offset_quantity_id="plane-offset",
            ),
        ),
        elements=(
            CylinderElement(
                element_id="outer-cylinder",
                axis_id="axis",
                radius_quantity_id="cylinder-radius",
            ),
        ),
        factors=(
            CylinderObservationFactor(
                factor_id="cylinder-fit",
                element_id="outer-cylinder",
                observation_frame_id="mesh-frame",
                observation_source_id="scan",
                observation_source_sha256=SOURCE_SHA256,
                selection_id="selection_0350f17f14784ce2b686fafc6e709dfb",
                selection_vertex_count=490,
                selection_vertex_ids_sha256=OUTER_IDS_SHA256,
                residual_scale_m=0.001,
            ),
            PlaneObservationFactor(
                factor_id="plane-fit",
                observation_frame_id="mesh-frame",
                observation_source_id="scan",
                observation_source_sha256=SOURCE_SHA256,
                plane_id="end-plane",
                selection_id="selection_0bcf6d9c18b84ed59026a3c906600409",
                selection_vertex_count=240,
                selection_vertex_ids_sha256=TOP_IDS_SHA256,
                residual_scale_m=0.001,
            ),
        ),
    )


def fixed_axis() -> FixedAxisLineRole:
    return FixedAxisLineRole(
        quantity_id="shared-axis",
        source=resolved_axis_line_source(
            frame_id="mesh-frame",
            result_id="standalone-cylinder-fit",
            quantity_id="fitted-cylinder-axis",
            resolved_value=LiteralAxisLineSource(
                closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
                direction=(0.0, 0.0, 1.0),
            ),
        ),
    )


def free_axis() -> FreeAxisLineRole:
    return FreeAxisLineRole(
        quantity_id="shared-axis",
        initial=LiteralAxisLineSource(
            closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
            direction=(0.0, 0.0, 1.0),
        ),
        maximum_direction_delta_rad=0.2,
        maximum_transverse_delta_m=0.005,
        rotation_scale_rad=0.01,
        transverse_scale_m=0.001,
    )


def free_scalar(
    quantity_id: str, initial: float, lower: float, upper: float
) -> FreeScalarRole:
    return FreeScalarRole(
        quantity_id=quantity_id,
        initial=LiteralScalarSource(value_m=initial),
        lower_m=lower,
        scale_m=0.001,
        upper_m=upper,
    )


def test_fixed_upstream_axis_prevents_plane_feedback() -> None:
    declaration = model()
    request = ReferenceGeometrySolveRequest(
        request_id="fit-plane-after-cylinder",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit",),
        quantity_roles=(
            fixed_axis(),
            free_scalar("plane-offset", 0.02, -0.1, 0.1),
        ),
    )
    compiled = compile_reference_geometry_solve(declaration, request, user_bundle())

    assert compiled.fixed_quantity_ids == ("shared-axis",)
    assert compiled.free_quantity_ids == ("plane-offset",)
    influence = compiled.factor_influences[0]
    assert [
        (item.quantity_id, item.component) for item in influence.free_components
    ] == [("plane-offset", "value")]
    assert influence.exact_geometry_ids == ("axis", "axis-positive", "end-plane")


def test_shared_free_axis_is_driven_by_both_observation_factors() -> None:
    declaration = model()
    before = declaration.model_dump(mode="json")
    request = ReferenceGeometrySolveRequest(
        request_id="joint-without-a-joint-node",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("cylinder-fit", "plane-fit"),
        quantity_roles=(
            free_axis(),
            free_scalar("cylinder-radius", 0.02, 0.001, 0.1),
            free_scalar("plane-offset", 0.02, -0.1, 0.1),
        ),
    )
    compiled = compile_reference_geometry_solve(declaration, request, user_bundle())

    assert compiled.free_quantity_ids == (
        "shared-axis",
        "cylinder-radius",
        "plane-offset",
    )
    cylinder, plane = compiled.factor_influences
    assert [
        (item.quantity_id, item.component) for item in cylinder.free_components
    ] == [
        ("shared-axis", "transverse-position"),
        ("shared-axis", "direction"),
        ("cylinder-radius", "value"),
    ]
    assert [(item.quantity_id, item.component) for item in plane.free_components] == [
        ("shared-axis", "direction"),
        ("plane-offset", "value"),
    ]
    assert declaration.model_dump(mode="json") == before


def test_plane_factor_does_not_claim_transverse_axis_location() -> None:
    declaration = model()
    request = ReferenceGeometrySolveRequest(
        request_id="plane-only-free-axis",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit",),
        quantity_roles=(
            free_axis(),
            free_scalar("plane-offset", 0.02, -0.1, 0.1),
        ),
    )
    compiled = compile_reference_geometry_solve(declaration, request, user_bundle())
    components = compiled.factor_influences[0].reachable_components
    assert all(item.component != "transverse-position" for item in components)


def test_roles_must_exactly_cover_active_dependencies() -> None:
    declaration = model()
    request = ReferenceGeometrySolveRequest(
        request_id="missing-offset-role",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit",),
        quantity_roles=(fixed_axis(),),
    )
    with pytest.raises(ValueError, match=r"missing=\['plane-offset'\]"):
        _ = compile_reference_geometry_solve(declaration, request, user_bundle())


def test_request_binds_exact_model_and_preserves_declaration_order() -> None:
    declaration = model()
    roles = (
        free_axis(),
        free_scalar("cylinder-radius", 0.02, 0.001, 0.1),
        free_scalar("plane-offset", 0.02, -0.1, 0.1),
    )
    wrong_digest = ReferenceGeometrySolveRequest(
        request_id="ordered-request",
        model_id=declaration.model_id,
        active_factor_ids=("cylinder-fit", "plane-fit"),
        quantity_roles=roles,
        model_sha256="f" * 64,
    )
    with pytest.raises(ValueError, match="model digest"):
        _ = compile_reference_geometry_solve(declaration, wrong_digest, user_bundle())

    reversed_factors = ReferenceGeometrySolveRequest(
        request_id="ordered-request",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit", "cylinder-fit"),
        quantity_roles=roles,
    )
    with pytest.raises(ValueError, match="active factors must preserve"):
        _ = compile_reference_geometry_solve(
            declaration, reversed_factors, user_bundle()
        )

    reversed_roles = ReferenceGeometrySolveRequest(
        request_id="ordered-request",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("cylinder-fit", "plane-fit"),
        quantity_roles=tuple(reversed(roles)),
    )
    with pytest.raises(ValueError, match="quantity roles must preserve"):
        _ = compile_reference_geometry_solve(declaration, reversed_roles, user_bundle())

    unknown_factor = ReferenceGeometrySolveRequest(
        request_id="unknown-factor",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("unknown-fit",),
        quantity_roles=(),
    )
    with pytest.raises(ValueError, match="unknown factors"):
        _ = compile_reference_geometry_solve(declaration, unknown_factor, user_bundle())


def test_free_roles_require_real_freedom() -> None:
    with pytest.raises(ValidationError, match="lower < upper"):
        _ = free_scalar("plane-offset", 0.02, 0.02, 0.02)
    payload = free_axis().model_dump(mode="json")
    payload["maximum_direction_delta_rad"] = 1.5707963267948966
    with pytest.raises(ValidationError, match="less than"):
        _ = FreeAxisLineRole.model_validate(payload)


def test_resolved_axis_input_is_content_and_frame_bound() -> None:
    source = fixed_axis().source
    assert isinstance(source, ResultAxisLineSource)
    payload = source.model_dump(mode="json")
    resolved_value = cast(dict[str, Any], payload["resolved_value"])
    resolved_value["closest_point_to_frame_origin_m"] = [0.001, 0.0, 0.0]
    with pytest.raises(ValidationError, match="digest does not match"):
        _ = ResultAxisLineSource.model_validate(payload)

    declaration = model()
    other_frame_source = resolved_axis_line_source(
        frame_id="other-frame",
        quantity_id="fitted-cylinder-axis",
        resolved_value=LiteralAxisLineSource(
            closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
            direction=(0.0, 0.0, 1.0),
        ),
        result_id="standalone-cylinder-fit",
    )
    request = ReferenceGeometrySolveRequest(
        request_id="cross-frame-fixed-axis",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit",),
        quantity_roles=(
            FixedAxisLineRole(quantity_id="shared-axis", source=other_frame_source),
            free_scalar("plane-offset", 0.02, -0.1, 0.1),
        ),
    )
    with pytest.raises(ValueError, match="resolved input crosses frames"):
        _ = compile_reference_geometry_solve(declaration, request, user_bundle())


def test_oriented_direction_rejects_literal_axis_sign_degeneracy() -> None:
    declaration = model()
    degenerate_axis = FreeAxisLineRole(
        quantity_id="shared-axis",
        initial=LiteralAxisLineSource(
            closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
            direction=(1.0, 0.0, 0.0),
        ),
        maximum_direction_delta_rad=0.2,
        maximum_transverse_delta_m=0.005,
        rotation_scale_rad=0.01,
        transverse_scale_m=0.001,
    )
    request = ReferenceGeometrySolveRequest(
        request_id="degenerate-direction",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("plane-fit",),
        quantity_roles=(
            degenerate_axis,
            free_scalar("plane-offset", 0.02, -0.1, 0.1),
        ),
    )
    with pytest.raises(ValueError, match="degenerate alignment hint"):
        _ = compile_reference_geometry_solve(declaration, request, user_bundle())

    near_orthogonal = math.sqrt(1.0 - 0.1**2)
    crossing_axis = degenerate_axis.model_copy(
        update={
            "initial": LiteralAxisLineSource(
                closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
                direction=(near_orthogonal, 0.0, 0.1),
            )
        }
    )
    crossing_request = request.model_copy(
        update={"quantity_roles": (crossing_axis, request.quantity_roles[1])}
    )
    with pytest.raises(ValueError, match="sign boundary"):
        _ = compile_reference_geometry_solve(
            declaration, crossing_request, user_bundle()
        )


def test_axis_value_is_canonical_and_has_no_axial_origin() -> None:
    with pytest.raises(ValidationError, match="canonical sign"):
        _ = LiteralAxisLineSource(
            closest_point_to_frame_origin_m=(0.0, 0.0, 0.0),
            direction=(0.0, 0.0, -1.0),
        )
    with pytest.raises(ValidationError, match="closest point"):
        _ = LiteralAxisLineSource(
            closest_point_to_frame_origin_m=(0.0, 0.0, 1.0),
            direction=(0.0, 0.0, 1.0),
        )


def test_model_rejects_incompatible_exact_references() -> None:
    payload = model().model_dump(mode="json")
    geometry = cast(list[dict[str, Any]], payload["geometry"])
    plane = next(item for item in geometry if item["kind"] == "oriented-plane")
    plane["offset_quantity_id"] = "cylinder-radius"
    with pytest.raises(ValidationError, match="requires a plane-offset quantity"):
        _ = ReferenceGeometryModel.model_validate(payload)


def test_model_rejects_implicit_cross_frame_observation() -> None:
    payload = model().model_dump(mode="json")
    frames = cast(list[dict[str, Any]], payload["frames"])
    frames.append(
        CoordinateFrameDefinition(frame_id="other-frame").model_dump(mode="json")
    )
    factors = cast(list[dict[str, Any]], payload["factors"])
    factors[1]["observation_frame_id"] = "other-frame"
    with pytest.raises(ValidationError, match="crosses frames without a transform"):
        _ = ReferenceGeometryModel.model_validate(payload)


def test_observation_factor_requires_nonempty_exact_membership() -> None:
    payload = model().model_dump(mode="json")
    factors = cast(list[dict[str, Any]], payload["factors"])
    factors[0]["selection_vertex_count"] = 0
    with pytest.raises(ValidationError, match="greater than 0"):
        _ = ReferenceGeometryModel.model_validate(payload)


def test_current_user_selections_bind_without_legacy_recipe_support() -> None:
    bundle = user_bundle()
    validate_selection_bindings(model(), bundle)

    legacy_recipe = (
        ROOT
        / "examples"
        / "nozzle-bayonette-simplified"
        / "recipes"
        / "cylinder-plane.json"
    )
    with pytest.raises(ValidationError):
        _ = ReferenceGeometryModel.model_validate_json(legacy_recipe.read_bytes())


def test_selection_binding_rejects_reused_identity_with_other_membership() -> None:
    bundle = user_bundle()
    payload = model().model_dump(mode="json")
    factors = cast(list[dict[str, Any]], payload["factors"])
    factors[0]["selection_vertex_ids_sha256"] = "f" * 64
    mismatched = ReferenceGeometryModel.model_validate(payload)
    with pytest.raises(ValueError, match="selection membership does not match"):
        validate_selection_bindings(mismatched, bundle)

    factors[0]["selection_vertex_ids_sha256"] = OUTER_IDS_SHA256
    factors[0]["observation_source_sha256"] = "f" * 64
    wrong_source = ReferenceGeometryModel.model_validate(payload)
    with pytest.raises(ValueError, match="selection source digest does not match"):
        validate_selection_bindings(wrong_source, bundle)


def test_resolved_scalar_is_content_bound_and_radius_stays_positive() -> None:
    source = resolved_scalar_source(
        quantity_id="fitted-radius", result_id="cylinder-result", value_m=0.02
    )
    payload = source.model_dump(mode="json")
    payload["value_m"] = 0.03
    with pytest.raises(ValidationError, match="digest does not match"):
        _ = ResultScalarSource.model_validate(payload)

    declaration = model()
    request = ReferenceGeometrySolveRequest(
        request_id="fixed-negative-radius",
        model_id=declaration.model_id,
        model_sha256=reference_geometry_model_sha256(declaration),
        active_factor_ids=("cylinder-fit",),
        quantity_roles=(
            free_axis(),
            FixedScalarRole(
                quantity_id="cylinder-radius",
                source=resolved_scalar_source(
                    quantity_id="fitted-radius",
                    result_id="bad-cylinder-result",
                    value_m=-0.02,
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="requires a positive value"):
        _ = compile_reference_geometry_solve(declaration, request, user_bundle())
