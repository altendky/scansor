"""Seed-only fits and one-pass connected mesh selection proposals."""

from collections import deque
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.fit_coordinates import AxisChart, CoordinateFrame, FitCoordinates
from experiments.fit_solver import (
    SolverDiagnostics,
    normalized_weights,
    solve_geometric_fit,
)
from experiments.mesh_cone_plane_fit import (
    cone_plane_residual_jacobian,
    valid_cone_geometry,
)
from experiments.mesh_cylinder_fit import Array
from experiments.mesh_sphere_fit import fit_sphere


def fit_seed(
    points: Array,
    weights: Array,
    normals: Array,
    kind: str,
    initial: Array,
    domain: tuple[float, float],
) -> dict[str, Any]:
    minimum = 3 if kind == "plane" else 4 if kind == "sphere" else 7
    if (
        len(points) < minimum
        or not np.isfinite(points).all()
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
    ):
        raise ValueError(
            f"seed fit needs at least {minimum} finite, positive-area observations"
        )
    w = normalized_weights(weights)
    solver: SolverDiagnostics | None = None
    if kind == "plane":
        center = w @ points
        q = points - center
        values, vectors = np.linalg.eigh(q.T @ (w[:, None] * q))
        if values[1] <= max(values[-1], 1e-30) * 1e-10:
            raise ValueError(
                "seed plane is ill-conditioned; paint a wider two-dimensional patch"
            )
        axis = vectors[:, 0]
        if float((w @ normals) @ axis) < 0:
            axis = -axis
        h = float(axis @ center)
        residual = points @ axis - h
        parameters = [*axis.tolist(), h]
        condition = float(values[-1] / values[1])
    elif kind == "sphere":
        result = fit_sphere(points, weights)
        parameters = result["parameters"]
        residual = np.linalg.norm(points - np.asarray(parameters[:3]), axis=1) - float(
            parameters[3]
        )
        condition = result["normal_matrix_condition"]
        solver = result.get("solver")
    elif kind in ("cone", "cylinder"):
        if initial.shape != (5,) or not np.isfinite(initial).all() or initial[4] <= 0:
            raise ValueError("invalid seed initialization")
        columns = [0, 1, 2, 3, 4, 6] if kind == "cone" else [0, 1, 2, 3, 4]

        def unpack(values: Array) -> Array:
            full = np.zeros(7)
            full[columns] = values
            return full

        def evaluate(values: Array) -> tuple[Array, Array]:
            residual, jacobian = cone_plane_residual_jacobian(
                points, np.empty((0, 3)), unpack(values), domain
            )
            return residual, jacobian[:, columns]

        start = np.append(initial, 0.0) if kind == "cone" else initial
        coordinates = FitCoordinates(
            CoordinateFrame.from_observations(points, weights),
            len(columns),
            axes=(AxisChart(0, 1, 2, 3, 4, 5 if kind == "cone" else None),),
        )
        fit = solve_geometric_fit(
            start,
            weights,
            coordinates,
            evaluate,
            lambda p: valid_cone_geometry(unpack(p), domain, points),
        )
        parameters = unpack(fit.parameters).tolist()
        residual, condition = fit.residual, fit.condition
        solver = fit.solver
    else:
        raise ValueError("unsupported seed surface type")
    fitted: dict[str, Any] = {
        "kind": kind,
        "parameters": parameters,
        "axial_domain": domain,
        "weighted_rms": float(np.sqrt(w @ residual**2)),
        "condition": condition,
        "residuals": residual.tolist(),
    }
    if solver is not None:
        fitted["solver"] = solver
    _, expected, inside = surface_distance(points, fitted)
    fitted["support_classification"] = {
        "inside_vertices": int(inside.sum()),
        "outside_vertices": int((~inside).sum()),
        "role": "selection-and-display",
    }
    fitted["normal_sign"] = (
        1.0 if float(w @ np.sum(expected * normals, axis=1)) >= 0 else -1.0
    )
    return fitted


def surface_distance(
    points: Array, fit: dict[str, Any]
) -> tuple[Array, Array, NDArray[np.bool_]]:
    p = np.array(fit["parameters"])
    if fit["kind"] == "plane":
        return (
            points @ p[:3] - p[3],
            np.broadcast_to(p[:3], points.shape).copy(),
            np.ones(len(points), dtype=bool),
        )
    if fit["kind"] == "sphere":
        radial = points - p[:3]
        rho = np.linalg.norm(radial, axis=1)
        return (
            rho - p[3],
            radial / np.maximum(rho, 1e-30)[:, None],
            rho > 0,
        )
    axis = np.array([p[2], p[3], 1.0])
    axis /= np.linalg.norm(axis)
    q = points - np.array([p[0], p[1], 0.0])
    z = q @ axis
    radial = q - z[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    scale = float(np.hypot(1, p[6]))
    expected = (radial / np.maximum(rho, 1e-30)[:, None] - p[6] * axis) / scale
    residual = (rho - p[4] - p[6] * z) / scale
    projected_z = (z + p[6] * (rho - p[4])) / (1 + p[6] ** 2)
    lo, hi = fit["axial_domain"]
    return (
        residual,
        expected,
        (rho > 0)
        & (p[4] + p[6] * projected_z > 0)
        & (projected_z >= lo)
        & (projected_z <= hi),
    )


def connected_growth(
    points: Array,
    normals: Array,
    triangles: NDArray[np.int32],
    weights: Array,
    seed: list[int],
    barriers: list[int],
    fit: dict[str, Any],
    distance: float,
    angle_degrees: float,
) -> dict[str, Any]:
    residual, expected, support = surface_distance(points, fit)
    eligible = (
        support & np.isfinite(residual) & (np.abs(residual) <= distance) & (weights > 0)
    )
    eligible &= np.sum(normals * expected, axis=1) * fit["normal_sign"] >= np.cos(
        np.radians(angle_degrees)
    )
    eligible[barriers] = False
    if set(seed).intersection(barriers):
        raise ValueError("seed overlaps a barrier selection")
    adjacency: list[set[int]] = [set() for _ in points]
    xyz = points[triangles]
    valid = (
        np.linalg.norm(np.cross(xyz[:, 1] - xyz[:, 0], xyz[:, 2] - xyz[:, 0]), axis=1)
        > 0
    )
    for triangle in triangles[valid]:
        a, b, c = (int(i) for i in triangle)
        for left, right in ((a, b), (b, c), (c, a)):
            if eligible[left] and eligible[right]:
                adjacency[left].add(right)
                adjacency[right].add(left)
    reached = {i for i in seed if eligible[i]}
    if not reached:
        raise ValueError(
            "no seed vertices meet the growth thresholds; adjust thresholds or seed coverage"
        )
    queue = deque(sorted(reached))
    while queue:
        for neighbor in adjacency[queue.popleft()]:
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)
    additions = sorted(reached.difference(seed))
    return {
        "ids": sorted(set(seed).union(reached)),
        "added_ids": additions,
        "rejected_seed_ids": sorted(i for i in seed if not eligible[i]),
        "eligible_count": int(eligible.sum()),
        "added_area": float(weights[additions].sum()),
    }
