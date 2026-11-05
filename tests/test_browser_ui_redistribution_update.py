"""Companion maintenance cannot approve new packages, notices or source bases."""

import base64
import copy
import hashlib
import io
import json
import runpy
import sys
import tarfile
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

type Scenario = tuple[
    dict[str, Any],
    dict[str, Any],
    Callable[..., dict[str, Any]],
    Callable[[str, int], bytes],
    dict[str, bytes],
]

ROOT = Path(__file__).resolve().parents[1]
UPDATER = runpy.run_path(str(ROOT / "scripts/update-browser-ui-redistribution.py"))
REFRESH = UPDATER["refresh"]
HEADLESS = "@headless-tree/core"
VERSIONS = {
    "three": ("0.186.1", "0.187.0"),
    "@dnd-kit/dom": ("0.5.0", "0.5.3"),
    "@preact/signals-core": ("1.14.4", "1.14.5"),
    "react": ("19.3.0", "19.3.1"),
    "react-dom": ("19.3.0", "19.3.1"),
    HEADLESS: ("1.7.0", "1.8.0"),
    "vite": ("8.3.4", "8.3.6"),
    "rolldown": ("1.2.12", "1.2.16"),
}


def no_acquisition(_url: str, _limit: int) -> bytes:
    pytest.fail("unchanged or uncovered notices must not be acquired")


