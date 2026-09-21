"""Strict internal selection records independent of fitted-geometry recipes."""

from __future__ import annotations

import hashlib
from typing import Annotated, ClassVar, Literal

from pydantic import ConfigDict, Field, StrictInt, field_validator, model_validator

from scansor.models import StrictModel

SELECTION_BUNDLE_FORMAT = "scansor-selection-bundle-v1"
SELECTION_BUNDLE_STATUS = "internal/provisional/compatibility-free/non-public-contract"
MAX_SELECTION_SOURCES = 16
MAX_SELECTIONS = 1_000
MAX_SOURCE_VERTICES = 5_000_000
MAX_SELECTION_VERTICES = 5_000_000

VertexId = Annotated[StrictInt, Field(ge=0)]
SelectionIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"),
]


def vertex_ids_sha256(vertex_ids: tuple[int, ...]) -> str:
    """Hash canonical newline-delimited decimal source-row IDs."""
    return hashlib.sha256(
        b"".join(f"{vertex_id}\n".encode("ascii") for vertex_id in vertex_ids)
    ).hexdigest()


class SelectionBundleRecord(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class SelectionSource(SelectionBundleRecord):
    label: str = Field(min_length=1, max_length=120)
    source_id: SelectionIdentifier
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    vertex_count: int = Field(ge=1, le=MAX_SOURCE_VERTICES)


class SelectionMembership(SelectionBundleRecord):
    depth_mode: Literal["through_all", "first_surface"] | None
    label: str = Field(min_length=1, max_length=120)
    selection_id: SelectionIdentifier
    source_id: SelectionIdentifier
    vertex_count: int = Field(ge=0, le=MAX_SELECTION_VERTICES)
    vertex_ids: tuple[VertexId, ...] = Field(max_length=MAX_SELECTION_VERTICES)
    vertex_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("vertex_ids", mode="before")
    @classmethod
    def restore_vertex_ids(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_vertex_ids(self) -> SelectionMembership:
        if self.vertex_ids != tuple(sorted(set(self.vertex_ids))):
            raise ValueError("selection vertex IDs must be unique and sorted")
        if self.vertex_count != len(self.vertex_ids):
            raise ValueError("selection vertex count does not match its IDs")
        if self.vertex_ids_sha256 != vertex_ids_sha256(self.vertex_ids):
            raise ValueError("selection vertex-ID digest does not match its IDs")
        return self


class SelectionBundle(SelectionBundleRecord):
    format: Literal["scansor-selection-bundle-v1"] = SELECTION_BUNDLE_FORMAT
    format_status: Literal[
        "internal/provisional/compatibility-free/non-public-contract"
    ] = SELECTION_BUNDLE_STATUS
    selections: tuple[SelectionMembership, ...] = Field(
        min_length=1, max_length=MAX_SELECTIONS
    )
    sources: tuple[SelectionSource, ...] = Field(
        min_length=1, max_length=MAX_SELECTION_SOURCES
    )

    @field_validator("selections", "sources", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_bindings(self) -> SelectionBundle:
        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("selection source IDs must be unique")
        if len({selection.selection_id for selection in self.selections}) != len(
            self.selections
        ):
            raise ValueError("selection IDs must be unique")
        for selection in self.selections:
            source = source_by_id.get(selection.source_id)
            if source is None:
                raise ValueError("selection references an undeclared source")
            if selection.vertex_ids and selection.vertex_ids[-1] >= source.vertex_count:
                raise ValueError("selection vertex ID is outside its source")
        return self
