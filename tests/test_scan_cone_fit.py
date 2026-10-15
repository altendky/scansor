"""Independent selected-data cone recovery, finite sheets and source workflows."""

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
from OCP.GeomAbs import GeomAbs_Cone
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
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.fit_coordinates import CoordinateFrame
from experiments.fit_initialization import (
    InitializationBinding,
    RecoveryPolicy,
    initialization_binding,
)
from experiments.fit_solver import AttemptLimits, FitCancelled
from experiments.mesh_cone_fit import (
    CONE_POLICY_VERSION,
    ConeFitFailure,
    cone_residual_jacobian,
    fit_selected_cone,
)
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.ocp_geometry import surface_patch
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_growth import surface_distance

ORIGIN = np.array([80.0, -60.0, 40.0])
AXES = [np.array([1.0, 0, 0]), np.array([0.2, -0.7, 0.3])]


def frame(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction = axis / np.linalg.norm(axis)
    first = np.cross(direction, np.eye(3)[int(np.argmin(np.abs(direction)))])
    first /= np.linalg.norm(first)
    return direction, first, np.cross(direction, first)


def cone_grid(
    axis: np.ndarray = AXES[1],
    *,
    radius: float = 3.0,
    slope: float = 0.15,
    full: bool = False,
    noise: float = 0.0,
    heights: np.ndarray | None = None,
    angles: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    direction, first, second = frame(axis)
    if angles is None:
        angles = (
            np.linspace(0, 2 * np.pi, 32, endpoint=False)
            if full
            else np.linspace(-1.4, 1.6, 24)
        )
    levels = np.linspace(-1, 2, 5) if heights is None else heights
    theta, axial = np.meshgrid(angles, levels)
    radial = (
        np.cos(theta.ravel())[:, None] * first + np.sin(theta.ravel())[:, None] * second
    )
    points = (
        ORIGIN
        + axial.ravel()[:, None] * direction
        + (
            radius + slope * axial.ravel() + noise * np.sin(np.arange(theta.size) * 1.7)
        )[:, None]
        * radial
    )
    normals = (radial - slope * direction) / np.hypot(1, slope)
    triangles: list[tuple[int, int, int]] = []
    width = len(angles)
    for row in range(len(levels) - 1):
        for column in range(width if full else width - 1):
            a = row * width + column
            b = row * width + (column + 1) % width
            triangles.extend([(a, b, a + width), (b, b + width, a + width)])
    return points, normals, np.array(triangles, dtype=np.int32)


def binding(
    points: np.ndarray,
    weights: np.ndarray,
    normals: np.ndarray,
    *,
    domain: tuple[float, float] | None = None,
    policy: RecoveryPolicy | None = None,
) -> InitializationBinding:
    return initialization_binding(
        hashlib.sha256(points.astype("<f8").tobytes()).hexdigest(),
        hashlib.sha256(
            weights.astype("<f8").tobytes() + normals.astype("<f8").tobytes()
        ).hexdigest(),
        list(range(len(points))),
        {
            "family": "cone",
            "references": {},
            "constraints": {},
            "support": domain,
            "settings": {},
        },
        policy_version=CONE_POLICY_VERSION,
        policy=policy,
    )


def geometry(result: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, float, float]:
    value = result["analytic"]
    return (
        np.array(value["origin"]),
        np.array(value["axis"]),
        float(value["radius"]),
        float(value["slope"]),
    )


def independent_residual(points: np.ndarray, result: dict[str, Any]) -> np.ndarray:
    origin, axis, radius, slope = geometry(result)
    offset = points - origin
    axial = offset @ axis
    rho = np.linalg.norm(offset - axial[:, None] * axis, axis=1)
    return (rho - radius - slope * axial) / np.hypot(1, slope)


def independent_gradient(
    points: np.ndarray, weights: np.ndarray, result: dict[str, Any]
) -> float:
    origin, axis, radius, slope = geometry(result)
    _, first, second = frame(axis)
    coordinates = CoordinateFrame.from_observations(points, weights)
    anchor = origin + ((coordinates.origin - origin) @ axis) * axis
    radius = (radius + slope * ((anchor - origin) @ axis)) / coordinates.length
    xyz = (
        (points - anchor) @ np.column_stack((first, second, axis)) / coordinates.length
    )
    x, y, z = xyz.T
    rho = np.hypot(x, y)
    ux, uy = x / rho, y / rho
    scale = np.hypot(1, slope)
    residual = (rho - radius - slope * z) / scale
    jacobian = np.column_stack(
        (
            -ux / scale,
            -uy / scale,
            (-z * ux - slope * x) / scale,
            (-z * uy - slope * y) / scale,
            -np.ones(len(points)) / scale,
            -z / scale - residual * slope / scale**2,
        )
    )
    return float(np.max(np.abs(jacobian.T @ ((weights / weights.sum()) * residual))))


def assert_known_cone(
    result: dict[str, Any],
    axis: np.ndarray,
    *,
    slope: float,
    scale: float = 1.0,
    radius: float = 3.0,
    tolerance: float = 1e-9,
) -> None:
    origin, direction, actual_radius, actual_slope = geometry(result)
    expected_axis = axis / np.linalg.norm(axis)
    sign = 1 if direction @ expected_axis >= 0 else -1
    nearest = origin + ((scale * ORIGIN - origin) @ direction) * direction
    assert np.linalg.norm((nearest - scale * ORIGIN) / scale) < tolerance
    assert np.linalg.norm(np.cross(direction, expected_axis)) < tolerance
    assert actual_slope == pytest.approx(sign * slope, abs=tolerance)
    expected_radius = radius + slope * ((origin / scale - ORIGIN) @ expected_axis)
    assert actual_radius / scale == pytest.approx(expected_radius, abs=tolerance)
    assert result["kind"] == result["analytic"]["kind"] == "cone"
    assert "parameters" not in result


def assert_positive_sheet(points: np.ndarray, fitted: dict[str, Any]) -> None:
    origin, axis, radius, slope = geometry(fitted)
    bounds = np.array(fitted["analytic"]["axial_domain"])
    assert bounds.shape == (2,) and np.isfinite(bounds).all() and bounds[0] < bounds[1]
    assert np.all(radius + slope * bounds > 0)
    offset = points - origin
    axial = offset @ axis
    rho = np.linalg.norm(offset - axial[:, None] * axis, axis=1)
    projected = (axial + slope * (rho - radius)) / (1 + slope**2)
    assert np.all(rho > 0) and np.all(radius + slope * projected > 0)


@pytest.mark.parametrize("axis", AXES)
@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
@pytest.mark.parametrize("slope", [0.15, -0.2, 1e-7, 0.0])
def test_partial_cone_is_selected_data_derived_at_arbitrary_pose_and_scale(
    axis: np.ndarray, scale: float, slope: float
) -> None:
    points, normals, _ = cone_grid(axis, slope=slope)
    points *= scale
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, axis, slope=slope, scale=scale)
    assert fitted["weighted_rms"] / scale < 1e-9
    assert independent_gradient(points, weights, fitted) < 1e-9
    assert_positive_sheet(points, fitted)
    initialization = fitted["initialization"]
    assert (
        initialization["algorithm"]
        == CONE_POLICY_VERSION
        == "selected-cone-recovery-v2"
    )
    assert initialization["observations"] == len(points)
    assert initialization["recovery"]["selected_role"] == "fresh"
    assert len(initialization["recovery"]["attempts"]) <= 7
    _ = json.dumps(fitted, allow_nan=False)


@pytest.mark.parametrize("slope", [0.15, -0.2])
def test_full_azimuth_cone_uses_all_nonuniform_observations(slope: float) -> None:
    points, normals, _ = cone_grid(AXES[0], slope=slope, full=True, noise=0.005)
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, AXES[0], slope=slope, tolerance=0.005)
    residual = independent_residual(points, fitted)
    np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-12)
    assert fitted["weighted_rms"] == pytest.approx(
        np.sqrt(np.average(residual**2, weights=weights)), abs=1e-12
    )
    assert 0.003 < fitted["weighted_rms"] < 0.004
    assert independent_gradient(points, weights, fitted) < 1e-9
    assert_positive_sheet(points, fitted)


