"""Portable accepted geometry, hostile inputs and bounded archive adoption."""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from typing import Any, Literal

import pytest
from pydantic import ValidationError

from experiments import volume_archive as codec
from experiments.scan_workspace import ScanCancelled
from experiments.selection_volume import (
    build_selection_volume,
    update_selection_volume_options,
)
from experiments.volume_archive import (
    VolumeArchive,
    decode_selection_volume,
    encode_selection_volume,
)
from tests.test_selection_volume import curved, grid, volume


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def packed(raw: bytes, *, level: int = 6, extra: bytes = b"") -> VolumeArchive:
    return VolumeArchive(
        sha256=hashlib.sha256(raw).hexdigest(),
        uncompressed_bytes=len(raw),
        data=base64.b64encode(zlib.compress(raw, level) + extra).decode("ascii"),
    )


@pytest.mark.parametrize("kind", ["mesh", "plane", "cylinder", "cone"])
def test_roundtrip_kinds_preserves_source_defaults_and_unloaded_edits(
    kind: Literal["mesh", "plane", "cylinder", "cone"],
) -> None:
    if kind in ("mesh", "plane"):
        _, _, _, _, saved = volume(kind)
    else:
        points, normals, faces, fit = curved(
            kind, slope=0 if kind == "cylinder" else 0.3
        )
        saved = build_selection_volume(
            points, normals, faces, list(range(len(points))), kind=kind, fit=fit
        )
    archive = encode_selection_volume(saved)
    restored = decode_selection_volume(
        VolumeArchive.model_validate_json(archive.model_dump_json())
    )
    assert restored == saved
    assert archive.sha256 == hashlib.sha256(canonical(saved)).hexdigest()
    override = update_selection_volume_options(restored, {"inward_thickness": 9.0})
    restored_override = decode_selection_volume(encode_selection_volume(override))
    reset = update_selection_volume_options(
        restored_override, {"inward_thickness": None}
    )
    assert reset == saved


def test_compressor_settings_do_not_define_geometry_identity() -> None:
    raw = canonical(volume()[4])
    fast, small = packed(raw, level=0), packed(raw, level=9)
    assert fast.data != small.data
    assert fast.sha256 == small.sha256
    assert decode_selection_volume(fast) == decode_selection_volume(small)


def test_record_validation_never_expands_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = encode_selection_volume(volume()[4])

    def forbidden() -> None:
        pytest.fail("ordinary archive model validation expanded geometry")

    monkeypatch.setattr(zlib, "decompressobj", forbidden)
    monkeypatch.setattr(base64, "b64decode", forbidden)
    assert VolumeArchive.model_validate(record.model_dump()) == record
    with pytest.raises(ValidationError, match="frozen"):
        record.data = "AAAA"


@pytest.mark.parametrize(
    "change",
    [
        {"format": "future"},
        {"encoding": "gzip"},
        {"sha256": "A" * 64},
        {"sha256": "a" * 63},
        {"uncompressed_bytes": True},
        {"uncompressed_bytes": "123"},
        {"uncompressed_bytes": 1.0},
        {"uncompressed_bytes": 0},
        {"data": b"AAAA"},
        {"unexpected": True},
    ],
)
def test_record_is_strict(change: dict[str, Any]) -> None:
    record = encode_selection_volume(volume()[4]).model_dump()
    record.update(change)
    with pytest.raises(ValidationError):
        _ = VolumeArchive.model_validate(record)


@pytest.mark.parametrize(
    "data", ["", "A", "A===", "====", "AA A", "éAAA", "AA==\n", "AB==", "AAB="]
)
def test_noncanonical_base64_rejected_without_decompression(data: str) -> None:
    with pytest.raises(ValidationError, match=r"base64|encoded"):
        _ = VolumeArchive(sha256="a" * 64, uncompressed_bytes=1, data=data)


def test_exact_encoded_and_expanded_admission_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = volume()[4]
    archive = encode_selection_volume(saved)
    monkeypatch.setattr(codec, "MAX_VOLUME_EXPANDED_BYTES", archive.uncompressed_bytes)
    monkeypatch.setattr(codec, "MAX_VOLUME_ENCODED_BYTES", len(archive.data))
    assert encode_selection_volume(saved) == archive
    assert decode_selection_volume(archive) == saved
    monkeypatch.setattr(
        codec, "MAX_VOLUME_EXPANDED_BYTES", archive.uncompressed_bytes - 1
    )
    with pytest.raises(ValueError, match="expanded"):
        _ = encode_selection_volume(saved)
    with pytest.raises(ValueError, match="expanded"):
        _ = decode_selection_volume(archive)
    monkeypatch.setattr(codec, "MAX_VOLUME_EXPANDED_BYTES", archive.uncompressed_bytes)
    monkeypatch.setattr(codec, "MAX_VOLUME_ENCODED_BYTES", len(archive.data) - 1)
    with pytest.raises(ValueError, match="encoded"):
        _ = encode_selection_volume(saved)
    with pytest.raises(ValueError, match="encoded"):
        _ = decode_selection_volume(archive)


