"""Capability discovery and committed references through the real local server."""

import json
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from experiments.feature_graph import Recipe, Selection
from experiments.nozzle_browser import Handler, NozzleServer
from experiments.nozzle_session import NozzleWorkspace


@pytest.fixture
def input_server() -> Iterator[tuple[NozzleServer, str]]:
    example = Path("examples/nozzle-bayonette-simplified")
    original = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_bytes()
    )
    source = original.nodes[0].model_dump()
    selection = next(
        node
        for node in original.nodes
        if isinstance(node, Selection) and node.id == "outer_band"
    )
    recipe = Recipe.model_validate(
        {
            "schema_version": 2,
            "nodes": [
                source,
                {
                    **selection.model_dump(),
                    "id": "single",
                    "label": "Single",
                    "ids": selection.ids[:1],
                },
                {**selection.model_dump(), "id": "samples", "label": "Samples"},
                {
                    "id": "sphere",
                    "label": "Sphere",
                    "operation": "fit",
                    "kind": "sphere",
                    "selections": ["samples"],
                },
                {
                    "id": "plane",
                    "label": "Plane",
                    "operation": "fit",
                    "kind": "plane",
                    "selections": ["samples"],
                },
                {
                    "id": "origin",
                    "label": "Origin",
                    "operation": "point",
                    "initial_coordinates": [0, 0, 0],
                },
                {
                    "id": "axis",
                    "label": "Axis",
                    "operation": "axis",
                    "initial_parameters": [0, 0, 0, 0],
                },
            ],
            "output": "origin",
        }
    )
    with NozzleServer(NozzleWorkspace(example), recipe=recipe) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield server, f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            worker.join(timeout=5)


def _request(
    base: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    request = Request(
        base + "/api/graph",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, cast(dict[str, Any], json.load(response))
    except HTTPError as error:
        return error.code, cast(dict[str, Any], json.load(error))


def test_snapshot_discovers_named_outputs_without_evaluation(
    input_server: tuple[NozzleServer, str],
) -> None:
    server, base = input_server
    before = server.graph.snapshot()
    code, state = _request(base)
    assert code == 200
    assert state["token"] == before["token"]
    assert state["states"] == before["states"]
    assert server.graph_job is None
    outputs = {
        (entry["reference"]["feature"], entry["reference"]["output"]): entry
        for entry in state["input_catalogue"]
    }
    assert outputs["single", "single_node"]["capability"] == "point"
    assert outputs["sphere", "center"]["capability"] == "point"
    assert outputs["origin", "point"]["capability"] == "point"
    assert outputs["plane", "plane"]["capability"] == "plane"
    assert outputs["samples", "single_node"]["reason"]
    assert set(state["input_requirements"]) >= {
        "point",
        "direction",
        "constrainable_plane",
    }
    assert Handler.files["/feature-inputs.js"] == (
        "feature-inputs.js",
        "text/javascript",
    )


def test_committed_output_identity_round_trips_and_invalid_edits_are_atomic(
    input_server: tuple[NozzleServer, str],
) -> None:
    server, base = input_server
    _, before = _request(base)
    recipe = before["recipe"]

    def reference(feature: str, output: str) -> dict[str, str]:
        return {"feature": feature, "output": output, "context": feature}

    recipe["nodes"].append(
        {
            "id": "frame",
            "label": "Frame",
            "operation": "frame",
            "origin_point": reference("sphere", "center"),
            "primary_reference": reference("plane", "plane"),
            "primary_output_axis": "+Z",
            "secondary_reference": reference("axis", "axis"),
            "secondary_output_axis": "+X",
        }
    )
    recipe["output"] = "frame"
    code, committed = _request(base, {"token": before["token"], "recipe": recipe})
    assert code == 200, committed
    assert committed["recipe"]["nodes"][-1]["origin_point"] == reference(
        "sphere", "center"
    )
    snapshot = server.graph.snapshot()
    bad = json.loads(json.dumps(committed["recipe"]))
    bad["nodes"][-1]["origin_point"] = reference("plane", "center")
    code, _ = _request(base, {"token": committed["token"], "recipe": bad})
    assert code == 422
    assert server.graph.snapshot() == snapshot
    code, _ = _request(base, {"token": before["token"], "recipe": recipe})
    assert code == 409
