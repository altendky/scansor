"""Independent seed-free sphere, shared-center, source replay and CAD fixtures."""

import hashlib
import io
import json
import struct
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Sphere
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from experiments.feature_graph import (
    EqualRadii,
    Feature,
    FeatureGraph,
    Growth,
    PointDefinition,
    Recipe,
    Selection,
    Source,
    SurfaceFit,
    fit_sphere_at_point,
)
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import InitializationBinding, initialization_binding
from experiments.fit_solver import FitCancelled
from experiments.mesh_sphere_fit import (
    SphereFitFailure,
    fit_shared_sphere_center,
    fit_sphere,
)
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.ocp_geometry import surface_patch
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_growth import fit_seed, surface_distance

CENTER = np.array([2.5, -1.75, 3.25])


def rotation() -> np.ndarray:
    a, b = 0.63, -0.44
    around_z = np.array(
        [[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]]
    )
    around_x = np.array(
        [[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]]
    )
    return around_z @ around_x


def sphere_grid(
    center: np.ndarray = CENTER,
    radius: float = 4.5,
    *,
    noise: float = 0.0,
    partial: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    azimuth, elevation = np.meshgrid(
        np.linspace(-0.7, 1.1, 24)
        if partial
        else np.linspace(0, 2 * np.pi, 24, endpoint=False),
        np.linspace(-0.3, 0.8, 9) if partial else np.linspace(-0.8, 0.9, 9),
    )
    directions = np.column_stack(
        (
            np.cos(elevation.ravel()) * np.cos(azimuth.ravel()),
            np.cos(elevation.ravel()) * np.sin(azimuth.ravel()),
            np.sin(elevation.ravel()),
        )
    )
    points = (
        center + (radius + noise * np.sin(np.arange(216) * 1.7))[:, None] * directions
    )
    triangles: list[tuple[int, int, int]] = []
    for row in range(8):
        for column in range(23 if partial else 24):
            first = 24 * row + column
            second = 24 * row + (column + 1) % 24
            triangles.extend(
                [(first, second, first + 24), (second, second + 24, first + 24)]
            )
    return points, directions, np.array(triangles, dtype=np.int32)


def circle_groups(
    *, coplanar: bool = False, noise: float = 0.0
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    theta = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    xy = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(32)))
    yz = (
        xy
        if coplanar
        else np.column_stack((np.zeros(32), np.cos(theta), np.sin(theta)))
    )
    directions = np.vstack((xy, yz))
    radii = np.repeat([3.0, 5.0], 32)
    points = (
        CENTER + (radii + noise * np.sin(np.arange(64) * 1.7))[:, None] * directions
    )
    weights = np.linspace(0.2, 2.0, 64)
    return [points[:32], points[32:]], [weights[:32], weights[32:]]


def binding(points: list[np.ndarray]) -> InitializationBinding:
    counts = [len(group) for group in points]
    boundaries = np.r_[0, np.cumsum(counts)]
    return initialization_binding(
        hashlib.sha256(np.concatenate(points).astype("<f8").tobytes()).hexdigest(),
        hashlib.sha256(b"independent-float64-sphere-fixture-v1").hexdigest(),
        list(range(sum(counts))),
        {
            "family": "sphere",
            "members": [list(range(int(a), int(b))) for a, b in pairwise(boundaries)],
            "references": {"center": "shared-free"},
            "constraints": {"radii": "independent"},
            "support": None,
            "settings": {},
        },
    )


def shared_gradient(
    parameters: np.ndarray, points: list[np.ndarray], weights: list[np.ndarray]
) -> float:
    joined = np.concatenate(points)
    combined = np.concatenate(weights)
    normalized = combined / combined.sum()
    frame = CoordinateFrame.from_observations(joined, combined)
    residuals: list[np.ndarray] = []
    jacobians: list[np.ndarray] = []
    for index, group in enumerate(points):
        offset = group - parameters[:3]
        distance = np.linalg.norm(offset, axis=1)
        residuals.append((distance - parameters[3 + index]) / frame.length)
        jacobian = np.zeros((len(group), 3 + len(points)))
        jacobian[:, :3] = -offset / distance[:, None]
        jacobian[:, 3 + index] = -1
        jacobians.append(jacobian)
    return float(
        np.max(
            np.abs(np.vstack(jacobians).T @ (normalized * np.concatenate(residuals)))
        )
    )


