from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import scansor.declared_generation_runs as generation_runs_module
from scansor.declared_generation import create_generation_request, prepare_generation
from scansor.declared_generation_models import GenerationRequest
from scansor.declared_generation_runs import (
    create_generation_run,
    verify_generation_run,
)
from scansor.errors import ScansorError
from scansor.files import rename_no_replace
from scansor.serialization import canonical_json, sha256

FIXTURE_IDS = ("asymmetric-stepped-v1", "coaxial-tube-v1")


def _request(
    fixture_id: str = "asymmetric-stepped-v1", seed: int = 7
) -> GenerationRequest:
    return create_generation_request(
        fixture_id,
        seed=seed,
        noise_sigma_m=20e-6,
    )


def _json_object(data: bytes) -> dict[str, Any]:
    value = json.loads(data)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _rewrite_primary(run: Path, name: str, data: bytes) -> None:
    _ = (run / name).write_bytes(data)
    manifest = _json_object((run / "manifest.json").read_bytes())
    manifest_artifacts = cast(dict[str, Any], manifest["artifacts"])
    manifest_artifacts[name] = {
        "byte_count": len(data),
        "sha256": sha256(data),
    }
    manifest_bytes = canonical_json(manifest)
    _ = (run / "manifest.json").write_bytes(manifest_bytes)
    _ = (run / "manifest.sha256").write_bytes(
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_generation_publication_replays_deterministically(
    tmp_path: Path, fixture_id: str
) -> None:
    request = _request(fixture_id)
    prepared = prepare_generation(request)
    changed = prepare_generation(_request(fixture_id, seed=8))
    assert prepare_generation(request) == prepared
    assert changed.source != prepared.source
    assert changed.provenance.generation_run_id != prepared.provenance.generation_run_id

    run = tmp_path / "generation"
    assert create_generation_run(run, request) == prepared
    assert verify_generation_run(run) == prepared
    assert set(item.name for item in run.iterdir()) == {
        "ground-truth.json",
        "manifest.json",
        "manifest.sha256",
        "observations.ply",
        "provenance.json",
    }
    assert prepared.provenance.request == request
    assert prepared.ground_truth.parameter_order == request.parameter_order
    assert prepared.ground_truth.element_ids == request.element_ids


def test_relocated_generation_verifies_without_writes(tmp_path: Path) -> None:
    original = tmp_path / "original"
    expected = create_generation_run(original, _request("coaxial-tube-v1"))
    relocated = tmp_path / "relocated"
    _ = original.rename(relocated)
    for item in relocated.iterdir():
        item.chmod(0o400)
    relocated.chmod(0o500)
    before = {
        item.name: (item.stat().st_mtime_ns, item.read_bytes())
        for item in relocated.iterdir()
    }
    try:
        assert verify_generation_run(relocated) == expected
        assert {
            item.name: (item.stat().st_mtime_ns, item.read_bytes())
            for item in relocated.iterdir()
        } == before
    finally:
        relocated.chmod(0o700)
        for item in relocated.iterdir():
            item.chmod(0o600)


@pytest.mark.parametrize(
    "tamper",
    ("canonical", "declaration", "model", "order", "source", "element"),
)
def test_tampering_fails_with_recomputed_manifest_and_sidecar(
    tmp_path: Path, tamper: str
) -> None:
    run = tmp_path / "generation"
    _ = create_generation_run(run, _request())
    if tamper == "canonical":
        name = "ground-truth.json"
        data = (run / name).read_bytes() + b" "
    elif tamper == "declaration":
        other = _request("coaxial-tube-v1")
        manifest = _json_object((run / "manifest.json").read_bytes())
        manifest["declaration"] = other.declaration.model_dump(mode="json")
        manifest["model_id"] = other.model_id
        manifest_bytes = canonical_json(manifest)
        _ = (run / "manifest.json").write_bytes(manifest_bytes)
        _ = (run / "manifest.sha256").write_bytes(
            f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
        )
        name = ""
        data = b""
    elif tamper == "model":
        name = "ground-truth.json"
        truth = _json_object((run / name).read_bytes())
        truth["model_id"] = "model." + "0" * 64
        data = canonical_json(truth)
    elif tamper == "order":
        name = "ground-truth.json"
        truth = _json_object((run / name).read_bytes())
        truth["parameter_order"] = list(reversed(truth["parameter_order"]))
        data = canonical_json(truth)
    elif tamper == "source":
        name = "observations.ply"
        data = (run / name).read_bytes() + b"tamper"
    else:
        name = "ground-truth.json"
        truth = _json_object((run / name).read_bytes())
        rows = cast(list[dict[str, Any]], truth["rows"])
        element_ids = cast(list[str], truth["element_ids"])
        rows[0]["expected_element_id"] = element_ids[1]
        data = canonical_json(truth)
    if name:
        _rewrite_primary(run, name, data)

    manifest_bytes = (run / "manifest.json").read_bytes()
    assert (run / "manifest.sha256").read_bytes() == (
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )
    with pytest.raises(ScansorError):
        _ = verify_generation_run(run)


def test_unknown_file_and_no_overwrite_fail_closed(tmp_path: Path) -> None:
    run = tmp_path / "generation"
    _ = create_generation_run(run, _request())
    with pytest.raises(ScansorError, match="already exists"):
        _ = create_generation_run(run, _request())
    _ = (run / "unknown").write_bytes(b"unexpected")
    with pytest.raises(ScansorError, match="file set"):
        _ = verify_generation_run(run)


def test_verification_rejects_root_relocation_during_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "generation"
    moved = tmp_path / "moved"
    _ = create_generation_run(run, _request())
    original = cast(
        Callable[[int, str, int], bytes],
        vars(generation_runs_module)["read_run_file"],
    )
    relocated = False

    def relocate_after_read(directory_fd: int, name: str, max_bytes: int) -> bytes:
        nonlocal relocated
        data = original(directory_fd, name, max_bytes)
        if not relocated:
            relocated = True
            _ = run.rename(moved)
            _ = shutil.copytree(moved, run)
        return data

    monkeypatch.setattr(generation_runs_module, "read_run_file", relocate_after_read)
    with pytest.raises(ScansorError, match="path changed"):
        _ = verify_generation_run(run)


def test_publication_revalidates_after_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "generation"

    def mutate_published_run(
        source_directory_fd: int,
        source_name: str,
        target_directory_fd: int,
        target_name: str,
    ) -> None:
        rename_no_replace(
            source_directory_fd,
            source_name,
            target_directory_fd,
            target_name,
        )
        published_fd = os.open(
            target_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=target_directory_fd,
        )
        try:
            unexpected_fd = os.open(
                "unexpected",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=published_fd,
            )
            os.close(unexpected_fd)
        finally:
            os.close(published_fd)

    monkeypatch.setattr(
        generation_runs_module, "rename_no_replace", mutate_published_run
    )
    with pytest.raises(ScansorError, match="inventory changed"):
        _ = create_generation_run(output, _request())
    assert not output.exists()
