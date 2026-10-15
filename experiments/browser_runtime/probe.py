"""The same actual Scansor fitting/graph/CAD probe in native Python and Pyodide."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import struct
import sys
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pydantic
from numpy.typing import NDArray

from experiments.feature_graph import (
    AxisDefinition,
    FeatureGraph,
    Growth,
    PointDefinition,
    Recipe,
    Selection,
    SurfaceFit,
)
from experiments.fit_initialization import initialization_binding
from experiments.mesh_cone_plane_fit import (
    cone_plane_residual_jacobian,
    fit_cone_plane,
)
from experiments.mesh_cylinder_fit import CYLINDER_POLICY_VERSION, fit_selected_cylinder
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_project import AcceptedScan, ScanProject
from experiments.scan_workspace import ScanLimits
from experiments.selection_growth import surface_distance
from experiments.surface_primitives import (
    primitive,
    rebase_revolution,
    reverse_revolution,
    transform_revolution,
)

EXAMPLE = "examples/nozzle-bayonette-simplified"
ATOL = 1e-10
RTOL = 1e-10


def create_fixture(path: Path) -> None:
    """Freeze native-generated float64 trig/noise once for both interpreters."""
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    theta, z = np.meshgrid(
        np.linspace(0, 1, 48) ** 1.4 * 1.6 * np.pi, np.linspace(-1, 1, 9)
    )
    cone = (
        np.array([0.3, -0.4, 0.0])
        + z.ravel()[:, None] * axis
        + (3.2 + 0.08 * z.ravel())[:, None]
        * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v)
    )
    x, y = np.meshgrid(np.linspace(-3, 3, 8), np.linspace(-3, 3, 8))
    plane = 4.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    rng = np.random.default_rng(123456)
    cone += rng.normal(0, 0.001, cone.shape)
    plane += rng.normal(0, 0.001, plane.shape)
    # Independent poses and nonuniform cells, frozen before either interpreter
    # admits them as float32 source PLY. No fixture frame or numerical seed.
    px, py = np.meshgrid([-2.0, -0.4, 0.8, 2.5], [-1.5, 0.3, 2.0])
    normals = np.asarray([[1.0, 0, 0], [2.0, -3, 5], [2.0, -3, 5]])
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    scales = np.asarray([1.0, 1e-6, 1e6])
    imported_planes: list[NDArray[np.float64]] = []
    for normal, scale in zip(normals, scales, strict=True):
        reference = np.eye(3)[int(np.argmin(np.abs(normal)))]
        u = np.cross(reference, normal)
        u /= np.linalg.norm(u)
        v = np.cross(normal, u)
        sample = (
            [6.0, -3.0, 9.0]
            + px.ravel()[:, None] * u
            + py.ravel()[:, None] * v
            + 0.001 * np.sin(np.arange(px.size))[:, None] * normal
        )
        imported_planes.append(scale * sample)
    azimuth, elevation = np.meshgrid(
        [-1.2, -0.65, -0.2, 0.2, 0.7, 1.25, 1.8],
        [-0.8, -0.35, 0.1, 0.5, 0.9],
    )
    directions = np.column_stack(
        [
            np.cos(elevation.ravel()) * np.cos(azimuth.ravel()),
            np.cos(elevation.ravel()) * np.sin(azimuth.ravel()),
            np.sin(elevation.ravel()),
        ]
    )
    imported_spheres: list[NDArray[np.float64]] = []
    sphere_normals: list[NDArray[np.float64]] = []
    sphere_centers: list[NDArray[np.float64]] = []
    sphere_radii: list[float] = []
    for index, scale in enumerate(scales):
        normal = normals[index]
        reference = np.eye(3)[int(np.argmin(np.abs(normal)))]
        u = np.cross(reference, normal)
        u /= np.linalg.norm(u)
        rotation = np.column_stack([u, np.cross(normal, u), normal])
        posed = directions @ rotation.T
        center = scale * np.array([3.0 + index, -4.0, 2.0 - index])
        radius = 3.0 * scale
        radial_noise = 0.001 * scale * np.sin(np.arange(len(posed)) * 1.7)
        imported_spheres.append(center + (radius + radial_noise[:, None]) * posed)
        sphere_normals.append(posed)
        sphere_centers.append(center)
        sphere_radii.append(radius)
    cylinder_axes = np.asarray(
        [[1.0, 0, 0], [1.0, 0.2, 1e-8], [0.3, -0.5, 1], [1.0, 0, 0]], dtype=np.float64
    )
    cylinder_axes /= np.linalg.norm(cylinder_axes, axis=1)[:, None]
    cylinder_angles, cylinder_heights = np.meshgrid(
        [0.0, 0.3, 0.8, 1.4, 1.9, 2.3, 2.9, 3.5, 4.0, 4.4, 5.0, 5.7],
        [-3.0, -1.3, 0.0, 1.2, 3.0],
    )
    imported_cylinders: list[NDArray[np.float64]] = []
    cylinder_normals: list[NDArray[np.float64]] = []
    cylinder_origins: list[NDArray[np.float64]] = []
    cylinder_scales = np.r_[scales, 1.0]
    for index, (axis, scale) in enumerate(
        zip(cylinder_axes, cylinder_scales, strict=True)
    ):
        first = np.cross(axis, np.eye(3)[int(np.argmin(np.abs(axis)))])
        first /= np.linalg.norm(first)
        second = np.cross(axis, first)
        radial = np.asarray(
            np.cos(cylinder_angles.ravel())[:, None] * first
            + np.sin(cylinder_angles.ravel())[:, None] * second,
            dtype=np.float64,
        )
        origin = scale * np.array([3.0 + index, -4.0, 5.0 - index])
        long_cylinder = index == 3
        height_scale = 1e5 if long_cylinder else 1.0
        radius = scale * (
            2.5
            + (0.0 if long_cylinder else 0.001) * np.sin(np.arange(len(radial)) * 1.7)
        )
        imported_cylinders.append(
            origin
            + scale * height_scale * cylinder_heights.ravel()[:, None] * axis
            + radius[:, None] * radial
        )
        cylinder_normals.append(radial)
        cylinder_origins.append(origin)
    np.savez(
        path,
        cone=cone,
        plane=plane,
        cw=np.linspace(0.1, 2, len(cone)),
        pw=np.linspace(0.2, 1, len(plane)),
        initial=np.array([0.0, 0.0, 0.0, 0.0, 3.0, 4.0, 0.0]),
        imported_planes=np.asarray(imported_planes),
        imported_plane_normals=normals,
        imported_plane_scales=scales,
        imported_spheres=np.asarray(imported_spheres),
        imported_sphere_normals=np.asarray(sphere_normals),
        imported_sphere_centers=np.asarray(sphere_centers),
        imported_sphere_radii=np.asarray(sphere_radii),
        imported_sphere_scales=scales,
        imported_cylinders=np.asarray(imported_cylinders),
        imported_cylinder_normals=np.asarray(cylinder_normals),
        imported_cylinder_axes=cylinder_axes,
        imported_cylinder_origins=np.asarray(cylinder_origins),
        imported_cylinder_scales=cylinder_scales,
    )


def imported_plane_probe(
    points: NDArray[np.float64], normal: NDArray[np.float64], scale: float
) -> dict[str, Any]:
    """Exercise actual source admission, standalone fit, growth and recipe replay."""
    faces = [
        face
        for row in range(2)
        for column in range(3)
        for i in [4 * row + column]
        for face in ((i, i + 1, i + 4), (i + 1, i + 5, i + 4))
    ]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        + f"element vertex {len(points)}\n"
        + "property float x\nproperty float y\nproperty float z\n"
        + "property float nx\nproperty float ny\nproperty float nz\n"
        + f"element face {len(faces)}\n"
        + "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    original = (
        header
        + b"".join(struct.pack("<6f", *point, *normal) for point in points)
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )
    limits = ScanLimits(100_000, 100, 100, 10_000_000)

    def open_source(filename: str) -> AcceptedScan:
        project = ScanProject()
        empty = project.accepted
        candidate = project.prepare_add(
            io.BytesIO(original),
            filename,
            limits,
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
        return project.commit(candidate)

    accepted = open_source("independent-plane.ply")
    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    recipe.nodes.extend(
        [
            Selection(
                id="patch",
                label="Patch",
                operation="selection",
                source="scan",
                ids=list(range(8)),
            ),
            SurfaceFit(
                id="plane",
                label="Plane",
                operation="fit",
                selections=["patch"],
                kind="plane",
            ),
            Growth(
                id="grown",
                label="Grown",
                operation="growth",
                seed_fit="plane",
                distance=0.02 * scale,
                angle_degrees=10,
            ),
        ]
    )
    recipe = recipe.model_copy(update={"output": "grown"})
    graph = FeatureGraph(accepted.workspace, recipe)
    state = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    assert not state["errors"] and not state["diagnostics"], state
    assert set(state["states"].values()) == {"ready"}
    fitted = state["results"]["plane"]
    assert fitted["initialization"]["orientation"] == "selected-normals"
    assert np.dot(fitted["parameters"][:3], normal) > 1 - 1e-5
    assert fitted["weighted_rms"] / scale < 0.005
    assert state["results"]["grown"]["ids"] == list(range(len(points)))
    reopened = open_source("renamed-identical-plane.ply")
    replay = FeatureGraph(
        reopened.workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    replayed = cast(dict[str, Any], replay.evaluate(str(replay.snapshot()["token"])))
    for key in ("recipe", "token", "states", "errors", "diagnostics", "results"):
        compare_values(replayed[key], state[key], f"plane.replay.{key}")
    return {
        "comparison_length_scale": scale,
        **{
            key: state[key]
            for key in ("recipe", "token", "states", "errors", "diagnostics", "results")
        },
    }


def imported_sphere_probe(
    points: NDArray[np.float64],
    normals: NDArray[np.float64],
    center: NDArray[np.float64],
    radius: float,
    scale: float,
) -> dict[str, Any]:
    """Frozen independent curved patches through admission, recovery and replay."""
    faces = [
        face
        for row in range(4)
        for column in range(6)
        for i in [7 * row + column]
        for face in ((i, i + 1, i + 7), (i + 1, i + 8, i + 7))
    ]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        + f"element vertex {len(points)}\n"
        + "property float x\nproperty float y\nproperty float z\n"
        + "property float nx\nproperty float ny\nproperty float nz\n"
        + f"element face {len(faces)}\n"
        + "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    original = (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(points, normals, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )
    limits = ScanLimits(100_000, 100, 100, 10_000_000)

    def open_source(filename: str) -> AcceptedScan:
        project = ScanProject()
        empty = project.accepted
        candidate = project.prepare_add(
            io.BytesIO(original),
            filename,
            limits,
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
        return project.commit(candidate)

    accepted = open_source("independent-sphere.ply")
    source_only = accepted.graph.evaluate(str(accepted.graph.snapshot()["token"]))
    assert source_only["states"] == {"scan": "ready"}
    report: dict[str, Any] = {"comparison_length_scale": scale}
    for shared_center in (False, True):
        recipe = Recipe.model_validate(source_only["recipe"])
        recipe.nodes.append(
            Selection(
                id="patch",
                label="Curved patch",
                operation="selection",
                source="scan",
                ids=list(range(21)),
            )
        )
        if shared_center:
            # An authored free point remains action intent. It is a numerical
            # hint for the connected solve, never a fixed center or source fact.
            recipe.nodes.append(
                PointDefinition(
                    id="center",
                    label="Free center",
                    operation="point",
                    initial_coordinates=(1e100 * scale, -2e100 * scale, 3e100 * scale),
                )
            )
        recipe.nodes.append(
            SurfaceFit(
                id="sphere",
                label="Sphere",
                operation="fit",
                selections=["patch"],
                kind="sphere",
                point="center" if shared_center else None,
            )
        )
        if not shared_center:
            recipe.nodes.append(
                Growth(
                    id="grown",
                    label="Grown sphere patch",
                    operation="growth",
                    seed_fit="sphere",
                    distance=0.02 * scale,
                    angle_degrees=10,
                )
            )
        recipe = recipe.model_copy(
            update={"output": "sphere" if shared_center else "grown"}
        )
        graph = FeatureGraph(accepted.workspace, recipe)
        state = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
        assert not state["errors"] and not state["diagnostics"], state
        assert set(state["states"].values()) == {"ready"}
        fitted = state["results"]["sphere"]
        np.testing.assert_allclose(
            np.asarray(fitted["parameters"][:3]) / scale,
            center / scale,
            rtol=0,
            atol=0.01,
        )
        assert abs(fitted["parameters"][3] - radius) / scale < 0.01
        assert fitted["weighted_rms"] / scale < 0.005
        if not shared_center:
            assert state["results"]["grown"]["ids"] == list(range(len(points)))
        if shared_center:
            recovery = fitted["initialization"]["recovery"]
            assert recovery["selected_role"] == "fresh"
            assert any(attempt["role"] == "hint" for attempt in recovery["attempts"])
            np.testing.assert_allclose(
                state["results"]["center"]["point_display"],
                fitted["parameters"][:3],
                rtol=0,
                atol=1e-12 * scale,
            )
        reopened = open_source("renamed-identical-sphere.ply")
        replay = FeatureGraph(
            reopened.workspace,
            Recipe.model_validate_json(recipe.model_dump_json()),
        )
        replayed = cast(
            dict[str, Any], replay.evaluate(str(replay.snapshot()["token"]))
        )
        for key in ("recipe", "token", "states", "errors", "diagnostics", "results"):
            compare_values(replayed[key], state[key], f"sphere.replay.{key}")
        report["shared_center" if shared_center else "standalone"] = {
            key: state[key]
            for key in ("recipe", "token", "states", "errors", "diagnostics", "results")
        }
    return report


def imported_cylinder_probe(
    points: NDArray[np.float64],
    normals: NDArray[np.float64],
    origin: NDArray[np.float64],
    axis: NDArray[np.float64],
    scale: float,
) -> dict[str, Any]:
    """Actual arbitrary-axis source admission, recovery, growth and fixed-axis fit."""
    faces = [
        face
        for row in range(4)
        for column in range(12)
        for a, b in [(12 * row + column, 12 * row + (column + 1) % 12)]
        for face in ((a, b, a + 12), (b, b + 12, a + 12))
    ]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        + f"element vertex {len(points)}\n"
        + "property float x\nproperty float y\nproperty float z\n"
        + "property float nx\nproperty float ny\nproperty float nz\n"
        + f"element face {len(faces)}\n"
        + "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    original = (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(points, normals, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *face) for face in faces)
    )

    def open_source(filename: str) -> AcceptedScan:
        project = ScanProject()
        empty = project.accepted
        candidate = project.prepare_add(
            io.BytesIO(original),
            filename,
            ScanLimits(100_000, 100, 100, 10_000_000),
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
        return project.commit(candidate)

    accepted = open_source("independent-cylinder.ply")
    np.testing.assert_array_equal(
        accepted.workspace.local, points.astype("<f4").astype("<f8")
    )
    selected = list(range(12, 48))
    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    recipe.nodes.extend(
        [
            Selection(
                id="patch",
                label="Cylinder patch",
                operation="selection",
                source="scan",
                ids=selected,
            ),
            SurfaceFit(
                id="cylinder",
                label="Cylinder",
                operation="fit",
                selections=["patch"],
                kind="cylinder",
            ),
            Growth(
                id="grown",
                label="Axially extended cylinder",
                operation="growth",
                seed_fit="cylinder",
                distance=0.02 * scale,
                angle_degrees=10,
            ),
        ]
    )
    recipe = recipe.model_copy(update={"output": "grown"})
    report: dict[str, Any] = {"comparison_length_scale": scale}
    for fixed in (False, True):
        active_recipe = recipe
        if fixed:
            active_recipe = Recipe.model_validate_json(recipe.model_dump_json())
            active_recipe.nodes.extend(
                [
                    AxisDefinition(
                        id="axis",
                        label="Reversed fitted axis",
                        operation="axis",
                        source_fit="cylinder",
                        direction_reversed=True,
                    ),
                    Selection(
                        id="all",
                        label="All cylinder observations",
                        operation="selection",
                        source="scan",
                        ids=list(range(len(points))),
                    ),
                    SurfaceFit(
                        id="bound",
                        label="Fixed-axis cylinder",
                        operation="fit",
                        selections=["all"],
                        kind="cylinder",
                        axis="axis",
                    ),
                ]
            )
            active_recipe = active_recipe.model_copy(update={"output": "bound"})
        graph = FeatureGraph(accepted.workspace, active_recipe)
        state = cast(
            dict[str, Any],
            graph.evaluate(str(graph.snapshot()["token"]), all_actions=True),
        )
        assert not state["errors"] and not state["diagnostics"], state
        assert set(state["states"].values()) == {"ready"}
        fitted = state["results"]["cylinder"]
        analytic = fitted["analytic"]
        fitted_axis = np.asarray(analytic["axis"])
        assert np.linalg.norm(np.cross(fitted_axis, axis)) < 0.001
        assert (
            np.linalg.norm(
                np.cross((np.asarray(analytic["origin"]) - origin) / scale, axis)
            )
            < 0.005
        )
        assert abs(analytic["radius"] / scale - 2.5) < 0.005
        assert fitted["weighted_rms"] / scale < 0.005
        assert state["results"]["grown"]["ids"] == list(range(len(points)))
        if fixed:
            np.testing.assert_allclose(
                state["results"]["axis"]["axis_display"],
                -fitted_axis,
                rtol=0,
                atol=1e-12,
            )
            bound = state["results"]["bound"]["analytic"]
            assert abs(float(np.dot(bound["axis"], fitted_axis))) > 1 - 1e-12
        reopened = open_source("renamed-identical-cylinder.ply")
        replay = FeatureGraph(
            reopened.workspace,
            Recipe.model_validate_json(active_recipe.model_dump_json()),
        )
        replayed = cast(
            dict[str, Any],
            replay.evaluate(str(replay.snapshot()["token"]), all_actions=True),
        )
        for key in ("recipe", "token", "states", "errors", "diagnostics", "results"):
            compare_values(replayed[key], state[key], f"cylinder.replay.{key}")
        report["axis_bound" if fixed else "standalone"] = {
            key: state[key]
            for key in ("recipe", "token", "states", "errors", "diagnostics", "results")
        }
    workspace = accepted.workspace
    assert (
        workspace.source_sha256 is not None and workspace.source_descriptor is not None
    )
    request_binding = initialization_binding(
        workspace.source_sha256,
        workspace.source_descriptor.interpretation_sha256,
        selected,
        {
            "family": "cylinder",
            "references": {},
            "constraints": {},
            "support": None,
            "settings": {},
        },
        policy_version=CYLINDER_POLICY_VERSION,
    )
    hinted = fit_selected_cylinder(
        workspace.local[selected],
        workspace.data.weights[selected],
        workspace.data.normals[selected],
        request_binding,
        hint={
            "origin": [1e100 * scale, -1e100 * scale, 1e100 * scale],
            "axis": [0.0, 0.0, 1.0],
            "radius": 1e100 * scale,
        },
    )
    attempts = hinted["initialization"]["recovery"]["attempts"]
    assert hinted["initialization"]["recovery"]["selected_role"] == "fresh"
    assert attempts[-1]["role"] == "hint" and attempts[-1]["evaluations"] == 0
    assert attempts[-1]["outcome"] == "screened-invalid-or-stale"
    report["hinted"] = {
        "source_sha256": workspace.source_sha256,
        "interpretation_sha256": workspace.source_descriptor.interpretation_sha256,
        "ids": selected,
        "result": hinted,
    }
    return report


def analytic_geometry_probe() -> dict[str, Any]:
    """Exact rational spatial cases, independent of fitting and source-Z charts."""
    rotation = np.array([[0.0, 1, 0], [-1, 0, 0], [0, 0, 1]])
    translation = np.array([2.0, -1, 4])
    report: dict[str, Any] = {}
    for kind, radius, slope, domain, points, membership in (
        (
            "cylinder",
            2.0,
            0.0,
            [-2.0, 4.0],
            [[1.0, -2, 5], [1, -4, 7], [7, -6, 5], [7, -4, 3], [8, -2, 5]],
            [True, True, True, True, False],
        ),
        (
            "cone",
            -1.0,
            0.5,
            [4.0, 8.0],
            [[7.0, -3, 5], [11, -4, 8], [6, -4, 5.5]],
            [True, True, False],
        ),
    ):
        geometry = {
            "kind": kind,
            "origin": [3.0, -4, 5],
            "axis": [1.0, 0, 0],
            "radius": radius,
            "slope": slope,
            "axial_domain": domain,
        }
        observations = np.asarray(points)
        variants = (
            (geometry, observations, False),
            (rebase_revolution(geometry, 6.0), observations, False),
            (reverse_revolution(geometry), observations, False),
            (
                transform_revolution(geometry, rotation, translation),
                observations @ rotation + translation,
                True,
            ),
        )
        records: list[dict[str, Any]] = []
        baseline_normals = None
        for value, selected, moved in variants:
            # Canonical data must remain authoritative even beside an unusable
            # old chart; horizontal geometry has no valid legacy substitute.
            surface = {
                "kind": kind,
                "analytic": value,
                "parameters": ["stale-legacy-chart"],
            }
            residual, normals, inside = surface_distance(selected, surface)
            np.testing.assert_allclose(residual, 0, rtol=RTOL, atol=ATOL)
            assert inside.tolist() == membership
            if baseline_normals is None:
                baseline_normals = normals
            np.testing.assert_allclose(
                normals,
                baseline_normals @ rotation if moved else baseline_normals,
                rtol=RTOL,
                atol=ATOL,
            )
            records.append(
                {
                    "geometry": value,
                    "primitive": primitive(surface),
                    "residuals": residual.tolist(),
                    "normals": normals.tolist(),
                    "inside": inside.tolist(),
                }
            )
        report[kind] = records
    return report


def numeric(root: Path, fixture: Path) -> dict[str, Any]:
    started = time.perf_counter()
    with np.load(fixture, allow_pickle=False) as data:
        assert all(array.dtype == np.float64 for array in data.values())
        synthetic = fit_cone_plane(
            data["cone"],
            data["plane"],
            data["cw"],
            data["pw"],
            data["initial"],
            (-3, 6),
        )
        residuals, _ = cone_plane_residual_jacobian(
            data["cone"], data["plane"], np.array(synthetic["parameters"]), (-3, 6)
        )
        synthetic_seconds = time.perf_counter() - started
        plane_started = time.perf_counter()
        imported_planes = [
            imported_plane_probe(points, normal, float(scale))
            for points, normal, scale in zip(
                data["imported_planes"],
                data["imported_plane_normals"],
                data["imported_plane_scales"],
                strict=True,
            )
        ]
        imported_plane_seconds = time.perf_counter() - plane_started
        sphere_started = time.perf_counter()
        imported_spheres = [
            imported_sphere_probe(points, normals, center, float(radius), float(scale))
            for points, normals, center, radius, scale in zip(
                data["imported_spheres"],
                data["imported_sphere_normals"],
                data["imported_sphere_centers"],
                data["imported_sphere_radii"],
                data["imported_sphere_scales"],
                strict=True,
            )
        ]
        imported_sphere_seconds = time.perf_counter() - sphere_started
        cylinder_started = time.perf_counter()
        imported_cylinders = [
            imported_cylinder_probe(points, normals, origin, axis, float(scale))
            for points, normals, origin, axis, scale in zip(
                data["imported_cylinders"],
                data["imported_cylinder_normals"],
                data["imported_cylinder_origins"],
                data["imported_cylinder_axes"],
                data["imported_cylinder_scales"],
                strict=True,
            )
        ]
        imported_cylinder_seconds = time.perf_counter() - cylinder_started
    started = time.perf_counter()
    workspace = NozzleWorkspace(root / EXAMPLE)
    load_seconds = time.perf_counter() - started
    started = time.perf_counter()
    nozzle = workspace.fit(workspace.default)
    nozzle_seconds = time.perf_counter() - started
    recipe = Recipe.model_validate_json(
        (root / EXAMPLE / "recipes/cone-plane.json").read_text()
    )
    graph = FeatureGraph(workspace, recipe)
    started = time.perf_counter()
    state = cast(dict[str, Any], graph.evaluate(str(graph.snapshot()["token"])))
    graph_seconds = time.perf_counter() - started
    assert not state["errors"] and not state["diagnostics"]
    assert set(state["states"].values()) == {"ready"}
    # Recreate from the saved recipe rather than keeping derived graph caches.
    replay = FeatureGraph(
        workspace, Recipe.model_validate_json(recipe.model_dump_json())
    )
    replay_state = cast(
        dict[str, Any], replay.evaluate(str(replay.snapshot()["token"]))
    )
    assert replay_state["token"] == state["token"]
    _ = compare_fit(replay_state["result"]["fit"], state["result"]["fit"])
    excluded = [
        name
        for name in ("OCP", "duckdb", "pyarrow", "psutil", "cyclopts", "structlog")
        if name in sys.modules
    ]
    assert not excluded, f"numerical startup loaded excluded dependencies: {excluded}"
    return {
        "versions": {
            "python": sys.version,
            "numpy": np.__version__,
            "pydantic": pydantic.__version__,
        },
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "counts": {
            "synthetic_cone": 432,
            "synthetic_plane": 64,
            "imported_plane_cases": len(imported_planes),
            "imported_sphere_cases": len(imported_spheres),
            "imported_cylinder_cases": len(imported_cylinders),
            "mesh_vertices": len(workspace.local),
            "mesh_triangles": len(workspace.data.triangles),
            "nozzle_cone": len(workspace.default.lateral_ids),
            "nozzle_plane": len(workspace.default.plane_ids),
        },
        "source_sha256": workspace.default.source_sha256,
        "model_sha256": workspace.model_sha256,
        "synthetic": {"fit": synthetic, "residuals": residuals.tolist()},
        "imported_planes": imported_planes,
        "imported_spheres": imported_spheres,
        "imported_cylinders": imported_cylinders,
        "analytic_geometry": analytic_geometry_probe(),
        "nozzle": nozzle,
        "graph": json.loads(
            json.dumps(
                {
                    key: state[key]
                    for key in (
                        "recipe",
                        "token",
                        "states",
                        "errors",
                        "diagnostics",
                        "result",
                        "results",
                    )
                },
                allow_nan=False,
            )
        ),
        "timing_seconds": {
            "synthetic": synthetic_seconds,
            "imported_planes": imported_plane_seconds,
            "imported_spheres": imported_sphere_seconds,
            "imported_cylinders": imported_cylinder_seconds,
            "workspace_load": load_seconds,
            "nozzle": nozzle_seconds,
            "graph": graph_seconds,
        },
    }


def compare_fit(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    comparisons = {}
    for key in ("parameters", "weighted_rms"):
        np.testing.assert_allclose(
            actual[key], expected[key], rtol=RTOL, atol=ATOL, err_msg=key
        )
        comparisons[f"{key}_max_abs_difference"] = float(
            np.max(np.abs(np.asarray(actual[key]) - expected[key]))
        )
    for key in ("termination", "rank", "iterations", "evaluations"):
        assert actual["solver"][key] == expected["solver"][key], (
            f"solver {key} mismatch"
        )
    comparisons["solver"] = actual["solver"]
    return comparisons


def compare_session(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    assert actual["session"] == expected["session"]
    result = compare_fit(actual["fit"], expected["fit"])
    for key in (
        "lateral_residuals",
        "plane_residuals",
        "axis_display",
        "point_display",
        "plane_point_display",
        "axial_domain",
        "reference_diameter",
        "signed_half_angle_degrees",
    ):
        np.testing.assert_allclose(
            actual[key], expected[key], rtol=RTOL, atol=ATOL, err_msg=key
        )
        result[f"{key}_max_abs_difference"] = float(
            np.max(np.abs(np.asarray(actual[key]) - expected[key]))
        )
    for key in ("selected_area", "surfaces"):
        if key in actual or key in expected:
            compare_values(actual.get(key), expected.get(key), key)
    return result


def compare_values(actual: Any, expected: Any, path: str) -> None:
    """Compare each graph action, including identities and derived geometry."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key, value in expected.items():
            compare_values(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, (list, tuple)):
        assert isinstance(actual, (list, tuple)) and len(actual) == len(expected), path
        for index, value in enumerate(expected):
            compare_values(actual[index], value, f"{path}[{index}]")
    elif isinstance(expected, float):
        assert isinstance(actual, (float, int)) and not isinstance(actual, bool), path
        assert math.isfinite(actual) and math.isfinite(expected), path
        np.testing.assert_allclose(actual, expected, rtol=RTOL, atol=ATOL, err_msg=path)
    else:
        assert type(actual) is type(expected) and actual == expected, path


