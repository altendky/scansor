import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from threading import Thread
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pytest

from experiments.face_building_limits import MAX_PAIRS, MAX_REGIONS
from experiments.feature_graph import Recipe
from experiments.nozzle_browser import Handler, NozzleServer
from experiments.nozzle_session import NozzleWorkspace


def test_fit_only_browser_import_does_not_load_cad_kernel() -> None:
    """Numerical sessions do not pay native CAD startup or memory overhead."""
    _ = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import experiments.nozzle_browser; assert not any(name == 'OCP' or name.startswith('OCP.') for name in sys.modules)",
        ],
        check=True,
    )


def test_workspace_bundle_is_a_served_browser_asset() -> None:
    assert Handler.files["/workspace.js"] == ("dist/workspace.js", "text/javascript")
    assert Handler.files["/workspace.css"] == ("dist/workspace.css", "text/css")
    assert Handler.files["/workspace-state.js"] == (
        "workspace-state.js",
        "text/javascript",
    )


def test_surface_trims_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/surface-trims.js"] == ("surface-trims.js", "text/javascript")


def test_cad_export_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/cad-export.js"] == ("cad-export.js", "text/javascript")


def test_fit_footprint_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/fit-footprint.js"] == ("fit-footprint.js", "text/javascript")


def test_model_picking_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/model-picking.js"] == ("model-picking.js", "text/javascript")


def test_reuse_volume_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/reuse-volume.js"] == (
        "reuse-volume.js",
        "text/javascript",
    )


def test_residual_display_module_is_a_served_browser_asset() -> None:
    assert Handler.files["/residual-display.js"] == (
        "residual-display.js",
        "text/javascript",
    )


def test_face_edge_stroke_modules_are_served_without_cad_imports() -> None:
    assert Handler.files["/edge-highlight.js"] == (
        "edge-highlight.js",
        "text/javascript",
    )
    for name in (
        "Line2",
        "LineGeometry",
        "LineMaterial",
        "LineSegments2",
        "LineSegmentsGeometry",
    ):
        assert Handler.files[f"/vendor/lines/{name}.js"] == (
            f"node_modules/three/examples/jsm/lines/{name}.js",
            "text/javascript",
        )


