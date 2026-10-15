"""Actual imported symmetry graphs over frozen independent strict-PLY bytes.

Fixture truth is used only after solving. The shipped numerical path reads
saved declarations and raw observations; it never imports the test generator.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.scan_project import AcceptedScan, ScanProject
from experiments.scan_workspace import ScanLimits

SYMMETRY_FIXTURES = "experiments/browser_runtime/symmetry"
ReplayComparison = Callable[
    [dict[str, Any], dict[str, Any], str, list[dict[str, Any]] | None], None
]


def _open(raw: bytes, name: str) -> AcceptedScan:
    project = ScanProject()
    empty = project.accepted
    return project.commit(
        project.prepare_add(
            io.BytesIO(raw),
            name,
            ScanLimits(1_000_000, 10_000, 10_000, 50_000_000),
            generation=empty.generation,
            token=str(empty.graph.snapshot()["token"]),
        )
    )


def symmetry_graph_probe(
    root: Path,
    case: str,
    *,
    compare_replay: ReplayComparison,
    replay_evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a named solve, account for every row, and replay renamed raw bytes."""
    directory = root / SYMMETRY_FIXTURES
    raw = (directory / f"{case}.ply").read_bytes()
    record = json.loads((directory / f"{case}.json").read_text())
    assert record["schema_version"] == 1 and record["id"] == case
    assert len(raw) == record["source_bytes"]
    assert hashlib.sha256(raw).hexdigest() == record["source_sha256"]
    accepted = _open(raw, f"{case}.ply")
    workspace = accepted.workspace

    # Independently decode this fixed fixture profile, including satellites and
    # original incident-area contribution. This does not generate fitting rows.
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    vertices = np.frombuffer(
        raw, dtype="<f4", count=6 * record["vertex_count"], offset=start
    ).reshape(-1, 6)
    faces = np.frombuffer(
        raw,
        dtype=np.dtype([("count", "u1"), ("indices", "<i4", (3,))]),
        count=record["triangle_count"],
        offset=start + vertices.nbytes,
    )
    assert len(raw) == start + vertices.nbytes + faces.nbytes
    assert np.all(faces["count"] == 3)
    xyz = vertices[:, :3].astype(np.float64)
    normals = vertices[:, 3:].astype(np.float64)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    weights = np.zeros(len(xyz))
    for triangle in faces["indices"]:
        a, b, c = xyz[triangle]
        contribution = np.linalg.norm(np.cross(b - a, c - a)) / 6
        for vertex in triangle:
            weights[vertex] += contribution
    np.testing.assert_array_equal(workspace.local, xyz)
    np.testing.assert_allclose(workspace.data.normals, normals, rtol=1e-14, atol=0)
    np.testing.assert_array_equal(workspace.data.triangles, faces["indices"])
    np.testing.assert_allclose(workspace.data.weights, weights, rtol=1e-14, atol=0)
    np.testing.assert_allclose(weights, record["incident_weights"], rtol=1e-14, atol=0)

    recipe = Recipe.model_validate(record["recipe"])
    graph = FeatureGraph(workspace, recipe)
    state = cast(
        dict[str, Any],
        graph.evaluate(str(graph.snapshot()["token"]), target=record["target"]),
    )
    assert not state["errors"] and not state["diagnostics"], state
    result = state["results"][record["target"]]
    assert set(result["surfaces"]) == set(record["ids"])
    assert result["initialization"]["recovery"]["selected_role"] == "fresh"
    total = mass = 0.0
    tolerance = 32 * record["quantization_bound"]
    for name, ids in record["ids"].items():
        surface = result["surfaces"][name]
        assert surface["ids"] == ids
        points, w = xyz[ids], weights[ids]
        truth = record["truth"][name]
        extent = max(
            float(np.max(np.linalg.norm(points - points.mean(axis=0), axis=1))), 1
        )
        if surface["kind"] == "plane":
            equation = np.asarray(surface["plane_equation"])
            residual = points @ equation[:3] - equation[3]
            expected = np.asarray(truth["plane_equation"])
            np.testing.assert_allclose(
                np.cross(equation[:3], expected[:3]), 0, atol=tolerance / extent
            )
            assert np.max(np.abs(residual)) <= tolerance
        else:
            geometry = surface["analytic"]
            origin, axis = np.asarray(geometry["origin"]), np.asarray(geometry["axis"])
            delta = points - origin
            heights = delta @ axis
            radial = np.linalg.norm(delta - heights[:, None] * axis, axis=1)
            residual = (
                radial - geometry["radius"] - geometry["slope"] * heights
            ) / np.hypot(1, geometry["slope"])
            truth_origin, truth_axis = (
                np.asarray(truth["origin"]),
                np.asarray(truth["axis"]),
            )
            difference = origin - truth_origin
            assert (
                np.linalg.norm(difference - (difference @ truth_axis) * truth_axis)
                <= tolerance
            )
            np.testing.assert_allclose(
                np.cross(axis, truth_axis), 0, atol=tolerance / extent
            )
            sign = 1 if axis @ truth_axis >= 0 else -1
            np.testing.assert_allclose(
                sign * geometry["slope"],
                truth["slope"],
                rtol=0,
                atol=tolerance / extent,
            )
            radius = geometry["radius"] + geometry["slope"] * (
                (truth_origin - origin) @ axis
            )
            np.testing.assert_allclose(radius, truth["radius"], rtol=0, atol=tolerance)
            domain = np.asarray(geometry["axial_domain"])
            support = surface["support_intent"]
            if support["kind"] == "selection":
                expected_domain = [float(heights.min()), float(heights.max())]
            elif support["kind"] == "reference-plane":
                normal = np.asarray(support["normal"])
                assert normal @ axis > 0
                shift = (support["offset"] - normal @ origin) / (normal @ axis)
                expected_domain = np.asarray(support["interval"]) + shift
            else:
                assert support["kind"] == "point"
                assert np.asarray(support["direction"]) @ axis > 0
                shift = (np.asarray(support["reference"]) - origin) @ axis
                expected_domain = np.asarray(support["interval"]) + shift
            np.testing.assert_allclose(domain, expected_domain, rtol=0, atol=1e-10)
            assert np.all(geometry["radius"] + geometry["slope"] * domain > 0)
        assert len(surface["residuals"]) == len(ids)
        np.testing.assert_allclose(
            surface["residuals"], residual, rtol=1e-10, atol=1e-12
        )
        objective = float(w @ residual**2)
        np.testing.assert_allclose(
            surface["weighted_rms"] ** 2, objective / w.sum(), rtol=1e-8, atol=1e-20
        )
        np.testing.assert_allclose(
            result["selected_area"][name], w.sum(), rtol=1e-14, atol=0
        )
        total += objective
        mass += float(w.sum())
    np.testing.assert_allclose(
        result["fit"]["weighted_rms"] ** 2, total / mass, rtol=1e-8, atol=1e-20
    )
    saved = recipe.model_dump_json()
    renamed = _open(raw, f"renamed-{case}.ply")
    replay = FeatureGraph(renamed.workspace, Recipe.model_validate_json(saved))
    replayed = cast(
        dict[str, Any],
        replay.evaluate(str(replay.snapshot()["token"]), target=record["target"]),
    )
    compare_replay(replayed, state, f"symmetry.{case}.replay", replay_evidence)
    return json.loads(
        json.dumps(
            {
                "id": case,
                "kind": record["kind"],
                "mode": record["mode"],
                "source_sha256": record["source_sha256"],
                "state": state,
            },
            allow_nan=False,
        )
    )


def symmetry_graph_probes(
    root: Path,
    *,
    compare_replay: ReplayComparison,
    replay_evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    return [
        symmetry_graph_probe(
            root,
            f"{kind}-{mode}",
            compare_replay=compare_replay,
            replay_evidence=replay_evidence,
        )
        for kind in ("plane", "cylinder", "cone")
        for mode in ("rotation", "mirror")
    ]