def encoded(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def archive_bytes(notices: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, value in notices.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return output.getvalue()


def package(name: str, version: str, raw: bytes) -> dict[str, Any]:
    return {
        "version": version,
        "resolved": f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz",
        "integrity": "sha512-"
        + base64.b64encode(hashlib.sha512(raw).digest()).decode(),
        "license": "MIT",
    }


@pytest.fixture
def scenario() -> Scenario:
    inventory: dict[str, Any] = {
        "archives": [{"group": "numeric", "native": {"approved": "unchanged"}}],
        "files": [
            {"group": "cad", "path": "approved/native/LICENSE", "sha256": "native"}
        ],
        "identities": {"cad": {"approved_commit": "a" * 40}, "ui_packages": []},
        "bindings": {"runtime_lock_sha256": "a" * 64},
        "requirements": {
            "native-source-proof": {"basis": "existing approval"},
            "runtime-npm-notices": {"paths": []},
        },
        "requirement_groups": {
            "cad": ["native-source-proof"],
            "ui": ["runtime-npm-notices"],
        },
        "covered_sources": [{"native": "approved unchanged source archive"}],
        "distribution_inputs": [{"native": "approved unchanged rebuild controls"}],
    }
    lock: dict[str, Any] = {"lockfileVersion": 3, "packages": {}}
    for name, (old, _) in VERSIONS.items():
        notice = f"Reviewed MIT notice for {name}\n".encode()
        raw = archive_bytes({"package/LICENSE": notice})
        current = package(name, old, raw)
        lock["packages"]["node_modules/" + name] = current | {
            "dev": name in {"vite", "rolldown"}
        }
        path = f"notices/ui-notices/{name}/{old}/LICENSE"
        if name == HEADLESS:
            path = f"notices/ui/{name}/{old}/LICENSE"
            inventory["files"].append(
                {
                    "group": "ui",
                    "path": path,
                    "url": "https://raw.githubusercontent.com/lukasbach/headless-tree/"
                    + "1" * 40
                    + "/LICENSE",
                    "basis": "Exact npm gitHead publisher notice",
                    "bytes": len(notice),
                    "sha256": hashlib.sha256(notice).hexdigest(),
                }
            )
        else:
            inventory["archives"].append(
                {
                    "group": "ui",
                    "component": name,
                    "version": old,
                    "url": current["resolved"],
                    "integrity": current["integrity"],
                    "license": "MIT",
                    "members": [
                        {
                            "archive_path": "package/LICENSE",
                            "path": path,
                            "bytes": len(notice),
                            "sha256": hashlib.sha256(notice).hexdigest(),
                        }
                    ],
                }
            )
        inventory["requirements"]["runtime-npm-notices"]["paths"].append(path)
        if name not in {"vite", "rolldown"}:
            inventory["identities"]["ui_packages"].append({"name": name} | current)
    inventory["bindings"]["ui_package_lock_sha256"] = hashlib.sha256(
        encoded(lock)
    ).hexdigest()
    responses: dict[str, bytes] = {}

    def change(
        name: str,
        *,
        notice: bytes | None = None,
        extra_notice: bool = False,
        nested_notice: str | None = None,
    ) -> dict[str, Any]:
        _, version = VERSIONS[name]
        content = (
            notice
            if notice is not None
            else f"Reviewed MIT notice for {name}\n".encode()
        )
        files = {"package/LICENSE": content}
        if name == HEADLESS:
            files = {"package/package.json": b"{}"}
        if extra_notice:
            files["package/NOTICE"] = b"New, unreviewed notice\n"
        if nested_notice is not None:
            files[nested_notice] = b"SPDX-License-Identifier: GPL-3.0-only\n"
        raw = archive_bytes(files)
        current = package(name, version, raw)
        lock["packages"]["node_modules/" + name].update(current)
        endpoint = (
            "https://registry.npmjs.org/"
            + urllib.parse.quote(name, safe="")
            + "/"
            + version
        )
        metadata = {
            "name": name,
            "version": version,
            "license": "MIT",
            "dist": {"tarball": current["resolved"], "integrity": current["integrity"]},
        }
        if name == HEADLESS:
            metadata |= {
                "gitHead": "2" * 40,
                "repository": {
                    "url": "git+ssh://git@github.com/lukasbach/headless-tree.git"
                },
            }
            responses[
                "https://raw.githubusercontent.com/lukasbach/headless-tree/"
                + "2" * 40
                + "/LICENSE"
            ] = content
        responses[endpoint] = encoded(metadata)
        responses[current["resolved"]] = raw
        return current

    def fetch(url: str, limit: int) -> bytes:
        value = responses[url]
        assert len(value) <= limit
        return value

    return inventory, lock, change, fetch, responses


def assert_native_unchanged(before: dict[str, Any], after: dict[str, Any]) -> None:
    for key in ("covered_sources", "distribution_inputs", "requirement_groups"):
        assert after[key] == before[key]
    assert (
        after["bindings"]["runtime_lock_sha256"]
        == before["bindings"]["runtime_lock_sha256"]
    )
    assert after["identities"]["cad"] == before["identities"]["cad"]
    assert (
        after["requirements"]["native-source-proof"]
        == before["requirements"]["native-source-proof"]
    )
    for kind in ("archives", "files"):
        assert [r for r in after[kind] if r.get("group") != "ui"] == [
            r for r in before[kind] if r.get("group") != "ui"
        ]


@pytest.mark.parametrize(
    "names",
    [
        ("three",),
        ("@dnd-kit/dom", "@preact/signals-core"),
        (HEADLESS,),
        ("react", "react-dom"),
    ],
)
def test_four_runtime_update_shapes_verify_notices_and_preserve_native(
    scenario: Scenario, names: tuple[str, ...]
) -> None:
    inventory, lock, change, fetch, _ = scenario
    original = copy.deepcopy(inventory)
    for name in names:
        _ = change(name)
    result = REFRESH(inventory, encoded(lock), fetch)
    assert inventory == original
    assert_native_unchanged(original, result)
    assert (
        result["bindings"]["ui_package_lock_sha256"]
        == hashlib.sha256(encoded(lock)).hexdigest()
    )
    identities = {r["name"]: r for r in result["identities"]["ui_packages"]}
    for name in names:
        assert identities[name]["version"] == VERSIONS[name][1]
        assert any(
            f"/{name}/{VERSIONS[name][1]}/LICENSE" in path
            for path in result["requirements"]["runtime-npm-notices"]["paths"]
        )
    if HEADLESS in names:
        record = next(r for r in result["files"] if r.get("group") == "ui")
        assert "/" + "2" * 40 + "/LICENSE" in record["url"]
        assert record["basis"] == "Exact npm gitHead publisher notice"


def test_changed_notice_bytes_cannot_be_approved_by_refresh(scenario: Scenario) -> None:
    inventory, lock, change, fetch, _ = scenario
    before = copy.deepcopy(inventory)
    _ = change("vite", notice=b"Changed license obligations\n")
    with pytest.raises(ValueError, match="changed notice bytes"):
        REFRESH(inventory, encoded(lock), fetch)
    assert inventory == before


def test_corrupt_archive_refuses_before_notice_intake(scenario: Scenario) -> None:
    inventory, lock, change, fetch, responses = scenario
    current = change("three")
    responses[current["resolved"]] = b"not the integrity-verified archive"
    with pytest.raises(ValueError, match="archive integrity mismatch"):
        REFRESH(inventory, encoded(lock), fetch)


def test_new_mit_transitive_runtime_package_is_uncovered(scenario: Scenario) -> None:
    inventory, lock, _, _, _ = scenario
    lock["packages"]["node_modules/new-transitive"] = package(
        "new-transitive", "1.0.0", b"new"
    )
    with pytest.raises(
        ValueError, match=r"unreviewed runtime notice coverage.*new-transitive"
    ):
        REFRESH(
            inventory,
            encoded(lock),
            no_acquisition,
        )


def test_build_helper_moving_to_runtime_needs_explicit_coverage(
    scenario: Scenario,
) -> None:
    inventory, lock, _, fetch, _ = scenario
    lock["packages"]["node_modules/vite"]["dev"] = False
    with pytest.raises(ValueError, match="unreviewed runtime notice coverage"):
        REFRESH(inventory, encoded(lock), fetch)


@pytest.mark.parametrize(
    "field,value",
    [
        ("license", "LicenseRef-Unreviewed"),
        ("integrity", "sha512-changed"),
        ("resolved", "https://unreviewed.example/three.tgz"),
    ],
)
def test_license_or_same_version_source_changes_refuse(
    scenario: Scenario, field: str, value: str
) -> None:
    inventory, lock, _, fetch, _ = scenario
    lock["packages"]["node_modules/three"][field] = value
    with pytest.raises(
        ValueError, match=r"license change|same-version source/integrity"
    ):
        REFRESH(inventory, encoded(lock), fetch)


def test_new_archive_notice_member_requires_review(scenario: Scenario) -> None:
    inventory, lock, change, fetch, _ = scenario
    _ = change("three", extra_notice=True)
    with pytest.raises(ValueError, match="changed archive notice coverage"):
        REFRESH(inventory, encoded(lock), fetch)


@pytest.mark.parametrize(
    "name,path",
    [
        ("three", "package/vendor/LICENSE"),
        ("three", "package/vendor/nested/NOTICE.txt"),
        ("three", "package/vendor/COPYING.LESSER"),
        ("three", "package/vendor/LICENSES/GPL-3.0-only.txt"),
        ("three", "package/vendor/NOTICES/third-party.txt"),
        (HEADLESS, "package/vendor/LICENSE"),
    ],
)
def test_unreviewed_nested_notice_refuses_with_root_notice_unchanged(
    scenario: Scenario, name: str, path: str
) -> None:
    inventory, lock, change, fetch, _ = scenario
    before = copy.deepcopy(inventory)
    _ = change(name, nested_notice=path)
    with pytest.raises(
        ValueError,
        match=r"changed archive notice coverage|publisher notice correspondence",
    ):
        REFRESH(inventory, encoded(lock), fetch)
    assert inventory == before


def test_custom_correspondence_basis_is_never_automatically_replaced(
    scenario: Scenario,
) -> None:
    inventory, lock, change, fetch, _ = scenario
    record = next(r for r in inventory["archives"] if r.get("component") == "three")
    record["url"] = (
        "https://raw.githubusercontent.com/reviewed/publisher/commit/LICENSE"
    )
    _ = change("three")
    with pytest.raises(ValueError, match="custom notice source"):
        REFRESH(inventory, encoded(lock), fetch)


def test_cli_refusal_does_not_write_even_after_other_components_verify(
    scenario: Scenario, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, lock, change, fetch, _ = scenario
    _ = change("@dnd-kit/dom")
    _ = change("vite", notice=b"Unreviewed replacement notice\n")
    source = tmp_path / "inventory.json"
    _ = source.write_bytes(encoded(inventory))
    package_lock = tmp_path / "package-lock.json"
    _ = package_lock.write_bytes(encoded(lock))
    main = UPDATER["main"]
    monkeypatch.setitem(main.__globals__, "INVENTORY", source)
    monkeypatch.setitem(main.__globals__, "PACKAGE_LOCK", package_lock)
    monkeypatch.setitem(main.__globals__, "fetch_https", fetch)
    monkeypatch.setattr(sys, "argv", ["update-browser-ui-redistribution.py"])
    assert main() == 1
    assert source.read_bytes() == encoded(inventory)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "inventory.json",
        "package-lock.json",
    ]


def test_checked_in_inventory_has_complete_current_runtime_coverage() -> None:
    inventory = json.loads(
        (ROOT / "scripts/browser-redistribution-lock.json").read_bytes()
    )
    lock = (ROOT / "experiments/browser_viewer/package-lock.json").read_bytes()
    assert (
        REFRESH(
            inventory,
            lock,
            no_acquisition,
        )
        == inventory
    )


def test_cli_check_refuses_stale_then_refresh_is_idempotent(
    scenario: Scenario, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, lock, change, fetch, _ = scenario
    _ = change("three")
    source = tmp_path / "inventory.json"
    _ = source.write_bytes(encoded(inventory))
    package_lock = tmp_path / "package-lock.json"
    _ = package_lock.write_bytes(encoded(lock))
    main = UPDATER["main"]
    monkeypatch.setitem(main.__globals__, "INVENTORY", source)
    monkeypatch.setitem(main.__globals__, "PACKAGE_LOCK", package_lock)
    monkeypatch.setitem(main.__globals__, "fetch_https", fetch)
    monkeypatch.setattr(sys, "argv", ["update-browser-ui-redistribution.py", "--check"])
    assert main() == 1
    assert source.read_bytes() == encoded(inventory)
    monkeypatch.setattr(sys, "argv", ["update-browser-ui-redistribution.py"])
    assert main() == 0
    updated = source.read_bytes()
    assert_native_unchanged(inventory, json.loads(updated))
    monkeypatch.setitem(main.__globals__, "fetch_https", no_acquisition)
    monkeypatch.setattr(sys, "argv", ["update-browser-ui-redistribution.py", "--check"])
    assert main() == 0
    assert source.read_bytes() == updated


@pytest.mark.parametrize(
    "url",
    [
        "http://registry.npmjs.org/package",
        "https://user:password@registry.npmjs.org/package",
        "https://registry.npmjs.org.evil.example/package",
    ],
)
def test_notice_downloads_require_reviewed_https_authority(url: str) -> None:
    with pytest.raises(ValueError, match="unreviewed notice source"):
        UPDATER["checked_url"](url, "registry.npmjs.org")
