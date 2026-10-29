"""Experimental recipe-only authoring for inspectable feature reuse children."""

from dataclasses import dataclass
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from experiments.feature_graph import (
    MAX_RECIPE_ACTIONS,
    CircularReuse,
    FeatureGraph,
    FeatureReuse,
    Recipe,
    Record,
    StaleGraph,
    SurfaceFit,
    dependencies,
    discover_reuse_lineage,
)
from experiments.feature_reuse import circular_placements


class ReuseChanges(Record):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    group_id: str | None = None
    fits: list[str] | None = Field(default=None, min_length=1, max_length=32)
    reference_selection: str | None = None
    target_selections: list[str] | None = Field(
        default=None, min_length=1, max_length=32
    )
    tangent_margin: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    normal_margin: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    normal_angle_degrees: float | None = Field(
        default=None, gt=0, le=90, allow_inf_nan=False
    )
    equal_corresponding_dimensions: bool | None = None
    circular: CircularReuse | None = None


class ReuseAuthoringRequest(Record):
    token: str
    reuse_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    changes: ReuseChanges = Field(default_factory=ReuseChanges)
    allocation_seed: str = Field(min_length=1, max_length=120)
    create: bool = False


@dataclass(frozen=True)
class ReusePlan:
    recipe: Recipe
    generated_ids: tuple[str, ...]
    removed_ids: tuple[str, ...]
    selected_id: str

    def metadata(self) -> dict[str, object]:
        return {
            "generated_ids": list(self.generated_ids),
            "removed_ids": list(self.removed_ids),
            "selected_id": self.selected_id,
        }


def _unique_label(base: str, nodes: list[dict[str, Any]]) -> str:
    taken = {str(node["label"]).strip().lower() for node in nodes}
    root = base.strip()[:120] or "Feature"
    label, number = root, 2
    while label.lower() in taken:
        suffix = f" {number}"
        label = root[: 120 - len(suffix)].rstrip() + suffix
        number += 1
    return label


