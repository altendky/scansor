#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Observe repeated native source UI cycles; no capacity/performance gate.

The stdlib-only entry point re-executes the explicit project interpreter. Fixture
generation, the measured HTTP server, the monitor and the browser are separate
processes. Read-only snapshots and instrumentation affect timings. RSS samples,
lifetime high-water, logical arrays and browser counters overlap: do not add them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import queue
import stat
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any, TextIO, cast, final

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()
NODE_SCRIPT = ROOT / "experiments/browser_static/measure-native-source-ui.mjs"
MAX_SAMPLES = 200_000
MAX_EVENTS = 512
STAGES = (
    "empty-baseline",
    "file-selected",
    "imported",
    "inspected",
    "selection-overlay",
    "reset",
)


def utc() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    _ = temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    _ = temporary.replace(path)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fresh_output(path: Path, repository: Path = ROOT) -> Path:
    """Never overwrite reports or put generated measurements in the repository."""
    output = path.expanduser().resolve()
    repository = repository.resolve()
    if output == repository or repository in output.parents:
        raise ValueError("Reports must be outside the repository")
    output.mkdir(parents=True, exist_ok=False)
    return output


def generate_inputs(directory: Path) -> None:
    """Executed before measurement, exclusively in the project interpreter."""
    from dataclasses import asdict

    from experiments.nozzle_browser import NATIVE_SCAN_LIMITS
    from experiments.scan_sources import PathInput
    from experiments.scan_workspace import prepare_scan
    from tests.native_scan_fixture import native_grid_fixture, native_scene_fixture

    directory.mkdir()
    sources = [
        (
            "raw",
            "RealityScan raw nozzle",
            "retained RealityScan source",
            (
                ROOT
                / "examples/nozzle-bayonette-simplified/nozzle-bayonette-simplified.ply"
            ).read_bytes(),
        ),
        (
            "scene",
            "Independent cylinder and plane",
            "generated",
            native_scene_fixture().raw,
        ),
        ("grid", "Generated source-only grid", "generated", native_grid_fixture().raw),
    ]
    records: list[dict[str, Any]] = []
    for identifier, label, kind, raw in sources:
        path = directory / f"{identifier}.ply"
        _ = path.write_bytes(raw)
        workspace = prepare_scan(PathInput(path), path.name, NATIVE_SCAN_LIMITS)
        positions, indices = workspace.display_buffers()
        records.append(
            {
                "id": identifier,
                "label": label,
                "kind": kind,
                "path": str(path),
                "filename": path.name,
                "original_bytes": len(raw),
                "source_sha256": digest(raw),
                "vertices": len(workspace.local),
                "triangles": len(workspace.data.triangles),
                "display_vertices": len(positions),
                "display_triangles": len(indices),
                "display": {
                    name: {
                        "dtype": array.dtype.str,
                        "components": 3,
                        "bytes": int(array.nbytes),
                        "sha256": digest(array.tobytes()),
                    }
                    for name, array in (("positions", positions), ("indices", indices))
                },
            }
        )
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    write_json(
        directory.parent / "inputs.json",
        {
            "schema_version": 1,
            "created_utc": utc(),
            "repository_revision": revision,
            "fixture_generator": {
                "path": "tests/native_scan_fixture.py",
                "sha256": digest((ROOT / "tests/native_scan_fixture.py").read_bytes()),
            },
            "limits": asdict(NATIVE_SCAN_LIMITS),
            "sources": records,
        },
    )


def process_high_water() -> dict[str, object]:
    try:
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {
            "bytes": int(value if sys.platform == "darwin" else value * 1024),
            "source": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
            "scope": "server process lifetime, including startup/imports; never a per-cycle peak",
        }
    except (ImportError, OSError, ValueError) as error:
        return {"bytes": None, "error": f"{type(error).__name__}: {error}"}


def staging_inventory(staging: Path) -> dict[str, Any]:
    files: list[dict[str, object]] = []
    vanished: list[str] = []
    for path in sorted(staging.rglob("*")):
        try:
            state = path.stat()
        except FileNotFoundError:
            # Receiver cleanup unlinks outside the coordinator lock. The
            # file-selected observation deliberately overlaps that operation.
            vanished.append(str(path.relative_to(staging)))
            continue
        if stat.S_ISREG(state.st_mode):
            files.append(
                {"name": str(path.relative_to(staging)), "bytes": state.st_size}
            )
    return {
        "staging_files": files[:32],
        "staging_file_count": len(files),
        "staging_vanished_during_observation": vanished[:32],
    }


