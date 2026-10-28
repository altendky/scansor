"""Bounded synchronous Blob reads for the disposable browser worker.

The JavaScript owner supplies a FileReaderSync range callback. Only requested
compressed/raw ranges cross into Python; archive members are decoded separately.
No browser imports are needed here, so the stream contract is testable natively.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, cast, override


class BlobStream(io.RawIOBase):
    def __init__(
        self,
        byte_length: int,
        read_range: Callable[[int, int], object],
        chunk_bytes: int,
    ) -> None:
        super().__init__()
        self._length: int = byte_length
        self._read_range: Callable[[int, int], object] = read_range
        self._chunk_bytes: int = chunk_bytes
        self._position: int = 0

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        self._checkClosed()
        return self._position

    @override
    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self._checkClosed()
        if type(offset) is not int or whence not in (
            io.SEEK_SET,
            io.SEEK_CUR,
            io.SEEK_END,
        ):
            raise ValueError("Expected an integer offset and valid seek origin")
        position = offset + (
            0
            if whence == io.SEEK_SET
            else self._position
            if whence == io.SEEK_CUR
            else self._length
        )
        if position < 0:
            raise ValueError("Cannot seek before the source")
        self._position = position
        return position

    @override
    def readinto(self, target: object) -> int:
        self._checkClosed()
        view = memoryview(cast(bytes, target)).cast("B")
        count = min(len(view), self._chunk_bytes, max(0, self._length - self._position))
        if count == 0:
            return 0
        chunk = self._read_range(self._position, count)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            convert = getattr(chunk, "to_py", None)
            if convert is None:
                raise OSError("Browser source range is not byte data")
            chunk = convert()
        if not isinstance(chunk, (bytes, bytearray, memoryview)) or len(chunk) != count:
            raise OSError("Browser source changed or returned an incomplete range")
        view[:count] = chunk
        self._position += count
        return count


@dataclass(frozen=True)
class BlobInput:
    """Replayable selected Blob/File without a Python-owned full-file copy."""

    byte_length: int
    filename: str
    read_range: Callable[[int, int], object]
    chunk_bytes: int = 65_536

    def __post_init__(self) -> None:
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("Expected a nonnegative exact Blob length")
        if type(self.chunk_bytes) is not int or self.chunk_bytes < 1:
            raise ValueError("Expected a positive Blob chunk bound")
        if not self.filename or len(self.filename) > 512:
            raise ValueError("Expected a bounded filename hint")

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with io.BufferedReader(
            BlobStream(self.byte_length, self.read_range, self.chunk_bytes),
            buffer_size=self.chunk_bytes,
        ) as stream:
            yield cast(BinaryIO, stream)
