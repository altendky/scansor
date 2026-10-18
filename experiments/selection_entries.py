"""Selection-owned scan entries and portable accepted volume definitions.

Containment is separate from graph dependencies. These records retain source
identity and authoring provenance without loading inactive scans or expanding
volume archives during recipe validation/publication.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from experiments.scan_workspace import SourceDescriptor, Workspace
from experiments.selection_volume import VolumeOptions
from experiments.volume_archive import VolumeArchive

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$", strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
Vertex = Annotated[int, Field(ge=0, strict=True)]
Membership = Annotated[list[Vertex], Field(max_length=25_000)]


class EntryRecord(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class SourceBinding(EntryRecord):
    """Exact scan identity, independent of filename hints and active Source ID."""

    source_sha256: Digest
    reference_sha256: Digest
    vertices: int = Field(gt=0, strict=True)
    descriptor: SourceDescriptor | None = None

    @model_validator(mode="after")
    def descriptor_matches(self) -> SourceBinding:
        descriptor = self.descriptor
        if descriptor is not None and (
            descriptor.source_sha256 != self.source_sha256
            or descriptor.binding_sha256 != self.reference_sha256
            or descriptor.vertices != self.vertices
        ):
            raise ValueError("scan binding does not match its source descriptor")
        return self

    @property
    def key(self) -> str:
        if self.descriptor is not None:
            return self.descriptor.binding_sha256
        record = {
            "source_sha256": self.source_sha256,
            "reference_sha256": self.reference_sha256,
            "vertices": self.vertices,
        }
        return hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
        ).hexdigest()


class VolumeReference(EntryRecord):
    selection: Identifier
    entry: Identifier
    volume: Identifier


class SurfaceAuthoringReference(EntryRecord):
    """A surface output reference without importing the feature graph."""

    feature: Identifier
    surface: Identifier | None = None


def _ordered_ids(ids: list[int] | None) -> list[int] | None:
    if ids is not None and ids != sorted(set(ids)):
        raise ValueError("selection IDs must be unique and sorted")
    return ids


class VolumeIntent(EntryRecord):
    kind: Literal["mesh", "plane", "cylinder", "cone"]
    fit: SurfaceAuthoringReference | None = None
    options: VolumeOptions = Field(default_factory=VolumeOptions)
    captured_ids: Membership | None = None
    captured_inputs: dict[Identifier, Membership] = Field(
        default_factory=dict, max_length=99
    )

    @field_validator("captured_ids")
    @classmethod
    def ordered_capture(cls, value: list[int] | None) -> list[int] | None:
        return _ordered_ids(value)

    @field_validator("captured_inputs")
    @classmethod
    def ordered_context(cls, value: dict[str, list[int]]) -> dict[str, list[int]]:
        for ids in value.values():
            _ = _ordered_ids(ids)
        return value

    @model_validator(mode="after")
    def fit_matches_kind(self) -> Self:
        if self.kind == "mesh" and self.fit is not None:
            raise ValueError("mesh volumes do not reference an analytic fit")
        if self.kind != "mesh" and self.fit is None:
            raise ValueError("analytic volumes require an authoring fit reference")
        return self


class SelectionVolume(VolumeIntent):
    id: Identifier
    label: str = Field(min_length=1, max_length=120)
    archive: VolumeArchive
    authoring_fingerprint: Digest


class SelectionEntry(EntryRecord):
    id: Identifier
    scan: SourceBinding
    mode: Literal["painted", "generated"]
    ids: Membership | None = None
    depth: Literal["through_all", "first_surface"] = "through_all"
    source_volume: VolumeReference | None = None
    volumes: list[SelectionVolume] = Field(default_factory=list, max_length=64)

    @field_validator("ids")
    @classmethod
    def ordered_membership(cls, value: list[int] | None) -> list[int] | None:
        return _ordered_ids(value)

    @model_validator(mode="after")
    def mode_and_ownership(self) -> SelectionEntry:
        if self.mode == "painted":
            if self.ids is None:
                raise ValueError("painted entries require IDs, including an empty list")
            if self.source_volume is not None:
                raise ValueError("painted entries cannot choose a source volume")
        elif self.ids is not None:
            raise ValueError("generated entries cannot contain painted IDs")
        if self.ids and self.ids[-1] >= self.scan.vertices:
            raise ValueError("selection ID exceeds its saved source vertex count")
        volume_ids = [volume.id for volume in self.volumes]
        if len(volume_ids) != len(set(volume_ids)):
            raise ValueError("owned volume IDs must be unique within an entry")
        for volume in self.volumes:
            if self.mode == "painted" and volume.captured_ids is not None:
                raise ValueError("only generated entries have captured authoring IDs")
            if volume.captured_ids and volume.captured_ids[-1] >= self.scan.vertices:
                raise ValueError("captured ID exceeds its saved source vertex count")
            for ids in volume.captured_inputs.values():
                if ids and ids[-1] >= self.scan.vertices:
                    raise ValueError(
                        "captured context ID exceeds its saved source vertex count"
                    )
        return self


def validate_entries(entries: list[SelectionEntry]) -> list[SelectionEntry]:
    """Reject ambiguous entry identity or multiple entries for the same scan."""
    ids = [entry.id for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("selection entry IDs must be unique")
    scans = [entry.scan.key for entry in entries]
    if len(scans) != len(set(scans)):
        raise ValueError("a Selection allows only one entry per scan binding")
    return entries


def active_entry(
    entries: list[SelectionEntry], binding_key: str
) -> SelectionEntry | None:
    return next(
        (entry for entry in validate_entries(entries) if entry.scan.key == binding_key),
        None,
    )


def membership_projection(entry: SelectionEntry) -> dict[str, Any]:
    """Authored observation input only; omit ownership, archives and labels."""
    if entry.mode == "painted":
        assert entry.ids is not None
        return {"mode": entry.mode, "ids": entry.ids.copy(), "depth": entry.depth}
    return {
        "mode": entry.mode,
        "source_volume": entry.source_volume.model_dump()
        if entry.source_volume is not None
        else None,
    }


def source_binding(workspace: Workspace) -> SourceBinding:
    if workspace.source_sha256 is None or not len(workspace.local):
        raise ValueError("an empty workspace has no selection source binding")
    return SourceBinding(
        source_sha256=workspace.source_sha256,
        reference_sha256=workspace.reference_sha256,
        vertices=len(workspace.local),
        descriptor=workspace.source_descriptor,
    )


def initial_entry_id(selection_id: str, binding_key: str) -> str:
    """Stable initial child identity without depending on labels or filenames."""
    payload = json.dumps([selection_id, binding_key], separators=(",", ":"))
    return "entry_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:48]
