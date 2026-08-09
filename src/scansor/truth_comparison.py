from __future__ import annotations

import math
import os
import stat
from pathlib import Path

from scansor.errors import ScansorError
from scansor.execution_run_models import ExecutionRunRecords
from scansor.execution_runs import verify_execution_run
from scansor.files import open_run_directory
from scansor.generation_models import PreparedGeneration
from scansor.generation_runs import verify_generation_run_fd
from scansor.mapping_models import MappingResult
from scansor.mapping_runs import verify_mapping_run_fd
from scansor.models import InspectionReport
from scansor.runs import verify_run_artifacts_fd
from scansor.serialization import sha256
from scansor.stepped_rotational_generation import generated_fixture_provenance


def _number(value: float) -> str:
    return format(value, ".17g")


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
    final_inspection, final_canonical = verify_run_artifacts_fd(
        inspection_fd,
        inspection_run,
        None,
        replay_raw=final_generated.source,
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


def _compare_truth_anchored(
    generation_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    execution_run: Path,
    descriptors: tuple[int, int, int, int],
) -> tuple[str, ...]:
    generation_fd, inspection_fd, mapping_fd, execution_fd = descriptors
    generated = verify_generation_run_fd(generation_fd, generation_run)
    inspection, canonical = verify_run_artifacts_fd(
        inspection_fd,
        inspection_run,
        None,
        replay_raw=generated.source,
    )
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
    expected = generated_fixture_provenance(generated, sha256(canonical))
    provenance = mapping.request.input_revision.synthetic_fixture
    if provenance.revision != "2" or provenance != expected:
        raise ScansorError("generation and mapping provenance do not match")
    if (
        inspection.source.sha256 != generated.provenance.source.sha256
        or inspection.source.byte_count != len(generated.source)
        or mapping.request.variant != "asymmetric-datum-flat"
        or records.result.request.problem != "fixed-pose-shape"
        or records.result.request.variant != "asymmetric-datum-flat"
    ):
        raise ScansorError("truth comparison inputs are outside the bounded contract")
    manifest = records.manifest
    if manifest is None:
        raise ScansorError("verified execution run lacks its manifest")
    result = records.result
    lines = (
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
    truth = generated.ground_truth.model_truth
    if result.final_parameters.values == () or (
        len(result.final_parameters.values) != len(truth.values)
    ):
        raise ScansorError("fit parameter dimension disagrees with generator truth")
    parameter_lines = tuple(
        f"parameter {name}: truth={_number(reference)} estimate={_number(estimate)} "
        + f"signed-error={_number(estimate - reference)} "
        + f"absolute-error={_number(abs(estimate - reference))}"
        for name, reference, estimate in zip(
            truth.parameter_order,
            truth.values,
            result.final_parameters.values,
            strict=True,
        )
    )
    residuals = result.final_evaluation.raw_residuals_m
    residual_rms = math.sqrt(
        math.fsum(value * value for value in residuals) / len(residuals)
    )
    training_summary = (
        "active-factor residuals: "
        f"count={len(residuals)} minimum={_number(min(residuals))} "
        f"maximum={_number(max(residuals))} "
        f"mean={_number(math.fsum(residuals) / len(residuals))} "
        f"rms={_number(residual_rms)}"
    )
    if records.held_out is None:
        raise ScansorError("completed execution lacks held-out evidence")
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
            + f"maximum={_number(maximum)} "
            + f"mean={_number(mean)} rms={_number(rms)}"
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

        def assert_roots_unchanged() -> None:
            for path, identity in zip(paths, identities, strict=True):
                current = os.stat(path, follow_symlinks=False)
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

        assert_roots_unchanged()
        output = _compare_truth_anchored(
            generation_run,
            inspection_run,
            mapping_run,
            execution_run,
            anchored,
        )
        assert_roots_unchanged()
        return output
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
