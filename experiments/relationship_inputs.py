"""Relationship signatures and exact input identities, independent of solving."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from experiments.feature_inputs import (
    REQUIREMENTS,
    FeatureInput,
    OutputReference,
    compatibility_reason,
    declared_output_specs,
    reference_key,
    validate_output_reference,
)

if TYPE_CHECKING:
    from experiments.feature_graph import Feature, Recipe


RELATIONSHIP_SIGNATURES: dict[str, dict[str, Any]] = {
    "coincident_planes": {
        "label": "Coincident planes",
        "base_name": "Coincident planes",
        "summary": "Same plane: normal and offset.",
        "operation": "plane_relationship",
        "relation": "coincident",
        "roles": {"participants": "relationship_plane"},
    },
    "parallel_planes": {
        "label": "Parallel planes",
        "base_name": "Parallel planes",
        "summary": "Same normal; independent offsets.",
        "operation": "plane_relationship",
        "relation": "parallel",
        "roles": {"participants": "relationship_plane_orientation"},
    },
    "equal_radii": {
        "label": "Equal radii",
        "base_name": "Equal radii",
        "summary": "Same radius; independent positions.",
        "operation": "equal_radii",
        "roles": {"participants": "relationship_radius"},
    },
    "mirror": {
        "label": "Mirror symmetry",
        "base_name": "Mirrored pair",
        "summary": "Exact mirrored pair.",
        "operation": "mirror_symmetry",
        "roles": {"members": "mirror_members", "datum": "mirror_plane"},
        "semantic_check": "same_geometry_kind",
    },
    "radius_plane_distance": {
        "label": "Radius equals plane distance",
        "base_name": "Radius equals plane distance",
        "summary": "Radius equals plane separation.",
        "operation": "equal",
        "roles": {
            "radius": "distance_radius",
            "plane": "distance_plane",
            "datum": "distance_datum",
        },
        "semantic_check": "same_axis",
    },
}


@dataclass(frozen=True)
class RelationshipEndpoint:
    reference: OutputReference
    owner: str | None

    @property
    def feature(self) -> str:
        return self.reference.feature

    @property
    def output(self) -> str:
        return self.reference.output

    @property
    def context(self) -> str:
        return self.reference.context or self.feature

    @property
    def key(self) -> str:
        return reference_key(self.reference)

    @property
    def is_binding(self) -> bool:
        return self.owner is not None


def normalize_endpoint(
    reference: FeatureInput | dict[str, Any],
    nodes: dict[str, Feature],
    requirement: str = "relationship_plane",
) -> RelationshipEndpoint:
    """Normalize an owner binding without recursing into graph discovery.

    A full reference always retains its publication context. Only a direct
    declared owner is a parameter binding; every other context is a fixed read.
    """
    contract = REQUIREMENTS[requirement]
    if isinstance(reference, str):
        if reference not in nodes:
            raise ValueError("relationship input refers to a removed feature")
        matches = [
            spec
            for spec in declared_output_specs(nodes[reference])
            if set(spec["capabilities"]).intersection(contract["capabilities"])
        ]
        if len(matches) != 1:
            raise ValueError(
                "relationship input requires an explicit compatible output"
            )
        ref = OutputReference(feature=reference, output=matches[0]["output"])
    else:
        ref = (
            reference
            if isinstance(reference, OutputReference)
            else OutputReference.model_validate(reference)
        )
    node = nodes.get(ref.feature)
    if node is None:
        raise ValueError("relationship input refers to a removed feature")
    spec = next(
        (spec for spec in declared_output_specs(node) if spec["output"] == ref.output),
        None,
    )
    if spec is None:
        raise ValueError("relationship input refers to an unavailable output")
    owner = spec["parameter_owner"] if ref.context == ref.feature else None
    descriptor = {
        **spec,
        "capability": spec["capabilities"][0],
        "uses": ["binding"] if owner else ["geometry"],
        "constrainable": owner is not None,
        "source": None,
        "frame": "workspace",
    }
    reason = compatibility_reason(descriptor, requirement)
    if reason:
        raise ValueError(reason)
    return RelationshipEndpoint(ref, ref.feature if owner else None)


def relationship_reference(
    reference: FeatureInput | dict[str, Any],
    recipe: Recipe,
    requirement: str = "relationship_plane",
) -> RelationshipEndpoint:
    endpoint = normalize_endpoint(
        reference, {node.id: node for node in recipe.nodes}, requirement
    )
    # The pure path establishes identity; this path also checks that the exact
    # non-owner publication is declared rather than guessing a ready overlay.
    if not endpoint.is_binding:
        _ = validate_output_reference(endpoint.reference, recipe, requirement)
    return endpoint


def validate_relationship_inputs(
    kind: str,
    references: Sequence[FeatureInput | dict[str, Any]],
    recipe: Recipe,
) -> dict[str, list[RelationshipEndpoint]]:
    """Assign declared roles using the same capability contracts as the UI."""
    if kind not in RELATIONSHIP_SIGNATURES:
        raise ValueError("unknown relationship signature")
    signature = RELATIONSHIP_SIGNATURES[kind]
    roles: dict[str, list[RelationshipEndpoint]] = {}
    assigned: set[int] = set()
    for role, requirement in signature["roles"].items():
        endpoints: list[RelationshipEndpoint] = []
        for index, reference in enumerate(references):
            try:
                endpoint = relationship_reference(reference, recipe, requirement)
            except ValueError:
                continue
            endpoints.append(endpoint)
            assigned.add(index)
        contract = REQUIREMENTS[requirement]
        if (
            len(endpoints) < contract["min_count"]
            or len(endpoints) > contract["max_count"]
        ):
            raise ValueError(
                f"relationship {role} cardinality does not meet its requirement"
            )
        if len({endpoint.key for endpoint in endpoints}) != len(endpoints):
            raise ValueError("relationship output references must be distinct")
        roles[role] = endpoints
    if len(assigned) != len(references):
        raise ValueError("relationship has an unavailable or incompatible input")
    nodes = {node.id: node for node in recipe.nodes}
    if (
        signature.get("semantic_check") == "same_geometry_kind"
        and len(
            {
                getattr(nodes[endpoint.feature], "kind", None)
                for endpoint in roles["members"]
            }
        )
        != 1
    ):
        raise ValueError("mirror members must have the same geometry kind")
    if signature.get("semantic_check") == "same_axis":
        radius, datum = roles["radius"][0], roles["datum"][0]
        if getattr(nodes[radius.feature], "axis", None) != getattr(
            nodes[datum.feature], "axis", None
        ):
            raise ValueError("radius and reference plane must use the same axis")
    return roles
