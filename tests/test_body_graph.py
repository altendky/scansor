"""Solid assembly runs only after face reviews and shares graph readiness."""

import json
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pytest

from experiments.feature_graph import Body, FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_browser import NozzleServer
from tests.test_arranged_face_workflow import graph_with_diagonal


def snapshot(graph: FeatureGraph) -> dict[str, Any]:
    return cast(dict[str, Any], graph.snapshot())


def face_graph(*, owner: bool = False) -> FeatureGraph:
    initial = graph_with_diagonal()
    payload = snapshot(initial)["recipe"]
    edges: list[dict[str, str]] = []
    for neighbor, keep in (
        ("left", "positive"),
        ("right", "negative"),
        ("bottom", "positive"),
        ("top", "negative"),
    ):
        edge_id = f"edge_{neighbor}"
        payload["nodes"].append(
            {
                "id": edge_id,
                "label": f"Edge {neighbor}",
                "operation": "surface_intersection",
                "first": {"feature": "plate"},
                "second": {"feature": neighbor},
            }
        )
        edges.append({"intersection": edge_id, "keep": keep})
    if owner:
        payload["nodes"].append(
            {
                "id": "ceiling",
                "label": "Ceiling",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 1,
            }
        )
        wall_edges: list[dict[str, str]] = []
        for neighbor, keep in (
            ("plate", "positive"),
            ("ceiling", "negative"),
            ("bottom", "positive"),
            ("top", "negative"),
        ):
            edge_id = f"wall_{neighbor}"
            payload["nodes"].append(
                {
                    "id": edge_id,
                    "label": f"Wall edge {neighbor}",
                    "operation": "surface_intersection",
                    "first": {"feature": "left"},
                    "second": {"feature": neighbor},
                }
            )
            wall_edges.append({"intersection": edge_id, "keep": keep})
        payload["nodes"].extend(
            [
                {
                    "id": "approved_wall",
                    "label": "Approved wall",
                    "operation": "trimmed_face",
                    "surface": {"feature": "left"},
                    "boundaries": wall_edges,
                },
                {
                    "id": "owner",
                    "label": "Reviewed plate",
                    "operation": "build_faces",
                    "surfaces": [{"feature": "plate"}, {"feature": "left"}],
                    "target": {"feature": "plate"},
                    "boundary_sources": ["approved_wall"],
                },
            ]
        )
    payload["nodes"].append(
        {
            "id": "face",
            "label": "Plate face",
            "operation": "trimmed_face",
            "surface": {"feature": "plate"},
            "boundaries": edges,
            **({"managed_by": "owner", "managed_key": "face"} if owner else {}),
        }
    )
    payload["nodes"].append(
        {"id": "body", "label": "Body", "operation": "body", "faces": ["face"]}
    )
    payload["output"] = "body"
    return FeatureGraph(initial.workspace, Recipe.model_validate(payload))


