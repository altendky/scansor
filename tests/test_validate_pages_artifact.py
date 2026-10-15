"""Synthetic selection fixtures exercise guards, not browser/runtime execution."""

import copy
import importlib.util
import json
import shutil
import stat
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pages_artifact", ROOT / "scripts/validate-pages-artifact.py"
)
assert SPEC is not None and SPEC.loader is not None
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def encoded(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True).encode() + b"\n"


def record(name: str, data: bytes, **extra: Any) -> dict[str, Any]:
    return {"path": name, **VALIDATOR.identity(data), **extra}


def semantic_report(name: str) -> dict[str, Any]:
    """Synthetic complete records, never evidence of actual worker execution."""
    graph = {
        "recipe": {"nodes": [{"id": "source", "operation": "source"}]},
        "token": "2" * 64,
        "states": {"source": "ready"},
        "errors": {},
        "diagnostics": {},
        "memberships": {},
        "results": {"source": {"vertices": 3}},
        "derived": {},
        "revision": 1,
    }
    profile, ids = VALIDATOR.SEMANTIC_DRIVERS[name]
    value = graph if profile == "graph-snapshot" else {"fit": {"radius": 1.0}}
    cases = [
        {"id": case_id, "profile": profile, "actual": value, "expected": value}
        for case_id in ids
    ]
    result = semantic_evidence(cases)
    if profile == "graph-snapshot":
        result["browser_snapshots"] = dict.fromkeys(
            ("default", "edited", "replayed"), graph
        )
        result["native_snapshots"] = dict.fromkeys(("default", "edited"), graph)
    return copy.deepcopy(result)


def semantic_evidence(cases: list[dict[str, Any]]) -> dict[str, Any]:
    raw = encoded({"cases": cases})
    receipt = VALIDATOR.trusted_semantic_policy()["compare_batch"](cases)
    receipt["input_sha256"] = VALIDATOR.sha256(raw)
    return {
        "semantic_parity": receipt,
        "semantic_parity_evidence": {"json": raw.decode(), **VALIDATOR.identity(raw)},
    }


def metadata() -> tuple[dict[str, Any], dict[str, Any]]:
    run = {
        "id": 12,
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "head_branch": "main",
        "path": ".github/workflows/ci.yml",
        "head_sha": "a" * 40,
        "repository": {"id": 34, "full_name": "altendky/scansor"},
        "head_repository": {"id": 34, "full_name": "altendky/scansor"},
        "run_started_at": "2026-01-01T00:00:00Z",
    }
    artifact = {
        "id": 56,
        "name": "validated-static-browser",
        "expired": False,
        "expires_at": "2099-01-01T00:00:00Z",
        "created_at": "2026-01-01T00:01:00Z",
        "size_in_bytes": 123,
        "digest": "sha256:" + "b" * 64,
        "url": "https://api.github.com/repos/altendky/scansor/actions/artifacts/56",
        "archive_download_url": "https://api.github.com/repos/altendky/scansor/actions/artifacts/56/zip",
        "workflow_run": {
            "id": 12,
            "repository_id": 34,
            "head_repository_id": 34,
            "head_branch": "main",
            "head_sha": "a" * 40,
        },
    }
    return run, artifact


def validate_metadata(run: dict[str, Any], artifact: dict[str, Any]) -> str:
    return VALIDATOR.validate_metadata(
        run,
        artifact,
        repository="altendky/scansor",
        run_id=12,
        artifact_id=56,
        archive_sha256="b" * 64,
        archive_size=123,
        now=datetime(2026, 1, 2, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("run", "event", "pull_request"),
        ("run", "head_branch", "feature"),
        ("run", "status", "in_progress"),
        ("run", "conclusion", "failure"),
        ("run", "path", ".github/workflows/untrusted.yml"),
        ("run", "id", 13),
        ("run", "head_sha", "abbreviated"),
        ("artifact", "id", 57),
        ("artifact", "name", "latest-site"),
        ("artifact", "expired", True),
        ("artifact", "expired", "false"),
        ("artifact", "expires_at", "2026-01-01T00:00:00Z"),
        ("artifact", "digest", "sha256:" + "c" * 64),
        ("artifact", "size_in_bytes", 122),
        ("artifact", "url", "https://example.invalid/artifact"),
        ("artifact", "created_at", "2025-12-31T23:59:59Z"),
    ],
)
def test_selected_metadata_must_be_successful_main_ci_and_exact_artifact(
    target: str,
    key: str,
    value: Any,
):
    run, artifact = metadata()
    (run if target == "run" else artifact)[key] = value
    with pytest.raises(ValueError):
        _ = validate_metadata(run, artifact)


