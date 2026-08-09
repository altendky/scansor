#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = []
# ///
"""Offline trust-boundary tests for track5_onshape_cad_repro.py."""

from __future__ import annotations

import contextlib
import copy
import gzip
import importlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, final, override
from unittest import mock

if TYPE_CHECKING:
    from experiments import track5_onshape_cad_repro as tool
else:
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    tool = importlib.import_module("experiments.track5_onshape_cad_repro")


def current_tool_sha() -> str:
    assert tool.__file__ is not None
    return tool.sha256(Path(tool.__file__).read_bytes())


def tool_private(name: str) -> Any:
    return vars(tool)[name]


def cylinder(
    index: int, radius: float, z0: float, z1: float, x_max: float | None = None
) -> dict[str, Any]:
    return {
        "axis": {"direction": [0.0, 0.0, -1.0], "originM": [0.0, 0.0, z0]},
        "faceId": f"cylinder-{index}",
        "radiusM": radius,
        "surfaceType": "cylinder",
        "trim": {"xMaxM": x_max},
        "zBoundsM": [z0, z1],
    }


def axial(
    index: int,
    station: float | None,
    sign: float,
    radius_bounds: list[float],
    x_bounds: list[float] | None = None,
) -> dict[str, Any]:
    bounds = {"radius": radius_bounds}
    if x_bounds is not None:
        bounds["x"] = x_bounds
    return {
        "boundsM": bounds,
        "faceId": f"axial-{index}",
        "kind": "axial",
        "orientation": True,
        "outwardNormal": [0.0, 0.0, sign],
        "sourceNormal": [0.0, 0.0, sign],
        "stationM": station,
        "surfaceType": "plane",
    }


def datum() -> dict[str, Any]:
    return {
        "boundsM": {
            "x": [0.016, 0.016],
            "y": [-0.008246211251235319, 0.008246211251235319],
            "z": [0.02, 0.05],
        },
        "faceId": "datum-1",
        "kind": "datum",
        "orientation": False,
        "outwardNormal": [1.0, 0.0, 0.0],
        "sourceNormal": [-1.0, 0.0, 0.0],
        "stationM": None,
        "surfaceType": "plane",
    }


def probe_payload(variant: str, element_id: str) -> dict[str, Any]:
    asymmetric = variant == "asymmetric_datum_flat"
    faces = [
        cylinder(1, 0.012, 0.0, 0.02),
        cylinder(2, 0.018, 0.02, 0.05, 0.016 if asymmetric else None),
        cylinder(3, 0.014, 0.05, 0.08),
        axial(1, 0.0, -1.0, [0.0, 0.012]),
        axial(
            2,
            0.02,
            -1.0,
            [0.012, 0.018],
            [-0.018, 0.016] if asymmetric else None,
        ),
        axial(
            3,
            0.05,
            1.0,
            [0.014, 0.018],
            [-0.018, 0.016] if asymmetric else None,
        ),
        axial(4, 0.08, 1.0, [0.0, 0.014]),
    ]
    if asymmetric:
        faces.append(datum())
    return {
        "elementId": element_id,
        "frame": {"axis": "+Z", "handedness": "right", "lengthUnit": "m"},
        "probeVersion": "track5-stepped-rotational-v1-v1",
        "solids": [{"faces": faces, "solidId": f"solid-{variant}"}],
        "variant": variant,
    }


def fs_value(value: object) -> dict[str, Any]:
    prefix = "com.belmonttech.serialize.fsvalue."
    if isinstance(value, dict):
        return {
            "btType": prefix + "BTFSValueMap",
            "typeTag": "",
            "value": [
                {
                    "btType": "BTFSValueMapEntry-2077",
                    "key": fs_value(key),
                    "value": fs_value(child),
                }
                for key, child in value.items()
            ],
        }
    if isinstance(value, list):
        return {
            "btType": prefix + "BTFSValueArray",
            "typeTag": "",
            "value": [fs_value(child) for child in value],
        }
    if isinstance(value, str):
        return {"btType": prefix + "BTFSValueString", "typeTag": "", "value": value}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {
            "btType": prefix + "BTFSValueNumber",
            "typeTag": "",
            "value": float(value),
        }
    raise AssertionError(f"unsupported test FeatureScript value: {value!r}")


def fs_map_child(node: dict[str, Any], key: str) -> dict[str, Any]:
    for entry in node["value"]:
        if entry["key"]["value"] == key:
            return entry["value"]
    raise AssertionError(f"missing FeatureScript map key: {key}")


