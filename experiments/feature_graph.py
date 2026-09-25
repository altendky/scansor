"""Experimental backend DAG for captured-mesh recipes; current state only."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Annotated, Any, ClassVar, Literal, cast, final

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from experiments.feature_reuse import (
    estimate_rigid_match,
    surface_region_frame,
    transformed_fit_seed,
)
from experiments.nozzle_coaxial import (
    FitSelection,
    ReferencePlaneGroup,
    fit_fixed_axis_group,
    fit_group,
)
from experiments.nozzle_session import (
    NozzleSession,
    NozzleWorkspace,
    SessionFit,
    VertexId,
)
from experiments.selection_growth import connected_growth, fit_seed
from experiments.selection_region import (
    apply_selection_region,
    build_selection_region,
    datum_frame,
)


class Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Node(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=120)
    group_id: str | None = None
    managed_by: str | None = None
    managed_key: str | None = Field(default=None, min_length=1, max_length=240)


class FeatureGroup(Record):
    """Presentation-only organization for actions in the feature tree."""

    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    label: str = Field(min_length=1, max_length=120)


class Source(Node):
    operation: Literal["source"]
    source_sha256: str
    reference_sha256: str


class Selection(Node):
    operation: Literal["selection"]
    source: str
    ids: list[VertexId] = Field(max_length=25_000)
    depth: Literal["through_all", "first_surface"] = "through_all"


class SurfaceFit(Node):
    """An independently evaluated fit action; joints never overwrite its result."""

    operation: Literal["fit"]
    selections: list[str] = Field(min_length=1, max_length=99)
    kind: Literal["cone", "cylinder", "plane", "sphere"]
    axial_domain: tuple[float, float] = (-2.0, 5.0)
    axis: str | None = None
    point: str | None = None
    reference_plane: str | None = None

    @model_validator(mode="after")
    def compatible_reference_geometry(self) -> SurfaceFit:
        references = tuple(
            value
            for value in (self.axis, self.point, self.reference_plane)
            if value is not None
        )
        if len(references) > 1:
            raise ValueError("a fit can reference only one datum")
        if self.reference_plane is not None and self.kind != "plane":
            raise ValueError("only a plane fit can reference a plane datum")
        if self.point is not None and self.kind != "sphere":
            raise ValueError("only a sphere fit can reference a point datum")
        if self.axis is not None and self.kind == "sphere":
            raise ValueError("a sphere fit can reference a point, not an axis")
        return self


class Growth(Node):
    operation: Literal["growth"]
    seed_fit: str
    barriers: list[str] = Field(default_factory=list, max_length=99)
    distance: float = Field(gt=0, allow_inf_nan=False)
    angle_degrees: float = Field(gt=0, le=90, allow_inf_nan=False)


class SelectionRegion(Node):
    """A fitted selection lifted into a reusable surface-following volume."""

    operation: Literal["selection_region"]
    selection: str
    fit: str
    axial_plane: str
    clock_plane: str
    tangent_margin: float = Field(default=0.4, ge=0, allow_inf_nan=False)
    normal_margin: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    normal_angle_degrees: float = Field(default=30.0, gt=0, le=90, allow_inf_nan=False)


class RegionSelection(Node):
    """Source membership resolved by placing an earlier reusable region."""

    operation: Literal["region_selection"]
    region: str
    source: str
    axial_plane: str
    clock_plane: str


class FeatureReuse(Node):
    """Reusable fit lineage placed by corresponding painted selections."""

    operation: Literal["feature_reuse"]
    fits: list[str] = Field(min_length=1, max_length=32)
    lineage: list[str] = Field(min_length=1, max_length=99)
    reference_selection: str
    target_selections: list[str] = Field(min_length=1, max_length=32)
    tangent_margin: float = Field(default=0.4, ge=0, allow_inf_nan=False)
    normal_margin: float = Field(default=0.5, ge=0, allow_inf_nan=False)
    normal_angle_degrees: float = Field(default=30.0, gt=0, le=90, allow_inf_nan=False)
    equal_corresponding_dimensions: bool = False


class ReuseSelection(Node):
    """Target membership generated for one source selection in a reuse instance."""

    operation: Literal["reuse_selection"]
    reuse: str
    fit: str
    source_selection: str
    target_selection: str


class Perpendicular(Node):
    operation: Literal["perpendicular"]
    lateral: str
    plane: str


class Coaxial(Node):
    operation: Literal["coaxial"]
    surface: str
    reference: str


class RotationalSymmetry(Node):
    operation: Literal["rotational_symmetry"]
    axis: str
    # Existing serialized name retained; inputs are same-type surface fits.
    # Ordered positive rotations about normalize(a,b,1), in degrees 0/120/240.
    planes: list[str] = Field(min_length=3, max_length=3)
    symmetric_extents: bool = True


class JointFit(Node):
    operation: Literal["joint_fit"]
    constraints: list[str] = Field(min_length=1, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def read_single_constraint(cls, value: object) -> object:
        # Read current recipes saved by the first prototype. New saves always
        # use the constraint list; this is format compatibility, not edit history.
        if (
            isinstance(value, dict)
            and "constraint" in value
            and "constraints" not in value
        ):
            return {
                **{k: v for k, v in value.items() if k != "constraint"},
                "constraints": [value["constraint"]],
            }
        return value


class AxisDefinition(Node):
    """Directed axis initialized manually, by a fit, or by two ordered points."""

    operation: Literal["axis"]
    source_fit: str | None = None
    source_points: tuple[str, str] | None = None
    initial_parameters: tuple[float, float, float, float] | None = None
    direction_reversed: bool = False

    @model_validator(mode="after")
    def exactly_one_initializer(self) -> AxisDefinition:
        if (
            sum(
                initializer is not None
                for initializer in (
                    self.source_fit,
                    self.source_points,
                    self.initial_parameters,
                )
            )
            != 1
        ):
            raise ValueError(
                "axis requires exactly one of source_fit, source_points, or initial_parameters"
            )
        if (
            self.source_points is not None
            and self.source_points[0] == self.source_points[1]
        ):
            raise ValueError("a point-pair axis requires two distinct points")
        return self


class PointDefinition(Node):
    """Explicit point with either a manual value or an upstream sphere initializer."""

    operation: Literal["point"]
    source_fit: str | None = None
    initial_coordinates: tuple[float, float, float] | None = None

    @model_validator(mode="after")
    def exactly_one_initializer(self) -> PointDefinition:
        if (self.source_fit is None) == (self.initial_coordinates is None):
            raise ValueError(
                "point requires exactly one of source_fit or initial_coordinates"
            )
        return self


class ScaleDistance(Record):
    """One known output distance between two fitted point datums."""

    first_point: str
    second_point: str
    known_distance: float = Field(gt=0, allow_inf_nan=False)
    weight: float = Field(default=1.0, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def distinct_points(self) -> ScaleDistance:
        if self.first_point == self.second_point:
            raise ValueError("a scale distance requires two distinct points")
        return self


class ScaleDefinition(Node):
    """Uniform output scale calibrated by one or more known distances."""

    operation: Literal["scale"]
    distances: list[ScaleDistance] = Field(min_length=1, max_length=32)


CoordinateAxis = Literal["+X", "-X", "+Y", "-Y", "+Z", "-Z"]


class FrameDefinition(Node):
    """Right-handed coordinate frame constructed from explicit datum references."""

    operation: Literal["frame"]
    origin_point: str
    primary_reference: str
    primary_output_axis: CoordinateAxis
    secondary_reference: str
    secondary_output_axis: CoordinateAxis

    @model_validator(mode="after")
    def distinct_output_axes(self) -> FrameDefinition:
        if self.primary_reference == self.secondary_reference:
            raise ValueError("frame primary and secondary references must differ")
        if self.primary_output_axis[-1] == self.secondary_output_axis[-1]:
            raise ValueError("frame primary and secondary output axes must differ")
        return self


class TransformDefinition(Node):
    """Applicable similarity transform composed from a frame and scale."""

    operation: Literal["transform"]
    frame: str
    scale: str


class PlaneDefinition(Node):
    """A reference plane constructed parallel or perpendicular to an explicit axis."""

    operation: Literal["reference_plane"]
    axis: str
    construction: Literal[
        "contains_axis", "parallel_to_axis", "perpendicular_to_axis"
    ] = "contains_axis"
    initial_angle_degrees: float | None = Field(default=0.0, allow_inf_nan=False)
    offset: float = Field(default=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def construction_parameters(self) -> PlaneDefinition:
        if (
            self.construction in ("contains_axis", "parallel_to_axis")
            and self.initial_angle_degrees is None
        ):
            raise ValueError("a plane parallel to an axis requires a clocking angle")
        if self.construction == "contains_axis" and self.offset != 0.0:
            raise ValueError("a plane containing an axis cannot have an offset")
        if (
            self.construction == "perpendicular_to_axis"
            and self.initial_angle_degrees is not None
        ):
            raise ValueError("a plane perpendicular to an axis has no clocking angle")
        return self


class MirrorSymmetry(Node):
    """Two same-type standalone fits reflected across an axial reference plane."""

    operation: Literal["mirror_symmetry"]
    plane: str
    surfaces: list[str] = Field(min_length=2, max_length=2)
    symmetric_extents: bool = True


class ParallelToPlane(Node):
    """A fitted plane exactly parallel to an explicit reference plane."""

    operation: Literal["parallel"]
    surface: str
    reference_plane: str


class SurfaceRadius(Record):
    measurement: Literal["radius"]
    surface: str


class PlaneDistance(Record):
    measurement: Literal["plane_distance"]
    surface: str
    reference_plane: str


QuantityMeasurement = Annotated[
    SurfaceRadius | PlaneDistance, Field(discriminator="measurement")
]


class EqualQuantities(Node):
    """Exact equality between two typed geometric measurements."""

    operation: Literal["equal"]
    left: QuantityMeasurement
    right: QuantityMeasurement


class EqualRadii(Node):
    """Exact shared radius for two or more cylinder observations."""

    operation: Literal["equal_radii"]
    surfaces: list[str] = Field(min_length=2, max_length=33)


class PlaneRelationship(Node):
    """Exact orientation, and optionally offset, shared by plane fits."""

    operation: Literal["plane_relationship"]
    relation: Literal["coincident", "parallel"]
    surfaces: list[str] = Field(min_length=2, max_length=33)


class AxisSolve(Node):
    """Explicit active factors that jointly refine one shared free axis."""

    operation: Literal["axis_solve"]
    axis: str
    factors: list[str] = Field(min_length=2, max_length=32)


Feature = Annotated[
    Source
    | Selection
    | SurfaceFit
    | Growth
    | SelectionRegion
    | RegionSelection
    | FeatureReuse
    | ReuseSelection
    | Perpendicular
    | Coaxial
    | RotationalSymmetry
    | JointFit
    | AxisDefinition
    | PointDefinition
    | ScaleDefinition
    | FrameDefinition
    | TransformDefinition
    | PlaneDefinition
    | MirrorSymmetry
    | ParallelToPlane
    | EqualQuantities
    | EqualRadii
    | PlaneRelationship
    | AxisSolve,
    Field(discriminator="operation"),
]


class Recipe(Record):
    schema_version: Literal[2] = 2
    nodes: list[Feature] = Field(min_length=1, max_length=100)
    groups: list[FeatureGroup] = Field(default_factory=list, max_length=100)
    output: str

    @model_validator(mode="before")
    @classmethod
    def migrate_unordered_recipe(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        payload = deepcopy(cast(dict[str, Any], value))
        if payload.get("schema_version", 1) != 1:
            return value
        for raw_node in cast(list[object], payload.get("nodes", [])):
            if not isinstance(raw_node, dict):
                raise ValueError("legacy action must be an object")
            node = cast(dict[str, Any], raw_node)
            if node.get("operation") in ("surface", "seed_fit"):
                node["operation"] = "fit"
            if node.get("operation") == "fit" and "selection" in node:
                node["selections"] = [node.pop("selection")]
        # Legacy graphs allowed forward references. Migrate once to a stable
        # dependency order; version 2 rejects forward references rather than
        # silently rearranging the user's actions.
        remaining: list[Feature] = [
            TypeAdapter(Feature).validate_python(n) for n in payload.get("nodes", [])
        ]
        ordered: list[Feature] = []
        seen: set[str] = set()
        while remaining:
            next_node = next(
                (n for n in remaining if set(dependencies(n)) <= seen), None
            )
            if next_node is None:
                raise ValueError(
                    "legacy graph contains a cycle or missing feature input"
                )
            remaining.remove(next_node)
            ordered.append(next_node)
            seen.add(next_node.id)
        payload["nodes"] = [n.model_dump() for n in ordered]
        payload["schema_version"] = 2
        return payload

    @model_validator(mode="after")
    def unique_feature_names(self) -> Recipe:
        names = [node.label.strip().casefold() for node in self.nodes]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        group_ids = [group.id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("feature group IDs must be unique")
        if set(group_ids) & {node.id for node in self.nodes}:
            raise ValueError("feature group IDs must not match feature IDs")
        group_names = [group.label.strip().casefold() for group in self.groups]
        if len(group_names) != len(set(group_names)):
            raise ValueError("feature group names must be unique")
        groups = set(group_ids)
        positions = {node.id: index for index, node in enumerate(self.nodes)}
        managed_keys: set[tuple[str, str]] = set()
        for node in self.nodes:
            if node.group_id is not None and node.group_id not in groups:
                raise ValueError("feature references an unknown organizational group")
            if (node.managed_by is None) != (node.managed_key is None):
                raise ValueError(
                    "managed features require both an owner and stable key"
                )
            if node.managed_by is None:
                continue
            if node.group_id is not None:
                raise ValueError("managed features inherit their owner's group")
            if (
                node.managed_by not in positions
                or positions[node.managed_by] >= positions[node.id]
            ):
                raise ValueError("managed feature owners must be earlier actions")
            key = (node.managed_by, cast(str, node.managed_key))
            if key in managed_keys:
                raise ValueError(
                    "managed feature keys must be unique within their owner"
                )
            managed_keys.add(key)
        return self


class GraphRequest(Record):
    token: str
    recipe: Recipe | None = None
    target: str | None = None
    all_actions: bool = False


def dependencies(node: Feature) -> list[str]:
    if isinstance(node, Selection):
        return [node.source]
    if isinstance(node, SurfaceFit):
        reference = node.axis or node.point or node.reference_plane
        return [*node.selections, *((reference,) if reference is not None else ())]
    if isinstance(node, Growth):
        return [node.seed_fit, *node.barriers]
    if isinstance(node, SelectionRegion):
        return [node.selection, node.fit, node.axial_plane, node.clock_plane]
    if isinstance(node, RegionSelection):
        return [node.region, node.source, node.axial_plane, node.clock_plane]
    if isinstance(node, FeatureReuse):
        return list(
            dict.fromkeys(
                [
                    *node.fits,
                    *node.lineage,
                    node.reference_selection,
                    *node.target_selections,
                ]
            )
        )
    if isinstance(node, ReuseSelection):
        return [node.reuse, node.fit, node.source_selection, node.target_selection]
    if isinstance(node, Perpendicular):
        return [node.lateral, node.plane]
    if isinstance(node, Coaxial):
        return [node.surface, node.reference]
    if isinstance(node, RotationalSymmetry):
        return [node.axis, *node.planes]
    if isinstance(node, JointFit):
        return node.constraints
    if isinstance(node, AxisDefinition):
        if node.source_fit is not None:
            return [node.source_fit]
        return list(node.source_points or ())
    if isinstance(node, PointDefinition):
        return [node.source_fit] if node.source_fit is not None else []
    if isinstance(node, ScaleDefinition):
        return list(
            dict.fromkeys(
                [
                    *(
                        ref
                        for distance in node.distances
                        for ref in (
                            distance.first_point,
                            distance.second_point,
                        )
                    ),
                ]
            )
        )
    if isinstance(node, FrameDefinition):
        return list(
            dict.fromkeys(
                [node.origin_point, node.primary_reference, node.secondary_reference]
            )
        )
    if isinstance(node, TransformDefinition):
        return [node.frame, node.scale]
    if isinstance(node, PlaneDefinition):
        return [node.axis]
    if isinstance(node, MirrorSymmetry):
        return [node.plane, *node.surfaces]
    if isinstance(node, ParallelToPlane):
        return [node.surface, node.reference_plane]
    if isinstance(node, EqualQuantities):
        refs: list[str] = []
        for measurement in (node.left, node.right):
            refs.append(measurement.surface)
            if isinstance(measurement, PlaneDistance):
                refs.append(measurement.reference_plane)
        return list(dict.fromkeys(refs))
    if isinstance(node, EqualRadii):
        return node.surfaces
    if isinstance(node, PlaneRelationship):
        return node.surfaces
    if isinstance(node, AxisSolve):
        return [node.axis, *node.factors]
    return []


def is_standalone_fit(node: SurfaceFit) -> bool:
    return node.axis is None and node.point is None and node.reference_plane is None


def fit_axis(node: SurfaceFit, nodes: dict[str, Feature]) -> str | None:
    if node.axis is not None:
        return node.axis
    if node.reference_plane is not None:
        reference = nodes[node.reference_plane]
        if isinstance(reference, PlaneDefinition):
            return reference.axis
    return None


def automatic_axis_components(
    nodes: dict[str, Feature],
) -> dict[str, tuple[list[SurfaceFit], set[str]]]:
    """Connected fit evidence for manually initialized (free) axes."""
    components: dict[str, tuple[list[SurfaceFit], set[str]]] = {}
    for axis in nodes.values():
        if not isinstance(axis, AxisDefinition) or axis.initial_parameters is None:
            continue
        factors = [
            node
            for node in nodes.values()
            if isinstance(node, SurfaceFit) and fit_axis(node, nodes) == axis.id
        ]
        if not any(factor.kind in ("cone", "cylinder") for factor in factors):
            continue
        members = {
            axis.id,
            *(factor.reference_plane for factor in factors if factor.reference_plane),
            *(factor.id for factor in factors),
        }
        components[axis.id] = (factors, members)
    return components


def automatic_point_components(
    nodes: dict[str, Feature],
) -> dict[str, tuple[list[SurfaceFit], set[str]]]:
    """Sphere evidence that jointly refines each manually initialized free point."""
    components: dict[str, tuple[list[SurfaceFit], set[str]]] = {}
    for point in nodes.values():
        if not isinstance(point, PointDefinition) or point.source_fit is not None:
            continue
        factors = [
            node
            for node in nodes.values()
            if isinstance(node, SurfaceFit)
            and node.kind == "sphere"
            and node.point == point.id
        ]
        if factors:
            components[point.id] = (factors, {point.id, *(fit.id for fit in factors)})
    return components


def automatic_equal_radius_components(
    nodes: dict[str, Feature],
) -> dict[str, set[str]]:
    """Radius relationships that participate whenever one member is evaluated."""
    return {
        node.id: {node.id, *node.surfaces}
        for node in nodes.values()
        if isinstance(node, EqualRadii)
    }


def automatic_plane_relationship_components(
    nodes: dict[str, Feature],
) -> dict[str, tuple[list[PlaneRelationship], list[SurfaceFit], set[str]]]:
    """Connected exact plane relationships evaluated as one constraint system."""
    relationships = [
        node for node in nodes.values() if isinstance(node, PlaneRelationship)
    ]
    pending = set(relationship.id for relationship in relationships)
    by_id = {relationship.id: relationship for relationship in relationships}
    components: dict[
        str, tuple[list[PlaneRelationship], list[SurfaceFit], set[str]]
    ] = {}
    while pending:
        first = next(
            relationship.id
            for relationship in relationships
            if relationship.id in pending
        )
        relation_ids = {first}
        surface_ids = set(by_id[first].surfaces)
        while True:
            connected = {
                relationship.id
                for relationship in relationships
                if relationship.id in pending
                and surface_ids.intersection(relationship.surfaces)
            }
            expanded_surfaces = {
                surface
                for relationship_id in connected | relation_ids
                for surface in by_id[relationship_id].surfaces
            }
            if connected <= relation_ids and expanded_surfaces == surface_ids:
                break
            relation_ids.update(connected)
            surface_ids.update(expanded_surfaces)
        pending.difference_update(relation_ids)
        component_relationships = [
            relationship
            for relationship in relationships
            if relationship.id in relation_ids
        ]
        component_surfaces = [
            cast(SurfaceFit, nodes[node_id])
            for node_id in nodes
            if node_id in surface_ids
        ]
        members = {*relation_ids, *surface_ids}
        for relationship_id in relation_ids:
            components[relationship_id] = (
                component_relationships,
                component_surfaces,
                members,
            )
    return components


def selection_frame_axis(
    axial_plane_id: str, clock_plane_id: str, nodes: dict[str, Feature]
) -> str:
    axial_plane = nodes[axial_plane_id]
    clock_plane = nodes[clock_plane_id]
    if (
        not isinstance(axial_plane, PlaneDefinition)
        or axial_plane.construction != "perpendicular_to_axis"
    ):
        raise ValueError("a selection frame requires a perpendicular axial plane")
    if not isinstance(clock_plane, PlaneDefinition) or clock_plane.construction not in (
        "contains_axis",
        "parallel_to_axis",
    ):
        raise ValueError("a selection frame requires an axis-parallel clock plane")
    if axial_plane.axis != clock_plane.axis:
        raise ValueError("selection-frame planes must reference the same axis")
    return axial_plane.axis


def selection_source(node: Feature, nodes: dict[str, Feature]) -> str:
    if isinstance(node, Selection):
        return node.source
    if isinstance(node, RegionSelection):
        return node.source
    if isinstance(node, ReuseSelection):
        return selection_source(nodes[node.target_selection], nodes)
    if isinstance(node, Growth):
        fitted = nodes[node.seed_fit]
        if isinstance(fitted, SurfaceFit):
            sources = {selection_source(nodes[key], nodes) for key in fitted.selections}
            if len(sources) == 1:
                return next(iter(sources))
    raise ValueError("expected selections from one source")


def discover_reuse_lineage(fit_ids: list[str], nodes: dict[str, Feature]) -> list[str]:
    """Return selected fits, their upstream inputs, and enclosed relationships."""
    selected = set(fit_ids)
    closure = set(fit_ids)

    def add_upstream() -> bool:
        before = len(closure)
        for key in tuple(closure):
            closure.update(dependencies(nodes[key]))
        return len(closure) != before

    while add_upstream():
        pass
    relationship_types = (
        Perpendicular,
        Coaxial,
        RotationalSymmetry,
        JointFit,
        MirrorSymmetry,
        ParallelToPlane,
        EqualQuantities,
        EqualRadii,
        AxisSolve,
    )

    def referenced_fits(candidate: Feature) -> set[str]:
        found: set[str] = set()
        pending = list(dependencies(candidate))
        visited: set[str] = set()
        while pending:
            ref = pending.pop()
            if ref in visited:
                continue
            visited.add(ref)
            referenced = nodes[ref]
            if isinstance(referenced, SurfaceFit):
                found.add(ref)
            elif isinstance(referenced, relationship_types):
                pending.extend(dependencies(referenced))
        return found

    changed = True
    while changed:
        changed = False
        for candidate in nodes.values():
            if candidate.id in closure or not isinstance(candidate, relationship_types):
                continue
            refs = dependencies(candidate)
            fitted_refs = referenced_fits(candidate)
            enclosed = bool(fitted_refs) and fitted_refs <= selected
            if enclosed:
                closure.add(candidate.id)
                closure.update(refs)
                changed = True
        while add_upstream():
            changed = True
    return [key for key in nodes if key in closure]


def joint_surfaces(
    node: JointFit, nodes: dict[str, Feature]
) -> tuple[list[SurfaceFit], list[SurfaceFit]]:
    """Compile a connected constraint group; independently offset perpendicular planes."""
    if len(set(node.constraints)) != len(node.constraints):
        raise ValueError("duplicate joint constraint")
    sides: dict[str, SurfaceFit] = {}
    planes: dict[str, SurfaceFit] = {}
    rotational_planes: dict[str, SurfaceFit] = {}
    edges: list[tuple[str, str]] = []
    anchors: set[str] = set()
    for key in node.constraints:
        constraint = nodes[key]
        if isinstance(constraint, Perpendicular):
            side, plane = nodes[constraint.lateral], nodes[constraint.plane]
            if (
                not isinstance(side, SurfaceFit)
                or side.kind not in ("cone", "cylinder")
                or not isinstance(plane, SurfaceFit)
                or plane.kind != "plane"
            ):
                raise ValueError(
                    "supported relationship needs a cone/cylinder and plane"
                )
            sides[side.id], planes[plane.id] = side, plane
            anchors.add(side.id)
        elif isinstance(constraint, RotationalSymmetry):
            axis = nodes[constraint.axis]
            if not isinstance(axis, SurfaceFit) or axis.kind not in (
                "cone",
                "cylinder",
            ):
                raise ValueError(
                    "rotational symmetry requires a cone/cylinder axis fit"
                )
            sides[axis.id] = axis
            if len(set(constraint.planes)) != 3:
                raise ValueError(
                    "rotational symmetry requires three distinct same-type surface fits"
                )
            for ref in constraint.planes:
                plane = nodes[ref]
                if not isinstance(plane, SurfaceFit):
                    raise ValueError("rotational symmetry inputs must be surface fits")
                if ref in rotational_planes:
                    raise ValueError(
                        "a surface cannot belong to multiple rotational groups"
                    )
                rotational_planes[ref] = plane
        elif isinstance(constraint, Coaxial):
            for ref in (constraint.surface, constraint.reference):
                surface = nodes[ref]
                if not isinstance(surface, SurfaceFit) or surface.kind not in (
                    "cone",
                    "cylinder",
                ):
                    raise ValueError(
                        "coaxial relationship requires cone/cylinder surfaces"
                    )
                sides[surface.id] = surface
            if constraint.surface == constraint.reference:
                raise ValueError("coaxial constraint must reference distinct surfaces")
            edges.append((constraint.surface, constraint.reference))
        else:
            raise ValueError("joint fit input must be a supported relationship")
    if (set(planes) | set(sides)).intersection(rotational_planes):
        raise ValueError(
            "a rotational plane cannot also be perpendicular to the axis; rotational surfaces cannot also be coaxial members"
        )
    if not planes:
        raise ValueError(
            "joint solve currently requires at least one perpendicular plane"
        )
    # All sides must share the same axis line, not only parallel directions from
    # sharing a plane. Coaxial edges must connect every side to one anchor.
    root = next(iter(anchors))
    connected = {root}
    while True:
        expanded = connected | {
            key for edge in edges if connected.intersection(edge) for key in edge
        }
        if expanded == connected:
            break
        connected = expanded
    if connected != set(sides):
        raise ValueError(
            "all lateral surfaces must belong to one connected coaxial group"
        )
    # Put the first perpendicular side first for compatibility with pair reports.
    primary = next(
        c.lateral
        for c in (nodes[key] for key in node.constraints)
        if isinstance(c, Perpendicular)
    )
    ordered = [sides[primary], *(side for key, side in sides.items() if key != primary)]
    return ordered, [*planes.values(), *rotational_planes.values()]


class SelectionOverlap(ValueError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        self.diagnostic: dict[str, Any] = {
            "kind": "selection_overlap",
            "ids": sorted({vertex for pair in conflicts for vertex in pair["ids"]}),
            "conflicts": conflicts,
        }
        super().__init__(
            f"Fit selections overlap at {len(self.diagnostic['ids'])} vertices; inspect the highlighted overlaps and adjust fit inputs, growth barriers or seeds"
        )


class StaleGraph(ValueError):
    """The current graph changed while a client or worker was using it."""


def workspace_reference_sha256(workspace: NozzleWorkspace) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "selection": workspace.data.selection,
                "model": workspace.model_sha256,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def directed_axis_result(
    node: AxisDefinition,
    *,
    parameters: list[float] | None = None,
    first_point: dict[str, Any] | None = None,
    second_point: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve an explicitly directed axis without assuming it has a usable Z slope."""
    if node.source_points is not None:
        if first_point is None or second_point is None:
            raise ValueError(f"{node.label}: both source points must be evaluated")
        point = np.asarray(first_point["point_display"], dtype=float)
        other = np.asarray(second_point["point_display"], dtype=float)
        direction = other - point
        length = float(np.linalg.norm(direction))
        if not np.isfinite(length) or length <= 1e-12:
            raise ValueError(f"{node.label}: source points must not coincide")
        direction /= length
        legacy_parameters = None
    else:
        if parameters is None:
            raise ValueError(f"{node.label}: axis initializer result is unavailable")
        values = np.asarray(parameters, dtype=float)
        if values.ndim != 1 or len(values) < 4 or not np.isfinite(values).all():
            raise ValueError(f"{node.label}: axis initializer must be finite")
        direction = np.asarray([values[2], values[3], 1.0], dtype=float)
        direction /= np.linalg.norm(direction)
        point = np.asarray([values[0], values[1], 0.0], dtype=float)
        legacy_parameters = values.tolist()
    if node.direction_reversed:
        direction = -direction
    return {
        "source_fit": node.source_fit,
        "source_points": list(node.source_points) if node.source_points else None,
        "parameters": legacy_parameters,
        "axis_display": direction.tolist(),
        "point_display": point.tolist(),
        "direction_reversed": node.direction_reversed,
    }


