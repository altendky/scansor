#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = []
# ///
"""Verify and replay experiment-local Track 5 Onshape CAD evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import zlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Never, Self

FORMAT = "scansor-track5-cad-evidence-v2-experiment-local-non-normative"
SUITE_FORMAT = "scansor-track5-cad-suite-v1-experiment-local-non-normative"
NORMALIZED_FORMAT = "scansor-track5-cad-normalized-v2-experiment-local"
REPORT_FORMAT = "scansor-track5-cad-comparison-v2-experiment-local"
PLAN_FORMAT = "scansor-track5-cad-effect-plan-v2-experiment-local"
FORMAT_STATUS = "experiment-local/non-normative"
INTEGRITY_SCOPE = (
    "SHA-256 sidecars and manifests provide content integrity only, not "
    "cryptographic authorship. A separately verified signed commit proves a signing "
    "key endorsed the commit object and committed bytes; it does not authenticate "
    "Onshape response origin, request/response pairing, or necessarily the human "
    "committer identity."
)

RUN_ID = "track5-cad-run-02"
RUN_01_ID = "track5-cad-run-01-backfill"
DOCUMENT_ID = "1b68f4b8f4a69c6b59d7616e"
START_VERSION_ID = "3aab803f12cc63b659ecb1ab"
START_MICROVERSION_ID = "3d03486b79ccfd0db79f5172"
RUN_01_VERSION_ID = "69ef588c1fee61aa2e65745c"
RUN_01_MICROVERSION_ID = "35908255c9c46816eae3e602"
MAIN_WORKSPACE_ID = "6e2cc94501f05a40302c95bc"
SANDBOX_FOLDER_ID = "b788af3dad6250b9ed521e6a"
WORKSPACE_NAME = "Track 5 CAD reproducibility run 2"
VERSION_NAME = "stepped-rotational-v1 CAD evidence run 2"
RUN_01_VERSION_NAME = "stepped-rotational-v1 CAD evidence"
CONTRACT = "stepped-rotational-v1"
GENERATOR_VERSION = "1.0.5"
GENERATOR_SOURCE_SHA256 = (
    "995a067d7f4bd247defd092a7e8501224ce7393e9e9e09dc47726f13b610a1e8"
)
CONTRACT_SHA256 = "2c77ce6c586a5f5ebc29f1dfe93f6f19264a8849a8dd62ccb22ef3b5338ca175"
LINEAR_TOLERANCE_M = 1e-9
ANGULAR_TOLERANCE_RAD = 1e-9

MAX_REPOSITORY_EVIDENCE_BYTES = 25 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_CHECKSUM_BYTES = 256
MAX_OPERATION_LOG_BYTES = 128 * 1024
MAX_DERIVED_BYTES = 4 * 1024 * 1024
MAX_NOTES_BYTES = 256 * 1024
MAX_STORED_ENTITY_BYTES = 16 * 1024 * 1024
MAX_RAW_ENTITY_BYTES = 16 * 1024 * 1024
MAX_RUN_FILES = 32
MAX_SUITE_FILES = 8
MAX_OPERATIONS = 8
MAX_TREE_ENTRIES = 64
MAX_TREE_DEPTH = 4
IO_CHUNK_SIZE = 64 * 1024
HEX_24 = re.compile(r"[0-9a-f]{24}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
TRANSIENT_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")

ALLOWED_HEADERS = frozenset(
    {"content-length", "content-type", "date", "etag", "request-id", "x-request-id"}
)
SECRET_KEYS = re.compile(
    r"authorization|cookie|oauth|token|api[-_]?key|apikey|secret", re.IGNORECASE
)
SECRET_VALUES = re.compile(
    r"\b(bearer|basic)\s+[a-z0-9._~+/=-]+|\b(access|refresh)[-_]?token\b",
    re.IGNORECASE,
)
SAFE_SECURITY_METADATA_KEYS = frozenset({"authorization_expires_at"})
SOURCE_PINS = {
    "contract": CONTRACT,
    "contract_sha256": CONTRACT_SHA256,
    "document_id": DOCUMENT_ID,
    "generator_source_sha256": GENERATOR_SOURCE_SHA256,
    "generator_version": GENERATOR_VERSION,
}
EXPECTED_VARIANTS = ("axisymmetric", "asymmetric_datum_flat")
RUN_01_VARIANT_NAMES = {
    "axisymmetric": "Axisymmetric",
    "asymmetric_datum_flat": "Asymmetric Datum Flat",
}
RUN_02_VARIANT_NAMES = {
    "axisymmetric": "Axisymmetric v2",
    "asymmetric_datum_flat": "Asymmetric Datum Flat v2",
}
RUN_01_ELEMENTS = {
    "axisymmetric": "3b0748b60dddef666359609b",
    "asymmetric_datum_flat": "9cd727f2ffb7eb665c99ed7f",
}
RUN_02_VERSION_ID = "3edab4a4e7521986ca01b160"
RUN_02_MICROVERSION_ID = "4b4d24b3a5716d2a58481ae9"
RUN_02_WORKSPACE_ID = "ea4a9557ef5bb6e110e45f7f"
RUN_02_ELEMENTS = {
    "axisymmetric": "32cee5c10f33711f9778c52c",
    "asymmetric_datum_flat": "834df2af620335b532c68169",
}
CLASSIFICATION_VOCABULARY = (
    "exact",
    "semantically equivalent",
    "expected revision-ID changes",
    "variable metadata",
    "tolerated numerical",
    "unsupported",
    "unexplained",
    "failure",
)

BOUNDED_FEATURESCRIPT = r"""function(context is Context, queries is map)
{
    var bodies = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);
    var faces = evaluateQuery(context, qOwnedByBody(bodies, EntityType.FACE));
    var result = [];
    for (var face in faces)
    {
        result = append(result, {
            "bounds" : evBox3d(context, { "topology" : face, "tight" : true }),
            "id" : transientQueriesToStrings(face),
            "surface" : evSurfaceDefinition(context, { "face" : face }),
            "tangent" : evFaceTangentPlane(context, {
                "face" : face,
                "parameter" : vector(0.5, 0.5)
            })
        });
    }
    return {
        "bodyCount" : size(evaluateQuery(context, bodies)),
        "faces" : result,
        "probeVersion" : "track5-stepped-rotational-v1-v1"
    };
}"""
FEATURESCRIPT_SHA256 = hashlib.sha256(BOUNDED_FEATURESCRIPT.encode()).hexdigest()
LIVE_TRANSPORT_IMPLEMENTED = False
AUTHENTICATED_PREFLIGHT_CAPTURE_IMPLEMENTED = False
AUTHORING_PAYLOADS_FROZEN = False
PROBE_PAYLOAD_FROZEN = True


class EvidenceError(ValueError):
    """Evidence is incomplete, malformed, unsafe, or inconsistent."""


@dataclass(frozen=True)
class OperationSpec:
    name: str
    endpoint: str
    method: str
    variant: str | None
    kind: str


@dataclass(frozen=True)
class VerifiedRun:
    manifest: dict[str, Any]
    manifest_sha256: str
    normalized: dict[str, Any]


@dataclass
class AnchoredFile:
    stream: Any
    size: int
    chain: tuple[tuple[tuple[str, ...], int, int, int], ...]


OPERATION_SPECS = (
    OperationSpec("version-anchor", "getVersion", "GET", None, "version"),
    OperationSpec(
        "element-inventory", "getElementsInDocument", "GET", None, "elements"
    ),
    OperationSpec(
        "axisymmetric-part-inventory",
        "getPartsWMVE",
        "GET",
        "axisymmetric",
        "parts",
    ),
    OperationSpec(
        "axisymmetric-body-details",
        "getBodyDetails",
        "GET",
        "axisymmetric",
        "body",
    ),
    OperationSpec(
        "axisymmetric-geometry-probe",
        "evalFeatureScript",
        "POST",
        "axisymmetric",
        "probe",
    ),
    OperationSpec(
        "asymmetric_datum_flat-part-inventory",
        "getPartsWMVE",
        "GET",
        "asymmetric_datum_flat",
        "parts",
    ),
    OperationSpec(
        "asymmetric_datum_flat-body-details",
        "getBodyDetails",
        "GET",
        "asymmetric_datum_flat",
        "body",
    ),
    OperationSpec(
        "asymmetric_datum_flat-geometry-probe",
        "evalFeatureScript",
        "POST",
        "asymmetric_datum_flat",
        "probe",
    ),
)
EXPECTED_CAPTURE_OPERATIONS = tuple(spec.name for spec in OPERATION_SPECS)


def fail(message: str) -> Never:
    raise EvidenceError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _open_bounded(path: Path, maximum: int, label: str) -> tuple[Any, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened_stat = os.fstat(descriptor)
        size = opened_stat.st_size
    except OSError as error:
        raise EvidenceError(f"{label}: cannot open regular file: {error}") from error
    if not stat.S_ISREG(opened_stat.st_mode):
        os.close(descriptor)
        fail(f"{label}: expected regular non-symlink file")
    if size < 0 or size > maximum:
        os.close(descriptor)
        fail(f"{label}: file size {size} exceeds bound {maximum}")
    return os.fdopen(descriptor, "rb"), size


def _stream_sha256(path: Path, maximum: int, label: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    read_size = 0
    stream, size = _open_bounded(path, maximum, label)
    with stream:
        while chunk := stream.read(IO_CHUNK_SIZE):
            read_size += len(chunk)
            if read_size > maximum:
                fail(f"{label}: file exceeded bound while reading")
            digest.update(chunk)
    if read_size != size:
        fail(f"{label}: file changed while reading")
    return digest.hexdigest(), read_size


def read_limited(path: Path, maximum: int, label: str) -> bytes:
    output = bytearray()
    stream, size = _open_bounded(path, maximum, label)
    with stream:
        while chunk := stream.read(min(IO_CHUNK_SIZE, maximum + 1 - len(output))):
            output.extend(chunk)
            if len(output) > maximum:
                fail(f"{label}: file exceeded bound while reading")
    if len(output) != size:
        fail(f"{label}: file changed while reading")
    return bytes(output)


class EvidenceDirectory:
    """Descriptor-anchored, no-follow access to one evidence directory."""

    def __init__(self, root: Path):
        required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
        if (
            any(not hasattr(os, name) for name in required_flags)
            or os.stat not in os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks
        ):
            fail("platform lacks required descriptor-anchored no-follow support")
        self.path = root
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            self.descriptor = os.open(root, flags)
            root_stat = os.fstat(self.descriptor)
        except OSError as error:
            raise EvidenceError(
                f"evidence root cannot be opened safely: {error}"
            ) from error
        if not stat.S_ISDIR(root_stat.st_mode):
            os.close(self.descriptor)
            fail("evidence root is not a directory")
        self.root_identity = (root_stat.st_dev, root_stat.st_ino)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        os.close(self.descriptor)

    @staticmethod
    def _parts(relative: Any, label: str) -> tuple[str, ...]:
        name = _expect_str(relative, label)
        pure = PurePosixPath(name)
        if (
            pure.is_absolute()
            or not pure.parts
            or ".." in pure.parts
            or pure.as_posix() != name
        ):
            fail(f"{label}: unsafe relative path")
        return pure.parts

    def _assert_root_unchanged(self) -> None:
        try:
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise EvidenceError(f"evidence root was replaced: {error}") from error
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != self.root_identity
        ):
            fail("evidence root was replaced")

    def open_file(self, relative: Any, maximum: int, label: str) -> AnchoredFile:
        self._assert_root_unchanged()
        parts = self._parts(relative, label)
        current_fd = os.dup(self.descriptor)
        chain: list[tuple[tuple[str, ...], int, int, int]] = []
        try:
            for index, part in enumerate(parts[:-1], 1):
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=current_fd,
                )
                opened_stat = os.fstat(next_fd)
                if not stat.S_ISDIR(opened_stat.st_mode):
                    os.close(next_fd)
                    fail(f"{label}: parent component is not a directory")
                chain.append(
                    (
                        parts[:index],
                        opened_stat.st_dev,
                        opened_stat.st_ino,
                        stat.S_IFDIR,
                    )
                )
                os.close(current_fd)
                current_fd = next_fd
            leaf_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current_fd,
            )
            leaf_stat = os.fstat(leaf_fd)
            if not stat.S_ISREG(leaf_stat.st_mode):
                os.close(leaf_fd)
                fail(f"{label}: leaf is not a regular file")
            if leaf_stat.st_size < 0 or leaf_stat.st_size > maximum:
                os.close(leaf_fd)
                fail(f"{label}: file size {leaf_stat.st_size} exceeds bound {maximum}")
            chain.append(
                (
                    parts,
                    leaf_stat.st_dev,
                    leaf_stat.st_ino,
                    stat.S_IFREG,
                )
            )
            return AnchoredFile(
                os.fdopen(leaf_fd, "rb"), leaf_stat.st_size, tuple(chain)
            )
        except OSError as error:
            raise EvidenceError(
                f"{label}: descriptor-anchored open failed: {error}"
            ) from error
        finally:
            os.close(current_fd)

    def confirm_unchanged(self, opened: AnchoredFile, label: str) -> None:
        self._assert_root_unchanged()
        for parts, expected_device, expected_inode, expected_type in opened.chain:
            current_fd = os.dup(self.descriptor)
            try:
                for index, part in enumerate(parts):
                    is_leaf = index == len(parts) - 1
                    flags = os.O_RDONLY | os.O_NOFOLLOW
                    if not is_leaf or expected_type == stat.S_IFDIR:
                        flags |= os.O_DIRECTORY
                    else:
                        flags |= getattr(os, "O_NONBLOCK", 0)
                    next_fd = os.open(part, flags, dir_fd=current_fd)
                    os.close(current_fd)
                    current_fd = next_fd
                current = os.fstat(current_fd)
            except OSError as error:
                raise EvidenceError(
                    f"{label}: path component was replaced: {error}"
                ) from error
            finally:
                os.close(current_fd)
            if stat.S_IFMT(current.st_mode) != expected_type or (
                current.st_dev,
                current.st_ino,
            ) != (expected_device, expected_inode):
                fail(f"{label}: path component was replaced")

    def read(self, relative: Any, maximum: int, label: str) -> bytes:
        opened = self.open_file(relative, maximum, label)
        output = bytearray()
        remaining = opened.size
        with opened.stream:
            while remaining:
                chunk = opened.stream.read(min(IO_CHUNK_SIZE, remaining + 1))
                if not chunk:
                    fail(f"{label}: file shrank after open")
                if len(chunk) > remaining:
                    fail(f"{label}: file grew after open")
                output.extend(chunk)
                remaining -= len(chunk)
            if opened.stream.read(1):
                fail(f"{label}: file grew after open")
        self.confirm_unchanged(opened, label)
        return bytes(output)

    def walk_files(self, maximum: int) -> set[str]:
        self._assert_root_unchanged()
        files: set[str] = set()
        stack = [(os.dup(self.descriptor), (), 0)]
        entry_count = 0
        while stack:
            directory_fd, prefix, depth = stack.pop()
            if depth > MAX_TREE_DEPTH:
                os.close(directory_fd)
                fail(f"evidence tree depth exceeds {MAX_TREE_DEPTH}")
            try:
                with os.scandir(os.dup(directory_fd)) as entries:
                    for entry in entries:
                        entry_count += 1
                        if entry_count > MAX_TREE_ENTRIES:
                            fail(
                                f"evidence tree entry count exceeds {MAX_TREE_ENTRIES}"
                            )
                        relative_parts = (*prefix, entry.name)
                        relative = PurePosixPath(*relative_parts).as_posix()
                        entry_stat = os.stat(
                            entry.name, dir_fd=directory_fd, follow_symlinks=False
                        )
                        if stat.S_ISLNK(entry_stat.st_mode):
                            fail(f"evidence contains symlink: {relative}")
                        if stat.S_ISDIR(entry_stat.st_mode):
                            try:
                                child_fd = os.open(
                                    entry.name,
                                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=directory_fd,
                                )
                            except OSError as error:
                                raise EvidenceError(
                                    f"evidence directory was replaced: {relative}: {error}"
                                ) from error
                            child_stat = os.fstat(child_fd)
                            if not stat.S_ISDIR(child_stat.st_mode) or (
                                child_stat.st_dev,
                                child_stat.st_ino,
                            ) != (entry_stat.st_dev, entry_stat.st_ino):
                                os.close(child_fd)
                                fail(f"evidence directory was replaced: {relative}")
                            stack.append((child_fd, relative_parts, depth + 1))
                        elif stat.S_ISREG(entry_stat.st_mode):
                            try:
                                leaf_fd = os.open(
                                    entry.name,
                                    os.O_RDONLY
                                    | os.O_NOFOLLOW
                                    | getattr(os, "O_NONBLOCK", 0),
                                    dir_fd=directory_fd,
                                )
                            except OSError as error:
                                raise EvidenceError(
                                    f"evidence leaf was replaced: {relative}: {error}"
                                ) from error
                            leaf_stat = os.fstat(leaf_fd)
                            os.close(leaf_fd)
                            if not stat.S_ISREG(leaf_stat.st_mode) or (
                                leaf_stat.st_dev,
                                leaf_stat.st_ino,
                            ) != (entry_stat.st_dev, entry_stat.st_ino):
                                fail(f"evidence leaf was replaced: {relative}")
                            files.add(relative)
                            if len(files) > maximum:
                                fail(f"evidence file count exceeds {maximum}")
                        else:
                            fail(f"evidence contains special file: {relative}")
            finally:
                os.close(directory_fd)
        self._assert_root_unchanged()
        return files


def verify_tool_source(expected_tool_sha256: str) -> str:
    if not HEX_64.fullmatch(expected_tool_sha256):
        fail("expected tool SHA-256 must be 64 lowercase hexadecimal characters")
    source = Path(__file__).resolve()
    sidecar = source.with_suffix(".sha256")
    sidecar_data = read_limited(sidecar, MAX_CHECKSUM_BYTES, "tool checksum sidecar")
    try:
        checksum, filename = sidecar_data.decode("ascii").strip().split(maxsplit=1)
    except (UnicodeDecodeError, ValueError) as error:
        raise EvidenceError("invalid tool checksum sidecar") from error
    if filename != source.name or checksum != expected_tool_sha256:
        fail("reviewed tool SHA-256 does not match checksum sidecar")
    actual, _ = _stream_sha256(source, 4 * 1024 * 1024, "tool source")
    if actual != expected_tool_sha256:
        fail("reviewed tool SHA-256 does not match current source")
    return actual


def canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
    except (RecursionError, TypeError, ValueError) as error:
        raise EvidenceError(f"value is not canonical JSON: {error}") from error


def _reject_constant(value: str) -> Never:
    fail(f"non-finite JSON value is prohibited: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_json(data: bytes, label: str, *, canonical: bool) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise EvidenceError(f"{label}: invalid UTF-8: {error}") from error
    if text.startswith("\ufeff"):
        fail(f"{label}: UTF-8 BOM is prohibited")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise EvidenceError(f"{label}: invalid JSON: {error}") from error
    _validate_json_value(value, label)
    if canonical and canonical_json(value) != data:
        fail(f"{label}: JSON is not experiment-canonical")
    return value


def _validate_json_value(value: Any, label: str) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 100:
            fail(f"{label}: JSON nesting exceeds 100")
        if isinstance(current, float) and not math.isfinite(current):
            fail(f"{label}: non-finite number")
        if isinstance(current, str) and any(
            0xD800 <= ord(char) <= 0xDFFF for char in current
        ):
            fail(f"{label}: surrogate code point is prohibited")
        if isinstance(current, dict):
            stack.extend((key, depth + 1) for key in current)
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def reject_secrets(value: Any, label: str = "value") -> None:
    stack: list[tuple[Any, str, int]] = [(value, label, 0)]
    while stack:
        current, current_label, depth = stack.pop()
        if depth > 100:
            fail(f"{label}: value nesting exceeds 100")
        if isinstance(current, dict):
            for key, child in current.items():
                if (
                    SECRET_KEYS.search(str(key))
                    and str(key) not in SAFE_SECURITY_METADATA_KEYS
                ):
                    fail(f"{current_label}: secret-bearing field is prohibited: {key}")
                stack.append((child, f"{current_label}.{key}", depth + 1))
        elif isinstance(current, list):
            stack.extend(
                (child, f"{current_label}[{index}]", depth + 1)
                for index, child in enumerate(current)
            )
        elif isinstance(current, str) and SECRET_VALUES.search(current):
            fail(f"{current_label}: probable credential material is prohibited")


def _expect_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(f"{label}: expected object")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        fail(f"{label}: expected array")
    return value


def _expect_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{label}: expected nonempty string")
    return value


def _expect_int(value: Any, label: str, *, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        fail(f"{label}: expected integer in 0..{maximum}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{label}: expected number")
    result = float(value)
    if not math.isfinite(result):
        fail(f"{label}: expected finite number")
    return 0.0 if result == 0.0 else result


def _vector(value: Any, label: str, *, unit: bool = False) -> list[float]:
    items = _expect_list(value, label)
    if len(items) != 3:
        fail(f"{label}: expected three coordinates")
    result = [_number(item, f"{label}[{index}]") for index, item in enumerate(items)]
    if unit:
        norm = math.sqrt(sum(item * item for item in result))
        if norm == 0.0 or abs(norm - 1.0) > ANGULAR_TOLERANCE_RAD:
            fail(f"{label}: expected unit vector")
    return result


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    expected_set = set(expected)
    if set(value) != expected_set:
        fail(
            f"{label}: fields mismatch; missing={sorted(expected_set - set(value))}, "
            f"unexpected={sorted(set(value) - expected_set)}"
        )


def selected_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for original_name, value in headers.items():
        name = original_name.lower()
        if SECRET_KEYS.search(name):
            fail(f"secret header is prohibited: {original_name}")
        if name in ALLOWED_HEADERS:
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                fail(f"invalid selected header: {original_name}")
            result[name] = value
    reject_secrets(result, "selected headers")
    return dict(sorted(result.items()))


def deterministic_gzip(data: bytes, *, compresslevel: int = 9) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, mtime=0, compresslevel=compresslevel
    ) as stream:
        stream.write(data)
    return output.getvalue()


def current_gzip_provenance() -> dict[str, Any]:
    vector = deterministic_gzip(b"track5-gzip-vector-v1\n", compresslevel=9)
    return {
        "compresslevel": 9,
        "filename": "",
        "flags": vector[3],
        "implementation": platform.python_implementation(),
        "mtime": 0,
        "os_byte": vector[9],
        "python_version": platform.python_version(),
        "same_runtime_vector_sha256": sha256(vector),
        "xfl": vector[8],
        "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
    }


def _validate_gzip_provenance(value: Any) -> dict[str, Any]:
    provenance = _expect_dict(value, "capture_metadata.gzip")
    _exact_keys(
        provenance,
        {
            "compresslevel",
            "filename",
            "flags",
            "implementation",
            "mtime",
            "os_byte",
            "python_version",
            "same_runtime_vector_sha256",
            "xfl",
            "zlib_runtime_version",
        },
        "capture_metadata.gzip",
    )
    if (
        provenance["filename"] != ""
        or provenance["mtime"] != 0
        or provenance["flags"] != 0
        or provenance["compresslevel"] not in range(1, 10)
        or provenance["xfl"] not in range(256)
        or provenance["os_byte"] not in range(256)
        or not HEX_64.fullmatch(str(provenance["same_runtime_vector_sha256"]))
    ):
        fail("invalid gzip capture provenance")
    for field in ("implementation", "python_version", "zlib_runtime_version"):
        _expect_str(provenance[field], f"gzip.{field}")
    current = current_gzip_provenance()
    same_runtime = all(
        provenance[field] == current[field]
        for field in ("implementation", "python_version", "zlib_runtime_version")
    )
    if same_runtime and any(
        provenance[field] != current[field]
        for field in (
            "compresslevel",
            "flags",
            "os_byte",
            "same_runtime_vector_sha256",
            "xfl",
        )
    ):
        fail("same-runtime gzip vector or header provenance mismatch")
    return provenance


def safe_relative_file(root: Path, relative: Any, label: str) -> Path:
    name = _expect_str(relative, label)
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or pure.as_posix() != name:
        fail(f"{label}: unsafe relative path")
    current = root
    for part in pure.parts:
        current /= part
        if current.is_symlink():
            fail(f"{label}: symlink is prohibited")
    if not current.is_file():
        fail(f"{label}: expected regular file: {name}")
    return current


def artifact_descriptor(data: bytes, stored: bytes, path: str) -> dict[str, Any]:
    return {
        "path": path,
        "raw_sha256": sha256(data),
        "raw_size": len(data),
        "stored_sha256": sha256(stored),
        "stored_size": len(stored),
    }


def _stream_gzip_opened(
    opened: AnchoredFile,
    descriptor: Mapping[str, Any],
    gzip_provenance: Mapping[str, Any],
    label: str,
) -> bytes:
    stored_size = _expect_int(
        descriptor["stored_size"],
        f"{label}.stored_size",
        maximum=MAX_STORED_ENTITY_BYTES,
    )
    raw_size = _expect_int(
        descriptor["raw_size"], f"{label}.raw_size", maximum=MAX_RAW_ENTITY_BYTES
    )
    if not HEX_64.fullmatch(str(descriptor["stored_sha256"])) or not HEX_64.fullmatch(
        str(descriptor["raw_sha256"])
    ):
        fail(f"{label}: invalid SHA-256 descriptor")
    decompressor = zlib.decompressobj(wbits=31)
    stored_hash = hashlib.sha256()
    actual_stored_size = 0
    raw_hash = hashlib.sha256()
    output = bytearray()
    header = bytearray()
    stream = opened.stream
    opened_size = opened.size
    if opened_size != stored_size:
        stream.close()
        fail(f"{label}: initial stored size mismatch")
    remaining_stored = stored_size
    with stream:
        while remaining_stored:
            chunk = stream.read(min(IO_CHUNK_SIZE, remaining_stored + 1))
            if not chunk:
                fail(f"{label}: stored file shrank after open")
            if len(chunk) > remaining_stored:
                fail(f"{label}: stored file grew after open")
            remaining_stored -= len(chunk)
            actual_stored_size += len(chunk)
            stored_hash.update(chunk)
            if len(header) < 10:
                header.extend(chunk[: 10 - len(header)])
            remaining = raw_size - len(output)
            try:
                decoded = decompressor.decompress(chunk, remaining + 1)
            except zlib.error as error:
                raise EvidenceError(f"{label}: invalid gzip: {error}") from error
            if len(decoded) > remaining or decompressor.unconsumed_tail:
                fail(f"{label}: decompressed data exceeds declared bound")
            output.extend(decoded)
            raw_hash.update(decoded)
            if decompressor.unused_data:
                fail(f"{label}: concatenated or trailing gzip data")
        if stream.read(1):
            fail(f"{label}: stored file grew after open")
    if (
        actual_stored_size != stored_size
        or stored_hash.hexdigest() != descriptor["stored_sha256"]
    ):
        fail(f"{label}: stored size or SHA-256 mismatch")
    try:
        decoded = decompressor.flush(raw_size - len(output) + 1)
    except zlib.error as error:
        raise EvidenceError(f"{label}: invalid gzip trailer: {error}") from error
    if len(output) + len(decoded) > raw_size:
        fail(f"{label}: decompressed data exceeds declared bound")
    output.extend(decoded)
    raw_hash.update(decoded)
    if not decompressor.eof or decompressor.unused_data:
        fail(f"{label}: truncated, concatenated, or trailing gzip data")
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08":
        fail(f"{label}: invalid gzip header")
    if (
        header[3] != 0
        or header[4:8] != b"\x00\x00\x00\x00"
        or header[8] != gzip_provenance["xfl"]
        or header[9] != gzip_provenance["os_byte"]
    ):
        fail(f"{label}: gzip header does not match recorded capture provenance")
    if len(output) != raw_size or raw_hash.hexdigest() != descriptor["raw_sha256"]:
        fail(f"{label}: raw size or SHA-256 mismatch")
    return bytes(output)


def _stream_gzip_artifact(
    path: Path,
    descriptor: Mapping[str, Any],
    gzip_provenance: Mapping[str, Any],
    label: str,
) -> bytes:
    stream, size = _open_bounded(path, MAX_STORED_ENTITY_BYTES, label)
    return _stream_gzip_opened(
        AnchoredFile(stream, size, ()), descriptor, gzip_provenance, label
    )


def _validate_artifact(
    root: EvidenceDirectory,
    descriptor_value: Any,
    gzip_provenance: Mapping[str, Any],
    label: str,
) -> bytes:
    descriptor = _expect_dict(descriptor_value, label)
    _exact_keys(
        descriptor,
        {"path", "raw_sha256", "raw_size", "stored_sha256", "stored_size"},
        label,
    )
    opened = root.open_file(
        descriptor["path"], MAX_STORED_ENTITY_BYTES, f"{label}.path"
    )
    try:
        result = _stream_gzip_opened(opened, descriptor, gzip_provenance, label)
    except Exception:
        if not opened.stream.closed:
            opened.stream.close()
        raise
    root.confirm_unchanged(opened, label)
    return result


def _walk_files(root: Path, maximum: int) -> set[str]:
    with EvidenceDirectory(root) as evidence:
        return evidence.walk_files(maximum)


def _run_truth_report_name(run_id: str) -> str:
    return (
        "reports/run-01-backfill-vs-truth.json"
        if run_id == RUN_01_ID
        else "reports/run-02-vs-truth.json"
    )


def _expected_run_files(manifest: Mapping[str, Any]) -> set[str]:
    result = {
        "manifest.json",
        "manifest.sha256",
        "operation-log.jsonl",
        "normalized/normalized.json",
        _run_truth_report_name(manifest["run"]["id"]),
    }
    for operation in manifest["operations"]:
        result.add(operation["request"]["path"])
        result.add(operation["response"]["path"])
    return result


def _exact_path(
    spec: OperationSpec,
    *,
    version_id: str,
    microversion_id: str,
    element_id: str | None,
    part_id: str | None,
) -> str:
    if spec.kind == "version":
        return f"/documents/d/{DOCUMENT_ID}/versions/{version_id}"
    if spec.kind == "elements":
        return f"/documents/d/{DOCUMENT_ID}/v/{version_id}/elements"
    if spec.kind == "parts":
        return f"/parts/d/{DOCUMENT_ID}/v/{version_id}/e/{element_id}"
    if spec.kind == "body":
        return (
            f"/parts/d/{DOCUMENT_ID}/m/{microversion_id}/e/{element_id}"
            f"/partid/{part_id}/bodydetails"
        )
    return (
        f"/partstudios/d/{DOCUMENT_ID}/m/{microversion_id}/e/{element_id}/featurescript"
    )


def _exact_query(spec: OperationSpec) -> dict[str, Any]:
    if spec.kind == "version":
        return {"linkDocumentId": "", "parents": True}
    if spec.kind == "parts":
        return {"configuration": "", "withThumbnails": False}
    if spec.kind == "body":
        return {
            "configuration": "",
            "elementMicroversionId": "",
            "includeGeometricData": True,
            "linkDocumentId": "",
            "rollbackBarIndex": -1,
        }
    if spec.kind == "probe":
        return {
            "configuration": "",
            "elementMicroversionId": "",
            "linkDocumentId": "",
            "rollbackBarIndex": -1,
        }
    return {}


def _expected_body(spec: OperationSpec, microversion_id: str) -> Any:
    if spec.kind != "probe":
        return None
    return {
        "libraryVersion": 3029,
        "queries": {},
        "rejectMicroversionSkew": True,
        "script": BOUNDED_FEATURESCRIPT,
        "serializationVersion": "1.2.21",
        "sourceMicroversion": microversion_id,
    }


def _parse_run(manifest: Mapping[str, Any]) -> dict[str, Any]:
    run = _expect_dict(manifest["run"], "manifest.run")
    _exact_keys(
        run,
        {
            "elements",
            "id",
            "kind",
            "microversion_id",
            "version_id",
            "workspace_id",
        },
        "manifest.run",
    )
    if run["id"] not in {RUN_01_ID, RUN_ID}:
        fail("wrong run ID")
    expected_kind = "backfill" if run["id"] == RUN_01_ID else "captured"
    if run["kind"] != expected_kind:
        fail("wrong run kind")
    for field in ("version_id", "microversion_id", "workspace_id"):
        if not HEX_24.fullmatch(str(run[field])):
            fail(f"run {field} must be 24 lowercase hexadecimal characters")
    elements = _expect_dict(run["elements"], "manifest.run.elements")
    _exact_keys(elements, EXPECTED_VARIANTS, "manifest.run.elements")
    if any(not HEX_24.fullmatch(str(value)) for value in elements.values()):
        fail("run element IDs must be 24 lowercase hexadecimal characters")
    if len(set(elements.values())) != 2:
        fail("run element IDs must be distinct")
    if run["id"] == RUN_01_ID:
        if (
            run["version_id"] != RUN_01_VERSION_ID
            or run["microversion_id"] != RUN_01_MICROVERSION_ID
            or run["workspace_id"] != MAIN_WORKSPACE_ID
            or elements != RUN_01_ELEMENTS
        ):
            fail("run-01 backfill identity mismatch")
    elif (
        run["version_id"] != RUN_02_VERSION_ID
        or run["microversion_id"] != RUN_02_MICROVERSION_ID
        or run["workspace_id"] != RUN_02_WORKSPACE_ID
        or elements != RUN_02_ELEMENTS
    ):
        fail("run-02 immutable branch identity mismatch")
    return run


def _validate_request(
    request: Mapping[str, Any],
    spec: OperationSpec,
    run: Mapping[str, Any],
    tool_sha: str,
) -> None:
    _exact_keys(
        request,
        {
            "body",
            "document_id",
            "element_id",
            "endpoint",
            "expected_tool_sha256",
            "featurescript_source_sha256",
            "method",
            "microversion_id",
            "operation_name",
            "part_id",
            "path",
            "query",
            "run_role",
            "source_pins",
            "variant",
            "version_id",
            "workspace_id",
        },
        f"{spec.name}.request",
    )
    element_id = run["elements"].get(spec.variant) if spec.variant else None
    part_id = request["part_id"]
    if spec.kind in {"body", "parts", "probe"} and request["element_id"] != element_id:
        fail(f"{spec.name}: element/variant binding mismatch")
    if (
        spec.kind not in {"body", "parts", "probe"}
        and request["element_id"] is not None
    ):
        fail(f"{spec.name}: unexpected element ID")
    if spec.kind != "body" and part_id is not None:
        fail(f"{spec.name}: unexpected part ID")
    if spec.kind == "body" and not TRANSIENT_ID.fullmatch(str(part_id)):
        fail(f"{spec.name}: invalid part ID")
    expected = {
        "body": _expected_body(spec, run["microversion_id"]),
        "document_id": DOCUMENT_ID,
        "element_id": element_id,
        "endpoint": spec.endpoint,
        "expected_tool_sha256": tool_sha,
        "featurescript_source_sha256": (
            FEATURESCRIPT_SHA256 if spec.kind == "probe" else None
        ),
        "method": spec.method,
        "microversion_id": run["microversion_id"],
        "operation_name": spec.name,
        "part_id": part_id,
        "path": _exact_path(
            spec,
            version_id=run["version_id"],
            microversion_id=run["microversion_id"],
            element_id=element_id,
            part_id=part_id,
        ),
        "query": _exact_query(spec),
        "run_role": run["kind"],
        "source_pins": SOURCE_PINS,
        "variant": spec.variant,
        "version_id": run["version_id"],
        "workspace_id": run["workspace_id"],
    }
    if request != expected:
        fail(
            f"{spec.name}: request does not match exact method/path/query/body contract"
        )


def _response_microversion(response: Mapping[str, Any]) -> Any:
    value = response.get("microversionId")
    if isinstance(value, dict):
        return value.get("theId", value.get("id"))
    return value


def _validate_official_anchors(
    responses: Mapping[str, Any],
    requests: Mapping[str, Mapping[str, Any]],
    run: Mapping[str, Any],
) -> None:
    version = responses["version-anchor"]
    expected_version_name = (
        RUN_01_VERSION_NAME if run["id"] == RUN_01_ID else VERSION_NAME
    )
    parents = version.get("parents")
    if (
        version.get("documentId") != DOCUMENT_ID
        or version.get("id") != run["version_id"]
        or version.get("microversion") != run["microversion_id"]
        or version.get("name") != expected_version_name
        or version.get("parent") != START_VERSION_ID
        or not isinstance(parents, list)
        or len(parents) != 1
        or not isinstance(parents[0], dict)
        or parents[0].get("id") != START_VERSION_ID
    ):
        fail("official version response does not anchor run identity and ancestry")
    elements_response = responses["element-inventory"]
    elements = _expect_list(elements_response, "element inventory")
    if len(elements) != len(EXPECTED_VARIANTS):
        fail("official element inventory must contain exactly both Part Studios")
    actual_elements: dict[str, str] = {}
    variant_names = (
        RUN_01_VARIANT_NAMES if run["id"] == RUN_01_ID else RUN_02_VARIANT_NAMES
    )
    for item_value in elements:
        item = _expect_dict(item_value, "element inventory item")
        if item.get("elementType") != "PARTSTUDIO" or item.get("name") not in set(
            variant_names.values()
        ):
            fail("official element inventory contains unexpected element")
        variant = next(
            key for key, name in variant_names.items() if item["name"] == name
        )
        if variant in actual_elements:
            fail("official element inventory contains duplicate variant")
        actual_elements[variant] = _expect_str(item.get("id"), "element ID")
    if len(set(actual_elements.values())) != len(EXPECTED_VARIANTS):
        fail("official element inventory contains duplicate element ID")
    if actual_elements != run["elements"]:
        fail("official element inventory does not anchor manifest element IDs")
    for variant in EXPECTED_VARIANTS:
        part_name = f"{variant}-part-inventory"
        body_name = f"{variant}-body-details"
        probe_name = f"{variant}-geometry-probe"
        parts_response = responses[part_name]
        parts = _expect_list(parts_response, f"{variant} parts")
        if len(parts) != 1:
            fail(f"{variant}: expected exactly one official solid part")
        part = _expect_dict(parts[0], f"{variant} part")
        part_id = _expect_str(part.get("partId"), f"{variant} part ID")
        if (
            not TRANSIENT_ID.fullmatch(part_id)
            or part.get("bodyType") != "solid"
            or part.get("elementId") != run["elements"][variant]
            or part.get("microversionId") != run["microversion_id"]
        ):
            fail(f"{variant}: invalid official part inventory")
        if requests[body_name]["part_id"] != part_id:
            fail(f"{variant}: body request is cross-paired with another part")
        body_response = responses[body_name]
        bodies = _expect_list(body_response.get("bodies"), f"{variant} bodies")
        if (
            len(bodies) != 1
            or _expect_dict(bodies[0], f"{variant} body").get("id") != part_id
            or _response_microversion(body_response) != run["microversion_id"]
            or bodies[0].get("type") != "SOLID"
            or body_response.get("errorEnum") != "NO_ERROR"
        ):
            fail(f"{variant}: body response is not bound to official part inventory")
        probe_response = responses[probe_name]
        if (
            probe_response.get("sourceMicroversion") != run["microversion_id"]
            or probe_response.get("btType") != "BTFeatureScriptEvalResponse-1859"
            or probe_response.get("console") != ""
            or probe_response.get("notices") != []
            or not isinstance(probe_response.get("result"), dict)
        ):
            fail(f"{variant}: FeatureScript response is cross-paired")


def _verify_operation_log(
    root: EvidenceDirectory,
    operations: Sequence[Any],
    expected_tool_sha256: str,
) -> None:
    data = root.read("operation-log.jsonl", MAX_OPERATION_LOG_BYTES, "operation log")
    lines = data.splitlines(keepends=True)
    if len(lines) != len(operations):
        fail("operation log count mismatch")
    for index, (line, operation) in enumerate(zip(lines, operations, strict=True), 1):
        record = _expect_dict(
            parse_json(line, f"operation log line {index}", canonical=True),
            f"operation log line {index}",
        )
        expected = {
            "endpoint": operation["endpoint"],
            "expected_tool_sha256": expected_tool_sha256,
            "name": operation["name"],
            "order": operation["order"],
            "predecessor_microversion": operation["predecessor_microversion"],
            "response_microversion": operation["response_metadata"][
                "response_microversion"
            ],
            "status": operation["response_metadata"]["status"],
            "timestamp": operation["response_metadata"]["timestamp"],
        }
        if record != expected:
            fail(f"operation log line {index} does not match manifest")


def _verify_raw_evidence_anchored(
    root: EvidenceDirectory, expected_tool_sha256: str
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], str]:
    verify_tool_source(expected_tool_sha256)
    manifest_bytes = root.read("manifest.json", MAX_MANIFEST_BYTES, "manifest")
    manifest_hash = sha256(manifest_bytes)
    manifest = _expect_dict(
        parse_json(manifest_bytes, "manifest.json", canonical=True), "manifest"
    )
    checksum = root.read("manifest.sha256", MAX_CHECKSUM_BYTES, "manifest checksum")
    if checksum != f"{manifest_hash}  manifest.json\n".encode("ascii"):
        fail("manifest checksum mismatch")
    _exact_keys(
        manifest,
        {
            "capture_metadata",
            "compressed_raw_envelope_size",
            "expected_tool_sha256",
            "format",
            "format_status",
            "operations",
            "run",
            "source_pins",
        },
        "manifest",
    )
    if manifest["format"] != FORMAT or manifest["format_status"] != FORMAT_STATUS:
        fail("unsupported evidence format")
    if manifest["expected_tool_sha256"] != expected_tool_sha256:
        fail("manifest expected tool SHA-256 mismatch")
    if manifest["source_pins"] != SOURCE_PINS:
        fail("generator source or contract pin mismatch")
    run = _parse_run(manifest)
    metadata = _expect_dict(manifest["capture_metadata"], "capture metadata")
    _exact_keys(
        metadata,
        {
            "captured_at",
            "gzip",
            "integrity_scope",
            "library_version",
            "serialization_version",
            "tool",
        },
        "capture metadata",
    )
    if metadata["integrity_scope"] != INTEGRITY_SCOPE:
        fail("evidence must state sidecar integrity/authorship boundary")
    tool_metadata = _expect_dict(metadata["tool"], "capture tool metadata")
    if tool_metadata.get("source_sha256") != expected_tool_sha256:
        fail("capture tool SHA-256 mismatch")
    gzip_provenance = _validate_gzip_provenance(metadata["gzip"])
    reject_secrets(manifest, "manifest")
    operations = _expect_list(manifest["operations"], "operations")
    if len(operations) != MAX_OPERATIONS:
        fail(f"operation count must be exactly {MAX_OPERATIONS}")
    raw: dict[str, bytes] = {}
    parsed: dict[str, Any] = {}
    total_stored = 0
    requests: dict[str, dict[str, Any]] = {}
    responses: dict[str, Any] = {}
    for order, (spec, operation_value) in enumerate(
        zip(OPERATION_SPECS, operations, strict=True), 1
    ):
        operation = _expect_dict(operation_value, f"operation[{order}]")
        _exact_keys(
            operation,
            {
                "endpoint",
                "name",
                "order",
                "predecessor_microversion",
                "request",
                "response",
                "response_metadata",
            },
            f"operation[{order}]",
        )
        if (
            operation["order"] != order
            or operation["name"] != spec.name
            or operation["endpoint"] != spec.endpoint
            or operation["predecessor_microversion"] != run["microversion_id"]
        ):
            fail(f"operation[{order}] ordering/name/endpoint/predecessor mismatch")
        request_raw = _validate_artifact(
            root, operation["request"], gzip_provenance, f"{spec.name}.request"
        )
        response_raw = _validate_artifact(
            root, operation["response"], gzip_provenance, f"{spec.name}.response"
        )
        total_stored += operation["request"]["stored_size"]
        total_stored += operation["response"]["stored_size"]
        request = _expect_dict(
            parse_json(request_raw, f"{spec.name} request", canonical=True), "request"
        )
        response = parse_json(response_raw, f"{spec.name} response", canonical=False)
        if spec.kind in {"elements", "parts"}:
            response = _expect_list(response, f"{spec.name} response")
        else:
            response = _expect_dict(response, f"{spec.name} response")
        reject_secrets(request, f"{spec.name} request")
        reject_secrets(response, f"{spec.name} response")
        _validate_request(request, spec, run, expected_tool_sha256)
        response_metadata = _expect_dict(
            operation["response_metadata"], f"{spec.name} response metadata"
        )
        _exact_keys(
            response_metadata,
            {
                "headers",
                "library_version",
                "microversion_skew",
                "response_microversion",
                "serialization_version",
                "status",
                "timestamp",
            },
            f"{spec.name} response metadata",
        )
        if response_metadata["headers"] != selected_headers(
            response_metadata["headers"]
        ):
            fail(f"{spec.name}: response headers are not selected and normalized")
        try:
            timestamp = datetime.fromisoformat(response_metadata["timestamp"])
        except (TypeError, ValueError) as error:
            raise EvidenceError(f"{spec.name}: invalid timestamp") from error
        if (
            timestamp.tzinfo is None
            or response_metadata["status"] != 200
            or response_metadata["microversion_skew"] is not False
            or response_metadata["response_microversion"] != run["microversion_id"]
        ):
            fail(f"{spec.name}: response status/timestamp/microversion mismatch")
        if spec.kind == "probe":
            if (
                response_metadata["library_version"] != metadata["library_version"]
                or response_metadata["serialization_version"]
                != metadata["serialization_version"]
                or response.get("libraryVersion") != metadata["library_version"]
                or response.get("serializationVersion")
                != metadata["serialization_version"]
                or response.get("microversionSkew") is not False
            ):
                fail(f"{spec.name}: FeatureScript metadata mismatch")
        elif (
            response_metadata["library_version"] is not None
            or response_metadata["serialization_version"] is not None
        ):
            fail(f"{spec.name}: non-FeatureScript serialization metadata must be null")
        requests[spec.name] = request
        responses[spec.name] = response
        raw[f"{spec.name}.request"] = request_raw
        raw[f"{spec.name}.response"] = response_raw
        parsed[f"{spec.name}.request"] = request
        parsed[f"{spec.name}.response"] = response
    compressed_size = _expect_int(
        manifest["compressed_raw_envelope_size"],
        "compressed_raw_envelope_size",
        maximum=MAX_REPOSITORY_EVIDENCE_BYTES,
    )
    if total_stored != compressed_size:
        fail("compressed raw envelope size mismatch")
    _validate_official_anchors(responses, requests, run)
    _verify_operation_log(root, operations, expected_tool_sha256)
    return manifest, raw, parsed, manifest_hash


def _verify_raw_evidence(
    root: Path, expected_tool_sha256: str
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], str]:
    with EvidenceDirectory(root) as evidence:
        return _verify_raw_evidence_anchored(evidence, expected_tool_sha256)


def _canonical_axis(direction: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in direction))
    if norm == 0.0 or not math.isfinite(norm):
        fail("zero or non-finite axis direction")
    normalized = [component / norm for component in direction]
    dominant = max(range(3), key=lambda index: (abs(normalized[index]), -index))
    if normalized[dominant] < 0.0:
        normalized = [-component for component in normalized]
    return [0.0 if component == 0.0 else component for component in normalized]


def _normalize_cylinder(face: Mapping[str, Any], label: str) -> dict[str, Any]:
    _exact_keys(
        face, {"axis", "faceId", "radiusM", "surfaceType", "trim", "zBoundsM"}, label
    )
    if face["surfaceType"] != "cylinder":
        fail(f"{label}: unsupported surface")
    axis = _expect_dict(face["axis"], f"{label}.axis")
    _exact_keys(axis, {"direction", "originM"}, f"{label}.axis")
    source_direction = _vector(axis["direction"], f"{label}.direction", unit=True)
    direction = _canonical_axis(source_direction)
    origin = _vector(axis["originM"], f"{label}.origin")
    projection = sum(a * b for a, b in zip(origin, direction, strict=True))
    closest = [
        0.0
        if value - projection * axis_value == 0.0
        else value - projection * axis_value
        for value, axis_value in zip(origin, direction, strict=True)
    ]
    bounds = _expect_list(face["zBoundsM"], f"{label}.zBoundsM")
    if len(bounds) != 2:
        fail(f"{label}: z bounds must be a pair")
    z_bounds = [_number(value, f"{label}.zBoundsM") for value in bounds]
    radius = _number(face["radiusM"], f"{label}.radiusM")
    if z_bounds[0] >= z_bounds[1] or radius <= 0.0:
        fail(f"{label}: invalid bounded cylinder")
    trim = _expect_dict(face["trim"], f"{label}.trim")
    _exact_keys(trim, {"xMaxM"}, f"{label}.trim")
    return {
        "axis": {
            "closest_point_m": closest,
            "direction_unoriented": direction,
            "source_direction": source_direction,
        },
        "radius_m": radius,
        "source_face_id": _expect_str(face["faceId"], f"{label}.faceId"),
        "trim": {
            "x_max_m": (
                None
                if trim["xMaxM"] is None
                else _number(trim["xMaxM"], f"{label}.trim.xMaxM")
            )
        },
        "z_bounds_m": z_bounds,
    }


def _normalize_plane(face: Mapping[str, Any], label: str) -> dict[str, Any]:
    _exact_keys(
        face,
        {
            "boundsM",
            "faceId",
            "kind",
            "orientation",
            "outwardNormal",
            "sourceNormal",
            "stationM",
            "surfaceType",
        },
        label,
    )
    kind = face["kind"]
    if face["surfaceType"] != "plane" or kind not in {"axial", "datum"}:
        fail(f"{label}: unsupported surface")
    if (kind == "axial") != (face["stationM"] is not None):
        fail(f"{label}: axial planes require stations and datum planes prohibit them")
    orientation = face["orientation"]
    if not isinstance(orientation, bool):
        fail(f"{label}: orientation must be boolean")
    source_normal = _vector(face["sourceNormal"], f"{label}.sourceNormal", unit=True)
    outward_normal = _vector(face["outwardNormal"], f"{label}.outwardNormal", unit=True)
    expected_outward = (
        source_normal if orientation else [-value for value in source_normal]
    )
    if (
        _angle(expected_outward, outward_normal, unoriented=False)
        > ANGULAR_TOLERANCE_RAD
    ):
        fail(f"{label}: source normal/orientation/outward normal are inconsistent")
    bounds = _expect_dict(face["boundsM"], f"{label}.boundsM")
    normalized_bounds: dict[str, list[float]] = {}
    for key, value in sorted(bounds.items()):
        if key not in {"radius", "x", "y", "z"}:
            fail(f"{label}: unsupported bound {key}")
        pair = _expect_list(value, f"{label}.{key}")
        if len(pair) != 2:
            fail(f"{label}: bound must be a pair")
        normalized_pair = [_number(item, f"{label}.{key}") for item in pair]
        if normalized_pair[0] > normalized_pair[1]:
            fail(f"{label}: reversed bound")
        normalized_bounds[key] = normalized_pair
    return {
        "bounds_m": normalized_bounds,
        "kind": kind,
        "orientation": orientation,
        "outward_normal": outward_normal,
        "source_face_id": _expect_str(face["faceId"], f"{label}.faceId"),
        "source_normal": source_normal,
        "station_m": (
            None
            if face["stationM"] is None
            else _number(face["stationM"], f"{label}.stationM")
        ),
    }


def _decode_fs_value(value: Any, label: str) -> Any:
    node = _expect_dict(value, label)
    bt_type = _expect_str(node.get("btType"), f"{label}.btType")
    if "BTFSValueMap" in bt_type and "Entry" not in bt_type:
        _exact_keys(node, {"btType", "typeTag", "value"}, label)
        result: dict[str, Any] = {}
        for index, entry_value in enumerate(_expect_list(node["value"], label)):
            entry = _expect_dict(entry_value, f"{label}[{index}]")
            _exact_keys(entry, {"btType", "key", "value"}, f"{label}[{index}]")
            key = _decode_fs_value(entry["key"], f"{label}[{index}].key")
            if not isinstance(key, str) or key in result:
                fail(f"{label}: invalid or duplicate FeatureScript map key")
            result[key] = _decode_fs_value(entry["value"], f"{label}[{index}].value")
        return result
    if "BTFSValueArray" in bt_type:
        _exact_keys(node, {"btType", "typeTag", "value"}, label)
        return [
            _decode_fs_value(item, f"{label}[{index}]")
            for index, item in enumerate(_expect_list(node["value"], label))
        ]
    if "BTFSValueWithUnits" in bt_type:
        _exact_keys(node, {"btType", "typeTag", "unitToPower", "value"}, label)
        if node["unitToPower"] != {"METER": 1}:
            fail(f"{label}: expected metre FeatureScript value")
        return _number(node["value"], label)
    if "BTFSValueNumber" in bt_type:
        _exact_keys(node, {"btType", "typeTag", "value"}, label)
        return _number(node["value"], label)
    if "BTFSValueString" in bt_type:
        _exact_keys(node, {"btType", "typeTag", "value"}, label)
        return _expect_str(node["value"], label)
    if "BTFSValueBoolean" in bt_type:
        _exact_keys(node, {"btType", "typeTag", "value"}, label)
        if not isinstance(node["value"], bool):
            fail(f"{label}: expected FeatureScript boolean")
        return node["value"]
    if "BTFSValueUndefined" in bt_type:
        _exact_keys(node, {"btType", "typeTag"}, label)
        return None
    fail(f"{label}: unsupported FeatureScript value type {bt_type}")


def _body_vector(value: Any, label: str) -> list[float]:
    vector = _expect_dict(value, label)
    return [_number(vector.get(axis), f"{label}.{axis}") for axis in ("x", "y", "z")]


def _body_box(value: Any, label: str) -> tuple[list[float], list[float]]:
    box = _expect_dict(value, label)
    return (
        _body_vector(box.get("minCorner"), f"{label}.minCorner"),
        _body_vector(box.get("maxCorner"), f"{label}.maxCorner"),
    )


def _official_probe_payload(
    body_response: Mapping[str, Any],
    probe_response: Mapping[str, Any],
    *,
    element_id: str,
    variant: str,
) -> dict[str, Any]:
    result = _expect_dict(probe_response.get("result"), "probe result")
    decoded = _expect_dict(_decode_fs_value(result, "probe result"), "probe payload")
    _exact_keys(decoded, {"bodyCount", "faces", "probeVersion"}, "probe payload")
    if decoded["bodyCount"] != 1.0 or decoded["probeVersion"] != (
        "track5-stepped-rotational-v1-v1"
    ):
        fail("official probe body count or version mismatch")
    bodies = _expect_list(body_response.get("bodies"), "body response")
    if len(bodies) != 1:
        fail("body response must contain exactly one solid")
    body = _expect_dict(bodies[0], "body")
    if body.get("type") != "SOLID":
        fail("body response must contain one solid body")
    body_faces = _expect_list(body.get("faces"), "body faces")
    probe_faces = _expect_list(decoded["faces"], "probe faces")
    by_id: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(probe_faces):
        face = _expect_dict(value, f"probe face {index}")
        _exact_keys(face, {"bounds", "id", "surface", "tangent"}, f"probe face {index}")
        face_id = _expect_str(face["id"], f"probe face {index}.id")
        if not TRANSIENT_ID.fullmatch(face_id) or face_id in by_id:
            fail("official probe contains invalid or duplicate face ID")
        by_id[face_id] = face
    body_ids = {_expect_str(face.get("id"), "body face ID") for face in body_faces}
    if set(by_id) != body_ids:
        fail("official probe and body face inventories differ")
    edge_radii: dict[str, float] = {}
    for edge_value in _expect_list(body.get("edges"), "body edges"):
        edge = _expect_dict(edge_value, "body edge")
        curve = _expect_dict(edge.get("curve"), "body edge curve")
        if curve.get("type") == "CIRCLE":
            edge_radii[_expect_str(edge.get("id"), "body edge ID")] = _number(
                curve.get("radius"), "body edge radius"
            )
    faces: list[dict[str, Any]] = []
    for index, face_value in enumerate(body_faces):
        face = _expect_dict(face_value, f"body face {index}")
        face_id = _expect_str(face.get("id"), f"body face {index}.id")
        probe_face = by_id[face_id]
        minimum, maximum = _body_box(face.get("box"), f"body face {face_id}.box")
        probe_bounds = _expect_dict(
            probe_face["bounds"], f"probe face {face_id}.bounds"
        )
        if any(
            abs(left - right) > LINEAR_TOLERANCE_M
            for left, right in zip(
                _vector(probe_bounds.get("minCorner"), "probe min corner"),
                minimum,
                strict=True,
            )
        ) or any(
            abs(left - right) > LINEAR_TOLERANCE_M
            for left, right in zip(
                _vector(probe_bounds.get("maxCorner"), "probe max corner"),
                maximum,
                strict=True,
            )
        ):
            fail("official probe and body tight bounds differ")
        surface = _expect_dict(face.get("surface"), f"body face {face_id}.surface")
        probe_surface = _expect_dict(
            probe_face["surface"], f"probe face {face_id}.surface"
        )
        if surface.get("type") != probe_surface.get("surfaceType"):
            fail("official probe and body surface types differ")
        if surface.get("type") == "CYLINDER":
            radius = _number(surface.get("radius"), "cylinder radius")
            probe_coord = _expect_dict(
                probe_surface.get("coordSystem"), "probe cylinder"
            )
            if (
                abs(_number(probe_surface.get("radius"), "probe radius") - radius)
                > LINEAR_TOLERANCE_M
                or _angle(
                    _vector(probe_coord.get("zAxis"), "probe cylinder axis", unit=True),
                    _body_vector(surface.get("axis"), "body cylinder axis"),
                    unoriented=True,
                )
                > ANGULAR_TOLERANCE_RAD
            ):
                fail("official probe and body cylinders differ")
            trim = (
                maximum[0]
                if variant == "asymmetric_datum_flat"
                and abs(radius - 0.018) <= LINEAR_TOLERANCE_M
                and maximum[0] < radius - LINEAR_TOLERANCE_M
                else None
            )
            faces.append(
                {
                    "axis": {
                        "direction": _body_vector(surface.get("axis"), "cylinder axis"),
                        "originM": _body_vector(
                            surface.get("origin"), "cylinder origin"
                        ),
                    },
                    "faceId": face_id,
                    "radiusM": radius,
                    "surfaceType": "cylinder",
                    "trim": {"xMaxM": trim},
                    "zBoundsM": [minimum[2], maximum[2]],
                }
            )
            continue
        if surface.get("type") != "PLANE":
            fail("body contains unsupported surface")
        source_normal = _body_vector(surface.get("normal"), "body plane normal")
        orientation = face.get("orientation")
        if not isinstance(orientation, bool):
            fail("body plane orientation must be boolean")
        outward = source_normal if orientation else [-value for value in source_normal]
        tangent = _expect_dict(probe_face["tangent"], "probe tangent plane")
        if (
            _angle(
                _vector(tangent.get("normal"), "probe tangent normal", unit=True),
                outward,
                unoriented=False,
            )
            > ANGULAR_TOLERANCE_RAD
        ):
            fail("official probe tangent normal and body orientation differ")
        axial = abs(source_normal[2]) > 0.5
        bounds: dict[str, list[float]]
        station: float | None
        if axial:
            radii: list[float] = []
            for loop_value in _expect_list(face.get("loops"), "plane loops"):
                loop = _expect_dict(loop_value, "plane loop")
                for coedge_value in _expect_list(loop.get("coedges"), "plane coedges"):
                    edge_id = _expect_str(
                        _expect_dict(coedge_value, "plane coedge").get("edgeId"),
                        "plane edge ID",
                    )
                    if edge_id in edge_radii:
                        radii.append(edge_radii[edge_id])
            unique_radii = sorted(set(radii))
            if len(unique_radii) == 1:
                bounds = {"radius": [0.0, unique_radii[0]]}
            elif len(unique_radii) == 2:
                bounds = {"radius": unique_radii}
            else:
                fail("axial plane circular bounds are incomplete")
            if maximum[0] < bounds["radius"][1] - LINEAR_TOLERANCE_M:
                bounds["x"] = [minimum[0], maximum[0]]
            station = _body_vector(surface.get("origin"), "plane origin")[2]
            kind = "axial"
        else:
            bounds = {
                "x": [minimum[0], maximum[0]],
                "y": [minimum[1], maximum[1]],
                "z": [minimum[2], maximum[2]],
            }
            station = None
            kind = "datum"
        faces.append(
            {
                "boundsM": bounds,
                "faceId": face_id,
                "kind": kind,
                "orientation": orientation,
                "outwardNormal": outward,
                "sourceNormal": source_normal,
                "stationM": station,
                "surfaceType": "plane",
            }
        )
    return {
        "elementId": element_id,
        "frame": {"axis": "+Z", "handedness": "right", "lengthUnit": "m"},
        "probeVersion": decoded["probeVersion"],
        "solids": [{"faces": faces, "solidId": body["id"]}],
        "variant": variant,
    }


def normalize_probe_response(
    response: Mapping[str, Any], *, element_id: str, raw_sha256: str
) -> dict[str, Any]:
    payload = _expect_dict(
        _expect_dict(response.get("result"), "probe result").get("value"),
        "probe payload",
    )
    _exact_keys(
        payload,
        {"elementId", "frame", "probeVersion", "solids", "variant"},
        "probe payload",
    )
    variant = _expect_str(payload["variant"], "probe variant")
    if variant not in EXPECTED_VARIANTS or payload["elementId"] != element_id:
        fail("probe variant/element mismatch")
    if payload["probeVersion"] != "track5-stepped-rotational-v1-v1":
        fail("unsupported probe version")
    if payload["frame"] != {"axis": "+Z", "handedness": "right", "lengthUnit": "m"}:
        fail("unsupported units or frame")
    solids = _expect_list(payload["solids"], "probe solids")
    if len(solids) != 1:
        fail("probe must contain exactly one solid")
    solid = _expect_dict(solids[0], "probe solid")
    _exact_keys(solid, {"faces", "solidId"}, "probe solid")
    faces = _expect_list(solid["faces"], "probe faces")
    cylinders: list[dict[str, Any]] = []
    planes: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, face_value in enumerate(faces):
        face = _expect_dict(face_value, f"probe face {index}")
        face_id = _expect_str(face.get("faceId"), f"probe face {index} ID")
        if face_id in ids:
            fail("duplicate revision-scoped face ID")
        ids.add(face_id)
        if face.get("surfaceType") == "cylinder":
            cylinders.append(_normalize_cylinder(face, f"probe face {index}"))
        elif face.get("surfaceType") == "plane":
            planes.append(_normalize_plane(face, f"probe face {index}"))
        else:
            fail(f"probe face {index}: unsupported surface")
    cylinders.sort(key=lambda item: (item["z_bounds_m"], item["radius_m"]))
    planes.sort(
        key=lambda item: (
            item["kind"],
            math.inf if item["station_m"] is None else item["station_m"],
        )
    )
    expected_faces = 7 if variant == "axisymmetric" else 8
    if (
        len(faces) != expected_faces
        or len(cylinders) != 3
        or len(planes) != expected_faces - 3
    ):
        fail(f"{variant}: wrong face inventory")
    if sum(plane["kind"] == "datum" for plane in planes) != (
        variant == "asymmetric_datum_flat"
    ):
        fail(f"{variant}: datum presence mismatch")
    return {
        "cylinders": cylinders,
        "element_id": element_id,
        "face_count": len(faces),
        "frame": {"axis": "+Z", "handedness": "right", "length_unit": "m"},
        "planes": planes,
        "raw_response_sha256": raw_sha256,
        "solid_id": _expect_str(solid["solidId"], "solid ID"),
        "variant": variant,
    }


def _replay_verified(
    manifest: Mapping[str, Any], raw: Mapping[str, bytes], parsed: Mapping[str, Any]
) -> dict[str, Any]:
    variants = []
    response_metadata = []
    for spec, operation in zip(OPERATION_SPECS, manifest["operations"], strict=True):
        response_raw = raw[f"{spec.name}.response"]
        response_metadata.append(
            {
                "endpoint": spec.endpoint,
                "library_version": operation["response_metadata"]["library_version"],
                "name": spec.name,
                "raw_response_sha256": sha256(response_raw),
                "serialization_version": operation["response_metadata"][
                    "serialization_version"
                ],
                "timestamp": operation["response_metadata"]["timestamp"],
            }
        )
        if spec.kind == "probe":
            probe_response = parsed[f"{spec.name}.response"]
            body_name = f"{spec.variant}-body-details"
            normalization_response = dict(probe_response)
            normalization_response["result"] = {
                "value": _official_probe_payload(
                    parsed[f"{body_name}.response"],
                    probe_response,
                    element_id=manifest["run"]["elements"][spec.variant],
                    variant=spec.variant,
                )
            }
            variants.append(
                normalize_probe_response(
                    normalization_response,
                    element_id=manifest["run"]["elements"][spec.variant],
                    raw_sha256=sha256(response_raw),
                )
            )
    variants.sort(key=lambda item: item["variant"])
    return {
        "expected_tool_sha256": manifest["expected_tool_sha256"],
        "format": NORMALIZED_FORMAT,
        "format_status": FORMAT_STATUS,
        "response_metadata": response_metadata,
        "run": manifest["run"],
        "source_pins": SOURCE_PINS,
        "variants": variants,
    }


def frozen_truth() -> dict[str, Any]:
    return {
        "axis_direction_unoriented": [0.0, 0.0, 1.0],
        "axis_line_closest_point_m": [0.0, 0.0, 0.0],
        "cylinders": [
            {"radius_m": 0.012, "trim_x_max_m": None, "z_bounds_m": [0.0, 0.02]},
            {"radius_m": 0.018, "trim_x_max_m": 0.016, "z_bounds_m": [0.02, 0.05]},
            {"radius_m": 0.014, "trim_x_max_m": None, "z_bounds_m": [0.05, 0.08]},
        ],
        "datum": {
            "bounds_m": {
                "x": [0.016, 0.016],
                "y": [-0.008246211251235319, 0.008246211251235319],
                "z": [0.02, 0.05],
            },
            "orientation": False,
            "outward_normal": [1.0, 0.0, 0.0],
            "source_normal": [-1.0, 0.0, 0.0],
        },
        "face_counts": {"asymmetric_datum_flat": 8, "axisymmetric": 7},
        "planes": [
            {
                "bounds_m": {"radius": [0.0, 0.012]},
                "normal": [0.0, 0.0, -1.0],
                "station": 0.0,
            },
            {
                "bounds_m": {"radius": [0.012, 0.018]},
                "normal": [0.0, 0.0, -1.0],
                "station": 0.02,
            },
            {
                "bounds_m": {"radius": [0.014, 0.018]},
                "normal": [0.0, 0.0, 1.0],
                "station": 0.05,
            },
            {
                "bounds_m": {"radius": [0.0, 0.014]},
                "normal": [0.0, 0.0, 1.0],
                "station": 0.08,
            },
        ],
    }


def _angle(left: Sequence[float], right: Sequence[float], *, unoriented: bool) -> float:
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))
    cross = [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]
    angle = math.atan2(math.sqrt(sum(value * value for value in cross)), dot)
    return min(angle, math.pi - angle) if unoriented else angle


def within_tolerance(difference: float, tolerance: float) -> bool:
    return math.isfinite(difference) and difference <= tolerance


def _linear_finding(path: str, left: float, right: float) -> dict[str, Any]:
    difference = abs(left - right)
    return {
        "actual": left,
        "classification": ("exact" if difference == 0.0 else "tolerated numerical")
        if within_tolerance(difference, LINEAR_TOLERANCE_M)
        else "failure",
        "difference_m": difference,
        "expected": right,
        "path": path,
        "tolerance_m": LINEAR_TOLERANCE_M,
    }


def _angular_finding(
    path: str, left: Sequence[float], right: Sequence[float], *, unoriented: bool
) -> dict[str, Any]:
    difference = _angle(left, right, unoriented=unoriented)
    return {
        "actual": list(left),
        "classification": ("exact" if difference == 0.0 else "tolerated numerical")
        if within_tolerance(difference, ANGULAR_TOLERANCE_RAD)
        else "failure",
        "difference_rad": difference,
        "expected": list(right),
        "path": path,
        "tolerance_rad": ANGULAR_TOLERANCE_RAD,
        "unoriented": unoriented,
    }


def _geometry_findings(
    left: Mapping[str, Any], right: Mapping[str, Any], *, truth: bool
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    left_variants = {item["variant"]: item for item in left["variants"]}
    right_variants = (
        None if truth else {item["variant"]: item for item in right["variants"]}
    )
    expected_truth = frozen_truth()
    if set(left_variants) != set(EXPECTED_VARIANTS) or (
        right_variants is not None and set(right_variants) != set(EXPECTED_VARIANTS)
    ):
        fail("normalized variants are incomplete")
    for variant_name in EXPECTED_VARIANTS:
        actual = left_variants[variant_name]
        if truth:
            if actual["face_count"] != expected_truth["face_counts"][variant_name]:
                fail(f"{variant_name}: face count mismatch")
            expected_cylinders = expected_truth["cylinders"]
            expected_planes = expected_truth["planes"]
        else:
            other = right_variants[variant_name]
            if actual["face_count"] != other["face_count"]:
                fail(f"{variant_name}: face count mismatch")
            expected_cylinders = other["cylinders"]
            expected_planes = [p for p in other["planes"] if p["kind"] == "axial"]
        for index, cylinder in enumerate(actual["cylinders"]):
            expected = expected_cylinders[index]
            prefix = f"{variant_name}.cylinders[{index}]"
            findings.append(
                _linear_finding(
                    f"{prefix}.radius_m", cylinder["radius_m"], expected["radius_m"]
                )
            )
            for bound in range(2):
                findings.append(
                    _linear_finding(
                        f"{prefix}.z_bounds_m[{bound}]",
                        cylinder["z_bounds_m"][bound],
                        expected["z_bounds_m"][bound],
                    )
                )
            findings.append(
                _angular_finding(
                    f"{prefix}.axis.direction",
                    cylinder["axis"]["direction_unoriented"],
                    expected_truth["axis_direction_unoriented"]
                    if truth
                    else expected["axis"]["direction_unoriented"],
                    unoriented=True,
                )
            )
            expected_point = (
                expected_truth["axis_line_closest_point_m"]
                if truth
                else expected["axis"]["closest_point_m"]
            )
            for coordinate in range(3):
                findings.append(
                    _linear_finding(
                        f"{prefix}.axis.closest_point_m[{coordinate}]",
                        cylinder["axis"]["closest_point_m"][coordinate],
                        expected_point[coordinate],
                    )
                )
            expected_trim = (
                expected["trim_x_max_m"]
                if truth and variant_name == "asymmetric_datum_flat"
                else (expected["trim"]["x_max_m"] if not truth else None)
            )
            actual_trim = cylinder["trim"]["x_max_m"]
            if (actual_trim is None) != (expected_trim is None):
                fail(f"{prefix}: trim presence mismatch")
            if actual_trim is not None:
                findings.append(
                    _linear_finding(
                        f"{prefix}.trim.x_max_m", actual_trim, expected_trim
                    )
                )
        axial = [plane for plane in actual["planes"] if plane["kind"] == "axial"]
        datum = [plane for plane in actual["planes"] if plane["kind"] == "datum"]
        expected_datum = (
            []
            if variant_name == "axisymmetric"
            else [
                expected_truth["datum"]
                if truth
                else next(
                    plane
                    for plane in right_variants[variant_name]["planes"]
                    if plane["kind"] == "datum"
                )
            ]
        )
        if len(axial) != 4 or len(datum) != len(expected_datum):
            fail(f"{variant_name}: plane inventory mismatch")
        for index, plane in enumerate(axial):
            expected = expected_planes[index]
            expected_station = expected["station"] if truth else expected["station_m"]
            expected_normal = (
                expected["normal"] if truth else expected["outward_normal"]
            )
            expected_bounds = dict(expected["bounds_m"])
            if truth and variant_name == "asymmetric_datum_flat" and index in {1, 2}:
                expected_bounds["x"] = [-0.018, 0.016]
            if set(plane["bounds_m"]) != set(expected_bounds):
                fail(
                    f"{variant_name}.planes[{index}]: bounded-domain dimensions mismatch"
                )
            findings.append(
                _linear_finding(
                    f"{variant_name}.planes[{index}].station_m",
                    plane["station_m"],
                    expected_station,
                )
            )
            findings.append(
                _angular_finding(
                    f"{variant_name}.planes[{index}].outward_normal",
                    plane["outward_normal"],
                    expected_normal,
                    unoriented=False,
                )
            )
            for bound_name, expected_pair in sorted(expected_bounds.items()):
                for bound in range(2):
                    findings.append(
                        _linear_finding(
                            f"{variant_name}.planes[{index}].{bound_name}[{bound}]",
                            plane["bounds_m"][bound_name][bound],
                            expected_pair[bound],
                        )
                    )
        if datum:
            expected = expected_datum[0]
            if plane_boolean_mismatch(datum[0], expected):
                fail("datum orientation mismatch")
            findings.append(
                _angular_finding(
                    f"{variant_name}.datum.source_normal",
                    datum[0]["source_normal"],
                    expected["source_normal"],
                    unoriented=False,
                )
            )
            findings.append(
                _angular_finding(
                    f"{variant_name}.datum.outward_normal",
                    datum[0]["outward_normal"],
                    expected["outward_normal"],
                    unoriented=False,
                )
            )
            for axis in ("x", "y", "z"):
                for bound in range(2):
                    findings.append(
                        _linear_finding(
                            f"{variant_name}.datum.{axis}[{bound}]",
                            datum[0]["bounds_m"][axis][bound],
                            expected["bounds_m"][axis][bound],
                        )
                    )
    return findings


def plane_boolean_mismatch(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> bool:
    return actual["orientation"] != expected["orientation"]


def _report(label: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    classifications = sorted({finding["classification"] for finding in findings})
    outcome = (
        "failure"
        if "failure" in classifications
        else (
            "tolerated numerical"
            if "tolerated numerical" in classifications
            else "exact"
        )
    )
    return {
        "classification_vocabulary": list(CLASSIFICATION_VOCABULARY),
        "classifications": classifications,
        "findings": findings,
        "format": REPORT_FORMAT,
        "label": label,
        "outcome": outcome,
        "tolerances": {
            "angular_rad": ANGULAR_TOLERANCE_RAD,
            "linear_m": LINEAR_TOLERANCE_M,
            "policy": "absolute or angular difference <= tolerance passes",
        },
    }


def compare_to_truth(normalized: Mapping[str, Any], label: str) -> dict[str, Any]:
    try:
        findings = _geometry_findings(normalized, frozen_truth(), truth=True)
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        findings = [
            {"classification": "failure", "detail": str(error), "path": "normalized"}
        ]
    return _report(label, findings)


def _revision_ids(normalized: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    for variant in normalized["variants"]:
        result.extend([variant["element_id"], variant["solid_id"]])
        result.extend(face["source_face_id"] for face in variant["cylinders"])
        result.extend(face["source_face_id"] for face in variant["planes"])
    return result


def _source_metadata(normalized: Mapping[str, Any]) -> Any:
    return {
        "response_metadata": normalized["response_metadata"],
        "source_axes": [
            cylinder["axis"]["source_direction"]
            for variant in normalized["variants"]
            for cylinder in variant["cylinders"]
        ],
    }


def _classify_raw_changes(
    left: Mapping[str, Any], right: Mapping[str, Any], notes: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    reject_secrets(notes, "raw change notes")
    left_hashes = {
        item["name"]: item["raw_response_sha256"] for item in left["response_metadata"]
    }
    right_hashes = {
        item["name"]: item["raw_response_sha256"] for item in right["response_metadata"]
    }
    if set(left_hashes) != set(EXPECTED_CAPTURE_OPERATIONS) or set(right_hashes) != set(
        EXPECTED_CAPTURE_OPERATIONS
    ):
        fail("normalized operation metadata mismatch")
    changed = {name for name in left_hashes if left_hashes[name] != right_hashes[name]}
    if set(notes) - changed:
        fail("raw change notes include unchanged or unknown operations")
    findings: list[dict[str, Any]] = []
    unexplained = False
    allowed = {
        "expected revision-ID changes",
        "tolerated numerical",
        "unsupported",
        "variable metadata",
    }
    for name in sorted(changed):
        note = notes.get(name)
        if not isinstance(note, dict):
            findings.append(
                {
                    "classification": "unexplained",
                    "left_raw_sha256": left_hashes[name],
                    "name": name,
                    "right_raw_sha256": right_hashes[name],
                }
            )
            unexplained = True
            continue
        _exact_keys(
            note,
            {"classification", "detail", "left_raw_sha256", "right_raw_sha256"},
            f"raw change note {name}",
        )
        if (
            note["classification"] not in allowed
            or not isinstance(note["detail"], str)
            or not note["detail"]
            or note["left_raw_sha256"] != left_hashes[name]
            or note["right_raw_sha256"] != right_hashes[name]
        ):
            fail(f"raw change note {name} is invalid or not hash-pinned")
        findings.append({"name": name, **note})
    return findings, unexplained


def validate_normalized_identity(
    value: Mapping[str, Any], expected_run_id: str
) -> None:
    if (
        value.get("format") != NORMALIZED_FORMAT
        or value.get("format_status") != FORMAT_STATUS
        or value.get("source_pins") != SOURCE_PINS
    ):
        fail("normalized format or source pins mismatch")
    if value.get("run", {}).get("id") != expected_run_id:
        fail(f"expected normalized run {expected_run_id}")


def compare_runs(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    label: str,
    raw_change_notes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    validate_normalized_identity(left, RUN_01_ID)
    validate_normalized_identity(right, RUN_ID)
    left_truth = compare_to_truth(left, f"{label}:left-vs-truth")
    right_truth = compare_to_truth(right, f"{label}:right-vs-truth")
    try:
        geometry = _geometry_findings(left, right, truth=False)
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        geometry = [
            {"classification": "failure", "detail": str(error), "path": "normalized"}
        ]
    classifications = sorted({finding["classification"] for finding in geometry})
    raw_findings, unexplained = _classify_raw_changes(
        left, right, raw_change_notes or {}
    )
    if (
        all(report["outcome"] != "failure" for report in (left_truth, right_truth))
        and "failure" not in classifications
    ):
        classifications.append("semantically equivalent")
    else:
        classifications.append("failure")
    if _revision_ids(left) != _revision_ids(right):
        classifications.append("expected revision-ID changes")
    if _source_metadata(left) != _source_metadata(right):
        classifications.append("variable metadata")
    classifications.extend(finding["classification"] for finding in raw_findings)
    if unexplained:
        classifications.extend(["unexplained", "failure"])
    classifications = list(dict.fromkeys(classifications))
    return {
        "classification_vocabulary": list(CLASSIFICATION_VOCABULARY),
        "classifications": classifications,
        "findings": geometry,
        "format": REPORT_FORMAT,
        "label": label,
        "left_truth": left_truth,
        "outcome": "failure"
        if "failure" in classifications
        else "semantically equivalent",
        "raw_changes": raw_findings,
        "right_truth": right_truth,
    }


def _validate_derived_bytes(
    root: EvidenceDirectory, relative: str, expected: bytes, label: str
) -> None:
    actual = root.read(relative, MAX_DERIVED_BYTES, label)
    value = parse_json(actual, label, canonical=True)
    reject_secrets(value, label)
    if actual != expected:
        fail(f"{label}: stored derived bytes do not equal recomputed output")


def _verify_run_snapshot(root: Path, expected_tool_sha256: str) -> VerifiedRun:
    with EvidenceDirectory(root) as evidence:
        manifest, raw, parsed, manifest_sha256 = _verify_raw_evidence_anchored(
            evidence, expected_tool_sha256
        )
        actual_files = evidence.walk_files(MAX_RUN_FILES)
        expected_files = _expected_run_files(manifest)
        if actual_files != expected_files:
            fail(
                f"run evidence files mismatch; missing={sorted(expected_files - actual_files)}, unexpected={sorted(actual_files - expected_files)}"
            )
        normalized = _replay_verified(manifest, raw, parsed)
        report_label = (
            "run-01-backfill-vs-truth"
            if manifest["run"]["id"] == RUN_01_ID
            else "run-02-vs-truth"
        )
        report = compare_to_truth(normalized, report_label)
        _validate_derived_bytes(
            evidence,
            "normalized/normalized.json",
            canonical_json(normalized),
            "normalized output",
        )
        truth_report = _run_truth_report_name(manifest["run"]["id"])
        _validate_derived_bytes(
            evidence,
            truth_report,
            canonical_json(report),
            "truth report",
        )
        return VerifiedRun(manifest, manifest_sha256, normalized)


def verify_evidence(root: Path, expected_tool_sha256: str) -> dict[str, Any]:
    return _verify_run_snapshot(root, expected_tool_sha256).manifest


def replay(root: Path, expected_tool_sha256: str) -> dict[str, Any]:
    return _verify_run_snapshot(root, expected_tool_sha256).normalized


def _read_notes(root: EvidenceDirectory, relative: str) -> dict[str, Any]:
    data = root.read(relative, MAX_NOTES_BYTES, "raw change notes")
    notes = _expect_dict(
        parse_json(data, "raw change notes", canonical=True), "raw change notes"
    )
    reject_secrets(notes, "raw change notes")
    return notes


def verify_suite(
    run_01_root: Path,
    run_02_root: Path,
    suite_root: Path,
    expected_tool_sha256: str,
) -> dict[str, Any]:
    verify_tool_source(expected_tool_sha256)
    run_01_snapshot = _verify_run_snapshot(run_01_root, expected_tool_sha256)
    run_02_snapshot = _verify_run_snapshot(run_02_root, expected_tool_sha256)
    run_01 = run_01_snapshot.normalized
    run_02 = run_02_snapshot.normalized
    with EvidenceDirectory(suite_root) as evidence:
        suite_manifest_bytes = evidence.read(
            "suite-manifest.json", MAX_MANIFEST_BYTES, "suite manifest"
        )
        suite_manifest = _expect_dict(
            parse_json(suite_manifest_bytes, "suite manifest", canonical=True),
            "suite manifest",
        )
        reject_secrets(suite_manifest, "suite manifest")
        checksum = evidence.read(
            "suite-manifest.sha256", MAX_CHECKSUM_BYTES, "suite checksum"
        )
        if checksum != f"{sha256(suite_manifest_bytes)}  suite-manifest.json\n".encode(
            "ascii"
        ):
            fail("suite manifest checksum mismatch")
        expected_suite_manifest = {
            "expected_tool_sha256": expected_tool_sha256,
            "format": SUITE_FORMAT,
            "format_status": FORMAT_STATUS,
            "integrity_scope": INTEGRITY_SCOPE,
            "run_manifests": {
                RUN_01_ID: run_01_snapshot.manifest_sha256,
                RUN_ID: run_02_snapshot.manifest_sha256,
            },
            "source_pins": SOURCE_PINS,
        }
        if suite_manifest != expected_suite_manifest:
            fail("suite manifest does not bind exact verified run manifests")
        notes = _read_notes(evidence, "raw-change-notes.json")
        reports = {
            "reports/run-01-backfill-vs-truth.json": compare_to_truth(
                run_01, "run-01-backfill-vs-truth"
            ),
            "reports/run-02-vs-truth.json": compare_to_truth(run_02, "run-02-vs-truth"),
            "reports/run-01-vs-run-02.json": compare_runs(
                run_01, run_02, "run-01-vs-run-02", notes
            ),
        }
        expected_files = {
            "suite-manifest.json",
            "suite-manifest.sha256",
            "raw-change-notes.json",
            *reports,
        }
        actual_files = evidence.walk_files(MAX_SUITE_FILES)
        if actual_files != expected_files:
            fail(
                f"suite files mismatch; missing={sorted(expected_files - actual_files)}, unexpected={sorted(actual_files - expected_files)}"
            )
        for relative, report in reports.items():
            _validate_derived_bytes(
                evidence, relative, canonical_json(report), relative
            )
        return suite_manifest


def request_templates() -> list[dict[str, Any]]:
    return [
        {
            "endpoint": "createWorkspace",
            "method": "POST",
            "stage": "fresh-workspace",
            "status": "blocked-payload-review",
        },
        {
            "endpoint": "createPartStudio",
            "method": "POST",
            "stage": "authoring",
            "status": "blocked-payload-review",
        },
        {
            "endpoint": "createVersion",
            "method": "POST",
            "stage": "version",
            "status": "blocked-payload-review",
        },
        *[
            {
                "endpoint": spec.endpoint,
                "method": spec.method,
                "name": spec.name,
                "stage": "authenticated-read-capture",
                "status": "blocked-transport",
            }
            for spec in OPERATION_SPECS
        ],
    ]


def live_blockers() -> tuple[str, ...]:
    capabilities = (
        (
            AUTHENTICATED_PREFLIGHT_CAPTURE_IMPLEMENTED,
            "authenticated live preflight capture/attestation",
        ),
        (LIVE_TRANSPORT_IMPLEMENTED, "authenticated live transport"),
        (AUTHORING_PAYLOADS_FROZEN, "reviewed authoring payloads"),
        (PROBE_PAYLOAD_FROZEN, "reviewed FeatureScript probe payload"),
    )
    return tuple(label for available, label in capabilities if not available)


def build_effect_plan(expected_tool_sha256: str) -> dict[str, Any]:
    verify_tool_source(expected_tool_sha256)
    blockers = live_blockers()
    plan = {
        "document_id": DOCUMENT_ID,
        "expected_tool_sha256": expected_tool_sha256,
        "format": PLAN_FORMAT,
        "integrity_scope": INTEGRITY_SCOPE,
        "live_blockers": list(blockers),
        "live_status": f"blocked: {', '.join(blockers)}",
        "main_workspace_id_prohibited": MAIN_WORKSPACE_ID,
        "operations": request_templates(),
        "required_immediate_authenticated_reads": [
            "exact account identity and account-context SHA-256",
            "document ID, Agent Sandbox parent ID, and public visibility",
            "Start version/microversion and empty Start contents",
            "current Main workspace ID and microversion without mutation",
            "target workspace and version names absent",
            "authorization expiry and evidence disposition",
            "current tool source and reviewed SHA-256",
        ],
        "run_id": RUN_ID,
        "sandbox_folder_id": SANDBOX_FOLDER_ID,
        "start_microversion_id": START_MICROVERSION_ID,
        "start_version_id": START_VERSION_ID,
        "version_name": VERSION_NAME,
        "workspace_name": WORKSPACE_NAME,
    }
    reject_secrets(plan, "effect plan")
    return plan


def validate_effect_args(args: argparse.Namespace) -> dict[str, Any]:
    if not args.execute:
        fail("effectful stages require explicit --execute")
    tool_sha = verify_tool_source(args.expected_tool_sha256)
    if (
        args.document_id != DOCUMENT_ID
        or args.start_version_id != START_VERSION_ID
        or args.start_microversion_id != START_MICROVERSION_ID
    ):
        fail("effect IDs do not exactly match frozen protocol")
    preflight_data = read_limited(args.preflight, MAX_MANIFEST_BYTES, "preflight")
    if sha256(preflight_data) != args.preflight_hash:
        fail("preflight hash mismatch")
    record = _expect_dict(
        parse_json(preflight_data, "preflight", canonical=True), "preflight"
    )
    _exact_keys(
        record,
        {
            "account_context_sha256",
            "account_identity_sha256",
            "authenticated_read_at",
            "authorization_expires_at",
            "document_id",
            "evidence_disposition_recorded",
            "expected_tool_sha256",
            "main_microversion_id",
            "main_workspace_id",
            "parent_folder_id",
            "prohibited_content_absent",
            "public_visibility",
            "start_empty",
            "start_microversion_id",
            "start_version_id",
            "target_names_absent",
        },
        "preflight",
    )
    reject_secrets(record, "preflight")
    now = datetime.now(UTC)
    try:
        read_at = datetime.fromisoformat(record["authenticated_read_at"])
        expires = datetime.fromisoformat(record["authorization_expires_at"])
    except (TypeError, ValueError) as error:
        raise EvidenceError("preflight timestamps are invalid") from error
    if (
        read_at.tzinfo is None
        or expires.tzinfo is None
        or not (now - timedelta(minutes=5) <= read_at <= now + timedelta(seconds=30))
        or not (now < expires <= now + timedelta(minutes=10))
    ):
        fail(
            "authenticated preflight is not immediate or authorization expiry is invalid"
        )
    if (
        record["expected_tool_sha256"] != tool_sha
        or record["document_id"] != DOCUMENT_ID
        or record["parent_folder_id"] != SANDBOX_FOLDER_ID
        or record["public_visibility"] is not True
        or record["start_version_id"] != START_VERSION_ID
        or record["start_microversion_id"] != START_MICROVERSION_ID
        or record["start_empty"] is not True
        or record["main_workspace_id"] != MAIN_WORKSPACE_ID
        or not HEX_24.fullmatch(str(record["main_microversion_id"]))
        or record["target_names_absent"] != [VERSION_NAME, WORKSPACE_NAME]
        or record["evidence_disposition_recorded"] is not True
        or record["prohibited_content_absent"] is not True
        or not HEX_64.fullmatch(str(record["account_context_sha256"]))
        or not HEX_64.fullmatch(str(record["account_identity_sha256"]))
    ):
        fail("authenticated preflight trust boundary mismatch")
    return record


def execute_effect(stage: str, args: argparse.Namespace) -> Never:
    validate_effect_args(args)
    blockers = live_blockers()
    fail(f"{stage}: live execution remains blocked: {', '.join(blockers)}")


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _add_tool_hash(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-tool-sha256", required=True)


def _add_effect_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--start-version-id", required=True)
    parser.add_argument("--start-microversion-id", required=True)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--preflight-hash", required=True)
    _add_tool_hash(parser)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--output", required=True, type=Path)
    _add_tool_hash(plan)
    for stage in ("preflight", "fresh-workspace", "author", "version", "capture"):
        effect = commands.add_parser(stage)
        _add_effect_arguments(effect)
    verify = commands.add_parser("verify-evidence")
    verify.add_argument("evidence", type=Path)
    _add_tool_hash(verify)
    replay_parser = commands.add_parser("replay")
    replay_parser.add_argument("evidence", type=Path)
    replay_parser.add_argument("--output", required=True, type=Path)
    _add_tool_hash(replay_parser)
    suite = commands.add_parser("verify-suite")
    suite.add_argument("run_01", type=Path)
    suite.add_argument("run_02", type=Path)
    suite.add_argument("suite", type=Path)
    _add_tool_hash(suite)
    capture_files = commands.add_parser("assemble-capture")
    capture_files.add_argument("responses", type=Path)
    capture_files.add_argument("--output", required=True, type=Path)
    capture_files.add_argument("--run-id", required=True, choices=(RUN_01_ID, RUN_ID))
    _add_tool_hash(capture_files)
    build_suite_parser = commands.add_parser("assemble-suite")
    build_suite_parser.add_argument("run_01", type=Path)
    build_suite_parser.add_argument("run_02", type=Path)
    build_suite_parser.add_argument("--output", required=True, type=Path)
    _add_tool_hash(build_suite_parser)
    args = parser.parse_args()
    try:
        if args.command == "plan":
            data = canonical_json(build_effect_plan(args.expected_tool_sha256))
            _write_new(args.output, data)
            print(f"effect plan sha256: {sha256(data)}")
            return 0
        if args.command in {
            "preflight",
            "fresh-workspace",
            "author",
            "version",
            "capture",
        }:
            execute_effect(args.command, args)
        if args.command == "verify-evidence":
            manifest = verify_evidence(args.evidence, args.expected_tool_sha256)
            print(f"evidence: PASS ({len(manifest['operations'])} operations)")
            return 0
        if args.command == "replay":
            normalized = replay(args.evidence, args.expected_tool_sha256)
            _write_new(args.output, canonical_json(normalized))
            print("replay: PASS (stored derived output independently matched)")
            return 0
        if args.command == "verify-suite":
            verify_suite(
                args.run_01, args.run_02, args.suite, args.expected_tool_sha256
            )
            print("suite: PASS (3 recomputed reports)")
            return 0
        if args.command == "assemble-capture":
            manifest = assemble_capture(
                args.responses,
                args.output,
                args.run_id,
                args.expected_tool_sha256,
            )
            print(
                f"capture: PASS ({len(manifest['operations'])} raw responses retained)"
            )
            return 0
        if args.command == "assemble-suite":
            assemble_suite(
                args.run_01, args.run_02, args.output, args.expected_tool_sha256
            )
            print("suite assembly: PASS (3 recomputed reports)")
            return 0
    except (EvidenceError, FileExistsError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 2


def runtime_metadata(expected_tool_sha256: str) -> dict[str, Any]:
    verify_tool_source(expected_tool_sha256)
    return {
        "captured_at": datetime.now(UTC).isoformat(),
        "gzip": current_gzip_provenance(),
        "integrity_scope": INTEGRITY_SCOPE,
        "library_version": 3029,
        "serialization_version": "1.2.21",
        "tool": {
            "implementation": platform.python_implementation(),
            "python": platform.python_version(),
            "source_sha256": expected_tool_sha256,
        },
    }


def _known_run(run_id: str) -> dict[str, Any]:
    if run_id == RUN_01_ID:
        return {
            "elements": dict(RUN_01_ELEMENTS),
            "id": RUN_01_ID,
            "kind": "backfill",
            "microversion_id": RUN_01_MICROVERSION_ID,
            "version_id": RUN_01_VERSION_ID,
            "workspace_id": MAIN_WORKSPACE_ID,
        }
    if run_id == RUN_ID:
        return {
            "elements": dict(RUN_02_ELEMENTS),
            "id": RUN_ID,
            "kind": "captured",
            "microversion_id": RUN_02_MICROVERSION_ID,
            "version_id": RUN_02_VERSION_ID,
            "workspace_id": RUN_02_WORKSPACE_ID,
        }
    fail("capture run ID is not frozen")


def _capture_request(
    spec: OperationSpec,
    run: Mapping[str, Any],
    part_ids: Mapping[str, str],
    tool_sha: str,
) -> dict[str, Any]:
    element_id = run["elements"].get(spec.variant) if spec.variant else None
    part_id = part_ids[spec.variant] if spec.kind == "body" else None
    return {
        "body": _expected_body(spec, run["microversion_id"]),
        "document_id": DOCUMENT_ID,
        "element_id": element_id,
        "endpoint": spec.endpoint,
        "expected_tool_sha256": tool_sha,
        "featurescript_source_sha256": (
            FEATURESCRIPT_SHA256 if spec.kind == "probe" else None
        ),
        "method": spec.method,
        "microversion_id": run["microversion_id"],
        "operation_name": spec.name,
        "part_id": part_id,
        "path": _exact_path(
            spec,
            version_id=run["version_id"],
            microversion_id=run["microversion_id"],
            element_id=element_id,
            part_id=part_id,
        ),
        "query": _exact_query(spec),
        "run_role": run["kind"],
        "source_pins": SOURCE_PINS,
        "variant": spec.variant,
        "version_id": run["version_id"],
        "workspace_id": run["workspace_id"],
    }


def assemble_capture(
    responses_root: Path,
    output: Path,
    run_id: str,
    expected_tool_sha256: str,
) -> dict[str, Any]:
    tool_sha = verify_tool_source(expected_tool_sha256)
    run = _known_run(run_id)
    if output.exists():
        fail("capture output already exists")
    expected_inputs = {f"{spec.name}.json" for spec in OPERATION_SPECS}
    response_raw: dict[str, bytes] = {}
    responses: dict[str, Any] = {}
    with EvidenceDirectory(responses_root) as inputs:
        actual_inputs = inputs.walk_files(MAX_OPERATIONS)
        if actual_inputs != expected_inputs:
            fail(
                f"capture inputs mismatch; missing={sorted(expected_inputs - actual_inputs)}, unexpected={sorted(actual_inputs - expected_inputs)}"
            )
        for spec in OPERATION_SPECS:
            relative = f"{spec.name}.json"
            raw = inputs.read(relative, MAX_RAW_ENTITY_BYTES, relative)
            value = parse_json(raw, relative, canonical=False)
            reject_secrets(value, relative)
            if spec.kind in {"elements", "parts"}:
                if not isinstance(value, list):
                    fail(f"{relative}: expected official top-level array")
            else:
                value = _expect_dict(value, relative)
            response_raw[spec.name] = raw
            responses[spec.name] = value
    part_ids: dict[str, str] = {}
    for variant in EXPECTED_VARIANTS:
        parts = _expect_list(responses[f"{variant}-part-inventory"], f"{variant} parts")
        if len(parts) != 1:
            fail(f"{variant}: expected exactly one part during capture")
        part_ids[variant] = _expect_str(
            _expect_dict(parts[0], f"{variant} part").get("partId"),
            f"{variant} part ID",
        )
    requests = {
        spec.name: _capture_request(spec, run, part_ids, tool_sha)
        for spec in OPERATION_SPECS
    }
    _validate_official_anchors(responses, requests, run)
    captured_at = datetime.now(UTC)
    metadata = runtime_metadata(tool_sha)
    metadata["captured_at"] = captured_at.isoformat()
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent))
    )
    try:
        operations: list[dict[str, Any]] = []
        log = bytearray()
        total_stored = 0
        for order, spec in enumerate(OPERATION_SPECS, 1):
            request_raw = canonical_json(requests[spec.name])
            stored_request = deterministic_gzip(request_raw)
            stored_response = deterministic_gzip(response_raw[spec.name])
            request_path = f"requests/{order:02d}-{spec.name}.json.gz"
            response_path = f"responses/{order:02d}-{spec.name}.json.gz"
            _write_new(temporary / request_path, stored_request)
            _write_new(temporary / response_path, stored_response)
            request_descriptor = artifact_descriptor(
                request_raw, stored_request, request_path
            )
            response_descriptor = artifact_descriptor(
                response_raw[spec.name], stored_response, response_path
            )
            total_stored += (
                request_descriptor["stored_size"] + response_descriptor["stored_size"]
            )
            timestamp = (captured_at + timedelta(microseconds=order)).isoformat()
            response_metadata = {
                "headers": {},
                "library_version": 3029 if spec.kind == "probe" else None,
                "microversion_skew": False,
                "response_microversion": run["microversion_id"],
                "serialization_version": "1.2.21" if spec.kind == "probe" else None,
                "status": 200,
                "timestamp": timestamp,
            }
            operation = {
                "endpoint": spec.endpoint,
                "name": spec.name,
                "order": order,
                "predecessor_microversion": run["microversion_id"],
                "request": request_descriptor,
                "response": response_descriptor,
                "response_metadata": response_metadata,
            }
            operations.append(operation)
            log.extend(
                canonical_json(
                    {
                        "endpoint": spec.endpoint,
                        "expected_tool_sha256": tool_sha,
                        "name": spec.name,
                        "order": order,
                        "predecessor_microversion": run["microversion_id"],
                        "response_microversion": run["microversion_id"],
                        "status": 200,
                        "timestamp": timestamp,
                    }
                )
            )
        manifest = {
            "capture_metadata": metadata,
            "compressed_raw_envelope_size": total_stored,
            "expected_tool_sha256": tool_sha,
            "format": FORMAT,
            "format_status": FORMAT_STATUS,
            "operations": operations,
            "run": run,
            "source_pins": SOURCE_PINS,
        }
        manifest_bytes = canonical_json(manifest)
        _write_new(temporary / "operation-log.jsonl", bytes(log))
        _write_new(temporary / "manifest.json", manifest_bytes)
        _write_new(
            temporary / "manifest.sha256",
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii"),
        )
        verified_manifest, raw, parsed, _ = _verify_raw_evidence(temporary, tool_sha)
        normalized = _replay_verified(verified_manifest, raw, parsed)
        report = compare_to_truth(
            normalized, _run_truth_report_name(run_id).split("/")[-1][:-5]
        )
        _write_new(temporary / "normalized/normalized.json", canonical_json(normalized))
        _write_new(temporary / _run_truth_report_name(run_id), canonical_json(report))
        verify_evidence(temporary, tool_sha)
        os.rename(temporary, output)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def assemble_suite(
    run_01_root: Path,
    run_02_root: Path,
    output: Path,
    expected_tool_sha256: str,
) -> dict[str, Any]:
    tool_sha = verify_tool_source(expected_tool_sha256)
    if output.exists():
        fail("suite output already exists")
    left_snapshot = _verify_run_snapshot(run_01_root, tool_sha)
    right_snapshot = _verify_run_snapshot(run_02_root, tool_sha)
    left = left_snapshot.normalized
    right = right_snapshot.normalized
    left_hashes = {
        item["name"]: item["raw_response_sha256"] for item in left["response_metadata"]
    }
    right_hashes = {
        item["name"]: item["raw_response_sha256"] for item in right["response_metadata"]
    }
    notes = {
        name: {
            "classification": (
                "variable metadata"
                if name in {"version-anchor", "element-inventory"}
                else "expected revision-ID changes"
            ),
            "detail": "hash-pinned immutable Onshape revision response change",
            "left_raw_sha256": left_hashes[name],
            "right_raw_sha256": right_hashes[name],
        }
        for name in left_hashes
        if left_hashes[name] != right_hashes[name]
    }
    output.mkdir()
    try:
        (output / "reports").mkdir()
        _write_new(output / "raw-change-notes.json", canonical_json(notes))
        manifest = {
            "expected_tool_sha256": tool_sha,
            "format": SUITE_FORMAT,
            "format_status": FORMAT_STATUS,
            "integrity_scope": INTEGRITY_SCOPE,
            "run_manifests": {
                RUN_01_ID: left_snapshot.manifest_sha256,
                RUN_ID: right_snapshot.manifest_sha256,
            },
            "source_pins": SOURCE_PINS,
        }
        manifest_bytes = canonical_json(manifest)
        _write_new(output / "suite-manifest.json", manifest_bytes)
        _write_new(
            output / "suite-manifest.sha256",
            f"{sha256(manifest_bytes)}  suite-manifest.json\n".encode("ascii"),
        )
        reports = {
            "run-01-backfill-vs-truth.json": compare_to_truth(
                left, "run-01-backfill-vs-truth"
            ),
            "run-02-vs-truth.json": compare_to_truth(right, "run-02-vs-truth"),
            "run-01-vs-run-02.json": compare_runs(
                left, right, "run-01-vs-run-02", notes
            ),
        }
        for name, report in reports.items():
            _write_new(output / "reports" / name, canonical_json(report))
        verify_suite(run_01_root, run_02_root, output, tool_sha)
        return manifest
    except Exception:
        shutil.rmtree(output)
        raise


if __name__ == "__main__":
    sys.exit(main())
