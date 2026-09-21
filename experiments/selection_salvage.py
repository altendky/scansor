"""One-time exact-v2 browser-recipe selection salvage; not a runtime loader."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import ClassVar, Literal, cast

from pydantic import ConfigDict, Field, field_validator

from experiments.feature_graph import (
    FeatureGraph,
    Growth,
    Recipe,
    Selection,
    Source,
    selection_source,
)
from experiments.nozzle_session import NozzleWorkspace
from scansor.files import read_regular
from scansor.models import StrictModel
from scansor.selection_bundle import (
    SelectionBundle,
    SelectionMembership,
    SelectionSource,
    vertex_ids_sha256,
)
from scansor.serialization import canonical_json, sha256

LEGACY_RECIPE_BYTES = 16 * 1024 * 1024
SALVAGE_REPORT_FORMAT = "scansor-selection-salvage-report-v1"
SALVAGE_REPORT_STATUS = "internal/provisional/one-time/non-public-evidence"


class SalvageRecord(StrictModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        validate_default=True,
    )


class LegacySourceBinding(SalvageRecord):
    legacy_reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str = Field(min_length=1)


class RecipeSalvage(SalvageRecord):
    discarded_node_ids: tuple[str, ...]
    discarded_output: str
    legacy_source_bindings: tuple[LegacySourceBinding, ...] = Field(min_length=1)
    materialized_growth_ids: tuple[str, ...]
    preserved_node_ids: tuple[str, ...]
    recipe_path: str = Field(min_length=1)
    recipe_schema_version: Literal[2] = 2
    recipe_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator(
        "discarded_node_ids",
        "legacy_source_bindings",
        "materialized_growth_ids",
        "preserved_node_ids",
        mode="before",
    )
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class SelectionSalvageEvidence(SalvageRecord):
    selection_id: str = Field(min_length=1)
    vertex_count: int = Field(ge=0)
    vertex_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SelectionSalvageReport(SalvageRecord):
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["scansor-selection-salvage-report-v1"] = SALVAGE_REPORT_FORMAT
    format_status: Literal["internal/provisional/one-time/non-public-evidence"] = (
        SALVAGE_REPORT_STATUS
    )
    recipes: tuple[RecipeSalvage, ...] = Field(min_length=1)
    selections: tuple[SelectionSalvageEvidence, ...] = Field(min_length=1)

    @field_validator("recipes", "selections", mode="before")
    @classmethod
    def restore_sequences(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


def _exact_v2_recipe(path: Path) -> tuple[bytes, Recipe]:
    data = read_regular(path, "legacy browser recipe", LEGACY_RECIPE_BYTES)
    try:
        loaded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"legacy browser recipe is invalid JSON: {path}") from error
    raw = cast(dict[str, object], loaded) if isinstance(loaded, dict) else None
    if (
        raw is None
        or type(raw.get("schema_version")) is not int
        or raw["schema_version"] != 2
    ):
        raise ValueError("selection salvage accepts exactly browser recipe version 2")
    return data, Recipe.model_validate(raw)


def _growth_membership(graph: FeatureGraph, growth_id: str) -> tuple[int, ...]:
    state = graph.evaluate(str(graph.snapshot()["token"]), growth_id)
    memberships = cast(dict[str, object], state["memberships"])
    ids = memberships[growth_id]
    if not isinstance(ids, list) or any(type(value) is not int for value in ids):
        raise ValueError(f"growth {growth_id!r} produced no resolved membership")
    return tuple(cast(list[int], ids))


def salvage_selections(
    example: Path, recipe_paths: tuple[Path, ...]
) -> tuple[SelectionBundle, SelectionSalvageReport]:
    if not recipe_paths:
        raise ValueError("at least one legacy browser recipe is required")
    workspace = NozzleWorkspace(example)
    sources: dict[str, SelectionSource] = {}
    selections: dict[str, SelectionMembership] = {}
    recipe_reports: list[RecipeSalvage] = []

    for recipe_path in recipe_paths:
        recipe_bytes, recipe = _exact_v2_recipe(recipe_path)
        graph = FeatureGraph(workspace, recipe)
        nodes = {node.id: node for node in recipe.nodes}
        preserved: list[str] = []
        materialized: list[str] = []
        discarded: list[str] = []
        legacy_source_bindings: list[LegacySourceBinding] = []
        for node in recipe.nodes:
            if isinstance(node, Source):
                source = SelectionSource(
                    label=node.label,
                    source_id=node.id,
                    source_sha256=node.source_sha256,
                    vertex_count=len(workspace.local),
                )
                previous = sources.setdefault(node.id, source)
                if previous != source:
                    raise ValueError(f"conflicting source definition {node.id!r}")
                legacy_source_bindings.append(
                    LegacySourceBinding(
                        legacy_reference_sha256=node.reference_sha256,
                        source_id=node.id,
                    )
                )
                preserved.append(node.id)
            elif isinstance(node, Selection):
                vertex_ids = tuple(node.ids)
                selection = SelectionMembership(
                    depth_mode=node.depth,
                    label=node.label,
                    selection_id=node.id,
                    source_id=node.source,
                    vertex_count=len(vertex_ids),
                    vertex_ids=vertex_ids,
                    vertex_ids_sha256=vertex_ids_sha256(vertex_ids),
                )
                previous = selections.setdefault(node.id, selection)
                if previous != selection:
                    raise ValueError(f"conflicting selection definition {node.id!r}")
                preserved.append(node.id)
            elif isinstance(node, Growth):
                source_id = selection_source(node, nodes)
                vertex_ids = _growth_membership(graph, node.id)
                selection = SelectionMembership(
                    depth_mode=None,
                    label=node.label,
                    selection_id=node.id,
                    source_id=source_id,
                    vertex_count=len(vertex_ids),
                    vertex_ids=vertex_ids,
                    vertex_ids_sha256=vertex_ids_sha256(vertex_ids),
                )
                previous = selections.setdefault(node.id, selection)
                if previous != selection:
                    raise ValueError(f"conflicting selection definition {node.id!r}")
                preserved.append(node.id)
                materialized.append(node.id)
            else:
                discarded.append(node.id)
        recipe_reports.append(
            RecipeSalvage(
                discarded_node_ids=tuple(discarded),
                discarded_output=recipe.output,
                legacy_source_bindings=tuple(legacy_source_bindings),
                materialized_growth_ids=tuple(materialized),
                preserved_node_ids=tuple(preserved),
                recipe_path=recipe_path.as_posix(),
                recipe_sha256=sha256(recipe_bytes),
            )
        )

    bundle = SelectionBundle(
        selections=tuple(selections.values()),
        sources=tuple(sources.values()),
    )
    bundle_bytes = canonical_json(bundle)
    evidence = tuple(
        SelectionSalvageEvidence(
            selection_id=selection.selection_id,
            vertex_count=len(selection.vertex_ids),
            vertex_ids_sha256=selection.vertex_ids_sha256,
        )
        for selection in bundle.selections
    )
    return bundle, SelectionSalvageReport(
        bundle_sha256=sha256(bundle_bytes),
        recipes=tuple(recipe_reports),
        selections=evidence,
    )


def write_new_salvage_artifact(path: Path, data: bytes, label: str) -> None:
    created = False
    try:
        with path.open("xb") as stream:
            created = True
            _ = stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ValueError(f"{label} already exists: {path}") from error
    except Exception:
        if created:
            try:
                path.unlink()
            except OSError as cleanup_error:
                raise ValueError(
                    f"{label} write failed and cleanup also failed: {path}"
                ) from cleanup_error
        raise


def publish_salvage(
    bundle_path: Path,
    report_path: Path,
    bundle: SelectionBundle,
    report: SelectionSalvageReport,
) -> None:
    if bundle_path.resolve(strict=False) == report_path.resolve(strict=False):
        raise ValueError("bundle and report must be different paths")
    if bundle_path.exists() or report_path.exists():
        raise ValueError("bundle and report outputs must both be new")
    bundle_written = False
    try:
        write_new_salvage_artifact(
            bundle_path, canonical_json(bundle), "selection bundle"
        )
        bundle_written = True
        write_new_salvage_artifact(
            report_path, canonical_json(report), "salvage report"
        )
    except Exception:
        if bundle_written:
            try:
                bundle_path.unlink()
            except OSError as cleanup_error:
                raise ValueError(
                    f"salvage publication failed and cleanup also failed: {bundle_path}"
                ) from cleanup_error
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--example", type=Path, required=True)
    _ = parser.add_argument("--recipe", type=Path, action="append", required=True)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    bundle, report = salvage_selections(args.example, tuple(args.recipe))
    publish_salvage(args.bundle, args.report, bundle, report)


if __name__ == "__main__":
    main()
