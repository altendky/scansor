from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.models import StrictModel

GENERATION_FORMAT_STATUS = (
    "internal/provisional/synthetic-only/fixed-topology/non-public"
)
GENERATION_RUN_FILES = frozenset(
    {
        "ground-truth.json",
        "manifest.json",
        "manifest.sha256",
        "observations.ply",
        "provenance.json",
    }
)
MAX_GENERATION_CONTROL_BYTES = 16 * 1024 * 1024
MAX_GENERATION_PLY_BYTES = 16 * 1024 * 1024
MAX_NOISE_SIGMA_M = 25e-6


class GenerationStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class GenerationRequest(GenerationStrictModel):
    noise_sigma_m: float = Field(gt=0.0, le=MAX_NOISE_SIGMA_M)
    sampling_profile: Literal["guarded-grid-v1"]
    seed: int = Field(ge=0, le=2**63 - 1)
    variant: Literal["asymmetric-datum-flat"]


class GenerationRowProvenance(GenerationStrictModel):
    fixture_observation_id: str = Field(pattern=r"^fixture-observation\.[0-9a-f]{24}$")
    role: Literal["training", "held-out"]
    row_index: int = Field(ge=0)


class GenerationSource(GenerationStrictModel):
    byte_count: int = Field(gt=0)
    fields: tuple[Literal["x", "y", "z"], ...] = ("x", "y", "z")
    filename: Literal["observations.ply"] = "observations.ply"
    frame: Literal["stepped-rotational-v0-synthetic-model-frame"] = (
        "stepped-rotational-v0-synthetic-model-frame"
    )
    scalar_type: Literal["float64"] = "float64"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: Literal["m"] = "m"

    @field_validator("fields", mode="before")
    @classmethod
    def restore_fields(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class PartitionRecord(GenerationStrictModel):
    coordinate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=0)
    fixture_observation_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationProvenance(GenerationStrictModel):
    fixture_id: Literal["stepped-rotational-v0-synthetic-fixture"] = (
        "stepped-rotational-v0-synthetic-fixture"
    )
    fixture_revision: Literal["2"] = "2"
    format: Literal["scansor-stepped-rotational-generated-provenance-v1"] = (
        "scansor-stepped-rotational-generated-provenance-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/fixed-topology/non-public"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_implementation: Literal["scansor.stepped-rotational-generator"] = (
        "scansor.stepped-rotational-generator"
    )
    generator_revision: Literal["provisional-1"] = "provisional-1"
    held_out_count: int = Field(ge=1)
    held_out_row_indices: tuple[int, ...]
    noise_clip_sigma: Literal[4] = 4
    noise_model: Literal["bounded-normal-v1"] = "bounded-normal-v1"
    noise_quantum_m: float = Field(default=1e-9, ge=1e-9, le=1e-9)
    noise_sigma_m: float = Field(gt=0.0, le=MAX_NOISE_SIGMA_M)
    outlier_policy: Literal["none"] = "none"
    partitions: dict[Literal["training", "held-out"], PartitionRecord]
    point_count: int = Field(gt=1)
    rows: tuple[GenerationRowProvenance, ...]
    sampling_profile: Literal["guarded-grid-v1"] = "guarded-grid-v1"
    seed: int = Field(ge=0, le=2**63 - 1)
    source: GenerationSource
    training_count: int = Field(ge=1)
    variant: Literal["asymmetric-datum-flat"] = "asymmetric-datum-flat"

    @field_validator("held_out_row_indices", "rows", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_rows(self) -> GenerationProvenance:
        if len(self.rows) != self.point_count or tuple(
            row.row_index for row in self.rows
        ) != tuple(range(self.point_count)):
            raise ValueError("generation provenance rows are not canonical")
        identifiers = tuple(row.fixture_observation_id for row in self.rows)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate fixture observation ID")
        held_out = tuple(row.row_index for row in self.rows if row.role == "held-out")
        if held_out != self.held_out_row_indices:
            raise ValueError("held-out row indices disagree with row roles")
        if self.training_count + self.held_out_count != self.point_count or (
            self.held_out_count != len(held_out)
        ):
            raise ValueError("generation role counts are inconsistent")
        if set(self.partitions) != {"training", "held-out"} or (
            self.partitions["training"].count != self.training_count
            or self.partitions["held-out"].count != self.held_out_count
        ):
            raise ValueError("generation partition counts are inconsistent")
        return self


class ModelTruth(GenerationStrictModel):
    parameter_order: tuple[
        Literal["r1", "r2", "r3", "s20", "s50", "s80", "datum_x"], ...
    ] = ("r1", "r2", "r3", "s20", "s50", "s80", "datum_x")
    units: Literal["metre"] = "metre"
    values: tuple[float, ...] = (0.012, 0.018, 0.014, 0.020, 0.050, 0.080, 0.016)

    @field_validator("parameter_order", "values", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class GenerationTruthRow(GenerationStrictModel):
    analytic_normal_model: tuple[float, float, float]
    expected_element_id: str
    fixture_observation_id: str = Field(pattern=r"^fixture-observation\.[0-9a-f]{24}$")
    generated_point_model_m: tuple[float, float, float]
    noiseless_point_model_m: tuple[float, float, float]
    normal_noise_offset_m: float
    role: Literal["training", "held-out"]
    row_index: int = Field(ge=0)

    @field_validator(
        "analytic_normal_model",
        "generated_point_model_m",
        "noiseless_point_model_m",
        mode="before",
    )
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class NoiseSummary(GenerationStrictModel):
    maximum_offset_m: float
    mean_offset_m: float
    minimum_offset_m: float
    root_mean_square_offset_m: float = Field(ge=0.0)


class GenerationGroundTruth(GenerationStrictModel):
    fixture_id: Literal["stepped-rotational-v0-synthetic-fixture"] = (
        "stepped-rotational-v0-synthetic-fixture"
    )
    fixture_revision: Literal["2"] = "2"
    format: Literal["scansor-stepped-rotational-ground-truth-v1"] = (
        "scansor-stepped-rotational-ground-truth-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/fixed-topology/non-public"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_truth: ModelTruth
    noise_summary: NoiseSummary
    rows: tuple[GenerationTruthRow, ...]
    variant: Literal["asymmetric-datum-flat"] = "asymmetric-datum-flat"

    @field_validator("rows", mode="before")
    @classmethod
    def restore_rows(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_rows(self) -> GenerationGroundTruth:
        if tuple(row.row_index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("ground-truth rows are not canonical")
        return self


class GenerationArtifact(GenerationStrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationManifest(GenerationStrictModel):
    artifacts: dict[str, GenerationArtifact]
    format: Literal["scansor-stepped-rotational-generation-run-manifest-v1"] = (
        "scansor-stepped-rotational-generation-run-manifest-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/fixed-topology/non-public"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_inventory(self) -> GenerationManifest:
        if set(self.artifacts) != {
            "ground-truth.json",
            "observations.ply",
            "provenance.json",
        }:
            raise ValueError("generation manifest inventory is invalid")
        return self


class PreparedGeneration(GenerationStrictModel):
    ground_truth: GenerationGroundTruth
    provenance: GenerationProvenance
    source: bytes
