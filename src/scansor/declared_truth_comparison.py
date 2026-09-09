from __future__ import annotations

import math
import os
import stat
from pathlib import Path

from scansor.declared_execution_run_models import ExecutionRunRecords
from scansor.declared_execution_runs import verify_execution_run
from scansor.declared_generation import generated_fixture_provenance
from scansor.declared_generation_models import (
    DeclaredGeneratedFixtureProvenance,
    PreparedGeneration,
)
from scansor.declared_generation_runs import verify_generation_run_fd
from scansor.errors import ScansorError
from scansor.files import open_run_directory
from scansor.mapping_models import MappingResult
from scansor.mapping_runs import verify_mapping_run_fd
from scansor.models import InspectionReport
from scansor.runs import verify_run_artifacts_fd
from scansor.serialization import canonical_json, sha256


def _number(value: float) -> str:
    return format(value, ".17g")


def _residual_summary(
    residuals: tuple[float, ...],
) -> tuple[int, float, float, float, float]:
    if not residuals or not all(math.isfinite(value) for value in residuals):
        raise ScansorError("active-factor residuals are empty or nonfinite")
    scale = max(abs(value) for value in residuals)
    if scale == 0.0:
        mean = 0.0
        rms = 0.0
    else:
        normalized = tuple(value / scale for value in residuals)
        mean = scale * (math.fsum(normalized) / len(normalized))
        rms = scale * math.sqrt(
            math.fsum(value * value for value in normalized) / len(normalized)
        )
    if not math.isfinite(mean) or not math.isfinite(rms):
        raise ScansorError("active-factor residual summary is nonfinite")
    return len(residuals), min(residuals), max(residuals), mean, rms


def _verify_inspection(
    inspection_fd: int,
    inspection_run: Path,
    generated: PreparedGeneration,
) -> tuple[InspectionReport, bytes]:
    try:
        return verify_run_artifacts_fd(
            inspection_fd,
            inspection_run,
            None,
            replay_raw=generated.source,
        )
    except ScansorError as error:
        raise ScansorError(f"truth comparison source replay failed: {error}") from error


def _require_stable_inputs(
    generation_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    execution_run: Path,
    generated: PreparedGeneration,
    inspection: InspectionReport,
    canonical: bytes,
    mapping: MappingResult,
    records: ExecutionRunRecords,
    descriptors: tuple[int, int, int, int],
) -> None:
    generation_fd, inspection_fd, mapping_fd, execution_fd = descriptors
    final_generated = verify_generation_run_fd(generation_fd, generation_run)
    final_inspection, final_canonical = _verify_inspection(
        inspection_fd, inspection_run, final_generated
    )
    final_mapping, _artifacts = verify_mapping_run_fd(
        mapping_fd,
        mapping_run,
        inspection_run,
        inspection_fd,
    )
    final_records = verify_execution_run(
        execution_run,
        inspection_run,
        mapping_run,
        anchored_descriptors=(execution_fd, inspection_fd, mapping_fd),
    )
    if (
        final_generated != generated
        or final_inspection != inspection
        or final_canonical != canonical
        or final_mapping != mapping
        or final_records != records
    ):
        raise ScansorError("truth comparison inputs changed during verification")