def compare_intent(actual: Any, expected: Any, path: str) -> None:
    """Exact JSON intent, allowing Python tuples to serialize as JSON arrays."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key, value in expected.items():
            compare_intent(actual[key], value, f"{path}.{key}")
    elif isinstance(expected, (list, tuple)):
        assert isinstance(actual, (list, tuple)) and len(actual) == len(expected), path
        for index, value in enumerate(expected):
            compare_intent(actual[index], value, f"{path}[{index}]")
    else:
        assert type(actual) is type(expected) and actual == expected, path


def compare_imported_planes(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> None:
    """Compare complete records in the frozen fixture's units, at fixed tolerances.

    Raw length/area tolerances are not invariant under the twelve-order scale
    range here. Normalize only known dimensional results using the expected
    fixture scale; input identities, membership and dimensionless evidence stay
    untouched. Do not normalize inert, explicitly authored plane support.
    """
    assert len(actual) == len(expected), "imported_planes count"

    def normalized(report: dict[str, Any], scale: float) -> dict[str, Any]:
        result = deepcopy(report)
        fitted = result["results"]["plane"]
        fitted["parameters"][3] /= scale
        for key in ("residuals", "weighted_rms"):
            if key in fitted:
                fitted[key] = (np.asarray(fitted[key]) / scale).tolist()
        initialization = fitted.get("initialization", {})
        for key in ("origin", "length"):
            if key in initialization:
                initialization[key] = (np.asarray(initialization[key]) / scale).tolist()
        grown = result["results"].get("grown", {})
        if "added_area" in grown:
            grown["added_area"] /= scale * scale
        return result

    for index, (value, reference) in enumerate(zip(actual, expected, strict=True)):
        scale = reference["comparison_length_scale"]
        if type(scale) not in (float, int) or not math.isfinite(scale) or scale <= 0:
            raise ValueError(
                "plane comparison length scale must be finite and positive"
            )
        assert value["comparison_length_scale"] == scale, "plane fixture scale mismatch"
        compare_values(
            normalized(value, float(scale)),
            normalized(reference, float(scale)),
            f"imported_planes[{index}]",
        )


def compare_imported_spheres(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> None:
    """Compare physical sphere outputs in frozen units and all recovery evidence.

    Recovery candidates, objectives and initial parameters use normalized charts;
    keep them dimensionless. Explicit actions and binding identities remain exact.
    """
    assert len(actual) == len(expected), "imported_spheres count"

    def normalized(report: dict[str, Any], scale: float) -> dict[str, Any]:
        result = deepcopy(report)
        for mode in ("standalone", "shared_center"):
            outputs = result[mode]["results"]
            fitted = outputs["sphere"]
            for key in ("parameters", "residuals", "weighted_rms"):
                fitted[key] = (np.asarray(fitted[key]) / scale).tolist()
            for key in ("origin", "length"):
                initialization = fitted.get("initialization", {})
                if key in initialization:
                    initialization[key] = (
                        np.asarray(initialization[key]) / scale
                    ).tolist()
            solver = fitted.get("solver", {})
            for key in ("coordinate_origin", "coordinate_length"):
                if key in solver:
                    solver[key] = (np.asarray(solver[key]) / scale).tolist()
            point = outputs.get("center", {})
            for key in ("point_display", "coordinates"):
                if key in point:
                    point[key] = (np.asarray(point[key]) / scale).tolist()
            grown = outputs.get("grown", {})
            if "added_area" in grown:
                grown["added_area"] /= scale * scale
        return result

    for index, (value, reference) in enumerate(zip(actual, expected, strict=True)):
        scale = reference["comparison_length_scale"]
        if type(scale) not in (float, int) or not math.isfinite(scale) or scale <= 0:
            raise ValueError(
                "sphere comparison length scale must be finite and positive"
            )
        assert value["comparison_length_scale"] == scale, (
            "sphere fixture scale mismatch"
        )
        for mode in ("standalone", "shared_center"):
            compare_intent(
                value[mode]["recipe"],
                reference[mode]["recipe"],
                f"imported_spheres[{index}].{mode}.recipe",
            )
        compare_values(
            normalized(value, float(scale)),
            normalized(reference, float(scale)),
            f"imported_spheres[{index}]",
        )


def compare_imported_cylinders(
    actual: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> None:
    """Compare complete canonical/recovery records in the frozen source's units."""
    assert len(actual) == len(expected), "imported_cylinders count"

    def normalized_fit(fitted: dict[str, Any], scale: float) -> None:
        analytic = fitted["analytic"]
        for key in ("origin", "radius", "axial_domain"):
            analytic[key] = (np.asarray(analytic[key]) / scale).tolist()
        for key in ("residuals", "weighted_rms", "anchor_reference", "axial_domain"):
            if key in fitted:
                fitted[key] = (np.asarray(fitted[key]) / scale).tolist()
        initialization = fitted.get("initialization", {})
        for key in ("origin", "length"):
            if key in initialization:
                initialization[key] = (np.asarray(initialization[key]) / scale).tolist()
        solver = fitted.get("solver", {})
        for key in ("coordinate_origin", "coordinate_length"):
            if key in solver:
                solver[key] = (np.asarray(solver[key]) / scale).tolist()

    def normalized(report: dict[str, Any], scale: float) -> dict[str, Any]:
        result = deepcopy(report)
        for mode in ("standalone", "axis_bound"):
            outputs = result[mode]["results"]
            for value in outputs.values():
                if "analytic" in value:
                    normalized_fit(value, scale)
                if "point_display" in value:
                    value["point_display"] = (
                        np.asarray(value["point_display"]) / scale
                    ).tolist()
                if "added_area" in value:
                    value["added_area"] /= scale * scale
        normalized_fit(result["hinted"]["result"], scale)
        return result

    for index, (value, reference) in enumerate(zip(actual, expected, strict=True)):
        scale = reference["comparison_length_scale"]
        if type(scale) not in (float, int) or not math.isfinite(scale) or scale <= 0:
            raise ValueError(
                "cylinder comparison length scale must be finite and positive"
            )
        assert value["comparison_length_scale"] == scale, (
            "cylinder fixture scale mismatch"
        )
        for mode in ("standalone", "axis_bound"):
            compare_intent(
                value[mode]["recipe"],
                reference[mode]["recipe"],
                f"imported_cylinders[{index}].{mode}.recipe",
            )
        compare_values(
            normalized(value, float(scale)),
            normalized(reference, float(scale)),
            f"imported_cylinders[{index}]",
        )


