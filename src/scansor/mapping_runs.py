from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from scansor import files as files_module
from scansor.errors import ScansorError
from scansor.files import (
    hash_run_file,
    open_run_directory,
    read_run_file,
    rename_no_replace,
    write_new_file,
)
from scansor.generation_models import GenerationRequest
from scansor.mapping_models import (
    ArtifactRecord,
    MappingManifest,
    MappingRequest,
    MappingResult,
)
from scansor.ply import canonical_npy, parse_ply
from scansor.runs import verify_run_artifacts_fd
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from scansor.stepped_rotational import MAX_MAPPING_ROWS, build_mapping
from scansor.stepped_rotational_generation import (
    generated_fixture_provenance,
    prepare_generation,
)
from scansor.synthetic_fixture import FIXTURE_FRAME, prepare_synthetic_fixture

MAPPING_RUN_FILES = frozenset({"manifest.json", "manifest.sha256", "mapping.json"})
MAX_MAPPING_BYTES = 32 * 1024 * 1024
_cleanup_rename_no_replace = rename_no_replace


def _mapping_artifacts(result: MappingResult) -> dict[str, bytes]:
    mapping_bytes = canonical_json(result)
    if len(mapping_bytes) > MAX_MAPPING_BYTES:
        raise ScansorError("mapping.json exceeds the mapping-run byte limit")
    manifest = MappingManifest(
        artifacts={
            "mapping.json": ArtifactRecord(
                byte_count=len(mapping_bytes), sha256=sha256(mapping_bytes)
            )
        },
        external_input=result.request.input_revision,
        mapping_run_id=result.mapping_run_id,
    )
    manifest_bytes = canonical_json(manifest)
    if len(manifest_bytes) > MAX_MAPPING_BYTES:
        raise ScansorError("mapping manifest exceeds the mapping-run byte limit")
    return {
        "mapping.json": mapping_bytes,
        "manifest.json": manifest_bytes,
        "manifest.sha256": (
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
        ),
    }


def _entry_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


publication_entry_identity = _entry_identity


