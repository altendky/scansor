from __future__ import annotations

import os
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Literal

import cyclopts
import structlog
from cyclopts import App, Parameter
from pydantic import ValidationError

from scansor.cli_models import (
    CompareTruthJob,
    FitJob,
    GenerateJob,
    MapJob,
    VerifyFitJob,
    VerifyMappingJob,
)
from scansor.errors import ScansorError
from scansor.execution_run_models import ExecutionRunRecords
from scansor.execution_runs import create_execution_run, verify_execution_run
from scansor.factor_models import ParameterVector, Problem, Variant
from scansor.files import open_run_directory
from scansor.generation_models import GenerationRequest, PreparedGeneration
from scansor.generation_runs import (
    create_generation_run,
    verify_generation_run,
    verify_generation_run_fd,
)
from scansor.logging import configure_logging
from scansor.mapping_models import (
    InputRevision,
    MappingRequest,
    MappingThresholds,
    RigidTransform,
)
from scansor.mapping_runs import create_mapping_run, verify_mapping_run
from scansor.models import InspectJobConfig, LogLevel, Settings
from scansor.runs import (
    inspect_source,
    publish_run,
    verify_run,
    verify_run_artifacts_fd,
)
from scansor.serialization import canonical_json, sha256
from scansor.settings import (
    ResolutionContext,
    RestrictedToml,
    environment_values,
    reject_unknown_environment,
    resolve_settings,
)
from scansor.stepped_rotational_factors import instantiate_factors
from scansor.stepped_rotational_generation import generated_fixture_provenance
from scansor.synthetic_fixture import prepare_synthetic_fixture
from scansor.truth_comparison import compare_truth as compare_truth_runs

app = App(
    name="scansor",
    help=(
        "Internal, provisional, non-public, synthetic-only, fixed-topology "
        "Scansor CLI fixture."
    ),
    version="0.0.0",
)
_defaults = Settings()
_context = ResolutionContext()

_INSPECT_CONFIG = frozenset(
    {
        "frame",
        "input_path",
        "log_level",
        "max_header_bytes",
        "max_input_bytes",
        "max_vertices",
        "output_path",
        "unit",
    }
)
_VERIFY_CONFIG = frozenset({"log_level"})
_MAP_CONFIG = frozenset(
    {
        "canonical_unit",
        "held_out_row_indices",
        "generation_run",
        "inspection_run",
        "log_level",
        "max_support_distance_m",
        "minimum_geometric_clearance_m",
        "minimum_region_samples",
        "model_frame",
        "observation_frame",
        "output_path",
        "rank_relative_threshold",
        "rotation_row_1",
        "rotation_row_2",
        "rotation_row_3",
        "source_unit",
        "transform_direction",
        "transform_scale",
        "transform_tolerance",
        "transition_guard_m",
        "translation_m",
        "translation_unit",
        "variant",
    }
)
_VERIFY_MAPPING_CONFIG = frozenset({"inspection_run", "log_level", "mapping_run"})
_FIT_CONFIG = frozenset(
    {
        "activation_policy",
        "active_factor_ids",
        "callback_limit",
        "callback_trace_byte_limit",
        "initial_parameter_units",
        "initial_values",
        "inspection_run",
        "log_level",
        "mapping_run",
        "output_path",
        "problem",
        "variant",
    }
)
_VERIFY_FIT_CONFIG = frozenset(
    {"execution_run", "inspection_run", "log_level", "mapping_run"}
)
_GENERATE_CONFIG = frozenset(
    {"log_level", "noise_sigma_m", "output_path", "sampling_profile", "seed", "variant"}
)
_COMMAND_CONFIG = {
    "generate-stepped-rotational": _GENERATE_CONFIG,
    "verify-generation": _VERIFY_CONFIG,
    "inspect": _INSPECT_CONFIG,
    "verify": _VERIFY_CONFIG,
    "map": _MAP_CONFIG,
    "verify-mapping": _VERIFY_MAPPING_CONFIG,
    "fit": _FIT_CONFIG,
    "verify-fit": _VERIFY_FIT_CONFIG,
    "compare-truth": _VERIFY_CONFIG,
}
_MUTATING_COMMANDS = frozenset({"generate-stepped-rotational", "inspect", "map", "fit"})