def strict_ply(points: np.ndarray, normals: np.ndarray, triangles: np.ndarray) -> bytes:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Independently generated spherical observations\n"
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
    return header + vertices + faces


def import_mesh(
    points: np.ndarray, normals: np.ndarray, triangles: np.ndarray
) -> ScanWorkspace:
    return prepare_scan(
        io.BytesIO(strict_ply(points, normals, triangles)),
        "independent-spheres.ply",
        ScanLimits(100_000, 1_000, 2_000, 10_000_000),
    )


def imported_spheres() -> ScanWorkspace:
    first, first_normals, first_triangles = sphere_grid(CENTER, 3.0)
    second, second_normals, second_triangles = sphere_grid(
        CENTER + np.array([7.0, -2.0, 4.0]), 5.0
    )
    return import_mesh(
        np.vstack((first, second)),
        np.vstack((first_normals, second_normals)),
        np.vstack((first_triangles, second_triangles + len(first))),
    )


def source(workspace: ScanWorkspace) -> Source:
    assert workspace.source_sha256 is not None
    assert workspace.descriptor is not None
    return Source(
        id="scan",
        label="Independent source",
        operation="source",
        source_sha256=workspace.source_sha256,
        reference_sha256=workspace.reference_sha256,
        descriptor=workspace.descriptor,
    )


def selection(name: str, ids: list[int]) -> Selection:
    return Selection(
        id=name,
        label=f"Selection {name}",
        operation="selection",
        source="scan",
        ids=ids,
    )


def sphere(name: str, selected: str, *, point: str | None = None) -> SurfaceFit:
    return SurfaceFit(
        id=name,
        label=f"Sphere {name}",
        operation="fit",
        selections=[selected],
        kind="sphere",
        point=point,
    )


def results(state: dict[str, object]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], state["results"])


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
@pytest.mark.parametrize("rotated", [False, True])
def test_direct_sphere_is_selection_derived_across_pose_and_scale(
    scale: float, rotated: bool
) -> None:
    points, directions, _ = sphere_grid()
    transform = rotation() if rotated else np.eye(3)
    translation = np.array([80.0, -60.0, 40.0])
    moved = scale * (points @ transform.T + translation)
    weights = np.linspace(0.2, 2.0, len(points))
    fitted = fit_sphere(moved, weights)
    expected_center = CENTER @ transform.T + translation
    np.testing.assert_allclose(
        np.array(fitted["parameters"][:3]) / scale, expected_center, atol=1e-11
    )
    assert fitted["parameters"][3] / scale == pytest.approx(4.5, abs=1e-11)
    assert fitted["weighted_rms"] / scale < 1e-11
    assert shared_gradient(np.array(fitted["parameters"]), [moved], [weights]) < 1e-10
    seed = fit_seed(moved, weights, directions @ transform.T, "sphere", None, (-2, 5))
    np.testing.assert_allclose(
        seed["parameters"], fitted["parameters"], atol=scale * 1e-12
    )
    assert "initialization" in fitted
    assert seed["initialization"] == fitted["initialization"]
    _ = json.dumps(seed["initialization"], allow_nan=False)


@pytest.mark.parametrize("hint", [np.full(5, np.nan), np.full(5, 1e100), np.zeros(2)])
def test_standalone_sphere_ignores_poisoned_optional_seed_values(
    hint: np.ndarray,
) -> None:
    points, normals, _ = sphere_grid(noise=0.001)
    weights = np.linspace(0.2, 2.0, len(points))
    absent = fit_seed(points, weights, normals, "sphere", None, (-2, 5))
    assert fit_seed(points, weights, normals, "sphere", hint, (-2, 5)) == absent


