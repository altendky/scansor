"""Independent ASCII/binary STL records and guarded non-seeking source streams."""

from __future__ import annotations

import hashlib
import io
import struct
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from itertools import pairwise
from typing import BinaryIO, cast, override

import numpy as np
import pytest

from scansor.mesh_stl import (
    StlCancelled,
    StlError,
    StlLimits,
    StlProgress,
    inspect_stl,
    iter_stl_facets,
)

NORMAL = (0.0, 0.0, 0.0)
TRIANGLE = ((-0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def binary(
    *, facets: int = 3, header: bytes = b"generated STL", attribute: int = 0
) -> bytes:
    record = struct.pack(
        "<12fH", *NORMAL, *(value for vertex in TRIANGLE for value in vertex), attribute
    )
    return header.ljust(80, b"\0") + struct.pack("<I", facets) + record * facets


def ascii_stl(
    *, facets: int = 3, normal: str = "0 0 0", newline: bytes = b"\n"
) -> bytes:
    record = "\n".join(
        (
            f"  facet normal {normal}",
            "    outer loop",
            "      vertex -0 0 0",
            "      vertex 1 0 0",
            "      vertex 0 1 0",
            "    endloop",
            "  endfacet",
            "",
        )
    ).encode("ascii")
    return (b"solid fixture\n" + record * facets + b"endsolid fixture\n").replace(
        b"\n", newline
    )


class Source:
    def __init__(self, raw: bytes, *, boundary: int = 7, maximum: int = 65_536) -> None:
        self.raw: bytes = raw
        self.boundary: int = boundary
        self.maximum: int = maximum
        self.opens: int = 0
        self.closes: int = 0
        self.requests: list[int] = []
        self.read_bytes: int = 0

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        source = self

        class Stream(io.RawIOBase):
            position: int = 0

            @override
            def read(self, size: int = -1) -> bytes:
                assert 0 < size <= source.maximum
                source.requests.append(size)
                data = source.raw[
                    self.position : self.position + min(size, source.boundary)
                ]
                self.position += len(data)
                source.read_bytes += len(data)
                return data

            @override
            def seek(self, offset: int, whence: int = 0) -> int:
                raise AssertionError("STL source must not seek")

        self.opens += 1
        try:
            with Stream() as stream:
                yield cast(BinaryIO, cast(object, stream))
        finally:
            self.closes += 1


LIMITS = StlLimits(2_000_000, 10_000, chunk_bytes=128)


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
@pytest.mark.parametrize("chunk_bytes", [1, 7, 50, 128])
def test_guarded_streams_preserve_facets_normals_signed_zero_and_exact_identity(
    encoding: str, chunk_bytes: int
) -> None:
    raw = ascii_stl() if encoding == "ascii" else binary()
    limits = replace(LIMITS, chunk_bytes=chunk_bytes)
    source = Source(raw, boundary=3, maximum=chunk_bytes)
    metadata = inspect_stl(source.open, len(raw), limits=limits)
    assert metadata.encoding == encoding and metadata.facets == 3
    assert metadata.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert source.opens == source.closes == 1
    chunks = list(iter_stl_facets(source.open, metadata, limits=limits))
    assert source.opens == source.closes == 2
    assert source.read_bytes == len(raw) * 2
    assert max(source.requests) <= chunk_bytes
    assert [chunk.start for chunk in chunks] == list(
        range(0, 3, max(1, chunk_bytes // 50))
    )
    vertices = np.concatenate([chunk.vertices for chunk in chunks])
    normals = np.concatenate([chunk.normals for chunk in chunks])
    assert vertices.dtype == normals.dtype == np.dtype("<f4")
    np.testing.assert_array_equal(vertices, [TRIANGLE] * 3)
    np.testing.assert_array_equal(normals, [NORMAL] * 3)
    assert np.signbit(vertices[:, 0, 0]).all()
    assert all(
        not chunk.vertices.flags.writeable and not chunk.normals.flags.writeable
        for chunk in chunks
    )


@pytest.mark.parametrize(
    "header", [b"solid mesh", b"solid\n", b"solid", b"\0binary\xff"]
)
def test_exact_binary_layout_takes_precedence_over_solid_header(header: bytes) -> None:
    raw = binary(header=header)
    metadata = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)
    assert metadata.encoding == "binary"


def test_ascii_indentation_and_blank_lines_can_extend_beyond_detection_probe() -> None:
    raw = b" " * 100 + b"\n\n" + ascii_stl(facets=1)
    source = Source(raw, boundary=1)
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    assert metadata.encoding == "ascii" and metadata.facets == 1


@pytest.mark.parametrize("encoding", ["ascii", "binary"])
def test_zero_facets_are_format_records_not_a_geometry_admission_claim(
    encoding: str,
) -> None:
    raw = ascii_stl(facets=0) if encoding == "ascii" else binary(facets=0)
    source = Source(raw)
    metadata = inspect_stl(source.open, len(raw), limits=replace(LIMITS, max_facets=0))
    assert metadata.facets == 0
    assert (
        list(
            iter_stl_facets(source.open, metadata, limits=replace(LIMITS, max_facets=0))
        )
        == []
    )


@pytest.mark.parametrize("mutate", ["truncate", "trailing", "count"])
def test_binary_count_and_exact_length_are_attested(mutate: str) -> None:
    raw = binary()
    if mutate == "truncate":
        raw = raw[:-1]
    if mutate == "trailing":
        raw += b"trailing"
    if mutate == "count":
        raw = raw[:80] + struct.pack("<I", 100) + raw[84:]
    with pytest.raises(StlError):
        _ = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)


@pytest.mark.parametrize("change", ["truncated_actual", "long_actual"])
def test_declared_byte_count_cannot_hide_truncation_or_growth(change: str) -> None:
    raw = ascii_stl()
    actual = raw[:-1] if change == "truncated_actual" else raw + b"x"
    with pytest.raises(StlError, match=r"truncated|declared"):
        _ = inspect_stl(Source(actual).open, len(raw), limits=LIMITS)


@pytest.mark.parametrize("attribute", [1, 0x8000, 0xFFFF])
def test_binary_attributes_and_known_header_color_extensions_are_rejected(
    attribute: int,
) -> None:
    raw = binary(attribute=attribute)
    with pytest.raises(StlError) as error:
        _ = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)
    assert error.value.category == "unsupported" and error.value.facet == 0
    for header in (b"COLOR=\xff\0\0\xff", b"MATERIAL=example"):
        raw = binary(header=header)
        with pytest.raises(StlError) as colored:
            _ = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)
        assert colored.value.category == "unsupported"


