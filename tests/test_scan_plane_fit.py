"""Independent plane geometry, imported-source replay, growth and CAD evidence."""

import io
import json
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from zipfile import ZipFile

import numpy as np
import pytest
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Plane
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape

from experiments.feature_graph import (
    FeatureGraph,
    Growth,
    Recipe,
    Selection,
    Source,
    SurfaceFit,
    fit_plane_to_reference,
)
from experiments.mesh_cone_fit import ConeFitFailure
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.ocp_geometry import surface_patch
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.selection_growth import (
    PlaneFitFailure,
    connected_growth,
    fit_seed,
    surface_distance,
)
from experiments.surface_primitives import primitive


def independent_grid(
    normal: np.ndarray,
    *,
    noise: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A nonuniform 4-by-3 mesh with independently prescribed plane geometry."""
    normal = normal / np.linalg.norm(normal)
    reference = np.eye(3)[int(np.argmin(np.abs(normal)))]
    first = np.cross(normal, reference)
    first /= np.linalg.norm(first)
    second = np.cross(normal, first)
    horizontal, vertical = np.meshgrid([-2.0, -0.5, 0.3, 3.0], [-1.0, 0.2, 2.0])
    positions = (
        [3.0, -4.0, 2.0]
        + horizontal.ravel()[:, None] * first
        + vertical.ravel()[:, None] * second
        + noise * np.cos(np.arange(12) * 1.7)[:, None] * normal
    )
    triangles: list[tuple[int, int, int]] = []
    for row in range(2):
        for column in range(3):
            start = row * 4 + column
            triangles.extend(
                [(start, start + 1, start + 4), (start + 1, start + 5, start + 4)]
            )
    return positions, np.tile(normal, (12, 1)), np.array(triangles, dtype=np.int32)


def strict_ply(
    positions: np.ndarray, normals: np.ndarray, triangles: np.ndarray
) -> bytes:
    """Write fixture bytes directly, without Scansor's PLY writer or examples."""
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment Independently generated nonuniform plane grid\n"
        f"element vertex {len(positions)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(triangles)}\n"
        "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    vertices = b"".join(
        struct.pack("<6f", *position, *normal)
        for position, normal in zip(positions, normals, strict=True)
    )
    faces = b"".join(struct.pack("<B3i", 3, *triangle) for triangle in triangles)
    return header + vertices + faces


def imported_grid(normal: np.ndarray, *, noise: float = 0.0) -> ScanWorkspace:
    positions, normals, triangles = independent_grid(normal, noise=noise)
    payload = strict_ply(positions, normals, triangles)
    return prepare_scan(
        io.BytesIO(payload),
        "independent-plane.ply",
        ScanLimits(2_000_000, 100, 100, 10_000_000),
    )


def imported_recipe(workspace: ScanWorkspace, *, grow: bool = False) -> Recipe:
    assert workspace.descriptor is not None
    assert workspace.source_sha256 is not None
    source = Source(
        id="scan",
        label="Independent source",
        operation="source",
        source_sha256=workspace.source_sha256,
        reference_sha256=workspace.reference_sha256,
        descriptor=workspace.descriptor,
    )
    selected = Selection(
        id="selected",
        label="Selected patch",
        operation="selection",
        source="scan",
        ids=[0, 1, 4, 5] if grow else list(range(len(workspace.local))),
    )
    plane = SurfaceFit(
        id="plane",
        label="Fitted plane",
        operation="fit",
        selections=["selected"],
        kind="plane",
    )
    if not grow:
        return Recipe(schema_version=2, nodes=[source, selected, plane], output="plane")
    growth = Growth(
        id="grown",
        label="Connected plane proposal",
        operation="growth",
        seed_fit="plane",
        distance=0.05,
        angle_degrees=10.0,
    )
    return Recipe(
        schema_version=2, nodes=[source, selected, plane, growth], output="grown"
    )


def results(state: dict[str, object]) -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], state["results"])


