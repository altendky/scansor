"""Independent source-space geometry and legacy-chart migration regressions."""

import hashlib
import io
import json
import struct
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    Selection,
    Source,
    SurfaceFit,
)
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.ocp_geometry import surface_patch
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_growth import surface_distance
from experiments.selection_region import apply_selection_region, build_selection_region
from experiments.surface_primitives import (
    primitive,
    rebase_revolution,
    reverse_revolution,
    revolution_geometry,
    transform_revolution,
)


def horizontal_cylinder() -> dict[str, Any]:
    return {
        "kind": "cylinder",
        "origin": [3.0, -4.0, 5.0],
        "axis": [1.0, 0.0, 0.0],
        "radius": 2.5,
        "slope": 0.0,
        "axial_domain": [-2.0, 4.0],
    }


def legacy_cone() -> dict[str, Any]:
    return {
        "kind": "cone",
        "parameters": [2.0, -3.0, 0.2, -0.3, 4.0, 0.0, 0.5],
        "axial_domain": [-2.0, 5.0],
    }


@pytest.mark.parametrize("reversed_axis", [False, True])
def test_frozen_symmetry_endpoint_survives_repeated_canonical_reads(
    reversed_axis: bool,
) -> None:
    # Actual published cylinder from the frozen imported symmetry probe. Its
    # rounded axis has norm 1+eps; renormalization excluded vertex192 by one ULP.
    directory = (
        Path(__file__).resolve().parents[1] / "experiments/browser_runtime/symmetry"
    )
    raw = (directory / "cylinder-rotation.ply").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "1967b5fbef70851799e10b36b93aab634489605dc65575857da33d10576eec69"
    )
    record = json.loads((directory / "cylinder-rotation.json").read_text())
    assert record["ids"]["main"] == list(range(0, 240, 3))
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    vertices = np.frombuffer(
        raw, dtype="<f4", count=6 * record["vertex_count"], offset=start
    ).reshape(-1, 6)
    points = vertices[::3][:80, :3].astype(np.float64)
    geometry: dict[str, Any] = {
        "kind": "cylinder",
        "origin": [11.4842720016505, -5.475575813806024, 10.441582089462466],
        "axis": [0.4877001918059968, 0.23200471461500444, 0.8416188777046322],
        "radius": 2.0000000174407604,
        "slope": 0.0,
        "axial_domain": [-8.006840652110514, -1.0068399598725697],
    }
    if reversed_axis:
        geometry["axis"] = [-v for v in geometry["axis"]]
        geometry["axial_domain"] = [-v for v in geometry["axial_domain"][::-1]]
    before = deepcopy(geometry)
    points_before = points.copy()
    heights = (points - geometry["origin"]) @ np.asarray(geometry["axis"])
    assert heights[64] == geometry["axial_domain"][0 if reversed_axis else 1]
    assert np.all(heights >= geometry["axial_domain"][0])
    assert np.all(heights <= geometry["axial_domain"][1])
    for repetitions in (1, 2, 8):
        current = deepcopy(geometry)
        for _ in range(repetitions):
            current = revolution_geometry(surface(current))
            assert current == before
            inside = surface_distance(points, surface(current))[2]
            assert (int(inside.sum()), int((~inside).sum())) == (80, 0)
    assert geometry == before
    np.testing.assert_array_equal(points, points_before)


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_canonical_roundoff_preservation_does_not_expand_hard_caps(kind: str) -> None:
    geometry: dict[str, Any] = {
        "kind": kind,
        "origin": [0.0, 0.0, 0.0],
        "axis": [1.0, 0.0, 0.0],
        "radius": 2.0,
        "slope": 0.0,
        "axial_domain": [1.0, 2.0],
    }
    points = np.array(
        [
            [1.0, 2.0, 0.0],
            [2.0, 2.0, 0.0],
            [np.nextafter(1.0, -np.inf), 2.0, 0.0],
            [np.nextafter(2.0, np.inf), 2.0, 0.0],
        ]
    )
    for _ in range(8):
        geometry = revolution_geometry(surface(geometry))
        np.testing.assert_array_equal(
            surface_distance(points, surface(geometry))[2],
            [True, True, False, False],
        )
        assert geometry["axial_domain"] == [1.0, 2.0]


