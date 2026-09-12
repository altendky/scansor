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
from scansor._plyio import Reader, build_layout, read_header


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
    source = example / "nozzle-bayonette-simplified.ply"
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
        layout = build_layout(
            read_header(stream), fixed_lists={("face", "vertex_indices"): 3}
        )
        reader = Reader(stream, layout, max_range_bytes=2_000_000)
        count = layout.element("vertex").element.count
        rows = reader.read_range("vertex", 0, count)
        faces = reader.read_range("face", 0, layout.element("face").element.count)
    triangles = faces["vertex_indices"]["values"]
    xyz = np.column_stack([rows[k] for k in ("x", "y", "z")]).astype(float)
    normals = np.column_stack([rows[k] for k in ("nx", "ny", "nz")]).astype(float)
    if not np.isfinite(xyz).all() or not np.isfinite(normals).all():
        raise ValueError("nonfinite geometry")
    if triangles.min() < 0 or triangles.max() >= count:
        raise ValueError("out-of-range triangle index")
    if len(ids) != selection["selected_vertices"] or not np.array_equal(
        ids, np.unique(ids)
    ):
        raise ValueError("unexpected selection count/order")
    if ids.min() < 0 or ids.max() >= count:
        raise ValueError("out-of-range selected vertex")
    a, b, c = (xyz[triangles[:, i]] for i in range(3))
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
    weights = np.bincount(
        triangles.ravel(), weights=np.repeat(areas / 3, 3), minlength=count
    ).astype(np.float64, copy=False)
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 0):
        raise ValueError("zero normal")
    normals /= lengths[:, None]
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
        int(np.count_nonzero(areas == 0)),
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