@pytest.mark.parametrize(
    "target,key,value",
    [
        ("repository", "full_name", "other/scansor"),
        ("head_repository", "id", 99),
        ("workflow_run", "id", 13),
        ("workflow_run", "head_repository_id", 99),
        ("workflow_run", "head_sha", "c" * 40),
        ("workflow_run", "head_branch", "feature"),
    ],
)
def test_repository_and_run_linkage_cannot_be_substituted(
    target: str, key: str, value: Any
):
    run, artifact = metadata()
    (artifact if target == "workflow_run" else run)[target][key] = value
    with pytest.raises(ValueError):
        _ = validate_metadata(run, artifact)


@pytest.fixture
def selected(tmp_path: Path) -> dict[str, Any]:
    """A tiny schema-valid historical package, with no real browser claims."""
    locks = tmp_path / "selected-locks"
    locks.mkdir()
    numeric = b"synthetic runtime; never executed"
    cad = b"synthetic locked CAD bytes; never installed"
    core = b"synthetic core license"
    runtime_lock: dict[str, Any] = {
        "schema_version": 1,
        "versions": {"python": "historical-fixture"},
        "assets": [
            record("runtime/pyodide.mjs", numeric, group="numeric"),
            record("runtime/cad.whl", cad, group="cad"),
        ],
        "licenses": [record("notices/core/LICENSE", core, group="license")],
    }
    _ = (locks / "runtime-lock.json").write_bytes(encoded(runtime_lock))
    _ = (locks / "package-lock.json").write_bytes(
        encoded({"lockfileVersion": 3, "packages": {}})
    )
    files: list[dict[str, Any]] = []
    groups: dict[str, list[str]] = {}
    requirements: dict[str, Any] = {}
    for group in ("numeric", "cad", "ui"):
        data = f"synthetic {group} notice".encode()
        name = f"{group}-LICENSE"
        _ = (locks / name).write_bytes(data)
        destination = f"notices/{group}/LICENSE"
        files.append(record(destination, data, group=group, source_file=name))
        groups[group] = [f"{group}-notice"]
        requirements[f"{group}-notice"] = {
            "kind": "assets",
            "paths": [destination],
            "basis": "synthetic fixture",
        }
    requirements["existing-core-and-wheel-notices"] = {
        "kind": "external",
        "reason": "runtime supplies locked core notices",
    }
    groups["numeric"].append("existing-core-and-wheel-notices")
    inventory: dict[str, Any] = {
        "schema_version": 1,
        "files": files,
        "archives": [],
        "covered_sources": [],
        "requirements": requirements,
        "requirement_groups": groups,
        "bindings": {
            "runtime_lock_sha256": VALIDATOR.sha256(
                (locks / "runtime-lock.json").read_bytes()
            ),
            "ui_package_lock_sha256": VALIDATOR.sha256(
                (locks / "package-lock.json").read_bytes()
            ),
        },
    }
    _ = (locks / "browser-redistribution-lock.json").write_bytes(encoded(inventory))
    assembler = VALIDATOR.runpy.run_path(
        str(ROOT / "scripts/build-browser-redistribution.py")
    )
    runtime_builder = VALIDATOR.runpy.run_path(
        str(ROOT / "scripts/build-browser-runtime.py")
    )
    app_builder = VALIDATOR.runpy.run_path(str(ROOT / "scripts/build-browser-app.py"))
    package = tmp_path / "package"
    assembled = assembler["assemble"](
        locks / "browser-redistribution-lock.json", package, None
    )
    _ = assembled
    assembled = assembler["verify"](
        package, inventory_path=locks / "browser-redistribution-lock.json"
    )

    def supplement(groups: list[str]) -> tuple[dict[str, bytes], dict[str, Any]]:
        manifest = assembler["select_verified_manifest"](assembled, groups)
        assets = {
            f"redistribution/{item['path']}": (package / item["path"]).read_bytes()
            for item in manifest["assets"]
        }
        raw = encoded(manifest)
        assets["redistribution/manifest.json"] = raw
        extra = set() if groups == ["ui"] else {"existing-core-and-wheel-notices"}
        return assets, {
            "path": "redistribution",
            "inventory_sha256": manifest["inventory_sha256"],
            "manifest_sha256": VALIDATOR.sha256(raw),
            "requirements": runtime_builder["complete_requirements"](
                manifest, groups, extra
            ),
        }

    example = encoded(
        {
            "name": "synthetic bounded fixture",
            "status": "test",
            "vertices": 0,
            "triangles": 0,
            "units": "millimeters",
            "files": {},
        }
    )
    example_path = "examples/nozzle-bayonette-simplified/manifest.json"
    source_zip = tmp_path / "source.zip"
    with zipfile.ZipFile(source_zip, "w") as archive:
        archive.writestr(example_path, example)
        archive.writestr(
            VALIDATOR.SEMANTIC_POLICY, (ROOT / VALIDATOR.SEMANTIC_POLICY).read_bytes()
        )
    runtime_files, runtime_redistribution = supplement(["numeric", "cad"])
    runtime_files.update(
        {
            "runtime/pyodide.mjs": numeric,
            "runtime/cad.whl": cad,
            "notices/core/LICENSE": core,
            "worker.mjs": b"synthetic worker",
            "integrity.mjs": b"synthetic helper",
            "scansor.zip": source_zip.read_bytes(),
        }
    )
    runtime_manifest = {
        "schema_version": 1,
        "worker_protocol": {
            "version": 4,
            "bootstrap": "initialize",
            "workspace": "retained-nozzle-numerical",
            "cad": "lazy-approved-ocp",
        },
        "cad": True,
        "versions": runtime_lock["versions"],
        "source_revision": "a" * 40,
        "source_worktree_dirty": False,
        "redistribution_ready": True,
        "redistribution_gaps": [],
        "redistribution": runtime_redistribution,
        "runtime_lock_sha256": inventory["bindings"]["runtime_lock_sha256"],
        "source_files": [
            {"archive_path": example_path, "sha256": VALIDATOR.sha256(example)},
            {
                "path": VALIDATOR.SEMANTIC_POLICY,
                "archive_path": VALIDATOR.SEMANTIC_POLICY,
                "sha256": VALIDATOR.sha256(
                    (ROOT / VALIDATOR.SEMANTIC_POLICY).read_bytes()
                ),
            },
        ],
        "assets": [
            record(
                name,
                data,
                group=next(
                    (
                        item["group"]
                        for item in [*runtime_lock["assets"], *runtime_lock["licenses"]]
                        if item["path"] == name
                    ),
                    "probe",
                ),
            )
            for name, data in runtime_files.items()
        ],
    }
    runtime_raw = encoded(runtime_manifest)
    runtime_hash = VALIDATOR.sha256(runtime_raw)
    prefix = f"app/runtime/{runtime_hash}"
    site_files, ui_redistribution = supplement(["ui"])
    site_files.update(
        {f"{prefix}/{name}": data for name, data in runtime_files.items()}
    )
    site_files[f"{prefix}/manifest.json"] = runtime_raw
    site_files["app/index.html"] = b"synthetic app; never executed"
    fixture_runtime = tmp_path / "fixture-runtime"
    fixture_runtime.mkdir()
    _ = (fixture_runtime / "scansor.zip").write_bytes(source_zip.read_bytes())
    site = {
        "schema_version": 1,
        "kind": "scansor-static-app",
        "entry": "app/index.html",
        "source_revision": "a" * 40,
        "source_worktree_dirty": False,
        "redistribution_ready": True,
        "runtime_redistribution_ready": True,
        "redistribution_gaps": [],
        "redistribution": ui_redistribution,
        "capabilities": {"runtime_bootstrap": True, "numerical": True, "cad": True},
        "backend": {
            "type": "worker",
            "runtimeURL": f"./runtime/{runtime_hash}/worker.mjs",
            "cad": True,
        },
        "runtime": {
            "path": prefix,
            "manifest_sha256": runtime_hash,
            "versions": runtime_lock["versions"],
            "source_revision": "a" * 40,
            "source_worktree_dirty": False,
            "cad_assets_present": True,
        },
        "build_inputs": [
            {
                "path": "experiments/browser_viewer/package-lock.json",
                "sha256": inventory["bindings"]["ui_package_lock_sha256"],
            }
        ],
        "bounded_examples": [
            app_builder["example_inventory"](fixture_runtime, runtime_manifest)
        ],
        "assets": [record(name, data) for name, data in site_files.items()],
    }
    site_raw = encoded(site)
    evidence = {
        "site-manifest.json": site_raw,
        "runtime-manifest.json": runtime_raw,
        "build-redistribution.json": encoded(assembled),
    }
    for name in VALIDATOR.DRIVERS:
        report = {
            "manifest_sha256": VALIDATOR.sha256(site_raw),
            "site_manifest_sha256": VALIDATOR.sha256(site_raw),
            "source_revision": "a" * 40,
            "source_worktree_dirty": False,
            "artifact_unchanged": True,
            "complete": True,
            "input_artifact_unchanged": True,
            "input_site_manifest_sha256": VALIDATOR.sha256(site_raw),
            "immutable_site_verified_after": True,
            "native_exit": {"code": 0, "signal": None},
            "native_close": {"code": 0, "signal": None},
        }
        if name in VALIDATOR.SEMANTIC_DRIVERS:
            report.update(semantic_report(name))
        evidence[f"{name}.json"] = encoded(report)
    when = "2026-01-01T00:00:00Z"
    steps: list[dict[str, Any]] = []
    for name in VALIDATOR.STEPS:
        step = {
            "name": name,
            "exit_code": 0,
            "elapsed_seconds": 0,
            "started_utc": when,
            "finished_utc": when,
        }
        filename = f"{name}.json"
        if filename in evidence:
            step["evidence"] = {
                "path": filename,
                **VALIDATOR.identity(evidence[filename]),
            }
        steps.append(step)
    evidence["verification.json"] = encoded(
        {
            "schema_version": 1,
            "kind": "scansor-static-ci-verification",
            "verified": True,
            "redistribution_ready": True,
            "redistribution_gaps": [],
            "steps": steps,
            "started_utc": when,
            "finished_utc": when,
            "site-manifest": VALIDATOR.identity(site_raw),
            "runtime-manifest": VALIDATOR.identity(runtime_raw),
            "redistribution-manifest": VALIDATOR.identity(
                evidence["build-redistribution.json"]
            ),
        }
    )
    members = {f"site/{name}": data for name, data in site_files.items()}
    members["site/manifest.json"] = site_raw
    members.update({f"evidence/{name}": data for name, data in evidence.items()})
    run, artifact = metadata()
    fixture = {
        "tmp": tmp_path,
        "locks": locks,
        "members": members,
        "run": run,
        "artifact": artifact,
    }
    refresh(fixture)
    return fixture


