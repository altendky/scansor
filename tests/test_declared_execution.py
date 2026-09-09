from __future__ import annotations

import copy
import math
import threading
from pathlib import Path
from typing import Any, Literal, cast

import pytest
from pydantic import ValidationError

import scansor.declared_execution as execution_module
import scansor.declared_execution_models as execution_models_module
from scansor.declared_execution import (
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
from scansor.declared_execution_models import (
    AdapterDescriptor,
    AdapterInvocation,
    BackendResponse,
    CallbackTraceEntry,
    ExecutionRequest,
    ExecutionResult,
    HeldOutSupportCandidate,
    execution_content_id,
    summarize_held_out,
)
from scansor.declared_factor_models import (
    ActiveFactorSelection,
    FactorEvaluation,
    InstantiatedFactorSet,
    ParameterVector,
)
from scansor.declared_factors import (
    evaluate_factors,
    instantiate_factors,
    select_active_factors,
)
from scansor.errors import ScansorError
from scansor.mapping_models import MappingResult
from scansor.model_declarations import (
    ModelDeclaration,
    ModelSemanticDeclaration,
    identify_model,
)
from scansor.observation_mapping import build_mapping
from scansor.serialization import canonical_json
from scansor.stepped_model_declarations import stepped_model_declaration

# Reuse the declared-factor tests' internal construction fixtures.
from tests.test_declared_factors import (
    _factor_case,  # pyright: ignore[reportPrivateUsage]
    _parameters,  # pyright: ignore[reportPrivateUsage]
)
from tests.test_mapping import (
    canonical_bytes,
    constructed_shell_declaration,
    fixture_points,
    request_for,
)

TEST_DESCRIPTOR = adapter_descriptor("tests.declared-recording", "1")
BackendTermination = Literal["converged", "limit", "stopped", "failure", "unknown"]


class ReturningAdapter:
    def __init__(
        self,
        values: tuple[float, ...],
        *,
        descriptor: AdapterDescriptor = TEST_DESCRIPTOR,
        callback_values: object | None = None,
        termination: BackendTermination = "converged",
    ) -> None:
        self.descriptor: AdapterDescriptor = descriptor
        self.values: tuple[float, ...] = values
        self.callback_values: object | None = callback_values
        self.termination: BackendTermination = termination
        self.calls: int = 0
        self.invocation: AdapterInvocation | None = None

    def execute(
        self,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        self.calls += 1
        self.invocation = invocation
        if self.callback_values is not None:
            _ = callback(self.callback_values)
        return backend_response(
            invocation,
            self.values,
            self.termination,
            raw_code="test-only",
        )


class CallbackAdapter:
    def __init__(
        self, value: object, *, descriptor: AdapterDescriptor = TEST_DESCRIPTOR
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


def execution_case(
    declaration: ModelDeclaration | None = None,
    *,
    points: list[tuple[float, float, float]] | None = None,
    initial: tuple[float, ...] | None = None,
    adapter: AdapterDescriptor = TEST_DESCRIPTOR,
    callback_limit: int = 10_000,
    callback_trace_byte_limit: int = 16 * 1024 * 1024,
) -> tuple[
    MappingResult,
    InstantiatedFactorSet,
    ActiveFactorSelection,
    ExecutionRequest,
]:
    declaration = declaration or constructed_shell_declaration()
    points = points or [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    mapping, factor_set, selection = _factor_case(declaration, points)
    request = create_execution_request(
        factor_set,
        selection,
        _parameters(declaration, initial),
        adapter,
        callback_limit=callback_limit,
        callback_trace_byte_limit=callback_trace_byte_limit,
    )
    return mapping, factor_set, selection, request


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
    values["result_id"] = execution_content_id(
        "execution-result", provisional, "result_id"
    )
    return ExecutionResult(**values)


def rehashed_request(request: ExecutionRequest, **updates: object) -> ExecutionRequest:
    changed = request.model_copy(update=updates)
    return changed.model_copy(
        update={
            "request_id": execution_content_id(
                "execution-request", changed, "request_id"
            )
        }
    )


def declaration_with_rank_scale(scale: float) -> ModelDeclaration:
    record = copy.deepcopy(
        constructed_shell_declaration().model_dump(mode="python", exclude={"model_id"})
    )
    record["optimization_preflight"]["relative_rank"]["parameter_scales"] = (scale,)
    record["optimization_preflight"]["relative_rank"]["residual_scale"] = 0.009
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def declaration_with_permissive_lower_bound() -> ModelDeclaration:
    record = copy.deepcopy(
        constructed_shell_declaration().model_dump(mode="python", exclude={"model_id"})
    )
    record["parameters"][0]["lower"] = -0.01
    return identify_model(ModelSemanticDeclaration.model_validate(record))


def test_request_and_invocation_copy_exact_declaration_policy_without_topology_fields() -> (
    None
):
    declaration = declaration_with_rank_scale(0.007)
    _mapping, factor_set, selection, request = execution_case(declaration)
    adapter = ReturningAdapter((0.02,))
    result = execute(request, factor_set, selection, adapter)

    assert request.declaration == declaration
    assert request.model_id == declaration.model_id
    assert request.problem == "fixed-pose-shape"
    assert request.parameter_order == ("shell-radius",)
    assert request.lower_bounds == (0.01,)
    assert request.upper_bounds == (0.03,)
    assert request.parameter_scales == (0.002,)
    assert (
        request.declaration.optimization_preflight.relative_rank.parameter_scales
        == (0.007,)
    )
    assert (
        request.declaration.optimization_preflight.relative_rank.residual_scale == 0.009
    )
    assert request.callback_protocol_revision == "declared-analytic-model-callback-v1"
    assert request.adapter.protocol_revision == (
        "declared-analytic-model-backend-adapter-v1"
    )
    assert adapter.invocation is not None
    assert adapter.invocation.declaration == declaration
    assert adapter.invocation.model_id == declaration.model_id
    assert adapter.invocation.parameter_scales == (0.002,)
    for field in ("contract_id", "variant"):
        assert field not in ExecutionRequest.model_fields
        assert field not in AdapterInvocation.model_fields
    assert "kind" not in HeldOutSupportCandidate.model_fields
    assert result.model_id == declaration.model_id


@pytest.mark.parametrize(
    ("declaration", "points"),
    [
        (
            constructed_shell_declaration(),
            [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)],
        ),
        (
            stepped_model_declaration("axisymmetric"),
            fixture_points(asymmetric=False),
        ),
        (
            stepped_model_declaration("asymmetric-datum-flat"),
            fixture_points(asymmetric=True),
        ),
    ],
)
def test_multiple_declared_dimensions_complete_and_replay(
    declaration: ModelDeclaration, points: list[tuple[float, float, float]]
) -> None:
    initial = tuple(parameter.nominal for parameter in declaration.parameters)
    _mapping, factor_set, selection, request = execution_case(
        declaration, points=points, initial=initial
    )
    adapter = ReturningAdapter(initial, callback_values=initial)

    result = execute(request, factor_set, selection, adapter)

    assert result.disposition == "completed-not-assessed"
    assert result.final_parameters == request.initial_parameters
    assert result.final_evaluation == result.initial_evaluation
    assert result.final_objective == 0.0
    assert result.callback_count == 1
    assert result.callback_trace[0].model_id == declaration.model_id
    assert result.raw_response is not None
    assert result.raw_response.model_id == declaration.model_id
    assert replay_execution(result, request, factor_set, selection) == result


def test_final_vector_is_independently_recomputed_and_not_taken_from_callback() -> None:
    _mapping, factor_set, selection, request = execution_case(initial=(0.021,))
    adapter = ReturningAdapter(
        (0.02,),
        callback_values=request.initial_parameters.values,
        termination="stopped",
    )

    result = execute(request, factor_set, selection, adapter)

    assert result.disposition == "completed-not-assessed"
    assert result.final_parameters is not None
    assert result.final_parameters.values == (0.02,)
    assert result.callback_trace[0].values == (0.021,)
    assert result.final_evaluation is not None
    assert result.final_evaluation.raw_residuals_m == (0.0, 0.0, 0.0)
    assert result.final_objective == 0.0
    assert result.normalized_termination.category == "backend-stopped"
    assert replay_execution(result, request, factor_set, selection) == result


def test_rehashed_policy_and_declaration_tampering_fail_before_adapter() -> None:
    _mapping, factor_set, selection, request = execution_case()
    adapter = ReturningAdapter((0.02,))
    stale_policy = rehashed_request(request, lower_bounds=(0.0,))
    with pytest.raises(ScansorError, match="invalid execution request"):
        _ = execute(stale_policy, factor_set, selection, adapter)
    assert adapter.calls == 0

    stale_declaration = request.declaration.model_copy(
        update={
            "frame": request.declaration.frame.model_copy(
                update={"frame_id": "tampered-frame"}
            )
        }
    )
    stale_model = rehashed_request(request, declaration=stale_declaration)
    with pytest.raises(ScansorError, match="invalid execution request"):
        _ = execute(stale_model, factor_set, selection, adapter)
    assert adapter.calls == 0


def test_valid_cross_model_substitution_fails_exact_graph_rebuild() -> None:
    _mapping, factor_set, selection, request = execution_case()
    replacement = declaration_with_rank_scale(0.006)
    replacement_values = tuple(
        parameter.nominal for parameter in replacement.parameters
    )
    substituted = rehashed_request(
        request,
        declaration=replacement,
        initial_parameters=ParameterVector(
            model_id=replacement.model_id, values=replacement_values
        ),
        model_id=replacement.model_id,
        parameter_order=tuple(
            parameter.parameter_id for parameter in replacement.parameters
        ),
        lower_bounds=tuple(parameter.lower for parameter in replacement.parameters),
        upper_bounds=tuple(parameter.upper for parameter in replacement.parameters),
        parameter_scales=tuple(
            parameter.diagnostic_scale for parameter in replacement.parameters
        ),
    )
    adapter = ReturningAdapter((0.02,))

    with pytest.raises(ScansorError, match=r"another model|does not match"):
        _ = execute(substituted, factor_set, selection, adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("value", "failure"),
    [
        ((), "callback-dimension-invalid"),
        ((math.nan,), "callback-nonfinite"),
        ((1.0,), "callback-out-of-bounds"),
        ((object(),), "callback-nonfinite"),
    ],
)
def test_callback_input_rejections_are_finite_safe_model_bound_and_replayable(
    value: object, failure: str
) -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, CallbackAdapter(value))

    assert result.disposition == "execution-failed"
    assert result.failures == (failure,)
    assert result.callback_trace[0].model_id == request.model_id
    if failure in {"callback-dimension-invalid", "callback-nonfinite"}:
        assert result.callback_trace[0].values == ()
    if failure == "callback-nonfinite":
        assert result.callback_trace[0].nonfinite_positions == (0,)
        assert b"nan" not in execution_result_bytes(result).lower()
    assert replay_execution(result, request, factor_set, selection) == result


def test_runtime_structure_validation_is_separate_from_bounds() -> None:
    declaration = declaration_with_permissive_lower_bound()
    _mapping, factor_set, selection, request = execution_case(declaration)
    result = execute(request, factor_set, selection, CallbackAdapter((0.0,)))

    assert request.lower_bounds == (-0.01,)
    assert result.failures == ("callback-structure-invalid",)
    assert replay_execution(result, request, factor_set, selection) == result


def test_callback_limit_records_evidence_without_an_unbounded_trace() -> None:
    _mapping, factor_set, selection, request = execution_case(callback_limit=1)

    class LimitAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback(invocation.initial_values)
            with pytest.raises(ScansorError, match="callback-limit-exceeded"):
                _ = callback(invocation.initial_values)
            return backend_response(invocation, invocation.initial_values, "limit")

    result = execute(request, factor_set, selection, LimitAdapter())

    assert result.callback_count == 1
    assert result.failures == ("callback-limit-exceeded",)
    assert result.callback_consistency_evidence is not None
    assert result.callback_consistency_evidence.model_id == request.model_id
    assert [
        (item.sequence_id, item.code)
        for item in result.callback_consistency_evidence.observations
    ] == [(1, "callback-limit-exceeded")]
    assert replay_execution(result, request, factor_set, selection) == result


@pytest.mark.parametrize("callback_limit", [1, 2])
def test_reentrancy_records_both_interruption_sides_and_replays(
    monkeypatch: pytest.MonkeyPatch,
    callback_limit: int,
) -> None:
    _mapping, factor_set, selection, request = execution_case(
        callback_limit=callback_limit
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
            _ = invocation
            active_callback = callback
            monkeypatch.setattr(execution_module, "evaluate_factors", recurse)
            return callback(request.initial_parameters.values)

    result = execute(request, factor_set, selection, ReentrantAdapter())
    monkeypatch.undo()

    cause = "callback-limit-exceeded" if callback_limit == 1 else "callback-reentrant"
    assert result.failures == (cause, "callback-evaluation-undefined")
    assert [entry.failure_code for entry in result.callback_trace] == (
        ["callback-evaluation-undefined"]
        + ([] if callback_limit == 1 else ["callback-reentrant"])
    )
    assert result.callback_consistency_evidence is not None
    assert [
        (item.sequence_id, item.code, item.related_sequence_id)
        for item in result.callback_consistency_evidence.observations
    ] == [
        (0, "callback-evaluation-undefined", 1),
        (1, cause, None),
    ]
    assert replay_execution(result, request, factor_set, selection) == result


def test_return_with_inflight_callback_is_sealed_as_incomplete(
    monkeypatch: pytest.MonkeyPatch,
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
            return backend_response(invocation, invocation.initial_values, "converged")

    result = execute(request, factor_set, selection, EarlyReturnAdapter())
    sealed = execution_result_bytes(result)

    assert result.disposition == "execution-failed"
    assert result.failures == ("callback-incomplete",)
    assert result.callback_trace == ()
    assert result.callback_consistency_evidence is not None
    assert result.callback_consistency_evidence.observations[0].code == (
        "callback-incomplete"
    )
    release.set()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()
    assert outcomes == ["callback-incomplete"]
    assert execution_result_bytes(result) == sealed
    monkeypatch.undo()
    assert replay_execution(result, request, factor_set, selection) == result


def test_trace_byte_budget_fails_closed_with_consistency_evidence() -> None:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    points = fixture_points(asymmetric=True)
    initial = tuple(parameter.nominal for parameter in declaration.parameters)
    _mapping, factor_set, selection, base = execution_case(
        declaration, points=points, initial=initial
    )
    baseline = execute(
        base,
        factor_set,
        selection,
        ReturningAdapter(initial, callback_values=initial),
    )
    assert baseline.callback_trace_byte_count > 1_024
    request = create_execution_request(
        factor_set,
        selection,
        base.initial_parameters,
        base.adapter,
        callback_trace_byte_limit=baseline.callback_trace_byte_count - 1,
    )

    result = execute(
        request,
        factor_set,
        selection,
        CallbackAdapter(initial, descriptor=request.adapter),
    )

    assert result.callback_trace == ()
    assert result.callback_trace_byte_count == 0
    assert result.failures == ("callback-trace-budget-exceeded",)
    assert result.callback_consistency_evidence is not None
    assert replay_execution(result, request, factor_set, selection) == result


def test_untrusted_response_dimension_and_model_provenance_fail_closed() -> None:
    _mapping, factor_set, selection, request = execution_case()

    class WrongDimensionAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback
            return backend_response(invocation, (), "converged")

    dimension = execute(request, factor_set, selection, WrongDimensionAdapter())
    assert dimension.disposition == "invalid-backend-output"
    assert dimension.failures == ("response-vector-invalid",)
    assert dimension.external_failure_evidence is not None
    assert dimension.external_failure_evidence.model_id == request.model_id
    assert replay_execution(dimension, request, factor_set, selection) == dimension

    class WrongModelAdapter:
        descriptor: AdapterDescriptor = request.adapter

        def execute(
            self,
            invocation: AdapterInvocation,
            callback: ResidualJacobianCallback,
        ) -> object:
            _ = callback
            response = backend_response(invocation, (0.02,), "converged")
            values = response.model_dump(mode="python", exclude={"response_id"})
            values["model_id"] = "model." + "0" * 64
            provisional = BackendResponse.model_construct(response_id="", **values)
            values["response_id"] = execution_content_id(
                "backend-response", provisional, "response_id"
            )
            return BackendResponse(**values)

    provenance = execute(request, factor_set, selection, WrongModelAdapter())
    assert provenance.failures == ("response-provenance-invalid",)
    assert replay_execution(provenance, request, factor_set, selection) == provenance


def test_replay_recomputes_objective_and_rejects_reidentified_tampering() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, ReturningAdapter((0.02,)))
    forged = reidentified_result(result, final_objective=1.0)

    with pytest.raises(ScansorError, match="disposition facts"):
        _ = replay_execution(forged, request, factor_set, selection)


def test_canonical_result_round_trip_and_semantic_ids() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(request, factor_set, selection, ReturningAdapter((0.02,)))
    encoded = execution_result_bytes(result)

    assert parse_execution_result(encoded) == result
    assert execution_content_id("execution-result", result, "result_id") == (
        result.result_id
    )
    with pytest.raises(ScansorError, match="canonical"):
        _ = parse_execution_result(encoded.replace(b":", b": ", 1))
    stale = result.model_copy(update={"result_id": "execution-result." + "0" * 64})
    with pytest.raises(ScansorError, match="invalid execution result"):
        _ = replay_execution(stale, request, factor_set, selection)


def test_held_out_uses_nominal_classification_and_final_assigned_residual_only() -> (
    None
):
    declaration = constructed_shell_declaration()
    training = [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
    held_out = [
        (0.02, 0.0, 0.0),
        (0.021, 0.0, 0.0),
        (0.02, 0.0, 2.0),
        (0.02, 0.0, 0.9999),
    ]
    points = [*training, *held_out]
    canonical = canonical_bytes(points)
    indices = tuple(range(len(training), len(points)))
    mapping = build_mapping(
        request_for(canonical, declaration=declaration, held_out=indices),
        canonical,
    )
    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(
        factor_set, tuple(factor.factor_id for factor in factor_set.factors)
    )
    request = create_execution_request(
        factor_set,
        selection,
        _parameters(declaration),
        TEST_DESCRIPTOR,
    )
    result = execute(request, factor_set, selection, ReturningAdapter((0.021,)))

    assessment = assess_held_out(result, request, factor_set, selection, mapping)

    assert [row.outcome for row in assessment.rows] == [
        "assigned",
        "outlier",
        "gap",
        "transition",
    ]
    assert assessment.rows[0].assigned_element_id == "shell"
    assert assessment.rows[0].raw_residual_m == pytest.approx(-0.001)
    assert all(row.raw_residual_m is None for row in assessment.rows[1:])
    assert assessment.summary.count == 1
    assert assessment.model_id == declaration.model_id
    assert all(row.model_id == declaration.model_id for row in assessment.rows)
    assert "kind" not in assessment.rows[0].candidates[0].model_dump()
    assert "accept" not in canonical_json(assessment).decode("ascii").lower()


def test_parameter_dimension_cap_is_declaration_driven_and_explicit() -> None:
    record = copy.deepcopy(
        constructed_shell_declaration().model_dump(mode="python", exclude={"model_id"})
    )
    additions = tuple(
        {
            "diagnostic_scale": 0.1,
            "lower": -1.0,
            "nominal": 0.0,
            "parameter_id": f"unused-{index}",
            "unit": "m",
            "upper": 1.0,
        }
        for index in range(64)
    )
    record["parameters"] += additions
    record["problem"]["varied_parameter_ids"] += tuple(
        item["parameter_id"] for item in additions
    )
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    _mapping, factor_set, selection = _factor_case(
        declaration,
        [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)],
    )

    with pytest.raises(ScansorError, match="exceeds 64"):
        _ = create_execution_request(
            factor_set,
            selection,
            _parameters(declaration),
            TEST_DESCRIPTOR,
        )


def test_declared_execution_modules_have_compatibility_free_import_boundaries() -> None:
    for module in (execution_models_module, execution_module):
        source = Path(module.__file__ or "").read_text(encoding="ascii").lower()
        for prohibited in (
            "from scansor.factor_models import",
            "from scansor.execution_models import",
            "stepped",
            "scipy",
            "optimizer",
            "mapping_runs",
            "pathlib",
            "filesystem",
        ):
            assert prohibited not in source


def test_callback_entry_rejects_a_reidentified_wrong_model() -> None:
    _mapping, factor_set, selection, request = execution_case()
    result = execute(
        request,
        factor_set,
        selection,
        ReturningAdapter((0.02,), callback_values=(0.02,)),
    )
    entry = result.callback_trace[0]
    values = cast(
        dict[str, Any],
        {
            name: getattr(entry, name)
            for name in type(entry).model_fields
            if name != "callback_entry_id"
        }
        | {"model_id": "model." + "0" * 64},
    )
    provisional = CallbackTraceEntry.model_construct(callback_entry_id="", **values)
    values["callback_entry_id"] = execution_content_id(
        "callback-entry", provisional, "callback_entry_id"
    )
    with pytest.raises(ValidationError, match="model disagree"):
        _ = CallbackTraceEntry(**values)


def large_plane_case(*, held_out_count: int = 0):
    record = constructed_shell_declaration().model_dump(
        mode="python", exclude={"model_id"}
    )
    record["parameters"][0].update(nominal=0.0, lower=-1.8e154, upper=1.8e154)
    record["elements"][0]["primitive"] = {
        "kind": "oriented-plane",
        "normal": (0.0, 0.0, 1.0),
        "offset": {"kind": "parameter", "parameter_id": "shell-radius"},
    }
    record["elements"][0]["domain"] = {
        "domain_id": "disk.domain",
        "predicates": (
            {
                "kind": "radial-interval",
                "predicate_id": "disk.radial",
                "lower": {"kind": "literal", "value": 0.0},
                "upper": {"kind": "literal", "value": 1.0},
            },
        ),
    }
    record["structural_predicates"] = ()
    for context in ("mapping_admission", "optimization_preflight"):
        record[context]["coverage_cells"] = ()
    declaration = identify_model(ModelSemanticDeclaration.model_validate(record))
    mapping, factor_set, selection = _factor_case(
        declaration,
        [(0.01, 0.0, 0.0)] * (3 + held_out_count),
        held_out=tuple(range(3, 3 + held_out_count)),
    )
    request = create_execution_request(
        factor_set, selection, _parameters(declaration), TEST_DESCRIPTOR
    )
    return mapping, factor_set, selection, request


def test_representable_extreme_objective_and_held_out_rms_complete_and_replay() -> None:
    mapping, factor_set, selection, request = large_plane_case(held_out_count=200)
    result = execute(request, factor_set, selection, ReturningAdapter((1e154,)))
    assert result.disposition == "completed-not-assessed"
    assert result.final_objective == pytest.approx(1.5e308)
    assert replay_execution(result, request, factor_set, selection) == result
    assessment = assess_held_out(result, request, factor_set, selection, mapping)
    assert assessment.summary.count == 200
    assert assessment.summary.mean_raw_residual_m == pytest.approx(-1e154)
    assert assessment.summary.root_mean_square_raw_residual_m == pytest.approx(1e154)
    assert (
        type(assessment).model_validate(assessment.model_dump(mode="python"))
        == assessment
    )


def test_unrepresentable_objective_is_a_replayable_closed_failure() -> None:
    _mapping, factor_set, selection, request = large_plane_case()
    result = execute(request, factor_set, selection, ReturningAdapter((1.8e154,)))
    assert result.disposition == "execution-failed"
    assert result.failures == ("final-objective-nonfinite",)
    assert result.final_objective is None
    assert result.final_parameters is None
    assert replay_execution(result, request, factor_set, selection) == result


@pytest.mark.parametrize(
    "residuals,expected_mean",
    [
        ((1e308, 1e308), 1e308),
        ((1e308, 1e308, -1e308, -1e308), 0.0),
    ],
)
def test_held_out_aggregation_handles_extreme_mean_and_rms(
    residuals: tuple[float, ...], expected_mean: float
) -> None:
    summary = summarize_held_out(residuals)
    assert summary.mean_raw_residual_m == expected_mean
    assert summary.root_mean_square_raw_residual_m == pytest.approx(1e308)


def test_held_out_changes_provenance_without_changing_training_numerics() -> None:
    from scansor.declared_numpy_backend import (
        NUMPY_GAUSS_NEWTON_DESCRIPTOR,
        DeclaredNumpyBackend,
    )

    declaration = constructed_shell_declaration()
    results: list[ExecutionResult] = []
    for held_out_radius in (0.0201, 0.0202):
        mapping, factor_set, selection = _factor_case(
            declaration,
            [(0.02, 0.0, axial) for axial in (-0.25, 0.25, 0.75)]
            + [(held_out_radius, 0.0, 0.1)],
            held_out=(3,),
        )
        request = create_execution_request(
            factor_set,
            selection,
            _parameters(declaration, (0.02015,)),
            NUMPY_GAUSS_NEWTON_DESCRIPTOR,
        )
        result = execute(request, factor_set, selection, DeclaredNumpyBackend())
        sealed = execution_result_bytes(result)
        _ = assess_held_out(result, request, factor_set, selection, mapping)
        assert execution_result_bytes(result) == sealed
        results.append(result)
    first, second = results
    assert first.request.mapping_run_id != second.request.mapping_run_id
    assert first.result_id != second.result_id
    assert first.final_parameters == second.final_parameters
    assert first.final_evaluation is not None and second.final_evaluation is not None
    assert (
        first.final_evaluation.raw_residuals_m
        == second.final_evaluation.raw_residuals_m
    )
    assert first.final_evaluation.jacobian == second.final_evaluation.jacobian
    assert first.final_objective == second.final_objective
    assert first.normalized_termination == second.normalized_termination
    assert [row.values for row in first.callback_trace] == [
        row.values for row in second.callback_trace
    ]
