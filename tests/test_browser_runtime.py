"""Runtime packaging must detect corrupt inputs and preserve reproducible sources."""

import hashlib
import json
import math
import runpy
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from experiments.browser_runtime.probe import (
    analytic_geometry_probe,
    compare_imported_cylinders,
    compare_imported_planes,
    compare_imported_spheres,
    compare_numeric,
    compare_session,
    create_fixture,
    imported_cylinder_probe,
    imported_sphere_probe,
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
        assert data["imported_spheres"].shape == (3, 35, 3)
        assert data["imported_sphere_normals"].shape == (3, 35, 3)
        assert data["imported_cylinders"].shape == (3, 60, 3)
        assert data["imported_cylinder_normals"].shape == (3, 60, 3)
        assert data["imported_cylinder_axes"].shape == (3, 3)
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
        compare_imported_planes([actual], [expected])


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
        compare_imported_planes([actual], [expected])


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
    compare_imported_planes([actual], [expected])
    assert actual == before_actual
    assert expected == before_expected


def test_imported_plane_parity_cannot_normalize_with_an_untrusted_actual_scale() -> (
    None
):
    # Independently normalizing each report would hide this coordinate change.
    with pytest.raises(AssertionError):
        compare_imported_planes(
            [imported_plane_record(2.0)], [imported_plane_record(1.0)]
        )


@pytest.mark.parametrize("scale", [0.0, -1.0, float("inf"), float("nan")])
def test_imported_plane_parity_rejects_invalid_frozen_expected_scale(
    scale: float,
) -> None:
    expected = imported_plane_record(1.0)
    expected["comparison_length_scale"] = scale
    with pytest.raises(ValueError, match="finite and positive"):
        compare_imported_planes([imported_plane_record(1.0)], [expected])


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
                "solver": {
                    "coordinate_origin": [3 * scale, -4 * scale, 2 * scale],
                    "coordinate_length": 2 * scale,
                    "rank": 4,
                    "iterations": 2,
                },
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
    shared["results"]["sphere"]["initialization"]["recovery"] = {
        "binding_sha256": "b" * 64,
        "selected": "fresh",
        "selected_role": "fresh",
        "attempts": [
            {
                "candidate": {"parameters": [0.1, 0.2, 0.3, 1.2]},
                "assessment": {"objective": 0.001, "rank": 4},
            }
        ],
    }
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
        compare_imported_spheres([actual], [expected])


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
        compare_imported_spheres([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_sphere_parity_accepts_roundoff_without_mutating_records(
    scale: float,
) -> None:
    expected = imported_sphere_record(scale)
    actual = deepcopy(expected)
    actual["standalone"]["results"]["sphere"]["residuals"][0] += 3e-16 * scale
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    compare_imported_spheres([actual], [expected])
    assert actual == before_actual
    assert expected == before_expected


def test_imported_sphere_parity_requires_the_frozen_expected_scale() -> None:
    with pytest.raises(AssertionError):
        compare_imported_spheres(
            [imported_sphere_record(2.0)], [imported_sphere_record(1.0)]
        )
    for scale in (0.0, -1.0, float("inf"), float("nan")):
        expected = imported_sphere_record(1.0)
        expected["comparison_length_scale"] = scale
        with pytest.raises(ValueError, match="finite and positive"):
            compare_imported_spheres([imported_sphere_record(1.0)], [expected])


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
    compare_imported_spheres([actual], [expected])
    actual["shared_center"]["recipe"]["nodes"][-1]["initial_coordinates"] = (
        math.nextafter(coordinate, math.inf),
        0.0,
        0.0,
    )
    with pytest.raises(
        AssertionError,
        match=r"imported_spheres\[0\]\.shared_center\.recipe\.nodes\[1\]\.initial_coordinates\[0\]",
    ):
        compare_imported_spheres([actual], [expected])


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
    compare_imported_spheres([report], [encoded])
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
            "algorithm": "selected-cylinder-recovery-v1",
            "chart": "rotated-normalized-cylinder-v1",
            "origin": [3 * scale, -4 * scale, 2 * scale],
            "length": 2 * scale,
            "coverage_ratio": 0.1,
            "recovery": {
                "binding_sha256": "b" * 64,
                "selected": "fresh-0",
                "selected_role": "fresh",
                "attempts": [
                    {
                        "candidate": {"parameters": [0.1, 0.2, 0.0, 0.0, 1.2]},
                        "role": "fresh",
                        "evaluations": 3,
                        "assessment": {"objective": 0.001, "condition": 2.0},
                        "solver": {"rank": 5, "iterations": 2},
                    }
                ],
            },
        },
        "solver": {
            "coordinate_origin": [3 * scale, -4 * scale, 2 * scale],
            "coordinate_length": 2 * scale,
            "rank": 5,
            "iterations": 2,
        },
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
        compare_imported_cylinders([actual], [expected])


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
        recovery["attempts"][0]["solver"]["rank"] = 4
    elif field == "evaluations":
        recovery["attempts"][0]["evaluations"] = 4
    else:
        recovery["selected_role"] = "hint"
    with pytest.raises(AssertionError):
        compare_imported_cylinders([actual], [expected])


@pytest.mark.parametrize("scale", [1e-6, 1.0, 1e6])
def test_imported_cylinder_parity_accepts_roundoff_without_mutating_inputs(
    scale: float,
) -> None:
    expected = imported_cylinder_record(scale)
    actual = deepcopy(expected)
    actual["hinted"]["result"]["residuals"][0] += 3e-16 * scale
    before_actual, before_expected = deepcopy(actual), deepcopy(expected)
    compare_imported_cylinders([actual], [expected])
    assert actual == before_actual and expected == before_expected


def test_imported_cylinder_parity_requires_the_frozen_expected_scale() -> None:
    with pytest.raises(AssertionError):
        compare_imported_cylinders(
            [imported_cylinder_record(2.0)], [imported_cylinder_record(1.0)]
        )
    for scale in (0.0, -1.0, float("inf"), float("nan"), True):
        expected = imported_cylinder_record(1.0)
        expected["comparison_length_scale"] = scale
        with pytest.raises(ValueError, match="finite and positive"):
            compare_imported_cylinders([imported_cylinder_record(1.0)], [expected])


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
    }
    actual = deepcopy(expected)
    actual["imported_cylinders"][0]["standalone"]["results"]["cylinder"]["analytic"][
        "radius"
    ] += 0.1
    with pytest.raises(AssertionError, match="imported_cylinders"):
        _ = compare_numeric(actual, expected)


@pytest.mark.parametrize("index", range(3))
def test_frozen_imported_cylinders_fit_grow_recover_and_replay(
    index: int, tmp_path: Path
) -> None:
    fixture = tmp_path / "fixture.npz"
    create_fixture(fixture)
    with np.load(fixture, allow_pickle=False) as data:
        report = imported_cylinder_probe(
            data["imported_cylinders"][index],
            data["imported_cylinder_normals"][index],
            data["imported_cylinder_origins"][index],
            data["imported_cylinder_axes"][index],
            float(data["imported_cylinder_scales"][index]),
        )
    encoded = json.loads(json.dumps(report, allow_nan=False))
    compare_imported_cylinders([report], [encoded])
    assert encoded["standalone"]["results"]["grown"]["ids"] == list(range(60))
    recovered = encoded["hinted"]["result"]["initialization"]["recovery"]
    assert recovered["selected_role"] == "fresh"
    assert recovered["attempts"][-1]["role"] == "hint"
    assert recovered["attempts"][-1]["evaluations"] == 0
