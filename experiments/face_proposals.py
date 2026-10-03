"""Reviewable face proposals; observation evidence never changes fits."""

from __future__ import annotations

import hashlib
import json
from itertools import combinations, pairwise
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.face_adjacency import circle_in_face_domains, classify_pair
from experiments.face_building_limits import MAX_PAIRS, MAX_REGIONS
from experiments.surface_extents import (
    distance_tolerance,
    primitive,
    surface_intersection,
    trimmed_face,
)

POLICY = (
    "Original selected observations suggest a region only when strictly interior "
    "evidence occupies candidate cells, every projection is defined and accounted for, and "
    "no unresolved adjacency remains. Mathematical intersections are candidates, "
    "not physical boundaries. Unsupported unreviewed pairs remain warnings; "
    "confirmed unsupported boundaries block their affected faces. Observation "
    "bounds are hints, not physical caps or disjointness proofs. Boundary-only, undefined, or missing evidence does "
    "not choose a region; disconnected arrangement cells may be reviewed together. Suggestions require confirmation and do not prove "
    "physical adjacency, sharp edges, or complete scan coverage. No observations "
    "or fit weights are removed or changed. Open ends remain physically unbounded "
    "and open regions are not suggested."
)


def _reference(reference: dict[str, Any]) -> dict[str, Any]:
    feature, surface = reference.get("feature"), reference.get("surface")
    if not isinstance(feature, str) or not feature:
        raise ValueError("surface references require a nonempty feature")
    if surface is not None and (not isinstance(surface, str) or not surface):
        raise ValueError("member surface must be null or a nonempty string")
    return {"feature": feature, "surface": surface}


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:32]


def reference_key(reference: dict[str, Any]) -> str:
    """Stable exact reference identity, retaining explicit solve context."""
    return _digest(_reference(reference))


def intersection_key(first: dict[str, Any], second: dict[str, Any]) -> str:
    references = [_reference(first), _reference(second)]
    references.sort(key=lambda reference: json.dumps(reference, sort_keys=True))
    return _digest(references)


def region_key(reference: dict[str, Any], boundaries: list[dict[str, Any]]) -> str:
    return _digest(
        [
            _reference(reference),
            sorted((use["intersection_key"], use["keep"]) for use in boundaries),
        ]
    )


def arrangement_region_key(
    reference: dict[str, Any],
    cutters: list[dict[str, Any]],
    domains: list[str],
    selector: dict[str, Any],
    finite_boundary_sources: list[str] | None = None,
) -> str:
    identity: list[Any] = [
        _reference(reference),
        [_reference(cutter) for cutter in cutters],
        domains,
        {
            key: value
            for key, value in selector.items()
            if key != "finite_sides" or value
        },
    ]
    if finite_boundary_sources:
        identity.append({"finite_boundary_sources": sorted(finite_boundary_sources)})
    return _digest(identity)


def _observation_bounds_gap(
    first: dict[str, Any], second: dict[str, Any]
) -> float | None:
    if first["observation_bounds"] is None or second["observation_bounds"] is None:
        return None
    first_lower, first_upper = first["observation_bounds"]
    second_lower, second_upper = second["observation_bounds"]
    gap = np.maximum(
        0, np.maximum(first_lower - second_upper, second_lower - first_upper)
    )
    value = float(np.linalg.norm(gap))
    return value if np.isfinite(value) else None


def _projected_coordinates(
    geometry: dict[str, Any],
    observations: NDArray[np.float64],
    origin: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.bool_]]:
    axis = np.asarray(geometry["axis"])
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        relative = observations - origin
        axial = relative @ axis
        radial = np.linalg.norm(relative - axial[:, None] * axis, axis=1)
        if geometry["kind"] == "plane":
            coordinates = radial
            defined = np.isfinite(coordinates)
        else:
            slope = geometry["slope"]
            coordinates = (axial + slope * (radial - geometry["radius"])) / (
                1 + slope * slope
            )
            radius = geometry["radius"] + slope * coordinates
            defined = (
                np.isfinite(coordinates)
                & np.isfinite(radial)
                & (radial > 0)
                & np.isfinite(radius)
                & (radius > 0)
            )
    return coordinates, defined


