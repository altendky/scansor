"""Recipe-only live collections; member identity never depends on evaluated values."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from experiments.feature_inputs import OutputReference, declared_output_specs

if TYPE_CHECKING:
    from experiments.feature_graph import Feature, FeatureReuse


def direction_output(node: Feature) -> str:
    matches = [
        spec["output"]
        for spec in declared_output_specs(node)
        if "direction" in spec["properties"]
    ]
    if len(matches) != 1:
        raise ValueError("The pattern template has no unambiguous direction output")
    return matches[0]


def collection_members(
    reference: OutputReference, nodes: dict[str, Feature]
) -> list[OutputReference] | None:
    from experiments.feature_graph import FeatureReuse, SurfaceFit

    owner = nodes.get(reference.feature)
    if not isinstance(owner, FeatureReuse):
        return None
    if reference.context != owner.id:
        raise ValueError(
            "Pattern collections require their exact feature-reuse context"
        )
    if not reference.output.startswith("direction:"):
        raise ValueError("The pattern does not declare this relationship collection")
    template = reference.output.removeprefix("direction:")
    if template not in owner.fits or not isinstance(
        source := nodes.get(template), SurfaceFit
    ):
        raise ValueError("The pattern collection template was removed or replaced")
    output = direction_output(source)
    members = [OutputReference(feature=template, output=output, context=owner.id)]
    for target in owner.target_selections:
        matches = [
            node
            for node in nodes.values()
            if node.managed_by == owner.id
            and node.managed_key == f"fit/{target}/{template}"
        ]
        if len(matches) != 1 or not isinstance(member := matches[0], SurfaceFit):
            raise ValueError(
                "The pattern collection has an incomplete or ambiguous generated inventory"
            )
        spec = next(
            spec
            for spec in declared_output_specs(member)
            if spec["output"] == direction_output(member)
        )
        if not spec["properties"]["direction"]["binding_eligible"]:
            raise ValueError(
                "A generated direction is supplied by another datum; this collection cannot constrain it independently"
            )
        members.append(OutputReference(feature=member.id, output=spec["output"]))
    return members


def collection_descriptors(
    owner: FeatureReuse, nodes: dict[str, Feature]
) -> list[dict[str, Any]]:
    descriptors: list[dict[str, Any]] = []
    for template in owner.fits:
        source = nodes.get(template)
        if source is None:
            continue
        try:
            output = direction_output(source)
            reference = OutputReference(
                feature=owner.id, output="direction:" + template
            )
            members = collection_members(reference, nodes)
            assert members is not None
        except ValueError:
            continue
        spec = next(
            spec for spec in declared_output_specs(source) if spec["output"] == output
        )
        properties = deepcopy(spec["properties"])
        properties = {"direction": properties["direction"]}
        properties["direction"].update(
            parameter_owner=None, binding_eligible=False, uses=["binding", "geometry"]
        )
        descriptors.append(
            {
                "reference": reference.model_dump(),
                "capability": "direction",
                "capabilities": ["direction"],
                "label": f"{owner.label} · {source.label} directions",
                "feature_label": f"{owner.label} · {source.label}",
                "producing_context": {
                    "id": owner.id,
                    "label": owner.label,
                    "kind": "collection",
                },
                "properties": properties,
                "parameter_owner": None,
                "constrainable": True,
                "ownership": "collection",
                "geometry_kind": "collection",
                "uses": ["binding", "geometry"],
                "axis": None,
                "construction": None,
                "standalone": False,
                "collection": {
                    "property": "direction",
                    "template": template,
                    "count": len(members),
                    "members": [member.model_dump() for member in members],
                    "source_count": 1,
                    "adjustable_count": len(members) - 1,
                },
            }
        )
    return descriptors
