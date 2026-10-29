"""Independent datums retain their world-plane meaning across feature reuse."""

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    PlaneDefinition,
    Recipe,
    SurfaceFit,
    reference_plane_result,
)
from experiments.feature_reuse import transformed_plane_initial
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.nozzle_session import NozzleWorkspace
from tests.test_direction_graph import fixture as fixture
from tests.test_direction_graph import reference


def _datum() -> dict[str, Any]:
    return {
        "id": "datum",
        "operation": "reference_plane",
        "label": "Independent source plane",
        "construction": "independent",
        "initial_normal": [0.01, 0.02, 1.0],
        "initial_angle_degrees": None,
        "offset": -23.0,
    }


def _recipe(nodes: list[dict[str, Any]]) -> Recipe:
    return Recipe.model_validate({"nodes": nodes, "output": nodes[-1]["id"]})


def _source_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = deepcopy(nodes)
    index = next(index for index, node in enumerate(result) if node["id"] == "cap")
    result.insert(index, _datum())
    result[index + 1]["reference_plane"] = "datum"
    return result


def _owned(recipe: Recipe, key: str) -> Any:
    return next(node for node in recipe.nodes if node.managed_key == key)


@pytest.mark.parametrize("normal", [[0.0, 0.0, -2.0], [0.3, -0.4, 0.5]])
@pytest.mark.parametrize("angle", [0.0, 1.1])
def test_independent_plane_placement_preserves_signed_world_equation(
    normal: list[float], angle: float
) -> None:
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.asarray([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])
    translation = np.asarray([4.0, -5.0, 6.0])
    source = {"construction": "independent", "plane_equation": [*normal, -7.0]}
    before = deepcopy(source)

    initial = transformed_plane_initial(source, rotation, translation)
    source_normal = np.asarray(normal) / np.linalg.norm(normal)
    expected_normal = source_normal @ rotation
    expected_offset = -7.0 / np.linalg.norm(normal) + expected_normal @ translation
    placed = reference_plane_result(
        None, PlaneDefinition.model_validate({**_datum(), **initial})
    )

    np.testing.assert_allclose(placed["normal_display"], expected_normal, atol=1e-14)
    assert placed["plane_equation"][3] == pytest.approx(expected_offset)
    assert initial["initial_angle_degrees"] is None
    assert "axis" not in initial
    point = source_normal * (-7.0 / np.linalg.norm(normal))
    assert (point @ rotation + translation) @ expected_normal == pytest.approx(
        expected_offset
    )
    assert source == before


def test_axis_derived_plane_placement_still_requires_its_axis() -> None:
    with pytest.raises(ValueError, match="requires a target axis"):
        _ = transformed_plane_initial(
            {"construction": "perpendicular_to_axis", "plane_equation": [0, 0, 1, 2]},
            np.eye(3),
            np.zeros(3),
        )


@pytest.mark.parametrize("context", ["parallel", "@plane/datum"])
def test_reuse_authoring_preserves_independent_plane_ids_and_exact_contexts(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], context: str
) -> None:
    _, original = fixture
    nodes = _source_nodes(original)
    fits = ["cap", "right"]
    if context == "parallel":
        fits.append("other")
        nodes.append(
            {
                "id": "parallel",
                "label": "Parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": [reference("cap", "plane"), reference("other", "plane")],
            }
        )
    nodes.append(
        {
            "id": "alignment",
            "label": "Read exact published direction",
            "operation": "direction_alignment",
            "directions": [reference("right"), reference("cap", "plane", context)],
        }
    )
    recipe = _recipe(nodes)
    before = recipe.model_dump()
    plan = reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges(
            label="Reused independent plane",
            fits=fits,
            reference_selection="cap_selection",
            target_selections=["left_selection"],
        ),
        "independent-plane",
        create=True,
    )
    copied = cast(PlaneDefinition, _owned(plan.recipe, "datum/left_selection/datum"))
    cap = cast(SurfaceFit, _owned(plan.recipe, "fit/left_selection/cap"))
    alignment = _owned(plan.recipe, "relationship/left_selection/alignment")
    expected_context = (
        _owned(plan.recipe, "relationship/left_selection/parallel").id
        if context == "parallel"
        else "@plane/" + copied.id
    )
    assert copied.axis is None and copied.construction == "independent"
    assert copied.initial_normal == (0.01, 0.02, 1.0)
    assert copied.offset == -23.0
    assert copied.placement is not None and copied.placement.source == "datum"
    assert cap.reference_plane == copied.id
    assert alignment.directions[1].model_dump() == reference(
        cap.id, "plane", expected_context
    )
    replay = Recipe.model_validate_json(plan.recipe.model_dump_json())
    repeated = reconcile_feature_reuse(replay, "reuse", ReuseChanges(), "new-seed")
    assert repeated.recipe == plan.recipe
    assert not repeated.generated_ids and not repeated.removed_ids
    assert recipe.model_dump() == before


