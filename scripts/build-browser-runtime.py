#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build a hash-verified static probe without installing Scansor in Pyodide."""

import argparse
import hashlib
import json
import os
import runpy
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "experiments/browser_runtime"
SOURCE_IMPORT_PROFILE = {
    "supported": True,
    "formats": ["binary-triangle-ply-v1"],
    "max_sources": 1,
    "limits": {
        "max_source_bytes": 8 * 1024**2,
        "max_vertices": 131_072,
        "max_faces": 262_144,
        "max_estimated_bytes": 96 * 1024**2,
        "chunk_bytes": 65_536,
    },
    "cancel": True,
    "recovery": "outside-worker-original-file",
}


def worker_protocol() -> dict[str, Any]:
    limits = runpy.run_path(str(ROOT / "experiments/actions_limits.py"))

    return {
        "version": 7,
        "bootstrap": "initialize",
        "workspace": "single-scan-candidate-transactions",
        "cad": "lazy-approved-ocp",
        "source_import": json.loads(json.dumps(SOURCE_IMPORT_PROFILE)),
        "source_actions": limits["actions_profile"](),
    }


def validate_worker_protocol(value: object) -> None:
    expected = worker_protocol()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("unsupported worker workspace protocol; rebuild it")
    if any(
        value[key] != expected[key]
        for key in expected
        if key not in ("source_import", "source_actions")
    ):
        raise ValueError("unsupported worker workspace protocol; rebuild it")
    actions = value["source_actions"]
    if (
        not isinstance(actions, dict)
        or actions != expected["source_actions"]
        or type(actions["supported"]) is not bool
        or type(actions["version"]) is not int
        or any(type(number) is not int for number in actions["limits"].values())
    ):
        raise ValueError("unsupported worker source-actions capability")
    profile = value["source_import"]
    reference = expected["source_import"]
    if (
        not isinstance(profile, dict)
        or set(profile) != set(reference)
        or any(profile[key] != reference[key] for key in reference if key != "limits")
    ):
        raise ValueError("unsupported worker source-import capability")
    if (
        type(profile["supported"]) is not bool
        or type(profile["cancel"]) is not bool
        or type(profile["max_sources"]) is not int
    ):
        raise ValueError("invalid worker source-import capability types")
    limits = profile["limits"]
    if not isinstance(limits, dict) or set(limits) != set(reference["limits"]):
        raise ValueError("invalid worker source-import limits")
    for key, number in limits.items():
        minimum = 24 if key == "chunk_bytes" else 1
        if type(number) is not int or number < minimum or number > 2**53 - 1:
            raise ValueError("invalid worker source-import limits")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError(f"asset path escapes artifact: {relative}")
    return path


def verify(root: Path, records: list[dict]) -> None:
    for record in records:
        path = checked_path(root, record["path"])
        if not path.is_file():
            raise ValueError(f"missing asset: {record['path']}")
        data = path.read_bytes()
        if len(data) != record["bytes"] or digest(data) != record["sha256"]:
            raise ValueError(f"asset integrity failure: {record['path']}")


