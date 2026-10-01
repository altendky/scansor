"""Reviewed batch authoring of explicit boundaries; no automatic fit/topology edits."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from typing import Any, cast

from pydantic import Field

from experiments.face_proposals import (
    intersection_key,
    propose_faces,
    reference_key,
    region_key,
)
from experiments.feature_graph import (
    AdjacencyChoice,
    BuildFaces,
    FaceBoundary,
    FaceScope,
    Feature,
    FeatureGraph,
    Recipe,
    Record,
    StaleGraph,
    SurfaceIntersection,
    SurfaceReference,
    TrimmedFace,
    dependencies,
    surface_reference_kind,
    validate_face_review_references,
)


class FacesPreviewRequest(Record):
    token: str
    owner_id: str | None = None
    surfaces: list[SurfaceReference] = Field(min_length=2)
    adjacencies: list[AdjacencyChoice] | None = None
    face_scopes: list[FaceScope] | None = None


class RegionChoice(Record):
    surface: SurfaceReference
    region_key: str


class FacesApplyRequest(FacesPreviewRequest):
    proposal_token: str
    label: str = Field(min_length=1, max_length=120)
    choices: list[RegionChoice] = Field(min_length=1)


def _signature(face: TrimmedFace, nodes: dict[str, Feature]) -> str:
    boundaries: list[dict[str, Any]] = []
    for boundary in face.boundaries:
        edge = nodes[boundary.intersection]
        assert isinstance(edge, SurfaceIntersection)
        boundaries.append(
            {
                "intersection_key": intersection_key(
                    edge.first.model_dump(), edge.second.model_dump()
                ),
                "keep": boundary.keep,
            }
        )
    return region_key(face.surface.model_dump(), boundaries)


def _depends_on(node_id: str, owner: str, nodes: dict[str, Feature]) -> bool:
    pending = [node_id]
    seen: set[str] = set()
    while pending:
        key = pending.pop()
        if key == owner:
            return True
        if key in seen:
            continue
        seen.add(key)
        pending.extend(dependencies(nodes[key]))
    return False


def preview_faces(graph: FeatureGraph, request: FacesPreviewRequest) -> dict[str, Any]:
    """Read-only proposal, bound to recipe AND the actual resolved geometry."""
    with graph.lock:
        snapshot = cast(dict[str, Any], graph.snapshot())
        if snapshot["token"] != request.token:
            raise StaleGraph("graph changed before face proposal; preview again")
        recipe = Recipe.model_validate(snapshot["recipe"])
        nodes = {node.id: node for node in recipe.nodes}
        if request.owner_id is not None and not isinstance(
            nodes.get(request.owner_id), BuildFaces
        ):
            raise ValueError("face update must name an existing Build faces action")
        owner = nodes.get(request.owner_id or "")
        adjacencies = (
            request.adjacencies
            if request.adjacencies is not None
            else owner.adjacencies
            if isinstance(owner, BuildFaces)
            else []
        )
        scopes = (
            request.face_scopes
            if request.face_scopes is not None
            else owner.face_scopes
            if isinstance(owner, BuildFaces)
            else []
        )
        validate_face_review_references(request.surfaces, adjacencies, scopes, nodes)
        scopes_by_key = {
            reference_key(scope.surface.model_dump()): scope for scope in scopes
        }
        scope_geometry: list[dict[str, Any]] = []
        previously_reused = (
            set(owner.reused_faces) if isinstance(owner, BuildFaces) else set()
        )
        keys = [reference_key(ref.model_dump()) for ref in request.surfaces]
        if len(set(keys)) != len(keys):
            raise ValueError("build faces surface references must be unique")
        inputs: list[dict[str, Any]] = []
        surface_labels: list[dict[str, Any]] = []
        for ref in request.surfaces:
            if ref.feature not in nodes:
                raise ValueError("build faces references an unknown geometry feature")
            kind = surface_reference_kind(ref, nodes)
            if kind not in ("plane", "cylinder", "cone"):
                raise ValueError(
                    "build faces currently supports planes, cylinders, and cones"
                )
            if snapshot["states"].get(ref.feature) != "ready":
                raise ValueError(
                    "evaluate the selected geometry before previewing faces"
                )
            result = snapshot["results"][ref.feature]
            surface = deepcopy(
                result if ref.surface is None else result["surfaces"][ref.surface]
            )
            label = nodes[ref.feature].label
            if ref.surface is not None:
                label += " → " + nodes[ref.surface].label
            ids = surface.get("ids", [])
            domains: list[dict[str, Any]] | None = None
            scope = scopes_by_key.get(reference_key(ref.model_dump()))
            if scope is not None:
                domains = []
                for face_id in sorted(scope.faces):
                    if request.owner_id is not None and _depends_on(
                        face_id, request.owner_id, nodes
                    ):
                        raise ValueError(
                            "physical scope cannot depend on this Build faces action"
                        )
                    if snapshot["states"].get(face_id) != "ready":
                        raise ValueError(
                            "evaluate physical scope faces before previewing"
                        )
                    domains.append(deepcopy(snapshot["results"][face_id]))
                scope_geometry.append({"surface": ref.model_dump(), "faces": domains})
            inputs.append(
                {
                    "reference": ref.model_dump(),
                    "label": label,
                    "surface": surface,
                    "observations": graph.workspace.local[ids],
                    "weights": graph.workspace.data.weights[ids],
                    "face_domains": domains,
                }
            )
            surface_labels.append(
                {"reference": ref.model_dump(), "label": label, "kind": kind}
            )
        plan = (
            propose_faces(
                inputs, adjacencies=[choice.model_dump() for choice in adjacencies]
            )
            if adjacencies
            else propose_faces(inputs)
        )
        existing_edges: dict[str, list[SurfaceIntersection]] = {}
        existing_faces: dict[str, list[TrimmedFace]] = {}
        for node in recipe.nodes:
            if isinstance(node, SurfaceIntersection):
                key = intersection_key(
                    node.first.model_dump(), node.second.model_dump()
                )
                existing_edges.setdefault(key, []).append(node)
            elif isinstance(node, TrimmedFace):
                existing_faces.setdefault(_signature(node, nodes), []).append(node)
        for edge in plan["intersections"]:
            candidates = [
                candidate
                for candidate in existing_edges.get(edge["key"], [])
                if candidate.managed_by == request.owner_id
                or request.owner_id is None
                or not _depends_on(candidate.id, request.owner_id, nodes)
            ]
            owned = next(
                (
                    n
                    for n in candidates
                    if n.managed_by == request.owner_id and request.owner_id is not None
                ),
                None,
            )
            edge["existing_id"] = (owned or candidates[0]).id if candidates else None
        for face in plan["faces"]:
            for region in face["regions"]:
                candidates = existing_faces.get(region["key"], [])
                owned = next(
                    (
                        n
                        for n in candidates
                        if request.owner_id is not None
                        and n.managed_by == request.owner_id
                    ),
                    None,
                )
                external = next(
                    (
                        n
                        for n in candidates
                        if n.managed_by != request.owner_id
                        and (
                            request.owner_id is None
                            or not _depends_on(n.id, request.owner_id, nodes)
                        )
                    ),
                    None,
                )
                # None is also the default owner for manual features.
                if request.owner_id is None:
                    external = next(iter(candidates), None)
                else:
                    external = next(
                        (
                            candidate
                            for candidate in candidates
                            if candidate.id in previously_reused
                        ),
                        external,
                    )
                region["owned_face_id"] = owned.id if owned is not None else None
                region["existing_face_id"] = (
                    external.id if external is not None else None
                )
                region["previously_selected"] = owned is not None or (
                    external is not None and external.id in previously_reused
                )
        plan.update(
            token=request.token,
            owner_id=request.owner_id,
            surfaces=surface_labels,
            adjacency_choices=[
                choice.model_dump()
                for choice in sorted(
                    adjacencies,
                    key=lambda choice: intersection_key(
                        choice.first.model_dump(), choice.second.model_dump()
                    ),
                )
            ],
            face_scopes=[
                {"surface": scope.surface.model_dump(), "faces": sorted(scope.faces)}
                for scope in sorted(
                    scopes, key=lambda scope: reference_key(scope.surface.model_dump())
                )
            ],
            scope_geometry_sha256=hashlib.sha256(
                json.dumps(scope_geometry, sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        )
        plan["proposal_token"] = hashlib.sha256(
            json.dumps(plan, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        return plan


def _unique_label(stem: str, names: set[str]) -> str:
    label = stem[:120]
    index = 2
    while label.strip().casefold() in names:
        suffix = f" {index}"
        label = stem[: 120 - len(suffix)] + suffix
        index += 1
    names.add(label.strip().casefold())
    return label


def _ordered(nodes: list[Feature]) -> list[Feature]:
    """Keep original relative priority, moving only as DAG dependencies require."""
    remaining = nodes.copy()
    result: list[Feature] = []
    seen: set[str] = set()
    while remaining:
        next_node = next(
            (node for node in remaining if set(dependencies(node)) <= seen), None
        )
        if next_node is None:
            raise ValueError("build faces update would introduce a dependency cycle")
        result.append(next_node)
        seen.add(next_node.id)
        remaining.remove(next_node)
    return result


def apply_faces(graph: FeatureGraph, request: FacesApplyRequest) -> dict[str, Any]:
    """Atomically reconcile only this owner's outputs after explicit review."""
    with graph.lock:
        plan = preview_faces(graph, request)
        if plan["proposal_token"] != request.proposal_token:
            raise StaleGraph("face proposals changed; preview and review again")
        snapshot = cast(dict[str, Any], graph.snapshot())
        recipe = Recipe.model_validate(snapshot["recipe"])
        nodes = {node.id: node for node in recipe.nodes}
        owner_id = request.owner_id or "build_faces_" + uuid.uuid4().hex
        old_owner = nodes.get(owner_id)
        old_outputs = {
            node.managed_key: node
            for node in recipe.nodes
            if node.managed_by == owner_id
        }
        labels = {
            node.label.strip().casefold()
            for node in recipe.nodes
            if node.id != owner_id
        }
        if request.label.strip().casefold() in labels:
            raise ValueError("feature names must be unique")
        labels.add(request.label.strip().casefold())
        by_surface = {reference_key(face["surface"]): face for face in plan["faces"]}
        selections: list[tuple[dict[str, Any], dict[str, Any]]] = []
        seen: set[str] = set()
        for choice in request.choices:
            key = reference_key(choice.surface.model_dump())
            if key in seen:
                raise ValueError("choose at most one region per surface")
            seen.add(key)
            face = by_surface.get(key)
            region = (
                next(
                    (r for r in face["regions"] if r["key"] == choice.region_key), None
                )
                if face is not None
                else None
            )
            if face is None or region is None:
                raise ValueError("accepted region is not in the reviewed proposal")
            if face.get("blocked_by_adjacency"):
                raise ValueError(
                    "confirmed adjacency is unsupported; resolve the boundary before applying this face"
                )
            if not region["preview"]["positions"] or not region["preview"]["indices"]:
                raise ValueError(
                    "accepted region has no valid preview; resolve its diagnostics before applying"
                )
            selections.append((cast(dict[str, Any], face), region))
        used_keys = {
            b["intersection_key"]
            for _, region in selections
            for b in region["boundaries"]
        }
        edges = {edge["key"]: edge for edge in plan["intersections"]}
        accepted_adjacencies = {
            intersection_key(choice["first"], choice["second"]): choice
            for choice in plan["adjacency_choices"]
        }
        for key in used_keys:
            edge = edges[key]
            accepted_adjacencies[key] = {
                "first": edge["first"],
                "second": edge["second"],
                "state": "confirmed",
            }
        outputs: dict[str, Feature] = {}
        edge_ids: dict[str, str] = {}
        reused_edges: list[str] = []
        reused_faces: list[str] = []

        def output_id(key: str) -> str:
            previous = old_outputs.get(key)
            if previous is not None:
                return previous.id
            value = (
                "built_"
                + hashlib.sha256((owner_id + "/" + key).encode()).hexdigest()[:32]
            )
            if value in nodes:
                raise ValueError("generated output ID is already in use")
            return value

        # Manual/external faces remain unchanged, including their edge choices.
        # Do not create unused owned edges for those already existing faces.
        needed_for_new = {
            b["intersection_key"]
            for _, region in selections
            if not region.get("existing_face_id") or region.get("owned_face_id")
            for b in region["boundaries"]
        }
        for key in sorted(used_keys):
            edge = edges[key]
            existing = nodes.get(edge.get("existing_id"))
            if existing is not None and existing.managed_by != owner_id:
                edge_ids[key] = existing.id
                reused_edges.append(existing.id)
                continue
            if key not in needed_for_new:
                continue
            managed_key = "intersection/" + key
            previous = old_outputs.get(managed_key)
            output = SurfaceIntersection(
                id=output_id(managed_key),
                label=previous.label
                if previous is not None
                else _unique_label(request.label + " · " + edge["label"], labels),
                operation="surface_intersection",
                first=SurfaceReference.model_validate(edge["first"]),
                second=SurfaceReference.model_validate(edge["second"]),
                managed_by=owner_id,
                managed_key=managed_key,
            )
            outputs[output.id] = output
            edge_ids[key] = output.id
        for face, region in selections:
            if region.get("existing_face_id") and not region.get("owned_face_id"):
                reused_faces.append(region["existing_face_id"])
                continue
            managed_key = "face/" + reference_key(face["surface"])
            previous = old_outputs.get(managed_key)
            output = TrimmedFace(
                id=output_id(managed_key),
                label=previous.label
                if previous is not None
                else _unique_label(
                    request.label + " · " + face["label"] + " face", labels
                ),
                operation="trimmed_face",
                surface=SurfaceReference.model_validate(face["surface"]),
                boundaries=[
                    FaceBoundary(
                        intersection=edge_ids[b["intersection_key"]], keep=b["keep"]
                    )
                    for b in region["boundaries"]
                ],
                managed_by=owner_id,
                managed_key=managed_key,
            )
            outputs[output.id] = output
        removed = {node.id for node in old_outputs.values()} - set(outputs)
        for node in recipe.nodes:
            if (
                node.id not in removed
                and node.id != owner_id
                and node.managed_by != owner_id
                and (set(dependencies(node)) & removed or node.managed_by in removed)
            ):
                raise ValueError(
                    f"cannot remove generated output used by {node.label!r}; revise its consumers first"
                )
        owner = BuildFaces(
            id=owner_id,
            label=request.label,
            operation="build_faces",
            surfaces=request.surfaces,
            reused_faces=list(dict.fromkeys(reused_faces)),
            reused_intersections=list(dict.fromkeys(reused_edges)),
            adjacencies=[
                AdjacencyChoice.model_validate(accepted_adjacencies[key])
                for key in sorted(accepted_adjacencies)
            ],
            face_scopes=[
                FaceScope.model_validate(scope) for scope in plan["face_scopes"]
            ],
            group_id=old_owner.group_id if old_owner is not None else None,
        )
        updated: list[Feature] = []
        for node in recipe.nodes:
            if node.id == owner_id:
                updated.append(owner)
            elif node.managed_by == owner_id:
                if node.id in outputs:
                    updated.append(outputs.pop(node.id))
            else:
                updated.append(node)
        if old_owner is None:
            updated.append(owner)
        updated.extend(outputs.values())
        if len(updated) > 100:
            raise ValueError(
                "build faces would exceed the current 100-action recipe limit; review a smaller surface set"
            )
        output = recipe.output
        if (
            old_owner is None and nodes[output].operation != "transform"
        ) or output in removed:
            output = owner_id
        revised = Recipe(
            schema_version=recipe.schema_version,
            nodes=_ordered(updated),
            groups=recipe.groups,
            output=output,
        )
        return cast(dict[str, Any], graph.replace(revised, request.token))