@pytest.mark.parametrize("noise", [0.0, 0.001])
def test_useful_partial_sphere_remains_identifiable_and_stationary(
    noise: float,
) -> None:
    points, _, _ = sphere_grid(partial=True, noise=noise)
    weights = np.linspace(0.2, 2.0, len(points))
    fitted = fit_sphere(points, weights)
    parameters = np.array(fitted["parameters"])
    np.testing.assert_allclose(parameters[:3], CENTER, atol=0.02 if noise else 1e-10)
    assert parameters[3] == pytest.approx(4.5, abs=0.02 if noise else 1e-10)
    assert shared_gradient(parameters, [points], [weights]) < 1e-10
    assert "solver" in fitted
    assert fitted["solver"]["termination"] == "projected-gradient"


@pytest.mark.parametrize(
    ("points", "code"),
    [
        (np.zeros((3, 3)), "insufficient-observations"),
        (np.ones((4, 3)), "rank-deficient"),
        (
            np.array([[-1.0, -1, 0], [1.0, -1, 0], [-1.0, 1, 0], [1.0, 1, 0]]),
            "rank-deficient",
        ),
        (
            np.array([[-1.0, -1, 1e-14], [1.0, -1, 0], [-1.0, 1, 0], [1.0, 1, 1e-14]]),
            "rank-deficient",
        ),
    ],
)
def test_direct_sphere_does_not_invent_identifiable_curvature(
    points: np.ndarray, code: str
) -> None:
    with pytest.raises(SphereFitFailure) as failure:
        _ = fit_sphere(points, np.ones(len(points)))
    assert failure.value.code == code
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
def test_combined_circle_evidence_identifies_center_when_each_patch_cannot(
    scale: float,
) -> None:
    points, weights = circle_groups()
    transform = rotation()
    translation = np.array([80.0, -60.0, 40.0])
    moved = [scale * (group @ transform.T + translation) for group in points]
    # The exact axis-aligned inputs are individually planar. The rotated forms
    # differ from a plane only at floating-point roundoff.
    for group, group_weights in zip(moved, weights, strict=True):
        with pytest.raises(SphereFitFailure) as failure:
            _ = fit_sphere(group, group_weights)
        assert failure.value.code == "rank-deficient"
    fitted = fit_shared_sphere_center(moved, weights, binding(moved))
    parameters = np.array(fitted["parameters"])
    np.testing.assert_allclose(
        parameters[:3] / scale, CENTER @ transform.T + translation, atol=1e-11
    )
    np.testing.assert_allclose(parameters[3:] / scale, [3.0, 5.0], atol=1e-11)
    assert shared_gradient(parameters, moved, weights) < 1e-10
    initialization = fitted["initialization"]
    assert initialization["algorithm"] == "joint-algebraic-shared-sphere-v1"
    assert initialization["chart"] == "normalized-center-radii-v1"
    assert initialization["algebraic_rank"] == 5
    assert initialization["observations"] == 64
    assert initialization["algebraic_condition"] == pytest.approx(
        2.8363638095158685, rel=1e-10
    )
    assert fitted["solver"]["rank"] == 5
    assert fitted["solver"]["termination"] == "projected-gradient"
    assert initialization["recovery"]["selected_role"] == "fresh"
    _ = json.dumps(initialization, allow_nan=False)


@pytest.mark.parametrize(
    "hint",
    [
        np.array([100.0, -100, 100]),
        np.array([1e6, -1e6, 1e6]),
        np.full(3, np.nan),
        np.full(3, np.inf),
        np.zeros(2),
    ],
)
def test_shared_center_recovers_from_wrong_or_nonfinite_optional_hints(
    hint: np.ndarray,
) -> None:
    points, weights = circle_groups()
    request_binding = binding(points)
    baseline = fit_shared_sphere_center(points, weights, request_binding)
    fitted = fit_shared_sphere_center(
        points, weights, request_binding, hint_center=hint
    )
    np.testing.assert_allclose(fitted["parameters"], baseline["parameters"], atol=1e-9)
    np.testing.assert_allclose(fitted["parameters"], [*CENTER, 3.0, 5.0], atol=1e-9)
    assert shared_gradient(np.array(fitted["parameters"]), points, weights) < 1e-10
    assert fitted["initialization"]["recovery"]["selected"] is not None


