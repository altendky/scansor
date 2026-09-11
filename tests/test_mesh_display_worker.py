from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from scansor.mesh_controls import Control, decode_control, encode_control
from scansor.mesh_display import export_display
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_display_verify import verify_display
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MIB
from scansor.mesh_supervisor import run_worker
from scansor.mesh_worker_request import WorkerRequest
from tests.test_mesh_artifacts import artifact_state, published
from tests.test_mesh_supervisor import control_object, fault_command


@pytest.mark.parametrize(
    "transform",
    (
        None,
        DisplayTransform(
            ("3fe0000000000000", "0000000000000000", "0000000000000000"), -1
        ),
    ),
)
def test_fresh_display_export_and_replay_equal_direct_full_artifacts(
    tmp_path: Path, transform: DisplayTransform | None
) -> None:
    first, second = published(tmp_path, "exceptional-values-v1")
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    expected = export_display(
        first.path,
        second.path,
        expected_dir,
        tmp_path,
        transform=transform,
        chunk_rows=2,
    )
    before = artifact_state(first.path), artifact_state(second.path)
    events: list[dict[str, Control]] = []
    result = run_worker(
        WorkerRequest(
            "display",
            first.path,
            contribution=second.path,
            destination=tmp_path,
            expected_import_id=first.identity,
            expected_contribution_id=second.identity,
            display_transform=transform,
            chunk_rows=7,
        ),
        tmp_path,
        progress=events.append,
    )
    assert result["status"] == "complete", result
    stages = cast(list[dict[str, Control]], result["published"])
    assert stages == [
        {
            "kind": "display",
            "path": str(tmp_path / ("mesh-display-" + expected.stage.identity)),
            "identity": expected.stage.identity,
        }
    ]
    displayed = Path(str(stages[0]["path"]))
    for file in expected.stage.path.iterdir():
        assert (displayed / file.name).read_bytes() == file.read_bytes(), file.name
    display_before = artifact_state(displayed)
    verified = run_worker(
        WorkerRequest(
            "verify-display",
            first.path,
            contribution=second.path,
            display=displayed,
            expected_import_id=first.identity,
            expected_contribution_id=second.identity,
            expected_display_id=expected.stage.identity,
            chunk_rows=3,
        ),
        tmp_path,
    )
    assert verified["status"] == "complete", verified
    replay = control_object(control_object(verified["worker_report"])["result"])
    assert replay["status"] == "verified" and replay["verified_data_files"] == 5
    assert replay["display_id"] == expected.stage.identity
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert artifact_state(displayed) == display_before
    for outcome in (result, verified):
        supervision = control_object(outcome["supervision"])
        worker = control_object(outcome["worker_report"])
        assert supervision["exit_code"] == 0
        assert (
            int(str(supervision["kernel_peak_rss_bytes"]))
            >= int(
                str(control_object(worker["memory_after_cleanup"])["os_peak_rss_bytes"])
            )
            > 0
        )
        assert int(str(supervision["requested_sample_interval_ns"])) <= 10_000_000
        assert int(str(supervision["samples"])) > 0
        with pytest.raises(ChildProcessError):
            _ = os.waitpid(int(str(supervision["pid"])), os.WNOHANG)
    assert [event["type"] for event in events if event["type"] != "progress"] == [
        "started",
        "published",
        "result",
    ]
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_fresh_display_replay_early_failure_reports_initial_plan(
    tmp_path: Path,
) -> None:
    imported, contributions = published(tmp_path, "right-triangle-orphan-v1")
    exported = export_display(
        imported.path, contributions.path, tmp_path, tmp_path, chunk_rows=2
    )
    artifacts = (imported.path, contributions.path, exported.stage.path)
    before = tuple(artifact_state(path) for path in artifacts)
    result = run_worker(
        WorkerRequest(
            "verify-display",
            imported.path,
            contribution=contributions.path,
            display=exported.stage.path,
            expected_display_id="a" * 64,
            budget_bytes=512 * MIB,
            chunk_rows=3,
        ),
        tmp_path,
    )
    assert result["status"] == "failed", result
    assert control_object(result["failure"])["category"] == "integrity"
    final = control_object(result["last_progress"])
    assert final["event"] == "final"
    assert final["phase"] == "initializing"
    plan = control_object(final["plan"])
    assert plan["budget_bytes"] == 512 * MIB
    assert plan["batch_rows"] == 3
    assert result["published"] == []
    assert tuple(artifact_state(path) for path in artifacts) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))


