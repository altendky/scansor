from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Literal, TypedDict, cast

import pytest

import scansor.execution_runs as execution_runs_module
from scansor import cli
from scansor.cli_models import ActivationPolicy
from scansor.execution_models import AdapterInvocation
from scansor.execution_runs import verify_execution_run
from scansor.factor_models import NOMINAL_SHAPE, Problem, Variant
from scansor.mapping_runs import verify_mapping_run
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from scansor.stepped_rotational_execution import (
    ResidualJacobianCallback,
    backend_response,
)
from scansor.stepped_rotational_factors import instantiate_factors
from scansor.stepped_rotational_numpy_backend import SteppedRotationalNumpyBackend
from scansor.synthetic_fixture import FIXTURE_FRAME, prepare_synthetic_fixture
from tests.test_cli import run_cli

ReportedTermination = Literal["converged", "limit", "stopped", "failure", "unknown"]


class _ActivationMutation(TypedDict):
    activation_policy: ActivationPolicy
    ids: tuple[str, ...] | None


def write_inspection(tmp_path: Path, variant: Variant) -> Path:
    fixture = prepare_synthetic_fixture(variant)
    source = tmp_path / f"{variant}.ply"
    _ = source.write_bytes(fixture.source)
    inspection = tmp_path / f"{variant}-inspection"
    completed = run_cli(
        tmp_path,
        "inspect",
        str(source),
        str(inspection),
        "--unit",
        "m",
        "--frame",
        FIXTURE_FRAME,
    )
    assert completed.returncode == 0, completed.stderr
    return inspection


def map_config(
    path: Path,
    inspection: Path,
    output: Path,
    variant: Variant,
    *,
    minimum_region_samples: int = 3,
) -> Path:
    fixture = prepare_synthetic_fixture(variant)
    _ = path.write_text(
        "\n".join(
            (
                "[scansor]",
                f'inspection_run = "{inspection}"',
                f'output_path = "{output}"',
                f'variant = "{variant}"',
                'source_unit = "m"',
                'canonical_unit = "m"',
                f'observation_frame = "{FIXTURE_FRAME}"',
                f'model_frame = "{FIXTURE_FRAME}"',
                'transform_direction = "observation-to-model"',
                "transform_scale = 1.0",
                "rotation_row_1 = [1.0, 0.0, 0.0]",
                "rotation_row_2 = [0.0, 1.0, 0.0]",
                "rotation_row_3 = [0.0, 0.0, 1.0]",
                "translation_m = [0.0, 0.0, 0.0]",
                'translation_unit = "m"',
                "held_out_row_indices = "
                + json.dumps(list(fixture.held_out_row_indices)),
                "max_support_distance_m = 0.00025",
                "minimum_geometric_clearance_m = 0.0001",
                f"minimum_region_samples = {minimum_region_samples}",
                "rank_relative_threshold = 1e-10",
                "transform_tolerance = 1e-10",
                "transition_guard_m = 0.0005",
                "",
            )
        ),
        encoding="ascii",
    )
    return path


def publish_mapping(
    tmp_path: Path,
    variant: Variant,
    *,
    minimum_region_samples: int = 3,
) -> tuple[Path, Path]:
    inspection = write_inspection(tmp_path, variant)
    mapping = tmp_path / f"{variant}-mapping"
    config = map_config(
        tmp_path / f"{variant}-map.toml",
        inspection,
        mapping,
        variant,
        minimum_region_samples=minimum_region_samples,
    )
    completed = run_cli(tmp_path, "--config", str(config), "map")
    expected = 0 if minimum_region_samples == 3 else 3
    assert completed.returncode == expected, completed.stderr
    assert "artifact validity: valid-published" in completed.stdout
    return inspection, mapping


