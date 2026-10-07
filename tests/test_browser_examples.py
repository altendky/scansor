"""Switching local examples replaces both mesh and editable recipe together."""

import json
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from experiments import browser_examples, nozzle_browser
from experiments.feature_graph import Recipe
from experiments.nozzle_browser import (
    NozzleServer,
    default_example_recipe,
    selection_bundle_recipe,
)
from experiments.nozzle_session import NozzleWorkspace, SessionFit


@pytest.fixture
def example_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[NozzleServer, str]]:
    monkeypatch.setattr(browser_examples, "REPEATED_BOSS_OUTPUT", tmp_path / "boss")
    directory = browser_examples.NOZZLE
    workspace = NozzleWorkspace(directory)
    with NozzleServer(workspace, example_path=directory) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server, f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join(timeout=5)


def request(
    base: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, Any]:
    req = Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
    )
    try:
        with urlopen(req, timeout=10) as response:
            body = response.read()
            return response.status, json.loads(
                body
            ) if response.headers.get_content_type() == "application/json" else body
    except HTTPError as error:
        return error.code, json.load(error)


def test_switches_actual_mesh_and_restore_recipe(
    example_server: tuple[NozzleServer, str],
) -> None:
    server, base = example_server
    _, catalogue = request(base, "/api/examples")
    assert catalogue == {
        "active": "nozzle",
        "examples": list(browser_examples.EXAMPLES),
    }
    _, old_mesh = request(base, "/mesh/positions")
    _, old_meta = request(base, "/api/meta")
    old_graph = server.graph
    old_default = Recipe.model_validate_json(
        browser_examples.example_recipe_path("nozzle").read_bytes()
    ).model_dump(mode="json")
    assert (
        Recipe.model_validate(old_graph.snapshot()["recipe"]).model_dump(mode="json")
        == old_default
    )
    assert len(old_default["nodes"]) == 30
    assert sum(node["operation"] == "fit" for node in old_default["nodes"]) == 11
    finished: Future[dict[str, object]] = Future()
    finished.set_result({})
    server.graph_job = finished
    server.graph_job_errors[("old", frozenset())] = "old error"
    status, result = request(
        base,
        "/api/examples/select",
        {"example": "repeated-boss", "token": old_graph.snapshot()["token"]},
    )
    assert status == 200
    assert result == {"active": "repeated-boss"}
    assert server.graph is not old_graph
    assert server.graph_job is None and not server.graph_job_errors
    _, mesh = request(base, "/mesh/positions")
    _, indices = request(base, "/mesh/indices")
    _, meta = request(base, "/api/meta")
    assert meta["vertices"] == 6292 and meta["triangles"] == 10976
    assert meta["session"]["source_sha256"] != old_meta["session"]["source_sha256"]
    assert mesh != old_mesh and len(mesh) == meta["vertices"] * 12
    assert len(indices) == meta["triangles"] * 12
    _, recipe = request(base, "/api/graph/example")
    _, graph = request(base, "/api/graph")
    assert graph["recipe"] == recipe
    boss_default = Recipe.model_validate_json(
        browser_examples.example_recipe_path("repeated-boss").read_bytes()
    ).model_dump(mode="json")
    assert recipe == boss_default and len(recipe["nodes"]) == 131
    assert {node["operation"] for node in recipe["nodes"]} >= {
        "fit",
        "frame",
        "scale",
        "transform",
        "feature_reuse",
    }
    assert set(graph["states"].values()) == {"unevaluated"}
    assert not graph["results"]
    # Editing and Restore example continue to use the newly selected source.
    edited = Recipe.model_validate(recipe)
    edited = edited.model_copy(
        update={
            "nodes": [
                *edited.nodes[:-1],
                edited.nodes[-1].model_copy(update={"label": "Edited selection"}),
            ]
        }
    )
    status, changed = request(
        base, "/api/graph", {"recipe": edited.model_dump(), "token": graph["token"]}
    )
    assert status == 200
    status, restored = request(
        base, "/api/graph", {"recipe": recipe, "token": changed["token"]}
    )
    assert status == 200 and restored["recipe"] == recipe
    generated_manifest = (
        browser_examples.REPEATED_BOSS_OUTPUT / "scan-coarse/manifest.json"
    )
    generated_bytes = generated_manifest.read_bytes()
    status, _ = request(
        base, "/api/examples/select", {"example": "nozzle", "token": restored["token"]}
    )
    assert status == 200
    assert (
        server.workspace.default.source_sha256 == old_meta["session"]["source_sha256"]
    )
    assert (
        Recipe.model_validate(server.graph.snapshot()["recipe"]).model_dump(mode="json")
        == old_default
    )
    assert server.example_recipe.model_dump(mode="json") == old_default
    # A second visit reuses generated artifacts without overwriting them.
    status, _ = request(
        base,
        "/api/examples/select",
        {"example": "repeated-boss", "token": server.graph.snapshot()["token"]},
    )
    assert status == 200 and generated_manifest.read_bytes() == generated_bytes


def test_rejects_stale_unknown_and_active_work_atomically(
    example_server: tuple[NozzleServer, str],
) -> None:
    server, base = example_server
    before = server.graph.snapshot()
    graph, workspace, buffers, recipe = (
        server.graph,
        server.workspace,
        server.buffers,
        server.example_recipe,
    )
    for payload, code in [
        ({"example": "repeated-boss", "token": "stale"}, 409),
        ({"example": "../../etc", "token": before["token"]}, 422),
        ({"example": "nozzle", "token": before["token"], "path": "/tmp"}, 422),
    ]:
        assert request(base, "/api/examples/select", payload)[0] == code
    pending_graph: Future[dict[str, object]] = Future()
    server.graph_job = pending_graph
    assert (
        request(
            base,
            "/api/examples/select",
            {"example": "repeated-boss", "token": before["token"]},
        )[0]
        == 409
    )
    pending_graph.set_result({})
    pending_fit: Future[SessionFit] = Future()
    server.job = pending_fit
    assert (
        request(
            base,
            "/api/examples/select",
            {"example": "repeated-boss", "token": before["token"]},
        )[0]
        == 409
    )
    _ = pending_fit.cancel()
    assert server.graph is graph and server.workspace is workspace
    assert server.buffers is buffers and server.example_recipe is recipe
    assert server.graph.snapshot() == before
    assert not browser_examples.REPEATED_BOSS_OUTPUT.exists()