def test_canonical_four_epsilon_preservation_keeps_unit_admission_limit() -> None:
    geometry = horizontal_cylinder()
    for ulps in (-4, 4):
        geometry["axis"] = [1.0 + ulps * np.finfo(float).eps, 0.0, 0.0]
        assert revolution_geometry(surface(geometry))["axis"] == geometry["axis"]
    geometry["axis"] = [1.0 + 5 * np.finfo(float).eps, 0.0, 0.0]
    assert revolution_geometry(surface(geometry))["axis"] == [1.0, 0.0, 0.0]
    geometry["axis"] = [1.0 + 5e-11, 0.0, 0.0]
    assert revolution_geometry(surface(geometry))["axis"] == [1.0, 0.0, 0.0]
    geometry["axis"] = [1.0 + 2e-10, 0.0, 0.0]
    with pytest.raises(ValueError, match="unit vector"):
        _ = revolution_geometry(surface(geometry))


def test_legacy_axis_keeps_its_existing_second_normalization() -> None:
    # Existing positive-Z legacy adaptation normalized once for the slope
    # chart and again at the spatial boundary. Preserve these exact bytes.
    legacy = {
        "kind": "cylinder",
        "parameters": [0.0, 0.0, 0.3, 0.4, 2.0, 0.0, 0.0],
    }
    before = deepcopy(legacy)
    assert revolution_geometry(legacy)["axis"] == [
        0.2683281572999748,
        0.3577708763999664,
        0.894427190999916,
    ]
    assert legacy == before


def oblique_cone() -> dict[str, Any]:
    axis = np.array([0.2, -0.3, 1.0])
    axis /= np.linalg.norm(axis)
    return {
        "kind": "cone",
        "origin": [2.0, -3.0, 0.0],
        "axis": axis.tolist(),
        "radius": 4.0,
        "slope": 0.5,
        "axial_domain": [-2.0, 5.0],
    }


def negative_anchor_cone() -> dict[str, Any]:
    return {
        "kind": "cone",
        "origin": [1.0, 4.0, -2.0],
        "axis": [0.0, 1.0, 0.0],
        "radius": -1.0,
        "slope": 0.5,
        "axial_domain": [8.0, 15.0],
    }


def surface(geometry: dict[str, Any]) -> dict[str, Any]:
    return {"kind": geometry["kind"], "analytic": deepcopy(geometry)}


