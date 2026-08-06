from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from scansor.errors import ScansorError
from scansor.mapping_models import (
    CandidateRecord,
    ExclusionRecord,
    HeldOutLeakageAudit,
    MappingDiagnostics,
    MappingRecord,
    MappingRequest,
    MappingResult,
    MappingThresholds,
    MembershipRecord,
    NormalDiagnostic,
    ObservationRecord,
)
from scansor.ply import canonical_npy, load_canonical_npy
from scansor.serialization import canonical_json, sha256

R1 = 0.012
R2 = 0.018
R3 = 0.014
S1 = 0.020
S2 = 0.050
S3 = 0.080
DATUM_X = 0.016
DATUM_HALF_WIDTH = 0.008246211251235319
MAX_MAPPING_ROWS = 20_000


@dataclass(frozen=True)
class Element:
    element_id: str
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]


@dataclass(frozen=True)
class SupportEvaluation:
    clearance_m: float
    projected_inside: bool
    signed_distance_m: float


@dataclass(frozen=True)
class NominalSupportCandidate:
    absolute_distance_m: float
    element_id: str
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]
    signed_distance_m: float


@dataclass(frozen=True)
class NominalSupportAssessment:
    candidates: tuple[NominalSupportCandidate, ...]
    geometric_clearance_m: float | None
    outcome: Literal["assigned", "ambiguous", "gap", "outlier", "transition"]


def _elements(variant: str) -> tuple[Element, ...]:
    items = [
        Element("cylinder.band-1", "cylindrical"),
        Element("cylinder.band-2", "cylindrical"),
        Element("cylinder.band-3", "cylindrical"),
        Element("plane.station-0", "axial-planar"),
        Element("plane.station-20", "axial-planar"),
        Element("plane.station-50", "axial-planar"),
        Element("plane.station-80", "axial-planar"),
    ]
    if variant == "asymmetric-datum-flat":
        items.append(Element("plane.datum-flat", "datum-planar"))
    return tuple(items)


def _identifier(kind: str, request: MappingRequest, row: int, suffix: str = "") -> str:
    revision = request.input_revision
    payload = "\0".join(
        (
            request.contract,
            request.variant,
            revision.inspection_run_id,
            revision.inspection_report_sha256,
            revision.canonical_sha256,
            revision.synthetic_fixture.content_sha256,
            str(row),
            suffix,
        )
    ).encode("ascii")
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


def _support(
    point: np.ndarray, element: Element, asymmetric: bool
) -> SupportEvaluation:
    x, y, z = (float(value) for value in point)
    radial = math.hypot(x, y)
    if element.element_id.startswith("cylinder"):
        index = int(element.element_id[-1]) - 1
        radii = (R1, R2, R3)
        stations = (0.0, S1, S2, S3)
        radius = radii[index]
        if radial == 0.0:
            return SupportEvaluation(0.0, False, -radius)
        projected_x = radius * x / radial
        clearances = [z - stations[index], stations[index + 1] - z]
        inside = min(clearances) >= 0.0
        if asymmetric and index == 1:
            clearances.append(DATUM_X - projected_x)
            inside = inside and projected_x <= DATUM_X
        return SupportEvaluation(min(clearances), inside, radial - radius)

    if element.element_id == "plane.datum-flat":
        clearances = [
            y + DATUM_HALF_WIDTH,
            DATUM_HALF_WIDTH - y,
            z - S1,
            S2 - z,
        ]
        return SupportEvaluation(min(clearances), min(clearances) >= 0.0, x - DATUM_X)

    station, inner, outer, sign = {
        "plane.station-0": (0.0, 0.0, R1, -1.0),
        "plane.station-20": (S1, R1, R2, -1.0),
        "plane.station-50": (S2, R3, R2, 1.0),
        "plane.station-80": (S3, 0.0, R3, 1.0),
    }[element.element_id]
    clearances = [radial - inner, outer - radial]
    inside = min(clearances) >= 0.0
    if asymmetric and element.element_id in {"plane.station-20", "plane.station-50"}:
        clearances.append(DATUM_X - x)
        inside = inside and x <= DATUM_X
    return SupportEvaluation(min(clearances), inside, sign * (z - station))


