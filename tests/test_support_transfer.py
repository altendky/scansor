"""Hard support survives actual imported-feature reuse, refitting and export."""

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
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Cylinder
from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    SurfaceFit,
    UnsupportedSupportTransfer,
)
from experiments.feature_reuse_authoring import ReuseChanges, reconcile_feature_reuse
from experiments.mesh_cone_fit import ConeFitFailure
from experiments.mesh_cylinder_fit import CylinderFitFailure
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.scan_workspace import ScanLimits, ScanWorkspace, prepare_scan
from experiments.support_intent import SupportIntentFailure

ROTATION = np.array([[0.0, 0, -1], [0, 1, 0], [1, 0, 0]])
TRANSLATION = np.array([20.0, 30, -10])
SECOND_ROTATION = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
SECOND_TRANSLATION = np.array([-9.0, 3, 8])
ORIGIN = np.array([3.0, -4, 5])


def paired_scan(kind: str, *, third: bool = False) -> ScanWorkspace:
    angles = np.linspace(-1.4, 1.6, 24)
    angles += 0.04 * np.sin(angles) + 0.02 * np.cos(2 * angles)
    theta, height = np.meshgrid(angles, [-1.0, -0.6, 0.2, 1.1, 2])
    radial = np.column_stack(
        (np.cos(theta.ravel()), np.sin(theta.ravel()), np.zeros(theta.size))
    )
    slope = 0.15 if kind == "cone" else 0.0
    points = (
        ORIGIN
        + height.ravel()[:, None] * [0, 0, 1]
        + (3 + slope * height.ravel())[:, None] * radial
    )
    normals = (radial - slope * np.array([0, 0, 1])) / np.hypot(1, slope)
    triangles: list[tuple[int, int, int]] = []
    for row in range(4):
        for col in range(23):
            a = row * 24 + col
            triangles.extend([(a, a + 1, a + 24), (a + 1, a + 25, a + 24)])
    p = np.vstack((points, points @ ROTATION + TRANSLATION))
    n = np.vstack((normals, normals @ ROTATION))
    t = np.vstack((triangles, np.asarray(triangles) + 120))
    if third:
        p = np.vstack(
            (
                p,
                (points @ ROTATION + TRANSLATION) @ SECOND_ROTATION
                + SECOND_TRANSLATION,
            )
        )
        n = np.vstack((n, normals @ ROTATION @ SECOND_ROTATION))
        t = np.vstack((t, np.asarray(triangles) + 240))
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(p)}\n"
        "property float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
        f"element face {len(t)}\nproperty list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    payload = (
        header
        + b"".join(
            struct.pack("<6f", *point, *normal)
            for point, normal in zip(p, n, strict=True)
        )
        + b"".join(struct.pack("<B3i", 3, *triangle) for triangle in t)
    )
    return prepare_scan(
        io.BytesIO(payload),
        "asymmetric-support-pair.ply",
        ScanLimits(200_000, 1_000, 2_000, 10_000_000),
    )


def base_recipe(workspace: ScanWorkspace, kind: str, *, fixed: bool = False) -> Recipe:
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Independent imported pair",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
            "descriptor": workspace.descriptor,
        },
        {
            "id": "patch",
            "label": "Source observations",
            "operation": "selection",
            "source": "scan",
            "ids": list(range(120)),
        },
        {
            "id": "target",
            "label": "Rotated occurrence",
            "operation": "selection",
            "source": "scan",
            "ids": list(range(120, 240)),
        },
    ]
    if fixed:
        nodes.append(
            {
                "id": "axis",
                "label": "Fixed source axis",
                "operation": "axis",
                "initial_parameters": [3, -4, 0, 0],
            }
        )
    nodes.append(
        {
            "id": "surface",
            "label": "Source finite patch",
            "operation": "fit",
            "kind": kind,
            "selections": ["patch"],
            "axial_domain": [4, 7],
            "axis": "axis" if fixed else None,
        }
    )
    return Recipe.model_validate(
        {"schema_version": 2, "nodes": nodes, "output": "surface"}
    )


