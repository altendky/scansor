from __future__ import annotations

import math
from typing import Any, Literal, cast

import numpy as np
from pydantic import BaseModel, ValidationError

from scansor.errors import ScansorError
from scansor.factor_models import (
    ASYMMETRIC_SHAPE_PARAMETERS,
    AXISYMMETRIC_SHAPE_PARAMETERS,
    FAILURE_CODE_ORDER,
    NOMINAL_SHAPE,
    POSE_PARAMETERS,
    ActiveElementCount,
    ActiveFactorSelection,
    ElementId,
    FactorContract,
    FactorDeclaration,
    FactorEvaluation,
    FactorStrictModel,
    FailureCode,
    InstantiatedFactor,
    InstantiatedFactorSet,
    ParameterVector,
    PreflightDiagnostics,
    content_id,
    factor_contract,
)
from scansor.mapping_models import MappingResult
from scansor.serialization import canonical_json, sha256

ELEMENTS: tuple[ElementId, ...] = (
    "cylinder.band-1",
    "cylinder.band-2",
    "cylinder.band-3",
    "plane.station-0",
    "plane.station-20",
    "plane.station-50",
    "plane.station-80",
)
DATUM_ELEMENT: Literal["plane.datum-flat"] = "plane.datum-flat"


def _identified[IdentifiedModel: FactorStrictModel](
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
    mapping = _revalidated(MappingResult, mapping, "mapping result")
    if mapping.disposition != "accepted":
        raise ScansorError("rejected source mapping cannot instantiate factors")
    contract = factor_contract()
    observations = {item.observation_id: item for item in mapping.observations}
    candidates = {item.candidate_id: item for item in mapping.candidates}
    mapping_content_sha256 = sha256(
        canonical_json(
            {
                "mapping_run_id": mapping.mapping_run_id,
                "mappings": [item.model_dump(mode="json") for item in mapping.mappings],
                "training_points": [
                    {
                        "observation_id": item.observation_id,
                        "point_model_m": item.point_model_m,
                        "row_index": item.row_index,
                    }
                    for item in mapping.observations
                ],
                "variant": mapping.request.variant,
            }
        )
    )
    mapping_request_sha256 = sha256(canonical_json(mapping.request))
    declarations: list[FactorDeclaration] = []
    factors: list[InstantiatedFactor] = []
    for mapped in mapping.mappings:
        observation = observations.get(mapped.observation_id)
        candidate = candidates.get(mapped.candidate_id)
        if observation is None or candidate is None:
            raise ScansorError("mapping factor provenance is unresolved")
        declaration = _identified(
            FactorDeclaration,
            "declaration",
            "declaration_id",
            {
                "candidate_id": mapped.candidate_id,
                "contract_id": contract.contract_id,
                "element_id": mapped.element_id,
                "factor_kind": candidate.kind,
                "mapping_content_sha256": mapping_content_sha256,
                "mapping_id": mapped.mapping_id,
                "mapping_role": mapped.role,
                "mapping_request_sha256": mapping_request_sha256,
                "mapping_run_id": mapping.mapping_run_id,
                "observation_id": mapped.observation_id,
                "row_index": observation.row_index,
                "variant": mapping.request.variant,
            },
        )
        factor = _identified(
            InstantiatedFactor,
            "factor",
            "factor_id",
            {
                "declaration_id": declaration.declaration_id,
                "point_model_m": observation.point_model_m,
            },
        )
        declarations.append(declaration)
        factors.append(factor)
    values: dict[str, object] = {
        "contract": contract,
        "declarations": tuple(declarations),
        "factors": tuple(factors),
        "mapping_run_id": mapping.mapping_run_id,
        "variant": mapping.request.variant,
    }
    return _identified(InstantiatedFactorSet, "factor-set", "factor_set_id", values)


def select_active_factors(
    factor_set: InstantiatedFactorSet, active_factor_ids: tuple[str, ...]
) -> ActiveFactorSelection:
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    known = [factor.factor_id for factor in factor_set.factors]
    positions = {factor_id: index for index, factor_id in enumerate(known)}
    if len(active_factor_ids) != len(set(active_factor_ids)):
        raise ScansorError("active factor IDs must be unique")
    unknown = [
        factor_id for factor_id in active_factor_ids if factor_id not in positions
    ]
    if unknown:
        raise ScansorError(f"unknown active factor ID: {unknown[0]}")
    selected_positions = [positions[factor_id] for factor_id in active_factor_ids]
    if selected_positions != sorted(selected_positions):
        raise ScansorError("active factor IDs must preserve factor-set relative order")
    return _identified(
        ActiveFactorSelection,
        "selection",
        "selection_id",
        {
            "active_factor_ids": active_factor_ids,
            "factor_set_id": factor_set.factor_set_id,
        },
    )


def _selected(
    factor_set: InstantiatedFactorSet, selection: ActiveFactorSelection
) -> tuple[tuple[FactorDeclaration, InstantiatedFactor], ...]:
    if selection.factor_set_id != factor_set.factor_set_id:
        raise ScansorError("active-factor selection belongs to another factor set")
    by_id = {
        factor.factor_id: (declaration, factor)
        for declaration, factor in zip(
            factor_set.declarations, factor_set.factors, strict=True
        )
    }
    try:
        selected = tuple(by_id[factor_id] for factor_id in selection.active_factor_ids)
    except KeyError as error:
        raise ScansorError("active-factor selection contains an unknown ID") from error
    expected_order = [
        factor.factor_id for factor in factor_set.factors if factor.factor_id in by_id
    ]
    positions = {factor_id: index for index, factor_id in enumerate(expected_order)}
    if [positions[item.factor_id] for _, item in selected] != sorted(
        positions[item.factor_id] for _, item in selected
    ):
        raise ScansorError("active-factor selection order was tampered")
    return selected


def _skew(vector: np.ndarray) -> np.ndarray:
    return np.array(
        (
            (0.0, -vector[2], vector[1]),
            (vector[2], 0.0, -vector[0]),
            (-vector[1], vector[0], 0.0),
        ),
        dtype=np.float64,
    )


def _rotation_and_right_jacobian(phi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    angle = float(np.linalg.norm(phi))
    angle_squared = angle * angle
    phi_skew = _skew(phi)
    if angle < 1e-4:
        a = 0.5 - angle_squared / 24.0 + angle_squared**2 / 720.0
        b = 1.0 / 6.0 - angle_squared / 120.0 + angle_squared**2 / 5040.0
        sinc = 1.0 - angle_squared / 6.0 + angle_squared**2 / 120.0
        cosc = 0.5 - angle_squared / 24.0 + angle_squared**2 / 720.0
    else:
        a = (1.0 - math.cos(angle)) / angle_squared
        b = (angle - math.sin(angle)) / (angle_squared * angle)
        sinc = math.sin(angle) / angle
        cosc = a
    rotation = np.eye(3) + sinc * phi_skew + cosc * (phi_skew @ phi_skew)
    right_jacobian = np.eye(3) - a * phi_skew + b * (phi_skew @ phi_skew)
    return rotation, right_jacobian


def _support_gradient(point: np.ndarray, element_id: str) -> np.ndarray:
    if not np.isfinite(point).all() or point.shape != (3,):
        raise ScansorError("factor point must be a finite three-vector")
    if element_id.startswith("cylinder.band-"):
        radial = math.hypot(float(point[0]), float(point[1]))
        if radial == 0.0:
            raise ScansorError("cylinder residual is undefined at radial zero")
        return np.array((point[0] / radial, point[1] / radial, 0.0))
    if element_id == DATUM_ELEMENT:
        return np.array((1.0, 0.0, 0.0))
    if element_id in {"plane.station-0", "plane.station-20"}:
        return np.array((0.0, 0.0, -1.0))
    if element_id in {"plane.station-50", "plane.station-80"}:
        return np.array((0.0, 0.0, 1.0))
    raise ScansorError(f"unsupported factor element: {element_id}")


def _residual(point: np.ndarray, element_id: str, shape: np.ndarray) -> float:
    gradient = _support_gradient(point, element_id)
    if element_id.startswith("cylinder.band-"):
        return math.hypot(float(point[0]), float(point[1])) - float(
            shape[int(element_id[-1]) - 1]
        )
    if element_id == DATUM_ELEMENT:
        return float(point[0] - shape[6])
    station_index = {
        "plane.station-0": None,
        "plane.station-20": 3,
        "plane.station-50": 4,
        "plane.station-80": 5,
    }[element_id]
    station = 0.0 if station_index is None else float(shape[station_index])
    return float(gradient[2] * (point[2] - station))


def _shape_row(element_id: str, size: int) -> np.ndarray:
    row = np.zeros(size, dtype=np.float64)
    if element_id.startswith("cylinder.band-"):
        row[int(element_id[-1]) - 1] = -1.0
    elif element_id == "plane.station-20":
        row[3] = 1.0
    elif element_id == "plane.station-50":
        row[4] = -1.0
    elif element_id == "plane.station-80":
        row[5] = -1.0
    elif element_id == DATUM_ELEMENT and size == 7:
        row[6] = -1.0
    return row


def evaluate_factors(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
) -> FactorEvaluation:
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    selection = _revalidated(
        ActiveFactorSelection, selection, "active-factor selection"
    )
    parameters = _revalidated(ParameterVector, parameters, "parameter vector")
    if parameters.variant != factor_set.variant:
        raise ScansorError("parameter and factor-set variants differ")
    selected = _selected(factor_set, selection)
    shape = np.asarray(NOMINAL_SHAPE, dtype=np.float64)
    residuals: list[float] = []
    rows: list[tuple[float, ...]] = []
    if parameters.problem == "fixed-pose-shape":
        shape = np.asarray(parameters.values, dtype=np.float64)
        parameter_order = (
            ASYMMETRIC_SHAPE_PARAMETERS
            if factor_set.variant == "asymmetric-datum-flat"
            else AXISYMMETRIC_SHAPE_PARAMETERS
        )
        for declaration, factor in selected:
            point = np.asarray(factor.point_model_m, dtype=np.float64)
            residuals.append(_residual(point, declaration.element_id, shape))
            rows.append(
                tuple(
                    float(value)
                    for value in _shape_row(
                        declaration.element_id, len(parameter_order)
                    )
                )
            )
    else:
        parameter_order = POSE_PARAMETERS
        pose = np.asarray(parameters.values, dtype=np.float64)
        rotation, right_jacobian = _rotation_and_right_jacobian(pose[3:])
        for declaration, factor in selected:
            source = np.asarray(factor.point_model_m, dtype=np.float64)
            point = rotation @ source + pose[:3]
            gradient = _support_gradient(point, declaration.element_id)
            residuals.append(_residual(point, declaration.element_id, shape))
            rotation_block = -gradient @ rotation @ _skew(source) @ right_jacobian
            rows.append(
                tuple(
                    float(value) for value in np.concatenate((gradient, rotation_block))
                )
            )
    if not all(math.isfinite(value) for value in residuals) or not all(
        math.isfinite(value) for row in rows for value in row
    ):
        raise ScansorError("factor evaluation produced nonfinite values")
    values: dict[str, object] = {
        "active_factor_ids": selection.active_factor_ids,
        "factor_set_id": factor_set.factor_set_id,
        "jacobian": tuple(rows),
        "parameter_order": parameter_order,
        "parameters": parameters,
        "raw_residuals_m": tuple(residuals),
        "selection_id": selection.selection_id,
    }
    return _identified(FactorEvaluation, "evaluation", "evaluation_id", values)


def _geometry_failures(
    contract: FactorContract, parameters: ParameterVector
) -> tuple[FailureCode, ...]:
    if parameters.problem == "fixed-geometry-pose-correction":
        lower = contract.pose_lower_m_rad
        upper = contract.pose_upper_m_rad
        return (
            ("parameter-out-of-bounds",)
            if any(
                value < low or value > high
                for value, low, high in zip(
                    parameters.values, lower, upper, strict=True
                )
            )
            else ()
        )
    size = len(parameters.values)
    values = parameters.values
    failures: list[FailureCode] = []
    if any(
        value < low or value > high
        for value, low, high in zip(
            values,
            contract.shape_lower_m[:size],
            contract.shape_upper_m[:size],
            strict=True,
        )
    ):
        failures.append("parameter-out-of-bounds")
    r1, r2, r3, s20, s50, s80 = values[:6]
    structurally_valid = (
        min(r1, r2, r3) > 0.0
        and 0.0 < s20 < s50 < s80
        and min(s20, s50 - s20, s80 - s50) > 0.004
        and r2 - r1 > 0.002
        and r2 - r3 > 0.002
    )
    if size == 7:
        datum = values[6]
        radicand = r2 * r2 - datum * datum
        structurally_valid = (
            structurally_valid
            and 0.0 < datum < r2
            and radicand > 0.0
            and math.sqrt(radicand) > 0.001
        )
    if not structurally_valid:
        failures.append("structural-geometry-invalid")
    return tuple(failures)


def parameter_domain_failures(
    contract: FactorContract, parameters: ParameterVector
) -> tuple[FailureCode, ...]:
    """Return only application-owned bounds and structural-domain failures."""
    contract = _revalidated(FactorContract, contract, "factor contract")
    parameters = _revalidated(ParameterVector, parameters, "parameter vector")
    return _geometry_failures(contract, parameters)


def evaluate_support_residual(
    point_model_m: tuple[float, float, float],
    element_id: ElementId,
    parameters: ParameterVector,
) -> float:
    """Evaluate one already assigned support without remapping the point."""
    parameters = _revalidated(ParameterVector, parameters, "parameter vector")
    source = np.asarray(point_model_m, dtype=np.float64)
    if source.shape != (3,) or not np.isfinite(source).all():
        raise ScansorError("factor point must be a finite three-vector")
    shape = np.asarray(NOMINAL_SHAPE, dtype=np.float64)
    point = source
    if parameters.problem == "fixed-pose-shape":
        shape = np.asarray(parameters.values, dtype=np.float64)
    else:
        pose = np.asarray(parameters.values, dtype=np.float64)
        rotation, _right_jacobian = _rotation_and_right_jacobian(pose[3:])
        point = rotation @ source + pose[:3]
    value = _residual(point, element_id, shape)
    if not math.isfinite(value):
        raise ScansorError("support evaluation produced a nonfinite residual")
    return value


def preflight_factors(
    factor_set: InstantiatedFactorSet,
    selection: ActiveFactorSelection,
    parameters: ParameterVector,
) -> PreflightDiagnostics:
    factor_set = _revalidated(
        InstantiatedFactorSet, factor_set, "instantiated factor set"
    )
    selection = _revalidated(
        ActiveFactorSelection, selection, "active-factor selection"
    )
    parameters = _revalidated(ParameterVector, parameters, "parameter vector")
    contract = factor_set.contract
    selected = _selected(factor_set, selection)
    required_elements: tuple[ElementId, ...] = ELEMENTS + (
        (DATUM_ELEMENT,) if factor_set.variant == "asymmetric-datum-flat" else ()
    )
    counts: dict[ElementId, int] = {element: 0 for element in required_elements}
    for declaration, _factor in selected:
        counts[declaration.element_id] += 1
    missing = tuple(element for element, count in counts.items() if count == 0)
    failures: list[FailureCode] = []
    if not selected:
        failures.append("empty-active-selection")
    if missing:
        failures.append("missing-active-elements")
    failures.extend(_geometry_failures(contract, parameters))
    evaluation: FactorEvaluation | None = None
    singular: tuple[float, ...] = ()
    rank = 0
    rank_available = False
    try:
        evaluation = evaluate_factors(factor_set, selection, parameters)
    except ScansorError as error:
        code: FailureCode = (
            "radial-zero-evaluation"
            if "radial zero" in str(error)
            else (
                "nonfinite-evaluation"
                if "nonfinite" in str(error) or "finite three-vector" in str(error)
                else "evaluation-undefined"
            )
        )
        failures.append(code)
    except (ValidationError, ValueError, FloatingPointError):
        failures.append("nonfinite-evaluation")
    if evaluation is not None:
        jacobian = np.asarray(evaluation.jacobian, dtype=np.float64).reshape(
            len(evaluation.jacobian), len(evaluation.parameter_order)
        )
        scales = np.asarray(
            (
                contract.shape_scales_m[: len(parameters.values)]
                if parameters.problem == "fixed-pose-shape"
                else contract.pose_scales_m_rad
            ),
            dtype=np.float64,
        )
        dimensionless = jacobian @ np.diag(scales) / contract.residual_scale_m
        if not np.isfinite(dimensionless).all():
            singular_array = np.array([], dtype=np.float64)
            failures.append("nonfinite-evaluation")
        else:
            try:
                singular_array = np.linalg.svd(dimensionless, compute_uv=False)
                rank_available = True
            except np.linalg.LinAlgError:
                singular_array = np.array([], dtype=np.float64)
                failures.append("rank-evaluation-failed")
        singular = tuple(float(value) for value in singular_array)
        limit = (
            singular_array[0] * contract.rank_relative_threshold
            if singular_array.size
            else 0.0
        )
        rank = int(np.sum(singular_array > limit)) if limit > 0.0 else 0
    expected_rank = (
        len(parameters.values)
        if parameters.problem == "fixed-pose-shape"
        else (5 if factor_set.variant == "axisymmetric" else 6)
    )
    if evaluation is not None and rank_available and rank < expected_rank:
        failures.append("rank-deficient")
    if (
        evaluation is not None
        and rank_available
        and parameters.problem == "fixed-geometry-pose-correction"
        and factor_set.variant == "axisymmetric"
    ):
        jacobian = np.asarray(evaluation.jacobian, dtype=np.float64).reshape(
            len(evaluation.jacobian), len(evaluation.parameter_order)
        )
        scales = np.asarray(contract.pose_scales_m_rad)
        dimensionless = jacobian @ np.diag(scales) / contract.residual_scale_m
        try:
            needs_complete_right_basis = dimensionless.shape[0] < dimensionless.shape[1]
            _u, _s, vh = np.linalg.svd(
                dimensionless, full_matrices=needs_complete_right_basis
            )
        except np.linalg.LinAlgError:
            failures.append("rank-evaluation-failed")
            rank_available = False
            vh = np.empty((0, 0), dtype=np.float64)
        pose = np.asarray(parameters.values, dtype=np.float64)
        rotation, right_jacobian = _rotation_and_right_jacobian(pose[3:])
        gauge = np.concatenate(
            (
                np.cross(np.array((0.0, 0.0, 1.0)), pose[:3]),
                np.linalg.solve(right_jacobian, rotation.T @ np.array((0.0, 0.0, 1.0))),
            )
        )
        dimensionless_gauge = gauge / scales
        dimensionless_gauge /= np.linalg.norm(dimensionless_gauge)
        roll_alignment = (
            abs(float(vh[-1] @ dimensionless_gauge)) if vh.shape == (6, 6) else 0.0
        )
        if rank_available and (rank != 5 or roll_alignment < 0.999999):
            failures.append("unexpected-pose-gauge")
    if (
        evaluation is not None
        and rank_available
        and parameters.problem == "fixed-geometry-pose-correction"
        and factor_set.variant == "asymmetric-datum-flat"
        and rank != 6
        and "rank-deficient" not in failures
    ):
        failures.append("rank-deficient")
    failure_codes = tuple(code for code in FAILURE_CODE_ORDER if code in failures)
    parameter_order = (
        ASYMMETRIC_SHAPE_PARAMETERS
        if parameters.problem == "fixed-pose-shape"
        and factor_set.variant == "asymmetric-datum-flat"
        else (
            AXISYMMETRIC_SHAPE_PARAMETERS
            if parameters.problem == "fixed-pose-shape"
            else POSE_PARAMETERS
        )
    )
    parameter_scales = (
        contract.shape_scales_m[: len(parameters.values)]
        if parameters.problem == "fixed-pose-shape"
        else contract.pose_scales_m_rad
    )
    values: dict[str, object] = {
        "active_element_counts": tuple(
            ActiveElementCount(element_id=element, count=count)
            for element, count in counts.items()
        ),
        "eligible_for_optimization": not failure_codes,
        "evaluation_id": evaluation.evaluation_id if evaluation is not None else None,
        "expected_rank": expected_rank,
        "failure_codes": failure_codes,
        "factor_set_id": factor_set.factor_set_id,
        "missing_active_elements": missing,
        "observed_rank": rank,
        "parameter_order": parameter_order,
        "parameter_scales": parameter_scales,
        "problem": parameters.problem,
        "rank_relative_threshold": contract.rank_relative_threshold,
        "residual_scale_m": contract.residual_scale_m,
        "selection_id": selection.selection_id,
        "singular_values_dimensionless": singular,
        "variant": factor_set.variant,
    }
    return _identified(PreflightDiagnostics, "preflight", "preflight_id", values)
