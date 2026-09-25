"""Rhino round trips preserve analytic surfaces, mesh topology, and alignment."""

from copy import deepcopy
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


def test_sphere_fit_exports_as_an_exact_sphere() -> None:
    fitted = {
        "kind": "sphere",
        "parameters": [1.2, -0.8, 3.4, 2.5],
        "ids": [0, 1, 2, 3],
        "axial_domain": [-2, 5],
    }
    positions = np.array(
        [[3.7, -0.8, 3.4], [-1.3, -0.8, 3.4], [1.2, 1.7, 3.4], [1.2, -0.8, 5.9]]
    )

    brep = surface_brep(fitted, positions)

    assert brep.IsValid
    assert brep.IsSolid
    assert len(brep.Faces) == 1
    assert brep.Surfaces[0].IsSphere()


@pytest.mark.parametrize("axis_up", [False, True])
def test_standalone_sphere_export_transforms_surface_and_mesh_together(
    axis_up: bool,
) -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    payload = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    ).model_dump()
    base = {node["id"]: node for node in payload["nodes"]}
    payload["nodes"] = [
        base["scan"],
        base["outer_band"],
        {
            "id": "sphere",
            "label": "Sphere",
            "operation": "fit",
            "selections": ["outer_band"],
            "kind": "sphere",
            "axial_domain": [-2, 5],
        },
    ]
    payload["output"] = "sphere"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    request = RhinoExportRequest(
        token=snapshot["token"],
        target="sphere",
        units="Millimeters",
        axis_up=axis_up,
    )

    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))

    brep = next(
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Brep)
    )
    assert brep.IsValid and brep.IsSolid and brep.Surfaces[0].IsSphere()
    mesh = next(
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Mesh)
    )
    actual = np.array(
        [[point.X, point.Y, point.Z] for point in mesh.Vertices.ToPoint3dArray()]
    )
    if axis_up:
        center = np.asarray(snapshot["results"]["sphere"]["parameters"][:3])
        expected = workspace.local - np.array([center[0], center[1], 0.0])
    else:
        expected = workspace.data.xyz
    np.testing.assert_allclose(actual, expected, atol=1e-11, rtol=0)


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


