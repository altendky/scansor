"""Face authoring has work budgets, not an arbitrary selected-surface cap."""

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

import experiments.face_proposals as face_proposals
from experiments.face_builder import (
    FacesApplyRequest,
    FacesPreviewRequest,
    preview_faces,
)
from experiments.face_proposals import propose_faces, reference_key
from experiments.feature_graph import BuildFaces, FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace


def inputs(count: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(count):
        surface = (
            {"kind": "plane", "plane_equation": [0, 0, 1, 2]}
            if index == 0
            else {"kind": "cylinder", "parameters": [0, 0, 0, 0, index, 0, 0]}
        )
        points = (
            [[0.2, 0, 2], [0, 0.3, 2], [-0.4, 0, 2]]
            if index == 0
            else [[index, 0, 0.2], [0, index, 0.3], [-index, 0, 0.4]]
        )
        result.append(
            {
                "reference": {"feature": f"surface_{index}", "surface": None},
                "label": f"Surface {index}",
                "surface": surface,
                "observations": np.asarray(points, dtype=float),
                "weights": np.ones(3),
            }
        )
    return result


@pytest.mark.parametrize("count", [17, 64, 128])
def test_authoring_schemas_accept_large_explicit_surface_and_reuse_lists(
    count: int,
) -> None:
    references = [value["reference"] for value in inputs(count)]
    preview = FacesPreviewRequest.model_validate(
        {"token": "graph", "surfaces": references}
    )
    apply = FacesApplyRequest.model_validate(
        {
            "token": "graph",
            "surfaces": references,
            "proposal_token": "reviewed",
            "label": "Batch",
            "choices": [
                {"surface": reference, "region_key": f"region_{index}"}
                for index, reference in enumerate(references)
            ],
        }
    )
    owner = BuildFaces.model_validate(
        {
            "id": "batch",
            "label": "Batch",
            "operation": "build_faces",
            "surfaces": references,
            "reused_faces": [f"face_{index}" for index in range(count)],
            "reused_intersections": [f"edge_{index}" for index in range(count)],
        }
    )
    assert len(preview.surfaces) == len(apply.surfaces) == len(apply.choices) == count
    assert (
        len(owner.surfaces)
        == len(owner.reused_faces)
        == len(owner.reused_intersections)
        == count
    )


@pytest.mark.parametrize("count", [17, 64, 128])
def test_large_supported_nonpathological_batch_is_not_truncated(count: int) -> None:
    selected = inputs(count)
    result = propose_faces(selected)
    assert len(result["faces"]) == count
    assert {face["key"] for face in result["faces"]} == {
        reference_key(value["reference"]) for value in selected
    }
    assert len(result["intersections"]) == count - 1
    assert sum(len(face["regions"]) for face in result["faces"]) == 3 * count - 2
    assert all(face["status"] == "suggested" for face in result["faces"])
    assert not result["diagnostics"]


def forbidden_geometry_work(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("geometry or preview work started before budget rejection")


def test_pair_budget_rejects_before_primitive_intersection_or_preview_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_proposals, "MAX_PAIRS", 2)
    for name in ("primitive", "circle_intersection", "trimmed_face", "_evidence"):
        monkeypatch.setattr(face_proposals, name, forbidden_geometry_work)
    with pytest.raises(ValueError, match="pair-check budget"):
        _ = propose_faces(inputs(3))


def test_region_budget_rejects_before_face_preview_or_evidence_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(face_proposals, "MAX_REGIONS", 1)
    for name in ("trimmed_face", "_evidence", "_projected_coordinates"):
        monkeypatch.setattr(face_proposals, name, forbidden_geometry_work)
    with pytest.raises(ValueError, match="region proposal limit"):
        _ = propose_faces(inputs(2))


def test_region_budget_failure_leaves_graph_and_fit_results_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    graph = FeatureGraph(
        NozzleWorkspace(example),
        Recipe.model_validate_json((example / "recipes/cone-plane.json").read_text()),
    )
    before = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    monkeypatch.setattr(face_proposals, "MAX_REGIONS", 1)
    request = FacesPreviewRequest.model_validate(
        {
            "token": before["token"],
            "surfaces": [
                {"feature": "fit", "surface": "side"},
                {"feature": "fit", "surface": "end"},
            ],
        }
    )
    with pytest.raises(ValueError, match="region proposal limit"):
        _ = preview_faces(graph, request)
    assert graph.snapshot() == before