def reuse_plan(recipe: Recipe, *, create: bool = True) -> tuple[Recipe, str]:
    plan = reconcile_feature_reuse(
        recipe,
        "reuse",
        ReuseChanges(
            label="Support transfer",
            fits=["surface"],
            reference_selection="patch",
            target_selections=["target"],
            tangent_margin=0.01,
            normal_margin=0.01,
            normal_angle_degrees=10,
        ),
        "independent-support-transfer",
        create=create,
    )
    clone = next(
        node for node in plan.recipe.nodes if node.managed_key == "fit/target/surface"
    )
    return plan.recipe, clone.id


def evaluate(workspace: ScanWorkspace, recipe: Recipe, clone_id: str) -> dict[str, Any]:
    graph = FeatureGraph(workspace, recipe)
    before = graph.snapshot()
    state = graph.evaluate(str(before["token"]), target=clone_id)
    assert not state["errors"]
    assert state["recipe"] == before["recipe"]
    return cast(dict[str, Any], state)


def support_endpoints(fitted: dict[str, Any]) -> np.ndarray:
    geometry = fitted["analytic"]
    return np.asarray(geometry["origin"]) + np.asarray(geometry["axial_domain"])[
        :, None
    ] * np.asarray(geometry["axis"])


def assert_transformed_support(source: dict[str, Any], target: dict[str, Any]) -> None:
    expected = support_endpoints(source) @ ROTATION + TRANSLATION
    np.testing.assert_allclose(support_endpoints(target), expected, atol=2e-5)
    record = target["support_intent"]
    assert record["kind"] == "reference-plane"
    np.testing.assert_allclose(record["normal"], [1, 0, 0], atol=2e-6)
    assert record["offset"] == pytest.approx(20, abs=2e-5)
    assert record["interval"] == [4, 7]
    assert target["weighted_rms"] < 1e-5
    assert "parameters" not in target


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
@pytest.mark.parametrize("fixed", [False, True])
def test_managed_reuse_transforms_authored_plane_and_replays_canonical_fit(
    kind: str, fixed: bool
) -> None:
    workspace = paired_scan(kind)
    recipe, clone_id = reuse_plan(base_recipe(workspace, kind, fixed=fixed))
    clone = next(node for node in recipe.nodes if node.id == clone_id)
    assert isinstance(clone, SurfaceFit) and clone.support_authority == "prototype"
    state = evaluate(workspace, recipe, clone_id)
    assert_transformed_support(state["results"]["surface"], state["results"][clone_id])
    replayed = evaluate(
        workspace,
        Recipe.model_validate_json(json.dumps(state["recipe"], allow_nan=False)),
        clone_id,
    )
    assert replayed["results"] == state["results"]


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_transferred_finite_support_reaches_step_in_target_world_frame(
    kind: str, tmp_path: Path
) -> None:
    workspace = paired_scan(kind)
    recipe, clone_id = reuse_plan(base_recipe(workspace, kind))
    state = evaluate(workspace, recipe, clone_id)
    fitted = state["results"][clone_id]
    bundle = export_cad(
        workspace,
        state,
        CadExportRequest(
            token=state["token"],
            target=clone_id,
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
        ),
    )
    with ZipFile(io.BytesIO(bundle)) as archive:
        metadata = json.loads(archive.read("metadata.json"))
        assert metadata["source_sha256"] == workspace.source_sha256
        path = tmp_path / f"transferred-{kind}.step"
        _ = path.write_bytes(archive.read("model.step"))
    reader = STEPControl_Reader()
    read = cast(Callable[[str], IFSelect_ReturnStatus], cast(Any, reader).ReadFile)
    assert read(str(path)) == IFSelect_RetDone
    assert reader.TransferRoots() == 1
    explorer = TopExp_Explorer(reader.OneShape(), TopAbs_FACE)
    assert explorer.More()
    face = TopoDS.Face(explorer.Current())
    adaptor = BRepAdaptor_Surface(face)
    assert adaptor.GetType() == (GeomAbs_Cone if kind == "cone" else GeomAbs_Cylinder)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    samples = np.asarray(
        [adaptor.Value(u0 + 0.4 * (u1 - u0), v).Coord() for v in (v0, v1)]
    )
    geometry = fitted["analytic"]
    axis = np.asarray(geometry["axis"])
    h = (samples - geometry["origin"]) @ axis
    np.testing.assert_allclose(np.sort(h), geometry["axial_domain"], atol=1e-6)