@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
def test_positive_apex_adjacent_patch_remains_a_valid_finite_cone(scale: float) -> None:
    points, normals, _ = cone_grid(
        radius=0.3, heights=np.array([-1.99, -1.9, -1.5, -0.5, 1.0])
    )
    points *= scale
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, AXES[1], slope=0.15, radius=0.3, scale=scale)
    assert fitted["weighted_rms"] / scale < 1e-9
    assert_positive_sheet(points, fitted)


@pytest.mark.parametrize(
    "defect",
    [
        "orthogonal",
        "distant",
        "small-radius",
        "large-radius",
        "wrong-taper",
        "nonfinite",
        "shape",
        "huge",
        "stale",
    ],
)
def test_wrong_optional_geometry_cannot_replace_fresh_cone_recovery(
    defect: str,
) -> None:
    points, normals, _ = cone_grid()
    weights = np.linspace(0.2, 2, len(points))
    request = binding(points, weights, normals)
    original = [a.copy() for a in (points, weights, normals)]
    baseline = fit_selected_cone(points, weights, normals, request)
    direction, transverse, _ = frame(AXES[1])
    hint: dict[str, Any] = {
        "origin": ORIGIN.tolist(),
        "axis": direction.tolist(),
        "radius": 3.0,
        "slope": 0.15,
    }
    if defect == "orthogonal":
        hint["axis"], hint["slope"] = transverse.tolist(), 0.0
    elif defect == "distant":
        hint["origin"] = [1e4, -1e4, 1e4]
    elif defect in {"small-radius", "large-radius"}:
        hint["radius"], hint["slope"] = (3e-6 if defect == "small-radius" else 3e6), 0.0
    elif defect == "wrong-taper":
        hint["slope"] = -0.15
    elif defect == "nonfinite":
        hint["slope"] = np.nan
    elif defect == "shape":
        hint["origin"] = [0, 0]
    elif defect == "huge":
        hint["origin"] = [1e100, -1e100, 1e100]
    fitted = fit_selected_cone(
        points,
        weights,
        normals,
        request,
        hint=hint,
        hint_binding_sha256="0" * 64 if defect == "stale" else None,
    )
    assert_known_cone(fitted, AXES[1], slope=0.15)
    actual = fitted["initialization"]["recovery"]["attempts"]
    expected = baseline["initialization"]["recovery"]["attempts"]
    assert [a for a in actual if a["role"] == "fresh"] == expected
    assert actual[-1]["role"] == "hint"
    for observed, before in zip((points, weights, normals), original, strict=True):
        np.testing.assert_array_equal(observed, before)
    _ = json.dumps(fitted, allow_nan=False)


