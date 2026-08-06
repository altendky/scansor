from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from scansor.errors import ScansorError
from scansor.execution_models import (
    MAX_CALLBACK_TRACE_BYTES,
    ExecutionResult,
    HeldOutAssessment,
)
from scansor.execution_run_models import (
    ExecutionRunArtifact,
    ExecutionRunManifest,
    ExecutionRunRecords,
    ExecutionRunSelection,
    HeldOutRunReference,
    InspectionRunReference,
    MappingRunReference,
    run_content_id,
)
from scansor.factor_models import ParameterVector
from scansor.files import (
    hash_run_file,
    open_run_directory,
    read_run_file,
    rename_no_replace,
    write_new_file,
)
from scansor.mapping_models import MappingManifest, MappingResult
from scansor.mapping_runs import (
    cleanup_unopened_empty_stage,
    cleanup_unpublished_stage,
    publication_entry_identity,
    rollback_published_stage,
    verify_mapping_run_fd,
)
from scansor.runs import verify_run_artifacts_fd
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from scansor.stepped_rotational_execution import (
    MAX_EXECUTION_RESULT_BYTES,
    assess_held_out,
    create_execution_request,
    execute,
    execution_result_bytes,
    replay_execution,
)
from scansor.stepped_rotational_factors import (
    instantiate_factors,
    select_active_factors,
)
from scansor.stepped_rotational_numpy_backend import (
    NUMPY_GAUSS_NEWTON_DESCRIPTOR,
    SteppedRotationalNumpyBackend,
)

MAX_SELECTION_BYTES = 4 * 1024 * 1024
MAX_HELD_OUT_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SIDECAR_BYTES = 256
COMPLETED_FILES = frozenset(
    {
        "selection.json",
        "result.json",
        "held-out.json",
        "manifest.json",
        "manifest.sha256",
    }
)
NONCOMPLETED_FILES = frozenset(
    {"selection.json", "result.json", "manifest.json", "manifest.sha256"}
)


def _identified_selection(values: dict[str, object]) -> ExecutionRunSelection:
    provisional = ExecutionRunSelection.model_construct(
        execution_selection_id="", **cast(dict[str, Any], values)
    )
    values["execution_selection_id"] = run_content_id(
        "execution-selection", provisional, "execution_selection_id"
    )
    return ExecutionRunSelection.model_validate(values)


def run_numpy_execution(
    mapping: MappingResult,
    active_factor_ids: tuple[str, ...],
    initial_parameters: ParameterVector,
    *,
    callback_limit: int = 256,
    callback_trace_byte_limit: int = MAX_CALLBACK_TRACE_BYTES,
) -> ExecutionRunRecords:
    mapping = MappingResult.model_validate(mapping.model_dump(mode="python"))
    initial_parameters = ParameterVector.model_validate(
        initial_parameters.model_dump(mode="python")
    )
    factor_set = instantiate_factors(mapping)
    selection = select_active_factors(factor_set, active_factor_ids)
    request = create_execution_request(
        factor_set,
        selection,
        initial_parameters,
        NUMPY_GAUSS_NEWTON_DESCRIPTOR,
        callback_limit=callback_limit,
        callback_trace_byte_limit=callback_trace_byte_limit,
    )
    selection_record = _identified_selection(
        {
            "active_selection": selection,
            "adapter": NUMPY_GAUSS_NEWTON_DESCRIPTOR,
            "callback_limit": callback_limit,
            "callback_trace_byte_limit": callback_trace_byte_limit,
            "factor_set_id": factor_set.factor_set_id,
            "initial_parameters": initial_parameters,
            "mapping_run_id": mapping.mapping_run_id,
        }
    )
    result = execute(request, factor_set, selection, SteppedRotationalNumpyBackend())
    held_out = (
        assess_held_out(result, request, factor_set, selection, mapping)
        if result.disposition == "completed-not-assessed"
        else None
    )
    return ExecutionRunRecords(
        held_out=held_out,
        result=result,
        selection=selection_record,
    )


def _artifact(data: bytes) -> ExecutionRunArtifact:
    return ExecutionRunArtifact(byte_count=len(data), sha256=sha256(data))


