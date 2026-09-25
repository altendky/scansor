from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from experiments.feature_reuse import estimate_rigid_match
from experiments.mesh_sphere_fit import fit_sphere
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
from experiments.selection_region import apply_selection_region, build_selection_region
from scansor.selection_bundle import SelectionBundle

DEFINITION = Path("examples/repeated-boss-selection/fixture.json")

PAINTED_MATCH_IDS = {
    "reference": [
        1388,
        1389,
        1390,
        1391,
        1392,
        1393,
        1394,
        1724,
        1733,
        1795,
        1799,
        1800,
        1994,
        1995,
        1999,
        2000,
        2004,
        2005,
        2009,
        2010,
        2012,
        2013,
        2014,
        2015,
        2016,
        2017,
        2018,
        2019,
        2028,
        2037,
        2046,
        2055,
        2064,
    ],
    "target": [
        4649,
        4650,
        4651,
        4662,
        4663,
        4664,
        5065,
        5070,
        5074,
        5075,
        5265,
        5269,
        5270,
        5274,
        5275,
        5280,
        5283,
        5284,
        5285,
        5286,
        5287,
        5288,
        5289,
        5298,
        5316,
        5325,
        5334,
        5343,
        5352,
    ],
}


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
        ("reference", 24_508, 45_760),
        ("scan-coarse", 6_292, 10_976),
        ("scan-fine", 21_795, 40_488),
        ("scan-rescan", 12_985, 23_534),
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
        pose_rotation = np.asarray(manifest["pose"]["part_to_scan_rotation"])
        pose_translation = np.asarray(manifest["pose"]["part_to_scan_translation_mm"])
        measured_part = np.load(root / "truth/measured-part.npy", allow_pickle=False)
        measured_scan = np.load(root / "truth/measured-scan.npy", allow_pickle=False)
        if manifest["realization"]["workspace_frame"] == "scan":
            np.testing.assert_allclose(workspace.origin, np.zeros(3))
            np.testing.assert_allclose(workspace.frame, np.eye(3))
            np.testing.assert_allclose(workspace.local, measured_scan, atol=8e-6)
            initial = workspace.data.selection["initial_parameters"]
            expected_axis = pose_rotation @ np.array([0.0, 0.0, 1.0])
            expected_center = pose_rotation @ np.array([-34.0, -20.0, 0.0])
            expected_point = (
                expected_center - expected_axis * expected_center[2] / expected_axis[2]
            )
            np.testing.assert_allclose(
                initial,
                [
                    expected_point[0],
                    expected_point[1],
                    expected_axis[0] / expected_axis[2],
                    expected_axis[1] / expected_axis[2],
                    11.0,
                ],
            )
        else:
            np.testing.assert_allclose(workspace.origin, pose_translation)
            np.testing.assert_allclose(workspace.frame, pose_rotation)
            np.testing.assert_allclose(workspace.local, measured_part, atol=8e-6)
            assert workspace.data.selection["initial_parameters"] == [
                -34.0,
                -20.0,
                0.0,
                0.0,
                11.0,
            ]
        assert (
            workspace.data.selection["azimuth_frame"]["origin"]
            == (workspace.data.selection["axis_seed"]["center"])
        )
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
        assert len(oracle.selections) == 20
        assert len(seed.selections) == 4
        boss_selections = [
            item for item in oracle.selections if item.label.startswith("boss-")
        ]
        sphere_selections = [
            item for item in oracle.selections if item.label.startswith("sphere-")
        ]
        assert {item.label.split()[0] for item in boss_selections} == {
            "boss-a",
            "boss-b",
            "boss-c",
            "boss-d",
        }
        assert {item.label.split()[1] for item in boss_selections} == {
            "outer",
            "bore",
            "shoulder",
            "clock",
        }
        assert {item.label for item in sphere_selections} == {
            "sphere-a sphere",
            "sphere-b sphere",
            "sphere-c sphere",
        }
        plate_top = next(
            item for item in oracle.selections if item.label == "plate top"
        )
        plate_top_ids = np.asarray(plate_top.vertex_ids)
        assert np.all(occurrence_codes[plate_top_ids] == 0)
        assert np.all(role_codes[plate_top_ids] == ROLE_CODES["plate"])
        assert all(item.vertex_count >= 3 for item in oracle.selections)
        for item in boss_selections:
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


