from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT_STATUS = "internal/provisional/non-public-contract"
REPORT_FORMAT = "scansor-inspection-report-v1"
MANIFEST_FORMAT = "scansor-inspection-manifest-v1"


def _validate_local_path(value: str | Path, label: str) -> None:
    path = os.fspath(value)
    if "\0" in path:
        raise ValueError(f"{label} contains an embedded NUL")
    try:
        _ = os.fsencode(path)
    except UnicodeError as error:
        raise ValueError(f"{label} is not encodable by the filesystem") from error


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class TomlConfigValues(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    frame: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    activation_policy: (
        Literal["all-instantiated-primary-training-v0", "exact-factor-ids"] | None
    ) = None
    active_factor_ids: tuple[str, ...] | None = None
    callback_limit: int | None = Field(default=None, ge=1, le=256)
    callback_trace_byte_limit: int | None = Field(
        default=None, ge=1_024, le=16 * 1024 * 1024
    )
    canonical_unit: Literal["m"] | None = None
    execution_run: str | None = None
    held_out_row_indices: tuple[int, ...] | None = None
    initial_parameter_units: Literal["metre", "metre/radian"] | None = None
    initial_values: tuple[float, ...] | None = None
    input_path: str | None = None
    inspection_run: str | None = None
    log_level: Literal["debug", "info", "warning", "error"] | None = None
    max_header_bytes: int | None = Field(default=None, ge=128, le=1_048_576)
    max_input_bytes: int | None = Field(default=None, ge=1_024, le=134_217_728)
    max_vertices: int | None = Field(default=None, ge=1, le=5_000_000)
    mapping_run: str | None = None
    max_support_distance_m: float | None = Field(default=None, gt=0.0, le=0.002)
    minimum_geometric_clearance_m: float | None = Field(default=None, gt=0.0, le=0.002)
    minimum_region_samples: int | None = Field(default=None, ge=1, le=1000)
    model_frame: Literal["stepped-rotational-v0-synthetic-model-frame"] | None = None
    observation_frame: str | None = Field(default=None, min_length=1, pattern=r".*\S.*")
    output_path: str | None = None
    problem: Literal["fixed-pose-shape", "fixed-geometry-pose-correction"] | None = None
    rank_relative_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)
    rotation_row_1: tuple[float, float, float] | None = None
    rotation_row_2: tuple[float, float, float] | None = None
    rotation_row_3: tuple[float, float, float] | None = None
    source_unit: Literal["m"] | None = None
    transform_direction: Literal["observation-to-model"] | None = None
    transform_scale: float | None = Field(default=None, ge=1.0, le=1.0)
    transform_tolerance: float | None = Field(default=None, gt=0.0, le=1e-6)
    transition_guard_m: float | None = Field(default=None, gt=0.0, le=0.002)
    translation_m: tuple[float, float, float] | None = None
    translation_unit: Literal["m"] | None = None
    unit: Literal["m", "mm"] | None = None
    variant: Literal["axisymmetric", "asymmetric-datum-flat"] | None = None

    @field_validator(
        "active_factor_ids",
        "held_out_row_indices",
        "initial_values",
        "rotation_row_1",
        "rotation_row_2",
        "rotation_row_3",
        "translation_m",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator(
        "execution_run",
        "input_path",
        "inspection_run",
        "mapping_run",
        "output_path",
    )
    @classmethod
    def validate_job_path(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_local_path(value, "job path")
        return value


class Settings(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    max_header_bytes: int = Field(default=65_536, ge=128, le=1_048_576)
    max_input_bytes: int = Field(default=67_108_864, ge=1_024, le=134_217_728)
    max_vertices: int = Field(default=5_000_000, ge=1, le=5_000_000)
    log_level: LogLevel = LogLevel.WARNING


SettingSource = Literal["command-line", "environment", "toml", "default"]


class ResolvedValue(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    source: SettingSource


class ResolvedLogLevel(ResolvedValue):
    value: Literal["debug", "info", "warning", "error"]


class ResolvedMaxHeaderBytes(ResolvedValue):
    value: int = Field(ge=128, le=1_048_576)


class ResolvedMaxInputBytes(ResolvedValue):
    value: int = Field(ge=1_024, le=134_217_728)


class ResolvedMaxVertices(ResolvedValue):
    value: int = Field(ge=1, le=5_000_000)


class ResolvedSettings(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    log_level: ResolvedLogLevel
    max_header_bytes: ResolvedMaxHeaderBytes
    max_input_bytes: ResolvedMaxInputBytes
    max_vertices: ResolvedMaxVertices

    def values(self) -> Settings:
        return Settings(
            log_level=LogLevel(self.log_level.value),
            max_header_bytes=self.max_header_bytes.value,
            max_input_bytes=self.max_input_bytes.value,
            max_vertices=self.max_vertices.value,
        )


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class JobRecord(StrictModel):
    deterministic: Literal[True] = True
    model: None = None
    normal_handling: Literal[
        "validate-finite-nonzero-and-preserve-or-record-absence"
    ] = "validate-finite-nonzero-and-preserve-or-record-absence"
    random_seed: None = None
    selection: Literal["inspect"] = "inspect"
    supported_fit_options: tuple[()] = ()

    @field_validator("supported_fit_options", mode="before")
    @classmethod
    def restore_empty_fit_options(cls, value: object) -> object:
        if value == []:
            return ()
        return value


class InspectJobConfig(JobRecord):
    frame: str = Field(min_length=1, pattern=r".*\S.*")
    input_path: Path
    output_path: Path
    unit: Literal["m", "mm"]

    @field_validator("input_path", "output_path")
    @classmethod
    def validate_job_path(cls, value: Path) -> Path:
        _validate_local_path(value, "job path")
        return value

    def record(self) -> JobRecord:
        return JobRecord.model_validate(
            self.model_dump(exclude={"frame", "input_path", "output_path", "unit"})
        )


class SourceRecord(StrictModel):
    byte_count: int = Field(ge=0)
    frame: str = Field(min_length=1, pattern=r".*\S.*")
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: Literal["m", "mm"]

    @field_validator("path")
    @classmethod
    def validate_local_path(cls, value: str) -> str:
        _validate_local_path(value, "source path")
        return value


class CanonicalRecord(StrictModel):
    byte_count: int = Field(ge=0)
    coordinate_unit: Literal["m"] = "m"
    media_type: Literal["application/x-npy"] = "application/x-npy"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class InspectionRecord(StrictModel):
    coordinate_source_dtype: Literal["float32", "float64"]
    fields: list[str]
    normal_magnitude_bounds: list[float] | None
    point_count: int = Field(gt=0)
    position_bounds_m: dict[str, list[float]]
    rgb_preserved: bool


class SemanticAbsences(StrictModel):
    active_factor_ids: list[str] = Field(default_factory=list)
    factors: list[str] = Field(default_factory=list)
    fit_result: None = None
    held_out_roles: list[str] = Field(default_factory=list)
    mappings: list[str] = Field(default_factory=list)
    memberships: list[str] = Field(default_factory=list)
    model: None = None
    observations: list[str] = Field(default_factory=list)
    publication_state: Literal["not-applicable"] = "not-applicable"


class InspectionReport(StrictModel):
    canonical: CanonicalRecord
    format: Literal["scansor-inspection-report-v1"] = REPORT_FORMAT
    format_status: Literal["internal/provisional/non-public-contract"] = FORMAT_STATUS
    inspection: InspectionRecord
    job: JobRecord
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_absences: SemanticAbsences
    settings: ResolvedSettings
    source: SourceRecord


class ArtifactRecord(StrictModel):
    byte_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunManifest(StrictModel):
    artifacts: dict[str, ArtifactRecord]
    format: Literal["scansor-inspection-manifest-v1"] = MANIFEST_FORMAT
    format_status: Literal["internal/provisional/non-public-contract"] = FORMAT_STATUS
    run_id: str = Field(pattern=r"^[0-9a-f]{64}$")
