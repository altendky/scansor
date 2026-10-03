"""Shared physical boundaries preserve fitting evidence and resolved geometry."""

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.surface_extents import circle_intersection, primitive, trimmed_face


def side(radius: float = 3, slope: float = 0) -> dict[str, Any]:
    return {
        "kind": "cone" if slope else "cylinder",
        "parameters": [1, 2, 0, 0, radius, 0, slope],
    }


def plane(z: float = 2, sign: float = 1) -> dict[str, Any]:
    return {"kind": "plane", "plane_equation": [0, 0, sign, sign * z]}


def boundary(
    surface: dict[str, Any], z: float, keep: str, name: str = "edge", sign: float = 1
) -> tuple[dict[str, Any], dict[str, Any]]:
    return {"intersection": name, "keep": keep}, circle_intersection(
        surface, plane(z, sign)
    )


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("slope", [0, 0.2, -0.1])
def test_circles_lie_on_both_surfaces(sign: float, slope: float) -> None:
    surface = side(slope=slope)
    surface["parameters"][2:4] = [0.2, -0.1]
    geometry = primitive(surface)
    axis, origin = np.asarray(geometry["axis"]), np.asarray(geometry["origin"])
    distance = float(axis @ origin + 2)
    cut = {"kind": "plane", "plane_equation": [*(sign * axis), sign * distance]}
    edge = circle_intersection(surface, cut)
    reverse = circle_intersection(cut, surface)
    assert edge == reverse
    np.testing.assert_allclose(edge["center_display"], origin + 2 * axis)
    assert edge["radius"] == pytest.approx(3 + 2 * slope)
    points = np.asarray(edge["preview"]["positions"]).reshape(-1, 3)
    np.testing.assert_allclose(
        np.cross(edge["basis_u_display"], edge["basis_v_display"]),
        edge["axis_display"],
        atol=1e-12,
    )
    np.testing.assert_allclose(points @ axis, distance, atol=1e-12)
    axial = (points - origin) @ axis
    np.testing.assert_allclose(
        np.linalg.norm(points - origin - axial[:, None] * axis, axis=1),
        3 + slope * axial,
        atol=1e-12,
    )


