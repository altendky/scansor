"""Fenced authoring and staged refresh of selection-owned accepted volumes.

Numerical capture happens on disposable ordinary graphs. The live graph changes
only after capture, archive validation, cancellation and the original edit-token
fence succeed. Staged refresh never publishes into the supplied graph.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, cast

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    Selection,
    Source,
    StaleGraph,
)
from experiments.scan_workspace import ScanCancelled, Workspace, check_cancelled
from experiments.selection_entries import (
    SelectionEntry,
    SelectionVolume,
    SurfaceAuthoringReference,
    VolumeIntent,
    VolumeReference,
    active_entry,
    initial_entry_id,
    source_binding,
)
from experiments.selection_entry_runtime import VolumeCache, resolve_membership
from experiments.selection_volume import VolumeOptions, update_selection_volume_options
from experiments.selection_volume_authoring import (
    authoring_context_recipe,
    authoring_fingerprint,
    authoring_recipe_intent,
    authoring_selection_ids,
    capture_volume,
    resolve_surface,
    resolve_volume,
)
from experiments.volume_archive import decode_selection_volume, encode_selection_volume

Kind = Literal["mesh", "plane", "cylinder", "cone"]


def _selection(recipe: Recipe, identifier: str) -> Selection:
    node = next((node for node in recipe.nodes if node.id == identifier), None)
    if not isinstance(node, Selection):
        raise ValueError("selection authoring requires an existing Selection")
    return node


def _replace_entry(recipe: Recipe, owner: Selection, entry: SelectionEntry) -> Recipe:
    payload = recipe.model_dump(mode="json")
    for node in cast(list[dict[str, Any]], payload["nodes"]):
        if node["id"] == owner.id:
            assert owner.entries is not None
            node["entries"] = [
                (entry if old.id == entry.id else old).model_dump(mode="json")
                for old in owner.entries
            ]
    return Recipe.model_validate(payload)


def _put_volume(
    recipe: Recipe, owner: Selection, entry: SelectionEntry, volume: SelectionVolume
) -> Recipe:
    volumes = [item for item in entry.volumes if item.id != volume.id]
    position = next(
        (i for i, old in enumerate(entry.volumes) if old.id == volume.id), len(volumes)
    )
    volumes.insert(position, volume)
    updated = SelectionEntry.model_validate({**entry.model_dump(), "volumes": volumes})
    return _replace_entry(recipe, owner, updated)


def convert_selection(
    recipe: Recipe, workspace: Workspace, selection_id: str
) -> Recipe:
    """Explicitly convert legacy painted membership, retaining the feature ID."""
    owner = _selection(recipe, selection_id)
    if owner.entries is not None:
        return recipe.model_copy(deep=True)
    binding = source_binding(workspace)
    source = next((node for node in recipe.nodes if node.id == owner.source), None)
    if not isinstance(source, Source) or (
        source.source_sha256 != binding.source_sha256
        or source.reference_sha256 != binding.reference_sha256
    ):
        raise ValueError("painted selection does not belong to the loaded scan")
    entry = SelectionEntry(
        id=initial_entry_id(owner.id, binding.key),
        scan=binding,
        mode="painted",
        ids=owner.ids.copy(),
        depth=owner.depth,
    )
    payload = recipe.model_dump(mode="json")
    for node in cast(list[dict[str, Any]], payload["nodes"]):
        if node["id"] == owner.id:
            _ = node.pop("ids")
            _ = node.pop("depth", None)
            node["entries"] = [entry.model_dump(mode="json")]
    return Recipe.model_validate(payload)


def _loaded_entry(
    owner: Selection, workspace: Workspace, identifier: str | None
) -> SelectionEntry:
    binding = source_binding(workspace)
    entry = (
        active_entry(owner.entries or [], binding.key)
        if identifier is None
        else next((item for item in owner.entries or [] if item.id == identifier), None)
    )
    if entry is None or entry.scan.key != binding.key:
        raise ValueError("load the selected authoring entry's scan before capture")
    return entry


def _ids(
    recipe: Recipe,
    selection: Selection,
    workspace: Workspace,
    cache: VolumeCache,
    cancelled: Callable[[], bool] | None = None,
) -> list[int]:
    ids, _ = resolve_membership(
        recipe, selection, workspace, cache, cancelled=cancelled
    )
    if ids is None:
        raise ValueError(f"selection {selection.label!r} needs a current source volume")
    return ids


def _freeze_generated(
    recipe: Recipe,
    workspace: Workspace,
    owner: Selection,
    entry: SelectionEntry,
    intent: VolumeIntent,
    cancelled: Callable[[], bool] | None,
) -> VolumeIntent:
    captured = intent.captured_inputs.copy()
    own_ids = intent.captured_ids
    inputs = authoring_selection_ids(recipe, owner, entry, intent)
    cache = VolumeCache()
    for identifier in sorted(inputs):
        check_cancelled(cancelled)
        node = _selection(recipe, identifier)
        original = active_entry(node.entries or [], entry.scan.key)
        if original is not None and original.mode == "generated":
            membership = _ids(recipe, node, workspace, cache, cancelled)
            if identifier == owner.id:
                own_ids = membership
            else:
                captured[identifier] = membership
    return VolumeIntent.model_validate(
        {
            **intent.model_dump(),
            "captured_ids": own_ids,
            "captured_inputs": captured,
        }
    )


def _context_graph(
    recipe: Recipe,
    workspace: Workspace,
    owner: Selection,
    entry: SelectionEntry,
    intent: VolumeIntent,
    cancelled: Callable[[], bool] | None,
) -> FeatureGraph[Any]:
    """Flatten capture inputs while retaining their ordinary constraint graph."""
    context = authoring_context_recipe(recipe, owner, entry, intent)
    needed = {node.id for node in context.nodes if isinstance(node, Selection)}
    captured = intent.captured_inputs.copy()
    if intent.captured_ids is not None:
        captured[owner.id] = intent.captured_ids
    cache = VolumeCache()
    payload = context.model_dump(mode="json")
    by_id = {node.id: node for node in context.nodes}
    for raw in cast(list[dict[str, Any]], payload["nodes"]):
        check_cancelled(cancelled)
        node = by_id[raw["id"]]
        if not isinstance(node, Selection) or node.entries is None:
            continue
        original = active_entry(node.entries, entry.scan.key)
        if node.id not in needed:
            ids = []
        elif node.id in captured:
            ids = captured[node.id].copy()
        elif original is None:
            raise ValueError("missing authoring scan entry in the selection closure")
        else:
            ids = _ids(recipe, node, workspace, cache, cancelled)
        _ = raw.pop("entries")
        raw["ids"] = ids
        raw["depth"] = original.depth if original is not None else "through_all"
    flattened = Recipe.model_validate(payload)
    check_cancelled(cancelled)
    return FeatureGraph(
        workspace,
        flattened,
        refresh_volumes=False,
        fit_intent=authoring_recipe_intent(recipe, owner, entry, intent),
    )


def _capture(
    recipe: Recipe,
    workspace: Workspace,
    owner: Selection,
    entry: SelectionEntry,
    intent: VolumeIntent,
    identifier: str,
    label: str,
    cancelled: Callable[[], bool] | None,
) -> SelectionVolume:
    check_cancelled(cancelled)
    if entry.scan.key != source_binding(workspace).key:
        raise ValueError("load the authoring scan to refresh this volume")
    context = _context_graph(recipe, workspace, owner, entry, intent, cancelled)
    token = str(context.snapshot()["token"])
    surface = (
        resolve_surface(context, token, intent.fit) if intent.fit is not None else None
    )
    check_cancelled(cancelled)
    plain = _selection(Recipe.model_validate(context.snapshot()["recipe"]), owner.id)
    archive = capture_volume(
        workspace,
        plain.ids,
        intent.kind,
        intent.options,
        surface,
        {
            "selection": owner.id,
            "entry": entry.id,
            "volume": identifier,
            "surface": intent.fit.model_dump() if intent.fit is not None else None,
        },
        cancelled,
    )
    fingerprint = authoring_fingerprint(recipe, owner, entry, intent)
    return SelectionVolume(
        **intent.model_dump(),
        id=identifier,
        label=label,
        archive=archive,
        authoring_fingerprint=fingerprint,
    )


def _snapshot_recipe(graph: FeatureGraph[Any], token: str) -> Recipe:
    state = graph.snapshot()
    if state["token"] != token:
        raise StaleGraph("graph changed before volume authoring")
    return Recipe.model_validate(state["recipe"])


def author_volume(
    graph: FeatureGraph[Any],
    token: str,
    *,
    selection: str,
    entry: str | None = None,
    volume: str,
    label: str,
    kind: Kind,
    fit: SurfaceAuthoringReference | dict[str, Any] | None = None,
    options: VolumeOptions | dict[str, Any] | None = None,
    replace: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Create new versus replace existing is explicit and token-fenced."""
    check_cancelled(cancelled)
    recipe = convert_selection(
        _snapshot_recipe(graph, token), graph.workspace, selection
    )
    owner = _selection(recipe, selection)
    original = _loaded_entry(owner, graph.workspace, entry)
    previous = next((item for item in original.volumes if item.id == volume), None)
    if (previous is not None) != replace:
        raise ValueError(
            "choose replace for an existing volume, or create for a new volume ID"
        )
    reference = (
        fit
        if isinstance(fit, SurfaceAuthoringReference)
        else SurfaceAuthoringReference.model_validate(fit)
        if fit is not None
        else None
    )
    controls = (
        options
        if isinstance(options, VolumeOptions)
        else VolumeOptions.model_validate(options)
        if options is not None
        else previous.options
        if previous is not None
        else VolumeOptions()
    )
    intent = VolumeIntent(kind=kind, fit=reference, options=controls)
    intent = _freeze_generated(
        recipe, graph.workspace, owner, original, intent, cancelled
    )
    accepted = _capture(
        recipe, graph.workspace, owner, original, intent, volume, label, cancelled
    )
    candidate = _put_volume(recipe, owner, original, accepted)
    check_cancelled(cancelled)
    return graph.replace(candidate, token)


