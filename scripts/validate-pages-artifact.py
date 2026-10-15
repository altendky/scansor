#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Select a verified push-main CI artifact without executing downloaded code.

The caller fetches metadata and three raw source locks at run.head_sha through
the GitHub API. The expected archive and site identities are explicit inputs;
there is no latest-artifact fallback. Only the validated site is emitted.
"""

import argparse
import hashlib
import json
import math
import re
import runpy
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, cast

ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_TOTAL = 768 * 1024 * 1024
MAX_MEMBER = 256 * 1024 * 1024
MAX_JSON = 32 * 1024 * 1024
MAX_FILES = 4096
LOCK_NAMES = (
    "runtime-lock.json",
    "browser-redistribution-lock.json",
    "package-lock.json",
)
STEPS = (
    "build-redistribution",
    "build-runtime",
    "build-site",
    "verify-runtime",
    "verify-site",
    "startup",
    "startup-isolated",
    "workspace",
    "cad",
    "recovery",
    "recovery-isolated",
    "source-import",
    "source-import-isolated",
    "build-numerical-runtime",
    "build-numerical-site",
    "numerical-workspace",
    "numerical-source-import",
    "numerical-source-import-isolated",
    "timing",
    "memory",
    "fitting",
    "generated",
    "source-import-memory",
    "verify-site-after",
    "verify-numerical-site-after",
)
DRIVERS = (
    "startup",
    "startup-isolated",
    "workspace",
    "cad",
    "recovery",
    "recovery-isolated",
    "source-import",
    "source-import-isolated",
    "numerical-workspace",
    "numerical-source-import",
    "numerical-source-import-isolated",
    "timing",
    "memory",
    "fitting",
    "generated",
    "source-import-memory",
)
EVIDENCE = {
    *(f"{name}.json" for name in DRIVERS),
    "verification.json",
    "build-redistribution.json",
    "site-manifest.json",
    "runtime-manifest.json",
    "numerical-site-manifest.json",
}
SEMANTIC_POLICY = "experiments/browser_runtime/semantic_parity.py"
SOURCE_GRAPH_DRIVERS = (
    "source-import",
    "source-import-isolated",
    "numerical-source-import",
    "numerical-source-import-isolated",
)
SOURCE_GRAPH_SCHEMA = "scansor-source-graph-semantic-v2"
SOURCE_GRAPH_PROJECTION = (
    "recipe",
    "token",
    "states",
    "errors",
    "diagnostics",
    "memberships",
    "results",
    "result",
    "derived",
    "input_catalogue",
    "input_requirements",
)
SOURCE_GRAPH_TRANSPORT = {
    "revision",
    "project_generation",
    "required_failures",
    "evaluation_running",
    "evaluation_error",
}
SOURCE_ACTION_CASES = {
    "explicit-matching-legacy": ("no_source_lookup", "recipe_preserved"),
    "raw-save-new-renamed-locate": ("source_only", "strict_source_rows"),
    "scene-located-fresh-graph-real-evaluate": (
        "strict_source_rows",
        "fresh_dirty_graph",
        "real_ui_evaluation",
        "independent_geometry_objective_and_support",
    ),
    "matching-no-upload-real-evaluate": (
        "no_source_upload",
        "new_epoch_and_dirty_graph",
        "real_ui_evaluation",
        "independent_geometry_objective_and_support",
    ),
    "matching-force-replayed": (
        "original_file_recovered",
        "strict_source_rows",
        "accepted_epoch_preserved",
        "derived_results_cleared",
        "real_ui_evaluation",
        "independent_geometry_objective_and_support",
    ),
    "popup-failure-preservation": (
        "picker_cancel",
        "locator_cancel",
        "wrong_same_name_preserves_work",
        "settings_rejected_before_prepare",
    ),
    "docked-failure-preservation": (
        "picker_cancel",
        "locator_cancel",
        "wrong_same_name_preserves_work",
        "settings_rejected_before_prepare",
    ),
    "stale-inspection": ("newer_actions_preserved", "no_source_prepare"),
    "cancel-before-adoption": ("prior_source_actions_and_draft_preserved",),
    "force-before-adoption": ("old_file_and_live_docked_draft_preserved",),
    "force-after-adoption-lost-ack": (
        "accepted_file_retained",
        "prior_actions_downloaded",
        "draft_preserved_until_explicit_retry",
        "warnings_recovered",
    ),
    "incompatible-legacy": ("no_example_guessing_or_source_prepare",),
    "empty-actions": ("new_epoch_and_empty_arrays",),
}
SEMANTIC_DRIVERS = {
    "workspace": ("graph-snapshot", ("replay", "default", "edited", "replayed")),
    "numerical-workspace": (
        "graph-snapshot",
        ("replay", "default", "edited", "replayed"),
    ),
    "recovery": ("result-map", ("crash-force",)),
    "recovery-isolated": ("result-map", ("crash-force",)),
    **{
        name: (
            "graph-snapshot",
            ("scene", "scene-replayed", "scene-actions", "scene-actions-replayed"),
        )
        for name in SOURCE_GRAPH_DRIVERS
    },
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "sha256": sha256(data)}


def object_value(value: Any) -> dict[str, Any]:
    require(isinstance(value, dict), "expected object")
    return cast(dict[str, Any], value)


def array_value(value: Any) -> list[Any]:
    require(isinstance(value, list), "expected array")
    return cast(list[Any], value)


def object_json(data: bytes) -> dict[str, Any]:
    require(len(data) <= MAX_JSON, "JSON input exceeds size limit")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value: str) -> Any:
        raise ValueError(f"non-finite JSON constant: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        require(math.isfinite(number), "non-finite JSON number")
        return number

    value = json.loads(
        data, object_pairs_hook=pairs, parse_constant=invalid, parse_float=finite_float
    )
    return object_value(value)


def regular_bytes(path: Path, limit: int = MAX_JSON) -> bytes:
    require(
        not any(p.is_symlink() for p in (path, *path.parents)) and path.is_file(),
        f"expected regular input: {path}",
    )
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, f"input exceeds size limit: {path}")
    return data


def canonical(value: Any) -> str:
    require(isinstance(value, str) and bool(value), "invalid archive/asset path")
    require(
        not any(c in value for c in "\\?#:\0")
        and all(part and not part.startswith(".") for part in value.split("/")),
        f"unsafe archive/asset path: {value}",
    )
    return value


def digest_value(value: Any) -> str:
    require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        "expected complete lowercase SHA-256",
    )
    return value


def positive_integer(value: Any) -> bool:
    return type(value) is int and value > 0


def timestamp(value: Any) -> datetime:
    require(isinstance(value, str), "expected timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "timestamp must have timezone")
    return result.astimezone(UTC)


def validate_metadata(
    run: dict[str, Any],
    artifact: dict[str, Any],
    *,
    repository: str,
    run_id: int,
    artifact_id: int,
    archive_sha256: str,
    archive_size: int,
    now: datetime | None = None,
) -> str:
    _ = digest_value(archive_sha256)
    require(
        positive_integer(run_id) and positive_integer(artifact_id),
        "explicit positive IDs required",
    )
    require(
        positive_integer(run.get("id"))
        and positive_integer(artifact.get("id"))
        and run.get("id") == run_id
        and artifact.get("id") == artifact_id,
        "run/artifact ID mismatch",
    )
    require(
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("path") == ".github/workflows/ci.yml",
        "requires successful completed push-main CI workflow run",
    )
    repo, head = (
        object_value(run.get("repository")),
        object_value(run.get("head_repository")),
    )
    require(
        repo.get("full_name") == repository
        and head.get("full_name") == repository
        and positive_integer(repo.get("id"))
        and positive_integer(head.get("id"))
        and head.get("id") == repo["id"],
        "run repository mismatch",
    )
    revision = run.get("head_sha")
    require(
        isinstance(revision, str)
        and re.fullmatch(r"[0-9a-f]{40}", revision) is not None,
        "run lacks full source revision",
    )
    linkage = object_value(artifact.get("workflow_run"))
    require(
        all(
            positive_integer(linkage.get(key))
            for key in ("id", "repository_id", "head_repository_id")
        )
        and all(
            linkage.get(key) == expected
            for key, expected in (
                ("id", run_id),
                ("repository_id", repo["id"]),
                ("head_repository_id", repo["id"]),
                ("head_branch", "main"),
                ("head_sha", revision),
            )
        ),
        "artifact run linkage mismatch",
    )
    endpoint = (
        f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}"
    )
    require(
        artifact.get("url") == endpoint
        and artifact.get("archive_download_url") == endpoint + "/zip",
        "artifact API location mismatch",
    )
    require(
        artifact.get("name") == "validated-static-browser", "unexpected artifact name"
    )
    require(
        artifact.get("expired") is False
        and timestamp(artifact.get("expires_at")) > (now or datetime.now(UTC)),
        "artifact expired or expiry unspecified",
    )
    require(
        type(artifact.get("size_in_bytes")) is int
        and artifact["size_in_bytes"] == archive_size
        and 0 < archive_size <= MAX_ARCHIVE,
        "artifact archive size mismatch or limit exceeded",
    )
    require(
        artifact.get("digest") == f"sha256:{archive_sha256}",
        "API and expected archive SHA-256 differ",
    )
    # A previous attempt's artifact cannot authorize a later successful rerun.
    require(
        timestamp(artifact.get("created_at")) >= timestamp(run.get("run_started_at")),
        "artifact predates selected run attempt",
    )
    return cast(str, revision)


def zip_inventory(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    require(len(entries) <= MAX_FILES, "archive entry count exceeds limit")
    files: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    directories: set[str] = set()
    total = 0
    for info in entries:
        name = canonical(info.filename[:-1] if info.is_dir() else info.filename)
        require(name not in seen, f"duplicate archive path: {name}")
        seen.add(name)
        mode = stat.S_IFMT(info.external_attr >> 16)
        require(
            mode in (0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG),
            f"nonregular archive member: {name}",
        )
        require(
            not info.flag_bits & 1
            and info.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
            "encrypted or unsupported archive compression",
        )
        require(
            info.file_size <= MAX_MEMBER and info.file_size >= 0,
            "archive member size exceeds limit",
        )
        total += info.file_size
        require(total <= MAX_TOTAL, "archive expanded size exceeds limit")
        if info.is_dir():
            require(info.file_size == 0, "nonempty archive directory")
            directories.add(name)
        else:
            files[name] = info
    parents = {
        p.as_posix() for name in files for p in Path(name).parents if p != Path(".")
    }
    require(directories.issubset(parents), "unneeded archive directory")
    require(not set(files) & parents, "archive file/directory collision")
    return files


def read_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int = MAX_JSON
) -> bytes:
    require(info.file_size <= limit, "archive JSON exceeds limit")
    with archive.open(info) as stream:
        data = stream.read(limit + 1)
    require(
        len(data) == info.file_size and len(data) <= limit,
        "archive member length mismatch",
    )
    return data


def records(value: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in array_value(value):
        record = object_value(item)
        name = canonical(record.get("path"))
        require(
            name not in result and name != "manifest.json",
            "duplicate/reserved asset path",
        )
        require(
            type(record.get("bytes")) is int and 0 <= record["bytes"] <= MAX_MEMBER,
            "invalid asset byte count",
        )
        _ = digest_value(record.get("sha256"))
        result[name] = record
    return result


def exact_json(value: Any) -> str:
    """Compare JSON types too: a numeric 1 must never stand in for true."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def trusted_semantic_policy() -> dict[str, Any]:
    # Execute only the reviewed checkout helper, never a downloaded archive member.
    path = ROOT / SEMANTIC_POLICY
    expected_hash = sha256(regular_bytes(path))
    policy = runpy.run_path(str(path))
    require(policy["policy_sha256"]() == expected_hash, "trusted policy changed")
    return policy


