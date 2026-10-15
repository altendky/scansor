"""The CI release boundary rejects incomplete provenance and untested files."""

import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-browser-static.py"
SPEC = importlib.util.spec_from_file_location("static_ci", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


def ready() -> dict[str, object]:
    return {
        "redistribution_ready": True,
        "runtime_redistribution_ready": True,
        "redistribution_gaps": [],
        "source_worktree_dirty": False,
        "source_revision": "a" * 40,
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("redistribution_ready", False),
        ("redistribution_ready", "true"),
        ("redistribution_gaps", ["corresponding source missing"]),
        ("source_worktree_dirty", True),
        ("source_revision", "different-source"),
    ],
)
def test_runtime_incomplete_even_when_site_claims_ready(key: str, value: object):
    runtime = ready()
    runtime[key] = value
    assert CI.redistribution_gaps(ready(), runtime)


def test_explicit_clean_readiness_is_required_from_both_manifests():
    assert CI.redistribution_gaps(ready(), ready()) == []
    assert CI.redistribution_gaps({}, {})
    site = ready()
    site["runtime_redistribution_ready"] = False
    assert CI.redistribution_gaps(site, ready())


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_revision", None),
        ("source_revision", ""),
        ("source_revision", "not-a-commit"),
        ("redistribution_gaps", None),
        ("redistribution_gaps", ""),
        ("redistribution_gaps", [False]),
    ],
)
def test_matching_missing_or_malformed_metadata_is_not_release_ready(
    key: str, value: object
):
    manifest = ready()
    manifest[key] = value
    assert CI.redistribution_gaps(manifest, manifest)
    del manifest[key]
    assert CI.redistribution_gaps(manifest, manifest)


def site_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    site, evidence, release = (
        tmp_path / "site",
        tmp_path / "evidence",
        tmp_path / "release",
    )
    site.mkdir()
    evidence.mkdir()
    _ = (site / "index.html").write_text("tested asset")
    _ = (site / "manifest.json").write_text(
        json.dumps(
            {"assets": [{"path": "index.html", **CI.identity(site / "index.html")}]}
        )
    )
    _ = (site / "unlisted-wheel.whl").write_bytes(b"never upload")
    _ = (evidence / "verification.json").write_text('{"verified":true}\n')
    _ = (evidence / "trace.zip").write_bytes(b"resource bodies never upload")
    return site, evidence, release


def test_release_contains_only_exact_tested_members_and_named_json(tmp_path: Path):
    site, evidence, release = site_fixture(tmp_path)
    CI.stage_release(
        site,
        evidence,
        release,
        CI.identity(site / "manifest.json")["sha256"],
        numerical_site=site,
        expected_numerical_manifest_sha256=CI.identity(site / "manifest.json")[
            "sha256"
        ],
    )
    files = {
        path.relative_to(release).as_posix()
        for path in release.rglob("*")
        if path.is_file()
    }
    assert files == {
        "site/index.html",
        "site/manifest.json",
        "evidence/verification.json",
        "evidence/numerical-assets/index.html",
    }
    assert (release / "site/index.html").read_bytes() == (
        site / "index.html"
    ).read_bytes()
    assert (release / "site/manifest.json").read_bytes() == (
        site / "manifest.json"
    ).read_bytes()


