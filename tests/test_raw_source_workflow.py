"""Independent arithmetic and downloaded-artifact failure controls, no UI claims."""

import copy
import io
import json
import struct
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from experiments.browser_static import raw_workflow_expected as oracle
from experiments.feature_graph import Recipe
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits


def ply_bytes(points: Any, triangles: Any, normals: Any | None = None) -> bytes:
    names = (
        ("x", "y", "z", "nx", "ny", "nz") if normals is not None else ("x", "y", "z")
    )
    scalar = "float" if normals is not None else "double"
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(points)}"]
    header.extend(f"property {scalar} {name}" for name in names)
    header.extend(
        [
            f"element face {len(triangles)}",
            "property list uchar int vertex_indices",
            "end_header",
        ]
    )
    rows = np.column_stack((points, normals)) if normals is not None else points
    payload = np.asarray(rows, dtype="<f4" if normals is not None else "<f8").tobytes()
    return (
        ("\n".join(header) + "\n").encode()
        + payload
        + b"".join(struct.pack("<Biii", 3, *row) for row in triangles)
    )


def test_reviewed_evaluation_lifecycle_is_transport_not_published_geometry() -> None:
    graph: dict[str, Any] = dict.fromkeys(oracle.PROJECTION)
    graph["project_generation"] = 3
    expected = oracle.project_graph(graph)
    evaluation = {
        "id": "native-job",
        "sequence": 1,
        "generation": 3,
        "kind": "ensure",
        "state": "complete",
        "roots": ["plane"],
    }
    value = {**graph, "evaluation": evaluation}
    before = copy.deepcopy(value)
    assert oracle.project_graph(value) == expected
    assert oracle.project_graph({**graph, "evaluation": None}) == expected
    assert value == before
    for defect in (
        {"sequence": True},
        {"generation": 4},
        {"kind": "unknown"},
        {"state": "unknown"},
        {"roots": ["plane", "plane"]},
        {"new_acceptance_classification": "rejected"},
    ):
        with pytest.raises(ValueError, match="evaluation lifecycle"):
            _ = oracle.project_graph({**graph, "evaluation": {**evaluation, **defect}})


@pytest.fixture
def raw() -> bytes:
    # Two rings and full incident triangles; individual selected vertices must
    # retain area from faces whose other vertices are outside the selection.
    theta = np.arange(12) * np.pi / 6
    directions = np.tile(
        np.column_stack((np.cos(theta), np.sin(theta), np.zeros(12))), (2, 1)
    )
    points = 2 * directions
    points[:, 2] = np.repeat([-1.0, 2.0], 12)
    faces: list[list[int]] = []
    for i in range(12):
        j = (i + 1) % 12
        faces.extend([[i, j, i + 12], [j, j + 12, i + 12]])
    return ply_bytes(points, faces, directions)