def validate_source_graph_evidence(
    item: dict[str, Any], cases: list[dict[str, Any]]
) -> None:
    require(
        item.get("source_graph_schema") == SOURCE_GRAPH_SCHEMA
        and item.get("source_graph_projection") == list(SOURCE_GRAPH_PROJECTION),
        "unsupported source graph evidence schema/projection",
    )
    snapshots = object_value(item.get("source_graph_snapshots"))
    require(
        set(snapshots)
        == {
            "scene",
            "scene_replayed",
            "scene_actions",
            "scene_actions_replayed",
            "native_scene",
        },
        "source graph snapshots incomplete",
    )
    projected: dict[str, dict[str, Any]] = {}
    for key, value in snapshots.items():
        graph = object_value(value)
        require(
            set(SOURCE_GRAPH_PROJECTION)
            <= set(graph)
            <= set(SOURCE_GRAPH_PROJECTION) | SOURCE_GRAPH_TRANSPORT,
            "source graph snapshot has unknown/missing projection fields",
        )
        projected[key] = {field: graph[field] for field in SOURCE_GRAPH_PROJECTION}
    scene_cases = [
        object_value(value)
        for value in array_value(item.get("cases"))
        if object_value(value).get("name") == "horizontal-scene"
    ]
    require(
        len(scene_cases) == 1, "source graph accepted scene case missing/duplicated"
    )
    generation = scene_cases[0].get("generation")
    require(
        type(generation) is int and 0 <= generation <= 2**53 - 1,
        "invalid source graph accepted generation",
    )
    actions = object_value(item.get("ui_actions"))
    require(actions.get("complete") is True, "source actions evidence incomplete")
    action_cases = [object_value(value) for value in array_value(actions.get("cases"))]
    require(
        [case.get("name") for case in action_cases] == list(SOURCE_ACTION_CASES),
        "source actions case coverage incomplete or substituted",
    )
    for case in action_cases:
        require(
            case.get("complete") is True
            and all(
                case.get(field) is True for field in SOURCE_ACTION_CASES[case["name"]]
            ),
            "source actions case failed or incomplete",
        )
    require(
        exact_json(action_cases[2].get("generation"))
        == exact_json(actions.get("scene_opened_generation"))
        and exact_json(action_cases[4].get("generation"))
        == exact_json(actions.get("scene_replayed_generation")),
        "source actions case does not match accepted generation",
    )
    generations = {
        "scene": generation,
        "scene_replayed": generation,
        "scene_actions": actions.get("scene_opened_generation"),
        "scene_actions_replayed": actions.get("scene_replayed_generation"),
    }
    for key, generation in generations.items():
        require(
            type(generation) is int and 0 <= generation <= 2**53 - 1,
            "invalid source graph accepted generation",
        )
        graph = object_value(snapshots[key])
        if key in ("scene_actions", "scene_actions_replayed"):
            observed = "scene_opened" if key == "scene_actions" else "scene_replayed"
            require(
                exact_json(graph) == exact_json(actions.get(observed)),
                "source actions snapshot differs from full UI observations",
            )
        states = object_value(graph.get("states"))
        require(
            type(graph.get("project_generation")) is int
            and graph["project_generation"] == generation,
            "source graph does not match accepted generation",
        )
        require(
            graph.get("evaluation_running") is False
            and graph.get("required_failures") == []
            and object_value(graph.get("errors")) == {}
            and bool(states)
            and all(state == "ready" for state in states.values())
            and graph.get("evaluation_error") is None,
            "source graph is not a successful terminal publication",
        )
    for case, key, path in zip(
        cases,
        ("scene", "scene_replayed", "scene_actions", "scene_actions_replayed"),
        (
            "scene.graph",
            "scene.replayed_graph",
            "actions.scene_opened",
            "actions.scene_replayed",
        ),
        strict=True,
    ):
        require(
            case.get("path") == path
            and exact_json(case["actual"]) == exact_json(projected[key])
            and exact_json(case["expected"]) == exact_json(projected["native_scene"]),
            "source semantic evidence differs from full snapshots/projection",
        )


