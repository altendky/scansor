from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.declared_execution_models import (
    MAX_CALLBACK_ATTEMPTS,
    MAX_CALLBACK_TRACE_BYTES,
    AdapterDescriptor,
    Disposition,
    ExecutionResult,
    HeldOutAssessment,
)
from scansor.declared_factor_models import ActiveFactorSelection, ParameterVector
from scansor.errors import ScansorError
from scansor.model_declarations import ModelDeclaration, revalidate_model_declaration
from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

EXECUTION_RUN_FORMAT = "scansor-declared-analytic-model-execution-run-manifest-v1"
FORMAT_STATUS = (
    "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
)


def run_content_id(prefix: str, value: StrictModel, field: str) -> str:
    semantic = value.model_dump(mode="json", exclude={field})
    return f"{prefix}.{sha256(canonical_json(semantic))}"


class ExecutionRunStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


def _validated_declaration(declaration: ModelDeclaration) -> ModelDeclaration:
    try:
        return revalidate_model_declaration(declaration)
    except ScansorError as error:
        raise ValueError(
            f"execution-run model declaration is invalid: {error}"
        ) from error


class ExecutionRunArtifact(ExecutionRunStrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionRunSelection(ExecutionRunStrictModel):
    active_selection: ActiveFactorSelection
    adapter: AdapterDescriptor
    callback_limit: int = Field(ge=1, le=MAX_CALLBACK_ATTEMPTS)
    callback_trace_byte_limit: int = Field(ge=1_024, le=MAX_CALLBACK_TRACE_BYTES)
    declaration: ModelDeclaration
    execution_selection_id: str = Field(pattern=r"^execution-selection\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    format: Literal["scansor-declared-analytic-model-execution-selection-v1"] = (
        "scansor-declared-analytic-model-execution-selection-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = FORMAT_STATUS
    initial_parameters: ParameterVector
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_selection(self) -> ExecutionRunSelection:
        declaration = _validated_declaration(self.declaration)
        if (
            declaration != self.declaration
            or self.model_id != declaration.model_id
            or self.active_selection.model_id != self.model_id
            or self.initial_parameters.model_id != self.model_id
            or len(self.initial_parameters.values) != len(declaration.parameters)
            or self.factor_set_id != self.active_selection.factor_set_id
            or self.execution_selection_id
            != run_content_id("execution-selection", self, "execution_selection_id")
        ):
            raise ValueError(
                "execution selection model binding, provenance, or ID is invalid"
            )
        return self


class InspectionRunReference(ExecutionRunStrictModel):
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class MappingRunReference(ExecutionRunStrictModel):
    format: Literal["scansor-declared-analytic-model-mapping-v2"]
    manifest_format: Literal["scansor-declared-analytic-model-mapping-manifest-v2"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class HeldOutRunReference(ExecutionRunStrictModel):
    assessment_id: str | None = Field(
        default=None, pattern=r"^held-out-assessment\.[0-9a-f]{64}$"
    )
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    state: Literal["assessed", "not-applicable-noncompleted"]

    @model_validator(mode="after")
    def validate_state(self) -> HeldOutRunReference:
        assessed = self.state == "assessed"
        if assessed != (self.assessment_id is not None and self.sha256 is not None):
            raise ValueError("held-out execution-run state is inconsistent")
        return self


class ExecutionRunManifest(ExecutionRunStrictModel):
    adapter: AdapterDescriptor
    artifacts: dict[str, ExecutionRunArtifact]
    declaration: ModelDeclaration
    disposition: Disposition
    execution_run_id: str = Field(pattern=r"^execution-run\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    format: Literal["scansor-declared-analytic-model-execution-run-manifest-v1"] = (
        EXECUTION_RUN_FORMAT
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = FORMAT_STATUS
    held_out: HeldOutRunReference
    inspection: InspectionRunReference
    mapping: MappingRunReference
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    problem: Literal["fixed-pose-shape"] = "fixed-pose-shape"
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    result_id: str = Field(pattern=r"^execution-result\.[0-9a-f]{64}$")
    selection: ExecutionRunSelection

    @field_validator("artifacts")
    @classmethod
    def copy_artifacts(
        cls, value: dict[str, ExecutionRunArtifact]
    ) -> dict[str, ExecutionRunArtifact]:
        return dict(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> ExecutionRunManifest:
        declaration = _validated_declaration(self.declaration)
        completed = self.disposition == "completed-not-assessed"
        expected_names = {"selection.json", "result.json"}
        if completed:
            expected_names.add("held-out.json")
        if (
            declaration != self.declaration
            or self.model_id != declaration.model_id
            or self.problem != declaration.problem.kind
            or self.selection.declaration != declaration
            or self.selection.model_id != self.model_id
            or self.mapping.model_id != self.model_id
            or set(self.artifacts) != expected_names
            or completed != (self.held_out.state == "assessed")
            or self.factor_set_id != self.selection.factor_set_id
            or self.mapping.run_id != self.selection.mapping_run_id
            or self.adapter != self.selection.adapter
            or self.execution_run_id
            != run_content_id("execution-run", self, "execution_run_id")
        ):
            raise ValueError(
                "execution-run manifest graph, model binding, or ID is invalid"
            )
        return self


class ExecutionRunRecords(ExecutionRunStrictModel):
    held_out: HeldOutAssessment | None
    manifest: ExecutionRunManifest | None = None
    result: ExecutionResult
    selection: ExecutionRunSelection

    @model_validator(mode="after")
    def validate_records(self) -> ExecutionRunRecords:
        completed = self.result.disposition == "completed-not-assessed"
        if completed != (self.held_out is not None):
            raise ValueError("execution-run held-out record disagrees with disposition")
        request = self.result.request
        declaration = self.selection.declaration
        if (
            self.result.model_id != self.selection.model_id
            or request.model_id != self.selection.model_id
            or request.declaration != declaration
            or request.problem != declaration.problem.kind
            or self.selection.active_selection.selection_id != request.selection_id
            or self.selection.active_selection.model_id != request.model_id
            or self.selection.factor_set_id != request.factor_set_id
            or self.selection.mapping_run_id != request.mapping_run_id
            or self.selection.initial_parameters != request.initial_parameters
            or self.selection.adapter != request.adapter
            or self.selection.callback_limit != request.callback_limit
            or self.selection.callback_trace_byte_limit
            != request.callback_trace_byte_limit
        ):
            raise ValueError("execution-run selection disagrees with result request")
        if self.held_out is not None and (
            self.held_out.model_id != request.model_id
            or any(row.model_id != request.model_id for row in self.held_out.rows)
            or self.held_out.execution_result_id != self.result.result_id
            or self.held_out.request_id != request.request_id
            or self.held_out.factor_set_id != request.factor_set_id
            or self.held_out.selection_id != request.selection_id
            or self.held_out.mapping_run_id != request.mapping_run_id
        ):
            raise ValueError("execution-run held-out provenance disagrees")
        if self.manifest is not None and (
            self.manifest.declaration != declaration
            or self.manifest.model_id != request.model_id
            or self.manifest.problem != request.problem
            or self.manifest.disposition != self.result.disposition
            or self.manifest.request_id != request.request_id
            or self.manifest.result_id != self.result.result_id
            or self.manifest.selection != self.selection
            or (
                self.held_out is not None
                and (
                    self.manifest.held_out.assessment_id != self.held_out.assessment_id
                    or self.manifest.held_out.sha256
                    != sha256(canonical_json(self.held_out))
                )
            )
        ):
            raise ValueError("execution-run records disagree with manifest")
        return self
