"""Read-only, bounded mesh queries with complete artifact/accounting checks.

Opening verifies structure, source/child hashes, ordered row digests and summaries.
It does not prove that derived geometry came from the source: mesh_replay owns
that independent recomputation. Query arrays are owned, readonly range copies.
"""

from __future__ import annotations

import math
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np

from scansor.errors import ScansorError
from scansor.mesh_accumulation import CORNER_REVISION
from scansor.mesh_artifact_io import Progress, ReadDirectory, ReadFile, no_progress
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, control_id, encode_control
from scansor.mesh_digests import RowDigest
from scansor.mesh_dispositions import normal_status, vertex_status
from scansor.mesh_errors import MeshImportError
from scansor.mesh_numeric import (
    bits_float,
    canonical_f32,
    normalized_weights,
    ordered_fold,
)
from scansor.mesh_ply import MeshPlyReader
from scansor.mesh_policy import (
    contribution_inventory,
    contribution_request,
    contribution_status,
)
from scansor.mesh_resources import MAX_BATCH_ROWS
from scansor.mesh_semantics import (
    ColumnSpec,
    complete_import_inventory,
    contribution_specs,
    import_specs,
)
from scansor.mesh_sidecar import MAX_SIDECAR_BYTES, interpret_sidecar
from scansor.mesh_snapshot import Snapshot, SourceBundle
from scansor.mesh_statistics import (
    ValueRange,
    add_category_counts,
    contribution_summary,
    import_summary,
)

_IMPORT_KEYS = frozenset(
    (
        "revision",
        "status",
        "source",
        "source_id",
        "profile",
        "numeric_revision",
        "importer_implementation",
        "association_revision",
        "physical_unit",
        "source_frame_id",
        "normal_convention",
        "vertices",
        "faces",
        "sidecar_interpretation",
        "columns",
        "accounting_revision",
        "row_digests",
        "summary",
        "corner_revision",
    )
)
_CONTRIBUTION_KEYS = frozenset(
    ("revision", "status", "import_id", "request", "summary", "columns", "row_digests")
)


def _same(actual: Control, expected: Control, label: str) -> None:
    # Canonical bytes distinguish bool/int and reject extra keys at every level.
    if encode_control(actual) != encode_control(expected):
        raise MeshImportError(
            "integrity", "artifact-verify", f"{label} differs from the required record"
        )


def _hash(value: Control) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise MeshImportError(
            "structure", "artifact-control", "invalid SHA-256 identity"
        )
    return value


def _summary_schema(actual: Control, template: Control, *, key: str = "") -> None:
    if isinstance(template, dict):
        if not isinstance(actual, dict) or actual.keys() != template.keys():
            raise MeshImportError(
                "structure", "artifact-control", "unknown or missing summary fields"
            )
        for name, expected in template.items():
            _summary_schema(actual[name], expected, key=name)
    elif key.endswith("_bits"):
        if actual is None and template is None:
            return
        if type(actual) is not str:
            raise MeshImportError(
                "structure", "artifact-control", "invalid summary binary64 encoding"
            )
        value = bits_float(actual)
        if not math.isfinite(value) or math.copysign(1.0, value) < 0:
            raise MeshImportError(
                "structure",
                "artifact-control",
                "summary measure must be finite nonnegative binary64",
            )
    elif type(template) is int:
        if type(actual) is not int or not 0 <= actual <= 2**64 - 1:
            raise MeshImportError(
                "structure", "artifact-control", "invalid summary count"
            )
    elif actual != template or type(actual) is not type(template):
        raise MeshImportError(
            "structure",
            "artifact-control",
            "unknown summary population, measure or revision",
        )


def _column_schema(value: Control, specs: tuple[ColumnSpec, ...]) -> None:
    if not isinstance(value, list) or len(value) != len(specs):
        raise MeshImportError(
            "structure", "artifact-control", "missing or extra column descriptors"
        )
    for entry, spec in zip(value, specs, strict=True):
        if not isinstance(entry, dict):
            raise MeshImportError(
                "structure", "artifact-control", "column descriptor must be an object"
            )
        expected: dict[str, Control] = {
            "name": spec.name,
            "shape": list(spec.shape),
            "dtype": spec.encoding,
            "byte_count": spec.byte_count,
            "sha256": _hash(entry.get("sha256")),
        }
        _same(entry, expected, "column descriptor")


def _digests(value: Control, names: tuple[str, ...]) -> dict[str, Control]:
    if not isinstance(value, dict) or set(value) != set(names):
        raise MeshImportError(
            "structure", "artifact-control", "unknown or missing row digest fields"
        )
    return {name: _hash(value[name]) for name in names}