def test_unrepresentable_finite_center_hint_is_screened_without_losing_fresh_fit() -> (
    None
):
    points, weights = circle_groups()
    original_points = [group.copy() for group in points]
    original_weights = [group.copy() for group in weights]
    request_binding = binding(points)
    baseline = fit_shared_sphere_center(points, weights, request_binding)
    recovered = fit_shared_sphere_center(
        points,
        weights,
        request_binding,
        hint_center=np.array([1e100, -1e100, 1e100]),
    )
    np.testing.assert_allclose(
        recovered["parameters"], baseline["parameters"], rtol=0, atol=1e-12
    )
    baseline_recovery = baseline["initialization"]["recovery"]
    diagnostics = recovered["initialization"]["recovery"]
    assert diagnostics["selected_role"] == "fresh"
    assert diagnostics["attempts"][0]["role"] == "fresh"
    assert (
        diagnostics["attempts"][0]["assessment"]["objective"]
        == baseline_recovery["attempts"][0]["assessment"]["objective"]
    )
    assert len(diagnostics["attempts"]) == 2
    hint = diagnostics["attempts"][1]
    assert hint["role"] == "hint"
    assert hint["candidate"]["chart"] == "unrepresentable-center-hint"
    assert hint["outcome"] == "screened-invalid-or-stale"
    assert hint["evaluations"] == 0
    assert hint["solver"] is None
    for actual, original in zip(points, original_points, strict=True):
        np.testing.assert_array_equal(actual, original)
    for actual, original in zip(weights, original_weights, strict=True):
        np.testing.assert_array_equal(actual, original)


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
def test_noisy_joint_algebraic_start_receives_geometric_refinement(
    scale: float,
) -> None:
    points, weights = circle_groups(noise=0.01)
    moved = [scale * (group + np.array([80.0, -60.0, 40.0])) for group in points]
    fitted = fit_shared_sphere_center(moved, weights, binding(moved))
    parameters = np.array(fitted["parameters"])
    np.testing.assert_allclose(
        parameters[:3] / scale - [80, -60, 40], CENTER, atol=0.01
    )
    np.testing.assert_allclose(parameters[3:] / scale, [3, 5], atol=0.01)
    assert fitted["solver"]["iterations"] > 0
    assert shared_gradient(parameters, moved, weights) <= 1e-10


def test_same_plane_circles_remain_unidentifiable_even_with_a_correct_hint() -> None:
    points, weights = circle_groups(coplanar=True)
    with pytest.raises(SphereFitFailure) as failure:
        _ = fit_shared_sphere_center(
            points, weights, binding(points), hint_center=CENTER
        )
    assert failure.value.code == "rank-deficient"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "defect", ["insufficient", "coincident", "invalid-weight", "nonfinite"]
)
def test_shared_preflight_failure_is_explicit(defect: str) -> None:
    points, weights = circle_groups()
    request_binding = binding(points)
    expected = "invalid-input"
    if defect == "insufficient":
        points[0], weights[0] = points[0][:3], weights[0][:3]
        expected = "insufficient-observations"
    elif defect == "coincident":
        points = [np.tile(CENTER, (32, 1)), np.tile(CENTER, (32, 1))]
        expected = "rank-deficient"
    elif defect == "invalid-weight":
        weights[0][0] = 0
    else:
        points[0][0, 0] = np.nan
    with pytest.raises(SphereFitFailure) as failure:
        _ = fit_shared_sphere_center(points, weights, request_binding)
    assert failure.value.code == expected