def server_snapshot(server: Any, staging: Path) -> dict[str, Any]:
    """Read existing owners only: no buffer encoding or graph publication."""
    import numpy as np

    with server.lock:
        accepted = server.accepted(buffers=False)
        # Avoid serializing complete recipe selections/results just to count
        # nodes and read the cursor. These private reads are observer-only.
        with accepted.graph.lock:
            token = accepted.graph._token()
            recipe_nodes = len(accepted.graph._recipe.nodes)
        workspace = accepted.workspace
        arrays: list[dict[str, Any]] = []
        roots: dict[int, int] = {}
        named = [
            (f"source.{name}", getattr(workspace.data, name))
            for name in (
                "xyz",
                "normals",
                "original_normals",
                "triangles",
                "weights",
                "face_status",
            )
        ]
        project = server._project
        if project is not None:
            named.extend(
                (
                    ("display.positions", project.accepted.positions),
                    ("display.indices", project.accepted.indices),
                )
            )
        for name, array in named:
            owner: Any = array
            while True:
                base: Any = owner.base
                if not isinstance(base, np.ndarray):
                    break
                owner = cast(Any, base)
            key = id(owner)
            roots[key] = int(owner.nbytes)
            arrays.append(
                {
                    "name": name,
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "logical_bytes": int(array.nbytes),
                    "backing_id": key,
                    "backing_bytes": int(owner.nbytes),
                }
            )
        operations: list[dict[str, Any]] = []
        for operation in server.source_imports._operations.values():
            operations.append(
                {
                    **operation.record(),
                    "retained_references": {
                        name: getattr(operation, name) is not None
                        for name in ("project", "context", "recipe", "path")
                    },
                }
            )
        return {
            "pid": os.getpid(),
            "monotonic_seconds": time.monotonic(),
            "utc": utc(),
            "project_generation": accepted.generation,
            "token": token,
            "source_sha256": workspace.source_sha256,
            "vertices": len(workspace.local),
            "triangles": len(workspace.data.triangles),
            "recipe_nodes": recipe_nodes,
            **source_authority_observation(workspace),
            "arrays": arrays,
            "unique_named_backing_bytes": sum(roots.values()),
            "arrays_caveat": "Named ndarray payloads only; overlaps process RSS; excludes objects, solver temporaries and allocator slack.",
            # The public busy property expires reservations; observation must
            # not drive that state transition.
            "import_busy": server.source_imports._active is not None,
            "operations": operations,
            **staging_inventory(staging),
            "process_high_water": process_high_water(),
        }


def source_authority_observation(workspace: Any) -> dict[str, Any]:
    """Logical replay authority and verified EOF, separate from Python allocation."""
    source = getattr(workspace, "source", None)
    storage = {
        "kind": "none",
        "backing_kind": "none",
        "authority_bytes": 0,
        "owned_source_payload_bytes": 0,
    }
    replay = {
        "bytes": 0,
        "sha256": None,
        "max_chunk_bytes": 0,
        "read_calls": 0,
        "eof_verified": False,
    }
    if source is not None:
        provider = source.input
        backing = provider
        owned = 0
        while True:
            data = getattr(backing, "data", None)
            if isinstance(data, bytes):
                owned += len(data)
            nested = getattr(backing, "authority", None)
            if nested is None:
                break
            backing = nested
        storage = {
            "kind": type(provider).__name__,
            "backing_kind": type(backing).__name__,
            "authority_bytes": source.byte_length,
            "owned_source_payload_bytes": owned,
        }
        hasher = hashlib.sha256()
        total = reads = maximum = 0
        with source.open() as stream:
            while True:
                chunk = stream.read(65_536)
                reads += 1
                if not chunk:
                    break
                total += len(chunk)
                maximum = max(maximum, len(chunk))
                hasher.update(chunk)
        if total != source.byte_length or hasher.hexdigest() != workspace.source_sha256:
            raise AssertionError("Accepted source authority differs at verified EOF")
        replay = {
            "bytes": total,
            "sha256": hasher.hexdigest(),
            "max_chunk_bytes": maximum,
            "read_calls": reads,
            "eof_verified": True,
        }
    return {
        "original_bytes": storage["authority_bytes"],
        "source_storage": storage,
        "source_replay": replay,
        "source_bytes_caveat": "original_bytes is the logical replayable authority length, not a retained Python allocation; owned_source_payload_bytes counts explicit byte backing only.",
    }