@pytest.mark.parametrize("reversed_normals", [False, True])
def test_normal_directions_are_aid_only_and_reversal_preserves_cone_geometry(
    reversed_normals: bool,
) -> None:
    points, normals, _ = cone_grid()
    if reversed_normals:
        normals = -normals
    else:
        normals[:] = [0, 0, 1]
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, AXES[1], slope=0.15)
    if reversed_normals:
        assert fitted["normal_sign"] == -1


@pytest.mark.parametrize(
    "family", ["strip", "ring", "generators", "plane", "noisy-plane"]
)
def test_unsupported_position_families_do_not_publish_a_cone(family: str) -> None:
    direction, first, second = frame(AXES[1])
    angles = (
        np.linspace(-1e-7, 1e-7, 24)
        if family == "strip"
        else np.array([-0.5, 0.5])
        if family == "generators"
        else np.linspace(-1.4, 1.6, 24)
    )
    theta, height = np.meshgrid(
        angles, [0.5] if family == "ring" else np.linspace(-1, 2, 5)
    )
    radial = (
        np.cos(theta.ravel())[:, None] * first + np.sin(theta.ravel())[:, None] * second
    )
    points = (
        ORIGIN
        + height.ravel()[:, None] * direction
        + (3 + 0.15 * height.ravel())[:, None] * radial
    )
    normals = (radial - 0.15 * direction) / np.hypot(1, 0.15)
    if "plane" in family:
        x, y = np.meshgrid(np.linspace(-3, 3, 12), np.linspace(-2, 2, 11))
        points = ORIGIN + x.ravel()[:, None] * first + y.ravel()[:, None] * second
        normals = np.tile(direction, (len(points), 1))
        if family == "noisy-plane":
            points += 0.005 * np.sin(np.arange(len(points)) * 1.7)[:, None] * direction
    weights = np.linspace(0.2, 2, len(points))
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit_selected_cone(
            points, weights, normals, binding(points, weights, normals)
        )
    assert failure.value.code
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("during", [False, True])
def test_cone_cancellation_is_a_control_signal(during: bool) -> None:
    points, normals, _ = cone_grid(noise=0.005)
    weights = np.ones(len(points))
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= (8 if during else 1)

    with pytest.raises(FitCancelled):
        _ = fit_selected_cone(
            points,
            weights,
            normals,
            binding(points, weights, normals),
            cancelled=cancelled,
        )
    assert calls == (8 if during else 1)


