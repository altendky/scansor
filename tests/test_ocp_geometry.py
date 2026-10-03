"""Kernel validity, exact areas, chart covariance, and display-only meshing."""

from __future__ import annotations

import math
from copy import deepcopy
from itertools import pairwise
from typing import Any

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane, GeomAbs_Sphere
from OCP.GProp import GProp_GProps
from OCP.Standard import Standard_ConstructionError
from OCP.TopoDS import TopoDS_Face, TopoDS_Shape

from experiments.ocp_geometry import (
    face_from_record,
    surface_patch,
    tessellate_face,
    transform_shape,
)
from experiments.surface_extents import circle_intersection, trimmed_face


def test_native_kernel_failure_preserves_validation_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import experiments.ocp_geometry as kernel

    def fail(*_args: Any) -> None:
        raise Standard_ConstructionError("native intersection failure")

    monkeypatch.setattr(kernel, "GeomAPI_IntSS", fail)
    with pytest.raises(ValueError, match="native intersection failure") as error:
        _ = circle_intersection(side(), cut(2))
    assert isinstance(error.value.__cause__, Standard_ConstructionError)


def area(shape: TopoDS_Shape) -> float:
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    return properties.Mass()


def side(radius: float = 3, slope: float = 0) -> dict[str, Any]:
    return {
        "kind": "cone" if slope else "cylinder",
        "parameters": [1, 2, 0, 0, radius, 0, slope],
    }


def cut(z: float, sign: float = 1) -> dict[str, Any]:
    return {"kind": "plane", "plane_equation": [0, 0, sign, sign * z]}


def bounded_side(
    radius: float, slope: float, lower: float, upper: float
) -> dict[str, Any]:
    surface = side(radius, slope)
    return trimmed_face(
        surface,
        [
            (
                {"intersection": "lower", "keep": "positive"},
                circle_intersection(surface, cut(lower)),
            ),
            (
                {"intersection": "upper", "keep": "negative"},
                circle_intersection(surface, cut(upper)),
            ),
        ],
        np.empty((0, 3)),
    )


@pytest.mark.parametrize(
    "radius,slope,lower,upper",
    [(3, 0, 1, 4), (3, 0.2, 1, 4), (3, -0.2, 1, 4), (-1, 0.2, 6, 9), (0, 0.2, 1, 4)],
)
def test_kernel_lateral_face_preserves_chart_and_exact_area(
    radius: float, slope: float, lower: float, upper: float
) -> None:
    record = bounded_side(radius, slope, lower, upper)
    face = face_from_record(record)
    assert BRepCheck_Analyzer(face).IsValid()
    assert BRepAdaptor_Surface(face).GetType() == (
        GeomAbs_Cone if slope else GeomAbs_Cylinder
    )
    radii = radius + slope * np.array([lower, upper])
    expected_area = math.pi * radii.sum() * (upper - lower) * math.sqrt(1 + slope**2)
    assert area(face) == pytest.approx(expected_area, rel=1e-10)
    points = np.asarray(record["preview"]["positions"]).reshape(-1, 3)
    np.testing.assert_allclose(
        np.linalg.norm(points[:, :2] - [1, 2], axis=1),
        radius + slope * points[:, 2],
        atol=1e-12,
    )
    assert points[:, 2].min() == pytest.approx(lower)
    assert points[:, 2].max() == pytest.approx(upper)


@pytest.mark.parametrize("slope", [-1e-10, 1e-10])
def test_shallow_cone_keeps_local_chart_and_small_axial_span(slope: float) -> None:
    surface = side(3, slope)
    surface["parameters"][:4] = [1e5, -2e5, 0.2, -0.1]
    axis = np.array([0.2, -0.1, 1.0])
    axis /= np.linalg.norm(axis)
    origin = np.array([1e5, -2e5, 0])
    lower, upper = 2.0, 2.0001
    boundaries: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for axial, keep in [(lower, "positive"), (upper, "negative")]:
        plane = {
            "kind": "plane",
            "plane_equation": [*axis, float(axis @ origin + axial)],
        }
        boundaries.append(
            ({"intersection": keep, "keep": keep}, circle_intersection(surface, plane))
        )
    record = trimmed_face(surface, boundaries, np.empty((0, 3)))
    face = face_from_record(record)
    assert BRepAdaptor_Surface(face).GetType() == GeomAbs_Cone
    expected_area = (
        math.pi
        * (6 + slope * (lower + upper))
        * (upper - lower)
        * math.sqrt(1 + slope**2)
    )
    assert area(face) == pytest.approx(expected_area, rel=1e-7)
    points = np.asarray(record["preview"]["positions"]).reshape(-1, 3)
    axial = (points - origin) @ axis
    radial = points - origin - axial[:, None] * axis
    np.testing.assert_allclose(
        np.linalg.norm(radial, axis=1), 3 + slope * axial, atol=1e-10
    )
    assert axial.min() == pytest.approx(lower, abs=1e-10)
    assert axial.max() == pytest.approx(upper, abs=1e-10)


