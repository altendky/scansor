from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

from pydantic import BaseModel, ValidationError

from scansor.errors import ScansorError
from scansor.files import (
    hash_run_file,
    open_run_directory,
    read_run_file,
    rename_no_replace,
    write_new_file,
)
from scansor.generation_models import (
    GENERATION_RUN_FILES,
    MAX_GENERATION_CONTROL_BYTES,
    MAX_GENERATION_PLY_BYTES,
    GenerationArtifact,
    GenerationGroundTruth,
    GenerationManifest,
    GenerationProvenance,
    GenerationRequest,
    PreparedGeneration,
)
from scansor.mapping_runs import (
    cleanup_unopened_empty_stage,
    cleanup_unpublished_stage,
    publication_entry_identity,
    rollback_published_stage,
)
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from scansor.stepped_rotational_generation import prepare_generation

_LIMITS = {
    "ground-truth.json": MAX_GENERATION_CONTROL_BYTES,
    "manifest.json": MAX_GENERATION_CONTROL_BYTES,
    "manifest.sha256": 256,
    "observations.ply": MAX_GENERATION_PLY_BYTES,
    "provenance.json": MAX_GENERATION_CONTROL_BYTES,
}


def _artifacts(prepared: PreparedGeneration) -> dict[str, bytes]:
    provenance = canonical_json(prepared.provenance)
    truth = canonical_json(prepared.ground_truth)
    primary = {
        "ground-truth.json": truth,
        "observations.ply": prepared.source,
        "provenance.json": provenance,
    }
    manifest = GenerationManifest(
        artifacts={
            name: GenerationArtifact(byte_count=len(data), sha256=sha256(data))
            for name, data in primary.items()
        },
        generation_run_id=prepared.provenance.generation_run_id,
    )
    manifest_bytes = canonical_json(manifest)
    return {
        **primary,
        "manifest.json": manifest_bytes,
        "manifest.sha256": (
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
        ),
    }


