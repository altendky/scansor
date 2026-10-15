"""Runtime packaging must detect corrupt inputs and preserve reproducible sources."""

import hashlib
import json
import math
import runpy
import zipfile
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime.probe import (
    analytic_geometry_probe,
    compare_imported_cones,
    compare_imported_cylinders,
    compare_imported_planes,
    compare_imported_spheres,
    compare_local_replay,
    compare_numeric,
    compare_session,
    compare_support_transfers,
    create_fixture,
    imported_cone_probe,
    imported_cylinder_probe,
    imported_sphere_probe,
    support_transfer_probe,
)
from experiments.browser_runtime.semantic_parity import SemanticParityError
from experiments.fit_initialization import DEFAULT_POLICY
from experiments.mesh_cone_fit import CONE_POLICY_VERSION
from experiments.mesh_cylinder_fit import CYLINDER_POLICY_VERSION

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
        assert data["imported_spheres"].shape == (3, 35, 3)
        assert data["imported_sphere_normals"].shape == (3, 35, 3)
        assert data["imported_cylinders"].shape == (4, 60, 3)
        assert data["imported_cylinder_normals"].shape == (4, 60, 3)
        assert data["imported_cylinder_axes"].shape == (4, 3)
        long_cylinder = data["imported_cylinders"][-1]
        assert np.ptp(long_cylinder[:, 0]) == 6e5
        np.testing.assert_allclose(
            np.linalg.norm(
                (long_cylinder - data["imported_cylinder_origins"][-1])[:, 1:], axis=1
            ),
            2.5,
            atol=1e-14,
        )
        assert data["imported_cones"].shape == (4, 60, 3)
        assert data["imported_cone_normals"].shape == (4, 60, 3)
        assert data["imported_cone_axes"].shape == (4, 3)
        assert data["support_points"].shape == (2, 60, 3)
        assert data["support_normals"].shape == (2, 60, 3)
        np.testing.assert_allclose(
            np.linalg.norm(data["support_normals"], axis=2), 1, atol=1e-14
        )
        np.testing.assert_array_equal(
            data["imported_cone_slopes"], [0.15, -0.2, 1e-7, 1e-7]
        )
        np.testing.assert_allclose(np.ptp(data["imported_cones"][3, :, 2]), 6e6)
        np.testing.assert_allclose(
            np.linalg.norm(data["imported_cone_normals"], axis=2), 1, atol=1e-14
        )
        np.testing.assert_allclose(
            np.linalg.norm(data["imported_sphere_normals"], axis=2), 1, atol=1e-14
        )
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


def test_parity_rejects_changed_imported_plane_before_legacy_fit_comparison():
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
            "results": {},
        },
        "imported_planes": [imported_plane_record(1.0)],
    }
    actual = deepcopy(expected)
    actual["imported_planes"][0]["results"]["plane"]["parameters"][:3] = [0.0, 1, 0]
    with pytest.raises(AssertionError, match=r"imported_planes.*plane\.parameters"):
        _ = compare_numeric(actual, expected)


@pytest.mark.parametrize("mutation", ["axis", "normal", "support"])
def test_worker_parity_rejects_changed_canonical_spatial_geometry(
    mutation: str,
) -> None:
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
            "results": {},
        },
        "imported_planes": [],
        "imported_spheres": [],
        "imported_cylinders": [],
        "imported_cones": [],
        "support_transfers": [],
        "imported_symmetries": [],
        "analytic_geometry": analytic_geometry_probe(),
    }
    actual = deepcopy(expected)
    if mutation == "axis":
        actual["analytic_geometry"]["cylinder"][0]["geometry"]["axis"][0] = 0.0
    elif mutation == "normal":
        actual["analytic_geometry"]["cone"][1]["normals"][0][0] += 0.1
    else:
        actual["analytic_geometry"]["cone"][0]["inside"][0] = False
    with pytest.raises(AssertionError, match="analytic_geometry"):
        _ = compare_numeric(actual, expected)