@pytest.mark.parametrize(
    "bad,cut,message",
    [
        (side(), {"kind": "plane", "plane_equation": [0.01, 0, 1, 2]}, "oblique"),
        (side(-1), plane(), "positive finite"),
        (side(1, -0.5), plane(), "apex"),
        (side(), {"kind": "plane", "plane_equation": [0, 0, 0, 2]}, "nonzero"),
        (
            side(),
            {"kind": "plane", "plane_equation": [0, 0, 1, float("inf")]},
            "finite",
        ),
        (side(), side(4), "plane and cylinder"),
    ],
)
def test_intersection_rejects_unsupported_or_degenerate_geometry(
    bad: dict[str, Any], cut: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _ = circle_intersection(bad, cut)


@pytest.mark.parametrize("sign", [-1, 1])
def test_two_shared_circles_bound_lateral_face(sign: float) -> None:
    surface = side()
    uses = [
        boundary(surface, 1, "positive" if sign > 0 else "negative", "bottom", sign),
        boundary(surface, 4, "negative" if sign > 0 else "positive", "top", sign),
    ]
    result = trimmed_face(surface, uses, np.empty((0, 3)))
    assert result["bounded"] and not result["preview_clipped"]
    assert result["bounds"]["axial"] == [1, 4]
    assert result["boundary_ids"] == ["bottom", "top"]
    points = np.asarray(result["preview"]["positions"]).reshape(-1, 3)
    np.testing.assert_allclose(np.linalg.norm(points[:, :2] - [1, 2], axis=1), 3)
    assert set(points[:, 2]) == {1, 4}


def test_annulus_and_disk_have_explicit_physical_radial_limits() -> None:
    outer = boundary(side(4), 2, "inside", "outer")
    inner = boundary(side(2), 2, "outside", "inner")
    result = trimmed_face(plane(), [outer, inner], np.empty((0, 3)))
    assert result["bounds"]["radial"] == [2, 4]
    assert result["bounded"]
    points = np.asarray(result["preview"]["positions"]).reshape(-1, 3)
    radii = np.linalg.norm(points[:, :2] - [1, 2], axis=1)
    assert np.all(radii >= 2 - 1e-12) and np.all(radii <= 4 + 1e-12)
    assert np.any(np.isclose(radii, 2)) and np.any(np.isclose(radii, 4))
    disk = trimmed_face(plane(), [outer], np.empty((0, 3)))
    assert disk["bounds"]["radial"] == [0, 4]
    triangles = np.asarray(disk["preview"]["indices"]).reshape(-1, 3)
    vertices = np.asarray(disk["preview"]["positions"]).reshape(-1, 3)
    assert np.all(
        np.linalg.norm(
            np.cross(
                vertices[triangles[:, 1]] - vertices[triangles[:, 0]],
                vertices[triangles[:, 2]] - vertices[triangles[:, 0]],
            ),
            axis=1,
        )
        > 0
    )
    for face in (result, disk):
        triangles = np.asarray(face["preview"]["indices"]).reshape(-1, 3)
        points = np.asarray(face["preview"]["positions"]).reshape(-1, 3)
        normals = np.cross(
            points[triangles[:, 1]] - points[triangles[:, 0]],
            points[triangles[:, 2]] - points[triangles[:, 0]],
        )
        assert np.all(normals @ face["geometry"]["axis"] > 0)


@pytest.mark.parametrize(
    "surface,uses,message",
    [
        (
            plane(),
            [
                boundary(side(2), 2, "inside", "small"),
                boundary(side(4), 2, "outside", "large"),
            ],
            "empty",
        ),
        (plane(), [boundary(side(2), 1, "inside")], "face plane"),
        (plane(), [boundary(side(), 2, "positive")], "inside/outside"),
        (side(), [boundary(side(), 2, "inside")], "positive/negative"),
        (side(), [boundary(side(4), 2, "positive")], "radius does not match"),
        (
            side(),
            [
                boundary(side(), 2, "positive", "a"),
                boundary(side(), 1, "negative", "b"),
            ],
            "empty",
        ),
    ],
)
def test_invalid_face_regions_fail(
    surface: dict[str, Any],
    uses: list[tuple[dict[str, Any], dict[str, Any]]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _ = trimmed_face(surface, uses, np.empty((0, 3)))


def test_nonconcentric_boundaries_retain_their_actual_centers() -> None:
    first = boundary(side(2), 2, "outside", "first")
    second = boundary(side(4), 2, "inside", "second")
    second[1]["center_display"][0] += 0.1
    result = trimmed_face(plane(), [first, second], np.empty((0, 3)))
    assert result["bounded"]
    assert result["bounds"]["loops"][0]["center_display"] == second[1]["center_display"]
    assert result["bounds"]["loops"][1]["center_display"] == first[1]["center_display"]


def test_incomplete_face_has_null_endpoint_and_display_only_coverage() -> None:
    uses = [boundary(side(), 2, "positive")]
    first = trimmed_face(side(), uses, np.array([[4, 2, 3], [4, 2, 5]]))
    second = trimmed_face(side(), uses, np.array([[4, 2, 3], [4, 2, 9]]))
    assert first["bounds"]["axial"] == second["bounds"]["axial"] == [2, None]
    assert not first["bounded"] and first["preview_clipped"]
    assert first["preview"] != second["preview"]


def test_rounded_kernel_circle_does_not_admit_near_apex_preview() -> None:
    surface = side(1, 1)
    use, edge = boundary(surface, 2, "negative")
    # Kernel roundoff can put the preview crop infinitesimally above the apex;
    # positive by one ulp is not sufficient evidence for a valid side region.
    edge["radius"] = float(np.nextafter(3.0, 0))
    with pytest.raises(ValueError, match="crosses the cone apex"):
        _ = trimmed_face(surface, [(use, edge)], np.empty((0, 3)))


@pytest.fixture(scope="module")
def workspace() -> NozzleWorkspace:
    return NozzleWorkspace(Path("examples/nozzle-bayonette-simplified"))


def recipe() -> dict[str, Any]:
    return Recipe.model_validate_json(
        Path("examples/nozzle-bayonette-simplified/recipes/cone-plane.json").read_text()
    ).model_dump()


def append_topology(payload: dict[str, Any], *, direct: bool = False) -> None:
    side_ref = {"feature": "side"} if direct else {"feature": "fit", "surface": "side"}
    plane_ref = {"feature": "end"} if direct else {"feature": "fit", "surface": "end"}
    payload["nodes"].extend(
        [
            {
                "id": "shared_edge",
                "label": "Shared edge",
                "operation": "surface_intersection",
                "first": side_ref,
                "second": plane_ref,
            },
            {
                "id": "shoulder_face",
                "label": "Shoulder face",
                "operation": "trimmed_face",
                "surface": plane_ref,
                "boundaries": [{"intersection": "shared_edge", "keep": "inside"}],
            },
            {
                "id": "wall_face",
                "label": "Wall face",
                "operation": "trimmed_face",
                "surface": side_ref,
                "boundaries": [{"intersection": "shared_edge", "keep": "negative"}],
            },
        ]
    )
    payload["output"] = "shoulder_face"


def test_explicit_joint_context_shared_faces_replay_without_evidence_changes(
    workspace: NozzleWorkspace,
) -> None:
    base = FeatureGraph(workspace, Recipe.model_validate(recipe()))
    before = cast(dict[str, Any], base.evaluate(str(base.snapshot()["token"])))
    payload = recipe()
    append_topology(payload)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    state = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert state["results"]["fit"] == before["results"]["fit"]
    assert state["memberships"] == before["memberships"]
    assert state["results"]["side"] == before["results"]["side"]
    solved = state["results"]["fit"]
    np.testing.assert_allclose(
        state["results"]["shared_edge"]["center_display"], solved["plane_point_display"]
    )
    assert (
        state["results"]["shoulder_face"]["boundary_ids"]
        == state["results"]["wall_face"]["boundary_ids"]
        == ["shared_edge"]
    )
    assert state["results"]["shoulder_face"]["bounded"]
    assert not state["results"]["wall_face"]["bounded"]
    replay = FeatureGraph(workspace, Recipe.model_validate(state["recipe"]))
    assert (
        replay.evaluate(str(replay.snapshot()["token"]), all_actions=True)["results"]
        == state["results"]
    )


@pytest.mark.parametrize(
    "mutation,message",
    [
        ({"feature": "fit"}, "explicit member"),
        ({"feature": "side", "surface": "side"}, "must not specify"),
        ({"feature": "outer_band"}, "surface reference"),
    ],
)
def test_surface_context_validation(
    workspace: NozzleWorkspace, mutation: dict[str, str], message: str
) -> None:
    payload = recipe()
    append_topology(payload)
    payload["nodes"][-3]["first"] = mutation
    with pytest.raises(ValueError, match=message):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_face_cannot_substitute_independent_fit_for_solved_boundary(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    append_topology(payload)
    payload["nodes"][-1]["surface"] = {"feature": "side"}
    with pytest.raises(ValueError, match="same surface context"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_refit_invalidates_shared_edge_and_both_faces(
    workspace: NozzleWorkspace,
) -> None:
    payload = recipe()
    append_topology(payload)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    old = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    payload["nodes"][1]["ids"] = payload["nodes"][1]["ids"][::2]
    stale = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(payload), old["token"])
    )
    for key in ("shared_edge", "shoulder_face", "wall_face"):
        assert stale["states"][key] == "stale"
        assert key not in stale["results"]
    new = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    assert (
        old["results"]["shared_edge"]["radius"]
        != new["results"]["shared_edge"]["radius"]
    )


def connected_recipe(workspace: NozzleWorkspace) -> dict[str, Any]:
    payload = recipe()
    base = {node["id"]: node for node in payload["nodes"]}
    payload["nodes"] = [base[key] for key in ("scan", "outer_band", "top_face")]
    payload["nodes"].append(
        {
            "id": "axis",
            "label": "Axis",
            "operation": "axis",
            "initial_parameters": workspace.data.selection["initial_parameters"][:4],
        }
    )
    for key in ("side", "end"):
        base[key]["axis"] = "axis"
        payload["nodes"].append(base[key])
    append_topology(payload, direct=True)
    return payload


def test_connected_sibling_edits_invalidate_downstream_topology(
    workspace: NozzleWorkspace,
) -> None:
    payload = connected_recipe(workspace)
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    old = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    )
    resolved = old["results"]["side"]["parameters"]
    cut = primitive(old["results"]["end"])["offset"]
    axis = np.asarray([resolved[2], resolved[3], 1.0])
    axis /= np.linalg.norm(axis)
    t = cut - axis @ [resolved[0], resolved[1], 0]
    assert old["results"]["shared_edge"]["radius"] == pytest.approx(
        resolved[4] + resolved[6] * t
    )
    # The boundary only depends on end and a datum; editing side is a hidden
    # connected-geometry influence, not an ordinary path to these consumers.
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["id"] not in ("shoulder_face", "wall_face")
    ]
    payload["nodes"].insert(
        -1,
        {
            "id": "datum",
            "label": "Datum",
            "operation": "reference_plane",
            "axis": "axis",
            "construction": "perpendicular_to_axis",
            "initial_angle_degrees": None,
            "offset": 2,
        },
    )
    payload["nodes"][-1]["second"] = {"feature": "datum"}
    payload["output"] = "shared_edge"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    old = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), "shared_edge")
    )
    changed = deepcopy(payload)
    changed["nodes"][2]["ids"] = changed["nodes"][2]["ids"][::2]
    state = cast(
        dict[str, Any], graph.replace(Recipe.model_validate(changed), old["token"])
    )
    assert state["states"]["side"] == state["states"]["shared_edge"] == "stale"


