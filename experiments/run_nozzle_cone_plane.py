"""Fit the saved nozzle as a finite cone side and perpendicular top plane."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from experiments.mesh_cone_plane_fit import cone_plane_residual_jacobian, fit_cone_plane
from experiments.mesh_cylinder_fit import fit_cylinder
from experiments.mesh_cylinder_plane_fit import (
    fit_cylinder_plane,
    joint_residual_jacobian,
)
from experiments.nozzle_joint_view import export_view
from experiments.run_nozzle_cylinder import load_example
from experiments.run_nozzle_cylinder_plane import load_plane_ids


def run(example: Path, output: Path) -> None:
    data = load_example(example)
    plane_ids = load_plane_ids(example, data)
    model_path = example / "models/cone-plane.json"
    model = json.loads(model_path.read_text())
    domain = tuple(float(x) for x in model["axial_domain"])
    if len(domain) != 2:
        raise ValueError("expected two axial support endpoints")
    support = (domain[0], domain[1])
    frame = np.array(data.selection["fit_frame"]["columns"])
    origin = np.array(data.selection["fit_frame"]["origin"])
    lateral = (data.xyz[data.ids] - origin) @ frame
    plane = (data.xyz[plane_ids] - origin) @ frame
    cw = data.weights[data.ids]
    pw = data.weights[plane_ids]
    cp = np.array(
        fit_cylinder(
            lateral, cw, np.array(data.selection["initial_parameters"], dtype=float)
        )["parameters"]
    )
    axis0 = np.array([cp[2], cp[3], 1.0])
    axis0 /= np.linalg.norm(axis0)
    h = float(pw @ (plane @ axis0) / pw.sum())
    baseline = fit_cylinder_plane(lateral, plane, cw, pw, np.concatenate((cp, [h])))
    bp = np.array(baseline["parameters"])
    initial = np.concatenate((bp, [0.0]))
    starts = [
        initial,
        initial + np.array([0.1, -0.1, 0.02, -0.02, 0.1, 0.1, 0.02]),
        initial + np.array([-0.1, 0.1, -0.02, 0.02, -0.1, -0.1, -0.02]),
    ]
    results = [
        fit_cone_plane(lateral, plane, cw, pw, start, support) for start in starts
    ]
    p = np.array(results[0]["parameters"])
    residual, _ = cone_plane_residual_jacobian(lateral, plane, p, support)
    baseline_residual, _ = joint_residual_jacobian(lateral, plane, bp)
    local_axis = np.array([p[2], p[3], 1.0])
    local_axis /= np.linalg.norm(local_axis)
    axis = frame @ local_axis
    point = origin + frame @ np.array([p[0], p[1], 0.0])
    offset = float(p[5] + axis @ origin)
    plane_z = float(offset - axis @ point)
    plane_point = point + plane_z * axis
    if not support[0] <= plane_z <= support[1]:
        raise ValueError(
            "plane intersection lies outside the declared display/support interval"
        )
    q = data.xyz[data.ids] - point
    z = q @ axis
    rho = np.linalg.norm(q - z[:, None] * axis, axis=1)
    radial = rho - (p[4] + p[6] * z)
    normal = radial / np.hypot(1, p[6])
    world = np.concatenate((normal, data.xyz[plane_ids] @ axis - offset))
    np.testing.assert_allclose(world, residual, rtol=1e-9, atol=2e-14)
    # Independently construct nearest side points and verify Euclidean distances.
    projected_z = (z + p[6] * (rho - p[4])) / (1 + p[6] ** 2)
    projected = (
        point
        + projected_z[:, None] * axis
        + (p[4] + p[6] * projected_z)[:, None] * (q - z[:, None] * axis) / rho[:, None]
    )
    euclidean = np.linalg.norm(data.xyz[data.ids] - projected, axis=1)
    np.testing.assert_allclose(euclidean, np.abs(normal), rtol=1e-8, atol=2e-14)
    ba = np.array([bp[2], bp[3], 1.0])
    ba /= np.linalg.norm(ba)
    # Common spatial bins use the unchanged selection reference frame for both fits.
    seed = data.selection["axis_seed"]
    seed_z = (data.xyz[data.ids] - seed["center"]) @ np.array(seed["axis"])
    edges = np.linspace(
        float(data.selection["axial_range"][0]),
        float(data.selection["axial_range"][1]),
        17,
    )
    bins = np.clip(np.searchsorted(edges, seed_z, side="right") - 1, 0, 15)
    profiles: list[dict[str, object]] = []
    for i in range(16):
        mask = bins == i
        area = cw[mask]
        count = int(np.count_nonzero(mask))
        profiles.append(
            {
                "axial_range": edges[i : i + 2].tolist(),
                "count": count,
                "selected_area": float(area.sum()),
                "cylinder_mean_normal_residual": float(
                    area @ baseline_residual[: len(lateral)][mask] / area.sum()
                )
                if count
                else None,
                "cone_mean_normal_residual": float(area @ normal[mask] / area.sum())
                if count
                else None,
            }
        )
    report = {
        "status": "exploratory captured cone-side/plane fit; unknown units; lower training error is not physical validation",
        "model": model,
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
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
                "mesh_cone_plane_fit.py",
                "run_nozzle_cylinder.py",
                "run_nozzle_cylinder_plane.py",
                "run_nozzle_cone_plane.py",
                "nozzle_joint_view.py",
            )
        },
        "numpy_version": np.__version__,
        "frame": data.selection["fit_frame"],
        "counts": {"lateral": len(lateral), "plane": len(plane)},
        "selected_area": {"lateral": float(cw.sum()), "plane": float(pw.sum())},
        "cylinder_plane_baseline": baseline,
        "cone_plane_fit": results[0],
        "multistart": results,
        "multistart_initial_parameters": [s.tolist() for s in starts],
        "multistart_max_parameter_difference": float(
            np.max(np.abs(np.array([r["parameters"] for r in results]) - p))
        ),
        "reference_diameter": float(2 * p[4]),
        "top_plane_diameter": float(2 * (p[4] + p[6] * plane_z)),
        "taper": float(p[6]),
        "signed_half_angle_degrees": float(np.degrees(np.arctan(p[6]))),
        "axis_change_from_joint_cylinder_degrees": float(
            np.degrees(
                np.arctan2(np.linalg.norm(np.cross(ba, local_axis)), ba @ local_axis)
            )
        ),
        "axis_world": axis.tolist(),
        "axis_point_world": point.tolist(),
        "plane_normal_world": axis.tolist(),
        "plane_offset_world": offset,
        "plane_axis_intersection_world": plane_point.tolist(),
        "cone_radial_rms": float(np.sqrt(cw @ (radial**2) / cw.sum())),
        "world_residual_max_difference": float(np.max(np.abs(world - residual))),
        "orthogonal_distance_max_difference": float(
            np.max(np.abs(euclidean - np.abs(normal)))
        ),
        "axial_profile": profiles,
    }
    output.mkdir(parents=True, exist_ok=False)
    _ = (output / "fit.json").write_text(json.dumps(report, indent=2) + "\n")
    np.savez(
        output / "residuals.npz",
        lateral_ids=data.ids,
        plane_ids=plane_ids,
        lateral_area=cw,
        plane_area=pw,
        cone_normal_residual=normal,
        cone_radial_residual=radial,
        plane_residual=residual[len(lateral) :],
        cylinder_normal_residual=baseline_residual[: len(lateral)],
        cylinder_plane_residual=baseline_residual[len(lateral) :],
    )
    export_view(
        output,
        data,
        plane_ids,
        residual,
        axis,
        point,
        plane_point,
        float(p[4]),
        taper=float(p[6]),
        lateral_kind="cone",
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
