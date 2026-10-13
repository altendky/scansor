"""Disposable cancellation fixture using only a built Scansor runtime artifact."""

import hashlib
import importlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import OCP
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Face

from experiments.mesh_cone_plane_fit import fit_cone_plane
from experiments.mesh_cylinder_fit import Array
from experiments.ocp_geometry import surface_patch, tessellate_face

# Pyodide's built-in JS bridge is intentionally unavailable in native Python.
js: Any = importlib.import_module("js")

reference = json.loads(Path("/native-results.json").read_text())
with np.load("/fixture.npz", allow_pickle=False) as data:
    fixture = {key: value.copy() for key, value in data.items()}
large = {
    key: np.tile(value, (300, 1)) if value.ndim == 2 else np.tile(value, 300)
    for key, value in fixture.items()
    if key != "initial"
}
large["initial"] = fixture["initial"]
completed = 0
recipe_json = json.dumps(reference["graph"]["recipe"], sort_keys=True)
_ = Path("/request-marker.txt").write_text("initialized")


def state() -> dict[str, Any]:
    return {
        "completed": completed,
        "source_sha256": reference["source_sha256"],
        "model_sha256": reference["model_sha256"],
        "committed_recipe_sha256": hashlib.sha256(recipe_json.encode()).hexdigest(),
        "virtual_file_contents": Path("/request-marker.txt").read_text(),
    }


def fit(data: dict[str, Array]) -> dict[str, Any]:
    global completed
    result = fit_cone_plane(
        data["cone"],
        data["plane"],
        data["cw"],
        data["pw"],
        data["initial"],
        (-3, 6),
    )
    np.testing.assert_allclose(
        result["parameters"],
        reference["synthetic"]["fit"]["parameters"],
        rtol=1e-10,
        atol=1e-10,
    )
    assert "solver" in result
    completed += 1
    return {"parameters": result["parameters"], "solver": result["solver"]}


def small_fit() -> dict[str, Any]:
    return fit(fixture)


def sphere() -> TopoDS_Face:
    return surface_patch(
        {"kind": "sphere", "ids": [0, 1, 2], "parameters": [0, 0, 0, 10]},
        np.array([[0.0, 0.0, 10.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]]),
    )


def cad_actual() -> dict[str, int]:
    global completed
    result = tessellate_face(sphere(), 1.0)
    completed += 1
    return {
        "vertices": len(result["positions"]) // 3,
        "triangles": len(result["indices"]) // 3,
    }


def cad_native() -> dict[str, Any]:
    global completed
    shape = sphere()
    # This is the same default mesher call Scansor uses. It does not use an
    # OCCT Message_ProgressRange/UserBreak callback or introduce another binding.
    js.postMessage(js.JSON.parse(json.dumps({"type": "native-enter"})))
    started = time.perf_counter()
    mesh = BRepMesh_IncrementalMesh(shape, 0.001, False, 0.1, False)
    elapsed = time.perf_counter() - started
    completed += 1
    triangulation = BRep_Tool.Triangulation_s(TopoDS.Face(shape), TopLoc_Location())
    return {
        "native_seconds": elapsed,
        "done": mesh.IsDone(),
        "triangles": triangulation.NbTriangles(),
    }


def run_job(job: str, count: int) -> dict[str, Any]:
    _ = Path("/request-marker.txt").write_text("in-flight disposable mutation")
    callback = {
        "small": small_fit,
        "large": lambda: fit(large),
        "cad_actual": cad_actual,
        "cad_native": cad_native,
    }[job]
    started = time.perf_counter()
    result: dict[str, Any] | None = None
    for _ in range(count):
        result = callback()
    if result is None:
        raise ValueError("probe count must be positive")
    return {"seconds": time.perf_counter() - started, "result": result, "count": count}


versions = {
    "python": __import__("platform").python_version(),
    "numpy": np.__version__,
    "pydantic": __import__("pydantic").__version__,
    "ocp": OCP.__version__,
}
