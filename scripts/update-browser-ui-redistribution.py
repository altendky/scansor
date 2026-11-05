#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refresh known UI notice identities without approving new notice/license bases.

Run with ``uv run --no-project scripts/update-browser-ui-redistribution.py``.
Changed notice bytes, uncovered runtime packages and custom-source changes need
a reviewed inventory update; there is intentionally no approval override flag.
"""

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "scripts/browser-redistribution-lock.json"
PACKAGE_LOCK = ROOT / "experiments/browser_viewer/package-lock.json"
HELPERS = {"vite", "rolldown"}
FIELDS = ("version", "resolved", "integrity", "license")
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
HEADLESS = "@headless-tree/core"
HEADLESS_REPOSITORIES = {
    "git+ssh://git@github.com/lukasbach/headless-tree.git",
    "git+https://github.com/lukasbach/headless-tree.git",
    "https://github.com/lukasbach/headless-tree",
}
Fetch = Callable[[str, int], bytes]


def checked_url(url: str, host: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"unreviewed notice source URL: {url}")


def fetch_https(url: str, limit: int) -> bytes:
    host = urllib.parse.urlsplit(url).hostname
    if host not in {"registry.npmjs.org", "raw.githubusercontent.com"}:
        raise ValueError(
            "notice refresh only supports the reviewed npm/publisher hosts"
        )
    checked_url(url, host)

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            checked_url(newurl, host)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(Redirects())
    with opener.open(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"notice input exceeds its bounded size: {url}")
    return data


def identity(name: str, package: dict[str, Any]) -> dict[str, str]:
    result = {"name": name}
    for key in FIELDS:
        value = package.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name}: missing locked {key}; review package provenance")
        result[key] = value
    return result


def packages(lock: dict[str, Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise ValueError("expected the reviewed npm v3 package-lock format")
    runtime: dict[str, dict] = {}
    helpers: dict[str, dict] = {}
    for path, package in lock["packages"].items():
        if not path:
            continue
        if "node_modules/" not in path or package.get("link"):
            raise ValueError(f"unreviewed package source: {path}")
        name = path.rsplit("node_modules/", 1)[-1]
        if package.get("dev", False) and name not in HELPERS:
            continue
        current = identity(name, package)
        selections = [helpers] if name in HELPERS else []
        if not package.get("dev", False):
            selections.append(runtime)
        for selected in selections:
            if name in selected and selected[name] != current:
                raise ValueError(
                    f"{name}: multiple versions need reviewed notice coverage"
                )
            selected[name] = current
    if set(helpers) != HELPERS:
        raise ValueError("the reviewed Vite/Rolldown helper coverage changed")
    return runtime, helpers


def root_notices(archive: tarfile.TarFile) -> set[str]:
    return {
        member.name
        for member in archive.getmembers()
        if member.name.count("/") == 1
        and re.fullmatch(
            r"(?i)package/(?:licen[sc]e|notice|copying|copyright)(?:[._-].*)?",
            member.name,
        )
    }


def verified_archive(
    name: str, current: dict, fetch: Fetch
) -> tuple[bytes, tarfile.TarFile, dict]:
    endpoint = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="")
    endpoint += "/" + urllib.parse.quote(current["version"], safe="")
    metadata = json.loads(fetch(endpoint, MAX_METADATA))
    if any(metadata.get(key) != current[key] for key in ("name", "version", "license")):
        raise ValueError(
            f"{name}: publisher metadata changed the reviewed license/source basis"
        )
    dist = metadata.get("dist", {})
    if (
        dist.get("tarball") != current["resolved"]
        or dist.get("integrity") != current["integrity"]
    ):
        raise ValueError(
            f"{name}: publisher metadata does not match the locked source/integrity"
        )
    checked_url(current["resolved"], "registry.npmjs.org")
    expected = f"/{name}/-/{name.rsplit('/', 1)[-1]}-{current['version']}.tgz"
    if (
        urllib.parse.unquote(urllib.parse.urlsplit(current["resolved"]).path)
        != expected
    ):
        raise ValueError(f"{name}: unreviewed npm archive source basis")
    raw = fetch(current["resolved"], MAX_ARCHIVE)
    match = re.fullmatch(
        r"(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})", current["integrity"]
    )
    if match is None:
        raise ValueError(f"{name}: unsupported locked integrity")
    expected_digest = base64.b64decode(match[2], validate=True)
    if hashlib.new(match[1], raw).digest() != expected_digest:
        raise ValueError(f"{name}: archive integrity mismatch")
    return raw, tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz"), metadata


def replace_paths(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [replace_paths(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: replace_paths(item, replacements) for key, item in value.items()}
    return value


def refresh(inventory: dict, lock_bytes: bytes, fetch: Fetch = fetch_https) -> dict:
    """Return a new inventory only after all changed known notices verify."""
    runtime, helpers = packages(json.loads(lock_bytes))
    reviewed_list = inventory["identities"]["ui_packages"]
    reviewed = {entry["name"]: entry for entry in reviewed_list}
    if len(reviewed) != len(reviewed_list) or set(reviewed) != set(runtime):
        unknown = sorted(set(runtime) - set(reviewed))
        removed = sorted(set(reviewed) - set(runtime))
        raise ValueError(
            f"unreviewed runtime notice coverage: new={unknown}, removed={removed}"
        )

    result = copy.deepcopy(inventory)
    records = {
        record["component"]: record
        for record in result["archives"]
        if record.get("group") == "ui"
    }
    for name in HELPERS:
        if name not in records:
            raise ValueError(f"{name}: missing reviewed helper notice")
        record = records[name]
        reviewed[name] = identity(name, record | {"resolved": record["url"]})
    current_packages = runtime | helpers
    replacements: dict[str, str] = {}
    for name, current in sorted(current_packages.items()):
        previous = reviewed[name]
        if current == previous:
            continue
        if current["license"] != previous["license"]:
            raise ValueError(f"{name}: license change needs deliberate policy review")
        if current["version"] == previous["version"]:
            raise ValueError(
                f"{name}: same-version source/integrity change needs review"
            )
        if name == HEADLESS:
            candidates = [
                record
                for record in result["files"]
                if record.get("group") == "ui"
                and record["path"] == f"notices/ui/{name}/{previous['version']}/LICENSE"
            ]
            if (
                len(candidates) != 1
                or candidates[0].get("basis") != "Exact npm gitHead publisher notice"
            ):
                raise ValueError(f"{name}: unreviewed publisher notice basis")
            record = candidates[0]
            if not re.fullmatch(
                r"https://raw\.githubusercontent\.com/lukasbach/headless-tree/[0-9a-f]{40}/LICENSE",
                record["url"],
            ):
                raise ValueError(f"{name}: unreviewed publisher notice source")
        else:
            record = records.get(name)
            if record is None or not record["url"].startswith(
                "https://registry.npmjs.org/"
            ):
                raise ValueError(
                    f"{name}: custom notice source needs a reviewed inventory update"
                )
            expected_previous = identity(name, record | {"resolved": record["url"]})
            if expected_previous != previous:
                raise ValueError(f"{name}: reviewed archive and identity disagree")

        raw, archive, metadata = verified_archive(name, current, fetch)
        with archive:
            if name == HEADLESS:
                head = metadata.get("gitHead", "")
                if (
                    metadata.get("repository", {}).get("url")
                    not in HEADLESS_REPOSITORIES
                    or not re.fullmatch(r"[0-9a-f]{40}", head)
                    or root_notices(archive)
                ):
                    raise ValueError(
                        f"{name}: publisher notice correspondence needs review"
                    )
                url = f"https://raw.githubusercontent.com/lukasbach/headless-tree/{head}/LICENSE"
                notice = fetch(url, MAX_METADATA)
                if (
                    hashlib.sha256(notice).hexdigest() != record["sha256"]
                    or len(notice) != record["bytes"]
                ):
                    raise ValueError(
                        f"{name}: changed publisher notice needs a reviewed inventory update"
                    )
                old_path = record["path"]
                record["path"] = f"notices/ui/{name}/{current['version']}/LICENSE"
                record["url"] = url
                replacements[old_path] = record["path"]
            else:
                expected_notices = {
                    member["archive_path"]
                    for member in record["members"]
                    if member["archive_path"].count("/") == 1
                }
                if root_notices(archive) != expected_notices:
                    raise ValueError(
                        f"{name}: changed archive notice coverage needs review"
                    )
                for member in record["members"]:
                    matches = [
                        entry
                        for entry in archive.getmembers()
                        if entry.name == member["archive_path"]
                    ]
                    if (
                        len(matches) != 1
                        or not matches[0].isfile()
                        or matches[0].size > MAX_METADATA
                    ):
                        raise ValueError(
                            f"{name}: missing/unsafe reviewed notice member"
                        )
                    stream = archive.extractfile(matches[0])
                    assert stream is not None
                    notice = stream.read()
                    if (
                        hashlib.sha256(notice).hexdigest() != member["sha256"]
                        or len(notice) != member["bytes"]
                    ):
                        raise ValueError(
                            f"{name}: changed notice bytes need a reviewed inventory update"
                        )
                    old_path = member["path"]
                    before = f"/{previous['version']}/"
                    if old_path.count(before) != 1:
                        raise ValueError(f"{name}: unreviewed notice path convention")
                    member["path"] = old_path.replace(before, f"/{current['version']}/")
                    replacements[old_path] = member["path"]
                record.update(
                    version=current["version"],
                    integrity=current["integrity"],
                    url=current["resolved"],
                )
                if "bytes" in record:
                    record["bytes"] = len(raw)
                if "sha256" in record:
                    record["sha256"] = hashlib.sha256(raw).hexdigest()

    result["requirements"] = replace_paths(result["requirements"], replacements)
    result["identities"]["ui_packages"] = [
        runtime[entry["name"]] for entry in reviewed_list
    ]
    result["bindings"]["ui_package_lock_sha256"] = hashlib.sha256(
        lock_bytes
    ).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="refuse stale inventory without writing"
    )
    args = parser.parse_args()
    temporary = None
    try:
        if INVENTORY.is_symlink() or PACKAGE_LOCK.is_symlink():
            raise ValueError("notice refresh inputs must be regular repository files")
        original = INVENTORY.read_bytes()
        updated = refresh(json.loads(original), PACKAGE_LOCK.read_bytes(), fetch_https)
        encoded = (json.dumps(updated, indent=2, sort_keys=True) + "\n").encode()
        if encoded == original:
            print("Reviewed browser UI notices already match the package lock.")
            return 0
        if args.check:
            raise ValueError("browser UI notice inventory needs verified regeneration")
        with tempfile.NamedTemporaryFile(
            dir=INVENTORY.parent, prefix=INVENTORY.name + ".", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, INVENTORY)
        print(
            "Refreshed reviewed browser UI notices; native/numeric/CAD records preserved."
        )
        return 0
    except (ValueError, OSError, KeyError, tarfile.TarError) as error:
        print(f"browser UI notice refresh refused: {error}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