def _verify_mapping_artifacts(
    directory_fd: int,
    artifacts: dict[str, bytes],
    identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    if set(os.listdir(directory_fd)) != set(MAPPING_RUN_FILES):
        raise ScansorError("mapping artifact inventory changed")
    current = {
        name: _entry_identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        for name in artifacts
    }
    if current != identities:
        raise ScansorError("mapping artifact entry changed")
    observed = {
        name: hash_run_file(directory_fd, name, len(data))
        for name, data in artifacts.items()
    }
    expected = {name: (len(data), sha256(data)) for name, data in artifacts.items()}
    if observed != expected:
        raise ScansorError("mapping artifact content changed")
    if set(os.listdir(directory_fd)) != set(MAPPING_RUN_FILES):
        raise ScansorError("mapping artifact inventory changed")
    final = {
        name: _entry_identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        for name in artifacts
    }
    if final != identities:
        raise ScansorError("mapping artifact entry changed")


def _assert_outside_inspection(
    directory_fd: int,
    inspection_identity: tuple[int, int],
    label: str = "inspection",
) -> None:
    current_fd = os.dup(directory_fd)
    try:
        while True:
            current = os.fstat(current_fd)
            current_identity = (current.st_dev, current.st_ino)
            if current_identity == inspection_identity:
                raise ScansorError(f"mapping output cannot be within its {label} tree")
            parent_fd = os.open(
                "..",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            parent = os.fstat(parent_fd)
            parent_identity = (parent.st_dev, parent.st_ino)
            if parent_identity == current_identity:
                os.close(parent_fd)
                return
            os.close(current_fd)
            current_fd = parent_fd
    finally:
        os.close(current_fd)


def _cleanup_unpublished_stage(
    parent_fd: int,
    stage_name: str,
    stage_fd: int,
    stage_identity: tuple[int, int],
    identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    quarantine_name = ""
    quarantine_identity: tuple[int, int] | None = None
    cleanup_name = ""
    cleanup_fd: int | None = None
    removed = False
    try:
        stage_path = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        if (stage_path.st_dev, stage_path.st_ino) != stage_identity:
            return
        names = set(os.listdir(stage_fd))
        if names != set(identities):
            return
        current = {
            name: _entry_identity(os.stat(name, dir_fd=stage_fd, follow_symlinks=False))
            for name in names
        }
        if current != identities or any(
            not stat.S_ISREG(metadata[2]) for metadata in current.values()
        ):
            return
        quarantine_name = f"{stage_name}.quarantine-{secrets.token_hex(8)}"
        files_module.rename_no_replace(
            parent_fd, stage_name, parent_fd, quarantine_name
        )
        quarantined = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        quarantine_identity = (quarantined.st_dev, quarantined.st_ino)
        if quarantine_identity != stage_identity:
            return
        names = set(os.listdir(stage_fd))
        if names != set(identities):
            return
        current = {
            name: _entry_identity(os.stat(name, dir_fd=stage_fd, follow_symlinks=False))
            for name in names
        }
        if current != identities:
            return
        cleanup_name = f"{stage_name}.cleanup-{secrets.token_hex(8)}"
        os.mkdir(cleanup_name, 0o700, dir_fd=parent_fd)
        cleanup_fd = os.open(
            cleanup_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        moved_identities: dict[str, tuple[int, int, int, int, int, int]] = {}
        for name in sorted(names):
            source_entry = _entry_identity(
                os.stat(name, dir_fd=stage_fd, follow_symlinks=False)
            )
            if source_entry != identities[name]:
                return
            _cleanup_rename_no_replace(stage_fd, name, cleanup_fd, name)
            moved_entry = _entry_identity(
                os.stat(name, dir_fd=cleanup_fd, follow_symlinks=False)
            )
            if moved_entry[:2] != identities[name][:2]:
                return
            moved_identities[name] = moved_entry
        for name in sorted(names):
            moved_entry = _entry_identity(
                os.stat(name, dir_fd=cleanup_fd, follow_symlinks=False)
            )
            if moved_entry != moved_identities[name]:
                return
            os.unlink(name, dir_fd=cleanup_fd)
        os.close(cleanup_fd)
        cleanup_fd = None
        os.rmdir(cleanup_name, dir_fd=parent_fd)
        cleanup_name = ""
        if os.listdir(stage_fd):
            return
        final_path = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        if (final_path.st_dev, final_path.st_ino) != stage_identity:
            return
        os.rmdir(quarantine_name, dir_fd=parent_fd)
        removed = True
    except (OSError, ScansorError):
        return
    finally:
        if cleanup_fd is not None:
            os.close(cleanup_fd)
        if quarantine_name and quarantine_identity is not None and not removed:
            _ = _restore_named_directory(
                parent_fd,
                quarantine_name,
                stage_name,
                quarantine_identity,
            )


cleanup_unpublished_stage = _cleanup_unpublished_stage


def _restore_named_directory(
    parent_fd: int,
    source_name: str,
    target_name: str,
    identity: tuple[int, int],
) -> bool:
    try:
        current = os.stat(source_name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            return False
        files_module.rename_no_replace(parent_fd, source_name, parent_fd, target_name)
        restored = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
        return (restored.st_dev, restored.st_ino) == identity
    except (OSError, ScansorError):
        return False


def _cleanup_unopened_empty_stage(
    parent_fd: int, stage_name: str, stage_identity: tuple[int, int]
) -> None:
    quarantine_name = f"{stage_name}.quarantine-{secrets.token_hex(8)}"
    quarantined = False
    quarantine_identity: tuple[int, int] | None = None
    removed = False
    try:
        current = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != stage_identity:
            return
        files_module.rename_no_replace(
            parent_fd, stage_name, parent_fd, quarantine_name
        )
        quarantined = True
        current = os.stat(quarantine_name, dir_fd=parent_fd, follow_symlinks=False)
        quarantine_identity = (current.st_dev, current.st_ino)
        if quarantine_identity != stage_identity:
            return
        os.rmdir(quarantine_name, dir_fd=parent_fd)
        removed = True
    except (OSError, ScansorError):
        return
    finally:
        if quarantined and quarantine_identity is not None and not removed:
            _ = _restore_named_directory(
                parent_fd,
                quarantine_name,
                stage_name,
                quarantine_identity,
            )


cleanup_unopened_empty_stage = _cleanup_unopened_empty_stage


def _verify_output_parent_path(output: Path, expected: os.stat_result) -> None:
    try:
        current = os.stat(output.parent, follow_symlinks=False)
    except OSError as error:
        raise ScansorError("mapping output parent changed") from error
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ScansorError("mapping output parent changed")


def _rollback_published_stage(
    parent_fd: int,
    output_name: str,
    stage_name: str,
    stage_identity: tuple[int, int],
) -> str | None:
    try:
        current = os.stat(output_name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != stage_identity:
            return None
        rollback_name = f"{stage_name}.rollback-{secrets.token_hex(8)}"
        files_module.rename_no_replace(parent_fd, output_name, parent_fd, rollback_name)
        restored = os.stat(rollback_name, dir_fd=parent_fd, follow_symlinks=False)
        restored_identity = (restored.st_dev, restored.st_ino)
        if restored_identity == stage_identity:
            return rollback_name
        _ = _restore_named_directory(
            parent_fd, rollback_name, output_name, restored_identity
        )
        return None
    except (OSError, ScansorError):
        return None


rollback_published_stage = _rollback_published_stage


def _publish_mapping_run(
    output: Path,
    result: MappingResult,
    inspection_identity: tuple[int, int],
    verify_input: Callable[[], None],
    generation_identity: tuple[int, int] | None = None,
) -> None:
    artifacts = _mapping_artifacts(result)
    output = output.absolute()
    if not output.name or output.name in {".", ".."}:
        raise ScansorError("mapping output must name a new directory")
    try:
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise ScansorError(
            "mapping output parent must be a non-symlink directory"
        ) from error
    stage_fd: int | None = None
    stage_name = ""
    stage_identity: tuple[int, int] | None = None
    identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    renamed = False
    published = False
    try:
        parent_stat = os.fstat(parent_fd)
        _assert_outside_inspection(parent_fd, inspection_identity)
        if generation_identity is not None:
            _assert_outside_inspection(parent_fd, generation_identity, "generation")
        try:
            _ = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ScansorError(f"mapping output already exists: {output}")
        stage_name = f".{output.name}.scansor-mapping-stage-{secrets.token_hex(8)}"
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_path = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        stage_identity = (stage_path.st_dev, stage_path.st_ino)
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        stage_stat = os.fstat(stage_fd)
        if (stage_stat.st_dev, stage_stat.st_ino) != stage_identity:
            raise ScansorError("mapping staging directory changed")
        for name, data in artifacts.items():
            identities[name] = _entry_identity(write_new_file(stage_fd, name, data))
        os.fsync(stage_fd)
        _verify_mapping_artifacts(stage_fd, artifacts, identities)
        _verify_output_parent_path(output, parent_stat)
        _assert_outside_inspection(parent_fd, inspection_identity)
        if generation_identity is not None:
            _assert_outside_inspection(parent_fd, generation_identity, "generation")
        verify_input()
        rename_no_replace(parent_fd, stage_name, parent_fd, output.name)
        renamed = True
        _verify_mapping_artifacts(stage_fd, artifacts, identities)
        _verify_output_parent_path(output, parent_stat)
        _assert_outside_inspection(parent_fd, inspection_identity)
        if generation_identity is not None:
            _assert_outside_inspection(parent_fd, generation_identity, "generation")
        final = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != stage_identity:
            raise ScansorError("mapping output path changed")
        _assert_outside_inspection(parent_fd, inspection_identity)
        if generation_identity is not None:
            _assert_outside_inspection(parent_fd, generation_identity, "generation")
        verify_input()
        _verify_mapping_artifacts(stage_fd, artifacts, identities)
        published = True
    except ScansorError:
        raise
    except OSError as error:
        raise ScansorError(f"mapping publication failed: {error}") from error
    finally:
        if stage_fd is not None:
            if renamed and not published and stage_identity is not None:
                rollback_name = _rollback_published_stage(
                    parent_fd,
                    output.name,
                    stage_name,
                    stage_identity,
                )
                if rollback_name is not None:
                    stage_name = rollback_name
            if not published and stage_identity is not None:
                _cleanup_unpublished_stage(
                    parent_fd,
                    stage_name,
                    stage_fd,
                    stage_identity,
                    identities,
                )
            os.close(stage_fd)
        elif not published and stage_identity is not None:
            _cleanup_unopened_empty_stage(parent_fd, stage_name, stage_identity)
        os.close(parent_fd)


def _validate_inspection_revision(
    request: MappingRequest, inspection_run: Path, inspection_fd: int
) -> tuple[bytes, bytes]:
    if request.input_revision.canonical_row_count > MAX_MAPPING_ROWS:
        raise ScansorError(
            f"mapping input exceeds the {MAX_MAPPING_ROWS:,}-row application limit"
        )
    revision = request.input_revision
    provenance = revision.synthetic_fixture
    if provenance.revision == "1":
        fixture = prepare_synthetic_fixture(request.variant)
        expected_provenance = fixture.provenance
        expected_canonical = fixture.canonical
        expected_held_out = fixture.held_out_row_indices
        expected_source = fixture.source
    else:
        generated = prepare_generation(
            GenerationRequest(
                noise_sigma_m=provenance.noise_sigma_m,
                sampling_profile=provenance.sampling_profile,
                seed=provenance.seed,
                variant=provenance.variant,
            )
        )
        parsed = parse_ply(generated.source, "m", 65_536, len(provenance.rows))
        expected_canonical = canonical_npy(parsed.canonical)
        expected_provenance = generated_fixture_provenance(
            generated, sha256(expected_canonical)
        )
        expected_held_out = generated.provenance.held_out_row_indices
        expected_source = generated.source
    inspection, canonical = verify_run_artifacts_fd(
        inspection_fd,
        inspection_run,
        None,
        replay_raw=expected_source if provenance.revision == "2" else None,
    )
    report_bytes = canonical_json(inspection)
    if (
        revision.inspection_run_id != inspection.run_id
        or revision.inspection_report_sha256 != sha256(report_bytes)
        or revision.canonical_sha256 != sha256(canonical)
        or revision.canonical_row_count != inspection.inspection.point_count
        or revision.observation_frame != inspection.source.frame
    ):
        raise ScansorError("mapping input revision does not match the inspection run")
    if (
        provenance != expected_provenance
        or canonical != expected_canonical
        or request.held_out_row_indices != expected_held_out
        or inspection.source.sha256 != provenance.source_sha256
        or inspection.source.byte_count != len(expected_source)
        or inspection.source.unit != "m"
        or inspection.source.frame != FIXTURE_FRAME
        or inspection.inspection.coordinate_source_dtype != "float64"
        or inspection.inspection.fields != ["x", "y", "z"]
        or inspection.inspection.rgb_preserved
        or inspection.inspection.normal_magnitude_bounds is not None
    ):
        raise ScansorError(
            "inspection does not match the designated synthetic fixture revision"
        )
    return report_bytes, canonical


def create_mapping_run(
    output: Path,
    inspection_run: Path,
    request: MappingRequest,
    *,
    generation_identity: tuple[int, int] | None = None,
    generation_run: Path | None = None,
    verify_generation_input: Callable[[], None] | None = None,
) -> MappingResult:
    """Validate an inspection revision, map it, and publish atomically."""
    inspection_fd = open_run_directory(inspection_run)
    try:
        inspection_stat = os.fstat(inspection_fd)
        inspection_identity = (inspection_stat.st_dev, inspection_stat.st_ino)
        output_parent_fd = os.open(
            output.absolute().parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            _assert_outside_inspection(output_parent_fd, inspection_identity)
            if generation_identity is not None:
                _assert_outside_inspection(
                    output_parent_fd, generation_identity, "generation"
                )
        finally:
            os.close(output_parent_fd)
        _report, canonical = _validate_inspection_revision(
            request, inspection_run, inspection_fd
        )
        current_inspection = os.stat(inspection_run, follow_symlinks=False)
        if (
            current_inspection.st_dev,
            current_inspection.st_ino,
        ) != inspection_identity:
            raise ScansorError("inspection run path changed before mapping publication")
        result = build_mapping(request, canonical)

        def verify_input() -> None:
            if verify_generation_input is not None:
                verify_generation_input()
            _report, current_canonical = _validate_inspection_revision(
                request, inspection_run, inspection_fd
            )
            if current_canonical != canonical:
                raise ScansorError(
                    "inspection artifacts changed during mapping publication"
                )
            if generation_identity is not None and generation_run is not None:
                current_generation = os.stat(generation_run, follow_symlinks=False)
                if (
                    current_generation.st_dev,
                    current_generation.st_ino,
                ) != generation_identity:
                    raise ScansorError(
                        "generation run path changed during mapping publication"
                    )

        _publish_mapping_run(
            output,
            result,
            inspection_identity,
            verify_input,
            generation_identity,
        )
        return result
    finally:
        os.close(inspection_fd)


def _load_mapping_run(directory_fd: int) -> tuple[MappingResult, dict[str, bytes]]:
    names = set(os.listdir(directory_fd))
    if names != set(MAPPING_RUN_FILES):
        raise ScansorError("mapping run file set mismatch")
    artifacts = {
        "mapping.json": read_run_file(directory_fd, "mapping.json", MAX_MAPPING_BYTES),
        "manifest.json": read_run_file(
            directory_fd, "manifest.json", MAX_MAPPING_BYTES
        ),
        "manifest.sha256": read_run_file(directory_fd, "manifest.sha256", 256),
    }
    mapping_bytes = artifacts["mapping.json"]
    manifest_bytes = artifacts["manifest.json"]
    sidecar = artifacts["manifest.sha256"]
    if sidecar != f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii"):
        raise ScansorError("mapping manifest sidecar mismatch")
    try:
        result = MappingResult.model_validate(
            parse_canonical_json(mapping_bytes, "mapping.json", MAX_MAPPING_BYTES)
        )
        manifest = MappingManifest.model_validate(
            parse_canonical_json(manifest_bytes, "manifest.json", MAX_MAPPING_BYTES)
        )
    except ValidationError as error:
        raise ScansorError(f"persisted mapping model is invalid: {error}") from error
    if (
        canonical_json(result) != mapping_bytes
        or canonical_json(manifest) != manifest_bytes
    ):
        raise ScansorError("persisted mapping run is not canonical")
    expected_artifact = ArtifactRecord(
        byte_count=len(mapping_bytes), sha256=sha256(mapping_bytes)
    )
    if (
        manifest.artifacts != {"mapping.json": expected_artifact}
        or manifest.external_input != result.request.input_revision
        or manifest.mapping_run_id != result.mapping_run_id
    ):
        raise ScansorError("mapping manifest inventory or revision mismatch")
    return result, artifacts


def verify_mapping_run_fd(
    directory_fd: int,
    mapping_run: Path,
    inspection_run: Path,
    inspection_fd: int,
) -> tuple[MappingResult, dict[str, bytes]]:
    """Replay mapping through already anchored mapping and inspection roots."""
    opened_root = os.fstat(directory_fd)
    opened_inspection = os.fstat(inspection_fd)
    result, artifacts = _load_mapping_run(directory_fd)
    identities = {
        name: _entry_identity(os.stat(name, dir_fd=directory_fd, follow_symlinks=False))
        for name in artifacts
    }
    _report, canonical = _validate_inspection_revision(
        result.request, inspection_run, inspection_fd
    )
    replay = build_mapping(
        MappingRequest.model_validate(result.request.model_dump()), canonical
    )
    if canonical_json(replay) != artifacts["mapping.json"]:
        raise ScansorError("recomputed mapping does not match the recorded run")
    _verify_mapping_artifacts(directory_fd, artifacts, identities)
    current_root = os.stat(mapping_run, follow_symlinks=False)
    current_inspection = os.stat(inspection_run, follow_symlinks=False)
    if not stat.S_ISDIR(current_root.st_mode) or (
        current_root.st_dev,
        current_root.st_ino,
    ) != (opened_root.st_dev, opened_root.st_ino):
        raise ScansorError("mapping run path changed during verification")
    if not stat.S_ISDIR(current_inspection.st_mode) or (
        current_inspection.st_dev,
        current_inspection.st_ino,
    ) != (opened_inspection.st_dev, opened_inspection.st_ino):
        raise ScansorError("inspection run path changed during mapping verification")
    return result, artifacts


def verify_mapping_run(mapping_run: Path, inspection_run: Path) -> MappingResult:
    directory_fd = open_run_directory(mapping_run)
    try:
        inspection_fd = open_run_directory(inspection_run)
        try:
            result, _artifacts = verify_mapping_run_fd(
                directory_fd, mapping_run, inspection_run, inspection_fd
            )
            return result
        finally:
            os.close(inspection_fd)
    finally:
        os.close(directory_fd)
