"""Experimental selectable outputs, distinct from numerical parameter bindings."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, ClassVar, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from experiments.feature_graph import Feature, Recipe


class OutputReference(BaseModel):
    """A declared output in one exact publication context."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    feature: str
    output: str = Field(min_length=1, max_length=120)
    context: str | None = None

    @model_validator(mode="before")
    @classmethod
    def canonical_direct_context(cls, value: Any) -> Any:
        if isinstance(value, dict):
            payload = cast(dict[str, Any], value)
            if payload.get("context") in (None, ""):
                return {**payload, "context": payload.get("feature")}
        return value


FeatureInput = str | OutputReference


def reference_key(reference: FeatureInput) -> str:
    return reference if isinstance(reference, str) else reference.model_dump_json()


def reference_dependencies(reference: FeatureInput) -> list[str]:
    if isinstance(reference, str):
        return [reference]
    return list(
        dict.fromkeys(
            [reference.feature]
            + (
                [
                    reference.context.split("/", 1)[1]
                    if reference.context.startswith(("@axis/", "@point/", "@plane/"))
                    else reference.context
                ]
                if reference.context
                else []
            )
        )
    )


def reference_json(reference: FeatureInput) -> str | dict[str, Any]:
    return reference if isinstance(reference, str) else reference.model_dump()


REQUIREMENTS: dict[str, dict[str, Any]] = {
    "relationship_direction": {
        "capabilities": ["direction"],
        "property": "direction",
        "uses": ["binding", "geometry"],
        "min_count": 2,
        "max_count": 33,
    },
    "point": {"capabilities": ["point"], "min_count": 1, "max_count": 1},
    "plane": {"capabilities": ["plane"], "min_count": 1, "max_count": 1},
    "direction": {
        "capabilities": ["direction"],
        "min_count": 1,
        "max_count": 1,
    },
    "fit_axis_line": {
        "capabilities": ["axis_line"],
        "property": "axis_line",
        "uses": ["geometry", "binding"],
        "ownership": "datum",
        "geometry_kinds": ["axis"],
        "direct_context": True,
        "min_count": 1,
        "max_count": 1,
        "fit_reference": {
            "kinds": ["cylinder", "cone"],
            "field": "axis",
            "output": "axis",
            "label": "Shared axis line",
        },
    },
    "fit_axis_direction": {
        "capabilities": ["direction"],
        "property": "direction",
        "uses": ["geometry", "binding"],
        "ownership": "datum",
        "geometry_kinds": ["axis"],
        "direct_context": True,
        "min_count": 1,
        "max_count": 1,
        "fit_reference": {
            "kinds": ["plane"],
            "field": "axis",
            "output": "axis",
            "label": "Normal follows axis direction",
        },
    },
    "fit_plane_orientation": {
        "capabilities": ["plane_orientation"],
        "property": "plane_orientation",
        "uses": ["geometry", "binding"],
        "ownership": "datum",
        "geometry_kinds": ["plane"],
        "direct_context": True,
        "min_count": 1,
        "max_count": 1,
        "fit_reference": {
            "kinds": ["plane"],
            "field": "reference_plane",
            "output": "plane",
            "label": "Parallel plane orientation",
        },
    },
    "fit_point_center": {
        "capabilities": ["point"],
        "property": "point",
        "uses": ["geometry", "binding"],
        "ownership": "datum",
        "geometry_kinds": ["point"],
        "direct_context": True,
        "min_count": 1,
        "max_count": 1,
        "fit_reference": {
            "kinds": ["sphere"],
            "field": "point",
            "output": "point",
            "label": "Shared sphere center",
        },
    },
    "constrainable_plane": {
        "capabilities": ["plane"],
        "ownership": "fit",
        "min_count": 2,
    },
    "constrainable_point": {
        "capabilities": ["point"],
        "ownership": "datum",
        "min_count": 1,
        "max_count": 1,
    },
    "relationship_plane": {
        "capabilities": ["plane"],
        "property": "plane",
        "uses": ["binding", "geometry"],
        "min_count": 2,
        "max_count": 33,
    },
    "relationship_plane_orientation": {
        "capabilities": ["plane_orientation"],
        "property": "plane_orientation",
        "uses": ["binding", "geometry"],
        "min_count": 2,
        "max_count": 33,
    },
    "relationship_axis": {
        "capabilities": ["axis_line"],
        "property": "axis_line",
        "uses": ["binding", "geometry"],
        "min_count": 2,
        "max_count": 33,
    },
    "coaxial_axis": {
        "capabilities": ["axis_line"],
        "property": "axis_line",
        "uses": ["binding"],
        "ownership": "datum",
        "min_count": 1,
        "max_count": 1,
    },
    "coaxial_members": {
        "capabilities": ["axis_line"],
        "property": "axis_line",
        "uses": ["binding"],
        "ownership": "fit",
        "geometry_kinds": ["cone", "cylinder"],
        "min_count": 1,
        "max_count": 32,
    },
    "relationship_radius": {
        "capabilities": ["radius"],
        "property": "radius",
        "uses": ["binding"],
        "geometry_kinds": ["cylinder", "sphere"],
        "min_count": 2,
        "max_count": 33,
    },
    "mirror_members": {
        "capabilities": ["surface"],
        "uses": ["binding"],
        "geometry_kinds": ["plane", "cylinder", "cone"],
        "standalone": True,
        "min_count": 2,
        "max_count": 2,
    },
    "mirror_plane": {
        "capabilities": ["plane"],
        "property": "plane",
        "uses": ["binding"],
        "ownership": "datum",
        "constructions": ["contains_axis"],
        "min_count": 1,
        "max_count": 1,
    },
    "distance_radius": {
        "capabilities": ["radius"],
        "property": "radius",
        "uses": ["binding"],
        "geometry_kinds": ["cylinder"],
        "axis_bound": True,
        "min_count": 1,
        "max_count": 1,
    },
    "distance_plane": {
        "capabilities": ["plane"],
        "property": "plane",
        "uses": ["binding"],
        "ownership": "fit",
        "standalone": True,
        "min_count": 1,
        "max_count": 1,
    },
    "distance_datum": {
        "capabilities": ["plane"],
        "property": "plane",
        "uses": ["binding"],
        "ownership": "datum",
        "min_count": 1,
        "max_count": 1,
    },
}