def compare_numeric(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    for key in ("fixture_sha256", "counts", "source_sha256", "model_sha256"):
        assert actual[key] == expected[key], f"{key} mismatch"
    graph, reference = actual["graph"], expected["graph"]
    for key in ("recipe", "token", "states", "errors", "diagnostics"):
        assert graph[key] == reference[key], f"graph {key} mismatch"
    compare_values(graph["results"], reference["results"], "graph.results")
    compare_imported_planes(actual["imported_planes"], expected["imported_planes"])
    compare_imported_spheres(actual["imported_spheres"], expected["imported_spheres"])
    compare_imported_cylinders(
        actual["imported_cylinders"], expected["imported_cylinders"]
    )
    compare_values(
        actual["analytic_geometry"], expected["analytic_geometry"], "analytic_geometry"
    )
    synthetic = compare_fit(actual["synthetic"]["fit"], expected["synthetic"]["fit"])
    np.testing.assert_allclose(
        actual["synthetic"]["residuals"],
        expected["synthetic"]["residuals"],
        rtol=RTOL,
        atol=ATOL,
    )
    synthetic["residual_max_abs_difference"] = float(
        np.max(
            np.abs(
                np.asarray(actual["synthetic"]["residuals"])
                - expected["synthetic"]["residuals"]
            )
        )
    )
    return {
        "synthetic": synthetic,
        "nozzle": compare_session(actual["nozzle"], expected["nozzle"]),
        "graph": compare_session(graph["result"], reference["result"]),
        "imported_plane_cases": len(actual["imported_planes"]),
        "imported_sphere_cases": len(actual["imported_spheres"]),
        "imported_cylinder_cases": len(actual["imported_cylinders"]),
        "rtol": RTOL,
        "atol": ATOL,
    }


def cad_smoke(output: Path) -> dict[str, Any]:
    """Scansor bounded cylinder face, meshing and named STEP; not full CAD coverage."""
    import OCP
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.Interface import Interface_Static

    # Intentionally probe the existing export implementation, without a new API.
    from experiments.nozzle_cad import (
        _step_bytes,  # pyright: ignore[reportPrivateUsage]
    )
    from experiments.ocp_geometry import face_from_record, tessellate_face
    from experiments.surface_extents import circle_intersection, trimmed_face

    surface = {"kind": "cylinder", "parameters": [1, 2, 0, 0, 3, 0, 0]}
    cuts = [
        circle_intersection(surface, {"kind": "plane", "plane_equation": [0, 0, 1, h]})
        for h in (1, 4)
    ]
    record = trimmed_face(
        surface,
        [
            ({"intersection": "lower", "keep": "positive"}, cuts[0]),
            ({"intersection": "upper", "keep": "negative"}, cuts[1]),
        ],
        np.empty((0, 3)),
    )
    face = face_from_record(record)
    assert BRepCheck_Analyzer(face).IsValid()
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, properties)
    expected_area = 18 * math.pi
    assert math.isclose(properties.Mass(), expected_area, rel_tol=1e-10)
    mesh = tessellate_face(face, 3)
    assert mesh["positions"] and mesh["indices"]
    # Initialize the native writer's settings before taking the restoration snapshot.
    from OCP.STEPCAFControl import STEPCAFControl_Writer

    writer = STEPCAFControl_Writer()
    assert writer is not None
    previous = Interface_Static.CVal_s("write.step.unit")
    data = _step_bytes({"side": face}, {"side": "肩面 Ø12 — café"}, "Millimeters")
    assert Interface_Static.CVal_s("write.step.unit") == previous
    assert b"SI_UNIT(.MILLI.,.METRE.)" in data
    _ = (output / "smoke.step").write_bytes(data)
    return {
        "version": OCP.__version__,
        "valid": True,
        "area": properties.Mass(),
        "expected_area": expected_area,
        "vertices": len(mesh["positions"]) // 3,
        "triangles": len(mesh["indices"]) // 3,
        "step_bytes": len(data),
        "global_units_restored": True,
    }