def fake_assembly(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from experiments import body_geometry

    calls: list[dict[str, Any]] = []

    def assemble(faces: dict[str, Any], sewing_tolerance: float) -> dict[str, Any]:
        assert faces["face"]["bounded"]
        calls.append(deepcopy(faces))
        return {
            "kind": "body",
            "valid": True,
            "volume": 1.0,
            "face_ids": list(faces),
            "input_fingerprint": body_geometry.body_input_fingerprint(
                faces, sewing_tolerance
            ),
            "sewing_tolerance": sewing_tolerance,
            "preview": faces["face"]["preview"],
        }

    monkeypatch.setattr(body_geometry, "assemble_body", assemble)
    return calls


@pytest.mark.parametrize("tolerance", [0, -1, float("inf"), float("nan")])
def test_body_requires_explicit_finite_positive_tolerance(tolerance: float) -> None:
    with pytest.raises(ValueError):
        _ = Body(
            id="body",
            label="Body",
            operation="body",
            faces=["face"],
            sewing_tolerance=tolerance,
        )


@pytest.mark.parametrize("faces", [[], ["face", "face"]])
def test_body_requires_nonempty_unique_faces(faces: list[str]) -> None:
    with pytest.raises(ValueError):
        _ = Body(id="body", label="Body", operation="body", faces=faces)


def test_body_rejects_fit_or_context_inputs() -> None:
    graph = face_graph()
    payload = snapshot(graph)["recipe"]
    payload["nodes"][-1]["faces"] = ["plate"]
    with pytest.raises(ValueError, match="physical face"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(payload))


def test_body_evaluation_reuses_current_results_and_invalidates_on_face_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = face_graph()
    calls = fake_assembly(monkeypatch)
    token = snapshot(graph)["token"]
    ready = cast(dict[str, Any], graph.evaluate(token))
    assert ready["states"]["body"] == "ready" and len(calls) == 1
    assert not graph.needs_evaluation(token, ["body"])
    assert graph.ensure_current(token, ["body"]) == ready
    assert graph.evaluate(token) == ready
    assert len(calls) == 1
    payload = deepcopy(ready["recipe"])
    next(node for node in payload["nodes"] if node["id"] == "right")["offset"] = 5
    stale = cast(dict[str, Any], graph.replace(Recipe.model_validate(payload), token))
    assert stale["states"]["body"] == "stale"
    assert "body" not in stale["results"]
    updated = cast(dict[str, Any], graph.ensure_current(stale["token"], ["body"]))
    assert updated["states"]["body"] == "ready" and len(calls) == 2
    assert calls[0] != calls[1]


def test_failed_body_keeps_diagnostics_without_implicit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import body_geometry

    graph = face_graph()
    calls: list[int] = []
    diagnostic = {
        "kind": "body",
        "problems": [{"message": "Open shell", "face_ids": ["face"]}],
    }

    def fail(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        raise body_geometry.BodyAssemblyError(diagnostic)

    monkeypatch.setattr(body_geometry, "assemble_body", fail)
    token = snapshot(graph)["token"]
    with pytest.raises(ValueError, match="Open shell"):
        _ = graph.evaluate(token)
    failed = snapshot(graph)
    assert failed["states"]["body"] == "failed"
    assert failed["diagnostics"]["body"] == diagnostic
    assert "body" not in failed["results"]
    assert not graph.needs_evaluation(token, ["body"])
    settled = graph.ensure_current(token, ["body"])
    assert settled["states"] == failed["states"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="Open shell"):
        _ = graph.evaluate(token)
    assert len(calls) == 2


def test_body_waits_for_owner_aggregate_review(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = face_graph(owner=True)
    calls = fake_assembly(monkeypatch)
    reviews: list[bool] = []

    def review(*args: Any) -> dict[str, Any]:
        assert not calls
        reviews.append(args[-1])
        return {"complete": True}

    monkeypatch.setattr(graph, "_shared_boundary_review", review)
    ready = cast(dict[str, Any], graph.evaluate(snapshot(graph)["token"]))
    assert reviews == [True]
    assert ready["states"]["body"] == "ready" and len(calls) == 1
    assert ready["results"]["owner"]["shared_boundary_review"]["complete"]


def test_owner_review_failure_blocks_body_even_when_previously_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = face_graph(owner=True)
    calls = fake_assembly(monkeypatch)

    def successful_review(*_args: Any) -> dict[str, Any]:
        return {"complete": True}

    monkeypatch.setattr(graph, "_shared_boundary_review", successful_review)
    ready = cast(dict[str, Any], graph.evaluate(snapshot(graph)["token"]))
    assert len(calls) == 1

    def fail_review(*_args: Any) -> dict[str, Any]:
        raise ValueError("Missing approved boundary")

    monkeypatch.setattr(graph, "_shared_boundary_review", fail_review)
    with pytest.raises(ValueError, match="Missing approved boundary"):
        _ = graph.evaluate(ready["token"])
    failed = snapshot(graph)
    assert failed["states"]["owner"] == "failed"
    assert failed["states"]["body"] == "blocked"
    assert "owner" in failed["diagnostics"]["body"]["blocked_by"]
    assert "body" not in failed["results"]
    assert len(calls) == 1


def test_owner_sibling_edits_invalidate_body_of_other_managed_face(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = face_graph(owner=True)
    _ = fake_assembly(monkeypatch)

    def review(*_args: Any) -> dict[str, Any]:
        return {"complete": True}

    monkeypatch.setattr(graph, "_shared_boundary_review", review)
    initial = snapshot(graph)
    payload = deepcopy(initial["recipe"])
    sibling = deepcopy(next(node for node in payload["nodes"] if node["id"] == "face"))
    sibling.update(id="sibling", label="Sibling face", managed_key="sibling")
    payload["nodes"].insert(-1, sibling)
    _ = graph.replace(Recipe.model_validate(payload), initial["token"])
    ready = cast(dict[str, Any], graph.evaluate(snapshot(graph)["token"]))
    assert ready["states"]["sibling"] == "ready"
    payload = deepcopy(ready["recipe"])
    next(node for node in payload["nodes"] if node["id"] == "sibling")["label"] = (
        "Edited sibling"
    )
    stale = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), ready["token"])
    )
    assert stale["states"]["owner"] == "stale"
    assert stale["states"]["face"] == "stale"
    assert stale["states"]["body"] == "stale"
    assert "body" not in stale["results"]


def test_stale_body_native_work_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    from experiments import body_geometry

    graph = face_graph()
    token = snapshot(graph)["token"]

    def replace_while_assembling(*_args: Any) -> dict[str, Any]:
        payload = snapshot(graph)["recipe"]
        payload["nodes"][-1]["sewing_tolerance"] = 1e-6
        _ = graph.replace(Recipe.model_validate(payload), token)
        return {"kind": "body", "valid": True}

    monkeypatch.setattr(body_geometry, "assemble_body", replace_while_assembling)
    with pytest.raises(StaleGraph):
        _ = graph.evaluate(token)
    state = snapshot(graph)
    assert state["states"]["body"] == "stale"
    assert "body" not in state["results"]


def _box_graph() -> FeatureGraph:
    initial = graph_with_diagonal()
    payload = snapshot(initial)["recipe"]
    payload["nodes"] = [node for node in payload["nodes"] if node["id"] != "diagonal"]
    payload["nodes"].append(
        {
            "id": "ceiling",
            "label": "Ceiling",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 1,
        }
    )
    pairs = [("plate", "ceiling"), ("left", "right"), ("bottom", "top")]
    surfaces = [surface for pair in pairs for surface in pair]
    for first, second in combinations(surfaces, 2):
        if (first, second) in pairs:
            continue
        edge_id = f"edge_{first}_{second}"
        payload["nodes"].append(
            {
                "id": edge_id,
                "label": f"Edge {first} {second}",
                "operation": "surface_intersection",
                "first": {"feature": first},
                "second": {"feature": second},
            }
        )
    faces: list[str] = []
    for surface in surfaces:
        face_id = f"face_{surface}"
        faces.append(face_id)
        boundaries: list[dict[str, Any]] = []
        for edge in payload["nodes"]:
            if edge["operation"] != "surface_intersection":
                continue
            refs = [edge["first"]["feature"], edge["second"]["feature"]]
            if surface not in refs:
                continue
            other = next(ref for ref in refs if ref != surface)
            boundaries.append(
                {
                    "intersection": edge["id"],
                    "keep": "positive"
                    if other in ("plate", "left", "bottom")
                    else "negative",
                }
            )
        payload["nodes"].append(
            {
                "id": face_id,
                "label": f"Face {surface}",
                "operation": "trimmed_face",
                "surface": {"feature": surface},
                "boundaries": boundaries,
            }
        )
    payload["nodes"].append(
        {
            "id": "body",
            "label": "Box",
            "operation": "body",
            "faces": faces,
            # These reconstructed planar faces carry their existing OCCT
            # tolerances; this is a declared join budget, not automatic healing.
            "sewing_tolerance": 1e-5,
        }
    )
    payload["output"] = "body"
    return FeatureGraph(initial.workspace, Recipe.model_validate(payload))


def test_real_box_body_replays_as_one_valid_solid() -> None:
    from experiments.body_geometry import body_input_fingerprint

    graph = _box_graph()
    ready = cast(dict[str, Any], graph.evaluate(snapshot(graph)["token"]))
    body = ready["results"]["body"]
    faces = next(node for node in ready["recipe"]["nodes"] if node["id"] == "body")[
        "faces"
    ]
    assert body["valid"] and body["volume"] == pytest.approx(48)
    assert body["face_ids"] == faces
    assert "input_faces" not in body
    assert body["input_fingerprint"] == body_input_fingerprint(
        {face_id: ready["results"][face_id] for face_id in faces}, 1e-5
    )
    assert body["face_count"] == 6 and body["preview"]["indices"]
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(ready["recipe"]))
    assert replay.evaluate(snapshot(replay)["token"])["results"] == ready["results"]


