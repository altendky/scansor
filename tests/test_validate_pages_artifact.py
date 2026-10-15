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
    graph: dict[str, Any] = {
        "recipe": {"nodes": [{"id": "source", "operation": "source"}]},
        "token": "2" * 64,
        "states": {"source": "ready"},
        "errors": {},
        "diagnostics": {},
        "memberships": {},
        "results": {"source": {"vertices": 3}},
        "result": {"radius": 1.0},
        "derived": {},
        "input_catalogue": [],
        "input_requirements": {},
        "revision": 1,
    }
    profile, ids = VALIDATOR.SEMANTIC_DRIVERS[name]
    if name in VALIDATOR.SOURCE_GRAPH_DRIVERS:
        graph.update(
            project_generation=7,
            evaluation_running=False,
            required_failures=[],
        )
        snapshots = {
            "scene": copy.deepcopy(graph),
            "scene_replayed": copy.deepcopy(graph),
            "scene_actions": {**copy.deepcopy(graph), "project_generation": 11},
            "scene_actions_replayed": {
                **copy.deepcopy(graph),
                "project_generation": 12,
            },
            "native_scene": {**copy.deepcopy(graph), "project_generation": 0},
        }
        cases = [
            {
                "id": case_id,
                "profile": profile,
                "path": path,
                "actual": {
                    field: snapshots[key][field]
                    for field in VALIDATOR.SOURCE_GRAPH_PROJECTION
                },
                "expected": {
                    field: snapshots["native_scene"][field]
                    for field in VALIDATOR.SOURCE_GRAPH_PROJECTION
                },
            }
            for case_id, key, path in zip(
                ids,
                ("scene", "scene_replayed", "scene_actions", "scene_actions_replayed"),
                (
                    "scene.graph",
                    "scene.replayed_graph",
                    "actions.scene_opened",
                    "actions.scene_replayed",
                ),
                strict=True,
            )
        ]
        return {
            **semantic_evidence(cases),
            "source_graph_schema": VALIDATOR.SOURCE_GRAPH_SCHEMA,
            "source_graph_projection": list(VALIDATOR.SOURCE_GRAPH_PROJECTION),
            "source_graph_snapshots": snapshots,
            "ui_actions": {
                "complete": True,
                "cases": [
                    {
                        "name": name,
                        "complete": True,
                        **{field: True for field in fields},
                        **(
                            {"generation": 11}
                            if name == "scene-located-fresh-graph-real-evaluate"
                            else {"generation": 12}
                            if name == "matching-force-replayed"
                            else {}
                        ),
                    }
                    for name, fields in VALIDATOR.SOURCE_ACTION_CASES.items()
                ],
                "scene_opened": snapshots["scene_actions"],
                "scene_replayed": snapshots["scene_actions_replayed"],
                "scene_opened_generation": 11,
                "scene_replayed_generation": 12,
            },
            "cases": [
                {"name": "raw-nozzle", "generation": 1},
                {"name": "horizontal-scene", "generation": 7},
            ],
        }
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


def refresh_semantic_input(
    report: dict[str, Any], batch: dict[str, Any], *, recompute: bool = False
) -> None:
    if recompute:
        report.update(semantic_evidence(batch["cases"]))
        return
    raw = encoded(batch)
    report["semantic_parity_evidence"] = {
        "json": raw.decode(),
        **VALIDATOR.identity(raw),
    }
    report["semantic_parity"]["input_sha256"] = VALIDATOR.sha256(raw)


