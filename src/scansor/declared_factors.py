from __future__ import annotations

from typing import Any, cast

import numpy as np
from pydantic import BaseModel, ValidationError

from scansor.declared_factor_models import (
    FAILURE_CODE_ORDER,
    ActiveFactorSelection,
    DeclaredFactorStrictModel,
    FactorDeclaration,
    FactorEvaluation,
    FailureCode,
    InstantiatedFactor,
    InstantiatedFactorSet,
    ParameterVector,
    PreflightDiagnostics,
    content_id,
)
from scansor.errors import ScansorError
from scansor.geometry_evaluator import DeclaredGeometryEvaluator
from scansor.mapping_models import MappingResult
from scansor.model_declarations import validate_runtime_parameter_structure


def _identified[IdentifiedModel: DeclaredFactorStrictModel](
    model_type: type[IdentifiedModel],
    prefix: str,
    field: str,
    values: dict[str, object],
) -> IdentifiedModel:
    provisional = model_type.model_construct(**cast(dict[str, Any], values))
    values[field] = content_id(prefix, provisional, field)
    return model_type(**values)


def _revalidated[ValidatedModel: BaseModel](
    model_type: type[ValidatedModel], value: ValidatedModel, label: str
) -> ValidatedModel:
    try:
        return model_type.model_validate(value.model_dump(mode="python"))
    except (TypeError, ValidationError, ValueError) as error:
        raise ScansorError(f"invalid {label}: {error}") from error


def instantiate_factors(mapping: MappingResult) -> InstantiatedFactorSet:
    """Create one model-bound factor for every accepted primary mapping."""

    mapping = _revalidated(MappingResult, mapping, "mapping result")
    if mapping.disposition != "accepted":
        raise ScansorError("rejected source mapping cannot instantiate factors")
    declaration = mapping.request.declaration
    observations = {item.observation_id: item for item in mapping.observations}
    candidates = {item.candidate_id: item for item in mapping.candidates}
    nominal = tuple(item.nominal for item in declaration.parameters)
    evaluator = DeclaredGeometryEvaluator(declaration, nominal)
    factor_declarations: list[FactorDeclaration] = []
    factors: list[InstantiatedFactor] = []
    ordered_mappings = sorted(
        mapping.mappings,
        key=lambda item: observations[item.observation_id].row_index,
    )
    for mapped in ordered_mappings:
        observation = observations.get(mapped.observation_id)
        candidate = candidates.get(mapped.candidate_id)
        if observation is None or candidate is None:
            raise ScansorError("mapping factor provenance is unresolved")
        if (
            mapped.model_id != declaration.model_id
            or observation.model_id != declaration.model_id
            or candidate.model_id != declaration.model_id
        ):
            raise ScansorError("mapping factor provenance mixes model identities")
        factor_declaration = _identified(
            FactorDeclaration,
            "factor-declaration",
            "declaration_id",
            {
                "candidate_id": mapped.candidate_id,
                "element_id": mapped.element_id,
                "mapping_id": mapped.mapping_id,
                "mapping_role": mapped.role,
                "mapping_run_id": mapping.mapping_run_id,
                "model_id": declaration.model_id,
                "observation_id": mapped.observation_id,
                "row_index": observation.row_index,
            },
        )
        nominal_coverage_cell_ids = tuple(
            cell.cell_id
            for cell in declaration.optimization_preflight.coverage_cells
            if cell.element_id == mapped.element_id
            and evaluator.classify_coverage(
                mapped.element_id, cell.domain, observation.point_model_m
            ).projected_inside
        )
        factor = _identified(
            InstantiatedFactor,
            "factor",
            "factor_id",
            {
                "declaration_id": factor_declaration.declaration_id,
                "model_id": declaration.model_id,
                "nominal_coverage_cell_ids": nominal_coverage_cell_ids,
                "point_model_m": observation.point_model_m,
            },
        )
        factor_declarations.append(factor_declaration)
        factors.append(factor)
    values: dict[str, object] = {
        "declaration": declaration,
        "declarations": tuple(factor_declarations),
        "factors": tuple(factors),
        "mapping_run_id": mapping.mapping_run_id,
        "model_id": declaration.model_id,
    }
    return _identified(InstantiatedFactorSet, "factor-set", "factor_set_id", values)