def declared_output_specs(node: Feature) -> list[dict[str, Any]]:
    """One structural capability registry, independent of solve discovery."""
    operation = node.operation
    kind = getattr(node, "kind", None)
    owner = (
        "fit"
        if operation == "fit"
        else "datum"
        if operation in {"axis", "point", "reference_plane"}
        else None
    )
    metadata = {
        "ownership": owner,
        "geometry_kind": kind
        or {"axis": "axis", "point": "point", "reference_plane": "plane"}.get(
            operation
        ),
        "axis": getattr(node, "axis", None),
        "construction": getattr(node, "construction", None),
        "standalone": operation == "fit"
        and not any(
            getattr(node, field, None) for field in ("axis", "point", "reference_plane")
        ),
    }

    def output(
        name: str, capabilities: list[str], *, bind: bool = False
    ) -> dict[str, Any]:
        properties: dict[str, dict[str, Any]] = {}

        def property_spec(
            identity: str,
            projection: str,
            quantities: dict[str, int],
            *,
            sense: str | None = None,
            authority: str | None = None,
            fixed_by: str | None = None,
        ) -> None:
            adjustable = bind and bool(quantities) and fixed_by is None
            properties[identity] = {
                "property": identity,
                "projection": projection,
                "frame": "workspace",
                "sense": sense,
                "orientation_authority": authority,
                "parameter_owner": {
                    "feature": node.id,
                    "output": name,
                    "kind": owner,
                    "quantities": quantities.copy(),
                }
                if adjustable
                else None,
                "binding_eligible": adjustable,
                "binding_reason": None
                if adjustable
                else fixed_by or "This property is read-only geometry",
                # Eligibility describes the owner, never freedom in a particular solve.
                "uses": ["geometry", "binding"] if adjustable else ["geometry"],
            }

        if "point" in capabilities:
            fixed = (
                "This point is supplied by its upstream datum"
                if name == "center" and getattr(node, "point", None)
                else "This datum is initialized from an upstream fit"
                if getattr(node, "source_fit", None)
                else None
            )
            property_spec("point", "point", {"position": 3}, fixed_by=fixed)
        if "axis_line" in capabilities:
            fixed = (
                "This axis is supplied by its upstream axis datum"
                if operation == "fit" and getattr(node, "axis", None)
                else "This datum is initialized from upstream geometry"
                if getattr(node, "source_fit", None)
                or getattr(node, "source_points", None)
                else None
            )
            sense = "directed" if operation == "axis" else "unsigned"
            authority = (
                "explicit_reversal"
                if operation == "axis" and getattr(node, "direction_reversed", False)
                else "ordered_points"
                if getattr(node, "source_points", None)
                else "chart_representative"
                if operation == "axis" and not getattr(node, "source_fit", None)
                else "fit_representative"
            )
            property_spec(
                "direction",
                "axis_direction",
                {"orientation": 2},
                sense=sense,
                authority=authority,
                fixed_by=fixed,
            )
            property_spec(
                "axis_line",
                "axis_line",
                {"orientation": 2, "transverse_position": 2},
                sense=sense,
                authority=authority,
                fixed_by=fixed,
            )
        if "plane" in capabilities:
            construction = getattr(node, "construction", None)
            orientation = (
                {"orientation": 2}
                if construction == "independent"
                else {"clocking": 1}
                if operation == "reference_plane"
                and construction != "perpendicular_to_axis"
                else {}
                if operation == "reference_plane"
                or getattr(node, "axis", None)
                or getattr(node, "reference_plane", None)
                else {"orientation": 2}
            )
            fixed = (
                None if orientation else "This normal is supplied by its upstream datum"
            )
            for identity in ("direction", "plane_orientation"):
                property_spec(
                    identity,
                    "plane_normal",
                    orientation,
                    sense="signed_normal",
                    authority="chart_representative"
                    if construction == "independent"
                    else "construction"
                    if operation == "reference_plane"
                    else "fit_representative",
                    fixed_by=fixed,
                )
            quantities = {
                **orientation,
                **({"offset": 1} if construction != "contains_axis" else {}),
            }
            property_spec(
                "plane",
                "plane",
                quantities,
                sense="signed_normal",
                authority="chart_representative"
                if construction == "independent"
                else "construction"
                if operation == "reference_plane"
                else "fit_representative",
            )
        if "radius" in capabilities:
            property_spec("radius", "radius", {"radius": 1})
        return {
            **metadata,
            "output": name,
            "capabilities": capabilities,
            "parameter_owner": {"feature": node.id, "output": name, "kind": owner}
            if bind
            else None,
            "properties": properties,
        }

    if operation == "selection":
        return [
            output("single_node", ["point"]),
            output("observations", ["observations"]),
        ]
    if operation == "surface_selection":
        return [output("observations", ["observations"])]
    if operation == "point":
        return [output("point", ["point"], bind=True)]
    if operation == "axis":
        return [output("axis", ["direction", "axis_line"], bind=True)]
    if operation == "reference_plane":
        return [
            output(
                "plane",
                ["plane", "plane_orientation", "surface", "direction"],
                bind=True,
            )
        ]
    if operation != "fit":
        return []
    if kind == "sphere":
        return [
            output("center", ["point"], bind=True),
            output("surface", ["surface"], bind=True),
            output("radius", ["scalar", "radius"], bind=True),
            output("observations", ["observations"]),
        ]
    if kind == "plane":
        return [
            output(
                "plane",
                ["plane", "plane_orientation", "surface", "direction"],
                bind=True,
            ),
            output("observations", ["observations"]),
        ]
    if kind in {"cylinder", "cone"}:
        return [
            output("surface", ["surface"], bind=True),
            output("axis", ["axis_line", "direction"], bind=True),
            *(
                [output("radius", ["scalar", "radius"], bind=True)]
                if kind == "cylinder"
                else []
            ),
            output("observations", ["observations"]),
        ]
    return []


