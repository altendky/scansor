from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from experiments.nozzle_browser import selection_bundle_recipe
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_fixture import (
    AXIAL_REGION_ROLES,
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
        ("reference", 20_182, 37_120),
        ("scan-coarse", 5_362, 9_128),
        ("scan-fine", 18_093, 33_096),
        ("scan-rescan", 10_939, 19_454),
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
    spec = load_fixture(DEFINITION)
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
        assert len(oracle.selections) == 16
        assert len(seed.selections) == 4
        assert {item.label.split()[0] for item in oracle.selections} == {
            "boss-a",
            "boss-b",
            "boss-c",
            "boss-d",
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
            expected_occurrence = (
                "boss-a",
                "boss-b",
                "boss-c",
                "boss-d",
            ).index(occurrence) + 1
            assert np.all(occurrence_codes[ids] == expected_occurrence)
            assert np.all(role_codes[ids] == ROLE_CODES[role])
            (u_min, u_max), (v_min, v_max) = REGION_BOUNDS[role]
            assert np.all((surface_uv[ids, 0] >= u_min) & (surface_uv[ids, 0] <= u_max))
            if role in AXIAL_REGION_ROLES:
                occurrence_height = (
                    spec.occurrences[expected_occurrence - 1].height_mm
                    or spec.boss.height_mm
                )
                axial_mm = surface_uv[ids, 1] * occurrence_height
                assert np.all(axial_mm >= v_min * spec.boss.height_mm)
                assert np.all(axial_mm <= v_max * spec.boss.height_mm)
            else:
                assert np.all(
                    (surface_uv[ids, 1] >= v_min) & (surface_uv[ids, 1] <= v_max)
                )
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


def test_occurrence_height_tilt_and_surface_jitter_are_recorded_in_truth(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    truth = root / "reference" / "truth"
    nominal = np.load(truth / "nominal-part.npy", allow_pickle=False)
    normals = np.load(truth / "normal-part.npy", allow_pickle=False)
    occurrence_codes = np.load(truth / "occurrence-code.npy", allow_pickle=False)
    role_codes = np.load(truth / "role-code.npy", allow_pickle=False)
    surface_uv = np.load(truth / "surface-uv.npy", allow_pickle=False)
    occurrences = json.loads((truth / "occurrences.json").read_text())["occurrences"]
    spec = load_fixture(DEFINITION)
    assert [item["occurrence_id"] for item in occurrences] == [
        item.occurrence_id for item in spec.occurrences
    ]

    for occurrence_code, expected_height in ((1, 14.0), (2, 22.0), (4, 14.0)):
        occurrence = spec.occurrences[occurrence_code - 1]
        shoulder = (occurrence_codes == occurrence_code) & (
            role_codes == ROLE_CODES["shoulder"]
        )
        axis = np.mean(normals[shoulder], axis=0)
        axis /= np.linalg.norm(axis)
        center = np.asarray((*occurrence.center_mm, 0.0))
        axial_positions = (nominal[shoulder] - center) @ axis
        np.testing.assert_allclose(axial_positions, expected_height, atol=1e-12)

    tilted_shoulder = (occurrence_codes == 4) & (role_codes == ROLE_CODES["shoulder"])
    tilted_axis = np.mean(normals[tilted_shoulder], axis=0)
    tilted_axis /= np.linalg.norm(tilted_axis)
    assert np.degrees(np.arccos(tilted_axis[2])) == pytest.approx(12.0)
    assert np.mod(np.degrees(np.arctan2(tilted_axis[1], tilted_axis[0])), 360.0) == (
        pytest.approx(330.0)
    )
    for occurrence_code in (2, 4):
        occurrence = spec.occurrences[occurrence_code - 1]
        clock = (occurrence_codes == occurrence_code) & (
            role_codes == ROLE_CODES["clock"]
        )
        actual_normal = np.mean(normals[clock], axis=0)
        actual_normal /= np.linalg.norm(actual_normal)
        azimuth = np.radians(occurrence.tilt_azimuth_degrees)
        tilt = np.radians(occurrence.tilt_degrees)
        clock_angle = np.radians(occurrence.clock_degrees)
        rz = np.array(
            (
                (np.cos(azimuth), -np.sin(azimuth), 0.0),
                (np.sin(azimuth), np.cos(azimuth), 0.0),
                (0.0, 0.0, 1.0),
            )
        )
        ry = np.array(
            (
                (np.cos(tilt), 0.0, np.sin(tilt)),
                (0.0, 1.0, 0.0),
                (-np.sin(tilt), 0.0, np.cos(tilt)),
            )
        )
        local_clock = np.array((np.cos(clock_angle), np.sin(clock_angle), 0.0))
        expected_normal = rz @ ry @ rz.T @ local_clock
        np.testing.assert_allclose(actual_normal, expected_normal, atol=1e-12)
        np.testing.assert_allclose(
            occurrences[occurrence_code - 1]["local_to_part_rotation"],
            rz
            @ ry
            @ rz.T
            @ np.array(
                (
                    (np.cos(clock_angle), -np.sin(clock_angle), 0.0),
                    (np.sin(clock_angle), np.cos(clock_angle), 0.0),
                    (0.0, 0.0, 1.0),
                )
            ),
            atol=1e-12,
        )

    boss_a_outer = (occurrence_codes == 1) & (role_codes == ROLE_CODES["outer"])
    interior_v = surface_uv[boss_a_outer, 1]
    interior_v = interior_v[(interior_v > 0.0) & (interior_v < 1.0)]
    grid_distance = np.abs(
        interior_v * spec.realizations[0].tessellation.axial_segments
        - np.round(interior_v * spec.realizations[0].tessellation.axial_segments)
    )
    assert np.max(grid_distance) > 0.01


def test_publication_refuses_to_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "fixture"
    _ = publish_fixture(output, DEFINITION)
    with pytest.raises(FileExistsError, match="already exists"):
        _ = publish_fixture(output, DEFINITION)


def test_checked_in_definition_is_valid_and_names_distinct_realizations() -> None:
    spec = load_fixture(DEFINITION)
    assert spec.format == "scansor-repeated-boss-selection-fixture-v2"
    assert [item.occurrence_id for item in spec.occurrences] == [
        "boss-a",
        "boss-b",
        "boss-c",
        "boss-d",
    ]
    assert spec.occurrences[1].height_mm == 22.0
    assert spec.occurrences[3].tilt_degrees == 12.0
    assert spec.occurrences[3].tilt_azimuth_degrees == 330.0
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
