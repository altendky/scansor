"""Private surface-selection previews over one immutable workspace topology."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Literal

from pydantic import Field

from experiments.feature_graph import FeatureGraph, Record, StaleGraph
from experiments.scan_workspace import Workspace, check_cancelled
from experiments.seed_surface_selection import (
    SurfaceSelectionResult,
    SurfaceSelectionTopology,
    build_surface_topology,
    grow_surface_selection,
)

SURFACE_SELECTION_PROFILE: dict[str, Any] = {
    "supported": True,
    "version": 1,
    "policy": "seed-normal-envelope-v1",
    "max_preview_ids": 2048,
}


class SurfaceSelectionPreviewRequest(Record):
    token: str
    seed_selection: str
    barriers: list[str] = Field(default_factory=list, max_length=99)
    angle_degrees: float = Field(default=20.0, gt=0, le=90, allow_inf_nan=False)
    setback: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    policy: Literal["seed-normal-envelope-v1"] = "seed-normal-envelope-v1"


# Weak identity keys avoid NumPy-backed workspace hashing and release topology
# when its source owner retires. Geometry is never copied into saved actions.
@dataclass
class _TopologyEntry:
    owner: weakref.ReferenceType[Workspace]
    lock: RLock = field(default_factory=RLock)
    topology: SurfaceSelectionTopology | None = None


_TOPOLOGIES: dict[int, _TopologyEntry] = {}
_TOPOLOGY_LOCK = RLock()


def surface_selection_result(
    workspace: Workspace,
    seeds: list[int],
    barriers: list[int],
    *,
    angle_degrees: float,
    setback: float,
    cancelled: Callable[[], bool] | None = None,
) -> SurfaceSelectionResult:
    check_cancelled(cancelled)
    key = id(workspace)
    with _TOPOLOGY_LOCK:
        entry = _TOPOLOGIES.get(key)
        if entry is None or entry.owner() is not workspace:

            def release(reference: weakref.ReferenceType[Workspace]) -> None:
                with _TOPOLOGY_LOCK:
                    current = _TOPOLOGIES.get(key)
                    if current is not None and current.owner is reference:
                        _ = _TOPOLOGIES.pop(key)

            entry = _TopologyEntry(weakref.ref(workspace, release))
            _TOPOLOGIES[key] = entry
    # Graph snapshots can collect an older workspace under the graph lock.
    # Builders call graph cancellation checkpoints, so they must never retain
    # the collection lock needed by that workspace's weakref callback.
    with entry.lock:
        check_cancelled(cancelled)
        if entry.topology is None:
            entry.topology = build_surface_topology(
                workspace.local,
                workspace.data.triangles,
                cancelled=cancelled,
            )
        topology = entry.topology
    check_cancelled(cancelled)
    return grow_surface_selection(
        topology,
        seeds,
        barriers,
        angle_degrees=angle_degrees,
        setback=setback,
        cancelled=cancelled,
    )


def prepare_surface_selection_preview(
    graph: FeatureGraph[Workspace], request: SurfaceSelectionPreviewRequest
) -> Callable[[Callable[[], bool] | None], dict[str, object]]:
    """Validate ready inputs now and capture private membership for queued work."""
    if len(set(request.barriers)) != len(request.barriers):
        raise ValueError("surface-selection barriers must be unique")
    if request.seed_selection in request.barriers:
        raise ValueError("surface-selection seeds cannot also be a barrier")
    _, inputs = graph.ready_selection_inputs(
        request.token, [request.seed_selection, *request.barriers]
    )
    seeds = inputs[request.seed_selection]
    barriers = sorted({vertex for key in request.barriers for vertex in inputs[key]})

    def preview(cancelled: Callable[[], bool] | None = None) -> dict[str, object]:
        def stopped() -> bool:
            if cancelled is not None and cancelled():
                return True
            try:
                return graph.publication_token(request.token) != request.token
            except StaleGraph:
                return True

        _ = graph.export_recipe(request.token)
        result = surface_selection_result(
            graph.workspace,
            seeds,
            barriers,
            angle_degrees=request.angle_degrees,
            setback=request.setback,
            cancelled=stopped,
        )
        check_cancelled(cancelled)
        _ = graph.export_recipe(request.token)
        return {
            "token": request.token,
            "preview": result.preview(
                max_ids=SURFACE_SELECTION_PROFILE["max_preview_ids"]
            ),
        }

    return preview
