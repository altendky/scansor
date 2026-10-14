#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Stage only hash-verified local archives. No Git or network operations."""

import hashlib
import json
import shutil
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
records = {
    r["name"]: r for r in json.loads((ROOT / "download-results.json").read_text())
}


def archive(name):
    p = ROOT / "downloads" / name
    assert hashlib.sha256(p.read_bytes()).hexdigest() == records[name]["sha256"]
    return p


def unpack(name, destination):
    target = ROOT / destination
    if target.exists():
        raise ValueError(f"Refusing to replace existing tree: {target}")
    temporary = ROOT / "unpack" / name
    temporary.mkdir(parents=True)
    with tarfile.open(archive(name)) as t:
        t.extractall(temporary, filter="data")
    entries = list(temporary.iterdir())
    assert len(entries) == 1 and entries[0].is_dir(), entries
    target.parent.mkdir(parents=True, exist_ok=True)
    entries[0].rename(target)
    temporary.rmdir()


for name, destination in [
    ("ocp-wasm.tar.gz", "src/ocp-wasm"),
    ("occt.tar.gz", "src/occt"),
    ("pybind11.tar.gz", "src/pybind11"),
    ("fmt.tar.gz", "src/fmt"),
    ("rapidjson.tar.gz", "src/rapidjson"),
    ("emsdk.tar.gz", "toolchain/emsdk"),
    ("binaryen.tar.gz", "toolchain/binaryen"),
]:
    unpack(name, destination)
p = ROOT / "src/ocp-generated"
p.mkdir()
with zipfile.ZipFile(archive("ocp-generated.zip")) as z:
    assert all((p / n).resolve().is_relative_to(p.resolve()) for n in z.namelist())
    z.extractall(p)
p = ROOT / "xbuildenv/314.0.7"
p.mkdir(parents=True)
with tarfile.open(archive("xbuildenv.tar.gz")) as t:
    t.extractall(p, filter="data")
(ROOT / "xbuildenv/xbuildenv").symlink_to("314.0.7", target_is_directory=True)
(p / ".installed").touch()
(p / ".build-python-version").write_text("3.14")
for name in ("tmp", "cache", "config", "logs", "notices"):
    (ROOT / name).mkdir(exist_ok=True)
(ROOT / "cache/ports").mkdir(exist_ok=True)
for name, port_name in [
    ("freetype.zip", "freetype.zip"),
    ("zlib.tar.gz", "zlib.3.1.tar.gz"),
]:
    shutil.copyfile(archive(name), ROOT / "cache/ports" / port_name)
print("Staged verified local sources and private tools")
