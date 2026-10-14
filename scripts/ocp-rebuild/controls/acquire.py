#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Retained, source-only acquisition/audit helper for the disposable #226 probe."""

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def audit():
    names = re.findall(
        r"^([a-zA-Z0-9_.-]+)==([^\s;\\]+)", (ROOT / "tools.lock").read_text(), re.M
    )
    records = []
    directory = ROOT / "tool-metadata"
    directory.mkdir(exist_ok=True)
    for name, version in names:
        raw = fetch(f"https://pypi.org/pypi/{name}/{version}/json")
        (directory / f"{name}-{version}.json").write_bytes(raw)
        document = json.loads(raw)
        info = document["info"]
        classifiers = [item for item in info["classifiers"] if "License" in item]
        record = {
            "name": name,
            "version": version,
            "expression": info.get("license_expression"),
            "license": info.get("license"),
            "classifiers": classifiers,
            "requires_dist": info.get("requires_dist"),
            "metadata_sha256": hashlib.sha256(raw).hexdigest(),
        }
        records.append(record)
        print(
            json.dumps(
                {
                    key: record[key]
                    for key in (
                        "name",
                        "version",
                        "expression",
                        "license",
                        "classifiers",
                    )
                }
            )
        )
    (ROOT / "tool-audit.json").write_text(json.dumps(records, indent=2) + "\n")


def download(manifest):
    records = json.loads(Path(manifest).read_text())
    directory = ROOT / "downloads"
    directory.mkdir(exist_ok=True)
    completed = []
    for record in records:
        path = directory / record["name"]
        raw = path.read_bytes() if path.exists() else fetch(record["url"])
        digest = hashlib.sha256(raw).hexdigest()
        if record.get("sha256") and digest != record["sha256"]:
            raise ValueError(f"Hash mismatch: {record['name']}")
        if record.get("sha512") and hashlib.sha512(raw).hexdigest() != record["sha512"]:
            raise ValueError(f"SHA-512 mismatch: {record['name']}")
        path.write_bytes(raw)
        completed.append({**record, "sha256": digest, "bytes": len(raw)})
        (ROOT / "download-results.json").write_text(
            json.dumps(completed, indent=2) + "\n"
        )
        print(record["name"], len(raw), digest, flush=True)


if sys.argv[1] == "audit":
    audit()
elif sys.argv[1] == "download":
    download(sys.argv[2])
else:
    raise SystemExit("Expected audit or download")
