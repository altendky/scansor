"""Rhino round trips preserve analytic surfaces, mesh topology, and alignment."""

from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import rhino3dm

from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.nozzle_rhino import RhinoExportRequest, export_rhino, surface_brep
from experiments.nozzle_session import NozzleWorkspace

rhino: Any = rhino3dm


@pytest.fixture(scope="module")
def evaluated() -> tuple[NozzleWorkspace, dict[str, Any]]:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    graph = FeatureGraph(
        workspace,
        Recipe.model_validate_json((example / "recipes/cone-plane.json").read_text()),
    )
    return workspace, cast(
        dict[str, Any], graph.evaluate(str(graph.snapshot()["token"]))
    )


@pytest.mark.parametrize("axis_up", [False, True])
def test_export_roundtrip_alignment_mesh_and_analytic_surfaces(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], axis_up: bool
) -> None:
    workspace, snapshot = evaluated
    before = workspace.local.copy()
    request = RhinoExportRequest(
        token=snapshot["token"], target="fit", units="Millimeters", axis_up=axis_up
    )
    data = export_rhino(workspace, snapshot, request)
    model = rhino.File3dm.FromByteArray(data)
    assert model.Settings.ModelUnitSystem == rhino.UnitSystem.Millimeters
    assert len(model.Objects) == 3
    breps = [
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Brep)
    ]
    assert len(breps) == 2
    assert all(b.IsValid and not b.IsSolid and len(b.Faces) == 1 for b in breps)
    assert sum(b.Surfaces[0].IsCone() for b in breps) == 1
    assert sum(b.Surfaces[0].IsPlanar() for b in breps) == 1
    mesh = next(
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Mesh)
    )
    assert len(mesh.Vertices) == len(workspace.local)
    assert mesh.Faces.Count == len(workspace.data.triangles)
    actual = np.array([[v.X, v.Y, v.Z] for v in mesh.Vertices.ToPoint3dArray()])
    if axis_up:
        transform = rhino.Transform.Rotation(
            rhino.Vector3d(*snapshot["result"]["axis_display"]),
            rhino.Vector3d(0, 0, 1),
            rhino.Point3d(0, 0, 0),
        )
        matrix = np.array(
            [[getattr(transform, f"M{i}{j}") for j in range(3)] for i in range(3)]
        )
        offset = matrix @ snapshot["result"]["point_display"]
        offset[2] = 0
        expected = workspace.local @ matrix.T - offset
        # Circular sections of the exported cone must be centered on Z.
        cone = next(b.Surfaces[0] for b in breps if b.Surfaces[0].IsCone())
        u, v = cone.Domain(0), cone.Domain(1)
        for fraction in (0.2, 0.8):
            points = [
                cone.PointAt(u.T0 + t * (u.T1 - u.T0), v.T0 + fraction * (v.T1 - v.T0))
                for t in np.linspace(0, 1, 31)
            ]
            xy = np.array([[p.X, p.Y] for p in points])
            equation = np.column_stack([2 * xy, np.ones(len(xy))])
            center = np.linalg.lstsq(equation, np.sum(xy**2, axis=1), rcond=None)[0][:2]
            np.testing.assert_allclose(center, [0, 0], atol=1e-11)
        np.testing.assert_allclose(
            matrix @ snapshot["result"]["axis_display"], [0, 0, 1], atol=1e-12
        )
    else:
        expected = workspace.data.xyz
    np.testing.assert_allclose(actual, expected, atol=1e-11, rtol=0)
    for index, expected_face in enumerate(workspace.data.triangles):
        face = mesh.Faces[index]
        assert tuple(face[:3]) == tuple(expected_face)
    np.testing.assert_array_equal(workspace.local, before)


def test_export_options_and_rejects_stale_results(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
) -> None:
    workspace, snapshot = evaluated
    request = RhinoExportRequest(
        token=snapshot["token"], target="fit", units="Meters", include_mesh=False
    )
    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))
    assert len(model.Objects) == 2
    assert model.Settings.ModelUnitSystem == rhino.UnitSystem.Meters
    with pytest.raises(StaleGraph):
        _ = export_rhino(
            workspace, snapshot, request.model_copy(update={"token": "stale"})
        )
    with pytest.raises(ValueError, match="evaluate"):
        _ = export_rhino(workspace, {**snapshot, "states": {"fit": "stale"}}, request)


