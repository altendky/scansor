#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Assemble an external, provider-neutral /app/ from UI and verified runtime assets."""

import argparse
import hashlib
import json
import runpy
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REDISTRIBUTION_LOCK = ROOT / "scripts/browser-redistribution-lock.json"
VIEWER = "experiments/browser_viewer"
EXAMPLE = "examples/nozzle-bayonette-simplified"
RUNTIME_BUILDER = runpy.run_path(
    str(Path(__file__).with_name("build-browser-runtime.py"))
)
BUILD_INPUTS = (
    "package.json",
    "package-lock.json",
    "vite.config.js",
    "vite.flexlayout.config.js",
    "workspace.jsx",
)
UI_FILES = (
    "index.html",
    "app.js",
    "source-project.js",
    "bootstrap.js",
    "worker-backend.js",
    "backend.js",
    "http-backend.js",
    "workspace-state.js",
    "action-tree.js",
    "display-transform.js",
    "cad-export.js",
    "body-ui.js",
    "feature-graph-view.js",
    "feature-names.js",
    "feature-inputs.js",
    "fit-reference.js",
    "relationship-inputs.js",
    "residual-display.js",
    "reuse-volume.js",
    "surface-trims.js",
    "graph-evaluation.js",
    "edge-highlight.js",
    "fit-footprint.js",
    "analytic-geometry.js",
    "fit-intent.js",
    "model-picking.js",
    "selection.js",
    "selection-owned-editor.js",
    "circular-reuse-editor.js",
    "surface-selection-editor.js",
    "navigation.js",
    "navigation-math.js",
    "style.css",
)
VENDOR_FILES = {
    "vendor/three.module.js": "node_modules/three/build/three.module.js",
    "vendor/three.core.js": "node_modules/three/build/three.core.js",
    "vendor/TrackballControls.js": "node_modules/three/examples/jsm/controls/TrackballControls.js",
    "vendor/OrbitControls.js": "node_modules/three/examples/jsm/controls/OrbitControls.js",
    "vendor/LICENSE": "node_modules/three/LICENSE",
    **{
        f"vendor/lines/{name}.js": f"node_modules/three/examples/jsm/lines/{name}.js"
        for name in (
            "Line2",
            "LineGeometry",
            "LineMaterial",
            "LineSegments2",
            "LineSegmentsGeometry",
        )
    },
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
        or any(character in relative for character in "\\?#:\0")
        or Path(relative).is_absolute()
    ):
        raise ValueError(f"invalid artifact path: {relative!r}")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"asset path escapes artifact: {relative}")
    return target


def read_asset(root: Path, record: dict) -> bytes:
    source = checked_path(root, record["path"])
    if not source.is_file():
        raise ValueError(f"missing asset: {record['path']}")
    data = source.read_bytes()
    if len(data) != record["bytes"] or digest(data) != record["sha256"]:
        raise ValueError(f"asset integrity failure: {record['path']}")
    return data


def read_runtime(runtime: Path) -> tuple[bytes, dict, dict[str, bytes]]:
    raw = checked_path(runtime, "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema_version") != 1 or not isinstance(
        manifest.get("assets"), list
    ):
        raise ValueError("unsupported runtime manifest")
    RUNTIME_BUILDER["validate_worker_protocol"](manifest.get("worker_protocol"))
    files = {"manifest.json": raw}
    for record in manifest["assets"]:
        name = record["path"]
        if name in files:
            raise ValueError(f"duplicate runtime asset: {name}")
        files[name] = read_asset(runtime, record)
    for required in (
        "worker.mjs",
        "integrity.mjs",
        "scansor.zip",
        "runtime/pyodide.mjs",
    ):
        if required not in files:
            raise ValueError(f"missing runtime asset record: {required}")
    return raw, manifest, files