def emit(value: object) -> None:
    print(json.dumps(value, allow_nan=False), flush=True)


def run_server(staging: Path) -> None:
    import numpy as np
    import pydantic

    from experiments.nozzle_browser import NozzleServer

    staging.mkdir()
    server = NozzleServer(None, 0, staging_directory=staging)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        loaded_sources: dict[str, str] = {}
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if filename is None:
                continue
            path = Path(filename).resolve()
            if path.is_relative_to(ROOT) and path.is_file():
                loaded_sources[str(path.relative_to(ROOT))] = digest(path.read_bytes())
        emit(
            {
                "event": "ready",
                "pid": os.getpid(),
                "origin": f"http://127.0.0.1:{server.server_port}/",
                "runtime_versions": {
                    "python": sys.version,
                    "numpy": np.__version__,
                    "pydantic": pydantic.__version__,
                },
                "loaded_repository_files_sha256": loaded_sources,
            }
        )
        for line in sys.stdin:
            request = json.loads(line)
            if request["event"] == "shutdown":
                break
            if request["event"] != "snapshot":
                raise ValueError("Unknown measurement control command")
            emit(
                {
                    "event": "snapshot",
                    "sequence": request["sequence"],
                    "snapshot": server_snapshot(server, staging),
                }
            )
    finally:
        server.shutdown()
        thread.join(10)
        server.server_close()
        emit(
            {
                "event": "closed",
                "remaining_staging_files": [
                    str(p.relative_to(staging))
                    for p in staging.rglob("*")
                    if p.is_file()
                ],
            }
        )


@final
class Sampler:
    """Bounded, durable RSS observations with actual timestamps and errors."""

    def __init__(
        self, read_rss: Callable[[], int], path: Path, interval: float
    ) -> None:
        self.read_rss = read_rss
        self.path = path
        self.interval = interval
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.count = 0
        self.errors = 0
        self.limit_reached = False
        self.failure: str | None = None

    def sample(self) -> dict[str, object]:
        record: dict[str, object] = {"monotonic_seconds": time.monotonic()}
        try:
            record["rss_bytes"] = self.read_rss()
        except Exception as error:
            self.errors += 1
            record.update(rss_bytes=None, error=f"{type(error).__name__}: {error}")
        return record

    def run(self) -> None:
        try:
            with self.path.open("x") as stream:
                while not self.stop.is_set():
                    if self.count >= MAX_SAMPLES:
                        self.limit_reached = True
                        break
                    _ = stream.write(json.dumps(self.sample(), allow_nan=False) + "\n")
                    stream.flush()
                    self.count += 1
                    _ = self.stop.wait(self.interval)
        except Exception as error:
            self.failure = f"{type(error).__name__}: {error}"

    def close(self) -> None:
        self.stop.set()
        self.thread.join(5)
        if self.thread.is_alive():
            self.failure = "Sampler thread did not stop"


def sample_summary(
    records: list[dict[str, Any]], start: float, end: float
) -> dict[str, object]:
    interval = [item for item in records if start <= item["monotonic_seconds"] <= end]
    valid = [
        item["rss_bytes"] for item in interval if item.get("rss_bytes") is not None
    ]
    times = [item["monotonic_seconds"] for item in interval]
    return {
        "start_monotonic_seconds": start,
        "end_monotonic_seconds": end,
        "sample_count": len(interval),
        "valid_sample_count": len(valid),
        "error_count": sum("error" in item for item in interval),
        "sampled_rss_min_bytes": min(valid) if valid else None,
        "sampled_rss_max_bytes": max(valid) if valid else None,
        "maximum_observed_gap_seconds": max(
            (b - a for a, b in pairwise(times)), default=None
        ),
        "first_sample_monotonic_seconds": times[0] if times else None,
        "last_sample_monotonic_seconds": times[-1] if times else None,
        "availability": (
            "observed-with-errors"
            if any("error" in item for item in interval)
            else "observed"
        )
        if valid
        else "unavailable",
    }