@pytest.mark.parametrize("inner", [0, 2])
@pytest.mark.parametrize("sign", [-1, 1])
def test_kernel_planar_face_is_valid_exact_disk_or_annulus(
    inner: float, sign: float
) -> None:
    plane = cut(2, sign)
    boundaries = [
        (
            {"intersection": "outer", "keep": "inside"},
            circle_intersection(side(4), plane),
        )
    ]
    if inner:
        boundaries.append(
            (
                {"intersection": "inner", "keep": "outside"},
                circle_intersection(side(inner), plane),
            )
        )
    record = trimmed_face(plane, boundaries, np.empty((0, 3)))
    face = face_from_record(record)
    assert BRepCheck_Analyzer(face).IsValid()
    assert BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane
    assert area(face) == pytest.approx(math.pi * (16 - inner**2), rel=1e-10)
    vertices = np.asarray(record["preview"]["positions"]).reshape(-1, 3)
    triangles = np.asarray(record["preview"]["indices"]).reshape(-1, 3)
    normals = np.cross(
        vertices[triangles[:, 1]] - vertices[triangles[:, 0]],
        vertices[triangles[:, 2]] - vertices[triangles[:, 0]],
    )
    assert np.all(sign * normals[:, 2] > 0)


def test_open_physical_face_cannot_export_even_with_closed_preview() -> None:
    surface = side()
    record = trimmed_face(
        surface,
        [
            (
                {"intersection": "end", "keep": "positive"},
                circle_intersection(surface, cut(2)),
            )
        ],
        np.array([[4, 2, 3], [4, 2, 7]]),
    )
    assert record["preview"]["indices"]
    assert record["bounds"]["axial"] == [2, None]
    with pytest.raises(ValueError, match="finite explicit bounds"):
        _ = face_from_record(record)


@pytest.mark.parametrize("scale", [1e-6, 1, 1e6])
def test_derived_mesh_normalization_does_not_change_physical_face(scale: float) -> None:
    record = bounded_side(3 * scale, 0, 2 * scale, 5 * scale)
    face = face_from_record(record)
    before = area(face)
    mesh = tessellate_face(face, 3 * scale)
    assert area(face) == before
    vertices = np.asarray(mesh["positions"]).reshape(-1, 3)
    np.testing.assert_allclose(
        np.linalg.norm(vertices[:, :2] - [1, 2], axis=1),
        3 * scale,
        rtol=1e-9,
        atol=1e-15,
    )
    assert 100 < len(mesh["indices"]) < 100000


@pytest.mark.parametrize("failed_attempts", [0, 1, 2])
@pytest.mark.parametrize("done_without_triangles", [False, True])
def test_display_meshing_retries_only_failed_or_empty_attempts(
    monkeypatch: pytest.MonkeyPatch,
    failed_attempts: int,
    done_without_triangles: bool,
) -> None:
    import experiments.ocp_geometry as kernel

    face = face_from_record(bounded_side(3, 0, 2, 5))
    before = area(face)
    original = vars(kernel)["BRepMesh_IncrementalMesh"]
    attempts: list[float] = []
    displays: list[TopoDS_Shape] = []

    class FailedMesh:
        def IsDone(self) -> bool:
            return done_without_triangles

        def GetStatusFlags(self) -> int:
            return 0

    def mesh(display: TopoDS_Shape, deflection: float, *args: Any) -> Any:
        attempts.append(deflection)
        displays.append(display)
        if len(attempts) <= failed_attempts:
            return FailedMesh()
        return original(display, deflection, *args)

    monkeypatch.setattr(kernel, "BRepMesh_IncrementalMesh", mesh)
    preview = tessellate_face(face, 3)
    assert attempts == [0.001, 0.0001, 0.00001][: failed_attempts + 1]
    assert preview["indices"] and np.isfinite(preview["positions"]).all()
    assert all(not first.IsSame(second) for first, second in pairwise(displays))
    assert area(face) == before


@pytest.mark.parametrize("done_without_triangles", [False, True])
def test_display_meshing_exhaustion_never_returns_an_empty_success(
    monkeypatch: pytest.MonkeyPatch, done_without_triangles: bool
) -> None:
    import experiments.ocp_geometry as kernel

    face = face_from_record(bounded_side(3, 0, 2, 5))
    before = area(face)
    attempts: list[float] = []

    class FailedMesh:
        def IsDone(self) -> bool:
            return done_without_triangles

        def GetStatusFlags(self) -> int:
            return 0

    def mesh(_display: TopoDS_Shape, deflection: float, *_args: Any) -> FailedMesh:
        attempts.append(deflection)
        return FailedMesh()

    monkeypatch.setattr(kernel, "BRepMesh_IncrementalMesh", mesh)
    with pytest.raises(ValueError, match=r"preview (has no triangles|meshing failed)"):
        _ = tessellate_face(face, 3)
    assert attempts == [0.001, 0.0001, 0.00001]
    assert area(face) == before


