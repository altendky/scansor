"""Independent scan admission, source binding and atomic project transactions."""

import hashlib
import io
import json
import struct
from pathlib import Path
from threading import Event, Thread
from typing import Any, cast, override

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe, Source
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_project import ScanProject
from experiments.scan_workspace import (
    ScanCancelled,
    ScanImportError,
    ScanLimits,
    ScanWorkspace,
    prepare_scan,
)
from tests.scan_source_fixture import read_source_bytes

EXAMPLE = Path("examples/nozzle-bayonette-simplified")


def mesh_bytes(
    positions: tuple[tuple[float, float, float], ...] = (
        (10.0, -3.0, 7.0),
        (12.0, -3.0, 7.0),
        (10.0, 0.0, 7.0),
    ),
    normals: tuple[tuple[float, float, float], ...] | None = None,
    faces: tuple[tuple[int, ...], ...] = ((0, 1, 2),),
    *,
    include_normals: bool = True,
) -> bytes:
    """Construct a generated fixture without exercising Scansor's writer."""
    if normals is None:
        normals = ((0.0, 0.0, 2.0),) * len(positions)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        + "comment Independently generated test geometry\n"
        + f"element vertex {len(positions)}\n"
        + "property float x\nproperty float y\nproperty float z\n"
        + (
            "property float nx\nproperty float ny\nproperty float nz\n"
            if include_normals
            else ""
        )
        + f"element face {len(faces)}\n"
        + "property list uchar int vertex_indices\nend_header\n"
    ).encode("ascii")
    vertices = b"".join(
        struct.pack("<6f", *position, *normal)
        if include_normals
        else struct.pack("<3f", *position)
        for position, normal in zip(positions, normals, strict=True)
    )
    triangles = b"".join(
        struct.pack("<B", len(face)) + struct.pack(f"<{len(face)}i", *face)
        for face in faces
    )
    return header + vertices + triangles


@pytest.fixture
def limits() -> ScanLimits:
    return ScanLimits(
        max_source_bytes=2_000_000,
        max_vertices=30_000,
        max_faces=60_000,
        max_estimated_bytes=100_000_000,
        chunk_bytes=65_536,
    )


def source_recipe(workspace: ScanWorkspace) -> Recipe:
    assert workspace.descriptor is not None
    assert workspace.source_sha256 is not None
    return Recipe(
        schema_version=2,
        nodes=[
            Source(
                id="scan",
                label="Imported scan",
                operation="source",
                source_sha256=workspace.source_sha256,
                reference_sha256=workspace.reference_sha256,
                descriptor=workspace.descriptor,
            )
        ],
        output="scan",
    )


def project_candidate(project: ScanProject, limits: ScanLimits, **kwargs: Any) -> Any:
    accepted = project.accepted
    return project.prepare_add(
        io.BytesIO(mesh_bytes()),
        "generated.ply",
        limits,
        generation=accepted.generation,
        token=str(accepted.graph.snapshot()["token"]),
        **kwargs,
    )


def recipe_record(state: dict[str, object]) -> dict[str, Any]:
    return cast(dict[str, Any], state["recipe"])


def test_generated_scan_preserves_source_coordinates_and_byte_identity(
    limits: ScanLimits,
) -> None:
    original = mesh_bytes()
    workspace = prepare_scan(io.BytesIO(original), "generated.ply", limits)
    descriptor = workspace.descriptor
    assert descriptor is not None
    assert descriptor.source_sha256 == hashlib.sha256(original).hexdigest()
    assert descriptor.source_bytes == len(original)
    assert descriptor.vertices == 3
    assert descriptor.triangles == 1
    assert read_source_bytes(workspace) == original
    np.testing.assert_array_equal(
        workspace.data.xyz, [[10, -3, 7], [12, -3, 7], [10, 0, 7]]
    )
    np.testing.assert_array_equal(workspace.local, workspace.data.xyz)
    np.testing.assert_array_equal(workspace.origin, np.zeros(3))
    np.testing.assert_array_equal(workspace.frame, np.eye(3))
    np.testing.assert_array_equal(workspace.data.original_normals, [[0, 0, 2]] * 3)
    np.testing.assert_array_equal(workspace.data.normals, [[0, 0, 1]] * 3)
    np.testing.assert_array_equal(workspace.data.weights, [1, 1, 1])
    metadata = workspace.metadata()
    assert metadata["vertices"] == 3
    assert metadata["triangles"] == 1
    assert "session" not in metadata
    assert "axial_domain" not in metadata