def sample_geometry(
    geometry: dict[str, Any], heights: np.ndarray, *, radial_error: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    axis = np.array(geometry["axis"])
    # All fixtures have a nonzero XY axis component. This independently chosen
    # basis need not agree with the implementation's display/CAD clocking.
    first = np.array([axis[1], -axis[0], 0.0])
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    angle, height = np.meshgrid(np.linspace(0, 2 * np.pi, 16, endpoint=False), heights)
    radial = (
        np.cos(angle.ravel())[:, None] * first + np.sin(angle.ravel())[:, None] * second
    )
    radius = geometry["radius"] + geometry["slope"] * height.ravel()
    points = (
        np.array(geometry["origin"])
        + height.ravel()[:, None] * axis
        + (radius + radial_error)[:, None] * radial
    )
    normals = (radial - geometry["slope"] * axis) / np.hypot(1.0, geometry["slope"])
    return points, normals


def assert_same_physical_surface(
    first: dict[str, Any], second: dict[str, Any], points: np.ndarray
) -> None:
    first_distance, first_normals, first_support = surface_distance(
        points, surface(first)
    )
    second_distance, second_normals, second_support = surface_distance(
        points, surface(second)
    )
    np.testing.assert_allclose(second_distance, first_distance, atol=1e-12)
    np.testing.assert_allclose(second_normals, first_normals, atol=1e-12)
    np.testing.assert_array_equal(second_support, first_support)


def test_horizontal_cylinder_distance_normal_and_support_are_source_space_geometry() -> (
    None
):
    geometry = horizontal_cylinder()
    # Explicit source-space XYZ construction; no positive-Z chart is involved.
    points = np.array(
        [[2.0, -1.2, 5.0], [6.0, -4.0, 2.2], [0.0, -1.5, 5.0], [8.0, -1.5, 5.0]]
    )
    distances, normals, support = surface_distance(points, surface(geometry))
    np.testing.assert_allclose(distances, [0.3, 0.3, 0, 0], atol=1e-14)
    np.testing.assert_allclose(
        normals, [[0, 1, 0], [0, 0, -1], [0, 1, 0], [0, 1, 0]], atol=1e-14
    )
    np.testing.assert_array_equal(support, [True, True, False, False])
    assert revolution_geometry(surface(geometry)) == geometry
    assert primitive(surface(geometry)) == {
        key: value for key, value in geometry.items() if key != "axial_domain"
    }


def test_oblique_canonical_cone_matches_independent_orthogonal_distances_and_legacy() -> (
    None
):
    geometry = oblique_cone()
    points, expected_normals = sample_geometry(
        geometry, np.array([-1.0, 1.0, 4.0]), radial_error=0.2
    )
    expected_distance = 0.2 / np.hypot(1, 0.5)
    canonical_result = surface_distance(points, surface(geometry))
    np.testing.assert_allclose(canonical_result[0], expected_distance, atol=1e-13)
    np.testing.assert_allclose(canonical_result[1], expected_normals, atol=1e-13)
    assert canonical_result[2].all()
    legacy_result = surface_distance(points, legacy_cone())
    for actual, expected in zip(canonical_result, legacy_result, strict=True):
        np.testing.assert_allclose(actual, expected, atol=1e-13)
    assert revolution_geometry(legacy_cone()) == geometry


def test_legacy_adapter_preserves_missing_support_without_inventing_endpoints() -> None:
    legacy = legacy_cone()
    _ = legacy.pop("axial_domain")
    adapted = revolution_geometry(legacy)
    assert adapted["axial_domain"] is None
    expected = oblique_cone()
    _ = expected.pop("axial_domain")
    assert primitive(legacy) == expected


def test_legacy_unknown_support_remains_unknown_through_geometry_operations() -> None:
    legacy = legacy_cone()
    _ = legacy.pop("axial_domain")
    geometry = revolution_geometry(legacy)
    before = deepcopy(geometry)
    for transformed in (
        rebase_revolution(geometry, 2.0),
        reverse_revolution(geometry),
        transform_revolution(geometry, np.eye(3), np.array([10.0, -20.0, 30.0])),
    ):
        assert transformed["axial_domain"] is None
    assert geometry == before


def test_canonical_reader_owns_outputs_without_mutating_input_arrays() -> None:
    geometry = horizontal_cylinder()
    geometry["axis"] = np.array([1.0 + 1e-13, 0, 0])
    geometry["origin"] = np.array(geometry["origin"])
    geometry["axial_domain"] = np.array(geometry["axial_domain"])
    original = deepcopy(geometry)
    fitted = {"kind": "cylinder", "analytic": geometry}
    adapted = revolution_geometry(fitted)
    for name in ("axis", "origin", "axial_domain"):
        np.testing.assert_array_equal(geometry[name], original[name])
        adapted[name][0] = -999
        np.testing.assert_array_equal(geometry[name], original[name])


def test_canonical_record_has_priority_over_conflicting_legacy_parameters_and_support() -> (
    None
):
    geometry = horizontal_cylinder()
    conflicting = surface(geometry) | {
        "parameters": [-40.0, 30.0, 100.0, -100.0, 17.0, 5.0, 0.0],
        "axial_domain": [100.0, 101.0],
    }
    before = deepcopy(conflicting)
    points, expected_normals = sample_geometry(geometry, np.array([-1.0, 2.0]))
    distances, normals, support = surface_distance(points, conflicting)
    np.testing.assert_allclose(distances, 0, atol=1e-13)
    np.testing.assert_allclose(normals, expected_normals, atol=1e-13)
    assert support.all()
    assert revolution_geometry(conflicting) == geometry
    assert conflicting == before


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "missing-axis",
        "nonunit-axis",
        "zero-axis",
        "nonfinite",
        "negative-cylinder",
        "cylinder-taper",
        "missing-support",
        "none-support",
        "reversed-support",
        "empty-support",
        "infinite-support",
        "wrong-kind",
    ],
)
def test_invalid_explicit_canonical_geometry_never_falls_back_to_valid_legacy(
    defect: str,
) -> None:
    legacy: dict[str, Any] = {
        "kind": "cylinder",
        "parameters": [0.0, 0, 0, 0, 2.5, 0, 0],
        "axial_domain": [-2.0, 4.0],
    }
    geometry = horizontal_cylinder()
    if defect == "none":
        legacy["analytic"] = None
    else:
        if defect == "missing-axis":
            _ = geometry.pop("axis")
        elif defect == "nonunit-axis":
            geometry["axis"] = [2.0, 0, 0]
        elif defect == "zero-axis":
            geometry["axis"] = [0.0, 0, 0]
        elif defect == "nonfinite":
            geometry["origin"][0] = float("nan")
        elif defect == "negative-cylinder":
            geometry["radius"] = -1
        elif defect == "cylinder-taper":
            geometry["slope"] = 0.1
        elif defect == "missing-support":
            _ = geometry.pop("axial_domain")
        elif defect == "none-support":
            geometry["axial_domain"] = None
        elif defect == "reversed-support":
            geometry["axial_domain"] = [4.0, -2.0]
        elif defect == "empty-support":
            geometry["axial_domain"] = [2.0, 2.0]
        elif defect == "infinite-support":
            geometry["axial_domain"] = [-2.0, float("inf")]
        else:
            geometry["kind"] = "plane"
        legacy["analytic"] = geometry
    with pytest.raises(ValueError):
        _ = revolution_geometry(legacy)
    with pytest.raises(ValueError):
        _ = primitive(legacy)


