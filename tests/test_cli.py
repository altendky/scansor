from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

import pytest

from tests.conftest import write_ply
from tests.test_runs import (
    malformed_unicode_field_npy,
    remove_persisted_field,
    rewrite_run_canonical,
    rewrite_run_settings,
    rewrite_run_source_path,
)


class _SourceReport(TypedDict):
    byte_count: int
    frame: str
    path: str
    sha256: str
    unit: str


class _SettingReport(TypedDict):
    source: str
    value: object


class _RunReport(TypedDict):
    job: dict[str, object]
    settings: dict[str, _SettingReport]
    source: _SourceReport


def run_cli(
    tmp_path: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("SCANSOR_")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if environment:
        env.update(environment)
    return subprocess.run(
        [sys.executable, "-m", "scansor", *arguments],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def report(path: Path) -> _RunReport:
    loaded = json.loads((path / "report.json").read_text(encoding="ascii"))
    if not isinstance(loaded, dict):
        raise AssertionError("run report must be a JSON object")
    return cast(_RunReport, cast(object, loaded))


def test_complete_inspect_job_from_toml_uses_invocation_directory(
    tmp_path: Path,
) -> None:
    source = write_ply(tmp_path / "input.ply")
    config_directory = tmp_path / "configuration"
    config_directory.mkdir()
    config = config_directory / "job.toml"
    _ = config.write_text(
        """[scansor]
input_path = "input.ply"
output_path = "toml-run"
unit = "mm"
frame = "toml-frame"
max_vertices = 1
""",
        encoding="ascii",
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config.relative_to(tmp_path)),
        "inspect",
    )
    assert completed.returncode == 0, completed.stderr
    run_report = report(tmp_path / "toml-run")
    assert run_report["source"] == {
        "byte_count": source.stat().st_size,
        "frame": "toml-frame",
        "path": str(source.absolute()),
        "sha256": run_report["source"]["sha256"],
        "unit": "mm",
    }
    assert run_report["job"] == {
        "deterministic": True,
        "model": None,
        "normal_handling": ("validate-finite-nonzero-and-preserve-or-record-absence"),
        "random_seed": None,
        "selection": "inspect",
        "supported_fit_options": [],
    }
    assert run_report["settings"]["max_vertices"] == {
        "source": "toml",
        "value": 1,
    }


def test_cli_config_environment_command_line_precedence(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply", rows=[(1.0, 2.0, 3.0)] * 4)
    config = tmp_path / "settings.toml"
    _ = config.write_text("[scansor]\nmax_vertices = 1\n", encoding="ascii")
    output = tmp_path / "run"
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(source),
        str(output),
        "--unit",
        "m",
        "--frame",
        "scanner",
        "--max-vertices",
        "4",
        environment={"SCANSOR_MAX_VERTICES": "2"},
    )
    assert completed.returncode == 0, completed.stderr
    settings = report(output)["settings"]
    assert settings["max_vertices"] == {"source": "command-line", "value": 4}

    underscore_output = tmp_path / "underscore-run"
    completed = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(underscore_output),
        "--unit",
        "m",
        "--frame",
        "scanner",
        "--max_vertices",
        "4",
    )
    assert completed.returncode == 0, completed.stderr
    assert report(underscore_output)["settings"]["max_vertices"] == {
        "source": "command-line",
        "value": 4,
    }

    env_output = tmp_path / "env-run"
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(source),
        str(env_output),
        "--unit",
        "m",
        "--frame",
        "scanner",
        environment={"SCANSOR_MAX_VERTICES": "4"},
    )
    assert completed.returncode == 0, completed.stderr
    assert report(env_output)["settings"]["max_vertices"] == {
        "source": "environment",
        "value": 4,
    }

    toml_output = tmp_path / "toml-run"
    _ = config.write_text("[scansor]\nmax_vertices = 4\n", encoding="ascii")
    completed = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(toml_output),
        "--unit",
        "m",
        "--frame",
        "scanner",
        "--config",
        str(config),
    )
    assert completed.returncode == 0, completed.stderr
    assert report(toml_output)["settings"]["max_vertices"] == {
        "source": "toml",
        "value": 4,
    }
    for run in (output, env_output, toml_output):
        verified = run_cli(tmp_path, "verify", str(run))
        assert verified.returncode == 0, verified.stderr
        assert "verification: PASS" in verified.stdout
    mismatched = tmp_path / "verify-settings.toml"
    _ = mismatched.write_text("[scansor]\nmax_vertices = 1\n", encoding="ascii")
    verified = run_cli(
        tmp_path,
        "--config",
        str(mismatched),
        "verify",
        str(output),
        environment={"SCANSOR_MAX_VERTICES": "2"},
    )
    assert verified.returncode == 0, verified.stderr
    assert "verification: PASS" in verified.stdout


