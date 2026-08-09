from __future__ import annotations

import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast, override

import numpy as np
import pytest
from pydantic import ValidationError

import scansor.execution_models as execution_models_module
import scansor.stepped_rotational_execution as execution_module
from scansor.errors import ScansorError
from scansor.execution_models import (
    AdapterDescriptor,
    AdapterInvocation,
    BackendResponse,
    CallbackConsistencyEvidence,
    CallbackConsistencyObservation,
    CallbackTraceEntry,
    ExecutionRequest,
    ExecutionResult,
    HeldOutAssessment,
    HeldOutSummary,
    NormalizedTermination,
)
from scansor.factor_models import (
    NOMINAL_SHAPE,
    ActiveFactorSelection,
    FactorEvaluation,
    InstantiatedFactorSet,
    ParameterVector,
    Problem,
    Variant,
    content_id,
)
from scansor.mapping_models import MappingResult, MappingThresholds
from scansor.serialization import canonical_json, sha256
from scansor.stepped_rotational import assess_nominal_support, build_mapping
from scansor.stepped_rotational_execution import (
    ResidualJacobianCallback,
    adapter_descriptor,
    assess_held_out,
    backend_response,
    create_execution_request,
    execute,
    execution_result_bytes,
    parse_execution_result,
    replay_execution,
)
from scansor.stepped_rotational_factors import (
    evaluate_factors,
    instantiate_factors,
    select_active_factors,
)
from tests.test_factors import factor_case, parameters
from tests.test_mapping import canonical_bytes, fixture_points, request_for

TEST_ADAPTER_DESCRIPTOR = adapter_descriptor("tests.reference", "1")
BackendTermination = Literal["converged", "limit", "stopped", "failure", "unknown"]


class ShapeReferenceAdapter:
    def __init__(self, descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR) -> None:
        self.descriptor: AdapterDescriptor = descriptor
        self.invocations: list[AdapterInvocation] = []
        self.evaluations: list[FactorEvaluation] = []

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        self.invocations.append(invocation)
        initial = callback(invocation.initial_values)
        self.evaluations.append(initial)
        jacobian = np.asarray(initial.jacobian)
        residuals = np.asarray(initial.raw_residuals_m)
        final = (
            np.asarray(invocation.initial_values)
            + np.linalg.lstsq(jacobian, -residuals, rcond=None)[0]
        )
        return backend_response(invocation, tuple(final), "converged", raw_code="test")


class PoseReferenceAdapter:
    def __init__(self, descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR) -> None:
        self.descriptor: AdapterDescriptor = descriptor

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        current = np.asarray(invocation.initial_values)
        lower = np.asarray(invocation.lower_bounds)
        upper = np.asarray(invocation.upper_bounds)
        for _ in range(12):
            evaluation = callback(tuple(float(value) for value in current))
            step = np.linalg.lstsq(
                np.asarray(evaluation.jacobian),
                -np.asarray(evaluation.raw_residuals_m),
                rcond=None,
            )[0]
            current = np.clip(current + step, lower, upper)
            if float(np.linalg.norm(step)) < 1e-13:
                break
        return backend_response(
            invocation, tuple(float(value) for value in current), "converged"
        )


class ReturningAdapter:
    def __init__(
        self,
        values: tuple[float, ...],
        termination: BackendTermination = "converged",
        descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR,
    ) -> None:
        self.descriptor: AdapterDescriptor = descriptor
        self.values: tuple[float, ...] = values
        self.termination: BackendTermination = termination
        self.calls: int = 0
        self.invocation: AdapterInvocation | None = None

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        _ = callback
        self.calls += 1
        self.invocation = invocation
        return backend_response(
            invocation,
            self.values,
            self.termination,
            raw_code="bounded",
            raw_message="test-only",
        )


class RaisingAdapter:
    def __init__(self, descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR) -> None:
        self.descriptor: AdapterDescriptor = descriptor

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        _ = invocation
        _ = callback
        raise RuntimeError("platform-specific secret traceback text")


class CallbackAdapter:
    def __init__(
        self,
        value: object,
        descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR,
    ) -> None:
        self.descriptor: AdapterDescriptor = descriptor
        self.value: object = value

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        _ = callback(self.value)
        return backend_response(invocation, invocation.initial_values, "unknown")


class MaliciousAdapter:
    def __init__(
        self,
        mutation: Callable[[BackendResponse, AdapterInvocation], object],
        descriptor: AdapterDescriptor = TEST_ADAPTER_DESCRIPTOR,
    ) -> None:
        self.descriptor: AdapterDescriptor = descriptor
        self.mutation: Callable[[BackendResponse, AdapterInvocation], object] = mutation

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        _ = callback
        response = backend_response(invocation, invocation.initial_values, "converged")
        return self.mutation(response, invocation)


def wrong_request_response(response: BackendResponse) -> BackendResponse:
    values = response.model_dump(mode="python", exclude={"response_id"})
    values["request_id"] = "execution-request." + "0" * 64
    provisional = BackendResponse.model_construct(response_id="", **values)
    values["response_id"] = execution_models_module.execution_content_id(
        "backend-response", provisional, "response_id"
    )
    return BackendResponse(**values)


def reidentified_result(result: ExecutionResult, **updates: object) -> ExecutionResult:
    values = cast(
        dict[str, Any],
        {
            name: getattr(result, name)
            for name in type(result).model_fields
            if name != "result_id"
        }
        | updates,
    )
    provisional = ExecutionResult.model_construct(result_id="", **values)
    values["result_id"] = execution_models_module.execution_content_id(
        "execution-result", provisional, "result_id"
    )
    return ExecutionResult(**values)


def reidentified_invocation(
    invocation: AdapterInvocation, **updates: object
) -> AdapterInvocation:
    values = cast(
        dict[str, Any],
        invocation.model_dump(mode="python", exclude={"invocation_id"}) | updates,
    )
    provisional = AdapterInvocation.model_construct(invocation_id="", **values)
    values["invocation_id"] = execution_models_module.execution_content_id(
        "invocation", provisional, "invocation_id"
    )
    return AdapterInvocation(**values)


def reidentified_consistency(
    evidence: CallbackConsistencyEvidence, **updates: object
) -> CallbackConsistencyEvidence:
    values = cast(
        dict[str, Any],
        {
            name: getattr(evidence, name)
            for name in type(evidence).model_fields
            if name != "evidence_id"
        }
        | updates,
    )
    provisional = CallbackConsistencyEvidence.model_construct(evidence_id="", **values)
    values["evidence_id"] = execution_models_module.execution_content_id(
        "callback-consistency", provisional, "evidence_id"
    )
    return CallbackConsistencyEvidence(**values)


