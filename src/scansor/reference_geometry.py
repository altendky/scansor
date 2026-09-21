"""Validated successor contract for shared reference geometry and solve influence."""

from __future__ import annotations

import math
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.models import StrictModel
from scansor.selection_bundle import SelectionBundle
from scansor.serialization import canonical_json, sha256

REFERENCE_GEOMETRY_MODEL_FORMAT = "scansor-reference-geometry-model-v1"
REFERENCE_GEOMETRY_SOLVE_FORMAT = "scansor-reference-geometry-solve-v1"
REFERENCE_GEOMETRY_STATUS = "internal/provisional/non-public-contract"
GEOMETRY_TOLERANCE = 1e-10

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9]+[a-zA-Z0-9_.-]*$"),
]
Vector3 = tuple[float, float, float]
AxisComponent = Literal["transverse-position", "direction"]


class ReferenceGeometryRecord(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


def _validate_unit(value: Vector3, label: str) -> None:
    norm = math.sqrt(sum(component * component for component in value))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=GEOMETRY_TOLERANCE):
        raise ValueError(f"{label} must be a unit vector")


def _validate_canonical_unoriented_direction(value: Vector3) -> None:
    _validate_unit(value, "axis-line direction")
    first_significant = next(
        (component for component in value if abs(component) > GEOMETRY_TOLERANCE),
        None,
    )
    if first_significant is None or first_significant < 0.0:
        message = (
            "axis-line direction must use the "
            "first-significant-component-positive canonical sign"
        )
        raise ValueError(message)


class CoordinateFrameDefinition(ReferenceGeometryRecord):
    coordinate_unit: Literal["m"] = "m"
    frame_id: Identifier
    handedness: Literal["right-handed"] = "right-handed"


class AxisLineQuantity(ReferenceGeometryRecord):
    frame_id: Identifier
    kind: Literal["axis-line"] = "axis-line"
    quantity_id: Identifier


class ScalarQuantity(ReferenceGeometryRecord):
    kind: Literal["scalar"] = "scalar"
    quantity_id: Identifier
    semantic: Literal["radius", "plane-offset"]
    unit: Literal["m"] = "m"


IndependentQuantity = Annotated[
    AxisLineQuantity | ScalarQuantity,
    Field(discriminator="kind"),
]


class AxisLineDefinition(ReferenceGeometryRecord):
    geometry_id: Identifier
    kind: Literal["axis-line"] = "axis-line"
    quantity_id: Identifier