@pytest.mark.parametrize("extra", [b"trailing", zlib.compress(b"second stream")])
def test_trailing_and_concatenated_streams_rejected(extra: bytes) -> None:
    archive = packed(canonical(volume()[4]), extra=extra)
    with pytest.raises(ValueError, match="trailing"):
        _ = decode_selection_volume(archive)


@pytest.mark.parametrize("change", ["truncated", "corrupt", "digest", "short", "long"])
def test_stream_integrity_and_length(change: str) -> None:
    archive = encode_selection_volume(volume()[4])
    fields = archive.model_dump()
    compressed = base64.b64decode(archive.data)
    if change == "truncated":
        fields["data"] = base64.b64encode(compressed[:-1]).decode("ascii")
    elif change == "corrupt":
        fields["data"] = base64.b64encode(b"invalid stream").decode("ascii")
    elif change == "digest":
        fields["sha256"] = "0" * 64
    else:
        fields["uncompressed_bytes"] += -1 if change == "short" else 1
    with pytest.raises(ValueError, match=r"compressed|digest|length|expanded"):
        _ = decode_selection_volume(fields)


def test_declared_size_stops_compression_bomb(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(codec, "MAX_VOLUME_EXPANDED_BYTES", 64)
    record = VolumeArchive(
        sha256="0" * 64,
        uncompressed_bytes=64,
        data=base64.b64encode(zlib.compress(b"x" * 100_000)).decode("ascii"),
    )
    with pytest.raises(ValueError, match="declared expanded"):
        _ = decode_selection_volume(record)


@pytest.mark.parametrize(
    "raw,match",
    [
        (b"\xff", "UTF-8"),
        (b'{"a":1,"a":2}', "duplicate"),
        (b'{"a":NaN}', "nonfinite"),
        (b'{"a":Infinity}', "nonfinite"),
        (b'{"a":-Infinity}', "nonfinite"),
        (b'{"a":1e999}', "compliant"),
        (b"{} trailing", "UTF-8"),
        (b'{ "a":1}', "canonical"),
        (b'{"z":1,"a":2}', "canonical"),
        (b'{"a":1.00}', "canonical"),
        (b"{}", "selection volume"),
    ],
)
def test_invalid_json_and_geometry_are_rejected(raw: bytes, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _ = decode_selection_volume(packed(raw))


def test_valid_noncanonical_geometry_rejected() -> None:
    raw = json.dumps(volume()[4], indent=2).encode()
    with pytest.raises(ValueError, match="canonical"):
        _ = decode_selection_volume(packed(raw))


@pytest.mark.parametrize("operation", ["encode", "decode"])
@pytest.mark.parametrize("after", [1, 4])
def test_cancellation_before_and_during_archive_work(
    operation: str, after: int
) -> None:
    saved = volume()[4]
    saved["provenance"]["note"] = "large bounded diagnostic" * 20_000
    archive = encode_selection_volume(saved)
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= after

    with pytest.raises(ScanCancelled):
        if operation == "encode":
            _ = encode_selection_volume(saved, cancelled=cancelled)
        else:
            _ = decode_selection_volume(archive, cancelled=cancelled)
    assert calls == after


def test_large_definition_is_compact_without_embedding_scan() -> None:
    points, normals, faces = grid(20)
    saved = build_selection_volume(points, normals, faces, list(range(len(points))))
    archive = encode_selection_volume(saved)
    assert len(archive.data) < archive.uncompressed_bytes // 4
    assert decode_selection_volume(archive) == saved
    assert set(archive.model_dump()) == {
        "format",
        "encoding",
        "sha256",
        "uncompressed_bytes",
        "data",
    }


def test_bypassed_record_validation_is_checked_at_adoption() -> None:
    archive = encode_selection_volume(volume()[4])
    invalid = archive.model_copy(update={"uncompressed_bytes": True})
    with pytest.raises(ValidationError):
        _ = decode_selection_volume(invalid)


def test_exact_decompression_chunk_boundaries_and_unicode_provenance() -> None:
    saved = volume()[4]
    saved["provenance"]["unicode"] = "\u03b1 \U0001f9ed"
    saved["provenance"]["padding"] = ""
    length = len(canonical(saved))
    saved["provenance"]["padding"] = "x" * (2 * 64 * 1024 - length)
    assert len(canonical(saved)) == 2 * 64 * 1024
    assert decode_selection_volume(encode_selection_volume(saved)) == saved
