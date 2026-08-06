from __future__ import annotations

import math
import threading
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ValidationError

from scansor.errors import ScansorError
from scansor.execution_models import (
    EXECUTION_FAILURE_ORDER,
    AdapterDescriptor,
    AdapterInvocation,
    BackendResponse,
    BoundActivity,
    CallbackConsistencyEvidence,
    CallbackConsistencyObservation,
    CallbackFailureCode,
    CallbackTraceEntry,
    ExecutionFailureCode,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStrictModel,
    ExternalFailureEvidence,
    HeldOutAssessment,
    HeldOutOutcome,
    HeldOutRowAssessment,
    HeldOutSummary,
    HeldOutSupportCandidate,
    NormalizedTermination,
    execution_content_id,
)
from scansor.factor_models import (
    ASYMMETRIC_SHAPE_PARAMETERS,
    AXISYMMETRIC_SHAPE_PARAMETERS,
    POSE_PARAMETERS,
    ActiveFactorSelection,
    FactorEvaluation,
    InstantiatedFactorSet,
    ParameterVector,
    PreflightDiagnostics,
)
from scansor.mapping_models import MappingResult
from scansor.serialization import canonical_json, parse_canonical_json
from scansor.stepped_rotational import assess_nominal_support
from scansor.stepped_rotational_factors import (
    evaluate_factors,
    evaluate_support_residual,
    instantiate_factors,
    parameter_domain_failures,
    preflight_factors,
    select_active_factors,
)

MAX_EXECUTION_RESULT_BYTES = 64 * 1024 * 1024


class ResidualJacobianCallback(Protocol):
    def __call__(self, values: object) -> FactorEvaluation: ...


class BackendAdapter(Protocol):
    descriptor: AdapterDescriptor

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object: ...


class CallbackRejected(ScansorError):
    code: CallbackFailureCode

    def __init__(self, code: CallbackFailureCode) -> None:
        super().__init__(code)
        self.code = code


def _revalidated[Model: BaseModel](
    model_type: type[Model], value: Model, label: str
) -> Model:
    try:
        return model_type.model_validate(value.model_dump(mode="python"))
    except Exception as error:
        raise ScansorError(f"invalid {label}: {error}") from error


def _identified[Model: ExecutionStrictModel](
    model_type: type[Model], prefix: str, field: str, values: dict[str, object]
) -> Model:
    provisional = model_type.model_construct(**cast(dict[str, Any], values))
    values[field] = execution_content_id(prefix, provisional, field)
    return model_type(**values)


def adapter_descriptor(implementation: str, revision: str) -> AdapterDescriptor:
    return _identified(
        AdapterDescriptor,
        "adapter",
        "adapter_id",
        {"implementation": implementation, "revision": revision},
    )