def fit_config(
    path: Path,
    inspection: Path,
    mapping: Path,
    output: Path,
    variant: Variant,
    problem: Problem,
    initial: tuple[float, ...],
    *,
    activation_policy: str = "all-instantiated-primary-training-v0",
    active_factor_ids: tuple[str, ...] | None = None,
    callback_limit: int = 256,
) -> Path:
    units = "metre" if problem == "fixed-pose-shape" else "metre/radian"
    lines = [
        "[scansor]",
        f'inspection_run = "{inspection}"',
        f'mapping_run = "{mapping}"',
        f'output_path = "{output}"',
        f'variant = "{variant}"',
        f'problem = "{problem}"',
        f'initial_parameter_units = "{units}"',
        "initial_values = " + json.dumps(initial),
        f'activation_policy = "{activation_policy}"',
        f"callback_limit = {callback_limit}",
    ]
    if active_factor_ids is not None:
        lines.append("active_factor_ids = " + json.dumps(active_factor_ids))
    _ = path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def rewrite_execution_artifact(run: Path, name: str, data: bytes) -> None:
    manifest_path = run / "manifest.json"
    manifest = parse_canonical_json(manifest_path.read_bytes(), "manifest", 1024 * 1024)
    _ = (run / name).write_bytes(data)
    manifest["artifacts"][name] = {
        "byte_count": len(data),
        "sha256": sha256(data),
    }
    manifest_bytes = canonical_json(manifest)
    _ = manifest_path.write_bytes(manifest_bytes)
    _ = (run / "manifest.sha256").write_bytes(
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
def test_map_and_verify_mapping_are_read_only(tmp_path: Path, variant: Variant) -> None:
    inspection, mapping = publish_mapping(tmp_path, variant)
    before = {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in (inspection, mapping)
    }
    completed = run_cli(
        tmp_path,
        "verify-mapping",
        str(mapping),
        str(inspection),
    )
    assert completed.returncode == 0, completed.stderr
    assert "mapping: accepted" in completed.stdout
    assert "artifact validity: valid-verified" in completed.stdout
    after = {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in (inspection, mapping)
    }
    assert after == before


def test_fixture_identity_oracles_are_pinned_independently() -> None:
    expected: dict[Variant, tuple[int, tuple[int, ...], str, str, str]] = {
        "axisymmetric": (
            647,
            (21,),
            "8f1d6005e676fe6c7de9477523b9cbf915460166e0cf2b9bd8b1466c09d187cd",
            "64cafdb6ba249217228e9c3b74b697b6e7b52ef328a9602190c2d7e8e36df403",
            "be31f4716bed83ff99e34a908698d173b428ff9a6543b90caff9564dc38d41b8",
        ),
        "asymmetric-datum-flat": (
            719,
            (24,),
            "9781dfb32340660dcab2e1b5ca1dd0f108b08c059bfb6bfe386af1bc435aee81",
            "bc99f9ecea67329477bb3e971dce613992d72b7725cdc9b5abdfc669147b244a",
            "7289237af08c8d57942800d4840b770dda9f848febe3d2125edfb338405e9393",
        ),
    }
    for variant, values in expected.items():
        fixture = prepare_synthetic_fixture(variant)
        assert (
            len(fixture.source),
            fixture.held_out_row_indices,
            fixture.provenance.source_sha256,
            fixture.provenance.canonical_sha256,
            fixture.provenance.content_sha256,
        ) == values


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            f'observation_frame = "{FIXTURE_FRAME}"',
            'observation_frame = "wrong-frame"',
        ),
        ("held_out_row_indices = [21]", "held_out_row_indices = [20]"),
        ('variant = "axisymmetric"', 'variant = "asymmetric-datum-flat"'),
        ('source_unit = "m"', 'source_unit = "mm"'),
        (
            'transform_direction = "observation-to-model"',
            'transform_direction = "model-to-observation"',
        ),
        ("transform_scale = 1.0", "transform_scale = 0.5"),
    ],
)
def test_map_rejects_wrong_semantic_assertions_without_publication(
    tmp_path: Path, old: str, new: str
) -> None:
    inspection = write_inspection(tmp_path, "axisymmetric")
    output = tmp_path / "mapping"
    config = map_config(tmp_path / "map.toml", inspection, output, "axisymmetric")
    contents = config.read_text(encoding="ascii")
    assert old in contents
    _ = config.write_text(contents.replace(old, new), encoding="ascii")
    completed = run_cli(tmp_path, "--config", str(config), "map")
    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    assert not output.exists()


def test_map_requires_every_threshold_and_publishes_nothing_when_one_is_omitted(
    tmp_path: Path,
) -> None:
    inspection = write_inspection(tmp_path, "axisymmetric")
    output = tmp_path / "mapping"
    config = map_config(tmp_path / "map.toml", inspection, output, "axisymmetric")
    contents = config.read_text(encoding="ascii")
    required = "transform_tolerance = 1e-10\n"
    assert required in contents
    _ = config.write_text(contents.replace(required, ""), encoding="ascii")

    completed = run_cli(tmp_path, "--config", str(config), "map")
    assert completed.returncode == 2
    assert "transform-tolerance" in completed.stderr
    assert "Traceback" not in completed.stderr
    assert not output.exists()