def _evidence(
    coordinates: NDArray[np.float64],
    defined: NDArray[np.bool_],
    weights: NDArray[np.float64],
    lower: float | None,
    upper: float | None,
    tolerance: float,
    planar: bool,
) -> dict[str, Any]:
    boundary = np.zeros(len(coordinates), dtype=bool)
    if lower is not None and not (planar and lower == 0):
        boundary |= np.abs(coordinates - lower) <= tolerance
    if upper is not None:
        boundary |= np.abs(coordinates - upper) <= tolerance
    boundary &= defined
    interior = defined & ~boundary
    if lower is not None:
        interior &= coordinates >= lower
    if upper is not None:
        interior &= coordinates <= upper
    normalized = weights / float(np.max(weights)) if len(weights) else weights
    total_weight = float(np.sum(normalized))
    values = coordinates[defined]
    return {
        "interior_count": int(np.sum(interior)),
        "boundary_count": int(np.sum(boundary)),
        "elsewhere_count": int(np.sum(defined & ~interior & ~boundary)),
        "undefined_count": int(np.sum(~defined)),
        "total_count": len(coordinates),
        "weighted_fraction": float(np.sum(normalized[interior]) / total_weight)
        if total_weight
        else 0.0,
        "projected_range": [float(np.min(values)), float(np.max(values))]
        if len(values)
        else None,
    }


