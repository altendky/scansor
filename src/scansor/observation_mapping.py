from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from scansor.errors import ScansorError
from scansor.geometry_evaluator import DeclaredGeometryEvaluator
from scansor.mapping_models import (
    CandidateRecord,
    ExclusionRecord,
    HeldOutLeakageAudit,
    MappingDiagnostics,
    MappingRecord,
    MappingRequest,
    MappingResult,
    MembershipRecord,
    NormalDiagnostic,
    ObservationRecord,
    mapping_admission_diagnostics,
)
from scansor.ply import canonical_npy, load_canonical_npy
from scansor.serialization import canonical_json, sha256

MAX_MAPPING_ROWS = 20_000


@dataclass(frozen=True)
class NominalSupportCandidate:
    absolute_distance_m: float
    element_id: str
    signed_distance_m: float


@dataclass(frozen=True)
class NominalSupportAssessment:
    candidates: tuple[NominalSupportCandidate, ...]
    geometric_clearance_m: float | None
    outcome: Literal["assigned", "ambiguous", "gap", "outlier", "transition"]


def _identifier(
    kind: str,
    request_identity: str,
    row: int,
    suffix: str = "",
) -> str:
    payload = "\0".join((request_identity, str(row), suffix)).encode("ascii")
    return f"{kind}.{hashlib.sha256(payload).hexdigest()[:24]}"


def _validate_transform(request: MappingRequest) -> tuple[np.ndarray, np.ndarray]:
    rotation = np.asarray(request.transform.rotation, dtype=np.float64)
    translation = np.asarray(request.transform.translation_m, dtype=np.float64)
    tolerance = request.thresholds.transform_tolerance
    orthogonality_error = float(np.max(np.abs(rotation.T @ rotation - np.eye(3))))
    determinant = float(np.linalg.det(rotation))
    if orthogonality_error > tolerance:
        raise ScansorError(
            "observation-to-model rotation is not orthonormal within the "
            + "declared tolerance"
        )
    if abs(determinant - 1.0) > tolerance:
        raise ScansorError(
            "observation-to-model rotation must be proper with determinant +1"
        )
    return rotation, translation


def _validate_canonical(request: MappingRequest, data: bytes) -> np.ndarray:
    revision = request.input_revision
    if revision.canonical_row_count > MAX_MAPPING_ROWS:
        raise ScansorError(
            f"mapping input exceeds the {MAX_MAPPING_ROWS:,}-row application limit"
        )
    if sha256(data) != revision.canonical_sha256:
        raise ScansorError("canonical.npy SHA-256 does not match the input revision")
    array = load_canonical_npy(data)
    if len(array) != revision.canonical_row_count:
        raise ScansorError("canonical.npy row count does not match the input revision")
    fields = array.dtype.names
    if fields not in {
        ("x_m", "y_m", "z_m"),
        ("x_m", "y_m", "z_m", "nx", "ny", "nz"),
        ("x_m", "y_m", "z_m", "red", "green", "blue"),
        ("x_m", "y_m", "z_m", "nx", "ny", "nz", "red", "green", "blue"),
    }:
        raise ScansorError(
            "canonical.npy has no supported inspection coordinate schema"
        )
    coordinates = np.column_stack([array[name] for name in ("x_m", "y_m", "z_m")])
    if not np.isfinite(coordinates).all():
        raise ScansorError("canonical coordinates must be finite")
    if canonical_npy(array) != data:
        raise ScansorError("canonical.npy bytes are not canonical")
    return array