def example_inventory(runtime: Path, manifest: dict) -> dict:
    """Record the actual checked-in bounded fixture contained in the source ZIP."""
    entries = {}
    with zipfile.ZipFile(checked_path(runtime, "scansor.zip")) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate source archive member")
        for record in manifest["source_files"]:
            name = record["archive_path"]
            checked_path(
                runtime, name
            )  # Validate the name without extracting anything.
            data = archive.read(name)
            if digest(data) != record["sha256"]:
                raise ValueError(f"source provenance mismatch: {name}")
            if name.startswith(EXAMPLE + "/"):
                entries[name] = {
                    "path": name,
                    "bytes": len(data),
                    "sha256": digest(data),
                }
        name = EXAMPLE + "/manifest.json"
        if name not in entries:
            raise ValueError("runtime source bundle lacks the bounded example manifest")
        fixture = json.loads(archive.read(name))
        for relative, expected in fixture["files"].items():
            source = entries.get(EXAMPLE + "/" + relative)
            if (
                source is None
                or source["sha256"] != expected["sha256"]
                or source["bytes"] != expected["bytes"]
            ):
                raise ValueError(f"bounded example integrity failure: {relative}")
    return {
        "id": "nozzle",
        "name": fixture["name"],
        "status": fixture["status"],
        "vertices": fixture["vertices"],
        "triangles": fixture["triangles"],
        "units": fixture["units"],
        "files": [entries[name] for name in sorted(entries)],
    }


def repository_state(root: Path) -> dict:
    return {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "worktree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            ).strip()
        ),
    }


def check_output(output: Path, root: Path, runtime: Path) -> Path:
    output = output.resolve()
    if output.is_relative_to(root.resolve()):
        raise ValueError("use an output directory outside the repository")
    if output.is_relative_to(runtime.resolve()) or runtime.resolve().is_relative_to(
        output
    ):
        raise ValueError("output and runtime input directories must not overlap")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError(
            "output directory must be empty; existing files are never replaced"
        )
    return output


def verify(output: Path) -> dict:
    manifest = json.loads(checked_path(output, "manifest.json").read_bytes())
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "scansor-static-app"
    ):
        raise ValueError("unsupported static app manifest")
    seen = set()
    for record in manifest["assets"]:
        if record["path"] in seen:
            raise ValueError(f"duplicate app asset: {record['path']}")
        seen.add(record["path"])
        read_asset(output, record)
    if manifest.get("redistribution_ready") is True and not isinstance(
        manifest.get("redistribution"), dict
    ):
        raise ValueError("ready static app lacks redistribution evidence")
    if manifest.get("redistribution") is not None:
        validate_redistribution(output, manifest)
    return manifest


def validate_redistribution(output: Path, manifest: dict) -> None:
    runtime_builder = runpy.run_path(str(ROOT / "scripts/build-browser-runtime.py"))
    runtime = checked_path(output, manifest["runtime"]["path"])
    raw, runtime_manifest, _ = read_runtime(runtime)
    if digest(raw) != manifest["runtime"]["manifest_sha256"]:
        raise ValueError("static runtime manifest identity mismatch")
    ui_locks = [
        record["sha256"]
        for record in manifest["build_inputs"]
        if record["path"] == f"{VIEWER}/package-lock.json"
    ]
    if len(ui_locks) != 1:
        raise ValueError("static UI lacks one package-lock identity")
    supplement, _ = runtime_builder["read_redistribution"](
        checked_path(output, "redistribution"),
        ["ui"],
        REDISTRIBUTION_LOCK,
        {
            "runtime_lock_sha256": runtime_manifest.get("runtime_lock_sha256"),
            "ui_package_lock_sha256": ui_locks[0],
        },
    )
    metadata = manifest["redistribution"]
    expected_requirements = runtime_builder["complete_requirements"](
        supplement, ["ui"], set()
    )
    if (
        not isinstance(metadata, dict)
        or metadata.get("inventory_sha256") != supplement["inventory_sha256"]
        or metadata.get("path") != "redistribution"
        or metadata.get("requirements") != expected_requirements
        or metadata.get("manifest_sha256")
        != digest(checked_path(output, "redistribution/manifest.json").read_bytes())
    ):
        raise ValueError("static UI redistribution evidence mismatch")
    ready = runtime_manifest.get("redistribution_ready") is True
    if ready:
        runtime_builder["validate_runtime_redistribution"](
            runtime, runtime_manifest, REDISTRIBUTION_LOCK
        )
    if (
        manifest.get("redistribution_ready") is not ready
        or manifest.get("runtime_redistribution_ready")
        != runtime_manifest.get("redistribution_ready", False)
        or manifest.get("redistribution_gaps")
        != runtime_manifest.get("redistribution_gaps", [])
    ):
        raise ValueError("static redistribution readiness mismatch")