def _property_descriptors(
    spec: dict[str, Any], reference: OutputReference, frame: str
) -> dict[str, dict[str, Any]]:
    properties = deepcopy(spec["properties"])
    for descriptor in properties.values():
        descriptor.update(reference=reference.model_dump(), frame=frame)
        if reference.context != reference.feature:
            descriptor.update(
                parameter_owner=None,
                binding_eligible=False,
                binding_reason="A solved publication is read-only geometry",
                uses=["geometry"],
            )
    return properties


def output_catalogue(
    recipe: Recipe,
    states: dict[str, str] | None = None,
    values: dict[str, dict[str, Any]] | None = None,
    *,
    frame: str = "workspace",
    coordinates: Any = None,
) -> list[dict[str, Any]]:
    """Discover declared outputs without evaluating or inspecting native geometry."""
    from experiments.feature_graph import (
        AxisDefinition,
        AxisSolve,
        CoaxialGroup,
        JointFit,
        MirrorSymmetry,
        PlaneDefinition,
        PlaneRelationship,
        Selection,
        SurfaceFit,
        dependencies,
        describe_geometry_influence,
        joint_surfaces,
        named_direction_component,
        plane_relationship_endpoints,
    )

    nodes = {node.id: node for node in recipe.nodes}
    influence = describe_geometry_influence(nodes)
    states = states or {}
    values = values or {}
    contexts: dict[str, list[str]] = {}
    context_members: dict[str, set[str]] = {}
    producing_contexts: dict[str, dict[str, str]] = {}
    solve_descriptions = {
        "axis": ("Shared-axis solve", "axis_solve"),
        "point": ("Shared-point solve", "point_solve"),
        "planes": ("Shared-plane solve", "plane_solve"),
    }
    for provider in influence.providers.values():
        for publisher, aliases in provider.publications.items():
            context_members[publisher] = set(provider.prerequisites)
            if publisher in nodes:
                producing_contexts[publisher] = {
                    "id": publisher,
                    "label": nodes[publisher].label,
                    "kind": "solve",
                }
            elif provider.solve is not None:
                family, anchor = provider.solve
                description, kind = solve_descriptions[family]
                producing_contexts[publisher] = {
                    "id": publisher,
                    "label": f"{description} · {nodes[anchor].label}",
                    "kind": kind,
                }
            for alias in aliases:
                contexts.setdefault(alias, []).append(publisher)
    explicit_axes = {
        node.axis for node in nodes.values() if isinstance(node, AxisSolve)
    }
    for node in nodes.values():
        if isinstance(node, JointFit):
            sides, planes = joint_surfaces(node, nodes)
            members = [member.id for member in [*sides, *planes]]
            direction_component = named_direction_component(node, nodes)
            if direction_component is not None:
                members.extend(
                    key
                    for key in direction_component[2]
                    if declared_output_specs(nodes[key])
                )
        elif isinstance(node, AxisSolve):
            members = [
                ref for ref in node.factors if isinstance(nodes[ref], SurfaceFit)
            ]
            members += [
                ref
                for factor in node.factors
                if isinstance(nodes[factor], (MirrorSymmetry, CoaxialGroup))
                for ref in cast(MirrorSymmetry | CoaxialGroup, nodes[factor]).surfaces
            ]
            members += [
                endpoint.feature
                for factor in node.factors
                if isinstance(nodes[factor], PlaneRelationship)
                for endpoint in plane_relationship_endpoints(
                    cast(PlaneRelationship, nodes[factor]), nodes
                )
                if endpoint.is_binding
                and isinstance(nodes[endpoint.feature], SurfaceFit)
            ]
        else:
            continue
        context_members[node.id] = {node.id}
        producing_contexts[node.id] = {
            "id": node.id,
            "label": node.label,
            "kind": "solve",
        }
        for member in members:
            contexts.setdefault(member, []).append(node.id)

    def provenance(key: str, visited: frozenset[str] = frozenset()) -> str | None:  # pyright: ignore[reportCallInDefaultInitializer]
        if key in visited:
            return None
        node = nodes[key]
        if isinstance(node, Selection):
            return node.source
        sources = {
            source
            for dep in dependencies(node)
            if (source := provenance(dep, visited | {key})) is not None
        }
        return next(iter(sources)) if len(sources) == 1 else None

    def closure(keys: set[str]) -> list[str]:
        pending = list(keys)
        seen: set[str] = set()
        while pending:
            key = pending.pop()
            if key in seen or key not in nodes:
                continue
            seen.add(key)
            pending.extend(dependencies(nodes[key]))
        return [node.id for node in recipe.nodes if node.id in seen]

    descriptors: list[dict[str, Any]] = []
    for node in recipe.nodes:
        specs = declared_output_specs(node)
        if not specs:
            continue
        primary = specs[0]
        output, capability, ownership = (
            primary["output"],
            primary["capabilities"][0],
            primary["ownership"],
        )
        offered = list(dict.fromkeys(contexts.get(node.id, [])))
        # Explicit axis solves have no declared datum-member output yet.
        unsupported = (
            isinstance(node, (AxisDefinition, PlaneDefinition))
            and (node.id if isinstance(node, AxisDefinition) else node.axis)
            in explicit_axes
        )
        offered = [
            ctx
            for ctx in offered
            if not ctx.startswith("@axis/") or ctx[6:] not in explicit_axes
        ]
        if not offered:
            offered = [node.id]
        geometry_contexts = set(offered)
        if primary["parameter_owner"]:
            offered = list(dict.fromkeys([node.id, *offered]))
        for context in offered:
            reference = OutputReference(feature=node.id, output=output, context=context)
            prerequisites = context_members.get(context, {node.id}) | {node.id}
            availability = states.get(context, states.get(node.id, "unevaluated"))
            if context.startswith("@"):
                participant_states = [
                    states.get(key, "unevaluated") for key in prerequisites
                ]
                availability = next(
                    (
                        state
                        for state in (
                            "failed",
                            "blocked",
                            "needs_input",
                            "running",
                            "stale",
                            "unevaluated",
                        )
                        if state in participant_states
                    ),
                    "ready",
                )
            reason: str | None = None
            publisher = nodes.get(context)
            if isinstance(publisher, CoaxialGroup) and publisher.axis in explicit_axes:
                availability, reason = (
                    "ambiguous",
                    "An explicit joint owns this axis; choose its member output instead of this Coaxial context",
                )
            elif unsupported and context in geometry_contexts:
                availability, reason = (
                    "ambiguous",
                    "This solve does not declare a datum output",
                )
            elif availability == "needs_input":
                reason = "Configure the selection inputs before using this output"
            elif (
                isinstance(node, Selection)
                and len(values.get(node.id, {}).get("ids", node.ids)) != 1
            ):
                availability, reason = (
                    "blocked",
                    "Select exactly one node for a point output",
                )
            preview = None
            if availability == "ready" and capability != "observations":
                try:
                    preview = resolve_output(
                        reference, nodes, values, coordinates=coordinates
                    )
                except (ValueError, KeyError, IndexError):
                    availability, reason = (
                        "blocked",
                        "The declared output is unavailable; evaluate its provider",
                    )
            if availability != "ready" and reason is None:
                reason = {
                    "unevaluated": "Evaluate its provider first",
                    "running": "Its provider is evaluating",
                    "stale": "Reevaluate its changed provider",
                    "failed": "Repair or retry its failed provider",
                    "blocked": "Repair its blocked provider",
                }.get(availability, "Output unavailable")
            constrainable = (
                context == node.id and primary["parameter_owner"] is not None
            )
            descriptors.append(
                {
                    "reference": reference.model_dump(),
                    "capability": capability,
                    "label": f"{node.label} — {output.replace('_', ' ')}"
                    + (
                        f" · {nodes[context].label}"
                        if context in nodes and context != node.id
                        else ""
                    ),
                    "feature_label": node.label,
                    "producing_context": {
                        "id": context,
                        "label": node.label,
                        "kind": "feature",
                    }
                    if context == node.id
                    else producing_contexts[context].copy(),
                    "availability": availability,
                    "reason": reason,
                    "source": provenance(node.id),
                    "frame": frame,
                    "ownership": ownership,
                    "constrainable": constrainable,
                    "dependencies": closure(prerequisites),
                    "preview": preview,
                    "capabilities": primary["capabilities"],
                    "properties": _property_descriptors(primary, reference, frame),
                    "uses": (["geometry"] if context in geometry_contexts else [])
                    + (
                        ["binding"]
                        if context == node.id and primary["parameter_owner"]
                        else []
                    ),
                    "parameter_owner": primary["parameter_owner"]
                    if context == node.id
                    else None,
                    **{
                        key: primary[key]
                        for key in (
                            "geometry_kind",
                            "axis",
                            "construction",
                            "standalone",
                        )
                    },
                }
            )
            for extra in specs[1:]:
                extra_output, extra_capability = (
                    extra["output"],
                    extra["capabilities"][0],
                )
                descriptor = deepcopy(descriptors[-1])
                descriptor.update(
                    reference=OutputReference(
                        feature=node.id, output=extra_output, context=context
                    ).model_dump(),
                    capability=extra_capability,
                    label=f"{node.label} — {extra_output}",
                    preview=None,
                    capabilities=extra["capabilities"],
                    properties=_property_descriptors(
                        extra,
                        OutputReference(
                            feature=node.id, output=extra_output, context=context
                        ),
                        frame,
                    ),
                    constrainable=context == node.id
                    and extra["parameter_owner"] is not None,
                    parameter_owner=extra["parameter_owner"]
                    if context == node.id
                    else None,
                    uses=(["geometry"] if context in geometry_contexts else [])
                    + (
                        ["binding"]
                        if context == node.id and extra["parameter_owner"]
                        else []
                    ),
                )
                if isinstance(node, Selection):
                    descriptor.update(
                        availability=states.get(node.id, "unevaluated"), reason=None
                    )
                # Supporting observations are a separate output, not geometry
                # to embed repeatedly in every catalogue publication.
                if extra_capability == "observations":
                    descriptor["preview"] = None
                elif descriptor["availability"] == "ready":
                    try:
                        descriptor["preview"] = resolve_output(
                            OutputReference.model_validate(descriptor["reference"]),
                            nodes,
                            values,
                            coordinates=coordinates,
                        )
                    except (ValueError, KeyError, IndexError):
                        descriptor.update(
                            availability="blocked",
                            reason="The declared output is unavailable; evaluate its provider",
                        )
                descriptors.append(descriptor)
    return descriptors