def reconcile_feature_reuse(
    recipe: Recipe,
    reuse_id: str,
    changes: ReuseChanges,
    allocation_seed: str,
    *,
    create: bool = False,
) -> ReusePlan:
    """Return a new recipe; existing ownership keys keep their identities."""
    payload = recipe.model_dump()
    nodes: list[dict[str, Any]] = payload["nodes"]
    originals = {node.id: node for node in recipe.nodes}
    owner = next((node for node in nodes if node["id"] == reuse_id), None)
    if create:
        if owner is not None:
            raise ValueError("The new feature reuse ID is already in use.")
        owner = cast(dict[str, Any], {"id": reuse_id, "operation": "feature_reuse"})
        nodes.append(owner)
    if owner is None or owner["operation"] != "feature_reuse":
        raise ValueError("This feature reuse action is no longer available.")
    owner.update(changes.model_dump(exclude_unset=True))
    fits = owner.get("fits")
    circular = (
        CircularReuse.model_validate(owner["circular"])
        if owner.get("circular")
        else None
    )
    if circular is not None:
        owner["target_selections"] = [
            f"occurrence_{index}" for index in range(1, circular.count)
        ]
        if not fits or not isinstance(originals.get(fits[0]), SurfaceFit):
            raise ValueError("Choose existing source Fits for the circular pattern.")
        owner["reference_selection"] = cast(SurfaceFit, originals[fits[0]]).selections[
            0
        ]
    if not fits or not owner.get("target_selections"):
        raise ValueError("Choose at least one fit and one target selection.")
    if any(not isinstance(originals.get(key), SurfaceFit) for key in fits):
        raise ValueError("The reused fits must name existing surface fits.")
    if any(originals[key].managed_by == reuse_id for key in fits):
        raise ValueError("A feature reuse cannot reuse its own generated fits.")
    try:
        owner["lineage"] = discover_reuse_lineage(fits, originals)
    except KeyError as error:
        raise ValueError(
            f"A reuse input is no longer available: {error.args[0]}"
        ) from error
    reuse = FeatureReuse.model_validate(owner)
    if reuse_id in reuse.lineage:
        raise ValueError("A feature reuse cannot reuse its own generated fits.")
    owner.clear()
    owner.update(reuse.model_dump())
    if reuse.circular is None and reuse.reference_selection in reuse.target_selections:
        raise ValueError("The reference selection cannot also be a target.")
    by_id = {node["id"]: node for node in nodes}
    selected = set(reuse.fits)
    relationships = {
        "perpendicular",
        "coaxial",
        "rotational_symmetry",
        "joint_fit",
        "mirror_symmetry",
        "parallel",
        "equal",
        "equal_radii",
        "plane_relationship",
        "axis_solve",
    }
    copied_sources = [
        by_id[key]
        for key in reuse.lineage
        if by_id[key]["operation"] in {"fit", "axis", "reference_plane"} | relationships
    ]
    if circular is not None:
        # Bound authoring before allocating new child dictionaries or UUIDs.
        existing_owned = sum(node.managed_by == reuse_id for node in recipe.nodes)
        per_occurrence = len(copied_sources) + sum(
            len(cast(SurfaceFit, originals[key]).selections) for key in fits
        )
        equalities = (
            sum(cast(SurfaceFit, originals[key]).kind == "cylinder" for key in fits)
            if reuse.equal_corresponding_dimensions
            else 0
        )
        if (
            len(recipe.nodes)
            - existing_owned
            + int(create)
            + (circular.count - 1) * per_occurrence
            + equalities
            > MAX_RECIPE_ACTIONS
        ):
            raise ValueError(
                "Circular pattern exceeds the recipe action budget before child allocation"
            )
    for key in reuse.lineage:
        node = by_id[key]
        if circular is not None and node["operation"] not in {
            "source",
            "selection",
            "growth",
            "surface_selection",
            "region_selection",
            "reuse_selection",
            "feature_reuse",
            "fit",
            "axis",
            "reference_plane",
            *relationships,
        }:
            raise ValueError(
                f"Circular reuse cannot clone the {node['operation']} closure at {node['label']}"
            )
        if node["operation"] == "fit" and key not in selected:
            raise ValueError(
                f"Include {node['label']} in the reused fits to preserve its datum relationships."
            )
        if node["operation"] == "point" or (
            node["operation"] == "fit"
            and (node["kind"] not in {"cylinder", "cone", "plane"} or node.get("point"))
        ):
            raise ValueError(
                "Feature reuse currently supports cylinder, cone and plane fits, not point datums."
            )
        if node["operation"] == "axis" and node.get("source_points"):
            raise ValueError(
                f"The point-pair axis {node['label']} cannot currently be reused."
            )

    # Adopt legacy selections and their fits without changing external references.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for child in nodes:
        if child["operation"] != "reuse_selection" or child["reuse"] != reuse_id:
            continue
        if child.get("managed_by") not in (None, reuse_id):
            raise ValueError("Cannot adopt a reuse selection owned by another feature.")
        child.update(
            group_id=None,
            managed_by=reuse_id,
            managed_key=f"selection/{child['target_selection']}/{child['fit']}/{child['source_selection']}",
        )
        groups.setdefault((child["target_selection"], child["fit"]), []).append(child)
    for (target, fit_id), group in groups.items():
        child_ids = {node["id"] for node in group}
        candidates = [
            node
            for node in nodes
            if node["operation"] == "fit"
            and len(node["selections"]) == len(child_ids)
            and set(node["selections"]) == child_ids
        ]
        key = f"fit/{target}/{fit_id}"
        adopted = next(
            (
                node
                for node in candidates
                if node.get("managed_by") == reuse_id and node.get("managed_key") == key
            ),
            candidates[0] if len(candidates) == 1 else None,
        )
        if adopted is None:
            raise ValueError(
                "The generated outputs were edited, so this reuse action cannot be restructured safely."
            )
        if adopted.get("managed_by") not in (None, reuse_id):
            raise ValueError("Cannot adopt a fit owned by another feature.")
        if adopted.get("managed_by") is None:
            adopted["support_authority"] = "local"
        adopted.update(group_id=None, managed_by=reuse_id, managed_key=key)
    owned = [node for node in nodes if node.get("managed_by") == reuse_id]
    owned_ids = {node["id"] for node in owned}
    by_key: dict[str, dict[str, Any]] = {}
    for node in owned:
        key = node.get("managed_key")
        if not key or key in by_key:
            raise ValueError(
                "Generated reuse outputs need unique ownership keys before they can be updated."
            )
        by_key[key] = node
    generated: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    retained: set[str] = set()

    def output(key: str, operation: str, label: str) -> dict[str, Any]:
        node = by_key.get(key)
        if node is not None and node["operation"] != operation:
            raise ValueError("A generated output has an incompatible operation.")
        if node is None:
            identity = uuid5(NAMESPACE_URL, f"{allocation_seed}/{reuse_id}/{key}").hex
            node = cast(
                dict[str, Any],
                {
                    "id": f"{operation}_{identity}",
                    "label": _unique_label(label, [*nodes, *generated]),
                    "operation": operation,
                    "managed_by": reuse_id,
                    "managed_key": key,
                },
            )
            if node["id"] in by_id or any(
                item["id"] == node["id"] for item in generated
            ):
                raise ValueError(
                    "A generated reuse ID is already in use; use a new allocation seed."
                )
            generated.append(node)
        node["group_id"] = None
        retained.add(node["id"])
        return node

    instances: dict[str, dict[str, str]] = {}
    for target_id in reuse.target_selections:
        target = by_id.get(target_id)
        if target is None and circular is None:
            raise ValueError("A target selection is no longer available.")
        occurrence = (
            int(target_id.removeprefix("occurrence_")) if circular is not None else None
        )
        target_label = (
            f"Occurrence {occurrence + 1}"
            if occurrence is not None
            else cast(dict[str, Any], target)["label"]
        )
        mapping: dict[str, str] = {}
        instances[target_id] = mapping
        copied: dict[str, dict[str, Any]] = {}
        selections: dict[str, list[str]] = {}
        for fit_id in reuse.fits:
            source_fit = by_id[fit_id]
            selections[fit_id] = []
            for source_selection_id in source_fit["selections"]:
                source_selection = by_id[source_selection_id]
                child = output(
                    f"selection/{target_id}/{fit_id}/{source_selection_id}",
                    "reuse_selection",
                    f"{source_selection['label']} at {target_label}",
                )
                child.update(
                    reuse=reuse_id,
                    fit=fit_id,
                    source_selection=source_selection_id,
                    target_selection=target_id,
                    occurrence=occurrence,
                )
                outputs.append(child)
                selections[fit_id].append(child["id"])
        # Reserve identities before remapping datum/factor/relationship references.
        for source in copied_sources:
            operation = source["operation"]
            family = (
                "fit"
                if operation == "fit"
                else (
                    "datum"
                    if operation in {"axis", "reference_plane"}
                    else "relationship"
                )
            )
            child = output(
                f"{family}/{target_id}/{source['id']}",
                operation,
                f"{source['label']} at {target_label}",
            )
            mapping[source["id"]] = child["id"]
            copied[source["id"]] = child

        def remap(key: str, mapping: dict[str, str] = mapping) -> str:
            if key not in mapping:
                name = by_id.get(key, {}).get("label", key)
                raise ValueError(
                    f"Include {name} in the reused feature to preserve its relationships."
                )
            return mapping[key]

        for source in copied_sources:
            child = copied[source["id"]]
            copy: dict[str, Any] = {
                **source,
                "id": child["id"],
                "label": child["label"],
                "group_id": None,
                "managed_by": reuse_id,
                "managed_key": child["managed_key"],
            }
            _ = copy.pop("placement", None)
            operation = source["operation"]
            if operation == "fit":
                if child["managed_key"] in by_key:
                    copy["support_authority"] = child.get("support_authority")
                    if child.get("support_authority") != "prototype":
                        copy["axial_domain"] = child.get("axial_domain")
                else:
                    copy["support_authority"] = "prototype"
                copy["selections"] = selections[source["id"]]
                for field in ("axis", "point", "reference_plane"):
                    if source.get(field):
                        copy[field] = remap(source[field])
            elif operation == "axis":
                if source.get("source_fit"):
                    copy["source_fit"] = remap(source["source_fit"])
                else:
                    copy["placement"] = {
                        "reuse": reuse_id,
                        "source": source["id"],
                        "target_selection": target_id,
                        **(
                            {"occurrence": occurrence} if occurrence is not None else {}
                        ),
                    }
            elif operation == "reference_plane":
                copy["axis"] = remap(source["axis"])
                copy["placement"] = {
                    "reuse": reuse_id,
                    "source": source["id"],
                    "target_selection": target_id,
                    **({"occurrence": occurrence} if occurrence is not None else {}),
                }
            elif operation == "equal":
                for side in ("left", "right"):
                    copy[side] = {
                        **source[side],
                        "surface": remap(source[side]["surface"]),
                    }
                    if source[side].get("reference_plane"):
                        copy[side]["reference_plane"] = remap(
                            source[side]["reference_plane"]
                        )
            else:
                for field in (
                    "surface",
                    "reference",
                    "lateral",
                    "plane",
                    "axis",
                    "reference_plane",
                ):
                    if source.get(field):
                        copy[field] = remap(source[field])
                for field in ("surfaces", "planes", "constraints", "factors"):
                    if source.get(field):
                        copy[field] = [remap(key) for key in source[field]]
            child.clear()
            child.update(copy)
            outputs.append(child)
    for fit_id in reuse.fits:
        source = by_id[fit_id]
        if not reuse.equal_corresponding_dimensions or source["kind"] != "cylinder":
            continue
        equality = output(
            f"equal-radius/{fit_id}",
            "equal_radii",
            f"{source['label']} radii all equal",
        )
        equality["surfaces"] = [
            fit_id,
            *(instances[target][fit_id] for target in reuse.target_selections),
        ]
        outputs.append(equality)
    removed = owned_ids - retained
    for node in recipe.nodes:
        if (
            node.id != reuse_id
            and node.id not in owned_ids
            and removed.intersection(dependencies(node))
        ):
            raise ValueError(f"Cannot remove generated outputs used by {node.label}.")
    block = {reuse_id, *owned_ids}
    references = set(dependencies(reuse))
    if references.intersection(block):
        raise ValueError("A reuse action cannot use one of its own generated outputs.")
    outside = [node for node in nodes if node["id"] not in block]
    insertion = (
        max(
            (
                -1,
                *(
                    index
                    for index, node in enumerate(outside)
                    if node["id"] in references
                ),
            )
        )
        + 1
    )
    result = [*outside[:insertion], owner, *outputs, *outside[insertion:]]
    selected_id = reuse_id
    if create:
        existing_ids = set(originals)
        added = [node for node in result if node["id"] not in existing_ids]
        result = [*(node.model_dump() for node in recipe.nodes), *added]
        selected_id = added[-1]["id"]
        payload["output"] = selected_id
    elif recipe.output in removed:
        payload["output"] = reuse_id
    payload["nodes"] = result
    updated = Recipe.model_validate(payload)
    seen: set[str] = set()
    for node in updated.nodes:
        missing = next((key for key in dependencies(node) if key not in seen), None)
        if missing is not None:
            name = next(
                (item.label for item in updated.nodes if item.id == missing), missing
            )
            raise ValueError(f"{node.label} would need to move after {name}.")
        seen.add(node.id)
    return ReusePlan(
        updated,
        tuple(node["id"] for node in generated),
        tuple(node["id"] for node in owned if node["id"] in removed),
        selected_id,
    )