def read_step(path: Path) -> dict[str, Any]:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.collections import Sequence_TDF_Label
    from OCP.GProp import GProp_GProps
    from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    reader = STEPCAFControl_Reader()
    # The other binding overload has an unexposed DESTEP_Parameters type.
    read_file = cast(Callable[[str], IFSelect_ReturnStatus], reader.ReadFile)
    assert read_file(str(path)) == IFSelect_RetDone
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFDoc_DocumentTool.SetLengthUnit_s(document, 0.001)
    assert reader.Transfer(document)
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    labels = Sequence_TDF_Label()
    tool.GetFreeShapes(labels)
    assert labels.Length() == 1
    label = labels.Value(1)
    name = TDataStd_Name()
    assert label.FindAttribute(TDataStd_Name.GetID_s(), name)
    assert name.Get().ToExtString() == "肩面 Ø12 — café"
    shape = tool.GetShape_s(label)
    assert BRepCheck_Analyzer(shape).IsValid()
    properties = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, properties)
    assert math.isclose(properties.Mass(), 18 * math.pi, rel_tol=1e-8)
    return {"valid": True, "area": properties.Mass(), "name": name.Get().ToExtString()}


def native(root: str, output: str, cad: bool) -> None:
    target = Path(output)
    create_fixture(target / "fixture.npz")
    result = numeric(Path(root), target / "fixture.npz")
    if cad:
        result["cad"] = cad_smoke(target)
        assert result["cad"]["version"] == "8.0.1.1"
        result["cad"]["readback"] = read_step(target / "smoke.step")
    _ = (target / "native-results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps({"native_versions": result["versions"], "counts": result["counts"]})
    )


