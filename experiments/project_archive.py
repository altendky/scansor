"""Bounded ZIP project members; archive names locate bytes, hashes bind sources.

Only compressed archive authority is retained. Members are reopened sequentially
and must reach EOF to validate their CRC, expanded length and content digest.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib
from collections import deque
from collections.abc import Callable, Generator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import PurePosixPath
from typing import IO, Any, BinaryIO, cast, final, override

from experiments.project_archive_limits import (
    ProjectArchiveLimits as ProjectArchiveLimits,
)
from experiments.project_archive_limits import (
    project_archive_profile as project_archive_profile,
)
from experiments.scan_sources import ReplayableInput
from experiments.scan_workspace import ScanImportError, check_cancelled

FORMAT = "scansor-project"
VERSION = 1
_HASH = re.compile(r"[0-9a-f]{64}")
_EOCD = struct.Struct("<4s4H2IH")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_LOCAL = struct.Struct("<4s5H3I2H")


_DEFAULT_LIMITS = ProjectArchiveLimits()


def _error(message: str, code: str = "invalid_project_archive") -> ScanImportError:
    return ScanImportError(message, code)


@contextmanager
def _open_source(source: ReplayableInput) -> Generator[BinaryIO]:
    try:
        with source.open() as stream:
            yield stream
    except OSError as error:
        raise _error(
            f"Project byte authority is unavailable: {error}", "source_unavailable"
        ) from error


def _name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 0 < len(value.encode("utf-8")) <= 512
        or any(ord(char) < 32 for char in value)
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise _error("Project archive entries require safe, exact relative names.")
    return value


def _exact(stream: BinaryIO, count: int, chunk: int) -> bytes:
    parts: list[bytes] = []
    remaining = count
    while remaining:
        block = stream.read(min(remaining, chunk))
        if not block or len(block) > min(remaining, chunk):
            raise _error(
                "The project archive is truncated or its source read is invalid."
            )
        parts.append(block)
        remaining -= len(block)
    return b"".join(parts)


def _extras(data: bytes) -> None:
    cursor = 0
    while cursor < len(data):
        if cursor + 4 > len(data):
            raise _error("Malformed project archive extra metadata.")
        tag, length = struct.unpack_from("<HH", data, cursor)
        cursor += 4
        if tag == 1 or cursor + length > len(data):
            raise _error("ZIP64 or malformed project extra metadata.")
        cursor += length


def _local_directory(
    stream: BinaryIO,
    infos: list[zipfile.ZipInfo],
    directory_offset: int,
    limits: ProjectArchiveLimits,
) -> None:
    ranges: list[tuple[int, int]] = []
    metadata = 0
    for info in infos:
        _ = stream.seek(info.header_offset)
        fields = _LOCAL.unpack(_exact(stream, _LOCAL.size, limits.chunk_bytes))
        signature, version, flags, method = fields[:4]
        crc, compressed, expanded, name_size, extra_size = fields[6:]
        metadata += _LOCAL.size + name_size + extra_size
        if metadata > limits.max_directory_bytes:
            raise _error(
                "Project local headers exceed their byte bound.", "source_limit"
            )
        if (
            signature != b"PK\x03\x04"
            or version > 20
            or flags != info.flag_bits
            or method != info.compress_type
        ):
            raise _error(
                "Project local and central headers disagree or require unsupported ZIP features."
            )
        encoded = _exact(stream, name_size, limits.chunk_bytes)
        expected_name = info.filename.encode("utf-8" if flags & 0x800 else "cp437")
        if encoded != expected_name:
            raise _error("Project local and central entry names disagree.")
        _extras(_exact(stream, extra_size, limits.chunk_bytes))
        expected = (info.CRC, info.compress_size, info.file_size)
        if (crc, compressed, expanded) != expected and not (
            flags & 8 and (crc, compressed, expanded) == (0, 0, 0)
        ):
            raise _error("Project local and central member sizes disagree.")
        end = stream.tell() + info.compress_size
        if flags & 8:
            if end + 12 > directory_offset:
                raise _error("Project member data descriptor overlaps its directory.")
            _ = stream.seek(end)
            prefix = _exact(stream, 4, limits.chunk_bytes)
            if prefix == b"PK\x07\x08":
                descriptor = _exact(stream, 12, limits.chunk_bytes)
                end += 16
            else:
                descriptor = prefix + _exact(stream, 8, limits.chunk_bytes)
                end += 12
            if struct.unpack("<3I", descriptor) != expected:
                raise _error(
                    "Project member data descriptor disagrees with its directory."
                )
        if end > directory_offset:
            raise _error("Project member overlaps its directory.")
        ranges.append((info.header_offset, end))
    ordered = sorted(ranges)
    if any(left[1] > right[0] for left, right in pairwise(ordered)):
        raise _error("Project archive member ranges overlap.")


@final
class _ArchiveReader(io.RawIOBase):
    """Bound underlying reads even when zipfile requests its bounded metadata."""

    def __init__(
        self, stream: BinaryIO, length: int, limits: ProjectArchiveLimits
    ) -> None:
        self.stream = stream
        self.length = length
        self.limits = limits

    @override
    def seekable(self) -> bool:
        return True

    @override
    def readable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self.stream.tell()

    @override
    def seek(self, offset: int, whence: int = 0) -> int:
        target = (
            offset
            if whence == 0
            else self.tell() + offset
            if whence == 1
            else self.length + offset
        )
        if whence not in {0, 1, 2} or not 0 <= target <= self.length:
            raise _error("Project archive seek exceeds the selected source.")
        return self.stream.seek(target)

    @override
    def read(self, size: int = -1) -> bytes:
        remaining = self.length - self.tell()
        count = remaining if size < 0 else min(size, remaining)
        maximum = max(self.limits.max_directory_bytes, self.limits.chunk_bytes, 65558)
        if count > maximum:
            raise _error(
                "Project archive metadata read exceeds its bound.", "source_limit"
            )
        return _exact(self.stream, count, self.limits.chunk_bytes)


def _directory(
    source: ReplayableInput, limits: ProjectArchiveLimits
) -> dict[str, zipfile.ZipInfo]:
    """Bound central-directory allocation before constructing a ZipFile."""
    if (
        type(source.byte_length) is not int
        or not 22 <= source.byte_length <= limits.max_archive_bytes
    ):
        raise _error(
            "Project archive exceeds its compressed-byte bound.", "source_limit"
        )
    with _open_source(source) as raw:
        tail_size = min(source.byte_length, 65558)
        _ = raw.seek(source.byte_length - tail_size)
        tail = _exact(raw, tail_size, limits.chunk_bytes)
        marker = tail.rfind(b"PK\x05\x06")
        if marker < 0 or marker + _EOCD.size > len(tail):
            raise _error("Project archive has no complete end directory.")
        signature, disk, directory_disk, on_disk, entries, size, offset, comment = (
            _EOCD.unpack_from(tail, marker)
        )
        _ = signature
        if marker + _EOCD.size + comment != len(tail):
            raise _error("Project archive has trailing or ambiguous end data.")
        if (
            disk
            or directory_disk
            or on_disk != entries
            or entries in {0, 65535}
            or size == 0xFFFFFFFF
            or offset == 0xFFFFFFFF
        ):
            raise _error("Split and ZIP64 project archives are not supported.")
        if entries > limits.max_entries or size > limits.max_directory_bytes:
            raise _error(
                "Project archive directory exceeds its resource bounds.", "source_limit"
            )
        directory_end = source.byte_length - tail_size + marker
        if offset + size != directory_end or offset < 0:
            raise _error("Project archive directory ranges are inconsistent.")
        _ = raw.seek(offset)
        directory = _exact(raw, size, limits.chunk_bytes)
        cursor = expanded = 0
        names: set[str] = set()
        local_offsets: set[int] = set()
        for _ in range(entries):
            if cursor + _CENTRAL.size > len(directory):
                raise _error("Project archive directory is truncated.")
            fields = _CENTRAL.unpack_from(directory, cursor)
            if fields[0] != b"PK\x01\x02":
                raise _error(
                    "Project archive directory contains an unsupported record."
                )
            flags, method, compressed, unpacked = (
                fields[3],
                fields[4],
                fields[8],
                fields[9],
            )
            name_size, extra_size, comment_size, start_disk = fields[10:14]
            attributes, local_offset = fields[15], fields[16]
            end = cursor + _CENTRAL.size + name_size + extra_size + comment_size
            if (
                end > len(directory)
                or start_disk
                or compressed == 0xFFFFFFFF
                or unpacked == 0xFFFFFFFF
                or local_offset == 0xFFFFFFFF
            ):
                raise _error("Project archive member metadata is truncated or ZIP64.")
            if (
                fields[2] > 20
                or flags & ~0x080E
                or flags & 1
                or method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise _error(
                    "Encrypted or unsupported project compression is not admitted."
                )
            encoded = directory[
                cursor + _CENTRAL.size : cursor + _CENTRAL.size + name_size
            ]
            try:
                name = _name(encoded.decode("utf-8" if flags & 0x800 else "cp437"))
            except UnicodeDecodeError as error:
                raise _error("Invalid project archive entry encoding.") from error
            if name in names or local_offset in local_offsets:
                raise _error("Project archive contains duplicate or ambiguous entries.")
            mode = attributes >> 16
            if (
                stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                or attributes & 0x10
            ):
                raise _error("Project archive links and directories are not admitted.")
            if (
                unpacked > limits.max_member_bytes
                or compressed > limits.max_archive_bytes
            ):
                raise _error(
                    "Project archive member exceeds its byte bound.", "source_limit"
                )
            expanded += unpacked
            if expanded > limits.max_expanded_bytes:
                raise _error(
                    "Project archive expanded-byte total exceeds its bound.",
                    "source_limit",
                )
            if local_offset >= offset or local_offset + 30 + compressed > offset:
                raise _error("Project archive member overlaps its directory.")
            # ZIP64 metadata is rejected even when its sentinel values are absent.
            extras = directory[
                cursor + _CENTRAL.size + name_size : cursor
                + _CENTRAL.size
                + name_size
                + extra_size
            ]
            _extras(extras)
            names.add(name)
            local_offsets.add(local_offset)
            cursor = end
        if cursor != len(directory) or min(local_offsets) != 0:
            raise _error("Project archive has extra or prepended directory data.")
        _ = raw.seek(0)
        try:
            with zipfile.ZipFile(
                _ArchiveReader(raw, source.byte_length, limits), "r", allowZip64=False
            ) as archive:
                infos = archive.infolist()
                if len(infos) != entries or {item.filename for item in infos} != names:
                    raise _error(
                        "Project archive directory interpretation is ambiguous."
                    )
                _local_directory(raw, infos, offset, limits)
                return {item.filename: item for item in infos}
        except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError) as error:
            raise _error(f"Invalid project archive: {error}") from error


@final
class _MemberReader(io.RawIOBase):
    def __init__(
        self,
        member: IO[bytes],
        length: int,
        expected: str | None,
        limits: ProjectArchiveLimits,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        self.member = member
        self.length = length
        self.expected = expected
        self.limits = limits
        self.cancelled = cancelled
        self.count = 0
        self.digest = hashlib.sha256()
        self.complete = False

    @override
    def readable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self.count

    @override
    def read(self, size: int = -1) -> bytes:
        check_cancelled(self.cancelled)
        if size < 0:
            raise _error(
                "Project scan members require bounded sequential reads.", "source_limit"
            )
        if size == 0:
            return b""
        size = min(size, self.limits.chunk_bytes)
        try:
            data = self.member.read(size)
        except (
            zipfile.BadZipFile,
            OSError,
            EOFError,
            RuntimeError,
            zlib.error,
        ) as error:
            raise _error(
                f"Project archive member validation failed: {error}"
            ) from error
        if len(data) > size:
            raise _error("Project archive member exceeded the requested range.")
        self.count += len(data)
        if self.count > self.length or self.count > self.limits.max_member_bytes:
            raise _error("Project member exceeded its expanded length.", "source_limit")
        self.digest.update(data)
        if not data:
            if self.count != self.length or (
                self.expected is not None and self.digest.hexdigest() != self.expected
            ):
                raise _error(
                    "Project scan byte count or SHA-256 differs from its declaration.",
                    "source_mismatch",
                )
            self.complete = True
        check_cancelled(self.cancelled)
        return data


@dataclass(frozen=True)
class ArchiveMemberInput:
    authority: ReplayableInput
    entry: str
    byte_length: int
    filename: str
    sha256: str | None
    limits: ProjectArchiveLimits
    cancelled: Callable[[], bool] | None = None

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with _open_source(self.authority) as raw:
            try:
                with zipfile.ZipFile(
                    _ArchiveReader(raw, self.authority.byte_length, self.limits),
                    "r",
                    allowZip64=False,
                ) as archive:
                    info = archive.getinfo(self.entry)
                    if info.file_size != self.byte_length:
                        raise _error(
                            "Project source member changed before replay.",
                            "source_mismatch",
                        )
                    with archive.open(info, "r") as stream:
                        reader = _MemberReader(
                            stream,
                            self.byte_length,
                            self.sha256,
                            self.limits,
                            self.cancelled,
                        )
                        yield cast(BinaryIO, cast(object, reader))
                        if not reader.complete:
                            raise _error(
                                "Project member was not consumed through its validated EOF."
                            )
            except (
                zipfile.BadZipFile,
                zipfile.LargeZipFile,
                KeyError,
                NotImplementedError,
                zlib.error,
            ) as error:
                raise _error(
                    f"Project archive member is unavailable: {error}"
                ) from error


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in values:
        if name in result:
            raise _error("Duplicate keys in project JSON.")
        result[name] = value
    return result


def _nonfinite(value: str) -> Any:
    raise _error("Nonfinite project JSON: " + value)


def _json_member(
    authority: ReplayableInput,
    info: zipfile.ZipInfo,
    maximum: int,
    limits: ProjectArchiveLimits,
    cancelled: Callable[[], bool] | None,
) -> dict[str, Any]:
    if info.file_size > maximum:
        raise _error("Project JSON exceeds its declared byte budget.", "source_limit")
    member = ArchiveMemberInput(
        authority, info.filename, info.file_size, info.filename, None, limits, cancelled
    )
    parts: list[bytes] = []
    with member.open() as stream:
        while block := stream.read(limits.chunk_bytes):
            parts.append(block)
    try:
        value = json.loads(
            b"".join(parts),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise _error(f"Invalid project JSON: {error}") from error
    if not isinstance(value, dict):
        raise _error("Project JSON must be an object.")
    return cast(dict[str, Any], value)


def _source(actions: Mapping[str, Any]) -> dict[str, Any] | None:
    nodes = actions.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise _error("Project actions require an explicit list of declarations.")
    declarations = cast(list[dict[str, Any]], nodes)
    sources = [node for node in declarations if node.get("operation") == "source"]
    if not nodes:
        return None
    if len(sources) != 1:
        raise _error("Project actions require exactly one active source declaration.")
    return sources[0]


def _identity(source: dict[str, Any]) -> tuple[str, int | None]:
    lineage = source.get("virtual_scan")
    descriptor = (
        cast(dict[str, Any], lineage).get("original")
        if isinstance(lineage, dict)
        else source.get("descriptor")
    )
    digest = (
        cast(dict[str, Any], descriptor).get("source_sha256")
        if isinstance(descriptor, dict)
        else source.get("source_sha256")
    )
    length = (
        cast(dict[str, Any], descriptor).get("source_bytes")
        if isinstance(descriptor, dict)
        else None
    )
    if (
        not isinstance(digest, str)
        or _HASH.fullmatch(digest) is None
        or (length is not None and (type(length) is not int or length <= 0))
    ):
        raise _error("Project source byte identity is invalid.")
    return digest, length


@dataclass(frozen=True)
class ProjectArchive:
    actions: dict[str, Any]
    original: ArchiveMemberInput | None
    authority: ReplayableInput
    manifest: dict[str, Any]
    legacy_reference: dict[str, Any] | None = None
    migrated_legacy: bool = False

    @property
    def member_name(self) -> str | None:
        return self.original.entry if self.original is not None else None


def read_project_archive(
    source: ReplayableInput,
    *,
    limits: ProjectArchiveLimits = _DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
    legacy_resolver: Callable[[dict[str, Any], ReplayableInput], dict[str, Any]]
    | None = None,
) -> ProjectArchive:
    check_cancelled(cancelled)
    infos = _directory(source, limits)
    if "actions.json" not in infos:
        raise _error("Project archive is missing actions.json.")
    actions = _json_member(
        source, infos["actions.json"], limits.max_actions_bytes, limits, cancelled
    )
    declaration = _source(actions)
    if "project.json" in infos:
        manifest = _json_member(
            source, infos["project.json"], limits.max_metadata_bytes, limits, cancelled
        )
        if (
            set(manifest) - {"format", "version", "actions", "legacy_reference"}
            or manifest.get("format") != FORMAT
            or type(manifest.get("version")) is not int
            or manifest["version"] != VERSION
            or manifest.get("actions") != "actions.json"
        ):
            raise _error("The project archive version or manifest is unsupported.")
        reference = None
        if manifest.get("legacy_reference") is not None:
            reference_name = _name(manifest["legacy_reference"])
            if reference_name not in infos or reference_name in {
                "project.json",
                "actions.json",
            }:
                raise _error("Project archive is missing its legacy reference record.")
            reference = _json_member(
                source,
                infos[reference_name],
                limits.max_metadata_bytes,
                limits,
                cancelled,
            )
        if declaration is None:
            if reference is not None:
                raise _error("An empty project cannot carry a legacy source reference.")
            return ProjectArchive(actions, None, source, manifest)
        entry = _name(declaration.pop("archive_entry", None))
        if entry not in infos or entry in {
            "project.json",
            "actions.json",
            manifest.get("legacy_reference"),
        }:
            raise _error(
                "Project source declaration references a missing or reserved entry."
            )
        digest, length = _identity(declaration)
        info = infos[entry]
        if length is not None and length != info.file_size:
            raise _error(
                "Project scan member length differs from its descriptor.",
                "source_mismatch",
            )
        if declaration.get("descriptor") is None and reference is None:
            raise _error("Legacy project actions require portable reference metadata.")
        original = ArchiveMemberInput(
            source, entry, info.file_size, PurePosixPath(entry).name, digest, limits
        )
        return ProjectArchive(actions, original, source, manifest, reference)
    # The handoff experiment is deliberately not a general filename heuristic.
    if (
        legacy_resolver is None
        or declaration is None
        or declaration.get("descriptor") is not None
        or "archive_entry" in declaration
        or len(infos) != 2
    ):
        raise _error("Unversioned projects require an explicit known legacy migration.")
    entries = [name for name in infos if name != "actions.json"]
    entry = entries[0]
    if not entry.lower().endswith(".ply"):
        raise _error("The known legacy project requires exactly one PLY member.")
    digest, length = _identity(declaration)
    info = infos[entry]
    if length is not None and info.file_size != length:
        raise _error(
            "Legacy project member length differs from its declaration.",
            "source_mismatch",
        )
    original = ArchiveMemberInput(
        source, entry, info.file_size, PurePosixPath(entry).name, digest, limits
    )
    reference = legacy_resolver(actions, original)
    return ProjectArchive(
        actions,
        original,
        source,
        {
            "format": FORMAT,
            "version": VERSION,
            "actions": "actions.json",
            "legacy_reference": "references/legacy.json",
        },
        reference,
        True,
    )


@dataclass(frozen=True)
class ProjectWriteReceipt:
    bytes_written: int
    source_entry: str | None
    source_sha256: str | None
    source_bytes: int
    queued_peak_bytes: int


@final
class _QueueWriter:
    def __init__(self, limits: ProjectArchiveLimits) -> None:
        self.limits = limits
        self.queue: deque[bytes] = deque()
        self.count = self.queued = self.peak = 0

    def write(self, value: bytes | memoryview) -> int:
        size = len(value)
        if self.count + size > self.limits.max_archive_bytes:
            raise _error(
                "Saved project exceeds its compressed output budget.", "source_limit"
            )
        self.count += size
        self.queued += size
        self.peak = max(self.peak, self.queued)
        if self.queued > max(
            self.limits.max_directory_bytes, 3 * self.limits.chunk_bytes + 65536
        ):
            raise _error(
                "Project compressor exceeded its bounded output queue.", "source_limit"
            )
        self.queue.append(bytes(value))
        return size

    def tell(self) -> int:
        return self.count

    def flush(self) -> None:
        pass

    def drain(self) -> Iterator[bytes]:
        while self.queue:
            item = self.queue.popleft()
            self.queued -= len(item)
            for start in range(0, len(item), self.limits.chunk_bytes):
                yield item[start : start + self.limits.chunk_bytes]


def _encoded(value: object, maximum: int) -> bytes:
    try:
        data = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as error:
        raise _error(f"Project definitions are not valid JSON: {error}") from error
    if len(data) > maximum:
        raise _error("Project definitions exceed their byte budget.", "source_limit")
    return data


def iter_project_archive(
    actions: dict[str, Any],
    original: ReplayableInput | None,
    *,
    limits: ProjectArchiveLimits = _DEFAULT_LIMITS,
    legacy_reference: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Generator[bytes, None, ProjectWriteReceipt]:
    """Yield bounded compressed chunks, including empty cancellation/ACK points."""
    payload = json.loads(_encoded(actions, limits.max_actions_bytes))
    declaration = _source(payload)
    source_entry = digest = None
    source_bytes = 0
    if declaration is not None:
        if original is None:
            raise _error("A project source requires replayable original bytes.")
        digest, length = _identity(declaration)
        if length is not None and original.byte_length != length:
            raise _error(
                "Project source length differs from its declaration.", "source_mismatch"
            )
        if not 0 < original.byte_length <= limits.max_member_bytes:
            raise _error(
                "Project source exceeds its expanded-byte bound.", "source_limit"
            )
        if declaration.get("descriptor") is None and legacy_reference is None:
            raise _error(
                "Saving a legacy project requires portable reference metadata."
            )
        source_entry = f"scans/{digest}.ply"
        declaration["archive_entry"] = source_entry
        source_bytes = original.byte_length
    elif original is not None or legacy_reference is not None:
        raise _error("An empty project cannot carry source bytes or legacy references.")
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "actions": "actions.json",
    }
    controls = [
        ("project.json", b""),
        ("actions.json", _encoded(payload, limits.max_actions_bytes)),
    ]
    if legacy_reference is not None:
        manifest["legacy_reference"] = "references/legacy.json"
        controls.append(
            (
                "references/legacy.json",
                _encoded(legacy_reference, limits.max_metadata_bytes),
            )
        )
    controls[0] = ("project.json", _encoded(manifest, limits.max_metadata_bytes))
    if (
        len(controls) + int(original is not None) > limits.max_entries
        or any(len(data) > limits.max_member_bytes for _, data in controls)
        or source_bytes + sum(len(data) for _, data in controls)
        > limits.max_expanded_bytes
    ):
        raise _error(
            "Saved project exceeds its aggregate archive bounds.", "source_limit"
        )
    queue = _QueueWriter(limits)
    with zipfile.ZipFile(
        cast(BinaryIO, cast(object, queue)),
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=False,
    ) as archive:
        for name, data in controls:
            check_cancelled(cancelled)
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.file_size = len(data)
            with archive.open(info, "w", force_zip64=False) as member:
                yield from queue.drain()
                for start in range(0, len(data), limits.chunk_bytes):
                    check_cancelled(cancelled)
                    _ = member.write(data[start : start + limits.chunk_bytes])
                    emitted = False
                    for chunk in queue.drain():
                        emitted = True
                        yield chunk
                    if not emitted:
                        yield b""
            yield from queue.drain()
        if original is not None:
            assert source_entry is not None
            source_digest = hashlib.sha256()
            count = 0
            info = zipfile.ZipInfo(source_entry)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.file_size = original.byte_length
            with (
                archive.open(info, "w", force_zip64=False) as member,
                _open_source(original) as stream,
            ):
                yield from queue.drain()
                while True:
                    check_cancelled(cancelled)
                    chunk = stream.read(limits.chunk_bytes)
                    if not chunk:
                        break
                    if len(chunk) > limits.chunk_bytes:
                        raise _error(
                            "Project source exceeded the requested read bound."
                        )
                    count += len(chunk)
                    if count > original.byte_length:
                        raise _error(
                            "Project source grew during saving.", "source_mismatch"
                        )
                    source_digest.update(chunk)
                    _ = member.write(chunk)
                    emitted = False
                    for compressed in queue.drain():
                        emitted = True
                        yield compressed
                    if not emitted:
                        yield b""
                if count != original.byte_length or source_digest.hexdigest() != digest:
                    raise _error(
                        "Project source bytes changed during saving.", "source_mismatch"
                    )
            yield from queue.drain()
        check_cancelled(cancelled)
        if (
            sum(46 + len(info.filename.encode()) for info in archive.infolist())
            > limits.max_directory_bytes
        ):
            raise _error(
                "Saved project directory exceeds its byte budget.", "source_limit"
            )
    yield from queue.drain()
    return ProjectWriteReceipt(
        queue.count, source_entry, digest, source_bytes, queue.peak
    )


def write_project_archive(
    actions: dict[str, Any],
    original: ReplayableInput | None,
    sink: BinaryIO,
    *,
    limits: ProjectArchiveLimits = _DEFAULT_LIMITS,
    legacy_reference: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ProjectWriteReceipt:
    producer = iter_project_archive(
        actions,
        original,
        limits=limits,
        legacy_reference=legacy_reference,
        cancelled=cancelled,
    )
    try:
        while True:
            try:
                block = next(producer)
            except StopIteration as finished:
                return cast(ProjectWriteReceipt, finished.value)
            if block and sink.write(block) != len(block):
                raise _error(
                    "Project output sink did not accept its complete bounded chunk."
                )
    finally:
        producer.close()
