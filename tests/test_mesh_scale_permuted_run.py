from __future__ import annotations

from pathlib import Path

import pytest

from experiments.mesh_scale_freeze import freeze_grid
from experiments.mesh_scale_generate import prepare_source
from experiments.mesh_scale_metrics import object_record
from experiments.mesh_scale_permuted_freeze import freeze_permuted
from experiments.mesh_scale_permuted_generate import prepare_permuted_source
from experiments.mesh_scale_run import run_case
from scansor.mesh_controls import decode_control


def fixtures(root: Path, *, noisy: bool) -> tuple[Path, Path, Path]:
    baseline, frozen, source = (
        root / "baseline.json",
        root / "permuted.json",
        root / "baseline.ply",
    )
    _ = freeze_grid(root, baseline, width=17, height=19, noisy=noisy, chunk_rows=11)
    _ = prepare_source(
        baseline, root, source.name, root / "baseline-prepared.json", chunk_rows=7
    )
    _ = freeze_permuted(root, frozen, width=17, height=19, noisy=noisy, chunk_rows=13)
    return baseline, frozen, source


@pytest.mark.parametrize("noisy", (False, True))
def test_independent_freeze_native_reorder_and_full_pipeline(
    tmp_path: Path, noisy: bool
) -> None:
    baseline, frozen, original = fixtures(tmp_path, noisy=noisy)
    before = original.read_bytes()
    prepared = prepare_permuted_source(
        frozen,
        baseline,
        original,
        tmp_path,
        "source.ply",
        tmp_path / "prepared.json",
        chunk_rows=7,
    )
    assert prepared["status"] == "verified-source"
    assert prepared["actual"] == prepared["expected"]
    assert original.read_bytes() == before
    run = run_case(
        frozen,
        tmp_path / "source.ply",
        tmp_path,
        "case",
        tmp_path / "run.json",
        budget_bytes=512 * 1024 * 1024,
        storage="disk",
        chunk_rows=7,
    )
    assert run["revision"] == "mesh-scale-permuted-grid-run-v1"
    assert run["status"] == "complete", run.get("error")
    assert set(object_record(run["operations"])) == {
        "import",
        "display",
        "verify-display",
    }
    assert decode_control((tmp_path / "run.json").read_bytes()) == run
    source_before = (tmp_path / "source.ply").read_bytes()
    with pytest.raises(FileExistsError):
        _ = prepare_permuted_source(
            frozen, baseline, original, tmp_path, "source.ply", tmp_path / "failed.json"
        )
    assert (tmp_path / "source.ply").read_bytes() == source_before
    assert (
        object_record(decode_control((tmp_path / "failed.json").read_bytes()))["status"]
        == "failed"
    )


def test_reorder_rejects_changed_baseline_and_preserves_failed_report(
    tmp_path: Path,
) -> None:
    baseline, frozen, original = fixtures(tmp_path, noisy=True)
    with original.open("r+b") as stream:
        _ = stream.seek(-1, 2)
        _ = stream.write(b"\xff")
    with pytest.raises(ValueError, match="baseline source differs"):
        _ = prepare_permuted_source(
            frozen,
            baseline,
            original,
            tmp_path,
            "changed.ply",
            tmp_path / "failed.json",
        )
    result = object_record(decode_control((tmp_path / "failed.json").read_bytes()))
    assert result["status"] == "failed"
    assert (tmp_path / "changed.ply").read_bytes() == b""


def test_permutation_map_closes_when_native_writer_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, frozen, original = fixtures(tmp_path, noisy=True)
    from scansor._plyio import Writer

    def fail(*_args: object) -> None:
        raise OSError("injected output failure")

    monkeypatch.setattr(Writer, "write_range", fail)
    with pytest.raises(OSError, match="injected output failure"):
        _ = prepare_permuted_source(
            frozen,
            baseline,
            original,
            tmp_path,
            "changed.ply",
            tmp_path / "failed.json",
        )
    result = object_record(decode_control((tmp_path / "failed.json").read_bytes()))
    assert result["status"] == "failed"
    assert object_record(result["error"])["type"] == "OSError"
