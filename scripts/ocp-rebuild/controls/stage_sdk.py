#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Stage and inventory the pinned compiler payload without running it."""

import hashlib
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
p = ROOT / "downloads/sdk-binaries.tar.xz"
assert (
    hashlib.sha256(p.read_bytes()).hexdigest()
    == "7ae996db24911edc535cccf069fb8a3ed63e8dac7abe58e758431abfe3255165"
)
target = ROOT / "toolchain/emsdk/upstream"
target.mkdir()
with tarfile.open(p) as archive:
    names = archive.getnames()
    (ROOT / "logs/sdk-archive-members.json").write_text(
        json.dumps(names, indent=2) + "\n"
    )
    archive.extractall(target, filter="data")
wrapper = target / "install"
assert wrapper.is_dir()
for child in wrapper.iterdir():
    child.rename(target / child.name)
wrapper.rmdir()
inventory = []
for p in sorted(target.rglob("*")):
    if p.is_file() and any(
        key in p.name.lower() for key in ("license", "copying", "notice", "copyright")
    ):
        raw = p.read_bytes()
        inventory.append(
            {
                "path": str(p.relative_to(ROOT)),
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
(ROOT / "logs/sdk-license-inventory.json").write_text(
    json.dumps(inventory, indent=2) + "\n"
)
print("SDK staged", len(names), "members", len(inventory), "notice files", flush=True)
print([item["path"] for item in inventory[:25]])