@final
class JsonPeer:
    """Bounded line protocol reader; stderr is persisted separately."""

    def __init__(self, process: subprocess.Popen[str], log: Path) -> None:
        self.process = process
        self.messages: queue.Queue[dict[str, Any] | Exception | None] = queue.Queue(
            MAX_EVENTS
        )
        self.log = log
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self) -> None:
        try:
            assert self.process.stdout is not None
            with self.log.open("x") as log:
                while line := self.process.stdout.readline(1_048_577):
                    if len(line) > 1_048_576:
                        raise ValueError("Oversized control record")
                    _ = log.write(line)
                    log.flush()
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("Control record must be an object")
                    self.messages.put(value, timeout=5)
        except Exception as error:
            self.messages.put(error, timeout=5)
        finally:
            self.messages.put(None, timeout=5)

    def receive(self, timeout: float) -> dict[str, Any]:
        try:
            message = self.messages.get(timeout=max(0.001, timeout))
        except queue.Empty as error:
            raise TimeoutError("Measurement control response timed out") from error
        if isinstance(message, Exception):
            raise message
        if message is None:
            raise RuntimeError(
                f"Measurement process closed output (exit {self.process.poll()})"
            )
        return message

    def send(self, message: object) -> None:
        assert self.process.stdin is not None
        _ = self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
        self.process.stdin.flush()


def stop_process(
    process: subprocess.Popen[str],
    *,
    graceful: Callable[[], None] | None = None,
    grace_seconds: float = 10,
) -> dict[str, Any]:
    record: dict[str, Any] = {"pid": process.pid, "forced": []}
    if process.poll() is None and graceful is not None:
        try:
            graceful()
        except Exception as error:
            record["graceful_error"] = f"{type(error).__name__}: {error}"
    try:
        try:
            _ = process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            record["forced"].append("terminate")
            process.terminate()
            try:
                _ = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                record["forced"].append("kill")
                process.kill()
                _ = process.wait(timeout=5)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    record["returncode"] = process.poll()
    return record