def _manifest(
    records: ExecutionRunRecords,
    inspection_reference: InspectionRunReference,
    mapping_reference: MappingRunReference,
    factor_contract_id: str,
    artifacts: dict[str, bytes],
) -> ExecutionRunManifest:
    held_out = (
        HeldOutRunReference(
            assessment_id=records.held_out.assessment_id,
            sha256=sha256(artifacts["held-out.json"]),
            state="assessed",
        )
        if records.held_out is not None
        else HeldOutRunReference(state="not-applicable-noncompleted")
    )
    values: dict[str, object] = {
        "adapter": records.selection.adapter,
        "artifacts": {name: _artifact(data) for name, data in artifacts.items()},
        "disposition": records.result.disposition,
        "factor_contract_id": factor_contract_id,
        "factor_set_id": records.selection.factor_set_id,
        "held_out": held_out,
        "inspection": inspection_reference,
        "mapping": mapping_reference,
        "problem": records.selection.initial_parameters.problem,
        "request_id": records.result.request.request_id,
        "result_id": records.result.result_id,
        "selection": records.selection,
        "variant": records.selection.initial_parameters.variant,
    }
    provisional = ExecutionRunManifest.model_construct(
        execution_run_id="", **cast(dict[str, Any], values)
    )
    values["execution_run_id"] = run_content_id(
        "execution-run", provisional, "execution_run_id"
    )
    return ExecutionRunManifest.model_validate(values)


def _references(
    inspection: object,
    canonical: bytes,
    mapping: MappingResult,
    mapping_artifacts: dict[str, bytes],
) -> tuple[InspectionRunReference, MappingRunReference]:
    report = cast(Any, inspection)
    try:
        mapping_manifest = MappingManifest.model_validate(
            parse_canonical_json(
                mapping_artifacts["manifest.json"],
                "mapping manifest",
                MAX_MANIFEST_BYTES,
            )
        )
    except ValidationError as error:
        raise ScansorError(f"mapping manifest model is invalid: {error}") from error
    return (
        InspectionRunReference(
            canonical_sha256=sha256(canonical),
            report_sha256=sha256(canonical_json(report)),
            run_id=report.run_id,
        ),
        MappingRunReference(
            format=mapping.format,
            manifest_format=mapping_manifest.format,
            manifest_sha256=sha256(mapping_artifacts["manifest.json"]),
            mapping_sha256=sha256(mapping_artifacts["mapping.json"]),
            run_id=mapping.mapping_run_id,
        ),
    )


def _assert_outside_inputs(
    parent_fd: int, input_identities: set[tuple[int, int]]
) -> None:
    current_fd = os.dup(parent_fd)
    try:
        while True:
            current = os.fstat(current_fd)
            current_identity = (current.st_dev, current.st_ino)
            if current_identity in input_identities:
                raise ScansorError("execution output cannot be within an input tree")
            parent = os.open(
                "..", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current_fd
            )
            parent_stat = os.fstat(parent)
            if (parent_stat.st_dev, parent_stat.st_ino) == current_identity:
                os.close(parent)
                return
            os.close(current_fd)
            current_fd = parent
    finally:
        os.close(current_fd)


