"""Reviewable connected cells constructed by the native surface arrangement."""

from collections.abc import Callable
from typing import Any

import numpy as np


def propose_regions(
    item: dict[str, Any],
    face: dict[str, Any],
    budget: int,
    key_for_region: Callable[..., str],
) -> None:
    from experiments.face_arrangement import arrange_faces

    cutters = item["cutters"]
    try:
        records = arrange_faces(
            item["geometry"],
            [
                {
                    "key": cutter["key"],
                    "geometry": cutter["geometry"],
                    **(
                        {"face_domains": cutter["face_domains"]}
                        if cutter.get("face_domains")
                        else {}
                    ),
                }
                for cutter in cutters
            ],
            domains=item.get("face_domains"),
            observations=item["observations"],
            coverage=np.concatenate(
                [item["observations"], *(c["observations"] for c in cutters)]
            ),
        )
    except ValueError as error:
        face["status"] = "unsupported"
        face["diagnostics"].append(f"Surface arrangement unavailable: {error}")
        return
    if len(records) > budget:
        raise ValueError("face region proposal limit exceeded")
    weights = item["weights"]
    finite_sources = sorted(
        {
            source_id
            for cutter in cutters
            for source_id in cutter.get("finite_boundary_sources", [])
        }
    )
    normalized = weights / float(np.max(weights)) if len(weights) else weights
    total_weight = float(np.sum(normalized))
    covered = np.zeros(len(weights), dtype=bool)
    interior_occupancy = np.zeros(len(weights), dtype=np.int64)
    all_defined = np.ones(len(weights), dtype=bool)
    for index, record in enumerate(records):
        masks = record["evidence"]
        interior = np.asarray(masks["interior"], dtype=bool)
        boundary = np.asarray(masks["boundary"], dtype=bool)
        defined = np.asarray(masks["defined"], dtype=bool)
        if any(mask.shape != weights.shape for mask in (interior, boundary, defined)):
            raise ValueError("arrangement evidence must account for every observation")
        covered |= interior | boundary
        interior_occupancy += interior
        all_defined &= defined
        selector = record["region_identity"]
        key = key_for_region(
            item["reference"],
            [c["reference"] for c in cutters],
            item.get("domain_ids", []),
            selector,
            finite_sources,
        )
        face["regions"].append(
            {
                "key": key,
                "label": f"Region {index + 1}",
                "boundaries": [],
                "arrangement": {
                    "cutters": [c["reference"] for c in cutters],
                    "domains": item.get("domain_ids", []),
                    "selector": selector,
                    "finite_boundary_sources": finite_sources,
                },
                "boundary_keys": record["boundary_keys"],
                "bounded": record["bounded"],
                "geometry": record["geometry"],
                "surface_kind": record["surface_kind"],
                "preview_clipped": record.get("preview_clipped", not record["bounded"]),
                "bounds": record["bounds"],
                "loops": record["loops"],
                "preview": record["preview"],
                "evidence": {
                    "interior_count": int(np.sum(interior)),
                    "boundary_count": int(np.sum(boundary)),
                    "elsewhere_count": int(np.sum(defined & ~interior & ~boundary)),
                    "undefined_count": int(np.sum(~defined)),
                    "total_count": len(weights),
                    "weighted_fraction": float(
                        np.sum(normalized[interior]) / total_weight
                    )
                    if total_weight
                    else 0.0,
                },
            }
        )
    open_count = sum(not region["bounded"] for region in face["regions"])
    if open_count:
        face["diagnostics"].append(
            f"{open_count} open regions omitted from suggestions; add physical boundaries."
        )
    if item["blocked_by_adjacency"]:
        face["status"] = "unsupported"
    elif np.any(interior_occupancy > 1):
        face["status"] = "unsupported"
        face["blocked_by_geometry"] = True
        face["diagnostics"].append(
            "Native cell classification overlaps: region review is unsafe until the arrangement is resolved."
        )
    elif item["unresolved_adjacencies"]:
        face["status"] = "requires_adjacency_review"
    elif not len(weights):
        face["status"] = "no_observations"
    elif np.all(all_defined & covered) and any(
        r["evidence"]["interior_count"] for r in face["regions"]
    ):
        face["suggested_region_keys"] = [
            r["key"]
            for r in face["regions"]
            if r["bounded"] and r["evidence"]["interior_count"]
        ]
        face["status"] = (
            "suggested" if face["suggested_region_keys"] else "missing_boundaries"
        )
        if len(face["suggested_region_keys"]) == 1:
            face["suggested_region_key"] = face["suggested_region_keys"][0]
    else:
        face["status"] = "ambiguous"
        face["diagnostics"].append(
            "Boundary-only, uncovered, or undefined observation evidence requires explicit region review."
        )