def _require_matching_provenance(
    generated: PreparedGeneration,
    inspection: InspectionReport,
    canonical: bytes,
    mapping: MappingResult,
    records: ExecutionRunRecords,
) -> None:
    generation = generated.provenance
    generation_request = generation.request
    truth = generated.ground_truth
    declaration = generation_request.declaration
    model_id = declaration.model_id
    parameter_order = tuple(
        parameter.parameter_id for parameter in declaration.parameters
    )
    element_order = tuple(element.element_id for element in declaration.elements)
    mapping_request = mapping.request
    revision = mapping_request.input_revision
    fixture = revision.synthetic_fixture
    expected_fixture = generated_fixture_provenance(generated, sha256(canonical))
    manifest = records.manifest
    if not isinstance(fixture, DeclaredGeneratedFixtureProvenance):
        raise ScansorError("mapping does not reference declared generated provenance")
    if fixture != expected_fixture:
        raise ScansorError("generation and mapping provenance do not match")
    if manifest is None:
        raise ScansorError("verified execution run lacks its manifest")
    result = records.result
    fit_request = result.request
    if (
        generation.model_id != model_id
        or truth.model_id != model_id
        or mapping.model_id != model_id
        or mapping_request.declaration != declaration
        or records.selection.model_id != model_id
        or records.selection.declaration != declaration
        or result.model_id != model_id
        or fit_request.model_id != model_id
        or fit_request.declaration != declaration
        or manifest.model_id != model_id
        or manifest.declaration != declaration
        or fixture.model_id != model_id
        or generation_request.model_id != model_id
    ):
        raise ScansorError("truth comparison model identities disagree")
    if (
        generation_request.parameter_order != parameter_order
        or truth.parameter_order != parameter_order
        or fit_request.parameter_order != parameter_order
        or generation_request.element_ids != element_order
        or truth.element_ids != element_order
    ):
        raise ScansorError("truth comparison declaration order bindings disagree")
    if (
        canonical_json(mapping_request.transform)
        != canonical_json(generation_request.transform)
        or revision.observation_frame != generation_request.source_frame
        or inspection.source.frame != generation_request.source_frame
        or inspection.source.sha256 != generation.source.sha256
        or inspection.source.sha256 != fixture.source_sha256
        or inspection.source.byte_count != generation.source.byte_count
        or inspection.source.byte_count != len(generated.source)
        or truth.source_sha256 != generation.source.sha256
        or revision.canonical_sha256 != sha256(canonical)
        or revision.canonical_row_count != len(generation.rows)
        or revision.canonical_row_count != inspection.inspection.point_count
        or mapping_request.held_out_row_indices != generation.held_out_row_indices
        or fixture.generation.generation_run_id != generation.generation_run_id
        or truth.generation_run_id != generation.generation_run_id
        or fit_request.mapping_run_id != mapping.mapping_run_id
        or records.selection.mapping_run_id != mapping.mapping_run_id
        or manifest.mapping.run_id != mapping.mapping_run_id
        or manifest.inspection.run_id != inspection.run_id
    ):
        raise ScansorError("truth comparison source or provenance bindings disagree")