def _interpret(source: SourceBundle) -> dict[str, Control]:
    sidecar = source.sidecar
    if sidecar is None:
        return interpret_sidecar(None)
    if sidecar.byte_count > MAX_SIDECAR_BYTES:
        return interpret_sidecar(None, byte_count=sidecar.byte_count)
    _ = sidecar.stream.seek(0)
    return interpret_sidecar(
        sidecar.stream.read(MAX_SIDECAR_BYTES + 1), byte_count=sidecar.byte_count
    )


@dataclass(frozen=True)
class MeshRange:
    source_id: str
    stage_id: str
    table: str
    start: int
    stop: int
    columns: dict[str, np.ndarray]


@dataclass
class ImportArtifact:
    directory: ReadDirectory
    source_directory: ReadDirectory
    files: list[ReadFile]
    columns: dict[str, Column]
    source: SourceBundle
    vertices: int
    faces: int
    has_normals: bool
    chunk_rows: int
    inventory: dict[str, Control]
    summary: dict[str, Control]
    identity: str
    source_id: str

    def check(self) -> None:
        self.directory.check()
        self.source_directory.check()
        for file in self.files:
            file.check()

    def read_vertices(self, start: int, stop: int) -> MeshRange:
        specs = import_specs(self.vertices, self.faces, normals=self.has_normals)[:-3]
        return self._range("vertices", specs, start, stop)

    def read_faces(self, start: int, stop: int) -> MeshRange:
        specs = import_specs(self.vertices, self.faces, normals=self.has_normals)[-3:]
        return self._range("faces", specs, start, stop)

    def _range(
        self, table: str, specs: tuple[ColumnSpec, ...], start: int, stop: int
    ) -> MeshRange:
        self.check()
        columns = {
            spec.name: self.columns[spec.name].read_range(start, stop) for spec in specs
        }
        self.check()
        return MeshRange(self.source_id, self.identity, table, start, stop, columns)


@dataclass
class ContributionArtifact:
    imported: ImportArtifact
    directory: ReadDirectory
    files: list[ReadFile]
    columns: dict[str, Column]
    inventory: dict[str, Control]
    summary: dict[str, Control]
    request: dict[str, Control]
    identity: str

    def check(self) -> None:
        self.imported.check()
        self.directory.check()
        for file in self.files:
            file.check()

    def read_vertices(self, start: int, stop: int) -> MeshRange:
        self.check()
        base = self.imported.read_vertices(start, stop)
        values = {
            name: column.read_range(start, stop)
            for name, column in self.columns.items()
        }
        self.check()
        return MeshRange(
            base.source_id,
            self.identity,
            "vertices",
            start,
            stop,
            base.columns | values,
        )


def _file(
    stack: ExitStack, directory: ReadDirectory, name: str, files: list[ReadFile]
) -> ReadFile:
    file = ReadFile(directory, name)
    _ = stack.callback(file.close)
    files.append(file)
    return file


def _sources(
    stack: ExitStack,
    directory: ReadDirectory,
    inventory: dict[str, Control],
    files: list[ReadFile],
    progress: Progress,
) -> SourceBundle:
    value = inventory.get("source")
    if not isinstance(value, dict) or value.get("sidecar") not in ("absent", "present"):
        raise MeshImportError(
            "structure", "artifact-control", "invalid source inventory"
        )
    present = value["sidecar"] == "present"
    directory.require_names(
        {"observations.ply", *(("observations.rsInfo",) if present else ())}
    )

    def snapshot(name: str, role: str) -> Snapshot:
        file = _file(stack, directory, name, files)
        digest = file.sha256(phase="artifact-source-" + role, progress=progress)
        return Snapshot(
            role,
            file.path,
            file.size,
            digest,
            file.stream,
            file.initial[:2],
            file.initial,
        )

    ply = snapshot("observations.ply", "mesh-ply")
    sidecar = snapshot("observations.rsInfo", "realityscan-rsinfo") if present else None
    result = SourceBundle(ply, sidecar)
    _same(value, result.inventory(), "source inventory")
    return result


