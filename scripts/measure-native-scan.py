#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Measure native source admission using the existing project environment.

The stdlib driver starts a fresh project interpreter for each source so fixture
generation and previous imports do not contaminate admission high-water marks.
HTTP mode includes a bounded same-process client; it does not measure browser/GPU
memory and its source-only grid is not a fitting-selection capacity claim.
"""

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GENERATE = r"""
import json, sys
from pathlib import Path
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / "src")]
from tests.native_scan_fixture import native_grid_fixture, native_scene_fixture
directory = Path(sys.argv[2]); directory.mkdir(parents=True, exist_ok=True)
for name, fixture in [("scene", native_scene_fixture()), ("grid-512x256", native_grid_fixture())]:
    (directory / (name + ".ply")).write_bytes(fixture.raw)
print(json.dumps({"generated": str(directory)}))
"""

MEASURE = r"""
import hashlib, json, platform, resource, shutil, sys, tempfile, threading, time
from pathlib import Path
from urllib.request import Request, urlopen
sys.path[:0] = [sys.argv[1], str(Path(sys.argv[1]) / "src")]
import numpy as np
import psutil
import pydantic
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits

source, directory, mode = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
limits = ScanLimits(32 * 1024**2, 500_000, 1_000_000, 512 * 1024**2)
process = psutil.Process()
progress = []
server = server_thread = None
if mode == "http":
    from experiments.nozzle_browser import NozzleServer
    limits = ScanLimits(8 * 1024**2, 131_072, 262_144, 96 * 1024**2)
    server = NozzleServer(scan_limits=limits, staging_directory=directory)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

def high_water():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)

baseline = process.memory_info().rss
baseline_high_water = high_water()
samples = [baseline]
stop = threading.Event()
def sample():
    while not stop.wait(0.001):
        samples.append(process.memory_info().rss)