def reidentified_callback_entry(
    entry: CallbackTraceEntry, **updates: object
) -> CallbackTraceEntry:
    values = cast(
        dict[str, Any],
        entry.model_dump(mode="python", exclude={"callback_entry_id"}) | updates,
    )
    provisional = CallbackTraceEntry.model_construct(callback_entry_id="", **values)
    values["callback_entry_id"] = execution_models_module.execution_content_id(
        "callback-entry", provisional, "callback_entry_id"
    )
    return CallbackTraceEntry(**values)


def reidentified_assessment(
    assessment: HeldOutAssessment, **updates: object
) -> HeldOutAssessment:
    values = cast(
        dict[str, Any],
        {
            name: getattr(assessment, name)
            for name in type(assessment).model_fields
            if name != "assessment_id"
        }
        | updates,
    )
    provisional = HeldOutAssessment.model_construct(assessment_id="", **values)
    values["assessment_id"] = execution_models_module.execution_content_id(
        "held-out-assessment", provisional, "assessment_id"
    )
    return HeldOutAssessment(**values)


def invalid_response_schema(
    _response: BackendResponse, _invocation: AdapterInvocation
) -> object:
    return object()


def invalid_response_id(
    response: BackendResponse, _invocation: AdapterInvocation
) -> object:
    return response.model_copy(update={"response_id": "backend-response." + "0" * 64})


def invalid_response_provenance(
    response: BackendResponse, _invocation: AdapterInvocation
) -> object:
    return wrong_request_response(response)


def invalid_response_vector(
    response: BackendResponse, invocation: AdapterInvocation
) -> object:
    return backend_response(invocation, response.final_values[:-1], "converged")


def execution_case(
    variant: Variant = "asymmetric-datum-flat",
    problem: Problem = "fixed-pose-shape",
    initial_values: tuple[float, ...] | None = None,
) -> tuple[
    MappingResult,
    InstantiatedFactorSet,
    ActiveFactorSelection,
    ExecutionRequest,
]:
    mapping, factor_set, selection = factor_case(variant)
    initial = parameters(variant, problem, initial_values)
    request = create_execution_request(
        factor_set, selection, initial, TEST_ADAPTER_DESCRIPTOR
    )
    return mapping, factor_set, selection, request


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_affine_shape_recovery_is_test_only_and_deterministic(
    variant: Variant,
) -> None:
    size = 7 if variant == "asymmetric-datum-flat" else 6
    truth = NOMINAL_SHAPE[:size]
    offset = (-0.0004, 0.0005, -0.0003, -0.0002, 0.0003, -0.0004, 0.0002)
    initial = tuple(value + delta for value, delta in zip(truth, offset, strict=False))
    _mapping, factor_set, selection, request = execution_case(
        variant, initial_values=initial
    )
    adapter = ShapeReferenceAdapter()
    first = execute(request, factor_set, selection, adapter)
    second = execute(request, factor_set, selection, ShapeReferenceAdapter())
    assert first == second
    assert first.disposition == "completed-not-assessed"
    assert first.normalized_termination.category == "backend-converged"
    assert first.final_parameters is not None
    np.testing.assert_allclose(first.final_parameters.values, truth, atol=1e-15, rtol=0)
    assert first.final_objective == pytest.approx(0.0, abs=1e-30)
    assert first.callback_count == 1
    assert replay_execution(first, request, factor_set, selection) == first
    invocation_json = canonical_json(adapter.invocations[0]).decode("ascii")
    for prohibited in (
        "held_out",
        "normal",
        "canonical",
        "point_model",
        "mappingresult",
    ):
        assert prohibited not in invocation_json.lower()


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_bounded_nonlinear_pose_recovery_and_axisymmetric_roll_gauge(
    variant: Variant,
) -> None:
    initial = (0.0003, -0.0002, 0.0001, 0.012, -0.009, 0.025)
    _mapping, factor_set, selection, request = execution_case(
        variant, "fixed-geometry-pose-correction", initial
    )
    result = execute(request, factor_set, selection, PoseReferenceAdapter())
    assert result.disposition == "completed-not-assessed"
    assert result.final_parameters is not None
    values = result.final_parameters.values
    np.testing.assert_allclose(values[:5], (0.0,) * 5, atol=2e-12, rtol=0)
    if variant == "axisymmetric":
        assert values[5] == pytest.approx(initial[5], abs=1e-5)
        assert abs(values[5]) > 0.02
    else:
        assert values[5] == pytest.approx(0.0, abs=2e-12)


def test_request_copies_exact_contract_policy_and_revalidates_graph() -> None:
    _mapping, factor_set, selection, request = execution_case()
    assert request.contract_id == factor_set.contract.contract_id
    assert request.parameter_order == factor_set.contract.shape_parameter_order
    assert request.lower_bounds == factor_set.contract.shape_lower_m
    assert request.upper_bounds == factor_set.contract.shape_upper_m
    assert request.parameter_scales == factor_set.contract.shape_scales_m
    assert request.callback_protocol_revision == "stepped-rotational-callback-v2"
    assert request.callback_limit == 10_000
    assert request.callback_trace_byte_limit == 16 * 1024 * 1024
    stale = request.model_copy(update={"lower_bounds": (0.0,) * 7})
    with pytest.raises(ScansorError, match=r"invalid execution request|does not match"):
        _ = execute(stale, factor_set, selection, ReturningAdapter(NOMINAL_SHAPE))


def test_runtime_adapter_descriptor_must_match_before_invocation() -> None:
    _mapping, factor_set, selection, request = execution_case()
    mismatch = ReturningAdapter(
        NOMINAL_SHAPE,
        descriptor=adapter_descriptor("tests.other", "1"),
    )
    with pytest.raises(ScansorError, match="descriptor differs"):
        _ = execute(request, factor_set, selection, mismatch)
    assert mismatch.calls == 0

    stale_descriptor = request.adapter.model_copy(
        update={"implementation": "tests.stale"}
    )
    stale = ReturningAdapter(NOMINAL_SHAPE, descriptor=stale_descriptor)
    with pytest.raises(ScansorError, match="invalid runtime adapter descriptor"):
        _ = execute(request, factor_set, selection, stale)
    assert stale.calls == 0


