from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import ConfigDict, field_validator, model_validator

from scansor.models import StrictModel


class RigidTransform(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )

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