class _PublishedAdverseOutcome(Exception):
    pass


class _PublishedOutputFailure(Exception):
    def __init__(self, exit_code: Literal[0, 3]) -> None:
        super().__init__("status output failed after publication")
        self.exit_code: Literal[0, 3] = exit_code


def _print_published_status(lines: tuple[str, ...], exit_code: Literal[0, 3]) -> None:
    try:
        print("\n".join(lines))
    except OSError as error:
        with suppress(OSError):
            print(
                f"WARNING: run was published but status output failed: {error}",
                file=sys.stderr,
            )
        raise _PublishedOutputFailure(exit_code) from error


def _resolved(
    *,
    log_level: LogLevel,
    max_header_bytes: int,
    max_input_bytes: int,
    max_vertices: int,
):
    return resolve_settings(
        _context,
        log_level=log_level,
        max_header_bytes=max_header_bytes,
        max_input_bytes=max_input_bytes,
        max_vertices=max_vertices,
    )


@app.command
def generate_stepped_rotational(
    output_path: Path,
    *,
    variant: Literal["asymmetric-datum-flat"],
    sampling_profile: Literal["guarded-grid-v1"],
    seed: int,
    noise_sigma_m: float,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Publish one internal provisional synthetic-only generated fixture."""
    configure_logging(log_level)
    job = GenerateJob(
        noise_sigma_m=noise_sigma_m,
        output_path=output_path,
        sampling_profile=sampling_profile,
        seed=seed,
        variant=variant,
    )
    prepared = create_generation_run(
        job.output_path,
        GenerationRequest(
            noise_sigma_m=job.noise_sigma_m,
            sampling_profile=job.sampling_profile,
            seed=job.seed,
            variant=job.variant,
        ),
    )
    provenance = prepared.provenance
    _print_published_status(
        (
            f"generation: published ({provenance.generation_run_id})",
            f"variant: {provenance.variant}",
            "observations: "
            + f"{provenance.point_count} (training={provenance.training_count}, "
            + f"held-out={provenance.held_out_count})",
            f"artifact validity: valid-published ({job.output_path.absolute()})",
        ),
        0,
    )


@app.command(name="verify-generation")
def verify_generation(
    generation_run: Path,
    *,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Read-only replay of an internal provisional synthetic-only generation."""
    configure_logging(log_level)
    prepared = verify_generation_run(generation_run)
    print(f"generation: verified ({prepared.provenance.generation_run_id})")
    print(f"artifact validity: valid-verified ({generation_run.absolute()})")


@app.command
def inspect(
    input_path: Path,
    output_path: Path,
    *,
    unit: Literal["m", "mm"],
    frame: str,
    log_level: LogLevel = _defaults.log_level,
    max_header_bytes: int = _defaults.max_header_bytes,
    max_input_bytes: int = _defaults.max_input_bytes,
    max_vertices: int = _defaults.max_vertices,
) -> None:
    """Inspect a bounded PLY source and publish a deterministic local run."""
    resolved = _resolved(
        log_level=log_level,
        max_header_bytes=max_header_bytes,
        max_input_bytes=max_input_bytes,
        max_vertices=max_vertices,
    )
    configure_logging(resolved.values().log_level)
    request = InspectJobConfig(
        input_path=input_path,
        output_path=output_path,
        unit=unit,
        frame=frame,
    )
    report, canonical = inspect_source(request, resolved)
    publish_run(request.output_path, report, canonical)
    structlog.get_logger().info(
        "inspection_published",
        run_id=report.run_id,
        point_count=report.inspection.point_count,
    )
    print(f"inspection: PASS ({report.run_id})")


@app.command
def verify(
    run: Path,
    *,
    input: Path | None = None,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Read-only verification and replay of an inspection run."""
    configure_logging(log_level)
    report = verify_run(run, input)
    structlog.get_logger().info("inspection_verified", run_id=report.run_id)
    print(f"verification: PASS ({report.run_id})")


def _publish_map_job(
    job: MapJob,
    generated: PreparedGeneration | None,
    generation_identity: tuple[int, int] | None,
    verify_generation_input: Callable[[], None] | None,
) -> None:
    inspection_fd = open_run_directory(job.inspection_run)
    try:
        report, canonical = verify_run_artifacts_fd(
            inspection_fd,
            job.inspection_run,
            None,
            replay_raw=generated.source if generated is not None else None,
        )
    finally:
        os.close(inspection_fd)
    fixture = prepare_synthetic_fixture(job.variant) if generated is None else None
    if report.source.unit != job.source_unit:
        raise ScansorError("source unit assertion differs from inspection provenance")
    if report.canonical.coordinate_unit != job.canonical_unit:
        raise ScansorError(
            "canonical unit assertion differs from inspection provenance"
        )
    if report.source.frame != job.observation_frame:
        raise ScansorError(
            "observation frame assertion differs from inspection provenance"
        )
    if job.model_frame != report.source.frame:
        raise ScansorError("model frame assertion differs from inspection provenance")
    if fixture is not None:
        expected_held_out = fixture.held_out_row_indices
        fixture_provenance = fixture.provenance
    else:
        assert generated is not None
        expected_held_out = generated.provenance.held_out_row_indices
        fixture_provenance = generated_fixture_provenance(generated, sha256(canonical))
    if job.held_out_row_indices != expected_held_out:
        raise ScansorError(
            "held-out row assertion differs from the designated synthetic fixture"
        )
    request = MappingRequest(
        held_out_row_indices=job.held_out_row_indices,
        input_revision=InputRevision(
            canonical_row_count=report.inspection.point_count,
            canonical_sha256=sha256(canonical),
            inspection_report_sha256=sha256(canonical_json(report)),
            inspection_run_id=report.run_id,
            observation_frame=report.source.frame,
            synthetic_fixture=fixture_provenance,
        ),
        thresholds=job.thresholds,
        transform=RigidTransform(
            direction=job.transform_direction,
            rotation=(
                job.rotation_row_1,
                job.rotation_row_2,
                job.rotation_row_3,
            ),
            scale=job.transform_scale,
            translation_m=job.translation_m,
        ),
        variant=job.variant,
    )
    result = create_mapping_run(
        job.output_path,
        job.inspection_run,
        request,
        generation_identity=generation_identity,
        generation_run=job.generation_run,
        verify_generation_input=verify_generation_input,
    )
    status = (
        f"mapping: {result.disposition} ({result.mapping_run_id})",
        f"artifact validity: valid-published ({job.output_path.absolute()})",
    )
    if result.disposition == "rejected":
        _print_published_status((*status, "quality assessment: not applicable"), 3)
        raise _PublishedAdverseOutcome
    _print_published_status(status, 0)


@app.command
def map(
    inspection_run: Path,
    output_path: Path,
    *,
    generation_run: Path | None = None,
    variant: Variant,
    source_unit: Literal["m"],
    canonical_unit: Literal["m"],
    observation_frame: str,
    model_frame: Literal["stepped-rotational-v0-synthetic-model-frame"],
    transform_direction: Literal["observation-to-model"],
    transform_scale: float,
    rotation_row_1: tuple[float, float, float],
    rotation_row_2: tuple[float, float, float],
    rotation_row_3: tuple[float, float, float],
    translation_m: tuple[float, float, float],
    translation_unit: Literal["m"],
    held_out_row_indices: tuple[int, ...],
    max_support_distance_m: float,
    minimum_geometric_clearance_m: float,
    minimum_region_samples: int,
    rank_relative_threshold: float,
    transform_tolerance: float,
    transition_guard_m: float,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Publish a strict synthetic-only stepped-rotational mapping run."""
    configure_logging(log_level)
    job = MapJob(
        canonical_unit=canonical_unit,
        held_out_row_indices=held_out_row_indices,
        generation_run=generation_run,
        inspection_run=inspection_run,
        model_frame=model_frame,
        observation_frame=observation_frame,
        output_path=output_path,
        rotation_row_1=rotation_row_1,
        rotation_row_2=rotation_row_2,
        rotation_row_3=rotation_row_3,
        source_unit=source_unit,
        thresholds=MappingThresholds(
            max_support_distance_m=max_support_distance_m,
            minimum_geometric_clearance_m=minimum_geometric_clearance_m,
            minimum_region_samples=minimum_region_samples,
            rank_relative_threshold=rank_relative_threshold,
            transform_tolerance=transform_tolerance,
            transition_guard_m=transition_guard_m,
        ),
        transform_direction=transform_direction,
        transform_scale=transform_scale,
        translation_m=translation_m,
        translation_unit=translation_unit,
        variant=variant,
    )
    if job.generation_run is None:
        _publish_map_job(job, None, None, None)
        return
    generation_run = job.generation_run
    generation_fd = open_run_directory(generation_run)
    try:
        generated = verify_generation_run_fd(generation_fd, generation_run)
        generation_stat = os.fstat(generation_fd)

        def verify_generation_input() -> None:
            replay = verify_generation_run_fd(generation_fd, generation_run)
            if replay != generated:
                raise ScansorError(
                    "generation artifacts changed during mapping publication"
                )

        _publish_map_job(
            job,
            generated,
            (generation_stat.st_dev, generation_stat.st_ino),
            verify_generation_input,
        )
    finally:
        os.close(generation_fd)


@app.command(name="verify-mapping")
def verify_mapping(
    mapping_run: Path,
    inspection_run: Path,
    *,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Read-only verification of a synthetic-only fixed-topology mapping run."""
    configure_logging(log_level)
    job = VerifyMappingJob(
        inspection_run=inspection_run,
        mapping_run=mapping_run,
    )
    result = verify_mapping_run(job.mapping_run, job.inspection_run)
    print(f"mapping: {result.disposition} ({result.mapping_run_id})")
    print(f"artifact validity: valid-verified ({job.mapping_run.absolute()})")


def _execution_status(
    records: ExecutionRunRecords, path: Path, validity: str
) -> tuple[tuple[str, ...], bool]:
    result = records.result
    manifest = records.manifest
    if result.disposition == "completed-not-assessed":
        execution = "completed"
        quality = "not configured"
    elif result.disposition == "ineligible":
        execution = "ineligible"
        quality = "not performed"
    elif result.disposition == "execution-failed":
        execution = "failed"
        quality = "not performed"
    else:
        execution = "invalid-backend-output"
        quality = "not performed"
    run_id = manifest.execution_run_id if manifest is not None else "unpublished"
    lines = (
        f"execution: {execution} ({run_id})",
        f"termination: {result.normalized_termination.category}",
        f"artifact validity: {validity} ({path.absolute()})",
        f"quality assessment: {quality}",
    )
    successful = (
        result.disposition == "completed-not-assessed"
        and result.normalized_termination.category == "backend-converged"
    )
    return lines, successful


def _print_execution_status(
    records: ExecutionRunRecords, path: Path, validity: str
) -> bool:
    lines, successful = _execution_status(records, path, validity)
    print("\n".join(lines))
    return successful


@app.command
def fit(
    inspection_run: Path,
    mapping_run: Path,
    output_path: Path,
    *,
    variant: Variant,
    problem: Problem,
    initial_parameter_units: Literal["metre", "metre/radian"],
    initial_values: tuple[float, ...],
    activation_policy: Literal[
        "all-instantiated-primary-training-v0", "exact-factor-ids"
    ],
    active_factor_ids: tuple[str, ...] | None = None,
    callback_limit: int = 256,
    callback_trace_byte_limit: int = 16 * 1024 * 1024,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Run the fixed synthetic-only NumPy backend and publish an execution run."""
    configure_logging(log_level)
    job = FitJob(
        activation_policy=activation_policy,
        active_factor_ids=active_factor_ids,
        callback_limit=callback_limit,
        callback_trace_byte_limit=callback_trace_byte_limit,
        initial_parameter_units=initial_parameter_units,
        initial_values=initial_values,
        inspection_run=inspection_run,
        mapping_run=mapping_run,
        output_path=output_path,
        problem=problem,
        variant=variant,
    )
    mapping_result = verify_mapping_run(job.mapping_run, job.inspection_run)
    if mapping_result.request.variant != job.variant:
        raise ScansorError("variant assertion differs from mapping provenance")
    if mapping_result.disposition != "accepted":
        raise ScansorError("rejected mapping cannot be used for execution")
    factor_set = instantiate_factors(mapping_result)
    active_ids = (
        tuple(factor.factor_id for factor in factor_set.factors)
        if job.activation_policy == "all-instantiated-primary-training-v0"
        else job.active_factor_ids
    )
    assert active_ids is not None
    initial = ParameterVector(
        problem=job.problem,
        units=job.initial_parameter_units,
        values=job.initial_values,
        variant=job.variant,
    )
    records = create_execution_run(
        job.output_path,
        job.inspection_run,
        job.mapping_run,
        active_ids,
        initial,
        callback_limit=job.callback_limit,
        callback_trace_byte_limit=job.callback_trace_byte_limit,
    )
    lines, successful = _execution_status(records, job.output_path, "valid-published")
    _print_published_status(lines, 0 if successful else 3)
    if not successful:
        raise _PublishedAdverseOutcome


@app.command(name="verify-fit")
def verify_fit(
    execution_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    *,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Read-only adapter-free verification of a synthetic-only execution run."""
    configure_logging(log_level)
    job = VerifyFitJob(
        execution_run=execution_run,
        inspection_run=inspection_run,
        mapping_run=mapping_run,
    )
    records = verify_execution_run(
        job.execution_run,
        job.inspection_run,
        job.mapping_run,
    )
    _ = _print_execution_status(records, job.execution_run, "valid-verified")


@app.command(name="compare-truth")
def compare_truth(
    generation_run: Path,
    inspection_run: Path,
    mapping_run: Path,
    execution_run: Path,
    *,
    log_level: LogLevel = _defaults.log_level,
) -> None:
    """Read-only internal synthetic-only comparison with nominal truth."""
    configure_logging(log_level)
    job = CompareTruthJob(
        execution_run=execution_run,
        generation_run=generation_run,
        inspection_run=inspection_run,
        mapping_run=mapping_run,
    )
    print(
        "\n".join(
            compare_truth_runs(
                job.generation_run,
                job.inspection_run,
                job.mapping_run,
                job.execution_run,
            )
        )
    )


@app.meta.default
def meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    config: Path | None = None,
) -> None:
    """Load only an explicitly selected TOML settings file."""
    global _context
    reject_unknown_environment()
    command = tokens[0] if tokens and tokens[0] in _COMMAND_CONFIG else ""
    names = _COMMAND_CONFIG.get(command, frozenset())
    mutating = command in _MUTATING_COMMANDS
    environment = environment_values(names, reject_irrelevant=mutating)
    sources: list[object] = [
        cyclopts.config.Dict(
            {"scansor": environment},
            root_keys=["scansor"],
            source="environment",
            use_commands_as_keys=False,
        )
    ]
    toml = None
    if config is not None:
        toml = RestrictedToml(
            config,
            must_exist=True,
            search_parents=False,
            root_keys=["scansor"],
            source="toml",
            use_commands_as_keys=False,
        )
        toml.allowed_names = names
        toml.reject_irrelevant = mutating
        # Force the documented source to load before command execution so its
        # accepted keys can be retained as provenance.
        loaded = toml.config
        table = loaded["scansor"]
        assert isinstance(table, dict)
        sources.append(
            cyclopts.config.Dict(
                {
                    "scansor": {
                        key: value for key, value in table.items() if key in names
                    }
                },
                root_keys=["scansor"],
                source="toml",
                use_commands_as_keys=False,
            )
        )
    app.config = sources
    _context = ResolutionContext(
        cli_tokens=tuple(tokens),
        toml_keys=(
            toml.observed_keys & names
            if toml is not None and command == "inspect"
            else frozenset()
        ),
    )
    app(tokens)


def main() -> int:
    try:
        app.meta()
        return 0
    except _PublishedAdverseOutcome:
        return 3
    except _PublishedOutputFailure as error:
        return error.exit_code
    except (OSError, RecursionError, ScansorError, ValidationError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except SystemExit as error:
        return 0 if error.code in {None, 0} else 2
