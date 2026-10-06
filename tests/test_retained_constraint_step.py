"""Retained constraints and reuse survive reviewed geometry and STEP replay."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.TCollection import TCollection_ExtendedString
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopoDS import TopoDS_Shape

import experiments.nozzle_cad as nozzle_cad
from experiments.feature_graph import FeatureGraph, Recipe
from experiments.nozzle_cad import CadExportRequest, export_cad
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from tests.retained_step_checks import (
    DIRECTION_TOLERANCE,
    LENGTH_TOLERANCE,
    AnalyticSurface,
    assert_surface,
    expected_surface,
    measure_surfaces,
    read_step,
)

RECIPE = Path(
    "examples/repeated-boss-selection/recipes/repeated-boss-reuse-and-alignment-demo.json"
)


def evaluate(graph: FeatureGraph, target: str | None = None) -> dict[str, Any]:
    state = cast(
        dict[str, Any],
        graph.evaluate(
            str(graph.snapshot()["token"]), target, all_actions=target is None
        ),
    )
    assert not state["errors"]
    return state


@dataclass
class RetainedRoundtrip:
    workspace: NozzleWorkspace
    warm: dict[str, Any]
    fresh: dict[str, Any]
    parallel: list[str]
    coincident: list[str]
    equal_radii: list[list[str]]
    upright_cylinders: list[str]
    face_ids: dict[str, str]
    face_request: CadExportRequest
    body_request: CadExportRequest
    repaired_gap: float


@pytest.fixture(scope="module")
def retained(tmp_path_factory: pytest.TempPathFactory) -> RetainedRoundtrip:
    root = tmp_path_factory.mktemp("retained-constraint-step")
    _ = publish_fixture(root / "boss", DEFINITION, realization_ids=("scan-coarse",))
    workspace = NozzleWorkspace(root / "boss/scan-coarse")
    recipe = Recipe.model_validate_json(RECIPE.read_bytes())
    nodes = recipe.model_dump()["nodes"]
    by_id = {node["id"]: node for node in nodes}
    coincidence = next(
        node
        for node in nodes
        if node["operation"] == "plane_relationship"
        and node["relation"] == "coincident"
    )
    parallel = by_id["plate-shoulders-parallel"]["surfaces"]
    equal_radii = [
        node["surfaces"]
        for node in nodes
        if node["operation"] == "equal_radii"
        and all(by_id[key].get("kind") == "cylinder" for key in node["surfaces"])
    ]
    assert len(equal_radii) == 2 and all(len(group) == 4 for group in equal_radii)
    assert any(by_id[key]["managed_by"] for group in equal_radii for key in group)
    upright_axes = {
        by_id[by_id[key]["reference_plane"]]["axis"]
        for key in parallel
        if by_id[key].get("reference_plane") is not None
    }
    upright_cylinders = [
        key
        for group in equal_radii
        for key in group
        if by_id[key]["axis"] in upright_axes
    ]
    assert len(upright_cylinders) == 6
    graph = FeatureGraph(workspace, recipe)
    _ = evaluate(graph, coincidence["id"])
    released = recipe.model_dump()
    next(node for node in released["nodes"] if node["id"] == coincidence["id"])[
        "relation"
    ] = "parallel"
    _ = graph.replace(Recipe.model_validate(released), str(graph.snapshot()["token"]))
    unconstrained = evaluate(graph, coincidence["id"])
    first, second = coincidence["surfaces"]
    gap = abs(
        unconstrained["results"][first]["plane_equation"][3]
        - unconstrained["results"][second]["plane_equation"][3]
    )
    assert gap > 1e-3, "the coincidence repair must correct a measurable offset"
    _ = graph.replace(recipe, str(graph.snapshot()["token"]))
    warm = evaluate(graph)
    assert set(warm["states"].values()) == {"ready"}
    assert graph.ensure_current(warm["token"], all_actions=True) == warm
    fresh = evaluate(FeatureGraph(workspace, Recipe.model_validate(warm["recipe"])))
    assert set(fresh["states"].values()) == {"ready"}
    assert fresh["token"] == warm["token"]

    required = set(parallel) | set(coincidence["surfaces"])
    required.update(key for group in equal_radii for key in group)
    body = next(node for node in nodes if node["operation"] == "body")
    face_ids: dict[str, str] = {}
    for node in nodes:
        if node["operation"] == "arranged_face" and node["id"] in body["faces"]:
            source = node["surface"]["feature"]
            if source in required:
                _ = face_ids.setdefault(source, node["id"])
    assert set(face_ids) == required
    transform = next(node["id"] for node in nodes if node["operation"] == "transform")
    matrix = np.asarray(warm["results"][transform]["matrix"])
    assert not np.allclose(matrix[:3, :3], np.eye(3))
    assert np.linalg.norm(matrix[:3, 3]) > 1
    assert abs(np.linalg.norm(matrix[:3, 0]) - 1) > 1e-3
    return RetainedRoundtrip(
        workspace,
        warm,
        fresh,
        parallel,
        coincidence["surfaces"],
        equal_radii,
        upright_cylinders,
        face_ids,
        CadExportRequest(
            token=warm["token"],
            scope="selected_faces",
            targets=list(face_ids.values()),
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
            transform=transform,
        ),
        CadExportRequest(
            token=warm["token"],
            scope="body",
            target=body["id"],
            units="Millimeters",
            axis_up=False,
            include_mesh=False,
            transform=transform,
        ),
        float(gap),
    )


def expected(
    case: RetainedRoundtrip, state: dict[str, Any]
) -> dict[str, AnalyticSurface]:
    matrix = np.asarray(state["results"][case.face_request.transform]["matrix"])
    return {
        key: expected_surface(state["results"][key], matrix) for key in case.face_ids
    }


def assert_relations(
    case: RetainedRoundtrip, surfaces: dict[str, AnalyticSurface]
) -> None:
    normal = surfaces[case.parallel[0]].direction
    for key in case.parallel:
        np.testing.assert_allclose(
            np.cross(surfaces[key].direction, normal),
            0,
            atol=DIRECTION_TOLERANCE,
            rtol=0,
        )
    first, second = (surfaces[key] for key in case.coincident)
    np.testing.assert_allclose(
        normal @ (first.location - second.location),
        0,
        atol=LENGTH_TOLERANCE,
        rtol=0,
    )
    for group in case.equal_radii:
        radius = surfaces[group[0]].radius
        assert radius is not None
        for key in group:
            actual_radius = surfaces[key].radius
            assert actual_radius is not None
            np.testing.assert_allclose(
                actual_radius, radius, atol=LENGTH_TOLERANCE, rtol=0
            )
    # The fourth boss is deliberately tilted. Only axes constrained through the
    # parallel shoulders' perpendicular reference planes are declared upright.
    for key in case.upright_cylinders:
        np.testing.assert_allclose(
            np.cross(surfaces[key].direction, normal),
            0,
            atol=DIRECTION_TOLERANCE,
            rtol=0,
        )


def measure_named_faces(
    case: RetainedRoundtrip, bundle: bytes, root: Path
) -> dict[str, AnalyticSurface]:
    shapes = read_step(bundle, root)
    nodes = {node["id"]: node for node in case.warm["recipe"]["nodes"]}
    # Locate roots with either Unicode labels or the binding's narrow-string
    # conversion. Geometry coverage must not require the current encoding quirk.
    names: dict[str, str] = {}
    for source, face in case.face_ids.items():
        label = nodes[face]["label"]
        aliases = {label, TCollection_ExtendedString(label).ToExtString()}
        matches = aliases & shapes.keys()
        assert len(matches) == 1, "STEP face must have one identifiable root"
        names[source] = matches.pop()
    assert set(shapes) == set(names.values())
    measurements: dict[str, AnalyticSurface] = {}
    for source in case.face_ids:
        surfaces = measure_surfaces(shapes[names[source]])
        assert len(surfaces) == 1
        measurements[source] = surfaces[0]
    return measurements


def test_retained_constraint_step_roundtrip(
    retained: RetainedRoundtrip, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warm_expected, fresh_expected = (
        expected(retained, retained.warm),
        expected(retained, retained.fresh),
    )
    assert retained.repaired_gap > 1e-3
    for key in warm_expected:
        assert_surface(warm_expected[key], fresh_expected[key])
    for label, state, wanted in (
        ("warm", retained.warm, warm_expected),
        ("fresh", retained.fresh, fresh_expected),
    ):
        measured = measure_named_faces(
            retained,
            export_cad(retained.workspace, state, retained.face_request),
            tmp_path / label,
        )
        for key, actual in measured.items():
            assert_surface(actual, wanted[key])
        assert_relations(retained, measured)
    assert_body_roundtrip(retained, tmp_path / "body")
    # Keep one collected case: work-stealing CI workers must not each rebuild the
    # retained coarse fixture and its warm/fresh Body just for a corruption check.
    for corruption in ("normal", "offset"):
        with monkeypatch.context() as patch:
            assert_corrupt_export(retained, tmp_path / corruption, patch, corruption)


def assert_body_roundtrip(retained: RetainedRoundtrip, tmp_path: Path) -> None:
    state, request = retained.warm, retained.body_request
    nodes = {node["id"]: node for node in state["recipe"]["nodes"]}
    body = nodes[request.target]
    shapes = read_step(export_cad(retained.workspace, state, request), tmp_path)
    assert set(shapes) == {body["label"]}
    shape = shapes[body["label"]]
    assert shape.ShapeType() == TopAbs_SOLID and BRepCheck_Analyzer(shape).IsValid()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    assert props.Mass() > 0
    matrix = np.asarray(state["results"][request.transform]["matrix"])
    scale = float(np.linalg.norm(matrix[:3, 0]))
    # Native volume integration is approximate; use the existing synthetic solid
    # roundtrip's relative allowance. Analytic support checks remain much tighter.
    np.testing.assert_allclose(
        props.Mass(),
        state["results"][request.target]["volume"] * scale**3,
        rtol=1e-6,
        atol=1e-7,
    )
    measured = measure_surfaces(shape)
    assert len(measured) == state["results"][request.target]["face_count"]
    sources = {nodes[key]["surface"]["feature"] for key in body["faces"]}
    wanted = {key: expected_surface(state["results"][key], matrix) for key in sources}
    matched: set[str] = set()
    # Face ordering is not stable across STEP; coincident supports can occur in
    # several disjoint physical cells. Require every actual and expected support.
    for actual in measured:
        matches: set[str] = set()
        for key, support in wanted.items():
            try:
                assert_surface(actual, support)
            except AssertionError:
                continue
            matches.add(key)
        assert matches, "Body STEP contains an unexpected analytic support"
        matched.update(matches)
    assert matched == sources, "Body STEP omitted an expected analytic support"


def assert_corrupt_export(
    retained: RetainedRoundtrip,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    wanted = expected(retained, retained.warm)
    source = retained.coincident[0]
    face_id = retained.face_ids[source]
    support = wanted[source]
    original = cast(
        Callable[[dict[str, TopoDS_Shape], dict[str, str], str], bytes],
        nozzle_cad._step_bytes,  # pyright: ignore[reportPrivateUsage]
    )

    def corrupt(
        patches: dict[str, TopoDS_Shape], names: dict[str, str], units: str
    ) -> bytes:
        transform = gp_Trsf()
        if corruption == "offset":
            transform.SetTranslation(gp_Vec(*(support.direction * 1e-3)))
        else:
            axis = np.cross(
                support.direction, np.eye(3)[np.argmin(np.abs(support.direction))]
            )
            transform.SetRotation(
                gp_Ax1(gp_Pnt(*support.location), gp_Dir(*axis)), 1e-3
            )
        changed = dict(patches)
        changed[face_id] = BRepBuilderAPI_Transform(
            patches[face_id], transform, True
        ).Shape()
        return original(changed, names, units)

    monkeypatch.setattr(nozzle_cad, "_step_bytes", corrupt)
    before = retained.warm["states"].copy()
    measured = measure_named_faces(
        retained,
        export_cad(retained.workspace, retained.warm, retained.face_request),
        tmp_path,
    )
    assert retained.warm["states"] == before and set(before.values()) == {"ready"}
    # Other patches must still pass, so malformed bundles cannot satisfy this test.
    for key in set(measured) - {source}:
        assert_surface(measured[key], wanted[key])
    with pytest.raises(AssertionError):
        assert_surface(measured[source], wanted[source])
    with pytest.raises(AssertionError):
        assert_relations(retained, measured)
