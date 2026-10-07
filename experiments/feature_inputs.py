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
                    if reference.context.startswith(("@axis/", "@point/"))
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
    "point": {"capabilities": ["point"], "min_count": 1, "max_count": 1},
    "plane": {"capabilities": ["plane"], "min_count": 1, "max_count": 1},
    "direction": {
        "capabilities": ["plane", "direction"],
        "min_count": 1,
        "max_count": 1,
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
}


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
        JointFit,
        MirrorSymmetry,
        PlaneDefinition,
        PointDefinition,
        Selection,
        SurfaceFit,
        dependencies,
        describe_geometry_influence,
        joint_surfaces,
    )

    nodes = {node.id: node for node in recipe.nodes}
    influence = describe_geometry_influence(nodes)
    states = states or {}
    values = values or {}
    contexts: dict[str, list[str]] = {}
    context_members: dict[str, set[str]] = {}
    for provider in influence.providers.values():
        for publisher, aliases in provider.publications.items():
            context_members[publisher] = set(provider.prerequisites)
            for alias in aliases:
                contexts.setdefault(alias, []).append(publisher)
    explicit_axes = {
        node.axis for node in nodes.values() if isinstance(node, AxisSolve)
    }
    for node in nodes.values():
        if isinstance(node, JointFit):
            sides, planes = joint_surfaces(node, nodes)
            members = [member.id for member in [*sides, *planes]]
        elif isinstance(node, AxisSolve):
            members = [
                ref for ref in node.factors if isinstance(nodes[ref], SurfaceFit)
            ]
            members += [
                ref
                for factor in node.factors
                if isinstance(nodes[factor], MirrorSymmetry)
                for ref in cast(MirrorSymmetry, nodes[factor]).surfaces
            ]
        else:
            continue
        context_members[node.id] = {node.id}
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
        if isinstance(node, Selection):
            output, capability, ownership = "single_node", "point", None
        elif isinstance(node, PointDefinition):
            output, capability, ownership = "point", "point", "datum"
        elif isinstance(node, PlaneDefinition):
            output, capability, ownership = "plane", "plane", "datum"
        elif isinstance(node, AxisDefinition):
            output, capability, ownership = "axis", "direction", "datum"
        elif isinstance(node, SurfaceFit) and node.kind in ("sphere", "plane"):
            output = "center" if node.kind == "sphere" else "plane"
            capability, ownership = (
                ("point" if node.kind == "sphere" else "plane"),
                "fit",
            )
        else:
            continue
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
                            "running",
                            "stale",
                            "unevaluated",
                        )
                        if state in participant_states
                    ),
                    "ready",
                )
            reason: str | None = None
            if unsupported:
                availability, reason = (
                    "ambiguous",
                    "This solve does not declare a datum output",
                )
            elif isinstance(node, Selection) and len(node.ids) != 1:
                availability, reason = (
                    "blocked",
                    "Select exactly one node for a point output",
                )
            preview = None
            if availability == "ready":
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
            constrainable = ownership in ("datum", "fit")
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
                    "availability": availability,
                    "reason": reason,
                    "source": provenance(node.id),
                    "frame": frame,
                    "ownership": ownership,
                    "constrainable": constrainable,
                    "dependencies": closure(prerequisites),
                    "preview": preview,
                }
            )
            extras = (
                [("observations", "observations")]
                if isinstance(node, Selection)
                else [
                    ("surface", "surface"),
                    ("radius", "scalar"),
                    ("observations", "observations"),
                ]
                if isinstance(node, SurfaceFit) and node.kind == "sphere"
                else [("observations", "observations")]
                if isinstance(node, SurfaceFit)
                else []
            )
            for extra_output, extra_capability in extras:
                descriptor = deepcopy(descriptors[-1])
                descriptor.update(
                    reference=OutputReference(
                        feature=node.id, output=extra_output, context=context
                    ).model_dump(),
                    capability=extra_capability,
                    label=f"{node.label} — {extra_output}",
                    preview=None,
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
                    descriptor["preview"] = resolve_output(
                        OutputReference.model_validate(descriptor["reference"]),
                        nodes,
                        values,
                        coordinates=coordinates,
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
    if descriptor["capability"] not in contract["capabilities"]:
        return "This output does not provide the required capability"
    if contract.get("ownership") and (
        descriptor["ownership"] != contract["ownership"]
        or not descriptor["constrainable"]
    ):
        return (
            "This input requires an adjustable parameter owner, not read-only geometry"
        )
    if source is not None and descriptor["source"] not in (None, source):
        return "This output belongs to a different source"
    if frame is not None and descriptor["frame"] != frame:
        return "This output belongs to a different coordinate frame"
    return None


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
        else output_catalogue(recipe, states, results, frame=frame or "workspace")
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
        if (
            reason is not None
            and not retained_output
            and descriptor["capability"]
            not in REQUIREMENTS[requirement]["capabilities"]
        ):
            # Outputs of another capability are noise, whereas ownership
            # restrictions explain why otherwise relevant geometry is rejected.
            continue
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
            for item in output_catalogue(recipe, frame=frame)
            if OutputReference.model_validate(item["reference"]) == canonical
        ),
        None,
    )
    if descriptor is None:
        raise ValueError(
            "output reference is removed, incompatible, or has an ambiguous context"
        )
    reason = compatibility_reason(descriptor, requirement)
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
    from experiments.feature_graph import Selection

    node = nodes[reference.feature]
    context = reference.context or reference.feature
    if isinstance(node, Selection) and reference.output == "single_node":
        if len(node.ids) != 1 or coordinates is None:
            raise ValueError("a point output requires exactly one selected node")
        return {
            "point_display": np.asarray(coordinates[node.ids[0]], dtype=float).tolist()
        }
    if isinstance(node, Selection) and reference.output == "observations":
        return {"ids": list(node.ids), "source": node.source}
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
        return {
            "kind": raw.get("kind", "sphere"),
            "parameters": list(raw["parameters"]),
        }
    if reference.output == "radius":
        return {"value": float(raw["parameters"][3]), "semantic": "radius"}
    if reference.output == "observations":
        return {"ids": list(raw["ids"])}
    if reference.output == "point":
        return {"point_display": list(raw["point_display"])}
    if reference.output == "plane":
        equation = raw.get("plane_equation", raw.get("parameters"))
        normal = raw.get(
            "normal_display", equation[:3] if equation is not None else None
        )
        if normal is None:
            raise ValueError("plane output has no normal")
        normal_array = np.asarray(normal, dtype=float)
        if equation is None:
            equation = [
                *normal_array.tolist(),
                float(normal_array @ np.asarray(raw["point_display"])),
            ]
        point = raw.get(
            "point_display",
            (
                normal_array * float(equation[3]) / float(normal_array @ normal_array)
            ).tolist(),
        )
        return {
            "normal_display": normal_array.tolist(),
            "plane_equation": list(equation),
            "point_display": list(point),
        }
    if reference.output == "axis":
        return {
            "axis_display": list(raw["axis_display"]),
            "point_display": list(raw["point_display"]),
        }
    raise ValueError("unknown selectable output")