def test_rejected_mapping_is_published_with_exit_three_and_verifies(
    tmp_path: Path,
) -> None:
    inspection, mapping = publish_mapping(
        tmp_path, "axisymmetric", minimum_region_samples=4
    )
    assert verify_mapping_run(mapping, inspection).disposition == "rejected"
    verified = run_cli(tmp_path, "verify-mapping", str(mapping), str(inspection))
    assert verified.returncode == 0, verified.stderr
    assert "mapping: rejected" in verified.stdout


@pytest.mark.parametrize("variant", ["axisymmetric", "asymmetric-datum-flat"])
@pytest.mark.parametrize(
    "problem", ["fixed-pose-shape", "fixed-geometry-pose-correction"]
)
def test_fit_and_verify_fit_for_both_variants_and_problems(
    tmp_path: Path, variant: Variant, problem: Problem
) -> None:
    inspection, mapping = publish_mapping(tmp_path, variant)
    size = 7 if variant == "asymmetric-datum-flat" else 6
    initial = NOMINAL_SHAPE[:size] if problem == "fixed-pose-shape" else (0.0,) * 6
    execution = tmp_path / f"{variant}-{problem}-execution"
    config = fit_config(
        tmp_path / "fit.toml",
        inspection,
        mapping,
        execution,
        variant,
        problem,
        initial,
    )
    completed = run_cli(tmp_path, "--config", str(config), "fit")
    assert completed.returncode == 0, completed.stderr
    assert "execution: completed" in completed.stdout
    assert "termination: backend-converged" in completed.stdout
    assert "quality assessment: not configured" in completed.stdout
    assert "completed-not-assessed" not in completed.stdout
    assert "accepted fit" not in completed.stdout.lower()

    records = verify_execution_run(execution, inspection, mapping)
    mapping_result = verify_mapping_run(mapping, inspection)
    factor_set = instantiate_factors(mapping_result)
    held_out_ids = {
        item.observation_id for item in mapping_result.held_out_observations
    }
    assert records.held_out is not None
    assert records.selection.adapter.implementation == (
        "scansor.numpy-gauss-newton.stepped-rotational-v0"
    )
    assert records.selection.adapter.revision == "provisional-1"
    assert records.selection.active_selection.active_factor_ids == tuple(
        item.factor_id for item in factor_set.factors
    )
    assert held_out_ids.isdisjoint(
        declaration.observation_id for declaration in factor_set.declarations
    )

    before = {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in (inspection, mapping, execution)
    }
    verified = run_cli(
        tmp_path,
        "verify-fit",
        str(execution),
        str(inspection),
        str(mapping),
    )
    assert verified.returncode == 0, verified.stderr
    assert "artifact validity: valid-verified" in verified.stdout
    assert "quality assessment: not configured" in verified.stdout
    assert {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in (inspection, mapping, execution)
    } == before


def test_exact_factor_ids_and_explicit_empty_selection(tmp_path: Path) -> None:
    inspection, mapping = publish_mapping(tmp_path, "asymmetric-datum-flat")
    mapping_result = verify_mapping_run(mapping, inspection)
    factor_set = instantiate_factors(mapping_result)
    ids = tuple(item.factor_id for item in factor_set.factors)

    exact_output = tmp_path / "exact-execution"
    exact = fit_config(
        tmp_path / "exact.toml",
        inspection,
        mapping,
        exact_output,
        "asymmetric-datum-flat",
        "fixed-pose-shape",
        NOMINAL_SHAPE,
        activation_policy="exact-factor-ids",
        active_factor_ids=ids,
    )
    assert run_cli(tmp_path, "--config", str(exact), "fit").returncode == 0

    empty_output = tmp_path / "empty-execution"
    empty = fit_config(
        tmp_path / "empty.toml",
        inspection,
        mapping,
        empty_output,
        "asymmetric-datum-flat",
        "fixed-pose-shape",
        NOMINAL_SHAPE,
        activation_policy="exact-factor-ids",
        active_factor_ids=(),
    )
    completed = run_cli(tmp_path, "--config", str(empty), "fit")
    assert completed.returncode == 3, completed.stderr
    assert "execution: ineligible" in completed.stdout
    assert "termination: not-invoked" in completed.stdout
    assert "quality assessment: not performed" in completed.stdout
    assert verify_execution_run(
        empty_output, inspection, mapping
    ).result.disposition == ("ineligible")
    verified = run_cli(
        tmp_path,
        "verify-fit",
        str(empty_output),
        str(inspection),
        str(mapping),
    )
    assert verified.returncode == 0
    records = verify_execution_run(empty_output, inspection, mapping)
    assert records.manifest is not None
    assert verified.stdout.splitlines() == [
        f"execution: ineligible ({records.manifest.execution_run_id})",
        "termination: not-invoked",
        f"artifact validity: valid-verified ({empty_output.absolute()})",
        "quality assessment: not performed",
    ]