def test_preflight_ineligible_short_circuits_without_adapter() -> None:
    _mapping, factor_set, _selection = factor_case()
    empty = select_active_factors(factor_set, ())
    request = create_execution_request(
        factor_set,
        empty,
        parameters("asymmetric-datum-flat"),
        adapter_descriptor("tests.recording", "1"),
    )
    adapter = ReturningAdapter(NOMINAL_SHAPE, descriptor=request.adapter)
    result = execute(request, factor_set, empty, adapter)
    assert result.disposition == "ineligible"
    assert result.normalized_termination.category == "not-invoked"
    assert result.invocation is None
    assert adapter.calls == 0
    assert replay_execution(result, request, factor_set, empty) == result


@pytest.mark.parametrize(
    ("value", "failure"),
    [
        ((0.0,), "callback-dimension-invalid"),
        ((math.nan,) * 7, "callback-nonfinite"),
        ((1.0,) * 7, "callback-out-of-bounds"),
        (
            (0.010, 0.017, 0.0145, 0.018, 0.019, 0.083, 0.0165),
            "callback-out-of-bounds",
        ),
    ],
)
def test_callback_rejections_are_stable_and_replayable(
    value: object, failure: str
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, CallbackAdapter(value))
    assert result.disposition == "execution-failed"
    assert result.normalized_termination.category == "callback-rejected"
    assert result.failures == (failure,)
    assert result.callback_trace[-1].status == "rejected"
    assert replay_execution(result, request, factor_set, selection) == result


def test_structural_callback_and_callback_limit_rejections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, request = execution_case()

    def structurally_invalid(
        _contract: object, _value: object
    ) -> tuple[Literal["structural-geometry-invalid"]]:
        return ("structural-geometry-invalid",)

    monkeypatch.setattr(
        execution_module,
        "parameter_domain_failures",
        structurally_invalid,
    )
    result = execute(
        request,
        factor_set,
        selection,
        CallbackAdapter(request.initial_parameters.values),
    )
    assert result.failures == ("callback-structure-invalid",)

    monkeypatch.undo()
    limited = create_execution_request(
        factor_set,
        selection,
        request.initial_parameters,
        request.adapter,
        callback_limit=1,
    )
    result = execute(
        limited,
        factor_set,
        selection,
        CallbackAdapter(limited.initial_parameters.values),
    )
    assert result.disposition == "completed-not-assessed"


def test_callback_evaluation_domain_and_reentrancy_are_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, request = execution_case()

    def undefined(
        _factor_set: InstantiatedFactorSet,
        _selection: ActiveFactorSelection,
        _parameters: ParameterVector,
    ) -> FactorEvaluation:
        raise ScansorError("undefined")

    class UndefinedAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            monkeypatch.setattr(execution_module, "evaluate_factors", undefined)
            return callback(invocation.initial_values)

    result = execute(
        request,
        factor_set,
        selection,
        UndefinedAdapter(),
    )
    assert result.failures == ("callback-evaluation-undefined",)
    assert result.callback_trace[-1].status == "rejected"

    monkeypatch.undo()
    with pytest.raises(ScansorError, match="callback trace differs"):
        _ = replay_execution(result, request, factor_set, selection)
    trace_bytes = result.callback_trace_byte_count
    evidence_values = cast(
        dict[str, Any],
        {
            "observations": (
                CallbackConsistencyObservation(
                    code="callback-evaluation-undefined", sequence_id=0
                ),
            ),
            "retained_trace_byte_count": trace_bytes,
            "trace_byte_limit": request.callback_trace_byte_limit,
        },
    )
    provisional_evidence = CallbackConsistencyEvidence.model_construct(
        evidence_id="", **evidence_values
    )
    with pytest.raises(ValidationError, match="lacks a reentrant cause"):
        _ = CallbackConsistencyEvidence(
            evidence_id=execution_models_module.execution_content_id(
                "callback-consistency", provisional_evidence, "evidence_id"
            ),
            **evidence_values,
        )
    original = evaluate_factors
    active_callback: ResidualJacobianCallback | None = None

    def recurse(
        current_factor_set: InstantiatedFactorSet,
        current_selection: ActiveFactorSelection,
        current_parameters: ParameterVector,
    ) -> FactorEvaluation:
        assert active_callback is not None
        _ = active_callback(request.initial_parameters.values)
        return original(current_factor_set, current_selection, current_parameters)

    class ReentrantAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            nonlocal active_callback
            active_callback = callback
            monkeypatch.setattr(execution_module, "evaluate_factors", recurse)
            return callback(invocation.initial_values)

    result = execute(request, factor_set, selection, ReentrantAdapter())
    assert "callback-reentrant" in result.failures
    assert [entry.failure_code for entry in result.callback_trace] == [
        "callback-evaluation-undefined",
        "callback-reentrant",
    ]
    monkeypatch.undo()
    assert replay_execution(result, request, factor_set, selection) == result
    assert result.callback_consistency_evidence is not None
    assert [
        (item.sequence_id, item.code, item.related_sequence_id)
        for item in result.callback_consistency_evidence.observations
    ] == [
        (0, "callback-evaluation-undefined", 1),
        (1, "callback-reentrant", None),
    ]
    reentrant_first_trace = (
        reidentified_callback_entry(result.callback_trace[1], sequence_id=0),
        reidentified_callback_entry(result.callback_trace[0], sequence_id=1),
    )
    reentrant_first_bytes = sum(
        len(canonical_json(entry)) for entry in reentrant_first_trace
    )
    reentrant_first_evidence = reidentified_consistency(
        result.callback_consistency_evidence,
        observations=(
            CallbackConsistencyObservation(code="callback-reentrant", sequence_id=0),
            CallbackConsistencyObservation(
                code="callback-evaluation-undefined",
                related_sequence_id=0,
                sequence_id=1,
            ),
        ),
        retained_trace_byte_count=reentrant_first_bytes,
    )
    reentrant_first = reidentified_result(
        result,
        callback_consistency_evidence=reentrant_first_evidence,
        callback_trace=reentrant_first_trace,
        callback_trace_byte_count=reentrant_first_bytes,
    )
    assert replay_execution(reentrant_first, request, factor_set, selection) == (
        reentrant_first
    )
    impossible_evaluation = reidentified_callback_entry(
        result.callback_trace[0],
        supplied_dimension=len(request.parameter_order),
        values=(1.0,) * len(request.parameter_order),
    )
    impossible_trace = (impossible_evaluation, result.callback_trace[1])
    impossible_bytes = sum(len(canonical_json(entry)) for entry in impossible_trace)
    impossible_evidence = reidentified_consistency(
        result.callback_consistency_evidence,
        retained_trace_byte_count=impossible_bytes,
    )
    with pytest.raises(ValidationError, match="callback input is impossible"):
        _ = reidentified_result(
            result,
            callback_consistency_evidence=impossible_evidence,
            callback_trace=impossible_trace,
            callback_trace_byte_count=impossible_bytes,
        )
    with pytest.raises(ValidationError, match="consistency evidence"):
        _ = reidentified_result(result, callback_consistency_evidence=None)


