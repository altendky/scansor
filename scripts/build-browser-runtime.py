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
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "experiments/browser_runtime"


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


def build(output: Path, cache: Path | None, cad: bool, native_python: Path) -> None:
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("use an output directory outside the repository")
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            "output directory must be empty; existing files are never replaced"
        )
    output.mkdir(parents=True, exist_ok=True)
    lock = json.loads((PROBE / "runtime-lock.json").read_text())
    records = [r for r in lock["assets"] if r["group"] == "numeric" or cad]
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
        shutil.copyfile(PROBE / filename, output / filename)
    for filename in ("LICENSE-MIT", "LICENSE-APACHE"):
        target = output / "notices/scansor" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / filename, target)
    notices = inventory_wheel_notices(output, records)
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
        "versions": lock["versions"],
        "cad": cad,
        "redistribution_ready": False,
        "redistribution_gaps": [
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
        "runtime_lock_sha256": digest((PROBE / "runtime-lock.json").read_bytes()),
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
        "--verify",
        action="store_true",
        help="verify an existing artifact without rebuilding",
    )
    args = parser.parse_args()
    if args.verify:
        manifest = json.loads((args.output / "manifest.json").read_text())
        verify(args.output, manifest["assets"])
        print("asset integrity: passed")
    else:
        build(args.output, args.cache, args.cad, args.native_python)


if __name__ == "__main__":
    main()
