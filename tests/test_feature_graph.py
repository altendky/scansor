"""Current-graph replay, dependency invalidation and no retained edit history."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from pathlib import Path
from threading import Event
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    StaleGraph,
    discover_reuse_lineage,
)
from experiments.mesh_cylinder_fit import Array
from experiments.nozzle_coaxial import FitSelection, fit_fixed_axis_group
from experiments.nozzle_session import NozzleSession, NozzleWorkspace, SessionFit

EXAMPLE = Path("examples/nozzle-bayonette-simplified")


@pytest.fixture
def graph() -> FeatureGraph:
    return FeatureGraph(
        NozzleWorkspace(EXAMPLE),
        Recipe.model_validate_json((EXAMPLE / "recipes/cone-plane.json").read_text()),
    )


def token(graph: FeatureGraph) -> str:
    return str(graph.snapshot()["token"])


def changed(graph: FeatureGraph, node_id: str, **changes: object) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    assert isinstance(payload, dict)
    for node in payload["nodes"]:
        if node["id"] == node_id:
            node.update(changes)
    return Recipe.model_validate(payload)


def test_recipe_requires_unique_feature_names(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"][1]["label"] = f"  {payload['nodes'][0]['label'].upper()}  "
    with pytest.raises(ValueError, match="feature names must be unique"):
        _ = Recipe.model_validate(payload)


def explicit_axis_recipe(
    graph: FeatureGraph, *, free: bool, side_kind: str = "cylinder"
) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    base = {node["id"]: node for node in payload["nodes"]}
    nodes = [base[key] for key in ("scan", "outer_band", "top_face")]
    if free:
        initial_parameters = graph.workspace.data.selection["initial_parameters"][:4]
        nodes.append(
            {
                "id": "reference_axis",
                "label": "Reference axis",
                "operation": "axis",
                "initial_parameters": initial_parameters,
            }
        )
    else:
        base["side"].update(kind=side_kind, axis=None)
        nodes.append(base["side"])
        nodes.append(
            {
                "id": "reference_axis",
                "label": "Reference axis",
                "operation": "axis",
                "source_fit": "side",
            }
        )
    if free:
        nodes.append(
            {
                "id": "side_factor",
                "label": "Cylinder axis factor",
                "operation": "fit",
                "selections": ["outer_band"],
                "kind": side_kind,
                "axial_domain": [-2, 5],
                "axis": "reference_axis",
            }
        )
    nodes.append(
        {
            "id": "plane_factor",
            "label": "Fixed-axis plane",
            "operation": "fit",
            "selections": ["top_face"],
            "kind": "plane",
            "axial_domain": [-2, 5],
            "axis": "reference_axis",
        }
    )
    if free:
        nodes.append(
            {
                "id": "shared_axis",
                "label": "Shared-axis joint",
                "operation": "axis_solve",
                "axis": "reference_axis",
                "factors": ["side_factor", "plane_factor"],
            }
        )
    payload["nodes"] = nodes
    payload["output"] = "shared_axis" if free else "plane_factor"
    return Recipe.model_validate(payload)


@pytest.mark.parametrize("source_kind", ["cone", "cylinder"])
def test_explicit_axis_can_lock_a_downstream_plane(
    graph: FeatureGraph, source_kind: str
) -> None:
    _ = graph.replace(
        explicit_axis_recipe(graph, free=False, side_kind=source_kind), token(graph)
    )
    state = graph.evaluate(token(graph))
    derived = cast(dict[str, dict[str, Any]], state["derived"])
    np.testing.assert_allclose(
        derived["reference_axis"]["parameters"][:4],
        derived["side"]["parameters"][:4],
    )
    assert "resolved_by" not in cast(dict[str, Any], state["results"])["reference_axis"]
    np.testing.assert_allclose(
        derived["plane_factor"]["plane_equation"][:3],
        derived["reference_axis"]["axis_display"],
    )
    before = derived["reference_axis"]
    edited = changed(graph, "top_face", ids=graph.workspace.default.plane_ids[::2])
    state = graph.replace(edited, token(graph))
    states = cast(dict[str, str], state["states"])
    assert states["side"] == states["reference_axis"] == "ready"
    assert states["plane_factor"] == "stale"
    assert cast(dict[str, Any], state["derived"])["reference_axis"] == before


@pytest.mark.parametrize("side_kind", ["cone", "cylinder"])
def test_explicit_axis_can_be_free_in_a_joint_side_plane_solve(
    graph: FeatureGraph, side_kind: str
) -> None:
    _ = graph.replace(
        explicit_axis_recipe(graph, free=True, side_kind=side_kind), token(graph)
    )
    connected_state = graph.evaluate(token(graph), target="side_factor")
    assert (
        cast(dict[str, Any], connected_state["results"])["reference_axis"][
            "resolved_by"
        ]
        == "connected_fits"
    )
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    derived = cast(dict[str, dict[str, Any]], state["derived"])
    assert set(result["surfaces"]) == {"side_factor", "plane_factor"}
    np.testing.assert_allclose(
        result["surfaces"]["side_factor"]["parameters"][:4],
        result["surfaces"]["plane_factor"]["parameters"][:4],
    )
    assert derived["reference_axis"]["source_fit"] is None
    assert derived["reference_axis"]["parameters"][:4] == pytest.approx(
        graph.workspace.data.selection["initial_parameters"][:4]
    )
    assert derived["side_factor"]["kind"] == side_kind
    assert result["fit"]["parameters"] != derived["reference_axis"]["parameters"]
    assert "resolved_by" not in cast(dict[str, Any], state["results"])["reference_axis"]
    if side_kind == "cone":
        assert abs(derived["side_factor"]["parameters"][6]) > 0
        assert abs(result["surfaces"]["side_factor"]["parameters"][6]) > 0


def test_reference_plane_is_explicit_and_contains_its_axis(graph: FeatureGraph) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    solve = payload["nodes"].pop()
    payload["nodes"].append(
        {
            "id": "mirror_plane",
            "label": "Mirror plane",
            "operation": "reference_plane",
            "axis": "reference_axis",
            "construction": "contains_axis",
            "initial_angle_degrees": 27.0,
            "offset": 0.0,
        }
    )
    payload["nodes"].append(solve)
    payload["output"] = "mirror_plane"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, dict[str, Any]], state["derived"])["mirror_plane"]
    axis = np.asarray(result["axis_display"])
    normal = np.asarray(result["normal_display"])
    point = np.asarray(result["point_display"])
    assert axis @ normal == pytest.approx(0.0, abs=1e-12)
    assert np.asarray(result["plane_equation"][:3]) @ point == pytest.approx(
        result["plane_equation"][3]
    )
    assert result["angle_degrees"] == 27.0


@pytest.mark.parametrize(
    ("construction", "angle", "offset"),
    [
        ("parallel_to_axis", 27.0, 1.75),
        ("perpendicular_to_axis", None, -2.5),
    ],
)
def test_reference_plane_supports_offset_parallel_and_perpendicular_constructions(
    graph: FeatureGraph, construction: str, angle: float | None, offset: float
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    solve = payload["nodes"].pop()
    payload["nodes"].append(
        {
            "id": "reference_plane",
            "label": "Reference plane",
            "operation": "reference_plane",
            "axis": "reference_axis",
            "construction": construction,
            "initial_angle_degrees": angle,
            "offset": offset,
        }
    )
    payload["nodes"].append(solve)
    payload["output"] = "reference_plane"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    derived = cast(dict[str, dict[str, Any]], state["derived"])
    result = derived["reference_plane"]
    axis_result = derived["reference_axis"]
    axis = np.asarray(result["axis_display"])
    normal = np.asarray(result["normal_display"])
    point = np.asarray(result["point_display"])
    anchor = np.asarray(axis_result["point_display"])
    basis_u = np.asarray(result["basis_u_display"])
    basis_v = np.asarray(result["basis_v_display"])
    assert result["construction"] == construction
    assert result["offset"] == offset
    assert normal @ basis_u == pytest.approx(0.0, abs=1e-12)
    assert normal @ basis_v == pytest.approx(0.0, abs=1e-12)
    if construction == "parallel_to_axis":
        assert normal @ axis == pytest.approx(0.0, abs=1e-12)
        np.testing.assert_allclose(point - anchor, offset * normal)
        assert normal @ anchor != pytest.approx(normal @ point)
    else:
        np.testing.assert_allclose(normal, axis)
        np.testing.assert_allclose(point - anchor, offset * axis)
        assert result["angle_degrees"] is None


def test_plane_fit_can_lock_to_an_explicit_plane_orientation(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=False).model_dump()
    payload["nodes"].pop()
    payload["nodes"].extend(
        [
            {
                "id": "top_datum",
                "label": "Top datum",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": -3.0,
            },
            {
                "id": "datum_plane_fit",
                "label": "Datum-oriented plane fit",
                "operation": "fit",
                "selections": ["top_face"],
                "kind": "plane",
                "axial_domain": [-2, 5],
                "reference_plane": "top_datum",
            },
        ]
    )
    payload["output"] = "datum_plane_fit"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    derived = cast(dict[str, dict[str, Any]], state["derived"])
    datum = derived["top_datum"]
    fitted = derived["datum_plane_fit"]
    normal = np.asarray(datum["normal_display"])
    ids = graph.workspace.default.plane_ids
    points = graph.workspace.local[ids]
    weights = graph.workspace.data.weights[ids]
    expected_offset = float(weights @ (points @ normal) / weights.sum())
    np.testing.assert_allclose(fitted["plane_equation"][:3], normal)
    assert fitted["plane_equation"][3] == pytest.approx(expected_offset)
    assert fitted["signed_relative_offset"] == pytest.approx(
        expected_offset - datum["plane_equation"][3]
    )
    assert fitted["ids"] == ids


def test_connected_fits_drive_their_free_axis_and_plane_without_a_joint(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    by_id = {node["id"]: node for node in payload["nodes"]}
    plane_factor = by_id["plane_factor"]
    plane_factor["axis"] = None
    plane_factor["reference_plane"] = "top_datum"
    payload["nodes"] = [
        node for node in payload["nodes"] if node["id"] != "shared_axis"
    ]
    plane_index = payload["nodes"].index(plane_factor)
    payload["nodes"].insert(
        plane_index,
        {
            "id": "top_datum",
            "label": "Top datum",
            "operation": "reference_plane",
            "axis": "reference_axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": -3.0,
        },
    )
    payload["output"] = plane_factor["id"]
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    results = cast(dict[str, dict[str, Any]], state["results"])
    derived = cast(dict[str, dict[str, Any]], state["derived"])
    resolved_axis = results["reference_axis"]
    resolved_plane = results["top_datum"]
    fitted_plane = results["plane_factor"]

    np.testing.assert_allclose(
        fitted_plane["plane_equation"], resolved_plane["plane_equation"]
    )
    np.testing.assert_allclose(
        resolved_plane["normal_display"], resolved_axis["axis_display"]
    )
    assert resolved_plane["offset"] != pytest.approx(-3.0)
    assert resolved_axis["parameters"] != derived["reference_axis"]["parameters"]
    assert resolved_axis["resolved_by"] == "connected_fits"
    assert derived["top_datum"]["offset"] == -3.0
    assert state["result"] is None

    edited = changed(
        graph,
        "outer_band",
        ids=graph.workspace.default.lateral_ids[::2],
    )
    stale = graph.replace(edited, token(graph))
    states = cast(dict[str, str], stale["states"])
    assert states["reference_axis"] == "stale"
    assert states["top_datum"] == "stale"
    assert states["side_factor"] == "stale"
    assert states["plane_factor"] == "stale"


def test_fitted_selection_region_replays_in_an_explicit_datum_frame(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    payload["nodes"] = [
        node for node in payload["nodes"] if node["id"] != "shared_axis"
    ]
    payload["nodes"].extend(
        [
            {
                "id": "region_axial",
                "label": "Region axial datum",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "construction": "perpendicular_to_axis",
                "initial_angle_degrees": None,
                "offset": 0.0,
            },
            {
                "id": "region_clock",
                "label": "Region clock datum",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "construction": "contains_axis",
                "initial_angle_degrees": 0.0,
                "offset": 0.0,
            },
            {
                "id": "outer_region",
                "label": "Outer reusable region",
                "operation": "selection_region",
                "selection": "outer_band",
                "fit": "side_factor",
                "axial_plane": "region_axial",
                "clock_plane": "region_clock",
                "tangent_margin": 0.0,
                "normal_margin": 0.25,
                "normal_angle_degrees": 45.0,
            },
            {
                "id": "replayed_outer",
                "label": "Replayed outer selection",
                "operation": "region_selection",
                "region": "outer_region",
                "source": "scan",
                "axial_plane": "region_axial",
                "clock_plane": "region_clock",
            },
        ]
    )
    payload["output"] = "replayed_outer"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    memberships = cast(dict[str, list[int]], state["memberships"])
    results = cast(dict[str, dict[str, Any]], state["results"])

    assert set(graph.workspace.default.lateral_ids) <= set(
        memberships["replayed_outer"]
    )
    assert results["outer_region"]["format"] == ("scansor-fitted-selection-region-v1")
    assert results["replayed_outer"]["vertex_count"] == len(
        memberships["replayed_outer"]
    )

    invalid = cast(dict[str, Any], graph.snapshot()["recipe"])
    invalid["nodes"].append(
        {
            "id": "self_driving_fit",
            "label": "Self-driving fit",
            "operation": "fit",
            "selections": ["replayed_outer"],
            "kind": "cylinder",
            "axial_domain": [-2.0, 5.0],
            "axis": "reference_axis",
        }
    )
    invalid["output"] = "self_driving_fit"
    with pytest.raises(ValueError, match="fit an applied region standalone"):
        _ = graph.replace(Recipe.model_validate(invalid), token(graph))


def test_feature_reuse_discovers_lineage_and_generates_a_target_selection(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    typed = {node.id: node for node in Recipe.model_validate(payload).nodes}
    lineage = discover_reuse_lineage(["side_factor"], typed)
    assert "shared_axis" not in lineage
    assert "shared_axis" in discover_reuse_lineage(
        ["side_factor", "plane_factor"], typed
    )
    payload["nodes"].extend(
        [
            {
                "id": "reuse_outer",
                "label": "Reuse outer feature",
                "operation": "feature_reuse",
                "fits": ["side_factor"],
                "lineage": lineage,
                "reference_selection": "outer_band",
                "target_selection": "outer_band",
                "tangent_margin": 0.0,
                "normal_margin": 0.25,
                "normal_angle_degrees": 35.0,
            },
            {
                "id": "reused_outer",
                "label": "Reused outer selection",
                "operation": "reuse_selection",
                "reuse": "reuse_outer",
                "fit": "side_factor",
                "source_selection": "outer_band",
            },
            {
                "id": "reused_outer_fit",
                "label": "Reused outer fit",
                "operation": "fit",
                "selections": ["reused_outer"],
                "kind": "cylinder",
                "axial_domain": [-2.0, 5.0],
            },
        ]
    )
    payload["output"] = "reused_outer_fit"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph), all_actions=True)
    memberships = cast(dict[str, list[int]], state["memberships"])
    results = cast(dict[str, Any], state["results"])
    match = results["reuse_outer"]
    result = results["reused_outer_fit"]

    assert set(graph.workspace.default.lateral_ids) <= set(memberships["reused_outer"])
    assert match["format"] == "scansor-rigid-occurrence-match-v1"
    np.testing.assert_allclose(match["rotation"], np.eye(3), atol=1e-8)
    np.testing.assert_allclose(match["translation"], np.zeros(3), atol=1e-8)
    assert result["kind"] == "cylinder"
    assert result["weighted_rms"] < 0.03


def test_fit_rejects_incompatible_or_multiple_reference_geometry(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    plane_factor = next(
        node for node in payload["nodes"] if node["id"] == "plane_factor"
    )
    plane_factor["reference_plane"] = "reference_axis"
    with pytest.raises(ValueError, match="only one datum"):
        _ = Recipe.model_validate(payload)

    plane_factor["axis"] = None
    plane_factor["kind"] = "cylinder"
    with pytest.raises(ValueError, match="only a plane fit"):
        _ = Recipe.model_validate(payload)

    plane_factor["kind"] = "plane"
    recipe = Recipe.model_validate(payload)
    with pytest.raises(ValueError, match="explicit reference plane"):
        _ = graph.replace(recipe, token(graph))


def test_mirror_symmetry_requires_a_plane_containing_its_axis(
    graph: FeatureGraph,
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    solve = payload["nodes"].pop()
    payload["nodes"].extend(
        [
            {
                "id": "offset_plane",
                "label": "Offset plane",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "construction": "parallel_to_axis",
                "initial_angle_degrees": 0.0,
                "offset": 1.0,
            },
            {
                "id": "mirror_pair",
                "label": "Mirror pair",
                "operation": "mirror_symmetry",
                "plane": "offset_plane",
                "surfaces": ["side_factor", "plane_factor"],
            },
            solve,
        ]
    )
    payload["output"] = solve["id"]
    with pytest.raises(
        ValueError,
        match="mirror symmetry on a shared axis requires a reference plane containing that axis",
    ):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))


def test_mirror_symmetry_references_two_same_type_standalone_fits(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    solve = payload["nodes"].pop()
    used = {
        vertex
        for node in payload["nodes"]
        if node["operation"] == "selection"
        for vertex in node["ids"]
    }
    available = [
        index
        for index, weight in enumerate(graph.workspace.data.weights)
        if weight > 0 and index not in used
    ][:20]
    payload["nodes"].extend(
        [
            {
                "id": "mirror_selection_a",
                "label": "Mirror selection A",
                "operation": "selection",
                "source": "scan",
                "ids": available[:10],
            },
            {
                "id": "mirror_selection_b",
                "label": "Mirror selection B",
                "operation": "selection",
                "source": "scan",
                "ids": available[10:],
            },
            {
                "id": "mirror_plane",
                "label": "Mirror plane",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "initial_angle_degrees": 0.0,
            },
            {
                "id": "mirror_a",
                "label": "Mirror A",
                "operation": "fit",
                "selections": ["mirror_selection_a"],
                "kind": "plane",
            },
            {
                "id": "mirror_b",
                "label": "Mirror B",
                "operation": "fit",
                "selections": ["mirror_selection_b"],
                "kind": "plane",
            },
            {
                "id": "mirror_pair",
                "label": "Mirrored pair",
                "operation": "mirror_symmetry",
                "plane": "mirror_plane",
                "surfaces": ["mirror_a", "mirror_b"],
            },
        ]
    )
    solve["factors"].append("mirror_pair")
    payload["nodes"].append(solve)
    payload["output"] = "shared_axis"
    recipe = Recipe.model_validate(payload)
    _ = graph.replace(recipe, token(graph))

    def fake_seed(
        points: Array,
        _weights: Array,
        _normals: Array,
        _kind: str,
        _initial: Array,
        _domain: tuple[float, float],
    ) -> dict[str, Any]:
        return {
            "kind": "plane",
            "parameters": [0.0, 0.0, 1.0, 0.0],
            "plane_equation": [0.0, 0.0, 1.0, 0.0],
            "residuals": [0.0] * len(points),
            "weighted_rms": 0.0,
            "condition": 1.0,
        }

    monkeypatch.setattr("experiments.feature_graph.fit_seed", fake_seed)

    def fake_group(*_args: object, **kwargs: object) -> SessionFit:
        groups = cast(
            tuple[tuple[FitSelection, FitSelection], ...], kwargs["mirror_groups"]
        )
        phases = cast(tuple[float, ...], kwargs["mirror_phases_radians"])
        assert [[surface.id for surface in group] for group in groups] == [
            ["mirror_a", "mirror_b"]
        ]
        assert phases == pytest.approx((0.0,))
        return cast(
            SessionFit,
            cast(
                object,
                {
                    "axis_display": [0.0, 0.0, 1.0],
                    "point_display": [0.0, 0.0, 0.0],
                    "mirror_planes": [
                        {
                            "phase_radians": 0.0,
                            "equation": [1.0, 0.0, 0.0, 0.0],
                            "direction": [0.0, 1.0, 0.0],
                        }
                    ],
                    "surfaces": {},
                },
            ),
        )

    monkeypatch.setattr("experiments.feature_graph.fit_group", fake_group)
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert result["mirror_planes"]["mirror_plane"]["plane_equation"] == [
        1.0,
        0.0,
        0.0,
        0.0,
    ]
    assert cast(dict[str, str], state["states"])["mirror_pair"] == "ready"

    invalid = recipe.model_dump()
    by_id = {node["id"]: node for node in invalid["nodes"]}
    by_id["mirror_pair"]["surfaces"] = ["mirror_a", "side_factor"]
    with pytest.raises(ValueError, match="standalone"):
        _ = graph.replace(Recipe.model_validate(invalid), token(graph))


def arch_relationship_recipe(graph: FeatureGraph) -> Recipe:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["id"] not in {"plane_factor", "shared_axis"}
    ]
    used = {
        vertex
        for node in payload["nodes"]
        if node["operation"] == "selection"
        for vertex in node["ids"]
    }
    available = [
        index
        for index, weight in enumerate(graph.workspace.data.weights)
        if weight > 0 and index not in used
    ][:20]
    payload["nodes"].extend(
        [
            {
                "id": "left_selection",
                "label": "Left wall observations",
                "operation": "selection",
                "source": "scan",
                "ids": available[:10],
            },
            {
                "id": "right_selection",
                "label": "Right wall observations",
                "operation": "selection",
                "source": "scan",
                "ids": available[10:],
            },
            {
                "id": "arch_midplane",
                "label": "Arch midplane",
                "operation": "reference_plane",
                "axis": "reference_axis",
                "initial_angle_degrees": 0.0,
            },
            {
                "id": "left_wall",
                "label": "Left wall",
                "operation": "fit",
                "selections": ["left_selection"],
                "kind": "plane",
            },
            {
                "id": "right_wall",
                "label": "Right wall",
                "operation": "fit",
                "selections": ["right_selection"],
                "kind": "plane",
            },
            {
                "id": "wall_mirror",
                "label": "Mirrored walls",
                "operation": "mirror_symmetry",
                "plane": "arch_midplane",
                "surfaces": ["left_wall", "right_wall"],
            },
            {
                "id": "wall_parallel",
                "label": "Wall parallel to midplane",
                "operation": "parallel",
                "surface": "left_wall",
                "reference_plane": "arch_midplane",
            },
            {
                "id": "radius_equals_wall_offset",
                "label": "Radius equals wall offset",
                "operation": "equal",
                "left": {"measurement": "radius", "surface": "side_factor"},
                "right": {
                    "measurement": "plane_distance",
                    "surface": "left_wall",
                    "reference_plane": "arch_midplane",
                },
            },
            {
                "id": "arch_solve",
                "label": "Arch solve",
                "operation": "axis_solve",
                "axis": "reference_axis",
                "factors": [
                    "side_factor",
                    "wall_mirror",
                    "wall_parallel",
                    "radius_equals_wall_offset",
                ],
            },
        ]
    )
    payload["output"] = "arch_solve"
    return Recipe.model_validate(payload)


def test_arch_primitives_compile_to_one_exact_radius_tied_mirror_group(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = arch_relationship_recipe(graph)
    _ = graph.replace(recipe, token(graph))

    def fake_seed(
        points: Array,
        _weights: Array,
        _normals: Array,
        _kind: str,
        _initial: Array,
        _domain: tuple[float, float],
    ) -> dict[str, Any]:
        return {
            "kind": "plane",
            "parameters": [1.0, 0.0, 0.0, 0.0],
            "plane_equation": [1.0, 0.0, 0.0, 0.0],
            "residuals": [0.0] * len(points),
            "weighted_rms": 0.0,
            "condition": 1.0,
        }

    monkeypatch.setattr("experiments.feature_graph.fit_seed", fake_seed)

    def fake_group(*args: object, **kwargs: object) -> SessionFit:
        assert cast(list[FitSelection], args[2]) == []
        assert kwargs["mirror_radius_surface_ids"] == ("side_factor",)
        return cast(
            SessionFit,
            cast(
                object,
                {
                    "axis_display": [0.0, 0.0, 1.0],
                    "point_display": [0.0, 0.0, 0.0],
                    "mirror_planes": [
                        {
                            "phase_radians": 0.0,
                            "equation": [1.0, 0.0, 0.0, 0.0],
                            "direction": [0.0, 1.0, 0.0],
                        }
                    ],
                    "surfaces": {},
                },
            ),
        )

    monkeypatch.setattr("experiments.feature_graph.fit_group", fake_group)
    state = graph.evaluate(token(graph))
    assert cast(dict[str, str], state["states"])["wall_parallel"] == "ready"
    assert cast(dict[str, str], state["states"])["radius_equals_wall_offset"] == "ready"
    assert cast(dict[str, Any], state["result"])["mirror_planes"]["arch_midplane"]


def test_arch_relationship_cluster_must_be_complete(graph: FeatureGraph) -> None:
    payload = arch_relationship_recipe(graph).model_dump()
    solve = next(node for node in payload["nodes"] if node["id"] == "arch_solve")
    solve["factors"].remove("radius_equals_wall_offset")
    with pytest.raises(ValueError, match="requires exactly one parallel"):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            (
                "reference_axis",
                {"source_fit": "plane_factor", "initial_parameters": None},
            ),
            "earlier",
        ),
        (("side_factor", {"kind": "plane"}), "requires a cone or cylinder"),
        (("shared_axis", {"factors": ["side_factor", "side_factor"]}), "unique"),
        (
            (
                "shared_axis",
                {
                    "factors": ["side_factor", "plane_factor"],
                    "axis": "side_factor",
                },
            ),
            "explicit axis",
        ),
    ],
)
def test_rejects_invalid_explicit_axis_graphs(
    graph: FeatureGraph, mutation: tuple[str, dict[str, object]], message: str
) -> None:
    recipe = explicit_axis_recipe(graph, free=True).model_dump()
    node_id, changes = mutation
    next(node for node in recipe["nodes"] if node["id"] == node_id).update(changes)
    with pytest.raises(ValueError, match=message):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(recipe))


@pytest.mark.parametrize(
    "initializer",
    [
        {"source_fit": None, "initial_parameters": None},
        {"source_fit": "side_factor", "initial_parameters": [0, 0, 0, 0]},
    ],
)
def test_axis_requires_exactly_one_initializer(
    graph: FeatureGraph, initializer: dict[str, object]
) -> None:
    recipe = explicit_axis_recipe(graph, free=True).model_dump()
    axis = next(node for node in recipe["nodes"] if node["id"] == "reference_axis")
    axis.update(initializer)
    with pytest.raises(ValueError, match="exactly one"):
        _ = Recipe.model_validate(recipe)


def test_manual_axis_initializer_must_be_finite(graph: FeatureGraph) -> None:
    recipe = explicit_axis_recipe(graph, free=True).model_dump()
    axis = next(node for node in recipe["nodes"] if node["id"] == "reference_axis")
    axis["initial_parameters"] = [0, 0, float("nan"), 0]
    with pytest.raises(ValueError, match="must be finite"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(recipe))


@pytest.mark.parametrize("operation", ["growth", "perpendicular"])
def test_axis_bound_fits_cannot_enter_legacy_evaluation_paths(
    graph: FeatureGraph, operation: str
) -> None:
    payload = explicit_axis_recipe(graph, free=True).model_dump()
    if operation == "growth":
        payload["nodes"].append(
            {
                "id": "legacy_growth",
                "label": "Legacy growth",
                "operation": "growth",
                "seed_fit": "side_factor",
                "barriers": [],
                "distance": 0.05,
                "angle_degrees": 20,
            }
        )
    else:
        payload["nodes"].insert(
            -1,
            {
                "id": "legacy_perpendicular",
                "label": "Legacy perpendicular",
                "operation": "perpendicular",
                "lateral": "side_factor",
                "plane": "plane_factor",
            },
        )
    with pytest.raises(ValueError, match="standalone"):
        _ = FeatureGraph(graph.workspace, Recipe.model_validate(payload))


def test_fixed_axis_rejects_a_zero_radius_cylinder(graph: FeatureGraph) -> None:
    workspace = copy(graph.workspace)
    workspace.local = np.column_stack(
        [np.zeros(7), np.zeros(7), np.arange(7, dtype=float)]
    )
    with pytest.raises(ValueError, match="radius must be positive"):
        _ = fit_fixed_axis_group(
            workspace,
            [FitSelection("line", list(range(7)), "cylinder", (-2, 7))],
            [],
            np.array([0, 0, 0, 0, 1, 0, 0], dtype=float),
        )


def test_recipe_replays_and_surface_kind_is_declared(graph: FeatureGraph) -> None:
    state = graph.evaluate(token(graph))
    result = state["result"]
    assert isinstance(result, dict)
    assert result["reference_diameter"] == pytest.approx(18.79278, abs=1e-5)
    old_token = token(graph)
    state = graph.replace(changed(graph, "side", kind="cylinder"), old_token)
    assert state["result"] is None
    assert state["states"] == {
        "scan": "ready",
        "outer_band": "ready",
        "top_face": "ready",
        "side": "stale",
        "end": "ready",
        "perpendicular": "stale",
        "fit": "stale",
    }
    result = graph.evaluate(token(graph))["result"]
    assert isinstance(result, dict)
    assert result["reference_diameter"] == pytest.approx(18.79299, abs=1e-5)
    assert result["signed_half_angle_degrees"] == 0
    with pytest.raises(StaleGraph):
        _ = graph.replace(changed(graph, "side", kind="cone"), old_token)


def test_save_load_has_current_graph_only_and_independent_snapshots(
    graph: FeatureGraph,
) -> None:
    _ = graph.evaluate(token(graph))
    recipe = changed(graph, "outer_band", ids=graph.workspace.default.lateral_ids[::2])
    _ = graph.replace(recipe, token(graph))
    snapshot = graph.snapshot()
    assert snapshot["result"] is None
    assert set(snapshot) == {
        "recipe",
        "token",
        "states",
        "errors",
        "diagnostics",
        "result",
        "derived",
        "memberships",
        "results",
    }
    assert set(recipe.model_dump()) == {"schema_version", "nodes", "output"}
    loaded = FeatureGraph(
        graph.workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    first, second = (
        graph.evaluate(token(graph))["result"],
        loaded.evaluate(token(loaded))["result"],
    )
    assert isinstance(first, dict) and isinstance(second, dict)
    np.testing.assert_allclose(
        first["reference_diameter"], second["reference_diameter"]
    )
    first["reference_diameter"] = -1
    assert graph.snapshot()["result"] != first


@pytest.mark.parametrize(
    ("node_id", "changes", "message"),
    [
        ("outer_band", {"source": "outer_band"}, "cycle"),
        ("side", {"selections": ["absent"]}, "missing"),
        ("scan", {"source_sha256": "wrong"}, "binding"),
        ("scan", {"reference_sha256": "wrong"}, "binding"),
        ("outer_band", {"ids": [1, 1]}, "unique"),
        ("side", {"kind": "plane"}, "relationship"),
    ],
)
def test_rejects_bad_graph_without_changing_current_state(
    graph: FeatureGraph, node_id: str, changes: dict[str, object], message: str
) -> None:
    before = graph.snapshot()
    with pytest.raises(ValueError, match=message):
        _ = graph.replace(changed(graph, node_id, **changes), token(graph))
    assert graph.snapshot() == before


def test_failure_status_is_authoritative(graph: FeatureGraph) -> None:
    _ = graph.replace(changed(graph, "top_face", ids=[]), token(graph))
    with pytest.raises(ValueError, match="at least"):
        _ = graph.evaluate(token(graph))
    assert cast(dict[str, str], graph.snapshot()["states"])["end"] == "failed"
    assert graph.snapshot()["result"] is None


def test_stale_worker_cannot_publish_after_edit(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = Event(), Event()
    original = graph.workspace.fit

    def delayed(
        session: NozzleSession,
        kind: str = "cone",
        support: tuple[float, float] | None = None,
    ) -> SessionFit:
        started.set()
        assert release.wait(5)
        return original(session, kind, support)

    monkeypatch.setattr(graph.workspace, "fit", delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(graph.evaluate, token(graph))
        assert started.wait(5)
        try:
            _ = graph.replace(changed(graph, "side", kind="cylinder"), token(graph))
        finally:
            release.set()
        with pytest.raises(StaleGraph):
            _ = future.result()
    assert graph.snapshot()["result"] is None
    assert cast(dict[str, str], graph.snapshot()["states"])["fit"] == "stale"


def coaxial_recipe(graph: FeatureGraph) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    ids = nodes["outer_band"]["ids"]
    nodes["outer_band"]["ids"] = ids[::2]
    payload["nodes"].extend(
        [
            {
                "id": "extra_selection",
                "label": "Other band observations",
                "operation": "selection",
                "source": "scan",
                "ids": ids[1::2],
            },
            {
                "id": "extra",
                "label": "Additional cone",
                "operation": "surface",
                "selection": "extra_selection",
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "axis",
                "label": "Coaxial",
                "operation": "coaxial",
                "surface": "extra",
                "reference": "side",
            },
        ]
    )
    nodes["fit"]["constraints"].append("axis")
    payload["schema_version"] = 1
    return Recipe.model_validate(payload)


def test_coaxial_graph_replay_and_invalidation(graph: FeatureGraph) -> None:
    _ = graph.replace(coaxial_recipe(graph), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert set(result["surfaces"]) == {"side", "end", "extra"}
    np.testing.assert_array_equal(
        result["surfaces"]["side"]["parameters"][:4],
        result["surfaces"]["extra"]["parameters"][:4],
    )
    assert result["reference_diameter"] == pytest.approx(18.79278, abs=0.002)
    saved = Recipe.model_validate(graph.snapshot()["recipe"])
    replay = FeatureGraph(graph.workspace, saved)
    assert replay.evaluate(token(replay))["result"] == result
    state = graph.replace(changed(graph, "extra", kind="cylinder"), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["fit"] == "stale"
    result = cast(dict[str, Any], graph.evaluate(token(graph))["result"])
    assert result["surfaces"]["extra"]["parameters"][6] == 0


@pytest.mark.parametrize(
    "mutation", ["self", "plane_axis", "overlap", "disconnected", "empty"]
)
def test_coaxial_invalid_graphs_and_empty_fit(
    graph: FeatureGraph, mutation: str
) -> None:
    payload = coaxial_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    if mutation == "self":
        nodes["axis"]["reference"] = "extra"
    elif mutation == "plane_axis":
        nodes["axis"]["reference"] = "end"
    elif mutation == "overlap":
        nodes["extra_selection"]["ids"] = nodes["outer_band"]["ids"]
    elif mutation == "disconnected":
        nodes["axis"].update(operation="perpendicular", lateral="extra", plane="end")
        del nodes["axis"]["surface"], nodes["axis"]["reference"]
    else:
        nodes["extra_selection"]["ids"] = []
    recipe = Recipe.model_validate(payload)
    if mutation in {"empty", "overlap"}:
        _ = graph.replace(recipe, token(graph))
        with pytest.raises(ValueError, match=r"at least|overlap"):
            _ = graph.evaluate(token(graph))
        assert graph.snapshot()["result"] is None
    else:
        with pytest.raises(ValueError):
            _ = graph.replace(recipe, token(graph))


def test_reads_original_single_constraint_recipe(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    fit = next(n for n in payload["nodes"] if n["id"] == "fit")
    fit["constraint"] = fit.pop("constraints")[0]
    recipe = Recipe.model_validate(payload)
    restored = FeatureGraph(graph.workspace, recipe).snapshot()
    stored = cast(dict[str, Any], restored["recipe"])
    fit = next(n for n in stored["nodes"] if n["id"] == "fit")
    assert fit["constraints"] == ["perpendicular"]
    assert "constraint" not in fit


def test_first_surface_selection_depth_round_trip(graph: FeatureGraph) -> None:
    _ = graph.replace(changed(graph, "outer_band", depth="first_surface"), token(graph))
    restored = FeatureGraph(
        graph.workspace, Recipe.model_validate(graph.snapshot()["recipe"])
    )
    recipe = cast(dict[str, Any], restored.snapshot()["recipe"])
    assert (
        next(n for n in recipe["nodes"] if n["id"] == "outer_band")["depth"]
        == "first_surface"
    )
    with pytest.raises(ValueError):
        _ = changed(graph, "outer_band", depth="front_normals")


def proposal_recipe(graph: FeatureGraph) -> Recipe:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "seed_fit",
                "label": "Seed only",
                "operation": "seed_fit",
                "selection": "outer_band",
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "growth",
                "label": "Connected additions",
                "operation": "growth",
                "seed_fit": "seed_fit",
                "barriers": ["top_face"],
                "distance": 0.05,
                "angle_degrees": 20,
            },
        ]
    )
    payload["schema_version"] = 1
    return Recipe.model_validate(payload)


def test_proposal_apply_replay_and_seed_invalidation(graph: FeatureGraph) -> None:
    original = graph.workspace.default.lateral_ids
    _ = graph.replace(proposal_recipe(graph), token(graph))
    state = graph.evaluate(token(graph), "growth")
    derived = cast(dict[str, Any], state["derived"])
    assert derived["growth"]["added_ids"]
    assert set(original) <= set(derived["growth"]["ids"])
    assert not set(derived["growth"]["ids"]).intersection(
        graph.workspace.default.plane_ids
    )
    assert state["result"] is None
    # Reusing a later proposal requires later fit/constraint/joint actions.
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"].extend(
        [
            {
                "id": "grown_fit",
                "label": "Grown cone",
                "operation": "fit",
                "selections": ["growth"],
                "kind": "cone",
                "axial_domain": [-2, 5],
            },
            {
                "id": "grown_constraint",
                "label": "Grown perpendicular",
                "operation": "perpendicular",
                "lateral": "grown_fit",
                "plane": "end",
            },
            {
                "id": "grown_joint",
                "label": "Grown joint",
                "operation": "joint_fit",
                "constraints": ["grown_constraint"],
            },
        ]
    )
    payload["output"] = "grown_joint"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    assert state["result"] is not None
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == state["result"]
    state = graph.replace(changed(graph, "outer_band", ids=original[::2]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, Any], state["memberships"])["growth"] is None
    assert "growth" not in cast(dict[str, Any], state["derived"])


def test_proposal_needs_no_plane_observations(graph: FeatureGraph) -> None:
    _ = graph.replace(proposal_recipe(graph), token(graph))
    _ = graph.replace(changed(graph, "top_face", ids=[]), token(graph))
    assert "growth" in cast(
        dict[str, Any], graph.evaluate(token(graph), "growth")["derived"]
    )


def test_growth_cycles_and_invalid_thresholds_rejected(graph: FeatureGraph) -> None:
    _ = graph.replace(proposal_recipe(graph), token(graph))
    with pytest.raises(ValueError, match="cycle"):
        _ = graph.replace(changed(graph, "growth", barriers=["growth"]), token(graph))
    with pytest.raises(ValueError):
        _ = changed(graph, "growth", distance=float("nan"))


def test_late_seed_fit_cannot_publish_after_edit(
    graph: FeatureGraph, monkeypatch: pytest.MonkeyPatch
) -> None:
    import experiments.feature_graph as module

    _ = graph.replace(proposal_recipe(graph), token(graph))
    started, release = Event(), Event()
    from experiments.selection_growth import fit_seed as original_fit

    def delayed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started.set()
        assert release.wait(5)
        return original_fit(*args, **kwargs)

    monkeypatch.setattr(module, "fit_seed", delayed)
    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(graph.evaluate, token(graph), "growth")
        assert started.wait(5)
        try:
            _ = graph.replace(
                changed(
                    graph, "outer_band", ids=graph.workspace.default.lateral_ids[::2]
                ),
                token(graph),
            )
        finally:
            release.set()
        with pytest.raises(StaleGraph):
            _ = pending.result(timeout=5)
    assert graph.snapshot()["derived"] == {}


def test_ordered_references_and_reorder_preserves_results(graph: FeatureGraph) -> None:
    before = graph.evaluate(token(graph))
    payload = cast(dict[str, Any], before["recipe"])
    assert payload["schema_version"] == 2
    # Independent selections can trade places without changing their identities.
    payload["nodes"][1:3] = reversed(payload["nodes"][1:3])
    after = graph.replace(Recipe.model_validate(payload), token(graph))
    assert after["results"] == before["results"]
    assert after["states"] == before["states"]
    payload["nodes"][0:2] = reversed(payload["nodes"][0:2])
    with pytest.raises(ValueError, match="earlier"):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))
    assert graph.snapshot() == after


def test_multi_selection_union_and_independent_plane(graph: FeatureGraph) -> None:
    baseline = cast(dict[str, Any], graph.evaluate(token(graph), "end")["results"])[
        "end"
    ]
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    ids = graph.workspace.default.plane_ids
    payload["nodes"].extend(
        [
            {
                "id": "patch_a",
                "label": "Plane patch A",
                "operation": "selection",
                "source": "scan",
                "ids": ids[::2],
            },
            {
                "id": "patch_b",
                "label": "Plane patch B",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            },
            {
                "id": "other_plane",
                "label": "Another plane",
                "operation": "fit",
                "selections": ["patch_a", "patch_b"],
                "kind": "plane",
            },
        ]
    )
    payload["output"] = "other_plane"
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["results"])["other_plane"]
    assert result["ids"] == ids
    np.testing.assert_allclose(result["parameters"], baseline["parameters"])
    assert result["weighted_rms"] == baseline["weighted_rms"]
    assert state["result"] is None  # No joint solve was requested.


def test_evaluate_all_includes_independent_actions_after_the_output(
    graph: FeatureGraph,
) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["nodes"].append(
        {
            "id": "independent_plane",
            "label": "Independent plane",
            "operation": "fit",
            "selections": ["top_face"],
            "kind": "plane",
        }
    )
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph), all_actions=True)
    assert cast(dict[str, str], state["states"])["independent_plane"] == "ready"
    assert "independent_plane" in cast(dict[str, Any], state["results"])
    with pytest.raises(ValueError, match="cannot also specify a target"):
        _ = graph.evaluate(token(graph), "side", all_actions=True)


def test_joint_keeps_standalone_results(graph: FeatureGraph) -> None:
    standalone = cast(dict[str, Any], graph.evaluate(token(graph), "side")["results"])[
        "side"
    ]
    state = graph.evaluate(token(graph))
    results = cast(dict[str, Any], state["results"])
    assert results["side"] == standalone
    assert not np.allclose(
        results["fit"]["surfaces"]["side"]["parameters"],
        standalone["parameters"],
        atol=1e-8,
        rtol=0,
    )


def test_migrates_unordered_legacy_recipe(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    payload["schema_version"] = 1
    for node in payload["nodes"]:
        if node["operation"] == "fit":
            node["operation"] = "surface"
            node["selection"] = node.pop("selections")[0]
    payload["nodes"].reverse()
    migrated = Recipe.model_validate(payload)
    restored = FeatureGraph(graph.workspace, migrated)
    assert migrated.schema_version == 2
    assert (
        restored.evaluate(token(restored))["result"]
        == graph.evaluate(token(graph))["result"]
    )


def test_additional_joint_plane_replay_and_invalidation(graph: FeatureGraph) -> None:
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    ids = nodes["top_face"]["ids"]
    nodes["top_face"]["ids"] = ids[::2]
    joint = nodes["fit"]
    payload["nodes"].remove(joint)
    payload["nodes"].extend(
        [
            {
                "id": "other_patch",
                "label": "Other plane patch",
                "operation": "selection",
                "source": "scan",
                "ids": ids[1::2],
            },
            {
                "id": "other_plane",
                "label": "Other plane",
                "operation": "fit",
                "selections": ["other_patch"],
                "kind": "plane",
            },
            {
                "id": "other_perpendicular",
                "label": "Other perpendicular",
                "operation": "perpendicular",
                "lateral": "side",
                "plane": "other_plane",
            },
            joint,
        ]
    )
    joint["constraints"].append("other_perpendicular")
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert set(result["surfaces"]) == {"side", "end", "other_plane"}
    np.testing.assert_array_equal(
        result["surfaces"]["end"]["parameters"][:4],
        result["surfaces"]["other_plane"]["parameters"][:4],
    )
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    state = graph.replace(changed(graph, "other_patch", ids=ids[1::4]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["end"] == "ready"
    assert graph.evaluate(token(graph))["result"] is not None


def test_overlap_diagnostics_exact_vertices_and_invalidation(
    graph: FeatureGraph,
) -> None:
    payload = coaxial_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    shared = nodes["outer_band"]["ids"][::5]
    nodes["extra_selection"]["ids"] = sorted(
        set(nodes["extra_selection"]["ids"]) | set(shared)
    )
    _ = graph.replace(Recipe.model_validate(payload), token(graph))
    with pytest.raises(ValueError, match="overlap"):
        _ = graph.evaluate(token(graph))
    state = graph.snapshot()
    diagnostics = cast(dict[str, Any], state["diagnostics"])
    assert diagnostics["fit"] == {
        "kind": "selection_overlap",
        "ids": shared,
        "conflicts": [{"fits": ["side", "extra"], "ids": shared}],
    }
    diagnostics["fit"]["ids"].clear()
    assert cast(dict[str, Any], graph.snapshot()["diagnostics"])["fit"]["ids"] == shared
    ids = sorted(set(nodes["extra_selection"]["ids"]) - set(shared))
    state = graph.replace(changed(graph, "extra_selection", ids=ids), token(graph))
    assert state["diagnostics"] == {}
    assert graph.evaluate(token(graph))["result"] is not None


def rotational_recipe(graph: FeatureGraph, kind: str = "plane") -> Recipe:
    # Replace only in-memory test observations with known generated geometry.
    # The source file and the user's viewer are never touched.
    from tests.test_mesh_rotational_planes import rotational_geometry

    sides, plane, _, _, group = rotational_geometry()
    if kind != "plane":
        from experiments.mesh_rotational_planes import RotationalPlanes, rotation_matrix

        # Same-type surfaces with complete angular coverage, transformed exactly.
        _, _, _, truth, _ = rotational_geometry()
        origin = np.array([truth[0], truth[1], 0.0])
        points = sides[0].points if kind == "cone" else sides[1].points
        repeated = tuple(
            origin + (points - origin) @ rotation_matrix(truth, i).T for i in range(3)
        )
        group = RotationalPlanes(repeated, tuple(np.ones(len(p)) for p in repeated))
    payload = cast(dict[str, Any], graph.snapshot()["recipe"])
    nodes = {n["id"]: n for n in payload["nodes"]}
    graph.workspace.local = graph.workspace.local.copy()
    offset = 0
    selections: list[list[int]] = []
    for points in [sides[0].points, plane, *group.points]:
        ids = list(range(offset, offset + len(points)))
        graph.workspace.local[ids] = points
        selections.append(ids)
        offset += len(points)
    nodes["outer_band"]["ids"], nodes["top_face"]["ids"] = selections[:2]
    joint = nodes["fit"]
    payload["nodes"].remove(joint)
    for i in range(3):
        payload["nodes"].extend(
            [
                {
                    "id": f"rot_selection_{i}",
                    "label": f"Slope {i}",
                    "operation": "selection",
                    "source": "scan",
                    "ids": selections[i + 2],
                },
                {
                    "id": f"rot_plane_{i}",
                    "label": f"Slope plane {i}",
                    "operation": "fit",
                    "selections": [f"rot_selection_{i}"],
                    "kind": kind,
                    "axial_domain": [-4, 5],
                },
            ]
        )
    payload["nodes"].extend(
        [
            {
                "id": "rotation",
                "label": "Threefold",
                "operation": "rotational_symmetry",
                "axis": "side",
                "planes": [f"rot_plane_{i}" for i in range(3)],
            },
            joint,
        ]
    )
    joint["constraints"].append("rotation")
    return Recipe.model_validate(payload)


def test_rotational_graph_replay_and_input_invalidation(graph: FeatureGraph) -> None:
    _ = graph.replace(rotational_recipe(graph), token(graph))
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert result["fit"]["weighted_rms"] < 1e-8
    assert len(result["surfaces"]) == 5
    for i in range(3):
        assert len(result["surfaces"][f"rot_plane_{i}"]["plane_equation"]) == 4
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    ids = cast(dict[str, Any], state["memberships"])["rot_selection_0"]
    state = graph.replace(changed(graph, "rot_selection_0", ids=ids[::2]), token(graph))
    assert state["result"] is None
    assert cast(dict[str, str], state["states"])["rotation"] == "stale"
    assert "side" in cast(dict[str, Any], state["results"])
    assert graph.evaluate(token(graph))["result"] is not None


@pytest.mark.parametrize("mutation", ["duplicate", "axis", "perpendicular", "forward"])
def test_invalid_rotational_constraints_rejected(
    graph: FeatureGraph, mutation: str
) -> None:
    payload = rotational_recipe(graph).model_dump()
    nodes = {n["id"]: n for n in payload["nodes"]}
    if mutation == "duplicate":
        nodes["rotation"]["planes"][1] = "rot_plane_0"
    elif mutation == "axis":
        nodes["rotation"]["axis"] = "end"
    elif mutation == "perpendicular":
        nodes["perpendicular"]["plane"] = "rot_plane_0"
        payload["nodes"].remove(nodes["perpendicular"])
        payload["nodes"].insert(-1, nodes["perpendicular"])
    else:
        payload["nodes"].remove(nodes["rotation"])
        payload["nodes"].insert(1, nodes["rotation"])
    with pytest.raises(ValueError):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_rotational_lateral_graph_preserves_types_and_replays(
    graph: FeatureGraph, kind: str
) -> None:
    recipe = rotational_recipe(graph, kind)
    _ = graph.replace(recipe, token(graph))
    before = graph.snapshot()["memberships"]
    state = graph.evaluate(token(graph))
    result = cast(dict[str, Any], state["result"])
    assert result["fit"]["weighted_rms"] < 1e-8
    for i in range(3):
        fitted = result["surfaces"][f"rot_plane_{i}"]
        assert fitted["kind"] == kind
        assert "plane_equation" not in fitted
        assert len(fitted["parameters"]) == 7
    assert state["memberships"] == before
    replay = FeatureGraph(graph.workspace, Recipe.model_validate(state["recipe"]))
    assert replay.evaluate(token(replay))["result"] == result
    payload = recipe.model_dump()
    next(n for n in payload["nodes"] if n["id"] == "rot_plane_1")["kind"] = "plane"
    with pytest.raises(ValueError, match="matching fit types"):
        _ = graph.replace(Recipe.model_validate(payload), token(graph))
    assert graph.snapshot()["recipe"] == state["recipe"]