@pytest.mark.parametrize("normal", [np.array([1.0, 0, 0]), np.array([2.0, -3, 4])])
@pytest.mark.parametrize("scale", [1e-21, 1e-6, 1.0, 1e6])
def test_direct_plane_is_seed_free_and_unit_covariant(
    normal: np.ndarray, scale: float
) -> None:
    points, normals, _ = independent_grid(normal)
    shift = np.array([20.0, -30.0, 40.0])
    moved = scale * (points + shift)
    weights = np.linspace(0.1, 2.0, len(points))
    fitted = fit_seed(moved, weights, normals, "plane", None, (-2, 5))
    expected_normal = normal / np.linalg.norm(normal)
    parameters = np.array(fitted["parameters"])
    np.testing.assert_allclose(parameters[:3], expected_normal, atol=1e-13)
    assert parameters[3] / scale == pytest.approx(
        expected_normal @ (points[0] + shift), abs=1e-12
    )
    assert fitted["weighted_rms"] / scale < 1e-12
    residual, expected_directions, support = surface_distance(moved, fitted)
    np.testing.assert_allclose(residual / scale, 0, atol=1e-12)
    np.testing.assert_allclose(expected_directions, normals, atol=1e-13)
    assert support.all()
    diagnostic = fitted["initialization"]
    assert diagnostic["algorithm"] == "weighted-covariance-plane-v1"
    assert diagnostic["chart"] == "workspace-normal-offset-v1"
    assert diagnostic["observations"] == len(points)
    assert diagnostic["orientation"] == "selected-normals"
    assert diagnostic["normal_projection"] == pytest.approx(1.0)
    assert diagnostic["coverage_ratio"] > 1e-10
    assert diagnostic["normal_gap_ratio"] > 1e-10
    eigenvalues = np.array(diagnostic["normalized_covariance_eigenvalues"])
    assert np.all(np.diff(eigenvalues) >= 0)
    assert diagnostic["length"] / scale > 0
    _ = json.dumps(diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    "hint", [np.full(5, np.nan), np.array([1e100, -1e100, 0, 1e100, -1]), np.zeros(2)]
)
def test_optional_poisoned_starts_cannot_change_a_direct_plane(
    hint: np.ndarray,
) -> None:
    points, normals, _ = independent_grid(np.array([2.0, -3, 4]), noise=0.01)
    weights = np.linspace(0.1, 2.0, len(points))
    absent = fit_seed(points, weights, normals, "plane", None, (-2, 5))
    poisoned = fit_seed(points, weights, normals, "plane", hint, (-2, 5))
    assert poisoned == absent