def test_loading_failure_preserves_workspace_and_requires_explicit_json(
    example_server: tuple[NozzleServer, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    server, base = example_server
    graph, workspace, recipe, buffers = (
        server.graph,
        server.workspace,
        server.example_recipe,
        server.buffers,
    )
    payload = {"example": "nozzle", "token": graph.snapshot()["token"]}
    implicit = Request(
        base + "/api/examples/select",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(HTTPError) as rejected:
        _ = urlopen(implicit, timeout=10)
    assert rejected.value.code == 403

    def missing_workspace(_path: Path) -> NozzleWorkspace:
        raise FileNotFoundError("fixture disappeared")

    monkeypatch.setattr(nozzle_browser, "NozzleWorkspace", missing_workspace)
    status, result = request(base, "/api/examples/select", payload)
    assert status == 422 and "could not load example" in result["error"]
    assert server.graph is graph and server.workspace is workspace
    assert server.example_recipe is recipe and server.buffers is buffers


def test_custom_workspace_identity_and_incomplete_fixture_are_not_guessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert browser_examples.example_identity(None) is None
    assert browser_examples.example_identity(tmp_path / "custom") is None
    output = tmp_path / "incomplete"
    output.mkdir()
    sentinel = output / "keep.txt"
    _ = sentinel.write_text("local data")
    monkeypatch.setattr(browser_examples, "REPEATED_BOSS_OUTPUT", output)
    with pytest.raises(ValueError, match="incomplete"):
        _ = browser_examples.example_directory("repeated-boss")
    assert sentinel.read_text() == "local data"


def test_explicit_recipe_and_custom_example_keep_existing_cli_behavior(
    tmp_path: Path,
) -> None:
    import shutil

    directory = browser_examples.NOZZLE
    workspace = NozzleWorkspace(directory)
    explicit = selection_bundle_recipe(
        workspace, directory / "selections/user-selection-bundle.json"
    )
    with NozzleServer(workspace, recipe=explicit, example_path=directory) as server:
        assert server.graph.snapshot()["recipe"] == explicit.model_dump()
        assert server.example_recipe == explicit
    custom = tmp_path / "custom"
    _ = shutil.copytree(directory, custom)
    fallback = default_example_recipe(workspace, custom)
    assert fallback == explicit
    with NozzleServer(workspace, example_path=custom) as server:
        assert server.active_example is None
        assert server.graph.snapshot()["recipe"] == explicit.model_dump()


def test_import_names_warns_and_preserves_real_geometry(
    example_server: tuple[NozzleServer, str],
) -> None:
    server, base = example_server
    _, before = request(base, "/api/graph")
    raw = json.loads(json.dumps(before["recipe"]))
    for node in raw["nodes"]:
        if node["label"] in {"outer fit", "recesses fit"}:
            node["label"] = node["label"].removesuffix(" fit")
    # Ordinary property edits keep strict names; only an explicit import repairs.
    assert (
        request(base, "/api/graph", {"token": before["token"], "recipe": raw})[0] == 422
    )
    status, imported = request(
        base, "/api/graph/import", {"token": before["token"], "recipe": raw}
    )
    assert status == 200
    assert {entry["new_name"] for entry in imported["import_warnings"]} == {
        "outer fit",
        "recesses fit",
    }
    assert imported["recipe"] == before["recipe"]
    assert set(imported["states"].values()) == {"unevaluated"}
    outer = next(
        node for node in imported["recipe"]["nodes"] if node["label"] == "outer fit"
    )
    # Target the imported fit independently of the saved bump-fit failure.
    state = cast(
        dict[str, Any], server.graph.evaluate(imported["token"], target=outer["id"])
    )
    assert state["states"][outer["id"]] == "ready"
    assert outer["id"] not in state["errors"]
    # No name warning leaks into later polling or ordinary edits.
    assert "import_warnings" not in request(base, "/api/graph")[1]


@pytest.mark.parametrize(
    "invalid", ["identity", "source", "reference", "order", "stale"]
)
def test_import_failure_keeps_current_graph(
    example_server: tuple[NozzleServer, str], invalid: str
) -> None:
    server, base = example_server
    _, before = request(base, "/api/graph")
    raw = json.loads(json.dumps(before["recipe"]))
    token = before["token"]
    if invalid == "identity":
        raw["nodes"][1]["id"] = raw["nodes"][0]["id"]
    elif invalid == "source":
        raw["nodes"][0]["source_sha256"] = "0" * 64
    elif invalid == "reference":
        raw["nodes"][1]["source"] = "missing"
    elif invalid == "order":
        raw["nodes"][0], raw["nodes"][1] = raw["nodes"][1], raw["nodes"][0]
    else:
        token = "stale"
    status, result = request(base, "/api/graph/import", {"token": token, "recipe": raw})
    assert status == (409 if invalid == "stale" else 422)
    assert "error" in result
    after = server.graph.snapshot()
    assert after["token"] == before["token"]
    assert (
        Recipe.model_validate(after["recipe"]).model_dump(mode="json")
        == before["recipe"]
    )