def validate_semantic_parity(
    item: dict[str, Any], name: str, policy: dict[str, Any]
) -> None:
    receipt = object_value(item.get("semantic_parity"))
    for field in ("complete", "semantic_complete", "budgets_validated"):
        require(receipt.get(field) is True, f"{name} semantic {field} is not true")
    for record in [
        receipt,
        *(object_value(value) for value in array_value(receipt.get("cases"))),
    ]:
        for field, minimum in (
            ("strict_fields_compared", 1),
            ("budget_records_validated", 0),
            ("diagnostic_difference_count", 0),
        ):
            require(
                type(record.get(field)) is int and record[field] >= minimum,
                f"{name} invalid semantic {field} count",
            )
    require(
        receipt.get("policy_version") == policy["POLICY_VERSION"]
        and receipt.get("policy_sha256") == policy["policy_sha256"](),
        f"{name} semantic policy differs from trusted checkout",
    )
    evidence = object_value(item.get("semantic_parity_evidence"))
    require(set(evidence) == {"json", "bytes", "sha256"}, "invalid semantic evidence")
    text = evidence.get("json")
    require(isinstance(text, str), "missing full semantic evidence JSON")
    raw = cast(str, text).encode("utf-8")
    require(
        type(evidence.get("bytes")) is int
        and evidence["bytes"] == len(raw)
        and evidence.get("sha256") == sha256(raw)
        and receipt.get("input_sha256") == sha256(raw),
        f"{name} semantic evidence identity mismatch",
    )
    batch = object_json(raw)
    require(set(batch) == {"cases"}, "unexpected semantic batch fields")
    cases = [object_value(case) for case in array_value(batch.get("cases"))]
    profile, ids = SEMANTIC_DRIVERS[name]
    require(
        [case.get("id") for case in cases] == list(ids),
        f"{name} semantic comparison cases differ",
    )
    for case in cases:
        require(
            {"id", "profile", "actual", "expected"} <= set(case)
            and set(case) <= {"id", "profile", "actual", "expected", "path"}
            and case["profile"] == profile,
            f"{name} invalid semantic comparison profile",
        )
        require(
            bool(object_value(case["actual"])) and bool(object_value(case["expected"])),
            f"{name} missing full semantic comparison records",
        )
    if name in SOURCE_GRAPH_DRIVERS:
        try:
            validate_source_graph_evidence(item, cases)
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from error
    elif profile == "graph-snapshot":
        browser = object_value(item.get("browser_snapshots"))
        native = object_value(item.get("native_snapshots"))
        pairs = (
            (browser.get("replayed"), browser.get("edited")),
            (browser.get("default"), native.get("default")),
            (browser.get("edited"), native.get("edited")),
            (browser.get("replayed"), native.get("edited")),
        )
        for case, (actual, expected) in zip(cases, pairs, strict=True):
            require(
                exact_json(case["actual"]) == exact_json(actual)
                and exact_json(case["expected"]) == exact_json(expected),
                f"{name} semantic evidence differs from full snapshots",
            )
    # Raw JSON is data. Recompute using the trusted, stdlib-only policy rather
    # than trusting success flags, accounting or a self-declared diagnostic ledger.
    try:
        recomputed = policy["compare_batch"](cases)
    except policy["SemanticParityError"] as error:
        raise ValueError(
            f"{name} trusted semantic comparison failed: {error}"
        ) from error
    submitted = {key: value for key, value in receipt.items() if key != "input_sha256"}
    require(
        exact_json(submitted) == exact_json(recomputed),
        f"{name} semantic receipt differs from trusted recomparison",
    )