def test_cone_attempts_retain_the_bound_callback_budget() -> None:
    points, normals, _ = cone_grid(noise=0.005)
    weights = np.ones(len(points))
    policy = RecoveryPolicy(
        max_fresh_attempts=1, attempt=AttemptLimits(max_evaluations=1)
    )
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit_selected_cone(
            points, weights, normals, binding(points, weights, normals, policy=policy)
        )
    recovery = failure.value.diagnostic["recovery"]
    executed = [a for a in recovery["attempts"] if a["evaluations"]]
    assert len(executed) == 1 and executed[0]["evaluations"] == 1
    assert executed[0]["outcome"] == "evaluation-limit"
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("axis_chart", [(0.2, -0.3), (-0.7, 0.4)])
@pytest.mark.parametrize(
    "radius,slope,levels",
    [(3.0, 0.15, (-1.0, 2.0)), (-1.0, 0.5, (8.0, 15.0)), (3.0, 0.0, (-1.0, 2.0))],
)
def test_analytic_cone_jacobian_matches_independent_finite_differences(
    axis_chart: tuple[float, float],
    radius: float,
    slope: float,
    levels: tuple[float, float],
) -> None:
    direction, first, second = frame(np.array([*axis_chart, 1.0]))
    theta, axial = np.meshgrid(np.linspace(-1.2, 1.7, 13), np.linspace(*levels, 4))
    radial = (
        np.cos(theta.ravel())[:, None] * first + np.sin(theta.ravel())[:, None] * second
    )
    points = (
        np.array([0.4, -0.6, 0.0])
        + axial.ravel()[:, None] * direction
        + (radius + slope * axial.ravel())[:, None] * radial
    )
    parameters = np.array([0.4, -0.6, *axis_chart, radius, slope])
    residual, jacobian = cone_residual_jacobian(points, parameters)
    np.testing.assert_allclose(residual, 0, atol=1e-14)
    numeric = np.empty_like(jacobian)
    for index in range(6):
        delta = np.zeros(6)
        delta[index] = 2e-6 * max(1, abs(parameters[index]))
        plus, _ = cone_residual_jacobian(points, parameters + delta)
        minus, _ = cone_residual_jacobian(points, parameters - delta)
        numeric[:, index] = (plus - minus) / (2 * delta[index])
    np.testing.assert_allclose(jacobian, numeric, rtol=2e-6, atol=2e-8)


def test_two_axial_levels_identify_cone_without_full_nuisance_rank() -> None:
    points, normals, _ = cone_grid(heights=np.array([-1.0, 2.0]))
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, AXES[1], slope=0.15)
    selected = fitted["initialization"]["recovery"]["selected"]
    initializer = fitted["initialization"]["initializers"][selected]
    assert initializer["center_rank"] == initializer["radial_rank"] == 2
    assert fitted["solver"]["rank"] == 6


@pytest.mark.parametrize(
    "defect",
    [
        "few",
        "coincident",
        "nonfinite",
        "zero-weight",
        "underflow",
        "zero-normal",
        "invalid-domain",
    ],
)
def test_cone_invalid_input_failures_are_typed_and_json_safe(defect: str) -> None:
    points, normals, _ = cone_grid()
    weights = np.ones(len(points))
    request = binding(points, weights, normals)
    domain = None
    if defect == "few":
        points, normals, weights = points[:6], normals[:6], weights[:6]
    elif defect == "coincident":
        points[:] = ORIGIN
    elif defect == "nonfinite":
        points[0, 0] = np.inf
    elif defect == "zero-weight":
        weights[0] = 0
    elif defect == "underflow":
        weights[0], weights[-1] = 1e-300, 1e300
    elif defect == "zero-normal":
        normals[0] = 0
    else:
        domain = (1.0, -1.0)
    with pytest.raises(ConeFitFailure) as failure:
        _ = fit_selected_cone(points, weights, normals, request, domain=domain)
    expected = {
        "few": "insufficient-observations",
        "zero-normal": "invalid-normals",
        "invalid-domain": "invalid-support",
    }
    assert failure.value.code == expected.get(defect, "invalid-input")
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_finite_positive_support_allows_negative_extrapolated_source_zero_radius() -> (
    None
):
    points, normals, _ = cone_grid()
    weights = np.linspace(0.2, 2, len(points))
    direction = AXES[1] / np.linalg.norm(AXES[1])
    from_zero = float(ORIGIN[2] / direction[2])
    assert 3 - 0.15 * from_zero < 0
    domain = (from_zero - 1, from_zero + 2)
    fitted = fit_selected_cone(
        points,
        weights,
        normals,
        binding(points, weights, normals, domain=domain),
        domain=domain,
    )
    assert_known_cone(fitted, AXES[1], slope=0.15)
    assert fitted["support_origin"] == "legacy-source-z0"
    assert fitted["source_axial_domain"] == list(domain)
    origin, axis, _radius, _slope = geometry(fitted)
    actual = origin + np.array(fitted["analytic"]["axial_domain"])[:, None] * axis
    expected = ORIGIN + np.array([-1.0, 2.0])[:, None] * direction
    np.testing.assert_allclose(
        actual[np.argsort(actual @ direction)], expected, atol=1e-9
    )
    assert_positive_sheet(points, fitted)