@final
class OwnedDescendants:
    """Track creation-verified descendants while the owned Node parent lives.

    Browser children may create another session; a process group alone cannot
    account for them. Polling cannot prove absence of unobserved processes that
    spawn and reparent between observations.
    """

    def __init__(self, process: subprocess.Popen[str]) -> None:
        import psutil

        self.psutil: Any = psutil
        self.process = process
        self.root: Any = psutil.Process(process.pid)
        self.root_created: float = self.root.create_time()
        self.observed: dict[tuple[int, float], tuple[Any, dict[str, Any]]] = {}
        self.errors: list[str] = []
        self.races: list[str] = []
        self.limit_reached = False
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self) -> None:
        while not self.stop.is_set() and self.process.poll() is None:
            try:
                if (
                    not self.root.is_running()
                    or self.root.create_time() != self.root_created
                ):
                    break
                for child in self.root.children(recursive=True):
                    try:
                        created = child.create_time()
                        parent = child.ppid()
                    except self.psutil.NoSuchProcess as error:
                        if len(self.races) < 32:
                            self.races.append(str(error))
                        continue
                    key = (child.pid, created)
                    with self.lock:
                        if key not in self.observed:
                            if len(self.observed) >= 256:
                                self.limit_reached = True
                                return
                            self.observed[key] = (
                                child,
                                {
                                    "pid": child.pid,
                                    "create_time": created,
                                    "observed_parent_pid": parent,
                                    "observed_monotonic_seconds": time.monotonic(),
                                    "ownership": "recursive descendant of live creation-verified owned Node parent",
                                },
                            )
                            self.changed.set()
            except self.psutil.NoSuchProcess as error:
                if len(self.races) < 32:
                    self.races.append(str(error))
                break
            except Exception as error:
                if len(self.errors) < 32:
                    self.errors.append(f"{type(error).__name__}: {error}")
                break
            _ = self.stop.wait(0.02)

    def cleanup(self) -> dict[str, Any]:
        self.stop.set()
        self.thread.join(5)
        record: dict[str, Any] = {
            "root_pid": self.process.pid,
            "root_create_time": self.root_created,
            "observed": [],
            "forced": [],
            "remaining": [],
            "errors": self.errors.copy(),
            "observation_races": self.races.copy(),
            "limit_reached": self.limit_reached,
            "coverage": "20ms polling while Node parent lives; short-lived or reparented-before-observation processes may be missed; no global process matching",
        }
        if self.thread.is_alive():
            record["errors"].append("Descendant observer did not stop")
        with self.lock:
            observed = list(self.observed.values())
        record["observed"] = [item for _, item in observed]

        def verified_live(handle: Any, proof: dict[str, Any]) -> bool:
            try:
                return bool(
                    handle.is_running()
                    and handle.create_time() == proof["create_time"]
                    and handle.status() != self.psutil.STATUS_ZOMBIE
                )
            except self.psutil.NoSuchProcess:
                return False
            except Exception as error:
                record["errors"].append(
                    f"verify {proof['pid']}: {type(error).__name__}: {error}"
                )
                return False

        live = [
            (handle, proof)
            for handle, proof in observed
            if verified_live(handle, proof)
        ]
        for handle, proof in live:
            try:
                handle.terminate()
                record["forced"].append(
                    {
                        "pid": proof["pid"],
                        "create_time": proof["create_time"],
                        "action": "terminate",
                    }
                )
            except self.psutil.NoSuchProcess:
                pass
            except Exception as error:
                record["errors"].append(
                    f"terminate {proof['pid']}: {type(error).__name__}: {error}"
                )
        if live:
            try:
                _ = self.psutil.wait_procs([handle for handle, _ in live], timeout=5)
            except Exception as error:
                record["errors"].append(
                    f"wait after terminate: {type(error).__name__}: {error}"
                )
        for handle, proof in live:
            if not verified_live(handle, proof):
                continue
            try:
                handle.kill()
                record["forced"].append(
                    {
                        "pid": proof["pid"],
                        "create_time": proof["create_time"],
                        "action": "kill",
                    }
                )
            except self.psutil.NoSuchProcess:
                pass
            except Exception as error:
                record["errors"].append(
                    f"kill {proof['pid']}: {type(error).__name__}: {error}"
                )
        if live:
            try:
                _ = self.psutil.wait_procs([handle for handle, _ in live], timeout=5)
            except Exception as error:
                record["errors"].append(
                    f"wait after kill: {type(error).__name__}: {error}"
                )
        record["remaining"] = [
            proof for handle, proof in live if verified_live(handle, proof)
        ]
        return record


