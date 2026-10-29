"""One authored alignment constrains every peer without fixing its first member."""

from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest

import experiments.selected_direction_fit as selected
from experiments.feature_graph import DirectionAlignment, Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.selected_direction_fit import SelectedDirectionComponent
from experiments.surface_primitives import revolution_geometry
from tests.test_direction_graph import aligned, evaluated, graph_with, reference
from tests.test_direction_graph import fixture as fixture


def direction(value: dict[str, Any]) -> np.ndarray:
    return np.asarray(
        value["plane_equation"][:3]
        if "plane_equation" in value
        else revolution_geometry(value)["axis"]
    )


def test_direction_group_retains_the_existing_peer_capacity_of_thirty_three() -> None:
    references = [reference(f"axis_{index}") for index in range(33)]
    declaration = DirectionAlignment.model_validate(
        {
            "id": "alignment",
            "label": "Alignment",
            "operation": "direction_alignment",
            "directions": references,
        }
    )
    assert len(declaration.directions) == 33


@pytest.mark.parametrize(
    "members", [("left", "right", "cap"), ("left", "right", "cap", "other")]
)
def test_mixed_peer_group_is_order_independent_and_compiles_a_spanning_tree(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
    monkeypatch: pytest.MonkeyPatch,
    members: tuple[str, ...],
) -> None:
    original = selected.fit_selected_direction_component
    compiled: list[Any] = []

    def capture(
        workspace: Any, component: SelectedDirectionComponent, **kwargs: Any
    ) -> dict[str, Any]:
        compiled.append(component.directions)
        return original(workspace, component, **kwargs)

    monkeypatch.setattr(selected, "fit_selected_direction_component", capture)
    orders = [members, tuple(reversed(members)), members[1:] + members[:1]]
    baseline: dict[str, Any] | None = None
    for order in orders:
        references = [
            reference(key, "plane" if key in {"cap", "other"} else "axis")
            for key in order
        ]
        graph = graph_with(
            fixture,
            {
                "id": "alignment",
                "operation": "direction_alignment",
                "directions": references,
            },
        )
        saved = graph.export_recipe()
        pending = cast(dict[str, Any], graph.snapshot())
        assert pending["states"]["alignment"] == "unevaluated"
        assert "alignment" not in pending["derived"]
        assert saved["nodes"][-1]["directions"] == references
        state = evaluated(graph, "alignment")
        assert state["states"]["alignment"] == "ready", state["errors"]
        assert graph.export_recipe() == saved
        surfaces = state["derived"]["alignment"]["surfaces"]
        assert set(surfaces) == set(members)
        first = direction(surfaces[members[0]])
        assert all(
            np.linalg.norm(np.cross(first, direction(surfaces[key]))) < 1e-7
            for key in members
        )
        left, right = (
            revolution_geometry(surfaces["left"]),
            revolution_geometry(surfaces["right"]),
        )
        assert (
            np.linalg.norm(
                np.cross(
                    np.asarray(right["origin"]) - np.asarray(left["origin"]), first
                )
            )
            > 8
        )
        assert abs(right["slope"]) > 0.1
        if "other" in surfaces:
            assert (
                abs(
                    surfaces["cap"]["plane_equation"][3]
                    - surfaces["other"]["plane_equation"][3]
                )
                > 3
            )
        if baseline is None:
            baseline = surfaces
        else:
            for key in members:
                assert (
                    np.linalg.norm(
                        np.cross(direction(baseline[key]), direction(surfaces[key]))
                    )
                    < 1e-10
                )
                if key in {"cap", "other"}:
                    np.testing.assert_allclose(
                        baseline[key]["plane_equation"],
                        surfaces[key]["plane_equation"],
                        atol=1e-9,
                    )
                else:
                    np.testing.assert_allclose(
                        revolution_geometry(baseline[key])["origin"],
                        revolution_geometry(surfaces[key])["origin"],
                        atol=1e-9,
                    )
        reopened = Recipe.model_validate_json(
            Recipe.model_validate(saved).model_dump_json()
        )
        assert reopened.model_dump() == Recipe.model_validate(saved).model_dump()
    assert len(compiled) == len(orders)
    assert all(len(edges) == len(members) - 1 for edges in compiled)
    assert all(edges == compiled[0] for edges in compiled)


def test_group_retains_manual_axis_position_and_unused_plane_offset(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "axis",
            "operation": "axis",
            "initial_parameters": [31.0, -19.0, 0.0, 0.0],
        },
        {
            "id": "datum",
            "operation": "reference_plane",
            "construction": "independent",
            "initial_normal": [0, 1, 0],
            "initial_angle_degrees": None,
            "offset": 73,
        },
        {
            "id": "alignment",
            "operation": "direction_alignment",
            "directions": [
                reference("axis"),
                reference("left"),
                reference("cap", "plane"),
                reference("datum", "plane"),
            ],
        },
    )
    state = evaluated(graph, "alignment")
    assert state["states"]["alignment"] == "ready", state["errors"]
    values = state["results"]
    np.testing.assert_allclose(values["axis"]["point_display"], [31, -19, 0], atol=1e-9)
    assert values["datum"]["plane_equation"][3] == pytest.approx(73)
    first = np.asarray(values["axis"]["axis_display"])
    assert np.linalg.norm(np.cross(first, values["datum"]["normal_display"])) < 1e-7
    assert abs(first[1]) < 0.1
    assert np.linalg.norm(np.cross(first, direction(values["left"]))) < 1e-7
    assert np.linalg.norm(np.cross(first, direction(values["cap"]))) < 1e-7


def test_group_preserves_two_fixed_alias_contexts_without_upstream_feedback(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]],
) -> None:
    graph = graph_with(
        fixture,
        {
            "id": "derived_axis",
            "operation": "axis",
            "source_fit": "left",
            "direction_reversed": True,
        },
        aligned("upstream", "left", "right"),
    )
    before = evaluated(graph)
    recipe = graph.export_recipe()
    refs = [
        reference("cap", "plane"),
        reference("other", "plane"),
        reference("left", "axis", "upstream"),
        reference("derived_axis", "axis", "upstream"),
    ]
    recipe["nodes"].append(
        {
            "id": "downstream",
            "label": "Downstream",
            "operation": "direction_alignment",
            "directions": refs,
        }
    )
    recipe["output"] = "downstream"
    _ = graph.replace(Recipe.model_validate(recipe), str(before["token"]))
    pending = cast(dict[str, Any], graph.snapshot())
    assert pending["states"]["downstream"] == "unevaluated"
    state = evaluated(graph, "downstream")
    assert state["states"]["downstream"] == "ready", state["errors"]
    assert state["derived"]["upstream"] == before["derived"]["upstream"]
    downstream = state["derived"]["downstream"]
    assert set(downstream["surfaces"]) == {"cap", "other"}
    assert (
        "left" not in downstream["resolved"]
        and "derived_axis" not in downstream["resolved"]
    )
    expected = before["derived"]["upstream"]["resolved"]["derived_axis"]["axis_display"]
    for key in ("cap", "other"):
        assert (
            np.linalg.norm(np.cross(expected, direction(downstream["surfaces"][key])))
            < 1e-7
        )
    assert graph.export_recipe()["nodes"][-1]["directions"] == refs


@pytest.mark.parametrize(
    "references",
    [
        [reference("left")],
        [reference("left"), "left", reference("cap", "plane")],
        [reference("left"), reference("cap", "plane"), reference("removed")],
        [reference("left")] * 34,
    ],
)
def test_peer_group_rejects_invalid_counts_duplicates_and_removed_outputs(
    fixture: tuple[NozzleWorkspace, list[dict[str, Any]]], references: list[Any]
) -> None:
    with pytest.raises(ValueError):
        _ = graph_with(
            fixture,
            {
                "id": "alignment",
                "operation": "direction_alignment",
                "directions": deepcopy(references),
            },
        )