@pytest.mark.parametrize("kind,taper", [("cylinder", 0.0), ("cone", 0.2)])
def test_exact_revolved_side(kind: str, taper: float) -> None:
    positions = np.array([[2, 0, 0], [2 + taper, 0, 1], [0, 2, 0]])
    fitted = {
        "kind": kind,
        "parameters": [0, 0, 0, 0, 2, 0, taper],
        "ids": [0, 1, 2],
        "axial_domain": (-1, 2),
    }
    brep = surface_brep(fitted, positions)
    assert brep.IsValid and not brep.IsSolid
    surface = brep.Surfaces[0]
    assert surface.IsCylinder() if kind == "cylinder" else surface.IsCone()
    for i in range(7):
        u, v = surface.Domain(0), surface.Domain(1)
        p = surface.PointAt(u.T0 + (u.T1 - u.T0) * i / 7, (v.T0 + v.T1) / 2)
        assert np.hypot(p.X, p.Y) == pytest.approx(2 + taper * p.Z, abs=1e-10)


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_symmetry_exports_identical_rotated_bounds_and_can_disable(kind: str) -> None:
    from experiments.nozzle_rhino import joint_breps

    positions: list[Any] = []
    surfaces = {}
    for slot in range(3):
        angle = slot * 2 * np.pi / 3
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        # Deliberately different rectangular/axial spans for every observation set.
        a, b = np.meshgrid(
            np.linspace(-0.3, 0.4 + slot * 0.2, 4),
            np.linspace(0.1, 0.7 + slot * 0.4, 5),
        )
        if kind == "plane":
            local = np.column_stack([a.ravel() + 3, b.ravel(), np.full(a.size, 2.0)])
            p = [0, 0, 1, 2]
        else:
            taper = 0.15 if kind == "cone" else 0.0
            z = b.ravel()
            local = np.column_stack(
                [
                    3 + (1 + taper * z) * np.cos(a.ravel()),
                    (1 + taper * z) * np.sin(a.ravel()),
                    z,
                ]
            )
            center = rotation @ [3, 0, 0]
            p = [center[0], center[1], 0, 0, 1, 0, taper]
        ids = list(range(len(positions), len(positions) + len(local)))
        positions.extend(local @ rotation.T)
        surfaces[str(slot)] = {
            "kind": kind,
            "parameters": p,
            "ids": ids,
            "axial_domain": (-2, 5),
        }
    result = {
        "surfaces": surfaces,
        "axis_display": [0, 0, 1],
        "point_display": [0, 0, 0],
    }
    constraint = {"operation": "rotational_symmetry", "planes": ["0", "1", "2"]}
    patches = joint_breps(result, [constraint], np.array(positions))
    model = rhino.File3dm()
    for patch in patches.values():
        _ = model.Objects.AddBrep(patch)
    import base64

    reread = rhino.File3dm.FromByteArray(base64.b64decode(model.Encode()))
    samples: list[Any] = []
    for slot, obj in enumerate(reread.Objects):
        assert obj.Geometry.IsValid
        surface = obj.Geometry.Surfaces[0]
        u, v = surface.Domain(0), surface.Domain(1)
        points = [
            surface.PointAt(u.T0 + (u.T1 - u.T0) * a, v.T0 + (v.T1 - v.T0) * b)
            for a, b in [(0, 0), (0.2, 0.4), (0.8, 1), (1, 1)]
        ]
        xyz = np.array([[p.X, p.Y, p.Z] for p in points])
        angle = slot * 2 * np.pi / 3
        rotation = np.array(
            [
                [np.cos(angle), -np.sin(angle), 0],
                [np.sin(angle), np.cos(angle), 0],
                [0, 0, 1],
            ]
        )
        samples.append(xyz @ rotation)
    for sample in samples[1:]:
        np.testing.assert_allclose(sample, samples[0], atol=1e-11)
    independent = joint_breps(
        result, [{**constraint, "symmetric_extents": False}], np.array(positions)
    )
    heights = [
        patch.GetBoundingBox().Max.Z - patch.GetBoundingBox().Min.Z
        for patch in independent.values()
    ]
    if kind != "plane":
        assert heights[2] > heights[0] * 1.5
    else:
        left = independent["0"].Surfaces[0].Domain(0)
        right = independent["1"].Surfaces[0].Domain(0)
        assert pytest.approx(right.T1 - right.T0) != left.T1 - left.T0


def test_origin_plane_places_constrained_plane_at_zero(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
) -> None:
    import json

    workspace, snapshot = evaluated
    request = RhinoExportRequest(
        token=snapshot["token"], target="fit", units="Millimeters", origin_plane="end"
    )
    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))
    plane = next(
        obj.Geometry.Surfaces[0]
        for obj in model.Objects
        if obj.Attributes.GetUserString("scansor_fit_id") == "end"
    )
    u, v = plane.Domain(0), plane.Domain(1)
    for a, b in [(0, 0), (0.3, 0.7), (1, 1)]:
        assert (
            abs(plane.PointAt(u.T0 + a * (u.T1 - u.T0), v.T0 + b * (v.T1 - v.T0)).Z)
            < 1e-11
        )
    matrix = np.array(json.loads(model.Strings["scansor_transform_local_to_export"]))
    origin = np.array([*snapshot["result"]["plane_point_display"], 1])
    np.testing.assert_allclose((matrix @ origin)[:3], 0, atol=1e-11)
    mesh = next(
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Mesh)
    )
    actual = np.array([[v.X, v.Y, v.Z] for v in mesh.Vertices.ToPoint3dArray()])
    np.testing.assert_allclose(
        actual, workspace.local @ matrix[:3, :3].T + matrix[:3, 3], atol=1e-11
    )
    for changes, message in [
        ({"axis_up": False}, "requires axis-up"),
        ({"origin_plane": "side"}, "must be a plane"),
        ({"origin_plane": "missing"}, "must be a plane"),
    ]:
        with pytest.raises(ValueError, match=message):
            _ = export_rhino(workspace, snapshot, request.model_copy(update=changes))
    from copy import deepcopy

    parallel = deepcopy(snapshot)
    parallel["results"]["fit"]["surfaces"]["end"]["plane_equation"] = [
        1,
        0,
        -snapshot["result"]["axis_display"][0] / snapshot["result"]["axis_display"][2],
        1,
    ]
    with pytest.raises(ValueError, match="parallel"):
        _ = export_rhino(workspace, parallel, request)