def fetch(record: dict, output: Path, cache: Path | None) -> None:
    target = checked_path(output, record["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    candidates = [] if cache is None else [cache / record["path"], cache / target.name]
    candidate = next((path for path in candidates if path.is_file()), None)
    if candidate is not None:
        data = candidate.read_bytes()
    else:
        with urllib.request.urlopen(record["url"], timeout=120) as response:
            data = response.read(record["bytes"] + 1)
    if len(data) != record["bytes"] or digest(data) != record["sha256"]:
        raise ValueError(f"asset integrity failure: {record['path']}")
    target.write_bytes(data)


def source_bundle(paths: list[str], target: Path) -> list[dict]:
    records = []
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in sorted(paths):
            data = checked_path(ROOT, relative).read_bytes()
            name = relative.removeprefix("src/")
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
            records.append(
                {"path": relative, "archive_path": name, "sha256": digest(data)}
            )
    return records


def inventory_wheel_notices(output: Path, records: list[dict]) -> list[dict]:
    notices = []
    for record in records:
        if not record["path"].endswith(".whl"):
            continue
        with zipfile.ZipFile(output / record["path"]) as archive:
            for name in archive.namelist():
                upper = name.upper()
                if not name.endswith("/") and any(
                    word in upper
                    for word in ("LICENSE", "LICENCE", "NOTICE", "COPYING")
                ):
                    relative = f"notices/{Path(record['path']).stem}/{name}"
                    target = checked_path(output, relative)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(name))
                    notices.append({"asset": record["path"], "path": relative})
    return notices


def read_redistribution(
    directory: Path,
    groups: list[str],
    inventory: Path,
    bindings: dict[str, str],
) -> tuple[dict, list[dict]]:
    """Accept only the reviewed inventory for the exact selected build inputs."""
    assembler = runpy.run_path(str(ROOT / "scripts/build-browser-redistribution.py"))
    manifest = assembler["verify"](directory, inventory_path=inventory)
    for name, expected in bindings.items():
        if manifest.get("bindings", {}).get(name) != expected:
            raise ValueError(f"redistribution build input mismatch: {name}")
    for group in groups:
        completion = manifest.get("groups", {}).get(group)
        if (
            completion is None
            or completion.get("supplemental_requirements_complete") is not True
        ):
            raise ValueError(f"incomplete redistribution group: {group}")
    selected = assembler["select_verified_manifest"](manifest, groups)
    return selected, selected["assets"]


def complete_requirements(
    manifest: dict, groups: list[str], external: set[str]
) -> list[dict]:
    requirements = []
    for group in groups:
        for requirement in manifest["groups"][group]["requirements"]:
            status = requirement["status"]
            if status != "fulfilled" and not (
                status == "external_required" and requirement["id"] in external
            ):
                raise ValueError(
                    f"unfulfilled redistribution requirement: {requirement['id']}"
                )
            requirements.append(requirement | {"group": group, "status": "fulfilled"})
    return requirements


def validate_core_notices(directory: Path, lock: dict, cad: bool) -> None:
    records = [r for r in lock["assets"] if r["group"] == "numeric" or cad]
    if not lock["licenses"]:
        raise ValueError("missing core redistribution notices")
    verify(directory, [*records, *lock["licenses"]])
    for record in records:
        # The approved CAD wheel omits notices entirely. Its independent CAD
        # supplement supplies binding/kernel/dependency notices and source.
        if record["group"] != "numeric" or not record["path"].endswith(".whl"):
            continue
        found = False
        with zipfile.ZipFile(checked_path(directory, record["path"])) as archive:
            for name in archive.namelist():
                if name.endswith("/") or not any(
                    word in name.upper()
                    for word in ("LICENSE", "LICENCE", "NOTICE", "COPYING")
                ):
                    continue
                found = True
                target = checked_path(
                    directory, f"notices/{Path(record['path']).stem}/{name}"
                )
                if not target.is_file() or target.read_bytes() != archive.read(name):
                    raise ValueError(f"missing or changed wheel notice: {name}")
        if not found:
            raise ValueError(
                f"selected wheel lacks redistribution notices: {record['path']}"
            )


def validate_runtime_profile(manifest: dict, lock: dict) -> None:
    if type(manifest.get("cad")) is not bool:
        raise ValueError("runtime CAD profile must be a boolean")
    records = manifest["assets"]
    listed = {record["path"]: record for record in records}
    if len(listed) != len(records):
        raise ValueError("duplicate runtime profile assets")
    selected = [
        record
        for record in lock["assets"]
        if record["group"] == "numeric" or manifest["cad"]
    ]
    for expected in [*selected, *lock["licenses"]]:
        actual = listed.get(expected["path"])
        if actual is None or any(
            actual.get(key) != expected[key] for key in ("bytes", "sha256", "group")
        ):
            raise ValueError(
                f"runtime profile lacks exact locked asset: {expected['path']}"
            )
    cad_records = [record for record in lock["assets"] if record["group"] == "cad"]
    if not manifest["cad"] and any(
        record.get("group") == "cad"
        or any(
            record["path"] == cad["path"] or record["sha256"] == cad["sha256"]
            for cad in cad_records
        )
        for record in records
    ):
        raise ValueError(
            "numerical profile includes CAD assets without CAD requirements"
        )

    approved_paths = {record["path"] for record in selected}
    if any(
        (
            record.get("group") in {"numeric", "cad"}
            or Path(record["path"]).suffix in {".whl", ".wasm"}
        )
        and record["path"] not in approved_paths
        for record in records
    ):
        raise ValueError("runtime profile contains an unreviewed executable asset")


def validate_runtime_redistribution(
    directory: Path, manifest: dict, inventory: Path
) -> None:
    lock_path = PROBE / "runtime-lock.json"
    raw = lock_path.read_bytes()
    if manifest.get("runtime_lock_sha256") != digest(raw):
        raise ValueError("runtime redistribution lock mismatch")
    lock = json.loads(raw)
    validate_runtime_profile(manifest, lock)
    metadata = manifest.get("redistribution")
    if not isinstance(metadata, dict) or metadata.get("path") != "redistribution":
        raise ValueError("runtime lacks redistribution evidence")
    packaged = checked_path(directory, "redistribution")
    if metadata.get("manifest_sha256") != digest(
        (packaged / "manifest.json").read_bytes()
    ):
        raise ValueError("runtime redistribution manifest mismatch")
    groups = ["numeric", "cad"] if manifest["cad"] else ["numeric"]
    supplement, _ = read_redistribution(
        packaged,
        groups,
        inventory,
        {"runtime_lock_sha256": manifest["runtime_lock_sha256"]},
    )
    requirements = complete_requirements(
        supplement, groups, {"existing-core-and-wheel-notices"}
    )
    if (
        metadata.get("inventory_sha256") != supplement["inventory_sha256"]
        or metadata.get("requirements") != requirements
        or manifest.get("redistribution_gaps") != []
    ):
        raise ValueError("runtime redistribution requirement evidence mismatch")
    validate_core_notices(directory, lock, manifest["cad"])


def build(
    output: Path,
    cache: Path | None,
    cad: bool,
    native_python: Path,
    redistribution: Path | None = None,
) -> None:
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("use an output directory outside the repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "output directory must be empty; existing files are never replaced"
        )
    runtime_lock_raw = (PROBE / "runtime-lock.json").read_bytes()
    runtime_lock_hash = digest(runtime_lock_raw)
    lock = json.loads(runtime_lock_raw)
    records = [r for r in lock["assets"] if r["group"] == "numeric" or cad]
    supplement = None
    supplemental_assets = []
    groups = ["numeric", "cad"] if cad else ["numeric"]
    if redistribution is not None:
        redistribution = redistribution.resolve()
        if output.is_relative_to(redistribution) or redistribution.is_relative_to(
            output
        ):
            raise ValueError("output and redistribution input must not overlap")
        supplement, supplemental_assets = read_redistribution(
            redistribution,
            groups,
            ROOT / "scripts/browser-redistribution-lock.json",
            {"runtime_lock_sha256": runtime_lock_hash},
        )
    output.mkdir(parents=True, exist_ok=True)
    for record in [*records, *lock["licenses"]]:
        print(f"asset: {record['path']}", flush=True)
        fetch(record, output, cache)
    paths = lock["sources"]["numeric"] + lock["sources"]["fixtures"]
    paths += [
        "experiments/browser_runtime/__init__.py",
        "experiments/browser_runtime/probe.py",
    ]
    if cad:
        paths += lock["sources"]["cad"]
    source_records = source_bundle(paths, output / "scansor.zip")
    for filename in ("index.html", "worker.mjs", "integrity.mjs"):
        shutil.copyfile(PROBE / filename, output / filename)
    for filename in (
        "README.md",
        "redistribution.md",
        "validation.md",
        "runtime-lock.json",
    ):
        if filename == "runtime-lock.json":
            (output / filename).write_bytes(runtime_lock_raw)
        else:
            shutil.copyfile(PROBE / filename, output / filename)
    for filename in ("LICENSE-MIT", "LICENSE-APACHE"):
        target = output / "notices/scansor" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, target)
    for filename in ("README.md", "validation.md", "decision.md"):
        target = output / "cancellation" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PROBE / "cancellation" / filename, target)
    notices = inventory_wheel_notices(output, records)
    redistribution_requirements = []
    if supplement is not None:
        validate_core_notices(output, lock, cad)
        redistribution_requirements = complete_requirements(
            supplement, groups, {"existing-core-and-wheel-notices"}
        )
        for record in supplemental_assets:
            target = checked_path(output, "redistribution/" + record["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checked_path(redistribution, record["path"]), target)
            verify(output, [record | {"path": "redistribution/" + record["path"]}])
        (output / "redistribution/manifest.json").write_text(
            json.dumps(supplement, indent=2, sort_keys=True) + "\n"
        )
    # Use the packaged source, not the editable checkout, for the reference run.
    with tempfile.TemporaryDirectory(prefix="native-", dir=output) as temporary:
        with zipfile.ZipFile(output / "scansor.zip") as archive:
            archive.extractall(temporary)
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from experiments.browser_runtime.probe import native; "
            "native(sys.argv[1], sys.argv[2], sys.argv[3] == 'cad')"
        )
        environment = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            OPENBLAS_NUM_THREADS="1",
            TMPDIR=temporary,
        )
        subprocess.run(
            [
                str(native_python.absolute()),
                "-I",
                "-c",
                code,
                temporary,
                str(output),
                "cad" if cad else "numeric",
            ],
            cwd=temporary,
            env=environment,
            check=True,
        )
    provenance = {
        "schema_version": 1,
        "worker_protocol": worker_protocol(),
        "versions": lock["versions"],
        "cad": cad,
        "redistribution_ready": supplement is not None,
        "redistribution": {
            "inventory_sha256": supplement["inventory_sha256"],
            "manifest_sha256": digest(
                (output / "redistribution/manifest.json").read_bytes()
            ),
            "requirements": redistribution_requirements,
            "path": "redistribution",
        }
        if supplement is not None
        else None,
        "redistribution_gaps": []
        if supplement is not None
        else [
            "Complete core/component notices and pydantic-core compiled Rust notices; see redistribution.md",
            *(
                [
                    "Approved OCP/OCCT corresponding source, patches and build/relink route"
                ]
                if cad
                else []
            ),
        ],
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_worktree_dirty": bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, text=True
            ).strip()
        ),
        "source_files": source_records,
        "wheel_notices": notices,
        "runtime_lock_sha256": runtime_lock_hash,
        "assets": [*records, *lock["licenses"]],
    }
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.relative_to(output).as_posix() not in {
            r["path"] for r in provenance["assets"]
        }:
            provenance["assets"].append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": digest(path.read_bytes()),
                    "group": "probe",
                }
            )
    verify(output, provenance["assets"])
    if supplement is not None:
        if digest((PROBE / "runtime-lock.json").read_bytes()) != runtime_lock_hash:
            raise ValueError("runtime lock changed during assembly")
        validate_runtime_redistribution(
            output, provenance, ROOT / "scripts/browser-redistribution-lock.json"
        )
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"built: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cache", type=Path, help="optional existing assets; corrupt cache fails"
    )
    parser.add_argument(
        "--cad",
        action="store_true",
        help="include the separately loaded approved OCP lineage",
    )
    parser.add_argument("--native-python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument(
        "--redistribution",
        type=Path,
        help="verified notice/source package from the reviewed redistribution lock",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify an existing artifact without rebuilding",
    )
    args = parser.parse_args()
    if args.verify:
        manifest = json.loads((args.output / "manifest.json").read_text())
        verify(args.output, manifest["assets"])
        if manifest.get("redistribution_ready") is True:
            validate_runtime_redistribution(
                args.output, manifest, ROOT / "scripts/browser-redistribution-lock.json"
            )
        print("asset integrity: passed")
    else:
        build(
            args.output, args.cache, args.cad, args.native_python, args.redistribution
        )


if __name__ == "__main__":
    main()
