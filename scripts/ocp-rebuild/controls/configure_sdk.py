#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Configure an archive-staged SDK with existing Node and private supported caches."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--jobs", type=int, choices=(1, 2, 4), default=1)
parser.add_argument("--repair-optimization", choices=("O1", "O4"), default="O1")
args = parser.parse_args()
sdk = ROOT / "toolchain/emsdk/upstream"
wrapper = sdk / "install"
if wrapper.exists():
    for child in wrapper.iterdir():
        child.rename(sdk / child.name)
    wrapper.rmdir()
cfg = ROOT / "config/emscripten.py"
cfg.write_text(
    "\n".join(
        [
            f"LLVM_ROOT = {str(sdk / 'bin')!r}",
            f"BINARYEN_ROOT = {str(ROOT / 'toolchain/binaryen')!r}",
            f"NODE_JS = {shutil.which('node')!r}",
            f"CACHE = {str(sdk / 'emscripten/cache')!r}",
            f"PORTS = {str(ROOT / 'cache/ports')!r}",
        ]
    )
    + "\n"
)
environment = {
    "PATH": os.pathsep.join(
        [
            str(ROOT / "venv/bin"),
            str(ROOT / "toolchain/binaryen/bin"),
            str(sdk / "emscripten"),
            str(sdk / "bin"),
            os.environ["PATH"],
        ]
    ),
    "PYODIDE_XBUILDENV_PATH": str(ROOT / "xbuildenv"),
    "PYODIDE_ROOT": str(ROOT / "xbuildenv/314.0.7/xbuildenv/pyodide-root"),
    "EMSDK_DIR": str(ROOT / "toolchain/emsdk"),
    "EMSCRIPTEN_DIR": str(sdk / "emscripten"),
    "PYODIDE_SKIP_EMSCRIPTEN_INSTALL": "1",
    "EM_CONFIG": str(cfg),
    "EM_CACHE": str(sdk / "emscripten/cache"),
    "EM_PORTS": str(ROOT / "cache/ports"),
    "EMCC_CORES": "1",
    "BINARYEN_CORES": "1",
    "TMPDIR": str(ROOT / "tmp"),
    "XDG_CACHE_HOME": str(ROOT / "cache"),
    "XDG_CONFIG_HOME": str(ROOT / "config"),
    "UV_CACHE_DIR": str(ROOT / "uv-cache"),
    "PIP_CACHE_DIR": str(ROOT / "cache/pip"),
    "PYTHONDONTWRITEBYTECODE": "1",
    "SKBUILD_CMAKE_BUILD_TYPE": "Release",
    "CFLAGS": "-O3",
    "CXXFLAGS": "-O3",
    "LDFLAGS": "-O1",
    "CMAKE_BUILD_PARALLEL_LEVEL": str(args.jobs),
    "_FORCE_OLD_SOURCES": "TRUE",
    # The pinned repair script parses true values; build tooling checks presence.
    "CI": "1" if args.repair_optimization == "O4" else "0",
}
(ROOT / "build-environment.json").write_text(json.dumps(environment, indent=2) + "\n")
print("Private SDK configuration:", cfg, hashlib.sha256(cfg.read_bytes()).hexdigest())
