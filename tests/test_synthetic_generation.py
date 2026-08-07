from __future__ import annotations

from pathlib import Path

import pytest

from scansor.errors import ScansorError
from scansor.generation_models import GenerationRequest
from scansor.generation_runs import create_generation_run, verify_generation_run
from scansor.serialization import canonical_json, sha256
from scansor.stepped_rotational_generation import prepare_generation
from tests.test_cli import run_cli


def request(seed: int = 7) -> GenerationRequest:
    return GenerationRequest(
        noise_sigma_m=20e-6,
        sampling_profile="guarded-grid-v1",
        seed=seed,
        variant="asymmetric-datum-flat",
    )


def test_generation_is_deterministic_bounded_and_partitioned() -> None:
    first = prepare_generation(request())
    replay = prepare_generation(request())
    changed = prepare_generation(request(seed=8))

    assert first == replay
    assert first.provenance.point_count == 317
    assert first.provenance.training_count == 230
    assert first.provenance.held_out_count == 87
    assert first.source != changed.source
    assert first.provenance.generation_run_id != changed.provenance.generation_run_id
    assert tuple(row.fixture_observation_id for row in first.provenance.rows) == tuple(
        row.fixture_observation_id for row in changed.provenance.rows
    )
    assert tuple(row.role for row in first.provenance.rows) == tuple(
        row.role for row in changed.provenance.rows
    )
    assert (
        max(abs(row.normal_noise_offset_m) for row in first.ground_truth.rows) <= 80e-6
    )
    assert first.source.startswith(
        b"ply\nformat binary_little_endian 1.0\nelement vertex 317\n"
        b"property double x\nproperty double y\nproperty double z\nend_header\n"
    )
    assert "expected_element_id" not in canonical_json(first.provenance).decode("ascii")


def test_generation_identity_oracles_are_pinned_independently() -> None:
    prepared = prepare_generation(request())
    assert (
        prepared.provenance.generation_run_id,
        sha256(prepared.source),
        sha256(canonical_json(prepared.provenance)),
        sha256(canonical_json(prepared.ground_truth)),
        prepared.provenance.rows[0].fixture_observation_id,
        prepared.provenance.rows[-1].fixture_observation_id,
    ) == (
        "644647ba295b61b12bb49c1987064d9baacd51443a9283822d5b6ff09ebed9ed",
        "555ad9a8ba14c83b83e246f3dae315ef98bd96b0fa42aa229dd1c17239e23b9c",
        "bc7912892ac0a52df4e49821bd9f58e89351fe206d3011c361ee4340e5481eeb",
        "5e3605d69eb09e28b40b953ad0facff36d46cbeefb9c05eb9c73f935f94d2b91",
        "fixture-observation.3075d094307c0329a8fb664d",
        "fixture-observation.e342095976e72270461c39a6",
    )


def test_quantized_noise_stays_inside_recorded_clip() -> None:
    sigma = 2e-10
    prepared = prepare_generation(
        GenerationRequest(
            noise_sigma_m=sigma,
            sampling_profile="guarded-grid-v1",
            seed=0,
            variant="asymmetric-datum-flat",
        )
    )
    assert all(
        abs(row.normal_noise_offset_m) <= 4 * sigma
        for row in prepared.ground_truth.rows
    )


def test_generation_run_verifies_read_only_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    run = tmp_path / "generation"
    expected = create_generation_run(run, request())
    before = {
        item.name: (item.stat().st_mtime_ns, item.read_bytes())
        for item in run.iterdir()
    }
    assert verify_generation_run(run) == expected
    assert {
        item.name: (item.stat().st_mtime_ns, item.read_bytes())
        for item in run.iterdir()
    } == before

    source = run / "observations.ply"
    source.write_bytes(source.read_bytes() + b"x")
    with pytest.raises(ScansorError, match=r"manifest|replay|content"):
        verify_generation_run(run)


def test_generation_refuses_overwrite(tmp_path: Path) -> None:
    run = tmp_path / "generation"
    _ = create_generation_run(run, request())
    with pytest.raises(ScansorError, match="already exists"):
        create_generation_run(run, request())


def test_generation_cli_accepts_explicit_toml_values(tmp_path: Path) -> None:
    output = tmp_path / "generation"
    config = tmp_path / "generation.toml"
    config.write_text(
        "\n".join(
            (
                "[scansor]",
                f'output_path = "{output}"',
                'variant = "asymmetric-datum-flat"',
                'sampling_profile = "guarded-grid-v1"',
                "seed = 7",
                "noise_sigma_m = 0.00002",
                "",
            )
        ),
        encoding="ascii",
    )
    completed = run_cli(
        tmp_path,
        "--config",
        str(config),
        "generate-stepped-rotational",
    )
    assert completed.returncode == 0, completed.stderr
    assert verify_generation_run(output) == prepare_generation(request())