def official_geometry_responses(
    variant: str, element_id: str, part_id: str, microversion: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = probe_payload(variant, element_id)
    source_faces = payload["solids"][0]["faces"]
    body_faces: list[dict[str, Any]] = []
    body_edges: list[dict[str, Any]] = []
    fs_faces: list[dict[str, Any]] = []
    for face_index, source in enumerate(source_faces):
        if source["surfaceType"] == "cylinder":
            radius = source["radiusM"]
            z0, z1 = source["zBoundsM"]
            x_max = source["trim"]["xMaxM"]
            minimum = [-radius, -radius, z0]
            maximum = [radius if x_max is None else x_max, radius, z1]
            surface = {
                "axis": {"x": 0.0, "y": 0.0, "z": -1.0},
                "origin": {"x": 0.0, "y": 0.0, "z": 0.04},
                "radius": radius,
                "type": "CYLINDER",
            }
            fs_surface = {
                "coordSystem": {"zAxis": [0.0, 0.0, -1.0]},
                "radius": radius,
                "surfaceType": "CYLINDER",
            }
            tangent = {"normal": [1.0, 0.0, 0.0]}
            loops: list[dict[str, Any]] = []
        else:
            bounds = source["boundsM"]
            if source["kind"] == "axial":
                outer = bounds["radius"][1]
                x_bounds = bounds.get("x", [-outer, outer])
                station = source["stationM"]
                minimum = [x_bounds[0], -outer, station]
                maximum = [x_bounds[1], outer, station]
                loops = []
                for radius_index, radius in enumerate(
                    sorted({value for value in bounds["radius"] if value > 0.0})
                ):
                    edge_id = f"edge-{face_index}-{radius_index}"
                    body_edges.append(
                        {
                            "curve": {"radius": radius, "type": "CIRCLE"},
                            "id": edge_id,
                        }
                    )
                    loops.append({"coedges": [{"edgeId": edge_id}]})
            else:
                minimum = [bounds[axis][0] for axis in ("x", "y", "z")]
                maximum = [bounds[axis][1] for axis in ("x", "y", "z")]
                loops = [{"coedges": []}]
            surface = {
                "normal": dict(
                    zip(("x", "y", "z"), source["sourceNormal"], strict=True)
                ),
                "origin": {"x": 0.0, "y": 0.0, "z": source["stationM"] or 0.0},
                "type": "PLANE",
            }
            fs_surface = {"surfaceType": "PLANE"}
            tangent = {"normal": source["outwardNormal"]}
        box = {
            "minCorner": dict(zip(("x", "y", "z"), minimum, strict=True)),
            "maxCorner": dict(zip(("x", "y", "z"), maximum, strict=True)),
        }
        body_faces.append(
            {
                "box": box,
                "id": source["faceId"],
                "loops": loops,
                "orientation": source.get("orientation", True),
                "surface": surface,
            }
        )
        fs_faces.append(
            {
                "bounds": {"minCorner": minimum, "maxCorner": maximum},
                "id": source["faceId"],
                "surface": fs_surface,
                "tangent": tangent,
            }
        )
    body = {
        "bodies": [
            {
                "edges": body_edges,
                "faces": body_faces,
                "id": part_id,
                "type": "SOLID",
            }
        ],
        "btType": "BTExportModelBodiesResponse-734",
        "errorEnum": "NO_ERROR",
        "microversionId": {
            "btType": "BTMicroversionId-366",
            "theId": microversion,
        },
    }
    probe = {
        "btType": "BTFeatureScriptEvalResponse-1859",
        "console": "",
        "libraryVersion": 3029,
        "microversionSkew": False,
        "notices": [],
        "rejectMicroversionSkew": False,
        "result": fs_value(
            {
                "bodyCount": 1,
                "faces": fs_faces,
                "probeVersion": "track5-stepped-rotational-v1-v1",
            }
        ),
        "serializationVersion": "1.2.21",
        "sourceMicroversion": microversion,
    }
    return body, probe


def run_identity(run_id: str) -> dict[str, Any]:
    if run_id == tool.RUN_01_ID:
        return {
            "elements": dict(tool.RUN_01_ELEMENTS),
            "id": run_id,
            "kind": "backfill",
            "microversion_id": tool.RUN_01_MICROVERSION_ID,
            "version_id": tool.RUN_01_VERSION_ID,
            "workspace_id": tool.MAIN_WORKSPACE_ID,
        }
    return {
        "elements": dict(tool.RUN_02_ELEMENTS),
        "id": tool.RUN_ID,
        "kind": "captured",
        "microversion_id": tool.RUN_02_MICROVERSION_ID,
        "version_id": tool.RUN_02_VERSION_ID,
        "workspace_id": tool.RUN_02_WORKSPACE_ID,
    }


def part_ids(run_id: str) -> dict[str, str]:
    prefix = "a" if run_id == tool.RUN_01_ID else "7"
    return {"axisymmetric": prefix * 24, "asymmetric_datum_flat": "8" * 24}


def response_for(
    spec: tool.OperationSpec, run: dict[str, Any], parts: dict[str, str]
) -> Any:
    microversion = run["microversion_id"]
    if spec.kind == "version":
        return {
            "documentId": tool.DOCUMENT_ID,
            "id": run["version_id"],
            "metadataWorkspaceId": "f" * 24,
            "microversion": microversion,
            "name": (
                tool.RUN_01_VERSION_NAME
                if run["id"] == tool.RUN_01_ID
                else tool.VERSION_NAME
            ),
            "parent": tool.START_VERSION_ID,
            "parents": [{"id": tool.START_VERSION_ID, "name": "Start"}],
        }
    if spec.kind == "elements":
        names = (
            tool.RUN_01_VARIANT_NAMES
            if run["id"] == tool.RUN_01_ID
            else tool.RUN_02_VARIANT_NAMES
        )
        return [
            {
                "elementType": "PARTSTUDIO",
                "id": run["elements"][variant],
                "name": names[variant],
            }
            for variant in tool.EXPECTED_VARIANTS
        ]
    variant = spec.variant
    assert variant is not None
    if spec.kind == "parts":
        return [
            {
                "bodyType": "solid",
                "elementId": run["elements"][variant],
                "microversionId": microversion,
                "partId": parts[variant],
            }
        ]
    if spec.kind == "body":
        return official_geometry_responses(
            variant,
            run["elements"][variant],
            parts[variant],
            microversion,
        )[0]
    return official_geometry_responses(
        variant, run["elements"][variant], parts[variant], microversion
    )[1]


def request_for(
    spec: tool.OperationSpec,
    run: dict[str, Any],
    parts: dict[str, str],
    tool_sha: str,
) -> dict[str, Any]:
    variant = spec.variant
    element_id = run["elements"].get(variant) if variant is not None else None
    part_id = parts[variant] if spec.kind == "body" and variant is not None else None
    return {
        "body": tool_private("_expected_body")(spec, run["microversion_id"]),
        "document_id": tool.DOCUMENT_ID,
        "element_id": element_id,
        "endpoint": spec.endpoint,
        "expected_tool_sha256": tool_sha,
        "featurescript_source_sha256": (
            tool.FEATURESCRIPT_SHA256 if spec.kind == "probe" else None
        ),
        "method": spec.method,
        "microversion_id": run["microversion_id"],
        "operation_name": spec.name,
        "part_id": part_id,
        "path": tool_private("_exact_path")(
            spec,
            version_id=run["version_id"],
            microversion_id=run["microversion_id"],
            element_id=element_id,
            part_id=part_id,
        ),
        "query": tool_private("_exact_query")(spec),
        "run_role": run["kind"],
        "source_pins": tool.SOURCE_PINS,
        "variant": spec.variant,
        "version_id": run["version_id"],
        "workspace_id": run["workspace_id"],
    }


def write_artifact(
    root: Path, relative: str, raw: bytes, *, provenance: dict[str, Any]
) -> dict[str, Any]:
    stored = tool.deterministic_gzip(raw, compresslevel=provenance["compresslevel"])
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(stored)
    return tool.artifact_descriptor(raw, stored, relative)


def write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    data = tool.canonical_json(manifest)
    _ = (root / "manifest.json").write_bytes(data)
    _ = (root / "manifest.sha256").write_bytes(
        f"{tool.sha256(data)}  manifest.json\n".encode("ascii")
    )


def write_run_derived(root: Path, tool_sha: str) -> None:
    manifest, raw, parsed, _ = tool_private("_verify_raw_evidence")(root, tool_sha)
    normalized = tool_private("_replay_verified")(manifest, raw, parsed)
    label = (
        "run-01-backfill-vs-truth"
        if manifest["run"]["id"] == tool.RUN_01_ID
        else "run-02-vs-truth"
    )
    report = tool.compare_to_truth(normalized, label)
    (root / "normalized").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    _ = (root / "normalized/normalized.json").write_bytes(
        tool.canonical_json(normalized)
    )
    _ = (
        root / tool_private("_run_truth_report_name")(manifest["run"]["id"])
    ).write_bytes(tool.canonical_json(report))


def build_evidence(root: Path, run_id: str, tool_sha: str) -> dict[str, Any]:
    run = run_identity(run_id)
    parts = part_ids(run_id)
    provenance = tool.current_gzip_provenance()
    operations: list[dict[str, Any]] = []
    log_lines: list[bytes] = []
    total = 0
    for order, spec in enumerate(tool.OPERATION_SPECS, 1):
        request = request_for(spec, run, parts, tool_sha)
        response = response_for(spec, run, parts)
        request_descriptor = write_artifact(
            root,
            f"requests/{order:02d}-{spec.name}.json.gz",
            tool.canonical_json(request),
            provenance=provenance,
        )
        response_descriptor = write_artifact(
            root,
            f"responses/{order:02d}-{spec.name}.json.gz",
            tool.canonical_json(response),
            provenance=provenance,
        )
        total += request_descriptor["stored_size"] + response_descriptor["stored_size"]
        response_metadata = {
            "headers": {"content-type": "application/json"},
            "library_version": 3029 if spec.kind == "probe" else None,
            "microversion_skew": False,
            "response_microversion": run["microversion_id"],
            "serialization_version": "1.2.21" if spec.kind == "probe" else None,
            "status": 200,
            "timestamp": f"2026-07-28T12:00:{order:02d}+00:00",
        }
        operation = {
            "endpoint": spec.endpoint,
            "name": spec.name,
            "order": order,
            "predecessor_microversion": run["microversion_id"],
            "request": request_descriptor,
            "response": response_descriptor,
            "response_metadata": response_metadata,
        }
        operations.append(operation)
        log_lines.append(
            tool.canonical_json(
                {
                    "endpoint": spec.endpoint,
                    "expected_tool_sha256": tool_sha,
                    "name": spec.name,
                    "order": order,
                    "predecessor_microversion": run["microversion_id"],
                    "response_microversion": run["microversion_id"],
                    "status": 200,
                    "timestamp": response_metadata["timestamp"],
                }
            )
        )
    manifest: dict[str, Any] = {
        "capture_metadata": {
            "captured_at": "2026-07-28T12:00:00+00:00",
            "gzip": provenance,
            "integrity_scope": tool.INTEGRITY_SCOPE,
            "library_version": 3029,
            "serialization_version": "1.2.21",
            "tool": {
                "implementation": "CPython",
                "python": "test",
                "source_sha256": tool_sha,
            },
        },
        "compressed_raw_envelope_size": total,
        "expected_tool_sha256": tool_sha,
        "format": tool.FORMAT,
        "format_status": tool.FORMAT_STATUS,
        "operations": operations,
        "run": run,
        "source_pins": tool.SOURCE_PINS,
    }
    _ = (root / "operation-log.jsonl").write_bytes(b"".join(log_lines))
    write_manifest(root, manifest)
    write_run_derived(root, tool_sha)
    return manifest


def write_capture_inputs(root: Path, run_id: str) -> None:
    root.mkdir()
    run = run_identity(run_id)
    parts = part_ids(run_id)
    for spec in tool.OPERATION_SPECS:
        _ = (root / f"{spec.name}.json").write_bytes(
            tool.canonical_json(response_for(spec, run, parts))
        )


def replace_artifact(
    root: Path,
    manifest: dict[str, Any],
    operation_index: int,
    side: str,
    raw: bytes,
) -> None:
    operation = manifest["operations"][operation_index]
    descriptor = operation[side]
    old_size = descriptor["stored_size"]
    operation[side] = write_artifact(
        root,
        descriptor["path"],
        raw,
        provenance=manifest["capture_metadata"]["gzip"],
    )
    manifest["compressed_raw_envelope_size"] += (
        operation[side]["stored_size"] - old_size
    )
    write_manifest(root, manifest)


def build_suite(
    suite: Path, run_01: Path, run_02: Path, tool_sha: str
) -> dict[str, Any]:
    left = tool.replay(run_01, tool_sha)
    right = tool.replay(run_02, tool_sha)
    left_hashes = {
        item["name"]: item["raw_response_sha256"] for item in left["response_metadata"]
    }
    right_hashes = {
        item["name"]: item["raw_response_sha256"] for item in right["response_metadata"]
    }
    notes = {
        name: {
            "classification": "expected revision-ID changes",
            "detail": "hash-pinned synthetic fixture revision change",
            "left_raw_sha256": left_hashes[name],
            "right_raw_sha256": right_hashes[name],
        }
        for name in left_hashes
        if left_hashes[name] != right_hashes[name]
    }
    suite.mkdir()
    (suite / "reports").mkdir()
    _ = (suite / "raw-change-notes.json").write_bytes(tool.canonical_json(notes))
    suite_manifest = {
        "expected_tool_sha256": tool_sha,
        "format": tool.SUITE_FORMAT,
        "format_status": tool.FORMAT_STATUS,
        "integrity_scope": tool.INTEGRITY_SCOPE,
        "run_manifests": {
            tool.RUN_01_ID: tool.sha256((run_01 / "manifest.json").read_bytes()),
            tool.RUN_ID: tool.sha256((run_02 / "manifest.json").read_bytes()),
        },
        "source_pins": tool.SOURCE_PINS,
    }
    manifest_bytes = tool.canonical_json(suite_manifest)
    _ = (suite / "suite-manifest.json").write_bytes(manifest_bytes)
    _ = (suite / "suite-manifest.sha256").write_bytes(
        f"{tool.sha256(manifest_bytes)}  suite-manifest.json\n".encode("ascii")
    )
    reports = {
        "run-01-backfill-vs-truth.json": tool.compare_to_truth(
            left, "run-01-backfill-vs-truth"
        ),
        "run-02-vs-truth.json": tool.compare_to_truth(right, "run-02-vs-truth"),
        "run-01-vs-run-02.json": tool.compare_runs(
            left, right, "run-01-vs-run-02", notes
        ),
    }
    for filename, report in reports.items():
        _ = (suite / "reports" / filename).write_bytes(tool.canonical_json(report))
    return suite_manifest


@final
class EvidenceTest(unittest.TestCase):
    temporary: Any = None
    base = Path()
    tool_sha = ""
    verify_patch: Any = None
    run_02 = Path()
    manifest: Any = None

    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.tool_sha = current_tool_sha()
        self.verify_patch = mock.patch.object(
            tool, "verify_tool_source", return_value=self.tool_sha
        )
        _ = self.verify_patch.start()
        self.run_02 = self.base / "run-02"
        self.run_02.mkdir()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)

    @override
    def tearDown(self) -> None:
        self.verify_patch.stop()
        self.temporary.cleanup()

    def verify(self) -> dict[str, Any]:
        return tool.verify_evidence(self.run_02, self.tool_sha)

    def operation_raw(self, index: int, side: str) -> Any:
        operation = self.manifest["operations"][index]
        stored = (self.run_02 / operation[side]["path"]).read_bytes()
        return json.loads(gzip.decompress(stored))

    def test_run_and_suite_recompute_all_derived_bytes(self) -> None:
        self.assertEqual(self.verify()["run"]["id"], tool.RUN_ID)
        run_01 = self.base / "run-01"
        run_01.mkdir()
        _ = build_evidence(run_01, tool.RUN_01_ID, self.tool_sha)
        suite = self.base / "suite"
        _ = build_suite(suite, run_01, self.run_02, self.tool_sha)
        self.assertEqual(
            tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)["format"],
            tool.SUITE_FORMAT,
        )

    def test_production_capture_and_suite_assemblers(self) -> None:
        run_01_inputs = self.base / "run-01-inputs"
        run_02_inputs = self.base / "run-02-inputs"
        write_capture_inputs(run_01_inputs, tool.RUN_01_ID)
        write_capture_inputs(run_02_inputs, tool.RUN_ID)
        run_01 = self.base / "assembled-run-01"
        run_02 = self.base / "assembled-run-02"
        suite = self.base / "assembled-suite"
        _ = tool.assemble_capture(run_01_inputs, run_01, tool.RUN_01_ID, self.tool_sha)
        _ = tool.assemble_capture(run_02_inputs, run_02, tool.RUN_ID, self.tool_sha)
        _ = tool.assemble_suite(run_01, run_02, suite, self.tool_sha)
        self.assertEqual(
            tool.verify_suite(run_01, run_02, suite, self.tool_sha)["format"],
            tool.SUITE_FORMAT,
        )

    def test_offline_replay_uses_no_network(self) -> None:
        with mock.patch.object(
            socket, "socket", side_effect=AssertionError("network used")
        ):
            first = tool.canonical_json(tool.replay(self.run_02, self.tool_sha))
            second = tool.canonical_json(tool.replay(self.run_02, self.tool_sha))
        self.assertEqual(first, second)

    def test_missing_stale_extra_and_secret_derived_rejected(self) -> None:
        normalized = self.run_02 / "normalized/normalized.json"
        original = normalized.read_bytes()
        normalized.unlink()
        with self.assertRaisesRegex(tool.EvidenceError, "files mismatch"):
            _ = self.verify()
        _ = normalized.write_bytes(
            original.replace(b'"format":', b'"extra":1,"format":')
        )
        with self.assertRaisesRegex(tool.EvidenceError, "canonical|recomputed output"):
            _ = self.verify()
        _ = normalized.write_bytes(original)
        _ = (self.run_02 / "reports/extra.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(tool.EvidenceError, "unexpected"):
            _ = self.verify()
        (self.run_02 / "reports/extra.json").unlink()
        value = json.loads(original)
        value["apiKey"] = "redacted"
        _ = normalized.write_bytes(tool.canonical_json(value))
        with self.assertRaisesRegex(tool.EvidenceError, "secret-bearing"):
            _ = self.verify()

    def test_wrong_role_report_and_suite_report_in_run_rejected(self) -> None:
        truth = self.run_02 / "reports/run-02-vs-truth.json"
        _ = truth.rename(self.run_02 / "reports/run-01-backfill-vs-truth.json")
        with self.assertRaisesRegex(tool.EvidenceError, "files mismatch"):
            _ = self.verify()
        truth = self.run_02 / "reports/run-01-backfill-vs-truth.json"
        _ = truth.rename(self.run_02 / "reports/run-02-vs-truth.json")
        _ = (self.run_02 / "reports/run-01-vs-run-02.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(tool.EvidenceError, "unexpected"):
            _ = self.verify()

    def test_suite_missing_stale_extra_and_secret_reports_rejected(self) -> None:
        run_01 = self.base / "run-01"
        run_01.mkdir()
        _ = build_evidence(run_01, tool.RUN_01_ID, self.tool_sha)
        suite = self.base / "suite"
        _ = build_suite(suite, run_01, self.run_02, self.tool_sha)
        report = suite / "reports/run-01-vs-run-02.json"
        original = report.read_bytes()
        report.unlink()
        with self.assertRaisesRegex(tool.EvidenceError, "suite files mismatch"):
            _ = tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)
        _ = report.write_bytes(original.replace(b'"format":', b'"stale":1,"format":'))
        with self.assertRaisesRegex(tool.EvidenceError, "canonical|recomputed output"):
            _ = tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)
        _ = report.write_bytes(original)
        _ = (suite / "extra.json").write_bytes(b"{}\n")
        with self.assertRaisesRegex(tool.EvidenceError, "unexpected"):
            _ = tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)
        (suite / "extra.json").unlink()
        notes = suite / "raw-change-notes.json"
        _ = notes.write_bytes(tool.canonical_json({"accessToken": "redacted"}))
        with self.assertRaisesRegex(tool.EvidenceError, "secret-bearing"):
            _ = tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)

    def test_expected_tool_and_generator_anchors_rejected(self) -> None:
        with self.assertRaisesRegex(tool.EvidenceError, "expected tool"):
            _ = tool.verify_evidence(self.run_02, "f" * 64)
        self.manifest["source_pins"] = dict(tool.SOURCE_PINS) | {
            "generator_version": "1.0.6"
        }
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "generator source or contract"):
            _ = self.verify()

    def test_sidecar_integrity_not_authorship_statement_required(self) -> None:
        self.manifest["capture_metadata"]["integrity_scope"] = "signed"
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "integrity/authorship"):
            _ = self.verify()

    def test_official_version_workspace_and_microversion_anchor_rejected(self) -> None:
        response = self.operation_raw(0, "response")
        for field in ("id", "microversion"):
            with self.subTest(field=field):
                changed = dict(response)
                changed[field] = "f" * 24
                replace_artifact(
                    self.run_02,
                    self.manifest,
                    0,
                    "response",
                    tool.canonical_json(changed),
                )
                with self.assertRaisesRegex(tool.EvidenceError, "version response"):
                    _ = self.verify()
                self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)

    def test_version_anchor_requires_explicit_start_and_run01_parentage(self) -> None:
        request = self.operation_raw(0, "request")
        self.assertEqual(request["query"], {"linkDocumentId": "", "parents": True})
        response = self.operation_raw(0, "response")
        response["parents"] = [{"id": "f" * 24}]
        replace_artifact(
            self.run_02,
            self.manifest,
            0,
            "response",
            tool.canonical_json(response),
        )
        with self.assertRaisesRegex(tool.EvidenceError, "identity and ancestry"):
            _ = self.verify()

        run_01 = self.base / "run-01-parent"
        run_01.mkdir()
        run_01_manifest = build_evidence(run_01, tool.RUN_01_ID, self.tool_sha)
        run_01_operation = run_01_manifest["operations"][0]
        run_01_response = json.loads(
            gzip.decompress(
                (run_01 / run_01_operation["response"]["path"]).read_bytes()
            )
        )
        self.assertEqual(
            run_01_response["parents"],
            [{"id": tool.START_VERSION_ID, "name": "Start"}],
        )
        run_01_response["parents"] = []
        replace_artifact(
            run_01,
            run_01_manifest,
            0,
            "response",
            tool.canonical_json(run_01_response),
        )
        with self.assertRaisesRegex(tool.EvidenceError, "identity and ancestry"):
            _ = tool.verify_evidence(run_01, self.tool_sha)

    def test_walk_files_detects_parent_and_leaf_replacement(self) -> None:
        root = self.base / "walk-replaced"
        child = root / "child"
        child.mkdir(parents=True)
        _ = (child / "file.txt").write_text("ok", encoding="ascii")
        original_open = os.open

        parent_replaced = False

        def replace_parent_once(
            path: str | Path,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal parent_replaced
            if path == "child" and flags & os.O_DIRECTORY and not parent_replaced:
                parent_replaced = True
                _ = child.rename(root / "child-old")
                _ = (root / "child").write_text("replacement", encoding="ascii")
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            mock.patch.object(os, "open", side_effect=replace_parent_once),
            self.assertRaisesRegex(tool.EvidenceError, "directory was replaced"),
        ):
            _ = tool_private("_walk_files")(root, tool.MAX_RUN_FILES)

        root = self.base / "walk-leaf"
        root.mkdir()
        leaf = root / "leaf.txt"
        _ = leaf.write_text("ok", encoding="ascii")
        leaf_replaced = False

        def replace_leaf_once(
            path: str | Path,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal leaf_replaced
            if path == "leaf.txt" and not flags & os.O_DIRECTORY and not leaf_replaced:
                leaf_replaced = True
                _ = leaf.rename(root / "leaf-old.txt")
                (root / "leaf.txt").mkdir()
            return original_open(path, flags, mode, dir_fd=dir_fd)

        with (
            mock.patch.object(os, "open", side_effect=replace_leaf_once),
            self.assertRaisesRegex(tool.EvidenceError, "leaf was replaced"),
        ):
            _ = tool_private("_walk_files")(root, tool.MAX_RUN_FILES)

    def test_official_element_inventory_forgery_rejected(self) -> None:
        response = self.operation_raw(1, "response")
        response[0]["id"] = "f" * 24
        replace_artifact(
            self.run_02,
            self.manifest,
            1,
            "response",
            tool.canonical_json(response),
        )
        with self.assertRaisesRegex(tool.EvidenceError, "element inventory"):
            _ = self.verify()

    def test_duplicate_official_element_inventory_rejected(self) -> None:
        response = self.operation_raw(1, "response")
        response[1] = copy.deepcopy(response[0])
        replace_artifact(
            self.run_02,
            self.manifest,
            1,
            "response",
            tool.canonical_json(response),
        )
        with self.assertRaisesRegex(tool.EvidenceError, "duplicate variant"):
            _ = self.verify()

    def test_swapped_element_part_probe_and_body_pairings_rejected(self) -> None:
        request = self.operation_raw(2, "request")
        request["element_id"] = self.manifest["run"]["elements"][
            "asymmetric_datum_flat"
        ]
        replace_artifact(
            self.run_02, self.manifest, 2, "request", tool.canonical_json(request)
        )
        with self.assertRaisesRegex(tool.EvidenceError, "element/variant"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        body_request = self.operation_raw(3, "request")
        body_request["part_id"] = "8" * 24
        body_request["path"] = tool_private("_exact_path")(
            tool.OPERATION_SPECS[3],
            version_id=self.manifest["run"]["version_id"],
            microversion_id=self.manifest["run"]["microversion_id"],
            element_id=self.manifest["run"]["elements"]["axisymmetric"],
            part_id="8" * 24,
        )
        replace_artifact(
            self.run_02, self.manifest, 3, "request", tool.canonical_json(body_request)
        )
        with self.assertRaisesRegex(
            tool.EvidenceError, "cross-paired|wrong face inventory"
        ):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        probe = self.operation_raw(4, "response")
        probe["sourceMicroversion"] = "f" * 24
        replace_artifact(
            self.run_02, self.manifest, 4, "response", tool.canonical_json(probe)
        )
        with self.assertRaisesRegex(
            tool.EvidenceError, "cross-paired|wrong face inventory"
        ):
            _ = self.verify()

    def test_exact_path_query_body_and_order_contracts_rejected(self) -> None:
        request = self.operation_raw(4, "request")
        mutations = (
            ("path", request["path"] + "/suffix"),
            ("query", {}),
            ("body", dict(request["body"]) | {"extra": True}),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(request)
                changed[field] = value
                replace_artifact(
                    self.run_02,
                    self.manifest,
                    4,
                    "request",
                    tool.canonical_json(changed),
                )
                with self.assertRaisesRegex(
                    tool.EvidenceError, "exact method/path/query/body"
                ):
                    _ = self.verify()
                self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        self.manifest["operations"][0], self.manifest["operations"][1] = (
            self.manifest["operations"][1],
            self.manifest["operations"][0],
        )
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "ordering/name"):
            _ = self.verify()

    def test_predecessor_and_operation_count_rejected(self) -> None:
        self.manifest["operations"][2]["predecessor_microversion"] = "f" * 24
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "predecessor"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        self.manifest["operations"].pop()
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "operation count"):
            _ = self.verify()

    def test_manifest_log_file_count_and_descriptor_bounds(self) -> None:
        manifest = self.run_02 / "manifest.json"
        _ = manifest.write_bytes(b" " * (tool.MAX_MANIFEST_BYTES + 1))
        with self.assertRaisesRegex(tool.EvidenceError, "exceeds bound"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        _ = (self.run_02 / "operation-log.jsonl").write_bytes(
            b" " * (tool.MAX_OPERATION_LOG_BYTES + 1)
        )
        with self.assertRaisesRegex(tool.EvidenceError, "exceeds bound"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        for index in range(tool.MAX_RUN_FILES + 1):
            _ = (self.run_02 / f"extra-{index}").write_bytes(b"")
        with self.assertRaisesRegex(tool.EvidenceError, "file count"):
            _ = self.verify()
        self.temporary.cleanup()
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.run_02 = self.base / "run-02"
        self.run_02.mkdir()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        self.manifest["operations"][0]["response"]["stored_size"] = (
            tool.MAX_STORED_ENTITY_BYTES + 1
        )
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "expected integer"):
            _ = self.verify()

    def test_gzip_hash_crc_trailing_concatenated_and_cross_runtime(self) -> None:
        descriptor = self.manifest["operations"][0]["response"]
        path = self.run_02 / descriptor["path"]
        original = path.read_bytes()
        path.write_bytes(original[:-1])
        with self.assertRaisesRegex(
            tool.EvidenceError, "initial stored size mismatch|stored size or SHA"
        ):
            _ = self.verify()
        path.write_bytes(original + original)
        descriptor["stored_size"] = len(original) * 2
        descriptor["stored_sha256"] = tool.sha256(original + original)
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "trailing gzip"):
            _ = self.verify()
        raw = b'{"cross":"runtime"}\n'
        stored = gzip.compress(raw, mtime=0)
        alternate = dict(tool.current_gzip_provenance())
        alternate["xfl"] = stored[8]
        alternate["os_byte"] = stored[9]
        temp = self.base / "alternate.gz"
        _ = temp.write_bytes(stored)
        result = tool_private("_stream_gzip_artifact")(
            temp,
            tool.artifact_descriptor(raw, stored, "alternate.gz"),
            alternate,
            "alternate",
        )
        self.assertEqual(result, raw)
        corrupt = bytearray(stored)
        corrupt[-8] ^= 1
        _ = temp.write_bytes(corrupt)
        bad = tool.artifact_descriptor(raw, bytes(corrupt), "alternate.gz")
        with self.assertRaisesRegex(tool.EvidenceError, "invalid gzip"):
            _ = tool_private("_stream_gzip_artifact")(temp, bad, alternate, "alternate")

    def test_stored_raw_hash_and_same_runtime_vector_mismatch_rejected(self) -> None:
        self.manifest["operations"][0]["response"]["stored_sha256"] = "f" * 64
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "stored size or SHA"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        self.manifest["operations"][0]["response"]["raw_sha256"] = "f" * 64
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "raw size or SHA"):
            _ = self.verify()
        self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)
        self.manifest["capture_metadata"]["gzip"]["same_runtime_vector_sha256"] = (
            "f" * 64
        )
        write_manifest(self.run_02, self.manifest)
        with self.assertRaisesRegex(tool.EvidenceError, "same-runtime gzip vector"):
            _ = self.verify()

    def test_decompressed_bound_checked_before_allocation(self) -> None:
        raw = b"x" * 1024
        stored = tool.deterministic_gzip(raw)
        path = self.base / "bounded.gz"
        _ = path.write_bytes(stored)
        descriptor = tool.artifact_descriptor(raw, stored, "bounded.gz")
        descriptor["raw_size"] = 10
        with self.assertRaisesRegex(tool.EvidenceError, "decompressed data exceeds"):
            _ = tool_private("_stream_gzip_artifact")(
                path, descriptor, tool.current_gzip_provenance(), "bounded"
            )

    def test_gzip_initial_size_mismatch_and_post_open_growth_rejected(self) -> None:
        raw = b'{"growth":"bounded"}\n'
        stored = tool.deterministic_gzip(raw)
        path = self.base / "growth.gz"
        _ = path.write_bytes(stored)
        descriptor = tool.artifact_descriptor(raw, stored, "growth.gz")
        wrong_size = dict(descriptor) | {"stored_size": len(stored) + 1}
        with self.assertRaisesRegex(tool.EvidenceError, "initial stored size mismatch"):
            _ = tool_private("_stream_gzip_artifact")(
                path, wrong_size, tool.current_gzip_provenance(), "growth"
            )
        original_open = tool_private("_open_bounded")

        def open_then_grow(
            opened_path: Path, maximum: int, label: str
        ) -> tuple[object, int]:
            stream, size = original_open(opened_path, maximum, label)
            with opened_path.open("ab") as writer:
                _ = writer.write(b"x")
            return stream, size

        with (
            mock.patch.object(tool, "_open_bounded", side_effect=open_then_grow),
            self.assertRaisesRegex(tool.EvidenceError, "grew after open"),
        ):
            _ = tool_private("_stream_gzip_artifact")(
                path, descriptor, tool.current_gzip_provenance(), "growth"
            )

    def test_tree_entry_depth_and_json_nesting_bounds(self) -> None:
        for index in range(tool.MAX_TREE_ENTRIES + 1):
            (self.run_02 / f"empty-{index}").mkdir()
        with self.assertRaisesRegex(tool.EvidenceError, "tree entry count"):
            _ = self.verify()
        deep_root = self.base / "deep"
        deep_root.mkdir()
        current = deep_root
        for index in range(tool.MAX_TREE_DEPTH + 2):
            current /= str(index)
            current.mkdir()
        with self.assertRaisesRegex(tool.EvidenceError, "tree depth"):
            _ = tool_private("_walk_files")(deep_root, tool.MAX_RUN_FILES)
        nested = "[" * 110 + "0" + "]" * 110
        with self.assertRaisesRegex(tool.EvidenceError, "nesting"):
            tool.parse_json(nested.encode(), "nested", canonical=False)

    def test_duplicate_invalid_utf8_nonfinite_and_secrets_rejected(self) -> None:
        invalid_values = (
            (b'{"x":1,"x":2}\n', "duplicate"),
            (b"\xff", "UTF-8"),
            (b'{"x":NaN}\n', "non-finite"),
            (tool.canonical_json({"accessToken": "redacted"}), "secret-bearing"),
        )
        for raw, message in invalid_values:
            with self.subTest(message=message):
                replace_artifact(self.run_02, self.manifest, 0, "response", raw)
                with self.assertRaisesRegex(tool.EvidenceError, message):
                    _ = self.verify()
                self.manifest = build_evidence(self.run_02, tool.RUN_ID, self.tool_sha)

    def test_reordered_faces_normalize_identically(self) -> None:
        original = tool.replay(self.run_02, self.tool_sha)
        response = self.operation_raw(4, "response")
        fs_map_child(response["result"], "faces")["value"].reverse()
        replace_artifact(
            self.run_02, self.manifest, 4, "response", tool.canonical_json(response)
        )
        body = self.operation_raw(3, "response")
        body["bodies"][0]["faces"].reverse()
        replace_artifact(
            self.run_02, self.manifest, 3, "response", tool.canonical_json(body)
        )
        write_run_derived(self.run_02, self.tool_sha)
        reordered = tool.replay(self.run_02, self.tool_sha)
        for value in (original, reordered):
            value.pop("response_metadata")
            for variant in value["variants"]:
                variant.pop("raw_response_sha256")
        self.assertEqual(original, reordered)

    def test_plane_station_and_orientation_semantics_rejected(self) -> None:
        response = {"result": {"value": probe_payload("axisymmetric", "e" * 24)}}
        faces = response["result"]["value"]["solids"][0]["faces"]
        faces[3]["stationM"] = None
        with self.assertRaisesRegex(tool.EvidenceError, "require stations"):
            _ = tool.normalize_probe_response(
                response, element_id="e" * 24, raw_sha256="a" * 64
            )
        response = {
            "result": {"value": probe_payload("asymmetric_datum_flat", "e" * 24)}
        }
        datum_face = response["result"]["value"]["solids"][0]["faces"][-1]
        datum_face["stationM"] = 0.02
        with self.assertRaisesRegex(tool.EvidenceError, "prohibit"):
            _ = tool.normalize_probe_response(
                response, element_id="e" * 24, raw_sha256="a" * 64
            )
        response = {
            "result": {"value": probe_payload("asymmetric_datum_flat", "e" * 24)}
        }
        response["result"]["value"]["solids"][0]["faces"][-1]["outwardNormal"] = [
            -1.0,
            0.0,
            0.0,
        ]
        with self.assertRaisesRegex(tool.EvidenceError, "inconsistent"):
            _ = tool.normalize_probe_response(
                response, element_id="e" * 24, raw_sha256="a" * 64
            )

    def test_asymmetric_axial_full_bounds_required_by_truth_and_cross_run(self) -> None:
        right = tool.replay(self.run_02, self.tool_sha)
        asymmetric = next(
            variant
            for variant in right["variants"]
            if variant["variant"] == "asymmetric_datum_flat"
        )
        axial_faces = [
            plane for plane in asymmetric["planes"] if plane["kind"] == "axial"
        ]
        self.assertEqual(axial_faces[1]["bounds_m"]["x"], [-0.018, 0.016])
        self.assertEqual(axial_faces[2]["bounds_m"]["x"], [-0.018, 0.016])

        missing = copy.deepcopy(right)
        missing_asymmetric = next(
            variant
            for variant in missing["variants"]
            if variant["variant"] == "asymmetric_datum_flat"
        )
        missing_axial = [
            plane for plane in missing_asymmetric["planes"] if plane["kind"] == "axial"
        ]
        missing_axial[1]["bounds_m"].pop("x")
        self.assertEqual(
            tool.compare_to_truth(missing, "missing-x")["outcome"], "failure"
        )

        altered = copy.deepcopy(right)
        altered_asymmetric = next(
            variant
            for variant in altered["variants"]
            if variant["variant"] == "asymmetric_datum_flat"
        )
        altered_axial = [
            plane for plane in altered_asymmetric["planes"] if plane["kind"] == "axial"
        ]
        altered_axial[2]["bounds_m"]["x"][1] += 2 * tool.LINEAR_TOLERANCE_M
        self.assertEqual(
            tool.compare_to_truth(altered, "altered-x")["outcome"], "failure"
        )

        left = copy.deepcopy(right)
        left["run"] = run_identity(tool.RUN_01_ID)
        self.assertEqual(
            tool.compare_runs(left, missing, "cross-run-missing")["outcome"],
            "failure",
        )
        self.assertEqual(
            tool.compare_runs(left, altered, "cross-run-altered")["outcome"],
            "failure",
        )

    def test_suite_manifest_run_hash_mutation_rejected(self) -> None:
        run_01 = self.base / "run-01"
        run_01.mkdir()
        _ = build_evidence(run_01, tool.RUN_01_ID, self.tool_sha)
        suite = self.base / "suite"
        suite_manifest = build_suite(suite, run_01, self.run_02, self.tool_sha)
        suite_manifest["run_manifests"][tool.RUN_ID] = "f" * 64
        data = tool.canonical_json(suite_manifest)
        _ = (suite / "suite-manifest.json").write_bytes(data)
        _ = (suite / "suite-manifest.sha256").write_bytes(
            f"{tool.sha256(data)}  suite-manifest.json\n".encode()
        )
        with self.assertRaisesRegex(tool.EvidenceError, "bind exact verified"):
            _ = tool.verify_suite(run_01, self.run_02, suite, self.tool_sha)

    def test_opposite_cross_run_axis_perturbations_use_angular_tolerance(self) -> None:
        right = tool.replay(self.run_02, self.tool_sha)
        left = copy.deepcopy(right)
        left["run"] = run_identity(tool.RUN_01_ID)
        angle = tool.ANGULAR_TOLERANCE_RAD * 0.75
        left_axis = [-math_sin(angle), 0.0, math_cos(angle)]
        right_axis = [math_sin(angle), 0.0, math_cos(angle)]
        left["variants"][0]["cylinders"][0]["axis"]["direction_unoriented"] = left_axis
        right["variants"][0]["cylinders"][0]["axis"]["direction_unoriented"] = (
            right_axis
        )
        self.assertNotEqual(tool.compare_to_truth(left, "left")["outcome"], "failure")
        self.assertNotEqual(tool.compare_to_truth(right, "right")["outcome"], "failure")
        self.assertEqual(
            tool.compare_runs(left, right, "cross-run")["outcome"], "failure"
        )