def _effective_parameters(
    factor_set: InstantiatedFactorSet, initial: ParameterVector
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
    contract = factor_set.contract
    if initial.problem == "fixed-pose-shape":
        size = len(initial.values)
        order = (
            ASYMMETRIC_SHAPE_PARAMETERS
            if factor_set.variant == "asymmetric-datum-flat"
            else AXISYMMETRIC_SHAPE_PARAMETERS
        )
        return (
            order,
            contract.shape_lower_m[:size],
            contract.shape_upper_m[:size],
            contract.shape_scales_m[:size],
        )
    return (
        POSE_PARAMETERS,
        contract.pose_lower_m_rad,
        contract.pose_upper_m_rad,
        contract.pose_scales_m_rad,
    )


def create_execution_request(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    initial_parameters: ParameterVector,
    adapter: AdapterDescriptor,
    *,
    callback_limit: int = 10_000,
    callback_trace_byte_limit: int = 16 * 1024 * 1024,
) -> ExecutionRequest:
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    selection = _revalidated(
        ActiveFactorSelection, selection, "active-factor selection"
    )
    initial_parameters = _revalidated(
        ParameterVector, initial_parameters, "initial parameter vector"
    )
    adapter = _revalidated(AdapterDescriptor, adapter, "adapter descriptor")
    if selection.factor_set_id != factor_set.factor_set_id:
        raise ScansorError("active-factor selection belongs to another factor set")
    if select_active_factors(factor_set, selection.active_factor_ids) != selection:
        raise ScansorError("active-factor selection does not match the factor graph")
    if initial_parameters.variant != factor_set.variant:
        raise ScansorError("initial parameter and factor-set variants differ")
    order, lower, upper, scales = _effective_parameters(factor_set, initial_parameters)
    return _identified(
        ExecutionRequest,
        "execution-request",
        "request_id",
        {
            "active_factor_count": len(selection.active_factor_ids),
            "adapter": adapter,
            "callback_limit": callback_limit,
            "callback_trace_byte_limit": callback_trace_byte_limit,
            "contract_id": factor_set.contract.contract_id,
            "factor_set_id": factor_set.factor_set_id,
            "initial_parameters": initial_parameters,
            "lower_bounds": lower,
            "mapping_run_id": factor_set.mapping_run_id,
            "parameter_order": order,
            "parameter_scales": scales,
            "problem": initial_parameters.problem,
            "selection_id": selection.selection_id,
            "upper_bounds": upper,
            "variant": factor_set.variant,
        },
    )


def backend_response(
    invocation: AdapterInvocation,
    final_values: tuple[float, ...],
    reported_termination: Literal[
        "converged", "limit", "stopped", "failure", "unknown"
    ],
    *,
    raw_code: str | None = None,
    raw_message: str | None = None,
) -> BackendResponse:
    invocation = _revalidated(AdapterInvocation, invocation, "adapter invocation")
    return _identified(
        BackendResponse,
        "backend-response",
        "response_id",
        {
            "adapter_id": invocation.adapter_id,
            "final_values": final_values,
            "invocation_id": invocation.invocation_id,
            "raw_code": raw_code,
            "raw_message": raw_message,
            "reported_termination": reported_termination,
            "request_id": invocation.request_id,
        },
    )


def _validate_execution_graph(
    request: ExecutionRequest,
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
) -> tuple[ExecutionRequest, InstantiatedFactorSet, ActiveFactorSelection]:
    request = _revalidated(ExecutionRequest, request, "execution request")
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    selection = _revalidated(
        ActiveFactorSelection, selection, "active-factor selection"
    )
    expected = create_execution_request(
        factor_set,
        selection,
        request.initial_parameters,
        request.adapter,
        callback_limit=request.callback_limit,
        callback_trace_byte_limit=request.callback_trace_byte_limit,
    )
    if expected != request:
        raise ScansorError(
            "execution request does not match the revalidated factor graph"
        )
    return request, factor_set, selection


def _invocation(request: ExecutionRequest) -> AdapterInvocation:
    return _identified(
        AdapterInvocation,
        "invocation",
        "invocation_id",
        {
            "adapter_id": request.adapter.adapter_id,
            "callback_limit": request.callback_limit,
            "callback_protocol_revision": request.callback_protocol_revision,
            "callback_trace_byte_limit": request.callback_trace_byte_limit,
            "contract_id": request.contract_id,
            "factor_count": request.active_factor_count,
            "factor_set_id": request.factor_set_id,
            "initial_values": request.initial_parameters.values,
            "lower_bounds": request.lower_bounds,
            "mapping_run_id": request.mapping_run_id,
            "parameter_dimension": len(request.parameter_order),
            "parameter_order": request.parameter_order,
            "parameter_scales": request.parameter_scales,
            "problem": request.problem,
            "request_id": request.request_id,
            "residual_dimension": request.active_factor_count,
            "selection_id": request.selection_id,
            "upper_bounds": request.upper_bounds,
            "variant": request.variant,
        },
    )


def _trace_entry(
    sequence_id: int,
    values: tuple[float, ...],
    *,
    evaluation: FactorEvaluation | None = None,
    failure: CallbackFailureCode | None = None,
    nonfinite_positions: tuple[int, ...] = (),
    supplied_dimension: int | None = None,
    wrapper_state: Literal["idle", "callback-active"] = "idle",
) -> CallbackTraceEntry:
    return _identified(
        CallbackTraceEntry,
        "callback-entry",
        "callback_entry_id",
        {
            "evaluation": evaluation,
            "failure_code": failure,
            "nonfinite_positions": nonfinite_positions,
            "sequence_id": sequence_id,
            "status": "successful" if failure is None else "rejected",
            "supplied_dimension": supplied_dimension,
            "values": values,
            "wrapper_state": wrapper_state,
        },
    )


class _GuardedCallback:
    def __init__(
        self,
        request: ExecutionRequest,
        factor_set: InstantiatedFactorSet,
        selection: ActiveFactorSelection,
    ) -> None:
        self.request: ExecutionRequest = request
        self.factor_set: InstantiatedFactorSet = factor_set
        self.selection: ActiveFactorSelection = selection
        self._entries: dict[int, CallbackTraceEntry] = {}
        self._pending: set[int] = set()
        self._observed_failures: set[ExecutionFailureCode] = set()
        self._consistency_observations: set[tuple[int, str, int | None]] = set()
        self._interrupted_sequences: dict[int, int] = {}
        self._trace_byte_count: int = 0
        self._sealed_trace: tuple[CallbackTraceEntry, ...] | None = None
        self.gate: Any = threading.Lock()
        self.state_gate: Any = threading.Lock()
        self.next_sequence: int = 0
        self.closed: bool = False
        self.active_sequence: int | None = None
        self.active_thread_id: int | None = None

    @property
    def trace(self) -> tuple[CallbackTraceEntry, ...]:
        with self.state_gate:
            if self._sealed_trace is not None:
                return self._sealed_trace
            return tuple(self._entries[index] for index in sorted(self._entries))

    @property
    def trace_byte_count(self) -> int:
        with self.state_gate:
            return self._trace_byte_count

    @property
    def failure_codes(self) -> tuple[ExecutionFailureCode, ...]:
        with self.state_gate:
            return _ordered_failures(list(self._observed_failures))

    @property
    def consistency_evidence(self) -> CallbackConsistencyEvidence | None:
        with self.state_gate:
            if not self._consistency_observations:
                return None
            observations = tuple(
                CallbackConsistencyObservation(
                    code=cast(Any, code),
                    related_sequence_id=related_sequence,
                    sequence_id=sequence,
                )
                for sequence, code, related_sequence in sorted(
                    self._consistency_observations,
                    key=lambda item: (item[0], item[1], item[2] or -1),
                )
            )
            values: dict[str, object] = {
                "observations": observations,
                "retained_trace_byte_count": self._trace_byte_count,
                "trace_byte_limit": self.request.callback_trace_byte_limit,
            }
            return _identified(
                CallbackConsistencyEvidence,
                "callback-consistency",
                "evidence_id",
                values,
            )

    def close(self) -> int:
        with self.state_gate:
            if self._sealed_trace is not None:
                return 0
            self.closed = True
            missing = tuple(sorted(self._pending))
            for sequence in missing:
                self._consistency_observations.add(
                    (sequence, "callback-incomplete", None)
                )
            if missing:
                self._observed_failures.add("callback-incomplete")
            terminal_sequences = tuple(
                sequence
                for sequence, code, _related in self._consistency_observations
                if code
                in {
                    "callback-incomplete",
                    "callback-trace-budget-exceeded",
                }
            )
            if terminal_sequences:
                first_unretained = min(terminal_sequences)
                retained = tuple(
                    self._entries[index]
                    for index in sorted(self._entries)
                    if index < first_unretained
                )
                self._entries = {entry.sequence_id: entry for entry in retained}
                self._trace_byte_count = sum(
                    len(canonical_json(entry)) for entry in retained
                )
            self._pending.clear()
            self._sealed_trace = tuple(
                self._entries[index] for index in sorted(self._entries)
            )
            sealed_failures: set[ExecutionFailureCode] = {
                cast(ExecutionFailureCode, entry.failure_code)
                for entry in self._sealed_trace
                if entry.failure_code is not None
            }
            sealed_failures.update(
                cast(ExecutionFailureCode, code)
                for _sequence, code, _related in self._consistency_observations
            )
            self._observed_failures = sealed_failures
            return len(missing)

    def _reserve(self) -> int:
        with self.state_gate:
            if self.closed:
                raise CallbackRejected("callback-incomplete")
            if self.next_sequence >= self.request.callback_limit:
                sequence = self.request.callback_limit
                self._observed_failures.add("callback-limit-exceeded")
                self._consistency_observations.add(
                    (sequence, "callback-limit-exceeded", None)
                )
                raise CallbackRejected("callback-limit-exceeded")
            sequence = self.next_sequence
            self.next_sequence += 1
            self._pending.add(sequence)
            return sequence

    def _commit(
        self,
        sequence_id: int,
        entry: CallbackTraceEntry,
        *,
        consistency_only: bool = False,
        related_sequence_id: int | None = None,
    ) -> None:
        entry_bytes = len(canonical_json(entry))
        with self.state_gate:
            if self.closed or sequence_id not in self._pending:
                raise CallbackRejected("callback-incomplete")
            self._pending.remove(sequence_id)
            if (
                entry_bytes > self.request.callback_trace_byte_limit
                or self._trace_byte_count + entry_bytes
                > self.request.callback_trace_byte_limit
            ):
                self.closed = True
                self._observed_failures.add("callback-trace-budget-exceeded")
                self._consistency_observations.add(
                    (sequence_id, "callback-trace-budget-exceeded", None)
                )
                raise CallbackRejected("callback-trace-budget-exceeded")
            self._entries[sequence_id] = entry
            self._trace_byte_count += entry_bytes
            if entry.failure_code is not None:
                self._observed_failures.add(entry.failure_code)
            if consistency_only and entry.failure_code is not None:
                self._consistency_observations.add(
                    (sequence_id, entry.failure_code, related_sequence_id)
                )

    def _reject(
        self,
        sequence_id: int,
        values: tuple[float, ...],
        code: CallbackFailureCode,
        *,
        nonfinite_positions: tuple[int, ...] = (),
        supplied_dimension: int | None = None,
        wrapper_state: Literal["idle", "callback-active"] = "idle",
        consistency_only: bool = False,
        related_sequence_id: int | None = None,
    ) -> FactorEvaluation:
        self._commit(
            sequence_id,
            _trace_entry(
                sequence_id,
                values,
                failure=code,
                nonfinite_positions=nonfinite_positions,
                supplied_dimension=supplied_dimension,
                wrapper_state=wrapper_state,
            ),
            consistency_only=consistency_only,
            related_sequence_id=related_sequence_id,
        )
        raise CallbackRejected(code)

    def __call__(self, values: object) -> FactorEvaluation:
        supplied = values
        exact_sequence = type(supplied) in {list, tuple}
        supplied_dimension = len(cast(Any, supplied)) if exact_sequence else None
        sequence_id = self._reserve()
        if not self.gate.acquire(blocking=False):
            with self.state_gate:
                if (
                    self.active_sequence is not None
                    and self.active_thread_id == threading.get_ident()
                ):
                    self._interrupted_sequences[self.active_sequence] = sequence_id
            return self._reject(
                sequence_id,
                (),
                "callback-reentrant",
                supplied_dimension=supplied_dimension,
                wrapper_state="callback-active",
                consistency_only=True,
            )
        with self.state_gate:
            self.active_sequence = sequence_id
            self.active_thread_id = threading.get_ident()
        try:
            if not exact_sequence or supplied_dimension != len(
                self.request.parameter_order
            ):
                return self._reject(
                    sequence_id,
                    (),
                    "callback-dimension-invalid",
                    supplied_dimension=supplied_dimension,
                )
            converted: list[float] = []
            invalid_positions: list[int] = []
            for index, item in enumerate(
                cast(list[object] | tuple[object, ...], supplied)
            ):
                if type(item) not in {int, float} or isinstance(item, bool):
                    invalid_positions.append(index)
                    continue
                try:
                    converted.append(float(cast(int | float, item)))
                except (OverflowError, ValueError):
                    invalid_positions.append(index)
            if invalid_positions:
                return self._reject(
                    sequence_id,
                    (),
                    "callback-nonfinite",
                    nonfinite_positions=tuple(invalid_positions),
                    supplied_dimension=supplied_dimension,
                    consistency_only=True,
                )
            values = tuple(converted)
            if not all(math.isfinite(value) for value in values):
                positions = tuple(
                    index
                    for index, value in enumerate(values)
                    if not math.isfinite(value)
                )
                return self._reject(
                    sequence_id,
                    (),
                    "callback-nonfinite",
                    nonfinite_positions=positions,
                    supplied_dimension=supplied_dimension,
                    consistency_only=True,
                )
            if any(
                value < low or value > high
                for value, low, high in zip(
                    values,
                    self.request.lower_bounds,
                    self.request.upper_bounds,
                    strict=True,
                )
            ):
                return self._reject(
                    sequence_id,
                    values,
                    "callback-out-of-bounds",
                    supplied_dimension=supplied_dimension,
                )
            parameters = ParameterVector(
                problem=self.request.problem,
                units=self.request.initial_parameters.units,
                values=values,
                variant=self.request.variant,
            )
            if "structural-geometry-invalid" in parameter_domain_failures(
                self.factor_set.contract, parameters
            ):
                return self._reject(
                    sequence_id,
                    values,
                    "callback-structure-invalid",
                    supplied_dimension=supplied_dimension,
                )
            try:
                evaluation = evaluate_factors(
                    self.factor_set, self.selection, parameters
                )
            except (ScansorError, ValidationError, ValueError, FloatingPointError):
                with self.state_gate:
                    interrupting_sequence = self._interrupted_sequences.get(sequence_id)
                return self._reject(
                    sequence_id,
                    values,
                    "callback-evaluation-undefined",
                    supplied_dimension=supplied_dimension,
                    consistency_only=interrupting_sequence is not None,
                    related_sequence_id=interrupting_sequence,
                )
            self._commit(
                sequence_id,
                _trace_entry(
                    sequence_id,
                    values,
                    evaluation=evaluation,
                    supplied_dimension=supplied_dimension,
                ),
            )
            return evaluation
        finally:
            with self.state_gate:
                if self.active_sequence == sequence_id:
                    self.active_sequence = None
                    self.active_thread_id = None
            self.gate.release()


def _ordered_failures(
    failures: list[ExecutionFailureCode],
) -> tuple[ExecutionFailureCode, ...]:
    return tuple(code for code in EXECUTION_FAILURE_ORDER if code in failures)


def _in_bounds(request: ExecutionRequest, values: tuple[float, ...]) -> bool:
    return len(values) == len(request.parameter_order) and all(
        low <= value <= high
        for value, low, high in zip(
            values, request.lower_bounds, request.upper_bounds, strict=True
        )
    )


def _response_parameters(
    request: ExecutionRequest, response: BackendResponse
) -> ParameterVector:
    return ParameterVector(
        problem=request.problem,
        units=request.initial_parameters.units,
        values=response.final_values,
        variant=request.variant,
    )


def _result(values: dict[str, object]) -> ExecutionResult:
    return _identified(ExecutionResult, "execution-result", "result_id", values)


def _external_failure(
    kind: Literal[
        "adapter-raised",
        "response-schema-invalid",
        "response-content-id-invalid",
        "response-provenance-invalid",
        "response-vector-invalid",
    ],
    observed_final_dimension: int | None = None,
) -> ExternalFailureEvidence:
    return _identified(
        ExternalFailureEvidence,
        "external-failure",
        "evidence_id",
        {
            "kind": kind,
            "observed_final_dimension": observed_final_dimension,
        },
    )


def _base_result_values(
    request: ExecutionRequest,
    preflight: PreflightDiagnostics,
    initial: FactorEvaluation | None,
) -> dict[str, object]:
    return {
        "adapter_id": request.adapter.adapter_id,
        "bound_activity": None,
        "callback_count": 0,
        "callback_consistency_evidence": None,
        "callback_trace": (),
        "callback_trace_byte_count": 0,
        "factor_set_id": request.factor_set_id,
        "external_failure_evidence": None,
        "final_evaluation": None,
        "final_objective": None,
        "final_parameters": None,
        "initial_evaluation": initial,
        "invocation": None,
        "preflight_id": preflight.preflight_id,
        "raw_response": None,
        "request": request,
        "selection_id": request.selection_id,
    }


def _callback_record(callback: _GuardedCallback) -> dict[str, object]:
    trace = callback.trace
    return {
        "callback_consistency_evidence": callback.consistency_evidence,
        "callback_count": len(trace),
        "callback_trace": trace,
        "callback_trace_byte_count": callback.trace_byte_count,
    }


def _termination_from_response(response: BackendResponse) -> NormalizedTermination:
    category = cast(
        Any,
        {
            "converged": "backend-converged",
            "limit": "backend-limit-reached",
            "stopped": "backend-stopped",
            "failure": "backend-reported-failure",
            "unknown": "backend-unknown",
        }[response.reported_termination],
    )
    return NormalizedTermination(
        category=category,
        raw_code=response.raw_code,
        raw_message=response.raw_message,
    )


def execute(
    request: ExecutionRequest,
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    adapter: BackendAdapter,
) -> ExecutionResult:
    request, factor_set, selection = _validate_execution_graph(
        request, factor_set, selection
    )
    try:
        runtime_descriptor = _revalidated(
            AdapterDescriptor, adapter.descriptor, "runtime adapter descriptor"
        )
    except AttributeError as error:
        raise ScansorError("runtime adapter has no descriptor") from error
    if runtime_descriptor != request.adapter:
        raise ScansorError("runtime adapter descriptor differs from execution request")
    preflight = preflight_factors(factor_set, selection, request.initial_parameters)
    try:
        initial = evaluate_factors(factor_set, selection, request.initial_parameters)
    except (ScansorError, ValidationError, ValueError, FloatingPointError):
        initial = None
    values = _base_result_values(request, preflight, initial)
    if not preflight.eligible_for_optimization:
        values.update(
            disposition="ineligible",
            failures=("preflight-ineligible",),
            normalized_termination=NormalizedTermination(category="not-invoked"),
        )
        return _result(values)

    invocation = _invocation(request)
    callback = _GuardedCallback(request, factor_set, selection)
    try:
        supplied_response = adapter.execute(invocation, callback)
    except Exception:
        incomplete_callbacks = callback.close()
        if incomplete_callbacks:
            incomplete_failures = list(callback.failure_codes)
            values.update(
                **_callback_record(callback),
                disposition="execution-failed",
                failures=_ordered_failures(incomplete_failures),
                external_failure_evidence=None,
                invocation=invocation,
                normalized_termination=NormalizedTermination(
                    category="callback-rejected"
                ),
            )
            return _result(values)
        callback_failures = list(callback.failure_codes)
        failures: list[ExecutionFailureCode] = []
        if callback_failures:
            failures.extend(callback_failures)
            termination = NormalizedTermination(category="callback-rejected")
        else:
            failures.append("adapter-raised")
            termination = NormalizedTermination(category="adapter-raised")
        values.update(
            **_callback_record(callback),
            disposition="execution-failed",
            failures=_ordered_failures(failures),
            external_failure_evidence=(
                None if callback_failures else _external_failure("adapter-raised")
            ),
            invocation=invocation,
            normalized_termination=termination,
        )
        return _result(values)

    incomplete_callbacks = callback.close()
    if incomplete_callbacks:
        incomplete_failures = list(callback.failure_codes)
        values.update(
            **_callback_record(callback),
            disposition="execution-failed",
            failures=_ordered_failures(incomplete_failures),
            external_failure_evidence=None,
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="callback-rejected"),
        )
        return _result(values)

    callback_failures = list(callback.failure_codes)
    if callback_failures:
        values.update(
            **_callback_record(callback),
            disposition="execution-failed",
            failures=_ordered_failures(callback_failures),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="callback-rejected"),
        )
        return _result(values)

    response_container_invalid = False
    if isinstance(supplied_response, BackendResponse):
        supplied_final_values = supplied_response.final_values
        response_container_invalid = type(supplied_final_values) is not tuple
    elif type(supplied_response) is dict:
        response_mapping = cast(dict[str, object], supplied_response)
        supplied_final = response_mapping.get("final_values")
        supplied_final_values = (
            supplied_final if type(supplied_final) in {list, tuple} else None
        )
        response_container_invalid = supplied_final_values is None
    else:
        supplied_final_values = None
    if response_container_invalid:
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=("response-schema-invalid",),
            external_failure_evidence=_external_failure("response-schema-invalid"),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
        )
        return _result(values)
    if supplied_final_values is not None and len(
        cast(Any, supplied_final_values)
    ) != len(request.parameter_order):
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=("response-vector-invalid",),
            external_failure_evidence=_external_failure(
                "response-vector-invalid", len(cast(Any, supplied_final_values))
            ),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
        )
        return _result(values)
    try:
        if isinstance(supplied_response, BackendResponse):
            response_data = supplied_response.model_dump(mode="python")
        elif type(supplied_response) is dict:
            response_data = supplied_response
        else:
            raise TypeError("response is not a mapping")
        response = BackendResponse.model_validate(response_data)
    except (TypeError, ValidationError, ValueError) as error:
        code: ExecutionFailureCode = (
            "response-content-id-invalid"
            if "response ID" in str(error)
            else "response-schema-invalid"
        )
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=(code,),
            external_failure_evidence=_external_failure(cast(Any, code)),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
        )
        return _result(values)
    if (
        response.adapter_id != invocation.adapter_id
        or response.invocation_id != invocation.invocation_id
        or response.request_id != invocation.request_id
    ):
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=("response-provenance-invalid",),
            external_failure_evidence=_external_failure("response-provenance-invalid"),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
        )
        return _result(values)
    if not _in_bounds(request, response.final_values):
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=("response-vector-invalid",),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
            raw_response=response,
        )
        return _result(values)
    final_parameters = _response_parameters(request, response)
    if "structural-geometry-invalid" in parameter_domain_failures(
        factor_set.contract, final_parameters
    ):
        values.update(
            **_callback_record(callback),
            disposition="invalid-backend-output",
            failures=("response-structure-invalid",),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
            raw_response=response,
        )
        return _result(values)
    try:
        final_evaluation = evaluate_factors(factor_set, selection, final_parameters)
    except (ScansorError, ValidationError, ValueError, FloatingPointError):
        values.update(
            **_callback_record(callback),
            disposition="execution-failed",
            failures=("final-evaluation-undefined",),
            invocation=invocation,
            normalized_termination=NormalizedTermination(category="invalid-response"),
            raw_response=response,
        )
        return _result(values)
    objective = 0.5 * math.fsum(
        residual * residual for residual in final_evaluation.raw_residuals_m
    )
    activity = cast(
        Any,
        tuple(
            "lower" if value == low else ("upper" if value == high else "interior")
            for value, low, high in zip(
                response.final_values,
                request.lower_bounds,
                request.upper_bounds,
                strict=True,
            )
        ),
    )
    values.update(
        **_callback_record(callback),
        bound_activity=BoundActivity(activity=activity),
        disposition="completed-not-assessed",
        failures=(),
        final_evaluation=final_evaluation,
        final_objective=objective,
        final_parameters=final_parameters,
        invocation=invocation,
        normalized_termination=_termination_from_response(response),
        raw_response=response,
    )
    return _result(values)