@pytest.mark.parametrize("component", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("normal", [False, True])
def test_binary_requires_finite_coordinate_and_raw_normal_components(
    component: float, normal: bool
) -> None:
    raw = bytearray(binary())
    struct.pack_into("<f", raw, 84 + (0 if normal else 12), component)
    with pytest.raises(StlError, match="finite"):
        _ = inspect_stl(Source(bytes(raw)).open, len(raw), limits=LIMITS)


def test_ascii_rounding_underflow_whitespace_and_inconsistent_normals_are_explicit() -> (
    None
):
    raw = ascii_stl(facets=1, normal="0 0 -7", newline=b"\r\n")
    raw = raw.replace(b"vertex -0 0 0", b"vertex -1e-100 1.000000059604644775390625 0")
    raw = b"\r\n\t" + raw.rstrip()
    source = Source(raw)
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    chunks = list(iter_stl_facets(source.open, metadata, limits=LIMITS))
    assert chunks[0].vertices[0, 0, 0] == 0 and np.signbit(chunks[0].vertices[0, 0, 0])
    # Exact halfway binary32 decimal rounds to the even value 1.0.
    assert chunks[0].vertices[0, 0, 1] == 1.0
    np.testing.assert_array_equal(chunks[0].normals[0], [0, 0, -7])


@pytest.mark.parametrize(
    "number",
    [b"NaN", b"inf", b"-Infinity", b"1e1000", b"3.5e38", b"0x1p0", b"1,2", b"1D2"],
)
def test_ascii_invalid_or_overflowing_numeric_tokens_are_rejected(
    number: bytes,
) -> None:
    raw = ascii_stl(facets=1).replace(b"vertex -0", b"vertex " + number)
    with pytest.raises(StlError):
        _ = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)


@pytest.mark.parametrize(
    "mutate",
    [
        "missing_solid",
        "missing_end",
        "four_vertices",
        "extra_normal",
        "extra_terminator",
        "multiple_solids",
        "unicode",
        "unknown_keyword",
    ],
)
def test_ascii_strict_grammar_has_no_silent_extensions(mutate: str) -> None:
    raw = ascii_stl(facets=1)
    edits: dict[str, Callable[[bytes], bytes]] = {
        "missing_solid": lambda data: data.replace(b"solid fixture\n", b"", 1),
        "missing_end": lambda data: data.replace(b"endsolid fixture\n", b""),
        "four_vertices": lambda data: data.replace(
            b"    endloop", b"vertex 0 0 0\n    endloop"
        ),
        "extra_normal": lambda data: data.replace(
            b"facet normal 0 0 0", b"facet normal 0 0 0 1"
        ),
        "extra_terminator": lambda data: data.replace(b"endloop", b"endloop extra"),
        "multiple_solids": lambda data: data + data,
        "unicode": lambda data: data.replace(b"fixture", b"f\xc3\xadxture"),
        "unknown_keyword": lambda data: data.replace(b"outer loop", b"outer patch"),
    }
    raw = edits[mutate](raw)
    with pytest.raises(StlError):
        _ = inspect_stl(Source(raw).open, len(raw), limits=LIMITS)


def test_source_facet_line_and_token_limits_are_independent() -> None:
    raw = ascii_stl(facets=3)
    source = Source(raw)
    with pytest.raises(StlError) as size:
        _ = inspect_stl(
            source.open, len(raw), limits=replace(LIMITS, max_source_bytes=len(raw) - 1)
        )
    assert size.value.category == "resource" and source.opens == 0
    for data, limits in (
        (raw, replace(LIMITS, max_facets=2)),
        (binary(facets=3), replace(LIMITS, max_facets=2)),
        (
            raw.replace(b"solid fixture", b"solid " + b"x" * 200),
            replace(LIMITS, max_line_bytes=128),
        ),
        (raw.replace(b"vertex -0", b"vertex " + b"0" * 129), LIMITS),
    ):
        with pytest.raises(StlError) as error:
            _ = inspect_stl(Source(data).open, len(data), limits=limits)
        assert error.value.category == "resource"


