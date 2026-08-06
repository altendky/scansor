from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

Variant = Literal["axisymmetric", "asymmetric-datum-flat"]
Problem = Literal["fixed-pose-shape", "fixed-geometry-pose-correction"]
FactorKind = Literal["cylindrical", "axial-planar", "datum-planar"]
FailureCode = Literal[
    "empty-active-selection",
    "missing-active-elements",
    "parameter-out-of-bounds",
    "structural-geometry-invalid",
    "radial-zero-evaluation",
    "nonfinite-evaluation",
    "evaluation-undefined",
    "rank-evaluation-failed",
    "rank-deficient",
    "unexpected-pose-gauge",
]
FAILURE_CODE_ORDER: tuple[FailureCode, ...] = (
    "empty-active-selection",
    "missing-active-elements",
    "parameter-out-of-bounds",
    "structural-geometry-invalid",
    "radial-zero-evaluation",
    "nonfinite-evaluation",
    "evaluation-undefined",
    "rank-evaluation-failed",
    "rank-deficient",
    "unexpected-pose-gauge",
)
ElementId = Literal[
    "cylinder.band-1",
    "cylinder.band-2",
    "cylinder.band-3",
    "plane.station-0",
    "plane.station-20",
    "plane.station-50",
    "plane.station-80",
    "plane.datum-flat",
]

AXISYMMETRIC_SHAPE_PARAMETERS = (
    "r1",
    "r2",
    "r3",
    "s20",
    "s50",
    "s80",
)
ASYMMETRIC_SHAPE_PARAMETERS = (*AXISYMMETRIC_SHAPE_PARAMETERS, "datum_x")
POSE_PARAMETERS = ("tx", "ty", "tz", "phix", "phiy", "phiz")
NOMINAL_SHAPE = (0.012, 0.018, 0.014, 0.020, 0.050, 0.080, 0.016)
SHAPE_LOWER = (0.010, 0.017, 0.012, 0.018, 0.047, 0.077, 0.015)
SHAPE_UPPER = (0.0145, 0.020, 0.0145, 0.022, 0.053, 0.083, 0.0165)
SHAPE_SCALES = (0.001,) * 7
POSE_LOWER = (-0.003, -0.003, -0.003, -0.08, -0.08, -0.08)
POSE_UPPER = (0.003, 0.003, 0.003, 0.08, 0.08, 0.08)
POSE_SCALES = (0.001, 0.001, 0.001, 1.0, 1.0, 1.0)


def content_id(prefix: str, value: StrictModel, field: str) -> str:
    semantic = value.model_dump(mode="json", exclude={field})
    return f"{prefix}.{sha256(canonical_json(semantic))}"


class FactorStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class FactorContract(FactorStrictModel):
    contract: Literal["stepped-rotational-v0-factor-v1"] = (
        "stepped-rotational-v0-factor-v1"
    )
    contract_id: str = Field(pattern=r"^contract\.[0-9a-f]{64}$")
    format_status: Literal["internal/provisional/synthetic-only/non-public"] = (
        "internal/provisional/synthetic-only/non-public"
    )
    identity_normalization: Literal[True] = True
    linear_loss: Literal[True] = True
    nominal_shape_m: tuple[float, ...] = NOMINAL_SHAPE
    pose_lower_m_rad: tuple[float, ...] = POSE_LOWER
    pose_parameter_order: tuple[str, ...] = POSE_PARAMETERS
    pose_scales_m_rad: tuple[float, ...] = POSE_SCALES
    pose_upper_m_rad: tuple[float, ...] = POSE_UPPER
    rank_relative_threshold: float = 1e-10
    residual_scale_m: float = 0.001
    shape_lower_m: tuple[float, ...] = SHAPE_LOWER
    shape_parameter_order: tuple[str, ...] = ASYMMETRIC_SHAPE_PARAMETERS
    shape_scales_m: tuple[float, ...] = SHAPE_SCALES
    shape_upper_m: tuple[float, ...] = SHAPE_UPPER
    topology: Literal["fixed"] = "fixed"
    unit_factor_weight: float = 1.0
    units: Literal["metre/radian"] = "metre/radian"

    @field_validator(
        "nominal_shape_m",
        "pose_lower_m_rad",
        "pose_parameter_order",
        "pose_scales_m_rad",
        "pose_upper_m_rad",
        "shape_lower_m",
        "shape_parameter_order",
        "shape_scales_m",
        "shape_upper_m",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_contract(self) -> FactorContract:
        if self.contract_id != content_id("contract", self, "contract_id"):
            raise ValueError("factor contract ID does not match semantic content")
        if (
            self.nominal_shape_m != NOMINAL_SHAPE
            or self.shape_lower_m != SHAPE_LOWER
            or self.shape_upper_m != SHAPE_UPPER
            or self.shape_scales_m != SHAPE_SCALES
            or self.pose_lower_m_rad != POSE_LOWER
            or self.pose_upper_m_rad != POSE_UPPER
            or self.pose_scales_m_rad != POSE_SCALES
            or self.shape_parameter_order != ASYMMETRIC_SHAPE_PARAMETERS
            or self.pose_parameter_order != POSE_PARAMETERS
            or self.rank_relative_threshold != 1e-10
            or self.residual_scale_m != 0.001
            or self.unit_factor_weight != 1.0
        ):
            raise ValueError("factor contract fixed policy changed")
        return self


def factor_contract() -> FactorContract:
    values = FactorContract.model_construct(contract_id="")
    return FactorContract(
        contract_id=content_id("contract", values, "contract_id"),
    )


class FactorDeclaration(FactorStrictModel):
    candidate_id: str
    contract_id: str = Field(pattern=r"^contract\.[0-9a-f]{64}$")
    declaration_id: str = Field(pattern=r"^declaration\.[0-9a-f]{64}$")
    element_id: ElementId
    factor_kind: FactorKind
    mapping_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_id: str
    mapping_role: Literal["primary-geometric"] = "primary-geometric"
    mapping_request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_id: str
    residual_definition: Literal["oriented-analytic-support-distance-v1"] = (
        "oriented-analytic-support-distance-v1"
    )
    residual_unit: Literal["m"] = "m"
    row_index: int = Field(ge=0)
    variant: Variant

    @model_validator(mode="after")
    def validate_id(self) -> FactorDeclaration:
        expected_kind: FactorKind
        if self.element_id.startswith("cylinder.band-"):
            expected_kind = "cylindrical"
        elif self.element_id == "plane.datum-flat":
            expected_kind = "datum-planar"
        else:
            expected_kind = "axial-planar"
        if self.factor_kind != expected_kind:
            raise ValueError("factor kind disagrees with element")
        if self.variant == "axisymmetric" and self.element_id == "plane.datum-flat":
            raise ValueError("axisymmetric factors cannot reference the datum plane")
        if self.declaration_id != content_id("declaration", self, "declaration_id"):
            raise ValueError("factor declaration ID does not match semantic content")
        return self


class InstantiatedFactor(FactorStrictModel):
    declaration_id: str = Field(pattern=r"^declaration\.[0-9a-f]{64}$")
    factor_id: str = Field(pattern=r"^factor\.[0-9a-f]{64}$")
    point_model_m: tuple[float, float, float]

    @field_validator("point_model_m", mode="before")
    @classmethod
    def restore_point(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_id(self) -> InstantiatedFactor:
        if self.factor_id != content_id("factor", self, "factor_id"):
            raise ValueError("instantiated factor ID does not match semantic content")
        return self


class InstantiatedFactorSet(FactorStrictModel):
    contract: FactorContract
    declarations: tuple[FactorDeclaration, ...]
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    factors: tuple[InstantiatedFactor, ...]
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_mapping_disposition: Literal["accepted"] = "accepted"
    variant: Variant

    @field_validator("declarations", "factors", mode="before")
    @classmethod
    def restore_records(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_graph(self) -> InstantiatedFactorSet:
        declaration_ids = [item.declaration_id for item in self.declarations]
        factor_ids = [item.factor_id for item in self.factors]
        mapping_ids = [item.mapping_id for item in self.declarations]
        candidate_ids = [item.candidate_id for item in self.declarations]
        observation_ids = [item.observation_id for item in self.declarations]
        rows = [item.row_index for item in self.declarations]
        if not self.declarations:
            raise ValueError("instantiated factor set must not be empty")
        if len(declaration_ids) != len(set(declaration_ids)):
            raise ValueError("duplicate factor declaration ID")
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("duplicate instantiated factor ID")
        for label, identifiers in (
            ("mapping", mapping_ids),
            ("candidate", candidate_ids),
            ("observation", observation_ids),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate source {label} ID")
        if rows != sorted(rows) or len(rows) != len(set(rows)):
            raise ValueError(
                "factor declarations must preserve unique mapping-relative row order"
            )
        if len(self.declarations) != len(self.factors) or any(
            factor.declaration_id != declaration.declaration_id
            for declaration, factor in zip(self.declarations, self.factors, strict=True)
        ):
            raise ValueError("factor declarations and instances disagree")
        if any(
            declaration.mapping_run_id != self.mapping_run_id
            or declaration.variant != self.variant
            or declaration.contract_id != self.contract.contract_id
            for declaration in self.declarations
        ):
            raise ValueError("factor declaration provenance disagrees with factor set")
        if (
            len({item.mapping_content_sha256 for item in self.declarations}) != 1
            or len({item.mapping_request_sha256 for item in self.declarations}) != 1
        ):
            raise ValueError("factor declarations mix mapping revisions")
        if self.factor_set_id != content_id("factor-set", self, "factor_set_id"):
            raise ValueError("factor set ID does not match semantic content")
        return self


class ActiveFactorSelection(FactorStrictModel):
    active_factor_ids: tuple[str, ...]
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")

    @field_validator("active_factor_ids", mode="before")
    @classmethod
    def restore_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_id(self) -> ActiveFactorSelection:
        if len(self.active_factor_ids) != len(set(self.active_factor_ids)):
            raise ValueError("active factor IDs must be unique")
        if self.selection_id != content_id("selection", self, "selection_id"):
            raise ValueError(
                "active-factor selection ID does not match semantic content"
            )
        return self


class ParameterVector(FactorStrictModel):
    problem: Problem
    units: Literal["metre", "metre/radian"]
    values: tuple[float, ...]
    variant: Variant

    @field_validator("values", mode="before")
    @classmethod
    def restore_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_dimension(self) -> ParameterVector:
        expected = (
            7
            if self.problem == "fixed-pose-shape"
            and self.variant == "asymmetric-datum-flat"
            else 6
        )
        if len(self.values) != expected:
            raise ValueError(f"{self.problem} requires exactly {expected} parameters")
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("parameter vector must be finite")
        expected_units = (
            "metre" if self.problem == "fixed-pose-shape" else "metre/radian"
        )
        if self.units != expected_units:
            raise ValueError("parameter vector units disagree with problem")
        return self


class FactorEvaluation(FactorStrictModel):
    active_factor_ids: tuple[str, ...]
    evaluation_id: str = Field(pattern=r"^evaluation\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    identity_normalization: Literal[True] = True
    jacobian: tuple[tuple[float, ...], ...]
    linear_loss: Literal[True] = True
    parameter_order: tuple[str, ...]
    parameters: ParameterVector
    raw_residuals_m: tuple[float, ...]
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    unit_factor_weight: float = Field(default=1.0, ge=1.0, le=1.0)

    @field_validator(
        "active_factor_ids",
        "jacobian",
        "parameter_order",
        "raw_residuals_m",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        if value and isinstance(value[0], list):
            return tuple(tuple(row) for row in value)
        return tuple(value)

    @model_validator(mode="after")
    def validate_evaluation(self) -> FactorEvaluation:
        expected_order = (
            ASYMMETRIC_SHAPE_PARAMETERS
            if self.parameters.problem == "fixed-pose-shape"
            and self.parameters.variant == "asymmetric-datum-flat"
            else (
                AXISYMMETRIC_SHAPE_PARAMETERS
                if self.parameters.problem == "fixed-pose-shape"
                else POSE_PARAMETERS
            )
        )
        if (
            len(self.active_factor_ids) != len(self.raw_residuals_m)
            or len(self.active_factor_ids) != len(set(self.active_factor_ids))
            or len(self.jacobian) != len(self.raw_residuals_m)
            or any(len(row) != len(self.parameter_order) for row in self.jacobian)
            or len(self.parameter_order) != len(self.parameters.values)
            or self.parameter_order != expected_order
        ):
            raise ValueError("factor evaluation dimensions disagree")
        if self.evaluation_id != content_id("evaluation", self, "evaluation_id"):
            raise ValueError("factor evaluation ID does not match semantic content")
        return self


class ActiveElementCount(FactorStrictModel):
    count: int = Field(ge=0)
    element_id: ElementId


class PreflightDiagnostics(FactorStrictModel):
    active_element_counts: tuple[ActiveElementCount, ...]
    eligible_for_optimization: bool
    evaluation_id: str | None
    expected_rank: int = Field(ge=0)
    failure_codes: tuple[FailureCode, ...]
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    missing_active_elements: tuple[str, ...]
    observed_rank: int = Field(ge=0)
    parameter_order: tuple[str, ...]
    parameter_scales: tuple[float, ...]
    preflight_id: str = Field(pattern=r"^preflight\.[0-9a-f]{64}$")
    problem: Problem
    rank_relative_threshold: float
    residual_scale_m: float
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    singular_values_dimensionless: tuple[float, ...]
    variant: Variant

    @field_validator(
        "active_element_counts",
        "failure_codes",
        "missing_active_elements",
        "parameter_order",
        "parameter_scales",
        "singular_values_dimensionless",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_diagnostics(self) -> PreflightDiagnostics:
        if self.eligible_for_optimization != (not self.failure_codes):
            raise ValueError("preflight eligibility and failures disagree")
        expected_failures = tuple(
            code for code in FAILURE_CODE_ORDER if code in self.failure_codes
        )
        if self.failure_codes != expected_failures:
            raise ValueError("preflight failure codes are duplicated or out of order")
        if len(self.parameter_order) != len(self.parameter_scales):
            raise ValueError("preflight parameter scales disagree with order")
        element_ids = [item.element_id for item in self.active_element_counts]
        if len(element_ids) != len(set(element_ids)):
            raise ValueError("preflight element counts contain duplicates")
        if self.preflight_id != content_id("preflight", self, "preflight_id"):
            raise ValueError("preflight ID does not match semantic content")
        return self