def test_explicit_output_transform_applies_to_surfaces_and_mesh(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]],
) -> None:
    workspace, original = evaluated
    snapshot = deepcopy(original)
    snapshot["recipe"]["nodes"].append(
        {
            "id": "output_transform",
            "label": "Output transform",
            "operation": "transform",
            "frame": "output_frame",
            "scale": "output_scale",
        }
    )
    snapshot["states"]["output_transform"] = "ready"
    matrix = np.array(
        [
            [0.0, -2.0, 0.0, 7.0],
            [2.0, 0.0, 0.0, -3.0],
            [0.0, 0.0, 2.0, 5.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    snapshot["results"]["output_transform"] = {"matrix": matrix.tolist()}
    request = RhinoExportRequest(
        token=snapshot["token"],
        target="fit",
        units="Millimeters",
        axis_up=False,
        transform="output_transform",
    )

    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))
    mesh = next(
        obj.Geometry for obj in model.Objects if isinstance(obj.Geometry, rhino.Mesh)
    )
    actual = np.array(
        [[point.X, point.Y, point.Z] for point in mesh.Vertices.ToPoint3dArray()]
    )
    expected = workspace.local @ matrix[:3, :3].T + matrix[:3, 3]
    np.testing.assert_allclose(actual, expected, atol=1e-11, rtol=0)
    with pytest.raises(ValueError, match="cannot be combined"):
        _ = export_rhino(
            workspace,
            snapshot,
            request.model_copy(update={"axis_up": True}),
        )


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


@pytest.mark.parametrize("side_kind", ["cone", "cylinder"])
def test_shared_axis_solve_exports_all_active_surfaces(side_kind: str) -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)
    payload = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    ).model_dump()
    base = {node["id"]: node for node in payload["nodes"]}
    base["side"]["kind"] = side_kind
    payload["nodes"] = [base[key] for key in ("scan", "outer_band", "top_face", "side")]
    payload["nodes"].extend(
        [
            {
                "id": "axis",
                "label": "Axis",
                "operation": "axis",
                "source_fit": "side",
            },
            {
                "id": "side_factor",
                "label": f"{side_kind.title()} factor",
                "operation": "fit",
                "selections": ["outer_band"],
                "kind": side_kind,
                "axial_domain": [-2, 5],
                "axis": "axis",
            },
            {
                "id": "plane_factor",
                "label": "Plane factor",
                "operation": "fit",
                "selections": ["top_face"],
                "kind": "plane",
                "axial_domain": [-2, 5],
                "axis": "axis",
            },
            {
                "id": "solve",
                "label": "Shared solve",
                "operation": "axis_solve",
                "axis": "axis",
                "factors": ["side_factor", "plane_factor"],
            },
        ]
    )
    payload["output"] = "solve"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    snapshot = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    request = RhinoExportRequest(
        token=snapshot["token"], target="solve", units="Millimeters"
    )
    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))
    fitted = [
        obj for obj in model.Objects if obj.Attributes.GetUserString("scansor_fit_id")
    ]
    assert {obj.Attributes.GetUserString("scansor_fit_id") for obj in fitted} == {
        "side_factor",
        "plane_factor",
    }
    side = next(
        obj.Geometry.Surfaces[0]
        for obj in fitted
        if obj.Attributes.GetUserString("scansor_fit_id") == "side_factor"
    )
    assert side.IsCone() if side_kind == "cone" else side.IsCylinder()

    plane_request = request.model_copy(update={"target": "plane_factor"})
    plane_model = rhino.File3dm.FromByteArray(
        export_rhino(workspace, snapshot, plane_request)
    )
    plane = next(
        obj.Geometry.Surfaces[0]
        for obj in plane_model.Objects
        if obj.Attributes.GetUserString("scansor_fit_id") == "plane_factor"
    )
    u, v = plane.Domain(0), plane.Domain(1)
    heights = [
        plane.PointAt(u.T0 + a * (u.T1 - u.T0), v.T0 + b * (v.T1 - v.T0)).Z
        for a, b in ((0, 0), (0.3, 0.7), (1, 1))
    ]
    np.testing.assert_allclose(heights, heights[0], atol=1e-11)


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