def compatibility_reason(
    descriptor: dict[str, Any],
    requirement: str,
    *,
    source: str | None = None,
    frame: str | None = None,
) -> str | None:
    if requirement not in REQUIREMENTS:
        raise ValueError(f"unknown input requirement {requirement!r}")
    contract = REQUIREMENTS[requirement]
    if not set(descriptor.get("capabilities", [descriptor["capability"]])).intersection(
        contract["capabilities"]
    ):
        return "This output does not provide the required capability"
    if contract.get("property") not in (None, *descriptor.get("properties", {})):
        return "This output does not provide the required geometric property"
    if contract.get("direct_context"):
        reference = descriptor.get("reference", {})
        if not reference or reference.get("context") != reference.get("feature"):
            return "This input requires a direct datum context; solved publications cannot be stored as a fit reference"
    uses = contract.get("uses", ["geometry"])
    if (
        requirement == "relationship_direction"
        and not descriptor.get("properties", {})
        .get("direction", {})
        .get("binding_eligible", False)
        and "geometry" not in descriptor.get("uses", ["geometry"])
    ):
        return (
            "This direction is read-only; choose its exact published geometry context"
        )
    if not set(descriptor.get("uses", ["geometry"])).intersection(uses):
        return "This output does not provide the required input role"
    if contract.get("ownership") and (
        descriptor["ownership"] != contract["ownership"]
        or not descriptor["constrainable"]
    ):
        if contract.get("fit_reference"):
            return "This fit input requires an explicit datum of the required kind"
        return (
            "This input requires an adjustable parameter owner, not read-only geometry"
        )
    if (
        contract.get("geometry_kinds")
        and descriptor.get("geometry_kind") not in contract["geometry_kinds"]
    ):
        return "This output has an incompatible geometry kind"
    if contract.get("standalone") and not descriptor.get("standalone"):
        return "This input requires a standalone parameter owner"
    if contract.get("axis_bound") and not descriptor.get("axis"):
        return "This input requires an axis-bound parameter owner"
    if (
        contract.get("constructions")
        and descriptor.get("construction") not in contract["constructions"]
    ):
        return "This input requires a compatible datum construction"
    if source is not None and descriptor["source"] not in (None, source):
        return "This output belongs to a different source"
    if frame is not None and descriptor["frame"] != frame:
        return "This output belongs to a different coordinate frame"
    return None