def test_limit_is_published_with_exit_three(tmp_path: Path) -> None:
    inspection, mapping = publish_mapping(tmp_path, "asymmetric-datum-flat")
    initial = tuple(value + 0.0001 for value in NOMINAL_SHAPE)
    output = tmp_path / "limited"
    config = fit_config(
        tmp_path / "limited.toml",
        inspection,
        mapping,
        output,
        "asymmetric-datum-flat",
        "fixed-pose-shape",
        initial,
        callback_limit=1,
    )
    completed = run_cli(tmp_path, "--config", str(config), "fit")
    assert completed.returncode == 3, completed.stderr
    assert "execution: completed" in completed.stdout
    assert "termination: backend-limit-reached" in completed.stdout
    assert "quality assessment: not configured" in completed.stdout
    assert output.is_dir()
    records = verify_execution_run(output, inspection, mapping)
    assert records.manifest is not None
    verified = run_cli(
        tmp_path,
        "verify-fit",
        str(output),
        str(inspection),
        str(mapping),
    )
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.splitlines() == [
        f"execution: completed ({records.manifest.execution_run_id})",
        "termination: backend-limit-reached",
        f"artifact validity: valid-verified ({output.absolute()})",
        "quality assessment: not configured",
    ]


@pytest.mark.parametrize(
    "reported", ["stopped", "failure", "unknown", "raised", "invalid", "callback"]
)
def test_stopped_failure_and_execution_failure_statuses_are_separate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reported: Literal["stopped", "failure", "unknown", "raised", "invalid", "callback"],
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "asymmetric-datum-flat")
    output = tmp_path / reported

    def terminate(
        _self: object,
        invocation: AdapterInvocation,
        callback: ResidualJacobianCallback,
    ) -> object:
        if reported == "raised":
            raise RuntimeError("test adapter failure")
        if reported == "invalid":
            return object()
        if reported == "callback":
            _ = callback((0.0,))
            raise AssertionError("rejected callback returned")
        normalized_reported = cast(ReportedTermination, reported)
        return backend_response(
            invocation,
            invocation.initial_values,
            normalized_reported,
            raw_code=f"test-{reported}",
        )

    backend_type = cast(
        type[SteppedRotationalNumpyBackend],
        vars(execution_runs_module)["SteppedRotationalNumpyBackend"],
    )
    monkeypatch.setattr(backend_type, "execute", terminate)

    class FitApp:
        @staticmethod
        def meta() -> None:
            cli.fit(
                inspection,
                mapping,
                output,
                variant="asymmetric-datum-flat",
                problem="fixed-pose-shape",
                initial_parameter_units="metre",
                initial_values=NOMINAL_SHAPE,
                activation_policy="all-instantiated-primary-training-v0",
            )

    monkeypatch.setattr(cli, "app", FitApp())
    assert cli.main() == 3
    captured = capsys.readouterr()
    expected = {
        "stopped": "backend-stopped",
        "failure": "backend-reported-failure",
        "unknown": "backend-unknown",
        "raised": "adapter-raised",
        "invalid": "invalid-response",
        "callback": "callback-rejected",
    }[reported]
    assert f"termination: {expected}" in captured.out
    expected_quality = (
        "not configured"
        if reported in {"stopped", "failure", "unknown"}
        else "not performed"
    )
    assert f"quality assessment: {expected_quality}" in captured.out
    assert output.is_dir()
    records = verify_execution_run(output, inspection, mapping)
    assert records.manifest is not None
    expected_execution = {
        "raised": "failed",
        "callback": "failed",
        "invalid": "invalid-backend-output",
    }.get(reported, "completed")
    assert captured.out.splitlines() == [
        f"execution: {expected_execution} ({records.manifest.execution_run_id})",
        f"termination: {expected}",
        f"artifact validity: valid-published ({output.absolute()})",
        f"quality assessment: {expected_quality}",
    ]
    verified = run_cli(
        tmp_path,
        "verify-fit",
        str(output),
        str(inspection),
        str(mapping),
    )
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.splitlines() == [
        f"execution: {expected_execution} ({records.manifest.execution_run_id})",
        f"termination: {expected}",
        f"artifact validity: valid-verified ({output.absolute()})",
        f"quality assessment: {expected_quality}",
    ]