def test_inspect_job_cli_overrides_environment_and_toml(tmp_path: Path) -> None:
    cli_source = write_ply(tmp_path / "cli.ply")
    _ = write_ply(tmp_path / "environment.ply")
    _ = write_ply(tmp_path / "toml.ply")
    config = tmp_path / "job.toml"
    _ = config.write_text(
        """[scansor]
input_path = "toml.ply"
output_path = "toml-run"
unit = "mm"
frame = "toml-frame"
""",
        encoding="ascii",
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(cli_source),
        "cli-run",
        "--unit",
        "m",
        "--frame",
        "cli-frame",
        environment={
            "SCANSOR_INPUT_PATH": "environment.ply",
            "SCANSOR_OUTPUT_PATH": "environment-run",
            "SCANSOR_UNIT": "mm",
            "SCANSOR_FRAME": "environment-frame",
        },
    )
    assert completed.returncode == 0, completed.stderr
    run_report = report(tmp_path / "cli-run")
    assert run_report["source"]["path"] == str(cli_source.absolute())
    assert run_report["source"]["unit"] == "m"
    assert run_report["source"]["frame"] == "cli-frame"
    assert not (tmp_path / "environment-run").exists()
    assert not (tmp_path / "toml-run").exists()


def test_inspect_job_environment_overrides_toml(tmp_path: Path) -> None:
    environment_source = write_ply(tmp_path / "environment.ply")
    _ = write_ply(tmp_path / "toml.ply")
    config = tmp_path / "job.toml"
    _ = config.write_text(
        """[scansor]
input_path = "toml.ply"
output_path = "toml-run"
unit = "m"
frame = "toml-frame"
""",
        encoding="ascii",
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        environment={
            "SCANSOR_INPUT_PATH": "environment.ply",
            "SCANSOR_OUTPUT_PATH": "environment-run",
            "SCANSOR_UNIT": "mm",
            "SCANSOR_FRAME": "environment-frame",
        },
    )
    assert completed.returncode == 0, completed.stderr
    run_report = report(tmp_path / "environment-run")
    assert run_report["source"]["path"] == str(environment_source.absolute())
    assert run_report["source"]["unit"] == "mm"
    assert run_report["source"]["frame"] == "environment-frame"
    assert not (tmp_path / "toml-run").exists()


def test_cli_default_provenance_and_replay(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    completed = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(output),
        "--unit",
        "mm",
        "--frame",
        "scanner-frame",
    )
    assert completed.returncode == 0, completed.stderr
    assert all(
        item["source"] == "default" for item in report(output)["settings"].values()
    )
    before = {item.name: item.read_bytes() for item in output.iterdir()}
    verified = run_cli(tmp_path, "verify", str(output))
    assert verified.returncode == 0, verified.stderr
    assert "verification: PASS" in verified.stdout
    assert {item.name: item.read_bytes() for item in output.iterdir()} == before


def test_cli_rejects_unknown_toml_and_environment(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    config = tmp_path / "settings.toml"
    _ = config.write_text("[scansor]\nmisspelled = 1\n", encoding="ascii")
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(source),
        str(tmp_path / "run"),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert completed.returncode == 2
    assert "unknown TOML setting" in completed.stderr
    completed = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(tmp_path / "run"),
        "--unit",
        "m",
        "--frame",
        "f",
        environment={"SCANSOR_MISSPELLED": "1"},
    )
    assert completed.returncode == 2
    assert "unknown SCANSOR" in completed.stderr