def _columns(
    stack: ExitStack,
    directory: ReadDirectory,
    specs: tuple[ColumnSpec, ...],
    files: list[ReadFile],
    chunk: int,
    progress: Progress,
    stage: str,
) -> tuple[dict[str, Column], list[Control]]:
    columns: dict[str, Column] = {}
    records: list[Control] = []
    for spec in specs:
        file = _file(stack, directory, spec.name, files)
        if file.size != spec.byte_count:
            raise MeshImportError(
                "integrity", "artifact-open", "column length differs from source shape"
            )
        records.append(
            {
                "name": spec.name,
                "shape": list(spec.shape),
                "dtype": spec.encoding,
                "byte_count": file.size,
                "sha256": file.sha256(
                    phase=f"artifact-{stage}-hash-{spec.name}", progress=progress
                ),
            }
        )
        column = Column(
            spec, path=file.path, reopen=True, max_range_bytes=chunk * spec.stride
        )
        _ = stack.callback(column.close)
        columns[spec.name] = column
        file.check()
    return columns, records


def _scan_import(
    data: ImportArtifact, progress: Progress
) -> tuple[dict[str, Control], dict[str, Control]]:
    vertex_specs = import_specs(data.vertices, data.faces, normals=data.has_normals)[
        :-3
    ]
    face_specs = import_specs(data.vertices, data.faces, normals=data.has_normals)[-3:]
    vertices_digest = RowDigest(
        "mesh-import-vertices-v1", vertex_specs, max_rows=data.chunk_rows
    )
    faces_digest = RowDigest(
        "mesh-import-faces-v1", face_specs, max_rows=data.chunk_rows
    )
    vertices, normals, faces = [0] * 2, [0] * 4, [0] * 5
    references, invalid, total = 0, 0, 0.0
    progress("artifact-import-vertices", 0, data.vertices)
    for start in range(0, data.vertices, data.chunk_rows):
        stop = min(start + data.chunk_rows, data.vertices)
        values = data.read_vertices(start, stop).columns
        xyz = values["xyz.bin"]
        supplied_normals = values.get("normals.bin")
        if not np.array_equal(xyz.view("<u4"), canonical_f32(xyz).view("<u4")) or (
            supplied_normals is not None
            and not np.array_equal(
                supplied_normals.view("<u4"),
                canonical_f32(supplied_normals).view("<u4"),
            )
        ):
            raise MeshImportError(
                "integrity", "artifact-verify", "noncanonical source coordinate bits"
            )
        if not np.array_equal(
            values["vertex-status.bin"], vertex_status(xyz)
        ) or not np.array_equal(
            values["normal-status.bin"], normal_status(supplied_normals, stop - start)
        ):
            raise MeshImportError(
                "integrity",
                "artifact-verify",
                "coordinate dispositions disagree with columns",
            )
        add_category_counts(values["vertex-status.bin"], vertices)
        add_category_counts(values["normal-status.bin"], normals)
        references += sum(int(v) for v in values["reference-count.bin"])
        if references > 3 * data.faces:
            raise MeshImportError(
                "integrity",
                "artifact-verify",
                "reference count exceeds source corner population",
            )
        vertices_digest.update(start, tuple(values[spec.name] for spec in vertex_specs))
        progress("artifact-import-vertices", stop, data.vertices)
        del values, xyz, supplied_normals
    progress("artifact-import-faces", 0, data.faces)
    for start in range(0, data.faces, data.chunk_rows):
        stop = min(start + data.chunk_rows, data.faces)
        values = data.read_faces(start, stop).columns
        indices, status, areas = (values[spec.name] for spec in face_specs)
        outside = (indices < 0) | (indices.astype(np.int64) >= data.vertices)
        if (
            not np.array_equal(np.any(outside, axis=1), status == 1)
            or np.any((status != 0) & (areas.view("<u8") != 0))
            or np.any((status == 0) & (areas <= 0))
        ):
            raise MeshImportError(
                "integrity",
                "artifact-verify",
                "face disposition/area/index inconsistency",
            )
        add_category_counts(status, faces)
        invalid += int(np.count_nonzero(outside))
        total = ordered_fold(areas, initial=total)
        faces_digest.update(start, (indices, status, areas))
        progress("artifact-import-faces", stop, data.faces)
        del values, indices, status, areas, outside
    if references + invalid != 3 * data.faces:
        raise MeshImportError(
            "integrity", "artifact-verify", "incomplete source corner accounting"
        )
    summary = import_summary(
        vertices=data.vertices,
        faces=data.faces,
        vertex_counts=vertices,
        normal_counts=normals,
        face_counts=faces,
        references=references,
        invalid_corners=invalid,
        face_total=total,
    )
    return summary, {
        "vertices": vertices_digest.finish(),
        "faces": faces_digest.finish(),
    }


