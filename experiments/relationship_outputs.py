"""Common relationship properties alias existing owners, never new variables."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import numpy as np
from numpy.typing import NDArray

from experiments.feature_inputs import FeatureInput, OutputReference, reference_key

if TYPE_CHECKING:
    from experiments.feature_graph import Feature
    from experiments.relationship_inputs import RelationshipEndpoint


def common_output_specs(node: Feature) -> list[dict[str, Any]]:
    from experiments.relationship_inputs import RELATIONSHIP_SIGNATURES

    declaration = next(
        (
            signature.get("common_outputs")
            for signature in RELATIONSHIP_SIGNATURES.values()
            if signature["operation"] == node.operation
            and signature.get("common_outputs")
        ),
        None,
    )
    if declaration is None:
        return []
    result: list[dict[str, Any]] = []
    for name, rule in declaration.items():
        direction = {
            "property": "direction",
            "projection": "axis_direction",
            "frame": "workspace",
            "sense": "unsigned",
            "orientation_authority": "relationship_common",
            "parameter_owner": None,
            "binding_aliases": [],
            "binding_eligible": False,
            "binding_reason": "This relationship has no adjustable direction owners",
            "uses": ["geometry"],
        }
        properties = {"direction": direction}
        if "axis_line" in rule["capabilities"]:
            properties["axis_line"] = {
                **direction,
                "property": "axis_line",
                "projection": "axis_line",
                "binding_reason": "This common axis is published geometry; datum-only inputs require an explicit axis datum",
            }
        result.append(
            {
                "output": name,
                "capabilities": rule["capabilities"],
                "properties": properties,
                "parameter_owner": None,
                "ownership": "relationship",
                "geometry_kind": rule["geometry_kind"],
                "axis": None,
                "construction": None,
                "standalone": False,
                "common_output": rule["binding_rule"],
            }
        )
    return result


def enrich_aliases(
    spec: dict[str, Any],
    node: Feature,
    nodes: dict[str, Feature],
    path: frozenset[str] | None = None,
    cache: dict[str, tuple[OutputReference, ...]] | None = None,
) -> dict[str, Any]:
    if "common_output" not in spec:
        return spec

    key = reference_key(OutputReference(feature=node.id, output=spec["output"]))
    path = path or frozenset()
    if key in path:
        raise ValueError("relationship binding aliases contain a cycle")
    path = path | {key}
    cache = {} if cache is None else cache
    if key in cache:
        aliases = cache[key]
    else:
        aliases = _uncached_aliases(node, nodes, path, cache)
        cache[key] = aliases
    result = deepcopy(spec)
    direction = result["properties"]["direction"]
    direction.update(
        binding_aliases=[alias.model_dump() for alias in aliases],
        binding_eligible=bool(aliases),
        binding_reason=None if aliases else direction["binding_reason"],
        uses=["geometry", "binding"] if aliases else ["geometry"],
    )
    return result


def _uncached_aliases(
    node: Feature,
    nodes: dict[str, Feature],
    path: frozenset[str],
    cache: dict[str, tuple[OutputReference, ...]],
) -> tuple[OutputReference, ...]:
    from experiments.feature_graph import CoaxialGroup, DirectionAlignment
    from experiments.relationship_inputs import (
        expand_relationship_references,
        normalize_endpoint,
    )

    candidates: list[FeatureInput | dict[str, Any]]
    if isinstance(node, CoaxialGroup):
        candidates = [OutputReference(feature=node.axis, output="axis")]
    else:
        assert isinstance(node, DirectionAlignment)
        candidates = expand_relationship_references(
            node.directions, nodes, "relationship_direction_targets"
        )
        if node.reference is not None and node.reference_use == "binding":
            candidates = [node.reference, *candidates]
    aliases: dict[str, OutputReference] = {}
    for reference in candidates:
        endpoint = normalize_endpoint(
            reference,
            nodes,
            "relationship_direction_targets",
            _alias_path=path,
            _alias_cache=cache,
        )
        for alias in endpoint.binding_aliases or (
            (endpoint.reference,) if endpoint.owner else ()
        ):
            _ = aliases.setdefault(reference_key(alias), alias)
    return tuple(aliases.values())


def physical_endpoints(
    endpoint: RelationshipEndpoint, nodes: dict[str, Feature]
) -> list[RelationshipEndpoint]:
    from experiments.relationship_inputs import normalize_endpoint

    if not endpoint.binding_aliases:
        return [endpoint]
    return [
        normalize_endpoint(
            reference, nodes, "relationship_direction_targets", use="binding"
        )
        for reference in endpoint.binding_aliases
    ]


def binding_relationship_dependencies(
    node: Feature, nodes: dict[str, Feature]
) -> set[str]:
    """Common bindings retain their declared source equations in named solves."""
    from experiments.relationship_inputs import (
        expand_relationship_references,
        normalize_endpoint,
    )

    if node.operation != "direction_alignment":
        return set()
    references: list[tuple[FeatureInput | dict[str, Any], str | None]] = [
        (ref, None)
        for ref in expand_relationship_references(
            node.directions, nodes, "relationship_direction_targets"
        )
    ]
    if node.reference is not None:
        references.append((node.reference, node.reference_use))
    return {
        endpoint.feature
        for ref, use in references
        if (
            endpoint := normalize_endpoint(
                ref, nodes, "relationship_direction_targets", use=use
            )
        ).binding_aliases
    }


def common_geometry(
    node: Feature,
    raw: dict[str, Any],
    nodes: dict[str, Feature],
    values: dict[str, dict[str, Any]],
    path: frozenset[str] | None = None,
    cache: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project only the named publisher's geometry; never a current overlay."""

    path = path or frozenset()
    if node.id in path:
        raise ValueError("relationship common output references contain a cycle")
    path = path | {node.id}
    cache = {} if cache is None else cache
    key = (node.id, id(raw))
    if key not in cache:
        cache[key] = _uncached_common_geometry(node, raw, nodes, values, path, cache)
    return deepcopy(cache[key])