def test_shared_center_cancellation_does_not_publish_a_result() -> None:
    points, weights = circle_groups()
    with pytest.raises(FitCancelled):
        _ = fit_shared_sphere_center(
            points, weights, binding(points), cancelled=lambda: True
        )


@pytest.mark.parametrize("shared", [False, True])
def test_sphere_fitting_rejects_weights_that_would_erase_a_positive_observation(
    shared: bool,
) -> None:
    if shared:
        groups, group_weights = circle_groups()
        group_weights[0][0] = 1e-300
        group_weights[1][-1] = 1e300
        with pytest.raises(SphereFitFailure) as failure:
            _ = fit_shared_sphere_center(groups, group_weights, binding(groups))
    else:
        points, _normals, _triangles = sphere_grid()
        weights = np.ones(len(points))
        weights[0], weights[-1] = 1e-300, 1e300
        with pytest.raises(SphereFitFailure) as failure:
            _ = fit_sphere(points, weights)
    assert failure.value.code == "invalid-input"
    assert "underflows" in str(failure.value)
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_imported_sphere_graph_growth_and_recipe_replay_are_fixture_independent() -> (
    None
):
    points, normals, triangles = sphere_grid()
    workspace = import_mesh(points, normals, triangles)
    assert not hasattr(workspace.data, "selection")
    assert np.ptp(workspace.data.weights) > 0
    selected = selection("patch", list(range(72)))
    fitted_sphere = sphere("sphere", "patch")
    growth = Growth(
        id="grown",
        label="Grown sphere patch",
        operation="growth",
        seed_fit="sphere",
        distance=0.001,
        angle_degrees=10,
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[source(workspace), selected, fitted_sphere, growth],
        output="grown",
    )
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    fitted = results(state)["sphere"]
    np.testing.assert_allclose(fitted["parameters"], [*CENTER, 4.5], atol=1e-5)
    assert len(fitted["residuals"]) == 72
    assert results(state)["grown"]["ids"] == list(range(216))
    residual, expected_normals, supported = surface_distance(workspace.local, fitted)
    assert supported.all()
    assert np.max(np.abs(residual)) < 1e-5
    np.testing.assert_allclose(expected_normals, workspace.data.normals, atol=1e-5)
    encoded = json.dumps(state["recipe"], allow_nan=False)
    assert "initial_parameters" not in encoded and "initialization" not in encoded
    replay = FeatureGraph(workspace, Recipe.model_validate_json(encoded))
    reopened = replay.evaluate(str(replay.snapshot()["token"]))
    assert not reopened["errors"]
    assert results(reopened) == results(state)


def test_authored_free_point_is_a_hint_and_its_saved_definition_is_preserved() -> None:
    points, normals, triangles = sphere_grid()
    workspace = import_mesh(points, normals, triangles)
    point = PointDefinition(
        id="center",
        label="Authored free center",
        operation="point",
        initial_coordinates=(100, -100, 100),
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[
            source(workspace),
            selection("patch", list(range(216))),
            point,
            sphere("sphere", "patch", point="center"),
        ],
        output="sphere",
    )
    graph = FeatureGraph(workspace, recipe)
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]))
    assert state["recipe"] == before["recipe"]
    np.testing.assert_allclose(
        results(state)["center"]["point_display"], CENTER, atol=1e-6
    )
    np.testing.assert_allclose(
        results(state)["sphere"]["parameters"], [*CENTER, 4.5], atol=1e-6
    )
    assert results(state)["sphere"]["resolved_by"] == "connected_fits"


