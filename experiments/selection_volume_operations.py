"""Small, token-fenced UI operations over Selection-owned volume records.

These controls share the graph's accepted archive cache. Preview pages expose
the original support cells, never a hull standing in for their footprint.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Literal, Self, cast

import numpy as np
from pydantic import Field, model_validator

from experiments.actions_limits import MAX_CONTROL_JSON_BYTES
from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    Selection,
    StaleGraph,
    SurfaceReference,
    surface_reference_kind,
)
from experiments.scan_workspace import check_cancelled
from experiments.selection_entries import (
    Digest,
    EntryRecord,
    Identifier,
    SelectionEntry,
    SurfaceAuthoringReference,
    VolumeIntent,
    VolumeReference,
    active_entry,
    source_binding,
)
from experiments.selection_entry_runtime import entry_diagnostic, volume_frame
from experiments.selection_volume import VolumeOptions
from experiments.selection_volume_authoring import (
    authoring_fingerprint,
    authoring_selection_ids,
    resolve_volume,
)
from experiments.selection_volume_transactions import (
    author_volume,
    convert_selection,
    set_volume_options,
)

MAX_PREVIEW_CELLS = 2048
VOLUME_OPERATIONS = frozenset(
    {
        "authorSelectionVolume",
        "setSelectionVolumeOptions",
        "chooseSelectionVolume",
        "getSelectionVolumeChoices",
        "getSelectionVolumePreview",
        "deleteSelectionVolume",
    }
)
VOLUME_QUERIES = frozenset({"getSelectionVolumeChoices", "getSelectionVolumePreview"})


class VolumeRequest(EntryRecord):
    token: str = Field(min_length=1, max_length=128)


class AuthorRequest(VolumeRequest):
    selection: Identifier
    entry: Identifier | None = None
    mode: Literal["create", "replace"]
    volume: Identifier | None = None
    target: VolumeReference | None = None
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["mesh", "plane", "cylinder", "cone"]
    fit: SurfaceAuthoringReference | None = None
    options: VolumeOptions | None = None

    @model_validator(mode="after")
    def explicit_destination(self) -> Self:
        if self.mode == "create":
            if self.volume is None or self.target is not None:
                raise ValueError(
                    "create requires a new volume ID and no replace target"
                )
        elif self.target is None or self.volume is not None:
            raise ValueError("replace requires an explicit target and no new volume ID")
        if self.target is not None and self.target.selection != self.selection:
            raise ValueError("replace a volume within the same Selection")
        _ = VolumeIntent(kind=self.kind, fit=self.fit)
        return self


class ReferenceRequest(VolumeRequest):
    reference: VolumeReference


class OptionsRequest(ReferenceRequest):
    options: VolumeOptions


class ChoicesRequest(VolumeRequest):
    selection: Identifier
    entry: Identifier | None = None


class ChooseRequest(ChoicesRequest):
    source_volume: VolumeReference | None


class PreviewRequest(ReferenceRequest):
    archive_sha256: Digest
    offset: int = Field(default=0, ge=0, strict=True)
    limit: int = Field(default=1024, ge=1, le=MAX_PREVIEW_CELLS, strict=True)


def _state(graph: FeatureGraph[Any], token: str) -> tuple[dict[str, Any], Recipe]:
    state = cast(dict[str, Any], graph.snapshot())
    if state["token"] != token:
        raise StaleGraph("graph changed before volume operation")
    return state, Recipe.model_validate(state["recipe"])


def _owner(recipe: Recipe, identifier: str) -> Selection:
    node = next((node for node in recipe.nodes if node.id == identifier), None)
    if not isinstance(node, Selection):
        raise ValueError("choose an existing Selection")
    return node


def _entry(
    owner: Selection, graph: FeatureGraph[Any], identifier: str | None
) -> SelectionEntry:
    entry = (
        active_entry(owner.entries or [], source_binding(graph.workspace).key)
        if identifier is None
        else next(
            (entry for entry in owner.entries or [] if entry.id == identifier), None
        )
    )
    if entry is None:
        raise ValueError("Selection has no entry for that scan")
    return entry


def _replace_entries(
    recipe: Recipe, selection: str, entries: list[SelectionEntry]
) -> Recipe:
    payload = recipe.model_dump(mode="json")
    for node in cast(list[dict[str, Any]], payload["nodes"]):
        if node["id"] == selection:
            node["entries"] = [entry.model_dump(mode="json") for entry in entries]
    return Recipe.model_validate(payload)


def author(
    graph: FeatureGraph[Any],
    request: AuthorRequest,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Capture privately; explicit cross-scan replacement moves owned identity."""
    _, recipe = _state(graph, request.token)
    recipe = convert_selection(recipe, graph.workspace, request.selection)
    owner = _owner(recipe, request.selection)
    entry = _entry(owner, graph, request.entry)
    if entry.scan.key != source_binding(graph.workspace).key:
        raise ValueError("load the authoring scan before creating a volume")
    target = request.target
    old = resolve_volume(recipe, target)[2] if target is not None else None
    identifier = target.volume if target is not None else request.volume
    assert identifier is not None
    same_entry = target is not None and target.entry == entry.id
    private = FeatureGraph(graph.workspace, recipe, refresh_volumes=False)
    _ = author_volume(
        private,
        str(private.snapshot()["token"]),
        selection=owner.id,
        entry=entry.id,
        volume=identifier,
        label=request.label,
        kind=request.kind,
        fit=request.fit,
        options=request.options or (old.options if old else None),
        replace=same_entry,
        cancelled=cancelled,
    )
    candidate = Recipe.model_validate(private.snapshot()["recipe"])
    if target is not None and not same_entry:
        # Capture first while the previous links still resolve. The transaction
        # freezes every generated observation in its true authoring closure.
        # Repointing those links cannot turn the new definition into a refit loop.
        new_reference = VolumeReference(
            selection=owner.id, entry=entry.id, volume=identifier
        )
        payload = candidate.model_dump(mode="json")
        for node in cast(list[dict[str, Any]], payload["nodes"]):
            if node.get("operation") != "selection":
                continue
            for saved_entry in cast(list[dict[str, Any]], node.get("entries") or []):
                if node["id"] == owner.id and saved_entry["id"] == target.entry:
                    saved_entry["volumes"] = [
                        volume
                        for volume in saved_entry["volumes"]
                        if volume["id"] != identifier
                    ]
                if saved_entry.get("source_volume") == target.model_dump(mode="json"):
                    saved_entry["source_volume"] = new_reference.model_dump(mode="json")
        candidate = Recipe.model_validate(payload)
    check_cancelled(cancelled)
    return graph.replace(candidate, request.token)


