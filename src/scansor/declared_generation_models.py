from __future__ import annotations

from typing import ClassVar, Literal

import numpy as np
from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.errors import ScansorError
from scansor.model_declarations import ModelDeclaration, revalidate_model_declaration
from scansor.models import StrictModel
from scansor.rigid_transform import RigidTransform
from scansor.serialization import canonical_json, sha256

GENERATION_FORMAT_STATUS = (
    "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
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
MAX_GENERATION_ROWS = 20_000
MAX_NOISE_SIGMA_M = 25e-6


class GenerationStrictModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


def _validate_declaration(declaration: ModelDeclaration, model_id: str) -> None:
    try:
        exact = revalidate_model_declaration(declaration)
    except ScansorError as error:
        raise ValueError(f"generation model declaration is invalid: {error}") from error
    if exact != declaration or model_id != exact.model_id:
        raise ValueError("generation model binding is inconsistent")


class FixtureSample(GenerationStrictModel):
    element_id: str = Field(min_length=1)
    key: str = Field(min_length=1)
    normal_model: tuple[float, float, float]
    point_model_m: tuple[float, float, float]
    role: Literal["training", "held-out"]

    @field_validator("normal_model", "point_model_m", mode="before")
    @classmethod
    def restore_vectors(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class FixtureDefinition(GenerationStrictModel):
    """Project-owned sampling definition; authoring is not an admission capability."""

    declaration: ModelDeclaration
    fixture_id: str = Field(min_length=1)
    revision: Literal["provisional-1"] = "provisional-1"
    samples: tuple[FixtureSample, ...] = Field(
        min_length=2, max_length=MAX_GENERATION_ROWS
    )
    source_frame: str = Field(min_length=1)
    transform: RigidTransform

    @field_validator("samples", mode="before")
    @classmethod
    def restore_samples(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class GenerationRequest(GenerationStrictModel):
    declaration: ModelDeclaration
    element_ids: tuple[str, ...]
    fixture_id: str = Field(min_length=1)
    fixture_revision: Literal["provisional-1"] = "provisional-1"
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    noise_sigma_m: float = Field(gt=0.0, le=MAX_NOISE_SIGMA_M)
    parameter_order: tuple[str, ...]
    sampling_profile: Literal["guarded-grid-v1"] = "guarded-grid-v1"
    seed: int = Field(ge=0, le=2**63 - 1)
    source_frame: str = Field(min_length=1)
    transform: RigidTransform

    @field_validator("element_ids", "parameter_order", mode="before")
    @classmethod
    def restore_orders(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_request(self) -> GenerationRequest:
        _validate_declaration(self.declaration, self.model_id)
        if self.parameter_order != tuple(
            p.parameter_id for p in self.declaration.parameters
        ):
            raise ValueError("generation parameter order disagrees with declaration")
        if self.element_ids != tuple(e.element_id for e in self.declaration.elements):
            raise ValueError("generation element order disagrees with declaration")
        rotation = np.asarray(self.transform.rotation, dtype=np.float64)
        if (
            float(np.max(np.abs(rotation.T @ rotation - np.eye(3)))) > 1e-10
            or abs(float(np.linalg.det(rotation)) - 1.0) > 1e-10
        ):
            raise ValueError("generation pose rotation must be proper and orthonormal")
        return self


class GenerationRowProvenance(GenerationStrictModel):
    expected_element_id: str = Field(min_length=1)
    fixture_observation_id: str = Field(pattern=r"^fixture-observation\.[0-9a-f]{24}$")
    role: Literal["training", "held-out"]
    row_index: int = Field(ge=0)


class GenerationSource(GenerationStrictModel):
    byte_count: int = Field(gt=0, le=MAX_GENERATION_PLY_BYTES)
    fields: tuple[Literal["x", "y", "z"], ...] = ("x", "y", "z")
    filename: Literal["observations.ply"] = "observations.ply"
    frame: str = Field(min_length=1)
    scalar_type: Literal["float64"] = "float64"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: Literal["m"] = "m"

    @field_validator("fields", mode="before")
    @classmethod
    def restore_fields(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class PartitionRecord(GenerationStrictModel):
    coordinate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    count: int = Field(ge=1)
    fixture_observation_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationProvenance(GenerationStrictModel):
    format: Literal["scansor-declared-analytic-model-generated-provenance-v1"] = (
        "scansor-declared-analytic-model-generated-provenance-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    generator_revision: Literal["provisional-1"] = "provisional-1"
    held_out_row_indices: tuple[int, ...]
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    noise_clip_sigma: Literal[4] = 4
    noise_model: Literal["bounded-normal-v1"] = "bounded-normal-v1"
    noise_quantum_m: float = Field(default=1e-9, ge=1e-9, le=1e-9)
    outlier_policy: Literal["none"] = "none"
    partitions: dict[Literal["training", "held-out"], PartitionRecord]
    request: GenerationRequest
    rows: tuple[GenerationRowProvenance, ...] = Field(
        min_length=2, max_length=MAX_GENERATION_ROWS
    )
    source: GenerationSource

    @field_validator("held_out_row_indices", "rows", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_provenance(self) -> GenerationProvenance:
        if (
            self.model_id != self.request.model_id
            or self.source.frame != self.request.source_frame
        ):
            raise ValueError("generation provenance model or frame binding disagrees")
        if self.source.fields != ("x", "y", "z"):
            raise ValueError("generation source fields are not XYZ")
        if tuple(row.row_index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("generation rows are not canonical")
        identifiers = tuple(row.fixture_observation_id for row in self.rows)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("generation fixture observation IDs are duplicated")
        if any(
            row.expected_element_id not in self.request.element_ids for row in self.rows
        ):
            raise ValueError("generation expected element ID is undeclared")
        held_out = tuple(row.row_index for row in self.rows if row.role == "held-out")
        if (
            held_out != self.held_out_row_indices
            or not held_out
            or len(held_out) == len(self.rows)
        ):
            raise ValueError("generation held-out partition is inconsistent")
        if set(self.partitions) != {"training", "held-out"} or any(
            self.partitions[role].count != sum(row.role == role for row in self.rows)
            for role in ("training", "held-out")
        ):
            raise ValueError("generation partition counts are inconsistent")
        if self.generation_run_id != sha256(
            canonical_json(self.model_dump(mode="json", exclude={"generation_run_id"}))
        ):
            raise ValueError("generation run ID does not match semantic content")
        return self


class GenerationTruthRow(GenerationRowProvenance):
    analytic_normal_model: tuple[float, float, float]
    generated_point_model_m: tuple[float, float, float]
    noiseless_point_model_m: tuple[float, float, float]
    normal_noise_offset_m: float

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
    declaration: ModelDeclaration
    element_ids: tuple[str, ...]
    format: Literal["scansor-declared-analytic-model-ground-truth-v1"] = (
        "scansor-declared-analytic-model-ground-truth-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    noise_summary: NoiseSummary
    parameter_order: tuple[str, ...]
    parameter_values: tuple[float, ...]
    rows: tuple[GenerationTruthRow, ...]
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    units: Literal["metre"] = "metre"

    @field_validator(
        "element_ids", "parameter_order", "parameter_values", "rows", mode="before"
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_truth(self) -> GenerationGroundTruth:
        _validate_declaration(self.declaration, self.model_id)
        if self.parameter_order != tuple(
            p.parameter_id for p in self.declaration.parameters
        ):
            raise ValueError("truth parameter order disagrees with declaration")
        if self.parameter_values != tuple(
            p.nominal for p in self.declaration.parameters
        ):
            raise ValueError(
                "truth values disagree with the nominal fixture declaration"
            )
        if self.element_ids != tuple(e.element_id for e in self.declaration.elements):
            raise ValueError("truth element order disagrees with declaration")
        if tuple(row.row_index for row in self.rows) != tuple(range(len(self.rows))):
            raise ValueError("truth rows are not canonical")
        if any(row.expected_element_id not in self.element_ids for row in self.rows):
            raise ValueError("truth expected element ID is undeclared")
        return self


class GenerationArtifact(GenerationStrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationManifest(GenerationStrictModel):
    artifacts: dict[str, GenerationArtifact]
    declaration: ModelDeclaration
    format: Literal["scansor-declared-analytic-model-generation-run-manifest-v1"] = (
        "scansor-declared-analytic-model-generation-run-manifest-v1"
    )
    format_status: Literal[
        "internal/provisional/synthetic-only/compatibility-free/non-public-contract"
    ] = GENERATION_FORMAT_STATUS
    generation_run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_manifest(self) -> GenerationManifest:
        _validate_declaration(self.declaration, self.model_id)
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

    @model_validator(mode="after")
    def validate_graph(self) -> PreparedGeneration:
        truth, provenance = self.ground_truth, self.provenance
        if (
            truth.model_id != provenance.model_id
            or truth.declaration != provenance.request.declaration
            or truth.generation_run_id != provenance.generation_run_id
            or truth.parameter_order != provenance.request.parameter_order
            or truth.element_ids != provenance.request.element_ids
            or truth.source_sha256 != provenance.source.sha256
            or sha256(self.source) != provenance.source.sha256
            or len(self.source) != provenance.source.byte_count
            or tuple(
                GenerationRowProvenance.model_validate(
                    row.model_dump(include=set(GenerationRowProvenance.model_fields))
                )
                for row in truth.rows
            )
            != provenance.rows
        ):
            raise ValueError(
                "generated model, source, or row provenance graph is inconsistent"
            )
        return self


class DeclaredGeneratedFixtureProvenance(GenerationStrictModel):
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: GenerationProvenance
    model_id: str = Field(pattern=r"^model\.[0-9a-f]{64}$")
    revision: Literal["declared-generated-v1"] = "declared-generated-v1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def rows(self) -> tuple[GenerationRowProvenance, ...]:
        return self.generation.rows

    @property
    def held_out_row_indices(self) -> tuple[int, ...]:
        return self.generation.held_out_row_indices

    @model_validator(mode="after")
    def validate_binding(self) -> DeclaredGeneratedFixtureProvenance:
        if (
            self.model_id != self.generation.model_id
            or self.source_sha256 != self.generation.source.sha256
        ):
            raise ValueError("generated fixture model or source binding disagrees")
        if self.content_sha256 != sha256(
            canonical_json(self.model_dump(mode="json", exclude={"content_sha256"}))
        ):
            raise ValueError("generated fixture content hash disagrees")
        return self
