"""Project harness: freeze input bytes and use exact packaged Python sources."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

FROZEN = {
    "raw-nozzle": "537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358",
    "horizontal-scene": "9666be32b5afe9df2b34a496c455339687b53c786fd0429700d0668ef0c586e1",
    "grid-256x128": "e453e179be254e6a16b497920dd482d2299315e8769eefa9b68c8d19dd59075c",
    "grid-512x256": "5555f879e1792e7e889036f545a1eb1d5bd2384047837065d20d9d7cfb9def4d",
}


def inputs(repository: Path, output: Path) -> None:
    helper = repository / "tests/native_scan_fixture.py"
    specification = importlib.util.spec_from_file_location(
        "frozen_scan_fixture", helper
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    scene = module.native_scene_fixture()
    cases = {
        "raw-nozzle": (
            (
                repository
                / "examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply"
            ).read_bytes(),
            24_999,
            49_994,
        ),
        "horizontal-scene": (scene.raw, len(scene.positions), len(scene.triangles)),
    }
    for name, nx, ny in (("grid-256x128", 256, 128), ("grid-512x256", 512, 256)):
        generated = module.native_grid_fixture(nx, ny)
        cases[name] = (
            generated.raw,
            len(generated.positions),
            len(generated.triangles),
        )
    records: list[dict[str, Any]] = []
    for name, (raw, vertices, faces) in cases.items():
        digest = hashlib.sha256(raw).hexdigest()
        if digest != FROZEN[name]:
            raise ValueError(f"Preregistered source bytes changed: {name}")
        _ = (output / f"{name}.ply").write_bytes(raw)
        records.append(
            {
                "name": name,
                "sha256": digest,
                "source_bytes": len(raw),
                "vertices": vertices,
                "faces": faces,
            }
        )
    oracle = {
        key: getattr(scene, key).tolist()
        for key in ("positions", "normals", "triangles", "weights")
    }
    oracle["selections"] = scene.selections
    oracle["truth"] = {
        "cylinder_axis": [1, 0, 0],
        "cylinder_point": [3, -4, 5],
        "cylinder_radius": 2.5,
        "plane_equation": [1, 0, 0, 8],
    }
    _ = (output / "horizontal-scene.expected.json").write_text(
        json.dumps(oracle, allow_nan=False) + "\n"
    )
    _ = (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
                "cases": records,
            },
            indent=2,
        )
        + "\n"
    )


def expected(runtime: Path, sources: Path, output: Path) -> None:
    manifest = json.loads((runtime / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(
        prefix="source-reference-", dir=output.parent
    ) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(runtime / "scansor.zip") as archive:
            if any(
                not (root / name).resolve().is_relative_to(root)
                for name in archive.namelist()
            ):
                raise ValueError("Packaged source escapes reference directory")
            archive.extractall(root)
        sys.path.insert(0, str(root))
        from experiments.feature_graph import Recipe
        from experiments.scan_project import ScanProject
        from experiments.scan_workspace import ScanLimits

        results: dict[str, Any] = {}
        for name in ("raw-nozzle", "horizontal-scene"):
            project = ScanProject()
            active = project.accepted
            raw = (sources / f"{name}.ply").read_bytes()
            candidate = project.prepare_add(
                io.BytesIO(raw),
                f"{name}.ply",
                ScanLimits(**manifest["worker_protocol"]["source_import"]["limits"]),
                generation=active.generation,
                token=str(active.graph.snapshot()["token"]),
            )
            active = project.commit(candidate)
            workspace = active.workspace
            evidence = {
                "descriptor": workspace.descriptor.model_dump()
                if workspace.descriptor
                else None,
                "positions": workspace.local.tolist(),
                "normals": workspace.data.normals.tolist(),
                "triangles": workspace.data.triangles.tolist(),
                "weights": workspace.data.weights.tolist(),
            }
            graph: Any = active.graph.snapshot()
            if name == "horizontal-scene":
                oracle = json.loads(
                    (sources / "horizontal-scene.expected.json").read_text()
                )
                recipe = graph["recipe"]
                for kind, ids in oracle["selections"].items():
                    recipe["nodes"].extend(
                        [
                            {
                                "id": kind + "_selection",
                                "label": kind + " selection",
                                "operation": "selection",
                                "source": "scan",
                                "ids": ids,
                            },
                            {
                                "id": kind,
                                "label": kind,
                                "operation": "fit",
                                "kind": kind,
                                "selections": [kind + "_selection"],
                            },
                        ]
                    )
                recipe["output"] = "cylinder"
                graph = active.graph.replace(
                    Recipe.model_validate(recipe), str(graph["token"])
                )
                graph = active.graph.evaluate(str(graph["token"]), all_actions=True)
                evidence["graph"] = graph
            results[name] = evidence
        _ = output.write_text(json.dumps(results, allow_nan=False) + "\n")


def authored(runtime: Path, sources: Path, recipes_path: Path, output: Path) -> None:
    """Evaluate actual UI-authored recipes using the identical packaged core."""
    manifest = json.loads((runtime / "manifest.json").read_text())
    recipes = json.loads(recipes_path.read_text())
    names = {
        "raw": "raw-nozzle",
        "raw_edited": "raw-nozzle",
        "scene": "horizontal-scene",
    }
    if set(recipes) != set(names):
        raise ValueError("Incomplete UI-authored recipe coverage")
    with tempfile.TemporaryDirectory(
        prefix="source-authored-reference-", dir=output.parent
    ) as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(runtime / "scansor.zip") as archive:
            if any(
                not (root / name).resolve().is_relative_to(root)
                for name in archive.namelist()
            ):
                raise ValueError("Packaged source escapes reference directory")
            archive.extractall(root)
        sys.path.insert(0, str(root))
        from experiments.feature_graph import Recipe
        from experiments.scan_project import ScanProject
        from experiments.scan_workspace import ScanLimits

        results: dict[str, Any] = {}
        for name, source in names.items():
            raw = (sources / f"{source}.ply").read_bytes()
            if hashlib.sha256(raw).hexdigest() != FROZEN[source]:
                raise ValueError("UI-authored reference source changed")
            project = ScanProject()
            empty = project.accepted
            prepared = project.prepare_add(
                io.BytesIO(raw),
                f"{source}.ply",
                ScanLimits(**manifest["worker_protocol"]["source_import"]["limits"]),
                generation=empty.generation,
                token=str(empty.graph.snapshot()["token"]),
            )
            accepted = project.commit(prepared)
            before = accepted.graph.snapshot()
            graph = accepted.graph.replace(
                Recipe.model_validate(recipes[name]), str(before["token"])
            )
            try:
                _ = accepted.graph.evaluate(str(graph["token"]), all_actions=True)
            finally:
                graph = cast(
                    dict[str, Any],
                    accepted.graph.readiness_snapshot(
                        str(graph["token"]), all_actions=True
                    ),
                )
                # A failed native reference is evidence too. Keep the complete
                # observation and earlier cases before rejecting this run.
                results[name] = graph
                _ = output.write_text(json.dumps(results, allow_nan=False) + "\n")
            if (
                graph["errors"]
                or graph["required_failures"]
                or graph.get("evaluation_running", False) is not False
                or not graph["states"]
                or any(state != "ready" for state in graph["states"].values())
            ):
                raise ValueError(f"Native UI-authored reference failed: {name}")


def cad_readback(bundle: Path, reference: Path, output: Path) -> None:
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_Reader
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    expected_geometry = json.loads(reference.read_text())["horizontal-scene"]["graph"][
        "results"
    ]["cylinder"]["analytic"]
    with zipfile.ZipFile(bundle) as archive:
        raw = archive.read("model.step")
    with tempfile.TemporaryDirectory(
        prefix="source-step-", dir=output.parent
    ) as temporary:
        step = Path(temporary) / "model.step"
        _ = step.write_bytes(raw)
        reader = STEPControl_Reader()
        read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
        if read(str(step)) != IFSelect_RetDone or reader.TransferRoots() != 1:
            raise ValueError("Imported source STEP could not be reopened")
        shape = reader.OneShape()
        if not BRepCheck_Analyzer(shape).IsValid():
            raise ValueError("Imported source STEP is invalid")
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        if not explorer.More():
            raise ValueError("Imported source STEP has no face")
        face = TopoDS.Face(explorer.Current())
        explorer.Next()
        if explorer.More():
            raise ValueError("Imported cylinder STEP has unexpected extra faces")
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() != GeomAbs_Cylinder:
            raise ValueError("Imported source STEP changed its analytic family")
        u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
        points = np.array(
            [
                surface.Value(u, v).Coord()
                for v in (v0, v1)
                for u in np.linspace(u0, u1, 7)
            ]
        )
        direction = np.array(expected_geometry["axis"])
        relative = points - expected_geometry["origin"]
        heights = relative @ direction
        radii = np.linalg.norm(relative - heights[:, None] * direction, axis=1)
        np.testing.assert_allclose(
            [heights.min(), heights.max()],
            expected_geometry["axial_domain"],
            rtol=0,
            atol=1e-7,
        )
        np.testing.assert_allclose(
            radii, expected_geometry["radius"], rtol=0, atol=1e-7
        )
        _ = output.write_text(
            json.dumps(
                {
                    "valid": True,
                    "family": "cylinder",
                    "axial_bounds": [float(heights.min()), float(heights.max())],
                    "radius": float(surface.Cylinder().Radius()),
                },
                allow_nan=False,
            )
            + "\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("mode", choices=("inputs", "expected", "authored", "cad"))
    _ = parser.add_argument("--repository", type=Path)
    _ = parser.add_argument("--runtime", type=Path)
    _ = parser.add_argument("--inputs", type=Path)
    _ = parser.add_argument("--recipes", type=Path)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "inputs":
        assert args.repository is not None
        inputs(args.repository, args.output)
    elif args.mode == "expected":
        assert args.runtime is not None and args.inputs is not None
        expected(args.runtime, args.inputs, args.output)
    elif args.mode == "authored":
        assert args.runtime is not None and args.inputs is not None
        assert args.recipes is not None
        authored(args.runtime, args.inputs, args.recipes, args.output)
    else:
        assert args.runtime is not None and args.inputs is not None
        cad_readback(args.runtime, args.inputs, args.output)


if __name__ == "__main__":
    main()