def imported_plane_record(scale: float) -> dict[str, Any]:
    """A complete probe-shaped report with dimensions prescribed independently."""
    return {
        "comparison_length_scale": scale,
        "recipe": {
            "nodes": [
                {
                    "id": "scan",
                    "operation": "source",
                    "source_sha256": "a" * 64,
                    "reference_sha256": "b" * 64,
                }
            ],
            "output": "grown",
        },
        "token": "source-bound-actions",
        "states": {"plane": "ready", "grown": "ready"},
        "errors": {},
        "diagnostics": {},
        "results": {
            "plane": {
                "kind": "plane",
                "ids": [4, 9],
                "parameters": [1.0, 0.0, 0.0, 3.0 * scale],
                "residuals": [0.001 * scale, -0.002 * scale],
                "weighted_rms": 0.0015 * scale,
                "axial_domain": [-2.0, 5.0],
                "normal_sign": 1.0,
                "initialization": {
                    "algorithm": "weighted-covariance-plane-v1",
                    "chart": "workspace-normal-offset-v1",
                    "origin": [3.0 * scale, -4.0 * scale, 2.0 * scale],
                    "length": 2.0 * scale,
                    "normalized_covariance_eigenvalues": [0.0, 0.3, 1.0],
                    "coverage_ratio": 0.3,
                    "normal_gap_ratio": 0.3,
                    "orientation": "selected-normals",
                    "normal_projection": 1.0,
                    "observations": 2,
                },
                "support_classification": {
                    "inside_vertices": 2,
                    "outside_vertices": 0,
                    "role": "selection-and-display",
                },
            },
            "grown": {
                "ids": [4, 9, 12],
                "added_ids": [12],
                "added_area": 0.01 * scale**2,
            },
        },
    }


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field", ["offset", "residual", "weighted_rms", "origin", "length", "added_area"]
)
def test_imported_plane_parity_rejects_errors_in_normalized_length_and_area_fields(
    scale: float, field: str
) -> None:
    expected = imported_plane_record(scale)
    actual = deepcopy(expected)
    plane = actual["results"]["plane"]
    # Each mutation has the same meaningful size in the frozen fixture's units.
    # At the small scale, raw length/area tolerances would silently admit it.
    delta = 1e-6 * scale
    if field == "offset":
        plane["parameters"][3] += delta
    elif field == "residual":
        plane["residuals"][0] += delta
    elif field == "weighted_rms":
        plane["weighted_rms"] += delta
    elif field == "origin":
        plane["initialization"]["origin"][0] += delta
    elif field == "length":
        plane["initialization"]["length"] += delta
    else:
        actual["results"]["grown"]["added_area"] += 1e-6 * scale**2
    with pytest.raises(AssertionError):
        _ = compare_imported_planes([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field", ["normal", "ids", "source", "binding", "axial_domain"]
)
def test_imported_plane_parity_keeps_geometry_directions_ids_and_bindings_strict(
    scale: float, field: str
) -> None:
    expected = imported_plane_record(scale)
    actual = deepcopy(expected)
    if field == "normal":
        actual["results"]["plane"]["parameters"][1] = 1e-6
    elif field == "ids":
        actual["results"]["plane"]["ids"][0] = 5
    elif field == "source":
        actual["recipe"]["nodes"][0]["source_sha256"] = "c" * 64
    elif field == "binding":
        actual["recipe"]["nodes"][0]["reference_sha256"] = "c" * 64
    else:
        actual["results"]["plane"]["axial_domain"][0] += 1e-6
    with pytest.raises(AssertionError):
        _ = compare_imported_planes([actual], [expected])


def test_imported_plane_parity_accepts_reported_large_scale_rounding_difference() -> (
    None
):
    expected = imported_plane_record(1e6)
    actual = deepcopy(expected)
    expected["results"]["plane"]["residuals"][0] = 2.114431
    actual["results"]["plane"]["residuals"][0] = 2.114431 + 3.51519702e-10
    with pytest.raises(AssertionError):
        np.testing.assert_allclose(
            actual["results"]["plane"]["residuals"],
            expected["results"]["plane"]["residuals"],
            rtol=1e-10,
            atol=1e-10,
        )
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    _ = compare_imported_planes([actual], [expected])
    assert actual == before_actual
    assert expected == before_expected


def test_imported_plane_parity_cannot_normalize_with_an_untrusted_actual_scale() -> (
    None
):
    # Independently normalizing each report would hide this coordinate change.
    with pytest.raises(AssertionError):
        _ = compare_imported_planes(
            [imported_plane_record(2.0)], [imported_plane_record(1.0)]
        )


@pytest.mark.parametrize("scale", [0.0, -1.0, float("inf"), float("nan")])
def test_imported_plane_parity_rejects_invalid_frozen_expected_scale(
    scale: float,
) -> None:
    expected = imported_plane_record(1.0)
    expected["comparison_length_scale"] = scale
    with pytest.raises(ValueError, match="finite and positive"):
        _ = compare_imported_planes([imported_plane_record(1.0)], [expected])


def bounded_fixture_recovery(
    parameters: list[float], rank: int, chart: str
) -> dict[str, Any]:
    """Complete bounded schema, independent of dimensional comparison helpers."""
    return {
        "policy": asdict(DEFAULT_POLICY),
        "binding_sha256": "b" * 64,
        "selected": "fresh-0",
        "selected_role": "fresh",
        "termination": "best-attempted-local-solution",
        "attempts": [
            {
                "candidate": {
                    "id": "fresh-0",
                    "parameters": parameters,
                    "derivation": "prescribed selected-data fixture",
                    "chart": chart,
                    "binding_sha256": "b" * 64,
                },
                "role": "fresh",
                "outcome": "locally-converged",
                "evaluations": 3,
                "assessment": {
                    "objective": 0.001,
                    "valid": True,
                    "rank": rank,
                    "condition": 2.0,
                    "termination": "projected-gradient",
                    "iterations": 2,
                },
                "solver": None,
            }
        ],
    }


def fixture_solver(scale: float, rank: int) -> dict[str, Any]:
    return {
        "implementation": "scaled-svd-least-squares-v1",
        "termination": "projected-gradient",
        "coordinate_origin": [3 * scale, -4 * scale, 2 * scale],
        "coordinate_length": 2 * scale,
        "rank": rank,
        "iterations": 2,
        "evaluations": 3,
        "scaled_jacobian_condition": 2.0,
        "scaled_projected_gradient_infinity_norm": 5e-11,
    }


def imported_sphere_record(scale: float) -> dict[str, Any]:
    """Prescribe physical lengths and normalized recovery quantities separately."""
    standalone: dict[str, Any] = {
        "recipe": {
            "nodes": [{"id": "scan", "source_sha256": "a" * 64}],
            "output": "grown",
        },
        "token": "sphere-actions",
        "states": {"sphere": "ready", "grown": "ready"},
        "errors": {},
        "diagnostics": {},
        "results": {
            "sphere": {
                "kind": "sphere",
                "ids": [4, 9],
                "parameters": [3 * scale, -4 * scale, 2 * scale, 5 * scale],
                "residuals": [0.001 * scale, -0.002 * scale],
                "weighted_rms": 0.0015 * scale,
                "axial_domain": [-2.0, 5.0],
                "normal_sign": 1.0,
                "initialization": {
                    "algorithm": "centered-algebraic-sphere-v1",
                    "chart": "normalized-center-radius-v1",
                    "origin": [3 * scale, -4 * scale, 2 * scale],
                    "length": 2 * scale,
                    "initial_parameters": [0.1, -0.2, 0.3, 1.2],
                    "algebraic_rank": 4,
                    "algebraic_condition": 2.0,
                },
                "solver": fixture_solver(scale, 4),
            },
            "grown": {
                "ids": [4, 9, 12],
                "added_ids": [12],
                "added_area": 0.01 * scale**2,
            },
        },
    }
    shared = deepcopy(standalone)
    shared["recipe"]["output"] = "sphere"
    del shared["results"]["grown"]
    del shared["states"]["grown"]
    shared["results"]["center"] = {
        "point_display": [3 * scale, -4 * scale, 2 * scale],
        "coordinates": [3 * scale, -4 * scale, 2 * scale],
    }
    shared["results"]["sphere"]["initialization"]["recovery"] = (
        bounded_fixture_recovery([0.1, 0.2, 0.3, 1.2], 4, "normalized-center-radii-v1")
    )
    shared["results"]["sphere"]["initialization"].update(
        algorithm="joint-algebraic-shared-sphere-v1",
        chart="normalized-center-radii-v1",
    )
    del shared["results"]["sphere"]["initialization"]["initial_parameters"]
    return {
        "comparison_length_scale": scale,
        "standalone": standalone,
        "shared_center": shared,
    }


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field",
    [
        "center",
        "radius",
        "residual",
        "rms",
        "origin",
        "length",
        "solver_origin",
        "solver_length",
        "point",
        "coordinates",
        "area",
    ],
)
def test_imported_sphere_parity_rejects_dimensional_errors_at_every_scale(
    scale: float,
    field: str,
) -> None:
    expected = imported_sphere_record(scale)
    actual = deepcopy(expected)
    sphere = actual["standalone"]["results"]["sphere"]
    delta = 1e-6 * scale
    if field in {"center", "radius"}:
        sphere["parameters"][3 if field == "radius" else 0] += delta
    elif field == "residual":
        sphere["residuals"][0] += delta
    elif field == "rms":
        sphere["weighted_rms"] += delta
    elif field == "origin":
        sphere["initialization"]["origin"][0] += delta
    elif field == "length":
        sphere["initialization"]["length"] += delta
    elif field == "solver_origin":
        sphere["solver"]["coordinate_origin"][0] += delta
    elif field == "solver_length":
        sphere["solver"]["coordinate_length"] += delta
    elif field in {"point", "coordinates"}:
        actual["shared_center"]["results"]["center"][
            "point_display" if field == "point" else field
        ][0] += delta
    else:
        actual["standalone"]["results"]["grown"]["added_area"] += 1e-6 * scale**2
    with pytest.raises(AssertionError):
        _ = compare_imported_spheres([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field",
    [
        "ids",
        "source",
        "token",
        "binding",
        "initial_parameters",
        "candidate_parameters",
        "objective",
        "rank",
    ],
)
def test_imported_sphere_parity_keeps_intent_and_normalized_evidence_strict(
    scale: float,
    field: str,
) -> None:
    expected = imported_sphere_record(scale)
    actual = deepcopy(expected)
    sphere = actual["standalone"]["results"]["sphere"]
    recovery = actual["shared_center"]["results"]["sphere"]["initialization"][
        "recovery"
    ]
    if field == "ids":
        sphere["ids"][0] = 5
    elif field == "source":
        actual["standalone"]["recipe"]["nodes"][0]["source_sha256"] = "c" * 64
    elif field == "token":
        actual["shared_center"]["token"] = "stale"
    elif field == "binding":
        recovery["binding_sha256"] = "c" * 64
    elif field == "initial_parameters":
        sphere["initialization"]["initial_parameters"][0] += 1e-6
    elif field == "candidate_parameters":
        recovery["attempts"][0]["candidate"]["parameters"][0] += 1e-6
    elif field == "objective":
        recovery["attempts"][0]["assessment"]["objective"] += 1e-6
    else:
        sphere["solver"]["rank"] = 3
    with pytest.raises(AssertionError):
        _ = compare_imported_spheres([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_sphere_parity_accepts_roundoff_without_mutating_records(
    scale: float,
) -> None:
    expected = imported_sphere_record(scale)
    actual = deepcopy(expected)
    actual["standalone"]["results"]["sphere"]["residuals"][0] += 3e-16 * scale
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    _ = compare_imported_spheres([actual], [expected])
    assert actual == before_actual
    assert expected == before_expected


def test_imported_sphere_parity_requires_the_frozen_expected_scale() -> None:
    with pytest.raises(AssertionError):
        _ = compare_imported_spheres(
            [imported_sphere_record(2.0)], [imported_sphere_record(1.0)]
        )
    for scale in (0.0, -1.0, float("inf"), float("nan")):
        expected = imported_sphere_record(1.0)
        expected["comparison_length_scale"] = scale
        with pytest.raises(ValueError, match="finite and positive"):
            _ = compare_imported_spheres([imported_sphere_record(1.0)], [expected])


@pytest.mark.parametrize("coordinate", [1.0, 1e100])
def test_imported_sphere_parity_keeps_authored_coordinates_exact_after_json_roundtrip(
    coordinate: float,
) -> None:
    expected = imported_sphere_record(1.0)
    expected["shared_center"]["recipe"]["nodes"].append(
        {"id": "center", "initial_coordinates": [coordinate, 0.0, 0.0]}
    )
    actual = deepcopy(expected)
    actual["shared_center"]["recipe"]["nodes"][-1]["initial_coordinates"] = (
        coordinate,
        0.0,
        0.0,
    )
    # Native model tuples and serialized arrays carry the same exact intent.
    _ = compare_imported_spheres([actual], [expected])
    actual["shared_center"]["recipe"]["nodes"][-1]["initial_coordinates"] = (
        math.nextafter(coordinate, math.inf),
        0.0,
        0.0,
    )
    with pytest.raises(
        AssertionError,
        match=r"imported_spheres\[0\]\.shared_center\.recipe\.nodes\[1\]\.initial_coordinates\[0\]",
    ):
        _ = compare_imported_spheres([actual], [expected])


@pytest.mark.parametrize("index", range(3))
def test_frozen_imported_spheres_fit_grow_recover_and_replay(
    index: int, tmp_path: Path
) -> None:
    fixture = tmp_path / "fixture.npz"
    create_fixture(fixture)
    with np.load(fixture, allow_pickle=False) as data:
        report = imported_sphere_probe(
            data["imported_spheres"][index],
            data["imported_sphere_normals"][index],
            data["imported_sphere_centers"][index],
            float(data["imported_sphere_radii"][index]),
            float(data["imported_sphere_scales"][index]),
        )
    encoded = json.loads(json.dumps(report, allow_nan=False))
    # This is the worker's real comparison shape: live model tuples against
    # the native JSON reference, while every authored number remains exact.
    _ = compare_imported_spheres([report], [encoded])
    assert encoded["standalone"]["results"]["grown"]["ids"] == list(range(35))
    recovered = encoded["shared_center"]["results"]["sphere"]["initialization"][
        "recovery"
    ]
    assert recovered["selected_role"] == "fresh"
    assert len(recovered["attempts"]) == 2


def imported_cylinder_record(scale: float) -> dict[str, Any]:
    """Physical canonical lengths and normalized recovery evidence are distinct."""
    fitted: dict[str, Any] = {
        "kind": "cylinder",
        "ids": [4, 9],
        "analytic": {
            "kind": "cylinder",
            "origin": [3 * scale, -4 * scale, 2 * scale],
            "axis": [1.0, 0.0, 0.0],
            "radius": 2.5 * scale,
            "slope": 0.0,
            "axial_domain": [-scale, 3 * scale],
        },
        "axial_domain": [-scale, 3 * scale],
        "anchor_reference": [3 * scale, -4 * scale, 2 * scale],
        "support_origin": "selection",
        "normal_sign": 1.0,
        "residuals": [0.001 * scale, -0.002 * scale],
        "weighted_rms": 0.0015 * scale,
        "initialization": {
            "algorithm": CYLINDER_POLICY_VERSION,
            "chart": "rotated-normalized-cylinder-v1",
            "origin": [3 * scale, -4 * scale, 2 * scale],
            "length": 2 * scale,
            "coverage_ratio": 0.1,
            "recovery": bounded_fixture_recovery(
                [0.1, 0.2, 0.0, 0.0, 1.2], 5, "normal-moment-0"
            ),
        },
        "solver": fixture_solver(scale, 5),
    }
    standalone: dict[str, Any] = {
        "recipe": {
            "nodes": [{"id": "scan", "source_sha256": "a" * 64}],
            "output": "grown",
        },
        "token": "cylinder-actions",
        "states": {"cylinder": "ready", "grown": "ready"},
        "errors": {},
        "diagnostics": {},
        "results": {
            "cylinder": fitted,
            "grown": {
                "ids": [4, 9, 12],
                "added_ids": [12],
                "added_area": 0.01 * scale**2,
            },
        },
    }
    axis_bound = deepcopy(standalone)
    axis_bound["recipe"]["output"] = "bound"
    axis_bound["results"]["bound"] = deepcopy(fitted)
    axis_bound["results"]["axis"] = {
        "point_display": [3 * scale, -4 * scale, 2 * scale],
        "axis_display": [-1.0, 0.0, 0.0],
    }
    return {
        "comparison_length_scale": scale,
        "standalone": standalone,
        "axis_bound": axis_bound,
        "hinted": {
            "source_sha256": "a" * 64,
            "interpretation_sha256": "c" * 64,
            "ids": [4, 9],
            "result": deepcopy(fitted),
        },
    }


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field",
    [
        "origin",
        "radius",
        "domain",
        "top_domain",
        "anchor",
        "residual",
        "rms",
        "init_origin",
        "init_length",
        "solver_origin",
        "solver_length",
        "axis_point",
        "hinted_radius",
        "area",
    ],
)
def test_imported_cylinder_parity_rejects_dimensional_errors_at_every_scale(
    scale: float, field: str
) -> None:
    expected = imported_cylinder_record(scale)
    actual = deepcopy(expected)
    cylinder = actual["standalone"]["results"]["cylinder"]
    delta = 1e-6 * scale
    if field in {"origin", "radius", "domain"}:
        key = "axial_domain" if field == "domain" else field
        if field == "radius":
            cylinder["analytic"][key] += delta
        else:
            cylinder["analytic"][key][0] += delta
    elif field in {"top_domain", "anchor", "residual"}:
        key = {
            "top_domain": "axial_domain",
            "anchor": "anchor_reference",
            "residual": "residuals",
        }[field]
        cylinder[key][0] += delta
    elif field == "rms":
        cylinder["weighted_rms"] += delta
    elif field in {"init_origin", "init_length"}:
        key = "origin" if field == "init_origin" else "length"
        if key == "origin":
            cylinder["initialization"][key][0] += delta
        else:
            cylinder["initialization"][key] += delta
    elif field in {"solver_origin", "solver_length"}:
        key = "coordinate_origin" if field == "solver_origin" else "coordinate_length"
        if key == "coordinate_origin":
            cylinder["solver"][key][0] += delta
        else:
            cylinder["solver"][key] += delta
    elif field == "axis_point":
        actual["axis_bound"]["results"]["axis"]["point_display"][0] += delta
    elif field == "hinted_radius":
        actual["hinted"]["result"]["analytic"]["radius"] += delta
    else:
        actual["standalone"]["results"]["grown"]["added_area"] += 1e-6 * scale**2
    with pytest.raises(AssertionError):
        _ = compare_imported_cylinders([actual], [expected])


@pytest.mark.parametrize(
    "field",
    [
        "axis",
        "normal_sign",
        "ids",
        "source",
        "interpretation",
        "binding",
        "candidate",
        "objective",
        "rank",
        "evaluations",
        "selected_role",
    ],
)
def test_imported_cylinder_parity_preserves_identity_and_full_normalized_evidence(
    field: str,
) -> None:
    expected = imported_cylinder_record(1e-6)
    actual = deepcopy(expected)
    cylinder = actual["standalone"]["results"]["cylinder"]
    recovery = cylinder["initialization"]["recovery"]
    if field == "axis":
        cylinder["analytic"]["axis"][0] = 0.0
    elif field == "normal_sign":
        cylinder["normal_sign"] = -1.0
    elif field == "ids":
        cylinder["ids"][0] = 5
    elif field == "source":
        actual["hinted"]["source_sha256"] = "d" * 64
    elif field == "interpretation":
        actual["hinted"]["interpretation_sha256"] = "d" * 64
    elif field == "binding":
        recovery["binding_sha256"] = "d" * 64
    elif field == "candidate":
        recovery["attempts"][0]["candidate"]["parameters"][4] += 1e-6
    elif field == "objective":
        recovery["attempts"][0]["assessment"]["objective"] += 1e-6
    elif field == "rank":
        cylinder["solver"]["rank"] = 4
    elif field == "evaluations":
        recovery["attempts"][0]["evaluations"] = 513
    else:
        recovery["selected_role"] = "hint"
    with pytest.raises(AssertionError):
        _ = compare_imported_cylinders([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_cylinder_parity_accepts_roundoff_without_mutating_inputs(
    scale: float,
) -> None:
    expected = imported_cylinder_record(scale)
    actual = deepcopy(expected)
    actual["hinted"]["result"]["residuals"][0] += 3e-16 * scale
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    _ = compare_imported_cylinders([actual], [expected])
    assert actual == before_actual and expected == before_expected


def test_imported_cylinder_parity_accepts_bounded_realized_evaluation_drift() -> None:
    expected = imported_cylinder_record(1.0)
    actual = deepcopy(expected)
    fitted = actual["standalone"]["results"]["cylinder"]
    fitted["initialization"]["recovery"]["attempts"][0]["evaluations"] = 4
    fitted["solver"]["evaluations"] = 4
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    _ = compare_imported_cylinders([actual], [expected])
    assert actual == before_actual and expected == before_expected


def test_imported_cylinder_parity_requires_the_frozen_expected_scale() -> None:
    with pytest.raises(AssertionError):
        _ = compare_imported_cylinders(
            [imported_cylinder_record(2.0)], [imported_cylinder_record(1.0)]
        )
    for scale in (0.0, -1.0, float("inf"), float("nan"), True):
        expected = imported_cylinder_record(1.0)
        expected["comparison_length_scale"] = scale
        with pytest.raises(ValueError, match="finite and positive"):
            _ = compare_imported_cylinders([imported_cylinder_record(1.0)], [expected])


def test_numeric_parity_checks_imported_cylinders_before_legacy_results() -> None:
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
            "results": {},
        },
        "imported_planes": [],
        "imported_spheres": [],
        "imported_cylinders": [imported_cylinder_record(1.0)],
        "imported_cones": [],
    }
    actual = deepcopy(expected)
    actual["imported_cylinders"][0]["standalone"]["results"]["cylinder"]["analytic"][
        "radius"
    ] += 0.1
    with pytest.raises(AssertionError, match="imported_cylinders"):
        _ = compare_numeric(actual, expected)


@pytest.mark.parametrize("index", range(4))
def test_frozen_imported_cylinders_fit_grow_recover_and_replay(
    index: int, tmp_path: Path
) -> None:
    fixture = tmp_path / "fixture.npz"
    create_fixture(fixture)
    replay_evidence: list[dict[str, Any]] = []
    with np.load(fixture, allow_pickle=False) as data:
        report = imported_cylinder_probe(
            data["imported_cylinders"][index],
            data["imported_cylinder_normals"][index],
            data["imported_cylinder_origins"][index],
            data["imported_cylinder_axes"][index],
            float(data["imported_cylinder_scales"][index]),
            replay_evidence=replay_evidence,
        )
    encoded = json.loads(json.dumps(report, allow_nan=False))
    receipts = compare_imported_cylinders([report], [encoded])
    assert len(receipts) == 1 and receipts[0]["semantic_complete"] is True
    assert [item["path"] for item in replay_evidence] == [
        "cylinder.replay.False",
        "cylinder.replay.True",
    ]
    assert all(
        item["receipt"]["semantic_complete"] is True
        and "memberships" in item["actual"]
        and "memberships" in item["expected"]
        for item in replay_evidence
    )
    assert encoded["standalone"]["results"]["grown"]["ids"] == list(range(60))
    recovered = encoded["hinted"]["result"]["initialization"]["recovery"]
    assert recovered["selected_role"] == "fresh"
    assert recovered["attempts"][-1]["role"] == "hint"
    assert recovered["attempts"][-1]["evaluations"] == 0


def imported_cone_record(scale: float) -> dict[str, Any]:
    report = imported_cylinder_record(scale)
    for mode in ("standalone", "axis_bound"):
        record = report[mode]
        record["results"]["cone"] = record["results"].pop("cylinder")
        record["states"]["cone"] = record["states"].pop("cylinder")
    for fitted in (
        report["standalone"]["results"]["cone"],
        report["axis_bound"]["results"]["cone"],
        report["axis_bound"]["results"]["bound"],
        report["hinted"]["result"],
    ):
        fitted["kind"] = "cone"
        fitted["analytic"].update(kind="cone", slope=0.15)
        fitted["initialization"]["algorithm"] = CONE_POLICY_VERSION
        fitted["initialization"]["chart"] = "rotated-normalized-cone-v1"
        fitted["initialization"]["recovery"] = bounded_fixture_recovery(
            [0.1, 0.2, 0.0, 0.0, 1.2, 0.15], 6, "normal-moment-0"
        )
        fitted["solver"] = fixture_solver(scale, 6)
    return report


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_frozen_support_transfer_preserves_physical_bounds_and_recipe(
    kind: str, tmp_path: Path
) -> None:
    fixture = tmp_path / "fixture.npz"
    create_fixture(fixture)
    index = 0 if kind == "cylinder" else 1
    replay_evidence: list[dict[str, Any]] = []
    with np.load(fixture, allow_pickle=False) as data:
        report = support_transfer_probe(
            data["support_points"][index],
            data["support_normals"][index],
            "cylinder" if kind == "cylinder" else "cone",
            replay_evidence=replay_evidence,
        )
    encoded = json.loads(json.dumps(report, allow_nan=False))
    receipts = compare_support_transfers([report], [encoded])
    assert len(receipts) == 1
    assert receipts[0]["semantic_complete"] is True
    assert receipts[0]["budgets_validated"] is True
    assert receipts[0]["budget_records_validated"] > 0
    assert len(replay_evidence) == 1
    replay = replay_evidence[0]
    assert replay["path"] == f"support.{kind}.replay"
    assert replay["receipt"]["semantic_complete"] is True
    assert replay["receipt"]["budgets_validated"] is True
    assert replay["receipt"]["budget_records_validated"] > 0
    for side in ("actual", "expected"):
        assert {
            "memberships",
            "derived",
            "input_catalogue",
            "input_requirements",
        } <= replay[side].keys()
        for key in report["state"]:
            assert json.loads(json.dumps(replay[side][key])) == encoded["state"][key]
    assert encoded["state"]["results"][report["clone"]]["support_intent"][
        "interval"
    ] == [1.0, 9.0]


@pytest.mark.parametrize("field", ["memberships", "derived"])
def test_local_replay_failure_retains_full_independent_operands_and_receipt(
    field: str,
) -> None:
    expected: dict[str, Any] = {
        "recipe": {"nodes": []},
        "results": {},
        "memberships": {"patch": [1, 2]},
        "derived": {"axis": {"axis_display": [1.0, 0.0, 0.0]}},
    }
    actual = deepcopy(expected)
    if field == "memberships":
        actual[field]["patch"] = [1]
    else:
        actual[field]["axis"]["axis_display"][0] = 0.0
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    evidence: list[dict[str, Any]] = []
    with pytest.raises(SemanticParityError, match=field) as failure:
        compare_local_replay(actual, expected, "support.failure.replay", evidence)
    assert len(evidence) == 1
    retained = evidence[0]
    assert retained["actual"] == before_actual
    assert retained["expected"] == before_expected
    assert retained["receipt"] == failure.value.receipt
    assert retained["receipt"]["complete"] is False
    assert actual == before_actual and expected == before_expected
    actual["memberships"]["patch"].clear()
    expected["derived"]["axis"]["axis_display"][0] = 42.0
    assert retained["actual"] == before_actual
    assert retained["expected"] == before_expected


@pytest.mark.parametrize(
    "changed", ["recipe", "plane", "binding", "membership", "owner"]
)
def test_support_transfer_parity_rejects_changed_intent_geometry_and_identity(
    changed: str,
) -> None:
    expected: dict[str, Any] = {
        "kind": "cylinder",
        "comparison_length_scale": 1.0,
        "clone": "copy",
        "state": {
            "recipe": {
                "nodes": [
                    {
                        "id": "copy",
                        "managed_by": "reuse",
                        "support_authority": "prototype",
                        "axial_domain": [1.0, 9.0],
                    }
                ]
            },
            "results": {
                "copy": {
                    "support_intent": {
                        "kind": "reference-plane",
                        "normal": [1.0, 0, 0],
                        "offset": 20.0,
                        "interval": [1.0, 9.0],
                    },
                    "ids": [60, 61],
                    "binding_sha256": "bound-to-intent",
                }
            },
        },
    }
    actual = deepcopy(expected)
    if changed == "recipe":
        actual["state"]["recipe"]["nodes"][0]["axial_domain"][0] += 1e-12
    elif changed == "owner":
        actual["state"]["recipe"]["nodes"][0]["managed_by"] = "foreign"
    elif changed == "plane":
        actual["state"]["results"]["copy"]["support_intent"]["offset"] += 0.01
    elif changed == "binding":
        actual["state"]["results"]["copy"]["binding_sha256"] = "stale"
    else:
        actual["state"]["results"]["copy"]["ids"] = [60]
    with pytest.raises(AssertionError, match="support_transfers"):
        _ = compare_support_transfers([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_cone_parity_valid_six_parameter_records(scale: float) -> None:
    expected = imported_cone_record(scale)
    before = deepcopy(expected)
    fitted = expected["standalone"]["results"]["cone"]
    attempt = fitted["initialization"]["recovery"]["attempts"][0]
    assert len(attempt["candidate"]["parameters"]) == 6
    assert attempt["assessment"]["rank"] == fitted["solver"]["rank"] == 6
    receipts = compare_imported_cones([expected], [json.loads(json.dumps(expected))])
    assert len(receipts) == 1
    assert receipts[0]["semantic_complete"] is True
    assert receipts[0]["budgets_validated"] is True
    assert receipts[0]["budget_records_validated"] > 0
    assert expected == before


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
@pytest.mark.parametrize(
    "field",
    ["radius", "residual", "area", "slope", "axis", "binding", "selected", "intent"],
)
def test_imported_cone_parity_checks_geometry_intent_and_recovery(
    scale: float, field: str
) -> None:
    expected = imported_cone_record(scale)
    actual = deepcopy(expected)
    fitted = actual["standalone"]["results"]["cone"]
    if field == "radius":
        fitted["analytic"]["radius"] += 1e-6 * scale
    elif field == "residual":
        fitted["residuals"][0] += 1e-6 * scale
    elif field == "area":
        actual["standalone"]["results"]["grown"]["added_area"] += 1e-6 * scale**2
    elif field == "slope":
        fitted["analytic"]["slope"] += 1e-6
    elif field == "axis":
        fitted["analytic"]["axis"][0] = -1.0
    elif field == "binding":
        fitted["initialization"]["recovery"]["binding_sha256"] = "changed"
    elif field == "selected":
        fitted["ids"][0] = 5
    else:
        actual["standalone"]["recipe"]["output"] = "changed"
    with pytest.raises(AssertionError, match="imported_cones"):
        _ = compare_imported_cones([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_cone_parity_accepts_unit_scaled_roundoff(scale: float) -> None:
    expected = imported_cone_record(scale)
    actual = deepcopy(expected)
    actual["standalone"]["results"]["cone"]["residuals"][0] += 1e-12 * scale
    original = deepcopy(actual)
    _ = compare_imported_cones([actual], [json.loads(json.dumps(expected))])
    assert actual == original


@pytest.mark.parametrize("index", range(4))
def test_frozen_imported_cones_fit_grow_recover_and_replay(
    index: int, tmp_path: Path
) -> None:
    fixture = tmp_path / "fixture.npz"
    create_fixture(fixture)
    replay_evidence: list[dict[str, Any]] = []
    with np.load(fixture, allow_pickle=False) as data:
        report = imported_cone_probe(
            data["imported_cones"][index],
            data["imported_cone_normals"][index],
            data["imported_cone_origins"][index],
            data["imported_cone_axes"][index],
            float(data["imported_cone_slopes"][index]),
            float(data["imported_cone_scales"][index]),
            replay_evidence=replay_evidence,
        )
    encoded = json.loads(json.dumps(report, allow_nan=False))
    receipts = compare_imported_cones([report], [encoded])
    assert len(receipts) == 1 and receipts[0]["semantic_complete"] is True
    assert [item["path"] for item in replay_evidence] == [
        "cone.replay.False",
        "cone.replay.True",
    ]
    assert all(
        item["receipt"]["semantic_complete"] is True
        and "memberships" in item["actual"]
        and "memberships" in item["expected"]
        for item in replay_evidence
    )
    fitted = encoded["standalone"]["results"]["cone"]
    assert fitted["kind"] == "cone"
    assert "parameters" not in fitted
    assert encoded["standalone"]["results"]["grown"]["ids"] == list(range(60))
    recovery = encoded["hinted"]["result"]["initialization"]["recovery"]
    assert recovery["selected_role"] == "fresh"
    assert recovery["attempts"][-1]["role"] == "hint"
    assert recovery["attempts"][-1]["evaluations"] == 0
