"""Bounded ASCII/binary STL facet decoding, without application semantics.

Coordinates and supplied facet normals are interpreted as little-endian binary32.
ASCII decimal tokens first round to binary64, then binary32; signed zero is kept.
Normals may be zero or inconsistent with winding. They are not normalized or
treated as measurement truth here. Connectivity, derived normals, area weights,
repair, units and vertex observations belong to the caller.

The caller supplies fresh sequential streams and their exact byte length.
Inspection validates/counts the complete source. Iteration revalidates its exact
identity while emitting bounded, owned facet chunks; exhaust the iterator before
accepting its results. Neither pass seeks or retains a whole-source byte buffer.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import BinaryIO, Literal, final

import numpy as np
from numpy.typing import NDArray

Encoding = Literal["ascii", "binary"]
Category = Literal["structure", "unsupported", "resource", "io", "cancelled"]
StreamFactory = Callable[[], AbstractContextManager[BinaryIO]]


class StlError(ValueError):
    def __init__(
        self, category: Category, message: str, *, facet: int | None = None
    ) -> None:
        self.category: Category = category
        self.facet: int | None = facet
        suffix = "" if facet is None else f" at facet {facet}"
        super().__init__(f"STL {category}{suffix}: {message}")


class StlCancelled(StlError):
    def __init__(self) -> None:
        super().__init__("cancelled", "decoding cancelled")


@dataclass(frozen=True)
class StlLimits:
    max_source_bytes: int
    max_facets: int
    chunk_bytes: int = 65_536
    max_line_bytes: int = 4096
    max_token_bytes: int = 128

    def __post_init__(self) -> None:
        for name in (
            "max_source_bytes",
            "max_facets",
            "chunk_bytes",
            "max_line_bytes",
            "max_token_bytes",
        ):
            value = getattr(self, name)
            minimum = 0 if name == "max_facets" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer at least {minimum}")


@dataclass(frozen=True)
class StlProgress:
    phase: str
    completed: int
    total: int


@dataclass(frozen=True)
class StlMetadata:
    encoding: Encoding
    byte_length: int
    facets: int
    source_sha256: str


@dataclass(frozen=True)
class StlFacetChunk:
    start: int
    vertices: NDArray[np.float32]
    normals: NDArray[np.float32]


_FACET = np.dtype(
    [("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")]
)
_NUMBER = re.compile(rb"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")


def _check(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise StlCancelled()


@final
class _Reader:
    def __init__(
        self,
        stream: BinaryIO,
        length: int,
        limits: StlLimits,
        phase: str,
        progress: Callable[[StlProgress], None] | None,
        cancelled: Callable[[], bool] | None,
    ) -> None:
        self.stream = stream
        self.length = length
        self.limits = limits
        self.phase = phase
        self.progress = progress
        self.cancelled = cancelled
        self.position = 0
        self.digest = hashlib.sha256()
        self.complete = False

    def report(self) -> None:
        _check(self.cancelled)
        if self.progress is not None:
            self.progress(StlProgress(self.phase, self.position, self.length))
        _check(self.cancelled)

    def read(self, size: int) -> bytes:
        self.report()
        requested = min(size, self.limits.chunk_bytes, self.length - self.position)
        if requested <= 0:
            return b""
        chunk = self.stream.read(requested)
        if not chunk or len(chunk) > requested:
            raise StlError("structure", "truncated source or invalid stream read")
        self.position += len(chunk)
        self.digest.update(chunk)
        self.report()
        return chunk

    def exact(self, size: int) -> bytes:
        if size > self.length - self.position:
            raise StlError("structure", "truncated facet/header payload")
        buffer = bytearray()
        while len(buffer) < size:
            buffer.extend(self.read(size - len(buffer)))
        return bytes(buffer)

    def finish(self) -> str:
        self.report()
        if self.position != self.length:
            raise StlError("structure", "unconsumed source payload")
        if self.stream.read(1):
            raise StlError("structure", "source exceeds its declared byte length")
        self.complete = True
        return self.digest.hexdigest()


def _length(length: int, limits: StlLimits) -> None:
    if type(length) is not int or length <= 0:
        raise StlError("structure", "source length must be a positive integer")
    if length > limits.max_source_bytes:
        raise StlError("resource", "source exceeds the byte limit")


def _detect(reader: _Reader) -> tuple[Encoding, bytes]:
    prefix = reader.exact(min(84, reader.length))
    if len(prefix) == 84:
        count = struct.unpack_from("<I", prefix, 80)[0]
        if 84 + 50 * count == reader.length:
            return "binary", prefix
    # Binary headers are arbitrary and can start with 'solid'. Exact binary
    # length/count agreement takes precedence over text magic, without seeking.
    # The first text token may follow bounded blank lines/indentation beyond
    # this probe. The strict ASCII parser validates 'solid' once its line is
    # complete; printable unknown formats still fail that grammar explicitly.
    if all(value in (9, 10, 13) or 32 <= value <= 126 for value in prefix):
        return "ascii", prefix
    raise StlError(
        "structure",
        "source has neither an ASCII solid header nor an exact binary STL layout",
    )


def _freeze(array: NDArray[np.float32]) -> NDArray[np.float32]:
    array.flags.writeable = False
    return array


def _binary(reader: _Reader, prefix: bytes) -> Iterator[StlFacetChunk]:
    if len(prefix) != 84:
        raise StlError("structure", "truncated binary STL header")
    count = struct.unpack_from("<I", prefix, 80)[0]
    if 84 + 50 * count != reader.length:
        raise StlError("structure", "binary facet count disagrees with source length")
    if count > reader.limits.max_facets:
        raise StlError("resource", "facet count exceeds its limit")
    if b"COLOR=" in prefix[:80] or b"MATERIAL=" in prefix[:80]:
        raise StlError(
            "unsupported", "binary header color/material extensions are unsupported"
        )
    # A single 50-byte record is the minimum work unit even for tiny I/O chunks.
    step = max(1, min(reader.limits.chunk_bytes // 50, count))
    for start in range(0, count, step):
        _check(reader.cancelled)
        rows = np.frombuffer(reader.exact(min(step, count - start) * 50), dtype=_FACET)
        attributes = rows["attribute"]
        if np.any(attributes != 0):
            first = start + int(np.flatnonzero(attributes != 0)[0])
            raise StlError(
                "unsupported",
                "nonzero facet attributes/color extensions are unsupported",
                facet=first,
            )
        vertices = rows["vertices"].copy()
        normals = rows["normal"].copy()
        finite = np.isfinite(vertices).all(axis=(1, 2)) & np.isfinite(normals).all(
            axis=1
        )
        if not finite.all():
            raise StlError(
                "structure",
                "coordinates and facet normal components must be finite",
                facet=start + int(np.flatnonzero(~finite)[0]),
            )
        yield StlFacetChunk(start, _freeze(vertices), _freeze(normals))


def _lines(reader: _Reader, prefix: bytes) -> Iterator[bytes]:
    buffer = bytearray(prefix)
    while True:
        _check(reader.cancelled)
        end = buffer.find(b"\n")
        if end >= 0:
            if end > reader.limits.max_line_bytes:
                raise StlError("resource", "ASCII line exceeds its byte limit")
            line = bytes(buffer[: end + 1])
            del buffer[: end + 1]
            yield line
            continue
        if len(buffer) > reader.limits.max_line_bytes:
            raise StlError("resource", "ASCII line exceeds its byte limit")
        if reader.position == reader.length:
            if buffer:
                yield bytes(buffer)
            return
        buffer.extend(reader.read(reader.limits.chunk_bytes))


def _tokens(reader: _Reader, prefix: bytes) -> Iterator[list[bytes]]:
    for line in _lines(reader, prefix):
        if any(
            (value < 32 and value not in (9, 10, 13)) or value > 126 for value in line
        ):
            raise StlError(
                "structure", "ASCII STL requires printable ASCII and whitespace"
            )
        tokens = line.split()
        if any(len(token) > reader.limits.max_token_bytes for token in tokens):
            raise StlError("resource", "ASCII token exceeds its byte limit")
        if tokens:
            yield tokens


def _next(
    tokens: Iterator[list[bytes]], expected: tuple[bytes, ...], facet: int | None = None
) -> list[bytes]:
    line = next(tokens, [])
    if tuple(line[: len(expected)]) != expected:
        raise StlError(
            "structure",
            f"expected {' '.join(word.decode('ascii') for word in expected)}",
            facet=facet,
        )
    return line


def _vector(
    tokens: list[bytes], prefix: tuple[bytes, ...], facet: int
) -> NDArray[np.float32]:
    if len(tokens) != len(prefix) + 3 or tuple(tokens[: len(prefix)]) != prefix:
        raise StlError(
            "structure", "expected exactly three numeric components", facet=facet
        )
    numbers = tokens[len(prefix) :]
    if any(_NUMBER.fullmatch(number) is None for number in numbers):
        raise StlError("structure", "invalid finite decimal token", facet=facet)
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        result = np.array([float(number) for number in numbers], dtype="<f4")
    if not np.isfinite(result).all():
        raise StlError(
            "structure", "decimal component exceeds finite binary32 range", facet=facet
        )
    return result


def _ascii(reader: _Reader, prefix: bytes) -> Iterator[StlFacetChunk]:
    tokens = _tokens(reader, prefix)
    _ = _next(tokens, (b"solid",))
    step = max(
        1,
        min(
            reader.limits.chunk_bytes // 50,
            reader.limits.max_facets,
            reader.length // 50,
        ),
    )
    vertices = np.empty((step, 3, 3), dtype="<f4")
    normals = np.empty((step, 3), dtype="<f4")
    count = filled = 0
    while True:
        _check(reader.cancelled)
        line = next(tokens, [])
        if not line:
            raise StlError(
                "structure", "missing ASCII endsolid terminator", facet=count
            )
        if line[:1] == [b"endsolid"]:
            if next(tokens, None) is not None:
                raise StlError(
                    "unsupported",
                    "multiple solids or trailing ASCII content are unsupported",
                )
            break
        if count >= reader.limits.max_facets:
            raise StlError("resource", "facet count exceeds its limit")
        normal = _vector(line, (b"facet", b"normal"), count)
        if _next(tokens, (b"outer", b"loop"), count) != [b"outer", b"loop"]:
            raise StlError(
                "structure", "outer loop accepts no extra tokens", facet=count
            )
        normals[filled] = normal
        for corner in range(3):
            vertices[filled, corner] = _vector(next(tokens, []), (b"vertex",), count)
        if _next(tokens, (b"endloop",), count) != [b"endloop"] or _next(
            tokens, (b"endfacet",), count
        ) != [b"endfacet"]:
            raise StlError(
                "structure", "facet terminators accept no extra tokens", facet=count
            )
        count += 1
        filled += 1
        if filled == step:
            yield StlFacetChunk(count - filled, _freeze(vertices), _freeze(normals))
            vertices = np.empty((step, 3, 3), dtype="<f4")
            normals = np.empty((step, 3), dtype="<f4")
            filled = 0
    if filled:
        yield StlFacetChunk(
            count - filled,
            _freeze(vertices[:filled].copy()),
            _freeze(normals[:filled].copy()),
        )


def inspect_stl(
    open_stream: StreamFactory,
    byte_length: int,
    *,
    limits: StlLimits,
    progress: Callable[[StlProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> StlMetadata:
    """Validate/count all facets while retaining only bounded work chunks."""
    _length(byte_length, limits)
    _check(cancelled)
    try:
        with open_stream() as stream:
            reader = _Reader(
                stream, byte_length, limits, "inspect", progress, cancelled
            )
            encoding, prefix = _detect(reader)
            count = 0
            for chunk in (
                _binary(reader, prefix)
                if encoding == "binary"
                else _ascii(reader, prefix)
            ):
                count += len(chunk.vertices)
            return StlMetadata(encoding, byte_length, count, reader.finish())
    except OSError as error:
        raise StlError("io", str(error)) from error


def iter_stl_facets(
    open_stream: StreamFactory,
    metadata: StlMetadata,
    *,
    limits: StlLimits,
    progress: Callable[[StlProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[StlFacetChunk]:
    """Replay owned binary32 facet chunks; exhaust to attest metadata identity."""
    _length(metadata.byte_length, limits)
    if (
        metadata.encoding not in ("ascii", "binary")
        or type(metadata.facets) is not int
        or not 0 <= metadata.facets <= limits.max_facets
    ):
        raise StlError("structure", "invalid or inadmissible STL metadata")
    if re.fullmatch(r"[0-9a-f]{64}", metadata.source_sha256) is None:
        raise StlError("structure", "invalid STL source hash")
    _check(cancelled)
    try:
        with open_stream() as stream:
            reader = _Reader(
                stream, metadata.byte_length, limits, "facets", progress, cancelled
            )
            encoding, prefix = _detect(reader)
            if encoding != metadata.encoding:
                raise StlError("structure", "STL encoding changed between passes")
            count = 0
            for chunk in (
                _binary(reader, prefix)
                if encoding == "binary"
                else _ascii(reader, prefix)
            ):
                count += len(chunk.vertices)
                if count > metadata.facets:
                    raise StlError(
                        "structure", "STL facet count changed between passes"
                    )
                yield chunk
            digest = reader.finish()
            if count != metadata.facets or digest != metadata.source_sha256:
                raise StlError(
                    "structure", "STL count or source hash changed between passes"
                )
    except OSError as error:
        raise StlError("io", str(error)) from error
