"""The same actual Scansor fitting/graph/CAD probe in native Python and Pyodide."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pydantic

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.mesh_cone_plane_fit import (
    cone_plane_residual_jacobian,
    fit_cone_plane,
)
from experiments.nozzle_session import NozzleWorkspace

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
    np.savez(
        path,
        cone=cone,
        plane=plane,
        cw=np.linspace(0.1, 2, len(cone)),
        pw=np.linspace(0.2, 1, len(plane)),
        initial=np.array([0.0, 0.0, 0.0, 0.0, 3.0, 4.0, 0.0]),
    )


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
            "mesh_vertices": len(workspace.local),
            "mesh_triangles": len(workspace.data.triangles),
            "nozzle_cone": len(workspace.default.lateral_ids),
            "nozzle_plane": len(workspace.default.plane_ids),
        },
        "source_sha256": workspace.default.source_sha256,
        "model_sha256": workspace.model_sha256,
        "synthetic": {"fit": synthetic, "residuals": residuals.tolist()},
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


def compare_numeric(actual: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    for key in ("fixture_sha256", "counts", "source_sha256", "model_sha256"):
        assert actual[key] == expected[key], f"{key} mismatch"
    graph, reference = actual["graph"], expected["graph"]
    for key in ("recipe", "token", "states", "errors", "diagnostics"):
        assert graph[key] == reference[key], f"graph {key} mismatch"
    compare_values(graph["results"], reference["results"], "graph.results")
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