def refresh(fixture: dict[str, Any]) -> None:
    archive_path = fixture["tmp"] / "artifact.zip"
    with zipfile.ZipFile(
        archive_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for name, data in fixture["members"].items():
            archive.writestr(name, data)
    raw = archive_path.read_bytes()
    fixture["archive"] = archive_path
    fixture["artifact"].update(
        size_in_bytes=len(raw), digest="sha256:" + VALIDATOR.sha256(raw)
    )
    fixture["archive_sha256"] = VALIDATOR.sha256(raw)
    fixture["site_sha256"] = VALIDATOR.sha256(fixture["members"]["site/manifest.json"])
    for name in ("run", "artifact"):
        _ = (fixture["tmp"] / f"{name}.json").write_bytes(encoded(fixture[name]))


def validate(fixture: dict[str, Any], output: Path | None = None) -> dict[str, Any]:
    return VALIDATOR.validate(
        fixture["archive"],
        fixture["tmp"] / "run.json",
        fixture["tmp"] / "artifact.json",
        fixture["locks"],
        output or fixture["tmp"] / "public-site",
        repository="altendky/scansor",
        run_id=12,
        artifact_id=56,
        archive_sha256=fixture["archive_sha256"],
        site_manifest_sha256=fixture["site_sha256"],
        evidence_output=fixture["tmp"] / "selection.json",
    )


@pytest.mark.parametrize("name", VALIDATOR.SEMANTIC_DRIVERS)
@pytest.mark.parametrize("field", ["semantic_parity", "semantic_parity_evidence"])
def test_semantic_profiles_require_their_complete_evidence(name: str, field: str):
    report = semantic_report(name)
    VALIDATOR.validate_semantic_parity(
        report, name, VALIDATOR.trusted_semantic_policy()
    )
    del report[field]
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SEMANTIC_DRIVERS)
@pytest.mark.parametrize(
    "field", ["complete", "semantic_complete", "budgets_validated"]
)
@pytest.mark.parametrize("value", [None, False, 1])
def test_semantic_success_flags_are_required_exact_booleans(
    name: str, field: str, value: Any
):
    report = semantic_report(name)
    if value is None:
        del report["semantic_parity"][field]
    else:
        report["semantic_parity"][field] = value
    with pytest.raises(ValueError, match="is not true"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("policy_version", "self-declared-policy"),
        ("policy_sha256", "0" * 64),
        ("input_sha256", "0" * 64),
        ("strict_fields_compared", 0),
        ("budget_records_validated", 1),
        ("diagnostic_difference_count", 1),
        ("diagnostic_differences", []),
        ("unrecognized_approval", True),
    ],
)
def test_forged_semantic_receipt_is_rejected(field: str, value: Any):
    report = semantic_report("recovery")
    if field == "diagnostic_differences":
        value = [{"path": "fit.radius", "actual": 2, "expected": 1, "reason": "allow"}]
    report["semantic_parity"][field] = value
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, "recovery", VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("fault", ["text", "bytes", "hash", "missing-json"])
def test_full_semantic_raw_text_and_receipt_are_bound(fault: str):
    report = semantic_report("recovery-isolated")
    evidence = report["semantic_parity_evidence"]
    if fault == "text":
        evidence["json"] += " "
    elif fault == "bytes":
        evidence["bytes"] = True
    elif fault == "hash":
        evidence["sha256"] = "0" * 64
    else:
        del evidence["json"]
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, "recovery-isolated", VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("fault", ["missing", "order", "id", "profile", "empty"])
def test_semantic_case_coverage_cannot_be_substituted(fault: str):
    report = semantic_report("workspace")
    batch = json.loads(report["semantic_parity_evidence"]["json"])
    cases = batch["cases"]
    if fault == "missing":
        cases.pop()
    elif fault == "order":
        cases.reverse()
    elif fault == "id":
        cases[0]["id"] = "other-case"
    elif fault == "profile":
        cases[0]["profile"] = "result-map"
    else:
        cases[0]["actual"] = {}
    raw = encoded(batch)
    report["semantic_parity_evidence"] = {
        "json": raw.decode(),
        **VALIDATOR.identity(raw),
    }
    report["semantic_parity"]["input_sha256"] = VALIDATOR.sha256(raw)
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, "workspace", VALIDATOR.trusted_semantic_policy()
        )