def test_roundoff_in_a_unit_axis_is_accepted_without_allowing_scale_in_the_axis() -> (
    None
):
    geometry = horizontal_cylinder()
    geometry["axis"][0] += 1e-13
    normalized = revolution_geometry(surface(geometry))
    assert np.linalg.norm(normalized["axis"]) == pytest.approx(1.0, abs=1e-12)
    assert normalized["radius"] == 2.5


@pytest.mark.parametrize(
    "parameters",
    [["0", 0, 0, 0, 2, 0, 0], [0, 0, False, 0, 2, 0, 0]],
)
def test_legacy_parameters_reject_numeric_strings_and_booleans(
    parameters: list[object],
) -> None:
    fitted = {
        "kind": "cylinder",
        "parameters": parameters,
        "axial_domain": [-2.0, 4.0],
    }
    with pytest.raises(ValueError):
        _ = revolution_geometry(fitted)
    with pytest.raises(ValueError):
        _ = primitive(fitted)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("axis", ["1", 0, 0]),
        ("origin", [0, False, 0]),
        ("radius", "2.5"),
        ("radius", True),
        ("slope", False),
        ("axial_domain", [-2, True]),
    ],
)
def test_canonical_geometry_rejects_string_and_boolean_coordinates_or_scalars(
    field: str, value: object
) -> None:
    geometry = horizontal_cylinder()
    geometry[field] = value
    with pytest.raises(ValueError):
        _ = revolution_geometry(surface(geometry))


