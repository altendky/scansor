from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.errors import ScansorError
from scansor.geometry_evaluator import DeclaredGeometryEvaluator
from scansor.model_declarations import (
    Identifier,
    ModelDeclaration,
    revalidate_model_declaration,
)
from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

FACTOR_FORMAT = "scansor-declared-analytic-model-factors-v1"
FACTOR_STATUS = (
    "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
)

FailureCode = Literal[
    "empty-active-selection",
    "missing-required-support",
    "insufficient-coverage",
    "parameter-out-of-bounds",
    "structural-geometry-invalid",
    "evaluation-failed",
    "rank-evaluation-failed",
    "rank-deficient",
]
FAILURE_CODE_ORDER: tuple[FailureCode, ...] = (
    "empty-active-selection",
    "missing-required-support",
    "insufficient-coverage",
    "parameter-out-of-bounds",
    "structural-geometry-invalid",
    "evaluation-failed",
    "rank-evaluation-failed",
    "rank-deficient",
)


class DeclaredFactorStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


def content_id(prefix: str, value: StrictModel, field: str) -> str:
    semantic = value.model_dump(mode="json", exclude={field})
    return f"{prefix}.{sha256(canonical_json(semantic))}"


class FactorDeclaration(DeclaredFactorStrictModel):
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    declaration_id: str = Field(pattern=r"^factor-declaration\.[0-9a-f]{64}$")
    element_id: Identifier
    mapping_id: str = Field(pattern=r"^mapping\.[0-9a-f]{24}$")
    mapping_role: Literal["primary-geometric"] = "primary-geometric"
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    row_index: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_id(self) -> FactorDeclaration:
        if self.declaration_id != content_id(
            "factor-declaration", self, "declaration_id"
        ):
            raise ValueError("factor declaration ID does not match semantic content")
        return self


