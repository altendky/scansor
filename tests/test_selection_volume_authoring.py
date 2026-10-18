"""Volume authoring follows actual solved geometry without cache feedback."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe, Selection, StaleGraph
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import ScanCancelled, SourceDescriptor, Workspace
from experiments.selection_entries import (
    SelectionEntry,
    SelectionVolume,
    SourceBinding,
    SurfaceAuthoringReference,
    VolumeIntent,
    VolumeReference,
    source_binding,
)
from experiments.selection_volume import apply_selection_volume
from experiments.selection_volume_authoring import (
    authoring_fingerprint,
    authoring_recipe_intent,
    authoring_selection_ids,
    capture_volume,
    resolve_surface,
    resolve_volume,
    semantic_recipe,
)
from experiments.volume_archive import VolumeArchive, decode_selection_volume


@pytest.fixture
def workspace() -> Workspace:
    x, y = np.meshgrid(np.arange(5.0), np.arange(5.0))
    local = np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size)))
    triangles: list[list[int]] = []
    for row in range(4):
        for col in range(4):
            a = row * 5 + col
            triangles.extend(([a, a + 1, a + 6], [a, a + 6, a + 5]))
    normals = np.tile([0.0, 0, 1], (len(local), 1))
    return _workspace(
        SimpleNamespace(
            name="Author scan",
            local=local,
            origin=np.zeros(3),
            frame=np.eye(3),
            source_sha256="a" * 64,
            reference_sha256="b" * 64,
            source_descriptor=None,
            data=SimpleNamespace(
                xyz=local.copy(),
                normals=normals,
                triangles=np.asarray(triangles, dtype=np.int32),
                weights=np.ones(len(local)),
            ),
        ),
    )


def _workspace(value: SimpleNamespace) -> Workspace:
    return cast(Workspace, cast(object, value))


def saved_volume(**changes: Any) -> SelectionVolume:
    # Intentionally not a zlib stream: intent and reference resolution must
    # never expand accepted archives merely to inspect authoring metadata.
    return SelectionVolume(
        id="volume",
        label="Reusable patch",
        kind="plane",
        fit=SurfaceAuthoringReference(feature="fit_a"),
        archive=VolumeArchive(sha256="0" * 64, uncompressed_bytes=1, data="eA=="),
        authoring_fingerprint="1" * 64,
        **changes,
    )


def authored_recipe(workspace: Workspace) -> Recipe:
    binding = source_binding(workspace)
    nodes: list[dict[str, Any]] = [
        {
            "id": "scan",
            "label": "Scan",
            "operation": "source",
            "source_sha256": workspace.source_sha256,
            "reference_sha256": workspace.reference_sha256,
        }
    ]
    for suffix, ids in (("a", list(range(12))), ("b", list(range(12, 25)))):
        entry = SelectionEntry(
            id="original",
            scan=binding,
            mode="painted",
            ids=ids,
            volumes=[saved_volume()] if suffix == "a" else [],
        )
        nodes.extend(
            [
                {
                    "id": f"selection_{suffix}",
                    "label": f"Patch {suffix}",
                    "operation": "selection",
                    "source": "scan",
                    "entries": [entry.model_dump(mode="json")],
                },
                {
                    "id": f"fit_{suffix}",
                    "label": f"Fit {suffix}",
                    "operation": "fit",
                    "selections": [f"selection_{suffix}"],
                    "kind": "plane",
                },
            ]
        )
    nodes.append(
        {
            "id": "parallel",
            "label": "Parallel",
            "operation": "plane_relationship",
            "relation": "parallel",
            "surfaces": ["fit_a", "fit_b"],
        }
    )
    return Recipe.model_validate(
        {"schema_version": 2, "nodes": nodes, "output": "parallel"}
    )


def authored_items(recipe: Recipe) -> tuple[Selection, SelectionEntry, SelectionVolume]:
    return resolve_volume(
        recipe,
        VolumeReference(selection="selection_a", entry="original", volume="volume"),
    )


@pytest.mark.parametrize("kind", ["mesh", "plane"])
def test_capture_preserves_hole_controls_and_protected_provenance(
    workspace: Workspace, kind: Any
) -> None:
    ids = [i for i in range(len(workspace.local)) if i != 12]
    archived = capture_volume(
        workspace,
        ids,
        kind,
        {"inward_thickness": 0.1, "outward_thickness": 0.3, "edge_padding": 0.0},
        {"kind": "plane", "plane_equation": [0, 0, 1, 0]} if kind == "plane" else None,
        {"purpose": "test", "source_binding": "forged", "source_frame": "forged"},
    )
    saved = decode_selection_volume(archived)
    assert saved["resolved"]["inward_thickness"] == 0.1
    assert saved["resolved"]["outward_thickness"] == 0.3
    assert saved["provenance"] == {
        "purpose": "test",
        "source_binding": source_binding(workspace).key,
        "source_frame": {
            "origin": workspace.origin.tolist(),
            "basis": workspace.frame.tolist(),
        },
    }
    assert (
        apply_selection_volume(
            workspace.local, workspace.data.normals, workspace.data.weights, saved
        )
        == ids
    )


def test_capture_rotates_normals_into_the_same_frame_as_saved_geometry(
    workspace: Workspace,
) -> None:
    rotation = np.array([[0.0, 0, 1], [0, 1, 0], [-1, 0, 0]])
    # A legacy frame maps raw-world +X normals to local +Z.
    rotated = _workspace(
        SimpleNamespace(
            **{
                **vars(workspace),
                "origin": np.array([4.0, 5, 6]),
                "frame": rotation,
                "data": SimpleNamespace(
                    **{**vars(workspace.data), "normals": np.tile([1.0, 0, 0], (25, 1))}
                ),
            }
        ),
    )
    saved = decode_selection_volume(
        capture_volume(rotated, list(range(25)), "mesh", None)
    )
    assert apply_selection_volume(
        rotated.local, np.tile([0.0, 0, 1], (25, 1)), np.ones(25), saved
    ) == list(range(25))
    assert saved["provenance"]["source_frame"]["basis"] == rotation.tolist()


def test_capture_honors_cancellation(workspace: Workspace) -> None:
    with pytest.raises(ScanCancelled):
        _ = capture_volume(workspace, [0], "mesh", None, cancelled=lambda: True)


@pytest.mark.parametrize(
    "reference",
    [
        VolumeReference(selection="fit_a", entry="original", volume="volume"),
        VolumeReference(selection="selection_a", entry="missing", volume="volume"),
        VolumeReference(selection="selection_a", entry="original", volume="missing"),
    ],
)
def test_volume_references_require_exact_containment(
    workspace: Workspace, reference: VolumeReference
) -> None:
    recipe = authored_recipe(workspace)
    assert authored_items(recipe)[2].archive.data == "eA=="
    with pytest.raises(ValueError):
        _ = resolve_volume(recipe, reference)


def test_fingerprint_tracks_connected_geometry_and_named_contexts(
    workspace: Workspace,
) -> None:
    recipe = authored_recipe(workspace)
    owner, entry, volume = authored_items(recipe)
    assert authoring_selection_ids(recipe, owner, entry, volume) == {
        "selection_a",
        "selection_b",
    }
    first = authoring_fingerprint(recipe, owner, entry, volume)
    payload = recipe.model_dump(mode="json")
    payload["nodes"][3]["entries"][0]["ids"] = [13, 14, 15]
    changed = Recipe.model_validate(payload)
    assert authoring_fingerprint(changed, *authored_items(changed)) != first
    payload = recipe.model_dump(mode="json")
    payload["nodes"][-1]["relation"] = "coincident"
    changed = Recipe.model_validate(payload)
    assert authoring_fingerprint(changed, *authored_items(changed)) != first
    named = volume.model_copy(
        update={"fit": SurfaceAuthoringReference(feature="parallel", surface="fit_a")}
    )
    assert authoring_selection_ids(recipe, owner, entry, named) == {
        "selection_a",
        "selection_b",
    }
    assert authoring_fingerprint(recipe, owner, entry, named) != first
    mesh = volume.model_copy(update={"kind": "mesh", "fit": None})
    assert authoring_selection_ids(recipe, owner, entry, mesh) == {"selection_a"}


def test_volume_intent_resolves_authoring_before_an_archive_exists(
    workspace: Workspace,
) -> None:
    recipe = authored_recipe(workspace)
    owner, entry, saved = authored_items(recipe)
    intent = VolumeIntent(
        kind=saved.kind,
        fit=saved.fit,
        options=saved.options,
        captured_ids=saved.captured_ids,
        captured_inputs=saved.captured_inputs,
    )
    assert "archive" not in intent.model_dump()
    assert authoring_selection_ids(recipe, owner, entry, intent) == {
        "selection_a",
        "selection_b",
    }
    assert authoring_fingerprint(recipe, owner, entry, intent) == authoring_fingerprint(
        recipe, owner, entry, saved
    )
    projection = authoring_recipe_intent(recipe, owner, entry, intent)
    assert projection == authoring_recipe_intent(recipe, owner, entry, saved)
    selected = next(node for node in projection["nodes"] if node["id"] == owner.id)
    assert selected["entries"][0]["ids"] == entry.ids
    assert "volumes" not in selected["entries"][0]
    assert "label" not in selected


def test_fingerprint_ignores_metadata_owned_caches_and_inactive_scans(
    workspace: Workspace,
) -> None:
    recipe = authored_recipe(workspace)
    first = authoring_fingerprint(recipe, *authored_items(recipe))
    binding = source_binding(workspace)
    original_intent = semantic_recipe(recipe, binding.key)
    payload = recipe.model_dump(mode="json")
    other = SourceBinding(
        source_sha256="c" * 64, reference_sha256="d" * 64, vertices=50
    )
    payload["nodes"][0]["source_sha256"] = other.source_sha256
    payload["nodes"][0]["reference_sha256"] = other.reference_sha256
    payload["groups"] = [{"id": "group", "label": "Presentation only"}]
    for node in payload["nodes"]:
        node["label"] += " renamed"
        node["group_id"] = "group"
        if node["operation"] == "selection":
            node["entries"].append(
                SelectionEntry(id="target", scan=other, mode="generated").model_dump(
                    mode="json"
                )
            )
    saved = payload["nodes"][1]["entries"][0]["volumes"][0]
    saved["label"] = "Renamed volume"
    saved["archive"]["sha256"] = "2" * 64
    saved["authoring_fingerprint"] = "3" * 64
    changed = Recipe.model_validate(payload)
    assert authoring_fingerprint(changed, *authored_items(changed)) == first
    assert semantic_recipe(changed, binding.key) == original_intent


def test_converting_an_authoring_sibling_preserves_existing_volume_freshness(
    workspace: Workspace,
) -> None:
    from experiments.selection_volume_transactions import convert_selection

    nested = authored_recipe(workspace)
    payload = nested.model_dump(mode="json")
    sibling = payload["nodes"][3]
    original = sibling.pop("entries")[0]
    sibling.update(ids=original["ids"], depth=original["depth"])
    legacy = Recipe.model_validate(payload)
    first = authoring_fingerprint(legacy, *authored_items(legacy))
    numerical = semantic_recipe(legacy, source_binding(workspace))
    converted = convert_selection(legacy, workspace, "selection_b")
    assert authoring_fingerprint(converted, *authored_items(converted)) == first
    assert semantic_recipe(converted, source_binding(workspace)) == numerical
    assert authoring_fingerprint(nested, *authored_items(nested)) == first
    changed = converted.model_dump(mode="json")
    changed["nodes"][3]["entries"][0]["ids"] = [12, 13, 14]
    edited = Recipe.model_validate(changed)
    assert authoring_fingerprint(edited, *authored_items(edited)) != first


def test_missing_authoring_entry_is_rejected_only_inside_relevant_closure(
    workspace: Workspace,
) -> None:
    recipe = authored_recipe(workspace)
    owner, entry, volume = authored_items(recipe)
    payload = recipe.model_dump(mode="json")
    payload["nodes"][3]["entries"][0]["scan"]["source_sha256"] = "c" * 64
    changed = Recipe.model_validate(payload)
    with pytest.raises(ValueError, match="missing authoring scan entry"):
        _ = authoring_fingerprint(changed, *authored_items(changed))
    mesh = volume.model_copy(update={"kind": "mesh", "fit": None})
    assert authoring_fingerprint(changed, owner, entry, mesh) == authoring_fingerprint(
        recipe, owner, entry, mesh
    )


def test_original_volume_options_and_membership_are_authored_inputs(
    workspace: Workspace,
) -> None:
    recipe = authored_recipe(workspace)
    owner, entry, volume = authored_items(recipe)
    first = authoring_fingerprint(recipe, owner, entry, volume)
    changed = volume.model_copy(
        update={"options": volume.options.model_copy(update={"edge_padding": 0.5})}
    )
    assert authoring_fingerprint(recipe, owner, entry, changed) != first
    payload = recipe.model_dump(mode="json")
    payload["nodes"][1]["entries"][0]["ids"] = [0, 1, 2]
    edited = Recipe.model_validate(payload)
    assert authoring_fingerprint(edited, *authored_items(edited)) != first


def test_flat_legacy_semantic_recipe_is_unchanged(workspace: Workspace) -> None:
    payload = authored_recipe(workspace).model_dump(mode="json")
    for node in payload["nodes"]:
        if node["operation"] == "selection":
            node["ids"] = node.pop("entries")[0]["ids"]
    recipe = Recipe.model_validate(payload)
    assert semantic_recipe(recipe, "irrelevant") == recipe.model_dump(mode="json")


def test_analytic_volume_requires_its_owner_in_the_actual_observation_closure(
    workspace: Workspace,
) -> None:
    payload = authored_recipe(workspace).model_dump(mode="json")
    payload["nodes"].pop()  # No connected relationship brings patch A into fit B.
    recipe = Recipe.model_validate(payload)
    owner, entry, volume = authored_items(recipe)
    unrelated = volume.model_copy(
        update={"fit": SurfaceAuthoringReference(feature="fit_b")}
    )
    with pytest.raises(ValueError, match="must consume its owning selection"):
        _ = authoring_selection_ids(recipe, owner, entry, unrelated)


def test_source_filename_hints_do_not_change_nested_authoring_identity(
    workspace: Workspace,
) -> None:
    descriptor = SourceDescriptor(
        source_sha256="a" * 64,
        source_bytes=1234,
        interpretation_sha256="c" * 64,
        vertices=25,
        triangles=32,
        filename="original.ply",
    )
    binding = SourceBinding(
        source_sha256=descriptor.source_sha256,
        reference_sha256=descriptor.binding_sha256,
        vertices=25,
        descriptor=descriptor,
    )
    payload = authored_recipe(workspace).model_dump(mode="json")
    payload["nodes"][0].update(
        source_sha256=binding.source_sha256,
        reference_sha256=binding.reference_sha256,
        descriptor=descriptor.model_dump(mode="json"),
    )
    for node in payload["nodes"]:
        if node["operation"] == "selection":
            node["entries"][0]["scan"] = binding.model_dump(mode="json")
    recipe = Recipe.model_validate(payload)
    first = authoring_fingerprint(recipe, *authored_items(recipe))
    before = semantic_recipe(recipe, binding.key)
    payload["nodes"][0]["descriptor"]["filename"] = "renamed.ply"
    for node in payload["nodes"]:
        if node["operation"] == "selection":
            node["entries"][0]["scan"]["descriptor"]["filename"] = "renamed.ply"
    changed = Recipe.model_validate(payload)
    assert authoring_fingerprint(changed, *authored_items(changed)) == first
    assert semantic_recipe(changed, binding.key) == before


def test_generated_adoption_freezes_all_observation_inputs_in_connected_context(
    workspace: Workspace,
) -> None:
    payload = authored_recipe(workspace).model_dump(mode="json")
    upstream = saved_volume().model_copy(
        update={"id": "upstream", "kind": "mesh", "fit": None}
    )
    binding = source_binding(workspace)
    payload["nodes"].insert(
        1,
        {
            "id": "painted",
            "label": "Painted source",
            "operation": "selection",
            "source": "scan",
            "entries": [
                SelectionEntry(
                    id="original",
                    scan=binding,
                    mode="painted",
                    ids=list(range(25)),
                    volumes=[upstream],
                ).model_dump(mode="json")
            ],
        },
    )
    for node in payload["nodes"]:
        if node["id"] in {"selection_a", "selection_b"}:
            entry = node["entries"][0]
            entry["mode"] = "generated"
            entry.pop("ids")
            entry["source_volume"] = {
                "selection": "painted",
                "entry": "original",
                "volume": "upstream",
            }
            if node["id"] == "selection_a":
                entry["volumes"][0]["captured_ids"] = list(range(12))
                entry["volumes"][0]["captured_inputs"] = {
                    "selection_a": list(range(12)),
                    "selection_b": list(range(12, 25)),
                }
    recipe = Recipe.model_validate(payload)
    first = authoring_fingerprint(recipe, *authored_items(recipe))
    intent = semantic_recipe(recipe, binding.key)
    payload["nodes"][1]["entries"][0]["volumes"][0]["archive"]["sha256"] = "2" * 64
    changed = Recipe.model_validate(payload)
    # Linked selections follow the new volume, while the accepted adoption's
    # observation evidence stays pinned until an explicit update.
    assert semantic_recipe(changed, binding.key) != intent
    assert authoring_fingerprint(changed, *authored_items(changed)) == first
    adopted = payload["nodes"][2]["entries"][0]["volumes"][0]
    adopted["captured_inputs"]["selection_b"] = [13, 14, 15]
    recaptured = Recipe.model_validate(payload)
    assert authoring_fingerprint(recaptured, *authored_items(recaptured)) != first


def test_resolved_surface_uses_full_direct_and_explicit_joint_geometry() -> None:
    example = Path("examples/nozzle-bayonette-simplified")
    recipe = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    )
    graph = FeatureGraph(NozzleWorkspace(example), recipe)
    token = str(graph.snapshot()["token"])
    direct = resolve_surface(graph, token, SurfaceAuthoringReference(feature="side"))
    joint = resolve_surface(
        graph, token, SurfaceAuthoringReference(feature="fit", surface="side")
    )
    assert direct["kind"] == joint["kind"] == "cone"
    assert direct["ids"] == joint["ids"]
    assert not np.allclose(direct["parameters"], joint["parameters"], atol=1e-8, rtol=0)
    assert (
        joint
        == cast(dict[str, Any], graph.snapshot()["results"])["fit"]["surfaces"]["side"]
    )
    with pytest.raises(ValueError, match="explicit member"):
        _ = resolve_surface(graph, token, SurfaceAuthoringReference(feature="fit"))
    with pytest.raises(StaleGraph):
        _ = resolve_surface(
            graph, "obsolete", SurfaceAuthoringReference(feature="side")
        )


def test_resolve_surface_does_not_substitute_nonready_or_wrong_member_geometry() -> (
    None
):
    example = Path("examples/nozzle-bayonette-simplified")
    recipe = Recipe.model_validate_json(
        (example / "recipes/cone-plane.json").read_text()
    )
    state = {
        "token": "token",
        "recipe": recipe.model_dump(),
        "states": {"side": "failed"},
        "results": {},
    }
    calls: list[Any] = []

    def snapshot() -> dict[str, Any]:
        return deepcopy(state)

    def ensure_current(token: str, **kwargs: Any) -> None:
        calls.append((token, kwargs))

    graph = cast(
        FeatureGraph[Workspace],
        cast(
            object,
            SimpleNamespace(
                snapshot=snapshot,
                ensure_current=ensure_current,
            ),
        ),
    )
    with pytest.raises(ValueError, match="ready surface"):
        _ = resolve_surface(graph, "token", SurfaceAuthoringReference(feature="side"))
    assert calls == [("token", {"targets": ["side"]})]
    with pytest.raises(ValueError, match="must not specify a member"):
        _ = resolve_surface(
            graph, "token", SurfaceAuthoringReference(feature="side", surface="end")
        )