def test_raw_retained_realityscan_file_needs_no_sidecars(limits: ScanLimits) -> None:
    original = (EXAMPLE / "nozzle-bayonette-simplified.ply").read_bytes()
    workspace = prepare_scan(io.BytesIO(original), "renamed.ply", limits)
    assert workspace.descriptor is not None
    assert workspace.source_sha256 == (
        "537880eb7e7e20d515cb838a0acfdc19d5f661bdb6ae81e80328c8490ad82358"
    )
    assert workspace.descriptor.vertices == 24_999
    assert workspace.descriptor.triangles == 49_994
    assert np.all(workspace.data.weights > 0)
    graph = FeatureGraph(workspace, source_recipe(workspace))
    state = graph.evaluate(str(graph.snapshot()["token"]))
    assert not state["errors"]
    assert state["states"] == {"scan": "ready"}


def test_filename_is_only_a_locating_hint(limits: ScanLimits) -> None:
    original = mesh_bytes()
    first = prepare_scan(io.BytesIO(original), "first.ply", limits)
    renamed = prepare_scan(io.BytesIO(original), "renamed.ply", limits)
    assert first.descriptor is not None
    assert renamed.descriptor is not None
    assert first.descriptor.filename != renamed.descriptor.filename
    assert first.descriptor.semantic_record() == renamed.descriptor.semantic_record()
    assert first.reference_sha256 == renamed.reference_sha256
    assert (
        first.descriptor.interpretation_sha256
        == renamed.descriptor.interpretation_sha256
    )


def test_short_reads_preserve_exact_source_with_bounded_requests(
    limits: ScanLimits,
) -> None:
    original = mesh_bytes()

    class ShortReadStream(io.BytesIO):
        largest_request: int = 0

        @override
        def read(self, size: int | None = -1) -> bytes:
            assert size is not None and 0 < size <= limits.chunk_bytes
            self.largest_request = max(self.largest_request, size)
            return super().read(min(size, 7))

    stream = ShortReadStream(original)
    workspace = prepare_scan(stream, "short-reads.ply", limits)
    assert stream.largest_request > 7
    assert read_source_bytes(workspace) == original
    assert workspace.source_sha256 == hashlib.sha256(original).hexdigest()
    np.testing.assert_array_equal(workspace.data.weights, [1, 1, 1])


@pytest.mark.parametrize(
    "original",
    [
        mesh_bytes(include_normals=False),
        mesh_bytes(normals=((0, 0, 0), (0, 0, 1), (0, 0, 1))),
        mesh_bytes(normals=((float("nan"), 0, 1), (0, 0, 1), (0, 0, 1))),
        mesh_bytes(positions=((float("inf"), 0, 0), (1, 0, 0), (0, 1, 0))),
        mesh_bytes(faces=((-1, 1, 2),)),
        mesh_bytes(faces=((0, 1, 3),)),
        mesh_bytes(faces=((0, 1),)),
        mesh_bytes()[:-1],
        mesh_bytes() + b"unexpected trailing bytes",
        mesh_bytes().replace(b"binary_little_endian", b"binary_big_endian"),
        mesh_bytes().replace(b"property float nx", b"property float red"),
    ],
    ids=[
        "missing-normals",
        "zero-normal",
        "nonfinite-normal",
        "nonfinite-position",
        "negative-index",
        "index-past-end",
        "nontriangle",
        "truncated",
        "trailing",
        "big-endian",
        "unsupported-attribute",
    ],
)
def test_invalid_or_unsupported_sources_are_rejected(
    limits: ScanLimits, original: bytes
) -> None:
    with pytest.raises(ScanImportError) as failure:
        _ = prepare_scan(io.BytesIO(original), "bad.ply", limits)
    assert failure.value.code


@pytest.mark.parametrize(
    "changed",
    [
        {"max_source_bytes": 1},
        {"max_vertices": 2},
        {"max_faces": 0},
        {"max_estimated_bytes": 1},
    ],
)
def test_explicit_resource_limits_reject_without_changing_project(
    limits: ScanLimits, changed: dict[str, int]
) -> None:
    settings = {
        "max_source_bytes": limits.max_source_bytes,
        "max_vertices": limits.max_vertices,
        "max_faces": limits.max_faces,
        "max_estimated_bytes": limits.max_estimated_bytes,
        "chunk_bytes": limits.chunk_bytes,
    }
    settings.update(changed)
    project = ScanProject()
    accepted = project.accepted
    with pytest.raises(ScanImportError):
        _ = project_candidate(project, ScanLimits(**settings))
    assert project.accepted is accepted


def test_degenerate_faces_keep_rows_and_contribute_no_area(limits: ScanLimits) -> None:
    original = mesh_bytes(
        positions=((0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 20)),
        faces=((0, 1, 2), (0, 1, 2), (0, 0, 1)),
    )
    workspace = prepare_scan(io.BytesIO(original), "generated.ply", limits)
    assert workspace.descriptor is not None
    assert workspace.descriptor.vertices == 4
    assert workspace.descriptor.triangles == 3
    np.testing.assert_array_equal(workspace.data.weights, [2, 2, 2, 0])
    np.testing.assert_array_equal(workspace.data.triangles[-1], [0, 0, 1])
    assert workspace.diagnostics.repeated_index_faces == 1
    assert workspace.diagnostics.zero_area_faces == 0
    assert workspace.diagnostics.isolated_vertices == 1
    assert workspace.diagnostics.positive_area_vertices == 3
    positions, indices = workspace.display_buffers()
    assert np.frombuffer(positions, dtype="<f4").reshape(-1, 3).shape == (4, 3)
    np.testing.assert_array_equal(
        np.frombuffer(indices, dtype="<u4").reshape(-1, 3), [[0, 1, 2], [0, 1, 2]]
    )
    payload = source_recipe(workspace).model_dump()
    payload["nodes"].append(
        {
            "id": "isolated",
            "label": "Isolated",
            "operation": "selection",
            "source": "scan",
            "ids": [3],
        }
    )
    payload["output"] = "isolated"
    with pytest.raises(ValueError, match="positive incident area"):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))


def test_authoritative_arrays_are_readonly_and_owned(limits: ScanLimits) -> None:
    stream = io.BytesIO(mesh_bytes())
    workspace = prepare_scan(stream, "generated.ply", limits)
    before = read_source_bytes(workspace)
    _ = stream.seek(0)
    _ = stream.write(b"corrupted input")
    assert read_source_bytes(workspace) == before
    for array in (
        workspace.data.xyz,
        workspace.data.normals,
        workspace.data.original_normals,
        workspace.data.weights,
        workspace.data.triangles,
        workspace.origin,
        workspace.frame,
    ):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.flat[0] = 99
    positions, indices = workspace.display_buffers()
    np.testing.assert_array_equal(
        np.frombuffer(positions, dtype="<f4").reshape(-1, 3), workspace.data.xyz
    )
    np.testing.assert_array_equal(np.frombuffer(indices, dtype="<u4"), [0, 1, 2])


def test_empty_project_evaluation_and_readiness_are_noops() -> None:
    graph = FeatureGraph(ScanWorkspace.empty(), Recipe(nodes=[], output=None))
    token = str(graph.snapshot()["token"])
    for state in (
        graph.evaluate(token),
        graph.evaluate(token, all_actions=True),
        graph.readiness_snapshot(token),
        graph.ensure_current(token),
    ):
        assert recipe_record(state)["nodes"] == []
        assert recipe_record(state)["output"] is None
        assert not state["errors"]
    with pytest.raises(ValueError):
        _ = graph.evaluate(token, targets=["missing"])


def test_generic_source_requires_matching_descriptor_and_single_source(
    limits: ScanLimits,
) -> None:
    workspace = prepare_scan(io.BytesIO(mesh_bytes()), "generated.ply", limits)
    recipe = source_recipe(workspace)
    duplicate = recipe.nodes[0].model_copy(update={"id": "other", "label": "Other"})
    with pytest.raises(ValueError):
        _ = FeatureGraph(
            workspace,
            Recipe(schema_version=2, nodes=[*recipe.nodes, duplicate], output="scan"),
        )
    payload = recipe.model_dump()
    payload["nodes"][0]["descriptor"]["vertices"] = 4
    with pytest.raises(ValueError):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))
    payload = recipe.model_dump()
    del payload["nodes"][0]["descriptor"]
    with pytest.raises(ValueError):
        _ = FeatureGraph(workspace, Recipe.model_validate(payload))
    with pytest.raises(ValueError):
        _ = FeatureGraph(ScanWorkspace.empty(), recipe)


def test_project_commits_source_graph_display_as_one_generation(
    limits: ScanLimits,
) -> None:
    project = ScanProject()
    initial = project.accepted
    candidate = project_candidate(project, limits)
    assert project.accepted is initial
    accepted = project.commit(candidate)
    assert project.accepted is accepted
    assert accepted.generation > initial.generation
    assert accepted.workspace.source_sha256 == hashlib.sha256(mesh_bytes()).hexdigest()
    assert len(recipe_record(accepted.graph.snapshot())["nodes"]) == 1
    np.testing.assert_array_equal(
        np.frombuffer(accepted.positions, dtype="<f4").reshape(-1, 3),
        accepted.workspace.local,
    )
    np.testing.assert_array_equal(
        np.frombuffer(accepted.indices, dtype="<u4"), [0, 1, 2]
    )
    with pytest.raises(ValueError):
        _ = project_candidate(project, limits)


def test_competing_and_foreign_candidates_cannot_overwrite_project(
    limits: ScanLimits,
) -> None:
    project = ScanProject()
    first = project_candidate(project, limits)
    second = project_candidate(project, limits)
    other = ScanProject()
    with pytest.raises(ValueError):
        _ = other.commit(first)
    accepted = project.commit(first)
    with pytest.raises(ValueError):
        _ = project.commit(second)
    assert project.accepted is accepted


def test_stale_generation_and_graph_token_reject_preparation(
    limits: ScanLimits,
) -> None:
    project = ScanProject()
    accepted = project.accepted
    for generation, token in (
        (accepted.generation + 1, str(accepted.graph.snapshot()["token"])),
        (accepted.generation, "not-the-current-token"),
    ):
        with pytest.raises(ValueError):
            _ = project.prepare_add(
                io.BytesIO(mesh_bytes()),
                "generated.ply",
                limits,
                generation=generation,
                token=token,
            )
    assert project.accepted is accepted


def test_cancellation_discards_candidate_and_preserves_accepted_project(
    limits: ScanLimits,
) -> None:
    project = ScanProject()
    initial = project.accepted
    with pytest.raises(ScanCancelled):
        _ = project_candidate(project, limits, cancelled=lambda: True)
    candidate = project_candidate(project, limits)
    with pytest.raises(ScanCancelled):
        _ = project.commit(candidate, cancelled=lambda: True)
    assert project.accepted is initial


def test_progress_can_cancel_during_preparation(limits: ScanLimits) -> None:
    project = ScanProject()
    initial = project.accepted
    phases: list[str] = []

    def progress(update: Any) -> None:
        assert 0 <= update.completed <= update.total
        phases.append(update.phase)

    with pytest.raises(ScanCancelled):
        _ = project_candidate(
            project,
            limits,
            progress=progress,
            cancelled=lambda: "vertices" in phases,
        )
    assert "vertices" in phases
    assert project.accepted is initial


def test_invalid_payload_preserves_accepted_project(limits: ScanLimits) -> None:
    project = ScanProject()
    initial = project.accepted
    with pytest.raises(ScanImportError):
        _ = project.prepare_add(
            io.BytesIO(mesh_bytes(faces=((0, 1, 9),))),
            "bad.ply",
            limits,
            generation=initial.generation,
            token=str(initial.graph.snapshot()["token"]),
        )
    assert project.accepted is initial


def test_modified_candidate_graph_cannot_be_committed(limits: ScanLimits) -> None:
    project = ScanProject()
    initial = project.accepted
    candidate = project_candidate(project, limits)
    graph = candidate.accepted.graph
    snapshot = graph.snapshot()
    recipe = recipe_record(snapshot)
    recipe["nodes"][0]["label"] = "Modified after preparation"
    _ = graph.replace(Recipe.model_validate(recipe), str(snapshot["token"]))
    with pytest.raises(ValueError):
        _ = project.commit(candidate)
    assert project.accepted is initial


def test_candidate_cannot_change_between_token_check_and_publication(
    limits: ScanLimits, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = ScanProject()
    candidate = project_candidate(project, limits)
    graph = candidate.accepted.graph
    snapshot = graph.snapshot()
    recipe = recipe_record(snapshot)
    recipe["nodes"][0]["label"] = "Concurrent change"
    mutation = Recipe.model_validate(recipe)
    token = str(snapshot["token"])
    snapshot_started = Event()
    mutation_attempted = Event()
    mutation_finished = Event()
    mutated_before_publication: list[bool] = []
    errors: list[BaseException] = []
    original_snapshot = graph.snapshot

    def paused_snapshot(if_revision: int | None = None) -> dict[str, object]:
        # Deliberately retain the old token after the snapshot's own lock ends.
        state = original_snapshot(if_revision)
        if snapshot_started.is_set():
            return state
        snapshot_started.set()
        assert mutation_attempted.wait(timeout=5)
        mutated_before_publication.append(mutation_finished.wait(timeout=1))
        return state

    def mutate() -> None:
        try:
            assert snapshot_started.wait(timeout=5)
            mutation_attempted.set()
            _ = graph.replace(mutation, token)
        except BaseException as error:
            errors.append(error)
        finally:
            mutation_finished.set()

    monkeypatch.setattr(graph, "snapshot", paused_snapshot)
    mutator = Thread(target=mutate, daemon=True)
    mutator.start()
    try:
        accepted = project.commit(candidate)
    finally:
        # Join after commit releases the publication lock; the mutator never
        # reads project state while it owns the graph lock.
        snapshot_started.set()
        mutator.join(timeout=5)
    assert not mutator.is_alive()
    assert not errors
    assert mutated_before_publication == [False]
    assert accepted is project.accepted


def test_evaluation_prevents_commit_and_reset_is_atomic(limits: ScanLimits) -> None:
    project = ScanProject()
    candidate = project_candidate(project, limits)
    with project.evaluation() as active:
        assert active is project.accepted
        with pytest.raises(ValueError):
            _ = project.commit(candidate)
    accepted = project.commit(candidate)
    token = str(accepted.graph.snapshot()["token"])
    with pytest.raises(ValueError):
        _ = project.reset(generation=accepted.generation, token="stale")
    assert project.accepted is accepted
    reset = project.reset(generation=accepted.generation, token=token)
    assert reset.generation > accepted.generation
    assert recipe_record(reset.graph.snapshot())["nodes"] == []
    assert reset.positions.size == 0
    assert reset.indices.size == 0


def test_example_binding_and_geometry_survive_common_loader(limits: ScanLimits) -> None:
    example = NozzleWorkspace(EXAMPLE)
    expected = hashlib.sha256(
        json.dumps(
            {"selection": example.data.selection, "model": example.model_sha256},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    assert example.reference_sha256 == expected
    original = (EXAMPLE / "nozzle-bayonette-simplified.ply").read_bytes()
    imported = prepare_scan(io.BytesIO(original), "raw.ply", limits)
    np.testing.assert_array_equal(imported.data.xyz, example.data.xyz)
    np.testing.assert_array_equal(imported.data.triangles, example.data.triangles)
    np.testing.assert_allclose(imported.data.normals, example.data.normals, atol=1e-15)
    np.testing.assert_allclose(imported.data.weights, example.data.weights, rtol=1e-14)