@pytest.mark.parametrize("operation", ["initial", "replace"])
def test_graph_tokens_ignore_equivalent_pydantic_exponent_spellings(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    points, normals, triangles = sphere_grid()
    workspace = import_mesh(points, normals, triangles)
    point = PointDefinition(
        id="center",
        label="Authored center",
        operation="point",
        initial_coordinates=(1e100, -1e100, 1.0),
    )
    recipe = Recipe(schema_version=2, nodes=[source(workspace), point], output="center")
    original_serializer = Recipe.model_dump_json
    explicit_exponent_sign = True

    def serialize(self: Recipe, *args: Any, **kwargs: Any) -> str:
        # Simulate the observed native 2.13/browser 2.12 Pydantic difference.
        value = original_serializer(self, *args, **kwargs).replace("e+100", "e100")
        return value.replace("e100", "e+100") if explicit_exponent_sign else value

    monkeypatch.setattr(Recipe, "model_dump_json", serialize)
    native_json = recipe.model_dump_json()
    graph = FeatureGraph(workspace, recipe)
    native_token = graph.snapshot()["token"]
    explicit_exponent_sign = False
    browser_json = recipe.model_dump_json()
    assert browser_json != native_json
    assert json.loads(browser_json) == json.loads(native_json)
    equivalent_recipe = Recipe.model_validate_json(browser_json)
    if operation == "initial":
        graph = FeatureGraph(workspace, equivalent_recipe)
        assert graph.snapshot()["token"] == native_token
    else:
        unchanged = graph.replace(equivalent_recipe, str(native_token))
        assert unchanged["token"] == native_token

    revised_point = point.model_copy(
        update={"initial_coordinates": (2e100, -1e100, 1.0)}
    )
    revised_recipe = Recipe(
        schema_version=2, nodes=[source(workspace), revised_point], output="center"
    )
    changed = graph.replace(revised_recipe, str(native_token))
    assert changed["token"] != native_token
    explicit_exponent_sign = True
    independently_reopened = FeatureGraph(workspace, revised_recipe)
    assert independently_reopened.snapshot()["token"] == changed["token"]
    assert (
        graph.replace(revised_recipe, str(changed["token"]))["token"]
        == changed["token"]
    )


def test_imported_shared_center_uses_combined_identifiability_in_graph() -> None:
    groups, _weights = circle_groups()
    points = np.concatenate(groups)
    normals = np.vstack(((groups[0] - CENTER) / 3, (groups[1] - CENTER) / 5))
    triangles = np.array(
        [
            (start, start + index, start + index + 1)
            for start in [0, 32]
            for index in range(1, 31)
        ],
        dtype=np.int32,
    )
    workspace = import_mesh(points, normals, triangles)
    point = PointDefinition(
        id="center",
        label="Shared free center",
        operation="point",
        initial_coordinates=(100, -100, 100),
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[
            source(workspace),
            point,
            selection("a", list(range(32))),
            sphere("first", "a", point="center"),
            selection("b", list(range(32, 64))),
            sphere("second", "b", point="center"),
        ],
        output="second",
    )
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    np.testing.assert_allclose(
        results(state)["center"]["point_display"], CENTER, atol=1e-6
    )
    np.testing.assert_allclose(
        results(state)["first"]["parameters"], [*CENTER, 3.0], atol=1e-6
    )
    np.testing.assert_allclose(
        results(state)["second"]["parameters"], [*CENTER, 5.0], atol=1e-6
    )
    assert (
        results(state)["first"]["parameters"][:3]
        == results(state)["second"]["parameters"][:3]
    )


def test_source_fit_center_is_fixed_for_a_downstream_sphere() -> None:
    workspace = imported_spheres()
    point = PointDefinition(
        id="fixed", label="Source-bound center", operation="point", source_fit="first"
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[
            source(workspace),
            selection("a", list(range(216))),
            sphere("first", "a"),
            point,
            selection("b", list(range(216, 432))),
            sphere("second", "b", point="fixed"),
        ],
        output="second",
    )
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    center = results(state)["first"]["parameters"][:3]
    assert results(state)["fixed"]["point_display"] == center
    assert results(state)["second"]["parameters"][:3] == center
    assert "resolved_by" not in results(state)["fixed"]
    distances = np.linalg.norm(workspace.local[216:] - center, axis=1)
    expected_radius = np.average(distances, weights=workspace.data.weights[216:])
    assert results(state)["second"]["parameters"][3] == pytest.approx(expected_radius)


def test_fixed_center_with_poor_residual_is_not_moved_to_improve_the_fit() -> None:
    points, normals, triangles = sphere_grid()
    workspace = import_mesh(points, normals, triangles)
    fixed = np.array([100.0, -100, 100])
    node = sphere("bound", "patch", point="fixed")
    fitted = fit_sphere_at_point(
        workspace, node, list(range(216)), {"point_display": fixed.tolist()}
    )
    np.testing.assert_array_equal(fitted["parameters"][:3], fixed)
    expected_radius = np.average(
        np.linalg.norm(workspace.local - fixed, axis=1), weights=workspace.data.weights
    )
    assert fitted["parameters"][3] == pytest.approx(expected_radius)
    assert fitted["weighted_rms"] > 1


def test_equal_radius_overlay_preserves_both_resolved_centers() -> None:
    workspace = imported_spheres()
    nodes: list[Feature] = [
        source(workspace),
        selection("a", list(range(216))),
        sphere("first", "a"),
        selection("b", list(range(216, 432))),
        sphere("second", "b"),
    ]
    initial_graph = FeatureGraph(
        workspace, Recipe(schema_version=2, nodes=nodes, output="second")
    )
    initial = initial_graph.evaluate(
        str(initial_graph.snapshot()["token"]), all_actions=True
    )
    relation = EqualRadii(
        id="radii",
        label="Equal radii",
        operation="equal_radii",
        surfaces=["first", "second"],
    )
    graph = FeatureGraph(
        workspace, Recipe(schema_version=2, nodes=[*nodes, relation], output="radii")
    )
    state = graph.evaluate(str(graph.snapshot()["token"]))
    combined_radii: list[np.ndarray] = []
    for name, interval in (("first", slice(0, 216)), ("second", slice(216, 432))):
        np.testing.assert_array_equal(
            results(state)[name]["parameters"][:3],
            results(initial)[name]["parameters"][:3],
        )
        combined_radii.append(
            np.linalg.norm(
                workspace.local[interval] - results(initial)[name]["parameters"][:3],
                axis=1,
            )
        )
    radius = np.average(np.concatenate(combined_radii), weights=workspace.data.weights)
    assert (
        results(state)["first"]["parameters"][3]
        == results(state)["second"]["parameters"][3]
    )
    assert results(state)["first"]["parameters"][3] == pytest.approx(radius)


def test_equal_radius_overlay_retains_the_resolved_shared_free_center() -> None:
    # The source patches come from different true centers and radii. The free
    # point first gives them one resolved center; the radius overlay retains it.
    workspace = imported_spheres()
    point = PointDefinition(
        id="center",
        label="Shared free center",
        operation="point",
        initial_coordinates=(100, -100, 100),
    )
    nodes: list[Feature] = [
        source(workspace),
        point,
        selection("a", list(range(216))),
        sphere("first", "a", point="center"),
        selection("b", list(range(216, 432))),
        sphere("second", "b", point="center"),
    ]
    baseline_graph = FeatureGraph(
        workspace, Recipe(schema_version=2, nodes=nodes, output="second")
    )
    baseline = baseline_graph.evaluate(str(baseline_graph.snapshot()["token"]))
    shared_center = np.array(results(baseline)["center"]["point_display"])
    assert (
        results(baseline)["first"]["parameters"][:3]
        == results(baseline)["second"]["parameters"][:3]
    )
    assert (
        abs(
            results(baseline)["first"]["parameters"][3]
            - results(baseline)["second"]["parameters"][3]
        )
        > 0.1
    )
    relation = EqualRadii(
        id="radii",
        label="Shared-center equal radii",
        operation="equal_radii",
        surfaces=["first", "second"],
    )
    graph = FeatureGraph(
        workspace, Recipe(schema_version=2, nodes=[*nodes, relation], output="radii")
    )
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]))
    assert not state["errors"]
    assert state["recipe"] == before["recipe"]
    np.testing.assert_array_equal(
        results(state)["center"]["point_display"], shared_center
    )
    distances = np.linalg.norm(workspace.local - shared_center, axis=1)
    weights = workspace.data.weights / workspace.data.weights.sum()
    expected_radius = float(weights @ distances)
    first_radius = results(state)["first"]["parameters"][3]
    assert first_radius == results(state)["second"]["parameters"][3]
    assert first_radius == pytest.approx(expected_radius)
    assert abs(weights @ (distances - first_radius)) < 1e-12
    for name, interval in (("first", slice(0, 216)), ("second", slice(216, 432))):
        fitted = results(state)[name]
        np.testing.assert_array_equal(fitted["parameters"][:3], shared_center)
        assert len(fitted["residuals"]) == 216
        np.testing.assert_allclose(
            fitted["residuals"], distances[interval] - first_radius, atol=1e-13
        )
        expected_rms = np.sqrt(
            np.average(
                (distances[interval] - first_radius) ** 2,
                weights=workspace.data.weights[interval],
            )
        )
        assert fitted["weighted_rms"] == pytest.approx(expected_rms)
        assert fitted["resolved_by"] == "equal_radii"