def circular_child_operations(
    reuse: FeatureReuse, nodes: dict[str, Any]
) -> dict[str, str]:
    """Validate saved ownership inventory without reconciling or allocating IDs."""
    assert reuse.circular is not None
    expected: dict[str, str] = {}
    for target in reuse.target_selections:
        for key in reuse.lineage:
            node = nodes[key]
            if node.operation in {
                "source",
                "selection",
                "growth",
                "surface_selection",
                "region_selection",
                "reuse_selection",
                "feature_reuse",
            }:
                continue
            family = (
                "fit"
                if node.operation == "fit"
                else "datum"
                if node.operation in {"axis", "reference_plane"}
                else "relationship"
            )
            expected[f"{family}/{target}/{key}"] = node.operation
        for fit in reuse.fits:
            for selection in nodes[fit].selections:
                expected[f"selection/{target}/{fit}/{selection}"] = "reuse_selection"
    if reuse.equal_corresponding_dimensions:
        for fit in reuse.fits:
            if nodes[fit].kind == "cylinder":
                expected[f"equal-radius/{fit}"] = "equal_radii"
    return expected


def _prepare(graph: FeatureGraph[Any], request: ReuseAuthoringRequest) -> ReusePlan:
    snapshot = cast(dict[str, Any], graph.snapshot())
    if snapshot["token"] != request.token:
        raise StaleGraph(
            "graph changed before feature reuse authoring; reload and try again"
        )
    plan = reconcile_feature_reuse(
        Recipe.model_validate(snapshot["recipe"]),
        request.reuse_id,
        request.changes,
        request.allocation_seed,
        create=request.create,
    )
    _ = graph.validate(plan.recipe)
    return plan