def test_placed_independent_plane_replays_without_forcing_fit_coincidence(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    workspace, original = fixture
    nodes = _source_nodes(original)
    source = next(node for node in nodes if node["id"] == "cap_selection")
    target = next(node for node in nodes if node["id"] == "other_selection")
    angle = 0.45
    cosine, sine = np.cos(angle), np.sin(angle)
    rotation = np.asarray([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])
    translation = np.asarray([3.0, 2.0, -4.0])
    workspace.local[target["ids"]] = (
        workspace.local[source["ids"]] @ rotation + translation
    )
    workspace.data.normals[target["ids"]] = (
        workspace.data.normals[source["ids"]]
        @ workspace.frame
        @ rotation
        @ workspace.frame.T
    )
    plan = reconcile_feature_reuse(
        _recipe(nodes),
        "reuse",
        ReuseChanges(
            label="Reused independent plane",
            fits=["cap"],
            reference_selection="cap_selection",
            target_selections=["other_selection"],
        ),
        "placed-plane",
        create=True,
    )
    copied = cast(PlaneDefinition, _owned(plan.recipe, "datum/other_selection/datum"))
    cap = cast(SurfaceFit, _owned(plan.recipe, "fit/other_selection/cap"))
    graph = FeatureGraph(workspace, plan.recipe)
    state = cast(
        dict[str, Any],
        graph.ensure_current(str(graph.snapshot()["token"]), targets=[cap.id]),
    )
    assert not state["errors"]
    datum_result = state["results"][copied.id]
    fitted = state["results"][cap.id]
    np.testing.assert_allclose(
        np.cross(datum_result["normal_display"], fitted["plane_equation"][:3]),
        0,
        atol=1e-12,
    )
    assert abs(datum_result["plane_equation"][3] - fitted["plane_equation"][3]) > 10
    assert datum_result["reuse_initializer"]["initial_angle_degrees"] is None
    assert "axis" not in datum_result["reuse_initializer"]

    copied_again = reconcile_feature_reuse(
        plan.recipe,
        "reuse_again",
        ReuseChanges(
            label="Reuse the placed occurrence",
            fits=[cap.id],
            reference_selection=cap.selections[0],
            target_selections=["cap_selection"],
        ),
        "placed-plane-again",
        create=True,
    )
    next_datum = cast(
        PlaneDefinition, _owned(copied_again.recipe, f"datum/cap_selection/{copied.id}")
    )
    next_cap = cast(
        SurfaceFit, _owned(copied_again.recipe, f"fit/cap_selection/{cap.id}")
    )
    assert next_datum.placement is not None
    assert next_datum.placement.source == copied.id
    assert next_datum.axis is None and next_cap.reference_plane == next_datum.id
    next_graph = FeatureGraph(workspace, copied_again.recipe)
    next_state = cast(
        dict[str, Any],
        next_graph.ensure_current(
            str(next_graph.snapshot()["token"]), targets=[next_cap.id]
        ),
    )
    assert not next_state["errors"]
    np.testing.assert_allclose(
        next_state["results"][next_cap.id]["plane_equation"],
        state["results"]["cap"]["plane_equation"],
        atol=1e-10,
    )

    payload = deepcopy(state["recipe"])
    next(node for node in payload["nodes"] if node["id"] == "datum")["offset"] = -19.0
    stale = cast(
        dict[str, Any], graph.replace(_recipe(payload["nodes"]), state["token"])
    )
    assert stale["states"][copied.id] == "stale"
    replay = cast(
        dict[str, Any], graph.ensure_current(stale["token"], targets=[cap.id])
    )
    assert not replay["errors"]
    assert replay["recipe"] == _recipe(payload["nodes"]).model_dump()
    assert (
        replay["memberships"][cap.selections[0]]
        == state["memberships"][cap.selections[0]]
    )
    np.testing.assert_allclose(
        replay["results"][cap.id]["plane_equation"],
        fitted["plane_equation"],
        atol=1e-10,
    )
