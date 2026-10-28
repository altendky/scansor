"""Bounded deterministic mesh derivatives for preliminary scan authoring.

Only connected mesh edges within one spatial/orientation cell are collapsed.
Representatives retain original observations; the resulting mesh receives its
own incident-area weights on ordinary import. This is not a topology-preserving
simplifier or an approximation certificate for the original fitting objective.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import BinaryIO, ClassVar, Literal, Self, cast, override

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from experiments.scan_workspace import (
    STL_PROFILE,
    ScanImportError,
    ScanLimits,
    ScanProgress,
    ScanWorkspace,
    SourceDescriptor,
    VerifiedSource,
    check_cancelled,
    prepare_scan,
    supported_source_interpretation,
)
from scansor._plyio import Layout
from scansor.mesh_ply import PROFILE, mesh_header, mesh_layout

_MAX_REFINEMENT_PASSES = 16


class ReductionSettings(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    version: Literal["edge-cell-reduction-v1"] = "edge-cell-reduction-v1"
    divisions: int = Field(ge=2, le=4096, strict=True)


class VirtualScanDerivation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    original: SourceDescriptor
    derived: SourceDescriptor
    settings: ReductionSettings

    @model_validator(mode="after")
    def identities(self) -> Self:
        for descriptor in (self.original, self.derived):
            if not supported_source_interpretation(descriptor):
                raise ValueError(
                    "Virtual scans require the supported scan interpretation."
                )
        if self.derived.profile != PROFILE:
            raise ValueError(
                "Virtual scans require the canonical PLY derivative profile."
            )
        if self.original.binding_sha256 == self.derived.binding_sha256:
            raise ValueError("A virtual scan must have a different source binding.")
        return self


@dataclass(frozen=True)
class ReductionDiagnostics:
    original_vertices: int
    original_triangles: int
    usable_original_triangles: int
    supported_original_vertices: int
    merged_vertices: int
    refinement_passes: int
    split_vertices: int
    collapsed_triangles: int
    zero_area_triangles: int
    duplicate_triangles: int
    unreferenced_representatives: int
    derived_vertices: int
    derived_triangles: int
    estimated_bytes: int


@dataclass(frozen=True)
class ReducedScan:
    workspace: ScanWorkspace
    derivation: VirtualScanDerivation
    diagnostics: ReductionDiagnostics


def _error(message: str, code: str = "invalid_reduction") -> ScanImportError:
    return ScanImportError(message, code)


def _verified_original(
    original: ScanWorkspace,
    limits: ScanLimits,
    report: Callable[[str, int, int], None],
    cancelled: Callable[[], bool] | None,
) -> tuple[ScanWorkspace, int]:
    if (
        not isinstance(cast(object, original), ScanWorkspace)
        or original.descriptor is None
    ):
        raise _error("Virtual scan creation requires an imported original scan.")
    descriptor = SourceDescriptor.model_validate(original.descriptor.model_dump())
    if not supported_source_interpretation(descriptor):
        raise _error("The original scan interpretation is unsupported.")
    if (
        original.source is None
        or original.source.descriptor.semantic_record() != descriptor.semantic_record()
    ):
        raise _error("The original scan bytes disagree with their source identity.")
    if not np.array_equal(original.origin, np.zeros(3)) or not np.array_equal(
        original.frame, np.eye(3)
    ):
        raise _error("Virtual scans require the imported source coordinate frame.")
    # Original and verified geometry, clustering/sort copies, derived geometry,
    # and bounded writer/parser temporaries can coexist. No source-byte copy is
    # retained. This estimate
    # is deliberately conservative, not a whole-runtime memory guarantee.
    estimated = (
        512 * descriptor.vertices
        + 384 * descriptor.triangles
        + 96 * limits.chunk_bytes
        + 2_097_152
    )
    if descriptor.profile == STL_PROFILE:
        # Reconstructing STL observations also performs exact-coordinate
        # sorting/deduplication while the original geometry remains retained.
        estimated += original.diagnostics.estimated_bytes
    if estimated > limits.max_estimated_bytes:
        raise _error(
            "Virtual scan exceeds the incremental working-set estimate.", "source_limit"
        )
    report("verify-original", 0, 1)
    verified = prepare_scan(
        original.source,
        descriptor.filename,
        limits,
        cancelled=cancelled,
    )
    if verified.descriptor is None or (
        verified.descriptor.semantic_record() != descriptor.semantic_record()
    ):
        raise _error("The original descriptor disagrees with its imported bytes.")
    for name in (
        "xyz",
        "normals",
        "original_normals",
        "triangles",
        "weights",
        "face_status",
    ):
        report("verify-arrays", 0, 1)
        if not np.array_equal(
            getattr(original.data, name), getattr(verified.data, name)
        ):
            raise _error(f"The original {name} disagrees with its source bytes.")
    report("verify-original", 1, 1)
    return verified, estimated


def _clusters(
    workspace: ScanWorkspace,
    settings: ReductionSettings,
    triangles: NDArray[np.int32],
    step: int,
    report: Callable[[str, int, int], None],
) -> NDArray[np.int64]:
    xyz = workspace.data.xyz
    low = xyz.min(axis=0)
    extent = float(np.max(xyz.max(axis=0) - low))
    if not np.isfinite(extent) or extent <= 0:
        raise _error("The original scan has no spatial extent.")
    width = extent / settings.divisions
    # Separate operations fix the rounding contract. Float32 source coordinates
    # fit safely in binary64 differences, division, and bounded integer cells.
    cells = np.floor((xyz - low) / width).astype(np.int64)
    # Keep exact maximum-boundary observations in their own cell instead of
    # moving the outer edge inward through minimum-ID representative choice.
    _ = np.clip(cells, 0, settings.divisions, out=cells)
    radix = settings.divisions + 1
    key = cells[:, 0] + radix * (cells[:, 1] + radix * cells[:, 2])
    del cells
    normals = workspace.data.original_normals
    orientation = (
        (normals[:, 0] > 0).astype(np.int64)
        + 2 * (normals[:, 1] > 0).astype(np.int64)
        + 4 * (normals[:, 2] > 0).astype(np.int64)
    )
    key = 8 * key + orientation
    roots = np.arange(len(xyz), dtype=np.int64)

    def root(vertex: int) -> int:
        while int(roots[vertex]) != vertex:
            roots[vertex] = roots[int(roots[vertex])]
            vertex = int(roots[vertex])
        return vertex

    for start in range(0, len(triangles), step):
        report("cluster-edges", start, len(triangles))
        for face in triangles[start : start + step]:
            a, b, c = (int(value) for value in face)
            for left, right in ((a, b), (b, c), (c, a)):
                if key[left] != key[right]:
                    continue
                first, second = root(left), root(right)
                if first != second:
                    roots[max(first, second)] = min(first, second)
    for start in range(0, len(roots), step):
        report("resolve-clusters", start, len(roots))
        for vertex in range(start, min(start + step, len(roots))):
            roots[vertex] = root(vertex)
    return roots


class _ArrayPlyStream(io.RawIOBase):
    def __init__(self, chunks: Iterator[memoryview]) -> None:
        super().__init__()
        self._chunks: Iterator[memoryview] = chunks
        self._pending: memoryview = memoryview(b"")

    @override
    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("generated source requires bounded reads")
        result = bytearray()
        while len(result) < size:
            if not self._pending:
                self._pending = next(self._chunks, memoryview(b""))
                if not self._pending:
                    break
            count = min(size - len(result), len(self._pending))
            result.extend(self._pending[:count])
            self._pending = self._pending[count:]
        return bytes(result)


@dataclass(frozen=True)
class _ArrayPlyInput:
    """Canonical source bytes replayed from the accepted geometry arrays."""

    xyz: NDArray[np.float64]
    normals: NDArray[np.float32]
    triangles: NDArray[np.int32]
    layout: Layout
    filename: str = "Virtual scan.ply"

    @property
    def byte_length(self) -> int:
        return self.layout.byte_count

    def _chunks(self) -> Iterator[memoryview]:
        yield memoryview(self.layout.header.raw)
        block = self.layout.element("vertex")
        for start in range(0, len(self.xyz), 2048):
            stop = min(start + 2048, len(self.xyz))
            rows = np.empty(stop - start, dtype=block.dtype)
            for axis, name in enumerate(("x", "y", "z")):
                rows[name] = self.xyz[start:stop, axis]
            for axis, name in enumerate(("nx", "ny", "nz")):
                rows[name] = self.normals[start:stop, axis]
            yield memoryview(rows).cast("B")
        block = self.layout.element("face")
        for start in range(0, len(self.triangles), 4096):
            stop = min(start + 4096, len(self.triangles))
            rows = np.empty(stop - start, dtype=block.dtype)
            rows["vertex_indices"]["count"] = 3
            rows["vertex_indices"]["values"] = self.triangles[start:stop]
            yield memoryview(rows).cast("B")

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with _ArrayPlyStream(self._chunks()) as stream:
            yield cast(BinaryIO, cast(object, stream))


def _derivative_source(
    original: ScanWorkspace,
    representatives: NDArray[np.int64],
    triangles: NDArray[np.int64],
    settings: ReductionSettings,
    limits: ScanLimits,
    report: Callable[[str, int, int], None],
) -> _ArrayPlyInput:
    descriptor = original.descriptor
    assert descriptor is not None
    header = mesh_header(
        len(representatives),
        len(triangles),
        normals=True,
        comments=(
            settings.version,
            f"original-sha256 {descriptor.source_sha256}",
            f"divisions {settings.divisions}",
        ),
    )
    layout = mesh_layout(header)
    expected = len(header.raw) + 24 * len(representatives) + 13 * len(triangles)
    if (
        expected > limits.max_source_bytes
        or len(representatives) > limits.max_vertices
        or len(triangles) > limits.max_faces
    ):
        raise _error(
            "The derived scan exceeds the declared source limits.", "source_limit"
        )
    report("derive-source", 0, 1)
    return _ArrayPlyInput(
        original.data.xyz[representatives],
        original.data.original_normals[representatives],
        triangles.astype(np.int32),
        layout,
    )


def _remap_faces(
    original: ScanWorkspace,
    triangles: NDArray[np.int32],
    roots: NDArray[np.int64],
    step: int,
    report: Callable[[str, int, int], None],
) -> tuple[NDArray[np.int64], int, int, NDArray[np.bool_]]:
    retained = np.empty((len(triangles), 3), dtype=np.int64)
    split = np.zeros(len(roots), dtype=np.bool_)
    count = collapsed = zero_area = 0
    for start in range(0, len(triangles), step):
        report("remap-faces", start, len(triangles))
        source_faces = triangles[start : start + step]
        mapped = roots[source_faces]
        distinct = (
            (mapped[:, 0] != mapped[:, 1])
            & (mapped[:, 1] != mapped[:, 2])
            & (mapped[:, 0] != mapped[:, 2])
        )
        collapsed += int(np.count_nonzero(~distinct))
        mapped = mapped[distinct]
        corners = original.data.xyz[mapped]
        vectors = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        positive = np.any(vectors != 0, axis=1)
        zero_area += int(np.count_nonzero(~positive))
        old = original.data.xyz[source_faces[distinct][positive]]
        old_vectors = np.cross(old[:, 1] - old[:, 0], old[:, 2] - old[:, 0])
        dot = np.sum(vectors[positive] * old_vectors, axis=1)
        mapped = mapped[positive]
        split[mapped[dot <= 0].ravel()] = True
        retained[count : count + len(mapped)] = mapped
        count += len(mapped)
    return retained[:count], collapsed, zero_area, split


def _refine_orientation(
    original: ScanWorkspace,
    triangles: NDArray[np.int32],
    roots: NDArray[np.int64],
    step: int,
    report: Callable[[str, int, int], None],
) -> tuple[NDArray[np.int64], int, int, int, int]:
    split_vertices = 0
    for refinement in range(_MAX_REFINEMENT_PASSES + 1):
        report("refine-orientation", refinement, _MAX_REFINEMENT_PASSES)
        retained, collapsed, zero_area, split = _remap_faces(
            original, triangles, roots, step, report
        )
        if not np.any(split):
            return retained, collapsed, zero_area, refinement, split_vertices
        if refinement == _MAX_REFINEMENT_PASSES:
            break
        # Return every implicated cluster to its original vertices. This is
        # monotone: no split vertex is merged again, and no inverted face is
        # silently deleted or rewound to make the derivative pass validation.
        members = np.flatnonzero(split[roots])
        split_vertices += int(np.count_nonzero(roots[members] != members))
        roots[members] = members
    raise _error(
        "Reduction exceeds the orientation refinement budget; use more detail.",
        "inverted_reduction",
    )


def prepare_virtual_scan(
    original: ScanWorkspace,
    settings: ReductionSettings,
    limits: ScanLimits,
    *,
    progress: Callable[[ScanProgress], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ReducedScan:
    """Prepare a detached derivative; never mutate or publish the original scan."""

    def report(phase: str, completed: int, total: int) -> None:
        check_cancelled(cancelled)
        if progress is not None:
            progress(ScanProgress(phase, completed, total))
        check_cancelled(cancelled)

    report("reduction-preflight", 0, 1)
    settings = ReductionSettings.model_validate(settings.model_dump())
    original, estimated = _verified_original(original, limits, report, cancelled)
    triangles = original.data.triangles[original.data.face_status == 0]
    if not len(triangles):
        raise _error("Virtual scan creation requires usable source triangles.")
    step = max(1, limits.chunk_bytes // 192)
    roots = _clusters(original, settings, triangles, step, report)
    supported = np.unique(triangles)
    merged = len(supported) - len(np.unique(roots[supported]))
    if merged <= 0:
        raise _error("The chosen detail makes no useful reduction.", "no_reduction")
    retained, collapsed, zero_area, refinement, split_vertices = _refine_orientation(
        original, triangles, roots, step, report
    )
    merged = len(supported) - len(np.unique(roots[supported]))
    if merged <= 0:
        raise _error(
            "Orientation refinement leaves no useful reduction.", "no_reduction"
        )
    count = len(retained)
    if not count:
        raise _error(
            "The chosen detail removes all usable triangles.", "empty_reduction"
        )
    report("deduplicate-faces", 0, count)
    _, first = np.unique(np.sort(retained, axis=1), axis=0, return_index=True)
    retained = retained[np.sort(first)]
    report("deduplicate-faces", count, count)
    representatives, inverse = np.unique(retained, return_inverse=True)
    final_faces = inverse.reshape((-1, 3))
    unreferenced = len(np.unique(roots)) - len(representatives)
    source = _derivative_source(
        original, representatives, final_faces, settings, limits, report
    )
    report("import-derivative", 0, 1)
    derived = prepare_scan(source, source.filename, limits, cancelled=cancelled)
    assert original.descriptor is not None and derived.descriptor is not None
    # The accepted source shares its final decoded arrays; release construction
    # arrays and never retain an additional packed PLY/source-sized byte buffer.
    source = _ArrayPlyInput(
        derived.data.xyz,
        derived.data.original_normals,
        derived.data.triangles,
        source.layout,
    )
    derived = replace(derived, source=VerifiedSource(source, derived.descriptor))
    assert derived.descriptor is not None
    result = ReducedScan(
        derived,
        VirtualScanDerivation(
            original=original.descriptor, derived=derived.descriptor, settings=settings
        ),
        ReductionDiagnostics(
            len(original.data.xyz),
            len(original.data.triangles),
            len(triangles),
            len(supported),
            merged,
            refinement,
            split_vertices,
            collapsed,
            zero_area,
            count - len(retained),
            unreferenced,
            len(representatives),
            len(final_faces),
            estimated,
        ),
    )
    report("reduction-prepared", 1, 1)
    return result