def assess_nominal_support(
    request: MappingRequest,
    point_model_m: tuple[float, float, float],
    evaluator: DeclaredGeometryEvaluator | None = None,
) -> NominalSupportAssessment:
    """Classify one point through the request's declared nominal model."""

    point = np.asarray(point_model_m, dtype=np.float64)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ScansorError("nominal support point must be a finite three-vector")
    declaration = request.declaration
    nominal = tuple(parameter.nominal for parameter in declaration.parameters)
    evaluator = evaluator or DeclaredGeometryEvaluator(declaration, nominal)
    candidates: list[NominalSupportCandidate] = []
    projected_inside = False
    transition = False
    for element in declaration.elements:
        evaluation = evaluator.classify_support(
            element.element_id,
            (float(point[0]), float(point[1]), float(point[2])),
        )
        if not evaluation.projected_inside:
            continue
        projected_inside = True
        absolute = abs(evaluation.signed_distance_m)
        if absolute > request.thresholds.max_support_distance_m:
            continue
        clearance = evaluation.boundary_clearance_m
        if clearance is None:
            raise ScansorError("inside declared support has no boundary clearance")
        if clearance < request.thresholds.transition_guard_m:
            transition = True
            continue
        candidates.append(
            NominalSupportCandidate(
                absolute_distance_m=absolute,
                element_id=element.element_id,
                signed_distance_m=evaluation.signed_distance_m,
            )
        )
    candidates.sort(key=lambda item: (item.absolute_distance_m, item.element_id))
    clearance = (
        candidates[1].absolute_distance_m - candidates[0].absolute_distance_m
        if len(candidates) > 1
        else None
    )
    ambiguous = (
        clearance is not None
        and clearance < request.thresholds.minimum_geometric_clearance_m
    )
    if transition:
        outcome: Literal["assigned", "ambiguous", "gap", "outlier", "transition"] = (
            "transition"
        )
    elif ambiguous:
        outcome = "ambiguous"
    elif candidates:
        outcome = "assigned"
    elif projected_inside:
        outcome = "outlier"
    else:
        outcome = "gap"
    return NominalSupportAssessment(
        candidates=tuple(candidates),
        geometric_clearance_m=clearance,
        outcome=outcome,
    )


def _normal_diagnostic(array: np.ndarray, row: int) -> NormalDiagnostic:
    fields = array.dtype.names or ()
    if "nx" not in fields:
        return NormalDiagnostic(source_present=False, magnitude=None)
    values = np.asarray([array[name][row] for name in ("nx", "ny", "nz")])
    if not np.isfinite(values).all():
        raise ScansorError("present normals must be finite diagnostics")
    scale = float(np.max(np.abs(values)))
    if scale == 0.0:
        raise ScansorError("present normals must have finite nonzero magnitude")
    magnitude = scale * math.sqrt(float(np.sum((values / scale) ** 2)))
    if not math.isfinite(magnitude) or magnitude == 0.0:
        raise ScansorError("present normals must have finite nonzero magnitude")
    return NormalDiagnostic(source_present=True, magnitude=magnitude)