class InstantiatedFactor(DeclaredFactorStrictModel):
    declaration_id: str = Field(pattern=r"^factor-declaration\.[0-9a-f]{64}$")
    factor_id: str = Field(pattern=r"^factor\.[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    nominal_coverage_cell_ids: tuple[str, ...]
    point_model_m: tuple[float, float, float]

    @field_validator("nominal_coverage_cell_ids", "point_model_m", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_id(self) -> InstantiatedFactor:
        if len(self.nominal_coverage_cell_ids) != len(
            set(self.nominal_coverage_cell_ids)
        ):
            raise ValueError("factor nominal coverage cell IDs must be unique")
        if self.factor_id != content_id("factor", self, "factor_id"):
            raise ValueError("instantiated factor ID does not match semantic content")
        return self


class InstantiatedFactorSet(DeclaredFactorStrictModel):
    declaration: ModelDeclaration
    declarations: tuple[FactorDeclaration, ...]
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    factors: tuple[InstantiatedFactor, ...]
    format: Literal["scansor-declared-analytic-model-factors-v1"] = FACTOR_FORMAT
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = FACTOR_STATUS
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")

    @field_validator("declarations", "factors", mode="before")
    @classmethod
    def restore_records(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_graph(self) -> InstantiatedFactorSet:
        try:
            model = revalidate_model_declaration(self.declaration)
        except ScansorError as error:
            raise ValueError(
                f"factor-set model declaration is invalid: {error}"
            ) from error
        if model != self.declaration or self.model_id != model.model_id:
            raise ValueError("factor-set model binding is inconsistent")
        if not self.declarations:
            raise ValueError("instantiated factor set must not be empty")
        if len(self.declarations) != len(self.factors):
            raise ValueError("factor declarations and instances disagree")
        declaration_ids = [item.declaration_id for item in self.declarations]
        factor_ids = [item.factor_id for item in self.factors]
        if len(declaration_ids) != len(set(declaration_ids)):
            raise ValueError("duplicate factor declaration ID")
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("duplicate instantiated factor ID")
        for label, identifiers in (
            ("mapping", [item.mapping_id for item in self.declarations]),
            ("candidate", [item.candidate_id for item in self.declarations]),
            ("observation", [item.observation_id for item in self.declarations]),
        ):
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate source {label} ID")
        rows = [item.row_index for item in self.declarations]
        if rows != sorted(rows) or len(rows) != len(set(rows)):
            raise ValueError("factor declarations must preserve canonical row order")
        element_ids = {item.element_id for item in model.elements}
        coverage_by_element = {
            element_id: tuple(
                cell.cell_id
                for cell in model.optimization_preflight.coverage_cells
                if cell.element_id == element_id
            )
            for element_id in element_ids
        }
        nominal = tuple(item.nominal for item in model.parameters)
        evaluator = DeclaredGeometryEvaluator(model, nominal)
        for factor_declaration, factor in zip(
            self.declarations, self.factors, strict=True
        ):
            if (
                factor.declaration_id != factor_declaration.declaration_id
                or factor.model_id != self.model_id
                or factor_declaration.model_id != self.model_id
                or factor_declaration.mapping_run_id != self.mapping_run_id
            ):
                raise ValueError("factor graph model or provenance binding disagrees")
            if factor_declaration.element_id not in element_ids:
                raise ValueError("factor references an undeclared model element")
            allowed_cells = coverage_by_element[factor_declaration.element_id]
            cell_by_id = {
                cell.cell_id: cell
                for cell in model.optimization_preflight.coverage_cells
            }
            expected_cells = tuple(
                cell_id
                for cell_id in allowed_cells
                if evaluator.classify_coverage(
                    factor_declaration.element_id,
                    cell_by_id[cell_id].domain,
                    factor.point_model_m,
                ).projected_inside
            )
            if expected_cells != factor.nominal_coverage_cell_ids:
                raise ValueError("factor nominal coverage classifications are invalid")
        if self.factor_set_id != content_id("factor-set", self, "factor_set_id"):
            raise ValueError("factor set ID does not match semantic content")
        return self


class ActiveFactorSelection(DeclaredFactorStrictModel):
    active_factor_ids: tuple[str, ...]
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
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


class ParameterVector(DeclaredFactorStrictModel):
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    units: Literal["m"] = "m"
    values: tuple[float, ...]

    @field_validator("values", mode="before")
    @classmethod
    def restore_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_values(self) -> ParameterVector:
        if not self.values:
            raise ValueError("parameter vector must not be empty")
        if not all(math.isfinite(value) for value in self.values):
            raise ValueError("parameter vector must be finite")
        return self


class FactorEvaluation(DeclaredFactorStrictModel):
    active_factor_ids: tuple[str, ...]
    evaluation_id: str = Field(pattern=r"^evaluation\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    identity_normalization: Literal[True] = True
    jacobian: tuple[tuple[float, ...], ...]
    linear_loss: Literal[True] = True
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
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
        if (
            self.parameters.model_id != self.model_id
            or len(self.active_factor_ids) != len(self.raw_residuals_m)
            or len(self.active_factor_ids) != len(set(self.active_factor_ids))
            or len(self.jacobian) != len(self.raw_residuals_m)
            or len(self.parameter_order) != len(self.parameters.values)
            or len(self.parameter_order) != len(set(self.parameter_order))
            or any(len(row) != len(self.parameter_order) for row in self.jacobian)
        ):
            raise ValueError("factor evaluation dimensions or model binding disagree")
        if self.evaluation_id != content_id("evaluation", self, "evaluation_id"):
            raise ValueError("factor evaluation ID does not match semantic content")
        return self


class PreflightDiagnostics(DeclaredFactorStrictModel):
    coverage_cell_counts: dict[str, int]
    coverage_cell_order: tuple[str, ...]
    eligible_for_optimization: bool
    evaluation_id: str | None
    expected_rank: int = Field(ge=0)
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    failure_codes: tuple[FailureCode, ...]
    missing_coverage_cells: tuple[str, ...]
    missing_required_support: tuple[str, ...]
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observed_rank: int = Field(ge=0)
    parameter_order: tuple[str, ...]
    parameter_scales: tuple[float, ...]
    preflight_id: str = Field(pattern=r"^preflight\.[0-9a-f]{64}$")
    rank_relative_threshold: float = Field(gt=0.0, lt=1.0)
    required_support_counts: dict[str, int]
    required_support_order: tuple[str, ...]
    residual_scale_m: float = Field(gt=0.0)
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    singular_values_dimensionless: tuple[float, ...]

    @field_validator(
        "coverage_cell_order",
        "failure_codes",
        "missing_coverage_cells",
        "missing_required_support",
        "parameter_order",
        "parameter_scales",
        "required_support_order",
        "singular_values_dimensionless",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_diagnostics(self) -> PreflightDiagnostics:
        expected_failures = tuple(
            code for code in FAILURE_CODE_ORDER if code in self.failure_codes
        )
        if (
            self.eligible_for_optimization != (not self.failure_codes)
            or self.failure_codes != expected_failures
        ):
            raise ValueError("preflight eligibility or failure ordering disagrees")
        if len(self.parameter_order) != len(self.parameter_scales):
            raise ValueError("preflight parameter scales disagree with order")
        if (
            len(self.parameter_order) != len(set(self.parameter_order))
            or self.expected_rank > len(self.parameter_order)
            or any(scale <= 0.0 for scale in self.parameter_scales)
        ):
            raise ValueError("preflight rank policy is invalid")
        if any(value < 0 for value in self.required_support_counts.values()) or any(
            value < 0 for value in self.coverage_cell_counts.values()
        ):
            raise ValueError("preflight counts must be nonnegative")
        if (
            any(value < 0.0 for value in self.singular_values_dimensionless)
            or tuple(sorted(self.singular_values_dimensionless, reverse=True))
            != self.singular_values_dimensionless
        ):
            raise ValueError("preflight singular values are invalid")
        if tuple(self.required_support_counts) != self.required_support_order:
            raise ValueError("required support count order disagrees")
        if tuple(self.coverage_cell_counts) != self.coverage_cell_order:
            raise ValueError("coverage cell count order disagrees")
        if self.preflight_id != content_id("preflight", self, "preflight_id"):
            raise ValueError("preflight ID does not match semantic content")
        return self
