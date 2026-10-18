"""License gates cover locked transitives and fail closed on incomplete evidence."""

import io
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, NoReturn

import pytest

from experiments import dependency_licenses as audit


@pytest.mark.parametrize(
    "expression",
    ["MIT", "(Apache-2.0 OR BSD-2-Clause) AND MIT", "PSFL", "MPL-2.0"],
)
def test_understood_licenses_pass(expression: str) -> None:
    assert audit.check_expression(expression)


@pytest.mark.parametrize(
    "expression",
    [
        "",
        "UNKNOWN",
        "LicenseRef-Custom",
        "GPL-3.0-only",
        "LGPL-2.1-or-later",
        "AGPL-3.0-only",
        "MIT OR GPL-2.0-only",
        "MIT AND LGPL-3.0-only",
        "BSD-3-Clause AND",
        "MIT BSD-2-Clause",
        "(MIT",
        "MIT)",
        "MIT WITH unknown",
    ],
)
def test_unknown_prohibited_and_malformed_licenses_fail(expression: str) -> None:
    with pytest.raises(ValueError):
        _ = audit.check_expression(expression)


def test_python_metadata_prefers_expression_and_recognizes_legacy_classifiers() -> None:
    assert (
        audit.metadata_license({"license_expression": "MIT", "license": "old prose"})
        == "MIT"
    )
    assert (
        audit.metadata_license(
            {"classifiers": ["License :: OSI Approved :: BSD License"]}
        )
        == "BSD family (legacy classifier)"
    )
    assert audit.metadata_license({"license": "PSFL"}) == "PSF-2.0"


@pytest.mark.parametrize(
    "info",
    [
        {},
        {"license_expression": "LicenseRef-Unknown", "license": "MIT"},
        {
            "license": "unreviewed prose",
            "classifiers": ["License :: OSI Approved :: MIT License"],
        },
        {
            "license_expression": "MIT",
            "classifiers": [
                "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)"
            ],
        },
    ],
)
def test_unknown_or_conflicting_metadata_fails(info: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _ = audit.metadata_license(info)


def python_fixture(root: Path, extra: str = "") -> None:
    _ = (root / "pyproject.toml").write_text('[project]\nname = "scansor"\n')
    _ = (root / "uv.lock").write_text(
        '[[package]]\nname = "scansor"\nversion = "0.0.0"\nsource = { editable = "." }\n'
        + '[[package]]\nname = "direct"\nversion = "1.0"\nsource = { registry = "https://pypi.org/simple" }\n'
        + '[[package]]\nname = "transitive-windows-only"\nversion = "2.0"\nsource = { registry = "https://pypi.org/simple" }\n'
        + extra
    )
    (root / "scripts").mkdir()
    _ = (root / "scripts/dependency-license-native-baseline.json").write_text(
        json.dumps({"python": {}})
    )


def test_python_lock_covers_transitives_without_platform_filtering(
    tmp_path: Path,
) -> None:
    python_fixture(tmp_path)
    assert audit.python_packages(tmp_path) == [
        ("direct", "1.0"),
        ("transitive-windows-only", "2.0"),
    ]


def test_unreviewed_python_sources_fail(tmp_path: Path) -> None:
    python_fixture(
        tmp_path,
        '[[package]]\nname = "new-local"\nversion = "1"\nsource = { editable = "other" }\n',
    )
    with pytest.raises(ValueError, match="unreviewed Python source"):
        _ = audit.python_packages(tmp_path)


@pytest.mark.parametrize(
    "version,hashes", [("2.0", ["sha256:approved"]), ("1.0", ["sha256:changed"])]
)
def test_native_approval_requires_exact_version_and_artifacts(
    tmp_path: Path, version: str, hashes: list[str]
) -> None:
    python_fixture(
        tmp_path,
        f'[[package]]\nname = "cadquery-ocp-novtk"\nversion = "{version}"\nsource = {{ registry = "https://pypi.org/simple" }}\nwheels = [{{hash = "{hashes[0]}"}}]\n',
    )
    baseline = {
        "python": {
            "cadquery-ocp-novtk": {"version": "1.0", "hashes": ["sha256:approved"]}
        }
    }
    _ = (tmp_path / "scripts/dependency-license-native-baseline.json").write_text(
        json.dumps(baseline)
    )
    with pytest.raises(ValueError, match="native kernel artifacts changed"):
        _ = audit.python_packages(tmp_path)


def test_npm_optional_transitive_and_missing_metadata_fail(tmp_path: Path) -> None:
    directory = tmp_path / "experiments/browser_viewer"
    directory.mkdir(parents=True)
    _ = (directory / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/direct": {"version": "1", "license": "MIT"},
                    "node_modules/optional-transitive": {
                        "version": "2",
                        "license": "AGPL-3.0-only",
                        "optional": True,
                        "os": ["win32"],
                    },
                    "node_modules/unknown": {"version": "3"},
                },
            }
        )
    )
    failures = audit.audit_npm(tmp_path)
    assert len(failures) == 2
    assert "optional-transitive" in failures[0]
    assert "unknown" in failures[1]


def test_registry_errors_fail_the_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*_args: object) -> NoReturn:
        raise OSError("registry unavailable")

    monkeypatch.setattr(audit, "fetch_metadata", unavailable)
    assert "registry unavailable" in audit.audit_python(("transitive", "1"))


def test_metadata_identity_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def response(*_args: object, **_kwargs: object) -> io.BytesIO:
        return io.BytesIO(
            json.dumps({"info": {"name": "other", "version": "1"}}).encode()
        )

    monkeypatch.setattr(urllib.request, "urlopen", response)
    with pytest.raises(ValueError, match="identity mismatch"):
        _ = audit.fetch_metadata("expected", "1")


def test_main_returns_failure_on_incomplete_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["audit", "--root", str(tmp_path)])
    assert audit.main() == 1


def test_build_resolution_covers_transitives_without_installing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["backend>=1,<2"]\n'
    )
    _ = (tmp_path / "mise.toml").write_text(
        '[tools]\npython = ["3.12.15", "3.13.16"]\n'
    )

    def compile_requirements(command: list[str], **kwargs: Any):
        assert "--universal" in command
        assert "--no-build" in command
        assert kwargs["input"] == "backend>=1,<2\n"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='[[packages]]\nname = "backend"\nversion = "1.2"\n[[packages]]\nname = "build-transitive"\nversion = "3.4"\n',
        )

    monkeypatch.setattr(subprocess, "run", compile_requirements)
    assert audit.build_packages(tmp_path) == [
        ("backend", "1.2"),
        ("build-transitive", "3.4"),
    ]


def test_build_resolution_rejects_unreviewed_sources(tmp_path: Path) -> None:
    _ = (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["backend @ https://example.invalid/backend.tar.gz"]\n'
    )
    with pytest.raises(ValueError, match="unreviewed build-system"):
        _ = audit.build_packages(tmp_path)