def test_face_authoring_evaluation_and_export_http_workflow() -> None:
    """Exercise the real adapter in an isolated server, not the user's graph."""
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    recipe = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    )
    with NozzleServer(workspace, recipe=recipe) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"

        def post(path: str, payload: dict[str, Any]) -> bytes:
            request = Request(
                base + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "X-Scansor-Request": "1"},
            )
            with urlopen(request, timeout=30) as response:
                if path == "/api/export/cad":
                    assert response.headers.get_content_type() == "application/zip"
                return response.read()

        try:
            with urlopen(base + "/api/meta", timeout=10) as response:
                limits = json.load(response)["face_building_limits"]
            assert limits == {
                "max_pair_checks": MAX_PAIRS,
                "max_regions": MAX_REGIONS,
            }
            with urlopen(base + "/", timeout=10) as response:
                assert b"new-surface-intersection" in response.read()
            with urlopen(base + "/surface-trims.js", timeout=10) as response:
                assert b"surfaceReferenceChoices" in response.read()
            with urlopen(base + "/fit-footprint.js", timeout=10) as response:
                assert b"fittedSelectionFootprint" in response.read()
            payload = recipe.model_dump()
            plane = {"feature": "fit", "surface": "end"}
            payload["nodes"].extend(
                [
                    {
                        "id": "edge",
                        "label": "Shared edge",
                        "operation": "surface_intersection",
                        "first": plane,
                        "second": {"feature": "fit", "surface": "side"},
                    },
                    {
                        "id": "face",
                        "label": "Trimmed face",
                        "operation": "trimmed_face",
                        "surface": plane,
                        "boundaries": [{"intersection": "edge", "keep": "inside"}],
                    },
                    {
                        "id": "wall",
                        "label": "Open wall",
                        "operation": "trimmed_face",
                        "surface": {"feature": "fit", "surface": "side"},
                        "boundaries": [{"intersection": "edge", "keep": "negative"}],
                    },
                ]
            )
            payload["output"] = "face"
            snapshot = json.loads(
                post(
                    "/api/graph",
                    {"token": server.graph.snapshot()["token"], "recipe": payload},
                )
            )
            _ = post(
                "/api/graph/evaluate", {"token": snapshot["token"], "all_actions": True}
            )
            assert server.graph_job is not None
            _ = server.graph_job.result(timeout=30)
            with urlopen(base + "/api/graph", timeout=10) as response:
                snapshot = json.load(response)
            assert snapshot["states"]["face"] == snapshot["states"]["edge"] == "ready"
            assert snapshot["results"]["face"]["bounded"]
            neighbors = json.loads(
                post(
                    "/api/graph/build-faces/candidates",
                    {
                        "token": snapshot["token"],
                        "target": plane,
                        "surfaces": [plane, {"feature": "fit", "surface": "side"}],
                    },
                )
            )
            assert len(neighbors["candidates"]) == 1
            shared = neighbors["candidates"][0]["shared_faces"]
            assert shared[0]["id"] == "wall" and shared[0]["preview_paths"]
            assert (
                json.loads(json.dumps(server.graph.snapshot()["recipe"]))
                == snapshot["recipe"]
            )
            data = post(
                "/api/export/cad",
                {
                    "token": snapshot["token"],
                    "target": "face",
                    "units": "Millimeters",
                    "axis_up": False,
                    "include_mesh": False,
                },
            )
            with ZipFile(BytesIO(data)) as bundle:
                assert "model.step" in bundle.namelist()
                assert bundle.read("model.step").startswith(b"ISO-10303-21;")
                assert "metadata.json" in bundle.namelist()
                assert not any(name.endswith(".ply") for name in bundle.namelist())
            # Face-set selection is explicit, deduplicated, and must not export
            # its unbounded wall guidance as another CAD object.
            collection = {
                "token": snapshot["token"],
                "scope": "selected_faces",
                "targets": ["face", "face"],
                "units": "Millimeters",
                "axis_up": False,
                "include_mesh": False,
            }
            data = post("/api/export/cad", collection)
            with ZipFile(BytesIO(data)) as bundle:
                exported = json.loads(bundle.read("metadata.json"))
                assert [item["feature"] for item in exported["objects"]] == ["face"]
                assert not exported["solid"] and not exported["sewn"]
            with pytest.raises(HTTPError) as open_collection:
                _ = post(
                    "/api/export/cad",
                    {
                        **collection,
                        "scope": "all_faces",
                        "targets": None,
                    },
                )
            assert open_collection.value.code == 422
            assert b"open region" in open_collection.value.read()
            with pytest.raises(HTTPError) as error:
                _ = post(
                    "/api/export/cad",
                    {
                        "token": snapshot["token"],
                        "target": "wall",
                        "units": "Millimeters",
                        "axis_up": False,
                        "include_mesh": False,
                    },
                )
            assert error.value.code == 422
            assert b"finite explicit bounds" in error.value.read()
            # Batch authoring reviews the same explicit regions and reuses
            # the existing manual face without modifying or adopting it.
            original_recipe = snapshot["recipe"]
            face_scopes = [{"surface": plane, "faces": ["face"]}]
            proposal = json.loads(
                post(
                    "/api/graph/build-faces/preview",
                    {
                        "token": snapshot["token"],
                        "surfaces": [plane, {"feature": "fit", "surface": "side"}],
                        "face_scopes": face_scopes,
                    },
                )
            )
            assert (
                json.loads(json.dumps(server.graph.snapshot()["recipe"]))
                == original_recipe
            )
            plane_proposal = next(
                face for face in proposal["faces"] if face["surface"] == plane
            )
            region = next(
                region
                for region in plane_proposal["regions"]
                if region["existing_face_id"] == "face"
            )
            updated = json.loads(
                post(
                    "/api/graph/build-faces/apply",
                    {
                        "token": snapshot["token"],
                        "proposal_token": proposal["proposal_token"],
                        "surfaces": [plane, {"feature": "fit", "surface": "side"}],
                        "label": "Reviewed faces",
                        "face_scopes": face_scopes,
                        "choices": [{"surface": plane, "region_key": region["key"]}],
                    },
                )
            )
            owner = next(
                node
                for node in updated["recipe"]["nodes"]
                if node["operation"] == "build_faces"
            )
            assert owner["reused_faces"] == ["face"]
            assert owner["face_scopes"] == face_scopes
            assert owner["adjacencies"][0]["state"] == "confirmed"
            assert next(
                node for node in updated["recipe"]["nodes"] if node["id"] == "face"
            ) == next(node for node in original_recipe["nodes"] if node["id"] == "face")
            _ = post(
                "/api/graph/evaluate",
                {"token": updated["token"], "target": owner["id"]},
            )
            assert server.graph_job is not None
            _ = server.graph_job.result(timeout=30)
            assert (
                cast(dict[str, Any], server.graph.snapshot())["states"][owner["id"]]
                == "ready"
            )
            # Round-trip the JSON recipe through load, then review/update the
            # same owner. Scopes and accepted boundaries must survive reopening.
            saved_recipe = json.loads(json.dumps(updated["recipe"]))
            loaded = json.loads(
                post(
                    "/api/graph",
                    {"token": updated["token"], "recipe": saved_recipe},
                )
            )
            _ = post(
                "/api/graph/evaluate",
                {"token": loaded["token"], "all_actions": True},
            )
            assert server.graph_job is not None
            _ = server.graph_job.result(timeout=30)
            reopened = json.loads(
                post(
                    "/api/graph/build-faces/preview",
                    {
                        "token": loaded["token"],
                        "owner_id": owner["id"],
                        "surfaces": owner["surfaces"],
                    },
                )
            )
            assert reopened["face_scopes"] == face_scopes
            assert reopened["adjacency_choices"] == owner["adjacencies"]
            repeated = json.loads(
                post(
                    "/api/graph/build-faces/apply",
                    {
                        "token": loaded["token"],
                        "owner_id": owner["id"],
                        "surfaces": owner["surfaces"],
                        "proposal_token": reopened["proposal_token"],
                        "label": owner["label"],
                        "choices": [{"surface": plane, "region_key": region["key"]}],
                    },
                )
            )
            assert repeated["recipe"] == saved_recipe
            with pytest.raises(HTTPError) as stale:
                _ = post(
                    "/api/graph/build-faces/preview",
                    {
                        "token": snapshot["token"],
                        "surfaces": [plane, {"feature": "fit", "surface": "side"}],
                    },
                )
            assert stale.value.code == 409
        finally:
            server.shutdown()
            worker.join(timeout=10)
