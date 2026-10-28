"""Pure capture and semantic provenance for selection-owned volume definitions.

Accepted archives are outputs, never inputs to their own authoring fingerprint.
Only a generated selection consuming another volume reads that archive's digest;
none of these helpers expands stored geometry while comparing authored intent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from typing import Any, Literal, cast

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    Selection,
    Source,
    StaleGraph,
    SurfaceReference,
    compile_execution_plan,
    describe_geometry_influence,
    surface_reference_kind,
)
from experiments.scan_workspace import Workspace
from experiments.selection_entries import (
    SelectionEntry,
    SelectionVolume,
    SourceBinding,
    SurfaceAuthoringReference,
    VolumeIntent,
    VolumeReference,
    active_entry,
    membership_projection,
    source_binding,
)
from experiments.selection_volume import FORMAT, VolumeOptions, build_selection_volume
from experiments.volume_archive import VolumeArchive, encode_selection_volume


def resolve_volume(
    recipe: Recipe, reference: VolumeReference
) -> tuple[Selection, SelectionEntry, SelectionVolume]:
    """Resolve exact containment identities, without opening an archive."""
    owner = next(
        (node for node in recipe.nodes if node.id == reference.selection), None
    )
    if not isinstance(owner, Selection) or owner.entries is None:
        raise ValueError("volume reference must name a selection with scan entries")
    entry = next((item for item in owner.entries if item.id == reference.entry), None)
    if entry is None:
        raise ValueError("volume reference names a missing selection entry")
    volume = next((item for item in entry.volumes if item.id == reference.volume), None)
    if volume is None:
        raise ValueError("volume reference names a missing owned volume")
    return owner, entry, volume


def resolve_surface(
    graph: FeatureGraph[Workspace], token: str, reference: SurfaceAuthoringReference
) -> dict[str, Any]:
    """Use the ordinary requested geometry context, including connected solves.

    A failed or blocked solve is left failed. No standalone fit is substituted
    for a named member and no independent numerical fallback is attempted.
    """
    before = graph.snapshot()
    if before["token"] != token:
        raise StaleGraph("graph changed before volume authoring")
    recipe = Recipe.model_validate(before["recipe"])
    nodes = {node.id: node for node in recipe.nodes}
    selected = SurfaceReference.model_validate(reference.model_dump())
    if selected.feature not in nodes:
        raise ValueError("volume authoring surface feature does not exist")
    kind = surface_reference_kind(selected, nodes)
    if kind not in {"plane", "cylinder", "cone"}:
        raise ValueError("volume authoring requires a plane, cylinder or cone")
    _ = graph.ensure_current(token, targets=[selected.feature])
    state = graph.snapshot()
    if state["token"] != token:
        raise StaleGraph("graph changed during volume authoring")
    if cast(dict[str, str], state["states"]).get(selected.feature) != "ready":
        raise ValueError("volume authoring requires ready surface geometry")
    result: object = cast(dict[str, Any], state["results"]).get(selected.feature)
    if not isinstance(result, dict):
        raise ValueError(
            "volume authoring surface geometry is unavailable in that context"
        )
    geometry = cast(dict[str, Any], result)
    if selected.surface is not None:
        members: object = geometry.get("surfaces")
        result = (
            cast(dict[str, Any], members).get(selected.surface)
            if isinstance(members, dict)
            else None
        )
        if not isinstance(result, dict):
            raise ValueError(
                "volume authoring surface geometry is unavailable in that context"
            )
        geometry = cast(dict[str, Any], result)
    if geometry.get("kind") != kind:
        raise ValueError(
            "volume authoring surface geometry is unavailable in that context"
        )
    return deepcopy(geometry)


def capture_volume(
    workspace: Workspace,
    ids: object,
    kind: Literal["mesh", "plane", "cylinder", "cone"],
    options: VolumeOptions | dict[str, Any] | None,
    surface: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> VolumeArchive:
    """Capture in the workspace frame and protect its source/frame provenance."""
    protected = {
        **(provenance or {}),
        "source_binding": source_binding(workspace).key,
        "source_frame": {
            "origin": workspace.origin.tolist(),
            "basis": workspace.frame.tolist(),
        },
    }
    volume = build_selection_volume(
        workspace.local,
        workspace.data.normals @ workspace.frame,
        workspace.data.triangles,
        ids,
        kind=kind,
        fit=surface,
        options=options,
        provenance=protected,
        cancelled=cancelled,
    )
    return encode_selection_volume(volume, cancelled=cancelled)


def _binding_intent(binding: SourceBinding) -> dict[str, Any]:
    record = binding.model_dump(mode="json")
    if binding.descriptor is not None:
        record["descriptor"] = binding.descriptor.semantic_record()
    return record


def _entry_intent(recipe: Recipe, entry: SelectionEntry) -> dict[str, Any]:
    record = {
        "scan": _binding_intent(entry.scan),
        **membership_projection(entry),
    }
    if entry.mode == "generated" and entry.source_volume is not None:
        _, _, volume = resolve_volume(recipe, entry.source_volume)
        record["source_volume_sha256"] = volume.archive.sha256
    return record


def _semantic_projection(
    recipe: Recipe,
    binding: SourceBinding,
    *,
    adoption: dict[str, list[int]] | None = None,
    reference_recipe: Recipe | None = None,
    require_entries: bool = True,
) -> dict[str, Any]:
    record = recipe.model_dump(mode="json")
    record["groups"] = []
    by_id = {node.id: node for node in recipe.nodes}
    for value in cast(list[dict[str, Any]], record["nodes"]):
        node = by_id[value["id"]]
        for name in ("label", "group_id"):
            value.pop(name, None)
        if isinstance(node, Source):
            # A retained derivative catalog does not change the authoring
            # observations or their source-context numerical problem.
            _ = value.pop("virtual_scan", None)
            value["source_sha256"] = binding.source_sha256
            value["reference_sha256"] = binding.reference_sha256
            value["descriptor"] = (
                binding.descriptor.semantic_record()
                if binding.descriptor is not None
                else None
            )
        elif isinstance(node, Selection):
            if node.entries is None:
                source = by_id.get(node.source)
                if not isinstance(source, Source) or (
                    source.source_sha256 != binding.source_sha256
                    or source.reference_sha256 != binding.reference_sha256
                ):
                    raise ValueError(
                        "missing authoring scan entry in the selection closure"
                    )
                _ = value.pop("ids", None)
                _ = value.pop("depth", None)
                value["entries"] = [
                    {
                        "scan": _binding_intent(binding),
                        "mode": "painted",
                        "ids": node.ids,
                        "depth": node.depth,
                    }
                ]
                continue
            entry = active_entry(node.entries, binding.key)
            if entry is None:
                if not require_entries:
                    # Unavailable inputs outside a requested solve remain setup
                    # diagnostics; they cannot prevent unrelated numerical work.
                    value["entries"] = []
                    continue
                raise ValueError(
                    "missing authoring scan entry in the selection closure"
                )
            if adoption is not None and node.id in adoption:
                entry_record = {
                    "scan": _binding_intent(entry.scan),
                    "mode": "painted",
                    "ids": adoption[node.id],
                    "depth": entry.depth,
                }
            else:
                entry_record = _entry_intent(reference_recipe or recipe, entry)
            value["entries"] = [entry_record]
    return record


def semantic_recipe(recipe: Recipe, binding_key: str | SourceBinding) -> dict[str, Any]:
    """Project relevant nested membership for numerical initialization identity.

    Flat legacy recipes retain their exact previous serialization. For nested
    recipes, inactive entries and all owned volume caches are omitted. A chosen
    generated input retains its upstream volume digest without decoding it.
    """
    nested = [
        node
        for node in recipe.nodes
        if isinstance(node, Selection) and node.entries is not None
    ]
    if not nested:
        return recipe.model_dump(mode="json")
    binding = (
        binding_key
        if isinstance(binding_key, SourceBinding)
        else next(
            (
                entry.scan
                for node in nested
                for entry in node.entries or []
                if entry.scan.key == binding_key
            ),
            None,
        )
    )
    if binding is None:
        raise ValueError("missing authoring scan binding in the selection entries")
    return _semantic_projection(recipe, binding, require_entries=False)


def _authoring_closure(
    recipe: Recipe, owner: Selection, entry: SelectionEntry, volume: VolumeIntent
) -> Recipe:
    nodes = {node.id: node for node in recipe.nodes}
    if not isinstance(nodes.get(owner.id), Selection) or owner.entries is None:
        raise ValueError("volume authoring owner must be an entry selection")
    if not any(
        item.id == entry.id and item.scan.key == entry.scan.key
        for item in owner.entries
    ):
        raise ValueError("volume authoring entry does not belong to its selection")
    roots = (
        {owner.id}
        if volume.kind == "mesh"
        else {cast(SurfaceAuthoringReference, volume.fit).feature}
    )
    if not roots <= nodes.keys():
        raise ValueError("volume authoring surface feature does not exist")
    if volume.fit is not None:
        kind = surface_reference_kind(
            SurfaceReference.model_validate(volume.fit.model_dump()), nodes
        )
        if kind != volume.kind:
            raise ValueError("volume kind must match its authoring surface")
    influence = describe_geometry_influence(nodes)
    plan = compile_execution_plan(
        recipe,
        roots,
        influence,
        selected_named_solves=entry.scan.descriptor is not None,
    )
    if volume.kind != "mesh" and owner.id not in plan.order:
        raise ValueError("authoring surface must consume its owning selection")
    included = set(plan.order) | {owner.id, owner.source}
    return recipe.model_copy(
        update={
            "nodes": [node for node in recipe.nodes if node.id in included],
            "output": None,
            "groups": [],
        }
    )


def authoring_selection_ids(
    recipe: Recipe, owner: Selection, entry: SelectionEntry, volume: VolumeIntent
) -> set[str]:
    """Selections whose observations participate in the actual authoring solve."""
    return {
        node.id
        for node in _authoring_closure(recipe, owner, entry, volume).nodes
        if isinstance(node, Selection)
    }


def authoring_recipe_intent(
    recipe: Recipe, owner: Selection, entry: SelectionEntry, volume: VolumeIntent
) -> dict[str, Any]:
    """Numerical initialization intent for the frozen source authoring context."""
    closure = _authoring_closure(recipe, owner, entry, volume)
    if entry.mode == "generated" and volume.captured_ids is None:
        raise ValueError("adopting generated membership requires captured vertex IDs")
    adoption = volume.captured_inputs.copy()
    if entry.mode == "generated":
        adoption[owner.id] = cast(list[int], volume.captured_ids)
    return _semantic_projection(
        closure, entry.scan, adoption=adoption, reference_recipe=recipe
    )


def authoring_fingerprint(
    recipe: Recipe, owner: Selection, entry: SelectionEntry, volume: VolumeIntent
) -> str:
    """Hash the actual authoring closure, excluding its accepted volume output."""
    intent = authoring_recipe_intent(recipe, owner, entry, volume)
    value = {
        "format": "scansor-selection-volume-authoring-v1",
        "kernel": FORMAT,
        "defaults": "source-spacing-v1",
        "scan": _binding_intent(entry.scan),
        "owner": {"selection": owner.id, "entry": entry.id},
        "kind": volume.kind,
        "options": volume.options.model_dump(mode="json"),
        "surface": volume.fit.model_dump(mode="json") if volume.fit else None,
        "recipe": intent,
    }
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()
