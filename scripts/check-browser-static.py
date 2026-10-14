#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build once and serially verify the bounded static Chromium experiment.

Uses already-installed project/browser dependencies. Uploadable evidence is an
explicit JSON allowlist; downloaded binaries and browser traces stay local until
both manifests declare redistribution readiness. No hosting is configured.
"""

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def utc() -> str:
    return datetime.now(UTC).isoformat()


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular JSON evidence: {path}")
    return json.loads(path.read_bytes())


def identity(path: Path) -> dict:
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def redistribution_gaps(site: dict, runtime: dict) -> list[str]:
    gaps = []
    for label, manifest in (("site", site), ("runtime", runtime)):
        if manifest.get("redistribution_ready") is not True:
            gaps.append(f"{label}: redistribution_ready is not true")
        inventory = manifest.get("redistribution_gaps")
        if not isinstance(inventory, list) or any(
            not isinstance(gap, str) for gap in inventory
        ):
            gaps.append(
                f"{label}: explicit string-list redistribution_gaps is required"
            )
        else:
            gaps.extend(f"{label}: {gap}" for gap in inventory)
        revision = manifest.get("source_revision")
        if (
            not isinstance(revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        ):
            gaps.append(f"{label}: complete source commit is required")
        if manifest.get("source_worktree_dirty") is not False:
            gaps.append(f"{label}: source worktree is dirty or unspecified")
    if site.get("source_revision") != runtime.get("source_revision"):
        gaps.append("UI and runtime source revisions differ")
    if site.get("runtime_redistribution_ready") is not True:
        gaps.append("site: runtime_redistribution_ready is not true")
    return gaps


def prepare_output(path: Path) -> Path:
    # Reject aliases and ancestors too: never create evidence inside an input
    # checkout or overwrite one through a symlink.
    absolute = path.absolute()
    for component in (absolute, *absolute.parents):
        if component.is_symlink():
            raise ValueError(f"symlink output component: {component}")
    resolved = absolute.resolve()
    if resolved.is_relative_to(ROOT) or ROOT.is_relative_to(resolved):
        raise ValueError("output must be outside and not contain the repository")
    resolved.mkdir(parents=True, exist_ok=True)
    if any(resolved.iterdir()):
        raise ValueError("output must be empty; existing evidence is never replaced")
    return resolved


def execute(
    command: list[str],
    log: Path,
    temporary: Path,
    *,
    timeout_seconds: float = 1800,
    grace_seconds: float = 2,
) -> dict:
    step = {"command": command, "started_utc": utc(), "log": str(log)}
    started = time.monotonic()
    environment = dict(os.environ, TMPDIR=str(temporary), PYTHONDONTWRITEBYTECODE="1")
    # Linux CI: give hung drivers and their browser/native descendants one
    # bounded process group. Ordinary cancellation remains the UI's policy.
    with log.open("xb") as stream:
        child = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            step["timeout"] = True
        finally:
            # Also retire descendants left by a failed/signaled driver or an
            # interrupted wrapper. Never leave browser/native probes behind.
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            deadline = time.monotonic() + grace_seconds
            while time.monotonic() < deadline:
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            code = child.wait()
    step.update(
        exit_code=code, elapsed_seconds=time.monotonic() - started, finished_utc=utc()
    )
    return step


def stage_release(
    site: Path, evidence: Path, destination: Path, expected_manifest_sha256: str
) -> None:
    """Copy only hash-verified manifest members, never unlisted harness files."""
    manifest_path = site / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("expected regular tested site manifest")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
        raise ValueError("release manifest differs from tested manifest")
    manifest = json.loads(manifest_bytes)
    files = {}
    for record in manifest["assets"]:
        relative = record["path"]
        if (
            not relative
            or any(part in ("", ".", "..") for part in relative.split("/"))
            or any(character in relative for character in "\\?#:\0")
        ):
            raise ValueError(f"invalid release asset path: {relative}")
        source = site / relative
        if not source.resolve().is_relative_to(site.resolve()) or any(
            component.is_symlink() for component in (source, *source.parents)
        ):
            raise ValueError(f"escaped or symlink release asset: {relative}")
        if relative in files or relative == "manifest.json":
            raise ValueError(f"duplicate/reserved release asset: {relative}")
        data = source.read_bytes()
        if {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()} != {
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }:
            raise ValueError(f"release asset integrity failure: {relative}")
        files[relative] = data
    files["manifest.json"] = manifest_bytes
    if destination.exists():
        raise ValueError("release output already exists")
    for name, data in files.items():
        target = destination / "site" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for source in sorted(evidence.glob("*.json")):
        _ = read_json(source)
        target = destination / "evidence" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-python", type=Path, default=ROOT / ".venv/bin/python")
    parser.add_argument(
        "--playwright-directory", type=Path, default=ROOT / "experiments/browser_viewer"
    )
    parser.add_argument(
        "--cache", type=Path, help="optional existing hash-verified download cache"
    )
    parser.add_argument(
        "--redistribution-cache",
        type=Path,
        help="optional private source/notice acquisition cache, separate from runtime assets",
    )
    parser.add_argument(
        "--measurements",
        action="store_true",
        help="include the four advisory workload drivers",
    )
    args = parser.parse_args()
    output = prepare_output(args.output)
    evidence, work = output / "evidence", output / "work"
    evidence.mkdir()
    work.mkdir()
    logs = work / "logs"
    logs.mkdir()
    temporary = work / "tmp"
    temporary.mkdir()
    # Resolving a venv's interpreter symlink selects the base interpreter and
    # loses its installed project packages. Preserve the user-selected path.
    native = str(args.native_python.absolute())
    runtime, site = work / "runtime", work / "site"
    redistribution = work / "redistribution"
    numerical_runtime, numerical_site = (
        work / "numerical-runtime",
        work / "numerical-site",
    )
    report = {
        "schema_version": 1,
        "kind": "scansor-static-ci-verification",
        "started_utc": utc(),
        "steps": [],
        "verified": False,
        "redistribution_ready": False,
        "redistribution_gaps": [],
        "browser_matrix": ["desktop Chromium, Linux CI, retained bounded example"],
        "measurement_policy": "Serial loopback diagnostics; no latency or peak-memory thresholds. Counts overlap and are not summed. No WAN/offline/other-engine claims.",
        "evidence_policy": "Only named driver JSON reports and metadata manifests are copied. No wheel, wasm, trace ZIP, source archive, screenshot, generated benchmark site or log upload.",
    }

    def run(name: str, command: list[str], result: Path | None = None) -> None:
        print(f"{name}: running", flush=True)
        step = execute(command, logs / f"{name}.log", temporary)
        step["name"] = name
        report["steps"].append(step)
        if result is not None and result.is_file():
            _ = read_json(result)
            destination = evidence / f"{name}.json"
            destination.write_bytes(result.read_bytes())
            step["evidence"] = {"path": destination.name, **identity(destination)}
        if step["exit_code"] != 0 or step.get("timeout"):
            print(
                (logs / f"{name}.log").read_text(errors="replace")[-12000:],
                file=sys.stderr,
            )
            raise RuntimeError(
                f"{name} failed; see {step['evidence']['path'] if 'evidence' in step else step['log']}"
            )
        if result is not None and "evidence" not in step:
            raise ValueError(
                f"{name} exited successfully without its required JSON report"
            )
        print(f"{name}: passed", flush=True)

    def python(name: str, script: str, options: list[str]) -> None:
        run(name, [sys.executable, str(ROOT / "scripts" / script), *options])

    def driver(
        name: str,
        filename: str,
        report_name: str,
        selected_site: Path = site,
        extra: tuple[str, ...] = (),
    ) -> None:
        destination = work / name
        run(
            name,
            [
                "node",
                str(ROOT / "experiments/browser_static" / filename),
                "--directory",
                str(selected_site),
                "--output",
                str(destination),
                "--playwright-directory",
                str(args.playwright_directory.resolve()),
                "--repository",
                str(ROOT),
                "--native-python",
                native,
                *extra,
            ],
            destination / report_name,
        )

    failure = None
    try:
        redistribution_cache = (
            args.redistribution_cache.resolve()
            if args.redistribution_cache is not None
            else work / "redistribution-cache"
        )
        run(
            "build-redistribution",
            [
                sys.executable,
                str(ROOT / "scripts/build-browser-redistribution.py"),
                "--inventory",
                str(ROOT / "scripts/browser-redistribution-lock.json"),
                "--output",
                str(redistribution),
                "--cache",
                str(redistribution_cache),
            ],
            redistribution / "manifest.json",
        )
        report["redistribution-manifest"] = identity(
            evidence / "build-redistribution.json"
        )
        supplement = ["--redistribution", str(redistribution)]
        cache = [] if args.cache is None else ["--cache", str(args.cache.resolve())]
        python(
            "build-runtime",
            "build-browser-runtime.py",
            [
                "--output",
                str(runtime),
                "--cad",
                "--native-python",
                native,
                *cache,
                *supplement,
            ],
        )
        python(
            "build-site",
            "build-browser-app.py",
            ["--runtime", str(runtime), "--output", str(site), *supplement],
        )
        python(
            "verify-runtime",
            "build-browser-runtime.py",
            ["--output", str(runtime), "--verify"],
        )
        python(
            "verify-site", "build-browser-app.py", ["--output", str(site), "--verify"]
        )
        for name, directory in (("site-manifest", site), ("runtime-manifest", runtime)):
            destination = evidence / f"{name}.json"
            destination.write_bytes((directory / "manifest.json").read_bytes())
            report[name] = identity(destination)
        driver("startup", "validate.mjs", "browser-static.json")
        driver(
            "startup-isolated",
            "validate.mjs",
            "browser-static.json",
            extra=("--isolation",),
        )
        driver("workspace", "validate-workspace.mjs", "workspace.json")
        driver("cad", "validate-cad.mjs", "cad.json")
        driver("recovery", "validate-recovery.mjs", "recovery.json")
        driver(
            "recovery-isolated",
            "validate-recovery.mjs",
            "recovery.json",
            extra=("--isolation",),
        )
        # Reuse downloads within this job; never expose a persistent binary cache.
        python(
            "build-numerical-runtime",
            "build-browser-runtime.py",
            [
                "--output",
                str(numerical_runtime),
                "--cache",
                str(runtime),
                "--native-python",
                native,
                *supplement,
            ],
        )
        python(
            "build-numerical-site",
            "build-browser-app.py",
            [
                "--runtime",
                str(numerical_runtime),
                "--output",
                str(numerical_site),
                *supplement,
            ],
        )
        driver(
            "numerical-workspace",
            "validate-workspace.mjs",
            "workspace.json",
            numerical_site,
        )
        if args.measurements:
            driver("timing", "measure.mjs", "report.json")
            driver("memory", "measure-memory.mjs", "memory.json")
            driver("fitting", "measure-fitting.mjs", "report.json")
            driver("generated", "measure-generated.mjs", "generated.json")
        python(
            "verify-site-after",
            "build-browser-app.py",
            ["--output", str(site), "--verify"],
        )
        python(
            "verify-numerical-site-after",
            "build-browser-app.py",
            ["--output", str(numerical_site), "--verify"],
        )
        if identity(site / "manifest.json") != report["site-manifest"]:
            raise ValueError("tested site manifest changed")
        report["verified"] = True
        gaps = redistribution_gaps(
            read_json(site / "manifest.json"), read_json(runtime / "manifest.json")
        )
        report["redistribution_gaps"] = gaps
        report["redistribution_ready"] = not gaps
    except Exception as error:
        failure = error
        report["failure"] = str(error)
    finally:
        report["finished_utc"] = utc()
        (evidence / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
        if report["redistribution_ready"]:
            try:
                stage_release(
                    site,
                    evidence,
                    output / "release",
                    report["site-manifest"]["sha256"],
                )
            except Exception as error:
                failure = error
                report["redistribution_ready"] = False
                report["failure"] = str(error)
                (evidence / "verification.json").write_text(
                    json.dumps(report, indent=2) + "\n"
                )
        if filename := os.environ.get("GITHUB_OUTPUT"):
            with Path(filename).open("a") as stream:
                stream.write(
                    f"redistribution_ready={str(report['redistribution_ready']).lower()}\n"
                )
        if filename := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(filename).open("a") as stream:
                stream.write(
                    f"\nStatic Chromium verification: **{'passed' if report['verified'] else 'failed'}**.\n\n"
                )
                stream.write(
                    f"Binary artifact upload: **{'eligible' if report['redistribution_ready'] else 'blocked'}**. Evidence contains the exact tested manifest hashes.\n\n"
                )
                for gap in report["redistribution_gaps"]:
                    stream.write(f"- {gap}\n")
    if failure is not None:
        raise failure
    print(
        json.dumps(
            {
                "verified": report["verified"],
                "redistribution_ready": report["redistribution_ready"],
                "evidence": str(evidence),
                "tested_site": str(site),
            }
        )
    )


if __name__ == "__main__":
    main()
