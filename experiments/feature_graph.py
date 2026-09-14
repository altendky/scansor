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

from experiments.nozzle_coaxial import FitSelection, fit_group
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


Feature = Annotated[
    Source
    | Selection
    | SurfaceFit
    | Growth
    | Perpendicular
    | Coaxial
    | RotationalSymmetry
    | JointFit,
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


class GraphRequest(Record):
    token: str
    recipe: Recipe | None = None
    target: str | None = None


def dependencies(node: Feature) -> list[str]:
    if isinstance(node, Selection):
        return [node.source]
    if isinstance(node, SurfaceFit):
        return node.selections
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
    return []


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


@final
class FeatureGraph:
    def __init__(self, workspace: NozzleWorkspace, recipe: Recipe) -> None:
        self.workspace = workspace
        # Bind the fixture adapter's frame/initialization as well as the raw mesh.
        self.reference_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "selection": workspace.data.selection,
                    "model": workspace.model_sha256,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        self.lock = RLock()
        self._recipe = self.validate(recipe)
        self._states: dict[str, str] = dict.fromkeys(
            (n.id for n in recipe.nodes), "unevaluated"
        )
        self._results: dict[str, SessionFit] = {}
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
            elif isinstance(node, Growth):
                fitted = nodes[node.seed_fit]
                if not isinstance(fitted, SurfaceFit):
                    raise ValueError("growth input must be an earlier fit")
                source = selection_source(node, nodes)
                for barrier in node.barriers:
                    if selection_source(nodes[barrier], nodes) != source:
                        raise ValueError("growth barrier belongs to another source")
            elif isinstance(node, Perpendicular):
                lateral, plane = nodes[node.lateral], nodes[node.plane]
                if (
                    not isinstance(lateral, SurfaceFit)
                    or lateral.kind not in ("cone", "cylinder")
                    or not isinstance(plane, SurfaceFit)
                    or plane.kind != "plane"
                ):
                    raise ValueError(
                        "supported relationship needs a cone/cylinder and plane"
                    )
            elif isinstance(node, RotationalSymmetry):
                axis = nodes[node.axis]
                if not isinstance(axis, SurfaceFit) or axis.kind not in (
                    "cone",
                    "cylinder",
                ):
                    raise ValueError(
                        "rotational symmetry requires a cone/cylinder axis fit"
                    )
                if len(set(node.planes)) != 3 or any(
                    not isinstance(nodes[ref], SurfaceFit) for ref in node.planes
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
                    not isinstance(n, SurfaceFit) or n.kind not in ("cone", "cylinder")
                    for n in pair
                ):
                    raise ValueError(
                        "coaxial relationship requires two distinct cone/cylinder surfaces"
                    )
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
            return {
                "recipe": self._recipe.model_dump(),
                "token": self._token(),
                "states": self._states.copy(),
                "errors": self._errors.copy(),
                "diagnostics": deepcopy(self._diagnostics),
                "result": deepcopy(self._results.get(self._recipe.output)),
                "derived": deepcopy(self._derived),
                "results": deepcopy({**self._derived, **self._results}),
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

    def evaluate(self, token: str, target: str | None = None) -> dict[str, object]:
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            recipe, epoch = self._recipe.model_copy(deep=True), self._epoch
            nodes = {n.id: n for n in recipe.nodes}
            target = recipe.output if target is None else target
            if target not in nodes or not isinstance(
                nodes[target], (JointFit, SurfaceFit, Growth, Selection, Source)
            ):
                raise ValueError(
                    "evaluation target must be a source, selection, fit or growth action"
                )
            needed = {target}
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

        def fitted_ids(surface: SurfaceFit) -> list[int]:
            return sorted({i for ref in surface.selections for i in membership(ref)})

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
                    derived = fit_seed(
                        self.workspace.local[ids],
                        self.workspace.data.weights[ids],
                        self.workspace.data.normals[ids] @ self.workspace.frame,
                        node.kind,
                        np.array(self.workspace.data.selection["initial_parameters"]),
                        node.axial_domain,
                    )
                    derived["ids"] = ids
                elif isinstance(node, Growth):
                    fitted = nodes[node.seed_fit]
                    assert isinstance(fitted, SurfaceFit)
                    with self.lock:
                        if epoch != self._epoch:
                            raise StaleGraph("graph changed during evaluation")
                        seed_result = deepcopy(self._derived[node.seed_fit])
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

                    def selected(surface: SurfaceFit) -> FitSelection:
                        selected_ids = fitted_ids(surface)
                        return FitSelection(
                            surface.id,
                            selected_ids,
                            surface.kind,
                            surface.axial_domain,
                        )

                    memberships = [
                        (surface.id, set(fitted_ids(surface)))
                        for surface in [*sides, *planes]
                    ]
                    conflicts: list[dict[str, Any]] = []
                    for index, (left, left_ids) in enumerate(memberships):
                        for right, right_ids in memberships[index + 1 :]:
                            overlap = sorted(left_ids & right_ids)
                            if overlap:
                                conflicts.append(
                                    {"fits": [left, right], "ids": overlap}
                                )
                    if conflicts:
                        raise SelectionOverlap(conflicts)
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