def _verify_artifacts(
    directory_fd: int,
    artifacts: dict[str, bytes],
    identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    if set(os.listdir(directory_fd)) != set(GENERATION_RUN_FILES):
        raise ScansorError("generation artifact inventory changed")
    current = {
        name: publication_entry_identity(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
        for name in artifacts
    }
    if current != identities:
        raise ScansorError("generation artifact entry changed")
    observed = {
        name: hash_run_file(directory_fd, name, _LIMITS[name]) for name in artifacts
    }
    expected = {name: (len(data), sha256(data)) for name, data in artifacts.items()}
    if observed != expected:
        raise ScansorError("generation artifact content changed")
    if set(os.listdir(directory_fd)) != set(GENERATION_RUN_FILES):
        raise ScansorError("generation artifact inventory changed")
    final = {
        name: publication_entry_identity(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
        for name in artifacts
    }
    if final != identities:
        raise ScansorError("generation artifact entry changed")


def _publish(output: Path, prepared: PreparedGeneration) -> None:
    output = output.absolute()
    if not output.name or output.name in {".", ".."}:
        raise ScansorError("generation output must name a new directory")
    artifacts = _artifacts(prepared)
    parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stage_fd: int | None = None
    stage_name = ""
    stage_identity: tuple[int, int] | None = None
    identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    renamed = False
    published = False
    try:
        parent_identity = os.fstat(parent_fd)
        try:
            _ = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ScansorError(f"generation output already exists: {output}")
        stage_name = f".{output.name}.scansor-generation-stage-{secrets.token_hex(8)}"
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_stat = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        stage_identity = (stage_stat.st_dev, stage_stat.st_ino)
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        if (os.fstat(stage_fd).st_dev, os.fstat(stage_fd).st_ino) != stage_identity:
            raise ScansorError("generation staging directory changed")
        for name, data in artifacts.items():
            identities[name] = publication_entry_identity(
                write_new_file(stage_fd, name, data)
            )
        os.fsync(stage_fd)
        _verify_artifacts(stage_fd, artifacts, identities)
        current_parent = os.stat(output.parent, follow_symlinks=False)
        if (current_parent.st_dev, current_parent.st_ino) != (
            parent_identity.st_dev,
            parent_identity.st_ino,
        ):
            raise ScansorError("generation output parent changed")
        rename_no_replace(parent_fd, stage_name, parent_fd, output.name)
        renamed = True
        _verify_artifacts(stage_fd, artifacts, identities)
        final = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != stage_identity:
            raise ScansorError("generation output path changed")
        final_parent = os.stat(output.parent, follow_symlinks=False)
        if (final_parent.st_dev, final_parent.st_ino) != (
            parent_identity.st_dev,
            parent_identity.st_ino,
        ):
            raise ScansorError("generation output parent changed")
        published = True
    except ScansorError:
        raise
    except OSError as error:
        raise ScansorError(f"generation publication failed: {error}") from error
    finally:
        if stage_fd is not None:
            if renamed and not published and stage_identity is not None:
                rollback_name = rollback_published_stage(
                    parent_fd, output.name, stage_name, stage_identity
                )
                if rollback_name is not None:
                    stage_name = rollback_name
            if not published and stage_identity is not None:
                cleanup_unpublished_stage(
                    parent_fd,
                    stage_name,
                    stage_fd,
                    stage_identity,
                    identities,
                )
            os.close(stage_fd)
        elif not published and stage_identity is not None:
            cleanup_unopened_empty_stage(parent_fd, stage_name, stage_identity)
        os.close(parent_fd)


def create_generation_run(
    output: Path, request: GenerationRequest
) -> PreparedGeneration:
    prepared = prepare_generation(request)
    _publish(output, prepared)
    return prepared


def _parse_exact[Model: BaseModel](model: type[Model], data: bytes, name: str) -> Model:
    try:
        value = model.model_validate(
            parse_canonical_json(data, name, MAX_GENERATION_CONTROL_BYTES)
        )
    except ValidationError as error:
        raise ScansorError(f"persisted generation model is invalid: {error}") from error
    if canonical_json(value) != data:
        raise ScansorError(f"persisted generation artifact is not canonical: {name}")
    return value


def verify_generation_run_fd(directory_fd: int, run: Path) -> PreparedGeneration:
    opened_root = os.fstat(directory_fd)
    names = set(os.listdir(directory_fd))
    if names != set(GENERATION_RUN_FILES):
        raise ScansorError("generation run file set mismatch")
    identities = {
        name: publication_entry_identity(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
        for name in names
    }
    artifacts = {
        name: read_run_file(directory_fd, name, _LIMITS[name]) for name in names
    }
    manifest_bytes = artifacts["manifest.json"]
    if artifacts["manifest.sha256"] != (
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    ):
        raise ScansorError("generation manifest sidecar mismatch")
    provenance = _parse_exact(
        GenerationProvenance, artifacts["provenance.json"], "provenance.json"
    )
    truth = _parse_exact(
        GenerationGroundTruth,
        artifacts["ground-truth.json"],
        "ground-truth.json",
    )
    manifest = _parse_exact(GenerationManifest, manifest_bytes, "manifest.json")
    expected_manifest = GenerationManifest(
        artifacts={
            name: GenerationArtifact(
                byte_count=len(artifacts[name]), sha256=sha256(artifacts[name])
            )
            for name in ("ground-truth.json", "observations.ply", "provenance.json")
        },
        generation_run_id=provenance.generation_run_id,
    )
    if (
        manifest != expected_manifest
        or truth.generation_run_id != provenance.generation_run_id
    ):
        raise ScansorError("generation manifest or truth provenance mismatch")
    replay = prepare_generation(
        GenerationRequest(
            noise_sigma_m=provenance.noise_sigma_m,
            sampling_profile=provenance.sampling_profile,
            seed=provenance.seed,
            variant=provenance.variant,
        )
    )
    prepared = PreparedGeneration(
        ground_truth=truth,
        provenance=provenance,
        source=artifacts["observations.ply"],
    )
    if replay != prepared:
        raise ScansorError("generation run does not replay exactly")
    _verify_artifacts(directory_fd, artifacts, identities)
    current_root = os.stat(run, follow_symlinks=False)
    if not stat.S_ISDIR(current_root.st_mode) or (
        current_root.st_dev,
        current_root.st_ino,
    ) != (opened_root.st_dev, opened_root.st_ino):
        raise ScansorError("generation run path changed during verification")
    return prepared


def verify_generation_run(run: Path) -> PreparedGeneration:
    directory_fd = open_run_directory(run)
    try:
        return verify_generation_run_fd(directory_fd, run)
    finally:
        os.close(directory_fd)