def reference_plane_result(
    axis_result: dict[str, Any], plane: PlaneDefinition
) -> dict[str, Any]:
    axis = np.asarray(axis_result["axis_display"], dtype=float)
    axis /= np.linalg.norm(axis)
    basis = np.eye(3)[int(np.argmin(np.abs(axis)))]
    u = np.cross(axis, basis)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    anchor = np.asarray(axis_result["point_display"], dtype=float)
    if plane.construction in ("contains_axis", "parallel_to_axis"):
        assert plane.initial_angle_degrees is not None
        angle = np.radians(plane.initial_angle_degrees)
        basis_u = axis
        basis_v = np.cos(angle) * u + np.sin(angle) * v
        normal = np.cross(basis_u, basis_v)
        point = anchor + plane.offset * normal
    else:
        basis_u = u
        basis_v = v
        normal = axis
        point = anchor + plane.offset * axis
    return {
        "axis_display": axis.tolist(),
        "basis_u_display": basis_u.tolist(),
        "basis_v_display": basis_v.tolist(),
        "point_display": point.tolist(),
        "radial_display": basis_v.tolist(),
        "normal_display": normal.tolist(),
        "plane_equation": [*normal.tolist(), float(normal @ point)],
        "angle_degrees": plane.initial_angle_degrees,
        "offset": plane.offset,
        "construction": plane.construction,
    }