def execution_result_bytes(result: ExecutionResult) -> bytes:
    result = _revalidated(ExecutionResult, result, "execution result")
    data = canonical_json(result)
    if len(data) > MAX_EXECUTION_RESULT_BYTES:
        raise ScansorError("execution result exceeds its canonical byte limit")
    return data


def parse_execution_result(data: bytes) -> ExecutionResult:
    try:
        result = ExecutionResult.model_validate(
            parse_canonical_json(data, "execution result", MAX_EXECUTION_RESULT_BYTES)
        )
    except ValidationError as error:
        raise ScansorError(f"execution result model is invalid: {error}") from error
    if canonical_json(result) != data:
        raise ScansorError("execution result does not match its canonical model")
    return result


def _expected_callback_entry(
    recorded: CallbackTraceEntry,
    request: ExecutionRequest,
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
) -> CallbackTraceEntry:
    if recorded.failure_code == "callback-dimension-invalid" and (
        recorded.supplied_dimension is None
        or recorded.supplied_dimension != len(request.parameter_order)
    ):
        return _trace_entry(
            recorded.sequence_id,
            (),
            failure="callback-dimension-invalid",
            supplied_dimension=recorded.supplied_dimension,
        )
    callback = _GuardedCallback(request, factor_set, selection)
    callback.next_sequence = recorded.sequence_id
    try:
        evaluation = callback(recorded.values)
    except CallbackRejected:
        return callback.trace[-1]
    return _trace_entry(
        recorded.sequence_id,
        recorded.values,
        evaluation=evaluation,
        supplied_dimension=recorded.supplied_dimension,
    )


