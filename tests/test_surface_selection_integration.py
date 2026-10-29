"""Derived seed surfaces participate in ordinary graph and source workflows."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import gc
import io
import json
import weakref
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from typing import Any, cast

import pytest

import experiments.seed_surface_authoring as authoring
from experiments.browser_runtime.workspace import BrowserWorkspace
from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph, selection_source
from experiments.feature_inputs import OutputReference, resolve_output
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.scan_project import ScanProject
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import ScanLimits, ScanWorkspace
from experiments.seed_surface_authoring import (
    SurfaceSelectionPreviewRequest,
    prepare_surface_selection_preview,
)
from experiments.seed_surface_selection import (
    SurfaceSelectionTopology,
    build_surface_topology,
)
from experiments.virtual_scan_reduction import ReductionSettings
from tests.native_scan_fixture import native_grid_fixture
from tests.test_browser_scan_actions import promote, unpack

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000)


def recipe(
    workspace: ScanWorkspace, count: int = 5, *, barriers: bool = False
) -> Recipe:
    assert workspace.descriptor is not None
    return Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": "scan",
                    "label": "Scan",
                    "operation": "source",
                    "source_sha256": workspace.source_sha256,
                    "reference_sha256": workspace.reference_sha256,
                    "descriptor": workspace.descriptor.model_dump(),
                },
                {
                    "id": "seeds",
                    "label": "Painted seeds",
                    "operation": "selection",
                    "source": "scan",
                    "ids": [count + 1],
                },
                {
                    "id": "barrier",
                    "label": "Barrier strip",
                    "operation": "selection",
                    "source": "scan",
                    "ids": list(range(2, count * count, count)),
                },
                {
                    "id": "surface",
                    "label": "Seed surface",
                    "operation": "surface_selection",
                    "seed_selection": "seeds",
                    "barriers": ["barrier"] if barriers else [],
                },
                {
                    "id": "plane",
                    "label": "Plane from surface",
                    "operation": "fit",
                    "kind": "plane",
                    "selections": ["surface"],
                },
            ],
            "output": "plane",
        }
    )


def project(count: int = 5, *, barriers: bool = False) -> ScanProject:
    owner = ScanProject()
    empty = owner.accepted
    accepted = owner.commit(
        owner.prepare_add(
            io.BytesIO(native_grid_fixture(count, count).raw),
            "grid.ply",
            LIMITS,
            generation=0,
            token=str(empty.graph.snapshot()["token"]),
        )
    )
    _ = accepted.graph.replace(
        recipe(accepted.workspace, count, barriers=barriers),
        str(accepted.graph.snapshot()["token"]),
    )
    return owner


def state(graph: FeatureGraph[ScanWorkspace]) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def test_surface_is_a_real_observation_dependency_and_saved_definitions_exclude_ids() -> (
    None
):
    owner = project(barriers=True)
    graph = owner.accepted.graph
    ready = cast(dict[str, Any], graph.ensure_current(state(graph)["token"], ["plane"]))
    assert set(ready["states"].values()) == {"ready"}
    ids = ready["memberships"]["surface"]
    assert 6 in ids and not set(ids).intersection(ready["memberships"]["barrier"])
    assert ready["results"]["plane"]["ids"] == ids
    nodes = {node.id: node for node in Recipe.model_validate(ready["recipe"]).nodes}
    assert selection_source(nodes["surface"], nodes) == "scan"
    descriptor = next(
        item
        for item in ready["input_catalogue"]
        if item["reference"]["feature"] == "surface"
    )
    assert (
        descriptor["capability"] == "observations"
        and descriptor["availability"] == "ready"
    )
    assert descriptor["preview"] is None
    output = resolve_output(
        OutputReference(feature="surface", output="observations"),
        nodes,
        ready["results"],
    )
    assert output == {"ids": ids, "source": "scan"}
    saved = graph.export_recipe()
    assert set(saved["nodes"][3]) == {
        "id",
        "label",
        "operation",
        "group_id",
        "managed_by",
        "managed_key",
        "seed_selection",
        "barriers",
        "angle_degrees",
        "setback",
        "policy",
    }
    reopened = FeatureGraph(
        owner.accepted.workspace, Recipe.model_validate_json(json.dumps(saved))
    )
    assert not state(reopened)["results"]
    replay = cast(
        dict[str, Any], reopened.ensure_current(state(reopened)["token"], ["plane"])
    )
    assert replay["memberships"]["surface"] == ids
    changed = Recipe.model_validate(saved)
    changed.nodes[3] = changed.nodes[3].model_copy(update={"angle_degrees": 21.0})
    invalidated = cast(dict[str, Any], graph.replace(changed, ready["token"]))
    assert invalidated["states"]["surface"] == invalidated["states"]["plane"] == "stale"
    assert (
        "surface" not in invalidated["results"]
        and "plane" not in invalidated["results"]
    )


def test_surface_can_drive_an_existing_reusable_region() -> None:
    owner = project()
    graph = owner.accepted.graph
    before = state(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "axis",
                "label": "Datum axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 0, 0],
            },
            {
                "id": "axial",
                "label": "Axial datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
            },
            {
                "id": "clock",
                "label": "Clock datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "contains_axis",
            },
            {
                "id": "region",
                "label": "Reusable seed surface",
                "operation": "selection_region",
                "selection": "surface",
                "fit": "plane",
                "axial_plane": "axial",
                "clock_plane": "clock",
            },
            {
                "id": "replayed",
                "label": "Replayed region",
                "operation": "region_selection",
                "region": "region",
                "source": "scan",
                "axial_plane": "axial",
                "clock_plane": "clock",
            },
        ]
    )
    payload["output"] = "replayed"
    changed = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), before["token"])
    )
    ready = cast(dict[str, Any], graph.ensure_current(changed["token"], ["replayed"]))
    assert ready["results"]["region"]["selection"] == "surface"
    assert (
        ready["memberships"]["replayed"]
        == ready["memberships"]["surface"]
        == list(range(25))
    )


def test_surface_fit_is_a_valid_source_for_ordinary_feature_reuse() -> None:
    owner = project()
    graph = owner.accepted.graph
    before = state(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"].append(
        {
            "id": "target",
            "label": "Target observations",
            "operation": "selection",
            "source": "scan",
            "ids": list(range(25)),
        }
    )
    plan = reconcile_feature_reuse(
        Recipe.model_validate(payload),
        "reuse",
        ReuseChanges(
            label="Reused seed surface",
            fits=["plane"],
            reference_selection="surface",
            target_selections=["target"],
        ),
        "surface-test",
        create=True,
    )
    changed = cast(dict[str, Any], graph.replace(plan.recipe, before["token"]))
    ready = cast(
        dict[str, Any], graph.ensure_current(changed["token"], all_actions=True)
    )
    assert not ready["errors"] and set(ready["states"].values()) == {"ready"}
    generated = [
        node
        for node in ready["recipe"]["nodes"]
        if node.get("managed_by") == "reuse" and node["operation"] == "reuse_selection"
    ]
    assert len(generated) == 1 and generated[0]["source_selection"] == "surface"
    assert ready["memberships"][generated[0]["id"]] == ready["memberships"]["surface"]


@pytest.mark.parametrize(
    "changes",
    [
        {"seed_selection": "plane"},
        {"seed_selection": "surface"},
        {"barriers": ["seeds"]},
        {"barriers": ["barrier", "barrier"]},
        {"angle_degrees": 0},
        {"angle_degrees": 91},
        {"setback": -1},
        {"policy": "chain-local"},
    ],
)
def test_invalid_surface_inputs_and_cycles_are_rejected(
    changes: dict[str, Any],
) -> None:
    owner = project()
    graph = owner.accepted.graph
    before = state(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"][3].update(changes)
    with pytest.raises(ValueError):
        _ = graph.replace(Recipe.model_validate(payload), before["token"])
    assert state(graph) == before


def test_preview_requires_ready_inputs_is_bounded_and_never_publishes() -> None:
    owner = project(65)
    graph = owner.accepted.graph
    request = SurfaceSelectionPreviewRequest(
        token=state(graph)["token"], seed_selection="seeds"
    )
    before = state(graph)
    with pytest.raises(ValueError, match="Evaluate the seed"):
        _ = prepare_surface_selection_preview(graph, request)
    assert state(graph) == before
    _ = graph.ensure_current(request.token, ["seeds"])
    before = state(graph)
    preview = cast(
        dict[str, Any],
        prepare_surface_selection_preview(graph, request)(None)["preview"],
    )
    assert isinstance(preview, dict)
    assert preview["counts"]["region"] == 65 * 65
    assert set(preview["samples"]) == {
        "seeds",
        "region",
        "boundaries",
        "barriers",
        "inset",
    }
    assert all(len(ids) <= 2048 for ids in preview["samples"].values())
    assert len(preview["samples"]["region"]) == 2048
    assert state(graph) == before
    private = prepare_surface_selection_preview(graph, request)
    changed = Recipe.model_validate(before["recipe"])
    changed.nodes[1] = changed.nodes[1].model_copy(update={"label": "Renamed seeds"})
    _ = graph.replace(changed, request.token)
    with pytest.raises(StaleGraph):
        _ = private(None)


def test_topology_cache_is_shared_and_released_with_immutable_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = project()
    workspace = owner.accepted.workspace
    original = build_surface_topology
    calls = 0

    def build(*args: Any, **kwargs: Any) -> SurfaceSelectionTopology:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(authoring, "build_surface_topology", build)
    for angle in (20.0, 30.0):
        result = authoring.surface_selection_result(
            workspace, [6], [], angle_degrees=angle, setback=0
        )
        assert len(result.ids) == 25
    assert calls == 1
    reference, key = weakref.ref(workspace), id(workspace)
    del owner, workspace
    _ = gc.collect()
    assert reference() is None and key not in authoring._TOPOLOGIES


def test_topology_build_leaves_collection_lock_available_for_source_retirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = project()
    workspace = owner.accepted.workspace
    entered, release = Event(), Event()

    def build(*args: Any, **kwargs: Any) -> SurfaceSelectionTopology:
        entered.set()
        assert release.wait(10)
        return build_surface_topology(*args, **kwargs)

    monkeypatch.setattr(authoring, "build_surface_topology", build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            authoring.surface_selection_result,
            workspace,
            [6],
            [],
            angle_degrees=20,
            setback=0,
        )
        assert entered.wait(10)
        acquired = authoring._TOPOLOGY_LOCK.acquire(timeout=0.2)
        try:
            # A graph snapshot can collect an old source while holding its graph
            # lock. Its weakref callback must not wait behind a builder whose
            # cancellation checkpoint needs that same graph lock.
            assert acquired, "topology work held the weak-cache collection lock"
        finally:
            if acquired:
                authoring._TOPOLOGY_LOCK.release()
            release.set()
        assert len(pending.result(timeout=10).ids) == 25


def test_source_replacement_and_virtual_return_keep_derived_definition() -> None:
    owner = project()
    before = state(owner.accepted.graph)
    original = owner.accepted.workspace
    original_binding = original.reference_sha256
    virtual = owner.commit_replace(
        owner.prepare_virtual_scan(
            ReductionSettings(divisions=2),
            LIMITS,
            generation=owner.accepted.generation,
            token=before["token"],
        )
    )
    pending = state(virtual.graph)
    assert pending["recipe"]["nodes"][3] == before["recipe"]["nodes"][3]
    assert pending["states"]["surface"] == pending["states"]["plane"] == "needs_input"
    returned = owner.commit_replace(
        owner.prepare_activate_scan(
            original_binding,
            LIMITS,
            generation=virtual.generation,
            token=pending["token"],
        )
    )
    assert returned.workspace is original
    ready = cast(
        dict[str, Any],
        returned.graph.ensure_current(state(returned.graph)["token"], ["plane"]),
    )
    assert (
        ready["states"]["plane"] == "ready"
        and len(ready["memberships"]["surface"]) == 25
    )
    candidate = owner.prepare_replace(
        io.BytesIO(native_grid_fixture(7, 7).raw),
        "replacement.ply",
        LIMITS,
        generation=returned.generation,
        token=ready["token"],
    )
    replacement = state(candidate.accepted.graph)
    assert (
        replacement["states"]["surface"]
        == replacement["states"]["plane"]
        == "needs_input"
    )
    assert replacement["recipe"]["nodes"][3] == before["recipe"]["nodes"][3]


@pytest.mark.parametrize("queued", [False, True])
def test_worker_preview_and_cancel_keep_prior_computed_results(
    monkeypatch: pytest.MonkeyPatch,
    queued: bool,
) -> None:
    adapter = BrowserWorkspace(None, scan_limits=LIMITS)
    empty = unpack(adapter.call_json("getGraph"))
    candidate = unpack(
        adapter.prepare_source_json(
            json.dumps(
                {
                    "operationId": "surface-scan",
                    "kind": "scan",
                    "generation": 0,
                    "token": empty["token"],
                    "filename": "grid.ply",
                }
            ),
            source_input=BytesInput(native_grid_fixture(5, 5).raw, "grid.ply"),
        )
    )
    _ = promote(adapter, candidate)
    workspace = cast(ScanWorkspace, adapter.workspace)
    graph = adapter.graph
    _ = graph.replace(recipe(workspace), str(graph.snapshot()["token"]))
    ready = cast(
        dict[str, Any], graph.ensure_current(str(graph.snapshot()["token"]), ["plane"])
    )
    payload = {"token": ready["token"], "seed_selection": "seeds"}
    result = unpack(adapter.call_json("previewSurfaceSelection", json.dumps(payload)))
    assert result["preview"]["counts"]["region"] == 25
    assert graph.snapshot()["results"] == ready["results"]
    adapter.checkpoint_operation("getGraph" if queued else "previewSurfaceSelection")

    def interrupt(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyboardInterrupt

    if not queued:
        monkeypatch.setattr(authoring, "grow_surface_selection", interrupt)
        stopped = json.loads(
            adapter.call_json("previewSurfaceSelection", json.dumps(payload))
        )
        assert stopped["error"]["code"] == "computation_cancelled"
    restored = unpack(
        adapter.restore_cancelled_json(
            json.dumps(ready["recipe"]), operation="previewSurfaceSelection"
        )
    )
    assert adapter.graph is graph and adapter.workspace is workspace
    assert (
        restored["results"] == ready["results"]
        and restored["states"] == ready["states"]
    )