def test_malformed_and_oversized_callback_inputs_are_bounded_and_replayable() -> None:
    _mapping, factor_set, selection, request = execution_case()
    for value, failure in (
        ([object()] * 7, "callback-nonfinite"),
        ([0.0] * 10_001, "callback-dimension-invalid"),
    ):
        result = execute(request, factor_set, selection, CallbackAdapter(value))
        assert result.failures == (failure,)
        assert result.callback_trace[0].values == ()
        assert len(execution_result_bytes(result)) < 1_000_000
        assert replay_execution(result, request, factor_set, selection) == result

    class ReentrantSequence(list[float]):
        pass

    result = execute(
        request,
        factor_set,
        selection,
        CallbackAdapter(ReentrantSequence(request.initial_parameters.values)),
    )
    assert result.failures == ("callback-dimension-invalid",)
    assert replay_execution(result, request, factor_set, selection) == result

    class HostileFloat(float):
        @override
        def __float__(self) -> float:
            raise RuntimeError("must not execute")

    hostile = tuple(HostileFloat(value) for value in request.initial_parameters.values)
    result = execute(request, factor_set, selection, CallbackAdapter(hostile))
    assert result.failures == ("callback-nonfinite",)
    assert result.callback_count == 1
    assert replay_execution(result, request, factor_set, selection) == result


@pytest.mark.parametrize("callback_limit", [1, 3])
def test_callback_limit_allows_n_attempts_and_rejects_n_plus_one(
    callback_limit: int,
) -> None:
    _mapping, factor_set, selection, base_request = execution_case()
    request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_limit=callback_limit,
    )

    class CatchingAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            for _ in range(callback_limit):
                _ = callback(invocation.initial_values)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, CatchingAdapter())
    assert result.callback_count == callback_limit
    assert result.failures == ("callback-limit-exceeded",)
    assert result.callback_consistency_evidence is not None
    assert [
        (item.sequence_id, item.code)
        for item in result.callback_consistency_evidence.observations
    ] == [(callback_limit, "callback-limit-exceeded")]
    assert replay_execution(result, request, factor_set, selection) == result


def test_callback_evidence_rejects_impossible_reservation_histories() -> None:
    _mapping, factor_set, selection, base_request = execution_case()
    request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_limit=1,
    )

    class OverLimitAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback(invocation.initial_values)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, OverLimitAdapter())
    assert result.callback_consistency_evidence is not None
    evidence = reidentified_consistency(
        result.callback_consistency_evidence,
        retained_trace_byte_count=0,
    )
    with pytest.raises(ValidationError, match="reservation prefix"):
        _ = reidentified_result(
            result,
            callback_consistency_evidence=evidence,
            callback_count=0,
            callback_trace=(),
            callback_trace_byte_count=0,
        )
    with pytest.raises(ValidationError, match="multiple outcomes"):
        _ = reidentified_consistency(
            result.callback_consistency_evidence,
            observations=(
                CallbackConsistencyObservation(
                    code="callback-incomplete", sequence_id=0
                ),
                CallbackConsistencyObservation(
                    code="callback-trace-budget-exceeded", sequence_id=0
                ),
            ),
        )
    with pytest.raises(ValidationError, match="multiple evaluations"):
        _ = reidentified_consistency(
            result.callback_consistency_evidence,
            observations=(
                CallbackConsistencyObservation(
                    code="callback-evaluation-undefined",
                    related_sequence_id=2,
                    sequence_id=0,
                ),
                CallbackConsistencyObservation(
                    code="callback-evaluation-undefined",
                    related_sequence_id=2,
                    sequence_id=1,
                ),
                CallbackConsistencyObservation(
                    code="callback-reentrant", sequence_id=2
                ),
            ),
        )


def test_callback_sanitized_evidence_must_be_canonical() -> None:
    _mapping, factor_set, selection, request = execution_case()
    dimension = execute(
        request, factor_set, selection, CallbackAdapter((0.0,))
    ).callback_trace[0]
    with pytest.raises(ValidationError, match=r"dimension-invalid.*canonical"):
        _ = reidentified_callback_entry(dimension, values=(0.0,))

    nonfinite_result = execute(
        request, factor_set, selection, CallbackAdapter((math.nan,) * 7)
    )
    nonfinite = nonfinite_result.callback_trace[0]
    with pytest.raises(ValidationError, match="finite-safe positions"):
        _ = reidentified_callback_entry(nonfinite, nonfinite_positions=(1, 0))
    with pytest.raises(ValidationError, match="finite-safe positions"):
        _ = reidentified_callback_entry(nonfinite, nonfinite_positions=(-1,))
    wrong_dimension = reidentified_callback_entry(nonfinite, supplied_dimension=8)
    wrong_bytes = len(canonical_json(wrong_dimension))
    assert nonfinite_result.callback_consistency_evidence is not None
    evidence = reidentified_consistency(
        nonfinite_result.callback_consistency_evidence,
        retained_trace_byte_count=wrong_bytes,
    )
    with pytest.raises(ValidationError, match="dimension disagrees"):
        _ = reidentified_result(
            nonfinite_result,
            callback_consistency_evidence=evidence,
            callback_trace=(wrong_dimension,),
            callback_trace_byte_count=wrong_bytes,
        )


def test_replay_matches_initial_evaluation_exception_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, _selection = factor_case()
    selection = select_active_factors(factor_set, ())
    request = create_execution_request(
        factor_set,
        selection,
        parameters("asymmetric-datum-flat"),
        TEST_ADAPTER_DESCRIPTOR,
    )

    def invalid_initial(
        _factor_set: InstantiatedFactorSet,
        _selection: ActiveFactorSelection,
        _parameters: ParameterVector,
    ) -> FactorEvaluation:
        raise ValueError("deterministic initial failure")

    monkeypatch.setattr(execution_module, "evaluate_factors", invalid_initial)
    result = execute(
        request,
        factor_set,
        selection,
        ReturningAdapter(NOMINAL_SHAPE),
    )
    assert result.initial_evaluation is None
    assert replay_execution(result, request, factor_set, selection) == result


