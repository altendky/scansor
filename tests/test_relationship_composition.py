"""Published geometry must satisfy composed local and cross-occurrence constraints."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    PlaneDefinition,
    PlaneRelationship,
    Recipe,
    SurfaceFit,
)
from experiments.feature_reuse import surface_region_frame
from experiments.mesh_cylinder_fit import Array
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE
from experiments.surface_primitives import basis, primitive
from scansor.constrained_least_squares import (
    ConstrainedLeastSquaresFailure,
    ConstrainedLeastSquaresResult,
    Evaluation,
    solve_constrained_least_squares,
)


def _token(graph: FeatureGraph) -> str:
    return str(graph.snapshot()["token"])


@pytest.fixture
def composed_graph() -> FeatureGraph:
    """Conflicting observations exercise exact constraints, not lucky input poses."""
    workspace = NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))
    original = Recipe.model_validate_json(
        Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json").read_text()
    ).model_dump()
    nodes: list[dict[str, Any]] = [original["nodes"][0]]
    cursor = 0

    def observations(name: str, points: Array) -> str:
        nonlocal cursor
        ids = list(range(cursor, cursor + len(points)))
        cursor += len(points)
        workspace.local[ids] = points
        workspace.data.weights[ids] = 1.0
        nodes.append(
            {
                "id": name,
                "label": name,
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            }
        )
        return name

    for index, (tilt, height) in enumerate(
        (((0.04, -0.03), 3.0), ((-0.025, 0.045), 3.15), ((0.01, 0.02), 5.0))
    ):
        axis = np.array([*tilt, 1.0])
        axis /= np.linalg.norm(axis)
        u, v = basis(axis)
        origin = np.array([index * 8.0, index * 2.0, 0.0])
        theta = np.tile(np.linspace(0, 2 * np.pi, 24, endpoint=False), 4)
        axial = np.repeat(np.linspace(-1, 4, 4), 24)
        radius = 2.0 + index * 0.025
        side = observations(
            f"side_selection_{index}",
            origin
            + axial[:, None] * axis
            + radius * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v),
        )
        grid = np.array([(x, y) for x in (-1.5, -0.5, 0.5, 1.5) for y in (-1, 0, 1)])
        shoulder = observations(
            f"shoulder_selection_{index}",
            origin + height * axis + grid[:, :1] * u + grid[:, 1:] * v,
        )
        clock = observations(
            f"clock_selection_{index}",
            origin + 1.6 * v + grid[:, :1] * axis + grid[:, 1:] * u,
        )
        nodes.append(
            {
                "id": f"axis_{index}",
                "label": f"axis_{index}",
                "operation": "axis",
                "initial_parameters": [*origin[:2], *tilt],
            }
        )
        for kind, construction, selection in (
            ("shoulder", "perpendicular_to_axis", shoulder),
            ("clock", "parallel_to_axis", clock),
        ):
            nodes.extend(
                [
                    {
                        "id": f"{kind}_datum_{index}",
                        "label": f"{kind}_datum_{index}",
                        "operation": "reference_plane",
                        "axis": f"axis_{index}",
                        "construction": construction,
                        "initial_angle_degrees": None if kind == "shoulder" else 0.0,
                    },
                    {
                        "id": f"{kind}_{index}",
                        "label": f"{kind}_{index}",
                        "operation": "fit",
                        "kind": "plane",
                        "reference_plane": f"{kind}_datum_{index}",
                        "selections": [selection],
                    },
                ]
            )
        nodes.append(
            {
                "id": f"side_{index}",
                "label": f"side_{index}",
                "operation": "fit",
                "kind": "cylinder",
                "axis": f"axis_{index}",
                "selections": [side],
                "axial_domain": [-2, 6],
            }
        )
    plate = observations(
        "plate_selection",
        np.array([(x, y, 0.0) for x in (-2, 5, 12) for y in (-2, 2, 6)]),
    )
    nodes.extend(
        [
            {
                "id": "plate",
                "label": "plate",
                "operation": "fit",
                "kind": "plane",
                "selections": [plate],
            },
            {
                "id": "upright_parallel",
                "label": "upright_parallel",
                "operation": "plane_relationship",
                "relation": "parallel",
                "surfaces": ["plate", "shoulder_0", "shoulder_1", "shoulder_2"],
            },
            {
                "id": "same_height",
                "label": "same_height",
                "operation": "plane_relationship",
                "relation": "coincident",
                "surfaces": ["shoulder_0", "shoulder_1"],
            },
            {
                "id": "same_radii",
                "label": "same_radii",
                "operation": "equal_radii",
                "surfaces": [f"side_{index}" for index in range(3)],
            },
            {
                "id": "consumer",
                "label": "consumer",
                "operation": "surface_intersection",
                "first": {"feature": "shoulder_0"},
                "second": {"feature": "clock_0"},
            },
        ]
    )
    original.update(nodes=nodes, output="consumer")
    return FeatureGraph(workspace, Recipe.model_validate(original))


def _assert_composed_geometry(graph: FeatureGraph, state: dict[str, Any]) -> None:
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    results = state["results"]
    normal = np.asarray(results["plate"]["plane_equation"][:3])
    for index in range(3):
        axis = np.asarray(primitive(results[f"side_{index}"])["axis"])
        assert np.linalg.norm(np.cross(axis, normal)) < 1e-10
        np.testing.assert_allclose(
            results[f"axis_{index}"]["axis_display"], axis, atol=1e-10
        )
        for kind in ("shoulder", "clock"):
            fitted = results[f"{kind}_{index}"]
            equation = np.asarray(fitted["plane_equation"])
            datum = results[f"{kind}_datum_{index}"]
            np.testing.assert_allclose(datum["plane_equation"], equation, atol=1e-10)
            if kind == "clock":
                assert abs(equation[:3] @ axis) < 1e-10
            else:
                assert np.linalg.norm(np.cross(equation[:3], axis)) < 1e-10
    np.testing.assert_allclose(
        results["shoulder_0"]["plane_equation"],
        results["shoulder_1"]["plane_equation"],
        atol=1e-10,
    )
    assert (
        abs(
            results["shoulder_2"]["plane_equation"][3]
            - results["shoulder_0"]["plane_equation"][3]
        )
        > 1.0
    )
    radii = [results[f"side_{index}"]["parameters"][4] for index in range(3)]
    np.testing.assert_allclose(radii, radii[0], atol=1e-12)
    for name in [
        "plate",
        *[
            f"{kind}_{index}"
            for index in range(3)
            for kind in ("side", "shoulder", "clock")
        ],
    ]:
        fitted = results[name]
        points = graph.workspace.local[fitted["ids"]]
        geometry = primitive(fitted)
        axis = np.asarray(geometry["axis"])
        if geometry["kind"] == "plane":
            residual = points @ axis - geometry["offset"]
        else:
            relative = points - geometry["origin"]
            radial = relative - (relative @ axis)[:, None] * axis
            residual = np.linalg.norm(radial, axis=1) - geometry["radius"]
        np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-10)
        weights = graph.workspace.data.weights[fitted["ids"]]
        expected = np.sqrt(weights @ residual**2 / weights.sum())
        assert fitted["weighted_rms"] == pytest.approx(expected, abs=1e-10)


def test_plane_relationships_preserve_complete_axis_components(
    composed_graph: FeatureGraph,
) -> None:
    state = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    _assert_composed_geometry(composed_graph, state)


def test_composed_relationships_reuse_warm_results(
    composed_graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = composed_graph.evaluate(_token(composed_graph), all_actions=True)

    def unexpected_evaluation(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("unchanged composed geometry must not be solved again")

    monkeypatch.setattr(composed_graph, "evaluate", unexpected_evaluation)
    warm = composed_graph.ensure_current(_token(composed_graph), all_actions=True)
    assert warm["results"] == expected["results"]


def test_relationship_edit_invalidates_all_coupled_geometry(
    composed_graph: FeatureGraph,
) -> None:
    _ = composed_graph.evaluate(_token(composed_graph), all_actions=True)
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    selection = next(
        node for node in payload["nodes"] if node["id"] == "shoulder_selection_1"
    )
    selection["ids"] = selection["ids"][::2]
    recipe = Recipe.model_validate(payload)
    changed = cast(
        dict[str, Any], composed_graph.replace(recipe, _token(composed_graph))
    )
    for key in (
        "same_height",
        "upright_parallel",
        "consumer",
        "axis_0",
        "side_0",
        "clock_0",
    ):
        assert changed["states"][key] == "stale"
    replay = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), target="consumer"),
    )
    fresh = FeatureGraph(composed_graph.workspace, recipe)
    expected = cast(dict[str, Any], fresh.evaluate(_token(fresh), all_actions=True))
    _assert_composed_geometry(composed_graph, replay)
    for name in expected["results"]:
        if (
            name.startswith(("side_", "shoulder_", "clock_"))
            and "parameters" in expected["results"][name]
        ):
            np.testing.assert_allclose(
                replay["results"][name]["parameters"],
                expected["results"][name]["parameters"],
                atol=1e-10,
            )


def test_named_radius_context_reads_final_composed_surfaces(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    for name, first in (
        ("named_context_circle", {"feature": "same_radii", "surface": "side_0"}),
        ("direct_circle", {"feature": "side_0"}),
    ):
        payload["nodes"].append(
            {
                "id": name,
                "label": name,
                "operation": "surface_intersection",
                "first": first,
                "second": {"feature": "shoulder_0"},
            }
        )
    payload["output"] = "named_context_circle"
    _ = composed_graph.replace(Recipe.model_validate(payload), _token(composed_graph))
    targeted = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), target="named_context_circle"),
    )
    assert targeted["results"]["named_context_circle"]["kind"] == "circle"
    assert targeted["results"]["named_context_circle"]["radius"] == pytest.approx(
        targeted["results"]["side_0"]["parameters"][4], abs=1e-10
    )
    assert targeted["states"]["same_height"] == "ready"
    assert targeted["states"]["upright_parallel"] == "ready"
    state = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    _assert_composed_geometry(composed_graph, state)
    named = state["results"]["named_context_circle"]
    direct = state["results"]["direct_circle"]
    assert named["kind"] == direct["kind"] == "circle"
    assert named["radius"] == pytest.approx(direct["radius"], abs=1e-10)
    np.testing.assert_allclose(
        named["center_display"], direct["center_display"], atol=1e-10
    )
    np.testing.assert_allclose(
        named["axis_display"], direct["axis_display"], atol=1e-10
    )


def test_source_fit_axis_tracks_its_jointly_refitted_cylinder(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    axis = next(node for node in payload["nodes"] if node["id"] == "axis_0")
    initial = axis.pop("initial_parameters")
    source = next(node for node in payload["nodes"] if node["id"] == "side_0").copy()
    source.update(id="source_cylinder", label="source_cylinder", axis=None)
    payload["nodes"].insert(payload["nodes"].index(axis), source)
    axis["source_fit"] = "source_cylinder"
    radius_relation = next(
        node for node in payload["nodes"] if node["id"] == "same_radii"
    )
    radius_relation["surfaces"].append("source_cylinder")
    _ = composed_graph.replace(Recipe.model_validate(payload), _token(composed_graph))
    state = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    _assert_composed_geometry(composed_graph, state)
    results = state["results"]
    source_geometry = primitive(results["source_cylinder"])
    np.testing.assert_allclose(
        results["axis_0"]["axis_display"], source_geometry["axis"], atol=1e-10
    )
    np.testing.assert_allclose(
        results["axis_0"]["point_display"], source_geometry["origin"], atol=1e-10
    )
    original_axis = np.array([initial[2], initial[3], 1.0])
    original_axis /= np.linalg.norm(original_axis)
    assert np.linalg.norm(np.cross(original_axis, source_geometry["axis"])) > 1e-3


def test_source_fit_point_tracks_its_jointly_refitted_sphere(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    cursor = 1 + max(
        vertex
        for node in payload["nodes"]
        if node["operation"] == "selection"
        for vertex in node["ids"]
    )
    center = np.array([4.0, 8.0, 1.0])
    latitude = np.tile(np.linspace(0.2, 1.2, 5), 12)
    longitude = np.repeat(np.linspace(0, 2 * np.pi, 12, endpoint=False), 5)
    directions = np.column_stack(
        (
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        )
    )
    additions: list[dict[str, Any]] = []
    for index, radius in enumerate((2.25, 2.2)):
        ids = list(range(cursor, cursor + len(directions)))
        cursor += len(directions)
        composed_graph.workspace.local[ids] = center + radius * directions
        composed_graph.workspace.data.weights[ids] = 1.0
        additions.append(
            {
                "id": f"sphere_selection_{index}",
                "label": f"sphere_selection_{index}",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            }
        )
    additions.extend(
        [
            {
                "id": "source_sphere",
                "label": "source_sphere",
                "operation": "fit",
                "kind": "sphere",
                "selections": ["sphere_selection_0"],
            },
            {
                "id": "source_center",
                "label": "source_center",
                "operation": "point",
                "source_fit": "source_sphere",
            },
            {
                "id": "bound_sphere",
                "label": "bound_sphere",
                "operation": "fit",
                "kind": "sphere",
                "point": "source_center",
                "selections": ["sphere_selection_1"],
            },
        ]
    )
    relation = next(node for node in payload["nodes"] if node["id"] == "same_radii")
    position = payload["nodes"].index(relation)
    payload["nodes"][position:position] = additions
    relation["surfaces"].extend(["source_sphere", "bound_sphere"])
    _ = composed_graph.replace(Recipe.model_validate(payload), _token(composed_graph))
    state = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    _assert_composed_geometry(composed_graph, state)
    results = state["results"]
    expected = results["source_sphere"]["parameters"][:3]
    for field in ("coordinates", "point_display"):
        np.testing.assert_allclose(
            results["source_center"][field], expected, atol=1e-10
        )
    np.testing.assert_allclose(
        results["bound_sphere"]["parameters"][:3], expected, atol=1e-10
    )
    assert np.linalg.norm(np.asarray(expected) - center) > 1e-3


def test_unbound_source_fit_axis_and_frame_use_final_joint_geometry(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    selection = deepcopy(
        next(node for node in payload["nodes"] if node["id"] == "side_selection_0")
    )
    selection.update(id="partial_source_selection", label="Partial source cylinder")
    # Change arc coverage with height so enforcing the shared radius moves both
    # the standalone cylinder's anchor and its direction.
    selection["ids"] = [
        vertex
        for index, vertex in enumerate(selection["ids"])
        if 2 * (index // 24) <= index % 24 < 12 + 2 * (index // 24)
    ]
    source = deepcopy(next(node for node in payload["nodes"] if node["id"] == "side_0"))
    source.update(
        id="external_source_cylinder",
        label="External source cylinder",
        axis=None,
        selections=[selection["id"]],
    )
    relation = next(node for node in payload["nodes"] if node["id"] == "same_radii")
    position = payload["nodes"].index(relation)
    payload["nodes"][position:position] = [
        selection,
        source,
        {
            "id": "external_axis",
            "label": "External source-fit axis",
            "operation": "axis",
            "source_fit": source["id"],
        },
        {
            "id": "external_datum",
            "label": "External unfitted datum",
            "operation": "reference_plane",
            "axis": "external_axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 2.0,
        },
        {
            "id": "external_origin",
            "label": "External frame origin",
            "operation": "point",
            "initial_coordinates": [0.0, 0.0, 0.0],
        },
    ]
    relation["surfaces"].append(source["id"])
    payload["nodes"].append(
        {
            "id": "external_frame",
            "label": "External source-fit frame",
            "operation": "frame",
            "origin_point": "external_origin",
            "primary_reference": "external_datum",
            "primary_output_axis": "+Z",
            "secondary_reference": "clock_datum_0",
            "secondary_output_axis": "+X",
        }
    )
    payload["output"] = "external_frame"
    recipe = Recipe.model_validate(payload)
    _ = composed_graph.replace(recipe, _token(composed_graph))

    def check(state: dict[str, Any]) -> None:
        assert not state["errors"]
        results = state["results"]
        geometry = primitive(results[source["id"]])
        raw = primitive(state["derived"][source["id"]])
        assert np.linalg.norm(np.cross(geometry["axis"], raw["axis"])) > 1e-3
        axis = results["external_axis"]
        np.testing.assert_allclose(
            axis["point_display"], geometry["origin"], atol=1e-10
        )
        np.testing.assert_allclose(axis["axis_display"], geometry["axis"], atol=1e-10)
        datum = results["external_datum"]
        np.testing.assert_allclose(
            datum["normal_display"], geometry["axis"], atol=1e-10
        )
        expected_offset = np.asarray(geometry["axis"]) @ geometry["origin"] + 2.0
        assert datum["plane_equation"][3] == pytest.approx(expected_offset, abs=1e-10)
        np.testing.assert_allclose(
            results["external_frame"]["z_axis_display"], geometry["axis"], atol=1e-10
        )

    targeted = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), target="external_frame"),
    )
    check(targeted)
    warm = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    check(warm)
    fresh = FeatureGraph(composed_graph.workspace, recipe)
    replay = cast(
        dict[str, Any], fresh.evaluate(_token(fresh), target="external_frame")
    )
    check(replay)
    for state in (warm, replay):
        np.testing.assert_allclose(
            state["results"]["external_frame"]["rotation"],
            targeted["results"]["external_frame"]["rotation"],
            atol=1e-10,
        )


def test_unbound_source_fit_point_frame_and_scale_use_final_joint_geometry(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    cursor = 1 + max(
        vertex
        for node in payload["nodes"]
        if node["operation"] == "selection"
        for vertex in node["ids"]
    )
    center = np.array([4.0, 8.0, 1.0])
    latitude = np.tile(np.linspace(0.2, 1.2, 5), 12)
    longitude = np.repeat(np.linspace(0, 2 * np.pi, 12, endpoint=False), 5)
    directions = np.column_stack(
        (
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        )
    )
    ids = list(range(cursor, cursor + len(directions)))
    composed_graph.workspace.local[ids] = center + 2.25 * directions
    composed_graph.workspace.data.weights[ids] = 1.0
    relation = next(node for node in payload["nodes"] if node["id"] == "same_radii")
    position = payload["nodes"].index(relation)
    payload["nodes"][position:position] = [
        {
            "id": "external_sphere_selection",
            "label": "External sphere selection",
            "operation": "selection",
            "source": "scan",
            "ids": ids,
        },
        {
            "id": "external_source_sphere",
            "label": "External source sphere",
            "operation": "fit",
            "kind": "sphere",
            "selections": ["external_sphere_selection"],
        },
        {
            "id": "external_center",
            "label": "External source-fit center",
            "operation": "point",
            "source_fit": "external_source_sphere",
        },
        {
            "id": "external_scale_anchor",
            "label": "External scale anchor",
            "operation": "point",
            "initial_coordinates": [0.0, 0.0, 0.0],
        },
    ]
    relation["surfaces"].append("external_source_sphere")
    payload["nodes"].extend(
        [
            {
                "id": "external_frame",
                "label": "External center frame",
                "operation": "frame",
                "origin_point": "external_center",
                "primary_reference": "axis_0",
                "primary_output_axis": "+Z",
                "secondary_reference": "clock_datum_0",
                "secondary_output_axis": "+X",
            },
            {
                "id": "external_scale",
                "label": "External center scale",
                "operation": "scale",
                "distances": [
                    {
                        "first_point": "external_scale_anchor",
                        "second_point": "external_center",
                        "known_distance": 10.0,
                    }
                ],
            },
            {
                "id": "external_transform",
                "label": "External center transform",
                "operation": "transform",
                "frame": "external_frame",
                "scale": "external_scale",
            },
        ]
    )
    payload["output"] = "external_transform"
    recipe = Recipe.model_validate(payload)
    _ = composed_graph.replace(recipe, _token(composed_graph))

    def check(state: dict[str, Any]) -> None:
        assert not state["errors"]
        results = state["results"]
        expected = np.asarray(results["external_source_sphere"]["parameters"][:3])
        assert np.linalg.norm(expected - center) > 1e-3
        for field in ("coordinates", "point_display"):
            np.testing.assert_allclose(
                results["external_center"][field], expected, atol=1e-10
            )
        np.testing.assert_allclose(
            results["external_frame"]["origin_display"], expected, atol=1e-10
        )
        distance = float(np.linalg.norm(expected))
        scale = results["external_scale"]
        assert scale["observations"][0]["measured_distance"] == pytest.approx(
            distance, abs=1e-10
        )
        assert scale["scale"] == pytest.approx(10.0 / distance, abs=1e-10)
        matrix = np.asarray(results["external_transform"]["matrix"])
        np.testing.assert_allclose(
            matrix @ np.r_[expected, 1.0], [0.0, 0.0, 0.0, 1.0], atol=1e-10
        )

    targeted = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), target="external_transform"),
    )
    check(targeted)
    warm = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    check(warm)
    fresh = FeatureGraph(composed_graph.workspace, recipe)
    replay = cast(
        dict[str, Any], fresh.evaluate(_token(fresh), target="external_transform")
    )
    check(replay)
    for state in (warm, replay):
        np.testing.assert_allclose(
            state["results"]["external_transform"]["matrix"],
            targeted["results"]["external_transform"]["matrix"],
            atol=1e-10,
        )


def test_reuse_source_keeps_plane_and_radius_constraints_across_radius_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from experiments import feature_graph as module

    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    payload = Recipe.model_validate_json(RECIPE.read_bytes()).model_dump()
    original = {node["id"]: node for node in payload["nodes"]}
    source_outer = "fit_6854e8ef6490471eac2deb01287f0ec4"
    source_shoulder = "fit_56e9d6f9a2a344629c52cba6931add0f"
    duplicate = deepcopy(original[source_outer])
    duplicate.update(
        id="source_outer_peer", label="source_outer_peer", axis=None, group_id=None
    )
    source_constraints = [
        duplicate,
        original["plate-top-selection"],
        original["plate-top-fit"],
        {
            "id": "source_planes_parallel",
            "label": "source_planes_parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": [source_shoulder, "plate-top-fit"],
        },
    ]
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["id"]
        not in {"plate-top-selection", "plate-top-fit", "plate-shoulders-parallel"}
    ]
    reuse = original["feature_reuse_boss_d"]
    position = payload["nodes"].index(reuse)
    payload["nodes"][position:position] = source_constraints
    radius_bridge = original["equal_radii_b1d585508886418780432add88f336ff"]
    radius_bridge["surfaces"].append("source_outer_peer")
    control_payload = deepcopy(payload)
    control_payload["nodes"] = control_payload["nodes"][
        : position + len(source_constraints)
    ]
    prior_radii = deepcopy(radius_bridge)
    prior_radii["surfaces"] = [source_outer, "source_outer_peer"]
    prior_radii.update(managed_by=None, managed_key=None)
    control_payload["nodes"].append(prior_radii)
    control_payload["output"] = "source_planes_parallel"
    control = FeatureGraph(
        NozzleWorkspace(root / "scan-coarse"), Recipe.model_validate(control_payload)
    )
    expected = cast(dict[str, Any], control.evaluate(_token(control), all_actions=True))
    captured: dict[str, dict[str, Any]] = {}
    original_region_frame = surface_region_frame

    def capture_prior(points: Array, fitted: dict[str, Any]) -> tuple[Array, Array]:
        for name in (source_outer, source_shoulder):
            if fitted.get("ids") == expected["results"][name]["ids"]:
                captured[name] = deepcopy(fitted)
        return original_region_frame(points, fitted)

    monkeypatch.setattr(module, "surface_region_frame", capture_prior)
    graph = FeatureGraph(
        NozzleWorkspace(root / "scan-coarse"), Recipe.model_validate(payload)
    )
    state = cast(dict[str, Any], graph.evaluate(_token(graph), all_actions=True))
    assert not state["errors"]
    assert set(state["states"].values()) == {"ready"}
    assert set(captured) == {source_outer, source_shoulder}
    for name, fitted in captured.items():
        np.testing.assert_allclose(
            fitted["parameters"], expected["results"][name]["parameters"], atol=1e-9
        )
    transfer = state["results"]["reuse_selection_boss_d_outer"]
    assert (
        "source_planes_parallel"
        in transfer["source_geometry_stage"]["excluded_relationships"]
    )

    def unexpected_frame(*_args: object, **_kwargs: object) -> tuple[Array, Array]:
        raise AssertionError("warm graph must not transfer selections again")

    monkeypatch.setattr(module, "surface_region_frame", unexpected_frame)
    warm = graph.ensure_current(_token(graph), all_actions=True)
    assert warm["results"] == state["results"]


@pytest.mark.parametrize(
    ("scale", "translation"),
    [(1e-3, [0.8, -1.2, 0.5]), (1e3, [8e6, -1.2e7, 5e6])],
)
def test_composed_fit_is_covariant_under_scaling_and_translation(
    composed_graph: FeatureGraph, scale: float, translation: list[float]
) -> None:
    baseline = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    shift = np.asarray(translation)
    for node in payload["nodes"]:
        if node["operation"] == "axis":
            cx, cy, a, b = node["initial_parameters"]
            node["initial_parameters"] = [
                scale * cx + shift[0] - a * shift[2],
                scale * cy + shift[1] - b * shift[2],
                a,
                b,
            ]
        elif node["operation"] == "fit":
            node["axial_domain"] = [scale * value for value in node["axial_domain"]]
    composed_graph.workspace.local[:] = scale * composed_graph.workspace.local + shift
    fresh = FeatureGraph(composed_graph.workspace, Recipe.model_validate(payload))
    transformed = cast(dict[str, Any], fresh.evaluate(_token(fresh), all_actions=True))
    assert not transformed["errors"]
    for name in [
        "plate",
        *[
            f"{kind}_{index}"
            for index in range(3)
            for kind in ("side", "shoulder", "clock")
        ],
    ]:
        before = primitive(baseline["results"][name])
        after = primitive(transformed["results"][name])
        normal = np.asarray(before["axis"])
        np.testing.assert_allclose(after["axis"], normal, atol=2e-9)
        if before["kind"] == "plane":
            assert after["offset"] == pytest.approx(
                scale * before["offset"] + np.asarray(after["axis"]) @ shift,
                abs=scale * 1e-7,
            )
        else:
            expected_origin = scale * np.asarray(before["origin"]) + shift
            expected_origin -= expected_origin[2] / normal[2] * normal
            np.testing.assert_allclose(
                after["origin"], expected_origin, atol=scale * 2e-5, rtol=0.0
            )
            assert after["radius"] == pytest.approx(
                scale * before["radius"], abs=scale * 1e-8
            )
        np.testing.assert_allclose(
            transformed["results"][name]["residuals"],
            scale * np.asarray(baseline["results"][name]["residuals"]),
            atol=scale * 2e-8,
            rtol=1e-6,
        )


def test_parallel_relationship_reports_conflicting_fixed_axes(
    composed_graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    for index in range(3):
        axis = next(node for node in payload["nodes"] if node["id"] == f"axis_{index}")
        _ = axis.pop("initial_parameters")
        initializer = next(
            node for node in payload["nodes"] if node["id"] == f"side_{index}"
        ).copy()
        initializer.update(
            id=f"fixed_side_{index}", label=f"fixed_side_{index}", axis=None
        )
        axis["source_fit"] = initializer["id"]
        position = payload["nodes"].index(axis)
        payload["nodes"].insert(position, initializer)
    _ = composed_graph.replace(Recipe.model_validate(payload), _token(composed_graph))
    with pytest.raises(ConstrainedLeastSquaresFailure) as failure:
        _ = composed_graph.evaluate(_token(composed_graph), all_actions=True)
    state = cast(dict[str, Any], composed_graph.snapshot())
    assert state["states"]["upright_parallel"] == "failed"
    assert state["states"]["consumer"] == "blocked"
    assert "consumer" not in state["results"]
    assert state["diagnostics"]["consumer"]["blocked_by"]
    assert failure.value.code == "infeasible-constraints"
    assert failure.value.diagnostics is not None
    for key in ("upright_parallel", "same_height"):
        diagnostic = state["diagnostics"][key]
        assert diagnostic["kind"] == "constrained_solver_failure"
        assert diagnostic["code"] == failure.value.code
        assert diagnostic["solver"]["constraint_evaluations"] > 0
        assert diagnostic["solver"]["constraint_violation"] > 1e-11
        assert diagnostic["solver"]["condition"] == "Infinity"
        assert key not in state["results"] and key not in state["derived"]
    _ = json.dumps(state, allow_nan=False)


def test_boss_relationships_keep_upright_axes_and_reused_datums_coherent(
    tmp_path: Path,
) -> None:
    root = tmp_path / "boss"
    _ = publish_fixture(root, DEFINITION, realization_ids=("scan-coarse",))
    payload = Recipe.model_validate_json(RECIPE.read_bytes()).model_dump()
    payload["nodes"].append(
        {
            "id": "same_height_shoulders",
            "label": "Same-height shoulders",
            "operation": "plane_relationship",
            "relation": "coincident",
            "surfaces": [
                "fit_56e9d6f9a2a344629c52cba6931add0f",
                "fit_target_3_shoulder",
            ],
        }
    )
    graph = FeatureGraph(
        NozzleWorkspace(root / "scan-coarse"), Recipe.model_validate(payload)
    )
    state = cast(dict[str, Any], graph.evaluate(_token(graph), all_actions=True))
    assert not state["errors"]
    results = state["results"]
    recipe = Recipe.model_validate(state["recipe"])
    fits = [node for node in recipe.nodes if isinstance(node, SurfaceFit)]
    upright = next(
        node for node in recipe.nodes if node.id == "plate-shoulders-parallel"
    )
    assert isinstance(upright, PlaneRelationship)
    normal = np.asarray(results["plate-top-fit"]["plane_equation"][:3])
    for shoulder_id in upright.surfaces:
        shoulder = next(node for node in fits if node.id == shoulder_id)
        np.testing.assert_allclose(
            np.cross(results[shoulder_id]["plane_equation"][:3], normal),
            0.0,
            atol=1e-10,
        )
        if shoulder.reference_plane is None:
            continue
        datum = next(
            node for node in recipe.nodes if node.id == shoulder.reference_plane
        )
        assert isinstance(datum, PlaneDefinition)
        axis = np.asarray(results[datum.axis]["axis_display"])
        assert np.linalg.norm(np.cross(axis, normal)) < 1e-10
        for fitted in fits:
            if fitted.axis == datum.axis:
                assert (
                    np.linalg.norm(
                        np.cross(primitive(results[fitted.id])["axis"], axis)
                    )
                    < 1e-10
                )
            if fitted.reference_plane is not None:
                fitted_datum = next(
                    node for node in recipe.nodes if node.id == fitted.reference_plane
                )
                assert isinstance(fitted_datum, PlaneDefinition)
                if (
                    fitted_datum.axis == datum.axis
                    and fitted_datum.construction == "parallel_to_axis"
                ):
                    assert (
                        abs(np.asarray(results[fitted.id]["plane_equation"][:3]) @ axis)
                        < 1e-10
                    )
    np.testing.assert_allclose(
        results["fit_56e9d6f9a2a344629c52cba6931add0f"]["plane_equation"],
        results["fit_target_3_shoulder"]["plane_equation"],
        atol=1e-10,
    )
    assert (
        abs(
            results["fit_target_2_shoulder"]["plane_equation"][3]
            - results["fit_target_3_shoulder"]["plane_equation"][3]
        )
        > 1.0
    )


@pytest.mark.parametrize("construction", ["contains_axis", "parallel_to_axis"])
def test_joint_geometry_analytic_jacobians_and_mixed_radius_constraints(
    monkeypatch: pytest.MonkeyPatch, construction: str
) -> None:
    from experiments import fit_relationship_component as module

    kernel = solve_constrained_least_squares
    checked = False

    def check_jacobians(
        initial: Array,
        observations: Evaluation,
        equalities: Evaluation,
        **kwargs: Any,
    ) -> ConstrainedLeastSquaresResult:
        nonlocal checked
        for evaluate in (observations, equalities):
            _, analytic = evaluate(initial)
            columns: list[Array] = []
            for index in range(len(initial)):
                step = np.zeros_like(initial)
                step[index] = 1e-6
                columns.append(
                    (evaluate(initial + step)[0] - evaluate(initial - step)[0]) / 2e-6
                )
            np.testing.assert_allclose(
                analytic, np.column_stack(columns), atol=2e-8, rtol=2e-7
            )
        checked = True
        return kernel(initial, observations, equalities, **kwargs)

    monkeypatch.setattr(module, "solve_constrained_least_squares", check_jacobians)
    surfaces: list[module.SurfaceInput] = []
    axes: dict[str, module.AxisInput] = {}

    def axis_input(name: str, origin: Array, tilt: tuple[float, float]) -> Array:
        direction = np.array([*tilt, 1.0])
        direction /= np.linalg.norm(direction)
        axes[name] = module.AxisInput(
            {
                "parameters": [*origin[:2], *tilt],
                "axis_display": direction.tolist(),
                "point_display": origin.tolist(),
            },
            free=True,
        )
        return direction

    def surface(
        name: str, kind: str, points: Array, parameters: list[float], **kwargs: Any
    ) -> None:
        surfaces.append(
            module.SurfaceInput(
                name,
                kind,
                points,
                np.linspace(0.8, 1.2, len(points)),
                {"kind": kind, "parameters": parameters, "axial_domain": [-2.0, 5.0]},
                **kwargs,
            )
        )

    origin = np.array([2.0, -3.0, 0.0])
    direction = axis_input("cone_axis", origin, (0.07, -0.04))
    u, v = basis(direction)
    theta = np.tile(np.linspace(0, 2 * np.pi, 20, endpoint=False), 4)
    axial = np.repeat(np.linspace(-1, 3, 4), 20)
    circle = np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v
    surface(
        "cone",
        "cone",
        origin + axial[:, None] * direction + (3.0 + 0.15 * axial)[:, None] * circle,
        [2.0, -3.0, 0.07, -0.04, 3.04, 0.0, 0.12],
        axis="cone_axis",
    )
    grid = np.array([(x, y) for x in (-1.5, -0.5, 0.5, 1.5) for y in (-1, 0, 1)])
    cap_points = origin + 3.0 * direction + grid[:, :1] * u + grid[:, 1:] * v
    cap_offset = float(direction @ origin + 3.0)
    surface(
        "cap",
        "plane",
        cap_points,
        [*direction, cap_offset],
        axis="cone_axis",
        datum="cap_datum",
        construction="perpendicular_to_axis",
    )
    surface("reversed_cap", "plane", cap_points, [*(-direction), -cap_offset])
    clock_offset = float(v @ origin) + (0.0 if construction == "contains_axis" else 1.5)
    clock_points = (
        origin
        + (clock_offset - v @ origin) * v
        + grid[:, :1] * direction
        + grid[:, 1:] * u
    )
    surface(
        "clock",
        "plane",
        clock_points,
        [*v, clock_offset],
        axis="cone_axis",
        datum="clock_datum",
        construction=construction,
    )

    cylinder_origin = np.array([-8.0, 2.0, 0.0])
    cylinder_axis = axis_input(
        "standalone_lateral_axis", cylinder_origin, (-0.03, 0.05)
    )
    cu, cv = basis(cylinder_axis)
    surface(
        "cylinder",
        "cylinder",
        cylinder_origin
        + axial[:, None] * cylinder_axis
        + 2.0 * (np.cos(theta)[:, None] * cu + np.sin(theta)[:, None] * cv),
        [-8.0, 2.0, -0.03, 0.05, 2.05, 0.0, 0.0],
        axis="standalone_lateral_axis",
    )
    center = np.array([10.0, 5.0, 2.0])
    latitude = np.tile(np.linspace(-1.0, 1.0, 4), 20)
    longitude = np.repeat(np.linspace(0, 2 * np.pi, 20, endpoint=False), 4)
    sphere_directions = np.column_stack(
        (
            np.cos(latitude) * np.cos(longitude),
            np.cos(latitude) * np.sin(longitude),
            np.sin(latitude),
        )
    )
    for index in range(2):
        surface(
            f"sphere_{index}",
            "sphere",
            center + 2.0 * sphere_directions,
            [*center, 2.1],
            point="shared_center",
        )
    fitted = module.fit_relationship_component(
        surfaces,
        axes,
        [("coincident", ["cap", "reversed_cap"])],
        [["cylinder", "sphere_0", "sphere_1"]],
        {
            "shared_center": module.PointInput(
                {"point_display": (center + np.array([0.1, -0.05, 0.03])).tolist()},
                free=True,
            )
        },
    )
    assert checked
    assert fitted["solver"]["constraint_violation"] < 1e-10
    results = fitted["surfaces"]
    np.testing.assert_allclose(
        results["cap"]["plane_equation"],
        -np.asarray(results["reversed_cap"]["plane_equation"]),
        atol=1e-10,
    )
    axis = np.asarray(fitted["axes"]["cone_axis"]["axis_display"])
    equation = np.asarray(results["clock"]["plane_equation"])
    assert abs(equation[:3] @ axis) < 1e-10
    if construction == "contains_axis":
        assert equation[3] == pytest.approx(
            equation[:3] @ fitted["axes"]["cone_axis"]["point_display"], abs=1e-10
        )
    radius = results["cylinder"]["parameters"][4]
    for index in range(2):
        assert results[f"sphere_{index}"]["parameters"][3] == pytest.approx(
            radius, abs=1e-10
        )
        np.testing.assert_allclose(
            results[f"sphere_{index}"]["parameters"][:3],
            fitted["points"]["shared_center"]["point_display"],
            atol=1e-10,
        )


def test_parallel_normal_signs_remain_consistent_around_an_odd_cycle() -> None:
    from experiments.fit_relationship_component import (
        AxisInput,
        SurfaceInput,
        fit_relationship_component,
    )

    axes: dict[str, AxisInput] = {}
    surfaces: list[SurfaceInput] = []
    # Deliberately inconsistent initial orientations, but observations identify
    # a common nonhorizontal optimum inside the supported positive-Z chart.
    truth = np.array([0.1, -0.05, 1.0])
    truth /= np.linalg.norm(truth)
    for index, azimuth in enumerate(np.radians([0.0, 120.0, 240.0])):
        direction = np.array([np.cos(azimuth), np.sin(azimuth), 0.2])
        direction /= np.linalg.norm(direction)
        anchor = np.array([index * 6.0, index * 3.0, 0.0])
        u, v = basis(truth)
        theta = np.tile(np.linspace(0, 2 * np.pi, 20, endpoint=False), 4)
        axial = np.repeat(np.linspace(-1, 3, 4), 20)
        parameters = [*anchor[:2], *(direction[:2] / direction[2]), 2.0, 0.0, 0.0]
        axes[f"axis_{index}"] = AxisInput(
            {
                "parameters": parameters,
                "axis_display": direction.tolist(),
                "point_display": anchor.tolist(),
            },
            free=True,
        )
        side_points = (
            anchor
            + axial[:, None] * truth
            + 2.0 * (np.cos(theta)[:, None] * u + np.sin(theta)[:, None] * v)
        )
        surfaces.append(
            SurfaceInput(
                f"side_{index}",
                "cylinder",
                side_points,
                np.linspace(0.7 + index * 0.1, 1.3, len(side_points)),
                {
                    "kind": "cylinder",
                    "parameters": parameters,
                    "axial_domain": [-2.0, 5.0],
                },
                axis=f"axis_{index}",
            )
        )
        grid = np.array([(x, y) for x in (-1, 0, 1) for y in (-1, 0, 1)])
        plane_points = anchor + 3.0 * truth + grid[:, :1] * u + grid[:, 1:] * v
        equation = [*direction, float(direction @ anchor + 3.0)]
        surfaces.append(
            SurfaceInput(
                f"plane_{index}",
                "plane",
                plane_points,
                np.ones(len(plane_points)),
                {"kind": "plane", "parameters": equation},
                axis=f"axis_{index}",
                construction="perpendicular_to_axis",
            )
        )
    fitted = fit_relationship_component(
        surfaces,
        axes,
        [
            ("parallel", ["plane_0", "plane_1"]),
            ("parallel", ["plane_1", "plane_2"]),
            ("parallel", ["plane_2", "plane_0"]),
        ],
        [],
    )
    assert fitted["solver"]["constraint_violation"] < 1e-10
    normal = np.asarray(fitted["surfaces"]["plane_0"]["plane_equation"][:3])
    for index in range(3):
        published = fitted["surfaces"][f"plane_{index}"]["plane_equation"][:3]
        axis = primitive(fitted["surfaces"][f"side_{index}"])["axis"]
        np.testing.assert_allclose(np.cross(published, normal), 0.0, atol=1e-10)
        np.testing.assert_allclose(np.cross(axis, normal), 0.0, atol=1e-10)


def test_step_roundtrip_preserves_composed_cap_and_cylinder_orientation(
    composed_graph: FeatureGraph, tmp_path: Path
) -> None:
    import io
    from zipfile import ZipFile

    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    from experiments.nozzle_cad import CadExportRequest, export_cad

    fitted = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    side_axis = np.asarray(primitive(fitted["results"]["side_0"])["axis"])
    lower_normal = np.asarray(primitive(fitted["results"]["plate"])["axis"])
    upper_normal = np.asarray(primitive(fitted["results"]["shoulder_0"])["axis"])
    payload = cast(dict[str, Any], composed_graph.snapshot()["recipe"])
    for name, plane in (("top_circle", "shoulder_0"), ("bottom_circle", "plate")):
        payload["nodes"].append(
            {
                "id": name,
                "label": name,
                "operation": "surface_intersection",
                "first": {"feature": "side_0"},
                "second": {"feature": plane},
            }
        )
    payload["nodes"].extend(
        [
            {
                "id": "corrected_cap",
                "label": "corrected_cap",
                "operation": "trimmed_face",
                "surface": {"feature": "shoulder_0"},
                "boundaries": [{"intersection": "top_circle", "keep": "inside"}],
            },
            {
                "id": "corrected_cylinder",
                "label": "corrected_cylinder",
                "operation": "trimmed_face",
                "surface": {"feature": "side_0"},
                "boundaries": [
                    {
                        "intersection": "bottom_circle",
                        "keep": "positive"
                        if lower_normal @ side_axis > 0
                        else "negative",
                    },
                    {
                        "intersection": "top_circle",
                        "keep": "negative"
                        if upper_normal @ side_axis > 0
                        else "positive",
                    },
                ],
            },
        ]
    )
    payload["output"] = "corrected_cylinder"
    _ = composed_graph.replace(Recipe.model_validate(payload), _token(composed_graph))
    state = cast(
        dict[str, Any],
        composed_graph.evaluate(_token(composed_graph), all_actions=True),
    )
    request = CadExportRequest(
        token=_token(composed_graph),
        scope="selected_faces",
        targets=["corrected_cap", "corrected_cylinder"],
        units="Millimeters",
        axis_up=False,
        include_mesh=False,
    )
    bundle_bytes = export_cad(composed_graph.workspace, state, request)
    with ZipFile(io.BytesIO(bundle_bytes)) as bundle:
        path = tmp_path / "composed.step"
        _ = path.write_bytes(bundle.read("model.step"))
    reader = STEPControl_Reader()
    assert cast(Any, reader).ReadFile(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 2
    explorer = TopExp_Explorer(reader.OneShape(), TopAbs_FACE)
    directions: dict[str, Array] = {}
    while explorer.More():
        surface = BRepAdaptor_Surface(TopoDS.Face(explorer.Current()))
        if surface.GetType() == GeomAbs_Plane:
            directions["plane"] = np.asarray(surface.Plane().Axis().Direction().Coord())
        elif surface.GetType() == GeomAbs_Cylinder:
            directions["cylinder"] = np.asarray(
                surface.Cylinder().Axis().Direction().Coord()
            )
        else:
            pytest.fail("export changed an analytic surface type")
        explorer.Next()
    assert set(directions) == {"plane", "cylinder"}
    np.testing.assert_allclose(
        np.cross(directions["plane"], directions["cylinder"]), 0.0, atol=1e-10
    )
    expected = (
        composed_graph.workspace.frame @ primitive(state["results"]["side_0"])["axis"]
    )
    np.testing.assert_allclose(
        np.cross(directions["cylinder"], expected), 0.0, atol=1e-10
    )
