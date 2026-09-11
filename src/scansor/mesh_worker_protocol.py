"""Bounded canonical JSON frames; the worker never transfers bulk arrays by IPC."""

from __future__ import annotations

import struct
import threading
from typing import BinaryIO

from scansor.mesh_controls import (
    MAX_CONTROL_BYTES,
    Control,
    decode_control,
    encode_control,
)
from scansor.mesh_errors import MeshImportError


class FrameWriter:
    def __init__(self, stream: BinaryIO) -> None:
        self.stream: BinaryIO = stream
        self.lock: threading.Lock = threading.Lock()

    def send(self, record: dict[str, Control]) -> None:
        raw = encode_control(record)
        with self.lock:
            for piece in (struct.pack(">I", len(raw)), raw):
                view, done = memoryview(piece), 0
                while done < len(view):
                    count = self.stream.write(view[done:])
                    if type(count) is not int or not 0 < count <= len(view) - done:
                        raise MeshImportError(
                            "execution", "worker-protocol", "IPC write made no progress"
                        )
                    done += count
            self.stream.flush()


class FrameReader:
    """Feed bounded pipe reads; no more than one frame plus a read is retained."""

    def __init__(self) -> None:
        self.buffer: bytearray = bytearray()
        self.length: int | None = None

    def feed(self, data: bytes) -> list[dict[str, Control]]:
        if len(data) > 65_536:
            raise MeshImportError(
                "structure", "worker-protocol", "pipe read exceeds the transport bound"
            )
        self.buffer.extend(data)
        result: list[dict[str, Control]] = []
        while True:
            length = self.length
            if length is None:
                if len(self.buffer) < 4:
                    break
                length = int.from_bytes(self.buffer[:4], byteorder="big")
                self.length = length
                del self.buffer[:4]
                if not 1 <= length <= MAX_CONTROL_BYTES:
                    raise MeshImportError(
                        "structure", "worker-protocol", "IPC frame exceeds 8 MiB"
                    )
            if len(self.buffer) < length:
                break
            raw = bytes(self.buffer[:length])
            del self.buffer[:length]
            self.length = None
            record = decode_control(raw)
            if not isinstance(record, dict):
                raise MeshImportError(
                    "structure", "worker-protocol", "IPC frame must be an object"
                )
            result.append(record)
        return result

    def finish(self) -> None:
        if self.length is not None or self.buffer:
            raise MeshImportError(
                "integrity", "worker-protocol", "worker left an incomplete IPC frame"
            )
