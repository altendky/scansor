#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.12.12"
# dependencies = []
# ///
"""Reconcile a verified generated fit with retained Track 5 CAD evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, Never

FORMAT = "scansor-generated-track5-cad-reconciliation-v1-experiment-local"
FORMAT_STATUS = "provisional/experiment-local/non-public-contract"
GENERATOR_SHA256 = "995a067d7f4bd247defd092a7e8501224ce7393e9e9e09dc47726f13b610a1e8"
CONTRACT_SHA256 = "2c77ce6c586a5f5ebc29f1dfe93f6f19264a8849a8dd62ccb22ef3b5338ca175"
SOLVER_SHA256 = "a998a433f402bbe52ff20311580742b55ed48a3565e0745699c764fffb92355d"
SOLVER_EVIDENCE_SHA256 = (
    "47d6caec9203e9daa0edd6ee0b9ead87b586e1cbc4c17855109eaad32e7b2256"
)
TRACK5_SHA256 = "8d165b5ea88b6c48ba25d0e351a60835d486a23e3fce4882ee725ba020dca3d7"
RUN_01_MANIFEST_SHA256 = (
    "aa257d2707a4fba283aaa2c810e48c4793d1d64a5475cdcd24bf8ae7e65ed1e3"
)
RUN_02_MANIFEST_SHA256 = (
    "373907a5c1a3d8ec7cd9cc2a878a71afc57ea6722132180e71ff8e23cd8e34ae"
)
SUITE_MANIFEST_SHA256 = (
    "f6e373536b6fa2f76b349624fa8652418191608e1434319cbc64ead9a7a0242a"
)
RAW_CHANGE_NOTES_SHA256 = (
    "1c853d7cc013a3144950afece3f0e3ce2ff55a80de9290d5ec4299133fcfabf7"
)
LINEAR_TOLERANCE_M = 1e-9
ANGULAR_TOLERANCE_RAD = 1e-9
SCENARIO_ID = "noiseless-fixed-pose"
SHAPE_NAMES = (
    "radius.band-1_m",
    "radius.band-2_m",
    "radius.band-3_m",
    "station-20_m",
    "station-50_m",
    "station-80_m",
    "datum-flat-x_m",
)
GENERATED_TRUTH = {
    "datum-flat-x_m": 0.016,
    "radius.band-1_m": 0.012,
    "radius.band-2_m": 0.018,
    "radius.band-3_m": 0.014,
    "station-20_m": 0.020,
    "station-50_m": 0.050,
    "station-80_m": 0.080,
}
EXPECTED_SOURCE_PINS = {
    "contract": "stepped-rotational-v1",
    "contract_sha256": CONTRACT_SHA256,
    "document_id": "1b68f4b8f4a69c6b59d7616e",
    "generator_source_sha256": GENERATOR_SHA256,
    "generator_version": "1.0.5",
}
INFLUENCE_CATEGORIES = (
    "bounds",
    "initialization",
    "loss",
    "residuals",
    "scales",
    "tuning",
    "weights",
    "held_out",
)
PROHIBITED_IMPORT_ROOTS = frozenset({"numpy", "scipy"})
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_REGULAR_BYTES = 32 * 1024 * 1024
IO_CHUNK_SIZE = 64 * 1024


class ReconciliationError(ValueError):
    """A source, evidence, ordering, or reconciliation requirement failed."""


def fail(message: str) -> Never:
    raise ReconciliationError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True)
        + "\n"
    ).encode("ascii")


def compact_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes, label: str) -> Any:
    if len(data) > MAX_JSON_BYTES:
        fail(f"{label} exceeds size limit")
    try:
        return json.loads(
            data.decode("ascii"),
            object_pairs_hook=unique_object,
            parse_constant=lambda value: fail(f"{label}: non-finite JSON {value}"),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconciliationError(f"{label}: invalid JSON: {error}") from error


def read_regular(path: Path, label: str) -> bytes:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    try:
        directory_fd = os.open(path.parent, directory_flags)
        descriptor = os.open(path.name, file_flags, dir_fd=directory_fd)
        opened = os.fstat(descriptor)
    except OSError as error:
        raise ReconciliationError(f"{label}: cannot open {path}: {error}") from error
    finally:
        if "directory_fd" in locals():
            os.close(directory_fd)
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        fail(f"{label}: expected regular non-symlink file: {path}")
    if opened.st_size < 0 or opened.st_size > MAX_REGULAR_BYTES:
        os.close(descriptor)
        fail(f"{label}: file size exceeds bound")
    output = bytearray()
    with os.fdopen(descriptor, "rb") as stream:
        while chunk := stream.read(IO_CHUNK_SIZE):
            output.extend(chunk)
            if len(output) > MAX_REGULAR_BYTES:
                fail(f"{label}: file exceeded bound while reading")
    if len(output) != opened.st_size:
        fail(f"{label}: file changed while reading")
    return bytes(output)


def verify_sidecar(
    path: Path, expected: str, label: str, *, replace_suffix: bool = False
) -> bytes:
    data = read_regular(path, label)
    sidecar_path = (
        path.with_suffix(".sha256")
        if replace_suffix
        else path.with_suffix(path.suffix + ".sha256")
    )
    sidecar = read_regular(sidecar_path, f"{label} sidecar")
    expected_sidecar = f"{expected}  {path.name}\n".encode("ascii")
    if sidecar != expected_sidecar or sha256(data) != expected:
        fail(f"{label} source/content or sidecar mismatch")
    return data


def source_has_no_optimizer_or_solver_import(source: bytes) -> dict[str, Any]:
    try:
        tree = ast.parse(source.decode("ascii"))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ReconciliationError(
            f"reconciliation source is invalid: {error}"
        ) from error
    imports: list[str] = []
    prohibited_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in {"least_squares", "minimize", "curve_fit", "root"}:
                prohibited_calls.append(name)
    prohibited_imports = sorted(
        name
        for name in imports
        if name.split(".", 1)[0] in PROHIBITED_IMPORT_ROOTS
        or name.startswith("generated_solver_evaluator")
    )
    if prohibited_imports or prohibited_calls:
        fail("reconciliation source contains an optimizer or solver import path")
    return {
        "optimizer_calls": prohibited_calls,
        "prohibited_imports": prohibited_imports,
        "solver_execution_boundary": "isolated pinned verifier subprocess only",
    }


def verify_runtime() -> dict[str, str]:
    actual = tuple(sys.version_info[:3])
    if actual != (3, 12, 12) or sys.implementation.name != "cpython":
        fail("reconciliation requires exact CPython 3.12.12")
    return {"implementation": sys.implementation.name, "python": "3.12.12"}


def run_solver_verifier(solver_source: Path, evidence: Path) -> None:
    repository_root = solver_source.parent.parent.resolve()
    temporary_root = (Path(os.environ.get("TMPDIR", "/tmp")) / "agents").resolve(
        strict=False
    )
    if temporary_root == repository_root or temporary_root.is_relative_to(
        repository_root
    ):
        fail("temporary workspace must be outside the repository")
    temporary_root.mkdir(parents=True, exist_ok=True)
    source_root = solver_source.parent
    files = (
        "generate_stepped_rotational_v1.py",
        "generate_stepped_rotational_v1.sha256",
        "generated_solver_evaluator_v1.py",
        "generated_solver_evaluator_v1.sha256",
        "generated-solver-evaluator-v1-evidence.json",
        "generated-solver-evaluator-v1-evidence.json.sha256",
    )
    with tempfile.TemporaryDirectory(
        dir=temporary_root, prefix="scansor-reconciliation-"
    ) as directory:
        snapshot = Path(directory) / "experiments"
        snapshot.mkdir()
        for name in files:
            (snapshot / name).write_bytes(
                read_regular(source_root / name, f"Phase 1 snapshot {name}")
            )
        snapshot_solver = snapshot / solver_source.name
        snapshot_evidence = snapshot / evidence.name
        verify_sidecar(
            snapshot / "generate_stepped_rotational_v1.py",
            GENERATOR_SHA256,
            "snapshot generator",
            replace_suffix=True,
        )
        verify_sidecar(
            snapshot_solver,
            SOLVER_SHA256,
            "snapshot solver",
            replace_suffix=True,
        )
        verify_sidecar(
            snapshot_evidence, SOLVER_EVIDENCE_SHA256, "snapshot solver evidence"
        )
        command = [
            "uv",
            "run",
            "--offline",
            str(snapshot_solver),
            "verify-evidence",
            str(snapshot_evidence),
        ]
        completed = subprocess.run(
            command,
            check=False,
            cwd=Path(directory),
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPYCACHEPREFIX": str(Path(directory) / "empty-pycache"),
                "UV_NO_SYNC": "1",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        fail(f"Phase 1 semantic verification failed: {detail}")


def verify_phase1(
    root: Path,
    events: list[str],
    verifier: Callable[[Path, Path], None] = run_solver_verifier,
) -> tuple[dict[str, Any], dict[str, Any]]:
    experiments = root / "experiments"
    generator = experiments / "generate_stepped_rotational_v1.py"
    solver = experiments / "generated_solver_evaluator_v1.py"
    evidence_path = experiments / "generated-solver-evaluator-v1-evidence.json"
    verify_sidecar(generator, GENERATOR_SHA256, "generator", replace_suffix=True)
    events.append("generator-source-and-sidecar-verified")
    verify_sidecar(solver, SOLVER_SHA256, "solver", replace_suffix=True)
    events.append("solver-source-and-sidecar-verified")
    evidence_before = verify_sidecar(
        evidence_path, SOLVER_EVIDENCE_SHA256, "solver evidence"
    )
    events.append("solver-evidence-and-sidecar-verified")
    verifier(solver, evidence_path)
    events.append("solver-semantic-recomputation-completed")
    evidence_after = verify_sidecar(
        evidence_path, SOLVER_EVIDENCE_SHA256, "solver evidence after recomputation"
    )
    if evidence_before != evidence_after:
        fail("solver evidence changed during verification")
    evidence = parse_json(evidence_after, "solver evidence")
    expected_contract = {
        "contract": "stepped-rotational-v1",
        "contract_logical_sha256": CONTRACT_SHA256,
        "generator_source_sha256": GENERATOR_SHA256,
        "generator_version": "1.0.5",
        "scope": "provisional/experiment-local/non-public-contract",
    }
    if (
        evidence.get("contract") != expected_contract
        or evidence.get("solver_source_sha256") != SOLVER_SHA256
        or evidence.get("runtime")
        != {
            "implementation": "cpython",
            "numpy": "2.3.1",
            "policy": "exact CPython runtime required for retained evidence equality",
            "python": "3.12.12",
            "scipy": "1.16.1",
        }
    ):
        fail("verified solver evidence source, contract, or runtime identity mismatch")
    scenario = evidence.get("scenarios", {}).get(SCENARIO_ID)
    if not isinstance(scenario, dict):
        fail("verified solver evidence lacks selected scenario")
    if (
        scenario.get("disposition") != "passed"
        or scenario.get("termination", {}).get("success") is not True
        or scenario.get("support", {}).get("geometry_valid") is not True
        or scenario.get("callbacks", {}).get("held_out_seen") != []
        or evidence.get("declared_evidence_boundaries", {}).get(
            "generated_held_out_used_for_fit_or_tuning"
        )
        is not False
    ):
        fail(
            "selected solver scenario or held-out boundary is not verified nominal fit"
        )
    estimate = scenario.get("solver", {}).get("estimate")
    if not isinstance(estimate, dict) or set(estimate) != set(SHAPE_NAMES):
        fail("selected solver estimate is incomplete")
    selected = {
        "estimate": estimate,
        "result_sha256": sha256(compact_json(scenario)),
        "scenario_id": SCENARIO_ID,
    }
    events.append("verified-solver-evidence-reopened-unchanged")
    return evidence, selected


def fitted_prediction(estimate: Mapping[str, Any]) -> dict[str, Any]:
    try:
        r1, r2, r3, s1, s2, s3, datum_x = (
            float(estimate[name]) for name in SHAPE_NAMES
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReconciliationError(
            "selected estimate is not seven finite numbers"
        ) from error
    values = (r1, r2, r3, s1, s2, s3, datum_x)
    if not all(math.isfinite(value) for value in values):
        fail("selected estimate is not finite")
    if not (0.0 < r1 < r2 and 0.0 < r3 < r2 and 0.0 < s1 < s2 < s3):
        fail("selected estimate violates fitted local geometry ordering")
    radicand = r2 * r2 - datum_x * datum_x
    if radicand <= 0.0:
        fail("selected estimate produces empty datum trim")
    y_half = math.sqrt(radicand)
    axis = {
        "closest_point_m": [0.0, 0.0, 0.0],
        "direction_unoriented": [0.0, 0.0, 1.0],
    }

    def cylinders(asymmetric: bool) -> list[dict[str, Any]]:
        return [
            {
                "axis": dict(axis),
                "radius_m": radius,
                "trim": {"x_max_m": datum_x if asymmetric and index == 1 else None},
                "z_bounds_m": bounds,
            }
            for index, (radius, bounds) in enumerate(
                ((r1, [0.0, s1]), (r2, [s1, s2]), (r3, [s2, s3]))
            )
        ]

    axial_specs = (
        (0.0, [0.0, r1], [0.0, 0.0, -1.0]),
        (s1, [r1, r2], [0.0, 0.0, -1.0]),
        (s2, [r3, r2], [0.0, 0.0, 1.0]),
        (s3, [0.0, r3], [0.0, 0.0, 1.0]),
    )

    def planes(asymmetric: bool) -> list[dict[str, Any]]:
        result = []
        for index, (station, radius_bounds, normal) in enumerate(axial_specs):
            bounds: dict[str, Any] = {"radius": radius_bounds}
            if asymmetric and index in {1, 2}:
                bounds["x"] = [-r2, datum_x]
            result.append(
                {
                    "bounds_m": bounds,
                    "kind": "axial",
                    "orientation": True,
                    "outward_normal": normal,
                    "source_normal": normal,
                    "station_m": station,
                }
            )
        if asymmetric:
            result.append(
                {
                    "bounds_m": {
                        "x": [datum_x, datum_x],
                        "y": [-y_half, y_half],
                        "z": [s1, s2],
                    },
                    "kind": "datum",
                    "orientation": False,
                    "outward_normal": [1.0, 0.0, 0.0],
                    "source_normal": [-1.0, 0.0, 0.0],
                    "station_m": None,
                }
            )
        return result

    variants = []
    for variant, asymmetric in (
        ("axisymmetric", False),
        ("asymmetric_datum_flat", True),
    ):
        variants.append(
            {
                "cylinders": cylinders(asymmetric),
                "face_count": 8 if asymmetric else 7,
                "frame": {"axis": "+Z", "handedness": "right", "length_unit": "m"},
                "planes": planes(asymmetric),
                "variant": variant,
            }
        )
    return {
        "derivation": "verified noiseless-fixed-pose seven-shape estimate only",
        "variants": variants,
    }


def frozen_prediction_bytes(estimate: Mapping[str, Any]) -> bytes:
    return compact_json(fitted_prediction(estimate))


def disposable_prediction(prediction_bytes: bytes) -> dict[str, Any]:
    prediction = parse_json(prediction_bytes, "fitted prediction")
    if not isinstance(prediction, dict) or compact_json(prediction) != prediction_bytes:
        fail("fitted prediction bytes are not canonical")
    return prediction


def _vector_angle(
    left: Sequence[float], right: Sequence[float], unoriented: bool
) -> float:
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))
    cross = (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )
    angle = math.atan2(math.sqrt(sum(value * value for value in cross)), dot)
    return min(angle, math.pi - angle) if unoriented else angle


def _comparison_summary(
    findings: Sequence[Mapping[str, Any]], label: str
) -> dict[str, Any]:
    linear = [
        float(item["difference_m"]) for item in findings if "difference_m" in item
    ]
    angular = [
        float(item["difference_rad"]) for item in findings if "difference_rad" in item
    ]
    classes = sorted({str(item["classification"]) for item in findings})
    if any(item not in {"exact", "tolerated numerical"} for item in classes):
        fail(f"{label} has unsupported, unexplained, or failed geometry")
    return {
        "classifications": classes,
        "field_count": len(findings),
        "label": label,
        "maximum_angular_error_rad": max(angular, default=0.0),
        "maximum_linear_error_m": max(linear, default=0.0),
        "outcome": "tolerated numerical"
        if "tolerated numerical" in classes
        else "exact",
        "tolerances": {
            "angular_rad": ANGULAR_TOLERANCE_RAD,
            "linear_m": LINEAR_TOLERANCE_M,
        },
    }


def compare_prediction(
    prediction_bytes: bytes,
    normalized: Mapping[str, Any],
    track5: ModuleType,
    label: str,
) -> tuple[dict[str, Any], list[str]]:
    prediction = disposable_prediction(prediction_bytes)
    expected_variants = {item["variant"]: item for item in prediction["variants"]}
    actual_variants = {item["variant"]: item for item in normalized.get("variants", [])}
    if set(actual_variants) != set(expected_variants):
        fail(f"{label}: both fitted variants are required")
    semantic: list[str] = []
    for name in ("axisymmetric", "asymmetric_datum_flat"):
        expected = expected_variants[name]
        actual = actual_variants[name]
        if actual.get("frame") != expected["frame"]:
            fail(f"{label}: {name} frame mismatch")
        if actual.get("face_count") != expected["face_count"]:
            fail(f"{label}: {name} face count mismatch")
        if len(actual.get("cylinders", [])) != 3:
            fail(f"{label}: {name} cylinder inventory mismatch")
        for cylinder in actual["cylinders"]:
            source = cylinder.get("axis", {}).get("source_direction")
            unoriented = cylinder.get("axis", {}).get("direction_unoriented")
            if (
                not isinstance(source, list)
                or not isinstance(unoriented, list)
                or _vector_angle(source, unoriented, True) > ANGULAR_TOLERANCE_RAD
            ):
                fail(f"{label}: {name} source/unoriented cylinder axis mismatch")
        if len(actual.get("planes", [])) != len(expected["planes"]):
            fail(f"{label}: {name} plane inventory mismatch")
        for plane, expected_plane in zip(
            actual["planes"], expected["planes"], strict=True
        ):
            source = plane.get("source_normal")
            outward = plane.get("outward_normal")
            orientation = plane.get("orientation")
            if (
                plane.get("kind") != expected_plane["kind"]
                or orientation is not expected_plane["orientation"]
                or _vector_angle(source, expected_plane["source_normal"], False)
                > ANGULAR_TOLERANCE_RAD
            ):
                fail(f"{label}: {name} source normal or orientation mismatch")
            expected_outward = (
                source if orientation is True else [-value for value in source]
            )
            if _vector_angle(expected_outward, outward, False) > ANGULAR_TOLERANCE_RAD:
                fail(f"{label}: {name} source/orientation/outward normal mismatch")
        semantic.extend(
            [
                f"{name}: right-handed metre +Z frame preserved",
                f"{name}: face count {expected['face_count']} and variant semantics preserved",
                f"{name}: cylinder axes compared as unoriented; source directions retained separately",
                f"{name}: axial and datum normals compared as oriented with source/orientation distinction",
            ]
        )
    synthetic = {"variants": list(prediction["variants"])}
    try:
        findings = track5._geometry_findings(synthetic, normalized, truth=False)
    except (KeyError, TypeError, ValueError) as error:
        raise ReconciliationError(
            f"{label}: geometry comparison failed: {error}"
        ) from error
    return _comparison_summary(findings, label), semantic


def generated_truth_comparison(prediction_bytes: bytes) -> dict[str, Any]:
    prediction = disposable_prediction(prediction_bytes)
    variants = {item["variant"]: item for item in prediction["variants"]}
    asymmetric = variants["asymmetric_datum_flat"]
    cylinders = asymmetric["cylinders"]
    estimate = {
        "radius.band-1_m": cylinders[0]["radius_m"],
        "radius.band-2_m": cylinders[1]["radius_m"],
        "radius.band-3_m": cylinders[2]["radius_m"],
        "station-20_m": cylinders[0]["z_bounds_m"][1],
        "station-50_m": cylinders[1]["z_bounds_m"][1],
        "station-80_m": cylinders[2]["z_bounds_m"][1],
        "datum-flat-x_m": cylinders[1]["trim"]["x_max_m"],
    }
    findings = []
    for name in SHAPE_NAMES:
        actual = float(estimate[name])
        expected = GENERATED_TRUTH[name]
        difference = abs(actual - expected)
        findings.append(
            {
                "classification": (
                    "exact"
                    if difference == 0.0
                    else "tolerated numerical"
                    if difference <= LINEAR_TOLERANCE_M
                    else "failure"
                ),
                "difference_m": difference,
            }
        )
    return _comparison_summary(findings, "fitted-to-generated-truth")


def load_track5(source: bytes, path: Path) -> ModuleType:
    name = "_scansor_verified_track5_reconciliation_dependency"
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        # Execute the hash-verified bytes directly; path loaders may accept stale pyc.
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102
    except (SyntaxError, TypeError) as error:
        raise ReconciliationError(
            f"cannot load verified Track 5 verifier: {error}"
        ) from error
    return module


def tree_fingerprint(
    root: Path, before_confirmation: Callable[[], None] | None = None
) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
        root_stat = os.fstat(root_fd)
    except OSError as error:
        raise ReconciliationError(
            f"CAD evidence root cannot be opened: {error}"
        ) from error
    if not stat.S_ISDIR(root_stat.st_mode):
        os.close(root_fd)
        fail(f"CAD evidence root is not a directory: {root}")
    entries: list[tuple[str, str]] = []
    chains: list[tuple[tuple[str, ...], int, int, int]] = []
    stack = [(os.dup(root_fd), ())]
    while stack:
        directory_fd, prefix = stack.pop()
        try:
            with os.scandir(os.dup(directory_fd)) as scanned:
                children = sorted(scanned, key=lambda entry: entry.name)
            for child in children:
                relative_parts = (*prefix, child.name)
                relative = "/".join(relative_parts)
                metadata = os.stat(
                    child.name, dir_fd=directory_fd, follow_symlinks=False
                )
                if stat.S_ISLNK(metadata.st_mode):
                    fail(f"CAD evidence contains symlink: {relative}")
                if stat.S_ISDIR(metadata.st_mode):
                    child_fd = os.open(child.name, flags, dir_fd=directory_fd)
                    opened = os.fstat(child_fd)
                    if (opened.st_dev, opened.st_ino) != (
                        metadata.st_dev,
                        metadata.st_ino,
                    ):
                        os.close(child_fd)
                        fail(f"CAD evidence directory was replaced: {relative}")
                    chains.append(
                        (
                            relative_parts,
                            opened.st_dev,
                            opened.st_ino,
                            stat.S_IFDIR,
                        )
                    )
                    stack.append((child_fd, relative_parts))
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    fail(f"CAD evidence contains special entry: {relative}")
                leaf_fd = os.open(
                    child.name,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=directory_fd,
                )
                opened = os.fstat(leaf_fd)
                if (opened.st_dev, opened.st_ino) != (
                    metadata.st_dev,
                    metadata.st_ino,
                ):
                    os.close(leaf_fd)
                    fail(f"CAD evidence file was replaced: {relative}")
                chains.append(
                    (
                        relative_parts,
                        opened.st_dev,
                        opened.st_ino,
                        stat.S_IFREG,
                    )
                )
                digest = hashlib.sha256()
                read_size = 0
                with os.fdopen(leaf_fd, "rb") as stream:
                    while chunk := stream.read(IO_CHUNK_SIZE):
                        digest.update(chunk)
                        read_size += len(chunk)
                        if read_size > MAX_REGULAR_BYTES:
                            fail(f"CAD evidence file exceeds bound: {relative}")
                if read_size != opened.st_size:
                    fail(f"CAD evidence file changed while reading: {relative}")
                entries.append((relative, digest.hexdigest()))
        finally:
            os.close(directory_fd)
    if before_confirmation is not None:
        before_confirmation()
    for parts, expected_device, expected_inode, expected_type in chains:
        current_fd = os.dup(root_fd)
        try:
            for index, part in enumerate(parts):
                is_leaf = index == len(parts) - 1
                component_flags = os.O_RDONLY | os.O_NOFOLLOW
                if not is_leaf or expected_type == stat.S_IFDIR:
                    component_flags |= os.O_DIRECTORY
                else:
                    component_flags |= getattr(os, "O_NONBLOCK", 0)
                next_fd = os.open(part, component_flags, dir_fd=current_fd)
                os.close(current_fd)
                current_fd = next_fd
            current = os.fstat(current_fd)
        except OSError as error:
            raise ReconciliationError(
                f"CAD evidence path component was replaced: {error}"
            ) from error
        finally:
            os.close(current_fd)
        if stat.S_IFMT(current.st_mode) != expected_type or (
            current.st_dev,
            current.st_ino,
        ) != (expected_device, expected_inode):
            fail("CAD evidence path component was replaced")
    try:
        current = os.stat(root, follow_symlinks=False)
    except OSError as error:
        os.close(root_fd)
        raise ReconciliationError(f"CAD evidence root was replaced: {error}") from error
    os.close(root_fd)
    if (current.st_dev, current.st_ino) != (root_stat.st_dev, root_stat.st_ino):
        fail("CAD evidence root was replaced")
    entries.sort()
    return {"file_count": len(entries), "tree_sha256": sha256(compact_json(entries))}


def verify_manifest_hash(root: Path, filename: str, expected: str, label: str) -> None:
    data = read_regular(root / filename, label)
    sidecar = read_regular(
        root / f"{filename.removesuffix('.json')}.sha256", f"{label} sidecar"
    )
    if sha256(data) != expected or sidecar != f"{expected}  {filename}\n".encode(
        "ascii"
    ):
        fail(f"{label} hash or sidecar mismatch")


def verify_track5(
    root: Path,
    prediction_bytes: bytes,
    events: list[str],
) -> dict[str, Any]:
    experiments = root / "experiments"
    source_path = experiments / "track5_onshape_cad_repro.py"
    verify_sidecar(source_path, TRACK5_SHA256, "Track 5 verifier", replace_suffix=True)
    events.append("track5-tool-source-and-sidecar-verified")
    source_data = read_regular(source_path, "Track 5 verifier")
    if sha256(source_data) != TRACK5_SHA256:
        fail("Track 5 verifier changed before import")
    track5 = load_track5(source_data, source_path)
    if track5.verify_tool_source(TRACK5_SHA256) != TRACK5_SHA256:
        fail("Track 5 verifier rejected its source identity")
    run_01 = experiments / "track5-cad-evidence-run-01-backfill"
    run_02 = experiments / "track5-cad-evidence-run-02"
    suite = experiments / "track5-cad-evidence-suite"
    roots = {"run-01-backfill": run_01, "run-02": run_02, "suite": suite}
    events.append("cad-evidence-paths-first-opened")
    before = {name: tree_fingerprint(path) for name, path in roots.items()}
    verify_manifest_hash(
        run_01, "manifest.json", RUN_01_MANIFEST_SHA256, "run-01 manifest"
    )
    verify_manifest_hash(
        run_02, "manifest.json", RUN_02_MANIFEST_SHA256, "run-02 manifest"
    )
    verify_manifest_hash(
        suite, "suite-manifest.json", SUITE_MANIFEST_SHA256, "suite manifest"
    )
    suite_manifest = track5.verify_suite(run_01, run_02, suite, TRACK5_SHA256)
    events.append("track5-suite-transitively-recomputed")
    normalized_01 = track5.replay(run_01, TRACK5_SHA256)
    events.append("track5-run-01-independently-replayed")
    normalized_02 = track5.replay(run_02, TRACK5_SHA256)
    events.append("track5-run-02-independently-replayed")
    if (
        normalized_01.get("source_pins") != EXPECTED_SOURCE_PINS
        or normalized_02.get("source_pins") != EXPECTED_SOURCE_PINS
        or suite_manifest.get("source_pins") != EXPECTED_SOURCE_PINS
    ):
        fail("Track 5 source pins mismatch")
    notes_data = read_regular(suite / "raw-change-notes.json", "raw change notes")
    if sha256(notes_data) != RAW_CHANGE_NOTES_SHA256:
        fail("raw change notes hash mismatch")
    notes = parse_json(notes_data, "raw change notes")
    truth_01 = track5.compare_to_truth(normalized_01, "run-01-backfill-vs-truth")
    truth_02 = track5.compare_to_truth(normalized_02, "run-02-vs-truth")
    cross_run = track5.compare_runs(
        normalized_01, normalized_02, "run-01-vs-run-02", notes
    )
    if (
        truth_01.get("outcome") not in {"exact", "tolerated numerical"}
        or truth_02.get("outcome") not in {"exact", "tolerated numerical"}
        or cross_run.get("outcome") != "semantically equivalent"
        or any(
            finding.get("classification") != "exact"
            for finding in cross_run.get("findings", [])
        )
        or any(
            value in {"failure", "unsupported", "unexplained"}
            for report in (truth_01, truth_02, cross_run)
            for value in report.get("classifications", [])
        )
    ):
        fail("existing Track 5 truth or cross-run outcome is not accepted")
    fitted_01, semantics_01 = compare_prediction(
        prediction_bytes, normalized_01, track5, "fitted-to-run-01-backfill"
    )
    fitted_02, semantics_02 = compare_prediction(
        prediction_bytes, normalized_02, track5, "fitted-to-run-02"
    )
    after = {name: tree_fingerprint(path) for name, path in roots.items()}
    if before != after:
        fail("Track 5 retained evidence changed during reconciliation")
    events.append("cad-evidence-read-only-fingerprints-unchanged")
    return {
        "existing_outcomes": {
            "cross_run": {
                "classifications": cross_run["classifications"],
                "normalized_geometry": "exact",
                "outcome": cross_run["outcome"],
                "revision_ids": "expected revision-ID changes",
                "source_metadata": "variable metadata",
            },
            "run_01_truth": truth_01["outcome"],
            "run_02_truth": truth_02["outcome"],
        },
        "fitted_to_runs": [fitted_01, fitted_02],
        "manifest_sha256": {
            "run_01": RUN_01_MANIFEST_SHA256,
            "run_02": RUN_02_MANIFEST_SHA256,
            "suite": SUITE_MANIFEST_SHA256,
        },
        "raw_change_notes_sha256": RAW_CHANGE_NOTES_SHA256,
        "read_only_tree_fingerprints": {
            "after": after,
            "before": before,
            "unchanged": True,
        },
        "semantic_findings": list(dict.fromkeys(semantics_01 + semantics_02)),
    }


def build_report(
    root: Path,
    solver_verifier: Callable[[Path, Path], None] = run_solver_verifier,
) -> dict[str, Any]:
    runtime = verify_runtime()
    events: list[str] = []
    source = verify_sidecar(
        Path(__file__).resolve(),
        current_source_sha(),
        "reconciliation",
        replace_suffix=True,
    )
    static_boundary = source_has_no_optimizer_or_solver_import(source)
    evidence, selected = verify_phase1(root, events, solver_verifier)
    prediction_bytes = frozen_prediction_bytes(selected["estimate"])
    events.append("fitted-prediction-frozen-and-hashed")
    fitted_truth = generated_truth_comparison(prediction_bytes)
    solver_completed = events.index("solver-semantic-recomputation-completed")
    track5 = verify_track5(root, prediction_bytes, events)
    cad_opened = events.index("cad-evidence-paths-first-opened")
    if solver_completed >= cad_opened:
        fail("CAD evidence opened before solver verification completed")
    evidence_classes = [
        {
            "allowed_role": "oracle and constructed fit expectation",
            "class": "generated truth",
        },
        {
            "allowed_role": "fit input through explicit active factors",
            "class": "generated training",
        },
        {"allowed_role": "post-fit evaluation only", "class": "generated held-out"},
        {
            "allowed_role": "post-fit source-geometry reconciliation only",
            "class": "CAD evidence",
        },
        {
            "allowed_role": "future fit/evaluation evidence under a separate protocol",
            "class": "future captured observations",
        },
        {
            "allowed_role": "future independent dimensional comparison",
            "class": "future physical reference",
        },
    ]
    held_out_count = evidence["data_separation"]["held_out_count"]
    return {
        "cad_influence": {name: [] for name in INFLUENCE_CATEGORIES},
        "claim": "generated experiment reconciliation only",
        "evidence_classes": evidence_classes,
        "fitted_prediction": {
            "derivation": "verified noiseless-fixed-pose seven-shape estimate only",
            "frozen_before_cad_open": True,
            "immutable_artifact": "canonical JSON bytes",
            "sha256": sha256(prediction_bytes),
            "comparison_inputs": "fresh independent deserialization per comparison",
        },
        "fitted_to_generated_truth": fitted_truth,
        "format": FORMAT,
        "format_status": FORMAT_STATUS,
        "held_out_exclusion": {
            "callback_ids_seen": [],
            "count": held_out_count,
            "fit_or_tuning_use": False,
            "role": "post-fit evaluation only; not consumed by reconciliation",
        },
        "inputs": {
            "contract_logical_sha256": CONTRACT_SHA256,
            "generator_source_sha256": GENERATOR_SHA256,
            "reconciliation_source_sha256": current_source_sha(),
            "solver_evidence_sha256": SOLVER_EVIDENCE_SHA256,
            "solver_source_sha256": SOLVER_SHA256,
            "track5_tool_sha256": TRACK5_SHA256,
        },
        "integrity_order": {
            "cad_paths_first_opened_event_index": cad_opened,
            "events": events,
            "solver_verification_completed_event_index": solver_completed,
            "solver_verified_before_any_cad_path_opened": True,
        },
        "overall_outcome": "pass",
        "runtime": runtime,
        "selected_solver_result": selected,
        "static_boundary": static_boundary,
        "track5": track5,
    }


def current_source_sha() -> str:
    path = Path(__file__).resolve()
    sidecar = path.with_suffix(".sha256")
    try:
        checksum, filename = (
            sidecar.read_text(encoding="ascii").strip().split(maxsplit=1)
        )
    except (OSError, ValueError) as error:
        raise ReconciliationError(
            f"invalid reconciliation source sidecar: {error}"
        ) from error
    if filename != path.name or len(checksum) != 64:
        fail("invalid reconciliation source sidecar")
    return checksum


def verify_report(root: Path, evidence_path: Path) -> dict[str, Any]:
    retained = verify_sidecar(
        evidence_path,
        sha256(read_regular(evidence_path, "reconciliation evidence")),
        "reconciliation evidence",
    )
    value = parse_json(retained, "reconciliation evidence")
    recomputed = build_report(root)
    if value != recomputed:
        fail("retained reconciliation evidence does not match recomputation")
    return value


def generate_evidence_bytes(root: Path) -> bytes:
    return canonical_json(build_report(root.resolve()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    verify = commands.add_parser("verify-evidence")
    verify.add_argument("evidence", type=Path)
    verify.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    args = parser.parse_args()
    try:
        if args.command == "generate":
            sys.stdout.buffer.write(generate_evidence_bytes(args.root))
            return 0
        report = verify_report(args.root.resolve(), args.evidence)
        print(f"evidence: PASS ({report['claim']})")
        return 0
    except (
        OSError,
        ReconciliationError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