def preview_feature_reuse(
    graph: FeatureGraph[Any], request: ReuseAuthoringRequest
) -> dict[str, Any]:
    with graph.lock:
        plan = _prepare(graph, request)
        owner = next(node for node in plan.recipe.nodes if node.id == request.reuse_id)
        circular_preview: dict[str, Any] | None = None
        if isinstance(owner, FeatureReuse) and owner.circular is not None:
            from experiments.selection_volume_authoring import resolve_volume

            circular = owner.circular
            state = cast(
                dict[str, Any],
                graph.ensure_current(request.token, targets=[circular.axis]),
            )
            if state["token"] != request.token:
                raise StaleGraph(
                    "source authoring changed during circular preview; reload before applying"
                )
            ready = state["states"].get(circular.axis) == "ready"
            circular_preview = {
                "axis": circular.axis,
                "count": circular.count,
                "ready": ready,
                "error": None
                if ready
                else state["errors"].get(
                    circular.axis,
                    "The pattern axis is not ready in its upstream context",
                ),
                "placements": circular_placements(
                    state["results"][circular.axis], circular.count
                )
                if ready
                else {},
                "volumes": [
                    {
                        **item.model_dump(),
                        "archive_sha256": resolve_volume(plan.recipe, item.volume)[
                            2
                        ].archive.sha256,
                    }
                    for item in circular.volumes
                ],
                "occurrences": [
                    {
                        "key": f"occurrence_{index}",
                        "index": index,
                        "angle_degrees": 360 * index / circular.count,
                        "children": [
                            node.id
                            for node in plan.recipe.nodes
                            if node.managed_by == owner.id
                            and f"/occurrence_{index}/" in str(node.managed_key)
                        ],
                    }
                    for index in range(1, circular.count)
                ],
            }
        return {
            "token": request.token,
            "recipe": plan.recipe.model_dump(),
            **plan.metadata(),
            **(
                {"circular_preview": circular_preview}
                if circular_preview is not None
                else {}
            ),
        }


def apply_feature_reuse(
    graph: FeatureGraph[Any], request: ReuseAuthoringRequest
) -> dict[str, Any]:
    with graph.lock:
        plan = _prepare(graph, request)
        return {
            **graph.replace(plan.recipe, request.token),
            "reuse_authoring": plan.metadata(),
        }