def test_callback_trace_budget_fails_closed_for_single_and_cumulative_entries() -> None:
    _mapping, factor_set, selection, base_request = execution_case()
    baseline = execute(
        base_request,
        factor_set,
        selection,
        ShapeReferenceAdapter(),
    )
    one_entry_bytes = baseline.callback_trace_byte_count
    assert one_entry_bytes > 1_024

    single_request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_trace_byte_limit=one_entry_bytes - 1,
    )
    single = execute(
        single_request,
        factor_set,
        selection,
        CallbackAdapter(
            single_request.initial_parameters.values,
            descriptor=single_request.adapter,
        ),
    )
    assert single.callback_count == 0
    assert single.callback_trace_byte_count == 0
    assert single.failures == ("callback-trace-budget-exceeded",)
    assert replay_execution(single, single_request, factor_set, selection) == single

    cumulative_limit = one_entry_bytes + max(1_024, one_entry_bytes // 2)
    cumulative_request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_trace_byte_limit=cumulative_limit,
    )

    class TwoCallAdapter:
        descriptor: AdapterDescriptor = cumulative_request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback(invocation.initial_values)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    cumulative = execute(
        cumulative_request,
        factor_set,
        selection,
        TwoCallAdapter(),
    )
    assert cumulative.callback_count == 1
    assert cumulative.callback_trace_byte_count == one_entry_bytes
    assert cumulative.failures == ("callback-trace-budget-exceeded",)
    assert cumulative.callback_trace_byte_count <= cumulative_limit
    assert (
        replay_execution(cumulative, cumulative_request, factor_set, selection)
        == cumulative
    )


def test_trace_budget_overflow_seals_reverse_commit_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, base_request = execution_case()
    baseline = execute(
        base_request,
        factor_set,
        selection,
        ShapeReferenceAdapter(),
    )
    request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_trace_byte_limit=baseline.callback_trace_byte_count
        + max(1_024, baseline.callback_trace_byte_count // 2),
    )
    guarded_type = cast(type[object], vars(execution_module)["_GuardedCallback"])
    original_reserve = cast(Callable[[object], int], vars(guarded_type)["_reserve"])
    first_reserved = threading.Event()
    release_first = threading.Event()
    threads: list[threading.Thread] = []
    outcomes: list[str] = []

    def reserve_inverted(callback: object) -> int:
        sequence = original_reserve(callback)
        if sequence == 0:
            first_reserved.set()
            assert release_first.wait(timeout=5)
        return sequence

    def invoke(callback: ResidualJacobianCallback, values: object) -> None:
        try:
            _ = callback(values)
        except ScansorError as error:
            outcomes.append(str(error))

    class ReverseCommitAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            monkeypatch.setattr(
                guarded_type,
                "_reserve",
                reserve_inverted,
            )
            thread = threading.Thread(
                target=invoke,
                args=(callback, invocation.initial_values),
                daemon=True,
            )
            threads.append(thread)
            thread.start()
            assert first_reserved.wait(timeout=5)
            _ = callback(invocation.initial_values)
            release_first.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, ReverseCommitAdapter())
    monkeypatch.undo()
    assert outcomes == ["callback-trace-budget-exceeded"]
    assert result.failures == ("callback-trace-budget-exceeded",)
    assert result.callback_trace == ()
    assert result.callback_trace_byte_count == 0
    assert replay_execution(result, request, factor_set, selection) == result


def test_concurrent_reentrancy_reserves_bounded_sequence_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, base_request = execution_case()
    request = create_execution_request(
        factor_set,
        selection,
        base_request.initial_parameters,
        base_request.adapter,
        callback_limit=2,
    )
    original = evaluate_factors
    entered = threading.Event()
    release = threading.Event()

    def blocked(
        current_factor_set: InstantiatedFactorSet,
        current_selection: ActiveFactorSelection,
        current_parameters: ParameterVector,
    ) -> FactorEvaluation:
        entered.set()
        assert release.wait(timeout=5)
        return original(current_factor_set, current_selection, current_parameters)

    class ConcurrentAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            monkeypatch.setattr(execution_module, "evaluate_factors", blocked)
            thread = threading.Thread(
                target=lambda: callback(invocation.initial_values), daemon=True
            )
            thread.start()
            assert entered.wait(timeout=5)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            release.set()
            thread.join(timeout=5)
            assert not thread.is_alive()
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, ConcurrentAdapter())
    monkeypatch.undo()
    assert result.callback_count == request.callback_limit == 2
    assert tuple(entry.sequence_id for entry in result.callback_trace) == (0, 1)
    assert result.failures == (
        "callback-limit-exceeded",
        "callback-reentrant",
    )
    assert replay_execution(result, request, factor_set, selection) == result


@pytest.mark.parametrize("late_rejection", [False, True])
def test_adapter_return_with_inflight_callback_fails_closed(
    monkeypatch: pytest.MonkeyPatch, late_rejection: bool
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    original = evaluate_factors
    entered = threading.Event()
    release = threading.Event()
    threads: list[threading.Thread] = []
    outcomes: list[str] = []

    def blocked(
        current_factor_set: InstantiatedFactorSet,
        current_selection: ActiveFactorSelection,
        current_parameters: ParameterVector,
    ) -> FactorEvaluation:
        entered.set()
        assert release.wait(timeout=5)
        if late_rejection:
            raise ScansorError("late evaluation rejection")
        return original(current_factor_set, current_selection, current_parameters)

    def invoke(callback: ResidualJacobianCallback, values: object) -> None:
        try:
            _ = callback(values)
        except ScansorError as error:
            outcomes.append(str(error))
        else:
            outcomes.append("success")

    class EarlyReturnAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            monkeypatch.setattr(execution_module, "evaluate_factors", blocked)
            thread = threading.Thread(
                target=invoke,
                args=(callback, invocation.initial_values),
                daemon=True,
            )
            threads.append(thread)
            thread.start()
            assert entered.wait(timeout=5)
            with pytest.raises(ScansorError):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, EarlyReturnAdapter())
    assert result.disposition == "execution-failed"
    assert result.failures == ("callback-reentrant", "callback-incomplete")
    assert result.callback_count == 0
    assert result.callback_trace == ()
    assert result.callback_consistency_evidence is not None
    assert [
        (item.sequence_id, item.code)
        for item in result.callback_consistency_evidence.observations
    ] == [(0, "callback-incomplete"), (1, "callback-reentrant")]
    sealed = execution_result_bytes(result)
    release.set()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()
    assert outcomes == ["callback-incomplete"]
    assert execution_result_bytes(result) == sealed
    monkeypatch.undo()
    assert replay_execution(result, request, factor_set, selection) == result