@pytest.fixture
def observation(raw: bytes) -> dict[str, Any]:
    geometry = oracle.decode_ply(raw)
    gesture = {
        "frozen_z_band": [27.8, 29.2],
        "matrix": np.diag([0.2, 0.2, 0.2, 1.0]).reshape(-1, order="F").tolist(),
        "box": {"x": 10, "y": 20, "width": 200, "height": 100},
        "width": 200,
        "height": 100,
        "delivered": [
            {"type": "pointerdown", "x": 115, "y": 0, "button": 0, "pointerId": 1},
            {"type": "pointerup", "x": 160, "y": 130, "button": 0, "pointerId": 1},
        ],
    }
    ids = oracle.projected_ids(geometry, gesture)
    gesture["expected_ids"] = ids
    project = ScanProject()
    empty = project.accepted
    active = project.commit(
        project.prepare_add(
            io.BytesIO(raw),
            "independent.ply",
            ScanLimits(1_000_000, 1000, 1000, 10_000_000),
            generation=0,
            token=str(empty.graph.snapshot()["token"]),
        )
    )
    before = active.graph.snapshot()
    recipe: dict[str, Any] = copy.deepcopy(cast(dict[str, Any], before["recipe"]))
    recipe["nodes"].extend(
        [
            {
                "id": "selection",
                "label": "Actual rows",
                "operation": "selection",
                "source": "scan",
                "ids": ids,
            },
            {
                "id": "fit",
                "label": "Cylinder",
                "operation": "fit",
                "kind": "cylinder",
                "selections": ["selection"],
            },
        ]
    )
    recipe["output"] = "fit"
    state = active.graph.replace(Recipe.model_validate(recipe), str(before["token"]))
    _ = active.graph.evaluate(str(state["token"]), all_actions=True)
    graph: dict[str, Any] = dict(
        active.graph.readiness_snapshot(str(state["token"]), all_actions=True)
    )
    graph.update(project_generation=1, evaluation_running=False)
    replayed = copy.deepcopy(graph)
    replayed["project_generation"] = 3
    metadata = active.workspace.metadata()
    descriptor = metadata["source"]
    checkpoints: dict[str, Any] = {}
    for name, epoch, g in (
        ("imported", 1, graph),
        ("original", 1, graph),
        ("reopened", 3, replayed),
        ("replayed", 3, replayed),
    ):
        checkpoints[name] = {
            "generation": epoch,
            "metadata": {**copy.deepcopy(metadata), "project_generation": epoch},
            "graph": copy.deepcopy(g),
        }
    checkpoints["imported"].update(
        positions=geometry.positions.ravel().tolist(),
        indices=geometry.triangles.ravel().tolist(),
    )
    checkpoints["empty"] = {
        "generation": 2,
        "metadata": {"project_generation": 2, "source": None},
        "graph": {"project_generation": 2, "recipe": {"nodes": []}, "results": {}},
    }
    checkpoints["reopened"]["metadata"]["source"]["filename"] = "renamed.ply"
    checkpoints["replayed"]["metadata"]["source"]["filename"] = "renamed.ply"
    checkpoints["reopened"]["graph"]["states"] = {
        key: "unevaluated" for key in graph["states"]
    }
    checkpoints["reopened"]["graph"]["results"] = {}
    assert isinstance(descriptor, dict)
    return {
        "schema": "scansor-raw-source-workflow-v1",
        "complete": True,
        "page_errors": [],
        "graphs": {"original": graph, "replayed": replayed},
        "graph_generations": {"original": 1, "replayed": 3},
        "checkpoints": checkpoints,
        "gesture": gesture,
        "raw_source": {"sha256": oracle.sha(raw), "bytes": len(raw)},
        "saved_recipe": graph["recipe"],
        "selection_id": "selection",
        "fit_id": "fit",
    }


def test_independent_decode_incident_area_and_original_order() -> None:
    xyz = [[0, 0, 0], [3, 0, 0], [0, 4, 0], [0, 0, 2]]
    triangles = [[0, 1, 2], [0, 3, 1]]
    raw = ply_bytes(xyz, triangles, [[0, 0, 2]] * 4)
    actual = oracle.decode_ply(raw)
    np.testing.assert_array_equal(actual.positions, xyz)
    np.testing.assert_array_equal(actual.triangles, triangles)
    np.testing.assert_array_equal(actual.normals, [[0, 0, 1]] * 4)
    # Triangle areas are6 and3; source-face incident thirds are2 and1.
    np.testing.assert_array_equal(actual.weights, [3, 3, 2, 1])


@pytest.mark.parametrize(
    "fault", ["truncated", "extra", "nontriangle", "index", "normal", "layout"]
)
def test_independent_decoder_rejects_changed_layout_or_rows(
    raw: bytes, fault: str
) -> None:
    changed = raw
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    if fault == "truncated":
        changed = raw[:-1]
    elif fault == "extra":
        changed = raw + b"x"
    elif fault == "nontriangle":
        changed = raw[: start + 24 * 24] + b"\x04" + raw[start + 24 * 24 + 1 :]
    elif fault == "index":
        changed = (
            raw[: start + 24 * 24 + 1]
            + struct.pack("<i", 99)
            + raw[start + 24 * 24 + 5 :]
        )
    elif fault == "normal":
        changed = raw[: start + 12] + b"\0" * 12 + raw[start + 24 :]
    else:
        changed = raw.replace(b"property float nx", b"property float ux")
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.decode_ply(changed)


