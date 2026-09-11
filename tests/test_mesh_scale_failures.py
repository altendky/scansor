from __future__ import annotations

from pathlib import Path

import pytest

from experiments.mesh_scale_failures import probe_failure
from experiments.mesh_scale_metrics import object_record
from scansor.mesh_controls import decode_control
from tests.test_mesh_scale_run import fixture


@pytest.mark.parametrize("kind", ("cancel", "startup-budget"))
def test_real_stop_probe_preserves_source_and_unrelated_files(
    tmp_path: Path, kind: str
) -> None:
    frozen, source = fixture(tmp_path)
    before = source.read_bytes()
    report = tmp_path / "probe.json"
    result = probe_failure(
        frozen,
        source,
        tmp_path,
        "case",
        report,
        kind=kind,
        cancel_phase="decode-vertices",
        chunk_rows=1,
    )
    assert result["status"] == "expected-failure-verified", result
    assert all(value is True for value in object_record(result["checks"]).values())
    assert result["marker_before"] == result["marker_after"]
    assert source.read_bytes() == before
    measured = object_record(decode_control((tmp_path / "case-run.json").read_bytes()))
    assert measured["status"] == "failed"
    assert decode_control(report.read_bytes()) == result
    assert not any((tmp_path / "case" / "work").iterdir())
    marker = (tmp_path / "case-unrelated" / "keep.bin").read_bytes()
    with pytest.raises(FileExistsError):
        _ = probe_failure(frozen, source, tmp_path, "case", report, kind=kind)
    assert (tmp_path / "case-unrelated" / "keep.bin").read_bytes() == marker
    assert decode_control(report.read_bytes()) == result
