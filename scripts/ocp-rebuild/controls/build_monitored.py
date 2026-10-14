#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Launch one guarded OCP build; stop before resource exhaustion; retain logs."""

import contextlib
import datetime
import json
import os
import resource
import shutil
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
env = os.environ | json.loads((ROOT / "build-environment.json").read_text())
command = [
    str(ROOT / "venv/bin/pyodide"),
    "build",
    "--exports=pyinit",
    "--no-isolation",
    "--skip-emscripten-install",
    "-Ccmake.define.ENABLE_IPO=OFF",
    "-v",
]
cwd = ROOT / "src/ocp-wasm/cadquery-ocp-novtk"
GIB = 1024**3


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (14 * GIB, 14 * GIB))


def usage(group):
    snapshot = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            stat = (p / "stat").read_text().split(") ", 1)[1].split()
            memory = int(stat[21]) * os.sysconf("SC_PAGE_SIZE")
            snapshot[int(p.name)] = {
                "pid": int(p.name),
                "parent": int(stat[1]),
                "process_group": int(stat[2]),
                "start_ticks": int(stat[19]),
                "rss_bytes": memory,
                "command": (p / "comm").read_text().strip(),
            }
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    descendants = {group}
    while True:
        children = {
            pid for pid, item in snapshot.items() if item["parent"] in descendants
        }
        if children.issubset(descendants):
            break
        descendants |= children
    processes = [snapshot[pid] for pid in sorted(descendants) if pid in snapshot]
    # Record inputs only for identity-verified owned compilers; omit host argv.
    for item in processes:
        if item["command"] not in ("clang", "clang++"):
            continue
        with contextlib.suppress(
            FileNotFoundError, ProcessLookupError, PermissionError
        ):
            stat = (
                Path(f"/proc/{item['pid']}/stat").read_text().split(") ", 1)[1].split()
            )
            if int(stat[19]) != item["start_ticks"]:
                continue
            arguments = (
                Path(f"/proc/{item['pid']}/cmdline").read_bytes().decode().split("\0")
            )
            item["source_files"] = [
                argument
                for argument in arguments
                if argument.endswith((".cpp", ".cxx", ".c"))
            ]
    rss = sum(item["rss_bytes"] for item in processes)
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    return {
        "rss_bytes": rss,
        "mem_available_bytes": available,
        "disk_free_bytes": shutil.disk_usage(ROOT).free,
        "processes": processes,
    }


started = time.monotonic()
with (ROOT / "logs/build.log").open("w") as log:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        preexec_fn=limits,
    )
    print("BUILD_PID", process.pid, "command", command, flush=True)
    job = {
        "pid": process.pid,
        "process_group": process.pid,
        "started_utc": datetime.datetime.now(datetime.UTC).isoformat(),
        "command": command,
        "cwd": str(cwd),
        "limits": {
            "per_process_address_space": 14 * GIB,
            "aggregate_rss": 12 * GIB,
            "minimum_mem_available": 5 * GIB,
            "minimum_disk_free": 8 * GIB,
            "timeout_seconds": 21600,
        },
        "finished": False,
    }
    (ROOT / "build-job.json").write_text(json.dumps(job, indent=2) + "\n")
    stop = None
    last_report = -60
    peak_rss = 0
    with (ROOT / "logs/resources.jsonl").open("w") as resources:
        while process.poll() is None:
            current = usage(process.pid)
            elapsed = time.monotonic() - started
            peak_rss = max(peak_rss, current["rss_bytes"])
            resources.write(json.dumps({"elapsed_seconds": elapsed, **current}) + "\n")
            resources.flush()
            if current["rss_bytes"] > 12 * GIB:
                stop = "aggregate RSS exceeded 12 GiB"
            elif current["mem_available_bytes"] < 5 * GIB:
                stop = "host available memory fell below 5 GiB"
            elif current["disk_free_bytes"] < 8 * GIB:
                stop = "filesystem free space fell below 8 GiB"
            elif elapsed > 21600:
                stop = "six-hour build timeout"
            if stop:
                # Ninja compiler commands have separate process groups. Verify
                # snapshot identities before stopping every owned descendant group.
                groups = {process.pid}
                for item in current["processes"]:
                    try:
                        stat = (
                            Path(f"/proc/{item['pid']}/stat")
                            .read_text()
                            .split(") ", 1)[1]
                            .split()
                        )
                        if int(stat[19]) == item["start_ticks"]:
                            groups.add(item["process_group"])
                    except (FileNotFoundError, ProcessLookupError):
                        pass
                for group in groups:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(group, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    for group in groups:
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(group, signal.SIGKILL)
                    process.wait()
                break
            if elapsed - last_report >= 45:
                print(
                    json.dumps(
                        {
                            "elapsed_seconds": round(elapsed),
                            "rss_gib": round(current["rss_bytes"] / GIB, 3),
                            "available_gib": round(
                                current["mem_available_bytes"] / GIB, 3
                            ),
                            "disk_free_gib": round(current["disk_free_bytes"] / GIB, 3),
                            "processes": current["processes"],
                        }
                    ),
                    flush=True,
                )
                last_report = elapsed
            time.sleep(1)
    job.update(
        finished=True,
        returncode=process.wait(),
        stop_reason=stop,
        elapsed_seconds=time.monotonic() - started,
        peak_rss_bytes=peak_rss,
    )
    (ROOT / "build-job.json").write_text(json.dumps(job, indent=2) + "\n")
    print("BUILD_FINISHED", json.dumps(job), flush=True)
    raise SystemExit(job["returncode"] if not stop else 125)
