"""Bounded portable archives of accepted selection geometry, never scan buffers.

Record validation does not expand or inspect the geometry. Adoption explicitly
decodes, verifies and validates it; graph snapshots can carry the frozen record
without allocating the expanded support again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from collections.abc import Callable, Iterator
from typing import Any, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from experiments.actions_limits import (
    MAX_VOLUME_ENCODED_BYTES,
    MAX_VOLUME_EXPANDED_BYTES,
)
from experiments.scan_workspace import check_cancelled
from experiments.selection_volume import validate_selection_volume

_CHUNK_BYTES = 64 * 1024
_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64_PATTERN = re.compile(r"[A-Za-z0-9+/]*={0,2}", re.ASCII)


class VolumeArchive(BaseModel):
    """Content identity is independent of compressor settings and versions."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )
    format: Literal["scansor-volume-archive-v1"] = "scansor-volume-archive-v1"
    encoding: Literal["zlib-base64"] = "zlib-base64"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    uncompressed_bytes: int
    data: str

    @field_validator("uncompressed_bytes")
    @classmethod
    def bounded_expansion(cls, value: int) -> int:
        if not 0 < value <= MAX_VOLUME_EXPANDED_BYTES:
            raise ValueError("volume archive exceeds the expanded geometry limit")
        return value

    @field_validator("data")
    @classmethod
    def canonical_base64(cls, value: str) -> str:
        if not 0 < len(value) <= MAX_VOLUME_ENCODED_BYTES:
            raise ValueError("volume archive exceeds the encoded geometry limit")
        if len(value) % 4 or _BASE64_PATTERN.fullmatch(value) is None:
            raise ValueError("volume archive requires canonical ASCII base64")
        if (value.endswith("==") and _BASE64.index(value[-3]) & 15) or (
            value.endswith("=")
            and not value.endswith("==")
            and _BASE64.index(value[-2]) & 3
        ):
            raise ValueError("volume archive requires canonical base64 padding bits")
        return value


def _canonical_chunks(value: object) -> Iterator[bytes]:
    encoder = json.JSONEncoder(
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    fragments: list[str] = []
    length = 0
    for fragment in encoder.iterencode(value):
        # Coalesce numeric fragments before compression/hash/cancellation work.
        # ASCII escaping makes the number of output bytes equal to characters.
        for start in range(0, len(fragment), _CHUNK_BYTES):
            piece = fragment[start : start + _CHUNK_BYTES]
            fragments.append(piece)
            length += len(piece)
            if length >= _CHUNK_BYTES:
                yield "".join(fragments).encode("ascii")
                fragments = []
                length = 0
    if fragments:
        yield "".join(fragments).encode("ascii")


def encode_selection_volume(
    volume: object, *, cancelled: Callable[[], bool] | None = None
) -> VolumeArchive:
    """Validate and compress a definition with explicit expansion admission."""
    check_cancelled(cancelled)
    saved = validate_selection_volume(volume)
    check_cancelled(cancelled)
    compressor = zlib.compressobj()
    digest = hashlib.sha256()
    compressed: list[bytes] = []
    raw_size = packed_size = 0

    def retain(chunk: bytes) -> None:
        nonlocal packed_size
        packed_size += len(chunk)
        if 4 * ((packed_size + 2) // 3) > MAX_VOLUME_ENCODED_BYTES:
            raise ValueError("volume archive exceeds the encoded geometry limit")
        if chunk:
            compressed.append(chunk)

    for chunk in _canonical_chunks(saved):
        check_cancelled(cancelled)
        raw_size += len(chunk)
        if raw_size > MAX_VOLUME_EXPANDED_BYTES:
            raise ValueError("volume archive exceeds the expanded geometry limit")
        digest.update(chunk)
        retain(compressor.compress(chunk))
    check_cancelled(cancelled)
    retain(compressor.flush())
    return VolumeArchive(
        sha256=digest.hexdigest(),
        uncompressed_bytes=raw_size,
        data=base64.b64encode(b"".join(compressed)).decode("ascii"),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("volume archive JSON contains duplicate keys")
        result[name] = value
    return result


def _finite_constant(value: str) -> None:
    raise ValueError(f"volume archive JSON contains nonfinite value {value}")


def decode_selection_volume(
    archive: VolumeArchive | object,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Adopt a verified single compressed stream within exact declared bounds."""
    check_cancelled(cancelled)
    # Revalidate models too: model_copy/model_construct can bypass validators.
    record = VolumeArchive.model_validate(
        archive.model_dump() if isinstance(archive, VolumeArchive) else archive
    )
    check_cancelled(cancelled)
    compressed = base64.b64decode(record.data, validate=True)
    decompressor = zlib.decompressobj()
    expanded: list[bytes] = []
    digest = hashlib.sha256()
    size = 0
    try:
        for start in range(0, len(compressed), _CHUNK_BYTES):
            pending = compressed[start : start + _CHUNK_BYTES]
            while pending:
                check_cancelled(cancelled)
                chunk = decompressor.decompress(
                    pending, min(_CHUNK_BYTES, record.uncompressed_bytes - size + 1)
                )
                size += len(chunk)
                if size > record.uncompressed_bytes:
                    raise ValueError(
                        "volume archive exceeds its declared expanded size"
                    )
                expanded.append(chunk)
                digest.update(chunk)
                if decompressor.unused_data:
                    raise ValueError("volume archive contains trailing compressed data")
                pending = decompressor.unconsumed_tail
    except zlib.error as error:
        raise ValueError("volume archive has an invalid compressed stream") from error
    if not decompressor.eof:
        raise ValueError("volume archive has a truncated compressed stream")
    if size != record.uncompressed_bytes:
        raise ValueError("volume archive expanded length does not match")
    if digest.hexdigest() != record.sha256:
        raise ValueError("volume archive content digest does not match")
    check_cancelled(cancelled)
    raw = b"".join(expanded)
    try:
        value = cast(
            object,
            json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=_finite_constant,
            ),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("volume archive must contain finite UTF-8 JSON") from error
    # Require a canonical representation even when its supplied digest is valid.
    position = 0
    try:
        for chunk in _canonical_chunks(value):
            check_cancelled(cancelled)
            if raw[position : position + len(chunk)] != chunk:
                raise ValueError("volume archive JSON is not canonical")
            position += len(chunk)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("volume archive must contain finite canonical JSON") from error
    if position != len(raw):
        raise ValueError("volume archive JSON is not canonical")
    check_cancelled(cancelled)
    saved = validate_selection_volume(value)
    check_cancelled(cancelled)
    return saved