@final
class GeometryLogicTest(unittest.TestCase):
    def test_official_array_and_transient_id_anchors(self) -> None:
        run = run_identity(tool.RUN_ID)
        parts = {variant: "JHD" for variant in tool.EXPECTED_VARIANTS}
        responses = {
            spec.name: response_for(spec, run, parts) for spec in tool.OPERATION_SPECS
        }
        requests = {
            spec.name: request_for(spec, run, parts, "a" * 64)
            for spec in tool.OPERATION_SPECS
        }
        tool_private("_validate_official_anchors")(responses, requests, run)
        responses["axisymmetric-part-inventory"][0]["partId"] = "bad/part"
        with self.assertRaisesRegex(tool.EvidenceError, "invalid official part"):
            tool_private("_validate_official_anchors")(responses, requests, run)

    def test_typed_featurescript_value_decoder_rejects_wrong_units(self) -> None:
        number = {
            "btType": "com.belmonttech.serialize.fsvalue.BTFSValueWithUnits",
            "typeTag": "",
            "unitToPower": {"METER": 1},
            "value": 0.012,
        }
        self.assertEqual(tool_private("_decode_fs_value")(number, "number"), 0.012)
        number["unitToPower"] = {"INCH": 1}
        with self.assertRaisesRegex(tool.EvidenceError, "metre"):
            tool_private("_decode_fs_value")(number, "number")

    def test_dominant_component_axis_canonicalization_and_tie_break(self) -> None:
        self.assertEqual(
            tool_private("_canonical_axis")([-1e-20, 0.0, 1.0]),
            [-1e-20, 0.0, 1.0],
        )
        self.assertNotEqual(
            tool_private("_canonical_axis")([-1e-20, 0.0, 1.0]),
            tool_private("_canonical_axis")([1e-20, 0.0, 1.0]),
        )
        self.assertGreater(tool_private("_canonical_axis")([-1e-20, 0.0, 1.0])[2], 0.0)
        tied = tool_private("_canonical_axis")([1.0, -1.0, 0.0])
        self.assertEqual(tool_private("_canonical_axis")([-1.0, 1.0, 0.0]), tied)
        self.assertGreater(tied[0], 0.0)

    def test_field_aware_linear_oriented_and_unoriented_boundaries(self) -> None:
        self.assertEqual(
            tool_private("_linear_finding")("x", 0.0, tool.LINEAR_TOLERANCE_M)[
                "classification"
            ],
            "tolerated numerical",
        )
        angle = tool.ANGULAR_TOLERANCE_RAD * 0.999
        axis = [math_sin(angle), 0.0, math_cos(angle)]
        self.assertNotEqual(
            tool_private("_angular_finding")(
                "axis", axis, [0.0, 0.0, -1.0], unoriented=True
            )["classification"],
            "failure",
        )
        outside = tool.ANGULAR_TOLERANCE_RAD * 1.001
        normal = [math_sin(outside), 0.0, math_cos(outside)]
        self.assertEqual(
            tool_private("_angular_finding")(
                "normal", normal, [0.0, 0.0, 1.0], unoriented=False
            )["classification"],
            "failure",
        )