def test_failed_imported_sphere_preserves_definitions_and_reports_identifiability() -> (
    None
):
    theta = np.linspace(0, 2 * np.pi, 16, endpoint=False)
    normals = np.column_stack((np.cos(theta), np.sin(theta), np.zeros(16)))
    points = CENTER + 3 * normals
    triangles = np.array(
        [(0, index, index + 1) for index in range(1, 15)], dtype=np.int32
    )
    workspace = import_mesh(points, normals, triangles)
    recipe = Recipe(
        schema_version=2,
        nodes=[
            source(workspace),
            selection("patch", list(range(16))),
            sphere("sphere", "patch"),
        ],
        output="sphere",
    )
    graph = FeatureGraph(workspace, recipe)
    before = graph.snapshot()
    with pytest.raises(SphereFitFailure) as failure:
        _ = graph.evaluate(str(before["token"]))
    assert failure.value.code == "rank-deficient"
    state = graph.snapshot()
    assert state["recipe"] == before["recipe"]
    assert state["token"] == before["token"]
    assert "sphere" not in results(state)
    diagnostic = cast(dict[str, dict[str, Any]], state["diagnostics"])["sphere"]
    assert diagnostic["code"] == "rank-deficient"
    _ = json.dumps(diagnostic, allow_nan=False)