@pytest.mark.parametrize("kind", ["plane", "cylinder", "cone"])
def test_mirror_symmetry_exports_reflected_matching_bounds_and_can_disable(
    kind: str,
) -> None:
    from experiments.nozzle_rhino import joint_breps

    positions: list[Any] = []
    surfaces = {}
    for slot in range(2):
        # Deliberately give the second member wider rectangular/axial support.
        a, z = np.meshgrid(
            np.linspace(-0.35, 0.45 + 0.25 * slot, 7),
            np.linspace(0.1 - 0.3 * slot, 0.7 + 0.7 * slot, 6),
        )
        if kind == "plane":
            canonical = np.column_stack(
                [3 + a.ravel(), z.ravel(), np.full(a.size, 2.0)]
            )
            parameters = [0, 0, 1, 2]
        else:
            taper = 0.15 if kind == "cone" else 0.0
            angle = a.ravel()
            axial = z.ravel()
            canonical = np.column_stack(
                [
                    3 + (1 + taper * axial) * np.cos(angle),
                    (1 + taper * axial) * np.sin(angle),
                    axial,
                ]
            )
            parameters = [3 if slot == 0 else -1, 0, 0, 0, 1, 0, taper]
        observed = canonical.copy()
        if slot == 1:
            observed[:, 0] = 2 - observed[:, 0]
        ids = list(range(len(positions), len(positions) + len(observed)))
        positions.extend(observed)
        surfaces[str(slot)] = {
            "kind": kind,
            "parameters": parameters,
            "ids": ids,
            "axial_domain": (-2, 5),
        }
    result = {
        "surfaces": surfaces,
        "axis_display": [0, 0, 1],
        "point_display": [0, 0, 0],
        "mirror_planes": {"mirror_plane": {"plane_equation": [1, 0, 0, 1]}},
    }
    constraint = {
        "operation": "mirror_symmetry",
        "plane": "mirror_plane",
        "surfaces": ["0", "1"],
    }
    patches = joint_breps(result, [constraint], np.array(positions))
    model = rhino.File3dm()
    for patch in patches.values():
        _ = model.Objects.AddBrep(patch)
    import base64

    reread = rhino.File3dm.FromByteArray(base64.b64decode(model.Encode()))

    def canonical_bounds(index: int) -> tuple[np.ndarray, np.ndarray]:
        bounds = reread.Objects[index].Geometry.GetBoundingBox()
        corners = np.array(
            [
                [x, y, z]
                for x in (bounds.Min.X, bounds.Max.X)
                for y in (bounds.Min.Y, bounds.Max.Y)
                for z in (bounds.Min.Z, bounds.Max.Z)
            ]
        )
        if index == 1:
            corners[:, 0] = 2 - corners[:, 0]
        return corners.min(axis=0), corners.max(axis=0)

    first, second = canonical_bounds(0), canonical_bounds(1)
    np.testing.assert_allclose(first, second, atol=1e-11)

    independent = joint_breps(
        result, [{**constraint, "symmetric_extents": False}], np.array(positions)
    )
    if kind == "plane":
        left = independent["0"].GetBoundingBox()
        right = independent["1"].GetBoundingBox()
        assert right.Max.X - right.Min.X > left.Max.X - left.Min.X
    else:
        heights = [
            independent[str(slot)].GetBoundingBox().Max.Z
            - independent[str(slot)].GetBoundingBox().Min.Z
            for slot in range(2)
        ]
        assert heights[1] > heights[0] * 1.5


def test_axis_solve_export_passes_mirror_factors_to_symmetric_bounding(
    evaluated: tuple[NozzleWorkspace, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import deepcopy

    import experiments.nozzle_rhino as nozzle_rhino

    workspace, source = evaluated
    snapshot = deepcopy(source)
    snapshot["recipe"]["nodes"].extend(
        [
            {
                "id": "reference_plane",
                "label": "Mirror plane",
                "operation": "reference_plane",
                "axis": "side",
                "initial_angle_degrees": 0,
            },
            {
                "id": "mirror",
                "label": "Mirror relationship",
                "operation": "mirror_symmetry",
                "plane": "reference_plane",
                "surfaces": ["side", "end"],
                "symmetric_extents": True,
            },
            {
                "id": "solve",
                "label": "Mirror solve",
                "operation": "axis_solve",
                "axis": "side",
                "factors": ["side", "end", "mirror"],
            },
        ]
    )
    snapshot["recipe"]["output"] = "solve"
    snapshot["states"]["solve"] = "ready"
    snapshot["results"]["solve"] = deepcopy(snapshot["result"])
    captured: list[dict[str, Any]] = []

    def capture_relationships(
        result: dict[str, Any], relationships: list[dict[str, Any]], positions: Any
    ) -> dict[str, Any]:
        captured.extend(relationships)
        return {
            id: surface_brep(surface, positions)
            for id, surface in result["surfaces"].items()
        }

    monkeypatch.setattr(nozzle_rhino, "joint_breps", capture_relationships)
    request = RhinoExportRequest(
        token=snapshot["token"],
        target="solve",
        units="Millimeters",
        include_mesh=False,
    )
    model = rhino.File3dm.FromByteArray(export_rhino(workspace, snapshot, request))
    assert len(model.Objects) == 2
    assert [relationship["id"] for relationship in captured] == ["mirror"]


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