def strict_ply(points: np.ndarray, normals: np.ndarray, triangles: np.ndarray) -> bytes:
    header = (
        "ply\nformat binary_little_endian 1.0\ncomment Independent finite cone\n"
        + f"element vertex {len(points)}\n"
        + "property float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        + f"element face {len(triangles)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    return (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(points, normals, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *triangle) for triangle in triangles)
    )


def imported_cone(axis: np.ndarray = AXES[1]) -> ScanWorkspace:
    points, normals, triangles = cone_grid(axis)
    return prepare_scan(
        io.BytesIO(strict_ply(points, normals, triangles)),
        "independent-cone.ply",
        ScanLimits(100_000, 1_000, 2_000, 10_000_000),
    )


def recipe_for(
    workspace: ScanWorkspace, *, domain: tuple[float, float] | None = None
) -> Recipe:
    assert workspace.source_sha256 is not None and workspace.descriptor is not None
    return Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Independent cone source",
                operation="source",
                source_sha256=workspace.source_sha256,
                reference_sha256=workspace.reference_sha256,
                descriptor=workspace.descriptor,
            ),
            Selection(
                id="patch",
                label="Central cone patch",
                operation="selection",
                source="scan",
                ids=list(range(24, 96)),
            ),
            SurfaceFit(
                id="cone",
                label="Selected cone",
                operation="fit",
                selections=["patch"],
                kind="cone",
                axial_domain=domain,
            ),
            Growth(
                id="grown",
                label="Axial cone growth",
                operation="growth",
                seed_fit="cone",
                distance=0.001,
                angle_degrees=10,
            ),
        ],
        output="grown",
    )


def results(state: dict[str, object]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], state["results"])


@pytest.mark.parametrize("axis", AXES)
def test_imported_cone_fits_grows_and_replays_without_example_seed(
    axis: np.ndarray,
) -> None:
    workspace = imported_cone(axis)
    assert not hasattr(workspace.data, "selection")
    graph = FeatureGraph(workspace, recipe_for(workspace))
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]))
    assert not state["errors"] and not state["diagnostics"]
    assert state["recipe"] == before["recipe"]
    fitted = results(state)["cone"]
    assert_known_cone(fitted, axis, slope=0.15, tolerance=1e-4)
    assert len(fitted["residuals"]) == 72
    assert results(state)["grown"]["ids"] == list(range(120))
    assert np.ptp(workspace.data.weights) > 0
    assert_positive_sheet(workspace.local, fitted)
    distance, normals, _inside = surface_distance(workspace.local, fitted)
    assert np.max(np.abs(distance)) < 1e-5
    np.testing.assert_allclose(normals, workspace.data.normals, atol=1e-5)
    encoded = json.dumps(state["recipe"], allow_nan=False)
    assert "initial_parameters" not in encoded and "initialization" not in encoded
    replay = FeatureGraph(workspace, Recipe.model_validate_json(encoded))
    reopened = replay.evaluate(str(replay.snapshot()["token"]))
    assert not reopened["errors"]
    assert results(reopened) == results(state)