def test_corner_spheres_are_recorded_and_fit_from_noisy_scan(tmp_path: Path) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    example = root / "scan-coarse"
    workspace = NozzleWorkspace(example)
    oracle = SelectionBundle.model_validate_json(
        (example / "selections/oracle-selection-bundle.json").read_bytes()
    )
    occurrence_codes = np.load(
        example / "truth/occurrence-code.npy", allow_pickle=False
    )
    role_codes = np.load(example / "truth/role-code.npy", allow_pickle=False)
    truth = json.loads((example / "truth/occurrences.json").read_text())
    pose_rotation = np.asarray(
        json.loads((example / "manifest.json").read_text())["pose"][
            "part_to_scan_rotation"
        ]
    )

    assert truth["spheres"] == [
        {
            "center_part_mm": [-52.0, -32.0, 5.0],
            "radius_mm": 5.0,
            "sphere_id": "sphere-a",
        },
        {
            "center_part_mm": [52.0, -32.0, 5.0],
            "radius_mm": 5.0,
            "sphere_id": "sphere-b",
        },
        {
            "center_part_mm": [52.0, 32.0, 5.0],
            "radius_mm": 5.0,
            "sphere_id": "sphere-c",
        },
    ]
    for sphere_index, expected in enumerate(truth["spheres"], start=5):
        selection = next(
            item
            for item in oracle.selections
            if item.selection_id == f"{expected['sphere_id']}-sphere"
        )
        ids = np.asarray(selection.vertex_ids)
        assert np.all(occurrence_codes[ids] == sphere_index)
        assert np.all(role_codes[ids] == ROLE_CODES["sphere"])
        result = fit_sphere(workspace.local[ids], workspace.data.weights[ids])
        expected_center = pose_rotation @ np.asarray(expected["center_part_mm"])
        np.testing.assert_allclose(result["parameters"][:3], expected_center, atol=0.03)
        assert result["parameters"][3] == pytest.approx(expected["radius_mm"], abs=0.03)