def propose_faces(
    inputs: list[dict[str, Any]],
    *,
    adjacencies: list[dict[str, Any]] | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Partition supported cuts, expose uncertainty, and leave Apply to the user."""
    if not inputs:
        raise ValueError("select at least one surface")
    pair_count = (
        len(inputs) - 1 if target is not None else len(inputs) * (len(inputs) - 1) // 2
    )
    if pair_count > MAX_PAIRS:
        raise ValueError("surface pair-check budget exceeded")
    prepared: list[dict[str, Any]] = []
    identities: set[str] = set()
    for item in inputs:
        reference = _reference(item["reference"])
        key = reference_key(reference)
        if key in identities:
            raise ValueError("duplicate exact surface reference")
        identities.add(key)
        observations = np.asarray(item["observations"], dtype=float)
        weights = np.asarray(item["weights"], dtype=float)
        if (
            observations.ndim != 2
            or observations.shape[1] != 3
            or not np.isfinite(observations).all()
        ):
            raise ValueError("observations must be finite Nx3 coordinates")
        if (
            weights.shape != (len(observations),)
            or not np.isfinite(weights).all()
            or np.any(weights <= 0)
        ):
            raise ValueError(
                "weights must be one finite positive value per observation"
            )
        try:
            geometry = primitive(item["surface"])
            errors: list[str] = []
        except ValueError as error:
            geometry = None
            errors = [str(error)]
        prepared.append(
            {
                **item,
                "reference": reference,
                "key": key,
                "observations": observations,
                "weights": weights,
                "observation_bounds": (
                    observations.min(axis=0),
                    observations.max(axis=0),
                )
                if len(observations)
                else None,
                "geometry": geometry,
                "errors": errors,
                "warnings": [],
                "pending_adjacencies": [],
                "unresolved_adjacencies": [],
                "blocked_by_adjacency": False,
                "edges": [],
                "cutters": [],
                "requires_arrangement": False,
            }
        )
    prepared.sort(key=lambda item: item["key"])
    target_key = reference_key(_reference(target)) if target is not None else None
    if target_key is not None and target_key not in identities:
        raise ValueError("guided face target must be a selected surface")
    intersections: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    choices: dict[str, str] = {}
    for choice in adjacencies or []:
        first_ref, second_ref = (
            _reference(choice["first"]),
            _reference(choice["second"]),
        )
        key = intersection_key(first_ref, second_ref)
        if (
            reference_key(first_ref) == reference_key(second_ref)
            or not {reference_key(first_ref), reference_key(second_ref)} <= identities
        ):
            raise ValueError("adjacency must name two distinct selected surfaces")
        if key in choices:
            raise ValueError("duplicate adjacency decision")
        if choice["state"] not in ("confirmed", "rejected"):
            raise ValueError("adjacency decisions must be confirmed or rejected")
        choices[key] = choice["state"]
    reviewed: list[dict[str, Any]] = []
    for first, second in combinations(prepared, 2):
        if target_key is not None and target_key not in (first["key"], second["key"]):
            continue
        key = intersection_key(first["reference"], second["reference"])
        mathematical = (
            classify_pair(first["geometry"], second["geometry"])
            if (first["geometry"] is not None and second["geometry"] is not None)
            else {
                "status": "uncertain",
                "category": "invalid_primitive",
                "reason": "Invalid or unsupported primitive geometry.",
            }
        )
        geometry = None
        reason = mathematical["reason"]
        rejected = mathematical["status"] == "proven_empty"
        if not rejected and choices.get(key) != "rejected":
            try:
                geometry = surface_intersection(first["surface"], second["surface"])
            except ValueError as error:
                reason = str(error)
        if geometry is not None:
            for item in (first, second):
                domains = item.get("face_domains")
                if (
                    domains is not None
                    and geometry["kind"] == "circle"
                    and circle_in_face_domains(geometry, domains) is False
                ):
                    rejected = True
                    reason = "Intersection is outside the explicitly selected physical face scope."
        if rejected and choices.get(key) == "confirmed":
            raise ValueError(
                "confirmed adjacency contradicts a proven empty intersection or physical scope"
            )
        native_pair_supported = (
            first["geometry"] is not None
            and second["geometry"] is not None
            and (
                mathematical["status"] == "candidate"
                or mathematical["category"]
                in {
                    "general_conic",
                    "general_quadric_pair",
                    "general_parallel_quadric_pair",
                }
            )
        )
        state = (
            "rejected"
            if rejected
            else choices.get(key)
            or (
                "proposed"
                if geometry is not None or native_pair_supported
                else "uncertain"
            )
        )
        pair = {
            "key": key,
            "first": first["reference"],
            "second": second["reference"],
            "label": f"{first['label']} / {second['label']}",
            "state": state,
            "mathematical": mathematical,
            "reason": reason,
            "supported": geometry is not None or native_pair_supported,
            "evidence": {
                "observation_bounds_gap": _observation_bounds_gap(first, second)
            },
        }
        reviewed.append(pair)
        if state == "rejected":
            continue
        for item, other in ((first, second), (second, first)):
            if other["geometry"] is not None and (
                geometry is not None or native_pair_supported
            ):
                item["cutters"].append(
                    {
                        "key": other["key"],
                        "reference": other["reference"],
                        "geometry": other["geometry"],
                        "observations": other["observations"],
                        **(
                            {
                                "face_domains": other["cut_domains"],
                                "finite_boundary_sources": other[
                                    "finite_boundary_sources"
                                ],
                            }
                            if other.get("cut_domains")
                            else {}
                        ),
                    }
                )
        if state in ("proposed", "uncertain"):
            first["pending_adjacencies"].append(key)
            second["pending_adjacencies"].append(key)
        if geometry is None:
            if native_pair_supported:
                pair["reason"] = (
                    "Native arrangement required; a single standalone intersection curve is unavailable."
                )
                for item in (first, second):
                    item["requires_arrangement"] = True
                continue
            diagnostics.append({**pair, "message": reason})
            for item, other in ((first, second), (second, first)):
                item["warnings"].append(f"With {other['label']}: {reason}")
                if state == "confirmed":
                    item["blocked_by_adjacency"] = True
                else:
                    item["unresolved_adjacencies"].append(key)
            continue
        edge = {
            "key": key,
            "first": first["reference"],
            "second": second["reference"],
            "label": f"{first['label']} / {second['label']}",
            "geometry": geometry,
        }
        intersections.append(edge)
        first["edges"].append(edge)
        second["edges"].append(edge)
    if len(intersections) > MAX_PAIRS:
        raise ValueError("intersection proposal limit exceeded")
    faces: list[dict[str, Any]] = []
    region_count = 0
    for item in prepared:
        if target_key is not None and item["key"] != target_key:
            continue
        use_arrangement = bool(item["cutters"]) and (
            any(cutter.get("face_domains") for cutter in item["cutters"])
            or item["requires_arrangement"]
            or bool(item["unresolved_adjacencies"])
            or bool(item.get("face_domains"))
            or any(edge["geometry"]["kind"] != "circle" for edge in item["edges"])
        )
        if (
            item["geometry"] is not None
            and item["geometry"]["kind"] == "cone"
            and not use_arrangement
        ):
            # General cone curves can bound a planar cap, but this slice does
            # not yet construct their periodic lateral topology. Retain only
            # explicit circular partial regions, never substitute them for a
            # confirmed unsupported boundary.
            unsupported = [
                edge for edge in item["edges"] if edge["geometry"]["kind"] != "circle"
            ]
            for edge in unsupported:
                reason = "Oblique cone lateral boundaries require explicit branch and seam review; circular partial regions do not include this cut."
                item["warnings"].append(reason)
                if choices.get(edge["key"]) == "confirmed":
                    item["blocked_by_adjacency"] = True
                else:
                    item["unresolved_adjacencies"].append(edge["key"])
                diagnostics.append({"key": edge["key"], "message": reason})
            item["edges"] = [edge for edge in item["edges"] if edge not in unsupported]
        face: dict[str, Any] = {
            "key": item["key"],
            "surface": item["reference"],
            "label": item["label"],
            "status": "unsupported"
            if item["errors"] or item["blocked_by_adjacency"]
            else (
                "requires_adjacency_review"
                if item["unresolved_adjacencies"]
                else "missing_boundaries"
            ),
            "suggested_region_key": None,
            "diagnostics": [*item["errors"], *item["warnings"]],
            "blocked_by_adjacency": item["blocked_by_adjacency"],
            "pending_adjacencies": sorted(item["pending_adjacencies"]),
            "adjacency_review_complete": not item["pending_adjacencies"],
            "regions": [],
        }
        faces.append(face)
        if item["geometry"] is not None and use_arrangement:
            from experiments.arrangement_face_proposals import (
                propose_regions as propose_arranged_regions,
            )

            propose_arranged_regions(
                item, face, MAX_REGIONS - region_count, arrangement_region_key
            )
            region_count += len(face["regions"])
            continue
        if not item["edges"] or item["geometry"] is None:
            if not item["edges"]:
                face["diagnostics"].append(
                    "No supported physical boundary was found; observation extrema are not caps."
                )
            continue
        geometry = item["geometry"]
        planar = geometry["kind"] == "plane"
        general = any(edge["geometry"]["kind"] != "circle" for edge in item["edges"])
        if planar and not general:
            positions = [
                np.asarray(edge["geometry"]["center_display"]) for edge in item["edges"]
            ]
            tolerance = distance_tolerance(
                positions, max(edge["geometry"]["radius"] for edge in item["edges"])
            )
            general = any(
                np.linalg.norm(p - positions[0]) > tolerance for p in positions
            )
        if general:
            from experiments.arrangement_face_proposals import propose_regions

            propose_regions(
                item, face, MAX_REGIONS - region_count, arrangement_region_key
            )
            region_count += len(face["regions"])
            continue
        centers = [
            np.asarray(edge["geometry"]["center_display"]) for edge in item["edges"]
        ]
        scale = max(edge["geometry"]["radius"] for edge in item["edges"])
        tolerance = distance_tolerance(centers, scale)
        origin = centers[0] if planar else np.asarray(geometry["origin"])
        if planar and any(
            np.linalg.norm(center - origin) > tolerance for center in centers
        ):
            face["status"] = "unsupported"
            face["diagnostics"].append(
                "Nonconcentric planar cuts require a general planar region representation; no subgroup was silently selected."
            )
            continue
        axis = np.asarray(geometry["axis"])
        cuts = [
            (
                float(edge["geometry"]["radius"])
                if planar
                else float((center - origin) @ axis),
                edge,
            )
            for edge, center in zip(item["edges"], centers, strict=True)
        ]
        cuts.sort(key=lambda cut: (cut[0], cut[1]["key"]))
        if any(upper[0] - lower[0] <= tolerance for lower, upper in pairwise(cuts)):
            face["status"] = "ambiguous"
            face["diagnostics"].append(
                "Distinct references produce coincident cuts; choose the intended shared edge explicitly."
            )
            continue
        region_count += len(cuts) + 1
        if region_count > MAX_REGIONS:
            raise ValueError("face region proposal limit exceeded")
        coordinates, defined = _projected_coordinates(
            geometry, item["observations"], origin
        )
        preview_failed = False
        for index in range(len(cuts) + 1):
            lower = cuts[index - 1][0] if index else (0.0 if planar else None)
            upper = cuts[index][0] if index < len(cuts) else None
            boundaries: list[dict[str, Any]] = []
            for edge, retains_lower in (
                (cuts[index - 1][1] if index else None, True),
                (cuts[index][1] if index < len(cuts) else None, False),
            ):
                if edge is None:
                    continue
                if planar:
                    keep = "outside" if retains_lower else "inside"
                else:
                    aligned = np.asarray(edge["geometry"]["axis_display"]) @ axis > 0
                    keep = "positive" if retains_lower == aligned else "negative"
                boundaries.append({"intersection_key": edge["key"], "keep": keep})
            by_key = {edge["key"]: edge for edge in item["edges"]}
            uses = [
                (
                    {"intersection": use["intersection_key"], "keep": use["keep"]},
                    by_key[use["intersection_key"]]["geometry"],
                )
                for use in boundaries
            ]
            label = f"{'Radial' if planar else 'Axial'} [{lower if lower is not None else 'open'}, {upper if upper is not None else 'open'}]"
            preview: dict[str, Any]
            try:
                preview = trimmed_face(item["surface"], uses, item["observations"])
            except ValueError as error:
                preview_failed = True
                face["diagnostics"].append(f"{label}: preview unavailable: {error}")
                preview = {
                    "bounded": lower is not None and upper is not None,
                    "bounds": {"radial" if planar else "axial": [lower, upper]},
                    "preview": {"positions": [], "indices": []},
                }
            face["regions"].append(
                {
                    "key": region_key(item["reference"], boundaries),
                    "label": label,
                    "boundaries": boundaries,
                    "bounded": preview["bounded"],
                    "bounds": preview["bounds"],
                    "geometry": preview.get("geometry"),
                    "surface_kind": preview.get("surface_kind"),
                    "loops": preview.get("loops", []),
                    "preview": preview["preview"],
                    "evidence": _evidence(
                        coordinates,
                        defined,
                        item["weights"],
                        lower,
                        upper,
                        tolerance,
                        planar,
                    ),
                }
            )
        open_count = sum(not region["bounded"] for region in face["regions"])
        if open_count:
            face["diagnostics"].append(
                f"{open_count} open regions omitted from suggestions; add physical boundaries."
            )
        if item["errors"] or preview_failed or item["blocked_by_adjacency"]:
            face["status"] = "unsupported"
            continue
        if item["unresolved_adjacencies"]:
            face["status"] = "requires_adjacency_review"
            face["diagnostics"].append(
                "Unsupported candidate pairs are unreviewed, not declared boundaries. Review adjacency and choose any partial region explicitly."
            )
            continue
        if not len(item["observations"]):
            face["status"] = "no_observations"
            face["diagnostics"].append(
                "No selected observation evidence; choose a region explicitly."
            )
            continue
        populated = [
            region for region in face["regions"] if region["evidence"]["interior_count"]
        ]
        if len(populated) == 1 and bool(np.all(defined)):
            if not populated[0]["bounded"]:
                face["status"] = "missing_boundaries"
                continue
            face["status"] = "suggested"
            face["suggested_region_key"] = populated[0]["key"]
            if (
                populated[0]["evidence"]["interior_count"] < 3
                or np.ptp(coordinates[defined]) <= tolerance
            ):
                face["diagnostics"].append(
                    "Sparse or negligible-spread evidence requires review; unanimity does not establish coverage or adjacency."
                )
        else:
            face["status"] = "ambiguous"
            face["diagnostics"].append(
                "Mixed regions, boundary-only evidence, or undefined primitive projections require an explicit choice."
            )
    intersections.sort(key=lambda edge: edge["key"])
    return {
        "intersections": intersections,
        "faces": faces,
        "diagnostics": diagnostics,
        "adjacencies": sorted(reviewed, key=lambda pair: pair["key"]),
        "policy": POLICY,
    }
