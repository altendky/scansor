"""A rejected parity result must retain its numerical evidence for diagnosis."""

import json
from pathlib import Path
from typing import Any

import pytest

from experiments.browser_runtime import probe
from experiments.browser_runtime.semantic_parity import SemanticParityError


@pytest.mark.parametrize("failed", [False, True])
def test_browser_probe_retains_both_full_records_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    result: dict[str, Any] = {
        "versions": {"python": "worker"},
        "counts": {},
        "fixture_sha256": "fixture",
        "source_sha256": "source",
        "model_sha256": "model",
        "timing_seconds": {},
        "synthetic": {"fit": {"parameters": [1, 2]}},
        "nozzle": {"fit": {"parameters": [3, 4]}},
        "graph": {"result": {"fit": {"parameters": [5, 6]}}, "states": {}},
        "discarded_trace": {"parameters": [1e20], "condition": "Infinity"},
    }
    expected = {**result, "versions": {"python": "native"}}
    expected_path = tmp_path / "native-results.json"
    _ = expected_path.write_text(json.dumps(expected))

    def mapped_path(value: str) -> Path:
        return expected_path if value == "/native-results.json" else Path(value)

    def numerical_result(
        _root: Path, _fixture: Path, *, replay_evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        assert replay_evidence == []
        return result

    monkeypatch.setattr(probe, "Path", mapped_path)
    monkeypatch.setattr(probe, "numeric", numerical_result)
    receipt = {"semantic_complete": not failed, "diagnostic_differences": []}

    def compare(*_: Any) -> dict[str, Any]:
        if failed:
            raise SemanticParityError("changed published radius", receipt)
        return receipt

    monkeypatch.setattr(probe, "compare_numeric", compare)
    report = json.loads(probe.browser())
    assert report["numeric_result"] == result
    assert report["native_result"] == expected
    if failed:
        assert report["parity_failure"] == "changed published radius"
        assert report["failed_semantic_parity"] == receipt
        assert "comparison" not in report
    else:
        assert report["comparison"] == receipt
        assert "parity_failure" not in report


def test_local_replay_failure_keeps_complete_operands_and_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_path = tmp_path / "native-results.json"
    _ = expected_path.write_text('{"reference":"native"}')

    def mapped_path(value: str) -> Path:
        return expected_path if value == "/native-results.json" else Path(value)

    def fail_replay(
        _root: Path, _fixture: Path, *, replay_evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        probe.compare_local_replay(
            {"radius": 3}, {"radius": 2}, "failed-replay", replay_evidence
        )
        raise AssertionError("comparison must fail")

    monkeypatch.setattr(probe, "Path", mapped_path)
    monkeypatch.setattr(probe, "numeric", fail_replay)
    report = json.loads(probe.browser())
    assert "parity_failure" in report
    assert report["native_result"] == {"reference": "native"}
    local = report["local_replay_evidence"]
    assert len(local) == 1
    assert local[0]["actual"] == {"radius": 3}
    assert local[0]["expected"] == {"radius": 2}
    assert local[0]["receipt"]["semantic_complete"] is False


def test_native_local_failure_writes_partial_evidence_before_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_fixture(_path: Path) -> None:
        pass

    def fail_replay(
        _root: Path, _fixture: Path, *, replay_evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        probe.compare_local_replay(
            {"radius": 3}, {"radius": 2}, "failed-replay", replay_evidence
        )
        raise AssertionError("comparison must fail")

    monkeypatch.setattr(probe, "create_fixture", no_fixture)
    monkeypatch.setattr(probe, "numeric", fail_replay)
    with pytest.raises(AssertionError, match="radius"):
        probe.native(str(tmp_path), str(tmp_path), False)
    report = json.loads((tmp_path / "native-results.json").read_text())
    assert report["local_replay_evidence"][0]["actual"] == {"radius": 3}
    assert report["local_replay_evidence"][0]["receipt"]["semantic_complete"] is False