def test_imported_cone_explicit_support_clips_growth_without_changing_selection() -> (
    None
):
    workspace = imported_cone()
    direction = AXES[1] / np.linalg.norm(AXES[1])
    from_zero = float(ORIGIN[2] / direction[2])
    recipe = recipe_for(workspace, domain=(from_zero - 0.4, from_zero + 0.7))
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    # Growth preserves the authored seed, including already painted rows beyond
    # explicit support. The bounds constrain additions rather than erase intent.
    assert results(state)["grown"]["ids"] == list(range(24, 96))
    assert results(state)["grown"]["added_ids"] == []
    assert len(results(state)["cone"]["residuals"]) == 72
    assert cast(Selection, Recipe.model_validate(state["recipe"]).nodes[1]).ids == list(
        range(24, 96)
    )
    assert_positive_sheet(workspace.local, results(state)["cone"])
    _distance, _normal, supported = surface_distance(
        workspace.local, results(state)["cone"]
    )
    assert np.flatnonzero(supported).tolist() == list(range(24, 72))


def assert_conical_shape(shape: TopoDS_Shape, fitted: dict[str, Any]) -> None:
    _origin, axis, _radius, slope = geometry(fitted)
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == GeomAbs_Cone
    cone = adaptor.Cone()
    assert np.linalg.norm(np.cross(cone.Axis().Direction().Coord(), axis)) < 1e-7
    assert abs(np.tan(cone.SemiAngle())) == pytest.approx(abs(slope), abs=1e-7)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = np.array(
        [
            adaptor.Value(u0 + (u1 - u0) * u, v0 + (v1 - v0) * v).Coord()
            for u, v in [(0.1, 0.3), (0.7, 0.4), (0.4, 0.9)]
        ]
    )
    np.testing.assert_allclose(independent_residual(samples, fitted), 0, atol=1e-7)
    explorer.Next()
    assert not explorer.More()