def test_ready_body_http_ensure_and_solid_export_do_not_evaluate_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from experiments import body_geometry

    graph = _box_graph()
    ready = cast(dict[str, Any], graph.evaluate(snapshot(graph)["token"]))

    def unexpected_evaluation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("ready Body must not evaluate again")

    monkeypatch.setattr(graph, "evaluate", unexpected_evaluation)
    monkeypatch.setattr(body_geometry, "assemble_body", unexpected_evaluation)
    with NozzleServer(
        graph.workspace, recipe=Recipe.model_validate(ready["recipe"])
    ) as server:
        server.graph = graph
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def post(path: str, payload: dict[str, Any]) -> bytes:
            request = Request(
                f"http://127.0.0.1:{server.server_port}{path}",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
            )
            with urlopen(request, timeout=30) as response:
                assert response.status == 200
                if path == "/api/export/cad":
                    assert response.headers.get_content_type() == "application/zip"
                return response.read()

        try:
            ensured = json.loads(
                post(
                    "/api/graph/ensure", {"token": ready["token"], "targets": ["body"]}
                )
            )
            assert not ensured["evaluation_running"]
            assert ensured["states"]["body"] == "ready"
            exported = post(
                "/api/export/cad",
                {
                    "token": ready["token"],
                    "scope": "body",
                    "target": "body",
                    "units": "Millimeters",
                    "axis_up": False,
                    "include_mesh": False,
                },
            )
            with ZipFile(BytesIO(exported)) as bundle:
                assert set(bundle.namelist()) == {"model.step", "metadata.json"}
                metadata = json.loads(bundle.read("metadata.json"))
                assert metadata["geometry_mode"] == "solid"
                assert metadata["solid"] and metadata["sewn"]
                assert metadata["objects"][0]["name"] == "Box"
                assert (
                    metadata["objects"][0]["source_faces"]
                    == ready["results"]["body"]["face_ids"]
                )
                assert metadata["objects"][0]["volume_local"] == pytest.approx(48)
                assert b"MANIFOLD_SOLID_BREP" in bundle.read("model.step")
            assert snapshot(graph) == ready
            assert server.graph_job is None
        finally:
            server.shutdown()
            worker.join(timeout=10)


