"""Repair display-name collisions at the saved-recipe import boundary only."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from experiments.feature_graph import Recipe


class ImportWarning(TypedDict):
    id: str
    old_name: str
    new_name: str
    kind: Literal["feature", "group"]


OPERATION_SUFFIXES = {
    "source": "source",
    "selection": "selection",
    "fit": "fit",
    "surface": "fit",
    "seed_fit": "fit",
    "point": "point",
    "axis": "axis",
    "reference_plane": "plane",
    "frame": "frame",
    "scale": "scale",
    "transform": "transform",
    "feature_reuse": "reuse",
    "reuse_selection": "selection",
    "body": "body",
    "growth": "growth",
    "selection_region": "region",
    "region_selection": "selection",
    "perpendicular": "relationship",
    "coaxial": "relationship",
    "rotational_symmetry": "symmetry",
    "joint_fit": "solve",
    "mirror_symmetry": "symmetry",
    "parallel": "relationship",
    "equal": "relationship",
    "equal_radii": "relationship",
    "plane_relationship": "relationship",
    "axis_solve": "solve",
    "surface_intersection": "intersection",
    "trimmed_face": "face",
    "arranged_face": "face",
    "build_faces": "faces",
}


def _rename_collisions(
    records: object, kind: Literal["feature", "group"]
) -> list[ImportWarning]:
    if not isinstance(records, list):
        return []  # The ordinary schema validation reports malformed records.
    objects = [cast(dict[str, Any], item) for item in records if isinstance(item, dict)]
    identities = [item["id"] for item in objects if isinstance(item.get("id"), str)]
    if len(identities) != len(set(identities)):
        raise ValueError(
            "duplicate feature ID"
            if kind == "feature"
            else "duplicate feature group ID"
        )
    reserved = {
        item["label"].strip().casefold()
        for item in objects
        if isinstance(item.get("label"), str)
    }
    seen: set[str] = set()
    next_numbers: dict[tuple[str, str], int] = {}
    warnings: list[ImportWarning] = []
    for item in objects:
        label, identifier = item.get("label"), item.get("id")
        if not isinstance(label, str) or not isinstance(identifier, str):
            continue
        key = label.strip().casefold()
        if key not in seen:
            seen.add(key)
            continue
        root = label.strip() or "Feature"
        operation = item.get("operation")
        if kind == "group":
            suffix = "group"
        else:
            suffix = (
                OPERATION_SUFFIXES.get(operation, "feature")
                if isinstance(operation, str)
                else "feature"
            )
        count_key = (root.casefold(), suffix)
        number = next_numbers.get(count_key, 1)
        while True:
            ending = f" {suffix}" + (f" {number}" if number > 1 else "")
            candidate = root[: 120 - len(ending)].rstrip() + ending
            if candidate.casefold() not in reserved:
                break
            number += 1
        next_numbers[count_key] = number + 1
        reserved.add(candidate.casefold())
        item["label"] = candidate
        warnings.append(
            {"id": identifier, "old_name": label, "new_name": candidate, "kind": kind}
        )
    return warnings


def import_recipe(value: object) -> tuple[Recipe, list[ImportWarning]]:
    """Copy imported JSON, rename display collisions, then apply the normal schema."""
    copied = deepcopy(value)
    warnings: list[ImportWarning] = []
    if isinstance(copied, dict):
        payload = cast(dict[str, Any], copied)
        warnings.extend(_rename_collisions(payload.get("nodes"), "feature"))
        warnings.extend(_rename_collisions(payload.get("groups"), "group"))
    return Recipe.model_validate(copied), warnings


def load_recipe(path: Path) -> Recipe:
    """CLI/default imports share collision handling and print visible warnings once."""
    recipe, warnings = import_recipe(json.loads(path.read_bytes()))
    for warning in warnings:
        print(
            f"Imported {path.name}: renamed duplicate {warning['kind']} name "
            + f"{warning['old_name']!r} to {warning['new_name']!r} (ID {warning['id']}).",
            file=sys.stderr,
        )
    return recipe
