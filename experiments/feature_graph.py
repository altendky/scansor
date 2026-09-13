"""Experimental backend DAG for captured-mesh recipes; current state only."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from threading import RLock
from typing import Annotated, ClassVar, Literal, final

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.nozzle_coaxial import FitSelection, fit_group
from experiments.nozzle_session import (
    NozzleSession,
    NozzleWorkspace,
    SessionFit,
    VertexId,
)


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
    depth: Literal["through_all"] = "through_all"


class Surface(Node):
    operation: Literal["surface"]
    selection: str
    kind: Literal["cone", "cylinder", "plane"]
    axial_domain: tuple[float, float] = (-2.0, 5.0)


class Perpendicular(Node):
    operation: Literal["perpendicular"]
    lateral: str
    plane: str


class Coaxial(Node):
    operation: Literal["coaxial"]
    surface: str
    reference: str


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
    Source | Selection | Surface | Perpendicular | Coaxial | JointFit,
    Field(discriminator="operation"),
]


class Recipe(Record):
    schema_version: Literal[1] = 1
    nodes: list[Feature] = Field(min_length=1, max_length=100)
    output: str


class GraphRequest(Record):
    token: str
    recipe: Recipe | None = None


def dependencies(node: Feature) -> list[str]:
    if isinstance(node, Selection):
        return [node.source]
    if isinstance(node, Surface):
        return [node.selection]
    if isinstance(node, Perpendicular):
        return [node.lateral, node.plane]
    if isinstance(node, Coaxial):
        return [node.surface, node.reference]
    if isinstance(node, JointFit):
        return node.constraints
    return []


def joint_surfaces(
    node: JointFit, nodes: dict[str, Feature]
) -> tuple[list[Surface], Surface]:
    """Compile a connected constraint group; one perpendicular plane for now."""
    if len(set(node.constraints)) != len(node.constraints):
        raise ValueError("duplicate joint constraint")
    sides: dict[str, Surface] = {}
    planes: dict[str, Surface] = {}
    edges: list[tuple[str, str]] = []
    anchors: set[str] = set()
    for key in node.constraints:
        constraint = nodes[key]
        if isinstance(constraint, Perpendicular):
            side, plane = nodes[constraint.lateral], nodes[constraint.plane]
            if (
                not isinstance(side, Surface)
                or side.kind not in ("cone", "cylinder")
                or not isinstance(plane, Surface)
                or plane.kind != "plane"
            ):
                raise ValueError(
                    "supported relationship needs a cone/cylinder and plane"
                )
            sides[side.id], planes[plane.id] = side, plane
            anchors.add(side.id)
        elif isinstance(constraint, Coaxial):
            for ref in (constraint.surface, constraint.reference):
                surface = nodes[ref]
                if not isinstance(surface, Surface) or surface.kind not in (
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
    if len(planes) != 1:
        raise ValueError(
            "joint solve currently requires exactly one perpendicular plane"
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
    return ordered, next(iter(planes.values()))


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
        self._errors: dict[str, str] = {}
        self._epoch = 0  # In-flight invalidation only; no retained previous states.

    def validate(self, recipe: Recipe) -> Recipe:
        nodes = {node.id: node for node in recipe.nodes}
        if len(nodes) != len(recipe.nodes):
            raise ValueError("duplicate feature ID")
        if not isinstance(nodes.get(recipe.output), JointFit):
            raise ValueError("output must reference a joint-fit node")
        refs = {key: dependencies(node) for key, node in nodes.items()}
        if any(dep not in nodes for deps in refs.values() for dep in deps):
            raise ValueError("missing feature input")
        try:
            _ = tuple(TopologicalSorter(refs).static_order())
        except CycleError as error:
            raise ValueError("feature graph contains a cycle") from error
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
            elif isinstance(node, Surface):
                if not isinstance(nodes[node.selection], Selection):
                    raise ValueError("surface input must be a selection")
                lo, hi = node.axial_domain
                if not np.isfinite([lo, hi]).all() or lo >= hi:
                    raise ValueError("invalid axial domain")
            elif isinstance(node, Perpendicular):
                lateral, plane = nodes[node.lateral], nodes[node.plane]
                if (
                    not isinstance(lateral, Surface)
                    or lateral.kind not in ("cone", "cylinder")
                    or not isinstance(plane, Surface)
                    or plane.kind != "plane"
                ):
                    raise ValueError(
                        "supported relationship needs a cone/cylinder and plane"
                    )
                left, right = nodes[lateral.selection], nodes[plane.selection]
                if not isinstance(left, Selection) or not isinstance(right, Selection):
                    raise ValueError("surface input must be a selection")
                if left.source != right.source or set(left.ids).intersection(right.ids):
                    raise ValueError(
                        "joint fit needs disjoint selections on the same source"
                    )
            elif isinstance(node, Coaxial):
                pair = [nodes[node.surface], nodes[node.reference]]
                if node.surface == node.reference or any(
                    not isinstance(n, Surface) or n.kind not in ("cone", "cylinder")
                    for n in pair
                ):
                    raise ValueError(
                        "coaxial relationship requires two distinct cone/cylinder surfaces"
                    )
            else:
                sides, plane = joint_surfaces(node, nodes)
                used: set[int] = set()
                selections: set[str] = set()
                sources: set[str] = set()
                for surface in [*sides, plane]:
                    selection = nodes[surface.selection]
                    if not isinstance(selection, Selection):
                        raise ValueError("surface input must be a selection")
                    sources.add(selection.source)
                    if selection.id in selections:
                        raise ValueError(
                            "joint surfaces require distinct selection nodes"
                        )
                    selections.add(selection.id)
                    if used.intersection(selection.ids):
                        raise ValueError(
                            "joint fit needs disjoint selections on the same source"
                        )
                    used.update(selection.ids)
                if len(sources) != 1:
                    raise ValueError(
                        "joint fit needs disjoint selections on the same source"
                    )
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
                "result": deepcopy(self._results.get(self._recipe.output)),
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
            self._errors = {
                key: value
                for key, value in self._errors.items()
                if key not in affected and any(n.id == key for n in recipe.nodes)
            }
            # Running work is invalidated even if content changes back later.
            self._states = {
                key: "stale" if value == "running" else value
                for key, value in self._states.items()
            }
            self._recipe = recipe
            self._epoch += 1
            return self.snapshot()

    def evaluate(self, token: str) -> dict[str, object]:
        with self.lock:
            if token != self._token():
                raise StaleGraph("graph changed before evaluation")
            recipe, epoch = self._recipe.model_copy(deep=True), self._epoch
            nodes = {n.id: n for n in recipe.nodes}
            needed = {recipe.output}
            while True:
                expanded = needed | {
                    dep for key in needed for dep in dependencies(nodes[key])
                }
                if expanded == needed:
                    break
                needed = expanded
            order = tuple(
                TopologicalSorter(
                    {key: dependencies(nodes[key]) for key in needed}
                ).static_order()
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
                if isinstance(node, JointFit):
                    sides, plane = joint_surfaces(node, nodes)

                    def selected(surface: Surface) -> FitSelection:
                        selection = nodes[surface.selection]
                        assert isinstance(selection, Selection)
                        return FitSelection(
                            surface.id,
                            selection.ids,
                            surface.kind,
                            surface.axial_domain,
                        )

                    if len(sides) > 1:
                        result = fit_group(
                            self.workspace,
                            [selected(side) for side in sides],
                            selected(plane),
                        )
                    else:
                        lateral = sides[0]
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
                raise
            with self.lock:
                if epoch != self._epoch:
                    raise StaleGraph(
                        "graph changed during evaluation; result discarded"
                    )
                self._states[key] = "ready"
                _ = self._errors.pop(key, None)
                if result is not None:
                    self._results[key] = result
        return self.snapshot()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--example", type=Path, default=Path("examples/nozzle-bayonette-simplified")
    )
    _ = parser.add_argument("--recipe", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    graph = FeatureGraph(
        NozzleWorkspace(args.example),
        Recipe.model_validate_json(args.recipe.read_text()),
    )
    state = graph.evaluate(str(graph.snapshot()["token"]))
    with args.output.open("x") as stream:
        _ = stream.write(json.dumps(state, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