def assert_spherical_shape(shape: TopoDS_Shape, parameters: np.ndarray) -> None:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == GeomAbs_Sphere
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = np.array(
        [
            adaptor.Value(u0 + (u1 - u0) * u, v0 + (v1 - v0) * v).Coord()
            for u, v in [(0.1, 0.3), (0.7, 0.4), (0.4, 0.9)]
        ]
    )
    np.testing.assert_allclose(
        np.linalg.norm(samples - parameters[:3], axis=1), parameters[3], atol=1e-7
    )
    explorer.Next()
    assert not explorer.More()


def test_imported_sphere_ocp_and_step_readback_preserve_source_coordinates(
    tmp_path: Path,
) -> None:
    points, normals, triangles = sphere_grid()
    workspace = import_mesh(points, normals, triangles)
    graph = FeatureGraph(
        workspace,
        Recipe(
            schema_version=2,
            nodes=[
                source(workspace),
                selection("patch", list(range(216))),
                sphere("sphere", "patch"),
            ],
            output="sphere",
        ),
    )
    state = graph.evaluate(str(graph.snapshot()["token"]))
    fitted = results(state)["sphere"]
    parameters = np.array(fitted["parameters"])
    assert_spherical_shape(surface_patch(fitted, workspace.local), parameters)
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=str(state["token"]),
            target="sphere",
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        np.testing.assert_array_equal(metadata["transform_local_to_export"], np.eye(4))
        assert metadata["source_sha256"] == workspace.source_sha256
        path = tmp_path / "independent-sphere.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_spherical_shape(reader.OneShape(), parameters)
