"""Independent fixed-record PLY decoding never seeks within sequential payloads."""

import io
from typing import cast, override

import numpy as np
import pytest

from scansor._plyio import PlyError, SequentialReader, build_layout, read_header
from scansor._plyio.format import Source
from tests.native_scan_fixture import native_grid_fixture


class Payload(io.BytesIO):
    @override
    def seek(self, offset: int, whence: int = 0) -> int:
        raise AssertionError("sequential payload must not seek")


def fixture(raw: bytes | None = None) -> tuple[SequentialReader, Payload]:
    data = native_grid_fixture(3, 3).raw if raw is None else raw
    header_stream = io.BytesIO(data)
    header = read_header(header_stream)
    layout = build_layout(header, fixed_lists={("face", "vertex_indices"): 3})
    payload = Payload(data[len(header.raw) :])
    return SequentialReader(
        cast(Source, payload),
        layout,
        byte_length=layout.byte_count,
        max_range_bytes=1024,
        io_block_bytes=7,
    ), payload


def test_sequential_exact_order_ranges_are_owned_readonly_and_eof_attested() -> None:
    reader, stream = fixture()
    first = reader.read_range("vertex", 0, 4)
    _ = reader.read_range("vertex", 4, 9)
    faces = reader.read_range("face", 0, 8)
    assert first.shape == (4,) and faces.shape == (8,)
    assert not first.flags.writeable and not faces.flags.writeable
    np.testing.assert_array_equal(first["x"], [-256, 0, 256, -256])
    reader.finish()
    assert not stream.closed


def test_sequential_rejects_out_of_order_incomplete_and_oversized_reads() -> None:
    reader, _ = fixture()
    with pytest.raises(PlyError, match="order"):
        _ = reader.read_range("face", 0, 1)
    with pytest.raises(PlyError, match="incomplete"):
        reader.finish()
    with pytest.raises(PlyError):
        _ = reader.read_range("vertex", 0, 10)


@pytest.mark.parametrize("suffix", [b"trailing", b""])
def test_sequential_rejects_trailing_or_truncated_payload(suffix: bytes) -> None:
    original = native_grid_fixture(3, 3).raw
    raw = original + suffix if suffix else original[:-1]
    reader, _ = fixture(raw)
    _ = reader.read_range("vertex", 0, 9)
    if suffix:
        _ = reader.read_range("face", 0, 8)
        with pytest.raises(PlyError, match="trailing"):
            reader.finish()
    else:
        with pytest.raises(PlyError, match="truncated"):
            _ = reader.read_range("face", 0, 8)


def test_sequential_attests_fixed_list_counts() -> None:
    raw = bytearray(native_grid_fixture(3, 3).raw)
    header = read_header(io.BytesIO(raw))
    raw[len(header.raw) + 9 * 24] = 4
    reader, _ = fixture(bytes(raw))
    _ = reader.read_range("vertex", 0, 9)
    with pytest.raises(PlyError, match="list count"):
        _ = reader.read_range("face", 0, 8)
