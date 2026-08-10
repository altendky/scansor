from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

import pytest

import scansor.cli as cli
import scansor.execution_runs as execution_runs_module
from scansor.errors import ScansorError
from scansor.execution_models import AdapterInvocation
from scansor.execution_runs import verify_execution_run
from scansor.generation_runs import verify_generation_run
from scansor.mapping_models import MappingThresholds
from scansor.mapping_runs import verify_mapping_run
from scansor.stepped_rotational_execution import (
    ResidualJacobianCallback,
    backend_response,
)
from scansor.stepped_rotational_numpy_backend import SteppedRotationalNumpyBackend
from tests.test_cli import run_cli


def _run_demo(tmp_path: Path, name: str = "demo"):
    return run_cli(
        tmp_path,
        "demo-fixed-pose",
        name,
        "--seed",
        "7",
        "--noise-sigma-m",
        "0.00002",
    )


def test_demo_runs_every_stage_and_keeps_existing_runs_usable(tmp_path: Path) -> None:
    completed = _run_demo(tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "selected application-owned fixed analytic" in completed.stdout
    assert "model authoring: not performed" in completed.stdout
    assert "mapping settings: generated-fixed-pose-demo-v0" in completed.stdout
    assert (
        "initial vector: generated-fixed-pose-demo-v0 metre "
        "[r1=0.0122, r2=0.0178, r3=0.0142, s20=0.0202, s50=0.0498, "
        "s80=0.0802, datum_x=0.0162]"
    ) in completed.stdout.splitlines()
    assert "generation: verified" in completed.stdout
    assert "verification: PASS" in completed.stdout
    assert "artifact validity: valid-verified" in completed.stdout
    assert "comparison: available" in completed.stdout
    assert "parameter r1: truth=" in completed.stdout
    assert "active-factor residuals:" in completed.stdout
    assert "held-out residuals:" in completed.stdout
    assert "quality assessment: not configured" in completed.stdout
    assert "accepted fit" not in completed.stdout.lower()

    root = tmp_path / "demo"
    assert {item.name for item in root.iterdir()} == {
        "generation",
        "inspection",
        "mapping",
        "execution",
    }
    generation = verify_generation_run(root / "generation")
    mapping = verify_mapping_run(root / "mapping", root / "inspection")
    records = verify_execution_run(
        root / "execution", root / "inspection", root / "mapping"
    )
    assert mapping.request.held_out_row_indices == (
        generation.provenance.held_out_row_indices
    )
    assert mapping.request.thresholds == MappingThresholds(
        max_support_distance_m=0.00025,
        minimum_geometric_clearance_m=0.0001,
        minimum_region_samples=3,
        rank_relative_threshold=1e-10,
        transform_tolerance=1e-10,
        transition_guard_m=0.0005,
    )
    assert records.selection.initial_parameters.values == (
        0.0122,
        0.0178,
        0.0142,
        0.0202,
        0.0498,
        0.0802,
        0.0162,
    )
    assert records.selection.initial_parameters.problem == "fixed-pose-shape"
    assert records.selection.initial_parameters.variant == "asymmetric-datum-flat"

    commands = (
        ("verify-generation", root / "generation"),
        ("verify", root / "inspection"),
        ("verify-mapping", root / "mapping", root / "inspection"),
        (
            "verify-fit",
            root / "execution",
            root / "inspection",
            root / "mapping",
        ),
        (
            "compare-truth",
            root / "generation",
            root / "inspection",
            root / "mapping",
            root / "execution",
        ),
    )
    for command in commands:
        verified = run_cli(tmp_path, *(str(value) for value in command))
        assert verified.returncode == 0, verified.stderr


def test_demo_is_deterministic_for_seed_and_sigma(tmp_path: Path) -> None:
    first = _run_demo(tmp_path, "first")
    second = _run_demo(tmp_path, "second")
    assert first.returncode == second.returncode == 0
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_generation = verify_generation_run(first_root / "generation")
    second_generation = verify_generation_run(second_root / "generation")
    assert first_generation == second_generation
    first_records = verify_execution_run(
        first_root / "execution", first_root / "inspection", first_root / "mapping"
    )
    second_records = verify_execution_run(
        second_root / "execution", second_root / "inspection", second_root / "mapping"
    )
    assert (
        first_records.result.final_parameters == second_records.result.final_parameters
    )
    first_evaluation = first_records.result.final_evaluation
    second_evaluation = second_records.result.final_evaluation
    assert first_evaluation is not None
    assert second_evaluation is not None
    assert first_evaluation.raw_residuals_m == second_evaluation.raw_residuals_m
    assert first_evaluation.jacobian == second_evaluation.jacobian
    assert first_records.held_out is not None
    assert second_records.held_out is not None
    assert first_records.held_out.summary == second_records.held_out.summary
    diagnostic_prefixes = (
        "parameter ",
        "active-factor residuals:",
        "held-out residuals:",
    )
    assert tuple(
        line
        for line in first.stdout.splitlines()
        if line.startswith(diagnostic_prefixes)
    ) == tuple(
        line
        for line in second.stdout.splitlines()
        if line.startswith(diagnostic_prefixes)
    )


@pytest.mark.parametrize("partial", [False, True])
def test_demo_refuses_existing_or_partial_output_root(
    tmp_path: Path, partial: bool
) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    if partial:
        (root / "generation").mkdir()
        _ = (root / "generation" / "marker").write_text("unchanged", encoding="ascii")
    before = tuple(
        (item.relative_to(root), item.read_bytes() if item.is_file() else None)
        for item in sorted(root.rglob("*"))
    )
    completed = _run_demo(tmp_path)
    assert completed.returncode == 2
    assert "already exists; refusing to overwrite" in completed.stderr
    after = tuple(
        (item.relative_to(root), item.read_bytes() if item.is_file() else None)
        for item in sorted(root.rglob("*"))
    )
    assert after == before


def test_demo_leaves_unambiguous_partial_root_and_retry_refuses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "partial"

    def fail_inspection(*_args: object, **_kwargs: object) -> None:
        raise ScansorError("test inspection failure")

    monkeypatch.setattr(cli, "inspect", fail_inspection)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 2
    assert {item.name for item in root.iterdir()} == {"generation"}
    before = {item.name: item.read_bytes() for item in (root / "generation").iterdir()}
    assert cli.main() == 2
    assert {
        item.name: item.read_bytes() for item in (root / "generation").iterdir()
    } == before
    captured = capsys.readouterr()
    assert "test inspection failure" in captured.err
    assert "already exists; refusing to overwrite" in captured.err


def test_demo_detects_output_root_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    moved = tmp_path / "moved"
    original = cast(Callable[..., None], cli.generate_stepped_rotational)

    def replace_root(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        _ = root.rename(moved)
        root.mkdir()

    monkeypatch.setattr(cli, "generate_stepped_rotational", replace_root)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 2
    assert not tuple(root.iterdir())
    assert (moved / "generation").is_dir()
    assert "demo output root changed during orchestration" in capsys.readouterr().err


def test_demo_does_not_anchor_replacement_during_root_reservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    moved = tmp_path / "moved"
    original = cast(Callable[..., None], vars(cli)["rename_no_replace"])

    def replace_after_rename(*args: object, **kwargs: object) -> None:
        original(*args, **kwargs)
        _ = root.rename(moved)
        root.mkdir()

    monkeypatch.setattr(cli, "rename_no_replace", replace_after_rename)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 2
    assert not tuple(root.iterdir())
    assert not tuple(moved.iterdir())
    assert "demo output root changed during reservation" in capsys.readouterr().err


def test_demo_rejects_staging_replacement_before_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    moved = tmp_path / "owned-stage"
    original = cast(Callable[..., int], os.open)

    def replace_before_open(path: object, *args: object, **kwargs: object) -> int:
        if isinstance(path, str) and ".scansor-demo-stage-" in path:
            stage = tmp_path / path
            _ = stage.rename(moved)
            stage.mkdir()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_before_open)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 2
    assert moved.is_dir()
    assert not root.exists()
    assert "staging directory changed" in capsys.readouterr().err


def test_demo_continues_after_successful_publication_status_output_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "status-output"
    original = cast(
        Callable[[tuple[str, ...], Literal[0, 3]], None],
        vars(cli)["_print_published_status"],
    )
    failure_type = vars(cli)["_PublishedOutputFailure"]
    calls = 0

    def fail_once(lines: tuple[str, ...], exit_code: Literal[0, 3]) -> None:
        nonlocal calls
        original(lines, exit_code)
        calls += 1
        if calls == 1:
            raise failure_type(exit_code)

    monkeypatch.setattr(cli, "_print_published_status", fail_once)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 0
    assert {item.name for item in root.iterdir()} == {
        "generation",
        "inspection",
        "mapping",
        "execution",
    }
    _ = verify_execution_run(root / "execution", root / "inspection", root / "mapping")


@pytest.mark.parametrize("reported", ["stopped", "raised"])
def test_demo_verifies_and_compares_published_adverse_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reported: Literal["stopped", "raised"],
) -> None:
    root = tmp_path / "adverse"

    def stop(
        _self: object,
        invocation: AdapterInvocation,
        _callback: ResidualJacobianCallback,
    ) -> object:
        if reported == "raised":
            raise RuntimeError("test adapter failure")
        return backend_response(
            invocation,
            invocation.initial_values,
            "stopped",
            raw_code="test-stopped",
        )

    backend_type = cast(
        type[SteppedRotationalNumpyBackend],
        vars(execution_runs_module)["SteppedRotationalNumpyBackend"],
    )
    monkeypatch.setattr(backend_type, "execute", stop)

    class DemoApp:
        @staticmethod
        def meta() -> None:
            cli.demo_fixed_pose(root, seed=7, noise_sigma_m=0.00002)

    monkeypatch.setattr(cli, "app", DemoApp())
    assert cli.main() == 3
    records = verify_execution_run(
        root / "execution", root / "inspection", root / "mapping"
    )
    expected_disposition = (
        "completed-not-assessed" if reported == "stopped" else "execution-failed"
    )
    expected_termination = (
        "backend-stopped" if reported == "stopped" else "adapter-raised"
    )
    assert records.result.disposition == expected_disposition
    assert records.result.normalized_termination.category == expected_termination
    captured = capsys.readouterr()
    assert "artifact validity: valid-verified" in captured.out
    expected_comparison = "available" if reported == "stopped" else "unavailable"
    assert f"comparison: {expected_comparison}" in captured.out
    assert f"termination: {expected_termination}" in captured.out