def browser() -> str:
    result = numeric(Path("/app"), Path("/fixture.npz"))
    expected = json.loads(Path("/native-results.json").read_text())
    comparison = compare_numeric(result, expected)
    report = {
        key: result[key]
        for key in (
            "versions",
            "counts",
            "fixture_sha256",
            "source_sha256",
            "model_sha256",
            "timing_seconds",
        )
    }
    report["native_versions"] = expected["versions"]
    report["comparison"] = comparison
    report["parameters"] = {
        "synthetic": result["synthetic"]["fit"]["parameters"],
        "nozzle": result["nozzle"]["fit"]["parameters"],
        "graph": result["graph"]["result"]["fit"]["parameters"],
    }
    report["graph_states"] = result["graph"]["states"]
    return json.dumps(report, allow_nan=False)


def browser_cad() -> str:
    """Load CAD only after the numerical startup/parity probe succeeded."""
    expected = json.loads(Path("/native-results.json").read_text())
    result = cad_smoke(Path("/"))
    np.testing.assert_allclose(
        result["area"], expected["cad"]["area"], rtol=RTOL, atol=ATOL
    )
    result["readback"] = read_step(Path("/smoke.step"))
    result["step_base64"] = base64.b64encode(Path("/smoke.step").read_bytes()).decode()
    return json.dumps(result, allow_nan=False)
