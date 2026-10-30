"""Experimental backend DAG for captured-mesh recipes; current state only."""

# Output models load independently; provider functions import this module only
# when called after initialization. The lazy provider integration is intentional.
# pyright: reportImportCycles=false

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Callable
from concurrent.futures import Executor, Future, wait
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from threading import Event, RLock
from typing import Annotated, Any, ClassVar, Generic, Literal, Self, cast, final

import numpy as np
from numpy.typing import NDArray
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)
from typing_extensions import TypeVar

from experiments.actions_limits import check_portable_actions
from experiments.feature_inputs import (
    REQUIREMENTS,
    FeatureInput,
    OutputReference,
    input_choices,
    output_catalogue,
    reference_dependencies,
    reference_json,
    resolve_output,
    validate_fit_reference,
    validate_output_reference,
)
from experiments.feature_inputs import (
    reference_key as input_reference_key,
)
from experiments.feature_reuse import (
    MAX_CIRCULAR_MEMBERSHIPS,
    MAX_CIRCULAR_SELECTION_VERTICES,
    circular_placements,
    estimate_rigid_match,
    surface_region_frame,
    transformed_axis_geometry,
    transformed_fit_seed,
    transformed_surface_geometry,
)
from experiments.fit_initialization import RecoveryFailure, initialization_binding
from experiments.fit_relationship_component import (
    AxisInput,
    MirrorInput,
    PlaneInput,
    RadiusDistanceInput,
    RelationshipFailure,
    RotationInput,
)
from experiments.fit_solver import FitCancelled, normalized_weights
from experiments.mesh_cone_fit import CONE_POLICY_VERSION, ConeFitFailure
from experiments.mesh_cylinder_fit import CYLINDER_POLICY_VERSION, CylinderFitFailure
from experiments.mesh_sphere_fit import SphereFitFailure, fit_shared_sphere_center
from experiments.native_replay import native_replay_scope
from experiments.nozzle_coaxial import (
    FitSelection,
    ReferencePlaneGroup,
    fit_fixed_axis_group,
    fit_group,
)
from experiments.nozzle_session import (
    NozzleSession,
    NozzleWorkspace,
    VertexId,
)
from experiments.relationship_inputs import (
    RELATIONSHIP_SIGNATURES,
    RelationshipEndpoint,
    normalize_endpoint,
    validate_relationship_inputs,
)
from experiments.scan_workspace import (
    ScanCancelled,
    ScanWorkspace,
    SourceDescriptor,
    Workspace,
    check_cancelled,
)
from experiments.selected_direction_fit import DirectionFitFailure
from experiments.selected_symmetry_fit import (
    SelectedSurface,
    SelectedSymmetryGroup,
    SymmetryFitFailure,
    fit_selected_symmetry_group,
)
from experiments.selection_entries import (
    SelectionEntry,
    VolumeReference,
    membership_projection,
    source_binding,
    validate_entries,
)
from experiments.selection_entry_runtime import (
    VolumeCache,
    current_entry,
    entry_diagnostic,
    resolve_membership,
    validate_records,
)
from experiments.selection_growth import PlaneFitFailure, connected_growth, fit_seed
from experiments.selection_region import (
    apply_selection_region,
    build_selection_region,
    datum_frame,
)
from experiments.support_intent import (
    SUPPORT_POLICY_VERSION,
    SupportIntent,
    SupportIntentFailure,
    legacy_support,
    support_record,
    transform_support,
)
from experiments.surface_extents import surface_intersection, trimmed_face
from experiments.surface_primitives import revolution_geometry, unit, vector
from experiments.virtual_scan_reduction import VirtualScanDerivation
from scansor.constrained_least_squares import ConstrainedLeastSquaresFailure

MAX_RECIPE_ACTIONS = 10_000


class Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class ReusePlacement(Record):
    """Initialize an occurrence datum from resolved source geometry and its match."""

    reuse: str
    source: str
    target_selection: str
    occurrence: int | None = Field(default=None, ge=1, le=31, strict=True)

    @model_serializer(mode="wrap")
    def portable(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value = handler(self)
        if self.occurrence is None:
            value.pop("occurrence", None)
        return cast(dict[str, Any], value)


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
    descriptor: SourceDescriptor | None = None
    virtual_scan: VirtualScanDerivation | None = None

    @model_validator(mode="after")
    def virtual_scan_binding(self) -> Self:
        if self.virtual_scan is None:
            return self
        lineage = VirtualScanDerivation.model_validate(self.virtual_scan.model_dump())
        descriptor = self.descriptor
        if descriptor is None or (
            self.source_sha256 != descriptor.source_sha256
            or self.reference_sha256 != descriptor.binding_sha256
            or descriptor.semantic_record()
            not in (
                lineage.original.semantic_record(),
                lineage.derived.semantic_record(),
            )
        ):
            raise ValueError("active Source must match a virtual scan lineage endpoint")
        return self

    @model_serializer(mode="wrap")
    def source_record(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        value = cast(dict[str, Any], handler(self))
        if self.virtual_scan is None:
            _ = value.pop("virtual_scan", None)
        return value


class Selection(Node):
    operation: Literal["selection"]
    source: str
    ids: list[VertexId] = Field(default_factory=list, max_length=25_000)
    depth: Literal["through_all", "first_surface"] = "through_all"
    entries: list[SelectionEntry] | None = Field(
        default=None, min_length=1, max_length=128
    )

    @model_validator(mode="before")
    @classmethod
    def one_membership_format(cls, value: Any) -> Any:
        if isinstance(value, dict):
            payload = cast(dict[str, Any], value)
            if payload.get("entries") is not None and {"ids", "depth"} & payload.keys():
                raise ValueError("entry selections cannot also define flat membership")
            if payload.get("entries") is None and "ids" not in payload:
                raise ValueError("a painted selection requires IDs")
        return value

    @model_validator(mode="after")
    def unique_entries(self) -> Selection:
        if self.entries is not None:
            _ = validate_entries(self.entries)
        return self

    @model_serializer(mode="wrap")
    def membership_record(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        value = cast(dict[str, Any], handler(self))
        if self.entries is None:
            _ = value.pop("entries", None)
        else:
            _ = value.pop("ids", None)
            _ = value.pop("depth", None)
        return value


class SurfaceFit(Node):
    """An independently evaluated fit action; joints never overwrite its result."""

    operation: Literal["fit"]
    selections: list[str] = Field(min_length=1, max_length=99)
    kind: Literal["cone", "cylinder", "plane", "sphere"]
    axial_domain: tuple[float, float] | None = None
    support_authority: Literal["local", "prototype"] | None = None
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


class SurfaceSelection(Node):
    """Connected source observations derived directly from painted seeds."""

    operation: Literal["surface_selection"]
    seed_selection: str
    barriers: list[str] = Field(default_factory=list, max_length=99)
    angle_degrees: float = Field(default=20.0, gt=0, le=90, allow_inf_nan=False)
    setback: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    policy: Literal["seed-normal-envelope-v1", "local-boundaries-v1"] = (
        "seed-normal-envelope-v1"
    )
    neighborhood_distance: float | None = Field(default=None, gt=0, allow_inf_nan=False)


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


class CircularVolume(Record):
    fit: str
    source_selection: str
    volume: VolumeReference


class CircularReuse(Record):
    axis: str
    count: int = Field(ge=2, le=32, strict=True)
    volumes: list[CircularVolume] = Field(min_length=1, max_length=99)


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
    circular: CircularReuse | None = None


class ReuseSelection(Node):
    """Target membership generated for one source selection in a reuse instance."""

    operation: Literal["reuse_selection"]
    reuse: str
    fit: str
    source_selection: str
    target_selection: str
    occurrence: int | None = Field(default=None, ge=1, le=31, strict=True)


class Perpendicular(Node):
    operation: Literal["perpendicular"]
    lateral: str
    plane: str


class Coaxial(Node):
    operation: Literal["coaxial"]
    surface: str
    reference: str


class CoaxialGroup(Node):
    """Multiple independent lateral shapes share one explicit datum line."""

    operation: Literal["coaxial_group"]
    axis: str
    surfaces: list[str] = Field(min_length=1, max_length=32)


class RotationalSymmetry(Node):
    operation: Literal["rotational_symmetry"]
    axis: str
    # Existing serialized name retained; inputs are same-type surface fits.
    # Ordered positive rotations about the resolved directed common axis.
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
    source_points: tuple[FeatureInput, FeatureInput] | None = None
    initial_parameters: tuple[float, float, float, float] | None = None
    direction_reversed: bool = False
    placement: ReusePlacement | None = None

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

    first_point: FeatureInput
    second_point: FeatureInput
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
    origin_point: FeatureInput
    primary_reference: FeatureInput
    primary_output_axis: CoordinateAxis
    secondary_reference: FeatureInput
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
    """An independently initialized plane or a construction on an explicit axis."""

    operation: Literal["reference_plane"]
    axis: str | None = None
    construction: Literal[
        "independent", "contains_axis", "parallel_to_axis", "perpendicular_to_axis"
    ] = "contains_axis"
    initial_normal: tuple[float, float, float] | None = None
    initial_angle_degrees: float | None = Field(default=0.0, allow_inf_nan=False)
    offset: float = Field(default=0.0, allow_inf_nan=False)
    placement: ReusePlacement | None = None

    @model_validator(mode="before")
    @classmethod
    def independent_defaults(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and cast(dict[str, Any], value).get("construction") == "independent"
            and "initial_angle_degrees" not in value
        ):
            return {**value, "initial_angle_degrees": None}
        return value

    @model_validator(mode="after")
    def construction_parameters(self) -> PlaneDefinition:
        if self.construction == "independent":
            if self.axis is not None or self.initial_angle_degrees is not None:
                raise ValueError("an independent plane has no axis or clocking angle")
            if (
                self.initial_normal is None
                or not all(math.isfinite(value) for value in self.initial_normal)
                or max(abs(value) for value in self.initial_normal) == 0
            ):
                raise ValueError(
                    "an independent plane requires a finite nonzero initial normal"
                )
            return self
        if self.axis is None:
            raise ValueError(
                "an axis-constructed reference plane requires an explicit axis"
            )
        if self.initial_normal is not None:
            raise ValueError(
                "an axis-constructed plane has no independent initial normal"
            )
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
    """Exact orientation, and optionally offset, shared by plane outputs."""

    operation: Literal["plane_relationship"]
    relation: Literal["coincident", "parallel"]
    surfaces: list[FeatureInput] = Field(min_length=2, max_length=33)


class DirectionAlignment(Node):
    """Exact unsigned parallelism without sharing position or plane offset."""

    operation: Literal["direction_alignment"]
    directions: list[FeatureInput] = Field(min_length=1, max_length=33)
    reference: OutputReference | None = None
    sense: Literal["unsigned"] = "unsigned"

    @property
    def surfaces(self) -> list[FeatureInput]:
        return [
            *([self.reference] if self.reference is not None else []),
            *self.directions,
        ]


GeometryRelationship = PlaneRelationship | DirectionAlignment


class AxisSolve(Node):
    """Explicit active factors that jointly refine one shared free axis."""

    operation: Literal["axis_solve"]
    axis: str
    factors: list[str] = Field(min_length=1, max_length=32)


class SurfaceReference(Record):
    """Choose independent/resolved geometry or a member in an explicit solve."""

    feature: str
    surface: str | None = None


class AdjacencyChoice(Record):
    """A reviewed physical relationship, not a fitting constraint."""

    first: SurfaceReference
    second: SurfaceReference
    state: Literal["confirmed", "rejected"]


class FaceScope(Record):
    """Explicit union of declared faces; never an observation-derived extent."""

    surface: SurfaceReference
    faces: list[str] = Field(min_length=1)


class SurfaceIntersection(Node):
    operation: Literal["surface_intersection"]
    first: SurfaceReference
    second: SurfaceReference


class FaceBoundary(Record):
    intersection: str
    keep: Literal["positive", "negative", "inside", "outside"]


class TrimmedFace(Node):
    operation: Literal["trimmed_face"]
    surface: SurfaceReference
    boundaries: list[FaceBoundary] = Field(min_length=1)
    boundary_sources: list[str] = Field(default_factory=list)


class RegionSelector(Record):
    """Reviewed connected cell identity, never a kernel enumeration index."""

    signs: dict[str, Literal["positive", "negative"]]
    finite_sides: dict[str, list[Literal["positive", "negative"]]] = Field(
        default_factory=dict
    )
    component_count: int = Field(ge=1)
    witness_chart: list[float] = Field(min_length=2, max_length=2)


class ArrangedFace(Node):
    operation: Literal["arranged_face"]
    surface: SurfaceReference
    cutters: list[SurfaceReference] = Field(min_length=1)
    domains: list[str] = Field(default_factory=list)
    selector: RegionSelector
    boundary_sources: list[str] = Field(default_factory=list)
    finite_boundary_sources: list[str] = Field(default_factory=list)


class BuildFaces(Node):
    """Reviewed authoring scope; accepted regions live in explicit child faces."""

    operation: Literal["build_faces"]
    surfaces: list[SurfaceReference] = Field(min_length=2)
    target: SurfaceReference | None = None
    boundary_sources: list[str] = Field(default_factory=list)
    reused_faces: list[str] = Field(default_factory=list)
    reused_intersections: list[str] = Field(default_factory=list)
    adjacencies: list[AdjacencyChoice] = Field(default_factory=list)
    face_scopes: list[FaceScope] = Field(default_factory=list)


class Body(Node):
    """An explicitly selected collection of faces forming one validated solid."""

    operation: Literal["body"]
    faces: list[str] = Field(min_length=1, max_length=MAX_RECIPE_ACTIONS)
    sewing_tolerance: float = Field(default=1e-7, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_faces(self) -> Body:
        if len(set(self.faces)) != len(self.faces):
            raise ValueError("body face references must be unique")
        return self


Feature = Annotated[
    Source
    | Selection
    | SurfaceFit
    | Growth
    | SurfaceSelection
    | SelectionRegion
    | RegionSelection
    | FeatureReuse
    | ReuseSelection
    | Perpendicular
    | Coaxial
    | CoaxialGroup
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
    | DirectionAlignment
    | AxisSolve
    | SurfaceIntersection
    | TrimmedFace
    | ArrangedFace
    | BuildFaces
    | Body,
    Field(discriminator="operation"),
]


class Recipe(Record):
    schema_version: Literal[2] = 2
    nodes: list[Feature] = Field(default_factory=list, max_length=MAX_RECIPE_ACTIONS)
    groups: list[FeatureGroup] = Field(default_factory=list, max_length=100)
    output: str | None = None

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
    targets: list[str] | None = Field(
        default=None, min_length=1, max_length=MAX_RECIPE_ACTIONS
    )


def dependencies(node: Feature) -> list[str]:
    if isinstance(node, Body):
        return node.faces
    if isinstance(node, ArrangedFace):
        return list(
            dict.fromkeys(
                [
                    node.surface.feature,
                    *(ref.feature for ref in node.cutters),
                    *node.domains,
                    *node.boundary_sources,
                    *([node.managed_by] if node.managed_by else []),
                ]
            )
        )
    if isinstance(node, SurfaceIntersection):
        return list(
            dict.fromkeys(
                [
                    node.first.feature,
                    node.second.feature,
                    *([node.managed_by] if node.managed_by else []),
                ]
            )
        )
    if isinstance(node, TrimmedFace):
        return list(
            dict.fromkeys(
                [
                    node.surface.feature,
                    *(b.intersection for b in node.boundaries),
                    *node.boundary_sources,
                    *([node.managed_by] if node.managed_by else []),
                ]
            )
        )
    if isinstance(node, BuildFaces):
        return list(
            dict.fromkeys(
                [
                    *(ref.feature for ref in node.surfaces),
                    *node.boundary_sources,
                    *node.reused_faces,
                    *node.reused_intersections,
                    *(face for scope in node.face_scopes for face in scope.faces),
                ]
            )
        )
    if isinstance(node, Selection):
        return [node.source]
    if isinstance(node, SurfaceFit):
        reference = node.axis or node.point or node.reference_plane
        return [*node.selections, *((reference,) if reference is not None else ())]
    if isinstance(node, Growth):
        return [node.seed_fit, *node.barriers]
    if isinstance(node, SurfaceSelection):
        return [node.seed_selection, *node.barriers]
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
                    *(
                        [node.circular.axis]
                        if node.circular
                        else node.target_selections
                    ),
                    *(
                        [item.volume.selection for item in node.circular.volumes]
                        if node.circular
                        else []
                    ),
                ]
            )
        )
    if isinstance(node, ReuseSelection):
        return [
            node.reuse,
            node.fit,
            node.source_selection,
            *([] if node.occurrence is not None else [node.target_selection]),
        ]
    if isinstance(node, Perpendicular):
        return [node.lateral, node.plane]
    if isinstance(node, Coaxial):
        return [node.surface, node.reference]
    if isinstance(node, CoaxialGroup):
        return [node.axis, *node.surfaces]
    if isinstance(node, RotationalSymmetry):
        return [node.axis, *node.planes]
    if isinstance(node, JointFit):
        return node.constraints
    if isinstance(node, AxisDefinition):
        if node.placement is not None:
            return [
                node.placement.reuse,
                node.placement.source,
                *(
                    []
                    if node.placement.occurrence is not None
                    else [node.placement.target_selection]
                ),
            ]
        if node.source_fit is not None:
            return [node.source_fit]
        return list(
            dict.fromkeys(
                dep
                for ref in node.source_points or ()
                for dep in reference_dependencies(ref)
            )
        )
    if isinstance(node, PointDefinition):
        return [node.source_fit] if node.source_fit is not None else []
    if isinstance(node, ScaleDefinition):
        return list(
            dict.fromkeys(
                [
                    *(
                        dep
                        for distance in node.distances
                        for ref in (
                            distance.first_point,
                            distance.second_point,
                        )
                        for dep in reference_dependencies(ref)
                    ),
                ]
            )
        )
    if isinstance(node, FrameDefinition):
        return list(
            dict.fromkeys(
                [
                    dep
                    for ref in (
                        node.origin_point,
                        node.primary_reference,
                        node.secondary_reference,
                    )
                    for dep in reference_dependencies(ref)
                ]
            )
        )
    if isinstance(node, TransformDefinition):
        return [node.frame, node.scale]
    if isinstance(node, PlaneDefinition):
        return [
            *([node.axis] if node.axis is not None else []),
            *(
                [
                    node.placement.reuse,
                    node.placement.source,
                    *(
                        []
                        if node.placement.occurrence is not None
                        else [node.placement.target_selection]
                    ),
                ]
                if node.placement
                else []
            ),
        ]
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
    if isinstance(node, (PlaneRelationship, DirectionAlignment)):
        return list(
            dict.fromkeys(
                dep for ref in node.surfaces for dep in reference_dependencies(ref)
            )
        )
    if isinstance(node, AxisSolve):
        return [node.axis, *node.factors]
    return []


def surface_reference_kind(
    reference: SurfaceReference, nodes: dict[str, Feature]
) -> str:
    """Reject implicit member/context substitution before any numerical work."""
    feature = nodes[reference.feature]
    if isinstance(feature, (SurfaceFit, PlaneDefinition)):
        if reference.surface is not None:
            raise ValueError("direct surface references must not specify a member")
        axis = (
            feature.axis
            if isinstance(feature, PlaneDefinition)
            else fit_axis(feature, nodes)
        )
        if any(
            isinstance(node, AxisSolve) and node.axis == axis for node in nodes.values()
        ):
            raise ValueError(
                "surface geometry on an explicitly solved axis is ambiguous; choose a fitted member in a named solve context (datum members are not supported yet)"
            )
        return feature.kind if isinstance(feature, SurfaceFit) else "plane"
    members: list[str]
    if isinstance(feature, JointFit):
        sides, planes = joint_surfaces(feature, nodes)
        members = [surface.id for surface in [*sides, *planes]]
    elif isinstance(feature, AxisSolve):
        members = [
            surface.id for surface in selected_symmetry_inputs(feature, nodes)[0]
        ]
        members.extend(
            ref
            for factor in feature.factors
            if isinstance(nodes[factor], MirrorSymmetry)
            for ref in cast(MirrorSymmetry, nodes[factor]).surfaces
        )
        members.extend(
            endpoint.feature
            for ref in feature.factors
            if isinstance(factor := nodes[ref], PlaneRelationship)
            for endpoint in plane_relationship_endpoints(factor, nodes)
            if endpoint.is_binding
            and isinstance(nodes[endpoint.feature], PlaneDefinition)
        )
    elif isinstance(feature, (EqualRadii, CoaxialGroup)):
        if isinstance(feature, CoaxialGroup) and any(
            isinstance(node, AxisSolve) and node.axis == feature.axis
            for node in nodes.values()
        ):
            raise ValueError(
                "coaxial group geometry is unavailable on an explicitly solved axis; choose a fitted member in the named solve context"
            )
        members = feature.surfaces
    elif isinstance(feature, (PlaneRelationship, DirectionAlignment)):
        members = [
            member
            for provider in describe_geometry_influence(nodes).providers.values()
            for member in provider.publications.get(feature.id, ())
            if isinstance(nodes[member], (SurfaceFit, PlaneDefinition))
        ]
    else:
        raise ValueError(
            "surface reference must name a fit, reference plane, or explicit geometry context"
        )
    if reference.surface not in members:
        raise ValueError("geometry context requires an explicit member surface")
    member = nodes[reference.surface]
    return (
        "plane"
        if isinstance(member, PlaneDefinition)
        else cast(SurfaceFit, member).kind
    )


def validate_face_review_references(
    surfaces: list[SurfaceReference],
    adjacencies: list[AdjacencyChoice],
    scopes: list[FaceScope],
    nodes: dict[str, Feature],
) -> None:
    def key(reference: SurfaceReference) -> tuple[str, str | None]:
        return reference.feature, reference.surface

    selected = {key(reference) for reference in surfaces}
    pairs: set[frozenset[tuple[str, str | None]]] = set()
    for choice in adjacencies:
        pair = frozenset((key(choice.first), key(choice.second)))
        if len(pair) != 2 or not pair <= selected:
            raise ValueError("adjacency must name two distinct selected surfaces")
        if pair in pairs:
            raise ValueError("duplicate adjacency decision")
        pairs.add(pair)
    scoped: set[tuple[str, str | None]] = set()
    for scope in scopes:
        identity = key(scope.surface)
        if identity not in selected or identity in scoped:
            raise ValueError("face scopes must name unique selected surfaces")
        scoped.add(identity)
        if len(set(scope.faces)) != len(scope.faces):
            raise ValueError("duplicate face in physical scope")
        for face_id in scope.faces:
            face = nodes.get(face_id)
            if not isinstance(face, (TrimmedFace, ArrangedFace)):
                raise ValueError(
                    "physical scope must reference an existing trimmed face"
                )
            if key(face.surface) != identity:
                raise ValueError(
                    "physical scope face must use the same exact surface context"
                )


def is_standalone_fit(node: SurfaceFit) -> bool:
    return node.axis is None and node.point is None and node.reference_plane is None


def coaxial_axis_bindings(nodes: dict[str, Feature]) -> dict[str, str]:
    """Compile explicit identities without changing saved fit declarations."""
    bindings: dict[str, str] = {}
    for group in nodes.values():
        if not isinstance(group, CoaxialGroup):
            continue
        if not isinstance(nodes.get(group.axis), AxisDefinition):
            raise ValueError("coaxial group requires an explicit datum axis")
        if len(set(group.surfaces)) != len(group.surfaces):
            raise ValueError("coaxial group members must be distinct")
        for key in group.surfaces:
            fit = nodes.get(key)
            if not isinstance(fit, SurfaceFit) or fit.kind not in {"cone", "cylinder"}:
                raise ValueError("coaxial group requires cone or cylinder fit owners")
            existing = bindings.get(key, fit.axis)
            if existing is not None and existing != group.axis:
                raise ValueError(
                    f"{fit.label}: conflicting datum axes in coaxial relationships"
                )
            bindings[key] = group.axis
    return bindings


def fit_axis(
    node: SurfaceFit, nodes: dict[str, Feature], bindings: dict[str, str] | None = None
) -> str | None:
    bindings = coaxial_axis_bindings(nodes) if bindings is None else bindings
    if node.id in bindings:
        return bindings[node.id]
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
    bindings = coaxial_axis_bindings(nodes)
    declared_axes = set(bindings.values())
    for axis in nodes.values():
        if not isinstance(axis, AxisDefinition) or (
            axis.initial_parameters is None and axis.id not in declared_axes
        ):
            continue
        factors = [
            node
            for node in nodes.values()
            if isinstance(node, SurfaceFit)
            and fit_axis(node, nodes, bindings) == axis.id
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


def plane_relationship_endpoints(
    relationship: GeometryRelationship, nodes: dict[str, Feature]
) -> list[RelationshipEndpoint]:
    """Keep parameter bindings distinct from exact publication reads."""
    requirement = (
        "relationship_direction"
        if isinstance(relationship, DirectionAlignment)
        else (
            "relationship_plane_orientation"
            if relationship.relation == "parallel"
            else "relationship_plane"
        )
    )
    from experiments.relationship_inputs import expand_relationship_references

    if isinstance(relationship, DirectionAlignment):
        targets = expand_relationship_references(
            relationship.directions, nodes, "relationship_direction_targets"
        )
        result = (
            [
                normalize_endpoint(
                    relationship.reference, nodes, "relationship_direction_reference"
                )
            ]
            if relationship.reference is not None
            else []
        )
        for reference in targets:
            endpoint = normalize_endpoint(
                reference, nodes, "relationship_direction_targets"
            )
            if not any(previous.key == endpoint.key for previous in result):
                result.append(endpoint)
        return result
    return [
        normalize_endpoint(reference, nodes, requirement)
        for reference in relationship.surfaces
    ]


def automatic_plane_relationship_components(
    nodes: dict[str, Feature],
    *,
    include_radii: bool = True,
) -> dict[str, tuple[list[GeometryRelationship], list[SurfaceFit], set[str]]]:
    """Relationships join through shared geometry, not just plane feature IDs."""
    relationships = [
        node
        for node in nodes.values()
        if isinstance(node, (PlaneRelationship, DirectionAlignment))
    ]
    axis_bindings = coaxial_axis_bindings(nodes)
    seeds = [relationship.id for relationship in relationships]
    seeds.extend(
        node.id
        for node in nodes.values()
        if isinstance(node, PlaneDefinition)
        and node.construction == "independent"
        and any(
            isinstance(surface, SurfaceFit) and surface.reference_plane == node.id
            for surface in nodes.values()
        )
    )
    pending = set(seeds)
    endpoints = {
        relationship.id: plane_relationship_endpoints(relationship, nodes)
        for relationship in relationships
    }
    bindings = {
        key: {endpoint.owner for endpoint in values if endpoint.owner is not None}
        for key, values in endpoints.items()
    }
    bindings.update({key: {key} for key in seeds if key not in bindings})
    direction_owners = {
        owner
        for relation in relationships
        if isinstance(relation, DirectionAlignment)
        for owner in bindings[relation.id]
    }
    legacy_plane_owners = {
        owner
        for relation in relationships
        if isinstance(relation, PlaneRelationship)
        for owner in bindings[relation.id]
    }
    components: dict[
        str, tuple[list[GeometryRelationship], list[SurfaceFit], set[str]]
    ] = {}

    def linked_datums(owner_ids: set[str]) -> tuple[set[str], set[str]]:
        surfaces = [
            nodes[key] for key in owner_ids if isinstance(nodes[key], SurfaceFit)
        ]
        axes = {
            axis
            for surface in surfaces
            if (axis := fit_axis(cast(SurfaceFit, surface), nodes, axis_bindings))
            is not None
        }
        axes.update(
            node.axis
            for key in owner_ids
            if isinstance(node := nodes[key], PlaneDefinition)
            and key in legacy_plane_owners
            and node.axis is not None
        )
        axes.update(key for key in owner_ids if isinstance(nodes[key], AxisDefinition))
        points = {
            point
            for surface in surfaces
            if (point := cast(SurfaceFit, surface).point) is not None
        }
        axes.update(
            node.id
            for node in nodes.values()
            if isinstance(node, AxisDefinition) and node.source_fit in owner_ids
        )
        points.update(
            node.id
            for node in nodes.values()
            if isinstance(node, PointDefinition) and node.source_fit in owner_ids
        )
        # A fixed upstream datum is an input, not a shared variable. Source-fit
        # datums join only when that source fit itself is active in this solve.
        return (
            {
                key
                for key in axes
                if isinstance(axis := nodes[key], AxisDefinition)
                and (
                    axis.initial_parameters is not None
                    or (
                        axis.source_fit in owner_ids
                        and axis_bindings.get(axis.source_fit or "") != key
                    )
                )
            },
            {
                key
                for key in points
                if isinstance(point := nodes[key], PointDefinition)
                and (point.source_fit is None or point.source_fit in owner_ids)
            },
        )

    def adjustable_owners(owner_ids: set[str]) -> set[str]:
        axes, _ = linked_datums(owner_ids)
        return {
            key
            for key in owner_ids
            if not isinstance(node := nodes[key], PlaneDefinition)
            or node.construction == "independent"
            or node.axis in axes
            or key in direction_owners
        }

    while pending:
        first = next(key for key in seeds if key in pending)
        relation_ids = {first}
        owner_ids = bindings[first].copy()

        while True:
            owner_ids.update(
                node.reference_plane
                for key in tuple(owner_ids)
                if isinstance(node := nodes[key], SurfaceFit)
                and node.reference_plane is not None
                and cast(PlaneDefinition, nodes[node.reference_plane]).construction
                == "independent"
            )
            axes, points = linked_datums(owner_ids)
            owner_ids.update(
                node.id
                for node in nodes.values()
                if isinstance(node, SurfaceFit)
                and (
                    fit_axis(node, nodes, axis_bindings) in axes
                    or node.point in points
                    or (
                        node.reference_plane in owner_ids
                        and cast(
                            PlaneDefinition, nodes[node.reference_plane]
                        ).construction
                        == "independent"
                    )
                )
            )
            if include_radii:
                for node in nodes.values():
                    if isinstance(node, EqualRadii) and owner_ids.intersection(
                        node.surfaces
                    ):
                        owner_ids.update(node.surfaces)
            connected = {
                key
                for key in seeds
                if key in pending
                and (
                    adjustable_owners(owner_ids).intersection(
                        adjustable_owners(bindings[key])
                    )
                    or axes.intersection(linked_datums(bindings[key])[0])
                    or points.intersection(linked_datums(bindings[key])[1])
                )
            }
            expanded_owners = {
                owner
                for relationship_id in connected | relation_ids
                for owner in bindings[relationship_id]
            }
            expanded_owners.update(owner_ids)
            expanded_axes, expanded_points = linked_datums(expanded_owners)
            complete = all(
                not isinstance(node, SurfaceFit)
                or (
                    fit_axis(node, nodes, axis_bindings) not in expanded_axes
                    and node.point not in expanded_points
                    and not (
                        node.reference_plane in expanded_owners
                        and cast(
                            PlaneDefinition, nodes[node.reference_plane]
                        ).construction
                        == "independent"
                    )
                )
                or node.id in expanded_owners
                for node in nodes.values()
            )
            if connected <= relation_ids and expanded_owners == owner_ids and complete:
                break
            relation_ids.update(connected)
            owner_ids.update(expanded_owners)
        pending.difference_update(relation_ids)
        component_relationships = [
            relationship
            for relationship in relationships
            if relationship.id in relation_ids
        ]
        component_surfaces = [
            cast(SurfaceFit, nodes[node_id])
            for node_id in nodes
            if node_id in owner_ids and isinstance(nodes[node_id], SurfaceFit)
        ]
        axes, points = linked_datums(owner_ids)
        members = {
            *relation_ids,
            *owner_ids,
            *axes,
            *points,
            *(
                node.id
                for node in nodes.values()
                if isinstance(node, PlaneDefinition) and node.axis in axes
            ),
            *(
                node.id
                for node in nodes.values()
                if include_radii
                and isinstance(node, EqualRadii)
                and set(node.surfaces) <= owner_ids
            ),
        }
        if any(
            isinstance(relation, DirectionAlignment)
            for relation in component_relationships
        ):
            members.update(
                node.id
                for node in nodes.values()
                if isinstance(node, CoaxialGroup)
                and node.axis in axes
                and set(node.surfaces) <= owner_ids
            )
        for relationship_id in relation_ids:
            components[relationship_id] = (
                component_relationships,
                component_surfaces,
                members,
            )
    return components


def selected_plane_component(
    relations: list[GeometryRelationship],
    surfaces: list[SurfaceFit],
    nodes: dict[str, Feature],
) -> bool:
    """Independent plane owners consume raw evidence, like direction factors."""
    return any(
        isinstance(relation, DirectionAlignment) for relation in relations
    ) or any(
        isinstance(nodes[key], PlaneDefinition)
        and cast(PlaneDefinition, nodes[key]).construction == "independent"
        for key in {
            *(
                surface.reference_plane
                for surface in surfaces
                if surface.reference_plane
            ),
            *(
                endpoint.feature
                for relation in relations
                for endpoint in plane_relationship_endpoints(relation, nodes)
                if endpoint.is_binding
            ),
        }
    )


def active_direction_datum_source(
    key: str, surface_ids: set[str], nodes: dict[str, Feature]
) -> str | None:
    """A source-fit binding aliases its active source, never a second owner."""
    node = nodes[key]
    if not isinstance(node, (AxisDefinition, PointDefinition)):
        return None
    source = node.source_fit
    if source not in surface_ids:
        return None
    assert source is not None
    if (
        isinstance(node, AxisDefinition)
        and fit_axis(cast(SurfaceFit, nodes[source]), nodes) == key
    ):
        # A self-initialized Coaxial axis retains its upstream initializer role.
        return None
    return source


def direction_component_inputs(
    relations: list[GeometryRelationship],
    surfaces: list[SurfaceFit],
    nodes: dict[str, Feature],
) -> tuple[set[str], set[str]]:
    """Selected memberships precede a solve; fixed publications remain reads."""
    prerequisites = {ref for surface in surfaces for ref in surface.selections}
    surface_ids = {surface.id for surface in surfaces}

    def require_binding(key: str) -> None:
        node = nodes[key]
        if active_direction_datum_source(key, surface_ids, nodes) is not None:
            return
        if (
            isinstance(node, PlaneDefinition)
            and node.axis is not None
            and active_direction_datum_source(node.axis, surface_ids, nodes) is not None
        ):
            if node.placement is not None:
                prerequisites.add(node.placement.reuse)
            return
        prerequisites.add(key)

    reads: set[str] = set()
    for relation in relations:
        for endpoint in plane_relationship_endpoints(relation, nodes):
            if not endpoint.is_binding:
                prerequisites.add(endpoint.context)
                reads.add(endpoint.context)
            elif not isinstance(nodes[endpoint.feature], SurfaceFit):
                require_binding(endpoint.feature)
    for surface in surfaces:
        for ref in (fit_axis(surface, nodes), surface.point, surface.reference_plane):
            if ref is not None:
                require_binding(ref)
    return prerequisites, reads


@dataclass(frozen=True)
class ProviderInfluence:
    """One provider's coupling, execution inputs, and publication identities."""

    task: str
    solve: tuple[Literal["axis", "point", "planes"], str] | None
    members: frozenset[str]
    aliases: frozenset[str]
    prerequisites: frozenset[str]
    resolved_reads: frozenset[str]
    publications: dict[str, frozenset[str]]


@dataclass(frozen=True)
class GeometryInfluence:
    """Shared provider metadata with the numerical component payloads."""

    axis_components: dict[str, tuple[list[SurfaceFit], set[str]]]
    point_components: dict[str, tuple[list[SurfaceFit], set[str]]]
    plane_components: dict[
        str, tuple[list[GeometryRelationship], list[SurfaceFit], set[str]]
    ]
    providers: dict[str, ProviderInfluence]
    axis_bindings: dict[str, str]

    def memberships(self) -> dict[str, frozenset[str]]:
        # A plane group's first task identity can change when actions reorder.
        # Its real publishers retain stable identities across that change.
        return {
            publisher: provider.members
            for provider in self.providers.values()
            for publisher in provider.publications
        }


def describe_geometry_influence(nodes: dict[str, Feature]) -> GeometryInfluence:
    """Describe providers once; consumers retain their own closure semantics."""
    axes = automatic_axis_components(nodes)
    points = automatic_point_components(nodes)
    planes = automatic_plane_relationship_components(nodes)
    providers: dict[str, ProviderInfluence] = {}
    for axis, (_, members) in axes.items():
        task = "@axis/" + axis
        aliases = frozenset(members) | {
            node.id
            for node in nodes.values()
            if isinstance(node, PlaneDefinition) and node.axis == axis
        }
        if cast(AxisDefinition, nodes[axis]).initial_parameters is None:
            aliases = frozenset(
                key for key in aliases if isinstance(nodes[key], SurfaceFit)
            )
        groups = [
            node.id
            for node in nodes.values()
            if isinstance(node, CoaxialGroup) and node.axis == axis
        ]
        prerequisites = frozenset(members)
        if groups:
            composed = next(
                (entry for entry in planes.values() if axis in entry[2]), None
            )
            if composed is not None and not coaxial_seed_component(
                axis, composed, nodes
            ):
                composed = None
            selected = composed[1] if composed is not None else axes[axis][0]
            request = AxisSolve.model_construct(
                id=task,
                label="Connected coaxial solve",
                operation="axis_solve",
                axis=axis,
                factors=[
                    *groups,
                    *(surface.id for surface in selected),
                    *(
                        [relation.id for relation in composed[0]]
                        if composed is not None and selected is composed[1]
                        else []
                    ),
                ],
            )
            _, inputs, _ = selected_symmetry_inputs(request, nodes)
            prerequisites = frozenset(inputs | {axis})
            members = members | {surface.id for surface in selected}
            aliases = aliases | {surface.id for surface in selected}
        providers[task] = ProviderInfluence(
            task,
            ("axis", axis),
            frozenset(members),
            aliases,
            prerequisites,
            frozenset(),
            {task: aliases, **{group: aliases for group in groups}},
        )
    for point, (_, members) in points.items():
        task = "@point/" + point
        aliases = frozenset(members)
        providers[task] = ProviderInfluence(
            task,
            ("point", point),
            aliases,
            aliases,
            aliases,
            frozenset(),
            {task: aliases},
        )
    for key, members in automatic_equal_radius_components(nodes).items():
        relation = nodes[key]
        assert isinstance(relation, EqualRadii)
        aliases = frozenset(members)
        surfaces = frozenset(relation.surfaces)
        providers[key] = ProviderInfluence(
            key, None, aliases, aliases, surfaces, surfaces, {key: aliases}
        )
    for key, (relations, surfaces, members) in planes.items():
        first = relations[0].id if relations else key
        task = ("@planes/" if relations else "@plane/") + first
        if task in providers:
            continue
        relation_ids = frozenset(relation.id for relation in relations)
        selected_direction = selected_plane_component(relations, surfaces, nodes)
        aliases = frozenset(
            key
            for key in members
            if not isinstance(node := nodes[key], PlaneDefinition)
            or node.axis in members
            or selected_direction
        )
        exact_contexts = frozenset(
            endpoint.context
            for relation in relations
            for endpoint in plane_relationship_endpoints(relation, nodes)
            if not endpoint.is_binding
        )
        prerequisites, fixed_reads = direction_component_inputs(
            relations, surfaces, nodes
        )
        publishers = (relation_ids or frozenset({task})) | frozenset(
            key for key in members if isinstance(nodes[key], (EqualRadii, CoaxialGroup))
        )
        providers[task] = ProviderInfluence(
            task,
            ("planes", first),
            frozenset(members),
            aliases,
            frozenset(prerequisites)
            if selected_direction
            else (frozenset(members) - relation_ids) | exact_contexts,
            frozenset(fixed_reads)
            if selected_direction
            else frozenset(surface.id for surface in surfaces)
            | (frozenset(members) - aliases),
            {
                key: aliases - publishers
                for key in (publishers if selected_direction else relation_ids)
            },
        )
    # A direction component owns every intersecting variable factor. Keeping
    # an independent axis/radius publisher would overwrite its exact solution.
    direction_members = set().union(
        *(
            entry[2]
            for entry in planes.values()
            if selected_plane_component(entry[0], entry[1], nodes)
        )
    )
    providers = {
        key: provider
        for key, provider in providers.items()
        if (provider.solve and provider.solve[0] == "planes")
        or not provider.members.intersection(direction_members)
    }
    return GeometryInfluence(
        axes, points, planes, providers, coaxial_axis_bindings(nodes)
    )


def coaxial_seed_component(
    axis: str,
    component: tuple[list[GeometryRelationship], list[SurfaceFit], set[str]],
    nodes: dict[str, Feature],
) -> bool:
    """The bounded single-line adapter cannot replace general component roles."""
    relations, surfaces, _ = component
    if selected_plane_component(relations, surfaces, nodes):
        return False
    if any(surface.kind not in {"cone", "cylinder", "plane"} for surface in surfaces):
        return False
    endpoints = [
        endpoint
        for relation in relations
        for endpoint in plane_relationship_endpoints(relation, nodes)
    ]
    if any(not endpoint.is_binding for endpoint in endpoints):
        return False
    axes = {
        linked
        for surface in surfaces
        if (linked := fit_axis(surface, nodes)) is not None
    }
    axes.update(
        datum.axis
        for endpoint in endpoints
        if isinstance(datum := nodes[endpoint.feature], PlaneDefinition)
        and datum.axis is not None
    )
    return axes == {axis}


@dataclass(frozen=True)
class PriorGeometrySolve:
    """Reuse-local source geometry before constraints involving transferred fits."""

    reuse: str
    surfaces: list[SurfaceFit]
    planes: list[GeometryRelationship]
    radii: EqualRadii | None
    excluded: set[str]
    radius_groups: tuple[EqualRadii, ...] = ()
    standalone: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    """Compiled initialization tasks, solve barriers, and resolved-value reads."""

    order: tuple[str, ...]
    dependencies: dict[str, set[str]]
    resolved_reads: dict[str, set[str]]
    solves: dict[str, tuple[Literal["axis", "point", "planes"], str]]
    aliases: dict[str, set[str]]
    explicit_axes: set[str]
    prior_solves: dict[str, PriorGeometrySolve]
    read_exclusions: dict[str, set[str]]
    reuse_source_contexts: frozenset[str]


def demanded_reuse_source_contexts(nodes: dict[str, Feature]) -> frozenset[str]:
    """Find authored reads of a reuse's source ports or live collections."""
    demanded: set[str] = set()
    for node in nodes.values():
        references = (
            node.surfaces
            if isinstance(node, (PlaneRelationship, DirectionAlignment))
            else (node.origin_point, node.primary_reference, node.secondary_reference)
            if isinstance(node, FrameDefinition)
            else node.source_points or ()
            if isinstance(node, AxisDefinition)
            else tuple(
                ref
                for distance in node.distances
                for ref in (distance.first_point, distance.second_point)
            )
            if isinstance(node, ScaleDefinition)
            else ()
        )
        for reference in references:
            if not isinstance(reference, OutputReference):
                continue
            owner = nodes.get(reference.context or reference.feature)
            if isinstance(owner, FeatureReuse) and (
                reference.feature in owner.fits
                or (
                    reference.feature == owner.id
                    and reference.output.startswith("direction:")
                )
            ):
                demanded.add(owner.id)
    return frozenset(demanded)


def reuse_transfer_descendants(reuse: str, nodes: dict[str, Feature]) -> set[str]:
    after = {
        node.id
        for node in nodes.values()
        if isinstance(node, ReuseSelection) and node.reuse == reuse
    }
    after.update(
        node.id
        for node in nodes.values()
        if isinstance(node, DirectionAlignment)
        and any(
            isinstance(reference, OutputReference)
            and reference.feature == reuse
            and reference.output.startswith("direction:")
            for reference in node.directions
        )
    )
    while True:
        expanded = after | {
            node.id for node in nodes.values() if after.intersection(dependencies(node))
        }
        if expanded == after:
            return after
        after = expanded


def prior_stage_exclusions(node: Feature, nodes: dict[str, Feature]) -> set[str]:
    """Generated reuse dimensions constrain fits AFTER membership transfer.

    They must not feed back into the source geometry that generated those same
    memberships. Other resolved source relationships still participate.
    """
    if isinstance(node, FeatureReuse):
        reuse = node.id
    elif isinstance(node, ReuseSelection):
        reuse = node.reuse
    elif (
        isinstance(node, (AxisDefinition, PlaneDefinition))
        and node.placement is not None
    ):
        reuse = node.placement.reuse
    else:
        return set()
    after = reuse_transfer_descendants(reuse, nodes)
    excluded = {
        candidate.id
        for candidate in nodes.values()
        if isinstance(candidate, EqualRadii) and candidate.id in after
    }
    for relations, _, members in automatic_plane_relationship_components(
        nodes
    ).values():
        if members.intersection(after):
            excluded.update(relation.id for relation in relations)
    return excluded


def compile_execution_plan(
    recipe: Recipe,
    roots: set[str],
    influence: GeometryInfluence,
    *,
    selected_named_solves: bool = False,
) -> ExecutionPlan:
    """Build the whole execution graph before selecting a requested closure.

    Authoring edges alone omit later contributors to connected geometry. A
    solve publishes resolved aliases without making its own raw initializers
    depend on that publication. Explicit solves retain their named contexts and
    the adapter's existing request-local precedence over automatic axis solves.
    """
    nodes = {node.id: node for node in recipe.nodes}
    plane_components = influence.plane_components
    source_contexts = demanded_reuse_source_contexts(nodes)
    task_dependencies = {key: set(dependencies(node)) for key, node in nodes.items()}
    named_inputs = {
        node.id: selected_symmetry_inputs(node, nodes)
        for node in recipe.nodes
        if has_selected_symmetry(node, nodes)
        and (
            selected_named_solves
            or (
                isinstance(node, JointFit)
                and named_direction_component(node, nodes) is not None
            )
            or (
                isinstance(node, AxisSolve)
                and any(isinstance(nodes[key], CoaxialGroup) for key in node.factors)
            )
        )
    }
    for key, (_, prerequisites, _) in named_inputs.items():
        task_dependencies[key] = prerequisites.copy()
    for node in recipe.nodes:
        references = (
            (node.origin_point, node.primary_reference, node.secondary_reference)
            if isinstance(node, FrameDefinition)
            else node.source_points
            if isinstance(node, AxisDefinition) and node.source_points is not None
            else tuple(
                ref
                for distance in node.distances
                for ref in (distance.first_point, distance.second_point)
            )
            if isinstance(node, ScaleDefinition)
            else tuple(node.surfaces)
            if isinstance(node, (PlaneRelationship, DirectionAlignment))
            else None
        )
        if references is not None and any(
            isinstance(ref, OutputReference) and ref.context in named_inputs
            for ref in references
        ):
            task_dependencies[node.id] = {
                dep
                for ref in references
                for dep in (
                    [ref.context]
                    if isinstance(ref, OutputReference) and ref.context in named_inputs
                    else reference_dependencies(ref)
                )
            }
    initial = roots.copy()
    while True:
        expanded = initial | {dep for key in initial for dep in task_dependencies[key]}
        expanded.update(
            node.id
            for node in recipe.nodes
            if node.managed_by in expanded
            and isinstance(nodes.get(node.managed_by or ""), BuildFaces)
        )
        if expanded == initial:
            break
        initial = expanded
    explicit_axes = {
        node.axis for key in initial if isinstance(node := nodes[key], AxisSolve)
    }
    solves: dict[str, tuple[Literal["axis", "point", "planes"], str]] = {}
    aliases: dict[str, set[str]] = {}
    providers: dict[str, set[str]] = {}
    reads: dict[str, set[str]] = {}
    prior_solves: dict[str, PriorGeometrySolve] = {}
    read_exclusions: dict[str, set[str]] = {}

    for provider in influence.providers.values():
        if provider.solve is not None and (
            provider.solve[0] == "axis" and provider.solve[1] in explicit_axes
        ):
            continue
        task = provider.task
        if provider.solve is not None:
            solves[task] = provider.solve
        task_dependencies.setdefault(task, set()).update(provider.prerequisites)
        aliases[task] = set(provider.aliases)
        if provider.resolved_reads:
            reads[task] = set(provider.resolved_reads)
        for publisher, outputs in provider.publications.items():
            if publisher != task:
                task_dependencies[publisher].add(task)
            for output in outputs:
                providers.setdefault(output, set()).add(publisher)
        if (
            provider.solve
            and provider.solve[0] == "planes"
            and selected_plane_component(
                plane_components[provider.solve[1]][0],
                plane_components[provider.solve[1]][1],
                nodes,
            )
        ):
            for key in provider.aliases:
                if isinstance(nodes[key], SurfaceFit):
                    task_dependencies[key] = {task}

    # Coaxial declarations initialize their owned fits from the complete
    # selected problem, rather than requiring successful standalone members.
    # A fixed source-fit axis still needs its real raw initializer first.
    for task, (kind, axis) in solves.items():
        if kind != "axis" or axis not in influence.axis_bindings.values():
            continue
        definition = cast(AxisDefinition, nodes[axis])
        raw_source = definition.source_fit
        for key in aliases[task]:
            if isinstance(nodes[key], SurfaceFit) and key != raw_source:
                task_dependencies[key].add(task)

    for node in recipe.nodes:
        if (
            isinstance(node, (AxisDefinition, PlaneDefinition))
            and node.placement is not None
        ):
            reads[node.id] = {node.placement.source}
        elif isinstance(node, (FrameDefinition, ScaleDefinition)):
            references = (
                (node.origin_point, node.primary_reference, node.secondary_reference)
                if isinstance(node, FrameDefinition)
                else tuple(
                    ref
                    for distance in node.distances
                    for ref in (distance.first_point, distance.second_point)
                )
            )
            reads[node.id] = {ref for ref in references if isinstance(ref, str)}
            task_dependencies[node.id].update(
                ref.context
                for ref in references
                if isinstance(ref, OutputReference)
                and ref.context
                and ref.context.startswith("@")
            )
        elif isinstance(node, TransformDefinition):
            reads[node.id] = set(dependencies(node))
        elif isinstance(node, AxisDefinition) and node.source_points is not None:
            reads[node.id] = {ref for ref in node.source_points if isinstance(ref, str)}
            task_dependencies[node.id].update(
                ref.context
                for ref in node.source_points
                if isinstance(ref, OutputReference)
                and ref.context
                and ref.context.startswith("@")
            )
        elif isinstance(node, SelectionRegion):
            reads[node.id] = {
                node.fit,
                node.axial_plane,
                node.clock_plane,
                selection_frame_axis(node.axial_plane, node.clock_plane, nodes),
            }
        elif isinstance(node, RegionSelection):
            reads[node.id] = {
                node.axial_plane,
                node.clock_plane,
                selection_frame_axis(node.axial_plane, node.clock_plane, nodes),
            }
        elif isinstance(node, ReuseSelection):
            reads[node.id] = {node.fit}
        elif isinstance(node, FeatureReuse) and node.id in source_contexts:
            reads[node.id] = {
                *node.fits,
                *([node.circular.axis] if node.circular is not None else []),
            }
        elif isinstance(node, FeatureReuse) and node.circular is not None:
            reads[node.id] = {node.circular.axis}
        elif isinstance(node, SurfaceIntersection):
            reads[node.id] = {node.first.feature, node.second.feature}
        elif isinstance(node, TrimmedFace):
            reads[node.id] = {node.surface.feature}
        elif isinstance(node, ArrangedFace):
            reads[node.id] = {
                node.surface.feature,
                *(ref.feature for ref in node.cutters),
            }
        elif isinstance(node, BuildFaces):
            reads[node.id] = {reference.feature for reference in node.surfaces}
        elif isinstance(node, AxisSolve):
            reads[node.id] = (
                named_inputs[node.id][2].copy()
                if node.id in named_inputs
                else {
                    cast(MirrorSymmetry, nodes[factor]).plane
                    for factor in node.factors
                    if isinstance(nodes[factor], MirrorSymmetry)
                }
            )
        if node.id in named_inputs:
            reads[node.id] = named_inputs[node.id][2].copy()

    # Preserve source-only constraints when a later transfer extends their
    # component. These results have a reuse-local port, never a global overlay.
    for node in recipe.nodes:
        if not isinstance(node, (ReuseSelection, FeatureReuse)):
            continue
        if isinstance(node, FeatureReuse) and node.id not in source_contexts:
            continue
        reuse_id = node.id if isinstance(node, FeatureReuse) else node.reuse
        source_ids = set(node.fits) if isinstance(node, FeatureReuse) else {node.fit}
        excluded = prior_stage_exclusions(node, nodes)
        if not excluded:
            continue
        after = reuse_transfer_descendants(reuse_id, nodes)
        prior_nodes: dict[str, Feature] = {}
        joint_prior_radii: set[str] = set()
        for key, value in nodes.items():
            if (
                isinstance(value, (PlaneRelationship, DirectionAlignment, SurfaceFit))
                and key in after
            ):
                continue
            if isinstance(value, EqualRadii):
                prior_members = [ref for ref in value.surfaces if ref not in after]
                if len(prior_members) < 2:
                    continue
                value = value.model_copy(update={"surfaces": prior_members})
            prior_nodes[key] = value
        for relations, surfaces, members in automatic_plane_relationship_components(
            prior_nodes
        ).values():
            if not source_ids.intersection(
                {surface.id for surface in surfaces}
            ) or not excluded.intersection(relation.id for relation in relations):
                continue
            task = "@reuse_planes/" + reuse_id + "/" + relations[0].id
            surface_ids = {surface.id for surface in surfaces}
            radius_groups = tuple(
                relation
                for relation in prior_nodes.values()
                if isinstance(relation, EqualRadii)
                and set(relation.surfaces) <= surface_ids
            )
            prior_solves[task] = PriorGeometrySolve(
                reuse_id, surfaces, relations, None, excluded, radius_groups
            )
            joint_prior_radii.update(relation.id for relation in radius_groups)
            task_dependencies[task] = (
                members
                - {relation.id for relation in relations}
                - {relation.id for relation in radius_groups if relation.id in excluded}
            )
            if selected_plane_component(relations, surfaces, prior_nodes):
                task_dependencies[task] = direction_component_inputs(
                    relations, surfaces, prior_nodes
                )[0]
            reads[task] = {surface.id for surface in surfaces}
            task_dependencies[node.id].difference_update(
                {
                    *surface_ids,
                    *(relation.id for relation in relations),
                    *(relation.id for relation in radius_groups),
                }
            )
            task_dependencies[node.id].add(task)
        for key in excluded:
            if key in joint_prior_radii:
                # The joint prior port already satisfies this radius equality.
                # A later independent radius overlay would restore old axes.
                continue
            relation = nodes[key]
            if not isinstance(relation, EqualRadii) or not source_ids.intersection(
                relation.surfaces
            ):
                continue
            surfaces = [
                cast(SurfaceFit, nodes[ref])
                for ref in relation.surfaces
                if ref not in after
            ]
            if len(surfaces) < 2:
                continue
            task = "@reuse_radii/" + reuse_id + "/" + relation.id
            prior_relation = relation.model_copy(
                update={"surfaces": [surface.id for surface in surfaces]}
            )
            prior_solves[task] = PriorGeometrySolve(
                reuse_id, surfaces, [], prior_relation, excluded
            )
            task_dependencies[task] = {surface.id for surface in surfaces}
            reads[task] = task_dependencies[task].copy()
            task_dependencies[node.id].add(task)

    # A one-template equality has no earlier multi-member relation to retain.
    # Give its raw source fit a private stage before the downstream component
    # replaces the ordinary fit task and consumes transferred observations.
    for owner in recipe.nodes:
        if not isinstance(owner, FeatureReuse) or owner.id not in source_contexts:
            continue
        after = reuse_transfer_descendants(owner.id, nodes)
        provided = {
            surface.id
            for stage in prior_solves.values()
            if stage.reuse == owner.id
            for surface in stage.surfaces
        }
        for source_id in set(owner.fits) - provided:
            if not any(
                source_id in provider.aliases
                and provider.aliases.intersection(after)
                and provider.solve is not None
                and provider.solve[0] == "planes"
                for provider in influence.providers.values()
            ):
                continue
            source = cast(SurfaceFit, nodes[source_id])
            if source.axis or source.point or source.reference_plane:
                raise ValueError(
                    "This pattern source needs an explicit upstream relationship stage before constraining its copies"
                )
            task = "@reuse_fit/" + owner.id + "/" + source_id
            prior_solves[task] = PriorGeometrySolve(
                owner.id,
                [source],
                [],
                None,
                prior_stage_exclusions(owner, nodes),
                standalone=True,
            )
            task_dependencies[task] = set(dependencies(source))
            for consumer in recipe.nodes:
                if consumer.id == owner.id or (
                    isinstance(consumer, ReuseSelection)
                    and consumer.reuse == owner.id
                    and consumer.fit == source_id
                ):
                    task_dependencies[consumer.id].discard(source_id)
                    task_dependencies[consumer.id].add(task)

    for task, references in reads.items():
        # A constrained solve consumes prior-stage values, never its own output.
        excluded = {task}
        if task in nodes:
            node = nodes[task]
            if not isinstance(node, FeatureReuse) or task in source_contexts:
                excluded.update(prior_stage_exclusions(node, nodes))
            if isinstance(node, EqualRadii):
                excluded.update(
                    relation.id
                    for relations, surfaces, _ in plane_components.values()
                    if set(node.surfaces).intersection(
                        surface.id for surface in surfaces
                    )
                    for relation in relations
                )
        if task in prior_solves:
            excluded.update(prior_solves[task].excluded)
        if task in solves and solves[task][0] == "planes":
            excluded.update(
                relation.id for relation in plane_components[solves[task][1]][0]
            )
        task_dependencies[task].update(
            *(providers.get(ref, set()) - excluded for ref in references)
        )
        read_exclusions[task] = excluded

    # Track implicit publication demand by root path. An exact output edge
    # brings only its named context; an independent legacy path to the same
    # physical initializer still retains its implicit solved publications.
    implicit_initial = roots.copy()
    pending_implicit = list(roots)
    while pending_implicit:
        node = nodes[pending_implicit.pop()]
        references = (
            (node.origin_point, node.primary_reference, node.secondary_reference)
            if isinstance(node, FrameDefinition)
            else node.source_points
            if isinstance(node, AxisDefinition) and node.source_points is not None
            else tuple(
                ref
                for distance in node.distances
                for ref in (distance.first_point, distance.second_point)
            )
            if isinstance(node, ScaleDefinition)
            else tuple(
                endpoint.owner if endpoint.is_binding else endpoint.reference
                for endpoint in plane_relationship_endpoints(node, nodes)
            )
            if isinstance(node, (PlaneRelationship, DirectionAlignment))
            else None
        )
        implicit_dependencies = (
            named_inputs[node.id][1]
            if node.id in named_inputs
            else dependencies(node)
            if references is None
            else [ref for ref in references if isinstance(ref, str)]
        )
        for dep in implicit_dependencies:
            if dep not in implicit_initial:
                implicit_initial.add(dep)
                pending_implicit.append(dep)
    needed = initial | {
        provider for key in implicit_initial for provider in providers.get(key, set())
    }
    while True:
        expanded = needed | {dep for key in needed for dep in task_dependencies[key]}
        expanded.update(
            node.id
            for node in recipe.nodes
            if node.managed_by in expanded
            and isinstance(nodes.get(node.managed_by or ""), BuildFaces)
        )
        if expanded == needed:
            break
        needed = expanded
    pending = [key for key in task_dependencies if key in needed]
    ordered: list[str] = []
    completed: set[str] = set()
    while pending:
        key = next(
            (key for key in pending if task_dependencies[key] <= completed), None
        )
        if key is None:
            # Follow prerequisite edges to show an actual cycle, not every node
            # merely blocked behind it. Stable recipe/task priority is retained.
            path: list[str] = []
            key = pending[0]
            while key not in path:
                path.append(key)
                key = next(
                    candidate
                    for candidate in pending
                    if candidate in task_dependencies[key]
                )
            cycle = [*path[path.index(key) :], key]

            def label(task: str) -> str:
                if task in nodes:
                    return nodes[task].label
                if task in prior_solves:
                    return f"prior source geometry for {nodes[prior_solves[task].reuse].label}"
                kind, identifier = solves[task]
                return f"{kind} solve for {nodes[identifier].label}"

            raise ValueError(
                "geometry influence cycle: " + " → ".join(label(task) for task in cycle)
            )
        pending.remove(key)
        ordered.append(key)
        completed.add(key)
    return ExecutionPlan(
        tuple(ordered),
        task_dependencies,
        reads,
        solves,
        aliases,
        explicit_axes,
        prior_solves,
        read_exclusions,
        source_contexts,
    )


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
    assert axial_plane.axis is not None
    return axial_plane.axis


def selection_source(node: Feature, nodes: dict[str, Feature]) -> str:
    if isinstance(node, Selection):
        return node.source
    if isinstance(node, RegionSelection):
        return node.source
    if isinstance(node, SurfaceSelection):
        return selection_source(nodes[node.seed_selection], nodes)
    if isinstance(node, ReuseSelection):
        return selection_source(
            nodes[
                node.source_selection
                if node.occurrence is not None
                else node.target_selection
            ],
            nodes,
        )
    if isinstance(node, Growth):
        fitted = nodes[node.seed_fit]
        if isinstance(fitted, SurfaceFit):
            sources = {selection_source(nodes[key], nodes) for key in fitted.selections}
            if len(sources) == 1:
                return next(iter(sources))
    raise ValueError("expected selections from one source")


def discover_reuse_lineage(fit_ids: list[str], nodes: dict[str, Feature]) -> list[str]:
    """Return copy inputs and enclosed relationships within this occurrence.

    Earlier occurrence providers remain ordinary graph dependencies. Their
    prototypes and placement inputs are provenance, not additional fits to copy.
    """
    selected = set(fit_ids)
    closure = set(fit_ids)

    def add_upstream() -> bool:
        before = len(closure)
        for key in tuple(closure):
            node = nodes[key]
            if isinstance(node, ReuseSelection):
                continue
            if isinstance(node, (AxisDefinition, PlaneDefinition)) and node.placement:
                if isinstance(node, PlaneDefinition) and node.axis is not None:
                    closure.add(node.axis)
                continue
            closure.update(dependencies(node))
        return len(closure) != before

    while add_upstream():
        pass
    relationship_types = (
        Perpendicular,
        Coaxial,
        CoaxialGroup,
        RotationalSymmetry,
        JointFit,
        MirrorSymmetry,
        ParallelToPlane,
        EqualQuantities,
        EqualRadii,
        PlaneRelationship,
        DirectionAlignment,
        AxisSolve,
    )

    def referenced_fits(candidate: Feature) -> set[str]:
        if isinstance(candidate, (PlaneRelationship, DirectionAlignment)):
            return {
                endpoint.owner
                for endpoint in plane_relationship_endpoints(candidate, nodes)
                if endpoint.owner is not None
                and isinstance(nodes[endpoint.owner], SurfaceFit)
            }
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


def has_selected_symmetry(node: Feature, nodes: dict[str, Feature]) -> bool:
    """The imported named paths with bounded observation-only initialization."""
    return (
        isinstance(node, JointFit)
        and any(
            isinstance(nodes[key], (RotationalSymmetry, DirectionAlignment))
            for key in node.constraints
        )
    ) or (
        isinstance(node, AxisSolve)
        and any(
            isinstance(nodes[key], (MirrorSymmetry, CoaxialGroup, PlaneRelationship))
            for key in node.factors
        )
    )


def named_direction_component(
    node: JointFit, nodes: dict[str, Feature]
) -> tuple[list[GeometryRelationship], list[SurfaceFit], set[str]] | None:
    if not any(isinstance(nodes[key], DirectionAlignment) for key in node.constraints):
        return None
    if any(
        not isinstance(
            nodes[key], (DirectionAlignment, PlaneRelationship, EqualRadii, SurfaceFit)
        )
        for key in node.constraints
    ):
        raise ValueError(
            "direction joints require direction, plane, or radius relationship factors"
        )
    selected = {
        key: value
        for key, value in nodes.items()
        if not isinstance(value, (DirectionAlignment, PlaneRelationship, EqualRadii))
        or key in node.constraints
    }
    components = automatic_plane_relationship_components(selected)
    first = next(
        key for key in node.constraints if isinstance(nodes[key], DirectionAlignment)
    )
    component = components[first]
    if not set(node.constraints) <= component[2]:
        raise ValueError(
            "joint direction factors must form one connected variable component"
        )
    return component


def selected_symmetry_inputs(
    node: Feature, nodes: dict[str, Feature]
) -> tuple[list[SurfaceFit], set[str], set[str]]:
    """Compile memberships and hard providers without admitting raw members.

    Manual free axes and clocks supply disposable starts. Placed declarations
    still need their actual registration/source stage; fixed fit/point providers
    remain mandatory, independently evaluated inputs.
    """
    axes: set[str] = set()
    planes: set[str] = set()
    if isinstance(node, JointFit):
        component = named_direction_component(node, nodes)
        if component is not None:
            relations, surfaces, _ = component
            prerequisites, reads = direction_component_inputs(
                relations, surfaces, nodes
            )
            return surfaces, prerequisites, reads
        sides, others = joint_surfaces(node, nodes)
        surfaces = [*sides, *others]
    elif isinstance(node, AxisSolve):
        axes.add(node.axis)
        members: dict[str, SurfaceFit] = {}
        for key in node.factors:
            factor = nodes[key]
            if isinstance(factor, SurfaceFit):
                members[key] = factor
            elif isinstance(factor, CoaxialGroup):
                axes.add(factor.axis)
                members.update(
                    (ref, cast(SurfaceFit, nodes[ref])) for ref in factor.surfaces
                )
            elif isinstance(factor, PlaneRelationship):
                for endpoint in plane_relationship_endpoints(factor, nodes):
                    if not endpoint.is_binding:
                        raise ValueError(
                            "named plane factors require direct parameter owners"
                        )
                    owner = nodes[endpoint.feature]
                    if isinstance(owner, SurfaceFit):
                        members[owner.id] = owner
                    elif isinstance(owner, PlaneDefinition):
                        planes.add(owner.id)
            elif isinstance(factor, MirrorSymmetry):
                planes.add(factor.plane)
                members.update(
                    (ref, cast(SurfaceFit, nodes[ref])) for ref in factor.surfaces
                )
            elif isinstance(factor, ParallelToPlane):
                planes.add(factor.reference_plane)
            elif isinstance(factor, EqualQuantities):
                planes.add(equality_measurements(factor)[1].reference_plane)
        surfaces = list(members.values())
    else:
        raise ValueError("expected a named symmetry solve")
    prerequisites = {ref for surface in surfaces for ref in surface.selections}
    for surface in surfaces:
        axis = fit_axis(surface, nodes)
        if axis is not None:
            axes.add(axis)
        if surface.reference_plane is not None:
            planes.add(surface.reference_plane)
    for key in planes:
        plane = cast(PlaneDefinition, nodes[key])
        if plane.axis is None:
            raise ValueError(
                "independent reference planes require a direction joint, not a legacy shared-axis solve"
            )
        axes.add(plane.axis)
    hard: set[str] = set()
    for key in axes:
        axis = cast(AxisDefinition, nodes[key])
        if axis.initial_parameters is None or axis.placement is not None:
            hard.add(key)
    for key in planes:
        plane = cast(PlaneDefinition, nodes[key])
        assert plane.axis is not None
        axis = cast(AxisDefinition, nodes[plane.axis])
        if axis.initial_parameters is None or plane.placement is not None:
            hard.add(key)
    return surfaces, prerequisites | hard, hard


def joint_surfaces(
    node: JointFit, nodes: dict[str, Feature]
) -> tuple[list[SurfaceFit], list[SurfaceFit]]:
    """Compile a connected constraint group; independently offset perpendicular planes."""
    if len(set(node.constraints)) != len(node.constraints):
        raise ValueError("duplicate joint constraint")
    component = named_direction_component(node, nodes)
    if component is not None:
        return (
            [surface for surface in component[1] if surface.kind != "plane"],
            [surface for surface in component[1] if surface.kind == "plane"],
        )
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


class UnsupportedSupportTransfer(ValueError):
    """Historical generated bounds need an explicit support authority choice."""

    def __init__(self, fit: str, reuse: str) -> None:
        self.diagnostic: dict[str, Any] = {
            "kind": "unsupported_support_transfer",
            "code": "unsupported-support",
            "fit": fit,
            "reuse": reuse,
            "issue": 264,
        }
        super().__init__(
            "Choose support from the prototype or local target bounds for this historical generated fit (#264)"
        )


def prototype_support_source(
    surface: SurfaceFit, nodes: dict[str, Feature]
) -> tuple[FeatureReuse, SurfaceFit, str]:
    """Verify the complete saved provenance before inheriting hard support."""
    owner = nodes.get(surface.managed_by or "")
    key = (surface.managed_key or "").split("/")
    if not isinstance(owner, FeatureReuse) or len(key) != 3 or key[0] != "fit":
        raise ValueError(
            "prototype support requires an exact generated reuse owner/key"
        )
    occurrence, source_id = key[1:]
    source = nodes.get(source_id)
    if (
        not isinstance(source, SurfaceFit)
        or source_id not in owner.fits
        or occurrence not in owner.target_selections
        or source.kind != surface.kind
    ):
        raise ValueError(
            "prototype support requires its declared source fit and occurrence"
        )
    source_selections: list[str] = []
    for reference in surface.selections:
        selected = nodes[reference]
        if not isinstance(selected, ReuseSelection) or (
            selected.managed_by != owner.id
            or selected.reuse != owner.id
            or selected.fit != source.id
            or selected.target_selection != occurrence
            or selected.source_selection not in source.selections
            or selected.managed_key
            != f"selection/{occurrence}/{source.id}/{selected.source_selection}"
        ):
            raise ValueError(
                "prototype support requires exact reuse selection provenance"
            )
        source_selections.append(selected.source_selection)
    if source_selections != source.selections:
        raise ValueError(
            "prototype support requires all source selections in occurrence order"
        )
    return owner, source, occurrence


def failure_diagnostic(error: Exception) -> dict[str, Any] | None:
    """Keep solver evidence separate from geometry and safe for strict JSON."""
    if isinstance(error, SelectionOverlap):
        return deepcopy(error.diagnostic)
    if isinstance(
        error,
        (
            PlaneFitFailure,
            SphereFitFailure,
            CylinderFitFailure,
            ConeFitFailure,
            UnsupportedSupportTransfer,
            SupportIntentFailure,
            SymmetryFitFailure,
            DirectionFitFailure,
            RelationshipFailure,
        ),
    ):
        return deepcopy(error.diagnostic)
    if isinstance(error, RecoveryFailure):
        return {
            "kind": "fit_recovery_failure",
            "code": error.code,
            "recovery": error.diagnostics.json(),
        }
    if not isinstance(error, ConstrainedLeastSquaresFailure):
        return None

    def json_value(value: Any) -> Any:
        if isinstance(value, float) and not math.isfinite(value):
            if math.isnan(value):
                return "NaN"
            return "Infinity" if value > 0 else "-Infinity"
        if isinstance(value, (tuple, list)):
            return [json_value(item) for item in value]
        if isinstance(value, dict):
            return {key: json_value(item) for key, item in value.items()}
        return value

    return {
        "kind": "constrained_solver_failure",
        "code": error.code,
        "solver": (
            json_value(asdict(error.diagnostics))
            if error.diagnostics is not None
            else None
        ),
    }


def workspace_reference_sha256(workspace: Workspace) -> str:
    return workspace.reference_sha256


def example_workspace(workspace: Workspace) -> NozzleWorkspace:
    """Explicit boundary until the fit-family integrations support user scans."""
    if isinstance(workspace, ScanWorkspace):
        raise ValueError("This fitting path is not yet supported for imported scans")
    return cast(NozzleWorkspace, workspace)


def directed_axis_result(
    node: AxisDefinition,
    *,
    parameters: list[float] | None = None,
    geometry: dict[str, Any] | None = None,
    geometry_is_directed: bool = False,
    first_point: dict[str, Any] | None = None,
    second_point: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve an explicitly directed axis without assuming it has a usable Z slope."""
    if geometry is not None:
        if "analytic" in geometry:
            analytic = revolution_geometry(geometry)
            point = vector(analytic["origin"], "axis anchor")
            direction = unit(analytic["axis"], "axis direction")
        else:
            point = vector(geometry["point_display"], "axis anchor")
            direction = unit(geometry["axis_display"], "axis direction")
        legacy_parameters = None
    elif node.source_points is not None:
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
    if node.direction_reversed and not geometry_is_directed:
        direction = -direction
    return {
        "source_fit": node.source_fit,
        "source_points": [reference_json(ref) for ref in node.source_points]
        if node.source_points
        else None,
        "parameters": legacy_parameters,
        "axis_display": direction.tolist(),
        "point_display": point.tolist(),
        "direction_reversed": node.direction_reversed,
    }


def connected_axis_result(
    node: AxisDefinition, prior: dict[str, Any], solve: dict[str, Any]
) -> dict[str, Any]:
    """Publish canonical group geometry without reapplying its directed sign."""
    initialized = initialized_axis(node, prior)
    if solve.get("axis_is_directed"):
        return directed_axis_result(
            initialized, geometry=solve, geometry_is_directed=True
        )
    return directed_axis_result(initialized, parameters=solve["fit"]["parameters"])


def initialized_axis(node: AxisDefinition, result: dict[str, Any]) -> AxisDefinition:
    if node.placement is not None and "direction_reversed" in result:
        return node.model_copy(
            update={"direction_reversed": result["direction_reversed"]}
        )
    return node


def initialized_plane(node: PlaneDefinition, result: dict[str, Any]) -> PlaneDefinition:
    return node.model_copy(update=result.get("reuse_initializer", {}))


def reference_plane_result(
    axis_result: dict[str, Any] | None, plane: PlaneDefinition
) -> dict[str, Any]:
    if plane.construction == "independent":
        assert plane.initial_normal is not None
        normal: NDArray[np.float64] = np.asarray(plane.initial_normal, dtype=float)
        normal /= np.max(np.abs(normal))
        normal /= np.linalg.norm(normal)
        coordinate = np.eye(3, dtype=float)[int(np.argmin(np.abs(normal)))]
        basis_u: NDArray[np.float64] = np.asarray(
            np.cross(normal, coordinate), dtype=float
        )
        basis_u /= np.linalg.norm(basis_u)
        basis_v: NDArray[np.float64] = np.cross(normal, basis_u)
        point: NDArray[np.float64] = plane.offset * normal
        return {
            "basis_u_display": basis_u.tolist(),
            "basis_v_display": basis_v.tolist(),
            "point_display": point.tolist(),
            "normal_display": normal.tolist(),
            "plane_equation": [*normal.tolist(), plane.offset],
            "angle_degrees": None,
            "offset": plane.offset,
            "construction": plane.construction,
        }
    assert axis_result is not None
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
        first = np.asarray(
            resolved[input_reference_key(distance.first_point)]["point_display"],
            dtype=float,
        )
        second = np.asarray(
            resolved[input_reference_key(distance.second_point)]["point_display"],
            dtype=float,
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
    origin = np.asarray(
        resolved[input_reference_key(node.origin_point)]["point_display"], dtype=float
    )
    primary = reference_direction(
        resolved[input_reference_key(node.primary_reference)], node.label
    )
    secondary = reference_direction(
        resolved[input_reference_key(node.secondary_reference)], node.label
    )
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
        "primary_reference": reference_json(node.primary_reference),
        "primary_output_axis": node.primary_output_axis,
        "secondary_reference": reference_json(node.secondary_reference),
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
    workspace: Workspace,
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
    workspace: Workspace,
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
    weights = normalized_weights(workspace.data.weights[ids])
    distances = np.linalg.norm(workspace.local[ids] - center, axis=1)
    radius = float(weights @ distances)
    if not np.isfinite(radius) or radius <= 0:
        raise ValueError(f"{surface.label}: sphere radius must be positive")
    residuals = distances - radius
    return {
        "kind": "sphere",
        "ids": ids,
        "parameters": [*center.tolist(), radius],
        "axial_domain": surface.axial_domain,
        "residuals": residuals.tolist(),
        "weighted_rms": float(np.sqrt(weights @ residuals**2)),
        "condition": 1.0,
        "point": surface.point,
    }


def fit_spheres_to_free_point(
    workspace: Workspace,
    point_id: str,
    surfaces: list[SurfaceFit],
    ids: list[list[int]],
    initial_center: np.ndarray | None = None,
) -> dict[str, Any]:
    """Derive a shared free center; preserve authored coordinates as optional hints."""
    points = [workspace.local[surface_ids] for surface_ids in ids]
    weights = [workspace.data.weights[surface_ids] for surface_ids in ids]
    descriptor = workspace.source_descriptor
    source_sha256 = workspace.source_sha256
    if source_sha256 is None:
        raise ValueError("point-bound spheres require an accepted source")
    binding = initialization_binding(
        source_sha256,
        descriptor.interpretation_sha256
        if descriptor is not None
        else workspace.reference_sha256,
        list(dict.fromkeys(vertex for group in ids for vertex in group)),
        {
            "family": "sphere",
            "point": {"id": point_id, "role": "free-shared-center"},
            "surfaces": [surface.model_dump(mode="json") for surface in surfaces],
            "memberships": ids,
        },
        policy_version="joint-algebraic-shared-sphere-v1",
    )
    fitted = fit_shared_sphere_center(
        points, weights, binding, hint_center=initial_center
    )
    parameters = np.asarray(fitted["parameters"])
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
                np.sqrt(normalized_weights(surface_weights) @ residuals**2)
            ),
            "condition": fitted["normal_matrix_condition"],
            "solver": fitted["solver"],
            "initialization": fitted["initialization"],
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
    workspace: Workspace,
    surfaces: list[SurfaceFit],
    results: list[dict[str, Any]],
    ids: list[list[int]],
) -> dict[str, Any]:
    """Fit one exact radius while retaining each surface's position and orientation."""
    radial_observations: list[np.ndarray] = []
    observation_weights: list[np.ndarray] = []
    for surface, result, surface_ids in zip(surfaces, results, ids, strict=True):
        if surface.kind == "cylinder":
            geometry = revolution_geometry(result)
            direction = np.asarray(geometry["axis"])
            point = np.asarray(geometry["origin"])
            offset = workspace.local[surface_ids] - point
            axial = offset @ direction
            radial = offset - axial[:, None] * direction
            radial_observations.append(np.linalg.norm(radial, axis=1))
        elif surface.kind == "sphere":
            parameters = np.asarray(result["parameters"], dtype=float)
            if parameters.shape != (4,) or not np.isfinite(parameters).all():
                raise ValueError(f"{surface.label}: fit result is unavailable")
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
        updated = deepcopy(result)
        if surface.kind == "cylinder" and "analytic" in result:
            updated["analytic"] = {**revolution_geometry(result), "radius": radius}
        else:
            parameters = list(result["parameters"])
            parameters[4 if surface.kind == "cylinder" else 3] = radius
            updated["parameters"] = parameters
        residuals = radial - radius
        adjusted[surface.id] = {
            **updated,
            "ids": surface_ids,
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
    workspace: Workspace,
    relationships: list[PlaneRelationship],
    surfaces: list[SurfaceFit],
    results: list[dict[str, Any]],
    ids: list[list[int]],
) -> dict[str, Any]:
    """Solve a connected set of exact coincident/parallel plane constraints."""
    nodes: dict[str, Feature] = {surface.id: surface for surface in surfaces}
    relation_surfaces = {
        relationship.id: [
            endpoint.feature
            for endpoint in plane_relationship_endpoints(relationship, nodes)
        ]
        for relationship in relationships
    }
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
        anchor = relation_surfaces[relationship.id][0]
        for surface_id in relation_surfaces[relationship.id][1:]:
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


@dataclass
class SharedBoundaryInputs:
    face_ids: list[str]
    faces: list[dict[str, Any]]
    target: dict[str, Any]
    sources: list[dict[str, Any]]
    geometry: dict[str, Any]


@final
@dataclass(frozen=True)
class GraphFailureCheckpoint:
    """Failure-only recovery evidence; contains no numerical or native caches."""

    recipe: Recipe
    token: str
    states: dict[str, str]
    errors: dict[str, str]
    diagnostics: dict[str, dict[str, Any]]
    volume_refresh_errors: dict[str, tuple[str, str]] = field(default_factory=dict)


WorkspaceType = TypeVar(
    "WorkspaceType", bound=Workspace, default=NozzleWorkspace, covariant=True
)


def recipe_content_token(recipe: Recipe) -> str:
    """Hash semantic content independently of Pydantic's JSON number spelling.

    Native and browser Pydantic versions can spell the same float as 1e+100
    and 1e100. Tokens must bind the same authored actions in either runtime.
    """
    encoded = json.dumps(
        recipe.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


@final
class FeatureGraph(Generic[WorkspaceType]):
    def __init__(
        self,
        workspace: WorkspaceType,
        recipe: Recipe,
        *,
        refresh_volumes: bool = True,
        fit_intent: dict[str, Any] | None = None,
        fit_executor: Executor | None = None,
    ) -> None:
        self._workspace = workspace
        self._refresh_volumes = refresh_volumes
        self._fit_intent = deepcopy(fit_intent)
        # The native coordinator may inject a bounded numerical worker pool.
        # Browser graphs and direct callers retain serial evaluation by default.
        self.fit_executor: Executor | None = fit_executor
        self._evaluation_origin_token: str | None = None
        self._volume_refresh_errors: dict[str, tuple[str, str]] = {}
        # Generic sources bind content/interpretation; retained example adapters
        # preserve their existing explicit legacy reference identity.
        self.reference_sha256 = workspace_reference_sha256(workspace)
        self.lock = RLock()
        self._volume_cache: VolumeCache = VolumeCache()
        self._recipe = self.validate(recipe)
        self._content_token = recipe_content_token(self._recipe)
        self._revision: int = 0  # Observable publications, independent of recipe edits.
        self._states: dict[str, str] = dict.fromkeys(
            (n.id for n in recipe.nodes), "unevaluated"
        )
        self._results: dict[str, Any] = {}
        self._connected_solves: dict[str, dict[str, Any]] = {}
        self._connected_point_solves: dict[str, dict[str, Any]] = {}
        self._derived: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._epoch: int = (
            0  # In-flight invalidation only; no retained previous states.
        )
        # One lightweight successful validation per task; no retained BReps.
        self._validation_reviews: dict[
            tuple[str, bool], tuple[str, dict[str, Any]]
        ] = {}
        self._mark_needs_input()

    def _selection_intent(self, node: Selection, recipe: Recipe) -> dict[str, Any]:
        value = node.model_dump(exclude={"label", "entries", "ids", "depth"})
        if node.entries is None:
            return {
                **value,
                "entry": {
                    "scan": source_binding(self.workspace).key,
                    "mode": "painted",
                    "ids": node.ids,
                    "depth": node.depth,
                },
            }
        entry = current_entry(node, self.workspace)
        if entry is None:
            return {**value, "entry": None}
        value["entry"] = {"scan": entry.scan.key, **membership_projection(entry)}
        if entry.source_volume is not None:
            from experiments.selection_volume_authoring import (
                authoring_fingerprint,
                resolve_volume,
            )

            owner, original, volume = resolve_volume(recipe, entry.source_volume)
            value["volume"] = {
                "sha256": volume.archive.sha256,
                "accepted_fingerprint": volume.authoring_fingerprint,
                "current_fingerprint": authoring_fingerprint(
                    recipe, owner, original, volume
                ),
            }
        return value

    def _volume_references(self, plan: ExecutionPlan) -> set[str]:
        references: set[str] = set()
        for node in self._recipe.nodes:
            if (
                not isinstance(node, Selection)
                or node.entries is None
                or node.id not in plan.order
            ):
                continue
            for entry in node.entries:
                references.update(
                    f"{node.id}/{entry.id}/{volume.id}" for volume in entry.volumes
                )
            entry = current_entry(node, self.workspace)
            if entry is not None and entry.source_volume is not None:
                ref = entry.source_volume
                references.add(f"{ref.selection}/{ref.entry}/{ref.volume}")
        return references

    def _mark_needs_input(self) -> None:
        """Settle missing setup and discard every affected published output."""
        waiting: dict[str, set[str]] = {}
        diagnostics: dict[str, dict[str, Any]] = {}
        for node in self._recipe.nodes:
            if isinstance(node, Selection) and node.entries is not None:
                diagnostic = entry_diagnostic(self._recipe, node, self.workspace)
                if diagnostic is not None:
                    reference = diagnostic.get("volume")
                    if reference is not None:
                        key = "/".join(
                            reference[name] for name in ("selection", "entry", "volume")
                        )
                        failure = self._volume_refresh_errors.get(key)
                        if failure is not None and failure[0] == diagnostic.get(
                            "refresh_fingerprint"
                        ):
                            diagnostic = {
                                **diagnostic,
                                "refresh_error": failure[1],
                                "message": f"The authoring inputs could not refresh this volume: {failure[1]}",
                            }
                    waiting[node.id] = {node.id}
                    diagnostics[node.id] = diagnostic
        if not waiting:
            return
        nodes = {node.id: node for node in self._recipe.nodes}
        plan = compile_execution_plan(
            self._recipe,
            set(nodes),
            describe_geometry_influence(nodes),
            selected_named_solves=isinstance(self.workspace, ScanWorkspace),
        )
        changed = True
        while changed:
            changed = False
            for task, dependencies_ in plan.dependencies.items():
                causes = set().union(
                    *(waiting.get(dep, set()) for dep in dependencies_)
                )
                if causes and not causes <= waiting.get(task, set()):
                    waiting.setdefault(task, set()).update(causes)
                    changed = True
        for task, causes in waiting.items():
            for key in plan.aliases.get(task, set()) | (
                {task} if task in nodes else set()
            ):
                self._states[key] = "needs_input"
                _ = self._errors.pop(key, None)
                _ = self._derived.pop(key, None)
                _ = self._results.pop(key, None)
                _ = self._connected_solves.pop(key, None)
                _ = self._connected_point_solves.pop(key, None)
                self._diagnostics[key] = diagnostics.get(
                    key,
                    {
                        "code": "needs_input",
                        "action": "configure_selections",
                        "blocked_by": sorted(causes),
                        "message": "Choose source volumes for the input selections",
                    },
                )

    @property
    def workspace(self) -> WorkspaceType:
        return self._workspace

    def failure_checkpoint(self) -> GraphFailureCheckpoint:
        """Retain unchanged failures when disposable computation is discarded."""
        with self.lock:
            states = {
                key: state
                for key, state in self._states.items()
                if state in {"failed", "blocked", "needs_input"}
            }
            return GraphFailureCheckpoint(
                self._recipe.model_copy(deep=True),
                self._token(),
                states,
                {key: error for key, error in self._errors.items() if key in states},
                deepcopy(
                    {
                        key: diagnostic
                        for key, diagnostic in self._diagnostics.items()
                        if key in states
                    }
                ),
                self._volume_refresh_errors.copy(),
            )

    def restore_failures(self, checkpoint: GraphFailureCheckpoint) -> None:
        """Restore failure evidence onto a fresh graph with the same recipe."""
        with self.lock:
            if checkpoint.token != self._token():
                raise StaleGraph("failure checkpoint belongs to another recipe")
            if self._revision != 0 or any(
                state not in {"unevaluated", "needs_input"}
                for state in self._states.values()
            ):
                raise ValueError("failure restoration requires a fresh graph")
            self._states.update(checkpoint.states)
            self._errors.update(checkpoint.errors)
            self._diagnostics.update(deepcopy(checkpoint.diagnostics))
            self._volume_refresh_errors = checkpoint.volume_refresh_errors.copy()
            self._mark_needs_input()
            if checkpoint.states:
                self._revision += 1

    def discard_evaluation(
        self, checkpoint: GraphFailureCheckpoint
    ) -> dict[str, object]:
        """Fence active work and discard partial results without reverting edits.

        The source owner keeps its evaluation lease until the old task retires.
        Only computation stores are replaced here; source, authored definitions,
        graph identity and native ownership stay with the accepted project.
        """
        with self.lock:
            current = self._recipe.model_copy(deep=True)
            fresh = FeatureGraph(
                self.workspace,
                checkpoint.recipe,
                refresh_volumes=self._refresh_volumes,
                fit_intent=self._fit_intent,
                fit_executor=self.fit_executor,
            )
            fresh.restore_failures(checkpoint)
            _ = fresh.replace(current, checkpoint.token)
            self._states = fresh._states
            self._results = fresh._results
            self._derived = fresh._derived
            self._errors = fresh._errors
            self._diagnostics = fresh._diagnostics
            self._connected_solves = fresh._connected_solves
            self._connected_point_solves = fresh._connected_point_solves
            self._validation_reviews = fresh._validation_reviews
            self._volume_refresh_errors = fresh._volume_refresh_errors
            self._volume_cache = fresh._volume_cache
            self._evaluation_origin_token = None
            self._epoch += 1
            self._revision += 1
            return self.snapshot()

    def validate(self, recipe: Recipe) -> Recipe:
        nodes = {node.id: node for node in recipe.nodes}
        _ = coaxial_axis_bindings(nodes)
        positions = {node.id: index for index, node in enumerate(recipe.nodes)}
        if len(nodes) != len(recipe.nodes):
            raise ValueError("duplicate feature ID")
        if (recipe.output is None and nodes) or (
            recipe.output is not None and recipe.output not in nodes
        ):
            raise ValueError("output must reference an action")
        sources = [node for node in recipe.nodes if isinstance(node, Source)]
        if len(sources) > 1:
            raise ValueError("only one scan Source is supported per project")
        if isinstance(self.workspace, ScanWorkspace):
            if self.workspace.descriptor is None and nodes:
                raise ValueError("empty workspace cannot contain actions")
            if self.workspace.descriptor is not None and len(sources) != 1:
                raise ValueError("an imported workspace requires its scan Source")
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
            if isinstance(node, SurfaceFit) and node.support_authority == "prototype":
                _ = prototype_support_source(node, nodes)
            if (
                isinstance(node, (AxisDefinition, PlaneDefinition))
                and node.placement is not None
            ):
                placement = node.placement
                reuse = nodes[placement.reuse]
                source = nodes[placement.source]
                if (
                    not isinstance(reuse, FeatureReuse)
                    or placement.target_selection not in reuse.target_selections
                ):
                    raise ValueError("datum placement requires a declared reuse target")
                if (reuse.circular is None) != (placement.occurrence is None) or (
                    placement.occurrence is not None
                    and placement.target_selection
                    != f"occurrence_{placement.occurrence}"
                ):
                    raise ValueError(
                        "placed circular datum requires exact occurrence provenance"
                    )
                if type(source) is not type(node) or source.id not in reuse.lineage:
                    raise ValueError(
                        "datum placement requires a same-kind source in the reuse lineage"
                    )
                if node.managed_by != reuse.id:
                    raise ValueError("placed datums must belong to their reuse action")
                if isinstance(node, AxisDefinition) and (
                    node.initial_parameters is None
                    or node.source_fit is not None
                    or node.source_points is not None
                ):
                    raise ValueError(
                        "match placement currently initializes manually declared free axes"
                    )
                if (
                    isinstance(node, PlaneDefinition)
                    and node.construction != cast(PlaneDefinition, source).construction
                ):
                    raise ValueError(
                        "placed planes must retain their source construction"
                    )
            if isinstance(node, (TrimmedFace, ArrangedFace, BuildFaces)):
                if isinstance(node, ArrangedFace) and (
                    len(set(node.finite_boundary_sources))
                    != len(node.finite_boundary_sources)
                    or not set(node.finite_boundary_sources)
                    <= set(node.boundary_sources)
                ):
                    raise ValueError(
                        "finite cutter sources must be unique approved boundary sources"
                    )
                if len(set(node.boundary_sources)) != len(node.boundary_sources):
                    raise ValueError("approved boundary source faces must be unique")
                target = node.target if isinstance(node, BuildFaces) else node.surface
                if node.boundary_sources and target is None:
                    raise ValueError(
                        "approved boundary sources require a guided target"
                    )
                for source_id in node.boundary_sources:
                    source_face = nodes[source_id]
                    if not isinstance(source_face, (TrimmedFace, ArrangedFace)):
                        raise ValueError(
                            "approved boundary sources must reference faces"
                        )
                    if source_face.surface == target:
                        raise ValueError(
                            "approved boundary source must be an adjoining surface, not the target"
                        )
                    if (
                        isinstance(node, ArrangedFace)
                        and source_id in node.finite_boundary_sources
                        and source_face.surface not in node.cutters
                    ):
                        raise ValueError(
                            "finite cutter source must belong to a selected cutter"
                        )
                    if (
                        isinstance(node, BuildFaces)
                        and source_face.surface not in node.surfaces
                    ):
                        raise ValueError(
                            "approved boundary source must belong to a selected neighbor"
                        )
            if isinstance(node, Source):
                if node.virtual_scan is not None:
                    # Internal model_copy callers can bypass field validators.
                    _ = Source.model_validate(node.model_dump(mode="json"))
                if (
                    node.source_sha256 != self.workspace.source_sha256
                    or node.reference_sha256 != self.reference_sha256
                ):
                    raise ValueError("source or reference binding mismatch")
                if isinstance(self.workspace, ScanWorkspace) and (
                    node.descriptor is None
                    or self.workspace.descriptor is None
                    or node.descriptor.semantic_record()
                    != self.workspace.descriptor.semantic_record()
                ):
                    raise ValueError(
                        "source descriptor does not match imported geometry"
                    )
            elif isinstance(node, Selection):
                if not isinstance(nodes[node.source], Source):
                    raise ValueError("selection input must be a source")
                entry = current_entry(node, self.workspace)
                ids = (
                    node.ids
                    if node.entries is None
                    else entry.ids
                    if entry is not None
                    else None
                )
                if ids is not None and (
                    ids != sorted(set(ids))
                    or (ids and ids[-1] >= len(self.workspace.local))
                ):
                    raise ValueError(
                        "selection IDs must be unique, sorted, and in range"
                    )
                if ids and np.any(self.workspace.data.weights[ids] <= 0):
                    raise ValueError("selection requires positive incident area")
            elif isinstance(node, SurfaceFit):
                if len(set(node.selections)) != len(node.selections):
                    raise ValueError("fit selection references must be unique")
                sources = {
                    selection_source(nodes[ref], nodes) for ref in node.selections
                }
                if len(sources) != 1:
                    raise ValueError("fit selections must share a source")
                if node.axial_domain is not None:
                    lo, hi = node.axial_domain
                    if not np.isfinite([lo, hi]).all() or lo >= hi:
                        raise ValueError("invalid axial domain")
                for field in ("axis", "point", "reference_plane"):
                    if (reference := getattr(node, field)) is not None:
                        _ = validate_fit_reference(node.kind, field, reference, nodes)
                if node.axis is not None:
                    axis = cast(AxisDefinition, nodes[node.axis])
                    if axis.source_points is not None and not isinstance(
                        self.workspace, ScanWorkspace
                    ):
                        raise ValueError(
                            "point-pair axes are currently datum-only and cannot drive a surface fit"
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
            elif isinstance(node, SurfaceIntersection):
                kinds = [
                    surface_reference_kind(ref, nodes)
                    for ref in (node.first, node.second)
                ]
                if sorted(kinds) not in (
                    ["cylinder", "plane"],
                    ["cone", "plane"],
                    ["plane", "plane"],
                ):
                    raise ValueError(
                        "boundaries require two planes, or a plane and cylinder or cone"
                    )
            elif isinstance(node, ArrangedFace):
                references = [node.surface, *node.cutters]
                if len({ref.model_dump_json() for ref in references}) != len(
                    references
                ):
                    raise ValueError(
                        "arrangement surface and cutter references must be unique"
                    )
                if any(
                    surface_reference_kind(ref, nodes)
                    not in ("plane", "cylinder", "cone")
                    for ref in references
                ):
                    raise ValueError("arrangements require planes, cylinders, or cones")
                if len(set(node.domains)) != len(node.domains):
                    raise ValueError("arrangement domain faces must be unique")
                for domain_id in node.domains:
                    domain = nodes[domain_id]
                    if (
                        not isinstance(domain, (TrimmedFace, ArrangedFace))
                        or domain.surface != node.surface
                    ):
                        raise ValueError(
                            "arrangement domains must be faces in the same exact surface context"
                        )
                if not np.isfinite(node.selector.witness_chart).all():
                    raise ValueError("arrangement witness must be finite")
            elif isinstance(node, TrimmedFace):
                kind = surface_reference_kind(node.surface, nodes)
                if len({b.intersection for b in node.boundaries}) != len(
                    node.boundaries
                ):
                    raise ValueError("face boundary references must be unique")
                for boundary in node.boundaries:
                    edge = nodes[boundary.intersection]
                    if not isinstance(edge, SurfaceIntersection):
                        raise ValueError(
                            "face boundaries must reference intersection features"
                        )
                    if node.surface not in (edge.first, edge.second):
                        raise ValueError(
                            "face and shared boundary must use the same surface context"
                        )
                    choices = (
                        ("inside", "outside")
                        if kind == "plane"
                        and all(
                            surface_reference_kind(ref, nodes) != "plane"
                            for ref in (edge.first, edge.second)
                            if ref != node.surface
                        )
                        else ("positive", "negative")
                    )
                    if boundary.keep not in choices:
                        raise ValueError(
                            "face boundary keep choice does not match surface kind"
                        )
            elif isinstance(node, Body):
                if any(
                    not isinstance(nodes[face_id], (TrimmedFace, ArrangedFace))
                    for face_id in node.faces
                ):
                    raise ValueError("body inputs must be physical face actions")
                if node.managed_by is not None:
                    raise ValueError("bodies cannot be managed face outputs")
            elif isinstance(node, BuildFaces):
                if node.target is not None and node.target not in node.surfaces:
                    raise ValueError("guided face target must be a selected surface")
                if node.target is not None and any(
                    node.target not in (choice.first, choice.second)
                    for choice in node.adjacencies
                ):
                    raise ValueError(
                        "guided adjacency choices must involve the target surface"
                    )
                validate_face_review_references(
                    node.surfaces, node.adjacencies, node.face_scopes, nodes
                )
                references = [ref.model_dump_json() for ref in node.surfaces]
                if len(set(references)) != len(references):
                    raise ValueError("build faces surface references must be unique")
                for reference in node.surfaces:
                    if surface_reference_kind(reference, nodes) not in (
                        "plane",
                        "cylinder",
                        "cone",
                    ):
                        raise ValueError(
                            "build faces currently supports planes, cylinders, and cones"
                        )
                for ref, kind in [
                    *((ref, (TrimmedFace, ArrangedFace)) for ref in node.reused_faces),
                    *((ref, SurfaceIntersection) for ref in node.reused_intersections),
                ]:
                    if (
                        not isinstance(nodes[ref], kind)
                        or nodes[ref].managed_by == node.id
                    ):
                        raise ValueError(
                            "reused outputs must be existing faces or intersections outside this build action"
                        )
            elif isinstance(node, Growth):
                fitted = nodes[node.seed_fit]
                if not isinstance(fitted, SurfaceFit) or not is_standalone_fit(fitted):
                    raise ValueError("growth input must be an earlier standalone fit")
                source = selection_source(node, nodes)
                for barrier in node.barriers:
                    if selection_source(nodes[barrier], nodes) != source:
                        raise ValueError("growth barrier belongs to another source")
            elif isinstance(node, SurfaceSelection):
                if len(set(node.barriers)) != len(node.barriers):
                    raise ValueError("surface-selection barriers must be unique")
                if node.seed_selection in node.barriers:
                    raise ValueError("surface-selection seeds cannot also be a barrier")
                source = selection_source(nodes[node.seed_selection], nodes)
                for barrier in node.barriers:
                    if selection_source(nodes[barrier], nodes) != source:
                        raise ValueError(
                            "surface-selection barrier belongs to another source"
                        )
            elif isinstance(node, SelectionRegion):
                selected = nodes[node.selection]
                fitted = nodes[node.fit]
                if not isinstance(
                    selected,
                    (
                        Selection,
                        Growth,
                        SurfaceSelection,
                        RegionSelection,
                        ReuseSelection,
                    ),
                ):
                    raise ValueError("a reusable region requires an earlier selection")
                if (
                    not isinstance(fitted, SurfaceFit)
                    or fitted.kind not in ("cylinder", "cone", "plane")
                    or node.selection not in fitted.selections
                ):
                    raise ValueError(
                        "a reusable region requires a cylinder, cone or plane fit using its selection"
                    )
                axis_id = selection_frame_axis(
                    node.axial_plane, node.clock_plane, nodes
                )
                if (
                    fitted.kind in {"cylinder", "cone"}
                    and fit_axis(fitted, nodes) != axis_id
                ):
                    raise ValueError(
                        "a revolution selection region must use its datum-frame axis"
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
                if (
                    node.circular is None
                    and node.reference_selection in node.target_selections
                ):
                    raise ValueError(
                        "reuse reference and target selections must be different"
                    )
                fits = [nodes[key] for key in node.fits]
                if any(
                    not isinstance(fit, SurfaceFit)
                    or fit.kind not in ("cylinder", "cone", "plane")
                    for fit in fits
                ):
                    raise ValueError(
                        "reuse currently supports cylinder, cone and plane fits"
                    )
                for selection_id in (
                    node.reference_selection,
                    *(node.target_selections if node.circular is None else []),
                ):
                    if not isinstance(
                        nodes[selection_id],
                        (
                            Selection,
                            Growth,
                            SurfaceSelection,
                            RegionSelection,
                            ReuseSelection,
                        ),
                    ):
                        raise ValueError("reuse matching inputs must be selections")
                sources = {
                    selection_source(nodes[node.reference_selection], nodes),
                    *(
                        selection_source(nodes[selection_id], nodes)
                        for selection_id in (
                            node.target_selections if node.circular is None else []
                        )
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
                if node.circular is not None:
                    circular = node.circular
                    if node.target_selections != [
                        f"occurrence_{index}" for index in range(1, circular.count)
                    ]:
                        raise ValueError(
                            "circular occurrence keys must match the declared total count"
                        )
                    axis = nodes[circular.axis]
                    if not isinstance(axis, AxisDefinition) and not (
                        isinstance(axis, SurfaceFit)
                        and axis.kind in {"cylinder", "cone"}
                    ):
                        raise ValueError(
                            "circular placement requires an upstream axis or revolution Fit"
                        )
                    from experiments.selection_volume_authoring import resolve_volume

                    expected_roles = {
                        (fit.id, selection)
                        for fit in fits
                        if isinstance(fit, SurfaceFit)
                        for selection in fit.selections
                    }
                    actual_roles = [
                        (item.fit, item.source_selection) for item in circular.volumes
                    ]
                    if (
                        len(set(actual_roles)) != len(actual_roles)
                        or set(actual_roles) != expected_roles
                    ):
                        raise ValueError(
                            "choose exactly one explicit volume for every source Fit input"
                        )
                    for item in circular.volumes:
                        owner, _, _ = resolve_volume(recipe, item.volume)
                        if owner.id != item.source_selection:
                            raise ValueError(
                                "circular volumes must belong to their declared source selection"
                            )
                    from experiments.feature_reuse_authoring import (
                        circular_child_operations,
                    )

                    expected_children = circular_child_operations(node, nodes)
                    actual_children = {
                        str(child.managed_key): child.operation
                        for child in recipe.nodes
                        if child.managed_by == node.id
                    }
                    if actual_children != expected_children:
                        raise ValueError(
                            "Circular pattern children must match the saved managed occurrence/role inventory; use explicit authoring to update it"
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
                if (reuse.circular is None) != (node.occurrence is None) or (
                    node.occurrence is not None
                    and node.target_selection != f"occurrence_{node.occurrence}"
                ):
                    raise ValueError(
                        "generated circular selection requires exact occurrence provenance"
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
            elif isinstance(node, CoaxialGroup):
                _ = validate_relationship_inputs(
                    "coaxial", {"axis": [node.axis], "members": node.surfaces}, recipe
                )
                sources = {
                    selection_source(nodes[key], nodes)
                    for ref in node.surfaces
                    for key in cast(SurfaceFit, nodes[ref]).selections
                }
                if len(sources) != 1:
                    raise ValueError("coaxial group members must share one source")
                for candidate in nodes.values():
                    if isinstance(candidate, JointFit):
                        sides, _ = joint_surfaces(candidate, nodes)
                        if set(node.surfaces).intersection(
                            surface.id for surface in sides
                        ):
                            raise ValueError(
                                "coaxial group members already belong to a legacy joint; use an axis solve"
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
                    for ref in node.source_points:
                        if isinstance(ref, OutputReference):
                            _ = validate_output_reference(ref, recipe, "point")
                        elif not isinstance(nodes[ref], PointDefinition):
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
                    for ref in (distance.first_point, distance.second_point):
                        if isinstance(ref, OutputReference):
                            _ = validate_output_reference(ref, recipe, "point")
                        elif not isinstance(nodes[ref], PointDefinition):
                            raise ValueError(
                                "scale distances require earlier point datums"
                            )
            elif isinstance(node, FrameDefinition):
                if isinstance(node.origin_point, OutputReference):
                    _ = validate_output_reference(node.origin_point, recipe, "point")
                elif not isinstance(nodes[node.origin_point], PointDefinition):
                    raise ValueError("frame origin requires a point datum")
                for reference_id in (
                    node.primary_reference,
                    node.secondary_reference,
                ):
                    if isinstance(reference_id, OutputReference):
                        _ = validate_output_reference(reference_id, recipe, "direction")
                        continue
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
                if node.axis is not None and not isinstance(
                    nodes[node.axis], AxisDefinition
                ):
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
                _ = validate_relationship_inputs(
                    "mirror", {"datum": [node.plane], "members": node.surfaces}, recipe
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
                _ = validate_relationship_inputs(
                    "radius_plane_distance",
                    {
                        "radius": [radius.surface],
                        "plane": [distance.surface],
                        "datum": [distance.reference_plane],
                    },
                    recipe,
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
                _ = validate_relationship_inputs(
                    "equal_radii", {"participants": node.surfaces}, recipe
                )
            elif isinstance(node, (PlaneRelationship, DirectionAlignment)):
                kind = (
                    "direction_alignment"
                    if isinstance(node, DirectionAlignment)
                    else "parallel_planes"
                    if node.relation == "parallel"
                    else "coincident_planes"
                )
                endpoints = validate_relationship_inputs(
                    kind,
                    {
                        "participants": node.directions,
                        "reference": [node.reference]
                        if node.reference is not None
                        else [],
                    }
                    if isinstance(node, DirectionAlignment)
                    else {"participants": node.surfaces},
                    recipe,
                )
                endpoints = [
                    endpoint for values in endpoints.values() for endpoint in values
                ]
                if len({endpoint.key for endpoint in endpoints}) != len(endpoints):
                    raise ValueError("relationship inputs must be unique")
            elif isinstance(node, AxisSolve):
                axis = nodes[node.axis]
                if not isinstance(axis, AxisDefinition):
                    raise ValueError("axis solve requires an explicit axis")
                if len(set(node.factors)) != len(node.factors):
                    raise ValueError("axis solve factor references must be unique")
                factors = [nodes[ref] for ref in node.factors]
                for factor in factors:
                    if not isinstance(factor, PlaneRelationship):
                        continue
                    referenced_axes: set[str] = set()
                    for endpoint in plane_relationship_endpoints(factor, nodes):
                        if not endpoint.is_binding:
                            raise ValueError(
                                "named plane factors require direct parameter owners"
                            )
                        owner = nodes[endpoint.feature]
                        linked = (
                            owner.axis
                            if isinstance(owner, PlaneDefinition)
                            else fit_axis(cast(SurfaceFit, owner), nodes)
                        )
                        if linked is not None:
                            referenced_axes.add(linked)
                    if referenced_axes != {node.axis}:
                        raise ValueError(
                            "named plane factors must reference only the selected axis"
                        )
                if any(
                    not (
                        isinstance(factor, SurfaceFit)
                        and fit_axis(factor, nodes) == node.axis
                        and factor.kind in ("cone", "cylinder", "plane")
                    )
                    and not (
                        isinstance(factor, CoaxialGroup) and factor.axis == node.axis
                    )
                    and not isinstance(factor, PlaneRelationship)
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
                typed_factors = (
                    selected_symmetry_inputs(node, nodes)[0]
                    if has_selected_symmetry(node, nodes)
                    else [
                        factor for factor in factors if isinstance(factor, SurfaceFit)
                    ]
                )
                if len(typed_factors) < 2:
                    raise ValueError(
                        "axis solve requires at least two distinct fit observations"
                    )
                active_ids = {factor.id for factor in typed_factors}
                for group in nodes.values():
                    if (
                        isinstance(group, CoaxialGroup)
                        and group.axis == node.axis
                        and active_ids.intersection(group.surfaces)
                        and not set(group.surfaces) <= active_ids
                    ):
                        raise ValueError(
                            "select the complete coaxial group in the axis solve"
                        )
                mirror_factors = [
                    factor for factor in factors if isinstance(factor, MirrorSymmetry)
                ]
                if {
                    factor.reference_plane
                    for factor in typed_factors
                    if factor.reference_plane is not None
                } & {factor.plane for factor in mirror_factors}:
                    raise ValueError(
                        "a joint cannot yet share one clocking plane between mirror and reference-bound fit factors"
                    )
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
                    sum(factor.kind in ("cone", "cylinder") for factor in typed_factors)
                    < 2
                    and not any(factor.kind == "plane" for factor in typed_factors)
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
                if len(sources) > 1 or (
                    not sources and named_direction_component(node, nodes) is None
                ):
                    raise ValueError("joint fit needs selections on the same source")
        direction_components = automatic_plane_relationship_components(nodes)
        for relations, _, members in direction_components.values():
            if not any(
                isinstance(relation, DirectionAlignment) for relation in relations
            ):
                continue
            for candidate in nodes.values():
                if isinstance(
                    candidate,
                    (
                        AxisSolve,
                        MirrorSymmetry,
                        ParallelToPlane,
                        EqualQuantities,
                        RotationalSymmetry,
                    ),
                ) and members.intersection(dependencies(candidate)):
                    raise ValueError(
                        "direction component overlaps an unsupported active factor; use an exact upstream solved context instead"
                    )
                if (
                    isinstance(candidate, JointFit)
                    and named_direction_component(candidate, nodes) is None
                ):
                    sides, planes = joint_surfaces(candidate, nodes)
                    if members.intersection(
                        surface.id for surface in [*sides, *planes]
                    ):
                        raise ValueError(
                            "direction component overlaps an unsupported named solve; use its exact solved context instead"
                        )
        if any(
            isinstance(ref, OutputReference)
            for node in recipe.nodes
            for ref in (
                (node.origin_point, node.primary_reference, node.secondary_reference)
                if isinstance(node, FrameDefinition)
                else node.source_points or ()
                if isinstance(node, AxisDefinition)
                else tuple(
                    ref
                    for distance in node.distances
                    for ref in (distance.first_point, distance.second_point)
                )
                if isinstance(node, ScaleDefinition)
                else tuple(node.surfaces)
                if isinstance(node, (PlaneRelationship, DirectionAlignment))
                else ()
            )
        ):
            _ = compile_execution_plan(
                recipe,
                set(nodes),
                describe_geometry_influence(nodes),
                selected_named_solves=isinstance(self.workspace, ScanWorkspace),
            )
        if any(
            isinstance(node, Selection) and node.entries is not None
            for node in recipe.nodes
        ):
            validate_records(recipe, self.workspace, self._volume_cache)
        check_portable_actions(recipe.model_dump(mode="json"))
        return recipe.model_copy(deep=True)

    def _input_values(self) -> dict[str, dict[str, Any]]:
        """Exact publication stores; callers hold the graph lock."""
        # Catalogue/resolution readers build detached outputs while the lock is
        # held; copying every stored face here needlessly duplicates provenance.
        values = cast(dict[str, dict[str, Any]], {**self._derived, **self._results})
        nodes = {node.id: node for node in self._recipe.nodes}
        for selection in self._recipe.nodes:
            if not isinstance(selection, Selection) or selection.entries is None:
                continue
            entry = current_entry(selection, self.workspace)
            ids = (
                entry.ids
                if entry is not None and entry.mode == "painted"
                else self._derived.get(selection.id, {}).get("ids")
            )
            values[selection.id] = {
                "ids": list(ids) if ids is not None else [],
                "source": selection.source,
            }
        for point_id, solve in self._connected_point_solves.items():
            values["@point/" + point_id] = {
                "outputs": {point_id: solve["point"]},
                "surfaces": solve["surfaces"],
            }
        for axis_id, solve in self._connected_solves.items():
            node = cast(AxisDefinition, nodes[axis_id])
            axis = connected_axis_result(node, self._derived.get(axis_id, {}), solve)
            outputs = {axis_id: axis}
            for plane in self._recipe.nodes:
                if isinstance(plane, PlaneDefinition) and plane.axis == axis_id:
                    outputs[plane.id] = solve.get("reference_planes", {}).get(
                        plane.id
                    ) or reference_plane_result(
                        axis, initialized_plane(plane, self._derived.get(plane.id, {}))
                    )
            values["@axis/" + axis_id] = {
                "outputs": outputs,
                "surfaces": solve.get("surfaces", {}),
            }
        return values

    def input_choices(
        self,
        expected_token: str,
        requirement: str = "point",
        *,
        consumer_id: str | None = None,
        retained: Any = (),
        source: str | None = None,
        frame: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        with self.lock:
            if expected_token != self._token():
                raise StaleGraph("graph changed before input discovery")
            catalogue = output_catalogue(
                self._recipe,
                self._states,
                self._input_values(),
                frame=self.reference_sha256,
                coordinates=self.workspace.local,
            )
            return input_choices(
                self._recipe,
                consumer_id=consumer_id,
                requirement=requirement,
                retained=retained,
                source=source,
                frame=frame,
                catalogue=catalogue,
            )

    def _token(self) -> str:
        # Content token detects conflicting client edits without an edit history.
        return self._content_token

    def publication_token(self, request_token: str) -> str:
        """Resolve only a refresh committed by this exact evaluation request."""
        with self.lock:
            if request_token not in {self._token(), self._evaluation_origin_token}:
                raise StaleGraph("graph changed after evaluation")
            return self._token()

    @property
    def volume_cache(self) -> VolumeCache:
        """Share the single bounded archive cache with read-only UI previews."""
        return self._volume_cache

    def export_recipe(self, token: str | None = None) -> dict[str, Any]:
        """Copy authored intent without constructing derived-result payloads."""
        with self.lock:
            if token is not None and token != self._token():
                raise StaleGraph("the recipe changed before export")
            return self._recipe.model_dump()

    def ready_selection_inputs(
        self, token: str, references: list[str]
    ) -> tuple[str, dict[str, list[int]]]:
        """Copy ready observation IDs for a private, non-publishing preview."""
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before surface-selection preview")
            nodes = {node.id: node for node in self._recipe.nodes}
            if not references or any(
                reference not in nodes for reference in references
            ):
                raise ValueError(
                    "surface-selection inputs must name existing selections"
                )
            sources = {
                selection_source(nodes[reference], nodes) for reference in references
            }
            if len(sources) != 1:
                raise ValueError("surface-selection inputs must share one source")
            values: dict[str, list[int]] = {}
            for reference in references:
                if self._states[reference] != "ready":
                    raise ValueError(
                        "Evaluate the seed and barrier selections before previewing"
                    )
                node = nodes[reference]
                ids = (
                    node.ids
                    if isinstance(node, Selection) and node.entries is None
                    else self._derived.get(reference, {}).get("ids")
                )
                if not isinstance(ids, list):
                    raise ValueError("surface-selection observations are unavailable")
                values[reference] = ids.copy()
            return next(iter(sources)), values

    def snapshot(
        self, if_revision: int | None = None, if_recipe_token: str | None = None
    ) -> dict[str, object]:
        """Read one publication atomically, omitting unchanged geometry.

        Observable state/result mutations must advance _revision under self.lock;
        recipe content tokens alone cannot identify evaluation publications.
        """
        with self.lock:
            if if_revision == self._revision and (
                if_recipe_token is None or if_recipe_token == self._token()
            ):
                return {
                    "token": self._token(),
                    "revision": self._revision,
                    "unchanged": True,
                    **(
                        {"evaluation_origin_token": self._evaluation_origin_token}
                        if self._evaluation_origin_token is not None
                        else {}
                    ),
                }
            publication_values = self._input_values()
            resolved = deepcopy({**self._derived, **self._results})
            for axis_id, solve in self._connected_solves.items():
                axis_node = next(
                    node
                    for node in self._recipe.nodes
                    if isinstance(node, AxisDefinition) and node.id == axis_id
                )
                axis = deepcopy(self._derived.get(axis_id, {}))
                axis.update(connected_axis_result(axis_node, axis, solve))
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
                            or reference_plane_result(
                                axis,
                                initialized_plane(node, self._derived.get(node.id, {})),
                            )
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
                axis_result = directed_axis_result(
                    node,
                    first_point=resolve_output(
                        node.source_points[0],
                        {n.id: n for n in self._recipe.nodes},
                        publication_values,
                        coordinates=self.workspace.local,
                    )
                    if isinstance(node.source_points[0], OutputReference)
                    else cast(dict[str, Any], resolved[node.source_points[0]]),
                    second_point=resolve_output(
                        node.source_points[1],
                        {n.id: n for n in self._recipe.nodes},
                        publication_values,
                        coordinates=self.workspace.local,
                    )
                    if isinstance(node.source_points[1], OutputReference)
                    else cast(dict[str, Any], resolved[node.source_points[1]]),
                )
                resolved[node.id] = axis_result
                for plane in self._recipe.nodes:
                    if (
                        isinstance(plane, PlaneDefinition)
                        and plane.axis == node.id
                        and self._states.get(plane.id) == "ready"
                    ):
                        resolved[plane.id] = reference_plane_result(axis_result, plane)
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
                    not isinstance(node, (PlaneRelationship, DirectionAlignment))
                    or self._states.get(node.id) != "ready"
                ):
                    continue
                relationship = self._derived.get(node.id, {})
                for fit_id, surface in relationship.get(
                    "resolved", relationship.get("surfaces", {})
                ).items():
                    resolved[fit_id] = deepcopy(surface)
            for context, value in self._derived.items():
                if context.startswith("@plane/"):
                    resolved.update(deepcopy(value.get("resolved", {})))
            catalogue = output_catalogue(
                self._recipe,
                self._states,
                publication_values,
                frame=self.reference_sha256,
                coordinates=self.workspace.local,
            )
            return {
                **(
                    {}
                    if if_recipe_token == self._token()
                    else {"recipe": self._recipe.model_dump()}
                ),
                "token": self._token(),
                "revision": self._revision,
                **(
                    {"evaluation_origin_token": self._evaluation_origin_token}
                    if self._evaluation_origin_token is not None
                    else {}
                ),
                "states": self._states.copy(),
                "errors": self._errors.copy(),
                "diagnostics": deepcopy(self._diagnostics),
                "result": deepcopy(self._results.get(self._recipe.output))
                if self._recipe.output is not None
                else None,
                "derived": deepcopy(self._derived),
                "results": resolved,
                "input_catalogue": catalogue,
                "input_contracts": deepcopy(REQUIREMENTS),
                "relationship_contracts": deepcopy(RELATIONSHIP_SIGNATURES),
                "input_requirements": {
                    name: input_choices(
                        self._recipe, requirement=name, catalogue=catalogue
                    )
                    for name in REQUIREMENTS
                },
                "memberships": {
                    n.id: n.ids.copy()
                    if isinstance(n, Selection) and n.entries is None
                    else publication_values.get(n.id, {}).get("ids")
                    if isinstance(n, Selection)
                    else deepcopy(self._derived.get(n.id, {}).get("ids"))
                    for n in self._recipe.nodes
                    if isinstance(
                        n,
                        (
                            Selection,
                            Growth,
                            SurfaceSelection,
                            RegionSelection,
                            ReuseSelection,
                        ),
                    )
                },
            }

    def replace_authored(self, value: object, token: str) -> dict[str, object]:
        """Reconcile managed circular definitions only at an explicit edit boundary.

        Loading, restoring and evaluating use ordinary replace/validation and
        never acquire new children. Reconciliation and protected-removal checks
        complete under the same stale cursor before publishing any edited state.
        """
        from experiments.feature_reuse_authoring import (
            ReuseChanges,
            reconcile_feature_reuse,
        )

        with self.lock:
            if token != self._token():
                raise StaleGraph(
                    "graph changed before authored circular reconciliation"
                )
            recipe = Recipe.model_validate(value)
            for owner in tuple(recipe.nodes):
                if isinstance(owner, FeatureReuse) and owner.circular is not None:
                    recipe = reconcile_feature_reuse(
                        recipe, owner.id, ReuseChanges(), "circular-authored-refresh-v1"
                    ).recipe
            return self.replace(recipe, token)

    def replace(self, recipe: Recipe, token: str) -> dict[str, object]:
        recipe = self.validate(recipe)
        with self.lock:
            if token != self._token():
                raise StaleGraph(
                    "graph changed; reload the current graph before editing"
                )
            before = {n.id: n for n in self._recipe.nodes}
            previous_lineage = [
                n.virtual_scan
                for n in self._recipe.nodes
                if isinstance(n, Source) and n.virtual_scan is not None
            ]
            next_lineage = [
                n.virtual_scan
                for n in recipe.nodes
                if isinstance(n, Source) and n.virtual_scan is not None
            ]
            if previous_lineage != next_lineage:
                raise ValueError(
                    "virtual scan lineage changes require a source transaction"
                )
            # Labels are presentation metadata. Keep evaluated geometry (and
            # settled failures) while the recipe token still tracks the rename.
            affected = {
                node.id
                for node in recipe.nodes
                if (previous := before.get(node.id)) is None
                or (
                    self._selection_intent(previous, self._recipe)
                    != self._selection_intent(node, recipe)
                    if isinstance(previous, Selection) and isinstance(node, Selection)
                    else previous.model_copy(update={"label": node.label}) != node
                )
            }
            from experiments.selection_volume_authoring import resolve_volume

            for node in recipe.nodes:
                previous = before.get(node.id)
                if (
                    isinstance(node, FeatureReuse)
                    and node.circular is not None
                    and isinstance(previous, FeatureReuse)
                    and previous.circular is not None
                ):
                    old_volumes = [
                        resolve_volume(self._recipe, item.volume)[2]
                        for item in previous.circular.volumes
                    ]
                    new_volumes = [
                        resolve_volume(recipe, item.volume)[2]
                        for item in node.circular.volumes
                    ]
                    if old_volumes != new_volumes:
                        affected.add(node.id)
            after_nodes = {n.id: n for n in recipe.nodes}
            # A first/last source-port consumer changes the reuse publication
            # and source-stage barriers even when its authored owner is unchanged.
            affected.update(
                demanded_reuse_source_contexts(before)
                ^ demanded_reuse_source_contexts(after_nodes)
            )
            changed_outputs = affected | (before.keys() - after_nodes.keys())
            # The build action's cached inventory also depends on its children,
            # without introducing a parent/child cycle in the evaluation DAG.
            for nodes in (before, after_nodes):
                affected.update(
                    node.managed_by
                    for key in changed_outputs
                    if (node := nodes.get(key)) is not None
                    and node.managed_by is not None
                    and isinstance(nodes.get(node.managed_by), BuildFaces)
                )
            before_influence = describe_geometry_influence(before)
            after_influence = describe_geometry_influence(after_nodes)
            before_memberships = before_influence.memberships()
            after_memberships = after_influence.memberships()
            for publisher in before_memberships.keys() | after_memberships.keys():
                before_members = before_memberships.get(publisher, frozenset())
                after_members = after_memberships.get(publisher, frozenset())
                if before_members != after_members:
                    affected.update(before_members | after_members)
            # Connected fitting changes geometry outside ordinary DAG edges.
            # Close BOTH kinds of influence together: newly affected sibling
            # surfaces must invalidate their downstream physical boundaries.
            component_members = [
                *before_memberships.values(),
                *after_memberships.values(),
            ]
            while True:
                expanded = affected | {
                    n.id for n in recipe.nodes if affected.intersection(dependencies(n))
                }
                for members in component_members:
                    if expanded.intersection(members):
                        expanded.update(members)
                if expanded == affected:
                    break
                affected = expanded
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
                if key in after_influence.axis_components
                and not affected.intersection(after_influence.axis_components[key][1])
            }
            self._connected_point_solves = {
                key: value
                for key, value in self._connected_point_solves.items()
                if key in after_influence.point_components
                and not affected.intersection(after_influence.point_components[key][1])
            }
            self._derived = {
                key: value
                for key, value in self._derived.items()
                if (key not in affected and any(n.id == key for n in recipe.nodes))
                or (
                    key.startswith("@plane/")
                    and key in after_influence.providers
                    and not affected.intersection(
                        after_influence.providers[key].members
                    )
                )
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
            current_volumes = {
                f"{node.id}/{entry.id}/{volume.id}"
                for node in recipe.nodes
                if isinstance(node, Selection)
                for entry in node.entries or []
                for volume in entry.volumes
            }
            self._volume_refresh_errors = {
                key: failure
                for key, failure in self._volume_refresh_errors.items()
                if key in current_volumes
            }
            self._recipe = recipe
            self._evaluation_origin_token = None
            self._content_token = recipe_content_token(recipe)
            self._epoch += 1
            self._revision += 1
            self._validation_reviews = {
                key: value
                for key, value in self._validation_reviews.items()
                if key[0] in after_nodes and key[0] not in affected
            }
            self._mark_needs_input()
            return self.snapshot()

    @staticmethod
    def _review_signature(inputs: SharedBoundaryInputs, complete: bool) -> str:
        from experiments.surface_primitives import primitive

        def geometry_record(record: dict[str, Any]) -> dict[str, Any]:
            # Validation output must not become its own input dependency.
            return {
                key: value
                for key, value in record.items()
                if key != "shared_boundary_review"
            }

        value = {
            "face_ids": inputs.face_ids,
            "faces": [geometry_record(record) for record in inputs.faces],
            "target": inputs.target,
            "sources": [
                {**source, "record": geometry_record(source["record"])}
                for source in inputs.sources
            ],
            # Strip numerical diagnostics/residuals by using the exact analytic
            # primitive consumed by the kernel, without rounding coordinates.
            "geometry": inputs.geometry
            if "axis" in inputs.geometry
            else primitive(inputs.geometry),
            "require_complete": complete,
        }
        return hashlib.sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()

    def _shared_boundary_review(
        self, task_id: str, epoch: int, inputs: SharedBoundaryInputs, complete: bool
    ) -> dict[str, Any]:
        signature = self._review_signature(inputs, complete)
        key = (task_id, complete)
        with self.lock:
            if epoch != self._epoch:
                raise StaleGraph("graph changed during shared-boundary review")
            cached = self._validation_reviews.get(key)
            if cached is not None and cached[0] == signature:
                return deepcopy(cached[1])
        from experiments.shared_face_boundaries import check_shared_boundaries

        review = check_shared_boundaries(
            inputs.faces,
            inputs.target,
            inputs.sources,
            inputs.geometry,
            require_complete=complete,
        )
        with self.lock:
            if epoch != self._epoch:
                raise StaleGraph("graph changed during shared-boundary review")
            self._validation_reviews[key] = (signature, deepcopy(review))
        return review

    @staticmethod
    def _owner_review_inputs(
        owner: BuildFaces, snapshot: dict[str, Any], nodes: dict[str, Feature]
    ) -> SharedBoundaryInputs:
        assert owner.target is not None
        faces = list(
            dict.fromkeys(
                [
                    *owner.reused_faces,
                    *(
                        node.id
                        for node in nodes.values()
                        if node.managed_by == owner.id
                        and isinstance(node, (TrimmedFace, ArrangedFace))
                    ),
                ]
            )
        )
        raw = snapshot["results"][owner.target.feature]
        geometry = (
            raw
            if owner.target.surface is None
            else raw["surfaces"][owner.target.surface]
        )
        return SharedBoundaryInputs(
            faces,
            [snapshot["results"][key] for key in faces],
            owner.target.model_dump(),
            FeatureGraph._boundary_review_sources(
                snapshot, nodes, owner.boundary_sources
            ),
            geometry,
        )

    @staticmethod
    def _boundary_review_sources(
        snapshot: dict[str, Any], nodes: dict[str, Feature], face_ids: list[str]
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for face_id in face_ids:
            node = nodes[face_id]
            assert isinstance(node, (TrimmedFace, ArrangedFace))
            sources.append(
                {
                    "id": face_id,
                    "record": snapshot["results"][face_id],
                    "boundary_uses": [
                        {
                            "intersection": snapshot["results"][boundary.intersection],
                            "keep": boundary.keep,
                        }
                        for boundary in node.boundaries
                    ]
                    if isinstance(node, TrimmedFace)
                    else [],
                }
            )
        return sources

    def evaluation_roots(
        self, token: str, targets: list[str] | None, all_actions: bool
    ) -> frozenset[str]:
        """Canonical request identity without building a geometry snapshot."""
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            nodes = {node.id for node in self._recipe.nodes}
            if all_actions and targets is not None:
                raise ValueError("ensure all cannot also specify targets")
            roots = (
                nodes
                if all_actions
                else set(
                    [self._recipe.output]
                    if targets is None and self._recipe.output is not None
                    else []
                    if targets is None
                    else targets
                )
            )
            if not nodes and targets is None:
                return frozenset()
            if not roots or not roots <= nodes:
                raise ValueError("evaluation targets must name existing actions")
            return frozenset(roots)

    def _ensure_plan(
        self, token: str, targets: list[str] | None, all_actions: bool
    ) -> ExecutionPlan:
        roots = set(self.evaluation_roots(token, targets, all_actions))
        nodes = {node.id: node for node in self._recipe.nodes}
        return compile_execution_plan(
            self._recipe,
            roots,
            describe_geometry_influence(nodes),
            selected_named_solves=isinstance(self.workspace, ScanWorkspace),
        )

    def readiness_snapshot(
        self, token: str, targets: list[str] | None = None, all_actions: bool = False
    ) -> dict[str, object]:
        """Report failures in the requested closure, including managed children."""
        with self.lock:
            plan = self._ensure_plan(token, targets, all_actions)
            return {
                **self.snapshot(),
                "required_failures": [
                    key
                    for key in plan.order
                    if self._states.get(key) in {"failed", "blocked"}
                ],
            }

    def needs_evaluation(
        self, token: str, targets: list[str] | None = None, all_actions: bool = False
    ) -> bool:
        """Check requested outputs, geometry providers and validation together."""
        return self._readiness(token, targets, all_actions)[0]

    def readiness_status(
        self, token: str, targets: list[str] | None = None, all_actions: bool = False
    ) -> tuple[bool, dict[str, object] | None]:
        """Reuse the inspected snapshot when returning settled outputs."""
        with self.lock:
            needed, snapshot = self._readiness(token, targets, all_actions)
            if needed:
                return True, None
            plan = self._ensure_plan(token, targets, all_actions)
            return False, {
                **(snapshot if snapshot is not None else self.snapshot()),
                "required_failures": [
                    key
                    for key in plan.order
                    if self._states.get(key) in {"failed", "blocked"}
                ],
            }

    def _readiness(
        self, token: str, targets: list[str] | None, all_actions: bool
    ) -> tuple[bool, dict[str, Any] | None]:
        with self.lock:
            plan = self._ensure_plan(token, targets, all_actions)
            nodes = {node.id: node for node in self._recipe.nodes}
            if self._refresh_volumes:
                from experiments.selection_volume_authoring import (
                    authoring_fingerprint,
                    resolve_volume,
                )

                for reference_key in self._volume_references(plan):
                    selection_id, entry_id, volume_id = reference_key.split("/")
                    owner, entry, volume = resolve_volume(
                        self._recipe,
                        VolumeReference(
                            selection=selection_id, entry=entry_id, volume=volume_id
                        ),
                    )
                    if entry.scan.key != source_binding(self.workspace).key:
                        continue
                    fingerprint = authoring_fingerprint(
                        self._recipe, owner, entry, volume
                    )
                    if (
                        fingerprint != volume.authoring_fingerprint
                        and self._volume_refresh_errors.get(reference_key, (None, ""))[
                            0
                        ]
                        != fingerprint
                    ):
                        return True, None
            if any(
                self._states[key] not in {"ready", "failed", "blocked", "needs_input"}
                for key in plan.order
                if key in nodes
            ):
                return True, None
            for task, (kind, identifier) in plan.solves.items():
                if task not in plan.order:
                    continue
                members = plan.aliases[task]
                if any(
                    self._states[key] in {"failed", "blocked", "needs_input"}
                    for key in members
                ):
                    continue
                if kind == "axis" and identifier not in self._connected_solves:
                    return True, None
                if kind == "point" and identifier not in self._connected_point_solves:
                    return True, None
                if task.startswith("@plane/") and task not in self._derived:
                    return True, None
            if any(axis in self._connected_solves for axis in plan.explicit_axes):
                return True, None
            snapshot: dict[str, Any] | None = None
            for key in plan.order:
                node = nodes.get(key)
                if (
                    not isinstance(node, BuildFaces)
                    or not node.boundary_sources
                    or node.target is None
                    or self._states[key] != "ready"
                ):
                    continue
                if snapshot is None:
                    snapshot = cast(dict[str, Any], self.snapshot())
                faces = [
                    *node.reused_faces,
                    *(
                        child.id
                        for child in nodes.values()
                        if child.managed_by == key
                        and isinstance(child, (TrimmedFace, ArrangedFace))
                    ),
                ]
                if any(self._states[face] in {"failed", "blocked"} for face in faces):
                    continue
                try:
                    inputs = self._owner_review_inputs(node, snapshot, nodes)
                except KeyError:
                    return True, None
                if any(self._states[face] != "ready" for face in inputs.face_ids):
                    continue
                cached = self._validation_reviews.get((key, True))
                if (
                    not self._derived[key]
                    .get("shared_boundary_review", {})
                    .get("complete")
                    or cached is None
                    or cached[0] != self._review_signature(inputs, True)
                ):
                    return True, None
            return False, snapshot

    def ensure_current(
        self,
        token: str,
        targets: list[str] | None = None,
        all_actions: bool = False,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        """Reuse current results; unchanged failures require an explicit retry."""
        check_cancelled(cancelled)
        with self.lock:
            needed, snapshot = self._readiness(token, targets, all_actions)
            if not needed:
                return snapshot if snapshot is not None else self.snapshot()
        return self.evaluate(
            token,
            all_actions=all_actions,
            targets=targets,
            retry_failed=False,
            cancelled=cancelled,
        )

    @native_replay_scope()
    def evaluate(
        self,
        token: str,
        target: str | None = None,
        all_actions: bool = False,
        *,
        targets: list[str] | None = None,
        retry_failed: bool = True,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        check_cancelled(cancelled)
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            references: set[str] = set()
            if self._refresh_volumes and any(
                isinstance(node, Selection) and node.entries is not None
                for node in self._recipe.nodes
            ):
                if target is not None and (targets is not None or all_actions):
                    raise ValueError(
                        "evaluation target cannot also specify targets or all actions"
                    )
                plan = self._ensure_plan(
                    token, [target] if target is not None else targets, all_actions
                )
                references = self._volume_references(plan)
            original = self._recipe.model_copy(deep=True) if references else None
            epoch = self._epoch
        if original is not None:
            from experiments.selection_volume_authoring import authoring_fingerprint
            from experiments.selection_volume_transactions import staged_refresh

            updated, errors = staged_refresh(
                self, original, references=references, cancelled=cancelled
            )
            failures = {
                key: value
                for key, value in self._volume_refresh_errors.items()
                if key not in references
            }
            for node in updated.nodes:
                if isinstance(node, Selection) and node.entries is not None:
                    for entry in node.entries:
                        for volume in entry.volumes:
                            key = f"{node.id}/{entry.id}/{volume.id}"
                            if key in errors:
                                failures[key] = (
                                    authoring_fingerprint(updated, node, entry, volume),
                                    errors[key],
                                )
            if updated != original:
                candidate = FeatureGraph(
                    self.workspace,
                    original,
                    refresh_volumes=False,
                    fit_executor=self.fit_executor,
                )
                with self.lock:
                    if token != self._token() or epoch != self._epoch:
                        raise StaleGraph(
                            "graph changed during volume refresh; candidate discarded"
                        )
                    candidate._states = self._states.copy()
                    candidate._results = deepcopy(self._results)
                    candidate._derived = deepcopy(self._derived)
                    candidate._connected_solves = deepcopy(self._connected_solves)
                    candidate._connected_point_solves = deepcopy(
                        self._connected_point_solves
                    )
                    candidate._errors = self._errors.copy()
                    candidate._diagnostics = deepcopy(self._diagnostics)
                    candidate._validation_reviews = deepcopy(self._validation_reviews)
                _ = candidate.replace(updated, token)
                candidate._volume_refresh_errors = failures
                candidate._mark_needs_input()
                candidate_revision = candidate._revision
                error: Exception | None = None
                try:
                    _ = candidate._evaluate(
                        str(candidate.snapshot()["token"]),
                        target,
                        all_actions,
                        targets=targets,
                        retry_failed=retry_failed,
                        cancelled=cancelled,
                    )
                except (StaleGraph, ScanCancelled):
                    raise
                except Exception as failure:
                    if candidate._revision == candidate_revision:
                        raise
                    error = failure
                with self.lock:
                    check_cancelled(cancelled)
                    if token != self._token() or epoch != self._epoch:
                        raise StaleGraph(
                            "graph changed during volume refresh; candidate discarded"
                        )
                    # Candidate revisions are private. Publish the entire recipe
                    # and its matching stores once, behind the existing fence.
                    self._recipe = candidate._recipe
                    self._content_token = candidate._content_token
                    self._states = candidate._states
                    self._results = candidate._results
                    self._derived = candidate._derived
                    self._connected_solves = candidate._connected_solves
                    self._connected_point_solves = candidate._connected_point_solves
                    self._errors = candidate._errors
                    self._diagnostics = candidate._diagnostics
                    self._validation_reviews = candidate._validation_reviews
                    self._volume_cache = candidate._volume_cache
                    self._volume_refresh_errors = candidate._volume_refresh_errors
                    self._evaluation_origin_token = token
                    self._epoch += 1
                    self._revision += 1
                if error is not None:
                    raise error
                return self.snapshot()
            with self.lock:
                if token != self._token() or epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during volume refresh; candidate discarded"
                    )
                if failures != self._volume_refresh_errors:
                    self._volume_refresh_errors = failures
                    self._mark_needs_input()
                    self._revision += 1
        return self._evaluate(
            token,
            target,
            all_actions,
            targets=targets,
            retry_failed=retry_failed,
            cancelled=cancelled,
        )

    def _circular_selection(
        self,
        recipe: Recipe,
        reuse: FeatureReuse,
        node: ReuseSelection,
        rotation: np.ndarray,
        translation: np.ndarray,
        source_ids: list[int],
        cancelled: Callable[[], bool] | None,
    ) -> tuple[list[int], dict[str, Any]]:
        from experiments.selection_entry_runtime import volume_frame, volume_geometry
        from experiments.selection_volume import apply_selection_volume
        from experiments.selection_volume_authoring import (
            authoring_fingerprint,
            resolve_volume,
        )

        assert reuse.circular is not None
        reference = next(
            item.volume
            for item in reuse.circular.volumes
            if item.fit == node.fit and item.source_selection == node.source_selection
        )
        owner, entry, volume = resolve_volume(recipe, reference)
        if (
            authoring_fingerprint(recipe, owner, entry, volume)
            != volume.authoring_fingerprint
        ):
            raise ValueError(
                "Refresh the circular source volume before selecting target observations"
            )
        geometry = volume_geometry(self._volume_cache, volume, cancelled=cancelled)
        volume_origin, volume_basis = volume_frame(geometry)
        frame, origin = self.workspace.frame, self.workspace.origin
        # World scan observations -> inverse placed workspace frame -> captured frame.
        query_rotation = frame @ rotation.T @ frame.T @ volume_basis
        query_translation = (
            (-origin @ frame - translation) @ rotation.T @ frame.T
            + origin
            - volume_origin
        ) @ volume_basis
        with self.lock:
            previous = {
                other.id: self._derived.get(other.id, {}).get("ids", [])
                for other in recipe.nodes
                if isinstance(other, ReuseSelection)
                and other.reuse == node.reuse
                and other.id != node.id
                and self._states.get(other.id) == "ready"
            }
        remaining = min(
            MAX_CIRCULAR_SELECTION_VERTICES,
            MAX_CIRCULAR_MEMBERSHIPS - sum(map(len, previous.values())),
        )
        if remaining < 1:
            raise ValueError("Circular pattern exceeds its aggregate membership budget")
        ids = apply_selection_volume(
            self.workspace.data.xyz,
            self.workspace.data.normals,
            self.workspace.data.weights,
            geometry,
            query_rotation=query_rotation,
            query_translation=query_translation,
            cancelled=cancelled,
            max_selected=remaining,
        )
        selected_ids = set(ids)
        overlaps: list[dict[str, Any]] = []
        for selection, previous_ids in previous.items():
            count = len(selected_ids.intersection(previous_ids))
            if count:
                overlaps.append({"selection": selection, "vertices": count})
        return ids, {
            "occurrence": node.occurrence,
            "volume_reference": reference.model_dump(),
            "volume_archive_sha256": volume.archive.sha256,
            "placement": {
                "rotation": rotation.tolist(),
                "translation": translation.tolist(),
            },
            "selection_diagnostics": {
                "source_vertices": len(source_ids),
                "selected_vertices": len(ids),
                "overlap_occurrence_zero": len(selected_ids.intersection(source_ids)),
                "overlaps": overlaps,
            },
        }

    def _prepare_numerical_fit(
        self,
        node: SurfaceFit,
        ids: list[int],
        *,
        intent: Callable[[], dict[str, Any]],
        reuse_inputs: list[dict[str, Any]],
        support: Callable[[], SupportIntent],
        epoch: int,
        stop_fits: Event,
        cancelled: Callable[[], bool] | None,
    ) -> Callable[[], dict[str, Any]]:
        """Snapshot numerical inputs on the coordinator, never in workers."""
        assert isinstance(self.workspace, ScanWorkspace)
        parameters: dict[str, Any] = {}
        domain = None if node.support_authority == "prototype" else node.axial_domain
        if reuse_inputs and len(reuse_inputs) != len(node.selections):
            raise ValueError("a fit cannot mix reused and ordinary selections")
        if node.kind in {"cylinder", "cone"}:
            descriptor = self.workspace.source_descriptor
            if descriptor is None:
                raise ValueError(f"{node.kind} fitting requires an accepted source")
            binding = initialization_binding(
                descriptor.source_sha256,
                descriptor.interpretation_sha256,
                ids,
                {
                    "family": node.kind,
                    "recipe": intent(),
                    "support_policy_version": SUPPORT_POLICY_VERSION,
                },
                policy_version=CONE_POLICY_VERSION
                if node.kind == "cone"
                else CYLINDER_POLICY_VERSION,
            )
            hint = None
            if reuse_inputs:
                hints = [value.get("fit_geometry") for value in reuse_inputs]
                if any(value != hints[0] for value in hints[1:]):
                    raise ValueError(
                        "reused fit selections must share one transformed source fit"
                    )
                hint = deepcopy(hints[0])
            parameters = {
                "binding": binding,
                "hint": hint,
                "hint_binding_sha256": binding.sha256 if hint is not None else None,
                "support": support(),
            }
        elif reuse_inputs:
            initials = [value.get("fit_initial") for value in reuse_inputs]
            domains = [value["target_axial_domain"] for value in reuse_inputs]
            if any(value != initials[0] for value in initials[1:]) or any(
                value != domains[0] for value in domains[1:]
            ):
                raise ValueError(
                    "reused fit selections must share one transformed source fit"
                )
            domain = tuple(domains[0]) if domains[0] is not None else None
        points = self.workspace.local[ids]
        weights = self.workspace.data.weights[ids]
        normals = self.workspace.data.normals[ids] @ self.workspace.frame
        for array in (points, weights, normals):
            array.flags.writeable = False

        def fit_cancelled() -> bool:
            if stop_fits.is_set() or (cancelled is not None and cancelled()):
                return True
            with self.lock:
                return epoch != self._epoch

        def run() -> dict[str, Any]:
            if fit_cancelled():
                raise FitCancelled("Fit cancelled before numerical evaluation")
            result = fit_seed(
                points,
                weights,
                normals,
                node.kind,
                None,
                domain,
                cancelled=fit_cancelled,
                **parameters,
            )
            if fit_cancelled():
                raise FitCancelled("Fit cancelled before publishing a result")
            result["ids"] = ids
            return result

        return run

    def _finish_numerical_fit(
        self,
        pending: dict[str, Future[dict[str, Any]]],
        epoch: int,
        cancelled: Callable[[], bool] | None,
        failures: dict[str, set[str]],
        note_failure: Callable[[str, Exception, set[str]], None],
    ) -> None:
        """Publish in submission order; worker completion never mutates stores."""
        key = next(iter(pending))
        future = pending.pop(key)
        try:
            value = future.result()
        except FitCancelled as error:
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during numerical fit") from error
            raise ScanCancelled() from error
        except Exception as error:
            if isinstance(error, (StaleGraph, ScanCancelled)):
                raise
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during numerical fit") from error
                self._states[key] = "failed"
                self._errors[key] = str(error)
                diagnostic = failure_diagnostic(error)
                if diagnostic is not None:
                    self._diagnostics[key] = diagnostic
                else:
                    _ = self._diagnostics.pop(key, None)
                self._revision += 1
            note_failure(key, error, {key})
            return
        check_cancelled(cancelled)
        with self.lock:
            if epoch != self._epoch:
                raise StaleGraph("graph changed during numerical fit; result discarded")
            if key in failures:
                return
            self._states[key] = "ready"
            self._derived[key] = value
            _ = self._errors.pop(key, None)
            _ = self._diagnostics.pop(key, None)
            self._revision += 1

    def _evaluate(
        self,
        token: str,
        target: str | None = None,
        all_actions: bool = False,
        *,
        targets: list[str] | None = None,
        retry_failed: bool = True,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        pending: dict[str, Future[dict[str, Any]]] = {}
        stop = Event()
        try:
            return self._evaluate_impl(
                token,
                target,
                all_actions,
                targets=targets,
                retry_failed=retry_failed,
                cancelled=cancelled,
                pending_fits=pending,
                stop_fits=stop,
            )
        finally:
            # Do not release the source/evaluation lease while owned numerical
            # children are still active. HTTP can report cancel-requested while
            # a non-cooperative child finishes at its next safe boundary.
            stop.set()
            for future in pending.values():
                _ = future.cancel()
            if pending:
                _ = wait(tuple(pending.values()))

    def _drain_numerical_fits(
        self,
        pending: dict[str, Future[dict[str, Any]]],
        epoch: int,
        cancelled: Callable[[], bool] | None,
        failures: dict[str, set[str]],
        note_failure: Callable[[str, Exception, set[str]], None],
        prerequisites: set[str] | None = None,
    ) -> None:
        # Bound selected-array copies to four jobs, irrespective of the supplied
        # executor's worker count, and preserve order when joining dependencies.
        while pending and (
            prerequisites is None
            or len(pending) >= 4
            or prerequisites.intersection(pending)
        ):
            self._finish_numerical_fit(
                pending, epoch, cancelled, failures, note_failure
            )

    def _record_evaluation_failure(
        self,
        key: str,
        error: Exception,
        epoch: int,
        note_failure: Callable[[str, Exception, set[str]], None],
    ) -> None:
        if isinstance(error, FitCancelled):
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during numerical fit") from error
            raise ScanCancelled() from error
        if isinstance(error, (ScanCancelled, StaleGraph)):
            raise error
        with self.lock:
            if epoch == self._epoch:
                self._states[key] = "failed"
                self._errors[key] = str(error)
                diagnostic = failure_diagnostic(error)
                if diagnostic is not None:
                    self._diagnostics[key] = diagnostic
                else:
                    _ = self._diagnostics.pop(key, None)
                self._revision += 1
        note_failure(key, error, {key})

    def _evaluate_impl(
        self,
        token: str,
        target: str | None = None,
        all_actions: bool = False,
        *,
        pending_fits: dict[str, Future[dict[str, Any]]],
        stop_fits: Event,
        targets: list[str] | None = None,
        retry_failed: bool = True,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, object]:
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            recipe, epoch = self._recipe.model_copy(deep=True), self._epoch
            nodes = {n.id: n for n in recipe.nodes}
            if not nodes and targets is None and target is None:
                return self.snapshot()
            influence = describe_geometry_influence(nodes)
            connected_components = influence.axis_components
            point_components = influence.point_components
            plane_relationship_components = influence.plane_components
            if targets is not None:
                if target is not None or all_actions:
                    raise ValueError(
                        "evaluation targets cannot also specify a target or all actions"
                    )
                roots = set(targets)
                if not roots or not roots <= nodes.keys():
                    raise ValueError("evaluation targets must name existing actions")
            elif all_actions:
                if target is not None:
                    raise ValueError("evaluate all cannot also specify a target")
                roots = set(nodes)
            else:
                target = recipe.output if target is None else target
                if target not in nodes or not isinstance(
                    nodes[target],
                    (
                        JointFit,
                        SurfaceFit,
                        Growth,
                        SurfaceSelection,
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
                        CoaxialGroup,
                        PlaneRelationship,
                        DirectionAlignment,
                        SurfaceIntersection,
                        TrimmedFace,
                        ArrangedFace,
                        BuildFaces,
                        Body,
                    ),
                ):
                    raise ValueError(
                        "evaluation target must be a source, selection, reuse, region, fit, point, axis, plane, frame, scale, transform, solve, face, body, or growth action"
                    )
                roots = {target}
            plan = compile_execution_plan(
                recipe,
                roots,
                influence,
                selected_named_solves=isinstance(self.workspace, ScanWorkspace),
            )
            order = plan.order

        def recovery_recipe() -> dict[str, Any]:
            if self._fit_intent is not None:
                return self._fit_intent
            from experiments.selection_volume_authoring import semantic_recipe

            if any(
                isinstance(node, Selection) and node.entries is not None
                for node in recipe.nodes
            ):
                return semantic_recipe(recipe, source_binding(self.workspace))
            return recipe.model_dump(mode="json")

        def membership(selection_id: str) -> list[int]:
            selection = nodes[selection_id]
            if isinstance(selection, Selection) and selection.entries is None:
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

        def input_result(reference: FeatureInput) -> dict[str, Any]:
            if isinstance(reference, str):
                return resolved_result(reference)
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during output resolution")
                return resolve_output(
                    reference,
                    nodes,
                    self._input_values(),
                    coordinates=self.workspace.local,
                )

        def boundary_source_records(face_ids: list[str]) -> list[dict[str, Any]]:
            sources: list[dict[str, Any]] = []
            for source_id in face_ids:
                source_face = nodes[source_id]
                assert isinstance(source_face, (TrimmedFace, ArrangedFace))
                sources.append(
                    {
                        "id": source_id,
                        "record": derived_result(source_id),
                        "boundary_uses": [
                            {
                                "intersection": derived_result(boundary.intersection),
                                "keep": boundary.keep,
                            }
                            for boundary in source_face.boundaries
                        ]
                        if isinstance(source_face, TrimmedFace)
                        else [],
                    }
                )
            return sources

        def resolved_result(
            node_id: str, excluded: set[str] | None = None
        ) -> dict[str, Any]:
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
                    value.update(connected_axis_result(node, value, solve))
                    value["resolved_by"] = "connected_fits"
                elif solve is not None and isinstance(node, PlaneDefinition):
                    solved_plane = solve.get("reference_planes", {}).get(node_id)
                    if solved_plane is not None:
                        value = deepcopy(solved_plane)
                    else:
                        assert node.axis is not None
                        axis_value = deepcopy(self._derived[node.axis])
                        axis_value.update(
                            connected_axis_result(
                                cast(AxisDefinition, nodes[node.axis]),
                                axis_value,
                                solve,
                            )
                        )
                        value = reference_plane_result(
                            axis_value, initialized_plane(node, value)
                        )
                elif solve is not None and isinstance(node, SurfaceFit):
                    surface = solve.get("surfaces", {}).get(node_id)
                    if surface is not None:
                        value = deepcopy(dict(surface))
                if point_solve is not None and isinstance(node, PointDefinition):
                    _ = value.update(deepcopy(point_solve["point"]))
                elif point_solve is not None and isinstance(node, SurfaceFit):
                    surface = point_solve["surfaces"].get(node_id)
                    if surface is not None:
                        value = deepcopy(surface)
                if isinstance(node, SurfaceFit):
                    for relationship in recipe.nodes:
                        if (
                            isinstance(relationship, EqualRadii)
                            and relationship.id not in (excluded or set())
                            and self._states.get(relationship.id) == "ready"
                        ):
                            adjusted = self._derived.get(relationship.id, {}).get(
                                "surfaces", {}
                            )
                            if node_id in adjusted:
                                value = deepcopy(adjusted[node_id])
                for relationship in recipe.nodes:
                    if (
                        isinstance(
                            relationship, (PlaneRelationship, DirectionAlignment)
                        )
                        and relationship.id not in (excluded or set())
                        and self._states.get(relationship.id) == "ready"
                    ):
                        context = self._derived.get(relationship.id, {})
                        adjusted = context.get("resolved", context.get("surfaces", {}))
                        if node_id in adjusted:
                            value = deepcopy(adjusted[node_id])
                for context_id, context in self._derived.items():
                    if (
                        context_id.startswith("@plane/")
                        and context_id not in (excluded or set())
                        and node_id in context.get("resolved", {})
                    ):
                        value = deepcopy(context["resolved"][node_id])
                return value

        def referenced_surface(reference: SurfaceReference) -> dict[str, Any]:
            if reference.surface is None:
                return resolved_result(reference.feature)
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during boundary evaluation")
                context = (
                    resolved_result(reference.feature)
                    if isinstance(nodes[reference.feature], EqualRadii)
                    else self._results.get(reference.feature)
                    or self._derived[reference.feature]
                )
                surfaces = context.get("surfaces")
                if surfaces is None or reference.surface not in surfaces:
                    raise ValueError(
                        "geometry context omitted the declared member surface"
                    )
                return deepcopy(dict(surfaces[reference.surface]))

        def has_explicit_support(surface: SurfaceFit) -> bool:
            if surface.support_authority == "prototype":
                _, source, _ = prototype_support_source(surface, nodes)
                return has_explicit_support(source)
            return surface.axial_domain is not None

        def fitted_ids(surface: SurfaceFit) -> list[int]:
            if (
                isinstance(self.workspace, ScanWorkspace)
                and surface.kind in {"cylinder", "cone"}
                and surface.support_authority is None
                and isinstance(nodes.get(surface.managed_by or ""), FeatureReuse)
                and any(
                    isinstance(nodes[ref], ReuseSelection) for ref in surface.selections
                )
                and (
                    surface.axial_domain is not None
                    or any(
                        isinstance(selection := nodes[ref], ReuseSelection)
                        and isinstance(source := nodes[selection.fit], SurfaceFit)
                        and has_explicit_support(source)
                        for ref in surface.selections
                    )
                )
            ):
                assert surface.managed_by is not None
                raise UnsupportedSupportTransfer(surface.id, surface.managed_by)
            return sorted({i for ref in surface.selections for i in membership(ref)})

        def effective_support(
            surface: SurfaceFit, excluded: set[str] | None = None
        ) -> SupportIntent:
            if surface.support_authority != "prototype":
                return legacy_support(surface.axial_domain)
            owner, source, occurrence = prototype_support_source(surface, nodes)
            match = resolved_result(owner.id, excluded)["matches"][occurrence]
            return transform_support(
                effective_support(source, excluded),
                np.asarray(match["rotation"], dtype=float),
                np.asarray(match["translation"], dtype=float),
            )

        def selected(surface: SurfaceFit) -> FitSelection:
            return FitSelection(
                surface.id,
                fitted_ids(surface),
                surface.kind,
                None
                if isinstance(self.workspace, ScanWorkspace)
                and surface.support_authority == "prototype"
                else surface.axial_domain,
                support=effective_support(surface)
                if isinstance(self.workspace, ScanWorkspace)
                and surface.kind in {"cylinder", "cone"}
                else None,
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

        def solve_selected_symmetry(node: JointFit | AxisSolve) -> dict[str, Any]:
            if isinstance(node, JointFit):
                component = named_direction_component(node, nodes)
                if component is not None:
                    return solve_direction_component(
                        component[0],
                        component[1],
                        set(node.constraints) | {node.id},
                        radius_groups=tuple(
                            cast(EqualRadii, nodes[key])
                            for key in node.constraints
                            if isinstance(nodes[key], EqualRadii)
                        ),
                    )
            """Lower saved factors directly from original memberships and roles."""
            surfaces, _, _ = selected_symmetry_inputs(node, nodes)
            reject_overlaps(surfaces)
            axis_records: dict[str, AxisInput] = {}
            plane_records: dict[str, PlaneInput] = {}

            def add_axis(key: str) -> None:
                if key in axis_records:
                    return
                definition = cast(AxisDefinition, nodes[key])
                free = definition.initial_parameters is not None
                if not free or definition.placement is not None:
                    value = resolved_result(key, plan.read_exclusions.get(node.id))
                else:
                    # The declared free vector is merely an optional proposal.
                    # Overflow/invalid geometry must not gate fresh recovery.
                    try:
                        with np.errstate(over="ignore", invalid="ignore"):
                            value = directed_axis_result(
                                definition,
                                parameters=list(definition.initial_parameters or ()),
                            )
                    except ValueError:
                        value = {}
                axis_records[key] = AxisInput(value, free=free)

            def add_plane(key: str) -> None:
                if key in plane_records:
                    return
                definition = cast(PlaneDefinition, nodes[key])
                if definition.axis is None:
                    raise ValueError(
                        "independent reference planes require a direction joint, not a legacy shared-axis solve"
                    )
                add_axis(definition.axis)
                free = axis_records[definition.axis].free
                if not free or definition.placement is not None:
                    value = resolved_result(key, plan.read_exclusions.get(node.id))
                else:
                    try:
                        with np.errstate(over="ignore", invalid="ignore"):
                            value = reference_plane_result(
                                axis_records[definition.axis].result, definition
                            )
                    except (ValueError, KeyError):
                        value = {}
                plane_records[key] = PlaneInput(
                    value,
                    free=free,
                    axis=definition.axis,
                    construction=definition.construction,
                )

            rotations: list[RotationInput] = []
            mirrors: list[MirrorInput] = []
            distances: list[RadiusDistanceInput] = []
            relationships: list[tuple[str, tuple[str, ...]]] = []
            member_axes: dict[str, str] = {}
            if isinstance(node, JointFit):
                sides, _ = joint_surfaces(node, nodes)
                declared_axes = {side.axis for side in sides if side.axis is not None}
                if len(declared_axes) > 1:
                    raise ValueError("a named coaxial group requires one axis provider")
                if declared_axes:
                    common = next(iter(declared_axes))
                    add_axis(common)
                else:
                    common = "@joint/" + node.id
                    axis_records[common] = AxisInput({}, free=True)
                member_axes.update((side.id, common) for side in sides)
                for key in node.constraints:
                    factor = nodes[key]
                    if isinstance(factor, Perpendicular):
                        member_axes[factor.plane] = common
                    elif isinstance(factor, RotationalSymmetry):
                        rotations.append(
                            RotationInput(
                                common, cast(tuple[str, str, str], tuple(factor.planes))
                            )
                        )
            else:
                common = node.axis
                add_axis(common)
                factors = [nodes[key] for key in node.factors]
                # Retain the complete-cluster validation, while the component's
                # radius-distance equation itself supplies the parallel normal.
                _ = constrained_mirror_radii(factors, nodes)
                relation_datums = {
                    endpoint.feature
                    for factor in factors
                    if isinstance(factor, PlaneRelationship)
                    for endpoint in plane_relationship_endpoints(factor, nodes)
                    if isinstance(nodes[endpoint.feature], PlaneDefinition)
                }
                full_datums = {
                    endpoint.feature
                    for factor in factors
                    if isinstance(factor, PlaneRelationship)
                    and factor.relation == "coincident"
                    for endpoint in plane_relationship_endpoints(factor, nodes)
                } | {
                    surface.reference_plane
                    for surface in surfaces
                    if surface.reference_plane
                }
                for factor in factors:
                    if isinstance(factor, MirrorSymmetry):
                        add_plane(factor.plane)
                        mirrors.append(
                            MirrorInput(
                                factor.plane,
                                cast(tuple[str, str], tuple(factor.surfaces)),
                            )
                        )
                    elif isinstance(factor, EqualQuantities):
                        radius, distance = equality_measurements(factor)
                        add_plane(distance.reference_plane)
                        distances.append(
                            RadiusDistanceInput(
                                radius.surface,
                                distance.surface,
                                distance.reference_plane,
                            )
                        )
                    elif isinstance(factor, PlaneRelationship):
                        ids: list[str] = []
                        for endpoint in plane_relationship_endpoints(factor, nodes):
                            if not endpoint.is_binding:
                                raise ValueError(
                                    "named plane factors require direct parameter owners"
                                )
                            ids.append(endpoint.feature)
                            if endpoint.feature in relation_datums:
                                add_plane(endpoint.feature)
                                record = plane_records[endpoint.feature]
                                if record.free and endpoint.feature not in full_datums:
                                    definition = initialized_plane(
                                        cast(PlaneDefinition, nodes[endpoint.feature]),
                                        record.result,
                                    )
                                    plane_records[endpoint.feature] = PlaneInput(
                                        record.result,
                                        True,
                                        axis=record.axis,
                                        construction=record.construction,
                                        offset_from_axis=definition.offset,
                                    )
                        relationships.append((factor.relation, tuple(ids)))

            selected_surfaces: list[SelectedSurface] = []
            for surface in surfaces:
                axis = member_axes.get(
                    surface.id, fit_axis(surface, nodes, influence.axis_bindings)
                )
                datum = surface.reference_plane
                construction = None
                if datum is not None:
                    add_plane(datum)
                    definition = cast(PlaneDefinition, nodes[datum])
                    axis, construction = definition.axis, definition.construction
                elif surface.kind == "plane" and axis is not None:
                    construction = "perpendicular_to_axis"
                if surface.kind != "plane" and axis is None:
                    axis = "@member/" + surface.id
                    axis_records[axis] = AxisInput({}, free=True)
                elif axis is not None and axis not in axis_records:
                    add_axis(axis)
                selected_surfaces.append(
                    SelectedSurface(selected(surface), axis, datum, construction)
                )
            fitted = fit_selected_symmetry_group(
                self.workspace,
                SelectedSymmetryGroup(
                    tuple(selected_surfaces),
                    common,
                    axis_records,
                    plane_records,
                    rotations=tuple(rotations),
                    mirrors=tuple(mirrors),
                    radius_distances=tuple(distances),
                    relationships=tuple(relationships),
                    gradient_tolerance=1e-11
                    if any(
                        isinstance(nodes[key], CoaxialGroup)
                        for key in getattr(node, "factors", ())
                    )
                    else None,
                    axis_anchor_reference=(0.0, 0.0, 0.0)
                    if any(
                        isinstance(nodes[key], CoaxialGroup)
                        for key in getattr(node, "factors", ())
                    )
                    else None,
                    common_axis_directed=not axis_records[common].free,
                    axis_alias_signs={key: 1 for key in axis_records},
                    plane_alias_signs={key: 1 for key in plane_records},
                    semantic_intent={
                        "recipe": recovery_recipe(),
                        "support_policy_version": SUPPORT_POLICY_VERSION,
                    },
                ),
            )
            if any(
                isinstance(nodes[key], CoaxialGroup)
                for key in getattr(node, "factors", ())
            ):
                if axis_records[common].free:
                    direction = np.asarray(fitted["axis_display"])
                    reference = np.asarray(
                        axis_records[common].result.get("axis_display", [])
                    )
                    if (
                        reference.shape == (3,)
                        and np.isfinite(reference).all()
                        and np.linalg.norm(reference) > 0
                    ):
                        reference = reference / np.linalg.norm(reference)
                    else:
                        reference = np.zeros(3)
                    alignment = float(direction @ reference)
                    if abs(alignment) > np.sqrt(2e-10):
                        if alignment < 0:
                            direction = -direction
                    else:
                        if direction[int(np.argmax(np.abs(direction)))] < 0:
                            direction = -direction
                        if cast(AxisDefinition, nodes[common]).direction_reversed:
                            direction = -direction
                    fitted["axis_display"] = direction.tolist()
                    fitted["axes"][common]["axis_display"] = direction.tolist()
                    for key, record in plane_records.items():
                        if (
                            record.offset_from_axis is not None
                            and record.construction == "perpendicular_to_axis"
                        ):
                            definition = initialized_plane(
                                cast(PlaneDefinition, nodes[key]), record.result
                            )
                            plane = reference_plane_result(fitted, definition)
                            fitted["planes"][key].update(plane)
                            fitted["equations"][key] = plane["plane_equation"]
                fitted["resolved"] = {
                    **fitted["surfaces"],
                    **fitted["axes"],
                    **fitted["planes"],
                }
            return fitted

        def solver_plane_phase(reference_id: str) -> float | None:
            plane = initialized_plane(
                cast(PlaneDefinition, nodes[reference_id]), derived_result(reference_id)
            )
            if plane.axis is None:
                raise ValueError(
                    "independent reference planes do not have a shared-axis clocking phase"
                )
            axis = derived_result(plane.axis)
            if isinstance(self.workspace, ScanWorkspace):
                return plane.initial_angle_degrees
            if (
                not axis.get("direction_reversed")
                or plane.initial_angle_degrees is None
            ):
                return plane.initial_angle_degrees
            # Solver charts use positive Z; datum frames retain directed axes.
            from experiments.feature_reuse import transformed_plane_initial

            parameters = axis["parameters"]
            direction = np.asarray([parameters[2], parameters[3], 1.0])
            direction /= np.linalg.norm(direction)
            initializer = transformed_plane_initial(
                derived_result(reference_id),
                np.eye(3),
                np.zeros(3),
                {**axis, "axis_display": direction.tolist()},
            )
            return initializer["initial_angle_degrees"]

        def solve_connected_axis(
            axis_id: str, fitted_factors: list[SurfaceFit]
        ) -> dict[str, Any]:
            reject_overlaps(fitted_factors)
            sources = {
                selection_source(nodes[ref], nodes)
                for factor in fitted_factors
                for ref in factor.selections
            }
            if len(sources) != 1:
                raise ValueError("connected fits on a free axis must share one source")
            coaxial = [
                item
                for item in recipe.nodes
                if isinstance(item, CoaxialGroup) and item.axis == axis_id
            ]
            composed = next(
                (
                    entry
                    for entry in plane_relationship_components.values()
                    if axis_id in entry[2]
                ),
                None,
            )
            if (
                coaxial
                and composed is not None
                and coaxial_seed_component(axis_id, composed, nodes)
            ):
                relations, all_surfaces, _ = composed
                request = AxisSolve.model_construct(
                    id="@axis/" + axis_id,
                    label="Connected coaxial solve",
                    operation="axis_solve",
                    axis=axis_id,
                    factors=list(
                        dict.fromkeys(
                            [
                                *(item.id for item in coaxial),
                                *(surface.id for surface in all_surfaces),
                                *(relation.id for relation in relations),
                            ]
                        )
                    ),
                )
                return solve_selected_symmetry(request)
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
                    solver_plane_phase(reference_id),
                )
                for reference_id, group in referenced_plane_factors.items()
            )
            axis_result = derived_result(axis_id)
            if cast(AxisDefinition, nodes[axis_id]).initial_parameters is None:
                if reference_plane_groups and isinstance(self.workspace, ScanWorkspace):
                    request = AxisSolve.model_construct(
                        id="@axis/" + axis_id,
                        label="Fixed coaxial solve",
                        operation="axis_solve",
                        axis=axis_id,
                        factors=[
                            *(item.id for item in coaxial),
                            *(factor.id for factor in fitted_factors),
                        ],
                    )
                    return solve_selected_symmetry(request)
                return fit_fixed_axis_group(
                    self.workspace,
                    [selected(side) for side in sides],
                    [selected(plane) for plane in planes],
                    axis_result
                    if isinstance(self.workspace, ScanWorkspace)
                    else np.asarray(axis_result["parameters"], dtype=float),
                    semantic_intent={
                        "recipe": recovery_recipe(),
                        "support_policy_version": SUPPORT_POLICY_VERSION,
                    }
                    if isinstance(self.workspace, ScanWorkspace)
                    else None,
                )
            fitted = fit_group(
                self.workspace,
                [selected(side) for side in sides],
                [selected(plane) for plane in planes],
                axis_initial=axis_result
                if isinstance(self.workspace, ScanWorkspace)
                else np.asarray(axis_result["parameters"], dtype=float),
                reference_plane_groups=reference_plane_groups,
                semantic_intent={
                    "recipe": recovery_recipe(),
                    "support_policy_version": SUPPORT_POLICY_VERSION,
                }
                if isinstance(self.workspace, ScanWorkspace)
                else None,
            )
            if coaxial and isinstance(self.workspace, ScanWorkspace):
                direction = np.asarray(fitted["axis_display"])
                point = np.asarray(fitted["point_display"])
                point -= (point @ direction) * direction
                fitted["point_display"] = point.tolist()
                for plane in fitted.get("reference_planes", {}).values():
                    normal = np.asarray(plane["normal_display"])
                    offset = plane["plane_equation"][3] - normal @ point
                    plane.update(
                        point_display=(point + offset * normal).tolist(),
                        offset=float(offset),
                    )
            return fitted

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

        def solve_direction_component(
            relations: list[GeometryRelationship],
            surfaces: list[SurfaceFit],
            excluded: set[str],
            *,
            include_radii: bool = True,
            radius_groups: tuple[EqualRadii, ...] | None = None,
        ) -> dict[str, Any]:
            from experiments.fit_relationship_component import (
                DirectionAlignmentInput,
                DirectionOperand,
                PointInput,
            )
            from experiments.selected_direction_fit import (
                SelectedDirectionComponent,
                SelectedDirectionSurface,
                fit_selected_direction_component,
                private_direction_axis,
            )

            reject_overlaps(surfaces)
            endpoints = {
                relation.id: plane_relationship_endpoints(relation, nodes)
                for relation in relations
            }
            surface_ids = {surface.id for surface in surfaces}
            member_axes = {
                surface.id: axis_id
                for surface in surfaces
                if (axis_id := fit_axis(surface, nodes)) is not None
            }
            member_axes.update(
                {
                    surface.id: private_direction_axis(surface.id)
                    for surface in surfaces
                    if surface.kind in {"cylinder", "cone"}
                    and surface.id not in member_axes
                }
            )
            axis_ids = {
                key
                for surface in surfaces
                if (key := fit_axis(surface, nodes)) is not None
            }
            datum_ids = {
                endpoint.feature
                for group in endpoints.values()
                for endpoint in group
                if endpoint.is_binding
                and isinstance(nodes[endpoint.feature], PlaneDefinition)
            }
            datum_ids.update(
                surface.reference_plane
                for surface in surfaces
                if surface.reference_plane is not None
            )
            axis_ids.update(
                axis
                for key in datum_ids
                if (axis := cast(PlaneDefinition, nodes[key]).axis) is not None
            )
            axis_ids.update(
                endpoint.feature
                for group in endpoints.values()
                for endpoint in group
                if endpoint.is_binding
                and isinstance(nodes[endpoint.feature], AxisDefinition)
            )
            full_axes = {
                key
                for surface in surfaces
                if (key := fit_axis(surface, nodes)) is not None
            }
            full_plane_ids = {
                surface.reference_plane
                for surface in surfaces
                if surface.reference_plane is not None
                and cast(PlaneDefinition, nodes[surface.reference_plane]).construction
                != "independent"
            }
            full_plane_ids.update(
                endpoint.feature
                for relation in relations
                if isinstance(relation, PlaneRelationship)
                and relation.relation == "coincident"
                for endpoint in endpoints[relation.id]
                if endpoint.is_binding
            )
            legacy_datums = {
                endpoint.feature
                for relation in relations
                if isinstance(relation, PlaneRelationship)
                for endpoint in endpoints[relation.id]
                if endpoint.is_binding
                and isinstance(nodes[endpoint.feature], PlaneDefinition)
            }
            legacy_datums.update(
                surface.reference_plane
                for surface in surfaces
                if surface.reference_plane is not None
            )
            free_axes = full_axes | {
                endpoint.feature
                for group in endpoints.values()
                for endpoint in group
                if endpoint.is_binding
                and isinstance(nodes[endpoint.feature], AxisDefinition)
            }
            free_axes.update(
                axis
                for key in legacy_datums
                if (axis := cast(PlaneDefinition, nodes[key]).axis) is not None
            )
            axes = {
                key: AxisInput({}, True)
                for key in member_axes.values()
                if key not in nodes
            }
            for key in axis_ids:
                definition = cast(AxisDefinition, nodes[key])
                source = active_direction_datum_source(key, surface_ids, nodes)
                axes[key] = (
                    AxisInput(
                        {"direction_reversed": definition.direction_reversed},
                        False,
                        source=member_axes[source],
                    )
                    if source is not None
                    else AxisInput(
                        resolved_result(key, excluded),
                        free=definition.initial_parameters is not None
                        and key in free_axes,
                        position_free=key in full_axes
                        and definition.initial_parameters is not None,
                    )
                )

            def free_axis(key: str) -> bool:
                seen: set[str] = set()
                while axes[key].source is not None:
                    if key in seen:
                        raise ValueError("direction axis aliases contain a cycle")
                    seen.add(key)
                    key = cast(str, axes[key].source)
                return axes[key].free

            planes: dict[str, PlaneInput] = {}
            for key in datum_ids:
                definition = cast(PlaneDefinition, nodes[key])
                if definition.construction == "independent":
                    planes[key] = PlaneInput(
                        resolved_result(key, excluded),
                        free=True,
                        orientation_free=True,
                        offset_free=key in full_plane_ids,
                    )
                    continue
                assert definition.axis is not None
                eligible = normalize_endpoint(
                    OutputReference(feature=key, output="plane"),
                    nodes,
                    "relationship_direction",
                ).is_binding
                active_parent = (
                    active_direction_datum_source(definition.axis, surface_ids, nodes)
                    is not None
                )
                planes[key] = PlaneInput(
                    deepcopy(self._derived.get(key, {}))
                    if active_parent
                    else resolved_result(key, excluded),
                    free=eligible
                    or (key in legacy_datums and free_axis(definition.axis)),
                    axis=definition.axis,
                    construction=definition.construction,
                    offset_from_axis=definition.offset
                    if key not in full_plane_ids
                    else None,
                    offset_free=eligible
                    or (key in legacy_datums and free_axis(definition.axis)),
                )
            centers: dict[str, PointInput] = {}
            for surface in surfaces:
                key = surface.point
                if key is None:
                    continue
                source = active_direction_datum_source(key, surface_ids, nodes)
                centers[key] = (
                    PointInput({}, False, source="@direction-point:" + source)
                    if source is not None
                    else PointInput(
                        resolved_result(key, excluded), key in point_components
                    )
                )
            selected_surfaces: list[SelectedDirectionSurface] = []
            for surface in surfaces:
                axis_id = member_axes.get(surface.id)
                independent_normal = (
                    surface.reference_plane is not None
                    and cast(
                        PlaneDefinition, nodes[surface.reference_plane]
                    ).construction
                    == "independent"
                )
                selected_surfaces.append(
                    SelectedDirectionSurface(
                        selection=selected(surface),
                        axis=axis_id,
                        datum=None if independent_normal else surface.reference_plane,
                        point=surface.point,
                        construction=cast(
                            PlaneDefinition, nodes[surface.reference_plane]
                        ).construction
                        if surface.reference_plane
                        else "perpendicular_to_axis",
                    )
                )

            def operand(endpoint: RelationshipEndpoint) -> DirectionOperand:
                key = endpoint.owner
                if key is None:
                    value = input_result(endpoint.reference)
                    if "plane_equation" in value:
                        planes[endpoint.key] = PlaneInput(value, False)
                        return DirectionOperand("plane", endpoint.key)
                    axes[endpoint.key] = AxisInput(value, False)
                    return DirectionOperand("axis", endpoint.key)
                node = nodes[key]
                if isinstance(node, AxisDefinition):
                    return DirectionOperand("axis", key)
                if isinstance(node, SurfaceFit) and node.kind in {"cone", "cylinder"}:
                    return DirectionOperand("axis", member_axes[key])
                return DirectionOperand("plane", key)

            alignment_endpoints = [
                sorted(endpoints[relation.id], key=lambda endpoint: endpoint.key)
                for relation in relations
                if isinstance(relation, DirectionAlignment)
            ]
            directions = tuple(
                DirectionAlignmentInput(
                    operand(group[0]),
                    operand(endpoint),
                )
                for group in alignment_endpoints
                for endpoint in group[1:]
            )
            directions += tuple(
                DirectionAlignmentInput(
                    DirectionOperand("plane", surface.id),
                    DirectionOperand("plane", surface.reference_plane),
                )
                for surface in surfaces
                if surface.reference_plane is not None
                and cast(PlaneDefinition, nodes[surface.reference_plane]).construction
                == "independent"
            )
            plane_relations = tuple(
                (
                    relation.relation,
                    tuple(
                        endpoint.owner or endpoint.key
                        for endpoint in endpoints[relation.id]
                    ),
                )
                for relation in relations
                if isinstance(relation, PlaneRelationship)
            )
            for relation in relations:
                if not isinstance(relation, PlaneRelationship):
                    continue
                for endpoint in endpoints[relation.id]:
                    if not endpoint.is_binding:
                        planes[endpoint.key] = PlaneInput(
                            input_result(endpoint.reference), False
                        )
            applied_radii = (
                radius_groups
                if radius_groups is not None
                else tuple(
                    node
                    for node in recipe.nodes
                    if include_radii
                    and isinstance(node, EqualRadii)
                    and set(node.surfaces) <= surface_ids
                )
            )
            radii = tuple(tuple(node.surfaces) for node in applied_radii)
            component = SelectedDirectionComponent(
                surfaces=tuple(selected_surfaces),
                axes=axes,
                planes=planes,
                centers=centers,
                directions=directions,
                relationships=plane_relations,
                radii=radii,
                semantic_intent={
                    "recipe": recovery_recipe(),
                    "support_policy_version": SUPPORT_POLICY_VERSION,
                },
            )
            fitted = fit_selected_direction_component(
                self.workspace, component, cancelled=cancelled
            )
            resolved = {
                **fitted["surfaces"],
                **{
                    key: value
                    for key, value in fitted["axes"].items()
                    if key in nodes and axes[key].free
                },
                **{
                    key: value
                    for key, value in fitted.get("planes", {}).items()
                    if key in nodes and planes[key].free
                },
                **{
                    key: value
                    for key, value in fitted.get("points", {}).items()
                    if key in nodes
                },
            }
            for key, value in list(resolved.items()):
                definition = nodes[key]
                if (
                    isinstance(definition, PlaneDefinition)
                    and definition.construction == "independent"
                ):
                    equation = value["plane_equation"]
                    resolved[key] = {
                        **deepcopy(self._derived.get(key, {})),
                        **reference_plane_result(
                            None,
                            definition.model_copy(
                                update={
                                    "initial_normal": tuple(equation[:3]),
                                    "offset": equation[3],
                                }
                            ),
                        ),
                        **value,
                        "offset": float(equation[3]),
                    }
            # Source-fit datums are derived publications, never extra free
            # direction variables. Publish them from this exact solved source.
            for node in recipe.nodes:
                if isinstance(node, AxisDefinition) and node.source_fit in surface_ids:
                    source_surface = fitted["surfaces"][node.source_fit]
                    resolved[node.id] = (
                        directed_axis_result(node, geometry=source_surface)
                        if "analytic" in source_surface
                        else directed_axis_result(
                            node, parameters=list(source_surface["parameters"])
                        )
                    )
                elif (
                    isinstance(node, PointDefinition) and node.source_fit in surface_ids
                ):
                    resolved[node.id] = {
                        "point_display": list(
                            fitted["surfaces"][node.source_fit]["parameters"][:3]
                        ),
                        "source_fit": node.source_fit,
                    }
            for node in recipe.nodes:
                if (
                    isinstance(node, PlaneDefinition)
                    and node.axis in resolved
                    and node.id not in resolved
                ):
                    if node.placement is not None and node.id not in self._derived:
                        raise ValueError(
                            "a placed plane datum requires its exact initialized placement before direction solving"
                        )
                    resolved[node.id] = reference_plane_result(
                        resolved[node.axis],
                        initialized_plane(node, self._derived.get(node.id, {})),
                    )
            applied_radius_ids = {node.id for node in applied_radii}
            for node in recipe.nodes:
                if isinstance(node, EqualRadii) and node.id in applied_radius_ids:
                    first_surface = fitted["surfaces"][node.surfaces[0]]
                    resolved[node.id] = {
                        "format": "scansor-equal-radii-v2",
                        "measurement": "radius",
                        "value": first_surface["parameters"][3]
                        if first_surface["kind"] == "sphere"
                        else revolution_geometry(first_surface)["radius"],
                        "surfaces": {
                            key: fitted["surfaces"][key] for key in node.surfaces
                        },
                    }
                elif (
                    isinstance(node, CoaxialGroup)
                    and node.axis in axes
                    and set(node.surfaces) <= surface_ids
                ):
                    resolved[node.id] = {
                        "format": "scansor-coaxial-group-v1",
                        "axis": node.axis,
                        "surfaces": {
                            key: fitted["surfaces"][key] for key in node.surfaces
                        },
                        "resolved": {
                            node.axis: fitted["axes"][node.axis],
                            **{key: fitted["surfaces"][key] for key in node.surfaces},
                        },
                    }
            return {
                **fitted,
                "format": "scansor-direction-component-v1",
                "sense": "unsigned",
                "relationships": [relation.id for relation in relations],
                "resolved": resolved,
            }

        def solve_plane_component(
            relations: list[GeometryRelationship],
            surfaces: list[SurfaceFit],
            excluded: set[str],
            *,
            include_radii: bool = True,
            radius_groups: tuple[EqualRadii, ...] | None = None,
        ) -> dict[str, Any]:
            if selected_plane_component(relations, surfaces, nodes):
                return solve_direction_component(
                    relations,
                    surfaces,
                    excluded,
                    include_radii=include_radii,
                    radius_groups=radius_groups,
                )
            endpoints = {
                relation.id: plane_relationship_endpoints(relation, nodes)
                for relation in relations
            }
            datum_ids = {
                endpoint.owner
                for group in endpoints.values()
                for endpoint in group
                if endpoint.owner is not None
                and isinstance(nodes[endpoint.owner], PlaneDefinition)
            }
            has_exact_contexts = any(
                not endpoint.is_binding
                for group in endpoints.values()
                for endpoint in group
            )
            if (
                not datum_ids
                and not has_exact_contexts
                and not any(fit_axis(surface, nodes) for surface in surfaces)
            ):
                return fit_plane_relationships(
                    self.workspace,
                    cast(list[PlaneRelationship], relations),
                    surfaces,
                    [resolved_result(surface.id, excluded) for surface in surfaces],
                    [fitted_ids(surface) for surface in surfaces],
                )
            from experiments.fit_relationship_component import (
                AxisInput,
                PlaneInput,
                PointInput,
                SurfaceInput,
                fit_relationship_component,
            )

            axis_ids = dict.fromkeys(
                axis
                for surface in surfaces
                if (axis := fit_axis(surface, nodes)) is not None
            )
            for key in datum_ids:
                datum_axis = cast(PlaneDefinition, nodes[key]).axis
                if datum_axis is not None:
                    axis_ids[datum_axis] = None
            datum_aware = bool(datum_ids or has_exact_contexts)
            axes = {
                key: AxisInput(
                    resolved_result(key, excluded),
                    (cast(AxisDefinition, nodes[key]).initial_parameters is not None)
                    and (datum_aware or key in connected_components)
                    and key not in plan.explicit_axes,
                )
                for key in axis_ids
            }
            point_ids = dict.fromkeys(
                surface.point for surface in surfaces if surface.point is not None
            )
            centers = {
                key: PointInput(resolved_result(key, excluded), key in point_components)
                for key in point_ids
            }
            datum_records: dict[str, PlaneInput] = {}
            if datum_aware:
                # A plane-reference observation already activates the datum's
                # whole plane, including the historical free offset role.
                datum_ids.update(
                    surface.reference_plane
                    for surface in surfaces
                    if surface.reference_plane is not None
                )
                full_plane_ids = {
                    surface.reference_plane
                    for surface in surfaces
                    if surface.reference_plane is not None
                } | {
                    endpoint.owner
                    for relation in relations
                    if cast(PlaneRelationship, relation).relation == "coincident"
                    for endpoint in endpoints[relation.id]
                    if endpoint.owner is not None
                }
                for key in datum_ids:
                    definition = initialized_plane(
                        cast(PlaneDefinition, nodes[key]), derived_result(key)
                    )
                    assert definition.axis is not None
                    datum_records[key] = PlaneInput(
                        resolved_result(key, excluded),
                        axes[definition.axis].free,
                        axis=definition.axis,
                        construction=definition.construction,
                        # Parallel consumes orientation only. Unused placement
                        # remains the authored construction of the current axis.
                        offset_from_axis=definition.offset
                        if key not in full_plane_ids
                        else None,
                    )
            for group in endpoints.values():
                for endpoint in group:
                    if not endpoint.is_binding:
                        datum_records[endpoint.key] = PlaneInput(
                            input_result(endpoint.reference), False
                        )
            inputs: list[SurfaceInput] = []
            for surface in surfaces:
                ids = fitted_ids(surface)
                reference = (
                    initialized_plane(
                        cast(PlaneDefinition, nodes[surface.reference_plane]),
                        derived_result(surface.reference_plane),
                    )
                    if surface.reference_plane
                    else None
                )
                value = resolved_result(surface.id, excluded)
                if isinstance(self.workspace, ScanWorkspace) and surface.kind in {
                    "cylinder",
                    "cone",
                }:
                    value["support_intent"] = support_record(
                        effective_support(surface, excluded)
                    )
                axis_id = fit_axis(surface, nodes)
                point_id = surface.point
                if axis_id is None and surface.kind in {"cylinder", "cone"}:
                    axis_id = "@" + surface.id
                    geometry = revolution_geometry(value)
                    axes[axis_id] = AxisInput(
                        {
                            "parameters": value.get("parameters"),
                            "axis_display": geometry["axis"],
                            "point_display": geometry["origin"],
                        },
                        True,
                    )
                if point_id is None and surface.kind == "sphere":
                    point_id = "@" + surface.id
                    centers[point_id] = PointInput(
                        {"point_display": value["parameters"][:3]}, True
                    )
                inputs.append(
                    SurfaceInput(
                        surface.id,
                        surface.kind,
                        self.workspace.local[ids],
                        self.workspace.data.weights[ids],
                        value,
                        axis_id,
                        surface.reference_plane,
                        reference.construction
                        if reference
                        else "perpendicular_to_axis",
                        point_id,
                    )
                )
            surface_ids = {surface.id for surface in surfaces}
            # Publish source-fit datums even when no member fit uses them. Their
            # raw initialization remains an input; consumers get final geometry.
            for node in recipe.nodes:
                if isinstance(node, AxisDefinition) and node.source_fit in surface_ids:
                    _ = axes.setdefault(
                        node.id, AxisInput(derived_result(node.id), False)
                    )
                if isinstance(node, PointDefinition) and node.source_fit in surface_ids:
                    _ = centers.setdefault(
                        node.id, PointInput(derived_result(node.id), False)
                    )
            for key, axis in list(axes.items()):
                axis_node = nodes.get(key)
                if (
                    isinstance(axis_node, AxisDefinition)
                    and axis_node.source_fit in surface_ids
                ):
                    source = next(
                        item for item in inputs if item.id == axis_node.source_fit
                    )
                    if source.axis is None:
                        raise ValueError(
                            "coupled axis initializer must be a lateral fit"
                        )
                    if source.axis != key:
                        axes[key] = AxisInput(axis.result, False, source.axis)
            for key, center in list(centers.items()):
                point_node = nodes.get(key)
                if (
                    isinstance(point_node, PointDefinition)
                    and point_node.source_fit in surface_ids
                ):
                    source = next(
                        item for item in inputs if item.id == point_node.source_fit
                    )
                    if source.point is None:
                        raise ValueError(
                            "coupled point initializer must be a sphere fit"
                        )
                    centers[key] = PointInput(center.result, False, source.point)
            equal_radii = [
                node
                for node in recipe.nodes
                if include_radii
                and isinstance(node, EqualRadii)
                and set(node.surfaces) <= surface_ids
            ]
            if radius_groups is not None:
                equal_radii = list(radius_groups)
            fitted = fit_relationship_component(
                inputs,
                axes,
                [
                    (
                        cast(PlaneRelationship, relation).relation,
                        [
                            endpoint.owner or endpoint.key
                            for endpoint in endpoints[relation.id]
                        ],
                    )
                    for relation in relations
                ],
                [relation.surfaces for relation in equal_radii],
                centers,
                planes=datum_records,
            )
            for key, axis in fitted["axes"].items():
                node = nodes.get(key)
                if isinstance(node, AxisDefinition) and node.source_fit in surface_ids:
                    source_surface = fitted["surfaces"][node.source_fit]
                    if "analytic" in source_surface:
                        axis.update(directed_axis_result(node, geometry=source_surface))
                    else:
                        axis["parameters"] = deepcopy(source_surface["parameters"])
            resolved = {
                **fitted["surfaces"],
                **{
                    key: value
                    for key, value in fitted.get("planes", {}).items()
                    if key in nodes and datum_records[key].free
                },
                **{
                    key: value
                    for key, value in fitted["axes"].items()
                    if key in nodes and (axes[key].free or axes[key].source is not None)
                },
                **{
                    key: value
                    for key, value in fitted["points"].items()
                    if key in nodes
                    and (centers[key].free or centers[key].source is not None)
                },
            }
            for node in recipe.nodes:
                if not isinstance(node, PlaneDefinition) or node.axis not in axes:
                    continue
                if not axes[node.axis].free and axes[node.axis].source is None:
                    continue
                if node.id in fitted.get("planes", {}):
                    # The component already publishes this datum's exact roles
                    # and directed construction frame, including moved offsets.
                    continue
                initializer = initialized_plane(node, derived_result(node.id))
                axis = fitted["axes"][node.axis]
                datum = reference_plane_result(axis, initializer)
                equation = fitted["equations"].get(node.id)
                if equation is not None:
                    normal = np.asarray(equation[:3])
                    anchor = np.asarray(axis["point_display"])
                    direction = np.asarray(axis["axis_display"])
                    point = anchor + (equation[3] - normal @ anchor) * normal
                    if node.construction != "perpendicular_to_axis":
                        radial = np.cross(normal, direction)
                        radial /= np.linalg.norm(radial)
                        zero = reference_plane_result(
                            axis,
                            initializer.model_copy(
                                update={"initial_angle_degrees": 0.0}
                            ),
                        )
                        u = np.asarray(zero["basis_v_display"])
                        v = np.cross(direction, u)
                        datum.update(
                            basis_u_display=direction.tolist(),
                            basis_v_display=radial.tolist(),
                            radial_display=radial.tolist(),
                            angle_degrees=float(
                                np.degrees(np.arctan2(radial @ v, radial @ u))
                            ),
                        )
                    datum.update(
                        normal_display=normal.tolist(),
                        plane_equation=equation,
                        point_display=point.tolist(),
                        offset=float(equation[3] - normal @ anchor),
                        resolved_by="plane_relationship",
                    )
                resolved[node.id] = datum
            for relation in equal_radii:
                first = fitted["surfaces"][relation.surfaces[0]]
                radius = (
                    first["parameters"][3]
                    if first["kind"] == "sphere"
                    else revolution_geometry(first)["radius"]
                )
                resolved[relation.id] = {
                    **deepcopy(
                        self._derived.get(
                            relation.id,
                            {
                                "format": "scansor-equal-radii-v2",
                                "measurement": "radius",
                            },
                        )
                    ),
                    "value": radius,
                    "surfaces": {
                        key: fitted["surfaces"][key] for key in relation.surfaces
                    },
                }
            first_endpoint = endpoints[relations[0].id][0]
            first_plane = {**fitted["surfaces"], **fitted.get("planes", {})}[
                first_endpoint.owner or first_endpoint.key
            ]
            return {
                "format": "scansor-plane-relationships-v1",
                "relationships": [relation.id for relation in relations],
                "normal_display": first_plane["plane_equation"][:3],
                "surfaces": fitted["surfaces"],
                "resolved": resolved,
                "weighted_rms": fitted["weighted_rms"],
                "solver": fitted["solver"],
            }

        def invalidate_resolved_consumers(
            outputs: set[str], excluded: set[str], provider: str | None = None
        ) -> None:
            """Provider changes invalidate resolved reads, not raw initializers."""
            provider_ids = {provider}
            if provider in plan.solves and plan.solves[provider][0] == "planes":
                provider_ids.update(
                    relation.id
                    for relation in plane_relationship_components[
                        plan.solves[provider][1]
                    ][0]
                )
            affected = {
                task
                for task, refs in plan.resolved_reads.items()
                if refs.intersection(outputs)
                and (
                    not provider_ids.intersection(plan.read_exclusions.get(task, set()))
                )
            } - excluded
            while True:
                expanded = affected | {
                    task
                    for task, deps in plan.dependencies.items()
                    if deps.intersection(affected)
                }
                if expanded == affected:
                    break
                affected = expanded
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                for task in affected:
                    if task in nodes:
                        if self._states[task] == "ready":
                            self._states[task] = "stale"
                        _ = self._derived.pop(task, None)
                        _ = self._results.pop(task, None)
                        # A later independent provider can invalidate these
                        # same consumers. Keep failure provenance until their
                        # own task succeeds or their recipe inputs are edited.
                        if self._states[task] not in {"failed", "blocked"}:
                            _ = self._errors.pop(task, None)
                            _ = self._diagnostics.pop(task, None)
                    elif task in plan.solves:
                        kind, identifier = plan.solves[task]
                        if kind == "axis":
                            _ = self._connected_solves.pop(identifier, None)
                        elif kind == "point":
                            _ = self._connected_point_solves.pop(identifier, None)
                        elif kind == "planes":
                            _ = self._derived.pop(task, None)
                if affected:
                    self._revision += 1

        def resolve_component(task: str) -> None:
            kind, identifier = plan.solves[task]
            factors: list[SurfaceFit] = []
            relations: list[GeometryRelationship] = []
            planes: list[SurfaceFit] = []
            axis_result: dict[str, Any] | None = None
            resolved: dict[str, Any] = {}
            if kind == "axis":
                factors, members = connected_components[identifier]
                error_ids = members
            elif kind == "point":
                factors, members = point_components[identifier]
                error_ids = members
            else:
                relations, planes, members = plane_relationship_components[identifier]
                error_ids = {relation.id for relation in relations}
                if not relations:
                    error_ids.update(
                        key
                        for key in members
                        if isinstance(nodes[key], PlaneDefinition)
                        and cast(PlaneDefinition, nodes[key]).construction
                        == "independent"
                    )
                if selected_plane_component(relations, planes, nodes):
                    error_ids.update(
                        key
                        for key in members
                        if isinstance(nodes[key], (EqualRadii, CoaxialGroup))
                    )
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                if kind == "axis" and identifier in self._connected_solves:
                    return
                if kind == "point" and identifier in self._connected_point_solves:
                    return
                if kind == "planes" and (
                    task in self._derived
                    if not relations
                    else all(self._states[key] == "ready" for key in error_ids)
                ):
                    return
            try:
                if kind == "axis":
                    axis_result = solve_connected_axis(identifier, factors)
                elif kind == "point":
                    resolved = solve_connected_point(identifier, factors)
                else:
                    resolved = solve_plane_component(
                        relations, planes, error_ids | {task}
                    )
            except Exception as error:
                with self.lock:
                    if epoch == self._epoch:
                        diagnostic = failure_diagnostic(error)
                        for member in error_ids:
                            self._states[member] = "failed"
                            self._errors[member] = str(error)
                            if diagnostic is not None:
                                self._diagnostics[member] = deepcopy(diagnostic)
                            else:
                                _ = self._diagnostics.pop(member, None)
                        if isinstance(error, SelectionOverlap):
                            self._diagnostics[identifier] = error.diagnostic
                        elif diagnostic is None:
                            _ = self._diagnostics.pop(identifier, None)
                        self._revision += 1
                raise
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during connected solve; result discarded"
                    )
                invalidate_resolved_consumers(plan.aliases[task], {task}, task)
                if kind == "axis":
                    assert axis_result is not None
                    self._connected_solves[identifier] = axis_result
                    if identifier in influence.axis_bindings.values():
                        for member, value in axis_result["surfaces"].items():
                            if member not in self._derived:
                                self._derived[member] = deepcopy(value)
                            self._states[member] = "ready"
                            _ = self._errors.pop(member, None)
                            _ = self._diagnostics.pop(member, None)
                elif kind == "point":
                    self._connected_point_solves[identifier] = resolved
                else:
                    if not relations:
                        self._derived[task] = deepcopy(resolved)
                    for member in error_ids:
                        if isinstance(nodes[member], PlaneDefinition):
                            continue
                        self._derived[member] = deepcopy(
                            resolved
                            if isinstance(
                                nodes[member], (PlaneRelationship, DirectionAlignment)
                            )
                            else resolved["resolved"][member]
                        )
                    if selected_plane_component(relations, planes, nodes):
                        for member, value in resolved["resolved"].items():
                            if member in nodes and isinstance(
                                nodes[member], SurfaceFit
                            ):
                                self._derived[member] = deepcopy(value)
                                self._states[member] = "ready"
                                _ = self._errors.pop(member, None)
                                _ = self._diagnostics.pop(member, None)
                for member in error_ids:
                    self._states[member] = "ready"
                    _ = self._errors.pop(member, None)
                    _ = self._diagnostics.pop(member, None)
                self._revision += 1

        with self.lock:
            for axis in plan.explicit_axes:
                if axis in self._connected_solves:
                    outputs = set(influence.providers["@axis/" + axis].aliases)
                    invalidate_resolved_consumers(outputs, set())
                    _ = self._connected_solves.pop(axis, None)
                    self._revision += 1
        prior_surfaces: dict[str, dict[str, dict[str, Any]]] = {}
        # Native cells are shared only within this evaluation epoch. Multiple
        # reviewed patches on one carrier must not repeat the same split.
        arrangements: dict[str, Any] = {}
        failures: dict[str, set[str]] = {}
        evaluation_errors: list[Exception] = []
        for task in order:
            waiting = {
                key
                for key in plan.aliases.get(task, set())
                | ({task} if task in nodes else set())
                if self._states.get(key) == "needs_input"
            }
            if waiting:
                failures[task] = waiting

        def note_failure(task: str, error: Exception, causes: set[str]) -> None:
            """Keep independent branches running; discard dependent geometry."""
            if isinstance(error, StaleGraph):
                raise error
            evaluation_errors.append(error)
            failures[task] = set(causes)
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during evaluation")
                for cause in causes:
                    if self._states.get(cause) == "failed":
                        failures.setdefault(cause, set()).update(causes)
                        _ = self._derived.pop(cause, None)
                        _ = self._results.pop(cause, None)
                        _ = self._connected_solves.pop(cause, None)
                        _ = self._connected_point_solves.pop(cause, None)
                changed = True
                while changed:
                    changed = False
                    for dependent, prerequisites in plan.dependencies.items():
                        blocked_by = set().union(
                            *(failures[ref] for ref in prerequisites if ref in failures)
                        )
                        if not blocked_by or blocked_by <= failures.get(
                            dependent, set()
                        ):
                            continue
                        failures.setdefault(dependent, set()).update(blocked_by)
                        changed = True
                        names = [
                            node.label for node in recipe.nodes if node.id in blocked_by
                        ]
                        message = "Blocked by " + ", ".join(names or [str(error)])
                        affected = plan.aliases.get(dependent, set()) | (
                            {dependent} if dependent in nodes else set()
                        )
                        for affected_id in affected:
                            # A real failure takes precedence over a downstream
                            # blocked state, including aliases of failed solves.
                            if (
                                affected_id in blocked_by
                                and self._states[affected_id] == "failed"
                            ):
                                continue
                            self._states[affected_id] = "blocked"
                            self._errors[affected_id] = message
                            self._diagnostics[affected_id] = {
                                "blocked_by": [
                                    node.id
                                    for node in recipe.nodes
                                    if node.id in blocked_by
                                ]
                            }
                            _ = self._derived.pop(affected_id, None)
                            _ = self._results.pop(affected_id, None)
                            _ = self._connected_solves.pop(affected_id, None)
                            _ = self._connected_point_solves.pop(affected_id, None)
                self._revision += 1

        def evaluate_surface_fit(node: SurfaceFit) -> dict[str, Any] | None:
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
            elif node.axis is None and isinstance(self.workspace, ScanWorkspace):
                run_fit = self._prepare_numerical_fit(
                    node,
                    ids,
                    intent=recovery_recipe,
                    reuse_inputs=[
                        derived_result(ref)
                        for ref in node.selections
                        if isinstance(nodes[ref], ReuseSelection)
                    ],
                    support=lambda fit=node: effective_support(
                        fit, plan.read_exclusions.get(fit.id)
                    ),
                    epoch=epoch,
                    stop_fits=stop_fits,
                    cancelled=cancelled,
                )
                if self.fit_executor is not None:
                    pending_fits[node.id] = self.fit_executor.submit(run_fit)
                    return None
                derived = run_fit()
            elif node.axis is None:
                fit_points = self.workspace.local[ids]
                reuse_inputs = [
                    derived_result(ref)
                    for ref in node.selections
                    if isinstance(nodes[ref], ReuseSelection)
                ]
                initial = (
                    None
                    if node.kind in ("plane", "sphere")
                    else np.array(
                        example_workspace(self.workspace).data.selection[
                            "initial_parameters"
                        ]
                    )
                )
                domain = node.axial_domain
                if domain is None and node.kind in ("cylinder", "cone"):
                    domain = example_workspace(self.workspace).support
                if reuse_inputs:
                    if len(reuse_inputs) != len(node.selections):
                        raise ValueError(
                            "a fit cannot mix reused and ordinary selections"
                        )
                    initials = [value.get("fit_initial") for value in reuse_inputs]
                    domains = [
                        tuple(value["target_axial_domain"])
                        if value["target_axial_domain"] is not None
                        else None
                        for value in reuse_inputs
                    ]
                    if any(value != initials[0] for value in initials[1:]) or any(
                        value != domains[0] for value in domains[1:]
                    ):
                        raise ValueError(
                            "reused fit selections must share one transformed source fit"
                        )
                    if initials[0] is not None and node.kind != "plane":
                        initial = np.asarray(initials[0], dtype=float)
                    domain = domains[0]
                    if (
                        initials[0] is not None
                        and initial is not None
                        and domain is not None
                    ):
                        point = np.asarray([initial[0], initial[1], 0.0])
                        direction = np.asarray([initial[2], initial[3], 1.0])
                        # The residual evaluator's support is distance
                        # along a unit axis, not the local-Z chart.
                        direction /= np.linalg.norm(direction)
                        axial = (fit_points - point) @ direction
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
                    axis_result
                    if isinstance(self.workspace, ScanWorkspace)
                    else np.asarray(axis_result["parameters"], dtype=float),
                    semantic_intent={
                        "recipe": recovery_recipe(),
                        "support_policy_version": SUPPORT_POLICY_VERSION,
                    }
                    if isinstance(self.workspace, ScanWorkspace)
                    else None,
                )
                fixed_surfaces = fixed.get("surfaces")
                if fixed_surfaces is None:
                    raise AssertionError("fixed-axis fit omitted its surface")
                derived = dict(fixed_surfaces[node.id])
                derived["condition"] = fixed["fit"]["normal_matrix_condition"]
                if node.kind == "plane" and "plane_equation" not in derived:
                    parameters = cast(list[float], derived["parameters"])
                    derived["plane_equation"] = [
                        *fixed["axis_display"],
                        parameters[5],
                    ]
            return derived

        if not retry_failed:
            # Preserve failed/blocked results until an explicit retry or edit;
            # still execute independent stale branches in the requested plan.
            for key in order:
                if key in nodes and self._states[key] == "failed":
                    note_failure(
                        key,
                        ValueError(self._errors.get(key, "evaluation failed")),
                        {key},
                    )
            # Synthetic providers do not depend on their own published aliases.
            # Their failure identities must therefore be seeded explicitly.
            for task in order:
                failed_outputs = (
                    plan.aliases.get(task, set())
                    if task in plan.solves
                    else {
                        consumer.id
                        for consumer in recipe.nodes
                        if isinstance(consumer, ReuseSelection)
                        and task in plan.dependencies[consumer.id]
                    }
                    if task in plan.prior_solves
                    else set()
                ) & failures.keys()
                if failed_outputs:
                    causes = set().union(*(failures[key] for key in failed_outputs))
                    note_failure(
                        task,
                        ValueError(
                            self._errors.get(next(iter(causes)), "evaluation failed")
                        ),
                        causes,
                    )

        for key in order:
            check_cancelled(cancelled)
            # Keep selection queries and graph mutations on the coordinator.
            # A fit can run while later independent memberships are resolved.
            # Four pending jobs bound retained selected-array copies regardless
            # of the externally owned executor's worker/queue configuration.
            self._drain_numerical_fits(
                pending_fits,
                epoch,
                cancelled,
                failures,
                note_failure,
                plan.dependencies[key],
            )
            if key in failures:
                continue
            if isinstance(nodes.get(key), Body):
                # Face owners are reviewed after all generated children. Bodies
                # must not observe provisional faces before that final review.
                continue
            if key in plan.prior_solves:
                stage = plan.prior_solves[key]
                try:
                    ids = [fitted_ids(surface) for surface in stage.surfaces]
                    if stage.standalone:
                        if not isinstance(self.workspace, ScanWorkspace):
                            raise ValueError(
                                "Standalone pattern source stages require an imported scan"
                            )
                        prior = {
                            "surfaces": {
                                surface.id: self._prepare_numerical_fit(
                                    surface,
                                    surface_ids,
                                    intent=recovery_recipe,
                                    reuse_inputs=[
                                        derived_result(ref)
                                        for ref in surface.selections
                                        if isinstance(nodes[ref], ReuseSelection)
                                    ],
                                    support=partial(
                                        effective_support, surface, stage.excluded
                                    ),
                                    epoch=epoch,
                                    stop_fits=stop_fits,
                                    cancelled=cancelled,
                                )()
                                for surface, surface_ids in zip(
                                    stage.surfaces, ids, strict=True
                                )
                            }
                        }
                    elif stage.radii is not None:
                        inputs = [
                            resolved_result(surface.id, stage.excluded)
                            for surface in stage.surfaces
                        ]
                        prior = fit_equal_radii(
                            self.workspace, stage.surfaces, inputs, ids
                        )
                    else:
                        prior = solve_plane_component(
                            stage.planes,
                            stage.surfaces,
                            stage.excluded,
                            include_radii=False,
                            radius_groups=stage.radius_groups,
                        )
                except Exception as error:
                    if isinstance(error, StaleGraph):
                        raise
                    failed_consumers: set[str] = set()
                    with self.lock:
                        if epoch == self._epoch:
                            for consumer in recipe.nodes:
                                if (
                                    isinstance(consumer, ReuseSelection)
                                    and key in plan.dependencies[consumer.id]
                                ):
                                    self._states[consumer.id] = "failed"
                                    self._errors[consumer.id] = (
                                        f"prior source geometry: {error}"
                                    )
                                    diagnostic = failure_diagnostic(error)
                                    if diagnostic is not None:
                                        self._diagnostics[consumer.id] = diagnostic
                                    else:
                                        _ = self._diagnostics.pop(consumer.id, None)
                                    failed_consumers.add(consumer.id)
                            if failed_consumers:
                                self._revision += 1
                    note_failure(key, error, failed_consumers)
                    continue
                prior_surfaces.setdefault(stage.reuse, {}).update(prior["surfaces"])
                prior_surfaces[stage.reuse].update(prior.get("resolved", {}))
                prior_surfaces[stage.reuse].update(prior.get("axes", {}))
                prior_surfaces[stage.reuse].update(prior.get("planes", {}))
                continue
            if key in plan.solves:
                try:
                    resolve_component(key)
                except Exception as error:
                    kind, identifier = plan.solves[key]
                    causes = (
                        connected_components[identifier][1]
                        if kind == "axis"
                        else point_components[identifier][1]
                        if kind == "point"
                        else {
                            relation.id
                            for relation in plane_relationship_components[identifier][0]
                        }
                    )
                    note_failure(key, error, causes)
                continue
            node = nodes[key]
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during evaluation; result discarded"
                    )
                if self._states[key] == "ready":
                    continue
                self._states[key] = "running"
                self._revision += 1
            try:
                result = None
                derived: dict[str, Any] | None = None
                if isinstance(node, Selection) and node.entries is not None:
                    ids, coverage = resolve_membership(
                        recipe,
                        node,
                        self.workspace,
                        self._volume_cache,
                        cancelled=cancelled,
                    )
                    if ids is None:
                        raise ValueError("selection requires input before evaluation")
                    derived = {"ids": ids, "source": node.source}
                    if coverage is not None:
                        derived["coverage"] = coverage
                elif isinstance(node, SurfaceIntersection):
                    derived = surface_intersection(
                        referenced_surface(node.first), referenced_surface(node.second)
                    )
                    derived.update(
                        first=node.first.model_dump(), second=node.second.model_dump()
                    )
                elif isinstance(node, ArrangedFace):
                    from experiments.face_arrangement import prepare_faces
                    from experiments.face_proposals import reference_key

                    surface = referenced_surface(node.surface)
                    cutters: list[dict[str, Any]] = [
                        {
                            "key": reference_key(ref.model_dump()),
                            "geometry": referenced_surface(ref),
                            **(
                                {
                                    "face_domains": [
                                        derived_result(source_id)
                                        for source_id in node.finite_boundary_sources
                                        if cast(
                                            TrimmedFace | ArrangedFace, nodes[source_id]
                                        ).surface
                                        == ref
                                    ]
                                }
                                if any(
                                    cast(
                                        TrimmedFace | ArrangedFace, nodes[source_id]
                                    ).surface
                                    == ref
                                    for source_id in node.finite_boundary_sources
                                )
                                else {}
                            ),
                        }
                        for ref in node.cutters
                    ]
                    domains = [
                        derived_result(face_id) for face_id in node.domains
                    ] or None
                    intent_key = json.dumps(
                        [node.surface.model_dump(), surface, cutters, domains],
                        sort_keys=True,
                        allow_nan=False,
                    )
                    if intent_key not in arrangements:
                        arrangements[intent_key] = prepare_faces(
                            surface,
                            cutters,
                            domains=domains,
                            observations=self.workspace.local[surface.get("ids", [])],
                            coverage=np.concatenate(
                                [
                                    self.workspace.local[surface.get("ids", [])],
                                    *(
                                        self.workspace.local[
                                            cutter["geometry"].get("ids", [])
                                        ]
                                        for cutter in cutters
                                    ),
                                ]
                            ),
                        )
                    derived = {
                        **arrangements[intent_key].select(node.selector.model_dump()),
                        "surface": node.surface.model_dump(),
                    }
                elif isinstance(node, TrimmedFace):
                    surface = referenced_surface(node.surface)
                    derived = trimmed_face(
                        surface,
                        [
                            (
                                boundary.model_dump(),
                                derived_result(boundary.intersection),
                            )
                            for boundary in node.boundaries
                        ],
                        self.workspace.local[surface.get("ids", [])],
                    )
                    derived["surface"] = node.surface.model_dump()
                elif isinstance(node, BuildFaces):
                    derived = {
                        "kind": "build_faces",
                        "target": node.target.model_dump()
                        if node.target is not None
                        else None,
                        "boundary_sources": node.boundary_sources,
                        "surface_references": [
                            ref.model_dump() for ref in node.surfaces
                        ],
                        "generated_faces": [
                            child.id
                            for child in recipe.nodes
                            if child.managed_by == node.id
                            and isinstance(child, (TrimmedFace, ArrangedFace))
                        ],
                        "generated_intersections": [
                            child.id
                            for child in recipe.nodes
                            if child.managed_by == node.id
                            and isinstance(child, SurfaceIntersection)
                        ],
                        "reused_faces": node.reused_faces,
                        "reused_intersections": node.reused_intersections,
                        "adjacencies": [
                            choice.model_dump() for choice in node.adjacencies
                        ],
                        "face_scopes": [
                            scope.model_dump() for scope in node.face_scopes
                        ],
                    }
                elif isinstance(node, SurfaceFit):
                    derived = evaluate_surface_fit(node)
                    if derived is None:
                        continue
                elif isinstance(node, AxisDefinition):
                    if node.placement is not None:
                        placement = node.placement
                        match = derived_result(placement.reuse)["matches"][
                            placement.target_selection
                        ]
                        source_axis = resolved_result(
                            placement.source, plan.read_exclusions.get(node.id)
                        )
                        rotation = np.asarray(match["rotation"], dtype=float)
                        translation = np.asarray(match["translation"], dtype=float)
                        if isinstance(self.workspace, ScanWorkspace):
                            placed = transformed_axis_geometry(
                                source_axis, rotation, translation
                            )
                            derived = directed_axis_result(
                                node, geometry=placed, geometry_is_directed=True
                            )
                        else:
                            from experiments.feature_reuse import (
                                transformed_axis_initial,
                            )

                            parameters, reversed_direction = transformed_axis_initial(
                                source_axis, rotation, translation
                            )
                            derived = directed_axis_result(
                                node.model_copy(
                                    update={"direction_reversed": reversed_direction}
                                ),
                                parameters=[*parameters, 0.0, 0.0, 0.0],
                            )
                    elif node.source_fit is not None:
                        source = derived_result(node.source_fit)
                        derived = (
                            directed_axis_result(node, geometry=source)
                            if "analytic" in source
                            else directed_axis_result(
                                node, parameters=list(source["parameters"])
                            )
                        )
                    elif node.source_points is not None:
                        derived = directed_axis_result(
                            node,
                            first_point=input_result(node.source_points[0]),
                            second_point=input_result(node.source_points[1]),
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
                            input_reference_key(reference): input_result(reference)
                            for distance in node.distances
                            for reference in (
                                distance.first_point,
                                distance.second_point,
                            )
                        },
                    )
                elif isinstance(node, FrameDefinition):
                    derived = frame_result(
                        node,
                        {
                            input_reference_key(reference): input_result(reference)
                            for reference in (
                                node.origin_point,
                                node.primary_reference,
                                node.secondary_reference,
                            )
                        },
                    )
                elif isinstance(node, TransformDefinition):
                    derived = output_transform_result(
                        node,
                        resolved_result(node.frame),
                        resolved_result(node.scale),
                    )
                elif isinstance(node, PlaneDefinition):
                    axis_result = (
                        derived_result(node.axis) if node.axis is not None else None
                    )
                    initializer = {}
                    if node.placement is not None:
                        from experiments.feature_reuse import transformed_plane_initial

                        placement = node.placement
                        match = derived_result(placement.reuse)["matches"][
                            placement.target_selection
                        ]
                        initializer = transformed_plane_initial(
                            resolved_result(
                                placement.source, plan.read_exclusions.get(node.id)
                            ),
                            np.asarray(match["rotation"], dtype=float),
                            np.asarray(match["translation"], dtype=float),
                            axis_result,
                            refine_axis=cast(
                                AxisDefinition, nodes[node.axis]
                            ).source_fit
                            is not None
                            if node.axis is not None
                            else False,
                        )
                    derived = reference_plane_result(
                        axis_result, node.model_copy(update=initializer)
                    )
                    if initializer:
                        derived["reuse_initializer"] = initializer
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
                elif isinstance(node, SurfaceSelection):
                    from experiments.seed_surface_authoring import (
                        surface_selection_result,
                    )

                    def selection_cancelled() -> bool:
                        if cancelled is not None and cancelled():
                            return True
                        with self.lock:
                            return epoch != self._epoch

                    surface_membership = surface_selection_result(
                        self.workspace,
                        membership(node.seed_selection),
                        sorted(
                            {
                                vertex
                                for ref in node.barriers
                                for vertex in membership(ref)
                            }
                        ),
                        angle_degrees=node.angle_degrees,
                        setback=node.setback,
                        policy=node.policy,
                        neighborhood_distance=node.neighborhood_distance,
                        cancelled=selection_cancelled,
                    )
                    summary = surface_membership.preview()
                    derived = {
                        "ids": surface_membership.ids.tolist(),
                        "source": selection_source(node, nodes),
                        "policy": node.policy,
                        "counts": summary["counts"],
                        "diagnostics": summary["diagnostics"],
                    }
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
                    if node.circular is not None:
                        matches = circular_placements(
                            deepcopy(prior_surfaces[node.id][node.circular.axis])
                            if node.circular.axis in prior_surfaces.get(node.id, {})
                            else resolved_result(
                                node.circular.axis, plan.read_exclusions.get(node.id)
                            ),
                            node.circular.count,
                        )
                    else:
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
                        **(
                            {
                                "outputs": {
                                    key: deepcopy(prior_surfaces[node.id][key])
                                    if key in prior_surfaces.get(node.id, {})
                                    else resolved_result(
                                        key, plan.read_exclusions.get(node.id)
                                    )
                                    for key in node.fits
                                }
                            }
                            if node.id in plan.reuse_source_contexts
                            else {}
                        ),
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
                    fit_result = prior_surfaces.get(node.reuse, {}).get(node.fit)
                    if fit_result is None:
                        fit_result = resolved_result(
                            node.fit, plan.read_exclusions.get(node.id, set())
                        )
                    else:
                        fit_result = deepcopy(fit_result)
                    fit_result.setdefault("kind", fitted.kind)
                    fit_result.setdefault("axial_domain", fitted.axial_domain)
                    fit_geometry = None
                    if isinstance(self.workspace, ScanWorkspace) and fitted.kind in {
                        "cylinder",
                        "cone",
                    }:
                        fit_geometry = transformed_surface_geometry(
                            fit_result, rotation, translation
                        )
                        fit_initial, target_domain = None, fit_geometry["axial_domain"]
                    else:
                        fit_initial, target_domain = transformed_fit_seed(
                            fit_result, rotation, translation
                        )
                    volume_output: dict[str, Any]
                    if reuse.circular is not None:
                        ids, volume_output = self._circular_selection(
                            recipe,
                            reuse,
                            node,
                            rotation,
                            translation,
                            source_ids,
                            cancelled,
                        )
                    else:
                        source_points = self.workspace.local[source_ids]
                        source_origin, source_frame = surface_region_frame(
                            source_points, fit_result
                        )
                        region = build_selection_region(
                            source_points,
                            self.workspace.data.normals[source_ids]
                            @ self.workspace.frame,
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
                        volume_output = {
                            "region": region,
                            "target_origin": target_origin.tolist(),
                            "target_rotation": target_frame.tolist(),
                        }
                    derived = {
                        "ids": ids,
                        "reuse": node.reuse,
                        "fit": node.fit,
                        "source_selection": node.source_selection,
                        "target_selection": node.target_selection,
                        "vertex_count": len(ids),
                        **volume_output,
                        "fit_initial": fit_initial,
                        "target_axial_domain": target_domain,
                    }
                    if fit_geometry is not None:
                        derived["fit_geometry"] = fit_geometry
                        derived["support_intent"] = support_record(
                            transform_support(
                                effective_support(
                                    fitted, plan.read_exclusions.get(node.id)
                                ),
                                rotation,
                                translation,
                            )
                        )
                    excluded = plan.read_exclusions.get(node.id, set()) - {node.id}
                    if excluded:
                        derived["source_geometry_stage"] = {
                            "phase": "before_transferred_fit_relationships",
                            "excluded_relationships": sorted(excluded),
                            "prior_relationships": sorted(
                                {
                                    relation.id
                                    for stage in plan.prior_solves.values()
                                    if stage.reuse == node.reuse
                                    for relation in [
                                        *stage.planes,
                                        *stage.radius_groups,
                                        *(
                                            [stage.radii]
                                            if stage.radii is not None
                                            else []
                                        ),
                                    ]
                                }
                            ),
                        }
                elif isinstance(node, CoaxialGroup):
                    solve = self._connected_solves.get(node.axis)
                    if solve is not None:
                        axis = connected_axis_result(
                            cast(AxisDefinition, nodes[node.axis]),
                            derived_result(node.axis),
                            solve,
                        )
                        derived = {
                            **deepcopy(solve),
                            "format": "scansor-coaxial-group-v1",
                            "axis": node.axis,
                            "resolved": {
                                node.axis: axis,
                                **deepcopy(solve["surfaces"]),
                                **deepcopy(solve.get("reference_planes", {})),
                            },
                        }
                elif isinstance(node, EqualRadii):
                    surfaces = [cast(SurfaceFit, nodes[ref]) for ref in node.surfaces]
                    surface_ids = [fitted_ids(surface) for surface in surfaces]
                    derived = fit_equal_radii(
                        self.workspace,
                        surfaces,
                        [
                            resolved_result(surface.id, {node.id})
                            for surface in surfaces
                        ],
                        surface_ids,
                    )
                elif isinstance(node, (PlaneRelationship, DirectionAlignment)):
                    # Its virtual solve publishes every relationship alias
                    # together, before any alias/consumer task can execute.
                    raise AssertionError(
                        "connected plane solve did not publish its relationship"
                    )
                elif (
                    (
                        isinstance(self.workspace, ScanWorkspace)
                        or (
                            isinstance(node, JointFit)
                            and named_direction_component(node, nodes) is not None
                        )
                        or (
                            isinstance(node, AxisSolve)
                            and any(
                                isinstance(nodes[key], CoaxialGroup)
                                for key in node.factors
                            )
                        )
                    )
                    and isinstance(node, (AxisSolve, JointFit))
                    and has_selected_symmetry(node, nodes)
                ):
                    result = solve_selected_symmetry(node)
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
                            solver_plane_phase(reference_id),
                        )
                        for reference_id, group in referenced_plane_factors.items()
                    )
                    axis_result = derived_result(node.axis)
                    result = fit_group(
                        self.workspace,
                        [selected(side) for side in sides],
                        [selected(plane) for plane in planes],
                        axis_initial=axis_result
                        if isinstance(self.workspace, ScanWorkspace)
                        else np.asarray(axis_result["parameters"], dtype=float),
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
                                    solver_plane_phase(mirror.plane),
                                )
                            )
                            for mirror in mirrors
                        ),
                        reference_plane_groups=reference_plane_groups,
                        semantic_intent={
                            "recipe": recovery_recipe(),
                            "support_policy_version": SUPPORT_POLICY_VERSION,
                        }
                        if isinstance(self.workspace, ScanWorkspace)
                        else None,
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
                    if (
                        isinstance(self.workspace, ScanWorkspace)
                        or len(sides) > 1
                        or len(planes) > 1
                        or rotations
                    ):
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
                            semantic_intent={
                                "recipe": recovery_recipe(),
                                "support_policy_version": SUPPORT_POLICY_VERSION,
                            }
                            if isinstance(self.workspace, ScanWorkspace)
                            else None,
                        )
                    else:
                        lateral, plane = sides[0], planes[0]
                        left, right = selected(lateral), selected(plane)
                        session = NozzleSession(
                            source_sha256=example_workspace(
                                self.workspace
                            ).default.source_sha256,
                            model_sha256=example_workspace(self.workspace).model_sha256,
                            lateral_ids=left.ids,
                            plane_ids=right.ids,
                        )
                        result = example_workspace(self.workspace).fit(
                            session, lateral.kind, lateral.axial_domain
                        )
                        result["surfaces"] = {
                            lateral.id: {
                                "kind": lateral.kind,
                                "ids": left.ids,
                                "parameters": result["fit"]["parameters"],
                                "axial_domain": lateral.axial_domain
                                if lateral.axial_domain is not None
                                else result["axial_domain"],
                                "residuals": result["lateral_residuals"],
                                "weighted_rms": result["fit"]["cone_weighted_rms"],
                            },
                            plane.id: {
                                "kind": "plane",
                                "ids": right.ids,
                                "parameters": result["fit"]["parameters"],
                                "axial_domain": plane.axial_domain
                                if plane.axial_domain is not None
                                else result["axial_domain"],
                                "residuals": result["plane_residuals"],
                                "weighted_rms": result["fit"]["plane_weighted_rms"],
                            },
                        }
                if (
                    isinstance(node, (TrimmedFace, ArrangedFace))
                    and derived is not None
                ):
                    face_derived = derived
                    face_derived["boundary_sources"] = node.boundary_sources
                    if node.boundary_sources:
                        face_derived["shared_boundary_review"] = (
                            self._shared_boundary_review(
                                key,
                                epoch,
                                SharedBoundaryInputs(
                                    [key],
                                    [face_derived],
                                    node.surface.model_dump(),
                                    boundary_source_records(node.boundary_sources),
                                    referenced_surface(node.surface),
                                ),
                                False,
                            )
                        )
            except Exception as error:
                self._record_evaluation_failure(key, error, epoch, note_failure)
                continue
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
                    if isinstance(node, EqualRadii):
                        invalidate_resolved_consumers(
                            plan.aliases[node.id], {node.id}, node.id
                        )
                    self._derived[key] = derived
                self._revision += 1
        self._drain_numerical_fits(
            pending_fits, epoch, cancelled, failures, note_failure
        )
        # Owners precede their generated children in the DAG. Check their
        # aggregate coverage only after evaluation, without adding a cycle.
        for owner in recipe.nodes:
            if (
                not isinstance(owner, BuildFaces)
                or not owner.boundary_sources
                or owner.id not in order
            ):
                continue
            if owner.target is None:
                continue
            faces = list(
                dict.fromkeys(
                    [
                        *owner.reused_faces,
                        *(
                            child.id
                            for child in recipe.nodes
                            if child.managed_by == owner.id
                            and isinstance(child, (TrimmedFace, ArrangedFace))
                        ),
                    ]
                )
            )
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during shared-boundary review")
                if self._states.get(owner.id) != "ready":
                    continue
                pending = [
                    face_id for face_id in faces if self._states.get(face_id) != "ready"
                ]
                if pending:
                    review = {
                        "complete": False,
                        "pending_faces": pending,
                    }
                    if self._derived[owner.id].get("shared_boundary_review") != review:
                        self._derived[owner.id]["shared_boundary_review"] = review
                        self._revision += 1
                    continue
                # Physical faces have no solver overlays. Copy only the owner
                # inputs, not every face preview/result in the entire graph.
                face_records = [deepcopy(self._derived[face_id]) for face_id in faces]
            try:
                review = self._shared_boundary_review(
                    owner.id,
                    epoch,
                    SharedBoundaryInputs(
                        faces,
                        face_records,
                        owner.target.model_dump(),
                        boundary_source_records(owner.boundary_sources),
                        referenced_surface(owner.target),
                    ),
                    True,
                )
            except Exception as error:
                if isinstance(error, StaleGraph):
                    raise
                with self.lock:
                    if epoch != self._epoch:
                        raise StaleGraph(
                            "graph changed during shared-boundary review"
                        ) from error
                    failed_ids = {
                        owner.id,
                        *(
                            face_id
                            for face_id in faces
                            if nodes[face_id].managed_by == owner.id
                        ),
                    }
                    for failed_id in failed_ids:
                        self._states[failed_id] = "failed"
                        self._errors[failed_id] = str(error)
                        _ = self._derived[failed_id].pop("shared_boundary_review", None)
                    self._revision += 1
                for failed_id in failed_ids:
                    note_failure(failed_id, error, failed_ids)
                continue
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during shared-boundary review")
                if self._derived[owner.id].get("shared_boundary_review") != review:
                    self._derived[owner.id]["shared_boundary_review"] = review
                    self._revision += 1
        for key in order:
            body = nodes.get(key)
            if not isinstance(body, Body) or key in failures:
                continue
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during body assembly")
                already_ready = self._states[key] == "ready"
                if not already_ready:
                    self._states[key] = "running"
                    self._revision += 1
            from experiments.body_geometry import BodyAssemblyError, assemble_body

            try:
                owners = {
                    face.managed_by
                    for face_id in body.faces
                    if (face := nodes[face_id]).managed_by is not None
                    and isinstance(nodes.get(face.managed_by), BuildFaces)
                }
                with self.lock:
                    if epoch != self._epoch:
                        raise StaleGraph("graph changed during body assembly")
                    unavailable = [
                        ref
                        for ref in [*body.faces, *sorted(owners)]
                        if self._states.get(ref) != "ready"
                    ]
                    incomplete = [
                        owner_id
                        for owner_id in sorted(owners)
                        if cast(BuildFaces, nodes[owner_id]).boundary_sources
                        and not self._derived.get(owner_id, {})
                        .get("shared_boundary_review", {})
                        .get("complete")
                    ]
                if unavailable or incomplete:
                    causes = list(dict.fromkeys([*unavailable, *incomplete]))
                    error = ValueError(
                        "Blocked by " + ", ".join(nodes[ref].label for ref in causes)
                    )
                    with self.lock:
                        if epoch != self._epoch:
                            raise StaleGraph("graph changed during body assembly")
                        self._states[key] = "blocked"
                        self._errors[key] = str(error)
                        self._diagnostics[key] = {"blocked_by": causes}
                        _ = self._derived.pop(key, None)
                        self._revision += 1
                    note_failure(key, error, set(causes))
                    continue
                if already_ready:
                    continue
                derived_body = assemble_body(
                    {face_id: derived_result(face_id) for face_id in body.faces},
                    body.sewing_tolerance,
                )
            except Exception as error:
                with self.lock:
                    if epoch == self._epoch:
                        self._states[key] = "failed"
                        self._errors[key] = str(error)
                        if isinstance(error, BodyAssemblyError):
                            self._diagnostics[key] = deepcopy(error.diagnostic)
                        else:
                            _ = self._diagnostics.pop(key, None)
                        self._revision += 1
                note_failure(key, error, {key})
                continue
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph("graph changed during body assembly")
                self._derived[key] = derived_body
                self._states[key] = "ready"
                _ = self._errors.pop(key, None)
                _ = self._diagnostics.pop(key, None)
                self._revision += 1
        if evaluation_errors:
            raise evaluation_errors[0]
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