def fit_reference_requirement(kind: str, field: str) -> str:
    """Find a supported legacy fit field in the shared consumer contracts."""
    matches = [
        name
        for name, contract in REQUIREMENTS.items()
        if (reference := contract.get("fit_reference")) is not None
        and kind in reference["kinds"]
        and field == reference["field"]
    ]
    if len(matches) != 1:
        raise ValueError(f"fit reference {field!r} is unavailable for {kind!r}")
    return matches[0]


def validate_fit_reference(
    kind: str,
    field: str,
    reference: FeatureInput | dict[str, Any],
    nodes: dict[str, Feature],
) -> OutputReference:
    """Check one supported datum without converting an exact read into a binding.

    Existing SurfaceFit fields store direct feature IDs. Their known output is
    supplied by the contract, never inferred from a generic string candidate.
    """
    requirement = fit_reference_requirement(kind, field)
    contract = REQUIREMENTS[requirement]
    reference = (
        OutputReference(feature=reference, output=contract["fit_reference"]["output"])
        if isinstance(reference, str)
        else reference
        if isinstance(reference, OutputReference)
        else OutputReference.model_validate(reference)
    )
    if reference.context != reference.feature:
        raise ValueError(
            "fit references require a direct datum context; solved publications cannot be stored in a legacy fit field"
        )
    node = nodes.get(reference.feature)
    if node is None:
        raise ValueError("fit reference refers to a removed datum")
    spec = next(
        (
            spec
            for spec in declared_output_specs(node)
            if spec["output"] == reference.output
        ),
        None,
    )
    if spec is None:
        raise ValueError(
            f"fit reference requires an explicit {field.replace('_', ' ')} providing the required datum output"
        )
    descriptor = {
        **spec,
        "reference": reference.model_dump(),
        "capability": spec["capabilities"][0],
        "uses": ["geometry", "binding"] if spec["parameter_owner"] else ["geometry"],
        "constrainable": spec["parameter_owner"] is not None,
        "source": None,
        "frame": "workspace",
    }
    reason = compatibility_reason(descriptor, requirement)
    if reason is not None:
        raise ValueError(reason)
    return reference


