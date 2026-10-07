"""Configured pins drive artifact production and strict conformance coverage."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from experiments.ci_python_versions import main, python_versions
from experiments.mesh_numeric_conformance import compare_matrix

SYSTEMS = (("Linux", "x86_64"), ("Windows", "AMD64"), ("Darwin", "arm64"))
VERSIONS = ("3.12.1", "3.13.1")
HASH_FIELDS = (
    "right_triangle_artifacts",
    "small_recipe_hashes",
    "philox_vectors",
    "implementation_ids",
)


def write_config(path: Path, versions: tuple[str, ...]) -> None:
    _ = path.write_text(f"[tools]\npython = {json.dumps(versions)}\n")


def write_reports(
    root: Path, versions: tuple[str, ...]
) -> dict[tuple[str, str, str], Path]:
    paths = {}
    for system, arch in SYSTEMS:
        for version in versions:
            directory = root / f"mesh-conformance-{system}-{version}"
            directory.mkdir(parents=True, exist_ok=True)
            for mode in ("default", "baseline"):
                report = {
                    "status": "passed",
                    "system": system,
                    "machine": arch,
                    "python": f"{version} (build provenance)",
                    "python_implementation": "CPython",
                    "mode": mode,
                    **{field: {"semantic": "same"} for field in HASH_FIELDS},
                }
                path = directory / f"{mode}.json"
                _ = path.write_text(json.dumps(report))
                paths[system, version, mode] = path
    return paths


@pytest.fixture
def matrix(tmp_path: Path) -> tuple[Path, dict[tuple[str, str, str], Path]]:
    config = tmp_path / "mise.toml"
    write_config(config, VERSIONS)
    return config, write_reports(tmp_path, VERSIONS)


def test_patch_update_drives_workflow_outputs_and_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "mise.toml"
    write_config(config, VERSIONS)
    _ = write_reports(tmp_path, VERSIONS)
    compare_matrix(tmp_path, mise_config=config)
    _ = capsys.readouterr()

    updated = ("3.12.2", VERSIONS[1])
    write_config(config, updated)
    monkeypatch.setattr(sys, "argv", ["ci_python_versions", "--config", str(config)])
    main()
    outputs = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    producer_versions = tuple(json.loads(outputs["versions"]))
    assert producer_versions == updated
    assert outputs["primary"] == updated[0]

    # Old complete artifacts cannot hide missing coverage for the new pin.
    with pytest.raises(FileNotFoundError, match=r"3\.12\.2"):
        compare_matrix(tmp_path, mise_config=config)
    _ = write_reports(tmp_path, producer_versions)
    compare_matrix(tmp_path, mise_config=config)
    compared = json.loads(capsys.readouterr().out)["compared"]
    assert set(compared) == {
        f"{system}/{version}/{mode}"
        for system, _ in SYSTEMS
        for version in updated
        for mode in ("default", "baseline")
    }


@pytest.mark.parametrize("system", [system for system, _ in SYSTEMS])
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("mode", ["default", "baseline"])
def test_comparison_requires_every_configured_report(
    tmp_path: Path,
    matrix: tuple[Path, dict[tuple[str, str, str], Path]],
    system: str,
    version: str,
    mode: str,
) -> None:
    config, paths = matrix
    paths[system, version, mode].unlink()
    with pytest.raises(FileNotFoundError):
        compare_matrix(tmp_path, mise_config=config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "failed"),
        ("system", "Windows"),
        ("machine", "arm64"),
        ("python", "3.12.2 (wrong interpreter)"),
        ("python_implementation", "PyPy"),
        ("mode", "baseline"),
    ],
)
def test_comparison_rejects_incorrect_identity(
    tmp_path: Path,
    matrix: tuple[Path, dict[tuple[str, str, str], Path]],
    field: str,
    value: str,
) -> None:
    config, paths = matrix
    path = paths["Linux", VERSIONS[0], "default"]
    report: dict[str, Any] = json.loads(path.read_text())
    report[field] = value
    _ = path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match="incorrect conformance matrix entry"):
        compare_matrix(tmp_path, mise_config=config)


@pytest.mark.parametrize("field", HASH_FIELDS)
def test_comparison_preserves_semantic_hash_checks(
    tmp_path: Path,
    matrix: tuple[Path, dict[tuple[str, str, str], Path]],
    field: str,
) -> None:
    config, paths = matrix
    path = paths["Darwin", VERSIONS[1], "baseline"]
    report: dict[str, Any] = json.loads(path.read_text())
    report[field] = {"semantic": "different"}
    _ = path.write_text(json.dumps(report))
    with pytest.raises(RuntimeError, match=f"cross-platform mismatch in {field}"):
        compare_matrix(tmp_path, mise_config=config)


@pytest.mark.parametrize(
    "pins",
    [
        '"3.12.1"',
        "[]",
        '["3.12"]',
        '["3.12.1", "3.12.1"]',
        '["3.12.1", 313]',
        '["3.12.1\\nprimary=other"]',
    ],
)
def test_workflow_pins_reject_empty_inexact_or_unsafe_values(
    tmp_path: Path, pins: str
) -> None:
    config = tmp_path / "mise.toml"
    _ = config.write_text(f"[tools]\npython = {pins}\n")
    with pytest.raises(ValueError, match="unique exact versions"):
        _ = python_versions(config)
