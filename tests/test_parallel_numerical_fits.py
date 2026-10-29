"""Independent native numerical jobs retain coordinator-owned publication."""

from __future__ import annotations

from concurrent.futures import Executor, ThreadPoolExecutor
from threading import Event
from typing import Any, cast

import numpy as np
import pytest

import experiments.feature_graph as graph_module
from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_browser import NozzleServer
from experiments.scan_sources import BytesInput
from experiments.scan_workspace import (
    ScanCancelled,
    ScanLimits,
    ScanWorkspace,
    prepare_scan_input,
)
from experiments.selection_growth import PlaneFitFailure, fit_seed
from tests.test_native_scan_import import terminal
from tests.test_scan_cylinder_fit import cylinder_grid
from tests.test_stl_scan_workspace import stl_bytes

LIMITS = ScanLimits(2_000_000, 20_000, 40_000, 100_000_000)


def setup_graph(
    executor: Executor | None, *, intersection: bool = False, cylinder: bool = False
) -> FeatureGraph[ScanWorkspace]:
    if cylinder:
        points, _, faces = cylinder_grid()
        count = len(points)
        vertices = np.vstack((points, points + np.array([10.0, 0, 0])))
        triangles = np.vstack((faces, faces + count))
    else:
        vertices = np.array(
            [
                [0, 0, 0],
                [1, 0, 0],
                [0, 1, 0],
                [1, 1, 0],
                [10, 0, 2],
                [11, 0, 2],
                [10, 1, 2],
                [11, 1, 2],
            ]
        )
        triangles = np.array([[0, 1, 2], [1, 3, 2], [4, 5, 6], [5, 7, 6]])
        count = 4
    raw = stl_bytes(
        [(float(row[0]), float(row[1]), float(row[2])) for row in vertices],
        [(int(row[0]), int(row[1]), int(row[2])) for row in triangles],
    )
    workspace = prepare_scan_input(BytesInput(raw, "independent.stl"), LIMITS)
    assert workspace.descriptor is not None
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
            "descriptor": workspace.descriptor.model_dump(),
        }
    ]
    # Deliberately interleave membership and fitting tasks as circular children do.
    for index in range(2):
        nodes.extend(
            [
                {
                    "id": f"s{index}",
                    "label": f"Selection {index}",
                    "operation": "selection",
                    "source": "scan",
                    "ids": list(range(index * count, (index + 1) * count)),
                },
                {
                    "id": f"f{index}",
                    "label": f"Fit {index}",
                    "operation": "fit",
                    "kind": "cylinder" if cylinder else "plane",
                    "selections": [f"s{index}"],
                },
            ]
        )
    if intersection:
        nodes.append(
            {
                "id": "intersection",
                "label": "Dependent intersection",
                "operation": "surface_intersection",
                "first": {"feature": "f0"},
                "second": {"feature": "f1"},
            }
        )
    return FeatureGraph(
        workspace,
        Recipe.model_validate(
            {"schema_version": 2, "nodes": nodes, "output": nodes[-1]["id"]}
        ),
        fit_executor=executor,
    )


def evaluate(graph: FeatureGraph[ScanWorkspace]) -> dict[str, Any]:
    return cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )


def snapshot(graph: FeatureGraph[ScanWorkspace]) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def test_independent_jobs_overlap_and_dependencies_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finished = [Event(), Event()], [Event(), Event()]
    release = Event()
    fit = fit_seed
    intersection_entered = Event()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        index = int(points[:, 0].mean() > 5)
        assert not points.flags.writeable
        entered[index].set()
        assert release.wait(10), "test did not release numerical jobs"
        result = fit(points, *args, **kwargs)
        finished[index].set()
        return result

    def dependent(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        assert all(event.is_set() for event in finished)
        intersection_entered.set()
        return {"kind": "test-intersection"}

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    monkeypatch.setattr(graph_module, "surface_intersection", dependent)
    with (
        ThreadPoolExecutor(max_workers=2) as numerical,
        ThreadPoolExecutor(max_workers=1) as coordinator,
    ):
        graph = setup_graph(numerical, intersection=True)
        future = coordinator.submit(evaluate, graph)
        try:
            assert all(event.wait(10) for event in entered)
            assert not intersection_entered.is_set()
            state = snapshot(graph)
            assert state["states"]["f0"] == state["states"]["f1"] == "running"
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["states"]["intersection"] == "ready"
    assert not result["errors"]


def test_failure_blocks_dependents_but_independent_fit_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = [Event(), Event()]
    release = Event()
    fit = fit_seed

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        index = int(points[:, 0].mean() > 5)
        entered[index].set()
        assert release.wait(10)
        if not index:
            raise PlaneFitFailure("test-failure", "first fit failed")
        return fit(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    with (
        ThreadPoolExecutor(max_workers=2) as numerical,
        ThreadPoolExecutor(max_workers=1) as coordinator,
    ):
        graph = setup_graph(numerical, intersection=True)
        future = coordinator.submit(evaluate, graph)
        try:
            assert all(event.wait(10) for event in entered)
        finally:
            release.set()
        with pytest.raises(PlaneFitFailure, match="first fit failed"):
            _ = future.result(timeout=10)
    result = snapshot(graph)
    assert result["states"]["f0"] == "failed"
    assert result["states"]["f1"] == "ready"
    assert result["states"]["intersection"] == "blocked"
    assert result["diagnostics"]["intersection"]["blocked_by"] == ["f0"]


@pytest.mark.parametrize("restore", [False, True])
def test_replaced_epoch_discards_every_late_fit_result(
    monkeypatch: pytest.MonkeyPatch, restore: bool
) -> None:
    entered = [Event(), Event()]
    release = Event()
    fit = fit_seed

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        entered[int(points[:, 0].mean() > 5)].set()
        assert release.wait(10)
        return fit(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    with (
        ThreadPoolExecutor(max_workers=2) as numerical,
        ThreadPoolExecutor(max_workers=1) as coordinator,
    ):
        graph = setup_graph(numerical)
        before = snapshot(graph)
        future = coordinator.submit(evaluate, graph)
        try:
            assert all(event.wait(10) for event in entered)
            recipe = Recipe.model_validate(before["recipe"])
            recipe.nodes[-1] = recipe.nodes[-1].model_copy(
                update={"label": "Changed fit"}
            )
            changed = graph.replace(recipe, str(before["token"]))
            if restore:
                _ = graph.replace(
                    Recipe.model_validate(before["recipe"]), str(changed["token"])
                )
            preserved = snapshot(graph)
        finally:
            release.set()
        with pytest.raises(StaleGraph):
            _ = future.result(timeout=10)
    assert snapshot(graph) == preserved
    assert "f0" not in preserved["results"] and "f1" not in preserved["results"]


@pytest.mark.parametrize("cylinder", [False, True])
def test_parallel_numerical_results_match_serial(cylinder: bool) -> None:
    serial = evaluate(setup_graph(None, cylinder=cylinder))
    with ThreadPoolExecutor(max_workers=2) as numerical:
        parallel = evaluate(setup_graph(numerical, cylinder=cylinder))
    assert serial["recipe"] == parallel["recipe"]
    assert serial["results"] == parallel["results"]
    assert serial["diagnostics"] == parallel["diagnostics"]
    assert serial["states"] == parallel["states"]


def test_native_server_runs_independent_fits_and_closes_its_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = [Event(), Event()]
    release = Event()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        entered[int(points[:, 0].mean() > 5)].set()
        assert release.wait(10)
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    with NozzleServer() as server:
        template = setup_graph(None)
        source = template.workspace.source
        assert source is not None
        context = server.import_context()
        operation = server.source_imports.reserve(
            generation=context.generation,
            token=context.token,
            filename=source.filename,
            size=source.byte_length,
        )
        with source.open() as stream:
            _ = server.source_imports.receive(
                str(operation["operationId"]), stream, source.byte_length
            )
        assert terminal(server.source_imports, operation)["status"] == "accepted"
        _ = server.graph.replace(
            Recipe.model_validate(template.snapshot()["recipe"]),
            str(server.graph.snapshot()["token"]),
        )
        future = server.submit(evaluate, server.graph)
        try:
            assert all(event.wait(10) for event in entered)
        finally:
            release.set()
        result = future.result(timeout=10)
        assert result["states"]["f0"] == result["states"]["f1"] == "ready"
        executor = server.graph.fit_executor
        assert executor is server.fit_worker
        assert executor is not None
    with pytest.raises(RuntimeError, match="shutdown"):
        _ = executor.submit(lambda: None)


def test_cancellation_reaches_running_job_and_skips_queued_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release, cancel = Event(), Event(), Event()
    second_entered = Event()

    def gated(points: np.ndarray, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if points[:, 0].mean() > 5:
            second_entered.set()
        entered.set()
        assert release.wait(10)
        assert kwargs["cancelled"]()
        return fit_seed(points, *args, **kwargs)

    monkeypatch.setattr(graph_module, "fit_seed", gated)
    with (
        ThreadPoolExecutor(max_workers=1) as numerical,
        ThreadPoolExecutor(max_workers=1) as coordinator,
    ):
        graph = setup_graph(numerical)
        future = coordinator.submit(
            graph.evaluate,
            str(graph.snapshot()["token"]),
            all_actions=True,
            cancelled=cancel.is_set,
        )
        try:
            assert entered.wait(10)
            cancel.set()
        finally:
            release.set()
        with pytest.raises(ScanCancelled):
            _ = future.result(timeout=10)
    assert not second_entered.is_set()
    result = snapshot(graph)
    assert not result["errors"]
    assert "f0" not in result["results"] and "f1" not in result["results"]
