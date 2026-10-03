"""General physical boundaries survive graph replay and read-only STEP export."""

import io
import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from numpy.typing import NDArray
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer

import experiments.feature_graph as feature_graph
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    apply_faces,
    preview_faces,
)
from experiments.face_geometry import face_from_record
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.nozzle_session import NozzleWorkspace

EXAMPLE = Path("examples/nozzle-bayonette-simplified")


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(EXAMPLE)


def recipe() -> dict[str, Any]:
    original = Recipe.model_validate_json(
        (EXAMPLE / "recipes/cone-plane.json").read_text()
    ).model_dump()
    original["nodes"] = [original["nodes"][0]]
    original["nodes"].append(
        {
            "id": "axis",
            "label": "Plate normal",
            "operation": "axis",
            "initial_parameters": [0, 0, 0, 0],
        }
    )
    for name, angle, offset in (
        ("plate", None, 0),
        ("left", 180, -4),
        ("right", 180, 4),
        ("bottom", 270, -3),
        ("top", 270, 3),
    ):
        original["nodes"].append(
            {
                "id": name,
                "label": name.title(),
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis"
                if angle is None
                else "parallel_to_axis",
                "initial_angle_degrees": angle,
                "offset": offset,
            }
        )
    original["output"] = "plate"
    return original


def add_boundaries(payload: dict[str, Any], holes: bool = False) -> None:
    uses: list[dict[str, str]] = []
    cuts = [
        ("left", "positive"),
        ("right", "negative"),
        ("bottom", "positive"),
        ("top", "negative"),
    ]
    if holes:
        cuts.extend([("hole_left", "outside"), ("hole_right", "outside")])
    for name, keep in cuts:
        edge = "edge_" + name
        payload["nodes"].append(
            {
                "id": edge,
                "label": "Boundary " + name,
                "operation": "surface_intersection",
                "first": {"feature": "plate"},
                "second": {"feature": name},
            }
        )
        uses.append({"intersection": edge, "keep": keep})
    payload["nodes"].append(
        {
            "id": "plate_face",
            "label": "Reviewed plate face",
            "operation": "trimmed_face",
            "surface": {"feature": "plate"},
            "boundaries": uses,
        }
    )
    payload["output"] = "plate_face"


def evaluate(graph: FeatureGraph) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), all_actions=True),
    )


def test_plane_pair_evaluates_line_and_replays_without_fit(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    payload["nodes"].append(
        {
            "id": "line",
            "label": "Shared plane line",
            "operation": "surface_intersection",
            "first": {"feature": "plate"},
            "second": {"feature": "left"},
        }
    )
    payload["output"] = "line"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = evaluate(graph)
    assert not snapshot["errors"] and snapshot["states"]["line"] == "ready"
    line = snapshot["results"]["line"]
    assert line["kind"] == "surface_intersection"
    assert len(line["curves"]) == 1 and line["curves"][0]["kind"] == "line"
    points = np.asarray(line["curves"][0]["preview"]["positions"]).reshape(-1, 3)
    for name in ("plate", "left"):
        equation = np.asarray(snapshot["results"][name]["plane_equation"])
        np.testing.assert_allclose(points @ equation[:3], equation[3], atol=1e-10)
    assert line["preview_clipped"]  # The finite display is not a physical cap.
    replay = FeatureGraph(workspace, Recipe.model_validate(snapshot["recipe"]))
    assert evaluate(replay)["results"] == snapshot["results"]


def test_signed_line_boundaries_make_explicit_rectangle_and_invalidate_on_edit(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    add_boundaries(payload)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = evaluate(graph)
    face = snapshot["results"]["plate_face"]
    assert face["bounded"] and not face["preview_clipped"]
    assert len(face["bounds"]["loops"]) == 1
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face_from_record(face), properties)
    assert properties.Mass() == pytest.approx(48)
    changed = deepcopy(snapshot["recipe"])
    next(node for node in changed["nodes"] if node["id"] == "right")["offset"] = 5
    stale = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(changed), snapshot["token"])
    )
    assert stale["states"]["edge_right"] == stale["states"]["plate_face"] == "stale"
    assert "plate_face" not in stale["results"]
    updated = evaluate(graph)
    BRepGProp.SurfaceProperties_s(
        face_from_record(updated["results"]["plate_face"]), properties
    )
    assert properties.Mass() == pytest.approx(54)