def test_observations_and_native_replay_use_all_actual_ids(
    raw: bytes, observation: dict[str, Any]
) -> None:
    geometry = oracle.decode_ply(raw)
    result = oracle.validate_observations(observation, geometry, raw)
    ids = observation["gesture"]["expected_ids"]
    assert 0 < len(ids) < len(geometry.positions)
    assert result["original"]["rows"] == result["replayed"]["rows"] == len(ids)
    output: dict[str, Any] = {}
    oracle.native_replay(raw, observation, output, None)
    assert set(output["native_graphs"]) == {"original", "replayed"}
    assert output["native_generations"] == {"original": 1, "replayed": 1}
    assert [
        case["id"]
        for case in json.loads(output["semantic_parity_evidence"]["json"])["cases"]
    ] == [
        "raw-original",
        "raw-replayed",
        "raw-ui-replay-equivalence",
        "raw-native-replay-equivalence",
    ]
    for name in ("original", "replayed"):
        assert "project_generation" not in output["native_graphs"][name]
        binding = output["native_source_bindings"][name]
        assert binding["accepted_generation"] == binding["owner_generation"] == 1
        assert binding["graph_is_owner"] is True
        assert binding["native_snapshot_has_generation"] is False
    assert output["semantic_parity"]["complete"] is True
    assert output["semantic_parity"]["budgets_validated"] is True
    assert output["semantic_parity"]["policy_sha256"] == oracle.POLICY_SHA256


def nested_painted_case(
    observation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes = observation["graphs"]["original"]["recipe"]["nodes"]
    source, selection = copy.deepcopy(nodes[0]), copy.deepcopy(nodes[1])
    ids = selection.pop("ids")
    depth = selection.pop("depth", "through_all")
    selection["entries"] = [
        {
            "id": "original_entry",
            "mode": "painted",
            "scan": {
                "source_sha256": source["source_sha256"],
                "reference_sha256": source["reference_sha256"],
                "vertices": source["descriptor"]["vertices"],
                "descriptor": copy.deepcopy(source["descriptor"]),
            },
            "ids": ids,
            "depth": depth,
            "source_volume": None,
            "volumes": [],
        }
    ]
    return selection, source


def test_independent_oracle_accepts_original_ids_in_nested_painted_entry(
    observation: dict[str, Any],
) -> None:
    selection, source = nested_painted_case(observation)
    membership = oracle.painted_membership(
        selection, source, source["descriptor"]["vertices"], nested_required=True
    )
    assert membership["ids"] == observation["gesture"]["expected_ids"]
    assert "ids" not in selection and "depth" not in selection


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "reference",
        "count",
        "descriptor",
        "mode",
        "volume",
        "source_volume",
        "duplicate_ids",
        "unsorted_ids",
        "flat_alias",
        "multiple_entries",
    ],
)
def test_independent_nested_selection_oracle_rejects_identity_and_membership_faults(
    observation: dict[str, Any], fault: str
) -> None:
    selection, source = nested_painted_case(observation)
    entry = selection["entries"][0]
    if fault == "source":
        entry["scan"]["source_sha256"] = "d" * 64
    elif fault == "reference":
        entry["scan"]["reference_sha256"] = "d" * 64
    elif fault == "count":
        entry["scan"]["vertices"] += 1
    elif fault == "descriptor":
        entry["scan"]["descriptor"]["vertices"] += 1
    elif fault == "mode":
        entry["mode"] = "generated"
    elif fault == "volume":
        entry["volumes"] = [{"id": "unrequested"}]
    elif fault == "source_volume":
        entry["source_volume"] = {"selection": "s", "entry": "e", "volume": "v"}
    elif fault == "duplicate_ids":
        entry["ids"].append(entry["ids"][-1])
    elif fault == "unsorted_ids":
        entry["ids"].reverse()
    elif fault == "flat_alias":
        selection["ids"] = entry["ids"]
    else:
        selection["entries"].append(copy.deepcopy(entry))
    with pytest.raises(ValueError):
        _ = oracle.painted_membership(
            selection, source, source["descriptor"]["vertices"]
        )


