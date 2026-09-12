from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.mesh_scale_freeze import freeze_grid
from experiments.mesh_scale_generate import prepare_source


def test_production_source_matches_independent_freeze_and_never_overwrites(
    tmp_path: Path,
) -> None:
    frozen, report = tmp_path / "frozen.json", tmp_path / "generated.json"
    _ = freeze_grid(tmp_path, frozen, width=3, height=5, noisy=True, chunk_rows=2)
    actual = prepare_source(frozen, tmp_path, "grid.ply", report, chunk_rows=7)
    assert actual["status"] == "verified-source"
    assert actual["actual"] == actual["expected"]
    saved = json.loads(report.read_bytes())
    assert saved == actual
    assert (
        saved["actual"]["sha256"]
        == hashlib.sha256((tmp_path / "grid.ply").read_bytes()).hexdigest()
    )
    original = (tmp_path / "grid.ply").read_bytes()
    failed = tmp_path / "failed.json"
    with pytest.raises(FileExistsError):
        _ = prepare_source(frozen, tmp_path, "grid.ply", failed)
    assert (tmp_path / "grid.ply").read_bytes() == original
    assert json.loads(failed.read_bytes())["status"] == "failed"


def test_disagreement_with_a_well_formed_frozen_hash_is_reported_without_accepting_native_output(
    tmp_path: Path,
) -> None:
    frozen, report = tmp_path / "frozen.json", tmp_path / "generated.json"
    _ = freeze_grid(tmp_path, frozen, width=2, height=2, noisy=False)
    record = json.loads(frozen.read_bytes())
    record["expectation"]["source"]["sha256"] = "0" * 64
    expected_bytes = (
        json.dumps(
            record["expectation"],
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()
    record["expectation_sha256"] = hashlib.sha256(expected_bytes).hexdigest()
    _ = frozen.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(ValueError, match="differs from frozen"):
        _ = prepare_source(frozen, tmp_path, "grid.ply", report)
    saved = json.loads(report.read_bytes())
    assert saved["status"] == "failed"
    assert saved["actual"]["sha256"] != saved["expected"]["sha256"]
    assert (tmp_path / "grid.ply").exists()


def test_corrupt_frozen_payload_is_rejected_before_generation(tmp_path: Path) -> None:
    frozen, report = tmp_path / "frozen.json", tmp_path / "generated.json"
    _ = freeze_grid(tmp_path, frozen, width=2, height=2, noisy=False)
    record = json.loads(frozen.read_bytes())
    record["expectation"]["width"] = 3
    _ = frozen.write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    with pytest.raises(ValueError, match="payload digest"):
        _ = prepare_source(frozen, tmp_path, "grid.ply", report)
    assert not (tmp_path / "grid.ply").exists()