def test_boundary_evaluation_waits_for_later_connected_fit(
    workspace: NozzleWorkspace,
) -> None:
    payload = connected_recipe(workspace)
    payload["nodes"] = [
        node
        for node in payload["nodes"]
        if node["id"] not in ("shoulder_face", "wall_face")
    ]
    datum = {
        "id": "datum",
        "label": "Datum",
        "operation": "reference_plane",
        "axis": "axis",
        "construction": "perpendicular_to_axis",
        "initial_angle_degrees": None,
        "offset": 2,
    }
    payload["nodes"].insert(5, datum)
    edge = next(node for node in payload["nodes"] if node["id"] == "shared_edge")
    edge["second"] = {"feature": "datum"}
    payload["nodes"].remove(edge)
    payload["nodes"].insert(6, edge)  # before end, which participates in the solve
    payload["output"] = "shared_edge"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    result = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    expected = circle_intersection(
        result["results"]["side"], result["results"]["datum"]
    )
    assert result["results"]["shared_edge"]["radius"] == expected["radius"]


def test_explicit_solve_requires_stable_named_context_for_boundaries(
    workspace: NozzleWorkspace,
) -> None:
    payload = connected_recipe(workspace)
    payload["nodes"].insert(
        6,
        {
            "id": "solve",
            "label": "Explicit solve",
            "operation": "axis_solve",
            "axis": "axis",
            "factors": ["side", "end"],
        },
    )
    with pytest.raises(ValueError, match="explicitly solved axis is ambiguous"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))
    for node in payload["nodes"]:
        if node["operation"] == "surface_intersection":
            node["first"] = {"feature": "solve", "surface": "side"}
            node["second"] = {"feature": "solve", "surface": "end"}
        if node["operation"] == "trimmed_face":
            node["surface"] = {
                "feature": "solve",
                "surface": "end" if node["id"] == "shoulder_face" else "side",
            }
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    first = cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]), "shared_edge")
    )
    second = cast(dict[str, Any], graph.evaluate(first["token"], "solve"))
    assert second["results"]["shared_edge"] == first["results"]["shared_edge"]
    assert second["states"]["shared_edge"] == "ready"


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_circle_and_face_uniform_scaling_and_translation(scale: float) -> None:
    surface = side(3 * scale)
    surface["parameters"][:2] = [100 * scale, -500 * scale]
    lower, upper = 2 * scale, 5 * scale
    first = boundary(surface, lower, "positive", "a")
    second = boundary(surface, upper, "negative", "b")
    face = trimmed_face(surface, [first, second], np.empty((0, 3)))
    np.testing.assert_allclose(
        first[1]["center_display"], [100 * scale, -500 * scale, lower]
    )
    np.testing.assert_allclose(face["bounds"]["axial"], [lower, upper])
    assert face["geometry"]["radius"] == 3 * scale


def test_scaled_plane_equations_are_equivalent() -> None:
    cut = plane()
    expected = circle_intersection(side(), cut)
    cut["plane_equation"] = [0, 0, 10, 20]
    assert circle_intersection(side(), cut) == expected
