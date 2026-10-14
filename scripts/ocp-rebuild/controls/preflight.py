#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check the privately configured tool versions without compiling OCCT."""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
env = os.environ | json.loads((ROOT / "build-environment.json").read_text())
results = []
for command in [
    ["emcc", "--version"],
    ["wasm-opt", "--version"],
    ["pyodide", "config", "get", "emscripten_version"],
    ["pyodide", "config", "list"],
]:
    process = subprocess.run(
        command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=90
    )
    record = {
        "command": command,
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    results.append(record)
    print(json.dumps(record), flush=True)
    (ROOT / "logs/preflight.json").write_text(json.dumps(results, indent=2) + "\n")
    if process.returncode:
        raise SystemExit(process.returncode)
