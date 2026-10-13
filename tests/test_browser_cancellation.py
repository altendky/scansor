"""A missing or interrupted investigation must never summarize as a success."""

import json
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("finished", [False, True])
def test_empty_cancellation_report_is_rejected(tmp_path: Path, finished: bool):
    record: dict[str, object] = {"runs": []}
    if finished:
        record["finished_utc"] = "2026-10-13T23:00:00Z"
    _ = (tmp_path / "results.json").write_text(json.dumps(record))
    script = (
        Path(__file__).resolve().parents[1]
        / "experiments/browser_runtime/cancellation/summarize.mjs"
    )
    result = subprocess.run(
        ["node", str(script), "--directory", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Incomplete cancellation report" in result.stderr
    assert json.loads((tmp_path / "summary.json").read_text())["complete"] is False


def test_probe_cannot_write_through_symlink_into_repository(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "reports"
    output.symlink_to(root, target_is_directory=True)
    result = subprocess.run(
        [
            "node",
            str(root / "experiments/browser_runtime/cancellation/run.mjs"),
            "--directory",
            str(tmp_path / "unused"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "Report output must be outside the repository" in result.stderr