def test_demo_validates_request_before_reserving_output_root(tmp_path: Path) -> None:
    completed = run_cli(
        tmp_path,
        "demo-fixed-pose",
        "invalid",
        "--seed",
        "7",
        "--noise-sigma-m",
        "0",
    )
    assert completed.returncode == 2
    assert not (tmp_path / "invalid").exists()


def test_demo_toml_log_level_is_recorded_as_toml_provenance(tmp_path: Path) -> None:
    config = tmp_path / "demo.toml"
    _ = config.write_text(
        "\n".join(
            (
                "[scansor]",
                'output_root = "configured"',
                "seed = 7",
                "noise_sigma_m = 0.00002",
                'log_level = "error"',
                "",
            )
        ),
        encoding="ascii",
    )
    completed = run_cli(tmp_path, "--config", str(config), "demo-fixed-pose")
    assert completed.returncode == 0, completed.stderr
    report = json.loads(
        (tmp_path / "configured" / "inspection" / "report.json").read_text(
            encoding="ascii"
        )
    )
    assert report["settings"]["log_level"] == {"source": "toml", "value": "error"}


def test_demo_help_states_provisional_synthetic_boundary(tmp_path: Path) -> None:
    completed = run_cli(tmp_path, "demo-fixed-pose", "--help")
    assert completed.returncode == 0
    assert "internal provisional synthetic-only" in completed.stdout
