"""Reuse preview and application through the real local HTTP authoring adapter."""

import json
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from experiments.feature_graph import Recipe
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.nozzle_browser import NozzleServer
from experiments.nozzle_session import NozzleWorkspace


@pytest.fixture
def server() -> Iterator[tuple[NozzleServer, str]]:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    original = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_bytes()
    )
    source = original.nodes[0].model_dump()
    selection = next(
        node for node in original.nodes if node.id == "outer_band"
    ).model_dump()
    nodes = [source]
    for key in ("a", "b", "c"):
        nodes.append({**selection, "id": key, "label": key})
    nodes.append(
        {
            "id": "outer",
            "label": "Outer",
            "operation": "fit",
            "kind": "cylinder",
            "selections": ["a"],
        }
    )
    recipe = Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": nodes,
            "groups": [{"id": "group", "label": "Group"}],
            "output": "outer",
        }
    )
    with NozzleServer(workspace, recipe=recipe) as application:
        worker = Thread(target=application.serve_forever, daemon=True)
        worker.start()
        try:
            yield application, f"http://127.0.0.1:{application.server_port}"
        finally:
            application.shutdown()
            worker.join(timeout=5)


def _post(
    base: str, operation: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    request = Request(
        base + "/api/graph/feature-reuse/" + operation,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, cast(dict[str, Any], json.load(response))
    except HTTPError as error:
        return error.code, cast(dict[str, Any], json.load(error))


def _creation(application: NozzleServer) -> dict[str, Any]:
    return {
        "token": application.graph.snapshot()["token"],
        "reuse_id": "reuse",
        "allocation_seed": "http-create",
        "create": True,
        "changes": {
            "label": "Reuse",
            "group_id": "group",
            "fits": ["outer"],
            "reference_selection": "a",
            "target_selections": ["b"],
        },
    }


def test_http_preview_matches_python_and_apply_without_mutating_the_graph(
    server: tuple[NozzleServer, str],
) -> None:
    application, base = server
    before = application.graph.snapshot()
    request = _creation(application)
    recipe = Recipe.model_validate(before["recipe"])
    expected = reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges.model_validate(request["changes"]),
        "http-create",
        create=True,
    )
    code, preview = _post(base, "preview", request)
    assert code == 200, preview
    assert application.graph.snapshot() == before
    assert preview["token"] == before["token"]
    assert Recipe.model_validate(preview["recipe"]) == expected.recipe
    assert preview["generated_ids"] == list(expected.generated_ids)
    assert preview["removed_ids"] == []
    assert preview["selected_id"] == expected.selected_id
    code, state = _post(base, "apply", request)
    assert code == 200, state
    assert state["recipe"] == preview["recipe"]
    assert state["recipe"]["output"] == preview["selected_id"]
    assert state["token"] != before["token"]
    assert state["revision"] > before["revision"]
    assert state["reuse_authoring"] == {
        key: preview[key] for key in ("generated_ids", "removed_ids", "selected_id")
    }
    applied = application.graph.snapshot()
    for operation in ("preview", "apply"):
        code, error = _post(base, operation, request)
        assert code == 409, error
        assert application.graph.snapshot() == applied


def test_http_edit_preserves_output_groups_and_stable_ids_then_falls_back_when_removed(
    server: tuple[NozzleServer, str],
) -> None:
    application, base = server
    code, created = _post(base, "apply", _creation(application))
    assert code == 200, created
    old_fit = created["recipe"]["output"]
    request = {
        "token": created["token"],
        "reuse_id": "reuse",
        "allocation_seed": "http-edit",
        "changes": {
            "group_id": None,
            "target_selections": ["b", "c"],
            "equal_corresponding_dimensions": True,
        },
    }
    code, preview = _post(base, "preview", request)
    assert code == 200, preview
    code, changed = _post(base, "apply", request)
    assert code == 200, changed
    assert changed["recipe"] == preview["recipe"]
    assert changed["recipe"]["output"] == old_fit
    assert (
        next(node for node in changed["recipe"]["nodes"] if node["id"] == "reuse")[
            "group_id"
        ]
        is None
    )
    equality = next(
        node
        for node in changed["recipe"]["nodes"]
        if node.get("managed_key") == "equal-radius/outer"
    )
    assert equality["surfaces"][0] == "outer"
    assert len(equality["surfaces"]) == 3
    code, reduced = _post(
        base,
        "apply",
        {
            "token": changed["token"],
            "reuse_id": "reuse",
            "allocation_seed": "http-reduce",
            "changes": {
                "target_selections": ["c"],
                "equal_corresponding_dimensions": False,
            },
        },
    )
    assert code == 200, reduced
    assert old_fit in reduced["reuse_authoring"]["removed_ids"]
    assert equality["id"] in reduced["reuse_authoring"]["removed_ids"]
    assert reduced["recipe"]["output"] == "reuse"


def test_http_external_consumer_and_invalid_requests_leave_state_unchanged(
    server: tuple[NozzleServer, str],
) -> None:
    application, base = server
    code, created = _post(base, "apply", _creation(application))
    assert code == 200, created
    payload = created["recipe"]
    payload["nodes"].append(
        {
            "id": "external",
            "label": "External datum",
            "operation": "axis",
            "source_fit": payload["output"],
        }
    )
    state = application.graph.replace(Recipe.model_validate(payload), created["token"])
    before = application.graph.snapshot()
    for operation in ("preview", "apply"):
        code, error = _post(
            base,
            operation,
            {
                "token": state["token"],
                "reuse_id": "reuse",
                "allocation_seed": "blocked",
                "changes": {"target_selections": ["c"]},
            },
        )
        assert code == 422, error
        assert "External datum" in error["error"]
        assert application.graph.snapshot() == before
        for changes in (
            {"target_selections": ["a"]},
            {"fits": ["missing"]},
            {"operation": "fit"},
        ):
            code, error = _post(
                base,
                operation,
                {
                    "token": state["token"],
                    "reuse_id": "reuse",
                    "allocation_seed": "invalid",
                    "changes": changes,
                },
            )
            assert code == 422, error
            assert application.graph.snapshot() == before


def test_http_recipe_authoring_remains_available_during_active_evaluation(
    server: tuple[NozzleServer, str],
) -> None:
    application, base = server
    pending: Future[dict[str, object]] = Future()
    application.graph_job = pending
    try:
        code, state = _post(base, "apply", _creation(application))
        assert code == 200, state
        assert any(node["id"] == "reuse" for node in state["recipe"]["nodes"])
    finally:
        pending.set_result({})