def validate_evidence(
    evidence: dict[str, bytes], site_raw: bytes, runtime_raw: bytes
) -> None:
    require(set(evidence) == EVIDENCE, "unexpected or missing evidence files")
    report = object_json(evidence["verification.json"])
    require(
        report.get("schema_version") == 1
        and report.get("kind") == "scansor-static-ci-verification"
        and report.get("verified") is True
        and report.get("redistribution_ready") is True
        and report.get("redistribution_gaps") == []
        and "failure" not in report,
        "CI verification report is not complete and ready",
    )
    steps = [object_value(value) for value in array_value(report.get("steps"))]
    require(
        [s.get("name") for s in steps] == list(STEPS),
        "requires exactly all ordered CI steps",
    )
    require(
        evidence["site-manifest.json"] == site_raw
        and evidence["runtime-manifest.json"] == runtime_raw,
        "evidence manifest differs from packaged manifest",
    )
    for key, filename in (
        ("site-manifest", "site-manifest.json"),
        ("runtime-manifest", "runtime-manifest.json"),
        ("numerical-site-manifest", "numerical-site-manifest.json"),
        ("redistribution-manifest", "build-redistribution.json"),
    ):
        require(
            report.get(key) == identity(evidence[filename]),
            f"CI metadata identity mismatch: {key}",
        )
    previous = timestamp(report.get("started_utc"))
    for step in steps:
        require(
            type(step.get("exit_code")) is int
            and step["exit_code"] == 0
            and not step.get("timeout")
            and "failure" not in step,
            f"CI step failed: {step['name']}",
        )
        elapsed = step.get("elapsed_seconds")
        require(type(elapsed) in (float, int), "invalid CI elapsed time")
        elapsed = cast(float, elapsed)
        require(
            math.isfinite(elapsed) and elapsed >= 0,
            "invalid CI elapsed time",
        )
        started, finished = (
            timestamp(step.get("started_utc")),
            timestamp(step.get("finished_utc")),
        )
        require(previous <= started <= finished, "CI step timestamps are not ordered")
        previous = finished
        if step["name"] in (*DRIVERS, "build-redistribution"):
            filename = f"{step['name']}.json"
            require(
                step.get("evidence")
                == {"path": filename, **identity(evidence[filename])},
                f"CI step evidence mismatch: {filename}",
            )
        else:
            require("evidence" not in step, "unexpected CI step evidence")
    require(
        previous <= timestamp(report.get("finished_utc")),
        "CI verification finished before steps",
    )
    site = object_json(site_raw)
    numerical_raw = evidence["numerical-site-manifest.json"]
    numerical = object_json(numerical_raw)
    require(
        numerical.get("kind") == "scansor-static-app"
        and numerical.get("source_revision") == site["source_revision"]
        and numerical.get("source_worktree_dirty") == site.get("source_worktree_dirty"),
        "numerical site source mismatch",
    )
    policy = trusted_semantic_policy()
    runtime = object_json(runtime_raw)
    helper_records = [
        object_value(value)
        for value in array_value(runtime.get("source_files"))
        if object_value(value).get("archive_path") == SEMANTIC_POLICY
    ]
    require(
        helper_records
        == [
            {
                "path": SEMANTIC_POLICY,
                "archive_path": SEMANTIC_POLICY,
                "sha256": policy["policy_sha256"](),
            }
        ],
        "packaged semantic policy differs from trusted checkout",
    )
    for name in DRIVERS:
        item = object_json(evidence[f"{name}.json"])
        require(
            "failure" not in item and "error" not in item, f"driver failure: {name}"
        )
        for key in (
            "errors",
            "cleanup_errors",
            "unexpected_requests",
            "external_requests",
            "api_requests",
            "page_errors",
        ):
            require(not item.get(key), f"driver {name} has {key}")
        if name in SEMANTIC_DRIVERS:
            validate_semantic_parity(item, name, policy)
            require(
                item.get("source_revision") == site["source_revision"],
                f"driver {name} source mismatch",
            )
            if name in ("workspace", "numerical-workspace"):
                require(
                    item.get("source_worktree_dirty") is False,
                    f"driver {name} tested dirty source",
                )
        if name == "generated":
            require(
                item.get("complete") is True
                and item.get("input_artifact_unchanged") is True
                and item.get("input_site_manifest_sha256") == sha256(site_raw),
                "generated evidence incomplete",
            )
        elif name == "fitting":
            require(
                item.get("immutable_site_verified_after") is True
                and item.get("native_exit") == {"code": 0, "signal": None}
                and item.get("native_close") == {"code": 0, "signal": None},
                "fitting evidence incomplete",
            )
        else:
            require(
                item.get("artifact_unchanged") is True,
                f"driver {name} changed artifact",
            )
        if "source-import" in name:
            require(not item.get("page_errors"), f"source-import page errors: {name}")
            require(
                not item.get("native_parity_errors"),
                f"source-import parity errors: {name}",
            )
            require(
                item.get("complete") is True and bool(item.get("cases")),
                f"source-import evidence incomplete: {name}",
            )
            checks = object_value(item.get("checks"))
            if name == "source-import-memory":
                require(
                    item.get("isolation") is False,
                    "source-import memory isolation mismatch",
                )
                cases = [
                    object_value(value) for value in array_value(item.get("cases"))
                ]
                require(
                    [case.get("name") for case in cases]
                    == [
                        "raw-nozzle",
                        "horizontal-scene",
                        "grid-256x128",
                        "grid-512x256",
                    ]
                    and all(case.get("complete") is True for case in cases),
                    "source-import memory cases incomplete",
                )
            else:
                requested = name.endswith("-isolated")
                actual = object_value(item.get("isolation_actual"))
                require(
                    item.get("isolation_requested") is requested
                    and actual.get("page") is requested
                    and actual.get("worker") is requested,
                    f"source-import isolation mismatch: {name}",
                )
            require(
                checks.get("isolation_matches") is True,
                f"source-import isolation check missing: {name}",
            )
            required_checks = (
                {
                    "source_import",
                    "source_identity",
                    "recovery",
                    "cleanup",
                    "cancel_before_adoption",
                    "force_after_adoption",
                    "cleanup_failure_recovery",
                    "ui_source_recovery",
                    "ui_legacy_startup",
                    "ui_source_actions",
                }
                if name != "source-import-memory"
                else {
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
                }
            )
            require(
                all(checks.get(key) is True for key in required_checks),
                f"source-import checks incomplete: {name}",
            )
        if name != "generated":
            field = (
                "site_manifest_sha256"
                if name in ("timing", "memory", "fitting")
                else "manifest_sha256"
            )
            require(
                item.get(field)
                == sha256(numerical_raw if name.startswith("numerical-") else site_raw),
                f"driver {name} tested another site",
            )
            require(
                item.get("source_revision") == site["source_revision"],
                f"driver {name} source mismatch",
            )


