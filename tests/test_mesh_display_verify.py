from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from scansor.mesh_controls import (
    Control,
    control_artifact,
    control_id,
    decode_control,
    encode_control,
)
from scansor.mesh_display import export_display
from scansor.mesh_display_verify import verify_display
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MIB
from tests.test_mesh_artifacts import artifact_state, published


@pytest.mark.parametrize(
    "name", ("right-triangle-orphan-v1", "exceptional-values-v1", "all-invalid-v1")
)
def test_complete_display_replay_is_readonly_and_chunk_independent(
    tmp_path: Path, name: str
) -> None:
    imported, contributions = published(tmp_path, name)
    exported = export_display(
        imported.path, contributions.path, tmp_path, tmp_path, chunk_rows=2
    )
    before = tuple(
        artifact_state(p)
        for p in (imported.path, contributions.path, exported.stage.path)
    )
    for budget, chunk in ((512, 1), (2048, 7)):
        events: list[dict[str, Control]] = []
        result = verify_display(
            exported.stage.path,
            imported.path,
            contributions.path,
            tmp_path,
            expected_display_id=exported.stage.identity,
            expected_import_id=imported.identity,
            expected_contribution_id=contributions.identity,
            budget_bytes=budget * MIB,
            chunk_rows=chunk,
            progress=events.append,
        )
        assert result["status"] == "verified"
        assert result["verified_data_files"] == 5
        assert result["display_id"] == exported.stage.identity
        assert events[0]["event"] == "phase"
        assert events[0]["phase"] == "initializing"
        initial_plan = cast(dict[str, Control], events[0]["plan"])
        assert initial_plan["budget_bytes"] == budget * MIB
        assert initial_plan["batch_rows"] == chunk
        assert all(isinstance(event["plan"], dict) for event in events)
        assert events[-1]["event"] == "final"
        assert events[-1]["plan"] == result["plan"]
        assert (
            initial_plan["disk_estimate_bytes"]
            != cast(dict[str, Control], result["plan"])["disk_estimate_bytes"]
        )
    assert (
        tuple(
            artifact_state(p)
            for p in (imported.path, contributions.path, exported.stage.path)
        )
        == before
    )
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize(
    "mutation",
    (
        "color",
        "duplicate-map-row",
        "legend-omission",
        "extra-file",
        "symlink",
        "wrong-id",
    ),
)
def test_display_replay_rejects_fully_rehashed_display_tampering_without_repair(
    tmp_path: Path, mutation: str
) -> None:
    imported, contributions = published(tmp_path, "right-triangle-orphan-v1")
    exported = export_display(
        imported.path, contributions.path, tmp_path, tmp_path, chunk_rows=2
    )
    root = exported.stage.path
    inventory = cast(
        dict[str, Control], decode_control((root / "inventory.json").read_bytes())
    )
    if mutation in ("color", "duplicate-map-row"):
        name = "validity.ply" if mutation == "color" else "view-vertices.bin"
        path = root / name
        raw = bytearray(path.read_bytes())
        if mutation == "color":
            offset = raw.index(b"end_header\n") + len(b"end_header\n") + 24
            raw[offset] = 1
        else:
            raw[8:16] = raw[:8]
        _ = path.write_bytes(raw)
        for record in cast(list[dict[str, Control]], inventory["files"]):
            if record["name"] == name:
                record["sha256"] = hashlib.sha256(raw).hexdigest()
    elif mutation == "legend-omission":
        legend = cast(
            dict[str, Control], decode_control((root / "legend.json").read_bytes())
        )
        cast(dict[str, Control], legend["omissions"])["nonfinite_source_vertices"] = 1
        _ = (root / "legend.json").write_bytes(encode_control(legend))
        inventory["legend"] = control_artifact("legend.json", legend)
    elif mutation == "extra-file":
        _ = (root / "unexpected").write_bytes(b"keep")
    elif mutation == "symlink":
        original = root / "weights.ply"
        foreign = tmp_path / "foreign.ply"
        _ = foreign.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(foreign)
    _ = (root / "inventory.json").write_bytes(encode_control(inventory))
    before = tuple(artifact_state(p) for p in (imported.path, contributions.path, root))
    events: list[dict[str, Control]] = []
    with pytest.raises(MeshImportError):
        _ = verify_display(
            root,
            imported.path,
            contributions.path,
            tmp_path,
            expected_display_id="a" * 64
            if mutation == "wrong-id"
            else control_id(inventory),
            chunk_rows=2,
            progress=events.append,
        )
    assert events[0]["phase"] == "initializing"
    assert events[-1]["event"] == "final"
    assert all(isinstance(event["plan"], dict) for event in events)
    if mutation in ("extra-file", "symlink", "wrong-id"):
        assert events[-1]["plan"] == events[0]["plan"]
    assert (
        tuple(artifact_state(p) for p in (imported.path, contributions.path, root))
        == before
    )
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize(
    "phase", ("artifact-import-vertices", "artifact-contribution-vertices")
)
def test_display_replay_cancelled_before_replanning_retains_initial_plan(
    tmp_path: Path, phase: str
) -> None:
    imported, contributions = published(tmp_path, "right-triangle-orphan-v1")
    exported = export_display(
        imported.path, contributions.path, tmp_path, tmp_path, chunk_rows=2
    )
    artifacts = (imported.path, contributions.path, exported.stage.path)
    before = tuple(artifact_state(path) for path in artifacts)
    events: list[dict[str, Control]] = []

    def cancel(event: dict[str, Control]) -> None:
        events.append(event)
        if event["event"] == "phase" and event["phase"] == phase:
            raise MeshImportError("cancelled", phase, "requested cancellation")

    with pytest.raises(MeshImportError) as caught:
        _ = verify_display(
            exported.stage.path,
            imported.path,
            contributions.path,
            tmp_path,
            chunk_rows=2,
            progress=cancel,
        )
    assert caught.value.category == "cancelled"
    assert events[0]["phase"] == "initializing"
    assert isinstance(events[0]["plan"], dict)
    assert events[-1]["event"] == "final"
    assert events[-1]["phase"] == phase
    assert all(event["plan"] == events[0]["plan"] for event in events)
    assert tuple(artifact_state(path) for path in artifacts) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_display_replay_refuses_scratch_inside_any_artifact(tmp_path: Path) -> None:
    imported, contributions = published(tmp_path, "right-triangle-orphan-v1")
    exported = export_display(
        imported.path, contributions.path, tmp_path, tmp_path, chunk_rows=2
    )
    for scratch in (imported.path, contributions.path, exported.stage.path):
        with pytest.raises(MeshImportError, match="outside"):
            _ = verify_display(
                exported.stage.path, imported.path, contributions.path, scratch
            )
