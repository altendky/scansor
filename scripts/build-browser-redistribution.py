#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Assemble pinned redistribution notices without installing or running packages.

Inputs are a curated supplemental inventory, not a license detector. Archive
contents are inspected as metadata; only explicitly pinned notice members are
copied. Covered-source archives are preserved intact, never unpacked. A cache
contains downloaded inputs, not an installed SDK. The output contains no SDK or
compiler binaries. An external requirement stays incomplete until a consuming
runtime/app builder supplies its evidence.
"""

import argparse
import base64
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, cast

CHUNK = 1024 * 1024
RESERVED = {"manifest.json", "inventory.json", "failure.json", "provenance"}
CODE_SUFFIXES = {
    ".a",
    ".c",
    ".cc",
    ".cpp",
    ".dll",
    ".exe",
    ".h",
    ".hpp",
    ".js",
    ".mjs",
    ".o",
    ".py",
    ".rs",
    ".so",
    ".wasm",
}


def object_value(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def array_value(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return cast(list[object], value)


def text_value(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def size_value(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return value


def canonical_path(value: object, label: str, *, directory: bool = False) -> str:
    raw = text_value(value, label)
    if directory:
        raw = raw.rstrip("/")
    if (
        not raw
        or PurePosixPath(raw).is_absolute()
        or any(p in ("", ".", "..") for p in raw.split("/"))
        or any(c in raw for c in "\\:\0?#")
        or any(p.startswith(".assembly-") for p in raw.split("/"))
    ):
        raise ValueError(f"unsafe {label}: {value!r}")
    return raw


def notice_name(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return (
        bool(
            re.search(
                r"(?:^|[-_.])(licen[sc]e|notice|copying|copyright|authors|unlicense|ftl|exception)",
                name,
            )
        )
        and PurePosixPath(name).suffix not in CODE_SUFFIXES
    )


def digest_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Source:
    identity: str
    url: str | None
    local: Path | None
    checks: tuple[tuple[str, str], ...]
    size: int | None


@dataclass(frozen=True)
class Member:
    archive_path: str
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Record:
    source: Source
    group: str
    path: str | None
    members: tuple[Member, ...]
    metadata: dict[str, object]


def sha256_value(value: object, label: str) -> str:
    raw = text_value(value, label)
    if not re.fullmatch(r"[0-9a-f]{64}", raw):
        raise ValueError(f"{label} must be lowercase SHA-256 hex")
    return raw


def parse_source(
    raw: dict[str, object], inventory_dir: Path, *, require_local: bool
) -> Source:
    checks: list[tuple[str, str]] = []
    if "sha256" in raw:
        checks.append(("sha256", sha256_value(raw["sha256"], "input sha256")))
    if "integrity" in raw:
        # This candidate's npm lock entries have one SHA-512 integrity value.
        sri = text_value(raw["integrity"], "input integrity")
        match = re.fullmatch(r"(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})", sri)
        if not match:
            raise ValueError("integrity must contain one SHA-256/384/512 SRI value")
        algorithm, encoded = match.groups()
        decoded = base64.b64decode(encoded, validate=True)
        if len(decoded) != hashlib.new(algorithm).digest_size:
            raise ValueError("integrity digest has the wrong length")
        checks.append((algorithm, decoded.hex()))
    if not checks:
        raise ValueError("every input needs sha256 or integrity")
    size = size_value(raw["bytes"], "input bytes") if "bytes" in raw else None
    url = None
    local = None
    if "source_file" in raw:
        relative = canonical_path(raw["source_file"], "local input path")
        local = (inventory_dir / relative).resolve()
        if not local.is_relative_to(inventory_dir) or (
            require_local and not local.is_file()
        ):
            raise ValueError(
                "local input must be an existing file below the inventory directory"
            )
        if "url" in raw:
            raise ValueError("input cannot have both source_file and url")
    else:
        url = text_value(raw.get("url"), "input url")
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("remote inputs require HTTPS without embedded credentials")
    algorithm, digest = max(checks, key=lambda pair: hashlib.new(pair[0]).digest_size)
    return Source(f"{algorithm}-{digest}", url, local, tuple(checks), size)


def validate_inventory(
    inventory: dict[str, object], inventory_dir: Path, *, require_local: bool = True
) -> tuple[list[Record], dict[str, list[str]]]:
    if inventory.get("schema_version") != 1:
        raise ValueError("unsupported supplemental inventory schema")
    bindings = object_value(inventory.get("bindings"), "bindings")
    for name in ("runtime_lock_sha256", "ui_package_lock_sha256"):
        _ = sha256_value(bindings.get(name), f"bindings.{name}")
    required = object_value(inventory.get("requirement_groups"), "requirement_groups")
    groups: dict[str, list[str]] = {}
    for name, value in required.items():
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            raise ValueError(f"invalid group: {name!r}")
        groups[name] = [
            text_value(v, "requirement id") for v in array_value(value, name)
        ]
        if len(groups[name]) != len(set(groups[name])):
            raise ValueError(f"duplicate requirement id in {name}")
    covered_groups: dict[str, str] = {}
    for value in array_value(inventory.get("covered_sources", []), "covered_sources"):
        declaration = object_value(value, "covered source")
        path = canonical_path(declaration.get("artifact_path"), "covered-source path")
        group = text_value(declaration.get("group", "numeric"), "covered-source group")
        if path in covered_groups or group not in groups:
            raise ValueError("duplicate covered-source path or undeclared source group")
        covered_groups[path] = group
    covered = set(covered_groups)
    controls: set[str] = set()
    for value in array_value(
        inventory.get("distribution_inputs", []), "distribution_inputs"
    ):
        declaration = object_value(value, "distribution input")
        path = canonical_path(
            declaration.get("artifact_path"), "distribution input path"
        )
        if path in controls:
            raise ValueError(f"duplicate distribution input declaration: {path}")
        if declaration.get("group") not in groups:
            raise ValueError("distribution input has an undeclared group")
        if declaration.get("role") not in (
            "build-control",
            "provenance",
            "corresponding-source",
        ):
            raise ValueError("distribution input needs an explicit supported role")
        _ = text_value(declaration.get("purpose"), "distribution input purpose")
        controls.add(path)
    destinations: set[str] = set()
    records: list[Record] = []

    def destination(value: object) -> str:
        path = canonical_path(value, "output asset path")
        if path.split("/")[0] in RESERVED:
            raise ValueError(f"reserved output asset path: {path}")
        if path in destinations:
            raise ValueError(f"duplicate output asset path: {path}")
        for other in destinations:
            if path.startswith(other + "/") or other.startswith(path + "/"):
                raise ValueError(
                    f"output asset file/directory conflict: {path}, {other}"
                )
        destinations.add(path)
        return path

    for kind in ("files", "archives"):
        for value in array_value(inventory.get(kind, []), kind):
            raw = object_value(value, kind + " entry")
            group = text_value(raw.get("group"), "input group")
            if group not in groups:
                raise ValueError(f"input refers to undeclared group: {group}")
            source = parse_source(raw, inventory_dir, require_local=require_local)
            if kind == "files":
                path = destination(raw.get("path"))
                if (
                    path not in covered
                    and path not in controls
                    and not notice_name(path)
                ):
                    raise ValueError(
                        f"direct asset is neither a notice nor declared source/build input: {path}"
                    )
                matching = [
                    object_value(v, "distribution input")
                    for v in array_value(
                        inventory.get("distribution_inputs", []), "distribution_inputs"
                    )
                    if object_value(v, "distribution input").get("artifact_path")
                    == path
                ]
                if matching and matching[0].get("group") != group:
                    raise ValueError(f"distribution input group mismatch: {path}")
                if path in covered_groups and covered_groups[path] != group:
                    raise ValueError(f"covered-source group mismatch: {path}")
                if source.size is None or not any(
                    a == "sha256" for a, _ in source.checks
                ):
                    raise ValueError("direct files require both bytes and sha256")
                records.append(Record(source, group, path, (), raw))
                continue
            members: list[Member] = []
            names: set[str] = set()
            for member_value in array_value(raw.get("members"), "archive members"):
                member = object_value(member_value, "archive member")
                archive_path = canonical_path(
                    member.get("archive_path"), "archive member path"
                )
                path = destination(member.get("path"))
                if archive_path in names:
                    raise ValueError(f"duplicate pinned archive member: {archive_path}")
                names.add(archive_path)
                # Some upstreams place component license terms in a README.
                # A reviewed, explicit basis permits that notice member while
                # still excluding implementation/binary suffixes.
                if "notice_basis" in member:
                    _ = text_value(member["notice_basis"], "notice basis")
                explicit_notice = (
                    "notice_basis" in member
                    and PurePosixPath(archive_path).suffix.lower() not in CODE_SUFFIXES
                )
                if (
                    not notice_name(archive_path) and not explicit_notice
                ) or not notice_name(path):
                    raise ValueError(
                        f"only notice members may be extracted: {archive_path}"
                    )
                members.append(
                    Member(
                        archive_path,
                        path,
                        size_value(member.get("bytes"), "member bytes"),
                        sha256_value(member.get("sha256"), "member sha256"),
                    )
                )
            if not members:
                raise ValueError("archive needs at least one pinned notice member")
            records.append(Record(source, group, None, tuple(members), raw))
    if not records:
        raise ValueError("inventory contains no inputs")
    if not covered.issubset(destinations):
        raise ValueError(
            "covered-source declaration lacks a corresponding direct asset"
        )
    direct_paths = {record.path for record in records if record.path is not None}
    if not controls.issubset(direct_paths) or not covered.issubset(direct_paths):
        raise ValueError("source/build declarations must identify direct files")
    return records, groups


def verify_input(path: Path, source: Source) -> dict[str, object]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"input cache is not a regular file: {path}")
    size = path.stat().st_size
    if source.size is not None and size != source.size:
        raise ValueError(
            f"input size mismatch: {source.identity}; expected {source.size}, got {size}"
        )
    for algorithm, expected in source.checks:
        if digest_file(path, algorithm) != expected:
            raise ValueError(f"input {algorithm} mismatch: {source.identity}")
    return {"bytes": size, "sha256": digest_file(path)}


def obtain(source: Source, cache: Path) -> Path:
    target = cache / (source.identity + ".input")
    if target.exists() or target.is_symlink():
        _ = verify_input(
            target, source
        )  # A corrupt cache is an error, not silently replaced.
        return target
    fd, temporary = tempfile.mkstemp(prefix="download-", dir=cache)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            if source.local is not None:
                with source.local.open("rb") as input_file:
                    shutil.copyfileobj(input_file, output, CHUNK)
            else:
                assert source.url is not None
                request = urllib.request.Request(
                    source.url, headers={"User-Agent": "Scansor-notice-assembler/0.1"}
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    shutil.copyfileobj(response, output, CHUNK)
        _ = verify_input(path, source)
        os.replace(path, target)
        return target
    finally:
        path.unlink(missing_ok=True)


def copy_member(stream: IO[bytes], member: Member, output: Path) -> None:
    path = output / member.path
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with path.open("xb") as target:
        while block := stream.read(min(CHUNK, member.size - size + 1)):
            size += len(block)
            if size > member.size:
                raise ValueError(
                    f"notice exceeds pinned byte count: {member.archive_path}"
                )
            digest.update(block)
            _ = target.write(block)
    if size != member.size or digest.hexdigest() != member.sha256:
        raise ValueError(f"notice member integrity mismatch: {member.archive_path}")


def extract_notices(path: Path, record: Record, output: Path) -> None:
    selected = {member.archive_path: member for member in record.members}
    seen: set[str] = set()
    found: set[str] = set()

    def visit(name: str, directory: bool) -> str:
        canonical = canonical_path(name, "archive entry path", directory=directory)
        if canonical in seen:
            raise ValueError(f"duplicate archive entry: {canonical}")
        seen.add(canonical)
        return canonical

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                name = visit(item.filename, item.is_dir())
                if name not in selected:
                    continue
                member = selected[name]
                if (
                    item.is_dir()
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.flag_bits & 1
                ):
                    raise ValueError(
                        f"selected ZIP notice is not a regular unencrypted file: {name}"
                    )
                if item.file_size != member.size:
                    raise ValueError(f"notice size metadata mismatch: {name}")
                with archive.open(item) as stream:
                    copy_member(stream, member, output)
                found.add(name)
    else:
        with tarfile.open(path, mode="r|*") as archive:
            for item in archive:
                name = visit(item.name, item.isdir())
                if name not in selected:
                    continue
                member = selected[name]
                if not item.isfile():
                    raise ValueError(
                        f"selected TAR notice is not a regular file: {name}"
                    )
                if item.size != member.size:
                    raise ValueError(f"notice size metadata mismatch: {name}")
                stream = archive.extractfile(item)
                if stream is None:
                    raise ValueError(f"missing notice stream: {name}")
                with stream:
                    copy_member(stream, member, output)
                found.add(name)
    if found != set(selected):
        raise ValueError(
            f"missing pinned archive notices: {sorted(set(selected) - found)}"
        )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def external_path(path: Path, label: str) -> None:
    for ancestor in (path, *path.parents):
        # The prescribed agent temporary root has sandbox bookkeeping .git
        # metadata. Protect actual project roots and linked worktrees instead.
        project = any(
            (ancestor / marker).exists()
            for marker in ("pyproject.toml", "package.json", "AGENTS.md")
        )
        if (ancestor / ".git").exists() and (project or (ancestor / ".git").is_file()):
            raise ValueError(f"{label} must be outside project repositories: {path}")


def overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def make_requirements(
    inventory: dict[str, object], group: str, ids: list[str], paths: list[str]
) -> list[dict[str, object]]:
    rules = object_value(inventory.get("requirements", {}), "requirements")
    result: list[dict[str, object]] = []
    for requirement in ids:
        rule = object_value(rules.get(requirement), f"requirement {requirement}")
        kind = text_value(rule.get("kind"), "requirement kind")
        if kind == "external":
            result.append(
                {
                    "id": requirement,
                    "status": "external_required",
                    "evidence": [],
                    "reason": rule.get("reason"),
                    "expected_evidence": rule.get("expected_evidence"),
                }
            )
            continue
        evidence = [
            canonical_path(v, "requirement evidence path")
            for v in array_value(rule.get("paths"), "requirement paths")
        ]
        if kind != "assets" or not evidence or not set(evidence).issubset(paths):
            raise ValueError(f"requirement {requirement} lacks group {group} evidence")
        result.append(
            {
                "id": requirement,
                "status": "fulfilled",
                "evidence": evidence,
                "basis": rule.get("basis"),
            }
        )
    return result


def choose_groups(
    available: dict[str, list[str]], selected: list[str] | None
) -> list[str]:
    names = sorted(available) if selected is None else sorted(set(selected))
    if not names or not set(names).issubset(available):
        raise ValueError(f"selected groups unavailable: {names}")
    return names


def source_offer(inventory: dict[str, object], group: str) -> dict[str, object]:
    sources = [
        object_value(v, "covered source")
        for v in array_value(inventory.get("covered_sources", []), "covered_sources")
    ]
    controls = [
        object_value(v, "distribution input")
        for v in array_value(
            inventory.get("distribution_inputs", []), "distribution_inputs"
        )
    ]
    return {
        "statement": "The covered-source archives and distribution inputs listed here are included in this selected artifact group. A consuming runtime/app package must preserve these relative paths or rewrite the locations when copying them.",
        "group": group,
        "covered_sources": [v for v in sources if v.get("group", "numeric") == group],
        "distribution_inputs": [v for v in controls if v.get("group") == group],
        "cpython_modifications": inventory.get("cpython_modifications")
        if group == "numeric"
        else None,
        "scope": "Source availability and recorded upstream changes; not an attestation of a historical compiler invocation or a byte-identical rebuild. The packaged inventory describes all approved input groups; only this selected group is offered here.",
    }


def expected_memberships(
    records: list[Record], names: list[str]
) -> dict[str, list[str]]:
    memberships: dict[str, list[str]] = {}
    for record in records:
        if record.group not in names:
            continue
        for path in (
            [record.path]
            if record.path is not None
            else [m.path for m in record.members]
        ):
            memberships[path] = [record.group]
    memberships["inventory.json"] = names
    for group in names:
        memberships[f"provenance/{group}.json"] = [group]
        memberships[f"provenance/{group}/source-offer.json"] = [group]
    return memberships


def group_completion(
    inventory: dict[str, object],
    groups: dict[str, list[str]],
    memberships: dict[str, list[str]],
    names: list[str],
) -> dict[str, object]:
    completion: dict[str, object] = {}
    for group in names:
        paths = sorted(
            path for path, belongs in memberships.items() if group in belongs
        )
        requirements = make_requirements(inventory, group, groups[group], paths)
        own = [r for r in requirements if r["status"] != "external_required"]
        completion[group] = {
            "supplemental_requirements_complete": bool(own)
            and all(r["status"] == "fulfilled" for r in own),
            "requirements_complete": bool(requirements)
            and all(r["status"] == "fulfilled" for r in requirements),
            "requirements": requirements,
            "assets": paths,
        }
    return completion


def package_manifest(
    inventory: dict[str, object],
    inventory_digest: str,
    completion: dict[str, object],
    assets: list[dict[str, object]],
    names: list[str],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "scansor-browser-redistribution",
        "inventory_sha256": inventory_digest,
        "bindings": inventory["bindings"],
        "identities": inventory.get("identities"),
        "selected_groups": names,
        "groups": completion,
        "assets": sorted(assets, key=lambda a: cast(str, a["path"])),
        "source_offer_paths": [
            f"provenance/{group}/source-offer.json" for group in names
        ],
        "redistribution_ready": all(
            cast(dict[str, object], g)["requirements_complete"] is True
            for g in completion.values()
        ),
        "authorization": "Requirement evidence only; this artifact does not authorize publication.",
        "cad": "No CAD group selected"
        if "cad" not in names
        else "See CAD group requirements",
    }


def checked_asset(root: Path, relative: object) -> Path:
    path = root / canonical_path(relative, "packaged asset path")
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError(f"packaged asset escapes root or is a symlink: {relative}")
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f"packaged asset has a symlink parent: {relative}")
    if not path.is_file():
        raise ValueError(f"missing packaged asset: {relative}")
    return path


def verify(root: Path, *, inventory_path: Path | None = None) -> dict[str, object]:
    """Verify packaged bytes and derive requirement status from the pinned plan.

    Pass the repository's checked-in inventory_path to bind the package to its
    approved input plan; omitting it provides integrity, not inventory approval.
    Source inputs/cache are unnecessary after assembly.
    """
    root = root.resolve()
    manifest = object_value(
        json.loads(checked_asset(root, "manifest.json").read_bytes()), "manifest"
    )
    raw = checked_asset(root, "inventory.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if manifest.get("inventory_sha256") != digest:
        raise ValueError("packaged inventory SHA-256 mismatch")
    if (
        inventory_path is not None
        and hashlib.sha256(inventory_path.read_bytes()).hexdigest() != digest
    ):
        raise ValueError("package does not match the approved inventory")
    inventory = object_value(json.loads(raw), "inventory")
    records, groups = validate_inventory(inventory, root, require_local=False)
    selected = [
        text_value(v, "selected group")
        for v in array_value(manifest.get("selected_groups"), "selected_groups")
    ]
    names = choose_groups(groups, selected)
    if selected != names:
        raise ValueError("selected_groups must be sorted and unique")
    memberships = expected_memberships(records, names)
    completion = group_completion(inventory, groups, memberships, names)
    expected_checks: dict[str, tuple[int, str]] = {"inventory.json": (len(raw), digest)}
    for record in records:
        if record.group not in names:
            continue
        if record.path is not None:
            sha = next(d for a, d in record.source.checks if a == "sha256")
            assert record.source.size is not None
            expected_checks[record.path] = (record.source.size, sha)
        else:
            expected_checks.update({m.path: (m.size, m.sha256) for m in record.members})
    for group in names:
        offer = (
            json.dumps(
                source_offer(inventory, group), indent=2, sort_keys=True
            ).encode()
            + b"\n"
        )
        expected_checks[f"provenance/{group}/source-offer.json"] = (
            len(offer),
            hashlib.sha256(offer).hexdigest(),
        )
    assets: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in array_value(manifest.get("assets"), "manifest assets"):
        item = object_value(value, "asset")
        path = canonical_path(item.get("path"), "asset path")
        if path in seen or path not in memberships:
            raise ValueError(f"duplicate or unapproved manifested asset: {path}")
        seen.add(path)
        expected_size = size_value(item.get("bytes"), "asset bytes")
        expected_sha = sha256_value(item.get("sha256"), "asset sha256")
        if item.get("groups") != memberships[path]:
            raise ValueError(f"asset group membership mismatch: {path}")
        file = checked_asset(root, path)
        if file.stat().st_size != expected_size or digest_file(file) != expected_sha:
            raise ValueError(f"packaged asset integrity failure: {path}")
        if path in expected_checks and expected_checks[path] != (
            expected_size,
            expected_sha,
        ):
            raise ValueError(f"packaged asset does not match pinned inventory: {path}")
        assets.append(item)
    if seen != set(memberships):
        raise ValueError(
            f"incomplete manifest assets: {sorted(set(memberships) - seen)}"
        )
    for group in names:
        provenance = object_value(
            json.loads(checked_asset(root, f"provenance/{group}.json").read_bytes()),
            "group provenance",
        )
        group_result = object_value(completion[group], "group completion")
        for key, expected in (
            ("requirements", group_result["requirements"]),
            ("target_exclusions", inventory.get("target_exclusions", [])),
            ("remaining_decisions", inventory.get("remaining_decisions", [])),
        ):
            if provenance.get(key) != expected:
                raise ValueError(f"group provenance {key} mismatch: {group}")
        receipts = array_value(provenance.get("inputs"), "input receipts")
        selected_records = [record for record in records if record.group == group]
        if len(receipts) != len(selected_records):
            raise ValueError(f"input receipt count mismatch: {group}")
        for receipt_value, record in zip(receipts, selected_records, strict=True):
            receipt = object_value(receipt_value, "input receipt")
            if receipt.get("input") != record.metadata or receipt.get("group") != group:
                raise ValueError(f"input receipt identity mismatch: {group}")
            verified = object_value(receipt.get("verified_input"), "verified input")
            byte_count = size_value(verified.get("bytes"), "verified input bytes")
            source_sha = sha256_value(verified.get("sha256"), "verified input sha256")
            if record.source.size is not None and byte_count != record.source.size:
                raise ValueError("input receipt size mismatch")
            expected = [d for a, d in record.source.checks if a == "sha256"]
            if expected and source_sha != expected[0]:
                raise ValueError("input receipt SHA-256 mismatch")
    derived = package_manifest(inventory, digest, completion, assets, names)
    if manifest != derived:
        raise ValueError(
            "manifest requirement completion or correspondence differs from derived evidence"
        )
    return derived


def select_verified_manifest(
    manifest: dict[str, object], groups: list[str]
) -> dict[str, object]:
    """Select from one already verified snapshot without reopening its inputs."""
    available = object_value(manifest["groups"], "manifest groups")
    names = sorted(set(groups))
    if not names or not set(names).issubset(available):
        raise ValueError("requested group absent from assembled package")
    assets: list[dict[str, object]] = []
    for value in array_value(manifest["assets"], "assets"):
        record = object_value(value, "asset")
        memberships = [
            text_value(v, "asset group")
            for v in array_value(record["groups"], "asset groups")
        ]
        selected = sorted(set(memberships).intersection(names))
        if selected:
            assets.append({**record, "groups": selected})
    completion = {name: available[name] for name in names}
    return manifest | {
        "selected_groups": names,
        "groups": completion,
        "assets": sorted(assets, key=lambda asset: cast(str, asset["path"])),
        "source_offer_paths": [
            f"provenance/{group}/source-offer.json" for group in names
        ],
        "redistribution_ready": all(
            object_value(value, "group completion")["requirements_complete"] is True
            for value in completion.values()
        ),
        "cad": "No CAD group selected"
        if "cad" not in names
        else "See CAD group requirements",
    }


def selected_manifest(root: Path, groups: list[str]) -> dict[str, object]:
    return select_verified_manifest(verify(root), groups)


def selected_assets(root: Path, groups: list[str]) -> list[dict[str, object]]:
    """Return verified asset records matching the rewritten subset manifest."""
    return [
        object_value(v, "asset")
        for v in array_value(selected_manifest(root, groups)["assets"], "assets")
    ]


def assemble(
    inventory_path: Path,
    output: Path,
    cache: Path | None,
    selected: list[str] | None = None,
) -> dict[str, object]:
    inventory_path = inventory_path.resolve()
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a fresh, nonexistent directory")
    output = output.resolve()
    if cache is not None:
        if cache.is_symlink():
            raise ValueError("cache must not be a symlink")
        cache = cache.resolve()
    raw = inventory_path.read_bytes()
    inventory = object_value(json.loads(raw), "inventory")
    records, groups = validate_inventory(inventory, inventory_path.parent)
    names = choose_groups(groups, selected)
    records = [record for record in records if record.group in names]
    memberships = expected_memberships(records, names)
    completion = group_completion(inventory, groups, memberships, names)
    external_path(output, "output")
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a fresh, nonexistent directory")
    for protected in (inventory_path, Path(__file__).resolve()):
        if protected.is_relative_to(output):
            raise ValueError("output cannot contain its inventory or assembler")
    if cache is not None:
        external_path(cache, "cache")
        if overlap(cache, output):
            raise ValueError("cache and output must not overlap")
        if cache.exists() and (not cache.is_dir() or cache.is_symlink()):
            raise ValueError("cache must be a directory")
    output.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(prefix=".assembly-", dir=output))
    actual_cache = cache or work
    actual_cache.mkdir(parents=True, exist_ok=True)
    try:
        sources = {record.source.identity: record.source for record in records}

        def acquire(source: Source) -> Path:
            return obtain(source, actual_cache)

        # Download/hash bounded streams concurrently; retain no full archive buffers.
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            acquired = dict(
                zip(
                    sources,
                    pool.map(acquire, sources.values()),
                    strict=True,
                )
            )
        receipts: list[dict[str, object]] = []
        # Every archive/file is verified before any notice extraction occurs.
        for record in records:
            receipt = verify_input(acquired[record.source.identity], record.source)
            receipts.append(
                {
                    "group": record.group,
                    "input": record.metadata,
                    "verified_input": receipt,
                }
            )
        assets: list[dict[str, object]] = []

        def asset(path: str, memberships: list[str]) -> None:
            file = output / path
            assets.append(
                {
                    "path": path,
                    "bytes": file.stat().st_size,
                    "sha256": digest_file(file),
                    "groups": memberships,
                }
            )

        for record in records:
            source = acquired[record.source.identity]
            if record.path is not None:
                target = output / record.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open("rb") as input_file, target.open("xb") as target_file:
                    shutil.copyfileobj(input_file, target_file, CHUNK)
                asset(record.path, [record.group])
            else:
                extract_notices(source, record, output)
                for member in record.members:
                    asset(member.path, [record.group])
        _ = (output / "inventory.json").write_bytes(raw)
        asset("inventory.json", names)
        for group in names:
            write_json(
                output / f"provenance/{group}/source-offer.json",
                source_offer(inventory, group),
            )
            asset(f"provenance/{group}/source-offer.json", [group])
            requirements = object_value(completion[group], "group completion")[
                "requirements"
            ]
            write_json(
                output / f"provenance/{group}.json",
                {
                    "inputs": [r for r in receipts if r["group"] == group],
                    "requirements": requirements,
                    "target_exclusions": inventory.get("target_exclusions", []),
                    "remaining_decisions": inventory.get("remaining_decisions", []),
                    "notice_scope": "Curated notice supersets; no claim that every listed component is linked into this target.",
                },
            )
            asset(f"provenance/{group}.json", [group])
        manifest = package_manifest(
            inventory, hashlib.sha256(raw).hexdigest(), completion, assets, names
        )
        write_json(output / "manifest.json", manifest)
        return manifest
    except BaseException as error:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "error": str(error),
                "inventory_sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        raise
    finally:
        shutil.rmtree(work)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--inventory", required=True, type=Path)
    _ = parser.add_argument("--output", required=True, type=Path)
    _ = parser.add_argument("--cache", type=Path)
    _ = parser.add_argument("--group", action="append", dest="groups")
    args = parser.parse_args()
    try:
        manifest = assemble(
            args.inventory.resolve(),
            args.output,
            args.cache,
            args.groups,
        )
        _ = verify(args.output.resolve(), inventory_path=args.inventory.resolve())
    except Exception as error:
        print(f"notice assembly failed: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "assets": len(cast(list[object], manifest["assets"])),
                "groups": {
                    name: {
                        key: completion[key]
                        for key in (
                            "supplemental_requirements_complete",
                            "requirements_complete",
                        )
                    }
                    for name, completion in cast(
                        dict[str, dict[str, object]], manifest["groups"]
                    ).items()
                },
                "redistribution_ready": manifest["redistribution_ready"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