def math_sin(value: float) -> float:
    import math

    return math.sin(value)


def math_cos(value: float) -> float:
    import math

    return math.cos(value)


@final
class EffectGuardTest(unittest.TestCase):
    temporary: Any = None
    root = Path()
    tool_sha = ""
    record: Any = None
    preflight = Path()

    @override
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tool_sha = current_tool_sha()
        now = datetime.now(UTC)
        self.record = {
            "account_context_sha256": "a" * 64,
            "account_identity_sha256": "b" * 64,
            "authenticated_read_at": now.isoformat(),
            "authorization_expires_at": (now + timedelta(minutes=5)).isoformat(),
            "document_id": tool.DOCUMENT_ID,
            "evidence_disposition_recorded": True,
            "expected_tool_sha256": self.tool_sha,
            "main_microversion_id": "c" * 24,
            "main_workspace_id": tool.MAIN_WORKSPACE_ID,
            "parent_folder_id": tool.SANDBOX_FOLDER_ID,
            "prohibited_content_absent": True,
            "public_visibility": True,
            "start_empty": True,
            "start_microversion_id": tool.START_MICROVERSION_ID,
            "start_version_id": tool.START_VERSION_ID,
            "target_names_absent": [tool.VERSION_NAME, tool.WORKSPACE_NAME],
        }
        self.preflight = self.root / "preflight.json"
        self.write_preflight()

    @override
    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_preflight(self) -> None:
        _ = self.preflight.write_bytes(tool.canonical_json(self.record))

    def args(self, execute: bool = True) -> Namespace:
        data = self.preflight.read_bytes()
        return Namespace(
            document_id=tool.DOCUMENT_ID,
            execute=execute,
            expected_tool_sha256=self.tool_sha,
            preflight=self.preflight,
            preflight_hash=tool.sha256(data),
            start_microversion_id=tool.START_MICROVERSION_ID,
            start_version_id=tool.START_VERSION_ID,
        )

    def test_every_effect_command_blocked_with_valid_execute_arguments(self) -> None:
        with mock.patch.object(tool, "verify_tool_source", return_value=self.tool_sha):
            for stage in (
                "preflight",
                "fresh-workspace",
                "author",
                "version",
                "capture",
            ):
                with (
                    self.subTest(stage=stage),
                    self.assertRaisesRegex(
                        tool.EvidenceError, "live execution remains blocked"
                    ),
                ):
                    tool.execute_effect(stage, self.args())

    def test_every_effect_cli_command_returns_blocked(self) -> None:
        common = [
            "--execute",
            "--document-id",
            tool.DOCUMENT_ID,
            "--start-version-id",
            tool.START_VERSION_ID,
            "--start-microversion-id",
            tool.START_MICROVERSION_ID,
            "--preflight",
            str(self.preflight),
            "--preflight-hash",
            tool.sha256(self.preflight.read_bytes()),
            "--expected-tool-sha256",
            self.tool_sha,
        ]
        with mock.patch.object(tool, "verify_tool_source", return_value=self.tool_sha):
            for stage in (
                "preflight",
                "fresh-workspace",
                "author",
                "version",
                "capture",
            ):
                error = io.StringIO()
                with (
                    self.subTest(stage=stage),
                    mock.patch.object(sys, "argv", ["track5", stage, *common]),
                    contextlib.redirect_stderr(error),
                ):
                    self.assertEqual(tool.main(), 2)
                    self.assertIn("live execution remains blocked", error.getvalue())

    def test_execute_required_and_every_preflight_boundary_rejected(self) -> None:
        with mock.patch.object(tool, "verify_tool_source", return_value=self.tool_sha):
            with self.assertRaisesRegex(tool.EvidenceError, "explicit --execute"):
                _ = tool.validate_effect_args(self.args(execute=False))
            mutations = {
                "expected_tool_sha256": "f" * 64,
                "document_id": "wrong",
                "parent_folder_id": "f" * 24,
                "public_visibility": False,
                "start_empty": False,
                "start_microversion_id": "f" * 24,
                "main_workspace_id": "f" * 24,
                "target_names_absent": [],
                "evidence_disposition_recorded": False,
                "prohibited_content_absent": False,
                "account_identity_sha256": "bad",
            }
            for field, value in mutations.items():
                with self.subTest(field=field):
                    original = self.record[field]
                    self.record[field] = value
                    self.write_preflight()
                    with self.assertRaises(tool.EvidenceError):
                        _ = tool.validate_effect_args(self.args())
                    self.record[field] = original
            self.record["authenticated_read_at"] = (
                datetime.now(UTC) - timedelta(minutes=6)
            ).isoformat()
            self.write_preflight()
            with self.assertRaisesRegex(tool.EvidenceError, "not immediate"):
                _ = tool.validate_effect_args(self.args())

    def test_plan_declares_integrity_and_live_blockers(self) -> None:
        with mock.patch.object(tool, "verify_tool_source", return_value=self.tool_sha):
            plan = tool.build_effect_plan(self.tool_sha)
        self.assertEqual(plan["integrity_scope"], tool.INTEGRITY_SCOPE)
        self.assertIn("blocked", plan["live_status"])
        self.assertEqual(plan["live_blockers"], list(tool.live_blockers()))
        self.assertEqual(plan["main_workspace_id_prohibited"], tool.MAIN_WORKSPACE_ID)

    def test_authenticated_preflight_capability_alone_blocks_plan_and_execution(
        self,
    ) -> None:
        capability_overrides: dict[str, Any] = {
            "AUTHENTICATED_PREFLIGHT_CAPTURE_IMPLEMENTED": False,
            "LIVE_TRANSPORT_IMPLEMENTED": True,
            "AUTHORING_PAYLOADS_FROZEN": True,
            "PROBE_PAYLOAD_FROZEN": True,
        }
        expected = ("authenticated live preflight capture/attestation",)
        with (
            mock.patch.multiple(tool, **capability_overrides),
            mock.patch.object(tool, "verify_tool_source", return_value=self.tool_sha),
        ):
            self.assertEqual(tool.live_blockers(), expected)
            plan = tool.build_effect_plan(self.tool_sha)
            self.assertEqual(plan["live_blockers"], list(expected))
            self.assertEqual(plan["live_status"], f"blocked: {expected[0]}")
            with self.assertRaisesRegex(
                tool.EvidenceError,
                "authenticated live preflight capture/attestation",
            ):
                tool.execute_effect("author", self.args())

    def test_integrity_wording_limits_signed_commit_and_response_provenance(
        self,
    ) -> None:
        self.assertEqual(
            tool.INTEGRITY_SCOPE,
            "SHA-256 sidecars and manifests provide content integrity only, not "
            + "cryptographic authorship. A separately verified signed commit proves a "
            + "signing key endorsed the commit object and committed bytes; it does not "
            + "authenticate Onshape response origin, request/response pairing, or "
            + "necessarily the human committer identity.",
        )


@final
class ToolSourceTest(unittest.TestCase):
    def test_actual_sidecar_matches_reviewed_current_source(self) -> None:
        assert tool.__file__ is not None
        checksum, filename = (
            Path(tool.__file__)
            .with_suffix(".sha256")
            .read_text(encoding="ascii")
            .split()
        )
        self.assertEqual(filename, Path(tool.__file__).name)
        self.assertEqual(tool.verify_tool_source(checksum), checksum)


if __name__ == "__main__":
    _ = unittest.main()