def build_mapping(request: MappingRequest, canonical: bytes) -> MappingResult:
    request = MappingRequest.model_validate(request.model_dump(mode="python"))
    array = _validate_canonical(request, canonical)
    rotation, translation = _validate_transform(request)
    held_out_rows = set(request.held_out_row_indices)
    request_identity = sha256(canonical_json(request))
    model_id = request.declaration.model_id
    nominal = tuple(parameter.nominal for parameter in request.declaration.parameters)
    evaluator = DeclaredGeometryEvaluator(request.declaration, nominal)

    observations: list[ObservationRecord] = []
    held_out: list[ObservationRecord] = []
    candidates: list[CandidateRecord] = []
    memberships: list[MembershipRecord] = []
    mappings: list[MappingRecord] = []
    exclusions: list[ExclusionRecord] = []
    training_normal_magnitudes: list[float] = []
    for row in range(len(array)):
        point_observation = np.asarray(
            [array[name][row] for name in ("x_m", "y_m", "z_m")],
            dtype=np.float64,
        )
        point_array = rotation @ point_observation + translation
        if not np.isfinite(point_array).all():
            raise ScansorError(
                "observation-to-model transform produced nonfinite coordinates"
            )
        point = (float(point_array[0]), float(point_array[1]), float(point_array[2]))
        observation_id = _identifier("observation", request_identity, row)
        normal = _normal_diagnostic(array, row)
        if row in held_out_rows:
            held_out.append(
                ObservationRecord(
                    evaluation_state="post-fit-evaluation/not-evaluated",
                    model_id=model_id,
                    normal=normal,
                    observation_id=observation_id,
                    point_model_m=point,
                    role="held-out",
                    row_index=row,
                )
            )
            continue
        if normal.magnitude is not None:
            training_normal_magnitudes.append(normal.magnitude)

        support = assess_nominal_support(request, point, evaluator)
        row_candidates: list[CandidateRecord] = []
        for assessed in support.candidates:
            candidate_id = _identifier(
                "candidate", request_identity, row, assessed.element_id
            )
            row_candidates.append(
                CandidateRecord(
                    absolute_distance_m=assessed.absolute_distance_m,
                    candidate_id=candidate_id,
                    element_id=assessed.element_id,
                    geometric_clearance_m=None,
                    model_id=model_id,
                    observation_id=observation_id,
                    row_index=row,
                    signed_distance_m=assessed.signed_distance_m,
                )
            )
        clearance = support.geometric_clearance_m
        row_candidates = [
            item.model_copy(
                update={"geometric_clearance_m": clearance if index == 0 else None}
            )
            for index, item in enumerate(row_candidates)
        ]
        candidates.extend(row_candidates)
        memberships.extend(
            MembershipRecord(
                candidate_id=item.candidate_id,
                element_id=item.element_id,
                membership_id=_identifier(
                    "membership", request_identity, row, item.element_id
                ),
                model_id=model_id,
                observation_id=observation_id,
            )
            for item in row_candidates
        )

        if support.outcome == "assigned":
            selected = row_candidates[0]
            observations.append(
                ObservationRecord(
                    evaluation_state="training-mapped",
                    model_id=model_id,
                    normal=normal,
                    observation_id=observation_id,
                    point_model_m=point,
                    role="training",
                    row_index=row,
                )
            )
            mappings.append(
                MappingRecord(
                    candidate_id=selected.candidate_id,
                    element_id=selected.element_id,
                    mapping_id=_identifier(
                        "mapping", request_identity, row, selected.element_id
                    ),
                    model_id=model_id,
                    observation_id=observation_id,
                )
            )
            continue

        reason: Literal["ambiguous", "gap", "outlier", "transition"] = support.outcome
        exclusions.append(
            ExclusionRecord(
                candidate_ids=tuple(item.candidate_id for item in row_candidates),
                exclusion_id=_identifier("exclusion", request_identity, row, reason),
                model_id=model_id,
                observation_id=observation_id,
                reason=reason,
                row_index=row,
            )
        )

    (
        required_counts,
        coverage_counts,
        missing_support,
        missing_coverage,
        parameter_order,
        singular,
        rank_value,
        rank_required,
    ) = mapping_admission_diagnostics(request.declaration, mappings, observations)
    element_counts = {
        element.element_id: sum(
            mapping.element_id == element.element_id for mapping in mappings
        )
        for element in request.declaration.elements
    }
    exclusion_counts = {
        reason: sum(item.reason == reason for item in exclusions)
        for reason in ("ambiguous", "gap", "outlier", "transition")
    }
    reasons = [reason for reason in exclusion_counts if exclusion_counts[reason]]
    if missing_support:
        reasons.append("missing-required-support")
    if missing_coverage:
        reasons.append("insufficient-coverage")
    if rank_value < rank_required:
        reasons.append("rank-deficient")
    normal_bounds = (
        (min(training_normal_magnitudes), max(training_normal_magnitudes))
        if training_normal_magnitudes
        else None
    )
    rank_policy = request.declaration.mapping_admission.relative_rank
    diagnostics = MappingDiagnostics(
        counts={
            "candidate": len(candidates),
            "canonical": len(array),
            "exclusion": len(exclusions),
            "held_out": len(held_out),
            "mapping": len(mappings),
            "membership": len(memberships),
            "observation": len(observations),
            "training": len(array) - len(held_out),
        },
        coverage_cell_counts=coverage_counts,
        coverage_cell_order=tuple(coverage_counts),
        exclusion_counts=exclusion_counts,
        held_out_leakage=HeldOutLeakageAudit(),
        missing_coverage_cells=missing_coverage,
        missing_required_support=missing_support,
        normal_magnitude_bounds=normal_bounds,
        per_element_training_mapping_counts=element_counts,
        rank_parameter_order=parameter_order,
        rank_relative_threshold=rank_policy.relative_threshold,
        rank_required=rank_required,
        rank_singular_values=singular,
        rank_value=rank_value,
        rejection_reasons=tuple(reasons),
        required_support_counts=required_counts,
        required_support_order=tuple(required_counts),
    )
    provisional = MappingResult.model_construct(
        candidates=tuple(candidates),
        diagnostics=diagnostics,
        disposition="rejected" if reasons else "accepted",
        exclusions=tuple(exclusions),
        held_out_observations=tuple(held_out),
        mapping_run_id="",
        mappings=tuple(mappings),
        memberships=tuple(memberships),
        model_id=model_id,
        observations=tuple(observations),
        request=request,
    )
    semantic = provisional.model_dump(mode="python", exclude={"mapping_run_id"})
    semantic["mapping_run_id"] = sha256(canonical_json(semantic))
    return MappingResult.model_validate(semantic)