def build(
    output: Path,
    runtime: Path,
    *,
    root: Path = ROOT,
    redistribution: Path | None = None,
) -> dict:
    runtime = runtime.resolve()
    output = check_output(output, root, runtime)
    raw, runtime_manifest, runtime_files = read_runtime(runtime)
    supplement = None
    supplemental_assets = []
    requirements = []
    ui_lock_hash = None
    ui_lock_raw = None
    if redistribution is not None:
        redistribution = redistribution.resolve()
        if output.is_relative_to(redistribution) or redistribution.is_relative_to(
            output
        ):
            raise ValueError("output and redistribution input must not overlap")
        runtime_builder = runpy.run_path(str(ROOT / "scripts/build-browser-runtime.py"))
        ui_lock_raw = checked_path(root, f"{VIEWER}/package-lock.json").read_bytes()
        ui_lock_hash = digest(ui_lock_raw)
        supplement, supplemental_assets = runtime_builder["read_redistribution"](
            redistribution,
            ["ui"],
            REDISTRIBUTION_LOCK,
            {
                "runtime_lock_sha256": runtime_manifest.get("runtime_lock_sha256"),
                "ui_package_lock_sha256": ui_lock_hash,
            },
        )
        requirements = runtime_builder["complete_requirements"](
            supplement, ["ui"], set()
        )
        if runtime_manifest.get("redistribution_ready") is True:
            runtime_builder["validate_runtime_redistribution"](
                runtime, runtime_manifest, REDISTRIBUTION_LOCK
            )
    inventory = example_inventory(runtime, runtime_manifest)
    runtime_hash = digest(raw)
    runtime_prefix = f"app/runtime/{runtime_hash}"
    # Read/validate every input before creating output, so failed preflight writes nothing.
    files = {f"{runtime_prefix}/{name}": data for name, data in runtime_files.items()}
    source_files = []
    mappings = {name: name for name in UI_FILES} | {
        "workspace.js": "dist/workspace.js",
        "workspace.css": "dist/workspace.css",
        **VENDOR_FILES,
    }
    for target, source in mappings.items():
        relative = f"{VIEWER}/{source}"
        path = checked_path(root, relative)
        if not path.is_file():
            raise ValueError(f"missing UI input: {relative}; build the viewer first")
        data = path.read_bytes()
        files[f"app/{target}"] = data
        source_files.append(
            {"path": relative, "artifact_path": f"app/{target}", "sha256": digest(data)}
        )
    # Source/native HTTP config must exist, but this artifact deliberately selects worker.
    native_config = checked_path(root, f"{VIEWER}/backend-config.js")
    if not native_config.is_file():
        raise ValueError("missing UI input: backend-config.js")
    source_files.append(
        {
            "path": f"{VIEWER}/backend-config.js",
            "sha256": digest(native_config.read_bytes()),
            "role": "native-default-overridden",
        }
    )
    config = {
        "type": "worker",
        "runtimeURL": f"./runtime/{runtime_hash}/worker.mjs",
        "cad": bool(runtime_manifest["cad"]),
        "sourceActions": runtime_manifest["worker_protocol"]["source_actions"][
            "supported"
        ],
    }
    files["app/backend-config.js"] = (
        "export default " + json.dumps(config, sort_keys=True) + ";\n"
    ).encode()
    landing_path = "experiments/browser_static/landing.html"
    files["index.html"] = checked_path(root, landing_path).read_bytes()
    for filename in ("LICENSE-MIT", "LICENSE-APACHE"):
        path = checked_path(root, filename)
        if not path.is_file():
            raise ValueError(f"missing UI notice: {filename}")
        files[f"notices/scansor/{filename}"] = path.read_bytes()
    if supplement is not None:
        for record in supplemental_assets:
            files["redistribution/" + record["path"]] = read_asset(
                redistribution, record
            )
        files["redistribution/manifest.json"] = (
            json.dumps(supplement, indent=2, sort_keys=True) + "\n"
        ).encode()
    state = repository_state(root)
    build_inputs = []
    for name in BUILD_INPUTS:
        relative = f"{VIEWER}/{name}"
        build_inputs.append(
            {
                "path": relative,
                "sha256": digest(ui_lock_raw)
                if name == "package-lock.json" and ui_lock_raw is not None
                else digest(checked_path(root, relative).read_bytes()),
            }
        )
    build_inputs.append({"path": landing_path, "sha256": digest(files["index.html"])})
    if (
        supplement is not None
        and digest(checked_path(root, f"{VIEWER}/package-lock.json").read_bytes())
        != ui_lock_hash
    ):
        raise ValueError("UI package lock changed during assembly")
    package = {
        "schema_version": 1,
        "kind": "scansor-static-app",
        "entry": "app/index.html",
        "capabilities": {
            "runtime_bootstrap": True,
            "numerical": True,
            "cad": bool(runtime_manifest["cad"]),
            "source_import": runtime_manifest["worker_protocol"]["source_import"],
            "source_actions": runtime_manifest["worker_protocol"]["source_actions"],
        },
        "source_revision": state["revision"],
        "source_worktree_dirty": state["worktree_dirty"],
        "source_files": source_files,
        "build_inputs": build_inputs,
        "runtime": {
            "path": runtime_prefix,
            "manifest_sha256": runtime_hash,
            "source_revision": runtime_manifest["source_revision"],
            "source_worktree_dirty": runtime_manifest["source_worktree_dirty"],
            "versions": runtime_manifest["versions"],
            "cad_assets_present": runtime_manifest["cad"],
        },
        "bounded_examples": [inventory],
        "backend": config,
        "redistribution_ready": supplement is not None
        and runtime_manifest.get("redistribution_ready") is True,
        "redistribution": {
            "inventory_sha256": supplement["inventory_sha256"],
            "manifest_sha256": digest(files["redistribution/manifest.json"]),
            "requirements": requirements,
            "path": "redistribution",
        }
        if supplement is not None
        else None,
        "runtime_redistribution_ready": runtime_manifest.get(
            "redistribution_ready", False
        ),
        "redistribution_gaps": [
            *runtime_manifest.get("redistribution_gaps", []),
            *(
                []
                if supplement is not None
                else ["Static UI dependency notice/source supplement is missing."]
            ),
        ],
        "assets": [
            {"path": name, "bytes": len(data), "sha256": digest(data)}
            for name, data in sorted(files.items())
        ],
    }
    output.mkdir(parents=True, exist_ok=True)
    # Recheck real containment immediately before writes; no copying through an escaped symlink.
    check_output(output, root, runtime)
    for name, data in files.items():
        target = checked_path(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    checked_path(output, "manifest.json").write_text(
        json.dumps(package, indent=2) + "\n"
    )
    verify(output)
    return package


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runtime", type=Path, help="existing hash-verified #201 artifact"
    )
    parser.add_argument(
        "--redistribution",
        type=Path,
        help="verified UI notice supplement from the reviewed redistribution lock",
    )
    parser.add_argument(
        "--verify", action="store_true", help="verify an existing assembled artifact"
    )
    args = parser.parse_args()
    if args.verify:
        verify(args.output.resolve())
        print("static app integrity: passed")
    else:
        if args.runtime is None:
            parser.error("--runtime is required when assembling")
        package = build(args.output, args.runtime, redistribution=args.redistribution)
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "runtime": package["runtime"],
                    "redistribution_ready": package["redistribution_ready"],
                }
            )
        )


if __name__ == "__main__":
    main()
