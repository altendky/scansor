from __future__ import annotations

import math
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.models import StrictModel
from scansor.serialization import canonical_json, sha256

MAPPING_FORMAT = "scansor-stepped-rotational-v0-mapping-v1"
MAPPING_MANIFEST_FORMAT = "scansor-stepped-rotational-v0-manifest-v1"


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
    minimum_region_samples: int = Field(default=3, ge=1, le=1000)
    rank_relative_threshold: float = Field(default=1e-10, gt=0.0, lt=1.0)
    transform_tolerance: float = Field(default=1e-10, gt=0.0, le=1e-6)
    transition_guard_m: float = Field(default=0.0005, gt=0.0, le=0.002)


class MappingRequest(MappingStrictModel):
    contract: Literal["stepped-rotational-v0"] = "stepped-rotational-v0"
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
    def validate_held_out_rows(self) -> MappingRequest:
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
        return self


class NormalDiagnostic(MappingStrictModel):
    magnitude: float | None
    source_present: bool
    trust: Literal["untrusted-diagnostic-only"] = "untrusted-diagnostic-only"


class ObservationRecord(MappingStrictModel):
    evaluation_state: Literal["training-mapped", "post-fit-evaluation/not-evaluated"]
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
    kind: Literal["cylindrical", "axial-planar", "datum-planar"]
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    row_index: int = Field(ge=0)
    signed_distance_m: float


class MembershipRecord(MappingStrictModel):
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    element_id: str
    membership_id: str = Field(pattern=r"^membership\.[0-9a-f]{24}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")


class MappingRecord(MappingStrictModel):
    candidate_id: str = Field(pattern=r"^candidate\.[0-9a-f]{24}$")
    element_id: str
    mapping_id: str = Field(pattern=r"^mapping\.[0-9a-f]{24}$")
    observation_id: str = Field(pattern=r"^observation\.[0-9a-f]{24}$")
    role: Literal["primary-geometric"] = "primary-geometric"


class ExclusionRecord(MappingStrictModel):
    candidate_ids: tuple[str, ...]
    exclusion_id: str = Field(pattern=r"^exclusion\.[0-9a-f]{24}$")
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
    exclusion_counts: dict[str, int]
    held_out_leakage: HeldOutLeakageAudit
    missing_required_regions: tuple[str, ...]
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

    @field_validator(
        "missing_required_regions",
        "rank_parameter_order",
        "rank_singular_values",
        "rejection_reasons",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("normal_magnitude_bounds", mode="before")
    @classmethod
    def restore_normal_bounds(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class MappingResult(MappingStrictModel):
    active_factor_ids: tuple[()] = ()
    cad_evidence: None = None
    candidates: tuple[CandidateRecord, ...]
    diagnostics: MappingDiagnostics
    disposition: Literal["accepted", "rejected"]
    exclusions: tuple[ExclusionRecord, ...]
    fit_result: None = None
    format: Literal["scansor-stepped-rotational-v0-mapping-v1"] = MAPPING_FORMAT
    format_status: Literal["internal/provisional/non-public-contract"] = (
        "internal/provisional/non-public-contract"
    )
    future_physical_reference: None = None
    held_out_observations: tuple[ObservationRecord, ...]
    instantiated_factors: None = None
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    mappings: tuple[MappingRecord, ...]
    memberships: tuple[MembershipRecord, ...]
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
            "training": (
                self.request.input_revision.canonical_row_count
                - len(self.held_out_observations)
            ),
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
        expected_element_counts = {
            element_id: sum(item.element_id == element_id for item in self.mappings)
            for element_id in self.diagnostics.per_element_training_mapping_counts
        }
        if (
            self.diagnostics.per_element_training_mapping_counts
            != expected_element_counts
        ):
            raise ValueError("per-element mapping counts are inconsistent")
        elements = (
            "cylinder.band-1",
            "cylinder.band-2",
            "cylinder.band-3",
            "plane.station-0",
            "plane.station-20",
            "plane.station-50",
            "plane.station-80",
        )
        parameters = (
            "radius.band-1",
            "radius.band-2",
            "radius.band-3",
            "station-20",
            "station-50",
            "station-80",
        )
        if self.request.variant == "asymmetric-datum-flat":
            elements += ("plane.datum-flat",)
            parameters += ("datum-flat-x",)
        if set(expected_element_counts) != set(elements):
            raise ValueError("per-element diagnostic inventory is invalid")
        missing = tuple(
            element
            for element in elements
            if expected_element_counts[element]
            < self.request.thresholds.minimum_region_samples
        )
        if self.diagnostics.missing_required_regions != missing:
            raise ValueError("missing-region diagnostics are inconsistent")
        parameter_elements = {
            "radius.band-1": "cylinder.band-1",
            "radius.band-2": "cylinder.band-2",
            "radius.band-3": "cylinder.band-3",
            "station-20": "plane.station-20",
            "station-50": "plane.station-50",
            "station-80": "plane.station-80",
            "datum-flat-x": "plane.datum-flat",
        }
        singular = tuple(
            sorted(
                (
                    math.sqrt(expected_element_counts[parameter_elements[parameter]])
                    for parameter in parameters
                ),
                reverse=True,
            )
        )
        rank_limit = (
            singular[0] * self.request.thresholds.rank_relative_threshold
            if singular
            else 0.0
        )
        rank = sum(value > rank_limit for value in singular) if rank_limit else 0
        if (
            self.diagnostics.rank_parameter_order != parameters
            or self.diagnostics.rank_singular_values != singular
            or self.diagnostics.rank_value != rank
            or self.diagnostics.rank_required != len(parameters)
            or self.diagnostics.rank_relative_threshold
            != self.request.thresholds.rank_relative_threshold
        ):
            raise ValueError("rank diagnostics are inconsistent")
        expected_reasons = tuple(
            [
                reason
                for reason in ("ambiguous", "gap", "outlier", "transition")
                if expected_exclusion_counts[reason]
            ]
            + (["missing-required-regions"] if missing else [])
            + (["rank-deficient"] if rank < len(parameters) else [])
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
    external_input: InputRevision
    format: Literal["scansor-stepped-rotational-v0-manifest-v1"] = (
        MAPPING_MANIFEST_FORMAT
    )
    format_status: Literal["internal/provisional/non-public-contract"] = (
        "internal/provisional/non-public-contract"
    )
    mapping_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
