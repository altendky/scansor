"""Choice discovery is a fenced read which cannot monopolize project editing."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast

import numpy as np
import pytest

from experiments import nozzle_browser as native
from experiments import selection_volume_operations as operations
from experiments.feature_graph import FeatureGraph, Recipe, Selection
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import ScanCancelled, ScanImportError, ScanWorkspace
from experiments.selection_volume_operations import ChoicesRequest
from tests.test_direction_graph import evaluated, graph_with
from tests.test_direction_graph import fixture as fixture
from tests.test_native_scan_http import NativeHttp
from tests.test_relationship_collections import grouped, pattern
from tests.test_relationship_property_outputs import downstream, planes, upstream
from tests.test_selection_volume_transactions import graph as small_graph

CHOICES = "/api/graph/selection-volumes/choices"
CANCEL = CHOICES + "/cancel"


@pytest.fixture(scope="module")
def nozzle_workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


@pytest.fixture
def choice_http(
    nozzle_workspace: NozzleWorkspace, tmp_path: Path
) -> Iterator[NativeHttp]:
    ids = np.flatnonzero(nozzle_workspace.data.weights > 0)[:8].tolist()
    recipe = Recipe.model_validate(
        {
            "nodes": [
                {
                    "id": "scan",
                    "operation": "source",
                    "label": "Scan",
                    "source_sha256": nozzle_workspace.source_sha256,
                    "reference_sha256": nozzle_workspace.reference_sha256,
                },
                {
                    "id": "patch",
                    "operation": "selection",
                    "label": "Painted patch",
                    "source": "scan",
                    "ids": ids,
                },
                {
                    "id": "plane",
                    "operation": "fit",
                    "label": "Plane",
                    "kind": "plane",
                    "selections": ["patch"],
                },
            ],
            "output": "plane",
        }
    )
    with native.NozzleServer(
        nozzle_workspace, recipe=recipe, staging_directory=tmp_path
    ) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield NativeHttp(server, tmp_path)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


def suspend_choices(monkeypatch: pytest.MonkeyPatch) -> tuple[Event, Event]:
    entered, release = Event(), Event()
    original = operations.compute_choices

    def wait(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        assert release.wait(10), "test did not release its private query"
        return original(*args, **kwargs)

    monkeypatch.setattr(native, "compute_choices", wait)
    return entered, release


def active_queries(server: native.NozzleServer) -> dict[str, native.NativeChoiceQuery]:
    return cast(dict[str, native.NativeChoiceQuery], vars(server)["_choice_queries"])


def start_query(
    http: NativeHttp,
    pool: ThreadPoolExecutor,
    cursor: dict[str, Any],
    query_id: str = "choices_test",
) -> Any:
    return pool.submit(
        http.request,
        CHOICES,
        {"token": cursor["token"], "selection": "patch"},
        generation=cursor["generation"],
        headers={"X-Scansor-Query-Id": query_id},
    )


def selection_edit(http: NativeHttp, cursor: dict[str, Any]) -> tuple[int, Any, Any]:
    recipe = http.server.graph.export_recipe(cursor["token"])
    recipe["nodes"][1]["ids"] = recipe["nodes"][1]["ids"][:-1]
    return http.request(
        "/api/graph",
        {"token": cursor["token"], "recipe": recipe},
        generation=cursor["generation"],
    )


def test_suspended_choices_allow_poll_cancel_and_selection_save(
    choice_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = suspend_choices(monkeypatch)
    cursor = choice_http.cursor()
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = start_query(choice_http, pool, cursor)
        try:
            assert entered.wait(2)
            assert not choice_http.server.busy()
            assert pool.submit(choice_http.request, "/api/graph").result(2)[0] == 200
            cancelled = pool.submit(
                choice_http.request,
                CANCEL,
                {"token": cursor["token"], "query_id": "choices_test"},
                generation=cursor["generation"],
            ).result(2)
            assert cancelled[0] == 200
            assert cancelled[1]["cancel_requested"] is True
            saved = pool.submit(selection_edit, choice_http, cursor).result(2)
            assert saved[0] == 200
            assert saved[1]["token"] != cursor["token"]
            assert not pending.done()  # All three operations finished before this read.
        finally:
            release.set()
        status, response, _ = pending.result(3)
        assert status == 409 and response["code"] == "choice_query_cancelled"
    assert not active_queries(choice_http.server)


@pytest.mark.parametrize("change", ["recipe", "publication", "reset", "source"])
def test_concurrent_changes_fence_read_results(
    choice_http: NativeHttp, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    entered, release = suspend_choices(monkeypatch)
    cursor = choice_http.cursor()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = start_query(choice_http, pool, cursor)
        try:
            assert entered.wait(2)
            if change == "recipe":
                assert (
                    pool.submit(selection_edit, choice_http, cursor).result(2)[0] == 200
                )
            elif change == "publication":
                state = choice_http.server.graph.evaluate(
                    cursor["token"], target="patch"
                )
                assert state["token"] == cursor["token"]
            elif change == "reset":
                assert (
                    pool.submit(
                        choice_http.request,
                        "/api/project/reset",
                        {**cursor, "confirmedDiscard": True},
                        generation=cursor["generation"],
                    ).result(2)[0]
                    == 200
                )
            else:
                assert (
                    pool.submit(
                        choice_http.request,
                        "/api/examples/select",
                        {"token": cursor["token"], "example": "nozzle"},
                        generation=cursor["generation"],
                    ).result(4)[0]
                    == 200
                )
        finally:
            release.set()
        status, response, _ = pending.result(3)
        assert status == 409
        assert response["code"] == (
            "stale_source" if change in {"reset", "source"} else "stale_graph"
        )
    assert not active_queries(choice_http.server)


def test_cancel_before_registration_and_mismatched_cursor(
    choice_http: NativeHttp,
) -> None:
    cursor = choice_http.cursor()
    payload = {"token": cursor["token"], "query_id": "choices_early"}
    for _ in range(2):
        status, response, _ = choice_http.request(
            CANCEL, payload, generation=cursor["generation"]
        )
        assert status == 200 and response["cancel_requested"] is True
    status, response, _ = choice_http.request(
        CHOICES,
        {"token": cursor["token"], "selection": "patch"},
        generation=cursor["generation"],
        headers={"X-Scansor-Query-Id": payload["query_id"]},
    )
    assert status == 409 and response["code"] == "choice_query_cancelled"
    assert not active_queries(choice_http.server)
    status, response, _ = choice_http.request(
        CANCEL, {**payload, "token": "another"}, generation=cursor["generation"]
    )
    assert status == 409 and response["code"] == "stale_graph"


def test_active_query_id_cannot_be_replaced_or_cancelled_by_other_cursor(
    choice_http: NativeHttp, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = suspend_choices(monkeypatch)
    cursor = choice_http.cursor()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = start_query(choice_http, pool, cursor)
        try:
            assert entered.wait(2)
            duplicate = pool.submit(
                choice_http.request,
                CHOICES,
                {"token": cursor["token"], "selection": "patch"},
                generation=cursor["generation"],
                headers={"X-Scansor-Query-Id": "choices_test"},
            ).result(2)
            assert duplicate[0] == 409 and duplicate[1]["code"] == "stale_graph"
            cancelled = pool.submit(
                choice_http.request,
                CANCEL,
                {"token": "another", "query_id": "choices_test"},
                generation=cursor["generation"],
            ).result(2)
            assert cancelled[0] == 409
            assert not active_queries(choice_http.server)[
                "choices_test"
            ].cancel.is_set()
        finally:
            release.set()
        assert pending.result(3)[0] == 200
    assert not active_queries(choice_http.server)


def test_registry_and_early_cancel_history_are_bounded(choice_http: NativeHttp) -> None:
    cursor = choice_http.cursor()
    accepted = choice_http.server.accepted()
    request = ChoicesRequest(token=cursor["token"], selection="patch")
    records = [
        choice_http.server.prepare_choice_query(accepted, request, f"choices_{index}")
        for index in range(native.MAX_CHOICE_QUERIES)
    ]
    try:
        status, response, _ = choice_http.request(
            CHOICES,
            request.model_dump(),
            generation=cursor["generation"],
            headers={"X-Scansor-Query-Id": "choices_overflow"},
        )
        assert status == 429 and response["code"] == "choice_query_busy"
        for index in range(native.MAX_CHOICE_CANCELLATIONS + 1):
            assert (
                choice_http.request(
                    CANCEL,
                    {"token": cursor["token"], "query_id": f"early_{index}"},
                    generation=cursor["generation"],
                )[0]
                == 200
            )
        history = cast(
            dict[str, Any], vars(choice_http.server)["_choice_cancellations"]
        )
        assert len(history) == native.MAX_CHOICE_CANCELLATIONS
    finally:
        for record in records:
            record.cancel.set()
            with pytest.raises(ScanImportError, match="query cancelled"):
                _ = choice_http.server.run_choice_query(record)
    assert not active_queries(choice_http.server)


def test_server_close_cancels_registered_reads(
    nozzle_workspace: NozzleWorkspace, tmp_path: Path
) -> None:
    with native.NozzleServer(nozzle_workspace, staging_directory=tmp_path) as server:
        accepted = server.accepted()
        selection = next(
            node.id
            for node in server.example_recipe.nodes
            if isinstance(node, Selection)
        )
        query = server.prepare_choice_query(
            accepted,
            ChoicesRequest(
                token=str(accepted.graph.snapshot()["token"]), selection=selection
            ),
            "choices_close",
        )
    assert query.cancel.is_set()
    with pytest.raises(ScanImportError, match="query cancelled"):
        _ = server.run_choice_query(query)
    assert not active_queries(server)


@pytest.mark.parametrize("query_id", ["", "bad id", "x" * 65])
def test_query_ids_are_bounded(choice_http: NativeHttp, query_id: str) -> None:
    cursor = choice_http.cursor()
    assert (
        choice_http.request(
            CHOICES,
            {"token": cursor["token"], "selection": "patch"},
            generation=cursor["generation"],
            headers={"X-Scansor-Query-Id": query_id},
        )[0]
        == 422
    )
    assert not active_queries(choice_http.server)


def test_choice_metadata_does_not_read_source_buffers_or_build_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = small_graph()
    request = ChoicesRequest(token=str(graph.snapshot()["token"]), selection="patch")
    inputs = operations.prepare_choices(graph, request)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "metadata-only choice discovery accessed live source/results"
        )

    monkeypatch.setattr(ScanWorkspace, "local", property(forbidden))
    monkeypatch.setattr(graph, "snapshot", forbidden)
    result = operations.compute_choices(inputs, request)
    assert result["surfaces"][0]["eligible"]
    graph.assert_read_current(inputs.token, inputs.revision)


def test_choices_support_cooperative_cancellation_without_mutation() -> None:
    graph = small_graph()
    before = graph.snapshot()
    with pytest.raises(ScanCancelled):
        _ = operations.dispatch(
            graph,
            "getSelectionVolumeChoices",
            {"token": str(before["token"]), "selection": "patch"},
            cancelled=lambda: True,
        )
    assert graph.snapshot() == before


def assert_catalogue_parity(
    graph: FeatureGraph[Any], selection: str, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    state = graph.snapshot()
    assert not state["errors"]
    request = ChoicesRequest(token=str(state["token"]), selection=selection)
    inputs = operations.prepare_choices(graph, request)
    actual = operations.compute_choices(inputs, request)

    def published_catalogue(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], state["input_catalogue"])

    with monkeypatch.context() as patch:
        patch.setattr(operations, "output_catalogue", published_catalogue)
        expected = operations.compute_choices(inputs, request)
    assert actual == expected
    return actual


@pytest.mark.parametrize("use", ["binding", "geometry"])
def test_ready_named_context_choices_match_full_publication(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    use: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planes(fixture)
    graph = graph_with(
        fixture,
        upstream(),
        downstream(use),
        {"id": "joint", "operation": "joint_fit", "constraints": ["downstream"]},
    )
    assert not evaluated(graph, "joint")["errors"]
    result = assert_catalogue_parity(graph, "left_selection", monkeypatch)
    assert any(item["reference"]["feature"] == "joint" for item in result["surfaces"])


def test_ready_reuse_source_choices_match_full_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, recipe, _ = pattern(upstream=True, equal=True)
    graph = FeatureGraph(workspace, grouped(recipe))
    assert not evaluated(graph, "alignment")["errors"]
    result = assert_catalogue_parity(graph, "bore_input", monkeypatch)
    assert any(item["reference"]["feature"] == "pattern" for item in result["surfaces"])