@pytest.mark.parametrize(
    "fault",
    [
        "residual",
        "radius",
        "objective",
        "subset",
        "support",
        "classification",
        "nonfinite",
    ],
)
def test_full_row_residual_objective_and_support_mutations_reject(
    raw: bytes, observation: dict[str, Any], fault: str
) -> None:
    geometry = oracle.decode_ply(raw)
    fit = copy.deepcopy(observation["graphs"]["original"]["results"]["fit"])
    if fault == "residual":
        fit["residuals"][0] += 1e-3
    elif fault == "radius":
        fit["analytic"]["radius"] += 1e-3
    elif fault == "objective":
        fit["weighted_rms"] += 1e-3
    elif fault == "subset":
        fit["ids"].pop()
    elif fault == "support":
        fit["analytic"]["axial_domain"][1] += 1e-3
    elif fault == "classification":
        fit["support_classification"]["outside_vertices"] += 1
    else:
        fit["residuals"][0] = "Infinity"
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.fit_arithmetic(fit, geometry, observation["gesture"]["expected_ids"])


@pytest.mark.parametrize(
    "fault",
    [
        "epoch",
        "bool-epoch",
        "metadata-epoch",
        "source",
        "selection",
        "seed",
        "pointer",
        "display",
        "running",
        "failure",
        "unknown-field",
        "band",
        "extra-datum",
    ],
)
def test_accepted_epoch_source_recipe_pointer_and_display_guards(
    raw: bytes, observation: dict[str, Any], fault: str
) -> None:
    report = copy.deepcopy(observation)
    graph = report["graphs"]["original"]
    if fault == "epoch":
        graph["project_generation"] = 999
    elif fault == "bool-epoch":
        graph["project_generation"] = True
    elif fault == "metadata-epoch":
        report["checkpoints"]["original"]["metadata"]["project_generation"] = 999
    elif fault == "source":
        graph["recipe"]["nodes"][0]["source_sha256"] = "0" * 64
    elif fault == "selection":
        graph["recipe"]["nodes"][1]["ids"].pop()
    elif fault == "seed":
        report["saved_recipe"]["nodes"][2]["initial_parameters"] = [1, 2, 3]
    elif fault == "pointer":
        report["gesture"]["delivered"][0]["x"] += 100
    elif fault == "display":
        report["checkpoints"]["imported"]["positions"][0] += 0.1
    elif fault == "running":
        graph["evaluation_running"] = True
    elif fault == "failure":
        graph["required_failures"] = ["fit"]
    elif fault == "band":
        report["gesture"]["frozen_z_band"] = [27.8, 29.3]
    elif fault == "extra-datum":
        graph["recipe"]["nodes"].append({"id": "other", "operation": "point"})
    else:
        graph["new_published_semantics"] = 1
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.validate_observations(report, oracle.decode_ply(raw), raw)