def test_failed_open_body_http_export_reports_its_name() -> None:
    graph = _box_graph()
    initial = snapshot(graph)
    payload = deepcopy(initial["recipe"])
    body = next(node for node in payload["nodes"] if node["id"] == "body")
    body["faces"].remove("face_ceiling")
    _ = graph.replace(Recipe.model_validate(payload), initial["token"])
    token = snapshot(graph)["token"]
    with pytest.raises(ValueError):
        _ = graph.evaluate(token)
    failed = snapshot(graph)
    assert failed["states"]["body"] == "failed"
    with NozzleServer(
        graph.workspace, recipe=Recipe.model_validate(failed["recipe"])
    ) as server:
        server.graph = graph
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/export/cad",
                data=json.dumps(
                    {
                        "token": token,
                        "scope": "body",
                        "target": "body",
                        "units": "Millimeters",
                        "axis_up": False,
                        "include_mesh": False,
                    }
                ).encode(),
                headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
            )
            with pytest.raises(HTTPError) as caught:
                _ = urlopen(request, timeout=30)
            assert caught.value.code == 422
            response = json.load(caught.value)
            assert "cannot export Body" in response["error"]
            assert "Box" in response["error"]
            assert failed["errors"]["body"] in response["error"]
        finally:
            server.shutdown()
            worker.join(timeout=10)
