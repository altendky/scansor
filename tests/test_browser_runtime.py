"""Runtime packaging must detect corrupt inputs and preserve reproducible sources."""

import hashlib
import runpy
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime.probe import (
    compare_numeric,
    compare_session,
    create_fixture,
)

BUILDER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/build-browser-runtime.py")
)


def record(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def test_integrity_rejects_same_size_corruption_and_missing_assets(tmp_path: Path):
    asset = tmp_path / "asset.wasm"
    _ = asset.write_bytes(b"good")
    expected = record(asset.name, b"good")
    BUILDER["verify"](tmp_path, [expected])
    _ = asset.write_bytes(b"evil")
    with pytest.raises(ValueError, match="integrity failure"):
        BUILDER["verify"](tmp_path, [expected])
    asset.unlink()
    with pytest.raises(ValueError, match="missing asset"):
        BUILDER["verify"](tmp_path, [expected])


def test_cache_corruption_is_not_silently_replaced(tmp_path: Path):
    cache = tmp_path / "cache"
    cache.mkdir()
    _ = (cache / "wheel.whl").write_bytes(b"evil")
    expected = record("runtime/wheel.whl", b"good") | {"url": "https://invalid.invalid"}
    with pytest.raises(ValueError, match="integrity failure"):
        BUILDER["fetch"](expected, tmp_path / "output", cache)
    assert not (tmp_path / "output/runtime/wheel.whl").exists()


@pytest.mark.parametrize("relative", ["../escape", "/absolute"])
def test_manifest_paths_cannot_escape_artifact(tmp_path: Path, relative: str):
    with pytest.raises(ValueError, match="escapes artifact"):
        BUILDER["checked_path"](tmp_path, relative)


def test_output_never_overwrites_existing_work(tmp_path: Path):
    _ = (tmp_path / "keep.txt").write_text("user work")
    with pytest.raises(ValueError, match="must be empty"):
        BUILDER["build"](tmp_path, None, False, Path("unused"))
    assert (tmp_path / "keep.txt").read_text() == "user work"


def test_source_archive_is_identical_despite_input_order_and_mtime(tmp_path: Path):
    bundle = BUILDER["source_bundle"]
    paths = ["src/scansor/__init__.py", "experiments/native_replay.py"]
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    one = bundle(paths, first)
    two = bundle(list(reversed(paths)), second)
    assert first.read_bytes() == second.read_bytes()
    assert one == two
    with zipfile.ZipFile(first) as archive:
        assert "scansor/__init__.py" in archive.namelist()
        assert "src/scansor/__init__.py" not in archive.namelist()
        assert all(
            info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()
        )


def test_core_requirement_checks_actual_locked_wheel_notices(tmp_path: Path):
    wheel = tmp_path / "runtime/numeric.whl"
    wheel.parent.mkdir()
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("example.dist-info/licenses/LICENSE", b"original notice")
    core = tmp_path / "notices/core/LICENSE"
    core.parent.mkdir(parents=True)
    _ = core.write_bytes(b"core notice")
    lock = {
        "assets": [
            record("runtime/numeric.whl", wheel.read_bytes()) | {"group": "numeric"}
        ],
        "licenses": [record("notices/core/LICENSE", core.read_bytes())],
    }
    with pytest.raises(ValueError, match="missing or changed wheel notice"):
        BUILDER["validate_core_notices"](tmp_path, lock, False)
    BUILDER["inventory_wheel_notices"](tmp_path, lock["assets"])
    BUILDER["validate_core_notices"](tmp_path, lock, False)
    notice = tmp_path / "notices/numeric/example.dist-info/licenses/LICENSE"
    _ = notice.write_bytes(b"tampered notice")
    with pytest.raises(ValueError, match="missing or changed wheel notice"):
        BUILDER["validate_core_notices"](tmp_path, lock, False)


def test_external_requirement_is_never_fulfilled_by_an_unknown_claim():
    manifest = {
        "groups": {
            "numeric": {
                "requirements": [
                    {"id": "unreviewed", "status": "external_required", "evidence": []}
                ]
            }
        }
    }
    with pytest.raises(ValueError, match="unfulfilled redistribution requirement"):
        BUILDER["complete_requirements"](
            manifest, ["numeric"], {"existing-core-and-wheel-notices"}
        )


def test_frozen_fixture_is_repeatable_and_uses_float64(tmp_path: Path):
    first, second = tmp_path / "first.npz", tmp_path / "second.npz"
    create_fixture(first)
    create_fixture(second)
    assert first.read_bytes() == second.read_bytes()
    with np.load(first, allow_pickle=False) as data:
        assert data["cone"].shape == (432, 3)
        assert data["plane"].shape == (64, 3)
        assert all(array.dtype == np.float64 for array in data.values())


def test_parity_rejects_changed_residuals_even_when_fit_summary_matches():
    fit = {
        "parameters": [1, 2],
        "weighted_rms": 0.001,
        "solver": {"termination": "done", "rank": 2, "iterations": 1, "evaluations": 1},
    }
    session = {
        "session": {"lateral_ids": [1, 2]},
        "fit": fit,
        "lateral_residuals": [0.001, -0.001],
        "plane_residuals": [0.0],
        "axis_display": [0, 0, 1],
        "point_display": [0, 0, 0],
        "plane_point_display": [0, 0, 1],
        "axial_domain": [0, 1],
        "reference_diameter": 2,
        "signed_half_angle_degrees": 0,
    }
    with pytest.raises(AssertionError):
        _ = compare_session(session | {"lateral_residuals": [0.002, -0.002]}, session)


def test_parity_rejects_changed_standalone_graph_result_before_joint_comparison():
    expected: dict[str, Any] = {
        "fixture_sha256": "fixture",
        "counts": {},
        "source_sha256": "source",
        "model_sha256": "model",
        "graph": {
            "recipe": {},
            "token": "token",
            "states": {},
            "errors": {},
            "diagnostics": {},
            "results": {
                "side": {
                    "parameters": [3.0],
                    "residuals": [0.001],
                    "ids": [7],
                    "kind": "cone",
                }
            },
        },
    }
    actual = expected | {
        "graph": expected["graph"]
        | {
            "results": {
                "side": {
                    "parameters": [4.0],
                    "residuals": [0.001],
                    "ids": [7],
                    "kind": "cone",
                }
            }
        }
    }
    with pytest.raises(AssertionError, match=r"graph\.results\.side\.parameters"):
        _ = compare_numeric(actual, expected)