def test_cancellation_closes_owned_stream_and_stops_before_full_source() -> None:
    raw = ascii_stl(facets=20)
    source = Source(raw)
    cancelled = False

    def progress(update: StlProgress) -> None:
        nonlocal cancelled
        cancelled = update.completed > 100

    with pytest.raises(StlCancelled):
        _ = inspect_stl(
            source.open,
            len(raw),
            limits=LIMITS,
            progress=progress,
            cancelled=lambda: cancelled,
        )
    assert source.opens == source.closes == 1 and source.read_bytes < len(raw)
    untouched = Source(raw)
    with pytest.raises(StlCancelled):
        _ = inspect_stl(untouched.open, len(raw), limits=LIMITS, cancelled=lambda: True)
    assert untouched.opens == 0


def test_second_pass_rejects_same_size_changed_source_and_forged_metadata() -> None:
    raw = ascii_stl()
    source = Source(raw)
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    source.raw = raw.replace(b"vertex 1", b"vertex 2", 1)
    with pytest.raises(StlError, match="hash changed"):
        _ = list(iter_stl_facets(source.open, metadata, limits=LIMITS))
    source.raw = raw
    with pytest.raises(StlError, match=r"count changed|count or source hash"):
        _ = list(
            iter_stl_facets(
                source.open,
                replace(metadata, facets=metadata.facets - 1),
                limits=LIMITS,
            )
        )
    with pytest.raises(StlError, match="hash"):
        _ = list(
            iter_stl_facets(
                source.open, replace(metadata, source_sha256="wrong"), limits=LIMITS
            )
        )


def test_degenerate_and_duplicate_facets_are_retained_without_topology_repair() -> None:
    raw = ascii_stl(facets=2).replace(b"vertex 1 0 0", b"vertex 0 0 0")
    source = Source(raw)
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    chunks = list(iter_stl_facets(source.open, metadata, limits=LIMITS))
    vertices = np.concatenate([chunk.vertices for chunk in chunks])
    assert metadata.facets == 2
    np.testing.assert_array_equal(vertices[0], vertices[1])
    np.testing.assert_array_equal(vertices[:, 0], vertices[:, 1])


def test_chunk_order_and_ownership_survive_later_reads() -> None:
    raw = bytearray(binary(facets=7))
    for index in range(7):
        struct.pack_into("<f", raw, 84 + 50 * index + 12, float(index))
        struct.pack_into("<f", raw, 84 + 50 * index + 8, -float(index))
    source = Source(bytes(raw))
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    chunks = list(iter_stl_facets(source.open, metadata, limits=LIMITS))
    assert [chunk.start for chunk in chunks] == [0, 2, 4, 6]
    for left, right in pairwise(chunks):
        assert not np.shares_memory(left.vertices, right.vertices)
    vertices = np.concatenate([chunk.vertices for chunk in chunks])
    normals = np.concatenate([chunk.normals for chunk in chunks])
    np.testing.assert_array_equal(vertices[:, 0, 0], list(range(7)))
    np.testing.assert_array_equal(normals[:, 2], [-index for index in range(7)])


def test_second_pass_cancellation_releases_source_without_certifying_completion() -> (
    None
):
    raw = ascii_stl(facets=20)
    source = Source(raw)
    metadata = inspect_stl(source.open, len(raw), limits=LIMITS)
    cancelled = False

    def progress(update: StlProgress) -> None:
        nonlocal cancelled
        assert update.phase == "facets"
        cancelled = update.completed > 100

    with pytest.raises(StlCancelled):
        _ = list(
            iter_stl_facets(
                source.open,
                metadata,
                limits=LIMITS,
                progress=progress,
                cancelled=lambda: cancelled,
            )
        )
    assert source.opens == source.closes == 2
    assert source.read_bytes < 2 * len(raw)


def test_stream_failure_has_stable_io_category_and_does_not_own_caller_stream() -> None:
    raw = binary()
    streams: list[io.BytesIO] = []

    @contextmanager
    def factory() -> Generator[BinaryIO]:
        class Broken(io.BytesIO):
            @override
            def read(self, size: int | None = -1) -> bytes:
                raise OSError("independent stream failure")

        with Broken(raw) as stream:
            streams.append(stream)
            yield stream

    with pytest.raises(StlError) as error:
        _ = inspect_stl(factory, len(raw), limits=LIMITS)
    assert error.value.category == "io"
    assert streams[0].closed


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_source_bytes", 0),
        ("max_facets", -1),
        ("max_facets", True),
        ("chunk_bytes", 0),
        ("max_line_bytes", 0),
        ("max_token_bytes", 0),
    ],
)
def test_limit_fields_are_strict_and_positive_except_empty_facet_admission(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        _ = replace(LIMITS, **{field: value})