@pytest.mark.parametrize(
    "fault",
    [
        "weights",
        "normals",
        "positions",
        "triangles",
        "epoch",
        "staging",
        "descriptor",
        "original-normals",
    ],
)
def test_actual_worker_vectors_cannot_be_replaced_with_claims(
    raw: bytes, observation: dict[str, Any], fault: str
) -> None:
    g = oracle.decode_ply(raw)
    report = copy.deepcopy(observation)
    report["worker_sources"] = {}
    for name, epoch in report["graph_generations"].items():
        report["worker_sources"][name] = {
            "project_generation": epoch,
            "source_sha256": oracle.sha(raw),
            "original_bytes": len(raw),
            "source_storage": {
                "kind": "BlobInput",
                "backing_kind": "BlobInput",
                "authority_bytes": len(raw),
                "owned_source_payload_bytes": 0,
            },
            "source_replay": {
                "bytes": len(raw),
                "sha256": oracle.sha(raw),
                "max_chunk_bytes": min(len(raw), 65_536),
                "read_calls": 2,
                "eof_verified": True,
            },
            "owned_fs_files": [],
            "owned_fs_bytes": 0,
            "descriptor": copy.deepcopy(
                report["graphs"][name]["recipe"]["nodes"][0]["descriptor"]
            ),
            "original_normals": g.original_normals.tolist()
            if g.original_normals is not None
            else None,
            **{
                key: value.tolist()
                for key, value in [
                    ("positions", g.positions),
                    ("normals", g.normals),
                    ("triangles", g.triangles),
                    ("weights", g.weights),
                ]
            },
        }
    _ = oracle.validate_observations(report, g, raw)
    worker = report["worker_sources"]["original"]
    if fault == "weights":
        worker["weights"][0] *= 2
    elif fault in ("normals", "positions", "triangles"):
        worker[fault][0][0] += 1
    elif fault == "epoch":
        worker["project_generation"] = 999
    elif fault == "descriptor":
        worker["descriptor"]["interpretation_sha256"] = "0" * 64
    elif fault == "original-normals":
        worker["original_normals"][0][0] += 1
    else:
        worker["owned_fs_files"] = ["/stale.ply"]
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.validate_observations(report, g, raw)


@pytest.mark.parametrize(
    "fault",
    [
        "reset-epoch",
        "reopen-epoch",
        "empty-source",
        "empty-actions",
        "ready-reopen",
        "cached-reopen",
        "rename",
    ],
)
def test_reset_and_reopen_do_not_retain_computed_state(
    raw: bytes, observation: dict[str, Any], fault: str
) -> None:
    report = copy.deepcopy(observation)
    empty, reopened = report["checkpoints"]["empty"], report["checkpoints"]["reopened"]
    if fault == "reset-epoch":
        empty["generation"] += 1
    elif fault == "reopen-epoch":
        reopened["metadata"]["project_generation"] += 1
    elif fault == "empty-source":
        empty["metadata"]["source"] = report["checkpoints"]["original"]["metadata"][
            "source"
        ]
    elif fault == "empty-actions":
        empty["graph"]["recipe"]["nodes"] = report["saved_recipe"]["nodes"]
    elif fault == "ready-reopen":
        reopened["graph"]["states"]["fit"] = "ready"
    elif fault == "cached-reopen":
        reopened["graph"]["results"]["fit"] = report["graphs"]["original"]["results"][
            "fit"
        ]
    else:
        reopened["metadata"]["source"]["filename"] = report["checkpoints"]["imported"][
            "metadata"
        ]["source"]["filename"]
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.validate_observations(report, oracle.decode_ply(raw), raw)


def test_native_replay_keeps_full_failure_receipts_and_enforces_budget(
    raw: bytes, observation: dict[str, Any]
) -> None:
    report = copy.deepcopy(observation)
    for name in ("original", "replayed"):
        report["graphs"][name]["results"]["fit"]["initialization"]["recovery"][
            "attempts"
        ][0]["evaluations"] = 513
    output: dict[str, Any] = {}
    with pytest.raises(AssertionError):
        oracle.native_replay(raw, report, output, None)
    assert set(output["native_graphs"]) == {"original", "replayed"}
    assert output["semantic_parity"]["complete"] is False
    assert len(json.loads(output["semantic_parity_evidence"]["json"])["cases"]) == 4


@pytest.mark.parametrize("fault", ["sha", "bytes", "name"])
def test_downloaded_bytes_are_bound(tmp_path: Path, fault: str) -> None:
    path = tmp_path / "actions.json"
    raw = b'{"nodes":[]}'
    _ = path.write_bytes(raw)
    record = {
        "path": str(path),
        "name": path.name,
        "bytes": len(raw),
        "sha256": oracle.sha(raw),
    }
    assert oracle.checked_download(path, record) == raw
    record[fault if fault != "sha" else "sha256"] = 0
    with pytest.raises(ValueError):
        _ = oracle.checked_download(path, record)


