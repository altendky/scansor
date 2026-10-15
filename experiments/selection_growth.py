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
from experiments.surface_primitives import revolution_geometry


class PlaneFitFailure(ValueError):
    """Direct plane identifiability/orientation evidence, separate from geometry."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        self.code: str = code
        self.diagnostic: dict[str, Any] = {
            "kind": "plane_fit_failure",
            "code": code,
            **(details or {}),
        }
        super().__init__(message)


def fit_seed(
    points: Array,
    weights: Array,
    normals: Array,
    kind: str,
    initial: Array | None,
    domain: tuple[float, float],
) -> dict[str, Any]:
    minimum = 3 if kind == "plane" else 4 if kind == "sphere" else 7
    if (
        len(points) < minimum
        or not np.isfinite(points).all()
        or not np.isfinite(weights).all()
        or np.any(weights <= 0)
    ):
        message = (
            f"seed fit needs at least {minimum} finite, positive-area observations"
        )
        if kind == "plane":
            raise PlaneFitFailure(
                "insufficient-observations", message, {"observations": len(points)}
            )
        raise ValueError(message)
    w = normalized_weights(weights)
    solver: SolverDiagnostics | None = None
    initialization: dict[str, Any] | None = None
    ambiguous_orientation = False
    if kind == "plane":
        normal_lengths = (
            np.linalg.norm(normals, axis=1) if normals.ndim == 2 else np.empty(0)
        )
        if (
            normals.shape != (len(points), 3)
            or not np.isfinite(normals).all()
            or not np.isfinite(normal_lengths).all()
            or np.any(normal_lengths <= 0)
        ):
            raise PlaneFitFailure(
                "invalid-normals", "plane orientation needs admitted usable normals"
            )
        try:
            frame = CoordinateFrame.from_observations(points, weights)
        except ValueError as error:
            raise PlaneFitFailure(
                "insufficient-coverage",
                "seed plane is ill-conditioned; paint a wider two-dimensional patch",
                {"observations": len(points)},
            ) from error
        q = (points - frame.origin) / frame.length
        values, vectors = np.linalg.eigh(q.T @ (w[:, None] * q))
        if values[0] < -32 * np.finfo(float).eps * max(float(values[-1]), 1.0):
            raise PlaneFitFailure(
                "numeric-failure", "invalid plane covariance spectrum"
            )
        # This matrix is positive semidefinite. Only roundoff-sized negative
        # eigenvalues are clamped; no positive observation variance is removed.
        values = np.maximum(values, 0.0)
        coverage = float(values[1] / values[2])
        gap = float((values[1] - values[0]) / values[2])
        initialization = {
            "algorithm": "weighted-covariance-plane-v1",
            "chart": "workspace-normal-offset-v1",
            "observations": len(points),
            "origin": frame.origin.tolist(),
            "length": frame.length,
            "normalized_covariance_eigenvalues": values.tolist(),
            "coverage_ratio": coverage,
            "normal_gap_ratio": gap,
        }
        if coverage <= 1e-10:
            raise PlaneFitFailure(
                "insufficient-coverage",
                "seed plane is ill-conditioned; paint a wider two-dimensional patch",
                initialization,
            )
        if gap <= 1e-10:
            raise PlaneFitFailure(
                "ambiguous-normal",
                "selected observations do not determine a unique plane normal",
                initialization,
            )
        axis = vectors[:, 0]
        projection = float(w @ (normals @ axis))
        normal_scale = float(w @ normal_lengths)
        ambiguous_orientation = abs(projection) <= 1e-12 * normal_scale
        if ambiguous_orientation:
            if axis[int(np.argmax(np.abs(axis)))] < 0:
                axis = -axis
        elif projection < 0:
            axis = -axis
        initialization["orientation"] = (
            "ambiguous" if ambiguous_orientation else "selected-normals"
        )
        initialization["normal_projection"] = abs(projection) / normal_scale
        h = float(axis @ frame.origin)
        residual = (q @ axis) * frame.length
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
        initialization = result.get("initialization")
    elif kind in ("cone", "cylinder"):
        if (
            initial is None
            or initial.shape != (5,)
            or not np.isfinite(initial).all()
            or initial[4] <= 0
        ):
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
    if initialization is not None:
        fitted["initialization"] = initialization
    _, expected, inside = surface_distance(points, fitted)
    fitted["support_classification"] = {
        "inside_vertices": int(inside.sum()),
        "outside_vertices": int((~inside).sum()),
        "role": "selection-and-display",
    }
    fitted["normal_sign"] = (
        1.0
        if ambiguous_orientation or float(w @ np.sum(expected * normals, axis=1)) >= 0
        else -1.0
    )
    return fitted


def surface_distance(
    points: Array, fit: dict[str, Any]
) -> tuple[Array, Array, NDArray[np.bool_]]:
    if fit["kind"] == "plane":
        p = np.array(fit["parameters"])
        return (
            points @ p[:3] - p[3],
            np.broadcast_to(p[:3], points.shape).copy(),
            np.ones(len(points), dtype=bool),
        )
    if fit["kind"] == "sphere":
        p = np.array(fit["parameters"])
        radial = points - p[:3]
        rho = np.linalg.norm(radial, axis=1)
        return (
            rho - p[3],
            radial / np.maximum(rho, 1e-30)[:, None],
            rho > 0,
        )
    geometry = revolution_geometry(fit)
    axis = np.asarray(geometry["axis"])
    radius, slope = geometry["radius"], geometry["slope"]
    q = points - np.asarray(geometry["origin"])
    z = q @ axis
    radial = q - z[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    scale = float(np.hypot(1, slope))
    expected = (radial / np.maximum(rho, 1e-30)[:, None] - slope * axis) / scale
    residual = (rho - radius - slope * z) / scale
    projected_z = (z + slope * (rho - radius)) / (1 + slope**2)
    domain = geometry["axial_domain"]
    inside = np.ones(len(points), dtype=bool)
    if domain is not None:
        inside = (projected_z >= domain[0]) & (projected_z <= domain[1])
    return (
        residual,
        expected,
        (rho > 0) & (radius + slope * projected_z > 0) & inside,
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
    if fit.get("initialization", {}).get("orientation") == "ambiguous":
        raise PlaneFitFailure(
            "ambiguous-orientation",
            "directional growth needs an oriented plane; selected normals do not establish a direction",
            fit["initialization"],
        )
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
