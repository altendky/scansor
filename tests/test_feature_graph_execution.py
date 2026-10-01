"""Compile all geometry influence before executing ordinary graph consumers."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

import experiments.feature_graph as feature_graph
from experiments.feature_graph import FeatureGraph, Recipe, discover_reuse_lineage
from experiments.nozzle_session import NozzleWorkspace
from experiments.selection_growth import connected_growth
from experiments.selection_region import build_selection_region


@pytest.fixture
def fixture() -> tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]]:
    path = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(path)
    payload = Recipe.model_validate_json(
        (path / "recipes/cone-plane.json").read_text()
    ).model_dump()
    return workspace, payload, {node["id"]: node for node in payload["nodes"]}


def selection(name: str, ids: list[int]) -> dict[str, Any]:
    return {
        "id": name,
        "label": name,
        "operation": "selection",
        "source": "scan",
        "ids": ids,
    }


def fit(name: str, selection_name: str, kind: str, **references: str) -> dict[str, Any]:
    return {
        "id": name,
        "label": name,
        "operation": "fit",
        "selections": [selection_name],
        "kind": kind,
        "axial_domain": [-2, 5],
        **references,
    }


def sphere_points(
    count: int, center: NDArray[np.float64], radius: float
) -> NDArray[np.float64]:
    height = np.linspace(-0.95, 0.95, count)
    angle = np.arange(count) * np.pi * (3 - np.sqrt(5))
    radial = np.sqrt(1 - height**2)
    return center + radius * np.column_stack(
        (radial * np.cos(angle), radial * np.sin(angle), height)
    )


def cylinder_points(
    count: int, radius: float, tilted: bool = False
) -> NDArray[np.float64]:
    axis = np.array([0.12, -0.08, 1.0] if tilted else [0.0, 0.0, 1.0])
    axis /= np.linalg.norm(axis)
    first = np.cross(axis, np.array([1.0, 0.0, 0.0]))
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    angle = np.arange(count) * np.pi * (3 - np.sqrt(5))
    return (
        np.array([0.3, -0.2, 0.0])
        + np.linspace(-1, 1, count)[:, None] * axis
        + radius * (np.cos(angle)[:, None] * first + np.sin(angle)[:, None] * second)
    )


def run(
    workspace: NozzleWorkspace, payload: dict[str, Any], all_actions: bool
) -> dict[str, Any]:
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    return cast(
        dict[str, Any],
        graph.evaluate(
            str(graph.snapshot()["token"]),
            target=None if all_actions else payload["output"],
            all_actions=all_actions,
        ),
    )


@pytest.mark.parametrize("all_actions", [False, True])
@pytest.mark.parametrize("consumer_kind", ["datum", "frame"])
def test_early_axis_consumer_uses_resolved_geometry_after_late_sibling(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
    consumer_kind: str,
) -> None:
    workspace, payload, base = fixture
    ids = workspace.default.lateral_ids
    first_ids, second_ids = ids[::2], ids[1::2]
    workspace.local[first_ids] = cylinder_points(len(first_ids), 3, tilted=True)
    workspace.local[second_ids] = cylinder_points(len(second_ids), 5, tilted=True)
    payload.update(
        nodes=[
            base["scan"],
            selection("a_selection", first_ids),
            selection("b_selection", second_ids),
            {
                "id": "axis",
                "label": "Free axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 0, 0],
            },
            fit("a", "a_selection", "cylinder", axis="axis"),
            {
                "id": "consumer",
                "label": "Early perpendicular datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 2,
            },
            fit("b", "b_selection", "cylinder", axis="axis"),
        ],
        output="consumer",
    )
    if consumer_kind == "frame":
        consumer_index = next(
            index
            for index, node in enumerate(payload["nodes"])
            if node["id"] == "consumer"
        )
        payload["nodes"][consumer_index : consumer_index + 1] = [
            {
                "id": "origin",
                "label": "Frame origin",
                "operation": "point",
                "initial_coordinates": [0, 0, 0],
            },
            {
                "id": "clock_point",
                "label": "Frame clock point",
                "operation": "point",
                "initial_coordinates": [1, 0, 0],
            },
            {
                "id": "clock",
                "label": "Frame clock axis",
                "operation": "axis",
                "source_points": ["origin", "clock_point"],
            },
            {
                "id": "consumer",
                "label": "Early axis frame",
                "operation": "frame",
                "origin_point": "origin",
                "primary_reference": "axis",
                "primary_output_axis": "+Z",
                "secondary_reference": "clock",
                "secondary_output_axis": "+X",
            },
        ]
    result = run(workspace, payload, all_actions)
    resolved = result["results"]
    assert resolved["axis"]["resolved_by"] == "connected_fits"
    np.testing.assert_allclose(
        resolved["consumer"][
            "normal_display" if consumer_kind == "datum" else "z_axis_display"
        ],
        resolved["axis"]["axis_display"],
        atol=1e-10,
    )
    assert result["states"]["b"] == "ready"


@pytest.mark.parametrize("all_actions", [False, True])
def test_early_scale_uses_joint_point_after_late_sphere_sibling(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
) -> None:
    workspace, payload, base = fixture
    ids = workspace.default.plane_ids
    first_ids, second_ids = ids[::2], ids[1::2]
    center = np.array([2.5, -1.75, 3.25])
    workspace.local[first_ids] = sphere_points(len(first_ids), center, 3)
    workspace.local[second_ids] = sphere_points(len(second_ids), center, 5)
    payload.update(
        nodes=[
            base["scan"],
            selection("a_selection", first_ids),
            selection("b_selection", second_ids),
            {
                "id": "center",
                "label": "Free center",
                "operation": "point",
                "initial_coordinates": [1, -1, 2],
            },
            fit("a", "a_selection", "sphere", point="center"),
            {
                "id": "anchor",
                "label": "Scale anchor",
                "operation": "point",
                "initial_coordinates": [10, 0, 0],
            },
            {
                "id": "consumer",
                "label": "Early scale",
                "operation": "scale",
                "distances": [
                    {
                        "first_point": "center",
                        "second_point": "anchor",
                        "known_distance": 10,
                    }
                ],
            },
            fit("b", "b_selection", "sphere", point="center"),
        ],
        output="consumer",
    )
    result = run(workspace, payload, all_actions)
    np.testing.assert_allclose(
        result["results"]["center"]["point_display"], center, atol=1e-8
    )
    assert result["results"]["consumer"]["scale"] == pytest.approx(
        10 / np.linalg.norm(np.array([10, 0, 0]) - center), rel=1e-8
    )
    assert result["states"]["b"] == "ready"


@pytest.mark.parametrize("all_actions", [False, True])
def test_early_growth_preserves_raw_seed_separate_from_equal_radius(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, payload, base = fixture
    ids = workspace.default.lateral_ids
    first_ids, second_ids = ids[::2], ids[1::2]
    workspace.local[first_ids] = cylinder_points(len(first_ids), 3)
    workspace.local[second_ids] = cylinder_points(len(second_ids), 5)
    payload.update(
        nodes=[
            base["scan"],
            selection("a_selection", first_ids),
            selection("b_selection", second_ids),
            fit("a", "a_selection", "cylinder"),
            {
                "id": "consumer",
                "label": "Early growth",
                "operation": "growth",
                "seed_fit": "a",
                "distance": 0.1,
                "angle_degrees": 90,
            },
            fit("b", "b_selection", "cylinder"),
            {
                "id": "same_radius",
                "label": "Equal radius",
                "operation": "equal_radii",
                "surfaces": ["a", "b"],
            },
        ],
        output="consumer",
    )
    captured: list[dict[str, Any]] = []

    def record_seed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.append(deepcopy(args[6]))
        return connected_growth(*args, **kwargs)

    monkeypatch.setattr(feature_graph, "connected_growth", record_seed)
    result = run(workspace, payload, all_actions)
    assert len(captured) == 1
    assert captured[0]["parameters"][4] == pytest.approx(3)
    assert captured[0]["parameters"][4] != pytest.approx(
        result["results"]["same_radius"]["value"]
    )
    assert "resolved_by" not in captured[0]
    assert result["states"]["b"] == "ready"


@pytest.mark.parametrize("all_actions", [False, True])
def test_early_selection_region_uses_resolved_shared_radius_after_late_member(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
) -> None:
    workspace, payload, base = fixture
    ids = workspace.default.lateral_ids
    first_ids, second_ids = ids[::2], ids[1::2]
    workspace.local[first_ids] = cylinder_points(len(first_ids), 3)
    workspace.local[second_ids] = cylinder_points(len(second_ids), 5)
    payload.update(
        nodes=[
            base["scan"],
            selection("a_selection", first_ids),
            selection("b_selection", second_ids),
            {
                "id": "axis",
                "label": "Free region axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 0, 0],
            },
            {
                "id": "axial",
                "label": "Axial datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
            },
            {
                "id": "clock",
                "label": "Clock datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "contains_axis",
            },
            fit("a", "a_selection", "cylinder", axis="axis"),
            {
                "id": "consumer",
                "label": "Early fitted region",
                "operation": "selection_region",
                "selection": "a_selection",
                "fit": "a",
                "axial_plane": "axial",
                "clock_plane": "clock",
            },
            fit("b", "b_selection", "cylinder", axis="axis"),
            {
                "id": "same_radius",
                "label": "Final shared radius",
                "operation": "equal_radii",
                "surfaces": ["a", "b"],
            },
        ],
        output="consumer",
    )
    result = run(workspace, payload, all_actions)
    assert result["results"]["consumer"]["radius"] == pytest.approx(
        result["results"]["same_radius"]["value"]
    )
    assert result["states"]["b"] == "ready"


def plane_component_recipe(
    workspace: NozzleWorkspace,
    payload: dict[str, Any],
    base: dict[str, dict[str, Any]],
    *,
    transitive: bool,
) -> dict[str, Any]:
    ids = workspace.default.plane_ids
    selections: list[dict[str, Any]] = []
    for index, name in enumerate(("a", "b", "c") if transitive else ("a", "b")):
        count = 3 if transitive else 2
        selected_ids = ids[index::count]
        angle = np.arange(len(selected_ids)) * np.pi * (3 - np.sqrt(5))
        radial = np.linspace(1, 3, len(selected_ids))
        x, y = radial * np.cos(angle), radial * np.sin(angle)
        workspace.local[selected_ids] = np.column_stack(
            (x, y, 2 + (0.1 + 0.1 * index) * x - 0.03 * index * y)
        )
        selections.append(selection(name + "_selection", selected_ids))
    nodes = [
        base["scan"],
        *selections,
        {
            "id": "origin",
            "label": "Frame origin",
            "operation": "point",
            "initial_coordinates": [0, 0, 0],
        },
        {
            "id": "clock",
            "label": "Clock direction",
            "operation": "axis",
            "source_points": ["origin", "clock_point"],
        },
    ]
    # Explicit recipe dependencies must remain a legal DAG. The clock datum's
    # second point precedes its axis, while implicit solved dependencies do not.
    nodes.insert(
        len(nodes) - 1,
        {
            "id": "clock_point",
            "label": "Clock point",
            "operation": "point",
            "initial_coordinates": [1, 0, 0],
        },
    )
    nodes.extend([fit("a", "a_selection", "plane"), fit("b", "b_selection", "plane")])
    relation = {
        "id": "ab",
        "label": "A parallel B",
        "operation": "plane_relationship",
        "relation": "parallel",
        "surfaces": ["a", "b"],
    }
    if transitive:
        nodes.append(relation)
    nodes.append(
        {
            "id": "consumer",
            "label": "Early frame",
            "operation": "frame",
            "origin_point": "origin",
            "primary_reference": "a",
            "primary_output_axis": "+Z",
            "secondary_reference": "clock",
            "secondary_output_axis": "+X",
        }
    )
    if transitive:
        nodes.extend(
            [
                fit("c", "c_selection", "plane"),
                {
                    "id": "bc",
                    "label": "B parallel C",
                    "operation": "plane_relationship",
                    "relation": "parallel",
                    "surfaces": ["b", "c"],
                },
            ]
        )
    else:
        nodes.append(relation)
    return {**payload, "nodes": nodes, "output": "consumer"}


@pytest.mark.parametrize("all_actions", [False, True])
@pytest.mark.parametrize("transitive", [False, True])
def test_early_plane_consumer_waits_for_complete_connected_relationships(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
    transitive: bool,
) -> None:
    workspace, payload, base = fixture
    result = run(
        workspace,
        plane_component_recipe(workspace, payload, base, transitive=transitive),
        all_actions,
    )
    results = result["results"]
    np.testing.assert_allclose(
        results["consumer"]["z_axis_display"],
        results["a"]["plane_equation"][:3],
        atol=1e-12,
    )
    assert results["a"]["resolved_by"] == "plane_relationship"
    assert result["states"]["ab"] == "ready"
    if transitive:
        assert result["states"]["c"] == result["states"]["bc"] == "ready"
        np.testing.assert_allclose(
            results["a"]["plane_equation"][:3],
            results["c"]["plane_equation"][:3],
            atol=1e-12,
        )


@pytest.mark.parametrize("all_actions", [False, True])
def test_real_geometry_influence_cycle_is_rejected_before_any_execution(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, payload, base = fixture
    payload.update(
        nodes=[
            base["scan"],
            base["top_face"],
            {
                "id": "axis",
                "label": "Region axis",
                "operation": "axis",
                "initial_parameters": [0, 0, 0, 0],
            },
            {
                "id": "axial",
                "label": "Axial datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
            },
            {
                "id": "clock",
                "label": "Clock datum",
                "operation": "reference_plane",
                "axis": "axis",
                "construction": "contains_axis",
            },
            fit("a", "top_face", "plane"),
            {
                "id": "region",
                "label": "Region from resolved A",
                "operation": "selection_region",
                "selection": "top_face",
                "fit": "a",
                "axial_plane": "axial",
                "clock_plane": "clock",
            },
            {
                "id": "applied_region",
                "label": "Membership from region",
                "operation": "region_selection",
                "region": "region",
                "source": "scan",
                "axial_plane": "axial",
                "clock_plane": "clock",
            },
            fit("b", "applied_region", "plane"),
            {
                "id": "same_orientation",
                "label": "Parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["a", "b"],
            },
        ],
        output="region",
    )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    before = graph.snapshot()

    def unexpected_fit(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError(
            "execution started before validating the geometry influence graph"
        )

    monkeypatch.setattr(feature_graph, "fit_seed", unexpected_fit)
    with pytest.raises(ValueError, match="influence cycle"):
        _ = graph.evaluate(
            str(before["token"]),
            target=None if all_actions else payload["output"],
            all_actions=all_actions,
        )
    assert graph.snapshot() == before


@pytest.mark.parametrize("all_actions", [False, True])
def test_raw_growth_can_feed_a_later_final_equal_radius_member_without_false_cycle(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
) -> None:
    workspace, payload, base = fixture
    payload.update(
        nodes=[
            base["scan"],
            base["outer_band"],
            fit("a", "outer_band", "cylinder"),
            {
                "id": "grown",
                "label": "Raw-seed growth",
                "operation": "growth",
                "seed_fit": "a",
                "distance": 0.1,
                "angle_degrees": 90,
            },
            fit("b", "grown", "cylinder"),
            {
                "id": "same_radius",
                "label": "Final equal radius",
                "operation": "equal_radii",
                "surfaces": ["a", "b"],
            },
        ],
        output="same_radius",
    )
    result = run(workspace, payload, all_actions)
    assert result["states"]["grown"] == result["states"]["same_radius"] == "ready"


@pytest.mark.parametrize("all_actions", [False, True])
def test_reuse_plane_membership_precedes_its_own_final_parallel_relationship(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    all_actions: bool,
) -> None:
    workspace, payload, base = fixture
    payload.update(
        nodes=[
            base["scan"],
            base["top_face"],
            selection("target_top", workspace.default.plane_ids),
            fit("source_plane", "top_face", "plane"),
            {
                "id": "reuse",
                "label": "Reuse plane family",
                "operation": "feature_reuse",
                "fits": ["source_plane"],
                "lineage": ["scan", "top_face", "source_plane"],
                "reference_selection": "top_face",
                "target_selections": ["target_top"],
                "tangent_margin": 0,
                "normal_margin": 0.25,
                "normal_angle_degrees": 35,
                "equal_corresponding_dimensions": True,
            },
            {
                "id": "transfer",
                "label": "Transferred plane selection",
                "operation": "reuse_selection",
                "reuse": "reuse",
                "fit": "source_plane",
                "source_selection": "top_face",
                "target_selection": "target_top",
            },
            fit("copy_plane", "transfer", "plane"),
            {
                "id": "parallel",
                "label": "Final family parallel planes",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["source_plane", "copy_plane"],
                "managed_by": "reuse",
                "managed_key": "parallel/source_plane",
            },
        ],
        output="parallel",
    )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    first = cast(
        dict[str, Any],
        graph.evaluate(
            str(graph.snapshot()["token"]),
            target=None if all_actions else "parallel",
            all_actions=all_actions,
        ),
    )
    assert (
        first["states"]["transfer"]
        == first["states"]["copy_plane"]
        == first["states"]["parallel"]
        == "ready"
    )
    assert first["memberships"]["transfer"]
    assert all(
        first["results"][name]["resolved_by"] == "plane_relationship"
        for name in ("source_plane", "copy_plane")
    )
    np.testing.assert_allclose(
        first["results"]["source_plane"]["plane_equation"][:3],
        first["results"]["copy_plane"]["plane_equation"][:3],
        atol=1e-12,
    )
    # Publishing the downstream relationship must not invalidate the earlier
    # membership-transfer stage or create an endless warm recompute loop.
    warmed = graph.evaluate(str(first["token"]), all_actions=True)
    assert warmed == first
    replay = FeatureGraph(workspace, Recipe.model_validate(first["recipe"]))
    replayed = replay.evaluate(str(replay.snapshot()["token"]), all_actions=True)
    assert replayed["results"] == first["results"]
    assert replayed["memberships"] == first["memberships"]


@pytest.mark.parametrize("kind", ["plane", "cylinder"])
@pytest.mark.parametrize("all_actions", [False, True])
@pytest.mark.parametrize("fail_prior", [False, True])
def test_reuse_preserves_prior_original_constraints_when_final_group_adds_copy(
    fixture: tuple[NozzleWorkspace, dict[str, Any], dict[str, dict[str, Any]]],
    kind: str,
    all_actions: bool,
    fail_prior: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, payload, base = fixture
    prior_recipe: dict[str, Any]
    if kind == "plane":
        prior_recipe = plane_component_recipe(
            workspace, payload, base, transitive=False
        )
        prior_recipe["nodes"] = [
            node
            for node in prior_recipe["nodes"]
            if node["id"] in {"scan", "a_selection", "b_selection", "a", "b", "ab"}
        ]
        prior_recipe["output"] = "ab"
    else:
        ids = workspace.default.lateral_ids
        a_ids, b_ids = ids[::2], ids[1::2]
        workspace.local[a_ids] = cylinder_points(len(a_ids), 3)
        workspace.local[b_ids] = cylinder_points(len(b_ids), 5)
        prior_recipe = {
            **payload,
            "nodes": [
                base["scan"],
                selection("a_selection", a_ids),
                selection("b_selection", b_ids),
                fit("a", "a_selection", kind),
                fit("b", "b_selection", kind),
                {
                    "id": "equal_radius",
                    "label": "Original equal radii",
                    "operation": "equal_radii",
                    "surfaces": ["a", "b"],
                },
            ],
            "output": "equal_radius",
        }
    baseline = run(workspace, prior_recipe, all_actions=True)
    expected = baseline["results"]["a"]
    original = baseline["derived"]["a"]
    nodes: list[dict[str, Any]] = deepcopy(prior_recipe["nodes"])
    if kind == "cylinder":
        # The final recipe moves the single all-equal action after its added
        # copy; the prior-stage solve must still retain original members A+B.
        nodes = [node for node in nodes if node["id"] != "equal_radius"]
    typed = Recipe.model_validate({**payload, "nodes": nodes, "output": "a"})
    lineage = discover_reuse_lineage(["a"], {node.id: node for node in typed.nodes})
    source_ids = next(node["ids"] for node in nodes if node["id"] == "a_selection")
    nodes.extend(
        [
            selection("a_target", source_ids),
            {
                "id": "reuse",
                "label": "Reuse constrained source",
                "operation": "feature_reuse",
                "fits": ["a"],
                "lineage": lineage,
                "reference_selection": "a_selection",
                "target_selections": ["a_target"],
                "tangent_margin": 0,
                "normal_margin": 0.25,
                "normal_angle_degrees": 35,
                "equal_corresponding_dimensions": True,
            },
            {
                "id": "transfer",
                "label": "Transferred constrained selection",
                "operation": "reuse_selection",
                "reuse": "reuse",
                "fit": "a",
                "source_selection": "a_selection",
                "target_selection": "a_target",
            },
            fit("copy", "transfer", kind),
            {
                "id": "final_relation",
                "label": "Final family relationship",
                "operation": "plane_relationship" if kind == "plane" else "equal_radii",
                "surfaces": ["a", "copy"] if kind == "plane" else ["a", "b", "copy"],
                **({"relation": "parallel"} if kind == "plane" else {}),
                "managed_by": "reuse",
                "managed_key": "final/source",
            },
        ]
    )
    captured: list[dict[str, Any]] = []

    def record_transfer_geometry(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.append(deepcopy(args[2]))
        return build_selection_region(*args, **kwargs)

    monkeypatch.setattr(
        feature_graph, "build_selection_region", record_transfer_geometry
    )
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate({**payload, "nodes": nodes, "output": "final_relation"}),
    )
    if fail_prior:

        def failed_prior_geometry(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise ValueError("bad prior geometry")

        monkeypatch.setattr(
            feature_graph,
            "fit_plane_relationships" if kind == "plane" else "fit_equal_radii",
            failed_prior_geometry,
        )
        with pytest.raises(ValueError, match="bad prior geometry"):
            _ = graph.evaluate(
                str(graph.snapshot()["token"]),
                target=None if all_actions else "final_relation",
                all_actions=all_actions,
            )
        failed = cast(dict[str, Any], graph.snapshot())
        assert failed["states"]["transfer"] == "failed"
        assert "prior source geometry" in failed["errors"]["transfer"]
        assert "bad prior geometry" in failed["errors"]["transfer"]
        assert failed["states"]["final_relation"] != "ready"
        assert "final_relation" not in failed["results"]
        assert not captured
        return
    first = cast(
        dict[str, Any],
        graph.evaluate(
            str(graph.snapshot()["token"]),
            target=None if all_actions else "final_relation",
            all_actions=all_actions,
        ),
    )
    assert len(captured) == 1
    if kind == "plane":
        np.testing.assert_allclose(
            captured[0]["plane_equation"][:3],
            expected["plane_equation"][:3],
            atol=1e-12,
        )
        assert not np.allclose(
            captured[0]["plane_equation"][:3], original["parameters"][:3], atol=1e-4
        )
    else:
        assert captured[0]["parameters"][4] == pytest.approx(expected["parameters"][4])
        assert captured[0]["parameters"][4] != pytest.approx(original["parameters"][4])
        assert first["results"]["final_relation"]["value"] != pytest.approx(
            expected["parameters"][4]
        )
    assert (
        first["states"]["transfer"]
        == first["states"]["copy"]
        == first["states"]["final_relation"]
        == "ready"
    )
    assert graph.evaluate(str(first["token"]), all_actions=True) == first
    assert len(captured) == 1