@pytest.mark.parametrize("axis", AXES)
def test_imported_selected_cone_ocp_and_step_preserve_world_geometry(
    axis: np.ndarray, tmp_path: Path
) -> None:
    workspace = imported_cone(axis)
    graph = FeatureGraph(workspace, recipe_for(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    fitted = results(state)["cone"]
    assert_conical_shape(surface_patch(fitted, workspace.local), fitted)
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=str(state["token"]),
            target="cone",
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["source_sha256"] == workspace.source_sha256
        np.testing.assert_array_equal(metadata["transform_local_to_export"], np.eye(4))
        path = tmp_path / "independent-cone.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_conical_shape(reader.OneShape(), fitted)


def test_noisy_oblique_cone_refines_a_perturbed_normal_axis_start() -> None:
    points, normals, _ = cone_grid(noise=0.005)
    index = np.arange(len(points))
    normals += 0.001 * np.column_stack(
        (np.sin(index), np.cos(0.8 * index), np.sin(0.6 * index))
    )
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    weights = np.linspace(0.2, 2, len(points))
    fitted = fit_selected_cone(
        points, weights, normals, binding(points, weights, normals)
    )
    assert_known_cone(fitted, AXES[1], slope=0.15, tolerance=0.005)
    assert fitted["solver"]["iterations"] > 0
    assert fitted["weighted_rms"] == pytest.approx(0.00350545, abs=1e-7)
    assert independent_gradient(points, weights, fitted) < 1e-9
    assert_positive_sheet(points, fitted)


def test_failed_imported_cone_preserves_saved_source_membership_and_intent() -> None:
    workspace = imported_cone()
    recipe = recipe_for(workspace)
    selected = cast(Selection, recipe.nodes[1]).model_copy(
        update={"ids": list(range(24))}
    )
    graph = FeatureGraph(
        workspace,
        Recipe(
            schema_version=2,
            nodes=[recipe.nodes[0], selected, recipe.nodes[2]],
            output="cone",
        ),
    )
    before = graph.snapshot()
    with pytest.raises(ConeFitFailure) as failure:
        _ = graph.evaluate(str(before["token"]))
    assert failure.value.code == "search-exhausted"
    assert failure.value.diagnostic["recovery"]["selected"] is None
    assert graph.snapshot()["recipe"] == before["recipe"]
    assert graph.snapshot()["token"] == before["token"]


def paired_workspace(*, offset: bool) -> tuple[ScanWorkspace, np.ndarray, np.ndarray]:
    if offset:
        points, normals, triangles = cone_grid()
        _axis, transverse, _second = frame(AXES[1])
        rotation, translation = np.eye(3), 2 * transverse
    else:
        angles = np.linspace(-1.4, 1.6, 24)
        points, normals, triangles = cone_grid(
            heights=np.array([-1.0, -0.6, 0.2, 1.1, 2.0]),
            angles=angles + 0.04 * np.sin(angles) + 0.02 * np.cos(2 * angles),
        )
        rotation = np.array([[0.0, 0, -1], [0, 1, 0], [1, 0, 0]])
        translation = np.array([20.0, 30.0, -10.0])
    workspace = prepare_scan(
        io.BytesIO(
            strict_ply(
                np.vstack((points, points @ rotation + translation)),
                np.vstack((normals, normals @ rotation)),
                np.vstack((triangles, triangles + len(points))),
            )
        ),
        "two-independent-cones.ply",
        ScanLimits(200_000, 1_000, 2_000, 10_000_000),
    )
    return workspace, rotation, translation


def test_fixed_source_fit_axis_cannot_move_to_fit_an_offset_imported_cone() -> None:
    workspace, _rotation, _translation = paired_workspace(offset=True)
    recipe = recipe_for(workspace)
    selected = cast(Selection, recipe.nodes[1]).model_copy(
        update={"ids": list(range(120))}
    )
    datum = AxisDefinition(
        id="fixed",
        label="Reversed upstream cone axis",
        operation="axis",
        source_fit="cone",
        direction_reversed=True,
    )
    target = Selection(
        id="target",
        label="Offset cone observations",
        operation="selection",
        source="scan",
        ids=list(range(120, 240)),
    )
    bound = SurfaceFit(
        id="bound",
        label="Cone on fixed axis",
        operation="fit",
        selections=["target"],
        kind="cone",
        axis="fixed",
    )
    graph = FeatureGraph(
        workspace,
        Recipe(
            schema_version=2,
            nodes=[recipe.nodes[0], selected, recipe.nodes[2], datum, target, bound],
            output="bound",
        ),
    )
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]))
    assert not state["errors"]
    assert state["recipe"] == before["recipe"]
    source_origin, source_axis, _radius, _slope = geometry(results(state)["cone"])
    np.testing.assert_allclose(
        results(state)["fixed"]["axis_display"], -source_axis, atol=1e-12
    )
    fitted = results(state)["bound"]
    origin, axis, radius, slope = geometry(fitted)
    assert np.linalg.norm(np.cross(axis, source_axis)) < 1e-12
    assert np.linalg.norm(np.cross(origin - source_origin, source_axis)) < 1e-10
    assert fitted["weighted_rms"] > 0.1
    points, weights = workspace.local[120:], workspace.data.weights[120:]
    assert_positive_sheet(points, fitted)
    length = CoordinateFrame.from_observations(points, weights).length
    relative = (points - origin) / length
    axial = relative @ axis
    rho = np.linalg.norm(relative - axial[:, None] * axis, axis=1)
    scale = np.hypot(1, slope)
    residual = (rho - radius / length - slope * axial) / scale
    jacobian = np.column_stack(
        (-np.ones(len(points)) / scale, -axial / scale - residual * slope / scale**2)
    )
    assert np.max(np.abs(jacobian.T @ ((weights / weights.sum()) * residual))) < 1e-8
    assert "parameters" not in fitted