@contextmanager
def open_import(
    path: Path,
    *,
    chunk_rows: int = 65_536,
    expected_id: str | None = None,
    progress: Progress = no_progress,
) -> Generator[ImportArtifact]:
    caller_failed = False
    if type(chunk_rows) is not int or not 1 <= chunk_rows <= MAX_BATCH_ROWS:
        raise MeshImportError(
            "resource-budget-too-small", "artifact-open", "invalid query batch bound"
        )
    try:
        with ExitStack() as stack:
            root = ReadDirectory(path)
            _ = stack.callback(root.close)
            files: list[ReadFile] = []
            inventory = _file(stack, root, "inventory.json", files).control()
            if (
                inventory.keys() != _IMPORT_KEYS
                or inventory.get("revision") != "mesh-import-v1"
                or inventory.get("status") != "complete"
            ):
                raise MeshImportError(
                    "structure",
                    "artifact-control",
                    "unknown, incomplete or malformed import inventory",
                )
            identity = control_id(inventory)
            if expected_id is not None and identity != _hash(expected_id):
                raise MeshImportError(
                    "integrity",
                    "artifact-verify",
                    "import identity differs from expected root",
                )
            source_dir = ReadDirectory(root.access / "source")
            _ = stack.callback(source_dir.close)
            source = _sources(stack, source_dir, inventory, files, progress)
            reader = MeshPlyReader(source.ply.stream, max_range_bytes=chunk_rows * 24)
            vertex_layout, face_layout = reader.layout.elements
            n, m = vertex_layout.element.count, face_layout.element.count
            has_normals = "nx" in (vertex_layout.dtype.names or ())
            specs = import_specs(n, m, normals=has_normals)
            root.require_names(
                {
                    "inventory.json",
                    "summary.json",
                    "source",
                    *(spec.name for spec in specs),
                }
            )
            _column_schema(inventory["columns"], specs)
            summary = _file(stack, root, "summary.json", files).control()
            _summary_schema(
                summary,
                import_summary(
                    vertices=n,
                    faces=m,
                    vertex_counts=[0] * 2,
                    normal_counts=[0] * 4,
                    face_counts=[0] * 5,
                    references=0,
                    invalid_corners=0,
                    face_total=0.0,
                ),
            )
            expected = complete_import_inventory(
                source=source.inventory(),
                ply_sha256=source.ply.sha256,
                vertices=n,
                faces=m,
                sidecar=_interpret(source),
                artifacts=cast(list[Control], inventory["columns"]),
                summary=summary,
                row_digests=_digests(inventory["row_digests"], ("vertices", "faces")),
            )
            expected["corner_revision"] = CORNER_REVISION
            _same(inventory, expected, "import metadata")
            columns, records = _columns(
                stack, root, specs, files, chunk_rows, progress, "import"
            )
            _same(records, inventory["columns"], "import column hashes")
            result = ImportArtifact(
                root,
                source_dir,
                files,
                columns,
                source,
                n,
                m,
                has_normals,
                chunk_rows,
                inventory,
                summary,
                identity,
                source.identity,
            )
            computed_summary, digests = _scan_import(result, progress)
            _same(summary, computed_summary, "import summary")
            expected["row_digests"] = digests
            expected["columns"] = records
            _same(inventory, expected, "import inventory")
            result.check()
            try:
                yield result
            except BaseException:
                caller_failed = True
                raise
            result.check()
    except (OSError, ScansorError) as error:
        if caller_failed or isinstance(error, MeshImportError):
            raise
        raise MeshImportError("integrity", "artifact-read", str(error)) from error