def validate_semantics(
    site: Path,
    manifest: dict[str, Any],
    locks: Path,
    assembled_evidence: dict[str, Any],
) -> bytes:
    """Use trusted current verification code with selected-source locks as data."""
    runtime_builder = runpy.run_path(str(ROOT / "scripts/build-browser-runtime.py"))
    assembler = runpy.run_path(str(ROOT / "scripts/build-browser-redistribution.py"))
    app_builder = runpy.run_path(str(ROOT / "scripts/build-browser-app.py"))
    runtime_record = object_value(manifest.get("runtime"))
    runtime_hash = digest_value(runtime_record.get("manifest_sha256"))
    prefix = f"app/runtime/{runtime_hash}"
    require(
        runtime_record.get("path") == prefix,
        "runtime path is not bound to its manifest",
    )
    runtime = site / prefix
    raw = regular_bytes(runtime / "manifest.json")
    require(sha256(raw) == runtime_hash, "runtime manifest SHA-256 mismatch")
    runtime_manifest = object_json(raw)
    runtime_builder["validate_worker_protocol"](runtime_manifest.get("worker_protocol"))
    require(
        object_value(manifest.get("capabilities")).get("source_import")
        == runtime_manifest["worker_protocol"]["source_import"],
        "site/runtime source capability mismatch",
    )
    require(
        exact_json(object_value(manifest.get("capabilities")).get("source_actions"))
        == exact_json(runtime_manifest["worker_protocol"]["source_actions"]),
        "site/runtime source-actions capability mismatch",
    )
    runtime_assets = records(runtime_manifest.get("assets"))
    site_assets = records(manifest.get("assets"))
    expected_runtime = {f"{prefix}/{name}" for name in runtime_assets} | {
        f"{prefix}/manifest.json"
    }
    require(
        {name for name in site_assets if name.startswith(prefix + "/")}
        == expected_runtime,
        "site/runtime asset closure mismatch",
    )
    for name, record in runtime_assets.items():
        require(
            all(
                site_assets[f"{prefix}/{name}"].get(key) == record[key]
                for key in ("bytes", "sha256")
            ),
            "site/runtime asset identity mismatch",
        )
    require(
        runtime_manifest.get("cad") is True
        and runtime_manifest.get("schema_version") == 1,
        "deployment requires the verified CAD runtime profile",
    )
    require(
        exact_json(manifest.get("backend"))
        == exact_json(
            {
                "type": "worker",
                "runtimeURL": f"./runtime/{runtime_hash}/worker.mjs",
                "cad": True,
                "sourceActions": True,
            }
        )
        and exact_json(manifest.get("capabilities"))
        == exact_json(
            {
                "runtime_bootstrap": True,
                "numerical": True,
                "cad": True,
                "source_import": runtime_manifest["worker_protocol"]["source_import"],
                "source_actions": runtime_manifest["worker_protocol"]["source_actions"],
            }
        ),
        "unexpected application backend/capabilities",
    )
    for value in (manifest, runtime_manifest, runtime_record):
        require(
            value.get("source_revision") == manifest["source_revision"]
            and value.get("source_worktree_dirty") is False,
            "source identity/cleanliness mismatch",
        )
    for value in (manifest, runtime_manifest):
        require(
            value.get("redistribution_ready") is True
            and value.get("redistribution_gaps") == [],
            "runtime/site redistribution is not ready",
        )
    require(
        manifest.get("runtime_redistribution_ready") is True,
        "site runtime is not redistribution ready",
    )
    runtime_lock_raw = regular_bytes(locks / "runtime-lock.json")
    runtime_lock = object_json(runtime_lock_raw)
    require(
        runtime_manifest.get("versions") == runtime_lock.get("versions")
        and runtime_record.get("versions") == runtime_lock.get("versions")
        and runtime_record.get("cad_assets_present") is True,
        "runtime versions/profile differ from selected source lock",
    )
    ui_hash = sha256(regular_bytes(locks / "package-lock.json"))
    bindings = {
        "runtime_lock_sha256": sha256(runtime_lock_raw),
        "ui_package_lock_sha256": ui_hash,
    }
    require(
        runtime_manifest.get("runtime_lock_sha256") == bindings["runtime_lock_sha256"],
        "selected runtime lock mismatch",
    )
    ui_inputs = [
        object_value(item).get("sha256")
        for item in array_value(manifest.get("build_inputs"))
        if object_value(item).get("path")
        == "experiments/browser_viewer/package-lock.json"
    ]
    require(ui_inputs == [ui_hash], "selected UI lock mismatch")
    runtime_builder["validate_runtime_profile"](runtime_manifest, runtime_lock)
    runtime_builder["validate_core_notices"](runtime, runtime_lock, True)
    require(
        {"worker.mjs", "integrity.mjs", "scansor.zip", "runtime/pyodide.mjs"}.issubset(
            runtime_assets
        ),
        "missing required runtime assets",
    )
    with zipfile.ZipFile(runtime / "scansor.zip") as source_archive:
        members = zip_inventory(source_archive)
        source_records = [
            object_value(item)
            for item in array_value(runtime_manifest.get("source_files"))
        ]
        source_names = [canonical(item.get("archive_path")) for item in source_records]
        require(
            len(source_names) == len(set(source_names))
            and set(source_names) == set(members),
            "source archive/provenance closure mismatch",
        )
        for item in source_records:
            require(
                sha256(
                    read_member(
                        source_archive, members[item["archive_path"]], MAX_MEMBER
                    )
                )
                == item.get("sha256"),
                "source archive provenance mismatch",
            )
    require(
        manifest.get("bounded_examples")
        == [app_builder["example_inventory"](runtime, runtime_manifest)],
        "bounded example inventory mismatch",
    )
    require(
        assembled_evidence.get("selected_groups") == ["cad", "numeric", "ui"],
        "assembly evidence lacks complete group selection",
    )
    for directory, value, groups, external in (
        (
            runtime / "redistribution",
            runtime_manifest,
            ["numeric", "cad"],
            {"existing-core-and-wheel-notices"},
        ),
        (site / "redistribution", manifest, ["ui"], set()),
    ):
        supplement = assembler["verify"](
            directory, inventory_path=locks / "browser-redistribution-lock.json"
        )
        require(
            assembler["select_verified_manifest"](assembled_evidence, groups)
            == supplement,
            "assembly evidence differs from verified selected package",
        )
        require(
            supplement.get("bindings") == bindings
            and supplement.get("selected_groups") == sorted(groups),
            "selected redistribution locks/groups mismatch",
        )
        for group in groups:
            require(
                supplement["groups"][group].get("supplemental_requirements_complete")
                is True,
                "incomplete redistribution requirements",
            )
        metadata = value.get("redistribution")
        require(
            isinstance(metadata, dict)
            and metadata
            == {
                "path": "redistribution",
                "inventory_sha256": supplement["inventory_sha256"],
                "manifest_sha256": sha256(regular_bytes(directory / "manifest.json")),
                "requirements": runtime_builder["complete_requirements"](
                    supplement, groups, external
                ),
            },
            "redistribution readiness evidence does not match derived requirements",
        )
        allowed = {record["path"] for record in supplement["assets"]} | {
            "manifest.json"
        }
        listed = site_assets if directory == site / "redistribution" else runtime_assets
        require(
            {
                name.removeprefix("redistribution/")
                for name in listed
                if name.startswith("redistribution/")
            }
            == allowed,
            "redistribution asset closure mismatch",
        )
    return raw