@pytest.mark.parametrize("domain", [(4.0, 7.0), (24.0, 27.0), None])
def test_reconciliation_preserves_local_target_override_even_when_equal_or_automatic(
    domain: tuple[float, float] | None,
) -> None:
    workspace = paired_scan("cone")
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    payload = recipe.model_dump()
    clone = next(node for node in payload["nodes"] if node["id"] == clone_id)
    clone.update(support_authority="local", axial_domain=domain)
    changed, changed_id = reuse_plan(Recipe.model_validate(payload), create=False)
    target = next(node for node in changed.nodes if node.id == changed_id)
    assert isinstance(target, SurfaceFit)
    assert target.support_authority == "local" and target.axial_domain == domain
    # Auto remains auto and a local Z0 interval cannot silently become the
    # inherited X-normal plane. A local plane may reject this horizontal patch;
    # any admitted alternate geometry still has to honor the local declaration.
    if domain is None:
        state = evaluate(workspace, changed, changed_id)
        assert state["results"][changed_id]["support_intent"]["kind"] == "selection"
    else:
        graph = FeatureGraph(workspace, changed)
        try:
            state = graph.evaluate(str(graph.snapshot()["token"]), target=changed_id)
        except (SupportIntentFailure, ConeFitFailure, CylinderFitFailure) as failure:
            _ = json.dumps(failure.diagnostic, allow_nan=False)
        else:
            fitted = cast(dict[str, Any], state["results"])[changed_id]
            assert fitted["support_intent"] == {
                "kind": "reference-plane",
                "normal": [0.0, 0.0, 1.0],
                "offset": 0.0,
                "interval": list(domain),
            }


@pytest.mark.parametrize("domain", [(4.0, 7.0), (24.0, 27.0), None])
def test_adopting_an_unowned_target_preserves_its_local_support_intent(
    domain: tuple[float, float] | None,
) -> None:
    workspace = paired_scan("cone")
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    payload = recipe.model_dump()
    clone = next(node for node in payload["nodes"] if node["id"] == clone_id)
    clone.update(
        managed_by=None, managed_key=None, support_authority=None, axial_domain=domain
    )
    adopted, identity = reuse_plan(Recipe.model_validate(payload), create=False)
    assert identity == clone_id
    target = next(node for node in adopted.nodes if node.id == identity)
    assert isinstance(target, SurfaceFit)
    assert target.support_authority == "local" and target.axial_domain == domain


def test_legacy_managed_explicit_bounds_cannot_infer_authority_from_tuple_equality() -> (
    None
):
    workspace = paired_scan("cone")
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    payload = recipe.model_dump()
    clone = next(node for node in payload["nodes"] if node["id"] == clone_id)
    clone["support_authority"] = None
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    with pytest.raises(UnsupportedSupportTransfer) as failure:
        _ = graph.evaluate(str(graph.snapshot()["token"]), target=clone_id)
    assert failure.value.diagnostic["code"] == "unsupported-support"
    assert failure.value.diagnostic["issue"] == 264
    _ = json.dumps(failure.value.diagnostic, allow_nan=False)


def test_source_support_edit_invalidates_target_initialization_binding() -> None:
    workspace = paired_scan("cone")
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    before = evaluate(workspace, recipe, clone_id)
    payload = recipe.model_dump()
    source = next(node for node in payload["nodes"] if node["id"] == "surface")
    source["axial_domain"] = [3.5, 7.5]
    changed = evaluate(workspace, Recipe.model_validate(payload), clone_id)
    old_fit, new_fit = before["results"][clone_id], changed["results"][clone_id]
    assert (
        old_fit["initialization"]["recovery"]["binding_sha256"]
        != new_fit["initialization"]["recovery"]["binding_sha256"]
    )
    assert old_fit["support_intent"]["interval"] == [4, 7]
    assert new_fit["support_intent"]["interval"] == [3.5, 7.5]