def test_close_discards_failure_facts_after_first_pending_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    guarded_type = cast(type[object], vars(execution_module)["_GuardedCallback"])
    original_reserve = cast(Callable[[object], int], vars(guarded_type)["_reserve"])
    first_reserved = threading.Event()
    release_first = threading.Event()
    threads: list[threading.Thread] = []
    outcomes: list[str] = []

    def reserve_inverted(callback: object) -> int:
        sequence = original_reserve(callback)
        if sequence == 0:
            first_reserved.set()
            assert release_first.wait(timeout=5)
        return sequence

    def invoke(callback: ResidualJacobianCallback, values: object) -> None:
        try:
            _ = callback(values)
        except ScansorError as error:
            outcomes.append(str(error))

    class GapAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            monkeypatch.setattr(
                guarded_type,
                "_reserve",
                reserve_inverted,
            )
            thread = threading.Thread(
                target=invoke,
                args=(callback, invocation.initial_values),
                daemon=True,
            )
            threads.append(thread)
            thread.start()
            assert first_reserved.wait(timeout=5)
            with pytest.raises(ScansorError):
                _ = callback((0.0,))
            return backend_response(invocation, NOMINAL_SHAPE, "converged")

    result = execute(request, factor_set, selection, GapAdapter())
    assert result.failures == ("callback-incomplete",)
    assert result.callback_trace == ()
    assert result.callback_consistency_evidence is not None
    assert [
        (item.sequence_id, item.code)
        for item in result.callback_consistency_evidence.observations
    ] == [(0, "callback-incomplete")]
    release_first.set()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()
    assert outcomes == ["callback-incomplete"]
    monkeypatch.undo()
    assert replay_execution(result, request, factor_set, selection) == result


def test_adapter_exception_is_sanitized_and_replayable() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, RaisingAdapter())
    assert result.disposition == "execution-failed"
    assert result.failures == ("adapter-raised",)
    assert result.normalized_termination.category == "adapter-raised"
    assert b"secret" not in execution_result_bytes(result)
    assert replay_execution(result, request, factor_set, selection) == result


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        (invalid_response_schema, "response-schema-invalid"),
        (invalid_response_id, "response-content-id-invalid"),
        (invalid_response_provenance, "response-provenance-invalid"),
        (invalid_response_vector, "response-vector-invalid"),
    ],
)
def test_untrusted_response_schema_ids_provenance_and_dimensions(
    mutation: Callable[[BackendResponse, AdapterInvocation], object], failure: str
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, MaliciousAdapter(mutation))
    assert result.disposition == "invalid-backend-output"
    assert result.failures == (failure,)
    assert replay_execution(result, request, factor_set, selection) == result


def test_backend_response_revalidates_stale_invocation() -> None:
    _mapping, factor_set, selection, request = execution_case()
    adapter = ReturningAdapter(NOMINAL_SHAPE)
    _ = execute(request, factor_set, selection, adapter)
    assert adapter.invocation is not None
    stale = adapter.invocation.model_copy(
        update={"request_id": "execution-request." + "0" * 64}
    )
    with pytest.raises(ScansorError, match="invalid adapter invocation"):
        _ = backend_response(stale, NOMINAL_SHAPE, "converged")


def test_result_model_rejects_reidentified_invocation_graph_tampering() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, ReturningAdapter(NOMINAL_SHAPE))
    assert result.invocation is not None
    assert result.raw_response is not None
    changed_lower = (request.lower_bounds[0] - 0.001, *request.lower_bounds[1:])
    invocation = reidentified_invocation(result.invocation, lower_bounds=changed_lower)
    response_values = cast(
        dict[str, Any],
        result.raw_response.model_dump(mode="python", exclude={"response_id"})
        | {"invocation_id": invocation.invocation_id},
    )
    provisional_response = BackendResponse.model_construct(
        response_id="", **response_values
    )
    response_values["response_id"] = execution_models_module.execution_content_id(
        "backend-response", provisional_response, "response_id"
    )
    response = BackendResponse(**response_values)
    with pytest.raises(ValidationError, match="invocation disagrees"):
        _ = reidentified_result(
            result,
            invocation=invocation,
            raw_response=response,
        )


def test_response_container_subclass_is_rejected_without_iteration() -> None:
    _mapping, factor_set, selection, request = execution_case()

    class HostileList(list[float]):
        @override
        def __iter__(self):
            raise RuntimeError("must not iterate")

    class HostileResponseAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback
            response = backend_response(invocation, NOMINAL_SHAPE, "converged")
            values = response.model_dump(mode="python")
            values["final_values"] = HostileList(NOMINAL_SHAPE)
            return values

    result = execute(request, factor_set, selection, HostileResponseAdapter())
    assert result.disposition == "invalid-backend-output"
    assert result.failures == ("response-schema-invalid",)
    assert replay_execution(result, request, factor_set, selection) == result
    forged = reidentified_result(
        result,
        normalized_termination=NormalizedTermination(
            category="invalid-response", raw_code="forged"
        ),
    )
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged, request, factor_set, selection)


def test_backend_claims_do_not_supply_objective_or_acceptance() -> None:
    _mapping, factor_set, selection, request = execution_case()
    adapter = ReturningAdapter(NOMINAL_SHAPE, "failure")
    result = execute(request, factor_set, selection, adapter)
    assert result.disposition == "completed-not-assessed"
    assert result.normalized_termination.category == "backend-reported-failure"
    assert result.final_objective == 0.0
    assert "accept" not in execution_result_bytes(result).decode("ascii").lower()


def test_final_vector_is_independent_of_last_callback_and_bounds_are_exact() -> None:
    _mapping, factor_set, selection, request = execution_case()

    class IndependentFinalAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback(invocation.initial_values)
            final = list(NOMINAL_SHAPE)
            final[0] = invocation.lower_bounds[0]
            final[1] = invocation.upper_bounds[1]
            return backend_response(invocation, tuple(final), "stopped")

    result = execute(request, factor_set, selection, IndependentFinalAdapter())
    assert result.final_parameters is not None
    assert result.final_parameters.values != result.callback_trace[-1].values
    assert result.bound_activity is not None
    assert result.bound_activity.activity[:2] == ("lower", "upper")
    assert result.normalized_termination.category == "backend-stopped"


