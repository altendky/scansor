"""Relationship signatures and exact input identities, independent of solving."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    "direction_alignment": {
        "label": "Aligned directions",
        "base_name": "Aligned directions",
        "summary": "Parallel unsigned directions; independent positions and offsets.",
        "operation": "direction_alignment",
        "sense": "unsigned",
        "joint_modes": ["direction"],
        "roles": {
            "reference": "relationship_direction_reference",
            "participants": "relationship_direction_targets",
        },
        "selection_pattern": "reference_targets",
        "reference_role": "reference",
        "expanded_min_count": 2,
        "expanded_max_count": 33,
        "role_labels": {
            "reference": "Same direction as (optional)",
            "participants": "Apply to",
        },
        "node_fields": {
            "reference": {
                "name": "reference",
                "encoding": "reference",
                "multiple": False,
                "optional": True,
            },
            "participants": {
                "name": "directions",
                "encoding": "reference",
                "multiple": True,
            },
        },
        "node_values": {"sense": "unsigned"},
    },
    "coincident_planes": {
        "label": "Coincident planes",
        "base_name": "Coincident planes",
        "summary": "Same plane: normal and offset.",
        "operation": "plane_relationship",
        "joint_modes": ["direction"],
        "relation": "coincident",
        "roles": {"participants": "relationship_plane"},
        "selection_pattern": "peers",
        "role_labels": {"participants": "Participants"},
        "node_fields": {
            "participants": {
                "name": "surfaces",
                "encoding": "reference",
                "multiple": True,
            }
        },
        "node_values": {"relation": "coincident"},
    },
    "parallel_planes": {
        "label": "Parallel planes",
        "base_name": "Parallel planes",
        "summary": "Same normal; independent offsets.",
        "operation": "plane_relationship",
        "joint_modes": ["direction"],
        "relation": "parallel",
        "roles": {"participants": "relationship_plane_orientation"},
        "selection_pattern": "peers",
        "role_labels": {"participants": "Participants"},
        "node_fields": {
            "participants": {
                "name": "surfaces",
                "encoding": "reference",
                "multiple": True,
            }
        },
        "node_values": {"relation": "parallel"},
    },
    "equal_radii": {
        "label": "Equal radii",
        "base_name": "Equal radii",
        "summary": "Same radius; independent positions.",
        "operation": "equal_radii",
        "joint_modes": ["direction"],
        "roles": {"participants": "relationship_radius"},
        "selection_pattern": "peers",
        "role_labels": {"participants": "Participants"},
        "node_fields": {
            "participants": {
                "name": "surfaces",
                "encoding": "feature",
                "multiple": True,
            }
        },
        "node_values": {},
    },
    "mirror": {
        "label": "Mirror symmetry",
        "base_name": "Mirrored pair",
        "summary": "Exactly two corresponding fits reflected across a reference plane.",
        "operation": "mirror_symmetry",
        "roles": {"members": "mirror_members", "datum": "mirror_plane"},
        "selection_pattern": "roles",
        "role_labels": {"members": "Mirrored fits", "datum": "Reference plane"},
        "reference_role": "datum",
        "node_fields": {
            "members": {"name": "surfaces", "encoding": "feature", "multiple": True},
            "datum": {"name": "plane", "encoding": "feature", "multiple": False},
        },
        "node_values": {"symmetric_extents": True},
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
        "selection_pattern": "roles",
        "role_labels": {
            "radius": "Radius",
            "plane": "Measured plane",
            "datum": "Reference plane",
        },
        "reference_role": "datum",
        # The nested measurement equation retains its specialized serializer.
        "node_fields": {},
        "node_values": {},
        "semantic_check": "same_axis",
    },
    "coaxial": {
        "label": "Coaxial",
        "base_name": "Coaxial",
        "summary": "Same axis line; independent radii and taper.",
        "operation": "coaxial_group",
        "roles": {"axis": "coaxial_axis", "members": "coaxial_members"},
        "selection_pattern": "reference_targets",
        "role_labels": {"axis": "Reference axis", "members": "Target fits"},
        "reference_role": "axis",
        "node_fields": {
            "axis": {"name": "axis", "encoding": "feature", "multiple": False},
            "members": {"name": "surfaces", "encoding": "feature", "multiple": True},
        },
        "node_values": {},
        "semantic_check": "compatible_axis",
    },
    "shared_center": {
        "label": "Shared center",
        "base_name": "Shared center",
        "summary": "Same point; independent sphere radii.",
        "operation": "shared_center_group",
        "roles": {"point": "shared_center_point", "members": "shared_center_members"},
        "selection_pattern": "reference_targets",
        "role_labels": {"point": "Reference point", "members": "Target fits"},
        "reference_role": "point",
        "node_fields": {
            "point": {"name": "point", "encoding": "feature", "multiple": False},
            "members": {"name": "surfaces", "encoding": "feature", "multiple": True},
        },
        "node_values": {},
        "semantic_check": "compatible_point",
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
            and (
                contract.get("property") is None
                or contract["property"] in spec["properties"]
            )
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
    owner = (
        spec["parameter_owner"]
        if ref.context == ref.feature
        and "binding" in contract.get("uses", ["geometry"])
        else None
    )
    if contract.get("property") == "direction" and not spec["properties"].get(
        "direction", {}
    ).get("binding_eligible", False):
        owner = None
    descriptor = {
        **spec,
        "reference": ref.model_dump(),
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


def expand_relationship_references(
    references: Sequence[FeatureInput | dict[str, Any]],
    nodes: dict[str, Feature],
    requirement: str,
) -> list[FeatureInput | dict[str, Any]]:
    from experiments.relationship_collections import collection_members

    expanded: list[FeatureInput | dict[str, Any]] = []
    for value in references:
        ref = (
            value
            if isinstance(value, (str, OutputReference))
            else OutputReference.model_validate(value)
        )
        members = None if isinstance(ref, str) else collection_members(ref, nodes)
        if members is not None:
            if not REQUIREMENTS[requirement].get("allow_collections"):
                raise ValueError(
                    "This relationship role does not accept pattern collections"
                )
            expanded.extend(members)
        else:
            expanded.append(value)
    return expanded


def validate_relationship_inputs(
    kind: str,
    references: Sequence[FeatureInput | dict[str, Any]]
    | Mapping[str, Sequence[FeatureInput | dict[str, Any]]],
    recipe: Recipe,
) -> dict[str, list[RelationshipEndpoint]]:
    """Validate explicit roles, preserving legacy unambiguous flat selections."""
    if kind not in RELATIONSHIP_SIGNATURES:
        raise ValueError("unknown relationship signature")
    signature = RELATIONSHIP_SIGNATURES[kind]
    if kind == "direction_alignment" and not isinstance(references, Mapping):
        references = {"participants": references}
    explicit_roles = isinstance(references, Mapping)
    if explicit_roles and set(references) - set(signature["roles"]):
        raise ValueError("relationship has an unknown input role")
    roles: dict[str, list[RelationshipEndpoint]] = {}
    assigned: set[int] = set()
    seen: set[str] = set()
    nodes = {node.id: node for node in recipe.nodes}
    source_keys: set[str] = set()
    if kind == "direction_alignment" and isinstance(references, Mapping):
        from experiments.relationship_collections import collection_members

        for value in references.get("participants", ()):
            ref = (
                value
                if isinstance(value, (str, OutputReference))
                else OutputReference.model_validate(value)
            )
            members = None if isinstance(ref, str) else collection_members(ref, nodes)
            if members is not None:
                source_keys.add(reference_key(members[0]))
    for role, requirement in signature["roles"].items():
        endpoints: list[RelationshipEndpoint] = []
        candidates = (
            references.get(role, ()) if isinstance(references, Mapping) else references
        )
        if isinstance(candidates, (str, OutputReference, Mapping)):
            raise ValueError("relationship role inputs must be a sequence")
        candidates = expand_relationship_references(candidates, nodes, requirement)
        for index, reference in enumerate(candidates):
            try:
                endpoint = relationship_reference(reference, recipe, requirement)
            except ValueError as error:
                if kind == "direction_alignment":
                    raise ValueError(
                        f"Direction input is unavailable: {error}"
                    ) from error
                if explicit_roles:
                    raise ValueError(
                        f"relationship {role} input is unavailable: {error}"
                    ) from error
                continue
            if not explicit_roles and index in assigned:
                raise ValueError(
                    "relationship input matches multiple roles; assign its role explicitly"
                )
            endpoints.append(endpoint)
            assigned.add(index)
        contract = REQUIREMENTS[requirement]
        if (
            len(endpoints) < contract["min_count"]
            or len(endpoints) > contract["max_count"]
        ):
            raise ValueError(
                f"relationship {signature['role_labels'][role]} cardinality is {len(endpoints)}; choose {contract['min_count']} to {contract['max_count']} compatible outputs"
            )
        if len({endpoint.key for endpoint in endpoints}) != len(endpoints):
            raise ValueError("relationship output references must be distinct")
        overlap = seen.intersection(endpoint.key for endpoint in endpoints)
        if overlap and kind == "direction_alignment" and role == "participants":
            allowed = overlap & source_keys
            endpoints = [
                endpoint for endpoint in endpoints if endpoint.key not in allowed
            ]
            overlap -= allowed
        if overlap:
            raise ValueError(
                "relationship output references must be distinct across roles"
            )
        seen.update(endpoint.key for endpoint in endpoints)
        roles[role] = endpoints
    if not explicit_roles and len(assigned) != len(references):
        raise ValueError("relationship has an unavailable or incompatible input")
    if (
        kind == "direction_alignment"
        and not signature["expanded_min_count"]
        <= len(seen)
        <= signature["expanded_max_count"]
    ):
        raise ValueError(
            "Aligned directions requires 2 to 33 distinct expanded outputs"
        )
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
    if signature.get("semantic_check") == "compatible_axis":
        axis = roles["axis"][0].feature
        if any(
            getattr(nodes[endpoint.feature], "axis", None) not in (None, axis)
            for endpoint in roles["members"]
        ):
            raise ValueError(
                "coaxial members already bound to another axis must be unbound or use the selected axis"
            )
        members = {endpoint.feature for endpoint in roles["members"]}
        if any(
            candidate.operation == "coaxial_group"
            and getattr(candidate, "axis", None) != axis
            and members.intersection(getattr(candidate, "surfaces", []))
            for candidate in recipe.nodes
        ):
            raise ValueError(
                "coaxial members already belong to a relationship using another axis"
            )
    if signature.get("semantic_check") == "compatible_point":
        point = roles["point"][0].feature
        if any(
            getattr(nodes[endpoint.feature], "point", None) not in (None, point)
            for endpoint in roles["members"]
        ):
            raise ValueError(
                "shared-center members already bound to another point must be unbound or use the selected point"
            )
        members = {endpoint.feature for endpoint in roles["members"]}
        if any(
            candidate.operation == "shared_center_group"
            and getattr(candidate, "point", None) != point
            and members.intersection(getattr(candidate, "surfaces", []))
            for candidate in recipe.nodes
        ):
            raise ValueError(
                "shared-center members already belong to a relationship using another point"
            )
    return roles