@pytest.mark.parametrize("kind", ["cylinder", "cone"])
def test_reusing_a_managed_clone_composes_its_original_plane_instead_of_target_z0(
    kind: str,
) -> None:
    workspace = paired_scan(kind, third=True)
    first, first_clone_id = reuse_plan(base_recipe(workspace, kind))
    payload = first.model_dump()
    payload["nodes"].append(
        {
            "id": "third",
            "label": "Third occurrence",
            "operation": "selection",
            "source": "scan",
            "ids": list(range(240, 360)),
        }
    )
    second = reconcile_feature_reuse(
        Recipe.model_validate(payload),
        "second_reuse",
        ReuseChanges(
            label="Reuse transferred feature",
            fits=[first_clone_id],
            reference_selection="target",
            target_selections=["third"],
            tangent_margin=0.01,
            normal_margin=0.01,
            normal_angle_degrees=10,
        ),
        "independent-chained-support-transfer",
        create=True,
    )
    clone = next(
        node
        for node in second.recipe.nodes
        if node.managed_key == f"fit/third/{first_clone_id}"
    )
    state = evaluate(workspace, second.recipe, clone.id)
    source, fitted = state["results"]["surface"], state["results"][clone.id]
    np.testing.assert_allclose(
        support_endpoints(fitted),
        (support_endpoints(source) @ ROTATION + TRANSLATION) @ SECOND_ROTATION
        + SECOND_TRANSLATION,
        atol=3e-5,
    )
    expected_normal = np.array([0.0, 0, 1]) @ ROTATION @ SECOND_ROTATION
    expected_offset = expected_normal @ (
        TRANSLATION @ SECOND_ROTATION + SECOND_TRANSLATION
    )
    np.testing.assert_allclose(
        fitted["support_intent"]["normal"], expected_normal, atol=3e-6
    )
    assert fitted["support_intent"]["offset"] == pytest.approx(
        expected_offset, abs=3e-5
    )
    assert fitted["support_intent"]["interval"] == [4, 7]
    assert fitted["weighted_rms"] < 1e-5
    replayed = evaluate(
        workspace,
        Recipe.model_validate_json(json.dumps(state["recipe"], allow_nan=False)),
        clone.id,
    )
    assert replayed["results"] == state["results"]


def test_registration_settings_edit_invalidates_support_binding_even_at_same_placement() -> (
    None
):
    workspace = paired_scan("cone")
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    before = evaluate(workspace, recipe, clone_id)
    payload = recipe.model_dump()
    owner = next(node for node in payload["nodes"] if node["id"] == "reuse")
    owner["tangent_margin"] = 0.02
    after = evaluate(workspace, Recipe.model_validate(payload), clone_id)
    first, second = before["results"][clone_id], after["results"][clone_id]
    assert (
        first["initialization"]["recovery"]["binding_sha256"]
        != second["initialization"]["recovery"]["binding_sha256"]
    )
    np.testing.assert_allclose(
        support_endpoints(first), support_endpoints(second), atol=1e-10
    )


def test_changed_registration_occurrence_recomputes_plane_and_invalidates_binding() -> (
    None
):
    workspace = paired_scan("cone", third=True)
    recipe, clone_id = reuse_plan(base_recipe(workspace, "cone"))
    before = evaluate(workspace, recipe, clone_id)
    payload = recipe.model_dump()
    target = next(node for node in payload["nodes"] if node["id"] == "target")
    target["ids"] = list(range(240, 360))
    after = evaluate(workspace, Recipe.model_validate(payload), clone_id)
    first, second = before["results"][clone_id], after["results"][clone_id]
    assert (
        first["initialization"]["recovery"]["binding_sha256"]
        != second["initialization"]["recovery"]["binding_sha256"]
    )
    expected = (
        support_endpoints(before["results"]["surface"]) @ ROTATION + TRANSLATION
    ) @ SECOND_ROTATION + SECOND_TRANSLATION
    np.testing.assert_allclose(support_endpoints(second), expected, atol=3e-5)
    assert not np.allclose(support_endpoints(first), support_endpoints(second))
