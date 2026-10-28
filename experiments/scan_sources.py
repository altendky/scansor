"""Replayable byte authorities, independent of mesh decoding and project state."""

from __future__ import annotations

import hashlib
import io
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, cast, final, override, runtime_checkable


@runtime_checkable
class ReplayableInput(Protocol):
    @property
    def byte_length(self) -> int: ...

    @property
    def filename(self) -> str: ...

    def open(self) -> AbstractContextManager[BinaryIO]: ...


@dataclass(frozen=True)
class BytesInput:
    """Explicit small/legacy in-memory input; streaming adapters use other sources."""

    data: bytes
    filename: str

    @property
    def byte_length(self) -> int:
        return len(self.data)

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with io.BytesIO(self.data) as stream:
            yield stream


@dataclass(frozen=True)
class PathInput:
    """Pin an optional owning lease, not an open file or an uncompressed copy."""

    path: Path
    filename: str = ""
    owner: object | None = field(default=None, repr=False, compare=False)
    byte_length: int = field(init=False)

    def __post_init__(self) -> None:
        path = Path(self.path)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "filename", self.filename or path.name)
        object.__setattr__(self, "byte_length", path.stat().st_size)

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with self.path.open("rb") as stream:
            yield stream


@final
class CheckedReader(io.RawIOBase):
    """Sequential bounded hashing reader; EOF attests exact length and identity.

    Only the initial seek(0) compatibility request is supported. The underlying
    source may be a sequential DEFLATE member; no backwards seek is performed.
    The caller owns and closes the underlying stream through its source context.
    """

    def __init__(
        self,
        stream: BinaryIO,
        byte_length: int,
        *,
        expected_sha256: str | None = None,
        chunk_bytes: int = 65_536,
    ) -> None:
        super().__init__()
        if type(byte_length) is not int or byte_length < 0:
            raise ValueError("source length must be a nonnegative integer")
        if type(chunk_bytes) is not int or chunk_bytes < 1:
            raise ValueError("source chunk size must be a positive integer")
        self._stream: BinaryIO = stream
        self._length: int = byte_length
        self._expected: str | None = expected_sha256
        self._maximum: int = chunk_bytes
        self._position: int = 0
        self._digest = hashlib.sha256()
        self._complete: bool = False

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return False

    @override
    def tell(self) -> int:
        return self._position

    @override
    def seek(self, offset: int, whence: int = 0) -> int:
        if offset == 0 and whence == 0 and self._position == 0:
            return 0
        raise io.UnsupportedOperation("source replay is sequential")

    @override
    def read(self, size: int = -1) -> bytes:
        if self.closed:
            raise ValueError("read of closed source")
        if type(size) is not int or size < 0:
            raise ValueError("source reads require an explicit bounded size")
        if size == 0:
            return b""
        if self._position == self._length:
            if not self._complete:
                if self._stream.read(1):
                    raise OSError("source exceeds its declared byte length")
                if (
                    self._expected is not None
                    and self._digest.hexdigest() != self._expected
                ):
                    raise OSError(
                        "source content hash does not match its verified identity"
                    )
                self._complete = True
            return b""
        requested = min(size, self._maximum, self._length - self._position)
        chunk = self._stream.read(requested)
        if (
            not isinstance(cast(object, chunk), bytes)
            or not 0 < len(chunk) <= requested
        ):
            raise OSError("source is truncated or returned an invalid read")
        self._position += len(chunk)
        self._digest.update(chunk)
        return chunk

    @property
    def sha256(self) -> str:
        if not self._complete:
            raise ValueError("source hash requires complete EOF verification")
        return self._digest.hexdigest()


def as_binary_stream(reader: CheckedReader) -> BinaryIO:
    """RawIOBase provides the bounded read/seek/tell subset used by the decoder."""
    return cast(BinaryIO, cast(object, reader))