def select_active_factors(
    factor_set: InstantiatedFactorSet, active_factor_ids: tuple[str, ...]
) -> ActiveFactorSelection:
    """Bind an explicit ordered factor subset; availability never activates it."""

    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    known = tuple(factor.factor_id for factor in factor_set.factors)
    positions = {factor_id: index for index, factor_id in enumerate(known)}
    if len(active_factor_ids) != len(set(active_factor_ids)):
        raise ScansorError("active factor IDs must be unique")
    if unknown := [item for item in active_factor_ids if item not in positions]:
        raise ScansorError(f"unknown active factor ID: {unknown[0]}")
    selected_positions = [positions[item] for item in active_factor_ids]
    if selected_positions != sorted(selected_positions):
        raise ScansorError("active factor IDs must preserve factor-set relative order")
    return _identified(
        ActiveFactorSelection,
        "selection",
        "selection_id",
        {
            "active_factor_ids": active_factor_ids,
            "factor_set_id": factor_set.factor_set_id,
            "model_id": factor_set.model_id,
        },
    )


def _selected(
    factor_set: InstantiatedFactorSet, selection: ActiveFactorSelection
) -> tuple[tuple[FactorDeclaration, InstantiatedFactor], ...]:
    if (
        selection.factor_set_id != factor_set.factor_set_id
        or selection.model_id != factor_set.model_id
    ):
        raise ScansorError(
            "active-factor selection belongs to another factor set or model"
        )
    by_id = {
        factor.factor_id: (factor_declaration, factor)
        for factor_declaration, factor in zip(
            factor_set.declarations, factor_set.factors, strict=True
        )
    }
    try:
        selected = tuple(by_id[item] for item in selection.active_factor_ids)
    except KeyError as error:
        raise ScansorError("active-factor selection contains an unknown ID") from error
    positions = {
        factor.factor_id: index for index, factor in enumerate(factor_set.factors)
    }
    if [positions[factor.factor_id] for _, factor in selected] != sorted(
        positions[factor.factor_id] for _, factor in selected
    ):
        raise ScansorError("active-factor selection order was tampered")
    return selected


def _validated_inputs(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
) -> tuple[
    InstantiatedFactorSet,
    ActiveFactorSelection,
    ParameterVector,
    tuple[tuple[FactorDeclaration, InstantiatedFactor], ...],
]:
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    selection = _revalidated(
        ActiveFactorSelection, selection, "active-factor selection"
    )
    parameters = _revalidated(ParameterVector, parameters, "parameter vector")
    if parameters.model_id != factor_set.model_id:
        raise ScansorError("parameter vector belongs to another model")
    if len(parameters.values) != len(factor_set.declaration.parameters):
        raise ScansorError(
            "parameter vector dimension disagrees with model declaration"
        )
    return factor_set, selection, parameters, _selected(factor_set, selection)


def _evaluate_validated(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
    selected: tuple[tuple[FactorDeclaration, InstantiatedFactor], ...],
) -> FactorEvaluation:
    evaluator = DeclaredGeometryEvaluator(factor_set.declaration, parameters.values)
    residuals: list[float] = []
    rows: list[tuple[float, ...]] = []
    for factor_declaration, factor in selected:
        evaluated = evaluator.evaluate_fixed_pose_shape(
            factor_declaration.element_id, factor.point_model_m
        )
        residuals.append(evaluated.residual_m)
        rows.append(evaluated.parameter_jacobian_row)
    parameter_order = tuple(
        item.parameter_id for item in factor_set.declaration.parameters
    )
    values: dict[str, object] = {
        "active_factor_ids": selection.active_factor_ids,
        "factor_set_id": factor_set.factor_set_id,
        "jacobian": tuple(rows),
        "model_id": factor_set.model_id,
        "parameter_order": parameter_order,
        "parameters": parameters,
        "raw_residuals_m": tuple(residuals),
        "selection_id": selection.selection_id,
    }
    return _identified(FactorEvaluation, "evaluation", "evaluation_id", values)


def evaluate_factors(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
) -> FactorEvaluation:
    """Evaluate selected fixed-pose factors through the declared geometry evaluator."""

    validated = _validated_inputs(factor_set, selection, parameters)
    return _evaluate_validated(*validated)