def assess_nominal_support(
    point_model_m: tuple[float, float, float],
    variant: Literal["axisymmetric", "asymmetric-datum-flat"],
    thresholds: MappingThresholds,
) -> NominalSupportAssessment:
    """Classify one point using only the fixed nominal mapping supports."""
    point = np.asarray(point_model_m, dtype=np.float64)
    if point.shape != (3,) or not np.isfinite(point).all():
        raise ScansorError("nominal support point must be a finite three-vector")
    asymmetric = variant == "asymmetric-datum-flat"
    candidates: list[NominalSupportCandidate] = []
    projected_inside = False
    transition = False
    for element in _elements(variant):
        evaluation = _support(point, element, asymmetric)
        if not evaluation.projected_inside:
            continue
        projected_inside = True
        absolute = abs(evaluation.signed_distance_m)
        if absolute > thresholds.max_support_distance_m:
            continue
        if evaluation.clearance_m < thresholds.transition_guard_m:
            transition = True
            continue
        candidates.append(
            NominalSupportCandidate(
                absolute_distance_m=absolute,
                element_id=element.element_id,
                kind=element.kind,
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
        clearance is not None and clearance < thresholds.minimum_geometric_clearance_m
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


def _rank(
    mappings: list[MappingRecord], variant: str, threshold: float
) -> tuple[tuple[str, ...], tuple[float, ...], int, int]:
    parameters = (
        "radius.band-1",
        "radius.band-2",
        "radius.band-3",
        "station-20",
        "station-50",
        "station-80",
    )
    if variant == "asymmetric-datum-flat":
        parameters += ("datum-flat-x",)
    targets = {
        "cylinder.band-1": "radius.band-1",
        "cylinder.band-2": "radius.band-2",
        "cylinder.band-3": "radius.band-3",
        "plane.station-20": "station-20",
        "plane.station-50": "station-50",
        "plane.station-80": "station-80",
        "plane.datum-flat": "datum-flat-x",
    }
    counts = {name: 0 for name in parameters}
    for mapping in mappings:
        parameter = targets.get(mapping.element_id)
        if parameter is not None:
            counts[parameter] += 1
    singular = tuple(
        sorted((math.sqrt(value) for value in counts.values()), reverse=True)
    )
    limit = singular[0] * threshold if singular else 0.0
    rank = sum(value > limit for value in singular) if limit > 0.0 else 0
    return parameters, singular, rank, len(parameters)


def build_mapping(request: MappingRequest, canonical: bytes) -> MappingResult:
    array = _validate_canonical(request, canonical)
    rotation, translation = _validate_transform(request)
    elements = _elements(request.variant)
    held_out_rows = set(request.held_out_row_indices)
    thresholds = request.thresholds

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
        point = rotation @ point_observation + translation
        if not np.isfinite(point).all():
            raise ScansorError(
                "observation-to-model transform produced nonfinite coordinates"
            )
        observation_id = _identifier("observation", request, row)
        normal = _normal_diagnostic(array, row)
        if row in held_out_rows:
            held_out.append(
                ObservationRecord(
                    evaluation_state="post-fit-evaluation/not-evaluated",
                    normal=normal,
                    observation_id=observation_id,
                    point_model_m=(
                        float(point[0]),
                        float(point[1]),
                        float(point[2]),
                    ),
                    role="held-out",
                    row_index=row,
                )
            )
            continue
        if normal.magnitude is not None:
            training_normal_magnitudes.append(normal.magnitude)

        support = assess_nominal_support(
            (float(point[0]), float(point[1]), float(point[2])),
            request.variant,
            thresholds,
        )
        row_candidates: list[CandidateRecord] = []
        for assessed in support.candidates:
            candidate_id = _identifier("candidate", request, row, assessed.element_id)
            row_candidates.append(
                CandidateRecord(
                    absolute_distance_m=assessed.absolute_distance_m,
                    candidate_id=candidate_id,
                    element_id=assessed.element_id,
                    geometric_clearance_m=None,
                    kind=assessed.kind,
                    observation_id=observation_id,
                    row_index=row,
                    signed_distance_m=assessed.signed_distance_m,
                )
            )
        clearance = support.geometric_clearance_m
        row_candidates = [
            item.model_copy(
                update={
                    "geometric_clearance_m": clearance if index == 0 else None,
                }
            )
            for index, item in enumerate(row_candidates)
        ]
        candidates.extend(row_candidates)
        memberships.extend(
            MembershipRecord(
                candidate_id=item.candidate_id,
                element_id=item.element_id,
                membership_id=_identifier("membership", request, row, item.element_id),
                observation_id=observation_id,
            )
            for item in row_candidates
        )

        if support.outcome == "assigned":
            selected = row_candidates[0]
            observations.append(
                ObservationRecord(
                    evaluation_state="training-mapped",
                    normal=normal,
                    observation_id=observation_id,
                    point_model_m=(
                        float(point[0]),
                        float(point[1]),
                        float(point[2]),
                    ),
                    role="training",
                    row_index=row,
                )
            )
            mappings.append(
                MappingRecord(
                    candidate_id=selected.candidate_id,
                    element_id=selected.element_id,
                    mapping_id=_identifier(
                        "mapping", request, row, selected.element_id
                    ),
                    observation_id=observation_id,
                )
            )
            continue

        reason: Literal["ambiguous", "gap", "outlier", "transition"]
        if support.outcome == "ambiguous":
            reason = "ambiguous"
        elif support.outcome == "outlier":
            reason = "outlier"
        elif support.outcome == "transition":
            reason = "transition"
        else:
            reason = "gap"
        exclusions.append(
            ExclusionRecord(
                candidate_ids=tuple(item.candidate_id for item in row_candidates),
                exclusion_id=_identifier("exclusion", request, row, reason),
                observation_id=observation_id,
                reason=reason,
                row_index=row,
            )
        )

    element_counts = {element.element_id: 0 for element in elements}
    for mapping in mappings:
        element_counts[mapping.element_id] += 1
    missing = tuple(
        element_id
        for element_id, count in element_counts.items()
        if count < thresholds.minimum_region_samples
    )
    parameter_order, singular, rank_value, rank_required = _rank(
        mappings, request.variant, thresholds.rank_relative_threshold
    )
    exclusion_counts = {
        reason: 0 for reason in ("ambiguous", "gap", "outlier", "transition")
    }
    for exclusion in exclusions:
        exclusion_counts[exclusion.reason] += 1
    reasons: list[str] = []
    reasons.extend(reason for reason in exclusion_counts if exclusion_counts[reason])
    if missing:
        reasons.append("missing-required-regions")
    if rank_value < rank_required:
        reasons.append("rank-deficient")
    normal_bounds = (
        (min(training_normal_magnitudes), max(training_normal_magnitudes))
        if training_normal_magnitudes
        else None
    )
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
        exclusion_counts=exclusion_counts,
        held_out_leakage=HeldOutLeakageAudit(),
        missing_required_regions=missing,
        normal_magnitude_bounds=normal_bounds,
        per_element_training_mapping_counts=element_counts,
        rank_parameter_order=parameter_order,
        rank_relative_threshold=thresholds.rank_relative_threshold,
        rank_required=rank_required,
        rank_singular_values=singular,
        rank_value=rank_value,
        rejection_reasons=tuple(reasons),
    )
    semantic = {
        "active_factor_ids": [],
        "cad_evidence": None,
        "candidates": [item.model_dump(mode="json") for item in candidates],
        "canonical_cloud": "referenced-canonical.npy",
        "diagnostics": diagnostics.model_dump(mode="json"),
        "disposition": "rejected" if reasons else "accepted",
        "exclusions": [item.model_dump(mode="json") for item in exclusions],
        "fit_result": None,
        "format": "scansor-stepped-rotational-v0-mapping-v1",
        "format_status": "internal/provisional/non-public-contract",
        "future_physical_reference": None,
        "held_out_observations": [item.model_dump(mode="json") for item in held_out],
        "instantiated_factors": None,
        "mappings": [item.model_dump(mode="json") for item in mappings],
        "memberships": [item.model_dump(mode="json") for item in memberships],
        "observations": [item.model_dump(mode="json") for item in observations],
        "raw_cloud": "referenced-by-inspection-report",
        "request": request.model_dump(mode="json"),
    }
    mapping_run_id = sha256(canonical_json(semantic))
    return MappingResult(
        candidates=tuple(candidates),
        diagnostics=diagnostics,
        disposition="rejected" if reasons else "accepted",
        exclusions=tuple(exclusions),
        held_out_observations=tuple(held_out),
        mapping_run_id=mapping_run_id,
        mappings=tuple(mappings),
        memberships=tuple(memberships),
        observations=tuple(observations),
        request=request,
    )
