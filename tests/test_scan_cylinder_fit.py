"""Independent selected-data cylinder recovery and imported-source regressions."""

import hashlib
import io
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from experiments.feature_graph import (
    AxisDefinition,
    FeatureGraph,
    Growth,
    Recipe,
    Selection,
    Source,
    SurfaceFit,
)
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import (
    InitializationBinding,
    RecoveryPolicy,
    initialization_binding,
)
from experiments.fit_relationship_component import (
    AxisInput,
    SurfaceInput,
    fit_relationship_component,
)
from experiments.fit_solver import AttemptLimits, FitCancelled
from experiments.mesh_cylinder_fit import CylinderFitFailure, fit_selected_cylinder
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.ocp_geometry import surface_patch
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_growth import surface_distance

ORIGIN = np.array([3.0, -4.0, 5.0])
AXES = [np.array([1.0, 0, 0]), np.array([1.0, 0.2, 1e-8]), np.array([0.3, -0.5, 1])]


def transverse_frame(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction = axis / np.linalg.norm(axis)
    first = np.cross(direction, np.eye(3)[int(np.argmin(np.abs(direction)))])
    first /= np.linalg.norm(first)
    return direction, first, np.cross(direction, first)


def cylinder_grid(
    axis: np.ndarray = AXES[2], *, partial: bool = False, noise: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Prescribed radius/axis, with angular and axial coverage independent of fits."""
    direction, first, second = transverse_frame(axis)
    angles = (
        np.linspace(-0.8, 1.2, 24)
        if partial
        else np.linspace(0, 2 * np.pi, 32, endpoint=False)
    )
    theta, height = np.meshgrid(angles, np.linspace(-3, 3, 7))
    radial = (
        np.cos(theta.ravel())[:, None] * first + np.sin(theta.ravel())[:, None] * second
    )
    points = (
        ORIGIN
        + height.ravel()[:, None] * direction
        + (2.5 + noise * np.sin(np.arange(theta.size) * 1.7))[:, None] * radial
    )
    triangles: list[tuple[int, int, int]] = []
    width = len(angles)
    for row in range(6):
        for column in range(width - 1 if partial else width):
            a = row * width + column
            b = row * width + (column + 1) % width
            triangles.extend([(a, b, a + width), (b, b + width, a + width)])
    return points, radial, np.array(triangles, dtype=np.int32)


def binding(
    points: np.ndarray,
    weights: np.ndarray,
    normals: np.ndarray,
    *,
    policy: RecoveryPolicy | None = None,
    domain: tuple[float, float] | None = None,
) -> InitializationBinding:
    return initialization_binding(
        hashlib.sha256(points.astype("<f8").tobytes()).hexdigest(),
        hashlib.sha256(
            weights.astype("<f8").tobytes() + normals.astype("<f8").tobytes()
        ).hexdigest(),
        list(range(len(points))),
        {
            "family": "cylinder",
            "references": {},
            "constraints": {},
            "support": domain,
            "settings": {},
        },
        policy=policy,
    )


def geometry(result: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float]:
    analytic = result["analytic"]
    return (
        np.array(analytic["origin"]),
        np.array(analytic["axis"]),
        float(analytic["radius"]),
    )


def residuals(points: np.ndarray, result: dict[str, Any]) -> np.ndarray:
    origin, axis, radius = geometry(result)
    offset = points - origin
    return np.linalg.norm(offset - (offset @ axis)[:, None] * axis, axis=1) - radius


def gradient(points: np.ndarray, weights: np.ndarray, result: dict[str, Any]) -> float:
    """Independent five-direction derivative in an axis-aligned normalized frame."""
    origin, axis, _radius = geometry(result)
    _direction, first, second = transverse_frame(axis)
    frame = CoordinateFrame.from_observations(points, weights)
    anchor = origin + ((frame.origin - origin) @ axis) * axis
    offset = (points - anchor) / frame.length
    axial = offset @ axis
    radial = offset - axial[:, None] * axis
    unit = radial / np.linalg.norm(radial, axis=1)[:, None]
    jacobian = np.column_stack(
        (
            -unit @ first,
            -unit @ second,
            -axial * (unit @ first),
            -axial * (unit @ second),
            -np.ones(len(points)),
        )
    )
    return float(
        np.max(
            np.abs(
                jacobian.T
                @ ((weights / weights.sum()) * residuals(points, result) / frame.length)
            )
        )
    )


def assert_known_cylinder(
    fitted: dict[str, Any], axis: np.ndarray, *, scale: float, tolerance: float
) -> None:
    origin, direction, radius = geometry(fitted)
    expected_axis = axis / np.linalg.norm(axis)
    expected_origin = scale * (ORIGIN + np.array([80, -60, 40]))
    nearest = origin + ((expected_origin - origin) @ direction) * direction
    assert np.linalg.norm(np.cross(direction, expected_axis)) < tolerance
    np.testing.assert_allclose(nearest / scale, expected_origin / scale, atol=tolerance)
    assert radius / scale == pytest.approx(2.5, abs=tolerance)
    assert np.linalg.norm(direction) == pytest.approx(1, abs=1e-13)
    assert fitted["analytic"]["slope"] == 0
    assert fitted["analytic"]["kind"] == "cylinder"
    assert "parameters" not in fitted
    lo, hi = fitted["analytic"]["axial_domain"]
    assert np.isfinite([lo, hi]).all() and lo < hi


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("partial", [False, True])
def test_selected_cylinder_recovers_arbitrary_pose_and_scale(
    scale: float, axis: np.ndarray, partial: bool
) -> None:
    points, normals, _triangles = cylinder_grid(axis, partial=partial)
    points = scale * (points + np.array([80, -60, 40]))
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cylinder(fitted, axis, scale=scale, tolerance=1e-10)
    assert fitted["weighted_rms"] / scale < 1e-10
    assert gradient(points, weights, fitted) < 1e-10
    assert fitted["solver"]["rank"] == 5
    initialization = fitted["initialization"]
    assert initialization["algorithm"] == "selected-cylinder-recovery-v1"
    assert initialization["chart"] == "rotated-normalized-cylinder-v1"
    assert initialization["observations"] == len(points)
    assert initialization["recovery"]["selected_role"] == "fresh"
    attempts = initialization["recovery"]["attempts"]
    assert 1 <= len([item for item in attempts if item["role"] == "fresh"]) <= 6
    _ = json.dumps(fitted, allow_nan=False)


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
@pytest.mark.parametrize("partial", [False, True])
def test_noisy_cylinder_refines_all_nonuniformly_weighted_observations(
    scale: float, partial: bool
) -> None:
    points, normals, _triangles = cylinder_grid(partial=partial, noise=0.005)
    points = scale * (points + np.array([80, -60, 40]))
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cylinder(fitted, AXES[2], scale=scale, tolerance=0.001)
    measured = residuals(points, fitted)
    np.testing.assert_allclose(fitted["residuals"], measured, atol=scale * 1e-12)
    expected_rms = np.sqrt((weights / weights.sum()) @ (measured / scale) ** 2)
    assert fitted["weighted_rms"] / scale == pytest.approx(expected_rms, abs=1e-12)
    assert 0.003 < expected_rms < 0.0037
    assert gradient(points, weights, fitted) < 1e-9
    assert fitted["solver"]["iterations"] > 0


@pytest.mark.parametrize("partial", [False, True])
def test_normals_propose_directions_without_overriding_positional_evidence(
    partial: bool,
) -> None:
    points, _normals, _triangles = cylinder_grid(partial=partial)
    points += [80, -60, 40]
    normals = np.tile([0.0, 0, 1], (len(points), 1))
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cylinder(fitted, AXES[2], scale=1, tolerance=1e-9)
    assert fitted["weighted_rms"] < 1e-10


def test_reversed_selected_normals_preserve_geometry_and_reverse_normal_sign() -> None:
    points, normals, _triangles = cylinder_grid()
    weights = np.linspace(0.2, 2, len(points))
    original = fit_selected_cylinder(
        points, weights, normals, binding(points, weights, normals)
    )
    reversed_fit = fit_selected_cylinder(
        points, weights, -normals, binding(points, weights, -normals)
    )
    assert geometry(original)[2] == pytest.approx(geometry(reversed_fit)[2], abs=1e-12)
    np.testing.assert_allclose(
        residuals(points, original), residuals(points, reversed_fit), atol=1e-12
    )
    assert original["normal_sign"] == 1
    assert reversed_fit["normal_sign"] == -1


@pytest.mark.parametrize(
    "hint",
    [
        {"origin": [1e4, -1e4, 0], "axis": [0, 1, 0], "radius": 2.5},
        {"origin": [1e100, -1e100, 1e100], "axis": [0, 0, 1], "radius": 1e100},
        {"origin": [3, -4, 5], "axis": [0, 0, 0], "radius": 2.5},
        {"origin": [3, -4, 5], "axis": [0, 0, 1], "radius": -2.5},
        {"origin": [np.nan, 0, 0], "axis": [0, 0, 1], "radius": 2.5},
        {"origin": [0, 0], "axis": [0, 0, 1], "radius": 2.5},
        {"analytic": {"origin": [np.inf, 0, 0], "axis": [1, 0, 0], "radius": 1}},
        {"origin": ORIGIN.tolist(), "axis": AXES[2].tolist(), "radius": 2.5e-6},
        {"origin": ORIGIN.tolist(), "axis": AXES[2].tolist(), "radius": 2.5e6},
    ],
)
def test_bad_optional_hint_preserves_fresh_candidates_and_success(
    hint: dict[str, Any],
) -> None:
    points, normals, _triangles = cylinder_grid(noise=0.005)
    weights = np.linspace(0.2, 2, len(points))
    before = [array.copy() for array in (points, weights, normals)]
    request = binding(points, weights, normals)
    baseline = fit_selected_cylinder(points, weights, normals, request)
    fitted = fit_selected_cylinder(points, weights, normals, request, hint=hint)
    np.testing.assert_allclose(
        residuals(points, fitted), residuals(points, baseline), atol=1e-8
    )

    def fresh(result: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            attempt
            for attempt in result["initialization"]["recovery"]["attempts"]
            if attempt["role"] == "fresh"
        ]

    assert fresh(fitted) == fresh(baseline)
    for actual, expected in zip((points, weights, normals), before, strict=True):
        np.testing.assert_array_equal(actual, expected)
    _ = json.dumps(fitted["initialization"], allow_nan=False)


def test_stale_valid_hint_is_screened_without_consuming_fresh_budget() -> None:
    points, normals, _triangles = cylinder_grid()
    weights = np.linspace(0.2, 2, len(points))
    request = binding(points, weights, normals)
    fitted = fit_selected_cylinder(
        points,
        weights,
        normals,
        request,
        hint={"origin": ORIGIN.tolist(), "axis": AXES[2].tolist(), "radius": 2.5},
        hint_binding_sha256="0" * 64,
    )
    hint = fitted["initialization"]["recovery"]["attempts"][-1]
    assert hint["role"] == "hint"
    assert hint["outcome"] == "screened-invalid-or-stale"
    assert hint["evaluations"] == 0 and hint["solver"] is None
    assert fitted["weighted_rms"] < 1e-10


@pytest.mark.parametrize(
    "case", ["strip", "circle", "generators", "plane", "noisy-plane"]
)
def test_unidentifiable_or_nearly_planar_positions_do_not_publish_a_cylinder(
    case: str,
) -> None:
    angles = (
        np.linspace(-1e-7, 1e-7, 32)
        if case == "strip"
        else np.linspace(0, 2 * np.pi, 32, endpoint=False)
    )
    if case == "generators":
        angles = np.array([-0.5, 0.5])
    theta, height = np.meshgrid(
        angles, [0.0] if case == "circle" else np.linspace(-3, 3, 7)
    )
    normals = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    points = 2.5 * normals + height.ravel()[:, None] * [0, 0, 1]
    if "plane" in case:
        x, y = np.meshgrid(np.linspace(-3, 3, 12), np.linspace(-2, 2, 11))
        points = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
        normals = np.tile([0.0, 0, 1], (len(points), 1))
        if case == "noisy-plane":
            points[:, 2] = 0.005 * np.sin(np.arange(len(points)) * 1.7)
    weights = np.linspace(0.2, 2, len(points))
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(
            points, weights, normals, binding(points, weights, normals)
        )
    if case != "noisy-plane":
        assert failure.value.code == "insufficient-coverage"
    else:
        assert failure.value.code == "search-exhausted"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "defect",
    [
        "few",
        "coincident",
        "nonfinite",
        "zero-weight",
        "underflow",
        "invalid-normal",
        "zero-normal",
    ],
)
def test_invalid_cylinder_observations_fail_explicitly(defect: str) -> None:
    points, normals, _triangles = cylinder_grid()
    weights = np.ones(len(points))
    request = binding(points, weights, normals)
    if defect == "few":
        points, normals, weights = points[:5], normals[:5], weights[:5]
    elif defect == "coincident":
        points[:] = 1e100
    elif defect == "nonfinite":
        points[0, 0] = np.inf
    elif defect == "zero-weight":
        weights[0] = 0
    elif defect == "underflow":
        weights[0], weights[-1] = 1e-300, 1e300
    elif defect == "invalid-normal":
        normals[0] = np.nan
    else:
        normals[0] = 0
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(points, weights, normals, request)
    expected = {"few": "insufficient-observations", "zero-normal": "invalid-normals"}
    assert failure.value.code == expected.get(defect, "invalid-input")
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("during", [False, True])
def test_selected_cylinder_cancellation_propagates(during: bool) -> None:
    points, normals, _triangles = cylinder_grid(noise=0.005)
    weights = np.ones(len(points))
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= (8 if during else 1)

    with pytest.raises(FitCancelled):
        _ = fit_selected_cylinder(
            points,
            weights,
            normals,
            binding(points, weights, normals),
            cancelled=cancelled,
        )
    assert calls == (8 if during else 1)


@pytest.mark.parametrize("extra_hint", [False, True])
def test_selected_cylinder_obeys_bound_callback_budget(extra_hint: bool) -> None:
    points, normals, _triangles = cylinder_grid(noise=0.005)
    weights = np.ones(len(points))
    policy = RecoveryPolicy(
        max_fresh_attempts=1, attempt=AttemptLimits(max_evaluations=1)
    )
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(
            points,
            weights,
            normals,
            binding(points, weights, normals, policy=policy),
            hint={"origin": ORIGIN.tolist(), "axis": AXES[2].tolist(), "radius": 2.5}
            if extra_hint
            else None,
        )
    recovery = failure.value.diagnostic["recovery"]
    assert failure.value.code == "search-exhausted"
    assert recovery["selected"] is None
    executed = [attempt for attempt in recovery["attempts"] if attempt["evaluations"]]
    assert len(executed) == (2 if extra_hint else 1)
    assert [attempt["role"] for attempt in executed] == (
        ["fresh", "hint"] if extra_hint else ["fresh"]
    )
    assert executed[0]["evaluations"] == 1
    assert executed[0]["outcome"] == "evaluation-limit"
    assert all(
        attempt["outcome"] == "fresh-attempt-limit"
        for attempt in recovery["attempts"][1:]
        if attempt["role"] == "fresh"
    )
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_explicit_legacy_domain_preserves_physical_source_zero_anchor() -> None:
    points, normals, _triangles = cylinder_grid()
    weights = np.linspace(0.2, 2, len(points))
    domain = (-2.0, 5.0)
    fitted = fit_selected_cylinder(
        points,
        weights,
        normals,
        binding(points, weights, normals, domain=domain),
        domain=domain,
    )
    origin, axis, _radius = geometry(fitted)
    expected_axis = AXES[2] / np.linalg.norm(AXES[2])
    legacy_anchor = ORIGIN - (ORIGIN[2] / expected_axis[2]) * expected_axis
    expected_ends = legacy_anchor + np.array(domain)[:, None] * expected_axis
    actual_ends = origin + np.array(fitted["analytic"]["axial_domain"])[:, None] * axis
    np.testing.assert_allclose(
        actual_ends[np.argsort(actual_ends @ expected_axis)], expected_ends, atol=1e-10
    )


def test_horizontal_explicit_legacy_domain_fails_instead_of_changing_meaning() -> None:
    points, normals, _triangles = cylinder_grid(AXES[0])
    weights = np.ones(len(points))
    domain = (-2.0, 5.0)
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(
            points,
            weights,
            normals,
            binding(points, weights, normals, domain=domain),
            domain=domain,
        )
    assert failure.value.code
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_explicit_support_cannot_collapse_during_large_anchor_rebasing() -> None:
    points, normals, _triangles = cylinder_grid(AXES[2])
    points += [0, 0, 1e9]
    weights = np.ones(len(points))
    domain = (-1e-9, 1e-9)
    with pytest.raises(CylinderFitFailure) as failure:
        _ = fit_selected_cylinder(
            points,
            weights,
            normals,
            binding(points, weights, normals, domain=domain),
            domain=domain,
        )
    assert failure.value.code
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def strict_ply(points: np.ndarray, normals: np.ndarray, triangles: np.ndarray) -> bytes:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Independently generated cylinder observations\n"
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


def imported_cylinder(axis: np.ndarray = AXES[2]) -> ScanWorkspace:
    points, normals, triangles = cylinder_grid(axis)
    return prepare_scan(
        io.BytesIO(strict_ply(points, normals, triangles)),
        "independent-cylinder.ply",
        ScanLimits(100_000, 1_000, 2_000, 10_000_000),
    )


def imported_recipe(
    workspace: ScanWorkspace, *, domain: tuple[float, float] | None = None
) -> Recipe:
    assert workspace.source_sha256 is not None and workspace.descriptor is not None
    source = Source(
        id="scan",
        label="Independent scan",
        operation="source",
        source_sha256=workspace.source_sha256,
        reference_sha256=workspace.reference_sha256,
        descriptor=workspace.descriptor,
    )
    selected = Selection(
        id="patch",
        label="Central cylinder patch",
        operation="selection",
        source="scan",
        ids=list(range(64, 160)),
    )
    fitted = SurfaceFit(
        id="cylinder",
        label="Selected cylinder",
        operation="fit",
        selections=["patch"],
        kind="cylinder",
        axial_domain=domain,
    )
    grown = Growth(
        id="grown",
        label="Axially extended patch",
        operation="growth",
        seed_fit="cylinder",
        distance=0.001,
        angle_degrees=10,
    )
    return Recipe(
        schema_version=2, nodes=[source, selected, fitted, grown], output="grown"
    )


def results(state: dict[str, object]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], state["results"])


@pytest.mark.parametrize("axis", [AXES[0], AXES[2]])
def test_imported_cylinder_growth_extends_automatic_support_and_recipe_replays(
    axis: np.ndarray,
) -> None:
    workspace = imported_cylinder(axis)
    graph = FeatureGraph(workspace, imported_recipe(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    fitted = results(state)["cylinder"]
    assert len(fitted["residuals"]) == 96
    assert results(state)["grown"]["ids"] == list(range(224))
    measured, _normals, _support = surface_distance(workspace.local, fitted)
    assert np.max(np.abs(measured)) < 1e-5
    assert np.ptp(workspace.data.weights) > 0
    encoded = json.dumps(state["recipe"], allow_nan=False)
    assert "initial_parameters" not in encoded and "initialization" not in encoded
    replay = FeatureGraph(workspace, Recipe.model_validate_json(encoded))
    reopened = replay.evaluate(str(replay.snapshot()["token"]))
    assert not reopened["errors"]
    assert results(reopened) == results(state)


def test_explicit_support_clips_imported_growth_to_its_declared_physical_span() -> None:
    workspace = imported_cylinder()
    axis = AXES[2] / np.linalg.norm(AXES[2])
    center_from_source_zero = float(ORIGIN[2] / axis[2])
    graph = FeatureGraph(
        workspace,
        imported_recipe(
            workspace,
            domain=(center_from_source_zero - 1.1, center_from_source_zero + 1.1),
        ),
    )
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    assert results(state)["grown"]["ids"] == list(range(64, 160))
    assert results(state)["cylinder"]["support_origin"] == "legacy-source-z0"


def test_failed_imported_cylinder_keeps_the_saved_selection_and_intent() -> None:
    workspace = imported_cylinder()
    recipe = imported_recipe(workspace)
    selected = cast(Selection, recipe.nodes[1]).model_copy(
        update={"ids": list(range(32))}
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[recipe.nodes[0], selected, recipe.nodes[2]],
        output="cylinder",
    )
    graph = FeatureGraph(workspace, recipe)
    before = graph.snapshot()
    with pytest.raises(CylinderFitFailure) as failure:
        _ = graph.evaluate(str(before["token"]))
    assert failure.value.code == "insufficient-coverage"
    assert graph.snapshot()["recipe"] == before["recipe"]
    assert graph.snapshot()["token"] == before["token"]
    assert graph.snapshot()["diagnostics"] == {"cylinder": failure.value.diagnostic}


def assert_cylindrical_shape(shape: TopoDS_Shape, fitted: dict[str, Any]) -> None:
    expected_origin, expected_axis, expected_radius = geometry(fitted)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == GeomAbs_Cylinder
    cylinder = adaptor.Cylinder()
    direction = np.array(cylinder.Axis().Direction().Coord())
    origin = np.array(cylinder.Axis().Location().Coord())
    assert np.linalg.norm(np.cross(direction, expected_axis)) < 1e-7
    assert np.linalg.norm(np.cross(origin - expected_origin, expected_axis)) < 1e-7
    assert cylinder.Radius() == pytest.approx(expected_radius, abs=1e-7)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = np.array(
        [
            adaptor.Value(u0 + (u1 - u0) * u, v0 + (v1 - v0) * v).Coord()
            for u, v in [(0.1, 0.3), (0.7, 0.4), (0.4, 0.9)]
        ]
    )
    np.testing.assert_allclose(residuals(samples, fitted), 0, atol=1e-7)
    explorer.Next()
    assert not explorer.More()


@pytest.mark.parametrize("axis", [AXES[0], AXES[2]])
def test_fitted_imported_cylinder_preserves_axis_and_radius_in_ocp_and_step(
    axis: np.ndarray, tmp_path: Path
) -> None:
    workspace = imported_cylinder(axis)
    graph = FeatureGraph(workspace, imported_recipe(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    fitted = results(state)["cylinder"]
    assert_cylindrical_shape(surface_patch(fitted, workspace.local), fitted)
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=str(state["token"]),
            target="cylinder",
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["source_sha256"] == workspace.source_sha256
        np.testing.assert_array_equal(metadata["transform_local_to_export"], np.eye(4))
        path = tmp_path / "independent-cylinder.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_cylindrical_shape(reader.OneShape(), fitted)


@pytest.mark.parametrize("axis", [AXES[0], AXES[2]])
def test_fixed_canonical_component_axis_is_retained_despite_a_poorer_fit(
    axis: np.ndarray,
) -> None:
    points, _normals, _triangles = cylinder_grid(axis, noise=0.005)
    direction, transverse, _second = transverse_frame(axis)
    fixed_origin = ORIGIN + 0.4 * transverse
    fixed_direction = -direction
    weights = np.linspace(0.2, 2, len(points))
    authored = {
        "point_display": fixed_origin.tolist(),
        "axis_display": fixed_direction.tolist(),
        "axis_is_directed": True,
    }
    initial = {
        "kind": "cylinder",
        "analytic": {
            "kind": "cylinder",
            "origin": fixed_origin.tolist(),
            "axis": fixed_direction.tolist(),
            "radius": 2.5,
            "slope": 0.0,
            "axial_domain": [-4.0, 4.0],
        },
        "support_origin": "selection",
        "anchor_reference": ORIGIN.tolist(),
    }
    fitted = fit_relationship_component(
        [SurfaceInput("side", "cylinder", points, weights, initial, axis="fixed")],
        {"fixed": AxisInput(authored, free=False)},
        [],
        [],
    )
    published = fitted["axes"]["fixed"]
    np.testing.assert_allclose(published["axis_display"], fixed_direction, atol=1e-14)
    np.testing.assert_allclose(published["point_display"], fixed_origin, atol=1e-14)
    side = fitted["surfaces"]["side"]
    offset = points - fixed_origin
    distances = np.linalg.norm(
        offset - (offset @ fixed_direction)[:, None] * fixed_direction, axis=1
    )
    assert side["analytic"]["radius"] == pytest.approx(
        np.average(distances, weights=weights), abs=1e-10
    )
    assert side["weighted_rms"] > 0.2
    assert "parameters" not in side and "parameters" not in published
    assert authored["point_display"] == fixed_origin.tolist()


def test_source_fit_axis_is_fixed_for_an_imported_downstream_cylinder() -> None:
    points, normals, triangles = cylinder_grid(AXES[0])
    _direction, transverse, _second = transverse_frame(AXES[0])
    second = points + 2 * transverse
    workspace = prepare_scan(
        io.BytesIO(
            strict_ply(
                np.vstack((points, second)),
                np.vstack((normals, normals)),
                np.vstack((triangles, triangles + len(points))),
            )
        ),
        "independent-offset-cylinders.ply",
        ScanLimits(200_000, 1_000, 2_000, 10_000_000),
    )
    base = imported_recipe(workspace)
    first_selection = cast(Selection, base.nodes[1]).model_copy(
        update={"ids": list(range(224))}
    )
    first_fit = cast(SurfaceFit, base.nodes[2])
    datum = AxisDefinition(
        id="fixed",
        label="Directed source-fit axis",
        operation="axis",
        source_fit="cylinder",
        direction_reversed=True,
    )
    target_selection = Selection(
        id="target",
        label="Offset target patch",
        operation="selection",
        source="scan",
        ids=list(range(224, 448)),
    )
    target_fit = SurfaceFit(
        id="bound",
        label="Cylinder with fixed upstream axis",
        operation="fit",
        selections=["target"],
        kind="cylinder",
        axis="fixed",
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[
            base.nodes[0],
            first_selection,
            first_fit,
            datum,
            target_selection,
            target_fit,
        ],
        output="bound",
    )
    graph = FeatureGraph(workspace, recipe)
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]))
    assert not state["errors"]
    assert state["recipe"] == before["recipe"]
    source_origin, source_axis, _radius = geometry(results(state)["cylinder"])
    np.testing.assert_allclose(
        results(state)["fixed"]["axis_display"], -source_axis, atol=1e-12
    )
    target_origin, target_axis, target_radius = geometry(results(state)["bound"])
    assert np.linalg.norm(np.cross(source_axis, target_axis)) < 1e-12
    assert np.linalg.norm(np.cross(target_origin - source_origin, source_axis)) < 1e-12
    offset = workspace.local[224:] - source_origin
    expected_distances = np.linalg.norm(
        offset - (offset @ source_axis)[:, None] * source_axis, axis=1
    )
    assert target_radius == pytest.approx(
        np.average(expected_distances, weights=workspace.data.weights[224:]), abs=1e-10
    )
    assert results(state)["bound"]["weighted_rms"] > 1


@pytest.mark.parametrize("partial", [False, True])
def test_shared_horizontal_cylinders_and_cap_recover_from_wrong_free_axis(
    partial: bool,
) -> None:
    from experiments.nozzle_coaxial import FitSelection, fit_group
    from experiments.selection_growth import connected_growth

    points, normals, triangles = cylinder_grid(AXES[0], partial=partial)
    axis, first, second = transverse_frame(AXES[0])
    height = (points - ORIGIN) @ axis
    radial = points - ORIGIN - height[:, None] * axis
    outer = ORIGIN + height[:, None] * axis + 1.6 * radial
    u, v = np.meshgrid(np.linspace(-1, 1, 5), np.linspace(-1, 1, 5))
    cap = ORIGIN + 4 * axis + u.ravel()[:, None] * first + v.ravel()[:, None] * second
    cap_triangles: list[tuple[int, int, int]] = []
    for row in range(4):
        for col in range(4):
            a = 5 * row + col
            cap_triangles.extend([(a, a + 1, a + 5), (a + 1, a + 6, a + 5)])
    count = len(points)
    workspace = prepare_scan(
        io.BytesIO(
            strict_ply(
                np.vstack((points, outer, cap)),
                np.vstack((normals, normals, np.tile(axis, (len(cap), 1)))),
                np.vstack(
                    (triangles, triangles + count, np.array(cap_triangles) + 2 * count)
                ),
            )
        ),
        "joint-cylinders-and-cap.ply",
        ScanLimits(200_000, 2_000, 4_000, 10_000_000),
    )
    fitted = fit_group(
        workspace,
        [
            FitSelection("inner", list(range(count)), "cylinder", None),
            FitSelection("outer", list(range(count, 2 * count)), "cylinder", None),
        ],
        [
            FitSelection(
                "cap", list(range(2 * count, 2 * count + len(cap))), "plane", None
            )
        ],
        axis_initial={
            "axis_display": [0, 0, -1],
            "point_display": [1e4, -1e4, 1e4],
            "direction_reversed": True,
        },
    )
    assert fitted["axis_is_directed"] is True
    np.testing.assert_allclose(fitted["axis_display"], -axis, atol=1e-5)
    for name, radius in (("inner", 2.5), ("outer", 4.0)):
        surface = fitted["surfaces"][name]
        anchor, direction, actual_radius = geometry(surface)
        assert actual_radius == pytest.approx(radius, abs=1e-5)
        assert np.linalg.norm(np.cross(anchor - ORIGIN, axis)) < 1e-5
        assert abs(direction @ axis) > 1 - 1e-10
        assert surface["weighted_rms"] < 1e-5
        assert surface["normal_sign"] == 1
    equation = np.asarray(fitted["surfaces"]["cap"]["plane_equation"])
    assert abs(equation[:3] @ axis) > 1 - 1e-10
    assert abs(float(equation[:3] @ (ORIGIN + 4 * axis) - equation[3])) < 1e-5
    grown = connected_growth(
        workspace.local,
        workspace.data.normals,
        workspace.data.triangles,
        workspace.data.weights,
        [0],
        [],
        fitted["surfaces"]["inner"],
        1e-4,
        5,
    )
    assert grown["ids"] == list(range(count))
