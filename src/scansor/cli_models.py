from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from scansor.execution_models import MAX_CALLBACK_TRACE_BYTES
from scansor.factor_models import Problem, Variant
from scansor.mapping_models import MappingThresholds
from scansor.models import StrictModel

ActivationPolicy = Literal[
    "all-instantiated-primary-training-v0",
    "exact-factor-ids",
]


def _validate_path(value: Path, label: str) -> Path:
    path = os.fspath(value)
    if "\0" in path:
        raise ValueError(f"{label} contains an embedded NUL")
    try:
        _ = os.fsencode(path)
    except UnicodeError as error:
        raise ValueError(f"{label} is not encodable by the filesystem") from error
    return value


class CommandModel(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class MapJob(CommandModel):
    canonical_unit: Literal["m"]
    held_out_row_indices: tuple[int, ...]
    generation_run: Path | None = None
    inspection_run: Path
    model_frame: Literal["stepped-rotational-v0-synthetic-model-frame"]
    observation_frame: str = Field(min_length=1, pattern=r".*\S.*")
    output_path: Path
    rotation_row_1: tuple[float, float, float]
    rotation_row_2: tuple[float, float, float]
    rotation_row_3: tuple[float, float, float]
    source_unit: Literal["m"]
    thresholds: MappingThresholds
    transform_direction: Literal["observation-to-model"]
    transform_scale: float = Field(ge=1.0, le=1.0)
    translation_m: tuple[float, float, float]
    translation_unit: Literal["m"]
    variant: Variant

    @field_validator(
        "held_out_row_indices",
        "rotation_row_1",
        "rotation_row_2",
        "rotation_row_3",
        "translation_m",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("inspection_run", "output_path")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        return _validate_path(value, "mapping job path")

    @field_validator("generation_run")
    @classmethod
    def validate_optional_path(cls, value: Path | None) -> Path | None:
        return _validate_path(value, "mapping generation path") if value else None

    @model_validator(mode="after")
    def validate_held_out_rows(self) -> MapJob:
        rows = self.held_out_row_indices
        if rows != tuple(sorted(set(rows))) or any(row < 0 for row in rows):
            raise ValueError(
                "held-out row indices must be nonnegative, unique, and sorted"
            )
        return self


class VerifyMappingJob(CommandModel):
    inspection_run: Path
    mapping_run: Path

    @field_validator("inspection_run", "mapping_run")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        return _validate_path(value, "mapping verification path")


class FitJob(CommandModel):
    activation_policy: ActivationPolicy
    active_factor_ids: tuple[str, ...] | None
    callback_limit: int = Field(default=256, ge=1, le=256)
    callback_trace_byte_limit: int = Field(
        default=MAX_CALLBACK_TRACE_BYTES,
        ge=1_024,
        le=MAX_CALLBACK_TRACE_BYTES,
    )
    initial_parameter_units: Literal["metre", "metre/radian"]
    initial_values: tuple[float, ...]
    inspection_run: Path
    mapping_run: Path
    output_path: Path
    problem: Problem
    variant: Variant

    @field_validator("active_factor_ids", "initial_values", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("inspection_run", "mapping_run", "output_path")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        return _validate_path(value, "fit job path")

    @model_validator(mode="after")
    def validate_semantics(self) -> FitJob:
        if self.activation_policy == "all-instantiated-primary-training-v0":
            if self.active_factor_ids is not None:
                raise ValueError(
                    "all-instantiated-primary-training-v0 forbids active factor IDs"
                )
        elif self.active_factor_ids is None:
            raise ValueError("exact-factor-ids requires an active factor ID list")
        if self.active_factor_ids is not None and len(self.active_factor_ids) != len(
            set(self.active_factor_ids)
        ):
            raise ValueError("active factor IDs must be unique")
        expected_units = (
            "metre" if self.problem == "fixed-pose-shape" else "metre/radian"
        )
        if self.initial_parameter_units != expected_units:
            raise ValueError("initial parameter units disagree with the problem")
        expected_dimension = (
            7
            if self.problem == "fixed-pose-shape"
            and self.variant == "asymmetric-datum-flat"
            else 6
        )
        if len(self.initial_values) != expected_dimension:
            message = (
                f"{self.problem} for {self.variant} requires exactly"
                f" {expected_dimension} initial values"
            )
            raise ValueError(message)
        return self


class VerifyFitJob(CommandModel):
    execution_run: Path
    inspection_run: Path
    mapping_run: Path

    @field_validator("execution_run", "inspection_run", "mapping_run")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        return _validate_path(value, "fit verification path")


class GenerateJob(CommandModel):
    noise_sigma_m: float = Field(gt=0.0, le=25e-6)
    output_path: Path
    sampling_profile: Literal["guarded-grid-v1"]
    seed: int = Field(ge=0, le=2**63 - 1)
    variant: Literal["asymmetric-datum-flat"]

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, value: Path) -> Path:
        return _validate_path(value, "generation output path")


class CompareTruthJob(CommandModel):
    execution_run: Path
    generation_run: Path
    inspection_run: Path
    mapping_run: Path

    @field_validator("execution_run", "generation_run", "inspection_run", "mapping_run")
    @classmethod
    def validate_paths(cls, value: Path) -> Path:
        return _validate_path(value, "truth comparison path")