def validate_observations(
    report: dict[str, Any], manifest: dict[str, Any], cycles: int
) -> list[str]:
    """Functional evidence only: deliberately no RSS or latency thresholds."""
    errors: list[str] = []
    browser: Any = report.get("browser_report")
    if (
        not isinstance(browser, dict)
        or cast(dict[str, Any], browser).get("complete") is not True
    ):
        errors.append("Browser report is missing or incomplete")
    if report.get("browser_finished", {}).get("complete") is not True:
        errors.append("Browser completion event is missing or incomplete")
    sources = {source["id"]: source for source in manifest["sources"]}
    expected = [
        (cycle, source, stage)
        for cycle in range(1, cycles + 1)
        for source in sources
        for stage in STAGES
    ]
    stages = report.get("stages", [])
    actual = [
        (s["event"]["cycle"], s["event"]["source"], s["event"]["stage"]) for s in stages
    ]
    if actual != expected:
        errors.append(
            "Stage sequence does not cover every source/cycle in frozen order"
        )
    prior_generation: int | None = None
    for entry in stages:
        event, state = entry["event"], entry["server"]
        label = f"{event['cycle']}/{event['source']}/{event['stage']}"
        if state["pid"] != report.get("server_pid"):
            errors.append(f"{label}: server process changed")
        generation = state["project_generation"]
        valid_generation = type(generation) is int
        if not valid_generation or (
            prior_generation is not None and generation < prior_generation
        ):
            errors.append(f"{label}: missing/backward project generation")
        source = sources[event["source"]]
        if event["stage"] in ("imported", "inspected", "selection-overlay"):
            storage, replay = (
                state.get("source_storage", {}),
                state.get("source_replay", {}),
            )
            if (
                storage.get("kind") != "PathInput"
                or storage.get("owned_source_payload_bytes") != 0
                or storage.get("authority_bytes") != state["original_bytes"]
                or replay.get("bytes") != state["original_bytes"]
                or replay.get("sha256") != source["source_sha256"]
                or replay.get("eof_verified") is not True
                or not 0 < replay.get("max_chunk_bytes", 0) <= 65_536
                or replay.get("read_calls", 0) <= 0
            ):
                errors.append(
                    f"{label}: streamed source authority evidence missing or differs"
                )
            if (
                state["source_sha256"] != source["source_sha256"]
                or state["vertices"] != source["vertices"]
                or state["triangles"] != source["triangles"]
            ):
                errors.append(f"{label}: accepted source identity/count mismatch")
            if (
                event["stage"] == "imported"
                and valid_generation
                and prior_generation is not None
                and generation <= prior_generation
            ):
                errors.append(f"{label}: import did not advance generation")
        if event["stage"] in ("empty-baseline", "reset"):
            if (
                state["source_sha256"] is not None
                or state["vertices"]
                or state["triangles"]
                or state["original_bytes"]
                or state["recipe_nodes"]
            ):
                errors.append(f"{label}: empty project retains accepted source/actions")
            if (
                event["stage"] == "reset"
                and valid_generation
                and prior_generation is not None
                and generation <= prior_generation
            ):
                errors.append(f"{label}: reset did not advance generation")
        if event["stage"] != "file-selected":
            if state["import_busy"] or state["staging_file_count"]:
                errors.append(f"{label}: source staging remains active")
            if any(
                any(operation["retained_references"].values())
                for operation in state["operations"]
                if operation.get("terminal")
            ):
                errors.append(
                    f"{label}: terminal operation retains source/candidate references"
                )
        # file-selected can already be accepted for tiny sources; never infer a
        # precommit state or use that racing observation as the import baseline.
        if event["stage"] != "file-selected" and valid_generation:
            prior_generation = generation
    cleanup = report.get("cleanup", {})
    for name in ("node", "server"):
        result = cleanup.get(name)
        if (
            not result
            or result.get("returncode") != 0
            or result.get("forced")
            or result.get("error")
            or result.get("graceful_error")
        ):
            errors.append(f"{name} process did not close normally")
    if cleanup.get("remaining_staging_files") != []:
        errors.append("Owned source staging was not cleaned")
    descendants = cleanup.get("node_descendants", {})
    if (
        descendants.get("forced")
        or descendants.get("remaining")
        or descendants.get("errors")
        or descendants.get("limit_reached")
    ):
        errors.append("Node descendant cleanup was forced or incomplete")
    sampler = report.get("sampler", {})
    if (
        sampler.get("sample_count", 0) == 0
        or report.get("rss", {}).get("valid_sample_count", 0) == 0
        or sampler.get("error_count", 0) != 0
        or sampler.get("failure")
        or sampler.get("limit_reached")
    ):
        errors.append("RSS sampler incomplete")
    return errors