def test_semantic_evidence_must_include_the_full_retained_snapshots():
    report = semantic_report("numerical-workspace")
    report["browser_snapshots"]["default"]["diagnostics"] = {"omitted": "trace"}
    with pytest.raises(ValueError, match="differs from full snapshots"):
        VALIDATOR.validate_semantic_parity(
            report, "numerical-workspace", VALIDATOR.trusted_semantic_policy()
        )


def test_trusted_recomparison_rejects_forged_geometry_with_updated_evidence_hashes():
    report = semantic_report("recovery")
    batch = json.loads(report["semantic_parity_evidence"]["json"])
    batch["cases"][0]["actual"]["fit"]["radius"] = 2.0
    raw = encoded(batch)
    report["semantic_parity_evidence"] = {
        "json": raw.decode(),
        **VALIDATOR.identity(raw),
    }
    report["semantic_parity"]["input_sha256"] = VALIDATOR.sha256(raw)
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, "recovery", VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("count", [513, -1, 1.5, True, "512"])
def test_equal_invalid_budget_counts_cannot_be_approved_by_forged_receipt(count: Any):
    binding = "3" * 64
    recovery = {
        "policy": {
            "version": "selected-data-recovery-v2",
            "max_proposals": 16,
            "max_fresh_attempts": 8,
            "attempt": {
                "max_iterations": 100,
                "max_trials": 16,
                "max_evaluations": 512,
            },
            "objective_atol": 1e-12,
            "objective_rtol": 1e-9,
        },
        "binding_sha256": binding,
        "attempts": [
            {
                "candidate": {
                    "id": "failed-start",
                    "parameters": [1.0],
                    "derivation": "synthetic budget guard",
                    "chart": "synthetic",
                    "binding_sha256": binding,
                },
                "role": "fresh",
                "outcome": "evaluation-limit",
                "evaluations": 512,
                "assessment": None,
                "solver": None,
            }
        ],
        "selected": None,
        "termination": "search-exhausted",
        "selected_role": None,
    }
    cases = [
        {
            "id": "crash-force",
            "profile": "result-map",
            "actual": {"fit": recovery},
            "expected": {"fit": recovery},
        }
    ]
    report = semantic_evidence(cases)
    assert report["semantic_parity"]["budget_records_validated"] > 0
    batch = json.loads(report["semantic_parity_evidence"]["json"])
    for side in ("actual", "expected"):
        batch["cases"][0][side]["fit"]["attempts"][0]["evaluations"] = count
    raw = encoded(batch)
    report["semantic_parity_evidence"] = {
        "json": raw.decode(),
        **VALIDATOR.identity(raw),
    }
    report["semantic_parity"]["input_sha256"] = VALIDATOR.sha256(raw)
    with pytest.raises(ValueError, match="trusted semantic comparison failed"):
        VALIDATOR.validate_semantic_parity(
            report, "recovery", VALIDATOR.trusted_semantic_policy()
        )