@pytest.mark.parametrize(
    "field",
    [
        "input_path = 1",
        'unit = "inch"',
        'frame = "   "',
        'selection = "fit"',
        'model = "arbitrary"',
        'supported_fit_options = ["anything"]',
    ],
)
def test_cli_rejects_malformed_or_unsupported_toml_job_fields_cleanly(
    tmp_path: Path, field: str
) -> None:
    name = field.partition(" = ")[0]
    required = {
        "input_path": 'input_path = "input.ply"',
        "output_path": 'output_path = "run"',
        "unit": 'unit = "m"',
        "frame": 'frame = "frame"',
    }
    required[name] = field
    config = tmp_path / "job.toml"
    _ = config.write_text(
        "[scansor]\n" + "\n".join(required.values()) + "\n",
        encoding="ascii",
    )
    completed = run_cli(tmp_path, "--config", str(config), "inspect")
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("field", ["input_path", "output_path"])
@pytest.mark.parametrize("escaped_path", [r"bad\u0000path", r"bad\uD800path"])
def test_cli_rejects_invalid_toml_job_paths_cleanly(
    tmp_path: Path, field: str, escaped_path: str
) -> None:
    required = {
        "input_path": 'input_path = "input.ply"',
        "output_path": 'output_path = "run"',
        "unit": 'unit = "m"',
        "frame": 'frame = "frame"',
    }
    required[field] = f'{field} = "{escaped_path}"'
    config = tmp_path / "job.toml"
    _ = config.write_text(
        "[scansor]\n" + "\n".join(required.values()) + "\n",
        encoding="ascii",
    )
    completed = run_cli(tmp_path, "--config", str(config), "inspect")
    assert completed.returncode == 2
    assert "invalid TOML config" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_cli_rejects_symlinked_explicit_toml(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    target = tmp_path / "target.toml"
    _ = target.write_text("[scansor]\nmax_vertices = 1\n", encoding="ascii")
    config = tmp_path / "settings.toml"
    config.symlink_to(target)
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(source),
        str(tmp_path / "run"),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert completed.returncode == 2
    assert "non-symlink" in completed.stderr


def test_cli_rejects_deep_toml_without_traceback(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    config = tmp_path / "deep.toml"
    _ = config.write_text(
        "scansor = " + "{ a = " * 1_500 + "1" + " }" * 1_500 + "\n",
        encoding="ascii",
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "inspect",
        str(source),
        str(tmp_path / "run"),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr


def test_pipeline_commands_are_discoverable(tmp_path: Path) -> None:
    completed = run_cli(tmp_path, "--help")
    assert completed.returncode == 0
    for command in ("map", "verify-mapping", "fit", "verify-fit"):
        assert command in completed.stdout


def test_verify_ignores_conflicting_current_inspect_job_configuration(
    tmp_path: Path,
) -> None:
    source = write_ply(tmp_path / "input.ply")
    run = tmp_path / "run"
    inspected = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(run),
        "--unit",
        "m",
        "--frame",
        "recorded-frame",
    )
    assert inspected.returncode == 0, inspected.stderr
    conflicting = tmp_path / "conflicting.toml"
    _ = conflicting.write_text(
        """[scansor]
input_path = "missing-toml.ply"
output_path = "wrong-toml-run"
unit = "mm"
frame = "wrong-toml-frame"
max_input_bytes = 1024
log_level = "error"
""",
        encoding="ascii",
    )
    verified = run_cli(
        tmp_path,
        "--config",
        str(conflicting),
        "verify",
        str(run),
        environment={
            "SCANSOR_INPUT_PATH": "missing-environment.ply",
            "SCANSOR_OUTPUT_PATH": "wrong-environment-run",
            "SCANSOR_UNIT": "mm",
            "SCANSOR_FRAME": "wrong-environment-frame",
            "SCANSOR_MAX_INPUT_BYTES": "1024",
            "SCANSOR_LOG_LEVEL": "error",
        },
    )
    assert verified.returncode == 0, verified.stderr
    assert "verification: PASS" in verified.stdout
    assert not (tmp_path / "wrong-toml-run").exists()
    assert not (tmp_path / "wrong-environment-run").exists()

    logged = run_cli(
        tmp_path,
        "--config",
        str(conflicting),
        "verify",
        str(run),
        "--log-level",
        "debug",
        environment={"SCANSOR_LOG_LEVEL": "error"},
    )
    assert logged.returncode == 0, logged.stderr
    assert "inspection_verified" in logged.stderr


def test_verify_rejects_wrong_persisted_setting_type_without_traceback(
    tmp_path: Path,
) -> None:
    source = write_ply(tmp_path / "input.ply")
    run = tmp_path / "run"
    inspected = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(run),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert inspected.returncode == 0, inspected.stderr
    rewrite_run_settings(run, "max_input_bytes", "67108864")
    verified = run_cli(tmp_path, "verify", str(run))
    assert verified.returncode == 2
    assert "persisted run model is invalid" in verified.stderr
    assert "Traceback" not in verified.stderr


@pytest.mark.parametrize(
    ("artifact", "field_paths"),
    [
        ("manifest", (("format",),)),
        (
            "report",
            tuple(
                ("job", field)
                for field in (
                    "deterministic",
                    "model",
                    "normal_handling",
                    "random_seed",
                    "selection",
                    "supported_fit_options",
                )
            ),
        ),
    ],
)
def test_verify_rejects_omitted_persisted_defaults_without_traceback(
    tmp_path: Path, artifact: str, field_paths: tuple[tuple[str, ...], ...]
) -> None:
    source = write_ply(tmp_path / "input.ply")
    run = tmp_path / "run"
    inspected = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(run),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert inspected.returncode == 0, inspected.stderr
    for field_path in field_paths:
        remove_persisted_field(run, artifact, field_path)
    verified = run_cli(tmp_path, "verify", str(run))
    assert verified.returncode == 2
    assert f"persisted {artifact}" in verified.stderr
    assert "canonical model" in verified.stderr
    assert "Traceback" not in verified.stderr


@pytest.mark.parametrize(
    "corruption", ["npy", "unicode-npy", "nul-path", "encoding-path"]
)
def test_verify_rejects_malformed_artifacts_without_traceback(
    tmp_path: Path, corruption: str
) -> None:
    source = write_ply(tmp_path / "input.ply")
    run = tmp_path / "run"
    inspected = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(run),
        "--unit",
        "m",
        "--frame",
        "f",
    )
    assert inspected.returncode == 0, inspected.stderr
    if corruption == "npy":
        rewrite_run_canonical(run, b"PK\x03\x04malformed zip")
    elif corruption == "unicode-npy":
        rewrite_run_canonical(run, malformed_unicode_field_npy())
    elif corruption == "nul-path":
        rewrite_run_source_path(run, "bad\0path")
    else:
        rewrite_run_source_path(run, "bad\ud800path")
    verified = run_cli(tmp_path, "verify", str(run))
    assert verified.returncode == 2
    assert "ERROR:" in verified.stderr
    assert "Traceback" not in verified.stderr