def native_run(args: argparse.Namespace) -> int:
    # This import is intentionally AFTER the explicit-interpreter bootstrap.
    import psutil

    output = fresh_output(args.output)
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src"))),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "TMPDIR": str(output),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_utc": utc(),
        "complete": False,
        "native_python": str(args.native_python),
        "monitor_versions": {"python": sys.version, "psutil": psutil.__version__},
        "host": {
            "platform": platform.platform(),
            "python": sys.version,
            "cpu_count": os.cpu_count(),
        },
        "settings": {
            "cycles": args.cycles,
            "rss_interval_seconds": args.sample_interval,
            "watchdog_seconds": args.timeout_seconds,
            "maximum_rss_samples": MAX_SAMPLES,
        },
        "caveats": [
            "Instrumented observations, not production latency or a capacity limit; acknowledgment waits are reported separately by the browser.",
            "Browser performance.now and Python monotonic clocks are separate; no cross-clock subtraction.",
            "RSS, lifetime high-water, named arrays, browser heap and logical GPU counters overlap and must not be summed.",
            "Sampled RSS can miss transient peaks; lifetime high-water includes server imports/startup and cannot reset between cycles.",
            "File selection events can race acceptance; snapshots report the actual observed state.",
        ],
        "instrumentation": {"orchestrator_sha256": digest(SCRIPT.read_bytes())},
        "stages": [],
        "errors": [],
        "cleanup": {},
    }
    node = server = None
    server_peer: JsonPeer | None = None
    sampler = None
    descendants: OwnedDescendants | None = None
    handles: list[TextIO] = []
    manifest: dict[str, Any] = {"sources": []}
    started = time.monotonic()
    sampling_started: float | None = None
    try:
        report["instrumentation"]["node_driver_sha256"] = digest(
            NODE_SCRIPT.read_bytes()
        )
        with (output / "fixture-generation.log").open("x") as fixture_log:
            _ = subprocess.run(
                [
                    str(args.native_python),
                    str(SCRIPT),
                    "--internal-fixtures",
                    str(output / "inputs"),
                ],
                cwd=ROOT,
                env=environment,
                stdout=fixture_log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=120,
            )
        manifest = json.loads((output / "inputs.json").read_text())
        report["inputs_manifest_sha256"] = digest((output / "inputs.json").read_bytes())
        report["repository_revision"] = manifest["repository_revision"]
        server_log = (output / "server.stderr.log").open("x")
        handles.append(server_log)
        server = subprocess.Popen(
            [
                str(args.native_python),
                "-u",
                str(SCRIPT),
                "--internal-server",
                str(output / "staging"),
            ],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=server_log,
            text=True,
            bufsize=1,
        )
        server_peer = JsonPeer(server, output / "server-events.ndjson")
        process = psutil.Process(server.pid)
        sampler = Sampler(
            lambda: process.memory_info().rss,
            output / "server-rss.ndjson",
            args.sample_interval,
        )
        sampling_started = time.monotonic()
        sampler.thread.start()
        ready = server_peer.receive(60)
        if ready.get("event") != "ready" or ready.get("pid") != server.pid:
            raise ValueError("Invalid server readiness record")
        report.update(
            server_pid=server.pid,
            origin=ready["origin"],
            server_runtime_versions=ready["runtime_versions"],
            server_loaded_repository_files_sha256=ready[
                "loaded_repository_files_sha256"
            ],
        )
        node_log = (output / "node.stderr.log").open("x")
        handles.append(node_log)
        node = subprocess.Popen(
            [
                "node",
                str(NODE_SCRIPT),
                "--origin",
                ready["origin"],
                "--inputs",
                str(output / "inputs.json"),
                "--output",
                str(output / "browser"),
                "--playwright-directory",
                str(args.playwright_directory),
                "--cycles",
                str(args.cycles),
                "--event-acks",
            ],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=node_log,
            text=True,
            bufsize=1,
        )
        node_peer = JsonPeer(node, output / "browser-events.ndjson")
        descendants = OwnedDescendants(node)
        sequence = 0
        while True:
            message = node_peer.receive(
                args.timeout_seconds - (time.monotonic() - started)
            )
            receipt = time.monotonic()
            if message.get("schema_version") != 1:
                raise ValueError("Invalid browser event schema")
            if message.get("event") == "finished":
                report["browser_finished"] = message
                break
            if (
                message.get("event") != "stage"
                or message.get("sequence") != sequence + 1
            ):
                raise ValueError("Missing/out-of-order browser stage event")
            sequence += 1
            if sequence > MAX_EVENTS:
                raise ValueError("Browser event budget exceeded")
            server_peer.send({"event": "snapshot", "sequence": sequence})
            snapshot = server_peer.receive(30)
            if (
                snapshot.get("event") != "snapshot"
                or snapshot.get("sequence") != sequence
            ):
                raise ValueError("Mismatched server snapshot")
            report["stages"].append(
                {
                    "event": message,
                    "host_received_monotonic_seconds": receipt,
                    "host_snapshot_received_monotonic_seconds": time.monotonic(),
                    "server": snapshot["snapshot"],
                }
            )
            write_json(output / "native-source-ui.json", report)
            node_peer.send({"schema_version": 1, "event": "ack", "sequence": sequence})
    except BaseException as error:
        report["errors"].append(
            {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        # End the measurement window before deliberate process teardown. An
        # active-operation RSS error remains evidence of incomplete coverage;
        # normal shutdown must not manufacture a NoSuchProcess sample.
        measurement_end = time.monotonic()
        if sampler is not None:
            sampler.close()
        report["measurement_window"] = {
            "start_monotonic_seconds": sampling_started,
            "end_monotonic_seconds": measurement_end,
            "excludes": "fixture generation and deliberate Node/server teardown",
        }
        if node is not None:
            report["cleanup"]["node"] = stop_process(node)
        if descendants is not None:
            report["cleanup"]["node_descendants"] = descendants.cleanup()
        if server is not None:
            report["cleanup"]["server"] = stop_process(
                server,
                graceful=(lambda: server_peer.send({"event": "shutdown"}))
                if server_peer is not None
                else None,
            )
        if sampler is not None:
            report["sampler"] = {
                "sample_count": sampler.count,
                "error_count": sampler.errors,
                "limit_reached": sampler.limit_reached,
                "failure": sampler.failure,
                "path": "server-rss.ndjson",
            }
            samples = (
                [json.loads(line) for line in sampler.path.read_text().splitlines()]
                if sampler.path.is_file()
                else []
            )
            window_start = sampling_started if sampling_started is not None else started
            report["rss"] = sample_summary(samples, window_start, measurement_end)
            previous = window_start
            for stage in report["stages"]:
                boundary = stage["host_snapshot_received_monotonic_seconds"]
                stage["rss_since_previous_stage"] = sample_summary(
                    samples, previous, boundary
                )
                previous = boundary
        for handle in handles:
            handle.close()
        report["cleanup"]["remaining_staging_files"] = [
            str(p.relative_to(output))
            for p in (output / "staging").rglob("*")
            if p.is_file()
        ]
        browser_path = output / "browser/browser-report.json"
        if browser_path.is_file():
            try:
                report["browser_report"] = json.loads(browser_path.read_text())
            except (ValueError, OSError) as error:
                report["errors"].append(
                    {"type": type(error).__name__, "message": str(error)}
                )
        report["validation_errors"] = validate_observations(
            report, manifest, args.cycles
        )
        report["complete"] = not report["errors"] and not report["validation_errors"]
        report["finished_utc"] = utc()
        write_json(output / "native-source-ui.json", report)
    print(
        json.dumps(
            {
                "report": str(output / "native-source-ui.json"),
                "complete": report["complete"],
            }
        )
    )
    return 0 if report["complete"] else 1


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["--internal-fixtures"]:
        generate_inputs(Path(arguments[1]))
        return 0
    if arguments[:1] == ["--internal-server"]:
        run_server(Path(arguments[1]))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--native-python", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument(
        "--playwright-directory", type=Path, default=ROOT / "experiments/browser_viewer"
    )
    _ = parser.add_argument("--cycles", type=int, default=2)
    _ = parser.add_argument("--sample-interval", type=float, default=0.02)
    _ = parser.add_argument("--timeout-seconds", type=float, default=900)
    _ = parser.add_argument(
        "--internal-native-runtime", action="store_true", help=argparse.SUPPRESS
    )
    args = parser.parse_args(arguments)
    if not args.native_python.is_absolute() or not args.native_python.is_file():
        parser.error("--native-python must be an existing absolute project interpreter")
    if not 1 <= args.cycles <= 5:
        parser.error("--cycles must be between 1 and 5 (bounded measurement driver)")
    if not math.isfinite(args.sample_interval) or args.sample_interval <= 0:
        parser.error("--sample-interval must be finite and positive")
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 3600:
        parser.error("--timeout-seconds must be finite and in (0,3600]")
    if args.internal_native_runtime:
        return native_run(args)
    # No third-party import takes place in uv's dependencies=[] environment.
    return subprocess.run(
        [str(args.native_python), str(SCRIPT), *arguments, "--internal-native-runtime"],
        cwd=ROOT,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