def test_canonical_result_round_trip_limits_and_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(
        request,
        factor_set,
        selection,
        ReturningAdapter(NOMINAL_SHAPE, descriptor=request.adapter),
    )
    data = execution_result_bytes(result)
    assert parse_execution_result(data) == result
    with pytest.raises(ScansorError, match="canonical"):
        _ = parse_execution_result(data.replace(b"  ", b" ", 1))
    with pytest.raises(ScansorError, match="duplicate"):
        _ = parse_execution_result(b'{"x":1,"x":2}\n')
    monkeypatch.setattr(execution_module, "MAX_EXECUTION_RESULT_BYTES", 1)
    with pytest.raises(ScansorError, match="byte limit"):
        _ = execution_result_bytes(result)
    stale = result.model_copy(update={"result_id": "execution-result." + "0" * 64})
    with pytest.raises(ScansorError, match="invalid execution result"):
        _ = replay_execution(stale, request, factor_set, selection)


def test_replay_rejects_self_consistent_forged_disposition_facts() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(
        request,
        factor_set,
        selection,
        ReturningAdapter(NOMINAL_SHAPE, descriptor=request.adapter),
    )
    assert result.raw_response is not None

    out_values = (0.009, *NOMINAL_SHAPE[1:])
    out_parameters = ParameterVector(
        problem=request.problem,
        units=request.initial_parameters.units,
        values=out_values,
        variant=request.variant,
    )
    out_evaluation = evaluate_factors(factor_set, selection, out_parameters)
    response_values = cast(
        dict[str, Any],
        result.raw_response.model_dump(mode="python", exclude={"response_id"})
        | {"final_values": out_values},
    )
    provisional_response = BackendResponse.model_construct(
        response_id="", **response_values
    )
    response_values["response_id"] = execution_models_module.execution_content_id(
        "backend-response", provisional_response, "response_id"
    )
    out_response = BackendResponse(**response_values)
    forged_out = reidentified_result(
        result,
        raw_response=out_response,
        final_parameters=out_parameters,
        final_evaluation=out_evaluation,
        final_objective=0.5
        * math.fsum(value * value for value in out_evaluation.raw_residuals_m),
    )
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged_out, request, factor_set, selection)

    common = cast(
        dict[str, Any],
        {
            "bound_activity": None,
            "final_evaluation": None,
            "final_objective": None,
            "final_parameters": None,
            "normalized_termination": NormalizedTermination(
                category="invalid-response"
            ),
        },
    )
    forged_vector = reidentified_result(
        result,
        **common,
        disposition="invalid-backend-output",
        failures=("response-vector-invalid",),
    )
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged_vector, request, factor_set, selection)

    forged_schema = reidentified_result(
        result,
        **common,
        raw_response=None,
        disposition="invalid-backend-output",
        failures=("response-schema-invalid",),
    )
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged_schema, request, factor_set, selection)

    forged_final = reidentified_result(
        result,
        **common,
        disposition="execution-failed",
        failures=("final-evaluation-undefined",),
    )
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged_final, request, factor_set, selection)

    evidence_values = cast(
        dict[str, Any],
        {
            "observations": (
                CallbackConsistencyObservation(
                    code="callback-reentrant", sequence_id=0
                ),
            ),
            "retained_trace_byte_count": 0,
            "trace_byte_limit": request.callback_trace_byte_limit,
        },
    )
    provisional_evidence = CallbackConsistencyEvidence.model_construct(
        evidence_id="", **evidence_values
    )
    evidence = CallbackConsistencyEvidence(
        evidence_id=execution_models_module.execution_content_id(
            "callback-consistency", provisional_evidence, "evidence_id"
        ),
        **evidence_values,
    )
    with pytest.raises(ValidationError, match="observation is impossible"):
        _ = reidentified_result(
            result,
            **common,
            raw_response=None,
            callback_consistency_evidence=evidence,
            callback_count=0,
            callback_trace=(),
            callback_trace_byte_count=0,
            disposition="execution-failed",
            failures=("callback-reentrant",),
        )


def test_request_rejects_self_consistent_unknown_factor_selection() -> None:
    _mapping, factor_set, _selection = factor_case()
    values = cast(
        dict[str, Any],
        {
            "active_factor_ids": ("factor." + "0" * 64,),
            "factor_set_id": factor_set.factor_set_id,
        },
    )
    provisional = ActiveFactorSelection.model_construct(selection_id="", **values)
    forged = ActiveFactorSelection(
        selection_id=content_id("selection", provisional, "selection_id"),
        **values,
    )
    with pytest.raises(ScansorError, match="unknown active factor"):
        _ = create_execution_request(
            factor_set,
            forged,
            parameters("asymmetric-datum-flat"),
            adapter_descriptor("tests.selection", "1"),
        )


def mapping_with_held_out_cases() -> tuple[MappingResult, bytes]:
    training = fixture_points()
    held_out = [
        training[0],
        (0.012, 0.0, 0.0000005),
        (0.013, 0.0, 0.010),
        (1.0, 1.0, 1.0),
        (0.0121, 0.0, 0.0199),
    ]
    points = [*training, *held_out]
    canonical = canonical_bytes(points)
    indices = tuple(range(len(training), len(points)))
    request = request_for(
        canonical,
        held_out=indices,
        thresholds=MappingThresholds(transition_guard_m=1e-6),
    )
    return build_mapping(request, canonical), canonical


def completed_for_mapping(
    mapping: MappingResult,
) -> tuple[
    InstantiatedFactorSet,
    ActiveFactorSelection,
    ExecutionRequest,
    ExecutionResult,
]:
    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(
        factor_set, tuple(item.factor_id for item in factor_set.factors)
    )
    request = create_execution_request(
        factor_set,
        selection,
        parameters(mapping.request.variant),
        adapter_descriptor("tests.held-out", "1"),
    )
    result = execute(
        request,
        factor_set,
        selection,
        ReturningAdapter(NOMINAL_SHAPE, descriptor=request.adapter),
    )
    return factor_set, selection, request, result


def test_post_fit_held_out_assignment_exclusions_and_sealed_result() -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    assert mapping.disposition == "accepted"
    factor_set, selection, request, result = completed_for_mapping(mapping)
    before = execution_result_bytes(result)
    assessment = assess_held_out(result, request, factor_set, selection, mapping)
    assert [row.outcome for row in assessment.rows] == [
        "assigned",
        "transition",
        "outlier",
        "gap",
        "ambiguous",
    ]
    assert assessment.summary.count == 1
    assert assessment.rows[0].raw_residual_m == 0.0
    assert execution_result_bytes(result) == before
    assert assessment.execution_result_id == result.result_id
    encoded = canonical_json(assessment).decode("ascii").lower()
    for prohibited in ("threshold_pass", "acceptance", "weight", "normal"):
        assert prohibited not in encoded