@pytest.mark.parametrize("fault", ["corrupt", "symlink", "traversal", "duplicate"])
def test_release_preflight_rejects_changed_or_escaped_assets_without_writes(
    tmp_path: Path, fault: str
):
    site, evidence, release = site_fixture(tmp_path)
    manifest = json.loads((site / "manifest.json").read_text())
    if fault == "corrupt":
        _ = (site / "index.html").write_text("changed asset")
    elif fault == "symlink":
        _ = (site / "index.html").rename(tmp_path / "outside.html")
        (site / "index.html").symlink_to(tmp_path / "outside.html")
    elif fault == "traversal":
        manifest["assets"][0]["path"] = "../outside.html"
    else:
        manifest["assets"].append(manifest["assets"][0])
    _ = (site / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        CI.stage_release(
            site,
            evidence,
            release,
            CI.identity(site / "manifest.json")["sha256"],
            numerical_site=site,
            expected_numerical_manifest_sha256=CI.identity(site / "manifest.json")[
                "sha256"
            ],
        )
    assert not release.exists()


def test_reports_cannot_overwrite_checkout_or_existing_evidence(tmp_path: Path):
    with pytest.raises(ValueError, match="repository"):
        CI.prepare_output(SCRIPT.parent)
    _ = (tmp_path / "keep.json").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        CI.prepare_output(tmp_path)
    assert (tmp_path / "keep.json").read_text() == "keep"


def test_release_manifest_must_be_the_one_tested(tmp_path: Path):
    site, evidence, release = site_fixture(tmp_path)
    expected = CI.identity(site / "manifest.json")["sha256"]
    manifest = json.loads((site / "manifest.json").read_text())
    manifest["untested"] = True
    _ = (site / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="tested manifest"):
        CI.stage_release(
            site,
            evidence,
            release,
            expected,
            numerical_site=site,
            expected_numerical_manifest_sha256=expected,
        )
    assert not release.exists()


def test_release_stages_the_bytes_it_hashed_even_if_input_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    site, evidence, release = site_fixture(tmp_path)
    asset = site / "index.html"
    expected_bytes = asset.read_bytes()
    expected_manifest = CI.identity(site / "manifest.json")["sha256"]
    numerical_site = tmp_path / "numerical-site"
    _ = shutil.copytree(site, numerical_site)
    original_read = Path.read_bytes

    def read_then_change(path: Path) -> bytes:
        data = original_read(path)
        if path == asset:
            _ = asset.write_bytes(b"changed after first read")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    CI.stage_release(
        site,
        evidence,
        release,
        expected_manifest,
        numerical_site=numerical_site,
        expected_numerical_manifest_sha256=expected_manifest,
    )
    assert (release / "site/index.html").read_bytes() == expected_bytes


def test_failed_build_stops_drivers_and_records_closed_release_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output, github_output = tmp_path / "run", tmp_path / "github-output"
    calls: list[list[str]] = []

    def failed(command: list[str], log: Path, _temporary: Path) -> dict[str, object]:
        calls.append(command)
        _ = log.write_text("deliberate build failure")
        return {"exit_code": 7, "log": str(log)}

    monkeypatch.setattr(CI, "execute", failed)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output", str(output)])
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with pytest.raises(RuntimeError, match="build-redistribution failed"):
        CI.main()
    assert len(calls) == 1
    report = json.loads((output / "evidence/verification.json").read_text())
    assert report["verified"] is False
    assert report["redistribution_ready"] is False
    assert report["steps"][0]["exit_code"] == 7
    assert report["steps"][0]["name"] == "build-redistribution"
    assert github_output.read_text() == "redistribution_ready=false\n"
    assert not (output / "release").exists()


def test_missing_supplement_manifest_keeps_release_gate_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "run"
    calls: list[list[str]] = []

    def missing(command: list[str], log: Path, _temporary: Path) -> dict[str, object]:
        calls.append(command)
        _ = log.write_text("No verified package manifest produced")
        return {"exit_code": 0, "log": str(log)}

    monkeypatch.setattr(CI, "execute", missing)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output", str(output)])
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with pytest.raises(ValueError, match="required JSON report"):
        CI.main()
    assert len(calls) == 1
    report = json.loads((output / "evidence/verification.json").read_text())
    assert report["redistribution_ready"] is False
    assert not (output / "release").exists()


def test_supplement_precedes_all_builders_and_runtime_completion_allows_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output, github_output = tmp_path / "run", tmp_path / "github-output"
    calls: list[list[str]] = []

    def successful(
        command: list[str], log: Path, _temporary: Path
    ) -> dict[str, object]:
        calls.append(command)
        _ = log.write_text("Simulated command boundary; not runtime validation")
        script = Path(command[1]).name
        destination = Path(command[command.index("--output") + 1])
        if "--verify" not in command:
            destination.mkdir(parents=True)
            if script == "build-browser-redistribution.py":
                # The runtime, not this supplemental flag, fulfills core notices.
                _ = (destination / "manifest.json").write_text(
                    json.dumps({"redistribution_ready": False})
                )
            elif script == "build-browser-runtime.py":
                _ = (destination / "manifest.json").write_text(json.dumps(ready()))
            elif script == "build-browser-app.py":
                asset = destination / "index.html"
                _ = asset.write_text("tested static fixture")
                _ = (destination / "manifest.json").write_text(
                    json.dumps(
                        ready()
                        | {"assets": [{"path": "index.html", **CI.identity(asset)}]}
                    )
                )
            else:
                name = {
                    "validate.mjs": "browser-static.json",
                    "validate-workspace.mjs": "workspace.json",
                    "validate-cad.mjs": "cad.json",
                    "validate-recovery.mjs": "recovery.json",
                    "validate-source-import.mjs": "source-import.json",
                }[script]
                _ = (destination / name).write_text('{"simulated":true}\n')
        return {"exit_code": 0, "log": str(log)}

    monkeypatch.setattr(CI, "execute", successful)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output", str(output)])
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    CI.main()
    assert len(calls) == 20
    assert Path(calls[0][1]).name == "build-browser-redistribution.py"
    assert calls[0][calls[0].index("--cache") + 1] == str(
        output / "work/redistribution-cache"
    )
    builders = [
        command
        for command in calls
        if Path(command[1]).name in ("build-browser-runtime.py", "build-browser-app.py")
        and "--verify" not in command
    ]
    assert len(builders) == 4
    assert all(
        command[command.index("--redistribution") + 1]
        == str(output / "work/redistribution")
        for command in builders
    )
    report = json.loads((output / "evidence/verification.json").read_text())
    assert report["verified"] and report["redistribution_ready"]
    assert report["redistribution-manifest"] == CI.identity(
        output / "evidence/build-redistribution.json"
    )
    assert github_output.read_text() == "redistribution_ready=true\n"
    assert (output / "release/site/index.html").read_text() == "tested static fixture"
    assert (output / "release/evidence/build-redistribution.json").is_file()
    assert report["numerical-runtime-manifest"] == CI.identity(
        output / "evidence/numerical-runtime-manifest.json"
    )
    assert (output / "release/evidence/numerical-assets/index.html").is_file()


@pytest.mark.parametrize("fault", ["manifest", "asset", "symlink"])
def test_numerical_release_proof_is_preflighted_before_any_publication(
    tmp_path: Path, fault: str
):
    site, evidence, release = site_fixture(tmp_path)
    numerical = tmp_path / "numerical"
    _ = shutil.copytree(site, numerical)
    expected = CI.identity(numerical / "manifest.json")["sha256"]
    if fault == "manifest":
        _ = (numerical / "manifest.json").write_text('{"changed":true}')
    elif fault == "asset":
        _ = (numerical / "index.html").write_text("changed")
    else:
        (numerical / "index.html").unlink()
        (numerical / "index.html").symlink_to(site / "index.html")
    with pytest.raises(ValueError):
        CI.stage_release(
            site,
            evidence,
            release,
            CI.identity(site / "manifest.json")["sha256"],
            numerical_site=numerical,
            expected_numerical_manifest_sha256=expected,
        )
    assert not release.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux CI process-group cleanup")
def test_failed_driver_does_not_leave_a_stubborn_descendant(tmp_path: Path):
    pid_file = tmp_path / "pid"
    child_code = "import os,signal,sys,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)"
    code = f"import pathlib,subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]])\nwhile not pathlib.Path(sys.argv[1]).exists(): time.sleep(.01)\nsys.exit(7)\n"
    result = CI.execute(
        [sys.executable, "-c", code, str(pid_file)],
        tmp_path / "driver.log",
        tmp_path,
        timeout_seconds=5,
        grace_seconds=0.1,
    )
    assert result["exit_code"] == 7
    pid = int(pid_file.read_text())
    status = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 5
    state = None
    while time.monotonic() < deadline:
        try:
            state = status.read_text().split(") ", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            # procfs can lose the process between opening and reading stat.
            state = None
            break
        if state == "Z":
            break
        time.sleep(0.01)
    if state is not None:
        # A terminated descendant can await the host reaper as a zombie; it
        # cannot execute or retain the computation after group cancellation.
        assert state == "Z"
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