def source_batch(report: dict[str, Any]) -> dict[str, Any]:
    snapshots = report["source_graph_snapshots"]
    return {
        "cases": [
            {
                "id": case_id,
                "profile": "graph-snapshot",
                "path": path,
                "actual": {
                    key: snapshots[snapshot][key]
                    for key in VALIDATOR.SOURCE_GRAPH_PROJECTION
                },
                "expected": {
                    key: snapshots["native_scene"][key]
                    for key in VALIDATOR.SOURCE_GRAPH_PROJECTION
                },
            }
            for case_id, snapshot, path in (
                ("scene", "scene", "scene.graph"),
                ("scene-replayed", "scene_replayed", "scene.replayed_graph"),
                ("scene-actions", "scene_actions", "actions.scene_opened"),
                (
                    "scene-actions-replayed",
                    "scene_actions_replayed",
                    "actions.scene_replayed",
                ),
            )
        ]
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
    runtime_manifest: dict[str, Any] = {
        "schema_version": 1,
        "worker_protocol": VALIDATOR.runpy.run_path(
            str(ROOT / "scripts/build-browser-runtime.py")
        )["worker_protocol"](),
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
    site: dict[str, Any] = {
        "schema_version": 1,
        "kind": "scansor-static-app",
        "entry": "app/index.html",
        "source_revision": "a" * 40,
        "source_worktree_dirty": False,
        "redistribution_ready": True,
        "runtime_redistribution_ready": True,
        "redistribution_gaps": [],
        "redistribution": ui_redistribution,
        "capabilities": {
            "runtime_bootstrap": True,
            "numerical": True,
            "cad": True,
            "source_import": runtime_manifest["worker_protocol"]["source_import"],
            "source_actions": runtime_manifest["worker_protocol"]["source_actions"],
        },
        "backend": {
            "type": "worker",
            "runtimeURL": f"./runtime/{runtime_hash}/worker.mjs",
            "cad": True,
            "sourceActions": True,
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
    numerical_raw = encoded(
        site | {"capabilities": site["capabilities"] | {"cad": False}}
    )
    evidence = {
        "site-manifest.json": site_raw,
        "numerical-site-manifest.json": numerical_raw,
        "runtime-manifest.json": runtime_raw,
        "build-redistribution.json": encoded(assembled),
    }
    for name in VALIDATOR.DRIVERS:
        report = {
            "manifest_sha256": VALIDATOR.sha256(
                numerical_raw if name.startswith("numerical-") else site_raw
            ),
            "site_manifest_sha256": VALIDATOR.sha256(site_raw),
            "source_revision": "a" * 40,
            "source_worktree_dirty": False,
            "artifact_unchanged": True,
            "complete": True,
            "isolation": False,
            "isolation_requested": name.endswith("-isolated"),
            "isolation_actual": {
                "page": name.endswith("-isolated"),
                "worker": name.endswith("-isolated"),
            },
            "input_artifact_unchanged": True,
            "input_site_manifest_sha256": VALIDATOR.sha256(site_raw),
            "immutable_site_verified_after": True,
            "native_exit": {"code": 0, "signal": None},
            "native_close": {"code": 0, "signal": None},
            "cases": [
                {"name": case, "complete": True}
                for case in (
                    "raw-nozzle",
                    "horizontal-scene",
                    "grid-256x128",
                    "grid-512x256",
                )
            ]
            if name == "source-import-memory"
            else [{"name": "generated test evidence"}],
            "checks": {
                "source_import": True,
                "source_identity": True,
                "isolation_matches": True,
                "all_cases_complete": True,
                "capacity_nonzero": True,
                "chunk_reads_bounded": True,
                "terminal_staging_released": True,
                "no_external_computation": True,
                "no_page_errors": True,
                "artifact_unchanged": True,
                "cancel_before_adoption": True,
                "force_after_adoption": True,
                "cleanup_failure_recovery": True,
                **(
                    {
                        "ui_source_recovery": True,
                        "ui_legacy_startup": True,
                        "ui_source_actions": True,
                    }
                    if name != "source-import-memory"
                    else {}
                ),
                "recovery": True,
                "cleanup": True,
            },
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
            "numerical-site-manifest": VALIDATOR.identity(numerical_raw),
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


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
def test_source_semantics_bind_worker_generation_without_borrowing_native_epoch(
    name: str,
):
    report = semantic_report(name)
    original = copy.deepcopy(report)
    assert report["source_graph_snapshots"]["native_scene"]["project_generation"] == 0
    for key in ("scene", "scene_replayed"):
        report["source_graph_snapshots"][key]["evaluation_error"] = None
    VALIDATOR.validate_semantic_parity(
        report, name, VALIDATOR.trusted_semantic_policy()
    )
    for key in ("scene", "scene_replayed"):
        del report["source_graph_snapshots"][key]["evaluation_error"]
    assert report == original


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("field", VALIDATOR.SOURCE_GRAPH_PROJECTION)
def test_source_projection_cannot_delete_fields_from_every_operand(
    name: str, field: str
):
    report = semantic_report(name)
    batch = source_batch(report)
    for graph in report["source_graph_snapshots"].values():
        del graph[field]
    for case in batch["cases"]:
        del case["actual"][field]
        del case["expected"][field]
    refresh_semantic_input(report, batch, recompute=True)
    with pytest.raises(ValueError, match="unknown/missing projection fields"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene", "scene_replayed", "native_scene"])
def test_source_projection_cannot_omit_one_raw_operand_field(name: str, target: str):
    report = semantic_report(name)
    del report["source_graph_snapshots"][target]["diagnostics"]
    with pytest.raises(ValueError, match="unknown/missing projection fields"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene", "scene_replayed", "native_scene", "all"])
def test_source_projection_rejects_unknown_semantics_even_when_all_equal(
    name: str, target: str
):
    report = semantic_report(name)
    snapshots = report["source_graph_snapshots"]
    targets = snapshots.values() if target == "all" else [snapshots[target]]
    for graph in targets:
        graph["new_semantic_field"] = {"uncompared": 42}
    with pytest.raises(ValueError, match="unknown/missing projection fields"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene", "scene_replayed"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_generation", 0),
        ("project_generation", True),
        ("evaluation_running", 0),
        ("evaluation_running", True),
        ("required_failures", None),
        ("required_failures", ["fit"]),
        ("errors", {"fit": "failed"}),
        ("states", {"source": "running"}),
        ("evaluation_error", False),
        ("evaluation_error", "failed"),
    ],
)
def test_source_graph_requires_actual_accepted_terminal_publication(
    name: str, target: str, field: str, value: Any
):
    report = semantic_report(name)
    report["source_graph_snapshots"][target][field] = value
    refresh_semantic_input(report, source_batch(report))
    with pytest.raises(ValueError, match=r"accepted generation|terminal publication"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("generation", [None, True, -1, "7"])
def test_source_accepted_generation_is_an_independent_nonnegative_integer(
    name: str, generation: Any
):
    report = semantic_report(name)
    report["cases"][1]["generation"] = generation
    with pytest.raises(ValueError, match="accepted generation"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene", "scene_replayed", "native_scene"])
def test_source_raw_snapshots_cannot_disagree_with_the_compared_projection(
    name: str, target: str
):
    report = semantic_report(name)
    report["source_graph_snapshots"][target]["result"]["radius"] = 2.0
    with pytest.raises(ValueError, match="differs from full snapshots/projection"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
def test_source_generation_cannot_exceed_the_browser_safe_integer_range(name: str):
    report = semantic_report(name)
    report["cases"][1]["generation"] = 2**53
    for key in ("scene", "scene_replayed"):
        report["source_graph_snapshots"][key]["project_generation"] = 2**53
    with pytest.raises(ValueError, match="accepted generation"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize(
    "fault",
    ["schema", "projection", "snapshot", "id", "order", "profile", "path", "policy"],
)
def test_source_semantic_protocol_is_not_replaceable(name: str, fault: str):
    report = semantic_report(name)
    batch = source_batch(report)
    if fault == "schema":
        report["source_graph_schema"] = "unknown-v2"
    elif fault == "projection":
        report["source_graph_projection"].reverse()
    elif fault == "snapshot":
        del report["source_graph_snapshots"]["scene_replayed"]
    elif fault == "policy":
        report["semantic_parity"]["policy_sha256"] = "0" * 64
    else:
        if fault == "id":
            batch["cases"][0]["id"] = "other"
        elif fault == "order":
            batch["cases"].reverse()
        elif fault == "profile":
            batch["cases"][0]["profile"] = "result-map"
        else:
            batch["cases"][0]["path"] = "another.graph"
        refresh_semantic_input(report, batch, recompute=True)
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("fault", ["physical", "budget"])
def test_archive_source_semantics_are_recomputed_after_all_envelope_hashes_change(
    selected: dict[str, Any], name: str, fault: str
):
    from tests.test_semantic_parity import constrained

    members = selected["members"]
    filename = f"{name}.json"
    report = json.loads(members[f"evidence/{filename}"])
    snapshots = report["source_graph_snapshots"]
    if fault == "budget":
        for graph in snapshots.values():
            graph["result"] = constrained()
        report["ui_actions"]["scene_opened"] = copy.deepcopy(snapshots["scene_actions"])
        report["ui_actions"]["scene_replayed"] = copy.deepcopy(
            snapshots["scene_actions_replayed"]
        )
        refresh_semantic_input(report, source_batch(report), recompute=True)
        for graph in snapshots.values():
            graph["result"]["initialization"]["recovery"]["attempts"][0][
                "evaluations"
            ] = 513
    else:
        for key in ("scene", "scene_replayed"):
            snapshots[key]["result"]["radius"] = 2.0
    report["ui_actions"]["scene_opened"] = copy.deepcopy(snapshots["scene_actions"])
    report["ui_actions"]["scene_replayed"] = copy.deepcopy(
        snapshots["scene_actions_replayed"]
    )
    refresh_semantic_input(report, source_batch(report))
    members[f"evidence/{filename}"] = encoded(report)
    verification = json.loads(members["evidence/verification.json"])
    step = next(step for step in verification["steps"] if step["name"] == name)
    step["evidence"] = {
        "path": filename,
        **VALIDATOR.identity(members[f"evidence/{filename}"]),
    }
    members["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match="trusted semantic comparison failed"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene_opened", "scene_replayed"])
@pytest.mark.parametrize("generation", [None, True, -1, 2**53, "11"])
def test_actions_replay_requires_an_independent_safe_accepted_epoch(
    name: str, target: str, generation: Any
):
    report = semantic_report(name)
    report["ui_actions"][target + "_generation"] = generation
    report["ui_actions"][target]["project_generation"] = generation
    index = 2 if target == "scene_opened" else 4
    report["ui_actions"]["cases"][index]["generation"] = generation
    with pytest.raises(ValueError, match="accepted generation"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("target", ["scene_actions", "scene_actions_replayed"])
@pytest.mark.parametrize(
    "fault", ["missing", "unknown", "running", "failed", "geometry", "budget"]
)
def test_actions_replay_cannot_hide_result_or_acceptance_changes(
    name: str, target: str, fault: str
):
    from tests.test_semantic_parity import constrained

    report = semantic_report(name)
    if fault == "budget":
        for graph in report["source_graph_snapshots"].values():
            graph["result"] = constrained()
        refresh_semantic_input(report, source_batch(report), recompute=True)
    graph = report["source_graph_snapshots"][target]
    if fault == "missing":
        del graph["diagnostics"]
    elif fault == "unknown":
        graph["new_acceptance_classification"] = "rejected"
    elif fault == "running":
        graph["evaluation_running"] = True
    elif fault == "failed":
        graph["required_failures"] = ["fit"]
    elif fault == "geometry":
        graph["result"]["radius"] = 2.0
    else:
        graph["result"]["initialization"]["recovery"]["attempts"][0]["evaluations"] = (
            513
        )
    if fault not in ("missing", "unknown"):
        refresh_semantic_input(report, source_batch(report))
    with pytest.raises(ValueError):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
def test_actions_ui_observation_cannot_be_substituted_for_the_compared_snapshot(
    name: str,
):
    report = semantic_report(name)
    report["ui_actions"]["scene_opened"] = copy.deepcopy(
        report["ui_actions"]["scene_opened"]
    )
    report["ui_actions"]["scene_opened"]["results"]["source"]["vertices"] = 4
    with pytest.raises(ValueError, match="differs from full UI observations"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize("fault", ["empty", "missing", "duplicate", "order", "unknown"])
def test_source_actions_coverage_cannot_be_certified_by_a_complete_flag(
    name: str, fault: str
):
    report = semantic_report(name)
    cases = report["ui_actions"]["cases"]
    if fault == "empty":
        cases.clear()
    elif fault == "missing":
        cases.pop()
    elif fault == "duplicate":
        cases.append(copy.deepcopy(cases[0]))
    elif fault == "order":
        cases.reverse()
    else:
        cases[0]["name"] = "unreviewed-substitute"
    with pytest.raises(ValueError, match="case coverage"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
        )


@pytest.mark.parametrize("name", VALIDATOR.SOURCE_GRAPH_DRIVERS)
@pytest.mark.parametrize(
    ("case_name", "field"),
    [
        ("explicit-matching-legacy", "no_source_lookup"),
        ("raw-save-new-renamed-locate", "strict_source_rows"),
        ("scene-located-fresh-graph-real-evaluate", "fresh_dirty_graph"),
        ("matching-no-upload-real-evaluate", "no_source_upload"),
        ("matching-force-replayed", "derived_results_cleared"),
        ("popup-failure-preservation", "wrong_same_name_preserves_work"),
        ("docked-failure-preservation", "settings_rejected_before_prepare"),
        ("stale-inspection", "newer_actions_preserved"),
        ("cancel-before-adoption", "prior_source_actions_and_draft_preserved"),
        ("force-before-adoption", "old_file_and_live_docked_draft_preserved"),
        ("force-after-adoption-lost-ack", "warnings_recovered"),
        ("incompatible-legacy", "no_example_guessing_or_source_prepare"),
        ("empty-actions", "new_epoch_and_empty_arrays"),
    ],
)
@pytest.mark.parametrize("fault", ["missing", "false", "number"])
def test_source_actions_each_required_case_must_keep_its_oracle(
    name: str, case_name: str, field: str, fault: str
):
    report = semantic_report(name)
    case = next(
        item for item in report["ui_actions"]["cases"] if item["name"] == case_name
    )
    if fault == "missing":
        del case[field]
    else:
        case[field] = False if fault == "false" else 1
    with pytest.raises(ValueError, match="case failed or incomplete"):
        VALIDATOR.validate_semantic_parity(
            report, name, VALIDATOR.trusted_semantic_policy()
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


@pytest.mark.parametrize(
    "driver", [name for name in VALIDATOR.DRIVERS if "source-import" in name]
)
@pytest.mark.parametrize("fault", ["missing-complete", "empty-cases", "failed-cleanup"])
def test_source_import_evidence_requires_executed_complete_checks(
    selected: dict[str, Any], driver: str, fault: str
) -> None:
    name = f"evidence/{driver}.json"
    report = json.loads(selected["members"][name])
    if fault == "missing-complete":
        del report["complete"]
    elif fault == "empty-cases":
        report["cases"] = []
    else:
        report["checks"]["cleanup"] = False
    selected["members"][name] = encoded(report)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    next(step for step in verification["steps"] if step["name"] == driver)[
        "evidence"
    ] = {"path": driver + ".json", **VALIDATOR.identity(selected["members"][name])}
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match="source-import"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize(
    "driver",
    [
        name
        for name in VALIDATOR.DRIVERS
        if "source-import" in name and name != "source-import-memory"
    ],
)
@pytest.mark.parametrize("fault", ["missing", "failed", "not-boolean"])
@pytest.mark.parametrize(
    "check", ["ui_source_recovery", "ui_legacy_startup", "ui_source_actions"]
)
def test_source_evidence_requires_executed_ui_recovery(
    selected: dict[str, Any], driver: str, fault: str, check: str
) -> None:
    name = f"evidence/{driver}.json"
    report = json.loads(selected["members"][name])
    if fault == "missing":
        del report["checks"][check]
    else:
        report["checks"][check] = False if fault == "failed" else 1
    selected["members"][name] = encoded(report)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    next(step for step in verification["steps"] if step["name"] == driver)[
        "evidence"
    ] = {"path": driver + ".json", **VALIDATOR.identity(selected["members"][name])}
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match="source-import checks incomplete"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize(
    "driver",
    [
        name
        for name in VALIDATOR.DRIVERS
        if "source-import" in name and name != "source-import-memory"
    ],
)
@pytest.mark.parametrize(
    "fault",
    ["requested", "page", "worker", "missing-actual", "copied-plain", "unchecked"],
)
def test_source_evidence_attests_requested_page_and_worker_isolation(
    selected: dict[str, Any], driver: str, fault: str
) -> None:
    name = f"evidence/{driver}.json"
    report = json.loads(selected["members"][name])
    if fault == "requested":
        report["isolation_requested"] = not report["isolation_requested"]
    elif fault in ("page", "worker"):
        report["isolation_actual"][fault] = not report["isolation_actual"][fault]
    elif fault == "missing-actual":
        del report["isolation_actual"]
    elif fault == "unchecked":
        report["checks"]["isolation_matches"] = False
    else:
        # Copy a report from the opposite isolation profile, including its flags.
        report["isolation_requested"] = not driver.endswith("-isolated")
        report["isolation_actual"] = {
            "page": report["isolation_requested"],
            "worker": report["isolation_requested"],
        }
    selected["members"][name] = encoded(report)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    next(step for step in verification["steps"] if step["name"] == driver)[
        "evidence"
    ] = {"path": driver + ".json", **VALIDATOR.identity(selected["members"][name])}
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match=r"isolation|object"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "isolation",
        "missing-grid",
        "duplicate-grid",
        "incomplete-case",
        "source_import",
        "recovery",
        "cleanup",
        "all_cases_complete",
        "capacity_nonzero",
        "chunk_reads_bounded",
        "terminal_staging_released",
        "isolation_matches",
        "no_external_computation",
        "no_page_errors",
        "artifact_unchanged",
    ],
)
def test_source_memory_evidence_requires_complete_matrix_and_invariants(
    selected: dict[str, Any], fault: str
) -> None:
    name = "evidence/source-import-memory.json"
    report = json.loads(selected["members"][name])
    if fault == "isolation":
        report["isolation"] = True
    elif fault == "missing-grid":
        report["cases"].pop()
    elif fault == "duplicate-grid":
        report["cases"][-1]["name"] = "grid-256x128"
    elif fault == "incomplete-case":
        report["cases"][-1]["complete"] = False
    else:
        report["checks"][fault] = False
    selected["members"][name] = encoded(report)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    next(
        step for step in verification["steps"] if step["name"] == "source-import-memory"
    )["evidence"] = {
        "path": "source-import-memory.json",
        **VALIDATOR.identity(selected["members"][name]),
    }
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match="source-import"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


@pytest.mark.parametrize(
    "driver", [name for name in VALIDATOR.DRIVERS if name.startswith("numerical-")]
)
@pytest.mark.parametrize("fault", ["primary-site-hash", "source-revision"])
def test_numerical_evidence_is_bound_to_its_manifest_and_selected_source(
    selected: dict[str, Any], driver: str, fault: str
) -> None:
    name = f"evidence/{driver}.json"
    report = json.loads(selected["members"][name])
    if fault == "primary-site-hash":
        report["manifest_sha256"] = VALIDATOR.sha256(
            selected["members"]["evidence/site-manifest.json"]
        )
    else:
        report["source_revision"] = "b" * 40
    selected["members"][name] = encoded(report)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    next(step for step in verification["steps"] if step["name"] == driver)[
        "evidence"
    ] = {"path": driver + ".json", **VALIDATOR.identity(selected["members"][name])}
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match=r"tested another site|source mismatch"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


def test_numerical_manifest_cannot_substitute_another_source(
    selected: dict[str, Any],
) -> None:
    name = "evidence/numerical-site-manifest.json"
    manifest = json.loads(selected["members"][name])
    manifest["source_revision"] = "b" * 40
    selected["members"][name] = encoded(manifest)
    verification = json.loads(selected["members"]["evidence/verification.json"])
    verification["numerical-site-manifest"] = VALIDATOR.identity(
        selected["members"][name]
    )
    selected["members"]["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(ValueError, match="numerical site source mismatch"):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()


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


@pytest.mark.parametrize(
    "fault",
    ["supported-number", "version-float", "backend-number", "missing", "unknown"],
)
def test_actions_capability_types_remain_bound_after_all_envelope_hashes_are_refreshed(
    selected: dict[str, Any], fault: str
):
    members = selected["members"]
    raw = members["site/manifest.json"]
    old_hash = VALIDATOR.sha256(raw)
    site = json.loads(raw)
    if fault == "supported-number":
        site["capabilities"]["source_actions"]["supported"] = 1
    elif fault == "version-float":
        site["capabilities"]["source_actions"]["version"] = 1.0
    elif fault == "backend-number":
        site["backend"]["sourceActions"] = 1
    elif fault == "missing":
        del site["capabilities"]["source_actions"]
    else:
        site["capabilities"]["source_actions"]["unrecognized"] = True
    raw = encoded(site)
    new_hash = VALIDATOR.sha256(raw)
    members["site/manifest.json"] = raw
    members["evidence/site-manifest.json"] = raw
    for name in VALIDATOR.DRIVERS:
        path = f"evidence/{name}.json"
        report = json.loads(members[path])
        for key in (
            "manifest_sha256",
            "site_manifest_sha256",
            "input_site_manifest_sha256",
        ):
            if report.get(key) == old_hash:
                report[key] = new_hash
        members[path] = encoded(report)
    verification = json.loads(members["evidence/verification.json"])
    verification["site-manifest"] = VALIDATOR.identity(raw)
    for step in verification["steps"]:
        if "evidence" in step:
            path = step["evidence"]["path"]
            step["evidence"] = {
                "path": path,
                **VALIDATOR.identity(members["evidence/" + path]),
            }
    members["evidence/verification.json"] = encoded(verification)
    refresh(selected)
    with pytest.raises(
        ValueError, match="source-actions capability|backend/capabilities"
    ):
        _ = validate(selected)
    assert not (selected["tmp"] / "public-site").exists()