def replay_execution(
    result: ExecutionResult,
    request: ExecutionRequest,
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
) -> ExecutionResult:
    result = _revalidated(ExecutionResult, result, "execution result")
    request, factor_set, selection = _validate_execution_graph(
        request, factor_set, selection
    )
    if result.request != request:
        raise ScansorError("execution replay request differs from recorded request")
    preflight = preflight_factors(factor_set, selection, request.initial_parameters)
    if result.preflight_id != preflight.preflight_id:
        raise ScansorError("execution replay preflight differs from recorded preflight")
    try:
        initial = evaluate_factors(factor_set, selection, request.initial_parameters)
    except (ScansorError, ValidationError, ValueError, FloatingPointError):
        initial = None
    if result.initial_evaluation != initial:
        raise ScansorError("execution replay initial evaluation differs")
    consistency_observations = (
        {
            (item.sequence_id, item.code)
            for item in result.callback_consistency_evidence.observations
        }
        if result.callback_consistency_evidence is not None
        else set()
    )
    for entry in result.callback_trace:
        observation = (entry.sequence_id, entry.failure_code)
        if observation in consistency_observations:
            if entry.failure_code == "callback-reentrant":
                expected_entry = _trace_entry(
                    entry.sequence_id,
                    (),
                    failure="callback-reentrant",
                    supplied_dimension=entry.supplied_dimension,
                    wrapper_state="callback-active",
                )
            elif entry.failure_code == "callback-nonfinite":
                expected_entry = _trace_entry(
                    entry.sequence_id,
                    (),
                    failure="callback-nonfinite",
                    nonfinite_positions=entry.nonfinite_positions,
                    supplied_dimension=entry.supplied_dimension,
                )
            elif entry.failure_code == "callback-evaluation-undefined":
                parameters = ParameterVector(
                    problem=request.problem,
                    units=request.initial_parameters.units,
                    values=entry.values,
                    variant=request.variant,
                )
                if "structural-geometry-invalid" in parameter_domain_failures(
                    factor_set.contract, parameters
                ):
                    raise ScansorError(
                        "callback consistency evaluation input is structurally invalid"
                    )
                expected_entry = _trace_entry(
                    entry.sequence_id,
                    entry.values,
                    failure="callback-evaluation-undefined",
                    supplied_dimension=entry.supplied_dimension,
                )
            else:
                raise ScansorError("callback consistency evidence is invalid")
        elif entry.failure_code in {
            "callback-reentrant",
            "callback-nonfinite",
        }:
            raise ScansorError("callback trace lacks consistency-only evidence")
        else:
            expected_entry = _expected_callback_entry(
                entry, request, factor_set, selection
            )
        if expected_entry != entry:
            raise ScansorError("execution replay callback trace differs")
    if result.invocation is not None and result.invocation != _invocation(request):
        raise ScansorError("execution replay invocation differs")
    callback_failures = _ordered_failures(
        cast(
            list[ExecutionFailureCode],
            [
                entry.failure_code
                for entry in result.callback_trace
                if entry.failure_code is not None
            ]
            + [code for _sequence, code in consistency_observations],
        )
    )
    if result.disposition == "ineligible":
        valid = (
            not preflight.eligible_for_optimization
            and result.invocation is None
            and result.raw_response is None
            and result.external_failure_evidence is None
            and result.callback_consistency_evidence is None
            and not result.callback_trace
            and result.failures == ("preflight-ineligible",)
            and result.normalized_termination
            == NormalizedTermination(category="not-invoked")
        )
    elif result.disposition == "completed-not-assessed":
        valid = (
            preflight.eligible_for_optimization
            and result.raw_response is not None
            and result.final_parameters is not None
            and result.final_evaluation is not None
            and result.bound_activity is not None
            and result.external_failure_evidence is None
            and result.callback_consistency_evidence is None
            and not result.failures
            and not callback_failures
            and result.callback_consistency_evidence is None
        )
        if valid:
            final_parameters = result.final_parameters
            raw_response = result.raw_response
            assert final_parameters is not None
            assert raw_response is not None
            domain_valid = _in_bounds(request, final_parameters.values) and not (
                parameter_domain_failures(factor_set.contract, final_parameters)
            )
            try:
                final = evaluate_factors(factor_set, selection, final_parameters)
            except (ScansorError, ValidationError, ValueError, FloatingPointError):
                valid = False
            else:
                objective = 0.5 * math.fsum(
                    value * value for value in final.raw_residuals_m
                )
                activity = BoundActivity(
                    activity=cast(
                        Any,
                        tuple(
                            "lower"
                            if value == low
                            else ("upper" if value == high else "interior")
                            for value, low, high in zip(
                                final_parameters.values,
                                request.lower_bounds,
                                request.upper_bounds,
                                strict=True,
                            )
                        ),
                    ),
                )
                valid = (
                    domain_valid
                    and result.final_evaluation == final
                    and result.final_objective == objective
                    and result.bound_activity == activity
                    and raw_response.final_values == final_parameters.values
                    and result.normalized_termination
                    == _termination_from_response(raw_response)
                )
    elif result.disposition == "invalid-backend-output":
        valid = (
            preflight.eligible_for_optimization
            and result.invocation is not None
            and result.normalized_termination
            == NormalizedTermination(category="invalid-response")
            and not callback_failures
            and len(result.failures) == 1
            and result.failures[0].startswith("response-")
        )
        if valid and result.failures == ("response-vector-invalid",):
            if result.raw_response is None:
                evidence = result.external_failure_evidence
                valid = (
                    evidence is not None
                    and evidence.kind == "response-vector-invalid"
                    and evidence.observed_final_dimension is not None
                    and evidence.observed_final_dimension
                    != len(request.parameter_order)
                )
            else:
                valid = result.external_failure_evidence is None and not _in_bounds(
                    request, result.raw_response.final_values
                )
        elif valid and result.failures == ("response-structure-invalid",):
            if (
                result.raw_response is None
                or not _in_bounds(request, result.raw_response.final_values)
                or result.external_failure_evidence is not None
            ):
                valid = False
            else:
                response_parameters = _response_parameters(request, result.raw_response)
                valid = "structural-geometry-invalid" in parameter_domain_failures(
                    factor_set.contract, response_parameters
                )
        elif valid:
            evidence = result.external_failure_evidence
            valid = (
                result.raw_response is None
                and evidence is not None
                and evidence.kind == result.failures[0]
            )
    else:
        if result.normalized_termination == NormalizedTermination(
            category="adapter-raised"
        ):
            valid = (
                preflight.eligible_for_optimization
                and result.invocation is not None
                and result.raw_response is None
                and result.failures == ("adapter-raised",)
                and not callback_failures
                and result.external_failure_evidence is not None
                and result.external_failure_evidence.kind == "adapter-raised"
            )
        elif result.normalized_termination == NormalizedTermination(
            category="callback-rejected"
        ):
            if "callback-incomplete" in result.failures:
                valid = (
                    preflight.eligible_for_optimization
                    and result.invocation is not None
                    and result.raw_response is None
                    and result.external_failure_evidence is None
                    and result.callback_consistency_evidence is not None
                    and "callback-incomplete" in callback_failures
                    and result.failures == callback_failures
                )
            else:
                valid = (
                    preflight.eligible_for_optimization
                    and result.invocation is not None
                    and result.raw_response is None
                    and result.external_failure_evidence is None
                    and bool(callback_failures)
                    and result.failures == callback_failures
                )
        else:
            valid = (
                preflight.eligible_for_optimization
                and result.invocation is not None
                and result.raw_response is not None
                and result.external_failure_evidence is None
                and result.callback_consistency_evidence is None
                and result.failures == ("final-evaluation-undefined",)
                and result.normalized_termination
                == NormalizedTermination(category="invalid-response")
                and not callback_failures
            )
            if valid:
                assert result.raw_response is not None
                response_parameters = _response_parameters(request, result.raw_response)
                valid = _in_bounds(request, response_parameters.values) and not (
                    parameter_domain_failures(factor_set.contract, response_parameters)
                )
                if valid:
                    try:
                        _ = evaluate_factors(factor_set, selection, response_parameters)
                    except (
                        ScansorError,
                        ValidationError,
                        ValueError,
                        FloatingPointError,
                    ):
                        pass
                    else:
                        valid = False
    if not valid:
        raise ScansorError("execution replay disposition facts are inconsistent")
    return result


