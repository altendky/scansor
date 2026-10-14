"""Prepare explicit generated test artifacts without changing supported sources.

Called by measure-generated.mjs with an existing native project interpreter.
Generation uses the existing repeated-boss fixture; native parity loads the exact
remapped production source archive in a fresh interpreter.
"""

from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, cast

FIXED = "examples/nozzle-bayonette-simplified/"
SCALES = (1, 2, 4)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_bytes()))


def checked_path(root: Path, relative: str) -> Path:
    if (
        not relative
        or re.search(r"[\\?#:\x00]", relative)
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or Path(relative).is_absolute()
    ):
        raise ValueError(f"invalid artifact path: {relative!r}")
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"artifact path escapes root: {relative}")
    return path


def checked_bytes(root: Path, record: dict[str, Any]) -> bytes:
    data = checked_path(root, record["path"]).read_bytes()
    if len(data) != record["bytes"] or digest(data) != record["sha256"]:
        raise ValueError(f"asset integrity failure: {record['path']}")
    return data


def source_archive(
    data: bytes, records: list[dict[str, Any]], root: Path
) -> dict[str, bytes]:
    declared = [record["archive_path"] for record in records]
    if len(declared) != len(set(declared)):
        raise ValueError("duplicate source provenance member")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate source archive member")
        for name in names:
            _ = checked_path(root, name)
        if set(names) != set(declared):
            raise ValueError("source archive members differ from source provenance")
        source = {name: archive.read(name) for name in names}
    for record in records:
        if digest(source[record["archive_path"]]) != record["sha256"]:
            raise ValueError(f"source provenance mismatch: {record['archive_path']}")
    return source


