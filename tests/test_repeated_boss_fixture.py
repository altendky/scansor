from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from experiments.nozzle_browser import selection_bundle_recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_fixture import (
    REGION_BOUNDS,
    ROLE_CODES,
    FixtureSpec,
    load_fixture,
    publish_fixture,
    sensor_offsets,
)
from experiments.run_nozzle_cylinder import load_example
from scansor.selection_bundle import SelectionBundle

DEFINITION = Path("examples/repeated-boss-selection/fixture.json")


def test_generated_repeated_boss_fixture_is_deterministic_and_browser_readable(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = publish_fixture(first_root, DEFINITION)
    second = publish_fixture(second_root, DEFINITION)
    assert first == second
    assert first["artifacts"] == second["artifacts"]
    assert [
        (item["realization_id"], item["vertices"], item["triangles"])
        for item in first["realizations"]
    ] == [
        ("reference", 15_878, 29_184),
        ("scan-coarse", 4_260, 7_240),
        ("scan-fine", 14_317, 26_164),
        ("scan-rescan", 8_660, 15_378),
    ]

    source_hashes: set[str] = set()
    for realization in (
        "reference",
        "scan-coarse",
        "scan-fine",
        "scan-rescan",
    ):
        root = first_root / realization
        example = load_example(root)
        workspace = NozzleWorkspace(root)
        manifest = json.loads((root / "manifest.json").read_text())
        source_hashes.add(manifest["files"][manifest["source_file"]]["sha256"])
        assert len(example.xyz) == manifest["vertices"]
        assert len(example.triangles) == manifest["triangles"]
        assert np.isfinite(example.xyz).all()
        assert np.isfinite(example.normals).all()
        assert np.all(example.weights > 0.0)
        face_cross = np.cross(
            example.xyz[example.triangles[:, 1]] - example.xyz[example.triangles[:, 0]],
            example.xyz[example.triangles[:, 2]] - example.xyz[example.triangles[:, 0]],
        )
        face_normals = np.sum(example.normals[example.triangles], axis=1)
        assert np.all(np.sum(face_cross * face_normals, axis=1) > 0.0)
        recipe = selection_bundle_recipe(workspace, root / manifest["selection_bundle"])
        assert len(recipe.nodes) == 5
        assert recipe.nodes[0].operation == "source"
        assert all(node.operation == "selection" for node in recipe.nodes[1:])
        result = workspace.fit(workspace.default, "cylinder")
        assert np.isfinite(result["fit"]["weighted_rms"])
    assert len(source_hashes) == 4


def test_truth_separates_nominal_as_built_sensor_and_pose(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    reference = root / "reference" / "truth"
    reference_nominal = np.load(reference / "nominal-part.npy", allow_pickle=False)
    assert np.array_equal(
        reference_nominal,
        np.load(reference / "as-built-part.npy", allow_pickle=False),
    )
    assert np.array_equal(
        reference_nominal,
        np.load(reference / "measured-part.npy", allow_pickle=False),
    )

    coarse = root / "scan-coarse" / "truth"
    nominal = np.load(coarse / "nominal-part.npy", allow_pickle=False)
    as_built = np.load(coarse / "as-built-part.npy", allow_pickle=False)
    measured = np.load(coarse / "measured-part.npy", allow_pickle=False)
    normals = np.load(coarse / "normal-part.npy", allow_pickle=False)
    assert not np.array_equal(nominal, as_built)
    assert not np.array_equal(as_built, measured)
    displacement = measured - as_built
    normal_offset = np.sum(displacement * normals, axis=1)
    assert (
        np.max(np.linalg.norm(displacement - normal_offset[:, None] * normals, axis=1))
        < 1e-12
    )
    assert np.max(np.abs(normal_offset)) <= 0.055 + 4.0 * 0.12 + 1e-12

    rescan_manifest = json.loads((root / "scan-rescan/manifest.json").read_text())
    assert rescan_manifest["pose"]["part_to_scan_rotation"] != np.eye(3).tolist()
    assert rescan_manifest["pose"]["part_to_scan_translation_mm"] != [0.0, 0.0, 0.0]
    rescan = root / "scan-rescan"
    measured_part = np.load(rescan / "truth/measured-part.npy", allow_pickle=False)
    measured_scan = np.load(rescan / "truth/measured-scan.npy", allow_pickle=False)
    normal_part = np.load(rescan / "truth/normal-part.npy", allow_pickle=False)
    normal_scan = np.load(rescan / "truth/normal-scan.npy", allow_pickle=False)
    rotation = np.asarray(rescan_manifest["pose"]["part_to_scan_rotation"])
    translation = np.asarray(rescan_manifest["pose"]["part_to_scan_translation_mm"])
    np.testing.assert_allclose(measured_scan, measured_part @ rotation.T + translation)
    np.testing.assert_allclose(normal_scan, normal_part @ rotation.T)
    assert np.array_equal(
        load_example(rescan).xyz,
        measured_scan.astype("<f4").astype(np.float64),
    )
    codes = json.loads((rescan / "truth/codes.json").read_text())
    occurrence_codes = np.load(rescan / "truth/occurrence-code.npy", allow_pickle=False)
    assert set(codes["occurrence_codes"]) == {
        str(int(value)) for value in np.unique(occurrence_codes)
    }


def test_role_regions_transfer_across_occurrences_and_topologies(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    first_ids: dict[str, tuple[int, ...]] = {}
    for realization in ("scan-coarse", "scan-fine", "scan-rescan"):
        realization_root = root / realization
        directory = realization_root / "selections"
        occurrence_codes = np.load(
            realization_root / "truth/occurrence-code.npy", allow_pickle=False
        )
        role_codes = np.load(
            realization_root / "truth/role-code.npy", allow_pickle=False
        )
        surface_uv = np.load(
            realization_root / "truth/surface-uv.npy", allow_pickle=False
        )
        oracle = SelectionBundle.model_validate_json(
            (directory / "oracle-selection-bundle.json").read_bytes()
        )
        seed = SelectionBundle.model_validate_json(
            (directory / "user-selection-bundle.json").read_bytes()
        )
        assert len(oracle.selections) == 12
        assert len(seed.selections) == 4
        assert {item.label.split()[0] for item in oracle.selections} == {
            "boss-a",
            "boss-b",
            "boss-c",
        }
        assert {item.label.split()[1] for item in oracle.selections} == {
            "outer",
            "bore",
            "shoulder",
            "clock",
        }
        assert all(item.vertex_count >= 3 for item in oracle.selections)
        for item in oracle.selections:
            occurrence, role = item.label.split()
            ids = np.asarray(item.vertex_ids)
            expected_occurrence = ("boss-a", "boss-b", "boss-c").index(occurrence) + 1
            assert np.all(occurrence_codes[ids] == expected_occurrence)
            assert np.all(role_codes[ids] == ROLE_CODES[role])
            (u_min, u_max), (v_min, v_max) = REGION_BOUNDS[role]
            assert np.all((surface_uv[ids, 0] >= u_min) & (surface_uv[ids, 0] <= u_max))
            assert np.all((surface_uv[ids, 1] >= v_min) & (surface_uv[ids, 1] <= v_max))
        first_ids[realization] = oracle.selections[0].vertex_ids
    assert len(set(first_ids.values())) == 3


def test_coarse_and_fine_reuse_noise_at_shared_surface_coordinates() -> None:
    spec = load_fixture(DEFINITION)
    coarse = spec.realizations[1].model_copy(
        update={"bias_amplitude_mm": 0.0, "sensor_sigma_mm": 1.0}
    )
    fine = spec.realizations[2].model_copy(
        update={"bias_amplitude_mm": 0.0, "sensor_sigma_mm": 1.0}
    )
    assert coarse.seed == fine.seed
    shared_uv = np.asarray([[[0.25, 0.5], [0.5, 0.75]]])
    assert np.array_equal(
        sensor_offsets(coarse, "boss-a", "outer", shared_uv),
        sensor_offsets(fine, "boss-a", "outer", shared_uv),
    )


def test_publication_refuses_to_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    _ = publish_fixture(output, DEFINITION)
    with pytest.raises(FileExistsError, match="already exists"):
        _ = publish_fixture(output, DEFINITION)


def test_checked_in_definition_is_valid_and_names_distinct_realizations() -> None:
    spec = load_fixture(DEFINITION)
    assert spec.format == "scansor-repeated-boss-selection-fixture-v1"
    assert [item.occurrence_id for item in spec.occurrences] == [
        "boss-a",
        "boss-b",
        "boss-c",
    ]
    assert [item.realization_id for item in spec.realizations] == [
        "reference",
        "scan-coarse",
        "scan-fine",
        "scan-rescan",
    ]

    invalid = json.loads(DEFINITION.read_text())
    invalid["realizations"][0]["sensor_sigma_mm"] = 0.01
    with pytest.raises(ValueError, match="exact reference requires zero corruption"):
        _ = FixtureSpec.model_validate(invalid)