def input_choices(
    recipe: Recipe,
    states: dict[str, str] | None = None,
    results: dict[str, dict[str, Any]] | None = None,
    *,
    consumer_id: str | None = None,
    requirement: str = "point",
    retained: Any = (),
    source: str | None = None,
    frame: str | None = None,
    catalogue: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    catalogue = (
        catalogue
        if catalogue is not None
        else output_catalogue(recipe, states, results)
    )
    choices: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    positions = {node.id: index for index, node in enumerate(recipe.nodes)}
    retained_keys = {
        reference_key(OutputReference.model_validate(ref))
        for ref in retained
        if isinstance(ref, dict)
    }
    for original in catalogue:
        descriptor = deepcopy(original)
        reason = compatibility_reason(
            descriptor, requirement, source=source, frame=frame
        )
        descriptor["compatible"] = reason is None
        ref = OutputReference.model_validate(descriptor["reference"])
        retained_output = reference_key(ref) in retained_keys
        if not retained_output and not set(
            descriptor.get("uses", ["geometry"])
        ).intersection(REQUIREMENTS[requirement].get("uses", ["geometry"])):
            continue
        if (
            reason is not None
            and not retained_output
            and not set(
                descriptor.get("capabilities", [descriptor["capability"]])
            ).intersection(REQUIREMENTS[requirement]["capabilities"])
        ):
            # Outputs of another capability are noise, whereas ownership
            # restrictions explain why otherwise relevant geometry is rejected.
            continue
        if (
            "binding" in REQUIREMENTS[requirement].get("uses", [])
            and descriptor.get("parameter_owner")
            and (
                requirement != "relationship_direction"
                or descriptor["properties"]
                .get("direction", {})
                .get("binding_eligible", False)
            )
        ):
            # Binding original observations/parameters does not read a failed
            # coupled publication. A corrective relationship remains authorable.
            descriptor.update(availability="ready", reason=None)
        if reason is None and consumer_id in positions:
            if consumer_id in descriptor["dependencies"]:
                reason = "This output would create a dependency cycle"
            elif any(
                positions[dep] >= positions[consumer_id]
                for dep in reference_dependencies(ref)
            ):
                reason = "Its provider must appear earlier than this input"
        if reason is not None:
            descriptor["compatible"] = False
            descriptor["reason"] = reason
            unavailable.append(descriptor)
        elif descriptor["availability"] == "ready":
            choices.append(descriptor)
        else:
            unavailable.append(descriptor)
    return {"choices": choices, "unavailable": unavailable}


def validate_output_references(
    references: list[OutputReference],
    recipe: Recipe,
    requirement: str,
) -> list[dict[str, Any]]:
    contract = REQUIREMENTS[requirement]
    if len(references) < contract["min_count"] or len(references) > contract.get(
        "max_count", len(references)
    ):
        raise ValueError("input cardinality does not meet its requirement")
    if len(set(references)) != len(references):
        raise ValueError("input output references must be distinct")
    return [validate_output_reference(ref, recipe, requirement) for ref in references]


def validate_parameter_owner(node: Feature, requirement: str) -> None:
    """Share capability/ownership checks without turning a binding into a read."""
    from experiments.feature_graph import PointDefinition, SurfaceFit

    descriptor = {
        "capability": "point"
        if isinstance(node, PointDefinition)
        else "plane"
        if isinstance(node, SurfaceFit) and node.kind == "plane"
        else None,
        "ownership": "datum"
        if isinstance(node, PointDefinition)
        else "fit"
        if isinstance(node, SurfaceFit)
        else None,
        "constrainable": isinstance(node, (PointDefinition, SurfaceFit)),
        "source": None,
        "frame": "workspace",
    }
    reason = compatibility_reason(descriptor, requirement)
    if reason is not None:
        raise ValueError(reason)


def validate_output_reference(
    reference: OutputReference,
    recipe: Recipe,
    requirement: str,
    *,
    frame: str = "workspace",
) -> dict[str, Any]:
    canonical = reference.model_copy(
        update={"context": reference.context or reference.feature}
    )
    descriptor = next(
        (
            item
            for item in output_catalogue(recipe)
            if OutputReference.model_validate(item["reference"]) == canonical
        ),
        None,
    )
    if descriptor is None:
        raise ValueError(
            "output reference is removed, incompatible, or has an ambiguous context"
        )
    reason = compatibility_reason(descriptor, requirement, frame=frame)
    if reason is not None:
        raise ValueError(reason)
    if descriptor["availability"] == "ambiguous" or (
        reference.output == "single_node" and descriptor["availability"] == "blocked"
    ):
        raise ValueError(descriptor["reason"])
    return descriptor


def resolve_output(
    reference: OutputReference,
    nodes: dict[str, Feature],
    values: dict[str, dict[str, Any]],
    *,
    coordinates: Any = None,
) -> dict[str, Any]:
    """Read an exact publisher, never whichever overlay was most recently ready."""
    from experiments.feature_graph import Selection, SurfaceSelection, selection_source

    node = nodes[reference.feature]
    context = reference.context or reference.feature
    selection_ids = (
        values.get(node.id, {}).get("ids", node.ids)
        if isinstance(node, Selection)
        else []
    )
    if isinstance(node, Selection) and reference.output == "single_node":
        if len(selection_ids) != 1 or coordinates is None:
            raise ValueError("a point output requires exactly one selected node")
        return {
            "point_display": np.asarray(
                coordinates[selection_ids[0]], dtype=float
            ).tolist()
        }
    if isinstance(node, Selection) and reference.output == "observations":
        return {"ids": list(selection_ids), "source": node.source}
    if isinstance(node, SurfaceSelection) and reference.output == "observations":
        return {
            "ids": list(values[node.id]["ids"]),
            "source": selection_source(node, nodes),
        }
    raw = values[context]
    if context != reference.feature:
        raw = raw.get("resolved", raw.get("surfaces", {})).get(
            reference.feature, raw.get("outputs", {}).get(reference.feature)
        )
        if raw is None:
            raise ValueError("context omitted the declared output")
    if reference.output == "center":
        return {"point_display": list(raw["parameters"][:3])}
    if reference.output == "surface":
        if getattr(node, "kind", None) == "sphere":
            return {
                "kind": raw.get("kind", "sphere"),
                "parameters": list(raw["parameters"]),
            }
        return {
            key: deepcopy(raw[key])
            for key in ("kind", "analytic", "parameters", "axial_domain")
            if key in raw
        }
    if reference.output == "radius":
        if getattr(node, "kind", None) == "cylinder":
            from experiments.surface_primitives import revolution_geometry

            radius = revolution_geometry(raw)["radius"]
        else:
            radius = raw["parameters"][3]
        return {"value": float(radius), "semantic": "radius"}
    if reference.output == "observations":
        return {"ids": list(raw["ids"])}
    if reference.output == "point":
        return {"point_display": list(raw["point_display"])}
    if reference.output == "plane":
        equation = raw.get("plane_equation", raw.get("parameters"))
        if equation is not None:
            coefficients = np.asarray(equation, dtype=float)
            if "plane_equation" not in raw and coefficients.shape == (7,):
                from experiments.surface_primitives import primitive

                if not np.isfinite(coefficients).all():
                    raise ValueError("plane joint parameters must be finite")
                plane = primitive({"kind": "plane", "parameters": coefficients})
                equation = [*plane["axis"], plane["offset"]]
                coefficients = np.asarray(equation, dtype=float)
            if coefficients.shape != (4,) or not np.isfinite(coefficients).all():
                raise ValueError("plane equation must contain four finite values")
            length = float(np.linalg.norm(coefficients[:3]))
            if not np.isfinite(length) or length <= 0:
                raise ValueError("plane equation must have a nonzero finite normal")
        normal = raw.get(
            "normal_display", equation[:3] if equation is not None else None
        )
        if normal is None:
            raise ValueError("plane output has no normal")
        normal_array = np.asarray(normal, dtype=float)
        if normal_array.shape != (3,) or not np.isfinite(normal_array).all():
            raise ValueError("plane normal must contain three finite values")
        normal_length = float(np.linalg.norm(normal_array))
        if not np.isfinite(normal_length) or normal_length <= 0:
            raise ValueError("plane normal must be nonzero and finite")
        if equation is None:
            point_array = np.asarray(raw["point_display"], dtype=float)
            if point_array.shape != (3,) or not np.isfinite(point_array).all():
                raise ValueError("plane point must contain three finite values")
            equation = [
                *normal_array.tolist(),
                float(normal_array @ point_array),
            ]
            if not np.isfinite(equation).all():
                raise ValueError("plane equation must contain four finite values")
        point = raw.get(
            "point_display",
            (
                normal_array * float(equation[3]) / float(normal_array @ normal_array)
            ).tolist(),
        )
        point_array = np.asarray(point, dtype=float)
        if point_array.shape != (3,) or not np.isfinite(point_array).all():
            raise ValueError("plane point must contain three finite values")
        return {
            "normal_display": normal_array.tolist(),
            "plane_equation": list(equation),
            "point_display": point_array.tolist(),
        }
    if reference.output == "axis":
        if node.operation == "fit":
            from experiments.surface_primitives import revolution_geometry

            geometry = revolution_geometry(raw)
            return {
                "axis_display": list(geometry["axis"]),
                "point_display": list(geometry["origin"]),
            }
        return {
            "axis_display": list(raw["axis_display"]),
            "point_display": list(raw["point_display"]),
        }
    raise ValueError("unknown selectable output")


def resolve_property(
    reference: OutputReference,
    property_name: str,
    nodes: dict[str, Feature],
    values: dict[str, dict[str, Any]],
    *,
    coordinates: Any = None,
    frame: str = "workspace",
    required_frame: str | None = None,
) -> dict[str, Any]:
    """Project a typed property from one exact output publication.

    References remain output references; property identity is a separate consumer
    contract. An eligible owner need not be free in the solve using this value.
    Like output_catalogue, frame declares the actual coordinate frame supplied
    by the caller; required_frame only checks it. Graph publications use workspace.
    """
    node = nodes.get(reference.feature)
    if node is None:
        raise ValueError("property reference refers to a removed feature")
    spec = next(
        (
            item
            for item in declared_output_specs(node)
            if item["output"] == reference.output
        ),
        None,
    )
    if spec is None or property_name not in spec["properties"]:
        raise ValueError("this output does not provide the requested property")
    descriptor = _property_descriptors(spec, reference, frame)[property_name]
    if required_frame is not None and descriptor["frame"] != required_frame:
        raise ValueError("this property belongs to a different coordinate frame")
    raw = resolve_output(reference, nodes, values, coordinates=coordinates)

    def vector(value: Any, *, normalize: bool = False) -> list[float]:
        array = np.asarray(value, dtype=float)
        if array.shape != (3,) or not np.isfinite(array).all():
            raise ValueError("property vector must contain three finite values")
        if normalize:
            scale = float(np.max(np.abs(array)))
            if scale == 0:
                raise ValueError("property direction must be nonzero")
            array = array / scale
            array /= np.linalg.norm(array)
        return array.tolist()

    projection = descriptor["projection"]
    if projection in {"axis_direction", "plane_normal"}:
        value = vector(
            raw["axis_display" if projection == "axis_direction" else "normal_display"],
            normalize=True,
        )
    elif projection == "point":
        value = vector(raw["point_display"])
    elif projection == "axis_line":
        value = {
            "point": vector(raw["point_display"]),
            "direction": vector(raw["axis_display"], normalize=True),
        }
    elif projection == "plane":
        equation = np.asarray(raw["plane_equation"], dtype=float)
        normal = vector(equation[:3], normalize=True)
        scale = float(np.max(np.abs(equation[:3])))
        offset = float((equation[3] / scale) / np.linalg.norm(equation[:3] / scale))
        if not np.isfinite(offset):
            raise ValueError("property plane offset must be finite")
        value = {"normal": normal, "offset": offset}
    elif projection == "radius":
        value = float(raw["value"])
        if not np.isfinite(value) or value <= 0:
            raise ValueError("property radius must be positive and finite")
    else:
        raise ValueError("unknown property projection")
    return {**descriptor, "value": value}