def test_verified_package_context_checks_members_before_import(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    source = b"raise AssertionError('data must not execute while verifying')\n"
    with zipfile.ZipFile(runtime / "scansor.zip", "w") as archive:
        archive.writestr("experiments/sentinel.py", source)
    raw = (runtime / "scansor.zip").read_bytes()
    manifest = {
        "assets": [
            {"path": "scansor.zip", "bytes": len(raw), "sha256": oracle.sha(raw)}
        ],
        "source_files": [
            {"archive_path": "experiments/sentinel.py", "sha256": oracle.sha(source)}
        ],
    }
    _ = (runtime / "manifest.json").write_text(json.dumps(manifest))
    with oracle.source_context(runtime, tmp_path) as root:
        assert (
            root is not None
            and (root / "experiments/sentinel.py").read_bytes() == source
        )
    manifest["source_files"][0]["sha256"] = "0" * 64
    _ = (runtime / "manifest.json").write_text(json.dumps(manifest))
    with (
        pytest.raises(ValueError, match="member differs"),
        oracle.source_context(runtime, tmp_path),
    ):
        pytest.fail("invalid packaged source reached import boundary")


def test_preloaded_checkout_modules_cannot_claim_packaged_native_origin(
    tmp_path: Path, raw: bytes, observation: dict[str, Any]
) -> None:
    # This module has imported the checkout core already. Moving sys.path must
    # never silently reinterpret those existing modules as packaged sources.
    output: dict[str, Any] = {}
    with pytest.raises(ValueError, match="checkout instead of packaged source"):
        oracle.native_replay(raw, observation, output, tmp_path)
    assert output == {}


@pytest.fixture
def cad_bundle(
    tmp_path: Path,
    raw: bytes,
    observation: dict[str, Any],
    request: pytest.FixtureRequest,
) -> bytes:
    # OCP constructs this independent analytic cylinder directly; no Scansor
    # transform, patch, export or fit result is used as its geometry oracle.
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Cylinder, gp_Dir, gp_Pnt
    from OCP.IFSelect import IFSelect_RetDone, IFSelect_ReturnStatus
    from OCP.STEPControl import (
        STEPControl_AsIs,
        STEPControl_StepModelType,
        STEPControl_Writer,
    )

    face = BRepBuilderAPI_MakeFace(
        gp_Cylinder(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 2),
        0,
        getattr(request, "param", 2 * np.pi),
        -1,
        2,
    ).Face()
    writer = STEPControl_Writer()
    transfer = cast(
        Callable[[Any, STEPControl_StepModelType], IFSelect_ReturnStatus],
        cast(Any, writer).Transfer,
    )
    assert transfer(face, STEPControl_AsIs) == IFSelect_RetDone
    step = tmp_path / "independent.step"
    assert writer.Write(str(step)) == IFSelect_RetDone
    g = oracle.decode_ply(raw)
    graph = observation["graphs"]["original"]
    metadata = {
        "revision": "scansor-cad-export-v1",
        "recipe": graph["recipe"],
        "source_sha256": oracle.sha(raw),
        "units": "Millimeters",
        "geometry_mode": "separate_faces",
        "sewn": False,
        "solid": False,
        "export": {
            "token": graph["token"],
            "scope": "target",
            "target": "fit",
            "axis_up": False,
            "transform": None,
            "include_mesh": True,
            "units": "Millimeters",
        },
        "transform_local_to_export": np.eye(4).tolist(),
        "objects": [{"feature": "fit", "surface_kind": "cylinder"}],
        "reference_mesh": {
            "file": "reference.ply",
            "vertices": len(g.positions),
            "triangles": len(g.triangles),
        },
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model.step", step.read_bytes())
        archive.writestr("metadata.json", json.dumps(metadata))
        archive.writestr("reference.ply", ply_bytes(g.positions, g.triangles))
    return buffer.getvalue()


def test_actual_step_reference_mesh_and_metadata_are_independent(
    tmp_path: Path, raw: bytes, observation: dict[str, Any], cad_bundle: bytes
) -> None:
    proof = oracle.cad_readback(
        cad_bundle, observation, oracle.decode_ply(raw), tmp_path
    )
    assert proof["valid"] is True and proof["family"] == "cylinder"
    assert proof["radius"] == pytest.approx(2, abs=1e-7)
    np.testing.assert_allclose(
        proof["world_endpoint_centers"], [[0, 0, -1], [0, 0, 2]], rtol=0, atol=1e-7
    )
    assert proof["angular_span"] == pytest.approx(2 * np.pi, abs=1e-7)


@pytest.mark.parametrize("cad_bundle", [np.pi], indirect=True)
def test_partial_cylinder_wedge_cannot_pass_radius_and_caps_only(
    tmp_path: Path, raw: bytes, observation: dict[str, Any], cad_bundle: bytes
) -> None:
    # This has the correct infinite cylinder, radius and world caps; only the
    # angular extent is wrong, so distance/end-height samples alone are vacuous.
    with pytest.raises(AssertionError):
        _ = oracle.cad_readback(
            cad_bundle, observation, oracle.decode_ply(raw), tmp_path
        )


@pytest.mark.parametrize(
    "fault",
    [
        "step",
        "radius",
        "bounds",
        "mesh-row",
        "mesh-face",
        "source",
        "recipe",
        "transform",
        "target",
        "units",
        "extra",
    ],
)
def test_actual_cad_readback_rejects_physical_and_identity_changes(
    tmp_path: Path,
    raw: bytes,
    observation: dict[str, Any],
    cad_bundle: bytes,
    fault: str,
) -> None:
    report = copy.deepcopy(observation)
    with zipfile.ZipFile(io.BytesIO(cad_bundle)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["metadata.json"])
    if fault == "step":
        entries["model.step"] = b"invalid STEP"
    elif fault == "radius":
        report["graphs"]["original"]["results"]["fit"]["analytic"]["radius"] += 1e-3
    elif fault == "bounds":
        report["graphs"]["original"]["results"]["fit"]["analytic"]["axial_domain"][
            1
        ] += 1e-3
    elif fault in ("mesh-row", "mesh-face"):
        g = oracle.decode_ply(raw)
        points, triangles = g.positions.copy(), g.triangles.copy()
        if fault == "mesh-row":
            points[0, 0] += 1e-3
        else:
            triangles[0, 0] = 1
        entries["reference.ply"] = ply_bytes(points, triangles)
    elif fault == "source":
        metadata["source_sha256"] = "0" * 64
    elif fault == "recipe":
        metadata["recipe"]["nodes"][0]["label"] = "changed"
    elif fault == "transform":
        metadata["transform_local_to_export"][0][3] = 1
    elif fault == "target":
        metadata["export"]["target"] = "other"
    elif fault == "units":
        metadata["units"] = "Inches"
    else:
        entries["unexpected.json"] = b"{}"
    entries["metadata.json"] = json.dumps(metadata).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with pytest.raises((ValueError, AssertionError)):
        _ = oracle.cad_readback(
            buffer.getvalue(), report, oracle.decode_ply(raw), tmp_path
        )


def test_reference_failure_is_retained_in_fresh_output(
    tmp_path: Path,
    raw: bytes,
    observation: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path, report_path, output = (
        tmp_path / "raw.ply",
        tmp_path / "report.json",
        tmp_path / "result.json",
    )
    _ = raw_path.write_bytes(raw)
    observation["complete"] = False
    _ = report_path.write_text(json.dumps(observation))
    monkeypatch.setattr(oracle, "RAW_SHA256", oracle.sha(raw))
    result = oracle.verify(raw_path, report_path, tmp_path / "missing.zip", output)
    assert (
        result["complete"] is False
        and "frozen raw row counts changed" in result["failure"]
    )
    assert json.loads(output.read_text()) == result
    with pytest.raises(ValueError, match="fresh"):
        _ = oracle.verify(raw_path, report_path, tmp_path / "missing.zip", output)