def test_cone_may_have_a_negative_anchor_radius_when_its_finite_patch_is_positive() -> (
    None
):
    geometry = negative_anchor_cone()
    points, expected_normals = sample_geometry(geometry, np.array([9.0, 11.0, 14.0]))
    distances, normals, support = surface_distance(points, surface(geometry))
    np.testing.assert_allclose(distances, 0, atol=1e-13)
    np.testing.assert_allclose(normals, expected_normals, atol=1e-13)
    assert support.all()
    assert revolution_geometry(surface(geometry))["radius"] == -1.0
    invalid = deepcopy(geometry)
    invalid["axial_domain"] = [1.0, 15.0]
    with pytest.raises(ValueError):
        _ = revolution_geometry(surface(invalid))


@pytest.mark.parametrize("geometry", [horizontal_cylinder(), oblique_cone()])
def test_rebasing_and_reversing_preserve_physical_patch_and_support(
    geometry: dict[str, Any],
) -> None:
    before = deepcopy(geometry)
    lo, hi = geometry["axial_domain"]
    points, _ = sample_geometry(
        geometry, np.array([lo - 0.1, lo + 0.5, hi - 0.5, hi + 0.1])
    )
    rebased = rebase_revolution(geometry, -10.0)
    expected_origin = np.array(geometry["origin"]) - 10 * np.array(geometry["axis"])
    np.testing.assert_allclose(rebased["origin"], expected_origin, atol=1e-13)
    assert rebased["radius"] == pytest.approx(
        geometry["radius"] - 10 * geometry["slope"]
    )
    np.testing.assert_allclose(rebased["axial_domain"], [lo + 10, hi + 10])
    assert_same_physical_surface(geometry, rebased, points)
    reversed_geometry = reverse_revolution(geometry)
    np.testing.assert_array_equal(
        reversed_geometry["axis"], -np.array(geometry["axis"])
    )
    assert reversed_geometry["slope"] == -geometry["slope"]
    assert reversed_geometry["axial_domain"] == [-hi, -lo]
    assert_same_physical_surface(geometry, reversed_geometry, points)
    assert geometry == before
    # Reversal is involutive and does not change the set of physical endpoints.
    assert reverse_revolution(reversed_geometry) == geometry
    old_endpoints = (
        np.array(geometry["origin"]) + np.array([lo, hi])[:, None] * geometry["axis"]
    )
    new_endpoints = (
        np.array(reversed_geometry["origin"])
        + np.array(reversed_geometry["axial_domain"])[:, None]
        * reversed_geometry["axis"]
    )
    np.testing.assert_allclose(new_endpoints, old_endpoints[::-1], atol=1e-13)


@pytest.mark.parametrize(
    "geometry", [horizontal_cylinder(), oblique_cone(), negative_anchor_cone()]
)
def test_row_rigid_transform_preserves_distances_normals_and_anchor_relative_support(
    geometry: dict[str, Any],
) -> None:
    rotation = np.array([[0.0, 1, 0], [-1.0, 0, 0], [0.0, 0, 1]])
    translation = np.array([11.0, -7.0, 4.0])
    lo, hi = geometry["axial_domain"]
    points, _ = sample_geometry(
        geometry, np.linspace(lo + 0.5, hi - 0.5, 3), radial_error=0.1
    )
    before = deepcopy(geometry)
    moved = transform_revolution(geometry, rotation, translation)
    np.testing.assert_allclose(
        moved["origin"], np.array(geometry["origin"]) @ rotation + translation
    )
    np.testing.assert_allclose(moved["axis"], np.array(geometry["axis"]) @ rotation)
    assert moved["radius"] == geometry["radius"]
    assert moved["slope"] == geometry["slope"]
    assert moved["axial_domain"] == geometry["axial_domain"]
    original_distance, original_normals, original_support = surface_distance(
        points, surface(geometry)
    )
    distance, normals, support = surface_distance(
        points @ rotation + translation, surface(moved)
    )
    np.testing.assert_allclose(distance, original_distance, atol=1e-12)
    np.testing.assert_allclose(normals, original_normals @ rotation, atol=1e-12)
    np.testing.assert_array_equal(support, original_support)
    assert geometry == before
    moved["origin"][0] = -999
    moved["axis"][0] = -999
    moved["axial_domain"][0] = -999
    assert geometry == before


