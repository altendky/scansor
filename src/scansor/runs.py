from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from scansor.errors import ScansorError
from scansor.files import (
    hash_run_file,
    open_run_directory,
    read_regular,
    read_run_file,
    rename_no_replace,
    write_new_file,
)
from scansor.models import (
    ArtifactRecord,
    CanonicalRecord,
    InspectionRecord,
    InspectionReport,
    InspectJobConfig,
    JobRecord,
    ResolvedSettings,
    RunManifest,
    SemanticAbsences,
    SourceRecord,
)
from scansor.ply import (
    MAX_CANONICAL_BYTES,
    canonical_npy,
    load_canonical_npy,
    parse_ply,
)
from scansor.serialization import canonical_json, parse_canonical_json, sha256

RUN_FILES = frozenset(
    {"canonical.npy", "manifest.json", "manifest.sha256", "report.json"}
)
MAX_CONTROL_BYTES = 8 * 1024 * 1024


def _entry_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _verify_staged_artifacts(
    stage_fd: int,
    expected: dict[str, bytes],
    created_identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    try:
        expected_names = set(expected)
        if set(os.listdir(stage_fd)) != expected_names:
            raise ScansorError("staged artifact file set changed during publication")
        identities = {
            name: _entry_identity(os.stat(name, dir_fd=stage_fd, follow_symlinks=False))
            for name in expected
        }
        if identities != created_identities:
            raise ScansorError("staged artifact entry changed during publication")
        rehashed = {
            name: hash_run_file(stage_fd, name, len(data))
            for name, data in expected.items()
        }
        expected_hashes = {
            name: (len(data), sha256(data)) for name, data in expected.items()
        }
        if rehashed != expected_hashes:
            raise ScansorError("staged artifact content changed during publication")
        if set(os.listdir(stage_fd)) != expected_names:
            raise ScansorError("staged artifact file set changed during publication")
        final_identities = {
            name: _entry_identity(os.stat(name, dir_fd=stage_fd, follow_symlinks=False))
            for name in expected
        }
        if final_identities != created_identities:
            raise ScansorError("staged artifact entry changed during publication")
    except OSError as error:
        raise ScansorError("staged artifact changed during publication") from error


def build_run(
    raw: bytes,
    source_path: Path,
    unit: Literal["m", "mm"],
    frame: str,
    settings: ResolvedSettings,
    job: JobRecord,
) -> tuple[InspectionReport, bytes]:
    values = settings.values()
    parsed = parse_ply(
        raw,
        unit,
        max_header_bytes=values.max_header_bytes,
        max_vertices=values.max_vertices,
    )
    canonical = canonical_npy(parsed.canonical)
    source_sha = sha256(raw)
    canonical_record = CanonicalRecord(
        byte_count=len(canonical),
        sha256=sha256(canonical),
    )
    inspection = InspectionRecord(
        coordinate_source_dtype=parsed.coordinate_source_dtype,
        fields=list(parsed.fields),
        normal_magnitude_bounds=(
            list(parsed.normal_magnitude_bounds)
            if parsed.normal_magnitude_bounds is not None
            else None
        ),
        point_count=parsed.point_count,
        position_bounds_m={
            key: list(value) for key, value in parsed.position_bounds_m.items()
        },
        rgb_preserved=parsed.rgb_preserved,
    )
    absences = SemanticAbsences()
    source_record = SourceRecord(
        byte_count=len(raw),
        frame=frame,
        path=str(source_path.absolute()),
        sha256=source_sha,
        unit=unit,
    )
    semantic = {
        "canonical": canonical_record.model_dump(mode="json"),
        "format": "scansor-inspection-report-v1",
        "format_status": "internal/provisional/non-public-contract",
        "inspection": inspection.model_dump(mode="json"),
        "job": job.model_dump(mode="json"),
        "semantic_absences": absences.model_dump(mode="json"),
        "settings": settings.model_dump(mode="json"),
        "source": source_record.model_dump(mode="json", exclude={"path"}),
    }
    report = InspectionReport(
        canonical=canonical_record,
        inspection=inspection,
        job=job,
        run_id=sha256(canonical_json(semantic)),
        semantic_absences=absences,
        settings=settings,
        source=source_record,
    )
    return report, canonical


def inspect_source(
    request: InspectJobConfig, settings: ResolvedSettings
) -> tuple[InspectionReport, bytes]:
    values = settings.values()
    raw = read_regular(request.input_path, "PLY input", values.max_input_bytes)
    return build_run(
        raw,
        request.input_path,
        request.unit,
        request.frame,
        settings,
        request.record(),
    )


def _manifest(
    report: InspectionReport, report_bytes: bytes, canonical: bytes
) -> RunManifest:
    return RunManifest(
        artifacts={
            "canonical.npy": ArtifactRecord(
                byte_count=len(canonical), sha256=sha256(canonical)
            ),
            "report.json": ArtifactRecord(
                byte_count=len(report_bytes), sha256=sha256(report_bytes)
            ),
        },
        run_id=report.run_id,
    )


def _publish_run(output: Path, report: InspectionReport, canonical: bytes) -> None:
    output = output.absolute()
    if not output.name or output.name in {".", ".."}:
        raise ScansorError("output must name a new run directory")
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent_stat = os.fstat(parent_fd)
        try:
            _ = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ScansorError(
                f"output already exists; refusing to overwrite: {output}"
            )
        stage_name = ""
        for _ in range(128):
            candidate = f".{output.name}.scansor-stage-{secrets.token_hex(8)}"
            try:
                os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            stage_name = candidate
            break
        if not stage_name:
            raise ScansorError("could not reserve a staging directory")
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        try:
            stage_stat = os.fstat(stage_fd)
            report_bytes = canonical_json(report)
            manifest_bytes = canonical_json(_manifest(report, report_bytes, canonical))
            sidecar = f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
            staged = {
                "canonical.npy": canonical,
                "report.json": report_bytes,
                "manifest.json": manifest_bytes,
                "manifest.sha256": sidecar,
            }
            created_identities = {
                name: _entry_identity(write_new_file(stage_fd, name, data))
                for name, data in staged.items()
            }
            os.fsync(stage_fd)
            _verify_staged_artifacts(stage_fd, staged, created_identities)
            current_parent = os.stat(output.parent, follow_symlinks=False)
            if (current_parent.st_dev, current_parent.st_ino) != (
                parent_stat.st_dev,
                parent_stat.st_ino,
            ):
                raise ScansorError("output parent changed during publication")
            rename_no_replace(parent_fd, stage_name, parent_fd, output.name)
            _verify_staged_artifacts(stage_fd, staged, created_identities)
            try:
                final_parent = os.stat(output.parent, follow_symlinks=False)
                final_output = os.stat(
                    output.name, dir_fd=parent_fd, follow_symlinks=False
                )
                path_matches = (
                    final_parent.st_dev,
                    final_parent.st_ino,
                    final_output.st_dev,
                    final_output.st_ino,
                ) == (
                    parent_stat.st_dev,
                    parent_stat.st_ino,
                    stage_stat.st_dev,
                    stage_stat.st_ino,
                )
            except OSError:
                path_matches = False
            if not path_matches:
                raise ScansorError("output path changed during publication")
        finally:
            os.close(stage_fd)
    finally:
        os.close(parent_fd)


def publish_run(output: Path, report: InspectionReport, canonical: bytes) -> None:
    try:
        _publish_run(output, report, canonical)
    except ScansorError:
        raise
    except (UnicodeError, ValueError) as error:
        raise ScansorError(f"invalid output path: {output}") from error


def _load_run(
    directory_fd: int,
) -> tuple[
    InspectionReport,
    bytes,
    dict[str, tuple[int, int, int, int, int, int]],
]:
    names = set(os.listdir(directory_fd))
    if names != set(RUN_FILES):
        missing = sorted(RUN_FILES - names)
        unexpected = sorted(names - RUN_FILES)
        raise ScansorError(
            f"run file set mismatch; missing={missing}, unexpected={unexpected}"
        )
    entry_identities = {
        name: _entry_identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        for name in RUN_FILES
    }
    canonical = read_run_file(directory_fd, "canonical.npy", MAX_CANONICAL_BYTES)
    report_bytes = read_run_file(directory_fd, "report.json", MAX_CONTROL_BYTES)
    manifest_bytes = read_run_file(directory_fd, "manifest.json", MAX_CONTROL_BYTES)
    sidecar = read_run_file(directory_fd, "manifest.sha256", 256)
    expected_sidecar = f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    if sidecar != expected_sidecar:
        raise ScansorError("manifest sidecar mismatch")
    try:
        manifest = RunManifest.model_validate(
            parse_canonical_json(manifest_bytes, "manifest.json", MAX_CONTROL_BYTES)
        )
        report = InspectionReport.model_validate(
            parse_canonical_json(report_bytes, "report.json", MAX_CONTROL_BYTES)
        )
    except ValidationError as error:
        raise ScansorError(f"persisted run model is invalid: {error}") from error
    if canonical_json(manifest) != manifest_bytes:
        raise ScansorError("persisted manifest does not match its canonical model")
    if canonical_json(report) != report_bytes:
        raise ScansorError("persisted report does not match its canonical model")
    expected_artifacts = {
        "canonical.npy": ArtifactRecord(
            byte_count=len(canonical), sha256=sha256(canonical)
        ),
        "report.json": ArtifactRecord(
            byte_count=len(report_bytes), sha256=sha256(report_bytes)
        ),
    }
    if manifest.artifacts != expected_artifacts or manifest.run_id != report.run_id:
        raise ScansorError("manifest artifact inventory or run ID mismatch")
    if report.canonical.byte_count != len(
        canonical
    ) or report.canonical.sha256 != sha256(canonical):
        raise ScansorError("report canonical artifact record mismatch")
    _ = load_canonical_npy(canonical)
    final_names = set(os.listdir(directory_fd))
    if final_names != set(RUN_FILES):
        raise ScansorError("run file set changed during verification")
    rehashed = {
        "canonical.npy": hash_run_file(
            directory_fd, "canonical.npy", MAX_CANONICAL_BYTES
        ),
        "report.json": hash_run_file(directory_fd, "report.json", MAX_CONTROL_BYTES),
        "manifest.json": hash_run_file(
            directory_fd, "manifest.json", MAX_CONTROL_BYTES
        ),
        "manifest.sha256": hash_run_file(directory_fd, "manifest.sha256", 256),
    }
    if rehashed != {
        "canonical.npy": (len(canonical), sha256(canonical)),
        "report.json": (len(report_bytes), sha256(report_bytes)),
        "manifest.json": (len(manifest_bytes), sha256(manifest_bytes)),
        "manifest.sha256": (len(sidecar), sha256(sidecar)),
    }:
        raise ScansorError("run artifacts changed during verification")
    _verify_run_entries(directory_fd, entry_identities)
    return report, canonical, entry_identities


def _verify_run_entries(
    directory_fd: int,
    entry_identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    if set(os.listdir(directory_fd)) != set(RUN_FILES):
        raise ScansorError("run file set changed during final verification")
    try:
        final_identities = {
            name: _entry_identity(
                os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            )
            for name in RUN_FILES
        }
    except OSError as error:
        raise ScansorError(
            "run artifact entry changed during final verification"
        ) from error
    if final_identities != entry_identities:
        raise ScansorError("run artifact entry changed during final verification")


def _verify_run_root(run: Path, opened_root: os.stat_result) -> None:
    try:
        current_root = os.stat(run, follow_symlinks=False)
    except OSError as error:
        raise ScansorError("run path changed during verification") from error
    if (current_root.st_dev, current_root.st_ino) != (
        opened_root.st_dev,
        opened_root.st_ino,
    ):
        raise ScansorError("run path changed during verification")


def verify_run_artifacts_fd(
    directory_fd: int,
    run: Path,
    replacement_input: Path | None,
) -> tuple[InspectionReport, bytes]:
    """Replay through an already anchored inspection-run descriptor."""
    opened_root = os.fstat(directory_fd)
    report, canonical, entry_identities = _load_run(directory_fd)
    _verify_run_root(run, opened_root)
    recorded_settings = report.settings
    source = replacement_input or Path(report.source.path)
    raw = read_regular(
        source, "replay PLY input", recorded_settings.values().max_input_bytes
    )
    replayed, replayed_canonical = build_run(
        raw,
        Path(report.source.path),
        report.source.unit,
        report.source.frame,
        recorded_settings,
        report.job,
    )
    if canonical_json(replayed) != canonical_json(report):
        raise ScansorError(
            "recomputed inspection report does not match the recorded run"
        )
    if replayed_canonical != canonical:
        raise ScansorError(
            "recomputed canonical artifact does not match the recorded run"
        )
    _verify_run_entries(directory_fd, entry_identities)
    _verify_run_root(run, opened_root)
    return report, canonical


def verify_run_artifacts(
    run: Path,
    replacement_input: Path | None,
) -> tuple[InspectionReport, bytes]:
    """Replay one anchored inspection run and return its verified artifacts."""
    directory_fd = open_run_directory(run)
    try:
        return verify_run_artifacts_fd(directory_fd, run, replacement_input)
    finally:
        os.close(directory_fd)


def verify_run(
    run: Path,
    replacement_input: Path | None,
) -> InspectionReport:
    report, _canonical = verify_run_artifacts(run, replacement_input)
    return report
