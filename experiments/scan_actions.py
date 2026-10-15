"""Saved-action source preflight; locating hints never grant file access."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from experiments.feature_graph import Recipe, Source
from experiments.feature_recipe_import import ImportWarning, import_recipe
from experiments.scan_workspace import (
    INTERPRETATION_SHA256,
    ScanImportError,
    Workspace,
)

ActionsKind = Literal["empty", "generic", "legacy"]
ActionsDisposition = Literal["matching", "locate", "empty"]


@dataclass(frozen=True)
class ScanActions:
    recipe: Recipe
    warnings: tuple[ImportWarning, ...]
    kind: ActionsKind
    source: Source | None


@dataclass(frozen=True)
class ActionsInspection:
    actions: ScanActions
    disposition: ActionsDisposition

    def json(self) -> dict[str, object]:
        source = self.actions.source
        return {
            "classification": self.disposition,
            "uploadRequired": self.disposition == "locate",
            "descriptor": source.descriptor.model_dump(mode="json")
            if source is not None and source.descriptor is not None
            else None,
            "recipe": self.actions.recipe.model_dump(mode="json"),
            "import_warnings": deepcopy(list(self.actions.warnings)),
        }


def preflight_actions(value: object) -> ScanActions:
    """Validate supported source declarations before asking for source bytes.

    Structural/binding validation against actual geometry follows in FeatureGraph.
    No fitting, example selection, interpretation change or filesystem access occurs.
    """
    try:
        recipe, warnings = import_recipe(value)
    except ValidationError as error:
        messages = [
            str(item["msg"])
            for item in error.errors(include_input=False, include_url=False)
        ]
        raise ScanImportError(
            ("Invalid saved actions: " + "; ".join(dict.fromkeys(messages)))[:1200],
            "invalid_actions",
        ) from error
    except (ValueError, TypeError) as error:
        raise ScanImportError(
            f"Invalid saved actions: {error}"[:1200], "invalid_actions"
        ) from error
    sources = [node for node in recipe.nodes if isinstance(node, Source)]
    if not recipe.nodes:
        return ScanActions(recipe, tuple(warnings), "empty", None)
    if len(sources) != 1:
        raise ScanImportError(
            "Saved actions must declare exactly one source, or an empty project.",
            "invalid_actions",
        )
    source = sources[0]
    if not all(
        re.fullmatch(r"[0-9a-f]{64}", value)
        for value in (source.source_sha256, source.reference_sha256)
    ):
        raise ScanImportError(
            "Saved source hashes are invalid.", "invalid_source_binding"
        )
    descriptor = source.descriptor
    if descriptor is None:
        return ScanActions(recipe, tuple(warnings), "legacy", source)
    if descriptor.interpretation_sha256 != INTERPRETATION_SHA256:
        raise ScanImportError(
            "The saved scan interpretation is not supported by this importer.",
            "unsupported_interpretation",
        )
    if (
        source.source_sha256 != descriptor.source_sha256
        or source.reference_sha256 != descriptor.binding_sha256
    ):
        raise ScanImportError(
            "Saved Source and descriptor identities disagree.", "invalid_source_binding"
        )
    return ScanActions(recipe, tuple(warnings), "generic", source)


def inspect_actions(value: object, workspace: Workspace) -> ActionsInspection:
    """Classify against the actual accepted workspace, never a filename match."""
    actions = preflight_actions(value)
    if actions.kind == "empty":
        return ActionsInspection(actions, "empty")
    source = actions.source
    assert source is not None
    content_matches = (
        source.source_sha256 == workspace.source_sha256
        and source.reference_sha256 == workspace.reference_sha256
    )
    descriptor = workspace.source_descriptor
    if actions.kind == "legacy":
        if descriptor is None and content_matches:
            return ActionsInspection(actions, "matching")
        raise ScanImportError(
            "These legacy actions require their explicitly compatible loaded example. "
            + "Choose that example explicitly before reopening them.",
            "incompatible_legacy",
        )
    assert source.descriptor is not None
    matching = (
        descriptor is not None
        and content_matches
        and descriptor.semantic_record() == source.descriptor.semantic_record()
    )
    return ActionsInspection(actions, "matching" if matching else "locate")