@pytest.mark.parametrize(
    "rotation",
    [
        np.diag([-1.0, 1, 1]),
        2 * np.eye(3),
        np.array([[1.0, 0.1, 0], [0.0, 1, 0], [0.0, 0, 1]]),
        np.full((3, 3), np.nan),
        np.eye(2),
    ],
)
def test_transform_rejects_reflection_scaling_shear_and_invalid_rotations(
    rotation: np.ndarray,
) -> None:
    with pytest.raises(ValueError):
        _ = transform_revolution(horizontal_cylinder(), rotation, np.zeros(3))


@pytest.mark.parametrize("translation", [np.zeros(2), np.array([0.0, float("inf"), 0])])
def test_transform_rejects_invalid_translations(translation: np.ndarray) -> None:
    with pytest.raises(ValueError):
        _ = transform_revolution(horizontal_cylinder(), np.eye(3), translation)


def test_cylinder_selection_region_uses_canonical_radius_without_legacy_parameters() -> (
    None
):
    geometry = horizontal_cylinder()
    points, normals = sample_geometry(geometry, np.array([-1.0, 0.0, 1.0]))
    # Columns are explicit source-space local X/Y/Z = Y/Z/X.
    rotation = np.array([[0.0, 0, 1], [1.0, 0, 0], [0.0, 1, 0]])
    origin = np.array(geometry["origin"])
    before = deepcopy(geometry)
    region = build_selection_region(
        points,
        normals,
        surface(geometry),
        origin,
        rotation,
        tangent_margin=0.1,
        normal_margin=0.05,
        normal_angle_degrees=10,
    )
    assert region["radius"] == 2.5
    rejected_point = origin + np.array([0.0, 3.0, 0.0])
    all_points = np.vstack((points, rejected_point))
    all_normals = np.vstack((normals, [0.0, 1, 0]))
    selected = apply_selection_region(
        all_points, all_normals, np.ones(len(all_points)), region, origin, rotation
    )
    assert selected == list(range(len(points)))
    assert geometry == before


def imported_patch(geometry: dict[str, Any]) -> ScanWorkspace:
    lower, upper = geometry["axial_domain"]
    points, normals = sample_geometry(
        geometry, np.linspace(float(lower), float(upper), 3)
    )
    triangles: list[tuple[int, int, int]] = []
    for row in range(2):
        for column in range(16):
            first, second = row * 16 + column, row * 16 + (column + 1) % 16
            triangles.extend(
                [(first, second, first + 16), (second, second + 16, first + 16)]
            )
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Independently generated analytic patch observations\n"
        f"element vertex {len(points)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(triangles)}\n"
        "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    vertices = b"".join(
        struct.pack("<6f", *point, *normal)
        for point, normal in zip(points, normals, strict=True)
    )
    faces = b"".join(struct.pack("<B3i", 3, *triangle) for triangle in triangles)
    return prepare_scan(
        io.BytesIO(header + vertices + faces),
        "independent-revolution.ply",
        ScanLimits(100_000, 100, 100, 10_000_000),
    )


def patch_recipe(workspace: ScanWorkspace, geometry: dict[str, Any]) -> Recipe:
    assert workspace.descriptor is not None and workspace.source_sha256 is not None
    return Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Independent source",
                operation="source",
                source_sha256=workspace.source_sha256,
                reference_sha256=workspace.reference_sha256,
                descriptor=workspace.descriptor,
            ),
            Selection(
                id="patch",
                label="Observed patch",
                operation="selection",
                source="scan",
                ids=list(range(len(workspace.local))),
            ),
            SurfaceFit(
                id="surface",
                label="Canonical patch",
                operation="fit",
                selections=["patch"],
                kind=geometry["kind"],
            ),
        ],
        output="surface",
    )


