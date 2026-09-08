from __future__ import annotations

from typing import ClassVar, Literal

import numpy as np
from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.errors import ScansorError
from scansor.geometry_evaluator import (
    DeclaredGeometryEvaluator,
)
from scansor.model_declarations import ModelDeclaration, revalidate_model_declaration
from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

MAPPING_FORMAT = "scansor-declared-analytic-model-mapping-v1"
MAPPING_MANIFEST_FORMAT = "scansor-declared-analytic-model-mapping-manifest-v1"
MAPPING_CONTRACT = "declared-analytic-fixed-pose-mapping-v1"
MAPPING_STATUS = (
    "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
)
ModelId = str


class MappingStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class SyntheticFixtureProvenance(MappingStrictModel):
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_id: Literal["stepped-rotational-v0-synthetic-fixture"] = (
        "stepped-rotational-v0-synthetic-fixture"
    )
    revision: Literal["1"] = "1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant: Literal["axisymmetric", "asymmetric-datum-flat"]


class GeneratedFixtureRow(MappingStrictModel):
    fixture_observation_id: str = Field(pattern=r"^fixture-observation\.[0-9a-f]{24}$")
    role: Literal["training", "held-out"]
    row_index: int = Field(ge=0)


class GeneratedSyntheticFixtureProvenance(MappingStrictModel):
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_id: Literal["stepped-rotational-v0-synthetic-fixture"] = (
        "stepped-rotational-v0-synthetic-fixture"
    )
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_revision: Literal["provisional-1"] = "provisional-1"
    held_out_row_indices: tuple[int, ...]
    noise_clip_sigma: Literal[4] = 4
    noise_model: Literal["bounded-normal-v1"] = "bounded-normal-v1"
    noise_quantum_m: float = Field(default=1e-9, ge=1e-9, le=1e-9)
    noise_sigma_m: float = Field(gt=0.0, le=25e-6)
    outlier_policy: Literal["none"] = "none"
    revision: Literal["2"] = "2"
    rows: tuple[GeneratedFixtureRow, ...]
    sampling_profile: Literal["guarded-grid-v1"] = "guarded-grid-v1"
    seed: int = Field(ge=0, le=2**63 - 1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    variant: Literal["asymmetric-datum-flat"] = "asymmetric-datum-flat"

    @field_validator("held_out_row_indices", "rows", mode="before")
    @classmethod
    def restore_generated_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_generated_rows(self) -> GeneratedSyntheticFixtureProvenance:
        if tuple(row.row_index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("generated fixture rows are not canonical")
        held_out = tuple(row.row_index for row in self.rows if row.role == "held-out")
        if held_out != self.held_out_row_indices:
            raise ValueError("generated fixture held-out roles disagree")
        identifiers = tuple(row.fixture_observation_id for row in self.rows)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("generated fixture IDs are duplicated")
        return self


FixtureProvenance = SyntheticFixtureProvenance | GeneratedSyntheticFixtureProvenance


class InputRevision(MappingStrictModel):
    canonical_row_count: int = Field(gt=0)
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_unit: Literal["m"] = "m"
    inspection_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inspection_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_frame: str = Field(min_length=1, pattern=r".*\S.*")
    synthetic_fixture: FixtureProvenance

    @model_validator(mode="after")
    def bind_fixture_canonical_hash(self) -> InputRevision:
        if self.synthetic_fixture.canonical_sha256 != self.canonical_sha256:
            raise ValueError("synthetic fixture and input canonical hashes differ")
        if (
            self.synthetic_fixture.revision == "2"
            and len(self.synthetic_fixture.rows) != self.canonical_row_count
        ):
            raise ValueError("generated fixture rows disagree with canonical row count")
        return self


class RigidTransform(MappingStrictModel):
    direction: Literal["observation-to-model"] = "observation-to-model"
    rotation: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]
    scale: float = 1.0
    translation_m: tuple[float, float, float]

    @field_validator("rotation", "translation_m", mode="before")
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(
                tuple(item) if isinstance(item, list) else item for item in value
            )
        return value

    @model_validator(mode="after")
    def validate_scale(self) -> RigidTransform:
        if self.scale != 1.0:
            raise ValueError("rigid transform scale must be exactly 1")
        return self


class MappingThresholds(MappingStrictModel):
    max_support_distance_m: float = Field(default=0.00025, gt=0.0, le=0.002)
    minimum_geometric_clearance_m: float = Field(default=0.0001, gt=0.0, le=0.002)
    transform_tolerance: float = Field(default=1e-10, gt=0.0, le=1e-6)
    transition_guard_m: float = Field(default=0.0005, gt=0.0, le=0.002)


class MappingRequest(MappingStrictModel):
    contract: Literal["declared-analytic-fixed-pose-mapping-v1"] = MAPPING_CONTRACT
    declaration: ModelDeclaration
    held_out_row_indices: tuple[int, ...] = ()
    input_revision: InputRevision
    thresholds: MappingThresholds = Field(default_factory=MappingThresholds)
    transform: RigidTransform
    variant: Literal["axisymmetric", "asymmetric-datum-flat"]

    @field_validator("held_out_row_indices", mode="before")
    @classmethod
    def restore_indices(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_request(self) -> MappingRequest:
        rows = self.held_out_row_indices
        if tuple(sorted(set(rows))) != rows:
            raise ValueError("held-out row indices must be unique and sorted")
        if any(
            row < 0 or row >= self.input_revision.canonical_row_count for row in rows
        ):
            raise ValueError("held-out row index is outside the canonical revision")
        if len(rows) >= self.input_revision.canonical_row_count:
            raise ValueError("at least one training row is required")
        if self.input_revision.synthetic_fixture.variant != self.variant:
            raise ValueError("synthetic fixture and mapping variants differ")
        try:
            revalidated = revalidate_model_declaration(self.declaration)
        except ScansorError as error:
            raise ValueError(str(error)) from error
        if revalidated != self.declaration:
            raise ValueError("mapping model declaration did not revalidate exactly")
        return self


class NormalDiagnostic(MappingStrictModel):
    magnitude: float | None
    source_present: bool
    trust: Literal["untrusted-diagnostic-only"] = "untrusted-diagnostic-only"


class ObservationRecord(MappingStrictModel):
    evaluation_state: Literal["training-mapped", "post-fit-evaluation/not-evaluated"]
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    normal: NormalDiagnostic
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    point_model_m: tuple[float, float, float]
    role: Literal["training", "held-out"]
    row_index: int = Field(ge=0)

    @field_validator("point_model_m", mode="before")
    @classmethod
    def restore_point(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class CandidateRecord(MappingStrictModel):
    absolute_distance_m: float = Field(ge=0.0)
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    element_id: str
    geometric_clearance_m: float | None = Field(default=None, ge=0.0)
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    row_index: int = Field(ge=0)
    signed_distance_m: float


class MembershipRecord(MappingStrictModel):
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    element_id: str
    membership_id: str = Field(pattern=r"^membership\.[0-9a-f]{24}$")
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")


class MappingRecord(MappingStrictModel):
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    element_id: str
    mapping_id: str = Field(pattern=r"^mapping\.[0-9a-f]{24}$")
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    role: Literal["primary-geometric"] = "primary-geometric"


class ExclusionRecord(MappingStrictModel):
    candidate_ids: tuple[str, ...]
    exclusion_id: str = Field(pattern=r"^exclusion\.[0-9a-f]{24}$")
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    reason: Literal["ambiguous", "gap", "outlier", "transition"]
    row_index: int = Field(ge=0)

    @field_validator("candidate_ids", mode="before")
    @classmethod
    def restore_candidate_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class HeldOutLeakageAudit(MappingStrictModel):
    active_factor_ids: tuple[()] = ()
    bound_row_indices: tuple[()] = ()
    candidate_row_indices: tuple[()] = ()
    coverage_row_indices: tuple[()] = ()
    factor_row_indices: tuple[()] = ()
    initialization_row_indices: tuple[()] = ()
    loss_row_indices: tuple[()] = ()
    mapping_row_indices: tuple[()] = ()
    rank_row_indices: tuple[()] = ()
    refinement_row_indices: tuple[()] = ()
    threshold_tuning_row_indices: tuple[()] = ()
    weight_row_indices: tuple[()] = ()

    @field_validator("*", mode="before")
    @classmethod
    def restore_empty_audit_fields(cls, value: object) -> object:
        return () if value == [] else value


class MappingDiagnostics(MappingStrictModel):
    counts: dict[str, int]
    coverage_cell_counts: dict[str, int]
    coverage_cell_order: tuple[str, ...]
    exclusion_counts: dict[str, int]
    held_out_leakage: HeldOutLeakageAudit
    missing_coverage_cells: tuple[str, ...]
    missing_required_support: tuple[str, ...]
    normal_magnitude_bounds: tuple[float, float] | None
    normal_policy: Literal[
        "missing-allowed; present-untrusted-diagnostic-only; never-classifying"
    ] = "missing-allowed; present-untrusted-diagnostic-only; never-classifying"
    per_element_training_mapping_counts: dict[str, int]
    rank_parameter_order: tuple[str, ...]
    rank_relative_threshold: float
    rank_required: int = Field(ge=0)
    rank_singular_values: tuple[float, ...]
    rank_training_only: Literal[True] = True
    rank_value: int = Field(ge=0)
    rejection_reasons: tuple[str, ...]
    required_support_counts: dict[str, int]
    required_support_order: tuple[str, ...]

    @field_validator(
        "missing_coverage_cells",
        "missing_required_support",
        "coverage_cell_order",
        "rank_parameter_order",
        "rank_singular_values",
        "rejection_reasons",
        "required_support_order",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("normal_magnitude_bounds", mode="before")
    @classmethod
    def restore_normal_bounds(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


def mapping_admission_diagnostics(
    declaration: ModelDeclaration,
    mappings: tuple[MappingRecord, ...] | list[MappingRecord],
    observations: tuple[ObservationRecord, ...] | list[ObservationRecord],
) -> tuple[
    dict[str, int],
    dict[str, int],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[float, ...],
    int,
    int,
]:
    policy = declaration.mapping_admission
    element_counts = {element.element_id: 0 for element in declaration.elements}
    for mapping in mappings:
        element_counts[mapping.element_id] += 1
    required_counts = {
        item.element_id: element_counts[item.element_id]
        for item in policy.required_support
    }
    missing_support = tuple(
        item.element_id
        for item in policy.required_support
        if required_counts[item.element_id] < item.minimum_count
    )
    observation_by_id = {item.observation_id: item for item in observations}
    nominal = tuple(item.nominal for item in declaration.parameters)
    evaluator = DeclaredGeometryEvaluator(declaration, nominal)
    coverage_counts = {cell.cell_id: 0 for cell in policy.coverage_cells}
    for mapping in mappings:
        point = observation_by_id[mapping.observation_id].point_model_m
        for cell in policy.coverage_cells:
            if (
                cell.element_id == mapping.element_id
                and evaluator.classify_coverage(
                    mapping.element_id, cell.domain, point
                ).projected_inside
            ):
                coverage_counts[cell.cell_id] += 1
    missing_coverage = tuple(
        cell.cell_id
        for cell in policy.coverage_cells
        if coverage_counts[cell.cell_id] < cell.minimum_count
    )
    rank_policy = policy.relative_rank
    parameter_indices = {
        item.parameter_id: index for index, item in enumerate(declaration.parameters)
    }
    rows: list[list[float]] = []
    for mapping in mappings:
        point = observation_by_id[mapping.observation_id].point_model_m
        jacobian = evaluator.evaluate_fixed_pose_shape(
            mapping.element_id, point
        ).parameter_jacobian_row
        rows.append(
            [
                jacobian[parameter_indices[parameter_id]]
                * scale
                / rank_policy.residual_scale
                for parameter_id, scale in zip(
                    rank_policy.parameter_ids,
                    rank_policy.parameter_scales,
                    strict=True,
                )
            ]
        )
    if rows and rank_policy.parameter_ids:
        singular = tuple(float(item) for item in np.linalg.svd(rows, compute_uv=False))
    else:
        singular = ()
    limit = singular[0] * rank_policy.relative_threshold if singular else 0.0
    rank = sum(item > limit for item in singular) if limit > 0.0 else 0
    return (
        required_counts,
        coverage_counts,
        missing_support,
        missing_coverage,
        rank_policy.parameter_ids,
        singular,
        rank,
        rank_policy.required_rank,
    )


class MappingResult(MappingStrictModel):
    active_factor_ids: tuple[()] = ()
    cad_evidence: None = None
    candidates: tuple[CandidateRecord, ...]
    diagnostics: MappingDiagnostics
    disposition: Literal["accepted", "rejected"]
    exclusions: tuple[ExclusionRecord, ...]
    fit_result: None = None
    format: Literal["scansor-declared-analytic-model-mapping-v1"] = MAPPING_FORMAT
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = MAPPING_STATUS
    future_physical_reference: None = None
    held_out_observations: tuple[ObservationRecord, ...]
    instantiated_factors: None = None
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    mappings: tuple[MappingRecord, ...]
    memberships: tuple[MembershipRecord, ...]
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")
    observations: tuple[ObservationRecord, ...]
    raw_cloud: Literal["referenced-by-inspection-report"] = (
        "referenced-by-inspection-report"
    )
    canonical_cloud: Literal["referenced-canonical.npy"] = "referenced-canonical.npy"
    request: MappingRequest

    @field_validator(
        "active_factor_ids",
        "candidates",
        "exclusions",
        "held_out_observations",
        "mappings",
        "memberships",
        "observations",
        mode="before",
    )
    @classmethod
    def restore_records(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_record_graph(self) -> MappingResult:
        model_id = self.request.declaration.model_id
        records = (
            *self.observations,
            *self.held_out_observations,
            *self.candidates,
            *self.memberships,
            *self.mappings,
            *self.exclusions,
        )
        if self.model_id != model_id or any(
            item.model_id != model_id for item in records
        ):
            raise ValueError("mapping records mix model identities")
        observation_ids = [item.observation_id for item in self.observations]
        held_out_ids = [item.observation_id for item in self.held_out_observations]
        excluded_ids = [item.observation_id for item in self.exclusions]
        candidate_ids = [item.candidate_id for item in self.candidates]
        collections = {
            "observation": observation_ids + held_out_ids + excluded_ids,
            "candidate": candidate_ids,
            "membership": [item.membership_id for item in self.memberships],
            "mapping": [item.mapping_id for item in self.mappings],
            "exclusion": [item.exclusion_id for item in self.exclusions],
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"duplicate {label} identifier")
        known_observations = set(collections["observation"])
        known_candidates = set(candidate_ids)
        if any(
            item.observation_id not in known_observations for item in self.candidates
        ):
            raise ValueError("candidate references an unknown observation")
        if any(
            item.observation_id not in known_observations
            or item.candidate_id not in known_candidates
            for item in (*self.memberships, *self.mappings)
        ):
            raise ValueError("membership or mapping reference is unresolved")
        if any(
            candidate_id not in known_candidates
            for item in self.exclusions
            for candidate_id in item.candidate_ids
        ):
            raise ValueError("exclusion references an unknown candidate")
        held_out = set(held_out_ids)
        if held_out & {
            item.observation_id
            for item in (*self.candidates, *self.memberships, *self.mappings)
        }:
            raise ValueError("held-out observation leaked into mapping-derived records")
        if any(
            item.role != "training" or item.evaluation_state != "training-mapped"
            for item in self.observations
        ) or any(
            item.role != "held-out"
            or item.evaluation_state != "post-fit-evaluation/not-evaluated"
            for item in self.held_out_observations
        ):
            raise ValueError("observation role and evaluation state disagree")
        observation_rows = {
            item.observation_id: item.row_index
            for item in (
                *self.observations,
                *self.held_out_observations,
                *self.exclusions,
            )
        }
        if any(
            item.row_index != observation_rows[item.observation_id]
            for item in self.candidates
        ) or any(
            item.row_index != observation_rows[item.observation_id]
            for item in self.exclusions
        ):
            raise ValueError("record row index disagrees with its observation")
        all_rows = [
            *(item.row_index for item in self.observations),
            *(item.row_index for item in self.held_out_observations),
            *(item.row_index for item in self.exclusions),
        ]
        if sorted(all_rows) != list(
            range(self.request.input_revision.canonical_row_count)
        ):
            raise ValueError("canonical rows are not partitioned exactly once")
        held_out_rows = set(self.request.held_out_row_indices)
        if held_out_rows & {
            item.row_index for item in (*self.candidates, *self.exclusions)
        }:
            raise ValueError("held-out row leaked into training-derived records")
        if self.diagnostics.counts != {
            "candidate": len(self.candidates),
            "canonical": self.request.input_revision.canonical_row_count,
            "exclusion": len(self.exclusions),
            "held_out": len(self.held_out_observations),
            "mapping": len(self.mappings),
            "membership": len(self.memberships),
            "observation": len(self.observations),
            "training": self.request.input_revision.canonical_row_count
            - len(self.held_out_observations),
        }:
            raise ValueError("mapping diagnostic counts are inconsistent")
        expected = "rejected" if self.diagnostics.rejection_reasons else "accepted"
        if self.disposition != expected:
            raise ValueError("mapping disposition and rejection reasons disagree")
        if [item.row_index for item in self.observations] != sorted(
            item.row_index for item in self.observations
        ) or [item.row_index for item in self.held_out_observations] != list(
            self.request.held_out_row_indices
        ):
            raise ValueError("observation records are not in canonical row order")
        if [item.row_index for item in self.exclusions] != sorted(
            item.row_index for item in self.exclusions
        ):
            raise ValueError("exclusions are not in canonical row order")
        candidate_order = [
            (item.row_index, item.absolute_distance_m, item.element_id)
            for item in self.candidates
        ]
        if candidate_order != sorted(candidate_order):
            raise ValueError("candidates are not in deterministic order")
        candidate_by_id = {item.candidate_id: item for item in self.candidates}
        if len(self.memberships) != len(self.candidates) or any(
            item.element_id != candidate_by_id[item.candidate_id].element_id
            or item.observation_id != candidate_by_id[item.candidate_id].observation_id
            for item in self.memberships
        ):
            raise ValueError("candidate memberships are inconsistent")
        mapped_observations = {item.observation_id for item in self.observations}
        if (
            len(self.mappings) != len(self.observations)
            or {item.observation_id for item in self.mappings} != mapped_observations
            or any(
                item.element_id != candidate_by_id[item.candidate_id].element_id
                or item.observation_id
                != candidate_by_id[item.candidate_id].observation_id
                for item in self.mappings
            )
        ):
            raise ValueError("primary mappings are inconsistent")
        expected_exclusion_counts = {
            reason: sum(item.reason == reason for item in self.exclusions)
            for reason in ("ambiguous", "gap", "outlier", "transition")
        }
        if self.diagnostics.exclusion_counts != expected_exclusion_counts:
            raise ValueError("exclusion diagnostics are inconsistent")
        try:
            (
                required_counts,
                coverage_counts,
                missing_support,
                missing_coverage,
                parameters,
                singular,
                rank,
                rank_required,
            ) = mapping_admission_diagnostics(
                self.request.declaration, self.mappings, self.observations
            )
        except (KeyError, ScansorError, ValueError) as error:
            raise ValueError(
                f"mapping admission diagnostics are invalid: {error}"
            ) from error
        element_counts = {
            element.element_id: sum(
                item.element_id == element.element_id for item in self.mappings
            )
            for element in self.request.declaration.elements
        }
        rank_policy = self.request.declaration.mapping_admission.relative_rank
        if (
            self.diagnostics.per_element_training_mapping_counts != element_counts
            or self.diagnostics.required_support_counts != required_counts
            or self.diagnostics.coverage_cell_counts != coverage_counts
            or self.diagnostics.required_support_order != tuple(required_counts)
            or self.diagnostics.coverage_cell_order != tuple(coverage_counts)
            or self.diagnostics.missing_required_support != missing_support
            or self.diagnostics.missing_coverage_cells != missing_coverage
        ):
            raise ValueError("mapping admission counts are inconsistent")
        if (
            self.diagnostics.rank_parameter_order != parameters
            or self.diagnostics.rank_singular_values != singular
            or self.diagnostics.rank_value != rank
            or self.diagnostics.rank_required != rank_required
            or self.diagnostics.rank_relative_threshold
            != rank_policy.relative_threshold
        ):
            raise ValueError("rank diagnostics are inconsistent")
        expected_reasons = tuple(
            [
                reason
                for reason in ("ambiguous", "gap", "outlier", "transition")
                if expected_exclusion_counts[reason]
            ]
            + (["missing-required-support"] if missing_support else [])
            + (["insufficient-coverage"] if missing_coverage else [])
            + (["rank-deficient"] if rank < rank_required else [])
        )
        if self.diagnostics.rejection_reasons != expected_reasons:
            raise ValueError("rejection reasons are inconsistent")
        expected_run_id = sha256(
            canonical_json(self.model_dump(exclude={"mapping_run_id"}, mode="json"))
        )
        if self.mapping_run_id != expected_run_id:
            raise ValueError("mapping run ID does not match semantic content")
        return self


class ArtifactRecord(MappingStrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class MappingManifest(MappingStrictModel):
    artifacts: dict[Literal["mapping.json"], ArtifactRecord]
    declaration: ModelDeclaration
    external_input: InputRevision
    format: Literal["scansor-declared-analytic-model-mapping-manifest-v1"] = (
        MAPPING_MANIFEST_FORMAT
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = MAPPING_STATUS
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: ModelId = Field(pattern=r"^model\.[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_model_binding(self) -> MappingManifest:
        if self.model_id != self.declaration.model_id:
            raise ValueError("mapping manifest model binding is inconsistent")
        return self