def _verify_artifacts(
    directory_fd: int,
    artifacts: dict[str, bytes],
    identities: dict[str, tuple[int, int, int, int, int, int]],
) -> None:
    if set(os.listdir(directory_fd)) != set(artifacts):
        raise ScansorError("execution-run artifact inventory changed")
    current = {
        name: publication_entry_identity(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
        for name in artifacts
    }
    if current != identities:
        raise ScansorError("execution-run artifact identity changed")
    observed = {
        name: hash_run_file(directory_fd, name, len(data))
        for name, data in artifacts.items()
    }
    expected = {name: (len(data), sha256(data)) for name, data in artifacts.items()}
    if observed != expected:
        raise ScansorError("execution-run artifact content changed")
    if set(os.listdir(directory_fd)) != set(artifacts):
        raise ScansorError("execution-run artifact inventory changed")
    final = {
        name: publication_entry_identity(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
        for name in artifacts
    }
    if final != identities:
        raise ScansorError("execution-run artifact identity changed")


def _verify_named_root(path: Path, opened: os.stat_result, label: str) -> None:
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ScansorError(f"{label} path changed") from error
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        raise ScansorError(f"{label} path changed")


def _open_two_roots(first: Path, second: Path) -> tuple[int, int]:
    first_fd = open_run_directory(first)
    try:
        second_fd = open_run_directory(second)
    except Exception:
        os.close(first_fd)
        raise
    return first_fd, second_fd


def _open_three_roots(first: Path, second: Path, third: Path) -> tuple[int, int, int]:
    first_fd, second_fd = _open_two_roots(first, second)
    try:
        third_fd = open_run_directory(third)
    except Exception:
        os.close(second_fd)
        os.close(first_fd)
        raise
    return first_fd, second_fd, third_fd


def _publish(
    output: Path,
    artifacts: dict[str, bytes],
    input_identities: set[tuple[int, int]],
    input_roots: tuple[tuple[Path, os.stat_result, str], ...],
    verify_inputs: Callable[[], None],
) -> None:
    output = output.absolute()
    if not output.name or output.name in {".", ".."}:
        raise ScansorError("execution output must name a new directory")
    try:
        parent_fd = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise ScansorError(
            "execution output parent must be a non-symlink directory"
        ) from error
    stage_fd: int | None = None
    stage_name = ""
    stage_identity: tuple[int, int] | None = None
    identities: dict[str, tuple[int, int, int, int, int, int]] = {}
    renamed = False
    published = False
    try:
        parent_stat = os.fstat(parent_fd)
        _assert_outside_inputs(parent_fd, input_identities)
        try:
            _ = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ScansorError(f"execution output already exists: {output}")
        stage_name = f".{output.name}.scansor-execution-stage-{secrets.token_hex(8)}"
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_stat = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        stage_identity = (stage_stat.st_dev, stage_stat.st_ino)
        stage_fd = os.open(
            stage_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        if (os.fstat(stage_fd).st_dev, os.fstat(stage_fd).st_ino) != stage_identity:
            raise ScansorError("execution staging directory changed")
        for name, data in artifacts.items():
            identities[name] = publication_entry_identity(
                write_new_file(stage_fd, name, data)
            )
        os.fsync(stage_fd)
        _verify_artifacts(stage_fd, artifacts, identities)
        _verify_named_root(output.parent, parent_stat, "execution output parent")
        for path, opened, label in input_roots:
            _verify_named_root(path, opened, label)
        verify_inputs()
        _assert_outside_inputs(parent_fd, input_identities)
        rename_no_replace(parent_fd, stage_name, parent_fd, output.name)
        renamed = True
        verify_inputs()
        _verify_artifacts(stage_fd, artifacts, identities)
        _verify_named_root(output.parent, parent_stat, "execution output parent")
        final = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (final.st_dev, final.st_ino) != stage_identity:
            raise ScansorError("execution output path changed")
        _assert_outside_inputs(parent_fd, input_identities)
        for path, opened, label in input_roots:
            _verify_named_root(path, opened, label)
        published = True
    except ScansorError:
        raise
    except OSError as error:
        raise ScansorError(f"execution publication failed: {error}") from error
    finally:
        if stage_fd is not None:
            if renamed and not published and stage_identity is not None:
                rollback = rollback_published_stage(
                    parent_fd, output.name, stage_name, stage_identity
                )
                if rollback is not None:
                    stage_name = rollback
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


def create_execution_run(
    output: Path,
    inspection_run: Path,
    mapping_run: Path,
    active_factor_ids: tuple[str, ...],
    initial_parameters: ParameterVector,
    callback_limit: int = 256,
    callback_trace_byte_limit: int = MAX_CALLBACK_TRACE_BYTES,
) -> ExecutionRunRecords:
    inspection_fd, mapping_fd = _open_two_roots(inspection_run, mapping_run)
    try:
        inspection_root = os.fstat(inspection_fd)
        mapping_root = os.fstat(mapping_fd)
        input_identities = {
            (inspection_root.st_dev, inspection_root.st_ino),
            (mapping_root.st_dev, mapping_root.st_ino),
        }
        parent_fd = os.open(
            output.absolute().parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            _assert_outside_inputs(parent_fd, input_identities)
        finally:
            os.close(parent_fd)
        inspection, canonical = verify_run_artifacts_fd(
            inspection_fd, inspection_run, None
        )
        mapping, mapping_artifacts = verify_mapping_run_fd(
            mapping_fd, mapping_run, inspection_run, inspection_fd
        )
        records = run_numpy_execution(
            mapping,
            active_factor_ids,
            initial_parameters,
            callback_limit=callback_limit,
            callback_trace_byte_limit=callback_trace_byte_limit,
        )
        selection_bytes = canonical_json(records.selection)
        result_bytes = execution_result_bytes(records.result)
        content_artifacts = {
            "selection.json": selection_bytes,
            "result.json": result_bytes,
        }
        if records.held_out is not None:
            held_out_bytes = canonical_json(records.held_out)
            if len(held_out_bytes) > MAX_HELD_OUT_BYTES:
                raise ScansorError("held-out.json exceeds its byte limit")
            content_artifacts["held-out.json"] = held_out_bytes
        if len(selection_bytes) > MAX_SELECTION_BYTES:
            raise ScansorError("selection.json exceeds its byte limit")
        inspection_reference, mapping_reference = _references(
            inspection, canonical, mapping, mapping_artifacts
        )
        factor_set = instantiate_factors(mapping)
        manifest = _manifest(
            records,
            inspection_reference,
            mapping_reference,
            factor_set.contract.contract_id,
            content_artifacts,
        )
        manifest_bytes = canonical_json(manifest)
        if len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ScansorError("execution manifest exceeds its byte limit")
        artifacts = content_artifacts | {
            "manifest.json": manifest_bytes,
            "manifest.sha256": f"{sha256(manifest_bytes)}  manifest.json\n".encode(
                "ascii"
            ),
        }

        def verify_inputs() -> None:
            final_inspection, final_canonical = verify_run_artifacts_fd(
                inspection_fd, inspection_run, None
            )
            final_mapping, final_mapping_artifacts = verify_mapping_run_fd(
                mapping_fd, mapping_run, inspection_run, inspection_fd
            )
            if (
                canonical_json(final_inspection) != canonical_json(inspection)
                or final_canonical != canonical
                or canonical_json(final_mapping) != canonical_json(mapping)
                or final_mapping_artifacts != mapping_artifacts
            ):
                raise ScansorError(
                    "execution input artifacts changed before publication"
                )

        _publish(
            output,
            artifacts,
            input_identities,
            (
                (inspection_run, inspection_root, "inspection run"),
                (mapping_run, mapping_root, "mapping run"),
            ),
            verify_inputs,
        )
        return ExecutionRunRecords(
            held_out=records.held_out,
            manifest=manifest,
            result=records.result,
            selection=records.selection,
        )
    finally:
        os.close(mapping_fd)
        os.close(inspection_fd)


def _parse_exact(model_type: Any, data: bytes, label: str, maximum: int) -> Any:
    try:
        model = model_type.model_validate(parse_canonical_json(data, label, maximum))
    except ValidationError as error:
        raise ScansorError(f"persisted {label} model is invalid: {error}") from error
    if canonical_json(model) != data:
        raise ScansorError(f"persisted {label} is not canonical")
    return model


def verify_execution_run(
    execution_run: Path,
    inspection_run: Path,
    mapping_run: Path,
) -> ExecutionRunRecords:
    execution_fd, inspection_fd, mapping_fd = _open_three_roots(
        execution_run, inspection_run, mapping_run
    )
    try:
        execution_root = os.fstat(execution_fd)
        inspection_root = os.fstat(inspection_fd)
        mapping_root = os.fstat(mapping_fd)
        names = set(os.listdir(execution_fd))
        if names != set(COMPLETED_FILES) and names != set(NONCOMPLETED_FILES):
            raise ScansorError("execution run file set mismatch")
        identities = {
            name: publication_entry_identity(
                os.stat(name, dir_fd=execution_fd, follow_symlinks=False)
            )
            for name in names
        }
        limits = {
            "selection.json": MAX_SELECTION_BYTES,
            "result.json": MAX_EXECUTION_RESULT_BYTES,
            "held-out.json": MAX_HELD_OUT_BYTES,
            "manifest.json": MAX_MANIFEST_BYTES,
            "manifest.sha256": MAX_SIDECAR_BYTES,
        }
        artifacts = {
            name: read_run_file(execution_fd, name, limits[name]) for name in names
        }
        manifest_bytes = artifacts["manifest.json"]
        if artifacts["manifest.sha256"] != (
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
        ):
            raise ScansorError("execution manifest sidecar mismatch")
        selection = _parse_exact(
            ExecutionRunSelection,
            artifacts["selection.json"],
            "selection.json",
            MAX_SELECTION_BYTES,
        )
        result = _parse_exact(
            ExecutionResult,
            artifacts["result.json"],
            "result.json",
            MAX_EXECUTION_RESULT_BYTES,
        )
        manifest = _parse_exact(
            ExecutionRunManifest,
            manifest_bytes,
            "manifest.json",
            MAX_MANIFEST_BYTES,
        )
        held_out = (
            _parse_exact(
                HeldOutAssessment,
                artifacts["held-out.json"],
                "held-out.json",
                MAX_HELD_OUT_BYTES,
            )
            if "held-out.json" in artifacts
            else None
        )
        records = ExecutionRunRecords(
            held_out=held_out,
            manifest=manifest,
            result=result,
            selection=selection,
        )
        expected_content = {
            name: _artifact(data)
            for name, data in artifacts.items()
            if name not in {"manifest.json", "manifest.sha256"}
        }
        if manifest.artifacts != expected_content:
            raise ScansorError("execution manifest artifact inventory mismatch")

        inspection, canonical = verify_run_artifacts_fd(
            inspection_fd, inspection_run, None
        )
        mapping, mapping_artifacts = verify_mapping_run_fd(
            mapping_fd, mapping_run, inspection_run, inspection_fd
        )
        factor_set = instantiate_factors(mapping)
        active_selection = select_active_factors(
            factor_set, selection.active_selection.active_factor_ids
        )
        if active_selection != selection.active_selection:
            raise ScansorError("execution selection does not reconstruct")
        request = create_execution_request(
            factor_set,
            active_selection,
            selection.initial_parameters,
            selection.adapter,
            callback_limit=selection.callback_limit,
            callback_trace_byte_limit=selection.callback_trace_byte_limit,
        )
        _ = replay_execution(result, request, factor_set, active_selection)
        expected_held_out = (
            assess_held_out(result, request, factor_set, active_selection, mapping)
            if result.disposition == "completed-not-assessed"
            else None
        )
        if expected_held_out != held_out:
            raise ScansorError("execution held-out assessment does not replay")
        inspection_reference, mapping_reference = _references(
            inspection, canonical, mapping, mapping_artifacts
        )
        expected_manifest = _manifest(
            ExecutionRunRecords(
                held_out=held_out,
                result=result,
                selection=selection,
            ),
            inspection_reference,
            mapping_reference,
            factor_set.contract.contract_id,
            {
                name: data
                for name, data in artifacts.items()
                if name not in {"manifest.json", "manifest.sha256"}
            },
        )
        if expected_manifest != manifest:
            raise ScansorError("execution manifest does not reconstruct")
        _verify_artifacts(execution_fd, artifacts, identities)
        final_inspection, final_canonical = verify_run_artifacts_fd(
            inspection_fd, inspection_run, None
        )
        final_mapping, final_mapping_artifacts = verify_mapping_run_fd(
            mapping_fd, mapping_run, inspection_run, inspection_fd
        )
        if (
            canonical_json(final_inspection) != canonical_json(inspection)
            or final_canonical != canonical
            or canonical_json(final_mapping) != canonical_json(mapping)
            or final_mapping_artifacts != mapping_artifacts
        ):
            raise ScansorError("execution input artifacts changed during verification")
        _verify_artifacts(execution_fd, artifacts, identities)
        _verify_named_root(execution_run, execution_root, "execution run")
        _verify_named_root(inspection_run, inspection_root, "inspection run")
        _verify_named_root(mapping_run, mapping_root, "mapping run")
        return records
    finally:
        os.close(mapping_fd)
        os.close(inspection_fd)
        os.close(execution_fd)