def choose(
    graph: FeatureGraph[Any],
    request: ChooseRequest,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    _, recipe = _state(graph, request.token)
    owner = _owner(recipe, request.selection)
    entry = _entry(owner, graph, request.entry)
    if entry.mode != "generated":
        raise ValueError("only generated scan entries choose a source volume")
    if request.source_volume is not None:
        if request.source_volume.selection != owner.id:
            raise ValueError("choose a volume owned by this Selection")
        _ = resolve_volume(recipe, request.source_volume)
    changed = SelectionEntry.model_validate(
        {**entry.model_dump(), "source_volume": request.source_volume}
    )
    candidate = _replace_entries(
        recipe,
        owner.id,
        [changed if old.id == entry.id else old for old in owner.entries or []],
    )
    check_cancelled(cancelled)
    return graph.replace(candidate, request.token)


def _cyclic_choices(
    recipe: Recipe, owner: Selection, entry: SelectionEntry
) -> set[tuple[str, str, str]]:
    """Find choices that would close an unfrozen authoring-input cycle.

    Build the existing dependency map once. Every affected authoring volume
    would gain an edge to the chosen volume; its ancestors are forbidden.
    No archive expansion or repeated whole-graph validation is needed here.
    """
    if entry.mode != "generated":
        return set()
    nodes = {node.id: node for node in recipe.nodes}
    reverse: dict[tuple[str, str, str], set[tuple[str, str, str]]] = {}
    affected: set[tuple[str, str, str]] = set()
    for node in recipe.nodes:
        if not isinstance(node, Selection):
            continue
        for author_entry in node.entries or []:
            for volume in author_entry.volumes:
                key = node.id, author_entry.id, volume.id
                for identifier in authoring_selection_ids(
                    recipe, node, author_entry, volume
                ):
                    if identifier in volume.captured_inputs or (
                        identifier == node.id and volume.captured_ids is not None
                    ):
                        continue
                    selection = nodes[identifier]
                    assert isinstance(selection, Selection)
                    observed = active_entry(
                        selection.entries or [], author_entry.scan.key
                    )
                    if observed is None:
                        continue
                    if selection.id == owner.id and observed.id == entry.id:
                        affected.add(key)
                    elif observed.source_volume is not None:
                        ref = observed.source_volume
                        reverse.setdefault(
                            (ref.selection, ref.entry, ref.volume), set()
                        ).add(key)
    blocked = affected.copy()
    pending = list(affected)
    while pending:
        for key in reverse.get(pending.pop(), set()):
            if key not in blocked:
                blocked.add(key)
                pending.append(key)
    return blocked


def choices(graph: FeatureGraph[Any], request: ChoicesRequest) -> dict[str, Any]:
    state, original = _state(graph, request.token)
    recipe = convert_selection(original, graph.workspace, request.selection)
    owner = _owner(recipe, request.selection)
    entry = _entry(owner, graph, request.entry)
    loaded = entry.scan.key == source_binding(graph.workspace).key
    diagnostic = entry_diagnostic(recipe, owner, graph.workspace) if loaded else None
    surfaces: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    nodes = {node.id: node for node in recipe.nodes}
    for descriptor in state["input_catalogue"]:
        if "surface" not in descriptor["capabilities"]:
            continue
        output = descriptor["reference"]
        context = output["context"]
        reference = SurfaceAuthoringReference(
            feature=context
            if context in nodes and context != output["feature"]
            else output["feature"],
            surface=output["feature"]
            if context in nodes and context != output["feature"]
            else None,
        )
        key = reference.feature, reference.surface
        if key in seen:
            continue
        seen.add(key)
        try:
            kind = surface_reference_kind(
                SurfaceReference.model_validate(reference.model_dump()), nodes
            )
            if kind not in {"plane", "cylinder", "cone"}:
                continue
            reason: str | None = None
            if not loaded:
                reason = "Load this entry's authoring scan first"
            elif diagnostic is not None:
                reason = str(diagnostic["message"])
            else:
                intent = VolumeIntent(
                    kind=cast(Literal["plane", "cylinder", "cone"], kind), fit=reference
                )
                if owner.id not in authoring_selection_ids(
                    recipe, owner, entry, intent
                ):
                    reason = (
                        "This surface's authoring solve does not use this Selection"
                    )
                elif state["states"].get(reference.feature) in {
                    "failed",
                    "blocked",
                    "needs_input",
                }:
                    reason = "Resolve the surface's authoring inputs before capture"
            surfaces.append(
                {
                    "reference": reference.model_dump(),
                    "label": descriptor["label"],
                    "kind": kind,
                    "eligible": reason is None,
                    "reason": reason,
                }
            )
        except (ValueError, KeyError) as error:
            # Unsupported implicit solve contexts remain visible with a reason.
            surfaces.append(
                {
                    "reference": reference.model_dump(),
                    "label": descriptor["label"],
                    "kind": descriptor.get("geometry_kind"),
                    "eligible": False,
                    "reason": str(error),
                }
            )
    volumes: list[dict[str, Any]] = []
    blocked = _cyclic_choices(recipe, owner, entry)
    for saved_entry in owner.entries or []:
        source_label = (
            saved_entry.scan.descriptor.filename
            if saved_entry.scan.descriptor
            else saved_entry.scan.source_sha256[:12]
        )
        for volume in saved_entry.volumes:
            cyclic = (owner.id, saved_entry.id, volume.id) in blocked
            volumes.append(
                {
                    "reference": VolumeReference(
                        selection=owner.id, entry=saved_entry.id, volume=volume.id
                    ).model_dump(),
                    "label": volume.label,
                    "kind": volume.kind,
                    "source_label": source_label,
                    "archive_sha256": volume.archive.sha256,
                    "eligible": not cyclic,
                    "reason": "This choice would create an authoring-input cycle"
                    if cyclic
                    else None,
                    "freshness": "current"
                    if authoring_fingerprint(recipe, owner, saved_entry, volume)
                    == volume.authoring_fingerprint
                    else "refresh_required",
                }
            )
    _ = _state(graph, request.token)
    return {
        "token": request.token,
        "selection": owner.id,
        "entry": entry.id,
        "surfaces": surfaces,
        "volumes": volumes,
    }


def preview(
    graph: FeatureGraph[Any],
    request: PreviewRequest,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _, recipe = _state(graph, request.token)
    owner, entry, volume = resolve_volume(recipe, request.reference)
    if volume.archive.sha256 != request.archive_sha256:
        raise StaleGraph("accepted volume changed before preview")
    geometry = graph.volume_cache.get(volume.archive, cancelled=cancelled)
    origin, basis = volume_frame(geometry)
    rotation = basis.T @ graph.workspace.frame
    translation = (origin - graph.workspace.origin) @ graph.workspace.frame
    cells = geometry["patch_vertices"]
    normals = geometry["patch_normals"]
    total = len(cells)
    if request.offset > total:
        raise ValueError("preview offset exceeds the saved support cell count")
    lower, upper = np.full(3, np.inf), np.full(3, -np.inf)
    for start in range(0, total, 2048):
        check_cancelled(cancelled)
        points = np.asarray(cells[start : start + 2048]) @ rotation + translation
        lower = np.minimum(lower, points.min(axis=(0, 1)))
        upper = np.maximum(upper, points.max(axis=(0, 1)))
    count = min(request.limit, total - request.offset)
    result: dict[str, Any] = {
        "token": request.token,
        "reference": request.reference.model_dump(),
        "archive_sha256": volume.archive.sha256,
        "kind": volume.kind,
        "label": volume.label,
        "freshness": "current"
        if authoring_fingerprint(recipe, owner, entry, volume)
        == volume.authoring_fingerprint
        else "refresh_required",
        "options": geometry["options"].copy(),
        "defaults": geometry["defaults"].copy(),
        "resolved": geometry["resolved"].copy(),
        "total_cells": total,
        "offset": request.offset,
        "bounds": {"min": lower.tolist(), "max": upper.tolist()},
    }
    while True:
        stop = request.offset + count
        result.update(
            returned_cells=count,
            next_offset=stop if stop < total else None,
            positions=(
                np.asarray(cells[request.offset : stop]).reshape((-1, 3)) @ rotation
                + translation
            )
            .ravel()
            .tolist(),
            normals=(
                np.asarray(normals[request.offset : stop]).reshape((-1, 3)) @ rotation
            )
            .ravel()
            .tolist(),
        )
        if len(json.dumps(result, allow_nan=False).encode()) <= MAX_CONTROL_JSON_BYTES:
            break
        count //= 2
        if count == 0:
            raise ValueError("preview cell exceeds the bounded response budget")
    check_cancelled(cancelled)
    _ = _state(graph, request.token)
    return result


def delete(
    graph: FeatureGraph[Any],
    request: ReferenceRequest,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    _, recipe = _state(graph, request.token)
    owner, entry, _ = resolve_volume(recipe, request.reference)
    for node in recipe.nodes:
        if isinstance(node, Selection) and any(
            saved.source_volume == request.reference for saved in node.entries or []
        ):
            raise ValueError(
                "choose or clear each linked scan entry's source volume before deleting it"
            )
    changed = SelectionEntry.model_validate(
        {
            **entry.model_dump(),
            "volumes": [
                volume
                for volume in entry.volumes
                if volume.id != request.reference.volume
            ],
        }
    )
    candidate = _replace_entries(
        recipe,
        owner.id,
        [changed if old.id == entry.id else old for old in owner.entries or []],
    )
    check_cancelled(cancelled)
    return graph.replace(candidate, request.token)


def dispatch(
    graph: FeatureGraph[Any],
    operation: str,
    payload: dict[str, Any],
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if operation == "authorSelectionVolume":
        return author(graph, AuthorRequest.model_validate(payload), cancelled)
    if operation == "chooseSelectionVolume":
        return choose(graph, ChooseRequest.model_validate(payload), cancelled)
    if operation == "setSelectionVolumeOptions":
        request = OptionsRequest.model_validate(payload)
        return set_volume_options(
            graph,
            request.token,
            request.reference,
            request.options.model_dump(exclude_unset=True),
            cancelled=cancelled,
        )
    if operation == "getSelectionVolumeChoices":
        return choices(graph, ChoicesRequest.model_validate(payload))
    if operation == "getSelectionVolumePreview":
        return preview(graph, PreviewRequest.model_validate(payload), cancelled)
    if operation == "deleteSelectionVolume":
        return delete(graph, ReferenceRequest.model_validate(payload), cancelled)
    raise ValueError("unsupported selection volume operation")
