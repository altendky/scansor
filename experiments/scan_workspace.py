"""Bounded, transport-independent scan sources for the viewer experiment.

Format readers own PLY/STL byte decoding. This module owns interactive admission,
observation identities, incident-area weights and separate display derivatives.
"""

from __future__ import annotations

import hashlib
import io
import json
import struct
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, ClassVar, Literal, Protocol, cast, override

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
from scansor.mesh_stl import (
    StlCancelled,
    StlError,
    StlLimits,
    StlProgress,
    inspect_stl,
    iter_stl_facets,
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
STL_PROFILE = "triangle-stl-f32-v1"
_STL_INTERPRETATION = {
    "version": "interactive-triangle-scan-v1",
    "profile": STL_PROFILE,
    "coordinates": "binary32-or-decimal-to-binary64-to-binary32-then-float64",
    "topology": "exact-coordinate-triplets-canonical-positive-zero-first-corner-order-v1",
    "normals": "ignore-stored-facet-normals;face-corner-ordered-binary64-area-vector-sums;reject-zero-nonfinite;normalize-round-binary32-then-unit-binary64-v1",
    "weights": "source-face-order-incident-area-thirds-v1",
    "units": "unknown",
}
STL_INTERPRETATION_SHA256 = hashlib.sha256(_canonical(_STL_INTERPRETATION)).hexdigest()
STL_FALLBACK_INTERPRETATION_SHA256 = hashlib.sha256(
    _canonical(
        {
            **_STL_INTERPRETATION,
            "normals": "ignore-stored-facet-normals;face-corner-ordered-binary64-area-vector-sums;exact-zero-fallback;reject-zero-area-nonfinite;normalize-round-binary32-then-unit-binary64-v1",
            "cancelled_normals": "largest-positive-area-incident-geometric-vector;first-source-facet-breaks-ties;exact-zero-only-v1",
        }
    )
).hexdigest()


class SourceDescriptor(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    version: Literal["interactive-triangle-scan-v1"] = "interactive-triangle-scan-v1"
    profile: Literal["binary-triangle-ply-v1", "triangle-stl-f32-v1"] = (
        "binary-triangle-ply-v1"
    )
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


def supported_source_interpretation(descriptor: SourceDescriptor) -> bool:
    return descriptor.interpretation_sha256 in {
        PROFILE: {INTERPRETATION_SHA256},
        STL_PROFILE: {STL_INTERPRETATION_SHA256, STL_FALLBACK_INTERPRETATION_SHA256},
    }.get(descriptor.profile, set())


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
    fallback_normal_vertices: int = 0
    fallback_normal_first_ids: tuple[int, ...] = ()


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

    def normal_warnings(self) -> list[dict[str, object]]:
        count = self.diagnostics.fallback_normal_vertices
        if not count:
            return []
        ids = list(self.diagnostics.fallback_normal_first_ids)
        return [
            {
                "code": "stl_normal_fallback",
                "vertices": count,
                "first_ids": ids,
                "message": (
                    f"STL normal averaging cancelled at {count:,} vertices "
                    f"(first IDs {ids}). Used the largest incident triangle's geometric "
                    "normal, with source order breaking ties. These directions are "
                    "ambiguous and can affect normal-based selection and fitting. "
                    "All vertices, triangles and original file bytes are preserved."
                ),
            }
        ]

    def metadata(self) -> dict[str, object]:
        from dataclasses import asdict

        stl = self.descriptor is not None and self.descriptor.profile == STL_PROFILE
        return {
            "name": self.name,
            "source": self.descriptor.model_dump() if self.descriptor else None,
            "vertices": len(self.local),
            "triangles": len(self.data.triangles),
            "display_triangles": int(np.count_nonzero(self.data.face_status == 0)),
            "units": "unknown",
            "diagnostics": asdict(self.diagnostics),
            "source_warnings": self.normal_warnings(),
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
                "profile": self.descriptor.profile if self.descriptor else PROFILE,
                "normals_required": not stl,
            },
            **({"normal_source": "geometry-area-weighted-smoothed"} if stl else {}),
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


def _decode_stl(
    source: ReplayableInput,
    filename: str,
    limits: ScanLimits,
    *,
    inspection_stream: BinaryIO | None = None,
    progress: Callable[[ScanProgress], None] | None,
    cancelled: Callable[[], bool] | None,
) -> ScanWorkspace:
    """Build vertex observations from exact admitted float32 STL coordinates.

    All incident usable triangle area vectors contribute to each vertex normal.
    This intentionally smooths across creases; positions/connectivity are not
    smoothed, repaired, tolerance-welded, or replaced with triangle centroids.
    """

    def report(phase: str, completed: int, total: int) -> None:
        check_cancelled(cancelled)
        if progress is not None:
            progress(ScanProgress(phase, completed, total))
        check_cancelled(cancelled)

    def parser_progress(update: StlProgress) -> None:
        report(f"stl-{update.phase}", update.completed, update.total)

    if 32 * limits.chunk_bytes + 2_097_152 > limits.max_estimated_bytes:
        raise ScanImportError(
            "STL parser work exceeds the incremental working-set estimate.",
            "source_limit",
        )
    parser_limits = StlLimits(
        limits.max_source_bytes, limits.max_faces, chunk_bytes=limits.chunk_bytes
    )

    def inspect_open() -> AbstractContextManager[BinaryIO]:
        return (
            source.open()
            if inspection_stream is None
            else nullcontext(inspection_stream)
        )

    metadata = inspect_stl(
        inspect_open,
        source.byte_length,
        limits=parser_limits,
        progress=parser_progress,
        cancelled=cancelled,
    )
    faces = metadata.facets
    if not faces:
        raise ScanImportError("An STL scan requires at least one triangle.")
    corner_count = 3 * faces
    # Account for corner storage, exact-coordinate sorting/deduplication copies,
    # first-occurrence remapping, final geometry, accumulation/display arrays,
    # and bounded parser work. This is an admission estimate, not whole-runtime
    # memory evidence. No Python coordinate dictionary or source byte copy grows
    # alongside these explicitly bounded arrays.
    estimated = 256 * corner_count + 192 * faces + 32 * limits.chunk_bytes + 2_097_152
    if estimated > limits.max_estimated_bytes:
        raise ScanImportError(
            "STL connectivity work exceeds the incremental working-set estimate.",
            "source_limit",
        )
    report("stl-corners", 0, corner_count)
    corners = np.empty((corner_count, 3), dtype="<f4")
    for chunk in iter_stl_facets(
        source.open,
        metadata,
        limits=parser_limits,
        progress=parser_progress,
        cancelled=cancelled,
    ):
        start = 3 * chunk.start
        target = corners[start : start + 3 * len(chunk.vertices)]
        target[:] = chunk.vertices.reshape((-1, 3))
        # -0 and +0 denote the same exact coordinate. Preserve all other
        # admitted binary32 values; there is no proximity/welding tolerance.
        target[target == 0] = 0
        report("stl-corners", start + len(target), corner_count)
    report("stl-connectivity", 0, corner_count)
    unique, first, inverse = np.unique(
        corners, axis=0, return_index=True, return_inverse=True
    )
    count = len(first)
    if count > limits.max_vertices or count > 2**31:
        raise ScanImportError(
            "STL exact-coordinate vertices exceed the declared limit.", "source_limit"
        )
    order = np.argsort(first, kind="stable")
    xyz = corners[first[order]].astype(np.float64)
    remap = np.empty(count, dtype=np.int64)
    remap[order] = np.arange(count, dtype=np.int64)
    triangles = remap[inverse.reshape(-1)].reshape((-1, 3)).astype("<i4")
    del unique, first, inverse, order, corners, remap
    report("stl-connectivity", corner_count, corner_count)
    sums = np.zeros((count, 3), dtype=np.float64)
    weights = np.zeros(count, dtype=np.float64)
    references = np.zeros(count, dtype=np.int64)
    statuses = np.empty(faces, dtype="u1")
    step = max(1, limits.chunk_bytes // 192)
    for start in range(0, faces, step):
        report("faces", start, faces)
        indices = triangles[start : start + step]
        vertices = xyz[indices]
        vectors = np.cross(
            vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
        )
        areas = np.linalg.norm(vectors, axis=1) / 2
        if not np.isfinite(areas).all():
            raise ScanImportError("Nonfinite STL triangle area.")
        repeated = (
            (indices[:, 0] == indices[:, 1])
            | (indices[:, 1] == indices[:, 2])
            | (indices[:, 0] == indices[:, 2])
        )
        statuses[start : start + len(indices)] = np.where(
            repeated, 3, np.where(areas == 0, 4, 0)
        )
        # Order is original facet then corner, independent of parser chunk size.
        np.add.at(sums, indices.ravel(), np.repeat(vectors, 3, axis=0))
        np.add.at(weights, indices.ravel(), np.repeat(areas / 3, 3))
        np.add.at(references, indices.ravel(), 1)
    report("stl-normals", 0, count)
    lengths = np.linalg.norm(sums, axis=1)
    zero_area = np.flatnonzero(weights <= 0)
    cancelled_normals = np.flatnonzero(
        (weights > 0) & ((lengths <= 0) | ~np.isfinite(lengths))
    )
    if len(zero_area) or not np.isfinite(sums).all() or not np.isfinite(weights).all():
        raise ScanImportError(
            f"STL derived normals are unusable: {len(zero_area)} zero-area-only vertices "
            + f"(first IDs {zero_area[:16].tolist()}); {len(cancelled_normals)} cancelled or nonfinite "
            + f"positive-area directions (first IDs {cancelled_normals[:16].tolist()}).",
            "invalid_stl_normals",
        )
    if len(cancelled_normals):
        # Preserve every observation and facet. Only exactly cancelled sums use
        # a chosen incident direction; near cancellation is not thresholded.
        affected = np.zeros(count, dtype=np.bool_)
        affected[cancelled_normals] = True
        largest = np.zeros(len(cancelled_normals), dtype=np.float64)
        for start in range(0, faces, step):
            report("stl-normal-fallback", start, faces)
            rows = triangles[start : start + step]
            rows = rows[np.any(affected[rows], axis=1)]
            vertices = xyz[rows]
            vectors = np.cross(
                vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
            )
            magnitudes = np.linalg.norm(vectors, axis=1)
            for indices, vector, magnitude in zip(
                rows, vectors, magnitudes, strict=True
            ):
                for vertex in indices[affected[indices]]:
                    slot = int(np.searchsorted(cancelled_normals, vertex))
                    if magnitude > largest[slot]:
                        largest[slot] = magnitude
                        sums[vertex] = vector
        lengths[cancelled_normals] = largest
        del affected, largest
    original_normals = (sums / lengths[:, None]).astype("<f4")
    normals = original_normals.astype(np.float64)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    del sums, lengths, zero_area
    hint = PurePosixPath(filename.replace("\\", "/")).name
    descriptor = SourceDescriptor(
        profile=STL_PROFILE,
        source_sha256=metadata.source_sha256,
        source_bytes=source.byte_length,
        interpretation_sha256=(
            STL_FALLBACK_INTERPRETATION_SHA256
            if len(cancelled_normals)
            else STL_INTERPRETATION_SHA256
        ),
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
        len(cancelled_normals),
        tuple(int(value) for value in cancelled_normals[:16]),
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


class _PrefixedStream(io.RawIOBase):
    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        super().__init__()
        self._prefix: bytes = prefix
        self._stream: BinaryIO = stream
        self._position: int = 0

    @override
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("source reads require an explicit bounded size")
        head = self._prefix[self._position : self._position + size]
        self._position += len(head)
        if len(head) == size:
            return head
        return head + self._stream.read(size - len(head))


def _prefix(
    raw: BinaryIO,
    source: ReplayableInput,
    limits: ScanLimits,
    cancelled: Callable[[], bool] | None,
) -> bytes:
    result = bytearray()
    target = min(84, source.byte_length)
    while len(result) < target:
        check_cancelled(cancelled)
        requested = min(limits.chunk_bytes, target - len(result))
        chunk = raw.read(requested)
        if not chunk or len(chunk) > requested:
            raise ScanImportError("Source was truncated during format detection.")
        result.extend(chunk)
    return bytes(result)


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
        with source.open() as raw:
            prefix = _prefix(raw, source, limits, cancelled)
            binary_stl = (
                len(prefix) == 84
                and 84 + 50 * struct.unpack_from("<I", prefix, 80)[0]
                == source.byte_length
            )
            if not binary_stl and prefix[:3] == b"ply":
                with (
                    _PrefixedStream(prefix, raw) as prefixed,
                    CheckedReader(
                        cast(BinaryIO, cast(object, prefixed)),
                        source.byte_length,
                        chunk_bytes=limits.chunk_bytes,
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
            # Inspection consumes this sniffed replay through its validated EOF.
            # Archive members and VerifiedSource require complete successful reads;
            # replaying the prefix avoids an extra full source pass or snapshot.
            with _PrefixedStream(prefix, raw) as prefixed:
                return _decode_stl(
                    source,
                    filename or source.filename,
                    limits,
                    inspection_stream=cast(BinaryIO, cast(object, prefixed)),
                    progress=progress,
                    cancelled=cancelled,
                )
    except StlCancelled as error:
        raise ScanCancelled() from error
    except StlError as error:
        code = (
            "source_limit"
            if error.category == "resource"
            else "unsupported_source"
            if error.category == "unsupported"
            else "invalid_source"
        )
        raise ScanImportError(str(error), code) from error
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
