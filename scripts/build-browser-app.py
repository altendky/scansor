#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Assemble an external, provider-neutral /app/ from UI and verified runtime assets."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEWER = "experiments/browser_viewer"
EXAMPLE = "examples/nozzle-bayonette-simplified"
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
    "residual-display.js",
    "reuse-volume.js",
    "surface-trims.js",
    "graph-evaluation.js",
    "edge-highlight.js",
    "fit-footprint.js",
    "model-picking.js",
    "selection.js",
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
    if manifest.get("worker_protocol") != {"version": 1, "bootstrap": "initialize"}:
        raise ValueError(
            "runtime lacks the supported worker bootstrap protocol; rebuild it"
        )
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
    return manifest


def build(output: Path, runtime: Path, *, root: Path = ROOT) -> dict:
    runtime = runtime.resolve()
    output = check_output(output, root, runtime)
    raw, runtime_manifest, runtime_files = read_runtime(runtime)
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
    config = {"type": "worker", "runtimeURL": f"./runtime/{runtime_hash}/worker.mjs"}
    files["app/backend-config.js"] = (
        "export default " + json.dumps(config, sort_keys=True) + ";\n"
    ).encode()
    files["index.html"] = (
        b'<!doctype html><meta charset="utf-8"><title>Scansor experiment</title><h1>Scansor browser experiment</h1><p>Local static bootstrap artifact. Workspace computation is introduced separately.</p><a href="./app/">Open Scansor</a>\n'
    )
    for filename in ("LICENSE-MIT", "LICENSE-APACHE"):
        path = checked_path(root, filename)
        if not path.is_file():
            raise ValueError(f"missing UI notice: {filename}")
        files[f"notices/scansor/{filename}"] = path.read_bytes()
    state = repository_state(root)
    build_inputs = []
    for name in BUILD_INPUTS:
        relative = f"{VIEWER}/{name}"
        build_inputs.append(
            {
                "path": relative,
                "sha256": digest(checked_path(root, relative).read_bytes()),
            }
        )
    package = {
        "schema_version": 1,
        "kind": "scansor-static-app",
        "entry": "app/index.html",
        "capabilities": {"runtime_bootstrap": True, "numerical": False, "cad": False},
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
        "redistribution_ready": False,
        "runtime_redistribution_ready": runtime_manifest.get(
            "redistribution_ready", False
        ),
        "redistribution_gaps": [
            *runtime_manifest.get("redistribution_gaps", []),
            "Static UI dependency notices require publication review; this package does not authorize publication.",
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
        "--verify", action="store_true", help="verify an existing assembled artifact"
    )
    args = parser.parse_args()
    if args.verify:
        verify(args.output.resolve())
        print("static app integrity: passed")
    else:
        if args.runtime is None:
            parser.error("--runtime is required when assembling")
        package = build(args.output, args.runtime)
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "runtime": package["runtime"],
                    "redistribution_ready": False,
                }
            )
        )


if __name__ == "__main__":
    main()
