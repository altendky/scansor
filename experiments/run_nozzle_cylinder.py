"""Replay the saved reduced-nozzle selection and exploratory cylinder fit."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from experiments.mesh_cylinder_fit import fit_cylinder, residual_jacobian
from experiments.scan_workspace import ScanLimits, prepare_scan


@dataclass(frozen=True)
class NozzleExample:
    xyz: NDArray[np.float64]
    normals: NDArray[np.float64]
    triangles: NDArray[np.int32]
    weights: NDArray[np.float64]
    ids: NDArray[np.int64]
    selection: dict[str, Any]
    selection_path: Path
    zero_area_faces: int


def selection_azimuth_mask(
    xyz: NDArray[np.float64], selection: dict[str, Any]
) -> NDArray[np.bool_]:
    """Apply an optional non-wrapping fit-frame azimuth gate."""
    bounds = selection.get("azimuth_range_degrees")
    if bounds is None:
        return np.ones(len(xyz), dtype=np.bool_)
    if (
        not isinstance(bounds, list)
        or len(bounds) != 2
        or not 0.0 <= float(bounds[0]) <= float(bounds[1]) <= 360.0
    ):
        raise ValueError("invalid selection azimuth range")
    azimuth_frame = selection.get("azimuth_frame", selection.get("fit_frame"))
    if not isinstance(azimuth_frame, dict):
        raise ValueError("an azimuth-gated selection requires a fit frame")
    local = (xyz - np.asarray(azimuth_frame["origin"], dtype=float)) @ np.asarray(
        azimuth_frame["columns"], dtype=float
    )
    angle = np.mod(np.degrees(np.arctan2(local[:, 1], local[:, 0])), 360.0)
    return (angle >= float(bounds[0])) & (angle <= float(bounds[1]))


def load_example(example: Path) -> NozzleExample:
    """Verify source and cylinder selection, returning full-mesh geometry/areas."""
    manifest = json.loads((example / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        with (example / name).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected["sha256"]:
            raise ValueError(f"example source hash mismatch: {name}")
    selection_path = example / manifest["selection"]
    selection = json.loads(selection_path.read_text())
    source = example / manifest.get("source_file", "nozzle-bayonette-simplified.ply")
    if selection["source_sha256"] != manifest["files"][source.name]["sha256"]:
        raise ValueError("selection refers to another source")
    ids_path = selection_path.parent / selection["vertex_ids_file"]
    if (
        hashlib.sha256(ids_path.read_bytes()).hexdigest()
        != selection["vertex_ids_sha256"]
    ):
        raise ValueError("saved selection hash mismatch")
    ids = np.loadtxt(ids_path, dtype=np.int64, ndmin=1)
    with source.open("rb") as stream:
        size = source.stat().st_size
        prepared = prepare_scan(
            stream,
            source.name,
            ScanLimits(
                max_source_bytes=size,
                max_vertices=max(1, size // 12),
                max_faces=size // 13,
                max_estimated_bytes=32 * size + 4 * 1024 * 1024,
            ),
        )
    if prepared.source_sha256 != selection["source_sha256"]:
        raise ValueError("example source changed during preparation")
    # Preserve the legacy experiment adapter's mutable numerical fixture arrays.
    # BrowserWorkspace freezes them at its boundary; generic sources already own
    # immutable arrays and never acquire fixture selection/model metadata.
    xyz, normals = prepared.data.xyz.copy(), prepared.data.normals.copy()
    triangles, weights = prepared.data.triangles, prepared.data.weights.copy()
    count = len(xyz)
    if len(ids) != selection["selected_vertices"] or not np.array_equal(
        ids, np.unique(ids)
    ):
        raise ValueError("unexpected selection count/order")
    if ids.min() < 0 or ids.max() >= count:
        raise ValueError("out-of-range selected vertex")
    seed = selection["axis_seed"]
    axis = np.array(seed["axis"])
    q = xyz - seed["center"]
    z = q @ axis
    radial = q - z[:, None] * axis
    rho = np.linalg.norm(radial, axis=1)
    outward = np.divide(
        np.sum(normals * radial, axis=1),
        rho,
        out=np.zeros_like(rho),
        where=rho > 0,
    )
    selected = (
        (z >= selection["axial_range"][0])
        & (z <= selection["axial_range"][1])
        & (rho >= selection["radius_range"][0])
        & (rho <= selection["radius_range"][1])
        & (outward >= selection["minimum_outward_normal_dot"])
        & (np.abs(normals @ axis) <= selection["maximum_abs_axial_normal_dot"])
        & selection_azimuth_mask(xyz, selection)
        & (weights > 0)
    )
    if not np.array_equal(np.flatnonzero(selected), ids):
        raise ValueError("geometric gates do not reproduce the saved selection")
    return NozzleExample(
        xyz,
        normals,
        triangles,
        weights,
        ids,
        selection,
        selection_path,
        prepared.diagnostics.repeated_index_faces
        + prepared.diagnostics.zero_area_faces,
    )


def run(example: Path, output: Path) -> None:
    """Check source/selection identity, recompute mesh areas, then fit all saved IDs."""
    data = load_example(example)
    xyz, weights, ids = data.xyz, data.weights, data.ids
    selection, selection_path = data.selection, data.selection_path
    frame = np.array(selection["fit_frame"]["columns"])
    origin = np.array(selection["fit_frame"]["origin"])
    points = (xyz[ids] - origin) @ frame
    initial = np.array(selection["initial_parameters"], dtype=float)
    result = fit_cylinder(points, weights[ids], initial)
    parameters = np.array(result["parameters"])
    residuals, _ = residual_jacobian(points, parameters)
    fit_axis = frame @ np.array([parameters[2], parameters[3], 1.0])
    fit_axis /= np.linalg.norm(fit_axis)
    report = {
        "status": "exploratory; unknown units; no physical accuracy claim",
        "source_sha256": selection["source_sha256"],
        "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        "solver_sha256": hashlib.sha256(
            Path(__file__).with_name("mesh_cylinder_fit.py").read_bytes()
        ).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_version": np.__version__,
        "selected_vertices": len(ids),
        "zero_area_faces": data.zero_area_faces,
        "fit": result,
        "diameter": float(2 * parameters[4]),
        "axis_world": fit_axis.tolist(),
        "axis_point_world": (
            origin + frame @ np.array([parameters[0], parameters[1], 0])
        ).tolist(),
    }
    # A new directory prevents accidentally overwriting an earlier result.
    output.mkdir(parents=True, exist_ok=False)
    _ = (output / "fit.json").write_text(json.dumps(report, indent=2) + "\n")
    np.savez(
        output / "residuals.npz",
        source_vertex_ids=ids,
        area=weights[ids],
        residual=residuals,
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
