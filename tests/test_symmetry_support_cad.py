"""Independent world-space endpoint checks for pooled observation CAD patches."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

import experiments.nozzle_cad as cad
from experiments.feature_graph import FeatureGraph, SurfaceFit
from experiments.support_intent import (
    PlaneSupport,
    PointSupport,
    SelectionSupport,
    support_record,
)
from tests.symmetry_fixture import SurfaceKind, SymmetryMode, raw_symmetry_fixture
from tests.test_scan_symmetry_graph import graph_token, named_result, symmetry_recipe


def rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    x, y, z = axis
    skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    return (
        np.cos(angle) * np.eye(3)
        + np.sin(angle) * skew
        + (1 - np.cos(angle)) * np.outer(axis, axis)
    )


def fixture(
    kind: str, mode: str, oblique: bool, support: str
) -> tuple[dict[str, Any], dict[str, Any], np.ndarray, list[list[float]]]:
    common = np.array([np.sqrt(2), 0, 1]) / np.sqrt(3)
    center = np.array([2.0, -3, 4])
    normal = np.array([-0.5, 0.5, 1 / np.sqrt(2)])
    pose_axis = np.array([1.0, -2, 3]) / np.sqrt(14)
    pose = rotation(pose_axis, 0.67) if oblique else np.eye(3)
    translation = np.array([7.0, -4, 2]) if oblique else np.zeros(3)
    count = 3 if mode == "rotation" else 2
    observed = [[8.0, 10], [10.0, 12], [9.0, 11]]
    hard = [[8.0, 9], [10.0, 11.5], [8.5, 12]]
    expected: list[list[float]] = []
    surfaces: dict[str, Any] = {}
    points: list[np.ndarray] = []
    total = 0
    for index in range(count):
        transform = (
            rotation(common, index * 2 * np.pi / 3)
            if mode == "rotation"
            else np.eye(3) - index * 2 * np.outer(normal, normal)
        )
        anchor = center + transform @ np.array([3.0, 1, -2])
        direction = transform @ np.array([0.0, 0, 1])
        u, v = transform[:, 0], transform[:, 1]
        angle, height = np.meshgrid(np.arange(12) * 2 * np.pi / 12, observed[index])
        radius, slope = (-1.0, 0.5) if kind == "cone" else (2.0, 0.0)
        rows = (
            anchor
            + height.ravel()[:, None] * direction
            + (radius + slope * height.ravel())[:, None]
            * (np.cos(angle.ravel())[:, None] * u + np.sin(angle.ravel())[:, None] * v)
        )
        anchor, direction = pose @ anchor + translation, pose @ direction
        points.append(rows @ pose.T + translation)
        automatic = support == "auto" or (support == "mixed" and index == 0)
        domain = observed[index] if automatic else hard[index]
        if support == "mixed" and index > 0:
            domain = [[9.0, 10.5], [11.0, 15]][index - 1]
        if automatic:
            intent = SelectionSupport()
            expected.append([8.0, 12])
        elif (support == "mixed" and index == 2) or (support != "mixed" and index % 2):
            # The authored plane passes through this member's canonical anchor.
            intent = PlaneSupport(
                (float(direction[0]), float(direction[1]), float(direction[2])),
                float(direction @ anchor),
                (domain[0], domain[1]),
            )
            expected.append([max(7.8, domain[0]), min(12.2, domain[1])])
        else:
            intent = PointSupport(
                (float(anchor[0]), float(anchor[1]), float(anchor[2])),
                (float(direction[0]), float(direction[1]), float(direction[2])),
                (domain[0], domain[1]),
            )
            expected.append([max(7.8, domain[0]), min(12.2, domain[1])])
        surfaces[str(index)] = {
            "kind": kind,
            "analytic": {
                "kind": kind,
                "origin": anchor.tolist(),
                "axis": direction.tolist(),
                "radius": radius,
                "slope": slope,
                "axial_domain": domain,
            },
            "support_intent": support_record(intent),
            "ids": list(range(total, total + len(rows))),
        }
        total += len(rows)
    posed_normal, posed_center = pose @ normal, pose @ center + translation
    result = {
        "surfaces": surfaces,
        "axis_display": (pose @ common).tolist(),
        "point_display": posed_center.tolist(),
        "mirror_planes": {
            "clock": {
                "plane_equation": [
                    *posed_normal.tolist(),
                    float(posed_normal @ posed_center),
                ]
            }
        },
    }
    constraint = {
        "operation": "rotational_symmetry" if mode == "rotation" else "mirror_symmetry",
        "planes" if mode == "rotation" else "surfaces": list(surfaces),
        "plane": "clock",
    }
    return result, constraint, np.vstack(points), expected


def check_endpoints(
    shape: TopoDS_Shape, geometry: dict[str, Any], expected: list[float]
) -> np.ndarray:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    explorer.Next()
    assert not explorer.More()
    lower_u, upper_u, lower_v, upper_v = BRepTools.UVBounds_s(face)
    surface = BRepAdaptor_Surface(face)
    points = np.array(
        [
            surface.Value(u, v).Coord()
            for v in (lower_v, upper_v)
            for u in np.linspace(lower_u, upper_u, 7)
        ]
    )
    relative = points - geometry["origin"]
    direction = np.array(geometry["axis"])
    heights = relative @ direction
    # OCCT cone V is generator distance, so compare actual world points instead.
    np.testing.assert_allclose(
        np.sort(heights.reshape(2, 7).mean(axis=1)), expected, rtol=0, atol=1e-7
    )
    np.testing.assert_allclose(
        np.ptp(heights.reshape(2, 7), axis=1), 0, rtol=0, atol=1e-7
    )
    radii = np.linalg.norm(relative - heights[:, None] * direction, axis=1)
    np.testing.assert_allclose(
        radii,
        geometry["radius"] + geometry["slope"] * heights,
        rtol=0,
        atol=1e-7,
    )
    assert np.all(radii > 0)
    return points


def step_roundtrip(shape: TopoDS_Shape, path: Path) -> TopoDS_Shape:
    _ = path.write_bytes(
        cad._step_bytes({"member": shape}, {"member": "Member"}, "Millimeters")  # pyright: ignore[reportPrivateUsage]
    )
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    return reader.OneShape()


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("oblique", [False, True])
@pytest.mark.parametrize("support", ["hard", "auto", "mixed"])
def test_pooled_bounds_keep_each_members_support(
    kind: str, mode: str, oblique: bool, support: str, tmp_path: Path
) -> None:
    result, constraint, positions, expected = fixture(kind, mode, oblique, support)
    saved = deepcopy(result)
    patches = cad.joint_shapes(result, [constraint], positions)
    assert set(patches) == set(result["surfaces"])
    for index, (name, shape) in enumerate(patches.items()):
        geometry = result["surfaces"][name]["analytic"]
        _ = check_endpoints(shape, geometry, expected[index])
        restored = step_roundtrip(shape, tmp_path / f"{name}.step")
        _ = check_endpoints(restored, geometry, expected[index])
    assert result == saved


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("oblique", [False, True])
def test_disabled_pooling_preserves_local_automatic_bounds(
    kind: str, mode: str, oblique: bool
) -> None:
    result, constraint, positions, _ = fixture(kind, mode, oblique, "auto")
    patches = cad.joint_shapes(
        result, [{**constraint, "symmetric_extents": False}], positions
    )
    for name, shape in patches.items():
        geometry = result["surfaces"][name]["analytic"]
        _ = check_endpoints(shape, geometry, geometry["axial_domain"])


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("oblique", [False, True])
def test_disjoint_member_caps_fail_the_entire_collection(
    kind: str, mode: str, oblique: bool
) -> None:
    result, constraint, positions, _ = fixture(kind, mode, oblique, "hard")
    member = result["surfaces"][str(len(result["surfaces"]) - 1)]
    member["analytic"]["axial_domain"] = [13, 14]
    member["support_intent"]["interval"] = [13, 14]
    with pytest.raises(ValueError, match="outside axial support"):
        _ = cad.joint_shapes(result, [constraint], positions)


@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("support", ["hard", "auto"])
def test_cone_anchor_rebase_and_reversal_preserve_world_caps(
    mode: str, reverse: bool, support: str, tmp_path: Path
) -> None:
    result, constraint, positions, expected = fixture("cone", mode, True, support)
    original = cad.joint_shapes(result, [constraint], positions)
    for surface in result["surfaces"].values():
        geometry = surface["analytic"]
        geometry["origin"] = (
            np.array(geometry["origin"]) + 20 * np.array(geometry["axis"])
        ).tolist()
        geometry["radius"] = 9.0
        geometry["axial_domain"] = [x - 20 for x in geometry["axial_domain"]]
        if reverse:
            geometry["axis"] = (-np.array(geometry["axis"])).tolist()
            geometry["slope"] = -0.5
            geometry["axial_domain"] = [-x for x in reversed(geometry["axial_domain"])]
        intent = surface["support_intent"]
        if intent["kind"] == "point":
            intent.update(
                reference=geometry["origin"],
                direction=geometry["axis"],
                interval=geometry["axial_domain"],
            )
        elif reverse and intent["kind"] == "reference-plane":
            intent["normal"] = (-np.array(intent["normal"])).tolist()
            intent["offset"] *= -1
            intent["interval"] = [-x for x in reversed(intent["interval"])]
    changed = cad.joint_shapes(result, [constraint], positions)
    for index, (name, shape) in enumerate(changed.items()):
        geometry = result["surfaces"][name]["analytic"]
        bounds = [x - 20 for x in expected[index]]
        if reverse:
            bounds = [-x for x in reversed(bounds)]
        points = check_endpoints(shape, geometry, bounds)
        _ = check_endpoints(
            step_roundtrip(shape, tmp_path / f"{name}.step"), geometry, bounds
        )
        # Both representations have the same world endpoint circles, regardless
        # of the OCCT seam, apex chart, or endpoint order.
        old_geometry = deepcopy(geometry)
        old_geometry["axis"] = (
            -np.array(geometry["axis"]) if reverse else np.array(geometry["axis"])
        ).tolist()
        old_geometry["origin"] = (
            np.array(geometry["origin"]) - 20 * np.array(old_geometry["axis"])
        ).tolist()
        old_geometry["radius"], old_geometry["slope"] = -1.0, 0.5
        _ = check_endpoints(original[name], old_geometry, expected[index])
        world_heights = (points - old_geometry["origin"]) @ np.array(
            old_geometry["axis"]
        )
        np.testing.assert_allclose(
            [world_heights.min(), world_heights.max()],
            expected[index],
            rtol=0,
            atol=1e-7,
        )


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_canonical_without_support_provenance_keeps_its_caps(
    kind: str, mode: str
) -> None:
    result, constraint, positions, expected = fixture(kind, mode, True, "hard")
    for surface in result["surfaces"].values():
        del surface["support_intent"]
    patches = cad.joint_shapes(result, [constraint], positions)
    for index, (name, shape) in enumerate(patches.items()):
        _ = check_endpoints(
            shape, result["surfaces"][name]["analytic"], expected[index]
        )


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
@pytest.mark.parametrize("bounded", [False, True])
def test_legacy_member_caps_and_absent_domain_keep_their_semantics(
    kind: str, mode: str, bounded: bool
) -> None:
    count = 3 if mode == "rotation" else 2
    normal = np.array([1.0, 0, 0])
    surfaces: dict[str, Any] = {}
    positions: list[np.ndarray] = []
    domains = [[8.0, 9], [10.0, 11.5], [8.5, 12]]
    observed = [[8.0, 10], [10.0, 12], [9.0, 11]]
    radius, slope = (-1.0, 0.5) if kind == "cone" else (2.0, 0.0)
    total = 0
    for index in range(count):
        matrix = (
            rotation(np.array([0.0, 0, 1]), index * 2 * np.pi / 3)
            if mode == "rotation"
            else np.eye(3) - index * 2 * np.outer(normal, normal)
        )
        anchor = matrix @ [3.0, 1, 0]
        angle, height = np.meshgrid(np.arange(12) * 2 * np.pi / 12, observed[index])
        rows = (
            np.column_stack(
                [
                    3 + (radius + slope * height.ravel()) * np.cos(angle.ravel()),
                    1 + (radius + slope * height.ravel()) * np.sin(angle.ravel()),
                    height.ravel(),
                ]
            )
            @ matrix.T
        )
        positions.append(rows)
        surfaces[str(index)] = {
            "kind": kind,
            "parameters": [float(anchor[0]), float(anchor[1]), 0, 0, radius, 0, slope],
            "ids": list(range(total, total + len(rows))),
        }
        if bounded:
            surfaces[str(index)]["axial_domain"] = domains[index]
        total += len(rows)
    result = {
        "surfaces": surfaces,
        "axis_display": [0, 0, 1],
        "point_display": [0, 0, 0],
        "mirror_planes": {"clock": {"plane_equation": [1, 0, 0, 0]}},
    }
    constraint = {
        "operation": "rotational_symmetry" if mode == "rotation" else "mirror_symmetry",
        "planes" if mode == "rotation" else "surfaces": list(surfaces),
        "plane": "clock",
    }
    patches = cad.joint_shapes(result, [constraint], np.vstack(positions))
    for index, (name, shape) in enumerate(patches.items()):
        parameters = surfaces[name]["parameters"]
        _ = check_endpoints(
            shape,
            {
                "origin": [*parameters[:2], 0],
                "axis": [0, 0, 1],
                "radius": radius,
                "slope": slope,
            },
            domains[index] if bounded else [7.8, 12.2],
        )


def face_corners(shape: TopoDS_Shape) -> np.ndarray:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    explorer.Next()
    assert not explorer.More()
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    surface = BRepAdaptor_Surface(face)
    return np.array([surface.Value(u, v).Coord() for u in (u0, u1) for v in (v0, v1)])


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
@pytest.mark.parametrize("mode", ["rotation", "mirror"])
def test_selected_named_graph_exports_member_support_to_step(
    kind: SurfaceKind, mode: SymmetryMode, tmp_path: Path
) -> None:
    source = raw_symmetry_fixture(kind, mode, oblique=True, support_kind="auto")
    recipe = symmetry_recipe(source, explicit_support=kind == "cone")
    graph = FeatureGraph(source.workspace, recipe)
    state = graph.evaluate(graph_token(graph))
    assert cast(dict[str, str], state["states"])["joint"] == "ready", state["errors"]
    result = named_result(state)
    saved = deepcopy(result)
    constraint = next(
        node.model_dump(mode="json")
        for node in recipe.nodes
        if node.operation in {"rotational_symmetry", "mirror_symmetry"}
    )
    positions = np.asarray(source.workspace.local)
    patches = cad.joint_shapes(result, [constraint], positions)
    assert set(patches) == set(source.truth)

    # Independently construct the actual solved symmetry transforms. They are
    # display pooling maps, not the fixture truth transforms or production CAD
    # transformation helpers; source quantization may move the fitted geometry.
    center = np.array(result["point_display"])
    common = np.array(result["axis_display"])
    common /= np.linalg.norm(common)
    transforms: list[tuple[np.ndarray, np.ndarray]] = []
    for index in range(len(source.members)):
        if mode == "rotation":
            matrix = rotation(common, index * 2 * np.pi / 3)
            offset = center - matrix @ center
        else:
            equation = np.array(result["mirror_planes"]["clock"]["plane_equation"])
            norm = np.linalg.norm(equation[:3])
            normal, distance = equation[:3] / norm, equation[3] / norm
            matrix = np.eye(3) - index * 2 * np.outer(normal, normal)
            offset = index * 2 * distance * normal
        transforms.append((matrix, offset))
    pooled = np.vstack(
        [
            (positions[source.ids[name]] - offset) @ matrix
            for name, (matrix, offset) in zip(source.members, transforms, strict=True)
        ]
    )
    if kind == "plane":
        equation = np.array(result["surfaces"][source.members[0]]["plane_equation"])
        normal = equation[:3] / np.linalg.norm(equation[:3])
        reference = np.eye(3)[int(np.argmin(abs(normal)))]
        u = np.cross(reference, normal)
        u /= np.linalg.norm(u)
        basis = np.column_stack([u, np.cross(normal, u)])
        uv = pooled @ basis
        lower, upper = uv.min(axis=0), uv.max(axis=0)
        pad = 0.05 * (upper - lower)
        lower, upper = lower - pad, upper + pad
        for name, (matrix, offset) in zip(source.members, transforms, strict=True):
            published = np.array(result["surfaces"][name]["plane_equation"])
            for shape in (
                patches[name],
                step_roundtrip(patches[name], tmp_path / f"{name}.step"),
            ):
                corners = face_corners(shape)
                np.testing.assert_allclose(
                    corners @ published[:3], published[3], rtol=0, atol=1e-7
                )
                actual = ((corners - offset) @ matrix) @ basis
                np.testing.assert_allclose(actual.min(axis=0), lower, rtol=0, atol=1e-7)
                np.testing.assert_allclose(actual.max(axis=0), upper, rtol=0, atol=1e-7)
    else:
        authored = {
            node.id: node for node in recipe.nodes if isinstance(node, SurfaceFit)
        }
        widened = False
        for name, (matrix, offset) in zip(source.members, transforms, strict=True):
            surface = result["surfaces"][name]
            geometry = surface["analytic"]
            origin, direction = np.array(geometry["origin"]), np.array(geometry["axis"])
            member_pool = pooled @ matrix.T + offset
            heights = (member_pool - origin) @ direction
            lower, upper = float(heights.min()), float(heights.max())
            if kind == "cone":
                # These saved hard intervals are distances from the current
                # line's intersection with SOURCE Z=0, not its centroid anchor.
                assert direction[2] > 0
                interval = authored[name].axial_domain
                assert interval is not None
                resolved = np.array(interval) - origin[2] / direction[2]
                np.testing.assert_allclose(
                    geometry["axial_domain"], resolved, rtol=0, atol=1e-10
                )
                assert surface["support_intent"] == {
                    "kind": "reference-plane",
                    "normal": [0.0, 0.0, 1.0],
                    "offset": 0.0,
                    "interval": list(interval),
                }
                pad = 0.05 * (upper - lower)
                expected = [
                    max(lower - pad, resolved[0]),
                    min(upper + pad, resolved[1]),
                ]
            else:
                assert surface["support_intent"] == {"kind": "selection"}
                expected = [lower, upper]
                widened |= not np.allclose(
                    expected, geometry["axial_domain"], rtol=0, atol=1e-7
                )
            _ = check_endpoints(patches[name], geometry, expected)
            _ = check_endpoints(
                step_roundtrip(patches[name], tmp_path / f"{name}.step"),
                geometry,
                expected,
            )
        if kind == "cylinder":
            assert widened
    assert result == saved
