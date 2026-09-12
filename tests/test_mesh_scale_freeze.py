from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments import mesh_scale_freeze
from experiments.mesh_scale_freeze import freeze_grid


def test_freeze_retains_implementation_expected_digest_and_exclusive_output(
    tmp_path: Path,
) -> None:
    output = tmp_path / "expected.json"
    result = freeze_grid(tmp_path, output, width=3, height=3, noisy=True, chunk_rows=7)
    saved = json.loads(output.read_bytes())
    assert saved == json.loads(json.dumps(result))
    assert saved["status"] == "complete"
    expected_bytes = (
        json.dumps(
            saved["expectation"], sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode()
    assert saved["expectation_sha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert set(saved["implementation"]) == {
        "experiments/mesh_scale_freeze.py",
        "experiments/mesh_scale_grid.py",
        "experiments/mesh_scale_integer.py",
        "experiments/mesh_scale_rounding.py",
        "tests/mesh_rounding_oracle.py",
        "tests/fixtures/mesh-numeric-goldens-v1.json",
    }
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        _ = freeze_grid(tmp_path, output, width=2, height=2, noisy=False)
    assert output.read_bytes() == original
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("git_file", (False, True))
def test_freeze_rejects_git_worktrees_ignored_directories_and_symlink_aliases(
    tmp_path: Path, git_file: bool
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    if git_file:
        _ = (repo / ".git").write_text("gitdir: /unused\n")
    else:
        (repo / ".git").mkdir()
        _ = (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / "local-inputs").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(repo / "local-inputs", target_is_directory=True)
    output = tmp_path / "expected.json"
    for directory in (repo, repo / "local-inputs", alias):
        with pytest.raises(ValueError, match="outside Git"):
            _ = freeze_grid(directory, output, width=2, height=2, noisy=False)
    assert not output.exists()
    assert list((repo / "local-inputs").iterdir()) == []


def test_freeze_records_failure_and_preserves_error(tmp_path: Path) -> None:
    output = tmp_path / "failed.json"
    with pytest.raises(ValueError, match="explicit S6 profile"):
        _ = freeze_grid(tmp_path, output, width=1, height=2, noisy=False)
    saved = json.loads(output.read_bytes())
    assert saved["status"] == "failed"
    assert saved["error"]["type"] == "ValueError"
    assert "expectation" not in saved
    assert list(tmp_path.iterdir()) == [output]


def test_freeze_rejects_implementation_change_before_claiming_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = mesh_scale_freeze._implementation  # pyright: ignore[reportPrivateUsage]
    calls = 0

    def changing_implementation() -> dict[str, str]:
        nonlocal calls
        calls += 1
        result = original()
        if calls > 1:
            result["experiments/mesh_scale_grid.py"] = "0" * 64
        return result

    monkeypatch.setattr(mesh_scale_freeze, "_implementation", changing_implementation)
    output = tmp_path / "changed.json"
    with pytest.raises(RuntimeError, match="implementation changed"):
        _ = freeze_grid(tmp_path, output, width=2, height=2, noisy=False)
    assert json.loads(output.read_bytes())["status"] == "failed"
    assert list(tmp_path.iterdir()) == [output]
