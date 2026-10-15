"""Graph cancellation retains the complete accepted source/actions boundary."""

import io
import json
from copy import deepcopy
from typing import Any, cast

import numpy as np
import pytest

from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanImportError, ScanLimits
from tests.native_scan_fixture import native_scene_fixture


def test_graph_recovery_preserves_open_warnings_source_and_epoch_after_lease() -> None:
    fixture = native_scene_fixture()
    project = ScanProject()
    initial = project.accepted
    candidate = project.prepare_add(
        io.BytesIO(fixture.raw),
        "scan.ply",
        ScanLimits(2_000_000, 30_000, 60_000, 100_000_000, chunk_bytes=64),
        generation=initial.generation,
        token=str(initial.graph.snapshot()["token"]),
    )
    source = project.commit(candidate)
    raw = cast(dict[str, Any], source.graph.snapshot()["recipe"])
    raw["nodes"].extend(
        [
            {
                "id": "paint",
                "label": "scan.ply",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(72, 84)),
                "depth": "through_all",
            },
            {
                "id": "plane",
                "label": "Plane",
                "operation": "fit",
                "selections": ["paint"],
                "kind": "plane",
            },
        ]
    )
    raw["output"] = "plane"
    opened = project.prepare_open(
        raw,
        generation=source.generation,
        token=str(source.graph.snapshot()["token"]),
        confirmed_discard=True,
    )
    accepted = project.commit_open(opened)
    assert accepted.import_warnings == (
        {
            "id": "paint",
            "old_name": "scan.ply",
            "new_name": "scan.ply selection",
            "kind": "feature",
        },
    )
    assert accepted.workspace is source.workspace
    checkpoint = accepted.graph.failure_checkpoint()
    expected_recipe = deepcopy(accepted.graph.snapshot()["recipe"])
    original_arrays = (
        accepted.positions,
        accepted.indices,
        accepted.workspace.data.xyz,
        accepted.workspace.data.normals,
        accepted.workspace.data.triangles,
        accepted.workspace.data.weights,
        accepted.workspace.data.original_normals,
        accepted.workspace.data.face_status,
    )
    original_values = [array.copy() for array in original_arrays]

    with project.evaluation() as held:
        assert held is accepted
        evaluated = accepted.graph.evaluate(checkpoint.token, all_actions=True)
        assert set(cast(dict[str, str], evaluated["states"]).values()) == {"ready"}
        with pytest.raises(ScanImportError) as busy:
            _ = project.recover_graph(
                generation=accepted.generation,
                token=checkpoint.token,
                recipe=checkpoint.recipe,
                checkpoint=checkpoint,
            )
        assert busy.value.code == "source_busy"
        assert project.accepted is accepted

    recovered = project.recover_graph(
        generation=accepted.generation,
        token=checkpoint.token,
        recipe=checkpoint.recipe,
        checkpoint=checkpoint,
    )
    assert recovered is project.accepted and recovered is not accepted
    assert recovered.graph is not accepted.graph
    assert recovered.generation == accepted.generation == 2
    assert recovered.import_warnings is accepted.import_warnings
    assert recovered.workspace is accepted.workspace
    arrays = (
        recovered.positions,
        recovered.indices,
        recovered.workspace.data.xyz,
        recovered.workspace.data.normals,
        recovered.workspace.data.triangles,
        recovered.workspace.data.weights,
        recovered.workspace.data.original_normals,
        recovered.workspace.data.face_status,
    )
    for array, original, values in zip(
        arrays, original_arrays, original_values, strict=True
    ):
        assert array is original and not array.flags.writeable
        np.testing.assert_array_equal(array, values)
    assert recovered.workspace.original_bytes is accepted.workspace.original_bytes
    assert recovered.workspace.reference_sha256 == accepted.workspace.reference_sha256
    snapshot = recovered.graph.snapshot()
    assert snapshot["recipe"] == expected_recipe
    assert snapshot["token"] == checkpoint.token
    assert set(cast(dict[str, str], snapshot["states"]).values()) == {"unevaluated"}
    assert snapshot["results"] == {}
    _ = json.dumps(snapshot, allow_nan=False)
