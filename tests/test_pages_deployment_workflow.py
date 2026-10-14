"""Exercise the actual first workflow step before downloads or Pages actions."""

import os
import subprocess
from pathlib import Path

import pytest


def guard_script() -> str:
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/deploy-github-pages.yml"
    ).read_text()
    block = workflow.split("        run: |\n", 1)[1].split("\n      - ", 1)[0]
    return "\n".join(line.removeprefix("          ") for line in block.splitlines())


@pytest.mark.parametrize(
    ("publish", "enabled", "allowed"),
    [
        ("false", "", True),
        ("true", "", False),
        ("true", "false", False),
        ("true", "true", True),
    ],
)
def test_publication_gate_precedes_any_download_or_package(
    tmp_path: Path, publish: str, enabled: str, allowed: bool
):
    environment = dict(
        os.environ,
        RUNNER_TEMP=str(tmp_path),
        GITHUB_ENV=str(tmp_path / "env"),
        CI_RUN_ID="123",
        ARTIFACT_ID="456",
        ARCHIVE_SHA256="a" * 64,
        SITE_MANIFEST_SHA256="b" * 64,
        PUBLISH_REQUESTED=publish,
        PUBLICATION_ENABLED=enabled,
    )
    result = subprocess.run(
        ["bash", "-c", guard_script()],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert (result.returncode == 0) is allowed
    assert (tmp_path / "agents/scansor-pages-selection").exists() is allowed
    if not allowed:
        assert "Publication is disabled" in result.stderr


@pytest.mark.parametrize("invalid", ["0", "../runs/1", "123?other=1", "123; touch bad"])
def test_untrusted_api_identifier_rejected_before_any_side_effect(
    tmp_path: Path, invalid: str
):
    result = subprocess.run(
        ["bash", "-c", guard_script()],
        env=dict(
            os.environ,
            RUNNER_TEMP=str(tmp_path),
            GITHUB_ENV=str(tmp_path / "env"),
            CI_RUN_ID=invalid,
            ARTIFACT_ID="456",
            ARCHIVE_SHA256="a" * 64,
            SITE_MANIFEST_SHA256="b" * 64,
            PUBLISH_REQUESTED="false",
            PUBLICATION_ENABLED="",
        ),
        capture_output=True,
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []
