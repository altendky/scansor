"""Raw RealityScan implementation evidence, without a physical-accuracy claim."""

import builtins
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pytest

import experiments.feature_graph as feature_graph
from experiments.feature_graph import FeatureGraph, Recipe, Selection, SurfaceFit
from experiments.scan_project import AcceptedScan, ScanProject
from experiments.scan_workspace import ScanLimits


def test_raw_nozzle_selected_fits_and_replay_need_no_example_sidecars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    example = Path("examples/nozzle-bayonette-simplified").resolve()
    raw = (example / "nozzle-bayonette-simplified.ply").read_bytes()
    renamed = tmp_path / "renamed-identical-scan.ply"
    _ = renamed.write_bytes(raw)
    limits = ScanLimits(2_000_000, 30_000, 60_000, 100_000_000)

    # Decode this known strict raw layout independently of example metadata and
    # importer arrays. The separate PLY admission suite owns malformed layouts.
    start = raw.index(b"end_header\n") + len(b"end_header\n")
    vertices = np.frombuffer(raw, dtype="<f4", count=24_999 * 6, offset=start).reshape(
        -1, 6
    )
    faces = np.frombuffer(
        raw,
        dtype=np.dtype([("count", "u1"), ("indices", "<i4", (3,))]),
        count=49_994,
        offset=start + vertices.nbytes,
    )
    assert len(raw) == start + vertices.nbytes + faces.nbytes
    assert np.all(faces["count"] == 3)
    xyz = vertices[:, :3].astype(np.float64)
    normals = vertices[:, 3:].astype(np.float64)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    weights = np.zeros(len(xyz))
    for triangle in faces["indices"]:
        a, b, c = xyz[triangle]
        contribution = np.linalg.norm(np.cross(b - a, c - a)) / 6
        for vertex in triangle:
            weights[vertex] += contribution

    original_path_open, original_io_open, original_open = (
        Path.open,
        io.open,
        builtins.open,
    )
    denied: list[Path] = []

    def deny_example(file: Any) -> None:
        if isinstance(file, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(file)).resolve()
            if path == example or example in path.parents:
                denied.append(path)
                raise AssertionError("example files are unavailable after raw capture")

    def guarded_path_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        deny_example(path)
        return original_path_open(path, *args, **kwargs)

    def guarded_io_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        deny_example(file)
        return original_io_open(file, *args, **kwargs)

    def guarded_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        deny_example(file)
        return original_open(file, *args, **kwargs)

    def denied_legacy_workspace(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("legacy example workspace is unavailable")

    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(feature_graph, "example_workspace", denied_legacy_workspace)
    # Prove all guarded entry points reject before normal file lookup, including
    # nonexistent historical names. The actual fit must add no denied attempts.
    for relative in (
        "manifest.json",
        "model.json",
        "selection.json",
        "recipes/cone-plane.json",
        "nozzle-bayonette-simplified.rsInfo",
    ):
        path = example / relative
        for opener in (Path.open, io.open, builtins.open):
            with pytest.raises(AssertionError, match="example files are unavailable"):
                _ = opener(path, "rb")
    denied.clear()

    def open_scan(stream: Any, filename: str) -> AcceptedScan:
        project = ScanProject()
        initial = project.accepted
        assert Recipe.model_validate(initial.graph.snapshot()["recipe"]).nodes == []
        prepared = project.prepare_add(
            stream,
            filename,
            limits,
            generation=initial.generation,
            token=str(initial.graph.snapshot()["token"]),
        )
        return project.commit(prepared)

    accepted = open_scan(io.BytesIO(raw), "raw-nozzle.ply")
    workspace = accepted.workspace
    with pytest.raises(AssertionError, match="legacy example workspace is unavailable"):
        _ = feature_graph.example_workspace(workspace)
    assert workspace.source_sha256 == hashlib.sha256(raw).hexdigest()
    np.testing.assert_array_equal(workspace.local, xyz)
    np.testing.assert_array_equal(workspace.data.triangles, faces["indices"])
    np.testing.assert_array_equal(workspace.data.original_normals, vertices[:, 3:])
    np.testing.assert_allclose(workspace.data.normals, normals, rtol=0, atol=1e-15)
    np.testing.assert_allclose(workspace.data.weights, weights, rtol=1e-14, atol=1e-14)
    assert np.all(weights > 0)
    assert not hasattr(workspace.data, "selection")

    recipe = Recipe.model_validate(accepted.graph.snapshot()["recipe"])
    assert [node.operation for node in recipe.nodes] == ["source"]
    # These preregistered position/normal masks stand for authored selections.
    # The rough lateral patch mixes real geometry: no metrology or residual
    # quality threshold is inferred from its fitted surface.
    authored: dict[str, list[int]] = {
        "top": np.flatnonzero((xyz[:, 2] > 30.5) & (normals[:, 2] > 0.95)).tolist(),
        "side": np.flatnonzero(
            (xyz[:, 2] > 27) & (xyz[:, 2] < 30) & (abs(normals[:, 2]) < 0.1)
        ).tolist(),
    }
    assert {name: len(ids) for name, ids in authored.items()} == {
        "top": 803,
        "side": 5066,
    }
    families: tuple[tuple[str, Literal["plane", "cylinder"]], ...] = (
        ("top", "plane"),
        ("side", "cylinder"),
    )
    for name, kind in families:
        recipe.nodes.extend(
            [
                Selection(
                    id=name + "_selection",
                    label=name + " selection",
                    operation="selection",
                    source="scan",
                    ids=authored[name],
                ),
                SurfaceFit(
                    id=name,
                    label=name + " fit",
                    operation="fit",
                    selections=[name + "_selection"],
                    kind=kind,
                ),
            ]
        )
    recipe = recipe.model_copy(update={"output": "side"})
    saved = json.dumps(recipe.model_dump(mode="json"), allow_nan=False)
    forbidden = {
        "initial_parameters",
        "initial_coordinates",
        "initialization",
        "seed",
        "fit_initial",
        "fit_geometry",
        "solver",
        "fit_frame",
        "frame",
        "model",
        "model_sha256",
        "parameters",
        "analytic",
        "residuals",
        "weighted_rms",
        "coordinate_origin",
        "coordinate_length",
    }

    def check_authored(value: Any) -> None:
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values():
                check_authored(child)
        elif isinstance(value, list):
            for child in value:
                check_authored(child)

    check_authored(json.loads(saved))
    graph = FeatureGraph(workspace, recipe)
    state = graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)
    assert set(cast(dict[str, Any], state["states"]).values()) == {"ready"}
    assert not state["errors"] and not state["diagnostics"]
    results = cast(dict[str, dict[str, Any]], state["results"])
    for name, ids in authored.items():
        fitted = results[name]
        assert fitted["ids"] == ids
        assert np.ptp(weights[ids]) > 0
        selected = xyz[ids]
        if name == "top":
            equation = np.asarray(fitted["parameters"])
            residual = selected @ equation[:3] - equation[3]
        else:
            geometry = fitted["analytic"]
            relative = selected - np.asarray(geometry["origin"])
            axis = np.asarray(geometry["axis"])
            perpendicular = relative - (relative @ axis)[:, None] * axis
            residual = np.linalg.norm(perpendicular, axis=1) - geometry["radius"]
        assert len(fitted["residuals"]) == len(ids)
        np.testing.assert_allclose(
            fitted["residuals"], residual, rtol=1e-12, atol=1e-13
        )
        objective = float(weights[ids] @ residual**2 / weights[ids].sum())
        assert fitted["weighted_rms"] ** 2 == pytest.approx(objective, rel=1e-12)
    assert recipe.model_dump(mode="json") == json.loads(saved)
    _ = json.dumps(results, allow_nan=False)

    with renamed.open("rb") as stream:
        reopened = open_scan(stream, renamed.name)
    assert reopened.workspace.descriptor is not None
    assert workspace.descriptor is not None
    assert reopened.workspace.descriptor.filename != workspace.descriptor.filename
    assert (
        reopened.workspace.descriptor.semantic_record()
        == workspace.descriptor.semantic_record()
    )
    np.testing.assert_array_equal(reopened.workspace.local, workspace.local)
    np.testing.assert_array_equal(
        reopened.workspace.data.weights, workspace.data.weights
    )
    replay = FeatureGraph(reopened.workspace, Recipe.model_validate_json(saved))
    replayed = replay.evaluate(str(replay.snapshot()["token"]), all_actions=True)
    assert replayed["results"] == state["results"]
    assert replayed["recipe"] == state["recipe"]
    assert replayed["token"] == state["token"]
    assert not denied