def set_volume_options(
    graph: FeatureGraph[Any],
    token: str,
    reference: VolumeReference,
    patch: VolumeOptions | dict[str, Any],
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Restore saved Auto controls without loading original geometry buffers."""
    check_cancelled(cancelled)
    recipe = _snapshot_recipe(graph, token)
    owner, entry, old = resolve_volume(recipe, reference)
    current = (
        authoring_fingerprint(recipe, owner, entry, old) == old.authoring_fingerprint
    )
    geometry = update_selection_volume_options(
        decode_selection_volume(old.archive, cancelled=cancelled), patch
    )
    options = VolumeOptions.model_validate(geometry["options"])
    archive = encode_selection_volume(geometry, cancelled=cancelled)
    intent = VolumeIntent.model_validate(
        {**old.model_dump(include=set(VolumeIntent.model_fields)), "options": options}
    )
    accepted = SelectionVolume(
        **intent.model_dump(),
        id=old.id,
        label=old.label,
        archive=archive,
        authoring_fingerprint=authoring_fingerprint(recipe, owner, entry, intent)
        if current
        else old.authoring_fingerprint,
    )
    candidate = _put_volume(recipe, owner, entry, accepted)
    check_cancelled(cancelled)
    return graph.replace(candidate, token)


def _key(reference: VolumeReference) -> str:
    return f"{reference.selection}/{reference.entry}/{reference.volume}"


def _refresh_order(
    recipe: Recipe, references: set[str] | None = None
) -> list[VolumeReference]:
    by_key: dict[str, VolumeReference] = {}
    prerequisites: dict[str, set[str]] = {}
    for owner in recipe.nodes:
        if not isinstance(owner, Selection) or owner.entries is None:
            continue
        for entry in owner.entries:
            for volume in entry.volumes:
                reference = VolumeReference(
                    selection=owner.id, entry=entry.id, volume=volume.id
                )
                key = _key(reference)
                by_key[key], prerequisites[key] = reference, set()
                for identifier in authoring_selection_ids(recipe, owner, entry, volume):
                    if identifier in volume.captured_inputs or (
                        identifier == owner.id and volume.captured_ids is not None
                    ):
                        continue
                    node = _selection(recipe, identifier)
                    original = active_entry(node.entries or [], entry.scan.key)
                    if original is not None and original.source_volume is not None:
                        prerequisites[key].add(_key(original.source_volume))
    result: list[VolumeReference] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("cyclic selection volume authoring inputs")
        if key in visited:
            return
        if key not in by_key:
            raise ValueError("missing source volume in authoring inputs")
        visiting.add(key)
        for dependency in sorted(prerequisites[key]):
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
        result.append(by_key[key])

    for key in sorted(by_key if references is None else references):
        visit(key)
    return result


def staged_refresh(
    graph: FeatureGraph[Any],
    recipe: Recipe,
    *,
    cancelled: Callable[[], bool] | None = None,
    references: set[str] | None = None,
) -> tuple[Recipe, dict[str, str]]:
    """Refresh requested contexts and prerequisites, without live publication.

    None requests all owned volumes. An empty set performs no capture. Reference
    keys are selection/entry/volume, matching the returned error dictionary.
    """
    candidate = recipe.model_copy(deep=True)
    errors: dict[str, str] = {}
    check_cancelled(cancelled)
    order = _refresh_order(candidate, references)
    if not order:
        return candidate, errors
    binding = source_binding(graph.workspace)
    for reference in order:
        check_cancelled(cancelled)
        owner, entry, volume = resolve_volume(candidate, reference)
        if entry.scan.key != binding.key:
            continue
        try:
            fingerprint = authoring_fingerprint(candidate, owner, entry, volume)
            if fingerprint == volume.authoring_fingerprint:
                continue
            intent = VolumeIntent.model_validate(
                volume.model_dump(include=set(VolumeIntent.model_fields))
            )
            accepted = _capture(
                candidate,
                graph.workspace,
                owner,
                entry,
                intent,
                volume.id,
                volume.label,
                cancelled,
            )
            candidate = _put_volume(candidate, owner, entry, accepted)
        except (StaleGraph, ScanCancelled):
            raise
        except Exception as error:
            errors[_key(reference)] = str(error)
    check_cancelled(cancelled)
    return candidate, errors