def test_verify_fit_cannot_invoke_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    execution = tmp_path / "execution"
    config = fit_config(
        tmp_path / "fit.toml",
        inspection,
        mapping,
        execution,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
    )
    assert run_cli(tmp_path, "--config", str(config), "fit").returncode == 0

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verification invoked the backend")

    monkeypatch.setattr(
        execution_runs_module,
        "SteppedRotationalNumpyBackend",
        forbidden,
    )
    cli.verify_fit(execution, inspection, mapping)


def test_pipeline_precedence_unknowns_and_collection_environment(
    tmp_path: Path,
) -> None:
    inspection = write_inspection(tmp_path, "axisymmetric")
    mapping = tmp_path / "mapping"
    config = map_config(
        tmp_path / "map.toml",
        inspection,
        mapping,
        "axisymmetric",
        minimum_region_samples=5,
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "map",
        "--minimum-region-samples",
        "3",
        environment={
            "SCANSOR_MINIMUM_REGION_SAMPLES": "4",
            "SCANSOR_HELD_OUT_ROW_INDICES": json.dumps(
                list(prepare_synthetic_fixture("axisymmetric").held_out_row_indices)
            ),
        },
    )
    assert completed.returncode == 0, completed.stderr

    bad = tmp_path / "bad.toml"
    _ = bad.write_text("[scansor]\nunknown_pipeline_field = 1\n", encoding="ascii")
    failed = run_cli(tmp_path, "--config", str(bad), "map")
    assert failed.returncode == 2
    assert "unknown TOML setting" in failed.stderr
    failed = run_cli(
        tmp_path,
        "map",
        environment={"SCANSOR_UNKNOWN_PIPELINE_FIELD": "1"},
    )
    assert failed.returncode == 2
    assert "unknown SCANSOR" in failed.stderr


def test_environment_overrides_toml_for_pipeline_setting(tmp_path: Path) -> None:
    inspection = write_inspection(tmp_path, "axisymmetric")
    mapping = tmp_path / "mapping"
    config = map_config(
        tmp_path / "map.toml",
        inspection,
        mapping,
        "axisymmetric",
        minimum_region_samples=5,
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "map",
        environment={"SCANSOR_MINIMUM_REGION_SAMPLES": "3"},
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        verify_mapping_run(
            mapping, inspection
        ).request.thresholds.minimum_region_samples
        == 3
    )