class AxisParallelDirectionDefinition(ReferenceGeometryRecord):
    axis_id: Identifier
    geometry_id: Identifier
    kind: Literal["axis-parallel-oriented-direction"] = (
        "axis-parallel-oriented-direction"
    )
    positive_alignment_hint: Vector3

    @field_validator("positive_alignment_hint", mode="before")
    @classmethod
    def restore_hint(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_hint(self) -> AxisParallelDirectionDefinition:
        _validate_unit(self.positive_alignment_hint, "positive alignment hint")
        return self


class OrientedPlaneDefinition(ReferenceGeometryRecord):
    geometry_id: Identifier
    kind: Literal["oriented-plane"] = "oriented-plane"
    normal_direction_id: Identifier
    offset_quantity_id: Identifier


ReferenceGeometryDefinition = Annotated[
    AxisLineDefinition | AxisParallelDirectionDefinition | OrientedPlaneDefinition,
    Field(discriminator="kind"),
]


class CylinderElement(ReferenceGeometryRecord):
    axis_id: Identifier
    element_id: Identifier
    kind: Literal["cylinder"] = "cylinder"
    radius_quantity_id: Identifier


AnalyticElement = Annotated[CylinderElement, Field(discriminator="kind")]


class CylinderObservationFactor(ReferenceGeometryRecord):
    element_id: Identifier
    factor_id: Identifier
    kind: Literal["cylinder-observation"] = "cylinder-observation"
    observation_frame_id: Identifier
    observation_source_id: Identifier
    observation_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    residual_scale_m: float = Field(gt=0.0)
    selection_id: Identifier
    selection_vertex_count: int = Field(gt=0)
    selection_vertex_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlaneObservationFactor(ReferenceGeometryRecord):
    factor_id: Identifier
    kind: Literal["plane-observation"] = "plane-observation"
    observation_frame_id: Identifier
    observation_source_id: Identifier
    observation_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plane_id: Identifier
    residual_scale_m: float = Field(gt=0.0)
    selection_id: Identifier
    selection_vertex_count: int = Field(gt=0)
    selection_vertex_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


ObservationFactor = Annotated[
    CylinderObservationFactor | PlaneObservationFactor,
    Field(discriminator="kind"),
]


class ReferenceGeometryModel(ReferenceGeometryRecord):
    elements: tuple[AnalyticElement, ...]
    factors: tuple[ObservationFactor, ...]
    format: Literal["scansor-reference-geometry-model-v1"] = (
        REFERENCE_GEOMETRY_MODEL_FORMAT
    )
    format_status: Literal["internal/provisional/non-public-contract"] = (
        REFERENCE_GEOMETRY_STATUS
    )
    frames: tuple[CoordinateFrameDefinition, ...]
    geometry: tuple[ReferenceGeometryDefinition, ...]
    model_id: Identifier
    quantities: tuple[IndependentQuantity, ...]

    @field_validator(
        "elements", "factors", "frames", "geometry", "quantities", mode="before"
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_graph(self) -> ReferenceGeometryModel:
        frames = _unique_by_id(self.frames, "frame_id", "frame")
        quantities = _unique_by_id(self.quantities, "quantity_id", "quantity")
        geometry = _unique_by_id(self.geometry, "geometry_id", "geometry")
        elements = _unique_by_id(self.elements, "element_id", "element")
        _ = _unique_by_id(self.factors, "factor_id", "factor")

        for quantity in self.quantities:
            if (
                isinstance(quantity, AxisLineQuantity)
                and quantity.frame_id not in frames
            ):
                message = (
                    f"axis-line quantity {quantity.quantity_id!r} references an"
                    " undeclared frame"
                )
                raise ValueError(message)

        for item in self.geometry:
            if isinstance(item, AxisLineDefinition):
                _ = _require_type(
                    quantities,
                    item.quantity_id,
                    AxisLineQuantity,
                    f"axis line {item.geometry_id!r}",
                )
            elif isinstance(item, AxisParallelDirectionDefinition):
                _ = _require_type(
                    geometry,
                    item.axis_id,
                    AxisLineDefinition,
                    f"oriented direction {item.geometry_id!r}",
                )
            else:
                _ = _require_type(
                    geometry,
                    item.normal_direction_id,
                    AxisParallelDirectionDefinition,
                    f"plane {item.geometry_id!r}",
                )
                offset = _require_type(
                    quantities,
                    item.offset_quantity_id,
                    ScalarQuantity,
                    f"plane {item.geometry_id!r}",
                )
                if offset.semantic != "plane-offset":
                    raise ValueError(
                        f"plane {item.geometry_id!r} requires a plane-offset quantity"
                    )

        for element in self.elements:
            _ = _require_type(
                geometry,
                element.axis_id,
                AxisLineDefinition,
                f"cylinder {element.element_id!r}",
            )
            radius = _require_type(
                quantities,
                element.radius_quantity_id,
                ScalarQuantity,
                f"cylinder {element.element_id!r}",
            )
            if radius.semantic != "radius":
                raise ValueError(
                    f"cylinder {element.element_id!r} requires a radius quantity"
                )

        for factor in self.factors:
            if factor.observation_frame_id not in frames:
                message = (
                    f"factor {factor.factor_id!r} references an undeclared"
                    + " observation frame"
                )
                raise ValueError(message)
            if isinstance(factor, CylinderObservationFactor):
                element = _require_type(
                    elements,
                    factor.element_id,
                    CylinderElement,
                    f"factor {factor.factor_id!r}",
                )
                axis = _require_type(
                    geometry,
                    element.axis_id,
                    AxisLineDefinition,
                    f"factor {factor.factor_id!r}",
                )
            else:
                plane = _require_type(
                    geometry,
                    factor.plane_id,
                    OrientedPlaneDefinition,
                    f"factor {factor.factor_id!r}",
                )
                direction = _require_type(
                    geometry,
                    plane.normal_direction_id,
                    AxisParallelDirectionDefinition,
                    f"factor {factor.factor_id!r}",
                )
                axis = _require_type(
                    geometry,
                    direction.axis_id,
                    AxisLineDefinition,
                    f"factor {factor.factor_id!r}",
                )
            axis_quantity = _require_type(
                quantities,
                axis.quantity_id,
                AxisLineQuantity,
                f"factor {factor.factor_id!r}",
            )
            if factor.observation_frame_id != axis_quantity.frame_id:
                raise ValueError(
                    f"factor {factor.factor_id!r} crosses frames without a transform"
                )
        return self


def _unique_by_id(
    values: tuple[ReferenceGeometryRecord, ...], field: str, label: str
) -> dict[str, ReferenceGeometryRecord]:
    result = {str(getattr(value, field)): value for value in values}
    if len(result) != len(values):
        raise ValueError(f"{label} IDs must be unique")
    return result


def _require_type[RecordT: ReferenceGeometryRecord](
    records: dict[str, ReferenceGeometryRecord],
    record_id: str,
    expected: type[RecordT],
    owner: str,
) -> RecordT:
    record = records.get(record_id)
    if not isinstance(record, expected):
        message = (
            f"{owner} references missing or incompatible {expected.__name__}"
            f" {record_id!r}"
        )
        raise ValueError(message)
    return record


class LiteralAxisLineSource(ReferenceGeometryRecord):
    closest_point_to_frame_origin_m: Vector3
    direction: Vector3
    kind: Literal["literal-axis-line"] = "literal-axis-line"

    @field_validator("closest_point_to_frame_origin_m", "direction", mode="before")
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_line(self) -> LiteralAxisLineSource:
        _validate_canonical_unoriented_direction(self.direction)
        dot = sum(
            point * direction
            for point, direction in zip(
                self.closest_point_to_frame_origin_m, self.direction, strict=True
            )
        )
        if not math.isclose(dot, 0.0, rel_tol=0.0, abs_tol=GEOMETRY_TOLERANCE):
            raise ValueError(
                "axis-line point must be the closest point to the frame origin"
            )
        return self


class ResultAxisLineSource(ReferenceGeometryRecord):
    frame_id: Identifier
    kind: Literal["resolved-axis-line"] = "resolved-axis-line"
    quantity_id: Identifier
    resolved_value: LiteralAxisLineSource
    result_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_id: Identifier

    @model_validator(mode="after")
    def validate_content_digest(self) -> ResultAxisLineSource:
        if self.result_content_sha256 != _source_content_sha256(self):
            raise ValueError("resolved axis-line source digest does not match content")
        return self


AxisLineSource = Annotated[
    LiteralAxisLineSource | ResultAxisLineSource,
    Field(discriminator="kind"),
]


class LiteralScalarSource(ReferenceGeometryRecord):
    kind: Literal["literal-scalar"] = "literal-scalar"
    value_m: float


class ResultScalarSource(ReferenceGeometryRecord):
    kind: Literal["resolved-scalar"] = "resolved-scalar"
    quantity_id: Identifier
    result_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_id: Identifier
    unit: Literal["m"] = "m"
    value_m: float

    @model_validator(mode="after")
    def validate_content_digest(self) -> ResultScalarSource:
        if self.result_content_sha256 != _source_content_sha256(self):
            raise ValueError("resolved scalar source digest does not match content")
        return self


ScalarSource = Annotated[
    LiteralScalarSource | ResultScalarSource,
    Field(discriminator="kind"),
]


def _source_content_sha256(
    source: ResultAxisLineSource | ResultScalarSource,
) -> str:
    content = source.model_dump(mode="json", exclude={"result_content_sha256"})
    return sha256(canonical_json(content))


def resolved_axis_line_source(
    *,
    frame_id: str,
    quantity_id: str,
    resolved_value: LiteralAxisLineSource,
    result_id: str,
) -> ResultAxisLineSource:
    prototype = ResultAxisLineSource.model_construct(
        frame_id=frame_id,
        quantity_id=quantity_id,
        resolved_value=resolved_value,
        result_content_sha256="",
        result_id=result_id,
    )
    return ResultAxisLineSource(
        frame_id=frame_id,
        quantity_id=quantity_id,
        resolved_value=resolved_value,
        result_content_sha256=_source_content_sha256(prototype),
        result_id=result_id,
    )


def resolved_scalar_source(
    *, quantity_id: str, result_id: str, value_m: float
) -> ResultScalarSource:
    prototype = ResultScalarSource.model_construct(
        quantity_id=quantity_id,
        result_content_sha256="",
        result_id=result_id,
        value_m=value_m,
    )
    return ResultScalarSource(
        quantity_id=quantity_id,
        result_content_sha256=_source_content_sha256(prototype),
        result_id=result_id,
        value_m=value_m,
    )


def _scalar_source_value(source: ScalarSource) -> float:
    return source.value_m


class FixedAxisLineRole(ReferenceGeometryRecord):
    kind: Literal["fixed-axis-line"] = "fixed-axis-line"
    quantity_id: Identifier
    role: Literal["fixed"] = "fixed"
    source: AxisLineSource


class FreeAxisLineRole(ReferenceGeometryRecord):
    initial: AxisLineSource
    kind: Literal["free-axis-line"] = "free-axis-line"
    maximum_direction_delta_rad: float = Field(gt=0.0, lt=math.pi / 2.0)
    maximum_transverse_delta_m: float = Field(gt=0.0)
    quantity_id: Identifier
    role: Literal["free"] = "free"
    rotation_scale_rad: float = Field(gt=0.0)
    transverse_scale_m: float = Field(gt=0.0)


class FixedScalarRole(ReferenceGeometryRecord):
    kind: Literal["fixed-scalar"] = "fixed-scalar"
    quantity_id: Identifier
    role: Literal["fixed"] = "fixed"
    source: ScalarSource


class FreeScalarRole(ReferenceGeometryRecord):
    initial: ScalarSource
    kind: Literal["free-scalar"] = "free-scalar"
    lower_m: float
    quantity_id: Identifier
    role: Literal["free"] = "free"
    scale_m: float = Field(gt=0.0)
    upper_m: float

    @model_validator(mode="after")
    def validate_bounds(self) -> FreeScalarRole:
        if self.lower_m >= self.upper_m:
            raise ValueError("free scalar requires lower < upper")
        if not self.lower_m <= _scalar_source_value(self.initial) <= self.upper_m:
            raise ValueError("free scalar literal initial value is outside its bounds")
        return self


QuantityRole = Annotated[
    FixedAxisLineRole | FreeAxisLineRole | FixedScalarRole | FreeScalarRole,
    Field(discriminator="kind"),
]


class ReferenceGeometrySolveRequest(ReferenceGeometryRecord):
    active_factor_ids: tuple[Identifier, ...]
    format: Literal["scansor-reference-geometry-solve-v1"] = (
        REFERENCE_GEOMETRY_SOLVE_FORMAT
    )
    format_status: Literal["internal/provisional/non-public-contract"] = (
        REFERENCE_GEOMETRY_STATUS
    )
    model_id: Identifier
    model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quantity_roles: tuple[QuantityRole, ...]
    request_id: Identifier

    @field_validator("active_factor_ids", "quantity_roles", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_local_uniqueness(self) -> ReferenceGeometrySolveRequest:
        if not self.active_factor_ids:
            raise ValueError("solve request requires at least one active factor")
        if len(set(self.active_factor_ids)) != len(self.active_factor_ids):
            raise ValueError("active factor IDs must be unique")
        role_ids = [role.quantity_id for role in self.quantity_roles]
        if len(set(role_ids)) != len(role_ids):
            raise ValueError("quantity roles must be unique")
        return self


class QuantityComponentReference(ReferenceGeometryRecord):
    component: Literal["transverse-position", "direction", "value"]
    quantity_id: Identifier


class FactorInfluence(ReferenceGeometryRecord):
    exact_geometry_ids: tuple[Identifier, ...]
    factor_id: Identifier
    free_components: tuple[QuantityComponentReference, ...]
    reachable_components: tuple[QuantityComponentReference, ...]


class CompiledReferenceGeometrySolve(ReferenceGeometryRecord):
    active_factor_ids: tuple[Identifier, ...]
    factor_influences: tuple[FactorInfluence, ...]
    fixed_quantity_ids: tuple[Identifier, ...]
    free_quantity_ids: tuple[Identifier, ...]
    model_id: Identifier
    request_id: Identifier
    selection_bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_selection_bindings(
    model: ReferenceGeometryModel, bundle: SelectionBundle
) -> None:
    sources = {source.source_id: source for source in bundle.sources}
    selections = {selection.selection_id: selection for selection in bundle.selections}
    for factor in model.factors:
        source = sources.get(factor.observation_source_id)
        if source is None:
            raise ValueError(
                f"factor {factor.factor_id!r} references a missing selection source"
            )
        if source.source_sha256 != factor.observation_source_sha256:
            raise ValueError(
                f"factor {factor.factor_id!r} selection source digest does not match"
            )
        selection = selections.get(factor.selection_id)
        if selection is None:
            raise ValueError(
                f"factor {factor.factor_id!r} references a missing selection"
            )
        if (
            selection.source_id != factor.observation_source_id
            or selection.vertex_count != factor.selection_vertex_count
            or selection.vertex_ids_sha256 != factor.selection_vertex_ids_sha256
        ):
            raise ValueError(
                f"factor {factor.factor_id!r} selection membership does not match"
            )


def reference_geometry_model_sha256(model: ReferenceGeometryModel) -> str:
    return sha256(canonical_json(model))


def compile_reference_geometry_solve(
    model: ReferenceGeometryModel,
    request: ReferenceGeometrySolveRequest,
    selection_bundle: SelectionBundle,
) -> CompiledReferenceGeometrySolve:
    validate_selection_bindings(model, selection_bundle)
    if request.model_id != model.model_id:
        raise ValueError("solve request references a different model")
    if request.model_sha256 != reference_geometry_model_sha256(model):
        raise ValueError("solve request model digest does not match the model")
    factors = {factor.factor_id: factor for factor in model.factors}
    quantities = {quantity.quantity_id: quantity for quantity in model.quantities}
    geometry = {item.geometry_id: item for item in model.geometry}
    elements = {element.element_id: element for element in model.elements}
    roles = {role.quantity_id: role for role in request.quantity_roles}

    active = set(request.active_factor_ids)
    unknown_factors = sorted(active - set(factors))
    if unknown_factors:
        raise ValueError(f"solve request references unknown factors: {unknown_factors}")
    expected_factor_order = tuple(
        factor.factor_id for factor in model.factors if factor.factor_id in active
    )
    if request.active_factor_ids != expected_factor_order:
        raise ValueError("active factors must preserve model declaration order")

    influences: list[FactorInfluence] = []
    reachable_quantity_ids: list[str] = []
    for factor_id in request.active_factor_ids:
        factor = factors.get(factor_id)
        if factor is None:
            raise ValueError(f"solve request references unknown factor {factor_id!r}")
        components, exact_geometry_ids = _factor_influence(factor, geometry, elements)
        for component in components:
            if component.quantity_id not in reachable_quantity_ids:
                reachable_quantity_ids.append(component.quantity_id)
        influences.append(
            FactorInfluence(
                exact_geometry_ids=exact_geometry_ids,
                factor_id=factor.factor_id,
                free_components=tuple(
                    component
                    for component in components
                    if component.quantity_id in roles
                    and roles[component.quantity_id].role == "free"
                ),
                reachable_components=components,
            )
        )

    reachable = set(reachable_quantity_ids)
    assigned = set(roles)
    if assigned != reachable:
        missing = sorted(reachable - assigned)
        extra = sorted(assigned - reachable)
        message = (
            "quantity roles must exactly cover active-factor dependencies;"
            f" missing={missing}, extra={extra}"
        )
        raise ValueError(message)

    expected_role_order = tuple(
        quantity.quantity_id
        for quantity in model.quantities
        if quantity.quantity_id in reachable
    )
    if tuple(roles) != expected_role_order:
        raise ValueError("quantity roles must preserve model declaration order")

    for quantity_id, role in roles.items():
        quantity = quantities[quantity_id]
        if isinstance(quantity, AxisLineQuantity):
            if not isinstance(role, FixedAxisLineRole | FreeAxisLineRole):
                raise ValueError(
                    f"axis-line quantity {quantity_id!r} has a scalar role"
                )
            source = (
                role.source if isinstance(role, FixedAxisLineRole) else role.initial
            )
            if (
                isinstance(source, ResultAxisLineSource)
                and source.frame_id != quantity.frame_id
            ):
                raise ValueError(
                    f"axis-line quantity {quantity_id!r} resolved input crosses frames"
                )
        elif not isinstance(role, FixedScalarRole | FreeScalarRole):
            raise ValueError(f"scalar quantity {quantity_id!r} has an axis-line role")
        elif quantity.semantic == "radius":
            _validate_radius_role(role, quantity_id)

    _validate_active_direction_orientations(influences, geometry, roles)

    return CompiledReferenceGeometrySolve(
        active_factor_ids=request.active_factor_ids,
        factor_influences=tuple(influences),
        fixed_quantity_ids=tuple(
            quantity_id
            for quantity_id in reachable_quantity_ids
            if roles[quantity_id].role == "fixed"
        ),
        free_quantity_ids=tuple(
            quantity_id
            for quantity_id in reachable_quantity_ids
            if roles[quantity_id].role == "free"
        ),
        model_id=model.model_id,
        request_id=request.request_id,
        selection_bundle_sha256=sha256(canonical_json(selection_bundle)),
    )


def _validate_radius_role(role: FixedScalarRole | FreeScalarRole, label: str) -> None:
    if isinstance(role, FreeScalarRole) and role.lower_m <= 0.0:
        raise ValueError(f"radius quantity {label!r} requires a positive lower bound")
    if isinstance(role, FixedScalarRole) and _scalar_source_value(role.source) <= 0.0:
        raise ValueError(f"radius quantity {label!r} requires a positive value")


def _validate_active_direction_orientations(
    influences: list[FactorInfluence],
    geometry: dict[str, ReferenceGeometryDefinition],
    roles: dict[str, QuantityRole],
) -> None:
    active_geometry = {
        geometry_id
        for influence in influences
        for geometry_id in influence.exact_geometry_ids
    }
    for geometry_id in active_geometry:
        direction = geometry[geometry_id]
        if not isinstance(direction, AxisParallelDirectionDefinition):
            continue
        axis = geometry[direction.axis_id]
        assert isinstance(axis, AxisLineDefinition)
        role = roles[axis.quantity_id]
        if isinstance(role, FixedAxisLineRole):
            source = role.source
        else:
            assert isinstance(role, FreeAxisLineRole)
            source = role.initial
        value = (
            source.resolved_value
            if isinstance(source, ResultAxisLineSource)
            else source
        )
        alignment = abs(
            sum(
                component * hint
                for component, hint in zip(
                    value.direction,
                    direction.positive_alignment_hint,
                    strict=True,
                )
            )
        )
        if alignment <= GEOMETRY_TOLERANCE:
            message = (
                f"oriented direction {direction.geometry_id!r} has a degenerate"
                + " alignment hint for its axis value"
            )
            raise ValueError(message)
        if isinstance(role, FreeAxisLineRole):
            sign_boundary_angle = math.asin(min(1.0, alignment))
            if role.maximum_direction_delta_rad >= sign_boundary_angle:
                message = (
                    f"free axis {axis.quantity_id!r} may cross its"
                    + " oriented-direction sign boundary"
                )
                raise ValueError(message)


def _factor_influence(
    factor: ObservationFactor,
    geometry: dict[str, ReferenceGeometryDefinition],
    elements: dict[str, AnalyticElement],
) -> tuple[tuple[QuantityComponentReference, ...], tuple[str, ...]]:
    if isinstance(factor, CylinderObservationFactor):
        cylinder = elements[factor.element_id]
        axis = geometry[cylinder.axis_id]
        assert isinstance(cylinder, CylinderElement)
        assert isinstance(axis, AxisLineDefinition)
        return (
            (
                QuantityComponentReference(
                    component="transverse-position", quantity_id=axis.quantity_id
                ),
                QuantityComponentReference(
                    component="direction", quantity_id=axis.quantity_id
                ),
                QuantityComponentReference(
                    component="value", quantity_id=cylinder.radius_quantity_id
                ),
            ),
            (axis.geometry_id,),
        )
    plane = geometry[factor.plane_id]
    assert isinstance(plane, OrientedPlaneDefinition)
    direction = geometry[plane.normal_direction_id]
    assert isinstance(direction, AxisParallelDirectionDefinition)
    axis = geometry[direction.axis_id]
    assert isinstance(axis, AxisLineDefinition)
    return (
        (
            QuantityComponentReference(
                component="direction", quantity_id=axis.quantity_id
            ),
            QuantityComponentReference(
                component="value", quantity_id=plane.offset_quantity_id
            ),
        ),
        (axis.geometry_id, direction.geometry_id, plane.geometry_id),
    )