def test_fitted_outer_region_selects_the_same_role_on_another_boss(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    example = root / "scan-coarse"
    workspace = NozzleWorkspace(example)
    selections = SelectionBundle.model_validate_json(
        (example / "selections/user-selection-bundle.json").read_bytes()
    )
    oracle = SelectionBundle.model_validate_json(
        (example / "selections/oracle-selection-bundle.json").read_bytes()
    )
    source_ids = np.asarray(
        next(
            item.vertex_ids
            for item in selections.selections
            if item.label == "boss-a outer"
        )
    )
    truth = json.loads((example / "truth/occurrences.json").read_text())["occurrences"]
    manifest = json.loads((example / "manifest.json").read_text())
    pose_rotation = np.asarray(manifest["pose"]["part_to_scan_rotation"])
    pose_translation = np.asarray(manifest["pose"]["part_to_scan_translation_mm"])
    source_rotation = pose_rotation @ np.asarray(
        truth[0]["local_to_part_rotation"], dtype=float
    )
    source_origin = (
        pose_rotation @ np.asarray(truth[0]["center_part_mm"], dtype=float)
        + pose_translation
    )
    source_local = (workspace.local[source_ids] - source_origin) @ source_rotation
    fitted_radius = float(np.median(np.linalg.norm(source_local[:, :2], axis=1)))
    region = build_selection_region(
        workspace.local[source_ids],
        (workspace.data.normals @ workspace.frame)[source_ids],
        {
            "kind": "cylinder",
            "parameters": [0.0, 0.0, 0.0, 0.0, fitted_radius, 0.0, 0.0],
        },
        source_origin,
        source_rotation,
        tangent_margin=0.55,
        normal_margin=0.35,
        normal_angle_degrees=25.0,
    )
    target_rotation = pose_rotation @ np.asarray(
        truth[1]["local_to_part_rotation"], dtype=float
    )
    target_origin = (
        pose_rotation @ np.asarray(truth[1]["center_part_mm"], dtype=float)
        + pose_translation
    )
    transferred = set(
        apply_selection_region(
            workspace.local,
            workspace.data.normals @ workspace.frame,
            workspace.data.weights,
            region,
            target_origin,
            target_rotation,
        )
    )
    expected = set(
        next(
            item.vertex_ids
            for item in oracle.selections
            if item.label == "boss-b outer"
        )
    )
    occurrence_codes = np.load(
        example / "truth/occurrence-code.npy", allow_pickle=False
    )
    role_codes = np.load(example / "truth/role-code.npy", allow_pickle=False)

    assert transferred
    assert all(occurrence_codes[index] == 2 for index in transferred)
    assert all(role_codes[index] == ROLE_CODES["outer"] for index in transferred)
    assert len(transferred & expected) / len(expected) >= 0.8


def test_painted_correspondence_selections_recover_the_tilted_boss_pose(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fixture"
    _ = publish_fixture(root, DEFINITION)
    example = root / "scan-coarse"
    workspace = NozzleWorkspace(example)
    normals = workspace.data.normals @ workspace.frame
    reference_ids = np.asarray(PAINTED_MATCH_IDS["reference"])
    target_ids = np.asarray(PAINTED_MATCH_IDS["target"])

    match = estimate_rigid_match(
        workspace.local[reference_ids],
        normals[reference_ids],
        workspace.local[target_ids],
        normals[target_ids],
    )
    occurrences = json.loads((example / "truth/occurrences.json").read_text())[
        "occurrences"
    ]
    reference = occurrences[0]
    target = occurrences[3]
    manifest = json.loads((example / "manifest.json").read_text())
    pose_rotation = np.asarray(manifest["pose"]["part_to_scan_rotation"])
    pose_translation = np.asarray(manifest["pose"]["part_to_scan_translation_mm"])
    reference_rotation = pose_rotation @ np.asarray(reference["local_to_part_rotation"])
    target_rotation = pose_rotation @ np.asarray(target["local_to_part_rotation"])
    expected_rotation = reference_rotation @ target_rotation.T
    reference_origin = (
        pose_rotation @ np.asarray(reference["center_part_mm"]) + pose_translation
    )
    target_origin = (
        pose_rotation @ np.asarray(target["center_part_mm"]) + pose_translation
    )
    expected_translation = target_origin - reference_origin @ expected_rotation
    actual_rotation = np.asarray(match["rotation"])
    actual_translation = np.asarray(match["translation"])
    angle_error = np.degrees(
        np.arccos(
            np.clip(
                (np.trace(actual_rotation @ expected_rotation.T) - 1.0) / 2.0,
                -1.0,
                1.0,
            )
        )
    )

    assert angle_error < 1.0
    assert np.linalg.norm(actual_translation - expected_translation) < 0.6
    assert match["rms"] < 0.25
    assert match["ambiguity_ratio"] > 1.5


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
    assert [item.sphere_id for item in spec.spheres] == [
        "sphere-a",
        "sphere-b",
        "sphere-c",
    ]
    assert [item.realization_id for item in spec.realizations] == [
        "reference",
        "scan-coarse",
        "scan-fine",
        "scan-rescan",
    ]
    assert spec.realizations[1].pose.rotation_xyz_degrees == (6.0, -8.0, 11.0)
    assert spec.realizations[1].workspace_frame == "scan"

    invalid = json.loads(DEFINITION.read_text())
    invalid["realizations"][0]["sensor_sigma_mm"] = 0.01
    with pytest.raises(ValueError, match="exact reference requires zero corruption"):
        _ = FixtureSpec.model_validate(invalid)