def assess_held_out(
    result: ExecutionResult,
    request: ExecutionRequest,
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    mapping: MappingResult,
) -> HeldOutAssessment:
    result = _revalidated(ExecutionResult, result, "execution result")
    request, factor_set, selection = _validate_execution_graph(
        request, factor_set, selection
    )
    sealed_bytes = execution_result_bytes(result)
    _ = replay_execution(result, request, factor_set, selection)
    mapping = _revalidated(MappingResult, mapping, "mapping result")
    if result.request != request or result.disposition != "completed-not-assessed":
        raise ScansorError("held-out assessment requires the exact completed result")
    if result.final_parameters is None:
        raise ScansorError("completed execution result lacks final parameters")
    reconstructed = instantiate_factors(mapping)
    if reconstructed != factor_set:
        raise ScansorError("held-out mapping does not reconstruct the factor set")
    reconstructed_selection = select_active_factors(
        reconstructed, selection.active_factor_ids
    )
    if reconstructed_selection != selection:
        raise ScansorError("held-out active-factor provenance differs")

    rows: list[HeldOutRowAssessment] = []
    for observation in mapping.held_out_observations:
        support = assess_nominal_support(
            observation.point_model_m,
            mapping.request.variant,
            mapping.request.thresholds,
        )
        candidates = tuple(
            HeldOutSupportCandidate(
                absolute_distance_m=item.absolute_distance_m,
                element_id=item.element_id,
                kind=item.kind,
                signed_distance_m=item.signed_distance_m,
            )
            for item in support.candidates
        )
        assigned = (
            support.candidates[0].element_id if support.outcome == "assigned" else None
        )
        residual: float | None = None
        outcome: HeldOutOutcome = support.outcome
        if assigned is not None:
            try:
                residual = evaluate_support_residual(
                    observation.point_model_m,
                    cast(Any, assigned),
                    result.final_parameters,
                )
            except (ScansorError, ValidationError, ValueError, FloatingPointError):
                outcome = "evaluation-error"
            if residual is not None and not math.isfinite(residual):
                residual = None
                outcome = "evaluation-error"
        row_values: dict[str, object] = {
            "assigned_element_id": assigned,
            "candidates": candidates,
            "geometric_clearance_m": support.geometric_clearance_m,
            "observation_id": observation.observation_id,
            "outcome": outcome,
            "raw_residual_m": residual,
            "row_index": observation.row_index,
        }
        rows.append(
            _identified(
                HeldOutRowAssessment,
                "held-out-row",
                "row_assessment_id",
                row_values,
            )
        )
    residuals = tuple(
        row.raw_residual_m for row in rows if row.raw_residual_m is not None
    )
    summary = HeldOutSummary(
        count=len(residuals),
        maximum_raw_residual_m=max(residuals) if residuals else None,
        mean_raw_residual_m=(
            math.fsum(residuals) / len(residuals) if residuals else None
        ),
        minimum_raw_residual_m=min(residuals) if residuals else None,
        root_mean_square_raw_residual_m=(
            math.sqrt(math.fsum(value * value for value in residuals) / len(residuals))
            if residuals
            else None
        ),
    )
    outcomes: tuple[HeldOutOutcome, ...] = (
        "assigned",
        "ambiguous",
        "transition",
        "gap",
        "outlier",
        "evaluation-error",
    )
    assessment = _identified(
        HeldOutAssessment,
        "held-out-assessment",
        "assessment_id",
        {
            "counts": {
                outcome: sum(row.outcome == outcome for row in rows)
                for outcome in outcomes
            },
            "execution_result_id": result.result_id,
            "factor_set_id": factor_set.factor_set_id,
            "mapping_run_id": mapping.mapping_run_id,
            "request_id": request.request_id,
            "rows": tuple(rows),
            "selection_id": selection.selection_id,
            "summary": summary,
        },
    )
    if execution_result_bytes(result) != sealed_bytes:
        raise ScansorError("held-out assessment changed the sealed execution result")
    return assessment