@pytest.mark.parametrize("mode", ("display-cleanup-failure", "crash-after-display"))
def test_failed_display_worker_keeps_and_reports_completed_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    before = artifact_state(first.path), artifact_state(second.path)
    fault_command(monkeypatch, mode)
    result = run_worker(
        WorkerRequest(
            "display",
            first.path,
            contribution=second.path,
            destination=tmp_path,
            chunk_rows=2,
        ),
        tmp_path,
    )
    assert result["status"] == "failed"
    stages = cast(list[dict[str, Control]], result["published"])
    assert len(stages) == 1 and stages[0]["kind"] == "display"
    assert control_object(result["failure"])["category"] == (
        "worker-exit" if mode == "crash-after-display" else "execution"
    )
    assert (
        verify_display(
            Path(str(stages[0]["path"])),
            first.path,
            second.path,
            tmp_path,
            expected_display_id=str(stages[0]["identity"]),
            chunk_rows=2,
        )["status"]
        == "verified"
    )
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert not list(tmp_path.glob(".scansor-mesh-*"))


def test_display_request_roundtrip_and_forbidden_fields(tmp_path: Path) -> None:
    request = WorkerRequest(
        "display",
        tmp_path / "import",
        contribution=tmp_path / "contribution",
        destination=tmp_path,
        display_transform=DisplayTransform(scale_power=-1),
    )
    record = request.record()
    assert WorkerRequest.from_record(decode_control(encode_control(record))) == request
    assert request.published_kinds == ("display",)
    assert request.readonly_artifacts == (request.source, request.contribution)
    for field, invalid in (
        ("contribution", None),
        ("destination", None),
        ("display", str(tmp_path)),
        ("sidecar", str(tmp_path)),
        ("storage", "ram"),
        ("expected_display_id", "a" * 64),
        ("display_transform", {}),
        ("revision", "mesh-worker-request-v1"),
    ):
        changed: dict[str, Control] = record | {field: invalid}
        with pytest.raises(MeshImportError):
            _ = WorkerRequest.from_record(changed)
    verification = replace(
        request,
        operation="verify-display",
        destination=None,
        display=tmp_path / "display",
        expected_display_id="a" * 64,
        display_transform=None,
    )
    assert (
        WorkerRequest.from_record(decode_control(encode_control(verification.record())))
        == verification
    )
    for field, invalid in (
        ("display", None),
        ("contribution", None),
        ("destination", str(tmp_path)),
        ("expected_display_id", "bad"),
        ("display_transform", DisplayTransform().record()),
    ):
        changed = verification.record()
        changed[field] = invalid
        with pytest.raises(MeshImportError):
            _ = WorkerRequest.from_record(changed)


@pytest.mark.parametrize("location", ("import", "contribution", "alias"))
@pytest.mark.parametrize("field", ("workdir", "destination"))
def test_display_worker_rejects_authority_writes_before_creating_any_workspace(
    tmp_path: Path, location: str, field: str
) -> None:
    first, second = published(tmp_path, "right-triangle-orphan-v1")
    target = second.path if location == "contribution" else first.path
    if location == "alias":
        alias = tmp_path / "alias"
        alias.symlink_to(target, target_is_directory=True)
        target = alias
    before = artifact_state(first.path), artifact_state(second.path)
    names = set(tmp_path.iterdir())
    request = WorkerRequest(
        "display",
        first.path,
        contribution=second.path,
        destination=target if field == "destination" else tmp_path,
    )
    with pytest.raises(MeshImportError, match="outside read-only"):
        _ = run_worker(request, target if field == "workdir" else tmp_path)
    assert (artifact_state(first.path), artifact_state(second.path)) == before
    assert set(tmp_path.iterdir()) == names
