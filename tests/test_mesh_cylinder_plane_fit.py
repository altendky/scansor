"""Independent geometry checks for exact cylinder/plane perpendicularity."""

from pathlib import Path

import numpy as np
import pytest

from experiments.mesh_cylinder_plane_fit import (
    fit_cylinder_plane,
    joint_residual_jacobian,
)


def test_recovers_joint_geometry() -> None:
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1, 0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    center = np.array([0.3, -0.4, 0.0])
    angle, height = np.meshgrid(
        np.linspace(0, 2 * np.pi, 48, endpoint=False), np.linspace(-2, 2, 9)
    )
    cylinder = (
        center
        + height.ravel()[:, None] * axis
        + 3.2
        * (np.cos(angle.ravel())[:, None] * u + np.sin(angle.ravel())[:, None] * v)
    )
    x, y = np.meshgrid(np.linspace(-3, 3, 8), np.linspace(-3, 3, 8))
    plane = 4.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    result = fit_cylinder_plane(
        cylinder,
        plane,
        np.linspace(0.1, 2, len(cylinder)),
        np.linspace(0.2, 1, len(plane)),
        np.array([0.0, 0, 0, 0, 3, 4]),
    )
    np.testing.assert_allclose(
        result["parameters"], [0.3, -0.4, 0.12, -0.08, 3.2, 4.1], atol=1e-8
    )
    assert result["cylinder_weighted_rms"] < 1e-8
    assert result["plane_weighted_rms"] < 1e-8


def test_joint_derivative() -> None:
    cylinder = np.array([[2.0, 3, 1], [-3, 2, -2], [4, -1, 3], [-1, -4, -1]])
    plane = np.array([[2.0, 1, 4], [-2, 3, 4], [-1, -3, 4]])
    parameters = np.array([0.2, -0.1, 0.05, -0.03, 3, 4])
    _, actual = joint_residual_jacobian(cylinder, plane, parameters)
    expected = np.empty_like(actual)
    for j in range(6):
        delta = np.zeros(6)
        delta[j] = 1e-6
        plus, _ = joint_residual_jacobian(cylinder, plane, parameters + delta)
        minus, _ = joint_residual_jacobian(cylinder, plane, parameters - delta)
        expected[:, j] = (plus - minus) / 2e-6
    np.testing.assert_allclose(actual, expected, rtol=1e-7, atol=1e-8)


def test_saved_example_joint_fit_and_selection_guard(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import shutil

    from experiments.run_nozzle_cylinder import run as run_cylinder
    from experiments.run_nozzle_cylinder_plane import run as run_joint

    example = (
        Path(__file__).resolve().parents[1] / "examples/nozzle-bayonette-simplified"
    )
    run_cylinder(example, tmp_path / "cylinder")
    run_joint(example, tmp_path / "joint")
    baseline = json.loads((tmp_path / "cylinder/fit.json").read_text())
    joint = json.loads((tmp_path / "joint/fit.json").read_text())
    assert baseline["diameter"] == pytest.approx(18.7909521644, abs=1e-7)
    assert joint["counts"] == {"cylinder": 1261, "plane": 642}
    np.testing.assert_array_equal(joint["axis_world"], joint["plane_normal_world"])
    assert joint["axis_change_degrees"] > 0.1
    assert joint["joint_fit"]["cylinder_weighted_rms"] > baseline["fit"]["weighted_rms"]
    assert joint["joint_fit"]["plane_weighted_rms"] < joint["fixed_cylinder_plane_rms"]
    assert joint["joint_fit"]["weighted_rms"] < joint["fixed_cylinder_combined_rms"]
    assert joint["multistart_max_parameter_difference"] < 1e-7
    assert joint["world_residual_max_difference"] < 1e-12
    with np.load(tmp_path / "joint/residuals.npz") as data:
        assert len(data["plane_ids"]) == 642
        assert len(data["cylinder_ids"]) == 1261
    # The colored view must retain the entire mesh, not only selected observations.
    from scansor._plyio import Reader, build_layout, read_header

    meshes: list[tuple[np.ndarray, np.ndarray]] = []
    for path in (
        example / "nozzle-bayonette-simplified.ply",
        tmp_path / "joint/scan-joint-residuals.ply",
    ):
        with path.open("rb") as stream:
            layout = build_layout(
                read_header(stream), fixed_lists={("face", "vertex_indices"): 3}
            )
            reader = Reader(stream, layout, max_range_bytes=2_000_000)
            vertices = reader.read_range("vertex", 0, 24999)
            faces = reader.read_range("face", 0, 49994)
            meshes.append(
                (
                    np.column_stack([vertices[k] for k in ("x", "y", "z")]),
                    faces["vertex_indices"]["values"],
                )
            )
    np.testing.assert_array_equal(meshes[0][0], meshes[1][0])
    np.testing.assert_array_equal(meshes[0][1], meshes[1][1])
    # Selection gates are replayed, rather than accepting a saved count alone.
    copied = tmp_path / "altered-example"
    _ = shutil.copytree(example, copied)
    path = copied / "selections/top-face.json"
    selection = json.loads(path.read_text())
    selection["axial_range"] = [100, 101]
    _ = path.write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="gates do not reproduce"):
        run_joint(copied, tmp_path / "invalid-output")
    assert not (tmp_path / "invalid-output").exists()
    _ = capsys.readouterr()