def _scan_contributions(
    data: ContributionArtifact, progress: Progress
) -> tuple[dict[str, Control], str, int]:
    imported = data.imported
    specs = contribution_specs(imported.vertices)
    digest = RowDigest(
        "mesh-contribution-vertices-v1", specs, max_rows=imported.chunk_rows
    )
    counts, area_total, weight_total = [0] * 4, 0.0, 0.0
    areas_range, weights_range = ValueRange(), ValueRange()
    progress("artifact-contribution-vertices", 0, imported.vertices)
    for start in range(0, imported.vertices, imported.chunk_rows):
        stop = min(start + imported.chunk_rows, imported.vertices)
        values = data.read_vertices(start, stop).columns
        status, areas, weights = (values[spec.name] for spec in specs)
        expected_status = contribution_status(
            values["vertex-status.bin"], values["reference-count.bin"], areas
        )
        if not np.array_equal(status, expected_status):
            raise MeshImportError(
                "integrity",
                "artifact-verify",
                "contribution dispositions disagree with columns",
            )
        add_category_counts(status, counts)
        area_total = ordered_fold(areas, initial=area_total)
        weight_total = ordered_fold(weights, initial=weight_total)
        areas_range.add(areas[status == 0])
        weights_range.add(weights[status == 0])
        digest.update(start, (status, areas, weights))
        progress("artifact-contribution-vertices", stop, imported.vertices)
        del values, status, areas, weights, expected_status
    # A second bounded pass uses the complete population's K/S, never a batch
    # subtotal. Source replay must still establish the per-vertex areas themselves.
    progress("artifact-check-normalization", 0, imported.vertices)
    for start in range(0, imported.vertices, imported.chunk_rows):
        stop = min(start + imported.chunk_rows, imported.vertices)
        data.check()
        areas = data.columns["vertex-area.bin"].read_range(start, stop)
        weights = data.columns["weight.bin"].read_range(start, stop)
        expected_weights = normalized_weights(
            areas, eligible_count=counts[0], total=area_total
        )
        if not np.array_equal(weights.view("<u8"), expected_weights.view("<u8")):
            raise MeshImportError(
                "integrity",
                "artifact-verify",
                "weights violate the resolved normalization",
            )
        progress("artifact-check-normalization", stop, imported.vertices)
        del areas, weights, expected_weights
    summary = contribution_summary(
        vertices=imported.vertices,
        counts=counts,
        area_total=area_total,
        area_range=areas_range,
        weight_total=weight_total,
        weight_range=weights_range,
    )
    return summary, digest.finish(), counts[0]


@contextmanager
def open_contributions(
    path: Path,
    imported: ImportArtifact,
    *,
    expected_id: str | None = None,
    progress: Progress = no_progress,
) -> Generator[ContributionArtifact]:
    """Use inside open_import; queries retain that import's source binding."""
    caller_failed = False
    try:
        imported.check()
        with ExitStack() as stack:
            root = ReadDirectory(path)
            _ = stack.callback(root.close)
            files: list[ReadFile] = []
            inventory = _file(stack, root, "inventory.json", files).control()
            if (
                inventory.keys() != _CONTRIBUTION_KEYS
                or inventory.get("revision") != "mesh-contribution-v1"
                or inventory.get("status")
                not in ("complete", "complete-no-eligible-points")
            ):
                raise MeshImportError(
                    "structure",
                    "artifact-control",
                    "unknown, incomplete or malformed contribution inventory",
                )
            identity = control_id(inventory)
            if expected_id is not None and identity != _hash(expected_id):
                raise MeshImportError(
                    "integrity",
                    "artifact-verify",
                    "contribution identity differs from expected root",
                )
            if inventory["import_id"] != imported.identity:
                raise MeshImportError(
                    "integrity",
                    "artifact-verify",
                    "contributions belong to another import",
                )
            specs = contribution_specs(imported.vertices)
            root.require_names(
                {
                    "inventory.json",
                    "summary.json",
                    "request.json",
                    *(spec.name for spec in specs),
                }
            )
            _column_schema(inventory["columns"], specs)
            request = _file(stack, root, "request.json", files).control()
            _same(
                request,
                contribution_request(imported.identity),
                "resolved contribution request",
            )
            summary = _file(stack, root, "summary.json", files).control()
            _summary_schema(
                summary,
                contribution_summary(
                    vertices=imported.vertices,
                    counts=[0] * 4,
                    area_total=0.0,
                    area_range=ValueRange(),
                    weight_total=0.0,
                    weight_range=ValueRange(),
                ),
            )
            expected = contribution_inventory(
                import_id=imported.identity,
                eligible=int(inventory["status"] == "complete"),
                request=request,
                summary=summary,
                artifacts=cast(list[Control], inventory["columns"]),
                row_digest=str(
                    _digests(inventory["row_digests"], ("vertices",))["vertices"]
                ),
            )
            _same(inventory, expected, "contribution metadata")
            columns, records = _columns(
                stack, root, specs, files, imported.chunk_rows, progress, "contribution"
            )
            _same(records, inventory["columns"], "contribution column hashes")
            result = ContributionArtifact(
                imported, root, files, columns, inventory, summary, request, identity
            )
            computed_summary, digest, eligible = _scan_contributions(result, progress)
            _same(summary, computed_summary, "contribution summary")
            expected = contribution_inventory(
                import_id=imported.identity,
                eligible=eligible,
                request=request,
                summary=computed_summary,
                artifacts=records,
                row_digest=digest,
            )
            _same(inventory, expected, "contribution inventory")
            result.check()
            try:
                yield result
            except BaseException:
                caller_failed = True
                raise
            result.check()
    except (OSError, ScansorError) as error:
        if caller_failed or isinstance(error, MeshImportError):
            raise
        raise MeshImportError("integrity", "artifact-read", str(error)) from error
