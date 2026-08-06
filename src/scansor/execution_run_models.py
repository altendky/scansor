from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.execution_models import (
    AdapterDescriptor,
    Disposition,
    ExecutionResult,
    HeldOutAssessment,
)
from scansor.factor_models import (
    ActiveFactorSelection,
    ParameterVector,
    Problem,
    Variant,
)
from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

EXECUTION_RUN_FORMAT = "scansor-stepped-rotational-v0-execution-run-manifest-v1"
FORMAT_STATUS = "internal/provisional/synthetic-only/fixed-topology/non-public"


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


class ExecutionRunArtifact(ExecutionRunStrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExecutionRunSelection(ExecutionRunStrictModel):
    active_selection: ActiveFactorSelection
    adapter: AdapterDescriptor
    callback_limit: int = Field(ge=1, le=10_000)
    callback_trace_byte_limit: int = Field(ge=1_024, le=16 * 1024 * 1024)
    execution_selection_id: str = Field(pattern=r"^execution-selection\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    format: Literal["scansor-stepped-rotational-v0-execution-selection-v1"] = (
        "scansor-stepped-rotational-v0-execution-selection-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/fixed-topology/non-public"
    ] = FORMAT_STATUS
    initial_parameters: ParameterVector
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_selection(self) -> ExecutionRunSelection:
        if (
            self.factor_set_id != self.active_selection.factor_set_id
            or self.execution_selection_id
            != run_content_id("execution-selection", self, "execution_selection_id")
        ):
            raise ValueError("execution selection provenance or ID is invalid")
        return self


class InspectionRunReference(ExecutionRunStrictModel):
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class MappingRunReference(ExecutionRunStrictModel):
    format: Literal["scansor-stepped-rotational-v0-mapping-v1"]
    manifest_format: Literal["scansor-stepped-rotational-v0-manifest-v1"]
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
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
    disposition: Disposition
    execution_run_id: str = Field(pattern=r"^execution-run\.[0-9a-f]{64}$")
    factor_contract_id: str = Field(pattern=r"^contract\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    format: Literal["scansor-stepped-rotational-v0-execution-run-manifest-v1"] = (
        EXECUTION_RUN_FORMAT
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/fixed-topology/non-public"
    ] = FORMAT_STATUS
    held_out: HeldOutRunReference
    inspection: InspectionRunReference
    mapping: MappingRunReference
    problem: Problem
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    result_id: str = Field(pattern=r"^execution-result\.[0-9a-f]{64}$")
    selection: ExecutionRunSelection
    variant: Variant

    @field_validator("artifacts")
    @classmethod
    def copy_artifacts(
        cls, value: dict[str, ExecutionRunArtifact]
    ) -> dict[str, ExecutionRunArtifact]:
        return dict(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> ExecutionRunManifest:
        completed = self.disposition == "completed-not-assessed"
        expected_names = {"selection.json", "result.json"}
        if completed:
            expected_names.add("held-out.json")
        if (
            set(self.artifacts) != expected_names
            or completed != (self.held_out.state == "assessed")
            or self.factor_set_id != self.selection.factor_set_id
            or self.mapping.run_id != self.selection.mapping_run_id
            or self.adapter != self.selection.adapter
            or self.problem != self.selection.initial_parameters.problem
            or self.variant != self.selection.initial_parameters.variant
            or self.execution_run_id
            != run_content_id("execution-run", self, "execution_run_id")
        ):
            raise ValueError("execution-run manifest graph or ID is invalid")
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
        if (
            self.selection.active_selection.selection_id != request.selection_id
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
            self.held_out.execution_result_id != self.result.result_id
            or self.held_out.request_id != request.request_id
            or self.held_out.factor_set_id != request.factor_set_id
            or self.held_out.selection_id != request.selection_id
            or self.held_out.mapping_run_id != request.mapping_run_id
        ):
            raise ValueError("execution-run held-out provenance disagrees")
        if self.manifest is not None and (
            self.manifest.disposition != self.result.disposition
            or self.manifest.request_id != self.result.request.request_id
            or self.manifest.result_id != self.result.result_id
            or self.manifest.selection != self.selection
        ):
            raise ValueError("execution-run records disagree with manifest")
        return self
