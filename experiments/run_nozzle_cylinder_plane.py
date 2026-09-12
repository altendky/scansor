"""Replay fixed nozzle selections and jointly fit a perpendicular cylinder/end plane."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from experiments.mesh_cylinder_fit import fit_cylinder
from experiments.mesh_cylinder_plane_fit import (
    fit_cylinder_plane,
    joint_residual_jacobian,
)
from experiments.nozzle_joint_view import export_view
from experiments.run_nozzle_cylinder import NozzleExample, load_example


def load_plane_ids(example: Path, data: NozzleExample) -> NDArray[np.int64]:
    selection = json.loads((example / "selections/top-face.json").read_text())
    if selection["source_sha256"] != data.selection["source_sha256"]:
        raise ValueError("plane selection refers to another mesh")
    path = example / "selections" / selection["vertex_ids_file"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != selection["vertex_ids_sha256"]:
        raise ValueError("plane selection IDs hash mismatch")
    ids = np.loadtxt(path, dtype=np.int64, ndmin=1)
    if len(ids) != selection["selected_vertices"] or not np.array_equal(
        ids, np.unique(ids)
    ):
        raise ValueError("invalid plane selection count/order")
    if len(ids) < 3 or ids.min() < 0 or ids.max() >= len(data.xyz):
        raise ValueError("invalid plane selection IDs")
    axis = np.array(selection["axis_seed"]["axis"])
    q = data.xyz - selection["axis_seed"]["center"]
    z = q @ axis
    rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
    mask = (
        (z >= selection["axial_range"][0])
        & (z <= selection["axial_range"][1])
        & (rho >= selection["radius_range"][0])
        & (rho <= selection["radius_range"][1])
        & (data.normals @ axis >= selection["minimum_axial_normal_dot"])
        & (data.weights > 0)
    )
    if not np.array_equal(np.flatnonzero(mask), ids):
        raise ValueError("plane selection gates do not reproduce saved IDs")
    if np.intersect1d(data.ids, ids).size:
        raise ValueError(
            "this experiment requires disjoint cylinder and plane selections"
        )
    return ids


def run(example: Path, output: Path) -> None:
    data = load_example(example)
    plane_ids = load_plane_ids(example, data)
    frame = np.array(data.selection["fit_frame"]["columns"])
    origin = np.array(data.selection["fit_frame"]["origin"])
    cylinder = (data.xyz[data.ids] - origin) @ frame
    plane = (data.xyz[plane_ids] - origin) @ frame
    cw = data.weights[data.ids]
    pw = data.weights[plane_ids]
    cylinder_only = fit_cylinder(
        cylinder, cw, np.array(data.selection["initial_parameters"], dtype=float)
    )
    cp = np.array(cylinder_only["parameters"])
    old_axis = np.array([cp[2], cp[3], 1])
    old_axis /= np.linalg.norm(old_axis)
    offset = float(pw @ (plane @ old_axis) / pw.sum())
    initial = np.concatenate((cp, [offset]))
    fixed_residual, _ = joint_residual_jacobian(cylinder, plane, initial)
    starts = [
        initial,
        initial + np.array([0.1, -0.1, 0.02, -0.02, 0.1, 0.1]),
        initial + np.array([-0.1, 0.1, -0.02, 0.02, -0.1, -0.1]),
    ]
    results = [fit_cylinder_plane(cylinder, plane, cw, pw, start) for start in starts]
    parameters = np.array(results[0]["parameters"])
    residual, _ = joint_residual_jacobian(cylinder, plane, parameters)
    axis = np.array([parameters[2], parameters[3], 1])
    axis /= np.linalg.norm(axis)
    world_axis = frame @ axis
    point = origin + frame @ np.array([parameters[0], parameters[1], 0])
    plane_offset = parameters[5] + world_axis @ origin
    plane_point = point + (plane_offset - world_axis @ point) * world_axis
    # Independent world-space evaluation of both analytic distances.
    q = data.xyz[data.ids] - point
    world_residual = np.concatenate(
        (
            np.linalg.norm(q - (q @ world_axis)[:, None] * world_axis, axis=1)
            - parameters[4],
            data.xyz[plane_ids] @ world_axis - plane_offset,
        )
    )
    np.testing.assert_allclose(residual, world_residual, rtol=1e-10, atol=2e-14)
    combined = np.concatenate((cw, pw))
    combined /= combined.sum()
    # Unconstrained weighted TLS plane provides a diagnostic lower bound for this plane region.
    mean = pw @ plane / pw.sum()
    centered = plane - mean
    eigenvalues, _ = np.linalg.eigh(centered.T @ (pw[:, None] * centered) / pw.sum())
    report = {
        "status": "exploratory real-scan joint fit; unknown units; no physical validation",
        "source_sha256": data.selection["source_sha256"],
        "selection_hashes": {
            name: hashlib.sha256(
                (example / "selections" / name).read_bytes()
            ).hexdigest()
            for name in ("outer-band.json", "top-face.json")
        },
        "implementation_hashes": {
            name: hashlib.sha256(
                Path(__file__).with_name(name).read_bytes()
            ).hexdigest()
            for name in (
                "mesh_cylinder_fit.py",
                "mesh_cylinder_plane_fit.py",
                "run_nozzle_cylinder.py",
                "run_nozzle_cylinder_plane.py",
                "nozzle_joint_view.py",
            )
        },
        "numpy_version": np.__version__,
        "constraint": "plane normal is exactly the same unit vector as cylinder axis; no penalty residual",
        "weighting": "whole-mesh incident triangle area/3; one global normalization across both fixed selections; no per-surface rebalancing or residual trimming",
        "counts": {"cylinder": len(data.ids), "plane": len(plane_ids)},
        "selected_area": {"cylinder": float(cw.sum()), "plane": float(pw.sum())},
        "frame": data.selection["fit_frame"],
        "initial_parameters": initial.tolist(),
        "multistart_initial_parameters": [s.tolist() for s in starts],
        "cylinder_only": cylinder_only,
        "joint_fit": results[0],
        "multistart": results,
        "multistart_max_parameter_difference": float(
            np.max(np.abs(np.array([r["parameters"] for r in results]) - parameters))
        ),
        "diameter": float(2 * parameters[4]),
        "diameter_change": float(2 * (parameters[4] - cp[4])),
        "axis_change_degrees": float(
            np.degrees(
                np.arctan2(np.linalg.norm(np.cross(axis, old_axis)), axis @ old_axis)
            )
        ),
        "axis_world": world_axis.tolist(),
        "axis_point_world": point.tolist(),
        "plane_normal_world": world_axis.tolist(),
        "plane_offset_world": float(plane_offset),
        "plane_axis_intersection_world": plane_point.tolist(),
        "fixed_cylinder_plane_rms": float(
            np.sqrt(pw @ (fixed_residual[len(cylinder) :] ** 2) / pw.sum())
        ),
        "fixed_cylinder_combined_rms": float(np.sqrt(combined @ (fixed_residual**2))),
        "unconstrained_plane_rms": float(np.sqrt(max(0, float(eigenvalues[0])))),
        "world_residual_max_difference": float(
            np.max(np.abs(residual - world_residual))
        ),
    }
    output.mkdir(parents=True, exist_ok=False)
    _ = (output / "fit.json").write_text(json.dumps(report, indent=2) + "\n")
    np.savez(
        output / "residuals.npz",
        cylinder_ids=data.ids,
        plane_ids=plane_ids,
        cylinder_area=cw,
        plane_area=pw,
        cylinder_residual=residual[: len(cylinder)],
        plane_residual=residual[len(cylinder) :],
    )
    export_view(
        output,
        data,
        plane_ids,
        residual,
        world_axis,
        point,
        plane_point,
        float(parameters[4]),
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--example", type=Path, default=Path("examples/nozzle-bayonette-simplified")
    )
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.example, args.output)