def test_collection_sources_replace_in_precedence_order(tmp_path: Path) -> None:
    fixture = prepare_synthetic_fixture("axisymmetric")
    inspection = write_inspection(tmp_path, "axisymmetric")

    environment_mapping = tmp_path / "environment-mapping"
    environment_config = map_config(
        tmp_path / "environment.toml",
        inspection,
        environment_mapping,
        "axisymmetric",
    )
    contents = environment_config.read_text(encoding="ascii").replace(
        "held_out_row_indices = [21]", "held_out_row_indices = [20]"
    )
    contents = contents.replace(
        "rotation_row_1 = [1.0, 0.0, 0.0]",
        "rotation_row_1 = [0.0, 1.0, 0.0]",
    )
    _ = environment_config.write_text(contents, encoding="ascii")
    completed = run_cli(
        tmp_path,
        "--config",
        str(environment_config),
        "map",
        environment={
            "SCANSOR_HELD_OUT_ROW_INDICES": "[21]",
            "SCANSOR_ROTATION_ROW_1": "[1.0, 0.0, 0.0]",
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        verify_mapping_run(environment_mapping, inspection).request.held_out_row_indices
        == fixture.held_out_row_indices
    )
    assert verify_mapping_run(
        environment_mapping, inspection
    ).request.transform.rotation[0] == (1.0, 0.0, 0.0)

    cli_mapping = tmp_path / "cli-mapping"
    cli_config = map_config(
        tmp_path / "cli.toml", inspection, cli_mapping, "axisymmetric"
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(cli_config),
        "map",
        "--held-out-row-indices",
        "21",
        "--rotation-row-1",
        "1.0",
        "0.0",
        "0.0",
        environment={
            "SCANSOR_HELD_OUT_ROW_INDICES": "[20]",
            "SCANSOR_ROTATION_ROW_1": "[0.0, 1.0, 0.0]",
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        verify_mapping_run(cli_mapping, inspection).request.held_out_row_indices
        == fixture.held_out_row_indices
    )
    assert verify_mapping_run(cli_mapping, inspection).request.transform.rotation[
        0
    ] == (
        1.0,
        0.0,
        0.0,
    )


def test_exact_factor_id_collection_precedence(tmp_path: Path) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    factor_set = instantiate_factors(verify_mapping_run(mapping, inspection))
    ids = tuple(item.factor_id for item in factor_set.factors)

    environment_output = tmp_path / "environment-execution"
    environment_config = fit_config(
        tmp_path / "environment-fit.toml",
        inspection,
        mapping,
        environment_output,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
        activation_policy="exact-factor-ids",
        active_factor_ids=(ids[0],),
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(environment_config),
        "fit",
        environment={"SCANSOR_ACTIVE_FACTOR_IDS": json.dumps(ids)},
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        verify_execution_run(
            environment_output, inspection, mapping
        ).selection.active_selection.active_factor_ids
        == ids
    )

    cli_output = tmp_path / "cli-execution"
    cli_config = fit_config(
        tmp_path / "cli-fit.toml",
        inspection,
        mapping,
        cli_output,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
        activation_policy="exact-factor-ids",
        active_factor_ids=(ids[0],),
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(cli_config),
        "fit",
        *(token for factor_id in ids for token in ("--active-factor-ids", factor_id)),
        environment={"SCANSOR_ACTIVE_FACTOR_IDS": json.dumps((ids[0],))},
    )
    assert completed.returncode == 0, completed.stderr
    assert (
        verify_execution_run(
            cli_output, inspection, mapping
        ).selection.active_selection.active_factor_ids
        == ids
    )


@pytest.mark.parametrize("command", ["verify", "verify-mapping", "verify-fit"])
def test_verifiers_ignore_malformed_known_irrelevant_configuration(
    tmp_path: Path, command: str
) -> None:
    config = tmp_path / "irrelevant.toml"
    _ = config.write_text(
        '[scansor]\nrotation_row_1 = ["not", "numeric"]\n', encoding="ascii"
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        command,
        environment={"SCANSOR_ROTATION_ROW_1": "not-json"},
    )
    assert completed.returncode == 2
    assert "invalid TOML" not in completed.stderr
    assert "invalid JSON array" not in completed.stderr


@pytest.mark.parametrize(
    ("command", "toml_setting", "environment"),
    [
        ("inspect", 'variant = "axisymmetric"', {"SCANSOR_VARIANT": "axisymmetric"}),
        ("map", 'input_path = "irrelevant.ply"', {"SCANSOR_INPUT_PATH": "x.ply"}),
        ("fit", 'frame = "irrelevant"', {"SCANSOR_FRAME": "irrelevant"}),
    ],
)
def test_mutating_commands_reject_recognized_wrong_command_settings(
    tmp_path: Path,
    command: str,
    toml_setting: str,
    environment: dict[str, str],
) -> None:
    config = tmp_path / f"{command}.toml"
    _ = config.write_text(f"[scansor]\n{toml_setting}\n", encoding="ascii")
    completed = run_cli(tmp_path, "--config", str(config), command)
    assert completed.returncode == 2
    assert "TOML setting(s) not valid for this command" in completed.stderr
    assert "Traceback" not in completed.stderr

    completed = run_cli(tmp_path, command, environment=environment)
    assert completed.returncode == 2
    assert "SCANSOR_* variable(s) not valid for this command" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "mutation",
    [
        {"activation_policy": "all-instantiated-primary-training-v0", "ids": ()},
        {"activation_policy": "exact-factor-ids", "ids": None},
    ],
)
def test_activation_policy_conflicts_fail_without_publication(
    tmp_path: Path, mutation: _ActivationMutation
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    output = tmp_path / "execution"
    config = fit_config(
        tmp_path / "fit.toml",
        inspection,
        mapping,
        output,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
        activation_policy=mutation["activation_policy"],
        active_factor_ids=mutation["ids"],
    )
    completed = run_cli(tmp_path, "--config", str(config), "fit")
    assert completed.returncode == 2
    assert not output.exists()


def test_invalid_vectors_paths_no_overwrite_and_tampering(tmp_path: Path) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    output = tmp_path / "execution"
    bad = fit_config(
        tmp_path / "bad-vector.toml",
        inspection,
        mapping,
        output,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:5],
    )
    completed = run_cli(tmp_path, "--config", str(bad), "fit")
    assert completed.returncode == 2
    assert not output.exists()

    nested = fit_config(
        tmp_path / "nested.toml",
        inspection,
        mapping,
        mapping / "execution",
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
    )
    completed = run_cli(tmp_path, "--config", str(nested), "fit")
    assert completed.returncode == 2
    assert "input tree" in completed.stderr

    good = fit_config(
        tmp_path / "good.toml",
        inspection,
        mapping,
        output,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
    )
    assert run_cli(tmp_path, "--config", str(good), "fit").returncode == 0
    repeated = run_cli(tmp_path, "--config", str(good), "fit")
    assert repeated.returncode == 2
    assert "already exists" in repeated.stderr

    _ = (output / "manifest.sha256").write_bytes(b"0" * 64)
    verified = run_cli(
        tmp_path,
        "verify-fit",
        str(output),
        str(inspection),
        str(mapping),
    )
    assert verified.returncode == 2
    assert "sidecar" in verified.stderr


def test_missing_initial_vector_and_mapping_output_overlap_fail_before_publication(
    tmp_path: Path,
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    missing = tmp_path / "missing.toml"
    _ = missing.write_text(
        "\n".join(
            (
                "[scansor]",
                f'inspection_run = "{inspection}"',
                f'mapping_run = "{mapping}"',
                f'output_path = "{tmp_path / "execution"}"',
                'variant = "axisymmetric"',
                'problem = "fixed-pose-shape"',
                'initial_parameter_units = "metre"',
                'activation_policy = "all-instantiated-primary-training-v0"',
                "",
            )
        ),
        encoding="ascii",
    )
    completed = run_cli(tmp_path, "--config", str(missing), "fit")
    assert completed.returncode == 2
    assert not (tmp_path / "execution").exists()

    overlap = map_config(
        tmp_path / "overlap.toml",
        inspection,
        inspection / "mapping",
        "axisymmetric",
    )
    completed = run_cli(tmp_path, "--config", str(overlap), "map")
    assert completed.returncode == 2
    assert "inspection tree" in completed.stderr
    assert not (inspection / "mapping").exists()


@pytest.mark.parametrize(
    "corruption", ["malformed", "noncanonical", "oversized", "unexpected"]
)
def test_verify_mapping_rejects_malformed_noncanonical_oversized_and_extra(
    tmp_path: Path, corruption: str
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    mapping_path = mapping / "mapping.json"
    manifest_path = mapping / "manifest.json"
    sidecar_path = mapping / "manifest.sha256"
    if corruption == "unexpected":
        _ = (mapping / "unexpected").write_bytes(b"")
    elif corruption == "oversized":
        _ = mapping_path.write_bytes(b" " * (32 * 1024 * 1024 + 1))
    else:
        original = parse_canonical_json(
            mapping_path.read_bytes(), "mapping", 32 * 1024 * 1024
        )
        if corruption == "malformed":
            mapping_bytes = b'{"x":1,"x":2}\n'
        else:
            mapping_bytes = json.dumps(original, sort_keys=True).encode("ascii") + b"\n"
        _ = mapping_path.write_bytes(mapping_bytes)
        manifest = parse_canonical_json(
            manifest_path.read_bytes(), "manifest", 32 * 1024 * 1024
        )
        manifest["artifacts"]["mapping.json"] = {
            "byte_count": len(mapping_bytes),
            "sha256": sha256(mapping_bytes),
        }
        manifest_bytes = canonical_json(manifest)
        _ = manifest_path.write_bytes(manifest_bytes)
        _ = sidecar_path.write_bytes(
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
        )
    completed = run_cli(
        tmp_path,
        "verify-mapping",
        str(mapping),
        str(inspection),
    )
    assert completed.returncode == 2
    assert "ERROR:" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_verify_fit_rejects_bounded_execution_artifact_corruptions(
    tmp_path: Path,
) -> None:
    inspection, mapping = publish_mapping(tmp_path, "axisymmetric")
    execution = tmp_path / "execution"
    config = fit_config(
        tmp_path / "fit.toml",
        inspection,
        mapping,
        execution,
        "axisymmetric",
        "fixed-pose-shape",
        NOMINAL_SHAPE[:6],
    )
    assert run_cli(tmp_path, "--config", str(config), "fit").returncode == 0
    original = {item.name: item.read_bytes() for item in execution.iterdir()}
    result = original["result.json"]

    for corruption in (
        "malformed",
        "tampered",
        "noncanonical",
        "oversized",
        "inconsistent",
    ):
        for name, data in original.items():
            _ = (execution / name).write_bytes(data)
        if corruption == "malformed":
            rewrite_execution_artifact(execution, "result.json", b'{"x":1,"x":2}\n')
        elif corruption == "tampered":
            _ = (execution / "result.json").write_bytes(result + b"x")
        elif corruption == "noncanonical":
            parsed = parse_canonical_json(result, "result", 64 * 1024 * 1024)
            rewrite_execution_artifact(
                execution,
                "result.json",
                json.dumps(parsed, sort_keys=True).encode("ascii") + b"\n",
            )
        elif corruption == "oversized":
            _ = (execution / "result.json").write_bytes(b" " * (64 * 1024 * 1024 + 1))
        else:
            (execution / "held-out.json").unlink()
        completed = run_cli(
            tmp_path,
            "verify-fit",
            str(execution),
            str(inspection),
            str(mapping),
        )
        assert completed.returncode == 2, corruption
        assert "ERROR:" in completed.stderr
        assert "Traceback" not in completed.stderr


def test_post_publication_stdout_failures_preserve_publication_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection = write_inspection(tmp_path, "axisymmetric")
    fixture = prepare_synthetic_fixture("axisymmetric")
    mapping = tmp_path / "mapping"

    class MapApp:
        @staticmethod
        def meta() -> None:
            cli.map(
                inspection,
                mapping,
                variant="axisymmetric",
                source_unit="m",
                canonical_unit="m",
                observation_frame=FIXTURE_FRAME,
                model_frame=FIXTURE_FRAME,
                transform_direction="observation-to-model",
                transform_scale=1.0,
                rotation_row_1=(1.0, 0.0, 0.0),
                rotation_row_2=(0.0, 1.0, 0.0),
                rotation_row_3=(0.0, 0.0, 1.0),
                translation_m=(0.0, 0.0, 0.0),
                translation_unit="m",
                held_out_row_indices=fixture.held_out_row_indices,
                max_support_distance_m=0.00025,
                minimum_geometric_clearance_m=0.0001,
                minimum_region_samples=3,
                rank_relative_threshold=1e-10,
                transform_tolerance=1e-10,
                transition_guard_m=0.0005,
            )

    def fail_print(*_args: object, **_kwargs: object) -> None:
        raise OSError("closed stdout")

    with monkeypatch.context() as context:
        context.setattr(cli, "app", MapApp())
        context.setattr(builtins, "print", fail_print)
        assert cli.main() == 0
    assert verify_mapping_run(mapping, inspection).disposition == "accepted"

    execution = tmp_path / "execution"

    class FitApp:
        @staticmethod
        def meta() -> None:
            cli.fit(
                inspection,
                mapping,
                execution,
                variant="axisymmetric",
                problem="fixed-pose-shape",
                initial_parameter_units="metre",
                initial_values=tuple(value + 0.0001 for value in NOMINAL_SHAPE[:6]),
                activation_policy="all-instantiated-primary-training-v0",
                callback_limit=1,
            )

    with monkeypatch.context() as context:
        context.setattr(cli, "app", FitApp())
        context.setattr(builtins, "print", fail_print)
        assert cli.main() == 3
    records = verify_execution_run(execution, inspection, mapping)
    assert records.result.normalized_termination.category == "backend-limit-reached"


def test_adverse_outcome_is_normalized_to_exit_three(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AdverseApp:
        @staticmethod
        def meta() -> None:
            adverse = cast(type[BaseException], vars(cli)["_PublishedAdverseOutcome"])
            raise adverse

    monkeypatch.setattr(cli, "app", AdverseApp())
    assert cli.main() == 3


def test_help_preserves_synthetic_non_public_claim_boundary(tmp_path: Path) -> None:
    for command in ("map", "fit", "verify-mapping", "verify-fit"):
        completed = run_cli(tmp_path, command, "--help")
        assert completed.returncode == 0
        combined = (completed.stdout + completed.stderr).lower()
        assert "synthetic-only" in combined
        assert "traceback" not in combined
    root = run_cli(tmp_path, "--help")
    assert "internal, provisional, non-public" in root.stdout.lower()
    assert "synthetic-only" in root.stdout.lower()
    assert "physical accuracy" not in root.stdout.lower()
