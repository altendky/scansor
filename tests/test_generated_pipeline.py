from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import scansor.cli as cli_module
import scansor.mapping_runs as mapping_runs_module
from scansor.errors import ScansorError
from scansor.execution_runs import verify_execution_run
from scansor.generation_runs import verify_generation_run
from scansor.mapping_models import MappingResult
from scansor.mapping_runs import verify_mapping_run
from scansor.synthetic_fixture import FIXTURE_FRAME
from tests.test_cli import run_cli


def _map_config(
    path: Path,
    generation: Path,
    inspection: Path,
    mapping: Path,
    held_out: tuple[int, ...],
) -> Path:
    path.write_text(
        "\n".join(
            (
                "[scansor]",
                f'generation_run = "{generation}"',
                f'inspection_run = "{inspection}"',
                f'output_path = "{mapping}"',
                'variant = "asymmetric-datum-flat"',
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
                "held_out_row_indices = " + json.dumps(held_out),
                "max_support_distance_m = 0.00025",
                "minimum_geometric_clearance_m = 0.0001",
                "minimum_region_samples = 3",
                "rank_relative_threshold = 1e-10",
                "transform_tolerance = 1e-10",
                "transition_guard_m = 0.0005",
                "",
            )
        ),
        encoding="ascii",
    )
    return path


def _fit_config(
    path: Path,
    inspection: Path,
    mapping: Path,
    execution: Path,
    *,
    empty_selection: bool = False,
) -> Path:
    activation = (
        (
            'activation_policy = "exact-factor-ids"',
            "active_factor_ids = []",
        )
        if empty_selection
        else ('activation_policy = "all-instantiated-primary-training-v0"',)
    )
    path.write_text(
        "\n".join(
            (
                "[scansor]",
                f'inspection_run = "{inspection}"',
                f'mapping_run = "{mapping}"',
                f'output_path = "{execution}"',
                'variant = "asymmetric-datum-flat"',
                'problem = "fixed-pose-shape"',
                'initial_parameter_units = "metre"',
                "initial_values = "
                "[0.0122, 0.0178, 0.0142, 0.0202, 0.0498, 0.0802, 0.0162]",
                *activation,
                "callback_limit = 256",
                "",
            )
        ),
        encoding="ascii",
    )
    return path


