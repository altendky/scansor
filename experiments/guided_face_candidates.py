"""Read-only surface-first neighbor discovery, not physical topology inference."""

from __future__ import annotations

from contextlib import suppress
from copy import deepcopy
from typing import Any, cast

import numpy as np
from pydantic import Field

from experiments.face_adjacency import classify_pair
from experiments.face_proposals import intersection_key, reference_key
from experiments.feature_graph import (
    ArrangedFace,
    BuildFaces,
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
)
from experiments.surface_extents import primitive, surface_intersection


class FaceCandidatesRequest(Record):
    token: str
    target: SurfaceReference
    surfaces: list[SurfaceReference] = Field(min_length=1)
    owner_id: str | None = None


def depends_on(node_id: str, owner: str, nodes: dict[str, Feature]) -> bool:
    pending = [node_id]
    seen: set[str] = set()
    while pending:
        key = pending.pop()
        if key == owner:
            return True
        if key not in seen:
            seen.add(key)
            pending.extend(dependencies(nodes[key]))
    return False


def physical_source_faces(
    snapshot: dict[str, Any],
    nodes: dict[str, Feature],
    target: SurfaceReference,
    neighbors: list[SurfaceReference],
    owner_id: str | None,
) -> list[str]:
    """Use actual approved boundary uses, never every listed splitter cutter."""
    neighbor_keys = {reference_key(ref.model_dump()) for ref in neighbors}
    target_key = reference_key(target.model_dump())
    result: list[str] = []
    for node in nodes.values():
        if not isinstance(node, (TrimmedFace, ArrangedFace)):
            continue
        if reference_key(node.surface.model_dump()) not in neighbor_keys:
            continue
        if snapshot["states"].get(node.id) != "ready":
            continue
        if owner_id is not None and depends_on(node.id, owner_id, nodes):
            continue
        if isinstance(node, ArrangedFace):
            physical = target_key in snapshot["results"][node.id].get(
                "boundary_keys", []
            )
        else:
            physical = any(
                isinstance(
                    edge := nodes.get(boundary.intersection), SurfaceIntersection
                )
                and {edge.first.model_dump_json(), edge.second.model_dump_json()}
                == {target.model_dump_json(), node.surface.model_dump_json()}
                for boundary in node.boundaries
            )
        if physical:
            result.append(node.id)
    return sorted(result)