def _uncached_common_geometry(
    node: Feature,
    raw: dict[str, Any],
    nodes: dict[str, Feature],
    values: dict[str, dict[str, Any]],
    path: frozenset[str],
    cache: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    from experiments.feature_inputs import resolve_output
    from experiments.relationship_inputs import (
        expand_relationship_references,
        normalize_endpoint,
    )

    if node.operation == "coaxial_group":
        record = raw.get("resolved", {}).get(
            node.axis, raw.get("axes", {}).get(node.axis)
        )
        if record is None:
            record = (
                raw.get("resolved", {})
                .get(node.id, {})
                .get("resolved", {})
                .get(node.axis)
            )
        if record is None:
            raise ValueError("the exact Coaxial publisher omitted its declared axis")
        return {
            "axis_display": list(record["axis_display"]),
            "point_display": list(record["point_display"]),
        }
    if node.operation != "direction_alignment":
        raise ValueError("this action does not publish a common direction")
    references: list[tuple[FeatureInput | dict[str, Any], str | None]] = [
        (reference, None)
        for reference in expand_relationship_references(
            node.directions, nodes, "relationship_direction_targets"
        )
    ]
    if node.reference is not None:
        references.insert(0, (node.reference, node.reference_use))
    directions: list[NDArray[np.float64]] = []
    for reference, use in references:
        endpoint = normalize_endpoint(
            reference, nodes, "relationship_direction_targets", use=use
        )
        target = nodes[endpoint.feature]
        if common_output_specs(target):
            source = raw if endpoint.is_binding else values[endpoint.context]
            value = common_geometry(target, source, nodes, values, path, cache)
        elif endpoint.is_binding:
            value = resolve_output(
                OutputReference(
                    feature=endpoint.feature, output=endpoint.output, context=node.id
                ),
                nodes,
                {node.id: raw},
            )
        else:
            value = resolve_output(endpoint.reference, nodes, values)
        vector = np.asarray(
            value.get("axis_display", value.get("normal_display")), dtype=float
        )
        if (
            vector.shape != (3,)
            or not np.isfinite(vector).all()
            or np.linalg.norm(vector) == 0
        ):
            raise ValueError(
                "the common direction requires finite nonzero published directions"
            )
        directions.append(vector / np.linalg.norm(vector))
    if not directions:
        raise ValueError("the relationship omitted its common direction")
    _, vectors = np.linalg.eigh(sum(np.outer(vector, vector) for vector in directions))
    direction = vectors[:, -1]
    if any(np.linalg.norm(np.cross(direction, vector)) > 1e-6 for vector in directions):
        raise ValueError("the exact relationship outputs do not share one direction")
    if direction[int(np.argmax(abs(direction)))] < 0:
        direction = -direction
    return {"axis_display": direction.tolist()}


def common_publication_values(
    nodes: dict[str, Feature], values: dict[str, dict[str, Any]], states: dict[str, str]
) -> dict[str, dict[str, Any]]:
    result = values.copy()
    cache: dict[tuple[str, int], dict[str, Any]] = {}
    for node in nodes.values():
        specs = common_output_specs(node)
        if not specs or states.get(node.id) != "ready" or node.id not in values:
            continue
        try:
            geometry = common_geometry(
                node, values[node.id], nodes, result, cache=cache
            )
        except (KeyError, ValueError, IndexError):
            # A missing optional output must not break unrelated project reads;
            # catalogue resolution reports this exact port as unavailable.
            continue
        result[node.id] = {
            **values[node.id],
            "common_outputs": {specs[0]["output"]: geometry},
        }
    return result