def test_complete_generated_noisy_cloud_workflow(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    generated = run_cli(
        tmp_path,
        "generate-stepped-rotational",
        str(generation),
        "--variant",
        "asymmetric-datum-flat",
        "--sampling-profile",
        "guarded-grid-v1",
        "--seed",
        "7",
        "--noise-sigma-m",
        "0.00002",
    )
    assert generated.returncode == 0, generated.stderr
    fixture = verify_generation_run(generation)
    verified_generation = run_cli(tmp_path, "verify-generation", str(generation))
    assert verified_generation.returncode == 0, verified_generation.stderr

    inspection = tmp_path / "inspection"
    inspected = run_cli(
        tmp_path,
        "inspect",
        str(generation / "observations.ply"),
        str(inspection),
        "--unit",
        "m",
        "--frame",
        FIXTURE_FRAME,
    )
    assert inspected.returncode == 0, inspected.stderr

    relocated_generation = tmp_path / "relocated-generation"
    generation.rename(relocated_generation)
    generation = relocated_generation
    mapping = tmp_path / "mapping"
    map_config = _map_config(
        tmp_path / "map.toml",
        generation,
        inspection,
        mapping,
        fixture.provenance.held_out_row_indices,
    )
    mapped = run_cli(tmp_path, "--config", str(map_config), "map")
    assert mapped.returncode == 0, mapped.stderr
    mapping_result = verify_mapping_run(mapping, inspection)
    assert mapping_result.disposition == "accepted"
    generated_provenance = mapping_result.request.input_revision.synthetic_fixture
    assert generated_provenance.revision == "2"
    assert tuple(
        row.fixture_observation_id for row in generated_provenance.rows
    ) == tuple(row.fixture_observation_id for row in fixture.provenance.rows)
    mapped_observations = (
        *mapping_result.observations,
        *mapping_result.held_out_observations,
    )
    paired_ids = tuple(
        (
            item.observation_id,
            generated_provenance.rows[item.row_index].fixture_observation_id,
        )
        for item in mapped_observations
    )
    assert len(paired_ids) == len(set(paired_ids))
    held_out_ids = {
        item.observation_id for item in mapping_result.held_out_observations
    }
    assert held_out_ids.isdisjoint(
        item.observation_id for item in mapping_result.mappings
    )
    malformed = mapping_result.model_dump(mode="python")
    malformed["observations"][0]["row_index"] = 999
    with pytest.raises(ValidationError, match="row index"):
        MappingResult.model_validate(malformed)
    assert verify_mapping_run(mapping, inspection) == mapping_result

    execution = tmp_path / "execution"
    fit_config = _fit_config(tmp_path / "fit.toml", inspection, mapping, execution)
    fitted = run_cli(tmp_path, "--config", str(fit_config), "fit")
    assert fitted.returncode == 0, fitted.stderr
    records = verify_execution_run(execution, inspection, mapping)
    assert records.result.disposition == "completed-not-assessed"

    roots = (generation, inspection, mapping, execution)
    before = {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in roots
    }
    compared = run_cli(
        tmp_path,
        "compare-truth",
        str(generation),
        str(inspection),
        str(mapping),
        str(execution),
    )
    assert compared.returncode == 0, compared.stderr
    assert "comparison: available" in compared.stdout
    assert "parameter r1:" in compared.stdout
    assert "quality assessment: not configured" in compared.stdout
    assert "accepted fit" not in compared.stdout.lower()
    assert {
        root: {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in root.iterdir()
        }
        for root in roots
    } == before

    ineligible = tmp_path / "ineligible"
    ineligible_config = _fit_config(
        tmp_path / "ineligible.toml",
        inspection,
        mapping,
        ineligible,
        empty_selection=True,
    )
    assert run_cli(tmp_path, "--config", str(ineligible_config), "fit").returncode == 3
    unavailable = run_cli(
        tmp_path,
        "compare-truth",
        str(generation),
        str(inspection),
        str(mapping),
        str(ineligible),
    )
    assert unavailable.returncode == 0, unavailable.stderr
    assert "comparison: unavailable" in unavailable.stdout
    assert "quality assessment: not performed" in unavailable.stdout


def test_generated_map_output_cannot_be_inside_generation(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    generated = run_cli(
        tmp_path,
        "generate-stepped-rotational",
        str(generation),
        "--variant",
        "asymmetric-datum-flat",
        "--sampling-profile",
        "guarded-grid-v1",
        "--seed",
        "7",
        "--noise-sigma-m",
        "0.00002",
    )
    assert generated.returncode == 0
    fixture = verify_generation_run(generation)
    inspection = tmp_path / "inspection"
    assert (
        run_cli(
            tmp_path,
            "inspect",
            str(generation / "observations.ply"),
            str(inspection),
            "--unit",
            "m",
            "--frame",
            FIXTURE_FRAME,
        ).returncode
        == 0
    )
    config = _map_config(
        tmp_path / "map.toml",
        generation,
        inspection,
        generation / "mapping",
        fixture.provenance.held_out_row_indices,
    )
    completed = run_cli(tmp_path, "--config", str(config), "map")
    assert completed.returncode == 2
    assert "within its generation tree" in completed.stderr


def test_mapping_publication_revalidates_generation_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generation = tmp_path / "generation"
    assert (
        run_cli(
            tmp_path,
            "generate-stepped-rotational",
            str(generation),
            "--variant",
            "asymmetric-datum-flat",
            "--sampling-profile",
            "guarded-grid-v1",
            "--seed",
            "7",
            "--noise-sigma-m",
            "0.00002",
        ).returncode
        == 0
    )
    fixture = verify_generation_run(generation)
    inspection = tmp_path / "inspection"
    assert (
        run_cli(
            tmp_path,
            "inspect",
            str(generation / "observations.ply"),
            str(inspection),
            "--unit",
            "m",
            "--frame",
            FIXTURE_FRAME,
        ).returncode
        == 0
    )
    original = mapping_runs_module._verify_mapping_artifacts
    calls = 0

    def mutate_after_staging(*args, **kwargs) -> None:
        nonlocal calls
        original(*args, **kwargs)
        calls += 1
        if calls == 1:
            source = generation / "observations.ply"
            source.write_bytes(source.read_bytes() + b"x")

    monkeypatch.setattr(
        mapping_runs_module, "_verify_mapping_artifacts", mutate_after_staging
    )
    output = tmp_path / "mapping"
    with pytest.raises(ScansorError, match=r"generation|artifact|manifest"):
        cli_module.map(
            inspection,
            output,
            generation_run=generation,
            variant="asymmetric-datum-flat",
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
            held_out_row_indices=fixture.provenance.held_out_row_indices,
            max_support_distance_m=0.00025,
            minimum_geometric_clearance_m=0.0001,
            minimum_region_samples=3,
            rank_relative_threshold=1e-10,
            transform_tolerance=1e-10,
            transition_guard_m=0.0005,
        )
    assert calls == 1
    assert not output.exists()
