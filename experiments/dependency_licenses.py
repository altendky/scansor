"""Fail closed on unreviewed license metadata in the maintained dependency locks.

Use PyPI's version-specific JSON API for every uv.lock package (including dev and
platform-specific dependencies) and the committed npm lock's license metadata.
This metadata gate supplements the separate native/browser redistribution proof.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# A deliberately small set of understood SPDX identifiers. New identifiers and
# exception clauses require review rather than being assumed acceptable.
LICENSES = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "ISC",
        "MIT",
        "MPL-2.0",
        "PSF-2.0",
        "Unlicense",
        "Zlib",
    }
)
LEGACY = {"PSFL": "PSF-2.0"}
CLASSIFIERS = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF-2.0",
    # This classifier identifies the BSD family, not a particular SPDX variant.
    "License :: OSI Approved :: BSD License": "BSD family (legacy classifier)",
}
PROHIBITED = re.compile(
    r"\b(?:A|L)?GPL\b|(?:AFFERO\s+|LESSER\s+)?GENERAL\s+PUBLIC\s+LICEN[CS]E", re.I
)


def check_expression(expression: str) -> str:
    """Accept understood expressions only; do not silently select a GPL alternative."""
    if not expression or PROHIBITED.search(expression):
        raise ValueError("missing or GPL/LGPL/AGPL license; user review required")
    expression = LEGACY.get(expression, expression)
    tokens = re.findall(r"\(|\)|[^\s()]+", expression)
    # A small parser validates syntax as well as identifiers. All branches must
    # be understood, even when an OR expression offers a permissive alternative.
    expect_license = True
    depth = 0
    for token in tokens:
        if expect_license and token == "(":
            depth += 1
        elif not expect_license and token == ")" and depth:
            depth -= 1
        elif expect_license and token in LICENSES:
            expect_license = False
        elif not expect_license and token in {"AND", "OR"}:
            expect_license = True
        else:
            raise ValueError(f"unreviewed license expression: {expression!r}")
    if depth or expect_license:
        raise ValueError(f"invalid license expression: {expression!r}")
    return expression


def metadata_license(info: dict[str, Any]) -> str:
    """Prefer SPDX metadata; accept specific legacy classifiers when needed."""
    classifiers = [
        item for item in info.get("classifiers", []) if item.startswith("License ::")
    ]
    if any(PROHIBITED.search(item) for item in classifiers):
        raise ValueError("GPL/LGPL/AGPL classifier; user review required")
    expression = info.get("license_expression")
    if expression:
        return check_expression(expression)
    legacy = (info.get("license") or "").strip()
    if legacy and legacy != "UNKNOWN":
        # Free-form license prose is not an SPDX assertion. Do not guess from a
        # classifier if conflicting or unrecognized license text was supplied.
        return check_expression(legacy)
    recognized = [CLASSIFIERS[item] for item in classifiers if item in CLASSIFIERS]
    if len(recognized) != 1:
        raise ValueError("missing or unreviewed Python license metadata")
    return recognized[0]


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_packages(root: Path) -> list[tuple[str, str]]:
    """Include all locked versions, not just packages installed on this runner."""
    lock = tomllib.loads((root / "uv.lock").read_text())
    packages: list[tuple[str, str]] = []
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    baseline = json.loads(
        (root / "scripts/dependency-license-native-baseline.json").read_text()
    )["python"]
    for package in lock["package"]:
        name, version = package["name"], package["version"]
        if name == project["name"] and package["source"] == {"editable": "."}:
            continue
        if package["source"] != {"registry": "https://pypi.org/simple"}:
            raise ValueError(f"{name}=={version}: unreviewed Python source")
        if name.startswith("cadquery-ocp"):
            artifacts = package.get("wheels", []) + (
                [package["sdist"]] if package.get("sdist") else []
            )
            expected = baseline.get(name, {})
            if version != expected.get("version") or sorted(
                item["hash"] for item in artifacts
            ) != expected.get("hashes"):
                raise ValueError(
                    f"{name}=={version}: native kernel artifacts changed; explicit license/approval review required"
                )
        packages.append((name, version))
    if not packages:
        raise ValueError("uv.lock has no external packages to check")
    return sorted(set(packages))


def build_packages(root: Path) -> list[tuple[str, str]]:
    """Resolve the range-based build requirements and transitives without builds."""
    manifest = tomllib.loads((root / "pyproject.toml").read_text())
    requirements = manifest["build-system"]["requires"]
    if not requirements or any(
        not isinstance(item, str)
        or re.fullmatch(r"[A-Za-z0-9_.<>=!~,* -]+", item) is None
        for item in requirements
    ):
        raise ValueError("unreviewed build-system requirements/source")
    primary = tomllib.loads((root / "mise.toml").read_text())["tools"]["python"][0]
    result = subprocess.run(
        [
            "uv",
            "pip",
            "compile",
            "--universal",
            "--no-build",
            "--no-config",
            "--format",
            "pylock.toml",
            "--python-version",
            primary,
            "--default-index",
            "https://pypi.org/simple",
            "-",
        ],
        input="\n".join(requirements) + "\n",
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    resolution = tomllib.loads(result.stdout)
    packages = resolution["packages"]
    if not packages or any(
        not item.get("version")
        or any(key in item for key in ("directory", "vcs", "archive"))
        for item in packages
    ):
        raise ValueError("incomplete or unreviewed build dependency resolution")
    return sorted({(item["name"], item["version"]) for item in packages})


def fetch_metadata(name: str, version: str) -> dict[str, Any]:
    # Only the fixed trusted origin is fetched; lockfile URLs are not executed.
    url = "https://pypi.org/pypi/{}/{}/json".format(
        urllib.parse.quote(name, safe=""), urllib.parse.quote(version, safe="")
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "scansor-license-check/1"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        info = json.load(response)["info"]
    if (
        normalize_name(info["name"]) != normalize_name(name)
        or info["version"] != version
    ):
        raise ValueError(f"{name}=={version}: PyPI metadata identity mismatch")
    return info


def audit_python(package: tuple[str, str]) -> str:
    name, version = package
    try:
        license_expression = metadata_license(fetch_metadata(name, version))
    except Exception as error:
        # Retrieval errors fail the gate too; no success on an incomplete audit.
        return f"python {name}=={version}: {error}"
    print(f"python {name}=={version}: {license_expression}")
    return ""


def audit_npm(root: Path) -> list[str]:
    lock = json.loads(
        (root / "experiments/browser_viewer/package-lock.json").read_text()
    )
    if lock.get("lockfileVersion") != 3 or not lock.get("packages"):
        raise ValueError("expected npm lockfile version 3 with package metadata")
    failures: list[str] = []
    for path, package in lock["packages"].items():
        if not path:
            continue  # The private workspace, not a dependency.
        label = f"npm {path}=={package.get('version', '?')}"
        try:
            expression = check_expression(package.get("license", ""))
        except ValueError as error:
            failures.append(f"{label}: {error}")
        else:
            print(f"{label}: {expression}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        failures = audit_npm(args.root)
        packages = sorted(set(python_packages(args.root) + build_packages(args.root)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            failures.extend(filter(None, pool.map(audit_python, packages)))
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as error:
        failures = [f"incomplete dependency license audit: {error}"]
    if failures:
        print(
            "Dependency license review required:\n" + "\n".join(failures),
            file=sys.stderr,
        )
        return 1
    print("All maintained locked Python and npm dependency licenses passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