@pytest.mark.parametrize("keep", ["inside", "outside"])
def test_plane_line_requires_signed_cutting_plane_choice(
    workspace: NozzleWorkspace, keep: str
) -> None:
    payload = recipe()
    add_boundaries(payload)
    payload["nodes"][-1]["boundaries"][0]["keep"] = keep
    with pytest.raises(ValueError, match="keep choice"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_nonconcentric_holes_graph_replay_and_readonly_step_metadata(
    workspace: NozzleWorkspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = recipe()
    ids = [workspace.default.plane_ids[0], workspace.default.plane_ids[-1]]
    seeded = {
        workspace.local[id].tobytes(): [-1.5 if index == 0 else 1.5, 0, 0, 0, 1, 0, 0]
        for index, id in enumerate(ids)
    }

    # Isolate graph/CAD integration from numerical fitting. These explicit
    # deterministic fitted cylinders retain the real fixture's observation IDs.
    def fitted(
        points: NDArray[np.float64],
        weights: NDArray[np.float64],
        normals: NDArray[np.float64],
        kind: str,
        initial: NDArray[np.float64],
        domain: tuple[float, float],
    ) -> dict[str, Any]:
        del weights, normals, initial
        assert kind == "cylinder"
        return {
            "kind": kind,
            "parameters": seeded[points[0].tobytes()],
            "axial_domain": domain,
            "residuals": [0.0] * len(points),
            "weighted_rms": 0.0,
        }

    monkeypatch.setattr(feature_graph, "fit_seed", fitted)
    for name, id in zip(("hole_left", "hole_right"), ids, strict=True):
        payload["nodes"].extend(
            [
                {
                    "id": name + "_selection",
                    "label": name + " evidence",
                    "operation": "selection",
                    "source": "scan",
                    "ids": [id],
                },
                {
                    "id": name,
                    "label": name,
                    "operation": "fit",
                    "kind": "cylinder",
                    "selections": [name + "_selection"],
                },
            ]
        )
    original = evaluate(FeatureGraph(workspace, Recipe.model_validate(payload)))
    add_boundaries(payload, holes=True)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = evaluate(graph)
    assert not snapshot["errors"]
    assert snapshot["memberships"] == original["memberships"]
    for name in ("hole_left", "hole_right", "plate"):
        assert snapshot["results"][name] == original["results"][name]
    record = snapshot["results"]["plate_face"]
    assert len(record["bounds"]["loops"]) == 3
    replay = FeatureGraph(workspace, Recipe.model_validate(snapshot["recipe"]))
    assert evaluate(replay)["results"] == snapshot["results"]

    before = deepcopy(snapshot)
    data = export_cad(
        workspace,
        snapshot,
        CadExportRequest(
            token=snapshot["token"],
            target="plate_face",
            units="Millimeters",
            include_mesh=False,
        ),
    )
    assert snapshot == before and graph.snapshot() == before
    with ZipFile(io.BytesIO(data)) as bundle:
        assert set(bundle.namelist()) == {"model.step", "metadata.json"}
        metadata = json.loads(bundle.read("metadata.json"))
        path = tmp_path / "plate.step"
        _ = path.write_bytes(bundle.read("model.step"))
    item = metadata["objects"][0]
    assert metadata["recipe"] == Recipe.model_validate(snapshot["recipe"]).model_dump(
        mode="json"
    )
    assert item["bounds"] == record["bounds"]
    assert item["boundary_uses"] == record["boundary_uses"]
    assert item["source_surface"] == {"feature": "plate", "surface": None}
    assert item["extent_authority"] == "declared_boundaries"
    reader = STEPControl_Reader()
    read_file = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read_file(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    shape = reader.OneShape()
    assert BRepCheck_Analyzer(shape).IsValid()
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    assert properties.Mass() == pytest.approx(48 - 2 * np.pi, rel=1e-9)
    for kind, count in ((TopAbs_FACE, 1), (TopAbs_WIRE, 3), (TopAbs_SOLID, 0)):
        explorer = TopExp_Explorer(shape, kind)
        actual = 0
        while explorer.More():
            actual += 1
            explorer.Next()
        assert actual == count

    references = [
        {"feature": name}
        for name in (
            "plate",
            "left",
            "right",
            "bottom",
            "top",
            "hole_left",
            "hole_right",
        )
    ]
    plan = preview_faces(
        graph,
        FacesPreviewRequest.model_validate(
            {"token": snapshot["token"], "surfaces": references}
        ),
    )
    plate = next(
        face for face in plan["faces"] if face["surface"]["feature"] == "plate"
    )
    region = next(
        region
        for region in plate["regions"]
        if len(region["bounds"].get("loops", [])) == 3
    )
    assert region["existing_face_id"] == "plate_face"
    applied = apply_faces(
        graph,
        FacesApplyRequest.model_validate(
            {
                "token": snapshot["token"],
                "surfaces": references,
                "proposal_token": plan["proposal_token"],
                "label": "Reviewed plate batch",
                "choices": [
                    {"surface": {"feature": "plate"}, "region_key": region["key"]}
                ],
            }
        ),
    )
    owner = next(
        node
        for node in applied["recipe"]["nodes"]
        if node["operation"] == "build_faces"
    )
    assert owner["reused_faces"] == ["plate_face"]
    assert evaluate(graph)["results"]["plate_face"] == record