@pytest.mark.parametrize("reported_status", ["first_failure", "all_failure", "reused"])
def test_display_meshing_checks_failure_flags_even_with_positive_triangles(
    monkeypatch: pytest.MonkeyPatch, reported_status: str
) -> None:
    from OCP.BRep import BRep_Tool
    from OCP.IMeshData import IMeshData_Failure, IMeshData_Reused
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    import experiments.ocp_geometry as kernel

    face = face_from_record(bounded_side(3, 0, 2, 5))
    before = area(face)
    original = vars(kernel)["BRepMesh_IncrementalMesh"]
    attempts: list[float] = []

    class ReportedMesh:
        def IsDone(self) -> bool:
            return True

        def GetStatusFlags(self) -> int:
            return int(
                IMeshData_Reused if reported_status == "reused" else IMeshData_Failure
            )

    def mesh(display: TopoDS_Shape, deflection: float, *args: Any) -> Any:
        attempts.append(deflection)
        actual = original(display, deflection, *args)
        assert actual.IsDone()
        triangulation = BRep_Tool.Triangulation_s(
            TopoDS.Face(display), TopLoc_Location()
        )
        assert triangulation and triangulation.NbTriangles() > 0
        if reported_status != "first_failure" or len(attempts) == 1:
            return ReportedMesh()
        return actual

    monkeypatch.setattr(kernel, "BRepMesh_IncrementalMesh", mesh)
    if reported_status == "all_failure":
        with pytest.raises(ValueError, match="preview meshing failed"):
            _ = tessellate_face(face, 3)
        assert attempts == [0.001, 0.0001, 0.00001]
    else:
        preview = tessellate_face(face, 3)
        assert preview["indices"]
        assert attempts == ([0.001] if reported_status == "reused" else [0.001, 0.0001])
    assert area(face) == before


@pytest.mark.parametrize("reflection", [False, True])
def test_kernel_transform_preserves_exact_geometry_and_uniform_scale(
    reflection: bool,
) -> None:
    face = face_from_record(bounded_side(3, 0.2, 1, 4))
    matrix = np.eye(4)
    matrix[:3, :3] *= 2
    if reflection:
        matrix[0, 0] *= -1
    matrix[:3, 3] = [4, -7, 3]
    result = transform_shape(face, matrix)
    assert BRepCheck_Analyzer(result).IsValid()
    assert area(result) == pytest.approx(4 * area(face), rel=1e-10)


@pytest.mark.parametrize(
    "matrix", [np.diag([1, 2, 1, 1]), np.diag([0, 0, 0, 1]), np.diag([1, 1, 1, 2])]
)
def test_transform_rejects_nonuniform_or_nonaffine_geometry(
    matrix: np.ndarray[Any, Any],
) -> None:
    with pytest.raises(ValueError, match="transform"):
        _ = transform_shape(face_from_record(bounded_side(3, 0, 1, 4)), matrix)


def test_observation_patch_padding_and_axial_support_remain_separate() -> None:
    positions = np.array([[4, 2, 1], [4, 2, 4]])
    surface = {**side(), "ids": [0, 1], "axial_domain": [1, 4.1]}
    face = surface_patch(surface, positions)
    assert isinstance(face, TopoDS_Face)
    assert area(face) == pytest.approx(2 * math.pi * 3 * 3.1)
    np.testing.assert_array_equal(positions, [[4, 2, 1], [4, 2, 4]])


def test_observation_planar_patch_and_full_sphere_remain_exact() -> None:
    positions = np.array([[0, 0, 2], [3, 0, 2], [0, 4, 2]])
    plane = surface_patch({**cut(2), "ids": [0, 1, 2]}, positions)
    assert area(plane) == pytest.approx(3 * 4 * 1.1**2)
    sphere = surface_patch(
        {"kind": "sphere", "parameters": [1, 2, 3, 4], "ids": [0]}, positions
    )
    assert BRepAdaptor_Surface(sphere).GetType() == GeomAbs_Sphere
    assert area(sphere) == pytest.approx(4 * math.pi * 4**2)


def test_invalid_record_is_rejected_without_healing() -> None:
    record = bounded_side(3, 0, 1, 4)
    before = deepcopy(record)
    record["geometry"]["basis_u"] = [1, 0, 1]
    with pytest.raises(ValueError, match="orthonormal"):
        _ = face_from_record(record)
    record["geometry"]["basis_u"] = before["geometry"]["basis_u"]
    assert record == before