def source_records(
    snapshot: dict[str, Any], nodes: dict[str, Feature], face_ids: list[str]
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for face_id in face_ids:
        node = nodes[face_id]
        assert isinstance(node, (TrimmedFace, ArrangedFace))
        uses = (
            [
                {
                    "intersection": deepcopy(snapshot["results"][b.intersection]),
                    "keep": b.keep,
                }
                for b in node.boundaries
            ]
            if isinstance(node, TrimmedFace)
            else []
        )
        records.append(
            {
                "id": face_id,
                "record": deepcopy(snapshot["results"][face_id]),
                "boundary_uses": uses,
            }
        )
    return records


def candidate_faces(
    graph: FeatureGraph, request: FaceCandidatesRequest
) -> dict[str, Any]:
    with graph.lock:
        snapshot = cast(dict[str, Any], graph.snapshot())
        if snapshot["token"] != request.token:
            raise StaleGraph("graph changed before neighbor review; review again")
        recipe = Recipe.model_validate(snapshot["recipe"])
        nodes = {node.id: node for node in recipe.nodes}
        if request.owner_id is not None and not isinstance(
            nodes.get(request.owner_id), BuildFaces
        ):
            raise ValueError("face review must name an existing Build faces action")
        refs = [request.target, *request.surfaces]
        available: dict[str, SurfaceReference] = {}
        for ref in refs:
            if ref.feature not in nodes:
                raise ValueError("neighbor review references unknown geometry")
            _ = surface_reference_kind(ref, nodes)
            available[reference_key(ref.model_dump())] = ref
        if snapshot["states"].get(request.target.feature) != "ready":
            raise ValueError("evaluate target geometry before reviewing neighbors")
        if surface_reference_kind(request.target, nodes) not in (
            "plane",
            "cylinder",
            "cone",
        ):
            raise ValueError("guided faces require a plane, cylinder, or cone target")
        if request.owner_id is not None and depends_on(
            request.target.feature, request.owner_id, nodes
        ):
            raise ValueError("guided target cannot depend on its Build faces action")

        def resolved(ref: SurfaceReference) -> dict[str, Any]:
            value = snapshot["results"][ref.feature]
            return value if ref.surface is None else value["surfaces"][ref.surface]

        def gap(first: dict[str, Any], second: dict[str, Any]) -> float | None:
            points = [
                graph.workspace.local[item.get("ids", [])] for item in (first, second)
            ]
            if any(not len(p) for p in points):
                return None
            low = [p.min(axis=0) for p in points]
            high = [p.max(axis=0) for p in points]
            return float(
                np.linalg.norm(
                    np.maximum(0, np.maximum(low[0] - high[1], low[1] - high[0]))
                )
            )

        target_surface = resolved(request.target)
        target_geometry = primitive(target_surface)
        known: dict[str, list[dict[str, Any]]] = {}
        for node in recipe.nodes:
            if not isinstance(node, BuildFaces):
                continue
            for choice in node.adjacencies:
                key = intersection_key(
                    choice.first.model_dump(), choice.second.model_dump()
                )
                known.setdefault(key, []).append(
                    {"owner_id": node.id, "label": node.label, "state": choice.state}
                )
        candidates: list[dict[str, Any]] = []
        for ref in available.values():
            if ref == request.target:
                continue
            label = nodes[ref.feature].label
            if ref.surface is not None:
                label += " → " + nodes[ref.surface].label
            geometry = None
            evidence: dict[str, Any] = {"observation_bounds_gap": None}
            supported = False
            mathematical = {
                "status": "uncertain",
                "category": "unavailable_geometry",
                "reason": "Evaluate this geometry before choosing it as a boundary.",
            }
            if request.owner_id is not None and depends_on(
                ref.feature, request.owner_id, nodes
            ):
                mathematical["reason"] = (
                    "This geometry depends on the face action and would create a cycle."
                )
            elif snapshot["states"].get(ref.feature) == "ready":
                try:
                    neighbor_surface = resolved(ref)
                    neighbor_geometry = primitive(neighbor_surface)
                    mathematical = classify_pair(target_geometry, neighbor_geometry)
                    evidence["observation_bounds_gap"] = gap(
                        target_surface, neighbor_surface
                    )
                    supported = mathematical["status"] == "candidate" or mathematical[
                        "category"
                    ] in {
                        "general_conic",
                        "general_quadric_pair",
                        "general_parallel_quadric_pair",
                    }
                    if mathematical["status"] != "proven_empty":
                        try:
                            geometry = surface_intersection(
                                target_surface, neighbor_surface
                            )
                            supported = True
                        except ValueError:
                            from experiments.general_face_geometry import (
                                generator_intersection_preview,
                            )

                            with suppress(ValueError):
                                geometry = generator_intersection_preview(
                                    target_geometry, neighbor_geometry
                                )
                except ValueError as error:
                    mathematical = {
                        "status": "uncertain",
                        "category": "unsupported_geometry",
                        "reason": str(error),
                    }
            pair_known = known.get(
                intersection_key(request.target.model_dump(), ref.model_dump()), []
            )
            states = {item["state"] for item in pair_known}
            conflict = len(states) > 1 or (
                "confirmed" in states and mathematical["status"] == "proven_empty"
            )
            state = (
                "conflict"
                if conflict
                else next(iter(states))
                if states
                else "rejected"
                if mathematical["status"] == "proven_empty"
                else "proposed"
                if supported
                else "uncertain"
            )
            shared_ids = physical_source_faces(
                snapshot, nodes, request.target, [ref], request.owner_id
            )
            shared_faces: list[dict[str, Any]] = []
            for face_id in shared_ids:
                from experiments.shared_face_boundaries import (
                    approved_boundary_previews,
                )

                shared: dict[str, Any] = {
                    "id": face_id,
                    "label": nodes[face_id].label,
                    "surface": cast(
                        TrimmedFace | ArrangedFace, nodes[face_id]
                    ).surface.model_dump(),
                }
                try:
                    shared["preview_paths"] = approved_boundary_previews(
                        source_records(snapshot, nodes, [face_id]),
                        request.target.model_dump(),
                        target_surface,
                    )
                except ValueError as error:
                    shared["preview_paths"] = []
                    shared["diagnostic"] = str(error)
                shared_faces.append(shared)
            candidates.append(
                {
                    "key": reference_key(ref.model_dump()),
                    "reference": ref.model_dump(),
                    "label": label,
                    "state": state,
                    "supported": supported,
                    "mathematical": mathematical,
                    "reason": mathematical["reason"],
                    "evidence": evidence,
                    "geometry": geometry,
                    "known_adjacencies": pair_known,
                    "shared_faces": shared_faces,
                    "conflict": conflict,
                }
            )
        candidates.sort(
            key=lambda item: (
                not (item["state"] == "confirmed" and not item["conflict"]),
                not item["supported"],
                item["evidence"]["observation_bounds_gap"]
                if item["evidence"]["observation_bounds_gap"] is not None
                else float("inf"),
                reference_key(item["reference"]),
            )
        )
        return {
            "token": request.token,
            "target": request.target.model_dump(),
            "target_key": reference_key(request.target.model_dump()),
            "candidates": candidates,
            "policy": "Choose physical neighbors deliberately. Ranking and mathematical curves are guidance, not adjacency proof. Omitted neighbors are not rejected; approved finite segments remain distinct from whole intersection curves.",
            "diagnostics": [],
        }
