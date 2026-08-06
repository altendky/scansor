from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.factor_models import FactorEvaluation, ParameterVector, Problem, Variant
from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

MAX_CALLBACK_ATTEMPTS = 10_000
MAX_CALLBACK_TRACE_BYTES = 16 * 1024 * 1024

ExecutionFailureCode = Literal[
    "preflight-ineligible",
    "adapter-raised",
    "callback-limit-exceeded",
    "callback-reentrant",
    "callback-dimension-invalid",
    "callback-nonfinite",
    "callback-out-of-bounds",
    "callback-structure-invalid",
    "callback-evaluation-undefined",
    "callback-incomplete",
    "callback-trace-budget-exceeded",
    "response-schema-invalid",
    "response-content-id-invalid",
    "response-provenance-invalid",
    "response-vector-invalid",
    "response-structure-invalid",
    "final-evaluation-undefined",
]
EXECUTION_FAILURE_ORDER: tuple[ExecutionFailureCode, ...] = (
    "preflight-ineligible",
    "adapter-raised",
    "callback-limit-exceeded",
    "callback-reentrant",
    "callback-dimension-invalid",
    "callback-nonfinite",
    "callback-out-of-bounds",
    "callback-structure-invalid",
    "callback-evaluation-undefined",
    "callback-incomplete",
    "callback-trace-budget-exceeded",
    "response-schema-invalid",
    "response-content-id-invalid",
    "response-provenance-invalid",
    "response-vector-invalid",
    "response-structure-invalid",
    "final-evaluation-undefined",
)
CallbackFailureCode = Literal[
    "callback-limit-exceeded",
    "callback-reentrant",
    "callback-dimension-invalid",
    "callback-nonfinite",
    "callback-out-of-bounds",
    "callback-structure-invalid",
    "callback-evaluation-undefined",
    "callback-incomplete",
    "callback-trace-budget-exceeded",
]
TerminationCategory = Literal[
    "not-invoked",
    "backend-converged",
    "backend-limit-reached",
    "backend-stopped",
    "backend-reported-failure",
    "backend-unknown",
    "adapter-raised",
    "callback-rejected",
    "invalid-response",
]
Disposition = Literal[
    "ineligible",
    "execution-failed",
    "invalid-backend-output",
    "completed-not-assessed",
]


def execution_content_id(prefix: str, value: StrictModel, field: str) -> str:
    semantic = value.model_dump(mode="json", exclude={field})
    return f"{prefix}.{sha256(canonical_json(semantic))}"


class ExecutionStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class AdapterDescriptor(ExecutionStrictModel):
    adapter_id: str = Field(pattern=r"^adapter\.[0-9a-f]{64}$")
    implementation: str = Field(min_length=1, max_length=128, pattern=r"^[ -~]+$")
    protocol_revision: Literal["stepped-rotational-backend-adapter-v1"] = (
        "stepped-rotational-backend-adapter-v1"
    )
    revision: str = Field(min_length=1, max_length=64, pattern=r"^[ -~]+$")

    @model_validator(mode="after")
    def validate_id(self) -> AdapterDescriptor:
        if self.adapter_id != execution_content_id("adapter", self, "adapter_id"):
            raise ValueError("adapter descriptor ID does not match semantic content")
        return self