def assert_kernel_patch(shape: TopoDS_Shape, geometry: dict[str, Any]) -> None:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == (
        GeomAbs_Cylinder if geometry["kind"] == "cylinder" else GeomAbs_Cone
    )
    axis = (
        adaptor.Cylinder().Axis()
        if geometry["kind"] == "cylinder"
        else adaptor.Cone().Axis()
    )
    direction = np.array(axis.Direction().Coord())
    expected_axis = np.array(geometry["axis"])
    assert abs(direction @ expected_axis) == pytest.approx(1.0, abs=1e-12)
    np.testing.assert_allclose(
        np.cross(np.array(axis.Location().Coord()) - geometry["origin"], expected_axis),
        0,
        atol=1e-7,
    )
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    points = np.array(
        [
            adaptor.Value(u0 + (u1 - u0) * u, v).Coord()
            for v in [v0, v1]
            for u in [0.1, 0.4, 0.8]
        ]
    )
    offset = points - geometry["origin"]
    height = offset @ expected_axis
    radius = np.linalg.norm(offset - height[:, None] * expected_axis, axis=1)
    np.testing.assert_allclose(
        radius, geometry["radius"] + geometry["slope"] * height, atol=1e-7
    )
    np.testing.assert_allclose(
        [height.min(), height.max()], geometry["axial_domain"], atol=1e-7
    )
    explorer.Next()
    assert not explorer.More()


@pytest.mark.parametrize("geometry", [horizontal_cylinder(), negative_anchor_cone()])
@pytest.mark.parametrize("stale_legacy_parameters", [False, True])
def test_canonical_only_result_exports_world_patch_and_step_without_numeric_chart(
    geometry: dict[str, Any], stale_legacy_parameters: bool, tmp_path: Path
) -> None:
    workspace = imported_patch(geometry)
    recipe = patch_recipe(workspace, geometry)
    fitted = surface(geometry) | {
        "ids": list(range(len(workspace.local))),
        "axial_domain": [100.0, 101.0],
    }
    if stale_legacy_parameters:
        fitted["parameters"] = ["stale"]
    else:
        assert "parameters" not in fitted
    assert primitive(fitted) == {
        key: value for key, value in geometry.items() if key != "axial_domain"
    }
    assert_kernel_patch(surface_patch(fitted, workspace.local), geometry)
    # A synthetic evaluated publication isolates the geometry boundary from
    # numerical fitting and deliberately includes contradictory legacy fields.
    snapshot: dict[str, Any] = {
        "token": "independent-canonical-publication",
        "recipe": recipe.model_dump(),
        "states": {"scan": "ready", "patch": "ready", "surface": "ready"},
        "results": {"surface": fitted},
    }
    archive_bytes = export_cad(
        workspace,
        snapshot,
        CadExportRequest(
            token=snapshot["token"],
            target="surface",
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(archive_bytes)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["source_sha256"] == workspace.source_sha256
        np.testing.assert_array_equal(metadata["transform_local_to_export"], np.eye(4))
        path = tmp_path / "canonical-patch.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_kernel_patch(reader.OneShape(), geometry)
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    evaluated = cast(dict[str, Any], state["results"])["surface"]
    actual = revolution_geometry(evaluated)
    assert evaluated["weighted_rms"] < 1e-6
    direction_dot = float(np.asarray(actual["axis"]) @ geometry["axis"])
    shift = float(
        (np.asarray(actual["origin"]) - geometry["origin"]) @ geometry["axis"]
    )
    assert actual["radius"] == pytest.approx(
        geometry["radius"] + geometry["slope"] * shift, abs=1e-6
    )
    assert actual["slope"] == pytest.approx(
        geometry["slope"] * np.sign(direction_dot), abs=1e-6
    )
    assert abs(direction_dot) > 1 - 1e-10