def write_files(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir()
    for relative, data in files.items():
        path = checked_path(root, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)


def project_module_origins(
    root: Path, packaged_source: dict[str, bytes]
) -> list[dict[str, Any]]:
    """Reject cached/editable project imports from another checkout."""
    root = root.resolve()
    records: list[dict[str, Any]] = []
    for name, module in sorted(sys.modules.items()):
        if name not in ("scansor", "experiments") and not name.startswith(
            ("scansor.", "experiments.")
        ):
            continue
        for package_path in getattr(module, "__path__", ()):
            if not Path(package_path).resolve().is_relative_to(root):
                raise ValueError(f"project package loaded from another tree: {name}")
        filename = getattr(module, "__file__", None)
        if filename is None:
            continue
        origin = Path(filename).resolve()
        if not origin.is_relative_to(root):
            raise ValueError(
                f"project module loaded from another tree: {name}: {origin}"
            )
        relative = origin.relative_to(root).as_posix()
        archive_path = relative.removeprefix("src/")
        actual = digest(origin.read_bytes())
        expected = packaged_source.get(archive_path)
        if expected is not None and actual != digest(expected):
            raise ValueError(f"loaded project module differs from artifact: {name}")
        records.append(
            {
                "module": name,
                "source_path": relative,
                "sha256": actual,
                "matches_packaged_source": expected is not None,
            }
        )
    return records


def prepare(repository: Path, site: Path, output: Path) -> list[dict[str, Any]]:
    """Make fresh generated copies; production code bytes must match the input."""
    repository = repository.resolve()
    sys.path[:0] = [str(repository / "src"), str(repository)]
    import numpy as np
    import pydantic

    from experiments.feature_graph import (
        Feature,
        Recipe,
        Selection,
        Source,
        SurfaceFit,
        workspace_reference_sha256,
    )
    from experiments.nozzle_session import NozzleWorkspace
    from experiments.repeated_boss_fixture import GENERATOR_REVISION, publish_fixture
    from scansor.serialization import canonical_json

    site_manifest = read_json(site / "manifest.json")
    site_files = {
        record["path"]: checked_bytes(site, record)
        for record in site_manifest["assets"]
    }
    runtime_prefix: str = site_manifest["runtime"]["path"]
    runtime_bytes = site_files[runtime_prefix + "/manifest.json"]
    if digest(runtime_bytes) != site_manifest["runtime"]["manifest_sha256"]:
        raise ValueError("runtime manifest identity mismatch")
    original: dict[str, Any] = json.loads(runtime_bytes)
    if original["worker_protocol"]["version"] != 4:
        raise ValueError("expected the supported production worker protocol")
    runtime_files = {
        record["path"]: checked_bytes(site / runtime_prefix, record)
        for record in original["assets"]
    }
    archive_bytes = runtime_files["scansor.zip"]
    source = source_archive(archive_bytes, original["source_files"], output)
    source_proof: list[dict[str, str]] = []
    for record in original["source_files"]:
        name: str = record["archive_path"]
        _ = checked_path(output, name)
        if digest(source[name]) != record["sha256"]:
            raise ValueError(f"source provenance mismatch: {name}")
        if not name.startswith(FIXED):
            # Preparation uses this repository's schema/loader. Refuse a
            # differently versioned checkout rather than mix implementations.
            current = checked_path(repository, record["path"]).read_bytes()
            if digest(current) != record["sha256"]:
                raise ValueError(
                    f"preparation repository differs from artifact: {name}"
                )
            source_proof.append({"archive_path": name, "sha256": record["sha256"]})
    _ = project_module_origins(repository, source)
    original_recipe = Recipe.model_validate_json(
        source[FIXED + "recipes/cone-plane.json"]
    )
    definition = repository / "examples/repeated-boss-selection/fixture.json"
    generator = repository / "experiments/repeated_boss_fixture.py"
    cases = output / "cases"
    cases.mkdir()
    reports: list[dict[str, Any]] = []
    for scale in SCALES:
        started = time.perf_counter()
        case = cases / str(scale)
        case.mkdir()
        generation = publish_fixture(
            case / "generated",
            definition,
            realization_ids=("scan-coarse",),
            tessellation_scale=scale,
        )
        fixture = case / "generated/scan-coarse"
        workspace = NozzleWorkspace(fixture)
        nodes: list[Feature] = []
        for node in original_recipe.nodes:
            if isinstance(node, Source):
                node = node.model_copy(
                    update={
                        "source_sha256": workspace.default.source_sha256,
                        "reference_sha256": workspace_reference_sha256(workspace),
                    }
                )
            elif isinstance(node, Selection):
                lateral = node.id == "outer_band"
                ids = (
                    workspace.default.lateral_ids
                    if lateral
                    else workspace.default.plane_ids
                )
                count = 128 if lateral else 32
                if len(ids) < count:
                    raise ValueError("generated seed membership is too small")
                chosen = np.asarray(ids)[
                    np.linspace(0, len(ids) - 1, count, dtype=int)
                ].tolist()
                node = node.model_copy(update={"ids": chosen})
            elif isinstance(node, SurfaceFit):
                node = node.model_copy(update={"axial_domain": workspace.support})
            nodes.append(node)
        recipe = Recipe.model_validate(
            original_recipe.model_copy(update={"nodes": nodes}).model_dump()
        )
        recipe_bytes = canonical_json(recipe)
        recipe_path = fixture / "recipes/nozzle-selection-and-fitting-demo.json"
        recipe_path.parent.mkdir()
        _ = recipe_path.write_bytes(recipe_bytes)
        entries = {
            name: data for name, data in source.items() if not name.startswith(FIXED)
        }
        records = [
            record
            for record in original["source_files"]
            if not record["archive_path"].startswith(FIXED)
        ]
        fixture_records: list[dict[str, Any]] = []
        for path in sorted(fixture.rglob("*")):
            if (
                not path.is_file()
                or path.relative_to(fixture).parts[0] == "truth"
                or "selection-bundle" in path.name
            ):
                continue
            relative = path.relative_to(fixture).as_posix()
            name = FIXED + relative
            data = path.read_bytes()
            entries[name] = data
            records.append(
                {
                    "path": str(path),
                    "archive_path": name,
                    "sha256": digest(data),
                    "origin_kind": "generated-test-only-remap",
                }
            )
            fixture_records.append(
                {"path": name, "bytes": len(data), "sha256": digest(data)}
            )
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as archive:
            for name, data in sorted(entries.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        generated_zip = zip_buffer.getvalue()
        manifest: dict[str, Any] = json.loads(runtime_bytes)
        manifest["cad"] = False
        manifest["source_files"] = records
        assets = [record for record in manifest["assets"] if record["group"] != "cad"]
        for record in assets:
            if record["path"] == "scansor.zip":
                record.update(bytes=len(generated_zip), sha256=digest(generated_zip))
        manifest["assets"] = assets
        proof = {
            "kind": "disposable-generated-display-tier",
            "scale": scale,
            "not_production_nozzle": True,
            "fixed_virtual_example_path_remapped": FIXED,
            "selected_lateral": 128,
            "selected_plane": 32,
            "source_sha256": workspace.default.source_sha256,
            "model_sha256": workspace.model_sha256,
            "recipe_sha256": digest(recipe_bytes),
            "input_site_manifest_sha256": digest((site / "manifest.json").read_bytes()),
            "input_source_archive_sha256": digest(archive_bytes),
            "definition_sha256": digest(definition.read_bytes()),
            "scaled_definition_sha256": generation["definition_sha256"],
            "generator_revision": GENERATOR_REVISION,
            "generator_sha256": digest(generator.read_bytes()),
            "production_python_unchanged": source_proof,
            "preparation_module_origins": project_module_origins(repository, source),
            "production_worker_sha256": digest(runtime_files["worker.mjs"]),
            "production_ui_unchanged": [
                record
                for record in site_manifest["assets"]
                if record["path"].startswith("app/")
                and not record["path"].startswith(runtime_prefix + "/")
                and record["path"] != "app/backend-config.js"
            ],
            "configuration_changes": "CAD disabled; runtime URL updated to generated manifest hash. Retained-nozzle catalogue/labels are unchanged test scaffolding, not a supported generated-source deployment.",
            "versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pydantic": pydantic.__version__,
            },
        }
        manifest["benchmark"] = proof
        new_manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
        new_prefix = "app/runtime/" + digest(new_manifest_bytes)
        generated_files = {
            name: data
            for name, data in site_files.items()
            if not name.startswith(runtime_prefix + "/")
        }
        for record in assets:
            relative = record["path"]
            generated_files[new_prefix + "/" + relative] = (
                generated_zip if relative == "scansor.zip" else runtime_files[relative]
            )
        generated_files[new_prefix + "/manifest.json"] = new_manifest_bytes
        configuration = {
            **site_manifest["backend"],
            "runtimeURL": "./runtime/" + digest(new_manifest_bytes) + "/worker.mjs",
            "cad": False,
        }
        generated_files["app/backend-config.js"] = (
            "export default " + json.dumps(configuration, sort_keys=True) + ";\n"
        ).encode()
        generated_manifest: dict[str, Any] = json.loads(
            (site / "manifest.json").read_bytes()
        )
        generated_manifest["benchmark"] = proof
        generated_manifest["backend"] = configuration
        generated_manifest["capabilities"]["cad"] = False
        generated_manifest["runtime"].update(
            path=new_prefix,
            manifest_sha256=digest(new_manifest_bytes),
            cad_assets_present=False,
        )
        actual_fixture = read_json(fixture / "manifest.json")
        generated_manifest["bounded_examples"] = [
            {
                "id": f"generated-boss-test-tier-{scale}",
                "name": actual_fixture["name"],
                "status": actual_fixture["status"],
                "vertices": actual_fixture["vertices"],
                "triangles": actual_fixture["triangles"],
                "units": actual_fixture["units"],
                "files": fixture_records,
            }
        ]
        generated_manifest["assets"] = [
            {"path": name, "bytes": len(data), "sha256": digest(data)}
            for name, data in sorted(generated_files.items())
        ]
        generated_files["manifest.json"] = (
            json.dumps(generated_manifest, indent=2) + "\n"
        ).encode()
        generated_site = case / "site"
        write_files(generated_site, generated_files)
        reports.append(
            {
                "scale": scale,
                "site": str(generated_site),
                "runtime": str(generated_site / new_prefix),
                "source_archive_bytes": len(generated_zip),
                "source_archive_sha256": digest(generated_zip),
                "preparation_seconds": time.perf_counter() - started,
                "provenance": proof,
            }
        )
    _ = (output / "generated-artifacts.json").write_text(
        json.dumps(reports, indent=2) + "\n"
    )
    return reports


def native_case(runtime: Path, output: Path) -> None:
    """Reference the exact packaged source; call in a fresh native interpreter."""
    manifest = read_json(runtime / "manifest.json")
    assets = [
        record for record in manifest["assets"] if record["path"] == "scansor.zip"
    ]
    if len(assets) != 1:
        raise ValueError("expected exactly one source archive asset")
    with tempfile.TemporaryDirectory(
        prefix="native-generated-", dir=output.parent
    ) as temporary:
        root = Path(temporary)
        # Read verified bytes once immediately before extraction, avoiding a
        # second filesystem read that could mix a changed ZIP into the reference.
        packaged_source = source_archive(
            checked_bytes(runtime, assets[0]), manifest["source_files"], root
        )
        for name, data in packaged_source.items():
            target = checked_path(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_bytes(data)
        sys.path.insert(0, str(root))
        import numpy as np
        import pydantic

        from experiments.browser_runtime.workspace import BrowserWorkspace

        def unpack(text: str) -> dict[str, Any]:
            value: dict[str, Any] = json.loads(text)
            if not value["ok"]:
                raise ValueError(value["error"])
            return cast(dict[str, Any], value["result"])

        started = time.perf_counter()
        workspace = BrowserWorkspace(root / FIXED)
        load = time.perf_counter() - started
        graph = unpack(workspace.call_json("getGraph"))
        started = time.perf_counter()
        prepared = unpack(
            workspace.prepare_evaluation_json(
                "evaluateGraph",
                json.dumps({"token": graph["token"], "all_actions": True}),
            )
        )
        graph = (
            unpack(workspace.finish_evaluation_json())
            if prepared["run"]
            else prepared["state"]
        )
        arrays = {
            "local": workspace.workspace.local,
            "xyz": workspace.workspace.data.xyz,
            "normals": workspace.workspace.data.normals,
            "triangles": workspace.workspace.data.triangles,
            "weights": workspace.workspace.data.weights,
            "ids": workspace.workspace.data.ids,
            "display_positions": workspace.display_positions,
            "display_indices": workspace.display_indices,
        }
        if graph["errors"] or set(graph["states"].values()) != {"ready"}:
            raise ValueError("native generated graph did not complete")
        if any(name in sys.modules for name in ("OCP", "duckdb", "pyarrow", "psutil")):
            raise ValueError("numerical generated graph loaded excluded dependencies")
        result = {
            "graph": graph,
            "project_module_origins": project_module_origins(root, packaged_source),
            "timings_seconds": {
                "workspace_load": load,
                "evaluate": time.perf_counter() - started,
            },
            "versions": {
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pydantic": pydantic.__version__,
            },
            "arrays": {
                name: {
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "bytes": array.nbytes,
                    "writeable": array.flags.writeable,
                }
                for name, array in arrays.items()
            },
        }
        _ = output.write_text(json.dumps(result, allow_nan=False) + "\n")