def preflight_factors(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
) -> PreflightDiagnostics:
    """Return deterministic optimizer-independent active-factor diagnostics."""

    factor_set, selection, parameters, selected = _validated_inputs(
        factor_set, selection, parameters
    )
    declaration = factor_set.declaration
    policy = declaration.optimization_preflight
    element_counts = {item.element_id: 0 for item in declaration.elements}
    coverage_counts = {item.cell_id: 0 for item in policy.coverage_cells}
    for factor_declaration, factor in selected:
        element_counts[factor_declaration.element_id] += 1
        for cell_id in factor.nominal_coverage_cell_ids:
            coverage_counts[cell_id] += 1
    required_counts = {
        item.element_id: element_counts[item.element_id]
        for item in policy.required_support
    }
    missing_support = tuple(
        item.element_id
        for item in policy.required_support
        if required_counts[item.element_id] < item.minimum_count
    )
    missing_coverage = tuple(
        item.cell_id
        for item in policy.coverage_cells
        if coverage_counts[item.cell_id] < item.minimum_count
    )
    failures: list[FailureCode] = []
    if not selected:
        failures.append("empty-active-selection")
    if missing_support:
        failures.append("missing-required-support")
    if missing_coverage:
        failures.append("insufficient-coverage")
    out_of_bounds = any(
        value < parameter.lower or value > parameter.upper
        for value, parameter in zip(
            parameters.values, declaration.parameters, strict=True
        )
    )
    if out_of_bounds:
        failures.append("parameter-out-of-bounds")
    structurally_valid = True
    try:
        _ = validate_runtime_parameter_structure(declaration, parameters.values)
    except ScansorError:
        structurally_valid = False
        failures.append("structural-geometry-invalid")

    evaluation: FactorEvaluation | None = None
    singular: tuple[float, ...] = ()
    observed_rank = 0
    rank_available = False
    if not out_of_bounds and structurally_valid:
        try:
            evaluation = _evaluate_validated(
                factor_set, selection, parameters, selected
            )
        except (ScansorError, ValidationError, ValueError, FloatingPointError):
            failures.append("evaluation-failed")
        if evaluation is not None:
            rank_policy = policy.relative_rank
            parameter_indices = {
                item.parameter_id: index
                for index, item in enumerate(declaration.parameters)
            }
            rows = [
                [
                    row[parameter_indices[parameter_id]]
                    * scale
                    / rank_policy.residual_scale
                    for parameter_id, scale in zip(
                        rank_policy.parameter_ids,
                        rank_policy.parameter_scales,
                        strict=True,
                    )
                ]
                for row in evaluation.jacobian
            ]
            try:
                singular_array = (
                    np.linalg.svd(np.asarray(rows, dtype=np.float64), compute_uv=False)
                    if rows and rank_policy.parameter_ids
                    else np.asarray((), dtype=np.float64)
                )
                rank_available = True
            except np.linalg.LinAlgError:
                singular_array = np.asarray((), dtype=np.float64)
                failures.append("rank-evaluation-failed")
            singular = tuple(float(item) for item in singular_array)
            limit = singular[0] * rank_policy.relative_threshold if singular else 0.0
            observed_rank = sum(item > limit for item in singular) if limit > 0.0 else 0
            if rank_available and observed_rank < rank_policy.required_rank:
                failures.append("rank-deficient")

    rank_policy = policy.relative_rank
    failure_codes = tuple(code for code in FAILURE_CODE_ORDER if code in failures)
    values: dict[str, object] = {
        "coverage_cell_counts": coverage_counts,
        "coverage_cell_order": tuple(coverage_counts),
        "eligible_for_optimization": not failure_codes,
        "evaluation_id": evaluation.evaluation_id if evaluation is not None else None,
        "expected_rank": rank_policy.required_rank,
        "factor_set_id": factor_set.factor_set_id,
        "failure_codes": failure_codes,
        "missing_coverage_cells": missing_coverage,
        "missing_required_support": missing_support,
        "model_id": factor_set.model_id,
        "observed_rank": observed_rank,
        "parameter_order": rank_policy.parameter_ids,
        "parameter_scales": rank_policy.parameter_scales,
        "rank_relative_threshold": rank_policy.relative_threshold,
        "required_support_counts": required_counts,
        "required_support_order": tuple(required_counts),
        "residual_scale_m": rank_policy.residual_scale,
        "selection_id": selection.selection_id,
        "singular_values_dimensionless": singular,
    }
    return _identified(PreflightDiagnostics, "preflight", "preflight_id", values)