def scale_result(
    node: ScaleDefinition,
    resolved: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Solve one uniform scale from known point-to-point distances."""
    observations: list[dict[str, Any]] = []
    measured: list[float] = []
    known: list[float] = []
    weights: list[float] = []
    for distance in node.distances:
        first = np.asarray(resolved[distance.first_point]["point_display"], dtype=float)
        second = np.asarray(
            resolved[distance.second_point]["point_display"], dtype=float
        )
        value = float(np.linalg.norm(second - first))
        if not np.isfinite(value) or value <= 1e-12:
            raise ValueError(
                f"{node.label}: scale points {distance.first_point!r} and {distance.second_point!r} coincide"
            )
        measured.append(value)
        known.append(distance.known_distance)
        weights.append(distance.weight)
    measured_array = np.asarray(measured)
    known_array = np.asarray(known)
    weight_array = np.asarray(weights)
    denominator = float(weight_array @ measured_array**2)
    scale = float(weight_array @ (measured_array * known_array) / denominator)
    residuals = scale * measured_array - known_array
    for distance, measured_value, residual in zip(
        node.distances, measured, residuals, strict=True
    ):
        observations.append(
            {
                **distance.model_dump(),
                "measured_distance": measured_value,
                "scaled_distance": scale * measured_value,
                "residual": float(residual),
            }
        )

    return {
        "format": "scansor-output-scale-v1",
        "scale": scale,
        "observations": observations,
        "weighted_rms": float(
            np.sqrt(weight_array @ residuals**2 / float(weight_array.sum()))
        ),
        "max_abs_residual": float(np.max(np.abs(residuals))),
    }


def reference_direction(result: dict[str, Any], label: str) -> np.ndarray:
    direction = result.get("normal_display", result.get("axis_display"))
    if direction is None:
        plane = result.get("plane_equation", result.get("parameters"))
        direction = plane[:3] if plane is not None else None
    value = np.asarray(direction, dtype=float)
    if value.shape != (3,) or not np.isfinite(value).all():
        raise ValueError(f"{label}: reference has no usable direction")
    length = float(np.linalg.norm(value))
    if length <= 1e-12:
        raise ValueError(f"{label}: reference direction is zero")
    return value / length


def frame_result(
    node: FrameDefinition,
    resolved: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Construct an explicit right-handed frame from two mapped directions."""
    origin = np.asarray(resolved[node.origin_point]["point_display"], dtype=float)
    primary = reference_direction(resolved[node.primary_reference], node.label)
    secondary = reference_direction(resolved[node.secondary_reference], node.label)
    projected = secondary - primary * float(secondary @ primary)
    projected_length = float(np.linalg.norm(projected))
    if not np.isfinite(projected_length) or projected_length <= 1e-10:
        raise ValueError(
            f"{node.label}: primary and secondary references must not be parallel"
        )
    projected /= projected_length

    axes: dict[str, np.ndarray] = {}
    for mapping, direction in (
        (node.primary_output_axis, primary),
        (node.secondary_output_axis, projected),
    ):
        axes[mapping[-1].lower()] = direction * (-1 if mapping[0] == "-" else 1)
    missing = ({"x", "y", "z"} - axes.keys()).pop()
    if missing == "x":
        axes["x"] = np.cross(axes["y"], axes["z"])
    elif missing == "y":
        axes["y"] = np.cross(axes["z"], axes["x"])
    else:
        axes["z"] = np.cross(axes["x"], axes["y"])
    axes[missing] /= np.linalg.norm(axes[missing])
    rotation = np.vstack([axes["x"], axes["y"], axes["z"]])
    return {
        "format": "scansor-coordinate-frame-v1",
        "origin_display": origin.tolist(),
        "x_axis_display": axes["x"].tolist(),
        "y_axis_display": axes["y"].tolist(),
        "z_axis_display": axes["z"].tolist(),
        "rotation": rotation.tolist(),
        "primary_reference": node.primary_reference,
        "primary_output_axis": node.primary_output_axis,
        "secondary_reference": node.secondary_reference,
        "secondary_output_axis": node.secondary_output_axis,
        "reference_separation_degrees": float(
            np.degrees(np.arccos(np.clip(float(primary @ secondary), -1.0, 1.0)))
        ),
    }


def output_transform_result(
    node: TransformDefinition,
    frame: dict[str, Any],
    scale: dict[str, Any],
) -> dict[str, Any]:
    """Compose an applicable similarity transform from frame and scale features."""
    factor = float(scale["scale"])
    origin = np.asarray(frame["origin_display"], dtype=float)
    rotation = np.asarray(frame["rotation"], dtype=float)
    matrix = np.eye(4)
    matrix[:3, :3] = factor * rotation
    matrix[:3, 3] = -matrix[:3, :3] @ origin
    return {
        "format": "scansor-output-transform-v2",
        "frame": node.frame,
        "scale_feature": node.scale,
        "scale": factor,
        "origin_display": origin.tolist(),
        "x_axis_display": list(frame["x_axis_display"]),
        "y_axis_display": list(frame["y_axis_display"]),
        "z_axis_display": list(frame["z_axis_display"]),
        "matrix": matrix.tolist(),
        "scale_weighted_rms": float(scale["weighted_rms"]),
        "scale_max_abs_residual": float(scale["max_abs_residual"]),
        "scale_observation_count": len(scale["observations"]),
    }


def fit_plane_to_reference(
    workspace: NozzleWorkspace,
    surface: SurfaceFit,
    ids: list[VertexId],
    reference: dict[str, Any],
) -> dict[str, Any]:
    """Fit an offset while preserving an upstream reference plane's orientation."""
    if len(ids) < 3:
        raise ValueError(f"{surface.id}: select at least three plane vertices")
    points = workspace.local[ids]
    weights = workspace.data.weights[ids]
    normal = np.asarray(reference["normal_display"], dtype=float)
    normal /= np.linalg.norm(normal)
    offset = float(weights @ (points @ normal) / weights.sum())
    residuals = points @ normal - offset
    reference_offset = float(reference["plane_equation"][3])
    return {
        "kind": "plane",
        "ids": ids,
        "parameters": [*normal.tolist(), offset],
        "plane_equation": [*normal.tolist(), offset],
        "axial_domain": surface.axial_domain,
        "residuals": residuals.tolist(),
        "weighted_rms": float(np.sqrt(weights @ residuals**2 / float(weights.sum()))),
        "condition": 1.0,
        "reference_plane": surface.reference_plane,
        "reference_offset": reference_offset,
        "signed_relative_offset": offset - reference_offset,
    }


def fit_sphere_at_point(
    workspace: NozzleWorkspace,
    surface: SurfaceFit,
    ids: list[int],
    point: dict[str, Any],
) -> dict[str, Any]:
    """Fit only a sphere radius while retaining an explicit fixed center."""
    if len(ids) < 4:
        raise ValueError(f"{surface.id}: select at least four sphere vertices")
    center = np.asarray(point["point_display"], dtype=float)
    if center.shape != (3,) or not np.isfinite(center).all():
        raise ValueError(f"{surface.label}: point result is unavailable")
    weights = workspace.data.weights[ids]
    distances = np.linalg.norm(workspace.local[ids] - center, axis=1)
    radius = float(weights @ distances / float(weights.sum()))
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError(f"{surface.label}: sphere radius must be positive")
    residuals = distances - radius
    return {
        "kind": "sphere",
        "ids": ids,
        "parameters": [*center.tolist(), radius],
        "axial_domain": surface.axial_domain,
        "residuals": residuals.tolist(),
        "weighted_rms": float(np.sqrt(weights @ residuals**2 / float(weights.sum()))),
        "condition": 1.0,
        "point": surface.point,
    }


def fit_spheres_to_free_point(
    workspace: NozzleWorkspace,
    point_id: str,
    surfaces: list[SurfaceFit],
    ids: list[list[int]],
    initial_center: np.ndarray,
) -> dict[str, Any]:
    """Jointly fit one exact center and an independent radius per sphere."""
    if initial_center.shape != (3,) or not np.isfinite(initial_center).all():
        raise ValueError("free point initialization must be a finite XYZ value")
    points = [workspace.local[surface_ids] for surface_ids in ids]
    weights = [workspace.data.weights[surface_ids] for surface_ids in ids]
    if any(len(surface_points) < 4 for surface_points in points):
        raise ValueError("each point-bound sphere needs at least four observations")
    total_weight = sum(float(surface_weights.sum()) for surface_weights in weights)
    if not np.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("point-bound spheres need positive-area observations")
    normalized_weights = [surface_weights / total_weight for surface_weights in weights]
    radii = [
        float(surface_weights @ np.linalg.norm(surface_points - initial_center, axis=1))
        / float(surface_weights.sum())
        for surface_points, surface_weights in zip(points, weights, strict=True)
    ]
    parameters = np.asarray([*initial_center, *radii], dtype=float)
    condition = float("inf")
    for _ in range(60):
        residual_parts: list[np.ndarray] = []
        jacobian_parts: list[np.ndarray] = []
        for index, surface_points in enumerate(points):
            delta = surface_points - parameters[:3]
            distance = np.linalg.norm(delta, axis=1)
            if np.any(distance <= np.finfo(float).eps):
                raise ValueError(
                    "sphere observations cannot coincide with their center"
                )
            residual_parts.append(distance - parameters[3 + index])
            jacobian = np.zeros((len(surface_points), len(parameters)))
            jacobian[:, :3] = -delta / distance[:, None]
            jacobian[:, 3 + index] = -1.0
            jacobian_parts.append(jacobian)
        residual = np.concatenate(residual_parts)
        jacobian = np.concatenate(jacobian_parts)
        combined_weights = np.concatenate(normalized_weights)
        objective = float(combined_weights @ residual**2)
        normal = jacobian.T @ (combined_weights[:, None] * jacobian)
        condition = float(np.linalg.cond(normal))
        if not np.isfinite(condition) or condition > 1e12:
            raise ValueError(
                "point-bound sphere geometry is ill-conditioned; select a wider curved patch"
            )
        gradient = jacobian.T @ (combined_weights * residual)
        step = np.linalg.solve(normal, -gradient)
        scale = max(1.0, *parameters[3:])
        if np.max(np.abs(step)) <= 1e-10 * scale:
            break
        for power in range(25):
            candidate = parameters + step * 2.0**-power
            if np.any(candidate[3:] <= 0):
                continue
            candidate_residual = np.concatenate(
                [
                    np.linalg.norm(surface_points - candidate[:3], axis=1)
                    - candidate[3 + index]
                    for index, surface_points in enumerate(points)
                ]
            )
            if float(combined_weights @ candidate_residual**2) < objective:
                parameters = candidate
                break
        else:
            if np.linalg.norm(gradient, ord=np.inf) <= 1e-10 * scale:
                break
            raise ValueError("point-bound sphere fit failed to decrease objective")
    adjusted: dict[str, dict[str, Any]] = {}
    for index, (surface, surface_points, surface_ids, surface_weights) in enumerate(
        zip(surfaces, points, ids, weights, strict=True)
    ):
        radius = float(parameters[3 + index])
        residuals = np.linalg.norm(surface_points - parameters[:3], axis=1) - radius
        adjusted[surface.id] = {
            "kind": "sphere",
            "ids": surface_ids,
            "parameters": [*parameters[:3].tolist(), radius],
            "axial_domain": surface.axial_domain,
            "residuals": residuals.tolist(),
            "weighted_rms": float(
                np.sqrt(surface_weights @ residuals**2 / float(surface_weights.sum()))
            ),
            "condition": condition,
            "point": point_id,
            "resolved_by": "connected_fits",
        }
    return {
        "format": "scansor-shared-sphere-center-v1",
        "point": {
            "point_display": parameters[:3].tolist(),
            "coordinates": parameters[:3].tolist(),
            "resolved_by": "connected_fits",
        },
        "surfaces": adjusted,
    }


def fit_equal_radii(
    workspace: NozzleWorkspace,
    surfaces: list[SurfaceFit],
    results: list[dict[str, Any]],
    ids: list[list[int]],
) -> dict[str, Any]:
    """Fit one exact radius while retaining each surface's position and orientation."""
    radial_observations: list[np.ndarray] = []
    observation_weights: list[np.ndarray] = []
    for surface, result, surface_ids in zip(surfaces, results, ids, strict=True):
        parameters = np.asarray(result["parameters"], dtype=float)
        if not np.isfinite(parameters).all():
            raise ValueError(f"{surface.label}: fit result is unavailable")
        if surface.kind == "cylinder" and parameters.shape == (7,):
            direction = np.asarray([parameters[2], parameters[3], 1.0])
            direction /= np.linalg.norm(direction)
            point = np.asarray([parameters[0], parameters[1], 0.0])
            offset = workspace.local[surface_ids] - point
            axial = offset @ direction
            radial = offset - axial[:, None] * direction
            radial_observations.append(np.linalg.norm(radial, axis=1))
        elif surface.kind == "sphere" and parameters.shape == (4,):
            radial_observations.append(
                np.linalg.norm(workspace.local[surface_ids] - parameters[:3], axis=1)
            )
        else:
            raise ValueError("all-equal radii require sphere or cylinder fits")
        observation_weights.append(workspace.data.weights[surface_ids])
    total_weight = sum(float(weights.sum()) for weights in observation_weights)
    if not np.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("all-equal radii need positive-area observations")
    radius = (
        sum(
            float(weights @ radial)
            for weights, radial in zip(
                observation_weights, radial_observations, strict=True
            )
        )
        / total_weight
    )
    adjusted: dict[str, dict[str, Any]] = {}
    for surface, result, surface_ids, radial, weights in zip(
        surfaces,
        results,
        ids,
        radial_observations,
        observation_weights,
        strict=True,
    ):
        parameters = list(result["parameters"])
        parameters[4 if surface.kind == "cylinder" else 3] = radius
        residuals = radial - radius
        adjusted[surface.id] = {
            **deepcopy(result),
            "ids": surface_ids,
            "parameters": parameters,
            "residuals": residuals.tolist(),
            "weighted_rms": float(
                np.sqrt(weights @ residuals**2 / float(weights.sum()))
            ),
            "resolved_by": "equal_radii",
        }
    return {
        "format": "scansor-equal-radii-v2",
        "measurement": "radius",
        "value": radius,
        "surfaces": adjusted,
    }


def fit_plane_relationships(
    workspace: NozzleWorkspace,
    relationships: list[PlaneRelationship],
    surfaces: list[SurfaceFit],
    results: list[dict[str, Any]],
    ids: list[list[int]],
) -> dict[str, Any]:
    """Solve a connected set of exact coincident/parallel plane constraints."""
    parent = {surface.id: surface.id for surface in surfaces}

    def root(surface_id: str) -> str:
        while parent[surface_id] != surface_id:
            parent[surface_id] = parent[parent[surface_id]]
            surface_id = parent[surface_id]
        return surface_id

    def union(left: str, right: str) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for relationship in relationships:
        if relationship.relation != "coincident":
            continue
        anchor = relationship.surfaces[0]
        for surface_id in relationship.surfaces[1:]:
            union(anchor, surface_id)

    groups: dict[str, list[int]] = {}
    for index, surface in enumerate(surfaces):
        groups.setdefault(root(surface.id), []).append(index)

    covariance = np.zeros((3, 3), dtype=float)
    total_weight = 0.0
    for indices in groups.values():
        group_points = np.concatenate(
            [workspace.local[ids[index]] for index in indices]
        )
        group_weights = np.concatenate(
            [workspace.data.weights[ids[index]] for index in indices]
        )
        weight = float(group_weights.sum())
        if not np.isfinite(weight) or weight <= 0:
            raise ValueError("plane relationships need positive-area observations")
        centroid = group_weights @ group_points / weight
        centered = group_points - centroid
        covariance += (centered * group_weights[:, None]).T @ centered
        total_weight += weight
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    normal = eigenvectors[:, int(np.argmin(eigenvalues))]
    reference_normals = [
        np.asarray(result["plane_equation"][:3], dtype=float)
        for result in results
        if "plane_equation" in result
    ]
    bound_normals = [
        np.asarray(result["plane_equation"][:3], dtype=float)
        for surface, result in zip(surfaces, results, strict=True)
        if (surface.axis is not None or surface.reference_plane is not None)
        and "plane_equation" in result
    ]
    if bound_normals:
        normal = sum(
            candidate if candidate @ bound_normals[0] >= 0 else -candidate
            for candidate in bound_normals
        )
        normal /= np.linalg.norm(normal)
    elif reference_normals and normal @ sum(reference_normals) < 0:
        normal = -normal

    adjusted: dict[str, dict[str, Any]] = {}
    group_offsets: dict[str, float] = {}
    for group_id, indices in groups.items():
        bound_offsets = [
            float(results[index]["plane_equation"][3])
            for index in indices
            if (
                surfaces[index].axis is not None
                or surfaces[index].reference_plane is not None
            )
            and "plane_equation" in results[index]
        ]
        if bound_offsets:
            group_offsets[group_id] = float(np.mean(bound_offsets))
            continue
        group_points = np.concatenate(
            [workspace.local[ids[index]] for index in indices]
        )
        group_weights = np.concatenate(
            [workspace.data.weights[ids[index]] for index in indices]
        )
        group_offsets[group_id] = float(
            group_weights @ (group_points @ normal) / group_weights.sum()
        )
    for surface, result, surface_ids in zip(surfaces, results, ids, strict=True):
        points = workspace.local[surface_ids]
        weights = workspace.data.weights[surface_ids]
        offset = group_offsets[root(surface.id)]
        residuals = points @ normal - offset
        adjusted[surface.id] = {
            **deepcopy(result),
            "kind": "plane",
            "ids": surface_ids,
            "parameters": [*normal.tolist(), offset],
            "plane_equation": [*normal.tolist(), offset],
            "residuals": residuals.tolist(),
            "weighted_rms": float(
                np.sqrt(weights @ residuals**2 / float(weights.sum()))
            ),
            "resolved_by": "plane_relationship",
        }
    return {
        "format": "scansor-plane-relationships-v1",
        "relationships": [relationship.id for relationship in relationships],
        "normal_display": normal.tolist(),
        "surfaces": adjusted,
        "weighted_rms": float(
            np.sqrt(
                sum(
                    workspace.data.weights[ids[index]]
                    @ np.asarray(adjusted[surface.id]["residuals"]) ** 2
                    for index, surface in enumerate(surfaces)
                )
                / total_weight
            )
        ),
    }


def equality_measurements(
    relationship: EqualQuantities,
) -> tuple[SurfaceRadius, PlaneDistance]:
    measurements = (relationship.left, relationship.right)
    radii = [value for value in measurements if isinstance(value, SurfaceRadius)]
    distances = [value for value in measurements if isinstance(value, PlaneDistance)]
    if len(radii) != 1 or len(distances) != 1:
        raise ValueError(
            "the bounded equality relationship requires one surface radius and one plane distance"
        )
    return radii[0], distances[0]


def constrained_mirror_radii(
    factors: list[Feature], nodes: dict[str, Feature]
) -> tuple[str | None, ...]:
    """Lower complete mirror/parallel/equality clusters to shared radii."""
    mirrors = [factor for factor in factors if isinstance(factor, MirrorSymmetry)]
    parallels = [factor for factor in factors if isinstance(factor, ParallelToPlane)]
    equalities = [factor for factor in factors if isinstance(factor, EqualQuantities)]
    used_parallel: set[str] = set()
    used_equal: set[str] = set()
    radius_sources: list[str | None] = []
    for mirror in mirrors:
        matching_parallel = [
            relationship
            for relationship in parallels
            if relationship.reference_plane == mirror.plane
            and relationship.surface in mirror.surfaces
        ]
        matching_equal: list[tuple[EqualQuantities, SurfaceRadius]] = []
        for relationship in equalities:
            radius, distance = equality_measurements(relationship)
            if (
                distance.reference_plane == mirror.plane
                and distance.surface in mirror.surfaces
                and any(
                    parallel.surface == distance.surface
                    for parallel in matching_parallel
                )
            ):
                matching_equal.append((relationship, radius))
        if not matching_parallel and not matching_equal:
            radius_sources.append(None)
            continue
        if len(matching_parallel) != 1 or len(matching_equal) != 1:
            raise ValueError(
                "a constrained mirror pair requires exactly one parallel relationship and one radius-to-plane-distance equality"
            )
        parallel = matching_parallel[0]
        equality, radius = matching_equal[0]
        if parallel.surface != equality_measurements(equality)[1].surface:
            raise ValueError(
                "parallel and equality relationships must reference the same mirror member"
            )
        cylinder = nodes[radius.surface]
        if not isinstance(cylinder, SurfaceFit) or cylinder.kind != "cylinder":
            raise ValueError("radius equality currently requires a cylinder fit")
        used_parallel.add(parallel.id)
        used_equal.add(equality.id)
        radius_sources.append(radius.surface)
    if used_parallel != {
        relationship.id for relationship in parallels
    } or used_equal != {relationship.id for relationship in equalities}:
        raise ValueError(
            "parallel and equality relationships must form a complete active mirror cluster"
        )
    return tuple(radius_sources)


@final
class FeatureGraph:
    def __init__(self, workspace: NozzleWorkspace, recipe: Recipe) -> None:
        self.workspace = workspace
        # Bind the fixture adapter's frame/initialization as well as the raw mesh.
        self.reference_sha256 = workspace_reference_sha256(workspace)
        self.lock = RLock()
        self._recipe = self.validate(recipe)
        self._states: dict[str, str] = dict.fromkeys(
            (n.id for n in recipe.nodes), "unevaluated"
        )
        self._results: dict[str, SessionFit] = {}
        self._connected_solves: dict[str, SessionFit] = {}
        self._connected_point_solves: dict[str, dict[str, Any]] = {}
        self._derived: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._epoch = 0  # In-flight invalidation only; no retained previous states.

    def validate(self, recipe: Recipe) -> Recipe:
        nodes = {node.id: node for node in recipe.nodes}
        positions = {node.id: index for index, node in enumerate(recipe.nodes)}
        if len(nodes) != len(recipe.nodes):
            raise ValueError("duplicate feature ID")
        if recipe.output not in nodes:
            raise ValueError("output must reference an action")
        seen: set[str] = set()
        for node in recipe.nodes:
            for ref in dependencies(node):
                if ref not in nodes:
                    raise ValueError(f"missing feature input {ref!r}")
                if ref not in seen:
                    raise ValueError(
                        f"action {node.label!r} references {nodes[ref].label!r}, which must appear earlier (forward reference or cycle)"
                    )
            seen.add(node.id)
        for node in nodes.values():
            if isinstance(node, Source):
                if (
                    node.source_sha256 != self.workspace.default.source_sha256
                    or node.reference_sha256 != self.reference_sha256
                ):
                    raise ValueError("source or reference binding mismatch")
            elif isinstance(node, Selection):
                if not isinstance(nodes[node.source], Source):
                    raise ValueError("selection input must be a source")
                if node.ids != sorted(set(node.ids)) or (
                    node.ids and node.ids[-1] >= len(self.workspace.local)
                ):
                    raise ValueError(
                        "selection IDs must be unique, sorted, and in range"
                    )
                if node.ids and np.any(self.workspace.data.weights[node.ids] <= 0):
                    raise ValueError("selection requires positive incident area")
            elif isinstance(node, SurfaceFit):
                if len(set(node.selections)) != len(node.selections):
                    raise ValueError("fit selection references must be unique")
                sources = {
                    selection_source(nodes[ref], nodes) for ref in node.selections
                }
                if len(sources) != 1:
                    raise ValueError("fit selections must share a source")
                lo, hi = node.axial_domain
                if not np.isfinite([lo, hi]).all() or lo >= hi:
                    raise ValueError("invalid axial domain")
                if node.axis is not None:
                    axis = nodes[node.axis]
                    if not isinstance(axis, AxisDefinition):
                        raise ValueError("axis-bound fit requires an explicit axis")
                    if axis.source_points is not None:
                        raise ValueError(
                            "point-pair axes are currently datum-only and cannot drive a surface fit"
                        )
                if node.point is not None and not isinstance(
                    nodes[node.point], PointDefinition
                ):
                    raise ValueError("point-bound fit requires an explicit point")
                if node.reference_plane is not None and not isinstance(
                    nodes[node.reference_plane], PlaneDefinition
                ):
                    raise ValueError(
                        "plane-bound fit requires an explicit reference plane"
                    )
                for selection_id in node.selections:
                    applied = nodes[selection_id]
                    if not isinstance(applied, RegionSelection):
                        continue
                    target_axis = selection_frame_axis(
                        applied.axial_plane, applied.clock_plane, nodes
                    )
                    axis = nodes[target_axis]
                    if (
                        fit_axis(node, nodes) == target_axis
                        and isinstance(axis, AxisDefinition)
                        and axis.source_fit is None
                    ):
                        raise ValueError(
                            "fit an applied region standalone before using it to initialize its target axis"
                        )
            elif isinstance(node, Growth):
                fitted = nodes[node.seed_fit]
                if not isinstance(fitted, SurfaceFit) or not is_standalone_fit(fitted):
                    raise ValueError("growth input must be an earlier standalone fit")
                source = selection_source(node, nodes)
                for barrier in node.barriers:
                    if selection_source(nodes[barrier], nodes) != source:
                        raise ValueError("growth barrier belongs to another source")
            elif isinstance(node, SelectionRegion):
                selected = nodes[node.selection]
                fitted = nodes[node.fit]
                if not isinstance(
                    selected, (Selection, Growth, RegionSelection, ReuseSelection)
                ):
                    raise ValueError("a reusable region requires an earlier selection")
                if (
                    not isinstance(fitted, SurfaceFit)
                    or fitted.kind not in ("cylinder", "plane")
                    or node.selection not in fitted.selections
                ):
                    raise ValueError(
                        "a reusable region requires a cylinder or plane fit using its selection"
                    )
                axis_id = selection_frame_axis(
                    node.axial_plane, node.clock_plane, nodes
                )
                if fitted.kind == "cylinder" and fit_axis(fitted, nodes) != axis_id:
                    raise ValueError(
                        "a cylinder selection region must use its datum-frame axis"
                    )
                if selection_source(selected, nodes) != selection_source(
                    nodes[fitted.selections[0]], nodes
                ):
                    raise ValueError("selection region inputs must share one source")
            elif isinstance(node, RegionSelection):
                region = nodes[node.region]
                source = nodes[node.source]
                if not isinstance(region, SelectionRegion):
                    raise ValueError("region application requires a reusable region")
                if not isinstance(source, Source):
                    raise ValueError("region application requires a source mesh")
                _ = selection_frame_axis(node.axial_plane, node.clock_plane, nodes)
                region_source = selection_source(nodes[region.selection], nodes)
                if node.source != region_source:
                    raise ValueError(
                        "region application currently requires the region's source mesh"
                    )
            elif isinstance(node, FeatureReuse):
                if len(set(node.fits)) != len(node.fits):
                    raise ValueError("a reuse feature cannot contain duplicate fits")
                if len(set(node.target_selections)) != len(node.target_selections):
                    raise ValueError("a reuse feature cannot contain duplicate targets")
                if node.reference_selection in node.target_selections:
                    raise ValueError(
                        "reuse reference and target selections must be different"
                    )
                fits = [nodes[key] for key in node.fits]
                if any(
                    not isinstance(fit, SurfaceFit)
                    or fit.kind not in ("cylinder", "plane")
                    for fit in fits
                ):
                    raise ValueError("reuse currently supports cylinder and plane fits")
                for selection_id in (
                    node.reference_selection,
                    *node.target_selections,
                ):
                    if not isinstance(
                        nodes[selection_id],
                        (Selection, Growth, RegionSelection, ReuseSelection),
                    ):
                        raise ValueError("reuse matching inputs must be selections")
                sources = {
                    selection_source(nodes[node.reference_selection], nodes),
                    *(
                        selection_source(nodes[selection_id], nodes)
                        for selection_id in node.target_selections
                    ),
                    *(
                        selection_source(nodes[selection_id], nodes)
                        for fit in fits
                        if isinstance(fit, SurfaceFit)
                        for selection_id in fit.selections
                    ),
                }
                if len(sources) != 1:
                    raise ValueError(
                        "reuse currently requires one source mesh for fits and matching selections"
                    )
                available = {
                    key: value
                    for key, value in nodes.items()
                    if positions[key] < positions[node.id]
                }
                expected = discover_reuse_lineage(node.fits, available)
                if node.lineage != expected:
                    raise ValueError(
                        "reuse lineage must contain the selected fits, their inputs, and enclosed relationships in action order"
                    )
            elif isinstance(node, ReuseSelection):
                reuse = nodes[node.reuse]
                fitted = nodes[node.fit]
                if not isinstance(reuse, FeatureReuse):
                    raise ValueError("a reused selection requires a reuse feature")
                if node.fit not in reuse.fits or not isinstance(fitted, SurfaceFit):
                    raise ValueError("a reused selection must belong to a reused fit")
                if node.target_selection not in reuse.target_selections:
                    raise ValueError(
                        "a reused selection must name one target of its reuse feature"
                    )
                if node.source_selection not in fitted.selections:
                    raise ValueError(
                        "a reused selection must reference one input of its source fit"
                    )
            elif isinstance(node, Perpendicular):
                lateral, plane = nodes[node.lateral], nodes[node.plane]
                if (
                    not isinstance(lateral, SurfaceFit)
                    or lateral.kind not in ("cone", "cylinder")
                    or not is_standalone_fit(lateral)
                    or not isinstance(plane, SurfaceFit)
                    or plane.kind != "plane"
                    or not is_standalone_fit(plane)
                ):
                    raise ValueError(
                        "legacy relationship needs standalone cone/cylinder and plane fits"
                    )
            elif isinstance(node, RotationalSymmetry):
                axis = nodes[node.axis]
                if (
                    not isinstance(axis, SurfaceFit)
                    or axis.kind
                    not in (
                        "cone",
                        "cylinder",
                    )
                    or not is_standalone_fit(axis)
                ):
                    raise ValueError(
                        "rotational symmetry requires a cone/cylinder axis fit"
                    )
                if len(set(node.planes)) != 3 or any(
                    not isinstance(nodes[ref], SurfaceFit)
                    or not is_standalone_fit(cast(SurfaceFit, nodes[ref]))
                    for ref in node.planes
                ):
                    raise ValueError(
                        "rotational symmetry requires three distinct same-type surface fits"
                    )
                if len({cast(SurfaceFit, nodes[ref]).kind for ref in node.planes}) != 1:
                    raise ValueError(
                        "rotational symmetry requires matching fit types; existing fits are not converted"
                    )
                if any(
                    cast(SurfaceFit, nodes[ref]).kind
                    not in ("cone", "cylinder", "plane")
                    for ref in node.planes
                ):
                    raise ValueError(
                        "rotational symmetry currently supports cone, cylinder, or plane fits"
                    )
            elif isinstance(node, Coaxial):
                pair = [nodes[node.surface], nodes[node.reference]]
                if node.surface == node.reference or any(
                    not isinstance(n, SurfaceFit)
                    or n.kind not in ("cone", "cylinder")
                    or not is_standalone_fit(n)
                    for n in pair
                ):
                    raise ValueError(
                        "legacy coaxial relationship requires two distinct standalone cone/cylinder fits"
                    )
            elif isinstance(node, AxisDefinition):
                if node.source_fit is not None:
                    source_fit = nodes[node.source_fit]
                    if (
                        not isinstance(source_fit, SurfaceFit)
                        or source_fit.kind not in ("cone", "cylinder")
                        or not is_standalone_fit(source_fit)
                    ):
                        raise ValueError(
                            "an axis fit initializer must be an earlier standalone cone or cylinder"
                        )
                elif node.source_points is not None:
                    points = [nodes[ref] for ref in node.source_points]
                    if any(not isinstance(point, PointDefinition) for point in points):
                        raise ValueError(
                            "a point-pair axis requires two earlier point datums"
                        )
                else:
                    assert node.initial_parameters is not None
                    if not np.isfinite(node.initial_parameters).all():
                        raise ValueError("manual axis initialization must be finite")
            elif isinstance(node, PointDefinition):
                if node.source_fit is not None:
                    source_fit = nodes[node.source_fit]
                    if (
                        not isinstance(source_fit, SurfaceFit)
                        or source_fit.kind != "sphere"
                        or not is_standalone_fit(source_fit)
                    ):
                        raise ValueError(
                            "a point fit initializer must be an earlier standalone sphere"
                        )
                else:
                    assert node.initial_coordinates is not None
                    if not np.isfinite(node.initial_coordinates).all():
                        raise ValueError("manual point initialization must be finite")
            elif isinstance(node, ScaleDefinition):
                pairs = [
                    frozenset((distance.first_point, distance.second_point))
                    for distance in node.distances
                ]
                if len(pairs) != len(set(pairs)):
                    raise ValueError("scale distance point pairs must be unique")
                for distance in node.distances:
                    if not isinstance(
                        nodes[distance.first_point], PointDefinition
                    ) or not isinstance(nodes[distance.second_point], PointDefinition):
                        raise ValueError("scale distances require earlier point datums")
            elif isinstance(node, FrameDefinition):
                if not isinstance(nodes[node.origin_point], PointDefinition):
                    raise ValueError("frame origin requires a point datum")
                for reference_id in (
                    node.primary_reference,
                    node.secondary_reference,
                ):
                    reference = nodes[reference_id]
                    if not isinstance(
                        reference, (AxisDefinition, PlaneDefinition)
                    ) and not (
                        isinstance(reference, SurfaceFit) and reference.kind == "plane"
                    ):
                        raise ValueError(
                            "frame directions require axes, reference planes, or plane fits"
                        )
            elif isinstance(node, TransformDefinition):
                if not isinstance(nodes[node.frame], FrameDefinition):
                    raise ValueError("transform requires an earlier frame feature")
                if not isinstance(nodes[node.scale], ScaleDefinition):
                    raise ValueError("transform requires an earlier scale feature")
            elif isinstance(node, PlaneDefinition):
                if not isinstance(nodes[node.axis], AxisDefinition):
                    raise ValueError("reference plane requires an explicit axis")
            elif isinstance(node, MirrorSymmetry):
                plane = nodes[node.plane]
                if not isinstance(plane, PlaneDefinition):
                    raise ValueError("mirror symmetry requires a reference plane")
                if plane.construction != "contains_axis":
                    raise ValueError(
                        "mirror symmetry on a shared axis requires a reference plane containing that axis"
                    )
                if len(set(node.surfaces)) != 2:
                    raise ValueError(
                        "mirror symmetry requires two distinct same-type surface fits"
                    )
                surfaces = [nodes[ref] for ref in node.surfaces]
                if any(
                    not isinstance(surface, SurfaceFit)
                    or not is_standalone_fit(surface)
                    or surface.kind not in ("cone", "cylinder", "plane")
                    for surface in surfaces
                ):
                    raise ValueError(
                        "mirror symmetry requires two supported standalone surface fits"
                    )
                if len({cast(SurfaceFit, surface).kind for surface in surfaces}) != 1:
                    raise ValueError(
                        "mirror symmetry requires matching fit types; existing fits are not converted"
                    )
            elif isinstance(node, ParallelToPlane):
                surface = nodes[node.surface]
                reference = nodes[node.reference_plane]
                if (
                    not isinstance(surface, SurfaceFit)
                    or surface.kind != "plane"
                    or not is_standalone_fit(surface)
                ):
                    raise ValueError(
                        "parallel relationship requires a standalone plane fit"
                    )
                if not isinstance(reference, PlaneDefinition):
                    raise ValueError(
                        "parallel relationship requires an explicit reference plane"
                    )
            elif isinstance(node, EqualQuantities):
                radius, distance = equality_measurements(node)
                cylinder = nodes[radius.surface]
                surface = nodes[distance.surface]
                reference = nodes[distance.reference_plane]
                if (
                    not isinstance(cylinder, SurfaceFit)
                    or cylinder.kind != "cylinder"
                    or cylinder.axis is None
                ):
                    raise ValueError(
                        "radius measurement requires an axis-bound cylinder fit"
                    )
                if (
                    not isinstance(surface, SurfaceFit)
                    or surface.kind != "plane"
                    or not is_standalone_fit(surface)
                ):
                    raise ValueError(
                        "plane-distance measurement requires a standalone plane fit"
                    )
                if not isinstance(reference, PlaneDefinition):
                    raise ValueError(
                        "plane-distance measurement requires an explicit reference plane"
                    )
                if cylinder.axis != reference.axis:
                    raise ValueError(
                        "radius and plane-distance measurements must use the same axis"
                    )
            elif isinstance(node, EqualRadii):
                if len(set(node.surfaces)) != len(node.surfaces):
                    raise ValueError("all-equal radius inputs must be unique")
                surfaces = [nodes[ref] for ref in node.surfaces]
                if any(
                    not isinstance(surface, SurfaceFit)
                    or surface.kind not in ("cylinder", "sphere")
                    for surface in surfaces
                ):
                    raise ValueError("all-equal radii require sphere or cylinder fits")
                overlapping = [
                    candidate
                    for candidate in nodes.values()
                    if isinstance(candidate, EqualRadii)
                    and positions[candidate.id] < positions[node.id]
                    and set(candidate.surfaces).intersection(node.surfaces)
                ]
                if overlapping:
                    raise ValueError(
                        "a fit may belong to only one all-equal radius relationship"
                    )
            elif isinstance(node, PlaneRelationship):
                if len(set(node.surfaces)) != len(node.surfaces):
                    raise ValueError("plane relationship inputs must be unique")
                surfaces = [nodes[ref] for ref in node.surfaces]
                if any(
                    not isinstance(surface, SurfaceFit) or surface.kind != "plane"
                    for surface in surfaces
                ):
                    raise ValueError("plane relationships require plane fits")
            elif isinstance(node, AxisSolve):
                axis = nodes[node.axis]
                if not isinstance(axis, AxisDefinition):
                    raise ValueError("axis solve requires an explicit axis")
                if len(set(node.factors)) != len(node.factors):
                    raise ValueError("axis solve factor references must be unique")
                factors = [nodes[ref] for ref in node.factors]
                if any(
                    not (
                        isinstance(factor, SurfaceFit)
                        and fit_axis(factor, nodes) == node.axis
                        and factor.kind in ("cone", "cylinder", "plane")
                    )
                    and not (
                        isinstance(factor, MirrorSymmetry)
                        and isinstance(nodes[factor.plane], PlaneDefinition)
                        and cast(PlaneDefinition, nodes[factor.plane]).axis == node.axis
                    )
                    and not (
                        isinstance(factor, ParallelToPlane)
                        and isinstance(nodes[factor.reference_plane], PlaneDefinition)
                        and cast(PlaneDefinition, nodes[factor.reference_plane]).axis
                        == node.axis
                    )
                    and not (
                        isinstance(factor, EqualQuantities)
                        and cast(
                            SurfaceFit,
                            nodes[equality_measurements(factor)[0].surface],
                        ).axis
                        == node.axis
                        and cast(
                            PlaneDefinition,
                            nodes[equality_measurements(factor)[1].reference_plane],
                        ).axis
                        == node.axis
                    )
                    for factor in factors
                ):
                    raise ValueError(
                        "axis solve inputs must be bound fits or relationships on its axis"
                    )
                typed_factors = [
                    factor for factor in factors if isinstance(factor, SurfaceFit)
                ]
                mirror_factors = [
                    factor for factor in factors if isinstance(factor, MirrorSymmetry)
                ]
                if len({factor.plane for factor in mirror_factors}) != len(
                    mirror_factors
                ):
                    raise ValueError(
                        "each mirror plane may drive only one pair in a joint"
                    )
                if not any(
                    factor.kind in ("cone", "cylinder") for factor in typed_factors
                ):
                    raise ValueError(
                        "free axis solve requires a cone or cylinder factor"
                    )
                mirror_radii = constrained_mirror_radii(factors, nodes)
                if (
                    not any(factor.kind == "plane" for factor in typed_factors)
                    and not mirror_factors
                ):
                    raise ValueError(
                        "free axis solve requires a plane factor or mirror relationship"
                    )
                for radius_surface in (value for value in mirror_radii if value):
                    if radius_surface not in node.factors:
                        raise ValueError(
                            "a radius equality requires its cylinder fit active in the solve"
                        )
                sources = {
                    selection_source(nodes[ref], nodes)
                    for factor in typed_factors
                    for ref in factor.selections
                }
                for factor in factors:
                    if isinstance(factor, MirrorSymmetry):
                        sources.update(
                            selection_source(nodes[ref], nodes)
                            for surface_ref in factor.surfaces
                            for ref in cast(SurfaceFit, nodes[surface_ref]).selections
                        )
                if len(sources) != 1:
                    raise ValueError("axis solve factors must share one source")
            else:
                sides, planes = joint_surfaces(node, nodes)
                sources = {
                    selection_source(nodes[ref], nodes)
                    for surface in [*sides, *planes]
                    for ref in surface.selections
                }
                if len(sources) != 1:
                    raise ValueError("joint fit needs selections on the same source")
        return recipe.model_copy(deep=True)

    def _token(self) -> str:
        # Content token detects conflicting client edits without an edit history.
        return hashlib.sha256(self._recipe.model_dump_json().encode()).hexdigest()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            resolved = deepcopy({**self._derived, **self._results})
            for axis_id, solve in self._connected_solves.items():
                axis_node = next(
                    node
                    for node in self._recipe.nodes
                    if isinstance(node, AxisDefinition) and node.id == axis_id
                )
                axis = deepcopy(self._derived.get(axis_id, {}))
                axis.update(
                    directed_axis_result(
                        axis_node, parameters=solve["fit"]["parameters"]
                    )
                )
                axis["resolved_by"] = "connected_fits"
                resolved[axis_id] = axis
                condition = solve["fit"]["normal_matrix_condition"]
                for fit_id, surface in solve.get("surfaces", {}).items():
                    resolved[fit_id] = {**deepcopy(surface), "condition": condition}
                solved_planes = solve.get("reference_planes", {})
                for node in self._recipe.nodes:
                    if isinstance(node, PlaneDefinition) and node.axis == axis_id:
                        resolved[node.id] = deepcopy(
                            solved_planes.get(node.id)
                            or reference_plane_result(axis, node)
                        )
            for point_id, solve in self._connected_point_solves.items():
                point = deepcopy(self._derived.get(point_id, {}))
                point.update(deepcopy(solve["point"]))
                resolved[point_id] = point
                for fit_id, surface in solve["surfaces"].items():
                    resolved[fit_id] = deepcopy(surface)
            for node in self._recipe.nodes:
                if (
                    not isinstance(node, AxisDefinition)
                    or node.source_points is None
                    or self._states.get(node.id) != "ready"
                ):
                    continue
                resolved[node.id] = directed_axis_result(
                    node,
                    first_point=resolved[node.source_points[0]],
                    second_point=resolved[node.source_points[1]],
                )
                for plane in self._recipe.nodes:
                    if (
                        isinstance(plane, PlaneDefinition)
                        and plane.axis == node.id
                        and self._states.get(plane.id) == "ready"
                    ):
                        resolved[plane.id] = reference_plane_result(
                            resolved[node.id], plane
                        )
            for node in self._recipe.nodes:
                if (
                    not isinstance(node, EqualRadii)
                    or self._states.get(node.id) != "ready"
                ):
                    continue
                relationship = self._derived.get(node.id, {})
                for fit_id, surface in relationship.get("surfaces", {}).items():
                    resolved[fit_id] = deepcopy(surface)
            for node in self._recipe.nodes:
                if (
                    not isinstance(node, PlaneRelationship)
                    or self._states.get(node.id) != "ready"
                ):
                    continue
                relationship = self._derived.get(node.id, {})
                for fit_id, surface in relationship.get("surfaces", {}).items():
                    resolved[fit_id] = deepcopy(surface)
            return {
                "recipe": self._recipe.model_dump(),
                "token": self._token(),
                "states": self._states.copy(),
                "errors": self._errors.copy(),
                "diagnostics": deepcopy(self._diagnostics),
                "result": deepcopy(self._results.get(self._recipe.output)),
                "derived": deepcopy(self._derived),
                "results": resolved,
                "memberships": {
                    n.id: n.ids.copy()
                    if isinstance(n, Selection)
                    else deepcopy(self._derived.get(n.id, {}).get("ids"))
                    for n in self._recipe.nodes
                    if isinstance(
                        n, (Selection, Growth, RegionSelection, ReuseSelection)
                    )
                },
            }

    def replace(self, recipe: Recipe, token: str) -> dict[str, object]:
        recipe = self.validate(recipe)
        with self.lock:
            if token != self._token():
                raise StaleGraph(
                    "graph changed; reload the current graph before editing"
                )
            before = {n.id: n for n in self._recipe.nodes}
            affected = {n.id for n in recipe.nodes if before.get(n.id) != n}
            while True:
                expanded = affected | {
                    n.id for n in recipe.nodes if affected.intersection(dependencies(n))
                }
                if expanded == affected:
                    break
                affected = expanded
            before_components = automatic_axis_components(before)
            after_nodes = {n.id: n for n in recipe.nodes}
            after_components = automatic_axis_components(after_nodes)
            for axis_id in before_components.keys() | after_components.keys():
                before_members = before_components.get(axis_id, ([], set()))[1]
                after_members = after_components.get(axis_id, ([], set()))[1]
                if before_members != after_members or affected.intersection(
                    before_members | after_members
                ):
                    affected.update(after_members)
            before_point_components = automatic_point_components(before)
            after_point_components = automatic_point_components(after_nodes)
            for point_id in (
                before_point_components.keys() | after_point_components.keys()
            ):
                before_members = before_point_components.get(point_id, ([], set()))[1]
                after_members = after_point_components.get(point_id, ([], set()))[1]
                if before_members != after_members or affected.intersection(
                    before_members | after_members
                ):
                    affected.update(after_members)
            before_plane_components = automatic_plane_relationship_components(before)
            after_plane_components = automatic_plane_relationship_components(
                after_nodes
            )
            for relationship_id in (
                before_plane_components.keys() | after_plane_components.keys()
            ):
                before_members = before_plane_components.get(
                    relationship_id, ([], [], set())
                )[2]
                after_members = after_plane_components.get(
                    relationship_id, ([], [], set())
                )[2]
                if before_members != after_members or affected.intersection(
                    before_members | after_members
                ):
                    affected.update(after_members)
            self._states = {
                n.id: ("stale" if n.id in before else "unevaluated")
                if n.id in affected
                else self._states[n.id]
                for n in recipe.nodes
            }
            self._results = {
                key: value
                for key, value in self._results.items()
                if key not in affected and any(n.id == key for n in recipe.nodes)
            }
            self._connected_solves = {
                key: value
                for key, value in self._connected_solves.items()
                if key in after_components
                and not affected.intersection(after_components[key][1])
            }
            self._connected_point_solves = {
                key: value
                for key, value in self._connected_point_solves.items()
                if key in after_point_components
                and not affected.intersection(after_point_components[key][1])
            }
            self._derived = {
                key: value
                for key, value in self._derived.items()
                if key not in affected and any(n.id == key for n in recipe.nodes)
            }
            self._errors = {
                key: value
                for key, value in self._errors.items()
                if key not in affected and any(n.id == key for n in recipe.nodes)
            }
            self._diagnostics = {
                key: value
                for key, value in self._diagnostics.items()
                if key not in affected and key in self._states
            }
            # Running work is invalidated even if content changes back later.
            self._states = {
                key: "stale" if value == "running" else value
                for key, value in self._states.items()
            }
            self._recipe = recipe
            self._epoch += 1
            return self.snapshot()

    def evaluate(
        self, token: str, target: str | None = None, all_actions: bool = False
    ) -> dict[str, object]:
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            recipe, epoch = self._recipe.model_copy(deep=True), self._epoch
            nodes = {n.id: n for n in recipe.nodes}
            connected_components = automatic_axis_components(nodes)
            point_components = automatic_point_components(nodes)
            equal_radius_components = automatic_equal_radius_components(nodes)
            plane_relationship_components = automatic_plane_relationship_components(
                nodes
            )
            if all_actions:
                if target is not None:
                    raise ValueError("evaluate all cannot also specify a target")
                order = tuple(nodes)
            else:
                target = recipe.output if target is None else target
                if target not in nodes or not isinstance(
                    nodes[target],
                    (
                        JointFit,
                        SurfaceFit,
                        Growth,
                        SelectionRegion,
                        RegionSelection,
                        FeatureReuse,
                        ReuseSelection,
                        Selection,
                        Source,
                        AxisDefinition,
                        PointDefinition,
                        ScaleDefinition,
                        FrameDefinition,
                        TransformDefinition,
                        PlaneDefinition,
                        AxisSolve,
                        EqualRadii,
                        PlaneRelationship,
                    ),
                ):
                    raise ValueError(
                        "evaluation target must be a source, selection, reuse, region, fit, point, axis, plane, frame, scale, transform, solve, or growth action"
                    )
                needed = {target}
                while True:
                    expanded = needed | {
                        dep for key in needed for dep in dependencies(nodes[key])
                    }
                    for _, members in connected_components.values():
                        if expanded.intersection(members):
                            expanded.update(members)
                    for _, members in point_components.values():
                        if expanded.intersection(members):
                            expanded.update(members)
                    for members in equal_radius_components.values():
                        if expanded.intersection(members):
                            expanded.update(members)
                    for _, _, members in plane_relationship_components.values():
                        if expanded.intersection(members):
                            expanded.update(members)
                    if expanded == needed:
                        break
                    needed = expanded
                order = tuple(n.id for n in recipe.nodes if n.id in needed)

        requested_explicit_solve_axes = {
            cast(AxisSolve, nodes[key]).axis
            for key in order
            if isinstance(nodes[key], AxisSolve)
        }

        def membership(selection_id: str) -> list[int]:
            selection = nodes[selection_id]
            if isinstance(selection, Selection):
                return selection.ids
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                return cast(list[int], self._derived[selection_id]["ids"])

        def derived_result(node_id: str) -> dict[str, Any]:
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                return deepcopy(self._derived[node_id])

        def resolved_result(node_id: str) -> dict[str, Any]:
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                node = nodes[node_id]
                axis_id = (
                    node.id
                    if isinstance(node, AxisDefinition)
                    else node.axis
                    if isinstance(node, PlaneDefinition)
                    else fit_axis(node, nodes)
                    if isinstance(node, SurfaceFit)
                    else None
                )
                solve = self._connected_solves.get(axis_id or "")
                point_id = (
                    node.id
                    if isinstance(node, PointDefinition)
                    else node.point
                    if isinstance(node, SurfaceFit)
                    else None
                )
                point_solve = self._connected_point_solves.get(point_id or "")
                value = deepcopy(self._derived[node_id])
                if solve is not None and isinstance(node, AxisDefinition):
                    value.update(
                        directed_axis_result(
                            node, parameters=solve["fit"]["parameters"]
                        )
                    )
                    value["resolved_by"] = "connected_fits"
                elif solve is not None and isinstance(node, PlaneDefinition):
                    solved_plane = solve.get("reference_planes", {}).get(node_id)
                    if solved_plane is not None:
                        value = deepcopy(solved_plane)
                    else:
                        axis_value = deepcopy(self._derived[node.axis])
                        axis_value.update(
                            directed_axis_result(
                                cast(AxisDefinition, nodes[node.axis]),
                                parameters=solve["fit"]["parameters"],
                            )
                        )
                        value = reference_plane_result(axis_value, node)
                elif solve is not None and isinstance(node, SurfaceFit):
                    surface = solve.get("surfaces", {}).get(node_id)
                    if surface is not None:
                        value = deepcopy(dict(surface))
                if point_solve is not None and isinstance(node, PointDefinition):
                    value.update(deepcopy(point_solve["point"]))
                elif point_solve is not None and isinstance(node, SurfaceFit):
                    surface = point_solve["surfaces"].get(node_id)
                    if surface is not None:
                        value = deepcopy(surface)
                if isinstance(node, SurfaceFit):
                    for relationship in recipe.nodes:
                        if (
                            isinstance(relationship, EqualRadii)
                            and self._states.get(relationship.id) == "ready"
                        ):
                            adjusted = self._derived.get(relationship.id, {}).get(
                                "surfaces", {}
                            )
                            if node_id in adjusted:
                                value = deepcopy(adjusted[node_id])
                    for relationship in recipe.nodes:
                        if (
                            isinstance(relationship, PlaneRelationship)
                            and self._states.get(relationship.id) == "ready"
                        ):
                            adjusted = self._derived.get(relationship.id, {}).get(
                                "surfaces", {}
                            )
                            if node_id in adjusted:
                                value = deepcopy(adjusted[node_id])
                return value

        def fitted_ids(surface: SurfaceFit) -> list[int]:
            return sorted({i for ref in surface.selections for i in membership(ref)})

        def selected(surface: SurfaceFit) -> FitSelection:
            return FitSelection(
                surface.id,
                fitted_ids(surface),
                surface.kind,
                surface.axial_domain,
            )

        def reject_overlaps(surfaces: list[SurfaceFit]) -> None:
            memberships = [
                (surface.id, set(fitted_ids(surface))) for surface in surfaces
            ]
            conflicts: list[dict[str, Any]] = []
            for index, (left, left_ids) in enumerate(memberships):
                for right, right_ids in memberships[index + 1 :]:
                    overlap = sorted(left_ids & right_ids)
                    if overlap:
                        conflicts.append({"fits": [left, right], "ids": overlap})
            if conflicts:
                raise SelectionOverlap(conflicts)

        def solve_connected_axis(
            axis_id: str, fitted_factors: list[SurfaceFit]
        ) -> SessionFit:
            reject_overlaps(fitted_factors)
            sources = {
                selection_source(nodes[ref], nodes)
                for factor in fitted_factors
                for ref in factor.selections
            }
            if len(sources) != 1:
                raise ValueError("connected fits on a free axis must share one source")
            sides = [factor for factor in fitted_factors if factor.kind != "plane"]
            planes = [
                factor
                for factor in fitted_factors
                if factor.kind == "plane" and factor.axis is not None
            ]
            referenced_plane_factors: dict[str, list[SurfaceFit]] = {}
            for factor in fitted_factors:
                if factor.reference_plane is not None:
                    referenced_plane_factors.setdefault(
                        factor.reference_plane, []
                    ).append(factor)
            reference_plane_groups = tuple(
                ReferencePlaneGroup(
                    reference_id,
                    tuple(selected(factor) for factor in group),
                    cast(PlaneDefinition, nodes[reference_id]).construction,
                    cast(PlaneDefinition, nodes[reference_id]).initial_angle_degrees,
                )
                for reference_id, group in referenced_plane_factors.items()
            )
            axis_result = derived_result(axis_id)
            return fit_group(
                self.workspace,
                [selected(side) for side in sides],
                [selected(plane) for plane in planes],
                axis_initial=np.asarray(axis_result["parameters"], dtype=float),
                reference_plane_groups=reference_plane_groups,
            )

        def solve_connected_point(
            point_id: str, fitted_factors: list[SurfaceFit]
        ) -> dict[str, Any]:
            reject_overlaps(fitted_factors)
            sources = {
                selection_source(nodes[ref], nodes)
                for factor in fitted_factors
                for ref in factor.selections
            }
            if len(sources) != 1:
                raise ValueError("connected fits on a free point must share one source")
            point_result = derived_result(point_id)
            return fit_spheres_to_free_point(
                self.workspace,
                point_id,
                fitted_factors,
                [fitted_ids(surface) for surface in fitted_factors],
                np.asarray(point_result["point_display"], dtype=float),
            )

        def resolve_completed_components(completed: set[str]) -> None:
            explicit_solve_axes = {
                cast(AxisSolve, nodes[key]).axis
                for key in completed
                if isinstance(nodes[key], AxisSolve)
            }
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                for axis_id in explicit_solve_axes:
                    _ = self._connected_solves.pop(axis_id, None)
            for axis_id, (factors, members) in connected_components.items():
                if axis_id in requested_explicit_solve_axes or not members <= completed:
                    continue
                with self.lock:
                    if axis_id in self._connected_solves:
                        continue
                try:
                    connected = solve_connected_axis(axis_id, factors)
                except Exception as error:
                    with self.lock:
                        if epoch == self._epoch:
                            for member in members:
                                self._states[member] = "failed"
                                self._errors[member] = str(error)
                            if isinstance(error, SelectionOverlap):
                                self._diagnostics[axis_id] = error.diagnostic
                            else:
                                _ = self._diagnostics.pop(axis_id, None)
                            _ = self._connected_solves.pop(axis_id, None)
                    raise
                with self.lock:
                    if epoch != self._epoch:
                        raise StaleGraph(
                            "graph changed during connected solve; result discarded"
                        )
                    self._connected_solves[axis_id] = connected
                    for member in members:
                        self._states[member] = "ready"
                        _ = self._errors.pop(member, None)
                    _ = self._diagnostics.pop(axis_id, None)
            for point_id, (factors, members) in point_components.items():
                if not members <= completed:
                    continue
                with self.lock:
                    if point_id in self._connected_point_solves:
                        continue
                try:
                    connected = solve_connected_point(point_id, factors)
                except Exception as error:
                    with self.lock:
                        if epoch == self._epoch:
                            for member in members:
                                self._states[member] = "failed"
                                self._errors[member] = str(error)
                            if isinstance(error, SelectionOverlap):
                                self._diagnostics[point_id] = error.diagnostic
                            else:
                                _ = self._diagnostics.pop(point_id, None)
                            _ = self._connected_point_solves.pop(point_id, None)
                    raise
                with self.lock:
                    if epoch != self._epoch:
                        raise StaleGraph(
                            "graph changed during connected solve; result discarded"
                        )
                    self._connected_point_solves[point_id] = connected
                    for member in members:
                        self._states[member] = "ready"
                        _ = self._errors.pop(member, None)
                    _ = self._diagnostics.pop(point_id, None)

        with self.lock:
            completed = {key for key, state in self._states.items() if state == "ready"}
        resolve_completed_components(completed)
        for key in order:
            node = nodes[key]
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during evaluation; result discarded"
                    )
                if self._states[key] == "ready":
                    continue
                self._states[key] = "running"
            try:
                result = None
                derived = None
                if isinstance(node, SurfaceFit):
                    ids = fitted_ids(node)
                    if node.reference_plane is not None:
                        derived = fit_plane_to_reference(
                            self.workspace,
                            node,
                            ids,
                            derived_result(node.reference_plane),
                        )
                    elif node.point is not None:
                        derived = fit_sphere_at_point(
                            self.workspace,
                            node,
                            ids,
                            derived_result(node.point),
                        )
                    elif node.axis is None:
                        fit_points = self.workspace.local[ids]
                        reuse_inputs = [
                            derived_result(ref)
                            for ref in node.selections
                            if isinstance(nodes[ref], ReuseSelection)
                        ]
                        initial = np.array(
                            self.workspace.data.selection["initial_parameters"]
                        )
                        domain = node.axial_domain
                        if reuse_inputs:
                            if len(reuse_inputs) != len(node.selections):
                                raise ValueError(
                                    "a fit cannot mix reused and ordinary selections"
                                )
                            initials = [
                                value.get("fit_initial") for value in reuse_inputs
                            ]
                            domains = [
                                tuple(value["target_axial_domain"])
                                for value in reuse_inputs
                            ]
                            if any(
                                value != initials[0] for value in initials[1:]
                            ) or any(value != domains[0] for value in domains[1:]):
                                raise ValueError(
                                    "reused fit selections must share one transformed source fit"
                                )
                            if initials[0] is not None:
                                initial = np.asarray(initials[0], dtype=float)
                            domain = cast(tuple[float, float], domains[0])
                            if initials[0] is not None:
                                point = np.asarray([initial[0], initial[1], 0.0])
                                direction = np.asarray([initial[2], initial[3], 1.0])
                                axial = (
                                    (fit_points - point)
                                    @ direction
                                    / float(direction @ direction)
                                )
                                padding = max(
                                    float(np.ptp(axial)) * 0.05,
                                    1e-3,
                                )
                                domain = (
                                    min(domain[0], float(np.min(axial)) - padding),
                                    max(domain[1], float(np.max(axial)) + padding),
                                )
                        derived = fit_seed(
                            fit_points,
                            self.workspace.data.weights[ids],
                            self.workspace.data.normals[ids] @ self.workspace.frame,
                            node.kind,
                            initial,
                            domain,
                        )
                        derived["ids"] = ids
                    else:
                        axis_result = derived_result(node.axis)
                        fixed = fit_fixed_axis_group(
                            self.workspace,
                            [selected(node)] if node.kind != "plane" else [],
                            [selected(node)] if node.kind == "plane" else [],
                            np.asarray(axis_result["parameters"], dtype=float),
                        )
                        fixed_surfaces = fixed.get("surfaces")
                        if fixed_surfaces is None:
                            raise AssertionError("fixed-axis fit omitted its surface")
                        derived = dict(fixed_surfaces[node.id])
                        derived["condition"] = fixed["fit"]["normal_matrix_condition"]
                        if node.kind == "plane":
                            parameters = cast(list[float], derived["parameters"])
                            derived["plane_equation"] = [
                                *fixed["axis_display"],
                                parameters[5],
                            ]
                elif isinstance(node, AxisDefinition):
                    if node.source_fit is not None:
                        source = derived_result(node.source_fit)
                        parameters = list(source["parameters"])
                        derived = directed_axis_result(node, parameters=parameters)
                    elif node.source_points is not None:
                        derived = directed_axis_result(
                            node,
                            first_point=resolved_result(node.source_points[0]),
                            second_point=resolved_result(node.source_points[1]),
                        )
                    else:
                        assert node.initial_parameters is not None
                        parameters = [*node.initial_parameters, 0.0, 0.0, 0.0]
                        derived = directed_axis_result(node, parameters=parameters)
                elif isinstance(node, PointDefinition):
                    if node.source_fit is not None:
                        source = derived_result(node.source_fit)
                        coordinates = list(source["parameters"][:3])
                    else:
                        assert node.initial_coordinates is not None
                        coordinates = list(node.initial_coordinates)
                    derived = {
                        "source_fit": node.source_fit,
                        "coordinates": coordinates,
                        "point_display": coordinates,
                    }
                elif isinstance(node, ScaleDefinition):
                    derived = scale_result(
                        node,
                        {
                            reference: resolved_result(reference)
                            for reference in dependencies(node)
                        },
                    )
                elif isinstance(node, FrameDefinition):
                    derived = frame_result(
                        node,
                        {
                            reference: resolved_result(reference)
                            for reference in dependencies(node)
                        },
                    )
                elif isinstance(node, TransformDefinition):
                    derived = output_transform_result(
                        node,
                        resolved_result(node.frame),
                        resolved_result(node.scale),
                    )
                elif isinstance(node, PlaneDefinition):
                    derived = reference_plane_result(derived_result(node.axis), node)
                elif isinstance(node, Growth):
                    fitted = nodes[node.seed_fit]
                    assert isinstance(fitted, SurfaceFit)
                    seed_result = derived_result(node.seed_fit)
                    barriers = sorted(
                        {i for ref in node.barriers for i in membership(ref)}
                    )
                    derived = connected_growth(
                        self.workspace.local,
                        self.workspace.data.normals @ self.workspace.frame,
                        self.workspace.data.triangles,
                        self.workspace.data.weights,
                        fitted_ids(fitted),
                        barriers,
                        seed_result,
                        node.distance,
                        node.angle_degrees,
                    )
                elif isinstance(node, SelectionRegion):
                    axis_id = selection_frame_axis(
                        node.axial_plane, node.clock_plane, nodes
                    )
                    origin, rotation = datum_frame(
                        resolved_result(axis_id),
                        resolved_result(node.axial_plane),
                        resolved_result(node.clock_plane),
                    )
                    ids = membership(node.selection)
                    derived = build_selection_region(
                        self.workspace.local[ids],
                        self.workspace.data.normals[ids] @ self.workspace.frame,
                        resolved_result(node.fit),
                        origin,
                        rotation,
                        tangent_margin=node.tangent_margin,
                        normal_margin=node.normal_margin,
                        normal_angle_degrees=node.normal_angle_degrees,
                    )
                    derived.update(
                        {
                            "selection": node.selection,
                            "fit": node.fit,
                            "source": selection_source(nodes[node.selection], nodes),
                        }
                    )
                elif isinstance(node, RegionSelection):
                    axis_id = selection_frame_axis(
                        node.axial_plane, node.clock_plane, nodes
                    )
                    origin, rotation = datum_frame(
                        resolved_result(axis_id),
                        resolved_result(node.axial_plane),
                        resolved_result(node.clock_plane),
                    )
                    region = derived_result(node.region)
                    ids = apply_selection_region(
                        self.workspace.local,
                        self.workspace.data.normals @ self.workspace.frame,
                        self.workspace.data.weights,
                        region,
                        origin,
                        rotation,
                    )
                    derived = {
                        "ids": ids,
                        "region": node.region,
                        "source": node.source,
                        "vertex_count": len(ids),
                    }
                elif isinstance(node, FeatureReuse):
                    reference_ids = membership(node.reference_selection)
                    normals = self.workspace.data.normals @ self.workspace.frame
                    matches = {}
                    for target_selection in node.target_selections:
                        target_ids = membership(target_selection)
                        matches[target_selection] = estimate_rigid_match(
                            self.workspace.local[reference_ids],
                            normals[reference_ids],
                            self.workspace.local[target_ids],
                            normals[target_ids],
                        )
                    derived = {
                        "format": "scansor-feature-reuse-v1",
                        "fits": node.fits,
                        "lineage": node.lineage,
                        "reference_selection": node.reference_selection,
                        "target_selections": node.target_selections,
                        "matches": matches,
                    }
                elif isinstance(node, ReuseSelection):
                    reuse = nodes[node.reuse]
                    fitted = nodes[node.fit]
                    assert isinstance(reuse, FeatureReuse)
                    assert isinstance(fitted, SurfaceFit)
                    reuse_result = derived_result(node.reuse)
                    match = reuse_result["matches"][node.target_selection]
                    rotation = np.asarray(match["rotation"], dtype=float)
                    translation = np.asarray(match["translation"], dtype=float)
                    source_ids = membership(node.source_selection)
                    source_points = self.workspace.local[source_ids]
                    fit_result = resolved_result(node.fit)
                    fit_result.setdefault("kind", fitted.kind)
                    fit_result.setdefault("axial_domain", fitted.axial_domain)
                    fit_initial, target_domain = transformed_fit_seed(
                        fit_result, rotation, translation
                    )
                    source_origin, source_frame = surface_region_frame(
                        source_points, fit_result
                    )
                    region = build_selection_region(
                        source_points,
                        self.workspace.data.normals[source_ids] @ self.workspace.frame,
                        fit_result,
                        source_origin,
                        source_frame,
                        tangent_margin=reuse.tangent_margin,
                        normal_margin=reuse.normal_margin,
                        normal_angle_degrees=reuse.normal_angle_degrees,
                    )
                    target_origin = source_origin @ rotation + translation
                    target_frame = rotation.T @ source_frame
                    ids = apply_selection_region(
                        self.workspace.local,
                        self.workspace.data.normals @ self.workspace.frame,
                        self.workspace.data.weights,
                        region,
                        target_origin,
                        target_frame,
                    )
                    derived = {
                        "ids": ids,
                        "reuse": node.reuse,
                        "fit": node.fit,
                        "source_selection": node.source_selection,
                        "target_selection": node.target_selection,
                        "vertex_count": len(ids),
                        "region": region,
                        "target_origin": target_origin.tolist(),
                        "target_rotation": target_frame.tolist(),
                        "fit_initial": fit_initial,
                        "target_axial_domain": target_domain,
                    }
                elif isinstance(node, EqualRadii):
                    surfaces = [cast(SurfaceFit, nodes[ref]) for ref in node.surfaces]
                    surface_ids = [fitted_ids(surface) for surface in surfaces]
                    derived = fit_equal_radii(
                        self.workspace,
                        surfaces,
                        [resolved_result(surface.id) for surface in surfaces],
                        surface_ids,
                    )
                elif isinstance(node, PlaneRelationship):
                    relationships, planes, _ = plane_relationship_components[node.id]
                    plane_ids = [fitted_ids(surface) for surface in planes]
                    derived = fit_plane_relationships(
                        self.workspace,
                        relationships,
                        planes,
                        [resolved_result(surface.id) for surface in planes],
                        plane_ids,
                    )
                elif isinstance(node, AxisSolve):
                    factors = [nodes[ref] for ref in node.factors]
                    fitted_factors = [
                        factor for factor in factors if isinstance(factor, SurfaceFit)
                    ]
                    mirrors = [
                        factor
                        for factor in factors
                        if isinstance(factor, MirrorSymmetry)
                    ]
                    mirror_radius_sources = constrained_mirror_radii(factors, nodes)
                    if len({mirror.plane for mirror in mirrors}) != len(mirrors):
                        raise ValueError(
                            "each mirror plane may drive only one pair in a joint"
                        )
                    mirror_surfaces = [
                        cast(SurfaceFit, nodes[ref])
                        for mirror in mirrors
                        for ref in mirror.surfaces
                    ]
                    reject_overlaps([*fitted_factors, *mirror_surfaces])
                    sides = [
                        factor for factor in fitted_factors if factor.kind != "plane"
                    ]
                    planes = [
                        factor
                        for factor in fitted_factors
                        if factor.kind == "plane" and factor.axis is not None
                    ]
                    referenced_plane_factors: dict[str, list[SurfaceFit]] = {}
                    for factor in fitted_factors:
                        if factor.reference_plane is not None:
                            referenced_plane_factors.setdefault(
                                factor.reference_plane, []
                            ).append(factor)
                    reference_plane_groups = tuple(
                        ReferencePlaneGroup(
                            reference_id,
                            tuple(selected(factor) for factor in group),
                            cast(PlaneDefinition, nodes[reference_id]).construction,
                            cast(
                                PlaneDefinition, nodes[reference_id]
                            ).initial_angle_degrees,
                        )
                        for reference_id, group in referenced_plane_factors.items()
                    )
                    axis_result = derived_result(node.axis)
                    result = fit_group(
                        self.workspace,
                        [selected(side) for side in sides],
                        [selected(plane) for plane in planes],
                        axis_initial=np.asarray(axis_result["parameters"], dtype=float),
                        mirror_groups=tuple(
                            (
                                selected(cast(SurfaceFit, nodes[mirror.surfaces[0]])),
                                selected(cast(SurfaceFit, nodes[mirror.surfaces[1]])),
                            )
                            for mirror in mirrors
                        ),
                        mirror_radius_surface_ids=mirror_radius_sources,
                        mirror_phases_radians=tuple(
                            np.radians(
                                cast(
                                    float,
                                    cast(
                                        PlaneDefinition, nodes[mirror.plane]
                                    ).initial_angle_degrees,
                                )
                            )
                            for mirror in mirrors
                        ),
                        reference_plane_groups=reference_plane_groups,
                    )
                    fitted_mirror_planes = cast(
                        list[dict[str, Any]], result.get("mirror_planes", [])
                    )
                    result_data = cast(dict[str, Any], cast(object, result))
                    result_data["mirror_planes"] = {
                        mirror.plane: {
                            "axis_display": result["axis_display"],
                            "basis_u_display": result["axis_display"],
                            "basis_v_display": values["direction"],
                            "point_display": result["point_display"],
                            "radial_display": values["direction"],
                            "normal_display": values["equation"][:3],
                            "plane_equation": values["equation"],
                            "angle_degrees": float(np.degrees(values["phase_radians"])),
                            "offset": 0.0,
                            "construction": "contains_axis",
                        }
                        for mirror, values in zip(
                            mirrors, fitted_mirror_planes, strict=True
                        )
                    }
                elif isinstance(node, JointFit):
                    sides, planes = joint_surfaces(node, nodes)
                    rotations = [
                        cast(RotationalSymmetry, nodes[ref])
                        for ref in node.constraints
                        if isinstance(nodes[ref], RotationalSymmetry)
                    ]
                    rotational_ids = {
                        ref for rotation in rotations for ref in rotation.planes
                    }

                    reject_overlaps([*sides, *planes])
                    if len(sides) > 1 or len(planes) > 1 or rotations:
                        result = fit_group(
                            self.workspace,
                            [selected(side) for side in sides],
                            [
                                selected(plane)
                                for plane in planes
                                if plane.id not in rotational_ids
                            ],
                            tuple(
                                tuple(
                                    selected(cast(SurfaceFit, nodes[ref]))
                                    for ref in rotation.planes
                                )
                                for rotation in rotations
                            ),
                        )
                    else:
                        lateral, plane = sides[0], planes[0]
                        left, right = selected(lateral), selected(plane)
                        session = NozzleSession(
                            source_sha256=self.workspace.default.source_sha256,
                            model_sha256=self.workspace.model_sha256,
                            lateral_ids=left.ids,
                            plane_ids=right.ids,
                        )
                        result = self.workspace.fit(
                            session, lateral.kind, lateral.axial_domain
                        )
                        result["surfaces"] = {
                            lateral.id: {
                                "kind": lateral.kind,
                                "ids": left.ids,
                                "parameters": result["fit"]["parameters"],
                                "axial_domain": lateral.axial_domain,
                                "residuals": result["lateral_residuals"],
                                "weighted_rms": result["fit"]["cone_weighted_rms"],
                            },
                            plane.id: {
                                "kind": "plane",
                                "ids": right.ids,
                                "parameters": result["fit"]["parameters"],
                                "axial_domain": plane.axial_domain,
                                "residuals": result["plane_residuals"],
                                "weighted_rms": result["fit"]["plane_weighted_rms"],
                            },
                        }
            except Exception as error:
                with self.lock:
                    if epoch == self._epoch:
                        self._states[key] = "failed"
                        self._errors[key] = str(error)
                        if isinstance(error, SelectionOverlap):
                            self._diagnostics[key] = error.diagnostic
                        else:
                            _ = self._diagnostics.pop(key, None)
                raise
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during evaluation; result discarded"
                    )
                self._states[key] = "ready"
                _ = self._errors.pop(key, None)
                _ = self._diagnostics.pop(key, None)
                if result is not None:
                    self._results[key] = result
                if derived is not None:
                    self._derived[key] = derived
            completed.add(key)
            resolve_completed_components(completed)
        resolve_completed_components(completed)
        return self.snapshot()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--example", type=Path, default=Path("examples/nozzle-bayonette-simplified")
    )
    _ = parser.add_argument("--recipe", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument(
        "--target", help="Evaluate a specific action instead of the recipe output"
    )
    args = parser.parse_args()
    graph = FeatureGraph(
        NozzleWorkspace(args.example),
        Recipe.model_validate_json(args.recipe.read_text()),
    )
    state = graph.evaluate(str(graph.snapshot()["token"]), args.target)
    with args.output.open("x") as stream:
        _ = stream.write(json.dumps(state, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