class ExecutionRequest(ExecutionStrictModel):
    active_factor_count: int = Field(ge=0)
    adapter: AdapterDescriptor
    callback_limit: int = Field(
        default=MAX_CALLBACK_ATTEMPTS, ge=1, le=MAX_CALLBACK_ATTEMPTS
    )
    callback_protocol_revision: Literal["stepped-rotational-callback-v2"] = (
        "stepped-rotational-callback-v2"
    )
    callback_trace_byte_limit: int = Field(
        default=MAX_CALLBACK_TRACE_BYTES,
        ge=1_024,
        le=MAX_CALLBACK_TRACE_BYTES,
    )
    contract_id: str = Field(pattern=r"^contract\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    initial_parameters: ParameterVector
    lower_bounds: tuple[float, ...]
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameter_order: tuple[str, ...]
    parameter_scales: tuple[float, ...]
    problem: Problem
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    upper_bounds: tuple[float, ...]
    variant: Variant

    @field_validator(
        "lower_bounds",
        "parameter_order",
        "parameter_scales",
        "upper_bounds",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_request(self) -> ExecutionRequest:
        dimension = len(self.initial_parameters.values)
        if (
            self.initial_parameters.problem != self.problem
            or self.initial_parameters.variant != self.variant
            or len(self.parameter_order) != dimension
            or len(self.lower_bounds) != dimension
            or len(self.upper_bounds) != dimension
            or len(self.parameter_scales) != dimension
            or any(
                low > high
                for low, high in zip(self.lower_bounds, self.upper_bounds, strict=True)
            )
            or any(scale <= 0.0 for scale in self.parameter_scales)
        ):
            raise ValueError(
                "execution request dimensions or parameter provenance disagree"
            )
        if self.request_id != execution_content_id(
            "execution-request", self, "request_id"
        ):
            raise ValueError("execution request ID does not match semantic content")
        return self


class AdapterInvocation(ExecutionStrictModel):
    adapter_id: str = Field(pattern=r"^adapter\.[0-9a-f]{64}$")
    callback_limit: int = Field(ge=1, le=MAX_CALLBACK_ATTEMPTS)
    callback_protocol_revision: Literal["stepped-rotational-callback-v2"]
    callback_trace_byte_limit: int = Field(ge=1_024, le=MAX_CALLBACK_TRACE_BYTES)
    contract_id: str = Field(pattern=r"^contract\.[0-9a-f]{64}$")
    factor_count: int = Field(ge=1)
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    initial_values: tuple[float, ...]
    invocation_id: str = Field(pattern=r"^invocation\.[0-9a-f]{64}$")
    lower_bounds: tuple[float, ...]
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    parameter_dimension: int = Field(ge=1, le=64)
    parameter_order: tuple[str, ...]
    parameter_scales: tuple[float, ...]
    problem: Problem
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    residual_dimension: int = Field(ge=1)
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    upper_bounds: tuple[float, ...]
    variant: Variant

    @field_validator(
        "initial_values",
        "lower_bounds",
        "parameter_order",
        "parameter_scales",
        "upper_bounds",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_invocation(self) -> AdapterInvocation:
        dimension = self.parameter_dimension
        if (
            any(
                len(value) != dimension
                for value in (
                    self.initial_values,
                    self.lower_bounds,
                    self.parameter_order,
                    self.parameter_scales,
                    self.upper_bounds,
                )
            )
            or self.factor_count != self.residual_dimension
        ):
            raise ValueError("adapter invocation dimensions disagree")
        if self.invocation_id != execution_content_id(
            "invocation", self, "invocation_id"
        ):
            raise ValueError("adapter invocation ID does not match semantic content")
        return self


class CallbackTraceEntry(ExecutionStrictModel):
    callback_entry_id: str = Field(pattern=r"^callback-entry\.[0-9a-f]{64}$")
    evaluation: FactorEvaluation | None
    failure_code: CallbackFailureCode | None
    nonfinite_positions: tuple[int, ...] = ()
    sequence_id: int = Field(ge=0)
    status: Literal["successful", "rejected"]
    supplied_dimension: int | None = Field(default=None, ge=0)
    values: tuple[float, ...]
    wrapper_state: Literal["idle", "callback-active"] = "idle"

    @field_validator("nonfinite_positions", "values", mode="before")
    @classmethod
    def restore_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_entry(self) -> CallbackTraceEntry:
        if self.status == "successful":
            if self.evaluation is None or self.failure_code is not None:
                raise ValueError("successful callback trace entry is incomplete")
        elif self.evaluation is not None or self.failure_code is None:
            raise ValueError("rejected callback trace entry is incomplete")
        if (
            self.wrapper_state == "callback-active"
            and self.failure_code != "callback-reentrant"
        ):
            raise ValueError(
                "callback-active state is reserved for reentrancy rejection"
            )
        if self.failure_code == "callback-reentrant" and (
            self.wrapper_state != "callback-active" or self.values
        ):
            raise ValueError("reentrant callback evidence is not canonical")
        if self.failure_code == "callback-dimension-invalid" and self.values:
            raise ValueError("dimension-invalid callback evidence is not canonical")
        if self.failure_code != "callback-reentrant" and self.wrapper_state != "idle":
            raise ValueError("non-reentrant callback has active wrapper state")
        if self.failure_code == "callback-nonfinite":
            if (
                not self.nonfinite_positions
                or self.supplied_dimension is None
                or self.values
                or self.nonfinite_positions
                != tuple(sorted(set(self.nonfinite_positions)))
                or any(position < 0 for position in self.nonfinite_positions)
            ):
                raise ValueError("nonfinite callback trace lacks finite-safe positions")
        elif self.nonfinite_positions:
            raise ValueError("nonfinite positions require nonfinite callback rejection")
        if (
            self.supplied_dimension is not None
            and self.nonfinite_positions
            and any(
                position >= self.supplied_dimension
                for position in self.nonfinite_positions
            )
        ):
            raise ValueError(
                "nonfinite callback position is outside supplied dimension"
            )
        if self.evaluation is not None and (
            self.evaluation.parameters.values != self.values
            or self.supplied_dimension != len(self.values)
        ):
            raise ValueError("successful callback values disagree with evaluation")
        if self.callback_entry_id != execution_content_id(
            "callback-entry", self, "callback_entry_id"
        ):
            raise ValueError("callback trace entry ID does not match semantic content")
        return self


class BackendResponse(ExecutionStrictModel):
    adapter_id: str = Field(pattern=r"^adapter\.[0-9a-f]{64}$")
    final_values: tuple[float, ...]
    invocation_id: str = Field(pattern=r"^invocation\.[0-9a-f]{64}$")
    raw_code: str | None = Field(default=None, max_length=64, pattern=r"^[ -~]*$")
    raw_message: str | None = Field(default=None, max_length=256, pattern=r"^[ -~]*$")
    reported_termination: Literal["converged", "limit", "stopped", "failure", "unknown"]
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    response_id: str = Field(pattern=r"^backend-response\.[0-9a-f]{64}$")

    @field_validator("final_values", mode="before")
    @classmethod
    def restore_values(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_id(self) -> BackendResponse:
        if not all(math.isfinite(value) for value in self.final_values):
            raise ValueError("backend response final values must be finite")
        if self.response_id != execution_content_id(
            "backend-response", self, "response_id"
        ):
            raise ValueError("backend response ID does not match semantic content")
        return self


class NormalizedTermination(ExecutionStrictModel):
    category: TerminationCategory
    raw_code: str | None = Field(default=None, max_length=64, pattern=r"^[ -~]*$")
    raw_message: str | None = Field(default=None, max_length=256, pattern=r"^[ -~]*$")


class BoundActivity(ExecutionStrictModel):
    activity: tuple[Literal["lower", "interior", "upper"], ...]

    @field_validator("activity", mode="before")
    @classmethod
    def restore_activity(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class ExternalFailureEvidence(ExecutionStrictModel):
    evidence_id: str = Field(pattern=r"^external-failure\.[0-9a-f]{64}$")
    kind: Literal[
        "adapter-raised",
        "response-schema-invalid",
        "response-content-id-invalid",
        "response-provenance-invalid",
        "response-vector-invalid",
    ]
    observed_final_dimension: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_evidence(self) -> ExternalFailureEvidence:
        if (self.kind == "response-vector-invalid") != (
            self.observed_final_dimension is not None
        ):
            raise ValueError("external failure dimension is not canonical")
        if self.evidence_id != execution_content_id(
            "external-failure", self, "evidence_id"
        ):
            raise ValueError("external failure evidence ID does not match content")
        return self


ConsistencyCode = Literal[
    "callback-reentrant",
    "callback-nonfinite",
    "callback-evaluation-undefined",
    "callback-incomplete",
    "callback-limit-exceeded",
    "callback-trace-budget-exceeded",
]


class CallbackConsistencyObservation(ExecutionStrictModel):
    code: ConsistencyCode
    related_sequence_id: int | None = Field(default=None, ge=0, le=10_000)
    sequence_id: int = Field(ge=0, le=10_000)


class CallbackConsistencyEvidence(ExecutionStrictModel):
    evidence_id: str = Field(pattern=r"^callback-consistency\.[0-9a-f]{64}$")
    observations: tuple[CallbackConsistencyObservation, ...] = Field(min_length=1)
    retained_trace_byte_count: int = Field(ge=0, le=MAX_CALLBACK_TRACE_BYTES)
    trace_byte_limit: int = Field(ge=1_024, le=MAX_CALLBACK_TRACE_BYTES)

    @field_validator("observations", mode="before")
    @classmethod
    def restore_observations(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_evidence(self) -> CallbackConsistencyEvidence:
        order = [
            (item.sequence_id, item.code, item.related_sequence_id)
            for item in self.observations
        ]
        if order != sorted(
            set(order), key=lambda item: (item[0], item[1], item[2] or -1)
        ):
            raise ValueError("callback consistency observations are not canonical")
        if len({item.sequence_id for item in self.observations}) != len(
            self.observations
        ):
            raise ValueError("callback consistency sequence has multiple outcomes")
        observations = {(item.sequence_id, item.code) for item in self.observations}
        evaluation_causes = [
            item.related_sequence_id
            for item in self.observations
            if item.code == "callback-evaluation-undefined"
        ]
        if len(evaluation_causes) != len(set(evaluation_causes)):
            raise ValueError("reentrant cause is assigned to multiple evaluations")
        for item in self.observations:
            if item.code == "callback-evaluation-undefined":
                if (
                    item.related_sequence_id is None
                    or (item.related_sequence_id, "callback-reentrant")
                    not in observations
                ):
                    raise ValueError(
                        "evaluation consistency evidence lacks a reentrant cause"
                    )
            elif item.related_sequence_id is not None:
                raise ValueError("callback consistency relation is not canonical")
        if self.retained_trace_byte_count > self.trace_byte_limit:
            raise ValueError("retained callback trace exceeds its byte limit")
        if self.evidence_id != execution_content_id(
            "callback-consistency", self, "evidence_id"
        ):
            raise ValueError("callback consistency evidence ID does not match content")
        return self


class ExecutionResult(ExecutionStrictModel):
    adapter_id: str = Field(pattern=r"^adapter\.[0-9a-f]{64}$")
    bound_activity: BoundActivity | None
    callback_count: int = Field(ge=0, le=MAX_CALLBACK_ATTEMPTS)
    callback_consistency_evidence: CallbackConsistencyEvidence | None
    callback_trace: tuple[CallbackTraceEntry, ...]
    callback_trace_byte_count: int = Field(ge=0, le=MAX_CALLBACK_TRACE_BYTES)
    disposition: Disposition
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    failures: tuple[ExecutionFailureCode, ...]
    external_failure_evidence: ExternalFailureEvidence | None
    final_evaluation: FactorEvaluation | None
    final_objective: float | None = Field(default=None, ge=0.0)
    final_parameters: ParameterVector | None
    format: Literal["scansor-stepped-rotational-v0-execution-result-v1"] = (
        "scansor-stepped-rotational-v0-execution-result-v1"
    )
    format_status: Literal["internal/provisional/synthetic-only/non-public"] = (
        "internal/provisional/synthetic-only/non-public"
    )
    initial_evaluation: FactorEvaluation | None
    invocation: AdapterInvocation | None
    normalized_termination: NormalizedTermination
    preflight_id: str = Field(pattern=r"^preflight\.[0-9a-f]{64}$")
    raw_response: BackendResponse | None
    request: ExecutionRequest
    result_id: str = Field(pattern=r"^execution-result\.[0-9a-f]{64}$")
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")

    @field_validator("callback_trace", "failures", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_result(self) -> ExecutionResult:
        expected_failures = tuple(
            code for code in EXECUTION_FAILURE_ORDER if code in self.failures
        )
        if self.failures != expected_failures:
            raise ValueError("execution failures are duplicated or out of order")
        if self.callback_count != len(self.callback_trace) or tuple(
            item.sequence_id for item in self.callback_trace
        ) != tuple(range(len(self.callback_trace))):
            raise ValueError("callback trace count or sequence is invalid")
        expected_trace_bytes = sum(
            len(canonical_json(item)) for item in self.callback_trace
        )
        if (
            self.callback_trace_byte_count != expected_trace_bytes
            or self.callback_trace_byte_count > self.request.callback_trace_byte_limit
        ):
            raise ValueError("callback trace byte accounting is invalid")
        if (
            self.adapter_id != self.request.adapter.adapter_id
            or self.factor_set_id != self.request.factor_set_id
            or self.selection_id != self.request.selection_id
        ):
            raise ValueError("execution result provenance disagrees with request")
        if self.raw_response is not None and (
            self.invocation is None
            or (
                self.raw_response.adapter_id != self.adapter_id
                or self.raw_response.request_id != self.request.request_id
                or self.raw_response.invocation_id != self.invocation.invocation_id
            )
        ):
            raise ValueError("validated backend response provenance disagrees")
        if self.invocation is not None:
            request = self.request
            invocation = self.invocation
            if (
                invocation.adapter_id != request.adapter.adapter_id
                or invocation.factor_count != request.active_factor_count
                or invocation.residual_dimension != request.active_factor_count
                or invocation.callback_limit != request.callback_limit
                or invocation.callback_protocol_revision
                != request.callback_protocol_revision
                or invocation.callback_trace_byte_limit
                != request.callback_trace_byte_limit
                or invocation.contract_id != request.contract_id
                or invocation.factor_set_id != request.factor_set_id
                or invocation.initial_values != request.initial_parameters.values
                or invocation.lower_bounds != request.lower_bounds
                or invocation.mapping_run_id != request.mapping_run_id
                or invocation.parameter_dimension != len(request.parameter_order)
                or invocation.parameter_order != request.parameter_order
                or invocation.parameter_scales != request.parameter_scales
                or invocation.problem != request.problem
                or invocation.request_id != request.request_id
                or invocation.selection_id != request.selection_id
                or invocation.upper_bounds != request.upper_bounds
                or invocation.variant != request.variant
            ):
                raise ValueError("embedded invocation disagrees with execution request")
        final_fields = (
            self.final_parameters,
            self.final_evaluation,
            self.final_objective,
            self.bound_activity,
        )
        if self.disposition == "completed-not-assessed":
            if (
                any(value is None for value in final_fields)
                or self.raw_response is None
            ):
                raise ValueError("completed result lacks final application evidence")
            if any(entry.status == "rejected" for entry in self.callback_trace):
                raise ValueError("completed result contains a rejected callback")
        elif any(value is not None for value in final_fields):
            raise ValueError("non-completed result contains final application evidence")
        if self.callback_count > self.request.callback_limit:
            raise ValueError("callback trace exceeds the request callback limit")
        for entry in self.callback_trace:
            evaluation = entry.evaluation
            if (
                entry.failure_code == "callback-nonfinite"
                and entry.supplied_dimension != len(self.request.parameter_order)
            ):
                raise ValueError("nonfinite callback dimension disagrees with request")
            if entry.failure_code == "callback-evaluation-undefined" and (
                entry.supplied_dimension != len(self.request.parameter_order)
                or len(entry.values) != len(self.request.parameter_order)
                or not all(math.isfinite(value) for value in entry.values)
                or any(
                    value < low or value > high
                    for value, low, high in zip(
                        entry.values,
                        self.request.lower_bounds,
                        self.request.upper_bounds,
                        strict=True,
                    )
                )
            ):
                raise ValueError("evaluation-undefined callback input is impossible")
            if evaluation is None:
                continue
            mismatch = (
                evaluation.factor_set_id != self.request.factor_set_id
                or evaluation.selection_id != self.request.selection_id
                or evaluation.parameters.problem != self.request.problem
                or evaluation.parameters.variant != self.request.variant
                or evaluation.parameter_order != self.request.parameter_order
            )
            if self.initial_evaluation is not None:
                mismatch = mismatch or (
                    evaluation.active_factor_ids
                    != self.initial_evaluation.active_factor_ids
                )
            if mismatch:
                raise ValueError("callback evaluation disagrees with execution request")
        prohibited_trace_codes = {
            "callback-limit-exceeded",
            "callback-incomplete",
            "callback-trace-budget-exceeded",
        }
        if any(
            entry.failure_code in prohibited_trace_codes
            for entry in self.callback_trace
        ):
            raise ValueError("callback trace contains an evidence-only failure")
        consistency_trace_codes = {
            (entry.sequence_id, entry.failure_code)
            for entry in self.callback_trace
            if entry.failure_code
            in {
                "callback-reentrant",
                "callback-nonfinite",
            }
        }
        evidence_observations = (
            {
                (item.sequence_id, item.code)
                for item in self.callback_consistency_evidence.observations
            }
            if self.callback_consistency_evidence is not None
            else set()
        )
        if not consistency_trace_codes.issubset(evidence_observations):
            raise ValueError("callback consistency evidence omits trace observations")
        trace_by_sequence = {
            entry.sequence_id: entry.failure_code for entry in self.callback_trace
        }
        incomplete_sequences = {
            sequence
            for sequence, code in evidence_observations
            if code == "callback-incomplete"
        }
        terminal_sequences = {
            sequence
            for sequence, code in evidence_observations
            if code
            in {
                "callback-incomplete",
                "callback-trace-budget-exceeded",
            }
        }
        if terminal_sequences and min(terminal_sequences) != self.callback_count:
            raise ValueError("callback consistency evidence has a reservation gap")
        for sequence, code in evidence_observations:
            if code == "callback-limit-exceeded":
                valid_observation = sequence == self.request.callback_limit
            elif code in {
                "callback-trace-budget-exceeded",
                "callback-incomplete",
            }:
                valid_observation = (
                    self.callback_count <= sequence < self.request.callback_limit
                )
            else:
                valid_observation = trace_by_sequence.get(sequence) == code or (
                    self.callback_count <= sequence < self.request.callback_limit
                    and any(
                        incomplete <= sequence for incomplete in incomplete_sequences
                    )
                )
            if not valid_observation:
                raise ValueError("callback consistency observation is impossible")
        if (
            (self.request.callback_limit, "callback-limit-exceeded")
            in evidence_observations
            and self.callback_count < self.request.callback_limit
            and not terminal_sequences
        ):
            raise ValueError("callback limit evidence omits its reservation prefix")
        consistency_failure_codes = {code for _, code in evidence_observations}
        if not consistency_failure_codes.issubset(set(self.failures)):
            raise ValueError("callback consistency failures are absent from result")
        if self.callback_consistency_evidence is not None and (
            self.callback_consistency_evidence.retained_trace_byte_count
            != self.callback_trace_byte_count
            or self.callback_consistency_evidence.trace_byte_limit
            != self.request.callback_trace_byte_limit
        ):
            raise ValueError("callback consistency evidence accounting disagrees")
        if self.external_failure_evidence is not None:
            evidence_kind = self.external_failure_evidence.kind
            if self.failures != (evidence_kind,):
                raise ValueError("external failure evidence disagrees with failures")
        if self.result_id != execution_content_id(
            "execution-result", self, "result_id"
        ):
            raise ValueError("execution result ID does not match semantic content")
        return self


HeldOutOutcome = Literal[
    "assigned", "ambiguous", "transition", "gap", "outlier", "evaluation-error"
]


class HeldOutSupportCandidate(ExecutionStrictModel):
    absolute_distance_m: float = Field(ge=0.0)
    element_id: str
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]
    signed_distance_m: float


class HeldOutRowAssessment(ExecutionStrictModel):
    assigned_element_id: str | None
    candidates: tuple[HeldOutSupportCandidate, ...]
    geometric_clearance_m: float | None = Field(default=None, ge=0.0)
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    outcome: HeldOutOutcome
    raw_residual_m: float | None
    row_assessment_id: str = Field(pattern=r"^held-out-row\.[0-9a-f]{64}$")
    row_index: int = Field(ge=0)

    @field_validator("candidates", mode="before")
    @classmethod
    def restore_candidates(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_row(self) -> HeldOutRowAssessment:
        if self.outcome == "assigned":
            if self.assigned_element_id is None or self.raw_residual_m is None:
                raise ValueError("assigned held-out row lacks evaluation")
        elif self.outcome == "evaluation-error":
            if self.assigned_element_id is None or self.raw_residual_m is not None:
                raise ValueError("held-out evaluation error fields disagree")
        elif self.assigned_element_id is not None or self.raw_residual_m is not None:
            raise ValueError("excluded held-out row contains evaluation")
        if self.row_assessment_id != execution_content_id(
            "held-out-row", self, "row_assessment_id"
        ):
            raise ValueError("held-out row ID does not match semantic content")
        return self


class HeldOutSummary(ExecutionStrictModel):
    count: int = Field(ge=0)
    maximum_raw_residual_m: float | None
    mean_raw_residual_m: float | None
    minimum_raw_residual_m: float | None
    root_mean_square_raw_residual_m: float | None = Field(default=None, ge=0.0)


class HeldOutAssessment(ExecutionStrictModel):
    assessment_id: str = Field(pattern=r"^held-out-assessment\.[0-9a-f]{64}$")
    counts: dict[HeldOutOutcome, int]
    execution_result_id: str = Field(pattern=r"^execution-result\.[0-9a-f]{64}$")
    factor_set_id: str = Field(pattern=r"^factor-set\.[0-9a-f]{64}$")
    format: Literal["scansor-stepped-rotational-v0-held-out-assessment-v1"] = (
        "scansor-stepped-rotational-v0-held-out-assessment-v1"
    )
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(pattern=r"^execution-request\.[0-9a-f]{64}$")
    rows: tuple[HeldOutRowAssessment, ...]
    selection_id: str = Field(pattern=r"^selection\.[0-9a-f]{64}$")
    summary: HeldOutSummary

    @field_validator("rows", mode="before")
    @classmethod
    def restore_rows(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_assessment(self) -> HeldOutAssessment:
        outcomes: tuple[HeldOutOutcome, ...] = (
            "assigned",
            "ambiguous",
            "transition",
            "gap",
            "outlier",
            "evaluation-error",
        )
        expected = {
            outcome: sum(row.outcome == outcome for row in self.rows)
            for outcome in outcomes
        }
        if self.counts != expected:
            raise ValueError("held-out assessment counts disagree with rows")
        residuals = tuple(
            row.raw_residual_m
            for row in self.rows
            if row.outcome == "assigned" and row.raw_residual_m is not None
        )
        expected_summary = HeldOutSummary(
            count=len(residuals),
            maximum_raw_residual_m=max(residuals) if residuals else None,
            mean_raw_residual_m=(
                math.fsum(residuals) / len(residuals) if residuals else None
            ),
            minimum_raw_residual_m=min(residuals) if residuals else None,
            root_mean_square_raw_residual_m=(
                math.sqrt(
                    math.fsum(value * value for value in residuals) / len(residuals)
                )
                if residuals
                else None
            ),
        )
        if self.summary != expected_summary:
            raise ValueError("held-out summary disagrees with assigned residuals")
        if self.assessment_id != execution_content_id(
            "held-out-assessment", self, "assessment_id"
        ):
            raise ValueError("held-out assessment ID does not match semantic content")
        return self