def _compare_truth_anchored(
    generation_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    execution_run: Path,
    descriptors: tuple[int, int, int, int],
) -> tuple[str, ...]:
    generation_fd, inspection_fd, mapping_fd, execution_fd = descriptors
    generated = verify_generation_run_fd(generation_fd, generation_run)
    inspection, canonical = _verify_inspection(inspection_fd, inspection_run, generated)
    mapping, _artifacts = verify_mapping_run_fd(
        mapping_fd,
        mapping_run,
        inspection_run,
        inspection_fd,
    )
    records = verify_execution_run(
        execution_run,
        inspection_run,
        mapping_run,
        anchored_descriptors=(execution_fd, inspection_fd, mapping_fd),
    )
    _require_matching_provenance(generated, inspection, canonical, mapping, records)
    manifest = records.manifest
    assert manifest is not None
    result = records.result
    lines = (
        f"model: {generated.provenance.model_id}",
        f"generation: {generated.provenance.generation_run_id}",
        f"execution: {result.disposition} ({manifest.execution_run_id})",
        f"termination: {result.normalized_termination.category}",
    )
    if result.disposition != "completed-not-assessed":
        output = (
            "comparison: unavailable",
            *lines,
            "quality assessment: not performed",
        )
        _require_stable_inputs(
            generation_run,
            inspection_run,
            mapping_run,
            execution_run,
            generated,
            inspection,
            canonical,
            mapping,
            records,
            descriptors,
        )
        return output
    if result.final_parameters is None or result.final_evaluation is None:
        raise ScansorError("completed execution lacks final comparison evidence")
    truth = generated.ground_truth
    if (
        result.final_parameters.model_id != generated.provenance.model_id
        or result.final_parameters.values == ()
        or len(result.final_parameters.values) != len(truth.parameter_values)
    ):
        raise ScansorError("fit parameter dimension disagrees with generator truth")
    parameter_lines = tuple(
        f"parameter {name}: truth={_number(reference)} estimate={_number(estimate)} "
        + f"signed-error={_number(estimate - reference)} "
        + f"absolute-error={_number(abs(estimate - reference))}"
        for name, reference, estimate in zip(
            result.request.parameter_order,
            truth.parameter_values,
            result.final_parameters.values,
            strict=True,
        )
    )
    residuals = result.final_evaluation.raw_residuals_m
    count, minimum, maximum, mean, rms = _residual_summary(residuals)
    training_summary = (
        "active-factor residuals: "
        f"count={count} minimum={_number(minimum)} "
        f"maximum={_number(maximum)} mean={_number(mean)} rms={_number(rms)}"
    )
    if records.held_out is None:
        raise ScansorError("completed execution lacks held-out evidence")
    if (
        tuple(row.row_index for row in records.held_out.rows)
        != generated.provenance.held_out_row_indices
    ):
        raise ScansorError("held-out evidence does not match generation provenance")
    summary = records.held_out.summary
    if summary.count and (
        summary.minimum_raw_residual_m is None
        or summary.maximum_raw_residual_m is None
        or summary.mean_raw_residual_m is None
        or summary.root_mean_square_raw_residual_m is None
    ):
        raise ScansorError("held-out residual summary is incomplete")
    if summary.count:
        minimum = summary.minimum_raw_residual_m
        maximum = summary.maximum_raw_residual_m
        mean = summary.mean_raw_residual_m
        rms = summary.root_mean_square_raw_residual_m
        assert (
            minimum is not None
            and maximum is not None
            and mean is not None
            and rms is not None
        )
        held_out_summary = (
            "held-out residuals: "
            + f"count={summary.count} minimum={_number(minimum)} "
            + f"maximum={_number(maximum)} mean={_number(mean)} rms={_number(rms)}"
        )
    else:
        held_out_summary = "held-out residuals: count=0 unavailable"
    output = (
        "comparison: available",
        *lines,
        *parameter_lines,
        training_summary,
        held_out_summary,
        "quality assessment: not configured",
    )
    _require_stable_inputs(
        generation_run,
        inspection_run,
        mapping_run,
        execution_run,
        generated,
        inspection,
        canonical,
        mapping,
        records,
        descriptors,
    )
    return output


def _assert_roots_unchanged(
    paths: tuple[Path, Path, Path, Path],
    identities: tuple[tuple[int, int], ...],
) -> None:
    for path, identity in zip(paths, identities, strict=True):
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise ScansorError(
                "truth comparison input root changed during verification"
            ) from error
        if (
            not stat.S_ISDIR(current.st_mode)
            or (
                current.st_dev,
                current.st_ino,
            )
            != identity
        ):
            raise ScansorError(
                "truth comparison input root changed during verification"
            )


def compare_truth(
    generation_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    execution_run: Path,
) -> tuple[str, ...]:
    paths = (generation_run, inspection_run, mapping_run, execution_run)
    descriptors: list[int] = []
    try:
        for path in paths:
            descriptors.append(open_run_directory(path))
        anchored = (
            descriptors[0],
            descriptors[1],
            descriptors[2],
            descriptors[3],
        )
        identities = tuple(
            (opened.st_dev, opened.st_ino)
            for opened in (os.fstat(descriptor) for descriptor in descriptors)
        )
        _assert_roots_unchanged(paths, identities)
        output = _compare_truth_anchored(
            generation_run,
            inspection_run,
            mapping_run,
            execution_run,
            anchored,
        )
        _assert_roots_unchanged(paths, identities)
        return output
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
