"""Bounded, transport-independent scan sources for the viewer experiment.

The PLY layer owns format I/O. This module owns interactive admission, original
row identities, incident-area weights and separate display derivatives.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, ClassVar, Literal, Protocol

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from experiments.scan_sources import (
    BytesInput,
    CheckedReader,
    ReplayableInput,
    as_binary_stream,
)
from scansor.mesh_ply import (
    PROFILE,
    MeshPlyError,
    MeshPlyReader,
    SequentialMeshPlyReader,
)

Array = NDArray[np.float64]


class ScanImportError(ValueError):
    def __init__(self, message: str, code: str = "invalid_source") -> None:
        self.code: str = code
        super().__init__(message)


class ScanCancelled(ScanImportError):
    def __init__(self) -> None:
        super().__init__(
            "Scan import cancelled; accepted work is unchanged.", "cancelled"
        )


@dataclass(frozen=True)
class ScanLimits:
    """Caller-owned caps, not a measured total-process memory guarantee."""

    max_source_bytes: int
    max_vertices: int
    max_faces: int
    max_estimated_bytes: int
    chunk_bytes: int = 65_536

    def __post_init__(self) -> None:
        for name in (
            "max_source_bytes",
            "max_vertices",
            "max_faces",
            "max_estimated_bytes",
            "chunk_bytes",
        ):
            value = getattr(self, name)
            minimum = 0 if name == "max_faces" else 24 if name == "chunk_bytes" else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be an integer at least {minimum}")


@dataclass(frozen=True)
class ScanProgress:
    phase: str
    completed: int
    total: int


def check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise ScanCancelled()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


_INTERPRETATION = {
    "version": "interactive-triangle-scan-v1",
    "profile": PROFILE,
    "coordinates": "original-row-order-float64",
    "normals": "required-finite-nonzero-original-plus-unit-derivative",
    "weights": "source-face-order-incident-area-thirds-v1",
    "units": "unknown",
}
INTERPRETATION_SHA256 = hashlib.sha256(_canonical(_INTERPRETATION)).hexdigest()


class SourceDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    version: Literal["interactive-triangle-scan-v1"] = "interactive-triangle-scan-v1"
    profile: Literal["binary-triangle-ply-v1"] = "binary-triangle-ply-v1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bytes: int = Field(gt=0, strict=True)
    interpretation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    vertices: int = Field(gt=0, strict=True)
    triangles: int = Field(ge=0, strict=True)
    filename: str = Field(min_length=1, max_length=512)
    units: Literal["unknown"] = "unknown"

    def semantic_record(self) -> dict[str, object]:
        return self.model_dump(exclude={"filename"})

    @property
    def binding_sha256(self) -> str:
        return hashlib.sha256(_canonical(self.semantic_record())).hexdigest()


@dataclass(frozen=True)
class VerifiedSource:
    """Replay the exact accepted source with bounded reads and EOF verification."""

    input: ReplayableInput
    descriptor: SourceDescriptor

    @property
    def byte_length(self) -> int:
        return self.descriptor.source_bytes

    @property
    def filename(self) -> str:
        return self.descriptor.filename

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        if self.input.byte_length != self.byte_length:
            raise ScanImportError(
                "The source length changed after acceptance.", "source_mismatch"
            )
        try:
            with (
                self.input.open() as stream,
                CheckedReader(
                    stream,
                    self.byte_length,
                    expected_sha256=self.descriptor.source_sha256,
                ) as checked,
            ):
                yield as_binary_stream(checked)
        except OSError as error:
            raise ScanImportError(str(error), "source_mismatch") from error

    def verify(self, *, cancelled: Callable[[], bool] | None = None) -> None:
        with self.open() as stream:
            while True:
                check_cancelled(cancelled)
                if not stream.read(65_536):
                    break

    def read_all(self, max_bytes: int) -> bytes:
        """Explicit small-fixture convenience; production consumers stream open()."""
        if type(max_bytes) is not int or not 0 <= self.byte_length <= max_bytes:
            raise ScanImportError(
                "Explicit source materialization exceeds its byte cap.", "source_limit"
            )
        chunks: list[bytes] = []
        with self.open() as stream:
            while chunk := stream.read(65_536):
                chunks.append(chunk)
        return b"".join(chunks)


class Geometry(Protocol):
    @property
    def xyz(self) -> Array: ...
    @property
    def normals(self) -> Array: ...
    @property
    def triangles(self) -> NDArray[np.int32]: ...
    @property
    def weights(self) -> Array: ...


class Workspace(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def data(self) -> Geometry: ...
    @property
    def local(self) -> Array: ...
    @property
    def origin(self) -> Array: ...
    @property
    def frame(self) -> Array: ...
    @property
    def source_sha256(self) -> str | None: ...
    @property
    def reference_sha256(self) -> str: ...
    @property
    def source_descriptor(self) -> SourceDescriptor | None: ...


@dataclass(frozen=True)
class ScanGeometry:
    xyz: Array
    normals: Array
    original_normals: NDArray[np.float32]
    triangles: NDArray[np.int32]
    weights: Array
    face_status: NDArray[np.uint8]


@dataclass(frozen=True)
class ScanDiagnostics:
    repeated_index_faces: int
    zero_area_faces: int
    isolated_vertices: int
    no_usable_area_vertices: int
    positive_area_vertices: int
    estimated_bytes: int


def _freeze[T: np.generic](array: NDArray[T]) -> NDArray[T]:
    array.flags.writeable = False
    return array


@dataclass(frozen=True)
class ScanWorkspace:
    name: str
    descriptor: SourceDescriptor | None
    data: ScanGeometry
    source: VerifiedSource | None
    diagnostics: ScanDiagnostics
    origin: Array
    frame: Array

    @classmethod
    def empty(cls) -> ScanWorkspace:
        return cls(
            "Empty project",
            None,
            ScanGeometry(
                _freeze(np.empty((0, 3))),
                _freeze(np.empty((0, 3))),
                _freeze(np.empty((0, 3), dtype="<f4")),
                _freeze(np.empty((0, 3), dtype="<i4")),
                _freeze(np.empty(0)),
                _freeze(np.empty(0, dtype="u1")),
            ),
            None,
            ScanDiagnostics(0, 0, 0, 0, 0, 0),
            _freeze(np.zeros(3)),
            _freeze(np.eye(3)),
        )

    @property
    def local(self) -> Array:
        return self.data.xyz

    @property
    def source_sha256(self) -> str | None:
        return self.descriptor.source_sha256 if self.descriptor else None

    @property
    def source_descriptor(self) -> SourceDescriptor | None:
        return self.descriptor

    @property
    def reference_sha256(self) -> str:
        return (
            self.descriptor.binding_sha256
            if self.descriptor
            else hashlib.sha256(b"empty-scan-project-v1").hexdigest()
        )

    def display_buffers(self) -> tuple[NDArray[np.float32], NDArray[np.uint32]]:
        positions = np.ascontiguousarray(self.data.xyz, dtype="<f4")
        indices = np.ascontiguousarray(
            self.data.triangles[self.data.face_status == 0], dtype="<u4"
        )
        return _freeze(positions), _freeze(indices)

    def metadata(self) -> dict[str, object]:
        from dataclasses import asdict

        return {
            "name": self.name,
            "source": self.descriptor.model_dump() if self.descriptor else None,
            "vertices": len(self.local),
            "triangles": len(self.data.triangles),
            "display_triangles": int(np.count_nonzero(self.data.face_status == 0)),
            "units": "unknown",
            "diagnostics": asdict(self.diagnostics),
            "bounds": [self.local.min(axis=0).tolist(), self.local.max(axis=0).tolist()]
            if len(self.local)
            else None,
            "display_frame": {
                "origin": self.origin.tolist(),
                "columns": self.frame.tolist(),
            },
            "positions": {"dtype": "<f4", "components": 3},
            "indices": {"dtype": "<u4", "components": 3},
            "capabilities": {
                "max_sources": 1,
                "profile": PROFILE,
                "normals_required": True,
            },
        }


def _preflight(
    reader: MeshPlyReader | SequentialMeshPlyReader, limits: ScanLimits
) -> tuple[int, int, int]:
    vertex, face = reader.layout.elements
    count, faces = vertex.element.count, face.element.count
    if (
        reader.layout.byte_count > limits.max_source_bytes
        or count > limits.max_vertices
        or faces > limits.max_faces
    ):
        raise ScanImportError(
            "Scan exceeds the declared byte/vertex/face limits.", "source_limit"
        )
    if tuple(prop.name for prop in vertex.element.properties) != (
        "x",
        "y",
        "z",
        "nx",
        "ny",
        "nz",
    ):
        raise ScanImportError(
            "This initial scan profile requires complete vertex normals.",
            "unsupported_source",
        )
    # Geometry, dispositions, display copies, and bounded parsing/area work.
    # Source bytes are replayed rather than retained as an uncompressed buffer.
    # Runtime/ZIP/transport overhead still requires whole-process evidence.
    estimated = 128 * count + 64 * faces + 32 * limits.chunk_bytes + 1_048_576
    if estimated > limits.max_estimated_bytes:
        raise ScanImportError(
            "Scan exceeds the declared incremental working-set estimate.",
            "source_limit",
        )
    return count, faces, estimated


def _decode_scan(
    source: ReplayableInput,
    stream: CheckedReader,
    filename: str,
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ScanWorkspace:
    """Decode one sequential replay into preallocated authoritative arrays."""

    def report(phase: str, completed: int, total: int) -> None:
        check_cancelled(cancelled)
        if progress is not None:
            progress(ScanProgress(phase, completed, total))
        check_cancelled(cancelled)

    report("preflight", 0, 1)
    report("reading", 0, source.byte_length)
    reader = SequentialMeshPlyReader(
        as_binary_stream(stream),
        byte_length=source.byte_length,
        max_range_bytes=limits.chunk_bytes,
        io_block_bytes=limits.chunk_bytes,
    )
    count, faces, estimated = _preflight(reader, limits)
    report("validation", 0, count + faces)
    xyz = np.empty((count, 3), dtype=np.float64)
    normals = np.empty((count, 3), dtype=np.float64)
    original_normals = np.empty((count, 3), dtype="<f4")
    triangles = np.empty((faces, 3), dtype="<i4")
    weights = np.zeros(count, dtype=np.float64)
    references = np.zeros(count, dtype=np.int64)
    statuses = np.zeros(faces, dtype="u1")
    step = max(1, limits.chunk_bytes // 24)
    for start in range(0, count, step):
        report("vertices", start, count)
        stop = min(count, start + step)
        rows = reader.read_range("vertex", start, stop)
        for column, name in enumerate(("x", "y", "z")):
            xyz[start:stop, column] = rows[name]
        for column, name in enumerate(("nx", "ny", "nz")):
            original_normals[start:stop, column] = rows[name]
        directions = original_normals[start:stop].astype(np.float64)
        if not np.isfinite(xyz[start:stop]).all() or not np.isfinite(directions).all():
            raise ScanImportError(
                f"Nonfinite position or normal in vertex rows {start}..{stop}."
            )
        lengths = np.linalg.norm(directions, axis=1)
        if np.any(lengths <= 0):
            raise ScanImportError(f"Zero normal in vertex rows {start}..{stop}.")
        normals[start:stop] = directions / lengths[:, None]
    step = max(1, limits.chunk_bytes // 13)
    for start in range(0, faces, step):
        report("faces", start, faces)
        stop = min(faces, start + step)
        indices = reader.read_range("face", start, stop)["vertex_indices"]["values"]
        if np.any(indices < 0) or np.any(indices >= count):
            raise ScanImportError(
                f"Out-of-range triangle index in face rows {start}..{stop}."
            )
        triangles[start:stop] = indices
        corners = xyz[indices]
        areas = (
            np.linalg.norm(
                np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]),
                axis=1,
            )
            / 2
        )
        if not np.isfinite(areas).all():
            raise ScanImportError("Nonfinite triangle area during source preparation.")
        repeated = (
            (indices[:, 0] == indices[:, 1])
            | (indices[:, 1] == indices[:, 2])
            | (indices[:, 0] == indices[:, 2])
        )
        statuses[start:stop] = np.where(repeated, 3, np.where(areas == 0, 4, 0))
        # Preserve source face/corner accumulation order across chunk sizes.
        np.add.at(weights, indices.ravel(), np.repeat(areas / 3, 3))
        np.add.at(references, indices.ravel(), 1)
    if not np.isfinite(weights).all():
        raise ScanImportError("Nonfinite incident-area contribution.")
    report("source-eof", count + faces, count + faces)
    reader.finish()
    hint = PurePosixPath(filename.replace("\\", "/")).name
    descriptor = SourceDescriptor(
        source_sha256=stream.sha256,
        source_bytes=source.byte_length,
        interpretation_sha256=INTERPRETATION_SHA256,
        vertices=count,
        triangles=faces,
        filename=hint,
    )
    diagnostics = ScanDiagnostics(
        int(np.count_nonzero(statuses == 3)),
        int(np.count_nonzero(statuses == 4)),
        int(np.count_nonzero(references == 0)),
        int(np.count_nonzero((references > 0) & (weights == 0))),
        int(np.count_nonzero(weights > 0)),
        estimated,
    )
    result = ScanWorkspace(
        hint,
        descriptor,
        ScanGeometry(
            _freeze(xyz),
            _freeze(normals),
            _freeze(original_normals),
            _freeze(triangles),
            _freeze(weights),
            _freeze(statuses),
        ),
        VerifiedSource(source, descriptor),
        diagnostics,
        _freeze(np.zeros(3)),
        _freeze(np.eye(3)),
    )
    report("prepared", count + faces, count + faces)
    return result


def _input(
    stream: ReplayableInput | BinaryIO,
    filename: str | None,
    limits: ScanLimits,
    cancelled: Callable[[], bool] | None,
) -> ReplayableInput:
    if isinstance(stream, ReplayableInput):
        source = stream
    else:
        # Compatibility for bounded legacy BytesIO/test callers. Native, worker
        # and archive adapters supply explicit replayable authorities instead.
        _ = stream.seek(0, 2)
        length = stream.tell()
        if not 0 < length <= limits.max_source_bytes:
            raise ScanImportError("Scan exceeds its source byte limit.", "source_limit")
        _ = stream.seek(0)
        chunks: list[bytes] = []
        seen = 0
        while seen < length:
            check_cancelled(cancelled)
            chunk = stream.read(min(limits.chunk_bytes, length - seen))
            if not chunk or len(chunk) > min(limits.chunk_bytes, length - seen):
                raise ScanImportError(
                    "Legacy input was truncated or returned an invalid read."
                )
            chunks.append(chunk)
            seen += len(chunk)
        if stream.read(1):
            raise ScanImportError(
                "Legacy input grew while taking its bounded snapshot."
            )
        source = BytesInput(b"".join(chunks), filename or "scan.ply")
    if (
        type(source.byte_length) is not int
        or not 0 < source.byte_length <= limits.max_source_bytes
    ):
        raise ScanImportError("Scan exceeds its source byte limit.", "source_limit")
    return source


def prepare_scan(
    stream: ReplayableInput | BinaryIO,
    filename: str | None,
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ScanWorkspace:
    """Prepare a caller-owned stream, retaining a stable import error boundary."""
    try:
        check_cancelled(cancelled)
        source = _input(stream, filename, limits, cancelled)
        with (
            source.open() as raw,
            CheckedReader(
                raw, source.byte_length, chunk_bytes=limits.chunk_bytes
            ) as checked,
        ):
            return _decode_scan(
                source,
                checked,
                filename or source.filename,
                limits,
                progress=progress,
                cancelled=cancelled,
            )
    except MeshPlyError as error:
        code = (
            "unsupported_source"
            if error.detail.category == "unsupported"
            else "invalid_source"
        )
        raise ScanImportError(str(error), code) from error
    except (OSError, ValidationError) as error:
        raise ScanImportError(str(error)) from error


def prepare_scan_input(
    source: ReplayableInput,
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ScanWorkspace:
    return prepare_scan(
        source, source.filename, limits, progress=progress, cancelled=cancelled
    )
