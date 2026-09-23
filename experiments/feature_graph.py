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


class Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Node(Record):
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
    kind: Literal["cone", "cylinder", "plane"]
    axial_domain: tuple[float, float] = (-2.0, 5.0)
    axis: str | None = None
    reference_plane: str | None = None

    @model_validator(mode="after")
    def compatible_reference_geometry(self) -> SurfaceFit:
        if self.axis is not None and self.reference_plane is not None:
            raise ValueError("a fit can reference only one datum")
        if self.reference_plane is not None and self.kind != "plane":
            raise ValueError("only a plane fit can reference a plane datum")
        return self


class Growth(Node):
    operation: Literal["growth"]
    seed_fit: str
    barriers: list[str] = Field(default_factory=list, max_length=99)
    distance: float = Field(gt=0, allow_inf_nan=False)
    angle_degrees: float = Field(gt=0, le=90, allow_inf_nan=False)


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
    """Explicit axis with either a manual value or an upstream fit initializer."""

    operation: Literal["axis"]
    source_fit: str | None = None
    initial_parameters: tuple[float, float, float, float] | None = None

    @model_validator(mode="after")
    def exactly_one_initializer(self) -> AxisDefinition:
        if (self.source_fit is None) == (self.initial_parameters is None):
            raise ValueError(
                "axis requires exactly one of source_fit or initial_parameters"
            )
        return self


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
    | Perpendicular
    | Coaxial
    | RotationalSymmetry
    | JointFit
    | AxisDefinition
    | PlaneDefinition
    | MirrorSymmetry
    | ParallelToPlane
    | EqualQuantities
    | AxisSolve,
    Field(discriminator="operation"),
]


class Recipe(Record):
    schema_version: Literal[2] = 2
    nodes: list[Feature] = Field(min_length=1, max_length=100)
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
        reference = node.axis or node.reference_plane
        return [*node.selections, *((reference,) if reference is not None else ())]
    if isinstance(node, Growth):
        return [node.seed_fit, *node.barriers]
    if isinstance(node, Perpendicular):
        return [node.lateral, node.plane]
    if isinstance(node, Coaxial):
        return [node.surface, node.reference]
    if isinstance(node, RotationalSymmetry):
        return [node.axis, *node.planes]
    if isinstance(node, JointFit):
        return node.constraints
    if isinstance(node, AxisDefinition):
        return [node.source_fit] if node.source_fit is not None else []
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
    if isinstance(node, AxisSolve):
        return [node.axis, *node.factors]
    return []


def is_standalone_fit(node: SurfaceFit) -> bool:
    return node.axis is None and node.reference_plane is None


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
        if not isinstance(axis, AxisDefinition) or axis.source_fit is not None:
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
            *(
                node.id
                for node in nodes.values()
                if isinstance(node, PlaneDefinition) and node.axis == axis.id
            ),
            *(factor.id for factor in factors),
        }
        components[axis.id] = (factors, members)
    return components


def selection_source(node: Feature, nodes: dict[str, Feature]) -> str:
    if isinstance(node, Selection):
        return node.source
    if isinstance(node, Growth):
        fitted = nodes[node.seed_fit]
        if isinstance(fitted, SurfaceFit):
            sources = {selection_source(nodes[key], nodes) for key in fitted.selections}
            if len(sources) == 1:
                return next(iter(sources))
    raise ValueError("expected selections from one source")


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
        self._derived: dict[str, dict[str, Any]] = {}
        self._errors: dict[str, str] = {}
        self._diagnostics: dict[str, dict[str, Any]] = {}
        self._epoch = 0  # In-flight invalidation only; no retained previous states.

    def validate(self, recipe: Recipe) -> Recipe:
        nodes = {node.id: node for node in recipe.nodes}
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
                if node.reference_plane is not None and not isinstance(
                    nodes[node.reference_plane], PlaneDefinition
                ):
                    raise ValueError(
                        "plane-bound fit requires an explicit reference plane"
                    )
            elif isinstance(node, Growth):
                fitted = nodes[node.seed_fit]
                if not isinstance(fitted, SurfaceFit) or not is_standalone_fit(fitted):
                    raise ValueError("growth input must be an earlier standalone fit")
                source = selection_source(node, nodes)
                for barrier in node.barriers:
                    if selection_source(nodes[barrier], nodes) != source:
                        raise ValueError("growth barrier belongs to another source")
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
                else:
                    assert node.initial_parameters is not None
                    if not np.isfinite(node.initial_parameters).all():
                        raise ValueError("manual axis initialization must be finite")
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
                    for surface in surfaces
                ):
                    raise ValueError(
                        "mirror symmetry requires two standalone surface fits"
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
                axis = deepcopy(self._derived.get(axis_id, {}))
                axis.update(
                    {
                        "parameters": solve["fit"]["parameters"],
                        "axis_display": solve["axis_display"],
                        "point_display": solve["point_display"],
                        "resolved_by": "connected_fits",
                    }
                )
                resolved[axis_id] = axis
                condition = solve["fit"]["normal_matrix_condition"]
                for fit_id, surface in solve.get("surfaces", {}).items():
                    resolved[fit_id] = {**deepcopy(surface), "condition": condition}
                for plane_id, plane in solve.get("reference_planes", {}).items():
                    resolved[plane_id] = deepcopy(plane)
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
                    if isinstance(n, (Selection, Growth))
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
                        Selection,
                        Source,
                        AxisDefinition,
                        PlaneDefinition,
                        AxisSolve,
                    ),
                ):
                    raise ValueError(
                        "evaluation target must be a source, selection, fit, axis, plane, solve, or growth action"
                    )
                needed = {target}
                for _, members in connected_components.values():
                    if target in members:
                        needed.update(members)
                while True:
                    expanded = needed | {
                        dep for key in needed for dep in dependencies(nodes[key])
                    }
                    if expanded == needed:
                        break
                    needed = expanded
                order = tuple(n.id for n in recipe.nodes if n.id in needed)

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
                    elif node.axis is None:
                        derived = fit_seed(
                            self.workspace.local[ids],
                            self.workspace.data.weights[ids],
                            self.workspace.data.normals[ids] @ self.workspace.frame,
                            node.kind,
                            np.array(
                                self.workspace.data.selection["initial_parameters"]
                            ),
                            node.axial_domain,
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
                    else:
                        assert node.initial_parameters is not None
                        parameters = [*node.initial_parameters, 0.0, 0.0, 0.0]
                    raw_axis = np.array([parameters[2], parameters[3], 1.0])
                    axis = raw_axis / np.linalg.norm(raw_axis)
                    point = np.array([parameters[0], parameters[1], 0.0])
                    derived = {
                        "source_fit": node.source_fit,
                        "parameters": parameters,
                        "axis_display": axis.tolist(),
                        "point_display": point.tolist(),
                    }
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
        evaluated = set(order)
        explicit_solve_axes = {
            cast(AxisSolve, nodes[key]).axis
            for key in evaluated
            if isinstance(nodes[key], AxisSolve)
        }
        with self.lock:
            if epoch != self._epoch:
                raise StaleGraph("graph changed during evaluation")
            for axis_id in explicit_solve_axes:
                _ = self._connected_solves.pop(axis_id, None)
        for axis_id, (factors, members) in connected_components.items():
            if axis_id in explicit_solve_axes or not members <= evaluated:
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