def test_held_out_summary_validation_rejects_reidentified_tampering() -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    factor_set, selection, request, result = completed_for_mapping(mapping)
    assessment = assess_held_out(result, request, factor_set, selection, mapping)
    bad_summaries = (
        assessment.summary.model_copy(update={"count": 2}),
        assessment.summary.model_copy(update={"minimum_raw_residual_m": 1.0}),
        assessment.summary.model_copy(update={"maximum_raw_residual_m": 1.0}),
        assessment.summary.model_copy(update={"mean_raw_residual_m": 1.0}),
        assessment.summary.model_copy(update={"root_mean_square_raw_residual_m": 1.0}),
    )
    for summary in bad_summaries:
        with pytest.raises(ValidationError, match="summary disagrees"):
            _ = reidentified_assessment(assessment, summary=summary)

    rows = tuple(row for row in assessment.rows if row.outcome != "assigned")
    counts = {
        outcome: sum(row.outcome == outcome for row in rows)
        for outcome in assessment.counts
    }
    empty_summary = HeldOutSummary(
        count=0,
        maximum_raw_residual_m=None,
        mean_raw_residual_m=None,
        minimum_raw_residual_m=None,
        root_mean_square_raw_residual_m=None,
    )
    no_assigned = reidentified_assessment(
        assessment,
        counts=counts,
        rows=rows,
        summary=empty_summary,
    )
    assert no_assigned.summary == empty_summary
    with pytest.raises(ValidationError, match="summary disagrees"):
        _ = reidentified_assessment(
            no_assigned,
            summary=empty_summary.model_copy(
                update={"count": 1, "mean_raw_residual_m": 0.0}
            ),
        )


def test_held_out_evaluation_error_is_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    factor_set, selection, request, result = completed_for_mapping(mapping)

    def fail_evaluation(*_args: object, **_kwargs: object) -> float:
        raise ScansorError("undefined")

    monkeypatch.setattr(execution_module, "evaluate_support_residual", fail_evaluation)
    assessment = assess_held_out(result, request, factor_set, selection, mapping)
    assert assessment.rows[0].outcome == "evaluation-error"
    assert assessment.rows[0].raw_residual_m is None


def test_held_out_nonfinite_residual_is_an_evaluation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    factor_set, selection, request, result = completed_for_mapping(mapping)

    def nonfinite_residual(*_args: object, **_kwargs: object) -> float:
        return math.nan

    monkeypatch.setattr(
        execution_module, "evaluate_support_residual", nonfinite_residual
    )
    assessment = assess_held_out(result, request, factor_set, selection, mapping)
    assert assessment.rows[0].outcome == "evaluation-error"
    assert assessment.rows[0].raw_residual_m is None


def test_held_out_wrong_provenance_and_noncompleted_result_are_rejected() -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    factor_set, selection, request, result = completed_for_mapping(mapping)
    other_mapping, _other_set, _other_selection = factor_case("axisymmetric")
    with pytest.raises(ScansorError, match="reconstruct"):
        _ = assess_held_out(result, request, factor_set, selection, other_mapping)

    ineligible_selection = select_active_factors(factor_set, ())
    ineligible_request = create_execution_request(
        factor_set,
        ineligible_selection,
        parameters("asymmetric-datum-flat"),
        request.adapter,
    )
    ineligible = execute(
        ineligible_request,
        factor_set,
        ineligible_selection,
        ReturningAdapter(NOMINAL_SHAPE, descriptor=ineligible_request.adapter),
    )
    with pytest.raises(ScansorError, match="completed"):
        _ = assess_held_out(
            ineligible,
            ineligible_request,
            factor_set,
            ineligible_selection,
            mapping,
        )


def test_held_out_rejects_self_consistent_but_replay_invalid_result() -> None:
    mapping, _canonical = mapping_with_held_out_cases()
    factor_set, selection, request, result = completed_for_mapping(mapping)
    changed = ParameterVector(
        problem=request.problem,
        units=request.initial_parameters.units,
        values=(request.lower_bounds[0], *NOMINAL_SHAPE[1:]),
        variant=request.variant,
    )
    forged = reidentified_result(result, final_parameters=changed)
    with pytest.raises(ScansorError, match="disposition facts"):
        _ = assess_held_out(forged, request, factor_set, selection, mapping)


def test_nominal_support_helper_preserves_mapping_bytes_and_ignores_normals() -> None:
    points = [*fixture_points(), fixture_points()[0]]
    plain = canonical_bytes(points)
    normal = canonical_bytes(points, normals=True, normal_value=7.0)
    plain_mapping = build_mapping(
        request_for(plain, held_out=(len(points) - 1,)), plain
    )
    normal_mapping = build_mapping(
        request_for(normal, held_out=(len(points) - 1,)), normal
    )
    support = assess_nominal_support(
        plain_mapping.held_out_observations[0].point_model_m,
        plain_mapping.request.variant,
        plain_mapping.request.thresholds,
    )
    assert support.outcome == "assigned"
    assert support.candidates[0].element_id == "cylinder.band-1"
    assert [item.element_id for item in plain_mapping.mappings] == [
        item.element_id for item in normal_mapping.mappings
    ]
    assert (
        sha256(
            canonical_json(
                plain_mapping.model_dump(mode="json", exclude={"mapping_run_id"})
            )
        )
        == plain_mapping.mapping_run_id
    )
    assert sha256(canonical_json(plain_mapping)) == (
        "28bd7083f8df51ee8599b7f9168a2a1331491e1b499bbfbb87b60fc216354a7c"
    )
    before = canonical_json(plain_mapping)
    _ = assess_nominal_support(
        plain_mapping.held_out_observations[0].point_model_m,
        plain_mapping.request.variant,
        plain_mapping.request.thresholds,
    )
    assert canonical_json(plain_mapping) == before


def test_execution_modules_have_pure_import_boundaries() -> None:
    for module in (execution_models_module, execution_module):
        assert module.__file__ is not None
        source = Path(module.__file__).read_text(encoding="ascii").lower()
        for prohibited in (
            "experiments",
            "onshape",
            "scansor.cli",
            "scansor.mapping_runs",
            "scipy",
            "optimizer",
            "pathlib",
            "filesystem",
        ):
            assert prohibited not in source


def test_execution_models_reject_semantic_tampering() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, ReturningAdapter(NOMINAL_SHAPE))
    record = result.model_dump(mode="json")
    record["callback_count"] = 999
    with pytest.raises(ValidationError, match="callback trace"):
        _ = ExecutionResult.model_validate(record)
    response = result.raw_response
    assert response is not None
    response_record = response.model_dump(mode="json")
    response_record["final_values"][0] += 0.001
    with pytest.raises(ValidationError, match="response ID"):
        _ = BackendResponse.model_validate(response_record)