sampler = threading.Thread(target=sample, daemon=True); sampler.start()
started = time.perf_counter()
transport = []
try:
    if mode == "core":
        project = ScanProject(); empty = project.accepted
        with tempfile.TemporaryDirectory(prefix="native-stage-", dir=directory) as temporary:
            staged = Path(temporary) / "source.ply"
            with source.open("rb") as src, staged.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=limits.chunk_bytes)
            staged_at = time.perf_counter()
            def record(value):
                progress.append({"phase": value.phase, "completed": value.completed, "total": value.total, "seconds": time.perf_counter() - started})
            with staged.open("rb") as stream:
                candidate = project.prepare_add(stream, source.name, limits, generation=empty.generation, token=str(empty.graph.snapshot()["token"]), progress=record)
            staged.unlink()
            accepted = project.commit(candidate)
        prepared_at = time.perf_counter()
        transport = [accepted.positions.tobytes(), accepted.indices.tobytes()]
    else:
        base = f"http://127.0.0.1:{server.server_port}"
        def request(path, payload=None, data=None, generation=None):
            headers = {"X-Scansor-Request": "1", "Origin": base}
            if generation is not None: headers["X-Scansor-Generation"] = str(generation)
            if payload is not None:
                data = json.dumps(payload).encode(); headers["Content-Type"] = "application/json"
            elif data is not None:
                headers.update({"Content-Type": "application/octet-stream", "Content-Length": str(source.stat().st_size)})
            with urlopen(Request(base + path, data=data, headers=headers), timeout=60) as response:
                body = response.read()
                return json.loads(body) if response.headers.get_content_type() == "application/json" else body
        meta = request("/api/meta")
        operation = request("/api/sources/imports", {"generation": meta["project_generation"], "token": meta["graph"]["token"], "filename": source.name, "size": source.stat().st_size})
        progress.append(operation)
        with source.open("rb") as stream:
            operation = request(f"/api/sources/imports/{operation['operationId']}/bytes", data=stream)
        staged_at = time.perf_counter()
        deadline = time.monotonic() + 60
        while not operation["terminal"]:
            if time.monotonic() > deadline: raise TimeoutError("source operation did not finish")
            time.sleep(0.005)
            operation = request(f"/api/sources/imports/{operation['operationId']}")
            progress.append(operation)
        if operation["status"] != "accepted": raise RuntimeError(operation)
        prepared_at = time.perf_counter()
        generation = operation["acceptedGeneration"]
        transport = [request(f"/mesh/{name}?generation={generation}", generation=generation) for name in ["positions", "indices"]]
        accepted = server._project.accepted
    completed = time.perf_counter()
    workspace = accepted.workspace
    arrays = {name: getattr(workspace.data, name).nbytes for name in ["xyz", "normals", "original_normals", "triangles", "weights", "face_status"]}
    with source.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert workspace.source_sha256 == digest
    assert hashlib.sha256(workspace.original_bytes).hexdigest() == digest
    assert len(accepted.graph.snapshot()["recipe"]["nodes"]) == 1
    samples.append(process.memory_info().rss)
    result = {
        "mode": mode, "filename": source.name, "source_bytes": source.stat().st_size, "source_sha256": digest,
        "admission_limits": {name: getattr(limits, name) for name in ["max_source_bytes", "max_vertices", "max_faces", "max_estimated_bytes", "chunk_bytes"]},
        "vertices": len(workspace.local), "faces": len(workspace.data.triangles), "units": workspace.descriptor.units,
        "seconds": {"staging_or_upload": staged_at-started, "prepare_and_commit_after_staging": prepared_at-staged_at, "display_encoding_or_http_readback": completed-prepared_at, "total": completed-started},
        "memory_bytes": {"baseline_rss": baseline, "sampled_rss_peak": max(samples), "sampled_increment_peak": max(samples)-baseline, "process_high_water_before": baseline_high_water, "process_high_water_after": high_water(), "immutable_original": len(workspace.original_bytes), "authoritative_arrays": arrays, "display_arrays": accepted.positions.nbytes + accepted.indices.nbytes, "transport_or_client_buffers": sum(len(value) for value in transport), "core_incremental_estimate": workspace.diagnostics.estimated_bytes},
        "progress": progress,
        "versions": {"python": sys.version, "numpy": np.__version__, "pydantic": pydantic.__version__, "platform": platform.platform()},
        "limitations": ["1ms RSS sampling can miss brief peaks", "process high-water may be dominated by imports", "HTTP mode includes same-process client buffering", "no browser, GPU or concurrent fit/CAD load", "large source tier does not expand selection/fitting limits"],
    }
finally:
    stop.set(); sampler.join()
    if server is not None:
        server.shutdown(); server_thread.join(); server.server_close()
print("MEASUREMENT:" + json.dumps(result, allow_nan=False))
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument("--mode", choices=("core", "http"), default="core")
    arguments = parser.parse_args()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    directory = arguments.output.parent / "native-measure-sources"
    subprocess.run(
        [str(arguments.native_python), "-I", "-c", GENERATE, str(ROOT), str(directory)],
        check=True,
        capture_output=True,
        text=True,
    )
    sources = [
        ROOT / "examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply",
        directory / "scene.ply",
        directory / "grid-512x256.ply",
    ]
    results = []
    for source in sources:
        process = subprocess.run(
            [
                str(arguments.native_python),
                "-I",
                "-c",
                MEASURE,
                str(ROOT),
                str(source),
                str(directory),
                arguments.mode,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode:
            raise RuntimeError(f"Measurement failed for {source}:\n{process.stderr}")
        line = next(
            line
            for line in process.stdout.splitlines()
            if line.startswith("MEASUREMENT:")
        )
        result = json.loads(line.removeprefix("MEASUREMENT:"))
        results.append(result)
        print(
            json.dumps(
                {
                    "case": source.name,
                    "mode": arguments.mode,
                    "seconds": result["seconds"],
                    "sampled_peak_bytes": result["memory_bytes"]["sampled_rss_peak"],
                }
            ),
            flush=True,
        )
    arguments.output.write_text(
        json.dumps({"schema_version": 1, "measurements": results}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