@pytest.mark.parametrize("explicit_support", [False, True])
def test_reconciled_cone_reuse_refits_rotated_source_and_replays_saved_actions(
    explicit_support: bool, tmp_path: Path
) -> None:
    workspace, rotation, translation = paired_workspace(offset=False)
    from_zero = float(ORIGIN[2] / frame(AXES[1])[0][2])
    initial = recipe_for(
        workspace,
        domain=(from_zero - 1, from_zero + 2) if explicit_support else None,
    )
    source_selection = cast(Selection, initial.nodes[1]).model_copy(
        update={"ids": list(range(120))}
    )
    target_selection = Selection(
        id="target",
        label="Rotated occurrence",
        operation="selection",
        source="scan",
        ids=list(range(120, 240)),
    )
    recipe = Recipe(
        schema_version=2,
        nodes=[initial.nodes[0], source_selection, initial.nodes[2], target_selection],
        output="cone",
    )
    plan = reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges(
            label="Cone reuse",
            fits=["cone"],
            reference_selection="patch",
            target_selections=["target"],
            tangent_margin=0.01,
            normal_margin=0.01,
            normal_angle_degrees=10,
        ),
        "independent-cone-transfer",
        create=True,
    )
    clone = next(
        node for node in plan.recipe.nodes if node.managed_key == "fit/target/cone"
    )
    assert isinstance(clone, SurfaceFit) and clone.kind == "cone"
    assert clone.axial_domain == (
        (from_zero - 1, from_zero + 2) if explicit_support else None
    )
    reused = next(
        node for node in plan.recipe.nodes if node.operation == "reuse_selection"
    )
    graph = FeatureGraph(workspace, plan.recipe)
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]), target=clone.id)
    assert not state["errors"]
    assert state["recipe"] == before["recipe"]
    fitted = results(state)[clone.id]
    assert fitted["ids"] == list(range(120, 240))
    assert fitted["support_origin"] == (
        "reference-plane" if explicit_support else "selection"
    )
    assert fitted["weighted_rms"] < 1e-5
    assert "parameters" not in fitted
    assert results(state)[reused.id]["fit_initial"] is None
    assert results(state)[reused.id]["fit_geometry"]["kind"] == "cone"
    source_origin, source_axis, source_radius, source_slope = geometry(
        results(state)["cone"]
    )
    origin, axis, radius, slope = geometry(fitted)
    expected_origin, expected_axis = (
        source_origin @ rotation + translation,
        source_axis @ rotation,
    )
    if explicit_support:
        source_bounds = np.asarray(results(state)["cone"]["analytic"]["axial_domain"])
        target_bounds = np.asarray(fitted["analytic"]["axial_domain"])
        np.testing.assert_allclose(
            origin + target_bounds[:, None] * axis,
            (source_origin + source_bounds[:, None] * source_axis) @ rotation
            + translation,
            atol=1e-5,
        )
    sign = 1 if axis @ expected_axis >= 0 else -1
    assert np.linalg.norm(np.cross(axis, expected_axis)) < 1e-5
    assert np.linalg.norm(np.cross(origin - expected_origin, expected_axis)) < 1e-5
    assert slope == pytest.approx(sign * source_slope, abs=1e-5)
    expected_radius = source_radius + source_slope * (
        (origin - expected_origin) @ expected_axis
    )
    assert radius == pytest.approx(expected_radius, abs=1e-5)
    assert_positive_sheet(workspace.local[120:], fitted)
    encoded = json.dumps(state["recipe"], allow_nan=False)
    replay = FeatureGraph(workspace, Recipe.model_validate_json(encoded))
    reopened = replay.evaluate(str(replay.snapshot()["token"]), target=clone.id)
    assert not reopened["errors"]
    assert results(reopened) == results(state)
    assert_conical_shape(surface_patch(fitted, workspace.local), fitted)
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=str(state["token"]),
            target=clone.id,
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["source_sha256"] == workspace.source_sha256
        path = tmp_path / "reused-cone.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_conical_shape(reader.OneShape(), fitted)


@pytest.mark.parametrize(
    "shift,reverse", [(30.0, False), (-30.0, False), (-30.0, True)]
)
def test_equivalent_rebased_hint_preserves_fresh_recovery_and_authored_support(
    shift: float, reverse: bool
) -> None:
    points, normals, _ = cone_grid()
    weights = np.linspace(0.2, 2, len(points))
    direction = frame(AXES[1])[0]
    from_zero = float(ORIGIN[2] / direction[2])
    domain = (from_zero - 1, from_zero + 2)
    request = binding(points, weights, normals, domain=domain)
    baseline = fit_selected_cone(points, weights, normals, request, domain=domain)
    hint = {
        "origin": (ORIGIN + shift * direction).tolist(),
        "axis": (-direction if reverse else direction).tolist(),
        "radius": 3 + 0.15 * shift,
        "slope": -0.15 if reverse else 0.15,
    }
    fitted = fit_selected_cone(
        points, weights, normals, request, hint=hint, domain=domain
    )
    assert_known_cone(fitted, AXES[1], slope=0.15)
    assert fitted["support_origin"] == "legacy-source-z0"
    assert fitted["source_axial_domain"] == list(domain)
    assert fitted["analytic"] == baseline["analytic"]
    assert fitted["weighted_rms"] == baseline["weighted_rms"]
    attempts = fitted["initialization"]["recovery"]["attempts"]
    assert [a for a in attempts if a["role"] == "fresh"] == baseline["initialization"][
        "recovery"
    ]["attempts"]
    assert attempts[-1]["role"] == "hint"
    assert_positive_sheet(points, fitted)
    _ = json.dumps(fitted, allow_nan=False)