def test_numeric_success_inside_a_case_is_not_a_boolean_receipt():
    report = semantic_report("recovery")
    report["semantic_parity"]["cases"][0]["semantic_complete"] = 1
    with pytest.raises(ValueError, match="differs from trusted recomparison"):
        VALIDATOR.validate_semantic_parity(
            report, "recovery", VALIDATOR.trusted_semantic_policy()
        )


def test_semantic_receipt_cannot_certify_only_empty_containers():
    report = semantic_evidence(
        [
            {
                "id": "crash-force",
                "profile": "result-map",
                "actual": {"fit": {}},
                "expected": {"fit": {}},
            }
        ]
    )
    assert report["semantic_parity"]["strict_fields_compared"] == 0
    with pytest.raises(ValueError, match="invalid semantic strict_fields_compared"):
        VALIDATOR.validate_semantic_parity(
            report, "recovery", VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SEMANTIC_DRIVERS)
def test_archive_gate_requires_semantic_evidence_after_report_hashes_are_refreshed(
    selected: dict[str, Any], name: str
):
    members = selected["members"]
    filename = f"{name}.json"
    report = json.loads(members[f"evidence/{filename}"])
    del report["semantic_parity"]
    members[f"evidence/{filename}"] = encoded(report)
    verification = json.loads(members["evidence/verification.json"])
    step = next(step for step in verification["steps"] if step["name"] == name)
    step["evidence"] = {
        "path": filename,
        **VALIDATOR.identity(members[f"evidence/{filename}"]),
    }
    members["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize("fault", ["missing", "forged-hash", "wrong-path"])
def test_packaged_policy_identity_is_bound_to_the_trusted_checkout(
    selected: dict[str, Any], fault: str
):
    evidence = {
        name.removeprefix("evidence/"): raw
        for name, raw in selected["members"].items()
        if name.startswith("evidence/")
    }
    runtime = json.loads(evidence["runtime-manifest.json"])
    helper = runtime["source_files"][-1]
    if fault == "missing":
        runtime["source_files"].pop()
    elif fault == "forged-hash":
        helper["sha256"] = "0" * 64
    else:
        helper["path"] = "artifact-controlled-policy.py"
    runtime_raw = encoded(runtime)
    evidence["runtime-manifest.json"] = runtime_raw
    verification = json.loads(evidence["verification.json"])
    verification["runtime-manifest"] = VALIDATOR.identity(runtime_raw)
    evidence["verification.json"] = encoded(verification)
    with pytest.raises(ValueError, match="packaged semantic policy differs"):
        VALIDATOR.validate_evidence(
            evidence, evidence["site-manifest.json"], runtime_raw
        )


def test_only_site_files_are_emitted_after_full_semantic_validation(
    selected: dict[str, Any],
):
    result = validate(selected)
    output = selected["tmp"] / "public-site"
    assert result["verified"] is True
    assert result["verification_steps"] == list(VALIDATOR.STEPS)
    assert {
        p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()
    } == {
        name.removeprefix("site/")
        for name in selected["members"]
        if name.startswith("site/")
    }
    assert not (output / "evidence").exists()
    assert (selected["tmp"] / "selection.json").is_file()


def test_historical_selected_locks_remain_valid_after_current_locks_change(
    selected: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
):
    today = selected["tmp"] / "current-repository"
    (today / "scripts").mkdir(parents=True)
    for name in (
        "build-browser-runtime.py",
        "build-browser-app.py",
        "build-browser-redistribution.py",
    ):
        _ = shutil.copyfile(ROOT / "scripts" / name, today / "scripts" / name)
    helper = today / VALIDATOR.SEMANTIC_POLICY
    helper.parent.mkdir(parents=True)
    _ = shutil.copyfile(ROOT / VALIDATOR.SEMANTIC_POLICY, helper)
    for name in (
        "experiments/browser_runtime/runtime-lock.json",
        "scripts/browser-redistribution-lock.json",
        "experiments/browser_viewer/package-lock.json",
    ):
        path = today / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_text('{"changed":"today"}')
    monkeypatch.setattr(VALIDATOR, "ROOT", today)
    assert validate(selected)["verified"] is True


@pytest.mark.parametrize(
    "fault",
    [
        "unlisted",
        "missing",
        "asset",
        "evidence",
        "short-steps",
        "failed-step",
        "timeout",
        "unready",
        "dirty",
        "source",
    ],
)
def test_tampering_never_creates_publishable_output(
    selected: dict[str, Any], fault: str
):
    members = selected["members"]
    if fault == "unlisted":
        members["site/debug.txt"] = b"unmanifested"
    elif fault == "missing":
        del members["evidence/cad.json"]
    elif fault == "asset":
        members["site/app/index.html"] = b"changed"
    elif fault == "evidence":
        members["evidence/cad.json"] = b"{}"
    elif fault in ("short-steps", "failed-step", "timeout"):
        report = json.loads(members["evidence/verification.json"])
        if fault == "short-steps":
            report["steps"].pop()
        elif fault == "failed-step":
            report["steps"][0]["exit_code"] = 1
        else:
            report["steps"][0]["timeout"] = True
        members["evidence/verification.json"] = encoded(report)
    else:
        manifest = json.loads(members["site/manifest.json"])
        key, value = {
            "unready": ("redistribution_ready", False),
            "dirty": ("source_worktree_dirty", True),
            "source": ("source_revision", "c" * 40),
        }[fault]
        manifest[key] = value
        members["site/manifest.json"] = encoded(manifest)
    refresh(selected)
    with pytest.raises(ValueError):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()
    assert not (selected["tmp"] / "selection.json").exists()


@pytest.mark.parametrize("lock", VALIDATOR.LOCK_NAMES)
def test_selected_lock_bytes_are_binding_inputs(selected: dict[str, Any], lock: str):
    path = selected["locks"] / lock
    _ = path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "site/.hidden",
        "site/x/.git/config",
        "site/a\\b",
        "site/a/../b",
        "site//empty",
        "site/x?query",
    ],
)
def test_archive_path_rejection_precedes_extraction(tmp_path: Path, name: str):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name, b"payload")
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError, match="unsafe"):
        _ = VALIDATOR.zip_inventory(archive)