def check_destination(path: Path, inputs: list[Path]) -> Path:
    absolute = path.absolute()
    require(
        not any(p.is_symlink() for p in (absolute, *absolute.parents)),
        "symlink output component",
    )
    resolved = absolute.resolve()
    require(not resolved.exists(), "output must be fresh and nonexistent")
    for source in [ROOT, *inputs]:
        source = source.resolve()
        require(
            not resolved.is_relative_to(source) and not source.is_relative_to(resolved),
            "output overlaps repository or input",
        )
    return resolved


def hash_stream(stream: BinaryIO) -> str:
    result = hashlib.sha256()
    while data := stream.read(1024 * 1024):
        result.update(data)
    return result.hexdigest()


def validate(
    archive_path: Path,
    run_path: Path,
    artifact_path: Path,
    locks_path: Path,
    output: Path,
    *,
    repository: str,
    run_id: int,
    artifact_id: int,
    archive_sha256: str,
    site_manifest_sha256: str,
    evidence_output: Path | None = None,
) -> dict[str, Any]:
    inputs = [archive_path, run_path, artifact_path, locks_path]
    output = check_destination(output, inputs)
    if evidence_output is not None:
        evidence_output = check_destination(evidence_output, [*inputs, output])
    _ = digest_value(site_manifest_sha256)
    run = object_json(regular_bytes(run_path))
    artifact = object_json(regular_bytes(artifact_path))
    lock_bytes = {name: regular_bytes(locks_path / name) for name in LOCK_NAMES}
    for data in lock_bytes.values():
        _ = object_json(data)
    require(
        not any(p.is_symlink() for p in (archive_path, *archive_path.parents))
        and archive_path.is_file(),
        "expected regular archive input",
    )
    with archive_path.open("rb") as stream:
        size = stream.seek(0, 2)
        _ = stream.seek(0)
        revision = validate_metadata(
            run,
            artifact,
            repository=repository,
            run_id=run_id,
            artifact_id=artifact_id,
            archive_sha256=archive_sha256,
            archive_size=size,
        )
        require(
            hash_stream(stream) == archive_sha256, "downloaded archive SHA-256 mismatch"
        )
        _ = stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            files = zip_inventory(archive)
            require("site/manifest.json" in files, "missing site manifest")
            site_raw = read_member(archive, files["site/manifest.json"])
            require(
                sha256(site_raw) == site_manifest_sha256,
                "expected site manifest SHA-256 mismatch",
            )
            manifest = object_json(site_raw)
            require(
                manifest.get("schema_version") == 1
                and manifest.get("kind") == "scansor-static-app"
                and manifest.get("entry") == "app/index.html",
                "unsupported static site manifest",
            )
            require(
                manifest.get("source_revision") == revision
                and manifest.get("source_worktree_dirty") is False,
                "site source does not match selected clean run head",
            )
            assets = records(manifest.get("assets"))
            require("app/index.html" in assets, "missing application entry")
            expected = (
                {f"site/{name}" for name in assets}
                | {"site/manifest.json"}
                | {f"evidence/{name}" for name in EVIDENCE}
            )
            require(set(files) == expected, "unexpected or missing archive payload")
            evidence = {
                name: read_member(archive, files[f"evidence/{name}"])
                for name in EVIDENCE
            }
            runtime_hash = digest_value(
                object_value(manifest.get("runtime")).get("manifest_sha256")
            )
            runtime_name = f"app/runtime/{runtime_hash}/manifest.json"
            require(runtime_name in assets, "missing runtime manifest asset")
            runtime_raw = read_member(archive, files[f"site/{runtime_name}"])
            validate_evidence(evidence, site_raw, runtime_raw)
            # All selection/provenance/archive checks precede any output creation.
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="pages-validation-", dir=output.parent
            ) as temporary:
                staging = Path(temporary)
                locks = staging / "locks"
                locks.mkdir()
                for name, data in lock_bytes.items():
                    _ = (locks / name).write_bytes(data)
                site = staging / "site"
                site.mkdir()
                _ = (site / "manifest.json").write_bytes(site_raw)
                for name, record in assets.items():
                    target = site / name
                    require(
                        files[f"site/{name}"].file_size == record["bytes"],
                        "manifest/archive size mismatch",
                    )
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with (
                        archive.open(files[f"site/{name}"]) as source,
                        target.open("xb") as destination,
                    ):
                        shutil.copyfileobj(source, destination, 1024 * 1024)
                    require(
                        target.stat().st_size == record["bytes"],
                        f"asset byte count mismatch: {name}",
                    )
                    with target.open("rb") as data:
                        require(
                            hash_stream(data) == record["sha256"],
                            f"asset SHA-256 mismatch: {name}",
                        )
                _ = validate_semantics(
                    site,
                    manifest,
                    locks,
                    object_json(evidence["build-redistribution.json"]),
                )
                result = {
                    "schema_version": 1,
                    "kind": "scansor-pages-artifact-selection",
                    "verified": True,
                    "repository": repository,
                    "run_id": run_id,
                    "artifact_id": artifact_id,
                    "source_revision": revision,
                    "archive_sha256": archive_sha256,
                    "archive_bytes": size,
                    "site_manifest_sha256": site_manifest_sha256,
                    "source_locks": {
                        name: identity(data) for name, data in lock_bytes.items()
                    },
                    "verification_steps": list(STEPS),
                    "redistribution_ready": True,
                    "redistribution_gaps": [],
                    "site_files": len(assets) + 1,
                    "site_bytes": len(site_raw)
                    + sum(record["bytes"] for record in assets.values()),
                }
                # Recheck paths after validation; never merge into existing output.
                _ = check_destination(output, inputs)
                _ = site.rename(output)
                if evidence_output is not None:
                    _ = check_destination(evidence_output, [*inputs, output])
                    evidence_output.parent.mkdir(parents=True, exist_ok=True)
                    with evidence_output.open("x") as destination:
                        _ = destination.write(json.dumps(result, indent=2) + "\n")
                return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "run", "artifact", "source-locks", "output"):
        _ = parser.add_argument(f"--{name}", type=Path, required=True)
    _ = parser.add_argument("--repository", required=True)
    _ = parser.add_argument("--run-id", type=int, required=True)
    _ = parser.add_argument("--artifact-id", type=int, required=True)
    _ = parser.add_argument("--archive-sha256", required=True)
    _ = parser.add_argument("--site-manifest-sha256", required=True)
    _ = parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    try:
        result = validate(
            args.archive,
            args.run,
            args.artifact,
            args.source_locks,
            args.output,
            repository=args.repository,
            run_id=args.run_id,
            artifact_id=args.artifact_id,
            archive_sha256=args.archive_sha256,
            site_manifest_sha256=args.site_manifest_sha256,
            evidence_output=args.evidence,
        )
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as error:
        parser.exit(1, f"artifact rejected: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