@pytest.mark.parametrize("normal", [np.array([1.0, 0, 0]), np.array([2.0, -3, 4])])
def test_imported_noisy_plane_uses_all_nonuniform_incident_areas(
    normal: np.ndarray,
) -> None:
    workspace = imported_grid(normal, noise=0.01)
    assert not hasattr(workspace.data, "selection")
    assert np.ptp(workspace.data.weights) > 0
    graph = FeatureGraph(workspace, imported_recipe(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    fitted = results(state)["plane"]
    equation = np.array(fitted["parameters"])
    expected_normal = normal / np.linalg.norm(normal)
    np.testing.assert_allclose(equation[:3], expected_normal, atol=0.01)
    residual = workspace.local @ equation[:3] - equation[3]
    normalized = workspace.data.weights / workspace.data.weights.sum()
    assert len(fitted["residuals"]) == len(workspace.local)
    np.testing.assert_allclose(fitted["residuals"], residual, atol=1e-13)
    assert fitted["weighted_rms"] == pytest.approx(np.sqrt(normalized @ residual**2))
    assert abs(normalized @ residual) < 1e-12
    # Compare the area contributions against an independently explicit loop.
    contributions = np.zeros(len(workspace.local))
    for triangle in workspace.data.triangles:
        a, b, c = workspace.local[triangle]
        contribution = np.linalg.norm(np.cross(b - a, c - a)) / 6
        for vertex in triangle:
            contributions[vertex] += contribution
    np.testing.assert_allclose(workspace.data.weights, contributions, atol=1e-14)
    assert fitted["initialization"]["observations"] == len(workspace.local)


@pytest.mark.parametrize("normal", [np.array([1.0, 0, 0]), np.array([2.0, -3, 4])])
def test_imported_graph_growth_and_saved_recipe_need_no_fixture_metadata(
    normal: np.ndarray,
) -> None:
    workspace = imported_grid(normal)
    graph = FeatureGraph(workspace, imported_recipe(workspace, grow=True))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    fitted = results(state)["plane"]
    assert results(state)["grown"]["ids"] == list(range(12))
    assert set(results(state)["grown"]["added_ids"]) == set(range(12)) - {0, 1, 4, 5}
    selected = [0, 1, 4, 5]
    direct = fit_seed(
        workspace.local[selected],
        workspace.data.weights[selected],
        workspace.data.normals[selected],
        "plane",
        None,
        (-2, 5),
    )
    np.testing.assert_allclose(fitted["parameters"], direct["parameters"], atol=1e-13)
    encoded = json.dumps(state["recipe"], allow_nan=False)
    assert "initial_parameters" not in encoded
    assert "initialization" not in encoded
    replay = FeatureGraph(workspace, Recipe.model_validate_json(encoded))
    reopened = replay.evaluate(str(replay.snapshot()["token"]))
    assert not reopened["errors"]
    assert results(reopened)["grown"] == results(state)["grown"]
    assert results(reopened)["plane"] == fitted


def test_planar_observations_do_not_become_an_imported_cone() -> None:
    workspace = imported_grid(np.array([0.0, 0, 1]))
    payload = imported_recipe(workspace).model_dump()
    payload["nodes"][-1]["kind"] = "cone"
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    with pytest.raises(ConeFitFailure) as failure:
        _ = graph.evaluate(str(graph.snapshot()["token"]))
    assert failure.value.code == "no-valid-candidates"
    assert failure.value.diagnostic["recovery"]["selected"] is None
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize(
    ("points", "code"),
    [
        (np.zeros((2, 3)), "insufficient-observations"),
        (np.ones((4, 3)), "insufficient-coverage"),
        (np.array([[0.0, 0, 0], [1.0, 0, 0], [2.0, 0, 0]]), "insufficient-coverage"),
        (
            np.array(
                [[x, y, z] for x in [-1.0, 1] for y in [-1.0, 1] for z in [-1.0, 1]]
            ),
            "ambiguous-normal",
        ),
    ],
)
def test_insufficient_or_ambiguous_geometry_is_not_published_as_a_unique_plane(
    points: np.ndarray, code: str
) -> None:
    with pytest.raises(PlaneFitFailure) as failure:
        _ = fit_seed(
            points,
            np.ones(len(points)),
            np.tile([0.0, 0, 1], (len(points), 1)),
            "plane",
            None,
            (-2, 5),
        )
    assert failure.value.code == code
    assert failure.value.diagnostic["code"] == code
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


@pytest.mark.parametrize("defect", ["zero", "nonfinite", "shape"])
def test_direct_plane_rejects_unusable_supplied_normals(defect: str) -> None:
    points, normals, _ = independent_grid(np.array([0.0, 0, 1]))
    if defect == "zero":
        normals[0] = 0
    elif defect == "nonfinite":
        normals[0, 0] = np.nan
    else:
        normals = normals[:, :2]
    with pytest.raises(PlaneFitFailure) as failure:
        _ = fit_seed(points, np.ones(12), normals, "plane", None, (-2, 5))
    assert failure.value.code == "invalid-normals"


def test_reversed_normals_reverse_the_equation_without_changing_the_plane() -> None:
    points, normals, _ = independent_grid(np.array([2.0, -3, 4]))
    forward = fit_seed(points, np.ones(12), normals, "plane", None, (-2, 5))
    reverse = fit_seed(points, np.ones(12), -normals, "plane", None, (-2, 5))
    np.testing.assert_allclose(reverse["parameters"], -np.array(forward["parameters"]))
    assert reverse["weighted_rms"] == pytest.approx(forward["weighted_rms"])
    assert reverse["initialization"]["orientation"] == "selected-normals"
    assert reverse["normal_sign"] == 1


def test_cancelling_normal_sign_keeps_unsigned_geometry_but_blocks_directed_growth() -> (
    None
):
    points, normals, triangles = independent_grid(np.array([0.0, 0, -1]))
    normals[::2] *= -1
    fitted = fit_seed(points, np.ones(12), normals, "plane", None, (-2, 5))
    np.testing.assert_allclose(fitted["parameters"][:3], [0, 0, 1], atol=1e-14)
    assert fitted["initialization"]["orientation"] == "ambiguous"
    assert fitted["initialization"]["normal_projection"] < 1e-12
    assert fitted["normal_sign"] == 1
    assert fitted["weighted_rms"] < 1e-13
    with pytest.raises(PlaneFitFailure) as failure:
        _ = connected_growth(
            points, normals, triangles, np.ones(12), [0, 1, 4, 5], [], fitted, 0.1, 20
        )
    assert failure.value.code == "ambiguous-orientation"


def test_graph_keeps_unsigned_plane_and_reports_ambiguous_growth_without_editing_actions() -> (
    None
):
    points = np.array([[0.0, 0, 0], [2.0, 0, 0], [0.0, 2, 0], [2.0, 2, 0]])
    normals = np.array([[0.0, 0, 1], [0.0, 0, 1], [0.0, 0, -1], [0.0, 0, -1]])
    triangles = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
    workspace = prepare_scan(
        io.BytesIO(strict_ply(points, normals, triangles)),
        "ambiguous-direction.ply",
        ScanLimits(2_000_000, 100, 100, 10_000_000),
    )
    recipe = imported_recipe(workspace)
    growth = Growth(
        id="grown",
        label="Ambiguous growth",
        operation="growth",
        seed_fit="plane",
        distance=0.1,
        angle_degrees=20,
    )
    recipe = Recipe(schema_version=2, nodes=[*recipe.nodes, growth], output="grown")
    graph = FeatureGraph(workspace, recipe)
    initial = graph.snapshot()
    with pytest.raises(PlaneFitFailure) as failure:
        _ = graph.evaluate(str(initial["token"]))
    assert failure.value.code == "ambiguous-orientation"
    state = graph.snapshot()
    assert state["recipe"] == initial["recipe"]
    assert state["token"] == initial["token"]
    assert cast(dict[str, str], state["states"])["plane"] == "ready"
    assert cast(dict[str, str], state["states"])["grown"] == "failed"
    assert results(state)["plane"]["initialization"]["orientation"] == "ambiguous"
    assert "grown" not in results(state)
    diagnostic = cast(dict[str, dict[str, Any]], state["diagnostics"])["grown"]
    assert diagnostic["code"] == "ambiguous-orientation"
    _ = json.dumps(diagnostic, allow_nan=False)


def test_explicit_reference_normal_is_exact_even_when_observations_prefer_another_plane() -> (
    None
):
    workspace = imported_grid(np.array([1.0, 0, 0]))
    node = SurfaceFit(
        id="constrained",
        label="Reference-oriented plane",
        operation="fit",
        selections=["selected"],
        kind="plane",
        reference_plane="datum",
    )
    reference = {"normal_display": [0.0, -1.0, 0.0], "plane_equation": [0, -1, 0, 5]}
    before = json.dumps(reference)
    fitted = fit_plane_to_reference(workspace, node, list(range(12)), reference)
    np.testing.assert_array_equal(fitted["parameters"][:3], reference["normal_display"])
    expected_offset = -float(
        workspace.data.weights @ workspace.local[:, 1] / workspace.data.weights.sum()
    )
    assert fitted["parameters"][3] == pytest.approx(expected_offset)
    assert fitted["signed_relative_offset"] == pytest.approx(expected_offset - 5)
    assert json.dumps(reference) == before


def assert_plane_shape(shape: TopoDS_Shape, equation: np.ndarray) -> None:
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == GeomAbs_Plane
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = np.array(
        [
            adaptor.Value(u0 + (u1 - u0) * u, v0 + (v1 - v0) * v).Coord()
            for u, v in [(0, 0), (0.2, 0.7), (1, 1)]
        ]
    )
    np.testing.assert_allclose(samples @ equation[:3], equation[3], atol=1e-7)
    explorer.Next()
    assert not explorer.More()


@pytest.mark.parametrize("normal", [np.array([1.0, 0, 0]), np.array([2.0, -3, 4])])
def test_imported_plane_primitive_patch_and_step_preserve_source_geometry(
    normal: np.ndarray, tmp_path: Path
) -> None:
    workspace = imported_grid(normal)
    graph = FeatureGraph(workspace, imported_recipe(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    fitted = results(state)["plane"]
    equation = np.array(fitted["parameters"])
    geometry = primitive(fitted)
    np.testing.assert_allclose(geometry["axis"], equation[:3], atol=1e-13)
    assert geometry["offset"] == pytest.approx(equation[3])
    assert_plane_shape(surface_patch(fitted, workspace.local), equation)
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=str(state["token"]),
            target="plane",
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        np.testing.assert_array_equal(metadata["transform_local_to_export"], np.eye(4))
        assert metadata["source_sha256"] == workspace.source_sha256
        path = tmp_path / "independent-plane.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    assert_plane_shape(reader.OneShape(), equation)