@pytest.mark.parametrize(
    "fault",
    [
        "duplicate",
        "symlink",
        "device",
        "collision",
        "unused-directory",
        "too-large",
        "too-many",
    ],
)
def test_archive_types_duplicates_and_bounds(
    tmp_path: Path, fault: str, monkeypatch: pytest.MonkeyPatch
):
    path = tmp_path / "bad.zip"
    info = zipfile.ZipInfo("site/file")
    if fault in ("symlink", "device"):
        info.external_attr = (
            (stat.S_IFLNK if fault == "symlink" else stat.S_IFCHR) | 0o644
        ) << 16
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(info, b"payload")
            if fault == "duplicate":
                archive.writestr("site/file", b"duplicate")
            elif fault == "collision":
                archive.writestr("site/file/child", b"collision")
            elif fault == "unused-directory":
                archive.writestr("unused/", b"")
    if fault == "too-large":
        monkeypatch.setattr(VALIDATOR, "MAX_MEMBER", 1)
    if fault == "too-many":
        monkeypatch.setattr(VALIDATOR, "MAX_FILES", 0)
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError):
        _ = VALIDATOR.zip_inventory(archive)


def test_expected_archive_and_site_hashes_are_independent_guards(
    selected: dict[str, Any],
):
    selected["archive_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="API and expected"):
        _ = validate(selected)
    selected["artifact"]["digest"] = "sha256:" + "c" * 64
    _ = (selected["tmp"] / "artifact.json").write_bytes(encoded(selected["artifact"]))
    with pytest.raises(ValueError, match="downloaded archive"):
        _ = validate(selected)
    refresh(selected)
    selected["site_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="expected site manifest"):
        _ = validate(selected)


def test_outputs_and_inputs_cannot_alias(selected: dict[str, Any]):
    existing = selected["tmp"] / "existing"
    existing.mkdir()
    _ = (existing / "keep").write_bytes(b"untouched")
    with pytest.raises(ValueError, match="fresh"):
        _ = validate(selected, existing)
    assert (existing / "keep").read_bytes() == b"untouched"
    with pytest.raises(ValueError, match="overlaps"):
        _ = validate(selected, selected["locks"] / "output")
    link = selected["tmp"] / "link"
    link.symlink_to(selected["locks"], target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        _ = validate(selected, link / "output")
    assert not (selected["locks"] / "output").exists()


def test_json_duplicate_keys_and_nonfinite_constants_are_rejected():
    for data in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"[]"):
        with pytest.raises(ValueError):
            _ = VALIDATOR.object_json(data)


def test_forged_ready_claim_cannot_override_derived_requirements(
    selected: dict[str, Any],
):
    members = copy.deepcopy(selected["members"])
    site = json.loads(members["site/manifest.json"])
    path = site["runtime"]["path"] + "/redistribution/manifest.json"
    supplement = json.loads(members["site/" + path])
    supplement["redistribution_ready"] = True  # Core fulfillment is external here.
    site["redistribution_ready"] = True
    extracted = selected["tmp"] / "semantic-site"
    extracted.mkdir()
    for name, data in members.items():
        if name.startswith("site/"):
            target = extracted / name.removeprefix("site/")
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = target.write_bytes(data)
    _ = (extracted / path).write_bytes(encoded(supplement))
    with pytest.raises(ValueError, match="derived evidence"):
        _ = VALIDATOR.validate_semantics(
            extracted,
            site,
            selected["locks"],
            json.loads(members["evidence/build-redistribution.json"]),
        )
