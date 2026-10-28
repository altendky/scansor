"""Detached recipe migration for one already-aligned replacement scan."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import numpy as np

from experiments.actions_limits import check_portable_actions
from experiments.feature_graph import FeatureGraph, Recipe, Selection, Source
from experiments.scan_workspace import ScanWorkspace, Workspace, check_cancelled
from experiments.selection_entries import (
    SelectionEntry,
    active_entry,
    initial_entry_id,
    source_binding,
)
from experiments.virtual_scan_reduction import VirtualScanDerivation


def replacement_recipe(
    recipe: Recipe,
    previous: Workspace,
    replacement: ScanWorkspace,
    *,
    virtual_scan: VirtualScanDerivation | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Recipe:
    """Retain definitions and source-bound evidence without reusing old row IDs.

    Both complete recipes are validated on disposable, unevaluated graphs. No
    accepted workspace or graph is modified. Source replacement deliberately
    requires the same evaluation frame; registration and frame migration are
    separate operations.
    """
    check_cancelled(cancelled)
    original_binding = source_binding(previous)
    new_binding = source_binding(replacement)
    if replacement.descriptor is None:
        raise ValueError("replacement requires an imported scan descriptor")
    if not np.array_equal(previous.origin, replacement.origin) or not np.array_equal(
        previous.frame, replacement.frame
    ):
        raise ValueError("replacement scan must use the existing coordinate frame")
    sources = [node for node in recipe.nodes if isinstance(node, Source)]
    if len(sources) != 1:
        raise ValueError("replacement requires exactly one active scan Source")
    check_portable_actions(recipe.model_dump(mode="json"))
    check_cancelled(cancelled)
    _ = FeatureGraph(previous, recipe, refresh_volumes=False)
    check_cancelled(cancelled)
    payload = recipe.model_dump(mode="json")
    nodes = {node.id: node for node in recipe.nodes}
    for record in cast(list[dict[str, Any]], payload["nodes"]):
        check_cancelled(cancelled)
        node = nodes[record["id"]]
        if isinstance(node, Source):
            record.update(
                source_sha256=new_binding.source_sha256,
                reference_sha256=new_binding.reference_sha256,
                descriptor=replacement.descriptor.model_dump(mode="json"),
                virtual_scan=virtual_scan.model_dump(mode="json")
                if virtual_scan
                else None,
            )
        elif isinstance(node, Selection):
            # This is the same canonical initial entry as convert_selection,
            # performed in one pass to avoid repeatedly copying saved archives.
            entries = (
                list(node.entries)
                if node.entries is not None
                else [
                    SelectionEntry(
                        id=initial_entry_id(node.id, original_binding.key),
                        scan=original_binding,
                        mode="painted",
                        ids=node.ids.copy(),
                        depth=node.depth,
                    )
                ]
            )
            if active_entry(entries, new_binding.key) is None:
                entries.append(
                    SelectionEntry(
                        id=initial_entry_id(node.id, new_binding.key),
                        scan=new_binding,
                        mode="generated",
                    )
                )
            _ = record.pop("ids", None)
            _ = record.pop("depth", None)
            record["entries"] = [entry.model_dump(mode="json") for entry in entries]
    candidate = Recipe.model_validate(payload)
    check_portable_actions(candidate.model_dump(mode="json"))
    check_cancelled(cancelled)
    _ = FeatureGraph(replacement, candidate, refresh_volumes=False)
    check_cancelled(cancelled)
    return candidate
